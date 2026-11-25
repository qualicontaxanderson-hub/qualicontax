# -*- coding: utf-8 -*-
"""Orçamentos: mais de uma empresa e o nome da empresa nova (25/11/2026).

Anderson: "poder escolher MAIS DE UMA empresa no orçamento, igual temos no contrato" e, na Abertura de empresa para
pessoa física, "o nome da empresa nova" que nasce como avulso ligado ao CPF.

- com_orcamento_empresas (orcamento_id, cliente_id, ordem): as empresas do orçamento; a 1ª continua em
  com_orcamentos.cliente_id (é dela a cobrança), as outras entram no PDF e no texto.
- com_orcamentos.empresa_nova_nome VARCHAR(160): o nome da empresa que vai ser aberta (cliente PF + Abertura de empresa).
- com_orcamentos.empresa_nova_id INT NULL: o avulso criado para esse nome (ligado ao CPF), preenchido quando existir.

    python migrations/migracao_22_orcamento_empresas_e_empresa_nova.py --apply
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


def _tabela(nome):
    return bool(execute_query("SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (nome,), fetch=True))


def aplicar(so_ver=False):
    if _tabela('com_orcamento_empresas'):
        print('  com_orcamento_empresas ja existe')
    else:
        print('  %s com_orcamento_empresas' % ('criaria' if so_ver else 'criando'))
        if not so_ver:
            execute_query("""CREATE TABLE com_orcamento_empresas (
                                 id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                                 orcamento_id INT NOT NULL,
                                 cliente_id INT NOT NULL,
                                 ordem TINYINT NOT NULL DEFAULT 1,
                                 UNIQUE KEY uq_orc_cli (orcamento_id, cliente_id),
                                 KEY ix_cli (cliente_id)
                             ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                             COMMENT='empresas do orçamento (a 1ª = com_orcamentos.cliente_id); 25/11/2026'""", fetch=False)
            print('  ok')
    for col, ddl in (('empresa_nova_nome', "ALTER TABLE com_orcamentos ADD COLUMN empresa_nova_nome VARCHAR(160) NULL COMMENT 'Abertura de empresa para PF: nome da empresa nova' AFTER interno"),
                     ('empresa_nova_id', "ALTER TABLE com_orcamentos ADD COLUMN empresa_nova_id INT NULL COMMENT 'avulso criado para a empresa nova (ligado ao CPF)' AFTER empresa_nova_nome")):
        if _coluna('com_orcamentos', col):
            print('  com_orcamentos.%s ja existe' % col); continue
        print('  %s com_orcamentos.%s' % ('criaria' if so_ver else 'criando', col))
        if not so_ver:
            execute_query(ddl, fetch=False); print('  ok')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='aplica (sem isso, so mostra o que faria)')
    a = ap.parse_args()
    print('migracao 22: orcamento com varias empresas e empresa nova%s' % ('' if a.apply else ' (so olhando)'))
    aplicar(so_ver=not a.apply)


if __name__ == '__main__':
    main()
