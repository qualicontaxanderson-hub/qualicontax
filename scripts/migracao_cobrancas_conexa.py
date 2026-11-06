# -*- coding: utf-8 -*-
"""Migração Conexa — PARTE 6b: cobranças (06/11/2026).

Aba ``fin_recebimentos`` (3.144 cobranças, ago/2025–out/2026) + ``fin_recebimentos_itens`` (3.613).

* TODAS vão para ``fin_cobrancas_conexa`` (histórico, só leitura, uma linha por cobrança com os itens
  em JSON) — sem tocar nos 2.182 títulos a receber que o extrato já criou.
* As ABERTAS (245, R$ 262.884,01) viram TÍTULO A RECEBER em ``fin_titulos`` (origem 'conexa',
  chave_idem 'conexa:cobranca:<id>'), para o extrato da Efí/Sicredi dar a baixa. Ok do Anderson 06/11.

Idempotente: cobrança já gravada é pulada; título só nasce se a chave_idem não existir.

    python scripts/migracao_cobrancas_conexa.py --json migracao.json                      # dry-run
    python scripts/migracao_cobrancas_conexa.py --json migracao.json --apply --csv saida.csv
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402
from models.fin_titulo import FinTitulo              # noqa: E402
from models.fin_contrato import EMPRESA_QUALICONTAX, CATEGORIA_HONORARIOS, CATEGORIA_DECIMO   # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
CATEGORIA_AVULSOS = 2        # fin_categorias 'Honorários avulsos'


def d_br(s):
    if not s:
        return None
    s = str(s)
    try:
        return datetime.strptime(s[:10], '%d/%m/%Y').date()
    except ValueError:
        return datetime.strptime(s[:10], '%Y-%m-%d').date()


def dig(s):
    return re.sub(r'\D', '', s or '') or None


def ler(caminho):
    d = json.load(open(caminho, encoding='utf-8'))
    def aba(n):
        r = d[n]
        return [dict(zip(r[0], x)) for x in r[1:]]
    return aba('fin_recebimentos'), aba('fin_recebimentos_itens')


def categoria_de(itens):
    tipos = {i['tipo'] for i in itens}
    if tipos == {'13º'}:
        return CATEGORIA_DECIMO
    if tipos and 'Mensalidade' not in tipos and '13º' not in tipos:
        return CATEGORIA_AVULSOS
    return CATEGORIA_HONORARIOS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--csv')
    a = ap.parse_args()
    modo = 'APLICANDO' if a.apply else 'DRY-RUN'
    if not execute_query("SHOW TABLES LIKE 'fin_cobrancas_conexa'", fetch=True):
        print('tabela fin_cobrancas_conexa não existe — rode migrations/migracao_06_cobrancas_conexa.py --apply')
        return
    rec, itens = ler(a.json)
    cli_num = {str(c['numero_cliente']): c for c in execute_query(
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes WHERE numero_cliente IS NOT NULL", fetch=True) or []}
    cli_cx = {str(c['conexa_id']): c for c in execute_query(
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, conexa_id FROM clientes WHERE conexa_id IS NOT NULL", fetch=True) or []}
    ja = {r['cobranca_conexa'] for r in execute_query('SELECT cobranca_conexa FROM fin_cobrancas_conexa', fetch=True) or []}
    idem = {r['chave_idem'] for r in execute_query("SELECT chave_idem FROM fin_titulos WHERE origem = 'conexa'", fetch=True) or []}
    por_cob = defaultdict(list)
    for x in itens:
        por_cob[x['cobranca_conexa']].append({'tipo': x['tipo_item'], 'servico': x['servico'], 'valor': x['valor'],
                                              'quant': x['quant'], 'obs': x['obs']})
    tot, saida = Counter(), []
    print(f'{modo}: {len(rec)} cobranças, {sum(1 for x in rec if x["status"] == "Aberta")} abertas')
    for x in rec:
        c = cli_num.get(str(x['numero_oficial'])) if x['numero_oficial'] else cli_cx.get(str(x['conexa_id']))
        its = por_cob.get(x['cobranca_conexa'], [])
        rotulo = '%s %s' % (x['numero_oficial'] or 'Conexa %s' % x['conexa_id'], (x['cliente'] or '')[:34])
        if not c:
            tot['sem cliente no app (vai só ao histórico)'] += 1
        if x['cobranca_conexa'] in ja:
            tot['já gravada'] += 1
            saida.append((rotulo, x['cobranca_conexa'], x['status'], x['valor'], x['vencimento'], 'já gravada')); continue
        aberta = x['status'] == 'Aberta'
        chave = 'conexa:cobranca:%s' % x['cobranca_conexa']
        cria_titulo = aberta and c is not None and chave not in idem
        if aberta and not c:
            tot['ABERTA sem cliente no app (sem título)'] += 1
        tot['histórico'] += 1
        if cria_titulo:
            tot['título a receber'] += 1
        saida.append((rotulo, x['cobranca_conexa'], x['status'], x['valor'], x['vencimento'],
                      'histórico' + (' + título' if cria_titulo else '')))
        if not a.apply:
            continue
        titulo_id = None
        if cria_titulo:
            comp = d_br(x['competencia'])
            desc = '%s · competência %s (Conexa %s)' % (
                'Honorários' if categoria_de(its) == CATEGORIA_HONORARIOS else ('13º' if categoria_de(its) == CATEGORIA_DECIMO else 'Serviços'),
                comp.strftime('%m/%Y') if comp else '—', x['cobranca_conexa'])
            titulo_id = FinTitulo.criar(
                'R', c['nome_razao_social'], categoria_de(its), desc[:255], comp, comp or d_br(x['vencimento']),
                d_br(x['vencimento']), x['valor'], EMPRESA_QUALICONTAX, contraparte_doc=dig(x['cpf_cnpj']) or dig(c.get('cpf_cnpj')),
                cliente_id=c['id'], origem='conexa', chave_idem=chave,
                observacao='Boleto emitido pelo Conexa (%s). Itens: %s' % (x['meio'] or '—', '; '.join('%s R$ %.2f' % (i['servico'], i['valor']) for i in its))[:2000])
            idem.add(chave)
            registrar_agente('escrita.titulo_conexa_migracao', 'financeiro', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='fin_titulos', registro_id=titulo_id,
                             depois={'cliente_id': c['id'], 'empresa': c['nome_razao_social'], 'valor': x['valor'],
                                     'vencimento': x['vencimento'], 'competencia': x['competencia'],
                                     'cobranca_conexa': x['cobranca_conexa'], 'meio': x['meio'], 'fonte': 'Conexa'})
        execute_query(
            """INSERT INTO fin_cobrancas_conexa (cobranca_conexa, cliente_id, destino, nome, cpf_cnpj, tipo, competencia,
                   vencimento, valor, valor_recebido, quitacao, data_credito, status, meio, conta, nf_numero, observacoes, itens, titulo_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (x['cobranca_conexa'], c['id'] if c else None, x['destino'], (x['cliente'] or '')[:255], x['cpf_cnpj'], x['tipo'],
             d_br(x['competencia']), d_br(x['vencimento']), x['valor'], x['valor_recebido'], d_br(x['quitacao']),
             d_br(x['data_credito']), x['status'], x['meio'], (x['conta'] or '')[:60] or None, x['nf_numero'],
             (x['observacoes'] or '')[:500] or None, json.dumps(its, ensure_ascii=False), titulo_id), fetch=False)
        ja.add(x['cobranca_conexa'])
    print('TOTAIS:')
    for k, v in sorted(tot.items()):
        print(' %6d %s' % (v, k))
    if a.csv:
        with open(a.csv, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['empresa', 'cobrança Conexa', 'status', 'valor', 'vencimento', 'ação'])
            w.writerows(saida)
        print('CSV:', a.csv)


if __name__ == '__main__':
    main()
