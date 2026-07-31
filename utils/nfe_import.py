# -*- coding: utf-8 -*-
"""Núcleo compartilhado de importação de NF-e para a conf-compras.

Extraído de ``routes/escrita_fiscal.py`` (sem mudança de comportamento) para poder
ser reusado por FORA das rotas — em especial pela captura SEFAZ
(``utils/integrations/dfe_captura.py``), que importar ``routes/`` causaria ciclo.

``_save_nfe`` grava UMA linha em ``nfe_importacoes`` + os itens em ``nfe_itens``,
com todos os campos (impostos, cfop, natureza) e o vínculo ``produto_catalogo_id``
por item. ``_save_nfe_dual`` decide entrada (dest) e/ou saída (emit).

O ``escrita_fiscal`` reexporta estes símbolos, então os chamadores antigos
(upload manual, sync Dropbox) continuam funcionando sem alteração.
"""
import re

from utils.db_helper import execute_query, execute_many

_MAX_XML_SIZE = 16_000_000  # MEDIUMTEXT max is 16 MB


def _lookup_vinculo(codigo_produto: str, cliente_id, grupo_id,
                    prefetch_rows: list):
    """
    In-memory vinculos lookup using pre-fetched rows.
    Priority order matches _auto_vincular_db: empresa → grupo.

    Regras de ramo de atividade e globais são ignoradas mesmo que as linhas
    ainda existam (Fase 1 do redesenho): a memorização de um cliente não pode
    classificar as notas de outro.
    """
    if not codigo_produto:
        return None
    rows = [r for r in prefetch_rows if r['codigo_produto_xml'] == codigo_produto]
    # 1. Empresa específica
    if cliente_id is not None:
        for r in rows:
            if r['cliente_id'] == cliente_id and r['grupo_id'] is None:
                return r['produto_catalogo_id']
    # 2. Grupo
    if grupo_id is not None:
        for r in rows:
            if r['grupo_id'] == grupo_id and r['cliente_id'] is None:
                return r['produto_catalogo_id']
    return None


