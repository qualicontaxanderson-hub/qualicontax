"""Rotas das páginas iniciais dos módulos sem blueprint próprio."""
from datetime import date

from flask import Blueprint, render_template, request
from utils.auth_helper import permission_required
from utils.db_helper import execute_query

modulos = Blueprint('modulos', __name__)


def _get_empresas_analise():
    return execute_query(
        "SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE situacao='ATIVO' ORDER BY nome_razao_social",
        fetch=True,
    ) or []


def _get_grupos_analise():
    return execute_query(
        "SELECT id, nome FROM grupos_clientes WHERE situacao='ATIVO' ORDER BY nome",
        fetch=True,
    ) or []


def _get_categorias_analise():
    rows = execute_query(
        "SELECT nome FROM nfe_produto_categorias ORDER BY ordem, nome",
        fetch=True,
    ) or []
    return [r['nome'] for r in rows if r.get('nome')]


def _empresa_where_analise(f_cliente_id, f_grupo_id, alias='n', params=None):
    if params is None:
        params = []
    clauses = []
    if f_cliente_id:
        cid = int(f_cliente_id)
        clauses.append(
            f"({alias}.cliente_id = %s"
            f" OR ({alias}.cliente_id IS NULL"
            f"     AND REPLACE(REPLACE(REPLACE({alias}.dest_cnpj,'.',''),'/',''),'-','')"
            f"       = (SELECT REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-','')"
            f"            FROM clientes WHERE id = %s)))"
        )
        params.append(cid)
        params.append(cid)
    if f_grupo_id:
        gid = int(f_grupo_id)
        clauses.append(
            f"({alias}.grupo_id = %s"
            f" OR ({alias}.grupo_id IS NULL"
            f"     AND REPLACE(REPLACE(REPLACE({alias}.dest_cnpj,'.',''),'/',''),'-','')"
            f"       IN (SELECT REPLACE(REPLACE(REPLACE(c.cpf_cnpj,'.',''),'/',''),'-','')"
            f"             FROM clientes c"
            f"             JOIN cliente_grupo_relacao cgr ON cgr.cliente_id = c.id"
            f"             WHERE cgr.grupo_id = %s)))"
        )
        params.append(gid)
        params.append(gid)
    return clauses, params


@modulos.route('/cadastros/')
@permission_required('clientes.index')
def cadastros():
    return render_template('cadastros/index.html')


@modulos.route('/comercial/')
@permission_required('modulos.comercial')
def comercial():
    return render_template('comercial/index.html')


@modulos.route('/dp/')
@permission_required('modulos.dp')
def dp():
    return render_template('dp/index.html')


@modulos.route('/legalizacao/')
@permission_required('modulos.legalizacao')
def legalizacao():
    return render_template('legalizacao/index.html')


@modulos.route('/analise/')
@permission_required('modulos.analise')
def analise():
    return render_template('analise/index.html')


@modulos.route('/analise/fiscal/')
@permission_required('modulos.analise')
def analise_fiscal():
    return render_template('analise/fiscal.html')


