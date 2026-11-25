# -*- coding: utf-8 -*-
"""E-mails do orçamento no molde do e-mail de cobrança (21/11/2026 — Anderson: "e-mail horrível").
Três e-mails, um modelo (templates/emails/orcamento.html): a proposta, o código de aceite e a confirmação do aceite."""
from datetime import date
from decimal import Decimal

from utils.cobranca_email import _env

SLOGAN = 'a EMPRESA é sua, a PARCERIA é NOSSA'


def _brl(v):
    return 'R$ ' + '{:,.2f}'.format(Decimal(str(v or 0))).replace(',', 'X').replace('.', ',').replace('X', '.')


def _doc(d):
    d = ''.join(ch for ch in str(d or '') if ch.isdigit())
    if len(d) == 14:
        return 'CNPJ %s.%s.%s/%s-%s' % (d[:2], d[2:5], d[5:8], d[8:12], d[12:])
    if len(d) == 11:
        return 'CPF %s.%s.%s-%s' % (d[:3], d[3:6], d[6:9], d[9:])
    return ''


def _base(o, titulo, eyebrow, saudacao, intro):
    doc = _doc(o.get('cliente_doc'))
    resp = o.get('criado_por_nome') or 'Qualicontax'
    n_mais = len(o.get('empresas_mais') or [])
    return {'titulo': titulo, 'eyebrow': eyebrow, 'data_txt': date.today().strftime('%d/%m/%Y'),
            'cliente_nome': o['cliente_nome'] + ((' e mais %d empresa%s' % (n_mais, 's' if n_mais > 1 else '')) if n_mais else ''), 'cliente_doc': doc,
            'cliente_rotulo': 'Empresa' if doc.startswith('CNPJ') else 'Cliente', 'saudacao': saudacao, 'intro': intro,
            'kpis': [], 'itens': [], 'total': '', 'botao': None, 'passos': [], 'codigo': '', 'aviso': '', 'anexos_txt': '',
            'slogan': SLOGAN, 'rodape': 'Enviado por %s, da Qualicontax Assessoria Contábil. Em caso de dúvida, responda este e-mail.' % resp}


def _itens(itens):
    return [{'nome': i['descricao'], 'valor': _brl(i['valor_total']), 'obs': '' if i['emite_nota'] else 'taxa repassada · só no boleto'} for i in itens]


def _kpis(o):
    from models.orcamento import COMO
    k = [('Total', _brl(o['total']))]
    if o.get('validade'):
        k.append(('Validade', o['validade'].strftime('%d/%m/%Y')))
    if o.get('como'):
        k.append(('Pagamento', COMO[o['como']] + ((' · %dx' % o['parcelas']) if o.get('parcelas') and o['parcelas'] > 1 else '')))
    return k


def _nome(o):
    return (o.get('solicitante_nome') or '').split(' ')[0].title()


def _texto(d, extras=()):
    t = [d['titulo'], d['cliente_nome'] + ((' · ' + d['cliente_doc']) if d['cliente_doc'] else ''), '', d['saudacao'], d['intro'], '']
    if d['codigo']:
        t += ['CÓDIGO: ' + d['codigo'] + ' (vale 30 minutos)', '']
    t += ['%s: %s' % (r, v) for r, v in d['kpis']]
    if d['itens']:
        t += ['', 'SERVIÇOS'] + ['  %s: %s%s' % (i['nome'], i['valor'], (' (' + i['obs'] + ')') if i['obs'] else '') for i in d['itens']] + ['  Total: ' + d['total']]
    if d['botao']:
        t += ['', '%s: %s' % d['botao']]
    t += list(extras)
    if d['aviso']:
        t += ['', d['aviso']]
    t += ['', d['slogan'], '', d['rodape']]
    return '\n'.join(t)


def proposta(o, itens, link):
    """E-mail que leva a proposta: itens, botão "Ver e aceitar", passos; o PDF vai anexado."""
    nome = _nome(o)
    d = _base(o, 'Orçamento %s' % o['numero'], 'Proposta de serviços', 'Olá%s!' % ((', ' + nome) if nome else ''),
              'Segue a nossa proposta%s. O orçamento completo, com as condições, está no PDF em anexo. Para aceitar, é só tocar no botão abaixo: você recebe um código neste e-mail, confirma seus dados e o aceite fica registrado com data e hora.'
              % ((' para ' + o['titulo'].lower()) if o.get('titulo') else ''))
    d['kpis'] = _kpis(o); d['itens'] = _itens(itens); d['total'] = _brl(o['total'])
    d['botao'] = ('Ver e aceitar a proposta', link)
    d['passos'] = [('Aceite.', 'Toque no botão, confirme seus dados e o código que chega por e-mail.'),
                   ('Execução.', 'A Qualicontax executa o serviço e acompanha cada etapa.'),
                   ('Cobrança.', 'Sai como combinado, com o comprovante por e-mail.')]
    d['anexos_txt'] = 'Em anexo: o PDF do orçamento.'
    return 'Orçamento %s · %s' % (o['numero'], o['cliente_nome']), _env().get_template('orcamento.html').render(d=d), _texto(d)


def codigo(o, cod):
    d = _base(o, 'Código de aceite', 'Orçamento %s' % o['numero'], 'Olá!', 'Este é o seu código para aceitar o orçamento %s. Digite-o na página da proposta. Se não foi você que pediu, ignore este e-mail.' % o['numero'])
    d['codigo'] = cod
    return 'Código %s para aceitar o orçamento %s' % (cod, o['numero']), _env().get_template('orcamento.html').render(d=d), _texto(d)


def aceite(o, itens, para_equipe=False):
    quando = o['aceito_em'].strftime('%d/%m/%Y às %H:%M')
    if para_equipe:
        d = _base(o, 'Orçamento %s aceito' % o['numero'], 'Aceite do cliente', '%s aceitou!' % (o.get('aceito_nome') or 'O cliente'),
                  'O orçamento %s (%s) foi aceito em %s%s. Agora é fazer o serviço e, feito, mandar para a cobrança em Comercial › Orçamentos. A via com o registro do aceite está em anexo.'
                  % (o['numero'], o['cliente_nome'], quando, (' por %s' % o['aceito_nome']) if o.get('aceito_nome') else ''))
    else:
        nome = (o.get('aceito_nome') or '').split(' ')[0].title()
        d = _base(o, 'Proposta aceita', 'Orçamento %s' % o['numero'], 'Obrigado%s!' % ((', ' + nome) if nome else ''),
                  'Seu aceite do orçamento %s foi registrado em %s. A Qualicontax já foi avisada e dá seguimento ao serviço. Em anexo vai a via da proposta com o registro do aceite.' % (o['numero'], quando))
    d['kpis'] = _kpis(o); d['itens'] = _itens(itens); d['total'] = _brl(o['total'])
    d['aviso'] = 'Registro do aceite: %s%s · %s · e-mail %s · IP %s.' % (o.get('aceito_nome') or '', (' (CPF %s)' % o['aceito_cpf']) if o.get('aceito_cpf') else '', quando, o.get('aceito_email') or '', o.get('aceito_ip') or '')
    d['anexos_txt'] = 'Em anexo: a via aceita, com a página de registro.'
    return ('%s aceitou o orçamento %s' % (o.get('aceito_nome'), o['numero'])) if para_equipe else ('Orçamento %s aceito · %s' % (o['numero'], o['cliente_nome'])), _env().get_template('orcamento.html').render(d=d), _texto(d)
