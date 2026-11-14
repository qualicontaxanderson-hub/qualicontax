# -*- coding: utf-8 -*-
"""Efí Bank — API de Cobranças (boleto) e API Pix (saldo, Pix recebidos) — 02/11/2026.

Duas APIs, duas autenticações (documentação em dev.efipay.com.br):

* **Cobranças** — ``POST /v1/authorize`` com HTTP Basic (Client ID : Client Secret),
  corpo ``{"grant_type": "client_credentials"}``; token Bearer por 600 s. SEM
  certificado. Produção ``cobrancas.api.efipay.com.br``, homologação
  ``cobrancas-h.api.efipay.com.br``.
* **Pix** — ``POST /oauth/token`` igual, mas TODA requisição exige o certificado
  .p12 da API (mTLS), baixado em Efí → API → Meus Certificados (uma vez, por
  ambiente). Produção ``pix.api.efipay.com.br``, homologação ``pix-h...``.

O que usamos:
* boleto em um passo ``POST /v1/charge/one-step`` (itens em centavos, pagador,
  vencimento, juros/multa/dias para baixa, mensagem, ``notification_url``);
  volta ``charge_id``, linha digitável (``barcode``), ``link``, ``pdf.charge`` e,
  se a conta tem chave Pix, ``pix.qrcode`` (copia-e-cola) e ``pix.qrcode_image``;
* ``GET /v1/charge/:id``, ``PUT /v1/charge/:id/cancel``, ``PUT /v1/charge/:id/billet``
  (novo vencimento), ``PUT /v1/charge/:id/metadata`` (URL de aviso);
* ``GET /v1/notification/:token`` — a Efí só POSTa o token na nossa URL; os
  eventos (status anterior/atual, valor, data do pagamento) vêm daqui;
* Pix: ``GET /v2/gn/saldo`` e ``GET /v2/pix?inicio&fim`` (recebidos).

Nada de credencial em log: as exceções carregam só o status HTTP e a mensagem
que a Efí devolveu.
"""
import base64
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

HOSTS = {
    'cobrancas': {'producao': 'https://cobrancas.api.efipay.com.br',
                  'homologacao': 'https://cobrancas-h.api.efipay.com.br'},
    'pix': {'producao': 'https://pix.api.efipay.com.br',
            'homologacao': 'https://pix-h.api.efipay.com.br'},
    # API Extratos (12/11/2026): o CNAB 240 diário da conta. mTLS como a Pix,
    # token em /v1/oauth/token. Só existe em produção.
    'extratos': {'producao': 'https://extratos.api.efipay.com.br',
                 'homologacao': 'https://extratos.api.efipay.com.br'},
}
TOKEN_PATH = {'cobrancas': '/v1/authorize', 'pix': '/oauth/token', 'extratos': '/v1/oauth/token'}
TIMEOUT = (10, 40)

SITUACOES = {
    'new': 'Criada', 'waiting': 'Aguardando pagamento', 'paid': 'Paga', 'unpaid': 'Vencida (não paga)',
    'canceled': 'Cancelada', 'settled': 'Baixada manualmente', 'link': 'Link de pagamento',
    'expired': 'Expirada', 'refunded': 'Devolvida', 'contested': 'Contestada',
}


class EfiErro(Exception):
    def __init__(self, mensagem, http=None, corpo=None):
        super().__init__(mensagem)
        self.http = http
        self.corpo = corpo


@dataclass
class ContaEfi:
    client_id: str
    client_secret: str
    ambiente: str = 'homologacao'          # homologacao | producao
    p12: bytes = b''                       # certificado da API (só a API Pix)
    p12_senha: str = ''
    _tokens: dict = field(default_factory=dict, repr=False)

    def __repr__(self):                    # nunca mostrar segredo em traceback/log
        return f'ContaEfi(ambiente={self.ambiente!r}, cert={"sim" if self.p12 else "não"})'


