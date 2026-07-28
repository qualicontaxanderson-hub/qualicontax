# -*- coding: utf-8 -*-
"""Núcleo compartilhado de gravação de CT-e (``cte_documentos`` / ``cte_nfe``).

Equivalente do ``utils/nfe_import.py`` para o frete, e com o mesmo propósito: ser
o ÚNICO lugar que grava CT-e, para que a captura SEFAZ e (na fatia seguinte) o
fluxo Dropbox/upload produzam linhas idênticas. Fica em ``utils/`` e não em
``routes/`` porque a captura importar ``routes/`` causaria ciclo.

Regras de idempotência (mesmas do motor de NF-e):
  * A identidade é (chave_acesso, cliente_id) — o mesmo CT-e pode interessar a
    dois clientes do escritório, cada um com a sua linha e o seu papel.
  * Reimportar o MESMO CT-e faz UPDATE (``atualizado_em`` muda → a tela mostra
    "ATUALIZADO"), nunca uma segunda linha.
  * Uma linha MANUAL (UPLOAD/DROPBOX) NUNCA é sobrescrita pela captura SEFAZ: a
    manual é a que o escritório conferiu. Nesse caso devolve 'dup' e não toca nada.
  * ``cancelado`` só sobe, nunca desce (``GREATEST``): o cancelamento vem por
    evento, que pode chegar ANTES de uma reimportação do CT-e autorizado.
"""
from utils.db_helper import execute_query, execute_many
from utils.cte_parser import papel_do_cliente

_MAX_XML_SIZE = 16_000_000  # MEDIUMTEXT max is 16 MB

# Colunas alimentadas pelo parser (header). Lista única, usada tanto no INSERT
# quanto no UPDATE — evita as duas listas divergirem com ~40 colunas.
_COLS = (
    'modelo', 'num_cte', 'serie', 'data_emissao', 'dh_emissao', 'cfop',
    'natureza_operacao', 'tp_cte', 'tp_serv', 'modal',
    'emit_cnpj', 'emit_nome', 'emit_uf',
    'rem_cnpj', 'rem_nome', 'rem_uf',
    'dest_cnpj', 'dest_nome', 'dest_uf',
    'exped_cnpj', 'exped_nome', 'receb_cnpj', 'receb_nome',
    'toma_cod', 'tomador_cnpj', 'tomador_nome', 'tomador_papel',
    'uf_ini', 'mun_ini', 'uf_fim', 'mun_fim',
    'valor_frete', 'valor_receber', 'valor_bc_icms', 'valor_icms', 'aliq_icms',
    'cst_icms', 'valor_tot_trib', 'protocolo',
)

_ORIGENS_MANUAIS = ('UPLOAD', 'DROPBOX')

SQL_CTE_ID = (
    "SELECT id, origem FROM cte_documentos "
    "WHERE chave_acesso = %s AND cliente_id <=> %s"
)

SQL_NFE_DEL = "DELETE FROM cte_nfe WHERE cte_id = %s"
SQL_NFE_INS = (
    "INSERT INTO cte_nfe (cte_id, chave_nfe, num_nota, serie, valor) "
    "VALUES (%s,%s,%s,%s,%s)"
)

# Cancelamento por evento (procEventoCTe). Guard origem='SEFAZ': o evento da
# captura não mexe numa linha que o escritório importou à mão.
SQL_CANCELA = (
    "UPDATE cte_documentos SET cancelado = 1 "
    "WHERE chave_acesso = %s AND origem = 'SEFAZ'"
)


def _sql_insert():
    cols = ('cliente_id', 'grupo_id', 'papel_cliente', 'chave_acesso', 'origem',
            'nome_arquivo', 'xml_raw', 'xml_caminho', 'nsu', 'cancelado') + _COLS
    ph = ','.join(['%s'] * len(cols))
    return f"INSERT INTO cte_documentos ({', '.join(cols)}) VALUES ({ph})", cols


def _sql_update():
    # COALESCE em xml_raw/xml_caminho: a captura SEFAZ grava só o caminho no
    # Dropbox; um reprocessamento não deve apagar o xml_raw de uma importação
    # anterior que o tinha. cancelado com GREATEST: só sobe.
    sets = [f'{c} = %s' for c in _COLS]
    sets += [
        'papel_cliente = %s', 'grupo_id = %s', 'nome_arquivo = %s',
        'xml_raw = COALESCE(%s, xml_raw)',
        'xml_caminho = COALESCE(%s, xml_caminho)',
        'nsu = COALESCE(%s, nsu)',
        'cancelado = GREATEST(cancelado, %s)',
    ]
    return f"UPDATE cte_documentos SET {', '.join(sets)} WHERE id = %s"


