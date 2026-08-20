# -*- coding: utf-8 -*-
"""Cria extrato_lancamentos — o extrato bancário (Documento E, fase 4).

TABELA COMPARTILHADA com o futuro A2 (extrato de CLIENTES), separada por
``empresa_id``: NULL = escritório (Qualicontax); cliente da carteira quando o
A2 chegar. O Documento E manda acertá-la AQUI primeiro para o A2 herdar pronta.
O que muda entre os dois é só o destino do lançamento: no escritório vai para
conciliação de título (fase 5); no cliente, para classificação contábil.

Idempotência do import (a mesma regra de ouro das baixas): ``hash_dedup`` é
UNIQUE. Com FITID (id que o banco dá no OFX), a chave é
empresa|banco|conta|fitid. Sem FITID, entra data|valor|descrição|documento|
número-da-repetição-no-dia — assim dois PIX idênticos no mesmo dia são DOIS
lançamentos, mas reimportar o mesmo arquivo não duplica nenhum.

``valor`` é ASSINADO: crédito positivo, débito negativo — como no banco.

    python migrations/criar_extrato_lancamentos.py            # dry-run
    python migrations/criar_extrato_lancamentos.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS extrato_lancamentos (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  empresa_id    INT NULL,
  banco         VARCHAR(60),
  conta         VARCHAR(60),
  data          DATE NOT NULL,
  valor         DECIMAL(15,2) NOT NULL,
  tipo          VARCHAR(10),
  descricao     VARCHAR(500),
  documento     VARCHAR(80),
  fitid         VARCHAR(120),
  hash_dedup    VARCHAR(64) NOT NULL,
  origem        VARCHAR(10) NOT NULL DEFAULT 'ofx',
  arquivo       VARCHAR(255),
  usuario_id    INT NULL,
  criado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_dedup (hash_dedup),
  INDEX idx_empresa_data (empresa_id, data),
  INDEX idx_fitid (fitid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def existe() -> bool:
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'extrato_lancamentos'""",
        fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    if existe():
        print('extrato_lancamentos já existe — nada a fazer.')
        return 0
    if not args.apply:
        print('[dry-run] criaria extrato_lancamentos. Rode com --apply.')
        return 0
    execute_query(DDL)
    assert existe()
    print('OK: extrato_lancamentos criada.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
