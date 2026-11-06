# -*- coding: utf-8 -*-
"""Parte 6, etapa 5 — convite para cadastrar honorários (06/11/2026).

O Anderson gera um link para uma empresa sem honorários e manda para quem a cadastrou (ou para a
Alessandra). A pessoa entra com o PRÓPRIO login e senha, vê só aquela empresa, cadastra o contrato
(que nasce PENDENTE, para ele aprovar) e o link morre. "tem que ter LOGIN e SENHA para ter segurança,
e depois de cadastrar não ter mais acesso."

    python migrations/migracao_09_honorarios_convites.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CREATE = """
CREATE TABLE IF NOT EXISTS fin_honorarios_convites (
    id INT NOT NULL AUTO_INCREMENT,
    token VARCHAR(48) NOT NULL,
    cliente_id INT NOT NULL,
    usuario_id INT NULL COMMENT 'para quem e o link; NULL = qualquer usuario logado',
    criado_por INT NOT NULL,
    expira_em DATETIME NOT NULL,
    usado_em DATETIME NULL,
    usado_por INT NULL,
    contrato_id INT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_hc_token (token),
    KEY idx_hc_cliente (cliente_id, usado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='link de uso unico para cadastrar os honorarios de uma empresa (login obrigatorio)'
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if execute_query("SHOW TABLES LIKE 'fin_honorarios_convites'", fetch=True):
        print('fin_honorarios_convites já existe.')
        return
    if not a.apply:
        print('DRY-RUN: criaria fin_honorarios_convites. Rode com --apply.')
        return
    execute_query(CREATE, fetch=False)
    print('tabela criada.')


if __name__ == '__main__':
    main()
