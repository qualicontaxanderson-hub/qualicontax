"""Blueprint Escrita Fiscal — Conferência de Compras (NF-e)."""
import logging
import re
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify,
)
from utils.auth_helper import login_required
from utils.db_helper import execute_query
from utils.nfe_parser import parse_nfe_xml
from utils import dropbox_sync
from utils.dropbox_sync import DropboxAuthError
from config import Config

logger = logging.getLogger(__name__)

_MAX_XML_SIZE = 16_000_000  # MEDIUMTEXT max is 16 MB
_DROPBOX_AUTH_ERROR_MSG = (
    'Credenciais Dropbox inválidas ou expiradas. '
    'Verifique DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY e DROPBOX_APP_SECRET.'
)

_UF_LIST = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT',
            'PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']

# Categorias de produtos para Postos de Combustíveis
CATEGORIAS_COMBUSTIVEL = [
    ('Combustíveis', [
        'Gasolina Comum', 'Gasolina Aditivada',
        'Etanol Comum', 'Etanol Aditivado',
        'Diesel S-500 Comum', 'Diesel S-500 Aditivado',
        'Diesel S10 Comum', 'Diesel S10 Aditivado',
    ]),
    ('Loja de Conveniência', ['Cigarros', 'Sorvetes', 'Salgados', 'Outros']),
    ('Lubrificantes e Aditivos', ['Lubrificantes', 'Aditivos', 'Outros']),
    ('Insumos e Despesas', []),
]

escrita_fiscal = Blueprint('escrita_fiscal', __name__, url_prefix='/escrita-fiscal')


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------
def _get_empresas():
    return execute_query(
        "SELECT id, nome_razao_social, cpf_cnpj FROM clientes WHERE situacao='ATIVO' ORDER BY nome_razao_social",
        fetch=True,
    ) or []


def _get_grupos():
    return execute_query(
        "SELECT id, nome FROM grupos_clientes WHERE situacao='ATIVO' ORDER BY nome",
        fetch=True,
    ) or []


def _get_categorias():
    """Retorna categorias e suas subcategorias do banco."""
    cats = execute_query(
        "SELECT id, nome FROM nfe_produto_categorias ORDER BY ordem, nome",
        fetch=True,
    ) or []
    subs = execute_query(
        "SELECT categoria_id, nome FROM nfe_produto_subcategorias ORDER BY ordem, nome",
        fetch=True,
    ) or []
    subs_map = {}
    for s in subs:
        subs_map.setdefault(s['categoria_id'], []).append(s['nome'])
    return [(c['nome'], subs_map.get(c['id'], [])) for c in cats]





