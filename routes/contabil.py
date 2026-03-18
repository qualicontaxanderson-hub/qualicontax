"""Rotas para o módulo Contábil - Conciliação Bancária"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models.conciliacao_bancaria import ConciliacaoBancaria
from models.memorizacao_conciliacao import MemorizacaoConciliacao
from models.cliente import Cliente
from models.grupo_cliente import GrupoCliente

# Cria Blueprint
contabil = Blueprint('contabil', __name__, url_prefix='/contabil')


@contabil.route('/')
@login_required
def index():
    """Página principal do módulo contábil"""
    return render_template('contabil/index.html')


@contabil.route('/conciliacoes')
@login_required
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
    clientes = Cliente.get_all()
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
    clientes = Cliente.get_all()
    
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
