# -*- coding: utf-8 -*-
"""PROPOSTA DE SERVIÇOS (25/11/2026 — Anderson: "quero montar uma outra aba como PROPOSTA DE SERVIÇOS... a proposta que seja
feita no COMERCIAL, quando aprovada, o USUARIO DISPARA e acompanha... se aprovado ai o USUARIO CONVERTE para CONTRATO...
é como adiantamento do Contrato").

A proposta mora na mesma tabela do contrato (prest_contratos, tipo PROPOSTA) com os MESMOS ``dados`` do formulário do
contrato (empresas, áreas, honorários, condições) mais o bloco ``dados['proposta']``: validade, A/C, itens de cada área,
outras considerações e serviços extraordinários — os textos do modelo em Word da casa (Entre Rios, Carlos Lira).
Aqui ficam os textos padrão, o ``montar`` (o que o PDF e as telas mostram) e o PDF, no molde do contrato."""
import io
import re
from datetime import date, datetime, timedelta

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models.prest_contrato import AREAS, QUALICONTAX, brl
from utils import prest_contrato_pdf as P
from utils.extenso import inteiro_extenso
from utils.texto import nome_proprio

# itens de cada área como saem na proposta (modelo da casa); a chave é a mesma das AREAS do contrato
ITENS_PADRAO = {
    'CONTABIL': ['Elaboração de Balanço Patrimonial, DRE e demais demonstrações, mediante o envio dos extratos bancários em PDF, OFX e Excel;',
                 'ECD (Escrituração Contábil Digital);', 'ECF (Escrituração Contábil Fiscal);',
                 'DIRF (Declaração de Imposto de Renda Retido na Fonte); e', 'Apuração dos impostos federais.'],
    'FISCAL': ['EFD Contribuições, mediante envio do arquivo;', 'EFD ICMS/IPI, mediante envio do arquivo; e',
               'Apuração dos impostos estaduais e municipais.'],
    'PESSOAL': ['Elaboração da folha de pagamento e do adiantamento salarial;', 'Admissões e demissões: acompanhamos e orientamos;',
                'eSocial: elaboramos e enviamos; e', 'Apuração dos impostos e guias sobre a folha de pagamento.'],
}
TITULO_AREA = {'CONTABIL': 'ÁREA CONTÁBIL', 'FISCAL': 'ÁREA FISCAL', 'PESSOAL': 'ÁREA TRABALHISTA'}
CONSIDERACOES_PADRAO = [
    'Regime de tributação Lucro Real estimado (mensal) ou trimestral, a ser decidido entre a contabilidade e o cliente;',
    'A entrega dos impostos e documentos para pagamento se dará com, no mínimo, 2 (dois) dias de antecedência, desde que todos os arquivos e documentos tenham sido enviados;',
    'Contamos com um aplicativo (Android e iOS) e uma Área VIP na internet, onde o cliente consulta todos os seus documentos e solicita serviços; e',
    'No decorrer da parceria faremos sugestões e traremos as novidades que o mercado apresenta, para manter o cliente sempre atualizado.',
]
EXTRAS_PADRAO = [
    'Constituição de empresas;', 'Alterações contratuais;',
    'Certidões negativas que exijam deslocamento a órgãos públicos (as emitidas pela internet não são cobradas);',
    'Reconhecimento de firmas e autenticações em cartório;', 'Emissão de alvará de funcionamento;',
    'Licenciamento do Corpo de Bombeiros; e', 'Declaração de Imposto de Renda Pessoa Física.',
]
VALIDADES = (10, 15, 30, 45)
STATUS = ('RASCUNHO', 'APROVACAO', 'APROVADO', 'ENVIADO', 'ACEITO', 'RECUSADO', 'CONVERTIDO', 'CANCELADO')
ROTULO = {'RASCUNHO': 'Rascunho', 'APROVACAO': 'Aguardando aprovação', 'APROVADO': 'Aprovada · pronta para disparar',
          'ENVIADO': 'Com o cliente', 'ACEITO': 'Aceita pelo cliente', 'RECUSADO': 'Recusada pelo cliente',
          'CONVERTIDO': 'Convertida em contrato', 'CANCELADO': 'Cancelada'}
