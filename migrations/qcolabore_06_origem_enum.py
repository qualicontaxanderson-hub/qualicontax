# -*- coding: utf-8 -*-
"""
Q-COLABORE — migration 06: acrescenta 'Q-COLABORE' ao ENUM ``origem`` de
``nfe_importacoes`` e ``cte_documentos``.

Por que
-------
O roteador passou a lançar o que recolhe da _ENTRADA e vinha gravando
origem='DROPBOX' (o ENUM não tinha valor melhor). A procedência real é o
Q-Colabore — a caixa por onde o cliente entrega documento. O branding vira o
valor gravado.

O histórico NÃO é reescrito: as 759 linhas 'DROPBOX' continuam DROPBOX, porque
foi isso que aconteceu. Só o que nascer daqui em diante é Q-COLABORE.

POR QUE O VALOR VAI NO FIM DA LISTA
-----------------------------------
Um ENUM é armazenado como o ÍNDICE do valor, não como o texto. Acrescentar no
fim não mexe em índice nenhum dos valores existentes: é alteração de metadado,
instantânea, sem tocar nas 41 mil linhas. Inserir no meio renumeraria tudo a
partir dali e forçaria a reescrita da tabela inteira — e, pior, mudaria o
significado das linhas já gravadas.

Isso não é teoria: foi medido contra este mesmo servidor (MySQL 9.4.0) antes de
escrever esta migration, numa tabela de rascunho com o ENUM idêntico:

    APPEND no FIM     + ALGORITHM=INSTANT ......... aceito
    INSERIR NO MEIO   + ALGORITHM=INSTANT ......... recusado, 1846:
        "Need to rebuild the table to change column type."

Por isso o ALTER daqui exige ``ALGORITHM=INSTANT`` explicitamente. Se um dia
alguém editar este arquivo e puser o valor fora do fim, o servidor RECUSA em
vez de reescrever a tabela caladamente. A exigência é a rede de proteção.

Idempotente e reversível.

  python migrations/qcolabore_06_origem_enum.py             # aplica
  python migrations/qcolabore_06_origem_enum.py --reverter  # tira do ENUM
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

TABELAS = ('nfe_importacoes', 'cte_documentos')
COLUNA = 'origem'
NOVO = 'Q-COLABORE'


def _migrate(sql):
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {sql[:200]}')


def _coluna(tabela):
    """(column_type, is_nullable, default) da coluna origem — ou None."""
    r = execute_query(
        "SELECT COLUMN_TYPE t, IS_NULLABLE n, COLUMN_DEFAULT d "
        "  FROM INFORMATION_SCHEMA.COLUMNS "
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (tabela, COLUNA), fetch=True, fetch_one=True)
    return r


def _valores(column_type):
    """['UPLOAD','DROPBOX',...] a partir de "enum('UPLOAD','DROPBOX',...)"."""
    return re.findall(r"'((?:[^']|'')*)'", column_type or '')


def _definicao(vals, info):
    """Remonta o DDL da coluna preservando NOT NULL e DEFAULT como estão.

    Lê os dois de INFORMATION_SCHEMA em vez de hardcodar: nfe_importacoes tem
    DEFAULT 'UPLOAD' e cte_documentos tem DEFAULT 'SEFAZ'. Escrever um valor
    fixo aqui trocaria o default de uma das duas sem ninguém perceber.
    """
    lista = ','.join("'" + v.replace("'", "''") + "'" for v in vals)
    ddl = f"ENUM({lista})"
    ddl += ' NULL' if info['n'] == 'YES' else ' NOT NULL'
    if info['d'] is not None:
        ddl += " DEFAULT '" + str(info['d']).replace("'", "''") + "'"
    return ddl


def _aplicar(tabela, vals, info):
    ddl = _definicao(vals, info)
    # ALGORITHM=INSTANT: o servidor RECUSA se isso exigir rebuild. Ver o
    # cabeçalho — a exigência é de propósito.
    _migrate(f"ALTER TABLE {tabela} MODIFY COLUMN {COLUNA} {ddl}, ALGORITHM=INSTANT")


def migrate_origem_enum():
    """Acrescenta 'Q-COLABORE' no FIM do ENUM das duas tabelas (idempotente)."""
    for tabela in TABELAS:
        print(f'Iniciando migração: {tabela}.{COLUNA} ...')
        info = _coluna(tabela)
        if not info:
            raise RuntimeError(f'{tabela}.{COLUNA} não existe.')
        vals = _valores(info['t'])
        if not vals:
            raise RuntimeError(f'{tabela}.{COLUNA} não é ENUM: {info["t"]}')
        if NOVO in vals:
            print(f"✓ '{NOVO}' já está no ENUM de {tabela}")
            continue
        n = (execute_query(f'SELECT COUNT(*) AS cnt FROM {tabela}',
                           fetch=True, fetch_one=True) or {}).get('cnt', 0)
        print(f'  {n} linha(s) na tabela — o append no fim não toca em nenhuma.')
        print(f'  antes : {info["t"]}')
        _aplicar(tabela, vals + [NOVO], info)
        print(f'  depois: {_coluna(tabela)["t"]}')
        print(f"✓ '{NOVO}' acrescentado ao ENUM de {tabela}")
    return True


def rollback_origem_enum():
    """Tira 'Q-COLABORE' do ENUM. Recusa se alguma linha já usa o valor."""
    for tabela in TABELAS:
        print(f'Revertendo: {tabela}.{COLUNA} ...')
        info = _coluna(tabela)
        if not info:
            print(f'  {tabela}.{COLUNA} não existe — nada a fazer')
            continue
        vals = _valores(info['t'])
        if NOVO not in vals:
            print(f"✓ '{NOVO}' já não está no ENUM de {tabela}")
            continue
        n = (execute_query(f'SELECT COUNT(*) AS cnt FROM {tabela} WHERE {COLUNA} = %s',
                           (NOVO,), fetch=True, fetch_one=True) or {}).get('cnt', 0)
        if n:
            # Tirar o valor do ENUM com linhas usando-o transformaria cada uma
            # numa string vazia — perda silenciosa de procedência.
            raise RuntimeError(
                f"{n} linha(s) de {tabela} já usam origem='{NOVO}'. Reversão "
                'recusada: elas virariam string vazia. Repontar antes.')
        # Remover é ALTER de verdade (o valor sai do fim, sem renumerar o resto,
        # então segue instantâneo).
        _aplicar(tabela, [v for v in vals if v != NOVO], info)
        print(f"✓ '{NOVO}' removido do ENUM de {tabela}")
    return True


if __name__ == '__main__':
    try:
        if '--reverter' in sys.argv:
            rollback_origem_enum()
        else:
            migrate_origem_enum()
        print('\n✓ Migração concluída com sucesso!')
    except Exception as e:
        print(f'\n✗ Migração falhou: {e}')
        sys.exit(1)
