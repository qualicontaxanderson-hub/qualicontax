# -*- coding: utf-8 -*-
"""Pedido de pagamento de comissão em PDF, em IMAGEM e em TEXTO de WhatsApp (18/11/2026).

Anderson: "no fechamento teremos imagem, pdf e até texto de whatsapp a ser enviado para o financeiro".
A pessoa fecha as comissões, abre o pedido e manda para Anderson ou Alessandra do jeito que preferir:
o PDF para guardar, a imagem para colar na conversa, o texto curto com o link da página (onde está o
QR e o Pix copia e cola). Os três saem do mesmo ``ctx`` montado pela rota ``comissao_pedido``:

    f        o fechamento (fin_comissao_fechamentos + usuario_nome)
    itens    os lançamentos do pedido (FinComissaoFechamento.itens)
    pix      {'pix_tipo', 'pix_chave'} ou {}
    falta    quanto falta pagar (float)
    codigo   Pix copia e cola (ou None)
    link     endereço da página do pedido
    logo_path
"""
import io
import os
import re
from urllib.parse import quote

PIX_ROT = {'cpf': 'CPF', 'cnpj': 'CNPJ', 'celular': 'Celular', 'email': 'E-mail', 'aleatoria': 'Chave aleatória'}


def brl(v):
    s = '%.2f' % abs(float(v or 0))
    inteiro, dec = s.split('.')
    inteiro = re.sub(r'(?<=\d)(?=(\d{3})+$)', '.', inteiro)
    return ('-' if float(v or 0) < 0 else '') + 'R$ ' + inteiro + ',' + dec


def _primeiro_nome(nome):
    return (nome or '').split()[0] if nome else ''


def _linha_item(l):
    """(cliente, detalhe, cobrado, comissão, marca) de um item do pedido."""
    nome = l.get('nome_razao_social') or ''
    num = str(l.get('numero_cliente') or '')
    desc = l.get('descricao') or l.get('servico_nome') or ''
    if l.get('destino') == 'MENSAL' and l.get('competencia') and 'venc.' not in desc:
        desc += ' · mensalidade de ' + l['competencia'].strftime('%m/%Y')
    com = float(l.get('comissao_valor') or 0)
    if l.get('desconto_de'):
        marca = 'desconto'
    elif l.get('comissao_adiantada') and l.get('titulo_status') != 'liquidado':
        marca = 'adiantada'
    else:
        marca = 'paga pelo cliente'
    return (num, nome, desc, float(l.get('valor_total') or 0), com, marca)


def resumo(itens):
    """Quantas pagas pelo cliente, quantas adiantadas, quantos descontos."""
    r = {'pagas': 0, 'adiantadas': 0, 'descontos': 0}
    for l in itens:
        m = _linha_item(l)[5]
        r['pagas' if m == 'paga pelo cliente' else 'adiantadas' if m == 'adiantada' else 'descontos'] += 1
    return r


def frase_resumo(itens):
    r = resumo(itens)
    partes = []
    if r['pagas']:
        partes.append('%d comiss%s de cobranças já pagas pelo cliente' % (r['pagas'], 'ão' if r['pagas'] == 1 else 'ões'))
    if r['adiantadas']:
        partes.append('%d adiantada%s (o cliente ainda não pagou)' % (r['adiantadas'], '' if r['adiantadas'] == 1 else 's'))
    if r['descontos']:
        partes.append('%d desconto%s de cobrança não recebida' % (r['descontos'], '' if r['descontos'] == 1 else 's'))
    return ' · '.join(partes) or 'sem itens'


