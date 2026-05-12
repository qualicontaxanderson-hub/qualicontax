"""Blueprint para gestão de Tipos de Cadastros Adicionais e auto-importação."""
import logging
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from utils.auth_helper import login_required
from models.cadastro_anp import CadastroAnp
from models.cliente import Cliente

adicionais = Blueprint('adicionais', __name__)
logger = logging.getLogger(__name__)

# Tipos de cadastro disponíveis (extensível)
TIPOS_CADASTROS = [
    {
        'slug': 'anp',
        'nome': 'ANP',
        'descricao': 'Ficha Cadastral da Agência Nacional do Petróleo, Gás Natural e Biocombustíveis',
        'icone': 'fas fa-gas-pump',
        'cor': '#e74c3c',
        'campos': [
            'Situação', 'Autorização', 'CNPJ', 'Razão Social', 'Bandeira',
            'Data Publicação', 'Latitude', 'Longitude', 'Sócios', 'Produtos',
        ],
    },
]


@adicionais.route('/cadastros/adicionais')
@login_required
def index():
    """Lista os tipos de cadastros adicionais disponíveis."""
    return render_template('cadastros/adicionais.html', tipos=TIPOS_CADASTROS)


@adicionais.route('/cadastros/adicionais/sync-dropbox-anp', methods=['POST'])
@login_required
def sync_dropbox_anp():
    """
    Varre a pasta Legalizacao/NOVO no Dropbox em busca de PDFs da ANP,
    lê cada um, verifica o CNPJ e atualiza/cria o cadastro ANP do cliente.
    """
    from utils import dropbox_sync
    from utils.anp_parser import extrair_dados_anp

    if not dropbox_sync.is_configured():
        return jsonify({'erro': 'Dropbox não configurado. Defina DROPBOX_APP_KEY e DROPBOX_REFRESH_TOKEN.'}), 400

    svc = dropbox_sync._service

    pasta_novo = '/Legalizacao/NOVO'
    try:
        entries = svc.list_folder(pasta_novo)
    except dropbox_sync.DropboxAuthError:
        return jsonify({'erro': 'Credenciais Dropbox inválidas ou expiradas. Verifique a configuração.'}), 401
    except Exception as exc:
        logger.error("Erro ao listar pasta Dropbox %s: %s", pasta_novo, exc)
        return jsonify({'erro': 'Erro ao listar pasta Dropbox. Verifique os logs do servidor.'}), 500

    pdf_entries = [e for e in entries if e.get('is_file') and e.get('name', '').lower().endswith('.pdf')]

    resultados = []
    for entry in pdf_entries:
        path = entry.get('path')
        nome = entry.get('name', '')
        try:
            content = svc.download_file(path)
        except Exception as exc:
            logger.error("Erro ao baixar %s: %s", nome, exc)
            resultados.append({'arquivo': nome, 'status': 'erro', 'mensagem': 'Erro ao baixar arquivo'})
            continue

        resultado = extrair_dados_anp(content)
        if not resultado['sucesso']:
            resultados.append({'arquivo': nome, 'status': 'erro', 'mensagem': resultado['erro']})
            continue

        cnpj_digits = resultado['cnpj']
        if not cnpj_digits:
            resultados.append({'arquivo': nome, 'status': 'ignorado', 'mensagem': 'CNPJ não encontrado no PDF'})
            continue

        # Busca cliente pelo CNPJ
        clientes_encontrados = Cliente.search(cnpj_digits)
        if not clientes_encontrados:
            resultados.append({
                'arquivo': nome, 'status': 'ignorado',
                'mensagem': f'CNPJ não encontrado no sistema',
            })
            continue

        cliente = clientes_encontrados[0]
        cliente_id = cliente['id']

        # Verifica se já existe cadastro ANP para esse cliente
        cadastros_existentes = CadastroAnp.get_by_cliente(cliente_id)
        anp_id = cadastros_existentes[0]['id'] if cadastros_existentes else None

        dados = resultado['dados']
        dados['fonte'] = 'DROPBOX'
        saved = CadastroAnp.save_full(
            cliente_id=cliente_id,
            data=dados,
            socios=resultado['socios'],
            produtos=resultado['produtos'],
            anp_id=anp_id,
        )

        if saved:
            resultados.append({
                'arquivo': nome,
                'status': 'importado',
                'cliente': cliente.get('nome_razao_social'),
                'cliente_id': cliente_id,
            })
        else:
            resultados.append({'arquivo': nome, 'status': 'erro', 'mensagem': 'Falha ao salvar no banco'})

    return jsonify({'resultados': resultados, 'total': len(pdf_entries)})