def _upsert_vinculo(cliente_id, grupo_id, ramo_atividade_id,
                    emit_cnpj, codigo_produto_xml,
                    descricao_produto_xml, produto_catalogo_id):
    """Insert-or-update nfe_produto_vinculo usando operador NULL-safe (<=>).
    O ON DUPLICATE KEY UPDATE padrão não funciona quando cliente_id/grupo_id
    são NULL porque MySQL permite múltiplas linhas (NULL, NULL, ...) no índice UNIQUE.
    """
    existing = execute_query(
        """SELECT id FROM nfe_produto_vinculo
            WHERE cliente_id        <=> %s
              AND grupo_id          <=> %s
              AND ramo_atividade_id <=> %s
              AND emit_cnpj         =   %s
              AND codigo_produto_xml=   %s
            LIMIT 1""",
        (cliente_id, grupo_id, ramo_atividade_id, emit_cnpj, codigo_produto_xml),
        fetch=True, fetch_one=True,
    )
    if existing:
        execute_query(
            """UPDATE nfe_produto_vinculo
                  SET produto_catalogo_id    = %s,
                      descricao_produto_xml  = %s
                WHERE id = %s""",
            (produto_catalogo_id, descricao_produto_xml, existing['id']),
        )
    else:
        execute_query(
            """INSERT INTO nfe_produto_vinculo
                   (cliente_id, grupo_id, ramo_atividade_id,
                    emit_cnpj, codigo_produto_xml,
                    descricao_produto_xml, produto_catalogo_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (cliente_id, grupo_id, ramo_atividade_id,
             emit_cnpj, codigo_produto_xml,
             descricao_produto_xml, produto_catalogo_id),
        )


def _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=None):
    """Retorna fragmento WHERE + params list para filtro empresa/grupo.

    Para notas importadas sem empresa definida (cliente_id IS NULL),
    faz fallback por dest_cnpj comparado ao cpf_cnpj do cliente selecionado.
    """
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


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/')
@login_required
def index():
    return render_template('escrita_fiscal/index.html')


# ---------------------------------------------------------------------------
# Conferência de Compras — página principal
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/')
@login_required
def conf_compras():
    empresas = _get_empresas()
    grupos = _get_grupos()
    emitentes = execute_query(
        "SELECT DISTINCT emit_cnpj, emit_nome FROM nfe_importacoes ORDER BY emit_nome",
        fetch=True,
    ) or []

    # KPIs começam zerados — serão atualizados via JS ao buscar
    stats = {'total_notas': 0, 'total_valor': 0, 'total_icms': 0,
             'total_pis': 0, 'total_cofins': 0}

    dropbox_ok = dropbox_sync.is_configured()

    return render_template(
        'escrita_fiscal/conf_compras.html',
        stats=stats,
        emitentes=emitentes,
        empresas=empresas,
        grupos=grupos,
        dropbox_configured=dropbox_ok,
        uf_list=_UF_LIST,
        dropbox_folder=Config.DROPBOX_XML_FOLDER,
    )


# ---------------------------------------------------------------------------
# API — notas fiscais (com filtros incluindo empresa/grupo)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/notas')
@login_required
def api_notas():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    f_emit_cnpj = request.args.get('emit_cnpj', '').strip()
    f_data_ini = request.args.get('data_ini', '').strip()
    f_data_fim = request.args.get('data_fim', '').strip()
    f_chave = request.args.get('chave', '').strip()
    f_num_nota = request.args.get('num_nota', '').strip()
    f_cfop = request.args.get('cfop', '').strip()
    f_emit_uf = request.args.get('emit_uf', '').strip()
    f_dest_cnpj = request.args.get('dest_cnpj', '').strip()
    f_vmin = request.args.get('vmin', '').strip()
    f_vmax = request.args.get('vmax', '').strip()
    f_origem = request.args.get('origem', '').strip()
    f_vinc_status = request.args.get('vinc_status', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 50

    where, params = [], []
    _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=params)
    extra_clauses, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra_clauses)

    if f_emit_cnpj:
        where.append('n.emit_cnpj = %s')
        params.append(f_emit_cnpj)
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    if f_chave:
        where.append('n.chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_num_nota:
        where.append('n.num_nota = %s')
        params.append(f_num_nota)
    if f_cfop:
        where.append('n.cfop LIKE %s')
        params.append(f'{f_cfop}%')
    if f_emit_uf:
        where.append('n.emit_uf = %s')
        params.append(f_emit_uf)
    if f_dest_cnpj:
        where.append('n.dest_cnpj LIKE %s')
        params.append(f'%{f_dest_cnpj}%')
    if f_vmin:
        where.append('n.valor_total >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('n.valor_total <= %s')
        params.append(float(f_vmax))
    if f_origem:
        where.append('n.origem = %s')
        params.append(f_origem)
    if f_vinc_status == 'completo':
        where.append(
            "NOT EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NULL)"
            " AND EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id)"
        )
    elif f_vinc_status == 'parcial':
        where.append(
            "EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NOT NULL)"
            " AND EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NULL)"
        )
    elif f_vinc_status == 'sem':
        where.append(
            "NOT EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NOT NULL)"
        )
    elif f_vinc_status == 'incompleto':
        where.append(
            "EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NULL)"
        )

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    offset = (page - 1) * per_page

    count_row = execute_query(
        f"SELECT COUNT(*) AS c FROM nfe_importacoes n {where_sql}",
        tuple(params), fetch=True, fetch_one=True,
    ) or {}
    total = count_row.get('c', 0)

    # KPI aggregates for the current filter
    kpi_row = execute_query(
        f"""SELECT COALESCE(SUM(n.valor_total),0) AS total_valor,
                   COALESCE(SUM(n.valor_icms),0) AS total_icms,
                   COALESCE(SUM(n.valor_pis),0) AS total_pis,
                   COALESCE(SUM(n.valor_cofins),0) AS total_cofins
              FROM nfe_importacoes n {where_sql}""",
        tuple(params), fetch=True, fetch_one=True,
    ) or {}

    rows = execute_query(
        f"""SELECT n.id, n.chave_acesso, n.num_nota, n.serie, n.data_emissao,
                   n.emit_cnpj, n.emit_nome, n.emit_uf,
                   n.dest_cnpj, n.dest_nome,
                   n.valor_total, n.valor_icms, n.valor_pis, n.valor_cofins, n.valor_ipi,
                   n.cfop, n.natureza_operacao, n.origem, n.nome_arquivo,
                   n.importado_em, n.cliente_id, n.grupo_id,
                   c.nome_razao_social AS empresa_nome,
                   g.nome AS grupo_nome,
                   (SELECT COUNT(*) FROM nfe_itens i WHERE i.nfe_id = n.id) AS qtd_itens,
                   (SELECT COUNT(*) FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NOT NULL) AS itens_vinculados
              FROM nfe_importacoes n
              LEFT JOIN clientes c ON c.id = COALESCE(
                  n.cliente_id,
                  (SELECT c2.id FROM clientes c2
                    WHERE REPLACE(REPLACE(REPLACE(c2.cpf_cnpj,'.',''),'/',''),'-','')
                        = REPLACE(REPLACE(REPLACE(n.dest_cnpj,'.',''),'/',''),'-','')
                    LIMIT 1)
              )
              LEFT JOIN grupos_clientes g ON g.id = n.grupo_id
              {where_sql}
             ORDER BY n.data_emissao DESC, n.id DESC
             LIMIT %s OFFSET %s""",
        tuple(params) + (per_page, offset),
        fetch=True,
    ) or []

    for r in rows:
        for k in ('data_emissao', 'importado_em'):
            if r.get(k) and hasattr(r[k], 'isoformat'):
                r[k] = r[k].isoformat()
        for k in ('valor_total', 'valor_icms', 'valor_pis', 'valor_cofins', 'valor_ipi'):
            r[k] = float(r.get(k) or 0)

    return jsonify({
        'total': total, 'page': page, 'per_page': per_page, 'rows': rows,
        'kpi': {k: float(kpi_row.get(k) or 0) for k in
                ('total_valor', 'total_icms', 'total_pis', 'total_cofins')},
    })


# ---------------------------------------------------------------------------
# API — itens de uma NF-e específica
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/itens/<int:nfe_id>')
@login_required
def api_itens(nfe_id):
    nota = execute_query(
        "SELECT id, emit_cnpj, emit_nome, num_nota, data_emissao, dest_nome, cliente_id, grupo_id "
        "FROM nfe_importacoes WHERE id = %s",
        (nfe_id,), fetch=True, fetch_one=True,
    )
    if not nota:
        return jsonify({'error': 'NF-e não encontrada'}), 404

    for k in ('data_emissao',):
        if nota.get(k) and hasattr(nota[k], 'isoformat'):
            nota[k] = nota[k].isoformat()

    itens = execute_query(
        """SELECT i.id, i.num_item, i.codigo_produto, i.descricao, i.ncm, i.cfop,
                  i.unidade, i.quantidade, i.valor_unitario, i.valor_total,
                  i.valor_icms, i.valor_pis, i.valor_cofins,
                  i.produto_catalogo_id,
                  p.nome AS produto_catalogo_nome, p.categoria AS produto_categoria
             FROM nfe_itens i
             LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
            WHERE i.nfe_id = %s
            ORDER BY i.num_item""",
        (nfe_id,), fetch=True,
    ) or []

    # Auto-aplicar regras memorizadas nos itens ainda sem vínculo
    emit_cnpj = nota.get('emit_cnpj', '')
    cliente_id = nota.get('cliente_id')
    grupo_id = nota.get('grupo_id')
    for it in itens:
        if it.get('produto_catalogo_id') is None and it.get('codigo_produto'):
            pid = _auto_vincular(emit_cnpj, it['codigo_produto'], cliente_id, grupo_id)
            if pid:
                execute_query(
                    "UPDATE nfe_itens SET produto_catalogo_id = %s WHERE id = %s",
                    (pid, it['id']),
                )
                prod = execute_query(
                    "SELECT nome, categoria FROM nfe_produtos_catalogo WHERE id = %s",
                    (pid,), fetch=True, fetch_one=True,
                )
                it['produto_catalogo_id'] = pid
                it['produto_catalogo_nome'] = prod['nome'] if prod else None
                it['produto_categoria'] = prod['categoria'] if prod else None

    for it in itens:
        for k in ('quantidade', 'valor_unitario', 'valor_total',
                  'valor_icms', 'valor_pis', 'valor_cofins'):
            it[k] = float(it.get(k) or 0)

    return jsonify({'nota': nota, 'itens': itens})


# ---------------------------------------------------------------------------
# API — sugestão de produto para vínculo automático
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/sugestao-produto')
@login_required
def api_sugestao_produto():
    emit_cnpj = request.args.get('emit_cnpj', '').strip()
    codigo_xml = request.args.get('codigo_produto', '').strip()
    cliente_id = request.args.get('cliente_id', '').strip()
    grupo_id = request.args.get('grupo_id', '').strip()

    if not emit_cnpj or not codigo_xml:
        return jsonify({'produto_id': None})

    # Procura vínculo registrado (específico por empresa → grupo → global)
    row = None
    for cli, grp in [
        (int(cliente_id) if cliente_id else None, None),
        (None, int(grupo_id) if grupo_id else None),
        (None, None),
    ]:
        q = ("SELECT produto_catalogo_id FROM nfe_produto_vinculo "
             "WHERE emit_cnpj = %s AND codigo_produto_xml = %s "
             "AND cliente_id %s AND grupo_id %s LIMIT 1")
        cli_cond = '= %s' if cli is not None else 'IS NULL'
        grp_cond = '= %s' if grp is not None else 'IS NULL'
        query = (f"SELECT produto_catalogo_id FROM nfe_produto_vinculo "
                 f"WHERE emit_cnpj = %s AND codigo_produto_xml = %s "
                 f"AND cliente_id {cli_cond} AND grupo_id {grp_cond} LIMIT 1")
        bind = [emit_cnpj, codigo_xml]
        if cli is not None:
            bind.append(cli)
        if grp is not None:
            bind.append(grp)
        row = execute_query(query, tuple(bind), fetch=True, fetch_one=True)
        if row:
            break

    return jsonify({'produto_id': row['produto_catalogo_id'] if row else None})


# ---------------------------------------------------------------------------
# API — vincular item ao produto do catálogo
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/vincular-produto', methods=['POST'])
@login_required
def api_vincular_produto():
    data = request.get_json(force=True) or {}
    item_id = data.get('item_id')
    produto_id = data.get('produto_id')  # None = desvincular
    salvar_regra = bool(data.get('salvar_regra', True))

    if not item_id:
        return jsonify({'error': 'item_id obrigatório'}), 400

    # Busca o item para obter emit_cnpj e código
    item = execute_query(
        """SELECT i.id, i.nfe_id, i.codigo_produto, i.descricao,
                  n.emit_cnpj, n.cliente_id, n.grupo_id
             FROM nfe_itens i JOIN nfe_importacoes n ON n.id = i.nfe_id
            WHERE i.id = %s""",
        (item_id,), fetch=True, fetch_one=True,
    )
    if not item:
        return jsonify({'error': 'Item não encontrado'}), 404

    # Atualiza o vínculo no item
    execute_query(
        "UPDATE nfe_itens SET produto_catalogo_id = %s WHERE id = %s",
        (produto_id, item_id),
    )

    # Salva regra de auto-vínculo e aplica retroativamente em todos os itens históricos
    retroativos = 0
    if salvar_regra and produto_id:
        emit_cnpj = item['emit_cnpj']
        cod = item['codigo_produto']
        cli = item.get('cliente_id')
        grp = item.get('grupo_id')
        # Descrição do produto conforme XML
        descricao_xml = item.get('descricao') or ''
        # Ramo de atividade do cliente (para escopo da regra global)
        ramo_id = _get_ramo_cliente(cli)
        # Salva regra para a empresa/grupo específico
        _upsert_vinculo(cli, grp, None, emit_cnpj, cod, descricao_xml, produto_id)
        # Salva regra com escopo de ramo de atividade
        _upsert_vinculo(None, None, ramo_id, emit_cnpj, cod, descricao_xml, produto_id)

        # Aplica retroativamente em todos os itens históricos do mesmo
        # emit_cnpj + codigo_produto que ainda estão sem vínculo (exceto o item atual,
        # que já foi atualizado acima).
        if emit_cnpj and cod:
            execute_query(
                """UPDATE nfe_itens i
                      JOIN nfe_importacoes n ON n.id = i.nfe_id
                   SET i.produto_catalogo_id = %s
                   WHERE i.produto_catalogo_id IS NULL
                     AND n.emit_cnpj = %s
                     AND i.codigo_produto = %s
                     AND i.id != %s""",
                (produto_id, emit_cnpj, cod, item_id),
            )
            row = execute_query(
                """SELECT COUNT(*) AS c FROM nfe_itens i
                      JOIN nfe_importacoes n ON n.id = i.nfe_id
                   WHERE i.produto_catalogo_id = %s
                     AND n.emit_cnpj = %s
                     AND i.codigo_produto = %s
                     AND i.id != %s""",
                (produto_id, emit_cnpj, cod, item_id),
                fetch=True, fetch_one=True,
            ) or {}
            retroativos = row.get('c', 0)

    # Nome do produto vinculado
    prod_nome = None
    if produto_id:
        p = execute_query(
            "SELECT nome, categoria FROM nfe_produtos_catalogo WHERE id = %s",
            (produto_id,), fetch=True, fetch_one=True,
        )
        if p:
            prod_nome = p['nome']

    return jsonify({'ok': True, 'produto_nome': prod_nome, 'retroativos': retroativos})


# ---------------------------------------------------------------------------
# API — por emissor
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/por-emissor')
@login_required
def api_por_emissor():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    f_data_ini = request.args.get('data_ini', '').strip()
    f_data_fim = request.args.get('data_fim', '').strip()

    where, params = [], []
    extra, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = execute_query(
        f"""SELECT n.emit_cnpj, n.emit_nome, n.emit_uf,
                   COUNT(*) AS qtd_notas,
                   SUM(n.valor_total) AS total_valor,
                   SUM(n.valor_icms) AS total_icms,
                   SUM(n.valor_pis) AS total_pis,
                   SUM(n.valor_cofins) AS total_cofins
              FROM nfe_importacoes n {where_sql}
             GROUP BY n.emit_cnpj, n.emit_nome, n.emit_uf
             ORDER BY total_valor DESC""",
        tuple(params), fetch=True,
    ) or []

    for r in rows:
        for k in ('total_valor', 'total_icms', 'total_pis', 'total_cofins'):
            r[k] = float(r.get(k) or 0)

    return jsonify(rows)


# ---------------------------------------------------------------------------
# API — por produto
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/por-produto')
@login_required
def api_por_produto():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    f_data_ini = request.args.get('data_ini', '').strip()
    f_data_fim = request.args.get('data_fim', '').strip()
    f_emit_cnpj = request.args.get('emit_cnpj', '').strip()
    f_ncm = request.args.get('ncm', '').strip()
    f_descricao = request.args.get('descricao', '').strip()

    where, params = [], []
    extra, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    if f_emit_cnpj:
        where.append('n.emit_cnpj = %s')
        params.append(f_emit_cnpj)
    if f_ncm:
        where.append('i.ncm LIKE %s')
        params.append(f'{f_ncm}%')
    if f_descricao:
        where.append('i.descricao LIKE %s')
        params.append(f'%{f_descricao}%')

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = execute_query(
        f"""SELECT i.codigo_produto, i.descricao, i.ncm, i.cfop, i.unidade,
                   i.produto_catalogo_id,
                   p.nome AS produto_catalogo_nome, p.categoria AS produto_categoria,
                   COUNT(DISTINCT n.id) AS qtd_notas,
                   SUM(i.quantidade) AS total_qtd,
                   SUM(i.valor_total) AS total_valor,
                   SUM(i.valor_icms) AS total_icms,
                   SUM(i.valor_pis) AS total_pis,
                   SUM(i.valor_cofins) AS total_cofins
              FROM nfe_itens i
              JOIN nfe_importacoes n ON n.id = i.nfe_id
              LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
              {where_sql}
             GROUP BY i.codigo_produto, i.descricao, i.ncm, i.cfop, i.unidade,
                      i.produto_catalogo_id, p.nome, p.categoria
             ORDER BY total_valor DESC
             LIMIT 500""",
        tuple(params), fetch=True,
    ) or []

    for r in rows:
        for k in ('total_qtd', 'total_valor', 'total_icms', 'total_pis', 'total_cofins'):
            r[k] = float(r.get(k) or 0)

    return jsonify(rows)


# ---------------------------------------------------------------------------
# Importar XML — upload manual
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/importar', methods=['POST'])
@login_required
def importar_xml():
    files = request.files.getlist('xml_files')
    cliente_id = request.form.get('cliente_id', '').strip() or None
    grupo_id = request.form.get('grupo_id', '').strip() or None

    if not files or all(f.filename == '' for f in files):
        flash('Nenhum arquivo selecionado.', 'warning')
        return redirect(url_for('escrita_fiscal.conf_compras'))

    ok, dup, err = 0, 0, 0
    errors = []

    for f in files:
        if not f.filename.lower().endswith('.xml'):
            err += 1
            errors.append(f'{f.filename}: não é um arquivo XML')
            continue
        try:
            content = f.read().decode('utf-8', errors='replace')
            parsed = parse_nfe_xml(content)
            result = _save_nfe(parsed, f.filename, 'UPLOAD', content,
                               cliente_id=cliente_id, grupo_id=grupo_id)
            if result == 'dup':
                dup += 1
            else:
                ok += 1
        except ValueError as exc:
            err += 1
            errors.append(f'{f.filename}: {exc}')
        except Exception as exc:
            err += 1
            errors.append(f'{f.filename}: erro inesperado — {exc}')

    msgs = []
    if ok:
        msgs.append(f'{ok} nota(s) importada(s) com sucesso.')
    if dup:
        msgs.append(f'{dup} nota(s) já existiam (duplicadas, ignoradas).')
    if err:
        msgs.append(f'{err} arquivo(s) com erro.')
    flash(' '.join(msgs) or 'Nenhuma nota processada.', 'success' if ok else 'warning')
    for e in errors[:5]:
        flash(e, 'danger')

    return redirect(url_for('escrita_fiscal.conf_compras'))


# ---------------------------------------------------------------------------
# Importar XML — sincronização com Dropbox
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/sync-dropbox', methods=['POST'])
@login_required
def sync_dropbox():
    if not dropbox_sync.is_configured():
        return jsonify({'error': 'Dropbox não configurado. Defina DROPBOX_ACCESS_TOKEN.'}), 400

    data = request.get_json(force=True) or {}
    cliente_id = data.get('cliente_id') or None
    grupo_id = data.get('grupo_id') or None

    files = dropbox_sync.list_xml_files()
    if not files:
        return jsonify({'ok': 0, 'dup': 0, 'err': 0,
                        'msg': 'Nenhum arquivo XML encontrado na pasta Dropbox.'}), 200

    ok, dup, err = 0, 0, 0
    for info in files:
        content = dropbox_sync.download_xml(info['path'])
        if content is None:
            err += 1
            continue
        try:
            parsed = parse_nfe_xml(content)
            result = _save_nfe(parsed, info['name'], 'DROPBOX', content,
                               cliente_id=cliente_id, grupo_id=grupo_id)
            if result == 'dup':
                dup += 1
            else:
                ok += 1
        except Exception:
            err += 1

    total = len(files)
    return jsonify({
        'ok': ok, 'dup': dup, 'err': err,
        'msg': f'{total} arquivo(s) lido(s). {ok} importado(s), {dup} duplicado(s), {err} erro(s).',
    })


# ---------------------------------------------------------------------------
# Importar XML — sincronização com Dropbox por departamento
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/importar-dropbox', methods=['POST'])
@login_required
def api_importar_dropbox():
    """Lê arquivos da pasta NOVO do departamento, importa e move para IMPORTADOS ou ERROS."""
    if not dropbox_sync.is_configured():
        return jsonify({'error': 'Dropbox não configurado. Defina DROPBOX_APP_KEY e DROPBOX_REFRESH_TOKEN.'}), 400

    data = request.get_json(force=True) or {}
    departamento = data.get('departamento', '').strip()
    cliente_id = data.get('cliente_id') or None
    grupo_id = data.get('grupo_id') or None

    if not departamento or departamento not in dropbox_sync.DEPARTAMENTOS:
        return jsonify({'error': 'Departamento inválido.'}), 400

    svc = dropbox_sync._service

    logger.info('Importar Dropbox: departamento=%r cliente_id=%r grupo_id=%r',
                departamento, cliente_id, grupo_id)

    # Resolve empresa fixa quando cliente_id ou grupo_id são fornecidos pelo front-end.
    # Quando nenhum dos dois é fornecido, a empresa é detectada arquivo a arquivo
    # a partir do dest_cnpj do XML (ver loop abaixo).
    empresa_nome_fixo = None
    empresa_numero_fixo = None
    if cliente_id:
        c = execute_query(
            "SELECT numero_cliente, nome_razao_social FROM clientes WHERE id = %s",
            (int(cliente_id),), fetch=True, fetch_one=True,
        )
        if c:
            empresa_nome_fixo = c['nome_razao_social']
            empresa_numero_fixo = c.get('numero_cliente') or None
    elif grupo_id:
        g = execute_query(
            "SELECT nome FROM grupos_clientes WHERE id = %s",
            (int(grupo_id),), fetch=True, fetch_one=True,
        )
        if g:
            empresa_nome_fixo = g['nome']

    pasta_novo = svc.pasta_novo(departamento)
    logger.info('Buscando XMLs em: %r', pasta_novo)
    try:
        files = svc.list_xml_files(pasta_novo)
    except DropboxAuthError:
        return jsonify({'error': _DROPBOX_AUTH_ERROR_MSG}), 401

    if not files:
        return jsonify({
            'ok': 0, 'dup': 0, 'err': 0, 'moved_ok': 0, 'moved_err': 0,
            'msg': 'Nenhum arquivo XML encontrado na pasta NOVO.',
        }), 200

    # Processa no máximo 50 arquivos por chamada para evitar timeout do worker.
    # Se houver mais arquivos, o front-end deve chamar novamente até receber
    # has_more=False ou msg indicando que não há mais arquivos.
    _BATCH_LIMIT = 50
    has_more = len(files) > _BATCH_LIMIT
    files = files[:_BATCH_LIMIT]

    now = datetime.now()
    # Cache de pastas já criadas no Dropbox para evitar chamadas redundantes.
    _pastas_criadas: set = set()

    def _get_or_create_pasta(path: str) -> str:
        if path not in _pastas_criadas:
            svc.ensure_folder(path)
            _pastas_criadas.add(path)
        return path

    ok, dup, err, moved_ok, moved_err = 0, 0, 0, 0, 0
    details = []

    for info in files:
        try:
            raw = svc.download_file(info['path'])
        except DropboxAuthError:
            return jsonify({'error': _DROPBOX_AUTH_ERROR_MSG}), 401
        if raw is None:
            err += 1
            details.append(f"{info['name']}: falha ao baixar do Dropbox")
            # Sem XML válido usa empresa/data padrão para a pasta de erros
            _dt = now
            _nome = empresa_nome_fixo or 'GLOBAL'
            _num = empresa_numero_fixo
            try:
                pasta_err = _get_or_create_pasta(
                    svc.pasta_erros(departamento, _nome, _dt, empresa_numero=_num))
                if svc.move_file(info['path'], f"{pasta_err}/{info['name']}"):
                    moved_err += 1
            except DropboxAuthError:
                logger.warning('Falha de autenticação ao mover %s para erros', info['name'])
            continue

        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            content = raw.decode('latin-1', errors='replace')

        try:
            parsed = parse_nfe_xml(content)

            # Determina empresa para a pasta: usa a seleção explícita do usuário;
            # caso não tenha, tenta detectar pelo CNPJ do XML.
            # Prioridade: dest_cnpj (empresa compradora) → emit_cnpj (empresa vendedora).
            _nome = empresa_nome_fixo
            _num = empresa_numero_fixo
            if _nome is None:
                for _cnpj_field in ('dest_cnpj', 'emit_cnpj'):
                    _cnpj_digits = re.sub(r'\D', '', parsed['header'].get(_cnpj_field, ''))
                    if len(_cnpj_digits) >= 11:
                        found = execute_query(
                            "SELECT id, numero_cliente, nome_razao_social FROM clientes "
                            "WHERE REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-','') = %s LIMIT 1",
                            (_cnpj_digits,), fetch=True, fetch_one=True,
                        )
                        if found:
                            _nome = found['nome_razao_social']
                            _num = found.get('numero_cliente') or None
                            logger.info(
                                '%s: empresa detectada por %s → %s',
                                info['name'], _cnpj_field, _nome,
                            )
                            break
                if _nome is None:
                    _nome = 'GLOBAL'
                    logger.warning(
                        '%s: empresa não detectada (dest_cnpj=%r emit_cnpj=%r) → GLOBAL',
                        info['name'],
                        parsed['header'].get('dest_cnpj', ''),
                        parsed['header'].get('emit_cnpj', ''),
                    )

            # Usa a data de emissão do XML para o mês/ano da pasta;
            # cai de volta para a data atual se o campo não estiver disponível.
            _dt = parsed['header'].get('data_emissao') or now
            if _dt is now:
                logger.warning('%s: data_emissao ausente no XML, usando data atual', info['name'])

            pasta_imp = _get_or_create_pasta(
                svc.pasta_importados(departamento, _nome, _dt, empresa_numero=_num))
            pasta_err = _get_or_create_pasta(
                svc.pasta_erros(departamento, _nome, _dt, empresa_numero=_num))

            result = _save_nfe(parsed, info['name'], 'DROPBOX', content,
                               cliente_id=cliente_id, grupo_id=grupo_id)
            if result == 'dup':
                dup += 1
            else:
                ok += 1
            # Sucesso (incluindo duplicata) → move para IMPORTADOS
            try:
                if svc.move_file(info['path'], f"{pasta_imp}/{info['name']}"):
                    moved_ok += 1
            except DropboxAuthError:
                logger.warning('Falha de autenticação ao mover %s para importados', info['name'])
        except DropboxAuthError:
            return jsonify({'error': _DROPBOX_AUTH_ERROR_MSG}), 401
        except Exception as exc:
            err += 1
            details.append(f"{info['name']}: {exc}")
            _dt = now
            _nome = empresa_nome_fixo or 'GLOBAL'
            _num = empresa_numero_fixo
            try:
                pasta_err = _get_or_create_pasta(
                    svc.pasta_erros(departamento, _nome, _dt, empresa_numero=_num))
                if svc.move_file(info['path'], f"{pasta_err}/{info['name']}"):
                    moved_err += 1
            except DropboxAuthError:
                logger.warning('Falha de autenticação ao mover %s para erros', info['name'])

    total = len(files)
    msg = (f'{total} arquivo(s) lido(s). {ok} importado(s), '
           f'{dup} duplicado(s), {err} com erro.')
    if moved_ok or moved_err:
        msg += f' {moved_ok} movido(s) para IMPORTADOS, {moved_err} movido(s) para ERROS.'
    if has_more:
        msg += ' Há mais arquivos na fila — clique em Importar novamente para continuar.'

    return jsonify({
        'ok': ok, 'dup': dup, 'err': err,
        'moved_ok': moved_ok, 'moved_err': moved_err,
        'has_more': has_more,
        'msg': msg,
        'details': details[:10],
    })


# ---------------------------------------------------------------------------
# Excluir NF-e
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/excluir/<int:nfe_id>', methods=['POST'])
@login_required
def excluir_nfe(nfe_id):
    execute_query("DELETE FROM nfe_importacoes WHERE id = %s", (nfe_id,))
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    flash('Nota fiscal excluída.', 'success')
    return redirect(url_for('escrita_fiscal.conf_compras'))


# ---------------------------------------------------------------------------
# Catálogo de Produtos — listagem
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/')
@login_required
def produtos_catalogo():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()

    where, params = [], []
    if f_cliente_id:
        where.append('cliente_id = %s')
        params.append(int(f_cliente_id))
    if f_grupo_id:
        where.append('grupo_id = %s')
        params.append(int(f_grupo_id))

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    produtos = execute_query(
        f"""SELECT p.id, p.codigo, p.nome, p.categoria, p.subcategoria, p.tipo_uso, p.unidade,
                   p.ativo, p.cliente_id, p.grupo_id,
                   c.nome_razao_social AS empresa_nome,
                   g.nome AS grupo_nome
              FROM nfe_produtos_catalogo p
              LEFT JOIN clientes c ON c.id = p.cliente_id
              LEFT JOIN grupos_clientes g ON g.id = p.grupo_id
              {where_sql}
             ORDER BY p.categoria, p.nome""",
        tuple(params) if params else None,
        fetch=True,
    ) or []

    empresas = _get_empresas()
    grupos = _get_grupos()
    categorias = _get_categorias()

    # Fetch full category objects (id + nome) for the management modal
    cats_db = execute_query(
        "SELECT id, nome FROM nfe_produto_categorias ORDER BY ordem, nome",
        fetch=True,
    ) or []
    subs_db = execute_query(
        "SELECT id, categoria_id, nome FROM nfe_produto_subcategorias ORDER BY ordem, nome",
        fetch=True,
    ) or []

    return render_template(
        'escrita_fiscal/produtos_catalogo.html',
        produtos=produtos,
        empresas=empresas,
        grupos=grupos,
        f_cliente_id=f_cliente_id,
        f_grupo_id=f_grupo_id,
        categorias=categorias,
        cats_db=cats_db,
        subs_db=subs_db,
    )


# ---------------------------------------------------------------------------
# Catálogo de Produtos — salvar (criar / editar)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/salvar', methods=['POST'])
@login_required
def produtos_catalogo_salvar():
    pid = request.form.get('id', '').strip() or None
    cliente_id = request.form.get('cliente_id', '').strip() or None
    grupo_id = request.form.get('grupo_id', '').strip() or None
    codigo = request.form.get('codigo', '').strip()
    nome = request.form.get('nome', '').strip()
    categoria = request.form.get('categoria', '').strip()
    subcategoria = request.form.get('subcategoria', '').strip()
    tipo_uso = request.form.get('tipo_uso', '').strip() or None
    unidade = request.form.get('unidade', '').strip()
    ativo = 1 if request.form.get('ativo') else 0

    if not nome:
        flash('Nome do produto é obrigatório.', 'danger')
        return redirect(url_for('escrita_fiscal.produtos_catalogo'))

    if pid:
        execute_query(
            """UPDATE nfe_produtos_catalogo
                  SET cliente_id=%s, grupo_id=%s, codigo=%s, nome=%s,
                      categoria=%s, subcategoria=%s, tipo_uso=%s, unidade=%s, ativo=%s
                WHERE id=%s""",
            (cliente_id, grupo_id, codigo, nome, categoria, subcategoria, tipo_uso, unidade, ativo, int(pid)),
        )
        flash('Produto atualizado.', 'success')
    else:
        execute_query(
            """INSERT INTO nfe_produtos_catalogo
                   (cliente_id, grupo_id, codigo, nome, categoria, subcategoria, tipo_uso, unidade, ativo)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (cliente_id, grupo_id, codigo, nome, categoria, subcategoria, tipo_uso, unidade, ativo),
        )
        flash('Produto cadastrado.', 'success')

    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


