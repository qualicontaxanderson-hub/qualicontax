# -*- coding: utf-8 -*-
"""Tabelas de códigos do IRPF (bens, pagamentos, isentos, exclusivos, dependentes, dívidas,
ocupações, países), copiadas de ``IRPF2024\\lib\\resources\\*.xml`` do programa da Receita para
``leiautes/tabelas.json``. Servem para a tela dizer "01|02 Prédio comercial" em vez do código.
"""
import json
import os

_ARQ = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'leiautes', 'tabelas.json')
_T = None


def _t():
    global _T
    if _T is None:
        with open(_ARQ, encoding='utf-8') as f:
            _T = json.load(f)
    return _T


def _itens(secao):
    s = _t().get(secao) or {}
    return s.get('itens') or {}


def _norm2(cod):
    c = str(cod or '').strip()
    if c.isdigit():
        return f'{int(c):02d}'
    return c


def bem(grupo, codigo, curto=False):
    t = _t().get('bens') or {}
    k = f'{_norm2(grupo)}|{_norm2(codigo)}'
    return (t.get('codigo_curto' if curto else 'codigo') or {}).get(k) or (t.get('codigo') or {}).get(k) or f'código {k}'


def grupo_bem(grupo):
    return ((_t().get('bens') or {}).get('grupo') or {}).get(_norm2(grupo)) or f'grupo {grupo}'


def pagamento(codigo):
    return _itens('pagamentos').get(_norm2(codigo)) or f'código {codigo}'


def isento(codigo):
    return _itens('isentos').get(_norm2(codigo)) or f'código {codigo}'


def exclusivo(codigo):
    return _itens('exclusivos').get(_norm2(codigo)) or f'código {codigo}'


def dependente(codigo, curto=True):
    s = _t().get('dependentes') or {}
    return (s.get('itens_curto' if curto else 'itens') or {}).get(_norm2(codigo)) or (s.get('itens') or {}).get(_norm2(codigo)) or f'código {codigo}'


def divida(codigo):
    return _itens('dividas').get(_norm2(codigo)) or f'código {codigo}'


def ocupacao(codigo):
    c = str(codigo if codigo is not None else '').strip()
    if not c or not c.strip('0'):
        return None
    it = _itens('ocupacoes')
    return it.get(c) or it.get(c.zfill(3)) or f'código {c}'


def pais(codigo):
    c = str(codigo or '').strip()
    it = _itens('paises')
    return it.get(c) or it.get(c.zfill(3)) or (f'país {c}' if c else None)


def tipo_declaracao(letra):
    return (_itens('tipo_declaracao') or {}).get(str(letra or 'A').upper()) or 'Ajuste anual'


BLOCOS = {'pj_titular': 'Recebidos de pessoa jurídica (titular)', 'pj_dependente': 'Recebidos de pessoa jurídica (dependentes)',
          'pf_exterior': 'Recebidos de pessoa física e do exterior (mês a mês)', 'isento': 'Isentos e não tributáveis',
          'exclusivo': 'Tributação exclusiva/definitiva', 'exclusivo_detalhe': 'Tributação exclusiva (detalhe por fonte)'}
PAGAMENTO_TIPO = {'T': 'titular', 'D': 'dependente', 'A': 'alimentando'}


def mod11(s):
    n = len(s)
    soma = sum((n + 1 - i) * int(c) for i, c in enumerate(s))
    dv = 11 - soma % 11
    return 0 if dv > 9 else dv


def numero_recibo(hash10):
    """Recibo de 12 dígitos = NR_HASH (10) + 2 DV (módulo 11 do programa). Lido do bytecode
    do IRPF (IRPFTxtUtil.obtemDVNumRecibo); conferir com um recibo impresso antes de confiar."""
    h = str(hash10 or '').strip()
    if len(h) != 10 or not h.isdigit():
        return None
    d1 = mod11(h)
    d2 = mod11(h + str(d1))
    return h + str(d1) + str(d2)


def recibo_pontuado(rec):
    r = str(rec or '')
    return '.'.join(r[i:i + 2] for i in range(0, len(r), 2)) if r else ''
