# -*- coding: utf-8 -*-
"""PDF do orçamento (21/11/2026). Anderson: "os modelos de Orçamento e Prestação têm que ser bem bonitos e elegantes para
impressionar"; "não vejo nome de quem solicitou"; "número de cliente não deve aparecer"; "quero saber quem assinou e quando".

Capa única e limpa: cabeçalho do escritório (logo + endereço), faixa verde-escura com o número e a validade, três quadros
(PARA · SOLICITADO POR · RESPONSÁVEL), tabela dos serviços com zebra, bloco do total, pagamento e condições lado a lado,
"como funciona" em três passos, e o bloco de aceite: QR code + link para o cliente aceitar pelo sistema ou, quando já aceito,
o registro (quem, quando, e-mail, IP, impressão digital). Aceito = ganha a página "Registro de aceite" no fim. ReportLab."""
import io
from datetime import date
from utils.texto import nome_proprio
from decimal import Decimal

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models.prest_contrato import QUALICONTAX
from utils.prest_contrato_pdf import _LOGO, _esc

W, H = A4
ML = MR = 1.7 * cm
LW = W - ML - MR
VERDE = colors.HexColor('#0f3d2e')
VERDE2 = colors.HexColor('#15803d')
VERDE3 = colors.HexColor('#22c55e')
TINTA = colors.HexColor('#0f172a')
CINZA = colors.HexColor('#64748b')
CINZA2 = colors.HexColor('#94a3b8')
LINHA = colors.HexColor('#e2e8f0')
FUNDO = colors.HexColor('#f6faf7')
FUNDO2 = colors.HexColor('#ecfdf3')
AMBAR = colors.HexColor('#b45309')
AMBARF = colors.HexColor('#fffbeb')


def _brl(v):
    return 'R$ ' + '{:,.2f}'.format(Decimal(str(v or 0))).replace(',', 'X').replace('.', ',').replace('X', '.')


def _doc(d):
    d = ''.join(ch for ch in str(d or '') if ch.isdigit())
    if len(d) == 14:
        return 'CNPJ %s.%s.%s/%s-%s' % (d[:2], d[2:5], d[5:8], d[8:12], d[12:])
    if len(d) == 11:
        return 'CPF %s.%s.%s-%s' % (d[:3], d[3:6], d[6:9], d[9:])
    return d


def _fone(f):
    d = ''.join(ch for ch in str(f or '') if ch.isdigit())
    if len(d) == 11:
        return '(%s) %s-%s' % (d[:2], d[2:7], d[7:])
    if len(d) == 10:
        return '(%s) %s-%s' % (d[:2], d[2:6], d[6:])
    return str(f or '')


