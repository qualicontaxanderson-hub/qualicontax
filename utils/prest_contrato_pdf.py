# -*- coding: utf-8 -*-
"""PDF do contrato de prestação de serviços (ReportLab). Anderson (21/11/2026): "os modelos de Orçamento e Prestação têm que
ser bem bonitos e elegantes para impressionar"; 23/11: o contrato no mesmo capricho do orçamento.

Folha de rosto no molde do orçamento: faixa verde-escura com o número e os honorários por mês, três quadros (CONTRATANTE ·
QUEM ASSINA · PELA QUALICONTAX), o resumo em oito caixinhas (honorários, vencimento, início, reajuste, 13º, aviso prévio,
atraso, foro), os serviços por área e "como assinar" em três passos (ou o bloco "assinado eletronicamente"). Depois vem o
contrato em si (partes, cláusulas, fecho), as assinaturas em cartões e, quando todos assinam, a página "Registro de
assinaturas". Bytes determinísticos (invariant): o mesmo contrato gera sempre o mesmo PDF, e é esse hash que cada pessoa aceita."""
import hashlib
import io
import os
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from utils.texto import nome_proprio

from models.prest_contrato import QUALICONTAX, brl

_VERDE = colors.HexColor('#0f3d2e')
_VERDE2 = colors.HexColor('#15803d')
_VERDE3 = colors.HexColor('#22c55e')
_TINTA = colors.HexColor('#0f172a')
_CINZA = colors.HexColor('#64748b')
_LINHA = colors.HexColor('#cfe5d8')
_LINHA2 = colors.HexColor('#e2e8f0')
_FUNDO = colors.HexColor('#f6faf7')
_FUNDO2 = colors.HexColor('#ecfdf3')
_AMBAR = colors.HexColor('#b45309')
_CLARO = colors.HexColor('#bbf7d0')
_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'images', 'logo.png')
W, H = A4
ML = MR = 2.0 * cm
LW = W - ML - MR
PAPEL = {'CONTRATANTE': 'Contratante', 'CONTRATADA': 'Contratada', 'TESTEMUNHA': 'Testemunha'}


