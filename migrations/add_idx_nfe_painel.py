# -*- coding: utf-8 -*-
"""Índices de COBERTURA em nfe_importacoes para os painéis (home e Status SEFAZ).

O PROBLEMA, medido em 13/09/2026
--------------------------------
``nfe_importacoes`` tem 750 mil linhas e **7 GB, e o XML está dentro da linha**
(``xml_raw``, 97% do tamanho). A memória do MySQL é 4 GB. Logo, qualquer
consulta que precise percorrer a tabela lê 7 GB do disco — e os painéis são
feitos disso:

    SELECT COUNT(*) ... FROM nfe_importacoes WHERE origem='SEFAZ' AND tipo='entrada'
        -> 5 minutos (topo do Status SEFAZ)
    SELECT ... COUNT(*) FROM nfe_importacoes WHERE tipo='saida' GROUP BY cliente_id
        -> minutos (ranking da home)
    SELECT MAX(emit_nome), COUNT(*) ... WHERE tipo='entrada' GROUP BY emit_cnpj
        -> 744 segundos medidos (ranking de emissores da home)

Hoje o único índice que começa por ``tipo`` ou ``origem`` não existe: os que
há são PRIMARY, uk_chave_tipo(chave,tipo), idx_chave, idx_cliente_origem
(cliente_id,origem,data_emissao), idx_data, idx_dest_cnpj, idx_emit_cnpj e
idx_importado. Nenhum serve a um filtro que começa por ``tipo`` ou ``origem``.

Com um índice que CONTÉM todas as colunas que a consulta usa, o MySQL nem abre
a tabela: lê só o índice (~30 MB, cabe na memória). É a diferença entre 5
minutos e menos de 1 segundo. Não substitui o expurgo do ``xml_raw`` (etapa 2
da regra dos 3 meses) — o expurgo é o que acaba com o problema; isto é o que
faz os painéis abrirem enquanto ele não vem.

OS DOIS ÍNDICES
---------------
``idx_painel (tipo, origem, cliente_id, importado_em, incompleta, data_emissao)``
    ``tipo`` e ``origem`` primeiro porque é assim que todo painel filtra
    (``tipo='entrada'``, ``origem='SEFAZ'``); ``cliente_id`` para os GROUP BY
    por empresa; ``importado_em``, ``incompleta`` e ``data_emissao`` porque
    são as colunas que o topo, a aba Empresas e a aba Baixadas do Status
    SEFAZ somam/comparam — com elas dentro do índice, a consulta é index-only.

``idx_tipo_emit (tipo, emit_cnpj)``
    Para o ranking de emissores da home (``WHERE tipo='entrada' GROUP BY
    emit_cnpj``). O ``MAX(emit_nome)`` ainda busca a linha, mas só das 34 mil
    notas de entrada, não das 750 mil.

CUSTO
-----
Uma passada pela tabela de 7 GB para construir os dois (um ALTER só, para
não ler a tabela duas vezes). Ontem um índice de 3 colunas levou 304s com
LOCK=NONE: leitura e escrita seguem, o site fica mais lento enquanto roda.
Espaço: ~60 MB. Rollback: DROP INDEX dos dois.

    python migrations/add_idx_nfe_painel.py            # dry-run
    python migrations/add_idx_nfe_painel.py --apply

Idempotente: cria só o que faltar.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELA = 'nfe_importacoes'
INDICES = {
    'idx_painel': '(tipo, origem, cliente_id, importado_em, incompleta, data_emissao)',
    'idx_tipo_emit': '(tipo, emit_cnpj)',
}
# As consultas que justificam a migração — o mesmo EXPLAIN antes e depois.
PROVAS = [
    ("topo do Status SEFAZ",
     f"SELECT COUNT(*) t, SUM(COALESCE(incompleta,0)=1) r FROM {TABELA} "
     f" WHERE origem='SEFAZ' AND tipo='entrada'"),
    ("ranking de saídas da home",
     f"SELECT cliente_id, COUNT(*) n FROM {TABELA} WHERE tipo='saida' "
     f" GROUP BY cliente_id ORDER BY n DESC LIMIT 15"),
    ("ranking de emissores da home",
     f"SELECT MAX(emit_nome) nome, COUNT(*) n FROM {TABELA} "
     f" WHERE tipo='entrada' AND COALESCE(emit_cnpj,'')<>'' "
     f" GROUP BY emit_cnpj ORDER BY n DESC LIMIT 15"),
]


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


def _explain(rotulo, sql):
    print(f'   {rotulo}:')
    for r in execute_query('EXPLAIN ' + sql, fetch=True) or []:
        print(f"      type={r.get('type')}  key={r.get('key')}  "
              f"rows={r.get('rows')}  extra={r.get('Extra')}")


def main():
    ap = argparse.ArgumentParser(description='Índices de cobertura dos painéis.')
    ap.add_argument('--apply', action='store_true',
                    help='executa; sem isto é só dry-run')
    ap.add_argument('--medir', action='store_true',
                    help='depois de criar, roda as consultas de prova e cronometra')
    args = ap.parse_args()

    tem = indices()
    if not tem:
        print(f'ERRO: tabela {TABELA} não encontrada.')
        return 1
    linhas, mb = _tamanho()
    print(f'{TABELA}: ~{linhas} linhas, {mb} MB')
    print(f'índices hoje: {", ".join(sorted(tem))}')
    print()

    faltam = {k: v for k, v in INDICES.items() if k not in tem}
    if not faltam:
        print('os dois índices já existem — nada a fazer.')
        return 0

    sql = (f'ALTER TABLE {TABELA} '
           + ', '.join(f'ADD INDEX {k} {v}' for k, v in faltam.items())
           + ', ALGORITHM=INPLACE, LOCK=NONE')

    print('ANTES — como o MySQL resolve hoje (EXPLAIN, não executa):')
    for rotulo, q in PROVAS:
        _explain(rotulo, q)
    print()

    if not args.apply:
        print('SQL QUE SERIA EXECUTADO:')
        print('   ' + sql + ';')
        print()
        print('ROLLBACK, se precisar:')
        for k in faltam:
            print(f'   ALTER TABLE {TABELA} DROP INDEX {k};')
        print()
        print('DRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        return 0

    print('EXECUTANDO (minutos; leitura e escrita seguem funcionando):')
    print('   ' + sql)
    t0 = time.time()
    r = execute_query(sql, fetch=False)
    dur = time.time() - t0
    if r is None:
        print(f'FALHOU depois de {dur:.0f}s. Nenhum índice nasceu; nada mais mudou.')
        return 1
    print(f'OK em {dur:.0f}s.')
    print()

    tem = indices()
    if any(k not in tem for k in faltam):
        print('ATENÇÃO: o comando voltou sem erro mas algum índice não aparece.')
        return 1

    print('DEPOIS — as mesmas consultas, agora:')
    for rotulo, q in PROVAS:
        _explain(rotulo, q)
        if args.medir:
            t0 = time.time()
            execute_query(q, fetch=True)
            print(f'      tempo real: {time.time() - t0:.2f}s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
