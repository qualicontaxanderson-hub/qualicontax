"""Rotas de Clientes - CRUD completo"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from decimal import Decimal, InvalidOperation
from utils.auth_helper import login_required, permission_required
from models.cliente import Cliente
from models.endereco_cliente import EnderecoCliente
from models.contato_cliente import ContatoCliente, AREAS_ATENDIMENTO
from models.cadastro_adicional_cliente import CadastroAdicionalCliente
from models.socio_cliente import SocioCliente
from models.grupo_cliente import GrupoCliente
from models.ramo_atividade import RamoAtividade
from models.cadastro_anp import CadastroAnp

clientes = Blueprint('clientes', __name__)


def _parse_decimal_input(value):
    """Converte entrada numérica para Decimal aceitando formatos 10,50 e 10.50."""
    cleaned = (value or '').strip().replace(' ', '')
    if not cleaned:
        raise InvalidOperation

    if ',' in cleaned and '.' in cleaned:
        # Ex.: 1.234,56 (pt-BR) ou 1,234.56 (en-US)
        if cleaned.rfind(',') > cleaned.rfind('.'):
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    elif ',' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')

    return Decimal(cleaned)


@clientes.route('/clientes')
@permission_required('clientes.index')
def index():
    """Lista todos os clientes com filtros e paginação"""
    try:
        # Parâmetros de filtro
        situacao    = request.args.get('situacao', '')
        regime      = request.args.get('regime', '')
        tipo_pessoa = request.args.get('tipo_pessoa', '')
        grupo_id    = request.args.get('grupo_id', '')
        ramo_id     = request.args.get('ramo_id', '')
        busca       = request.args.get('busca', '')
        busca_tipo  = request.args.get('busca_tipo', 'nome')   # 'nome' | 'numero' | 'ramo'
        sort_by     = request.args.get('sort_by',  'numero')   # 'nome' | 'numero'
        sort_dir    = request.args.get('sort_dir', 'asc')      # 'asc'  | 'desc'
        page        = request.args.get('page', 1, type=int)
        per_page    = 50

        # Buscar clientes com filtros
        filters = {}
        if situacao:
            filters['situacao'] = situacao
        if regime:
            filters['regime_tributario'] = regime
        if tipo_pessoa:
            filters['tipo_pessoa'] = tipo_pessoa
        if grupo_id:
            filters['grupo_id'] = grupo_id
        if ramo_id:
            filters['ramo_id'] = ramo_id
        if busca:
            filters['busca']      = busca
            filters['busca_tipo'] = busca_tipo
        filters['sort_by']  = sort_by
        filters['sort_dir'] = sort_dir

        result = Cliente.get_all(filters=filters, page=page, per_page=per_page)
        grupos = GrupoCliente.get_all(situacao='ATIVO')
        ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')

        # Verificar se houve erro na obtenção dos dados
        if result is None:
            flash('Erro ao buscar clientes. Verifique a conexão com o banco de dados.', 'danger')
            result = {'clientes': [], 'page': 1, 'total_pages': 0, 'total': 0,
                      'stats': {'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0, 'pj': 0}}

        # stats is now bundled inside result (no separate DB call needed)
        stats = result.get('stats', {'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0, 'pj': 0})

        return render_template('clientes/index.html',
                             clientes=result['clientes'],
                             page=result['page'],
                             total_pages=result['total_pages'],
                              total=result['total'],
                              stats=stats,
                              grupos=grupos,
                              ramos_atividade=ramos_atividade,
                              sort_by=sort_by,
                              sort_dir=sort_dir,
                              filtros={'situacao': situacao, 'regime': regime,
                                       'tipo_pessoa': tipo_pessoa, 'grupo_id': grupo_id,
                                       'ramo_id': ramo_id, 'busca': busca,
                                       'busca_tipo': busca_tipo})
    
    except Exception as e:
        flash(f'Erro ao carregar página de clientes: {str(e)}', 'danger')
        return render_template('clientes/index.html',
                              clientes=[],
                             page=1,
                             total_pages=0,
                              total=0,
                              stats={'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0, 'pj': 0},
                              grupos=[],
                              ramos_atividade=[],
                              sort_by='numero',
                              sort_dir='asc',
                              filtros={'situacao': '', 'regime': '', 'tipo_pessoa': '',
                                       'grupo_id': '', 'ramo_id': '',
                                       'busca': '', 'busca_tipo': 'nome'})


@clientes.route('/clientes/novo', methods=['GET', 'POST'])
@login_required
def novo():
    """Criar novo cliente"""
    if request.method == 'POST':
        # The form has duplicate-named fields (PF + PJ sections both submit
        # 'cpf_cnpj' and 'nome_razao_social'). The hidden section sends an empty
        # value that appears first in getlist(); pick the first non-empty one.
        cpf_cnpj = next((v for v in request.form.getlist('cpf_cnpj') if v.strip()), '')
        nome_razao_social = next((v for v in request.form.getlist('nome_razao_social') if v.strip()), '')
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
        if not request.form.get('tipo_pessoa') or not nome_razao_social or not cpf_cnpj:
            flash('Preencha todos os campos obrigatórios.', 'danger')
            ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')
            return render_template('clientes/form.html', cliente=None, ramos_atividade=ramos_atividade, cliente_ramo=None)
        
        # Criar cliente
        data = {
            'numero_cliente': numero_cliente if numero_cliente else None,
            'tipo_pessoa': request.form.get('tipo_pessoa'),
            'nome_razao_social': nome_razao_social,
            'nome_fantasia': request.form.get('nome_fantasia'),
            'cpf_cnpj': cpf_cnpj,
            'inscricao_estadual': request.form.get('inscricao_estadual'),
            'inscricao_municipal': request.form.get('inscricao_municipal'),
            'email': request.form.get('email'),
            'telefone': request.form.get('telefone'),
            'celular': request.form.get('celular'),
            'regime_tributario': request.form.get('regime_tributario'),
            'porte_empresa': request.form.get('porte_empresa'),
            'cnae_fiscal': request.form.get('cnae_fiscal'),
            'cnae_fiscal_descricao': request.form.get('cnae_fiscal_descricao'),
            'situacao': request.form.get('situacao', 'ATIVO'),
            'data_inicio_atividade': request.form.get('data_inicio_atividade'),
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
            
            # Salvar endereço se fornecido
            cep = request.form.get('cep', '').strip()
            logradouro = request.form.get('logradouro', '').strip()
            
            if cep or logradouro:  # Se pelo menos um campo de endereço foi preenchido
                try:
                    endereco_data = {
                        'tipo': 'COMERCIAL',  # Padrão para PJ
                        'cep': cep,
                        'logradouro': logradouro,
                        'numero': request.form.get('numero', '').strip(),
                        'complemento': request.form.get('complemento', '').strip(),
                        'bairro': request.form.get('bairro', '').strip(),
                        'cidade': request.form.get('cidade', '').strip(),
                        'estado': request.form.get('estado', '').strip(),
                        'pais': 'Brasil',
                        'principal': True
                    }
                    
                    EnderecoCliente.create(
                        cliente_id=cliente_id,
                        tipo=endereco_data['tipo'],
                        cep=endereco_data['cep'],
                        logradouro=endereco_data['logradouro'],
                        numero=endereco_data['numero'],
                        complemento=endereco_data['complemento'],
                        bairro=endereco_data['bairro'],
                        cidade=endereco_data['cidade'],
                        estado=endereco_data['estado'],
                        pais=endereco_data['pais'],
                        principal=endereco_data['principal']
                    )
                except Exception as e:
                    # Se houver erro no endereço, não impede a criação do cliente
                    print(f"Erro ao salvar endereço: {e}")
            
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
    cadastros_adicionais = CadastroAdicionalCliente.get_by_cliente(id)
    socios = SocioCliente.get_by_cliente(id)
    # Compute the active-partner total in Python instead of a separate DB query
    socios_total_percentual = sum(
        float(s.get('percentual_participacao') or 0)
        for s in socios
        if s.get('ativo')
    )

    # Cadastros ANP vinculados ao cliente — batch-load socios/produtos to avoid N+1
    cadastros_anp = CadastroAnp.get_by_cliente(id)
    if cadastros_anp:
        anp_socios_map = CadastroAnp.get_socios_by_cliente(id)
        anp_produtos_map = CadastroAnp.get_produtos_by_cliente(id)
        for anp in cadastros_anp:
            anp['socios'] = anp_socios_map.get(anp['id'], [])
            anp['produtos'] = anp_produtos_map.get(anp['id'], [])
    
    grupos_disponiveis = []
    
    return render_template('clientes/detalhes.html',
                         cliente=cliente,
                         enderecos=enderecos,
                         contatos=contatos,
                         grupos=grupos,
                         grupos_disponiveis=grupos_disponiveis,
                         ramos_atividade=ramos_atividade,
                          processos=processos,
                          tarefas=tarefas,
                          obrigacoes=obrigacoes,
                         cadastros_adicionais=cadastros_adicionais,
                         cadastros_anp=cadastros_anp,
                         socios=socios,
                         socios_total_percentual=socios_total_percentual,
                          areas_atendimento=AREAS_ATENDIMENTO)


@clientes.route('/clientes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar(id):
    """Editar cliente"""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        flash('Cliente não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    enderecos_cliente = EnderecoCliente.get_by_cliente(id)
    endereco_principal = next((e for e in enderecos_cliente if e.get('principal')), enderecos_cliente[0] if enderecos_cliente else None)
    
    if request.method == 'POST':
        try:
            # The form has duplicate-named fields (PF + PJ sections both submit
            # 'cpf_cnpj' and 'nome_razao_social'). The hidden section sends an empty
            # value that appears first in getlist(); pick the first non-empty one.
            cpf_cnpj = next((v for v in request.form.getlist('cpf_cnpj') if v.strip()), '')
            nome_razao_social = next((v for v in request.form.getlist('nome_razao_social') if v.strip()), '')

            # Validação de campos obrigatórios
            if not request.form.get('tipo_pessoa') or not nome_razao_social or not cpf_cnpj:
                flash('Preencha todos os campos obrigatórios.', 'danger')
                ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')
                cliente_ramos = RamoAtividade.get_by_cliente(id)
                ramos_cliente = [ramo['id'] for ramo in cliente_ramos]
                grupos = GrupoCliente.get_all(situacao='ATIVO')
                grupos_cliente = Cliente.get_grupos(id)
                return render_template('clientes/form.html', cliente=cliente, grupos=grupos, grupos_cliente=grupos_cliente, ramos_atividade=ramos_atividade, ramos_cliente=ramos_cliente, endereco_principal=endereco_principal)
            
            # Validar número do cliente se fornecido
            numero_cliente = request.form.get('numero_cliente', '').strip()
            if numero_cliente and Cliente.existe_numero_cliente(numero_cliente, id):
                flash(f'Número do cliente "{numero_cliente}" já está em uso por outro cliente!', 'danger')
                ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')
                cliente_ramos = RamoAtividade.get_by_cliente(id)
                ramos_cliente = [ramo['id'] for ramo in cliente_ramos]
                grupos = GrupoCliente.get_all(situacao='ATIVO')
                grupos_cliente = Cliente.get_grupos(id)
                return render_template('clientes/form.html', cliente=cliente, grupos=grupos, grupos_cliente=grupos_cliente, ramos_atividade=ramos_atividade, ramos_cliente=ramos_cliente, endereco_principal=endereco_principal)
            
            data = {
                'numero_cliente': numero_cliente if numero_cliente else None,
                'tipo_pessoa': request.form.get('tipo_pessoa'),
                'nome_razao_social': nome_razao_social,
                'cpf_cnpj': cpf_cnpj,
                'inscricao_estadual': request.form.get('inscricao_estadual'),
                'inscricao_municipal': request.form.get('inscricao_municipal'),
                'email': request.form.get('email'),
                'telefone': request.form.get('telefone'),
                'celular': request.form.get('celular'),
                'regime_tributario': request.form.get('regime_tributario'),
                'porte_empresa': request.form.get('porte_empresa'),
                'cnae_fiscal': request.form.get('cnae_fiscal'),
                'cnae_fiscal_descricao': request.form.get('cnae_fiscal_descricao'),
                'situacao': request.form.get('situacao'),
                'data_inicio_atividade': request.form.get('data_inicio_atividade'),
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

                # Atualizar/salvar endereço principal
                cep = request.form.get('cep', '').strip()
                logradouro = request.form.get('logradouro', '').strip()
                numero = request.form.get('numero', '').strip()
                complemento = request.form.get('complemento', '').strip()
                bairro = request.form.get('bairro', '').strip()
                cidade = request.form.get('cidade', '').strip()
                estado = request.form.get('estado', '').strip()
                possui_dados_endereco = any([cep, logradouro, numero, complemento, bairro, cidade, estado])

                if possui_dados_endereco:
                    try:
                        if endereco_principal:
                            EnderecoCliente.update(
                                endereco_principal['id'],
                                endereco_principal.get('tipo') or 'COMERCIAL',
                                cep,
                                logradouro,
                                numero,
                                complemento=complemento,
                                bairro=bairro,
                                cidade=cidade,
                                estado=estado,
                                pais=endereco_principal.get('pais') or 'Brasil',
                                principal=True
                            )
                        else:
                            EnderecoCliente.create(
                                cliente_id=id,
                                tipo='COMERCIAL',
                                cep=cep,
                                logradouro=logradouro,
                                numero=numero,
                                complemento=complemento,
                                bairro=bairro,
                                cidade=cidade,
                                estado=estado,
                                pais='Brasil',
                                principal=True
                            )
                    except Exception as endereco_error:
                        print(f"Erro ao salvar endereço do cliente {id}: {endereco_error}")
                
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
    return render_template('clientes/form.html', cliente=cliente, grupos=grupos, grupos_cliente=grupos_cliente, ramos_atividade=ramos_atividade, ramos_cliente=ramos_cliente, endereco_principal=endereco_principal)


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
        areas_atendimento=request.form.getlist('areas_atendimento'),
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


# Rotas para Cadastros Adicionais
@clientes.route('/clientes/<int:cliente_id>/cadastros-adicionais/novo', methods=['POST'])
@login_required
def novo_cadastro_adicional(cliente_id):
    """Adicionar novo cadastro adicional"""
    tipo = request.form.get('tipo', '').strip()
    campo = request.form.get('campo', '').strip()

    if not tipo or not campo:
        flash('Preencha os campos obrigatórios de cadastro adicional.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    cadastro_id = CadastroAdicionalCliente.create(
        cliente_id=cliente_id,
        tipo=tipo,
        campo=campo,
        valor=request.form.get('valor'),
        data_referencia=request.form.get('data_referencia'),
        observacoes=request.form.get('observacoes'),
        ativo=True
    )

    if cadastro_id:
        flash('Cadastro adicional adicionado com sucesso!', 'success')
    else:
        flash('Erro ao adicionar cadastro adicional!', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/cadastros-adicionais/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_cadastro_adicional(id):
    """Excluir cadastro adicional"""
    cadastro = CadastroAdicionalCliente.get_by_id(id)
    if not cadastro:
        flash('Cadastro adicional não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))

    cliente_id = cadastro['cliente_id']
    sucesso = CadastroAdicionalCliente.delete(id)

    if sucesso:
        flash('Cadastro adicional excluído com sucesso!', 'success')
    else:
        flash('Erro ao excluir cadastro adicional!', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id))


# Rotas para Sócios
@clientes.route('/clientes/<int:cliente_id>/socios/novo', methods=['POST'])
@login_required
def novo_socio(cliente_id):
    """Adicionar novo sócio"""
    nome = request.form.get('nome', '').strip()
    cpf = request.form.get('cpf', '').strip()
    email = request.form.get('email', '').strip() or None
    telefone = request.form.get('telefone', '').strip() or None
    percentual_raw = (request.form.get('percentual_participacao') or '').strip()
    responsavel = request.form.get('responsavel') == 'on'

    if not nome or not cpf or not percentual_raw:
        flash('Preencha nome, CPF e percentual do sócio.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    cpf_numeros = ''.join(ch for ch in cpf if ch.isdigit())
    if len(cpf_numeros) != 11:
        flash('CPF inválido. Informe um CPF com 11 dígitos.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    try:
        percentual = _parse_decimal_input(percentual_raw)
    except (InvalidOperation, ValueError):
        flash('Percentual inválido. Use um número válido.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    if percentual <= 0 or percentual > 100:
        flash('Percentual deve ser maior que 0 e menor ou igual a 100.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    total_atual = SocioCliente.get_total_percentual(cliente_id)
    novo_total = total_atual + percentual
    if novo_total > Decimal('100'):
        restante = Decimal('100') - total_atual
        flash(f'Total de participação não pode ultrapassar 100%. Restante disponível: {restante:.2f}%.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    socio_id = SocioCliente.create(
        cliente_id=cliente_id,
        nome=nome,
        cpf=cpf,
        email=email,
        telefone=telefone,
        percentual_participacao=percentual,
        responsavel=responsavel,
        ativo=True
    )

    if socio_id:
        total_final = SocioCliente.get_total_percentual(cliente_id)
        if total_final == Decimal('100'):
            flash('Sócio adicionado com sucesso! Total de participação atingiu 100%.', 'success')
        else:
            flash(f'Sócio adicionado. Total atual de participação: {total_final:.2f}%.', 'warning')
    else:
        flash('Erro ao adicionar sócio!', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/socios/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_socio(id):
    """Excluir sócio"""
    socio = SocioCliente.get_by_id(id)
    if not socio:
        flash('Sócio não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))

    cliente_id = socio['cliente_id']
    sucesso = SocioCliente.delete(id)

    if sucesso:
        total_final = SocioCliente.get_total_percentual(cliente_id)
        flash(f'Sócio excluído com sucesso. Total atual de participação: {total_final:.2f}%.', 'success')
    else:
        flash('Erro ao excluir sócio!', 'danger')

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
            
            # DEBUG: Log para verificar dados completos
            print(f"=== DADOS RETORNADOS PELA BRASIL API ===")
            print(f"Email: {data.get('email')}")
            print(f"Correio Eletrônico: {data.get('correio_eletronico')}")
            print(f"Endereço Eletrônico: {data.get('endereco_eletronico')}")
            print(f"DDD Telefone 1: {data.get('ddd_telefone_1')}")
            print(f"DDD Telefone 2: {data.get('ddd_telefone_2')}")
            print(f"Razão Social: {data.get('razao_social')}")
            print(f"Data Início Atividade: {data.get('data_inicio_atividade')}")
            print(f"Inscrições Estaduais (raw): {data.get('inscricoes_estaduais')}")
            print(f"CNAE Fiscal: {data.get('cnae_fiscal')}")
            print(f"CNAEs Secundários: {data.get('cnaes_secundarios')}")
            print(f"CEP: {data.get('cep')}")
            print(f"Logradouro: {data.get('logradouro')}")
            print(f"Número: {data.get('numero')}")
            print(f"UF: {data.get('uf')}")
            
            # Extrair inscrição estadual (IE)
            inscricao_estadual = ''
            if 'inscricoes_estaduais' in data and isinstance(data['inscricoes_estaduais'], list):
                print(f"DEBUG: Total de IEs: {len(data['inscricoes_estaduais'])}")
                # Pegar a primeira IE ativa
                for idx, ie_obj in enumerate(data['inscricoes_estaduais']):
                    print(f"DEBUG: IE[{idx}] = {ie_obj} (tipo: {type(ie_obj)})")
                    if isinstance(ie_obj, dict):
                        ie_numero = ie_obj.get('inscricao_estadual', '')
                        ie_ativo = ie_obj.get('ativo', False)
                        print(f"DEBUG: IE número: {ie_numero}, ativo: {ie_ativo}")
                        if ie_ativo and ie_numero:
                            inscricao_estadual = ie_numero
                            print(f"DEBUG: IE ativa encontrada: {inscricao_estadual}")
                            break
                # Se não encontrou ativa, pega a primeira disponível
                if not inscricao_estadual and len(data['inscricoes_estaduais']) > 0:
                    ie_obj = data['inscricoes_estaduais'][0]
                    if isinstance(ie_obj, dict):
                        inscricao_estadual = ie_obj.get('inscricao_estadual', '')
                        print(f"DEBUG: IE primeira disponível: {inscricao_estadual}")
            elif 'inscricao_estadual' in data and data['inscricao_estadual']:
                # Às vezes vem direto como string
                inscricao_estadual = data['inscricao_estadual']
                print(f"DEBUG: IE como string direta: {inscricao_estadual}")
            
            print(f"Inscrição Estadual extraída: '{inscricao_estadual}'")
            
            # Extrair e-mail - tentar múltiplos campos possíveis
            # Brasil API pode retornar em diferentes formatos
            email = (data.get('email') or 
                    data.get('correio_eletronico') or 
                    data.get('endereco_eletronico') or  # NOVO: Campo "ENDEREÇO ELETRÔNICO"
                    data.get('email_principal') or 
                    '')
            if email:
                print(f"DEBUG: Email encontrado: {email}")
            
            # Extrair CNAEs secundários
            cnaes_secundarios = data.get('cnaes_secundarios', [])
            print(f"DEBUG: CNAEs Secundários encontrados: {len(cnaes_secundarios)}")
            if cnaes_secundarios:
                for idx, cnae in enumerate(cnaes_secundarios[:3]):  # Mostrar só primeiros 3 no log
                    print(f"DEBUG: CNAE Secundário[{idx}]: {cnae}")
            
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
                    'inscricao_estadual': inscricao_estadual,
                    'cnae_fiscal': data.get('cnae_fiscal', ''),
                    'cnae_fiscal_descricao': data.get('cnae_fiscal_descricao', ''),
                    'cnaes_secundarios': cnaes_secundarios,  # NOVO: CNAEs secundários
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
                    'email': email,  # MELHORADO: tenta múltiplos campos
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


# ---------------------------------------------------------------------------
# Rotas ANP
# ---------------------------------------------------------------------------

_MAX_ANP_PDF_MB = 20
_MAX_ANP_PDF_BYTES = _MAX_ANP_PDF_MB * 1024 * 1024


@clientes.route('/clientes/<int:cliente_id>/anp/importar-pdf', methods=['POST'])
@login_required
def anp_importar_pdf(cliente_id):
    """Recebe PDF da ANP, extrai dados e exibe formulário de confirmação."""
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))

    arquivo = request.files.get('arquivo_pdf')
    if not arquivo or not arquivo.filename:
        flash('Selecione um arquivo PDF.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    if not arquivo.filename.lower().endswith('.pdf'):
        flash('Apenas arquivos .pdf são aceitos.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    pdf_bytes = arquivo.read()
    if len(pdf_bytes) > _MAX_ANP_PDF_BYTES:
        flash(f'O arquivo excede o tamanho máximo de {_MAX_ANP_PDF_MB} MB.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    from utils.anp_parser import extrair_dados_anp
    resultado = extrair_dados_anp(pdf_bytes)

    if not resultado['sucesso']:
        flash(f'Erro ao processar PDF: {resultado["erro"]}', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    # Verifica se o CNPJ do PDF bate com o do cliente
    cnpj_pdf = resultado['cnpj']
    cnpj_cliente = ''.join(c for c in (cliente.get('cpf_cnpj') or '') if c.isdigit())
    cnpj_ok = (cnpj_pdf == cnpj_cliente) if cnpj_pdf and cnpj_cliente else None

    return render_template(
        'clientes/anp_preview.html',
        cliente=cliente,
        dados=resultado['dados'],
        socios=resultado['socios'],
        produtos=resultado['produtos'],
        texto_bruto=resultado['texto_bruto'],
        cnpj_ok=cnpj_ok,
        cnpj_pdf=cnpj_pdf,
    )


@clientes.route('/clientes/<int:cliente_id>/anp/salvar', methods=['POST'])
@login_required
def anp_salvar(cliente_id):
    """Salva (cria ou atualiza) cadastro ANP a partir do formulário."""
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))

    anp_id_raw = request.form.get('anp_id')
    anp_id = int(anp_id_raw) if anp_id_raw and anp_id_raw.isdigit() else None

    data = {
        'situacao': request.form.get('situacao', '').strip() or None,
        'autorizacao': request.form.get('autorizacao', '').strip() or None,
        'cnpj_anp': request.form.get('cnpj_anp', '').strip() or None,
        'razao_social': request.form.get('razao_social', '').strip() or None,
        'nome_fantasia': request.form.get('nome_fantasia', '').strip() or None,
        'endereco': request.form.get('endereco', '').strip() or None,
        'complemento': request.form.get('complemento', '').strip() or None,
        'bairro': request.form.get('bairro', '').strip() or None,
        'municipio_uf': request.form.get('municipio_uf', '').strip() or None,
        'cep': request.form.get('cep', '').strip() or None,
        'nr_despacho': request.form.get('nr_despacho', '').strip() or None,
        'data_publicacao': request.form.get('data_publicacao') or None,
        'bandeira': request.form.get('bandeira', '').strip() or None,
        'data_inicio_bandeira': request.form.get('data_inicio_bandeira') or None,
        'tipo_posto': request.form.get('tipo_posto', '').strip() or None,
        'pmqc': request.form.get('pmqc', '').strip() or None,
        'delivery': request.form.get('delivery', '').strip() or None,
        'latitude': request.form.get('latitude', '').strip() or None,
        'longitude': request.form.get('longitude', '').strip() or None,
        'data_emissao': request.form.get('data_emissao', '').strip() or None,
        'fonte': request.form.get('fonte', 'MANUAL'),
    }

    # Sócios: cada linha não-vazia
    socios_raw = request.form.get('socios_json', '')
    import json
    try:
        socios = [s for s in json.loads(socios_raw) if s.strip()] if socios_raw else []
    except (json.JSONDecodeError, TypeError):
        socios = []

    # Produtos
    produtos_json = request.form.get('produtos_json', '')
    try:
        produtos = json.loads(produtos_json) if produtos_json else []
    except (json.JSONDecodeError, TypeError):
        produtos = []

    saved_id = CadastroAnp.save_full(
        cliente_id=cliente_id,
        data=data,
        socios=socios,
        produtos=produtos,
        anp_id=anp_id,
    )

    if saved_id:
        flash('Cadastro ANP salvo com sucesso!', 'success')
    else:
        flash('Erro ao salvar cadastro ANP.', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id) + '#cadastros-adicionais')


@clientes.route('/clientes/<int:cliente_id>/anp/<int:anp_id>')
@login_required
def anp_detalhe(cliente_id, anp_id):
    """Exibe detalhes completos de um cadastro ANP."""
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))

    anp = CadastroAnp.get_by_id(anp_id)
    if not anp or anp['cliente_id'] != cliente_id:
        flash('Cadastro ANP não encontrado.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    socios = CadastroAnp.get_socios(anp_id)
    produtos = CadastroAnp.get_produtos(anp_id)

    return render_template(
        'clientes/anp_detalhe.html',
        cliente=cliente,
        anp=anp,
        socios=socios,
        produtos=produtos,
    )


@clientes.route('/clientes/<int:cliente_id>/anp/<int:anp_id>/editar', methods=['GET'])
@login_required
def anp_editar(cliente_id, anp_id):
    """Formulário de edição de cadastro ANP (pré-preenchido)."""
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))

    anp = CadastroAnp.get_by_id(anp_id)
    if not anp or anp['cliente_id'] != cliente_id:
        flash('Cadastro ANP não encontrado.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    socios = [s['nome'] for s in CadastroAnp.get_socios(anp_id)]
    produtos = CadastroAnp.get_produtos(anp_id)

    return render_template(
        'clientes/anp_form.html',
        cliente=cliente,
        anp=anp,
        socios=socios,
        produtos=produtos,
        modo='editar',
    )


@clientes.route('/clientes/<int:cliente_id>/anp/<int:anp_id>/excluir', methods=['POST'])
@login_required
def anp_excluir(cliente_id, anp_id):
    """Exclui cadastro ANP."""
    anp = CadastroAnp.get_by_id(anp_id)
    if not anp or anp['cliente_id'] != cliente_id:
        flash('Cadastro ANP não encontrado.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    CadastroAnp.delete(anp_id)
    flash('Cadastro ANP excluído com sucesso.', 'success')
    return redirect(url_for('clientes.detalhes', id=cliente_id) + '#cadastros-adicionais')


@clientes.route('/clientes/<int:cliente_id>/anp/novo', methods=['GET'])
@login_required
def anp_novo(cliente_id):
    """Formulário de cadastro ANP manual (em branco)."""
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))

    return render_template(
        'clientes/anp_form.html',
        cliente=cliente,
        anp=None,
        socios=[],
        produtos=[],
        modo='novo',
    )


# Aliases para manter compatibilidade
list_clientes = index
create_cliente = novo
view_cliente = detalhes
edit_cliente = editar
create = novo
view = detalhes
edit = editar
