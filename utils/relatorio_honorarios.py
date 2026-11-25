# -*- coding: utf-8 -*-
"""
Relatório de CONFERÊNCIA DA COBRANÇA DO MÊS — Honorários (25/11/2026), em PDF.

Anderson: "como eu conseguirei consultar o que estaremos cobrando do AUTO POSTO XINGU LTDA e de todas as outras
empresas? Precisamos ter um relatório que me auxilie nisso, porque senão, quando eu fizer a emissão dos honorários,
como será puxado ou conferido antes de enviar?... um relatório em PDF bem melhor do que temos em Cobranças, com logo
do banco e tudo mais".

O relatório parte do mesmo painel da tela (utils/honorarios_painel.painel): uma linha por empresa com os itens que
vão na cobrança (honorários do contrato, 13º, desconto mensal, lançamentos aprovados pela equipe), o banco do boleto,
se sai nota, o vencimento e o total. Lançamentos AGUARDANDO aprovação aparecem avisados (não somam). Os filtros são
os da tela: quadro aberto (a emitir, emitidas, pagas, em atraso, todos), banco, nota, grupo, 13º, lançamentos,
faixa de valor e a busca.
"""
import io
import os
import tempfile
from collections import OrderedDict
from decimal import Decimal
from xml.sax.saxutils import escape

FASES = OrderedDict([('emitir', 'A emitir'), ('cobradas', 'Emitidas, aguardando pagamento'), ('pagas', 'Pagas'),
                     ('atrasadas', 'Mensalidades em atraso'), ('contratos', 'Todos os contratos ativos')])
FAIXAS = [('ate500', 'até R$ 500', 0, 500), ('500a1000', 'R$ 500 a 1.000', 500, 1000),
          ('1000a2000', 'R$ 1.000 a 2.000', 1000, 2000), ('mais2000', 'acima de R$ 2.000', 2000, 10 ** 12)]
_PASTA_SVG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'images', 'bancos')
_PNG = {}


def _d(v):
    return Decimal(str(v or 0)).quantize(Decimal('0.01'))


def _brl(v):
    v = _d(v)
    s = f'{abs(v):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return ('- ' if v < 0 else '') + 'R$ ' + s


def _dbr(d):
    return d.strftime('%d/%m/%Y') if d else ''


def _faixa(v):
    v = float(v or 0)
    for k, _, a, b in FAIXAS:
        if a < v <= b:
            return k
    return 'ate500' if v <= 0 else 'mais2000'


# ---------------------------------------------------------------------------
# filtros (os mesmos da tela; a tela manda pela URL)
# ---------------------------------------------------------------------------
def ler_filtros(a):
    def lista(k):
        return [x for x in (a.get(k) or '').split('|') if x]
    fase = a.get('fase') or 'emitir'
    return {'fase': fase if fase in FASES else 'emitir', 'banco': lista('banco'), 'nota': lista('nota'), 'grupo': lista('grupo'),
            'dec': lista('dec'), 'lan': lista('lan'), 'valor': lista('valor'), 'q': (a.get('q') or '').strip()}


def _valor_tela(l):
    """O valor que o cartão da tela usa para a faixa: a cobrança a emitir (+ a parcela do 13º pendente)."""
    v = l['total'] if l['fase'] in ('emitir', 'erro') else Decimal('0')
    d = l.get('dec13')
    if d and d.get('pendente'):
        v += d['total']
    return v


