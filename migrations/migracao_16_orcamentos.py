# -*- coding: utf-8 -*-
"""Orçamentos do Comercial (21/11/2026).

Anderson (20/11/2026): "quase 100% dos serviços serão feitos aqui no Comercial e migrado para o Financeiro, para
controle... o oficial teria que ter sido feito um Orçamento no Comercial, aprovado por nós... feito o serviço, o
Gabriel entra no orçamento que ele fez e manda para a Cobrança: já vem para Financeiro / Lançamentos com o
histórico completo, e eu novamente aprovo para cobrar".

Tabelas: ``com_orcamentos`` (o orçamento), ``com_orcamento_itens`` (os serviços do catálogo com valor) e
``com_orcamento_eventos`` (a linha do tempo: quem fez o quê e quando). ``fin_lancamentos.orcamento_id`` liga o
lançamento ao orçamento que o gerou.

    python migrations/migracao_16_orcamentos.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

TABELAS = {
    'com_orcamentos': """
        CREATE TABLE IF NOT EXISTS com_orcamentos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            empresa_id INT NOT NULL DEFAULT 2,
            numero VARCHAR(12) NULL COMMENT 'ORC-000001',
            cliente_id INT NOT NULL COMMENT 'cliente cadastrado ou avulso (clientes.avulso = 1)',
            titulo VARCHAR(160) NULL,
            status VARCHAR(12) NOT NULL DEFAULT 'RASCUNHO' COMMENT 'RASCUNHO|APROVACAO|APROVADO|COBRANCA|CONCLUIDO|REPROVADO|CANCELADO',
            validade DATE NULL COMMENT 'ate quando a proposta vale',
            como VARCHAR(12) NULL COMMENT 'BOLETO_NOTA|BOLETO|MENSAL: como pretende cobrar',
            parcelas TINYINT NULL,
            comissao_tipo VARCHAR(8) NULL COMMENT 'PADRAO|PCT|FIXO|SEM: a comissao pedida por quem montou',
            comissao_valor DECIMAL(12,2) NULL,
            total DECIMAL(12,2) NOT NULL DEFAULT 0,
            observacao TEXT NULL COMMENT 'condicoes e observacoes que vao para o cliente',
            nota_interna TEXT NULL COMMENT 'so a equipe ve',
            token VARCHAR(40) NULL,
            criado_por INT NOT NULL,
            criado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            aprovado_por INT NULL, aprovado_em DATETIME NULL,
            enviado_em DATETIME NULL COMMENT 'quando foi mandado ao cliente',
            feito_em DATETIME NULL COMMENT 'quando a pessoa deu o servico como feito e mandou cobrar',
            lancamento_id INT NULL COMMENT 'fin_lancamentos criado ao mandar para a cobranca',
            motivo VARCHAR(400) NULL COMMENT 'reprovacao ou cancelamento',
            cancelado_em DATETIME NULL,
            atualizado_em DATETIME NULL ON UPDATE CURRENT_TIMESTAMP,
            KEY idx_cli (cliente_id), KEY idx_status (status), KEY idx_quem (criado_por), UNIQUE KEY uk_token (token)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    'com_orcamento_itens': """
        CREATE TABLE IF NOT EXISTS com_orcamento_itens (
            id INT AUTO_INCREMENT PRIMARY KEY,
            orcamento_id INT NOT NULL,
            servico_id INT NULL COMMENT 'nfse_servicos',
            descricao VARCHAR(200) NOT NULL,
            quantidade DECIMAL(10,2) NOT NULL DEFAULT 1,
            valor_unitario DECIMAL(12,2) NOT NULL,
            valor_total DECIMAL(12,2) NOT NULL,
            emite_nota TINYINT(1) NOT NULL DEFAULT 1 COMMENT 'copiado do servico na hora: 0 = so no boleto',
            ordem SMALLINT NOT NULL DEFAULT 1,
            KEY idx_orc (orcamento_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    'com_orcamento_eventos': """
        CREATE TABLE IF NOT EXISTS com_orcamento_eventos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            orcamento_id INT NOT NULL,
            quando DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            usuario_id INT NULL,
            usuario_nome VARCHAR(120) NULL,
            tipo VARCHAR(20) NOT NULL COMMENT 'criado|editado|aprovacao|aprovado|reprovado|enviado|cobranca|concluido|cancelado|nota',
            texto VARCHAR(400) NULL,
            KEY idx_orc (orcamento_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
}
COLUNAS = {   # (tabela, coluna): definição
    ('fin_lancamentos', 'orcamento_id'): "ADD COLUMN orcamento_id INT NULL COMMENT 'com_orcamentos que gerou este lancamento' AFTER contrato_id",
}


def _tem_tabela(t):
    r = execute_query("SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (t,), fetch=True, fetch_one=True) or {}
    return bool(r.get('n'))


def _tem_coluna(t, c):
    r = execute_query("SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s", (t, c), fetch=True, fetch_one=True) or {}
    return bool(r.get('n'))


def aplicar(so_ver=False):
    for t, sql in TABELAS.items():
        if _tem_tabela(t):
            print('  tabela %-24s ja existe' % t); continue
        print('  tabela %-24s %s' % (t, 'criaria' if so_ver else 'criando'))
        if not so_ver:
            execute_query(sql, fetch=False)
    for (t, c), ddl in COLUNAS.items():
        if _tem_coluna(t, c):
            print('  coluna %s.%s ja existe' % (t, c)); continue
        print('  coluna %s.%s %s' % (t, c, 'criaria' if so_ver else 'criando'))
        if not so_ver:
            execute_query('ALTER TABLE %s %s' % (t, ddl), fetch=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    aplicar(so_ver=not a.apply)
    print('ok' if a.apply else 'so olhando (use --apply)')


if __name__ == '__main__':
    main()
