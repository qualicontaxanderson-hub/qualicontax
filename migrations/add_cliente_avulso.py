# -*- coding: utf-8 -*-
"""Cadastro AVULSO — a empresa que ainda não é cliente (21/08/2026).

Empresa que chega para um serviço pontual (alteração contratual, escrituração
avulsa, orçamento do Comercial) precisa de cadastro para os arquivos terem
onde morar — mas NÃO é da carteira. Regra do Anderson: "avulso nunca aparece
com os clientes, NUNCA".

Por que uma COLUNA e não uma tabela separada: assim "virar cliente" não é
migração de dados, é só ganhar o número. Endereço, contato, sócio, certificado
e anexos já funcionam no avulso desde o primeiro dia, e é impossível acabar com
dois cadastros da mesma empresa (o CNPJ é único na tabela).

Por que a coluna e não "número vazio": hoje número em branco significa "usa o
ID automático" — deixar em branco NÃO é dizer que é avulso. O avulso é ato
explícito (botão com confirmação na tela), então merece marca própria.

No Dropbox o avulso mora em ``AVULSO/{CNPJ}`` (inclusive o certificado). Ao
virar cliente, a pasta migra inteira para ``EMPRESAS/{nº - razão}`` pela mesma
máquina já testada na troca de número.

    python migrations/add_cliente_avulso.py            # dry-run
    python migrations/add_cliente_avulso.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402


def coluna_existe(nome):
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clientes'
              AND COLUMN_NAME = %s""",
        (nome,), fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


COLS = [
    ('avulso', "ALTER TABLE clientes ADD COLUMN avulso TINYINT(1) NOT NULL "
               "DEFAULT 0 AFTER numero_cliente, ADD INDEX idx_avulso (avulso)"),
    ('avulso_em', "ALTER TABLE clientes ADD COLUMN avulso_em DATETIME NULL "
                  "AFTER avulso"),
    ('virou_cliente_em', "ALTER TABLE clientes ADD COLUMN virou_cliente_em "
                         "DATETIME NULL AFTER avulso_em"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    pend = [c for c, _ in COLS if not coluna_existe(c)]
    print('Pendências:', ', '.join(pend) if pend else 'nenhuma')
    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0

    for col, ddl in COLS:
        if not coluna_existe(col):
            execute_query(ddl)
            assert coluna_existe(col), col
            print(f'OK: clientes.{col}')

    n = (execute_query('SELECT COUNT(*) AS n FROM clientes WHERE avulso = 1',
                       fetch=True, fetch_one=True) or {}).get('n', 0)
    print(f'Avulsos hoje: {n} (os 91 clientes existentes seguem como clientes).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
