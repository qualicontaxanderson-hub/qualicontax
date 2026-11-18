# -*- coding: utf-8 -*-
"""Pix "copia e cola" estático (BR Code, padrão EMV do Banco Central) — 17/11/2026.

Usado no pedido de pagamento de comissão: quem paga (Anderson / Alessandra) copia o código ou lê o QR e o
banco já abre com a chave, o nome e o valor certos. Não passa por banco nenhum: é só o texto padronizado.
"""
import io
import re
import unicodedata


def _campo(id_, valor):
    return f'{id_}{len(valor):02d}{valor}'


def _limpo(s, n):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().upper()
    return re.sub(r'[^A-Z0-9 .\-]', '', s).strip()[:n] or 'X'


def _crc16(texto):
    crc = 0xFFFF
    for ch in texto.encode('utf-8'):
        crc ^= ch << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f'{crc:04X}'


def chave_normalizada(tipo, chave):
    """CPF/CNPJ só dígitos; celular no formato +55DDDNUMERO; e-mail minúsculo; aleatória como veio."""
    c = (chave or '').strip()
    if tipo in ('cpf', 'cnpj'):
        return re.sub(r'\D', '', c)
    if tipo == 'celular':
        d = re.sub(r'\D', '', c)
        if d.startswith('55') and len(d) in (12, 13):
            d = d[2:]
        return '+55' + d
    if tipo == 'email':
        return c.lower()
    return c


def copia_e_cola(chave, nome, valor=None, cidade='BRASIL', txid='***', mensagem=''):
    gui = _campo('00', 'br.gov.bcb.pix') + _campo('01', chave)
    if mensagem:
        gui += _campo('02', _limpo(mensagem, 40))
    p = _campo('00', '01') + _campo('26', gui) + _campo('52', '0000') + _campo('53', '986')
    if valor:
        p += _campo('54', f'{float(valor):.2f}')
    p += _campo('58', 'BR') + _campo('59', _limpo(nome, 25)) + _campo('60', _limpo(cidade, 15))
    p += _campo('62', _campo('05', re.sub(r'[^A-Za-z0-9]', '', txid or '')[:25] or '***'))
    p += '6304'
    return p + _crc16(p)


def qr_data_uri(texto):
    """QR do código em PNG embutido (data:image/png;base64,...)."""
    import base64
    import qrcode
    img = qrcode.make(texto, box_size=7, border=2)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
