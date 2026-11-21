# -*- coding: utf-8 -*-
"""PDF do orçamento (21/11/2026): mesmo cabeçalho do contrato de prestação (logo + endereço do escritório), faixa com o
número e a validade, dados do cliente, tabela dos serviços (o que entra na nota e o que vai só no boleto), total, condições
e o rodapé com quem montou. ReportLab, como o contrato."""
import io
from datetime import date
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models.prest_contrato import QUALICONTAX
from utils.prest_contrato_pdf import _cabecalho, _esc

W, H = A4
ML = MR = 1.8 * cm
VERDE = colors.HexColor('#0f3d2e')
VERDE2 = colors.HexColor('#16a34a')
CINZA = colors.HexColor('#64748b')
LINHA = colors.HexColor('#e2e8f0')
FUNDO = colors.HexColor('#f6faf7')
AMBAR = colors.HexColor('#b45309')


def _brl(v):
    return 'R$ ' + '{:,.2f}'.format(Decimal(str(v or 0))).replace(',', 'X').replace('.', ',').replace('X', '.')


def _doc(d):
    d = ''.join(ch for ch in str(d or '') if ch.isdigit())
    if len(d) == 14:
        return '%s.%s.%s/%s-%s' % (d[:2], d[2:5], d[5:8], d[8:12], d[12:])
    if len(d) == 11:
        return '%s.%s.%s-%s' % (d[:3], d[3:6], d[6:9], d[9:])
    return d


def _st():
    base = ParagraphStyle('b', fontName='Helvetica', fontSize=9.2, leading=12.5, textColor=colors.HexColor('#0f172a'))
    return {
        'b': base,
        'tit': ParagraphStyle('tit', parent=base, fontName='Helvetica-Bold', fontSize=16, leading=19, textColor=colors.white),
        'tit2': ParagraphStyle('tit2', parent=base, fontSize=9, textColor=colors.white),
        'sec': ParagraphStyle('sec', parent=base, fontName='Helvetica-Bold', fontSize=8.2, textColor=VERDE, spaceBefore=8, spaceAfter=3),
        'nome': ParagraphStyle('nome', parent=base, fontName='Helvetica-Bold', fontSize=12, leading=15),
        'mi': ParagraphStyle('mi', parent=base, fontSize=8.2, leading=11, textColor=CINZA),
        'th': ParagraphStyle('th', parent=base, fontName='Helvetica-Bold', fontSize=7.6, textColor=CINZA),
        'thr': ParagraphStyle('thr', parent=base, fontName='Helvetica-Bold', fontSize=7.6, textColor=CINZA, alignment=TA_RIGHT),
        'td': ParagraphStyle('td', parent=base, fontSize=9.2),
        'tdr': ParagraphStyle('tdr', parent=base, fontSize=9.2, alignment=TA_RIGHT),
        'tot': ParagraphStyle('tot', parent=base, fontName='Helvetica-Bold', fontSize=12, alignment=TA_RIGHT, textColor=VERDE),
        'totl': ParagraphStyle('totl', parent=base, fontName='Helvetica-Bold', fontSize=9.5, alignment=TA_RIGHT),
        'obs': ParagraphStyle('obs', parent=base, fontSize=9, leading=13),
        'rod': ParagraphStyle('rod', parent=base, fontSize=8, leading=11, textColor=CINZA),
    }


