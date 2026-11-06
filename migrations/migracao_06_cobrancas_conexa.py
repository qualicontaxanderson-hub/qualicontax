# -*- coding: utf-8 -*-
"""Migração Conexa — PARTE 6b: histórico de cobranças do Conexa (06/11/2026).

As 3.144 cobranças de ago/2025 a out/2026 (3.613 itens) ficam em tabela PRÓPRIA, para consulta na
ficha do cliente e nos relatórios, sem mexer nos 2.182 títulos a receber que já vieram do extrato
(decisão do Anderson 05/11: "não dobrar"). Só as 245 ABERTAS viram título a receber de verdade
(``fin_titulos``, origem 'conexa'), com baixa pelo extrato da Efí/Sicredi (ok dele em 06/11).

    python migrations/migracao_06_cobrancas_conexa.py            # dry-run
    python migrations/migracao_06_cobrancas_conexa.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CREATE = """
CREATE TABLE IF NOT EXISTS fin_cobrancas_conexa (
    id INT NOT NULL AUTO_INCREMENT,
    cobranca_conexa INT NOT NULL COMMENT 'id da cobranca no Conexa',
    cliente_id INT NULL COMMENT 'clientes.id (pelo numero oficial ou pelo conexa_id do avulso)',
    destino VARCHAR(10) NULL COMMENT 'CLIENTE|AVULSO',
    nome VARCHAR(255) NULL,
    cpf_cnpj VARCHAR(20) NULL,
    tipo VARCHAR(12) NULL COMMENT 'Contratual|Avulsa',
    competencia DATE NULL,
    vencimento DATE NULL,
    valor DECIMAL(15,2) NOT NULL,
    valor_recebido DECIMAL(15,2) NULL,
    quitacao DATE NULL,
    data_credito DATE NULL,
    status VARCHAR(10) NOT NULL COMMENT 'Quitada|Aberta',
    meio VARCHAR(40) NULL,
    conta VARCHAR(60) NULL,
    nf_numero INT NULL COMMENT 'NFS-e serie 1 (Conexa)',
    observacoes VARCHAR(500) NULL,
    itens JSON NULL COMMENT '[{tipo, servico, valor, quant, obs}]',
    titulo_id INT NULL COMMENT 'fin_titulos criado para as abertas',
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_cc_conexa (cobranca_conexa),
    KEY idx_cc_cliente (cliente_id, competencia),
    KEY idx_cc_status (status, vencimento),
    KEY idx_cc_doc (cpf_cnpj)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='historico de cobrancas importado do Conexa (ago/2025-out/2026), so leitura'
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    existe = execute_query("SHOW TABLES LIKE 'fin_cobrancas_conexa'", fetch=True)
    print('tabela fin_cobrancas_conexa:', 'já existe' if existe else 'não existe')
    if existe:
        print('  linhas:', execute_query('SELECT COUNT(*) n FROM fin_cobrancas_conexa', fetch=True, fetch_one=True)['n'])
        return
    if not a.apply:
        print('DRY-RUN: criaria a tabela. Rode com --apply.')
        return
    execute_query(CREATE, fetch=False)
    print('tabela criada.')


if __name__ == '__main__':
    main()
