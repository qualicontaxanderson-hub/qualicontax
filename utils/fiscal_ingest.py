# -*- coding: utf-8 -*-
"""Núcleo de LANÇAMENTO de um .xml fiscal no banco — sem Flask.

Por que existe
--------------
O roteador (cron_roteador.py) SÓ MOVIA arquivo: classificava e arquivava em
EMPRESAS/.../FISCAL/{SENTIDO} sem nunca lançar a nota. Quem lançava era o job de
importação do blueprint, que lê a pasta /Fiscal/NOVO. Resultado: um .xml jogado
na _ENTRADA era arquivado e NUNCA entrava em nfe_importacoes/cte_documentos.

Este módulo é a metade que faltava. É o MESMO core do upload manual — os
helpers vieram de routes/escrita_fiscal.py sem mudança de comportamento, na
mesma receita já usada em utils/nfe_import.py: extrai para utils/ (para o cron
poder importar sem arrastar Flask) e o blueprint reexporta os nomes antigos.

Idempotência: ``_save_nfe``/``_save_cte`` deduplicam por (chave, cliente_id,
tipo). Reimportar o mesmo XML devolve 'dup' e não escreve nada — é isso que
torna seguro importar ANTES de arquivar e repetir no tick seguinte.
"""
import logging
import re

import xml.etree.ElementTree as ET

from utils.cte_import import _save_cte
from utils.cte_parser import parse_cte_xml, papel_do_cliente
from utils.db_helper import execute_query, transacao
from utils.nfe_import import _save_nfe_dual
from utils.nfe_parser import parse_nfe_xml

# Mesmo teto do xml_raw de evento da captura (a coluna é MEDIUMTEXT).
_MAX_XML_EVENTO = 4_000_000


def _parse(content: str):
    """ElementTree do XML, ou None. Tolera lixo antes do '<' (BOM, espaços)."""
    try:
        return ET.fromstring(content)
    except Exception:
        i = content.find('<')
        if i > 0:
            try:
                return ET.fromstring(content[i:])
            except Exception:
                return None
        return None

logger = logging.getLogger(__name__)

# A coluna `origem` de nfe_importacoes E de cte_documentos é um ENUM fechado:
#
#     ENUM('UPLOAD','DROPBOX','SEFAZ','Q-ROBO','Q-COLABORE')
#
# Este módulo já nasceu com um bug por causa disso: passava origem='ROTEADOR',
# o MySQL recusava o INSERT em strict mode, execute_query devolvia None e o
# _save_nfe levantava "Falha ao salvar NF-e no banco" — 10 notas do primeiro
# teste real caíram assim. Não perdemos nada (o roteador não arquiva o que não
# lançou), mas o erro só apareceu em produção. Daí o ORIGENS_VALIDAS: um valor
# inválido morre AQUI, com mensagem clara, em vez de três camadas adiante.
#
# ATENÇÃO: esta lista tem de espelhar o ENUM do banco. Quem acrescentar valor
# aqui precisa da migration correspondente ANTES do deploy (a de 'Q-COLABORE' é
# migrations/qcolabore_06_origem_enum.py) — código novo contra ENUM velho é
# INSERT recusado em produção.
ORIGENS_VALIDAS = frozenset({'UPLOAD', 'DROPBOX', 'SEFAZ', 'Q-ROBO', 'Q-COLABORE'})

# O que o roteador grava. Foi 'DROPBOX' entre 06/08/2026 20:43 e a migration 06:
# na época o ENUM não tinha valor melhor, e a caixa era descrita pelo meio
# (Dropbox) em vez de pelo produto. A procedência real é o Q-Colabore — o canal
# por onde o cliente entrega documento.
#
# As 759 linhas 'DROPBOX' NÃO foram repontadas: é verdade histórica, foi isso
# que o sistema gravou. Só o que nasce daqui em diante é Q-COLABORE.
ORIGEM_ROTEADOR = 'Q-COLABORE'


