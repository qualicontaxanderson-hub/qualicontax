# -*- coding: utf-8 -*-
"""Cria fin_saldos — a âncora REAL do fluxo de caixa (Documento E, fase 9).

O fluxo de caixa do documento parte do "saldo hoje (extrato, real)". O extrato
automático é a fase 4 (OFX/Excel/PDF e depois Pluggy); até lá o saldo é
INFORMADO À MÃO pelo Anderson, e a tela diz de quando ele é. Cada informe é
uma LINHA NOVA (histórico completo, com quem informou); o vigente é o último.
Quando a fase 4 chegar, o extrato passa a gravar aqui com origem='extrato' —
mesma tabela, mesma leitura, sem retrabalho.

    python migrations/add_fin_saldos.py            # dry-run
    python migrations/add_fin_saldos.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS fin_saldos (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  data        DATE NOT NULL,
  valor       DECIMAL(15,2) NOT NULL,
  origem      VARCHAR(10) NOT NULL DEFAULT 'manual',
  usuario_id  INT NULL,
  criado_em   DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_data (data)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def existe() -> bool:
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'fin_saldos'""",
        fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    if existe():
        print('fin_saldos já existe — nada a fazer.')
        return 0
    if not args.apply:
        print('[dry-run] criaria fin_saldos. Rode com --apply.')
        return 0
    execute_query(DDL)
    assert existe()
    print('OK: fin_saldos criada.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
