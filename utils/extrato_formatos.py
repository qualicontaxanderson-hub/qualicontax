# -*- coding: utf-8 -*-
"""Formatos de extrato por banco — o CATÁLOGO e o DESPACHANTE (14/09/2026).

Anderson: "temos clientes organizados e desorganizados; o cliente que tem um
banco num mês manda PDF, no outro OFX e no outro Excel. Vale deixar tudo
configurado e com essa tabela morando no sistema."

Duas coisas moram aqui:

1. ``CATALOGO`` — a tabela "Formatos por banco" que aparece em Financeiro →
   Contas: para cada banco, o que cada formato entrega (ok / irregular /
   igual ao OFX / não configurado) e a observação. É a referência de quem
   atende o cliente: "mande o OFX", "no C6 mande o PDF".

2. ``ler_arquivo(nome, dados, senhas)`` — reconhece o arquivo pelo CONTEÚDO
   e devolve ``(previa, formato)`` no mesmo formato para OFX, PDF e CSV, para
   o roteador e a importação manual não precisarem saber quem leu.

REGRA: OFX é o padrão para todo banco (um leitor serve para todos). PDF e
CSV só onde o OFX é incompleto (C6) ou onde o cliente só tem esse arquivo
(Nubank). Layout novo = leitor novo, escrito olhando o arquivo real.
"""
import logging

logger = logging.getLogger(__name__)

# status: 'ok' | 'irregular' | 'igual' (mesmo conteúdo do OFX, não precisa)
#         | 'nao' (não configurado — mande o OFX)
CATALOGO = [
    {'banco_id': '336', 'nome': 'C6',
     'ofx': ('irregular', 'Pix enviado sem o nome de quem recebeu e boleto sem cedente. Entra, mas o completo é o PDF.'),
     'pdf': ('ok', 'O extrato completo do C6. Vale sozinho; se o OFX vier também, os dois se encaixam.'),
     'csv': ('nao', 'Mande o PDF.')},
    {'banco_id': '748', 'nome': 'Sicredi',
     'ofx': ('ok', 'CPF/CNPJ e nome nos dois sentidos.'),
     'pdf': ('nao', 'Mande o OFX.'),
     'csv': ('nao', 'Mande o OFX.')},
    {'banco_id': '260', 'nome': 'Nubank',
     'ofx': ('ok', 'Nome, CNPJ e banco de origem na descrição.'),
     'pdf': ('igual', 'Mesmo conteúdo do OFX; lido pela conta na capa (o CPF vem oculto).'),
     'csv': ('igual', 'Mesmo conteúdo do OFX, com o mesmo identificador. Precisa do número da empresa no nome do arquivo.')},
    {'banco_id': '364', 'nome': 'Efí',
     'ofx': ('ok', '"Pix enviado via chave: X", "Recebimento de cobrança: N de X".'),
     'pdf': ('nao', 'Mande o OFX.'),
     'csv': ('nao', 'Mande o OFX.')},
    {'banco_id': '237', 'nome': 'Bradesco',
     'ofx': ('ok', 'Nome nas transferências e no Pix recebido.'),
     'pdf': ('nao', 'Mande o OFX.'),
     'csv': ('nao', 'Mande o OFX.')},
    {'banco_id': '403', 'nome': 'Cora',
     'ofx': ('ok', 'Nome nos dois sentidos.'),
     'pdf': ('nao', 'Mande o OFX.'),
     'csv': ('nao', 'Mande o OFX.')},
]

ROTULO = {'ok': 'OK', 'irregular': 'Irregular', 'igual': 'Igual ao OFX', 'nao': 'Não configurado'}


def catalogo():
    """Linhas prontas para a tela."""
    out = []
    for b in CATALOGO:
        linha = {'banco_id': b['banco_id'], 'nome': b['nome']}
        for f in ('ofx', 'pdf', 'csv'):
            st, nota = b[f]
            linha[f] = {'status': st, 'rotulo': ROTULO[st], 'nota': nota}
        out.append(linha)
    return out


# ---------------------------------------------------------------------------
# Despachante
# ---------------------------------------------------------------------------
class ArquivoDesconhecido(Exception):
    """Arquivo que nenhum leitor reconhece — fica onde está."""


def ler_arquivo(nome, dados, senhas=()):
    """(previa, formato).

    formato: 'ofx' | 'csv' | 'pdf' — leu. Marcadores quando não leu:
    'pdf-senha' (protegido, ninguém abriu), 'pdf-outro' (extrato de banco
    sem leitor), 'csv-outro' (CSV com colunas desconhecidas). Levanta
    ``ArquivoDesconhecido`` para o que não é extrato (DANFE, boleto...).
    Em 'pdf-outro'/'csv-outro' a previa é {'motivo': texto}.
    """
    n = (nome or '').lower()
    if n.endswith('.ofx'):
        from utils.ofx_parser import parse_ofx
        return parse_ofx(dados), 'ofx'
    if n.endswith('.csv'):
        from utils.extrato_csv import parse_csv, CsvInvalido
        try:
            return parse_csv(dados), 'csv'
        except CsvInvalido as e:
            return {'motivo': str(e)}, 'csv-outro'
    if n.endswith('.pdf'):
        return _ler_pdf(dados, senhas)
    raise ArquivoDesconhecido(nome)


def _ler_pdf(dados, senhas):
    from utils.extrato_pdf_c6 import (_abrir, e_extrato_c6, parse_pdf_c6, PdfProtegido,
                                      parece_extrato_bancario, MOTIVO_PDF_OUTRO)
    from utils.extrato_pdf_nubank import e_extrato_nubank, parse_pdf_nubank, PdfNubankInvalido
    try:
        doc = _abrir(dados, senhas)
    except PdfProtegido:
        return None, 'pdf-senha'
    paginas = [p.get_text() for p in doc]
    if not paginas:
        raise ArquivoDesconhecido('pdf vazio')
    if e_extrato_c6(paginas[0]):
        return parse_pdf_c6(dados, senhas), 'pdf'
    if e_extrato_nubank(paginas[0]):
        try:
            return parse_pdf_nubank(paginas), 'pdf'
        except PdfNubankInvalido as e:
            return {'motivo': f'Extrato do Nubank que não consegui ler: {e}'}, 'pdf-outro'
    if parece_extrato_bancario(paginas[0]):
        return {'motivo': MOTIVO_PDF_OUTRO}, 'pdf-outro'
    raise ArquivoDesconhecido('pdf que não é extrato')
