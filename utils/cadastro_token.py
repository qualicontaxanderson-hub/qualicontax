# -*- coding: utf-8 -*-
"""Q-COLABORE Bloco 1 — link de USO ÚNICO (``cadastro_link``).

Dois tipos de link, mesma mecânica:

  CADASTRO  admin gera → destinatário abre o formulário de candidatura
  SENHA     admin gera → usuário existente troca a senha

O que este módulo garante
-------------------------
1) O token em claro existe UMA vez, no retorno de ``gerar()``. O banco guarda
   só o SHA-256. Um dump vazado não vira acesso; o ``token_prefixo`` serve
   apenas para o admin reconhecer qual link é qual na tela.

2) Uso único de verdade, sem janela de corrida. ``consumir()`` é um UPDATE
   condicional: quem obtiver ``rowcount == 1`` ganhou. Não existe
   SELECT-depois-UPDATE aqui — dois cliques simultâneos no mesmo link resultam
   em um sucesso e um "já utilizado", nunca em dois cadastros.

3) Diagnóstico honesto. Quando o UPDATE não pega, aí sim um SELECT descobre o
   PORQUÊ (expirado / usado / revogado / inexistente / tipo errado) para a tela
   dizer a coisa certa, em vez de um genérico "link inválido".

POR QUE AS ESCRITAS NÃO USAM ``execute_query``
----------------------------------------------
``execute_query(..., fetch=False)`` devolve ``True`` para UPDATE/DELETE
independentemente do rowcount (db_helper.py:213-218 — a docstring de lá promete
o número de linhas, mas o código só devolve lastrowid ou True). Construir o uso
único sobre esse retorno daria um ``consumir()`` que SEMPRE diz "ganhei" — dois
cliques simultâneos criariam dois cadastros, e o bug seria invisível em teste
sequencial.

Então toda escrita daqui passa por ``transacao()``, que entrega o cursor real e
portanto o ``cursor.rowcount`` verdadeiro. Não é preferência de estilo: é a
única forma de a garantia deste módulo ser real.

Sem Flask: só db_helper. Testável a seco.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta

from utils.db_helper import execute_query, transacao

logger = logging.getLogger(__name__)

TABELA = 'cadastro_link'
TIPOS = ('CADASTRO', 'SENHA')

TOKEN_BYTES = 32                 # secrets.token_urlsafe(32) -> 43 chars
PREFIXO_LEN = 8
TENTATIVAS_TOKEN = 5
VALIDADE_HORAS_PADRAO = 72

# Motivos devolvidos por inspecionar()/consumir(). Texto de tela fica na rota;
# aqui só o código, para a UI poder traduzir sem parsear frase.
OK = 'ok'
EXPIRADO = 'expirado'
USADO = 'usado'
REVOGADO = 'revogado'
INEXISTENTE = 'inexistente'
TIPO_ERRADO = 'tipo_errado'


class CadastroTokenError(RuntimeError):
    """Falha de infraestrutura ao manipular um link (não é 'link inválido')."""


# ---------------------------------------------------------------------------
# Internos
# ---------------------------------------------------------------------------
def _hash(token: str) -> str:
    """SHA-256 hex do token. É isto — e só isto — que o banco vê."""
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


def hash_token(token: str) -> str:
    """Hash canônico do token, exposto para quem precisa consumir o link dentro da
    PRÓPRIA transação (ex.: /senha grava a senha e queima o token no mesmo UPDATE
    atômico, então não pode usar consumir(), que abre transação à parte). Usa o
    mesmo _hash — não há duas formas de derivar o que o banco guarda."""
    return _hash(token)


def _valida_tipo(tipo: str) -> str:
    t = (tipo or '').strip().upper()
    if t not in TIPOS:
        raise ValueError(f'tipo inválido: {tipo!r} (esperado {" ou ".join(TIPOS)})')
    return t


def _escrever(sql, params=None, cur=None) -> int:
    """Executa uma escrita e devolve o rowcount REAL.

    Via transacao() de propósito — ver a nota no topo do módulo. Erro de banco
    sobe como CadastroTokenError em vez de virar None silencioso.

    Com ``cur`` (cursor de uma transação já aberta pelo chamador), escreve DENTRO
    dela e deixa a exceção subir crua — quem abriu a transação é quem faz o
    rollback. É o que permite gerar o link no MESMO commit de outra operação
    (ex.: bloquear o acesso e emitir o link de redefinição atomicamente).
    """
    if cur is not None:
        cur.execute(sql, params or ())
        return int(cur.rowcount or 0)
    try:
        with transacao() as c:
            c.execute(sql, params or ())
            return int(c.rowcount or 0)
    except Exception as exc:
        raise CadastroTokenError(f'falha ao gravar em {TABELA}: {exc}') from exc


def _inserir(sql, params=None, cur=None) -> int:
    """Executa um INSERT e devolve o lastrowid. Com ``cur``, insere na transação
    do chamador (ver _escrever)."""
    if cur is not None:
        cur.execute(sql, params or ())
        return int(cur.lastrowid or 0)
    try:
        with transacao() as c:
            c.execute(sql, params or ())
            return int(c.lastrowid or 0)
    except Exception as exc:
        raise CadastroTokenError(f'falha ao inserir em {TABELA}: {exc}') from exc


def _token_unico() -> tuple:
    """(token_claro, token_hash) garantidamente livre na tabela.

    Colisão em 32 bytes é praticamente impossível; a checagem existe para que um
    acaso vire nova tentativa em vez de estourar o UNIQUE no meio da gravação.
    Mesmo racional do _token_unico() do qrobo_chaves.
    """
    for _ in range(TENTATIVAS_TOKEN):
        token = secrets.token_urlsafe(TOKEN_BYTES)
        th = _hash(token)
        ja = execute_query(f'SELECT 1 AS x FROM {TABELA} WHERE token_hash = %s',
                           (th,), fetch=True, fetch_one=True)
        if not ja:
            return token, th
    raise CadastroTokenError('Não foi possível gerar um token único.')


def _motivo_da_falha(token_hash: str, tipo: str) -> dict:
    """Por que o UPDATE não pegou. Chamado SÓ depois de rowcount == 0."""
    row = execute_query(
        f"""SELECT id, tipo, usuario_id, destinatario, criado_por, criado_em,
                   expira_em, usado_em, revogado_em,
                   (expira_em <= NOW()) AS vencido
              FROM {TABELA} WHERE token_hash = %s""",
        (token_hash,), fetch=True, fetch_one=True)
    if not row:
        return {'ok': False, 'motivo': INEXISTENTE, 'link': None}
    if row.get('revogado_em'):
        motivo = REVOGADO
    elif row.get('usado_em'):
        motivo = USADO
    elif row.get('tipo') != tipo:
        motivo = TIPO_ERRADO
    elif row.get('vencido'):
        motivo = EXPIRADO
    else:
        # Perdeu a corrida por micros: outro processo consumiu entre o UPDATE e
        # este SELECT. Do ponto de vista de quem chamou, é 'usado'.
        motivo = USADO
    return {'ok': False, 'motivo': motivo, 'link': row}


# ---------------------------------------------------------------------------
# 1) Gerar
# ---------------------------------------------------------------------------
def gerar(tipo: str, criado_por: int, *, destinatario: str = None,
          usuario_id: int = None,
          validade_horas: int = VALIDADE_HORAS_PADRAO,
          url_builder=None, cur=None) -> tuple:
    """Cria um link de uso único. Devolve ``(token_claro, link_id)``.

    O token claro é devolvido UMA vez e não é recuperável depois — quem chamou
    tem que entregá-lo ao destinatário nesta mesma execução.

    ``tipo='SENHA'`` exige ``usuario_id`` (o dono da senha). ``tipo='CADASTRO'``
    aceita usuario_id=None: a conta ainda não existe.

    ``url_builder`` (opcional): callable ``token -> url_completa``. Existe para
    este módulo continuar SEM Flask — quem tem ``url_for`` (a rota) passa o
    construtor, e a URL é gravada em ``url_claro`` no MESMO INSERT do hash. É uma
    cópia operacional de vida curta (o admin reenvia o convite perdido sem gerar
    outro link); morre no consumo/revogação/expiração. Sem url_builder,
    ``url_claro`` nasce NULL e o link vale só pelo hash. Se o builder estourar, a
    geração NÃO falha: perde-se só a cópia, nunca o link.

    ``cur`` (opcional): cursor de uma transação já aberta. Com ele, a revogação
    dos pendentes e o INSERT do novo link ocorrem DENTRO dessa transação — então
    bloquear o acesso (senha_pendente=1) e emitir o link de redefinição são um
    único commit atômico. Sem ``cur``, cada escrita abre sua própria transação
    como antes.
    """
    tipo = _valida_tipo(tipo)
    if not criado_por:
        raise ValueError('criado_por é obrigatório (todo link tem um autor).')
    if tipo == 'SENHA' and not usuario_id:
        raise ValueError('link de SENHA exige usuario_id.')
    if validade_horas < 1:
        raise ValueError('validade_horas deve ser >= 1.')

    # Revoga os pendentes anteriores do mesmo alvo: dois links vivos para a
    # mesma pessoa é convite a confusão (e a segundo cadastro).
    revogados = revogar_pendentes(tipo, destinatario=destinatario,
                                  usuario_id=usuario_id, revogado_por=criado_por,
                                  cur=cur)
    if revogados:
        logger.info('[cadastro_token] %d link(s) %s pendente(s) revogado(s) '
                    'antes de gerar o novo.', revogados, tipo)

    token, token_hash = _token_unico()
    expira_em = datetime.now() + timedelta(hours=validade_horas)

    # Cópia operacional da URL (ver docstring). Nunca deixa a geração falhar.
    url_claro = None
    if url_builder is not None:
        try:
            url_claro = (url_builder(token) or None)
            if url_claro:
                url_claro = url_claro[:255]
        except Exception as exc:
            logger.warning('[cadastro_token] url_builder falhou; link segue só '
                           'pelo hash: %s', exc)
            url_claro = None

    link_id = _inserir(
        f"""INSERT INTO {TABELA}
              (tipo, token_hash, token_prefixo, destinatario, usuario_id,
               criado_por, expira_em, url_claro)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (tipo, token_hash, token[:PREFIXO_LEN], destinatario or None,
         usuario_id or None, criado_por, expira_em, url_claro), cur=cur)

    logger.info('[cadastro_token] link %s id=%s criado por %s, expira %s.',
                tipo, link_id, criado_por, expira_em)
    return token, link_id


