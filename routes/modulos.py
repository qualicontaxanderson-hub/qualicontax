"""Rotas das páginas iniciais dos módulos sem blueprint próprio."""
import logging
import threading
from datetime import date, datetime, timezone

from flask import Blueprint, render_template, request, jsonify
from flask_login import current_user

from routes.adicionais import TIPOS_CADASTROS
from utils.auth_helper import permission_required
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
modulos = Blueprint('modulos', __name__)
FORNECEDOR_CARDS_AUTO_OPEN_DEFAULT = 2
PRODUTO_CARDS_AUTO_OPEN_DEFAULT = 2


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
    """Monta cláusulas SQL de escopo (empresa/grupo) e retorna (clauses, params)."""
    if params is None:
        params = []
    else:
        # Evita mutar a lista recebida pelo chamador.
        params = list(params)
    # Análise Fiscal = COMPRAS (entradas). Sem este filtro, as linhas tipo='saida'
    # da captura SEFAZ (cliente_id = o vendedor) entrariam na análise de compras do
    # próprio vendedor e inflariam valor/ICMS. Escopo de tipo é obrigatório aqui.
    clauses = [f"{alias}.tipo = 'entrada'"]
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


# ---------------------------------------------------------------------------
# Home do Cadastros — números do carrossel e dos tiles.
# SÓ LEITURA. Mesmo contrato JSON da home do fiscal (counters / cards /
# gerado_em_ms), para reaproveitar static/js/home_destaques.js.
#
# Regra: NADA de número inventado. Card cuja fonte não existe simplesmente não
# entra, e card sem série real de verdade vai SEM sparkline — linha reta
# desenhada por falta de dado é decoração se passando por informação.
# ---------------------------------------------------------------------------
_CAD_HOME_CACHE: dict = {}
_CAD_HOME_CACHE_LOCK = threading.Lock()
_CAD_HOME_TTL_S = 60


def _cad_q1(sql, params=None):
    """Escalar isolado: se a query falhar, o card dela some em vez de derrubar a home."""
    try:
        return execute_query(sql, tuple(params or ()), fetch=True, fetch_one=True) or {}
    except Exception:
        logger.exception('[cadastros-home] query omitida')
        return {}


def _cad_i(d, k):
    v = (d or {}).get(k)
    return int(v) if v is not None else 0


def _cad_trend(atual, anterior):
    """Pílula de variação. Sem base anterior não inventa percentual."""
    if not anterior:
        return {'tipo': 'neutro', 'rotulo': 'no mês'}
    pct = round((atual - anterior) * 100.0 / anterior)
    if pct > 0:
        return {'tipo': 'alta', 'pct': pct}
    if pct < 0:
        return {'tipo': 'baixa', 'pct': pct}
    return {'tipo': 'igual', 'pct': 0}


