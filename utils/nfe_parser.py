"""Utilitário para parsear arquivos XML de NF-e (Nota Fiscal Eletrônica)."""
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


# Namespace da NF-e
_NS = 'http://www.portalfiscal.inf.br/nfe'


def _find(node, tag):
    """Busca um elemento pelo tag, considerando ou não o namespace."""
    el = node.find(f'{{{_NS}}}{tag}')
    if el is not None:
        return el
    return node.find(tag)


def _text(node, tag, default=''):
    """Retorna o texto de um elemento filho, ou default."""
    el = _find(node, tag)
    return el.text.strip() if (el is not None and el.text) else default


def _find_recursive(node, tag):
    """Busca recursivamente um elemento pelo tag."""
    el = node.find(f'.//{{{_NS}}}{tag}')
    if el is not None:
        return el
    return node.find(f'.//{tag}')


def _text_recursive(node, tag, default=''):
    """Retorna o texto de um elemento filho recursivamente."""
    el = _find_recursive(node, tag)
    return el.text.strip() if (el is not None and el.text) else default


def _float(node, tag, default=0.0, recursive=False):
    fn = _text_recursive if recursive else _text
    v = fn(node, tag, '')
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _dec(node, tag, recursive=False):
    """Valor monetário como Decimal, a partir do TEXTO do XML.

    Não passa por float de propósito: o vUnCom da NF-e vem com até 10 casas
    (ex.: 2.6279794128) e somar/dividir isso em ponto flutuante produz centavos
    fantasmas. Ausente/ilegível = 0, que é o neutro das somas do custo.
    """
    if node is None:
        return Decimal('0')
    fn = _text_recursive if recursive else _text
    try:
        return Decimal(fn(node, tag, '') or '0')
    except InvalidOperation:
        return Decimal('0')


def _valor_comercial(prod, imposto):
    """CUSTO COMERCIAL do item: ``(custo_total_item, valor_unit_comercial)``.

    Para combustível (e qualquer mercadoria com ICMS-ST), o custo real por
    unidade NÃO é o ``vUnCom``: o ICMS-ST, o IPI, o frete, o seguro e as despesas
    acessórias entram no custo mas ficam FORA do ``vProd``; o desconto sai.

        custo_total_item     = vProd + vICMSST + vIPI + vFrete + vSeg + vOutro - vDesc
        valor_unit_comercial = custo_total_item / qCom

    Ex. real (NF 1002, INTEGRACAO COMBUSTIVEIS): vProd 7.883,94 + vICMSST 576,06
    = 8.460,00; dividido por 3.000 = **2,8200** o litro, contra o vUnCom de
    2,6280 que a nota exibe.

    Os campos moram em DOIS grupos diferentes do XML: vFrete/vSeg/vOutro/vDesc
    ficam em ``<prod>``, enquanto vICMSST/vIPI ficam em ``<imposto>`` (dentro de
    ICMS.../IPI..., por isso a busca recursiva).

    Parte de ``vProd``, e não de ``vUnCom × qCom``: o vUnCom já vem arredondado
    na origem e multiplicá-lo devolveria um total que não fecha com a nota.

    Devolve ``(None, None)`` quando ``qCom`` é 0, ausente ou ilegível: sem
    quantidade não há custo unitário, e um custo total solto sem o unitário
    correspondente só serviria para confundir a conferência. NULL no banco
    significa "não sei", que é diferente de zero. Nunca levanta
    ZeroDivisionError — importação não pode cair por causa de um item torto.
    """
    q_com = _dec(prod, 'qCom')
    if q_com <= 0:
        return None, None
    custo = (_dec(prod, 'vProd')
             + _dec(imposto, 'vICMSST', recursive=True)
             + _dec(imposto, 'vIPI', recursive=True)
             + _dec(prod, 'vFrete')
             + _dec(prod, 'vSeg')
             + _dec(prod, 'vOutro')
             - _dec(prod, 'vDesc'))
    custo = custo.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    unit = (custo / q_com).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    return custo, unit


