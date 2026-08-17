# -*- coding: utf-8 -*-
"""Adiciona nfse_eventos.divergencia — o sinal que o parser calculava e jogava fora.

O QUE ERA
---------
``evento_para_registro()`` faz uma conferência cruzada: o tipo do evento vem do
ENVELOPE do ADN (campo ``TipoEvento``) e o nome do elemento dentro do XML
(``e101101`` e afins) serve de confirmação. Quando os dois discordam, o parser
monta uma string explicando a divergência — e desde 17/08/2026 também compara a
chave do envelope com o ``chNFSe`` do XML.

Só que a coluna nunca existiu. O parser produzia ``reg['divergencia']`` e o
``SQL_EVT_UPSERT`` não a listava, então o valor morria ali. Duas fontes
concordando é confirmação; discordando é sinal de que o leiaute mudou debaixo de
nós — exatamente o tipo de aviso que não pode ser descartado em silêncio.

COMO APARECEU
-------------
Pelo pior caminho possível. A rota ``/conf-nfse/api/detalhe/<id>`` fazia SELECT
dessa coluna inexistente; ``execute_query`` devolve ``None`` em erro e o ``or []``
do chamador transformava a falha em "esta nota não tem evento". O painel de
detalhe mostrava "Nenhum evento" para TODA nota, inclusive cancelada, com HTTP
200. Só apareceu quando o Flask foi instalado na máquina e os endpoints puderam
ser exercitados de verdade.

NÃO PRECISA DE RECAPTURA. Nenhum dos 32 eventos capturados tem divergência (nem
um ``revisar=1``), então a coluna nasce vazia e correta. Daqui para frente ela é
preenchida na captura normal.

    python migrations/add_nfse_evento_divergencia.py            # dry-run
    python migrations/add_nfse_evento_divergencia.py --apply
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELA = 'nfse_eventos'
COLUNA = 'divergencia'
DEF = ("VARCHAR(255) NULL COMMENT "
       "'envelope x XML discordaram: tipo ou chave. NULL = conferiram'")


def tem_coluna():
    r = execute_query(
        'SELECT COLUMN_NAME c FROM INFORMATION_SCHEMA.COLUMNS '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s',
        (TABELA, COLUNA), fetch=True, fetch_one=True)
    return bool(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true', help='executa; sem isto é dry-run')
    args = ap.parse_args()

    existe = execute_query(
        'SELECT COUNT(*) n FROM INFORMATION_SCHEMA.TABLES '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', (TABELA,),
        fetch=True, fetch_one=True)
    if not existe or not existe['n']:
        print(f'ERRO: tabela {TABELA} não encontrada.')
        return 1

    if tem_coluna():
        print(f'{TABELA}.{COLUNA} já existe. Nada a fazer.')
        return 0

    sql = f'ALTER TABLE {TABELA} ADD COLUMN {COLUNA} {DEF}'
    print('A EXECUTAR:')
    print('   ' + sql)
    print()
    if not args.apply:
        print('DRY-RUN. Nada foi alterado. Repita com --apply.')
        print()
        print('ROLLBACK:')
        print(f'   ALTER TABLE {TABELA} DROP COLUMN {COLUNA};')
        return 0

    if execute_query(sql, fetch=False) is None:
        print('FALHOU. Nada foi alterado.')
        return 1
    if not tem_coluna():
        print('FALHOU: o ALTER passou mas a coluna não apareceu.')
        return 1
    print(f'OK: {TABELA}.{COLUNA} criada.')
    print('Nenhuma recaptura necessária — nasce vazia e é preenchida na captura.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
