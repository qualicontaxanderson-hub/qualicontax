"""Rotas do Dashboard"""
from flask import Blueprint, render_template, jsonify
from utils.auth_helper import login_required
from utils.db_helper import execute_query

dashboard = Blueprint('dashboard', __name__)


@dashboard.route('/')
@login_required
def index():
    """Mostra dashboard principal com cards e gráficos"""
    return render_template('dashboard.html')


@dashboard.route('/stats')
@login_required
def get_stats():
    """Retorna JSON com estatísticas do dashboard em uma única consulta ao banco."""
    row = execute_query(
        """SELECT
               (SELECT COUNT(*) FROM clientes)                                            AS cli_total,
               (SELECT COUNT(*) FROM clientes WHERE situacao = 'Ativo')                  AS cli_ativos,
               (SELECT COUNT(*) FROM contratos WHERE situacao = 'Ativo')                 AS con_total
        """,
        fetch=True, fetch_one=True,
    ) or {}

    stats = {
        'clientes': {
            'total':  int(row.get('cli_total')  or 0),
            'ativos': int(row.get('cli_ativos') or 0),
        },
        'contratos': {
            'total': int(row.get('con_total') or 0),
        },
        'processos': {'total': 0, 'em_andamento': 0},   # tabelas não implementadas ainda
        'obrigacoes': {'total': 0, 'pendentes': 0},       # tabelas não implementadas ainda
    }

    return jsonify(stats)
