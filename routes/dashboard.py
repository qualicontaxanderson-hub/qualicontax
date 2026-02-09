"""Rotas do Dashboard"""
from flask import Blueprint, render_template, jsonify
from utils.auth_helper import login_required
from utils.db_helper import execute_query

dashboard = Blueprint('dashboard', __name__)


@dashboard.route('/')
@login_required
def index():
    """Mostra dashboard principal com cards e gráficos"""
    return render_template('dashboard/index.html')


@dashboard.route('/stats')
@login_required
def get_stats():
    """Retorna JSON com estatísticas do dashboard"""
    # Estatísticas de clientes
    clientes_query = "SELECT COUNT(*) as total, SUM(CASE WHEN situacao = 'Ativo' THEN 1 ELSE 0 END) as ativos FROM clientes"
    clientes_stats = execute_query(clientes_query, fetch=True, fetch_one=True)
    
    # Estatísticas de processos
    processos_query = "SELECT COUNT(*) as total, SUM(CASE WHEN status = 'Em Andamento' THEN 1 ELSE 0 END) as em_andamento FROM processos"
    processos_stats = execute_query(processos_query, fetch=True, fetch_one=True)
    
    # Estatísticas de obrigações
    obrigacoes_query = "SELECT COUNT(*) as total, SUM(CASE WHEN status = 'Pendente' THEN 1 ELSE 0 END) as pendentes FROM obrigacoes"
    obrigacoes_stats = execute_query(obrigacoes_query, fetch=True, fetch_one=True)
    
    # Estatísticas de contratos
    contratos_query = "SELECT COUNT(*) as total FROM contratos WHERE situacao = 'Ativo'"
    contratos_stats = execute_query(contratos_query, fetch=True, fetch_one=True)
    
    stats = {
        'clientes': {
            'total': clientes_stats['total'] if clientes_stats else 0,
            'ativos': clientes_stats['ativos'] if clientes_stats else 0
        },
        'processos': {
            'total': processos_stats['total'] if processos_stats else 0,
            'em_andamento': processos_stats['em_andamento'] if processos_stats else 0
        },
        'obrigacoes': {
            'total': obrigacoes_stats['total'] if obrigacoes_stats else 0,
            'pendentes': obrigacoes_stats['pendentes'] if obrigacoes_stats else 0
        },
        'contratos': {
            'total': contratos_stats['total'] if contratos_stats else 0
        }
    }
    
    return jsonify(stats)
