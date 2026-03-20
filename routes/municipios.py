"""Rotas de Municípios - CRUD completo"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from utils.auth_helper import login_required
from models.municipio import Municipio

municipios = Blueprint('municipios', __name__)

UFS = [
    'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA',
    'MT', 'MS', 'MG', 'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN',
    'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO',
]


@municipios.route('/municipios')
@login_required
def index():
    """Lista todos os municípios cadastrados"""
    try:
        uf = request.args.get('uf', '')
        situacao = request.args.get('situacao', '')
        busca = request.args.get('busca', '')

        lista = Municipio.get_all(
            uf=uf or None,
            situacao=situacao or None,
            busca=busca or None,
        )
        return render_template('municipios/index.html',
                               municipios=lista,
                               ufs=UFS,
                               filtro_uf=uf,
                               filtro_situacao=situacao,
                               filtro_busca=busca)
    except Exception as e:
        flash(f'Erro ao carregar municípios: {str(e)}', 'danger')
        return render_template('municipios/index.html',
                               municipios=[],
                               ufs=UFS,
                               filtro_uf='',
                               filtro_situacao='',
                               filtro_busca='')


@municipios.route('/municipios/novo', methods=['GET', 'POST'])
@login_required
def novo():
    """Criar novo município"""
    if request.method == 'POST':
        try:
            nome = (request.form.get('nome') or '').strip()
            uf = (request.form.get('uf') or '').strip()
            site_prefeitura = (request.form.get('site_prefeitura') or '').strip()
            situacao = request.form.get('situacao', 'ATIVO')

            if not nome:
                flash('Nome do município é obrigatório!', 'danger')
                return render_template('municipios/form.html', municipio=None, ufs=UFS)
            if not uf or uf.upper() not in UFS:
                flash('UF inválida!', 'danger')
                return render_template('municipios/form.html', municipio=None, ufs=UFS)

            municipio_id = Municipio.create(nome, uf, site_prefeitura or None, situacao)
            if municipio_id:
                flash('Município cadastrado com sucesso!', 'success')
                return redirect(url_for('municipios.index'))
            else:
                flash('Erro ao cadastrar município!', 'danger')

        except Exception as e:
            flash(f'Erro ao cadastrar município: {str(e)}', 'danger')

    return render_template('municipios/form.html', municipio=None, ufs=UFS)


@municipios.route('/municipios/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar(id):
    """Editar município"""
    municipio = Municipio.get_by_id(id)
    if not municipio:
        flash('Município não encontrado!', 'danger')
        return redirect(url_for('municipios.index'))

    if request.method == 'POST':
        try:
            nome = (request.form.get('nome') or '').strip()
            uf = (request.form.get('uf') or '').strip()
            site_prefeitura = (request.form.get('site_prefeitura') or '').strip()
            situacao = request.form.get('situacao', 'ATIVO')

            if not nome:
                flash('Nome do município é obrigatório!', 'danger')
                return render_template('municipios/form.html', municipio=municipio, ufs=UFS)
            if not uf or uf.upper() not in UFS:
                flash('UF inválida!', 'danger')
                return render_template('municipios/form.html', municipio=municipio, ufs=UFS)

            sucesso = Municipio.update(id, nome, uf, site_prefeitura or None, situacao)
            if sucesso is not None:
                flash('Município atualizado com sucesso!', 'success')
                return redirect(url_for('municipios.index'))
            else:
                flash('Erro ao atualizar município!', 'danger')

        except Exception as e:
            flash(f'Erro ao atualizar município: {str(e)}', 'danger')

    return render_template('municipios/form.html', municipio=municipio, ufs=UFS)


@municipios.route('/municipios/<int:id>/deletar', methods=['POST'])
@login_required
def deletar(id):
    """Deletar município"""
    try:
        sucesso = Municipio.delete(id)
        if sucesso is not None:
            flash('Município removido com sucesso!', 'success')
        else:
            flash('Erro ao remover município!', 'danger')
    except Exception as e:
        flash(f'Erro ao remover município: {str(e)}', 'danger')
    return redirect(url_for('municipios.index'))
