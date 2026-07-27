# -*- coding: utf-8 -*-
"""Backfill: dfe_documentos/dfe_itens -> nfe_importacoes/nfe_itens (origem='SEFAZ').

Traz as notas JÁ capturadas (que ficaram só nas tabelas cruas dfe_*) para a tela
conf-compras. Reusa o MESMO upsert do motor (dfe_captura.SQL_NOTA_UPSERT/
SQL_RESUMO_UPSERT/SQL_ITEM_INS), então as garantias são idênticas às da captura:

  * não duplica         — UPSERT por (chave_acesso, 'entrada');
  * não rebaixa         — completa->resumo nunca (resumo é no-op; incompleta só cai);
  * não toca o manual   — IF(origem='SEFAZ', VALUES(x), x) preserva linha UPLOAD/DROPBOX.

Idempotente: seguro rodar quantas vezes quiser.

Uso:
    python migrations/backfill_dfe_para_conf_compras.py            # DRY-RUN (não grava)
    python migrations/backfill_dfe_para_conf_compras.py --apply    # grava de verdade

Ordem no deploy: o DRY-RUN roda a QUALQUER momento (só lê). O --apply exige as
colunas novas de nfe_importacoes (origem+='SEFAZ', xml_caminho, incompleta), que a
migração do boot (init_db.run_migrations) adiciona — ou seja, faça o deploy do
código ANTES de rodar o --apply. O script confere isso e aborta com aviso claro.
"""
import sys

import mysql.connector

from config import Config
from utils.db_helper import execute_query
from utils.integrations.dfe_captura import (
    SQL_NOTA_UPSERT, SQL_RESUMO_UPSERT, SQL_NOTA_ID, SQL_ITENS_DEL, SQL_ITEM_INS,
    _uf_da_chave, _data_de,
)


def _digitos(v):
    return "".join(ch for ch in str(v or "") if ch.isdigit())


# Contagens do dry-run em UMA query: casa cada dfe_documentos com a linha da
# conf-compras (se houver) por (chave, 'entrada') e classifica o destino.
SQL_DRYRUN = """
    SELECT
      COUNT(*)                                          AS total,
      SUM(d.resumo = 0)                                 AS completas,
      SUM(d.resumo = 1)                                 AS resumos,
      SUM(d.emit_nome IS NOT NULL AND d.valor_total > 0)                   AS com_cabecalho,
      SUM(d.emit_nome IS NULL OR d.valor_total IS NULL OR d.valor_total=0) AS sem_cabecalho,
      SUM(ni.id IS NULL)                                AS inserir,
      SUM(ni.origem = 'SEFAZ')                          AS ja_sefaz,
      SUM(ni.origem IN ('UPLOAD','DROPBOX'))            AS pular_manual,
      SUM(ni.id IS NULL AND d.resumo = 0)               AS inserir_completa,
      SUM(ni.id IS NULL AND d.resumo = 1)               AS inserir_resumo
    FROM dfe_documentos d
    LEFT JOIN nfe_importacoes ni
           ON ni.chave_acesso = d.chave AND ni.tipo = 'entrada'
"""

# Itens que serão (re)gravados: das notas novas OU já-SEFAZ (essas o apply
# regrava via DELETE+INSERT). Itens de linha manual NÃO entram.
SQL_DRYRUN_ITENS = """
    SELECT COUNT(*) AS itens
    FROM dfe_itens it
    JOIN dfe_documentos d ON d.id = it.documento_id
    LEFT JOIN nfe_importacoes ni
           ON ni.chave_acesso = d.chave AND ni.tipo = 'entrada'
    WHERE ni.id IS NULL OR ni.origem = 'SEFAZ'
"""

# Notas cruas + nome/CNPJ do cliente (dest da nota é o próprio cliente).
SQL_LER_DOCS = """
    SELECT d.id, d.cliente_id, d.chave, d.resumo, d.numero, d.serie,
           d.dh_emissao, d.emit_cnpj, d.emit_nome, d.dest_cnpj, d.valor_total,
           d.situacao, d.xml_caminho,
           c.nome_razao_social AS dest_nome,
           c.cpf_cnpj          AS cliente_cnpj
    FROM dfe_documentos d
    JOIN clientes c ON c.id = d.cliente_id
    ORDER BY d.id
"""

SQL_LER_ITENS = """
    SELECT n_item, cprod_fornecedor, produto_xml, ncm, unidade,
           quantidade, valor_unitario, valor_total
    FROM dfe_itens WHERE documento_id = %s ORDER BY n_item
"""


def _conectar():
    """Conexão dedicada com autocommit OFF: o backfill inteiro numa transação
    (all-or-nothing). NÃO usa o pool do db_helper (que é autocommit=True)."""
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT,
        autocommit=False, time_zone='-03:00',
    )


def _preflight_schema():
    """Confere que as colunas novas de nfe_importacoes existem antes do --apply.
    Retorna (ok, faltando:list). Sem elas, o INSERT do apply quebraria."""
    faltando = []
    for col in ('xml_caminho', 'incompleta'):
        r = execute_query(
            "SELECT COUNT(*) AS c FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_importacoes' "
            "AND COLUMN_NAME = %s", (col,), fetch=True, fetch_one=True,
        ) or {}
        if not r.get('c'):
            faltando.append(col)
    enum = execute_query(
        "SELECT COLUMN_TYPE AS t FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_importacoes' "
        "AND COLUMN_NAME = 'origem'", fetch=True, fetch_one=True,
    ) or {}
    if 'SEFAZ' not in (enum.get('t') or ''):
        faltando.append("origem+='SEFAZ'")
    return (not faltando), faltando