def filtrar(P, f):
    """Devolve (linhas, textos dos filtros) — como a tela: quadro aberto e depois cada dimensão (OU dentro, E entre)."""
    fase = f['fase']
    if fase == 'emitir':
        linhas = [l for l in P['linhas'] if l.get('pendente')]
    elif fase == 'atrasadas':
        linhas = list(P['devendo'])
    elif fase == 'contratos':
        linhas = list(P['linhas'])
    else:
        linhas = [l for l in P['linhas'] if l.get('fase') == fase]
    textos = [FASES[fase]]
    nomes_b = {str(b['id']): b['nome'] for b in P['bancos']}

    def passa(l):
        if f['banco'] and str((l.get('banco') or {}).get('id', '')) not in f['banco']:
            return False
        if f['nota'] and ('1' if l.get('nota') else '0') not in f['nota']:
            return False
        if f['grupo'] and (l.get('grupo') or '(sem grupo)') not in f['grupo']:
            return False
        if f['dec'] and ('1' if l.get('decimo') else '0') not in f['dec']:
            return False
        if f['lan'] and ('pend' if l.get('pendentes') else ('1' if l.get('lancs') else '0')) not in f['lan']:
            return False
        if f['valor'] and _faixa(_valor_tela(l)) not in f['valor']:
            return False
        if f['q']:
            q = f['q'].lower(); qd = ''.join(ch for ch in q if ch.isdigit())
            bu = ' '.join(str(x or '') for x in (l.get('numero'), l.get('empresa'), l['contrato'].get('nome_fantasia'),
                                                   l['contrato'].get('cpf_cnpj'), l.get('grupo'))).lower()
            if q not in bu and not (len(qd) >= 4 and qd in ''.join(ch for ch in bu if ch.isdigit())):
                return False
        return True

    linhas = [l for l in linhas if passa(l)]
    if f['banco']:
        textos.append('Banco: ' + ', '.join(nomes_b.get(x, 'sem banco') for x in f['banco']))
    if f['nota']:
        textos.append('Nota: ' + ', '.join('nota + boleto' if x == '1' else 'só boleto' for x in f['nota']))
    if f['grupo']:
        textos.append('Grupo: ' + ', '.join(f['grupo']))
    if f['dec']:
        textos.append('13º: ' + ', '.join('com 13º' if x == '1' else 'sem 13º' for x in f['dec']))
    if f['lan']:
        textos.append('Lançamentos: ' + ', '.join({'pend': 'aguardando aprovação', '1': 'com lançamentos', '0': 'sem lançamentos'}.get(x, x) for x in f['lan']))
    if f['valor']:
        rot = {k: r for k, r, _, _ in FAIXAS}
        textos.append('Valor: ' + ', '.join(rot.get(x, x) for x in f['valor']))
    if f['q']:
        textos.append('Busca: "%s"' % f['q'])
    return linhas, textos


# ---------------------------------------------------------------------------
# logos dos bancos: SVG da pasta static → PNG (PyMuPDF), uma vez por processo
# ---------------------------------------------------------------------------
def _logo_png(cod):
    cod = str(cod or '').zfill(3)
    if cod in _PNG:
        return _PNG[cod]
    svg = os.path.join(_PASTA_SVG, cod + '.svg')
    out = None
    if os.path.exists(svg):
        try:
            import fitz
            doc = fitz.open(svg)
            pix = doc[0].get_pixmap(dpi=180, alpha=True)
            out = os.path.join(tempfile.gettempdir(), 'qc_banco_%s.png' % cod)
            pix.save(out)
        except Exception:
            out = None
    _PNG[cod] = out
    return out


