"""Rotas de Processos"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from utils.auth_helper import login_required
from models.processo import Processo
from models.cliente import Cliente

processos = Blueprint('processos', __name__)


@processos.route('/processos')
@login_required
def list_processos():
    """Lista processos"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    
    # Filtros
    filters = {}
    if request.args.get('cliente_id'):
        filters['cliente_id'] = request.args.get('cliente_id', type=int)
    if request.args.get('tipo'):
        filters['tipo'] = request.args.get('tipo')
    if request.args.get('status'):
        filters['status'] = request.args.get('status')
    
    result = Processo.get_all(filters=filters, page=page, per_page=per_page)
    
    return render_template('processos/list.html',
                          processos=result['processos'],
                          page=result['page'],
                          total_pages=result['total_pages'],
                          total=result['total'],
                          filters=filters)


@processos.route('/processos/novo', methods=['GET', 'POST'])
@login_required
def create():
    """GET/POST novo processo"""
    if request.method == 'POST':
        data = {
            'cliente_id': request.form.get('cliente_id', type=int),
            'numero_processo': request.form.get('numero_processo'),
            'tipo': request.form.get('tipo'),
            'status': request.form.get('status', 'Em Andamento'),
            'data_abertura': request.form.get('data_abertura'),
            'data_conclusao': request.form.get('data_conclusao'),
            'descricao': request.form.get('descricao')
        }
        
        # Validações básicas
        if not data['cliente_id'] or not data['numero_processo'] or not data['tipo']:
            flash('Preencha todos os campos obrigatórios.', 'danger')
            clientes_list = Cliente.get_all(per_page=1000)['clientes']
            return render_template('processos/form.html', processo=data, clientes=clientes_list)
        
        processo_id = Processo.create(data)
        
        if processo_id:
            flash('Processo cadastrado com sucesso!', 'success')
            return redirect(url_for('processos.view', id=processo_id))
        else:
            flash('Erro ao cadastrar processo.', 'danger')
    
    # GET - Busca lista de clientes para o dropdown
    clientes_list = Cliente.get_all(per_page=1000)['clientes']
    
    return render_template('processos/form.html', processo=None, clientes=clientes_list)


@processos.route('/processos/<int:id>')
@login_required
def view(id):
    """Visualiza detalhes do processo"""
    processo = Processo.get_by_id(id)
    
    if not processo:
        flash('Processo não encontrado.', 'danger')
        return redirect(url_for('processos.list'))
    
    # Busca cliente relacionado
    cliente = None
    if processo.get('cliente_id'):
        cliente = Cliente.get_by_id(processo['cliente_id'])
    
    return render_template('processos/view.html', processo=processo, cliente=cliente)
