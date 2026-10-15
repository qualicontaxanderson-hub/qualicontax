# -*- coding: utf-8 -*-
"""Acrescenta o tipo da rua (RUA, AVENIDA, RODOVIA, ESTRADA...) nos endereços já gravados.

Por que existe (15/10/2026): a BrasilAPI manda o tipo num campo à parte e a
busca do CNPJ o descartava (corrigido em 53fa101). 280 de 326 endereços
ficaram só com o nome ("SEBASTIAO GENEROSO DE FREITAS" em vez de "RUA ...").

Regra, endereço por endereço das empresas (PJ):
  * o cadastro tem o tipo repetido ou abreviado ("RUA RUA X", "AV X") -> grava "RUA X", "AVENIDA X";
  * a Receita diz "RUA X" e o cadastro tem exatamente "X"  -> grava "RUA X";
  * o cadastro já é igual ao da Receita                     -> nada a fazer;
  * qualquer outra diferença (endereço digitado à mão, empresa mudou,
    endereço de correspondência...)                         -> NÃO mexe, só lista.
O UPDATE só acontece se o logradouro ainda for o mesmo lido na consulta.

    python scripts/enderecos_tipo_rua.py --cache r.json          # dry-run: consulta e lista, não grava
    python scripts/enderecos_tipo_rua.py --cache r.json --apply  # grava (reaproveita o cache)
"""
import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('MIGRATIONS_DONE', '1')

from utils.db_helper import execute_query          # noqa: E402
from utils import cnpj_receita as RF                # noqa: E402


def chave(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Z0-9]+', ' ', s.upper()).strip()


# primeira palavra do prefixo tem que ser um tipo de via: "SILVA" contra
# "RUA JOAO DA SILVA" não pode virar "acrescenta o tipo"
TIPOS = {'RUA', 'AVENIDA', 'RODOVIA', 'ESTRADA', 'TRAVESSA', 'ALAMEDA', 'PRACA', 'VIA', 'LARGO',
         'VIELA', 'BECO', 'QUADRA', 'SETOR', 'CHACARA', 'FAZENDA', 'SITIO', 'VILA', 'PARQUE',
         'JARDIM', 'CONJUNTO', 'LOTEAMENTO', 'AREA', 'ACESSO', 'VIADUTO', 'PASSAGEM', 'MARGINAL',
         'CAMINHO', 'RAMAL', 'LINHA', 'NUCLEO', 'CONDOMINIO', 'RESIDENCIAL', 'PATIO', 'GALERIA'}


def expandir(s):
    """"AV X" -> "AVENIDA X", "R X" -> "RUA X", "EST X" -> "ESTRADA X" (a ReceitaWS
    abrevia). Abreviação seguida de UMA letra/dígito fica ("R 5" é nome de rua)."""
    p = (s or '').split(' ', 1)
    if len(p) == 2 and RF._via(p[0]) and chave(p[0]) != RF._via(p[0]) and len(chave(p[1]).split(' ')[0]) >= 2:
        return f'{RF._via(p[0])} {p[1]}'
    return s


