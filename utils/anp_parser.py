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

Estratégia de extração (em ordem de prioridade):
  1. extract_tables() – preserva colunas/linhas da tabela estruturada.
  2. Fallback texto – para campos ausentes nas tabelas.
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
_RE_CEP = re.compile(r'\b(\d{5}-?\d{3})\b')

# Inline-layout helpers (fields merged onto one unlabelled line)
_RE_INLINE_CNPJ_14 = re.compile(r'(?<!\d)(\d{14})(?!\d)')
_RE_AUTH_CODE = re.compile(r'\b([A-Z]{1,4}/[A-Z]{0,3}\d{3,})\b')
_RE_NR_DESPACHO = re.compile(r'\bANP\s+N[ºo°\.]\s*(\d+)\b', re.IGNORECASE)
_RE_MUNICIPIO_UF = re.compile(
    r'([A-ZÁÀÃÉÊÍÓÕÚÇÜ][A-ZÁÀÃÉÊÍÓÕÚÇÜa-záàãéêíóõúçü\s\-\']+)'
    r'/([A-Z]{2})\b'
)

# Known Tipo de Posto values (max 20 chars each)
_TIPOS_POSTO_KNOWN = [
    'POSTO REVENDEDOR',
    'TROCA A ÓLEO',
    'TROCA A OLEO',
    'POSTO FLUTUANTE',
    'AVIAÇÃO GERAL',
    'AVIAÇÃO',
    'AVIAO GERAL',
    'AVIAO',
    'TRR',
]


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
# Helpers de layout compacto (campos mesclados em uma linha sem rótulos)
# ---------------------------------------------------------------------------

def _has_header_confusion(dados):
    """Retorna True quando dois ou mais campos do cabeçalho têm o mesmo valor longo."""
    campos = ('situacao', 'autorizacao', 'razao_social', 'nome_fantasia')
    vals = [str(dados.get(c) or '').strip() for c in campos]
    non_empty = [v for v in vals if len(v) > 10]
    return len(non_empty) >= 2 and len(set(non_empty)) == 1


def _extract_header_inline(lines):
    """
    Fallback para PDFs cujo cabeçalho é condensado em uma única linha sem
    rótulos separadores, e.g.:
      'EM OPERAÇÃO PR/SP0199861 36142094000180 AUTO POSTO PARQUE PIQUERI LTDA'
    Detecta pela presença de um CNPJ de 14 dígitos contínuos com contexto
    antes (situação + autorização) e depois (razão social).
    """
    for line in lines:
        stripped = line.strip()
        m_cnpj = _RE_INLINE_CNPJ_14.search(stripped)
        if not m_cnpj:
            continue
        before = stripped[:m_cnpj.start()].strip()
        after = stripped[m_cnpj.end():].strip()
        # Exige texto antes e depois do CNPJ
        if not before or not after:
            continue
        result = {'cnpj_anp': m_cnpj.group(1)}
        m_auth = _RE_AUTH_CODE.search(before)
        if m_auth:
            result['autorizacao'] = m_auth.group(1)
            situacao = before[:m_auth.start()].strip()
            if situacao:
                result['situacao'] = situacao
        if after:
            result['razao_social'] = after
        return result
    return {}


def _has_address_confusion(dados):
    """Retorna True quando dois ou mais campos de endereço têm o mesmo valor longo."""
    campos = ('endereco', 'complemento', 'bairro', 'municipio_uf')
    vals = [str(dados.get(c) or '').strip() for c in campos]
    non_empty = [v for v in vals if len(v) > 10]
    return len(non_empty) >= 2 and len(set(non_empty)) == 1