# ---------------------------------------------------------------------------
# Texto do WhatsApp
# ---------------------------------------------------------------------------
def texto_whatsapp(ctx):
    f, itens, pix = ctx['f'], ctx['itens'], ctx.get('pix') or {}
    r = resumo(itens)
    quando = f['criado_em'].strftime('%d/%m/%Y') if f.get('criado_em') else ''
    linhas = ['*Pedido de comissão nº %d · %s*' % (f['id'], f['usuario_nome'])]
    if r['pagas']:
        linhas.append('%d comiss%s de cobranças já pagas pelo cliente' % (r['pagas'], 'ão' if r['pagas'] == 1 else 'ões'))
    if r['adiantadas']:
        linhas.append('%d adiantada%s: o cliente ainda não pagou (fica em atraso na minha tela)' % (r['adiantadas'], '' if r['adiantadas'] == 1 else 's'))
    if r['descontos']:
        linhas.append('%d desconto%s de cobrança que não recebemos' % (r['descontos'], '' if r['descontos'] == 1 else 's'))
    linhas.append('Total: *%s*' % brl(f['total']))
    if float(f.get('valor_pago') or 0) > 0 and ctx.get('falta', 0) > 0:
        linhas.append('Já recebi %s · falta *%s*' % (brl(f['valor_pago']), brl(ctx['falta'])))
    if pix.get('pix_chave'):
        linhas.append('Pix (%s): %s' % (PIX_ROT.get(pix.get('pix_tipo'), 'chave'), pix['pix_chave']))
        if pix.get('titular'):
            linhas.append('Em nome de: %s (o Pix vai para o banco dono da chave)' % pix['titular'])
    else:
        linhas.append('Ainda sem chave Pix no meu cadastro.')
    if quando:
        linhas.append('Pedido em %s' % quando)
    if ctx.get('link'):
        linhas.append('QR, Pix copia e cola e a lista completa: %s' % ctx['link'])
    return '\n'.join(linhas)


def link_whatsapp(texto):
    """wa.me sem número: abre o WhatsApp com o texto pronto e a pessoa escolhe para quem manda."""
    return 'https://wa.me/?text=' + quote(texto, safe='')


