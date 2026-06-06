"""Blueprint Escrita Fiscal — Conferência de Compras (NF-e)."""
import logging
import re
import threading
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify,
)
from flask_login import current_user
from utils.auth_helper import login_required, permission_required
from utils.db_helper import execute_query, execute_many
from utils.nfe_parser import parse_nfe_xml
from utils import dropbox_sync
from utils.dropbox_sync import DropboxAuthError, DropboxError
from utils import import_jobs
from config import Config

logger = logging.getLogger(__name__)

_MAX_XML_SIZE = 16_000_000  # MEDIUMTEXT max is 16 MB
_DROPBOX_AUTH_ERROR_MSG = (
    'Credenciais Dropbox inválidas ou expiradas. '
    'Verifique DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY e DROPBOX_APP_SECRET.'
)
# Máximo de arquivos por lote no endpoint SÍNCRONO (api_importar_dropbox).
# Limitado pelo timeout do worker Gunicorn (300 s). Não alterar sem revisar
# a math do _GUNICORN_WORKER_TIMEOUT, pois cada arquivo inclui 1 download +
# 2-3 queries DB + 1 move no Dropbox — tudo operações de rede.
_DROPBOX_BATCH_LIMIT = 20

# Máximo de arquivos por lote nos jobs de BACKGROUND (_run_import_job e
# importar_departamento_background). Sem restrição de timeout HTTP, então
# pode ser maior. Lotes maiores reduzem o número de round-trips para listar
# a pasta NOVO (list_xml_files), que é a principal fonte de overhead.
# 100 arquivos: 9000 arquivos ÷ 100 = 90 iterações vs 450 com batch=20.
_DROPBOX_BATCH_LIMIT_BG = 100

# Máximo de iterações de lote no job de background — guarda-chuva contra
# loop infinito caso a lógica de parada por progresso falhe.
_MAX_IMPORT_ITERATIONS = 1000

# Máximo de mensagens de erro de detalhe armazenadas por job de importação.
_MAX_ERROR_DETAILS = 50

# Workers para download e move paralelos do Dropbox.
_DOWNLOAD_WORKERS = 5

# Timeout do worker Gunicorn (segundos). Margem reservada para serialização
# e envio da resposta HTTP antes de o worker ser encerrado pelo gunicorn.
_GUNICORN_WORKER_TIMEOUT = 300
_GUNICORN_RESPONSE_MARGIN = 60

# Namespace NF-e (usado para detecção de XMLs de evento)
_NFE_NS = 'http://www.portalfiscal.inf.br/nfe'
# Tags raiz de XMLs de evento NF-e (carta de correção, cancelamento, manifestação…)
_NFE_EVENT_ROOT_TAGS = frozenset({
    'procEventoNFe', 'envEvento', 'retEnvEvento',
    'resNFe', 'retCancNFe', 'procCancNFe',
})

# Tags raiz de CT-e — não são NF-e, devem ficar em NOVO para processamento futuro.
_CTE_ROOT_TAGS = frozenset({
    'cteProc', 'procCTe', 'CTe', 'retCTe', 'CTeOS', 'cteOSProc',
})

# Códigos tpEvento por categoria
_TPEVENTO_CANCELAMENTO = frozenset({'110111', '111111', '110113', '110112'})
_TPEVENTO_CCE          = frozenset({'110110'})
_TPEVENTO_MANIFESTACAO = frozenset({'210200', '210210', '210220', '210240'})
_TPEVENTO_DESCR: dict = {
    '110111': 'Cancelamento',
    '111111': 'Cancelamento por Substituição',
    '110113': 'Cancelamento por Substituição',
    '110112': 'Encerramento',
    '110110': 'Carta de Correção (CC-e)',
    '210200': 'Confirmação da Operação',
    '210210': 'Ciência da Operação',
    '210220': 'Desconhecimento da Operação',
    '210240': 'Operação não Realizada',
}

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
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes WHERE situacao='ATIVO' ORDER BY nome_razao_social",
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


def _build_cliente_doc_cache() -> dict:
    """Indexa clientes por documento numérico para matching robusto de CNPJ/CPF."""
    rows = execute_query(
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes",
        fetch=True,
    ) or []
    cache: dict = {}
    for row in rows:
        digits = re.sub(r'\D', '', row.get('cpf_cnpj') or '')
        if len(digits) < 11:
            continue
        doc_keys = {digits}
        nozero = digits.lstrip('0')
        if nozero:
            doc_keys.add(nozero)
        item = {
            'id': row['id'],
            'numero_cliente': row.get('numero_cliente'),
            'nome_razao_social': row['nome_razao_social'],
        }
        for k in doc_keys:
            cache.setdefault(k, item)
    return cache


def _find_cliente_by_doc_digits(doc_digits: str, cache: dict) -> dict | None:
    """Busca cliente por documento usando variações normalizadas."""
    digits = re.sub(r'\D', '', doc_digits or '')
    if len(digits) < 11:
        return None
    nozero = digits.lstrip('0')
    return cache.get(digits) or (cache.get(nozero) if nozero else None)





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