# ---------------------------------------------------------------------------
# Catálogo de Produtos — excluir
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/excluir/<int:pid>', methods=['POST'])
@login_required
def produtos_catalogo_excluir(pid):
    execute_query("DELETE FROM nfe_produtos_catalogo WHERE id = %s", (pid,))
    flash('Produto excluído.', 'success')
    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


# ---------------------------------------------------------------------------
# Categorias — criar
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/categorias/criar', methods=['POST'])
@login_required
def categoria_criar():
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash('Nome da categoria é obrigatório.', 'danger')
        return redirect(url_for('escrita_fiscal.produtos_catalogo'))
    existing = execute_query(
        "SELECT id FROM nfe_produto_categorias WHERE nome = %s", (nome,), fetch=True, fetch_one=True,
    )
    if existing:
        flash('Já existe uma categoria com esse nome.', 'warning')
    else:
        max_ordem = execute_query(
            "SELECT COALESCE(MAX(ordem),0)+1 AS o FROM nfe_produto_categorias", fetch=True, fetch_one=True,
        ) or {}
        execute_query(
            "INSERT INTO nfe_produto_categorias (nome, ordem) VALUES (%s, %s)",
            (nome, max_ordem.get('o', 0)),
        )
        flash(f'Categoria "{nome}" criada.', 'success')
    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


