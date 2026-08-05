# -*- coding: utf-8 -*-
"""
Gerador de DANFCE (NFC-e, modelo 65) em ReportLab — Python puro, SEM navegador.
Padrao NACIONAL: le tudo do XML, inclusive QR Code e URL de consulta (infNFeSupl),
que sao os unicos pedacos estaduais e ja vem dentro do proprio XML. Um template p/ toda UF.

API para o app:
    from utils.danfce import gerar_danfce_pdf
    pdf_bytes = gerar_danfce_pdf(xml)     # xml: str ou bytes -> bytes (PDF)

CLI (teste):
    python danfce.py entrada.xml saida.pdf
"""
import sys, re, io
import xml.etree.ElementTree as ET
import qrcode
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

MM = 72 / 25.4
LARGURA = 80 * MM           # bobina 80mm
MARGEM = 3 * MM
CONT = LARGURA - 2 * MARGEM  # largura util
FONT = 'Courier'
FONT_B = 'Courier-Bold'

# ---------- parse helpers ----------
def _strip_ns(xml_bytes):
    txt = xml_bytes.decode('utf-8', 'ignore') if isinstance(xml_bytes, bytes) else xml_bytes
    txt = re.sub(r'\sxmlns(:\w+)?="[^"]+"', '', txt)
    txt = re.sub(r'<(/?)\w+:', r'<\1', txt)
    return ET.fromstring(txt)

def _t(node, path, default=''):
    if node is None:
        return default
    el = node.find(path)
    return el.text.strip() if (el is not None and el.text) else default

def _cnpj(c):
    c = re.sub(r'\D', '', c or '')
    if len(c) == 14:
        return f'{c[0:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:14]}'
    if len(c) == 11:
        return f'{c[0:3]}.{c[3:6]}.{c[6:9]}-{c[9:11]}'
    return c

def _num(v, dec=2):
    try:
        return f'{float(v):,.{dec}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return v or '0,00'

def _chave(ch):
    ch = re.sub(r'\D', '', ch or '')
    return ' '.join(ch[i:i+4] for i in range(0, len(ch), 4))

def _dt(s):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})', s or '')
    return f'{m.group(3)}/{m.group(2)}/{m.group(1)} {m.group(4)}:{m.group(5)}:{m.group(6)}' if m else (s or '')

_TPAG = {
    '01': 'Dinheiro', '02': 'Cheque', '03': 'Cartao de Credito', '04': 'Cartao de Debito',
    '05': 'Credito Loja', '10': 'Vale Alimentacao', '11': 'Vale Refeicao', '12': 'Vale Presente',
    '13': 'Vale Combustivel', '15': 'Boleto', '16': 'Deposito', '17': 'PIX',
    '18': 'Transferencia', '19': 'Fidelidade', '90': 'Sem pagamento', '99': 'Outros',
}

def _wrap(c, text, font, size, maxw):
    """Quebra texto por largura real (stringWidth). Preserva quebras existentes."""
    out = []
    for raw in (text or '').split('\n'):
        palavras, linha = raw.split(' '), ''
        for p in palavras:
            teste = (linha + ' ' + p).strip()
            if c.stringWidth(teste, font, size) <= maxw or not linha:
                linha = teste
            else:
                out.append(linha); linha = p
        out.append(linha)
    return out or ['']

