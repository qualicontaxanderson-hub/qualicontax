# -*- coding: utf-8 -*-
"""Formas de pagamento das vendas, lidas do XML da nota (tabela nfe_pagamentos).

Cada <detPag> do XML vira uma linha: tipo (dinheiro, débito, crédito, PIX...),
valor, bandeira, CNPJ da credenciadora (maquininha), autorização e o troco da
nota (só na linha 1). Uma venda paga em duas formas dá duas linhas.

Três caminhos gravam por aqui, todos com ``ler_pagamentos`` + ``gravar``:
  * a chegada da nota de SAÍDA (``registrar_saida``, chamado por _save_nfe) —
    Q-Robô, upload, Dropbox: toda venda nova já entra com o pagamento;
  * o preenchimento de quem ainda tem o XML no banco (xml_raw);
  * o script do passado, que lê os XMLs do Dropbox (scripts/preencher_pagamentos.py).

Gravar é INSERT IGNORE na chave (chave_acesso, n): reler o mesmo XML não
duplica. Nunca levanta para quem chama: o pagamento é um extra da nota, e uma
falha aqui não pode impedir a venda de entrar.
"""
import logging
import re

from utils.xml_seguro import fromstring_seguro

_log = logging.getLogger(__name__)

# Tabela da NT (leiaute NF-e/NFC-e 4.00), os mesmos rótulos do NH.
FORMAS = {
    '01': 'Dinheiro', '02': 'Cheque', '03': 'Cartão de Crédito', '04': 'Cartão de Débito',
    '05': 'Crédito Loja / A Prazo', '10': 'Vale Alimentação', '11': 'Vale Refeição',
    '12': 'Vale Presente', '13': 'Vale Combustível', '14': 'Duplicata Mercantil',
    '15': 'Boleto', '16': 'Depósito Bancário', '17': 'PIX Dinâmico',
    '18': 'Transferência / Carteira Digital', '19': 'Fidelidade / Cashback',
    '20': 'PIX Estático', '21': 'Crédito em Loja', '22': 'Pagamento Eletrônico',
    '90': 'Sem Pagamento', '99': 'Outros',
}
BANDEIRAS = {
    '01': 'Visa', '02': 'Mastercard', '03': 'American Express', '04': 'Sorocred',
    '05': 'Diners', '06': 'Elo', '07': 'Hipercard', '08': 'Aura', '09': 'Cabal',
    '10': 'Alelo', '11': 'Banes Card', '12': 'CalCard', '13': 'Credz', '14': 'Discover',
    '15': 'GoodCard', '16': 'GreenCard', '17': 'Hiper', '18': 'JCB', '19': 'Mais',
    '20': 'MaxVan', '21': 'Policard', '22': 'RedeCompras', '23': 'Sodexo',
    '24': 'ValeCard', '25': 'Verocheque', '26': 'VR', '27': 'Ticket', '99': 'Outros',
}

_RE_PAG = re.compile(r'<pag\b[^>]*>.*?</pag>', re.S)
_RE_NS = re.compile(r'\sxmlns(:\w+)?="[^"]*"')

SQL_INSERT = (
    "INSERT IGNORE INTO nfe_pagamentos "
    "(chave_acesso, n, cliente_id, data_emissao, tpag, descricao, valor, "
    " tband, credenciadora, autorizacao, integrado, troco) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
)


def _txt(el, nome, tam=None):
    f = el.find(nome) if el is not None else None
    v = (f.text or '').strip() if f is not None else ''
    return (v[:tam] if tam else v) or None


def _valor(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


def ler_pagamentos(xml):
    """XML da nota (str/bytes) -> lista de dicts, um por forma, na ordem do XML.

    Lê só o bloco <pag> (rápido para o preenchimento de milhões de notas).
    Aceita o leiaute 4.00 (<pag><detPag>...</detPag><vTroco/></pag>) e o antigo
    3.10 (um <pag> por forma, sem <detPag>). Sem <pag> -> []."""
    if isinstance(xml, (bytes, bytearray)):
        xml = xml.decode('utf-8', 'replace')
    blocos = _RE_PAG.findall(xml or '')
    if not blocos:
        return []
    raiz = fromstring_seguro('<r>' + _RE_NS.sub('', ''.join(blocos)) + '</r>')
    formas, troco = [], 0.0
    for pag in raiz.findall('pag'):
        detalhes = pag.findall('detPag') or [pag]
        troco += _valor(_txt(pag, 'vTroco'))
        for dp in detalhes:
            tpag = _txt(dp, 'tPag')
            if not tpag:
                continue
            card = dp.find('card')
            integ = _txt(card, 'tpIntegra')
            cred = re.sub(r'\D', '', _txt(card, 'CNPJ') or '')
            formas.append({
                'tpag': tpag.zfill(2)[:2],
                'descricao': _txt(dp, 'xPag', 60),
                'valor': _valor(_txt(dp, 'vPag')),
                'tband': _txt(card, 'tBand', 2),
                'credenciadora': cred[:14] or None,
                'autorizacao': _txt(card, 'cAut', 60),
                'integrado': int(integ) if integ and integ.isdigit() else None,
                'troco': 0.0,
            })
    if formas:
        formas[0]['troco'] = round(troco, 2)
    return formas


def linhas(chave, cliente_id, data_emissao, formas):
    """Tuplas para SQL_INSERT (n começa em 1)."""
    return [(chave, n, cliente_id, data_emissao, f['tpag'], f['descricao'], f['valor'],
             f['tband'], f['credenciadora'], f['autorizacao'], f['integrado'], f['troco'])
            for n, f in enumerate(formas, 1)]


def registrar_saida(xml_raw, chave, cliente_id, data_emissao):
    """Chamado na chegada de cada nota de SAÍDA. Nunca levanta."""
    try:
        formas = ler_pagamentos(xml_raw)
        if formas:
            from utils.db_helper import execute_many
            execute_many(SQL_INSERT, linhas(chave, cliente_id, data_emissao, formas))
    except Exception:
        _log.warning('pagamento da nota %s não gravado', chave, exc_info=True)
