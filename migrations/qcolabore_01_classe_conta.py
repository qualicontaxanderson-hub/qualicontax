# -*- coding: utf-8 -*-
"""
Q-COLABORE Bloco 1 / Parte 1 — migration 01: ``usuarios.classe_conta``.

Por que uma coluna NOVA
-----------------------
O ``tipo_usuario`` responde "que cargo essa pessoa tem no escritório"
(ADMIN/GERENTE/CONTADOR/ASSISTENTE/ESTAGIARIO). A pergunta do Q-Colabore é
outra e ortogonal: "essa conta é de um FUNCIONÁRIO ou de um CLIENTE?".
Enfiar 'CLIENTE' no ENUM de tipo_usuario misturaria os dois eixos e faria toda
regra de permissão existente ter que aprender um caso novo. Coluna separada:
``tipo_usuario`` e ``is_admin()`` seguem intocados, e nada que existe muda de
comportamento.

O guarda-corpo (CHECK)
----------------------
``tipo_usuario`` é NOT NULL e SEM default: toda conta CLIENTE vai carregar
algum valor só para satisfazer a coluna. Hoje isso é seguro na prática
(``is_admin()`` devolve False e ``has_permission()`` sem perfis nega tudo), mas
deixa um caminho aberto: alguém marcar tipo_usuario='ADMIN' numa linha CLIENTE
e ela virar admin do escritório. O CHECK fecha esse caminho NO BANCO, sem tocar
no is_admin().

CUIDADO: MySQL < 8.0.16 PARSEIA E IGNORA o CHECK em silêncio — aplicaria "com
sucesso" uma regra que não existe. Por isso a versão é detectada e, se for
antiga, a constraint NÃO é aplicada e o script AVISA em vez de fingir.

Idempotente e reversível.

  python migrations/qcolabore_01_classe_conta.py             # aplica
  python migrations/qcolabore_01_classe_conta.py --reverter  # desfaz
"""
import os
import re
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
COLUNA = 'classe_conta'
CONSTRAINT = 'ck_usu_cliente_sem_privilegio'
INDICE = 'ix_usu_classe'
VERSAO_MINIMA_CHECK = (8, 0, 16)


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


def _indice_existe(tabela, indice):
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
        (tabela, indice), fetch=True, fetch_one=True,
    ) or {}
    return r.get('cnt', 0) > 0


def _constraint_existe(nome):
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND CONSTRAINT_NAME = %s",
        (nome,), fetch=True, fetch_one=True,
    ) or {}
    return r.get('cnt', 0) > 0


def _versao_mysql():
    """(tupla_versao_ou_None, texto_cru). None quando não deu para interpretar."""
    texto = (execute_query("SELECT VERSION() AS v", fetch=True,
                           fetch_one=True) or {}).get('v') or ''
    m = re.match(r'(\d+)\.(\d+)\.(\d+)', texto)
    return (tuple(int(x) for x in m.groups()) if m else None), texto


def _suporta_check():
    versao, texto = _versao_mysql()
    if versao is None:
        return False, texto or '(desconhecida)'
    return versao >= VERSAO_MINIMA_CHECK, texto