# ---------- extracao ----------
def _extrair(xml):
    root = _strip_ns(xml)
    inf = root.find('.//infNFe')
    ide, emit = inf.find('ide'), inf.find('emit')
    ender = emit.find('enderEmit')
    tot = inf.find('.//ICMSTot')
    supl, prot = root.find('.//infNFeSupl'), root.find('.//infProt')

    itens = []
    for det in inf.findall('det'):
        prod = det.find('prod')
        itens.append(dict(
            cProd=_t(prod, 'cProd'), xProd=_t(prod, 'xProd'), infAd=_t(det, 'infAdProd'),
            qCom=_num(_t(prod, 'qCom'), 3), uCom=_t(prod, 'uCom'),
            vUn=_num(_t(prod, 'vUnCom'), 2), vProd=_num(_t(prod, 'vProd'), 2),
        ))

    pags, vTroco = [], _num('0')
    pag = inf.find('pag')
    if pag is not None:
        for dp in pag.findall('detPag'):
            pags.append((_TPAG.get(_t(dp, 'tPag'), 'Outros'), _num(_t(dp, 'vPag'))))
        if _t(pag, 'vTroco'):
            vTroco = _num(_t(pag, 'vTroco'))

    dest = inf.find('dest')
    if dest is not None:
        doc = _cnpj(_t(dest, 'CPF') or _t(dest, 'CNPJ'))
        consumidor = f"CONSUMIDOR: {_t(dest, 'xNome')} {doc}".strip()
    else:
        consumidor = 'CONSUMIDOR NAO IDENTIFICADO'

    qr_txt = _t(supl, 'qrCode')
    return dict(
        emit_nome=_t(emit, 'xNome'), emit_cnpj=_cnpj(_t(emit, 'CNPJ')),
        emit_end=f"{_t(ender,'xLgr')}, {_t(ender,'nro')} {_t(ender,'xCpl')}".strip(),
        emit_bairro=_t(ender, 'xBairro'), emit_mun=f"{_t(ender,'xMun')} - {_t(ender,'UF')}",
        emit_ie=_t(emit, 'IE'), emit_fone=_t(ender, 'fone'),
        itens=itens, pags=pags, vTroco=vTroco,
        qtd_itens=str(len(itens)), v_total=_num(_t(tot, 'vProd')), v_desc=_num(_t(tot, 'vDesc')),
        v_pagar=_num(_t(tot, 'vNF')), v_trib=_num(_t(tot, 'vTotTrib')),
        consumidor=consumidor, nNF=_t(ide, 'nNF').zfill(9), serie=_t(ide, 'serie'),
        dhEmi=_dt(_t(ide, 'dhEmi')),
        nProt=_t(prot, 'nProt'), dhRecbto=_dt(_t(prot, 'dhRecbto')),
        chave=_chave((inf.get('Id') or '').replace('NFe', '')),
        url_chave=_t(supl, 'urlChave'), qr_txt=qr_txt, infCpl=_t(inf.find('infAdic'), 'infCpl'),
    )