def _estilos():
    base = ParagraphStyle('base', fontName='Helvetica', fontSize=9.6, leading=13.4, alignment=TA_JUSTIFY, textColor=_TINTA)
    esq = ParagraphStyle('esq', parent=base, alignment=TA_LEFT)
    return {
        'base': base,
        'titulo': ParagraphStyle('titulo', parent=base, fontName='Helvetica-Bold', fontSize=13.5, leading=17, alignment=TA_CENTER, textColor=colors.white),
        'parte_n': ParagraphStyle('parte_n', parent=base, fontName='Helvetica-Bold', fontSize=8.5, textColor=_VERDE2, spaceBefore=4),
        'clausula': ParagraphStyle('clausula', parent=base, spaceBefore=5),
        'item': ParagraphStyle('item', parent=base, leftIndent=16, spaceBefore=1),
        'centro': ParagraphStyle('centro', parent=base, alignment=TA_CENTER),
        # folha de rosto
        'faixa_k': ParagraphStyle('fk', parent=esq, fontName='Helvetica-Bold', fontSize=7.6, leading=10, textColor=_CLARO),
        'faixa_t': ParagraphStyle('ft', parent=esq, fontName='Helvetica-Bold', fontSize=19, leading=22, textColor=colors.white),
        'faixa_rk': ParagraphStyle('frk', parent=esq, fontName='Helvetica-Bold', fontSize=7.6, leading=10, textColor=_CLARO, alignment=TA_RIGHT),
        'faixa_rv': ParagraphStyle('frv', parent=esq, fontName='Helvetica-Bold', fontSize=16, leading=19, textColor=colors.white, alignment=TA_RIGHT),
        'faixa_r': ParagraphStyle('fr', parent=esq, fontSize=8.6, leading=11, textColor=colors.white, alignment=TA_RIGHT),
        'k': ParagraphStyle('k', parent=esq, fontName='Helvetica-Bold', fontSize=7.2, leading=9.5, textColor=_VERDE2),
        'nome': ParagraphStyle('n', parent=esq, fontName='Helvetica-Bold', fontSize=10.2, leading=12.6),
        'mi': ParagraphStyle('mi', parent=esq, fontSize=8.2, leading=11, textColor=_CINZA),
        'sec': ParagraphStyle('sec', parent=esq, fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=_VERDE, spaceBefore=10, spaceAfter=4),
        'kpi_k': ParagraphStyle('kk', parent=esq, fontName='Helvetica-Bold', fontSize=6.8, leading=9, textColor=_VERDE2),
        'kpi_v': ParagraphStyle('kv', parent=esq, fontName='Helvetica-Bold', fontSize=10.4, leading=13),
        'kpi_s': ParagraphStyle('ks', parent=esq, fontSize=7.6, leading=10, textColor=_CINZA),
        'sv_a': ParagraphStyle('sva', parent=esq, fontName='Helvetica-Bold', fontSize=9, leading=12),
        'sv_i': ParagraphStyle('svi', parent=esq, fontSize=8.2, leading=11, textColor=_CINZA),
        'passo_n': ParagraphStyle('pn', parent=esq, fontName='Helvetica-Bold', fontSize=13, textColor=_VERDE2, alignment=TA_CENTER),
        'passo_t': ParagraphStyle('pt', parent=esq, fontName='Helvetica-Bold', fontSize=9, leading=12),
        'passo_s': ParagraphStyle('ps', parent=esq, fontSize=8.2, leading=11, textColor=_CINZA),
        'ace_t': ParagraphStyle('at', parent=esq, fontName='Helvetica-Bold', fontSize=10.5, leading=13, textColor=_VERDE),
        'ace': ParagraphStyle('ac', parent=esq, fontSize=8.8, leading=12),
        # cartões de assinatura e registro
        'card_st_ok': ParagraphStyle('cok', parent=esq, fontName='Helvetica-Bold', fontSize=7.2, leading=9.5, textColor=_VERDE2),
        'card_st_wait': ParagraphStyle('cwt', parent=esq, fontName='Helvetica-Bold', fontSize=7.2, leading=9.5, textColor=_AMBAR),
        'card_nome': ParagraphStyle('cn', parent=esq, fontName='Helvetica-Bold', fontSize=9.6, leading=12.5),
        'card_sub': ParagraphStyle('cs', parent=esq, fontSize=7.9, leading=10.5, textColor=_CINZA),
        'reg_t': ParagraphStyle('reg_t', parent=esq, fontName='Helvetica-Bold', fontSize=13, textColor=_VERDE, spaceAfter=4),
        'reg': ParagraphStyle('reg', parent=esq, fontSize=8.6, leading=11.8),
        'mono': ParagraphStyle('mono', parent=esq, fontName='Courier', fontSize=7.6, leading=10),
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
    for i, linha in enumerate(getattr(doc, 'cabecalho', None) or QUALICONTAX['cabecalho']):
        canvas.drawRightString(W - MR, topo - 0.32 * cm - i * 9.2, linha)
    canvas.setStrokeColor(_VERDE3); canvas.setLineWidth(1.4)
    canvas.line(ML, topo - 1.65 * cm, W - MR, topo - 1.65 * cm)
    # rodapé
    canvas.setStrokeColor(_LINHA2); canvas.setLineWidth(0.5)
    canvas.line(ML, 1.45 * cm, W - MR, 1.45 * cm)
    canvas.setFont('Helvetica', 7); canvas.setFillColor(_CINZA)
    canvas.drawRightString(W - MR, 1.0 * cm, 'página %d' % doc.page)
    if getattr(doc, 'rodape', None):
        canvas.drawString(ML, 1.0 * cm, doc.rodape)
    canvas.restoreState()


def _esc(s):
    return (str(s or '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _doc(d):
    d = ''.join(ch for ch in str(d or '') if ch.isdigit())
    if len(d) == 14:
        return 'CNPJ %s.%s.%s/%s-%s' % (d[:2], d[2:5], d[5:8], d[8:12], d[12:])
    if len(d) == 11:
        return 'CPF %s.%s.%s-%s' % (d[:3], d[3:6], d[6:9], d[9:])
    return str(d or '')


def _rico(s, apelido=None, parte=False):
    """Dá vida ao texto: nomes das partes, valores, datas, prazos e percentuais em negrito (Anderson, 06/11:
    "está seco, nomes em negrito, dar vida ao contrato")."""
    t = _esc(s)
    if parte:                                                       # razão social até a primeira vírgula
        t = re.sub(r'^([^,]+),', r'<b>\1</b>,', t, count=1)
    for pat in (r'(CNPJ nº [\d./-]+)', r'(CPF nº [\d.-]+)', r'(CRC nº [\w./-]+)', r'(R\$ [\d.]+,\d{2}(?: \([^)]+\))?)',
                r'(\d{2}/\d{2}/\d{4})', r'(\d+%? \([a-zç ]+\)(?: (?:dias|meses|testemunhas?|honorário mensal))?)', r'(dia \d+ \([a-z]+\))'):
        t = re.sub(pat, r'<b>\1</b>', t)
    t = re.sub(r'(?<![\w>])(QUALICONTAX)(?![\w<])', r'<b>\1</b>', t)
    if apelido:
        t = re.sub(r'(?<![\w>])(%s)(?![\w<])' % re.escape(_esc(apelido)), r'<b>\1</b>', t)
    t = re.sub(r'(neste ato representad[ao]s? por seu [^,]+, )([^,]+), portador', r'\1<b>\2</b>, portador', t)
    return t


def _faixa_titulo(texto, st):
    t = Table([[Paragraph(_esc(texto), st['titulo'])]], colWidths=[LW])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), _VERDE), ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
                           ('ROUNDEDCORNERS', [6, 6, 6, 6])]))
    return t


