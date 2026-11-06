# -*- coding: utf-8 -*-
"""Correção da migração financeira do Conexa (06/11/2026): contratos e títulos na EMPRESA ERRADA.

O que aconteceu
---------------
As abas fin_mensalidades e fin_recebimentos do migracao.json trazem uma coluna "numero_oficial" que NÃO é o
número do cliente no Acessórias/app — é outro índice. As partes 6.1 (contratos de honorários) e 6.2 (cobranças
abertas do Conexa) casaram o cliente por esse número, e 211 dos 215 contratos e 211 dos 245 títulos ficaram
ligados a uma empresa que não é a dona. Os avulsos (casados pelo conexa_id) ficaram certos. A aba de contatos
casou pelo CNPJ e também está certa (24/24 conferidos).

Como corrige
------------
Pelo CNPJ, que é a chave verdadeira: contrato -> mensalidade Conexa -> conexa_id -> CNPJ (vem nas cobranças)
-> clientes.cpf_cnpj. Título -> contraparte_doc -> clientes.cpf_cnpj. Histórico fin_cobrancas_conexa -> cpf_cnpj.
Nada mais foi gerado em cima (sem lotes, boletos, baixas, lançamentos, convites).

Uso:  python scripts/correcao_cliente_conexa.py            (só confere e grava o CSV)
      python scripts/correcao_cliente_conexa.py --aplicar  (grava e registra no log)
"""
import argparse
import csv
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
JSON = os.environ.get('MIGRACAO_JSON') or os.path.join(
    os.environ.get('TEMP', ''), 'claude', 'bfaf016c5ad5231b', 'e89f5fc4-0cb5-42aa-bac0-a61a03c3e62d',
    'scratchpad', 'migracao_in', 'migracao.json')
CSV_OUT = os.path.join(os.path.dirname(JSON), '..', 'correcao_cliente_conexa.csv')


def so(x):
    return re.sub(r'\D', '', str(x or ''))


