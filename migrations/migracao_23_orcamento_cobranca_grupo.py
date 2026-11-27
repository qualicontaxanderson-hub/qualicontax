# -*- coding: utf-8 -*-
"""Orçamentos: cobrança do grupo (26/11/2026).

Anderson: no orçamento com várias empresas, escolher "um boleto para tudo, no nome de quem paga" ou "um boleto por
empresa, cada serviço dizendo de qual empresa é".

- com_orcamentos.cobranca_modo VARCHAR(12) NULL: 'UNICA' (padrão; NULL vale UNICA) ou 'POR_EMPRESA'.
- com_orcamentos.pagador_cliente_id INT NULL: em UNICA, quem paga quando não é a 1ª empresa (cliente_id).
- com_orcamento_itens.cliente_id INT NULL: em POR_EMPRESA, de qual empresa é o serviço.

    python migrations/migracao_23_orcamento_cobranca_grupo.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402


def _coluna(tabela, coluna):
    return execute_query("""SELECT COLUMN_TYPE AS tipo FROM INFORMATION_SCHEMA.COLUMNS
                             WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s""", (tabela, coluna), fetch=True, fetch_one=True)


def aplicar(so_ver=False):
    for tabela, col, ddl in (
            ('com_orcamentos', 'cobranca_modo', "ALTER TABLE com_orcamentos ADD COLUMN cobranca_modo VARCHAR(12) NULL COMMENT 'UNICA (um boleto para tudo) | POR_EMPRESA' AFTER como"),
            ('com_orcamentos', 'pagador_cliente_id', "ALTER TABLE com_orcamentos ADD COLUMN pagador_cliente_id INT NULL COMMENT 'em UNICA: quem paga, quando não é a 1ª empresa' AFTER cobranca_modo"),
            ('com_orcamento_itens', 'cliente_id', "ALTER TABLE com_orcamento_itens ADD COLUMN cliente_id INT NULL COMMENT 'em POR_EMPRESA: de qual empresa é o serviço' AFTER orcamento_id")):
        if _coluna(tabela, col):
            print('  %s.%s ja existe' % (tabela, col)); continue
        print('  %s %s.%s' % ('criaria' if so_ver else 'criando', tabela, col))
        if not so_ver:
            execute_query(ddl, fetch=False); print('  ok')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='aplica (sem isso, so mostra o que faria)')
    a = ap.parse_args()
    print('migracao 23: cobranca do grupo no orcamento%s' % ('' if a.apply else ' (so olhando)'))
    aplicar(so_ver=not a.apply)


if __name__ == '__main__':
    main()
