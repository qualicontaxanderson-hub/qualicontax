# -*- coding: utf-8 -*-
"""Correção do NOME nos títulos a receber vindos do Conexa (12/11/2026).

O que aconteceu
---------------
A planilha Migracao_Cadastros_App_Qualicontax.xlsx (04/11) traz, na aba fin_recebimentos, uma coluna
"numero_oficial" que NÃO é o número do Acessórias (2.979 de 2.979 linhas divergem da aba empresas pelo CNPJ).
A importação de 06/11 achou o cliente por esse número e gravou no título o nome da empresa errada. No mesmo dia
o scripts/correcao_cliente_conexa.py religou o cliente pelo CNPJ, mas deixou o contraparte_nome antigo:
o título da Longhi (#476) continuou escrito "BANZAI CONSULTORIA" (#284).

Como corrige
------------
Só o contraparte_nome, e só quando o CNPJ confere nos TRÊS lugares: cobrança do Conexa (fin_cobrancas_conexa),
título (contraparte_doc) e cadastro (clientes, pelo cliente_id). O nome vem do nosso cadastro. Nenhum número
do Conexa é usado. Valor, vencimento, cliente e situação não mudam.

Uso:  python scripts/correcao_nome_titulos_conexa.py            (só confere: grava a lista .xlsx e o backup)
      python scripts/correcao_nome_titulos_conexa.py --aplicar  (grava, registra no log de atividade)
"""
import argparse
import io
import json
import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
SAIDA = os.path.join(os.path.expanduser('~'), 'Downloads')
CARIMBO = datetime.now().strftime('%Y%m%d_%H%M%S')


def so(s):
    return re.sub(r'\D', '', s or '')


def _json(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def main(aplicar):
    todos = execute_query("SELECT * FROM fin_titulos WHERE origem = 'conexa' ORDER BY id", fetch=True) or []
    bkp = os.path.join(SAIDA, f'backup_fin_titulos_conexa_{CARIMBO}.json')
    with open(bkp, 'w', encoding='utf-8') as f:
        json.dump([{k: _json(v) for k, v in r.items()} for r in todos], f, ensure_ascii=False, indent=1)
    print(f'backup: {len(todos)} títulos do Conexa -> {bkp}')

    linhas = execute_query(
        """SELECT t.id, t.status, t.valor, t.vencimento, t.contraparte_nome, t.contraparte_doc, t.cliente_id,
                  c.numero_cliente, c.nome_razao_social, c.cpf_cnpj,
                  x.cobranca_conexa, x.nome AS conexa_nome, x.cpf_cnpj AS conexa_doc
             FROM fin_titulos t
             JOIN clientes c ON c.id = t.cliente_id
             LEFT JOIN fin_cobrancas_conexa x ON x.titulo_id = t.id
            WHERE t.origem = 'conexa' ORDER BY c.nome_razao_social, t.vencimento""", fetch=True) or []

    mudar, iguais, travados = [], 0, []
    for r in linhas:
        docs_ok = so(r['contraparte_doc']) == so(r['cpf_cnpj']) == so(r['conexa_doc']) != ''
        if not docs_ok:
            travados.append(r)
            continue
        if (r['contraparte_nome'] or '').strip() == (r['nome_razao_social'] or '').strip():
            iguais += 1
            continue
        mudar.append(r)

    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Corrigir nome'
    cab = ['Título', 'Situação', 'Vencimento', 'Valor', 'Nome ERRADO no título', 'Nome CERTO (nosso cadastro)',
           'Nº cadastro', 'CNPJ', 'Nome no Conexa', 'Cobrança Conexa']
    ws.append(cab)
    for c in ws[1]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='1F5F3F')
    for r in mudar:
        ws.append([r['id'], r['status'], r['vencimento'], float(r['valor']), r['contraparte_nome'],
                   r['nome_razao_social'], r['numero_cliente'], r['cpf_cnpj'], r['conexa_nome'], r['cobranca_conexa']])
    for col, w in zip('ABCDEFGHIJ', (8, 10, 12, 11, 44, 44, 10, 20, 44, 12)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = 'A2'
    if travados:
        ws2 = wb.create_sheet('Não mexe (CNPJ não confere)')
        ws2.append(['Título', 'Nome no título', 'CNPJ título', 'Cadastro', 'CNPJ cadastro', 'CNPJ Conexa'])
        for r in travados:
            ws2.append([r['id'], r['contraparte_nome'], r['contraparte_doc'], r['nome_razao_social'], r['cpf_cnpj'], r['conexa_doc']])
    lista = os.path.join(SAIDA, f'Titulos_Conexa_nome_corrigir_{CARIMBO}.xlsx')
    wb.save(lista)

    print(f'títulos do Conexa: {len(linhas)} | nome já certo: {iguais} | corrigir: {len(mudar)} | '
          f'travados (CNPJ não confere): {len(travados)}')
    print(f'lista: {lista}')
    for r in mudar[:8]:
        print(f"  #{r['id']}: {r['contraparte_nome'][:38]!r} -> {r['nome_razao_social'][:38]!r} (nº {r['numero_cliente']})")
    if not aplicar:
        print('SÓ CONFERÊNCIA: nada foi gravado.')
        return 0

    for r in mudar:
        execute_query('UPDATE fin_titulos SET contraparte_nome = %s WHERE id = %s AND origem = %s',
                      (r['nome_razao_social'], r['id'], 'conexa'), fetch=False)
        registrar_agente('escrita.titulo_conexa_nome_correcao', 'financeiro', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                         tabela='fin_titulos', registro_id=r['id'],
                         depois={'contraparte_nome': r['nome_razao_social'], 'antes': r['contraparte_nome'],
                                 'numero': r['numero_cliente'], 'doc': r['contraparte_doc'],
                                 'motivo': 'nome gravado pelo numero_oficial errado da planilha; cliente já certo pelo CNPJ'})
    print(f'APLICADO: {len(mudar)} títulos com o nome corrigido.')
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--aplicar', action='store_true')
    sys.exit(main(ap.parse_args().aplicar))
