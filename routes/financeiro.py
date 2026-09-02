"""Rotas do módulo Financeiro — Recebimentos"""
import logging
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

from models.fin_titulo import FinTitulo, FinCategoria, FinProgramacao
from utils.atividade import registrar
from utils.db_helper import execute_query
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
    keys = ('tipo', 'status', 'venc_de', 'venc_ate', 'categoria_id', 'busca', 'emp')
    return {k: request.form.get(k) or request.args.get(k) or '' for k in keys}


def _empresas_ctx():
    """(empresas ativas, ids selecionados no ?emp=, mapa id->apelido).

    ``emp`` é CSV de cliente_ids; vazio = todas. Ids fora das minhas empresas
    são descartados — ninguém enxerga empresa que não está marcada.
    """
    from models.fin_empresa import FinEmpresa
    emps = FinEmpresa.listar()
    validos = {e['cliente_id'] for e in emps}
    raw = (request.args.get('emp') or request.form.get('emp') or '').strip()
    sel = [int(x) for x in raw.split(',') if x.isdigit() and int(x) in validos]
    mapa = {e['cliente_id']: e['apelido'] for e in emps}
    return emps, sel, mapa


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

    from models.fin_titulo import FinCentroCusto
    centro_id = request.args.get('centro') or None
    emps, sel, mapa = _empresas_ctx()
    lista = FinTitulo.listar(tipo=None if tipo == 'todos' else tipo,
                             status=status, venc_de=venc_de, venc_ate=venc_ate,
                             categoria_id=categoria_id, busca=busca,
                             empresa_ids=sel, centro_id=centro_id)
    return render_template(
        'financeiro/titulos.html',
        titulos=lista,
        resumo=FinTitulo.resumo(empresa_ids=sel),
        categorias=FinCategoria.listar(),
        centros=FinCentroCusto.listar(),
        hoje=date.today(),
        fin_empresas=emps, sel_empresas=sel, emp_mapa=mapa,
        filtros=dict(tipo=tipo, status=status, venc_de=venc_de or '',
                     venc_ate=venc_ate or '', categoria_id=categoria_id or '',
                     busca=busca or '', emp=','.join(map(str, sel)),
                     centro=centro_id or ''))


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

    from models.fin_empresa import FinEmpresa
    empresa_raw = (f.get('empresa_id') or '').strip()

    valor = None
    erro = None
    if not empresa_raw.isdigit() or int(empresa_raw) not in FinEmpresa.ids_validos():
        erro = 'Escolha a empresa do lançamento.'
    elif tipo not in ('R', 'P'):
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

    centro_raw = (f.get('centro_custo_id') or '').strip()
    tid = FinTitulo.criar(
        empresa_id=int(empresa_raw),
        centro_custo_id=int(centro_raw) if centro_raw.isdigit() else None,
        tipo=tipo, contraparte_nome=contraparte,
        contraparte_doc=(f.get('contraparte_doc') or '').strip() or None,
        categoria_id=int(categoria_id), descricao=descricao,
        competencia=competencia + '-01', emissao=emissao,
        vencimento=vencimento, valor=valor,
        observacao=(f.get('observacao') or '').strip() or None)
    # "Repetir todo mês": cria a PROGRAMAÇÃO junto e amarra o título deste mês
    # nela (com a chave_idem da competência — a geração automática do mês não
    # vai duplicar o que já foi lançado à mão).
    if tid and f.get('repetir_mensal') == 'on':
        from models.fin_titulo import FinProgramacao
        pid = FinProgramacao.criar(
            empresa_id=int(empresa_raw), tipo=tipo, descricao=descricao,
            contraparte_nome=contraparte,
            contraparte_doc=(f.get('contraparte_doc') or '').strip() or None,
            categoria_id=int(categoria_id),
            centro_custo_id=int(centro_raw) if centro_raw.isdigit() else None,
            valor_esperado=valor,
            dia_vencimento=int(vencimento[8:10]),
            variavel=f.get('natureza') == 'variavel',
            inicio=competencia + '-01')
        if pid:
            execute_query(
                'UPDATE fin_titulos SET programacao_id = %s, chave_idem = %s '
                'WHERE id = %s',
                (pid, f'prog:{pid}:comp:{competencia}', tid))
            registrar('escrita.criou_programacao', 'financeiro',
                      tabela='fin_programacoes', registro_id=pid,
                      depois={'descricao': descricao, 'valor': str(valor),
                              'dia': int(vencimento[8:10]),
                              'variavel': f.get('natureza') == 'variavel',
                              'origem': 'novo_titulo'})
            flash(f'Programação mensal criada — todo mês nasce um título de '
                  f'"{descricao}".', 'success')

    if tid:
        registrar('escrita.criou_titulo', 'financeiro', tabela='fin_titulos',
                  registro_id=tid,
                  depois={'empresa_id': int(empresa_raw), 'tipo': tipo,
                          'contraparte': contraparte,
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

    # A pergunta das três opções (E2.3): 'juros' e 'desconto' já chegam com os
    # campos divididos pelo JS; 'reajuste' atualiza a mensalidade da programação
    # daqui pra frente — e o título deste mês, para a baixa fechar exata.
    decisao = (f.get('decisao') or '').strip()
    if decisao == 'reajuste':
        from models.fin_titulo import FinProgramacao
        t = FinTitulo.get_by_id(titulo_id)
        if t and t.get('programacao_id') and t['status'] == 'aberto' \
                and not t['valor_baixado']:
            antigo = FinProgramacao.reajustar(t['programacao_id'], valor)
            if antigo is not None:
                execute_query('UPDATE fin_titulos SET valor = %s '
                              "WHERE id = %s AND status = 'aberto'",
                              (valor, titulo_id))
                registrar('escrita.reajustou_programacao', 'financeiro',
                          tabela='fin_programacoes',
                          registro_id=t['programacao_id'],
                          antes={'valor_esperado': str(antigo)},
                          depois={'valor_esperado': str(valor),
                                  'titulo_id': titulo_id})
                flash(f'Mensalidade reajustada: '
                      f'R$ {float(antigo):.2f} → R$ {float(valor):.2f} '
                      'daqui pra frente.', 'success')

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
    from models.fin_titulo import FinCentroCusto
    # Por padrão a tela mostra só o que está EM USO. As desativadas continuam
    # alcançáveis por ?ver=todas — sem isso não haveria como reativar —, mas
    # não poluem a leitura: depois da migração do plano elas eram 40 e
    # apareciam no meio das vivas, cinzas, dando a impressão de sujeira.
    ver_todas = request.args.get('ver') == 'todas'
    cats = FinCategoria.listar(apenas_ativas=False)
    return render_template('financeiro/categorias.html',
                           categorias=cats,
                           ver_todas=ver_todas,
                           qtd_inativas=sum(1 for c in cats if not c['ativo']),
                           total_ativas=sum(1 for c in cats if c['ativo']),
                           blocos=FinCategoria.blocos(apenas_ativas=not ver_todas),
                           grupos=FinCategoria.grupos(),
                           pais=FinCategoria.pais(),
                           centros=FinCentroCusto.listar(apenas_ativos=False),
                           usos_centros=FinCentroCusto.usos(),
                           usos=FinCategoria.usos())


@financeiro.route('/financeiro/categorias/nova', methods=['POST'])
@permission_required('financeiro.categorias')
def categoria_nova():
    tipo = request.form.get('tipo')
    grupo = (request.form.get('grupo_novo') or '').strip() \
        or (request.form.get('grupo') or '').strip()
    nome = (request.form.get('nome') or '').strip()
    pai_raw = (request.form.get('pai_id') or '').strip()
    if pai_raw.isdigit():
        # SUBcategoria: herda tipo, grupo e ordem do pai — só o nome importa.
        if not nome:
            flash('Informe o nome da subcategoria.', 'danger')
        elif FinCategoria.criar(None, None, nome, pai_id=int(pai_raw)):
            registrar('escrita.criou_categoria_fin', 'financeiro',
                      tabela='fin_categorias',
                      depois={'pai_id': int(pai_raw), 'nome': nome, 'sub': True})
            flash(f'Subcategoria "{nome}" criada.', 'success')
        else:
            flash('Não deu: pai inválido (sub de sub não existe) ou nome '
                  'repetido no grupo.', 'warning')
        return redirect(url_for('financeiro.categorias'))
    # Os quatro tipos, nao dois. O modal sempre ofereceu Investimento e
    # Transferencia, e esta linha recusava os dois calada: a pessoa escolhia,
    # clicava em Criar e recebia "preencha tipo, grupo e nome" com tudo
    # preenchido. So a migracao criava categoria desses tipos.
    if tipo not in FinCategoria.TIPOS or not grupo or not nome:
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


@financeiro.route('/financeiro/categorias/<int:cat_id>/editar', methods=['POST'])
@permission_required('financeiro.categorias')
def categoria_editar(cat_id):
    """Nome, grupo e pai numa tacada — o renomear so mexia no nome."""
    nome = (request.form.get('nome') or '').strip()
    grupo = (request.form.get('grupo_novo') or '').strip() \
        or (request.form.get('grupo') or '').strip()
    pai_raw = (request.form.get('pai_id') or '').strip()
    pai_id = int(pai_raw) if pai_raw.isdigit() else None

    if not nome:
        flash('Informe o nome.', 'danger')
        return redirect(url_for('financeiro.categorias'))

    antes = next((c for c in FinCategoria.listar(apenas_ativas=False)
                  if c['id'] == cat_id), None)
    ok, motivo = FinCategoria.editar(cat_id, nome, grupo, pai_id)
    if ok:
        registrar('escrita.editou_categoria_fin', 'financeiro',
                  tabela='fin_categorias', registro_id=cat_id,
                  antes={'nome': (antes or {}).get('nome'),
                         'grupo': (antes or {}).get('grupo'),
                         'pai_id': (antes or {}).get('pai_id')},
                  depois={'nome': nome, 'grupo': grupo, 'pai_id': pai_id})
        flash(f'"{nome}" atualizada.', 'success')
    else:
        flash(motivo, 'warning')
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
# Centros de custo (E2.2) — GO, SP e GERAL (rateia meio a meio na leitura)
# =======================================================================
@financeiro.route('/financeiro/centros/novo', methods=['POST'])
@permission_required('financeiro.categorias')
def centro_novo():
    from models.fin_titulo import FinCentroCusto
    nome = (request.form.get('nome') or '').strip().upper()
    rateia = request.form.get('rateia') == 'on'
    if not nome:
        flash('Informe o nome do centro de custo.', 'danger')
    elif FinCentroCusto.criar(nome, rateia):
        registrar('escrita.criou_centro_custo', 'financeiro',
                  tabela='fin_centros_custo',
                  depois={'nome': nome, 'rateia': rateia})
        flash(f'Centro de custo "{nome}" criado.', 'success')
    else:
        flash('Esse centro de custo já existe.', 'warning')
    return redirect(url_for('financeiro.categorias'))


@financeiro.route('/financeiro/centros/<int:cc_id>/editar', methods=['POST'])
@permission_required('financeiro.categorias')
def centro_editar(cc_id):
    """Nome e rateio — o renomear só mexia no nome, e o rateio é o que
    muda o número do relatório."""
    from models.fin_titulo import FinCentroCusto
    nome = (request.form.get('nome') or '').strip().upper()
    rateia = request.form.get('rateia') == 'on'
    if not nome:
        flash('Informe o nome do centro de custo.', 'danger')
        return redirect(url_for('financeiro.categorias'))

    antes = next((x for x in FinCentroCusto.listar(apenas_ativos=False)
                  if x['id'] == cc_id), None)
    ok, motivo = FinCentroCusto.editar(cc_id, nome, rateia)
    if ok:
        registrar('escrita.editou_centro_custo', 'financeiro',
                  tabela='fin_centros_custo', registro_id=cc_id,
                  antes={'nome': (antes or {}).get('nome'),
                         'rateia': (antes or {}).get('rateia')},
                  depois={'nome': nome, 'rateia': rateia})
        flash(f'Centro de custo "{nome}" atualizado.', 'success')
    else:
        flash(motivo, 'warning')
    return redirect(url_for('financeiro.categorias'))


@financeiro.route('/financeiro/centros/<int:cc_id>/alternar', methods=['POST'])
@permission_required('financeiro.categorias')
def centro_alternar(cc_id):
    from models.fin_titulo import FinCentroCusto
    atual = next((x for x in FinCentroCusto.listar(apenas_ativos=False)
                  if x['id'] == cc_id), None)
    if not atual:
        flash('Centro de custo não encontrado.', 'danger')
    else:
        FinCentroCusto.set_ativo(cc_id, not atual['ativo'])
        registrar('escrita.alternou_centro_custo', 'financeiro',
                  tabela='fin_centros_custo', registro_id=cc_id,
                  depois={'ativo': not atual['ativo']})
        flash('Centro de custo atualizado.', 'success')
    return redirect(url_for('financeiro.categorias'))


# =======================================================================
# DRE gerencial (Documento E, seção 7)
# =======================================================================
_DRE_IMPOSTOS = 'Impostos sobre serviço'
_DRE_FINANCEIRAS = 'Financeiras'
_DRE_MESES = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
              'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']


def _aplicar_centro(rows, centro_sel, centros):
    """Peneira do centro de custo COM o rateio (E2.2).

    centro_sel None → devolve tudo (visão TODOS). Centro normal (GO/SP) →
    linhas dele + fração IGUAL das linhas dos centros que rateiam (GERAL
    com GO e SP = meio a meio). Centro que rateia (GERAL) → só as linhas
    cruas dele, sem rateio (a tela avisa). Linhas SEM centro ficam de fora
    das visões filtradas — a nota diz quanto ficou de fora, para o buraco
    aparecer em vez de sumir.
    """
    if not centro_sel:
        return rows, None
    ativos = [c for c in centros if c['ativo']]
    base = [c for c in ativos if not c['rateia']]
    rateiam = {c['id'] for c in ativos if c['rateia']}
    alvo_rateia = centro_sel in rateiam
    n = len(base) or 1
    out = []
    fora_sem = Decimal('0')
    for r in rows:
        cid = r.get('centro')
        v = Decimal(str(r['total'] or 0))
        if cid == centro_sel:
            out.append(r)
        elif cid is None:
            fora_sem += abs(v)
        elif (not alvo_rateia) and cid in rateiam:
            r2 = dict(r)
            r2['total'] = v / n
            out.append(r2)
    nota = {'sem_centro': fora_sem, 'rateio': not alvo_rateia and bool(rateiam),
            'partes': n, 'cru': alvo_rateia}
    return out, nota


def _montar_dre(rows, grupos_vis=None):
    """Linhas de exibição do DRE na ordem da seção 7 do documento.

    Grupos R somam na RECEITA BRUTA; o grupo 'Impostos sobre serviço' desce
    antes da RECEITA LÍQUIDA; qualquer outro grupo P — **inclusive Financeiras**
    — entra no bloco operacional, na ordem do plano.

    Financeiras subiu para dentro do operacional em 02/09/2026, a pedido do
    Anderson: juros e tarifa de banco sao custo de operar, e mante-las embaixo
    fazia RESULTADO OPERACIONAL e RESULTADO LIQUIDO diferirem por trocados
    (R$ 152,28 no ano) — duas linhas quase iguais que so davam ruido.

    INVESTIMENTO (tipo I) ganhou bloco proprio, ABAIXO do resultado e FORA
    dele: comprar um apartamento nao e despesa e nao pode reduzir o lucro —
    mas some-lo da tela escondia R$ 61 mil que sairam do banco. A ultima linha,
    RESULTADO APOS INVESTIMENTOS, responde a pergunta do dono: sobrou quanto
    depois de tudo, inclusive do que eu investi.

    ``grupos_vis`` e o RECORTE: None mostra o DRE inteiro; um conjunto de nomes
    de grupo mostra so eles e devolve UM subtotal, dito com todas as letras.
    O recorte e feito aqui, no servidor, e nunca somado na tela — a mesma lei
    da previa das regras: um numero, um lugar que o calcula.
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

    # ---- RECORTE: so os grupos escolhidos, com um subtotal proprio -------
    if grupos_vis is not None:
        vistos = sorted((g for g in grupos.values() if g['grupo'] in grupos_vis),
                        key=lambda g: (g['tipo'] != 'R', g['ordem']))
        linhas = [{'tipo': 'grupo', 'sinal': '+' if g['tipo'] == 'R' else '-',
                   'g': g} for g in vistos]
        tot = [Decimal('0')] * 12
        for g in vistos:
            for i in range(12):
                tot[i] += g['meses'][i] if g['tipo'] == 'R' else -g['meses'][i]
        linhas.append({'tipo': 'subtotal', 'recorte': True,
                       'rotulo': 'SOMA DOS %d GRUPOS MOSTRADOS' % len(vistos),
                       'meses': tot})
        return linhas

    receitas = sorted((g for (t, _n), g in grupos.items() if t == 'R'),
                      key=lambda g: g['ordem'])
    impostos = [g for (t, n), g in grupos.items()
                if t == 'P' and n == _DRE_IMPOSTOS]
    # Financeiras NAO sai mais daqui: e despesa operacional como as outras.
    operacionais = sorted((g for (t, n), g in grupos.items()
                           if t == 'P' and n != _DRE_IMPOSTOS),
                          key=lambda g: g['ordem'])
    investimentos = sorted((g for (t, _n), g in grupos.items() if t == 'I'),
                           key=lambda g: g['ordem'])

    rb = soma(receitas)
    imp = soma(impostos)
    rl = [rb[i] - imp[i] for i in range(12)]
    op = soma(operacionais)
    ro = [rl[i] - op[i] for i in range(12)]
    inv = soma(investimentos)
    apos = [ro[i] - inv[i] for i in range(12)]

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
    # O bloco de investimento so aparece quando existe: linha zerada num ano
    # sem investimento seria enfeite, e enfeite em relatorio vira duvida.
    if investimentos:
        for g in investimentos:
            linhas.append({'tipo': 'grupo', 'sinal': '-', 'g': g})
        linhas.append({'tipo': 'subtotal',
                       'rotulo': 'RESULTADO APÓS INVESTIMENTOS', 'meses': apos})
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
    from models.fin_titulo import FinCentroCusto
    emps, sel, mapa = _empresas_ctx()
    centros = FinCentroCusto.listar()
    centro_raw = (request.args.get('centro') or '').strip()
    centro_sel = int(centro_raw) if centro_raw.isdigit() and \
        any(c['id'] == int(centro_raw) for c in centros) else None
    rows, nota_centro = _aplicar_centro(
        FinDre.por_ano(ano, regime, empresa_ids=sel), centro_sel, centros)

    # Os grupos que EXISTEM no ano, na ordem do plano — é o que vira chip.
    # Sai da mesma consulta que monta a tela: um grupo sem movimento no ano
    # nao vira filtro, porque filtrar por ele daria uma tela vazia.
    ordem_g = {}
    for r in rows:
        k = (r['tipo'], r['grupo'])
        ordem_g[k] = min(ordem_g.get(k, r['ordem']), r['ordem'])
    grupos_todos = [g for (_t, g) in sorted(ordem_g, key=lambda k: (k[0] != 'R',
                                                                    ordem_g[k]))]
    bruto = (request.args.get('grupos') or '').strip()
    escolhidos = [g for g in bruto.split('|') if g in grupos_todos] if bruto else []
    # Escolher TODOS e o mesmo que nao filtrar: sem recorte, sem aviso.
    grupos_sel = escolhidos if escolhidos and len(escolhidos) < len(grupos_todos) else []
    linhas = _montar_dre(rows, set(grupos_sel) or None)
    # No celular abre um mes por vez: comeca no ULTIMO mes com movimento
    # (o retrato mais recente); sem movimento nenhum, no mes de hoje.
    mes_ini = max((i + 1 for l in linhas if l['tipo'] == 'grupo'
                   for i, v in enumerate(l['g']['meses']) if v), default=None)
    if mes_ini is None:
        mes_ini = date.today().month if ano == ano_atual else 12
    return render_template('financeiro/dre.html', linhas=linhas,
                           mes_ini=mes_ini,
                           grupos_todos=grupos_todos, grupos_sel=grupos_sel,
                           regime=regime,
                           fin_empresas=emps, sel_empresas=sel, emp_mapa=mapa,
                           centros=centros, centro_sel=centro_sel,
                           nota_centro=nota_centro,
                           ano=ano, anos=anos, meses=_DRE_MESES)


# =======================================================================
# Fluxo de caixa projetado (Documento E, seção 6 / fase 9)
# =======================================================================
def _montar_fluxo(saldo_inicial, rows, hoje, dias):
    """Projeção diária a partir de hoje.

    Vencidos em aberto entram no PRIMEIRO dia (a cobrança deles é para já);
    a tela avisa quanto do primeiro dia é atraso. O que vence depois do
    horizonte não some: vira o rodapé "fora do horizonte".
    Devolve (linhas, resumo). Cada linha: data, entrada, saida, saldo,
    atrasado_e, atrasado_s. Só dias com movimento viram linha.
    """
    from collections import defaultdict
    limite = hoje + __import__('datetime').timedelta(days=dias)
    pordia = defaultdict(lambda: {'entrada': Decimal('0'), 'saida': Decimal('0'),
                                  'atrasado_e': Decimal('0'), 'atrasado_s': Decimal('0')})
    fora = {'entrada': Decimal('0'), 'saida': Decimal('0')}
    for r in rows:
        v = Decimal(str(r['total'] or 0))
        venc = r['vencimento']
        campo = 'entrada' if r['tipo'] == 'R' else 'saida'
        if venc > limite:
            fora[campo] += v
            continue
        dia = max(venc, hoje)              # vencido cai no primeiro dia
        pordia[dia][campo] += v
        if venc < hoje:
            pordia[dia]['atrasado_' + ('e' if r['tipo'] == 'R' else 's')] += v

    linhas = []
    saldo = saldo_inicial
    menor = {'saldo': saldo, 'data': hoje}
    primeiro_negativo = None
    for dia in sorted(pordia):
        m = pordia[dia]
        saldo = saldo + m['entrada'] - m['saida']
        linhas.append({'data': dia, 'saldo': saldo, **m})
        if saldo < menor['saldo']:
            menor = {'saldo': saldo, 'data': dia}
        if saldo < 0 and primeiro_negativo is None:
            primeiro_negativo = dia
    resumo = {'saldo_final': saldo, 'menor': menor,
              'primeiro_negativo': primeiro_negativo, 'fora': fora}
    return linhas, resumo


@financeiro.route('/financeiro/fluxo')
@permission_required('financeiro.fluxo')
def fluxo():
    from models.fin_titulo import FinFluxo
    try:
        dias = int(request.args.get('dias') or 30)
    except ValueError:
        dias = 30
    if dias not in (30, 60, 90):
        dias = 30
    hoje = date.today()
    emps, sel, mapa = _empresas_ctx()
    saldos = FinFluxo.saldos_vigentes(sel)
    saldo_total = sum((Decimal(str(x['valor'])) for x in saldos), Decimal('0'))
    linhas, resumo = _montar_fluxo(saldo_total,
                                   FinFluxo.abertos_por_vencimento(sel),
                                   hoje, dias)
    return render_template('financeiro/fluxo.html', linhas=linhas,
                           resumo=resumo, saldos=saldos,
                           saldo_total=saldo_total,
                           fin_empresas=emps, sel_empresas=sel, emp_mapa=mapa,
                           dias=dias, hoje=hoje)


@financeiro.route('/financeiro/fluxo/saldo', methods=['POST'])
@permission_required('financeiro.fluxo')
def fluxo_saldo():
    from models.fin_titulo import FinFluxo
    from models.fin_empresa import FinEmpresa
    empresa_raw = (request.form.get('empresa_id') or '').strip()
    if not empresa_raw.isdigit() or int(empresa_raw) not in FinEmpresa.ids_validos():
        flash('Escolha de qual empresa é o saldo.', 'danger')
        return redirect(url_for('financeiro.fluxo'))
    try:
        valor = _dec_form(request.form.get('valor'))
    except InvalidOperation:
        flash('Valor de saldo inválido.', 'danger')
        return redirect(url_for('financeiro.fluxo'))
    data = request.form.get('data') or date.today().isoformat()
    FinFluxo.registrar_saldo(data, valor, current_user.id, int(empresa_raw))
    registrar('escrita.informou_saldo', 'financeiro', tabela='fin_saldos',
              depois={'empresa_id': int(empresa_raw), 'data': data,
                      'valor': str(valor)})
    flash('Saldo registrado — a projeção parte dele agora.', 'success')
    return redirect(url_for('financeiro.fluxo'))


# =======================================================================
# Extrato bancário — importação OFX (Documento E, fase 4)
#
# Ordem combinada com o Anderson (20/08/2026): OFX -> Excel -> PDF sólidos
# primeiro; Pluggy (API agregadora) por último, como aposta com retaguarda.
# =======================================================================
@financeiro.route('/financeiro/extrato')
@permission_required('financeiro.extrato')
def extrato():
    from models.extrato_lancamento import ExtratoLancamento
    from models.fin_titulo import FinCentroCusto
    emps, sel, mapa = _empresas_ctx()

    def _um(chave, validos=None):
        v = (request.args.get(chave) or '').strip()
        return v if (not validos or v in validos) else ''

    filtros = dict(
        data_de=request.args.get('data_de') or '',
        data_ate=request.args.get('data_ate') or '',
        conta=request.args.get('conta') or '',
        busca=(request.args.get('busca') or '').strip(),
        documento=(request.args.get('documento') or '').strip(),
        # Sem categoria e o PADRAO: o objetivo da tela e classificar.
        # "todos" e o valor explicito da aba Lancamentos.
        classif=_um('classif', ('sim', 'nao', 'conferir', 'todos')) or 'nao',
        tipo=_um('tipo', ('credito', 'debito')),
        categoria_id=_um('categoria_id'),
        centro_id=_um('centro_id'),
        vmin=(request.args.get('vmin') or '').strip(),
        vmax=(request.args.get('vmax') or '').strip())

    # O que vai para o SQL: vazio vira None; números viram número.
    args = {k: (v or None) for k, v in filtros.items()}
    for k in ('vmin', 'vmax'):
        try:
            args[k] = float(args[k]) if args[k] else None
        except ValueError:
            args[k] = None
    if args.get('categoria_id') and not str(args['categoria_id']).isdigit():
        args['categoria_id'] = None
    if args.get('centro_id') and args['centro_id'] != 'sem' \
            and not str(args['centro_id']).isdigit():
        args['centro_id'] = None
    if args.get('classif') == 'todos':
        args['classif'] = None
    args['empresa_ids'] = sel

    # Quantos filtros o usuário ligou — vira o selo do painel fechado.
    # O padrao (sem categoria) nao conta: selo permanente e ruido.
    ativos = sum(1 for k, v in filtros.items() if v and k != 'classif')
    if filtros['classif'] != 'nao':
        ativos += 1

    # A lista sai UMA vez e é agrupada por dia aqui: decidir "mudou o dia?"
    # no Jinja exigiria comparar a linha com a anterior, que foi o que
    # duplicou cabeçalho na tela de categorias quando a ordem veio torta.
    lancs = ExtratoLancamento.listar(**args)
    return render_template('financeiro/extrato.html',
                           lancamentos=lancs,
                           dias=ExtratoLancamento.por_dia(lancs),
                           totais=ExtratoLancamento.totais(**args),
                           contas=ExtratoLancamento.contas(empresa_ids=sel),
                           # apelido e agência vêm do CADASTRO de contas: o
                           # extrato guarda o nome cru do OFX, e o do Sicredi
                           # chega como "CCPI DO CERRADO DE GO".
                           contas_cad=ExtratoLancamento.contas_mapa(),
                           ler=ExtratoLancamento.ler_descricao,
                           doc_fmt=ExtratoLancamento.formatar_doc,
                           categorias=FinCategoria.listar(),
                           centros=FinCentroCusto.listar(),
                           # os grupos de empresa que ja existem no cadastro:
                           # a regra pode valer para o grupo inteiro, e quem
                           # entrar nele depois herda
                           grupos_emp=execute_query(
                               'SELECT g.id, g.nome, COUNT(r.cliente_id) AS n '
                               '  FROM grupos_clientes g '
                               '  LEFT JOIN cliente_grupo_relacao r ON r.grupo_id = g.id '
                               ' GROUP BY g.id, g.nome ORDER BY g.nome',
                               fetch=True) or [],
                           fin_empresas=emps, sel_empresas=sel, emp_mapa=mapa,
                           filtros_ativos=ativos, hoje=date.today(),
                           limite=500, filtros=filtros)


def _volta_extrato(**padrao):
    """Volta para a MESMA visao do extrato de onde o gesto partiu.

    O referrer traz a aba e os filtros (classif=..., datas, conta); sem ele,
    cai no padrao do gesto. So a query e reaproveitada — o caminho e sempre
    o nosso, nunca o que veio de fora.
    """
    from urllib.parse import urlsplit
    ref = urlsplit(request.referrer or '')
    if ref.path.rstrip('/').endswith('/financeiro/extrato') and ref.query:
        return redirect(url_for('financeiro.extrato') + '?' + ref.query)
    return redirect(url_for('financeiro.extrato', **padrao))


@financeiro.route('/financeiro/extrato/importar', methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_importar():
    from models.extrato_lancamento import ExtratoLancamento
    from models.fin_empresa import FinEmpresa
    from utils.ofx_parser import parse_ofx, chave_dedup, OfxInvalido
    empresa_raw = (request.form.get('empresa_id') or '').strip()
    if not empresa_raw.isdigit() or int(empresa_raw) not in FinEmpresa.ids_validos():
        flash('Escolha de qual empresa é a conta antes de importar.', 'warning')
        return _volta_extrato()
    empresa_id = int(empresa_raw)
    arquivos = [a for a in request.files.getlist('arquivos') if a and a.filename]
    if not arquivos:
        flash('Escolha ao menos um arquivo OFX.', 'warning')
        return _volta_extrato()

    for arq in arquivos:
        try:
            dados = parse_ofx(arq.read())
        except OfxInvalido as e:
            flash(f'{arq.filename}: {e}', 'danger')
            continue
        except Exception:
            flash(f'{arq.filename}: não consegui ler este arquivo — me mande '
                  'ele que eu ajusto o leitor.', 'danger')
            continue

        # chave de idempotência por lançamento; sem FITID, conta a repetição
        # do mesmo (data, valor, descrição, documento) DENTRO do arquivo.
        repeticoes, candidatos = {}, []
        for l in dados['lancamentos']:
            k = (l['data'], str(l['valor']), l['descricao'], l['documento'])
            n = repeticoes.get(k, 0)
            repeticoes[k] = n + 1
            h = chave_dedup(empresa_id, dados['banco'], dados['conta'], l, n)
            candidatos.append((h, l))

        vistos, unicos = set(), []
        for h, l in candidatos:               # FITID repetido no mesmo arquivo
            if h in vistos:
                continue
            vistos.add(h)
            unicos.append((h, l))
        ja_existiam = ExtratoLancamento.hashes_existentes([h for h, _ in unicos])
        novos = [(h, l) for h, l in unicos if h not in ja_existiam]
        auto = 0
        if novos:
            ExtratoLancamento.inserir_lote(
                novos, dados['banco'], dados['conta'], arq.filename,
                current_user.id, empresa_id=empresa_id)
            # E2.4: os que casam com memorização já chegam classificados —
            # "nem precisa ter trabalho de encontrar".
            from models.extrato_lancamento import ExtratoMemorizacao
            from utils.db_helper import execute_query as _q
            marks = ','.join(['%s'] * len(novos))
            ids_rows = _q(f'SELECT id FROM extrato_lancamentos '
                          f'WHERE hash_dedup IN ({marks})',
                          tuple(h for h, _ in novos), fetch=True) or []
            auto = ExtratoMemorizacao.aplicar_em_ids([r['id'] for r in ids_rows])

        msg = (f"{arq.filename}: {len(novos)} lançamento(s) novo(s), "
               f"{len(unicos) - len(novos)} já estavam (ignorados)")
        if auto:
            msg += f'; {auto} já chegaram CLASSIFICADOS pela memorização'
        if dados.get('saldo'):
            sd = dados['saldo']
            quando = f" em {sd['data'][8:10]}/{sd['data'][5:7]}" if sd.get('data') else ''
            msg += (f". Saldo do arquivo: R$ {sd['valor']:,.2f}{quando} — "
                    "confira no Fluxo de Caixa se vale atualizar o saldo real.")
        flash(msg, 'success' if novos else 'warning')
        registrar('escrita.importou_extrato', 'financeiro',
                  tabela='extrato_lancamentos',
                  depois={'empresa_id': empresa_id, 'arquivo': arq.filename,
                          'banco': dados['banco'],
                          'conta': dados['conta'], 'novos': len(novos),
                          'repetidos': len(unicos) - len(novos)})
    return _volta_extrato()


# =======================================================================
# Contas bancárias e a fila de "de quem é esta conta?" (21/08/2026)
#
# A CONTA é a impressão digital da empresa: o roteador não pergunta nada a
# ninguém, lê a conta do arquivo e sabe de quem é. Aqui só se cadastra, se
# responde a pendência e se apaga um período para refazer.
# =======================================================================
@financeiro.route('/financeiro/contas')
@permission_required('financeiro.extrato')
def contas():
    from models.extrato_lancamento import FinContaBancaria, FinExtratoPendencia
    emps, sel, mapa = _empresas_ctx()
    # Órfã (chegou sem número no nome e com conta desconhecida) é sinal de
    # funcionário que não seguiu o combinado: só ADMIN vê, para cobrar.
    admin = bool(getattr(current_user, 'is_admin', None) and current_user.is_admin())
    return render_template(
        'financeiro/contas.html',
        contas=FinContaBancaria.listar(empresa_ids=sel, apenas_ativas=False),
        pendencias=FinExtratoPendencia.listar(empresa_ids=sel, ver_orfas=admin),
        eh_admin=admin,
        fin_empresas=emps, sel_empresas=sel, emp_mapa=mapa)


@financeiro.route('/financeiro/contas/nova', methods=['POST'])
@permission_required('financeiro.extrato')
def conta_nova():
    from models.fin_empresa import FinEmpresa
    from utils.extrato_ingest import registrar_conta, BANCOS
    f = request.form
    emp = (f.get('empresa_id') or '').strip()
    conta = (f.get('conta') or '').strip()
    banco_id = (f.get('banco_id') or '').strip()
    if not emp.isdigit() or int(emp) not in FinEmpresa.ids_validos():
        flash('Escolha a empresa dona da conta.', 'danger')
    elif not conta:
        flash('Informe o número da conta.', 'danger')
    else:
        nome_banco = BANCOS.get(banco_id.lstrip('0'), f.get('banco_nome') or '')
        novo = registrar_conta(
            int(emp), banco_id, nome_banco or None, conta,
            agencia=(f.get('agencia') or '').strip() or None,
            apelido=(f.get('apelido') or '').strip() or None,
            usuario_id=current_user.id)
        if novo:
            registrar('escrita.criou_conta_bancaria', 'financeiro',
                      tabela='fin_contas', registro_id=novo,
                      depois={'empresa_id': int(emp), 'banco': nome_banco,
                              'conta': conta})
            flash(f'Conta {conta} cadastrada — os extratos dela passam a entrar '
                  'sozinhos, com qualquer nome de arquivo.', 'success')
        else:
            flash('Essa conta já está cadastrada (talvez em outra empresa).',
                  'warning')
    return redirect(url_for('financeiro.contas'))


@financeiro.route('/financeiro/contas/<int:conta_id>/alternar', methods=['POST'])
@permission_required('financeiro.extrato')
def conta_alternar(conta_id):
    from models.extrato_lancamento import FinContaBancaria
    c = FinContaBancaria.get(conta_id)
    if not c:
        flash('Conta não encontrada.', 'danger')
    else:
        FinContaBancaria.set_ativa(conta_id, not c['ativo'])
        registrar('escrita.alternou_conta_bancaria', 'financeiro',
                  tabela='fin_contas', registro_id=conta_id,
                  depois={'ativo': not c['ativo']})
        flash('Conta reativada.' if not c['ativo'] else
              'Conta desativada — extratos dela voltam a ficar pendentes.',
              'success')
    return redirect(url_for('financeiro.contas'))


@financeiro.route('/financeiro/pendencias/<int:pid>/resolver', methods=['POST'])
@permission_required('financeiro.extrato')
def pendencia_resolver(pid):
    """Alguém disse de quem é a conta: cadastra e RELÊ o arquivo na hora."""
    from models.extrato_lancamento import FinExtratoPendencia
    from models.fin_empresa import FinEmpresa
    from utils.extrato_ingest import registrar_conta, BANCOS
    p = FinExtratoPendencia.get(pid)
    if not p:
        flash('Pendência não encontrada.', 'danger')
        return redirect(url_for('financeiro.contas'))
    emp = (request.form.get('empresa_id') or '').strip()
    if not emp.isdigit() or int(emp) not in FinEmpresa.ids_validos():
        flash('Escolha a empresa dona desta conta.', 'danger')
        return redirect(url_for('financeiro.contas'))

    conta = (request.form.get('conta') or p.get('conta') or '').strip()
    if not conta:
        flash('Informe o número da conta (o arquivo não trouxe).', 'danger')
        return redirect(url_for('financeiro.contas'))

    novo = registrar_conta(
        int(emp), p.get('banco_id'),
        BANCOS.get(str(p.get('banco_id') or '').lstrip('0'), p.get('banco_nome')),
        conta, agencia=(request.form.get('agencia') or p.get('agencia') or '').strip() or None,
        usuario_id=current_user.id)
    registrar('escrita.resolveu_pendencia_extrato', 'financeiro',
              tabela='fin_extrato_pendencias', registro_id=pid,
              depois={'empresa_id': int(emp), 'conta': conta,
                      'arquivo': p['arquivo']})

    # relê SÓ o arquivo desta pendência — nunca a pasta inteira. Quem responde
    # uma pergunta não pode mexer no que não foi perguntado.
    import cron_extrato
    try:
        r = cron_extrato.processar_um(p['caminho'], usuario_id=current_user.id)
    except Exception:
        logging.getLogger(__name__).exception('[extrato] falha ao reler %s',
                                              p['caminho'])
        r = {'ok': False, 'motivo': 'não consegui reler o arquivo agora'}
    FinExtratoPendencia.resolver(pid)
    if r.get('ok'):
        flash(f'Conta {conta} cadastrada e o arquivo entrou: {r["novos"]} '
              f'lançamento(s) de {r["empresa"]}. Os próximos meses entram '
              'sozinhos, com qualquer nome de arquivo.', 'success')
    else:
        flash(f'Conta {conta} cadastrada. O arquivo não entrou agora '
              f'({r.get("motivo")}) — ele entra no próximo ciclo.', 'warning')
    return redirect(url_for('financeiro.contas'))


@financeiro.route('/financeiro/extrato/apagar-periodo', methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_apagar_periodo():
    """Errou? Apaga o período daquela conta e manda o arquivo de novo.

    É a alternativa combinada ao "desfazer" — a idempotência garante que
    reenviar o mesmo arquivo recria exatamente o que foi apagado.
    """
    from utils.db_helper import execute_query as _q
    f = request.form
    emp = (f.get('empresa_id') or '').strip()
    de, ate = (f.get('de') or '').strip(), (f.get('ate') or '').strip()
    conta = (f.get('conta') or '').strip()
    if not emp.isdigit() or not de or not ate:
        flash('Escolha a empresa e o período a apagar.', 'danger')
        return _volta_extrato()

    cond = ['empresa_id = %s', 'data BETWEEN %s AND %s']
    params = [int(emp), de, ate]
    if conta:
        cond.append("CONCAT(COALESCE(banco,''), ' · ', COALESCE(conta,'')) = %s")
        params.append(conta)
    where = ' AND '.join(cond)

    quantos = (_q(f'SELECT COUNT(*) AS n FROM extrato_lancamentos WHERE {where}',
                  tuple(params), fetch=True, fetch_one=True) or {}).get('n', 0)
    if not quantos:
        flash('Nenhum lançamento nesse período/conta — nada foi apagado.',
              'warning')
        return _volta_extrato()

    _q(f'DELETE FROM extrato_lancamentos WHERE {where}', tuple(params))
    registrar('escrita.apagou_periodo_extrato', 'financeiro',
              tabela='extrato_lancamentos',
              antes={'empresa_id': int(emp), 'de': de, 'ate': ate,
                     'conta': conta or 'todas', 'apagados': quantos})
    flash(f'{quantos} lançamento(s) apagados de {de} a {ate}. Mande o arquivo '
          'para a pasta de novo — ele entra limpo.', 'success')
    return _volta_extrato()


# =======================================================================
# Classificação + memorização do extrato (E2.4)
# =======================================================================
@financeiro.route('/financeiro/extrato/<int:lanc_id>/classificar', methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_classificar(lanc_id):
    from models.extrato_lancamento import ExtratoLancamento, ExtratoMemorizacao
    f = request.form
    lanc = ExtratoLancamento.get(lanc_id)
    if not lanc:
        flash('Lançamento não encontrado.', 'danger')
        return _volta_extrato()
    cat_raw = (f.get('categoria_id') or '').strip()
    cat = next((c for c in FinCategoria.listar() if str(c['id']) == cat_raw), None)
    if not cat:
        flash('Escolha a categoria.', 'danger')
        return _volta_extrato()
    # Crédito é receita, débito é despesa. TRANSFERÊNCIA vale nos dois
    # sentidos (é dinheiro seu andando de conta) e INVESTIMENTO vale na
    # saída — sem isso o extrato não tinha como classificar nenhum dos dois,
    # e é justamente aqui que eles aparecem.
    esperado = 'R' if lanc['valor'] >= 0 else 'P'
    aceitos = {esperado, 'T'} | ({'I'} if esperado == 'P' else set())
    if cat['tipo'] not in aceitos:
        flash(('Este lançamento é um CRÉDITO — escolha receita ou transferência.'
               if esperado == 'R' else
               'Este lançamento é um DÉBITO — escolha despesa, investimento '
               'ou transferência.'), 'danger')
        return _volta_extrato()
    centro_raw = (f.get('centro_custo_id') or '').strip()
    centro = int(centro_raw) if centro_raw.isdigit() else None

    virar_regra = f.get('memorizar') == 'on'
    retroagir = f.get('retroagir') == 'on'
    mem_id = None
    varridos = 0
    aviso = None

    if virar_regra:
        d = _regra_do_form(f, lanc)
        if not d['termos'] or len(' '.join(d['termos'])) < 4:
            flash('A regra precisa de pelo menos um trecho com 4 letras.', 'danger')
            return _volta_extrato()
        if d['escopo'] == 'lista' and not d['empresas']:
            flash('Escolha ao menos uma empresa para a regra.', 'danger')
            return _volta_extrato()
        if d['escopo'] == 'grupo' and not d['grupo_id']:
            flash('Escolha o grupo de empresas.', 'danger')
            return _volta_extrato()

        mem_id = ExtratoMemorizacao.criar(
            d['termos'], cat['id'], centro,
            empresa_id=lanc.get('empresa_id'),
            conta=d['conta'], sinal=d['sinal'], valor_exato=d['valor_exato'],
            escopo=d['escopo'], grupo_id=d['grupo_id'], empresas=d['empresas'],
            aplicar=d['aplicar'], criado_por=current_user.id)

        if mem_id:
            registrar('escrita.criou_regra_extrato', 'financeiro',
                      tabela='fin_extrato_memorizacoes', registro_id=mem_id,
                      depois={'termos': d['termos'], 'categoria': cat['nome'],
                              'centro_custo_id': centro, 'conta': d['conta'],
                              'sinal': d['sinal'], 'valor_exato': d['valor_exato'],
                              'escopo': d['escopo'], 'grupo_id': d['grupo_id'],
                              'empresas': sorted(d['empresas']),
                              'aplicar': d['aplicar'], 'retroagiu': retroagir})
            # O passado so e tocado quando a pessoa pediu.
            if retroagir:
                varridos = ExtratoMemorizacao.aplicar_retroativa(mem_id)
        else:
            aviso = ('Já existe uma regra ativa idêntica — com os mesmos '
                     'trechos, as mesmas condições e o mesmo alcance. Este '
                     'lançamento foi classificado, mas nenhuma regra nova '
                     'nasceu.')

    # ---- O TÍTULO (degrau 6). Três saídas, e transferência não tem título:
    # não é obrigação, é dinheiro andando entre contas próprias.
    titulo_acao = (f.get('titulo_acao') or 'nao').strip()
    conciliou, titulo_msg = None, ''
    if titulo_acao in ('criar', 'casar') and cat['tipo'] != 'T':
        from datetime import date as _date
        leitura = ExtratoLancamento.ler_descricao(lanc['descricao'])
        if titulo_acao == 'criar':
            comp_raw = (f.get('titulo_competencia') or '').strip()  # AAAA-MM
            try:
                ano, mes = comp_raw.split('-')[:2]
                competencia = _date(int(ano), int(mes), 1)
            except (ValueError, AttributeError):
                competencia = lanc['data'].replace(day=1)
            okc, motivo, tid = ExtratoLancamento.conciliar(
                lanc, criar={'competencia': competencia,
                             'contraparte_nome': leitura['nome'] or cat['nome'],
                             'contraparte_doc': leitura['doc'],
                             'categoria_id': cat['id'],
                             'centro_custo_id': centro},
                usuario_id=current_user.id)
        else:
            tid_raw = (f.get('titulo_id') or '').strip()
            okc, motivo, tid = ExtratoLancamento.conciliar(
                lanc, titulo_id=int(tid_raw) if tid_raw.isdigit() else None,
                usuario_id=current_user.id)
        if okc:
            conciliou = tid
            registrar('escrita.conciliou_extrato', 'financeiro',
                      tabela='fin_titulos', registro_id=tid,
                      depois={'lancamento_id': lanc_id, 'acao': titulo_acao,
                              'valor': float(lanc['valor'])})
            titulo_msg = (' Título criado já quitado.' if titulo_acao == 'criar'
                          else ' Baixa registrada no título.')
        else:
            flash(f'A classificação valeu, mas o título não: {motivo}', 'warning')

    # ---- "ISSO SE REPETE?" (degrau 7). So com titulo no gesto: programacao
    # gera titulo todo mes, e sem titulo agora nao ha o que repetir.
    repete = (f.get('repete') or '').strip()
    # ...e SO quando o gesto memorizou. "So este lancamento" quer dizer unico:
    # a tela ja nao pergunta, e esta trava impede que uma aba velha ou um POST
    # fora de ordem crie a programacao assim mesmo (02/09/2026 — o Anderson
    # achou: o Como dizia unico e o Repete? criava conta mensal).
    if conciliou and repete in ('fixa', 'variavel') and virar_regra:
        from datetime import date as _date
        leitura = ExtratoLancamento.ler_descricao(lanc['descricao'])
        nome_prog = (leitura['nome'] or cat['nome'])[:255]
        tipo_prog = 'R' if lanc['valor'] >= 0 else 'P'
        # Ja existe programacao ATIVA para a mesma contraparte, mesmo tipo e
        # mesma empresa? Criar outra faria gerar_mes duplicar a conta.
        dup = execute_query(
            'SELECT id FROM fin_programacoes '
            ' WHERE ativo = 1 AND empresa_id = %s AND tipo = %s '
            '   AND contraparte_nome = %s',
            (lanc.get('empresa_id'), tipo_prog, nome_prog),
            fetch=True, fetch_one=True)
        if dup:
            flash(f'Já existe programação ativa para "{nome_prog}" — '
                  'nenhuma nova foi criada.', 'warning')
        else:
            d = lanc['data']
            # inicio no MES SEGUINTE: o titulo deste mes acabou de nascer na
            # conciliacao; comecar neste mes faria gerar_mes duplicar.
            inicio = (_date(d.year + 1, 1, 1) if d.month == 12
                      else _date(d.year, d.month + 1, 1))
            pid = FinProgramacao.criar(
                empresa_id=lanc.get('empresa_id'), tipo=tipo_prog,
                descricao=nome_prog, contraparte_nome=nome_prog,
                categoria_id=cat['id'],
                valor_esperado=abs(float(lanc['valor'])),
                dia_vencimento=d.day,
                variavel=(repete == 'variavel'), inicio=inicio,
                contraparte_doc=leitura['doc'] or None,
                centro_custo_id=centro)
            if pid and pid is not True:
                registrar('escrita.criou_programacao', 'financeiro',
                          tabela='fin_programacoes', registro_id=pid,
                          depois={'contraparte': nome_prog,
                                  'valor_esperado': abs(float(lanc['valor'])),
                                  'dia_vencimento': d.day,
                                  'variavel': repete == 'variavel',
                                  'inicio': str(inicio), 'origem': 'extrato'})
                titulo_msg += (
                    f' Programação criada: todo dia {d.day}'
                    + (', valor variável.' if repete == 'variavel'
                       else f', {abs(float(lanc["valor"])):.2f} esperados.'))

    ok = ExtratoLancamento.classificar(lanc_id, cat['id'], centro, mem_id)
    if ok and mem_id:
        # A regra acabou de classificar este lancamento: conta como uso. Sem
        # isto ela aparece como "nunca usada" na tela logo depois de nascer.
        ExtratoMemorizacao.contar_uso(mem_id)
    if ok:
        registrar('escrita.classificou_extrato', 'financeiro',
                  tabela='extrato_lancamentos', registro_id=lanc_id,
                  depois={'categoria': cat['nome'], 'centro_custo_id': centro,
                          'regra_id': mem_id})
        # A FRASE tem UMA cabeca, e o numero que abre e o TOTAL. Antes cada
        # pedaco colava a sua parte no fim e sobrava para a pessoa somar: a
        # tela dizia "3 antigos" (contando o proprio da tela), a frase dizia
        # "2 antigos", e o total — 3 classificacoes — nao aparecia em lugar
        # nenhum. (02/09/2026, achado pelo Anderson.)
        aprovar = f.get('aplicar') == 'aprovar'
        # varridos conta TODOS os que a regra pegou, inclusive este; os outros
        # sao os que a pessoa nao estava vendo.
        antigos = max(0, varridos - 1) if (mem_id and retroagir) else 0
        partes = []
        if antigos and not aprovar:
            partes.append(
                '%d lançamentos classificados em "%s" — este e mais %s.'
                % (antigos + 1, cat['nome'],
                   'um que já estava no extrato' if antigos == 1
                   else '%d que já estavam no extrato' % antigos))
        else:
            partes.append('Classificado em "%s".' % cat['nome'])
            # Regra de APROVACAO nao deixa os antigos prontos: eles nascem
            # preenchidos e marcados. Chamar isso de "classificado" seria
            # mentir no numero.
            if antigos:
                partes.append(
                    'Mais %s preenchido%s e marcado%s "a conferir".'
                    % ('um antigo foi' if antigos == 1
                       else '%d antigos foram' % antigos,
                       '' if antigos == 1 else 's', '' if antigos == 1 else 's'))
        if titulo_msg.strip():
            partes.append(titulo_msg.strip())
        if mem_id:
            partes.append('Regra criada: os próximos %s.'
                          % ('vão pedir sua conferência' if aprovar
                             else 'entram sozinhos'))
            if retroagir and not antigos:
                partes.append('Nenhum lançamento antigo se encaixou.')
        flash(' '.join(partes), 'success')
        if aviso:
            flash(aviso, 'warning')
    else:
        flash('Erro ao classificar.', 'danger')
    return _volta_extrato()


def _regra_do_form(f, lanc):
    """Le do formulario o desenho da regra. Um lugar so, porque a previa e a
    gravacao TEM de ler igual — senao a tela mostra um numero e grava outro."""
    termos = [t.strip() for t in f.getlist('termo') if t and t.strip()]
    if not termos:
        um = (f.get('termos') or '').strip()
        termos = [um] if um else []

    escopo = (f.get('escopo') or 'empresa').strip()
    grupo_raw = (f.get('grupo_id') or '').strip()
    empresas = [int(e) for e in f.getlist('empresa') if str(e).isdigit()]

    valor_raw = (f.get('valor_exato') or '').strip()
    try:
        valor = abs(float(valor_raw.replace('.', '').replace(',', '.'))) if valor_raw else None
    except ValueError:
        valor = None

    return {
        'termos': termos,
        'conta': (f.get('conta') or '').strip() or None,
        'sinal': (f.get('sinal') or '').strip().upper()[:1] or None,
        'valor_exato': valor,
        'escopo': escopo if escopo in ('empresa', 'lista', 'grupo') else 'empresa',
        'grupo_id': int(grupo_raw) if grupo_raw.isdigit() else None,
        'empresas': empresas,
        'aplicar': 'aprovar' if (f.get('aplicar') or '') == 'aprovar' else 'direto',
        'empresa_id': lanc.get('empresa_id') if lanc else None,
    }


@financeiro.route('/financeiro/extrato/<int:lanc_id>/confirmar',
                  methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_confirmar(lanc_id):
    """O humano diz "era isso mesmo". A classificacao e o vinculo com a
    regra ficam — confirmar nao e reclassificar."""
    from models.extrato_lancamento import ExtratoLancamento
    if ExtratoLancamento.confirmar(lanc_id):
        # O aval humano chegou: agora o titulo pode nascer — "quando for
        # aparecendo vai informando no DRE" (Anderson, 27/08).
        lanc = ExtratoLancamento.get(lanc_id)
        resultado = ExtratoLancamento.conciliar_automatico(
            lanc, usuario_id=current_user.id) if lanc else 'pulado'
        registrar('escrita.confirmou_extrato', 'financeiro',
                  tabela='extrato_lancamentos', registro_id=lanc_id,
                  depois={'conferir': False, 'titulo': resultado})
        flash('Confirmado.' + (' Título criado — já está no DRE.'
                               if resultado == 'criou' else
                               (' Baixa registrada no título.'
                                if resultado == 'casou' else '')), 'success')
    else:
        flash('Este lançamento não estava aguardando conferência.', 'warning')
    return _volta_extrato(classif='conferir')


@financeiro.route('/financeiro/extrato/regra/<int:mem_id>/confirmar-todos',
                  methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_confirmar_todos(mem_id):
    """A regra acertou em todos os que deixou esperando."""
    from models.extrato_lancamento import ExtratoLancamento
    # os ids ANTES de limpar a marca: depois nao ha mais como saber quais eram
    alvos = execute_query(
        'SELECT id FROM extrato_lancamentos '
        ' WHERE memorizacao_id = %s AND conferir = 1', (mem_id,), fetch=True) or []
    n = ExtratoLancamento.confirmar_da_regra(mem_id)
    # o aval veio para todos: cada um concilia e entra no DRE
    titulos = {'criou': 0, 'casou': 0, 'pulado': 0, 'erro': 0}
    for a in alvos:
        lanc = ExtratoLancamento.get(a['id'])
        if lanc:
            titulos[ExtratoLancamento.conciliar_automatico(
                lanc, usuario_id=current_user.id)] += 1
    registrar('escrita.confirmou_extrato_lote', 'financeiro',
              tabela='fin_extrato_memorizacoes', registro_id=mem_id,
              depois={'confirmados': n, 'titulos': titulos})
    flash(f'{n} lançamento(s) confirmados.' if n
          else 'Nenhum lançamento desta regra aguardava conferência.',
          'success' if n else 'warning')
    return _volta_extrato(classif='conferir')


@financeiro.route('/financeiro/extrato/<int:lanc_id>/sugestoes')
@permission_required('financeiro.extrato')
def extrato_sugestoes(lanc_id):
    """O que o sistema propoe para este lancamento, com a contagem real."""
    from models.extrato_lancamento import ExtratoLancamento, RegraExtrato
    lanc = ExtratoLancamento.get(lanc_id)
    if not lanc:
        return jsonify(ok=False, msg='Lançamento não encontrado.'), 404
    leitura = ExtratoLancamento.ler_descricao(lanc['descricao'])
    return jsonify(
        ok=True,
        lancamento={
            'id': lanc['id'],
            'descricao': ExtratoLancamento.corrigir_acento(lanc['descricao']),
            'nome': leitura['nome'], 'doc': leitura['doc'],
            'valor': float(lanc['valor']), 'conta': lanc['conta'] or '',
            # o apelido do CADASTRO, nao o nome cru do OFX: a lista ja mostra
            # "Sicredi", e o assistente mostrando "CCPI DO CERRADO DE GO"
            # pareceria outra conta
            'banco': (ExtratoLancamento.contas_mapa()
                      .get(str(lanc['conta'] or ''), {})
                      .get('apelido') or lanc['banco'] or ''),
            'data': lanc['data'].strftime('%d/%m/%Y') if lanc.get('data') else '',
        },
        sugestoes=_sugestoes_do_lote(lanc, RegraExtrato.sugestoes(lanc)))


def _sugestoes_do_lote(lanc, sugestoes):
    """No LOTE, so entra a proposta que casa com TODOS os selecionados.

    Uma regra que pega 2 dos 3 marcados nao e a regra que a pessoa acha que
    esta criando — e o terceiro voltaria amanha para classificar de novo. A
    checagem usa ``RegraExtrato.casa``, o mesmo juiz que aplica, e nao uma
    comparacao de texto escrita aqui.
    """
    from models.extrato_lancamento import RegraExtrato
    bruto = (request.args.get('lote') or '').strip()
    ids = [int(i) for i in bruto.split(',') if i.strip().isdigit()]
    ids = [i for i in ids if i != lanc['id']]
    if not ids:
        return sugestoes

    marks = ','.join(['%s'] * len(ids))
    outros = execute_query(
        f'SELECT id, descricao, valor, conta, empresa_id, categoria_id '
        f'  FROM extrato_lancamentos WHERE id IN ({marks})',
        tuple(ids), fetch=True) or []
    if not outros:
        return sugestoes

    def casa_com_todos(termos):
        # empresa_id VAZIO de proposito: `empresas_da` devolve None e a
        # checagem fica so no TEXTO. O alcance por empresa e pergunta do
        # passo Empresas, mais adiante — nao cabe filtrar por ele aqui.
        falsa = {'id': 0, 'termos': termos, 'ativo': 1, 'escopo': 'empresa',
                 'grupo_id': None, 'empresa_id': None,
                 'conta': None, 'sinal': None, 'valor_exato': None}
        return all(RegraExtrato.casa(falsa, o, None) for o in outros)

    return [s for s in sugestoes if casa_com_todos(s['termos'])]


@financeiro.route('/financeiro/extrato/<int:lanc_id>/titulos-candidatos')
@permission_required('financeiro.extrato')
def extrato_titulos_candidatos(lanc_id):
    """Titulos em aberto que podem ser este pagamento — o melhor primeiro."""
    from models.extrato_lancamento import ExtratoLancamento
    lanc = ExtratoLancamento.get(lanc_id)
    if not lanc:
        return jsonify(ok=False, msg='Lançamento não encontrado.'), 404
    cands = ExtratoLancamento.titulos_candidatos(lanc)
    return jsonify(ok=True, candidatos=[{
        'id': t['id'],
        'quem': t['contraparte_nome'],
        'descricao': (t['descricao'] or '')[:60],
        'categoria': '%s · %s' % (t['categoria_grupo'], t['categoria_nome']),
        'competencia': t['competencia'].strftime('%m/%Y') if t['competencia'] else '',
        'vencimento': t['vencimento'].strftime('%d/%m/%Y') if t['vencimento'] else '',
        'saldo': float(t['valor']) - float(t['valor_baixado'] or 0),
    } for t in cands])


@financeiro.route('/financeiro/extrato/regra/previa', methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_regra_previa():
    """Quantos lancamentos ESTA regra pegaria — antes de gravar nada.

    Devolve tambem quantos ja tem categoria: sao os que a regra NAO vai
    mexer, e some-los ao total seria prometer o que nao vai acontecer.
    """
    from models.extrato_lancamento import ExtratoLancamento, RegraExtrato
    lanc_raw = (request.form.get('lancamento_id') or '').strip()
    lanc = ExtratoLancamento.get(int(lanc_raw)) if lanc_raw.isdigit() else None
    d = _regra_do_form(request.form, lanc)
    if not d['termos']:
        return jsonify(ok=True, n=0, livres=0, ocupados=0, saidas=0,
                       entradas=0, exemplos=[])

    falsa = dict(d, id=0, ativo=1)
    todos = RegraExtrato.preve(falsa, so_sem_categoria=False)
    livres = [a for a in todos if not a['categoria_id']]
    saidas = sum(1 for a in livres if float(a['valor'] or 0) < 0)
    # ANTIGOS e o que a pessoa realmente ve como "os outros": o lancamento
    # aberto no assistente esta em `livres` (ainda sem categoria) e chama-lo de
    # antigo foi o que fez a tela dizer 3 e a frase dizer 2 (02/09/2026).
    # A conta sai daqui, e nao de um "-1" no JavaScript: quando o lancamento
    # aberto JA tem categoria ele nem esta na lista, e subtrair mentiria.
    atual = lanc['id'] if lanc else None
    antigos = sum(1 for a in livres if a['id'] != atual)
    return jsonify(
        ok=True, n=len(livres), livres=len(livres), antigos=antigos,
        ocupados=len(todos) - len(livres),
        saidas=saidas, entradas=len(livres) - saidas,
        exemplos=[{'descricao': ExtratoLancamento.corrigir_acento(a['descricao'])[:70],
                   'valor': float(a['valor']),
                   'data': a['data'].strftime('%d/%m/%Y') if a.get('data') else ''}
                  for a in livres[:6]])


@financeiro.route('/financeiro/extrato/lote', methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_lote():
    """Classifica varios de uma vez — pelo MESMO assistente do de um.

    Ate 02/09/2026 o lote tinha um modal proprio, menor de proposito:
    categoria, centro e uma caixinha "criar regra junto". Na pratica virou
    uma segunda tela com regras diferentes das do assistente — sem escolher
    o trecho, sem condicoes, sem titulo. O Anderson pediu identico, e
    identico e a palavra certa: a tela do lote passou a ser o assistente,
    com os mesmos passos e o mesmo JS.

    O que o lote NAO pode espelhar, e por que:

    casar titulo  e 1 para 1 — um titulo existente casa com UM lancamento.
                  No lote so existem "criar para cada um" e "sem titulo";
    programacao   nasce UMA, do lancamento mais recente da selecao. Uma por
                  lancamento faria N contas mensais da mesma coisa.

    A ordem aqui e de proposito: cria a regra, classifica os SELECIONADOS e
    so entao retroage. Assim o retroativo pega exatamente os OUTROS — os
    selecionados ja tem categoria e o ``so_sem_categoria`` os pula — e o
    numero que aparece na frase e o certo, sem subtracao adivinhada.
    """
    from models.extrato_lancamento import ExtratoLancamento, RegraExtrato
    f = request.form
    ids = [int(i) for i in f.getlist('lanc') if str(i).isdigit()]
    if not ids:
        flash('Nenhum lançamento selecionado.', 'warning')
        return _volta_extrato()

    marks = ','.join(['%s'] * len(ids))
    lancs = execute_query(
        f'SELECT id, empresa_id, conta, valor, descricao, categoria_id, data '
        f'  FROM extrato_lancamentos WHERE id IN ({marks}) ORDER BY data DESC',
        tuple(ids), fetch=True) or []

    livres = [l for l in lancs if not l['categoria_id']]
    pulados = len(lancs) - len(livres)
    if not livres:
        flash('Todos os selecionados já têm categoria — o lote não '
              'sobrescreve decisão já tomada.', 'warning')
        return _volta_extrato()

    # Credito e receita, debito e despesa: uma categoria nao serve aos dois.
    sinais = {('R' if float(l['valor']) >= 0 else 'P') for l in livres}
    if len(sinais) > 1:
        flash('A seleção mistura entradas e saídas — classifique cada '
              'sentido de uma vez, porque uma categoria não serve aos dois.',
              'danger')
        return _volta_extrato()

    cat_raw = (f.get('categoria_id') or '').strip()
    cat = next((c for c in FinCategoria.listar() if str(c['id']) == cat_raw), None)
    if not cat:
        flash('Escolha a categoria.', 'danger')
        return _volta_extrato()
    # A MESMA regra do de um lancamento, e nao uma parecida: TRANSFERENCIA
    # vale nos dois sentidos (e dinheiro seu andando de conta) e INVESTIMENTO
    # vale na saida. Escrever aqui uma versao mais curta fez o lote recusar as
    # 41 categorias de investimento que a propria tela oferecia (02/09/2026).
    esperado = next(iter(sinais))
    aceitos = {esperado, 'T'} | ({'I'} if esperado == 'P' else set())
    if cat['tipo'] not in aceitos:
        flash(('A seleção é de CRÉDITOS — escolha receita ou transferência.'
               if esperado == 'R' else
               'A seleção é de DÉBITOS — escolha despesa, investimento '
               'ou transferência.'), 'danger')
        return _volta_extrato()

    centro_raw = (f.get('centro_custo_id') or '').strip()
    centro = int(centro_raw) if centro_raw.isdigit() else None

    #: O REPRESENTANTE da selecao: o mais recente. E dele que saem o trecho
    #: proposto, o dia da programacao e o valor esperado — o mes que acabou de
    #: acontecer diz mais sobre o proximo do que um de seis meses atras.
    rep_raw = (f.get('rep_id') or '').strip()
    rep = next((l for l in livres if str(l['id']) == rep_raw), livres[0])

    # ---- A REGRA (uma so, para a selecao inteira) -----------------------
    virar_regra = f.get('memorizar') == 'on'
    retroagir = f.get('retroagir') == 'on'
    mem_id, varridos, aviso = None, 0, None

    if virar_regra:
        d = _regra_do_form(f, rep)
        if not d['termos'] or len(' '.join(d['termos'])) < 4:
            flash('A regra precisa de pelo menos um trecho com 4 letras.', 'danger')
            return _volta_extrato()
        if d['escopo'] == 'lista' and not d['empresas']:
            flash('Escolha ao menos uma empresa para a regra.', 'danger')
            return _volta_extrato()
        if d['escopo'] == 'grupo' and not d['grupo_id']:
            flash('Escolha o grupo de empresas.', 'danger')
            return _volta_extrato()

        mem_id = RegraExtrato.criar(
            d['termos'], cat['id'], centro,
            empresa_id=rep.get('empresa_id'),
            conta=d['conta'], sinal=d['sinal'], valor_exato=d['valor_exato'],
            escopo=d['escopo'], grupo_id=d['grupo_id'], empresas=d['empresas'],
            aplicar=d['aplicar'], criado_por=current_user.id)

        if mem_id:
            registrar('escrita.criou_regra_extrato', 'financeiro',
                      tabela='fin_extrato_memorizacoes', registro_id=mem_id,
                      depois={'termos': d['termos'], 'categoria': cat['nome'],
                              'centro_custo_id': centro, 'conta': d['conta'],
                              'sinal': d['sinal'], 'valor_exato': d['valor_exato'],
                              'escopo': d['escopo'], 'grupo_id': d['grupo_id'],
                              'empresas': sorted(d['empresas']),
                              'aplicar': d['aplicar'], 'retroagiu': retroagir,
                              'origem': 'lote', 'selecionados': len(livres)})
        else:
            aviso = ('Já existe uma regra ativa idêntica — com os mesmos '
                     'trechos, as mesmas condições e o mesmo alcance. Os '
                     'lançamentos foram classificados, mas nenhuma regra '
                     'nova nasceu.')

    # ---- CLASSIFICAR os selecionados ------------------------------------
    for l in livres:
        ExtratoLancamento.classificar(l['id'], cat['id'], centro, mem_id)
    if mem_id:
        RegraExtrato.contar_uso(mem_id, len(livres))

    # ---- RETROAGIR: agora pega exatamente os OUTROS ---------------------
    if mem_id and retroagir:
        varridos = RegraExtrato.aplicar_retroativa(mem_id)

    # ---- O TITULO, um por lancamento ------------------------------------
    # "casar" nao existe aqui: casar e 1 para 1. Sobram criar e nao.
    titulo_acao = (f.get('titulo_acao') or 'nao').strip()
    criados, falhou_titulo = 0, 0
    if titulo_acao == 'criar' and cat['tipo'] != 'T':
        for l in livres:
            leitura = ExtratoLancamento.ler_descricao(l['descricao'])
            okc, motivo, tid = ExtratoLancamento.conciliar(
                l, criar={'competencia': l['data'].replace(day=1),
                          'contraparte_nome': leitura['nome'] or cat['nome'],
                          'contraparte_doc': leitura['doc'],
                          'categoria_id': cat['id'],
                          'centro_custo_id': centro},
                usuario_id=current_user.id)
            if okc:
                criados += 1
                registrar('escrita.conciliou_extrato', 'financeiro',
                          tabela='fin_titulos', registro_id=tid,
                          depois={'lancamento_id': l['id'], 'acao': 'criar',
                                  'valor': float(l['valor']), 'origem': 'lote'})
            else:
                falhou_titulo += 1

    # ---- "ISSO SE REPETE?" — UMA programacao, do representante ----------
    repete = (f.get('repete') or '').strip()
    prog_msg = ''
    if criados and repete in ('fixa', 'variavel') and virar_regra:
        from datetime import date as _date
        leitura = ExtratoLancamento.ler_descricao(rep['descricao'])
        nome_prog = (leitura['nome'] or cat['nome'])[:255]
        tipo_prog = 'R' if rep['valor'] >= 0 else 'P'
        dup = execute_query(
            'SELECT id FROM fin_programacoes '
            ' WHERE ativo = 1 AND empresa_id = %s AND tipo = %s '
            '   AND contraparte_nome = %s',
            (rep.get('empresa_id'), tipo_prog, nome_prog),
            fetch=True, fetch_one=True)
        if dup:
            flash(f'Já existe programação ativa para "{nome_prog}" — '
                  'nenhuma nova foi criada.', 'warning')
        else:
            dt = rep['data']
            inicio = (_date(dt.year + 1, 1, 1) if dt.month == 12
                      else _date(dt.year, dt.month + 1, 1))
            pid = FinProgramacao.criar(
                empresa_id=rep.get('empresa_id'), tipo=tipo_prog,
                descricao=nome_prog, contraparte_nome=nome_prog,
                categoria_id=cat['id'],
                valor_esperado=abs(float(rep['valor'])),
                dia_vencimento=dt.day,
                variavel=(repete == 'variavel'), inicio=inicio,
                contraparte_doc=leitura['doc'] or None,
                centro_custo_id=centro)
            if pid and pid is not True:
                registrar('escrita.criou_programacao', 'financeiro',
                          tabela='fin_programacoes', registro_id=pid,
                          depois={'contraparte': nome_prog,
                                  'valor_esperado': abs(float(rep['valor'])),
                                  'dia_vencimento': dt.day,
                                  'variavel': repete == 'variavel',
                                  'inicio': str(inicio), 'origem': 'lote'})
                prog_msg = (f'Programação criada: todo dia {dt.day}'
                            + (', valor variável.' if repete == 'variavel'
                               else f', {abs(float(rep["valor"])):.2f} esperados.'))

    registrar('escrita.classificou_extrato_lote', 'financeiro',
              tabela='extrato_lancamentos',
              depois={'quantos': len(livres), 'ids': [l['id'] for l in livres],
                      'categoria': cat['nome'], 'centro_custo_id': centro,
                      'regra_id': mem_id, 'titulos_criados': criados,
                      'retroagiu': bool(mem_id and retroagir),
                      'antigos': varridos,
                      'pulados_ja_classificados': pulados})

    # ---- A FRASE, na mesma forma do assistente de um --------------------
    aprovar = f.get('aplicar') == 'aprovar'
    partes = []
    if varridos and not aprovar:
        partes.append(
            '%d lançamentos classificados em "%s" — os %d que você marcou e '
            'mais %s.' % (len(livres) + varridos, cat['nome'], len(livres),
                          'um que já estava no extrato' if varridos == 1
                          else '%d que já estavam no extrato' % varridos))
    else:
        partes.append('%d lançamento%s classificado%s em "%s".'
                      % (len(livres), '' if len(livres) == 1 else 's',
                         '' if len(livres) == 1 else 's', cat['nome']))
        if varridos:
            partes.append('Mais %s preenchido%s e marcado%s "a conferir".'
                          % ('um antigo foi' if varridos == 1
                             else '%d antigos foram' % varridos,
                             '' if varridos == 1 else 's',
                             '' if varridos == 1 else 's'))
    if criados:
        partes.append('%d título%s criado%s já quitado%s.'
                      % (criados, '' if criados == 1 else 's',
                         '' if criados == 1 else 's',
                         '' if criados == 1 else 's'))
    if falhou_titulo:
        partes.append('%d não deu para conciliar.' % falhou_titulo)
    if prog_msg:
        partes.append(prog_msg)
    if mem_id:
        partes.append('Regra criada: os próximos %s.'
                      % ('vão pedir sua conferência' if aprovar
                         else 'entram sozinhos'))
        if retroagir and not varridos:
            partes.append('Nenhum lançamento antigo se encaixou.')
    if pulados:
        partes.append('%d já tinha%s categoria e ficou como estava%s.'
                      % (pulados, '' if pulados == 1 else 'm',
                         '' if pulados == 1 else 'm'))
    flash(' '.join(partes), 'success')
    if aviso:
        flash(aviso, 'warning')
    return _volta_extrato()

@financeiro.route('/financeiro/extrato/lote/titulos', methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_lote_titulos():
    """Gera (ou casa) os titulos dos lancamentos JA CLASSIFICADOS.

    E o que faz o classificado-por-regra aparecer no DRE: regra nao cria
    titulo, e o DRE le titulos. A competencia de cada um e o mes do proprio
    lancamento.
    """
    from models.extrato_lancamento import ExtratoLancamento
    ids = [int(i) for i in request.form.getlist('lanc') if str(i).isdigit()]
    if not ids:
        flash('Nenhum lançamento selecionado.', 'warning')
        return _volta_extrato()

    marks = ','.join(['%s'] * len(ids))
    lancs = execute_query(
        f"""SELECT e.*, c.tipo AS cat_tipo, c.nome AS cat_nome
              FROM extrato_lancamentos e
              JOIN fin_categorias c ON c.id = e.categoria_id
             WHERE e.id IN ({marks})""", tuple(ids), fetch=True) or []

    criados, casados, pulados, erros = 0, 0, 0, []
    for l in lancs:
        if l['cat_tipo'] == 'T':
            pulados += 1              # transferencia nao e obrigacao
            continue
        ja = execute_query(
            'SELECT 1 FROM fin_titulo_baixas WHERE lancamento_id = %s '
            'UNION SELECT 1 FROM fin_titulos WHERE chave_idem = %s LIMIT 1',
            (l['id'], 'extrato:%s' % l['id']), fetch=True, fetch_one=True)
        if ja:
            pulados += 1              # ja amarrado: idempotente
            continue

        # CASA-PRIMEIRO: se uma programacao ja gerou o titulo do mes, a baixa
        # entra nele — criar outro duplicaria a conta no DRE.
        leitura = ExtratoLancamento.ler_descricao(l['descricao'])
        alvo = None
        for cand in ExtratoLancamento.titulos_candidatos(l, limite=3):
            saldo = float(cand['valor']) - float(cand['valor_baixado'] or 0)
            if (leitura['doc'] and cand['contraparte_doc'] == leitura['doc']
                    and abs(saldo - abs(float(l['valor']))) <= 0.01):
                alvo = cand['id']
                break

        if alvo:
            ok, motivo, tid = ExtratoLancamento.conciliar(
                l, titulo_id=alvo, usuario_id=current_user.id)
            if ok:
                casados += 1
            else:
                erros.append('#%s: %s' % (l['id'], motivo))
            continue

        ok, motivo, tid = ExtratoLancamento.conciliar(
            l, criar={'competencia': l['data'].replace(day=1),
                      'contraparte_nome': leitura['nome'] or l['cat_nome'],
                      'contraparte_doc': leitura['doc'],
                      'categoria_id': l['categoria_id'],
                      'centro_custo_id': l['centro_custo_id']},
            usuario_id=current_user.id)
        if ok:
            criados += 1
        else:
            erros.append('#%s: %s' % (l['id'], motivo))

    registrar('escrita.gerou_titulos_lote', 'financeiro',
              tabela='fin_titulos',
              depois={'ids': ids, 'criados': criados, 'casados': casados,
                      'pulados': pulados, 'erros': erros[:5]})

    partes = []
    if criados:
        partes.append(f'{criados} título(s) criados já quitados')
    if casados:
        partes.append(f'{casados} casado(s) com título existente')
    if pulados:
        partes.append(f'{pulados} pulado(s) — transferência ou já amarrado')
    if erros:
        partes.append(f'{len(erros)} com erro: {"; ".join(erros[:3])}')
    flash(('. '.join(partes) + '. O DRE já reflete.') if (criados or casados)
          else ('. '.join(partes) or 'Nada a fazer.'),
          'success' if (criados or casados) else 'warning')
    return _volta_extrato(classif='sim')


@financeiro.route('/financeiro/extrato/memorizacoes')
@permission_required('financeiro.extrato')
def extrato_memorizacoes():
    from models.extrato_lancamento import ExtratoLancamento, RegraExtrato
    regras = RegraExtrato.listar()
    _emps, _sel, mapa = _empresas_ctx()

    # Duas contagens por regra, e elas contam historias diferentes:
    #   presos    o que ELA classificou — e o que volta se for desfeita
    #   pendentes o que casa e AINDA nao entrou — o que o botao de retroagir
    #             vai pegar, escrito no proprio botao
    presos, pendentes, regra_empresas = {}, {}, {}
    universo = RegraExtrato.universo(so_sem_categoria=False) if regras else []
    for r in regras:
        presos[r['id']] = sum(1 for l in universo
                              if l.get('memorizacao_id') == r['id'])
        pendentes[r['id']] = len(RegraExtrato.preve(
            r, so_sem_categoria=True, universo=universo))
        if r.get('escopo') == 'lista':
            regra_empresas[r['id']] = [
                mapa.get(e) or ('empresa %s' % e)
                for e in sorted(RegraExtrato.empresas_da(r) or [])]

    return render_template('financeiro/extrato_memorizacoes.html',
                           memorizacoes=regras,
                           presos=presos, pendentes=pendentes,
                           regra_empresas=regra_empresas,
                           emp_mapa=mapa,
                           categorias=FinCategoria.listar(),
                           contas_cad=ExtratoLancamento.contas_mapa())


@financeiro.route('/financeiro/extrato/regra/<int:mem_id>/editar',
                  methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_regra_editar(mem_id):
    """Edita a regra. Os ja classificados vao pelo caminho que o USUARIO
    escolher: ficam como estao, ou a regra nova decide de novo cada um."""
    from models.extrato_lancamento import RegraExtrato
    f = request.form
    volta = redirect(url_for('financeiro.extrato_memorizacoes'))
    r = RegraExtrato.get(mem_id)
    if not r or not r.get('ativo'):
        flash('Regra não encontrada (ou desativada).', 'danger')
        return volta

    termos = [t.strip() for t in f.getlist('termo') if t and t.strip()]
    cat_raw = (f.get('categoria_id') or '').strip()
    cats = FinCategoria.listar()
    cat = next((c for c in cats if str(c['id']) == cat_raw), None)
    if not termos or not cat:
        flash('A regra precisa de ao menos um trecho e uma categoria.', 'danger')
        return volta
    # trocar P por R viraria o sentido da regra — isso e criar OUTRA regra
    velha = next((c for c in cats if c['id'] == r.get('categoria_id')), None)
    if velha and cat['tipo'] != velha['tipo']:
        flash('A nova categoria é de outro tipo (entrada × saída) — '
              'para virar o sentido, crie outra regra.', 'danger')
        return volta

    conta = (f.get('conta') or '').strip()[:60] or None
    aplicar = (f.get('aplicar') or '').strip()
    presos = execute_query(
        'SELECT COUNT(*) n FROM extrato_lancamentos WHERE memorizacao_id = %s',
        (mem_id,), fetch=True, fetch_one=True)['n']
    antigos = (f.get('antigos') or '').strip()
    if presos and antigos not in ('ficam', 'reprocessa'):
        flash(f'Escolha o que fazer com os {presos} que a regra já '
              'classificou.', 'danger')
        return volta

    antes = {'termos': r.get('termos'), 'conta': r.get('conta'),
             'aplicar': r.get('aplicar'), 'categoria_id': r.get('categoria_id')}
    ok, motivo, _ = RegraExtrato.editar(
        mem_id, termos=termos, categoria_id=cat['id'], conta=conta,
        aplicar=aplicar or None)
    if not ok:
        flash(f'Não deu para editar: {motivo}', 'danger')
        return volta

    devolvidos = reclassificados = 0
    if presos and antigos == 'reprocessa':
        devolvidos, reclassificados = RegraExtrato.reprocessar(mem_id)

    registrar('escrita.editou_regra_extrato', 'financeiro',
              tabela='fin_extrato_memorizacoes', registro_id=mem_id,
              antes=antes,
              depois={'termos': termos, 'conta': conta, 'aplicar': aplicar,
                      'categoria_id': cat['id'],
                      'antigos': antigos or 'nada-preso',
                      'devolvidos': devolvidos,
                      'reclassificados': reclassificados})

    msg = 'Regra atualizada.'
    if antigos == 'reprocessa':
        msg += (f' {devolvidos} devolvido(s), {reclassificados} '
                'reclassificado(s) pela regra nova')
        if devolvidos > reclassificados:
            msg += (f' — {devolvidos - reclassificados} não casam mais e '
                    'voltaram para Sem categoria')
        msg += '.'
    elif presos:
        msg += f' Os {presos} já classificados ficaram como estavam.'
    flash(msg, 'success')
    return volta


@financeiro.route('/financeiro/extrato/memorizacoes/<int:mem_id>/retroagir',
                  methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_regra_retroagir(mem_id):
    """Aplica a regra nos lancamentos antigos que ainda estao sem categoria.

    Existe porque criar a regra "so daqui para frente" e o caminho seguro, e
    depois a pessoa quer os antigos tambem. Sem este botao ela teria de
    apagar a regra e refazer.
    """
    from models.extrato_lancamento import RegraExtrato
    r = RegraExtrato.get(mem_id)
    if not r:
        flash('Regra não encontrada.', 'danger')
        return redirect(url_for('financeiro.extrato_memorizacoes'))
    if not r['ativo']:
        flash('Regra desativada — reative antes de aplicar nos antigos.', 'warning')
        return redirect(url_for('financeiro.extrato_memorizacoes'))

    n = RegraExtrato.aplicar_retroativa(mem_id)
    registrar('escrita.aplicou_regra_retroativa', 'financeiro',
              tabela='fin_extrato_memorizacoes', registro_id=mem_id,
              depois={'lancamentos_classificados': n,
                      'termos': r['termos'], 'aplicar': r['aplicar']})
    if n:
        flash(f'{n} lançamento(s) antigo(s) classificado(s)' +
              (' e marcados para conferência.' if r['aplicar'] == 'aprovar' else '.'),
              'success')
    else:
        flash('Nenhum lançamento antigo se encaixou nesta regra.', 'warning')
    return redirect(url_for('financeiro.extrato_memorizacoes'))


@financeiro.route('/financeiro/extrato/memorizacoes/<int:mem_id>/alternar',
                  methods=['POST'])
@permission_required('financeiro.extrato')
def extrato_memorizacao_alternar(mem_id):
    from models.extrato_lancamento import ExtratoMemorizacao
    m = ExtratoMemorizacao.get(mem_id)
    if not m:
        flash('Memorização não encontrada.', 'danger')
    else:
        ligar = not m['ativo']
        # Ao DESLIGAR, a pessoa escolhe o que acontece com o passado. Ao
        # religar nao ha o que perguntar: a regra volta a valer para o que
        # vier, e o retroativo e um gesto proprio.
        devolver = (not ligar) and request.form.get('devolver') == 'on'
        if ligar:
            ExtratoMemorizacao.set_ativa(mem_id, True)
            devolvidos = 0
        else:
            devolvidos = ExtratoMemorizacao.desativar(mem_id, devolver=devolver)
        registrar('escrita.alternou_regra_extrato', 'financeiro',
                  tabela='fin_extrato_memorizacoes', registro_id=mem_id,
                  antes={'ativo': bool(m['ativo'])},
                  depois={'ativo': ligar, 'devolveu': devolver,
                          'lancamentos_devolvidos': devolvidos})
        if ligar:
            flash('Regra reativada — ela volta a valer para o que vier.', 'success')
        elif devolver:
            flash(f'Regra desativada e {devolvidos} lançamento(s) voltaram para '
                  '"sem categoria". O que foi classificado à mão não foi tocado.',
                  'success')
        else:
            flash('Regra desativada — ela para de valer daqui para frente, e o '
                  'que já classificou continua como está.', 'success')
    return redirect(url_for('financeiro.extrato_memorizacoes'))


# =======================================================================
# Destaques da HOME do Financeiro — mesmo padrão da home do Fiscal:
# a página abre instantânea e busca isto por fetch (home_destaques.js);
# cache de 60s; card que falhar é OMITIDO (melhor faltar que mentir).
# =======================================================================
import threading
import time as _time
from datetime import datetime as _dt, timezone as _tz

_FIN_HOME_CACHE: dict = {}
_FIN_HOME_LOCK = threading.Lock()
_FIN_HOME_TTL_S = 60


def _fin_brl(v):
    s = f'{float(v or 0):,.2f}'
    return 'R$ ' + s.replace(',', 'X').replace('.', ',').replace('X', '.')


def _fin_home_payload():
    from models.fin_titulo import FinTitulo, FinCategoria, FinFluxo
    from utils.db_helper import execute_query
    import logging
    log = logging.getLogger(__name__)
    cards, counters = [], {}

    def card(fn):
        try:
            c = fn()
            if c:
                cards.append(c)
        except Exception:
            log.exception('[fin-home] card falhou')

    resumo = None
    try:
        resumo = FinTitulo.resumo()
    except Exception:
        log.exception('[fin-home] resumo falhou')

    def _c_receber():
        if resumo is None:
            return None
        r = resumo['R']
        return {'icone': 'fa-hand-holding-dollar', 'titulo': 'A receber (R$)',
                'valor': int(round(float(r['em_aberto'] or 0))),
                'apoio': f"{r['qtd'] or 0} título(s) em aberto · "
                         f"{_fin_brl(r['vencido'])} vencidos",
                'trend': {'tipo': 'neutro', 'rotulo': 'aberto'}}

    def _c_pagar():
        if resumo is None:
            return None
        p = resumo['P']
        return {'icone': 'fa-money-bill-transfer', 'titulo': 'A pagar (R$)',
                'valor': int(round(float(p['em_aberto'] or 0))),
                'apoio': f"{p['qtd'] or 0} título(s) em aberto · "
                         f"{_fin_brl(p['vencido'])} vencidos",
                'trend': {'tipo': 'neutro', 'rotulo': 'aberto'}}

    def _c_saldo():
        sd = FinFluxo.saldo_vigente()
        if not sd:
            return None                      # sem saldo, sem card — não inventa
        return {'icone': 'fa-vault', 'titulo': 'Saldo real (R$)',
                'valor': int(round(float(sd['valor'] or 0))),
                'apoio': ('informado ' + ('à mão' if sd['origem'] == 'manual'
                          else 'pelo extrato') +
                          f" em {sd['data'].strftime('%d/%m')}"),
                'trend': {'tipo': 'neutro', 'rotulo': 'caixa'}}

    def _c_extrato():
        row = execute_query(
            """SELECT COUNT(*) AS n FROM extrato_lancamentos
                WHERE data >= CURDATE() - INTERVAL 30 DAY""",
            fetch=True, fetch_one=True) or {}
        dias = execute_query(
            """SELECT data, COUNT(*) AS n FROM extrato_lancamentos
                WHERE data >= CURDATE() - INTERVAL 13 DAY
                GROUP BY data""", fetch=True) or []
        mapa = {r['data'].isoformat(): int(r['n']) for r in dias}
        serie = []
        for i in range(13, -1, -1):
            d = (date.today() - __import__('datetime').timedelta(days=i)).isoformat()
            serie.append(mapa.get(d, 0))
        c = {'icone': 'fa-university', 'titulo': 'Extrato: 30 dias',
             'valor': int(row.get('n') or 0),
             'apoio': 'lançamentos importados do banco',
             'trend': {'tipo': 'neutro', 'rotulo': '30d'}}
        if any(serie):                       # sparkline só com série real
            c['spark'] = serie
            c['spark_tipo'] = 'barra'
        return c

    def _c_resultado():
        row = execute_query(
            """SELECT COUNT(*) AS n,
                      COALESCE(SUM(CASE WHEN c.tipo = 'R' THEN t.valor
                                        ELSE -t.valor END), 0) AS res
                 FROM fin_titulos t
                 JOIN fin_categorias c ON c.id = t.categoria_id
                WHERE t.status <> 'cancelado'
                  AND t.competencia >= (CURDATE() - INTERVAL (DAY(CURDATE())-1) DAY)""",
            fetch=True, fetch_one=True) or {}
        if not row.get('n'):
            return None                      # mês sem lançamento não vira card
        return {'icone': 'fa-chart-pie', 'titulo': 'Resultado do mês (R$)',
                'valor': int(round(float(row.get('res') or 0))),
                'apoio': f"competência {date.today().strftime('%m/%Y')} · "
                         f"{row['n']} título(s) — ver DRE",
                'trend': {'tipo': 'neutro', 'rotulo': 'competência'}}

    card(_c_receber)
    card(_c_pagar)
    card(_c_saldo)
    card(_c_extrato)
    card(_c_resultado)

    def conta(chave, sql):
        try:
            r = execute_query(sql, fetch=True, fetch_one=True) or {}
            counters[chave] = int(r.get('n') or 0)
        except Exception:
            log.exception('[fin-home] contador %s falhou', chave)

    conta('titulos', "SELECT COUNT(*) n FROM fin_titulos WHERE status IN ('aberto','parcial')")
    conta('extrato', 'SELECT COUNT(*) n FROM extrato_lancamentos')
    conta('fluxo', "SELECT COUNT(*) n FROM fin_titulos WHERE status IN ('aberto','parcial') "
                   'AND vencimento <= CURDATE() + INTERVAL 7 DAY')
    conta('dre', "SELECT COUNT(DISTINCT MONTH(competencia)) n FROM fin_titulos "
                 "WHERE status <> 'cancelado' AND YEAR(competencia) = YEAR(CURDATE())")
    conta('categorias', 'SELECT COUNT(*) n FROM fin_categorias WHERE ativo = 1')
    conta('programacoes', 'SELECT COUNT(*) n FROM fin_programacoes WHERE ativo = 1')

    return {'cards': cards, 'counters': counters,
            'gerado_em_ms': int(_time.time() * 1000)}


@financeiro.route('/financeiro/api/home-destaques')
@permission_required('financeiro.index')
def api_home_destaques():
    """Números da home (carrossel + contadores). Cache de 60s por usuário."""
    uid = getattr(current_user, 'id', None)
    agora = _dt.now(_tz.utc).timestamp()
    with _FIN_HOME_LOCK:
        hit = _FIN_HOME_CACHE.get(uid)
        if hit and (agora - hit[0]) < _FIN_HOME_TTL_S:
            return jsonify(hit[1])
    payload = _fin_home_payload()
    with _FIN_HOME_LOCK:
        _FIN_HOME_CACHE[uid] = (agora, payload)
    return jsonify(payload)


# =======================================================================
# Programações (E2.3) — contas que se repetem todo mês
# =======================================================================
@financeiro.route('/financeiro/programacoes')
@permission_required('financeiro.programacoes')
def programacoes():
    from models.fin_titulo import FinProgramacao, FinCentroCusto
    emps, sel, mapa = _empresas_ctx()
    return render_template('financeiro/programacoes.html',
                           programacoes=FinProgramacao.listar(empresa_ids=sel),
                           categorias=FinCategoria.listar(),
                           centros=FinCentroCusto.listar(),
                           fin_empresas=emps, sel_empresas=sel, emp_mapa=mapa,
                           hoje=date.today())


@financeiro.route('/financeiro/programacoes/nova', methods=['POST'])
@permission_required('financeiro.programacoes')
def programacao_nova():
    from models.fin_titulo import FinProgramacao
    from models.fin_empresa import FinEmpresa
    f = request.form
    empresa_raw = (f.get('empresa_id') or '').strip()
    dia_raw = (f.get('dia_vencimento') or '').strip()
    erro = None
    valor = None
    if not empresa_raw.isdigit() or int(empresa_raw) not in FinEmpresa.ids_validos():
        erro = 'Escolha a empresa.'
    elif f.get('tipo') not in ('R', 'P'):
        erro = 'Escolha receber ou pagar.'
    elif not (f.get('descricao') or '').strip() or not (f.get('contraparte_nome') or '').strip():
        erro = 'Preencha descrição e contraparte.'
    elif not (f.get('categoria_id') or '').isdigit():
        erro = 'Escolha a categoria.'
    elif not dia_raw.isdigit() or not 1 <= int(dia_raw) <= 31:
        erro = 'Dia de vencimento entre 1 e 31.'
    else:
        try:
            valor = _dec_form(f.get('valor_esperado'))
            if valor <= 0:
                erro = 'Valor esperado deve ser maior que zero.'
        except InvalidOperation:
            erro = 'Valor esperado inválido.'
    if erro:
        flash(erro, 'danger')
        return redirect(url_for('financeiro.programacoes'))
    centro_raw = (f.get('centro_custo_id') or '').strip()
    pid = FinProgramacao.criar(
        empresa_id=int(empresa_raw), tipo=f.get('tipo'),
        descricao=f.get('descricao').strip(),
        contraparte_nome=f.get('contraparte_nome').strip(),
        contraparte_doc=(f.get('contraparte_doc') or '').strip() or None,
        categoria_id=int(f.get('categoria_id')),
        centro_custo_id=int(centro_raw) if centro_raw.isdigit() else None,
        valor_esperado=valor, dia_vencimento=int(dia_raw),
        variavel=f.get('natureza') == 'variavel')
    if pid:
        registrar('escrita.criou_programacao', 'financeiro',
                  tabela='fin_programacoes', registro_id=pid,
                  depois={'descricao': f.get('descricao').strip(),
                          'valor': str(valor), 'dia': int(dia_raw),
                          'variavel': f.get('natureza') == 'variavel'})
        flash('Programação criada — use "Gerar títulos do mês" para lançar.',
              'success')
    else:
        flash('Erro ao criar a programação.', 'danger')
    return redirect(url_for('financeiro.programacoes'))


@financeiro.route('/financeiro/programacoes/<int:pid>/atualizar', methods=['POST'])
@permission_required('financeiro.programacoes')
def programacao_atualizar(pid):
    from models.fin_titulo import FinProgramacao
    p = FinProgramacao.get(pid)
    if not p:
        flash('Programação não encontrada.', 'danger')
        return redirect(url_for('financeiro.programacoes'))
    acao = request.form.get('acao')
    if acao == 'ativo':
        FinProgramacao.atualizar(pid, ativo=not p['ativo'])
        flash('Programação reativada.' if not p['ativo']
              else 'Programação pausada — não gera mais títulos.', 'success')
    elif acao == 'editar':
        try:
            valor = _dec_form(request.form.get('valor_esperado'))
        except InvalidOperation:
            flash('Valor inválido.', 'danger')
            return redirect(url_for('financeiro.programacoes'))
        dia_raw = (request.form.get('dia_vencimento') or '').strip()
        if not dia_raw.isdigit() or not 1 <= int(dia_raw) <= 31:
            flash('Dia de vencimento entre 1 e 31.', 'danger')
            return redirect(url_for('financeiro.programacoes'))
        FinProgramacao.atualizar(
            pid, valor_esperado=valor, dia_vencimento=int(dia_raw),
            variavel=request.form.get('natureza') == 'variavel')
        flash('Programação atualizada.', 'success')
    registrar('escrita.alterou_programacao', 'financeiro',
              tabela='fin_programacoes', registro_id=pid,
              antes={'valor_esperado': str(p['valor_esperado']),
                     'ativo': bool(p['ativo'])},
              depois={'acao': acao})
    return redirect(url_for('financeiro.programacoes'))


@financeiro.route('/financeiro/programacoes/gerar', methods=['POST'])
@permission_required('financeiro.programacoes')
def programacoes_gerar():
    from models.fin_titulo import FinProgramacao
    comp = (request.form.get('competencia') or '').strip()   # YYYY-MM
    if not re.match(r'^\d{4}-\d{2}$', comp):
        flash('Informe o mês para gerar.', 'danger')
        return redirect(url_for('financeiro.programacoes'))
    ano, mes = int(comp[:4]), int(comp[5:7])
    gerados, ja_existiam, nomes = FinProgramacao.gerar_mes(ano, mes)
    registrar('escrita.gerou_programacoes', 'financeiro', tabela='fin_titulos',
              depois={'competencia': comp, 'gerados': gerados,
                      'ja_existiam': ja_existiam})
    if gerados:
        flash(f'{gerados} título(s) de {mes:02d}/{ano} gerados '
              f'({", ".join(nomes[:6])}{"..." if len(nomes) > 6 else ""}); '
              f'{ja_existiam} já existiam.', 'success')
    else:
        flash(f'Nada novo para {mes:02d}/{ano}: {ja_existiam} título(s) já '
              'existiam (gerar de novo não duplica).', 'warning')
    return redirect(url_for('financeiro.programacoes'))


# =======================================================================
# Minhas Empresas (E2.1) — quem participa do financeiro multiempresa
# =======================================================================
@financeiro.route('/financeiro/empresas')
@permission_required('financeiro.empresas')
def empresas():
    from models.fin_empresa import FinEmpresa
    return render_template('financeiro/empresas.html',
                           empresas=FinEmpresa.listar(apenas_ativas=False))


@financeiro.route('/financeiro/empresas/buscar')
@permission_required('financeiro.empresas')
def empresas_buscar():
    from models.fin_empresa import FinEmpresa
    from utils.db_helper import execute_query as _q
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify([])
    dig = ''.join(ch for ch in q if ch.isdigit())
    ja = FinEmpresa.ids_validos() | {e['cliente_id'] for e in
                                     FinEmpresa.listar(apenas_ativas=False)}
    rows = _q(
        """SELECT id, numero_cliente, nome_razao_social AS nome, cpf_cnpj AS doc
             FROM clientes
            WHERE nome_razao_social LIKE %s
               OR REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '-', ''), '/', '') LIKE %s
               OR numero_cliente = %s
            ORDER BY nome_razao_social LIMIT 10""",
        (f'%{q}%', f'%{dig}%' if dig else q, q), fetch=True) or []
    return jsonify([r for r in rows if r['id'] not in ja])


@financeiro.route('/financeiro/empresas/marcar', methods=['POST'])
@permission_required('financeiro.empresas')
def empresa_marcar():
    from models.fin_empresa import FinEmpresa
    from models.cliente import Cliente
    raw = (request.form.get('cliente_id') or '').strip()
    apelido = (request.form.get('apelido') or '').strip().upper()
    cli = Cliente.get_by_id(int(raw)) if raw.isdigit() else None
    if not cli:
        flash('Escolha um cadastro para marcar como empresa do financeiro.', 'danger')
    else:
        if not apelido:
            apelido = (cli.get('nome_razao_social') or '').split()[0][:40].upper()
        if FinEmpresa.marcar(int(raw), apelido):
            registrar('escrita.marcou_empresa_fin', 'financeiro',
                      tabela='fin_empresas',
                      depois={'cliente_id': int(raw), 'apelido': apelido})
            flash(f'{apelido} agora participa do financeiro.', 'success')
        else:
            flash('Esse cadastro já está marcado.', 'warning')
    return redirect(url_for('financeiro.empresas'))


@financeiro.route('/financeiro/empresas/<int:emp_id>/atualizar', methods=['POST'])
@permission_required('financeiro.empresas')
def empresa_atualizar(emp_id):
    from models.fin_empresa import FinEmpresa
    e = FinEmpresa.get(emp_id)
    if not e:
        flash('Empresa não encontrada.', 'danger')
        return redirect(url_for('financeiro.empresas'))
    acao = request.form.get('acao')
    if acao == 'apelido':
        novo_ap = (request.form.get('apelido') or '').strip().upper()
        if novo_ap:
            FinEmpresa.atualizar(emp_id, apelido=novo_ap)
            flash('Apelido atualizado.', 'success')
    elif acao == 'consolidado':
        FinEmpresa.atualizar(emp_id, no_consolidado=not e['no_consolidado'])
        flash('Regra do consolidado atualizada.', 'success')
    elif acao == 'ativo':
        if e['ativo'] and FinEmpresa.tem_movimento(e['cliente_id']):
            # some dos chips e dos lançamentos NOVOS; o histórico continua
            FinEmpresa.atualizar(emp_id, ativo=False)
            flash('Empresa desativada — o histórico dela continua guardado.', 'success')
        else:
            FinEmpresa.atualizar(emp_id, ativo=not e['ativo'])
            flash('Empresa reativada.' if not e['ativo'] else 'Empresa desativada.',
                  'success')
    registrar('escrita.alterou_empresa_fin', 'financeiro', tabela='fin_empresas',
              registro_id=emp_id, depois={'acao': acao})
    return redirect(url_for('financeiro.empresas'))


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------
def _preserve_filters():
    """Retorna os filtros ativos da query string para manter na redirect."""
    keys = ('empresa_id', 'conta_id', 'forma_recebimento_id',
            'status', 'data_inicio', 'data_fim', 'f_descricao')
    return {k: request.form.get(k) or request.args.get(k) or ''
            for k in keys}
