# -*- coding: utf-8 -*-
"""Emissor de NFS-e — endereço do webservice da prefeitura (ISSWeb/Fiorilli) na matriz (06/11/2026).

Municípios que não aceitam o Emissor Nacional e usam o ISSWeb da Fiorilli (Senador Canedo)
recebem a MESMA DPS nacional no webservice do município. A URL é da prefeitura: mora na matriz.

(Molde: nfse_emissao_05_liberacao.)


SÓ ACRESCENTA uma coluna; nada que o Financeiro usa é alterado.

    python migrations/nfse_emissao_07_ws_url.py            # dry-run: só mostra
    python migrations/nfse_emissao_07_ws_url.py --apply    # executa

Rollback:
    ALTER TABLE nfse_emissao_config DROP COLUMN ws_url;
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

COLUNAS = (
    ('ws_url', 'ALTER TABLE nfse_emissao_config ADD COLUMN ws_url VARCHAR(255) NULL'),
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
