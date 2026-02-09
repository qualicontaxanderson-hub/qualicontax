"""Rotas de Relatórios"""
from flask import Blueprint, render_template, request
from utils.auth_helper import login_required
from utils.db_helper import execute_query

relatorios = Blueprint('relatorios', __name__)


@relatorios.route('/relatorios')
@login_required
def index():
    """Página principal de relatórios"""
    return render_template('relatorios/index.html')


@relatorios.route('/relatorios/clientes')
@login_required
def clientes_report():
    """Relatório de clientes"""
    # Filtros
    tipo_pessoa = request.args.get('tipo_pessoa')
    regime_tributario = request.args.get('regime_tributario')
    situacao = request.args.get('situacao')
    
    conditions = []
    params = []
    
    if tipo_pessoa:
        conditions.append("tipo_pessoa = %s")
        params.append(tipo_pessoa)
    
    if regime_tributario:
        conditions.append("regime_tributario = %s")
        params.append(regime_tributario)
    
    if situacao:
        conditions.append("situacao = %s")
        params.append(situacao)
    
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    
    # Relatório de clientes
    query = f"""
        SELECT tipo_pessoa, nome_razao_social, cpf_cnpj, email, telefone,
               regime_tributario, situacao, data_inicio_contrato
        FROM clientes
        {where_clause}
        ORDER BY nome_razao_social
    """
    
    clientes = execute_query(query, tuple(params), fetch=True) or []
    
    # Estatísticas
    stats_query = f"""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN tipo_pessoa = 'Física' THEN 1 ELSE 0 END) as fisica,
            SUM(CASE WHEN tipo_pessoa = 'Jurídica' THEN 1 ELSE 0 END) as juridica,
            SUM(CASE WHEN situacao = 'Ativo' THEN 1 ELSE 0 END) as ativos,
            SUM(CASE WHEN situacao = 'Inativo' THEN 1 ELSE 0 END) as inativos
        FROM clientes
        {where_clause}
    """
    
    stats = execute_query(stats_query, tuple(params), fetch=True, fetch_one=True)
    
    return render_template('relatorios/clientes.html',
                          clientes=clientes,
                          stats=stats,
                          filters={'tipo_pessoa': tipo_pessoa, 'regime_tributario': regime_tributario, 'situacao': situacao})


@relatorios.route('/relatorios/processos')
@login_required
def processos_report():
    """Relatório de processos"""
    # Filtros
    tipo = request.args.get('tipo')
    status = request.args.get('status')
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    
    conditions = []
    params = []
    
    if tipo:
        conditions.append("p.tipo = %s")
        params.append(tipo)
    
    if status:
        conditions.append("p.status = %s")
        params.append(status)
    
    if data_inicio:
        conditions.append("p.data_abertura >= %s")
        params.append(data_inicio)
    
    if data_fim:
        conditions.append("p.data_abertura <= %s")
        params.append(data_fim)
    
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    
    # Relatório de processos
    query = f"""
        SELECT p.*, c.nome_razao_social as cliente_nome
        FROM processos p
        LEFT JOIN clientes c ON p.cliente_id = c.id
        {where_clause}
        ORDER BY p.data_abertura DESC
    """
    
    processos = execute_query(query, tuple(params), fetch=True) or []
    
    # Estatísticas
    stats_query = f"""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'Em Andamento' THEN 1 ELSE 0 END) as em_andamento,
            SUM(CASE WHEN status = 'Concluído' THEN 1 ELSE 0 END) as concluidos,
            SUM(CASE WHEN status = 'Pendente' THEN 1 ELSE 0 END) as pendentes,
            SUM(COALESCE(valor, 0)) as valor_total
        FROM processos p
        {where_clause}
    """
    
    stats = execute_query(stats_query, tuple(params), fetch=True, fetch_one=True)
    
    return render_template('relatorios/processos.html',
                          processos=processos,
                          stats=stats,
                          filters={'tipo': tipo, 'status': status, 'data_inicio': data_inicio, 'data_fim': data_fim})


@relatorios.route('/relatorios/obrigacoes')
@login_required
def obrigacoes_report():
    """Relatório de obrigações"""
    # Filtros
    status = request.args.get('status')
    periodo = request.args.get('periodo')
    tipo = request.args.get('tipo')
    
    conditions = []
    params = []
    
    if status:
        conditions.append("status = %s")
        params.append(status)
    
    if periodo:
        conditions.append("periodo = %s")
        params.append(periodo)
    
    if tipo:
        conditions.append("tipo_obrigacao = %s")
        params.append(tipo)
    
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    
    # Relatório de obrigações
    query = f"""
        SELECT o.*, c.nome_razao_social as cliente_nome
        FROM obrigacoes o
        LEFT JOIN clientes c ON o.cliente_id = c.id
        {where_clause}
        ORDER BY o.data_vencimento
    """
    
    obrigacoes = execute_query(query, tuple(params), fetch=True) or []
    
    # Estatísticas
    stats_query = f"""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'Pendente' THEN 1 ELSE 0 END) as pendentes,
            SUM(CASE WHEN status = 'Concluída' THEN 1 ELSE 0 END) as concluidas,
            SUM(CASE WHEN status = 'Atrasada' THEN 1 ELSE 0 END) as atrasadas
        FROM obrigacoes o
        {where_clause}
    """
    
    stats = execute_query(stats_query, tuple(params), fetch=True, fetch_one=True)
    
    return render_template('relatorios/obrigacoes.html',
                          obrigacoes=obrigacoes,
                          stats=stats,
                          filters={'status': status, 'periodo': periodo, 'tipo': tipo})
