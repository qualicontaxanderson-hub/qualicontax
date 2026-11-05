# -*- coding: utf-8 -*-
"""Migração de cadastros (Acessórias + Conexa) — parte 1: colunas novas em ``clientes`` (04/11/2026).

Só o que o Anderson pediu (04/11/2026, "precisa mesmo criar essas colunas?"): NIRE (e a data de registro,
que virá da consulta), o vínculo com o Conexa (id), "emite NF" (SIM = nota marcada; NÃO = só boleto) e o
jeito de cobrar (Própria, Via grupo com a empresa pagadora, Pendente). Dia de vencimento e meio de
faturamento ficam nas mensalidades (parte 6); tags vão para Observações; a data do cadastro no
Acessórias entra em data_inicio_contrato quando vazio; CPFs/CNPJs extras vão para
cadastros_adicionais_clientes.

Inscrição estadual pode haver MAIS DE UMA (há empresa com nove): tabela própria
``clientes_inscricoes_estaduais``; a coluna ``clientes.inscricao_estadual`` continua sendo a principal.

    python migrations/migracao_01_clientes_colunas.py            # dry-run
    python migrations/migracao_01_clientes_colunas.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

TABELA = 'clientes'
ALTERS = [
    ('nire', "ADD COLUMN nire VARCHAR(20) NULL COMMENT 'NIRE (Junta Comercial)' AFTER inscricao_municipal"),
    ('nire_data_registro', "ADD COLUMN nire_data_registro DATE NULL COMMENT 'data de registro do NIRE' AFTER nire"),
    ('conexa_id', "ADD COLUMN conexa_id INT NULL COMMENT 'id do cliente no Conexa (migracao 11/2026)' AFTER nire_data_registro"),
    ('emite_nf', "ADD COLUMN emite_nf TINYINT(1) NULL COMMENT '1 = emite NFS-e na cobranca; 0 = so boleto; NULL = nao definido' AFTER conexa_id"),
    ('cobranca', "ADD COLUMN cobranca VARCHAR(10) NULL COMMENT 'PROPRIA | VIA_GRUPO | PENDENTE' AFTER emite_nf"),
    ('cobranca_pagador_id', "ADD COLUMN cobranca_pagador_id INT NULL COMMENT 'empresa que paga quando cobranca = VIA_GRUPO' AFTER cobranca"),
]
INDICES = [
    ('uk_clientes_conexa_id', 'ALTER TABLE clientes ADD UNIQUE INDEX uk_clientes_conexa_id (conexa_id)'),
]
CREATE_IE = """
CREATE TABLE IF NOT EXISTS clientes_inscricoes_estaduais (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT NOT NULL,
    uf CHAR(2) NULL,
    ie VARCHAR(20) NOT NULL,
    isenta TINYINT(1) NOT NULL DEFAULT 0,
    principal TINYINT(1) NOT NULL DEFAULT 0,
    origem VARCHAR(20) NULL COMMENT 'acessorias | app | receita',
    criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_cliente_uf_ie (cliente_id, uf, ie),
    KEY ix_ie_cliente (cliente_id),
    CONSTRAINT fk_ie_cliente FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def coluna_existe(coluna):
    return bool(execute_query(
        'SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s '
        'AND COLUMN_NAME = %s', (TABELA, coluna), fetch=True))


def indice_existe(nome):
    return bool(execute_query(
        'SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s '
        'AND INDEX_NAME = %s', (TABELA, nome), fetch=True))


def tabela_existe(nome):
    return bool(execute_query(
        'SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', (nome,), fetch=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem ele só lista')
    args = ap.parse_args()
    pend_cols = [(c, ddl) for c, ddl in ALTERS if not coluna_existe(c)]
    for c, _ in ALTERS:
        print(f'{TABELA}.{c}:', 'criar' if any(c == p for p, _ in pend_cols) else 'já existe')
    pend_idx = [(n, ddl) for n, ddl in INDICES if not indice_existe(n)]
    for n, _ in INDICES:
        print(f'índice {n}:', 'criar' if any(n == p for p, _ in pend_idx) else 'já existe')
    print('tabela clientes_inscricoes_estaduais:', 'já existe' if tabela_existe('clientes_inscricoes_estaduais') else 'criar')
    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0
    for c, ddl in pend_cols:
        if execute_query(f'ALTER TABLE {TABELA} {ddl}') is None:
            print(f'FALHOU: coluna {c} não criada.')
            return 1
        assert coluna_existe(c)
        print(f'OK: {c} criada.')
    for n, ddl in pend_idx:
        if execute_query(ddl) is None:
            print(f'FALHOU: índice {n}.')
            return 1
        print(f'OK: índice {n}.')
    if not tabela_existe('clientes_inscricoes_estaduais'):
        if execute_query(CREATE_IE) is None:
            print('FALHOU: tabela clientes_inscricoes_estaduais.')
            return 1
        print('OK: tabela clientes_inscricoes_estaduais criada.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
