"""Rotas de Clientes - CRUD completo"""
import re
import logging
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from decimal import Decimal, InvalidOperation
from utils.auth_helper import login_required, permission_required
from utils.atividade import registrar, rotulo_empresa
from utils.auditoria_fmt import AUDITORIA_INICIO, hist_preparar
from utils.form_helpers import limpar_form
from utils.db_helper import execute_query
from models.cliente import Cliente
from models.endereco_cliente import EnderecoCliente
from models.contato_cliente import ContatoCliente, AREAS_ATENDIMENTO
from models.cadastro_adicional_cliente import CadastroAdicionalCliente
from models.socio_cliente import SocioCliente
from models.grupo_cliente import GrupoCliente
from models.ramo_atividade import RamoAtividade
from models.cadastro_anp import CadastroAnp
from models.dfe_certificado import DfeCertificado
from models.cliente_contador import ClienteContador
from utils import dropbox_sync
from utils.certificado_digital import (
    abrir_certificado, cifrar_senha,
    SenhaInvalidaError, DocumentoNaoEncontradoError, CertificadoError,
)

clientes = Blueprint('clientes', __name__)
logger = logging.getLogger(__name__)


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
                      'stats': {'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0, 'pj': 0,
                                'com_certificado': 0, 'cert_vencendo': 0}}

        # stats is now bundled inside result (no separate DB call needed)
        stats = result.get('stats', {'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0,
                                     'pj': 0, 'com_certificado': 0, 'cert_vencendo': 0})

        # Nível do certificado por cliente para a coluna Certificado — MESMA regra
        # única do classificar_validade (verde 'ok' / âmbar 'laranja' / vermelho
        # 'vencido'); 'sem_data' quando não há certificado. A tela nunca mostra
        # vazio: sem cert vira "sem certificado" em cinza.
        for c in result['clientes']:
            c['cert_nivel'] = DfeCertificado.classificar_validade(
                c.get('cert_validade')).get('nivel', 'sem_data')

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
                              stats={'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0, 'pj': 0,
                                     'com_certificado': 0, 'cert_vencendo': 0},
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
            'aberta_pela_casa': 1 if request.form.get('aberta_pela_casa') else 0,
            'criado_por': current_user.id
        }
        # E1: 'None'/'null'/'' de qualquer caminho viram VAZIO antes de gravar.
        data = limpar_form(data)

        cliente_id = Cliente.create(data)

        if cliente_id:
            # AUDITORIA (D2): quem criou este cadastro.
            registrar('escrita.criou_cliente', 'cadastros', tabela='clientes',
                      registro_id=cliente_id, depois=data)

            # Marca de CONTADOR (fora do INSERT: a lista de colunas do create()
            # é explícita e não inclui is_contador).
            if request.form.get('is_contador'):
                ClienteContador.marcar_como_contador(cliente_id, 1)

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

    # AUDITORIA (D2): leitura — alguém abriu a ficha deste cliente de propósito.
    registrar('leitura.abriu_ficha_cliente', 'cadastros', tabela='clientes',
              registro_id=id, depois={'numero': cliente.get('numero_cliente'),
                                       'nome': cliente.get('nome_razao_social')})

    with ThreadPoolExecutor(max_workers=11) as executor:
        f_enderecos = executor.submit(EnderecoCliente.get_by_cliente, id)
        f_contatos = executor.submit(ContatoCliente.get_by_cliente, id)
        f_grupos = executor.submit(Cliente.get_grupos, id)
        f_ramos = executor.submit(RamoAtividade.get_by_cliente, id)
        f_processos = executor.submit(Cliente.get_processos, id)
        f_tarefas = executor.submit(Cliente.get_tarefas, id)
        f_obrigacoes = executor.submit(Cliente.get_obrigacoes, id)
        f_cadastros_ad = executor.submit(CadastroAdicionalCliente.get_by_cliente, id)
        f_socios = executor.submit(SocioCliente.get_by_cliente, id)
        f_certificado = executor.submit(DfeCertificado.get_by_cliente, id)
        f_contadores = executor.submit(ClienteContador.contadores_do_cliente, id)
        f_contadores_disp = executor.submit(ClienteContador.listar_contadores)

    enderecos = f_enderecos.result()
    contatos = f_contatos.result()
    grupos = f_grupos.result()
    ramos_atividade = f_ramos.result()
    processos = f_processos.result()
    tarefas = f_tarefas.result()
    obrigacoes = f_obrigacoes.result()
    cadastros_adicionais = f_cadastros_ad.result()
    socios = f_socios.result()
    certificado = f_certificado.result()
    contadores = f_contadores.result()
    # Dropdown "adicionar": só is_contador=1, menos os JÁ VINCULADOS e o PRÓPRIO
    # cadastro (um cadastro não é contador de si mesmo). Compara sempre c['id']
    # (int) com o id da rota — nunca o dict inteiro.
    _ja = {c['id'] for c in contadores}
    contadores_disponiveis = [c for c in f_contadores_disp.result()
                              if c['id'] != id and c['id'] not in _ja]
    # Classificação da validade p/ a aba Certificado Digital (VENCIDO/vencendo/OK).
    if certificado:
        certificado['cert_status'] = DfeCertificado.classificar_validade(
            certificado.get('validade'))
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
                         certificado=certificado,
                         contadores=contadores,
                         contadores_disponiveis=contadores_disponiveis,
                         socios_total_percentual=socios_total_percentual,
                          areas_atendimento=AREAS_ATENDIMENTO)


