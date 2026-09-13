# -*- coding: utf-8 -*-
"""``app_config.valor``: TEXT (64 KB) -> MEDIUMTEXT (16 MB).

POR QUE
-------
``app_config`` virou o lugar onde o cron guarda os painéis calculados fora do
site (``utils/painel_cache.py``): a home do Fiscal (5 KB), o Q-Robô (1 KB) e,
desde 13/09/2026, o Status SEFAZ inteiro. Esse último tem 191 empresas, 3.155
linhas de Baixadas e 3.000 eventos de histórico: **1,41 MB** de JSON. O
primeiro cálculo terminou e falhou na gravação:

    1406 (22001): Data too long for column 'valor' at row 1

TEXT guarda 65.535 bytes. MEDIUMTEXT guarda 16 MB — sobra para o painel
crescer com os 200 robôs sem voltar aqui.

CUSTO
-----
Nenhum que se perceba: a tabela tem 4 linhas. ALGORITHM=INPLACE, LOCK=NONE
como nas outras, por disciplina.

    python migrations/alter_app_config_valor_mediumtext.py            # dry-run
    python migrations/alter_app_config_valor_mediumtext.py --apply

Idempotente: lê o tipo atual e não faz nada se já é MEDIUMTEXT/LONGTEXT.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELA = 'app_config'
COLUNA = 'valor'


def tipo_atual():
    r = execute_query(
        'SELECT DATA_TYPE t FROM INFORMATION_SCHEMA.COLUMNS '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s',
        (TABELA, COLUNA), fetch=True, fetch_one=True) or {}
    return (r.get('t') or '').lower()


def main():
    ap = argparse.ArgumentParser(description='app_config.valor -> MEDIUMTEXT.')
    ap.add_argument('--apply', action='store_true',
                    help='executa; sem isto é só dry-run')
    args = ap.parse_args()

    t = tipo_atual()
    if not t:
        print(f'ERRO: coluna {TABELA}.{COLUNA} não encontrada.')
        return 1
    n = (execute_query(f'SELECT COUNT(*) n FROM {TABELA}', fetch=True,
                       fetch_one=True) or {}).get('n')
    print(f'{TABELA}.{COLUNA}: tipo hoje = {t} ({n} linhas na tabela)')

    if t in ('mediumtext', 'longtext'):
        print('já comporta o painel — nada a fazer.')
        return 0

    sql = (f'ALTER TABLE {TABELA} MODIFY {COLUNA} MEDIUMTEXT NOT NULL, '
           f'ALGORITHM=INPLACE, LOCK=NONE')

    if not args.apply:
        print()
        print('SQL QUE SERIA EXECUTADO:')
        print('   ' + sql + ';')
        print()
        print('ROLLBACK, se precisar (só cabe se nenhum valor passar de 64 KB):')
        print(f'   ALTER TABLE {TABELA} MODIFY {COLUNA} TEXT NOT NULL;')
        print()
        print('DRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        return 0

    print('EXECUTANDO:')
    print('   ' + sql)
    t0 = time.time()
    r = execute_query(sql, fetch=False)
    dur = time.time() - t0
    if r is None:
        print(f'FALHOU depois de {dur:.1f}s. Nada mudou.')
        return 1
    print(f'OK em {dur:.1f}s. tipo agora = {tipo_atual()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
