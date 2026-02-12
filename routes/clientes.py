"""Rotas de Clientes - CRUD completo"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from utils.auth_helper import login_required
from models.cliente import Cliente
from models.endereco_cliente import EnderecoCliente
from models.contato_cliente import ContatoCliente
from models.grupo_cliente import GrupoCliente
from models.ramo_atividade import RamoAtividade

clientes = Blueprint('clientes', __name__)


@clientes.route('/clientes')
@login_required
def index():
    """Lista todos os clientes com filtros e paginação"""
    try:
        # Parâmetros de filtro
        situacao = request.args.get('situacao', '')
        regime = request.args.get('regime', '')
        tipo_pessoa = request.args.get('tipo_pessoa', '')
        busca = request.args.get('busca', '')
        page = request.args.get('page', 1, type=int)
        per_page = 20
        
        # Buscar clientes com filtros
        filters = {}
        if situacao:
            filters['situacao'] = situacao
        if regime:
            filters['regime_tributario'] = regime
        if tipo_pessoa:
            filters['tipo_pessoa'] = tipo_pessoa
        if busca:
            filters['busca'] = busca
        
        result = Cliente.get_all(filters=filters, page=page, per_page=per_page)
        
        # Contadores para o dashboard
        stats = Cliente.get_stats()
        
        # Verificar se houve erro na obtenção dos dados
        if result is None:
            flash('Erro ao buscar clientes. Verifique a conexão com o banco de dados.', 'danger')
            result = {'clientes': [], 'page': 1, 'total_pages': 0, 'total': 0}
        
        if stats is None:
            flash('Erro ao buscar estatísticas. Verifique a conexão com o banco de dados.', 'danger')
            stats = {'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0, 'pj': 0}
        
        return render_template('clientes/index.html', 
                             clientes=result['clientes'],
                             page=result['page'],
                             total_pages=result['total_pages'],
                             total=result['total'],
                             stats=stats,
                             filtros={'situacao': situacao, 'regime': regime, 'tipo_pessoa': tipo_pessoa, 'busca': busca})
    
    except Exception as e:
        flash(f'Erro ao carregar página de clientes: {str(e)}', 'danger')
        return render_template('clientes/index.html',
                             clientes=[],
                             page=1,
                             total_pages=0,
                             total=0,
                             stats={'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0, 'pj': 0},
                             filtros={'situacao': '', 'regime': '', 'tipo_pessoa': '', 'busca': ''})


@clientes.route('/clientes/novo', methods=['GET', 'POST'])
@login_required
def novo():
    """Criar novo cliente"""
    if request.method == 'POST':
        # Validações
        cpf_cnpj = request.form.get('cpf_cnpj')
        numero_cliente = request.form.get('numero_cliente', '').strip()
        
        if Cliente.existe_cpf_cnpj(cpf_cnpj):
            flash('CPF/CNPJ já cadastrado!', 'danger')
            return redirect(url_for('clientes.novo'))
        
        # Validar número do cliente se fornecido
        if numero_cliente and Cliente.existe_numero_cliente(numero_cliente):
            flash(f'Número do cliente "{numero_cliente}" já está em uso!', 'danger')
            ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')
            return render_template('clientes/form.html', cliente=None, ramos_atividade=ramos_atividade, cliente_ramo=None)
        
        # Validação de campos obrigatórios
        if not request.form.get('tipo_pessoa') or not request.form.get('nome_razao_social') or not cpf_cnpj:
            flash('Preencha todos os campos obrigatórios.', 'danger')
            ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')
            return render_template('clientes/form.html', cliente=None, ramos_atividade=ramos_atividade, cliente_ramo=None)
        
        # Criar cliente
        data = {
            'numero_cliente': numero_cliente if numero_cliente else None,
            'tipo_pessoa': request.form.get('tipo_pessoa'),
            'nome_razao_social': request.form.get('nome_razao_social'),
            'nome_fantasia': request.form.get('nome_fantasia'),
            'cpf_cnpj': cpf_cnpj,
            'inscricao_estadual': request.form.get('inscricao_estadual'),
            'inscricao_municipal': request.form.get('inscricao_municipal'),
            'email': request.form.get('email'),
            'telefone': request.form.get('telefone'),
            'celular': request.form.get('celular'),
            'regime_tributario': request.form.get('regime_tributario'),
            'porte_empresa': request.form.get('porte_empresa'),
            'situacao': request.form.get('situacao', 'ATIVO'),
            'data_inicio_contrato': request.form.get('data_inicio_contrato'),
            'observacoes': request.form.get('observacoes'),
            'criado_por': current_user.id
        }
        
        cliente_id = Cliente.create(data)
        
        if cliente_id:
            # Adicionar aos ramos de atividade selecionados (múltiplos)
            ramos_ids = request.form.getlist('ramos_atividade_ids')
            for ramo_id in ramos_ids:
                try:
                    RamoAtividade.add_cliente(int(ramo_id), cliente_id)
                except:
                    pass  # Ignora erros de duplicação
            
            flash('Cliente criado com sucesso!', 'success')
            return redirect(url_for('clientes.detalhes', id=cliente_id))
        else:
            flash('Erro ao criar cliente!', 'danger')
    
    ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')
    return render_template('clientes/form.html', cliente=None, ramos_atividade=ramos_atividade, ramos_cliente=[])


@clientes.route('/clientes/<int:id>')
@login_required
def detalhes(id):
    """Visualizar detalhes do cliente com abas"""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        flash('Cliente não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    
    enderecos = EnderecoCliente.get_by_cliente(id)
    contatos = ContatoCliente.get_by_cliente(id)
    grupos = Cliente.get_grupos(id)
    ramos_atividade = RamoAtividade.get_by_cliente(id)
    processos = Cliente.get_processos(id)
    tarefas = Cliente.get_tarefas(id)
    obrigacoes = Cliente.get_obrigacoes(id)
    
    # Buscar grupos disponíveis (que o cliente ainda não pertence)
    todos_grupos = GrupoCliente.get_all(situacao='ATIVO')
    grupos_ids_cliente = [g['id'] for g in grupos]
    grupos_disponiveis = [g for g in todos_grupos if g['id'] not in grupos_ids_cliente]
    
    return render_template('clientes/detalhes.html',
                         cliente=cliente,
                         enderecos=enderecos,
                         contatos=contatos,
                         grupos=grupos,
                         grupos_disponiveis=grupos_disponiveis,
                         ramos_atividade=ramos_atividade,
                         processos=processos,
                         tarefas=tarefas,
                         obrigacoes=obrigacoes)


@clientes.route('/clientes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar(id):
    """Editar cliente"""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        flash('Cliente não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    
    if request.method == 'POST':
        try:
            # Validação de campos obrigatórios
            if not request.form.get('tipo_pessoa') or not request.form.get('nome_razao_social') or not request.form.get('cpf_cnpj'):
                flash('Preencha todos os campos obrigatórios.', 'danger')
                ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')
                cliente_ramos = RamoAtividade.get_by_cliente(id)
                ramos_cliente = [ramo['id'] for ramo in cliente_ramos]
                grupos = GrupoCliente.get_all(situacao='ATIVO')
                grupos_cliente = Cliente.get_grupos(id)
                return render_template('clientes/form.html', cliente=cliente, grupos=grupos, grupos_cliente=grupos_cliente, ramos_atividade=ramos_atividade, ramos_cliente=ramos_cliente)
            
            # Validar número do cliente se fornecido
            numero_cliente = request.form.get('numero_cliente', '').strip()
            if numero_cliente and Cliente.existe_numero_cliente(numero_cliente, id):
                flash(f'Número do cliente "{numero_cliente}" já está em uso por outro cliente!', 'danger')
                ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')
                cliente_ramos = RamoAtividade.get_by_cliente(id)
                ramos_cliente = [ramo['id'] for ramo in cliente_ramos]
                grupos = GrupoCliente.get_all(situacao='ATIVO')
                grupos_cliente = Cliente.get_grupos(id)
                return render_template('clientes/form.html', cliente=cliente, grupos=grupos, grupos_cliente=grupos_cliente, ramos_atividade=ramos_atividade, ramos_cliente=ramos_cliente)
            
            data = {
                'numero_cliente': numero_cliente if numero_cliente else None,
                'tipo_pessoa': request.form.get('tipo_pessoa'),
                'nome_razao_social': request.form.get('nome_razao_social'),
                'cpf_cnpj': request.form.get('cpf_cnpj'),
                'inscricao_estadual': request.form.get('inscricao_estadual'),
                'inscricao_municipal': request.form.get('inscricao_municipal'),
                'email': request.form.get('email'),
                'telefone': request.form.get('telefone'),
                'celular': request.form.get('celular'),
                'regime_tributario': request.form.get('regime_tributario'),
                'porte_empresa': request.form.get('porte_empresa'),
                'situacao': request.form.get('situacao'),
                'data_inicio_contrato': request.form.get('data_inicio_contrato'),
                'observacoes': request.form.get('observacoes')
            }
            
            sucesso = Cliente.update(id, data)
            
            # Check if sucesso is not None (None indicates error, 0 or positive number indicates success)
            if sucesso is not None:
                # Gerenciar ramos de atividade (múltiplos)
                ramos_ids_novos = request.form.getlist('ramos_atividade_ids')
                cliente_ramos_atuais = RamoAtividade.get_by_cliente(id)
                
                # Remover todos os ramos atuais
                for ramo_atual in cliente_ramos_atuais:
                    RamoAtividade.remove_cliente(ramo_atual['id'], id)
                
                # Adicionar novos ramos selecionados
                for ramo_id in ramos_ids_novos:
                    try:
                        RamoAtividade.add_cliente(int(ramo_id), id)
                    except:
                        pass  # Ignora erros de duplicação
                
                flash('Cliente atualizado com sucesso!', 'success')
                return redirect(url_for('clientes.detalhes', id=id))
            else:
                flash('Erro ao atualizar cliente. Verifique os dados e tente novamente.', 'danger')
        except Exception as e:
            flash(f'Erro ao atualizar cliente: {str(e)}', 'danger')
            print(f"Erro ao atualizar cliente {id}: {str(e)}")
    
    # GET - Buscar ramos de atividade e ramos atuais do cliente
    ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')
    cliente_ramos = RamoAtividade.get_by_cliente(id)
    ramos_cliente = [ramo['id'] for ramo in cliente_ramos]  # Lista de IDs para checkboxes
    grupos = GrupoCliente.get_all(situacao='ATIVO')
    grupos_cliente = Cliente.get_grupos(id)
    return render_template('clientes/form.html', cliente=cliente, grupos=grupos, grupos_cliente=grupos_cliente, ramos_atividade=ramos_atividade, ramos_cliente=ramos_cliente)


@clientes.route('/clientes/<int:id>/inativar', methods=['POST'])
@login_required
def inativar(id):
    """Inativar cliente"""
    sucesso = Cliente.update_situacao(id, 'INATIVO')
    if sucesso:
        flash('Cliente inativado com sucesso!', 'success')
    else:
        flash('Erro ao inativar cliente!', 'danger')
    return redirect(url_for('clientes.index'))


@clientes.route('/clientes/<int:id>/deletar', methods=['POST'])
@login_required
def delete(id):
    """DELETE cliente"""
    cliente = Cliente.get_by_id(id)
    
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))
    
    if Cliente.delete(id):
        flash('Cliente removido com sucesso!', 'success')
    else:
        flash('Erro ao remover cliente.', 'danger')
    
    return redirect(url_for('clientes.index'))


# Rotas para Endereços
@clientes.route('/clientes/<int:cliente_id>/enderecos/novo', methods=['POST'])
@login_required
def novo_endereco(cliente_id):
    """Adicionar novo endereço"""
    endereco_id = EnderecoCliente.create(
        cliente_id=cliente_id,
        tipo=request.form.get('tipo'),
        cep=request.form.get('cep'),
        logradouro=request.form.get('logradouro'),
        numero=request.form.get('numero'),
        complemento=request.form.get('complemento'),
        bairro=request.form.get('bairro'),
        cidade=request.form.get('cidade'),
        estado=request.form.get('estado'),
        pais=request.form.get('pais', 'Brasil'),
        principal=request.form.get('principal') == 'on'
    )
    
    if endereco_id:
        flash('Endereço adicionado com sucesso!', 'success')
    else:
        flash('Erro ao adicionar endereço!', 'danger')
    
    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/enderecos/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_endereco(id):
    """Excluir endereço"""
    endereco = EnderecoCliente.get_by_id(id)
    if not endereco:
        flash('Endereço não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    
    cliente_id = endereco['cliente_id']
    
    sucesso = EnderecoCliente.delete(id)
    if sucesso:
        flash('Endereço excluído com sucesso!', 'success')
    else:
        flash('Erro ao excluir endereço!', 'danger')
    
    return redirect(url_for('clientes.detalhes', id=cliente_id))


# Rotas para Contatos
@clientes.route('/clientes/<int:cliente_id>/contatos/novo', methods=['POST'])
@login_required
def novo_contato(cliente_id):
    """Adicionar novo contato"""
    contato_id = ContatoCliente.create(
        cliente_id=cliente_id,
        nome=request.form.get('nome'),
        cargo=request.form.get('cargo'),
        email=request.form.get('email'),
        telefone=request.form.get('telefone'),
        celular=request.form.get('celular'),
        departamento=request.form.get('departamento'),
        principal=request.form.get('principal') == 'on',
        ativo=True
    )
    
    if contato_id:
        flash('Contato adicionado com sucesso!', 'success')
    else:
        flash('Erro ao adicionar contato!', 'danger')
    
    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/contatos/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_contato(id):
    """Excluir contato"""
    contato = ContatoCliente.get_by_id(id)
    if not contato:
        flash('Contato não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    
    cliente_id = contato['cliente_id']
    
    sucesso = ContatoCliente.delete(id)
    if sucesso:
        flash('Contato excluído com sucesso!', 'success')
    else:
        flash('Erro ao excluir contato!', 'danger')
    
    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/clientes/<int:cliente_id>/adicionar-grupo', methods=['POST'])
@login_required
def adicionar_grupo(cliente_id):
    """Adicionar cliente a um grupo"""
    try:
        grupo_id = request.form.get('grupo_id', type=int)
        
        if not grupo_id:
            flash('Grupo não selecionado!', 'danger')
            return redirect(url_for('clientes.detalhes', id=cliente_id))
        
        # Verificar se cliente existe
        cliente = Cliente.get_by_id(cliente_id)
        if not cliente:
            flash('Cliente não encontrado!', 'danger')
            return redirect(url_for('clientes.index'))
        
        # Adicionar cliente ao grupo
        sucesso = GrupoCliente.add_cliente(grupo_id, cliente_id)
        
        if sucesso:
            flash('Cliente adicionado ao grupo com sucesso!', 'success')
        else:
            flash('Erro ao adicionar cliente ao grupo!', 'danger')
    
    except Exception as e:
        flash(f'Erro ao adicionar cliente ao grupo: {str(e)}', 'danger')
    
    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/clientes/<int:cliente_id>/remover-grupo/<int:grupo_id>', methods=['POST'])
@login_required
def remover_grupo(cliente_id, grupo_id):
    """Remover cliente de um grupo"""
    try:
        # Verificar se cliente existe
        cliente = Cliente.get_by_id(cliente_id)
        if not cliente:
            flash('Cliente não encontrado!', 'danger')
            return redirect(url_for('clientes.index'))
        
        # Remover cliente do grupo
        sucesso = GrupoCliente.remove_cliente(grupo_id, cliente_id)
        
        if sucesso is not None:
            flash('Cliente removido do grupo com sucesso!', 'success')
        else:
            flash('Erro ao remover cliente do grupo!', 'danger')
    
    except Exception as e:
        flash(f'Erro ao remover cliente do grupo: {str(e)}', 'danger')
    
    return redirect(url_for('clientes.detalhes', id=cliente_id))


# API para busca de CEP
@clientes.route('/api/cep/<cep>')
@login_required
def buscar_cep(cep):
    """Buscar dados do CEP via API externa"""
    import requests
    try:
        response = requests.get(f'https://viacep.com.br/ws/{cep}/json/', timeout=5)
        if response.status_code == 200:
            return jsonify(response.json())
    except requests.RequestException as e:
        print(f"Erro ao buscar CEP: {e}")
    return jsonify({'erro': True}), 404


@clientes.route('/api/consultar-cnpj/<cnpj>')
@login_required
def consultar_cnpj(cnpj):
    """
    Consulta CNPJ na Receita Federal via Brasil API
    Retorna dados da empresa para preenchimento automático
    """
    import requests
    import re
    
    try:
        # Remover caracteres não numéricos
        cnpj_limpo = re.sub(r'\D', '', cnpj)
        
        # Validar tamanho
        if len(cnpj_limpo) != 14:
            return jsonify({
                'success': False,
                'message': 'CNPJ deve ter 14 dígitos'
            }), 400
        
        # Consultar Brasil API
        url = f'https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}'
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            # Extrair dados relevantes
            resultado = {
                'success': True,
                'data': {
                    'cnpj': data.get('cnpj', ''),
                    'razao_social': data.get('razao_social', ''),
                    'nome_fantasia': data.get('nome_fantasia', ''),
                    'situacao_cadastral': data.get('descricao_situacao_cadastral', ''),
                    'porte': data.get('porte', ''),
                    'natureza_juridica': data.get('natureza_juridica', ''),
                    'data_inicio_atividade': data.get('data_inicio_atividade', ''),
                    'cnae_fiscal': data.get('cnae_fiscal', ''),
                    'cnae_fiscal_descricao': data.get('cnae_fiscal_descricao', ''),
                    # Endereço
                    'logradouro': data.get('logradouro', ''),
                    'numero': data.get('numero', ''),
                    'complemento': data.get('complemento', ''),
                    'bairro': data.get('bairro', ''),
                    'municipio': data.get('municipio', ''),
                    'uf': data.get('uf', ''),
                    'cep': data.get('cep', ''),
                    # Contatos
                    'ddd_telefone_1': data.get('ddd_telefone_1', ''),
                    'ddd_telefone_2': data.get('ddd_telefone_2', ''),
                    'email': data.get('email', ''),
                    # QSAs (sócios)
                    'qsa': data.get('qsa', [])
                }
            }
            
            return jsonify(resultado), 200
        
        elif response.status_code == 404:
            return jsonify({
                'success': False,
                'message': 'CNPJ não encontrado na Receita Federal'
            }), 404
        
        else:
            return jsonify({
                'success': False,
                'message': 'Erro ao consultar CNPJ. Tente novamente.'
            }), 500
    
    except requests.Timeout:
        return jsonify({
            'success': False,
            'message': 'Timeout ao consultar CNPJ. Tente novamente.'
        }), 408
    
    except Exception as e:
        print(f"Erro ao consultar CNPJ: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Erro ao consultar CNPJ: {str(e)}'
        }), 500


# Aliases para manter compatibilidade
list_clientes = index
create_cliente = novo
view_cliente = detalhes
edit_cliente = editar
create = novo
view = detalhes
edit = editar