def _caixa(fundo, borda=None, pad=10):
    est = [('BACKGROUND', (0, 0), (-1, -1), fundo), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
           ('LEFTPADDING', (0, 0), (-1, -1), pad), ('RIGHTPADDING', (0, 0), (-1, -1), pad), ('TOPPADDING', (0, 0), (-1, -1), pad), ('BOTTOMPADDING', (0, 0), (-1, -1), pad)]
    if borda:
        est.append(('BOX', (0, 0), (-1, -1), 0.6, borda))
    return TableStyle(est)


def _pct(s):
    """'2,00' → '2'; '1,50' → '1,5'."""
    s = str(s or '').replace('.', ',')
    return s.rstrip('0').rstrip(',') if ',' in s else s


def _item(i):
    """'Emissão de balancetes; e' → 'Emissão de balancetes'."""
    return re.sub(r'(;\s*e|;|\.)\s*$', '', str(i or '').strip())


def _nome_curto(razao):
    """"QUALICONTAX ASSESSORIA CONTABIL LTDA – EPP" → "Qualicontax Assessoria Contabil Ltda"."""
    from utils.texto import nome_proprio
    return nome_proprio(re.split(r'\s[–-]\s', str(razao or ''))[0])


# ───────────────────────── folha de rosto ─────────────────────────
def _capa(m, registro, numero, st):
    f = m['ficha']; emps = m.get('empresas') or []; qc = m.get('contratada') or {}
    el = []
    # faixa: número à esquerda, honorários à direita
    quando = max(a['assinado_em'] for a in registro) if registro else None
    dir_ = [Paragraph('HONORÁRIOS MENSAIS', st['faixa_rk']), Paragraph(_esc(brl(f['valor'])), st['faixa_rv']),
            Paragraph(('assinado eletronicamente em %s' % quando.strftime('%d/%m/%Y')) if quando else
                      ('vence dia %d · início %s' % (f['dia'], f['inicio'].strftime('%d/%m/%Y'))), st['faixa_r'])]
    esq = [Paragraph(_esc(m['titulo']), st['faixa_k']), Paragraph(('Contrato nº %s' % _esc(numero)) if numero else 'Contrato', st['faixa_t'])]
    faixa = Table([[esq, dir_]], colWidths=[LW * .58, LW * .42])
    faixa.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), _VERDE), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 14), ('RIGHTPADDING', (0, 0), (-1, -1), 14), ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12)]))
    el += [faixa, Spacer(1, 8)]
    # três quadros
    q1 = [Paragraph('CONTRATANTE' + (' · GRUPO DE %d EMPRESAS' % len(emps) if len(emps) > 1 else ''), st['k'])]
    for e in emps[:4]:
        q1.append(Paragraph(_esc(e.get('razao')), st['nome']))
        sit = e.get('situacao') or 'PAGA'
        tag = {'VIA_GRUPO': ' · via grupo, sem cobrança', 'SEM_MOVIMENTO': ' · sem movimento, sem cobrança'}.get(sit, '')
        if sit == 'PAGA' and len(emps) > 1:
            tag = ' · paga %s' % _esc(brl(e.get('valor') if f['valores_por_empresa'] and e.get('valor') else f['valor']))
        mi = _doc(e.get('cnpj')) + tag
        q1.append(Paragraph(mi, st['mi']))
    if len(emps) > 4:
        q1.append(Paragraph('e mais %d empresa(s), listadas no contrato' % (len(emps) - 4), st['mi']))
    rep = f['representante']
    q2 = [Paragraph('QUEM ASSINA PELA CONTRATANTE', st['k']), Paragraph(_esc(_pessoa(rep['nome'])), st['nome']),
          Paragraph(_esc(' · '.join(x for x in (rep.get('cargo'), ('CPF ' + rep['cpf']) if rep.get('cpf') else '') if x)), st['mi'])]
    if f.get('extra'):
        ex = f['extra']
        q2 += [Spacer(1, 4), Paragraph(_esc(_pessoa(ex['nome'])), st['nome']),
               Paragraph(_esc(' · '.join(x for x in (ex.get('cargo') or 'representante autorizado(a)', ('CPF ' + ex['cpf']) if ex.get('cpf') else '') if x)), st['mi'])]
    q = f['assina']
    q3 = [Paragraph('PELA QUALICONTAX', st['k']), Paragraph(_esc(_pessoa(q.get('nome'))), st['nome']), Paragraph(_esc(q.get('cargo') or ''), st['mi']),
          Paragraph(_esc(_nome_curto(qc.get('razao'))) + ((' · CRC %s' % _esc(qc['crc'])) if qc.get('crc') else ''), st['mi'])]
    t3 = Table([[q1, q2, q3]], colWidths=[LW * .40, LW * .32, LW * .28])
    t3.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), _FUNDO), ('BOX', (0, 0), (-1, -1), 0.6, _LINHA2), ('LINEBEFORE', (1, 0), (-1, -1), 0.6, _LINHA2),
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 11), ('RIGHTPADDING', (0, 0), (-1, -1), 11), ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9)]))
    el += [t3]
    # resumo em caixinhas
    def kpi(k, v, s=''):
        return [Paragraph(k, st['kpi_k']), Paragraph(_esc(v), st['kpi_v']), Paragraph(_esc(s), st['kpi_s'])]
    linhas = [[kpi('HONORÁRIOS', brl(f['valor']), 'por mês' + ((' · soma das %d pagantes' % len(f.get('pagantes') or emps)) if f['valores_por_empresa'] else '')),
               kpi('VENCIMENTO', 'dia %d' % f['dia'], 'de cada mês'),
               kpi('INÍCIO', f['inicio'].strftime('%d/%m/%Y'), 'por tempo indeterminado'),
               kpi('REAJUSTE', f['reajuste'][0], f['reajuste'][1])],
              [kpi('13º HONORÁRIO', f['decimo'][0], f['decimo'][1]),
               kpi('AVISO PRÉVIO', '%d dias' % f['aviso'], 'por escrito'),
               kpi('ATRASO', 'multa de %s%%' % _pct(f['multa']), 'juros de %s%% ao mês ou fração' % _pct(f['juros'])),
               kpi('FORO', f['foro'], '')]]
    tk = Table(linhas, colWidths=[LW / 4] * 4)
    tk.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, _LINHA2), ('INNERGRID', (0, 0), (-1, -1), 0.4, _LINHA2), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))
    el += [Paragraph('RESUMO DO CONTRATO', st['sec']), tk]
    # serviços por área
    dados = []
    for nome, itens in f['areas']:
        dados.append([Paragraph(_esc(nome.capitalize()), st['sv_a']), Paragraph(_esc('; '.join(_item(i) for i in itens) + '.'), st['sv_i'])])
    if f['extras']:
        dados.append([Paragraph('Serviços adicionais', st['sv_a']), Paragraph(_esc('; '.join(x.capitalize() if x.isupper() else x for x in f['extras']) + '.'), st['sv_i'])])
    if dados:
        ts = Table(dados, colWidths=[LW * .26, LW * .74])
        est = [('LINEBELOW', (0, 0), (-1, -2), 0.4, _LINHA2), ('BOX', (0, 0), (-1, -1), 0.6, _LINHA2), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
               ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10), ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]
        for r in range(len(dados)):
            if r % 2 == 1:
                est.append(('BACKGROUND', (0, r), (-1, r), _FUNDO))
        ts.setStyle(TableStyle(est))
        el += [Paragraph('SERVIÇOS CONTRATADOS', st['sec']), ts]
    # como assinar / assinado
    if registro:
        linhas = [Paragraph('✔ Contrato assinado eletronicamente', st['ace_t'])]
        for a in registro:
            linhas.append(Paragraph('<b>%s</b> · %s · em <b>%s</b>' % (_esc(_pessoa(a.get('assinado_nome') or a.get('nome'))), PAPEL.get(a['papel'], a['papel']).lower(),
                                                                       a['assinado_em'].strftime('%d/%m/%Y às %H:%M')), st['ace']))
        linhas.append(Paragraph('Identidade confirmada por código enviado ao e-mail de cada pessoa · registro completo na última página', st['mi']))
        bloco = Table([[linhas]], colWidths=[LW]); bloco.setStyle(_caixa(_FUNDO2, _VERDE3, pad=12))
        el += [Spacer(1, 12), bloco]
    else:
        passos = [('1', 'Link', 'Cada pessoa que assina recebe o seu link por e-mail (a equipe também pode mandar pelo WhatsApp).'),
                  ('2', 'Código', 'Abre o contrato, confere, e confirma com o código que chega no e-mail. Fica registrado com data, hora e IP.'),
                  ('3', 'Via assinada', 'Quando todos assinam, a via final com o registro das assinaturas volta por e-mail para cada um.')]
        cel = [[Paragraph(n, st['passo_n']), [Paragraph(tit, st['passo_t']), Paragraph(txt, st['passo_s'])]] for n, tit, txt in passos]
        linha = Table([[Table([c], colWidths=[0.8 * cm, LW / 3 - 0.8 * cm - 8], style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 4)])) for c in cel]],
                      colWidths=[LW / 3] * 3)
        linha.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
        el += [Paragraph('COMO ASSINAR', st['sec']), linha]
    el.append(PageBreak())
    return el