# ---------------------------------------------------------------------------
# QR em PNG (bytes) para o PDF e a imagem
# ---------------------------------------------------------------------------
def _qr_png(texto, box=6):
    import qrcode
    img = qrcode.make(texto, box_size=box, border=1)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def gerar_pdf(ctx):
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, SimpleDocTemplate, Spacer, Table, TableStyle
    from utils.relatorio_clientes import CINZA, LINHA, ZEBRA, _p

    f, itens, pix = ctx['f'], ctx['itens'], ctx.get('pix') or {}
    ROXO = colors.HexColor('#5B21B6')
    ROXO_CL = colors.HexColor('#EDE9FE')
    VERDE = colors.HexColor('#15803D')
    VERM = colors.HexColor('#B91C1C')
    AMBAR = colors.HexColor('#92400E')
    TEXTO = colors.HexColor('#0F172A')
    pago = f['status'] == 'PAGO'

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=10 * mm, bottomMargin=12 * mm, leftMargin=14 * mm, rightMargin=14 * mm,
                            title='Pedido de comissão nº %d' % f['id'], author='Qualicontax')
    L = doc.width
    el = []

    # Cabeçalho: logo, título, valor
    logo = ''
    if ctx.get('logo_path') and os.path.exists(ctx['logo_path']):
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(ctx['logo_path']).getSize()
            logo = Image(ctx['logo_path'], width=14 * mm * iw / ih, height=14 * mm)
        except Exception:
            logo = ''
    quando = f['criado_em'].strftime('%d/%m/%Y') if f.get('criado_em') else ''
    situacao = ('PAGO' if pago else 'PAGO EM PARTE · FALTA ' + brl(ctx.get('falta', 0)) if f['status'] == 'PAGO_PARCIAL'
                else 'CANCELADO' if f['status'] == 'CANCELADO' else 'AGUARDANDO PAGAMENTO')
    meio = [_p('PEDIDO DE PAGAMENTO DE COMISSÃO Nº %d' % f['id'], 7.5, colors.white, bold=True),
            _p(escape(f['usuario_nome']), 17, colors.white, bold=True, leading=20),
            _p(escape(frase_resumo(itens)) + (' · pedido em ' + quando if quando else ''), 8.5, colors.white, leading=11)]
    dir_ = [_p(brl(f['total']), 20, colors.white, bold=True, align=TA_RIGHT, leading=23),
            _p(situacao, 7.5, colors.white, bold=True, align=TA_RIGHT)]
    cab = Table([[logo, meio, dir_]], colWidths=[30 * mm, L - 30 * mm - 58 * mm, 58 * mm])
    cab.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), VERDE if pago else ROXO), ('ROUNDEDCORNERS', [8, 8, 8, 8]),
                             ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 9),
                             ('BOTTOMPADDING', (0, 0), (-1, -1), 9), ('LEFTPADDING', (0, 0), (0, 0), 8)]))
    el += [cab, Spacer(1, 9)]

    # Itens
    cab_it = [_p('<b>Nº</b>', 7.5, CINZA), _p('<b>EMPRESA</b>', 7.5, CINZA), _p('<b>O QUE FOI COBRADO</b>', 7.5, CINZA),
              _p('<b>COBRADO</b>', 7.5, CINZA, align=TA_RIGHT), _p('<b>COMISSÃO</b>', 7.5, CINZA, align=TA_RIGHT), _p('<b>SITUAÇÃO</b>', 7.5, CINZA)]
    linhas = [cab_it]
    estilos = [('LINEBELOW', (0, 0), (-1, 0), 0.8, ROXO), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
               ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
               ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, ZEBRA]), ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINHA)]
    for l in itens:
        num, nome, desc, cobrado, com, marca = _linha_item(l)
        cor = VERM if marca == 'desconto' else AMBAR if marca == 'adiantada' else VERDE
        rot = {'desconto': 'desconto · não recebida', 'adiantada': 'adiantada · cliente ainda não pagou'}.get(marca, 'paga pelo cliente')
        linhas.append([_p(num or '—', 8, CINZA), _p(escape(nome), 8.5, TEXTO, bold=True), _p(escape(desc), 7.8, CINZA, leading=10),
                       _p(brl(cobrado), 8.5, TEXTO, align=TA_RIGHT), _p('<b>%s</b>' % brl(com), 9, cor, align=TA_RIGHT),
                       _p(rot, 7.5, cor, bold=True)])
    linhas.append(['', '', '', _p('<b>TOTAL DO PEDIDO</b>', 9, TEXTO, align=TA_RIGHT), _p('<b>%s</b>' % brl(f['total']), 11, ROXO, align=TA_RIGHT), ''])
    estilos += [('LINEABOVE', (0, -1), (-1, -1), 1.2, ROXO), ('TOPPADDING', (0, -1), (-1, -1), 7), ('BACKGROUND', (0, -1), (-1, -1), colors.white)]
    tb = Table(linhas, colWidths=[11 * mm, 52 * mm, L - 11 * mm - 52 * mm - 24 * mm - 26 * mm - 38 * mm, 24 * mm, 26 * mm, 38 * mm], repeatRows=1)
    tb.setStyle(TableStyle(estilos))
    el += [tb, Spacer(1, 10)]

    # Pix: QR + chave + copia e cola
    if pago:
        aviso = Table([[_p('<b>Pedido quitado.</b> Nada a pagar.', 9, VERDE)]], colWidths=[L])
        aviso.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F0FDF4')), ('ROUNDEDCORNERS', [6, 6, 6, 6]),
                                   ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8), ('LEFTPADDING', (0, 0), (-1, -1), 10)]))
        el.append(aviso)
    elif ctx.get('codigo'):
        qr = Image(io.BytesIO(_qr_png(ctx['codigo'], box=5)), width=42 * mm, height=42 * mm)
        txt = [_p('PAGAR POR PIX', 7.5, ROXO, bold=True),
               _p('%s: <b>%s</b>' % (PIX_ROT.get(pix.get('pix_tipo'), 'Chave'), escape(pix.get('pix_chave') or '')), 10, TEXTO, leading=14),
               _p('Valor: <b>%s</b>' % brl(ctx.get('falta', 0)), 10, TEXTO, leading=14),
               _p('Em nome de <b>%s</b> · o Pix vai para o banco dono da chave' % escape(pix.get('titular') or f['usuario_nome']), 8.5, AMBAR if pix.get('outro') else CINZA, leading=12),
               Spacer(1, 4), _p('Pix copia e cola:', 7.5, CINZA),
               _p(escape(ctx['codigo']), 6.6, colors.HexColor('#334155'), leading=8.5),
               Spacer(1, 4), _p('Depois de pagar não precisa fazer mais nada: quando o extrato puxar a saída, o pedido fica quitado sozinho.', 7.5, CINZA, leading=10)]
        pixt = Table([[qr, txt]], colWidths=[48 * mm, L - 48 * mm])
        pixt.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FAF8FF')), ('BOX', (0, 0), (-1, -1), 0.6, ROXO_CL),
                                  ('ROUNDEDCORNERS', [8, 8, 8, 8]), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                                  ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))
        el.append(pixt)
    else:
        el.append(_p('%s ainda não cadastrou a chave Pix (Meu cadastro).' % escape(_primeiro_nome(f['usuario_nome'])), 9, AMBAR))

    # Pagamentos já feitos
    if ctx.get('pags'):
        el += [Spacer(1, 8), _p('PAGAMENTOS', 7.5, ROXO, bold=True), Spacer(1, 3)]
        for p in ctx['pags']:
            como = ('quitado pelo extrato %s: "%s"' % (p.get('extrato_banco') or '', p.get('extrato_desc') or '')) if p.get('extrato_id') else ('registrado por ' + (p.get('por_nome') or 'quem aprova'))
            el.append(_p('<b>%s</b> em %s · %s' % (brl(p['valor']), p['data'].strftime('%d/%m/%Y'), escape(como)), 8.5, TEXTO, leading=12))

    if ctx.get('link'):
        el += [Spacer(1, 10), _p('Página do pedido: %s' % escape(ctx['link']), 7.5, CINZA, align=TA_CENTER)]
    doc.build(el)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Imagem (PNG) — para colar na conversa
