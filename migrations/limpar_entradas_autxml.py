# -*- coding: utf-8 -*-
"""Retroativo: conserta as ENTRADAS FANTASMA de autXML que a captura SEFAZ criou.

Contexto: capturando com o certificado de uma empresa (ex.: Qualicontax), o
distDFeInt também entrega notas em que a empresa é apenas AUTORIZADA a baixar o XML
(autXML) — NÃO é a destinatária. O motor antigo gravava essas notas como ENTRADA da
dona do certificado. O guard novo (dfe_captura._importar_nfe_completa via
_mesmo_titular) já impede isso daqui pra frente; este script limpa o histórico.

FANTASMA = linha origem='SEFAZ', tipo='entrada', cujo dest_cnpj (>=11 díg.) NÃO bate
com o CNPJ/CPF do cliente DONO da linha pela RAIZ (CNPJ: 8 primeiros dígitos — matriz
cobre filial; CPF: 11 inteiros). Matriz/filial do mesmo grupo NÃO é fantasma.

Cada fantasma cai em UMA de 3 categorias (a ação do --apply):

  (1) dest é OUTRO cliente cadastrado  -> RE-HOME: a nota é a entrada REAL do dest,
      só ficou atribuída à dona do cert. O UNIQUE(chave,'entrada') impede o cert do
      dest de recapturá-la, então NÃO se apaga: muda-se cliente_id para o dest e
      zera-se produto_catalogo_id dos itens (re-vínculo sob o cliente certo, como a
      Fase 2 de memorizações). É o caso do MOURA que puxou notas de URZEDA/JK/etc.

  (2) emit é cliente cadastrado (e dest não é cliente)  -> vira SAÍDA do emit
      (re-parse do xml_raw, igual ao backfill_saidas_sefaz) e APAGA a entrada
      fantasma. É o caso das notas da KET (dest = CPF de consumidor).

  (3) ninguém cadastrado  -> só APAGA a entrada fantasma (fica no backup).

BACKUP/ROLLBACK: no --apply, primeiro copia (CTAS) TODAS as entradas afetadas e seus
itens para autxml_entrada_bkp / autxml_itens_bkp; as SAÍDAS da cat (2) são criadas
antes (idempotentes — _save_nfe devolve 'dup' se já existirem); e só então, numa
transação única com conferência de contagem, faz o RE-HOME e os DELETEs. Diverge a
contagem -> ROLLBACK. O rollback manual (SQL) é impresso no fim.

Uso:
    python migrations/limpar_entradas_autxml.py            # DRY-RUN (nada grava)
    python migrations/limpar_entradas_autxml.py --apply    # aplica com backup
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                               # noqa: E402
from config import Config                            # noqa: E402
from utils.db_helper import execute_query            # noqa: E402
from utils.nfe_parser import parse_nfe_xml           # noqa: E402
from utils.nfe_import import _save_nfe               # noqa: E402

BKP_ENTRADA = 'autxml_entrada_bkp'
BKP_ITENS = 'autxml_itens_bkp'

_DIG = "REPLACE(REPLACE(REPLACE(REPLACE({c},'.',''),'/',''),'-',''),' ','')"


def _candidatas():
    """Entradas SEFAZ fantasma (dest ≠ dono por raiz), já classificadas.

    Traz emit_cli/dest_cli (cliente cadastrado que casa EXATO com emit/dest), se a
    saída da chave já existe, e se tem xml_raw para re-parse."""
    ddest = _DIG.format(c='n.dest_cnpj')
    downer = _DIG.format(c='c.cpf_cnpj')
    demit = _DIG.format(c='n.emit_cnpj')
    same_root = (
        f"( (CHAR_LENGTH({ddest})=14 AND CHAR_LENGTH({downer})=14 "
        f"     AND LEFT({ddest},8)=LEFT({downer},8)) OR ({ddest}={downer}) )"
    )
    dcli = _DIG.format(c='ce.cpf_cnpj')
    dcli_d = _DIG.format(c='cd.cpf_cnpj')
    return execute_query(
        "SELECT n.id, SUBSTRING(n.chave_acesso,21,2) AS modelo, n.chave_acesso, "
        "       n.cliente_id AS dono_id, c.numero_cliente AS dono_num, "
        "       c.nome_razao_social AS dono_nome, n.num_nota, n.valor_total, "
        "       n.emit_cnpj, n.emit_nome, n.dest_cnpj, n.dest_nome, "
        "       (n.xml_raw IS NOT NULL AND n.xml_raw <> '') AS tem_xml, "
        "       (SELECT ce.id FROM clientes ce WHERE " + dcli + " = " + demit + " LIMIT 1) AS emit_cli, "
        "       (SELECT ce.numero_cliente FROM clientes ce WHERE " + dcli + " = " + demit + " LIMIT 1) AS emit_num, "
        "       (SELECT cd.id FROM clientes cd WHERE " + dcli_d + " = " + ddest + " LIMIT 1) AS dest_cli, "
        "       (SELECT cd.numero_cliente FROM clientes cd WHERE " + dcli_d + " = " + ddest + " LIMIT 1) AS dest_num, "
        "       (SELECT s.id FROM nfe_importacoes s WHERE s.chave_acesso = n.chave_acesso "
        "          AND s.tipo='saida' LIMIT 1) AS saida_id "
        "FROM nfe_importacoes n JOIN clientes c ON c.id = n.cliente_id "
        "WHERE n.origem='SEFAZ' AND n.tipo='entrada' AND n.cliente_id IS NOT NULL "
        f"  AND CHAR_LENGTH({ddest}) >= 11 AND NOT {same_root} "
        "ORDER BY n.cliente_id, n.id",
        fetch=True,
    ) or []


def _categoria(r):
    """1=re-home ao dest, 2=vira saída do emit + apaga, 3=só apaga."""
    if r['dest_cli'] and r['dest_cli'] != r['dono_id']:
        return 1
    if r['emit_cli'] and r['emit_cli'] != r['dono_id']:
        return 2
    return 3


def conectar():
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        charset='utf8mb4', autocommit=False, time_zone='-03:00',
    )


def _in(ids):
    """'(1,2,3)' seguro (ids são inteiros vindos do banco)."""
    return "(" + ",".join(str(int(i)) for i in ids) + ")"


def relatorio(rows):
    cat1 = [r for r in rows if _categoria(r) == 1]
    cat2 = [r for r in rows if _categoria(r) == 2]
    cat3 = [r for r in rows if _categoria(r) == 3]

    print("=" * 88)
    print(f"ENTRADAS FANTASMA autXML (dest ≠ dono por raiz): {len(rows)}")
    print("=" * 88)

    por_dono = {}
    for r in rows:
        k = f"{r['dono_num']} - {(r['dono_nome'] or '')[:30]}"
        por_dono[k] = por_dono.get(k, 0) + 1
    print("\nPor DONO da entrada fantasma:")
    for k, v in sorted(por_dono.items(), key=lambda x: -x[1]):
        print(f"   {v:>4}  {k}")

    print("\n(1) RE-HOME ao dest (dest = outro cliente cadastrado): "
          f"{len(cat1)}")
    alvo = {}
    for r in cat1:
        alvo[r['dest_num']] = alvo.get(r['dest_num'], 0) + 1
    for num, v in sorted(alvo.items(), key=lambda x: -x[1]):
        print(f"       -> vira entrada do cliente #{num}: {v}")

    print(f"\n(2) VIRA SAÍDA do emit + APAGA (emit cadastrado): {len(cat2)}")
    alvo2 = {}
    sem_xml_saida = 0
    for r in cat2:
        alvo2[r['emit_num']] = alvo2.get(r['emit_num'], 0) + 1
        if not r['saida_id'] and not r['tem_xml']:
            sem_xml_saida += 1
    for num, v in sorted(alvo2.items(), key=lambda x: -x[1]):
        print(f"       -> saída do cliente #{num}: {v}")
    if sem_xml_saida:
        print(f"       ! {sem_xml_saida} sem saída e SEM xml_raw -> serão PULADAS "
              "(não apaga, não dá pra reconstruir a saída)")

    print(f"\n(3) SÓ APAGA (ninguém cadastrado): {len(cat3)}")

    return cat1, cat2, cat3


def aplicar(cnx, cur, cat1, cat2, cat3):
    afetadas = [r['id'] for r in (cat1 + cat2 + cat3)]
    if not afetadas:
        print("\nNada a fazer.")
        return 0

    # Guard: backups não podem já existir (evita misturar execuções).
    for t in (BKP_ENTRADA, BKP_ITENS):
        cur.execute("SELECT COUNT(*) c FROM information_schema.tables "
                    "WHERE table_schema=DATABASE() AND table_name=%s", (t,))
        if cur.fetchone()['c']:
            print(f"\nABORTADO: a tabela de backup {t} já existe. "
                  "Renomeie/remova antes de rodar de novo (confira o que guarda).")
            return 2

    print("\n" + "=" * 88)
    print("APLICANDO (backup CTAS -> saídas idempotentes -> transação de re-home/delete)")
    print("=" * 88)

    # 1) BACKUP (CTAS faz commit implícito): snapshot de TODAS as entradas afetadas
    #    e seus itens ANTES de qualquer mudança.
    cur.execute(f"CREATE TABLE {BKP_ENTRADA} AS SELECT * FROM nfe_importacoes "
                f"WHERE id IN {_in(afetadas)}")
    cur.execute(f"CREATE TABLE {BKP_ITENS} AS SELECT * FROM nfe_itens "
                f"WHERE nfe_id IN {_in(afetadas)}")
    cnx.commit()
    cur.execute(f"SELECT COUNT(*) c FROM {BKP_ENTRADA}")
    n_bkp_e = cur.fetchone()['c']
    cur.execute(f"SELECT COUNT(*) c FROM {BKP_ITENS}")
    n_bkp_i = cur.fetchone()['c']
    print(f"  1. backup -> {BKP_ENTRADA}: {n_bkp_e} entrada(s), "
          f"{BKP_ITENS}: {n_bkp_i} item(ns)")
    if n_bkp_e != len(afetadas):
        print(f"  ABORTADO: backup gravou {n_bkp_e} != {len(afetadas)} esperadas.")
        return 1

    # 2) SAÍDAS da cat (2), via core compartilhado (idempotente: 'dup' se já existe).
    #    Feito ANTES da transação para não misturar a conexão-pool do _save_nfe com
    #    a transação crua abaixo. Só apaga a entrada de quem tiver saída garantida.
    cat2_del = []
    cat2_pula = []
    criadas = ja = err = 0
    for r in cat2:
        chave = r['chave_acesso']
        if r['saida_id']:
            ja += 1
            cat2_del.append(r)
            continue
        if not r['tem_xml']:
            cat2_pula.append(r)
            continue
        try:
            full = execute_query("SELECT xml_raw FROM nfe_importacoes WHERE id=%s",
                                 (r['id'],), fetch=True, fetch_one=True)
            parsed = parse_nfe_xml(full['xml_raw'])
            res = _save_nfe(parsed, f"{chave}.xml", 'SEFAZ', full['xml_raw'],
                            cliente_id=r['emit_cli'], tipo='saida')
            criadas += (res == 'ok')
            ja += (res == 'dup')
            cat2_del.append(r)
        except Exception as exc:
            err += 1
            cat2_pula.append(r)
            print(f"     ! erro criando saída da chave ...{chave[-6:]}: {exc}")
    print(f"  2. saídas cat(2): criadas={criadas} já_existiam={ja} "
          f"erro={err} puladas={len(cat2_pula)}")

    # 3) Transação única: re-home cat(1) + delete cat(2 com saída) + cat(3).
    del_ids = [r['id'] for r in (cat2_del + cat3)]
    if cnx.in_transaction:
        cnx.rollback()
    cnx.start_transaction()
    try:
        n_rehome = n_itens_null = 0
        for r in cat1:                       # re-home 1 a 1 (cada dest é diferente)
            # grupo_id -> NULL: a linha NÃO pode manter o grupo do dono ANTIGO. Como
            # cliente↔grupo é N:N (um dest pode ter 0 ou vários grupos), não há grupo
            # único a herdar; NULL é o valor consistente (todas as entradas SEFAZ têm
            # grupo_id=NULL). Explícito para nunca vazar o grupo antigo — mesmo que a
            # linha venha a receber grupo no futuro.
            cur.execute("UPDATE nfe_importacoes SET cliente_id=%s, grupo_id=NULL "
                        "WHERE id=%s AND tipo='entrada'", (r['dest_cli'], r['id']))
            n_rehome += cur.rowcount
        if cat1:
            cur.execute("UPDATE nfe_itens SET produto_catalogo_id=NULL "
                        f"WHERE nfe_id IN {_in([r['id'] for r in cat1])}")
            n_itens_null = cur.rowcount

        n_del_it = n_del_e = 0
        if del_ids:
            cur.execute(f"DELETE FROM nfe_itens WHERE nfe_id IN {_in(del_ids)}")
            n_del_it = cur.rowcount
            cur.execute(f"DELETE FROM nfe_importacoes WHERE id IN {_in(del_ids)} "
                        "AND tipo='entrada' AND origem='SEFAZ'")
            n_del_e = cur.rowcount

        print(f"  3. re-home={n_rehome} (itens zerados={n_itens_null})  "
              f"apagadas={n_del_e} (itens apagados={n_del_it})")

        divs = []
        if n_rehome != len(cat1):
            divs.append(f"re-home {n_rehome} != {len(cat1)}")
        if n_del_e != len(del_ids):
            divs.append(f"apagadas {n_del_e} != {len(del_ids)}")
        if divs:
            cnx.rollback()
            print("  ROLLBACK — contagens não fecharam: " + "; ".join(divs))
            return 1
        cnx.commit()
        print("  COMMIT OK.")
    except Exception as e:
        cnx.rollback()
        print(f"  ROLLBACK por erro: {e}")
        return 1

    _print_rollback([r['id'] for r in cat1], del_ids)
    return 0


def _print_rollback(rehome_ids, del_ids):
    print("\n" + "-" * 88)
    print("ROLLBACK MANUAL (enquanto os backups existirem):")
    if rehome_ids:
        print("  -- desfaz o re-home (cliente_id, grupo_id e vínculo dos itens):")
        print(f"  UPDATE nfe_importacoes n JOIN {BKP_ENTRADA} b ON b.id=n.id "
              f"SET n.cliente_id=b.cliente_id, n.grupo_id=b.grupo_id "
              f"WHERE b.id IN {_in(rehome_ids)};")
        print(f"  UPDATE nfe_itens i JOIN {BKP_ITENS} b ON b.id=i.id "
              f"SET i.produto_catalogo_id=b.produto_catalogo_id "
              f"WHERE b.nfe_id IN {_in(rehome_ids)};")
    if del_ids:
        print("  -- restaura as entradas apagadas e seus itens:")
        print(f"  INSERT INTO nfe_importacoes SELECT * FROM {BKP_ENTRADA} "
              f"WHERE id IN {_in(del_ids)};")
        print(f"  INSERT INTO nfe_itens SELECT * FROM {BKP_ITENS} "
              f"WHERE nfe_id IN {_in(del_ids)};")
        print("  -- (opcional) remover as saídas criadas na cat(2):")
        print("  --   confira antes: SELECT id,chave_acesso,cliente_id FROM "
              "nfe_importacoes WHERE tipo='saida' AND origem='SEFAZ';")
    print(f"  -- concluído o rollback: DROP TABLE {BKP_ENTRADA}, {BKP_ITENS};")


def main():
    aplicar_flag = '--apply' in sys.argv
    print("\n" + "#" * 88)
    print("#  LIMPEZA DE ENTRADAS FANTASMA autXML"
          f"{'  [--APPLY]' if aplicar_flag else '  [DRY-RUN]'}")
    print(f"#  banco: {Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")
    print("#" * 88)

    rows = _candidatas()
    cat1, cat2, cat3 = relatorio(rows)

    if not aplicar_flag:
        print("\n" + "=" * 88)
        print("DRY-RUN — nada foi alterado.")
        print("Para aplicar:  python migrations/limpar_entradas_autxml.py --apply")
        print("=" * 88)
        return 0

    cnx = conectar()
    cur = cnx.cursor(dictionary=True)
    try:
        return aplicar(cnx, cur, cat1, cat2, cat3)
    finally:
        cur.close()
        cnx.close()


if __name__ == '__main__':
    sys.exit(main())
