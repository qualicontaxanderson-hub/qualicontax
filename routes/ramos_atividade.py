"""Rotas de Ramos de Atividade - CRUD completo"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import current_user
from utils.auth_helper import login_required
from models.ramo_atividade import RamoAtividade
from models.cliente import Cliente

ramos_atividade = Blueprint('ramos_atividade', __name__)


@ramos_atividade.route('/ramodeatividade')
@login_required
def index():
    """Lista todos os ramos de atividade"""
    try:
        # Parâmetros de filtro
        situacao = request.args.get('situacao', '')
        
        # Buscar ramos
        if situacao:
            ramos_list = RamoAtividade.get_all(situacao=situacao)
        else:
            ramos_list = RamoAtividade.get_all()
        
        # Contar clientes em cada ramo
        for ramo in ramos_list:
            clientes = RamoAtividade.get_clientes(ramo['id'])
            ramo['total_clientes'] = len(clientes)
        
        return render_template('ramos_atividade/index.html',
                             ramos=ramos_list,
                             filtro_situacao=situacao)
    
    except Exception as e:
        flash(f'Erro ao carregar ramos de atividade: {str(e)}', 'danger')
        return render_template('ramos_atividade/index.html',
                             ramos=[],
                             filtro_situacao='')


@ramos_atividade.route('/ramodeatividade/novo', methods=['GET', 'POST'])
@login_required
def novo():
    """Criar novo ramo de atividade"""
    if request.method == 'POST':
        try:
            nome = request.form.get('nome')
            descricao = request.form.get('descricao')
            situacao = request.form.get('situacao', 'ATIVO')
            
            # Validação
            if not nome:
                flash('Nome do ramo de atividade é obrigatório!', 'danger')
                return render_template('ramos_atividade/form.html', ramo=None)
            
            # Criar ramo
            ramo_id = RamoAtividade.create(nome, descricao, situacao)
            
            if ramo_id:
                flash('Ramo de atividade criado com sucesso!', 'success')
                return redirect(url_for('ramos_atividade.detalhes', id=ramo_id))
            else:
                flash('Erro ao criar ramo de atividade!', 'danger')
        
        except Exception as e:
            flash(f'Erro ao criar ramo de atividade: {str(e)}', 'danger')
    
    return render_template('ramos_atividade/form.html', ramo=None)


@ramos_atividade.route('/ramodeatividade/<int:id>')
@login_required
def detalhes(id):
    """Visualizar detalhes do ramo e seus clientes"""
    try:
        ramo = RamoAtividade.get_by_id(id)
        if not ramo:
            flash('Ramo de atividade não encontrado!', 'danger')
            return redirect(url_for('ramos_atividade.index'))
        
        # Buscar clientes do ramo
        clientes_ramo = RamoAtividade.get_clientes(id)
        
        # Buscar todos os clientes para poder adicionar
        todos_clientes = Cliente.get_all(page=1, per_page=1000)
        clientes_disponiveis = todos_clientes.get('clientes', [])
        
        # Filtrar clientes que já estão no ramo
        clientes_ids_no_ramo = [c['id'] for c in clientes_ramo]
        clientes_disponiveis = [c for c in clientes_disponiveis if c['id'] not in clientes_ids_no_ramo]
        
        return render_template('ramos_atividade/detalhes.html',
                             ramo=ramo,
                             clientes=clientes_ramo,
                             clientes_disponiveis=clientes_disponiveis)
    
    except Exception as e:
        flash(f'Erro ao carregar detalhes do ramo: {str(e)}', 'danger')
        return redirect(url_for('ramos_atividade.index'))


@ramos_atividade.route('/ramodeatividade/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar(id):
    """Editar ramo de atividade"""
    ramo = RamoAtividade.get_by_id(id)
    if not ramo:
        flash('Ramo de atividade não encontrado!', 'danger')
        return redirect(url_for('ramos_atividade.index'))
    
    if request.method == 'POST':
        try:
            nome = request.form.get('nome')
            descricao = request.form.get('descricao')
            situacao = request.form.get('situacao', 'ATIVO')
            
            # Validação
            if not nome:
                flash('Nome do ramo de atividade é obrigatório!', 'danger')
                return render_template('ramos_atividade/form.html', ramo=ramo)
            
            # Atualizar ramo
            sucesso = RamoAtividade.update(id, nome, descricao, situacao)
            
            if sucesso is not None:
                flash('Ramo de atividade atualizado com sucesso!', 'success')
                return redirect(url_for('ramos_atividade.detalhes', id=id))
            else:
                flash('Erro ao atualizar ramo de atividade!', 'danger')
        
        except Exception as e:
            flash(f'Erro ao atualizar ramo de atividade: {str(e)}', 'danger')
    
    return render_template('ramos_atividade/form.html', ramo=ramo)


@ramos_atividade.route('/ramodeatividade/<int:id>/deletar', methods=['POST'])
@login_required
def deletar(id):
    """Deletar ramo de atividade"""
    try:
        sucesso = RamoAtividade.delete(id)
        if sucesso is not None:
            flash('Ramo de atividade removido com sucesso!', 'success')
        else:
            flash('Erro ao remover ramo de atividade!', 'danger')
    except Exception as e:
        flash(f'Erro ao remover ramo de atividade: {str(e)}', 'danger')
    
    return redirect(url_for('ramos_atividade.index'))


@ramos_atividade.route('/ramodeatividade/<int:ramo_id>/adicionar-cliente', methods=['POST'])
@login_required
def adicionar_cliente(ramo_id):
    """Adicionar cliente ao ramo de atividade"""
    try:
        cliente_id = request.form.get('cliente_id', type=int)
        
        if not cliente_id:
            flash('Cliente não selecionado!', 'danger')
            return redirect(url_for('ramos_atividade.detalhes', id=ramo_id))
        
        sucesso = RamoAtividade.add_cliente(ramo_id, cliente_id)
        
        if sucesso:
            flash('Cliente adicionado ao ramo de atividade com sucesso!', 'success')
        else:
            flash('Erro ao adicionar cliente ao ramo de atividade!', 'danger')
    
    except Exception as e:
        flash(f'Erro ao adicionar cliente: {str(e)}', 'danger')
    
    return redirect(url_for('ramos_atividade.detalhes', id=ramo_id))


@ramos_atividade.route('/ramodeatividade/<int:ramo_id>/remover-cliente/<int:cliente_id>', methods=['POST'])
@login_required
def remover_cliente(ramo_id, cliente_id):
    """Remover cliente do ramo de atividade"""
    try:
        sucesso = RamoAtividade.remove_cliente(ramo_id, cliente_id)
        
        if sucesso is not None:
            flash('Cliente removido do ramo de atividade com sucesso!', 'success')
        else:
            flash('Erro ao remover cliente do ramo de atividade!', 'danger')
    
    except Exception as e:
        flash(f'Erro ao remover cliente: {str(e)}', 'danger')
    
    return redirect(url_for('ramos_atividade.detalhes', id=ramo_id))
