# -*- coding: utf-8 -*-
"""
Relatório de CLIENTES (11/10/2026) — resumido ou completo, em PDF (ReportLab) e
Excel (openpyxl). Python puro: recebe a lista já montada pela tela de Clientes
(routes/clientes._lista_clientes_tela) e devolve bytes.

    ctx = {'tipo': 'resumido'|'completo', 'clientes': [...], 'extra': {id: {...}},
           'kpis': {...}, 'segmentos': {'COMERCIO': 'Comércio', ...},
           'filtros': 'Ativos · Lucro Real', 'gerado_em': '11/10/2026 09:30',
           'gerado_por': 'Anderson', 'logo_path': '/.../logo.png' | None}

As contagens vêm da MESMA lista dos cartões: o relatório nunca diverge da tela.
"""
import io
from datetime import date, datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

# ── Paleta (a mesma do app) ──────────────────────────────────────────────────
VERDE = colors.HexColor('#16A34A')
VERDE_ESC = colors.HexColor('#0F3D2E')
VERDE_MED = colors.HexColor('#15803D')
CINZA = colors.HexColor('#64748B')
CINZA_CL = colors.HexColor('#94A3B8')
LINHA = colors.HexColor('#E8F1EC')
ZEBRA = colors.HexColor('#F7FBF8')

REGIMES = [('lucro_real', 'Lucro Real', '#2563EB'), ('lucro_presumido', 'Lucro Presumido', '#EA580C'),
           ('simples', 'Simples Nacional', '#16A34A'), ('mei', 'MEI', '#CA8A04'),
           ('pf', 'Pessoa Física', '#7C3AED')]
REGIME_NOME = {k: n for k, n, _ in REGIMES}
SEG_COR = {'COMERCIO': '#0EA5E9', 'SERVICOS': '#16A34A', 'LOCACAO': '#CA8A04', 'INDUSTRIA': '#64748B'}
# nível do certificado → (rótulo, cor do texto, fundo)
CERT = {'ok': ('Válido', '#047857', '#ECFDF5'),
        'laranja': ('Vence em 30 dias', '#B45309', '#FFFBEB'),
        'vencido': ('Vencido', '#B91C1C', '#FEF2F2'),
        'sem_data': ('Sem certificado', '#64748B', '#F1F5F9')}
PORTE = {'ME': 'ME', 'EPP': 'EPP', 'MEDIO': 'Médio', 'GRANDE': 'Grande', 'MEI': 'MEI'}


# ── Dados comuns aos dois formatos ───────────────────────────────────────────
def _nivel(c):
    return c.get('cert_nivel') if c.get('cert_nivel') in CERT else 'sem_data'


def _validade(c):
    v = c.get('cert_validade')
    return v.date() if isinstance(v, datetime) else v


def _data(v):
    if isinstance(v, datetime):
        v = v.date()
    return v.strftime('%d/%m/%Y') if isinstance(v, date) else ''


def _segs(c, segmentos):
    if c.get('tipo_pessoa') == 'PF':
        return ['Pessoa Física'] if c.get('ramo_atividade_nome') else []
    return [segmentos.get(s, s) for s in (c.get('f_segs') or '').split(',') if s]


def _grupo(c):
    g = c.get('f_grupo')
    return g['nome'] if g else ''


def _num(c):
    n = (c.get('numero_cliente') or '').strip()
    return int(n) if n.isdigit() else n


def _contagens(ctx):
    """Quadros do resumo: por regime, segmento, certificado e município."""
    lista, segmentos = ctx['clientes'], ctx['segmentos']
    reg = {k: 0 for k, _, _ in REGIMES}
    seg = {k: 0 for k in segmentos}
    sem_ramo = 0
    mun = {}
    for c in lista:
        if c.get('f_reg') in reg:
            reg[c['f_reg']] += 1
        if not c.get('ramo_atividade_nome'):
            sem_ramo += 1
        for s in (c.get('f_segs') or '').split(','):
            if s in seg:
                seg[s] += 1
        if c.get('f_local'):
            mun[c['f_local']] = mun.get(c['f_local'], 0) + 1
    k = ctx['kpis']
    return {
        'regime': [(n, reg[key], cor) for key, n, cor in REGIMES],
        'segmento': [(segmentos[s], seg[s], SEG_COR.get(s, '#64748B')) for s in segmentos]
                    + [('Sem ramo de atividade', sem_ramo, '#DC2626')],
        'cert': [('Válido', k['cert_ok'], '#16A34A'), ('Vence em 30 dias', k['cert_vencendo'], '#D97706'),
                 ('Vencido', k['cert_vencido'], '#DC2626'), ('Sem certificado', k['cert_sem'], '#94A3B8')],
        'municipio': sorted(mun.items(), key=lambda x: (-x[1], x[0])),
    }