def _st():
    b = ParagraphStyle('b', fontName='Helvetica', fontSize=9.4, leading=13, textColor=TINTA)
    return {
        'b': b,
        'faixa_k': ParagraphStyle('fk', parent=b, fontName='Helvetica-Bold', fontSize=7.6, textColor=colors.HexColor('#bbf7d0'), leading=10),
        'faixa_t': ParagraphStyle('ft', parent=b, fontName='Helvetica-Bold', fontSize=19, leading=22, textColor=colors.white),
        'faixa_r': ParagraphStyle('fr', parent=b, fontSize=9, leading=12, textColor=colors.white, alignment=TA_RIGHT),
        'faixa_rb': ParagraphStyle('frb', parent=b, fontName='Helvetica-Bold', fontSize=12.5, leading=15, textColor=colors.white, alignment=TA_RIGHT),
        'k': ParagraphStyle('k', parent=b, fontName='Helvetica-Bold', fontSize=7.2, leading=9.5, textColor=VERDE2),
        'nome': ParagraphStyle('n', parent=b, fontName='Helvetica-Bold', fontSize=10.5, leading=13),
        'mi': ParagraphStyle('mi', parent=b, fontSize=8.2, leading=11, textColor=CINZA),
        'sec': ParagraphStyle('sec', parent=b, fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=VERDE, spaceBefore=10, spaceAfter=4),
        'th': ParagraphStyle('th', parent=b, fontName='Helvetica-Bold', fontSize=7.6, textColor=colors.white),
        'thr': ParagraphStyle('thr', parent=b, fontName='Helvetica-Bold', fontSize=7.6, textColor=colors.white, alignment=TA_RIGHT),
        'td': ParagraphStyle('td', parent=b, fontSize=9.4, leading=12.5),
        'tdr': ParagraphStyle('tdr', parent=b, fontSize=9.4, leading=12.5, alignment=TA_RIGHT),
        'tot_k': ParagraphStyle('tk', parent=b, fontSize=8.4, textColor=CINZA, alignment=TA_RIGHT),
        'tot_v': ParagraphStyle('tv', parent=b, fontName='Helvetica-Bold', fontSize=9.4, alignment=TA_RIGHT),
        'tot_big_k': ParagraphStyle('tbk', parent=b, fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor('#bbf7d0')),
        'tot_big': ParagraphStyle('tb', parent=b, fontName='Helvetica-Bold', fontSize=16, leading=19, textColor=colors.white, alignment=TA_RIGHT),
        'obs': ParagraphStyle('obs', parent=b, fontSize=9, leading=13),
        'passo_n': ParagraphStyle('pn', parent=b, fontName='Helvetica-Bold', fontSize=13, textColor=VERDE2, alignment=TA_CENTER),
        'passo_t': ParagraphStyle('pt', parent=b, fontName='Helvetica-Bold', fontSize=9, leading=12),
        'passo_s': ParagraphStyle('ps', parent=b, fontSize=8.2, leading=11, textColor=CINZA),
        'ace_t': ParagraphStyle('at', parent=b, fontName='Helvetica-Bold', fontSize=10.5, leading=13, textColor=VERDE),
        'ace': ParagraphStyle('ac', parent=b, fontSize=8.8, leading=12),
        'link': ParagraphStyle('lk', parent=b, fontName='Courier', fontSize=7.6, leading=10, textColor=VERDE2),
        'rod': ParagraphStyle('rod', parent=b, fontSize=8, leading=11, textColor=CINZA),
        'reg_t': ParagraphStyle('rt', parent=b, fontName='Helvetica-Bold', fontSize=14, textColor=VERDE, spaceAfter=6),
        'reg': ParagraphStyle('rg', parent=b, fontSize=9, leading=12.5),
        'mono': ParagraphStyle('mono', parent=b, fontName='Courier', fontSize=7.4, leading=10),
    }


def _pagina(numero, data_doc):
    def _f(canvas, doc):
        canvas.saveState()
        topo = H - 1.0 * cm
        try:
            img = ImageReader(_LOGO); iw, ih = img.getSize(); lh = 1.45 * cm
            canvas.drawImage(img, ML, topo - lh, width=lh * iw / ih, height=lh, mask='auto')
        except Exception:
            canvas.setFont('Helvetica-Bold', 13); canvas.setFillColor(VERDE); canvas.drawString(ML, topo - 0.9 * cm, 'QUALICONTAX')
        canvas.setFont('Helvetica', 7.6); canvas.setFillColor(CINZA)
        for i, linha in enumerate(getattr(doc, 'cabecalho', None) or QUALICONTAX['cabecalho']):
            canvas.drawRightString(W - MR, topo - 0.32 * cm - i * 9.2, linha)
        canvas.setStrokeColor(VERDE3); canvas.setLineWidth(1.4); canvas.line(ML, topo - 1.65 * cm, W - MR, topo - 1.65 * cm)
        canvas.setStrokeColor(LINHA); canvas.setLineWidth(0.5); canvas.line(ML, 1.45 * cm, W - MR, 1.45 * cm)
        canvas.setFont('Helvetica', 7); canvas.setFillColor(CINZA)
        qc = getattr(doc, 'qc', None) or QUALICONTAX
        canvas.drawString(ML, 1.0 * cm, 'Orçamento %s · %s · %s · CNPJ %s' % (numero, data_doc.strftime('%d/%m/%Y'), qc['razao'], qc['cnpj']))
        canvas.drawRightString(W - MR, 1.0 * cm, 'página %d' % doc.page)
        canvas.restoreState()
    return _f