# ---------------------------------------------------------------------------
# Categorias — excluir
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/categorias/excluir/<int:cid>', methods=['POST'])
@login_required
def categoria_excluir(cid):
    cat = execute_query(
        "SELECT nome FROM nfe_produto_categorias WHERE id = %s", (cid,), fetch=True, fetch_one=True,
    )
    if not cat:
        flash('Categoria não encontrada.', 'danger')
        return redirect(url_for('escrita_fiscal.produtos_catalogo'))
    in_use = execute_query(
        "SELECT COUNT(*) AS cnt FROM nfe_produtos_catalogo WHERE categoria = %s",
        (cat['nome'],), fetch=True, fetch_one=True,
    ) or {}
    if in_use.get('cnt', 0) > 0:
        flash(f'A categoria "{cat["nome"]}" está em uso por produtos e não pode ser excluída.', 'danger')
    else:
        execute_query("DELETE FROM nfe_produto_categorias WHERE id = %s", (cid,))
        flash(f'Categoria "{cat["nome"]}" excluída.', 'success')
    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


# ---------------------------------------------------------------------------
# Sub-Categorias — criar
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/subcategorias/criar', methods=['POST'])
@login_required
def subcategoria_criar():
    categoria_id = request.form.get('categoria_id', '').strip()
    nome = request.form.get('nome', '').strip()
    if not categoria_id or not nome:
        flash('Categoria e nome da sub-categoria são obrigatórios.', 'danger')
        return redirect(url_for('escrita_fiscal.produtos_catalogo'))
    existing = execute_query(
        "SELECT id FROM nfe_produto_subcategorias WHERE categoria_id = %s AND nome = %s",
        (int(categoria_id), nome), fetch=True, fetch_one=True,
    )
    if existing:
        flash('Já existe uma sub-categoria com esse nome nessa categoria.', 'warning')
    else:
        max_ordem = execute_query(
            "SELECT COALESCE(MAX(ordem),0)+1 AS o FROM nfe_produto_subcategorias WHERE categoria_id = %s",
            (int(categoria_id),), fetch=True, fetch_one=True,
        ) or {}
        execute_query(
            "INSERT INTO nfe_produto_subcategorias (categoria_id, nome, ordem) VALUES (%s, %s, %s)",
            (int(categoria_id), nome, max_ordem.get('o', 0)),
        )
        flash(f'Sub-categoria "{nome}" criada.', 'success')
    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