# ---------- desenho (uma pagina de altura variavel) ----------
def gerar_danfce_pdf(xml):
    d = _extrair(xml)
    buf = io.BytesIO()
    # canvas temporario so pra medir com stringWidth
    medidor = canvas.Canvas(io.BytesIO())

    ops = []  # (tipo, ...)
    def linha(txt, font=FONT, size=7.5, align='L', lead=None):
        lead = lead or (size + 2)
        for ln in _wrap(medidor, txt, font, size, CONT):
            ops.append(('txt', ln, font, size, align, lead))
    def item_valor(esq, dir_, size=7.5):
        ops.append(('lr', esq, dir_, size, size + 2))
    def hr():
        ops.append(('hr', 4))
    def gap(h=2):
        ops.append(('gap', h))
    def qr(txt):
        ops.append(('qr', txt, 40 * MM))

    # cabecalho
    for ln in _wrap(medidor, d['emit_nome'], FONT_B, 8.5, CONT):
        ops.append(('txt', ln, FONT_B, 8.5, 'C', 10))
    linha('CNPJ: ' + d['emit_cnpj'], size=7.3, align='C')
    linha(d['emit_end'], size=7.3, align='C')
    linha(d['emit_bairro'] + ' - ' + d['emit_mun'], size=7.3, align='C')
    linha('IE: %s   Fone: %s' % (d['emit_ie'], d['emit_fone']), size=7.3, align='C')
    hr()
    for ln in ['DANFE NFC-e - Documento Auxiliar', 'da Nota Fiscal de Consumidor Eletronica']:
        ops.append(('txt', ln, FONT_B, 7.6, 'C', 9))
    hr()
    # itens
    ops.append(('txt', 'COD  DESCRICAO', FONT_B, 7.2, 'L', 9))
    ops.append(('lrb', 'QTD  UN  VL.UN', 'VL.TOTAL', 7.2, 9))
    for it in d['itens']:
        linha('%s  %s' % (it['cProd'], it['xProd']), size=7.4)
        if it['infAd']:
            linha(it['infAd'], size=6.6)
        item_valor('%s %s x %s' % (it['qCom'], it['uCom'], it['vUn']), it['vProd'], 7.4)
        gap(1)
    hr()
    # totais
    item_valor('QTD. TOTAL DE ITENS', d['qtd_itens'])
    item_valor('Descontos R$', d['v_desc'])
    ops.append(('lrb', 'VALOR TOTAL R$', d['v_total'], 8, 10))
    hr()
    # pagamento
    ops.append(('lrb', 'FORMA PAGAMENTO', 'VALOR PAGO R$', 7.5, 10))
    for nome, val in d['pags']:
        item_valor(nome, val)
    item_valor('Troco R$', d['vTroco'])
    hr()
    linha('Consulte pela Chave de Acesso em:', size=7, align='C')
    linha(d['url_chave'], size=7, align='C')
    for ln in _wrap(medidor, d['chave'], FONT_B, 7.6, CONT):
        ops.append(('txt', ln, FONT_B, 7.6, 'C', 9))
    hr()
    ops.append(('txt', d['consumidor'], FONT_B, 7.6, 'C', 10))
    linha('NFC-e no %s  Serie: %s' % (d['nNF'], d['serie']), size=7, align='C')
    linha('Emissao: ' + d['dhEmi'], size=7, align='C')
    linha('Protocolo de Autorizacao: ' + d['nProt'], size=7, align='C')
    linha('Data de Autorizacao: ' + d['dhRecbto'], size=7, align='C')
    gap(3)
    if d['qr_txt']:
        qr(d['qr_txt'])
    hr()
    if d['infCpl']:
        linha(d['infCpl'], size=6.6)
        hr()
    linha('Tributos Totais Incidentes', size=6.8, align='C')
    linha('(Lei Federal 12.741/12): R$ ' + d['v_trib'], size=6.8, align='C')

    # ---- mede altura total ----
    def altura(op):
        if op[0] == 'txt':
            return op[5]
        if op[0] in ('lr', 'lrb'):
            return op[4]
        if op[0] == 'hr':
            return op[1]
        if op[0] == 'gap':
            return op[1]
        if op[0] == 'qr':
            return op[2] + 4
        return 0
    total_h = MARGEM * 2 + sum(altura(o) for o in ops)

    # ---- desenha ----
    c = canvas.Canvas(buf, pagesize=(LARGURA, total_h))
    y = total_h - MARGEM
    for op in ops:
        if op[0] == 'txt':
            _, txt, font, size, align, lead = op
            c.setFont(font, size)
            if align == 'C':
                c.drawCentredString(LARGURA / 2, y - size, txt)
            else:
                c.drawString(MARGEM, y - size, txt)
            y -= lead
        elif op[0] in ('lr', 'lrb'):
            font = FONT_B if op[0] == 'lrb' else FONT
            _, esq, dir_, size, lead = op
            c.setFont(font, size)
            c.drawString(MARGEM, y - size, esq)
            c.drawRightString(LARGURA - MARGEM, y - size, dir_)
            y -= lead
        elif op[0] == 'hr':
            y -= op[1] / 2
            c.setDash(1, 2)
            c.line(MARGEM, y, LARGURA - MARGEM, y)
            c.setDash()
            y -= op[1] / 2
        elif op[0] == 'gap':
            y -= op[1]
        elif op[0] == 'qr':
            _, txt, size = op
            qr = qrcode.QRCode(box_size=10, border=0)
            qr.add_data(txt); qr.make(fit=True)
            img = qr.make_image(fill_color='black', back_color='white')
            b = io.BytesIO(); img.save(b, format='PNG'); b.seek(0)
            c.drawImage(ImageReader(b), (LARGURA - size) / 2, y - size, size, size)
            y -= size + 4
    c.showPage()
    c.save()
    return buf.getvalue()

def main():
    xml = open(sys.argv[1], 'rb').read()
    open(sys.argv[2], 'wb').write(gerar_danfce_pdf(xml))
    print('OK ->', sys.argv[2])

if __name__ == '__main__':
    main()
