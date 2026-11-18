# -*- coding: utf-8 -*-
"""Contas para receber de um usuário (18/11/2026).

Anderson: "insira um campo para cadastrar um outro pra receber! A pessoa cadastra o nome e CPF de alguém e, no
usuário, escolhe qual ficará para emissão dos pagamentos, inclusive porque alguns gostam de receber na PJ".
Cada pessoa pode ter várias linhas em ``usuario_dados_bancarios`` (a própria conta, a de outra pessoa, a da
empresa) e UMA delas recebe os pagamentos (``recebe_pagamentos`` = 1): é a que sai no pedido de comissão, com o
nome do TITULAR no Pix (o banco do pagador confere o nome com o dono da chave; com nome diferente ele recusa).
Dado bancário nunca vai para o log: só quais campos mudaram.
"""
import re

from utils.db_helper import execute_query

CAMPOS = ('apelido', 'banco_codigo', 'banco_nome', 'agencia', 'conta', 'conta_tipo', 'titular_nome', 'titular_cpf', 'pix_tipo', 'pix_chave')
PIX_TIPOS = [('CPF', 'CPF'), ('CNPJ', 'CNPJ'), ('TELEFONE', 'Celular'), ('EMAIL', 'E-mail'), ('ALEATORIA', 'Chave aleatória')]
CONTA_TIPOS = [('CORRENTE', 'Corrente'), ('POUPANCA', 'Poupança'), ('SALARIO', 'Salário'), ('PAGAMENTO', 'Pagamento')]
LIM = {'apelido': 60, 'banco_codigo': 5, 'banco_nome': 120, 'agencia': 15, 'conta': 25, 'titular_nome': 255, 'titular_cpf': 18, 'pix_chave': 140}


def contas_de(usuario_id):
    """Todas as contas da pessoa, a escolhida primeiro."""
    return execute_query('SELECT * FROM usuario_dados_bancarios WHERE usuario_id = %s ORDER BY recebe_pagamentos DESC, id',
                         (usuario_id,), fetch=True) or []


def escolhida(usuario_id):
    """A conta que recebe os pagamentos ({} se não há nenhuma). Sem marcação, vale a mais nova (como era antes)."""
    r = execute_query("""SELECT * FROM usuario_dados_bancarios WHERE usuario_id = %s
                         ORDER BY recebe_pagamentos DESC, id DESC LIMIT 1""", (usuario_id,), fetch=True, fetch_one=True)
    return r or {}


def escolher(usuario_id, conta_id, por):
    """Marca UMA conta da pessoa como a que recebe. Devolve True se existia."""
    if not execute_query('SELECT id FROM usuario_dados_bancarios WHERE id = %s AND usuario_id = %s', (conta_id, usuario_id), fetch=True):
        return False
    execute_query('UPDATE usuario_dados_bancarios SET recebe_pagamentos = (id = %s), atualizado_por = %s WHERE usuario_id = %s',
                  (conta_id, por, usuario_id), fetch=False)
    return True


def validar(c):
    """Lista de erros (vazia = pode salvar). ``c`` com as chaves de CAMPOS."""
    erros = []
    if c.get('pix_tipo') not in (None, '') + tuple(k for k, _ in PIX_TIPOS):
        c['pix_tipo'] = None
    if c.get('conta_tipo') not in (None, '') + tuple(k for k, _ in CONTA_TIPOS):
        c['conta_tipo'] = None
    if c.get('pix_chave') and not c.get('pix_tipo'):
        erros.append('Escolha o tipo da chave Pix.')
    d = re.sub(r'\D', '', c.get('pix_chave') or '')
    if c.get('pix_tipo') == 'CPF' and c.get('pix_chave') and len(d) != 11:
        erros.append('A chave Pix do tipo CPF precisa ter 11 números.')
    if c.get('pix_tipo') == 'CNPJ' and c.get('pix_chave') and len(d) != 14:
        erros.append('A chave Pix do tipo CNPJ precisa ter 14 números.')
    if c.get('pix_tipo') == 'EMAIL' and c.get('pix_chave') and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', c['pix_chave']):
        erros.append('A chave Pix do tipo e-mail não parece um e-mail.')
    doc = re.sub(r'\D', '', c.get('titular_cpf') or '')
    if doc and len(doc) not in (11, 14):
        erros.append('O CPF ou CNPJ do titular precisa ter 11 ou 14 números.')
    if c.get('pix_chave') and not (c.get('titular_nome') or '').strip():
        erros.append('Escreva o nome do titular da conta: é ele que vai no Pix.')
    return erros


