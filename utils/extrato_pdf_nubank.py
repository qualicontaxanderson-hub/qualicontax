# -*- coding: utf-8 -*-
"""Extrato em PDF do Nubank (14/09/2026).

Mesmo conteúdo do OFX e do CSV do Nubank — existe porque o cliente
desorganizado manda o que tem à mão ("um mês PDF, no outro OFX, no outro
Excel"). O PDF esconde o CPF do titular (•••.511.418-••), mas traz agência
e conta na primeira página: a identificação é pela CONTA, como no OFX.

Sem identificador único por linha: a deduplicação contra o OFX/CSV do mesmo
mês é por data + valor + descrição (``casar_por_data_valor``), como no C6.

LAYOUT (texto extraído, uma coisa por linha):
    '04 FEV 2026'                    ← dia
    'Total de entradas'              ← rótulo, ignorado
    '+ 1.000,00'                     ← abre um lançamento (sinal e valor)
    'Transferência recebida pelo Pix'
    'Qualicontax Assessoria ... - 11.158.475'   ← descrição em 1..n linhas
    '1.000,00'                       ← fecha o lançamento (valor sem sinal)
"""
import re
from decimal import Decimal

BANCO_ID = '260'
BANCO_NOME = 'NU PAGAMENTOS S.A.'

_MESES = {'JAN': 1, 'FEV': 2, 'MAR': 3, 'ABR': 4, 'MAI': 5, 'JUN': 6,
          'JUL': 7, 'AGO': 8, 'SET': 9, 'OUT': 10, 'NOV': 11, 'DEZ': 12}
_RE_DIA = re.compile(r'^(\d{2}) ([A-Z]{3}) (\d{4})$')
_RE_ABRE = re.compile(r'^([+-])\s*([\d.]+),(\d{2})$')
_RE_FECHA = re.compile(r'^([\d.]+),(\d{2})$')
_RE_CONTA = re.compile(r'^(\d{3,}-\d)$')


class PdfNubankInvalido(Exception):
    pass


def _descricao(linhas):
    """'Transferência recebida pelo Pix' + 'Qualicontax ... - 11.158.475' +
    '/0001-27 - COOP...' → igual ao OFX: 'Transferência recebida pelo Pix -
    Qualicontax ... - 11.158.475/0001-27 - COOP...'."""
    partes = [x.strip() for x in linhas if x and x.strip()]
    if not partes:
        return ''
    resto = ''
    for x in partes[1:]:
        resto = (resto + x) if (x.startswith('/') or resto.endswith('/')) else (resto + ' ' + x).strip()
    return (f'{partes[0]} - {resto}' if resto else partes[0])[:500]


def e_extrato_nubank(texto_pag1):
    t = texto_pag1 or ''
    return ('nubank' in t.lower() or 'Nu Pagamentos' in t) and 'Movimentações' in t \
        or ('CPF' in t and 'Agência' in t and 'Conta' in t and 'Saldo final do período' in t)


def parse_pdf_nubank(paginas):
    """``paginas``: lista de textos (uma string por página)."""
    if not paginas or not e_extrato_nubank(paginas[0]):
        raise PdfNubankInvalido('não é um extrato do Nubank.')
    cab = [l.strip() for l in paginas[0].splitlines()[:12]]
    agencia = next((l for l in cab if re.fullmatch(r'\d{4}', l)), None)
    conta = next((l for l in cab if _RE_CONTA.match(l)), None)
    if not conta:
        raise PdfNubankInvalido('extrato do Nubank sem o número da conta na capa.')
    conta_fmt = f'{int(agencia)}/{conta}' if agencia else conta
    titular = (cab[0] or '').strip() or None

    lancs = []
    dia = None
    aberto = None
    for pag in paginas:
        for raw in pag.splitlines():
            l = raw.strip()
            md = _RE_DIA.match(l)
            if md and md.group(2) in _MESES:
                dia = f'{int(md.group(3)):04d}-{_MESES[md.group(2)]:02d}-{int(md.group(1)):02d}'
                aberto = None
                continue
            if dia is None:
                continue
            if aberto is None:
                ma = _RE_ABRE.match(l)
                if ma:
                    v = Decimal(ma.group(2).replace('.', '') + '.' + ma.group(3))
                    aberto = {'valor': -v if ma.group(1) == '-' else v, 'desc': []}
                continue
            mf = _RE_FECHA.match(l)
            if mf and Decimal(mf.group(1).replace('.', '') + '.' + mf.group(2)) == abs(aberto['valor']):
                lancs.append({
                    'data': dia, 'data_contabil': dia,
                    'valor': aberto['valor'],
                    'tipo': 'credito' if aberto['valor'] >= 0 else 'debito',
                    'descricao': _descricao(aberto['desc']),
                    'documento': None, 'fitid': None,
                })
                aberto = None
                continue
            if l and not l.startswith('Total de '):
                aberto['desc'].append(l)
    if not lancs:
        raise PdfNubankInvalido('extrato do Nubank sem nenhum lançamento legível.')
    return {'banco': BANCO_NOME, 'banco_id': BANCO_ID, 'conta': conta_fmt,
            'cpf': None, 'titular': titular, 'saldo': None, 'lancamentos': lancs}