# ---------------------------------------------------------------------------
# 2) Inspecionar (read-only — NÃO consome)
# ---------------------------------------------------------------------------
def inspecionar(token_claro: str, tipo: str = None) -> dict:
    """Estado do link, sem consumir. ``{'ok': bool, 'motivo': str, 'link': dict|None}``.

    É o que a tela usa ao ABRIR o formulário, para já dizer "link expirado" em
    vez de deixar a pessoa preencher tudo e falhar no envio.
    """
    if not token_claro:
        return {'ok': False, 'motivo': INEXISTENTE, 'link': None}
    token_hash = _hash(token_claro)
    row = execute_query(
        f"""SELECT id, tipo, token_prefixo, destinatario, usuario_id, criado_por,
                   criado_em, expira_em, usado_em, revogado_em,
                   (expira_em <= NOW()) AS vencido
              FROM {TABELA} WHERE token_hash = %s""",
        (token_hash,), fetch=True, fetch_one=True)
    if not row:
        return {'ok': False, 'motivo': INEXISTENTE, 'link': None}
    if row.get('revogado_em'):
        return {'ok': False, 'motivo': REVOGADO, 'link': row}
    if row.get('usado_em'):
        return {'ok': False, 'motivo': USADO, 'link': row}
    if tipo is not None and row.get('tipo') != _valida_tipo(tipo):
        return {'ok': False, 'motivo': TIPO_ERRADO, 'link': row}
    if row.get('vencido'):
        return {'ok': False, 'motivo': EXPIRADO, 'link': row}
    return {'ok': True, 'motivo': OK, 'link': row}


