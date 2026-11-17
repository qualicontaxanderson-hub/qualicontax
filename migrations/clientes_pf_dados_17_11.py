# -*- coding: utf-8 -*-
"""Dados pessoais da pessoa física, completos (17/11/2026).

Anderson: "cadastrando uma pessoa física e veja, está pobre demais os dados pessoais... eu coloco solteiro e
abre o regime de casamento... me traga coisas melhores".

Colunas novas em ``clientes_pf_dados`` (tudo opcional):
  sexo, naturalidade_cidade, naturalidade_uf, nome_pai, nome_social  -> Junta, contrato social, gov.br
  pis_nit, grau_instrucao, raca_cor                                   -> eSocial do sócio com pró-labore
  ecac_procuracao_ate                                                 -> procuração eletrônica no e-CAC
E em ``clientes_pf_documentos``: ``validade`` (CNH, passaporte, RNM) para avisar quando vence.

Só acrescenta colunas; não altera nem apaga nada. O init_db faz o mesmo no boot (idempotente).

    python migrations/clientes_pf_dados_17_11.py            # mostra o que faria
    python migrations/clientes_pf_dados_17_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

COLUNAS_PF = [
    ('sexo', "CHAR(1) NULL COMMENT 'M/F'"),
    ('naturalidade_cidade', 'VARCHAR(80) NULL'),
    ('naturalidade_uf', 'CHAR(2) NULL'),
    ('nome_pai', 'VARCHAR(255) NULL'),
    ('nome_social', 'VARCHAR(255) NULL'),
    ('pis_nit', "VARCHAR(14) NULL COMMENT 'PIS/PASEP/NIT (eSocial)'"),
    ('grau_instrucao', "VARCHAR(2) NULL COMMENT 'tabela 1 do eSocial (01-12)'"),
    ('raca_cor', "CHAR(1) NULL COMMENT 'tabela do eSocial (1-6)'"),
    ('ecac_procuracao_ate', "DATE NULL COMMENT 'procuracao eletronica no e-CAC vale ate'"),
]
COLUNAS_DOC = [
    ('validade', "DATE NULL COMMENT 'CNH, passaporte, RNM: valido ate'"),
]


def _colunas(tabela):
    return {r['Field'] for r in execute_query(f'SHOW COLUMNS FROM {tabela}', fetch=True) or []}


def aplicar(apply):
    for tabela, cols in (('clientes_pf_dados', COLUNAS_PF), ('clientes_pf_documentos', COLUNAS_DOC)):
        tem = _colunas(tabela)
        faltam = [(c, t) for c, t in cols if c not in tem]
        print(tabela, 'faltam:', [c for c, _ in faltam] or 'nada')
        if apply:
            for c, t in faltam:                 # um ALTER por coluna: se um falhar, os outros ficam
                execute_query(f'ALTER TABLE {tabela} ADD COLUMN {c} {t}', fetch=False)
                print('  criada', c)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    aplicar(ap.parse_args().apply)
