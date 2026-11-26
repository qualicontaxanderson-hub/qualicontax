# -*- coding: utf-8 -*-
"""PDF do Termo de Acordo e Confissão de Dívida (modelo aprovado pelo Anderson em 26/11/2026: "o desenho e o PDF estão ok").

Mesmo capricho do contrato (reaproveita o cabeçalho, as caixas e os estilos de utils/prest_contrato_pdf.py, sem mexer nele):
folha de rosto (faixa com o total, quem deve, quem assina, resumo em oito caixinhas, o que entra no acordo, como assinar),
o termo em cláusulas, a tabela das parcelas e os cartões de assinatura. Bytes determinísticos (invariant)."""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from utils.prest_contrato_pdf import (LW, ML, MR, _FUNDO, _FUNDO2, _LINHA2, _VERDE2, _cabecalho, _caixa, _esc, _estilos,
                                      _faixa_titulo, _nome_curto, _pessoa, _rico)

TEAL = colors.HexColor('#115e59')
EXT = {1: 'uma', 2: 'duas', 3: 'três', 4: 'quatro', 5: 'cinco', 6: 'seis', 7: 'sete', 8: 'oito', 9: 'nove', 10: 'dez',
       11: 'onze', 12: 'doze', 15: 'quinze', 18: 'dezoito', 20: 'vinte', 24: 'vinte e quatro', 30: 'trinta', 36: 'trinta e seis'}
DIA_EXT = {i: s for i, s in enumerate(['', 'um', 'dois', 'três', 'quatro', 'cinco', 'seis', 'sete', 'oito', 'nove', 'dez', 'onze',
                                         'doze', 'treze', 'catorze', 'quinze', 'dezesseis', 'dezessete', 'dezoito', 'dezenove',
                                         'vinte', 'vinte e um', 'vinte e dois', 'vinte e três', 'vinte e quatro', 'vinte e cinco',
                                         'vinte e seis', 'vinte e sete', 'vinte e oito', 'vinte e nove', 'trinta', 'trinta e um'])}
ROMPE_CLAUSULA = {
    '2_ATRASADAS': 'O atraso de 2 (duas) parcelas, consecutivas ou não, rompe este acordo',
    '1_30_DIAS': 'O atraso de qualquer parcela por mais de 30 (trinta) dias rompe este acordo',
    'SEM': ('A parcela em atraso continua devida e é cobrada como qualquer cobrança vencida, sem que o atraso rompa este acordo.'),
}


def _clausula_rompe(a):
    """Etapa 4 (26/11/2026, Anderson): rompido o acordo, o desconto cai — volta a dívida original menos o que foi pago."""
    if a['rompe'] == 'SEM':
        return ROMPE_CLAUSULA['SEM']
    if a['desconto']:
        return (ROMPE_CLAUSULA[a['rompe']] + ': o desconto da cláusula primeira é cancelado e a DEVEDORA volta a dever o valor '
                'original da dívida, de %s, descontado tudo o que já tiver pago neste acordo; esse saldo vence de uma vez e volta '
                'a ser cobrado pela CREDORA.' % _brl(a['divida']))
    return (ROMPE_CLAUSULA[a['rompe']] + ': o saldo que faltar pagar vence de uma vez e volta a ser cobrado pela CREDORA, '
            'descontadas as parcelas já pagas.')


def _brl(v):
    from utils.acordos import brl
    return brl(v)


