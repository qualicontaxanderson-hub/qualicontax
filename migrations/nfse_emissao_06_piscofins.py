# -*- coding: utf-8 -*-
"""Emissor de NFS-e — PIS/COFINS PRÓPRIO na matriz fiscal (06/11/2026).

Fora do Simples a nota leva piscofins{CST, base, alíquotas, valores, tpRetPisCofins} mesmo sem
retenção (notas reais de Presumido/Real em Goiás). CST e alíquotas são da empresa: moram na matriz.

(Molde: nfse_emissao_05_liberacao.)


SÓ ACRESCENTA três colunas; nada que o Financeiro usa é alterado.

    python migrations/nfse_emissao_06_piscofins.py            # dry-run: só mostra
    python migrations/nfse_emissao_06_piscofins.py --apply    # executa

Rollback:
    ALTER TABLE nfse_emissao_config DROP COLUMN aliquota_cofins, DROP COLUMN aliquota_pis, DROP COLUMN piscofins_cst;
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

COLUNAS = (
    ('piscofins_cst', 'ALTER TABLE nfse_emissao_config ADD COLUMN piscofins_cst VARCHAR(2) NULL'),
    ('aliquota_pis', 'ALTER TABLE nfse_emissao_config ADD COLUMN aliquota_pis DECIMAL(7,4) NULL'),
    ('aliquota_cofins', 'ALTER TABLE nfse_emissao_config ADD COLUMN aliquota_cofins DECIMAL(7,4) NULL'),
)


def existentes():
    rows = execute_query(
        "SELECT COLUMN_NAME c FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'nfse_emissao_config'", fetch=True) or []
    return {r['c'] for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem ele só mostra')
    args = ap.parse_args()
    tem = existentes()
    if not tem:
        print('FALHOU: tabela nfse_emissao_config não encontrada.')
        return 1
    faltam = [(c, ddl) for c, ddl in COLUNAS if c not in tem]
    for c, ddl in COLUNAS:
        print(f'  {c}: {"já existe" if c in tem else "a criar -> " + ddl}')
    if not faltam:
        print('Nada a fazer.')
        return 0
    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0
    for c, ddl in faltam:                       # um ALTER por coluna (lição do idx_operacao)
        if execute_query(ddl) is None:
            print(f'FALHOU: {c} não criada.')
            return 1
        print(f'OK: {c} criada.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
