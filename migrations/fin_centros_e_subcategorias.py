# -*- coding: utf-8 -*-
"""Centros de custo + subcategorias no financeiro (E2.2, 20/08/2026).

CENTRO DE CUSTO por ESTADO, como o Anderson pensa o negócio: GO, SP e GERAL.
A regra do rateio é do documento dele: "não tem receita na GERAL, aí o rateio
tem que ficar meio a meio" — o centro marcado ``rateia=1`` (GERAL) divide em
PARTES IGUAIS entre os centros normais ativos na hora do relatório (com GO e
SP, meio a meio; se um dia entrar um terceiro estado, vira um terço cada).
O rateio acontece NA LEITURA (DRE): o título guarda o centro cru.

SUBCATEGORIAS no plano gerencial: ``fin_categorias.pai_id`` — ex. Informática
(categoria) → Conexa, Alterdata, Claude (subcategorias). Um nível só: sub de
sub não existe. A sub herda tipo e grupo (linha do DRE) do pai.

``fin_titulos.centro_custo_id`` é OPCIONAL: título sem centro aparece como
"Sem centro de custo" no relatório — o buraco aparece, não some.

    python migrations/fin_centros_e_subcategorias.py            # dry-run
    python migrations/fin_centros_e_subcategorias.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL_CENTROS = """
CREATE TABLE IF NOT EXISTS fin_centros_custo (
  id        INT AUTO_INCREMENT PRIMARY KEY,
  nome      VARCHAR(40) NOT NULL,
  rateia    TINYINT(1) NOT NULL DEFAULT 0,
  ordem     INT DEFAULT 0,
  ativo     TINYINT(1) NOT NULL DEFAULT 1,
  criado_em DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_nome (nome)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

SEED_CENTROS = [('GO', 0, 10), ('SP', 0, 20), ('GERAL', 1, 30)]

# Exemplo que o próprio Anderson deu: Informática → Conexa, Alterdata, Claude.
SEED_SUBS = ('Informática', 'P', 'Tecnologia', ['Conexa', 'Alterdata', 'Claude'])


def coluna_existe(tabela, coluna):
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
              AND COLUMN_NAME = %s""",
        (tabela, coluna), fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def tabela_existe(nome):
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s""",
        (nome,), fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    pend = []
    if not tabela_existe('fin_centros_custo'):
        pend.append('criar fin_centros_custo (+ seed GO/SP/GERAL)')
    if not coluna_existe('fin_titulos', 'centro_custo_id'):
        pend.append('fin_titulos.centro_custo_id')
    if not coluna_existe('fin_categorias', 'pai_id'):
        pend.append('fin_categorias.pai_id (+ seed Informática→Conexa/Alterdata/Claude)')
    print('Pendências:', '; '.join(pend) if pend else 'nenhuma')

    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0

    if not tabela_existe('fin_centros_custo'):
        execute_query(DDL_CENTROS)
        for nome, rateia, ordem in SEED_CENTROS:
            execute_query('INSERT INTO fin_centros_custo (nome, rateia, ordem) '
                          'VALUES (%s, %s, %s)', (nome, rateia, ordem))
        print('OK: fin_centros_custo criada e semeada (GO, SP, GERAL[rateia]).')

    if not coluna_existe('fin_titulos', 'centro_custo_id'):
        execute_query('ALTER TABLE fin_titulos ADD COLUMN centro_custo_id INT NULL '
                      'AFTER categoria_id, ADD INDEX idx_centro (centro_custo_id)')
        print('OK: fin_titulos.centro_custo_id.')

    if not coluna_existe('fin_categorias', 'pai_id'):
        execute_query('ALTER TABLE fin_categorias ADD COLUMN pai_id INT NULL '
                      'AFTER id, ADD INDEX idx_pai (pai_id)')
        nome_pai, tipo, grupo, subs = SEED_SUBS
        pai = execute_query(
            'SELECT id FROM fin_categorias WHERE tipo = %s AND nome = %s',
            (tipo, nome_pai), fetch=True, fetch_one=True)
        if not pai:
            r = execute_query('SELECT MAX(ordem) AS m FROM fin_categorias '
                              'WHERE tipo = %s AND grupo = %s', (tipo, grupo),
                              fetch=True, fetch_one=True)
            ordem = ((r or {}).get('m') or 60) + 1
            pai_id = execute_query(
                'INSERT INTO fin_categorias (tipo, grupo, nome, ordem) '
                'VALUES (%s, %s, %s, %s)', (tipo, grupo, nome_pai, ordem))
        else:
            pai_id = pai['id']
        for sub in subs:
            execute_query(
                'INSERT IGNORE INTO fin_categorias (pai_id, tipo, grupo, nome, ordem) '
                'SELECT %s, tipo, grupo, %s, ordem FROM fin_categorias WHERE id = %s',
                (pai_id, sub, pai_id))
        print('OK: fin_categorias.pai_id + Informática com 3 subcategorias.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
