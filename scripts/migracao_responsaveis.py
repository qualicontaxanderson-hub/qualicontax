# -*- coding: utf-8 -*-
"""Migração Acessórias+Conexa — PARTE 5c: responsáveis do escritório por empresa e departamento (05/11/2026).

Aba ``responsaveis`` da planilha: 2.647 linhas (numero_oficial, departamento, responsavel), 22 nomes.
Decisão do Anderson (05/11, pela lista de funcionários do Acessórias):
  * quem está na lista e não tem usuário no app GANHA usuário (assistente, senha pendente):
    Alessandra Pereira, Bruna Schumann, Carol Santos ("Caroline"), Julian Amaral, Talita Miyazaki;
  * quem NÃO está mais na lista saiu da casa — Fernanda Mendes, Isabella Tomia, Danielle de Sousa,
    Ivonne Gabrielle, Gabriel Duque — e as marcações delas ficam SEM responsável, para redistribuir
    na ficha (cartão "Responsáveis no escritório").

Idempotente: usuário só é criado se não houver outro com o mesmo login; responsável só grava quando
muda (INSERT ... ON DUPLICATE KEY UPDATE) e o log sai por EMPRESA (uma linha com o que mudou).

    python scripts/migracao_responsaveis.py --json migracao.json                       # dry-run
    python scripts/migracao_responsaveis.py --json migracao.json --apply --csv saida.csv
"""
import argparse
import csv
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'

# nome no Acessórias -> login no app (o id é resolvido no banco)
NOME_PARA_LOGIN = {
    'Alessandra Pereira': 'alessandra',
    'Rodrigo Silva': 'rodrigo',
    'Gabriel Mendes': 'gabriel',
    'Bruna Schumann': 'bruna.schumann',
    'Graziella de Almeida': 'graziella',
    'Henrique Vicentini': 'henrique',
    'Julian Amaral': 'julian',
    'Bruna Lopes': 'bruna',
    'Sérgio Camara': 'sergio',
    'Ester de Almeida': 'ester2',
    'Talita Miyazaki': 'talita',
    'Karina Sousa': 'karina.sousa',
    'Caroline': 'carol',
    'Guylhermmy Ponciano': 'guylhermmy',
    'Anderson Antunes': 'anderson',
    'Albert Antunes': 'albert',
    'Melchisedech Castilho Oliveira': 'melchisedech',
}
SAIRAM = {'Fernanda Mendes', 'Isabella Tomia', 'Danielle de Sousa', 'Ivonne Gabrielle', 'Gabriel Duque'}

# usuários novos: (login, nome, e-mail, departamento principal)
NOVOS = [
    ('alessandra', 'Alessandra Pereira', 'alessandra@qualicontax.com.br', 'Financeiro'),
    ('bruna.schumann', 'Bruna Schumann', 'bruna.schumann@qualicontax.com.br', 'Pessoal'),
    ('carol', 'Carol Santos', 'carol@qualicontax.com.br', 'Pessoal'),
    ('julian', 'Julian Amaral', 'julian@qualicontax.com.br', 'Fiscal'),
    ('talita', 'Talita Miyazaki', 'talita@qualicontax.com.br', 'Fiscal'),
]