@modulos.route('/analise/fiscal/compras-nfe/')
@permission_required('modulos.analise')
def analise_fiscal_compras():
    today = date.today()
    data_ini_default = today.replace(day=1).isoformat()
    data_fim_default = today.isoformat()

    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    f_data_ini = request.args.get('data_ini', '').strip() or data_ini_default
    f_data_fim = request.args.get('data_fim', '').strip() or data_fim_default
    f_categoria = request.args.get('categoria', '').strip()
    f_produto_id = request.args.get('produto_id', '').strip()
    f_descricao = request.args.get('descricao', '').strip()
    buscar = request.args.get('buscar', '').strip()

    empresas = _get_empresas_analise()
    grupos = _get_grupos_analise()
    categorias = _get_categorias_analise()

    resumo = {
        'total_notas': 0,
        'total_itens': 0,
        'total_qtd': 0.0,
        'total_valor': 0.0,
        'total_icms': 0.0,
        'total_fornecedores': 0,
    }
    categorias_rows = []
    produtos_rows = []
    fornecedores_rows = []
    detalhamento_rows = []
    erro_filtros = ''
    searched = bool(
        buscar or f_cliente_id or f_grupo_id or
        request.args.get('data_ini', '').strip() or
        request.args.get('data_fim', '').strip() or
        f_categoria or f_produto_id or f_descricao
    )

    if searched and not (f_cliente_id or f_grupo_id):
        erro_filtros = 'Selecione uma empresa ou um grupo para gerar a análise.'

    if searched and not erro_filtros:
        where, params = _empresa_where_analise(f_cliente_id, f_grupo_id, alias='n', params=[])
        if f_data_ini:
            where.append('n.data_emissao >= %s')
            params.append(f_data_ini)
        if f_data_fim:
            where.append('n.data_emissao <= %s')
            params.append(f_data_fim)
        if f_produto_id:
            where.append('i.produto_catalogo_id = %s')
            params.append(int(f_produto_id))
        elif f_categoria == '__sem_vinculo__':
            where.append('i.produto_catalogo_id IS NULL')
        elif f_categoria:
            where.append('p.categoria = %s')
            params.append(f_categoria)
        if f_descricao:
            where.append('(COALESCE(p.nome, i.descricao) LIKE %s OR i.descricao LIKE %s)')
            like_value = f'%{f_descricao}%'
            params.extend([like_value, like_value])

        where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

        resumo_row = execute_query(
            f"""SELECT COUNT(DISTINCT n.id) AS total_notas,
                       COUNT(DISTINCT n.emit_cnpj) AS total_fornecedores,
                       COUNT(*) AS total_itens,
                       COALESCE(SUM(i.quantidade), 0) AS total_qtd,
                       COALESCE(SUM(i.valor_total), 0) AS total_valor,
                       COALESCE(SUM(i.valor_icms), 0) AS total_icms
                  FROM nfe_itens i
                  JOIN nfe_importacoes n ON n.id = i.nfe_id
                  LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
                  {where_sql}""",
            tuple(params),
            fetch=True,
            fetch_one=True,
        ) or {}
        resumo = {
            'total_notas': int(resumo_row.get('total_notas') or 0),
            'total_itens': int(resumo_row.get('total_itens') or 0),
            'total_qtd': float(resumo_row.get('total_qtd') or 0),
            'total_valor': float(resumo_row.get('total_valor') or 0),
            'total_icms': float(resumo_row.get('total_icms') or 0),
            'total_fornecedores': int(resumo_row.get('total_fornecedores') or 0),
        }

        categorias_rows = execute_query(
            f"""SELECT COALESCE(p.categoria, '— Sem vínculo —') AS categoria,
                       COALESCE(NULLIF(p.subcategoria, ''), '—') AS subcategoria,
                       COUNT(DISTINCT n.id) AS qtd_notas,
                       COUNT(*) AS qtd_itens,
                       COALESCE(SUM(i.quantidade), 0) AS total_qtd,
                       COALESCE(SUM(i.valor_total), 0) AS total_valor,
                       COALESCE(SUM(i.valor_icms), 0) AS total_icms
                  FROM nfe_itens i
                  JOIN nfe_importacoes n ON n.id = i.nfe_id
                  LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
                  {where_sql}
                 GROUP BY COALESCE(p.categoria, '— Sem vínculo —'),
                          COALESCE(NULLIF(p.subcategoria, ''), '—')
                 ORDER BY total_valor DESC, categoria, subcategoria
                 LIMIT 100""",
            tuple(params),
            fetch=True,
        ) or []

        produtos_rows = execute_query(
            f"""SELECT COALESCE(p.categoria, '— Sem vínculo —') AS categoria,
                       COALESCE(NULLIF(p.subcategoria, ''), '—') AS subcategoria,
                       COALESCE(NULLIF(p.nome, ''), i.descricao) AS produto_nome,
                       COALESCE(NULLIF(p.unidade, ''), i.unidade) AS unidade,
                       COUNT(DISTINCT n.id) AS qtd_notas,
                       COUNT(*) AS qtd_itens,
                       COALESCE(SUM(i.quantidade), 0) AS total_qtd,
                       COALESCE(SUM(i.valor_total), 0) AS total_valor,
                       COALESCE(SUM(i.valor_icms), 0) AS total_icms
                  FROM nfe_itens i
                  JOIN nfe_importacoes n ON n.id = i.nfe_id
                  LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
                  {where_sql}
                 GROUP BY COALESCE(p.categoria, '— Sem vínculo —'),
                          COALESCE(NULLIF(p.subcategoria, ''), '—'),
                          COALESCE(NULLIF(p.nome, ''), i.descricao),
                          COALESCE(NULLIF(p.unidade, ''), i.unidade)
                 ORDER BY total_valor DESC, produto_nome
                 LIMIT 200""",
            tuple(params),
            fetch=True,
        ) or []

        fornecedores_rows = execute_query(
            f"""SELECT n.emit_cnpj,
                       n.emit_nome,
                       n.emit_uf,
                       COUNT(DISTINCT n.id) AS qtd_notas,
                       COUNT(*) AS qtd_itens,
                       COALESCE(SUM(i.quantidade), 0) AS total_qtd,
                       COALESCE(SUM(i.valor_total), 0) AS total_valor,
                       COALESCE(SUM(i.valor_icms), 0) AS total_icms
                  FROM nfe_itens i
                  JOIN nfe_importacoes n ON n.id = i.nfe_id
                  LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
                  {where_sql}
                 GROUP BY n.emit_cnpj, n.emit_nome, n.emit_uf
                 ORDER BY total_valor DESC, n.emit_nome
                 LIMIT 200""",
            tuple(params),
            fetch=True,
        ) or []

        detalhamento_rows = execute_query(
            f"""SELECT n.data_emissao,
                       n.num_nota,
                       n.serie,
                       c.nome_razao_social AS empresa_nome,
                       g.nome AS grupo_nome,
                       n.emit_nome,
                       n.emit_cnpj,
                       COALESCE(p.categoria, '— Sem vínculo —') AS categoria,
                       COALESCE(NULLIF(p.subcategoria, ''), '—') AS subcategoria,
                       COALESCE(NULLIF(p.nome, ''), i.descricao) AS produto_nome,
                       COALESCE(NULLIF(p.unidade, ''), i.unidade) AS unidade,
                       i.quantidade,
                       i.valor_unitario,
                       i.valor_total,
                       i.valor_icms
                  FROM nfe_itens i
                  JOIN nfe_importacoes n ON n.id = i.nfe_id
                  LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
                  LEFT JOIN clientes c ON c.id = n.cliente_id
                  LEFT JOIN grupos_clientes g ON g.id = n.grupo_id
                  {where_sql}
                 ORDER BY n.data_emissao DESC, n.id DESC, i.num_item ASC
                 LIMIT 500""",
            tuple(params),
            fetch=True,
        ) or []

        for collection in (categorias_rows, produtos_rows, fornecedores_rows, detalhamento_rows):
            for row in collection:
                for key in ('total_qtd', 'total_valor', 'total_icms', 'quantidade', 'valor_unitario'):
                    if key in row:
                        row[key] = float(row.get(key) or 0)

    return render_template(
        'analise/compras_nfe.html',
        empresas=empresas,
        grupos=grupos,
        categorias=categorias,
        resumo=resumo,
        categorias_rows=categorias_rows,
        produtos_rows=produtos_rows,
        fornecedores_rows=fornecedores_rows,
        detalhamento_rows=detalhamento_rows,
        searched=searched,
        erro_filtros=erro_filtros,
        f_cliente_id=f_cliente_id,
        f_grupo_id=f_grupo_id,
        f_data_ini=f_data_ini,
        f_data_fim=f_data_fim,
        f_categoria=f_categoria,
        f_produto_id=f_produto_id,
        f_descricao=f_descricao,
    )
