# -*- coding: utf-8 -*-
"""Cobrança dos honorários ganha a situação SEM_MOVIMENTO (25/11/2026).

Anderson: "se for feito o grupo tem que ser escolhido quem e qual CNPJ pagará. Sem Movimento lógico não pagará, Via
Grupo também... agora se for empresas pagantes ter a opção de distribuir". A coluna ``clientes.cobranca`` era
VARCHAR(10) (PROPRIA | VIA_GRUPO | PENDENTE) e não cabe SEM_MOVIMENTO (13 letras): alarga para VARCHAR(15).
Nada muda nos valores já gravados.

    python migrations/migracao_21_cobranca_sem_movimento.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402


def _coluna(tabela, coluna):
    return execute_query("""SELECT COLUMN_TYPE AS tipo, COLUMN_COMMENT AS com FROM INFORMATION_SCHEMA.COLUMNS
                             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s""",
                         (tabela, coluna), fetch=True, fetch_one=True)


def aplicar(so_ver=False):
    c = _coluna('clientes', 'cobranca')
    if not c:
        print('  clientes.cobranca nao existe (rode a migracao 01 antes)'); return
    print('  clientes.cobranca hoje: %s' % c['tipo'])
    if c['tipo'].lower() == 'varchar(15)':
        print('  ja esta em VARCHAR(15)'); return
    print('  %s para VARCHAR(15)' % ('alargaria' if so_ver else 'alargando'))
    if not so_ver:
        execute_query("ALTER TABLE clientes MODIFY COLUMN cobranca VARCHAR(15) NULL COMMENT 'PROPRIA | VIA_GRUPO | SEM_MOVIMENTO | PENDENTE'", fetch=False)
        print('  ok: %s' % _coluna('clientes', 'cobranca')['tipo'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    aplicar(so_ver=not a.apply)


if __name__ == '__main__':
    main()
