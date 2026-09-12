# -*- coding: utf-8 -*-
"""Índice de (cliente_id, origem, data_emissao) em nfe_importacoes.

POR QUE ESTA MIGRAÇÃO EXISTE
---------------------------
``nfe_importacoes`` é a maior tabela do banco — **2,9 GB, 77% do total** — e
**não tem índice em ``cliente_id``**. Os que existem são PRIMARY(id),
uk_chave_tipo(chave,tipo), idx_chave, idx_emit_cnpj, idx_data, idx_dest_cnpj e
idx_importado.

Consequência medida em 12/09/2026:

    EXPLAIN SELECT COUNT(*), MAX(data_emissao) FROM nfe_importacoes
     WHERE cliente_id = 37 AND origem = 'Q-ROBO'
    -> type=ALL   key=None   rows=206.408   **17,58 segundos**

Isso é varredura completa dos 2,9 GB. **Toda** consulta de notas por empresa
paga esse preço — as telas do Fiscal inclusive —, e o preço cresce a cada robô
instalado (são 14, com 200 a instalar).

O GATILHO IMEDIATO
------------------
``utils/robo_fase.situacao()`` faz exatamente essa contagem para decidir se o
robô pode mandar agora, e roda **uma vez por ciclo de cada robô**. Sem este
índice, 214 robôs produziriam varreduras contínuas de 17s — eu teria criado
algo muito pior do que o problema que consertei hoje. Por isso o índice vem
ANTES da feature.

A ORDEM DAS COLUNAS NÃO É ARBITRÁRIA
------------------------------------
``(cliente_id, origem, data_emissao)``:

* **cliente_id** primeiro porque é o filtro que TODA consulta usa — e sozinho
  ele já corta de 293 mil para as notas de uma empresa;
* **origem** em seguida porque separa Q-ROBO de SEFAZ/DROPBOX/Q-COLABORE, que é
  o segundo filtro mais comum (e o do arquivador);
* **data_emissao** por último porque assim o ``MAX(data_emissao)`` da fase sai
  do próprio índice, sem tocar na tabela. Um índice só de ``cliente_id``
  resolveria a contagem e ainda faria o MAX ler linha por linha.

O CUSTO, e ele é real
---------------------
ALTER TABLE numa tabela de 2,9 GB com ~400 inserções por minuto entrando. O
InnoDB constrói índice secundário com ALGORITHM=INPLACE: leitura e escrita
seguem funcionando (não é lock de tabela), mas leva minutos e consome disco
temporário. Não há meio-caminho: se falhar, o índice não nasce e nada mais
muda.

    python migrations/add_idx_nfe_cliente_origem.py            # dry-run
    python migrations/add_idx_nfe_cliente_origem.py --apply

Idempotente: confere INFORMATION_SCHEMA antes e não faz nada se já existe.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELA = 'nfe_importacoes'
INDICE = 'idx_cliente_origem'
COLUNAS = '(cliente_id, origem, data_emissao)'


def indices():
    r = execute_query(
        'SELECT DISTINCT INDEX_NAME i FROM INFORMATION_SCHEMA.STATISTICS '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', (TABELA,),
        fetch=True) or []
    return {x['i'] for x in r}


def _tamanho():
    r = execute_query(
        'SELECT table_rows linhas, ROUND((data_length+index_length)/1024/1024) mb '
        '  FROM INFORMATION_SCHEMA.TABLES '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', (TABELA,),
        fetch=True, fetch_one=True) or {}
    return r.get('linhas'), r.get('mb')


def main():
    ap = argparse.ArgumentParser(description='Indice de cliente_id em nfe_importacoes.')
    ap.add_argument('--apply', action='store_true',
                    help='executa; sem isto é só dry-run')
    args = ap.parse_args()

    tem = indices()
    if not tem:
        print(f'ERRO: tabela {TABELA} não encontrada.')
        return 1
    linhas, mb = _tamanho()
    print(f'{TABELA}: ~{linhas} linhas, {mb} MB')
    print(f'índices hoje: {", ".join(sorted(tem))}')
    print()

    if INDICE in tem:
        print(f'{INDICE} já existe — nada a fazer.')
        return 0

    sql = (f'ALTER TABLE {TABELA} ADD INDEX {INDICE} {COLUNAS}, '
           f'ALGORITHM=INPLACE, LOCK=NONE')

    # A prova do antes: o mesmo EXPLAIN que justificou a migração. Rodar de
    # novo aqui é o que permite comparar depois — número prometido sem medida
    # é promessa, não resultado.
    print('ANTES — como o MySQL resolve a consulta da fase hoje:')
    for r in execute_query(
            f"EXPLAIN SELECT COUNT(*), MAX(data_emissao) FROM {TABELA} "
            f" WHERE cliente_id = 1 AND origem = 'Q-ROBO'", fetch=True) or []:
        print(f"   type={r.get('type')}  key={r.get('key')}  "
              f"rows={r.get('rows')}  extra={r.get('Extra')}")
    print()

    if not args.apply:
        print('SQL QUE SERIA EXECUTADO:')
        print('   ' + sql + ';')
        print()
        print('LOCK=NONE pede ao InnoDB para NÃO travar a tabela: leitura e')
        print('escrita seguem durante a construção. Se o servidor não puder')
        print('atender isso, ele RECUSA o comando em vez de travar calado —')
        print('e é justamente por isso que o LOCK vai explícito.')
        print()
        print('ROLLBACK, se precisar:')
        print(f'   ALTER TABLE {TABELA} DROP INDEX {INDICE};')
        print()
        print('DRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        return 0

    print('EXECUTANDO (pode levar minutos):')
    print('   ' + sql)
    t0 = time.time()
    r = execute_query(sql, fetch=False)
    dur = time.time() - t0
    if r is None:
        print(f'FALHOU depois de {dur:.0f}s. O índice não nasceu; '
              'nada mais mudou.')
        return 1
    print(f'OK em {dur:.0f}s.')
    print()

    if INDICE not in indices():
        print('ATENÇÃO: o comando voltou sem erro mas o índice não aparece.')
        return 1

    print('DEPOIS — a mesma consulta, agora:')
    for r in execute_query(
            f"EXPLAIN SELECT COUNT(*), MAX(data_emissao) FROM {TABELA} "
            f" WHERE cliente_id = 1 AND origem = 'Q-ROBO'", fetch=True) or []:
        print(f"   type={r.get('type')}  key={r.get('key')}  "
              f"rows={r.get('rows')}  extra={r.get('Extra')}")
    t0 = time.time()
    execute_query(f"SELECT COUNT(*) n, MAX(data_emissao) u FROM {TABELA} "
                  f" WHERE cliente_id = 1 AND origem = 'Q-ROBO'",
                  fetch=True, fetch_one=True)
    print(f'   tempo real da consulta: {time.time() - t0:.2f}s '
          '(era 17,58s antes do índice)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
