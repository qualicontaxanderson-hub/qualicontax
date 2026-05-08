"""Rotas das páginas iniciais dos módulos sem blueprint próprio."""
from flask import Blueprint, render_template
from utils.auth_helper import login_required, permission_required

modulos = Blueprint('modulos', __name__)


@modulos.route('/cadastros/')
@permission_required('clientes.index')
def cadastros():
    return render_template('cadastros/index.html')


@modulos.route('/comercial/')
@permission_required('modulos.comercial')
def comercial():
    return render_template('comercial/index.html')


@modulos.route('/dp/')
@permission_required('modulos.dp')
def dp():
    return render_template('dp/index.html')


@modulos.route('/legalizacao/')
@permission_required('modulos.legalizacao')
def legalizacao():
    return render_template('legalizacao/index.html')


@modulos.route('/analise/')
@permission_required('modulos.analise')
def analise():
    return render_template('analise/index.html')
