# -*- coding: utf-8 -*-
"""Migração Acessórias+Conexa — PARTE 6a: contratos de honorários (06/11/2026).

A tabela ``fin_contratos`` do Documento E existia vazia e sem lugar para o que o Anderson decidiu
em 05/11/2026: um contrato por empresa, serviço "Honorários contábeis", valor = soma das
discriminações (Monjolo/Centro Norte têm várias), 13º em quatro modos (parcela única, duas
parcelas, personalizado, sem), reajuste pelo salário mínimo todo janeiro (migrados: jan/2027;
novos: primeiro janeiro depois de 12 meses) com exceções (índice próprio, data própria, sem,
escalonado), e o fluxo de aprovação (funcionário autorizado cadastra → Anderson aprova).

Só ALTER TABLE idempotente (cada coluna conferida antes) + a tabela de itens. Quem enche é
scripts/migracao_honorarios.py (215 mensalidades do Conexa) e, depois, a tela.

    python migrations/migracao_05_honorarios.py            # dry-run (só mostra)
    python migrations/migracao_05_honorarios.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

# (coluna, definição) — na ordem em que devem aparecer
COLUNAS = [
    ('servico_id', "INT NULL COMMENT 'nfse_servicos: Honorarios contabeis = 1'"),
    ('plano', "VARCHAR(60) NULL COMMENT 'faixa/rede do Conexa (Mensalidade Rede Pavao...)'"),
    ('decimo_tipo', "VARCHAR(14) NOT NULL DEFAULT 'NENHUM' COMMENT 'NENHUM|UNICA|DUAS|PERSONALIZADO'"),
    ('decimo_parcelas', "JSON NULL COMMENT 'PERSONALIZADO: [{\"mes\":10,\"pct\":50},{\"mes\":11,\"pct\":50}] (mes = competencia)'"),
    ('decimo_desde', "DATE NULL COMMENT 'na casa desde: base do 13o proporcional (vazio = inicio do contrato); migrados = 2025-01-01, o Conexa cobrava inteiro'"),
    ('reajuste_tipo', "VARCHAR(16) NOT NULL DEFAULT 'SALARIO_MINIMO' COMMENT 'SALARIO_MINIMO|INDICE|DATA_PROPRIA|SEM|ESCALONADO'"),
    ('proximo_reajuste', "DATE NULL COMMENT 'competencia a partir da qual vale o proximo reajuste'"),
    ('reajuste_obs', "VARCHAR(255) NULL COMMENT 'indice combinado, degraus do escalonado, motivo do adiamento'"),
    ('situacao', "VARCHAR(12) NOT NULL DEFAULT 'ATIVO' COMMENT 'PENDENTE|ATIVO|ENCERRADO|REPROVADO'"),
    ('conexa_contrato_id', "INT NULL COMMENT 'rastro da migracao (fin_mensalidades.contrato_conexa)'"),
    ('observacao', "VARCHAR(255) NULL COMMENT 'recado de quem cadastrou / motivo da reprovacao'"),
    ('criado_por', 'INT NULL'),
    ('aprovado_por', 'INT NULL'),
    ('aprovado_em', 'DATETIME NULL'),
    ('criado_em', 'TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP'),
    ('atualizado_em', 'TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'),
]
INDICES = [
    ('uq_contrato_conexa', 'UNIQUE KEY uq_contrato_conexa (conexa_contrato_id)'),
    ('idx_contrato_cliente', 'KEY idx_contrato_cliente (cliente_id, situacao)'),
    ('idx_contrato_reajuste', 'KEY idx_contrato_reajuste (situacao, proximo_reajuste)'),
]
CREATE_ITENS = """
CREATE TABLE IF NOT EXISTS fin_contrato_itens (
    id INT NOT NULL AUTO_INCREMENT,
    contrato_id INT NOT NULL,
    servico_id INT NULL COMMENT 'nfse_servicos (opcional: LMC, loja de conveniencia...)',
    descricao VARCHAR(120) NOT NULL,
    valor DECIMAL(15,2) NOT NULL,
    ordem SMALLINT NOT NULL DEFAULT 1,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_ci_contrato (contrato_id),
    CONSTRAINT fk_ci_contrato FOREIGN KEY (contrato_id) REFERENCES fin_contratos (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='discriminacoes do contrato de honorarios: a soma e o valor do contrato (06/11/2026)'
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    existentes = {c['Field'] for c in execute_query('SHOW COLUMNS FROM fin_contratos', fetch=True) or []}
    indices = {i['Key_name'] for i in execute_query('SHOW INDEX FROM fin_contratos', fetch=True) or []}
    faltam = [(c, d) for c, d in COLUNAS if c not in existentes]
    idx_faltam = [(n, d) for n, d in INDICES if n not in indices]
    itens_existe = bool(execute_query("SHOW TABLES LIKE 'fin_contrato_itens'", fetch=True))
    print('fin_contratos: %d colunas a criar: %s' % (len(faltam), [c for c, _ in faltam]))
    print('fin_contratos: %d índices a criar: %s' % (len(idx_faltam), [n for n, _ in idx_faltam]))
    print('fin_contrato_itens:', 'já existe' if itens_existe else 'não existe')
    if not faltam and not idx_faltam and itens_existe:
        print('nada a fazer.')
        return
    if not a.apply:
        print('DRY-RUN. Rode com --apply.')
        return
    anterior = None
    for col, defin in COLUNAS:
        if col in existentes:
            anterior = col
            continue
        pos = ' AFTER %s' % anterior if anterior else ''
        execute_query('ALTER TABLE fin_contratos ADD COLUMN %s %s%s' % (col, defin, pos), fetch=False)
        print('  + coluna', col)
        anterior = col
    for nome, defin in idx_faltam:
        execute_query('ALTER TABLE fin_contratos ADD %s' % defin, fetch=False)
        print('  + índice', nome)
    if not itens_existe:
        execute_query(CREATE_ITENS, fetch=False)
        print('  + tabela fin_contrato_itens')
    print('pronto.')


if __name__ == '__main__':
    main()
