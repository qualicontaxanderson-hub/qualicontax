# -*- coding: utf-8 -*-
"""Conta de cobrança do Bradesco (14/11/2026).

O que é só do Bradesco (carteira, agência e conta da negociação, telefone impresso no boleto) vai numa
coluna JSON ``config_json`` — a Efí não usa. As credenciais seguem nas colunas cifradas de sempre
(client_id_cif, client_secret_cif, certificado_cif = .pfx do mTLS).

    python migrations/fin_cobranca_bradesco_14_11.py            # mostra o que faria
    python migrations/fin_cobranca_bradesco_14_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    tem = {r['Field'] for r in execute_query('SHOW COLUMNS FROM fin_cobranca_contas', fetch=True)}
    falta = 'config_json' not in tem
    print('falta config_json' if falta else 'já existe')
    if a.apply and falta:
        execute_query('ALTER TABLE fin_cobranca_contas ADD COLUMN config_json JSON NULL', fetch=False)
        print('criada config_json')