def _valores_header(header):
    return tuple(header.get(c) for c in _COLS)


def _gravar_nfes(cte_id, nfes):
    """(Re)grava as NF-e transportadas: DELETE + INSERT, idempotente por CT-e."""
    execute_query(SQL_NFE_DEL, (cte_id,), fetch=False)
    if not nfes:
        return 0
    dados = [(cte_id, n.get('chave_nfe'), n.get('num_nota') or '',
              n.get('serie') or '', n.get('valor')) for n in nfes]
    execute_many(SQL_NFE_INS, dados)
    return len(dados)


def _save_cte(parsed: dict, nome_arquivo: str, origem: str,
              cliente_id=None, grupo_id=None, xml_raw: str | None = None,
              xml_caminho: str | None = None, nsu=None,
              papel_cliente: str | None = None,
              cnpj_cliente: str | None = None) -> str:
    """Grava (ou atualiza) UM CT-e + as NF-e transportadas.

    Args:
        parsed: retorno de ``utils.cte_parser.parse_cte_xml``.
        origem: 'SEFAZ' | 'DROPBOX' | 'UPLOAD'.
        cliente_id: empresa dona da linha (na captura, o dono do certificado).
        papel_cliente: se None, é deduzido comparando ``cnpj_cliente`` com as
            partes do CT-e (tomador → remetente → destinatário → ...).
        xml_raw: conteúdo do XML (fluxo manual). xml_caminho: ponteiro no Dropbox
            (fluxo SEFAZ). Passe um ou outro.

    Returns:
        'ok'  — linha nova
        'upd' — linha existente atualizada (vira "ATUALIZADO" na tela)
        'dup' — existe e é MANUAL: preservada intacta (captura não sobrescreve)

    Raises:
        Exception se a gravação falhar de verdade (o chamador aborta o documento
        e NÃO avança o cursor de NSU além dele).
    """
    header = parsed['header']
    chave = header['chave_acesso']
    cli = int(cliente_id) if cliente_id else None
    grp = int(grupo_id) if grupo_id else None
    papel = papel_cliente or papel_do_cliente(header, cnpj_cliente or '')
    xml_store = xml_raw[:_MAX_XML_SIZE] if xml_raw else None
    cancelado = int(header.get('cancelado') or 0)

    existente = execute_query(SQL_CTE_ID, (chave, cli), fetch=True, fetch_one=True)

    if existente:
        if origem == 'SEFAZ' and existente.get('origem') in _ORIGENS_MANUAIS:
            return 'dup'
        sql = _sql_update()
        params = _valores_header(header) + (
            papel, grp, nome_arquivo, xml_store, xml_caminho, nsu, cancelado,
            existente['id'],
        )
        if execute_query(sql, params, fetch=False) is None:
            raise Exception(f'Falha ao atualizar CT-e {chave} (cliente_id={cli})')
        _gravar_nfes(existente['id'], parsed.get('nfes') or [])
        return 'upd'

    sql, _cols = _sql_insert()
    params = (cli, grp, papel, chave, origem, nome_arquivo, xml_store,
              xml_caminho, nsu, cancelado) + _valores_header(header)
    cte_id = execute_query(sql, params, fetch=False)

    if not cte_id or cte_id is True:
        # INSERT falhou (ou não devolveu id). Pode ser corrida: outro processo
        # inseriu a mesma (chave, cliente_id) entre o SELECT e o INSERT.
        já = execute_query(SQL_CTE_ID, (chave, cli), fetch=True, fetch_one=True)
        if not já:
            raise Exception(f'Falha ao salvar CT-e {chave} (cliente_id={cli})')
        cte_id = já['id']

    _gravar_nfes(cte_id, parsed.get('nfes') or [])
    return 'ok'


def marcar_cte_cancelado(chave: str) -> bool:
    """Marca o CT-e como cancelado (evento 110111). Só em linha origem='SEFAZ'."""
    if not chave:
        return False
    return execute_query(SQL_CANCELA, (chave,), fetch=False) is not None
