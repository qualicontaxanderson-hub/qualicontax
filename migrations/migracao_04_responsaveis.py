# -*- coding: utf-8 -*-
"""Migração Acessórias+Conexa — PARTE 5c: responsável do escritório por empresa e departamento (05/11/2026).

O Acessórias traz, para cada empresa, QUEM do escritório responde por cada departamento
(Contábil, Fiscal, Pessoal, Financeiro, Legalização, Certificado Digital, Visita): 2.647 marcações
em 444 empresas. O app não tinha onde guardar isso (cliente_contadores é outra coisa: o CONTADOR
com certificado). Esta migração só cria a tabela; quem enche é scripts/migracao_responsaveis.py
e, depois, a ficha do cliente (cartão "Responsáveis no escritório").

    python migrations/migracao_04_responsaveis.py            # dry-run (só mostra)
    python migrations/migracao_04_responsaveis.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CREATE = """
CREATE TABLE IF NOT EXISTS clientes_responsaveis (
    id INT NOT NULL AUTO_INCREMENT,
    cliente_id INT NOT NULL,
    departamento_id INT NOT NULL,
    usuario_id INT NOT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_cr_cliente_depto (cliente_id, departamento_id),
    KEY idx_cr_usuario (usuario_id),
    KEY idx_cr_departamento (departamento_id),
    CONSTRAINT fk_cr_cliente FOREIGN KEY (cliente_id) REFERENCES clientes (id) ON DELETE CASCADE,
    CONSTRAINT fk_cr_departamento FOREIGN KEY (departamento_id) REFERENCES departamentos (id),
    CONSTRAINT fk_cr_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='quem do escritorio responde por cada departamento da empresa (migracao Acessorias 11/2026)'
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    existe = execute_query("SHOW TABLES LIKE 'clientes_responsaveis'", fetch=True)
    print('tabela clientes_responsaveis:', 'já existe' if existe else 'não existe')
    if existe:
        n = execute_query('SELECT COUNT(*) n FROM clientes_responsaveis', fetch=True, fetch_one=True)['n']
        print('  linhas:', n)
        return
    if not a.apply:
        print('DRY-RUN: criaria a tabela. Rode com --apply.')
        return
    execute_query(CREATE, fetch=False)
    print('tabela criada.')


if __name__ == '__main__':
    main()
