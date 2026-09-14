# -*- coding: utf-8 -*-
"""``nfe_importacoes.hora_emissao`` (TIME) — a hora da nota fora do XML.

POR QUE
-------
``data_emissao`` é DATE. A hora só existia dentro do ``xml_raw`` (``<dhEmi>``),
e as telas de Saídas a extraíam com SUBSTRING no XML. A etapa 2 da regra
"documento no Dropbox, dado no banco" (13/09/2026) esvazia o ``xml_raw`` das
notas com mais de 3 meses de emissão — e a hora iria junto. Esta coluna a
guarda: o expurgo a preenche na MESMA instrução que apaga o XML
(``utils/expurgo_xml.py``), e as telas leem ``COALESCE(hora_emissao, <XML>)``.

CUSTO
-----
``ADD COLUMN`` ao final da tabela é INSTANT no MySQL 8 (só metadados, não
reescreve os 9 GB). Se o servidor recusar INSTANT, cai para INPLACE (minutos,
sem travar). Nunca preenche nada aqui: fica NULL até o expurgo passar.

    python migrations/add_hora_emissao.py            # dry-run
    python migrations/add_hora_emissao.py --apply
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELA = 'nfe_importacoes'
COLUNA = 'hora_emissao'


def existe():
    r = execute_query(
        'SELECT COUNT(*) n FROM INFORMATION_SCHEMA.COLUMNS '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s',
        (TABELA, COLUNA), fetch=True, fetch_one=True) or {}
    return int(r.get('n') or 0) > 0


def main():
    ap = argparse.ArgumentParser(description='hora_emissao em nfe_importacoes.')
    ap.add_argument('--apply', action='store_true', help='executa; sem isto é só dry-run')
    args = ap.parse_args()

    if existe():
        print(f'{TABELA}.{COLUNA} já existe — nada a fazer.')
        return 0
    sqls = [f'ALTER TABLE {TABELA} ADD COLUMN {COLUNA} TIME NULL, ALGORITHM=INSTANT',
            f'ALTER TABLE {TABELA} ADD COLUMN {COLUNA} TIME NULL, ALGORITHM=INPLACE, LOCK=NONE']
    if not args.apply:
        print('SQL QUE SERIA EXECUTADO (o segundo só se o primeiro for recusado):')
        for s in sqls:
            print('   ' + s + ';')
        print()
        print('ROLLBACK, se precisar:')
        print(f'   ALTER TABLE {TABELA} DROP COLUMN {COLUNA};')
        print()
        print('DRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        return 0
    for s in sqls:
        print('EXECUTANDO: ' + s)
        t0 = time.time()
        r = execute_query(s, fetch=False)
        if r is not None:
            print(f'OK em {time.time() - t0:.1f}s.')
            break
        print('   recusado; tentando a forma seguinte.')
    if not existe():
        print('FALHOU: a coluna não existe. Nada mais mudou.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