def gerar_pdf(a, rodape=None):
    """``a`` = dict com numero, devedora{razao, cnpj, endereco}, assina{nome, cpf, cargo}, qc{nome, cargo}, itens[{descricao,
    vencimento, valor}], divida, desconto, motivo_desconto, total, plano[(n, venc, valor, entrada)], forma, rompe, dia,
    contratada{razao, cnpj, endereco, cabecalho}, cidade_data. Devolve bytes."""
    from utils.acordos import FORMA, ROMPE, mes_rot
    st = _estilos()
    qc_emp = a['contratada']
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=ML, rightMargin=MR, topMargin=3.1 * cm, bottomMargin=2.1 * cm,
                            title='Termo de Acordo nº %s' % a['numero'], author=_nome_curto(qc_emp['razao']), invariant=1)
    doc.rodape = ' · '.join(x for x in (rodape, 'Termo de Acordo nº %s' % a['numero'], _nome_curto(qc_emp['razao']),
                                         'CNPJ %s' % qc_emp['cnpj']) if x)
    doc.cabecalho = qc_emp.get('cabecalho')
    dev, ass, qc = a['devedora'], a['assina'], a['qc']
    pl = a['plano']
    ent = next((p for p in pl if p[3]), None)
    normais = [p for p in pl if not p[3]]
    n = len(normais)
    el = []

    # ── folha de rosto ──
    esq = [Paragraph('TERMO DE ACORDO E CONFISSÃO DE DÍVIDA', st['faixa_k']), Paragraph('Acordo nº %s' % _esc(a['numero']), st['faixa_t'])]
    dir_ = [Paragraph('TOTAL DO ACORDO', st['faixa_rk']), Paragraph(_brl(a['total']), st['faixa_rv']),
            Paragraph(_esc(('entrada + ' if ent else '') + '%dx de %s · sem juros' % (n, _brl(normais[0][2]))), st['faixa_r'])]
    f = Table([[esq, dir_]], colWidths=[LW * .58, LW * .42])
    f.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), TEAL), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                           ('LEFTPADDING', (0, 0), (-1, -1), 14), ('RIGHTPADDING', (0, 0), (-1, -1), 14),
                           ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12)]))
    el += [f, Spacer(1, 8)]
    q1 = [Paragraph('DEVEDORA', st['k']), Paragraph(_esc(dev['razao']), st['nome']), Paragraph(_esc(dev.get('doc_txt') or ''), st['mi'])]
    q2 = [Paragraph('QUEM ASSINA PELA DEVEDORA', st['k']), Paragraph(_esc(_pessoa(ass.get('nome')) or '—'), st['nome']),
          Paragraph(_esc(' · '.join(x for x in (ass.get('cargo'), ('CPF ' + ass['cpf']) if ass.get('cpf') else '') if x)), st['mi'])]
    q3 = [Paragraph('CREDORA · QUALICONTAX', st['k']), Paragraph(_esc(_pessoa(qc.get('nome')) or '—'), st['nome']),
          Paragraph(_esc(qc.get('cargo') or ''), st['mi']), Paragraph(_esc(_nome_curto(qc_emp['razao'])), st['mi'])]
    t3 = Table([[q1, q2, q3]], colWidths=[LW * .40, LW * .32, LW * .28])
    t3.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), _FUNDO), ('BOX', (0, 0), (-1, -1), 0.6, _LINHA2),
                            ('LINEBEFORE', (1, 0), (-1, -1), 0.6, _LINHA2), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('LEFTPADDING', (0, 0), (-1, -1), 11), ('RIGHTPADDING', (0, 0), (-1, -1), 11),
                            ('TOPPADDING', (0, 0), (-1, -1), 9), ('BOTTOMPADDING', (0, 0), (-1, -1), 9)]))
    el.append(t3)

    def kpi(k, v, s=''):
        return [Paragraph(k, st['kpi_k']), Paragraph(_esc(v), st['kpi_v']), Paragraph(_esc(s), st['kpi_s'])]

    desc_txt = _brl(a['desconto']) if a['desconto'] else 'nenhum'
    tk = Table([[kpi('DÍVIDA', _brl(a['divida']), '%d ite%s' % (len(a['itens']), 'm' if len(a['itens']) == 1 else 'ns')),
                 kpi('DESCONTO', desc_txt, a.get('motivo_desconto') or ''),
                 kpi('JUROS E CORREÇÃO', 'zero', 'em todo o acordo'),
                 kpi('ENTRADA', _brl(ent[2]) if ent else 'sem entrada', ('vence ' + ent[1].strftime('%d/%m/%Y')) if ent else '')],
                [kpi('PARCELAS', '%dx %s' % (n, _brl(normais[0][2])), 'a última acerta os centavos' if n > 1 and normais[-1][2] != normais[0][2] else ''),
                 kpi('VENCIMENTO', 'dia %d' % a['dia'], 'o mesmo do honorário' if a['forma'] == 'JUNTO' else 'de cada mês'),
                 kpi('DE / ATÉ', '%s a %s' % (mes_rot(normais[0][1], True), mes_rot(normais[-1][1], True)) if n > 1 else mes_rot(normais[0][1], True), FORMA[a['forma']]),
                 kpi('SE NÃO PAGAR', ROMPE[a['rompe']][1], ROMPE[a['rompe']][2])]],
               colWidths=[LW / 4] * 4)
    tk.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, _LINHA2), ('INNERGRID', (0, 0), (-1, -1), 0.4, _LINHA2),
                            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 10),
                            ('RIGHTPADDING', (0, 0), (-1, -1), 8), ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))
    el += [Paragraph('RESUMO DO ACORDO', st['sec']), tk]

    def tabela(cab, linhas, larg):
        dados = [[Paragraph('<b>%s</b>' % c, st['k']) for c in cab]] + \
                [[Paragraph(_esc(v), st['sv_i'] if j < len(r) - 1 else st['sv_a']) for j, v in enumerate(r)] for r in linhas]
        t = Table(dados, colWidths=larg, repeatRows=1)
        t.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.6, _LINHA2), ('LINEBELOW', (0, 0), (-1, -2), 0.4, _LINHA2),
                               ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('BACKGROUND', (0, 0), (-1, 0), _FUNDO2),
                               ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
                               ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
        return t

    linhas = [(i['descricao'], i['vencimento'].strftime('%d/%m/%Y') if i.get('vencimento') else '—', _brl(i['valor'])) for i in a['itens']]
    if a['desconto']:
        linhas.append(('Desconto' + (' — ' + a['motivo_desconto'] if a.get('motivo_desconto') else ''), '', '− ' + _brl(a['desconto'])))
    linhas.append(('Total do acordo', '', _brl(a['total'])))
    el += [Paragraph('O QUE ENTRA NO ACORDO', st['sec']), tabela(['TÍTULO', 'VENCEU EM', 'VALOR'], linhas, [LW * .55, LW * .22, LW * .23])]
    passos = [('1', 'Link', 'O termo vai por e-mail e WhatsApp para quem assina pela devedora.'),
              ('2', 'Código', 'Confere o acordo e confirma com o código que chega no e-mail. Fica registrado data, hora e IP.'),
              ('3', 'Parcelas', 'Assinou: os títulos acima ficam "renegociados no %s" e as parcelas começam a ser cobradas.' % a['numero'])]
    cel = [[Paragraph(nn, st['passo_n']), [Paragraph(tt, st['passo_t']), Paragraph(tx, st['passo_s'])]] for nn, tt, tx in passos]
    linha = Table([[Table([c], colWidths=[0.8 * cm, LW / 3 - 0.8 * cm - 8],
                          style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0),
                                            ('RIGHTPADDING', (0, 0), (-1, -1), 4)])) for c in cel]], colWidths=[LW / 3] * 3)
    linha.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 8)]))
    el += [KeepTogether([Paragraph('COMO ASSINAR', st['sec']), linha]), PageBreak()]

    # ── o termo ──
    el += [_faixa_titulo('TERMO DE ACORDO E CONFISSÃO DE DÍVIDA', st), Spacer(1, 4),
           Paragraph('Acordo nº <b>%s</b>' % _esc(a['numero']), st['centro']), Spacer(1, 10)]
    rep = ''
    if ass.get('nome'):
        rep = ', neste ato representada por seu %s, %s%s' % (ass.get('cargo') or 'representante', _pessoa(ass['nome']),
                                                           (', portador(a) do CPF nº %s' % ass['cpf']) if ass.get('cpf') else '')
    partes = ['%s, inscrita no %s%s%s, doravante DEVEDORA.' % (dev['razao'], dev.get('doc_txt') or '',
                                                              (', com sede em %s' % dev['endereco']) if dev.get('endereco') else '', rep),
              '%s, inscrita no CNPJ nº %s, com sede na %s, neste ato representada por seu %s, %s, doravante CREDORA ou QUALICONTAX.'
              % (qc_emp['razao'], qc_emp['cnpj'], qc_emp.get('endereco') or '', qc.get('cargo') or 'sócio', _pessoa(qc.get('nome')))]
    for i, p in enumerate(partes, 1):
        el += [Paragraph('%d · %s' % (i, 'DEVEDORA' if i == 1 else 'CREDORA'), st['parte_n']), Paragraph(_rico(p, parte=True), st['base'])]
    el += [Spacer(1, 8), Paragraph('As partes acima têm, entre si, justo e acordado o presente Termo de Acordo e Confissão de Dívida, '
                                   'que se rege pelas cláusulas seguintes.', st['base'])]
    ult = normais[-1]
    pag = 'A dívida será paga '
    if ent:
        pag += 'com entrada de %s, vencendo em %s, e o restante ' % (_brl(ent[2]), ent[1].strftime('%d/%m/%Y'))
    if n == 1:
        pag += 'em parcela única de %s, vencendo em %s.' % (_brl(ult[2]), ult[1].strftime('%d/%m/%Y'))
    else:
        pag += ('em %d (%s) parcelas mensais e sucessivas de %s, vencendo a primeira em %s e as demais no dia %d (%s) dos meses '
                'seguintes, até %s' % (n, EXT.get(n, str(n)), _brl(normais[0][2]), normais[0][1].strftime('%d/%m/%Y'), a['dia'],
                                       DIA_EXT.get(a['dia'], str(a['dia'])), ult[1].strftime('%d/%m/%Y')))
        pag += (', quando a última parcela será de %s, acertando os centavos.' % _brl(ult[2])) if ult[2] != normais[0][2] else '.'
    p_forma = ('PARÁGRAFO PRIMEIRO. Cada parcela será cobrada no mesmo boleto do honorário mensal, como uma linha à parte, para que '
               'a DEVEDORA pague um boleto só por mês.' if a['forma'] == 'JUNTO' else
               'PARÁGRAFO PRIMEIRO. Cada parcela será cobrada em boleto próprio, enviado à DEVEDORA antes do vencimento.')
    p2 = ('PARÁGRAFO SEGUNDO. Se o contrato de prestação de serviços for encerrado antes do fim do acordo, as parcelas restantes passam '
          'a ser cobradas em boleto separado, mantidos o valor e o dia de vencimento.') if a['forma'] == 'JUNTO' else None
    claus = [
        ('CLÁUSULA PRIMEIRA — DA DÍVIDA.', [
            'A DEVEDORA reconhece e confessa dever à CREDORA a quantia líquida, certa e exigível de %s, referente aos valores de '
            'prestação de serviços contábeis vencidos e não pagos listados no quadro "O que entra no acordo", que integra este termo.'
            % _brl(a['divida'])] + ([
            'PARÁGRAFO ÚNICO. A CREDORA concede desconto de %s, e o valor do acordo passa a ser %s. O desconto vale só se o acordo '
            'for cumprido: se for rompido, o desconto é cancelado (cláusula quarta).' % (_brl(a['desconto']), _brl(a['total']))]
            if a['desconto'] and a['rompe'] != 'SEM' else [
            'PARÁGRAFO ÚNICO. A CREDORA concede desconto de %s, e o valor do acordo passa a ser %s.'
            % (_brl(a['desconto']), _brl(a['total']))] if a['desconto'] else [])),
        ('CLÁUSULA SEGUNDA — DO PAGAMENTO.', [pag, p_forma] + ([p2] if p2 else [])),
        ('CLÁUSULA TERCEIRA — SEM JUROS E SEM CORREÇÃO.', ['Sobre a dívida e sobre as parcelas deste acordo não incidem juros nem correção monetária.']),
        ('CLÁUSULA QUARTA — DO ROMPIMENTO.', [_clausula_rompe(a)] + (
            ['PARÁGRAFO ÚNICO. O rompimento não acrescenta juros, multa ou correção ao saldo.'] if a['rompe'] != 'SEM' else [])),
        ('CLÁUSULA QUINTA — DA QUITAÇÃO.', [
            'Com a assinatura, os títulos listados passam a constar como renegociados neste acordo. Paga a última parcela, a CREDORA '
            'dá à DEVEDORA plena e geral quitação dos valores listados, para nada mais reclamar a esse título.']),
        ('CLÁUSULA SEXTA — DO CONTRATO DE PRESTAÇÃO DE SERVIÇOS.', [
            'Este termo não altera o contrato de prestação de serviços entre as partes: os honorários mensais continuam devidos '
            'normalmente, junto com as parcelas deste acordo.']),
        ('CLÁUSULA SÉTIMA — DO TÍTULO EXECUTIVO.', [
            'A DEVEDORA reconhece este termo como título executivo extrajudicial, nos termos do art. 784, inciso III e § 4º, do '
            'Código de Processo Civil, assinado eletronicamente pelas partes.']),
        ('CLÁUSULA OITAVA — DO FORO.', ['Fica eleito o foro da Comarca de São Paulo/SP para dirimir qualquer questão oriunda deste termo.']),
    ]
    for titulo, paras in claus:
        for j, p in enumerate(paras):
            if j == 0:
                el.append(Paragraph('<font color="#0f3d2e"><b>%s</b></font> %s' % (_esc(titulo), _rico(p)), st['clausula']))
            else:
                pt, _, resto = p.partition('. ')
                el.append(Paragraph('<b>%s.</b> %s' % (_esc(pt), _rico(resto)), st['clausula']))
    lp = [(('Entrada' if e else '%d/%d' % (nn, n)), v.strftime('%d/%m/%Y'), _brl(val)) for nn, v, val, e in pl] + [('Total', '', _brl(a['total']))]
    el += [Spacer(1, 6), KeepTogether([Paragraph('PARCELAS DO ACORDO', st['sec']), tabela(['PARCELA', 'VENCIMENTO', 'VALOR'], lp, [LW * .40, LW * .35, LW * .25])])]
    el += [Spacer(1, 10), Paragraph('E, por estarem justas e acordadas, as partes assinam eletronicamente este termo, que produz efeitos '
                                    'a partir da última assinatura.', st['base'])]

    def cartao(rot, nome, sub):
        t = Table([[[Paragraph('ASSINATURA ELETRÔNICA', st['card_st_wait']), Spacer(1, 3), Paragraph(_esc(_pessoa(nome) or '—'), st['card_nome']),
                     Paragraph(_esc(sub), st['card_sub']), Paragraph(_esc(rot), st['card_sub']), Paragraph('aguardando', st['card_sub'])]]],
                  colWidths=[LW / 2 - 6])
        t.setStyle(_caixa(_FUNDO, _LINHA2, pad=10))
        return t

    fim = [Spacer(1, 14), Paragraph('<b>%s</b>' % _esc(a['cidade_data']), st['centro']), Spacer(1, 12),
           Table([['']], colWidths=[LW], style=[('LINEBELOW', (0, 0), (-1, -1), 1.0, _VERDE2)]),
           Paragraph('ASSINATURAS', st['parte_n']), Spacer(1, 8)]
    par = Table([[cartao('pela DEVEDORA · ' + dev['razao'], ass.get('nome'), ('CPF ' + ass['cpf']) if ass.get('cpf') else ''),
                  cartao('pela CREDORA · ' + _nome_curto(qc_emp['razao']), qc.get('nome'), qc.get('cargo') or '')]], colWidths=[LW / 2] * 2)
    par.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    fim.append(par)
    el.append(KeepTogether(fim))
    doc.build(el, onFirstPage=_cabecalho, onLaterPages=_cabecalho)
    return buf.getvalue()


