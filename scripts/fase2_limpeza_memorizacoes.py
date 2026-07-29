#!/usr/bin/env python
"""
Fase 2 do redesenho de Memorizações — limpeza DESTRUTIVA (sem backfill).

A Fase 1 (commit ed92093) parou de gravar e de aplicar regras de escopo RAMO e
GLOBAL, mas deixou as linhas antigas na tabela e os itens que elas já haviam
classificado indevidamente. Este script remove as duas coisas:

  1. Apaga de ``nfe_produto_vinculo`` as linhas de escopo RAMO
     (ramo_atividade_id preenchido) e GLOBAL (cliente_id, grupo_id e
     ramo_atividade_id todos NULL). MANTÉM escopo EMPRESA e GRUPO.

  2. Desvincula (produto_catalogo_id = NULL) todo item de nota de ENTRADA cuja
     empresa NÃO tenha memorização de escopo EMPRESA cobrindo aquele
     emit_cnpj + codigo_produto — ou seja, tudo que só estava classificado por
     causa do vazamento entre clientes.

MODOS
    python scripts/fase2_limpeza_memorizacoes.py           # DRY-RUN (padrão)
    python scripts/fase2_limpeza_memorizacoes.py --apply   # executa

O DRY-RUN é estritamente read-only: nenhum INSERT/UPDATE/DELETE/DDL.

No --apply, a ordem é: cria as tabelas de backup, GRAVA O BACKUP, e só então
desvincula os itens e apaga as regras — tudo numa única transação, com
conferência de contagem antes do COMMIT (divergiu, faz ROLLBACK).

Rodar no ambiente que tem as variáveis do banco (Railway), não local.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                              # noqa: E402
from config import Config                           # noqa: E402

BKP_REGRAS = 'nfe_produto_vinculo_bkp_fase2'
BKP_ITENS = 'nfe_itens_desvinc_bkp_fase2'

# ---------------------------------------------------------------------------
# Predicados canônicos — usados igualzinho na contagem, no backup e na escrita.
# ---------------------------------------------------------------------------

# Linhas a APAGAR: escopo RAMO ou escopo GLOBAL.
W_REGRAS_DELETE = (
    "(ramo_atividade_id IS NOT NULL) "
    "OR (cliente_id IS NULL AND grupo_id IS NULL AND ramo_atividade_id IS NULL)"
)

# Itens a DESVINCULAR: entrada, já classificados, sem regra de EMPRESA que os
# cubra. Nota sem empresa (n.cliente_id NULL) nunca casa no NOT EXISTS — o
# item cai aqui, que é o comportamento desejado (não havia escopo que o
# justificasse).
FROM_ITENS_ALVO = """
  FROM nfe_itens i
  JOIN nfe_importacoes n ON n.id = i.nfe_id
 WHERE i.produto_catalogo_id IS NOT NULL
   AND n.tipo = 'entrada'
   AND NOT EXISTS (
           SELECT 1 FROM nfe_produto_vinculo v
            WHERE v.cliente_id          = n.cliente_id
              AND v.grupo_id           IS NULL
              AND v.ramo_atividade_id  IS NULL
              AND v.emit_cnpj           = n.emit_cnpj
              AND v.codigo_produto_xml  = i.codigo_produto)
"""

Q_CONTA_REGRAS = """
SELECT
    SUM(ramo_atividade_id IS NOT NULL)                                    AS ramo,
    SUM(cliente_id IS NULL AND grupo_id IS NULL
        AND ramo_atividade_id IS NULL)                                    AS global_,
    SUM(cliente_id IS NOT NULL AND ramo_atividade_id IS NULL)             AS empresa,
    SUM(cliente_id IS NULL AND grupo_id IS NOT NULL
        AND ramo_atividade_id IS NULL)                                    AS grupo,
    SUM(cliente_id IS NOT NULL AND ramo_atividade_id IS NOT NULL)         AS anomalia,
    COUNT(*)                                                              AS total
  FROM nfe_produto_vinculo
