"""Aba CONTABILIDADES em Cadastros (14/10/2026).

Escritórios de onde os clientes vieram. Toda empresa CLIENTE PJ tem "aberta
pela Qualicontax" OU a contabilidade anterior; o avulso só precisa ao virar
cliente. Usa as permissões de clientes (ver é clientes.index, mexer é
clientes.edit) — é um pedaço do cadastro de clientes, não um módulo à parte.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from utils.auth_helper import permission_required, login_required
from utils.atividade import registrar
from utils.db_helper import execute_query
from models.contabilidade import Contabilidade, limpar

contabilidades = Blueprint('contabilidades', __name__)


def _kpis():
    """Clientes PJ ativos da carteira (sem avulso) pela origem."""
    r = execute_query(
        """SELECT COUNT(*) AS total,
                  SUM(COALESCE(aberta_pela_casa, 0) = 1) AS casa,
                  SUM(COALESCE(aberta_pela_casa, 0) = 0 AND contabilidade_anterior_id IS NOT NULL) AS anterior,
                  SUM(COALESCE(aberta_pela_casa, 0) = 0 AND contabilidade_anterior_id IS NULL) AS sem
             FROM clientes
            WHERE tipo_pessoa = 'PJ' AND COALESCE(avulso, 0) = 0 AND situacao = 'ATIVO'""",
        fetch=True, fetch_one=True) or {}
    return {k: int(r.get(k) or 0) for k in ('total', 'casa', 'anterior', 'sem')}


@contabilidades.route('/contabilidades')
@permission_required('clientes.index')
def index():
    lista = Contabilidade.listar(situacao=None)
    # empresas de cada escritório (nº e nome) numa ida só
    por = {}
    for e in execute_query(
            "SELECT contabilidade_anterior_id AS cid, id, numero_cliente, nome_razao_social, COALESCE(avulso, 0) AS avulso "
            "FROM clientes WHERE contabilidade_anterior_id IS NOT NULL ORDER BY nome_razao_social",
            fetch=True) or []:
        por.setdefault(e['cid'], []).append(e)
    for c in lista:
        c['lista_empresas'] = por.get(c['id'], [])
    return render_template('contabilidades/index.html', contabilidades=lista, stats=_kpis())


def _validar(d):
    if not d.get('nome'):
        return 'Digite o nome do escritório.'
    if d.get('email') and '@' not in d['email']:
        return 'E-mail inválido.'
    return None


@contabilidades.route('/contabilidades/salvar', methods=['POST'])
@permission_required('clientes.edit')
def salvar():
    """Novo ou edição, pela janela da aba."""
    cid = request.form.get('id', type=int)
    d = limpar(request.form)
    erro = _validar(d)
    if erro:
        flash(erro, 'danger')
        return redirect(url_for('contabilidades.index'))
    if cid:
        antes = Contabilidade.get(cid)
        if not antes:
            flash('Escritório não encontrado.', 'danger')
            return redirect(url_for('contabilidades.index'))
        Contabilidade.atualizar(cid, request.form, current_user.id)
        registrar('escrita.alterou_contabilidade', 'cadastros', tabela='contabilidades', registro_id=cid,
                  antes={k: antes.get(k) for k in d}, depois=d)
        flash(f'Escritório {d["nome"]} atualizado.', 'success')
    else:
        ja = Contabilidade.por_nome(d['nome'])
        if ja:
            flash(f'O escritório {ja["nome"]} já está cadastrado.', 'warning')
            return redirect(url_for('contabilidades.index'))
        cid = Contabilidade.criar(request.form, current_user.id)
        registrar('escrita.criou_contabilidade', 'cadastros', tabela='contabilidades', registro_id=cid, depois=d)
        flash(f'Escritório {d["nome"]} cadastrado.', 'success')
    return redirect(url_for('contabilidades.index'))


@contabilidades.route('/contabilidades/<int:cid>/situacao', methods=['POST'])
@permission_required('clientes.edit')
def situacao(cid):
    """Inativa (some da escolha no cadastro) ou reativa. Não apaga: as
    empresas que vieram dele continuam apontando para ele."""
    c = Contabilidade.get(cid)
    if not c:
        flash('Escritório não encontrado.', 'danger')
        return redirect(url_for('contabilidades.index'))
    nova = 'INATIVO' if c['situacao'] == 'ATIVO' else 'ATIVO'
    Contabilidade.mudar_situacao(cid, nova)
    registrar('escrita.alterou_contabilidade', 'cadastros', tabela='contabilidades', registro_id=cid,
              antes={'situacao': c['situacao']}, depois={'situacao': nova})
    flash(f'{c["nome"]}: {"inativado" if nova == "INATIVO" else "reativado"}.', 'success')
    return redirect(url_for('contabilidades.index'))


@contabilidades.route('/contabilidades/api/rapido', methods=['POST'])
@login_required
def rapido():
    """"Novo escritório" sem sair da tela (formulário do cliente e converter
    avulso). Nome que já existe devolve o existente (e o reativa)."""
    dados = request.get_json(silent=True) or {}
    d = limpar(dados)
    erro = _validar(d)
    if erro:
        return jsonify(success=False, message=erro), 400
    ja = Contabilidade.por_nome(d['nome'])
    if ja:
        if ja.get('situacao') != 'ATIVO':
            Contabilidade.mudar_situacao(ja['id'], 'ATIVO')
        c = Contabilidade.get(ja['id'])
        return jsonify(success=True, ja_existia=True, item=_item(c, 0))
    cid = Contabilidade.criar(dados, current_user.id)
    if not cid:
        return jsonify(success=False, message='Não consegui cadastrar agora. Tente de novo.'), 500
    registrar('escrita.criou_contabilidade', 'cadastros', tabela='contabilidades', registro_id=cid,
              depois={**d, 'origem': 'formulario_cliente'})
    return jsonify(success=True, ja_existia=False, item=_item(Contabilidade.get(cid), 0))


def _item(c, empresas):
    return {'id': c['id'], 'nome': c['nome'], 'cidade': c.get('cidade') or '', 'uf': c.get('uf') or '',
            'contato': c.get('contato') or '', 'telefone': c.get('telefone') or '',
            'email': c.get('email') or '', 'empresas': empresas}
