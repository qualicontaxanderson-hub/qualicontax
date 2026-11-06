# -*- coding: utf-8 -*-
"""PDF do contrato de prestação de serviços (ReportLab): logo e cabeçalho do escritório, título em faixa, cláusulas,
blocos de assinatura e, quando todos assinam, a página "Registro de assinaturas". Bytes determinísticos (invariant):
o mesmo contrato gera sempre o mesmo PDF, e é esse hash que cada pessoa aceita."""
import hashlib
import io
import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models.prest_contrato import QUALICONTAX

_VERDE = colors.HexColor('#0f3d2e')
_VERDE2 = colors.HexColor('#15803d')
_CINZA = colors.HexColor('#64748b')
_LINHA = colors.HexColor('#cfe5d8')
_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'images', 'logo.png')
W, H = A4
ML = MR = 2.0 * cm


def _estilos():
    base = ParagraphStyle('base', fontName='Helvetica', fontSize=9.6, leading=13.4, alignment=TA_JUSTIFY)
    return {
        'base': base,
        'titulo': ParagraphStyle('titulo', parent=base, fontName='Helvetica-Bold', fontSize=13.5, leading=17, alignment=TA_CENTER, textColor=colors.white),
        'parte_n': ParagraphStyle('parte_n', parent=base, fontName='Helvetica-Bold', fontSize=8.5, textColor=_VERDE2, spaceBefore=4),
        'clausula': ParagraphStyle('clausula', parent=base, spaceBefore=5),
        'item': ParagraphStyle('item', parent=base, leftIndent=16, spaceBefore=1),
        'centro': ParagraphStyle('centro', parent=base, alignment=TA_CENTER),
        'ass_nome': ParagraphStyle('ass_nome', parent=base, fontName='Helvetica-Bold', alignment=TA_CENTER, fontSize=9.2, leading=12),
        'ass_sub': ParagraphStyle('ass_sub', parent=base, alignment=TA_CENTER, fontSize=7.8, leading=10, textColor=_CINZA),
        'ass_ok': ParagraphStyle('ass_ok', parent=base, alignment=TA_CENTER, fontSize=7.8, leading=10, textColor=_VERDE2, fontName='Helvetica-Bold'),
        'reg_t': ParagraphStyle('reg_t', parent=base, fontName='Helvetica-Bold', fontSize=13, textColor=_VERDE, spaceAfter=4, alignment=TA_LEFT),
        'reg': ParagraphStyle('reg', parent=base, fontSize=8.6, leading=11.8, alignment=TA_LEFT),
        'mono': ParagraphStyle('mono', parent=base, fontName='Courier', fontSize=7.6, leading=10, alignment=TA_LEFT),
    }


def _cabecalho(canvas, doc):
    canvas.saveState()
    topo = H - 1.0 * cm
    # logo à esquerda (proporção original), bloco de endereço à direita
    try:
        img = ImageReader(_LOGO)
        iw, ih = img.getSize()
        lh = 1.45 * cm
        lw = lh * iw / ih
        canvas.drawImage(img, ML, topo - lh, width=lw, height=lh, mask='auto')
    except Exception:
        canvas.setFont('Helvetica-Bold', 13); canvas.setFillColor(_VERDE); canvas.drawString(ML, topo - 0.9 * cm, 'QUALICONTAX')
    canvas.setFont('Helvetica', 7.6); canvas.setFillColor(_CINZA)
    for i, linha in enumerate(QUALICONTAX['cabecalho']):
        canvas.drawRightString(W - MR, topo - 0.32 * cm - i * 9.2, linha)
    canvas.setStrokeColor(_VERDE2); canvas.setLineWidth(1.2)
    canvas.line(ML, topo - 1.65 * cm, W - MR, topo - 1.65 * cm)
    # rodapé
    canvas.setStrokeColor(_LINHA); canvas.setLineWidth(0.5)
    canvas.line(ML, 1.45 * cm, W - MR, 1.45 * cm)
    canvas.setFont('Helvetica', 7); canvas.setFillColor(_CINZA)
    canvas.drawRightString(W - MR, 1.0 * cm, 'página %d' % doc.page)
    if getattr(doc, 'rodape', None):
        canvas.drawString(ML, 1.0 * cm, doc.rodape)
    canvas.restoreState()


def _esc(s):
    return (str(s or '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _faixa_titulo(texto, st):
    t = Table([[Paragraph(_esc(texto), st['titulo'])]], colWidths=[W - ML - MR])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), _VERDE), ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
                           ('ROUNDEDCORNERS', [6, 6, 6, 6])]))
    return t


def _bloco_assinatura(a, f, st):
    """Um assinante: linha (ou "assinado eletronicamente"), nome, CPF, papel/empresa e data."""
    papel = {'CONTRATANTE': (a.get('empresa') or 'Contratante'), 'CONTRATADA': QUALICONTAX['razao'].split(' –')[0], 'TESTEMUNHA': 'Testemunha'}.get(a['papel'], '')
    linhas = []
    if f:
        linhas.append(Paragraph('assinado eletronicamente', st['ass_ok']))
    else:
        linhas.append(Spacer(1, 14)); linhas.append(Table([['']], colWidths=[6.8 * cm], style=[('LINEBELOW', (0, 0), (-1, -1), 0.7, _VERDE)]))
    linhas.append(Paragraph(_esc(a.get('nome') or ''), st['ass_nome']))
    if a.get('cpf'):
        linhas.append(Paragraph('CPF ' + _esc(a['cpf']), st['ass_sub']))
    linhas.append(Paragraph(_esc(papel), st['ass_sub']))
    if f:
        linhas.append(Paragraph(f['assinado_em'].strftime('%d/%m/%Y %H:%M'), st['ass_sub']))
    return linhas