def _caixa(cor_fundo, cor_borda=None, pad=10, radius=None):
    est = [('BACKGROUND', (0, 0), (-1, -1), cor_fundo), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
           ('LEFTPADDING', (0, 0), (-1, -1), pad), ('RIGHTPADDING', (0, 0), (-1, -1), pad), ('TOPPADDING', (0, 0), (-1, -1), pad), ('BOTTOMPADDING', (0, 0), (-1, -1), pad)]
    if cor_borda:
        est.append(('BOX', (0, 0), (-1, -1), 0.6, cor_borda))
    return TableStyle(est)


def _pessoa(nome):
    """Nome de pessoa em maiúsculas e minúsculas, como no contrato e na proposta (26/11/2026 — Anderson: "veja como está na
    proposta e no contratos"): o cadastro guarda ANDERSON ANTUNES VIEIRA e o usuário Anderson Antunes Vieira; no PDF saem
    iguais. Empresa continua como está no cadastro (maiúsculas)."""
    return nome_proprio(nome or '')


def gerar_pdf(o, itens, link=None, data_doc=None):
    """``o`` = Orcamento.get(id); ``itens`` = Orcamento.itens(id); ``link`` = página de aceite (None = controle interno);
    ``data_doc`` = data impressa no rodapé (a do envio, para a impressão digital não mudar de um dia para o outro)."""
    st = _st()
    data_doc = data_doc or date.today()
    from models.prest_contrato import PrestContrato
    qc = PrestContrato.contratada()        # 24/11/2026: razão, CNPJ, CRC e endereço vêm do cadastro da Qualicontax (nº 1), como no contrato
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=2.6 * cm, bottomMargin=1.9 * cm,
                            title='Orçamento %s' % o['numero'], author=qc['razao'], invariant=1)   # sem data de criação: a impressão digital não muda
    doc.qc = qc; doc.cabecalho = qc.get('cabecalho') or QUALICONTAX['cabecalho']
    el = []
    validade = o['validade'].strftime('%d/%m/%Y') if o.get('validade') else None
    interno = bool(o.get('interno'))
    aceito = bool(o.get('aceito_em'))

    # ── faixa ──
    dir_txt = ('PROPOSTA ACEITA em %s' % o['aceito_em'].strftime('%d/%m/%Y')) if aceito else (('válida até %s' % validade) if validade else 'sem prazo de validade')
    faixa = Table([[[Paragraph('PROPOSTA DE SERVIÇOS' + (' · CONTROLE INTERNO' if interno else ''), st['faixa_k']), Paragraph('Orçamento %s' % _esc(o['numero']), st['faixa_t'])],
                    [Paragraph(_esc(o['titulo']) if o.get('titulo') else '', st['faixa_r']), Paragraph(dir_txt, st['faixa_rb'])]]],
                  colWidths=[LW * .56, LW * .44])
    faixa.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), VERDE), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 14), ('RIGHTPADDING', (0, 0), (-1, -1), 14), ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12)]))
    el += [faixa, Spacer(1, 8)]

    # ── três quadros: PARA · SOLICITADO POR · RESPONSÁVEL ──
    pf = len(''.join(ch for ch in str(o.get('cliente_doc') or '') if ch.isdigit())) == 11
    para = [Paragraph('PARA', st['k']), Paragraph(_esc(_pessoa(o['cliente_nome']) if pf else o['cliente_nome']), st['nome'])]
    if _doc(o.get('cliente_doc')):                        # só o CPF/CNPJ: o e-mail do cadastro é informativo; quem recebe é "quem pediu" (26/11/2026)
        para.append(Paragraph(_esc(_doc(o.get('cliente_doc'))), st['mi']))
    for nome in (o.get('empresas_mais') or []):           # mais de uma empresa (25/11/2026)
        para.append(Paragraph(_esc('+ ' + nome), st['mi']))
    if o.get('empresa_nova_nome'):
        para.append(Paragraph(_esc('Empresa a abrir: ' + o['empresa_nova_nome']), st['mi']))
    sol = [Paragraph('SOLICITADO POR', st['k'])]
    if o.get('solicitante_nome'):
        sol.append(Paragraph(_esc(_pessoa(o['solicitante_nome'])), st['nome']))
        mi = [x for x in (o.get('solicitante_email'), _fone(o.get('solicitante_fone')) if o.get('solicitante_fone') else '') if x]
        if mi:
            sol.append(Paragraph(_esc(' · '.join(mi)), st['mi']))
    else:
        sol.append(Paragraph('—', st['nome']))
    resp = [Paragraph('RESPONSÁVEL NA QUALICONTAX', st['k']), Paragraph(_esc(_pessoa(o.get('criado_por_nome')) or 'Qualicontax'), st['nome'])]
    mi = [x for x in (o.get('criado_por_email'), _fone(o.get('criado_por_fone')) if o.get('criado_por_fone') else '') if x]
    if mi:
        resp.append(Paragraph(_esc(' · '.join(mi)), st['mi']))
    q3 = Table([[para, sol, resp]], colWidths=[LW * .40, LW * .30, LW * .30])
    q3.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), FUNDO), ('BOX', (0, 0), (-1, -1), 0.6, LINHA), ('LINEBEFORE', (1, 0), (-1, -1), 0.6, LINHA),
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 11), ('RIGHTPADDING', (0, 0), (-1, -1), 11), ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9)]))
    el += [q3, Spacer(1, 4)]

    # ── serviços ──
    el.append(Paragraph('SERVIÇOS', st['sec']))
    dados = [[Paragraph('Serviço', st['th']), Paragraph('Qtd', st['thr']), Paragraph('Valor', st['thr']), Paragraph('Total', st['thr'])]]
    tem_taxa = False
    cabecalhos = []                                   # linhas de empresa (um boleto por empresa, 26/11/2026)

    def _linha_item(i):
        nonlocal tem_taxa
        nome = _esc(i['descricao'])
        if not i['emite_nota']:
            nome += ' <font color="#b45309" size="7.4">· taxa repassada, só no boleto</font>'; tem_taxa = True
        qd = Decimal(str(i['quantidade']))
        dados.append([Paragraph(nome, st['td']), Paragraph(('%g' % qd) if qd == qd.to_integral() else str(qd).replace('.', ','), st['tdr']),
                      Paragraph(_brl(i['valor_unitario']), st['tdr']), Paragraph(_brl(i['valor_total']), st['tdr'])])
    if o.get('por_empresa'):
        ids = [e['id'] for e in o.get('empresas') or []]
        grupos = [(e['nome'], [i for i in itens if i.get('cliente_id') == e['id']]) for e in o.get('empresas') or []]
        soltos = [i for i in itens if i.get('cliente_id') not in ids]
        if soltos:
            grupos.append((o.get('cliente_nome') or '', soltos))
        for nome_e, its in grupos:
            if not its:
                continue
            cabecalhos.append(len(dados))
            sub = sum((Decimal(str(i['valor_total'])) for i in its), Decimal('0'))
            dados.append([Paragraph('<b>%s</b> <font color="#64748b" size="7.4">· boleto próprio</font>' % _esc(nome_e), st['td']), '', '', Paragraph('<b>%s</b>' % _brl(sub), st['tdr'])])
            for i in its:
                _linha_item(i)
    else:
        for i in itens:
            _linha_item(i)
    t = Table(dados, colWidths=[LW * .60, LW * .09, LW * .155, LW * .155], repeatRows=1)
    est = [('BACKGROUND', (0, 0), (-1, 0), VERDE2), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINHA),
           ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]
    for r in range(1, len(dados)):
        if r in cabecalhos:
            est += [('SPAN', (0, r), (2, r)), ('BACKGROUND', (0, r), (-1, r), colors.HexColor('#e8f3ec')), ('LINEABOVE', (0, r), (-1, r), 0.8, VERDE2)]
        elif r % 2 == 0:
            est.append(('BACKGROUND', (0, r), (-1, r), FUNDO))
    t.setStyle(TableStyle(est))
    el.append(t)
    total = sum((Decimal(str(i['valor_total'])) for i in itens), Decimal('0'))
    nota = sum((Decimal(str(i['valor_total'])) for i in itens if i['emite_nota']), Decimal('0'))
    linhas_tot = []
    if tem_taxa and nota:
        linhas_tot += [[Paragraph('Serviços com nota fiscal', st['tot_k']), Paragraph(_brl(nota), st['tot_v'])],
                       [Paragraph('Taxas repassadas (só no boleto)', st['tot_k']), Paragraph(_brl(total - nota), st['tot_v'])]]
    tot_box = Table([[Paragraph('TOTAL' + ((' · %dx de %s' % (o['parcelas'], _brl(total / o['parcelas']))) if o.get('parcelas') and o['parcelas'] > 1 else ''), st['tot_big_k']), Paragraph(_brl(total), st['tot_big'])]],
                    colWidths=[LW * .17, LW * .24])
    tot_box.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), VERDE), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12), ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9)]))
    linhas_tot.append(['', tot_box])
    tt = Table(linhas_tot, colWidths=[LW * .59, LW * .41])
    tt.setStyle(TableStyle([('ALIGN', (1, 0), (1, -1), 'RIGHT'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
    el += [Spacer(1, 6), tt]

    # ── pagamento e condições ──
    como = {'BOLETO_NOTA': 'Boleto bancário com nota fiscal de serviço', 'BOLETO': 'Boleto bancário', 'MENSAL': 'Incluído na mensalidade dos honorários'}.get(o.get('como'))
    pag = [Paragraph('PAGAMENTO', st['k'])]
    if como:
        pag.append(Paragraph(_esc(como) + ((', em %d parcelas' % o['parcelas']) if o.get('parcelas') and o['parcelas'] > 1 else ', à vista'), st['obs']))
    if o.get('por_empresa'):
        pag.append(Paragraph('Um boleto para cada empresa, com os serviços dela.', st['obs']))
    elif o.get('pagador_outro'):
        pag.append(Paragraph('Boleto único em nome de <b>%s</b>, com os serviços de todas as empresas.' % _esc(o['pagador_nome']), st['obs']))
    if tem_taxa:
        pag.append(Paragraph('As taxas repassadas são valores pagos a terceiros (Junta Comercial, cartório, Correios) sem acréscimo; entram no boleto e não compõem a nota fiscal.', st['mi']))
    if validade and not aceito:
        pag.append(Paragraph('Proposta válida até <b>%s</b>.' % validade, st['obs']))
    cond = [Paragraph('CONDIÇÕES', st['k'])]
    if o.get('observacao'):
        for par in str(o['observacao']).split('\n'):
            if par.strip():
                cond.append(Paragraph('• ' + _esc(par.strip()), st['obs']))
    else:
        cond.append(Paragraph('• Serviço executado após o aceite e a entrega dos documentos necessários.', st['obs']))
    pc = Table([[pag, cond]], colWidths=[LW * .46, LW * .54])
    pc.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, LINHA), ('LINEBEFORE', (1, 0), (1, -1), 0.6, LINHA), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('LEFTPADDING', (0, 0), (-1, -1), 11), ('RIGHTPADDING', (0, 0), (-1, -1), 11), ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9)]))
    el += [Spacer(1, 10), pc]

    # ── como funciona ──
    passos = [('1', 'Aceite', 'Você aceita a proposta pelo link seguro ou respondendo este e-mail.' if not interno else 'Orçamento aprovado internamente.'),
              ('2', 'Execução', 'A Qualicontax executa o serviço e acompanha cada etapa.'),
              ('3', 'Cobrança', 'A cobrança sai como combinado, com o comprovante por e-mail.')]
    cel = [[Paragraph(n, st['passo_n']), [Paragraph(tit, st['passo_t']), Paragraph(txt, st['passo_s'])]] for n, tit, txt in passos]
    linha = Table([[Table([c], colWidths=[0.8 * cm, LW / 3 - 0.8 * cm - 8], style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 4)])) for c in cel]],
                  colWidths=[LW / 3] * 3)
    linha.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
    el += [Paragraph('COMO FUNCIONA', st['sec']), linha]

    # ── aceite ──
    if aceito:
        bloco = Table([[[Paragraph('✔ Proposta aceita eletronicamente', st['ace_t']),
                         Paragraph('<b>%s</b>%s · em <b>%s</b>' % (_esc(_pessoa(o.get('aceito_nome'))), (' · CPF %s' % _esc(o['aceito_cpf'])) if o.get('aceito_cpf') else '', o['aceito_em'].strftime('%d/%m/%Y às %H:%M')), st['ace']),
                         Paragraph('e-mail %s · IP %s · registro completo na última página' % (_esc(o.get('aceito_email') or ''), _esc(o.get('aceito_ip') or '')), st['mi'])]]], colWidths=[LW])
        bloco.setStyle(_caixa(FUNDO2, VERDE3, pad=12))
        el += [Spacer(1, 10), bloco]
    elif link and not interno:
        d = Drawing(2.6 * cm, 2.6 * cm)
        w = qr.QrCodeWidget(link); b0 = w.getBounds(); bw, bh = b0[2] - b0[0], b0[3] - b0[1]
        w.barWidth, w.barHeight = 2.6 * cm, 2.6 * cm
        from reportlab.graphics.shapes import Group
        g = Group(w); g.scale(2.6 * cm / bw, 2.6 * cm / bh); d.add(g)
        bloco = Table([[d, [Paragraph('Para aceitar esta proposta', st['ace_t']),
                            Paragraph('Aponte a câmera do celular para o código ou abra o link abaixo. Você recebe um código por e-mail, confirma seu nome e pronto: o aceite fica registrado com data e hora, e uma via assinada volta para o seu e-mail.', st['ace']),
                            Paragraph(_esc(link), st['link'])]]], colWidths=[3.2 * cm, LW - 3.2 * cm])
        bloco.setStyle(_caixa(FUNDO2, VERDE3, pad=12))
        el += [Spacer(1, 10), KeepTogether(bloco)]

    el += [Spacer(1, 14), Paragraph('Atenciosamente,', st['b']), Spacer(1, 10),
           Paragraph('<b>%s</b>' % _esc(_pessoa(o.get('criado_por_nome')) or qc['razao']), st['b']),
           Paragraph(_esc(qc['razao']) + ' · CNPJ %s · CRC %s' % (qc['cnpj'], qc.get('crc') or QUALICONTAX['crc']), st['mi'])]

    # ── registro de aceite ──
    if aceito:
        el.append(PageBreak())
        el.append(Paragraph('Registro de aceite eletrônico', st['reg_t']))
        el.append(Paragraph('Este registro integra o orçamento %s e comprova o aceite da proposta pelo cliente, nos termos da MP 2.200-2/2001 e da Lei 14.063/2020.' % _esc(o['numero']), st['reg']))
        linhas = [('Orçamento', o['numero']), ('Cliente', _pessoa(o['cliente_nome']) if pf else o['cliente_nome']), ('Valor', _brl(o['total'])),
                  ('Aceito por', _pessoa(o.get('aceito_nome')) + ((' · CPF %s' % o['aceito_cpf']) if o.get('aceito_cpf') else '')),
                  ('Data e hora', o['aceito_em'].strftime('%d/%m/%Y %H:%M:%S')), ('E-mail (código de confirmação)', o.get('aceito_email') or ''),
                  ('Endereço IP', o.get('aceito_ip') or ''), ('Navegador', (o.get('aceito_agente') or '')[:160]),
                  ('Enviado ao cliente em', o['enviado_em'].strftime('%d/%m/%Y %H:%M') if o.get('enviado_em') else ''),
                  ('Impressão digital do documento (SHA-256)', o.get('aceito_hash') or '')]
        tr = Table([[Paragraph('<b>%s</b>' % _esc(k), st['reg']), Paragraph(_esc(v), st['mono'] if 'SHA' in k else st['reg'])] for k, v in linhas], colWidths=[LW * .34, LW * .66])
        tr.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, -1), 0.4, LINHA), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                                ('LEFTPADDING', (0, 0), (-1, -1), 4)]))
        el += [Spacer(1, 8), tr, Spacer(1, 10), Paragraph('A impressão digital é calculada sobre o PDF exatamente como foi apresentado ao cliente no momento do aceite; qualquer alteração posterior produz outra impressão.', st['mi'])]

    doc.build(el, onFirstPage=_pagina(o['numero'], data_doc), onLaterPages=_pagina(o['numero'], data_doc))
    return buf.getvalue()
