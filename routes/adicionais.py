"""Blueprint para gestão de Tipos de Cadastros Adicionais e auto-importação."""
import logging
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from utils.auth_helper import login_required
from utils.db_helper import get_last_db_error, _set_last_db_error
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


def _validar_tamanhos_campos_anp(dados):
    """Valida limites de campos da tabela cadastros_anp e retorna erro amigável."""

    # Detecta confusão do parser: quando campos distintos têm o mesmo valor longo
    # — sinal de que o layout do PDF não foi reconhecido corretamente.
    def _preview(text, max_len=60):
        return (text[:max_len] + '...') if len(text) > max_len else text

    header_campos = ('situacao', 'autorizacao', 'razao_social', 'nome_fantasia')
    header_vals = [str(dados.get(f) or '').strip() for f in header_campos]
    header_non_empty = [v for v in header_vals if len(v) > 10]
    if len(header_non_empty) >= 2 and len(set(header_non_empty)) == 1:
        return (
            f'Formato de PDF não reconhecido: os campos de identificação (Situação, Autorização, '
            f'Razão Social) foram extraídos com o mesmo valor incorreto, indicando que o layout '
            f'deste PDF é diferente do padrão esperado. '
            f'Valor extraído: "{_preview(header_non_empty[0])}"'
        )

    addr_campos = ('endereco', 'complemento', 'bairro', 'municipio_uf')
    addr_vals = [str(dados.get(f) or '').strip() for f in addr_campos]
    addr_non_empty = [v for v in addr_vals if len(v) > 10]
    if len(addr_non_empty) >= 2 and len(set(addr_non_empty)) == 1:
        return (
            f'Formato de PDF não reconhecido: os campos de endereço (Complemento, Bairro, '
            f'Município/UF) foram todos extraídos com o mesmo valor do Endereço. '
            f'Valor: "{_preview(addr_non_empty[0])}"'
        )

    limites = {
        'situacao': 100,
        'autorizacao': 100,
        'cnpj_anp': 18,
        'razao_social': 255,
        'nome_fantasia': 255,
        'endereco': 255,
        'complemento': 100,
        'bairro': 100,
        'municipio_uf': 100,
        'cep': 10,
        'nr_despacho': 50,
        'bandeira': 100,
        'tipo_posto': 20,
        'pmqc': 50,
        'delivery': 10,
        'latitude': 30,
        'longitude': 30,
    }
    for campo, maximo in limites.items():
        valor = dados.get(campo)
        if valor is None:
            continue
        texto = str(valor).strip()
        if len(texto) > maximo:
            preview = (texto[:80] + '...') if len(texto) > 80 else texto
            return (
                f'Info não localizada corretamente no PDF: campo {campo.upper()} excede o limite '
                f'({len(texto)}/{maximo}). Valor extraído: "{preview}"'
            )
    return None


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

        # Busca cliente pelo CNPJ (comparação apenas de dígitos, independente de formatação)
        cliente = Cliente.get_by_cnpj_digits(cnpj_digits)
        if not cliente:
            resultados.append({
                'arquivo': nome, 'status': 'ignorado',
                'mensagem': f'Info não localizada: CNPJ {cnpj_digits} não foi localizado no sistema',
            })
            continue

        cliente_id = cliente['id']

        # Verifica se já existe cadastro ANP para esse cliente
        cadastros_existentes = CadastroAnp.get_by_cliente(cliente_id)
        anp_id = cadastros_existentes[0]['id'] if cadastros_existentes else None

        dados = resultado['dados']
        dados['fonte'] = 'DROPBOX'
        erro_validacao = _validar_tamanhos_campos_anp(dados)
        if erro_validacao:
            resultados.append({'arquivo': nome, 'status': 'erro', 'mensagem': erro_validacao})
            continue

        _set_last_db_error('')  # reset antes de cada tentativa de save
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
            db_err = get_last_db_error()
            if db_err:
                logger.error("Falha ao salvar cadastro ANP do arquivo '%s': %s", nome, db_err)
            msg = 'Falha ao salvar no banco de dados. Verifique os logs do servidor para mais detalhes.'
            resultados.append({
                'arquivo': nome,
                'status': 'erro',
                'mensagem': msg,
            })

    return jsonify({'resultados': resultados, 'total': len(pdf_entries)})
