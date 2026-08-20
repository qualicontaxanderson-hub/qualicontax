# -*- coding: utf-8 -*-
"""Programações de pagamento/recebimento (E2.3, 20/08/2026).

A conta que se repete todo mês vira PROGRAMAÇÃO: Conexa R$ 499,00 todo dia
10, salário, energia. O sistema gera o título do mês sozinho (idempotente
por chave_idem "prog:{id}:comp:{YYYY-MM}" — gerar duas vezes não duplica,
mesma proteção da geração de contratos do Documento E).

DUAS NATUREZAS (decisão do Anderson em 20/08/2026):
* FIXA (Conexa 499): veio diferente do esperado → a tela pergunta, com TRÊS
  opções: juros/multa do atraso (sugerida quando pagou depois do vencimento)
  · reajuste daqui pra frente (atualiza a programação) · só deste mês.
* VARIÁVEL (energia, salário — ``variavel=1``): flutuar é a natureza dela;
  o valor esperado é só referência para o fluxo e NINGUÉM pergunta nada.

``fin_titulos.programacao_id`` liga o título gerado à sua programação — é
por ele que a baixa sabe se pergunta e o reajuste sabe quem atualizar.

    python migrations/criar_fin_programacoes.py            # dry-run
    python migrations/criar_fin_programacoes.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS fin_programacoes (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id       INT NOT NULL,
  tipo             CHAR(1) NOT NULL,
  descricao        VARCHAR(255) NOT NULL,
  contraparte_nome VARCHAR(255) NOT NULL,
  contraparte_doc  VARCHAR(20),
  categoria_id     INT NOT NULL,
  centro_custo_id  INT NULL,
  valor_esperado   DECIMAL(15,2) NOT NULL,
  dia_vencimento   TINYINT NOT NULL,
  variavel         TINYINT(1) NOT NULL DEFAULT 0,
  inicio           DATE NOT NULL,
  fim              DATE NULL,
  ativo            TINYINT(1) NOT NULL DEFAULT 1,
  observacao       TEXT,
  criado_em        DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_ativa (ativo, dia_vencimento),
  INDEX idx_empresa (empresa_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def tabela_existe(nome):
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s""",
        (nome,), fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def coluna_existe(tabela, coluna):
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
              AND COLUMN_NAME = %s""",
        (tabela, coluna), fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    pend = []
    if not tabela_existe('fin_programacoes'):
        pend.append('criar fin_programacoes')
    if not coluna_existe('fin_titulos', 'programacao_id'):
        pend.append('fin_titulos.programacao_id')
    print('Pendências:', '; '.join(pend) if pend else 'nenhuma')
    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0

    if not tabela_existe('fin_programacoes'):
        execute_query(DDL)
        assert tabela_existe('fin_programacoes')
        print('OK: fin_programacoes criada.')
    if not coluna_existe('fin_titulos', 'programacao_id'):
        execute_query('ALTER TABLE fin_titulos ADD COLUMN programacao_id INT NULL '
                      'AFTER contrato_id, ADD INDEX idx_prog (programacao_id)')
        print('OK: fin_titulos.programacao_id.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
