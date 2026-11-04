# -*- coding: utf-8 -*-
"""Cobrança — o cliente abriu? (04/11/2026).

Pergunta do Anderson depois do primeiro e-mail real: "quando o cliente ler o e-mail ou baixar
conseguimos ter a confirmação?". O que dá para saber com certeza é o CLIQUE: os botões do
e-mail passam por um link nosso (``/c/<código>/boleto`` e ``/c/<código>/nota``) que anota a hora
e segue para a Efí ou entrega o PDF da nota. A abertura do e-mail vem de uma imagem de 1 px
(``/c/<código>/pixel.png``) — é indício, não prova (Apple Mail carrega sozinho; Outlook bloqueia).

Colunas novas em ``fin_boletos``:
  * ``email_token``      — código aleatório do boleto, único; só quem tem o e-mail tem o código;
  * ``email_visto_em``   — primeira vez que o pixel carregou;
  * ``abriu_boleto_em``  — primeira vez que clicou em "Abrir o boleto";
  * ``abriu_nota_em``    — primeira vez que clicou em "Ver a nota fiscal".

    python migrations/fin_cobranca_04_rastreio.py            # dry-run
    python migrations/fin_cobranca_04_rastreio.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

TABELA = 'fin_boletos'
ALTERS = [
    ('email_token', "ADD COLUMN email_token VARCHAR(48) NULL COMMENT 'codigo dos links do e-mail de cobranca' AFTER email_enviado_para"),
    ('email_visto_em', "ADD COLUMN email_visto_em DATETIME NULL COMMENT 'pixel do e-mail carregou (indicio)' AFTER email_token"),
    ('abriu_boleto_em', "ADD COLUMN abriu_boleto_em DATETIME NULL COMMENT 'clicou em Abrir o boleto' AFTER email_visto_em"),
    ('abriu_nota_em', "ADD COLUMN abriu_nota_em DATETIME NULL COMMENT 'clicou em Ver a nota fiscal' AFTER abriu_boleto_em"),
]
INDICE = ('uk_fin_boletos_email_token', 'ADD UNIQUE INDEX uk_fin_boletos_email_token (email_token)')


def coluna_existe(coluna):
    return bool(execute_query(
        'SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s '
        'AND COLUMN_NAME = %s', (TABELA, coluna), fetch=True))


def indice_existe(nome):
    return bool(execute_query(
        'SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s '
        'AND INDEX_NAME = %s', (TABELA, nome), fetch=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem ele só lista')
    args = ap.parse_args()
    pend = [(c, ddl) for c, ddl in ALTERS if not coluna_existe(c)]
    for c, _ in ALTERS:
        print(f'{TABELA}.{c}:', 'criar' if any(c == p for p, _ in pend) else 'já existe')
    idx_pend = not indice_existe(INDICE[0])
    print(f'{TABELA}.{INDICE[0]}:', 'criar' if idx_pend else 'já existe')
    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0
    for c, ddl in pend:
        if execute_query(f'ALTER TABLE {TABELA} {ddl}') is None:
            print(f'FALHOU: coluna {c} não criada.')
            return 1
        assert coluna_existe(c)
        print(f'OK: {c} criada.')
    if idx_pend:
        if execute_query(f'ALTER TABLE {TABELA} {INDICE[1]}') is None:
            print(f'FALHOU: índice {INDICE[0]} não criado.')
            return 1
        print(f'OK: {INDICE[0]} criado.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