# ---------------------------------------------------------------------------
# 3) Consumir (o UPDATE atômico)
# ---------------------------------------------------------------------------
def consumir(token_claro: str, tipo: str, *, ip: str = None) -> dict:
    """Marca o link como usado, de forma atômica. ``{'ok', 'motivo', 'link'}``.

    O uso-único mora INTEIRO no WHERE abaixo. Se ``rowcount == 1``, este
    chamador — e nenhum outro — pode seguir criando a conta / trocando a senha.
    Só quando o UPDATE não pega é que vamos ao banco descobrir o motivo.
    """
    if not token_claro:
        return {'ok': False, 'motivo': INEXISTENTE, 'link': None}
    tipo = _valida_tipo(tipo)
    token_hash = _hash(token_claro)

    # url_claro morre no MESMO UPDATE que marca o uso: a cópia operacional não
    # sobrevive ao link. Quem obtiver rowcount==1 já a apagou atomicamente.
    afetadas = _escrever(
        f"""UPDATE {TABELA}
               SET usado_em = NOW(), usado_ip = %s, url_claro = NULL
             WHERE token_hash = %s
               AND tipo = %s
               AND usado_em IS NULL
               AND revogado_em IS NULL
               AND expira_em > NOW()""",
        (ip or None, token_hash, tipo))

    if afetadas != 1:
        res = _motivo_da_falha(token_hash, tipo)
        logger.info('[cadastro_token] consumo recusado (%s).', res['motivo'])
        return res

    link = execute_query(
        f"""SELECT id, tipo, token_prefixo, destinatario, usuario_id, criado_por,
                   criado_em, expira_em, usado_em, usado_ip
              FROM {TABELA} WHERE token_hash = %s""",
        (token_hash,), fetch=True, fetch_one=True)
    logger.info('[cadastro_token] link %s id=%s CONSUMIDO.', tipo,
                (link or {}).get('id'))
    return {'ok': True, 'motivo': OK, 'link': link}


