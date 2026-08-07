# -*- coding: utf-8 -*-
"""
Q-COLABORE — migration 07: ``url_claro`` no link + contatos corporativos e
modalidade de trabalho na candidatura.

O que muda
----------
1) cadastro_link.url_claro VARCHAR(255) NULL
   Exceção CONTROLADA à regra "nunca token em claro" (migrations 02/06). O admin
   precisa reenviar o convite a quem o perdeu SEM gerar outro link — então a URL
   completa fica gravada ENQUANTO o link está pendente e é apagada (SET NULL) no
   uso, na revogação ou quando a expiração é percebida. Vive no máximo 72h e
   morre junto com o link. O token continua NÃO recuperável por hash; url_claro é
   uma cópia operacional de vida curta, não o cofre.

2) cadastro_pendente:
     email_corporativo   VARCHAR(120) NULL
     celular_corporativo VARCHAR(20)  NULL
     modalidade_trabalho ENUM('HOME_OFFICE','ESCRITORIO','HIBRIDO') NULL
   Os dois primeiros já foram criados pela migration 03 (como VARCHAR(255)/(20));
   por isso o ADD é idempotente e os PULA quando existem — a migration 07 não
   reescreve coluna alheia. modalidade_trabalho é a única realmente nova aqui.
   Tudo NULL: pendências antigas não têm o dado e não se inventa valor.

Idempotente (checa information_schema antes de cada ADD) e reversível.

  python migrations/qcolabore_07_link_claro_e_modalidade.py             # aplica
  python migrations/qcolabore_07_link_claro_e_modalidade.py --reverter  # desfaz
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


def _add_coluna(tabela, coluna, definicao):
    """ADD COLUMN idempotente: só executa se a coluna ainda não existe."""
    if _coluna_existe(tabela, coluna):
        print(f'• {tabela}.{coluna} já existe — mantida como está')
        return
    _migrate(f'ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}')
    print(f'✓ {tabela}.{coluna} adicionada')


def _drop_coluna(tabela, coluna):
    if not _coluna_existe(tabela, coluna):
        print(f'✓ {tabela}.{coluna} já não existe')
        return
    _migrate(f'ALTER TABLE {tabela} DROP COLUMN {coluna}')
    print(f'✓ {tabela}.{coluna} removida')


def migrate():
    print('Iniciando migração 07: url_claro + modalidade de trabalho ...')

    _add_coluna(
        'cadastro_link', 'url_claro',
        "VARCHAR(255) NULL DEFAULT NULL "
        "COMMENT 'URL completa; preenchida só enquanto PENDENTE, apagada no "
        "uso/revogação/expiração'")

    # email/celular corporativos: criados pela migration 03. O ADD abaixo os
    # pula por idempotência — está aqui só para a migration ser completa se algum
    # ambiente antigo nunca tiver tido essas colunas.
    _add_coluna('cadastro_pendente', 'email_corporativo', 'VARCHAR(120) NULL')
    _add_coluna('cadastro_pendente', 'celular_corporativo', 'VARCHAR(20) NULL')
    _add_coluna(
        'cadastro_pendente', 'modalidade_trabalho',
        "ENUM('HOME_OFFICE','ESCRITORIO','HIBRIDO') NULL")

    return True


def rollback():
    """Desfaz SÓ o que a 07 criou.

    email_corporativo/celular_corporativo NÃO são removidos: pertencem à migration
    03 e já existiam antes da 07. Reverter a 07 não pode apagar coluna de outra
    migration — para removê-los, reverta a 03.
    """
    print('Revertendo migração 07 ...')
    _drop_coluna('cadastro_link', 'url_claro')
    _drop_coluna('cadastro_pendente', 'modalidade_trabalho')
    print('  (email_corporativo/celular_corporativo preservados — são da migration 03)')
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
