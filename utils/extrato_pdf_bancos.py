# -*- coding: utf-8 -*-
"""Extratos em PDF de Sicredi, Efí e Bradesco (14/09/2026).

Cada banco desenha o PDF de um jeito; cada leitor abaixo foi escrito olhando
o arquivo real e reconhece o SEU layout pela capa. Todos devolvem o mesmo
dicionário do ``parse_ofx``: banco, banco_id, conta, lancamentos [{data,
data_contabil, valor, descricao, documento, fitid, tipo}].

Como o PDF não traz o identificador do OFX, a deduplicação contra o que já
está no banco é por data + valor (+ descrição igual, quando dá) — o mesmo
casamento do PDF do C6 — e o Efí e o Bradesco ainda trazem o número do
documento (protocolo / Dcto), que o OFX também guarda.

SICREDI  capa 'Cooperativa: 3950' + 'Conta: 15639-0'; linhas
         data / descrição / [documento] / valor / saldo. O documento pode
         faltar (boleto) — o valor é a linha com vírgula e sinal, o saldo a
         seguinte.
EFÍ      capa 'Extrato financeiro' + 'Protocolo'; linhas
         data / descrição (1..n linhas) / protocolo (10 dígitos) / valor com
         sinal ('+1.500,00', '-12,80'); 'Saldo do dia' é pulado. Sem conta na
         capa: a identificação é pelo protocolo já gravado.
BRADESCO capa 'Extrato de: Ag:' + 'CC:'; linhas [data] / lançamento (1..n)
         / dcto / valor / saldo — a data só aparece quando muda.
"""
import re
from decimal import Decimal

_RE_DATA = re.compile(r'^(\d{2})/(\d{2})/(\d{4})$')
_RE_NUM = re.compile(r'^([+-]?)\s*([\d.]+),(\d{2})$')


class PdfBancoInvalido(Exception):
    pass


def _dec(txt):
    m = _RE_NUM.match((txt or '').strip().replace('\xa0', '').replace(' ', ''))
    if not m:
        return None
    v = Decimal(m.group(2).replace('.', '') + '.' + m.group(3))
    return -v if m.group(1) == '-' else v


def _iso(txt):
    m = _RE_DATA.match((txt or '').strip())
    return f'{m.group(3)}-{m.group(2)}-{m.group(1)}' if m else None


def _lanc(data, valor, descricao, documento=None):
    return {'data': data, 'data_contabil': data, 'valor': valor,
            'tipo': 'credito' if valor >= 0 else 'debito',
            'descricao': ' '.join((descricao or '').split())[:500],
            'documento': (documento or None), 'fitid': None}


# ---------------------------------------------------------------------------
# SICREDI
# ---------------------------------------------------------------------------
def e_sicredi(t):
    return 'Cooperativa:' in t and 'Conta:' in t and 'Associado:' in t


def parse_sicredi(paginas):
    cab = paginas[0]
    coop = re.search(r'Cooperativa:\s*(\d+)', cab)
    cc = re.search(r'Conta:\s*([\d.-]+)', cab)
    if not cc:
        raise PdfBancoInvalido('extrato do Sicredi sem o número da conta na capa.')
    conta = f'{coop.group(1)}/{cc.group(1)}' if coop else cc.group(1)
    lancs = []
    linhas = [l.strip() for p in paginas for l in p.splitlines()]
    # 'Lançamentos Futuros (Próximos 30 dias)' fecha o extrato: o que vem
    # depois é agendamento, não movimento.
    corte = next((k for k, l in enumerate(linhas) if l.startswith('Lançamentos Futuros')), None)
    if corte is not None:
        linhas = linhas[:corte]
    i, n = 0, len(linhas)
    while i < n:
        d = _iso(linhas[i])
        if not d:
            i += 1
            continue
        # descrição, [documento], valor, saldo
        j = i + 1
        desc = linhas[j] if j < n else ''
        j += 1
        doc = None
        if j < n and _dec(linhas[j]) is None:
            doc = linhas[j]
            j += 1
        v = _dec(linhas[j]) if j < n else None
        if v is None or not desc or _iso(desc):
            i += 1
            continue
        # o 'Documento' do PDF (PIX_DEB, COB000001, nº do boleto) NÃO é o id do
        # OFX: fica de fora, e a deduplicação é por data + valor.
        lancs.append(_lanc(d, v, desc))
        i = j + 2                       # pula o saldo
    if not lancs:
        raise PdfBancoInvalido('extrato do Sicredi sem lançamento legível.')
    return {'banco': 'Sicredi', 'banco_id': '748', 'conta': conta, 'saldo': None, 'lancamentos': lancs}


