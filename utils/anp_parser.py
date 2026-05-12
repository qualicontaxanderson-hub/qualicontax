"""
Parser de PDF da ANP (Agência Nacional do Petróleo).

Extrai campos da Ficha Cadastral de Distribuidores/Postos emitida
pelo sistema da ANP usando pdfplumber.

Campos extraídos:
    situacao, autorizacao, cnpj, razao_social, nome_fantasia,
    endereco, complemento, bairro, municipio_uf, cep,
    nr_despacho, data_publicacao, bandeira, data_inicio_bandeira,
    tipo_posto, pmqc, delivery, latitude, longitude,
    data_emissao, socios, produtos
"""
import re
import io
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    import pdfplumber
    _PDFPLUMBER = True
except ImportError:
    _PDFPLUMBER = False
    logger.warning("pdfplumber não disponível. Instale com: pip install pdfplumber==0.11.9")

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_RE_CNPJ = re.compile(r'\b(\d{2}[\.\s]?\d{3}[\.\s]?\d{3}[\/\\\s]?\d{4}[\-\s]?\d{2})\b')
_RE_DATE = re.compile(r'\b(\d{1,2}/\d{1,2}/\d{4})\b')
_RE_DATE2 = re.compile(r'\b(\d{4}-\d{2}-\d{2})\b')
_RE_LAT_LON = re.compile(r'(-?\d+\.\d{4,})')
_RE_NUMERO = re.compile(r'(\d[\d.,]*)')
_RE_DATETIME = re.compile(r'(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}(?::\d{2})?)')


def _only_digits(text):
    return re.sub(r'\D', '', text or '')


def _norm(text):
    """Remove espaços extras."""
    return re.sub(r'\s+', ' ', (text or '')).strip()


def _parse_br_date(text):
    """Converte DD/MM/YYYY → YYYY-MM-DD para guardar no banco."""
    if not text:
        return None
    m = _RE_DATE.search(text)
    if m:
        try:
            return datetime.strptime(m.group(1), '%d/%m/%Y').strftime('%Y-%m-%d')
        except ValueError:
            pass
    m2 = _RE_DATE2.search(text)
    if m2:
        return m2.group(1)
    return None


def _after(lines, label, stop_labels=None, max_distance=4):
    """
    Retorna o valor que aparece logo após um rótulo (label) nas linhas.
    Busca na mesma linha ou na próxima linha não-vazia.
    """
    stop_labels = stop_labels or []
    label_lc = label.lower()
    for i, line in enumerate(lines):
        if label_lc in line.lower():
            # Tenta extrair da mesma linha após o label
            idx = line.lower().find(label_lc)
            rest = line[idx + len(label_lc):].strip().lstrip(':').strip()
            if rest:
                # verifica se o resto não é outro rótulo
                if not any(sl.lower() in rest.lower() for sl in stop_labels):
                    return rest
            # Procura nas próximas linhas
            for j in range(i + 1, min(i + 1 + max_distance, len(lines))):
                candidate = lines[j].strip()
                if not candidate:
                    continue
                if any(sl.lower() in candidate.lower() for sl in stop_labels):
                    break
                return candidate
    return None


# ---------------------------------------------------------------------------
# Parsers por seção
# ---------------------------------------------------------------------------

def _extract_header_block(lines):
    """
    Extrai o bloco principal: Situação, Autorização, CNPJ, Razão Social,
    Nome Fantasia, Data de Emissão.
    """
    result = {}

    # Data/Hora Emissão: "12/05/2026 08:57:53"
    for line in lines[:10]:
        m = _RE_DATETIME.search(line)
        if m:
            try:
                result['data_emissao'] = datetime.strptime(
                    f"{m.group(1)} {m.group(2)}", '%d/%m/%Y %H:%M:%S'
                ).strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass
            break

    # Situação
    situacao = _after(lines, 'Situação', stop_labels=['Autorização', 'CNPJ'])
    if situacao:
        result['situacao'] = _norm(situacao)

    # Autorização
    autorizacao = _after(lines, 'Autorização', stop_labels=['CNPJ', 'Razão'])
    if autorizacao:
        result['autorizacao'] = _norm(autorizacao)

    # CNPJ — pega o primeiro número com formato CNPJ do texto
    cnpj_val = _after(lines, 'CNPJ', stop_labels=['Razão', 'Nome'])
    if cnpj_val:
        m = _RE_CNPJ.search(cnpj_val)
        if m:
            result['cnpj_anp'] = m.group(1)
    # Fallback: busca global
    if 'cnpj_anp' not in result:
        for line in lines:
            m = _RE_CNPJ.search(line)
            if m:
                result['cnpj_anp'] = m.group(1)
                break

    # Razão Social
    razao = _after(lines, 'Razão Social', stop_labels=['Nome Fantasia', 'Situação'])
    if razao:
        result['razao_social'] = _norm(razao)

    # Nome Fantasia
    fantasia = _after(lines, 'Nome Fantasia', stop_labels=['Endereço', 'Situação'])
    if fantasia:
        result['nome_fantasia'] = _norm(fantasia)

    return result


