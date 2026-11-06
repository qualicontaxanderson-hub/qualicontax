# -*- coding: utf-8 -*-
"""Número do contrato de prestação gerado pelo sistema (Anderson, 06/11/2026: "o sistema me dará aleatório com letras
e números"): QC-XXXXXX, seis caracteres sem letras ambíguas (sem 0/O, 1/I), único.

    python migrations/migracao_14_prest_numero.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    tem = execute_query("SHOW COLUMNS FROM prest_contratos LIKE 'numero'", fetch=True)
    print('prest_contratos.numero:', 'já existe' if tem else 'não existe')
    if not a.apply:
        print('dry-run. Use --apply.')
        return
    if not tem:
        execute_query("ALTER TABLE prest_contratos ADD COLUMN numero VARCHAR(12) NULL COMMENT 'QC-XXXXXX gerado pelo sistema' AFTER id, ADD UNIQUE KEY uq_pc_numero (numero)", fetch=False)
    from models.prest_contrato import PrestContrato
    for r in execute_query('SELECT id FROM prest_contratos WHERE numero IS NULL', fetch=True) or []:
        execute_query('UPDATE prest_contratos SET numero = %s WHERE id = %s', (PrestContrato.gerar_numero(), r['id']), fetch=False)
    print('ok')


if __name__ == '__main__':
    main()