# ---------------------------------------------------------------------------
# Sub-Categorias — excluir
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/subcategorias/excluir/<int:sid>', methods=['POST'])
@login_required
def subcategoria_excluir(sid):
    sub = execute_query(
        "SELECT s.nome, c.nome AS cat_nome FROM nfe_produto_subcategorias s "
        "JOIN nfe_produto_categorias c ON c.id = s.categoria_id WHERE s.id = %s",
        (sid,), fetch=True, fetch_one=True,
    )
    if not sub:
        flash('Sub-categoria não encontrada.', 'danger')
        return redirect(url_for('escrita_fiscal.produtos_catalogo'))
    in_use = execute_query(
        "SELECT COUNT(*) AS cnt FROM nfe_produtos_catalogo WHERE subcategoria = %s",
        (sub['nome'],), fetch=True, fetch_one=True,
    ) or {}
    if in_use.get('cnt', 0) > 0:
        flash(f'A sub-categoria "{sub["nome"]}" está em uso e não pode ser excluída.', 'danger')
    else:
        execute_query("DELETE FROM nfe_produto_subcategorias WHERE id = %s", (sid,))
        flash(f'Sub-categoria "{sub["nome"]}" excluída.', 'success')
    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


@escrita_fiscal.route('/conf-compras/api/produtos-catalogo')
@login_required
def api_produtos_catalogo():
    cliente_id = request.args.get('cliente_id', '').strip()
    grupo_id = request.args.get('grupo_id', '').strip()

    # Retorna produtos do cliente + do grupo + globais
    conds, params = ["ativo = 1"], []
    scope_or = ["(cliente_id IS NULL AND grupo_id IS NULL)"]
    if cliente_id:
        scope_or.append("cliente_id = %s")
        params.append(int(cliente_id))
    if grupo_id:
        scope_or.append("grupo_id = %s")
        params.append(int(grupo_id))
    conds.append('(' + ' OR '.join(scope_or) + ')')

    rows = execute_query(
        "SELECT id, codigo, nome, categoria, subcategoria, unidade "
        "FROM nfe_produtos_catalogo WHERE " + ' AND '.join(conds) +
        " ORDER BY categoria, nome",
        tuple(params) if params else None,
        fetch=True,
    ) or []

    return jsonify(rows)


