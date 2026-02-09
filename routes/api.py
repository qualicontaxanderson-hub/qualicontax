"""Rotas de API para chamadas AJAX"""
from flask import Blueprint, request, jsonify
from utils.auth_helper import login_required
from models.cliente import Cliente
from utils.db_helper import execute_query

api = Blueprint('api', __name__)


@api.route('/api/clientes/search')
@login_required
def search_clientes():
    """Busca clientes, retorna JSON"""
    query_text = request.args.get('q', '')
    
    if not query_text or len(query_text) < 2:
        return jsonify({'clientes': []})
    
    clientes = Cliente.search(query_text)
    
    # Formata resultado para JSON
    result = []
    for cliente in clientes:
        result.append({
            'id': cliente['id'],
            'nome_razao_social': cliente['nome_razao_social'],
            'cpf_cnpj': cliente['cpf_cnpj'],
            'email': cliente['email'],
            'tipo_pessoa': cliente['tipo_pessoa'],
            'situacao': cliente['situacao']
        })
    
    return jsonify({'clientes': result})


@api.route('/api/dashboard/charts')
@login_required
def get_chart_data():
    """Dados de gráficos para dashboard"""
    # Gráfico de clientes por regime tributário
    regime_query = """
        SELECT regime_tributario, COUNT(*) as total
        FROM clientes
        WHERE situacao = 'Ativo' AND regime_tributario IS NOT NULL
        GROUP BY regime_tributario
        ORDER BY total DESC
    """
    regime_data = execute_query(regime_query, fetch=True) or []
    
    # Gráfico de processos por status
    processos_query = """
        SELECT status, COUNT(*) as total
        FROM processos
        GROUP BY status
        ORDER BY total DESC
    """
    processos_data = execute_query(processos_query, fetch=True) or []
    
    # Gráfico de obrigações por mês
    obrigacoes_query = """
        SELECT 
            DATE_FORMAT(data_vencimento, '%Y-%m') as mes,
            COUNT(*) as total,
            SUM(CASE WHEN status = 'Concluída' THEN 1 ELSE 0 END) as concluidas,
            SUM(CASE WHEN status = 'Pendente' THEN 1 ELSE 0 END) as pendentes
        FROM obrigacoes
        WHERE data_vencimento >= DATE_SUB(NOW(), INTERVAL 6 MONTH)
        GROUP BY mes
        ORDER BY mes
    """
    obrigacoes_data = execute_query(obrigacoes_query, fetch=True) or []
    
    # Gráfico de contratos por tipo de serviço
    contratos_query = """
        SELECT tipo_servico, COUNT(*) as total
        FROM contratos
        WHERE situacao = 'Ativo'
        GROUP BY tipo_servico
        ORDER BY total DESC
    """
    contratos_data = execute_query(contratos_query, fetch=True) or []
    
    charts = {
        'clientes_por_regime': {
            'labels': [item['regime_tributario'] for item in regime_data],
            'data': [item['total'] for item in regime_data]
        },
        'processos_por_status': {
            'labels': [item['status'] for item in processos_data],
            'data': [item['total'] for item in processos_data]
        },
        'obrigacoes_por_mes': {
            'labels': [item['mes'] for item in obrigacoes_data],
            'concluidas': [item['concluidas'] for item in obrigacoes_data],
            'pendentes': [item['pendentes'] for item in obrigacoes_data]
        },
        'contratos_por_tipo': {
            'labels': [item['tipo_servico'] for item in contratos_data],
            'data': [item['total'] for item in contratos_data]
        }
    }
    
    return jsonify(charts)
