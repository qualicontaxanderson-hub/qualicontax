# -*- coding: utf-8 -*-
"""Índices de reajuste cadastrados no sistema (25/11/2026).

Anderson: "clico em índice próprio e coloco o índice, e já temos que ter um campo no sistema para colocar esses índices".
Tabela ``reajuste_indices``: sigla (IPCA, IGP-M…), nome por extenso, descrição curta, situação. Edita-se em
Financeiro › SERVIÇOS › Índices de reajuste; o contrato de prestação escolhe numa caixa (Índice próprio / Data própria)
e o texto do contrato sai como "pela variação acumulada do IPCA (Índice Nacional de Preços ao Consumidor Amplo)".

    python migrations/migracao_20_reajuste_indices.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

SEMENTE = [
    ('IPCA', 'Índice Nacional de Preços ao Consumidor Amplo', 'IBGE · a inflação oficial do país', 1),
    ('IGP-M', 'Índice Geral de Preços do Mercado', 'FGV · o mais usado em aluguéis e contratos', 2),
    ('INPC', 'Índice Nacional de Preços ao Consumidor', 'IBGE · famílias de 1 a 5 salários mínimos', 3),
    ('IPC-FIPE', 'Índice de Preços ao Consumidor da Fipe', 'Fipe · cidade de São Paulo', 4),
    ('IGP-DI', 'Índice Geral de Preços – Disponibilidade Interna', 'FGV · preços no atacado, varejo e construção', 5),
]


def _tem_tabela(t):
    r = execute_query("SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                      (t,), fetch=True, fetch_one=True) or {}
    return bool(r.get('n'))


def aplicar(so_ver=False):
    if _tem_tabela('reajuste_indices'):
        print('  reajuste_indices ja existe')
    else:
        print('  reajuste_indices %s' % ('criaria' if so_ver else 'criando'))
        if not so_ver:
            execute_query("""CREATE TABLE reajuste_indices (
                id INT AUTO_INCREMENT PRIMARY KEY,
                sigla VARCHAR(20) NOT NULL,
                nome VARCHAR(120) NOT NULL,
                descricao VARCHAR(160) NULL,
                situacao ENUM('ATIVO', 'INATIVO') NOT NULL DEFAULT 'ATIVO',
                ordem INT NOT NULL DEFAULT 0,
                criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_sigla (sigla)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
              COMMENT='indices de reajuste dos contratos de prestacao (Financeiro > Servicos > Indices de reajuste)'""", fetch=False)
    if so_ver:
        print('  semente: %d indices' % len(SEMENTE)); return
    for sigla, nome, desc, ordem in SEMENTE:
        ja = execute_query("SELECT id FROM reajuste_indices WHERE sigla = %s", (sigla,), fetch=True, fetch_one=True)
        if ja:
            continue
        execute_query("INSERT INTO reajuste_indices (sigla, nome, descricao, ordem) VALUES (%s, %s, %s, %s)", (sigla, nome, desc, ordem), fetch=False)
        print('  + %s' % sigla)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    aplicar(so_ver=not a.apply)


if __name__ == '__main__':
    main()