# ---------------------------------------------------------------------------
# Certificado Digital (.pfx) — busca na _ENTRADA e vínculo por empresa
# ---------------------------------------------------------------------------
def _tokens_certificado(cliente):
    """Tokens que identificam a empresa no nome do .pfx: número e CNPJ (14 díg.)."""
    tokens = []
    numero = (cliente.get('numero_cliente') or '').strip()
    cnpj = re.sub(r'\D', '', cliente.get('cpf_cnpj') or '')
    if numero:
        tokens.append(numero)
    if cnpj:
        tokens.append(cnpj)
    return tokens


# Separadores aceitos entre o NÚMERO da empresa e o resto do nome do arquivo.
_CERT_SEPARADORES = ('-', '_', ' ', '.')


def _nome_base_pfx(nome):
    """Nome do arquivo sem a extensão .pfx final (case-insensitive)."""
    return nome[:-4] if nome.lower().endswith('.pfx') else nome


def _nome_casa_numero(nome, numero):
    """REGRA 1 — o nome COMEÇA com o número da empresa: o número EXATO, ou o
    número seguido de um separador (``-``, ``_``, espaço ou ``.``).

    O separador obrigatório evita casamento de prefixo ambíguo: a empresa 51 NÃO
    casa com "515-....pfx" (depois de "51" vem "5", não um separador). É prefixo
    de propósito — número no meio do nome não vale, senão "Venc 04.09.26" casaria
    com a empresa 4, a 9 e a 26.
    """
    if not numero:
        return False
    base_l = _nome_base_pfx(nome).lower()
    tok_l = str(numero).lower()
    if base_l == tok_l:
        return True
    return (base_l.startswith(tok_l)
            and len(base_l) > len(tok_l)
            and base_l[len(tok_l)] in _CERT_SEPARADORES)


def _nome_casa_documento(nome, doc):
    """REGRA 2 — o nome CONTÉM o documento da empresa em QUALQUER posição.

    Só dígitos crus (CNPJ com 14, CPF com 11), com FRONTEIRA DE DÍGITOS: o
    documento não pode ser pedaço de uma sequência numérica maior. Sem essa
    fronteira, um nome com a chave de 44 dígitos de uma NF-e casaria com
    qualquer empresa cujo CNPJ aparecesse por acaso lá dentro.

    Diferente da regra do número, aqui é "contém" e não "prefixo": o CNPJ é
    identificador único, então não há o risco de ambiguidade que o prefixo do
    número tem. Isso faz "BRR BLINDAGENS 28472667000106.pfx" ser encontrado.
    """
    if not doc or len(doc) not in (11, 14):
        return False
    return re.search(rf'(?<!\d){re.escape(doc)}(?!\d)',
                     _nome_base_pfx(nome)) is not None


def _nome_casa_empresa(nome, cliente):
    """Regra de nome do .pfx: REGRA 1 (número como prefixo) OU REGRA 2 (documento
    contido). Fora disso, o arquivo NÃO é lido."""
    numero = (cliente.get('numero_cliente') or '').strip()
    doc = re.sub(r'\D', '', cliente.get('cpf_cnpj') or '')
    return _nome_casa_numero(nome, numero) or _nome_casa_documento(nome, doc)


