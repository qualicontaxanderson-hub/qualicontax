# -*- coding: utf-8 -*-
"""Serviço de chaves do Q-Robô — núcleo do Portal do Instalador (Fase 1).

É a lógica do ``install_qrobo_23.py`` (script manual, um por posto) virando
função reutilizável, com auditoria obrigatória. Consumidores: o portal
(``/qrobo``, origem='PORTAL') e o painel admin (origem='ADMIN'). Nenhuma rota
aqui — só serviço.

REGRAS QUE ESTE MÓDULO GARANTE (não dependem de quem chama):

  1. Resolver por NÚMERO do cliente com a trava do script original: número que
     não existe, ou que casa com mais de um cliente, NÃO gera nada.
  2. Confirmar antes de agir: ``resolver_numero`` devolve a razão social e o
     estado atual do robô; quem chama mostra e só então pede a geração.
  3. ``data_inicio_captura`` NUNCA fica NULL (padrão = hoje). NULL significa
     capturar o histórico inteiro quando o robô ligar — foi o que aconteceu com
     o posto JK.
  4. Toda geração/regeração vira linha em ``qrobo_auditoria``, na MESMA
     transação da escrita em ``robo_config``. Não existe chave sem trilha.
  5. A auditoria guarda só o PREFIXO do token (8 chars). A trilha serve para
     casar "esta linha gerou a chave que está no robô", não para ser um segundo
     cofre de segredos.

FUSO: toda marcação de tempo é ``NOW()``/``CURDATE()`` do MySQL. O pool conecta
com ``time_zone='-03:00'`` (utils/db_helper.py), então grava em BRT. O relógio
do processo é UTC no Railway e não é usado para nada aqui.
"""
import re
import secrets
from datetime import date

from utils.db_helper import execute_query, transacao

# 32 bytes -> 64 chars hex, exatamente o VARCHAR(64) UNIQUE de robo_config.
TOKEN_BYTES = 32
PREFIXO_LEN = 8
TENTATIVAS_TOKEN = 5

ACAO_GERADA = 'CHAVE_GERADA'
ACAO_REGERADA = 'CHAVE_REGERADA'
ACAO_DOWNLOAD = 'DOWNLOAD_INSTALADOR'

ORIGEM_PORTAL = 'PORTAL'
ORIGEM_ADMIN = 'ADMIN'

_RE_DATA = re.compile(r'^\d{4}-\d{2}-\d{2}$')


# ---------------------------------------------------------------------------
# Contexto do request (ip / user-agent) — opcional, nunca derruba a operação
# ---------------------------------------------------------------------------
def contexto_request():
    """Devolve (ip, user_agent) do request atual; (None, None) fora de request.

    No Railway o app fica atrás de proxy, então ``remote_addr`` é o proxy: o IP
    real vem no primeiro item de ``X-Forwarded-For``.
    """
    try:
        from flask import request, has_request_context
        if not has_request_context():
            return None, None
        xff = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
        ip = xff or request.remote_addr
        ua = (request.headers.get('User-Agent') or '').strip()
        return (ip or None), (ua[:255] or None)
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# 1) Resolver NÚMERO do cliente -> cliente + estado atual do robô
# ---------------------------------------------------------------------------
def resolver_numero(numero):
    """Resolve ``numero_cliente`` -> cliente. NÃO gera nada, NÃO grava nada.

    Devolve dict com ``ok``:
      ok=True  -> {'cliente': {...}, 'robo': {...}|None}
      ok=False -> {'erro': 'numero_vazio'|'nao_encontrado'|'ambiguo',
                   'candidatos': [...]}   (candidatos só no caso ambíguo)

    A comparação é EXATA (mesma do install_qrobo_23.py): 'numero_cliente' é
    VARCHAR e '023' não é o mesmo cadastro que '23'. Sem normalização
    silenciosa — melhor não achar do que achar o posto errado.
    """
    numero = (numero or '').strip()
    if not numero:
        return {'ok': False, 'erro': 'numero_vazio'}

    linhas = execute_query(
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, situacao "
        "  FROM clientes WHERE numero_cliente = %s ORDER BY id",
        (numero,), fetch=True,
    ) or []

    if not linhas:
        return {'ok': False, 'erro': 'nao_encontrado', 'numero': numero}
    if len(linhas) > 1:
        return {'ok': False, 'erro': 'ambiguo', 'numero': numero,
                'candidatos': [{'cliente_id': r['id'],
                                'razao_social': r['nome_razao_social'],
                                'cpf_cnpj': r['cpf_cnpj']} for r in linhas]}

    cli = linhas[0]
    return {'ok': True,
            'cliente': {'cliente_id': cli['id'],
                        'numero_cliente': cli['numero_cliente'],
                        'razao_social': cli['nome_razao_social'],
                        'cpf_cnpj': cli['cpf_cnpj'],
                        'situacao': cli['situacao']},
            'robo': estado_robo(cli['id'])}