def recs(d, aba):
    rows = d[aba]
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def main(aplicar):
    d = json.load(open(JSON, encoding='utf-8'))
    mens = {str(m['contrato_conexa']): m for m in recs(d, 'fin_mensalidades')}
    cx_doc = {}
    for x in recs(d, 'fin_recebimentos'):
        if x.get('conexa_id') and x.get('cpf_cnpj'):
            cx_doc[str(x['conexa_id'])] = so(x['cpf_cnpj'])

    clientes = execute_query('SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes', fetch=True) or []
    by_doc, by_nome, by_id = {}, {}, {}
    for c in clientes:
        by_doc.setdefault(so(c['cpf_cnpj']), []).append(c)
        by_nome.setdefault((c['nome_razao_social'] or '').strip().upper(), []).append(c)
        by_id[c['id']] = c

    linhas = []
    erros = []

    # ---- contratos ------------------------------------------------------------------------------------
    contratos = execute_query('SELECT id, cliente_id, conexa_contrato_id, valor FROM fin_contratos WHERE conexa_contrato_id IS NOT NULL',
                              fetch=True) or []
    mudar_ct = []
    for ct in contratos:
        m = mens.get(str(ct['conexa_contrato_id']))
        if not m:
            erros.append(('contrato', ct['id'], 'mensalidade Conexa não achada'))
            continue
        doc = cx_doc.get(str(m['conexa_id']))
        alvos = by_doc.get(doc) if doc else None
        if not alvos:
            alvos = by_nome.get((m['cliente'] or '').strip().upper())
            via = 'nome'
        else:
            via = 'cnpj'
        if not alvos or len(alvos) != 1:
            erros.append(('contrato', ct['id'], 'cliente não identificado (%s): %s' % (m['conexa_id'], m['cliente'])))
            continue
        novo = alvos[0]
        if novo['id'] != ct['cliente_id']:
            mudar_ct.append((ct, novo, m, via))
        linhas.append(['contrato', ct['id'], m['cliente'], ct['valor'], ct['cliente_id'], by_id[ct['cliente_id']]['nome_razao_social'],
                       novo['id'], novo['numero_cliente'], novo['nome_razao_social'], 'muda' if novo['id'] != ct['cliente_id'] else 'certo', via])
    # um contrato vigente por cliente: conferir colisão depois da troca
    destino = [n['id'] for _, n, _, _ in mudar_ct] + [ct['cliente_id'] for ct in contratos if ct['id'] not in {c['id'] for c, _, _, _ in mudar_ct}]
    dup = {x for x in destino if destino.count(x) > 1}
    if dup:
        erros.append(('contrato', '-', 'dois contratos cairiam no mesmo cliente: %s' % sorted(dup)))

    # ---- títulos --------------------------------------------------------------------------------------
    titulos = execute_query("SELECT id, cliente_id, contraparte_doc, contraparte_nome, valor FROM fin_titulos WHERE origem = 'conexa'",
                            fetch=True) or []
    mudar_tt = []
    for t in titulos:
        alvos = by_doc.get(so(t['contraparte_doc']))
        if not alvos or len(alvos) != 1:
            erros.append(('titulo', t['id'], 'cliente não identificado pelo doc %s' % t['contraparte_doc']))
            continue
        novo = alvos[0]
        if novo['id'] != t['cliente_id']:
            mudar_tt.append((t, novo))
        linhas.append(['titulo', t['id'], t['contraparte_nome'], t['valor'], t['cliente_id'], by_id[t['cliente_id']]['nome_razao_social'],
                       novo['id'], novo['numero_cliente'], novo['nome_razao_social'], 'muda' if novo['id'] != t['cliente_id'] else 'certo', 'cnpj'])

    # ---- histórico ------------------------------------------------------------------------------------
    hist = execute_query('SELECT id, cliente_id, cpf_cnpj FROM fin_cobrancas_conexa', fetch=True) or []
    mudar_h = []
    for h in hist:
        alvos = by_doc.get(so(h['cpf_cnpj']))
        if not alvos or len(alvos) != 1:
            erros.append(('historico', h['id'], 'doc %s' % h['cpf_cnpj']))
            continue
        if alvos[0]['id'] != h['cliente_id']:
            mudar_h.append((h['id'], alvos[0]['id']))

    with open(CSV_OUT, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['tabela', 'id', 'empresa (Conexa)', 'valor', 'cliente_id hoje', 'empresa hoje (ERRADA)', 'cliente_id certo', 'nº certo', 'empresa certa', 'ação', 'via'])
        w.writerows(linhas)
    print('contratos: %d conferidos, %d mudam | títulos: %d conferidos, %d mudam | histórico: %d linhas, %d mudam'
          % (len(contratos), len(mudar_ct), len(titulos), len(mudar_tt), len(hist), len(mudar_h)))
    print('CSV:', os.path.abspath(CSV_OUT))
    for e in erros:
        print('  ERRO', e)
    if erros:
        print('Nada aplicado: resolva os erros acima.')
        return 1
    if not aplicar:
        print('Modo conferência. Rode com --aplicar para gravar.')
        return 0

    for ct, novo, m, via in mudar_ct:
        execute_query('UPDATE fin_contratos SET cliente_id = %s WHERE id = %s', (novo['id'], ct['id']), fetch=False)
        registrar_agente('escrita.contrato_conexa_correcao', 'financeiro', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                         tabela='fin_contratos', registro_id=ct['id'],
                         depois={'cliente_id': novo['id'], 'numero': novo['numero_cliente'], 'empresa': novo['nome_razao_social'],
                                 'antes_cliente_id': ct['cliente_id'], 'antes_empresa': by_id[ct['cliente_id']]['nome_razao_social'],
                                 'conexa_contrato': ct['conexa_contrato_id'], 'conexa_cliente': m['cliente'], 'via': via,
                                 'motivo': 'numero_oficial das abas fin_* do migracao.json não era o número do cliente'})
    for t, novo in mudar_tt:
        execute_query('UPDATE fin_titulos SET cliente_id = %s WHERE id = %s', (novo['id'], t['id']), fetch=False)
        registrar_agente('escrita.titulo_conexa_correcao', 'financeiro', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                         tabela='fin_titulos', registro_id=t['id'],
                         depois={'cliente_id': novo['id'], 'numero': novo['numero_cliente'], 'empresa': novo['nome_razao_social'],
                                 'antes_cliente_id': t['cliente_id'], 'antes_empresa': by_id[t['cliente_id']]['nome_razao_social'],
                                 'contraparte': t['contraparte_nome'], 'doc': t['contraparte_doc']})
    # histórico: 3 mil linhas numa só instrução (linha a linha levava mais de meia hora pela rede)
    if mudar_h:
        execute_query(
            """UPDATE fin_cobrancas_conexa cc
                 JOIN clientes c ON REPLACE(REPLACE(REPLACE(c.cpf_cnpj, '.', ''), '/', ''), '-', '') COLLATE utf8mb4_unicode_ci
                                  = REPLACE(REPLACE(REPLACE(cc.cpf_cnpj, '.', ''), '/', ''), '-', '')
                  SET cc.cliente_id = c.id
                WHERE cc.cliente_id <> c.id""", fetch=False)
    registrar_agente('escrita.historico_conexa_correcao', 'financeiro', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                     tabela='fin_cobrancas_conexa', registro_id=None,
                     depois={'linhas_corrigidas': len(mudar_h), 'contratos': len(mudar_ct), 'titulos': len(mudar_tt)})
    print('APLICADO: %d contratos, %d títulos, %d linhas de histórico.' % (len(mudar_ct), len(mudar_tt), len(mudar_h)))
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--aplicar', action='store_true')
    sys.exit(main(ap.parse_args().aplicar))