# ---------------------------------------------------------------------------
# Cache de clientes por documento  (movido de routes/escrita_fiscal.py)
# ---------------------------------------------------------------------------
def _build_cliente_doc_cache() -> dict:
    """Indexa clientes por documento numérico para matching robusto de CNPJ/CPF."""
    rows = execute_query(
        # avulso NÃO é cliente fiscal: XML dele não vira lançamento
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes "
        "WHERE avulso = 0",
        fetch=True,
    ) or []
    cache: dict = {}
    for row in rows:
        digits = re.sub(r'\D', '', row.get('cpf_cnpj') or '')
        if len(digits) < 11:
            continue
        doc_keys = {digits}
        nozero = digits.lstrip('0')
        if nozero:
            doc_keys.add(nozero)
        item = {
            'id': row['id'],
            'numero_cliente': row.get('numero_cliente'),
            'nome_razao_social': row['nome_razao_social'],
        }
        for k in doc_keys:
            cache.setdefault(k, item)
    return cache


def _find_cliente_by_doc_digits(doc_digits: str, cache: dict) -> dict | None:
    """Busca cliente por documento usando variações normalizadas."""
    digits = re.sub(r'\D', '', doc_digits or '')
    if len(digits) < 11:
        return None
    nozero = digits.lstrip('0')
    return cache.get(digits) or (cache.get(nozero) if nozero else None)


# ---------------------------------------------------------------------------
# Detecção de modelo  (movido de routes/escrita_fiscal.py)
# ---------------------------------------------------------------------------
# NF-e/NFC-e (55/65) e CT-e (57) entram no MESMO lote. O modelo é decidido
# ANTES de qualquer parse (dígitos 21-22 da chave do Id=, ou a raiz do XML
# quando não há Id) — era daí que vinha o "Nó não encontrado no XML": um CT-e
# caindo no parser de NF-e.
_CTE_PARTES = ('emit_cnpj', 'rem_cnpj', 'dest_cnpj', 'exped_cnpj', 'receb_cnpj',
               'tomador_cnpj')
_MODELOS_CTE = ('57', '67', '64')
# Id="CTe3521..." / Id="NFe3521..." — os 44 dígitos da chave. Eventos (Id="ID11...")
# têm mais de 44 dígitos antes das aspas e de propósito NÃO casam aqui.
_RE_CHAVE_ID = re.compile(r'\bId\s*=\s*["\'][A-Za-z]*(\d{44})["\']')
_RE_RAIZ_XML = re.compile(r'<\s*(?![?!])([A-Za-z_][\w.\-]*)')
_RAIZES_CTE = {'cteproc', 'cteosproc', 'cte', 'cteos', 'gtve',
               'infcte', 'infcteos', 'infgtve'}
# Evento de NF-e: TEM trilha de import (ver importar_evento). Antes caía no
# 'skip' junto com a inutilização — o arquivo era arquivado e o cancelamento
# nunca chegava ao banco, então uma nota cancelada na SEFAZ aparecia ATIVA na
# tela. Agora entra em dfe_eventos e, no 110111, marca a nota.
_RAIZES_EVENTO_NFE = {'proceventonfe', 'procevento', 'evento', 'reteventonfe'}

# Evento de CT-e: TEM trilha de import (ver importar_evento_cte). Antes caía no
# 'skip' junto com a inutilização — um cancelamento de CT-e largado na _ENTRADA
# era arquivado na pasta certa da empresa e o CT-e seguia ATIVO na tela.
_RAIZES_EVENTO_CTE = {'proceventocte', 'eventocte', 'reteventocte'}

# Inutilização continua sem trilha por XML avulso: vira 'skip' declarado
# (arquiva, não lança) em vez de morrer no parser de NF-e.
_RAIZES_SEM_IMPORT = {'inutnfe', 'procinutnfe', 'retinutnfe'}

# cStat de evento ACEITO pela SEFAZ. Qualquer outro valor é REJEIÇÃO — o pedido
# de cancelamento existe como arquivo, mas a SEFAZ não o homologou e o documento
# continua VÁLIDO. Sem esta conferência, um cancelamento rejeitado (573 "duplic.
# de evento", 594 "prazo excedido"...) cancelaria o CT-e no nosso banco enquanto
# ele segue ativo na SEFAZ — divergência que só apareceria na fiscalização.
#
# IMPORTADO da captura de CT-e em 14/08/2026, em vez de declarado aqui. Antes
# eram DOIS conjuntos para a mesma regra, e eles divergiram: este aceitava
# {135,136} e o da captura não conferia nada. O mesmo cancelamento tinha
# desfecho diferente conforme entrasse pela SEFAZ ou pela _ENTRADA — e o 155
# (homologado FORA DE PRAZO, portanto aceito) era descartado por este lado.
from utils.integrations.cte_captura import (                     # noqa: E402
    CSTAT_CANCELAMENTO_CTE_OK as _CSTAT_EVENTO_REGISTRADO)

