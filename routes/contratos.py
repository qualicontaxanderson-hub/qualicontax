"""Rotas de Contratos"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from utils.auth_helper import login_required
from utils.db_helper import execute_query
from models.cliente import Cliente

contratos = Blueprint('contratos', __name__)


@contratos.route('/contratos')
@login_required
def list():
    """Lista contratos"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    offset = (page - 1) * per_page
    
    # Filtros
    cliente_id = request.args.get('cliente_id', type=int)
    situacao = request.args.get('situacao')
    
    conditions = []
    params = []
    
    if cliente_id:
        conditions.append("c.cliente_id = %s")
        params.append(cliente_id)
    
    if situacao:
        conditions.append("c.situacao = %s")
        params.append(situacao)
    
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    
    # Total de registros
    count_query = f"SELECT COUNT(*) as total FROM contratos c{where_clause}"
    total_result = execute_query(count_query, tuple(params), fetch=True, fetch_one=True)
    total = total_result['total'] if total_result else 0
    
    # Busca contratos com join em clientes
    params.extend([per_page, offset])
    query = f"""
        SELECT c.*, cl.nome_razao_social as cliente_nome
        FROM contratos c
        LEFT JOIN clientes cl ON c.cliente_id = cl.id
        {where_clause}
        ORDER BY c.data_criacao DESC
        LIMIT %s OFFSET %s
    """
    
    contratos_list = execute_query(query, tuple(params), fetch=True) or []
    total_pages = (total + per_page - 1) // per_page
    
    return render_template('contratos/list.html',
                          contratos=contratos_list,
                          page=page,
                          total_pages=total_pages,
                          total=total)


@contratos.route('/contratos/novo', methods=['GET', 'POST'])
@login_required
def create():
    """GET/POST novo contrato"""
    if request.method == 'POST':
        data = {
            'cliente_id': request.form.get('cliente_id', type=int),
            'numero_contrato': request.form.get('numero_contrato'),
            'tipo_servico': request.form.get('tipo_servico'),
            'valor_mensal': request.form.get('valor_mensal'),
            'data_inicio': request.form.get('data_inicio'),
            'data_fim': request.form.get('data_fim'),
            'situacao': request.form.get('situacao', 'Ativo'),
            'observacoes': request.form.get('observacoes')
        }
        
        # Validações básicas
        if not data['cliente_id'] or not data['numero_contrato'] or not data['tipo_servico']:
            flash('Preencha todos os campos obrigatórios.', 'danger')
            clientes_list = Cliente.get_all(per_page=1000)['clientes']
            return render_template('contratos/form.html', contrato=data, clientes=clientes_list)
        
        query = """
            INSERT INTO contratos (
                cliente_id, numero_contrato, tipo_servico, valor_mensal,
                data_inicio, data_fim, situacao, observacoes, data_criacao
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """
        params = (
            data['cliente_id'],
            data['numero_contrato'],
            data['tipo_servico'],
            data['valor_mensal'],
            data['data_inicio'],
            data['data_fim'],
            data['situacao'],
            data['observacoes']
        )
        
        contrato_id = execute_query(query, params)
        
        if contrato_id:
            flash('Contrato cadastrado com sucesso!', 'success')
            return redirect(url_for('contratos.list'))
        else:
            flash('Erro ao cadastrar contrato.', 'danger')
    
    # GET - Busca lista de clientes para o dropdown
    clientes_list = Cliente.get_all(per_page=1000)['clientes']
    
    return render_template('contratos/form.html', contrato=None, clientes=clientes_list)
