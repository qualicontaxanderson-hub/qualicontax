# -*- coding: utf-8 -*-
"""Inscrições estaduais com TIPO (09/11/2026) — ver models/inscricao_estadual.py.

1. Colunas novas em clientes_inscricoes_estaduais: tipo (NORMAL/ST/RURAL), propriedade, municipio,
   conferir, conferir_motivo.
2. Isenta vira só ``clientes.inscricao_estadual = 'ISENTO'``: as linhas "ISENTO/ISENTA" saem da tabela
   (o cadastro vazio com linha isenta ganha o ISENTO).
3. Cadastro com IE e sem linha na tabela: a linha entra (Normal, principal, UF do endereço).
4. CONFERIR (vai ao A CADASTRAR): IE da Acessórias diferente da do app; "IE" que é um CNPJ; empresa
   isenta com IE na Acessórias; IE principal de outra UF que não a do endereço (Petroriente).
5. Petrogoiás (515): a IE de GO é a da empresa; as 8 de outros estados viram Substituto Tributário
   (Anderson: "tem uma que seria da própria empresa e as demais substituto").

    python scripts/ie_tipos_09_11.py            # simulação
    python scripts/ie_tipos_09_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query  # noqa: E402
from utils.atividade import registrar_agente  # noqa: E402
from models.inscricao_estadual import dig, eh_isento, uf_do_endereco  # noqa: E402

AUTOR = (62, 'Anderson Antunes Vieira (pedido no chat, aplicado pelo Claude)', 'anderson')
COLS = [('tipo', "VARCHAR(10) NOT NULL DEFAULT 'NORMAL' AFTER ie"),
        ('propriedade', 'VARCHAR(120) NULL AFTER tipo'),
        ('municipio', 'VARCHAR(80) NULL AFTER propriedade'),
        ('conferir', 'TINYINT(1) NOT NULL DEFAULT 0 AFTER principal'),
        ('conferir_motivo', 'VARCHAR(160) NULL AFTER conferir')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    ex = {r['COLUMN_NAME'] for r in execute_query(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'clientes_inscricoes_estaduais'", fetch=True)}
    falta = [c for c in COLS if c[0] not in ex]
    print('colunas a criar:', [c[0] for c in falta])
    if a.apply:
        for nome, ddl in falta:
            execute_query(f'ALTER TABLE clientes_inscricoes_estaduais ADD COLUMN {nome} {ddl}', fetch=False)
    tem_cols = a.apply or not falta

    linhas = execute_query("SELECT i.*, c.inscricao_estadual AS cad, c.numero_cliente AS num, c.nome_razao_social AS nome "
                           "FROM clientes_inscricoes_estaduais i JOIN clientes c ON c.id = i.cliente_id", fetch=True) or []
    # 2) isentas
    isentas = [x for x in linhas if eh_isento(x['ie'])]
    print('linhas ISENTO/ISENTA que saem:', len(isentas),
          '| cadastros que ganham ISENTO:', sum(1 for x in isentas if not (x['cad'] or '').strip()))
    # 3) cadastro com IE sem linha
    sem_linha = execute_query("SELECT c.id, c.numero_cliente num, c.inscricao_estadual ie FROM clientes c "
                              "WHERE COALESCE(c.inscricao_estadual,'') <> '' AND c.inscricao_estadual NOT IN ('ISENTO','ISENTA') "
                              "AND NOT EXISTS (SELECT 1 FROM clientes_inscricoes_estaduais i WHERE i.cliente_id = c.id)", fetch=True) or []
    print('cadastros com IE e sem linha (entram):', [x['num'] for x in sem_linha])
    # 4) conferir
    reais = [x for x in linhas if not eh_isento(x['ie'])]
    por_cli = {}
    for x in reais:
        por_cli.setdefault(x['cliente_id'], []).append(x)
    conferir = {}   # id da linha -> motivo
    for cid, ls in por_cli.items():
        cad = ls[0]['cad']
        for x in ls:
            if len(dig(x['ie'])) == 14 and '/' in x['ie'] or (len(dig(x['ie'])) == 14 and dig(x['ie']) == dig(execute_query(
                    "SELECT cpf_cnpj FROM clientes WHERE id = %s", (cid,), fetch=True, fetch_one=True)['cpf_cnpj'])):
                conferir[x['id']] = 'este número é o CNPJ, não a inscrição estadual'
        if cid == 5:      # Petrogoiás: tratada no passo 5
            continue
        if eh_isento(cad):
            for x in ls:
                conferir.setdefault(x['id'], 'o cadastro está como ISENTO, mas a Acessórias tinha esta IE')
            continue
        if len(ls) > 1:
            for x in ls:
                if not x['principal']:
                    conferir.setdefault(x['id'], 'a Acessórias tinha esta IE e o cadastro tem outra: qual vale?')
        for x in ls:
            ufe = uf_do_endereco(cid)
            if x['principal'] and ufe and x['uf'] and x['uf'] != ufe:
                conferir.setdefault(x['id'], f'IE principal de {x["uf"]}, mas o endereço é de {ufe}')
    for iid, m in sorted(conferir.items()):
        x = next(l for l in linhas if l['id'] == iid)
        print(f'  conferir: {x["num"]} {x["nome"][:30]} | {x["uf"]} {x["ie"]} -> {m}')
    # 5) Petrogoiás
    st = [x for x in reais if x['cliente_id'] == 5 and x['uf'] != 'GO']
    print('Petrogoiás: viram Substituto Tributário:', [f"{x['uf']} {x['ie']}" for x in st])

    if not a.apply:
        print('SIMULAÇÃO. Repita com --apply.')
        return
    for x in isentas:
        if not (x['cad'] or '').strip():
            execute_query("UPDATE clientes SET inscricao_estadual = 'ISENTO' WHERE id = %s", (x['cliente_id'],))
        execute_query("DELETE FROM clientes_inscricoes_estaduais WHERE id = %s", (x['id'],))
    for x in sem_linha:
        execute_query("INSERT IGNORE INTO clientes_inscricoes_estaduais (cliente_id, uf, ie, tipo, principal, origem) "
                      "VALUES (%s, %s, %s, 'NORMAL', 1, 'app')", (x['id'], uf_do_endereco(x['id']), x['ie'][:20]))
    for iid, m in conferir.items():
        execute_query("UPDATE clientes_inscricoes_estaduais SET conferir = 1, conferir_motivo = %s WHERE id = %s", (m, iid))
    for x in st:
        execute_query("UPDATE clientes_inscricoes_estaduais SET tipo = 'ST', principal = 0 WHERE id = %s", (x['id'],))
    if st:
        registrar_agente('escrita.alterou_ie', 'cadastros', *AUTOR, tabela='clientes', registro_id=5,
                         depois={'cliente_id': 5, 'inscricoes': {f"{x['uf']} {x['ie']}": {'antes': 'Normal', 'depois': 'Substituto Tributário'} for x in st}})
    for iid in conferir:
        x = next(l for l in linhas if l['id'] == iid)
        registrar_agente('escrita.alterou_ie', 'cadastros', *AUTOR, tabela='clientes', registro_id=x['cliente_id'],
                         depois={'cliente_id': x['cliente_id'], 'inscricoes': {f"{x['uf']} {x['ie']}": {'antes': None, 'depois': 'Conferir: ' + conferir[iid]}}})
    print('feito.')


if __name__ == '__main__':
    main()