# ---------------------------------------------------------------------------
# API — vincular todos os itens de uma NF-e ao mesmo produto
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/vincular-todos', methods=['POST'])
@login_required
def api_vincular_todos():
    data = request.get_json(force=True) or {}
    nfe_id = data.get('nfe_id')
    produto_id = data.get('produto_id')

    if not nfe_id or not produto_id:
        return jsonify({'error': 'nfe_id e produto_id são obrigatórios'}), 400

    nota = execute_query(
        "SELECT id, emit_cnpj, cliente_id, grupo_id FROM nfe_importacoes WHERE id = %s",
        (nfe_id,), fetch=True, fetch_one=True,
    )
    if not nota:
        return jsonify({'error': 'NF-e não encontrada'}), 404

    itens = execute_query(
        "SELECT id, codigo_produto, descricao FROM nfe_itens WHERE nfe_id = %s",
        (nfe_id,), fetch=True,
    ) or []

    emit_cnpj = nota['emit_cnpj']
    cli = nota.get('cliente_id')
    grp = nota.get('grupo_id')
    ramo_id = _get_ramo_cliente(cli)

    item_ids = [it['id'] for it in itens]
    for it in itens:
        execute_query(
            "UPDATE nfe_itens SET produto_catalogo_id = %s WHERE id = %s",
            (produto_id, it['id']),
        )
        cod = it['codigo_produto']
        descricao_xml = it.get('descricao') or ''
        if cod:
            _upsert_vinculo(cli, grp, None, emit_cnpj, cod, descricao_xml, produto_id)
            _upsert_vinculo(None, None, ramo_id, emit_cnpj, cod, descricao_xml, produto_id)

            # Aplica retroativamente em itens históricos sem vínculo do mesmo
            # emit_cnpj + codigo_produto, fora desta NF
            if emit_cnpj:
                placeholders = ','.join(['%s'] * len(item_ids))
                execute_query(
                    f"""UPDATE nfe_itens i
                          JOIN nfe_importacoes n ON n.id = i.nfe_id
                       SET i.produto_catalogo_id = %s
                       WHERE i.produto_catalogo_id IS NULL
                         AND n.emit_cnpj = %s
                         AND i.codigo_produto = %s
                         AND i.id NOT IN ({placeholders})""",
                    tuple([produto_id, emit_cnpj, cod] + item_ids),
                )

    prod = execute_query(
        "SELECT nome FROM nfe_produtos_catalogo WHERE id = %s",
        (produto_id,), fetch=True, fetch_one=True,
    )
    prod_nome = prod['nome'] if prod else ''
    return jsonify({'ok': True, 'vinculados': len(itens), 'produto_nome': prod_nome})