# ───────────────────────── assinaturas ─────────────────────────
def _pessoa(nome):
    """Nome de pessoa sempre em maiúsculas e minúsculas (25/11/2026 — Anderson: "continuou os nomes diferentes"): o cadastro
    guarda FERNANDA COSTA BORBA e os usuários Albert Antunes Vieira; no PDF saem iguais. Empresas continuam em maiúsculas."""
    return nome_proprio(nome or '')


def _linhas_empresas_assina(a, st):
    """Rede (25/11/2026): uma linha por empresa pela qual a pessoa assina; avisa quando ela não consta no quadro de sócios."""
    out = []
    for e in a.get('empresas_assina') or []:
        txt = '%s · %s' % (_esc(e.get('razao')), _esc(e.get('cnpj') or ''))
        if not e.get('socio'):
            txt += ' · <font color="#b45309"><b>não consta no quadro de sócios</b></font>'
        out.append(Paragraph('assina por ' + txt, st['card_sub']))
    return out


def _cartao_assinatura(a, feito, st, razao_contratada=None):
    """Um assinante em cartão: situação, nome, CPF, papel/empresa e data."""
    papel = {'CONTRATANTE': ('Contratante' if a.get('empresas_assina') else (a.get('empresa') or 'Contratante')), 'CONTRATADA': _nome_curto(razao_contratada or QUALICONTAX['razao']), 'TESTEMUNHA': 'Testemunha'}.get(a['papel'], '')
    linhas = [Paragraph('✔ ASSINADO ELETRONICAMENTE' if feito else 'ASSINATURA ELETRÔNICA', st['card_st_ok'] if feito else st['card_st_wait']), Spacer(1, 3),
              Paragraph(_esc(_pessoa(feito.get('assinado_nome') if feito else a.get('nome') or '')), st['card_nome'])]
    sub = ' · '.join(x for x in (('CPF ' + (a.get('cpf') or '')) if a.get('cpf') else '', papel) if x)
    linhas.append(Paragraph(_esc(sub), st['card_sub']))
    linhas += _linhas_empresas_assina(a, st)
    linhas.append(Paragraph(feito['assinado_em'].strftime('%d/%m/%Y às %H:%M') if feito else 'aguardando', st['card_sub']))
    t = Table([[linhas]], colWidths=[LW / 2 - 6])
    t.setStyle(_caixa(_FUNDO2 if feito else _FUNDO, _VERDE3 if feito else _LINHA2, pad=10))
    return t