STATUS_CLS = {'RASCUNHO': ('tx-perd', 'fa-pen-ruler'), 'APROVACAO': ('tx-venc', 'fa-stamp'), 'APROVADO': ('tx-ok', 'fa-paper-plane'),
              'ENVIADO': ('tx-hoje', 'fa-hourglass-half'), 'ACEITO': ('tx-ok', 'fa-file-signature'), 'RECUSADO': ('tx-ina', 'fa-thumbs-down'),
              'CONVERTIDO': ('tx-fut', 'fa-file-contract'), 'CANCELADO': ('tx-perd', 'fa-ban')}


def linhas(texto):
    """Textarea 'um item por linha' → lista limpa (tira marcadores digitados)."""
    out = []
    for l in str(texto or '').splitlines():
        l = re.sub(r'^\s*[-•*·]\s*', '', l).strip()
        if l:
            out.append(l[:300])
    return out


def proposta_padrao(dados=None, segmento=''):
    """O bloco ``proposta`` de uma proposta nova: textos da casa, A/C com quem assina pela empresa."""
    d = dados or {}
    emp = (d.get('empresas') or [{}])[0]
    ac = [n for n in ((emp.get('rep_nome') or ''), ((d.get('assinante_extra') or {}).get('nome') or '')) if n]
    cons = list(CONSIDERACOES_PADRAO)
    if segmento:
        cons[-1] = cons[-1].replace('que o mercado apresenta', 'que o mercado de %s apresenta' % segmento)
    return {'validade_dias': 15, 'ac': ac, 'itens': {k: list(v) for k, v in ITENS_PADRAO.items()},
            'consideracoes': cons, 'extras': list(EXTRAS_PADRAO), 'segmento': segmento or ''}


def _reais_extenso(v):
    v = round(float(v or 0), 2)
    r = int(v); c = int(round((v - r) * 100))
    t = inteiro_extenso(r) + (' real' if r == 1 else ' reais')
    if c:
        t += ' e ' + inteiro_extenso(c) + (' centavo' if c == 1 else ' centavos')
    return t


def _lista_nomes(nomes):
    nomes = [nome_proprio(n) for n in nomes if n]
    if not nomes:
        return ''
    return nomes[0] if len(nomes) == 1 else ', '.join(nomes[:-1]) + ' e ' + nomes[-1]


def _f(v):
    try:
        return float(str(v or '0').replace(',', '.'))
    except ValueError:
        return 0.0


def _data_pt(d):
    meses = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro')
    return '%d de %s de %d' % (d.day, meses[d.month - 1], d.year)


