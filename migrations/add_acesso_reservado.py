# -*- coding: utf-8 -*-
"""Empresa RESERVADA: só o admin vê, com exceção nominal por usuário.

POR QUE
-------
02/10/2026, antes de abrir o sistema à equipe: há empresas (as dos sócios do
escritório, por exemplo) que nenhum funcionário deve ver, mesmo sendo do
departamento que cuida delas. A regra pedida pelo Anderson:

* a empresa marcada como reservada some para todo usuário que não é ADMIN —
  listas, seletores, buscas, notas, downloads e URL digitada;
* o admin libera uma pessoa específica para ela (a EXCEÇÃO), sem liberar as
  outras reservadas.

A antiga ``usuario_empresas_permitidas`` (lista de quem vê o quê) NÃO é esta
regra e nunca foi aplicada por nenhuma rota; estava vazia em 02/10/2026.

O QUE CRIA
----------
* ``clientes.acesso_reservado`` TINYINT(1) NOT NULL DEFAULT 0 — INSTANT no
  MySQL 8 (só metadados). Default 0: nada muda até o admin marcar uma empresa.
* ``cliente_acesso_excecao`` (cliente_id, usuario_id) — quem, além do admin,
  enxerga aquela reservada.

    python migrations/add_acesso_reservado.py            # dry-run
    python migrations/add_acesso_reservado.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

SQL_COLUNA = [
    'ALTER TABLE clientes ADD COLUMN acesso_reservado TINYINT(1) NOT NULL DEFAULT 0, '
    'ALGORITHM=INSTANT',
    'ALTER TABLE clientes ADD COLUMN acesso_reservado TINYINT(1) NOT NULL DEFAULT 0, '
    'ALGORITHM=INPLACE, LOCK=NONE',
]

SQL_TABELA = """
CREATE TABLE IF NOT EXISTS cliente_acesso_excecao (
    cliente_id  INT NOT NULL,
    usuario_id  INT NOT NULL,
    criado_em   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    criado_por  INT NULL,
    PRIMARY KEY (cliente_id, usuario_id),
    KEY idx_excecao_usuario (usuario_id),
    CONSTRAINT fk_excecao_cliente FOREIGN KEY (cliente_id)
        REFERENCES clientes (id) ON DELETE CASCADE,
    CONSTRAINT fk_excecao_usuario FOREIGN KEY (usuario_id)
        REFERENCES usuarios (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def coluna_existe():
    r = execute_query(
        'SELECT COUNT(*) n FROM INFORMATION_SCHEMA.COLUMNS '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s',
        ('clientes', 'acesso_reservado'), fetch=True, fetch_one=True) or {}
    return int(r.get('n') or 0) > 0


def tabela_existe():
    r = execute_query(
        'SELECT COUNT(*) n FROM INFORMATION_SCHEMA.TABLES '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s',
        ('cliente_acesso_excecao',), fetch=True, fetch_one=True) or {}
    return int(r.get('n') or 0) > 0


def main():
    ap = argparse.ArgumentParser(description='Empresa reservada + exceções.')
    ap.add_argument('--apply', action='store_true', help='executa; sem isto é só dry-run')
    args = ap.parse_args()

    falta_coluna, falta_tabela = not coluna_existe(), not tabela_existe()
    if not (falta_coluna or falta_tabela):
        print('clientes.acesso_reservado e cliente_acesso_excecao já existem — nada a fazer.')
        return 0
    if not args.apply:
        print('SQL QUE SERIA EXECUTADO:')
        if falta_coluna:
            for s in SQL_COLUNA:
                print('   ' + s + ';   (o segundo só se o primeiro for recusado)')
        if falta_tabela:
            print(SQL_TABELA.strip() + ';')
        print()
        print('ROLLBACK, se precisar:')
        print('   DROP TABLE cliente_acesso_excecao;')
        print('   ALTER TABLE clientes DROP COLUMN acesso_reservado;')
        print()
        print('DRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        return 0
    if falta_coluna:
        for s in SQL_COLUNA:
            print('EXECUTANDO: ' + s)
            if execute_query(s, fetch=False) is not None:
                break
            print('   recusado; tentando a forma seguinte.')
    if falta_tabela:
        print('EXECUTANDO: CREATE TABLE cliente_acesso_excecao')
        execute_query(SQL_TABELA, fetch=False)
    ok = coluna_existe() and tabela_existe()
    print('OK.' if ok else 'FALHOU: confira o log acima.')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