def _upsert_vinculo_batch(cliente_id, grupo_id, ramo_atividade_id,
                          emit_cnpj, codigo_desc_map: dict, produto_catalogo_id):
    """Batch version of _upsert_vinculo for multiple product codes at once.

    codigo_desc_map: {codigo_produto_xml: descricao_produto_xml}

    Performs 3 queries (SELECT + UPDATE + INSERT) instead of 2×N queries,
    reducing 40-second "Aplicar a Todos" to sub-second for any NF-e size.
    """
    if not codigo_desc_map or not emit_cnpj:
        return

    codigos = list(codigo_desc_map.keys())
    ph = ','.join(['%s'] * len(codigos))

    existing_rows = execute_query(
        f"""SELECT id, codigo_produto_xml FROM nfe_produto_vinculo
             WHERE cliente_id        <=> %s
               AND grupo_id          <=> %s
               AND ramo_atividade_id <=> %s
               AND emit_cnpj         =   %s
               AND codigo_produto_xml IN ({ph})""",
        (cliente_id, grupo_id, ramo_atividade_id, emit_cnpj, *codigos),
        fetch=True,
    ) or []

    existing_map = {r['codigo_produto_xml']: r['id'] for r in existing_rows}

    # Batch UPDATE existing rows (all get the same produto_catalogo_id)
    if existing_map:
        id_list = list(existing_map.values())
        id_ph = ','.join(['%s'] * len(id_list))
        execute_query(
            f"UPDATE nfe_produto_vinculo SET produto_catalogo_id = %s WHERE id IN ({id_ph})",
            tuple([produto_catalogo_id] + id_list),
        )

    # Batch INSERT new rows
    new_codes = [cod for cod in codigos if cod not in existing_map]
    if new_codes:
        values_ph = ','.join(['(%s,%s,%s,%s,%s,%s,%s)'] * len(new_codes))
        params: list = []
        for cod in new_codes:
            params.extend([
                cliente_id, grupo_id, ramo_atividade_id,
                emit_cnpj, cod, codigo_desc_map[cod], produto_catalogo_id,
            ])
        execute_query(
            f"""INSERT INTO nfe_produto_vinculo
                   (cliente_id, grupo_id, ramo_atividade_id,
                    emit_cnpj, codigo_produto_xml,
                    descricao_produto_xml, produto_catalogo_id)
               VALUES {values_ph}""",
            tuple(params),
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


def _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=None):
    """Filtro empresa/grupo para Saídas (cliente = emitente do XML)."""
    if params is None:
        params = []
    clauses = []
    if f_cliente_id:
        cid = int(f_cliente_id)
        clauses.append(
            f"({alias}.cliente_id = %s"
            f" OR ({alias}.cliente_id IS NULL"
            f"     AND REPLACE(REPLACE(REPLACE({alias}.emit_cnpj,'.',''),'/',''),'-','')"
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
            f"     AND REPLACE(REPLACE(REPLACE({alias}.emit_cnpj,'.',''),'/',''),'-','')"
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
@permission_required('escrita_fiscal.index')
def index():
    from utils.scheduler import get_scheduled_time
    schedule = get_scheduled_time() if current_user.is_admin() else {}
    return render_template(
        'escrita_fiscal/index.html',
        is_admin=current_user.is_admin(),
        schedule_texto=schedule.get('texto', ''),
        departamento='Fiscal',
    )


# ---------------------------------------------------------------------------
# Conferência de Compras — página principal
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/')
@permission_required('escrita_fiscal.conf_compras')
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

    extra_clauses, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    where = ["n.tipo = 'entrada'"] + extra_clauses

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

    # Single query: window functions supply total count + KPI aggregates while
    # LIMIT/OFFSET pages the rows — avoids 3 separate round-trips to the DB.
    all_rows = execute_query(
        f"""SELECT n.id, n.chave_acesso, n.num_nota, n.serie, n.data_emissao,
                   n.emit_cnpj, n.emit_nome, n.emit_uf,
                   n.dest_cnpj, n.dest_nome,
                   n.valor_total, n.valor_icms, n.valor_pis, n.valor_cofins, n.valor_ipi,
                   n.cfop, n.natureza_operacao, n.origem, n.nome_arquivo,
                   n.importado_em, n.cliente_id, n.grupo_id,
                   c.nome_razao_social AS empresa_nome,
                   g.nome AS grupo_nome,
                   COALESCE(ic.qtd_itens, 0) AS qtd_itens,
                   COALESCE(ic.itens_vinculados, 0) AS itens_vinculados,
                   COUNT(*) OVER() AS _total,
                   COALESCE(SUM(n.valor_total) OVER(), 0) AS _kpi_valor,
                   COALESCE(SUM(n.valor_icms)  OVER(), 0) AS _kpi_icms,
                   COALESCE(SUM(n.valor_pis)   OVER(), 0) AS _kpi_pis,
                   COALESCE(SUM(n.valor_cofins) OVER(), 0) AS _kpi_cofins
              FROM nfe_importacoes n
              LEFT JOIN clientes c ON c.id = n.cliente_id
              LEFT JOIN grupos_clientes g ON g.id = n.grupo_id
              LEFT JOIN (
                  SELECT nfe_id,
                         COUNT(*) AS qtd_itens,
                         COUNT(produto_catalogo_id) AS itens_vinculados
                    FROM nfe_itens
                   GROUP BY nfe_id
              ) ic ON ic.nfe_id = n.id
              {where_sql}
             ORDER BY n.data_emissao DESC, n.id DESC
             LIMIT %s OFFSET %s""",
        tuple(params) + (per_page, offset),
        fetch=True,
    ) or []

    # Extract window-function values from the first row (same for all rows)
    first = all_rows[0] if all_rows else {}
    total = int(first.get('_total') or 0)
    kpi = {
        'total_valor': float(first.get('_kpi_valor') or 0),
        'total_icms':  float(first.get('_kpi_icms')  or 0),
        'total_pis':   float(first.get('_kpi_pis')   or 0),
        'total_cofins':float(first.get('_kpi_cofins') or 0),
    }

    # Handle empty result: total/kpi must still be valid (window cols absent)
    if not all_rows:
        total = 0
        kpi = {'total_valor': 0, 'total_icms': 0, 'total_pis': 0, 'total_cofins': 0}

    rows = []
    _window_cols = {'_total', '_kpi_valor', '_kpi_icms', '_kpi_pis', '_kpi_cofins'}
    for r in all_rows:
        row = {k: v for k, v in r.items() if k not in _window_cols}
        for k in ('data_emissao', 'importado_em'):
            if row.get(k) and hasattr(row[k], 'isoformat'):
                row[k] = row[k].isoformat()
        for k in ('valor_total', 'valor_icms', 'valor_pis', 'valor_cofins', 'valor_ipi'):
            row[k] = float(row.get(k) or 0)
        rows.append(row)

    return jsonify({
        'total': total, 'page': page, 'per_page': per_page, 'rows': rows,
        'kpi': kpi,
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

    # Auto-aplicar regras memorizadas nos itens ainda sem vínculo (batch)
    emit_cnpj = nota.get('emit_cnpj', '')
    cliente_id = nota.get('cliente_id')
    grupo_id = nota.get('grupo_id')
    unlinked = [it for it in itens if it.get('produto_catalogo_id') is None and it.get('codigo_produto')]
    if unlinked:
        codigos = list({it['codigo_produto'] for it in unlinked})
        mapa = _auto_vincular_batch(emit_cnpj, codigos, cliente_id, grupo_id)
        if mapa:
            # Collect unique pids to fetch names in one query
            pids = list(set(mapa.values()))
            placeholders_p = ','.join(['%s'] * len(pids))
            prod_rows = execute_query(
                f"SELECT id, nome, categoria FROM nfe_produtos_catalogo WHERE id IN ({placeholders_p})",
                tuple(pids), fetch=True,
            ) or []
            prod_map = {r['id']: r for r in prod_rows}
            # Collect updates to apply in one batch
            updates = [(mapa[it['codigo_produto']], it['id'])
                       for it in unlinked if it['codigo_produto'] in mapa]
            # Batch UPDATE: group items by product ID to minimize DB round-trips
            by_product: dict = defaultdict(list)
            for pid, item_id in updates:
                by_product[pid].append(item_id)
            for pid, ids in by_product.items():
                ph = ','.join(['%s'] * len(ids))
                execute_query(
                    f"UPDATE nfe_itens SET produto_catalogo_id = %s WHERE id IN ({ph})",
                    tuple([pid] + ids),
                )
            # Update in-memory objects
            for it in unlinked:
                pid = mapa.get(it['codigo_produto'])
                if pid:
                    prod = prod_map.get(pid)
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

    # Nome do produto vinculado — returned so the caller can update the UI
    prod_nome = None
    if produto_id:
        p = execute_query(
            "SELECT nome, categoria FROM nfe_produtos_catalogo WHERE id = %s",
            (produto_id,), fetch=True, fetch_one=True,
        )
        if p:
            prod_nome = p['nome']

    return jsonify({'ok': True, 'produto_nome': prod_nome})


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

    where, params = ["n.tipo = 'entrada'"], []
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

    where, params = ["n.tipo = 'entrada'"], []
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
# API — Resumo de produtos para o painel de totais (todos os registros do filtro)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/resumo-produtos')
@login_required
def api_resumo_produtos():
    """Retorna totais agregados por categoria → produto para todos os registros
    que correspondam ao filtro atual (sem paginação).  Inclui também os itens
    ainda sem vínculo agrupados numa categoria especial."""
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id   = request.args.get('grupo_id', '').strip()
    f_data_ini   = request.args.get('data_ini', '').strip()
    f_data_fim   = request.args.get('data_fim', '').strip()
    f_emit_cnpj  = request.args.get('emit_cnpj', '').strip()
    f_emit_uf    = request.args.get('emit_uf', '').strip()

    where, params = ["n.tipo = 'entrada'"], []
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
    if f_emit_uf:
        where.append('n.emit_uf = %s')
        params.append(f_emit_uf)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    # Agrega por produto_catalogo_id (vinculados) ou por código/descrição (sem vínculo)
    rows = execute_query(
        f"""SELECT
               p.categoria                          AS categoria,
               p.subcategoria                       AS subcategoria,
               p.id                                 AS produto_id,
               p.nome                               AS produto_nome,
               p.unidade                            AS produto_unidade,
               COALESCE(SUM(i.quantidade), 0)       AS total_qtd,
               COALESCE(SUM(i.valor_total), 0)      AS total_valor,
               COALESCE(SUM(i.valor_icms), 0)       AS total_icms,
               COUNT(DISTINCT n.id)                 AS qtd_notas
           FROM nfe_itens i
           JOIN nfe_importacoes n ON n.id = i.nfe_id
           JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
           {where_sql}
           GROUP BY p.categoria, p.subcategoria, p.id, p.nome, p.unidade
           ORDER BY p.categoria, p.subcategoria, total_valor DESC""",
        tuple(params), fetch=True,
    ) or []

    # Itens sem vínculo — agrupados pelo descrição normalizada do XML
    unlinked = execute_query(
        f"""SELECT
               i.descricao                          AS produto_nome,
               i.unidade                            AS produto_unidade,
               COALESCE(SUM(i.quantidade), 0)       AS total_qtd,
               COALESCE(SUM(i.valor_total), 0)      AS total_valor,
               COALESCE(SUM(i.valor_icms), 0)       AS total_icms,
               COUNT(DISTINCT n.id)                 AS qtd_notas
           FROM nfe_itens i
           JOIN nfe_importacoes n ON n.id = i.nfe_id
           {where_sql}
               {'AND' if where_sql else 'WHERE'} i.produto_catalogo_id IS NULL
           GROUP BY i.descricao, i.unidade
           ORDER BY total_valor DESC
           LIMIT 200""",
        tuple(params), fetch=True,
    ) or []

    # Converte decimais
    for r in rows:
        for k in ('total_qtd', 'total_valor', 'total_icms'):
            r[k] = float(r.get(k) or 0)

    for r in unlinked:
        for k in ('total_qtd', 'total_valor', 'total_icms'):
            r[k] = float(r.get(k) or 0)
        r['categoria']       = '— Sem vínculo —'
        r['subcategoria']    = None
        r['produto_id']      = None

    # Monta estrutura hierárquica: { categoria: { subcategoria: [produtos] } }
    from collections import OrderedDict
    cats = OrderedDict()

    def _add(cat, subcat, row):
        if cat not in cats:
            cats[cat] = {'total_valor': 0, 'total_qtd': 0, 'total_icms': 0, 'qtd_notas': 0, 'subcats': OrderedDict()}
        c = cats[cat]
        c['total_valor'] += row['total_valor']
        c['total_icms']  += row['total_icms']
        c['qtd_notas']   += row.get('qtd_notas', 0)
        sub_key = subcat or ''
        if sub_key not in c['subcats']:
            c['subcats'][sub_key] = {'total_valor': 0, 'total_qtd': 0, 'total_icms': 0, 'produtos': []}
        s = c['subcats'][sub_key]
        s['total_valor'] += row['total_valor']
        s['total_icms']  += row['total_icms']
        s['produtos'].append({
            'id':       row.get('produto_id'),
            'nome':     row.get('produto_nome') or '—',
            'unidade':  row.get('produto_unidade') or '',
            'total_qtd':   row['total_qtd'],
            'total_valor': row['total_valor'],
            'total_icms':  row['total_icms'],
            'qtd_notas':   row.get('qtd_notas', 0),
        })

    for r in rows:
        _add(r.get('categoria') or '— Sem categoria —', r.get('subcategoria'), r)
    for r in unlinked:
        _add(r['categoria'], r.get('subcategoria'), r)

    # Serializa preservando ordem
    result = []
    for cat_nome, cat_data in cats.items():
        subcats_list = []
        for sub_nome, sub_data in cat_data['subcats'].items():
            subcats_list.append({
                'nome':        sub_nome,
                'total_valor': round(sub_data['total_valor'], 2),
                'total_icms':  round(sub_data['total_icms'],  2),
                'produtos':    sub_data['produtos'],
            })
        result.append({
            'categoria':   cat_nome,
            'total_valor': round(cat_data['total_valor'], 2),
            'total_icms':  round(cat_data['total_icms'],  2),
            'qtd_notas':   cat_data['qtd_notas'],
            'subcats':     subcats_list,
        })

    return jsonify(result)


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

    _upload_cache = _build_cliente_doc_cache()

    for f in files:
        if not f.filename.lower().endswith('.xml'):
            err += 1
            errors.append(f'{f.filename}: não é um arquivo XML')
            continue
        try:
            content = f.read().decode('utf-8', errors='replace')
            parsed = parse_nfe_xml(content)

            dest_cli = int(cliente_id) if cliente_id else None
            grp_id = int(grupo_id) if grupo_id else None

            # Detecta cliente emitente para gerar registro de saída
            emit_digits = re.sub(r'\D', '', parsed['header'].get('emit_cnpj', ''))
            emit_cli = None
            if len(emit_digits) >= 11:
                _ef = _find_cliente_by_doc_digits(emit_digits, _upload_cache)
                if _ef and _ef['id'] != dest_cli:
                    emit_cli = _ef['id']

            if dest_cli is None and emit_cli is None:
                # Sem empresa selecionada e nenhum CNPJ reconhecido — salva sem vínculo
                result = _save_nfe(parsed, f.filename, 'UPLOAD', content,
                                   grupo_id=grp_id, tipo='entrada')
            else:
                result = _save_nfe_dual(parsed, f.filename, 'UPLOAD', content,
                                        dest_cli=dest_cli, emit_cli=emit_cli,
                                        grupo_id=grp_id)
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

    # Resposta JSON para chamadas AJAX (modal de Importação Manual)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': ok, 'dup': dup, 'err': err, 'errors': errors[:10]})

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
                               cliente_id=cliente_id, grupo_id=grupo_id, tipo='entrada')
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
    # Cursor para paginação filtrada: nome do último arquivo analisado na chamada anterior.
    # Usado quando um filtro de empresa/grupo está ativo para avançar pelo diretório NOVO
    # sem re-processar os mesmos arquivos que foram pulados (skipped) em batches anteriores.
    last_scanned = (data.get('last_scanned') or '').strip()

    if not departamento or departamento not in dropbox_sync.DEPARTAMENTOS:
        return jsonify({'error': 'Departamento inválido.'}), 400
    departamento = dropbox_sync.normalize_departamento(departamento)

    svc = dropbox_sync._service

    logger.info('Importar Dropbox: departamento=%r cliente_id=%r grupo_id=%r',
                departamento, cliente_id, grupo_id)

    # ------------------------------------------------------------------
    # Monta conjunto de CNPJs aceitos como filtro (digits only, sem pontuação).
    # None = aceitar todos.  Quando cliente_id ou grupo_id é fornecido pelo
    # front-end, apenas XMLs cujo dest_cnpj bata com esse conjunto são
    # processados; os demais são IGNORADOS (ficam na pasta NOVO intocados).
    # A empresa salva no banco é sempre determinada pelo dest_cnpj do XML —
    # NUNCA pelo cliente_id/grupo_id do filtro — para garantir fidedignidade.
    # ------------------------------------------------------------------
    filter_cnpjs: set | None = None  # set de strings de dígitos
    if cliente_id:
        c = execute_query(
            "SELECT cpf_cnpj FROM clientes WHERE id = %s",
            (int(cliente_id),), fetch=True, fetch_one=True,
        )
        if c:
            _d = re.sub(r'\D', '', c['cpf_cnpj'] or '')
            if _d:
                filter_cnpjs = {_d}
    elif grupo_id:
        members = execute_query(
            "SELECT c.cpf_cnpj FROM clientes c "
            "JOIN cliente_grupo_relacao cgr ON cgr.cliente_id = c.id "
            "WHERE cgr.grupo_id = %s",
            (int(grupo_id),), fetch=True,
        ) or []
        filter_cnpjs = {re.sub(r'\D', '', m['cpf_cnpj'] or '') for m in members} - {''}
        if not filter_cnpjs:
            filter_cnpjs = None  # grupo vazio → sem filtro

    pasta_novo = svc.pasta_novo(departamento)
    logger.info('Buscando XMLs em: %r', pasta_novo)
    try:
        files = svc.list_xml_files(pasta_novo)
    except DropboxAuthError:
        return jsonify({'error': _DROPBOX_AUTH_ERROR_MSG}), 401
    except DropboxError:
        logger.exception('Erro ao listar pasta Dropbox %r', pasta_novo)
        return jsonify({'error': 'Erro ao conectar ao Dropbox. Verifique as credenciais e a conexão.'}), 502

    if not files:
        return jsonify({
            'ok': 0, 'dup': 0, 'err': 0, 'moved_ok': 0, 'moved_err': 0,
            'msg': 'Nenhum arquivo XML encontrado na pasta NOVO.',
        }), 200

    # ------------------------------------------------------------------
    # Paginação por cursor quando filtro de empresa/grupo está ativo.
    # Arquivos pulados (skipped) permanecem em NOVO na mesma posição
    # alfabética, então sem cursor cada chamada re-processaria o mesmo
    # lote sem nunca alcançar os arquivos da empresa selecionada.
    # O cursor (last_scanned) é o nome do último arquivo analisado na
    # chamada anterior; avançamos para o arquivo imediatamente seguinte
    # na lista ordenada pelo Dropbox.
    # ------------------------------------------------------------------
    if filter_cnpjs is not None and last_scanned:
        # Cursor legado do endpoint traz apenas o nome; usa path vazio como menor
        # sufixo possível para avançar para o próximo item ordenado por (name, path).
        cursor_key = (last_scanned.lower(), '')
        _cursor_applied = False
        for _ci, _cf in enumerate(files):
            _cf_key = ((_cf.get('name') or '').lower(), _cf.get('path') or '')
            if _cf_key > cursor_key:
                files = files[_ci:]
                _cursor_applied = True
                break
        if not _cursor_applied:
            # Cursor aponta para além do último arquivo — toda a pasta foi varrida.
            files = []

    if not files and last_scanned:
        # Pasta totalmente varrida com filtro ativo.
        return jsonify({
            'ok': 0, 'dup': 0, 'err': 0, 'skipped': 0,
            'moved_ok': 0, 'moved_err': 0, 'has_more': False,
            'last_scanned': None, 'unregistered_companies': [],
            'imported_companies': [], 'details': [],
            'msg': 'Todos os arquivos da pasta NOVO foram analisados.',
        }), 200

    # Processa no máximo _DROPBOX_BATCH_LIMIT arquivos por chamada para evitar timeout do worker.
    # Se houver mais arquivos, o front-end deve chamar novamente até receber
    # has_more=False ou msg indicando que não há mais arquivos.
    has_more = len(files) > _DROPBOX_BATCH_LIMIT
    files = files[:_DROPBOX_BATCH_LIMIT]

    # Registra o nome do último arquivo deste lote para uso como cursor na próxima chamada.
    last_scanned_out = files[-1]['name'] if files else None

    now = datetime.now()
    # Cache de pastas já criadas no Dropbox para evitar chamadas redundantes.
    _pastas_criadas: set = set()
    # Cache de vínculos de produto para evitar N×M consultas DB por lote.
    # Chave: (emit_cnpj, codigo_produto, cli, grp) → produto_catalogo_id | None
    _vinculos_cache: dict = {}
    # Cache de dest_cnpj → cliente para evitar repetir a mesma lookup por arquivo.
    _cnpj_cliente_cache: dict = _build_cliente_doc_cache()

    def _get_or_create_pasta(path: str) -> str:
        if path not in _pastas_criadas:
            svc.ensure_folder(path)
            _pastas_criadas.add(path)
        return path

    ok, dup, err, moved_ok, moved_err, skipped = 0, 0, 0, 0, 0, 0
    analyzed_in_batch = 0
    details = []
    # Empresas detectadas nos XMLs que não têm cadastro no sistema.
    # Chave: CNPJ dígitos (ou nome do arquivo), Valor: nome da empresa do XML.
    unregistered: dict = {}
    # Sumário de empresas/períodos importados: (numero, nome) → set of (year, month)
    _imported_companies: dict = {}

    for info in files:
        try:
            raw = svc.download_file(info['path'])
        except DropboxAuthError:
            return jsonify({'error': _DROPBOX_AUTH_ERROR_MSG}), 401
        if raw is None:
            err += 1
            analyzed_in_batch += 1
            details.append(f"{info['name']}: falha ao baixar do Dropbox")
            # Arquivo deixado em NOVO para reprocessamento automático.
            # Não criamos pasta GLOBAL — sem o conteúdo do XML não é possível
            # identificar a empresa nem garantir a organização correta.
            continue

        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            content = raw.decode('latin-1', errors='replace')

        # Inicializa variáveis de contexto antes do try para que o bloco
        # except sempre possa referenciá-las sem risco de NameError
        # (ocorre quando parse_nfe_xml lança exceção antes de atribuí-las).
        _nome = None
        _num = None
        _cli = None
        _dt = now

        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # Classifica o XML antes de qualquer processamento.
        # ------------------------------------------------------------------
        _clf = _classify_xml(content)

        if _clf['tipo'] == 'cte':
            logger.info('%s: CT-e detectado — deixado em NOVO', info['name'])
            skipped += 1
            analyzed_in_batch += 1
            continue

        if _clf['tipo'] in ('cancelamento', 'cce', 'manifestacao', 'evento_outro'):
            # Aplica filtro de empresa/grupo se ativo.
            if filter_cnpjs is not None and (
                not _clf['dest_cnpj_digits'] or _clf['dest_cnpj_digits'] not in filter_cnpjs
            ):
                skipped += 1
                analyzed_in_batch += 1
                logger.info('%s: evento %r, CNPJ=%r não pertence ao filtro, ignorado',
                            info['name'], _clf['tipo'], _clf['dest_cnpj_digits'])
                continue

            logger.info('%s: %s → processando e movendo para IMPORTADOS',
                        info['name'], _clf['descr_evento'])
            _proc = _process_evento(_clf, info['name'], content, _cnpj_cliente_cache, now)
            ok += 1
            try:
                pasta_imp_ev = _get_or_create_pasta(
                    svc.pasta_importados(departamento, _proc['empresa_nome'],
                                         _proc['dt'], empresa_numero=_proc['empresa_num']))
                if svc.move_file(info['path'], f"{pasta_imp_ev}/{info['name']}"):
                    moved_ok += 1
                else:
                    details.append(f"{info['name']}: falha ao mover evento para IMPORTADOS")
            except DropboxAuthError:
                logger.warning('Falha de autenticação ao mover evento %s', info['name'])
            analyzed_in_batch += 1
            continue

        try:
            parsed = parse_nfe_xml(content)

            # Extrai data de emissão imediatamente após o parse para que esteja
            # disponível mesmo se uma exceção ocorrer mais adiante — o bloco
            # except usa _dt ao mover o arquivo para a pasta ERROS, e sem essa
            # atribuição antecipada ele usaria `now` (data atual) em vez da data
            # real do XML.
            _dt = parsed['header'].get('data_emissao') or now
            if _dt is now:
                logger.warning('%s: data_emissao ausente no XML, usando data atual', info['name'])

            # ----------------------------------------------------------
            # Detecta empresa SEMPRE pelo dest_cnpj do XML.
            # A seleção do modal (cliente_id / grupo_id) é usada apenas
            # como filtro — nunca para sobrescrever a empresa do XML.
            # ----------------------------------------------------------
            dest_cnpj_digits = re.sub(r'\D', '', parsed['header'].get('dest_cnpj', ''))

            # Aplica filtro ANTES de verificar cadastro: quando empresa/grupo
            # está selecionado, XMLs de outras empresas são silenciosamente
            # ignorados (ficam em NOVO). Isso evita que apareçam na lista de
            # "empresas não cadastradas" quando o usuário filtra por uma empresa.
            if filter_cnpjs is not None:
                if len(dest_cnpj_digits) < 11 or dest_cnpj_digits not in filter_cnpjs:
                    skipped += 1
                    analyzed_in_batch += 1
                    logger.info('%s: dest_cnpj=%r não pertence ao filtro, ignorado',
                                info['name'], dest_cnpj_digits)
                    continue

            if len(dest_cnpj_digits) >= 11:
                found = _find_cliente_by_doc_digits(dest_cnpj_digits, _cnpj_cliente_cache)
                if found:
                    _cli = found['id']
                    _nome = found['nome_razao_social']
                    _num = found.get('numero_cliente') or None
                    logger.info('%s: empresa detectada por dest_cnpj → %s', info['name'], _nome)

            # Detecta cliente emitente para geração de registro de saída
            _emit_cli_sync = None
            _emit_digits_sync = re.sub(r'\D', '', parsed['header'].get('emit_cnpj', ''))
            if len(_emit_digits_sync) >= 11:
                _emit_found_sync = _find_cliente_by_doc_digits(_emit_digits_sync, _cnpj_cliente_cache)
                if _emit_found_sync and _emit_found_sync['id'] != _cli:
                    _emit_cli_sync = _emit_found_sync['id']
                    if _nome is None:
                        # Só emitente encontrado: usa ele para nomear pasta
                        _nome = _emit_found_sync['nome_razao_social']
                        _num = _emit_found_sync.get('numero_cliente') or None

            if _nome is None:
                # Nenhum cliente encontrado como dest NEM emit — não importar.
                _raw_dest_cnpj = parsed['header'].get('dest_cnpj', '')
                _dest_nome_xml = (parsed['header'].get('dest_nome', '') or '').strip()
                _unreg_key = dest_cnpj_digits or _raw_dest_cnpj or info['name']
                _unreg_label = _dest_nome_xml or _raw_dest_cnpj or 'CNPJ não identificado'
                unregistered[_unreg_key] = _unreg_label
                analyzed_in_batch += 1
                logger.warning(
                    '%s: empresa não cadastrada (dest_cnpj=%r, dest_nome=%r) → deixado em NOVO',
                    info['name'], _raw_dest_cnpj, _dest_nome_xml,
                )
                continue

            # Salva entrada (se dest encontrado) e/ou saída (se emit encontrado)
            result = _save_nfe_dual(parsed, info['name'], 'DROPBOX', content,
                                    dest_cli=_cli,
                                    emit_cli=_emit_cli_sync,
                                    grupo_id=grupo_id if _cli is None else None,
                                    vinculos_cache=_vinculos_cache)
            if result == 'dup':
                dup += 1
            else:
                ok += 1
            # Registra empresa/período para o sumário do resultado.
            try:
                _period_y = _dt.year if hasattr(_dt, 'year') else now.year
                _period_m = _dt.month if hasattr(_dt, 'month') else now.month
                _co_key = (str(_num or ''), _nome)
                _period = (_period_y, _period_m)
                if _co_key not in _imported_companies:
                    _imported_companies[_co_key] = {}
                if _period not in _imported_companies[_co_key]:
                    _imported_companies[_co_key][_period] = {'ok': 0, 'dup': 0, 'err': 0}
                _imported_companies[_co_key][_period]['dup' if result == 'dup' else 'ok'] += 1
            except Exception:
                pass
            # Sucesso (incluindo duplicata) → move para IMPORTADOS
            # (pasta criada apenas neste momento, não antecipadamente)
            try:
                pasta_imp = _get_or_create_pasta(
                    svc.pasta_importados(departamento, _nome, _dt, empresa_numero=_num))
                if svc.move_file(info['path'], f"{pasta_imp}/{info['name']}"):
                    moved_ok += 1
                else:
                    details.append(f"{info['name']}: falha ao mover para IMPORTADOS no Dropbox")
            except DropboxAuthError:
                logger.warning('Falha de autenticação ao mover %s para importados', info['name'])
            analyzed_in_batch += 1
        except DropboxAuthError:
            return jsonify({'error': _DROPBOX_AUTH_ERROR_MSG}), 401
        except Exception as exc:
            err += 1
            analyzed_in_batch += 1
            if len(details) < _MAX_ERROR_DETAILS:
                details.append({
                    'arquivo': info['name'],
                    'empresa': (_nome or 'DESCONHECIDO')[:80],
                    'erro':    str(exc)[:200],
                })
            logger.exception('Erro ao processar %s', info['name'])
            # Move para ERROS sempre que ocorre uma exceção.
            # Quando a empresa foi identificada usa a pasta da empresa; caso
            # contrário usa DESCONHECIDO para não deixar o arquivo em NOVO.
            _err_empresa = _nome or 'DESCONHECIDO'
            _err_num = _num if _nome else None
            try:
                pasta_err = _get_or_create_pasta(
                    svc.pasta_erros(departamento, _err_empresa, _dt, empresa_numero=_err_num))
                if svc.move_file(info['path'], f"{pasta_err}/{info['name']}"):
                    moved_err += 1
                else:
                    details.append(f"{info['name']}: falha ao mover para ERROS no Dropbox")
            except DropboxAuthError:
                logger.warning('Falha de autenticação ao mover %s para erros', info['name'])

    total = len(files)
    msg = (f'{total} arquivo(s) analisado(s). {ok} importado(s), '
           f'{dup} duplicado(s), {err} com erro.')
    if skipped:
        msg += f' {skipped} ignorado(s) (não pertencem à empresa/grupo selecionado).'
    if unregistered:
        msg += (f' {len(unregistered)} empresa(s) não cadastrada(s) — XMLs não importados.'
                ' Cadastre as empresas listadas abaixo e importe novamente.')
    if moved_ok or moved_err:
        msg += f' {moved_ok} movido(s) para IMPORTADOS, {moved_err} movido(s) para ERROS.'

    # Segurança: se nenhum arquivo foi fisicamente movido para fora da pasta NOVO,
    # desliga has_more para evitar que o front-end entre em loop infinito
    # re-listando os mesmos arquivos (p.ex. XMLs de evento sem empresa identificada
    # que ficam em NOVO e incrementam err sem sair do lugar).
    # EXCEÇÃO: quando um filtro de empresa/grupo está ativo e houve arquivos pulados
    # (skipped), o cursor last_scanned avança para um novo bloco de arquivos na próxima
    # chamada — não há risco de loop infinito, então has_more deve permanecer True.
    files_physically_moved = moved_ok + moved_err
    # Se nenhum arquivo do lote chegou a um estado analisado, interrompe paginação
    # para evitar reprocessar o mesmo lote infinitamente.
    if has_more and analyzed_in_batch == 0:
        has_more = False
    elif has_more and files_physically_moved == 0:
        if filter_cnpjs is None or skipped == 0:
            has_more = False

    if has_more:
        msg += ' Há mais arquivos na fila — clique em Importar novamente para continuar.'
    elif unregistered and files_physically_moved == 0:
        msg += ' Cadastre as empresas e importe novamente para continuar.'

    # Converte o dict {cnpj: nome} em lista ordenada para o frontend.
    unreg_list = [{'cnpj': k, 'nome': v} for k, v in sorted(unregistered.items(), key=lambda x: x[1])]

    # Sumário de empresas importadas com totais por período.
    imported_companies_list = []
    for (num, nome), periods_data in sorted(_imported_companies.items(), key=lambda x: x[0][1] or ''):
        periodos = [
            {'periodo': f'{m:02d}/{y}', 'ok': s['ok'], 'dup': s['dup'], 'err': s['err']}
            for (y, m), s in sorted(periods_data.items())
            if s['ok'] + s['dup'] + s['err'] > 0
        ]
        if periodos:
            imported_companies_list.append({'numero': num, 'nome': nome, 'periodos': periodos})

    return jsonify({
        'ok': ok, 'dup': dup, 'err': err, 'skipped': skipped,
        'moved_ok': moved_ok, 'moved_err': moved_err,
        'has_more': has_more,
        'last_scanned': last_scanned_out,
        'unregistered_companies': unreg_list,
        'imported_companies': imported_companies_list,
        'msg': msg,
        'details': details[:10],
    })


# ---------------------------------------------------------------------------
# Importação assíncrona — background thread (não bloqueia workers gunicorn)
# ---------------------------------------------------------------------------

def _download_batch_parallel(svc, files: list) -> 'dict | None':
    """Baixa arquivos do Dropbox em paralelo com ThreadPoolExecutor.

    Retorna {path: bytes|None} para cada arquivo do lote.
    Retorna None se qualquer download levantar DropboxAuthError.
    """
    results: dict = {}

    def _fetch(file_info: dict):
        return file_info['path'], svc.download_file(file_info['path'])

    with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as executor:
        future_to_file = {executor.submit(_fetch, f): f for f in files}
        for future in as_completed(future_to_file):
            try:
                path, content = future.result()
                results[path] = content
            except DropboxAuthError:
                for pending in future_to_file:
                    pending.cancel()
                return None
            except Exception as exc:
                file_info = future_to_file[future]
                logger.warning('[import_job] Download falhou %r: %s', file_info.get('path'), exc)
                results[file_info['path']] = None
    return results


def _execute_moves_parallel(
    svc, pending_moves: list
) -> 'tuple[int, int, list]':
    """Executa moves do Dropbox em paralelo com ThreadPoolExecutor.

    pending_moves: lista de (from_path, to_path, move_type, file_name)
    move_type é 'ok' (→ IMPORTADOS) ou 'err' (→ ERROS).

    Retorna (moved_ok, moved_err, error_details).
    """
    if not pending_moves:
        return 0, 0, []

    moved_ok = moved_err = 0
    error_details: list = []

    def _move(args: tuple):
        from_path, to_path, move_type, file_name = args
        try:
            success = svc.move_file(from_path, to_path)
            return move_type, success, file_name
        except DropboxAuthError:
            logger.warning('[import_job] Auth ao mover %s', file_name)
            return move_type, False, file_name
        except Exception as exc:
            logger.warning('[import_job] Erro ao mover %s: %s', file_name, exc)
            return move_type, False, file_name

    with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as executor:
        for move_type, success, file_name in executor.map(_move, pending_moves, timeout=120):
            if success:
                if move_type == 'ok':
                    moved_ok += 1
                else:
                    moved_err += 1
            else:
                dest = 'IMPORTADOS' if move_type == 'ok' else 'ERROS'
                error_details.append(f"{file_name}: falha ao mover para {dest} no Dropbox")

    return moved_ok, moved_err, error_details


def _run_import_job(job: dict, departamento: str,
                    filter_cnpjs: 'set | None', grupo_id_val: 'int | None') -> None:
    """Executa importação Dropbox completa em background thread.

    Processa todos os lotes de ``_DROPBOX_BATCH_LIMIT_BG`` arquivos até que a
    pasta NOVO fique vazia ou sem progresso real, atualizando ``job`` com o
    progresso entre cada lote.  Suporta filtro de empresa/grupo e parada
    antecipada via ``job['stop_requested']``.

    Não requer contexto Flask — usa diretamente execute_query / dropbox_sync.
    """
    svc = dropbox_sync._service
    pasta_novo = svc.pasta_novo(departamento)

    ok = dup = err = skipped = 0
    moved_ok = moved_err = 0
    unregistered: dict = {}          # cnpj/key → nome
    _imported_companies: dict = {}   # (num, nome) → set of (year, month)
    details: list = []
    _vinculos_cache: dict = {}
    _cnpj_cliente_cache: dict = _build_cliente_doc_cache()
    _pastas_criadas: set = set()
    last_scanned_key: tuple[str, str] | None = None  # cursor (name_lower, path)

    def _get_or_create_pasta(path: str) -> str:
        if path not in _pastas_criadas:
            svc.ensure_folder(path)
            _pastas_criadas.add(path)
        return path

    def _snapshot() -> None:
        """Grava progresso atual no dict do job (lido pelo endpoint /status)."""
        unreg_list = [
            {'cnpj': k, 'nome': v}
            for k, v in sorted(unregistered.items(), key=lambda x: x[1])
        ]
        imp_list = []
        for (num, nome), periods_data in sorted(
                _imported_companies.items(), key=lambda x: x[0][1] or ''):
            periodos = [
                {'periodo': f'{m:02d}/{y}',
                 'ok': s['ok'], 'dup': s['dup'], 'err': s['err']}
                for (y, m), s in sorted(periods_data.items())
                if s['ok'] + s['dup'] + s['err'] > 0
            ]
            if periodos:
                imp_list.append({'numero': num, 'nome': nome, 'periodos': periodos})
        total = ok + dup + err + skipped
        msg = (f'{total} arquivo(s) processado(s). {ok} importado(s), '
               f'{dup} duplicado(s), {err} com erro.')
        if skipped:
            msg += f' {skipped} ignorado(s).'
        if unregistered:
            msg += f' {len(unregistered)} empresa(s) não cadastrada(s).'
        if moved_ok or moved_err:
            msg += f' {moved_ok} movido(s) para IMPORTADOS, {moved_err} movido(s) para ERROS.'
        job.update({
            'ok': ok, 'dup': dup, 'err': err, 'skipped': skipped,
            'moved_ok': moved_ok, 'moved_err': moved_err,
            'msg': msg,
            'unregistered_companies': unreg_list,
            'imported_companies': imp_list,
            'details': details[:_MAX_ERROR_DETAILS],
        })

    try:
        for _iteration in range(_MAX_IMPORT_ITERATIONS):
            if job.get('stop_requested'):
                job['status'] = 'stopped'
                _snapshot()
                job['msg'] += ' Importação interrompida pelo usuário.'
                return

            try:
                files = svc.list_xml_files(pasta_novo)
            except DropboxAuthError:
                job['status'] = 'error'
                job['msg'] = _DROPBOX_AUTH_ERROR_MSG
                return
            except DropboxError:
                logger.exception('[import_job] Erro ao listar pasta %r', pasta_novo)
                job['status'] = 'error'
                job['msg'] = 'Erro ao conectar ao Dropbox. Verifique as credenciais.'
                return

            if not files:
                break

            # Garante ordem estável entre iterações para que o cursor avance
            # monotonicamente e não volte para arquivos já analisados.
            files = sorted(files, key=lambda f: ((f.get('name') or '').lower(), f.get('path') or ''))

            # Aplica cursor para avançar além dos arquivos já analisados em lotes
            # anteriores — inclui arquivos de empresas não cadastradas (que ficam em
            # NOVO mas já foram processados nesta execução) e arquivos saltados por
            # filtro de empresa/grupo.
            if last_scanned_key:
                advanced = False
                for ci, cf in enumerate(files):
                    cf_key = ((cf.get('name') or '').lower(), cf.get('path') or '')
                    if cf_key > last_scanned_key:
                        files = files[ci:]
                        advanced = True
                        break
                if not advanced:
                    # Cursor além do último arquivo — pasta totalmente varrida.
                    break

            if not files:
                break

            batch = files[:_DROPBOX_BATCH_LIMIT_BG]
            has_more = len(files) > _DROPBOX_BATCH_LIMIT_BG
            last_scanned_this_key = (
                ((batch[-1].get('name') or '').lower(), batch[-1].get('path') or '')
                if batch else None
            )
            batch_skipped = 0
            batch_unregistered_this = 0
            batch_processed = 0
            now = datetime.now()
            # Moves acumulados durante o processamento — executados em paralelo no final.
            pending_moves: list[tuple[str, str, str, str]] = []  # (from, to, type, name)

            # ── Phase 1: downloads em paralelo ────────────────────────────
            downloaded = _download_batch_parallel(svc, batch)
            if downloaded is None:
                job['status'] = 'error'
                job['msg'] = _DROPBOX_AUTH_ERROR_MSG
                _snapshot()
                return

            # ── Phase 2: parse + DB (serial — mantém integridade transacional) ──
            for info in batch:
                if job.get('stop_requested'):
                    break

                raw = downloaded.get(info['path'])
                if raw is None:
                    err += 1
                    details.append(f"{info['name']}: falha ao baixar do Dropbox")
                    batch_processed += 1
                    continue

                try:
                    content = raw.decode('utf-8')
                except UnicodeDecodeError:
                    content = raw.decode('latin-1', errors='replace')

                _nome = None
                _num = None
                _cli = None
                _dt = now

                # Classifica o XML antes de qualquer processamento.
                _clf = _classify_xml(content)

                if _clf['tipo'] == 'cte':
                    logger.info('[import_job] %s: CT-e — deixado em NOVO', info['name'])
                    skipped += 1
                    batch_processed += 1
                    continue

                if _clf['tipo'] in ('cancelamento', 'cce', 'manifestacao', 'evento_outro'):
                    if filter_cnpjs is not None and (
                        not _clf['dest_cnpj_digits'] or _clf['dest_cnpj_digits'] not in filter_cnpjs
                    ):
                        batch_skipped += 1
                        skipped += 1
                        batch_processed += 1
                        continue

                    _proc = _process_evento(_clf, info['name'], content, _cnpj_cliente_cache, now)
                    ok += 1
                    try:
                        pasta_imp_ev = _get_or_create_pasta(
                            svc.pasta_importados(departamento, _proc['empresa_nome'],
                                                 _proc['dt'], empresa_numero=_proc['empresa_num']))
                        pending_moves.append((
                            info['path'], f"{pasta_imp_ev}/{info['name']}", 'ok', info['name']))
                    except DropboxAuthError:
                        logger.warning('[import_job] Auth ao criar pasta para evento %s', info['name'])
                    batch_processed += 1
                    continue

                try:
                    parsed = parse_nfe_xml(content)
                    _dt = parsed['header'].get('data_emissao') or now
                    dest_cnpj_digits = re.sub(r'\D', '', parsed['header'].get('dest_cnpj', ''))

                    # Aplica filtro de empresa/grupo se ativo.
                    if filter_cnpjs is not None:
                        if len(dest_cnpj_digits) < 11 or dest_cnpj_digits not in filter_cnpjs:
                            batch_skipped += 1
                            skipped += 1
                            batch_processed += 1
                            continue

                    if len(dest_cnpj_digits) >= 11:
                        found = _find_cliente_by_doc_digits(dest_cnpj_digits, _cnpj_cliente_cache)
                        if found:
                            _cli = found['id']
                            _nome = found['nome_razao_social']
                            _num = found.get('numero_cliente') or None

                    # Detecta emitente para registro de saída
                    _emit_cli_job = None
                    _emit_digs_job = re.sub(r'\D', '', parsed['header'].get('emit_cnpj', ''))
                    if len(_emit_digs_job) >= 11:
                        _ef_job = _find_cliente_by_doc_digits(_emit_digs_job, _cnpj_cliente_cache)
                        if _ef_job and _ef_job['id'] != _cli:
                            _emit_cli_job = _ef_job['id']
                            if _nome is None:
                                _nome = _ef_job['nome_razao_social']
                                _num = _ef_job.get('numero_cliente') or None

                    if _nome is None:
                        _raw_dest_cnpj = parsed['header'].get('dest_cnpj', '')
                        _dest_nome_xml = (parsed['header'].get('dest_nome', '') or '').strip()
                        _unreg_key = dest_cnpj_digits or _raw_dest_cnpj or info['name']
                        unregistered[_unreg_key] = _dest_nome_xml or _raw_dest_cnpj or 'CNPJ não identificado'
                        batch_unregistered_this += 1
                        batch_processed += 1
                        continue

                    result = _save_nfe_dual(parsed, info['name'], 'DROPBOX', content,
                                            dest_cli=_cli, emit_cli=_emit_cli_job,
                                            grupo_id=grupo_id_val if _cli is None else None,
                                            vinculos_cache=_vinculos_cache)
                    if result == 'dup':
                        dup += 1
                    else:
                        ok += 1
                    try:
                        _period_y = _dt.year if hasattr(_dt, 'year') else now.year
                        _period_m = _dt.month if hasattr(_dt, 'month') else now.month
                        _co_key = (str(_num or ''), _nome)
                        _period = (_period_y, _period_m)
                        if _co_key not in _imported_companies:
                            _imported_companies[_co_key] = {}
                        if _period not in _imported_companies[_co_key]:
                            _imported_companies[_co_key][_period] = {'ok': 0, 'dup': 0, 'err': 0}
                        _imported_companies[_co_key][_period]['dup' if result == 'dup' else 'ok'] += 1
                    except Exception:
                        pass
                    try:
                        pasta_imp = _get_or_create_pasta(
                            svc.pasta_importados(departamento, _nome, _dt, empresa_numero=_num))
                        pending_moves.append((
                            info['path'], f"{pasta_imp}/{info['name']}", 'ok', info['name']))
                    except DropboxAuthError:
                        logger.warning('[import_job] Auth ao criar pasta para %s', info['name'])
                    batch_processed += 1

                except DropboxAuthError:
                    job['status'] = 'error'
                    job['msg'] = _DROPBOX_AUTH_ERROR_MSG
                    _snapshot()
                    return
                except Exception as exc:
                    err += 1
                    if len(details) < _MAX_ERROR_DETAILS:
                        details.append({
                            'arquivo': info['name'],
                            'empresa': (_nome or 'DESCONHECIDO')[:80],
                            'erro':    str(exc)[:200],
                        })
                    if _nome:
                        try:
                            _pe_y = _dt.year if hasattr(_dt, 'year') else now.year
                            _pe_m = _dt.month if hasattr(_dt, 'month') else now.month
                            _pe_key = (str(_num or ''), _nome)
                            _pe_p = (_pe_y, _pe_m)
                            if _pe_key not in _imported_companies:
                                _imported_companies[_pe_key] = {}
                            if _pe_p not in _imported_companies[_pe_key]:
                                _imported_companies[_pe_key][_pe_p] = {'ok': 0, 'dup': 0, 'err': 0}
                            _imported_companies[_pe_key][_pe_p]['err'] += 1
                        except Exception:
                            pass
                    logger.exception('[import_job] Erro ao processar %s', info['name'])
                    _err_empresa = _nome or 'DESCONHECIDO'
                    _err_num = _num if _nome else None
                    try:
                        pasta_err = _get_or_create_pasta(
                            svc.pasta_erros(departamento, _err_empresa, _dt,
                                            empresa_numero=_err_num))
                        pending_moves.append((
                            info['path'], f"{pasta_err}/{info['name']}", 'err', info['name']))
                    except DropboxAuthError:
                        logger.warning('[import_job] Auth ao criar pasta de erros para %s', info['name'])
                    batch_processed += 1

            # ── Phase 3: moves em paralelo ────────────────────────────────
            if pending_moves:
                m_ok, m_err, m_details = _execute_moves_parallel(svc, pending_moves)
                moved_ok += m_ok
                moved_err += m_err
                details.extend(m_details)

            # Atualiza cursor com o último arquivo analisado neste lote.
            if last_scanned_this_key:
                last_scanned_key = last_scanned_this_key

            _snapshot()

            if not has_more:
                break

            if batch_processed == 0:
                break

    except Exception:
        logger.exception('[import_job] Falha inesperada no job de importação')
        job['status'] = 'error'
        job['msg'] = 'Falha inesperada durante a importação. Consulte os logs do servidor.'
        _snapshot()
        return

    if job.get('status') == 'running':
        job['status'] = 'done'
    _snapshot()
    logger.info('[import_job] Concluído: ok=%d dup=%d err=%d skipped=%d', ok, dup, err, skipped)


@escrita_fiscal.route('/conf-compras/api/importar-dropbox/start', methods=['POST'])
@login_required
def api_importar_dropbox_start():
    """Inicia importação Dropbox em background thread e retorna job_id imediatamente.

    O cliente deve chamar /status/<job_id> periodicamente para acompanhar
    o progresso, e /stop/<job_id> para interromper antes da conclusão.
    """
    if not dropbox_sync.is_configured():
        return jsonify({'error': 'Dropbox não configurado. Defina DROPBOX_APP_KEY e DROPBOX_REFRESH_TOKEN.'}), 400

    active = import_jobs.get_active_job_for_user(current_user.id)
    if active:
        return jsonify({'error': 'Você já possui uma importação em andamento.', 'job_id': active}), 429

    data = request.get_json(force=True) or {}
    departamento = data.get('departamento', '').strip()
    cliente_id = data.get('cliente_id') or None
    grupo_id = data.get('grupo_id') or None

    if not departamento or departamento not in dropbox_sync.DEPARTAMENTOS:
        return jsonify({'error': 'Departamento inválido.'}), 400
    departamento = dropbox_sync.normalize_departamento(departamento)

    # Resolve filter_cnpjs aqui, no contexto HTTP, para não precisar de app
    # context na thread de background.
    filter_cnpjs: 'set | None' = None
    grupo_id_val: 'int | None' = None
    if cliente_id:
        c = execute_query(
            "SELECT cpf_cnpj FROM clientes WHERE id = %s",
            (int(cliente_id),), fetch=True, fetch_one=True,
        )
        if c:
            _d = re.sub(r'\D', '', c['cpf_cnpj'] or '')
            if _d:
                filter_cnpjs = {_d}
    elif grupo_id:
        grupo_id_val = int(grupo_id)
        members = execute_query(
            "SELECT c.cpf_cnpj FROM clientes c "
            "JOIN cliente_grupo_relacao cgr ON cgr.cliente_id = c.id "
            "WHERE cgr.grupo_id = %s",
            (grupo_id_val,), fetch=True,
        ) or []
        filter_cnpjs = {re.sub(r'\D', '', m['cpf_cnpj'] or '') for m in members} - {''}
        if not filter_cnpjs:
            filter_cnpjs = None

    job_id, job = import_jobs.create_job(user_id=current_user.id)
    t = threading.Thread(
        target=_run_import_job,
        args=(job, departamento, filter_cnpjs, grupo_id_val),
        daemon=True,
        name=f'import-job-{job_id}',
    )
    t.start()
    logger.info('import_job %s iniciado: depto=%r filter=%r', job_id, departamento, filter_cnpjs)
    return jsonify({'job_id': job_id})


@escrita_fiscal.route('/conf-compras/api/importar-dropbox/status/<job_id>')
@login_required
def api_importar_dropbox_status(job_id: str):
    """Retorna o estado atual de um job de importação assíncrona."""
    job = import_jobs.get_job(job_id)
    if job is None:
        return jsonify({'error': 'Job não encontrado ou expirado.'}), 404
    return jsonify(job)


@escrita_fiscal.route('/conf-compras/api/importar-dropbox/stop/<job_id>', methods=['POST'])
@login_required
def api_importar_dropbox_stop(job_id: str):
    """Solicita parada antecipada de um job de importação assíncrona."""
    stopped = import_jobs.request_stop(job_id)
    return jsonify({'ok': stopped})


def importar_departamento_background(departamento: str, origem: str = 'agendado',
                                      usuario_id: int = None,
                                      deadline: float = None) -> dict:
    """Executa a importação completa de um departamento sem contexto HTTP.

    Processa todos os arquivos XML da pasta NOVO do departamento, fazendo
    múltiplas passagens (lotes de ``_DROPBOX_BATCH_LIMIT_BG``) até que não haja
    mais arquivos a processar.  Não aplica filtro de empresa/grupo.

    Retorna um dict com o sumário: ok, dup, err, moved_ok, moved_err, skipped,
    log_id (id do registro em scheduler_import_log), file_logs (lista de detalhes).
    Pensado para uso por tarefas agendadas (scheduler) e execução manual.
    """
    import json as _json
    import time as _time

    if not dropbox_sync.is_configured():
        logger.warning('importar_departamento_background: Dropbox não configurado, abortando.')
        return {'ok': 0, 'dup': 0, 'err': 0, 'moved_ok': 0, 'moved_err': 0, 'skipped': 0, 'log_id': None, 'file_logs': []}

    if departamento not in dropbox_sync.DEPARTAMENTOS:
        logger.warning('importar_departamento_background: departamento inválido %r', departamento)
        return {'ok': 0, 'dup': 0, 'err': 0, 'moved_ok': 0, 'moved_err': 0, 'skipped': 0, 'log_id': None, 'file_logs': []}
    departamento = dropbox_sync.normalize_departamento(departamento)

    # Cria registro de auditoria antes de iniciar
    iniciado_em = datetime.now(timezone.utc)
    log_id = None
    try:
        log_id = execute_query(
            "INSERT INTO scheduler_import_log (iniciado_em, departamento, origem, usuario_id) "
            "VALUES (%s, %s, %s, %s)",
            (iniciado_em, departamento, origem, usuario_id), fetch=False,
        )
    except Exception:
        logger.exception('[agendado] Falha ao criar registro de log para %r', departamento)

    file_logs: list = []  # [{arquivo, resultado, empresa, detalhe}]

    svc = dropbox_sync._service
    pasta_novo = svc.pasta_novo(departamento)
    logger.info('[agendado] Importando departamento=%r, pasta=%r', departamento, pasta_novo)

    totals = {
        'ok': 0,
        'dup': 0,
        'err': 0,
        'moved_ok': 0,
        'moved_err': 0,
        'skipped': 0,
        'error': None,
        'pasta': pasta_novo,
    }
    _vinculos_cache: dict = {}
    _cnpj_cliente_cache: dict = _build_cliente_doc_cache()
    _pastas_criadas: set = set()
    _last_seen_key: tuple[str, str] | None = None  # cursor (name_lower, path)

    def _get_or_create_pasta(path: str) -> str:
        if path not in _pastas_criadas:
            svc.ensure_folder(path)
            _pastas_criadas.add(path)
        return path

    # Loop de lotes — continua enquanto houver arquivos novos para processar.
    # O cursor _last_seen garante que arquivos de empresas não cadastradas (que
    # ficam em NOVO) não bloqueiem o processamento dos demais.
    max_iterations = 1000  # guarda-chuva contra loop infinito
    iteration = 0
    while iteration < max_iterations:
        iteration += 1
        if deadline is not None:
            if _time.monotonic() >= deadline:
                logger.warning('[agendado] departamento=%r: deadline atingido, encerrando loop.', departamento)
                break
        try:
            files = svc.list_xml_files(pasta_novo)
        except DropboxAuthError as exc:
            logger.error('[agendado] Erro de autenticação ao listar %r: %s', pasta_novo, exc)
            totals['error'] = 'Erro de autenticação no Dropbox. Verifique o token de acesso.'
            break
        except DropboxError as exc:
            logger.error('[agendado] Erro ao listar %r: %s', pasta_novo, exc)
            totals['error'] = (
                f'Não foi possível ler a pasta "{pasta_novo}". '
                'Verifique se a variável DROPBOX_ROOT_FOLDER está configurada corretamente '
                '(ex.: DROPBOX_ROOT_FOLDER=/Aplicativos/ESCRITA FISCAL). '
                'Consulte os logs do servidor para mais detalhes.'
            )
            break

        if not files:
            logger.info('[agendado] Nenhum arquivo em %r — concluído.', pasta_novo)
            break

        # Mantém ordenação determinística para o cursor _last_seen não regredir.
        files = sorted(files, key=lambda f: ((f.get('name') or '').lower(), f.get('path') or ''))

        # Avança cursor para pular arquivos já analisados em lotes anteriores.
        if _last_seen_key:
            advanced = False
            for ci, cf in enumerate(files):
                cf_key = ((cf.get('name') or '').lower(), cf.get('path') or '')
                if cf_key > _last_seen_key:
                    files = files[ci:]
                    advanced = True
                    break
            if not advanced:
                logger.info('[agendado] Cursor além do último arquivo em %r — concluído.', pasta_novo)
                break
            if not files:
                break

        batch = files[:_DROPBOX_BATCH_LIMIT_BG]
        now = datetime.now(ZoneInfo('America/Sao_Paulo'))
        batch_moved = 0
        batch_unregistered_this = 0
        batch_processed = 0

        for info in batch:
            _nome = None
            _num = None
            _cli = None
            _dt = now

            try:
                raw = svc.download_file(info['path'])
            except DropboxAuthError as exc:
                logger.error('[agendado] Falha de auth ao baixar %s: %s', info['name'], exc)
                break
            if raw is None:
                totals['err'] += 1
                logger.warning('[agendado] %s: falha ao baixar, deixado em NOVO', info['name'])
                batch_processed += 1
                continue

            try:
                content = raw.decode('utf-8')
            except UnicodeDecodeError:
                content = raw.decode('latin-1', errors='replace')

            # Classifica o XML antes de qualquer processamento.
            _clf = _classify_xml(content)

            if _clf['tipo'] == 'cte':
                logger.info('[agendado] %s: CT-e — deixado em NOVO', info['name'])
                totals['skipped'] += 1
                file_logs.append({'arquivo': info['name'], 'resultado': 'ignorado',
                                  'empresa': '', 'detalhe': 'CT-e — aguardando suporte futuro'})
                batch_processed += 1
                continue

            if _clf['tipo'] in ('cancelamento', 'cce', 'manifestacao', 'evento_outro'):
                logger.info('[agendado] %s: %s → movendo para IMPORTADOS',
                            info['name'], _clf['descr_evento'])
                _proc = _process_evento(_clf, info['name'], content, _cnpj_cliente_cache, now)
                totals['ok'] += 1
                file_logs.append({'arquivo': info['name'], 'resultado': 'importado',
                                  'empresa': _proc['empresa_nome'],
                                  'detalhe': _proc['detalhe']})
                try:
                    pasta_imp_ev = _get_or_create_pasta(
                        svc.pasta_importados(departamento, _proc['empresa_nome'],
                                             _proc['dt'], empresa_numero=_proc['empresa_num']))
                    if svc.move_file(info['path'], f"{pasta_imp_ev}/{info['name']}"):
                        totals['moved_ok'] += 1
                        batch_moved += 1
                    else:
                        file_logs.append({'arquivo': info['name'], 'resultado': 'erro',
                                          'empresa': _proc['empresa_nome'],
                                          'detalhe': 'Falha ao mover evento para IMPORTADOS'})
                except DropboxAuthError:
                    logger.warning('[agendado] Falha de auth ao mover evento %s', info['name'])
                batch_processed += 1
                continue

            try:
                parsed = parse_nfe_xml(content)
                _dt = parsed['header'].get('data_emissao') or now
                dest_cnpj_digits = re.sub(r'\D', '', parsed['header'].get('dest_cnpj', ''))

                if len(dest_cnpj_digits) >= 11:
                    found = _find_cliente_by_doc_digits(dest_cnpj_digits, _cnpj_cliente_cache)
                    if found:
                        _cli = found['id']
                        _nome = found['nome_razao_social']
                        _num = found.get('numero_cliente') or None

                # Detecta emitente para registro de saída
                _emit_cli_ag = None
                _emit_digs_ag = re.sub(r'\D', '', parsed['header'].get('emit_cnpj', ''))
                if len(_emit_digs_ag) >= 11:
                    _ef_ag = _find_cliente_by_doc_digits(_emit_digs_ag, _cnpj_cliente_cache)
                    if _ef_ag and _ef_ag['id'] != _cli:
                        _emit_cli_ag = _ef_ag['id']
                        if _nome is None:
                            _nome = _ef_ag['nome_razao_social']
                            _num = _ef_ag.get('numero_cliente') or None

                if _nome is None:
                    _raw_dest_cnpj = parsed['header'].get('dest_cnpj', '')
                    _dest_nome_xml = (parsed['header'].get('dest_nome', '') or '').strip()
                    logger.info('[agendado] %s: empresa não cadastrada (dest_cnpj=%r, nome=%r) → NOVO',
                                info['name'], _raw_dest_cnpj, _dest_nome_xml)
                    totals['skipped'] += 1
                    file_logs.append({'arquivo': info['name'], 'resultado': 'ignorado',
                                      'empresa': _dest_nome_xml or _raw_dest_cnpj,
                                      'detalhe': f'Empresa não cadastrada (CNPJ: {_raw_dest_cnpj})'})
                    batch_unregistered_this += 1
                    batch_processed += 1
                    continue

                result = _save_nfe_dual(parsed, info['name'], 'DROPBOX', content,
                                        dest_cli=_cli, emit_cli=_emit_cli_ag,
                                        vinculos_cache=_vinculos_cache)
                if result == 'dup':
                    totals['dup'] += 1
                    file_logs.append({'arquivo': info['name'], 'resultado': 'duplicata',
                                      'empresa': _nome, 'detalhe': 'NF-e já importada anteriormente'})
                else:
                    totals['ok'] += 1
                    file_logs.append({'arquivo': info['name'], 'resultado': 'importado',
                                      'empresa': _nome, 'detalhe': 'Importado com sucesso → IMPORTADOS'})

                try:
                    pasta_imp = _get_or_create_pasta(
                        svc.pasta_importados(departamento, _nome, _dt, empresa_numero=_num))
                    if svc.move_file(info['path'], f"{pasta_imp}/{info['name']}"):
                        totals['moved_ok'] += 1
                        batch_moved += 1
                    else:
                        file_logs.append({'arquivo': info['name'], 'resultado': 'erro',
                                          'empresa': _nome, 'detalhe': 'Falha ao mover para IMPORTADOS no Dropbox'})
                except DropboxAuthError:
                    logger.warning('[agendado] Falha de auth ao mover %s para importados', info['name'])
                batch_processed += 1

            except DropboxAuthError as exc:
                logger.error('[agendado] Falha de auth ao processar %s: %s', info['name'], exc)
                break
            except Exception as exc:
                totals['err'] += 1
                _detalhe_err = str(exc)[:200]
                logger.exception('[agendado] Erro ao processar %s: %s', info['name'], exc)
                _err_empresa = _nome or 'DESCONHECIDO'
                _err_num = _num if _nome else None
                file_logs.append({'arquivo': info['name'], 'resultado': 'erro',
                                  'empresa': _err_empresa, 'detalhe': _detalhe_err})
                try:
                    pasta_err = _get_or_create_pasta(
                        svc.pasta_erros(departamento, _err_empresa, _dt, empresa_numero=_err_num))
                    if svc.move_file(info['path'], f"{pasta_err}/{info['name']}"):
                        totals['moved_err'] += 1
                        batch_moved += 1
                except DropboxAuthError:
                    logger.warning('[agendado] Falha de auth ao mover %s para erros', info['name'])
                batch_processed += 1

        # Atualiza cursor com o último arquivo analisado neste lote.
        if batch:
            _last_seen_key = ((batch[-1].get('name') or '').lower(), batch[-1].get('path') or '')

        # Sem progresso neste lote → pára apenas quando não houve nenhum arquivo
        # processado (nem movidos, nem empresas não cadastradas).  O cursor acima
        # garante que os mesmos arquivos não serão re-processados em iterações futuras.
        if batch_processed == 0:
            logger.info('[agendado] Nenhum arquivo processado neste lote — encerrando loop.')
            break

    logger.info('[agendado] departamento=%r concluído: %s', departamento, totals)

    # Persiste resultado no log de auditoria
    concluido_em = datetime.now(timezone.utc)
    try:
        execute_query(
            "UPDATE scheduler_import_log SET concluido_em=%s, ok=%s, dup=%s, err=%s, "
            "moved_ok=%s, moved_err=%s, skipped=%s, detalhes=%s WHERE id=%s",
            (concluido_em, totals['ok'], totals['dup'], totals['err'],
             totals['moved_ok'], totals['moved_err'], totals['skipped'],
             _json.dumps(file_logs, default=str, ensure_ascii=False),
             log_id),
            fetch=False,
        )
    except Exception:
        logger.exception('[agendado] Falha ao atualizar log_id=%s', log_id)

    totals['log_id'] = log_id
    totals['file_logs'] = file_logs
    return totals


# ---------------------------------------------------------------------------
# Execução manual do job de importação (para testes / auditoria)
# ---------------------------------------------------------------------------
def _run_all_departments_job(job: dict, usuario_id: 'int | None', departamentos: list) -> None:
    """Executa importação dos departamentos informados sequencialmente em background thread.

    Atualiza ``job`` (dict compartilhado) com progresso em tempo real para que
    o endpoint de status possa informar o cliente via polling.
    """
    deps = departamentos
    job['total_deps'] = len(deps)
    job['completed_deps'] = 0
    job['resumo'] = {}
    job['erros'] = []

    total_ok = 0
    total_dup = 0
    total_err = 0
    total_skipped = 0

    for dep in deps:
        if job.get('stop_requested'):
            break
        job['current_dep'] = dep
        job['msg'] = f'Processando {dep} ({job["completed_deps"] + 1}/{len(deps)})...'
        try:
            result = importar_departamento_background(dep, origem='manual', usuario_id=usuario_id)
            if result.get('error'):
                job['erros'].append(result['error'])
            dep_entry = {
                'ok': result['ok'],
                'dup': result['dup'],
                'err': result['err'],
                'moved_ok': result['moved_ok'],
                'moved_err': result['moved_err'],
                'skipped': result['skipped'],
                'log_id': result.get('log_id'),
                'pasta': result.get('pasta', ''),
            }
            new_resumo = dict(job['resumo'])
            new_resumo[dep] = dep_entry
            job['resumo'] = new_resumo
            total_ok += result['ok']
            total_dup += result['dup']
            total_err += result['err']
            total_skipped += result['skipped']
        except Exception:
            logger.exception('_run_all_departments_job: erro no dep %r', dep)
            job['erros'].append(
                f'Erro ao processar departamento {dep}. Consulte os logs do servidor.'
            )
        job['completed_deps'] += 1
        job['ok'] = total_ok
        job['dup'] = total_dup
        job['err'] = total_err
        job['skipped'] = total_skipped

    total_departamentos = len(deps)
    job['msg'] = (
        f'{total_ok} importado(s), {total_dup} duplicata(s), '
        f'{total_err} erro(s), {total_skipped} ignorado(s) '
        f'em {total_departamentos} departamento(s).'
    )
    job['current_dep'] = None
    job['status'] = 'done'
    logger.info('_run_all_departments_job concluído: ok=%d dup=%d err=%d skipped=%d',
                total_ok, total_dup, total_err, total_skipped)


@escrita_fiscal.route('/conf-compras/api/executar-importacao-agendada', methods=['POST'])
@login_required
def api_executar_importacao_agendada():
    """Dispara imediatamente a importação de um ou todos os departamentos em background thread.

    Restrito a usuários administradores.  Aceita ``departamento`` no body JSON:
    - Vazio ou ``"todos"`` → importa todos os departamentos (admin only).
    - Nome de departamento válido → importa somente ele.

    Retorna imediatamente um ``job_id`` consultável via
    GET /api/executar-importacao-agendada/status/<job_id>.
    """
    usuario = current_user
    if not usuario.is_authenticated or not usuario.is_admin():
        return jsonify({'error': 'Acesso restrito a administradores.'}), 403

    if not dropbox_sync.is_configured():
        return jsonify({'error': 'Dropbox não configurado.'}), 400

    data = request.get_json(force=True) or {}
    departamento = (data.get('departamento') or '').strip()

    if not departamento or departamento.lower() == 'todos':
        departamentos = list(dropbox_sync.DEPARTAMENTOS_CANONICOS)
    else:
        if departamento not in dropbox_sync.DEPARTAMENTOS:
            return jsonify({'error': f'Departamento inválido: {departamento!r}.'}), 400
        departamentos = [dropbox_sync.normalize_departamento(departamento)]

    usuario_id = getattr(usuario, 'id', None)

    job_id, job = import_jobs.create_job()
    job['resumo'] = {}
    job['erros'] = []
    job['current_dep'] = None
    job['completed_deps'] = 0
    job['total_deps'] = len(departamentos)

    t = threading.Thread(
        target=_run_all_departments_job,
        args=(job, usuario_id, departamentos),
        daemon=True,
        name=f'import-all-{job_id}',
    )
    t.start()
    logger.info('api_executar_importacao_agendada: job %s iniciado', job_id)
    return jsonify({'job_id': job_id})


@escrita_fiscal.route('/conf-compras/api/executar-importacao-agendada/status/<job_id>')
@login_required
def api_executar_agendada_status(job_id: str):
    """Retorna o estado atual de um job de importação de todos os departamentos."""
    job = import_jobs.get_job(job_id)
    if job is None:
        return jsonify({'error': 'Job não encontrado ou expirado.'}), 404
    return jsonify(job)


@escrita_fiscal.route('/conf-compras/api/log-importacoes')
@login_required
def api_log_importacoes():
    """Retorna os últimos registros do log de importação agendada/manual.

    Parâmetros opcionais: limit (padrão 50), log_id (para buscar detalhes de uma entrada específica).
    """
    import json as _json

    _brt = ZoneInfo('America/Sao_Paulo')

    def _fmt_ts(v):
        """Converte timestamp UTC (datetime ou string) para horário de Brasília."""
        if v is None:
            return None
        if isinstance(v, datetime):
            if v.tzinfo is None:
                # naive datetime stored as UTC — convert to BRT
                v = v.replace(tzinfo=ZoneInfo('UTC')).astimezone(_brt)
            else:
                v = v.astimezone(_brt)
            return v.strftime('%Y-%m-%d %H:%M')
        # String fallback: parse as UTC and convert to BRT
        try:
            s = str(v).strip().replace('T', ' ')
            if len(s) < 19:
                return s[:16]
            dt = datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')
            dt = dt.replace(tzinfo=ZoneInfo('UTC')).astimezone(_brt)
            return dt.strftime('%Y-%m-%d %H:%M')
        except (ValueError, Exception):
            return str(v).replace('T', ' ')[:16]

    log_id = request.args.get('log_id', type=int)
    if log_id:
        row = execute_query(
            "SELECT id, iniciado_em, concluido_em, departamento, origem, usuario_id, "
            "ok, dup, err, moved_ok, moved_err, skipped, detalhes "
            "FROM scheduler_import_log WHERE id = %s",
            (log_id,), fetch=True, fetch_one=True,
        )
        if not row:
            return jsonify({'error': 'Log não encontrado'}), 404
        if row.get('detalhes') and isinstance(row['detalhes'], str):
            try:
                row['detalhes'] = _json.loads(row['detalhes'])
            except Exception:
                pass
        row['iniciado_em'] = _fmt_ts(row.get('iniciado_em'))
        row['concluido_em'] = _fmt_ts(row.get('concluido_em'))
        return jsonify({'row': row})

    limit = min(request.args.get('limit', 50, type=int), 200)
    rows = execute_query(
        "SELECT id, iniciado_em, concluido_em, departamento, origem, "
        "ok, dup, err, moved_ok, moved_err, skipped "
        "FROM scheduler_import_log "
        "ORDER BY iniciado_em DESC LIMIT %s",
        (limit,), fetch=True,
    ) or []

    for r in rows:
        r['iniciado_em'] = _fmt_ts(r.get('iniciado_em'))
        r['concluido_em'] = _fmt_ts(r.get('concluido_em'))
    return jsonify({'rows': rows})


@escrita_fiscal.route('/conf-compras/api/horario-agendado', methods=['GET'])
@login_required
def api_horario_agendado():
    """Retorna o horário atual do job de importação automática."""
    from utils.scheduler import get_scheduled_time
    return jsonify(get_scheduled_time())


@escrita_fiscal.route('/conf-compras/api/horario-agendado', methods=['POST'])
@login_required
def api_configurar_horario_agendado():
    """Atualiza o horário do job de importação automática (somente administradores).

    Body JSON: {"hora": 0-23, "minuto": 0-59}
    """
    usuario = current_user
    if not usuario.is_authenticated or not usuario.is_admin():
        return jsonify({'error': 'Acesso restrito a administradores.'}), 403

    data = request.get_json(silent=True) or {}
    try:
        hora = int(data.get('hora', -1))
        minuto = int(data.get('minuto', -1))
        if not (0 <= hora <= 23 and 0 <= minuto <= 59):
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({'error': 'Hora (0-23) e minuto (0-59) são obrigatórios e devem ser válidos.'}), 400

    from utils.scheduler import reschedule, get_scheduled_time
    horario_anterior = get_scheduled_time().get('texto', '—')
    try:
        reschedule(hora, minuto)
    except Exception:
        logger.exception('api_configurar_horario_agendado: erro ao reagendar')
        return jsonify({'error': 'Erro interno ao atualizar o horário. Tente novamente.'}), 500

    logger.info('Horário do scheduler atualizado de %s para %02d:%02d por usuário %s',
                horario_anterior, hora, minuto, usuario.id)
    return jsonify({'ok': True, 'hora': hora, 'minuto': minuto, 'texto': f'{hora:02d}:{minuto:02d}'})


@escrita_fiscal.route('/conf-compras/excluir/<int:nfe_id>', methods=['POST'])
@login_required
def excluir_nfe(nfe_id):
    execute_query("DELETE FROM nfe_importacoes WHERE id = %s", (nfe_id,))
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    flash('Nota fiscal excluída.', 'success')
    return redirect(url_for('escrita_fiscal.conf_compras'))


# ---------------------------------------------------------------------------
# API — exclusão em lote (por filtros ativos)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/excluir-lote', methods=['POST'])
@login_required
def excluir_lote():
    data = request.get_json(silent=True) or {}
    f_cliente_id = str(data.get('cliente_id', '')).strip()
    f_grupo_id   = str(data.get('grupo_id', '')).strip()
    f_emit_cnpj  = str(data.get('emit_cnpj', '')).strip()
    f_data_ini   = str(data.get('data_ini', '')).strip()
    f_data_fim   = str(data.get('data_fim', '')).strip()
    f_chave      = str(data.get('chave', '')).strip()
    f_num_nota   = str(data.get('num_nota', '')).strip()
    f_cfop       = str(data.get('cfop', '')).strip()
    f_emit_uf    = str(data.get('emit_uf', '')).strip()
    f_dest_cnpj  = str(data.get('dest_cnpj', '')).strip()
    f_vmin       = str(data.get('vmin', '')).strip()
    f_vmax       = str(data.get('vmax', '')).strip()
    f_origem     = str(data.get('origem', '')).strip()

    where, params = ["n.tipo = 'entrada'"], []
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

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    count_row = execute_query(
        f"SELECT COUNT(*) AS total FROM nfe_importacoes n {where_sql}",
        params, fetch=True, fetch_one=True,
    ) or {}
    total = int(count_row.get('total', 0))

    execute_query(
        f"DELETE n FROM nfe_importacoes n {where_sql}",
        params,
    )
    return jsonify({'ok': True, 'deleted': total})


# ---------------------------------------------------------------------------
# Catálogo de Produtos — listagem
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/')
@permission_required('escrita_fiscal.produtos_catalogo')
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

    resp = jsonify(rows)
    resp.headers['Cache-Control'] = 'private, max-age=300'
    return resp


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

    if not itens:
        prod = execute_query(
            "SELECT nome FROM nfe_produtos_catalogo WHERE id = %s",
            (produto_id,), fetch=True, fetch_one=True,
        )
        return jsonify({'ok': True, 'vinculados': 0, 'produto_nome': prod['nome'] if prod else ''})

    item_ids = [it['id'] for it in itens]

    # Batch UPDATE all items of this NF-e at once
    ph = ','.join(['%s'] * len(item_ids))
    execute_query(
        f"UPDATE nfe_itens SET produto_catalogo_id = %s WHERE id IN ({ph})",
        tuple([produto_id] + item_ids),
    )

    # Collect unique codes for rule upserts and retroactive apply
    unique_codes = {it['codigo_produto']: it.get('descricao') or ''
                    for it in itens if it.get('codigo_produto')}

    # Batch upsert rules for all unique codes: 6 queries instead of N×4
    _upsert_vinculo_batch(cli, grp, None, emit_cnpj, unique_codes, produto_id)
    _upsert_vinculo_batch(None, None, ramo_id, emit_cnpj, unique_codes, produto_id)

    # Retroactive apply: single batch UPDATE covering all historical items for all unique codes
    if emit_cnpj and unique_codes:
        item_ids_ph = ','.join(['%s'] * len(item_ids))
        cod_ph = ','.join(['%s'] * len(unique_codes))
        execute_query(
            f"""UPDATE nfe_itens i
                  JOIN nfe_importacoes n ON n.id = i.nfe_id
               SET i.produto_catalogo_id = %s
               WHERE i.produto_catalogo_id IS NULL
                 AND n.emit_cnpj = %s
                 AND i.codigo_produto IN ({cod_ph})
                 AND i.id NOT IN ({item_ids_ph})""",
            tuple([produto_id, emit_cnpj] + list(unique_codes.keys()) + item_ids),
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
@permission_required('escrita_fiscal.memorizacoes')
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
            "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes WHERE id = %s",
            (vinculo['cliente_id'],), fetch=True,
        ) or []
    elif vinculo.get('ramo_atividade_id'):
        # Regra por ramo de atividade — lista clientes do mesmo ramo que importaram desse fornecedor
        empresas = execute_query(
            """SELECT DISTINCT c.id, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj
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
            """SELECT DISTINCT c.id, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj
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


def _lookup_vinculo(codigo_produto: str, cliente_id, grupo_id,
                    prefetch_rows: list, cli_ramos: set):
    """
    In-memory vinculos lookup using pre-fetched rows.
    Priority order matches _auto_vincular_db: empresa → grupo → ramo → global.
    """
    if not codigo_produto:
        return None
    rows = [r for r in prefetch_rows if r['codigo_produto_xml'] == codigo_produto]
    # 1. Empresa específica
    if cliente_id is not None:
        for r in rows:
            if r['cliente_id'] == cliente_id and r['grupo_id'] is None:
                return r['produto_catalogo_id']
    # 2. Grupo
    if grupo_id is not None:
        for r in rows:
            if r['grupo_id'] == grupo_id and r['cliente_id'] is None:
                return r['produto_catalogo_id']
    # 3. Ramo de atividade
    if cli_ramos:
        for r in rows:
            if (r['cliente_id'] is None and r['grupo_id'] is None
                    and r.get('ramo_atividade_id') in cli_ramos):
                return r['produto_catalogo_id']
    # 4. Global
    for r in rows:
        if (r['cliente_id'] is None and r['grupo_id'] is None
                and r.get('ramo_atividade_id') is None):
            return r['produto_catalogo_id']
    return None


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------
def _save_nfe(parsed: dict, nome_arquivo: str, origem: str, xml_raw: str,
              cliente_id=None, grupo_id=None, tipo: str = 'entrada',
              vinculos_cache: dict | None = None):
    """
    Salva NF-e parseada no banco.
    tipo: 'entrada' (destinatário é nosso cliente) ou 'saida' (emitente é nosso cliente).
    Returns: 'ok' ou 'dup'
    """
    h = parsed['header']
    chave = h['chave_acesso']

    existing = execute_query(
        "SELECT id FROM nfe_importacoes WHERE chave_acesso = %s AND tipo = %s",
        (chave, tipo), fetch=True, fetch_one=True,
    )
    if existing:
        return 'dup'

    xml_raw_store = xml_raw[:_MAX_XML_SIZE] if xml_raw else ''
    cli = int(cliente_id) if cliente_id else None
    grp = int(grupo_id) if grupo_id else None
    dest_uf = h.get('dest_uf', '') or ''

    # Para entradas: auto-detecta empresa pelo dest_cnpj quando não fornecida explicitamente
    if cli is None and tipo == 'entrada':
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
               (cliente_id, grupo_id, tipo, nome_arquivo, chave_acesso, num_nota, serie,
                data_emissao, emit_cnpj, emit_nome, emit_uf,
                dest_cnpj, dest_nome, dest_uf,
                valor_total, valor_icms, valor_pis, valor_cofins, valor_ipi,
                cfop, natureza_operacao, xml_raw, origem)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            cli, grp, tipo, nome_arquivo, chave, h['num_nota'], h['serie'],
            h['data_emissao'], h['emit_cnpj'], h['emit_nome'], h['emit_uf'],
            h['dest_cnpj'], h['dest_nome'], dest_uf,
            h['valor_total'], h['valor_icms'], h['valor_pis'],
            h['valor_cofins'], h['valor_ipi'],
            h['cfop'], h['natureza_operacao'], xml_raw_store, origem,
        ),
    )

    if nfe_id is None:
        # INSERT falhou — pode ser race condition: outro processo inseriu o mesmo
        # (chave_acesso, tipo) entre o SELECT acima e este INSERT (violação UNIQUE).
        already = execute_query(
            "SELECT id FROM nfe_importacoes WHERE chave_acesso = %s AND tipo = %s",
            (chave, tipo), fetch=True, fetch_one=True,
        )
        if already:
            return 'dup'
        raise Exception(f"Falha ao salvar NF-e no banco (chave_acesso: {chave}, tipo: {tipo})")

    # Pre-fetch all vinculos for this emit_cnpj in ONE query (avoids N×4 queries
    # per item, which caused gunicorn worker timeouts on large batches).
    # Results are shared via vinculos_cache across NF-es from the same emitente.
    _emit_cnpj = h['emit_cnpj']
    _vkey = f'__vrows__{_emit_cnpj}'
    if vinculos_cache is not None and _vkey in vinculos_cache:
        _vrows = vinculos_cache[_vkey]
    else:
        _vrows = execute_query(
            "SELECT codigo_produto_xml, cliente_id, grupo_id, ramo_atividade_id, "
            "produto_catalogo_id FROM nfe_produto_vinculo WHERE emit_cnpj = %s",
            (_emit_cnpj,), fetch=True,
        ) or []
        if vinculos_cache is not None:
            vinculos_cache[_vkey] = _vrows

    # Pre-fetch ramos for this client (one query per unique client per batch).
    _rkey = f'__ramos__{cli}'
    if vinculos_cache is not None and _rkey in vinculos_cache:
        _cli_ramos = vinculos_cache[_rkey]
    else:
        _cli_ramos = set()
        if cli:
            _ramo_rows = execute_query(
                "SELECT ramo_atividade_id FROM cliente_ramo_atividade_relacao "
                "WHERE cliente_id = %s",
                (cli,), fetch=True,
            ) or []
            _cli_ramos = {r['ramo_atividade_id'] for r in _ramo_rows
                          if r.get('ramo_atividade_id')}
        if vinculos_cache is not None:
            vinculos_cache[_rkey] = _cli_ramos

    items_data = []
    for item in parsed.get('itens', []):
        # In-memory priority lookup: empresa → grupo → ramo → global.
        prod_id = _lookup_vinculo(item['codigo_produto'], cli, grp, _vrows, _cli_ramos)
        items_data.append((
            nfe_id, item['num_item'], item['codigo_produto'],
            item['descricao'], item['ncm'], item['cfop'],
            item['unidade'], item['quantidade'], item['valor_unitario'],
            item['valor_total'], item['valor_icms'],
            item['valor_pis'], item['valor_cofins'], prod_id,
        ))

    if items_data:
        execute_many(
            """INSERT INTO nfe_itens
                   (nfe_id, num_item, codigo_produto, descricao, ncm, cfop,
                    unidade, quantidade, valor_unitario, valor_total,
                    valor_icms, valor_pis, valor_cofins, produto_catalogo_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            items_data,
        )

    return 'ok'


def _save_nfe_dual(parsed: dict, nome_arquivo: str, origem: str, xml_raw: str,
                   dest_cli=None, emit_cli=None,
                   grupo_id=None, vinculos_cache=None) -> str:
    """Salva NF-e como entrada (dest_cli) e/ou saída (emit_cli) quando aplicável.

    Se dest_cli e emit_cli são None, levanta ValueError.
    Returns: 'ok' se qualquer registro foi criado; 'dup' se todos já existiam.
    """
    results = []
    if dest_cli is not None:
        results.append(_save_nfe(
            parsed, nome_arquivo, origem, xml_raw,
            cliente_id=dest_cli, grupo_id=grupo_id,
            tipo='entrada', vinculos_cache=vinculos_cache,
        ))
    if emit_cli is not None and emit_cli != dest_cli:
        results.append(_save_nfe(
            parsed, nome_arquivo, origem, xml_raw,
            cliente_id=emit_cli, tipo='saida',
            vinculos_cache=vinculos_cache,
        ))
    if not results:
        raise ValueError('Nenhum cliente (dest/emit) associado a este XML')
    return 'ok' if 'ok' in results else 'dup'


def _classify_xml(content: str) -> dict:
    """Classifica um XML fiscal e extrai metadados para tratamento inteligente.

    Retorna dict:
        tipo: 'nfe'|'nfce'|'cancelamento'|'cce'|'manifestacao'|'evento_outro'
              |'cte'|'desconhecido'
        root_tag, chave_nfe, tp_evento, descr_evento, seq_evento,
        dh_evento (datetime|None), dest_cnpj_digits
    """
    out: dict = {
        'tipo': 'desconhecido',
        'root_tag': '',
        'chave_nfe': '',
        'tp_evento': '',
        'descr_evento': '',
        'seq_evento': 1,
        'dh_evento': None,
        'dest_cnpj_digits': '',
    }
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return out

    raw_tag = root.tag
    tag = raw_tag.split('}')[-1] if '}' in raw_tag else raw_tag
    out['root_tag'] = tag

    # CT-e — deixar em NOVO
    if tag in _CTE_ROOT_TAGS:
        out['tipo'] = 'cte'
        return out

    # NF-e / NFC-e — detecta modelo (55 vs 65)
    if tag in ('nfeProc', 'NFe'):
        mod = ''
        for xpath in [f'.//{{{_NFE_NS}}}mod', './/mod']:
            el = root.find(xpath)
            if el is not None and el.text:
                mod = el.text.strip()
                break
        out['tipo'] = 'nfce' if mod == '65' else 'nfe'
        return out

    # Eventos NF-e
    if tag in _NFE_EVENT_ROOT_TAGS:
        # chNFe
        for xpath in [f'.//{{{_NFE_NS}}}chNFe', './/chNFe']:
            el = root.find(xpath)
            if el is not None and el.text:
                out['chave_nfe'] = re.sub(r'\D', '', el.text.strip())
                break

        # tpEvento
        for xpath in [f'.//{{{_NFE_NS}}}tpEvento', './/tpEvento']:
            el = root.find(xpath)
            if el is not None and el.text:
                out['tp_evento'] = el.text.strip()
                break

        # nSeqEvento
        for xpath in [f'.//{{{_NFE_NS}}}nSeqEvento', './/nSeqEvento']:
            el = root.find(xpath)
            if el is not None and el.text:
                try:
                    out['seq_evento'] = int(el.text.strip())
                except ValueError:
                    pass
                break

        # dhEvento / dhRegEvento
        for xpath in [f'.//{{{_NFE_NS}}}dhEvento', './/dhEvento',
                      f'.//{{{_NFE_NS}}}dhRegEvento', './/dhRegEvento']:
            el = root.find(xpath)
            if el is not None and el.text:
                try:
                    out['dh_evento'] = datetime.fromisoformat(
                        el.text.strip().replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass
                break

        # CNPJ para filtro de empresa/grupo
        for xpath in [f'.//{{{_NFE_NS}}}CNPJ', './/CNPJ',
                      f'.//{{{_NFE_NS}}}CNPJDest', './/CNPJDest']:
            el = root.find(xpath)
            if el is not None and el.text:
                digits = re.sub(r'\D', '', el.text.strip())
                if len(digits) >= 11:
                    out['dest_cnpj_digits'] = digits
                    break

        tp = out['tp_evento']
        out['descr_evento'] = _TPEVENTO_DESCR.get(tp, f'Evento {tp}' if tp else 'Evento desconhecido')

        if tp in _TPEVENTO_CANCELAMENTO:
            out['tipo'] = 'cancelamento'
        elif tp in _TPEVENTO_CCE:
            out['tipo'] = 'cce'
        elif tp in _TPEVENTO_MANIFESTACAO:
            out['tipo'] = 'manifestacao'
        else:
            out['tipo'] = 'evento_outro'

        return out

    return out  # desconhecido — tenta parse_nfe_xml como fallback


def _marcar_cancelada(chave_nfe: str) -> int:
    """Marca NF-e(s) com a chave como canceladas. Retorna quantas linhas foram marcadas."""
    if not chave_nfe:
        return 0
    execute_query(
        "UPDATE nfe_importacoes SET cancelada = 1 WHERE chave_acesso = %s",
        (chave_nfe,), fetch=False,
    )
    row = execute_query(
        "SELECT COUNT(*) AS cnt FROM nfe_importacoes WHERE chave_acesso = %s AND cancelada = 1",
        (chave_nfe,), fetch=True, fetch_one=True,
    ) or {}
    return int(row.get('cnt', 0))


def _salvar_evento(chave_nfe: str, tp_evento: str, descr_evento: str,
                   seq_evento: int, dh_evento, xml_raw: str,
                   nome_arquivo: str) -> None:
    """Persiste evento (CC-e ou outro relevante) em nfe_eventos, vinculando à NF-e se encontrada."""
    nfe_id = None
    if chave_nfe:
        row = execute_query(
            "SELECT id FROM nfe_importacoes WHERE chave_acesso = %s LIMIT 1",
            (chave_nfe,), fetch=True, fetch_one=True,
        )
        if row:
            nfe_id = row['id']
    execute_query(
        """INSERT INTO nfe_eventos
               (nfe_id, chave_nfe, tp_evento, descricao_evento,
                seq_evento, dh_evento, xml_raw, nome_arquivo)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (nfe_id, chave_nfe, tp_evento, descr_evento,
         seq_evento, dh_evento, (xml_raw or '')[:_MAX_XML_SIZE], nome_arquivo),
        fetch=False,
    )


def _process_evento(clf: dict, file_name: str, xml_raw: str,
                    cnpj_cache: dict, now) -> dict:
    """Executa operações de banco para um evento NF-e e determina pasta destino.

    Retorna dict: empresa_nome, empresa_num, dt, detalhe.
    """
    _ev_nome = None
    _ev_num = None
    chave = clf.get('chave_nfe', '')
    tp = clf.get('tp_evento', '')
    descr = clf.get('descr_evento', f'Evento {tp}')
    dh = clf.get('dh_evento') or now

    # Busca empresa pela chave NF-e no banco (mais confiável)
    if chave:
        ev_rec = execute_query(
            "SELECT c.nome_razao_social, c.numero_cliente "
            "FROM nfe_importacoes n "
            "JOIN clientes c ON c.id = n.cliente_id "
            "WHERE n.chave_acesso = %s LIMIT 1",
            (chave,), fetch=True, fetch_one=True,
        )
        if ev_rec:
            _ev_nome = ev_rec['nome_razao_social']
            _ev_num = ev_rec.get('numero_cliente') or None

    # Fallback: busca empresa pelo CNPJ extraído do evento
    if not _ev_nome:
        cnpj_dig = clf.get('dest_cnpj_digits', '')
        if cnpj_dig and len(cnpj_dig) >= 11:
            ev_found = _find_cliente_by_doc_digits(cnpj_dig, cnpj_cache)
            if ev_found:
                _ev_nome = ev_found['nome_razao_social']
                _ev_num = ev_found.get('numero_cliente') or None

    # Empresa não identificada → pasta genérica dentro de IMPORTADOS
    if not _ev_nome:
        _ev_nome = 'EVENTOS'
        _ev_num = None

    # Operação de banco conforme tipo
    tipo = clf.get('tipo', 'evento_outro')
    if tipo == 'cancelamento' and chave:
        cnt = _marcar_cancelada(chave)
        detalhe = f'{descr} — {"NF-e cancelada" if cnt else "NF-e não encontrada no sistema"}'
    elif tipo == 'cce' and chave:
        _salvar_evento(chave, tp, descr, clf.get('seq_evento', 1), dh, xml_raw, file_name)
        detalhe = f'{descr} — registrada no sistema'
    else:
        detalhe = descr

    return {
        'empresa_nome': _ev_nome,
        'empresa_num':  _ev_num,
        'dt':           dh,
        'detalhe':      detalhe,
    }


def _get_ramo_cliente(cliente_id):
    """Retorna o primeiro ramo_atividade_id do cliente, ou None."""
    if not cliente_id:
        return None
    row = execute_query(
        "SELECT ramo_atividade_id FROM cliente_ramo_atividade_relacao WHERE cliente_id = %s LIMIT 1",
        (cliente_id,), fetch=True, fetch_one=True,
    )
    return row['ramo_atividade_id'] if row else None


def _auto_vincular(emit_cnpj: str, codigo_produto: str, cliente_id, grupo_id,
                   cache: dict | None = None):
    """
    Tenta encontrar um vínculo automático registrado para o par emit_cnpj + codigo_produto.
    Busca na ordem: empresa específica → grupo → ramo de atividade → global.
    O parâmetro `cache` (dict mutável) permite reutilizar resultados dentro de um lote
    de importação, eliminando consultas DB repetidas para o mesmo par.
    """
    if not emit_cnpj or not codigo_produto:
        return None

    cache_key = (emit_cnpj, codigo_produto, cliente_id, grupo_id)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    result = _auto_vincular_db(emit_cnpj, codigo_produto, cliente_id, grupo_id)

    if cache is not None:
        cache[cache_key] = result
    return result


def _auto_vincular_db(emit_cnpj: str, codigo_produto: str, cliente_id, grupo_id):
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


def _auto_vincular_batch(emit_cnpj: str, codigos: list, cliente_id, grupo_id) -> dict:
    """
    Versão batch de _auto_vincular_db: recebe uma lista de codigos_produto e
    retorna um dict {codigo_produto: produto_catalogo_id} em 2 queries ao invés
    de disparar até 5 queries sequenciais.
    Respeita a mesma prioridade: empresa(1) → grupo(2) → ramo(3) → global(4).
    """
    if not emit_cnpj or not codigos:
        return {}

    # Query 1 (only when needed): fetch ramo_ids for this client
    ramo_ids: list = []
    if cliente_id:
        ramos = execute_query(
            "SELECT ramo_atividade_id FROM cliente_ramo_atividade_relacao WHERE cliente_id = %s",
            (cliente_id,), fetch=True,
        ) or []
        ramo_ids = [r['ramo_atividade_id'] for r in ramos if r.get('ramo_atividade_id')]

    # Query 2: single combined lookup with all 4 scopes as OR, priority via CASE
    ph_c = ','.join(['%s'] * len(codigos))
    params: list = [emit_cnpj] + list(codigos)

    scope_or_parts = []
    case_parts = []

    if cliente_id:
        scope_or_parts.append("(cliente_id = %s AND grupo_id IS NULL AND ramo_atividade_id IS NULL)")
        case_parts.append("WHEN cliente_id = %s AND grupo_id IS NULL AND ramo_atividade_id IS NULL THEN 1")
        params += [cliente_id, cliente_id]

    if grupo_id:
        scope_or_parts.append("(grupo_id = %s AND cliente_id IS NULL AND ramo_atividade_id IS NULL)")
        case_parts.append("WHEN grupo_id = %s AND cliente_id IS NULL AND ramo_atividade_id IS NULL THEN 2")
        params += [grupo_id, grupo_id]

    if ramo_ids:
        ph_r = ','.join(['%s'] * len(ramo_ids))
        scope_or_parts.append(
            f"(ramo_atividade_id IN ({ph_r}) AND cliente_id IS NULL AND grupo_id IS NULL)"
        )
        case_parts.append(
            f"WHEN ramo_atividade_id IN ({ph_r}) AND cliente_id IS NULL AND grupo_id IS NULL THEN 3"
        )
        params += ramo_ids + ramo_ids  # once for OR, once for CASE

    # Always include global scope
    scope_or_parts.append("(cliente_id IS NULL AND grupo_id IS NULL AND ramo_atividade_id IS NULL)")

    case_sql = "CASE " + " ".join(case_parts) + " ELSE 4 END" if case_parts else "4"
    scope_sql = " OR ".join(scope_or_parts)

    rows = execute_query(
        f"SELECT codigo_produto_xml, produto_catalogo_id, {case_sql} AS priority "
        f"FROM nfe_produto_vinculo "
        f"WHERE emit_cnpj = %s AND codigo_produto_xml IN ({ph_c}) "
        f"AND produto_catalogo_id IS NOT NULL "
        f"AND ({scope_sql}) "
        f"ORDER BY priority",
        tuple(params),
        fetch=True,
    ) or []

    # Keep highest-priority (lowest number) match per codigo
    result: dict = {}
    for r in rows:
        cod = r['codigo_produto_xml']
        if cod not in result:
            result[cod] = r['produto_catalogo_id']


# ===========================================================================
# Conferência de Saídas
# ===========================================================================

@escrita_fiscal.route('/conf-saidas/')
@permission_required('escrita_fiscal.conf_saidas')
def conf_saidas():
    empresas = _get_empresas()
    grupos = _get_grupos()
    destinatarios = execute_query(
        "SELECT DISTINCT dest_cnpj, dest_nome FROM nfe_importacoes "
        "WHERE tipo='saida' ORDER BY dest_nome",
        fetch=True,
    ) or []
    dropbox_ok = dropbox_sync.is_configured()
    stats = {'total_notas': 0, 'total_valor': 0, 'total_icms': 0,
             'total_pis': 0, 'total_cofins': 0}
    return render_template(
        'escrita_fiscal/conf_saidas.html',
        stats=stats,
        destinatarios=destinatarios,
        empresas=empresas,
        grupos=grupos,
        dropbox_configured=dropbox_ok,
        uf_list=_UF_LIST,
        dropbox_folder=Config.DROPBOX_XML_FOLDER,
    )


@escrita_fiscal.route('/conf-saidas/api/notas')
@login_required
def api_notas_saidas():
    f_cliente_id  = request.args.get('cliente_id', '').strip()
    f_grupo_id    = request.args.get('grupo_id', '').strip()
    f_dest_cnpj   = request.args.get('dest_cnpj', '').strip()
    f_data_ini    = request.args.get('data_ini', '').strip()
    f_data_fim    = request.args.get('data_fim', '').strip()
    f_chave       = request.args.get('chave', '').strip()
    f_num_nota    = request.args.get('num_nota', '').strip()
    f_cfop        = request.args.get('cfop', '').strip()
    f_dest_uf     = request.args.get('dest_uf', '').strip()
    f_emit_cnpj   = request.args.get('emit_cnpj', '').strip()
    f_vmin        = request.args.get('vmin', '').strip()
    f_vmax        = request.args.get('vmax', '').strip()
    f_origem      = request.args.get('origem', '').strip()
    f_vinc_status = request.args.get('vinc_status', '').strip()
    page          = max(1, int(request.args.get('page', 1)))
    per_page      = 50

    extra_clauses, params = _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=[])
    where = ["n.tipo = 'saida'"] + extra_clauses

    if f_dest_cnpj:
        where.append('n.dest_cnpj LIKE %s')
        params.append(f'%{f_dest_cnpj}%')
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
    if f_dest_uf:
        where.append('n.dest_uf = %s')
        params.append(f_dest_uf)
    if f_emit_cnpj:
        where.append('n.emit_cnpj LIKE %s')
        params.append(f'%{f_emit_cnpj}%')
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

    all_rows = execute_query(
        f"""SELECT n.id, n.chave_acesso, n.num_nota, n.serie, n.data_emissao,
                   n.emit_cnpj, n.emit_nome, n.emit_uf,
                   n.dest_cnpj, n.dest_nome, n.dest_uf,
                   n.valor_total, n.valor_icms, n.valor_pis, n.valor_cofins, n.valor_ipi,
                   n.cfop, n.natureza_operacao, n.origem, n.nome_arquivo,
                   n.importado_em, n.cliente_id, n.grupo_id,
                   c.nome_razao_social AS empresa_nome,
                   g.nome AS grupo_nome,
                   COALESCE(ic.qtd_itens, 0) AS qtd_itens,
                   COALESCE(ic.itens_vinculados, 0) AS itens_vinculados,
                   COUNT(*) OVER() AS _total,
                   COALESCE(SUM(n.valor_total) OVER(), 0) AS _kpi_valor,
                   COALESCE(SUM(n.valor_icms)  OVER(), 0) AS _kpi_icms,
                   COALESCE(SUM(n.valor_pis)   OVER(), 0) AS _kpi_pis,
                   COALESCE(SUM(n.valor_cofins) OVER(), 0) AS _kpi_cofins
              FROM nfe_importacoes n
              LEFT JOIN clientes c ON c.id = n.cliente_id
              LEFT JOIN grupos_clientes g ON g.id = n.grupo_id
              LEFT JOIN (
                  SELECT nfe_id,
                         COUNT(*) AS qtd_itens,
                         COUNT(produto_catalogo_id) AS itens_vinculados
                    FROM nfe_itens
                   GROUP BY nfe_id
              ) ic ON ic.nfe_id = n.id
              {where_sql}
             ORDER BY n.data_emissao DESC, n.id DESC
             LIMIT %s OFFSET %s""",
        tuple(params) + (per_page, offset),
        fetch=True,
    ) or []

    first = all_rows[0] if all_rows else {}
    total = int(first.get('_total') or 0)
    kpi = {
        'total_valor':  float(first.get('_kpi_valor') or 0),
        'total_icms':   float(first.get('_kpi_icms')  or 0),
        'total_pis':    float(first.get('_kpi_pis')   or 0),
        'total_cofins': float(first.get('_kpi_cofins') or 0),
    }
    if not all_rows:
        total = 0
        kpi = {'total_valor': 0, 'total_icms': 0, 'total_pis': 0, 'total_cofins': 0}

    _window_cols = {'_total', '_kpi_valor', '_kpi_icms', '_kpi_pis', '_kpi_cofins'}
    rows = []
    for r in all_rows:
        row = {k: v for k, v in r.items() if k not in _window_cols}
        for k in ('data_emissao', 'importado_em'):
            if row.get(k) and hasattr(row[k], 'isoformat'):
                row[k] = row[k].isoformat()
        for k in ('valor_total', 'valor_icms', 'valor_pis', 'valor_cofins', 'valor_ipi'):
            row[k] = float(row.get(k) or 0)
        rows.append(row)

    return jsonify({'total': total, 'page': page, 'per_page': per_page, 'rows': rows, 'kpi': kpi})


