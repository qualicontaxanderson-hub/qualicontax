# -*- coding: utf-8 -*-
"""Número "sem número" escrito de um jeito só: S/N (10/11/2026).

Anderson (10/11/2026): "Troca para S/N" — os endereços antigos com SN, S N, SNR, S/NR... passam a S/N,
a mesma regra que o servidor aplica desde 99bae60 (models/endereco_cliente._numero).

    python scripts/endereco_sn_10_11.py            # lista o que mudaria
    python scripts/endereco_sn_10_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from models.endereco_cliente import _numero, SEM_NUMERO   # noqa: E402

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    rows = execute_query("SELECT id, cliente_id, numero FROM enderecos_clientes WHERE numero IS NOT NULL", fetch=True) or []
    mudar = [r for r in rows if _numero(r['numero']) == SEM_NUMERO and r['numero'] != SEM_NUMERO]
    for r in mudar:
        print('endereco %s (cliente %s): %r -> %r' % (r['id'], r['cliente_id'], r['numero'], SEM_NUMERO))
    print('total a trocar:', len(mudar))
    if a.apply and mudar:
        for r in mudar:
            execute_query("UPDATE enderecos_clientes SET numero = %s WHERE id = %s AND numero = %s",
                          (SEM_NUMERO, r['id'], r['numero']), fetch=False)
        sobra = [r for r in (execute_query("SELECT numero FROM enderecos_clientes WHERE numero IS NOT NULL", fetch=True) or [])
                 if _numero(r['numero']) == SEM_NUMERO and r['numero'] != SEM_NUMERO]
        print('trocados; sobrou fora do padrão:', len(sobra))
