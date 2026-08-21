# -*- coding: utf-8 -*-
"""Fila de pendências do extrato — o arquivo que chegou e não se identificou.

Regra combinada com o Anderson em 21/08/2026:

* Conta conhecida → lança e arquiva, **não importa o nome do arquivo** ("pode
  vir coco de galinha no nome").
* Conta desconhecida COM número da empresa no nome (``148 - cora.ofx``) → vira
  pendência AMARRADA à empresa 148: quando alguém for trabalhar nela, a tela
  avisa "temos um OFX do Cora, complete agência e conta".
* Conta desconhecida SEM número → pendência ÓRFÃ, esperando alguém dizer de
  quem é.
* Respondida uma vez → a conta entra em ``fin_contas`` e os próximos meses
  entram sozinhos, com qualquer nome.

O arquivo NÃO sai da _ENTRADA enquanto a pendência existe — some da fila
quando for lançado de verdade.

    python migrations/criar_fin_extrato_pendencias.py            # dry-run
    python migrations/criar_fin_extrato_pendencias.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS fin_extrato_pendencias (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  arquivo       VARCHAR(255) NOT NULL,
  caminho       VARCHAR(500) NOT NULL,
  empresa_id    INT NULL,
  numero_no_nome VARCHAR(10) NULL,
  banco_id      VARCHAR(10),
  banco_nome    VARCHAR(60),
  agencia       VARCHAR(20),
  conta         VARCHAR(40),
  qtd_lancamentos INT DEFAULT 0,
  periodo       VARCHAR(40),
  motivo        VARCHAR(400),
  status        VARCHAR(15) NOT NULL DEFAULT 'aberta',
  visto_em      DATETIME DEFAULT CURRENT_TIMESTAMP,
  criado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_caminho (caminho),
  INDEX idx_status (status, empresa_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def existe():
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'fin_extrato_pendencias'""",
        fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    if existe():
        print('fin_extrato_pendencias já existe.')
        return 0
    if not args.apply:
        print('[dry-run] criaria fin_extrato_pendencias. Rode com --apply.')
        return 0
    execute_query(DDL)
    assert existe()
    print('OK: fin_extrato_pendencias criada.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
