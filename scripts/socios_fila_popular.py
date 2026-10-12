# -*- coding: utf-8 -*-
"""Enche a fila "Sócios a cadastrar" com o quadro de sócios da Receita.

Para cada empresa PJ ATIVA sem nenhum sócio atual, consulta a Receita
(BrasilAPI; ReceitaWS se ela falhar) e põe o QSA em socios_fila. Repetir não
duplica (chave única empresa x pessoa). Não mexe em socios_clientes: quem
liga é a equipe, pela tela Cadastros > Sócios a cadastrar.

    python scripts/socios_fila_popular.py                 # dry-run (só conta)
    python scripts/socios_fila_popular.py --apply         # grava a fila
    python scripts/socios_fila_popular.py --cache x.json  # reaproveita consultas
                                                          # já feitas ({cliente_id: {ok, qsa}})
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
from utils import socios_fila as SF                 # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true', help='grava (sem isso é dry-run)')
    ap.add_argument('--cache', help='JSON com consultas já feitas')
    ap.add_argument('--pausa', type=float, default=1.3, help='segundos entre consultas à Receita')
    args = ap.parse_args()
    cache = json.load(open(args.cache, encoding='utf-8')) if args.cache else {}

    emp = execute_query(
        """SELECT c.id, c.numero_cliente, c.cpf_cnpj FROM clientes c
            WHERE c.tipo_pessoa = 'PJ' AND c.situacao = 'ATIVO'
              AND NOT EXISTS (SELECT 1 FROM socios_clientes s WHERE s.cliente_id = c.id AND s.ativo = 1)
            ORDER BY c.id""", fetch=True) or []
    print(f'empresas PJ ativas sem sócio: {len(emp)}')
    cont, status = Counter(), Counter()
    for i, e in enumerate(emp, 1):
        v = cache.get(str(e['id']))
        if not v or v.get('ok') is not True:
            try:
                d, _ = RF.consultar(e['cpf_cnpj'])
                v = {'ok': True, 'qsa': d.get('qsa') or []}
            except RF.CnpjNaoEncontrado:
                v = {'ok': 'nao_existe'}
            except Exception as ex:                 # fonte fora do ar: fica para a próxima rodada
                v = {'ok': False, 'erro': str(ex)[:120]}
            time.sleep(args.pausa)
        if v.get('ok') is not True:
            cont['consulta ' + ('falhou' if v.get('ok') is False else 'CNPJ não existe')] += 1
            continue
        if not v['qsa']:
            cont['sem quadro de sócios (individual/MEI)'] += 1
            continue
        itens = SF.popular(e['id'], v['qsa'], gravar=args.apply)
        cont['com quadro'] += 1
        status.update(i['status'] for i in itens)
        if i % 25 == 0:
            print(f'  {i}/{len(emp)}', flush=True)
    print('empresas:', dict(cont))
    print('linhas da fila:', dict(status), '| pessoas distintas pendentes:',
          '(veja na tela)' if args.apply else '—')
    if not args.apply:
        print('[dry-run] nada gravado. Rode com --apply para encher a fila.')
    else:
        n = execute_query("SELECT status, COUNT(*) n, COUNT(DISTINCT chave) p FROM socios_fila GROUP BY status",
                          fetch=True)
        print('socios_fila agora:', n)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
