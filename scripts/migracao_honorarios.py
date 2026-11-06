# -*- coding: utf-8 -*-
"""Migração Conexa — PARTE 6a: contratos de honorários (06/11/2026).

Aba ``fin_mensalidades`` da planilha (327 linhas = 215 mensalidades + 112 parcelas de 13º) e, para as
discriminações, os itens da cobrança de out/2026 (``fin_recebimentos_itens``, tipo Mensalidade) — no
Conexa o valor da mensalidade é a SOMA dos itens (Centro Norte: 2.400 = 1.500 do posto + 3 serviços
de 900 que viviam como "venda não faturada").

Decisões do Anderson (05–06/11/2026): um contrato por empresa; serviço "Honorários contábeis"
("Mensalidade Rede X" é faixa de preço, vira honorários e a rede fica em ``plano``); 13º pelas
parcelas do Conexa (duas parcelas / única / sem); reajuste de todos em jan/2027 pelo salário mínimo;
competência M vence no dia do contrato em M+1.

Idempotente: pula quem já tem ``conexa_contrato_id`` gravado ou contrato vigente. Um log por contrato
(escrita.contrato_conexa_migracao).

    python scripts/migracao_honorarios.py --json migracao.json                      # dry-run
    python scripts/migracao_honorarios.py --json migracao.json --apply --csv saida.csv
"""
import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402
from models.fin_contrato import FinContrato, SERVICO_HONORARIOS   # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
REAJUSTE_MIGRADOS = datetime(2027, 1, 1).date()

# nome do item no Conexa (normalizado) -> nome em nfse_servicos
ALIASES = {
    'lmc': 'LMC — Livro de Movimentação de Combustíveis',
    'sistema de emissao de ct-e': 'Sistema de emissão de CT-e',
    'emissao de nota fiscal eletronica': 'Emissão de nota fiscal eletrônica',
    'folha de pagamento': 'Folha de pagamento',
    'complemento de mensalidade': 'Complemento de honorários',
    'endereco virtual': 'Endereço virtual',
    'sistema de envio de recibos trabalhistas': 'Sistema de envio de recibos trabalhistas',
    'arquivo remessa - folha de pagamento': 'Arquivo remessa — folha de pagamento',
}


def norm(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'\s+mensal$', '', s.strip())
    return re.sub(r'\s+', ' ', s)


def data_br(s):
    return datetime.strptime(s, '%d/%m/%Y').date() if s else None


def ler(caminho):
    d = json.load(open(caminho, encoding='utf-8'))
    def aba(n):
        r = d[n]
        return [dict(zip(r[0], x)) for x in r[1:]]
    return aba('fin_mensalidades'), aba('fin_esporadicos'), aba('fin_recebimentos_itens')