@escrita_fiscal.route('/conf-saidas/api/por-destinatario')
@login_required
def api_por_destinatario():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id   = request.args.get('grupo_id', '').strip()
    f_data_ini   = request.args.get('data_ini', '').strip()
    f_data_fim   = request.args.get('data_fim', '').strip()

    where = ["n.tipo = 'saida'"]
    extra, params = _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = execute_query(
        f"""SELECT n.dest_cnpj, n.dest_nome, n.dest_uf,
                   COUNT(*) AS qtd_notas,
                   SUM(n.valor_total)  AS total_valor,
                   SUM(n.valor_icms)   AS total_icms,
                   SUM(n.valor_pis)    AS total_pis,
                   SUM(n.valor_cofins) AS total_cofins
              FROM nfe_importacoes n {where_sql}
             GROUP BY n.dest_cnpj, n.dest_nome, n.dest_uf
             ORDER BY total_valor DESC""",
        tuple(params), fetch=True,
    ) or []

    for r in rows:
        for k in ('total_valor', 'total_icms', 'total_pis', 'total_cofins'):
            r[k] = float(r.get(k) or 0)

    return jsonify(rows)


@escrita_fiscal.route('/conf-saidas/api/por-produto')
@login_required
def api_por_produto_saidas():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id   = request.args.get('grupo_id', '').strip()
    f_data_ini   = request.args.get('data_ini', '').strip()
    f_data_fim   = request.args.get('data_fim', '').strip()
    f_dest_cnpj  = request.args.get('dest_cnpj', '').strip()
    f_ncm        = request.args.get('ncm', '').strip()
    f_descricao  = request.args.get('descricao', '').strip()

    where = ["n.tipo = 'saida'"]
    extra, params = _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    if f_dest_cnpj:
        where.append('n.dest_cnpj LIKE %s')
        params.append(f'%{f_dest_cnpj}%')
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
                   SUM(i.quantidade)   AS total_qtd,
                   SUM(i.valor_total)  AS total_valor,
                   SUM(i.valor_icms)   AS total_icms,
                   SUM(i.valor_pis)    AS total_pis,
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


