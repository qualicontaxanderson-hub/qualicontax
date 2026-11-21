# -*- coding: utf-8 -*-
"""Cria fin_extrato_contas_fora — contas que NÃO são do grupo (21/11/2026).

Anderson: "ficou uma conta que não é do nosso GRUPO que ficará aqui, por que ela aparece aqui?". Extrato de
cliente que cai na _ENTRADA virava cartão em "Contas novas esperando empresa". Com o botão "Não é do grupo"
a conta entra aqui: o arquivo vai para a pasta _FORA_FINANCEIRO e os próximos extratos dela não perguntam
mais (cron_extrato). Quando o Contábil começar a ler extrato de cliente, é daqui que ele parte.

    python migrations/add_fin_extrato_contas_fora.py            # dry-run
    python migrations/add_fin_extrato_contas_fora.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS fin_extrato_contas_fora (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  banco_id    VARCHAR(10) NULL,
  banco_nome  VARCHAR(80) NULL,
  conta       VARCHAR(40) NOT NULL,
  conta_norm  VARCHAR(40) NOT NULL,
  arquivo     VARCHAR(255) NULL,
  usuario_id  INT NULL,
  criado_em   DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_banco_conta (banco_id, conta_norm)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def existe() -> bool:
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'fin_extrato_contas_fora'""",
        fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    if existe():
        print('fin_extrato_contas_fora já existe — nada a fazer.')
        return 0
    if not args.apply:
        print('[dry-run] criaria fin_extrato_contas_fora. Rode com --apply.')
        return 0
    execute_query(DDL)
    assert existe()
    print('OK: fin_extrato_contas_fora criada.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
