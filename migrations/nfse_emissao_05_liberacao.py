# -*- coding: utf-8 -*-
"""Emissor de NFS-e — marca "Conferido e liberado para emitir" na matriz fiscal (06/11/2026).

A matriz fiscal da empresa é UMA só (``nfse_emissao_config``, uma linha por clientes.id):
o Financeiro e o Fiscal leem e gravam a mesma linha. O Fiscal configura a empresa do
cliente e, quando nada falta, marca que conferiu — sem essa marca, a empresa do cliente
não emite. Mudar a configuração depois derruba a marca (``NfseConfig.salvar``).

SÓ ACRESCENTA duas colunas; nada que o Financeiro usa é alterado.

    python migrations/nfse_emissao_05_liberacao.py            # dry-run: só mostra
    python migrations/nfse_emissao_05_liberacao.py --apply    # executa

Rollback:
    ALTER TABLE nfse_emissao_config DROP COLUMN liberado_por, DROP COLUMN liberado_em;
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

COLUNAS = (
    ('liberado_em', 'ALTER TABLE nfse_emissao_config ADD COLUMN liberado_em DATETIME NULL'),
    ('liberado_por', 'ALTER TABLE nfse_emissao_config ADD COLUMN liberado_por INT NULL'),
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