@escrita_fiscal.route('/conf-saidas/excluir-lote', methods=['POST'])
@login_required
def excluir_lote_saidas():
    data = request.get_json(silent=True) or {}
    f_cliente_id = str(data.get('cliente_id', '')).strip()
    f_grupo_id   = str(data.get('grupo_id', '')).strip()
    f_dest_cnpj  = str(data.get('dest_cnpj', '')).strip()
    f_data_ini   = str(data.get('data_ini', '')).strip()
    f_data_fim   = str(data.get('data_fim', '')).strip()
    f_chave      = str(data.get('chave', '')).strip()
    f_num_nota   = str(data.get('num_nota', '')).strip()
    f_cfop       = str(data.get('cfop', '')).strip()
    f_dest_uf    = str(data.get('dest_uf', '')).strip()
    f_emit_cnpj  = str(data.get('emit_cnpj', '')).strip()
    f_vmin       = str(data.get('vmin', '')).strip()
    f_vmax       = str(data.get('vmax', '')).strip()
    f_origem     = str(data.get('origem', '')).strip()

    where = ["n.tipo = 'saida'"]
    extra_clauses, params = _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra_clauses)

    if f_dest_cnpj:
        where.append('n.dest_cnpj LIKE %s')
        params.append(f'%{f_dest_cnpj}%')
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
    if f_dest_uf:
        where.append('n.dest_uf = %s')
        params.append(f_dest_uf)
    if f_emit_cnpj:
        where.append('n.emit_cnpj LIKE %s')
        params.append(f'%{f_emit_cnpj}%')
    if f_vmin:
        where.append('n.valor_total >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('n.valor_total <= %s')
        params.append(float(f_vmax))
    if f_origem:
        where.append('n.origem = %s')
        params.append(f_origem)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    count_row = execute_query(
        f"SELECT COUNT(*) AS total FROM nfe_importacoes n {where_sql}",
        params, fetch=True, fetch_one=True,
    ) or {}
    total = int(count_row.get('total', 0))
    execute_query(f"DELETE n FROM nfe_importacoes n {where_sql}", params)
    return jsonify({'ok': True, 'deleted': total})

    return result
