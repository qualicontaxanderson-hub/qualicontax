# -*- coding: utf-8 -*-
"""
Q-COLABORE Bloco 1 / Parte 1 — migration 05 (o "+1"): ``usuarios.nick``.

Por que separada das outras quatro
----------------------------------
As migrations 01-04 são a espinha do Bloco 1. Esta é uma coluna de conveniência
que o recon original não previa e que só apareceu quando o DDL do
cadastro_pendente foi escrito: o candidato preenche ``nick_escolhido`` e, na
aprovação, o valor não teria para onde ir — usuarios não tinha coluna de nick.
Descartar dado que a pessoa digitou é o tipo de perda silenciosa que ninguém
percebe até alguém perguntar "cadê meu apelido?".

Fica separada porque tem um motivo próprio e uma reversão própria: se um dia o
nick for abandonado, some só esta, sem mexer na classe_conta.

VARCHAR(60) NULL, sem UNIQUE: nick é rótulo de UI, não identidade. Login, email
e cpf continuam sendo as chaves únicas de usuarios — dois "Dedé" não podem
travar um cadastro.

Idempotente e reversível (a reversão preserva a coluna se alguém já preencheu).

  python migrations/qcolabore_05_usuarios_nick.py             # aplica
  python migrations/qcolabore_05_usuarios_nick.py --reverter  # desfaz
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# O console do Windows abre em cp1252 e os simbolos de status (checkmark) do
# padrao das migrations deste repo estouram UnicodeEncodeError NO PRINT — depois
# de o DDL ja ter rodado, deixando a migration pela metade e parecendo que
# falhou. Forca UTF-8 na saida para que rodar daqui seja igual a rodar no Railway.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from utils.db_helper import execute_query, get_last_db_error  # noqa: E402

TABELA = 'usuarios'
COLUNA = 'nick'


def _migrate(sql):
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {sql[:180]}')


def _coluna_existe(tabela, coluna):
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (tabela, coluna), fetch=True, fetch_one=True,
    ) or {}
    return r.get('cnt', 0) > 0


def migrate_usuarios_nick():
    """Cria usuarios.nick (idempotente)."""
    print(f'Iniciando migração: {TABELA}.{COLUNA} ...')
    if _coluna_existe(TABELA, COLUNA):
        print(f'✓ Coluna {COLUNA} já existe')
        return True
    _migrate(f'ALTER TABLE {TABELA} ADD COLUMN {COLUNA} VARCHAR(60) NULL AFTER nome')
    print(f'✓ Coluna {COLUNA} criada')
    return True


def rollback_usuarios_nick():
    """Remove a coluna — mas só se ninguém tiver preenchido."""
    print(f'Revertendo: {TABELA}.{COLUNA} ...')
    if not _coluna_existe(TABELA, COLUNA):
        print(f'✓ Coluna {COLUNA} já não existe')
        return True
    n = (execute_query(
        f"SELECT COUNT(*) AS cnt FROM {TABELA} "
        f"WHERE {COLUNA} IS NOT NULL AND {COLUNA} <> ''",
        fetch=True, fetch_one=True) or {}).get('cnt', 0)
    if n:
        # Nick é dado de gente, não metadado: não some por reversão automática.
        raise RuntimeError(
            f'{n} usuário(s) com nick preenchido. Reversão recusada — apague os '
            'valores à mão antes, se essa for mesmo a intenção.')
    _migrate(f'ALTER TABLE {TABELA} DROP COLUMN {COLUNA}')
    print(f'✓ Coluna {COLUNA} removida')
    return True


if __name__ == '__main__':
    try:
        if '--reverter' in sys.argv:
            rollback_usuarios_nick()
        else:
            migrate_usuarios_nick()
        print('\n✓ Migração concluída com sucesso!')
    except Exception as e:
        print(f'\n✗ Migração falhou: {e}')
        sys.exit(1)