# Ator de máquina do roteador na auditoria (logs_sistema).
#
# usuario_id fica NULL DE PROPÓSITO: não existe — e não deve existir — uma linha
# em `usuarios` para um cron; inventar um usuário fake só para satisfazer a FK
# sujaria o cadastro de gente e apareceria nas telas de seleção. A coluna é
# NULLABLE, e a auditoria já foi desenhada para não depender dela: usuario_nome
# e usuario_login são COPIADOS no ato justamente para sobreviver ao
# ON DELETE SET NULL da FK (ver utils/atividade.py). São eles que dizem quem fez.
_ATOR_ROTEADOR_NOME = 'ROTEADOR (_ENTRADA)'
_ATOR_ROTEADOR_LOGIN = 'roteador'


def _modelo_do_xml(content: str) -> str:
    """Modelo fiscal (dígitos 21-22 da chave) lido do Id=, sem parsear o XML.
    Devolve '' quando o documento não traz chave no Id."""
    m = _RE_CHAVE_ID.search(content)
    return m.group(1)[20:22] if m else ''


def _raiz_do_xml(content: str) -> str:
    m = _RE_RAIZ_XML.search(content)
    return m.group(1).split(':')[-1].lower() if m else ''


def _e_cte(content: str) -> bool:
    """True quando o XML é de CT-e (57) / CT-e OS (67) / GTV-e (64). Decidido
    pelo modelo da chave e, na falta dela, pela raiz do XML."""
    modelo = _modelo_do_xml(content)
    if modelo:
        return modelo in _MODELOS_CTE
    return _raiz_do_xml(content) in _RAIZES_CTE


# ---------------------------------------------------------------------------
# Import de UM documento  (movido de routes/escrita_fiscal.py)
# ---------------------------------------------------------------------------
def _importar_um_cte(nome: str, content: str, cache, origem: str = 'UPLOAD'):
    """Importa UM XML de CT-e. Devolve ('ok'|'dup', None) quando entrou, ou
    (None, 'motivo') quando o arquivo foi rejeitado.

    ``origem`` tem default 'UPLOAD' para os chamadores antigos (o upload manual
    do blueprint) continuarem idênticos; o roteador passa ORIGEM_ROTEADOR."""
    try:
        parsed = parse_cte_xml(content)
    except Exception as exc:
        return None, f'{nome}: XML não é um CT-e válido ({exc}).'
    header = parsed['header']
    chave = header.get('chave_acesso') or ''
    modelo = header.get('modelo') or (chave[20:22] if len(chave) >= 22 else '')
    if modelo != '57':
        return None, f'{nome}: não é CT-e modelo 57 (modelo {modelo or "?"}).'
    # Quais partes são clientes cadastrados → uma linha por cliente, com o papel
    # real (emitente → SAÍDA; tomador/dest/rem → entrada; os dois → dual).
    achados = {}   # cliente_id -> cnpj_dígitos
    for campo in _CTE_PARTES:
        dig = re.sub(r'\D', '', header.get(campo) or '')
        if len(dig) < 11:
            continue
        hit = _find_cliente_by_doc_digits(dig, cache)
        if hit and hit['id'] not in achados:
            achados[hit['id']] = dig
    if not achados:
        return None, f'{nome}: nenhuma empresa cadastrada nesse CT-e.'
    novo = False
    for cid, dig in achados.items():
        papel = papel_do_cliente(header, dig)
        if _save_cte(parsed, nome, origem, cliente_id=cid,
                     xml_raw=content, papel_cliente=papel, cnpj_cliente=dig) == 'ok':
            novo = True
    return ('ok' if novo else 'dup'), None