def _localizar_certificados_novo(svc, cliente):
    """Lista os .pfx da _ENTRADA que casam com a empresa: NÚMERO como prefixo
    (+ separador) OU DOCUMENTO contido em qualquer posição — ver
    ``_nome_casa_empresa``. Extensão .pfx/.PFX aceita (case-insensitive).

    Retorna a LISTA de itens casados (0, 1 ou vários). A busca é sempre refeita
    no servidor (não confia em caminho vindo do cliente). O chamador decide: 0 =
    não encontrado; 1 = usa; vários = recusa (não escolhe o certificado sozinho).
    """
    if not _tokens_certificado(cliente):
        return []
    achados = []
    for item in svc.list_folder(svc.pasta_cert_novo()):
        nome = item.get('name') or ''
        if not item.get('is_file') or not nome.lower().endswith('.pfx'):
            continue
        if _nome_casa_empresa(nome, cliente):
            achados.append(item)
    return achados


def _erro_ambiguidade_certificado(achados):
    """Mensagem quando mais de um .pfx casa com a empresa — lista quais, não escolhe."""
    nomes = ', '.join(sorted(i.get('name', '?') for i in achados))
    return ('Mais de um certificado casa com esta empresa na _ENTRADA: '
            f'{nomes}. Deixe só o arquivo correto na pasta e tente de novo.')


@clientes.route('/clientes/<int:id>/certificado/buscar', methods=['POST'])
@login_required
def certificado_buscar(id):
    """Procura sozinho o .pfx da empresa na _ENTRADA (sem seleção manual)."""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        return jsonify({'ok': False, 'erro': 'Cliente não encontrado.'}), 404

    svc = dropbox_sync._service
    if not svc.is_configured():
        return jsonify({'ok': False, 'erro': 'Dropbox não configurado. '
                        'Defina DROPBOX_APP_KEY e DROPBOX_REFRESH_TOKEN.'}), 400

    if not _tokens_certificado(cliente):
        return jsonify({'ok': False, 'erro': 'Empresa sem número nem CNPJ para localizar o certificado.'}), 400

    try:
        achados = _localizar_certificados_novo(svc, cliente)
    except dropbox_sync.DropboxAuthError:
        return jsonify({'ok': False, 'erro': 'Credenciais Dropbox inválidas ou expiradas.'}), 401
    except dropbox_sync.DropboxError:
        return jsonify({'ok': False, 'erro': 'Erro ao acessar o Dropbox. Verifique a conexão.'}), 502

    if len(achados) > 1:
        return jsonify({'ok': False, 'erro': _erro_ambiguidade_certificado(achados)}), 200
    if not achados:
        return jsonify({'ok': True, 'encontrado': False,
                        'erro': 'Certificado não encontrado na pasta _ENTRADA.'}), 200

    return jsonify({'ok': True, 'encontrado': True, 'arquivo': achados[0]['name']}), 200


def _fmt_cnpj(doc):
    """Formata 14 dígitos como CNPJ (XX.XXX.XXX/XXXX-XX); devolve cru se não for 14."""
    d = re.sub(r'\D', '', str(doc or ''))
    if len(d) != 14:
        return d
    return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'


def _matriz_ou_filial(doc):
    """'matriz' se a ordem do estabelecimento (dígitos 9–12) for 0001, senão 'filial'."""
    d = re.sub(r'\D', '', str(doc or ''))
    return 'matriz' if len(d) == 14 and d[8:12] == '0001' else 'filial'


