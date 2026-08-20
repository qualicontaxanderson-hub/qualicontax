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


# =======================================================================
# Categorias do plano gerencial (Documento E, fase 3)
# =======================================================================
@financeiro.route('/financeiro/categorias')
@permission_required('financeiro.categorias')
def categorias():
    cats = FinCategoria.listar(apenas_ativas=False)
    return render_template('financeiro/categorias.html',
                           categorias=cats,
                           grupos=FinCategoria.grupos(),
                           usos=FinCategoria.usos())


@financeiro.route('/financeiro/categorias/nova', methods=['POST'])
@permission_required('financeiro.categorias')
def categoria_nova():
    tipo = request.form.get('tipo')
    grupo = (request.form.get('grupo_novo') or '').strip()         or (request.form.get('grupo') or '').strip()
    nome = (request.form.get('nome') or '').strip()
    if tipo not in ('R', 'P') or not grupo or not nome:
        flash('Preencha tipo, grupo e nome da categoria.', 'danger')
    elif FinCategoria.criar(tipo, grupo, nome):
        registrar('escrita.criou_categoria_fin', 'financeiro',
                  tabela='fin_categorias',
                  depois={'tipo': tipo, 'grupo': grupo, 'nome': nome})
        flash(f'Categoria "{nome}" criada no grupo {grupo}.', 'success')
    else:
        flash('Essa categoria já existe nesse grupo.', 'warning')
    return redirect(url_for('financeiro.categorias'))


@financeiro.route('/financeiro/categorias/<int:cat_id>/renomear', methods=['POST'])
@permission_required('financeiro.categorias')
def categoria_renomear(cat_id):
    nome = (request.form.get('nome') or '').strip()
    if not nome:
        flash('Informe o novo nome.', 'danger')
    elif FinCategoria.renomear(cat_id, nome):
        registrar('escrita.renomeou_categoria_fin', 'financeiro',
                  tabela='fin_categorias', registro_id=cat_id,
                  depois={'nome': nome})
        flash('Categoria renomeada.', 'success')
    else:
        flash('Não consegui renomear (nome repetido no grupo?).', 'warning')
    return redirect(url_for('financeiro.categorias'))


@financeiro.route('/financeiro/categorias/<int:cat_id>/alternar', methods=['POST'])
@permission_required('financeiro.categorias')
def categoria_alternar(cat_id):
    atual = next((c for c in FinCategoria.listar(apenas_ativas=False)
                  if c['id'] == cat_id), None)
    if not atual:
        flash('Categoria não encontrada.', 'danger')
    else:
        FinCategoria.set_ativa(cat_id, not atual['ativo'])
        registrar('escrita.alternou_categoria_fin', 'financeiro',
                  tabela='fin_categorias', registro_id=cat_id,
                  antes={'ativo': bool(atual['ativo'])},
                  depois={'ativo': not atual['ativo']})
        flash(('Categoria reativada.' if not atual['ativo']
               else 'Categoria desativada — some dos lançamentos novos; '
                    'os títulos antigos continuam com ela.'), 'success')
    return redirect(url_for('financeiro.categorias'))


# =======================================================================
# DRE gerencial (Documento E, seção 7)
# =======================================================================
_DRE_IMPOSTOS = 'Impostos sobre serviço'
_DRE_FINANCEIRAS = 'Financeiras'
_DRE_MESES = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
              'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']