def _extract_address_block(lines):
    """Extrai Endereço, Complemento, Bairro, Município/UF, CEP."""
    result = {}

    endereco = _after(lines, 'Endereço', stop_labels=['Complemento', 'Bairro', 'Município'])
    if endereco:
        result['endereco'] = _norm(endereco)

    complemento = _after(lines, 'Complemento', stop_labels=['Bairro', 'Município'])
    if complemento:
        result['complemento'] = _norm(complemento)

    bairro = _after(lines, 'Bairro', stop_labels=['Município', 'CEP'])
    if bairro:
        result['bairro'] = _norm(bairro)

    municipio = _after(lines, 'Município/UF', stop_labels=['CEP', 'Nr'])
    if municipio:
        result['municipio_uf'] = _norm(municipio)

    cep = _after(lines, 'CEP', stop_labels=['Nr Despacho', 'Data'])
    if cep:
        result['cep'] = _norm(cep)

    return result


def _extract_despacho_block(lines):
    """Extrai Nr Despacho, Data Publicação, Bandeira/Início, Tipo de Posto, PMQC."""
    result = {}

    nr_despacho = _after(lines, 'Nr Despacho', stop_labels=['Data', 'Bandeira'])
    if nr_despacho:
        result['nr_despacho'] = _norm(nr_despacho)

    # Data da Publicação
    pub_text = _after(lines, 'Data da Publicação', stop_labels=['Bandeira', 'Tipo'])
    if not pub_text:
        pub_text = _after(lines, 'Data Publicação', stop_labels=['Bandeira', 'Tipo'])
    if pub_text:
        result['data_publicacao'] = _parse_br_date(pub_text)

    # Bandeira/Início — valor pode ser "RAIZEN - 24/09/2021"
    bandeira_raw = _after(lines, 'Bandeira/Início', stop_labels=['Tipo de Posto', 'PMQC'])
    if not bandeira_raw:
        bandeira_raw = _after(lines, 'Bandeira/Inicio', stop_labels=['Tipo de Posto', 'PMQC'])
    if bandeira_raw:
        # Separa bandeira da data de início
        m = _RE_DATE.search(bandeira_raw)
        if m:
            result['data_inicio_bandeira'] = _parse_br_date(m.group(1))
            # Bandeira = tudo antes do " - " e da data
            bandeira_nome = re.sub(r'\s*[-–]\s*' + re.escape(m.group(1)), '', bandeira_raw)
            bandeira_nome = bandeira_nome.strip(' -–')
            if bandeira_nome:
                result['bandeira'] = _norm(bandeira_nome)
        else:
            result['bandeira'] = _norm(bandeira_raw)

    # Tipo de Posto
    tipo_posto = _after(lines, 'Tipo de Posto', stop_labels=['PMQC', 'Delivery'])
    if tipo_posto:
        result['tipo_posto'] = _norm(tipo_posto)

    # PMQC
    pmqc = _after(lines, 'PMQC', stop_labels=['Delivery', 'Latitude'])
    if pmqc:
        result['pmqc'] = _norm(pmqc)

    # Delivery
    delivery = _after(lines, 'Delivery', stop_labels=['Data Autorização', 'Latitude', 'Longitude'])
    if delivery:
        result['delivery'] = _norm(delivery)

    return result


def _extract_geo_block(lines):
    """Extrai Latitude e Longitude."""
    result = {}

    lat = _after(lines, 'Latitude', stop_labels=['Longitude', 'SRC'])
    if lat:
        m = _RE_LAT_LON.search(lat)
        if m:
            result['latitude'] = m.group(1)

    lon = _after(lines, 'Longitude', stop_labels=['SRC', 'Data da'])
    if lon:
        m = _RE_LAT_LON.search(lon)
        if m:
            result['longitude'] = m.group(1)

    # Fallback: busca dois floats de alta precisão consecutivos
    if 'latitude' not in result or 'longitude' not in result:
        floats = _RE_LAT_LON.findall('\n'.join(lines))
        if len(floats) >= 2 and 'latitude' not in result:
            result['latitude'] = floats[0]
            result['longitude'] = floats[1]

    return result


def _extract_socios(lines):
    """
    Extrai a lista de sócios do PDF.
    A seção começa com "Sócios" e vai até a próxima seção ou fim.
    """
    socios = []
    in_socios = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lc = stripped.lower()
        # Detecta início da seção
        if lc in ('sócios', 'socios', 'quadro de sócios', 'quadro societário'):
            in_socios = True
            continue
        # Para ao encontrar seção posterior
        if in_socios and any(kw in lc for kw in ('produto', 'bico', 'tancagem', 'combustível',
                                                    'capacidade', 'equipamento', 'observ')):
            break
        if in_socios and stripped and len(stripped) > 2:
            # Evita linhas de cabeçalho
            if 'nome' == lc or 'cpf' == lc:
                continue
            socios.append(stripped)
    return socios