# ---------------------------------------------------------------------------
# Memorizações — listagem
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/memorizacoes/')
@login_required
def memorizacoes():
    rows = execute_query(
        """SELECT v.id, v.cliente_id, v.grupo_id, v.ramo_atividade_id,
                  v.emit_cnpj, v.codigo_produto_xml,
                  COALESCE(v.descricao_produto_xml,
                      (SELECT i.descricao FROM nfe_itens i
                         JOIN nfe_importacoes n ON n.id = i.nfe_id
                        WHERE n.emit_cnpj = v.emit_cnpj
                          AND i.codigo_produto = v.codigo_produto_xml
                        LIMIT 1)
                  ) AS descricao_produto_xml,
                  v.produto_catalogo_id, v.criado_em,
                  p.nome AS produto_nome, p.categoria AS produto_categoria,
                  c.nome_razao_social AS empresa_nome,
                  g.nome AS grupo_nome,
                  ra.nome AS ramo_nome,
                  (SELECT n.emit_nome FROM nfe_importacoes n
                    WHERE n.emit_cnpj = v.emit_cnpj LIMIT 1) AS fornecedor_nome
             FROM nfe_produto_vinculo v
             LEFT JOIN nfe_produtos_catalogo p ON p.id = v.produto_catalogo_id
             LEFT JOIN clientes c ON c.id = v.cliente_id
             LEFT JOIN grupos_clientes g ON g.id = v.grupo_id
             LEFT JOIN ramos_atividade ra ON ra.id = v.ramo_atividade_id
            ORDER BY v.emit_cnpj, v.codigo_produto_xml""",
        fetch=True,
    ) or []

    for r in rows:
        if r.get('criado_em') and hasattr(r['criado_em'], 'isoformat'):
            r['criado_em'] = r['criado_em'].isoformat()

    # Catálogo de produtos para o modal de edição
    catalogo = execute_query(
        "SELECT id, nome, categoria FROM nfe_produtos_catalogo WHERE ativo = 1 ORDER BY categoria, nome",
        fetch=True,
    ) or []

    return render_template('escrita_fiscal/memorizacoes.html', rows=rows, catalogo=catalogo)


# ---------------------------------------------------------------------------
# Memorizações — listar empresas que usam a memorização
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/memorizacoes/empresas-vinculadas/<int:vid>')
@login_required
def memorizacoes_empresas(vid):
    vinculo = execute_query(
        "SELECT emit_cnpj, cliente_id, grupo_id, ramo_atividade_id FROM nfe_produto_vinculo WHERE id = %s",
        (vid,), fetch=True, fetch_one=True,
    )
    if not vinculo:
        return jsonify({'error': 'Memorização não encontrada'}), 404

    emit_cnpj = vinculo['emit_cnpj']

    if vinculo.get('cliente_id'):
        # Regra específica para uma empresa
        empresas = execute_query(
            "SELECT id, nome_razao_social, cpf_cnpj FROM clientes WHERE id = %s",
            (vinculo['cliente_id'],), fetch=True,
        ) or []
    elif vinculo.get('ramo_atividade_id'):
        # Regra por ramo de atividade — lista clientes do mesmo ramo que importaram desse fornecedor
        empresas = execute_query(
            """SELECT DISTINCT c.id, c.nome_razao_social, c.cpf_cnpj
                 FROM clientes c
                 JOIN cliente_ramo_atividade_relacao crar ON crar.cliente_id = c.id
                   AND crar.ramo_atividade_id = %s
                 JOIN nfe_importacoes n ON (
                     n.cliente_id = c.id
                     OR (n.cliente_id IS NULL
                         AND REPLACE(REPLACE(REPLACE(c.cpf_cnpj,'.',''),'/',''),'-','')
                           = REPLACE(REPLACE(REPLACE(n.dest_cnpj,'.',''),'/',''),'-',''))
                 )
                   AND n.emit_cnpj = %s
                ORDER BY c.nome_razao_social""",
            (vinculo['ramo_atividade_id'], emit_cnpj), fetch=True,
        ) or []
    else:
        # Regra global — todas as empresas que já importaram desse fornecedor
        # (considera tanto cliente_id explícito quanto match por dest_cnpj)
        empresas = execute_query(
            """SELECT DISTINCT c.id, c.nome_razao_social, c.cpf_cnpj
                 FROM clientes c
                 JOIN nfe_importacoes n ON (
                     n.cliente_id = c.id
                     OR (n.cliente_id IS NULL
                         AND REPLACE(REPLACE(REPLACE(c.cpf_cnpj,'.',''),'/',''),'-','')
                           = REPLACE(REPLACE(REPLACE(n.dest_cnpj,'.',''),'/',''),'-',''))
                 )
                 WHERE n.emit_cnpj = %s
                ORDER BY c.nome_razao_social""",
            (emit_cnpj,), fetch=True,
        ) or []

    return jsonify({'ok': True, 'empresas': [dict(e) for e in empresas]})


