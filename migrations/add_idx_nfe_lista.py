# -*- coding: utf-8 -*-
"""Índice de cobertura das LISTAS de Entradas e Saídas por empresa.

MEDIDO EM 13/09/2026
--------------------
A lista de Saídas da Westpark (259 mil notas do Q-Robô) levou **498 s** — e
com o teto de 30 s por consulta ela simplesmente não abre. Duas causas na
consulta: (1) ``COUNT(*) OVER()`` e ``SUM(valor_*) OVER()`` obrigam o MySQL a
ler TODAS as linhas do filtro (259 mil linhas x ~8 KB de XML inline = 2 GB)
só para devolver 50; (2) um ``LEFT JOIN (SELECT nfe_id, COUNT(*) ... FROM
nfe_itens GROUP BY nfe_id)`` agrega 1,17 milhão de itens a cada página.

A reescrita da rota separa página (50 linhas) de totais (uma consulta de
agregado). Este índice é o que faz o agregado ser index-only:

``idx_lista (cliente_id, tipo, data_emissao, cancelada, valor_total,
            valor_icms, valor_pis, valor_cofins)``

* ``cliente_id`` e ``tipo`` são o filtro de toda lista por empresa;
* ``data_emissao`` cobre o período e o ORDER BY da página;
* ``cancelada`` porque o filtro "canceladas" entra no WHERE;
* os quatro valores são exatamente os KPIs do topo da tela.

Sem empresa selecionada (visão geral) o agregado vira uma varredura do
índice inteiro (~60 MB) em vez da tabela (9 GB).

CUSTO: uma passada pela tabela (LOCK=NONE; os anteriores levaram 218-394 s).
~60 MB. Rollback: DROP INDEX.

    python migrations/add_idx_nfe_lista.py            # dry-run
    python migrations/add_idx_nfe_lista.py --apply --medir
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELA = 'nfe_importacoes'
INDICE = 'idx_lista'
COLUNAS = ('(cliente_id, tipo, data_emissao, cancelada, valor_total, '
           'valor_icms, valor_pis, valor_cofins)')
PROVA = (f"SELECT COUNT(*) t, COALESCE(SUM(valor_total),0) v, COALESCE(SUM(valor_icms),0) i "
         f"FROM {TABELA} n WHERE n.tipo = 'saida' AND n.cliente_id = 98")


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
    ap = argparse.ArgumentParser(description='Índice de cobertura das listas por empresa.')
    ap.add_argument('--apply', action='store_true', help='executa; sem isto é só dry-run')
    ap.add_argument('--medir', action='store_true', help='cronometra a consulta de prova depois')
    args = ap.parse_args()

    tem = indices()
    if not tem:
        print(f'ERRO: tabela {TABELA} não encontrada.')
        return 1
    print(f'índices hoje: {", ".join(sorted(tem))}')
    if INDICE in tem:
        print(f'{INDICE} já existe — nada a fazer.')
        return 0
    sql = f'ALTER TABLE {TABELA} ADD INDEX {INDICE} {COLUNAS}, ALGORITHM=INPLACE, LOCK=NONE'
    print('ANTES — totais da lista de Saídas de uma empresa (EXPLAIN):')
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
