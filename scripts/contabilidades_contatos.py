# -*- coding: utf-8 -*-
"""Contabilidades com endereço completo e vários contatos (19/10/2026).

Acrescenta nome_fantasia, cep, logradouro, numero, complemento e bairro em
``contabilidades`` e cria ``contabilidade_contatos``. O ``contato`` antigo de
cada escritório (se houver) vira o contato principal. Só acrescenta: não
altera nem apaga coluna ou dado que já existe.

    python scripts/contabilidades_contatos.py            # dry-run: só mostra
    python scripts/contabilidades_contatos.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from utils.db_helper import execute_query  # noqa: E402

COLUNAS = [('nome_fantasia', 'VARCHAR(150) NULL AFTER nome'), ('cep', 'VARCHAR(9) NULL AFTER email'),
           ('logradouro', 'VARCHAR(150) NULL AFTER cep'), ('numero', 'VARCHAR(20) NULL AFTER logradouro'),
           ('complemento', 'VARCHAR(100) NULL AFTER numero'), ('bairro', 'VARCHAR(80) NULL AFTER complemento')]
TABELA = """CREATE TABLE IF NOT EXISTS contabilidade_contatos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    contabilidade_id INT NOT NULL,
    nome VARCHAR(120) NOT NULL,
    funcao VARCHAR(40) NULL,
    telefone VARCHAR(20) NULL,
    email VARCHAR(150) NULL,
    principal TINYINT(1) NOT NULL DEFAULT 0,
    criado_por INT NULL,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    KEY idx_contab_contatos (contabilidade_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""


def tem_coluna(c):
    r = execute_query("SELECT COUNT(*) n FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                      "AND TABLE_NAME = 'contabilidades' AND COLUMN_NAME = %s", (c,), fetch=True, fetch_one=True) or {}
    return int(r.get('n') or 0) > 0


def tem_tabela():
    r = execute_query("SELECT COUNT(*) n FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                      "AND TABLE_NAME = 'contabilidade_contatos'", fetch=True, fetch_one=True) or {}
    return int(r.get('n') or 0) > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    faltam = [(c, d) for c, d in COLUNAS if not tem_coluna(c)]
    print('DDL:')
    for c, d in faltam:
        print(f'  ALTER TABLE contabilidades ADD COLUMN {c} {d};')
    print('  ' + (TABELA.replace('\n', '\n  ') + ';' if not tem_tabela() else '-- contabilidade_contatos já existe'))
    antigos = execute_query("SELECT id, nome, contato, telefone, email FROM contabilidades "
                            "WHERE COALESCE(contato, '') <> ''", fetch=True) or []
    print(f'\nContato antigo que vira contato principal: {len(antigos)}')
    for r in antigos:
        print(f"  {r['nome']}: {r['contato']}")
    if not a.apply:
        print('\nDRY-RUN: nada gravado. Rode com --apply para aplicar.')
        return 0
    for c, d in faltam:
        execute_query(f'ALTER TABLE contabilidades ADD COLUMN {c} {d}', fetch=False)
    execute_query(TABELA, fetch=False)
    for r in antigos:
        ja = execute_query("SELECT COUNT(*) n FROM contabilidade_contatos WHERE contabilidade_id = %s",
                           (r['id'],), fetch=True, fetch_one=True) or {}
        if not ja.get('n'):
            execute_query("INSERT INTO contabilidade_contatos (contabilidade_id, nome, principal) VALUES (%s, %s, 1)",
                          (r['id'], ' '.join(r['contato'].split()).upper()), fetch=False)
    print('APLICADO. Conferência:')
    print('  colunas:', [c for c, _ in COLUNAS if tem_coluna(c)])
    print('  tabela contabilidade_contatos:', tem_tabela())
    return 0


if __name__ == '__main__':
    sys.exit(main())