def parse_nfe_xml(xml_content: str) -> dict:
    """
    Parseia o conteúdo de um arquivo NF-e XML.

    Returns:
        dict com keys: chave, header (dict) e itens (list de dict)
        Raises ValueError se o XML não for uma NF-e válida.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise ValueError(f'XML inválido: {exc}') from exc

    # Aceita: <nfeProc>, <NFe> ou <nfeProc xmlns=...>
    if root.tag.endswith('NFe'):
        nfe = root
    else:
        nfe = _find(root, 'NFe')
        if nfe is None:
            raise ValueError('Nó <NFe> não encontrado no XML')

    infNFe = _find(nfe, 'infNFe')
    if infNFe is None:
        raise ValueError('Nó <infNFe> não encontrado')

    # Chave de acesso pelo atributo Id (remove prefixo "NFe")
    chave = infNFe.get('Id', '').replace('NFe', '')
    if not chave:
        # Tenta pegar do protNFe
        chave = _text_recursive(root, 'chNFe')
    ide = _find(infNFe, 'ide')
    num_nota = _text(ide, 'nNF') if ide is not None else ''
    serie = _text(ide, 'serie') if ide is not None else ''
    nat_op = _text(ide, 'natOp') if ide is not None else ''
    dh_emi_raw = _text(ide, 'dhEmi') if ide is not None else ''
    # Fallback para dEmi (versão antiga da NF-e)
    if not dh_emi_raw and ide is not None:
        dh_emi_raw = _text(ide, 'dEmi')
    data_emissao = None
    if dh_emi_raw:
        # Formatos: 2024-01-15T10:30:00-03:00 ou 2024-01-15
        try:
            data_emissao = datetime.fromisoformat(dh_emi_raw[:10]).date()
        except ValueError:
            pass

    # ---- EMIT ----
    emit = _find(infNFe, 'emit')
    emit_cnpj = ''
    if emit is not None:
        emit_cnpj = _text(emit, 'CNPJ') or _text(emit, 'CPF')
    emit_nome = _text(emit, 'xNome') if emit is not None else ''
    emit_uf = ''
    if emit is not None:
        ender_emit = _find(emit, 'enderEmit')
        if ender_emit is not None:
            emit_uf = _text(ender_emit, 'UF')

    # ---- DEST ----
    dest = _find(infNFe, 'dest')
    dest_cnpj = ''
    if dest is not None:
        dest_cnpj = _text(dest, 'CNPJ') or _text(dest, 'CPF')
    dest_nome = _text(dest, 'xNome') if dest is not None else ''
    dest_uf = ''
    if dest is not None:
        ender_dest = _find(dest, 'enderDest')
        if ender_dest is not None:
            dest_uf = _text(ender_dest, 'UF')

    # ---- TOTAL ----
    total = _find(infNFe, 'total')
    icms_tot = _find(total, 'ICMSTot') if total is not None else None
    vNF = _float(icms_tot, 'vNF') if icms_tot is not None else 0.0
    vICMS = _float(icms_tot, 'vICMS') if icms_tot is not None else 0.0
    vPIS = _float(icms_tot, 'vPIS') if icms_tot is not None else 0.0
    vCOFINS = _float(icms_tot, 'vCOFINS') if icms_tot is not None else 0.0
    vIPI = _float(icms_tot, 'vIPI') if icms_tot is not None else 0.0

    # Primary CFOP: primeiro item
    primary_cfop = ''

    # ---- ITENS (det) ----
    itens = []
    for det in infNFe.findall(f'{{{_NS}}}det') or infNFe.findall('det'):
        n_item = det.get('nItem', '')
        prod = _find(det, 'prod')
        if prod is None:
            continue

        cfop = _text(prod, 'CFOP')
        if not primary_cfop and cfop:
            primary_cfop = cfop

        imposto = _find(det, 'imposto')
        v_icms_item = 0.0
        v_pis_item = 0.0
        v_cofins_item = 0.0
        if imposto is not None:
            v_icms_item = _float(imposto, 'vICMS', recursive=True)
            v_pis_item = _float(imposto, 'vPIS', recursive=True)
            v_cofins_item = _float(imposto, 'vCOFINS', recursive=True)

        custo_total, valor_unit_comercial = _valor_comercial(prod, imposto)

        itens.append({
            'num_item': int(n_item) if n_item.isdigit() else 0,
            'codigo_produto': _text(prod, 'cProd'),
            'descricao': _text(prod, 'xProd'),
            'ncm': _text(prod, 'NCM'),
            'cfop': cfop,
            'unidade': _text(prod, 'uCom') or _text(prod, 'uTrib'),
            'quantidade': _float(prod, 'qCom'),
            'valor_unitario': _float(prod, 'vUnCom'),
            'valor_total': _float(prod, 'vProd'),
            'valor_icms': v_icms_item,
            'valor_pis': v_pis_item,
            'valor_cofins': v_cofins_item,
            'custo_total_item': custo_total,
            'valor_unit_comercial': valor_unit_comercial,
        })

    header = {
        'chave_acesso': chave,
        'num_nota': num_nota,
        'serie': serie,
        'natureza_operacao': nat_op,
        'data_emissao': data_emissao,
        'emit_cnpj': emit_cnpj,
        'emit_nome': emit_nome,
        'emit_uf': emit_uf,
        'dest_cnpj': dest_cnpj,
        'dest_nome': dest_nome,
        'dest_uf': dest_uf,
        'valor_total': vNF,
        'valor_icms': vICMS,
        'valor_pis': vPIS,
        'valor_cofins': vCOFINS,
        'valor_ipi': vIPI,
        'cfop': primary_cfop,
    }

    return {'chave': chave, 'header': header, 'itens': itens}
