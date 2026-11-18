# -*- coding: utf-8 -*-
"""MEU CADASTRO (17/11/2026) — cada usuário vê e altera os PRÓPRIOS dados.

Anderson: "os usuarios todos eles tem que ter acesso ao seu cadastro... alterar, exemplo o miguel não acesso ao
cadastro dele". Os dados pessoais são os que a pessoa deu no link de cadastro (cadastro_pendente, ligado pelo
usuario_id); banco e Pix ficam em usuario_dados_bancarios (o mesmo de Configurações → Usuários e da chave Pix
das comissões). Nome, login e acesso continuam só com o administrador. Dado bancário nunca vai para o log:
só QUAIS campos mudaram.
"""
import re
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from utils.atividade import registrar
from utils.auth_helper import login_required
from utils.db_helper import execute_query

meu_cadastro = Blueprint('meu_cadastro', __name__)

PESSOAIS = ('cpf', 'data_nascimento', 'email_pessoal', 'celular_pessoal', 'celular_corporativo',
            'cep', 'logradouro', 'numero', 'complemento', 'bairro', 'cidade', 'estado')
BANCARIOS = ('banco_codigo', 'banco_nome', 'agencia', 'conta', 'conta_tipo', 'titular_nome', 'titular_cpf', 'pix_tipo', 'pix_chave')
PIX_TIPOS = [('CPF', 'CPF'), ('CNPJ', 'CNPJ'), ('TELEFONE', 'Celular'), ('EMAIL', 'E-mail'), ('ALEATORIA', 'Chave aleatória')]
CONTA_TIPOS = [('CORRENTE', 'Corrente'), ('POUPANCA', 'Poupança'), ('SALARIO', 'Salário'), ('PAGAMENTO', 'Pagamento')]
_LIM = {'cpf': 14, 'email_pessoal': 255, 'celular_pessoal': 20, 'celular_corporativo': 20, 'cep': 9, 'logradouro': 255,
        'numero': 20, 'complemento': 100, 'bairro': 100, 'cidade': 100, 'estado': 2, 'banco_codigo': 5, 'banco_nome': 120,
        'agencia': 15, 'conta': 25, 'titular_nome': 255, 'titular_cpf': 14, 'pix_chave': 140}


def _dados(uid):
    cad = execute_query('SELECT * FROM cadastro_pendente WHERE usuario_id = %s ORDER BY id DESC LIMIT 1', (uid,), fetch=True, fetch_one=True)
    banco = execute_query('SELECT * FROM usuario_dados_bancarios WHERE usuario_id = %s ORDER BY id DESC LIMIT 1', (uid,), fetch=True, fetch_one=True)
    return cad, banco or {}


def _txt(k):
    v = (request.form.get(k) or '').strip()
    return v[:_LIM[k]] if v and k in _LIM else (v or None)


@meu_cadastro.route('/meu-cadastro', methods=['GET', 'POST'])
@login_required
def index():
    uid = current_user.id
    cad, banco = _dados(uid)
    if request.method == 'POST':
        erros, mudou = [], []
        # ── dados pessoais (só quando a pessoa tem o cadastro do link) ──
        if cad:
            novo = {k: _txt(k) for k in PESSOAIS}
            if novo['cpf']:
                d = re.sub(r'\D', '', novo['cpf'])
                if len(d) != 11:
                    erros.append('CPF precisa ter 11 números.')
                novo['cpf'] = d
            if novo['data_nascimento']:
                try:
                    dn = date.fromisoformat(novo['data_nascimento'])
                    if dn.year < 1900 or dn > date.today():
                        raise ValueError
                    novo['data_nascimento'] = dn
                except ValueError:
                    erros.append('Data de nascimento inválida.')
            if novo['email_pessoal'] and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', novo['email_pessoal']):
                erros.append('E-mail pessoal inválido.')
            if novo['estado']:
                novo['estado'] = novo['estado'].upper()
            for k in ('celular_pessoal', 'celular_corporativo', 'cep'):
                if novo[k]:
                    novo[k] = re.sub(r'\D', '', novo[k])
            if not erros:
                mud = [k for k in PESSOAIS if (str(cad.get(k)) if cad.get(k) is not None else None) != (str(novo[k]) if novo[k] is not None else None)]
                if mud:
                    execute_query('UPDATE cadastro_pendente SET ' + ', '.join(f'{k} = %s' for k in mud) + ' WHERE id = %s',
                                  tuple(novo[k] for k in mud) + (cad['id'],), fetch=False)
                    mudou += mud
        # ── banco e Pix ──
        nb = {k: _txt(k) for k in BANCARIOS}
        if nb['pix_tipo'] not in (None,) + tuple(k for k, _ in PIX_TIPOS):
            nb['pix_tipo'] = None
        if nb['conta_tipo'] not in (None,) + tuple(k for k, _ in CONTA_TIPOS):
            nb['conta_tipo'] = None
        if nb['pix_chave'] and not nb['pix_tipo']:
            erros.append('Escolha o tipo da chave Pix.')
        if nb['pix_tipo'] == 'CPF' and nb['pix_chave'] and len(re.sub(r'\D', '', nb['pix_chave'])) != 11:
            erros.append('A chave Pix do tipo CPF precisa ter 11 números.')
        if nb['pix_tipo'] == 'EMAIL' and nb['pix_chave'] and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', nb['pix_chave']):
            erros.append('A chave Pix do tipo e-mail não parece um e-mail.')
        if not erros:
            mudb = [k for k in BANCARIOS if (banco.get(k) or None) != nb[k]]
            if mudb:
                if banco.get('id'):
                    execute_query('UPDATE usuario_dados_bancarios SET ' + ', '.join(f'{k} = %s' for k in mudb) + ', atualizado_por = %s WHERE id = %s',
                                  tuple(nb[k] for k in mudb) + (uid, banco['id']), fetch=False)
                else:
                    execute_query('INSERT INTO usuario_dados_bancarios (usuario_id, ' + ', '.join(BANCARIOS) + ', atualizado_por) VALUES (%s, '
                                  + ', '.join(['%s'] * len(BANCARIOS)) + ', %s)', (uid,) + tuple(nb[k] for k in BANCARIOS) + (uid,), fetch=False)
                mudou += mudb
        if erros:
            for e in erros:
                flash(e, 'danger')
        elif mudou:
            registrar('escrita.alterou_meu_cadastro', 'configuracoes', tabela='usuarios', registro_id=uid,
                      depois={'campos_alterados': sorted(mudou)})   # só os nomes dos campos: nunca os valores
            flash('Seu cadastro foi salvo.', 'success')
        else:
            flash('Nada mudou.', 'info')
        return redirect(url_for('meu_cadastro.index'))
    return render_template('meu_cadastro.html', cad=cad, banco=banco, PIX_TIPOS=PIX_TIPOS, CONTA_TIPOS=CONTA_TIPOS)