def _extract_address_inline(lines):
    """
    Fallback para layout compacto de endereço onde todos os campos aparecem
    numa única linha sem rótulos, e.g.:
      'RUA PIQUERI 211  PIRITUBA  SÃO PAULO/SP  02956-020'
    Extrai Município/UF e CEP por padrão; o texto antes do município é
    usado como Endereço.
    """
    _label_keywords = ('endereço', 'endereco', 'complemento', 'bairro',
                       'município', 'municipio', 'cep')
    for line in lines:
        stripped = line.strip()
        lc = stripped.lower()
        # Pula linhas de rótulos
        if any(kw in lc for kw in _label_keywords):
            continue
        m_cep = _RE_CEP.search(stripped)
        m_mun = _RE_MUNICIPIO_UF.search(stripped)
        if not m_cep and not m_mun:
            continue
        result = {}
        if m_cep:
            result['cep'] = m_cep.group(1)
        if m_mun:
            result['municipio_uf'] = _norm(m_mun.group(0))
            before_mun = stripped[:m_mun.start()].strip()
            if before_mun:
                result['endereco'] = _norm(before_mun)
        elif m_cep:
            before_cep = stripped[:m_cep.start()].strip()
            if before_cep:
                result['endereco'] = _norm(before_cep)
        return result
    return {}


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
        m = _RE_CEP.search(cep)
        if m:
            result['cep'] = _norm(m.group(1))

    if 'cep' not in result:
        texto_bruto = '\n'.join(lines)
        m = _RE_CEP.search(texto_bruto)
        if m:
            result['cep'] = _norm(m.group(1))

    return result


def _extract_despacho_block(lines):
    """Extrai Nr Despacho, Data Publicação, Bandeira/Início, Tipo de Posto, PMQC."""
    result = {}

    nr_despacho = _after(lines, 'Nr Despacho', stop_labels=['Data', 'Bandeira'])
    if nr_despacho:
        result['nr_despacho'] = _norm(nr_despacho)
    else:
        m_nr = _RE_NR_DESPACHO.search('\n'.join(lines))
        if m_nr:
            result['nr_despacho'] = m_nr.group(1)

    # Data da Publicação
    pub_text = _after(lines, 'Data da Publicação', stop_labels=['Bandeira', 'Tipo'])
    if not pub_text:
        pub_text = _after(lines, 'Data Publicação', stop_labels=['Bandeira', 'Tipo'])
    if pub_text:
        result['data_publicacao'] = _parse_br_date(pub_text)
    if 'data_publicacao' not in result:
        for line in lines:
            lc = line.lower()
            if 'publica' in lc:
                pub_fallback = _parse_br_date(line)
                if pub_fallback:
                    result['data_publicacao'] = pub_fallback
                    break

    # Bandeira/Início — valor pode ser "RAIZEN - 24/09/2021"
    bandeira_raw = _after(lines, 'Bandeira/Início', stop_labels=['Tipo de Posto', 'PMQC'])
    if not bandeira_raw:
        bandeira_raw = _after(lines, 'Bandeira/Inicio', stop_labels=['Tipo de Posto', 'PMQC'])
    if bandeira_raw:
        lc_bandeira = bandeira_raw.lower()
        # Alguns PDFs compactam tudo numa linha (ANP Nº, Publicação e Bandeira).
        # Nesses casos, mantém apenas o trecho após "Bandeira".
        if 'bandeira' in lc_bandeira:
            idx = lc_bandeira.rfind('bandeira')
            bandeira_raw = bandeira_raw[idx + len('bandeira'):].strip(' :;-–')

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
        if result.get('bandeira'):
            # Remove restos de tokens de outras colunas quando vierem colados.
            result['bandeira'] = re.sub(
                r'(?i)\b(anp|n[ºo°\.]?|publica(?:ção|cao)|tipo de posto|pmqc)\b.*$',
                '',
                result['bandeira']
            ).strip(' -–:;')

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

    # Corrige tipo_posto se foi extraído como bloco grande (layout compacto)
    tipo = result.get('tipo_posto', '')
    if tipo and len(tipo) > 20:
        tipo_upper = tipo.upper()
        found = next((t for t in _TIPOS_POSTO_KNOWN if t.upper() in tipo_upper), None)
        result['tipo_posto'] = found

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
    invalid_tokens = ('voltar', 'editar', 'excluir', 'copiar', 'whatsapp')

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
                if any(tok in prod.lower() for tok in invalid_tokens):
                    continue
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
                    if any(tok in produto_nome.lower() for tok in invalid_tokens):
                        continue
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

    # Remove duplicados mantendo ordem
    vistos = set()
    dedup = []
    for p in produtos:
        key = (
            (p.get('produto') or '').strip().lower(),
            str(p.get('tancagem_m3')),
            str(p.get('bicos')),
        )
        if not key[0] or key in vistos:
            continue
        vistos.add(key)
        dedup.append(p)

    return dedup


# ---------------------------------------------------------------------------
# Extração primária via tabelas estruturadas
# ---------------------------------------------------------------------------

