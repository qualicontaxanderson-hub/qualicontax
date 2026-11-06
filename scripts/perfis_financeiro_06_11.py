# -*- coding: utf-8 -*-
"""Perfis do financeiro para quem lança serviço e cadastra honorários (Anderson, 06/11/2026: "Faça assim mas
analisarei isso sobre se vamos criar perfis específicos ou se vamos individual").

Os perfis que existiam (FISCAL - PADRÃO A, IMPLANTAÇÃO) são de 9 pessoas; marcar neles daria lançamento e comissão
a todo mundo. Por isso nascem DOIS perfis só com as permissões novas, e as pessoas ganham o perfil A MAIS dos que já
têm. Tudo reversível em Config → Perfis / Usuários.

  COMERCIAL - LANÇAMENTOS  : financeiro.lancamentos + financeiro.honorarios_cadastrar  -> Gabriel (19), Melke (65), Guilherme (69)
  HONORÁRIOS - CADASTRO    : financeiro.honorarios_cadastrar                           -> Alessandra (92)

Idempotente: roda de novo sem duplicar. Uso: python scripts/perfis_financeiro_06_11.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
PERFIS = {
    'COMERCIAL - LANÇAMENTOS': {
        'descricao': 'Lança serviço avulso (vai para aprovação), vê as próprias comissões e cadastra honorários da empresa que cadastrou',
        'permissoes': ['financeiro.lancamentos', 'financeiro.honorarios_cadastrar'],
        'usuarios': [19, 65, 69],
    },
    'HONORÁRIOS - CADASTRO': {
        'descricao': 'Cadastra honorários da empresa que cadastrou ou que lhe foi passada por link (vai para aprovação)',
        'permissoes': ['financeiro.honorarios_cadastrar'],
        'usuarios': [92],
    },
}


def main():
    for nome, p in PERFIS.items():
        ex = execute_query('SELECT id FROM perfis_acesso WHERE nome = %s', (nome,), fetch=True, fetch_one=True)
        if ex:
            pid = ex['id']
            print('perfil já existe:', nome, pid)
        else:
            pid = execute_query('INSERT INTO perfis_acesso (nome, descricao, situacao) VALUES (%s, %s, %s)',
                                (nome, p['descricao'], 'ATIVO'), fetch=False)
            print('perfil criado:', nome, pid)
            registrar_agente('perfil_criado', 'configuracoes', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='perfis_acesso', registro_id=pid, depois={'nome': nome, 'permissoes': p['permissoes']})
        for cod in p['permissoes']:
            if not execute_query('SELECT 1 FROM perfil_permissoes WHERE perfil_id = %s AND permissao_codigo = %s', (pid, cod), fetch=True):
                execute_query('INSERT INTO perfil_permissoes (perfil_id, permissao_codigo) VALUES (%s, %s)', (pid, cod), fetch=False)
        for uid in p['usuarios']:
            u = execute_query('SELECT id, nome FROM usuarios WHERE id = %s', (uid,), fetch=True, fetch_one=True)
            if not u:
                print('  usuário %s não existe' % uid)
                continue
            if execute_query('SELECT 1 FROM usuario_perfis WHERE usuario_id = %s AND perfil_id = %s', (uid, pid), fetch=True):
                print('  já tinha:', u['nome'])
                continue
            execute_query('INSERT INTO usuario_perfis (usuario_id, perfil_id) VALUES (%s, %s)', (uid, pid), fetch=False)
            registrar_agente('usuario_perfil_adicionado', 'configuracoes', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='usuario_perfis', registro_id=uid, depois={'usuario': u['nome'], 'perfil': nome})
            print('  perfil dado a:', u['nome'])
    print('pronto.')


if __name__ == '__main__':
    main()