# ---------------------------------------------------------------------------
# EFÍ
# ---------------------------------------------------------------------------
def e_efi(t):
    return ('Extrato' in t and 'nanceiro' in t and 'Protocolo' in t)


_RE_PROTO = re.compile(r'^\d{8,12}$')


def parse_efi(paginas):
    lancs = []
    linhas = [l.strip() for p in paginas for l in p.splitlines()]
    i, n = 0, len(linhas)
    while i < n:
        d = _iso(linhas[i])
        if not d:
            i += 1
            continue
        if i + 1 < n and linhas[i + 1].lower().startswith('saldo do dia'):
            i += 3
            continue
        # descrição até o protocolo, depois o valor
        desc, j = [], i + 1
        while j < n and not _RE_PROTO.match(linhas[j]) and not _iso(linhas[j]) and len(desc) < 6:
            desc.append(linhas[j])
            j += 1
        if j < n and _RE_PROTO.match(linhas[j]):
            proto = linhas[j]
            v = _dec(linhas[j + 1]) if j + 1 < n else None
            if v is not None and desc:
                lancs.append(_lanc(d, v, ' '.join(desc).replace(' • ', ': ').replace('•', ':').replace('ﬁ', 'fi'), documento=proto))
                i = j + 2
                continue
        i += 1
    if not lancs:
        raise PdfBancoInvalido('extrato da Efí sem lançamento legível.')
    return {'banco': 'Efí', 'banco_id': '364', 'conta': None, 'saldo': None, 'lancamentos': lancs}


# ---------------------------------------------------------------------------
# BRADESCO
# ---------------------------------------------------------------------------
def e_bradesco(t):
    return 'Extrato de: Ag' in t and 'CC:' in t


_RE_DOC = re.compile(r'^\d{5,9}$')


def parse_bradesco(paginas):
    cab = paginas[0]
    m = re.search(r'(\d{4,5})\s*\|\s*(\d{4,}-?\d?)', cab)
    conta = None
    if m:
        conta = f"{int(m.group(1))}/{m.group(2).lstrip('0')}"
    lancs = []
    linhas = [l.strip() for p in paginas for l in p.splitlines()]
    data, desc = None, []
    i, n = 0, len(linhas)
    while i < n:
        l = linhas[i]
        d = _iso(l)
        if d:
            data, desc = d, []
            i += 1
            continue
        if _RE_DOC.match(l) and i + 2 < n and _dec(linhas[i + 1]) is not None and _dec(linhas[i + 2]) is not None:
            v = _dec(linhas[i + 1])
            if data and desc and v != 0:
                lancs.append(_lanc(data, v, ' '.join(desc), documento=l))
            desc = []
            i += 3                      # dcto, valor, saldo
            continue
        if l and _dec(l) is None and not l.startswith('SALDO'):
            if l in ('Data', 'Lançamento', 'Dcto.', 'Crédito (R$)', 'Débito (R$)', 'Saldo (R$)') or 'Extrato de' in l or 'Agência | Conta' in l or 'Total' in l:
                i += 1
                continue
            desc.append(l)
        elif l.startswith('SALDO'):
            desc = []
        i += 1
    if not lancs:
        raise PdfBancoInvalido('extrato do Bradesco sem lançamento legível.')
    return {'banco': 'Bradesco', 'banco_id': '237', 'conta': conta, 'saldo': None, 'lancamentos': lancs}