# ---------------------------------------------------------------------------
def _fonte(bold=False, tam=28):
    from PIL import ImageFont
    try:
        import reportlab
        p = os.path.join(os.path.dirname(reportlab.__file__), 'fonts', 'VeraBd.ttf' if bold else 'Vera.ttf')
        return ImageFont.truetype(p, tam)
    except Exception:
        return ImageFont.load_default()


def _corta(draw, texto, fonte, larg):
    if draw.textlength(texto, font=fonte) <= larg:
        return texto
    while texto and draw.textlength(texto + '…', font=fonte) > larg:
        texto = texto[:-1]
    return texto.rstrip() + '…'


def gerar_png(ctx):
    from PIL import Image, ImageDraw

    f, itens, pix = ctx['f'], ctx['itens'], ctx.get('pix') or {}
    pago = f['status'] == 'PAGO'
    W = 1080
    M = 48
    ROXO, ROXO_ESC, VERDE, VERM, AMBAR = (124, 58, 237), (76, 29, 149), (21, 128, 61), (185, 28, 28), (146, 64, 14)
    TEXTO, CINZA, LINHA, FUNDO = (15, 23, 42), (100, 116, 139), (226, 232, 240), (248, 250, 252)
    f_tit, f_nome, f_val = _fonte(True, 22), _fonte(True, 40), _fonte(True, 54)
    f_b, f_n, f_s, f_xs = _fonte(True, 26), _fonte(False, 26), _fonte(False, 22), _fonte(False, 19)

    alt_cab = 230
    alt_item = 88
    tem_pix = (not pago) and bool(ctx.get('codigo'))
    alt_pix = 400 if tem_pix else 110
    H = alt_cab + 70 + alt_item * len(itens) + 90 + alt_pix + 70
    img = Image.new('RGB', (W, H), 'white')
    d = ImageDraw.Draw(img)

    # Cabeçalho
    d.rounded_rectangle((0, 0, W, alt_cab), radius=0, fill=VERDE if pago else ROXO_ESC)
    d.rectangle((0, 0, W, 8), fill=VERDE if pago else ROXO)
    x = M
    if ctx.get('logo_path') and os.path.exists(ctx['logo_path']):
        try:
            lg = Image.open(ctx['logo_path']).convert('RGBA')
            lh = 64
            lg = lg.resize((int(lg.width * lh / lg.height), lh))
            d.rounded_rectangle((M, 40, M + lg.width + 28, 40 + lh + 24), radius=16, fill='white')
            img.paste(lg, (M + 14, 52), lg)
            x = M + lg.width + 28 + 26
        except Exception:
            pass
    d.text((x, 44), 'PEDIDO DE PAGAMENTO DE COMISSÃO Nº %d' % f['id'], font=f_tit, fill=(221, 214, 254))
    d.text((x, 80), _corta(d, f['usuario_nome'], f_nome, W - x - M), font=f_nome, fill='white')
    quando = f['criado_em'].strftime('%d/%m/%Y') if f.get('criado_em') else ''
    d.text((x, 134), _corta(d, frase_resumo(itens) + (' · pedido em ' + quando if quando else ''), f_xs, W - x - M), font=f_xs, fill=(237, 233, 254))
    d.text((M, 170), brl(f['total']), font=f_val, fill='white')
    sit = ('pago' if pago else 'pago em parte · falta ' + brl(ctx.get('falta', 0)) if f['status'] == 'PAGO_PARCIAL'
           else 'cancelado' if f['status'] == 'CANCELADO' else 'aguardando pagamento')
    tw = d.textlength(sit, font=f_s)
    d.rounded_rectangle((W - M - tw - 32, 180, W - M, 222), radius=21, fill=(22, 101, 52) if pago else (109, 40, 217))
    d.text((W - M - tw - 16, 188), sit, font=f_s, fill='white')

    # Itens
    y = alt_cab + 34
    d.text((M, y), 'O QUE ENTROU NESTE PEDIDO', font=_fonte(True, 20), fill=ROXO_ESC)
    y += 40
    for i, l in enumerate(itens):
        num, nome, desc, cobrado, com, marca = _linha_item(l)
        cor = VERM if marca == 'desconto' else AMBAR if marca == 'adiantada' else VERDE
        rot = {'desconto': 'desconto · não recebida', 'adiantada': 'adiantada · cliente ainda não pagou'}.get(marca, 'paga pelo cliente')
        if i % 2:
            d.rectangle((M - 12, y - 6, W - M + 12, y + alt_item - 10), fill=FUNDO)
        d.rounded_rectangle((M, y + 6, M + 72, y + 40), radius=8, fill=LINHA)
        nw = d.textlength(num or '—', font=f_xs)
        d.text((M + 36 - nw / 2, y + 12), num or '—', font=f_xs, fill=CINZA)
        vw = d.textlength(brl(com), font=f_b)
        rw = d.textlength(rot, font=f_xs)
        larg = W - M - (M + 88) - max(vw, rw) - 24          # o texto da esquerda para antes do valor/rótulo da direita
        d.text((M + 88, y + 2), _corta(d, nome, f_b, larg), font=f_b, fill=TEXTO)
        d.text((M + 88, y + 38), _corta(d, desc + ' · cobrado ' + brl(cobrado), f_xs, larg), font=f_xs, fill=CINZA)
        d.text((W - M - vw, y + 2), brl(com), font=f_b, fill=cor)
        d.text((W - M - rw, y + 40), rot, font=f_xs, fill=cor)
        y += alt_item
    d.line((M, y + 4, W - M, y + 4), fill=ROXO, width=3)
    d.text((M, y + 22), 'Total do pedido', font=f_b, fill=TEXTO)
    tw = d.textlength(brl(f['total']), font=f_val)
    d.text((W - M - tw, y + 10), brl(f['total']), font=f_val, fill=ROXO)
    y += 90

    # Pix
    if tem_pix:
        d.rounded_rectangle((M, y, W - M, y + alt_pix - 20), radius=22, fill=(250, 248, 255), outline=(233, 227, 251), width=2)
        qr = Image.open(io.BytesIO(_qr_png(ctx['codigo'], box=8))).convert('RGB').resize((300, 300))
        img.paste(qr, (M + 30, y + 40))
        tx = M + 30 + 300 + 36
        d.text((tx, y + 40), 'PAGAR POR PIX', font=_fonte(True, 20), fill=ROXO_ESC)
        d.text((tx, y + 80), PIX_ROT.get(pix.get('pix_tipo'), 'Chave') + ':', font=f_s, fill=CINZA)
        d.text((tx, y + 110), _corta(d, pix.get('pix_chave') or '', f_b, W - M - tx - 20), font=f_b, fill=TEXTO)
        d.text((tx, y + 162), 'Valor:', font=f_s, fill=CINZA)
        d.text((tx, y + 192), brl(ctx.get('falta', 0)), font=f_b, fill=TEXTO)
        d.text((tx, y + 236), _corta(d, 'Em nome de ' + (pix.get('titular') or f['usuario_nome']), f_xs, W - M - tx - 20), font=f_xs, fill=AMBAR if pix.get('outro') else CINZA)
        d.text((tx, y + 264), 'O Pix copia e cola está na página do pedido.', font=f_xs, fill=CINZA)
    elif pago:
        d.rounded_rectangle((M, y, W - M, y + 80), radius=18, fill=(240, 253, 244), outline=(187, 247, 208), width=2)
        d.text((M + 24, y + 24), 'Pedido quitado. Nada a pagar.', font=f_b, fill=VERDE)
    else:
        d.rounded_rectangle((M, y, W - M, y + 80), radius=18, fill=(255, 251, 235), outline=(253, 230, 138), width=2)
        d.text((M + 24, y + 26), _corta(d, '%s ainda não cadastrou a chave Pix em Meu cadastro.' % _primeiro_nome(f['usuario_nome']), f_s, W - 2 * M - 48), font=f_s, fill=AMBAR)
    y += alt_pix

    d.text((M, H - 46), 'Qualicontax · Financeiro · Lançamentos · Comissões', font=f_xs, fill=CINZA)
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()