def dados_pdf(numero, cliente, endereco, v, quando=None):
    """Monta o dict de gerar_pdf a partir do que validar() devolve (prévia) — ou de um acordo gravado (ver de_acordo)."""
    from models.prest_contrato import PrestContrato
    from utils.prest_contrato_pdf import _doc
    from utils.acordos import hoje
    MESES = ['', 'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']
    h = quando or hoje()
    qc_emp = PrestContrato.contratada()
    return {'numero': numero, 'contratada': qc_emp,
            'devedora': {'razao': cliente['nome_razao_social'], 'doc_txt': _doc(cliente.get('cpf_cnpj')).replace('CNPJ ', 'CNPJ nº ').replace('CPF ', 'CPF nº '),
                         'endereco': endereco},
            'assina': v['assina'], 'qc': v['qc'],
            'itens': [{'descricao': t['desc'], 'vencimento': t['vencimento'], 'valor': t['saldo']} for t in v['titulos']] +
                     [{'descricao': ds, 'vencimento': None, 'valor': vl} for ds, vl in v['outros']],
            'divida': v['divida'], 'desconto': v['desconto'], 'motivo_desconto': v.get('motivo_desconto'), 'total': v['total'],
            'plano': v['plano'], 'forma': v['forma'], 'rompe': v['rompe'], 'dia': v['dia'],
            'cidade_data': '%s, %d de %s de %d.' % (qc_emp.get('cidade') or 'São Paulo', h.day, MESES[h.month], h.year)}