def resolver_cliente_id(cliente_id):
    """Mesmo retorno de ``resolver_numero``, mas partindo do cliente_id.

    Serve para quando o instalador ESCOLHE numa lista de busca: aí não há
    ambiguidade a resolver — a identidade já veio cravada.
    """
    try:
        cid = int(cliente_id)
    except (TypeError, ValueError):
        return {'ok': False, 'erro': 'cliente_inexistente'}

    cli = execute_query(
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, situacao "
        "  FROM clientes WHERE id = %s", (cid,), fetch=True, fetch_one=True)
    if not cli:
        return {'ok': False, 'erro': 'cliente_inexistente', 'cliente_id': cid}

    return {'ok': True,
            'cliente': {'cliente_id': cli['id'],
                        'numero_cliente': cli['numero_cliente'],
                        'razao_social': cli['nome_razao_social'],
                        'cpf_cnpj': cli['cpf_cnpj'],
                        'situacao': cli['situacao']},
            'robo': estado_robo(cli['id'])}


# Mínimo de caracteres para buscar — 2, o mesmo do /api/clientes/search do app.
# Não sobe para 3: existe posto com nome curto no cadastro ("AUTO POSTO JK"),
# e exigir 3 letras deixaria o instalador sem achá-lo pelo nome.
MIN_TERMO_TEXTO = 2
MIN_TERMO_NUMERO = 2
LIMITE_BUSCA = 15


def buscar_clientes(termo, limite=LIMITE_BUSCA):
    """Busca por número OU parte da razão social OU CNPJ. SÓ IDENTIDADE.

    Espelha o padrão do app (LIKE '%termo%', ordenado por nome, com teto), mas
    é um caminho PRÓPRIO do portal: devolve apenas número, razão social, CNPJ e
    situação — nada de e-mail, contrato, regime ou qualquer dado fiscal.

    Devolve [] quando o termo é curto demais — quem chama não precisa tratar.
    """
    termo = (termo or '').strip()
    so_digitos = _digitos(termo)
    minimo = MIN_TERMO_NUMERO if so_digitos and so_digitos == termo else MIN_TERMO_TEXTO
    if len(termo) < minimo:
        return []

    # Escapa os curingas do LIKE: '100%' procura o texto, não um prefixo.
    seguro = termo.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    padrao = f'%{seguro}%'
    limite = max(1, min(int(limite or LIMITE_BUSCA), 50))

    return execute_query(
        "SELECT id AS cliente_id, numero_cliente, nome_razao_social AS razao_social, "
        "       cpf_cnpj, situacao, "
        "       (SELECT COUNT(*) FROM robo_config r WHERE r.cliente_id = c.id) AS tem_robo "
        "  FROM clientes c "
        " WHERE c.numero_cliente = %s "
        "    OR c.numero_cliente LIKE %s "
        "    OR c.nome_razao_social LIKE %s "
        "    OR REGEXP_REPLACE(COALESCE(c.cpf_cnpj, ''), '[^0-9]', '') LIKE %s "
        " ORDER BY (c.numero_cliente = %s) DESC, c.situacao = 'ATIVO' DESC, "
        "          c.nome_razao_social "
        " LIMIT %s",
        (termo, padrao, padrao, f'%{so_digitos}%' if so_digitos else '\x00',
         termo, limite),
        fetch=True) or []


def _digitos(texto):
    return re.sub(r'\D', '', texto or '')


def estado_robo(cliente_id):
    """Estado do robô do cliente (ou None). NUNCA devolve o robo_token."""
    return execute_query(
        "SELECT r.cliente_id, r.ativo, r.data_inicio_captura, r.robo_reset_seq, "
        "       r.robo_ultimo_contato, r.criado_em, "
        "       r.token_gerado_em, r.token_gerado_por, r.token_versao, "
        "       u.nome AS token_gerado_por_nome, "
        "       (r.robo_token IS NOT NULL AND r.robo_token <> '') AS tem_token, "
        "       LEFT(COALESCE(r.robo_token, ''), %s) AS token_prefixo, "
        "       TIMESTAMPDIFF(MINUTE, r.robo_ultimo_contato, NOW()) AS min_sem_contato "
        "  FROM robo_config r "
        "  LEFT JOIN usuarios u ON u.id = r.token_gerado_por "
        " WHERE r.cliente_id = %s",
        (PREFIXO_LEN, cliente_id), fetch=True, fetch_one=True,
    )


