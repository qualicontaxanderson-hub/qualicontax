# -*- coding: utf-8 -*-
"""Regime tributário pode ficar EM BRANCO (05/11/2026).

Decisão do Anderson: "Essa parte de Regime deixa pendência para o pessoal resolver, porque se
preenchermos o erro é do Simples." Até aqui a coluna era NOT NULL e o modelo gravava SIMPLES quando
ninguém escolhia — o cadastro saía "do Simples" sem ninguém ter decidido. Agora: a coluna aceita NULL,
o modelo não inventa regime para PJ, e o PJ sem regime aparece em A CADASTRAR (pendência "Regime").

    python migrations/migracao_02_regime_nulo.py            # dry-run
    python migrations/migracao_02_regime_nulo.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

ALTER = ("ALTER TABLE clientes MODIFY COLUMN regime_tributario "
         "ENUM('SIMPLES','LUCRO_PRESUMIDO','LUCRO_REAL','MEI') NULL DEFAULT NULL "
         "COMMENT 'NULL = ainda nao definido (pendencia em A CADASTRAR)'")


def estado():
    return execute_query(
        "SELECT IS_NULLABLE, COLUMN_TYPE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clientes' AND COLUMN_NAME = 'regime_tributario'",
        fetch=True, fetch_one=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    e = estado()
    print('antes:', e)
    if e and e['IS_NULLABLE'] == 'YES':
        print('já aceita NULL — nada a fazer')
        return
    if not a.apply:
        print('DRY-RUN, faria:', ALTER)
        return
    execute_query(ALTER, fetch=False)
    print('depois:', estado())


if __name__ == '__main__':
    main()
