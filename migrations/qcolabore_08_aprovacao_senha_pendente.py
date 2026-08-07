# -*- coding: utf-8 -*-
"""
Q-COLABORE — migration 08: aprovação de candidatura vira usuário SEM senha.

O que muda em ``usuarios``
--------------------------
1) senha_pendente TINYINT(1) NOT NULL DEFAULT 0
   A conta nasce da aprovação já ATIVA, mas sem senha: o admin não define senha
   por ninguém. Enquanto ``senha_pendente=1``, o login recusa com "falta definir
   sua senha" em vez de "senha incorreta" — a Parte 6 (link de senha) zera a
   flag quando a pessoa criar a própria senha.

2) senha_hash passa a aceitar NULL.
   Hoje é NOT NULL, o que obrigaria a inventar um hash falso para o aprovado sem
   senha. NULL diz a verdade — "não há senha ainda" — e o gate de login é a flag
   acima (nunca se chama verify_password com hash NULL). Alargar NOT NULL→NULL não
   quebra INSERT algum existente (todos continuam informando um valor).

Status/decisão da pendência (status, decidido_por, decidido_em, decisao_motivo,
usuario_id) já existem desde a migration 03 — nada a criar lá.

Idempotente (checa information_schema antes de cada passo) e reversível.

  python migrations/qcolabore_08_aprovacao_senha_pendente.py             # aplica
  python migrations/qcolabore_08_aprovacao_senha_pendente.py --reverter  # desfaz
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


def _migrate(sql):
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {sql[:180]}')


def _coluna(nome):
    """Devolve a linha de information_schema da coluna, ou None."""
    return execute_query(
        "SELECT COLUMN_NAME, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABELA, nome), fetch=True, fetch_one=True)


def migrate():
    print('Iniciando migração 08: senha_pendente + senha_hash nullable ...')

    if _coluna('senha_pendente'):
        print('• usuarios.senha_pendente já existe — mantida')
    else:
        _migrate("ALTER TABLE usuarios ADD COLUMN senha_pendente TINYINT(1) "
                 "NOT NULL DEFAULT 0 "
                 "COMMENT 'aprovado sem senha; bloqueia login até a Parte 6 definir'")
        print('✓ usuarios.senha_pendente adicionada')

    sh = _coluna('senha_hash')
    if sh and sh.get('IS_NULLABLE') == 'YES':
        print('• usuarios.senha_hash já aceita NULL — mantida')
    else:
        _migrate('ALTER TABLE usuarios MODIFY COLUMN senha_hash VARCHAR(255) NULL')
        print('✓ usuarios.senha_hash agora aceita NULL')

    return True


def rollback():
    """Desfaz a 08. Só derruba senha_pendente; senha_hash fica nullable.

    Voltar senha_hash a NOT NULL só é seguro se nenhuma conta estiver sem senha
    (senão o ALTER falha) — e um NULL a mais numa coluna alargada não faz mal.
    Então o rollback avisa e não força.
    """
    print('Revertendo migração 08 ...')
    if _coluna('senha_pendente'):
        n = (execute_query(
            "SELECT COUNT(*) AS c FROM usuarios WHERE senha_pendente = 1",
            fetch=True, fetch_one=True) or {}).get('c', 0)
        if n:
            print(f'  ATENÇÃO: {n} conta(s) aguardando definição de senha perderão a flag.')
        _migrate('ALTER TABLE usuarios DROP COLUMN senha_pendente')
        print('✓ usuarios.senha_pendente removida')
    else:
        print('✓ usuarios.senha_pendente já não existe')

    nulos = (execute_query(
        "SELECT COUNT(*) AS c FROM usuarios WHERE senha_hash IS NULL",
        fetch=True, fetch_one=True) or {}).get('c', 0)
    if nulos:
        print(f'  senha_hash mantida NULLABLE: há {nulos} conta(s) com senha_hash NULL '
              '(reverter a NOT NULL falharia). Defina senha nelas antes, se quiser estreitar.')
    else:
        print('  senha_hash deixada NULLABLE (inofensivo). Estreite manualmente se desejar.')
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