"""

Q_CONTA_ITENS = "SELECT COUNT(*) AS qtd" + FROM_ITENS_ALVO

Q_ITENS_POR_EMPRESA = """
SELECT n.cliente_id,
       c.numero_cliente,
       c.nome_razao_social,
       COUNT(*) AS qtd
""" + FROM_ITENS_ALVO + """
 GROUP BY n.cliente_id, c.numero_cliente, c.nome_razao_social
 ORDER BY qtd DESC
"""
# c só existe no GROUP BY acima porque o JOIN é adicionado abaixo:
Q_ITENS_POR_EMPRESA = Q_ITENS_POR_EMPRESA.replace(
    "JOIN nfe_importacoes n ON n.id = i.nfe_id",
    "JOIN nfe_importacoes n ON n.id = i.nfe_id\n  LEFT JOIN clientes c ON c.id = n.cliente_id",
)

Q_TOTAL_ITENS_CLASSIFICADOS = """
SELECT COUNT(*) AS qtd
  FROM nfe_itens i
  JOIN nfe_importacoes n ON n.id = i.nfe_id
 WHERE i.produto_catalogo_id IS NOT NULL AND n.tipo = 'entrada'
"""

DDL_BKP_REGRAS = f"""
CREATE TABLE IF NOT EXISTS {BKP_REGRAS} (
    id                    INT,
    cliente_id            INT NULL,
    grupo_id              INT NULL,
    ramo_atividade_id     INT NULL,
    emit_cnpj             VARCHAR(18),
    codigo_produto_xml    VARCHAR(60),
    descricao_produto_xml VARCHAR(500) NULL,
    produto_catalogo_id   INT,
    criado_em             TIMESTAMP NULL,
    backup_em             TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_id (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

DDL_BKP_ITENS = f"""
CREATE TABLE IF NOT EXISTS {BKP_ITENS} (
    item_id             INT,
    produto_catalogo_id INT,
    cliente_id          INT NULL,
    emit_cnpj           VARCHAR(18),
    codigo_produto      VARCHAR(60),
    backup_em           TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

INS_BKP_REGRAS = f"""
INSERT INTO {BKP_REGRAS}
       (id, cliente_id, grupo_id, ramo_atividade_id, emit_cnpj,
        codigo_produto_xml, descricao_produto_xml, produto_catalogo_id, criado_em)
SELECT  id, cliente_id, grupo_id, ramo_atividade_id, emit_cnpj,
        codigo_produto_xml, descricao_produto_xml, produto_catalogo_id, criado_em
  FROM nfe_produto_vinculo
 WHERE {W_REGRAS_DELETE}
"""

INS_BKP_ITENS = f"""
INSERT INTO {BKP_ITENS}
       (item_id, produto_catalogo_id, cliente_id, emit_cnpj, codigo_produto)
SELECT i.id, i.produto_catalogo_id, n.cliente_id, n.emit_cnpj, i.codigo_produto
""" + FROM_ITENS_ALVO

# Desvincula exatamente o que foi para o backup — nunca mais, nunca menos.
UPD_DESVINCULA = f"""
UPDATE nfe_itens i
  JOIN {BKP_ITENS} b ON b.item_id = i.id
   SET i.produto_catalogo_id = NULL
"""

DEL_REGRAS = f"DELETE FROM nfe_produto_vinculo WHERE {W_REGRAS_DELETE}"


def num(v):
    """1234567 -> '1.234.567' (separador pt-BR)."""
    return f"{int(v or 0):,}".replace(',', '.')


def conectar():
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        charset='utf8mb4', autocommit=False,
    )


def relatorio(cur):
    """Levantamento read-only. Devolve (regras, total_itens, por_empresa)."""
    cur.execute(Q_CONTA_REGRAS)
    regras = cur.fetchone()

    cur.execute(Q_TOTAL_ITENS_CLASSIFICADOS)
    classificados = cur.fetchone()['qtd']

    cur.execute(Q_CONTA_ITENS)
    itens = cur.fetchone()['qtd']

    cur.execute(Q_ITENS_POR_EMPRESA)
    por_empresa = cur.fetchall()

    apagar = int(regras['ramo'] or 0) + int(regras['global_'] or 0)
    manter = int(regras['empresa'] or 0) + int(regras['grupo'] or 0)

    print()
    print('=' * 78)
    print('MEMORIZAÇÕES (nfe_produto_vinculo)')
    print('=' * 78)
    print(f"  A APAGAR   escopo RAMO ....................... {num(regras['ramo']):>10}")
    print(f"             escopo GLOBAL ..................... {num(regras['global_']):>10}")
    print(f"             {'-' * 44} {num(apagar):>10}")
    print(f"  A MANTER   escopo EMPRESA .................... {num(regras['empresa']):>10}")
    print(f"             escopo GRUPO ...................... {num(regras['grupo']):>10}")
    print(f"             {'-' * 44} {num(manter):>10}")
    print(f"  TOTAL na tabela hoje ........................... {num(regras['total']):>10}")
    if int(regras['anomalia'] or 0):
        print()
        print(f"  ATENÇÃO: {num(regras['anomalia'])} linha(s) com cliente_id E "
              f"ramo_atividade_id preenchidos.")
        print('           Elas contam como RAMO e SERÃO APAGADAS (o predicado de')
        print('           ramo tem precedência). Confira antes de aplicar:')
        print('           SELECT * FROM nfe_produto_vinculo')
        print('            WHERE cliente_id IS NOT NULL AND ramo_atividade_id IS NOT NULL;')

    print()
    print('=' * 78)
    print('ITENS A DESVINCULAR (nfe_itens de notas de ENTRADA)')
    print('=' * 78)
    print(f'  Itens de entrada classificados hoje ........... {num(classificados):>10}')
    print(f'  A DESVINCULAR (sem regra de EMPRESA) .......... {num(itens):>10}')
    pct = (itens / classificados * 100) if classificados else 0
    print(f'  Permanecem classificados ...................... '
          f'{num(classificados - itens):>10}   ({100 - pct:.1f}%)')

    print()
    print('  Quebra por empresa:')
    if not por_empresa:
        print('     (nenhum item a desvincular)')
    else:
        # Largura calculada sobre os rótulos JÁ resolvidos — senão o texto da
        # nota sem empresa (mais longo que os nomes) sairia truncado.
        nomes = [r['nome_razao_social'] or '(NF-e sem empresa definida)'
                 for r in por_empresa]
        w = min(max(max(len(n) for n in nomes), 20), 48)
        print(f"     {'cliente_id':>10}  {'nº':>6}  {'empresa'.ljust(w)}  {'itens':>9}")
        print(f"     {'-' * 10}  {'-' * 6}  {'-' * w}  {'-' * 9}")
        for r, nome in zip(por_empresa, nomes):
            cid = r['cliente_id']
            print(f"     {str(cid if cid is not None else '—'):>10}  "
                  f"{str(r['numero_cliente'] or '—'):>6}  {nome[:w].ljust(w)}  "
                  f"{num(r['qtd']):>9}")
        print(f"     {'-' * 10}  {'-' * 6}  {'-' * w}  {'-' * 9}")
        print(f"     {'TOTAL'.rjust(10 + 2 + 6 + 2 + w)}  {num(itens):>9}")

    return apagar, itens, por_empresa


def checar_backups_vazios(cur):
    """Impede um segundo --apply de misturar backups de execuções diferentes."""
    for tabela in (BKP_REGRAS, BKP_ITENS):
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (tabela,))
        if not cur.fetchone()['cnt']:
            continue
        cur.execute(f"SELECT COUNT(*) AS cnt FROM {tabela}")
        cnt = cur.fetchone()['cnt']
        if cnt:
            print()
            print(f'ABORTADO: a tabela de backup {tabela} já tem {num(cnt)} linha(s).')
            print('A Fase 2 provavelmente já foi aplicada. Se quiser rodar de novo,')
            print(f'renomeie ou remova {tabela} antes (e confira o que ela guarda).')
            return False
    return True


def aplicar(cnx, cur, apagar_esperado, itens_esperado):
    # DDL primeiro: no MySQL, CREATE TABLE faz commit implícito e encerraria a
    # transação no meio do caminho.
    cur.execute(DDL_BKP_REGRAS)
    cur.execute(DDL_BKP_ITENS)
    cnx.commit()

    if not checar_backups_vazios(cur):
        return 2

    print()
    print('=' * 78)
    print('APLICANDO (transação única)')
    print('=' * 78)

    cnx.start_transaction()
    try:
        cur.execute(INS_BKP_REGRAS)
        n_bkp_regras = cur.rowcount
        print(f'  1. backup das regras   -> {BKP_REGRAS}: {num(n_bkp_regras)} linha(s)')

        cur.execute(INS_BKP_ITENS)
        n_bkp_itens = cur.rowcount
        print(f'  2. backup dos itens    -> {BKP_ITENS}: {num(n_bkp_itens)} linha(s)')

        cur.execute(UPD_DESVINCULA)
        n_desv = cur.rowcount
        print(f'  3. itens desvinculados -> {num(n_desv)}')

        cur.execute(DEL_REGRAS)
        n_del = cur.rowcount
        print(f'  4. regras apagadas     -> {num(n_del)}')

        divergencias = []
        if n_bkp_regras != apagar_esperado:
            divergencias.append(
                f'backup de regras {n_bkp_regras} != previsto {apagar_esperado}')
        if n_bkp_itens != itens_esperado:
            divergencias.append(
                f'backup de itens {n_bkp_itens} != previsto {itens_esperado}')
        if n_desv != n_bkp_itens:
            divergencias.append(
                f'desvinculados {n_desv} != backup de itens {n_bkp_itens}')
        if n_del != n_bkp_regras:
            divergencias.append(
                f'apagadas {n_del} != backup de regras {n_bkp_regras}')
        if divergencias:
            cnx.rollback()
            print()
            print('ROLLBACK — as contagens não fecharam:')
            for d in divergencias:
                print(f'   - {d}')
            return 1

        cnx.commit()
        print()
        print('COMMIT OK.')
    except Exception as e:
        cnx.rollback()
        print()
        print(f'ROLLBACK por erro: {e}')
        return 1

    print()
    print('Para desfazer (enquanto os backups existirem):')
    print(f'  UPDATE nfe_itens i JOIN {BKP_ITENS} b ON b.item_id = i.id')
    print('     SET i.produto_catalogo_id = b.produto_catalogo_id;')
    print('  INSERT INTO nfe_produto_vinculo')
    print('         (id, cliente_id, grupo_id, ramo_atividade_id, emit_cnpj,')
    print('          codigo_produto_xml, descricao_produto_xml, produto_catalogo_id, criado_em)')
    print('  SELECT  id, cliente_id, grupo_id, ramo_atividade_id, emit_cnpj,')
    print('          codigo_produto_xml, descricao_produto_xml, produto_catalogo_id, criado_em')
    print(f'    FROM {BKP_REGRAS};')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true',
                    help='executa de verdade (padrão: apenas relata)')
    args = ap.parse_args()

    print()
    print('#' * 78)
    print('#  FASE 2 — limpeza de memorizações RAMO/GLOBAL'
          f'{"  [--APPLY]" if args.apply else "  [DRY-RUN]":>31}')
    print(f'#  banco: {Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}')
    print('#' * 78)

    cnx = conectar()
    cur = cnx.cursor(dictionary=True)
    try:
        apagar, itens, _ = relatorio(cur)

        if not args.apply:
            print()
            print('=' * 78)
            print('DRY-RUN — nada foi alterado.')
            print('Para executar:  python scripts/fase2_limpeza_memorizacoes.py --apply')
            print('=' * 78)
            return 0

        return aplicar(cnx, cur, apagar, itens)
    finally:
        cur.close()
        cnx.close()


if __name__ == '__main__':
    sys.exit(main())