# Rótulos que identificam linhas de cabeçalho da ficha ANP
_LABELS_HEADER = frozenset({
    'situação', 'situacao',
    'autorização', 'autorizacao',
    'cnpj',
    'razão social', 'razao social',
    'nome fantasia',
    'endereço', 'endereco',
    'complemento',
    'bairro',
    'município/uf', 'municipio/uf',
    'cep',
    'nr despacho', 'nr. despacho',
    'data da publicação', 'data publicação', 'data da publicacao', 'data publicacao',
    'bandeira/início', 'bandeira/inicio', 'bandeira / início', 'bandeira / inicio',
    'tipo de posto',
    'pmqc',
    'delivery',
    'data autorização delivery', 'data autorizacao delivery',
    'número despacho delivery', 'numero despacho delivery',
    'latitude',
    'longitude',
    'src',
    'data da obtenção', 'data da obtencao',
    'origem',
})

# Mapeamento: rótulo normalizado → campo no dict de dados
_LABEL_TO_FIELD = {
    'situação': 'situacao',
    'situacao': 'situacao',
    'autorização': 'autorizacao',
    'autorizacao': 'autorizacao',
    'cnpj': 'cnpj_anp',
    'razão social': 'razao_social',
    'razao social': 'razao_social',
    'nome fantasia': 'nome_fantasia',
    'endereço': 'endereco',
    'endereco': 'endereco',
    'complemento': 'complemento',
    'bairro': 'bairro',
    'município/uf': 'municipio_uf',
    'municipio/uf': 'municipio_uf',
    'cep': 'cep',
    'nr despacho': 'nr_despacho',
    'nr. despacho': 'nr_despacho',
    'data da publicação': 'data_publicacao',
    'data publicação': 'data_publicacao',
    'data da publicacao': 'data_publicacao',
    'data publicacao': 'data_publicacao',
    'bandeira/início': '_bandeira_raw',
    'bandeira/inicio': '_bandeira_raw',
    'bandeira / início': '_bandeira_raw',
    'bandeira / inicio': '_bandeira_raw',
    'tipo de posto': 'tipo_posto',
    'pmqc': 'pmqc',
    'delivery': 'delivery',
    'latitude': 'latitude',
    'longitude': 'longitude',
}


def _build_label_map_from_tables(tabelas):
    """
    Varre todas as tabelas extraídas pelo pdfplumber e constrói um dict
    {rótulo_normalizado: valor} emparelhando linhas de cabeçalho (com
    rótulos conhecidos) com a linha de valores imediatamente abaixo.

    A ficha ANP tem layout:
        [Situação] [Autorização] [CNPJ] [Razão Social] [Nome Fantasia]
        [EM OPERAÇÃO] [PR/GO...] [33.503...] [POSTO ...] [NOVO ...]
        ...
    """
    label_map = {}

    for tabela in tabelas:
        if not tabela:
            continue
        n = len(tabela)
        i = 0
        while i < n:
            row = tabela[i]
            if not row:
                i += 1
                continue

            cells = [_norm(str(c or '')) for c in row]
            # Conta quantas células são rótulos conhecidos
            hits = sum(1 for c in cells if c.lower() in _LABELS_HEADER)

            if hits == 0:
                i += 1
                continue

            # É uma linha de rótulos; pega a linha de valores logo abaixo
            if i + 1 >= n:
                i += 1
                continue

            val_row = tabela[i + 1]
            if not val_row:
                i += 2
                continue

            vals = [_norm(str(c or '')) for c in val_row]

            # Verifica se a linha seguinte também é de rótulos (não de valores)
            val_hits = sum(1 for v in vals if v.lower() in _LABELS_HEADER)
            if val_hits >= max(2, hits):
                # A linha seguinte também é cabeçalho, não avança o índice
                i += 1
                continue

            # Mapeia rótulo → valor célula a célula
            for j, cell in enumerate(cells):
                if cell and j < len(vals) and vals[j]:
                    label_map[cell.lower()] = vals[j]

            i += 2  # pula a linha de valores

    return label_map