def gerar_pdf(montado, registro=None, rodape=None):
    """``montado`` = PrestContrato.montar(dados). ``registro`` = assinaturas feitas (página final). Devolve bytes."""
    st = _estilos()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=3.1 * cm, bottomMargin=2.1 * cm,
                            title=montado['titulo'], author='Qualicontax Assessoria Contábil', invariant=1)
    doc.rodape = rodape
    el = [_faixa_titulo(montado['titulo'], st), Spacer(1, 10)]
    for i, p in enumerate(montado['partes'], 1):
        el.append(Paragraph('%d · %s' % (i, 'CONTRATANTE' if i == 1 else 'CONTRATADA'), st['parte_n']))
        el.append(Paragraph(_esc(p), st['base']))
    el.append(Spacer(1, 8)); el.append(Paragraph(_esc(montado['preambulo']), st['base'])); el.append(Spacer(1, 2))
    for titulo, paras in montado['clausulas']:
        primeiro = True
        for p in paras:
            if primeiro:
                el.append(Paragraph('<font color="#0f3d2e"><b>%s</b></font> %s' % (_esc(titulo), _esc(p)), st['clausula'])); primeiro = False
            elif p[:1].isdigit():
                import re
                cabeca = bool(re.match(r'^\d+\. [A-ZÀ-Ú ]+$', p))          # "1. CONTABILIDADE" vira título da área
                el.append(Paragraph(('<b>%s</b>' % _esc(p)) if cabeca else _esc(p), st['item']))
            elif p.startswith('PARÁGRAFO'):
                pt, _, resto = p.partition('. ')
                el.append(Paragraph('<b>%s.</b> %s' % (_esc(pt), _esc(resto)), st['clausula']))
            else:
                el.append(Paragraph(_esc(p), st['clausula']))
    el.append(Spacer(1, 10)); el.append(Paragraph(_esc(montado['fecho']), st['base']))
    el.append(Spacer(1, 14)); el.append(Paragraph(_esc(montado['local_data']), st['centro'])); el.append(Spacer(1, 20))
    ass = montado.get('assinantes') or []
    feitas = {(a['papel'], int(a['ordem'])): a for a in (registro or [])}
    blocos = [_bloco_assinatura(a, feitas.get((a['papel'], int(a.get('ordem') or 1))), st) for a in ass]
    for i in range(0, len(blocos), 2):
        par = blocos[i:i + 2]
        t = Table([[par[0], par[1] if len(par) > 1 else '']], colWidths=[(W - ML - MR) / 2] * 2)
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('ALIGN', (0, 0), (-1, -1), 'CENTER')]))
        el.append(KeepTogether([t, Spacer(1, 18)]))
    if registro:
        el.append(PageBreak())
        el.append(_faixa_titulo('REGISTRO DE ASSINATURAS ELETRÔNICAS', st)); el.append(Spacer(1, 10))
        el.append(Paragraph(_esc('Documento: %s. Assinado pelo sistema da Qualicontax (app.qualicontax.com.br) com confirmação de identidade por código '
                                 'enviado ao e-mail de cada signatário. A impressão digital SHA-256 abaixo é a do documento que cada pessoa viu e aceitou.' % montado['titulo']), st['reg']))
        el.append(Spacer(1, 8))
        linhas = [['Signatário', 'Papel', 'CPF', 'E-mail', 'Assinado em', 'IP']]
        for a in registro:
            linhas.append([Paragraph(_esc(a.get('assinado_nome') or a.get('nome')), st['reg']), a['papel'].capitalize(), a.get('assinado_cpf') or a.get('cpf') or '',
                           Paragraph(_esc(a.get('email') or ''), st['reg']), a['assinado_em'].strftime('%d/%m/%Y %H:%M:%S'), a.get('assinado_ip') or ''])
        t = Table(linhas, colWidths=[4.3 * cm, 2.2 * cm, 2.8 * cm, 4.3 * cm, 2.6 * cm, 1.8 * cm], repeatRows=1)
        t.setStyle(TableStyle([('FONT', (0, 0), (-1, -1), 'Helvetica', 7.6), ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 7.6),
                               ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8f5ed')), ('TEXTCOLOR', (0, 0), (-1, 0), _VERDE),
                               ('LINEBELOW', (0, 0), (-1, -1), 0.3, _LINHA), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
        el.append(t); el.append(Spacer(1, 10))
        el.append(Paragraph('<b>Impressão digital (SHA-256) do documento aceito</b>', st['reg']))
        el.append(Paragraph(_esc(registro[0].get('assinado_hash') or ''), st['mono']))
        el.append(Spacer(1, 8))
        quando = max(a['assinado_em'] for a in registro)
        el.append(Paragraph(_esc('Registro consolidado em %s, quando o último signatário assinou. Fundamento: art. 10, § 2º, da MP nº 2.200-2/2001 e art. 4º da '
                                 'Lei nº 14.063/2020 (assinatura eletrônica).' % quando.strftime('%d/%m/%Y %H:%M:%S')), st['reg']))
    doc.build(el, onFirstPage=_cabecalho, onLaterPages=_cabecalho)
    return buf.getvalue()


def sha256(b):
    return hashlib.sha256(b).hexdigest()
