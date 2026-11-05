# -*- coding: utf-8 -*-
"""Migração — PARTE 4d: início de contrato só de quem entrou de 01/05/2025 para cá (05/11/2026).

Decisão do Anderson: "Início de contrato vamos colocar os que temos certeza, que são os que entraram
de 01/05/2025 pra cá, e os demais depois eu passo uma planilha para a Alessandra atualizar."

A parte 1 pôs em data_inicio_contrato a DATA DE CADASTRO NO ACESSÓRIAS (cadastro_s3d) de quem não
tinha nada. Mas o Acessórias foi implantado em 11/2022: 179 empresas ficaram com "início" em 2022,
que é a implantação, não a entrada. Este script LIMPA (NULL) a data de quem a parte 1 preencheu com
cadastro_s3d anterior a 01/05/2025. Data que não bate com cadastro_s3d (alguém digitou) fica como
está e é só relatada. Idempotente. Log: escrita.limpou_inicio_contrato_migracao.

Uso:
    python scripts/migracao_inicio_contrato.py --json <migracao.json>            # dry-run
    python scripts/migracao_inicio_contrato.py --json <migracao.json> --apply
"""
import argparse
import io
import json
import os
import re
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                        # noqa: E402,F401
from utils.db_helper import execute_query            # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
CORTE = date(2025, 5, 1)


def data_br(v):
    s = str(v or '').strip()
    m = re.match(r'^(\d{2})/(\d{2})/(\d{4})', s) or None
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', required=True, help='migracao.json (abas em lista com cabeçalho na linha 0)')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--limpar-digitadas', action='store_true',
                    help='05/11/2026, Anderson: "Limpar para ela trabalhar" — apaga também as datas < 05/2025 digitadas por alguém')
    a = ap.parse_args()
    m = json.load(open(a.json, encoding='utf-8'))
    emp = m['empresas']
    head = [str(h) for h in emp[0]]
    cad = {}
    for r in emp[1:]:
        d = dict(zip(head, r))
        n = str(d.get('numero_oficial') or '').split('.')[0]
        if n.isdigit():
            cad[int(n)] = data_br(d.get('cadastro_s3d'))

    app = execute_query("""SELECT id, numero_cliente, nome_razao_social, data_inicio_contrato
                             FROM clientes WHERE avulso = 0 AND numero_cliente REGEXP '^[0-9]+$'""", fetch=True) or []
    tot, limpar, digitadas = Counter(), [], []
    for c in app:
        n = int(c['numero_cliente'])
        ini = c['data_inicio_contrato']
        if n not in cad:
            tot['fora da planilha'] += 1
            continue
        if not ini:
            tot['sem início (ok, fica para a planilha da Alessandra)'] += 1
            continue
        if ini >= CORTE:
            tot['início >= 01/05/2025 (mantido)'] += 1
            continue
        if cad[n] == ini:
            tot['limpa (a parte 1 pôs a data de cadastro no Acessórias < 05/2025)'] += 1
            limpar.append(c)
        elif a.limpar_digitadas:
            tot['limpa (digitada por alguém antes de 05/2025; fica para a planilha)'] += 1
            c['digitada'] = True
            limpar.append(c)
        else:
            tot['< 05/2025 mas DIGITADA por alguém (mantida, só relato)'] += 1
            digitadas.append(c)

    print(f"{'APLICANDO' if a.apply else 'DRY-RUN'}: {len(app)} clientes numerados | planilha {len(cad)}")
    for k, v in tot.most_common():
        print(f'  {v:4} {k}')
    print('\nDigitadas antes de 05/2025 (ficam, conferir na planilha da Alessandra):')
    for c in sorted(digitadas, key=lambda x: int(x['numero_cliente'])):
        print(f"  {c['numero_cliente']:>5} {c['nome_razao_social'][:40]:40} {c['data_inicio_contrato']:%d/%m/%Y} (Acessórias: {cad[int(c['numero_cliente'])] or '-'})")
    if a.apply:
        for c in limpar:
            execute_query('UPDATE clientes SET data_inicio_contrato = NULL, alterado_por = %s, alterado_em = NOW() WHERE id = %s', (USUARIO_ID, c['id']))
            registrar_agente('escrita.limpou_inicio_contrato_migracao', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='clientes', registro_id=c['id'],
                             depois={'data_inicio_contrato_antes': c['data_inicio_contrato'].isoformat(), 'data_inicio_contrato': None,
                                     'motivo': ('data digitada antes de 01/05/2025, sem certeza (muitas são a abertura da empresa); aguarda planilha'
                                                if c.get('digitada') else
                                                'era a data de cadastro no Acessórias (< 01/05/2025), não a entrada; aguarda planilha')})
        print(f'\nlimpas: {len(limpar)}')


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    main()
