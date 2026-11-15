# -*- coding: utf-8 -*-
"""Logo do banco em todas as telas — 15/11/2026.

Anderson (15/11/2026): "os icons dos bancos estão sendo alterados em todos lugares que aparecem os
Bancos?" … "pode subir mas vamos arrumar". Um lugar só decide: o arquivo
``static/images/bancos/<código de 3 dígitos>.svg``. Banco sem arquivo continua com o quadradinho
na cor da marca (como era antes), então nenhuma tela fica sem nada.

Uso: no Jinja ``{{ logo_banco(banco_id, cor) }}``; no JavaScript, as telas recebem
``logos_banco()`` ({'237': '/static/images/bancos/237.svg', ...}) e montam o <img> igual.
"""
import os
import re

from markupsafe import Markup, escape

_PASTA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'images', 'bancos')
_CACHE = {}


# Nome como o arquivo do banco escreve -> código (o extrato guarda o texto do arquivo: 'Banco C6 S.A.',
# 'NU PAGAMENTOS S.A.', 'CCPI DO CERRADO DE GO' é o OFX do Sicredi, '0237' é o OFX do Bradesco)
_POR_NOME = [
    (r'bradesco', '237'), (r'\bc6\b', '336'), (r'\bcora\b', '403'), (r'\bef[ií]\b|gerencianet', '364'),
    (r'sicredi|ccpi|cerrado', '748'), (r'nubank|nu pagamentos', '260'), (r'\bxp\b', '348'),
    (r'mercado ?pago', '323'), (r'ita[uú]', '341'), (r'santander', '033'), (r'caixa', '104'),
    (r'banco do brasil|^bb\b', '001'), (r'\binter\b', '077'), (r'btg', '208'), (r'safra', '422'),
    (r'sicoob', '756'),
]


def codigo(banco_id):
    """'0237', '237', 237, 'Banco C6 S.A.' -> código de 3 dígitos; '' / None / desconhecido -> None."""
    t = str(banco_id or '').strip()
    if not t:
        return None
    if re.fullmatch(r'\d+', t):
        d = t.lstrip('0')
        return d.zfill(3) if d else None
    low = t.lower()
    for rx, c in _POR_NOME:
        if re.search(rx, low):
            return c
    return None


def _arquivos():
    if 'c' not in _CACHE:
        try:
            _CACHE['c'] = {f[:-4] for f in os.listdir(_PASTA) if f.endswith('.svg')}
        except OSError:
            _CACHE['c'] = set()
    return _CACHE['c']


def url(banco_id):
    c = codigo(banco_id)
    return f'/static/images/bancos/{c}.svg' if c and c in _arquivos() else None


def logos_banco():
    """{'237': url, ...} — chave sem zeros à esquerda, como as telas comparam o banco_id."""
    return {c.lstrip('0'): f'/static/images/bancos/{c}.svg' for c in sorted(_arquivos())}


def logo_banco(banco_id, cor=None, tam=20, titulo=''):
    """<img> do logo, ou o quadradinho na cor da marca quando o banco não tem logo."""
    u = url(banco_id)
    if u:
        return Markup(f'<img class="bco-logo" src="{u}" alt="" width="{int(tam)}" height="{int(tam)}"'
                      f' style="width:{int(tam)}px;height:{int(tam)}px" title="{escape(titulo)}">')
    return Markup(f'<i class="bco-ponto" style="background:{escape(cor or "#94a3b8")}"></i>')
