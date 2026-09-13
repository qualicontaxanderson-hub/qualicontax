# -*- coding: utf-8 -*-
"""Índice de cobertura para os contadores da home por MÊS DE EMISSÃO.

O QUE FALTAVA DEPOIS DO idx_painel (13/09/2026)
-----------------------------------------------
``idx_painel`` resolveu os painéis que filtram por ``tipo``/``origem``. Sobrou
o cartão de contadores da home (entradas e saídas do mês, valor, NFC-e x
NF-e, mês anterior), que precisa de ``cancelada``, ``valor_total`` e
``chave_acesso`` — colunas fora de qualquer índice. Mesmo restrito aos dois
últimos meses por ``data_emissao`` (idx_data), o MySQL acha a entrada no
índice e ainda vai buscar a LINHA de cada nota, e a linha carrega o XML
(~8 KB): 320 mil linhas x 8 KB = 2,5 GB lidos para somar oito números.

Com todas as colunas da consulta dentro do índice, ela vira index-only:
percorre ~25 MB de índice em vez de 2,5 GB de tabela.

``idx_mes (data_emissao, tipo, cancelada, valor_total, chave_acesso)``
    ``data_emissao`` primeiro porque todo contador é "deste mês" / "mês
    anterior" — range na primeira coluna; as outras quatro são exatamente as
    que a consulta lê. ``chave_acesso`` inteira (varchar(44)) e não prefixo:
    prefixo não serve para cobertura, e o SUBSTRING(chave,21,2) que separa
    NFC-e (65) de NF-e (55) precisa da coluna toda.

Também cobre o "dia a dia do mês" (``DAY(data_emissao) ... WHERE
COALESCE(cancelada,0)=0 AND data_emissao>=mês``), que hoje leva 13s.

CUSTO
-----
Uma passada pela tabela (9,2 GB de dados, ~1,2 milhão de linhas em 13/09,
crescendo ~200 mil por noite com os robôs). Os dois índices anteriores
levaram 394s com LOCK=NONE; este tem uma coluna de 44 chars e deve ficar
entre 5 e 10 min. ~110 MB de espaço. Rollback: DROP INDEX.

    python migrations/add_idx_nfe_mes.py            # dry-run
    python migrations/add_idx_nfe_mes.py --apply --medir

Idempotente: não faz nada se já existe.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELA = 'nfe_importacoes'
INDICE = 'idx_mes'
COLUNAS = '(data_emissao, tipo, cancelada, valor_total, chave_acesso)'
_MES = "(CURDATE() - INTERVAL (DAY(CURDATE())-1) DAY)"
_PMES = f"({_MES} - INTERVAL 1 MONTH)"
PROVA = (
    f"SELECT SUM(tipo='entrada' AND COALESCE(cancelada,0)=0 AND data_emissao>={_MES}) ent_mes, "
    f" COALESCE(SUM(CASE WHEN tipo='entrada' AND COALESCE(cancelada,0)=0 AND data_emissao>={_MES} "
    f"   THEN valor_total END),0) ent_valor, "
    f" SUM(tipo='saida' AND COALESCE(cancelada,0)=0 AND data_emissao>={_MES} "
    f"   AND SUBSTRING(chave_acesso,21,2)='65') sai_nfce "
    f"FROM {TABELA} WHERE data_emissao >= {_PMES}")


def indices():
    r = execute_query(
        'SELECT DISTINCT INDEX_NAME i FROM INFORMATION_SCHEMA.STATISTICS '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', (TABELA,),
        fetch=True) or []
    return {x['i'] for x in r}


def _explain(sql):
    for r in execute_query('EXPLAIN ' + sql, fetch=True) or []:
        print(f"   type={r.get('type')}  key={r.get('key')}  "
              f"rows={r.get('rows')}  extra={r.get('Extra')}")


def main():
    ap = argparse.ArgumentParser(description='Índice de cobertura dos contadores do mês.')
    ap.add_argument('--apply', action='store_true', help='executa; sem isto é só dry-run')
    ap.add_argument('--medir', action='store_true',
                    help='depois de criar, roda a consulta de prova e cronometra')
    args = ap.parse_args()

    tem = indices()
    if not tem:
        print(f'ERRO: tabela {TABELA} não encontrada.')
        return 1
    print(f'índices hoje: {", ".join(sorted(tem))}')
    if INDICE in tem:
        print(f'{INDICE} já existe — nada a fazer.')
        return 0

    sql = (f'ALTER TABLE {TABELA} ADD INDEX {INDICE} {COLUNAS}, '
           f'ALGORITHM=INPLACE, LOCK=NONE')
    print('ANTES — contadores da home (EXPLAIN, não executa):')
    _explain(PROVA)
    print()
    if not args.apply:
        print('SQL QUE SERIA EXECUTADO:')
        print('   ' + sql + ';')
        print()
        print('ROLLBACK, se precisar:')
        print(f'   ALTER TABLE {TABELA} DROP INDEX {INDICE};')
        print()
        print('DRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        return 0

    print('EXECUTANDO (minutos; leitura e escrita seguem funcionando):')
    print('   ' + sql)
    t0 = time.time()
    r = execute_query(sql, fetch=False)
    dur = time.time() - t0
    if r is None:
        print(f'FALHOU depois de {dur:.0f}s. O índice não nasceu; nada mais mudou.')
        return 1
    print(f'OK em {dur:.0f}s.')
    if INDICE not in indices():
        print('ATENÇÃO: o comando voltou sem erro mas o índice não aparece.')
        return 1
    print('DEPOIS:')
    _explain(PROVA)
    if args.medir:
        t0 = time.time()
        execute_query(PROVA, fetch=True, fetch_one=True)
        print(f'   tempo real: {time.time() - t0:.2f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
