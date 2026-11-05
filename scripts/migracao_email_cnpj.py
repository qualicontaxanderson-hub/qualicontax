# -*- coding: utf-8 -*-
"""Migração — PARTE 4c: o e-mail do cadastro principal passa a ser O QUE ESTÁ NO CNPJ (05/11/2026).

Decisão do Anderson: "no cadastro principal tem que marcar o que está no CNPJ (...) fazer refresh e
atualizar todos os e-mails deixando igual ao que está no CNPJ, porque os funcionários colocam da
contabilidade e depois não colocam o do cliente, e eu quero acabar com isso."

Só a ReceitaWS traz o e-mail do CNPJ (a BrasilAPI não), e ela aceita 3 consultas por minuto. Por
isso o trabalho é em dois passos:
  --coletar   consulta cada PJ ativa e guarda no cache JSON (retomável; ~21 s por CNPJ)
  (sem flag)  dry-run: mostra o que mudaria a partir do cache
  --apply     grava clientes.email = e-mail do CNPJ (SOBRESCREVE, por decisão dele), log por cliente
              escrita.email_cnpj_migracao com antes/depois. Receita sem e-mail: não mexe, só relata.
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                        # noqa: E402,F401
from utils.db_helper import execute_query            # noqa: E402
from utils import cnpj_receita as RF                 # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
DOMINIOS_ESCRITORIO = ('qualicontax', 'm3a3')
ENCHIMENTO = re.compile(r'@email\.com$|^(teste|exemplo|naotem|semema?il)@', re.I)
SQL = """SELECT c.id, c.numero_cliente, c.avulso, c.nome_razao_social, c.cpf_cnpj, c.email
           FROM clientes c
          WHERE c.tipo_pessoa <> 'PF' AND c.situacao = 'ATIVO'
            AND LENGTH(REGEXP_REPLACE(c.cpf_cnpj, '[^0-9]', '')) = 14
          ORDER BY c.avulso, CAST(c.numero_cliente AS UNSIGNED), c.id"""


def dig(v):
    return re.sub(r'\D', '', str(v or ''))


def carregar(cache):
    return json.load(open(cache, encoding='utf-8')) if os.path.exists(cache) else {}


def salvar(cache, d):
    tmp = cache + '.tmp'
    json.dump(d, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    os.replace(tmp, cache)


def coletar(alvo, cache, pausa):
    d = carregar(cache)
    pend = [c for c in alvo if dig(c['cpf_cnpj']) not in d or d[dig(c['cpf_cnpj'])].get('erro') == 'indisponivel']
    print(f'coletando {len(pend)} CNPJs (cache já tem {len(d)})')
    for i, c in enumerate(pend, 1):
        cnpj = dig(c['cpf_cnpj'])
        for tent in range(4):
            try:
                r = RF._receitaws(cnpj)
                d[cnpj] = {'email': (r.get('email') or '').strip().lower(), 'razao': r.get('razao_social'), 'em': datetime.now().isoformat(timespec='seconds')}
                break
            except RF.CnpjNaoEncontrado:
                d[cnpj] = {'erro': 'nao_encontrado', 'em': datetime.now().isoformat(timespec='seconds')}
                break
            except Exception as e:
                d[cnpj] = {'erro': 'indisponivel', 'detalhe': str(e)[:120], 'em': datetime.now().isoformat(timespec='seconds')}
                time.sleep(30)
        salvar(cache, d)
        print(f"  {i}/{len(pend)} {c['numero_cliente'] or 'AV'} {c['nome_razao_social'][:35]}: {d[cnpj].get('email') or d[cnpj].get('erro')}", flush=True)
        time.sleep(pausa)
    print('coleta terminada:', Counter('ok' if 'email' in v else v['erro'] for v in d.values()))


def aplicar(alvo, cache, apply, csv_path, manter_escritorio=False, limpar_sem_email=False):
    d = carregar(cache)
    tot, linhas = Counter(), []
    for c in alvo:
        cnpj = dig(c['cpf_cnpj'])
        r = d.get(cnpj)
        rot = f"{c['numero_cliente'] or 'AV'} {c['nome_razao_social'][:40]}"
        atual = (c['email'] or '').strip().lower()
        if not r:
            tot['sem coleta'] += 1
            continue
        if r.get('erro'):
            tot[f"Receita: {r['erro']}"] += 1
            linhas.append((rot, f"Receita {r['erro']}", atual, ''))
            continue
        novo = r['email']
        if not novo:
            # 05/11/2026 (Anderson): "Sem e-mail no CNPJ: é para deixar o que está na Receita Federal" —
            # ou seja, VAZIO. O cadastro passa a refletir o CNPJ também quando o CNPJ não tem e-mail.
            if atual and limpar_sem_email:
                tot[f"{'limpado' if apply else 'limparia'} (CNPJ sem e-mail na Receita)"] += 1
                linhas.append((rot, 'limpa (CNPJ sem e-mail)', atual, ''))
                if apply:
                    execute_query('UPDATE clientes SET email = NULL, alterado_por = %s, alterado_em = NOW() WHERE id = %s', (USUARIO_ID, c['id']))
                    registrar_agente('escrita.email_cnpj_migracao', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                                     tabela='clientes', registro_id=c['id'],
                                     depois={'email_antes': atual, 'email': None, 'fonte': 'receitaws (CNPJ sem e-mail na Receita)'})
            else:
                tot['CNPJ sem e-mail na Receita (já vazio)' if not atual else 'CNPJ sem e-mail na Receita (não mexe)'] += 1
                linhas.append((rot, 'CNPJ sem e-mail na Receita', atual, ''))
            continue
        # 05/11/2026 (Anderson, vendo a planilha): "é para deixar no CADASTRO como está na RECEITA, não é
        # para deixar como cadastraram" — vale também para e-mail da Qualicontax e para "email@email.com".
        enchimento = bool(ENCHIMENTO.search(novo))
        if novo == atual:
            tot['já igual ao CNPJ'] += 1
            linhas.append((rot, 'já igual ao CNPJ', atual, novo))
            continue
        tipo = 'enchimento' if enchimento else ('escritório' if any(k in novo for k in DOMINIOS_ESCRITORIO) else 'cliente')
        if tipo == 'escritório' and manter_escritorio:
            # 05/11/2026 (Anderson, sobre os 55 CNPJs com e-mail da Qualicontax na Receita): "Pode deixar
            # como está e eu vou pedir para eles alterarem" — o cadastro fica como está; a planilha lista.
            tot['CNPJ com e-mail do escritório (não mexe, ele pede ao cliente para alterar)'] += 1
            linhas.append((rot, 'CNPJ com e-mail do escritório', atual, novo))
            continue
        tot[f"{'trocado' if apply else 'trocaria'} (e-mail do {tipo} no CNPJ)" if atual else f"{'preenchido' if apply else 'preencheria'} (e-mail do {tipo} no CNPJ)"] += 1
        linhas.append((rot, ('troca' if atual else 'preenche') + (f' ({tipo})' if tipo != 'cliente' else ''), atual, novo))
        if apply:
            execute_query('UPDATE clientes SET email = %s, alterado_por = %s, alterado_em = NOW() WHERE id = %s', (novo[:255], USUARIO_ID, c['id']))
            registrar_agente('escrita.email_cnpj_migracao', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='clientes', registro_id=c['id'],
                             depois={'email_antes': atual or None, 'email': novo, 'fonte': 'receitaws (e-mail do CNPJ)'})
    print('TOTAIS:', dict(tot))
    if csv_path:
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';'); w.writerow(['empresa', 'acao', 'e-mail no app', 'e-mail no CNPJ']); w.writerows(linhas)
        print('CSV:', csv_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cache', required=True)
    ap.add_argument('--coletar', action='store_true')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--pausa', type=float, default=21.0)
    ap.add_argument('--csv')
    ap.add_argument('--limpar-sem-email', action='store_true', help='CNPJ sem e-mail na Receita: esvazia o e-mail do cadastro')
    ap.add_argument('--manter-escritorio', action='store_true', help='não troca quando o e-mail do CNPJ é da Qualicontax/M3A3')
    a = ap.parse_args()
    alvo = execute_query(SQL, fetch=True) or []
    print(f'{len(alvo)} PJ ativas')
    if a.coletar:
        coletar(alvo, a.cache, a.pausa)
    else:
        aplicar(alvo, a.cache, a.apply, a.csv, a.manter_escritorio, a.limpar_sem_email)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    main()
