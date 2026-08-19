# -*- coding: utf-8 -*-
"""Adiciona socios_clientes.pf_cliente_id — sócio vira VÍNCULO com o cadastro PF.

O QUE MUDA
----------
O modal "Adicionar Sócio" era digitação livre: nome, CPF, e-mail e telefone
soltos, sem relação nenhuma com o cadastro de clientes. O Anderson decidiu em
19/08/2026 que sócio NÃO se digita — escolhe-se uma Pessoa Física que já exista
no cadastro (``clientes.tipo_pessoa = 'PF'``). Esta coluna guarda esse vínculo.

As colunas nome/cpf/email/telefone continuam existindo como RETRATO do momento
do vínculo, mas a exibição passa a vir do cadastro vivo (LEFT JOIN em
``SocioCliente.get_by_cliente``): corrigiu o telefone da pessoa no cadastro,
corrigiu em todas as sociedades dela.

A tabela está VAZIA em produção (0 linhas em 19/08/2026) — a coluna nasce sem
nenhum registro órfão para tratar.

    python migrations/add_socio_pf_cliente_id.py            # dry-run
    python migrations/add_socio_pf_cliente_id.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402


def coluna_existe() -> bool:
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'socios_clientes'
              AND COLUMN_NAME = 'pf_cliente_id'""",
        fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true', help='executa o ALTER (sem isso é dry-run)')
    args = ap.parse_args()

    if coluna_existe():
        print('socios_clientes.pf_cliente_id já existe — nada a fazer.')
        return 0

    total = (execute_query('SELECT COUNT(*) AS n FROM socios_clientes',
                           fetch=True, fetch_one=True) or {}).get('n', '?')
    print(f'socios_clientes tem {total} linha(s); a coluna nova nasce NULL em todas.')

    sql = ('ALTER TABLE socios_clientes '
           'ADD COLUMN pf_cliente_id INT NULL AFTER cliente_id, '
           'ADD INDEX idx_socios_pf_cliente (pf_cliente_id)')
    if not args.apply:
        print('[dry-run] executaria:')
        print('  ' + sql)
        return 0

    execute_query(sql)
    assert coluna_existe(), 'ALTER rodou mas a coluna não apareceu'
    print('OK: coluna pf_cliente_id criada (com índice).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
