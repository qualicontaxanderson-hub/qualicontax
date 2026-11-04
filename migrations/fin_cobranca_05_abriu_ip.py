# -*- coding: utf-8 -*-
"""Cobrança — de onde o cliente abriu (04/11/2026).

Pergunta do Anderson: "tem como gravar o IP que abriu?". Serve para separar o clique do cliente
da visita automática do servidor de e-mail (que carimba segundos depois do envio).

Colunas novas em ``fin_boletos``: ``abriu_ip`` (IP real, do X-Forwarded-For atrás do proxy do
Railway) e ``abriu_agente`` (navegador/programa) da ÚLTIMA abertura de qualquer botão ou do pixel.

    python migrations/fin_cobranca_05_abriu_ip.py            # dry-run
    python migrations/fin_cobranca_05_abriu_ip.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

TABELA = 'fin_boletos'
ALTERS = [
    ('abriu_ip', "ADD COLUMN abriu_ip VARCHAR(45) NULL COMMENT 'IP da ultima abertura pelo e-mail' AFTER abriu_nota_em"),
    ('abriu_agente', "ADD COLUMN abriu_agente VARCHAR(255) NULL COMMENT 'navegador da ultima abertura pelo e-mail' AFTER abriu_ip"),
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