def salvar(usuario_id, conta_id, c, por):
    """Cria ou altera uma conta da pessoa. Devolve (id, campos_que_mudaram). A 1ª conta já nasce escolhida."""
    c = {k: ((c.get(k) or '').strip()[:LIM.get(k, 255)] or None) if isinstance(c.get(k), str) else (c.get(k) or None) for k in CAMPOS}
    if conta_id:
        atual = execute_query('SELECT * FROM usuario_dados_bancarios WHERE id = %s AND usuario_id = %s', (conta_id, usuario_id), fetch=True, fetch_one=True)
        if not atual:
            return None, []
        mud = [k for k in CAMPOS if (atual.get(k) or None) != c[k]]
        if mud:
            execute_query('UPDATE usuario_dados_bancarios SET ' + ', '.join(f'{k} = %s' for k in mud) + ', atualizado_por = %s WHERE id = %s',
                          tuple(c[k] for k in mud) + (por, conta_id), fetch=False)
        return conta_id, mud
    if not any(c.values()):
        return None, []
    primeira = not contas_de(usuario_id)
    novo = execute_query('INSERT INTO usuario_dados_bancarios (usuario_id, ' + ', '.join(CAMPOS) + ', recebe_pagamentos, atualizado_por) VALUES (%s, '
                         + ', '.join(['%s'] * len(CAMPOS)) + ', %s, %s)', (usuario_id,) + tuple(c[k] for k in CAMPOS) + (1 if primeira else 0, por), fetch=False)
    return novo, list(CAMPOS)


def excluir(usuario_id, conta_id):
    """Apaga uma conta que NÃO é a que recebe (a escolhida só sai depois de outra ser escolhida)."""
    # execute_query devolve True em UPDATE/DELETE mesmo sem linha: conferir ANTES (ver utils/db_helper.py)
    if not execute_query('SELECT id FROM usuario_dados_bancarios WHERE id = %s AND usuario_id = %s AND recebe_pagamentos = 0',
                         (conta_id, usuario_id), fetch=True):
        return False
    execute_query('DELETE FROM usuario_dados_bancarios WHERE id = %s AND usuario_id = %s AND recebe_pagamentos = 0', (conta_id, usuario_id), fetch=False)
    return True


def rotulo(c, nome_usuario=''):
    """'Conta da Elizangela · Elizangela Rocha Cristino · Banco Inter · chave aleatória'."""
    if not c:
        return ''
    partes = []
    if c.get('apelido'):
        partes.append(c['apelido'])
    if c.get('titular_nome'):
        partes.append(c['titular_nome'])
    if c.get('banco_nome'):
        partes.append(re.sub(r'^\d{3}\s*-\s*', '', c['banco_nome']).title() if c['banco_nome'].isupper() else c['banco_nome'])
    if c.get('pix_tipo'):
        partes.append(dict(PIX_TIPOS).get(c['pix_tipo'], c['pix_tipo']).lower() if c['pix_tipo'] not in ('CPF', 'CNPJ') else c['pix_tipo'])
    return ' · '.join(partes)


def titular_e_outro(c, nome_usuario):
    """True quando a conta escolhida está em nome de outra pessoa ou empresa (1º e último nome diferentes)."""
    t = (c.get('titular_nome') or '').strip().upper().split()
    u = (nome_usuario or '').strip().upper().split()
    if not t or not u:
        return False
    return not (t[0] == u[0] and t[-1] == u[-1])
