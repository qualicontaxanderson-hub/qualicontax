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

from utils.cte_import import _save_cte
from utils.cte_parser import parse_cte_xml, papel_do_cliente
from utils.db_helper import execute_query
from utils.nfe_import import _save_nfe_dual
from utils.nfe_parser import parse_nfe_xml

logger = logging.getLogger(__name__)


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
# Eventos e inutilização não têm trilha de import por XML avulso (a captura
# SEFAZ é quem grava dfe_eventos). Reconhecidos aqui só para virarem 'skip'
# explícito em vez de morrerem no parser de NF-e.
_RAIZES_SEM_IMPORT = {'proceventonfe', 'proceventocte', 'procevento', 'evento',
                      'reteventonfe', 'inutnfe', 'procinutnfe', 'retinutnfe'}


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
def _importar_um_cte(nome: str, content: str, cache):
    """Importa UM XML de CT-e. Devolve ('ok'|'dup', None) quando entrou, ou
    (None, 'motivo') quando o arquivo foi rejeitado."""
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
        if _save_cte(parsed, nome, 'UPLOAD', cliente_id=cid,
                     xml_raw=content, papel_cliente=papel, cnpj_cliente=dig) == 'ok':
            novo = True
    return ('ok' if novo else 'dup'), None


# ---------------------------------------------------------------------------
# A porta de entrada do roteador
# ---------------------------------------------------------------------------
def importar_xml(nome: str, dados: bytes, cache: dict,
                 origem: str = 'ROTEADOR') -> tuple:
    """Lança UM .xml no banco. Devolve ``(status, motivo)``.

    status ∈ ``ok`` (entrou) | ``dup`` (já estava) | ``skip`` (tipo sem trilha
    de import — evento/inutilização) | ``erro`` (recusado, com motivo).

    NÃO levanta exceção por XML ruim: devolve ('erro', motivo). Quem chama
    decide se arquiva ou deixa na origem — e a decisão do roteador é NÃO
    arquivar o que não foi lançado.

    O ``origem`` grava a procedência na linha (o mesmo campo que recebe
    'SEFAZ'/'Q-ROBO'/'UPLOAD' nos outros fluxos).
    """
    if isinstance(dados, bytes):
        content = dados.decode('utf-8', errors='replace')
    else:
        content = dados or ''
    if not content.strip():
        return 'erro', 'arquivo vazio'

    raiz = _raiz_do_xml(content)
    if raiz in _RAIZES_SEM_IMPORT:
        # Arquivar sim, lançar não: evento avulso não tem import por XML.
        return 'skip', f'{raiz}: sem trilha de import (só arquivamento)'

    if _e_cte(content):
        res, motivo = _importar_um_cte(nome, content, cache)
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

    try:
        return _save_nfe_dual(parsed, nome, origem, content,
                              dest_cli=dest_cli, emit_cli=emit_cli), ''
    except ValueError as exc:
        return 'erro', f'{nome}: {exc}'


__all__ = [
    '_build_cliente_doc_cache', '_find_cliente_by_doc_digits',
    '_CTE_PARTES', '_MODELOS_CTE', '_RE_CHAVE_ID', '_RE_RAIZ_XML',
    '_RAIZES_CTE', '_modelo_do_xml', '_e_cte', '_importar_um_cte',
    'importar_xml',
]