def chave(x):
    return str(x['numero_oficial']) if x['numero_oficial'] else 'C%s' % x['conexa_id']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--csv')
    a = ap.parse_args()
    modo = 'APLICANDO' if a.apply else 'DRY-RUN'
    if not execute_query("SHOW COLUMNS FROM fin_contratos LIKE 'conexa_contrato_id'", fetch=True):
        print('fin_contratos sem as colunas novas — rode migrations/migracao_05_honorarios.py --apply')
        return

    mens, espor, itens = ler(a.json)
    cli_num = {str(c['numero_cliente']): c for c in execute_query(
        "SELECT id, numero_cliente, nome_razao_social, conexa_id, situacao FROM clientes WHERE numero_cliente IS NOT NULL", fetch=True) or []}
    cli_cx = {str(c['conexa_id']): c for c in execute_query(
        "SELECT id, numero_cliente, nome_razao_social, conexa_id, situacao FROM clientes WHERE conexa_id IS NOT NULL", fetch=True) or []}
    servicos = {norm(s['nome']): s['id'] for s in execute_query('SELECT id, nome FROM nfse_servicos', fetch=True) or []}
    alias_id = {k: servicos.get(norm(v)) for k, v in ALIASES.items()}
    ja = {r['conexa_contrato_id'] for r in execute_query('SELECT conexa_contrato_id FROM fin_contratos WHERE conexa_contrato_id IS NOT NULL', fetch=True) or []}

    itens_out = defaultdict(list)
    for x in itens:
        if x['competencia'] == '2026-10-01' and x['tipo_item'] == 'Mensalidade':
            itens_out[chave(x)].append((x['servico'], float(x['valor'])))
    # "vendas não faturadas" mensais do Conexa (Centro Norte): último mês lançado
    extras_nf = defaultdict(dict)
    for x in espor:
        if x['status'] == 'Não Faturada' and x['servico'] == 'Mensalidade' and x['obs']:
            extras_nf[chave(x)][x['obs'].strip()] = float(x['valor'])
    decimo = defaultdict(list)
    for x in mens:
        if x['tipo'] != 'Mensalidade':
            decimo[chave(x)].append(x['plano'])

    tot, saida = Counter(), []
    print(f'{modo}: {sum(1 for x in mens if x["tipo"] == "Mensalidade")} mensalidades')
    for x in sorted((m for m in mens if m['tipo'] == 'Mensalidade'), key=lambda m: (m['numero_oficial'] is None, m['numero_oficial'] or 0)):
        k = chave(x)
        c = cli_num.get(str(x['numero_oficial'])) if x['numero_oficial'] else cli_cx.get(str(x['conexa_id']))
        rotulo = '%s %s' % (x['numero_oficial'] or 'Conexa %s' % x['conexa_id'], x['cliente'][:34])
        if not c:
            tot['empresa não está no app'] += 1
            saida.append((rotulo, '', x['valor'], '', '', 'empresa não está no app')); continue
        if x['contrato_conexa'] in ja:
            tot['já importado'] += 1
            saida.append((rotulo, c['id'], x['valor'], '', '', 'já importado')); continue
        if FinContrato.por_cliente(c['id']):
            tot['empresa já tem contrato vigente'] += 1
            saida.append((rotulo, c['id'], x['valor'], '', '', 'já tem contrato vigente')); continue
        valor = round(float(x['valor']), 2)
        # discriminações: itens de out/26; se faltar, o próprio plano
        its = []
        for nome, v in itens_out.get(k, []):
            n = norm(nome)
            if n.startswith('mensalidade') and 'loja' not in n and 'conveniencia' not in n:
                its.append({'descricao': 'Honorários contábeis', 'valor': v, 'servico_id': SERVICO_HONORARIOS})
            else:
                its.append({'descricao': ALIASES.get(n, nome).replace(' Mensal', '')[:120], 'valor': v, 'servico_id': alias_id.get(n)})
        for nome, v in sorted(extras_nf.get(k, {}).items()):
            its.append({'descricao': 'Honorários contábeis — %s' % nome, 'valor': v, 'servico_id': SERVICO_HONORARIOS})
        if not its:
            its = [{'descricao': 'Honorários contábeis', 'valor': valor, 'servico_id': SERVICO_HONORARIOS}]
        soma = round(sum(i['valor'] for i in its), 2)
        obs = ''
        if abs(soma - valor) > 0.009:
            obs = 'itens de out/26 somam %.2f; contrato %.2f — gravado como item único' % (soma, valor)
            tot['itens não batem (item único)'] += 1
            its = [{'descricao': 'Honorários contábeis', 'valor': valor, 'servico_id': SERVICO_HONORARIOS}]
        # juntar itens iguais (duas linhas "Honorários contábeis" viram uma)
        juntos = {}
        for i in its:
            j = juntos.setdefault(i['descricao'], dict(i, valor=0.0))
            j['valor'] = round(j['valor'] + i['valor'], 2)
        its = list(juntos.values())
        ds = decimo.get(k, [])
        dtipo = 'NENHUM' if not ds else ('UNICA' if any('nica' in p for p in ds) else 'DUAS')
        inicio = data_br(x['inicio'])
        fim = data_br(x['fim'])
        tot['criar'] += 1
        tot['13º ' + dtipo] += 1
        saida.append((rotulo, c['id'], valor, x['dia_vencimento'], dtipo, 'criar: ' + '; '.join('%s=%.2f' % (i['descricao'], i['valor']) for i in its) + ('; ' + obs if obs else '')))
        if not a.apply:
            continue
        try:
            cid = FinContrato.criar(
                cliente_id=c['id'], valor=valor, dia_vencimento=x['dia_vencimento'], inicio=inicio, fim=fim, itens=its,
                plano=x['plano'], decimo_tipo=dtipo, reajuste_tipo='SALARIO_MINIMO', proximo_reajuste=REAJUSTE_MIGRADOS,
                situacao='ATIVO', conexa_contrato_id=x['contrato_conexa'], criado_por=USUARIO_ID, aprovado_por=USUARIO_ID)
        except ValueError as e:
            tot['erro'] += 1
            saida.append((rotulo, c['id'], valor, x['dia_vencimento'], dtipo, 'ERRO: %s' % e)); continue
        ja.add(x['contrato_conexa'])
        registrar_agente('escrita.contrato_conexa_migracao', 'financeiro', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                         tabela='fin_contratos', registro_id=cid,
                         depois={'cliente_id': c['id'], 'numero': c.get('numero_cliente'), 'empresa': c['nome_razao_social'],
                                 'valor': valor, 'dia_vencimento': x['dia_vencimento'], 'inicio': x['inicio'], 'plano': x['plano'],
                                 'decimo': dtipo, 'reajuste': 'SALARIO_MINIMO jan/2027',
                                 'itens': [{'descricao': i['descricao'], 'valor': i['valor']} for i in its],
                                 'conexa_contrato_id': x['contrato_conexa'], 'obs': obs or None, 'fonte': 'Conexa'})
    print('TOTAIS:')
    for k2, v in sorted(tot.items()):
        print(f'  {v:5d} {k2}')
    if a.csv:
        with open(a.csv, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['empresa', 'cliente_id', 'valor', 'dia venc.', '13º', 'ação'])
            w.writerows(saida)
        print('CSV:', a.csv)


if __name__ == '__main__':
    main()
