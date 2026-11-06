# -*- coding: utf-8 -*-
"""Valor por extenso em reais, como vai no contrato: 1.095,00 → "um mil e noventa e cinco reais"."""
from decimal import Decimal

_UNID = ['', 'um', 'dois', 'três', 'quatro', 'cinco', 'seis', 'sete', 'oito', 'nove', 'dez', 'onze', 'doze', 'treze',
         'quatorze', 'quinze', 'dezesseis', 'dezessete', 'dezoito', 'dezenove']
_DEZ = ['', '', 'vinte', 'trinta', 'quarenta', 'cinquenta', 'sessenta', 'setenta', 'oitenta', 'noventa']
_CEN = ['', 'cento', 'duzentos', 'trezentos', 'quatrocentos', 'quinhentos', 'seiscentos', 'setecentos', 'oitocentos', 'novecentos']
_ESC = [('', ''), ('mil', 'mil'), ('milhão', 'milhões'), ('bilhão', 'bilhões')]


def _ate_999(n):
    if n == 0:
        return ''
    if n == 100:
        return 'cem'
    c, r = divmod(n, 100)
    partes = []
    if c:
        partes.append(_CEN[c])
    if r:
        if r < 20:
            partes.append(_UNID[r])
        else:
            d, u = divmod(r, 10)
            partes.append(_DEZ[d] + (' e ' + _UNID[u] if u else ''))
    return ' e '.join(partes)


def inteiro_extenso(n):
    """1095 → 'um mil e noventa e cinco'; 1377 → 'um mil, trezentos e setenta e sete'; 1500 → 'um mil e quinhentos'."""
    n = int(n)
    if n == 0:
        return 'zero'
    grupos, i = [], 0               # [(valor_do_grupo, texto)] do maior para o menor
    while n:
        n, g = divmod(n, 1000)
        if g:
            singular, plural = _ESC[i]
            txt = _ate_999(g) + ((' ' + (singular if g == 1 else plural)) if singular else '')
            grupos.append((g, txt.strip()))
        i += 1
    grupos.reverse()
    if len(grupos) == 1:
        return grupos[0][1]
    g_ult, t_ult = grupos[-1]
    liga = ' e ' if (g_ult < 100 or g_ult % 100 == 0) else ', '
    return ', '.join(t for _, t in grupos[:-1]) + liga + t_ult


def reais_extenso(valor):
    v = Decimal(str(valor)).quantize(Decimal('0.01'))
    reais, cents = int(v), int((v - int(v)) * 100)
    partes = []
    if reais:
        txt = inteiro_extenso(reais)
        de = ' de' if txt.endswith(('milhão', 'milhões', 'bilhão', 'bilhões')) else ''
        partes.append(txt + de + (' real' if reais == 1 else ' reais'))
    if cents:
        partes.append(inteiro_extenso(cents) + (' centavo' if cents == 1 else ' centavos'))
    return ' e '.join(partes) if partes else 'zero reais'
