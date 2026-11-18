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

from utils import contas_recebimento as cr
from utils.atividade import registrar
from utils.auth_helper import login_required
from utils.db_helper import execute_query

meu_cadastro = Blueprint('meu_cadastro', __name__)

# 18/11/2026: o aparelho de quem entra no sistema fica marcado (cookie de 1 ano), para a página do cliente (/c/<token>)
# saber que quem abriu é da equipe — ver utils/equipe_aparelho.py
from utils.equipe_aparelho import marcar as _marcar_aparelho  # noqa: E402
meu_cadastro.after_app_request(_marcar_aparelho)

PESSOAIS = ('cpf', 'data_nascimento', 'email_pessoal', 'celular_pessoal', 'celular_corporativo',
            'cep', 'logradouro', 'numero', 'complemento', 'bairro', 'cidade', 'estado')
BANCARIOS = cr.CAMPOS          # 18/11/2026: várias contas para receber (utils/contas_recebimento.py)
PIX_TIPOS = cr.PIX_TIPOS
CONTA_TIPOS = cr.CONTA_TIPOS
_LIM = {'cpf': 14, 'email_pessoal': 255, 'celular_pessoal': 20, 'celular_corporativo': 20, 'cep': 9, 'logradouro': 255,
        'numero': 20, 'complemento': 100, 'bairro': 100, 'cidade': 100, 'estado': 2}
_LIM.update(cr.LIM)


def _dados(uid):
    cad = execute_query('SELECT * FROM cadastro_pendente WHERE usuario_id = %s ORDER BY id DESC LIMIT 1', (uid,), fetch=True, fetch_one=True)
    return cad, cr.escolhida(uid)


def _conta_do_form(uid, contas):
    """Qual conta o formulário mostra: ?conta=nova (em branco), ?conta=<id> (uma das dela) ou a escolhida."""
    pedida = request.values.get('conta') or ''
    if pedida == 'nova':
        return {}
    if pedida.isdigit():
        for c in contas:
            if c['id'] == int(pedida):
                return c
    return next((c for c in contas if c.get('recebe_pagamentos')), contas[0] if contas else {})


def _txt(k):
    v = (request.form.get(k) or '').strip()
    return v[:_LIM[k]] if v and k in _LIM else (v or None)


@meu_cadastro.route('/meu-cadastro', methods=['GET', 'POST'])
@login_required
def index():
    uid = current_user.id
    cad, _ = _dados(uid)
    contas = cr.contas_de(uid)
    banco = _conta_do_form(uid, contas)
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
        # ── conta para receber (a do formulário: uma das dela ou uma nova) ──
        nb = {k: _txt(k) for k in BANCARIOS}
        erros += cr.validar(nb)
        conta_nova = None
        if not erros:
            cid = int(request.form.get('conta_id') or 0) or None
            if cid and not any(c['id'] == cid for c in contas):
                cid = None
            salvo, mudb = cr.salvar(uid, cid, nb, uid)
            if mudb:
                mudou += mudb
                conta_nova = salvo if not cid else None
            if request.form.get('recebe') == '1' and salvo:
                cr.escolher(uid, salvo, uid)
                mudou.append('recebe_pagamentos')
        if erros:
            for e in erros:
                flash(e, 'danger')
        elif mudou:
            registrar('escrita.alterou_meu_cadastro', 'configuracoes', tabela='usuarios', registro_id=uid,
                      depois={'campos_alterados': sorted(set(mudou))})   # só os nomes dos campos: nunca os valores
            flash('Seu cadastro foi salvo.' + (' A conta nova entrou na lista; marque "Usar esta para receber" se for ela que recebe.' if conta_nova and len(contas) else ''), 'success')
        else:
            flash('Nada mudou.', 'info')
        return redirect(url_for('meu_cadastro.index'))
    return render_template('meu_cadastro.html', cad=cad, banco=banco, contas=contas, PIX_TIPOS=PIX_TIPOS, CONTA_TIPOS=CONTA_TIPOS,
                           rotulo=cr.rotulo, outro=cr.titular_e_outro)


@meu_cadastro.route('/meu-cadastro/conta/<int:cid>/usar', methods=['POST'])
@login_required
def conta_usar(cid):
    """Esta conta passa a receber os meus pagamentos (pedido de comissão)."""
    if cr.escolher(current_user.id, cid, current_user.id):
        registrar('escrita.escolheu_conta_recebimento', 'configuracoes', tabela='usuario_dados_bancarios', registro_id=cid,
                  depois={'usuario_id': current_user.id})
        flash('Pronto: os seus pedidos de pagamento saem para esta conta.', 'success')
    else:
        flash('Conta não encontrada.', 'danger')
    return redirect(url_for('meu_cadastro.index'))


@meu_cadastro.route('/meu-cadastro/conta/<int:cid>/excluir', methods=['POST'])
@login_required
def conta_excluir(cid):
    if cr.excluir(current_user.id, cid):
        registrar('escrita.excluiu_conta_recebimento', 'configuracoes', tabela='usuario_dados_bancarios', registro_id=cid,
                  depois={'usuario_id': current_user.id})
        flash('Conta apagada.', 'success')
    else:
        flash('Essa conta é a que recebe os pagamentos: escolha outra antes de apagar.', 'warning')
    return redirect(url_for('meu_cadastro.index'))
