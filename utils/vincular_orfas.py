# -*- coding: utf-8 -*-
"""Dá ``cliente_id`` à nota que nasceu sem empresa mas cujo CNPJ é de um cliente.

Por que existe (19/09/2026): a Conferência de Entradas do Terra Branca ficava
em branco — 49 notas no cartão, "Nenhuma nota" na tabela. Três notas ÓRFÃS
(cliente_id NULL) de um upload de 17/06/2026, anterior à auto-detecção pelo
dest_cnpj (27/07), faziam o escopo da tela acrescentar ``OR n.id IN (...)`` ao
WHERE; com ``ORDER BY data_emissao DESC LIMIT 50`` o MySQL trocava o índice
por empresa (49 linhas) pelo índice de data (1,15 milhão) e a consulta passava
dos 30 s. A consulta parou de contornar a órfã (routes.escrita_fiscal.
_escopo_empresa); este passo a CONSERTA, para ela aparecer onde deve.

Como uma órfã ainda pode nascer hoje: upload de XML cujo destinatário NÃO é
cliente grava sem vínculo, de propósito ("salva sem vínculo" em importar_xml);
se esse CNPJ vira cliente depois, a nota fica órfã de um dono que agora existe.

Regras:
* só linhas com ``cliente_id IS NULL`` — idx_lista (NF-e) e ix_cte_cliente
  (CT-e) começam por cliente_id, então o IS NULL é um range curto, não varredura;
* o documento que manda é o do PAPEL da linha: entrada → dest_cnpj,
  saída → emit_cnpj, CT-e → tomador_cnpj — as mesmas colunas que o escopo da
  tela usava no fallback;
* só clientes ``avulso = 0``; um CNPJ que case com MAIS de um cliente NÃO
  vincula (fica para decisão humana e sai no resumo como ``ambiguas``);
* o UPDATE é por id, com ``AND cliente_id IS NULL`` — nunca troca um vínculo
  que outro processo tenha dado no meio do caminho;
* ``LOTE`` linhas por tabela por rodada; o resumo vai para o manutencao_status.
"""
import logging
import time

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

LOTE = 500
_DIGITOS = "REPLACE(REPLACE(REPLACE({c},'.',''),'/',''),'-','')"

# (tabela, expressão SQL do documento da EMPRESA naquela linha; alias n)
ALVOS = (
    ('nfe_importacoes', "IF(n.tipo = 'saida', n.emit_cnpj, n.dest_cnpj)"),
    ('cte_documentos', "n.tomador_cnpj"),
)


def _candidatas(tabela, col_doc, limite):
    """{id_da_nota: {cliente_id, ...}} das órfãs cujo documento casa com cliente.

    Devolve ``None`` quando a consulta foi cortada pelo teto de 30 s — o
    chamador NÃO pode confundir isso com "não há órfã".
    """
    rows = execute_query(
        f"SELECT n.id, c.id AS cliente_id"
        f"  FROM {tabela} n"
        f"  JOIN clientes c ON c.avulso = 0"
        f"   AND {_DIGITOS.format(c='c.cpf_cnpj')} = {_DIGITOS.format(c=col_doc)}"
        f" WHERE n.cliente_id IS NULL"
        f" ORDER BY n.id"
        f" LIMIT %s", (limite,), fetch=True)
    if rows is None:
        return None
    por_nota = {}
    for r in rows:
        por_nota.setdefault(r['id'], set()).add(r['cliente_id'])
    if len(rows) >= limite and por_nota:
        # o corte pode ter deixado de fora o 2º cliente da última nota —
        # ela volta na próxima rodada, inteira
        por_nota.pop(max(por_nota))
    return por_nota


def vincular(prazo_seg=30, dry=False, lote=LOTE):
    """Uma passada pelas duas tabelas. Devolve o resumo para o status do cron."""
    t0 = time.monotonic()
    res = {'vinculadas': 0, 'ambiguas': 0, 'dry': bool(dry)}
    for tabela, col_doc in ALVOS:
        if time.monotonic() - t0 > prazo_seg:
            res['sem_tempo'] = True
            break
        mapa = _candidatas(tabela, col_doc, lote)
        if mapa is None:
            res['erro'] = f'{tabela}: consulta cortada'
            logger.error('[orfas] %s: a consulta das órfãs foi cortada; nada vinculado.', tabela)
            continue
        pares = sorted(((next(iter(cids)), nid) for nid, cids in mapa.items() if len(cids) == 1),
                       key=lambda p: p[1])
        ambiguas = sorted(nid for nid, cids in mapa.items() if len(cids) > 1)
        if ambiguas:
            res['ambiguas'] += len(ambiguas)
            logger.warning('[orfas] %s: %s nota(s) com CNPJ de MAIS de um cliente, não vinculadas: %s',
                           tabela, len(ambiguas), ambiguas[:20])
        if not pares:
            continue
        if not dry:
            for cliente_id, nid in pares:
                execute_query(f"UPDATE {tabela} SET cliente_id = %s"
                              f" WHERE id = %s AND cliente_id IS NULL",
                              (cliente_id, nid), fetch=False)
        res['vinculadas'] += len(pares)
        res[tabela] = [f'{nid}->{cid}' for cid, nid in pares[:20]]
        logger.warning('[orfas] %s: %s nota(s) %s ao dono pelo CNPJ: %s',
                       tabela, len(pares), 'que SERIAM vinculadas (dry)' if dry else 'vinculadas',
                       res[tabela])
    res['seg'] = round(time.monotonic() - t0, 1)
    return res