class Efi:
    """Uma sessão por conta de cobrança. Tokens guardados em memória até expirar."""

    def __init__(self, conta: ContaEfi):
        self.c = conta
        self.amb = 'producao' if conta.ambiente == 'producao' else 'homologacao'

    # ------------------------------------------------------------------ auth
    def _basic(self):
        par = f'{self.c.client_id}:{self.c.client_secret}'.encode()
        return {'Authorization': 'Basic ' + base64.b64encode(par).decode(), 'Content-Type': 'application/json'}

    def _token(self, api):
        t = self.c._tokens.get(api)
        if t and t['vence'] > time.time() + 15:
            return t['token']
        url = HOSTS[api][self.amb] + TOKEN_PATH[api]
        kw = {'headers': self._basic(), 'json': {'grant_type': 'client_credentials'}, 'timeout': TIMEOUT}
        if api in ('pix', 'extratos'):
            with self._cert() as cert:
                r = requests.post(url, cert=cert, **kw)
        else:
            r = requests.post(url, **kw)
        if r.status_code != 200:
            raise EfiErro(self._msg(r, 'A Efí não aceitou as credenciais'), r.status_code, _json(r))
        j = r.json()
        self.c._tokens[api] = {'token': j['access_token'], 'vence': time.time() + int(j.get('expires_in') or 600)}
        return j['access_token']

    class _CertTemp:
        """Arquivo PEM temporário (requests só aceita caminho). Apagado ao sair."""
        def __init__(self, p12, senha):
            self.p12, self.senha, self.caminho = p12, senha, None

        def __enter__(self):
            if not self.p12:
                raise EfiErro('Esta conta não tem o certificado da API (.p12): a API Pix exige.')
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.serialization import pkcs12
            try:
                key, cert, extra = pkcs12.load_key_and_certificates(
                    self.p12, (self.senha or '').encode() or None)
            except Exception as exc:
                raise EfiErro(f'Certificado da API inválido ou senha errada ({exc}).') from exc
            pem = cert.public_bytes(serialization.Encoding.PEM)
            pem += key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption())
            for c in extra or []:
                pem += c.public_bytes(serialization.Encoding.PEM)
            fd, self.caminho = tempfile.mkstemp(suffix='.pem')
            with os.fdopen(fd, 'wb') as f:
                f.write(pem)
            return self.caminho

        def __exit__(self, *a):
            if self.caminho:
                try:
                    os.remove(self.caminho)
                except OSError:
                    pass

    def _cert(self):
        return Efi._CertTemp(self.c.p12, self.c.p12_senha)

    # ------------------------------------------------------------------ http
    @staticmethod
    def _msg(r, prefixo):
        j = _json(r) or {}
        det = j.get('error_description') or j.get('message') or j.get('error') or ''
        if isinstance(det, dict):
            det = det.get('message') or det.get('property') or str(det)
        if isinstance(j.get('error_description'), dict):
            d = j['error_description']
            det = (d.get('message') or '') + (f" (campo {d['property']})" if d.get('property') else '')
        return f'{prefixo}: HTTP {r.status_code}' + (f' — {det}' if det else '')

    def _cob(self, metodo, caminho, **kw):
        url = HOSTS['cobrancas'][self.amb] + caminho
        h = {'Authorization': 'Bearer ' + self._token('cobrancas'), 'Content-Type': 'application/json'}
        r = requests.request(metodo, url, headers=h, timeout=TIMEOUT, **kw)
        if r.status_code >= 300:
            raise EfiErro(self._msg(r, 'A Efí recusou a operação'), r.status_code, _json(r))
        return _json(r) or {}

    def _pix(self, metodo, caminho, **kw):
        url = HOSTS['pix'][self.amb] + caminho
        h = {'Authorization': 'Bearer ' + self._token('pix'), 'Content-Type': 'application/json'}
        with self._cert() as cert:
            r = requests.request(metodo, url, headers=h, cert=cert, timeout=TIMEOUT, **kw)
        if r.status_code >= 300:
            raise EfiErro(self._msg(r, 'A API Pix da Efí recusou'), r.status_code, _json(r))
        return _json(r) or {}

    # ------------------------------------------------------------------ teste
    def testar(self):
        """Prova as credenciais: token de cobranças (sempre) e saldo pela API Pix (se há certificado).
        Devolve texto para a tela."""
        self._token('cobrancas')
        partes = ['credenciais aceitas pela API de cobranças']
        if self.c.p12:
            s = self.saldo()
            partes.append(f"saldo R$ {s['saldo']:.2f}".replace('.', ','))
        else:
            partes.append('sem certificado: saldo e Pix recebidos ficam indisponíveis')
        return '; '.join(partes)

    # ------------------------------------------------------------------ boleto
    def criar_boleto(self, itens, pagador, vencimento, custom_id=None, notification_url=None,
                     juros_mes=None, multa_pct=None, dias_baixa=None, mensagem=None, efi_avisa_por_email=False):
        """Boleto em um passo. ``itens``: [{nome, valor(Decimal|float em reais), quantidade}];
        ``pagador``: {nome, documento (CPF 11 / CNPJ 14 dígitos), email, telefone, endereco{...}}.
        ``efi_avisa_por_email``: a Efí só manda e-mail ao pagador quando recebe o e-mail dele; o padrão é
        NÃO mandar (quem avisa o cliente é o nosso app). Devolve o ``data`` da Efí (charge_id, barcode, link, pdf, pix...)."""
        corpo = {
            'items': [{'name': (i['nome'] or 'Serviço')[:255], 'value': _cent(i['valor']),
                       'amount': int(i.get('quantidade') or 1)} for i in itens],
            'payment': {'banking_billet': {
                'customer': _pagador(pagador, com_email=efi_avisa_por_email),
                'expire_at': vencimento.strftime('%Y-%m-%d') if hasattr(vencimento, 'strftime') else str(vencimento),
                'configurations': _config(juros_mes, multa_pct, dias_baixa),
            }},
        }
        if mensagem:
            corpo['payment']['banking_billet']['message'] = _mensagem(mensagem)
        meta = {}
        if custom_id:
            meta['custom_id'] = str(custom_id)[:60]
        if notification_url:
            meta['notification_url'] = notification_url
        if meta:
            corpo['metadata'] = meta
        return self._cob('POST', '/v1/charge/one-step', json=corpo).get('data') or {}

    def consultar(self, charge_id):
        return self._cob('GET', f'/v1/charge/{int(charge_id)}').get('data') or {}

    def listar_boletos(self, desde, ate, pagina=1, limite=100):
        """Boletos CRIADOS entre ``desde`` e ``ate`` (datas), uma página. Só enxerga os boletos
        criados por esta mesma aplicação (14/11/2026: os do Conexa só pela aplicação "Conexa")."""
        return self._cob('GET', '/v1/charges', params={
            'charge_type': 'billet', 'begin_date': desde.isoformat(), 'end_date': ate.isoformat(),
            'limit': int(limite), 'page': int(pagina)}).get('data') or []

    def cancelar(self, charge_id):
        return self._cob('PUT', f'/v1/charge/{int(charge_id)}/cancel')

    def alterar_vencimento(self, charge_id, vencimento):
        return self._cob('PUT', f'/v1/charge/{int(charge_id)}/billet',
                         json={'expire_at': vencimento.strftime('%Y-%m-%d')})

    def definir_aviso(self, charge_id, notification_url, custom_id=None):
        corpo = {'notification_url': notification_url}
        if custom_id:
            corpo['custom_id'] = str(custom_id)[:60]
        return self._cob('PUT', f'/v1/charge/{int(charge_id)}/metadata', json=corpo)

    def notificacao(self, token):
        """Eventos da notificação: lista ordenada de {id, type, status{current, previous},
        identifiers{charge_id}, value (centavos), received_by_bank_at, created_at}."""
        return self._cob('GET', f'/v1/notification/{token}').get('data') or []

    # ------------------------------------------------------------------ pix
    def saldo(self):
        j = self._pix('GET', '/v2/gn/saldo')
        return {'saldo': float(j.get('saldo') or 0), 'bloqueado': j.get('bloqueios') or j.get('bloqueado') or {}}

    def pix_recebidos(self, inicio, fim):
        """Pix recebidos entre duas datas (date). Devolve a lista ``pix`` da Efí."""
        def rfc(d, fim_dia=False):
            dt = datetime.combine(d, datetime.max.time() if fim_dia else datetime.min.time())
            return dt.replace(microsecond=0, tzinfo=timezone(timedelta(hours=-3))).isoformat()
        j = self._pix('GET', '/v2/pix', params={'inicio': rfc(inicio), 'fim': rfc(fim, True), 'paginacao.itensPorPagina': 1000})
        return j.get('pix') or []

    # ------------------------------------------------------------- extratos
    def _ext(self, caminho, timeout=(10, 60)):
        url = HOSTS['extratos'][self.amb] + caminho
        h = {'Authorization': 'Bearer ' + self._token('extratos')}
        with self._cert() as cert:
            r = requests.get(url, headers=h, cert=cert, timeout=timeout)
        if r.status_code >= 300:
            raise EfiErro(self._msg(r, 'A API Extratos da Efí recusou'), r.status_code, _json(r))
        return r

    def extrato_arquivos(self):
        """Arquivos CNAB 240 que a Efí gerou (agendamento diário, ~30 dias):
        [{nome, periodo_inicio, periodo_fim, ...}]."""
        return _json(self._ext('/v1/extrato-cnab/arquivos')) or []

    def extrato_baixar(self, nome):
        """Conteúdo (texto) do CNAB 240 do arquivo ``nome``. A Efí entrega um
        .zip (às vezes em base64); devolve o .txt de dentro."""
        import io
        import zipfile
        r = self._ext('/v1/extrato-cnab/download/' + nome)
        dados = r.content
        if not dados.startswith(b'PK'):
            dados = base64.b64decode(r.text.strip().strip('"'))
        z = zipfile.ZipFile(io.BytesIO(dados))
        txts = [n for n in z.namelist() if not n.endswith('/')]
        return z.read(txts[0]).decode('latin-1') if txts else ''


