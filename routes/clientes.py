"""Rotas de Clientes - CRUD completo"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from utils.auth_helper import login_required
from models.cliente import Cliente

clientes = Blueprint('clientes', __name__)


@clientes.route('/clientes')
@login_required
def list():
    """Lista todos os clientes com filtros e paginação"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    filters = {}
    if request.args.get('tipo_pessoa'):
        filters['tipo_pessoa'] = request.args.get('tipo_pessoa')
    if request.args.get('situacao'):
        filters['situacao'] = request.args.get('situacao')
    if request.args.get('regime_tributario'):
        filters['regime_tributario'] = request.args.get('regime_tributario')
    
    result = Cliente.get_all(filters=filters, page=page, per_page=per_page)
    
    return render_template('clientes/list.html',
                          clientes=result['clientes'],
                          page=result['page'],
                          total_pages=result['total_pages'],
                          total=result['total'],
                          filters=filters)


@clientes.route('/clientes/novo', methods=['GET', 'POST'])
@login_required
def create():
    """GET/POST para formulário de novo cliente"""
    if request.method == 'POST':
        data = {
            'tipo_pessoa': request.form.get('tipo_pessoa'),
            'nome_razao_social': request.form.get('nome_razao_social'),
            'cpf_cnpj': request.form.get('cpf_cnpj'),
            'inscricao_estadual': request.form.get('inscricao_estadual'),
            'inscricao_municipal': request.form.get('inscricao_municipal'),
            'email': request.form.get('email'),
            'telefone': request.form.get('telefone'),
            'celular': request.form.get('celular'),
            'regime_tributario': request.form.get('regime_tributario'),
            'porte_empresa': request.form.get('porte_empresa'),
            'data_inicio_contrato': request.form.get('data_inicio_contrato'),
            'situacao': request.form.get('situacao', 'Ativo'),
            'observacoes': request.form.get('observacoes')
        }
        
        # Validações básicas
        if not data['tipo_pessoa'] or not data['nome_razao_social'] or not data['cpf_cnpj']:
            flash('Preencha todos os campos obrigatórios.', 'danger')
            return render_template('clientes/form.html', cliente=data)
        
        cliente_id = Cliente.create(data)
        
        if cliente_id:
            flash('Cliente cadastrado com sucesso!', 'success')
            return redirect(url_for('clientes.view', id=cliente_id))
        else:
            flash('Erro ao cadastrar cliente.', 'danger')
            return render_template('clientes/form.html', cliente=data)
    
    return render_template('clientes/form.html', cliente=None)


@clientes.route('/clientes/<int:id>')
@login_required
def view(id):
    """Visualiza detalhes do cliente"""
    cliente = Cliente.get_by_id(id)
    
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.list'))
    
    return render_template('clientes/view.html', cliente=cliente)


@clientes.route('/clientes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def edit(id):
    """GET/POST para formulário de edição de cliente"""
    cliente = Cliente.get_by_id(id)
    
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.list'))
    
    if request.method == 'POST':
        data = {
            'tipo_pessoa': request.form.get('tipo_pessoa'),
            'nome_razao_social': request.form.get('nome_razao_social'),
            'cpf_cnpj': request.form.get('cpf_cnpj'),
            'inscricao_estadual': request.form.get('inscricao_estadual'),
            'inscricao_municipal': request.form.get('inscricao_municipal'),
            'email': request.form.get('email'),
            'telefone': request.form.get('telefone'),
            'celular': request.form.get('celular'),
            'regime_tributario': request.form.get('regime_tributario'),
            'porte_empresa': request.form.get('porte_empresa'),
            'data_inicio_contrato': request.form.get('data_inicio_contrato'),
            'situacao': request.form.get('situacao'),
            'observacoes': request.form.get('observacoes')
        }
        
        # Validações básicas
        if not data['tipo_pessoa'] or not data['nome_razao_social'] or not data['cpf_cnpj']:
            flash('Preencha todos os campos obrigatórios.', 'danger')
            return render_template('clientes/form.html', cliente=cliente)
        
        if Cliente.update(id, data):
            flash('Cliente atualizado com sucesso!', 'success')
            return redirect(url_for('clientes.view', id=id))
        else:
            flash('Erro ao atualizar cliente.', 'danger')
    
    return render_template('clientes/form.html', cliente=cliente)


@clientes.route('/clientes/<int:id>/deletar', methods=['POST'])
@login_required
def delete(id):
    """DELETE cliente"""
    cliente = Cliente.get_by_id(id)
    
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.list'))
    
    if Cliente.delete(id):
        flash('Cliente removido com sucesso!', 'success')
    else:
        flash('Erro ao remover cliente.', 'danger')
    
    return redirect(url_for('clientes.list'))