def gerar_pdf(o, itens):
    """``o`` = Orcamento.get(id) (com cliente_*), ``itens`` = Orcamento.itens(id). Devolve bytes."""
    st = _st()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=2.5 * cm, bottomMargin=1.9 * cm,
                            title='Orçamento %s' % o['numero'], author=QUALICONTAX['razao'])
    doc.rodape = 'Orçamento %s · emitido em %s' % (o['numero'], date.today().strftime('%d/%m/%Y'))
    el = []
    # faixa do título
    validade = o['validade'].strftime('%d/%m/%Y') if o.get('validade') else None
    faixa = Table([[Paragraph('ORÇAMENTO <font size="10">%s</font>' % _esc(o['numero']), st['tit']),
                    Paragraph(('válido até <b>%s</b>' % validade) if validade else 'sem prazo de validade', ParagraphStyle('r', parent=st['tit2'], alignment=TA_RIGHT))]],
                  colWidths=[(W - ML - MR) * .62, (W - ML - MR) * .38])
    faixa.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), VERDE), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12), ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9)]))
    el += [faixa, Spacer(1, 10)]
    # cliente
    el.append(Paragraph('PARA', st['sec']))
    linhas = [Paragraph(_esc(o['cliente_nome']), st['nome'])]
    mi = []
    if o.get('cliente_doc'):
        mi.append(('CNPJ ' if len(''.join(ch for ch in str(o['cliente_doc']) if ch.isdigit())) == 14 else 'CPF ') + _doc(o['cliente_doc']))
    if o.get('cliente_num'):
        mi.append('cliente nº %s' % o['cliente_num'])
    if o.get('cliente_email'):
        mi.append(str(o['cliente_email']))
    if mi:
        linhas.append(Paragraph(_esc(' · '.join(mi)), st['mi']))
    if o.get('titulo'):
        linhas.append(Paragraph('<b>Assunto:</b> %s' % _esc(o['titulo']), st['b']))
    el += linhas + [Spacer(1, 4)]
    # itens
    el.append(Paragraph('SERVIÇOS', st['sec']))
    dados = [[Paragraph('Serviço', st['th']), Paragraph('Qtd', st['thr']), Paragraph('Valor', st['thr']), Paragraph('Total', st['thr'])]]
    so_boleto = False
    for i in itens:
        nome = _esc(i['descricao'])
        if not i['emite_nota']:
            nome += ' <font color="#b45309" size="7">(taxa repassada)</font>'
            so_boleto = True
        q = Decimal(str(i['quantidade']))
        dados.append([Paragraph(nome, st['td']), Paragraph(('%g' % q) if q == q.to_integral() else str(q).replace('.', ','), st['tdr']),
                      Paragraph(_brl(i['valor_unitario']), st['tdr']), Paragraph(_brl(i['valor_total']), st['tdr'])])
    total = sum((Decimal(str(i['valor_total'])) for i in itens), Decimal('0'))
    dados.append(['', '', Paragraph('TOTAL', st['totl']), Paragraph(_brl(total), st['tot'])])
    lw = W - ML - MR
    t = Table(dados, colWidths=[lw * .58, lw * .10, lw * .16, lw * .16], repeatRows=1)
    t.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, 0), 0.8, LINHA), ('LINEBELOW', (0, 1), (-1, -3), 0.4, LINHA),
                           ('ROWBACKGROUNDS', (0, 1), (-1, -2), [colors.white, FUNDO]),
                           ('LINEABOVE', (2, -1), (-1, -1), 1.1, VERDE2), ('TOPPADDING', (0, -1), (-1, -1), 7),
                           ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                           ('TOPPADDING', (0, 0), (-1, -2), 5), ('BOTTOMPADDING', (0, 0), (-1, -2), 5)]))
    el += [t]
    if so_boleto:
        el.append(Paragraph('Taxa repassada: valor pago a terceiros (Junta Comercial, cartório, Correios...) que repassamos sem acréscimo; é cobrada no boleto e não compõe a nota fiscal de serviço.', st['rod']))
    # condições
    cond = []
    como = {'BOLETO_NOTA': 'boleto bancário com nota fiscal de serviço', 'BOLETO': 'boleto bancário', 'MENSAL': 'incluído na mensalidade dos honorários'}.get(o.get('como'))
    if como:
        cond.append('Pagamento: %s%s.' % (como, (' em %d parcelas' % o['parcelas']) if o.get('parcelas') and o['parcelas'] > 1 else ''))
    if validade:
        cond.append('Proposta válida até %s.' % validade)
    el.append(Paragraph('CONDIÇÕES', st['sec']))
    for c in cond:
        el.append(Paragraph('• ' + _esc(c), st['obs']))
    if o.get('observacao'):
        for par in str(o['observacao']).split('\n'):
            if par.strip():
                el.append(Paragraph('• ' + _esc(par.strip()), st['obs']))
    el += [Spacer(1, 14), Paragraph('Atenciosamente,', st['b']), Spacer(1, 16),
           Paragraph('<b>%s</b>' % _esc(QUALICONTAX['razao']), st['b']), Paragraph('CNPJ %s · CRC %s' % (QUALICONTAX['cnpj'], QUALICONTAX['crc']), st['mi'])]
    if o.get('criado_por_nome'):
        el.append(Paragraph('Orçamento montado por %s' % _esc(o['criado_por_nome']), st['mi']))
    doc.build(el, onFirstPage=_cabecalho, onLaterPages=_cabecalho)
    return buf.getvalue()