@clientes.route('/clientes/<int:id>/certificado/vincular', methods=['POST'])
@login_required
def certificado_vincular(id):
    """Valida a senha, confere o titular, move p/ IMPORTADOS e grava o vínculo."""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        return jsonify({'ok': False, 'erro': 'Cliente não encontrado.'}), 404

    data = request.get_json(silent=True) or request.form
    senha = (data.get('senha') or '').strip()
    if not senha:
        return jsonify({'ok': False, 'erro': 'Informe a senha do certificado.'}), 400

    # Fase 2: vínculo por PROCURAÇÃO (e-CPF do contador / terceiro autorizado).
    # Só surte efeito quando o titular do .pfx NÃO for a empresa (passo 4); no
    # caminho normal (titular == empresa) o checkbox é ignorado de propósito.
    procuracao_pedida = str(data.get('procuracao') or '').strip().lower() in (
        '1', 'true', 'on', 'sim')

    svc = dropbox_sync._service
    if not svc.is_configured():
        return jsonify({'ok': False, 'erro': 'Dropbox não configurado.'}), 400

    # 1) Reencontra o arquivo na _ENTRADA (não confia em path do cliente).
    try:
        achados = _localizar_certificados_novo(svc, cliente)
    except dropbox_sync.DropboxAuthError:
        return jsonify({'ok': False, 'erro': 'Credenciais Dropbox inválidas ou expiradas.'}), 401
    except dropbox_sync.DropboxError:
        return jsonify({'ok': False, 'erro': 'Erro ao acessar o Dropbox. Verifique a conexão.'}), 502
    if len(achados) > 1:
        return jsonify({'ok': False, 'erro': _erro_ambiguidade_certificado(achados)}), 200
    if not achados:
        return jsonify({'ok': False, 'erro': 'Certificado não encontrado na pasta _ENTRADA.'}), 200
    item = achados[0]

    # 2) Baixa o .pfx em memória.
    raw = svc.download_file(item['path'])
    if raw is None:
        return jsonify({'ok': False, 'erro': 'Falha ao baixar o certificado do Dropbox.'}), 502

    # 3) Valida a senha e extrai o documento do titular.
    try:
        info = abrir_certificado(raw, senha)
    except SenhaInvalidaError:
        return jsonify({'ok': False, 'erro': 'Senha do certificado incorreta.'}), 200
    except DocumentoNaoEncontradoError:
        return jsonify({'ok': False, 'erro': 'Não foi possível identificar o CNPJ/CPF no certificado.'}), 200
    except CertificadoError as exc:
        return jsonify({'ok': False, 'erro': f'Certificado inválido: {exc}'}), 200

    # 3b) BLOQUEIO: certificado VENCIDO não vincula — vincular só criaria um vínculo
    #     que já nasce quebrado (a SEFAZ recusa o mTLS com HTTP 403). Só bloqueia o
    #     vencido; um cert válido, mesmo vencendo em breve (laranja), vincula normal.
    if info['validade'] and DfeCertificado.classificar_validade(
            info['validade'])['nivel'] == 'vencido':
        return jsonify({'ok': False,
                        'erro': f'Certificado vencido em {info["validade"].strftime("%d/%m/%Y")} '
                                '— não é possível vincular um certificado vencido. '
                                'Renove e tente de novo.'}), 200

    # 4) Confere se o certificado é mesmo dessa empresa.
    #    O e-CNPJ é emitido para a RAIZ (8 primeiros dígitos) e cobre as filiais.
    #    Match exato → vincula normal. Match SÓ na raiz (matriz cobrindo filial) →
    #    vincula, mas com AVISO explícito (flexibilização consciente da regra:
    #    quero ver na tela p/ não vincular na empresa errada). Raiz diferente →
    #    recusa — SALVO se o vínculo foi pedido como PROCURAÇÃO. CPF (11 díg.)
    #    exige igualdade exata (sem lógica de raiz).
    #
    #    ORDEM DOS RAMOS (importa):
    #      a) doc_cert == doc_empresa  -> caminho NORMAL, intocado. Cobre PJ+e-CNPJ
    #         (14 díg.) e PF+e-CPF (11 díg.) igualmente: a comparação é entre
    #         strings de dígitos, sem olhar tipo_doc nem comprimento.
    #      b) procuração                -> AGNÓSTICA DE TIPO. Só "titular ≠ empresa
    #         E procuracao=1". Vale e-CPF (contador) e e-CNPJ (escritório).
    #      c) raiz                      -> CNPJ-only, como sempre foi.
    doc_cert = info['documento']
    doc_empresa = re.sub(r'\D', '', cliente.get('cpf_cnpj') or '')
    aviso = None
    procuracao = 0
    if doc_cert != doc_empresa:
        if procuracao_pedida:
            # PROCURAÇÃO: titular de terceiro é o caso de uso, não o erro.
            # SEM checagem de tipo_doc/comprimento de propósito — e-CPF (11) e
            # e-CNPJ (14) são igualmente válidos como certificado de terceiro.
            #
            # Guarda: a empresa PRECISA ter CPF/CNPJ próprio. Sem ele,
            # dfe_captura.py (`cliente.cpf_cnpj or vinc.cnpj`) cairia no FALLBACK
            # e consultaria a SEFAZ com o documento do CONTADOR — trazendo os
            # documentos dele para dentro desta empresa.
            if not doc_empresa:
                return jsonify({'ok': False,
                                'erro': 'Esta empresa está sem CPF/CNPJ cadastrado. '
                                        'Cadastre o documento antes de vincular um '
                                        'certificado de procuração — sem ele a captura '
                                        'consultaria a SEFAZ com o documento do titular '
                                        'do certificado, e não com o desta empresa.'}), 200
            procuracao = 1
            aviso = (f'Certificado de TERCEIRO (procuração) — titular {doc_cert} '
                     f'≠ empresa {doc_empresa}. Confira.')
        else:
            raiz_ok = (info['tipo_doc'] == 'CNPJ'
                       and len(doc_cert) == 14 and len(doc_empresa) == 14
                       and doc_cert[:8] == doc_empresa[:8])
            if not raiz_ok:
                return jsonify({'ok': False,
                                'erro': 'O certificado da pasta não é dessa empresa '
                                        f'(certificado: {doc_cert} · empresa: {doc_empresa or "sem CNPJ"}).'}), 200
            aviso = (f'Certificado da {_matriz_ou_filial(doc_cert)} {_fmt_cnpj(doc_cert)} '
                     f'vinculado à {_matriz_ou_filial(doc_empresa)} {_fmt_cnpj(doc_empresa)} '
                     f'(mesma raiz — o e-CNPJ da matriz cobre as filiais).')

    # 5) Cifra a senha ANTES de mover (se falhar aqui, o .pfx continua em NOVO).
    try:
        senha_cifrada = cifrar_senha(senha)
    except CertificadoError as exc:
        return jsonify({'ok': False, 'erro': str(exc)}), 500

    # 6) Move + renomeia para CERTIFICADO/{numero} - {razão}/{CNPJ}.pfx
    origem = item['path']
    numero = (cliente.get('numero_cliente') or '').strip() or None
    razao = cliente.get('nome_razao_social') or 'SEM_NOME'
    pasta_dest = svc.pasta_cert_importados(razao, numero)
    destino = f'{pasta_dest}/{doc_cert}.pfx'
    svc.ensure_folder(pasta_dest)

    # 6a) RENOVAÇÃO: preserva o .pfx anterior desta empresa ANTES de mover o novo —
    #     NUNCA apaga, NUNCA sobrescreve (move_file sobrescreve por padrão, então
    #     uma renovação de mesmo CNPJ perderia o antigo). O arquivo antigo é
    #     renomeado para ``substituido{CNPJ}.venc.{dd.mm.aaaa}.pfx``, com o
    #     VENCIMENTO do cert antigo. A validade vem do banco (foi lida do próprio
    #     .pfx quando ele foi vinculado; reabri-lo aqui exigiria a senha dele, que
    #     não temos). Numa 2ª renovação, sufixa .2, .3… para não colidir.
    #     Lê ``anterior`` ANTES do move (o upsert do passo 7 substitui o registro).
    anterior = DfeCertificado.get_by_cliente(id)

    def _pasta_de(p):
        return p.rsplit('/', 1)[0] if '/' in p else ''

    a_preservar = []
    if anterior:
        _ap = (anterior.get('dropbox_path') or '').strip()
        if _ap and _pasta_de(_ap).lower() == pasta_dest.lower():
            a_preservar.append((_ap, anterior.get('cnpj') or doc_cert, anterior.get('validade')))
    # arquivo já no destino (mesmo nome do novo) que o move sobrescreveria
    if svc._path_exists(destino) and all(destino.lower() != p.lower() for p, _, _ in a_preservar):
        a_preservar.append((destino, doc_cert, anterior.get('validade') if anterior else None))

    renamed = []
    for _old, _cnpj_old, _val in a_preservar:
        if not svc._path_exists(_old):
            continue
        _data = _val.strftime('%d.%m.%Y') if _val else 'sem_venc'
        _base = f'{pasta_dest}/substituido{_cnpj_old}.venc.{_data}'
        _novo = f'{_base}.pfx'
        _k = 2
        while svc._path_exists(_novo):
            _novo = f'{_base}.{_k}.pfx'
            _k += 1
        try:
            if not svc.move_file(_old, _novo):
                return jsonify({'ok': False, 'erro': 'Falha ao arquivar o certificado anterior no Dropbox.'}), 502
        except dropbox_sync.DropboxAuthError:
            return jsonify({'ok': False, 'erro': 'Credenciais Dropbox inválidas ou expiradas.'}), 401
        renamed.append((_old, _novo))

    # 6b) Move o novo .pfx com o nome padrão {CNPJ}.pfx (NUNCA a senha no nome).
    #     O destino agora está livre (o anterior, se havia, virou substituido...).
    try:
        if not svc.move_file(origem, destino):
            return jsonify({'ok': False, 'erro': 'Falha ao mover o certificado no Dropbox.'}), 502
    except dropbox_sync.DropboxAuthError:
        return jsonify({'ok': False, 'erro': 'Credenciais Dropbox inválidas ou expiradas.'}), 401

    # 6c) Mensagem p/ a tela: qual cert saiu de cena e como foi preservado.
    substituicao = None
    if anterior and renamed:
        _val = anterior.get('validade')
        _nome_arq = renamed[0][1].rsplit('/', 1)[-1]
        substituicao = (
            f'Substituiu o certificado anterior desta empresa '
            f'({anterior.get("tipo_doc")} {anterior.get("cnpj")}'
            + (f', válido até {_val.strftime("%d/%m/%Y")}' if _val else '')
            + f'). O arquivo antigo foi PRESERVADO no Dropbox como {_nome_arq} '
              f'(nunca apagado, nunca sobrescrito).')

    # 7) Grava o vínculo. Se falhar, tenta devolver o .pfx para NOVO (sem órfão);
    #    se nem a reversão der certo, loga o caminho para recuperação manual.
    ok = DfeCertificado.upsert(
        cliente_id=id, cnpj=doc_cert, tipo_doc=info['tipo_doc'],
        senha_cifrada=senha_cifrada, dropbox_path=destino, validade=info['validade'],
        procuracao=procuracao,
    )
    if not ok:
        revertido = False
        try:
            revertido = svc.move_file(destino, origem)
        except Exception as exc:
            logger.error('Falha ao reverter move do certificado %s -> %s: %s',
                         destino, origem, exc)
        # Desfaz a preservação do cert anterior (substituido... -> nome original):
        # o dropbox_path do banco (que não mudou, pois o upsert falhou) aponta para
        # o nome antigo, então ele precisa voltar a existir.
        for _orig, _novo in reversed(renamed):
            try:
                svc.move_file(_novo, _orig)
            except Exception:
                logger.error('Falha ao desfazer preservação do cert anterior %s -> %s',
                             _novo, _orig)
        if revertido:
            logger.error('Vínculo do certificado NÃO gravado (cliente_id=%s). '
                         'Arquivo devolvido para NOVO: %s', id, origem)
            return jsonify({'ok': False, 'erro': 'Falha ao gravar o vínculo no banco. '
                            'O certificado foi devolvido para a pasta _ENTRADA — tente novamente.'}), 500
        logger.error('Vínculo do certificado NÃO gravado (cliente_id=%s) e reversão FALHOU. '
                     'ARQUIVO ÓRFÃO em: %s', id, destino)
        return jsonify({'ok': False, 'erro': 'Falha ao gravar o vínculo no banco. '
                        f'Atenção: o arquivo ficou em {destino} — avise o suporte.'}), 500

    # AUDITORIA (D2): vinculou/trocou certificado. senha_cifrada e dropbox_path
    # estão na lista negra — entram só pelo NOME, nunca o valor (nada de dentro
    # do .pfx). Registra a troca (antes = cert anterior, se houver).
    _antes_cert = None
    if anterior:
        _antes_cert = {'cnpj': anterior.get('cnpj'), 'validade': anterior.get('validade'),
                       'senha_cifrada': '(anterior)', 'dropbox_path': anterior.get('dropbox_path')}
    registrar('escrita.vinculou_certificado', 'cadastros', tabela='dfe_certificados',
              registro_id=id, antes=_antes_cert,
              depois={'cnpj': doc_cert, 'tipo_doc': info['tipo_doc'],
                      'validade': info['validade'], 'procuracao': procuracao,
                      'senha_cifrada': '(nova)', 'dropbox_path': destino})

    return jsonify({
        'ok': True,
        'documento': doc_cert,
        'tipo_doc': info['tipo_doc'],
        'procuracao': procuracao,
        'validade': info['validade'].isoformat() if info['validade'] else None,
        'arquivo': f'{doc_cert}.pfx',
        'aviso': aviso,                 # vínculo por raiz (matriz→filial) OU procuração
        'substituicao': substituicao,   # preenchido só quando trocou um cert anterior
    }), 200


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
                'observacoes': request.form.get('observacoes'),
                'aberta_pela_casa': 1 if request.form.get('aberta_pela_casa') else 0,
                'alterado_por': current_user.id      # AUDITORIA (D2): quem alterou
            }
            # E1: 'None'/'null'/'' de qualquer caminho viram VAZIO antes de gravar.
            data = limpar_form(data)

            sucesso = Cliente.update(id, data)

            # Check if sucesso is not None (None indicates error, 0 or positive number indicates success)
            if sucesso is not None:
                # AUDITORIA (D2): grava só os campos de negócio que mudaram
                # (o helper calcula o diff; alterado_por/criado_por ficam fora).
                _campos_log = ('numero_cliente', 'tipo_pessoa', 'nome_razao_social',
                               'cpf_cnpj', 'inscricao_estadual', 'inscricao_municipal',
                               'email', 'telefone', 'celular', 'regime_tributario',
                               'porte_empresa', 'cnae_fiscal', 'cnae_fiscal_descricao',
                               'situacao', 'observacoes', 'aberta_pela_casa')
                registrar('escrita.alterou_cliente', 'cadastros', tabela='clientes',
                          registro_id=id,
                          antes={k: cliente.get(k) for k in _campos_log},
                          depois={k: data.get(k) for k in _campos_log})
                # Marca de CONTADOR (fora do UPDATE, que tem lista de colunas
                # explícita). Desmarcar pode ser RECUSADO se o cadastro ainda
                # for contador de alguém.
                r_cont = ClienteContador.marcar_como_contador(
                    id, 1 if request.form.get('is_contador') else 0)
                if not r_cont.get('ok') and r_cont.get('erro'):
                    flash(r_cont['erro'], 'warning')

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
        registrar('escrita.criou_endereco', 'cadastros', tabela='enderecos_clientes',
                  registro_id=endereco_id,
                  depois={'cliente_id': cliente_id, 'tipo': request.form.get('tipo'),
                          'cep': request.form.get('cep'), 'logradouro': request.form.get('logradouro'),
                          'numero': request.form.get('numero'), 'bairro': request.form.get('bairro'),
                          'cidade': request.form.get('cidade'), 'estado': request.form.get('estado'),
                          **rotulo_empresa(cliente_id)})
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
        registrar('escrita.excluiu_endereco', 'cadastros', tabela='enderecos_clientes',
                  registro_id=id,
                  antes={'cliente_id': cliente_id, 'tipo': endereco.get('tipo'),
                         'logradouro': endereco.get('logradouro'), 'cidade': endereco.get('cidade'),
                         **rotulo_empresa(cliente_id)})
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
        registrar('escrita.criou_socio', 'cadastros', tabela='socios_clientes',
                  registro_id=socio_id,
                  depois={'cliente_id': cliente_id, 'nome': nome, 'cpf': cpf,
                          'email': email, 'telefone': telefone,
                          'percentual_participacao': str(percentual), 'responsavel': responsavel,
                          **rotulo_empresa(cliente_id)})
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
        registrar('escrita.excluiu_socio', 'cadastros', tabela='socios_clientes',
                  registro_id=id,
                  antes={'cliente_id': cliente_id, 'nome': socio.get('nome'),
                         'cpf': socio.get('cpf'), **rotulo_empresa(cliente_id)})
        total_final = SocioCliente.get_total_percentual(cliente_id)
        flash(f'Sócio excluído com sucesso. Total atual de participação: {total_final:.2f}%.', 'success')
    else:
        flash('Erro ao excluir sócio!', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/clientes/<int:cliente_id>/contadores/vincular', methods=['POST'])
@login_required
def vincular_contador(cliente_id):
    """Liga um contador (cadastro is_contador=1) a este cliente."""
    r = ClienteContador.vincular_contador(
        cliente_id,
        request.form.get('contador_id'),
        request.form.get('finalidade'),
    )
    if r.get('ok'):
        flash(f'Contador {r.get("contador", "")} vinculado com sucesso!', 'success')
    else:
        flash(r.get('erro') or 'Falha ao vincular o contador.', 'danger')
    return redirect(url_for('clientes.detalhes', id=cliente_id) + '#certificado')


@clientes.route('/clientes/<int:cliente_id>/contadores/<int:contador_id>/desvincular',
                methods=['POST'])
@login_required
def desvincular_contador(cliente_id, contador_id):
    """Remove o vínculo cliente <-> contador."""
    r = ClienteContador.desvincular_contador(cliente_id, contador_id)
    flash('Contador desvinculado.' if r.get('ok')
          else (r.get('erro') or 'Falha ao desvincular.'),
          'success' if r.get('ok') else 'danger')
    return redirect(url_for('clientes.detalhes', id=cliente_id) + '#certificado')


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


# ===========================================================================
# D3/D4 — Histórico de auditoria. Tradução (rótulos/verbos/empresa) e o
# transform linha->cartão vivem em utils/auditoria_fmt.py (compartilhado com
# a tela geral de Auditoria). Aqui só a rota da aba da ficha do cliente.
# ===========================================================================


@clientes.route('/clientes/<int:id>/historico')
@login_required
def historico(id):
    """Partial (AJAX) da aba Histórico: alterações (padrão) ou consultas (admin)."""
    view = 'consultas' if request.args.get('view') == 'consultas' else 'alteracoes'
    # GUARDA NO SERVIDOR: consultas só ADMIN (esconder no front não é trava).
    if view == 'consultas' and not current_user.is_admin():
        return ('<div class="hist-vazio">Apenas administradores podem ver as consultas.</div>'), 403

    try:
        page = max(1, int(request.args.get('page', 1) or 1))
    except (TypeError, ValueError):
        page = 1
    per = 30
    off = (page - 1) * per
    sid = str(id)

    if view == 'alteracoes':
        # Cadastro inteiro: clientes + certificado (registro_id = cliente_id) e as
        # filhas endereços/sócios/contratos (cliente_id vive no JSON do log).
        cond = ("acao LIKE 'escrita.%%' AND ("
                "(tabela_afetada IN ('clientes','dfe_certificados') AND registro_id = %s) "
                "OR (tabela_afetada IN ('enderecos_clientes','socios_clientes','contratos') AND "
                "(JSON_UNQUOTE(JSON_EXTRACT(dados_novos,'$.cliente_id')) = %s "
                "OR JSON_UNQUOTE(JSON_EXTRACT(dados_anteriores,'$.cliente_id')) = %s)))")
        params = [id, sid, sid]
    else:
        # Consultas: abrir ficha (registro_id) + buscas/exportações desta empresa
        # (cliente_id no filtro) + captura SEFAZ (cliente_id no corpo).
        cond = ("acao LIKE 'leitura.%%' AND ("
                "(tabela_afetada = 'clientes' AND registro_id = %s) "
                "OR JSON_UNQUOTE(JSON_EXTRACT(dados_novos,'$.filtros.cliente_id')) = %s "
                "OR JSON_UNQUOTE(JSON_EXTRACT(dados_novos,'$.cliente_id')) = %s)")
        params = [id, sid, sid]

    total = (execute_query(f"SELECT COUNT(*) AS c FROM logs_sistema WHERE {cond}",
                           tuple(params), fetch=True, fetch_one=True) or {}).get('c', 0)
    rows = execute_query(
        f"SELECT id, data_hora, usuario_id, usuario_nome, acao, tabela_afetada, registro_id, "
        f"dados_anteriores, dados_novos FROM logs_sistema WHERE {cond} "
        f"ORDER BY data_hora DESC, id DESC LIMIT %s OFFSET %s",
        tuple(params + [per, off]), fetch=True) or []

    # Autor: usa usuario_nome (D2). Linha antiga sem nome → join por id; senão removido.
    faltam = {r['usuario_id'] for r in rows if not r.get('usuario_nome') and r.get('usuario_id')}
    nomes = {}
    if faltam:
        ph = ','.join(['%s'] * len(faltam))
        for u in execute_query(f"SELECT id, nome FROM usuarios WHERE id IN ({ph})",
                               tuple(faltam), fetch=True) or []:
            nomes[u['id']] = u['nome']

    itens = [hist_preparar(r, nomes) for r in rows]
    total_paginas = max(1, (total + per - 1) // per)
    return render_template('clientes/_historico.html', itens=itens, view=view, page=page,
                           total=total, total_paginas=total_paginas, cliente_id=id,
                           auditoria_inicio=AUDITORIA_INICIO, is_admin=current_user.is_admin())
