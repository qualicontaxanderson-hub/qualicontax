# -*- coding: utf-8 -*-
"""Parte 6, etapa 3 — itens do lote mensal (06/11/2026).

Uma linha por empresa cobrada no lote da competência: o que entrou (honorários, 13º, lançamentos),
o que saiu (nota, boleto, título, e-mail) e o erro, se houve. UNIQUE (lote, cliente) é a idempotência:
rodar o lote de novo só cobra quem ainda não foi cobrado.

    python migrations/migracao_08_lote_itens.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CREATE = """
CREATE TABLE IF NOT EXISTS fin_lote_itens (
    id INT NOT NULL AUTO_INCREMENT,
    lote_id INT NOT NULL,
    cliente_id INT NOT NULL,
    contrato_id INT NULL,
    itens JSON NULL COMMENT '[{origem: honorarios|decimo|lancamento, servico_id, nome, valor, lancamento_id}]',
    total DECIMAL(15,2) NOT NULL,
    vencimento DATE NOT NULL,
    status VARCHAR(8) NOT NULL DEFAULT 'pendente' COMMENT 'pendente|ok|erro|pulado',
    titulo_id INT NULL,
    nfse_id INT NULL,
    boleto_id INT NULL,
    email VARCHAR(255) NULL COMMENT 'para quem o e-mail saiu (ou o aviso de que nao saiu)',
    erro TEXT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_li_lote_cliente (lote_id, cliente_id),
    KEY idx_li_status (lote_id, status),
    CONSTRAINT fk_li_lote FOREIGN KEY (lote_id) REFERENCES fin_lotes_mensais (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='uma empresa cobrada no lote mensal: o que entrou e o que saiu'
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if execute_query("SHOW TABLES LIKE 'fin_lote_itens'", fetch=True):
        print('fin_lote_itens já existe.')
        return
    if not a.apply:
        print('DRY-RUN: criaria fin_lote_itens. Rode com --apply.')
        return
    execute_query(CREATE, fetch=False)
    print('tabela criada.')


if __name__ == '__main__':
    main()
