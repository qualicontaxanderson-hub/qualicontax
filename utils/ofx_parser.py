# -*- coding: utf-8 -*-
"""Leitor de OFX sem dependência externa (Documento E, fase 4).

Aguenta as duas gerações do formato e as manias dos bancos brasileiros:

* OFX 1.x — SGML com cabeçalho ``OFXHEADER:100`` e tags de folha SEM
  fechamento (``<MEMO>Pix recebido`` e acabou). É o que a maioria dos bancos
  daqui exporta, quase sempre em cp1252/latin-1.
* OFX 2.x — XML de verdade, com ``<?xml?>`` e tags fechadas, normalmente UTF-8.
* Datas ``YYYYMMDD`` com rabos variados (``120000[-3:BRT]``) — só os 8
  primeiros dígitos interessam.
* Valores com ponto OU vírgula decimal, com ou sem milhar.

A extração é por regex em cima dos blocos ``<STMTTRN>...</STMTTRN>`` (os
agregados têm fechamento até no SGML), o que serve para os dois formatos —
mais robusto do que exigir XML válido de arquivo de banco.

Devolve dados crus; QUEM grava (e deduplica) é o import da rota. O hash de
idempotência também nasce aqui (``chave_dedup``) para o parser e o import
contarem a mesma história.
"""
import hashlib
import re
from decimal import Decimal, InvalidOperation


class OfxInvalido(ValueError):
    """Arquivo que não dá para ler como OFX (mensagem própria para tela)."""


def _decodificar(raw: bytes) -> str:
    """Decodifica respeitando o cabeçalho; na dúvida, latin-1 nunca estoura."""
    cabeca = raw[:600].decode('ascii', errors='ignore').upper()
    if 'UTF-8' in cabeca or 'UNICODE' in cabeca or raw[:3] == b'\xef\xbb\xbf':
        try:
            return raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode('cp1252')
    except UnicodeDecodeError:
        return raw.decode('latin-1', errors='replace')


def _campo(bloco: str, tag: str):
    """Valor de uma tag de folha, com ou sem fechamento (SGML ou XML)."""
    m = re.search(rf'<{tag}>\s*([^<\r\n]*)', bloco, re.IGNORECASE)
    if not m:
        return None
    v = m.group(1).strip()
    return v or None


def _data(v):
    """'20260815120000[-3:BRT]' → '2026-08-15' (só os 8 dígitos importam)."""
    if not v:
        return None
    dig = re.sub(r'\D', '', v)[:8]
    if len(dig) != 8:
        return None
    return f'{dig[:4]}-{dig[4:6]}-{dig[6:8]}'


def _valor(v):
    """'-1.234,56' | '-1234.56' | '1234,56' → Decimal. None se não for número."""
    if not v:
        return None
    s = v.strip().replace(' ', '')
    if ',' in s and '.' in s:
        s = s.replace('.', '').replace(',', '.')     # 1.234,56
    elif ',' in s:
        s = s.replace(',', '.')                      # 1234,56
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def chave_dedup(empresa_id, banco, conta, lanc, repeticao=0):
    """Chave de idempotência do lançamento (UNIQUE no banco).

    Com FITID a chave é do próprio banco. Sem FITID entra o número da
    repetição do MESMO (data, valor, descrição, documento) dentro do arquivo:
    dois PIX idênticos no dia são dois lançamentos; reimportar não duplica.
    """
    if lanc.get('fitid'):
        base = f"{empresa_id or ''}|{banco or ''}|{conta or ''}|fitid|{lanc['fitid']}"
    else:
        base = '|'.join(str(x or '') for x in (
            empresa_id, banco, conta, lanc.get('data'), lanc.get('valor'),
            lanc.get('descricao'), lanc.get('documento'), f'#{repeticao}'))
    return hashlib.sha256(base.encode('utf-8')).hexdigest()


def parse_ofx(raw: bytes) -> dict:
    """bytes do arquivo → {banco, conta, saldo, lancamentos[]}.

    lancamento: {data, valor(assinado), tipo(credito|debito), descricao,
    documento, fitid}. saldo: {valor, data} do LEDGERBAL, ou None.
    """
    texto = _decodificar(raw)
    if not re.search(r'<OFX>', texto, re.IGNORECASE):
        raise OfxInvalido('Isto não parece um arquivo OFX (sem bloco <OFX>).')

    # Conta: BANKACCTFROM (corrente) ou CCACCTFROM (cartão)
    banco = conta = None
    m = re.search(r'<(?:BANKACCTFROM|CCACCTFROM)>(.*?)</(?:BANKACCTFROM|CCACCTFROM)>',
                  texto, re.IGNORECASE | re.DOTALL)
    if m:
        bloco = m.group(1)
        banco = _campo(bloco, 'BANKID')
        agencia = _campo(bloco, 'BRANCHID')
        cc = _campo(bloco, 'ACCTID')
        conta = f'{agencia}/{cc}' if agencia and cc else cc
    org = None
    m = re.search(r'<FI>(.*?)</FI>', texto, re.IGNORECASE | re.DOTALL)
    if m:
        org = _campo(m.group(1), 'ORG')
    # O CODIGO (BANKID) e o identificador confiavel: o Sicredi manda o nome da
    # COOPERATIVA no ORG ('CCPI DO CERRADO DE GO'), que muda de praca para
    # praca. Guardamos os dois — nome para ler, codigo para reconhecer.
    banco_id = re.sub(r'\D', '', banco or '').lstrip('0') or None
    banco = org or banco                     # nome do banco vale mais que número

    lancamentos = []
    for bloco in re.findall(r'<STMTTRN>(.*?)</STMTTRN>', texto,
                            re.IGNORECASE | re.DOTALL):
        valor = _valor(_campo(bloco, 'TRNAMT'))
        data = _data(_campo(bloco, 'DTPOSTED'))
        if valor is None or data is None:
            continue                          # lixo não vira lançamento
        memo = _campo(bloco, 'MEMO')
        nome = _campo(bloco, 'NAME')
        payee = None
        mp = re.search(r'<PAYEE>(.*?)</PAYEE>', bloco, re.IGNORECASE | re.DOTALL)
        if mp:
            payee = _campo(mp.group(1), 'NAME')
        descricao = ' - '.join(p for p in (nome or payee, memo) if p) \
            or (memo or nome or payee or '')
        lancamentos.append({
            'data': data,
            'valor': valor,
            'tipo': 'credito' if valor >= 0 else 'debito',
            'descricao': descricao[:500],
            'documento': _campo(bloco, 'CHECKNUM') or _campo(bloco, 'REFNUM'),
            'fitid': _campo(bloco, 'FITID'),
        })

    saldo = None
    m = re.search(r'<LEDGERBAL>(.*?)</LEDGERBAL>', texto,
                  re.IGNORECASE | re.DOTALL)
    if m:
        sv = _valor(_campo(m.group(1), 'BALAMT'))
        sd = _data(_campo(m.group(1), 'DTASOF'))
        if sv is not None:
            saldo = {'valor': sv, 'data': sd}

    if not lancamentos and saldo is None:
        raise OfxInvalido('OFX sem nenhum lançamento legível.')
    return {'banco': banco, 'banco_id': banco_id, 'conta': conta,
            'saldo': saldo, 'lancamentos': lancamentos}