def gerar_pdf(montado, registro=None, rodape=None, numero=None):
    """``montado`` = PrestContrato.montar(dados). ``registro`` = assinaturas feitas (página final). Devolve bytes."""
    st = _estilos()
    buf = io.BytesIO()
    qc = montado.get('contratada') or {}
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=3.1 * cm, bottomMargin=2.1 * cm,
                            title=montado['titulo'] + ((' nº %s' % numero) if numero else ''), author=_nome_curto(qc.get('razao') or QUALICONTAX['razao']), invariant=1)
    doc.rodape = ' · '.join(x for x in (rodape, _nome_curto(qc.get('razao') or QUALICONTAX['razao']), 'CNPJ %s' % (qc.get('cnpj') or QUALICONTAX['cnpj'])) if x)
    doc.cabecalho = montado.get('cabecalho')
    ap = montado.get('apelido')
    el = []
    if montado.get('ficha'):
        el += _capa(montado, registro, numero, st)
    el.append(_faixa_titulo(montado['titulo'], st))
    if numero:
        el.append(Spacer(1, 4)); el.append(Paragraph('Contrato nº <b>%s</b>' % _esc(numero), st['centro']))
    el.append(Spacer(1, 10))
    for i, p in enumerate(montado['partes'], 1):
        el.append(Paragraph('%d · %s' % (i, 'CONTRATANTE' if i == 1 else 'CONTRATADA'), st['parte_n']))
        el.append(Paragraph(_rico(p, ap, parte=True), st['base']))
    el.append(Spacer(1, 8)); el.append(Paragraph(_rico(montado['preambulo'], ap), st['base'])); el.append(Spacer(1, 2))
    for titulo, paras in montado['clausulas']:
        primeiro = True
        for p in paras:
            if primeiro:
                el.append(Paragraph('<font color="#0f3d2e"><b>%s</b></font> %s' % (_esc(titulo), _rico(p, ap)), st['clausula'])); primeiro = False
            elif p[:1].isdigit():
                cabeca = bool(re.match(r'^\d+\. [A-ZÀ-Ú ]+$', p))          # "1. CONTABILIDADE" vira título da área
                el.append(Paragraph(('<font color="#15803d"><b>%s</b></font>' % _esc(p)) if cabeca else _rico(p, ap), st['item']))
            elif p.startswith('PARÁGRAFO'):
                pt, _, resto = p.partition('. ')
                el.append(Paragraph('<b>%s.</b> %s' % (_esc(pt), _rico(resto, ap)), st['clausula']))
            else:
                el.append(Paragraph(_rico(p, ap), st['clausula']))
    el.append(Spacer(1, 10)); el.append(Paragraph(_rico(montado['fecho'], ap), st['base']))
    # local/data + ASSINATURAS + todos os cartões ficam JUNTOS na mesma página (testemunha sozinha numa folha é feio)
    bloco_final = [Spacer(1, 14), Paragraph('<b>%s</b>' % _esc(montado['local_data']), st['centro']), Spacer(1, 12),
                   Table([['']], colWidths=[LW], style=[('LINEBELOW', (0, 0), (-1, -1), 1.0, _VERDE2)]),
                   Paragraph('ASSINATURAS', st['parte_n']), Spacer(1, 8)]
    ass = montado.get('assinantes') or []
    feitas = {(a['papel'], int(a['ordem'])): a for a in (registro or [])}
    cartoes = [_cartao_assinatura(a, feitas.get((a['papel'], int(a.get('ordem') or 1))), st, qc.get('razao')) for a in ass]
    for i in range(0, len(cartoes), 2):
        par = cartoes[i:i + 2]
        t = Table([[par[0], par[1] if len(par) > 1 else '']], colWidths=[LW / 2] * 2)
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                               ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
        bloco_final.append(t)
    el.append(KeepTogether(bloco_final))
    if registro:
        el.append(PageBreak())
        el.append(_faixa_titulo('REGISTRO DE ASSINATURAS ELETRÔNICAS', st)); el.append(Spacer(1, 10))
        el.append(Paragraph(_esc('Este registro integra o %s%s e comprova as assinaturas feitas pelo sistema da Qualicontax (app.qualicontax.com.br), com a identidade '
                                 'de cada pessoa confirmada por código enviado ao seu e-mail. A impressão digital SHA-256 abaixo é a do documento que cada uma viu e aceitou.'
                                 % (montado['titulo'].lower(), (' nº %s' % numero) if numero else '')), st['reg']))
        el.append(Spacer(1, 8))
        cards = []
        for a in registro:
            linhas = [Paragraph(' · '.join(x for x in (PAPEL.get(a['papel'], a['papel']).upper(), _esc(a.get('empresa') or (_nome_curto(qc.get('razao')) if a['papel'] == 'CONTRATADA' else ''))) if x), st['card_st_ok']),
                      Spacer(1, 3), Paragraph(_esc(_pessoa(a.get('assinado_nome') or a.get('nome'))), st['card_nome']),
                      Paragraph(_esc(' · '.join(x for x in (('CPF ' + (a.get('assinado_cpf') or a.get('cpf') or '')) if (a.get('assinado_cpf') or a.get('cpf')) else '', a.get('email') or '') if x)), st['card_sub'])]
            linhas += _linhas_empresas_assina(a, st)
            linhas.append(Paragraph(_esc('%s · IP %s' % (a['assinado_em'].strftime('%d/%m/%Y às %H:%M:%S'), a.get('assinado_ip') or '')), st['card_sub']))
            if a.get('assinado_agente'):
                linhas.append(Paragraph(_esc(a['assinado_agente'][:110]), st['card_sub']))
            t = Table([[linhas]], colWidths=[LW / 2 - 6]); t.setStyle(_caixa(_FUNDO2, _VERDE3, pad=10)); cards.append(t)
        for i in range(0, len(cards), 2):
            par = cards[i:i + 2]
            t = Table([[par[0], par[1] if len(par) > 1 else '']], colWidths=[LW / 2] * 2)
            t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                                   ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
            el.append(t)
        el.append(Spacer(1, 10))
        sha = Table([[[Paragraph('IMPRESSÃO DIGITAL (SHA-256) DO DOCUMENTO ACEITO', st['k']), Paragraph(_esc(registro[0].get('assinado_hash') or ''), st['mono'])]]], colWidths=[LW])
        sha.setStyle(_caixa(_FUNDO, _LINHA2, pad=10)); el.append(sha)
        el.append(Spacer(1, 8))
        quando = max(a['assinado_em'] for a in registro)
        el.append(Paragraph(_esc('Registro consolidado em %s, quando a última pessoa assinou. Fundamento: art. 10, § 2º, da MP nº 2.200-2/2001 e art. 4º da '
                                 'Lei nº 14.063/2020 (assinatura eletrônica). A impressão digital é calculada sobre o PDF exatamente como foi apresentado a cada pessoa; '
                                 'qualquer alteração posterior produz outra impressão.' % quando.strftime('%d/%m/%Y %H:%M:%S')), st['reg']))
    doc.build(el, onFirstPage=_cabecalho, onLaterPages=_cabecalho)
    return buf.getvalue()


def sha256(b):
    return hashlib.sha256(b).hexdigest()
