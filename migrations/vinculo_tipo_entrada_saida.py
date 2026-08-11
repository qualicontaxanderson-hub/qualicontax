# -*- coding: utf-8 -*-
"""Separa as memorizações (nfe_produto_vinculo) por TIPO: entrada (Compras) x saída.

Hoje o vínculo é (cliente_id, grupo_id, emit_cnpj, codigo_produto_xml) SEM tipo. O
item recebe produto_catalogo_id no import casando esse vínculo — então uma
memorização feita em COMPRAS classifica também as SAÍDAS cujo emitente/código batem
(toda saída tem emit = o próprio posto; os códigos de combustível se repetem em todo
cupom → vazamento em massa). Decisão: memorizações existentes valem SÓ para Compras;
as Saídas começam do zero.

DOIS PASSOS SEPARADOS, para a sequência segura (aplicar --schema, deployar o código
tipo-scoped, e só então --desvincular, recontando na hora):

  --schema       ADD COLUMN tipo ENUM('entrada','saida') NOT NULL DEFAULT 'entrada'
                 (linhas atuais -> 'entrada') + troca a UNIQUE para incluir tipo.
                 ADITIVO: não quebra o código atual.

  --desvincular  produto_catalogo_id -> NULL nos itens de nota tipo='saida' (as
                 saídas começam do zero). RECONTA na hora (o nº cresce enquanto o
                 Q-Robô importa). Faz backup dos itens antes.

Sem --apply, cada modo é DRY-RUN (não grava). Uso:
  python migrations/vinculo_tipo_entrada_saida.py --schema                # dry-run
  python migrations/vinculo_tipo_entrada_saida.py --schema --apply        # aplica
  python migrations/vinculo_tipo_entrada_saida.py --desvincular           # dry-run
  python migrations/vinculo_tipo_entrada_saida.py --desvincular --apply   # aplica
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                               # noqa: E402
from config import Config                            # noqa: E402

BKP_VINC = 'nfe_produto_vinculo_bkp_tipo'
BKP_ITENS = 'nfe_itens_saida_desvinc_bkp_tipo'

WHERE_ITENS_SAIDA = ("FROM nfe_itens i JOIN nfe_importacoes n ON n.id = i.nfe_id "
                     "WHERE n.tipo = 'saida' AND i.produto_catalogo_id IS NOT NULL")


def conectar():
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        charset='utf8mb4', autocommit=False)


def _tem_col_tipo(cur):
    cur.execute("SELECT COUNT(*) c FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name='nfe_produto_vinculo' "
                "AND column_name='tipo'")
    return cur.fetchone()['c'] > 0


def _uk_atual(cur):
    cur.execute("SELECT GROUP_CONCAT(column_name ORDER BY seq_in_index) cols "
                "FROM information_schema.statistics WHERE table_schema=DATABASE() "
                "AND table_name='nfe_produto_vinculo' AND index_name='uk_vinculo'")
    r = cur.fetchone()
    return r['cols'] if r else '(sem uk_vinculo)'


def _tabela_existe(cur, nome):
    cur.execute("SELECT COUNT(*) c FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name=%s", (nome,))
    return cur.fetchone()['c'] > 0


# ---------------------------------------------------------------- SCHEMA
def schema(cnx, cur, aplicar):
    tem = _tem_col_tipo(cur)
    uk = _uk_atual(cur)
    cur.execute("SELECT COUNT(*) n FROM nfe_produto_vinculo")
    nvinc = cur.fetchone()['n']
    print("=" * 82)
    print(f"[--schema] {'APPLY' if aplicar else 'DRY-RUN'}")
    print("=" * 82)
    print(f"  nfe_produto_vinculo: {nvinc} linha(s)  (todas -> tipo='entrada')")
    print(f"  coluna 'tipo' já existe? {'SIM' if tem else 'NÃO'}")
    print(f"  UNIQUE key atual: {uk}")
    print("\n  AÇÕES:")
    print("    1) ALTER TABLE nfe_produto_vinculo "
          "ADD COLUMN tipo ENUM('entrada','saida') NOT NULL DEFAULT 'entrada'")
    print("    2) DROP INDEX uk_vinculo; ADD UNIQUE uk_vinculo "
          "(cliente_id, grupo_id, emit_cnpj, codigo_produto_xml, tipo)")
    if not aplicar:
        print(f"\n  (backup de segurança: {BKP_VINC} = snapshot dos {nvinc} vínculos)")
        print("\n  DRY-RUN — nada alterado. (é ADITIVO; não quebra o código atual)")
        print("  ROLLBACK após apply: ALTER ... DROP INDEX uk_vinculo, ADD UNIQUE (…sem tipo…);")
        print("                       ALTER ... DROP COLUMN tipo;")
        return 0

    # Backup dos vínculos ANTES do ALTER (guardrail). CTAS = commit implícito.
    if _tabela_existe(cur, BKP_VINC):
        print(f"\n  ABORTADO: backup {BKP_VINC} já existe. Remova/renomeie antes.")
        return 2
    cur.execute(f"CREATE TABLE {BKP_VINC} AS SELECT * FROM nfe_produto_vinculo")
    cnx.commit()
    cur.execute(f"SELECT COUNT(*) c FROM {BKP_VINC}")
    print(f"    -> backup {BKP_VINC}: {cur.fetchone()['c']} vínculos")

    if not tem:
        cur.execute("ALTER TABLE nfe_produto_vinculo "
                    "ADD COLUMN tipo ENUM('entrada','saida') NOT NULL DEFAULT 'entrada'")
        print("    -> coluna tipo criada (existentes = 'entrada')")
    else:
        print("    -> coluna tipo já existia (no-op)")
    cur.execute("ALTER TABLE nfe_produto_vinculo DROP INDEX uk_vinculo, "
                "ADD UNIQUE KEY uk_vinculo (cliente_id, grupo_id, emit_cnpj, "
                "codigo_produto_xml, tipo)")
    print("    -> UNIQUE agora inclui tipo. UNIQUE:", _uk_atual(cur))
    print("\n  ROLLBACK:")
    print("    ALTER TABLE nfe_produto_vinculo DROP INDEX uk_vinculo,")
    print("      ADD UNIQUE KEY uk_vinculo (cliente_id, grupo_id, emit_cnpj, codigo_produto_xml);")
    print("    ALTER TABLE nfe_produto_vinculo DROP COLUMN tipo;")
    return 0


# ------------------------------------------------------------ DESVINCULAR
def desvincular(cnx, cur, aplicar):
    cur.execute("SELECT COUNT(*) n " + WHERE_ITENS_SAIDA)
    alvo = cur.fetchone()['n']
    print("=" * 82)
    print(f"[--desvincular] {'APPLY' if aplicar else 'DRY-RUN'}   (recontado agora)")
    print("=" * 82)
    cur.execute("SELECT n.cliente_id, c.numero_cliente num, c.nome_razao_social nome, "
                "COUNT(*) qtd FROM nfe_itens i JOIN nfe_importacoes n ON n.id=i.nfe_id "
                "LEFT JOIN clientes c ON c.id=n.cliente_id "
                "WHERE n.tipo='saida' AND i.produto_catalogo_id IS NOT NULL "
                "GROUP BY n.cliente_id, c.numero_cliente, c.nome_razao_social ORDER BY qtd DESC")
    for r in cur.fetchall():
        print(f"    cliente_id={r['cliente_id']} (nº {r['num']} - "
              f"{(r['nome'] or '')[:34]}): {r['qtd']} itens")
    print(f"    {'-'*58}\n    TOTAL a desvincular AGORA: {alvo} itens de saída")
    print("\n  AÇÃO: UPDATE nfe_itens i JOIN nfe_importacoes n ON n.id=i.nfe_id "
          "SET i.produto_catalogo_id=NULL WHERE n.tipo='saida' AND i.produto_catalogo_id IS NOT NULL")
    if not aplicar:
        print("\n  DRY-RUN — nada alterado. Faça DEPOIS do código tipo-scoped estar no ar.")
        return 0

    if _tabela_existe(cur, BKP_ITENS):
        print(f"\n  ABORTADO: backup {BKP_ITENS} já existe (execução anterior). Remova antes.")
        return 2
    # backup dos itens a desvincular (CTAS = commit implícito)
    cur.execute(f"CREATE TABLE {BKP_ITENS} AS "
                f"SELECT i.id, i.nfe_id, i.produto_catalogo_id " + WHERE_ITENS_SAIDA)
    cnx.commit()
    cur.execute(f"SELECT COUNT(*) c FROM {BKP_ITENS}")
    nb = cur.fetchone()['c']
    print(f"\n  backup -> {BKP_ITENS}: {nb} itens")

    if cnx.in_transaction:
        cnx.rollback()
    cnx.start_transaction()
    try:
        cur.execute("UPDATE nfe_itens i JOIN nfe_importacoes n ON n.id=i.nfe_id "
                    "SET i.produto_catalogo_id=NULL "
                    "WHERE n.tipo='saida' AND i.produto_catalogo_id IS NOT NULL")
        nd = cur.rowcount
        print(f"  desvinculados: {nd}")
        if nd != nb:
            cnx.rollback()
            print(f"  ROLLBACK: desvinculados {nd} != backup {nb}.")
            return 1
        cnx.commit()
        print("  COMMIT OK.")
    except Exception as e:
        cnx.rollback()
        print(f"  ROLLBACK por erro: {e}")
        return 1
    print("\n  ROLLBACK MANUAL:")
    print(f"    UPDATE nfe_itens i JOIN {BKP_ITENS} b ON b.id=i.id "
          "SET i.produto_catalogo_id=b.produto_catalogo_id;")
    print(f"    DROP TABLE {BKP_ITENS};")
    return 0


def main():
    modo_schema = '--schema' in sys.argv
    modo_desv = '--desvincular' in sys.argv
    aplicar = '--apply' in sys.argv
    print("\n" + "#" * 82)
    print("#  nfe_produto_vinculo por TIPO (entrada x saída)")
    print(f"#  banco: {Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")
    print("#" * 82)
    if modo_schema == modo_desv:      # nenhum ou ambos
        print("\nUse UM modo: --schema  ou  --desvincular   (+ --apply para gravar).")
        print("Ex.: python migrations/vinculo_tipo_entrada_saida.py --schema")
        return 2
    cnx = conectar()
    cur = cnx.cursor(dictionary=True)
    try:
        return schema(cnx, cur, aplicar) if modo_schema else desvincular(cnx, cur, aplicar)
    finally:
        cur.close()
        cnx.close()


if __name__ == '__main__':
    sys.exit(main())