def montar(dados, data_base=None):
    """O que o PDF, a janela e a página do cliente mostram. ``dados`` = dados da proposta (os do contrato + 'proposta')."""
    p = dados.get('proposta') or proposta_padrao(dados)
    emps = dados.get('empresas') or []
    rede = len(emps) > 1
    apelido = (dados.get('apelido') or '').strip()
    pagantes = [e for e in emps if (e.get('situacao') or 'PAGA') == 'PAGA']
    por_empresa = bool(dados.get('valores_por_empresa')) and len(pagantes) > 1
    total = _f(dados.get('valor'))
    linhas_hon = []
    for e in emps:
        sit = e.get('situacao') or 'PAGA'
        if sit == 'PAGA':
            v = _f(e.get('valor')) if por_empresa else (total if len(pagantes) == 1 else 0.0)
            linhas_hon.append({'razao': e.get('razao'), 'cnpj': e.get('cnpj'), 'valor': v, 'situacao': 'PAGA',
                               'texto': brl(v)})
        elif sit == 'VIA_GRUPO':
            linhas_hon.append({'razao': e.get('razao'), 'cnpj': e.get('cnpj'), 'valor': 0.0, 'situacao': sit, 'texto': 'via grupo, sem cobrança'})
        else:
            linhas_hon.append({'razao': e.get('razao'), 'cnpj': e.get('cnpj'), 'valor': 0.0, 'situacao': sit, 'texto': 'sem movimento, sem cobrança'})
    destino = ('Grupo ' + apelido) if (rede and apelido) else (nome_proprio(emps[0].get('razao') or '') if emps else '')
    if rede and not apelido:
        destino = 'Grupo de empresas ' + nome_proprio(emps[0].get('razao') or '')
    data = data_base or (datetime.fromisoformat(dados['data_proposta']).date() if dados.get('data_proposta') else date.today())
    validade = int(p.get('validade_dias') or 15)
    ate = data + timedelta(days=validade)
    areas = [k for k in (dados.get('areas') or []) if k in AREAS]
    itens = p.get('itens') or {}
    blocos = [(TITULO_AREA.get(k, AREAS[k][0]), list(itens.get(k) or ITENS_PADRAO.get(k) or [])) for k in areas]
    extras_esp = [x for x in (dados.get('servicos_extras') or []) if x]
    if extras_esp:
        blocos.append(('SERVIÇOS ESPECÍFICOS', [x.rstrip('.;') + ';' for x in extras_esp]))
    rt = dados.get('reajuste_tipo') or 'SALARIO_MINIMO'
    reaj = {'SALARIO_MINIMO': 'anual, pelo salário mínimo, em janeiro', 'INDICE': 'anual, pelo %s' % (dados.get('reajuste_indice') or 'índice'),
            'DATA_PROPRIA': 'anual, pelo %s, em %s' % (dados.get('reajuste_indice') or 'índice', dados.get('reajuste_mes') or 'data própria'),
            'ESCALONADO': 'escalonado (%s)' % (dados.get('reajuste_escala') or ''), 'SEM': 'de comum acordo'}.get(rt, rt)
    reaj_curto = {'SALARIO_MINIMO': 'salário mínimo', 'INDICE': dados.get('reajuste_indice') or 'índice', 'DATA_PROPRIA': dados.get('reajuste_indice') or 'índice',
                  'ESCALONADO': 'escalonado', 'SEM': 'de comum acordo'}.get(rt, rt)
    return {
        'titulo': 'PROPOSTA DE SERVIÇOS', 'destino': destino, 'ac': [n for n in (p.get('ac') or []) if n], 'ac_txt': _lista_nomes(p.get('ac') or []),
        'empresas': emps, 'rede': rede, 'apelido': apelido, 'data': data, 'data_txt': _data_pt(data), 'validade_dias': validade, 'validade_ate': ate,
        'blocos': blocos, 'consideracoes': list(p.get('consideracoes') or []), 'extras': list(p.get('extras') or []),
        'extras_cobrados': dados.get('extras_cobrados', True),
        'honorarios': linhas_hon, 'total': total, 'total_txt': brl(total), 'total_extenso': _reais_extenso(total),
        'dia_vencimento': dados.get('dia_vencimento') or 10, 'reajuste': reaj, 'reajuste_curto': reaj_curto,
        'decimo': dados.get('decimo') or 'COBRADO', 'inicio_txt': 'na assinatura do contrato', 'contratada': dados.get('contratada') or QUALICONTAX,
        'assina': dados.get('assina') or {}, 'n_pagantes': len(pagantes), 'por_empresa': por_empresa,
    }


# ───────────────────────── PDF ─────────────────────────
def _e(s):
    return P._esc(s)


