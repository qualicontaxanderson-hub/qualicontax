# -*- coding: utf-8 -*-
"""Cobrança — para quem o e-mail do boleto foi (03/11/2026).

``fin_boletos.email_enviado_em`` já existia; falta guardar o endereço, para a tela da nota
dizer "enviado em 03/11 10:20 para fulano@..." e o reenvio não ser às cegas.

    python migrations/fin_cobranca_03_email_para.py            # dry-run
    python migrations/fin_cobranca_03_email_para.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

TABELA = 'fin_boletos'
ALTERS = [
    ('email_enviado_para', "ADD COLUMN email_enviado_para VARCHAR(255) NULL COMMENT 'destinatarios do ultimo e-mail de cobranca' AFTER email_enviado_em"),
]


def coluna_existe(coluna):
    return bool(execute_query(
        'SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s '
        'AND COLUMN_NAME = %s', (TABELA, coluna), fetch=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem ele só lista')
    args = ap.parse_args()
    pend = [(c, ddl) for c, ddl in ALTERS if not coluna_existe(c)]
    for c, _ in ALTERS:
        print(f'{TABELA}.{c}:', 'criar' if any(c == p for p, _ in pend) else 'já existe')
    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0
    for c, ddl in pend:
        if execute_query(f'ALTER TABLE {TABELA} {ddl}') is None:
            print(f'FALHOU: coluna {c} não criada.')
            return 1
        assert coluna_existe(c)
        print(f'OK: {c} criada.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