# ---------------------------------------------------------------------------
# Dono do EVENTO — resolvido pela CHAVE DA NOTA, não pelo CNPJ emitente
# ---------------------------------------------------------------------------
def dono_da_nota(chave: str) -> dict | None:
    """Cliente dono do documento de ``chave``. ``None`` se a nota não está aqui.

    Por que não pelo CNPJ emitente
    ------------------------------
    O ``classificar()`` do roteador decidia o dono do evento pelos dígitos 6-20
    da chave — que são o CNPJ do EMITENTE da nota. Num evento de cancelamento de
    compra, o emitente é o FORNECEDOR (a Raizen, no caso que motivou isto), que
    não é cliente do escritório: o evento virava REVISAR e ficava parado na
    _ENTRADA para sempre, enquanto a nota seguia ativa na tela.

    O dono certo é o dono da NOTA. Como a nota já está no banco (o roteador a
    lançou antes), basta perguntar por ela. Devolve o número e a razão porque o
    roteador precisa deles para montar EMPRESAS/<nº - razão>/FISCAL/EVENTOS.
    """
    if not chave or len(chave) != 44:
        return None
    for tabela in ('nfe_importacoes', 'cte_documentos'):
        r = execute_query(
            f"""SELECT c.id, c.numero_cliente, c.nome_razao_social
                  FROM {tabela} d JOIN clientes c ON c.id = d.cliente_id
                 WHERE d.chave_acesso = %s LIMIT 1""",
            (chave,), fetch=True, fetch_one=True)
        if r:
            return {'cliente_id': r['id'],
                    'numero': (r['numero_cliente'] or '').strip(),
                    'razao': r['nome_razao_social'] or ''}
    return None


def importar_evento(nome: str, content: str, chave_nota: str = None) -> tuple:
    """Lança UM procEventoNFe. Devolve ``(status, motivo)``.

    Grava o histórico em ``dfe_eventos`` e, quando ``tpEvento=110111``, marca
    ``nfe_importacoes.cancelada=1`` para a chave da nota — exatamente o que a
    captura SEFAZ faz em ``dfe_captura.gravar_evento``. Reusa o parser e os SQL
    de lá em vez de reescrevê-los: é o mesmo documento e a mesma regra, e uma
    segunda implementação divergiria no primeiro ajuste.

    Diferença ÚNICA em relação ao caminho da captura: aqui NÃO se sobe o XML
    para o Dropbox. O arquivo já está no Dropbox — veio da _ENTRADA — e quem o
    move para FISCAL/EVENTOS é o roteador, depois deste lançamento.

    Os dois passos vão na MESMA transação: ou o evento fica registrado com a
    nota marcada, ou nada entra.
    """
    from utils.integrations.dfe_captura import (
        CSTAT_CANCELAMENTO_OK, SQL_CANCELA_NOTA, SQL_EVENTO_UPSERT,
        TP_CANCELAMENTO, extrair_evento)

    root = _parse(content)
    if root is None:
        return 'erro', f'{nome}: evento ilegível'
    try:
        ev = extrair_evento(root)
    except Exception as exc:
        return 'erro', f'{nome}: evento sem identificação ({exc})'

    ch = ev.get('ch_nfe') or chave_nota
    if not ch:
        # ch_nfe é NOT NULL em dfe_eventos. Sem ela não há o que registrar nem o
        # que cancelar — arquiva e segue, como a captura também faz.
        return 'skip', f'{nome}: evento sem chNFe (só arquivamento)'

    dono = dono_da_nota(ch)
    if not dono:
        # Evento de nota que o sistema não tem. Não inventa dono: sem cliente_id
        # a linha de dfe_eventos ficaria órfã e o cancelamento não teria alvo.
        return 'erro', (f'{nome}: a nota {ch[:12]}… do evento não está no sistema '
                        '— importe a nota antes')

    # GUARDA DE STATUS — mesma regra do lado da captura (gravar_evento), lida da
    # MESMA constante. Um cancelamento REJEITADO pela SEFAZ (573 duplicidade,
    # 594 fora do prazo, ...) deixa a nota ATIVA lá; marcá-la aqui só produz
    # divergência. O evento continua sendo REGISTRADO no histórico dos dois
    # jeitos — a guarda decide apenas se a nota muda de estado.
    #
    # c_stat vazio significa XML só com o PEDIDO, sem o retEvento da resposta:
    # não dá para afirmar que a SEFAZ aceitou, então não se marca.
    cancela = (ev['tp_evento'] == TP_CANCELAMENTO
               and ev.get('c_stat') in CSTAT_CANCELAMENTO_OK)

    cancelou = 0
    try:
        with transacao() as cur:
            cur.execute(SQL_EVENTO_UPSERT, (
                dono['cliente_id'], ev['chave_evento'], ch, ev['tp_evento'],
                ev['n_seq'], ev['descricao'], ev['dh_txt'], None, None,
                ev['org_cnpj'], None, content[:_MAX_XML_EVENTO],
            ))
            if cancela:
                cur.execute(SQL_CANCELA_NOTA, (ch,))
                cancelou = int(cur.rowcount or 0)
    except Exception as exc:
        return 'erro', f'{nome}: falha ao gravar evento — {exc}'

    if cancela:
        logger.info('[fiscal_ingest] evento 110111 %s (cStat=%s): %d linha(s) da '
                    'nota %s marcadas como canceladas.', ev['chave_evento'],
                    ev.get('c_stat'), cancelou, ch)
        return 'ok', f"cancelamento aplicado ({cancelou} linha(s))"

    if ev['tp_evento'] == TP_CANCELAMENTO:
        logger.warning('[fiscal_ingest] cancelamento da nota %s NAO aceito pela '
                       'SEFAZ (cStat=%s) — nota segue ATIVA. Evento %s registrado '
                       'no historico.', ch, ev.get('c_stat') or '?',
                       ev['chave_evento'])
        return 'ok', (f"evento 110111 registrado, mas NAO aceito pela SEFAZ "
                      f"(cStat={ev.get('c_stat') or '?'}) — nota segue ativa")

    return 'ok', f"evento {ev['tp_evento']} registrado"


