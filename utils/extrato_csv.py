# -*- coding: utf-8 -*-
"""Extrato em CSV e em planilha — leitor por LAYOUT conhecido (14/09/2026).

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
* **Sicredi**  preâmbulo com ``Cooperativa: 3950`` e ``Conta: 15639-0``;
               ``Data,Descrição,Documento,Valor (R$),Saldo (R$)``. A coluna
               Documento traz o TIPO ('PIX_DEB') quando não há número — tipo
               não vira documento, senão a deduplicação casaria linhas
               diferentes pela mesma chave.

O Bradesco tem DOIS extratos: o da empresa ('Lançamento', 'Dcto.') e o da
pessoa física ('Histórico', 'Docto.', data de dois dígitos, e a descrição
quebrada em duas linhas). O mesmo layout atende os dois.

A PLANILHA (.xls e .xlsx) é a mesma tabela do CSV em outro invólucro:
``parse_planilha`` converte e entrega ao mesmo miolo. A extensão mente (o
'c6 jan a jul.xlsx' é um .xls por dentro, e ainda vem com senha), então quem
manda é a assinatura do arquivo.

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
    m = re.match(r'^(\d{2})/(\d{2})/(\d{2})$', v)     # PF do Bradesco: 08/01/26
    if m:
        sec = '20' if int(m.group(3)) < 70 else '19'
        return f'{sec}{m.group(3)}-{m.group(2)}-{m.group(1)}'
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
    # PJ escreve 'Lançamento' e 'Dcto.'; PF escreve 'Histórico' e 'Docto.'
    d = _data(g('data'))
    cred, deb = _valor(g('creditor'), ','), _valor(g('debitor'), ',')
    if d is None or (cred is None and deb is None):
        return None                       # 'SALDO ANTERIOR', 'SALDO INVEST FÁCIL'...
    v = cred if cred is not None else deb
    if v is None or v == 0:
        return None
    if deb is not None and cred is None and v > 0:
        v = -v                            # débito às vezes vem sem o sinal
    return _lanc(d, v, g('lancamento') or g('historico'),
                 documento=g('dcto') or g('docto'))


def _map_sicredi(g):
    d, v = _data(g('data')), _valor(g('valorr'), '.')
    if d is None or v is None or v == 0:
        return None
    # A coluna 'Documento' do Sicredi traz o TIPO ('PIX_DEB', 'PIX_CRED') quando
    # não há número. Tipo não é identificador: viraria uma chave repetida em
    # centenas de linhas e a deduplicação casaria lançamentos diferentes.
    doc = re.sub(r'\D', '', (g('documento') or ''))
    return _lanc(d, v, g('descricao'), documento=doc if len(doc) >= 5 else None)


def _cont_bradesco(g):
    """'; Qualicontax Assessoria Contabil;;' — a continuação da descrição."""
    if _data(g('data')) or _valor(g('creditor'), ',') or _valor(g('debitor'), ','):
        return None
    return ((g('historico') or g('lancamento') or '').strip()) or None


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
                 'assinatura': (('data', 'lancamento', 'dcto', 'creditor', 'debitor'),
                                ('data', 'historico', 'docto', 'creditor', 'debitor')),
                 'mapear': _map_bradesco, 'continuacao': _cont_bradesco,
                 'conta_regex': re.compile(
                     r'Ag(?:[êe]ncia)?:?\s*(\d{4,5})\s*(?:\||\s)\s*(?:CC|C/C|Conta):?\s*([\d.-]+)', re.I)},
    'sicredi': {'banco_id': '748', 'banco': 'Sicredi', 'nome': 'Sicredi',
                'assinatura': ('data', 'descricao', 'documento', 'valorr', 'saldor'),
                'mapear': _map_sicredi,
                'conta_regex': re.compile(r'Cooperativa:\s*(\d+)[\s\S]{0,80}?Conta:\s*([\d.-]+)', re.I)},
    'efi': {'banco_id': '364', 'banco': 'Efí', 'nome': 'Efí',
            'assinatura': ('tipo', 'protocolo', 'data', 'valor'), 'mapear': _map_efi,
            'conta_regex': None},
}


def identificar_layout(cabecalho):
    cols = tuple(_norm_col(c) for c in cabecalho)
    for nome, lay in LAYOUTS.items():
        a = lay['assinatura']
        for ass in (a if isinstance(a[0], tuple) else (a,)):
            if len(cols) >= len(ass) and cols[:len(ass)] == ass:
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
    return _montar(linhas, linhas_txt, 'CSV')


def _montar(linhas, linhas_txt, rotulo):
    """O miolo, servido pelo CSV e pela planilha: acha o cabeçalho, reconhece
    o layout, tira a conta do preâmbulo e mapeia linha a linha."""
    # cabeçalho: a primeira das 20 primeiras linhas cuja assinatura eu conheço
    cab_i, nome, lay = None, None, None
    for i, l in enumerate(linhas[:20]):
        n, la = identificar_layout(l)
        if la:
            cab_i, nome, lay = i, n, la
            break
    if lay is None:
        primeira = next((l for l in linhas if any((c or '').strip() for c in l)), [])
        raise CsvInvalido(f'{rotulo} com colunas que eu não conheço: '
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
            continue
        cont = lay.get('continuacao')
        if cont and lancs:
            try:
                extra = cont(g)
            except Exception:
                extra = None
            if extra:
                lancs[-1]['descricao'] = (lancs[-1]['descricao'] + ' ' + extra).strip()[:500]
    if not lancs:
        raise CsvInvalido(f'{rotulo} do {lay["nome"]} sem nenhum lançamento legível.')
    return {'banco': lay['banco'], 'banco_id': lay['banco_id'], 'conta': conta,
            'layout': nome, 'saldo': None, 'lancamentos': lancs}


# ---------------------------------------------------------------------------
# Planilha (.xls e .xlsx)
# ---------------------------------------------------------------------------
def _linhas_xls(raw):
    import xlrd                                   # só .xls (BIFF), puro Python
    wb = xlrd.open_workbook(file_contents=raw)
    sh = wb.sheet_by_index(0)
    out = []
    for r in range(sh.nrows):
        linha = []
        for c in range(sh.ncols):
            v = sh.cell_value(r, c)
            if isinstance(v, float) and v == int(v):
                v = int(v)
            linha.append('' if v is None else str(v))
        out.append(linha)
    return out


def _linhas_xlsx(raw):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    sh = wb[wb.sheetnames[0]]
    out = []
    for linha in sh.iter_rows(values_only=True):
        out.append(['' if v is None else str(v) for v in linha])
    wb.close()
    return out


def parse_planilha(raw, nome=''):
    """A planilha do banco é a MESMA tabela do CSV — só muda o invólucro.

    O Bradesco, o Sicredi e o C6 deixam baixar o extrato em Excel; quem baixou
    assim manda assim. Aqui a planilha vira linhas de texto e segue pelo mesmo
    caminho do CSV: assinatura das colunas, preâmbulo com a conta, mapeamento.
    """
    # A EXTENSÃO MENTE: o 'c6 jan a jul.xlsx' é um .xls por dentro. Quem manda
    # é a assinatura do arquivo — 'PK' é zip (xlsx), D0CF11E0 é OLE2 (xls).
    if 'EncryptedPackage'.encode('utf-16-le') in raw[:16384]:
        # O C6 entrega a planilha com senha, como faz com o PDF. Aqui ela não
        # abre: o CSV do mesmo extrato vem aberto e traz o mesmo conteúdo.
        raise CsvInvalido('planilha protegida por senha (o C6 faz isso); '
                          'mande o CSV ou o OFX do mesmo período.')
    if raw[:2] == b'PK':
        abrir = _linhas_xlsx
    elif raw[:4] == bytes((0xD0, 0xCF, 0x11, 0xE0)):
        abrir = _linhas_xls
    else:
        abrir = _linhas_xlsx if (nome or '').lower().endswith('.xlsx') else _linhas_xls
    try:
        linhas = abrir(raw)
    except ImportError as e:
        raise CsvInvalido(f'planilha que eu não consigo abrir aqui ({e}); mande o OFX.')
    except Exception as e:
        raise CsvInvalido(f'planilha ilegível: {e}')
    if not linhas:
        raise CsvInvalido('planilha vazia.')
    linhas_txt = [' '.join(c for c in l if c) for l in linhas]
    return _montar(linhas, linhas_txt, 'Planilha')