def _cadastros_home_payload():
    cards = []

    # --- escalares (as mesmas definições da fita de KPIs de /clientes, para os
    #     números baterem entre as duas telas) ---
    c = _cad_q1(
        "SELECT "
        " (SELECT COUNT(*) FROM clientes WHERE situacao='ATIVO')                    ativos,"
        " (SELECT COUNT(*) FROM clientes)                                           total_cli,"
        " (SELECT COUNT(DISTINCT cliente_id) FROM dfe_certificados WHERE ativo=1)   com_cert,"
        " (SELECT COUNT(DISTINCT cliente_id) FROM dfe_certificados"
        "    WHERE ativo=1 AND validade IS NOT NULL AND validade >= CURDATE()"
        "      AND validade <= CURDATE() + INTERVAL 30 DAY)                         venc30,"
        " (SELECT COUNT(DISTINCT cliente_id) FROM dfe_certificados"
        "    WHERE ativo=1 AND validade IS NOT NULL"
        "      AND validade >  CURDATE() + INTERVAL 30 DAY"
        "      AND validade <= CURDATE() + INTERVAL 90 DAY)                         venc90,"
        " (SELECT COUNT(DISTINCT cliente_id) FROM dfe_certificados"
        "    WHERE ativo=1 AND validade IS NOT NULL AND validade < CURDATE())       vencidos,"
        " (SELECT COUNT(*) FROM grupos_clientes)                                    grupos,"
        " (SELECT COUNT(*) FROM grupos_clientes WHERE situacao='ATIVO')             grupos_at,"
        " (SELECT COUNT(*) FROM ramos_atividade)                                    ramos,"
        " (SELECT COUNT(*) FROM ramos_atividade WHERE situacao='ATIVO')             ramos_at,"
        " (SELECT COUNT(*) FROM municipios)                                         munis,"
        " (SELECT COUNT(*) FROM municipios WHERE situacao='ATIVO')                  munis_at,"
        " (SELECT COUNT(*) FROM contratos)                                          contratos,"
        " (SELECT COUNT(*) FROM contratos WHERE situacao='Ativo')                   contratos_at")

    ativos, total_cli = _cad_i(c, 'ativos'), _cad_i(c, 'total_cli')
    com_cert, venc30 = _cad_i(c, 'com_cert'), _cad_i(c, 'venc30')
    venc90, vencidos = _cad_i(c, 'venc90'), _cad_i(c, 'vencidos')
    grupos, grupos_at = _cad_i(c, 'grupos'), _cad_i(c, 'grupos_at')
    ramos, ramos_at = _cad_i(c, 'ramos'), _cad_i(c, 'ramos_at')
    munis, munis_at = _cad_i(c, 'munis'), _cad_i(c, 'munis_at')
    contratos, contratos_at = _cad_i(c, 'contratos'), _cad_i(c, 'contratos_at')

    # --- série real de cadastros novos por mês (clientes.criado_em) ---
    serie = execute_query(
        "SELECT DATE_FORMAT(criado_em,'%Y-%m') ym, COUNT(*) n FROM clientes "
        "WHERE criado_em >= DATE_FORMAT(CURDATE() - INTERVAL 5 MONTH,'%Y-%m-01') "
        "GROUP BY ym ORDER BY ym", fetch=True) or []
    por_mes = {r['ym']: int(r['n']) for r in serie}
    hoje = date.today()
    meses = []
    for i in range(5, -1, -1):
        m = hoje.month - i
        a = hoje.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        meses.append('%04d-%02d' % (a, m))
    spark_cli = [por_mes.get(m, 0) for m in meses]
    novos_mes, novos_mes_ant = spark_cli[-1], spark_cli[-2]

    # 1) Clientes ativos — tem série e comparação reais
    cards.append({
        'titulo': 'Clientes ativos', 'icone': 'fa-user-tie',
        'valor': ativos,
        'apoio': ('%d cadastrado%s no total · %d novo%s neste mês'
                  % (total_cli, '' if total_cli == 1 else 's',
                     novos_mes, '' if novos_mes == 1 else 's')),
        'trend': _cad_trend(novos_mes, novos_mes_ant),
        'spark': spark_cli, 'spark_tipo': 'linha',
    })

    # 2) Cobertura de certificado. Sem sparkline: não há série temporal de
    #    "quando cada cliente passou a ter certificado" que valha o desenho.
    if com_cert or vencidos:
        sem_cert = max(total_cli - com_cert, 0)
        cards.append({
            'titulo': 'Com certificado', 'icone': 'fa-certificate',
            'valor': com_cert,
            'apoio': ('de %d cliente%s · %d sem certificado'
                      % (total_cli, '' if total_cli == 1 else 's', sem_cert)),
            'trend': {'tipo': 'neutro', 'rotulo': 'cadastro'},
        })

    # 3) Vencendo em 30 dias — o número que exige ação. Aqui a série é a própria
    #    distribuição por faixa de validade, que é o assunto do card:
    #    [já vencidos, 0–30 dias, 31–90 dias, mais de 90 dias].
    cards.append({
        'titulo': 'Vencendo em 30 dias', 'icone': 'fa-triangle-exclamation',
        'valor': venc30,
        'apoio': ('%d vence%s em 31–90 dias · %d já vencido%s'
                  % (venc90, 'm' if venc90 != 1 else '',
                     vencidos, 's' if vencidos != 1 else '')),
        'trend': {'tipo': 'neutro', 'rotulo': 'validade'},
        'spark': [vencidos, venc30, venc90, max(com_cert - venc30 - venc90, 0)],
        'spark_tipo': 'barra',
    })

    # 4/5/6) Tabelas auxiliares — sem coluna de data utilizável em grupos, e
    # volume pequeno demais em ramos/municípios: vão sem sparkline, de propósito.
    cards.append({
        'titulo': 'Grupos', 'icone': 'fa-users-cog', 'valor': grupos,
        'apoio': '%d ativo%s' % (grupos_at, '' if grupos_at == 1 else 's'),
        'trend': {'tipo': 'neutro', 'rotulo': 'total'},
    })
    cards.append({
        'titulo': 'Ramos de atividade', 'icone': 'fa-industry', 'valor': ramos,
        'apoio': '%d ativo%s' % (ramos_at, '' if ramos_at == 1 else 's'),
        'trend': {'tipo': 'neutro', 'rotulo': 'total'},
    })
    cards.append({
        'titulo': 'Municípios', 'icone': 'fa-map-marked-alt', 'valor': munis,
        'apoio': '%d ativo%s com portal cadastrado' % (munis_at, '' if munis_at == 1 else 's'),
        'trend': {'tipo': 'neutro', 'rotulo': 'total'},
    })

    # 7) Contratos — hoje a tabela está vazia; 0 é o número REAL, não placeholder
    cards.append({
        'titulo': 'Contratos', 'icone': 'fa-file-contract', 'valor': contratos,
        'apoio': ('nenhum contrato cadastrado ainda' if not contratos
                  else '%d ativo%s' % (contratos_at, '' if contratos_at == 1 else 's')),
        'trend': {'tipo': 'neutro', 'rotulo': 'total'},
    })

    counters = {
        'clientes': ativos, 'grupos': grupos, 'ramos': ramos,
        'contratos': contratos, 'municipios': munis,
        'adicionais': len(TIPOS_CADASTROS),
    }
    return {
        'cards': cards,
        'counters': counters,
        'gerado_em_ms': int(datetime.now(timezone.utc).timestamp() * 1000),
    }


