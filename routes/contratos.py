"""Rotas de Contratos — módulo antigo (nunca funcionou; tabela ``contratos`` com 0 linhas).

Desde 06/11/2026 o contrato de prestação de serviços é feito em Financeiro > CONTRATOS (routes/financeiro_contratos.py):
montado do cadastro, com número QC-XXXXXX gerado pelo sistema e assinatura eletrônica. Estas rotas só levam para lá,
para os links antigos (Comercial, ficha do cliente) continuarem valendo.
"""
from flask import Blueprint, redirect, request, url_for
from utils.auth_helper import login_required

contratos = Blueprint('contratos', __name__)


@contratos.route('/contratos')
@login_required
def list_contratos():
    return redirect(url_for('financeiro.contratos'))


@contratos.route('/contratos/novo', methods=['GET', 'POST'])
@login_required
def create():
    return redirect(url_for('financeiro.contrato_novo', cliente_id=request.args.get('cliente_id', type=int)))