def _extract_all_from_table_map(label_map):
    """
    Converte o label_map (de _build_label_map_from_tables) no dict de dados
    usado pelo restante do sistema, processando campos especiais como CNPJ,
    datas, bandeira/início e coordenadas.
    """
    result = {}

    for lbl, field in _LABEL_TO_FIELD.items():
        if lbl in label_map:
            val = label_map[lbl]
            if val and field not in result:
                result[field] = val

    # Processa Bandeira/Início: "BANDEIRA BRANCA - 02/02/2021"
    bandeira_raw = result.pop('_bandeira_raw', None)
    if bandeira_raw:
        m = _RE_DATE.search(bandeira_raw)
        if m:
            result['data_inicio_bandeira'] = _parse_br_date(m.group(1))
            bandeira_nome = re.sub(
                r'\s*[-–]\s*' + re.escape(m.group(1)), '', bandeira_raw
            ).strip(' -–')
            if bandeira_nome:
                result['bandeira'] = _norm(bandeira_nome)
        else:
            result['bandeira'] = _norm(bandeira_raw)

    # Normaliza CNPJ
    cnpj_raw = result.get('cnpj_anp', '')
    if cnpj_raw:
        m = _RE_INLINE_CNPJ_14.search(cnpj_raw)
        if m:
            result['cnpj_anp'] = m.group(1)
        else:
            m2 = _RE_CNPJ.search(cnpj_raw)
            if m2:
                result['cnpj_anp'] = m2.group(1)

    # Converte datas textuais para YYYY-MM-DD
    for field in ('data_publicacao',):
        if field in result:
            parsed = _parse_br_date(result[field])
            if parsed:
                result[field] = parsed

    # Normaliza lat/lon
    for field in ('latitude', 'longitude'):
        if field in result:
            m = _RE_LAT_LON.search(str(result[field]))
            if m:
                result[field] = m.group(1)

    # CEP: aceita 8 dígitos contínuos (sem traço) além do formato padrão
    cep_raw = result.get('cep', '')
    if cep_raw:
        m = _RE_CEP.search(cep_raw)
        if m:
            result['cep'] = m.group(1)
        elif re.fullmatch(r'\d{8}', cep_raw):
            result['cep'] = cep_raw[:5] + '-' + cep_raw[5:]

    return result


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
            'erro': 'Erro ao processar o arquivo PDF. Verifique se o arquivo não está corrompido.',
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

    # ── 1. Extração via tabelas (primária) ────────────────────────────────────
    label_map = _build_label_map_from_tables(all_tabelas)
    if label_map:
        dados.update(_extract_all_from_table_map(label_map))
        logger.debug("Tabela ANP: %d rótulos encontrados", len(label_map))

    # ── 2. Data/hora emissão (só no texto do cabeçalho) ───────────────────────
    for line in all_lines[:10]:
        m = _RE_DATETIME.search(line)
        if m:
            try:
                dados['data_emissao'] = datetime.strptime(
                    f"{m.group(1)} {m.group(2)}", '%d/%m/%Y %H:%M:%S'
                ).strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass
            break

    # ── 3. Fallback texto para campos ainda ausentes ───────────────────────────
    _campos_header = ('situacao', 'autorizacao', 'cnpj_anp', 'razao_social')
    if not all(dados.get(c) for c in _campos_header):
        text_header = _extract_header_block(all_lines)
        if _has_header_confusion(text_header):
            inline = _extract_header_inline(all_lines)
            if inline:
                text_header.update(inline)
        for k, v in text_header.items():
            if not dados.get(k):
                dados[k] = v

    _campos_endereco = ('endereco', 'municipio_uf')
    if not all(dados.get(c) for c in _campos_endereco):
        text_addr = _extract_address_block(all_lines)
        if _has_address_confusion(text_addr):
            addr_inline = _extract_address_inline(all_lines)
            if addr_inline:
                text_addr.update(addr_inline)
                for campo in ('complemento', 'bairro'):
                    if text_addr.get(campo) and text_addr.get('endereco') and \
                            text_addr[campo] == text_addr['endereco']:
                        text_addr.pop(campo)
        for k, v in text_addr.items():
            if not dados.get(k):
                dados[k] = v

    if not dados.get('bandeira') and not dados.get('nr_despacho'):
        text_desp = _extract_despacho_block(all_lines)
        for k, v in text_desp.items():
            if not dados.get(k):
                dados[k] = v

    if not dados.get('latitude'):
        text_geo = _extract_geo_block(all_lines)
        for k, v in text_geo.items():
            if not dados.get(k):
                dados[k] = v

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
