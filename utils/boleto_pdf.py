# -*- coding: utf-8 -*-
"""PDF do boleto montado por nós (Bradesco) — 14/11/2026.

A API "Cobrança com QR Code" do Bradesco registra o boleto e devolve a linha digitável e o Pix
copia-e-cola, mas NÃO devolve PDF. Este módulo monta o boleto em A4:

* em cima, o **Recibo do Pagador**: logo e contato do escritório, pagador, vencimento, valor, a lista
  "Serviços cobrados neste boleto" (cada serviço com o seu valor e o total) e o QR do Pix;
* embaixo, a **Ficha de Compensação** no padrão Febraban (campos, instruções, código de barras
  intercalado 2 de 5).

Modelo aprovado pelo Anderson (14/11/2026): "Ficou muito bom"; pediu o logo da Qualicontax e o
telefone/WhatsApp 11 2523-1815.
"""
import io
import os
import re
from datetime import date
from decimal import Decimal

LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'images', 'logo.png')


# ---------------------------------------------------------------------------
# Regras Febraban
# ---------------------------------------------------------------------------
def _num(s):
    return re.sub(r'\D', '', str(s or ''))


def mod10(n):
    s, p = 0, 2
    for d in reversed(n):
        x = int(d) * p
        s += x // 10 + x % 10
        p = 1 if p == 2 else 2
    r = s % 10
    return str(0 if r == 0 else 10 - r)


def mod11_barra(n):
    s, p = 0, 2
    for d in reversed(n):
        s += int(d) * p
        p = 2 if p == 9 else p + 1
    r = 11 - s % 11
    return '1' if r in (0, 10, 11) else str(r)


def dv_nosso_numero_bradesco(carteira, nosso):
    s, p = 0, 2
    for d in reversed(_num(carteira).zfill(2) + _num(nosso).zfill(11)):
        s += int(d) * p
        p = 2 if p == 7 else p + 1
    r = s % 11
    return '0' if r == 0 else ('P' if r == 1 else str(11 - r))


def barras_da_linha(linha):
    """Os 44 números do código de barras a partir da linha digitável (47 números)."""
    d = _num(linha)
    if len(d) != 47:
        return None
    return d[0:4] + d[32] + d[33:47] + d[4:9] + d[10:20] + d[21:31]


def linha_formatada(linha):
    d = _num(linha)
    if len(d) != 47:
        return linha or ''
    return f'{d[0:5]}.{d[5:10]} {d[10:15]}.{d[15:21]} {d[21:26]}.{d[26:32]} {d[32]} {d[33:47]}'


def montar_bradesco(agencia, carteira, nosso, conta, vencimento, valor):
    """(barras, linha) calculados pela regra do Bradesco — só para o MODELO; o boleto de verdade usa a
    linha que o banco devolve."""
    livre = _num(agencia).zfill(4) + _num(carteira).zfill(2) + _num(nosso).zfill(11) + _num(conta).zfill(7) + '0'
    fator = str(1000 + (vencimento - date(2025, 2, 22)).days).zfill(4)
    sem_dv = '2379' + fator + str(int(Decimal(str(valor)) * 100)).zfill(10) + livre
    dv = mod11_barra(sem_dv)
    barra = sem_dv[:4] + dv + sem_dv[4:]
    c1 = '2379' + livre[:5]; c1 += mod10(c1)
    c2 = livre[5:15]; c2 += mod10(c2)
    c3 = livre[15:25]; c3 += mod10(c3)
    return barra, c1 + c2 + c3 + dv + barra[5:19]


def brl(v):
    return 'R$ ' + f'{Decimal(str(v or 0)):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _data(d):
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.strftime('%d/%m/%Y') if d else ''