def importar_evento_cte(nome: str, content: str) -> tuple:
    """Lança UM procEventoCTe. Devolve ``(status, motivo)``.

    Espelha o ``importar_evento`` da NF-e, com uma diferença de fundo: NÃO existe
    tabela de histórico de evento de CT-e (o ``dfe_eventos`` é NF-e — ``ch_nfe``
    é NOT NULL). Então "registrar no banco", aqui, é exatamente o que a captura
    de CT-e já faz em ``cte_captura._gravar_evento``: só o CANCELAMENTO mexe na
    linha, marcando ``cte_documentos.cancelado=1``. O parser e o SQL vêm de lá —
    não há segunda implementação.

    TRÊS GUARDAS, nesta ordem:

    1. TIPO — a raiz ``procEventoCTe`` é a MESMA para todo evento de CT-e: Carta
       de Correção (110110), Comprovante de Entrega (110160), Prestação em
       Desacordo (610110), EPEC... Só o 110111 pode marcar. Sem esta guarda, uma
       carta de correção cancelaria o CT-e.
    2. STATUS — só marca com ``cStat`` de evento REGISTRADO (135/136), lido do
       ``retEventoCTe`` (o bloco de RESPOSTA da SEFAZ, não o do pedido). Um
       cancelamento REJEITADO não cancela nada.
    3. ÓRFÃO — o CT-e existe no banco? A pergunta é feita ao ``dono_da_nota``,
       o MESMO helper que o lado NF-e usa, e ANTES do UPDATE. Não morre em
       silêncio: loga e devolve 'erro'. O efeito no roteador é o que interessa:
       'erro' faz o arquivo FICAR na _ENTRADA, e o tick seguinte reprocessa
       sozinho depois que o documento entrar. Foi assim que o caso da CC-e de
       11/08 se resolveu — o evento ficou parado às 20:31 e passou às 20:46,
       sem intervenção.

       Por que NÃO se usa o rowcount do UPDATE para isso: sem ``CLIENT_FOUND_ROWS``
       (e o pool do db_helper não liga essa flag), o MySQL devolve em rowcount as
       linhas ALTERADAS, não as CASADAS. Um CT-e JÁ cancelado e um CT-e AUSENTE
       dão os dois rowcount=0 — medido contra o banco real. Decidir "órfão" por
       esse número prenderia para sempre na _ENTRADA todo evento reprocessado,
       repetindo de 15 em 15 minutos um aviso falso de "CT-e não está no sistema".
       Reprocessar o mesmo evento é rotina aqui: é o que torna seguro lançar
       antes de arquivar.
    """
    from utils.cte_import import marcar_cte_cancelado
    from utils.integrations.cte_captura import (
        TP_CANCELAMENTO_CTE, _extrair_evento_cte)
    from utils.integrations.dfe_sefaz import _find, _text

    root = _parse(content)
    if root is None:
        return 'erro', f'{nome}: evento de CT-e ilegível'
    try:
        ev = _extrair_evento_cte(root)
    except Exception as exc:
        return 'erro', f'{nome}: evento de CT-e sem identificação ({exc})'

    # GUARDA 1 — TIPO. Arquiva e sai, sem UPDATE.
    if ev['tp_evento'] != TP_CANCELAMENTO_CTE:
        return 'skip', (f"evento de CT-e {ev['tp_evento'] or '?'} "
                        '(não é cancelamento) — só arquivamento')

    ch = ev['ch_cte']
    if not ch:
        return 'skip', 'evento de CT-e sem chCTe — só arquivamento'

    # GUARDA 2 — STATUS. O cStat do PEDIDO não existe; o que vale é o da
    # resposta. _find varre por nome local, então busca-se dentro do retEventoCTe
    # para não pegar um cStat de outro bloco.
    ret = _find(root, 'retEventoCTe')
    c_stat = _text(ret, 'cStat') if ret is not None else None
    if c_stat not in _CSTAT_EVENTO_REGISTRADO:
        logger.warning('[fiscal_ingest] cancelamento de CT-e %s NÃO homologado '
                       '(cStat=%s) — CT-e segue ativo.', ch, c_stat)
        return 'skip', (f'cancelamento de CT-e não homologado (cStat={c_stat or "?"}) '
                        '— só arquivamento')

    # GUARDA 3 — ÓRFÃO, perguntado ANTES do UPDATE (ver docstring: o rowcount
    # NÃO distingue "ausente" de "já cancelado"). Mesmo critério do lado NF-e:
    # não arquiva, fica na _ENTRADA e o próximo tick tenta de novo.
    if not dono_da_nota(ch):
        logger.warning('[fiscal_ingest] evento 110111 de CT-e ÓRFÃO: o CT-e %s '
                       'não está no sistema — nada marcado, arquivo segue na '
                       '_ENTRADA para o próximo tick.', ch)
        return 'erro', (f'o CT-e {ch[:12]}… do cancelamento não está no sistema '
                        '— importe o CT-e antes')

    try:
        linhas = marcar_cte_cancelado(ch)
    except Exception as exc:
        return 'erro', f'{nome}: falha ao cancelar CT-e — {exc}'

    # linhas == 0 aqui significa JÁ ESTAVA cancelado (o CT-e existe — a guarda 3
    # acabou de confirmar). É o reprocessamento do mesmo evento: nada mudou, nada
    # a auditar. Só um fato que muda estado vira linha de auditoria.
    if not linhas:
        logger.info('[fiscal_ingest] CT-e %s já estava cancelado — evento '
                    'reprocessado, nada a alterar.', ch)
        return 'dup', 'CT-e já estava cancelado'

    # Auditoria. SEM o nome do arquivo, de propósito: a linha identifica o
    # DOCUMENTO (chave), não o caminho de onde ele veio.
    try:
        from utils.atividade import registrar_agente
        registrar_agente(
            'escrita.cancelou_cte_por_evento', 'fiscal',
            usuario_id=None, usuario_nome=_ATOR_ROTEADOR_NOME,
            usuario_login=_ATOR_ROTEADOR_LOGIN,
            tabela='cte_documentos',
            # registro_id fica None: a mesma chave pode ter uma linha por
            # cliente (dual-save), então não há UM id a apontar.
            registro_id=None,
            depois={'chave_acesso': ch, 'cancelado': 1,
                    'tp_evento': ev['tp_evento'], 'c_stat': c_stat,
                    'chave_evento': ev['chave_evento'],
                    'linhas_afetadas': linhas})
    except Exception:
        logger.exception('[fiscal_ingest] falha ao registrar auditoria do '
                         'cancelamento de CT-e %s (o cancelamento VALE).', ch)

    logger.info('[fiscal_ingest] evento 110111 de CT-e %s (cStat=%s): %d linha(s) '
                'marcadas como canceladas.', ch, c_stat, linhas)
    return 'ok', f'CT-e cancelado ({linhas} linha(s))'


