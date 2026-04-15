"""Blueprint Escrita Fiscal — Conferência de Compras (NF-e)."""
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, current_app,
)
from utils.auth_helper import login_required
from utils.db_helper import execute_query
from utils.nfe_parser import parse_nfe_xml
from utils import dropbox_sync
from config import Config

_MAX_XML_SIZE = 16_000_000  # MEDIUMTEXT max is 16 MB

_UF_LIST = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT',
            'PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']

escrita_fiscal = Blueprint('escrita_fiscal', __name__, url_prefix='/escrita-fiscal')
# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/')
@login_required
def index():
    return render_template('escrita_fiscal/index.html')


# ---------------------------------------------------------------------------
# Conferência de Compras — lista principal
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/')
@login_required
def conf_compras():
    # Totais para o cabeçalho
    stats = execute_query(
        """SELECT COUNT(*) AS total_notas,
                  COALESCE(SUM(valor_total), 0) AS total_valor,
                  COALESCE(SUM(valor_icms), 0) AS total_icms,
                  COALESCE(SUM(valor_pis), 0) AS total_pis,
                  COALESCE(SUM(valor_cofins), 0) AS total_cofins
             FROM nfe_importacoes""",
        fetch=True, fetch_one=True,
    ) or {}

    # Emitentes distintos para o filtro
    emitentes = execute_query(
        "SELECT DISTINCT emit_cnpj, emit_nome FROM nfe_importacoes ORDER BY emit_nome",
        fetch=True,
    ) or []

    dropbox_ok = dropbox_sync.is_configured()

    return render_template(
        'escrita_fiscal/conf_compras.html',
        stats=stats,
        emitentes=emitentes,
        dropbox_configured=dropbox_ok,
        uf_list=_UF_LIST,
        dropbox_folder=Config.DROPBOX_XML_FOLDER,
    )