def migrate_classe_conta():
    """Cria usuarios.classe_conta, o índice e o CHECK."""
    print(f'Iniciando migração: {TABELA}.{COLUNA} ...')

    # 1) A coluna. O DEFAULT 'FUNCIONARIO' É o backfill: toda linha existente
    #    já nasce classificada certo, sem um único UPDATE.
    if _coluna_existe(TABELA, COLUNA):
        print(f'✓ Coluna {COLUNA} já existe')
    else:
        _migrate(f"""
            ALTER TABLE {TABELA}
              ADD COLUMN {COLUNA} ENUM('FUNCIONARIO','CLIENTE')
                  NOT NULL DEFAULT 'FUNCIONARIO'
                  AFTER tipo_usuario
        """)
        print(f'✓ Coluna {COLUNA} criada (todas as contas existentes = FUNCIONARIO)')

    # 2) Índice — a listagem de funcionários vai filtrar por classe.
    if _indice_existe(TABELA, INDICE):
        print(f'✓ Índice {INDICE} já existe')
    else:
        _migrate(f"ALTER TABLE {TABELA} ADD KEY {INDICE} ({COLUNA})")
        print(f'✓ Índice {INDICE} criado')

    # 3) O CHECK — só se o servidor de fato o respeitar.
    if _constraint_existe(CONSTRAINT):
        print(f'✓ Constraint {CONSTRAINT} já existe')
        return True

    suporta, texto = _suporta_check()
    if not suporta:
        alvo = '.'.join(str(x) for x in VERSAO_MINIMA_CHECK)
        print()
        print(f'⚠ MySQL {texto} < {alvo} — CHECK seria PARSEADO E IGNORADO em silêncio.')
        print(f'  Constraint {CONSTRAINT} NÃO aplicada, de propósito: melhor')
        print('  ausência declarada do que uma regra que só existe no papel.')
        print('  A proteção fica no decorator da Parte 3.')
        return True

    # Antes de criar: se já houver linha violando, o ALTER falharia com erro de
    # driver. Recusa explícita com a lista é bem mais útil.
    ruins = execute_query(
        f"SELECT id, nome, tipo_usuario FROM {TABELA} "
        f"WHERE {COLUNA} = 'CLIENTE' AND tipo_usuario <> 'ASSISTENTE'",
        fetch=True) or []
    if ruins:
        print(f'  ✗ {len(ruins)} conta(s) CLIENTE com privilégio de funcionário:')
        for r in ruins:
            print(f"      id={r['id']}  {r['nome']}  tipo_usuario={r['tipo_usuario']}")
        raise RuntimeError('Corrija essas contas antes de aplicar o CHECK.')

    _migrate(f"""
        ALTER TABLE {TABELA}
          ADD CONSTRAINT {CONSTRAINT}
          CHECK ({COLUNA} = 'FUNCIONARIO' OR tipo_usuario = 'ASSISTENTE')
    """)
    print(f'✓ Constraint {CONSTRAINT} aplicada (MySQL {texto})')
    return True


def rollback_classe_conta():
    """Remove CHECK, índice e a coluna. Recusa se houver conta CLIENTE."""
    print(f'Revertendo: {TABELA}.{COLUNA} ...')

    if _coluna_existe(TABELA, COLUNA):
        n = (execute_query(
            f"SELECT COUNT(*) AS cnt FROM {TABELA} WHERE {COLUNA} = 'CLIENTE'",
            fetch=True, fetch_one=True) or {}).get('cnt', 0)
        if n:
            raise RuntimeError(
                f'{n} conta(s) CLIENTE existem — remover a coluna as transformaria '
                'silenciosamente em contas de funcionário. Reversão recusada.')

    if _constraint_existe(CONSTRAINT):
        _migrate(f'ALTER TABLE {TABELA} DROP CHECK {CONSTRAINT}')
        print(f'✓ Constraint {CONSTRAINT} removida')
    if _indice_existe(TABELA, INDICE):
        _migrate(f'ALTER TABLE {TABELA} DROP INDEX {INDICE}')
        print(f'✓ Índice {INDICE} removido')
    if _coluna_existe(TABELA, COLUNA):
        _migrate(f'ALTER TABLE {TABELA} DROP COLUMN {COLUNA}')
        print(f'✓ Coluna {COLUNA} removida')
    return True


if __name__ == '__main__':
    try:
        if '--reverter' in sys.argv:
            rollback_classe_conta()
        else:
            migrate_classe_conta()
        print('\n✓ Migração concluída com sucesso!')
    except Exception as e:
        print(f'\n✗ Migração falhou: {e}')
        sys.exit(1)
