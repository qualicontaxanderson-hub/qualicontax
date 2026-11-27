# -*- coding: utf-8 -*-
"""
Conferência de DESTINATÁRIOS do lote de honorários (26/11/2026), em PDF e Excel.

Anderson, antes do 1º envio de honorários pelo app: "eu quero ter a garantia para quem será enviado todos os boletos".
A lista sai da mesma prévia que emite (utils/lote_mensal.previa): uma linha por empresa marcada, com o valor, se sai
nota, e exatamente os e-mails que vão receber a cobrança, com a origem de cada um (contato do Financeiro, contato
principal, contato da empresa, e-mail da pessoa). Quem não tem e-mail, ou só tem e-mail da própria Qualicontax, aparece
em vermelho e NÃO sai no lote; quem já tem cobrança desta competência fora do lote aparece em âmbar.
"""
import io
from decimal import Decimal
from xml.sax.saxutils import escape

SITUACOES = {'ok': 'vai sair', 'sem_email': 'sem e-mail: não sai', 'interno': 'só e-mail da Qualicontax: não sai',
             'fora_lote': 'já tem cobrança desta competência fora do lote: conferir', 'forcado': 'vai sair (emitir mesmo assim)'}


def _brl(v):
    v = Decimal(str(v or 0))
    s = f'{v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return 'R$ ' + s


def resumo(linhas):
    """Números do alto da conferência."""
    r = {'n': len(linhas), 'ok': 0, 'v_ok': Decimal('0'), 'sem_email': 0, 'interno': 0, 'fora_lote': 0, 'emails': 0, 'v_fora': Decimal('0')}
    for l in linhas:
        if l['situacao'] in ('ok', 'forcado'):
            r['ok'] += 1
            r['v_ok'] += Decimal(str(l['total']))
            r['emails'] += len(l['destinos'])
        else:
            r[l['situacao']] += 1
            r['v_fora'] += Decimal(str(l['total']))
    return r


