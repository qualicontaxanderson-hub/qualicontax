"""Rotas de Relatórios"""
from flask import Blueprint, render_template, request
from utils.auth_helper import login_required
from utils.db_helper import execute_query
from collections import defaultdict
from datetime import datetime

relatorios = Blueprint('relatorios', __name__)

_MONTH_ABBR_PT = ['', 'JAN', 'FEV', 'MAR', 'ABR', 'MAI', 'JUN',
                  'JUL', 'AGO', 'SET', 'OUT', 'NOV', 'DEZ']


def _get_months_in_range(data_inicio, data_fim):
    """Return list of (year, month) tuples for all months in [data_inicio, data_fim]."""
    try:
        start = datetime.strptime(data_inicio, '%Y-%m-%d')
        end = datetime.strptime(data_fim, '%Y-%m-%d')
    except (ValueError, TypeError):
        return []
    months = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def _build_despesas_tree(rows):
    """
    Build hierarchical expense tree from flat query rows.

    Each row must have keys: categoria_contabil, mes, ano, total.
    categoria_contabil uses ' > ' as level separator, e.g.:
      "VEICULOS EMPRESA > FIORINO > MANUTENÇÃO"

    Returns an ordered list of section dicts:
      { name, monthly_totals, total, categories: [
          { name, monthly_totals, total, subcategories: [
              { name, monthly_totals, total }
          ]}
      ]}
    """
    # section -> category -> subcategory -> (year, month) -> total
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float))))

    for row in rows:
        cat_str = (row.get('categoria_contabil') or '').strip()
        if not cat_str:
            continue
        parts = [p.strip() for p in cat_str.split('>')]
        mes = int(row['mes'])
        ano = int(row['ano'])
        total = float(row['total'] or 0)

        section = parts[0]
        category = parts[1] if len(parts) > 1 else parts[0]
        subcategory = ' > '.join(parts[2:]) if len(parts) > 2 else None

        data[section][category][subcategory][(ano, mes)] += total

    sections = []
    for section_name in sorted(data.keys()):
        sec_monthly = defaultdict(float)
        sec_total = 0.0
        categories = []

        for cat_name in sorted(data[section_name].keys()):
            cat_monthly = defaultdict(float)
            cat_total = 0.0
            subcats = []

            for subcat_name, monthly in data[section_name][cat_name].items():
                for (y, m), val in monthly.items():
                    cat_monthly[(y, m)] += val
                    cat_total += val
                    sec_monthly[(y, m)] += val
                    sec_total += val
                if subcat_name is not None:
                    subcats.append({
                        'name': subcat_name,
                        'monthly_totals': dict(monthly),
                        'total': sum(monthly.values()),
                    })

            subcats.sort(key=lambda x: x['name'])
            categories.append({
                'name': cat_name,
                'monthly_totals': dict(cat_monthly),
                'total': cat_total,
                'subcategories': subcats,
            })

        sections.append({
            'name': section_name,
            'monthly_totals': dict(sec_monthly),
            'total': sec_total,
            'categories': categories,
        })

    return sections


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


@relatorios.route('/relatorios/conf_despesas')
@login_required
def conf_despesas():
    """Relatório de Conferência de Despesas por Categoria — sempre consulta dados ao vivo."""
    today = datetime.today()
    default_inicio = f"{today.year}-01-01"
    default_fim = f"{today.year}-12-31"

    data_inicio = request.args.get('data_inicio', default_inicio) or default_inicio
    data_fim = request.args.get('data_fim', default_fim) or default_fim
    empresa_ids_raw = request.args.getlist('empresa_ids[]')

    # Validate dates
    try:
        datetime.strptime(data_inicio, '%Y-%m-%d')
        datetime.strptime(data_fim, '%Y-%m-%d')
    except ValueError:
        data_inicio = default_inicio
        data_fim = default_fim

    # List of active empresas for the filter widget
    empresas = execute_query(
        "SELECT id, nome FROM empresas WHERE situacao = 'ATIVO' ORDER BY nome",
        fetch=True,
    ) or []

    # Generate ordered month list for table columns
    months = _get_months_in_range(data_inicio, data_fim)
    month_labels = {(y, m): _MONTH_ABBR_PT[m] for y, m in months}

    # Build empresa filter (safe: validate to positive integers)
    params = [data_inicio, data_fim]
    empresa_filter = ""
    selected_empresa_ids = []
    if empresa_ids_raw:
        valid_ids = []
        for eid in empresa_ids_raw:
            try:
                v = int(eid)
                if v > 0:
                    valid_ids.append(v)
            except (ValueError, TypeError):
                pass
        if valid_ids:
            selected_empresa_ids = valid_ids
            placeholders = ','.join(['%s'] * len(valid_ids))
            empresa_filter = f"AND cb.empresa_id IN ({placeholders})"
            params.extend(valid_ids)

    # Query live data — no caching layer; every request reads current DB state
    query = f"""
        SELECT
            tb.categoria_contabil,
            MONTH(tb.data_transacao) AS mes,
            YEAR(tb.data_transacao)  AS ano,
            SUM(CASE WHEN tb.tipo = 'DEBITO' THEN tb.valor
                     ELSE -tb.valor END)       AS total
        FROM transacoes_bancarias tb
        JOIN conciliacoes_bancarias cb ON tb.conciliacao_id = cb.id
        WHERE tb.data_transacao BETWEEN %s AND %s
          AND tb.categoria_contabil IS NOT NULL
          AND tb.categoria_contabil != ''
          {empresa_filter}
        GROUP BY tb.categoria_contabil,
                 YEAR(tb.data_transacao),
                 MONTH(tb.data_transacao)
        ORDER BY tb.categoria_contabil, ano, mes
    """
    rows = execute_query(query, tuple(params), fetch=True) or []

    sections = _build_despesas_tree(rows)

    # Compute grand totals per month and overall
    overall_monthly = defaultdict(float)
    grand_total = 0.0
    for section in sections:
        for key, val in section['monthly_totals'].items():
            overall_monthly[key] += val
        grand_total += section['total']

    return render_template(
        'relatorios/conf_despesas.html',
        sections=sections,
        months=months,
        month_labels=month_labels,
        overall_monthly=dict(overall_monthly),
        grand_total=grand_total,
        empresas=empresas,
        filtros=dict(
            data_inicio=data_inicio,
            data_fim=data_fim,
            empresa_ids=selected_empresa_ids,
        ),
    )

