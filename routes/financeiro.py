"""Rotas do módulo Financeiro — Recebimentos"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from utils.auth_helper import login_required, permission_required

financeiro = Blueprint('financeiro', __name__)

try:
    from models.lancamento_recebimento import LancamentoRecebimento
    LancamentoRecebimento.ensure_tables()
except Exception:
    LancamentoRecebimento = None  # type: ignore


@financeiro.route('/financeiro/')
@permission_required('financeiro.index')
def index():
    return render_template('financeiro/index.html')


# -----------------------------------------------------------------------
# Lista de recebimentos (com filtros)
# -----------------------------------------------------------------------
@financeiro.route('/financeiro/recebimento/')
@permission_required('financeiro.recebimento')
def recebimento_index():
    """Lista lançamentos de recebimento com filtros."""
    empresa_id          = request.args.get('empresa_id')
    conta_id            = request.args.get('conta_id')
    forma_recebimento_id = request.args.get('forma_recebimento_id')
    status              = request.args.get('status')
    data_inicio         = request.args.get('data_inicio')
    data_fim            = request.args.get('data_fim')
    f_descricao         = request.args.get('f_descricao')

    lancamentos = []
    totais = {}
    empresas = []
    contas = []
    formas = []

    if LancamentoRecebimento:
        lancamentos = LancamentoRecebimento.listar(
            empresa_id=empresa_id,
            conta_id=conta_id,
            forma_recebimento_id=forma_recebimento_id,
            status=status,
            data_inicio=data_inicio,
            data_fim=data_fim,
            descricao=f_descricao,
        )
        totais  = LancamentoRecebimento.totais(empresa_id=empresa_id, conta_id=conta_id)
        empresas = LancamentoRecebimento.listar_empresas()
        contas   = LancamentoRecebimento.listar_contas(empresa_id=empresa_id)
        formas   = LancamentoRecebimento.listar_formas_recebimento()

    return render_template(
        'financeiro/recebimento.html',
        lancamentos=lancamentos,
        totais=totais,
        empresas=empresas,
        contas=contas,
        formas=formas,
        filtros=dict(
            empresa_id=empresa_id or '',
            conta_id=conta_id or '',
            forma_recebimento_id=forma_recebimento_id or '',
            status=status or '',
            data_inicio=data_inicio or '',
            data_fim=data_fim or '',
            f_descricao=f_descricao or '',
        ),
    )


# -----------------------------------------------------------------------
# Excluir lançamento único
# -----------------------------------------------------------------------
@financeiro.route('/financeiro/recebimento/<int:lancamento_id>/excluir', methods=['POST'])
@login_required
def recebimento_excluir(lancamento_id):
    """Exclui um único lançamento de recebimento."""
    if not LancamentoRecebimento:
        flash('Módulo financeiro não disponível.', 'danger')
        return redirect(url_for('financeiro.recebimento_index'))

    lancamento = LancamentoRecebimento.get_by_id(lancamento_id)
    if not lancamento:
        flash('Lançamento não encontrado.', 'danger')
        return redirect(url_for('financeiro.recebimento_index', **_preserve_filters()))

    if LancamentoRecebimento.excluir(lancamento_id):
        flash('Lançamento excluído com sucesso!', 'success')
    else:
        flash('Erro ao excluir lançamento.', 'danger')

    return redirect(url_for('financeiro.recebimento_index', **_preserve_filters()))


# -----------------------------------------------------------------------
# Excluir lançamentos em lote
# -----------------------------------------------------------------------
@financeiro.route('/financeiro/recebimento/excluir-lote', methods=['POST'])
@login_required
def recebimento_excluir_lote():
    """Exclui múltiplos lançamentos selecionados via checkbox."""
    if not LancamentoRecebimento:
        flash('Módulo financeiro não disponível.', 'danger')
        return redirect(url_for('financeiro.recebimento_index'))

    ids_raw = request.form.getlist('ids')
    try:
        ids = [int(i) for i in ids_raw if i.isdigit()]
    except (ValueError, AttributeError):
        ids = []

    if not ids:
        flash('Nenhum lançamento selecionado.', 'warning')
        return redirect(url_for('financeiro.recebimento_index', **_preserve_filters()))

    if LancamentoRecebimento.excluir_lote(ids):
        flash(f'{len(ids)} lançamento(s) excluído(s) com sucesso!', 'success')
    else:
        flash('Erro ao excluir lançamentos.', 'danger')

    return redirect(url_for('financeiro.recebimento_index', **_preserve_filters()))


# =======================================================================
# Contas a pagar e a receber do ESCRITÓRIO (Documento E, fase 2)
#
# A baixa manual passa por registrar_baixa() — o ÚNICO escritor de
# fin_titulo_baixas (regra de ouro do documento). Estas rotas nunca tocam
# fin_titulo_baixas nem fin_titulos.status diretamente.
# =======================================================================
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from flask_login import current_user

from models.fin_titulo import FinTitulo, FinCategoria
from utils.atividade import registrar
from utils.financeiro_core import registrar_baixa, BaixaInvalida


def _dec_form(raw):
    """'1.234,56' ou '1234.56' → Decimal (levanta InvalidOperation)."""
    s = (str(raw or '')).strip()
    if not s:
        raise InvalidOperation('vazio')
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    return Decimal(s)


def _filtros_titulos():
    keys = ('tipo', 'status', 'venc_de', 'venc_ate', 'categoria_id', 'busca')
    return {k: request.form.get(k) or request.args.get(k) or '' for k in keys}


@financeiro.route('/financeiro/titulos')
@permission_required('financeiro.titulos')
def titulos():
    """Contas a pagar e a receber do escritório, em ordem de vencimento."""
    tipo = request.args.get('tipo', 'R')
    if tipo not in ('R', 'P', 'todos'):
        tipo = 'R'
    status = request.args.get('status', 'abertos')
    venc_de = request.args.get('venc_de') or None
    venc_ate = request.args.get('venc_ate') or None
    categoria_id = request.args.get('categoria_id') or None
    busca = (request.args.get('busca') or '').strip() or None

    lista = FinTitulo.listar(tipo=None if tipo == 'todos' else tipo,
                             status=status, venc_de=venc_de, venc_ate=venc_ate,
                             categoria_id=categoria_id, busca=busca)
    return render_template(
        'financeiro/titulos.html',
        titulos=lista,
        resumo=FinTitulo.resumo(),
        categorias=FinCategoria.listar(),
        hoje=date.today(),
        filtros=dict(tipo=tipo, status=status, venc_de=venc_de or '',
                     venc_ate=venc_ate or '', categoria_id=categoria_id or '',
                     busca=busca or ''))


@financeiro.route('/financeiro/titulos/novo', methods=['POST'])
@permission_required('financeiro.titulos')
def titulo_novo():
    f = request.form
    tipo = f.get('tipo')
    contraparte = (f.get('contraparte_nome') or '').strip()
    categoria_id = (f.get('categoria_id') or '').strip()
    descricao = (f.get('descricao') or '').strip()
    competencia = (f.get('competencia') or '').strip()      # YYYY-MM
    vencimento = (f.get('vencimento') or '').strip()
    emissao = (f.get('emissao') or '').strip() or date.today().isoformat()

    valor = None
    erro = None
    if tipo not in ('R', 'P'):
        erro = 'Escolha o tipo: a receber ou a pagar.'
    elif not contraparte:
        erro = 'Informe de quem é o título (contraparte).'
    elif not descricao:
        erro = 'Informe a descrição.'
    elif not categoria_id.isdigit():
        erro = 'Escolha a categoria.'
    elif not re.match(r'^\d{4}-\d{2}$', competencia):
        erro = 'Informe a competência (mês de referência).'
    elif not vencimento:
        erro = 'Informe o vencimento.'
    else:
        try:
            valor = _dec_form(f.get('valor'))
            if valor <= 0:
                erro = 'Valor deve ser maior que zero.'
        except InvalidOperation:
            erro = 'Valor inválido.'
    if erro:
        flash(erro, 'danger')
        return redirect(url_for('financeiro.titulos', **_filtros_titulos()))

    tid = FinTitulo.criar(
        tipo=tipo, contraparte_nome=contraparte,
        contraparte_doc=(f.get('contraparte_doc') or '').strip() or None,
        categoria_id=int(categoria_id), descricao=descricao,
        competencia=competencia + '-01', emissao=emissao,
        vencimento=vencimento, valor=valor,
        observacao=(f.get('observacao') or '').strip() or None)
    if tid:
        registrar('escrita.criou_titulo', 'financeiro', tabela='fin_titulos',
                  registro_id=tid,
                  depois={'tipo': tipo, 'contraparte': contraparte,
                          'descricao': descricao, 'valor': str(valor),
                          'competencia': competencia, 'vencimento': vencimento})
        flash('Título lançado!', 'success')
    else:
        flash('Erro ao lançar o título.', 'danger')
    return redirect(url_for('financeiro.titulos', **_filtros_titulos()))


@financeiro.route('/financeiro/titulos/<int:titulo_id>/baixar', methods=['POST'])
@permission_required('financeiro.titulos')
def titulo_baixar(titulo_id):
    f = request.form
    try:
        valor = _dec_form(f.get('valor'))
        extras = {}
        for campo in ('juros', 'multa', 'desconto'):
            extras[campo] = _dec_form(f.get(campo)) if (f.get(campo) or '').strip() else 0
    except InvalidOperation:
        flash('Valor inválido na baixa.', 'danger')
        return redirect(url_for('financeiro.titulos', **_filtros_titulos()))

    try:
        r = registrar_baixa(
            titulo_id, valor, f.get('data_baixa') or date.today().isoformat(),
            'manual', referencia=(f.get('referencia') or '').strip() or None,
            usuario_id=current_user.id, **extras)
    except BaixaInvalida as e:
        flash(str(e), 'danger')
        return redirect(url_for('financeiro.titulos', **_filtros_titulos()))

    if not r['criada']:
        flash('Essa baixa já estava registrada — nada foi duplicado.', 'warning')
    else:
        registrar('escrita.baixou_titulo', 'financeiro',
                  tabela='fin_titulo_baixas', registro_id=r['baixa_id'],
                  depois={'titulo_id': titulo_id, 'valor': str(valor),
                          'status_resultante': r['status'], **{
                              k: str(v) for k, v in extras.items() if v}})
        if r['status'] == 'liquidado':
            flash('Baixa registrada — título liquidado!', 'success')
        else:
            flash('Baixa parcial registrada — título segue em aberto pelo saldo.',
                  'success')
    return redirect(url_for('financeiro.titulos', **_filtros_titulos()))


@financeiro.route('/financeiro/titulos/<int:titulo_id>/cancelar', methods=['POST'])
@permission_required('financeiro.titulos')
def titulo_cancelar(titulo_id):
    t = FinTitulo.get_by_id(titulo_id)
    if not t:
        flash('Título não encontrado.', 'danger')
        return redirect(url_for('financeiro.titulos', **_filtros_titulos()))
    if t['status'] == 'cancelado':
        flash('Este título já estava cancelado.', 'warning')
        return redirect(url_for('financeiro.titulos', **_filtros_titulos()))
    if FinTitulo.cancelar(titulo_id):
        registrar('escrita.cancelou_titulo', 'financeiro', tabela='fin_titulos',
                  registro_id=titulo_id,
                  antes={'status': t.get('status') if t else None},
                  depois={'status': 'cancelado',
                          'descricao': t.get('descricao') if t else None})
        flash('Título cancelado.', 'success')
    else:
        flash('Não deu para cancelar: título já tem baixa (ou já está '
              'liquidado/cancelado).', 'warning')
    return redirect(url_for('financeiro.titulos', **_filtros_titulos()))


@financeiro.route('/financeiro/titulos/<int:titulo_id>/excluir', methods=['POST'])
@permission_required('financeiro.titulos')
def titulo_excluir(titulo_id):
    t = FinTitulo.get_by_id(titulo_id)
    if not t:
        flash('Título não encontrado.', 'danger')
        return redirect(url_for('financeiro.titulos', **_filtros_titulos()))
    if FinTitulo.excluir(titulo_id):
        registrar('escrita.excluiu_titulo', 'financeiro', tabela='fin_titulos',
                  registro_id=titulo_id,
                  antes={'descricao': t.get('descricao') if t else None,
                         'valor': str(t.get('valor')) if t else None})
        flash('Título excluído.', 'success')
    else:
        flash('Não deu para excluir: só título aberto, sem baixa e lançado à '
              'mão pode ser excluído — os demais se cancelam.', 'warning')
    return redirect(url_for('financeiro.titulos', **_filtros_titulos()))


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------
def _preserve_filters():
    """Retorna os filtros ativos da query string para manter na redirect."""
    keys = ('empresa_id', 'conta_id', 'forma_recebimento_id',
            'status', 'data_inicio', 'data_fim', 'f_descricao')
    return {k: request.form.get(k) or request.args.get(k) or ''
            for k in keys}
