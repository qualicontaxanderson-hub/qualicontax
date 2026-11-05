# -*- coding: utf-8 -*-
"""Migração Acessórias — limpeza dos contatos "só nome" (05/11/2026).

Anderson, vendo a aba Contatos: "Esses que estão dessa forma nem tem que aparecer aqui... Só quem tem
e-mail e celular ou um ou outro." A importação (scripts/migracao_contatos.py) tinha criado 71 contatos
sem e-mail, sem celular e sem telefone. Este script apaga SÓ esses: contato ativo, sem os três campos,
criado pela migração (há log escrita.contato_acessorias_migracao com o id dele). Cada exclusão vai
para o log (escrita.excluiu_contato_migracao) com nome, cargo, empresa e departamentos, para poder
refazer na mão se preciso.

    python scripts/migracao_contatos_so_nome.py            # dry-run
    python scripts/migracao_contatos_so_nome.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'

SQL = """
SELECT c.id, c.cliente_id, c.nome, c.cargo, cl.numero_cliente, cl.nome_razao_social,
       (SELECT GROUP_CONCAT(d.nome ORDER BY d.nome SEPARATOR ', ') FROM contatos_departamentos cd
          JOIN departamentos d ON d.id = cd.departamento_id WHERE cd.contato_id = c.id) AS departamentos
  FROM contatos_clientes c JOIN clientes cl ON cl.id = c.cliente_id
 WHERE c.ativo = 1
   AND (c.email IS NULL OR c.email = '') AND (c.celular IS NULL OR c.celular = '')
   AND (c.telefone IS NULL OR c.telefone = '')
   AND EXISTS (SELECT 1 FROM logs_sistema l
                WHERE l.acao = 'escrita.contato_acessorias_migracao' AND l.registro_id = c.id)
 ORDER BY cl.numero_cliente + 0, c.id
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    lista = execute_query(SQL, fetch=True) or []
    print(f"{'APLICANDO' if a.apply else 'DRY-RUN'}: {len(lista)} contatos só com nome (vindos da migração)")
    for c in lista:
        print(f"  {c['numero_cliente']:>5} {c['nome_razao_social'][:38]:38} | {c['nome'][:30]:30} | {c['cargo'] or '':18} | {c['departamentos'] or '(nenhum)'}")
        if a.apply:
            execute_query('DELETE FROM contatos_departamentos WHERE contato_id = %s', (c['id'],), fetch=False)
            execute_query('DELETE FROM contatos_clientes WHERE id = %s', (c['id'],), fetch=False)
            registrar_agente('escrita.excluiu_contato_migracao', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='contatos_clientes', registro_id=c['id'],
                             depois={'excluido': {'cliente_id': c['cliente_id'], 'numero': c['numero_cliente'],
                                                  'empresa': c['nome_razao_social'], 'contato': c['nome'], 'cargo': c['cargo'],
                                                  'departamentos': c['departamentos']},
                                     'motivo': 'contato só com nome (sem e-mail, celular e telefone) — Anderson 05/11/2026'})
    print('FIM')


if __name__ == '__main__':
    main()
