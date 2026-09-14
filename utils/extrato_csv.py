# -*- coding: utf-8 -*-
"""Extrato em CSV — leitor por LAYOUT conhecido (14/09/2026).

CSV não é padrão: cada banco escolhe colunas, separador, formato de data e
de número. Por isso o leitor reconhece o layout pelo CABEÇALHO e recusa o
que não conhece — recusar é melhor do que adivinhar coluna de valor.

Layouts conhecidos:
* **Nubank** — ``Data,Valor,Identificador,Descrição``. Mesmo conteúdo do
  OFX, com o mesmo identificador único (vira ``fitid``, então OFX e CSV do
  mesmo mês não duplicam).

O CSV NÃO diz de que conta é. A identificação vem do número da empresa no
nome do arquivo + o banco do layout (``banco_id``): se a empresa tem UMA
conta nesse banco, é ela; senão vira pendência.
"""
import csv
import io
import re
from decimal import Decimal, InvalidOperation


class CsvInvalido(Exception):
    """Não é um CSV de extrato que eu saiba ler."""


#: Layouts por assinatura do cabeçalho (colunas normalizadas, sem acento,
#: minúsculas). Cada layout diz o banco e como mapear as colunas.
LAYOUTS = {
    'nubank': {
        'banco_id': '260', 'banco': 'NU PAGAMENTOS S.A.',
        'assinatura': ('data', 'valor', 'identificador', 'descricao'),
        'col': {'data': 'data', 'valor': 'valor', 'fitid': 'identificador',
                'descricao': 'descricao'},
        'data_fmt': 'dmy',      # 04/02/2026
        'decimal': '.',         # 1000.00
    },
}


def _norm_col(c):
    import unicodedata
    t = unicodedata.normalize('NFKD', str(c or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '', t.lower())


def _decodificar(raw):
    for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('latin-1', errors='replace')


def _data(v, fmt):
    v = (v or '').strip()
    m = re.match(r'^(\d{2})/(\d{2})/(\d{4})$', v)
    if m and fmt == 'dmy':
        return f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', v)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    return None


def _valor(v, decimal):
    s = (v or '').strip().replace('R$', '').replace(' ', '')
    if not s:
        return None
    if decimal == ',':
        s = s.replace('.', '').replace(',', '.')
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def identificar_layout(cabecalho):
    cols = tuple(_norm_col(c) for c in cabecalho)
    for nome, lay in LAYOUTS.items():
        if cols[:len(lay['assinatura'])] == lay['assinatura']:
            return nome, lay
    return None, None


def parse_csv(raw):
    """bytes → dict no espírito do ``parse_ofx``: banco, banco_id, conta=None,
    layout, lancamentos [{data, valor, descricao, documento, fitid, tipo}]."""
    texto = _decodificar(raw)
    amostra = texto[:2000]
    try:
        dialeto = csv.Sniffer().sniff(amostra, delimiters=',;\t')
    except csv.Error:
        dialeto = csv.excel
    leitor = csv.reader(io.StringIO(texto), dialeto)
    linhas = [l for l in leitor if any((c or '').strip() for c in l)]
    if not linhas:
        raise CsvInvalido('CSV vazio.')
    nome, lay = identificar_layout(linhas[0])
    if not lay:
        raise CsvInvalido('CSV com colunas que eu não conheço: '
                          + ', '.join(str(c)[:20] for c in linhas[0][:6])
                          + '. Hoje leio o CSV do Nubank; deste banco mande o OFX.')
    idx = {_norm_col(c): i for i, c in enumerate(linhas[0])}
    lancs = []
    for l in linhas[1:]:
        def g(k):
            i = idx.get(lay['col'][k])
            return l[i] if i is not None and i < len(l) else None
        data = _data(g('data'), lay['data_fmt'])
        valor = _valor(g('valor'), lay['decimal'])
        if data is None or valor is None:
            continue
        lancs.append({
            'data': data, 'valor': valor,
            'tipo': 'credito' if valor >= 0 else 'debito',
            'descricao': (g('descricao') or '').strip()[:500],
            'documento': None,
            'fitid': (g('fitid') or '').strip() or None,
        })
    if not lancs:
        raise CsvInvalido('CSV sem nenhum lançamento legível.')
    return {'banco': lay['banco'], 'banco_id': lay['banco_id'], 'conta': None,
            'layout': nome, 'saldo': None, 'lancamentos': lancs}
