# -*- coding: utf-8 -*-
"""E-mail do escritório num lugar só: o Departamento (14/11/2026).

Anderson: "e-mail tem que ser no Config / Departamento e lá coloco uma vez só o e-mail e pronto !!
O APP inteiro usaremos só e-mails de lá e vinculando para cada serviço".

- ``email_caixas``: uma linha por CAIXA (endereço). Senha cifrada, servidor, porta e o último teste.
  Vários departamentos podem usar a mesma caixa (Legalização, Atendimento e Comercial = legalizacao@):
  a senha é uma só.
- ``departamentos.email_nome``: o nome que o cliente vê no "De:" (Qualicontax - Financeiro).
- ``fin_cobranca_contas.departamento_id``: por qual departamento a conta de cobrança manda o boleto.

Dados: a senha da caixa financeiro@ que hoje está na conta de cobrança da Efí é COPIADA para
email_caixas (nada é apagado; as colunas antigas ficam como reserva) e as contas que usam
financeiro@ passam a apontar para o departamento Financeiro.

    python migrations/emails_02_caixas_14_11.py            # mostra o que faria
    python migrations/emails_02_caixas_14_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CRIAR = """
CREATE TABLE IF NOT EXISTS email_caixas (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  email           VARCHAR(120) NOT NULL,
  senha_cif       BLOB NULL COMMENT 'Fernet(DFE_CRYPTO_KEY)',
  host            VARCHAR(80) NOT NULL DEFAULT 'smtp.titan.email',
  porta           SMALLINT NOT NULL DEFAULT 465 COMMENT '465 SSL; 587 STARTTLS',
  testado_em      DATETIME NULL,
  teste_resultado VARCHAR(255) NULL,
  alterado_por    INT NULL,
  alterado_em     DATETIME NULL,
  criado_em       DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_email (email)
)"""


def colunas(tabela):
    return {r['Field'] for r in execute_query(f'SHOW COLUMNS FROM {tabela}', fetch=True)}


def tabela_existe(nome):
    return bool(execute_query('SHOW TABLES LIKE %s', (nome,), fetch=True))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    passos = []
    if not tabela_existe('email_caixas'):
        passos.append(('criar tabela email_caixas', CRIAR))
    if 'email_nome' not in colunas('departamentos'):
        passos.append(('departamentos.email_nome',
                       "ALTER TABLE departamentos ADD COLUMN email_nome VARCHAR(60) NULL "
                       "COMMENT 'nome que o cliente vê no De:'"))
    if 'departamento_id' not in colunas('fin_cobranca_contas'):
        passos.append(('fin_cobranca_contas.departamento_id',
                       "ALTER TABLE fin_cobranca_contas ADD COLUMN departamento_id INT NULL "
                       "COMMENT 'manda o boleto pela caixa deste departamento'"))
    for rot, _ in passos:
        print('DDL:', rot)

    # a caixa mais recente testada de cada endereço que já tem senha nas contas de cobrança
    origem = execute_query(
        """SELECT c.id, c.email_remetente, c.email_nome, c.email_senha_cif, c.email_host, c.email_porta,
                  c.email_testado_em, c.email_teste_resultado
             FROM fin_cobranca_contas c
            WHERE c.email_remetente IS NOT NULL AND c.email_senha_cif IS NOT NULL
            ORDER BY c.email_testado_em IS NULL, c.email_testado_em DESC""", fetch=True) or []
    vistas = {}
    for o in origem:
        vistas.setdefault(o['email_remetente'].strip().lower(), o)
    deps = execute_query('SELECT id, nome, email FROM departamentos', fetch=True) or []
    dep_do_email = {}
    for d in deps:
        if d['email']:
            dep_do_email.setdefault(d['email'].strip().lower(), d)
    for em, o in vistas.items():
        d = dep_do_email.get(em)
        print(f'DADO: caixa {em} (senha copiada da conta de cobrança {o["id"]}, testada {o["email_testado_em"]})'
              f' -> departamento {d["nome"] if d else "NENHUM (fica só a caixa)"}')
    contas = execute_query('SELECT id, apelido, email_remetente FROM fin_cobranca_contas '
                           'WHERE email_remetente IS NOT NULL', fetch=True) or []
    for c in contas:
        d = dep_do_email.get(c['email_remetente'].strip().lower())
        print(f'DADO: conta de cobrança {c["id"]} ({c["apelido"]}) envia por -> '
              f'{d["nome"] if d else "SEM DEPARTAMENTO com " + c["email_remetente"]}')

    if not a.apply:
        print('\n(nada gravado; rode com --apply)')
        sys.exit(0)

    for rot, sql in passos:
        execute_query(sql, fetch=False)
        print('feito:', rot)
    for em, o in vistas.items():
        execute_query(
            """INSERT INTO email_caixas (email, senha_cif, host, porta, testado_em, teste_resultado, alterado_em)
               VALUES (%s, %s, %s, %s, %s, %s, NOW())
               ON DUPLICATE KEY UPDATE email = email""",
            (em, o['email_senha_cif'], o['email_host'] or 'smtp.titan.email', o['email_porta'] or 465,
             o['email_testado_em'], o['email_teste_resultado']), fetch=False)
        d = dep_do_email.get(em)
        if d and o.get('email_nome'):
            execute_query('UPDATE departamentos SET email_nome = %s WHERE id = %s AND email_nome IS NULL',
                          (o['email_nome'], d['id']), fetch=False)
    for c in contas:
        d = dep_do_email.get(c['email_remetente'].strip().lower())
        if d:
            execute_query('UPDATE fin_cobranca_contas SET departamento_id = %s WHERE id = %s '
                          'AND departamento_id IS NULL', (d['id'], c['id']), fetch=False)
    print('dados copiados.')