def gerar_pdf(m, numero=None, aceite=None, recusa=None, situacao_txt=None):
    """``m`` = montar(dados). ``aceite`` = dict do cliente que aceitou (nome, cpf, em, ip, hash) → bloco de registro no fim."""
    st = P._estilos(); LW = P.LW
    st['bullet'] = ParagraphStyle('bul', parent=st['base'], leftIndent=14, bulletIndent=3, spaceBefore=1.5)
    st['area'] = ParagraphStyle('area', parent=st['sec'], fontSize=9, spaceBefore=9)
    st['ol'] = ParagraphStyle('ol', parent=st['base'], fontSize=10.2, leading=14)
    buf = io.BytesIO()
    qc = m.get('contratada') or QUALICONTAX
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=P.ML, rightMargin=P.MR, topMargin=3.1 * cm, bottomMargin=2.1 * cm,
                            title='Proposta de serviços%s' % ((' nº %s' % numero) if numero else ''), author=P._nome_curto(qc.get('razao') or QUALICONTAX['razao']), invariant=1)
    doc.rodape = ' · '.join(x for x in (('Proposta nº %s' % numero) if numero else 'Proposta', situacao_txt or ('válida até %s' % m['validade_ate'].strftime('%d/%m/%Y')),
                                        P._nome_curto(qc.get('razao') or QUALICONTAX['razao']), 'CNPJ %s' % (qc.get('cnpj') or QUALICONTAX['cnpj'])) if x)
    doc.cabecalho = qc.get('cabecalho') or QUALICONTAX['cabecalho']
    el = []
    esq = [Paragraph('PROPOSTA DE SERVIÇOS', st['faixa_k']), Paragraph(_e(m['destino']), st['faixa_t'])]
    if m['ac_txt']:
        esq.append(Paragraph(_e('A/C %s' % m['ac_txt']), st['faixa_r'].clone('fl', alignment=0)))
    dir_ = [Paragraph('HONORÁRIOS MENSAIS', st['faixa_rk']), Paragraph(m['total_txt'], st['faixa_rv']),
            Paragraph(_e('%d empresa%s%s' % (len(m['empresas']), 's' if len(m['empresas']) != 1 else '', (' · nº %s' % numero) if numero else '')), st['faixa_r'])]
    t = Table([[esq, dir_]], colWidths=[LW * .62, LW * .38]); t.setStyle(P._caixa(P._VERDE, pad=14)); el.append(t); el.append(Spacer(1, 10))
    kp = []
    for k, v, s in [('DATA', m['data'].strftime('%d/%m/%Y'), 'São Paulo'), ('VALIDADE', '%d dias' % m['validade_dias'], 'até ' + m['validade_ate'].strftime('%d/%m/%Y')),
                    ('INÍCIO', 'na assinatura', 'do contrato'), ('VENCIMENTO', 'todo dia %s' % m['dia_vencimento'], 'de cada mês'),
                    ('REAJUSTE', m['reajuste_curto'], {'SALARIO_MINIMO': 'todo janeiro'}.get('SALARIO_MINIMO' if 'salário' in m['reajuste_curto'] else '', 'anual'))]:
        kp.append([Paragraph(k, st['kpi_k']), Paragraph(_e(v), st['kpi_v']), Paragraph(_e(s), st['kpi_s'])])
    t = Table([kp], colWidths=[LW / 5] * 5)
    t.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), .6, P._LINHA2), ('INNERGRID', (0, 0), (-1, -1), .6, P._LINHA2), ('BACKGROUND', (0, 0), (-1, -1), P._FUNDO),
                           ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7), ('LEFTPADDING', (0, 0), (-1, -1), 9), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    el.append(t); el.append(Spacer(1, 12))
    el.append(Paragraph(_e('Prezado(a)s %s,' % m['ac_txt']) if m['ac_txt'] else 'Prezado(a) cliente,', st['base'])); el.append(Spacer(1, 4))
    quem = ('as empresas do %s' % m['destino']) if m['rede'] else ('a %s' % m['destino'])
    el.append(Paragraph(_e('Desejando um bom atendimento, enviamos com antecedência todos os valores referentes aos nossos serviços para %s. '
                           'Abaixo, o que está incluído em cada área, as condições e os honorários, conforme segue:' % quem), st['base']))
    for titulo, itens in m['blocos']:
        el.append(Paragraph('<font color="#15803d"><b>%s</b></font>' % _e(titulo), st['area']))
        for i in itens:
            el.append(Paragraph(_e(i), st['bullet'], bulletText='•'))
    if m['consideracoes']:
        el.append(Paragraph('<font color="#15803d"><b>OUTRAS CONSIDERAÇÕES</b></font>', st['area']))
        for i in m['consideracoes']:
            el.append(Paragraph(_e(i), st['bullet'], bulletText='•'))
    if m['extras']:
        el.append(Paragraph('<font color="#15803d"><b>SERVIÇOS EXTRAORDINÁRIOS</b></font>', st['area']))
        el.append(Paragraph(_e('Os serviços abaixo não fazem parte dos honorários mensais e, se solicitados, serão cobrados mediante orçamento prévio:'
                               if m['extras_cobrados'] else
                               'Os serviços abaixo não fazem parte dos honorários mensais; se solicitados, ficam a cargo do cliente apenas as taxas, emolumentos e custas de órgãos públicos e terceiros:'), st['base']))
        for i in m['extras']:
            el.append(Paragraph(_e(i), st['bullet'], bulletText='•'))
    # honorários
    bloco = [Paragraph('<font color="#15803d"><b>HONORÁRIOS PARA EXECUÇÃO DOS SERVIÇOS</b></font>', st['area'])]
    cab = [Paragraph('EMPRESA', st['k']), Paragraph('HONORÁRIOS MENSAIS', st['k'].clone('kr3', alignment=2))]
    lin = [cab]
    for h in m['honorarios']:
        lin.append([[Paragraph(_e(h['razao']), st['nome']), Paragraph(_e('CNPJ ' + (h['cnpj'] or '')), st['mi'])],
                    Paragraph(('<b>%s</b>' % brl(h['valor'])) if h['situacao'] == 'PAGA' else _e(h['texto']), st['ol'].clone('r3', alignment=2, textColor=P._TINTA if h['situacao'] == 'PAGA' else P._CINZA))])
    if len(m['honorarios']) > 1:
        lin.append([Paragraph('<b>TOTAL MENSAL%s</b>' % (' DO GRUPO' if m['rede'] else ''), st['nome']), Paragraph('<b>%s</b>' % m['total_txt'], st['ol'].clone('t3', alignment=2, fontSize=12.5, textColor=P._VERDE2))])
    t = Table(lin, colWidths=[LW * .66, LW * .34])
    sty = [('BOX', (0, 0), (-1, -1), .6, P._LINHA2), ('LINEBELOW', (0, 0), (-1, 0), .8, P._VERDE3), ('LINEBELOW', (0, 1), (-1, -2), .4, P._LINHA2),
           ('BACKGROUND', (0, 0), (-1, 0), P._FUNDO), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
           ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6), ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8)]
    if len(m['honorarios']) > 1:
        sty.append(('BACKGROUND', (0, -1), (-1, -1), P._FUNDO2))
    t.setStyle(TableStyle(sty))
    dec = {'COBRADO': 'O 13º honorário é cobrado em duas parcelas, em novembro e dezembro.', 'UNICA': 'O 13º honorário é cobrado em parcela única, em dezembro.', 'ISENTO': 'Não há cobrança de 13º honorário.'}.get(m['decimo'], '')
    bloco += [Spacer(1, 4), t, Spacer(1, 6),
              Paragraph(_e('Os honorários mensais somam %s (%s), vencendo todo dia %s de cada mês, com reajuste %s. %s Os valores valem para esta proposta até %s; '
                           'aceita, ela se converte no contrato de prestação de serviços, que é assinado eletronicamente por cada parte.'
                           % (m['total_txt'], m['total_extenso'], m['dia_vencimento'], m['reajuste'], dec, m['validade_ate'].strftime('%d/%m/%Y'))), st['base'])]
    el.append(KeepTogether(bloco))
    if aceite:
        cx = Table([[[Paragraph('PROPOSTA ACEITA', st['ace_t']), Spacer(1, 3),
                      Paragraph(_e('%s%s aceitou esta proposta em %s, pelo link enviado pela Qualicontax, com a identidade confirmada por código enviado ao e-mail %s.'
                                   % (nome_proprio(aceite.get('nome') or ''), (' (CPF %s)' % aceite['cpf']) if aceite.get('cpf') else '',
                                      aceite['em'].strftime('%d/%m/%Y às %H:%M') if hasattr(aceite.get('em'), 'strftime') else str(aceite.get('em') or ''), aceite.get('email') or '')), st['ace']),
                      Paragraph(_e('IP %s · impressão digital (SHA-256) do PDF aceito: %s' % (aceite.get('ip') or '', aceite.get('hash') or '')), st['card_sub'])]]], colWidths=[LW])
        cx.setStyle(P._caixa(P._FUNDO2, P._VERDE3, pad=12)); el += [Spacer(1, 12), cx]
    elif recusa:
        cx = Table([[[Paragraph('PROPOSTA RECUSADA', st['ace_t'].clone('rt', textColor=P._AMBAR)),
                      Paragraph(_e('%s recusou esta proposta em %s%s.' % (nome_proprio(recusa.get('nome') or 'O cliente'), recusa.get('em_txt') or '', (': "%s"' % recusa['motivo']) if recusa.get('motivo') else '')), st['ace'])]]], colWidths=[LW])
        cx.setStyle(P._caixa(P._FUNDO, P._LINHA2, pad=12)); el += [Spacer(1, 12), cx]
    else:
        passos = [[Paragraph(str(n), st['passo_n']), [Paragraph(_e(tt), st['passo_t']), Paragraph(_e(ss), st['passo_s'])]] for n, tt, ss in
                  [(1, 'Abra o link que recebeu por e-mail ou WhatsApp', 'a proposta completa fica na tela e em PDF'),
                   (2, 'Peça o código e confirme seu nome', 'o código de 6 dígitos chega no seu e-mail'),
                   (3, 'Toque em "Aceito a proposta"', 'a Qualicontax recebe na hora e prepara o contrato')]]
        tp = Table(passos, colWidths=[26, LW - 26 - 28]); tp.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
        cx = Table([[[Paragraph('COMO ACEITAR', st['ace_t']), Spacer(1, 4), tp]]], colWidths=[LW]); cx.setStyle(P._caixa(P._FUNDO2, P._VERDE3, pad=12))
        el += [Spacer(1, 12), cx]
    assina = m.get('assina') or {}
    fecho = [Spacer(1, 16), Paragraph(_e('Estamos à sua disposição.'), st['base']), Spacer(1, 18),
             Paragraph('<b>%s</b>' % _e(P._nome_curto(qc.get('razao') or QUALICONTAX['razao'])), st['centro']),
             Paragraph(_e(' · '.join(x for x in ('CNPJ %s' % (qc.get('cnpj') or QUALICONTAX['cnpj']), ('CRC %s' % qc['crc']) if qc.get('crc') else '',
                                                 ('%s, %s' % (nome_proprio(assina.get('nome') or ''), assina.get('cargo') or '')) if assina.get('nome') else '') if x)),
                       st['centro'].clone('c2', textColor=P._CINZA, fontSize=8.4))]
    el.append(KeepTogether(fecho))
    doc.build(el, onFirstPage=P._cabecalho, onLaterPages=P._cabecalho)
    return buf.getvalue()