# ---------------------------------------------------------------------------
# 4) Vincular a conta criada / revogar
# ---------------------------------------------------------------------------
def vincular_usuario(link_id: int, usuario_id: int) -> bool:
    """Amarra ao link a conta que nasceu dele (aprovação de um CADASTRO)."""
    if not link_id or not usuario_id:
        raise ValueError('link_id e usuario_id são obrigatórios.')
    return _escrever(f'UPDATE {TABELA} SET usuario_id = %s WHERE id = %s',
                     (usuario_id, link_id)) == 1


def revogar(link_id: int, revogado_por: int) -> bool:
    """Mata um link que ainda não foi usado. Idempotente (já revogado → False)."""
    if not link_id:
        raise ValueError('link_id é obrigatório.')
    return _escrever(
        f"""UPDATE {TABELA}
               SET revogado_em = NOW(), revogado_por = %s, url_claro = NULL
             WHERE id = %s AND usado_em IS NULL AND revogado_em IS NULL""",
        (revogado_por, link_id)) == 1


def revogar_pendentes(tipo: str, *, destinatario: str = None,
                      usuario_id: int = None, revogado_por: int = None,
                      cur=None) -> int:
    """Revoga os links vivos do mesmo alvo. Devolve quantos caíram.

    Sem destinatario NEM usuario_id não faz nada: um WHERE sem alvo revogaria os
    links pendentes de TODO MUNDO. ``cur`` escreve na transação do chamador.
    """
    tipo = _valida_tipo(tipo)
    if not destinatario and not usuario_id:
        return 0
    cond, params = [], [revogado_por, tipo]
    if usuario_id:
        cond.append('usuario_id = %s')
        params.append(usuario_id)
    if destinatario:
        cond.append('destinatario = %s')
        params.append(destinatario)
    return _escrever(
        f"""UPDATE {TABELA}
               SET revogado_em = NOW(), revogado_por = %s, url_claro = NULL
             WHERE tipo = %s AND ({' OR '.join(cond)})
               AND usado_em IS NULL AND revogado_em IS NULL""",
        tuple(params), cur=cur)


def status_do_link(row: dict) -> str:
    """Estado derivado de uma linha de listar(): pendente/usado/expirado/revogado.

    Mora aqui e não no template porque a ordem de precedência é regra de
    negócio, não apresentação: um link REVOGADO depois de expirado ainda conta
    como revogado, e um USADO nunca vira 'expirado' quando a data passa.
    """
    if not row:
        return INEXISTENTE
    if row.get('revogado_em'):
        return REVOGADO
    if row.get('usado_em'):
        return USADO
    if row.get('vencido'):
        return EXPIRADO
    return 'pendente'


def limpar_url_claro_vencidos() -> int:
    """Apaga url_claro dos links já vencidos que ainda a carregam.

    Limpeza PREGUIÇOSA: chamada por listar() sempre que o admin abre a tela, sem
    cron novo. A cópia operacional só faz sentido enquanto o link é reenviável —
    vencido, ela some junto com a utilidade. Ainda-pendentes não são tocados.
    O rowcount costuma ser 0; o UPDATE só pega o que sobrou.
    """
    return _escrever(
        f"""UPDATE {TABELA}
               SET url_claro = NULL
             WHERE url_claro IS NOT NULL
               AND usado_em IS NULL
               AND revogado_em IS NULL
               AND expira_em <= NOW()""")


def listar(tipo: str = None, apenas_pendentes: bool = False, limite: int = 100):
    """Links para a tela do admin. NUNCA devolve token_hash — só o prefixo.

    Devolve ``url_claro`` (a URL completa) SÓ dos links ainda pendentes — os
    demais estados já a têm NULL no banco. É a única saída autorizada do token em
    claro, e existe para o botão "Copiar link" da tela do admin.
    """
    limpar_url_claro_vencidos()   # varredura preguiçosa antes de ler
    sql = [f"""SELECT l.id, l.tipo, l.token_prefixo, l.destinatario, l.usuario_id,
                      l.criado_por, l.criado_em, l.expira_em, l.usado_em,
                      l.revogado_em, l.url_claro,
                      (l.expira_em <= NOW()) AS vencido,
                      u.nome AS criado_por_nome
                 FROM {TABELA} l
                 LEFT JOIN usuarios u ON u.id = l.criado_por"""]
    where, params = [], []
    if tipo:
        where.append('l.tipo = %s')
        params.append(_valida_tipo(tipo))
    if apenas_pendentes:
        where.append('l.usado_em IS NULL AND l.revogado_em IS NULL '
                     'AND l.expira_em > NOW()')
    if where:
        sql.append('WHERE ' + ' AND '.join(where))
    sql.append('ORDER BY l.criado_em DESC LIMIT %s')
    params.append(int(limite))
    linhas = execute_query(' '.join(sql), tuple(params), fetch=True) or []
    for r in linhas:
        r['status'] = status_do_link(r)
    return linhas