def dry_run():
    r = execute_query(SQL_DRYRUN, fetch=True, fetch_one=True) or {}
    it = execute_query(SQL_DRYRUN_ITENS, fetch=True, fetch_one=True) or {}
    g = lambda k: int(r.get(k) or 0)
    print("=" * 64)
    print("BACKFILL dfe_documentos -> nfe_importacoes   (DRY-RUN — nada gravado)")
    print("=" * 64)
    print(f"  dfe_documentos (total)..............: {g('total')}")
    print("  -- natureza dos dados --")
    print(f"    COM cabecalho (emit_nome+valor>0).: {g('com_cabecalho')}  <- servem p/ conferir")
    print(f"    SEM cabecalho (casca vazia).......: {g('sem_cabecalho')}")
    print(f"    completas (tem itens).............: {g('completas')}")
    print(f"    resumo (so cabecalho, sem itens)..: {g('resumos')}")
    print("  -- o que o --apply faz --")
    print(f"  -> VAI INSERIR (novas)..............: {g('inserir')}")
    print(f"       completas (com itens)..........: {g('inserir_completa')}")
    print(f"       resumo (sem itens, incompleta).: {g('inserir_resumo')}")
    print(f"  -> JA EXISTEM como SEFAZ (atualiza).: {g('ja_sefaz')}")
    print(f"  -> PULAR (linha manual, intocada)...: {g('pular_manual')}")
    print(f"  itens a (re)inserir em nfe_itens....: {int(it.get('itens') or 0)}")
    print("=" * 64)
    ok, faltando = _preflight_schema()
    if not ok:
        print(f"  ATENCAO: para o --apply faltam no schema: {', '.join(faltando)}.")
        print("           Faca o deploy do codigo (migracao no boot) antes de aplicar.")
    print("  Confira os numeros. Para GRAVAR: rode de novo com  --apply")


def apply():
    ok, faltando = _preflight_schema()
    if not ok:
        print(f"ABORTADO: schema incompleto (faltam: {', '.join(faltando)}). "
              "Faca o deploy do codigo primeiro (a migracao do boot adiciona as colunas).")
        return 1

    docs = execute_query(SQL_LER_DOCS, fetch=True) or []
    conn = _conectar()
    # buffered: consumimos SELECT de checagem no meio de INSERTs sem "unread result".
    cur = conn.cursor(dictionary=True, buffered=True)
    novas = ja_sefaz = pular_manual = n_itens = 0
    try:
        for d in docs:
            chave = d['chave']

            # Classifica ANTES de gravar: novo / já-SEFAZ (refresh) / manual (pula).
            cur.execute(SQL_NOTA_ID, (chave,))
            pre = cur.fetchone()
            if pre and pre['origem'] in ('UPLOAD', 'DROPBOX'):
                pular_manual += 1          # linha do sprint manual — NUNCA toca
                continue
            nova = pre is None

            cancelada = 1 if d['situacao'] == 'cancelada' else 0
            nome_arq = f"{chave}.xml"
            emit_uf = _uf_da_chave(chave)
            data_emissao = _data_de(str(d['dh_emissao']) if d['dh_emissao'] else None)

            if d['resumo']:
                # dest = o proprio interessado (resumo nao traz destinatario).
                cur.execute(SQL_RESUMO_UPSERT, (
                    d['cliente_id'], nome_arq, chave, d['numero'], d['serie'],
                    data_emissao, d['emit_cnpj'], d['emit_nome'], emit_uf,
                    _digitos(d['cliente_cnpj']), d['dest_nome'], None,
                    d['valor_total'], cancelada,
                ))
            else:
                cur.execute(SQL_NOTA_UPSERT, (
                    d['cliente_id'], nome_arq, chave, d['numero'], d['serie'],
                    data_emissao, d['emit_cnpj'], d['emit_nome'], emit_uf,
                    d['dest_cnpj'], d['dest_nome'], None,
                    d['valor_total'], "", "", cancelada, d['xml_caminho'],
                ))
                # Linha manual já foi pulada acima → aqui é sempre nova/SEFAZ: itens ok.
                cur.execute(SQL_NOTA_ID, (chave,))
                row = cur.fetchone()
                if row:
                    cur.execute(SQL_ITENS_DEL, (row['id'],))
                    cur.execute(SQL_LER_ITENS, (d['id'],))
                    for i in cur.fetchall():
                        cur.execute(SQL_ITEM_INS, (
                            row['id'], i['n_item'], i['cprod_fornecedor'], i['produto_xml'],
                            i['ncm'], i['unidade'], i['quantidade'],
                            i['valor_unitario'], i['valor_total'],
                        ))
                        n_itens += 1

            if nova:
                novas += 1
            else:
                ja_sefaz += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    print("=" * 64)
    print("BACKFILL APLICADO  (dfe_documentos -> nfe_importacoes)")
    print("=" * 64)
    print(f"  ENTRARAM novas.....................: {novas}")
    print(f"  JA existiam como SEFAZ (refresh)...: {ja_sefaz}")
    print(f"  PULADAS (ja existiam do manual)....: {pular_manual}")
    print(f"  itens gravados em nfe_itens........: {n_itens}")
    print(f"  total processado...................: {novas + ja_sefaz + pular_manual}")
    return 0


if __name__ == '__main__':
    if '--apply' in sys.argv:
        sys.exit(apply())
    dry_run()
