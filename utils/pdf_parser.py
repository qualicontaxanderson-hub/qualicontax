"""Utilitário para extração de transações de PDFs de extratos bancários"""
import re
import io
import logging

logger = logging.getLogger(__name__)

# Tenta importar pdfplumber; se não disponível, usa fallback
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    logger.warning("pdfplumber não disponível. Instale com: pip install pdfplumber==0.11.9")


# Padrões de data aceitos no Brasil
_DATA_PATTERNS = [
    # DD/MM/YYYY ou DD/MM/YY
    re.compile(r'\b(\d{2}/\d{2}/(?:\d{4}|\d{2}))\b'),
    # DD-MM-YYYY
    re.compile(r'\b(\d{2}-\d{2}-\d{4})\b'),
    # YYYY-MM-DD
    re.compile(r'\b(\d{4}-\d{2}-\d{2})\b'),
]

# Padrão de valor monetário: 1.234,56 ou 1234,56 ou -1.234,56
_VALOR_PATTERN = re.compile(
    r'(?<!\d)(-?\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})|'
    r'-?\s?\d{1,9},\d{2})(?!\d)'
)


def _parse_valor(texto):
    """
    Converte texto de valor monetário brasileiro para float.
    Ex: '1.234,56' → 1234.56 ; '-500,00' → -500.0
    """
    texto = texto.replace(' ', '').replace('.', '').replace(',', '.')
    try:
        return float(texto)
    except ValueError:
        return None


def _extrair_data(linha):
    """Tenta extrair a primeira data encontrada na linha."""
    for pat in _DATA_PATTERNS:
        m = pat.search(linha)
        if m:
            return m.group(1)
    return None


def _linha_e_transacao(linha):
    """
    Heurística: linha é provavelmente uma transação se contém uma data
    e pelo menos um valor monetário.
    """
    tem_data = any(pat.search(linha) for pat in _DATA_PATTERNS)
    if not tem_data:
        return False
    valores = _VALOR_PATTERN.findall(linha)
    return len(valores) >= 1


def extrair_transacoes_pdf(arquivo_bytes):
    """
    Extrai transações de um arquivo PDF de extrato bancário.

    Args:
        arquivo_bytes (bytes): Conteúdo binário do arquivo PDF

    Returns:
        dict: {
            'sucesso': bool,
            'transacoes': list of dict (data, descricao, valor, tipo),
            'texto_bruto': str,
            'aviso': str ou None,
            'erro': str ou None,
        }
    """
    if not PDFPLUMBER_AVAILABLE:
        return {
            'sucesso': False,
            'transacoes': [],
            'texto_bruto': '',
            'aviso': None,
            'erro': (
                'Biblioteca pdfplumber não instalada. '
                'Execute: pip install pdfplumber==0.11.9'
            ),
        }

    texto_completo = []
    transacoes = []

    try:
        with pdfplumber.open(io.BytesIO(arquivo_bytes)) as pdf:
            for pagina in pdf.pages:
                # Tenta extrair tabelas primeiro (mais preciso)
                tabelas = pagina.extract_tables()
                for tabela in tabelas:
                    for row in tabela:
                        if not row:
                            continue
                        linha = ' | '.join(cell or '' for cell in row)
                        texto_completo.append(linha)
                        t = _processar_linha(linha)
                        if t:
                            transacoes.append(t)

                # Também extrai texto livre (captura linhas que não estão em tabelas)
                texto_pagina = pagina.extract_text() or ''
                for linha in texto_pagina.splitlines():
                    linha = linha.strip()
                    if not linha:
                        continue
                    texto_completo.append(linha)
                    if _linha_e_transacao(linha):
                        t = _processar_linha(linha)
                        if t:
                            # Evita duplicatas (mesma data + valor + descrição parcial)
                            chave = (t['data'], t['valor'])
                            if not any(
                                ex['data'] == t['data'] and ex['valor'] == t['valor']
                                for ex in transacoes
                            ):
                                transacoes.append(t)

    except Exception as exc:
        logger.error("Erro ao processar PDF: %s", exc)
        return {
            'sucesso': False,
            'transacoes': [],
            'texto_bruto': '\n'.join(texto_completo),
            'aviso': None,
            'erro': f'Erro ao processar PDF: {exc}',
        }

    aviso = None
    if not transacoes:
        aviso = (
            'Nenhuma transação identificada automaticamente. '
            'O PDF pode estar em formato de imagem (escaneado) ou '
            'ter uma estrutura não reconhecida. '
            'Use o texto bruto abaixo para revisar manualmente.'
        )

    return {
        'sucesso': True,
        'transacoes': transacoes,
        'texto_bruto': '\n'.join(texto_completo),
        'aviso': aviso,
        'erro': None,
    }


def _processar_linha(linha):
    """
    Tenta converter uma linha de texto em uma transação estruturada.

    Returns:
        dict ou None
    """
    data = _extrair_data(linha)
    if not data:
        return None

    valores_raw = _VALOR_PATTERN.findall(linha)
    if not valores_raw:
        return None

    # O último valor numérico costuma ser o valor da transação
    valor_raw = valores_raw[-1].replace(' ', '')
    valor = _parse_valor(valor_raw)
    if valor is None:
        return None

    # Descrição: tudo entre a data e o valor encontrado, com espaços colapsados
    descricao = linha
    # Remove a data
    for pat in _DATA_PATTERNS:
        descricao = pat.sub('', descricao)
    # Remove the last matched value occurrence using its position in the original
    last_match = None
    for m in _VALOR_PATTERN.finditer(descricao):
        last_match = m
    if last_match:
        descricao = descricao[:last_match.start()] + descricao[last_match.end():]
    # Limpa separadores comuns e espaços extras
    descricao = re.sub(r'\s*\|\s*', ' ', descricao)
    descricao = re.sub(r'\s{2,}', ' ', descricao).strip(' -|/')
    descricao = descricao or 'Sem descrição'

    # Tipo CREDITO se valor > 0, DEBITO se negativo
    tipo = 'CREDITO' if valor >= 0 else 'DEBITO'
    valor_abs = abs(valor)

    return {
        'data': data,
        'descricao': descricao[:200],
        'valor': valor_abs,
        'tipo': tipo,
        'valor_original': valor,
    }
