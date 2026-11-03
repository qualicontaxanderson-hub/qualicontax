# -*- coding: utf-8 -*-
"""Cobrança — e-mail de cobrança por conta (03/11/2026).

O boleto e a nota vão ao cliente pelo NOSSO app, e saem da caixa do Financeiro
(financeiro@qualicontax.com.br, já usada pela equipe), não da app@ que manda os
relatórios. Como a caixa é da empresa que cobra, ela fica na conta de cobrança:
endereço, nome do remetente e senha CIFRADA no banco (mesma chave mestra das
credenciais da Efí) — nada vai para o Railway nem passa pelo chat.

``avisa_por`` decide quem avisa o cliente do boleto: ``app`` (nosso e-mail),
``efi`` (a Efí manda o dela, para isso o boleto leva o e-mail do pagador) ou
``ninguem``.

    python migrations/fin_cobranca_02_email.py            # dry-run: só lista
    python migrations/fin_cobranca_02_email.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

TABELA = 'fin_cobranca_contas'
ALTERS = [
    ('email_remetente', "ADD COLUMN email_remetente VARCHAR(120) NULL COMMENT 'caixa que manda boleto e nota: financeiro@...'"),
    ('email_nome', "ADD COLUMN email_nome VARCHAR(60) NULL COMMENT 'nome que aparece no De: (Qualicontax Financeiro)'"),
    ('email_senha_cif', "ADD COLUMN email_senha_cif BLOB NULL COMMENT 'senha da caixa, Fernet(DFE_CRYPTO_KEY)'"),
    ('email_host', "ADD COLUMN email_host VARCHAR(80) NOT NULL DEFAULT 'smtp.titan.email'"),
    ('email_porta', "ADD COLUMN email_porta SMALLINT NOT NULL DEFAULT 465 COMMENT '465 SSL; 587 STARTTLS'"),
    ('email_testado_em', 'ADD COLUMN email_testado_em DATETIME NULL'),
    ('email_teste_resultado', 'ADD COLUMN email_teste_resultado VARCHAR(255) NULL'),
    ('avisa_por', "ADD COLUMN avisa_por VARCHAR(8) NOT NULL DEFAULT 'app' COMMENT 'quem avisa o cliente do boleto: app | efi | ninguem'"),
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
    print(f'OK: {len(pend)} coluna(s) criada(s).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