def _img_logo(cod, mm_alt):
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image
    p = _logo_png(cod)
    if not p:
        return ''
    try:
        iw, ih = ImageReader(p).getSize()
        return Image(p, width=mm_alt * mm * iw / ih, height=mm_alt * mm)
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# o PDF
# ---------------------------------------------------------------------------
def gerar_pdf(ctx):
    """ctx: competencia, venc_mes, linhas, bancos, filtros, gerado_em, gerado_por, logo_path."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, KeepTogether, SimpleDocTemplate, Spacer, Table, TableStyle
    from utils.relatorio_clientes import CINZA, LINHA, VERDE, VERDE_ESC, ZEBRA, _CanvasNumerado, _p

    comp, venc_mes, linhas = ctx['competencia'], ctx['venc_mes'], ctx['linhas']
    ROXO, ROXO_ESC, ROXO_CL = colors.HexColor('#6D28D9'), colors.HexColor('#4C1D95'), colors.HexColor('#F5F3FF')
    AMBAR, AMBAR_CL = colors.HexColor('#B45309'), colors.HexColor('#FFFBEB')
    VERDE_CL, TEXTO = colors.HexColor('#F0FDF4'), colors.HexColor('#1E293B')
    titulo = 'Cobrança de %s' % comp.strftime('%m/%Y')
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=8 * mm, bottomMargin=13 * mm, leftMargin=9 * mm, rightMargin=9 * mm,
                            title=titulo, author='Qualicontax')
    L = doc.width
    el = []

    # ── Cabeçalho ──
    logo = ''
    if ctx.get('logo_path'):
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(ctx['logo_path']).getSize()
            logo = Image(ctx['logo_path'], width=15 * mm * iw / ih, height=15 * mm)
        except Exception:
            logo = ''
    chip = Table([[_p('CONFERÊNCIA ANTES DE EMITIR', 6.8, colors.white, bold=True, align=TA_CENTER)]], colWidths=[48 * mm], rowHeights=[5.2 * mm])
    chip.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), VERDE), ('ROUNDEDCORNERS', [3, 3, 3, 3]), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                              ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
    lt = Table([[_p(escape(titulo), 15, VERDE_ESC, bold=True, leading=18), chip]], colWidths=[L - 34 * mm - 52 * mm - 54 * mm, 54 * mm])
    lt.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    meio = [lt, _p('Honorários, 13º, descontos e lançamentos de cada empresa nesta competência · boletos vencem em <b>%s</b> · Qualicontax Assessoria Contábil'
                   % venc_mes.strftime('%m/%Y'), 8.3, CINZA)]
    meta = _p(f"Gerado em <b>{ctx['gerado_em']}</b>" + (f"<br/>por {escape(ctx['gerado_por'])}" if ctx.get('gerado_por') else ''), 7.8, CINZA, align=TA_RIGHT, leading=10.5)
    head = Table([[logo, meio, meta]], colWidths=[34 * mm, L - 34 * mm - 52 * mm, 52 * mm])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LINEBELOW', (0, 0), (-1, -1), 2, VERDE),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 7), ('LEFTPADDING', (0, 0), (0, 0), 0)]))
    el += [head, Spacer(1, 5)]

    filt = Table([[_p('<b>O QUE ENTROU</b>', 6.8, colors.HexColor('#15803D')),
                   _p('&nbsp;&nbsp;·&nbsp;&nbsp;'.join(escape(t) for t in ctx['filtros']), 7.8, colors.HexColor('#334155'), leading=10.5)]],
                 colWidths=[24 * mm, L - 24 * mm])
    filt.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), ZEBRA), ('BOX', (0, 0), (-1, -1), 0.6, LINHA), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                              ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5), ('ROUNDEDCORNERS', [5, 5, 5, 5])]))
    el += [filt, Spacer(1, 6)]

    # ── Somas ──
    def valor_linha(l):
        return _d(l['atual']['valor']) if (l.get('atual') and l['fase'] != 'emitir') else _d(l['total'])

    soma = {'hon': Decimal('0'), 'dec': Decimal('0'), 'lan': Decimal('0'), 'desc': Decimal('0'), 'pend': Decimal('0')}
    n_nota = 0
    por_banco = OrderedDict()
    for l in linhas:
        for i in l['itens']:
            v = _d(i['valor'])
            if i.get('lancamento_id') and v >= 0:
                soma['lan'] += v
            elif i.get('lancamento_id') or i['origem'] == 'desconto':
                soma['desc'] += v
            elif i['origem'] == 'honorarios':
                soma['hon'] += v
            elif i['origem'] == 'decimo':
                soma['dec'] += v
        d = l.get('dec13')
        if d and d.get('pendente'):
            soma['dec'] += _d(d['total'])
        soma['pend'] += _d(l.get('v_pend') or 0)
        n_nota += 1 if l.get('nota') else 0
        b = l.get('banco') or {}
        k = b.get('id') or 0
        if k not in por_banco:
            por_banco[k] = {'banco': b, 'n': 0, 'valor': Decimal('0'), 'nota': 0}
        por_banco[k]['n'] += 1
        por_banco[k]['valor'] += valor_linha(l) + (_d(d['total']) if (d and d.get('pendente')) else Decimal('0'))
        por_banco[k]['nota'] += 1 if l.get('nota') else 0
    total = sum((valor_linha(l) + (_d(l['dec13']['total']) if (l.get('dec13') and l['dec13'].get('pendente')) else Decimal('0')) for l in linhas), Decimal('0'))

    cards = [('Empresas', str(len(linhas)), '%d com nota · %d só boleto' % (n_nota, len(linhas) - n_nota), '#0F3D2E', '#F0FDF4'),
             ('Honorários', _brl(soma['hon']), 'dos contratos', '#15803D', '#ECFDF5'),
             ('13º', _brl(soma['dec']), 'parcelas deste mês', '#7C3AED', '#F5F3FF'),
             ('Lançamentos', _brl(soma['lan']), 'aprovados pela equipe', '#6D28D9', '#F5F3FF'),
             ('Descontos', _brl(soma['desc']), 'contrato e lançados', '#B91C1C', '#FEF2F2'),
             ('Total a cobrar', _brl(total), '%d boleto%s' % (len(linhas), 's' if len(linhas) != 1 else ''), '#0F3D2E', '#0F3D2E')]
    gap = 2.2 * mm
    cw = (L - gap * (len(cards) - 1)) / len(cards)
    cel = []
    for rot, v, sub, cor, fundo in cards:
        ult = rot.startswith('Total')
        t = Table([[_p(rot.upper(), 6.4, colors.white if ult else colors.HexColor(cor), bold=True)],
                   [_p(f'<b>{v}</b>', 10.2, colors.white if ult else colors.HexColor(cor), leading=12.2)],
                   [_p(sub, 6.3, colors.HexColor('#D1FAE5') if ult else CINZA)]], colWidths=[cw])
        t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(fundo)), ('LINEABOVE', (0, 0), (-1, 0), 2.2, colors.HexColor(cor)),
                               ('LEFTPADDING', (0, 0), (-1, -1), 6), ('TOPPADDING', (0, 0), (-1, 0), 4), ('TOPPADDING', (0, 1), (-1, -1), 1),
                               ('BOTTOMPADDING', (0, -1), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -2), 0)]))
        cel += [t, '']
    krow = Table([cel[:-1]], colWidths=sum([[cw, gap] for _ in cards], [])[:-1])
    krow.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    el += [krow, Spacer(1, 6)]

    # ── Por banco ──
    if por_banco:
        bl = []
        nb = len(por_banco)
        bw = (L - gap * (nb - 1)) / nb
        for k, x in por_banco.items():
            b = x['banco']
            lg = _img_logo(b.get('cod'), 6) if b else ''
            bl.append(Table([[lg, [_p('<b>%s</b>' % escape(b.get('nome') or 'Sem banco'), 8.6, TEXTO, leading=10),
                                   _p('%d empresa%s · %d com nota' % (x['n'], 's' if x['n'] != 1 else '', x['nota']), 6.6, CINZA, leading=8.5)],
                              _p('<b>%s</b>' % _brl(x['valor']), 9.6, VERDE_ESC, align=TA_RIGHT)]],
                            colWidths=[9 * mm, bw - 9 * mm - 30 * mm, 30 * mm]))
            bl[-1].setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (0, 0), 2), ('BOX', (0, 0), (-1, -1), 0.6, LINHA),
                                        ('ROUNDEDCORNERS', [5, 5, 5, 5]), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
        row = []
        for t in bl:
            row += [t, '']
        br = Table([row[:-1]], colWidths=sum([[bw, gap] for _ in bl], [])[:-1])
        br.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
        el += [_p('<b>POR BANCO DO BOLETO</b>', 6.8, colors.HexColor('#15803D')), Spacer(1, 2), br, Spacer(1, 7)]

    if not linhas:
        el += [Spacer(1, 20), _p('Nenhuma empresa com esses filtros.', 11, CINZA, align=TA_CENTER)]
        doc.build(el, canvasmaker=lambda *a, **k: _CanvasNumerado(*a, rodape='Qualicontax · ' + ctx['gerado_em'], **k))
        return buf.getvalue()

    # ── A lista: uma linha por empresa, com os itens dentro ──
    W = [9 * mm, 46 * mm, 60 * mm, 20 * mm, 15 * mm, 16 * mm, L - (9 + 46 + 60 + 20 + 15 + 16) * mm]
    hd = lambda t, a=0: _p(t, 6.8, colors.white, bold=True, align=a)
    cab = [hd('Nº'), hd('Empresa'), hd('O que vai na cobrança'), hd('Banco'), hd('Nota'), hd('Vence'), hd('Total', TA_RIGHT)]
    rows = [cab]
    st = [('BACKGROUND', (0, 0), (-1, 0), VERDE_ESC), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
          ('TOPPADDING', (0, 0), (-1, -1), 3.2), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.2), ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
          ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINHA), ('BOX', (0, 0), (-1, -1), 0.6, LINHA)]
    iw = W[2] - 8
    for n, l in enumerate(sorted(linhas, key=lambda x: (x.get('empresa') or '').lower()), start=1):
        c = l['contrato']
        emp = [_p('<b>%s</b>' % escape(l['empresa'] or ''), 8, TEXTO, leading=9.6)]
        sub = ' · '.join(x for x in (c.get('cpf_cnpj') or '', l.get('grupo') or '') if x)
        if sub:
            emp.append(_p(escape(sub), 6.4, CINZA, leading=8))
        if l.get('fase') == 'erro':
            emp.append(_p('<b>a cobrança não saiu</b> — emita de novo', 6.4, colors.HexColor('#B91C1C'), leading=8))
        elif l.get('fase') == 'pagas':
            emp.append(_p('paga', 6.4, colors.HexColor('#15803D'), leading=8))
        elif l.get('fase') == 'atrasadas' or (l.get('atrasos') and l.get('fase') != 'pagas'):
            dev = sum((_d(x['valor']) - _d(x['pago']) for x in l.get('atrasos') or []), Decimal('0'))
            emp.append(_p('<b>deve %s</b> de %s' % (_brl(dev), ', '.join(x['mes'] for x in l.get('atrasos') or [])), 6.4, colors.HexColor('#B91C1C'), leading=8))
        elif l.get('fase') == 'cobradas':
            emp.append(_p('emitida · aguardando pagamento', 6.4, colors.HexColor('#1D4ED8'), leading=8))
        # itens
        it_rows, it_st = [], [('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0.8),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 0.8), ('VALIGN', (0, 0), (-1, -1), 'TOP')]
        for i in l['itens']:
            v = _d(i['valor'])
            if i.get('lancamento_id'):
                cor, nome = (colors.HexColor('#15803D') if v < 0 else ROXO_ESC), '<b>%s</b>' % escape(i['nome'] or '')
                nome += ' <font size="6" color="#7C3AED">%s%s</font>' % ('desconto lançado' if v < 0 else 'lançamento da equipe', (' · pedido por ' + escape((i.get('por') or '').split(' ')[0])) if i.get('por') else '')
                it_rows.append([_p(nome, 7.2, cor, leading=8.8), _p('<b>%s</b>' % _brl(v), 7.2, cor, align=TA_RIGHT)])
                it_st.append(('BACKGROUND', (0, len(it_rows) - 1), (-1, len(it_rows) - 1), ROXO_CL))
            elif i['origem'] == 'decimo':
                it_rows.append([_p(escape(i['nome'] or '13º honorário'), 7.2, colors.HexColor('#6D28D9'), leading=8.8), _p(_brl(v), 7.2, colors.HexColor('#6D28D9'), align=TA_RIGHT)])
            elif i['origem'] == 'desconto':
                it_rows.append([_p(escape(i['nome'] or ''), 7.2, colors.HexColor('#15803D'), leading=8.8), _p(_brl(v), 7.2, colors.HexColor('#15803D'), align=TA_RIGHT)])
            else:
                it_rows.append([_p(escape(i['nome'] or ''), 7.2, TEXTO, leading=8.8), _p(_brl(v), 7.2, TEXTO, align=TA_RIGHT)])
        d = l.get('dec13')
        if d and d.get('pendente'):
            it_rows.append([_p('%s <font size="6" color="#64748B">boleto próprio · vence %s</font>' % (escape(d['itens'][0]['nome']), _dbr(d['vencimento'])), 7.2, colors.HexColor('#6D28D9'), leading=8.8),
                            _p(_brl(d['total']), 7.2, colors.HexColor('#6D28D9'), align=TA_RIGHT)])
        for p in l.get('pendentes') or []:
            it_rows.append([_p('<b>%s</b> <font size="6" color="#D97706">aguardando aprovação%s · não entra até aprovar</font>' % (escape(p['nome'] or ''), (' · pedido por ' + escape((p.get('por') or '').split(' ')[0])) if p.get('por') else ''), 7.2, AMBAR, leading=8.8),
                            _p('(%s)' % _brl(p['valor']), 7.2, AMBAR, align=TA_RIGHT)])
            it_st.append(('BACKGROUND', (0, len(it_rows) - 1), (-1, len(it_rows) - 1), AMBAR_CL))
        itens_t = Table(it_rows, colWidths=[iw - 22 * mm, 22 * mm])
        itens_t.setStyle(TableStyle(it_st))
        b = l.get('banco') or {}
        lg = _img_logo(b.get('cod'), 4.2) if b else ''
        banco_t = Table([[lg, _p(escape(b.get('nome') or 'sem banco') + (' <font size="5.5" color="#64748B">só %s</font>' % comp.strftime('%m/%Y') if l.get('banco_mes') else ''), 7, TEXTO, leading=8.6)]],
                        colWidths=[6 * mm, W[3] - 8 - 6 * mm])
        banco_t.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0),
                                     ('BOTTOMPADDING', (0, 0), (-1, -1), 0), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
        nota = _p('nota + boleto' if l.get('nota') else 'só boleto', 6.8, colors.HexColor('#15803D') if l.get('nota') else CINZA, leading=8.4)
        venc = (l['atual']['venc'] if (l.get('atual') and l['fase'] != 'emitir') else l.get('vencimento'))
        tot = valor_linha(l) + (_d(d['total']) if (d and d.get('pendente')) else Decimal('0'))
        rows.append([_p(str(l.get('numero') or '—'), 7.4, CINZA, bold=True), emp, itens_t, banco_t, nota, _p(_dbr(venc), 7.2, TEXTO), _p('<b>%s</b>' % _brl(tot), 8, VERDE_ESC, align=TA_RIGHT)])
        if n % 2 == 0:
            st.append(('BACKGROUND', (0, n), (-1, n), ZEBRA))
    rows.append(['', _p('<b>TOTAL · %d empresa%s</b>' % (len(linhas), 's' if len(linhas) != 1 else ''), 8, VERDE_ESC), '', '', '', '', _p('<b>%s</b>' % _brl(total), 9, VERDE_ESC, align=TA_RIGHT)])
    st += [('BACKGROUND', (0, -1), (-1, -1), VERDE_CL), ('LINEABOVE', (0, -1), (-1, -1), 0.9, VERDE), ('SPAN', (1, -1), (5, -1))]
    t = Table(rows, colWidths=W, repeatRows=1)
    t.setStyle(TableStyle(st))
    el.append(t)

    # ── Aguardando aprovação ──
    pend = [(l, p) for l in linhas for p in (l.get('pendentes') or [])]
    if pend:
        el += [Spacer(1, 9), _p('<b>LANÇAMENTOS AGUARDANDO APROVAÇÃO</b> — não entram na cobrança até serem aprovados em Financeiro › Lançamentos', 6.8, AMBAR), Spacer(1, 2)]
        pr = [[hd('Nº'), hd('Empresa'), hd('Lançamento'), hd('Pedido por'), hd('Valor', TA_RIGHT)]]
        for l, p in pend:
            pr.append([_p(str(l.get('numero') or '—'), 7.4, CINZA, bold=True), _p(escape(l['empresa'] or ''), 7.4, TEXTO), _p(escape(p['nome'] or ''), 7.4, TEXTO),
                       _p(escape(p.get('por') or ''), 7.4, TEXTO), _p(_brl(p['valor']), 7.4, AMBAR, align=TA_RIGHT)])
        pr.append(['', _p('<b>TOTAL</b>', 7.6, AMBAR), '', '', _p('<b>%s</b>' % _brl(soma['pend']), 8, AMBAR, align=TA_RIGHT)])
        pt = Table(pr, colWidths=[9 * mm, 62 * mm, L - (9 + 62 + 40 + 24) * mm, 40 * mm, 24 * mm], repeatRows=1)
        pt.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), AMBAR), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 2.6),
                                ('BOTTOMPADDING', (0, 0), (-1, -1), 2.6), ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                                ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINHA), ('BOX', (0, 0), (-1, -1), 0.6, LINHA),
                                ('BACKGROUND', (0, -1), (-1, -1), AMBAR_CL), ('SPAN', (1, -1), (3, -1))]))
        el.append(KeepTogether([pt]))

    doc.build(el, canvasmaker=lambda *a, **k: _CanvasNumerado(*a, rodape='Qualicontax · ' + ctx['gerado_em'], **k))
    return buf.getvalue()
