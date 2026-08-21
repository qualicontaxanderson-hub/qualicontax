# -*- coding: utf-8 -*-
"""Cria fin_contas — a conta bancária é a IMPRESSÃO DIGITAL da empresa.

Problema real do Anderson (21/08/2026): "a ameba pode colocar 100 no extrato
da empresa 1, e são tão burros que fazem toda a contabilidade da empresa 100
sem notar". Confiar no número que o funcionário digita no nome do arquivo é
confiar no elo mais fraco.

A solução é não perguntar nada a ele: **todo OFX traz o número da conta**
(conferido nos 5 bancos reais em 21/08/2026 — Bradesco 16865, C6 211346179,
Cora 17381650, EFí 545806-4, Sicredi 39500000000156390). Com as contas
cadastradas, o sistema lê a conta e sabe de quem é — sem chute e sem digitação.

E o número no nome do arquivo deixa de ser comando e vira CONFERÊNCIA: se o
arquivo diz "100" mas a conta é da empresa 1, o roteador RECUSA e grita a
contradição, em vez de obedecer. O erro morre na porta.

Conta desconhecida não vira palpite: fica parada até alguém dizer de quem é —
uma vez só.

    python migrations/criar_fin_contas.py            # dry-run
    python migrations/criar_fin_contas.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS fin_contas (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id    INT NOT NULL,
  banco_id      VARCHAR(10),
  banco_nome    VARCHAR(60),
  agencia       VARCHAR(20),
  conta         VARCHAR(40) NOT NULL,
  conta_norm    VARCHAR(40) NOT NULL,
  apelido       VARCHAR(60),
  ativo         TINYINT(1) NOT NULL DEFAULT 1,
  criado_por    INT NULL,
  criado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_conta (banco_id, conta_norm),
  INDEX idx_empresa (empresa_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# As 5 contas dos arquivos reais que o Anderson mandou, todas da QUALICONTAX
# (cliente_id 2) — exceto onde ele disser o contrário. Nascem cadastradas para
# a primeira rodada já acertar sem perguntar nada.
SEMENTE = [
    # (banco_id, banco_nome, agencia, conta, apelido)
    ('237', 'Bradesco', None, '16865', 'Bradesco'),
    ('336', 'C6', '1', '211346179', 'C6'),
    ('403', 'Cora', '1', '17381650', 'Cora'),
    ('364', 'EFI', None, '545806-4', 'EFí'),
    ('748', 'Sicredi', None, '39500000000156390', 'Sicredi'),
]
EMPRESA_SEMENTE = 2      # QUALICONTAX


def norm(conta):
    """'545806-4' e '5458064' são a MESMA conta — o banco varia a grafia."""
    import re
    return re.sub(r'\D', '', str(conta or '')).lstrip('0') or str(conta or '')


def tabela_existe():
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'fin_contas'""",
        fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    if tabela_existe():
        n = (execute_query('SELECT COUNT(*) AS n FROM fin_contas',
                           fetch=True, fetch_one=True) or {}).get('n', 0)
        print(f'fin_contas já existe ({n} conta(s)).')
    else:
        print('fin_contas: criar + semear as 5 contas dos arquivos reais.')

    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0

    if not tabela_existe():
        execute_query(DDL)
        assert tabela_existe()
        print('OK: fin_contas criada.')

    n = (execute_query('SELECT COUNT(*) AS n FROM fin_contas',
                       fetch=True, fetch_one=True) or {}).get('n', 0)
    if n == 0:
        for banco_id, banco_nome, ag, conta, apelido in SEMENTE:
            execute_query(
                'INSERT INTO fin_contas (empresa_id, banco_id, banco_nome, '
                'agencia, conta, conta_norm, apelido) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                (EMPRESA_SEMENTE, banco_id, banco_nome, ag, conta,
                 norm(conta), apelido))
        print(f'OK: {len(SEMENTE)} contas semeadas na empresa {EMPRESA_SEMENTE}.')
    else:
        print('Semente pulada (já há contas).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