# ---------------------------------------------------------------------------
def _json(r):
    try:
        return r.json()
    except ValueError:
        return None


def _cent(v):
    from decimal import Decimal, ROUND_HALF_UP
    return int((Decimal(str(v)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _dig(v):
    return ''.join(ch for ch in str(v or '') if ch.isdigit())


def _pagador(p, com_email=False):
    # Nome e endereço do pagador vão em Maiúscula e minúscula (o cadastro guarda em caixa alta).
    # O e-mail só vai quando a Efí deve avisar o pagador (ela dispara o e-mail dela ao receber o endereço).
    from utils.texto import nome_proprio
    doc = _dig(p.get('documento'))
    nome = nome_proprio((p.get('nome') or '').strip())[:255]
    c = {}
    if len(doc) == 14:
        c['juridical_person'] = {'corporate_name': nome, 'cnpj': doc}
    else:
        c['name'] = nome
        c['cpf'] = doc
    if com_email and p.get('email'):
        c['email'] = p['email'][:255]
    fone = _dig(p.get('telefone'))
    if len(fone) in (10, 11):
        c['phone_number'] = fone
    e = p.get('endereco') or {}
    if e.get('cep') and e.get('logradouro') and e.get('cidade') and e.get('uf'):
        c['address'] = {'street': nome_proprio(e['logradouro'])[:255], 'number': (e.get('numero') or 'S/N')[:30],
                        'neighborhood': nome_proprio(e.get('bairro') or '')[:80], 'zipcode': _dig(e['cep']),
                        'city': nome_proprio(e['cidade'])[:80], 'state': e['uf'][:2].upper()}
        if e.get('complemento'):
            c['address']['complement'] = nome_proprio(e['complemento'])[:60]
    return c


def _config(juros_mes, multa_pct, dias_baixa):
    # Juros e multa só entram quando são maiores que zero: a Efí recusa o bloco com valor 0
    # ("A propriedade [interest.value] é obrigatória"); sem o bloco o boleto sai sem encargo.
    cfg = {}
    multa = int(round(float(multa_pct or 0) * 100))                   # 2,00 % -> 200
    if multa > 0:
        cfg['fine'] = multa
    juros = int(round(float(juros_mes or 0) * 100))                   # 1,00 % a.m. -> 100
    if juros > 0:
        cfg['interest'] = {'value': juros, 'type': 'monthly'}
    if dias_baixa is not None:
        cfg['days_to_write_off'] = max(0, min(120, int(dias_baixa)))
    return cfg


def _mensagem(txt):
    linhas = [l.strip()[:100] for l in str(txt).replace('\r', '').split('\n') if l.strip()][:4]
    return '\n'.join(linhas)
