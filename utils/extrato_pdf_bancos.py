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

_RE_DATA = re.compile(r'^(\d{2})/(\d{2})/(\d{2}|\d{4})$')
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
    if not m:
        return None
    ano = m.group(3)
    if len(ano) == 2:                     # o extrato PF do Bradesco usa 08/01/26
        ano = ('20' if int(ano) < 70 else '19') + ano
    return f'{ano}-{m.group(2)}-{m.group(1)}'


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
    """PJ escreve 'CC:', PF escreve 'Conta:' — os dois dizem 'Extrato de: Ag'."""
    return 'Extrato de: Ag' in t and ('CC:' in t or 'Conta:' in t)


# O Bradesco desenha uma TABELA, e o texto corrido dela sai em ordens
# diferentes no PJ e no PF (medido em 15/09/2026: no PF a coluna Histórico
# vem inteira antes do resto). Por isso este leitor não lê linhas de texto:
# lê PALAVRAS COM COORDENADA, agrupa por altura e distribui por coluna a
# partir do x de cada título do cabeçalho. Vale para os dois extratos.
_TITULOS = (('data', ('data',)),
            ('hist', ('lancamento', 'historico')),
            ('doc', ('dcto', 'docto')),
            ('cred', ('credito',)),
            ('deb', ('debito',)),
            ('saldo', ('saldo',)))
_MARGEM = 8.0        # o texto da coluna começa alguns pontos à esquerda do título
_VIZINHA = 16.0      # distância em y para uma linha só de texto ser do lançamento


def _slug(t):
    import unicodedata
    t = unicodedata.normalize('NFKD', t or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '', t.lower())


def _linhas_com_x(pagina, tol=2.5):
    """As palavras da página agrupadas por altura: [{'y', 'w': [(x, texto)]}]."""
    linhas = []
    for x0, y0, _x1, _y1, txt, *_ in sorted(pagina.get_text('words'),
                                            key=lambda w: (round(w[1], 1), w[0])):
        if not (txt or '').strip():
            continue
        if linhas and abs(linhas[-1]['y'] - y0) <= tol:
            linhas[-1]['w'].append((x0, txt))
        else:
            linhas.append({'y': y0, 'w': [(x0, txt)]})
    for l in linhas:
        l['w'].sort()
    return linhas


def _titulos(linha):
    """{coluna: x} se esta linha for a dos títulos da tabela; senão None."""
    achado = {}
    for x, t in linha['w']:
        n = _slug(t)
        for chave, nomes in _TITULOS:
            if n in nomes and chave not in achado:
                achado[chave] = x
    if {'data', 'hist', 'saldo'} <= set(achado) and ('cred' in achado or 'deb' in achado):
        return achado
    return None


def _celulas(linha, cortes):
    """A linha repartida nas colunas da tabela."""
    ordem = sorted(cortes.items(), key=lambda kv: kv[1])
    out = {c: [] for c, _ in ordem}
    for x, t in linha['w']:
        col = ordem[0][0]
        for chave, x0 in ordem:
            if x >= x0 - _MARGEM:
                col = chave
        out[col].append(t)
    return {c: ' '.join(v).strip() for c, v in out.items()}


def parse_bradesco(paginas, doc=None):
    if doc is None:
        raise PdfBancoInvalido('extrato do Bradesco precisa das coordenadas da página.')
    conta = None
    m = re.search(r'Ag:?\s*(\d{4,5})\s*\|\s*(?:CC|C/C|Conta):?\s*(\d{4,}-?\d?)',
                  ' '.join(' '.join(t for _x, t in l['w']) for l in _linhas_com_x(doc[0])))
    if m:
        conta = f"{int(m.group(1))}/{m.group(2).lstrip('0')}"

    lancs = []
    cortes = None
    data = None          # a data só é escrita quando muda — atravessa a página
    sobra = []           # descrição que ficou no pé da página, sem o valor dela
    for pagina in doc:
        cels = []
        for l in _linhas_com_x(pagina):
            achado = _titulos(l)
            if achado:
                cortes = achado           # a tabela (re)começa aqui
                continue
            if cortes is None:
                continue                  # capa/resumo, antes da primeira tabela
            c = _celulas(l, cortes)
            c['y'] = l['y']
            # 'SALDO ANTERIOR' e 'Total 682.468,04 -688.216,41' são resumo,
            # não lançamento — e o rótulo cai ora na 1ª, ora na 2ª coluna.
            texto = f"{c.get('data') or ''} {c.get('hist') or ''}".strip().upper()
            if texto.startswith('SALDO') or texto.startswith('TOTAL'):
                continue
            cels.append(c)
        # Um lançamento é a linha que tem documento ou valor; as linhas só de
        # texto ao redor dela são a descrição (no PF vêm antes E depois).
        valor_de = [j for j, c in enumerate(cels)
                    if c.get('doc') or _dec(c.get('cred')) is not None or _dec(c.get('deb')) is not None]
        if not valor_de:
            continue
        descricao = {j: [] for j in valor_de}
        soltas = []
        for j, c in enumerate(cels):
            if not c.get('hist') or j in descricao:
                continue
            perto = min(valor_de, key=lambda k: abs(cels[k]['y'] - c['y']))
            if abs(cels[perto]['y'] - c['y']) <= _VIZINHA:
                descricao[perto].append((c['y'], c['hist']))
            elif j > valor_de[-1]:
                soltas.append(c['hist'])  # pé da página: o valor vem na próxima
        if sobra:                         # ... e aqui ele chega
            descricao[valor_de[0]].insert(0, (-1, ' '.join(sobra)))
        sobra = soltas
        for j, c in enumerate(cels):
            d = _iso(c.get('data'))
            if d:
                data = d
            if j not in descricao:
                continue
            v = _dec(c.get('cred'))
            if v is None:
                v = _dec(c.get('deb'))
                if v is not None and v > 0:
                    v = -v                # o débito às vezes vem sem o sinal
            if v is None or v == 0 or not data:
                continue
            texto = ' '.join(t for _y, t in sorted(descricao[j]))
            lancs.append(_lanc(data, v, texto, documento=(c.get('doc') or None)))
    if not lancs:
        raise PdfBancoInvalido('extrato do Bradesco sem lançamento legível.')
    return {'banco': 'Bradesco', 'banco_id': '237', 'conta': conta,
            'saldo': None, 'lancamentos': lancs}
