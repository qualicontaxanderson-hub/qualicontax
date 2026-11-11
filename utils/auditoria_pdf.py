# -*- coding: utf-8 -*-
"""PDF "Relatório de atividades" de uma pessoa (Auditoria, 11/11/2026).

Mesmo cabeçalho/cores do utils/relatorio.py. Um bloco por dia: faixa verde com
o dia, horário e números; depois a linha do tempo (hora, empresa, o que fez e o
antes → depois). Retrato A4 — sai para imprimir e assinar.
"""
import io
import os
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Image, Spacer, KeepTogether

from utils.relatorio import _p, GREEN, DGREEN, GREY, LINE, TOTBG, ZEBRA
from reportlab.lib.enums import TA_RIGHT

_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'images', 'logo.png')
_AMBAR = colors.HexColor('#B45309')
_VERM = colors.HexColor('#B91C1C')


def _e(t):
    return escape(str(t or ''))


def _o_que(a):
    """Texto do ato para a coluna 'O que fez' (verbo + detalhes, curto)."""
    partes = ['<b>%s</b>%s' % (_e(a['verbo']), ('  (%d vezes, até %s)' % (a['vezes'], a['hora_fim'])) if a.get('vezes', 1) > 1 else '')]
    if a.get('busca'):
        partes.append('buscou: %s' % _e(a['busca']))
    for c in a.get('campos') or []:
        if c['tipo'] == 'sensivel':
            partes.append('%s alterada' % _e(c['label']))
        elif c['tipo'] == 'lista':
            partes.append('%s: %s%s' % (_e(c['label']),
                          ('<font color="#15803d">+ %s</font> ' % _e(', '.join(c['entrou']))) if c['entrou'] else '',
                          ('<font color="#b91c1c">- %s</font>' % _e(', '.join(c['saiu']))) if c['saiu'] else ''))
        elif c['tipo'] == 'novo':
            partes.append('%s: %s' % (_e(c['label']), _e(c.get('depois'))))
        elif c['tipo'] == 'removido':
            partes.append('%s: <strike>%s</strike>' % (_e(c['label']), _e(c.get('antes'))))
        else:
            partes.append('%s: <font color="#94a3b8"><strike>%s</strike></font>&nbsp;»&nbsp;%s'
                          % (_e(c['label']), _e(c.get('antes')), _e(c.get('depois'))))
    if a.get('detalhe'):
        partes.append('<font color="#64748b">%s</font>' % _e(a['detalhe']))
    return '<br/>'.join(partes[:12])


def gerar_pdf_pessoa(nome, periodo, kpi, dias, gerado_em='', gerado_por=''):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=10 * mm, bottomMargin=14 * mm,
                            leftMargin=10 * mm, rightMargin=10 * mm,
                            title='Atividades de %s' % nome)
    larg = doc.width
    el = []

    logo = ''
    if os.path.exists(_LOGO):
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(_LOGO).getSize()
            h = 14 * mm
            logo = Image(_LOGO, width=h * iw / ih, height=h)
        except Exception:
            logo = ''
    titulo = [_p('Relatório de atividades', 16, DGREEN, bold=True),
              _p('Qualicontax — Auditoria do sistema', 8.5, GREY)]
    meta = _p('Gerado em %s<br/>por %s' % (_e(gerado_em), _e(gerado_por)), 7.5, GREY, align=TA_RIGHT)
    head = Table([[logo, titulo, meta]], colWidths=[36 * mm, larg - 86 * mm, 50 * mm])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                              ('LINEBELOW', (0, 0), (-1, -1), 2, GREEN),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
    el += [head, Spacer(1, 6)]
    el += [_p('<b>Pessoa:</b> %s&nbsp;&nbsp;&nbsp;&nbsp;<b>Período:</b> %s' % (_e(nome), _e(periodo)), 9.5),
           Spacer(1, 4),
           _p('%d ações · %d alterações · %d consultas · %d empresas · %d dia(s) com movimento'
              % (kpi['n'], kpi['escritas'], kpi['leituras'], kpi['empresas'], kpi['dias']), 8.5, GREY),
           Spacer(1, 8)]

    for d in reversed(dias):                          # do dia mais antigo ao mais novo
        c = d['cartao']
        faixa = '%s&nbsp;&nbsp;·&nbsp;&nbsp;%s às %s&nbsp;&nbsp;·&nbsp;&nbsp;%d ações' % (
            _e(d['rotulo']), c['ini'], c['fim'], c['n'])
        if c['ips']:
            faixa += '&nbsp;&nbsp;·&nbsp;&nbsp;IP %s' % _e(', '.join(c['ips']))
        bloco = [Table([[_p(faixa, 9, DGREEN, bold=True)]], colWidths=[larg],
                       style=[('BACKGROUND', (0, 0), (-1, -1), TOTBG),
                              ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)])]
        if c['sinais']:
            txt = ' · '.join('%s%s' % (_e(s['texto']), (' às %s' % s['hora']) if s['hora'] else '')
                             for s in c['sinais'])
            cor = _VERM if any(s['nivel'] == 'bad' for s in c['sinais']) else _AMBAR
            bloco += [Spacer(1, 3), _p('Atenção: ' + txt, 8, cor, bold=True)]
        linhas = [[_p('Hora', 7.5, colors.white, bold=True), _p('Empresa', 7.5, colors.white, bold=True),
                   _p('O que fez', 7.5, colors.white, bold=True)]]
        for b in d['blocos']:
            for a in b['atos']:
                linhas.append([_p(a['hora'], 8), _p(_e(a.get('empresa') or '—'), 7.5),
                               _p(_o_que(a), 7.5)])
        t = Table(linhas, colWidths=[14 * mm, 52 * mm, larg - 66 * mm], repeatRows=1)
        st = [('BACKGROUND', (0, 0), (-1, 0), DGREEN), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
              ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
              ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINE)]
        for r in range(2, len(linhas), 2):
            st.append(('BACKGROUND', (0, r), (-1, r), ZEBRA))
        t.setStyle(TableStyle(st))
        el += [KeepTogether(bloco), Spacer(1, 3), t, Spacer(1, 10)]

    if not dias:
        el.append(_p('Nenhuma movimentação desta pessoa no período.', 9, GREY))

    def _rodape(canvas, d):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(GREY)
        canvas.drawString(10 * mm, 7 * mm, 'Qualicontax — Relatório de atividades de %s (%s)' % (nome, periodo))
        canvas.drawRightString(d.pagesize[0] - 10 * mm, 7 * mm, 'Página %d' % canvas.getPageNumber())
        canvas.restoreState()

    doc.build(el, onFirstPage=_rodape, onLaterPages=_rodape)
    return buf.getvalue()
