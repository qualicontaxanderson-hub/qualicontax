# -*- coding: utf-8 -*-
"""Cria fin_conta_saldos — o saldo do banco de cada conta, gravado a cada extrato (21/11/2026).

Anderson: "preciso acompanhar os saldos das contas". O OFX traz o saldo (LEDGERBAL) e o PDF do Bradesco a coluna
Saldo; até aqui o leitor jogava fora. Um registro por conta e dia (o mais novo do dia vale). Ver utils/conta_saldo.py.

    python migrations/add_fin_conta_saldos.py            # dry-run
    python migrations/add_fin_conta_saldos.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS fin_conta_saldos (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  conta_id       INT NOT NULL,
  data           DATE NOT NULL,
  valor          DECIMAL(15,2) NOT NULL,
  origem         VARCHAR(12) NOT NULL,
  arquivo        VARCHAR(255) NULL,
  criado_em      DATETIME DEFAULT CURRENT_TIMESTAMP,
  atualizado_em  DATETIME NULL,
  UNIQUE KEY uq_conta_data (conta_id, data),
  INDEX idx_conta (conta_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def existe() -> bool:
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'fin_conta_saldos'""",
        fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    if existe():
        print('fin_conta_saldos já existe — nada a fazer.')
        return 0
    if not args.apply:
        print('[dry-run] criaria fin_conta_saldos. Rode com --apply.')
        return 0
    execute_query(DDL)
    assert existe()
    print('OK: fin_conta_saldos criada.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
