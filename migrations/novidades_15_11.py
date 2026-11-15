# -*- coding: utf-8 -*-
"""Novidades: o que mudou no sistema, contado para a equipe (15/11/2026).

Anderson: "quero um outro item nessa barra que seriam as novidades ou alterações do sistema".

- ``novidades``: cada aviso. ``situacao`` rascunho / publicada / descartada. ``commit_hash``
  marca o rascunho que nasceu de uma publicação do sistema (não importa duas vezes; o
  descartado também fica, para não voltar). ``tela_id`` é a tela do catálogo de permissões:
  dá o atalho "Abrir" e faz o aviso aparecer só para quem tem acesso àquela tela.
- ``novidades_lidas``: quem já viu o quê (o número verde no menu é o que falta ver) e a reação
  (Entendi / Tenho dúvida).

Colunas tipo, imagem_* e reacao entraram no mesmo dia, depois da primeira criação: o --apply
acrescenta as que faltarem numa tabela que já existe.

Só cria tabela; não altera nem apaga nada. Collation igual às tabelas antigas.

    python migrations/novidades_15_11.py            # mostra o que faria
    python migrations/novidades_15_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

TABELAS = {
    'novidades': """
CREATE TABLE IF NOT EXISTS novidades (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  titulo        VARCHAR(160) NOT NULL,
  texto         TEXT NOT NULL,
  tipo          VARCHAR(12) NOT NULL DEFAULT 'melhoria' COMMENT 'novo, melhoria, correcao, aviso',
  area          VARCHAR(40) NOT NULL DEFAULT 'Todos' COMMENT 'grupo do catálogo ou Todos',
  tela_id       VARCHAR(10) NULL COMMENT 'tela do catálogo: atalho e quem vê',
  imagem_tipo   VARCHAR(40) NULL,
  imagem_dados  MEDIUMBLOB NULL COMMENT 'print da tela (até 3 MB)',
  data          DATE NOT NULL,
  situacao      VARCHAR(12) NOT NULL DEFAULT 'rascunho' COMMENT 'rascunho, publicada, descartada',
  commit_hash   VARCHAR(12) NULL,
  criado_por    INT NULL,
  publicada_por INT NULL,
  publicada_em  DATETIME NULL,
  criado_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
  atualizado_em DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_commit (commit_hash),
  KEY idx_situacao (situacao, data)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    'novidades_lidas': """
CREATE TABLE IF NOT EXISTS novidades_lidas (
  usuario_id   INT NOT NULL,
  novidade_id  INT NOT NULL,
  lida_em      DATETIME DEFAULT CURRENT_TIMESTAMP,
  reacao       VARCHAR(8) NULL COMMENT 'util (entendi) ou duvida',
  PRIMARY KEY (usuario_id, novidade_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
}


COLUNAS = [  # (tabela, coluna, definição) — acrescentadas se faltarem
    ('novidades', 'tipo', "VARCHAR(12) NOT NULL DEFAULT 'melhoria' COMMENT 'novo, melhoria, correcao, aviso' AFTER texto"),
    ('novidades', 'imagem_tipo', 'VARCHAR(40) NULL AFTER tela_id'),
    ('novidades', 'imagem_dados', "MEDIUMBLOB NULL COMMENT 'print da tela (até 3 MB)' AFTER imagem_tipo"),
    ('novidades_lidas', 'reacao', "VARCHAR(8) NULL COMMENT 'util (entendi) ou duvida' AFTER lida_em"),
]


def tabela_existe(nome):
    return bool(execute_query('SHOW TABLES LIKE %s', (nome,), fetch=True))


def coluna_existe(tabela, coluna):
    return bool(execute_query(f'SHOW COLUMNS FROM {tabela} LIKE %s', (coluna,), fetch=True))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    faltam = [n for n in TABELAS if not tabela_existe(n)]
    for n in TABELAS:
        print(('CRIAR  ' if n in faltam else 'já existe  ') + n)
    cols = [(t, c, d) for t, c, d in COLUNAS if t not in faltam and not coluna_existe(t, c)]
    for t, c, _ in cols:
        print(f'ACRESCENTAR coluna  {t}.{c}')
    if not a.apply:
        print('\n(nada gravado; rode com --apply)')
        sys.exit(0)
    for n in faltam:
        execute_query(TABELAS[n], fetch=False)
        print('criada', n, '->', tabela_existe(n))
    for t, c, d in cols:
        execute_query(f'ALTER TABLE {t} ADD COLUMN {c} {d}', fetch=False)
        print('coluna', t, c, '->', coluna_existe(t, c))
    for n in TABELAS:
        execute_query(f'ALTER TABLE {n} CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci', fetch=False)
