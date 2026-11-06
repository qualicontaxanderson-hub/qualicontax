# -*- coding: utf-8 -*-
"""PDF do contrato de prestação de serviços (ReportLab), com o cabeçalho que o escritório usa e, quando todos
assinam, a página "Registro de assinaturas" (quem, CPF, e-mail, data/hora, IP e a impressão digital do documento)."""
import hashlib
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, KeepTogether

from models.prest_contrato import QUALICONTAX

_VERDE = colors.HexColor('#0f3d2e')
_CINZA = colors.HexColor('#64748b')


def _estilos():
    base = ParagraphStyle('base', fontName='Helvetica', fontSize=9.5, leading=13, alignment=TA_JUSTIFY)
    return {
        'base': base,
        'titulo': ParagraphStyle('titulo', parent=base, fontName='Helvetica-Bold', fontSize=13, leading=17, alignment=TA_CENTER, spaceAfter=10),
        'parte_n': ParagraphStyle('parte_n', parent=base, fontName='Helvetica-Bold', fontSize=9, textColor=_VERDE),
        'clausula': ParagraphStyle('clausula', parent=base, spaceBefore=5),
        'item': ParagraphStyle('item', parent=base, leftIndent=14),
        'centro': ParagraphStyle('centro', parent=base, alignment=TA_CENTER),
        'ass_nome': ParagraphStyle('ass_nome', parent=base, fontName='Helvetica-Bold', alignment=TA_CENTER, fontSize=9),
        'ass_sub': ParagraphStyle('ass_sub', parent=base, alignment=TA_CENTER, fontSize=8, textColor=_CINZA),
        'reg_t': ParagraphStyle('reg_t', parent=base, fontName='Helvetica-Bold', fontSize=12, textColor=_VERDE, spaceAfter=6),
        'reg': ParagraphStyle('reg', parent=base, fontSize=8.5, leading=11.5),
        'mono': ParagraphStyle('mono', parent=base, fontName='Courier', fontSize=7.5, leading=10),
    }


def _cabecalho(canvas, doc):
    canvas.saveState()
    w, h = A4
    y = h - 1.3 * cm
    canvas.setFont('Helvetica-Bold', 11); canvas.setFillColor(_VERDE)
    canvas.drawString(2 * cm, y, 'QUALICONTAX')
    canvas.setFont('Helvetica', 7.5); canvas.setFillColor(_CINZA)
    for i, linha in enumerate(QUALICONTAX['cabecalho']):
        canvas.drawRightString(w - 2 * cm, y - i * 9, linha)
    canvas.setStrokeColor(colors.HexColor('#bfe3cd')); canvas.setLineWidth(0.6)
    canvas.line(2 * cm, y - 36, w - 2 * cm, y - 36)
    canvas.setFont('Helvetica', 7); canvas.setFillColor(_CINZA)
    canvas.drawRightString(w - 2 * cm, 1.1 * cm, 'página %d' % doc.page)
    if getattr(doc, 'rodape', None):
        canvas.drawString(2 * cm, 1.1 * cm, doc.rodape)
    canvas.restoreState()