# ---------------------------------------------------------------------------
# 2) Gerar / regerar a chave
# ---------------------------------------------------------------------------
def _token_unico():
    """Token novo garantidamente livre na robo_config.

    Colisão em 64 chars hex é praticamente impossível; a checagem existe para
    que um acaso vire nova tentativa em vez de estourar o UNIQUE no meio da
    transação (que abortaria a geração com erro de driver).
    """
    for _ in range(TENTATIVAS_TOKEN):
        token = secrets.token_hex(TOKEN_BYTES)
        ja = execute_query("SELECT 1 AS x FROM robo_config WHERE robo_token = %s",
                           (token,), fetch=True, fetch_one=True)
        if not ja:
            return token
    raise RuntimeError('Não foi possível gerar um robo_token único.')


def _normaliza_data_inicio(valor):
    """('YYYY-MM-DD'|date|None) -> 'YYYY-MM-DD'|None. Levanta ValueError se inválida.

    None/vazio é legítimo e significa "decide no SQL": CURDATE() ao criar, ou
    manter a data atual ao regerar. Data no futuro é recusada — faria o robô
    ignorar tudo que emitisse até lá (o status ``ignorado_data``), que nunca é
    a intenção de quem está instalando na pista.
    """
    if valor is None or valor == '':
        return None
    if isinstance(valor, date):
        texto = valor.isoformat()
    else:
        texto = str(valor).strip()
        if not _RE_DATA.match(texto):
            raise ValueError('data_invalida')
        try:
            date.fromisoformat(texto)
        except ValueError:
            raise ValueError('data_invalida')
    if date.fromisoformat(texto) > date.today():
        # date.today() aqui é só um guarda-corpo grosseiro (processo em UTC pode
        # estar 1 dia à frente do BRT por 3h). A recusa real de data futura é
        # confirmada no SQL, em BRT, dentro da transação.
        raise ValueError('data_futura')
    return texto


def gerar_chave(cliente_id, usuario_id, usuario_nome, *, regerar=False,
                data_inicio=None, origem=ORIGEM_PORTAL, ip=None, user_agent=None):
    """Gera (ou regera) o robo_token do cliente e grava a auditoria — atômico.

    ``regerar=False`` recusa se já existir robo_config: a tela precisa mostrar
    o aviso de invalidação e pedir a segunda confirmação antes de insistir.

    Devolve dict:
      ok=True  -> {'token': <64 chars, mostrado UMA vez>, 'acao', 'versao',
                   'data_inicio_captura', 'cliente', 'auditoria_id'}
      ok=False -> {'erro': 'cliente_inexistente'|'ja_existe'|'data_invalida'|
                           'data_futura', ...}
    """
    cliente = execute_query(
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj "
        "  FROM clientes WHERE id = %s",
        (cliente_id,), fetch=True, fetch_one=True,
    )
    if not cliente:
        return {'ok': False, 'erro': 'cliente_inexistente', 'cliente_id': cliente_id}

    try:
        data_txt = _normaliza_data_inicio(data_inicio)
    except ValueError as exc:
        return {'ok': False, 'erro': str(exc), 'valor': data_inicio}

    # Recusa data futura pelo relógio do BANCO (BRT). O guarda em Python usa o
    # relógio do processo, que é UTC no Railway e fica 3h à frente — de
    # madrugada ele deixaria passar um "amanhã" que ainda é futuro em BRT.
    if data_txt:
        futura = execute_query("SELECT (%s > CURDATE()) AS futura", (data_txt,),
                               fetch=True, fetch_one=True) or {}
        if futura.get('futura'):
            return {'ok': False, 'erro': 'data_futura', 'valor': data_txt}

    atual = estado_robo(cliente_id)
    if atual and not regerar:
        return {'ok': False, 'erro': 'ja_existe', 'robo': atual,
                'cliente': _resumo_cliente(cliente)}

    acao = ACAO_REGERADA if atual else ACAO_GERADA
    token = _token_unico()

    with transacao() as cur:
        if atual:
            # COALESCE: data informada > data que já estava > hoje. O terceiro
            # termo é o que conserta uma robo_config antiga com NULL (caso JK)
            # sem exigir que o instalador digite nada.
            cur.execute(
                "UPDATE robo_config "
                "   SET robo_token = %s, "
                "       token_gerado_em = NOW(), "
                "       token_gerado_por = %s, "
                "       token_versao = COALESCE(token_versao, 0) + 1, "
                "       data_inicio_captura = COALESCE(%s, data_inicio_captura, CURDATE()) "
                " WHERE cliente_id = %s",
                (token, usuario_id, data_txt, cliente_id),
            )
        else:
            cur.execute(
                "INSERT INTO robo_config "
                "  (cliente_id, data_inicio_captura, robo_token, robo_reset_seq, "
                "   ativo, token_gerado_em, token_gerado_por, token_versao) "
                "VALUES (%s, COALESCE(%s, CURDATE()), %s, 0, 1, NOW(), %s, 1)",
                (cliente_id, data_txt, token, usuario_id),
            )

        cur.execute(
            "SELECT data_inicio_captura, token_versao, ativo "
            "  FROM robo_config WHERE cliente_id = %s", (cliente_id,))
        gravado = cur.fetchone()

        cur.execute(
            "INSERT INTO qrobo_auditoria "
            "  (acao, origem, usuario_id, usuario_nome, cliente_id, numero_cliente, "
            "   razao_social, token_prefixo, ip, user_agent) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (acao, origem, usuario_id, (usuario_nome or '?')[:150], cliente_id,
             cliente['numero_cliente'], cliente['nome_razao_social'],
             token[:PREFIXO_LEN], ip, (user_agent or None)),
        )
        auditoria_id = cur.lastrowid

    return {'ok': True, 'token': token, 'acao': acao,
            'versao': int(gravado['token_versao'] or 1),
            'ativo': bool(gravado['ativo']),
            'data_inicio_captura': gravado['data_inicio_captura'],
            'cliente': _resumo_cliente(cliente), 'auditoria_id': auditoria_id}


