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
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes",
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

# Inutilização e evento de CT-e continuam sem trilha por XML avulso: viram
# 'skip' declarado (arquiva, não lança) em vez de morrer no parser de NF-e.
# O cancelamento de CT-e tem caminho próprio (utils.cte_import.marcar_cte_cancelado,
# hoje só alimentado pela captura SEFAZ) — fica para uma fatia futura.
_RAIZES_SEM_IMPORT = {'proceventocte', 'inutnfe', 'procinutnfe', 'retinutnfe'}


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
        SQL_CANCELA_NOTA, SQL_EVENTO_UPSERT, TP_CANCELAMENTO, extrair_evento)

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

    cancelou = 0
    try:
        with transacao() as cur:
            cur.execute(SQL_EVENTO_UPSERT, (
                dono['cliente_id'], ev['chave_evento'], ch, ev['tp_evento'],
                ev['n_seq'], ev['descricao'], ev['dh_txt'], None, None,
                ev['org_cnpj'], None, content[:_MAX_XML_EVENTO],
            ))
            if ev['tp_evento'] == TP_CANCELAMENTO:
                cur.execute(SQL_CANCELA_NOTA, (ch,))
                cancelou = int(cur.rowcount or 0)
    except Exception as exc:
        return 'erro', f'{nome}: falha ao gravar evento — {exc}'

    if ev['tp_evento'] == TP_CANCELAMENTO:
        logger.info('[fiscal_ingest] evento 110111 %s: %d linha(s) da nota %s '
                    'marcadas como canceladas.', ev['chave_evento'], cancelou, ch)
        return 'ok', f"cancelamento aplicado ({cancelou} linha(s))"
    return 'ok', f"evento {ev['tp_evento']} registrado"


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
    if raiz in _RAIZES_EVENTO_NFE:
        return importar_evento(nome, content)
    if raiz in _RAIZES_SEM_IMPORT:
        # Arquivar sim, lançar não: inutilização e evento de CT-e não têm import
        # por XML avulso. 'skip' declarado, não silêncio.
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
