# -*- coding: utf-8 -*-
"""Preenche a natureza jurídica (tipo jurídico) de toda empresa pela Receita.

Fase A do tipo jurídico (14/10/2026). Para cada cadastro PJ (cliente, avulso,
ativo ou não) consulta a Receita (BrasilAPI; ReceitaWS se ela falhar) e grava
código + descrição em clientes.natureza_codigo / natureza_juridica.

    python scripts/natureza_popular.py --cache n.json          # dry-run: consulta e conta, não grava
    python scripts/natureza_popular.py --cache n.json --apply  # grava (reaproveita o cache)
"""
import argparse
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('MIGRATIONS_DONE', '1')

from utils.db_helper import execute_query          # noqa: E402
from utils import cnpj_receita as RF                # noqa: E402
from utils.natureza import classificar, FAMILIAS    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true', help='grava (sem isso é dry-run)')
    ap.add_argument('--cache', help='JSON das consultas ({cliente_id: {ok, cod, desc}}); lido e atualizado')
    ap.add_argument('--pausa', type=float, default=1.3, help='segundos entre consultas à Receita')
    args = ap.parse_args()
    cache = {}
    if args.cache and os.path.exists(args.cache):
        cache = json.load(open(args.cache, encoding='utf-8'))

    emp = execute_query(
        """SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, situacao, COALESCE(avulso, 0) AS avulso
             FROM clientes WHERE tipo_pessoa = 'PJ' ORDER BY id""", fetch=True)
    if not emp:            # None = banco não respondeu; lista vazia aqui também é erro
        print('ERRO: não consegui ler os cadastros do banco — nada foi feito.', flush=True)
        return 1
    print(f'cadastros PJ: {len(emp)}', flush=True)
    fam, nome, falhas, sa = Counter(), Counter(), [], []
    gravados = 0
    for i, e in enumerate(emp, 1):
        v = cache.get(str(e['id']))
        if not v or v.get('ok') is not True:
            try:
                d, _ = RF.consultar(e['cpf_cnpj'])
                v = {'ok': True, 'cod': d.get('natureza_juridica_codigo') or '', 'desc': d.get('natureza_juridica') or ''}
            except RF.CnpjNaoEncontrado:
                v = {'ok': 'nao_existe'}
            except Exception as ex:                 # fonte fora do ar: fica para a próxima rodada
                v = {'ok': False, 'erro': str(ex)[:120]}
            cache[str(e['id'])] = v
            if args.cache and i % 20 == 0:
                json.dump(cache, open(args.cache, 'w', encoding='utf-8'))
            time.sleep(args.pausa)
        if v.get('ok') is not True or not v.get('cod'):
            falhas.append((e['numero_cliente'] or 'avulso', e['nome_razao_social'], v.get('ok'), v.get('erro', '')))
            continue
        rot, f = classificar(v['cod'], v['desc'])
        fam[f] += 1
        nome[f"{v['cod']} {v['desc']}"] += 1
        if f == 'sa':
            sa.append(f"{e['numero_cliente'] or 'avulso'} · {e['nome_razao_social']} · {rot}"
                      f"{'' if e['situacao'] == 'ATIVO' else ' (' + e['situacao'] + ')'}")
        if args.apply:
            r = execute_query(
                "UPDATE clientes SET natureza_codigo = %s, natureza_juridica = %s, natureza_em = NOW() WHERE id = %s",
                (v['cod'][:4], (v['desc'] or '')[:120], e['id']))
            gravados += 1 if r is not None else 0
        if i % 25 == 0:
            print(f'  {i}/{len(emp)}', flush=True)
    if args.cache:
        json.dump(cache, open(args.cache, 'w', encoding='utf-8'))

    rot_fam = dict(FAMILIAS)
    print('\nPOR FAMÍLIA:')
    for f, n in fam.most_common():
        print(f'  {rot_fam.get(f, f):24} {n}')
    print('\nPOR NATUREZA (código e descrição da Receita):')
    for k, n in nome.most_common():
        print(f'  {n:4}  {k}')
    print(f'\nS/A ({len(sa)}):')
    for s in sa:
        print('  ', s)
    print(f'\nSEM RESPOSTA ({len(falhas)}):')
    for f in falhas:
        print('  ', f)
    if args.apply:
        print(f'\ngravados: {gravados}')
    else:
        print('\n[dry-run] nada gravado. Rode com --apply para gravar.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
