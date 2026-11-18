# -*- coding: utf-8 -*-
"""Comissão adiantada e desconto (18/11/2026).

Anderson: "vamos poder pagar as comissões posteriores também, mas não sairá dos atrasados para ele ter
ciência e, no futuro, se não recebermos será descontado nas comissões dos meses seguintes". A pessoa
pode pedir a comissão de uma cobrança ATRASADA antes de o cliente pagar (``comissao_adiantada`` = 1); o
cartão continua em "Esperando o cliente pagar". Se a cobrança for dada como perdida (título cancelado),
nasce um lançamento de DESCONTO (comissão negativa, ``desconto_de`` = o adiantado) que entra no próximo
pedido e abate o total.

    python migrations/migracao_15_comissao_adiantada.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

COLUNAS = {
    'comissao_adiantada': "ADD COLUMN comissao_adiantada TINYINT(1) NOT NULL DEFAULT 0 "
                          "COMMENT '1 = pedida antes de o cliente pagar (cobranca atrasada)' AFTER comissao_status",
    'desconto_de': "ADD COLUMN desconto_de INT NULL COMMENT 'fin_lancamentos adiantado que este desconto abate' AFTER comissao_adiantada",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    faltam = [c for c in COLUNAS if not execute_query("SHOW COLUMNS FROM fin_lancamentos LIKE %s", (c,), fetch=True)]
    print('colunas a criar em fin_lancamentos:', faltam or 'nenhuma')
    if not faltam:
        return
    if not a.apply:
        print('DRY-RUN. Rode com --apply.')
        return
    execute_query('ALTER TABLE fin_lancamentos ' + ', '.join(COLUNAS[c] for c in faltam), fetch=False)
    print('pronto.')


if __name__ == '__main__':
    main()
