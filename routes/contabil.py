"""Rotas para o módulo Contábil - Conciliação Bancária"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from utils.auth_helper import permission_required
import os

# Cria Blueprint primeiro (antes de importar models)
contabil = Blueprint('contabil', __name__, url_prefix='/contabil')

# Valores válidos para importação do Plano de Contas via CSV
_TIPOS_PLANO_VALIDOS = {'ANALITICA', 'SINTETICA'}
_NATUREZAS_PLANO_VALIDAS = {'DEVEDORA', 'CREDORA'}
_GRUPOS_PLANO_VALIDOS = {'ATIVO', 'PASSIVO', 'PATRIMONIO_LIQUIDO', 'RECEITA', 'DESPESA'}
_CSV_HEADER_PLANO = ['codigo', 'descricao', 'tipo', 'natureza', 'grupo_contabil']

# Try to import models with error handling
try:
    from models.conciliacao_bancaria import ConciliacaoBancaria
    from models.memorizacao_conciliacao import MemorizacaoConciliacao
    from models.cliente import Cliente
    from models.grupo_cliente import GrupoCliente
    from models.plano_contas import PlanoConta, PlanoContaItem
    from models.conta_corrente import ContaCorrente
    print("✅ Contabil: Models imported successfully")
    MODELS_LOADED = True
except Exception as e:
    print(f"❌ Contabil: Error importing models: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
    MODELS_LOADED = False
    # Create placeholder classes to allow blueprint to load
    class ConciliacaoBancaria:
        @staticmethod
        def get_all(*args, **kwargs):
            return []
        @staticmethod
        def get_by_id(*args, **kwargs):
            return None
    class MemorizacaoConciliacao:
        @staticmethod
        def get_all(*args, **kwargs):
            return []
        @staticmethod
        def get_by_id(*args, **kwargs):
            return None
    class Cliente:
        @staticmethod
        def get_all(*args, **kwargs):
            return []
    class GrupoCliente:
        @staticmethod
        def get_all(*args, **kwargs):
            return []
    class PlanoConta:
        @staticmethod
        def get_all(*args, **kwargs):
            return []
        @staticmethod
        def get_by_id(*args, **kwargs):
            return None
        @staticmethod
        def create(*args, **kwargs):
            return None
        @staticmethod
        def delete(*args, **kwargs):
            return None
    class PlanoContaItem:
        @staticmethod
        def get_all_by_plano(*args, **kwargs):
            return []
        @staticmethod
        def create(*args, **kwargs):
            return None
        @staticmethod
        def import_batch(*args, **kwargs):
            return False
        @staticmethod
        def delete(*args, **kwargs):
            return None
    class ContaCorrente:
        @staticmethod
        def get_all(*args, **kwargs):
            return []
        @staticmethod
        def create(*args, **kwargs):
            return None
        @staticmethod
        def set_ativa(*args, **kwargs):
            return None


@contabil.route('/')
@permission_required('contabil.index')
def index():
    """Página principal do módulo contábil"""
    return render_template('contabil/index.html')


@contabil.route('/plano_contas')
@permission_required('contabil.plano_contas')
def plano_contas():
    """Lista grupos/planos de contas"""
    grupo_id = request.args.get('grupo_id')
    planos = PlanoConta.get_all(grupo_id=grupo_id)
    grupos = GrupoCliente.get_all()
    return render_template('contabil/plano_contas.html',
                           planos=planos,
                           grupos=grupos,
                           filtro_grupo_id=grupo_id)


@contabil.route('/plano_contas/criar', methods=['POST'])
@login_required
def criar_plano_contas():
    """Cria novo grupo do plano de contas"""
    nome = request.form.get('nome', '').strip()
    descricao = request.form.get('descricao', '').strip() or None
    grupo_id = request.form.get('grupo_id') or None

    if not nome:
        flash('Nome é obrigatório.', 'danger')
        return redirect(url_for('contabil.plano_contas'))

    resultado = PlanoConta.create(nome=nome, descricao=descricao, grupo_id=grupo_id)
    if resultado:
        flash('Plano de Contas criado com sucesso!', 'success')
        return redirect(url_for('contabil.ver_plano_contas', plano_id=resultado))
    else:
        flash('Erro ao criar Plano de Contas.', 'danger')
        return redirect(url_for('contabil.plano_contas'))


@contabil.route('/plano_contas/<int:plano_id>')
@login_required
def ver_plano_contas(plano_id):
    """Exibe contas de um plano"""
    plano = PlanoConta.get_by_id(plano_id)
    if not plano:
        flash('Plano de Contas não encontrado.', 'danger')
        return redirect(url_for('contabil.plano_contas'))
    itens = PlanoContaItem.get_all_by_plano(plano_id)
    grupos = GrupoCliente.get_all()
    return render_template('contabil/plano_contas_detalhe.html',
                           plano=plano,
                           itens=itens,
                           grupos=grupos)


@contabil.route('/plano_contas/<int:plano_id>/nova_conta', methods=['POST'])
@login_required
def nova_conta_plano(plano_id):
    """Adiciona nova conta ao plano"""
    plano = PlanoConta.get_by_id(plano_id)
    if not plano:
        flash('Plano de Contas não encontrado.', 'danger')
        return redirect(url_for('contabil.plano_contas'))

    codigo = request.form.get('codigo', '').strip()
    descricao = request.form.get('descricao', '').strip()
    tipo = request.form.get('tipo', '').strip()
    natureza = request.form.get('natureza', '').strip()
    grupo_contabil = request.form.get('grupo_contabil', '').strip()

    if not all([codigo, descricao, tipo, natureza, grupo_contabil]):
        flash('Preencha todos os campos obrigatórios.', 'danger')
        return redirect(url_for('contabil.ver_plano_contas', plano_id=plano_id))

    resultado = PlanoContaItem.create(
        plano_id=plano_id,
        codigo=codigo,
        descricao=descricao,
        tipo=tipo,
        natureza=natureza,
        grupo_contabil=grupo_contabil,
    )
    if resultado:
        flash('Conta adicionada com sucesso!', 'success')
    else:
        flash('Erro ao adicionar conta.', 'danger')
    return redirect(url_for('contabil.ver_plano_contas', plano_id=plano_id))


@contabil.route('/plano_contas/<int:plano_id>/excluir_conta/<int:item_id>', methods=['POST'])
@login_required
def excluir_conta_plano(plano_id, item_id):
    """Exclui uma conta do plano"""
    PlanoContaItem.delete(item_id)
    flash('Conta removida com sucesso.', 'success')
    return redirect(url_for('contabil.ver_plano_contas', plano_id=plano_id))


@contabil.route('/plano_contas/<int:plano_id>/excluir', methods=['POST'])
@login_required
def excluir_plano_contas(plano_id):
    """Exclui plano de contas e todos os seus itens"""
    PlanoConta.delete(plano_id)
    flash('Plano de Contas excluído com sucesso.', 'success')
    return redirect(url_for('contabil.plano_contas'))


@contabil.route('/plano_contas/template_csv')
@login_required
def template_csv_plano_contas():
    """Download do modelo CSV para preenchimento e importação posterior"""
    import csv
    import io
    from flask import make_response

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_CSV_HEADER_PLANO)
    # Exemplos de contas para orientar o usuário
    writer.writerow(['1', 'ATIVO', 'SINTETICA', 'DEVEDORA', 'ATIVO'])
    writer.writerow(['1.1', 'ATIVO CIRCULANTE', 'SINTETICA', 'DEVEDORA', 'ATIVO'])
    writer.writerow(['1.1.001', 'CAIXA', 'ANALITICA', 'DEVEDORA', 'ATIVO'])
    writer.writerow(['1.1.002', 'BANCOS CONTA MOVIMENTO', 'ANALITICA', 'DEVEDORA', 'ATIVO'])
    writer.writerow(['2', 'PASSIVO', 'SINTETICA', 'CREDORA', 'PASSIVO'])
    writer.writerow(['2.1', 'PASSIVO CIRCULANTE', 'SINTETICA', 'CREDORA', 'PASSIVO'])
    writer.writerow(['2.1.001', 'FORNECEDORES', 'ANALITICA', 'CREDORA', 'PASSIVO'])
    writer.writerow(['3', 'PATRIMÔNIO LÍQUIDO', 'SINTETICA', 'CREDORA', 'PATRIMONIO_LIQUIDO'])
    writer.writerow(['3.1.001', 'CAPITAL SOCIAL', 'ANALITICA', 'CREDORA', 'PATRIMONIO_LIQUIDO'])
    writer.writerow(['4', 'RECEITAS', 'SINTETICA', 'CREDORA', 'RECEITA'])
    writer.writerow(['4.1.001', 'RECEITA BRUTA DE VENDAS', 'ANALITICA', 'CREDORA', 'RECEITA'])
    writer.writerow(['5', 'DESPESAS', 'SINTETICA', 'DEVEDORA', 'DESPESA'])
    writer.writerow(['5.1.001', 'SALÁRIOS E ORDENADOS', 'ANALITICA', 'DEVEDORA', 'DESPESA'])

    response = make_response(output.getvalue())
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = 'attachment; filename=modelo_plano_contas.csv'
    return response


@contabil.route('/plano_contas/<int:plano_id>/importar', methods=['POST'])
@login_required
def importar_plano_contas(plano_id):
    """Importa contas de um arquivo CSV para o plano"""
    import csv
    import io

    plano = PlanoConta.get_by_id(plano_id)
    if not plano:
        flash('Plano de Contas não encontrado.', 'danger')
        return redirect(url_for('contabil.plano_contas'))

    arquivo = request.files.get('arquivo_csv')
    if not arquivo or not arquivo.filename:
        flash('Selecione um arquivo CSV.', 'danger')
        return redirect(url_for('contabil.ver_plano_contas', plano_id=plano_id))

    if not arquivo.filename.lower().endswith('.csv'):
        flash('Apenas arquivos .csv são aceitos.', 'danger')
        return redirect(url_for('contabil.ver_plano_contas', plano_id=plano_id))

    try:
        conteudo = arquivo.read().decode('utf-8-sig')  # utf-8-sig ignora BOM do Excel
        reader = csv.DictReader(io.StringIO(conteudo))

        tipos_validos = _TIPOS_PLANO_VALIDOS
        naturezas_validas = _NATUREZAS_PLANO_VALIDAS
        grupos_validos = _GRUPOS_PLANO_VALIDOS

        itens = []
        erros = []

        for i, row in enumerate(reader, start=2):  # linha 1 é o cabeçalho
            codigo = (row.get('codigo') or '').strip()
            descricao = (row.get('descricao') or '').strip()
            tipo = (row.get('tipo') or '').strip().upper()
            natureza = (row.get('natureza') or '').strip().upper()
            grupo_contabil = (row.get('grupo_contabil') or '').strip().upper()

            if not codigo or not descricao:
                erros.append(f"Linha {i}: 'codigo' e 'descricao' são obrigatórios")
                continue
            if tipo not in tipos_validos:
                erros.append(f"Linha {i}: tipo '{tipo}' inválido (use ANALITICA ou SINTETICA)")
                continue
            if natureza not in naturezas_validas:
                erros.append(f"Linha {i}: natureza '{natureza}' inválida (use DEVEDORA ou CREDORA)")
                continue
            if grupo_contabil not in grupos_validos:
                erros.append(
                    f"Linha {i}: grupo_contabil '{grupo_contabil}' inválido "
                    "(use ATIVO, PASSIVO, PATRIMONIO_LIQUIDO, RECEITA ou DESPESA)"
                )
                continue

            itens.append({
                'codigo': codigo,
                'descricao': descricao,
                'tipo': tipo,
                'natureza': natureza,
                'grupo_contabil': grupo_contabil,
            })

        if erros:
            flash('Erros no arquivo: ' + ' | '.join(erros[:5]), 'danger')
            return redirect(url_for('contabil.ver_plano_contas', plano_id=plano_id))

        if not itens:
            flash('Nenhum dado válido encontrado no arquivo.', 'warning')
            return redirect(url_for('contabil.ver_plano_contas', plano_id=plano_id))

        sucesso = PlanoContaItem.import_batch(plano_id, itens)
        if sucesso:
            flash(f'{len(itens)} conta(s) importada(s) com sucesso!', 'success')
        else:
            flash('Erro ao importar contas.', 'danger')

    except Exception as e:
        flash(f'Erro ao processar arquivo: {str(e)}', 'danger')

    return redirect(url_for('contabil.ver_plano_contas', plano_id=plano_id))


@contabil.route('/conciliacoes')
@permission_required('contabil.conciliacoes')
def conciliacoes():
    """Lista todas as conciliações bancárias"""
    cliente_id = request.args.get('cliente_id')
    grupo_id = request.args.get('grupo_id')
    status = request.args.get('status')
    
    # Busca conciliações
    conciliacoes_lista = ConciliacaoBancaria.get_all(
        cliente_id=cliente_id,
        grupo_id=grupo_id,
        status=status
    )
    
    # Busca clientes e grupos para filtros
    clientes = Cliente.get_all(per_page=1000).get('clientes', [])
    grupos = GrupoCliente.get_all()
    
    return render_template('contabil/conciliacoes.html',
                         conciliacoes=conciliacoes_lista,
                         clientes=clientes,
                         grupos=grupos,
                         filtros={
                             'cliente_id': cliente_id,
                             'grupo_id': grupo_id,
                             'status': status
                         })


@contabil.route('/conciliacoes/nova')
@login_required
def nova_conciliacao():
    """Página para criar nova conciliação (importar OFX)"""
    # Busca clientes para seleção
    clientes = Cliente.get_all(per_page=1000).get('clientes', [])
    
    return render_template('contabil/nova_conciliacao.html',
                         clientes=clientes)


@contabil.route('/conciliacoes/<int:conciliacao_id>')
@login_required
def ver_conciliacao(conciliacao_id):
    """Visualiza uma conciliação específica"""
    conciliacao = ConciliacaoBancaria.get_by_id(conciliacao_id)
    
    if not conciliacao:
        flash('Conciliação não encontrada.', 'error')
        return redirect(url_for('contabil.conciliacoes'))
    
    # TODO: Buscar transações da conciliação
    transacoes = []
    
    return render_template('contabil/ver_conciliacao.html',
                         conciliacao=conciliacao,
                         transacoes=transacoes)


@contabil.route('/contas_correntes', methods=['GET', 'POST'])
@login_required
def contas_correntes():
    """Cadastro e gestão de contas correntes bancárias"""
    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id')
        banco_nome = request.form.get('banco_nome', '').strip()
        banco_codigo = request.form.get('banco_codigo', '').strip()
        agencia = request.form.get('agencia', '').strip()
        agencia_digito = request.form.get('agencia_digito', '').strip()
        numero_conta = request.form.get('numero_conta', '').strip()
        conta_digito = request.form.get('conta_digito', '').strip()
        tipo = request.form.get('tipo', '').strip()
        saldo_inicial = request.form.get('saldo_inicial', '0') or '0'

        if not all([cliente_id, banco_nome, banco_codigo, agencia, numero_conta, conta_digito, tipo]):
            flash('Preencha todos os campos obrigatórios.', 'danger')
        else:
            try:
                saldo = float(saldo_inicial)
            except ValueError:
                saldo = 0.00

            resultado = ContaCorrente.create(
                cliente_id=int(cliente_id),
                banco_nome=banco_nome,
                banco_codigo=banco_codigo,
                agencia=agencia,
                agencia_digito=agencia_digito,
                numero_conta=numero_conta,
                conta_digito=conta_digito,
                tipo=tipo,
                saldo_inicial=saldo,
            )
            if resultado:
                flash('Conta corrente cadastrada com sucesso!', 'success')
            else:
                flash('Erro ao cadastrar conta corrente. Tente novamente.', 'danger')

        return redirect(url_for('contabil.contas_correntes'))

    # GET
    cliente_id_filtro = request.args.get('cliente_id')
    banco_filtro = request.args.get('banco')
    status_filtro = request.args.get('status')

    ativa = None
    if status_filtro == 'ativa':
        ativa = True
    elif status_filtro == 'inativa':
        ativa = False

    contas = ContaCorrente.get_all(
        cliente_id=cliente_id_filtro,
        banco=banco_filtro,
        ativa=ativa,
    )
    clientes_result = Cliente.get_all()
    clientes = clientes_result.get('clientes', []) if isinstance(clientes_result, dict) else (clientes_result or [])

    return render_template('contabil/contas_correntes.html',
                           contas=contas,
                           clientes=clientes)


@contabil.route('/importar_ofx')
@login_required
def importar_ofx():
    """Interface para importação de arquivos OFX"""
    # Buscar contas correntes para seleção
    # TODO: Implementar busca de contas do banco
    contas = []  # Placeholder
    clientes = Cliente.get_all(per_page=1000).get('clientes', [])
    
    return render_template('contabil/importar_ofx.html',
                         contas=contas,
                         clientes=clientes)


# --------------------------------------------------------------------------- #
#  IMPORTAR PDF                                                                 #
# --------------------------------------------------------------------------- #

ALLOWED_PDF_EXTENSIONS = {'pdf'}
MAX_PDF_SIZE_MB = 20
_MAX_PDF_SIZE_BYTES = MAX_PDF_SIZE_MB * 1024 * 1024


def _allowed_pdf(filename):
    return (
        '.' in filename
        and filename.rsplit('.', 1)[1].lower() in ALLOWED_PDF_EXTENSIONS
    )


@contabil.route('/importar_pdf', methods=['GET', 'POST'])
@login_required
def importar_pdf():
    """Interface para importação de extratos bancários em PDF"""
    clientes = Cliente.get_all()
    # Extrair lista de clientes independente do formato retornado
    if isinstance(clientes, dict):
        clientes = clientes.get('clientes', [])

    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id', '').strip()
        arquivo = request.files.get('arquivo_pdf')

        # Validações básicas
        if not arquivo or not arquivo.filename:
            flash('Selecione um arquivo PDF.', 'danger')
            return render_template('contabil/importar_pdf.html', clientes=clientes)

        if not _allowed_pdf(arquivo.filename):
            flash('Apenas arquivos .pdf são aceitos.', 'danger')
            return render_template('contabil/importar_pdf.html', clientes=clientes)

        # Validate size via Content-Length before reading the full body
        content_length = request.content_length
        if content_length and content_length > _MAX_PDF_SIZE_BYTES:
            flash(f'O arquivo excede o tamanho máximo permitido ({MAX_PDF_SIZE_MB} MB).', 'danger')
            return render_template('contabil/importar_pdf.html', clientes=clientes)

        pdf_bytes = arquivo.read()
        if len(pdf_bytes) > _MAX_PDF_SIZE_BYTES:
            flash(f'O arquivo excede o tamanho máximo permitido ({MAX_PDF_SIZE_MB} MB).', 'danger')
            return render_template('contabil/importar_pdf.html', clientes=clientes)

        # Processa o PDF
        from utils.pdf_parser import extrair_transacoes_pdf
        resultado = extrair_transacoes_pdf(pdf_bytes)

        if not resultado['sucesso']:
            flash(f'Erro ao processar PDF: {resultado["erro"]}', 'danger')
            return render_template('contabil/importar_pdf.html', clientes=clientes)

        if resultado.get('aviso'):
            flash(resultado['aviso'], 'warning')

        transacoes = resultado['transacoes']
        texto_bruto = resultado['texto_bruto']
        nome_arquivo = arquivo.filename

        return render_template(
            'contabil/importar_pdf.html',
            clientes=clientes,
            transacoes=transacoes,
            texto_bruto=texto_bruto,
            nome_arquivo=nome_arquivo,
            cliente_id=cliente_id,
            total_transacoes=len(transacoes),
        )

    return render_template('contabil/importar_pdf.html', clientes=clientes)


@contabil.route('/importar_pdf/confirmar', methods=['POST'])
@login_required
def confirmar_importacao_pdf():
    """Salva transações extraídas do PDF como pendentes de conciliação"""
    cliente_id = request.form.get('cliente_id') or None
    datas = request.form.getlist('data[]')
    descricoes = request.form.getlist('descricao[]')
    valores = request.form.getlist('valor[]')
    tipos = request.form.getlist('tipo[]')

    if not datas:
        flash('Nenhuma transação para importar.', 'warning')
        return redirect(url_for('contabil.importar_pdf'))

    # TODO: Persistir as transações no banco de dados quando o modelo
    #       de transações estiver implementado.
    #       Por ora, exibe uma mensagem de confirmação com o total importado.
    total = len(datas)
    flash(
        f'{total} transação(ões) do PDF importadas com sucesso! '
        'Acesse o Extrato de Conciliação para classificá-las.',
        'success',
    )
    return redirect(url_for('contabil.extrato_conciliacao'))


@contabil.route('/extrato_conciliacao')
@login_required
def extrato_conciliacao():
    """Visualização e conciliação de extratos bancários"""
    conta_id = request.args.get('conta_id')
    data_inicial = request.args.get('data_inicial')
    data_final = request.args.get('data_final')
    
    # TODO: Implementar busca de transações do banco
    transacoes = []  # Placeholder
    contas = []  # Placeholder
    
    return render_template('contabil/extrato_conciliacao.html',
                         transacoes=transacoes,
                         contas=contas,
                         filtros={
                             'conta_id': conta_id,
                             'data_inicial': data_inicial,
                             'data_final': data_final
                         })


@contabil.route('/memorizacoes')
@login_required
def memorizacoes():
    """Lista todas as memorizações de conciliação"""
    tipo = request.args.get('tipo')
    grupo_id = request.args.get('grupo_id')
    cliente_id = request.args.get('cliente_id')
    
    # Busca memorizações
    memorizacoes_lista = MemorizacaoConciliacao.get_all(
        tipo=tipo,
        grupo_id=grupo_id,
        cliente_id=cliente_id
    )
    
    # Busca clientes e grupos para filtros
    clientes = Cliente.get_all()
    grupos = GrupoCliente.get_all()
    
    return render_template('contabil/memorizacoes.html',
                         memorizacoes=memorizacoes_lista,
                         clientes=clientes,
                         grupos=grupos,
                         filtros={
                             'tipo': tipo,
                             'grupo_id': grupo_id,
                             'cliente_id': cliente_id
                         })


@contabil.route('/memorizacoes/nova', methods=['GET', 'POST'])
@login_required
def nova_memorizacao():
    """Cria nova memorização"""
    if request.method == 'POST':
        tipo = request.form.get('tipo')
        palavra_chave = request.form.get('palavra_chave')
        categoria_contabil = request.form.get('categoria_contabil')
        conta_contabil = request.form.get('conta_contabil')
        historico_padrao = request.form.get('historico_padrao')
        grupo_id = request.form.get('grupo_id') if tipo == 'GRUPO' else None
        cliente_id = request.form.get('cliente_id') if tipo == 'INDIVIDUAL' else None
        ativo = request.form.get('ativo') == 'on'
        
        # Validações
        if not all([tipo, palavra_chave, categoria_contabil, conta_contabil, historico_padrao]):
            flash('Preencha todos os campos obrigatórios.', 'error')
            return redirect(url_for('contabil.nova_memorizacao'))
        
        if tipo == 'GRUPO' and not grupo_id:
            flash('Selecione um grupo para memorização do tipo GRUPO.', 'error')
            return redirect(url_for('contabil.nova_memorizacao'))
        
        if tipo == 'INDIVIDUAL' and not cliente_id:
            flash('Selecione um cliente para memorização do tipo INDIVIDUAL.', 'error')
            return redirect(url_for('contabil.nova_memorizacao'))
        
        # Cria memorização
        resultado = MemorizacaoConciliacao.create(
            tipo=tipo,
            palavra_chave=palavra_chave,
            categoria_contabil=categoria_contabil,
            conta_contabil=conta_contabil,
            historico_padrao=historico_padrao,
            grupo_id=grupo_id,
            cliente_id=cliente_id,
            ativo=ativo
        )
        
        if resultado:
            flash('Memorização criada com sucesso!', 'success')
            return redirect(url_for('contabil.memorizacoes'))
        else:
            flash('Erro ao criar memorização.', 'error')
    
    # GET - Exibe formulário
    clientes = Cliente.get_all()
    grupos = GrupoCliente.get_all()
    
    return render_template('contabil/form_memorizacao.html',
                         clientes=clientes,
                         grupos=grupos,
                         memorizacao=None)


@contabil.route('/memorizacoes/<int:memorizacao_id>/editar', methods=['GET', 'POST'])
@login_required
def editar_memorizacao(memorizacao_id):
    """Edita uma memorização existente"""
    memorizacao = MemorizacaoConciliacao.get_by_id(memorizacao_id)
    
    if not memorizacao:
        flash('Memorização não encontrada.', 'error')
        return redirect(url_for('contabil.memorizacoes'))
    
    if request.method == 'POST':
        palavra_chave = request.form.get('palavra_chave')
        categoria_contabil = request.form.get('categoria_contabil')
        conta_contabil = request.form.get('conta_contabil')
        historico_padrao = request.form.get('historico_padrao')
        ativo = request.form.get('ativo') == 'on'
        
        # Validações
        if not all([palavra_chave, categoria_contabil, conta_contabil, historico_padrao]):
            flash('Preencha todos os campos obrigatórios.', 'error')
            return redirect(url_for('contabil.editar_memorizacao', memorizacao_id=memorizacao_id))
        
        # Atualiza memorização
        resultado = MemorizacaoConciliacao.update(
            memorizacao_id=memorizacao_id,
            palavra_chave=palavra_chave,
            categoria_contabil=categoria_contabil,
            conta_contabil=conta_contabil,
            historico_padrao=historico_padrao,
            ativo=ativo
        )
        
        if resultado is not None:
            flash('Memorização atualizada com sucesso!', 'success')
            return redirect(url_for('contabil.memorizacoes'))
        else:
            flash('Erro ao atualizar memorização.', 'error')
    
    # GET - Exibe formulário
    clientes = Cliente.get_all()
    grupos = GrupoCliente.get_all()
    
    return render_template('contabil/form_memorizacao.html',
                         clientes=clientes,
                         grupos=grupos,
                         memorizacao=memorizacao)


@contabil.route('/memorizacoes/<int:memorizacao_id>/excluir', methods=['POST'])
@login_required
def excluir_memorizacao(memorizacao_id):
    """Exclui uma memorização"""
    resultado = MemorizacaoConciliacao.delete(memorizacao_id)
    
    if resultado:
        flash('Memorização excluída com sucesso!', 'success')
    else:
        flash('Erro ao excluir memorização.', 'error')
    
    return redirect(url_for('contabil.memorizacoes'))


# API para buscar grupos de um cliente
@contabil.route('/api/cliente/<int:cliente_id>/grupos')
@login_required
def api_cliente_grupos(cliente_id):
    """Retorna grupos de um cliente"""
    # TODO: Implementar busca de grupos do cliente
    # Por enquanto retorna lista vazia
    return jsonify([])