# ══════════════════════════════════════════════════════════════════════════════
# PDF
# ══════════════════════════════════════════════════════════════════════════════
def _p(txt, size=8, cor=colors.black, bold=False, align=TA_LEFT, leading=None):
    st = ParagraphStyle('x', fontName='Helvetica-Bold' if bold else 'Helvetica', fontSize=size,
                        leading=leading or size + 2.2, textColor=cor, alignment=align)
    return Paragraph(str(txt), st)


def _e(v):
    return escape(str(v)) if v not in (None, '') else '—'


class _CanvasNumerado(rl_canvas.Canvas):
    """'Página X de Y' no rodapé (precisa saber o total antes de desenhar)."""

    def __init__(self, *a, rodape='', **kw):
        super().__init__(*a, **kw)
        self._paginas, self._rodape = [], rodape

    def showPage(self):
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas)
        for est in self._paginas:
            self.__dict__.update(est)
            self.saveState()
            w = self._pagesize[0]
            self.setStrokeColor(LINHA); self.setLineWidth(0.8)
            self.line(8 * mm, 9.5 * mm, w - 8 * mm, 9.5 * mm)
            self.setFont('Helvetica', 7); self.setFillColor(CINZA)
            self.drawString(8 * mm, 6 * mm, self._rodape)
            self.drawRightString(w - 8 * mm, 6 * mm, 'Página %d de %d' % (self._pageNumber, total))
            self.restoreState()
            super().showPage()
        super().save()


def _barra(n, total, cor, larg=34 * mm):
    d = Drawing(larg, 6)
    d.add(Rect(0, 1, larg, 4, fillColor=colors.HexColor('#EEF2F0'), strokeColor=None))
    if total and n:
        d.add(Rect(0, 1, max(1.5, larg * n / total), 4, fillColor=colors.HexColor(cor), strokeColor=None))
    return d


def _quadro(titulo, linhas, total, larg, obs=None):
    """Mini-tabela do resumo: rótulo | barra | quantidade | %."""
    lb = larg - 2 * mm
    corpo = [[_p(titulo.upper(), 7, VERDE_MED, bold=True), '', '', '']]
    for rot, n, cor in linhas:
        pct = f'{100 * n / total:.0f}%' if total else '—'
        corpo.append([_p(escape(rot), 7.6, colors.HexColor('#1E293B')), _barra(n, total, cor, lb * .30),
                      _p(f'<b>{n}</b>', 8, VERDE_ESC, align=TA_RIGHT), _p(pct, 7, CINZA, align=TA_RIGHT)])
    if obs:
        corpo.append([_p(obs, 6.3, CINZA_CL), '', '', ''])
    t = Table(corpo, colWidths=[lb * .41, lb * .30, lb * .13, lb * .16])
    st = [('SPAN', (0, 0), (-1, 0)), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
          ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
          ('TOPPADDING', (0, 0), (-1, -1), 2.2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.2),
          ('BOTTOMPADDING', (0, 0), (-1, 0), 5), ('LINEBELOW', (0, 0), (-1, 0), 0.8, LINHA),
          ('BOX', (0, 0), (-1, -1), 0.8, LINHA), ('BACKGROUND', (0, 0), (-1, -1), colors.white),
          ('ROUNDEDCORNERS', [6, 6, 6, 6])]
    if obs:
        st.append(('SPAN', (0, -1), (-1, -1)))
    t.setStyle(TableStyle(st))
    return t


def _cert_celula(c, size=7.6, linha=False):
    nv = _nivel(c)
    rot, cor, _ = CERT[nv]
    v, dias = _validade(c), c.get('cert_dias')
    br = ' · ' if linha else '<br/>'
    if nv == 'ok':
        txt = f'<b>Válido</b>{br}até {_data(v)}'
    elif nv == 'laranja':
        txt = f'<b>Vence em {dias} dia{"" if dias == 1 else "s"}</b>{br}{_data(v)}'
    elif nv == 'vencido':
        txt = f'<b>Vencido</b>{br}{_data(v)}'
    else:
        txt = '<b>Sem certificado</b>'
    return _p(txt, size, colors.HexColor(cor), leading=size + 2)