def _save_nfe(parsed: dict, nome_arquivo: str, origem: str, xml_raw: str,
              cliente_id=None, grupo_id=None, tipo: str = 'entrada',
              vinculos_cache: dict | None = None):
    """
    Salva NF-e parseada no banco.
    tipo: 'entrada' (destinatário é nosso cliente) ou 'saida' (emitente é nosso cliente).
    Returns: 'ok' ou 'dup'
    """
    h = parsed['header']
    chave = h['chave_acesso']

    existing = execute_query(
        "SELECT id FROM nfe_importacoes WHERE chave_acesso = %s AND tipo = %s",
        (chave, tipo), fetch=True, fetch_one=True,
    )
    if existing:
        return 'dup'

    xml_raw_store = xml_raw[:_MAX_XML_SIZE] if xml_raw else ''
    cli = int(cliente_id) if cliente_id else None
    grp = int(grupo_id) if grupo_id else None
    dest_uf = h.get('dest_uf', '') or ''

    # Para entradas: auto-detecta empresa pelo dest_cnpj quando não fornecida explicitamente
    if cli is None and tipo == 'entrada':
        dest_cnpj_raw = h.get('dest_cnpj', '')
        dest_digits = re.sub(r'\D', '', dest_cnpj_raw)
        if len(dest_digits) >= 11:
            found = execute_query(
                "SELECT id FROM clientes WHERE REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-','') = %s LIMIT 1",
                (dest_digits,), fetch=True, fetch_one=True,
            )
            if found:
                cli = found['id']

    nfe_id = execute_query(
        """INSERT INTO nfe_importacoes
               (cliente_id, grupo_id, tipo, nome_arquivo, chave_acesso, num_nota, serie,
                data_emissao, emit_cnpj, emit_nome, emit_uf,
                dest_cnpj, dest_nome, dest_uf,
                valor_total, valor_icms, valor_pis, valor_cofins, valor_ipi,
                cfop, natureza_operacao, xml_raw, origem)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            cli, grp, tipo, nome_arquivo, chave, h['num_nota'], h['serie'],
            h['data_emissao'], h['emit_cnpj'], h['emit_nome'], h['emit_uf'],
            h['dest_cnpj'], h['dest_nome'], dest_uf,
            h['valor_total'], h['valor_icms'], h['valor_pis'],
            h['valor_cofins'], h['valor_ipi'],
            h['cfop'], h['natureza_operacao'], xml_raw_store, origem,
        ),
    )

    if nfe_id is None:
        # INSERT falhou — pode ser race condition: outro processo inseriu o mesmo
        # (chave_acesso, tipo) entre o SELECT acima e este INSERT (violação UNIQUE).
        already = execute_query(
            "SELECT id FROM nfe_importacoes WHERE chave_acesso = %s AND tipo = %s",
            (chave, tipo), fetch=True, fetch_one=True,
        )
        if already:
            return 'dup'
        raise Exception(f"Falha ao salvar NF-e no banco (chave_acesso: {chave}, tipo: {tipo})")

    # Pre-fetch all vinculos for this emit_cnpj in ONE query (avoids N×4 queries
    # per item, which caused gunicorn worker timeouts on large batches).
    # Results are shared via vinculos_cache across NF-es from the same emitente.
    _emit_cnpj = h['emit_cnpj']
    # Vínculo é escopado por TIPO: memorização de Compras (entrada) NÃO classifica
    # Saídas e vice-versa. A key do cache inclui o tipo para não misturar.
    _vkey = f'__vrows__{tipo}__{_emit_cnpj}'
    if vinculos_cache is not None and _vkey in vinculos_cache:
        _vrows = vinculos_cache[_vkey]
    else:
        _vrows = execute_query(
            "SELECT codigo_produto_xml, cliente_id, grupo_id, "
            "produto_catalogo_id FROM nfe_produto_vinculo "
            "WHERE emit_cnpj = %s AND tipo = %s",
            (_emit_cnpj, tipo), fetch=True,
        ) or []
        if vinculos_cache is not None:
            vinculos_cache[_vkey] = _vrows

    items_data = []
    for item in parsed.get('itens', []):
        # In-memory priority lookup: empresa → grupo.
        prod_id = _lookup_vinculo(item['codigo_produto'], cli, grp, _vrows)
        items_data.append((
            nfe_id, item['num_item'], item['codigo_produto'],
            item['descricao'], item['ncm'], item['cfop'],
            item['unidade'], item['quantidade'], item['valor_unitario'],
            item['valor_total'], item['valor_icms'],
            item['valor_pis'], item['valor_cofins'], prod_id,
        ))

    if items_data:
        execute_many(
            """INSERT INTO nfe_itens
                   (nfe_id, num_item, codigo_produto, descricao, ncm, cfop,
                    unidade, quantidade, valor_unitario, valor_total,
                    valor_icms, valor_pis, valor_cofins, produto_catalogo_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            items_data,
        )

    return 'ok'


def _save_nfe_dual(parsed: dict, nome_arquivo: str, origem: str, xml_raw: str,
                   dest_cli=None, emit_cli=None,
                   grupo_id=None, vinculos_cache=None,
                   apenas_entrada: bool = False) -> str:
    """Salva NF-e como entrada (dest_cli) e/ou saída (emit_cli) quando aplicável.

    ``apenas_entrada=True`` grava SÓ a entrada (ótica do destinatário), ignorando
    o emitente mesmo que seja cliente — usado pela captura SEFAZ, que é sempre 1
    linha na ótica do dono do certificado. O fluxo Dropbox usa o default (False),
    mantendo a entrada-dupla intacta.

    Se dest_cli e emit_cli são None, levanta ValueError.
    Returns: 'ok' se qualquer registro foi criado; 'dup' se todos já existiam.
    """
    results = []
    if dest_cli is not None:
        results.append(_save_nfe(
            parsed, nome_arquivo, origem, xml_raw,
            cliente_id=dest_cli, grupo_id=grupo_id,
            tipo='entrada', vinculos_cache=vinculos_cache,
        ))
    if not apenas_entrada and emit_cli is not None and emit_cli != dest_cli:
        results.append(_save_nfe(
            parsed, nome_arquivo, origem, xml_raw,
            cliente_id=emit_cli, tipo='saida',
            vinculos_cache=vinculos_cache,
        ))
    if not results:
        raise ValueError('Nenhum cliente (dest/emit) associado a este XML')
    return 'ok' if 'ok' in results else 'dup'