def _esc(s):
    return (str(s or '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def gerar_pdf(montado, registro=None, rodape=None):
    """``montado`` = PrestContrato.montar(dados). ``registro`` = lista de assinaturas feitas (para a página final).
    Devolve bytes."""
    st = _estilos()
    buf = io.BytesIO()
    # invariant=1: sem data de criação e ids aleatórios no PDF, para que o mesmo contrato gere SEMPRE os mesmos bytes
    # (o hash que cada pessoa aceita tem que ser o do PDF enviado)
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=3.2 * cm, bottomMargin=2 * cm,
                            title=montado['titulo'], author='Qualicontax', invariant=1)
    doc.rodape = rodape
    el = [Paragraph(_esc(montado['titulo']), st['titulo'])]
    for i, p in enumerate(montado['partes'], 1):
        el.append(Paragraph('%d · %s' % (i, 'CONTRATANTE' if i == 1 else 'CONTRATADA'), st['parte_n']))
        el.append(Paragraph(_esc(p), st['base']))
        el.append(Spacer(1, 5))
    el.append(Spacer(1, 4)); el.append(Paragraph(_esc(montado['preambulo']), st['base'])); el.append(Spacer(1, 4))
    for titulo, paras in montado['clausulas']:
        primeiro = True
        for p in paras:
            if primeiro:
                el.append(Paragraph('<b>%s</b> %s' % (_esc(titulo), _esc(p)), st['clausula'])); primeiro = False
            elif p[:2].isdigit() or p[:1].isdigit():
                el.append(Paragraph(_esc(p), st['item']))
            elif p.startswith('PARÁGRAFO'):
                pt, _, resto = p.partition('. ')
                el.append(Paragraph('<b>%s.</b> %s' % (_esc(pt), _esc(resto)), st['clausula']))
            else:
                el.append(Paragraph(_esc(p), st['clausula']))
    el.append(Spacer(1, 10)); el.append(Paragraph(_esc(montado['fecho']), st['base']))
    el.append(Spacer(1, 12)); el.append(Paragraph(_esc(montado['local_data']), st['centro'])); el.append(Spacer(1, 22))
    # ---- assinaturas ----
    ass = montado.get('assinantes') or []
    feitas = {(a['papel'], int(a['ordem'])): a for a in (registro or [])}
    blocos = []
    for a in ass:
        f = feitas.get((a['papel'], int(a.get('ordem') or 1)))
        linha = '______________________________' if not f else 'assinado eletronicamente'
        sub = {'CONTRATANTE': (a.get('empresa') or ''), 'CONTRATADA': QUALICONTAX['razao'].split(' –')[0], 'TESTEMUNHA': 'Testemunha'}.get(a['papel'], '')
        if f:
            sub = (sub + ' · ' if sub else '') + f['assinado_em'].strftime('%d/%m/%Y %H:%M')
        blocos.append([Paragraph(linha, st['centro']), Paragraph(_esc(a.get('nome') or ''), st['ass_nome']),
                       Paragraph(_esc((a.get('cpf') and 'CPF ' + a['cpf']) or ''), st['ass_sub']), Paragraph(_esc(sub), st['ass_sub'])])
    for i in range(0, len(blocos), 2):
        par = blocos[i:i + 2]
        t = Table([[par[0], par[1] if len(par) > 1 else '']], colWidths=[8.5 * cm, 8.5 * cm])
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        el.append(KeepTogether([t, Spacer(1, 16)]))
    # ---- registro de assinaturas ----
    if registro:
        el.append(PageBreak())
        el.append(Paragraph('REGISTRO DE ASSINATURAS ELETRÔNICAS', st['reg_t']))
        el.append(Paragraph(_esc('Documento: %s · assinado pelo sistema Qualicontax (app.qualicontax.com.br) com confirmação de identidade por código enviado ao '
                                 'e-mail de cada signatário. A impressão digital SHA-256 abaixo é a do documento que cada pessoa viu e aceitou.' % montado['titulo']), st['reg']))
        el.append(Spacer(1, 8))
        linhas = [['Signatário', 'Papel', 'CPF', 'E-mail', 'Assinado em', 'IP']]
        for a in registro:
            linhas.append([Paragraph(_esc(a.get('assinado_nome') or a.get('nome')), st['reg']), a['papel'].capitalize(), a.get('assinado_cpf') or a.get('cpf') or '',
                           Paragraph(_esc(a.get('email') or ''), st['reg']), a['assinado_em'].strftime('%d/%m/%Y %H:%M:%S'), a.get('assinado_ip') or ''])
        t = Table(linhas, colWidths=[4.2 * cm, 2.2 * cm, 2.8 * cm, 4.4 * cm, 2.6 * cm, 1.8 * cm], repeatRows=1)
        t.setStyle(TableStyle([('FONT', (0, 0), (-1, -1), 'Helvetica', 7.5), ('FONT', (0, 0), (-1, 0), 'Helvetica-Bold', 7.5),
                               ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8f5ed')), ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
                               ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        el.append(t); el.append(Spacer(1, 8))
        h = registro[0].get('assinado_hash') or ''
        el.append(Paragraph('Impressão digital (SHA-256) do documento aceito:', st['reg']))
        el.append(Paragraph(_esc(h), st['mono']))
        el.append(Spacer(1, 6))
        el.append(Paragraph(_esc('Registro gerado em %s. Fundamento: art. 10, § 2º, da MP nº 2.200-2/2001 e art. 4º da Lei nº 14.063/2020 (assinatura eletrônica).'
                                 % datetime.now().strftime('%d/%m/%Y %H:%M:%S')), st['reg']))
    doc.build(el, onFirstPage=_cabecalho, onLaterPages=_cabecalho)
    return buf.getvalue()


def sha256(b):
    return hashlib.sha256(b).hexdigest()
