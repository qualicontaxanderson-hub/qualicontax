# -*- coding: utf-8 -*-
"""
Migration — clientes.aberta_pela_casa.

ADD COLUMN aberta_pela_casa TINYINT(1) NOT NULL DEFAULT 0
  COMMENT 'empresa constituída pela Qualicontax — dado comercial'

Por quê guardar: saber quantos clientes nasceram dentro da casa é informação que o
comercial vai querer filtrar depois. Barato agora, caro de reconstruir mais tarde.
Nasce 0 para toda a base existente (ninguém marcado até alguém marcar na tela).

Idempotente (checa information_schema antes do ADD) e reversível.

  python migrations/cliente_aberta_pela_casa.py             # aplica
  python migrations/cliente_aberta_pela_casa.py --reverter  # desfaz
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from utils.db_helper import execute_query, get_last_db_error  # noqa: E402

TABELA = 'clientes'
COLUNA = 'aberta_pela_casa'


def _migrate(sql):
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {sql[:180]}')


def _coluna_existe():
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABELA, COLUNA), fetch=True, fetch_one=True,
    ) or {}
    return r.get('cnt', 0) > 0


def migrate():
    print(f'Migração: {TABELA}.{COLUNA} ...')
    if _coluna_existe():
        print(f'• {TABELA}.{COLUNA} já existe — nada a fazer')
        return True
    _migrate(
        f"ALTER TABLE {TABELA} ADD COLUMN {COLUNA} TINYINT(1) NOT NULL DEFAULT 0 "
        "COMMENT 'empresa constituída pela Qualicontax — dado comercial'")
    print(f'✓ {TABELA}.{COLUNA} adicionada')
    return True


def rollback():
    print(f'Revertendo: {TABELA}.{COLUNA} ...')
    if not _coluna_existe():
        print(f'✓ {TABELA}.{COLUNA} já não existe')
        return True
    _migrate(f'ALTER TABLE {TABELA} DROP COLUMN {COLUNA}')
    print(f'✓ {TABELA}.{COLUNA} removida')
    return True


if __name__ == '__main__':
    try:
        if '--reverter' in sys.argv:
            rollback()
        else:
            migrate()
        print('\n✓ Migração concluída com sucesso!')
    except Exception as e:
        print(f'\n✗ Migração falhou: {e}')
        sys.exit(1)