def decidir(gravado, receita):
    """('ok'|'tipo'|'repetido'|'diferente'|'sem_receita'|'vazio', novo).

    'repetido': o cadastro tem o tipo duplicado ("RUA RUA X") ou abreviado
    ("AV X") -> grava "RUA X" / "AVENIDA X"."""
    receita = expandir(RF.limpar_logradouro(receita))
    limpo = expandir(RF.limpar_logradouro(gravado))
    if gravado and limpo != re.sub(r'\s+', ' ', gravado).strip():
        return 'repetido', limpo
    g, r = chave(limpo), chave(receita)
    if not r:
        return 'sem_receita', None
    if not g:
        return 'vazio', None
    if g == r:
        return 'ok', None
    if g.split()[0] in TIPOS or g.split()[0] in ('R', 'AV', 'ROD', 'EST', 'TV', 'AL', 'PCA'):
        return 'diferente', None    # já tem tipo; a Receita às vezes repete ("RUA RUA X")
    pre = r[:-len(g)].split() if r.endswith(' ' + g) else []
    if 1 <= len(pre) <= 2 and pre[0] in TIPOS:
        return 'tipo', re.sub(r'\s+', ' ', receita).strip()
    return 'diferente', None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true', help='grava (sem isso é dry-run)')
    ap.add_argument('--cache', required=True, help='JSON das consultas ({cliente_id: {ok, logradouro, fonte}})')
    ap.add_argument('--csv', help='CSV com o antes/depois de cada endereço')
    ap.add_argument('--pausa', type=float, default=1.3, help='segundos entre consultas (a ReceitaWS aceita 3/min)')
    args = ap.parse_args()
    cache = json.load(open(args.cache, encoding='utf-8')) if os.path.exists(args.cache) else {}

    linhas = execute_query(
        """SELECT e.id AS end_id, e.cliente_id, e.logradouro, e.principal, c.numero_cliente,
                  c.nome_razao_social, c.cpf_cnpj
             FROM enderecos_clientes e JOIN clientes c ON c.id = e.cliente_id
            WHERE c.tipo_pessoa = 'PJ' ORDER BY e.cliente_id, e.principal DESC, e.id""", fetch=True)
    if not linhas:
        print('ERRO: não consegui ler os endereços do banco — nada foi feito.', flush=True)
        return 1
    clientes = sorted({l['cliente_id'] for l in linhas})
    print(f'endereços de empresas: {len(linhas)} ({len(clientes)} empresas)', flush=True)

    fontes = Counter()
    for i, cid in enumerate(clientes, 1):
        v = cache.get(str(cid))
        if v and v.get('ok') in (True, 'nao_existe'):
            continue
        doc = next(l['cpf_cnpj'] for l in linhas if l['cliente_id'] == cid)
        try:
            d, fonte = RF.consultar(doc)
            v = {'ok': True, 'logradouro': d.get('logradouro') or '', 'fonte': fonte}
            fontes[fonte] += 1
            pausa = args.pausa if fonte == 'brasilapi' else 21     # ReceitaWS: 3 por minuto
        except RF.CnpjNaoEncontrado:
            v, pausa = {'ok': 'nao_existe'}, args.pausa
        except Exception as ex:                          # fonte fora do ar: fica para a próxima rodada
            v, pausa = {'ok': False, 'erro': str(ex)[:120]}, 21
        cache[str(cid)] = v
        json.dump(cache, open(args.cache, 'w', encoding='utf-8'), ensure_ascii=False)
        if i % 20 == 0:
            print(f'  {i}/{len(clientes)} consultadas ({dict(fontes)})', flush=True)
        time.sleep(pausa)

    cont, mudar, sem_consulta, rel = Counter(), [], 0, []
    for l in linhas:
        v = cache.get(str(l['cliente_id'])) or {}
        if v.get('ok') is not True:
            sem_consulta += 1
            cont['sem_consulta'] += 1
            continue
        tipo, novo = decidir(l['logradouro'], v.get('logradouro'))
        cont[tipo] += 1
        rel.append({'numero': l['numero_cliente'] or 'avulso', 'empresa': l['nome_razao_social'],
                    'principal': l['principal'], 'gravado': l['logradouro'] or '',
                    'receita': v.get('logradouro') or '', 'decisao': tipo, 'novo': novo or ''})
        if tipo in ('tipo', 'repetido'):
            mudar.append((l, novo))

    print('\nRESUMO:', dict(cont), flush=True)
    print(f'\nACRESCENTA O TIPO OU TIRA O REPETIDO ({len(mudar)}):')
    for l, novo in mudar:
        print(f"  {str(l['numero_cliente'] or 'avulso'):>6}  {(l['logradouro'] or '')[:42]:42} -> {novo}")
    dif = [r for r in rel if r['decisao'] == 'diferente']
    print(f'\nDIFERENTE DA RECEITA, NÃO MEXE ({len(dif)}):')
    for r in dif:
        print(f"  {str(r['numero']):>6}  cadastro: {r['gravado'][:40]:40} | Receita: {r['receita'][:45]}")
    if args.csv:
        with open(args.csv, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=list(rel[0].keys()) if rel else ['numero'], delimiter=';')
            w.writeheader(); w.writerows(rel)
        print(f'\nCSV: {args.csv}')

    if not args.apply:
        print('\nDRY-RUN: nada foi gravado.')
        return 0
    gravados = 0
    for l, novo in mudar:
        n = execute_query("UPDATE enderecos_clientes SET logradouro = %s WHERE id = %s AND logradouro = %s",
                          (novo, l['end_id'], l['logradouro']), fetch=False)
        gravados += 1 if n else 0
    print(f'\nGRAVADOS: {gravados} de {len(mudar)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
