# -*- coding: utf-8 -*-
"""Classificação + memorização do extrato (E2.4, 20/08/2026).

O pedido do Anderson, ao pé da letra: "quando vir no extrato já memorizar e
quando vir novamente NEM PRECISA ter trabalho de encontrar". Classificou um
lançamento do banco (categoria + centro de custo) e marcou "memorizar" →
vira um padrão; TODO lançamento futuro (e os antigos ainda sem categoria)
cuja descrição contém o padrão chega classificado sozinho. Classificar SEM
memorizar continua existindo — é o lançamento único.

* ``extrato_lancamentos`` ganha categoria_id, centro_custo_id e
  memorizacao_id (quem classificou: NULL+categoria = foi à mão).
* ``fin_extrato_memorizacoes``: o padrão é um TRECHO da descrição (contém,
  sem caixa); empresa_id NULL = vale para todas as minhas empresas. Quando
  mais de um padrão casa, vence o MAIS LONGO (mais específico).

Mesmo espírito das memorizações do fiscal: o sistema aprende com a primeira
decisão humana e repete sozinho.

    python migrations/criar_extrato_memorizacoes.py            # dry-run
    python migrations/criar_extrato_memorizacoes.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS fin_extrato_memorizacoes (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id       INT NULL,
  padrao           VARCHAR(160) NOT NULL,
  categoria_id     INT NOT NULL,
  centro_custo_id  INT NULL,
  ativo            TINYINT(1) NOT NULL DEFAULT 1,
  usos             INT NOT NULL DEFAULT 0,
  ultimo_uso       DATETIME NULL,
  criado_por       INT NULL,
  criado_em        DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_ativa (ativo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

COLS = [
    ('categoria_id', 'ALTER TABLE extrato_lancamentos ADD COLUMN categoria_id INT NULL '
                     'AFTER descricao, ADD INDEX idx_cat (categoria_id)'),
    ('centro_custo_id', 'ALTER TABLE extrato_lancamentos ADD COLUMN centro_custo_id INT NULL '
                        'AFTER categoria_id'),
    ('memorizacao_id', 'ALTER TABLE extrato_lancamentos ADD COLUMN memorizacao_id INT NULL '
                       'AFTER centro_custo_id'),
]


def tabela_existe(nome):
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s""",
        (nome,), fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def coluna_existe(coluna):
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'extrato_lancamentos' AND COLUMN_NAME = %s""",
        (coluna,), fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    pend = []
    if not tabela_existe('fin_extrato_memorizacoes'):
        pend.append('criar fin_extrato_memorizacoes')
    pend += [f'extrato_lancamentos.{c}' for c, _ in COLS if not coluna_existe(c)]
    print('Pendências:', '; '.join(pend) if pend else 'nenhuma')
    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0

    if not tabela_existe('fin_extrato_memorizacoes'):
        execute_query(DDL)
        assert tabela_existe('fin_extrato_memorizacoes')
        print('OK: fin_extrato_memorizacoes criada.')
    for col, ddl in COLS:
        if not coluna_existe(col):
            execute_query(ddl)
            assert coluna_existe(col)
            print(f'OK: extrato_lancamentos.{col}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
