"""Rotas de Grupos de Clientes - CRUD completo"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from utils.auth_helper import login_required
from models.grupo_cliente import GrupoCliente
from models.cliente import Cliente

grupos = Blueprint('grupos', __name__)


@grupos.route('/grupos')
@login_required
def index():
    """Lista todos os grupos"""
    try:
        # Parâmetros de filtro
        situacao = request.args.get('situacao', '')
        
        # Buscar grupos
        if situacao:
            grupos_list = GrupoCliente.get_all(situacao=situacao)
        else:
            grupos_list = GrupoCliente.get_all()
        
        # Contar clientes em cada grupo
        for grupo in grupos_list:
            clientes = GrupoCliente.get_clientes(grupo['id'])
            grupo['total_clientes'] = len(clientes)
        
        return render_template('grupos/index.html',
                             grupos=grupos_list,
                             filtro_situacao=situacao)
    
    except Exception as e:
        flash(f'Erro ao carregar grupos: {str(e)}', 'danger')
        return render_template('grupos/index.html',
                             grupos=[],
                             filtro_situacao='')


@grupos.route('/grupos/novo', methods=['GET', 'POST'])
@login_required
def novo():
    """Criar novo grupo"""
    if request.method == 'POST':
        try:
            nome = request.form.get('nome')
            descricao = request.form.get('descricao')
            situacao = request.form.get('situacao', 'ATIVO')
            
            # Validação
            if not nome:
                flash('Nome do grupo é obrigatório!', 'danger')
                return render_template('grupos/form.html', grupo=None)
            
            # Criar grupo
            grupo_id = GrupoCliente.create(nome, descricao, situacao)
            
            if grupo_id:
                flash('Grupo criado com sucesso!', 'success')
                return redirect(url_for('grupos.detalhes', id=grupo_id))
            else:
                flash('Erro ao criar grupo!', 'danger')
        
        except Exception as e:
            flash(f'Erro ao criar grupo: {str(e)}', 'danger')
    
    return render_template('grupos/form.html', grupo=None)


@grupos.route('/grupos/<int:id>')
@login_required
def detalhes(id):
    """Visualizar detalhes do grupo e seus clientes"""
    try:
        grupo = GrupoCliente.get_by_id(id)
        if not grupo:
            flash('Grupo não encontrado!', 'danger')
            return redirect(url_for('grupos.index'))
        
        # Buscar clientes do grupo
        clientes_grupo = GrupoCliente.get_clientes(id)
        
        # Buscar todos os clientes para poder adicionar
        todos_clientes = Cliente.get_all(page=1, per_page=1000)
        clientes_disponiveis = todos_clientes.get('clientes', [])
        
        # Filtrar clientes que já estão no grupo
        clientes_ids_no_grupo = [c['id'] for c in clientes_grupo]
        clientes_disponiveis = [c for c in clientes_disponiveis if c['id'] not in clientes_ids_no_grupo]
        
        return render_template('grupos/detalhes.html',
                             grupo=grupo,
                             clientes=clientes_grupo,
                             clientes_disponiveis=clientes_disponiveis)
    
    except Exception as e:
        flash(f'Erro ao carregar detalhes do grupo: {str(e)}', 'danger')
        return redirect(url_for('grupos.index'))


@grupos.route('/grupos/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar(id):
    """Editar grupo"""
    grupo = GrupoCliente.get_by_id(id)
    if not grupo:
        flash('Grupo não encontrado!', 'danger')
        return redirect(url_for('grupos.index'))
    
    if request.method == 'POST':
        try:
            nome = request.form.get('nome')
            descricao = request.form.get('descricao')
            situacao = request.form.get('situacao', 'ATIVO')
            
            # Validação
            if not nome:
                flash('Nome do grupo é obrigatório!', 'danger')
                return render_template('grupos/form.html', grupo=grupo)
            
            # Atualizar grupo
            sucesso = GrupoCliente.update(id, nome, descricao, situacao)
            
            if sucesso is not None:
                flash('Grupo atualizado com sucesso!', 'success')
                return redirect(url_for('grupos.detalhes', id=id))
            else:
                flash('Erro ao atualizar grupo!', 'danger')
        
        except Exception as e:
            flash(f'Erro ao atualizar grupo: {str(e)}', 'danger')
    
    return render_template('grupos/form.html', grupo=grupo)


@grupos.route('/grupos/<int:id>/deletar', methods=['POST'])
@login_required
def deletar(id):
    """Deletar grupo"""
    try:
        sucesso = GrupoCliente.delete(id)
        if sucesso is not None:
            flash('Grupo removido com sucesso!', 'success')
        else:
            flash('Erro ao remover grupo!', 'danger')
    except Exception as e:
        flash(f'Erro ao remover grupo: {str(e)}', 'danger')
    
    return redirect(url_for('grupos.index'))


@grupos.route('/grupos/<int:grupo_id>/adicionar-cliente', methods=['POST'])
@login_required
def adicionar_cliente(grupo_id):
    """Adicionar cliente ao grupo"""
    try:
        cliente_id = request.form.get('cliente_id', type=int)
        
        if not cliente_id:
            flash('Cliente não selecionado!', 'danger')
            return redirect(url_for('grupos.detalhes', id=grupo_id))
        
        sucesso = GrupoCliente.add_cliente(grupo_id, cliente_id)
        
        if sucesso:
            flash('Cliente adicionado ao grupo com sucesso!', 'success')
        else:
            flash('Erro ao adicionar cliente ao grupo!', 'danger')
    
    except Exception as e:
        flash(f'Erro ao adicionar cliente: {str(e)}', 'danger')
    
    return redirect(url_for('grupos.detalhes', id=grupo_id))


@grupos.route('/grupos/<int:grupo_id>/remover-cliente/<int:cliente_id>', methods=['POST'])
@login_required
def remover_cliente(grupo_id, cliente_id):
    """Remover cliente do grupo"""
    try:
        sucesso = GrupoCliente.remove_cliente(grupo_id, cliente_id)
        
        if sucesso is not None:
            flash('Cliente removido do grupo com sucesso!', 'success')
        else:
            flash('Erro ao remover cliente do grupo!', 'danger')
    
    except Exception as e:
        flash(f'Erro ao remover cliente: {str(e)}', 'danger')
    
    return redirect(url_for('grupos.detalhes', id=grupo_id))