def _extract_produtos(lines, tabelas):
    """
    Extrai produtos, tancagem e bicos.
    Tenta primeiro via tabelas pdfplumber (mais preciso), depois via texto.
    """
    produtos = []

    # --- Via tabelas estruturadas ---
    for tabela in tabelas:
        for row in tabela:
            if not row or len(row) < 2:
                continue
            cells = [_norm(c or '') for c in row]
            # Identifica coluna de produto (primeira célula não-vazia longa)
            # Linha de cabeçalho
            if any('produto' in c.lower() for c in cells) and any('bico' in c.lower() or 'tanc' in c.lower() for c in cells):
                continue
            prod = cells[0] if cells[0] else None
            if not prod or len(prod) < 3:
                continue
            # Ignora linhas de rótulo (como "Produtos", "Combustíveis")
            if prod.lower() in ('produtos', 'combustíveis', 'lubrificantes', 'outros'):
                continue

            tancagem = None
            bicos = None
            for c in cells[1:]:
                if tancagem is None:
                    m = _RE_NUMERO.search(c)
                    if m:
                        try:
                            tancagem = float(m.group(1).replace(',', '.'))
                        except ValueError:
                            pass
                elif bicos is None:
                    m = _RE_NUMERO.search(c)
                    if m:
                        try:
                            bicos = int(float(m.group(1).replace(',', '.')))
                        except ValueError:
                            pass
            if prod:
                produtos.append({'produto': prod, 'tancagem_m3': tancagem, 'bicos': bicos})

    if produtos:
        return produtos

    # --- Fallback: via texto ---
    in_produtos = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        lc = stripped.lower()
        if any(kw in lc for kw in ('produto', 'tancagem', 'combustível')) and 'bico' in lc:
            in_produtos = True
            continue
        if in_produtos:
            # Para em outra seção conhecida
            if any(kw in lc for kw in ('assinatura', 'declaração', 'observ', 'nota')):
                break
            # Tenta extrair produto e números
            numeros = _RE_NUMERO.findall(stripped)
            if numeros and len(stripped) > 5:
                produto_nome = re.sub(r'\d[\d.,\s]*$', '', stripped).strip()
                if produto_nome:
                    tancagem = None
                    bicos = None
                    if len(numeros) >= 1:
                        try:
                            tancagem = float(numeros[0].replace(',', '.'))
                        except ValueError:
                            pass
                    if len(numeros) >= 2:
                        try:
                            bicos = int(float(numeros[1].replace(',', '.')))
                        except ValueError:
                            pass
                    produtos.append({'produto': produto_nome, 'tancagem_m3': tancagem, 'bicos': bicos})

    return produtos


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def extrair_dados_anp(arquivo_bytes):
    """
    Extrai dados de uma Ficha Cadastral ANP em PDF.

    Args:
        arquivo_bytes (bytes): conteúdo binário do PDF

    Returns:
        dict com:
            sucesso (bool)
            dados (dict): campos do cadastro ANP
            socios (list[str])
            produtos (list[dict])
            cnpj (str): CNPJ extraído (apenas dígitos)
            texto_bruto (str)
            erro (str|None)
    """
    if not _PDFPLUMBER:
        return {
            'sucesso': False, 'dados': {}, 'socios': [], 'produtos': [],
            'cnpj': '', 'texto_bruto': '',
            'erro': 'Biblioteca pdfplumber não instalada. Execute: pip install pdfplumber==0.11.9',
        }

    all_lines = []
    all_tabelas = []
    try:
        with pdfplumber.open(io.BytesIO(arquivo_bytes)) as pdf:
            for page in pdf.pages:
                tabelas = page.extract_tables()
                all_tabelas.extend(tabelas or [])
                texto = page.extract_text() or ''
                for line in texto.splitlines():
                    all_lines.append(line)
    except Exception as exc:
        logger.error("Erro ao abrir PDF ANP: %s", exc)
        return {
            'sucesso': False, 'dados': {}, 'socios': [], 'produtos': [],
            'cnpj': '', 'texto_bruto': '',
            'erro': f'Erro ao processar PDF: {exc}',
        }

    texto_bruto = '\n'.join(all_lines)

    # Verifica se parece ser um PDF da ANP
    texto_lc = texto_bruto.lower()
    is_anp = any(kw in texto_lc for kw in ('agência nacional do petróleo', 'anp', 'ficha cadastral', 'autorização'))
    if not is_anp:
        return {
            'sucesso': False, 'dados': {}, 'socios': [], 'produtos': [],
            'cnpj': '', 'texto_bruto': texto_bruto,
            'erro': 'Este PDF não parece ser uma Ficha Cadastral da ANP.',
        }

    dados = {}
    dados.update(_extract_header_block(all_lines))
    dados.update(_extract_address_block(all_lines))
    dados.update(_extract_despacho_block(all_lines))
    dados.update(_extract_geo_block(all_lines))

    socios = _extract_socios(all_lines)
    produtos = _extract_produtos(all_lines, all_tabelas)

    cnpj_digits = _only_digits(dados.get('cnpj_anp', ''))

    return {
        'sucesso': True,
        'dados': dados,
        'socios': socios,
        'produtos': produtos,
        'cnpj': cnpj_digits,
        'texto_bruto': texto_bruto,
        'erro': None,
    }
