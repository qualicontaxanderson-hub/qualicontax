# -*- coding: utf-8 -*-
"""Multiempresa no financeiro (E2.1, 20/08/2026) — a fundação do E2.

O financeiro deixa de ser só da Qualicontax: Brilho Transportes, HPA
Transportes e a pessoa física do Anderson entram (e a tela "Minhas Empresas"
deixa marcar outras, ex.: Albert PF). Separação por empresa em TUDO, e
consolidado que junta todas OU algumas.

* ``fin_empresas`` — quem participa. ``cliente_id`` aponta para o cadastro de
  clientes (as empresas JÁ existem lá; nada duplicado). ``apelido`` é o nome
  curto dos chips; ``no_consolidado`` diz se entra na soma por padrão.
* ``empresa_id`` (= clientes.id) entra em fin_titulos, fin_saldos e
  fin_contratos. A extrato_lancamentos já tinha a coluna — o significado
  fica DEFINITIVO: empresa dona do dado. NULL antigo (= "escritório") vira
  Qualicontax (id 2) — hoje as tabelas estão vazias, o UPDATE é por higiene.

Feito AGORA porque as fin_* estão zeradas: amanhã seria migração de dados.

    python migrations/fin_multiempresa.py            # dry-run
    python migrations/fin_multiempresa.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

QUALICONTAX_ID = 2

DDL_EMPRESAS = """
CREATE TABLE IF NOT EXISTS fin_empresas (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  cliente_id     INT NOT NULL,
  apelido        VARCHAR(40) NOT NULL,
  ordem          INT DEFAULT 0,
  no_consolidado TINYINT(1) NOT NULL DEFAULT 1,
  ativo          TINYINT(1) NOT NULL DEFAULT 1,
  criado_em      DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_cliente (cliente_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# (cliente_id, apelido, ordem) — conferidos no cadastro em 20/08/2026.
SEMENTE = [
    (2,  'QUALICONTAX', 10),
    (26, 'BRILHO',      20),
    (29, 'HPA',         30),
    (1,  'ANDERSON PF', 40),
]

ALTERS = [
    ('fin_titulos', 'empresa_id',
     'ALTER TABLE fin_titulos ADD COLUMN empresa_id INT NULL AFTER id, '
     'ADD INDEX idx_empresa (empresa_id, tipo, status)'),
    ('fin_saldos', 'empresa_id',
     'ALTER TABLE fin_saldos ADD COLUMN empresa_id INT NULL AFTER id, '
     'ADD INDEX idx_empresa (empresa_id)'),
    ('fin_contratos', 'empresa_id',
     'ALTER TABLE fin_contratos ADD COLUMN empresa_id INT NULL AFTER id'),
]

BACKFILLS = [
    f"UPDATE fin_titulos SET empresa_id = {QUALICONTAX_ID} WHERE empresa_id IS NULL",
    f"UPDATE fin_saldos SET empresa_id = {QUALICONTAX_ID} WHERE empresa_id IS NULL",
    f"UPDATE fin_contratos SET empresa_id = {QUALICONTAX_ID} WHERE empresa_id IS NULL",
    f"UPDATE extrato_lancamentos SET empresa_id = {QUALICONTAX_ID} WHERE empresa_id IS NULL",
]


def coluna_existe(tabela, coluna):
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
              AND COLUMN_NAME = %s""",
        (tabela, coluna), fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def tabela_existe(nome):
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s""",
        (nome,), fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    pend_tab = not tabela_existe('fin_empresas')
    pend_cols = [(t, c, ddl) for t, c, ddl in ALTERS if not coluna_existe(t, c)]
    print('fin_empresas:', 'criar' if pend_tab else 'já existe')
    for t, c, _ in pend_cols:
        print(f'{t}.{c}: criar')
    if not pend_tab and not pend_cols:
        print('Nada estrutural a fazer.')

    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0

    if pend_tab:
        execute_query(DDL_EMPRESAS)
        assert tabela_existe('fin_empresas')
        print('OK: fin_empresas criada.')
    for t, c, ddl in pend_cols:
        execute_query(ddl)
        assert coluna_existe(t, c), f'{t}.{c} não apareceu'
        print(f'OK: {t}.{c}')

    n = (execute_query('SELECT COUNT(*) AS n FROM fin_empresas',
                       fetch=True, fetch_one=True) or {}).get('n', 0)
    if n == 0:
        for cid, apelido, ordem in SEMENTE:
            cli = execute_query('SELECT id FROM clientes WHERE id = %s',
                                (cid,), fetch=True, fetch_one=True)
            if not cli:
                print(f'! cliente {cid} ({apelido}) não existe — pulado')
                continue
            execute_query(
                'INSERT INTO fin_empresas (cliente_id, apelido, ordem) '
                'VALUES (%s, %s, %s)', (cid, apelido, ordem))
        print(f'OK: semente com {len(SEMENTE)} empresas.')
    else:
        print(f'fin_empresas já tem {n} linhas — semente pulada.')

    for sql in BACKFILLS:
        execute_query(sql)
    print('OK: backfill de empresa_id (linhas órfãs viram Qualicontax).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
