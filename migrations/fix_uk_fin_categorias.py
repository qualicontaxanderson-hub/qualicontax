# -*- coding: utf-8 -*-
"""A chave unica de fin_categorias passa a considerar o PAI.

Ela nasceu como (tipo, grupo, nome), o que so funciona num plano de dois
niveis. Com tres niveis — GRUPO > CATEGORIA > SUBCATEGORIA — o mesmo nome
precisa existir sob pais diferentes: "Manutencao" vale para o SPIN, para o
Classic e para o F250, e "Simples Nacional" e ao mesmo tempo uma categoria
de imposto e uma subcategoria de Parcelamentos.

Sem isso, o banco aceita o primeiro e RECUSA os demais. Pior: o
``execute_query`` engole o erro 1062 e devolve None, entao a recusa nao
aparece — a semeadura anterior relatou 177 nos criados quando 44 tinham sido
silenciosamente rejeitados.

Nao da para simplesmente acrescentar ``pai_id`` ao indice: em MySQL varias
linhas com NULL nao colidem entre si, e todas as categorias de 1o nivel tem
pai_id NULL — perderiamos a protecao justamente no nivel de cima. Por isso a
coluna gerada ``pai_key``, que troca NULL por 0 e volta a ser comparavel.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv                                # noqa: E402
load_dotenv()

from utils.db_helper import execute_query                     # noqa: E402


def indices():
    r = execute_query('SHOW INDEX FROM fin_categorias', fetch=True) or []
    d = {}
    for x in r:
        d.setdefault(x['Key_name'], []).append(x['Column_name'])
    return d


def tem_coluna(nome):
    r = execute_query('SHOW COLUMNS FROM fin_categorias LIKE %s', (nome,),
                      fetch=True, fetch_one=True)
    return bool(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    ix = indices()
    print('indices hoje:')
    for k, v in ix.items():
        print('   %-12s %s' % (k, ' + '.join(v)))
    print()
    print('pai_key existe?', tem_coluna('pai_key'))

    # Colisoes que a chave atual esta bloqueando agora mesmo.
    dup = execute_query(
        """SELECT tipo, grupo, nome, COUNT(*) n FROM fin_categorias
            GROUP BY tipo, grupo, nome HAVING n > 1""", fetch=True) or []
    print('nomes repetidos hoje (deveriam poder existir):', len(dup))

    if not args.apply:
        print()
        print('[dry-run] faria: ADD COLUMN pai_key + DROP uk_cat + CREATE uk_cat2')
        print('          (nao apaga nenhuma linha)')
        return 0

    if not tem_coluna('pai_key'):
        execute_query('ALTER TABLE fin_categorias '
                      'ADD COLUMN pai_key INT AS (IFNULL(pai_id, 0)) STORED')
        print('OK: coluna gerada pai_key criada.')

    if 'uk_cat' in indices():
        execute_query('ALTER TABLE fin_categorias DROP INDEX uk_cat')
        print('OK: uk_cat antiga removida.')

    if 'uk_cat2' not in indices():
        execute_query('ALTER TABLE fin_categorias '
                      'ADD UNIQUE KEY uk_cat2 (tipo, grupo, pai_key, nome)')
        print('OK: uk_cat2 (tipo, grupo, pai_key, nome) criada.')

    print()
    print('indices agora:')
    for k, v in indices().items():
        print('   %-12s %s' % (k, ' + '.join(v)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