def gerar_pdf(ctx):
    completo = ctx['tipo'] == 'completo'
    lista, k = ctx['clientes'], ctx['kpis']
    total = len(lista)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=8 * mm, bottomMargin=13 * mm,
                            leftMargin=8 * mm, rightMargin=8 * mm,
                            title=f"Relatório de Clientes ({ctx['tipo']})", author='Qualicontax')
    L = doc.width
    el = []

    # ── Cabeçalho ──
    logo = ''
    if ctx.get('logo_path'):
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(ctx['logo_path']).getSize()
            h = 14 * mm
            logo = Image(ctx['logo_path'], width=h * iw / ih, height=h)
        except Exception:
            logo = ''
    chip = Table([[_p(('COMPLETO' if completo else 'RESUMIDO'), 7, colors.white, bold=True, align=TA_CENTER)]],
                 colWidths=[20 * mm], rowHeights=[5 * mm])
    chip.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), VERDE), ('ROUNDEDCORNERS', [3, 3, 3, 3]),
                              ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 0),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
    tit = Table([[_p('Relatório de Clientes', 18, VERDE_ESC, bold=True, leading=21), chip]],
                colWidths=[72 * mm, 24 * mm])
    tit.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    meio = [tit, _p('Qualicontax Assessoria Contábil — carteira de clientes', 8.5, CINZA)]
    meta = _p(f"Gerado em <b>{ctx['gerado_em']}</b>" + (f"<br/>por {escape(ctx['gerado_por'])}" if ctx.get('gerado_por') else ''),
              8, CINZA, align=TA_RIGHT, leading=11)
    head = Table([[logo, meio, meta]], colWidths=[36 * mm, L - 96 * mm, 60 * mm])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LINEBELOW', (0, 0), (-1, -1), 2, VERDE),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 7), ('LEFTPADDING', (0, 0), (0, 0), 0)]))
    el += [head, Spacer(1, 5)]
    filtros = ctx.get('filtros') or 'Carteira inteira (sem filtro)'
    el += [_p(f'<b>Filtros:</b> {escape(filtros)}&nbsp;&nbsp;&nbsp;·&nbsp;&nbsp;&nbsp;'
              f'<b>{total}</b> cliente{"" if total == 1 else "s"} neste relatório', 8.5, colors.HexColor('#334155')),
           Spacer(1, 6)]

    # ── KPIs ──
    cards = [('Clientes', total, '#0F3D2E', '#F0FDF4'), ('Ativos', k['ativos'], '#15803D', '#ECFDF5'),
             ('Inativos', k['inativos'], '#475569', '#F8FAFC'),
             ('Certificado válido', k['com_certificado'], '#0369A1', '#F0F9FF'),
             ('Vence em 30 dias', k['cert_vencendo'], '#B45309', '#FFFBEB'),
             ('Certificado vencido', k['cert_vencido'], '#B91C1C', '#FEF2F2'),
             ('Sem certificado', k['cert_sem'], '#64748B', '#F8FAFC')]
    gap = 2.5 * mm
    cw = (L - gap * (len(cards) - 1)) / len(cards)
    cel = []
    for rot, n, cor, fundo in cards:
        t = Table([[_p(f'<b>{n}</b>', 17, colors.HexColor(cor), leading=19)],
                   [_p(rot.upper(), 6.5, CINZA, bold=True)]], colWidths=[cw])
        t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(fundo)),
                               ('LINEABOVE', (0, 0), (-1, 0), 2.2, colors.HexColor(cor)),
                               ('LEFTPADDING', (0, 0), (-1, -1), 7), ('TOPPADDING', (0, 0), (-1, 0), 6),
                               ('BOTTOMPADDING', (0, -1), (-1, -1), 6), ('TOPPADDING', (0, 1), (-1, 1), 0)]))
        cel += [t, '']
    krow = Table([cel[:-1]], colWidths=sum([[cw, gap] for _ in cards], [])[:-1])
    krow.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                              ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    el += [krow, Spacer(1, 7)]

    # ── Resumo em quadros ──
    cont = _contagens(ctx)
    qw = (L - 3 * gap) / 4
    mun = cont['municipio']
    mun_l = [(m, n, '#0F766E') for m, n in mun[:6]]
    if len(mun) > 6:
        mun_l.append((f'Outros {len(mun) - 6} municípios', sum(n for _, n in mun[6:]), '#94A3B8'))
    quadros = [_quadro('Regime de tributação', cont['regime'], total, qw),
               _quadro('Segmento', cont['segmento'], total, qw,
                       obs='Uma empresa pode estar em mais de um segmento.'),
               _quadro('Certificado digital', cont['cert'], total, qw),
               _quadro('Municípios', mun_l, total, qw)]
    qrow = Table([[quadros[0], '', quadros[1], '', quadros[2], '', quadros[3]]],
                 colWidths=[qw, gap, qw, gap, qw, gap, qw])
    qrow.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                              ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    el += [qrow, Spacer(1, 8)]

    # ── Atenção: certificados vencidos / vencendo ──
    alerta = [c for c in lista if _nivel(c) in ('vencido', 'laranja')]
    if alerta:
        alerta.sort(key=lambda c: c.get('cert_dias') if c.get('cert_dias') is not None else 99999)
        lin = [[_p('<b>ATENÇÃO — CERTIFICADOS VENCIDOS OU VENCENDO EM 30 DIAS</b>', 7.5, colors.HexColor('#92400E')),
                '', '', '']]
        for c in alerta:
            lin.append([_p(f'<b>{_e(c.get("numero_cliente"))}</b>', 7.6, VERDE_ESC),
                        _p(_e(c.get('nome_razao_social')), 7.6), _p(_e(c.get('cpf_cnpj')), 7.4, CINZA),
                        _cert_celula(c, 7.4, linha=True)])
        aw = [12 * mm, L - 12 * mm - 42 * mm - 50 * mm, 42 * mm, 50 * mm]
        ta = Table(lin, colWidths=aw, repeatRows=1)
        ta.setStyle(TableStyle([('SPAN', (0, 0), (-1, 0)), ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFFBEB')),
                                ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#FCD34D')),
                                ('LINEBELOW', (0, 1), (-1, -2), 0.4, colors.HexColor('#FDE68A')),
                                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                                ('ROUNDEDCORNERS', [6, 6, 6, 6])]))
        el += [ta, Spacer(1, 8)]

    # ── Tabela de clientes ──
    el.append(_p('RELAÇÃO DE CLIENTES', 8, VERDE_MED, bold=True))
    el.append(Spacer(1, 3))
    hd = lambda t, a=TA_LEFT: _p(t, 7, colors.white, bold=True, align=a)
    linhas, inativas = [], []
    if completo:
        extra = ctx.get('extra') or {}
        larg = [11, 62, 29, 25, 40, 46, 42, 26]
        linhas.append([hd('Nº'), hd('Empresa'), hd('Inscrições'), hd('Regime'), hd('Atividades'),
                       hd('Endereço'), hd('Contato'), hd('Certificado')])
        for c in lista:
            x = extra.get(c['id'], {})
            emp = f'<b>{_e(c.get("nome_razao_social"))}</b>'
            if x.get('nome_fantasia'):
                emp += f'<br/><font color="#64748B">{escape(x["nome_fantasia"])}</font>'
            emp += (f'<br/><font face="Courier-Bold" size="7">{_e(c.get("cpf_cnpj"))}</font>')
            if _grupo(c):
                emp += f'<br/><font color="#15803D">Grupo: {escape(_grupo(c))}</font>'
            if c.get('situacao') != 'ATIVO':
                emp += ' <font color="#B91C1C"><b>· INATIVO</b></font>'
            insc = (f'<font color="#7D968A" size="6">IE</font> <b>{_e(c.get("inscricao_estadual"))}</b><br/>'
                    f'<font color="#7D968A" size="6">IM</font> <b>{_e(c.get("inscricao_municipal"))}</b>')
            reg = f'<b>{REGIME_NOME.get(c.get("f_reg"), "—")}</b>'
            if x.get('porte_empresa'):
                reg += f'<br/><font color="#64748B">Porte {PORTE.get(x["porte_empresa"], x["porte_empresa"])}</font>'
            segs = _segs(c, ctx['segmentos'])
            ativ = (f'<font color="#15803D"><b>{escape(", ".join(segs))}</b></font><br/>' if segs else '') + \
                   (escape(c['ramo_atividade_nome']) if c.get('ramo_atividade_nome')
                    else '<font color="#B91C1C">sem ramo</font>')
            if x.get('cnae_fiscal'):
                ativ += f'<br/><font color="#64748B" size="6.4">CNAE {escape(str(x["cnae_fiscal"]))}</font>'
            rua = ', '.join(v for v in (x.get('logradouro'), x.get('numero')) if v)
            end = '<br/>'.join(escape(v) for v in (
                ' — '.join(v for v in (rua, x.get('complemento')) if v), x.get('bairro'),
                ' · '.join(v for v in (c.get('f_local'), ('CEP ' + x['cep']) if x.get('cep') else '') if v)) if v) or '—'
            cont_ = '<br/>'.join(v for v in (escape(c['email']) if c.get('email') else '',
                                             f'<b>{escape(c["celular"])}</b>' if c.get('celular') else '',
                                             escape(c['telefone']) if c.get('telefone') else '') if v) or '—'
            linhas.append([_p(f'<b>{_e(c.get("numero_cliente"))}</b>', 7.6, VERDE_ESC),
                           _p(emp, 7.3), _p(insc, 7.2), _p(reg, 7.2), _p(ativ, 7), _p(end, 7),
                           _p(cont_, 7), _cert_celula(c, 7.1)])
            if c.get('situacao') != 'ATIVO':
                inativas.append(len(linhas) - 1)
    else:
        larg = [11, 76, 35, 27, 38, 36, 40, 14]
        linhas.append([hd('Nº'), hd('Razão social'), hd('CNPJ / CPF'), hd('Regime'), hd('Município/UF'),
                       hd('Grupo'), hd('Certificado'), hd('Situação')])
        for c in lista:
            ativo = c.get('situacao') == 'ATIVO'
            linhas.append([_p(f'<b>{_e(c.get("numero_cliente"))}</b>', 7.8, VERDE_ESC),
                           _p(f'<b>{_e(c.get("nome_razao_social"))}</b>', 7.6),
                           _p(f'<font face="Courier-Bold">{_e(c.get("cpf_cnpj"))}</font>', 7.4),
                           _p(REGIME_NOME.get(c.get('f_reg'), '—'), 7.4),
                           _p(_e(c.get('f_local')), 7.4),
                           _p(escape(_grupo(c)), 7.2, VERDE_MED) if _grupo(c) else _p('—', 7.2, CINZA_CL),
                           _cert_celula(c, 7.2, linha=True),
                           _p(f'<b>{"Ativo" if ativo else "Inativo"}</b>', 7.2,
                              colors.HexColor('#15803D' if ativo else '#B91C1C'))])
            if not ativo:
                inativas.append(len(linhas) - 1)
    soma = sum(larg)
    tbl = Table(linhas, colWidths=[L * w / soma for w in larg], repeatRows=1)
    st = [('BACKGROUND', (0, 0), (-1, 0), VERDE_ESC), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
          ('TOPPADDING', (0, 0), (-1, -1), 3.2 if completo else 2.6),
          ('BOTTOMPADDING', (0, 0), (-1, -1), 3.2 if completo else 2.6),
          ('TOPPADDING', (0, 0), (-1, 0), 4.5), ('BOTTOMPADDING', (0, 0), (-1, 0), 4.5),
          ('LEFTPADDING', (0, 0), (-1, -1), 4.5), ('RIGHTPADDING', (0, 0), (-1, -1), 4.5),
          ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINHA)]
    for r in range(2, len(linhas), 2):
        st.append(('BACKGROUND', (0, r), (-1, r), ZEBRA))
    for r in inativas:
        st.append(('BACKGROUND', (0, r), (-1, r), colors.HexColor('#FEF2F2')))
    tbl.setStyle(TableStyle(st))
    el.append(tbl)
    if not lista:
        el += [Spacer(1, 6), _p('Nenhum cliente com os filtros escolhidos.', 9, CINZA)]

    rodape = f"Qualicontax Assessoria Contábil — Relatório de Clientes ({ctx['tipo']}) — gerado em {ctx['gerado_em']}"
    doc.build(el, canvasmaker=lambda *a, **kw: _CanvasNumerado(*a, rodape=rodape, **kw))
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# EXCEL
# ══════════════════════════════════════════════════════════════════════════════
def gerar_xlsx(ctx):
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.chart.label import DataLabelList
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    completo = ctx['tipo'] == 'completo'
    lista, k = ctx['clientes'], ctx['kpis']
    total = len(lista)
    F = 'Calibri'
    fill = lambda hexa: PatternFill('solid', fgColor=hexa)
    fino = Side(style='thin', color='E2E8F0')
    borda = Border(bottom=fino)

    wb = Workbook()

    # ── Aba RESUMO ──
    ws = wb.active
    ws.title = 'Resumo'
    ws.sheet_view.showGridLines = False
    ws.column_dimensions['A'].width = 2
    for col, w in zip('BCDEFGHIJKLMNOP', (24, 11, 9, 3, 24, 11, 9, 3, 24, 11, 9, 3, 24, 11, 9)):
        ws.column_dimensions[col].width = w
    ws['B2'] = 'Relatório de Clientes — ' + ('Completo' if completo else 'Resumido')
    ws['B2'].font = Font(name=F, size=18, bold=True, color='0F3D2E')
    ws['B3'] = 'Qualicontax Assessoria Contábil'
    ws['B3'].font = Font(name=F, size=11, color='64748B')
    ws['B4'] = f"Gerado em {ctx['gerado_em']}" + (f" por {ctx['gerado_por']}" if ctx.get('gerado_por') else '')
    ws['B4'].font = Font(name=F, size=10, color='64748B')
    ws['B5'] = 'Filtros: ' + (ctx.get('filtros') or 'carteira inteira (sem filtro)') + f'  ·  {total} clientes'
    ws['B5'].font = Font(name=F, size=10, bold=True, color='334155')
    for c in range(2, 17):
        ws.cell(row=6, column=c).border = Border(bottom=Side(style='medium', color='16A34A'))

    cards = [('CLIENTES', total, '0F3D2E', 'F0FDF4'), ('ATIVOS', k['ativos'], '15803D', 'ECFDF5'),
             ('INATIVOS', k['inativos'], '475569', 'F8FAFC'),
             ('CERTIFICADO VÁLIDO', k['com_certificado'], '0369A1', 'F0F9FF'),
             ('VENCE EM 30 DIAS', k['cert_vencendo'], 'B45309', 'FFFBEB'),
             ('CERTIFICADO VENCIDO', k['cert_vencido'], 'B91C1C', 'FEF2F2'),
             ('SEM CERTIFICADO', k['cert_sem'], '64748B', 'F8FAFC')]
    # 7 cartões em duas linhas (4 + 3), cada um em 2 colunas (rótulo grande + número)
    # situação em cima (3), certificado embaixo (4): cada cartão ocupa 3 colunas
    pos = [(8, 2), (8, 6), (8, 10), (11, 2), (11, 6), (11, 10), (11, 14)]
    for (r, col), (rot, n, cor, fundo) in zip(pos, cards):
        a = ws.cell(row=r, column=col, value=rot)
        b = ws.cell(row=r + 1, column=col, value=n)
        a.font = Font(name=F, size=9, bold=True, color='64748B')
        b.font = Font(name=F, size=22, bold=True, color=cor)
        for rr in (r, r + 1):
            for cc in (col, col + 1, col + 2):
                ws.cell(row=rr, column=cc).fill = fill(fundo)
        for cc in (col, col + 1, col + 2):
            ws.cell(row=r, column=cc).border = Border(top=Side(style='thick', color=cor))
        b.alignment = Alignment(horizontal='left', vertical='center')
        ws.row_dimensions[r + 1].height = 30

    cont = _contagens(ctx)

    def quadro(r0, col, titulo, linhas, obs=None):
        ws.cell(row=r0, column=col, value=titulo.upper()).font = Font(name=F, size=10, bold=True, color='15803D')
        for i, h in enumerate(('', 'Empresas', '%')):
            c = ws.cell(row=r0 + 1, column=col + i, value=h or titulo.split()[0])
            c.font = Font(name=F, size=9, bold=True, color='FFFFFF'); c.fill = fill('0F3D2E')
            c.alignment = Alignment(horizontal='right' if i else 'left')
        r = r0 + 2
        for rot, n, cor in linhas:
            a = ws.cell(row=r, column=col, value=rot)
            b = ws.cell(row=r, column=col + 1, value=n)
            p = ws.cell(row=r, column=col + 2, value=(n / total) if total else 0)
            a.font = Font(name=F, size=10, color='1E293B')
            b.font = Font(name=F, size=10, bold=True, color=cor.lstrip('#'))
            p.number_format = '0%'; p.font = Font(name=F, size=9, color='64748B')
            for cc in (a, b, p):
                cc.border = borda
            r += 1
        if obs:
            ws.cell(row=r, column=col, value=obs).font = Font(name=F, size=8, italic=True, color='94A3B8')
            r += 1
        return r0 + 2, r - 1 - (1 if obs else 0)

    r_reg = quadro(15, 2, 'Regime de tributação', cont['regime'])
    r_seg = quadro(15, 6, 'Segmento', cont['segmento'], obs='Uma empresa pode estar em mais de um segmento.')
    r_cert = quadro(15, 10, 'Certificado digital', cont['cert'])
    quadro(15, 14, 'Municípios', [(m, n, '#0F766E') for m, n in cont['municipio']])

    # gráficos: regime e certificado
    # um gráfico embaixo de cada quadro (regime, segmento, certificado)
    for (ini, fim), col, titulo, ancora, cor in ((r_reg, 2, 'Clientes por regime', 'B26', '16A34A'),
                                                 (r_seg, 6, 'Clientes por segmento', 'F26', '0EA5E9'),
                                                 (r_cert, 10, 'Certificado digital', 'J26', '0369A1')):
        ch = BarChart(); ch.type = 'bar'; ch.style = 10; ch.title = titulo
        ch.add_data(Reference(ws, min_col=col + 1, min_row=ini, max_row=fim), titles_from_data=False)
        ch.set_categories(Reference(ws, min_col=col, min_row=ini, max_row=fim))
        ch.legend = None; ch.height = 7; ch.width = 8.6
        ch.y_axis.majorGridlines = None; ch.x_axis.scaling.orientation = 'maxMin'
        ch.y_axis.delete = True; ch.x_axis.delete = False
        s = ch.series[0]; s.graphicalProperties.solidFill = cor; s.graphicalProperties.line.solidFill = cor
        s.dLbls = DataLabelList(); s.dLbls.showVal = True
        s.dLbls.showSerName = s.dLbls.showCatName = s.dLbls.showLegendKey = s.dLbls.showPercent = False
        ws.add_chart(ch, ancora)
    ws.freeze_panes = None

    # ── Aba CLIENTES ──
    wc = wb.create_sheet('Clientes')
    wc.sheet_view.showGridLines = False
    extra = ctx.get('extra') or {}
    if completo:
        cols = [('Nº', 7), ('Razão social', 46), ('Nome fantasia', 28), ('Tipo', 7), ('CNPJ / CPF', 20),
                ('Insc. Estadual', 16), ('Insc. Municipal', 15), ('Regime', 17), ('Porte', 9),
                ('Segmento', 20), ('Atividades', 34), ('Grupo', 26), ('E-mail', 32), ('Celular', 16),
                ('Telefone', 16), ('CEP', 11), ('Endereço', 40), ('Bairro', 22), ('Município', 22), ('UF', 5),
                ('CNAE', 11), ('Descrição do CNAE', 46), ('Início da atividade', 13), ('Cliente desde', 13),
                ('Certificado', 17), ('Validade', 12), ('Dias p/ vencer', 9), ('Situação', 10), ('Cadastrado em', 13)]
    else:
        cols = [('Nº', 7), ('Razão social', 50), ('CNPJ / CPF', 20), ('Regime', 17), ('Segmento', 20),
                ('Atividades', 34), ('Município', 22), ('UF', 5), ('Grupo', 26), ('E-mail', 32), ('Celular', 16),
                ('Certificado', 17), ('Validade', 12), ('Situação', 10)]
    nc = len(cols)
    wc['A1'] = 'Relatório de Clientes — ' + ('Completo' if completo else 'Resumido')
    wc['A1'].font = Font(name=F, size=15, bold=True, color='0F3D2E')
    wc['A2'] = f"Gerado em {ctx['gerado_em']}  ·  Filtros: {ctx.get('filtros') or 'carteira inteira'}  ·  {total} clientes"
    wc['A2'].font = Font(name=F, size=10, color='64748B')
    HR = 4
    for i, (h, w) in enumerate(cols, 1):
        c = wc.cell(row=HR, column=i, value=h)
        c.font = Font(name=F, size=10, bold=True, color='FFFFFF'); c.fill = fill('0F3D2E')
        c.alignment = Alignment(vertical='center', wrap_text=True)
        wc.column_dimensions[get_column_letter(i)].width = w
    wc.row_dimensions[HR].height = 30

    r = HR + 1
    for c in lista:
        x = extra.get(c['id'], {})
        nv = _nivel(c)
        cidade, uf = (c.get('f_local') or '/').rsplit('/', 1) if c.get('f_local') else ('', '')
        base = {
            'Nº': _num(c), 'Razão social': c.get('nome_razao_social'), 'Nome fantasia': x.get('nome_fantasia'),
            'Tipo': c.get('tipo_pessoa'), 'CNPJ / CPF': c.get('cpf_cnpj'),
            'Insc. Estadual': c.get('inscricao_estadual'), 'Insc. Municipal': c.get('inscricao_municipal'),
            'Regime': REGIME_NOME.get(c.get('f_reg'), ''), 'Porte': PORTE.get(x.get('porte_empresa'), x.get('porte_empresa')),
            'Segmento': ', '.join(_segs(c, ctx['segmentos'])), 'Atividades': c.get('ramo_atividade_nome') or 'sem ramo',
            'Grupo': _grupo(c), 'E-mail': c.get('email'), 'Celular': c.get('celular'), 'Telefone': c.get('telefone'),
            'CEP': x.get('cep'),
            'Endereço': ' — '.join(v for v in (', '.join(v for v in (x.get('logradouro'), x.get('numero')) if v),
                                              x.get('complemento')) if v),
            'Bairro': x.get('bairro'), 'Município': cidade, 'UF': uf,
            'CNAE': x.get('cnae_fiscal'), 'Descrição do CNAE': x.get('cnae_fiscal_descricao'),
            'Início da atividade': x.get('data_inicio_atividade'), 'Cliente desde': x.get('data_inicio_contrato'),
            'Certificado': CERT[nv][0], 'Validade': _validade(c),
            'Dias p/ vencer': c.get('cert_dias'), 'Situação': 'Ativo' if c.get('situacao') == 'ATIVO' else 'Inativo',
            'Cadastrado em': x.get('criado_em'),
        }
        for i, (h, _) in enumerate(cols, 1):
            v = base.get(h)
            if isinstance(v, datetime):
                v = v.date()
            cell = wc.cell(row=r, column=i, value=v if v not in ('',) else None)
            cell.font = Font(name=F, size=10, color='1E293B', bold=h in ('Nº', 'Razão social'))
            cell.border = borda
            cell.alignment = Alignment(vertical='center')
            if isinstance(v, date):
                cell.number_format = 'DD/MM/YYYY'
            if h == 'Certificado':
                cell.font = Font(name=F, size=10, bold=True, color=CERT[nv][1].lstrip('#'))
                cell.fill = fill(CERT[nv][2].lstrip('#'))
            elif h == 'Situação' and v == 'Inativo':
                cell.font = Font(name=F, size=10, bold=True, color='B91C1C')
            elif h == 'Atividades' and v == 'sem ramo':
                cell.font = Font(name=F, size=10, italic=True, color='B91C1C')
        if r % 2 == 0:
            for i in range(1, nc + 1):
                if not (cols[i - 1][0] == 'Certificado'):
                    wc.cell(row=r, column=i).fill = fill('F7FBF8')
        r += 1
    ult = max(r - 1, HR)
    wc.auto_filter.ref = f'A{HR}:{get_column_letter(nc)}{ult}'
    wc.freeze_panes = wc.cell(row=HR + 1, column=3)
    wc.print_title_rows = f'{HR}:{HR}'
    for w_ in (ws, wc):
        w_.page_setup.orientation = 'landscape'
        w_.page_setup.fitToWidth = 1; w_.page_setup.fitToHeight = 0
        w_.sheet_properties.pageSetUpPr.fitToPage = True
    wb.active = 0
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