def _resumo_cliente(cliente):
    return {'cliente_id': cliente['id'],
            'numero_cliente': cliente['numero_cliente'],
            'razao_social': cliente['nome_razao_social'],
            'cpf_cnpj': cliente['cpf_cnpj']}


# ---------------------------------------------------------------------------
# 3) Download do instalador (a trilha; o arquivo em si é da Fase 4)
# ---------------------------------------------------------------------------
def registrar_download(usuario_id, usuario_nome, versao=None,
                       origem=ORIGEM_PORTAL, ip=None, user_agent=None):
    """Grava a linha de auditoria de um download do instalador."""
    return execute_query(
        "INSERT INTO qrobo_auditoria "
        "  (acao, origem, usuario_id, usuario_nome, instalador_versao, ip, user_agent) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (ACAO_DOWNLOAD, origem, usuario_id, (usuario_nome or '?')[:150],
         (versao or None), ip, (user_agent or None)),
    )


# ---------------------------------------------------------------------------
# 4) Leitura da trilha
# ---------------------------------------------------------------------------
# Sem DATE_FORMAT de propósito: o driver interpola '%s' na query, e um '%d'
# literal de máscara de data quebraria a chamada com parâmetros. criado_em já
# vem em BRT (pool em -03:00); quem exibe formata com strftime.
_SELECT_AUDITORIA = (
    "SELECT id, acao, origem, usuario_id, usuario_nome, cliente_id, "
    "       numero_cliente, razao_social, token_prefixo, instalador_versao, "
    "       ip, user_agent, criado_em "
    "  FROM qrobo_auditoria "
)


def historico_usuario(usuario_id, limite=50):
    """Trilha do próprio colaborador (o que ELE gerou/baixou)."""
    return execute_query(
        _SELECT_AUDITORIA + " WHERE usuario_id = %s ORDER BY criado_em DESC, id DESC LIMIT %s",
        (usuario_id, int(limite)), fetch=True,
    ) or []


def ultimo_download(usuario_id):
    """Última vez que este colaborador baixou o instalador (ou None)."""
    return execute_query(
        _SELECT_AUDITORIA + " WHERE usuario_id = %s AND acao = %s "
        " ORDER BY criado_em DESC, id DESC LIMIT 1",
        (usuario_id, ACAO_DOWNLOAD), fetch=True, fetch_one=True,
    )


def historico_geral(limite=200, acao=None, cliente_id=None, usuario_id=None):
    """Trilha completa — visão do admin no painel."""
    onde, params = [], []
    if acao:
        onde.append("acao = %s")
        params.append(acao)
    if cliente_id:
        onde.append("cliente_id = %s")
        params.append(int(cliente_id))
    if usuario_id:
        onde.append("usuario_id = %s")
        params.append(int(usuario_id))
    sql = _SELECT_AUDITORIA + (" WHERE " + " AND ".join(onde) if onde else "")
    sql += " ORDER BY criado_em DESC, id DESC LIMIT %s"
    params.append(int(limite))
    return execute_query(sql, tuple(params), fetch=True) or []
