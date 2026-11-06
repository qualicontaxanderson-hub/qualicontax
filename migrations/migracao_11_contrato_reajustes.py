# -*- coding: utf-8 -*-
"""Parte 6 — histórico de reajustes dos contratos de honorários (06/11/2026).

Decisão do Anderson (05/11): reajuste padrão pelo salário mínimo todo janeiro; ele DIGITA o percentual em janeiro
e aplica a todos do padrão de uma vez. Cada aplicação guarda o antes (valor e discriminações) para poder desfazer.

    python migrations/migracao_11_contrato_reajustes.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CREATE = """
CREATE TABLE IF NOT EXISTS fin_contrato_reajustes (
    id INT NOT NULL AUTO_INCREMENT,
    contrato_id INT NOT NULL,
    cliente_id INT NOT NULL,
    lote VARCHAR(40) NOT NULL COMMENT 'ex. SM-2027-01: salario minimo, vigencia jan/2027',
    tipo VARCHAR(20) NOT NULL COMMENT 'SALARIO_MINIMO|INDICE|DATA_PROPRIA|ESCALONADO|MANUAL',
    vigencia DATE NOT NULL COMMENT 'competencia a partir da qual vale',
    percentual DECIMAL(8,4) NOT NULL,
    valor_antes DECIMAL(15,2) NOT NULL,
    valor_depois DECIMAL(15,2) NOT NULL,
    itens_antes JSON NULL,
    proximo_antes DATE NULL,
    aplicado_por INT NOT NULL,
    aplicado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    desfeito_em DATETIME NULL,
    desfeito_por INT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_cr_lote (contrato_id, lote),
    KEY idx_cr_cliente (cliente_id, vigencia)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='reajustes aplicados aos contratos de honorarios (com o antes, para desfazer)'
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    existe = execute_query("SHOW TABLES LIKE 'fin_contrato_reajustes'", fetch=True)
    print('fin_contrato_reajustes:', 'já existe' if existe else 'não existe')
    if not a.apply:
        print('dry-run. Use --apply para criar.')
        return
    execute_query(CREATE, fetch=False)
    print('ok')


if __name__ == '__main__':
    main()
