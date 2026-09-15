# -*- coding: utf-8 -*-
"""Extrato em CSV — leitor por LAYOUT conhecido (14/09/2026).

CSV não é padrão: cada banco escolhe colunas, separador, formato de data e
de número, e alguns põem um preâmbulo antes do cabeçalho (C6, Bradesco). Por
isso o leitor procura o CABEÇALHO nas primeiras linhas, reconhece o layout
pela assinatura das colunas e recusa o que não conhece — recusar é melhor
do que adivinhar qual coluna é o valor.

Layouts conhecidos (escritos olhando o arquivo real de cada banco):
* **Nubank**   ``Data,Valor,Identificador,Descrição`` — id único (fitid).
* **Cora**     ``Data,Transação,Tipo Transação,Identificação,Valor`` — sem id.
* **C6**       preâmbulo com ``Agência: 1 / Conta: 211346179``; colunas
               ``Data Lançamento,Data Contábil,Título,Descrição,Entrada(R$),Saída(R$),Saldo``.
* **Bradesco** preâmbulo ``;Extrato de: Agência: 2505  Conta: 16865-3``;
               ``Data;Lançamento;Dcto.;Crédito (R$);Débito (R$);Saldo (R$)`` — Dcto = documento.
* **Efí**      ``"Tipo";"Protocolo";"Data";"Valor"`` — Protocolo = documento
               (o OFX guarda o mesmo número, com zeros à esquerda).

O que o CSV não diz (a conta, na maioria) a identificação resolve por outro
caminho: conta no preâmbulo, documento já gravado, empresa no nome do arquivo.
"""
import csv
import io
import re
from decimal import Decimal, InvalidOperation


class CsvInvalido(Exception):
    """Não é um CSV de extrato que eu saiba ler."""


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


def _data(v):
    v = (v or '').strip()
    m = re.match(r'^(\d{2})/(\d{2})/(\d{4})', v)
    if m:
        return f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', v)
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    return None


def _valor(v, decimal):
    s = (v or '').strip().replace('R$', '').replace(' ', '').replace('\xa0', '')
    if not s:
        return None
    if decimal == ',':
        s = s.replace('.', '').replace(',', '.')
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _lanc(data, valor, descricao, documento=None, fitid=None, data_contabil=None):
    return {'data': data, 'data_contabil': data_contabil or data, 'valor': valor,
            'tipo': 'credito' if valor >= 0 else 'debito',
            'descricao': (descricao or '').strip()[:500],
            'documento': (documento or '').strip()[:80] or None,
            'fitid': (fitid or '').strip()[:120] or None}


# ---------------------------------------------------------------------------
# Os layouts. ``mapear(g)`` recebe um acessor g('coluna normalizada') e devolve
# o lançamento ou None (linha de saldo, cabeçalho repetido, lixo).
# ---------------------------------------------------------------------------
def _map_nubank(g):
    d, v = _data(g('data')), _valor(g('valor'), '.')
    if d is None or v is None:
        return None
    return _lanc(d, v, g('descricao'), fitid=g('identificador'))


def _map_cora(g):
    d, v = _data(g('data')), _valor(g('valor'), '.')
    if d is None or v is None:
        return None
    desc = ' - '.join(x for x in ((g('transacao') or '').strip(), (g('identificacao') or '').strip()) if x)
    return _lanc(d, v, desc)


def _map_c6(g):
    d = _data(g('datalancamento'))
    dc = _data(g('datacontabil'))
    ent, sai = _valor(g('entradar'), '.'), _valor(g('saidar'), '.')
    if d is None or (ent is None and sai is None):
        return None
    v = (ent or Decimal(0)) - (sai or Decimal(0))
    if v == 0:
        return None
    desc = (g('descricao') or '').strip() or (g('titulo') or '').strip()
    return _lanc(d, v, desc, data_contabil=dc)


def _map_bradesco(g):
    d = _data(g('data'))
    cred, deb = _valor(g('creditor'), ','), _valor(g('debitor'), ',')
    if d is None or (cred is None and deb is None):
        return None                       # 'SALDO ANTERIOR', 'SALDO INVEST FÁCIL'...
    v = cred if cred is not None else deb
    if v is None or v == 0:
        return None
    if deb is not None and cred is None and v > 0:
        v = -v                            # débito às vezes vem sem o sinal
    return _lanc(d, v, g('lancamento'), documento=g('dcto'))


def _map_efi(g):
    tipo = (g('tipo') or '').strip()
    if tipo.lower().startswith('saldo'):
        return None
    d, v = _data(g('data')), _valor(g('valor'), ',')
    if d is None or v is None:
        return None
    # OFX escreve "Recebimento de cobrança: 1040351646 de X"; o CSV usa "•".
    desc = tipo.replace(' • ', ': ').replace('•', ':')
    return _lanc(d, v, desc, documento=g('protocolo'))


