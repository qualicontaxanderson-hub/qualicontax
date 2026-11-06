# -*- coding: utf-8 -*-
"""Parte 6, etapa 4b — comissão sobre honorários e indicação (06/11/2026).

Quem tem acordo de HONORARIOS numa empresa (ex.: Guilherme 25%) ou de INDICACAO (prêmio à vista ou
em 2x) ganha a comissão quando a cobrança mensal daquela empresa é paga. Para andar na MESMA esteira
dos avulsos (aguardando → liberada → fechamento → pagamento), o lote grava uma linha em
``fin_lancamentos`` por competência, com ``origem`` HONORARIOS ou INDICACAO, já FATURADA e ligada ao
título do lote. A tela de Lançamentos lista só origem SERVICO; a de comissões lista tudo.

    python migrations/migracao_10_lancamentos_origem.py --apply
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
    if execute_query("SHOW COLUMNS FROM fin_lancamentos LIKE 'origem'", fetch=True):
        print('fin_lancamentos.origem já existe.')
        return
    if not a.apply:
        print('DRY-RUN: criaria fin_lancamentos.origem. Rode com --apply.')
        return
    execute_query("ALTER TABLE fin_lancamentos ADD COLUMN origem VARCHAR(10) NOT NULL DEFAULT 'SERVICO' "
                  "COMMENT 'SERVICO (lancado por alguem) | HONORARIOS | INDICACAO (comissao gerada pelo lote)' AFTER destino, "
                  "ADD KEY idx_fl_origem (origem, comissao_acordo_id, competencia)", fetch=False)
    print('coluna criada.')


if __name__ == '__main__':
    main()