# ---------------------------------------------------------------------------
# API JSON — retorna notas com filtros
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/notas')
@login_required
def api_notas():
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
    page = max(1, int(request.args.get('page', 1)))
    per_page = 50

    where, params = [], []

    if f_emit_cnpj:
        where.append('emit_cnpj = %s')
        params.append(f_emit_cnpj)
    if f_data_ini:
        where.append('data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('data_emissao <= %s')
        params.append(f_data_fim)
    if f_chave:
        where.append('chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_num_nota:
        where.append('num_nota = %s')
        params.append(f_num_nota)
    if f_cfop:
        where.append('cfop LIKE %s')
        params.append(f'{f_cfop}%')
    if f_emit_uf:
        where.append('emit_uf = %s')
        params.append(f_emit_uf)
    if f_dest_cnpj:
        where.append('dest_cnpj LIKE %s')
        params.append(f'%{f_dest_cnpj}%')
    if f_vmin:
        where.append('valor_total >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('valor_total <= %s')
        params.append(float(f_vmax))
    if f_origem:
        where.append('origem = %s')
        params.append(f_origem)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    offset = (page - 1) * per_page

    count_row = execute_query(
        f"SELECT COUNT(*) AS c FROM nfe_importacoes {where_sql}",
        tuple(params), fetch=True, fetch_one=True,
    ) or {}
    total = count_row.get('c', 0)

    rows = execute_query(
        f"""SELECT id, chave_acesso, num_nota, serie, data_emissao,
                   emit_cnpj, emit_nome, emit_uf,
                   dest_cnpj, dest_nome,
                   valor_total, valor_icms, valor_pis, valor_cofins, valor_ipi,
                   cfop, natureza_operacao, origem, nome_arquivo,
                   importado_em
              FROM nfe_importacoes {where_sql}
             ORDER BY data_emissao DESC, id DESC
             LIMIT %s OFFSET %s""",
        tuple(params) + (per_page, offset),
        fetch=True,
    ) or []

    # Serialise dates
    for r in rows:
        for k in ('data_emissao', 'importado_em'):
            if r.get(k) and hasattr(r[k], 'isoformat'):
                r[k] = r[k].isoformat()

    return jsonify({'total': total, 'page': page, 'per_page': per_page, 'rows': rows})


# ---------------------------------------------------------------------------
# API JSON — notas agrupadas por emissor
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/por-emissor')
@login_required
def api_por_emissor():
    f_data_ini = request.args.get('data_ini', '').strip()
    f_data_fim = request.args.get('data_fim', '').strip()

    where, params = [], []
    if f_data_ini:
        where.append('data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('data_emissao <= %s')
        params.append(f_data_fim)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = execute_query(
        f"""SELECT emit_cnpj, emit_nome, emit_uf,
                   COUNT(*) AS qtd_notas,
                   SUM(valor_total) AS total_valor,
                   SUM(valor_icms) AS total_icms,
                   SUM(valor_pis) AS total_pis,
                   SUM(valor_cofins) AS total_cofins
              FROM nfe_importacoes {where_sql}
             GROUP BY emit_cnpj, emit_nome, emit_uf
             ORDER BY total_valor DESC""",
        tuple(params), fetch=True,
    ) or []

    for r in rows:
        for k in ('total_valor', 'total_icms', 'total_pis', 'total_cofins'):
            r[k] = float(r.get(k) or 0)

    return jsonify(rows)


# ---------------------------------------------------------------------------
# API JSON — itens agrupados por produto
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/por-produto')
@login_required
def api_por_produto():
    f_data_ini = request.args.get('data_ini', '').strip()
    f_data_fim = request.args.get('data_fim', '').strip()
    f_emit_cnpj = request.args.get('emit_cnpj', '').strip()
    f_ncm = request.args.get('ncm', '').strip()
    f_descricao = request.args.get('descricao', '').strip()

    where, params = [], []
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
                   COUNT(DISTINCT n.id) AS qtd_notas,
                   SUM(i.quantidade) AS total_qtd,
                   SUM(i.valor_total) AS total_valor,
                   SUM(i.valor_icms) AS total_icms,
                   SUM(i.valor_pis) AS total_pis,
                   SUM(i.valor_cofins) AS total_cofins
              FROM nfe_itens i
              JOIN nfe_importacoes n ON n.id = i.nfe_id
              {where_sql}
             GROUP BY i.codigo_produto, i.descricao, i.ncm, i.cfop, i.unidade
             ORDER BY total_valor DESC
             LIMIT 500""",
        tuple(params), fetch=True,
    ) or []

    for r in rows:
        for k in ('total_qtd', 'total_valor', 'total_icms', 'total_pis', 'total_cofins'):
            r[k] = float(r.get(k) or 0)

    return jsonify(rows)


# ---------------------------------------------------------------------------
# Import — upload de arquivos XML
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/importar', methods=['POST'])
@login_required
def importar_xml():
    files = request.files.getlist('xml_files')
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
            result = _save_nfe(parsed, f.filename, 'UPLOAD', content)
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

    if errors:
        for e in errors[:5]:
            flash(e, 'danger')

    return redirect(url_for('escrita_fiscal.conf_compras'))


# ---------------------------------------------------------------------------
# Import — sincronização com Dropbox
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/sync-dropbox', methods=['POST'])
@login_required
def sync_dropbox():
    if not dropbox_sync.is_configured():
        return jsonify({'error': 'Dropbox não configurado. Defina DROPBOX_ACCESS_TOKEN no ambiente.'}), 400

    files = dropbox_sync.list_xml_files()
    if not files:
        return jsonify({'ok': 0, 'dup': 0, 'err': 0, 'msg': 'Nenhum arquivo XML encontrado na pasta Dropbox.'}), 200

    ok, dup, err = 0, 0, 0
    for info in files:
        content = dropbox_sync.download_xml(info['path'])
        if content is None:
            err += 1
            continue
        try:
            parsed = parse_nfe_xml(content)
            result = _save_nfe(parsed, info['name'], 'DROPBOX', content)
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
# Helpers
# ---------------------------------------------------------------------------
def _save_nfe(parsed: dict, nome_arquivo: str, origem: str, xml_raw: str):
    """
    Salva uma NF-e parseada no banco.
    Returns: 'ok' ou 'dup' (chave já existe)
    """
    h = parsed['header']
    chave = h['chave_acesso']

    # Verifica duplicata
    existing = execute_query(
        "SELECT id FROM nfe_importacoes WHERE chave_acesso = %s",
        (chave,), fetch=True, fetch_one=True,
    )
    if existing:
        return 'dup'

    # Trunca xml_raw se necessário (MEDIUMTEXT suporta 16 MB)
    xml_raw_store = xml_raw[:_MAX_XML_SIZE] if xml_raw else ''

    nfe_id = execute_query(
        """INSERT INTO nfe_importacoes
               (nome_arquivo, chave_acesso, num_nota, serie,
                data_emissao, emit_cnpj, emit_nome, emit_uf,
                dest_cnpj, dest_nome,
                valor_total, valor_icms, valor_pis, valor_cofins, valor_ipi,
                cfop, natureza_operacao, xml_raw, origem)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            nome_arquivo, chave, h['num_nota'], h['serie'],
            h['data_emissao'], h['emit_cnpj'], h['emit_nome'], h['emit_uf'],
            h['dest_cnpj'], h['dest_nome'],
            h['valor_total'], h['valor_icms'], h['valor_pis'],
            h['valor_cofins'], h['valor_ipi'],
            h['cfop'], h['natureza_operacao'], xml_raw_store, origem,
        ),
    )

    for item in parsed.get('itens', []):
        execute_query(
            """INSERT INTO nfe_itens
                   (nfe_id, num_item, codigo_produto, descricao, ncm, cfop,
                    unidade, quantidade, valor_unitario, valor_total,
                    valor_icms, valor_pis, valor_cofins)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                nfe_id, item['num_item'], item['codigo_produto'],
                item['descricao'], item['ncm'], item['cfop'],
                item['unidade'], item['quantidade'], item['valor_unitario'],
                item['valor_total'], item['valor_icms'],
                item['valor_pis'], item['valor_cofins'],
            ),
        )

    return 'ok'