# ---------------------------------------------------------------------------
# A porta de entrada do roteador
# ---------------------------------------------------------------------------
def importar_xml(nome: str, dados: bytes, cache: dict,
                 origem: str = ORIGEM_ROTEADOR) -> tuple:
    """Lança UM .xml no banco. Devolve ``(status, motivo)``.

    status ∈ ``ok`` (entrou) | ``dup`` (já estava) | ``skip`` (tipo sem trilha
    de import — evento/inutilização) | ``erro`` (recusado, com motivo).

    NÃO levanta exceção: devolve ('erro', motivo) para QUALQUER falha. Quem
    chama decide se arquiva ou deixa na origem — e a decisão do roteador é NÃO
    arquivar o que não foi lançado.

    O ``origem`` grava a procedência na linha e é validado contra o ENUM da
    coluna — ver ORIGENS_VALIDAS.
    """
    if origem not in ORIGENS_VALIDAS:
        raise ValueError(
            f'origem {origem!r} não existe no ENUM de nfe_importacoes.origem '
            f'({", ".join(sorted(ORIGENS_VALIDAS))}). Erro de programação.')
    if isinstance(dados, bytes):
        content = dados.decode('utf-8', errors='replace')
    else:
        content = dados or ''
    if not content.strip():
        return 'erro', 'arquivo vazio'

    raiz = _raiz_do_xml(content)
    # Evento de CT-e ANTES do de NF-e: 'proceventocte' não casa com nenhuma raiz
    # de _RAIZES_EVENTO_NFE, mas a ordem deixa a precedência explícita para quem
    # for mexer nos conjuntos depois.
    if raiz in _RAIZES_EVENTO_CTE:
        return importar_evento_cte(nome, content)
    if raiz in _RAIZES_EVENTO_NFE:
        return importar_evento(nome, content)
    if raiz in _RAIZES_SEM_IMPORT:
        # Arquivar sim, lançar não: inutilização não tem import por XML avulso.
        # 'skip' declarado, não silêncio.
        return 'skip', f'{raiz}: sem trilha de import (só arquivamento)'

    if _e_cte(content):
        try:
            res, motivo = _importar_um_cte(nome, content, cache, origem)
        except Exception as exc:
            return 'erro', f'{nome}: falha ao gravar CT-e — {exc}'
        if motivo:
            return 'erro', motivo
        return res, ''

    # NF-e / NFC-e (55/65). Diferente do upload manual, aqui NÃO existe empresa
    # selecionada na tela: dest e emit saem do próprio XML, contra a base.
    try:
        parsed = parse_nfe_xml(content)
    except Exception as exc:
        return 'erro', f'{nome}: XML não é uma NF-e válida ({exc})'

    header = parsed.get('header') or {}
    dest_cli = emit_cli = None
    _d = _find_cliente_by_doc_digits(header.get('dest_cnpj') or '', cache)
    if _d:
        dest_cli = _d['id']
    _e = _find_cliente_by_doc_digits(header.get('emit_cnpj') or '', cache)
    if _e and _e['id'] != dest_cli:
        emit_cli = _e['id']

    if dest_cli is None and emit_cli is None:
        # O classificar() do roteador já barra isso como SEM_MATCH; se chegou
        # aqui é divergência entre os dois — não inventa vínculo, recusa.
        return 'erro', f'{nome}: emitente e destinatário não são clientes'

    # except Exception, não só ValueError: o _save_nfe levanta Exception CRUA
    # quando o INSERT falha ("Falha ao salvar NF-e no banco"). Capturando só
    # ValueError, essa falha escapava para o handler genérico do roteador e a
    # linha do roteador_log saía SEM o marcador [import=...] — o log deixava de
    # dizer que quem falhou foi o lançamento. Aconteceu no primeiro teste real.
    try:
        return _save_nfe_dual(parsed, nome, origem, content,
                              dest_cli=dest_cli, emit_cli=emit_cli), ''
    except Exception as exc:
        return 'erro', f'{nome}: {exc}'


__all__ = [
    '_build_cliente_doc_cache', '_find_cliente_by_doc_digits',
    '_CTE_PARTES', '_MODELOS_CTE', '_RE_CHAVE_ID', '_RE_RAIZ_XML',
    '_RAIZES_CTE', '_modelo_do_xml', '_e_cte', '_importar_um_cte',
    'importar_xml',
]