# ---------------------------------------------------------------------------
# O PDF
# ---------------------------------------------------------------------------
def gerar(d):
    """``d``: dict com beneficiario{nome, doc, endereco, telefone}, pagador{nome, doc, endereco},
    itens[{nome, valor}], valor, vencimento, emissao, numero_documento, nosso_numero (já formatado),
    carteira, agencia_codigo, linha_digitavel (47 números), pix (copia-e-cola ou ''), instrucoes[str],
    modelo (bool: marca d'água). Devolve os bytes do PDF."""
    from reportlab.graphics.barcode.common import I2of5
    from reportlab.lib.colors import HexColor, black
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    ben, pag = d['beneficiario'], d['pagador']
    itens = d.get('itens') or []
    total = Decimal(str(d['valor']))
    linha = _num(d.get('linha_digitavel'))
    barras = barras_da_linha(linha)
    venc = _data(d['vencimento'])
    emis = _data(d.get('emissao') or date.today())
    instr = d.get('instrucoes') or []

    AZUL = HexColor('#1f3a5f'); CINZA = HexColor('#6b7280'); LINHA_C = HexColor('#9ca3af')
    VERMELHO = HexColor('#cc092f'); VERDE = HexColor('#15803d')

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    L, R = 12 * mm, W - 12 * mm
    c.setTitle(f"Boleto {pag.get('nome') or ''} - venc {venc}")
    c.setAuthor(ben.get('nome') or '')

    def cabe(txt, fonte, tam, largura):
        while txt and stringWidth(txt, fonte, tam) > largura:
            txt = txt[:-4] + '...'
        return txt

    def rot(x, y, txt):
        c.setFont('Helvetica', 6); c.setFillColor(CINZA); c.drawString(x + 1.2 * mm, y - 2.6 * mm, txt); c.setFillColor(black)

    def caixa(x, y, w, h, r, v, bold=False, direita=False, size=8.5):
        c.setStrokeColor(LINHA_C); c.setLineWidth(0.5); c.rect(x, y - h, w, h)
        rot(x, y, r)
        fonte = 'Helvetica-Bold' if bold else 'Helvetica'
        v = cabe(str(v or ''), fonte, size, w - 3 * mm)
        c.setFont(fonte, size)
        if direita:
            c.drawRightString(x + w - 1.5 * mm, y - 6.3 * mm, v)
        else:
            c.drawString(x + 1.2 * mm, y - 6.3 * mm, v)

    if d.get('modelo'):
        c.saveState(); c.setFillColor(HexColor('#f3f4f6')); c.setFont('Helvetica-Bold', 60)
        c.translate(W / 2, H / 2); c.rotate(35); c.drawCentredString(0, 0, 'MODELO - SEM VALOR'); c.restoreState()

    # ===== Recibo do Pagador =====
    y = H - 12 * mm
    x_txt = L
    if os.path.exists(LOGO):
        img = ImageReader(LOGO)
        iw, ih = img.getSize()
        lh = 15 * mm
        lw = lh * iw / ih
        c.drawImage(img, L, y - lh + 2 * mm, lw, lh, mask='auto')
        x_txt = L + lw + 4 * mm
    c.setFont('Helvetica-Bold', 9); c.setFillColor(AZUL); c.drawString(x_txt, y - 1 * mm, ben.get('nome') or '')
    c.setFillColor(CINZA); c.setFont('Helvetica', 7.5)
    c.drawString(x_txt, y - 4.8 * mm, f"CNPJ {ben.get('doc') or ''}")
    c.drawString(x_txt, y - 8.4 * mm, cabe(ben.get('endereco') or '', 'Helvetica', 7.5, R - x_txt - 50 * mm))
    if ben.get('telefone'):
        c.setFillColor(VERDE); c.setFont('Helvetica-Bold', 8)
        c.drawString(x_txt, y - 12.2 * mm, f"Telefone e WhatsApp: {ben['telefone']}")
    c.setFillColor(black); c.setFont('Helvetica-Bold', 10); c.drawRightString(R, y - 1 * mm, 'Recibo do Pagador')
    c.setFont('Helvetica', 7.5); c.drawRightString(R, y - 4.8 * mm, 'Boleto Bradesco com Pix')

    y -= 17 * mm
    cw = R - L
    caixa(L, y, cw * .55, 9 * mm, 'Pagador', f"{pag.get('nome') or ''} - {pag.get('doc_rotulo') or 'CNPJ'} {pag.get('doc') or ''}", bold=True)
    caixa(L + cw * .55, y, cw * .15, 9 * mm, 'Nº do documento', d.get('numero_documento'))
    caixa(L + cw * .70, y, cw * .15, 9 * mm, 'Vencimento', venc, bold=True)
    caixa(L + cw * .85, y, cw * .15, 9 * mm, 'Valor', brl(total), bold=True, direita=True)

    y -= 13 * mm
    c.setFont('Helvetica-Bold', 9); c.drawString(L, y, 'Serviços cobrados neste boleto')
    y -= 2 * mm
    qr_w = 40 * mm
    tab_r = R - qr_w - 6 * mm
    c.setFillColor(HexColor('#eef2f7')); c.rect(L, y - 6 * mm, tab_r - L, 6 * mm, stroke=0, fill=1); c.setFillColor(black)
    c.setFont('Helvetica-Bold', 7.5); c.drawString(L + 2 * mm, y - 4.2 * mm, 'Descrição')
    c.drawRightString(tab_r - 2 * mm, y - 4.2 * mm, 'Valor')
    yy = y - 6 * mm
    max_linhas = 8
    mostrar = itens if len(itens) <= max_linhas else itens[:max_linhas - 1]
    resto = [] if len(itens) <= max_linhas else itens[max_linhas - 1:]
    c.setFont('Helvetica', 8)
    for it in mostrar:
        yy -= 6 * mm
        c.drawString(L + 2 * mm, yy + 1.8 * mm, cabe(it['nome'], 'Helvetica', 8, tab_r - L - 30 * mm))
        c.drawRightString(tab_r - 2 * mm, yy + 1.8 * mm, brl(it['valor']))
        c.setStrokeColor(HexColor('#e5e7eb')); c.line(L, yy, tab_r, yy)
    if resto:
        yy -= 6 * mm
        c.drawString(L + 2 * mm, yy + 1.8 * mm, f'Demais serviços ({len(resto)})')
        c.drawRightString(tab_r - 2 * mm, yy + 1.8 * mm, brl(sum(Decimal(str(i['valor'])) for i in resto)))
        c.line(L, yy, tab_r, yy)
    yy -= 6.5 * mm
    c.setFont('Helvetica-Bold', 9); c.drawString(L + 2 * mm, yy + 2 * mm, 'Total')
    c.drawRightString(tab_r - 2 * mm, yy + 2 * mm, brl(total))
    if d.get('rodape_recibo'):
        c.setFont('Helvetica', 7); c.setFillColor(CINZA)
        c.drawString(L + 2 * mm, yy - 3 * mm, cabe(d['rodape_recibo'], 'Helvetica', 7, tab_r - L - 4 * mm)); c.setFillColor(black)

    qx = R - qr_w
    c.setStrokeColor(LINHA_C); c.roundRect(qx - 2 * mm, y - 50 * mm, qr_w + 2 * mm, 50 * mm, 2 * mm)
    c.setFont('Helvetica-Bold', 9); c.drawCentredString(qx + qr_w / 2 - 1 * mm, y - 5 * mm, 'Pague com Pix')
    if d.get('pix'):
        import qrcode
        qr = qrcode.QRCode(border=1, box_size=6); qr.add_data(d['pix']); qr.make(fit=True)
        b = io.BytesIO(); qr.make_image().save(b, format='PNG'); b.seek(0)
        c.drawImage(ImageReader(b), qx + 2 * mm, y - 43 * mm, 34 * mm, 34 * mm)
        c.setFont('Helvetica', 6.5); c.setFillColor(CINZA)
        c.drawCentredString(qx + qr_w / 2 - 1 * mm, y - 47 * mm, 'Abra o app do banco e leia o QR'); c.setFillColor(black)
    else:
        c.setFont('Helvetica', 7); c.setFillColor(CINZA)
        c.drawCentredString(qx + qr_w / 2 - 1 * mm, y - 25 * mm, 'Pague pelo código de barras'); c.setFillColor(black)

    y_corte = min(y - 58 * mm, yy - 10 * mm)
    c.setDash(2, 2); c.setStrokeColor(CINZA); c.line(L, y_corte, R, y_corte); c.setDash()
    c.setFont('Helvetica', 6); c.setFillColor(CINZA); c.drawRightString(R, y_corte + 1.5 * mm, 'Corte na linha pontilhada')
    c.setFillColor(black)

    # ===== Ficha de Compensação =====
    y = y_corte - 6 * mm
    c.setFillColor(VERMELHO); c.setFont('Helvetica-Bold', 14); c.drawString(L, y - 6 * mm, 'Bradesco'); c.setFillColor(black)
    c.setLineWidth(1.2); c.setStrokeColor(black)
    c.line(L + 28 * mm, y - 1 * mm, L + 28 * mm, y - 8 * mm); c.line(L + 46 * mm, y - 1 * mm, L + 46 * mm, y - 8 * mm)
    c.setFont('Helvetica-Bold', 14); c.drawCentredString(L + 37 * mm, y - 6 * mm, '237-2')
    c.setFont('Helvetica-Bold', 10.5); c.drawRightString(R, y - 6 * mm, linha_formatada(linha))
    c.line(L, y - 8 * mm, R, y - 8 * mm); c.setLineWidth(0.5)
    y -= 8 * mm
    dir_w = 45 * mm; esq = R - L - dir_w; xd = R - dir_w; hh = 8.5 * mm
    caixa(L, y, esq, hh, 'Local de pagamento',
          'Pagável preferencialmente na rede Bradesco, no Bradesco Expresso ou pelo Pix', size=7.5)
    caixa(xd, y, dir_w, hh, 'Vencimento', venc, bold=True, direita=True); y -= hh
    caixa(L, y, esq, hh, 'Beneficiário', f"{ben.get('nome') or ''} - CNPJ {ben.get('doc') or ''}", size=7.5)
    caixa(xd, y, dir_w, hh, 'Agência / Código do beneficiário', d.get('agencia_codigo'), direita=True); y -= hh
    x = L
    for f, r_, v in [(.18, 'Data do documento', emis), (.22, 'Nº do documento', d.get('numero_documento')),
                     (.15, 'Espécie doc.', 'DM'), (.10, 'Aceite', 'N'), (.35, 'Data do processamento', emis)]:
        caixa(x, y, esq * f, hh, r_, v); x += esq * f
    caixa(xd, y, dir_w, hh, 'Nosso número', d.get('nosso_numero'), direita=True); y -= hh
    x = L
    for f, r_, v in [(.18, 'Uso do banco', ''), (.12, 'Carteira', d.get('carteira')), (.12, 'Espécie', 'R$'),
                     (.28, 'Quantidade', ''), (.30, 'Valor', '')]:
        caixa(x, y, esq * f, hh, r_, v); x += esq * f
    caixa(xd, y, dir_w, hh, '(=) Valor do documento', brl(total), bold=True, direita=True); y -= hh
    alt = hh * 5
    c.setStrokeColor(LINHA_C); c.rect(L, y - alt, esq, alt)
    rot(L, y, 'Instruções (texto de responsabilidade do beneficiário)')
    c.setFont('Helvetica', 8)
    for i, t in enumerate(instr[:5]):
        c.drawString(L + 2 * mm, y - 7 * mm - i * 4.2 * mm, cabe(t, 'Helvetica', 8, esq - 5 * mm))
    if itens and len(instr) < 6:
        c.setFont('Helvetica', 7); c.setFillColor(CINZA)
        ref = 'Referente a: ' + '; '.join(i['nome'] for i in itens)
        c.drawString(L + 2 * mm, y - 7 * mm - min(len(instr), 5) * 4.2 * mm, cabe(ref, 'Helvetica', 7, esq - 5 * mm))
        c.setFillColor(black)
    for i, r_ in enumerate(['(-) Desconto / Abatimento', '(-) Outras deduções', '(+) Mora / Multa',
                            '(+) Outros acréscimos', '(=) Valor cobrado']):
        caixa(xd, y - i * hh, dir_w, hh, r_, '')
    y -= alt
    ph = 14 * mm
    c.setStrokeColor(LINHA_C); c.rect(L, y - ph, R - L, ph)
    rot(L, y, 'Pagador')
    c.setFont('Helvetica-Bold', 8.5)
    c.drawString(L + 2 * mm, y - 6 * mm, f"{pag.get('nome') or ''} - {pag.get('doc_rotulo') or 'CNPJ'} {pag.get('doc') or ''}")
    c.setFont('Helvetica', 8); c.drawString(L + 2 * mm, y - 10 * mm, cabe(pag.get('endereco') or '', 'Helvetica', 8, R - L - 4 * mm))
    c.setFont('Helvetica', 6); c.setFillColor(CINZA); c.drawString(L + 1.2 * mm, y - ph + 1.2 * mm, 'Sacador / Avalista:')
    c.setFillColor(black)
    y -= ph
    c.setFont('Helvetica', 6.5); c.setFillColor(CINZA)
    c.drawRightString(R, y - 3 * mm, 'Autenticação mecânica - Ficha de Compensação'); c.setFillColor(black)
    if barras:
        I2of5(barras, barWidth=0.254 * mm, ratio=3, barHeight=13 * mm, bearers=0, quiet=0,
              checksum=0).drawOn(c, L, y - 18 * mm)
    c.showPage()
    c.save()
    return buf.getvalue()