def ler(caminho):
    d = json.load(open(caminho, encoding='utf-8'))
    r = d['responsaveis']
    return [dict(zip(r[0], x)) for x in r[1:]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--csv')
    a = ap.parse_args()
    modo = 'APLICANDO' if a.apply else 'DRY-RUN'

    deps = {d['nome']: d['id'] for d in execute_query('SELECT id, nome FROM departamentos', fetch=True) or []}
    if not execute_query("SHOW TABLES LIKE 'clientes_responsaveis'", fetch=True):
        print('tabela clientes_responsaveis não existe — rode migrations/migracao_04_responsaveis.py --apply')
        return

    # 1. usuários novos
    usuarios = {u['login']: u for u in execute_query('SELECT id, login, nome FROM usuarios', fetch=True) or []}
    print(f'{modo}: usuários')
    for login, nome, email, depto in NOVOS:
        if login in usuarios:
            print(f'  já existe: {login} ({usuarios[login]["nome"]})')
            continue
        print(f'  criar: {login} — {nome} <{email}> depto {depto} (assistente, senha pendente)')
        if a.apply:
            uid = execute_query(
                """INSERT INTO usuarios (nome, email, login, senha_hash, senha_pendente, tipo_usuario, classe_conta,
                                         situacao, departamento_id)
                   VALUES (%s, %s, %s, NULL, 1, 'ASSISTENTE', 'FUNCIONARIO', 'ATIVO', %s)""",
                (nome, email, login, deps.get(depto)), fetch=False)
            usuarios[login] = {'id': uid, 'login': login, 'nome': nome}
            registrar_agente('escrita.criou_usuario_migracao', 'configuracoes', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='usuarios', registro_id=uid,
                             depois={'nome': nome, 'login': login, 'email': email, 'departamento': depto,
                                     'tipo': 'ASSISTENTE', 'senha_pendente': 1, 'fonte': 'Acessórias (responsáveis)'})

    faltam = [n for n, l in NOME_PARA_LOGIN.items() if l not in usuarios]
    if faltam:
        print('nomes do Acessórias cujo login não existe no app:', faltam)
        if a.apply:
            return

    # 2. responsáveis
    clientes = {str(c['numero_cliente']): c for c in execute_query(
        "SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE avulso = 0 AND numero_cliente REGEXP '^[0-9]+$'", fetch=True) or []}
    atuais = {}
    for r in execute_query('SELECT cliente_id, departamento_id, usuario_id FROM clientes_responsaveis', fetch=True) or []:
        atuais[(r['cliente_id'], r['departamento_id'])] = r['usuario_id']
    nomes_usuario = {u['id']: u['nome'] for u in usuarios.values()}

    linhas = ler(a.json)
    por_empresa = {}
    for x in linhas:
        por_empresa.setdefault(str(x['numero_oficial']), []).append(x)
    tot, saida, nomes_desconhecidos = Counter(), [], Counter()
    print(f'{modo}: {len(linhas)} marcações em {len(por_empresa)} empresas')
    for num, marcas in por_empresa.items():
        cli = clientes.get(num)
        if not cli:
            tot['empresa não está no app (número)'] += len(marcas)
            for m in marcas:
                saida.append((num, '', m['departamento'], m['responsavel'], '', 'empresa não está no app'))
            continue
        mudou = {}
        for m in marcas:
            depto, nome = m['departamento'], (m['responsavel'] or '').strip()
            did = deps.get(depto)
            if not did:
                tot['departamento desconhecido'] += 1
                saida.append((num, cli['nome_razao_social'][:40], depto, nome, '', 'departamento desconhecido'))
                continue
            if nome in SAIRAM:
                tot['sem responsável (saiu da casa)'] += 1
                saida.append((num, cli['nome_razao_social'][:40], depto, nome, '', 'saiu da casa: fica sem responsável'))
                continue
            login = NOME_PARA_LOGIN.get(nome)
            if not login or login not in usuarios:
                tot['nome não mapeado'] += 1
                nomes_desconhecidos[nome] += 1
                saida.append((num, cli['nome_razao_social'][:40], depto, nome, '', 'nome não mapeado'))
                continue
            uid = usuarios[login]['id']
            antes = atuais.get((cli['id'], did))
            if antes == uid:
                tot['já estava igual'] += 1
                saida.append((num, cli['nome_razao_social'][:40], depto, nome, usuarios[login]['nome'], 'já estava igual'))
                continue
            tot['gravado' if antes is None else 'trocado'] += 1
            saida.append((num, cli['nome_razao_social'][:40], depto, nome, usuarios[login]['nome'], 'gravado' if antes is None else 'trocado'))
            mudou[depto] = {'antes': nomes_usuario.get(antes), 'depois': usuarios[login]['nome']}
            if a.apply:
                execute_query(
                    """INSERT INTO clientes_responsaveis (cliente_id, departamento_id, usuario_id)
                       VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE usuario_id = VALUES(usuario_id)""",
                    (cli['id'], did, uid), fetch=False)
        if mudou and a.apply:
            registrar_agente('escrita.responsavel_acessorias_migracao', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='clientes', registro_id=cli['id'],
                             depois={'numero': num, 'empresa': cli['nome_razao_social'], 'responsaveis': mudou, 'fonte': 'Acessórias'})
    print('TOTAIS:')
    for k, v in tot.most_common():
        print(f'  {v:5d} {k}')
    if nomes_desconhecidos:
        print('nomes não mapeados:', dict(nomes_desconhecidos))
    if a.csv:
        with open(a.csv, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['nº', 'empresa', 'departamento', 'responsável no Acessórias', 'usuário no app', 'ação'])
            w.writerows(saida)
        print('CSV:', a.csv)


if __name__ == '__main__':
    main()
