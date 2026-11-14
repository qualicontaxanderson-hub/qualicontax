# -*- coding: utf-8 -*-
"""Conta de cobrança SÓ PARA CONSULTA (14/11/2026).

Anderson (14/11/2026): "se der para migrar tudo pra nós eu prefiro, exemplo se eu precisar baixar um
boleto ou alterar eu ter a opção de fazer tudo aqui".

Os boletos do Conexa foram emitidos pela aplicação "Conexa" da nossa conta Efí; só as credenciais dela
os enxergam. Ela entra como uma conta de cobrança a mais (avisos, consulta, cancelar), marcada
``so_consulta = 1``: nunca é oferecida para emitir boleto novo nem vira a conta padrão da empresa.

    python migrations/fin_cobranca_contas_so_consulta_14_11.py            # mostra o que faria
    python migrations/fin_cobranca_contas_so_consulta_14_11.py --apply
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
    falta = 'so_consulta' not in tem
    print('falta so_consulta' if falta else 'já existe')
    if a.apply and falta:
        execute_query('ALTER TABLE fin_cobranca_contas ADD COLUMN so_consulta TINYINT(1) NOT NULL DEFAULT 0',
                      fetch=False)
        print('criada so_consulta')
