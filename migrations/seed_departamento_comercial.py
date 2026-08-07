# -*- coding: utf-8 -*-
"""
Seed — departamento ``Comercial`` (idempotente).

A lista de departamentos do formulário público e do painel de análise vem de
``departamentos WHERE ativo = 1``. Comercial simplesmente não existia (nem ativo,
nem inativo), então candidatos comerciais não tinham onde se encaixar. Este seed
insere a linha se — e só se — ela ainda não existe. NÃO reativa nada: reativar um
departamento desativado é decisão de quem administra, não de migration.

  python migrations/seed_departamento_comercial.py             # insere se faltar
  python migrations/seed_departamento_comercial.py --reverter  # remove se órfão
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

NOME = 'Comercial'


def _migrate(sql, params=None):
    if execute_query(sql, params, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Seed abortado — {erro} | SQL: {sql[:180]}')


def _achar():
    """Linha do departamento pelo nome (o collation da coluna é case-insensitive)."""
    return execute_query(
        'SELECT id, nome, ativo FROM departamentos WHERE nome = %s',
        (NOME,), fetch=True, fetch_one=True)


def migrate():
    print(f'Seed: departamento {NOME!r} ...')
    row = _achar()
    if row:
        # Já existe: NÃO mexe no ativo — se estiver inativo, a reativação é decisão
        # manual do admin, deliberadamente fora daqui.
        print(f'• já existe (id={row["id"]}, ativo={row["ativo"]}) — nada a fazer')
        return True
    _migrate('INSERT INTO departamentos (nome, ativo) VALUES (%s, 1)', (NOME,))
    novo = _achar()
    print(f'✓ inserido (id={novo["id"] if novo else "?"}, ativo=1)')
    return True


def rollback():
    """Remove o Comercial só se nada apontar para ele — trilha não pode quebrar."""
    print(f'Revertendo seed: departamento {NOME!r} ...')
    row = _achar()
    if not row:
        print('✓ já não existe')
        return True
    dep_id = row['id']
    usos = 0
    for tabela, coluna in (('usuarios', 'departamento_id'),
                           ('cadastro_pendente_departamentos', 'departamento_id'),
                           ('niveis_acesso_departamento', 'departamento_id')):
        r = execute_query(f'SELECT COUNT(*) AS c FROM {tabela} WHERE {coluna} = %s',
                          (dep_id,), fetch=True, fetch_one=True) or {}
        usos += r.get('c', 0)
    if usos:
        print(f'  ATENÇÃO: {usos} vínculo(s) apontam para {NOME} (id={dep_id}). '
              'NÃO removido — desative pela tela se quiser tirá-lo da lista.')
        return True
    _migrate('DELETE FROM departamentos WHERE id = %s', (dep_id,))
    print(f'✓ removido (id={dep_id})')
    return True


if __name__ == '__main__':
    try:
        if '--reverter' in sys.argv:
            rollback()
        else:
            migrate()
        print('\n✓ Seed concluído com sucesso!')
    except Exception as e:
        print(f'\n✗ Seed falhou: {e}')
        sys.exit(1)
