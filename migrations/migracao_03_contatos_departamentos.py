# -*- coding: utf-8 -*-
"""Migração Acessórias+Conexa — PARTE 5a: departamento VISITA e a ligação contato x departamento (05/11/2026).

Decisões do Anderson (05/11): o Acessórias é o padrão dos contatos e traz SIM/NÃO por departamento
(Contábil, Fiscal, Pessoal, Financeiro, Legalização, Certificado Digital, Visita); "já é para deixar
tudo cadastrado"; celular do contato passa a ser obrigatório. Até aqui o contato tinha um texto livre
``departamento`` e um JSON ``areas_atendimento`` sem ligação com a tabela ``departamentos``.

Faz (idempotente):
  1. INSERT do departamento 'Visita' se não existir;
  2. CREATE TABLE contatos_departamentos (contato_id, departamento_id) — quem recebe o quê;
  3. converte as áreas antigas dos contatos já existentes (areas_atendimento JSON) para a ligação nova,
     só para contato que ainda não tem nenhuma ligação: todas as 6 áreas antigas = todos os 7
     departamentos; PESSOAL = Pessoal; FINANCEIRO = Financeiro; FISCAL/TRIBUTARIO = Fiscal;
     CONTABIL = Contábil; SOCIETARIO = Legalização.

    python migrations/migracao_03_contatos_departamentos.py            # dry-run
    python migrations/migracao_03_contatos_departamentos.py --apply
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CREATE = """
CREATE TABLE IF NOT EXISTS contatos_departamentos (
    contato_id INT NOT NULL,
    departamento_id INT NOT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (contato_id, departamento_id),
    KEY idx_cd_departamento (departamento_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='quais departamentos cada contato do cliente recebe (migracao Acessorias 11/2026)'
"""
AREAS_PARA_DEPTOS = {
    'FINANCEIRO': ['Financeiro'], 'FISCAL': ['Fiscal'], 'TRIBUTARIO': ['Fiscal'], 'CONTABIL': ['Contábil'],
    'PESSOAL': ['Pessoal'], 'SOCIETARIO': ['Legalização'],
}
TODOS = ['Contábil', 'Fiscal', 'Pessoal', 'Financeiro', 'Legalização', 'Certificado Digital', 'Visita']


def tabela_existe(nome):
    return bool(execute_query('SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s',
                              (nome,), fetch=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    rot = 'APLICANDO' if a.apply else 'DRY-RUN'

    deps = {d['nome']: d['id'] for d in execute_query('SELECT id, nome FROM departamentos', fetch=True) or []}
    if 'Visita' in deps:
        print(f'{rot}: departamento Visita já existe (id {deps["Visita"]})')
    else:
        print(f'{rot}: cria o departamento Visita')
        if a.apply:
            deps['Visita'] = execute_query("INSERT INTO departamentos (nome, descricao, ativo) VALUES ('Visita', 'Visita ao cliente (contatos que recebem aviso de visita)', 1)")

    if tabela_existe('contatos_departamentos'):
        print(f'{rot}: tabela contatos_departamentos já existe')
    else:
        print(f'{rot}: cria a tabela contatos_departamentos')
        if a.apply:
            execute_query(CREATE, fetch=False)

    contatos = execute_query("""SELECT c.id, c.areas_atendimento,
                                       (SELECT COUNT(*) FROM contatos_departamentos d WHERE d.contato_id = c.id) AS n
                                  FROM contatos_clientes c""", fetch=True) if tabela_existe('contatos_departamentos') else \
        [dict(r, n=0) for r in execute_query('SELECT id, areas_atendimento FROM contatos_clientes', fetch=True) or []]
    conv, sem = 0, 0
    for c in contatos or []:
        if c['n']:
            continue
        try:
            areas = json.loads(c['areas_atendimento']) if c['areas_atendimento'] else []
        except ValueError:
            areas = []
        if not areas:
            sem += 1
            continue
        nomes = TODOS if len(set(areas)) >= 6 else sorted({d for a_ in areas for d in AREAS_PARA_DEPTOS.get(a_, [])})
        conv += 1
        if a.apply:
            for n in nomes:
                if n in deps:
                    execute_query('INSERT IGNORE INTO contatos_departamentos (contato_id, departamento_id) VALUES (%s, %s)',
                                  (c['id'], deps[n]), fetch=False)
    print(f'{rot}: contatos existentes convertidos: {conv} | sem área (ficam sem departamento): {sem}')


if __name__ == '__main__':
    main()