# ---------------------------------------------------------------------------
# Memorizações — editar (troca produto vinculado)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/memorizacoes/editar/<int:vid>', methods=['POST'])
@login_required
def memorizacoes_editar(vid):
    data = request.get_json(force=True) or {}
    produto_id = data.get('produto_id')
    if not produto_id:
        return jsonify({'error': 'produto_id obrigatório'}), 400

    execute_query(
        "UPDATE nfe_produto_vinculo SET produto_catalogo_id = %s WHERE id = %s",
        (int(produto_id), vid),
    )

    prod = execute_query(
        "SELECT nome, categoria FROM nfe_produtos_catalogo WHERE id = %s",
        (int(produto_id),), fetch=True, fetch_one=True,
    )
    return jsonify({
        'ok': True,
        'produto_nome': prod['nome'] if prod else '',
        'produto_categoria': prod['categoria'] if prod else '',
    })


# ---------------------------------------------------------------------------
# Memorizações — excluir
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/memorizacoes/excluir/<int:vid>', methods=['POST'])
@login_required
def memorizacoes_excluir(vid):
    execute_query("DELETE FROM nfe_produto_vinculo WHERE id = %s", (vid,))
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    flash('Memorização excluída.', 'success')
    return redirect(url_for('escrita_fiscal.memorizacoes'))


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------
def _save_nfe(parsed: dict, nome_arquivo: str, origem: str, xml_raw: str,
              cliente_id=None, grupo_id=None):
    """
    Salva NF-e parseada no banco.
    Returns: 'ok' ou 'dup'
    """
    h = parsed['header']
    chave = h['chave_acesso']

    existing = execute_query(
        "SELECT id FROM nfe_importacoes WHERE chave_acesso = %s",
        (chave,), fetch=True, fetch_one=True,
    )
    if existing:
        return 'dup'

    xml_raw_store = xml_raw[:_MAX_XML_SIZE] if xml_raw else ''
    cli = int(cliente_id) if cliente_id else None
    grp = int(grupo_id) if grupo_id else None

    # Auto-detect empresa from dest_cnpj when not explicitly provided
    if cli is None:
        dest_cnpj_raw = h.get('dest_cnpj', '')
        dest_digits = re.sub(r'\D', '', dest_cnpj_raw)
        if len(dest_digits) >= 11:
            found = execute_query(
                "SELECT id FROM clientes WHERE REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-','') = %s LIMIT 1",
                (dest_digits,), fetch=True, fetch_one=True,
            )
            if found:
                cli = found['id']

    nfe_id = execute_query(
        """INSERT INTO nfe_importacoes
               (cliente_id, grupo_id, nome_arquivo, chave_acesso, num_nota, serie,
                data_emissao, emit_cnpj, emit_nome, emit_uf,
                dest_cnpj, dest_nome,
                valor_total, valor_icms, valor_pis, valor_cofins, valor_ipi,
                cfop, natureza_operacao, xml_raw, origem)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            cli, grp, nome_arquivo, chave, h['num_nota'], h['serie'],
            h['data_emissao'], h['emit_cnpj'], h['emit_nome'], h['emit_uf'],
            h['dest_cnpj'], h['dest_nome'],
            h['valor_total'], h['valor_icms'], h['valor_pis'],
            h['valor_cofins'], h['valor_ipi'],
            h['cfop'], h['natureza_operacao'], xml_raw_store, origem,
        ),
    )

    for item in parsed.get('itens', []):
        # Tenta auto-vincular produto pelo emit_cnpj + codigo_produto
        prod_id = _auto_vincular(h['emit_cnpj'], item['codigo_produto'], cli, grp)

        execute_query(
            """INSERT INTO nfe_itens
                   (nfe_id, num_item, codigo_produto, descricao, ncm, cfop,
                    unidade, quantidade, valor_unitario, valor_total,
                    valor_icms, valor_pis, valor_cofins, produto_catalogo_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                nfe_id, item['num_item'], item['codigo_produto'],
                item['descricao'], item['ncm'], item['cfop'],
                item['unidade'], item['quantidade'], item['valor_unitario'],
                item['valor_total'], item['valor_icms'],
                item['valor_pis'], item['valor_cofins'], prod_id,
            ),
        )

    return 'ok'


def _get_ramo_cliente(cliente_id):
    """Retorna o primeiro ramo_atividade_id do cliente, ou None."""
    if not cliente_id:
        return None
    row = execute_query(
        "SELECT ramo_atividade_id FROM cliente_ramo_atividade_relacao WHERE cliente_id = %s LIMIT 1",
        (cliente_id,), fetch=True, fetch_one=True,
    )
    return row['ramo_atividade_id'] if row else None


def _auto_vincular(emit_cnpj: str, codigo_produto: str, cliente_id, grupo_id):
    """
    Tenta encontrar um vínculo automático registrado para o par emit_cnpj + codigo_produto.
    Busca na ordem: empresa específica → grupo → ramo de atividade → global.
    """
    if not emit_cnpj or not codigo_produto:
        return None

    # 1. Empresa/grupo específico
    for cli, grp in [
        (cliente_id, None),
        (None, grupo_id),
    ]:
        if cli is None and grp is None:
            continue
        cli_cond = '= %s' if cli is not None else 'IS NULL'
        grp_cond = '= %s' if grp is not None else 'IS NULL'
        query = (f"SELECT produto_catalogo_id FROM nfe_produto_vinculo "
                 f"WHERE emit_cnpj = %s AND codigo_produto_xml = %s "
                 f"AND cliente_id {cli_cond} AND grupo_id {grp_cond} LIMIT 1")
        bind = [emit_cnpj, codigo_produto]
        if cli is not None:
            bind.append(cli)
        if grp is not None:
            bind.append(grp)
        row = execute_query(query, tuple(bind), fetch=True, fetch_one=True)
        if row:
            return row['produto_catalogo_id']

    # 2. Ramo de atividade do cliente
    if cliente_id:
        ramos = execute_query(
            "SELECT ramo_atividade_id FROM cliente_ramo_atividade_relacao WHERE cliente_id = %s",
            (cliente_id,), fetch=True,
        ) or []
        ramo_ids = [r['ramo_atividade_id'] for r in ramos if r.get('ramo_atividade_id')]
        if ramo_ids:
            placeholders = ','.join(['%s'] * len(ramo_ids))
            row = execute_query(
                f"SELECT produto_catalogo_id FROM nfe_produto_vinculo "
                f"WHERE emit_cnpj = %s AND codigo_produto_xml = %s "
                f"AND cliente_id IS NULL AND grupo_id IS NULL "
                f"AND ramo_atividade_id IN ({placeholders}) LIMIT 1",
                tuple([emit_cnpj, codigo_produto] + ramo_ids),
                fetch=True, fetch_one=True,
            )
            if row:
                return row['produto_catalogo_id']

    # 3. Global (sem empresa, grupo ou ramo)
    row = execute_query(
        "SELECT produto_catalogo_id FROM nfe_produto_vinculo "
        "WHERE emit_cnpj = %s AND codigo_produto_xml = %s "
        "AND cliente_id IS NULL AND grupo_id IS NULL AND ramo_atividade_id IS NULL LIMIT 1",
        (emit_cnpj, codigo_produto), fetch=True, fetch_one=True,
    )
    if row:
        return row['produto_catalogo_id']

    return None