LAYOUTS = {
    'nubank': {'banco_id': '260', 'banco': 'NU PAGAMENTOS S.A.', 'nome': 'Nubank',
               'assinatura': ('data', 'valor', 'identificador', 'descricao'), 'mapear': _map_nubank,
               'conta_regex': None},
    'cora': {'banco_id': '403', 'banco': 'Cora SCD SA', 'nome': 'Cora',
             'assinatura': ('data', 'transacao', 'tipotransacao', 'identificacao', 'valor'), 'mapear': _map_cora,
             'conta_regex': None},
    'c6': {'banco_id': '336', 'banco': 'Banco C6 S.A.', 'nome': 'C6',
           'assinatura': ('datalancamento', 'datacontabil', 'titulo', 'descricao', 'entradar', 'saidar'),
           'mapear': _map_c6,
           'conta_regex': re.compile(r'Ag[êe]ncia:\s*(\d+)\s*/\s*Conta:\s*([\d.-]+)', re.I)},
    'bradesco': {'banco_id': '237', 'banco': 'Bradesco', 'nome': 'Bradesco',
                 'assinatura': ('data', 'lancamento', 'dcto', 'creditor', 'debitor'), 'mapear': _map_bradesco,
                 'conta_regex': re.compile(r'Ag[êe]ncia:\s*(\d+)\s+Conta:\s*([\d.-]+)', re.I)},
    'efi': {'banco_id': '364', 'banco': 'Efí', 'nome': 'Efí',
            'assinatura': ('tipo', 'protocolo', 'data', 'valor'), 'mapear': _map_efi,
            'conta_regex': None},
}


def identificar_layout(cabecalho):
    cols = tuple(_norm_col(c) for c in cabecalho)
    for nome, lay in LAYOUTS.items():
        a = lay['assinatura']
        if len(cols) >= len(a) and cols[:len(a)] == a:
            return nome, lay
    return None, None


def parse_csv(raw):
    """bytes → dict no espírito do ``parse_ofx``: banco, banco_id, conta (se o
    arquivo disser), layout, lancamentos [{data, data_contabil, valor,
    descricao, documento, fitid, tipo}]."""
    texto = _decodificar(raw).replace('\r\n', '\n').replace('\r', '\n')
    linhas_txt = texto.splitlines()
    if not any(l.strip() for l in linhas_txt):
        raise CsvInvalido('CSV vazio.')
    # separador: o que mais aparece nas primeiras linhas com conteúdo
    amostra = '\n'.join(l for l in linhas_txt[:40] if l.strip())
    sep = max(',;\t', key=lambda c: amostra.count(c))
    leitor = csv.reader(io.StringIO(texto, newline=''), delimiter=sep, quotechar='"')
    linhas = list(leitor)
    # cabeçalho: a primeira das 20 primeiras linhas cuja assinatura eu conheço
    cab_i, nome, lay = None, None, None
    for i, l in enumerate(linhas[:20]):
        n, la = identificar_layout(l)
        if la:
            cab_i, nome, lay = i, n, la
            break
    if lay is None:
        primeira = next((l for l in linhas if any((c or '').strip() for c in l)), [])
        raise CsvInvalido('CSV com colunas que eu não conheço: '
                          + ', '.join(str(c)[:22] for c in primeira[:6])
                          + '. Layouts que leio: Nubank, Cora, C6, Bradesco e Efí; deste banco mande o OFX.')
    conta = None
    if lay['conta_regex']:
        m = lay['conta_regex'].search('\n'.join(linhas_txt[:cab_i + 1]))
        if m:
            ag, cc = m.group(1), m.group(2)
            conta = f'{int(ag)}/{cc}' if ag and ag.strip('0') else cc
    idx = {_norm_col(c): i for i, c in enumerate(linhas[cab_i])}
    lancs = []
    for l in linhas[cab_i + 1:]:
        if not any((c or '').strip() for c in l):
            continue
        def g(k, _l=l):
            i = idx.get(k)
            return _l[i] if i is not None and i < len(_l) else None
        try:
            x = lay['mapear'](g)
        except Exception:
            x = None
        if x:
            lancs.append(x)
    if not lancs:
        raise CsvInvalido(f'CSV do {lay["nome"]} sem nenhum lançamento legível.')
    return {'banco': lay['banco'], 'banco_id': lay['banco_id'], 'conta': conta,
            'layout': nome, 'saldo': None, 'lancamentos': lancs}