@modulos.route('/cadastros/api/destaques')
@permission_required('clientes.index')
def api_cadastros_destaques():
    """Números da home do Cadastros (carrossel + contadores). Cache 60s por usuário."""
    uid = getattr(current_user, 'id', None)
    agora = datetime.now(timezone.utc).timestamp()
    with _CAD_HOME_CACHE_LOCK:
        hit = _CAD_HOME_CACHE.get(uid)
        if hit and (agora - hit[0]) < _CAD_HOME_TTL_S:
            return jsonify(hit[1])
    payload = _cadastros_home_payload()
    with _CAD_HOME_CACHE_LOCK:
        _CAD_HOME_CACHE[uid] = (agora, payload)
    return jsonify(payload)


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
    f_categorias = [v.strip() for v in request.args.getlist('categoria') if v.strip()]
    f_produto_ids = [v.strip() for v in request.args.getlist('produto_id') if v.strip()]
    f_descricao = request.args.get('descricao', '').strip()
    f_emit_cnpjs = [v.strip() for v in request.args.getlist('emit_cnpj') if v.strip()]
    f_ordem_data = request.args.get('ordem_data', 'asc').strip().lower()
    if f_ordem_data not in ('asc', 'desc'):
        f_ordem_data = 'asc'
    buscar = request.args.get('buscar', '').strip()

    empresas = _get_empresas_analise()
    grupos = _get_grupos_analise()
    categorias = _get_categorias_analise()
    emitentes = []
    if f_cliente_id or f_grupo_id:
        where_emit, params_emit = _empresa_where_analise(f_cliente_id, f_grupo_id, alias='n', params=[])
        if f_data_ini:
            where_emit.append('n.data_emissao >= %s')
            params_emit.append(f_data_ini)
        if f_data_fim:
            where_emit.append('n.data_emissao <= %s')
            params_emit.append(f_data_fim)
        where_emit_sql = ('WHERE ' + ' AND '.join(where_emit)) if where_emit else ''
        emitentes = execute_query(
            f"""SELECT n.emit_cnpj, MAX(n.emit_nome) AS emit_nome
                  FROM nfe_importacoes n
                  {where_emit_sql}
                 GROUP BY n.emit_cnpj
                 ORDER BY emit_nome
                 LIMIT 500""",
            tuple(params_emit),
            fetch=True,
        ) or []

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
    fornecedor_cards = []
    fornecedor_cards_auto_open = FORNECEDOR_CARDS_AUTO_OPEN_DEFAULT
    produto_cards = []
    produto_cards_auto_open = PRODUTO_CARDS_AUTO_OPEN_DEFAULT
    produto_historico_rows = []
    detalhamento_rows = []
    erro_filtros = ''
    searched = bool(
        buscar or f_cliente_id or f_grupo_id or
        (f_data_ini != data_ini_default) or
        (f_data_fim != data_fim_default) or
        f_categorias or f_produto_ids or f_descricao or f_emit_cnpjs
    )

    if searched and not (f_cliente_id or f_grupo_id):
        erro_filtros = 'Selecione uma empresa ou um grupo para gerar a análise.'

    if searched and not erro_filtros:
        date_order = 'DESC' if f_ordem_data == 'desc' else 'ASC'
        where, params = _empresa_where_analise(f_cliente_id, f_grupo_id, alias='n', params=[])
        if f_data_ini:
            where.append('n.data_emissao >= %s')
            params.append(f_data_ini)
        if f_data_fim:
            where.append('n.data_emissao <= %s')
            params.append(f_data_fim)
        if f_emit_cnpjs:
            ph = ','.join(['%s'] * len(f_emit_cnpjs))
            where.append(f'n.emit_cnpj IN ({ph})')
            params.extend(f_emit_cnpjs)
        if f_produto_ids:
            valid_ids = []
            for pid in f_produto_ids:
                try:
                    valid_ids.append(int(pid))
                except (ValueError, TypeError):
                    pass
            if valid_ids:
                ph = ','.join(['%s'] * len(valid_ids))
                where.append(f'i.produto_catalogo_id IN ({ph})')
                params.extend(valid_ids)
        elif f_categorias:
            has_sem_vinculo = '__sem_vinculo__' in f_categorias
            real_cats = [c for c in f_categorias if c != '__sem_vinculo__']
            if has_sem_vinculo and real_cats:
                ph = ','.join(['%s'] * len(real_cats))
                where.append(f'(i.produto_catalogo_id IS NULL OR p.categoria IN ({ph}))')
                params.extend(real_cats)
            elif has_sem_vinculo:
                where.append('i.produto_catalogo_id IS NULL')
            elif real_cats:
                ph = ','.join(['%s'] * len(real_cats))
                where.append(f'p.categoria IN ({ph})')
                params.extend(real_cats)
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

        produto_historico_rows = execute_query(
            f"""SELECT n.data_emissao,
                       n.emit_nome,
                       n.emit_cnpj,
                       n.num_nota,
                       n.serie,
                       COALESCE(NULLIF(p.nome, ''), i.descricao) AS produto_nome,
                       COALESCE(NULLIF(p.unidade, ''), i.unidade) AS unidade,
                       i.quantidade,
                       i.valor_unitario,
                       i.valor_total,
                       i.valor_icms
                  FROM nfe_itens i
                  JOIN nfe_importacoes n ON n.id = i.nfe_id
                  LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
                  {where_sql}
                 ORDER BY COALESCE(NULLIF(p.nome, ''), i.descricao),
                          n.data_emissao {date_order},
                          n.id {date_order},
                          i.num_item ASC
                 LIMIT 2000""",
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
                 ORDER BY n.data_emissao {date_order}, n.id {date_order}, i.num_item ASC
                 LIMIT 500""",
            tuple(params),
            fetch=True,
        ) or []

        for collection in (categorias_rows, produtos_rows, fornecedores_rows, produto_historico_rows, detalhamento_rows):
            for row in collection:
                for key in ('total_qtd', 'total_valor', 'total_icms', 'quantidade', 'valor_unitario'):
                    if key in row:
                        row[key] = float(row.get(key) or 0)

        fornecedores_map = {}
        for row in produto_historico_rows:
            emit_cnpj = (row.get('emit_cnpj') or '').strip()
            emit_nome = (row.get('emit_nome') or 'Fornecedor não identificado').strip()
            fornecedor_key = emit_cnpj or emit_nome
            fornecedor = fornecedores_map.get(fornecedor_key)
            if not fornecedor:
                fornecedor = {
                    'emit_nome': emit_nome,
                    'emit_cnpj': emit_cnpj,
                    'total_qtd': 0.0,
                    'total_valor': 0.0,
                    'total_icms': 0.0,
                    'total_itens': 0,
                    'primeira_data': None,
                    'ultima_data': None,
                    'produtos_map': {},
                }
                fornecedores_map[fornecedor_key] = fornecedor

            quantidade = float(row.get('quantidade') or 0)
            valor_total = float(row.get('valor_total') or 0)
            valor_icms = float(row.get('valor_icms') or 0)
            data_emissao = row.get('data_emissao')
            produto_nome = (row.get('produto_nome') or 'Produto não informado').strip()
            unidade = (row.get('unidade') or '').strip()
            produto_key = f'{produto_nome}::{unidade}'

            fornecedor['total_qtd'] += quantidade
            fornecedor['total_valor'] += valor_total
            fornecedor['total_icms'] += valor_icms
            fornecedor['total_itens'] += 1
            if data_emissao:
                if not fornecedor['primeira_data'] or data_emissao < fornecedor['primeira_data']:
                    fornecedor['primeira_data'] = data_emissao
                if not fornecedor['ultima_data'] or data_emissao > fornecedor['ultima_data']:
                    fornecedor['ultima_data'] = data_emissao

            produto = fornecedor['produtos_map'].get(produto_key)
            if not produto:
                produto = {
                    'produto_nome': produto_nome,
                    'unidade': unidade,
                    'total_qtd': 0.0,
                    'total_valor': 0.0,
                    'total_icms': 0.0,
                    'total_itens': 0,
                    'ultima_data': None,
                    'valor_unitario_medio': 0.0,
                }
                fornecedor['produtos_map'][produto_key] = produto

            produto['total_qtd'] += quantidade
            produto['total_valor'] += valor_total
            produto['total_icms'] += valor_icms
            produto['total_itens'] += 1
            if data_emissao and (not produto['ultima_data'] or data_emissao > produto['ultima_data']):
                produto['ultima_data'] = data_emissao

        for fornecedor in fornecedores_map.values():
            produtos_list = []
            for produto in fornecedor['produtos_map'].values():
                if produto['total_qtd'] > 0:
                    produto['valor_unitario_medio'] = produto['total_valor'] / produto['total_qtd']
                produtos_list.append(produto)
            produtos_list.sort(key=lambda p: (p['total_valor'], p['produto_nome']), reverse=True)
            fornecedor['produtos'] = produtos_list
            fornecedor['qtd_produtos'] = len(produtos_list)
            del fornecedor['produtos_map']
            fornecedor_cards.append(fornecedor)

        fornecedor_cards.sort(key=lambda f: (f['total_valor'], f['emit_nome']), reverse=True)

        produtos_map = {}
        for row in produto_historico_rows:
            produto_nome = (row.get('produto_nome') or 'Produto não informado').strip()
            unidade = (row.get('unidade') or '').strip()
            produto_key = f'{produto_nome}::{unidade}'
            produto = produtos_map.get(produto_key)
            if not produto:
                produto = {
                    'produto_nome': produto_nome,
                    'unidade': unidade,
                    'total_qtd': 0.0,
                    'total_valor': 0.0,
                    'total_icms': 0.0,
                    'total_itens': 0,
                    'primeira_data': None,
                    'ultima_data': None,
                    'valor_unitario_medio': 0.0,
                    'fornecedores_set': set(),
                    'notas_set': set(),
                    'itens': [],
                }
                produtos_map[produto_key] = produto

            quantidade = float(row.get('quantidade') or 0)
            valor_total = float(row.get('valor_total') or 0)
            valor_icms = float(row.get('valor_icms') or 0)
            valor_unitario = float(row.get('valor_unitario') or 0)
            data_emissao = row.get('data_emissao')
            emit_nome = (row.get('emit_nome') or 'Fornecedor não identificado').strip()
            emit_cnpj = (row.get('emit_cnpj') or '').strip()
            num_nota = row.get('num_nota')
            serie = row.get('serie')

            produto['total_qtd'] += quantidade
            produto['total_valor'] += valor_total
            produto['total_icms'] += valor_icms
            produto['total_itens'] += 1
            if data_emissao:
                if not produto['primeira_data'] or data_emissao < produto['primeira_data']:
                    produto['primeira_data'] = data_emissao
                if not produto['ultima_data'] or data_emissao > produto['ultima_data']:
                    produto['ultima_data'] = data_emissao

            fornecedor_key = emit_cnpj or emit_nome
            if fornecedor_key:
                produto['fornecedores_set'].add(fornecedor_key)
            produto['notas_set'].add((num_nota, serie, emit_cnpj, data_emissao))

            produto['itens'].append({
                'data_emissao': data_emissao,
                'emit_nome': emit_nome,
                'emit_cnpj': emit_cnpj,
                'num_nota': num_nota,
                'serie': serie,
                'quantidade': quantidade,
                'valor_unitario': valor_unitario,
                'valor_total': valor_total,
                'valor_icms': valor_icms,
            })

        for produto in produtos_map.values():
            total_qtd = float(produto.get('total_qtd') or 0)
            if total_qtd > 0:
                produto['valor_unitario_medio'] = produto['total_valor'] / total_qtd
            produto['qtd_fornecedores'] = len(produto['fornecedores_set'])
            produto['qtd_notas'] = len(produto['notas_set'])
            del produto['fornecedores_set']
            del produto['notas_set']
            produto_cards.append(produto)

        produto_cards.sort(key=lambda p: (p['total_valor'], p['produto_nome']), reverse=True)

    return render_template(
        'analise/compras_nfe.html',
        empresas=empresas,
        grupos=grupos,
        categorias=categorias,
        emitentes=emitentes,
        resumo=resumo,
        categorias_rows=categorias_rows,
        produtos_rows=produtos_rows,
        fornecedores_rows=fornecedores_rows,
        fornecedor_cards=fornecedor_cards,
        fornecedor_cards_auto_open=fornecedor_cards_auto_open,
        produto_cards=produto_cards,
        produto_cards_auto_open=produto_cards_auto_open,
        produto_historico_rows=produto_historico_rows,
        detalhamento_rows=detalhamento_rows,
        searched=searched,
        erro_filtros=erro_filtros,
        f_cliente_id=f_cliente_id,
        f_grupo_id=f_grupo_id,
        f_data_ini=f_data_ini,
        f_data_fim=f_data_fim,
        f_categorias=f_categorias,
        f_produto_ids=f_produto_ids,
        f_descricao=f_descricao,
        f_emit_cnpjs=f_emit_cnpjs,
        f_ordem_data=f_ordem_data,
    )