def gerar_pdf(ctx):
    """ctx: competencia, linhas (ver routes/financeiro_lote._conferencia), gerado_em, gerado_por, logo_path."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, SimpleDocTemplate, Spacer, Table, TableStyle
    from utils.relatorio_clientes import CINZA, LINHA, VERDE, VERDE_ESC, ZEBRA, _CanvasNumerado, _p

    comp, linhas = ctx['competencia'], ctx['linhas']
    VERM, VERM_CL, AMB, AMB_CL = colors.HexColor('#B91C1C'), colors.HexColor('#FEF2F2'), colors.HexColor('#B45309'), colors.HexColor('#FFFBEB')
    TEXTO, VERDE_CL = colors.HexColor('#1E293B'), colors.HexColor('#F0FDF4')
    titulo = 'Quem recebe a cobrança de %s' % comp.strftime('%m/%Y')
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=8 * mm, bottomMargin=13 * mm, leftMargin=9 * mm, rightMargin=9 * mm,
                            title=titulo, author='Qualicontax')
    L = doc.width
    el = []

    logo = ''
    if ctx.get('logo_path'):
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(ctx['logo_path']).getSize()
            logo = Image(ctx['logo_path'], width=15 * mm * iw / ih, height=15 * mm)
        except Exception:
            logo = ''
    chip = Table([[_p('CONFERÊNCIA', 6.8, colors.white, bold=True, align=TA_CENTER)]], colWidths=[28 * mm], rowHeights=[5.2 * mm])
    chip.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), VERDE), ('ROUNDEDCORNERS', [3, 3, 3, 3]), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                              ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
    lt = Table([[_p(escape(titulo), 15, VERDE_ESC, bold=True, leading=18), chip]], colWidths=[L - 34 * mm - 52 * mm - 32 * mm, 32 * mm])
    lt.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    meio = [lt, _p('Para cada empresa marcada, os e-mails que recebem a nota e o boleto, e de onde cada e-mail vem · '
                   'sem e-mail ou só e-mail da Qualicontax a cobrança NÃO sai · Qualicontax Assessoria Contábil', 8.3, CINZA)]
    meta = _p(f"Gerado em <b>{ctx['gerado_em']}</b>" + (f"<br/>por {escape(ctx['gerado_por'])}" if ctx.get('gerado_por') else ''), 7.8, CINZA, align=TA_RIGHT, leading=10.5)
    head = Table([[logo, meio, meta]], colWidths=[34 * mm, L - 34 * mm - 52 * mm, 52 * mm])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LINEBELOW', (0, 0), (-1, -1), 2, VERDE),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 7), ('LEFTPADDING', (0, 0), (0, 0), 0)]))
    el += [head, Spacer(1, 6)]

    # ── Somas ──
    r = resumo(linhas)
    cards = [('VÃO SAIR', '%d empresa%s' % (r['ok'], 's' if r['ok'] != 1 else ''), _brl(r['v_ok']) + ' · %d e-mail%s' % (r['emails'], 's' if r['emails'] != 1 else ''), VERDE, VERDE_CL),
             ('SEM E-MAIL', str(r['sem_email']), 'não saem: cadastrar o contato comercial', VERM, VERM_CL),
             ('SÓ E-MAIL DA QUALICONTAX', str(r['interno']), 'não saem: cadastrar o contato comercial', VERM, VERM_CL),
             ('JÁ COBRADA FORA DO LOTE', str(r['fora_lote']), 'conferir antes de emitir', AMB, AMB_CL)]
    gap, cw = 3 * mm, (L - 3 * 3 * mm) / 4
    cel = []
    for k, v, sub, cor, fundo in cards:
        t = Table([[_p(k, 6.6, cor, bold=True)], [_p(escape(v), 13, TEXTO, bold=True, leading=15)], [_p(escape(sub), 6.6, CINZA)]], colWidths=[cw])
        t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), fundo), ('BOX', (0, 0), (-1, -1), 0.6, cor), ('ROUNDEDCORNERS', [5, 5, 5, 5]),
                               ('LEFTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
        cel += [t, '']
    krow = Table([cel[:-1]], colWidths=sum([[cw, gap] for _ in cards], [])[:-1])
    krow.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    el += [krow, Spacer(1, 7)]

    # ── Tabela ──
    W = [12 * mm, 58 * mm, 24 * mm, L - (12 + 58 + 24 + 34) * mm, 34 * mm]
    rows = [[_p('Nº', 7, colors.white, bold=True), _p('Empresa', 7, colors.white, bold=True), _p('Valor', 7, colors.white, bold=True, align=TA_RIGHT),
             _p('Quem recebe (e-mail · origem)', 7, colors.white, bold=True), _p('Situação', 7, colors.white, bold=True)]]
    estilo = [('BACKGROUND', (0, 0), (-1, 0), VERDE_ESC), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINHA),
              ('TOPPADDING', (0, 0), (-1, -1), 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5), ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4)]
    for i, l in enumerate(linhas, 1):
        sit = l['situacao']
        cor_sit = VERM if sit in ('sem_email', 'interno') else (AMB if sit == 'fora_lote' else colors.HexColor('#15803D'))
        desc = []
        if l.get('nota'):
            desc.append('nota + boleto')
        else:
            desc.append('só boleto')
        if l.get('tem13'):
            desc.append('13º em boleto próprio')
        if l.get('vencimento'):
            desc.append('vence ' + l['vencimento'].strftime('%d/%m/%Y'))
        emp = [_p('<b>%s</b>' % escape(l['empresa']), 7.6, TEXTO, leading=9.5), _p(escape(' · '.join(desc)), 6.4, CINZA)]
        if l['destinos']:
            mails = [_p('<b>%s</b> <font color="#64748B">· %s%s</font>' % (escape(d['email']), escape(d['fonte']), (' (%s)' % escape(d['nome'])) if d.get('nome') else ''), 6.9, TEXTO, leading=9)
                     for d in l['destinos']]
        elif sit == 'interno':
            mails = [_p('descartado: <b>%s</b> (e-mail da Qualicontax)' % escape(e), 6.9, VERM, leading=9) for e in l['internos']]
        else:
            mails = [_p('nenhum contato com e-mail', 6.9, VERM, leading=9)]
        sit_txt = SITUACOES[sit]
        if sit == 'fora_lote' and l.get('fora'):
            f = l['fora']
            sit_txt += ' — título %s, %s%s' % (f['id'], _brl(f['valor']), (', vence ' + f['vencimento'].strftime('%d/%m/%Y')) if f.get('vencimento') else '')
        rows.append([_p(escape(str(l.get('numero') or '—')), 7.4, CINZA, bold=True), emp, _p(_brl(l['total']), 7.6, TEXTO, bold=True, align=TA_RIGHT),
                     mails, _p(escape(sit_txt), 6.8, cor_sit, bold=True, leading=9)])
        if sit in ('sem_email', 'interno'):
            estilo.append(('BACKGROUND', (0, i), (-1, i), VERM_CL))
        elif sit == 'fora_lote':
            estilo.append(('BACKGROUND', (0, i), (-1, i), AMB_CL))
        elif i % 2 == 0:
            estilo.append(('BACKGROUND', (0, i), (-1, i), ZEBRA))
    t = Table(rows, colWidths=W, repeatRows=1)
    t.setStyle(TableStyle(estilo))
    el.append(t)
    doc.build(el, canvasmaker=lambda *a, **k: _CanvasNumerado(*a, rodape='Qualicontax · ' + ctx['gerado_em'], **k))
    return buf.getvalue()


def gerar_xlsx(ctx):
    """Mesma lista em Excel: uma linha por e-mail (a empresa repete), para filtrar e conferir."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    comp, linhas = ctx['competencia'], ctx['linhas']
    wb = Workbook(); ws = wb.active; ws.title = 'Quem recebe %s' % comp.strftime('%m-%Y')
    ws.append(['Quem recebe a cobrança de %s · gerado em %s%s' % (comp.strftime('%m/%Y'), ctx['gerado_em'], (' por ' + ctx['gerado_por']) if ctx.get('gerado_por') else '')])
    ws.cell(row=1, column=1).font = Font(bold=True, color='0F3D2E', size=12)
    cols = ['Nº', 'Empresa', 'Valor', 'Nota', '13º próprio', 'Vencimento', 'E-mail', 'Origem do e-mail', 'Nome do contato', 'Situação']
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(cols))
    ws.append(cols)
    for i in range(1, len(cols) + 1):
        c = ws.cell(row=2, column=i); c.font = Font(bold=True, color='FFFFFF'); c.fill = PatternFill('solid', fgColor='15803D')
    verm, amb = PatternFill('solid', fgColor='FEF2F2'), PatternFill('solid', fgColor='FFFBEB')
    for l in linhas:
        sit = l['situacao']
        sit_txt = SITUACOES[sit]
        if sit == 'fora_lote' and l.get('fora'):
            sit_txt += ' (título %s, %s)' % (l['fora']['id'], _brl(l['fora']['valor']))
        base = [l.get('numero') or '', l['empresa'], float(l['total']), 'sim' if l.get('nota') else 'não', 'sim' if l.get('tem13') else '',
                l['vencimento'].strftime('%d/%m/%Y') if l.get('vencimento') else '']
        dest = l['destinos'] or ([{'email': e, 'fonte': 'DESCARTADO: e-mail da Qualicontax', 'nome': ''} for e in l['internos']] if sit == 'interno'
                                 else [{'email': '', 'fonte': 'nenhum contato com e-mail', 'nome': ''}])
        for d in dest:
            ws.append(base + [d['email'], d['fonte'], d.get('nome') or '', sit_txt])
            if sit in ('sem_email', 'interno'):
                for i in range(1, len(cols) + 1):
                    ws.cell(row=ws.max_row, column=i).fill = verm
            elif sit == 'fora_lote':
                for i in range(1, len(cols) + 1):
                    ws.cell(row=ws.max_row, column=i).fill = amb
            ws.cell(row=ws.max_row, column=3).number_format = '#,##0.00'
    for i, w in enumerate((7, 42, 12, 6, 10, 12, 38, 26, 24, 44), 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = 'A3'
    for row in ws.iter_rows(min_row=3, max_col=len(cols)):
        row[2].alignment = Alignment(horizontal='right')
    bio = io.BytesIO(); wb.save(bio)
    return bio.getvalue()