def _montar_dre(rows):
    """Linhas de exibição do DRE na ordem da seção 7 do documento.

    Grupos R somam na RECEITA BRUTA; o grupo 'Impostos sobre serviço' desce
    antes da RECEITA LÍQUIDA; 'Financeiras' desce depois do RESULTADO
    OPERACIONAL; qualquer outro grupo P (inclusive os criados depois na tela
    de categorias) entra no bloco operacional, na ordem do plano.
    """
    grupos = {}
    for r in rows:
        g = grupos.setdefault((r['tipo'], r['grupo']), {
            'tipo': r['tipo'], 'grupo': r['grupo'], 'ordem': r['ordem'],
            'meses': [Decimal('0')] * 12, 'total': Decimal('0'), 'cats': {}})
        g['ordem'] = min(g['ordem'], r['ordem'])
        c = g['cats'].setdefault(r['nome'], {'nome': r['nome'],
                                             'meses': [Decimal('0')] * 12,
                                             'total': Decimal('0')})
        v = Decimal(str(r['total'] or 0))
        i = int(r['mes']) - 1
        g['meses'][i] += v
        g['total'] += v
        c['meses'][i] += v
        c['total'] += v
    for g in grupos.values():
        g['cats'] = sorted(g['cats'].values(), key=lambda c: c['nome'])

    def soma(lista):
        out = [Decimal('0')] * 12
        for g in lista:
            for i in range(12):
                out[i] += g['meses'][i]
        return out

    receitas = sorted((g for (t, _n), g in grupos.items() if t == 'R'),
                      key=lambda g: g['ordem'])
    impostos = [g for (t, n), g in grupos.items()
                if t == 'P' and n == _DRE_IMPOSTOS]
    financeiras = [g for (t, n), g in grupos.items()
                   if t == 'P' and n == _DRE_FINANCEIRAS]
    operacionais = sorted((g for (t, n), g in grupos.items()
                           if t == 'P' and n not in (_DRE_IMPOSTOS,
                                                     _DRE_FINANCEIRAS)),
                          key=lambda g: g['ordem'])

    rb = soma(receitas)
    imp = soma(impostos)
    rl = [rb[i] - imp[i] for i in range(12)]
    op = soma(operacionais)
    ro = [rl[i] - op[i] for i in range(12)]
    fin = soma(financeiras)
    liq = [ro[i] - fin[i] for i in range(12)]

    linhas = []
    for g in receitas:
        linhas.append({'tipo': 'grupo', 'sinal': '+', 'g': g})
    linhas.append({'tipo': 'subtotal', 'rotulo': 'RECEITA BRUTA', 'meses': rb})
    for g in impostos:
        linhas.append({'tipo': 'grupo', 'sinal': '-', 'g': g})
    linhas.append({'tipo': 'subtotal', 'rotulo': 'RECEITA LÍQUIDA', 'meses': rl})
    for g in operacionais:
        linhas.append({'tipo': 'grupo', 'sinal': '-', 'g': g})
    linhas.append({'tipo': 'subtotal', 'rotulo': 'RESULTADO OPERACIONAL', 'meses': ro})
    for g in financeiras:
        linhas.append({'tipo': 'grupo', 'sinal': '-', 'g': g})
    linhas.append({'tipo': 'subtotal', 'rotulo': 'RESULTADO LÍQUIDO', 'meses': liq})
    return linhas


@financeiro.route('/financeiro/dre')
@permission_required('financeiro.dre')
def dre():
    """DRE gerencial do escritório — competência OU caixa, sempre rotulado."""
    from models.fin_titulo import FinDre
    regime = request.args.get('regime', 'competencia')
    if regime not in ('competencia', 'caixa'):
        regime = 'competencia'
    ano_atual = date.today().year
    anos = FinDre.anos_com_dado()
    if ano_atual not in anos:
        anos.insert(0, ano_atual)
    try:
        ano = int(request.args.get('ano') or ano_atual)
    except ValueError:
        ano = ano_atual
    linhas = _montar_dre(FinDre.por_ano(ano, regime))
    return render_template('financeiro/dre.html', linhas=linhas, regime=regime,
                           ano=ano, anos=anos, meses=_DRE_MESES)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------
def _preserve_filters():
    """Retorna os filtros ativos da query string para manter na redirect."""
    keys = ('empresa_id', 'conta_id', 'forma_recebimento_id',
            'status', 'data_inicio', 'data_fim', 'f_descricao')
    return {k: request.form.get(k) or request.args.get(k) or ''
            for k in keys}