def de_acordo(a):
    """dict de gerar_pdf a partir de um acordo gravado (utils.acordos.get)."""
    from models.prest_contrato import PrestContrato
    e = PrestContrato.empresa(a['cliente_id']) or {}
    v = {'assina': {'nome': a.get('assina_nome'), 'cpf': a.get('assina_cpf'), 'cargo': a.get('assina_cargo')},
         'qc': {'nome': a.get('qc_nome'), 'cpf': a.get('qc_cpf'), 'cargo': a.get('qc_cargo')},
         'titulos': [{'desc': i['descricao'], 'vencimento': i['vencimento'], 'saldo': i['valor']} for i in a['itens'] if i.get('titulo_id')],
         'outros': [(i['descricao'], i['valor']) for i in a['itens'] if not i.get('titulo_id')],
         'divida': a['divida'], 'desconto': a['desconto'], 'motivo_desconto': a.get('motivo_desconto'), 'total': a['total'],
         'plano': [(p['n'], p['vencimento'], p['valor'], bool(p['entrada'])) for p in a['parcelas']],
         'forma': a['forma'], 'rompe': a['rompe'], 'dia': a['dia']}
    return dados_pdf(a['numero'], {'nome_razao_social': a['nome_razao_social'], 'cpf_cnpj': a['cpf_cnpj']}, e.get('endereco') or '', v,
                     quando=a['criado_em'].date() if a.get('criado_em') else None)
