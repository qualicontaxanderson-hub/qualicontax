"""Rotas das páginas iniciais dos módulos sem blueprint próprio."""
from flask import Blueprint, render_template
from utils.auth_helper import login_required

modulos = Blueprint('modulos', __name__)


@modulos.route('/cadastros/')
@login_required
def cadastros():
    return render_template('cadastros/index.html')


@modulos.route('/legalizacao/')
@login_required
def legalizacao():
    return render_template('legalizacao/index.html')


@modulos.route('/analise/')
@login_required
def analise():
    return render_template('analise/index.html')
