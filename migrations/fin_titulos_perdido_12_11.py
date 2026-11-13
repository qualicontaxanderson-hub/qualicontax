# -*- coding: utf-8 -*-
"""Título PERDIDO (12/11/2026).

Anderson (12/11/2026): "Para manter o registro, vamos fazer assim, ter os RECEBIDOS / A RECEBER / ATRASADOS /
INADIMPLENTES / PERDIDOS. Nesses perdidos colocaremos os que estão como EXCLUIR por que não vão pagar mais...
Só para manter o Registro, melhor".

Perdido é um título CANCELADO com motivo: o status continua 'cancelado' (sai do em aberto, do fluxo e do DRE
como qualquer cancelado, sem mexer no resto do Financeiro), e estas três colunas guardam quando, por quem e por
quê. A aba Perdidos lista os cancelados com perdido_em preenchido; "Voltar para em aberto" limpa as três.

    python migrations/fin_titulos_perdido_12_11.py            # mostra o que faria
    python migrations/fin_titulos_perdido_12_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

COLUNAS = [
    ('perdido_em', 'DATETIME NULL'),
    ('perdido_por', 'INT NULL'),
    ('perdido_motivo', 'VARCHAR(200) NULL'),
]

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    tem = {r['Field'] for r in execute_query('SHOW COLUMNS FROM fin_titulos', fetch=True)}
    faltam = [(c, t) for c, t in COLUNAS if c not in tem]
    print('faltam:', [c for c, _ in faltam] or 'nada')
    if a.apply and faltam:
        # uma coluna por ALTER: se uma falhar, as outras ficam e o script pode rodar de novo
        for c, t in faltam:
            execute_query(f'ALTER TABLE fin_titulos ADD COLUMN {c} {t}', fetch=False)
            print('criada', c)
