# -*- coding: utf-8 -*-
"""Cora — API de boletos (v2), extrato e avisos de pagamento — 18/11/2026.

Anderson (18/11/2026): "Vamos montar o processo para o API do Cora para cobrança e extratos, já temos
isso pronto só para usar" (a conta Cora da Qualicontax já tem a integração via API liberada).

Documentação: developers.cora.com.br (modalidade "Integração Direta").

* **Autenticação**: mTLS em TODA chamada com o certificado (``certificate.pem``) e a chave privada
  (``private-key.key``) que o app do Cora gera em Conta → Integrações via APIs. Não há client secret:
  o token sai de ``POST /token`` com ``grant_type=client_credentials&client_id=int-...`` (form) e vale
  24 h. Cada ambiente tem o seu par (produção e stage não se misturam).
* **Hosts**: produção ``matls-clients.api.cora.com.br``; teste (stage) ``matls-clients.api.stage.cora.com.br``.
* **Endpoints usados**:
  - ``POST /v2/invoices/`` (cabeçalho ``Idempotency-Key`` = UUID novo por boleto) — registra o boleto
    com código de barras, linha digitável, PDF pronto (``payment_options.bank_slip.url``) e, pedindo
    ``payment_forms`` BANK_SLIP + PIX, o Pix copia e cola (``pix.emv``). Valor mínimo R$ 5,00. Só o
    1º item de ``services`` conta.
  - ``GET /v2/invoices/{id}`` — situação (OPEN, LATE, PAID, CANCELLED, IN_PAYMENT...), ``total_paid``,
    ``occurrence_date`` e ``payments[]``.
  - ``GET /v2/invoices?state=PAID&start&end`` — pagos por data de pagamento (rede de segurança).
  - ``DELETE /v2/invoices/{id}`` — cancelamento (204).
  - ``GET /bank-statement/statement?start&end`` — extrato: ``entries[]`` com ``type`` CREDIT/DEBIT,
    ``amount`` em centavos, ``transaction.description`` e ``counterParty{name, identity}``.
  - ``POST /endpoints`` — aviso de pagamento (webhook): ``resource=invoice``, ``trigger=paid``. O aviso
    é um POST SEM corpo: o id do boleto vem no cabeçalho ``webhook-resource-id``. Qualquer HTTPS serve
    (diferente do Bradesco, que exige certificado EV/OV).
  - ``POST /v2/invoices/pay`` — só no stage: simula o pagamento de um boleto.

Nada de credencial em log: as exceções carregam só o status HTTP e a mensagem do banco.
"""
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import requests

logger = logging.getLogger(__name__)

HOSTS = {'producao': 'https://matls-clients.api.cora.com.br', 'homologacao': 'https://matls-clients.api.stage.cora.com.br'}
P_TOKEN = '/token'
P_INVOICES = '/v2/invoices'
P_PAGAR_STAGE = '/v2/invoices/pay'
P_EXTRATO = '/bank-statement/statement'
P_ENDPOINTS = '/endpoints'
TIMEOUT = (10, 45)
VALOR_MINIMO = Decimal('5.00')          # o Cora recusa boleto abaixo de 500 centavos

# status do Cora → nossa situação (a mesma palavra da Efí, para as telas e o núcleo)
STATUS = {'OPEN': 'waiting', 'LATE': 'unpaid', 'PAID': 'paid', 'CANCELLED': 'canceled',
          'IN_PAYMENT': 'waiting', 'INITIATED': 'waiting', 'DRAFT': 'new', 'RECURRENCE_DRAFT': 'new'}


class CoraErro(Exception):
    def __init__(self, mensagem, http=None, corpo=None):
        super().__init__(mensagem)
        self.http = http
        self.corpo = corpo


@dataclass
class ContaCora:
    client_id: str
    pem: bytes = b''                        # certificate.pem + private-key.key, um atrás do outro
    ambiente: str = 'homologacao'
    _token: dict = field(default_factory=dict, repr=False)

    def __repr__(self):
        return f'ContaCora(ambiente={self.ambiente!r}, cert={"sim" if self.pem else "não"})'


def _num(s):
    return re.sub(r'\D', '', str(s or ''))


def _cent(v):
    return int((Decimal(str(v)) * 100).quantize(Decimal('1')))


def juntar_pem(certificado, chave):
    """Confere e junta o certificado e a chave privada que o app do Cora gerou num só PEM (o que o
    ``requests`` aceita em ``cert=``). Levanta ValueError com o motivo para a tela."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    cert_b = certificado if isinstance(certificado, bytes) else str(certificado or '').encode()
    key_b = chave if isinstance(chave, bytes) else str(chave or '').encode()
    try:
        cert = x509.load_pem_x509_certificate(cert_b)
    except Exception as exc:
        raise ValueError(f'O arquivo do certificado não é um certificate.pem válido ({exc.__class__.__name__}).') from exc
    try:
        key = serialization.load_pem_private_key(key_b, password=None)
    except Exception as exc:
        raise ValueError(f'O arquivo da chave não é uma private-key.key válida ({exc.__class__.__name__}).') from exc
    if cert.public_key().public_numbers() != key.public_key().public_numbers():
        raise ValueError('A chave privada não é a deste certificado: baixe os dois arquivos da mesma geração no app do Cora.')
    pem = cert.public_bytes(serialization.Encoding.PEM)
    pem += key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return pem


def validade_do_pem(pem):
    """Vencimento do certificado dentro do PEM (date) ou None."""
    try:
        from cryptography import x509
        c = x509.load_pem_x509_certificate(pem if isinstance(pem, bytes) else str(pem).encode())
        fim = getattr(c, 'not_valid_after_utc', None) or c.not_valid_after
        return fim.date()
    except Exception:
        return None


class _CertTemp:
    """Arquivo PEM temporário (requests só aceita caminho), apagado ao sair."""

    def __init__(self, pem):
        self.pem, self.caminho = pem, None

    def __enter__(self):
        if not self.pem:
            raise CoraErro('Esta conta não tem o certificado e a chave da API do Cora.')
        fd, self.caminho = tempfile.mkstemp(suffix='.pem')
        with os.fdopen(fd, 'wb') as f:
            f.write(self.pem)
        return self.caminho

    def __exit__(self, *a):
        if self.caminho:
            try:
                os.remove(self.caminho)
            except OSError:
                pass


class Cora:
    """Uma sessão por conta de cobrança. Token em memória até expirar."""

    def __init__(self, conta: ContaCora):
        self.c = conta
        self.host = HOSTS['producao' if conta.ambiente == 'producao' else 'homologacao']

    # ------------------------------------------------------------------ http
    def _token(self, cert):
        t = self.c._token
        if t and t['vence'] > time.time() + 60:
            return t['token']
        try:
            r = requests.post(self.host + P_TOKEN, cert=cert, timeout=TIMEOUT,
                              data={'grant_type': 'client_credentials', 'client_id': self.c.client_id},
                              headers={'Content-Type': 'application/x-www-form-urlencoded'})
        except requests.exceptions.SSLError as exc:
            raise CoraErro('O Cora não aceitou o certificado (SSL): confira se o certificate.pem e a private-key.key '
                           f'são deste ambiente ({self.c.ambiente}).') from exc
        except requests.RequestException as exc:
            raise CoraErro(f'O Cora não respondeu ({exc.__class__.__name__}).') from exc
        if r.status_code != 200:
            raise CoraErro(_msg(r, 'O Cora não aceitou as credenciais'), r.status_code, _json(r))
        j = r.json()
        self.c._token = {'token': j['access_token'], 'vence': time.time() + int(j.get('expires_in') or 3600)}
        return j['access_token']

    def _req(self, metodo, caminho, corpo=None, params=None, idem=False, ok=(200, 201, 204)):
        with _CertTemp(self.c.pem) as cert:
            tok = self._token(cert)
            h = {'Authorization': 'Bearer ' + tok, 'Accept': 'application/json'}
            if corpo is not None:
                h['Content-Type'] = 'application/json'
            if idem:
                h['Idempotency-Key'] = str(uuid.uuid4())
            try:
                r = requests.request(metodo, self.host + caminho, json=corpo, params=params, cert=cert,
                                     timeout=TIMEOUT, headers=h)
            except requests.RequestException as exc:
                raise CoraErro(f'O Cora não respondeu ({exc.__class__.__name__}).') from exc
        if r.status_code not in ok:
            raise CoraErro(_msg(r, 'O Cora recusou'), r.status_code, _json(r))
        return _json(r) if r.content else {}

    # ------------------------------------------------------------------ uso
    def testar(self):
        """Prova certificado + client id (token) e a conta: pede o extrato de hoje (só consulta). Devolve texto."""
        with _CertTemp(self.c.pem) as cert:
            self.c._token = {}
            self._token(cert)
        hoje = date.today()
        j = self._req('GET', P_EXTRATO, params={'start': date.fromordinal(hoje.toordinal() - 1).isoformat(),
                                                'end': hoje.isoformat(), 'perPage': 1})
        cab = (j or {}).get('header') or {}
        dono = cab.get('businessName') or ''
        doc = _num(cab.get('businessDocument'))
        amb = ' (ambiente de teste do Cora)' if self.c.ambiente != 'producao' else ''
        return ('credenciais, certificado e conta aceitos' + (f' — conta de {dono}' if dono else '')
                + (f', CNPJ {doc}' if doc else '') + amb)

    def criar_boleto(self, codigo, pagador, nome_servico, descricao, valor, vencimento, juros_mes=None, multa_pct=None,
                     com_pix=True, email_aviso=None):
        """Registra o boleto. ``pagador``: {documento, nome, email, endereco{cep, logradouro, numero, complemento,
        bairro, cidade, uf}}. O endereço só vai completo (o Cora exige todos os campos quando vai algum).
        Devolve a resposta do banco (dict)."""
        valor = Decimal(str(valor))
        if valor < VALOR_MINIMO:
            raise CoraErro(f'O Cora só emite boleto de R$ {VALOR_MINIMO:.2f} ou mais.'.replace('.', ','))
        doc = _num(pagador.get('documento'))
        if len(doc) not in (11, 14):
            raise CoraErro('O pagador precisa de CPF ou CNPJ para o boleto do Cora.')
        nome = re.sub(r'\s+', ' ', str(pagador.get('nome') or '')).strip()[:255]
        if len(nome) < 3:
            raise CoraErro('O nome do pagador precisa ter pelo menos 3 letras.')
        cliente = {'name': nome, 'document': {'identity': doc, 'type': 'CPF' if len(doc) == 11 else 'CNPJ'}}
        email = (pagador.get('email') or '').strip()
        if email and '@' in email and ' ' not in email:
            cliente['email'] = email[:255]
        end = pagador.get('endereco') or {}
        cep = _num(end.get('cep'))
        obrig = {'street': (end.get('logradouro') or '').strip()[:255], 'number': str(end.get('numero') or '').strip()[:30],
                 'district': (end.get('bairro') or '').strip()[:120], 'city': (end.get('cidade') or '').strip()[:120],
                 'state': (end.get('uf') or end.get('estado') or '').strip().upper()[:2], 'zip_code': cep}
        if all(obrig.values()) and len(cep) == 8 and len(obrig['state']) == 2:
            if (end.get('complemento') or '').strip():
                obrig['complement'] = end['complemento'].strip()[:120]
            cliente['address'] = obrig
        corpo = {
            'code': str(codigo or '')[:64],
            'customer': cliente,
            'services': [{'name': (nome_servico or 'Serviços')[:255], 'description': (descricao or '')[:255] or None,
                          'amount': _cent(valor)}],
            'payment_terms': {'due_date': vencimento.strftime('%Y-%m-%d') if hasattr(vencimento, 'strftime') else str(vencimento)},
            'payment_forms': ['BANK_SLIP', 'PIX'] if com_pix else ['BANK_SLIP'],
        }
        if not corpo['services'][0]['description']:
            del corpo['services'][0]['description']
        juros = Decimal(str(juros_mes or 0))
        multa = Decimal(str(multa_pct or 0))
        if multa > 0:
            corpo['payment_terms']['fine'] = {'rate': float(round(multa, 2))}
        if juros > 0:
            corpo['payment_terms']['interest'] = {'rate': float(round(juros, 2))}
        if email_aviso:
            # o Cora manda o e-mail dele ao pagador; o padrão do escritório é o NOSSO app avisar (avisa_por='app')
            corpo['notification'] = {'name': nome, 'channels': [{'channel': 'EMAIL', 'contact': email_aviso,
                                                                 'rules': ['NOTIFY_ON_DUE_DATE', 'NOTIFY_WHEN_PAID']}]}
        return self._req('POST', P_INVOICES + '/', corpo, idem=True)

    def consultar(self, invoice_id):
        return self._req('GET', f'{P_INVOICES}/{invoice_id}')

    def cancelar(self, invoice_id):
        """DELETE: 204 cancelado. 422 PAY-0005 = o estado não permite (já cancelado / pago)."""
        return self._req('DELETE', f'{P_INVOICES}/{invoice_id}')

    def pagos(self, de, ate):
        """Boletos PAGOS entre ``de`` e ``ate`` (data do pagamento), todas as páginas. Lista de dicts."""
        out, pagina = [], 1
        for _ in range(50):
            j = self._req('GET', P_INVOICES, params={'state': 'PAID', 'start': de.isoformat(), 'end': ate.isoformat(),
                                                     'page': pagina, 'perPage': 200})
            itens = (j or {}).get('items') or []
            out += itens
            total = int((j or {}).get('totalItems') or 0)
            if len(itens) < 200 or len(out) >= total:
                break
            pagina += 1
        return out

    def pagar_no_stage(self, invoice_id):
        """Só no ambiente de teste: o Cora simula o pagamento integral hoje."""
        if self.c.ambiente == 'producao':
            raise CoraErro('Simular pagamento só existe no ambiente de teste do Cora.')
        return self._req('POST', P_PAGAR_STAGE, {'id': invoice_id}, idem=True)

    def extrato(self, de, ate, tipo=None):
        """Lançamentos (``entries``) entre ``de`` e ``ate``, todas as páginas; ``header`` e ``aggregations`` do 1º."""
        out, pagina, cab = [], 1, {}
        for _ in range(100):
            params = {'start': de.isoformat(), 'end': ate.isoformat(), 'page': pagina, 'perPage': 500}
            if tipo:
                params['type'] = tipo
            j = self._req('GET', P_EXTRATO, params=params) or {}
            if pagina == 1:
                cab = {k: j.get(k) for k in ('header', 'aggregations', 'start', 'end')}
            itens = j.get('entries') or []
            out += itens
            if len(itens) < 500:
                break
            pagina += 1
        return {'entries': out, **cab}

    def endpoints(self):
        j = self._req('GET', P_ENDPOINTS)
        return j if isinstance(j, list) else (j.get('items') or j.get('endpoints') or [])

    def criar_endpoint(self, url, resource='invoice', trigger='paid'):
        # readTimeout maior que os 2 s padrão: a nossa resposta consulta o boleto no próprio Cora antes de responder
        return self._req('POST', P_ENDPOINTS, {'url': url, 'resource': resource, 'trigger': trigger,
                                               'connectionTimeout': 3000, 'readTimeout': 15000}, idem=True)

    def excluir_endpoint(self, endpoint_id):
        return self._req('DELETE', f'{P_ENDPOINTS}/{endpoint_id}')


# ---------------------------------------------------------------------------
# Leitura das respostas
# ---------------------------------------------------------------------------
def situacao_da_consulta(j):
    """(situação no nosso vocabulário, valor_pago, pago_em) a partir da consulta/listagem do boleto."""
    st = str((j or {}).get('status') or '').upper()
    sit = STATUS.get(st, 'waiting')
    if sit != 'paid':
        return sit, None, None
    cent = j.get('total_paid')
    if not cent:
        cent = sum(int(p.get('total_paid') or 0) for p in (j.get('payments') or []) if str(p.get('status') or '').upper() in ('SUCCESS', 'PAID', ''))
    valor = (Decimal(int(cent)) / 100).quantize(Decimal('0.01')) if cent else None
    pago_em = _dia(j.get('occurrence_date')) or _dia(j.get('paid_at'))
    if not pago_em:
        pags = [p for p in (j.get('payments') or []) if p.get('finalized_at') or p.get('created_at')]
        if pags:
            pago_em = _dia(pags[-1].get('finalized_at') or pags[-1].get('created_at'))
    return 'paid', valor, pago_em


def _dia(s):
    """'2026-11-18' / '2026-11-18T13:32:56Z' → date em Brasília (o Cora escreve em UTC)."""
    s = str(s or '').strip()
    if not s:
        return None
    try:
        if len(s) == 10:
            return date.fromisoformat(s)
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if dt.tzinfo is not None:
            dt = dt.astimezone(ZoneInfo('America/Sao_Paulo'))
        return dt.date()
    except ValueError:
        return None


def _json(r):
    try:
        return r.json()
    except ValueError:
        return {'texto': (r.text or '')[:500]}


def _msg(r, prefixo):
    j = _json(r)
    det = ''
    if isinstance(j, dict):
        det = (j.get('message') or j.get('error_description') or j.get('error') or j.get('detail') or j.get('texto') or '')
        cod = j.get('code')
        if cod and str(cod) not in str(det):
            det = f'{cod} {det}'.strip()
        erros = j.get('errors') or []
        if erros and isinstance(erros, list):
            e = erros[0]
            det = f"{det} {e.get('message') or e.get('description') or e}".strip() if isinstance(e, dict) else f'{det} {e}'
    if not det:
        det = {401: 'token inválido ou vencido (credencial de outro ambiente?)', 404: 'não encontrado nesta conta',
               415: 'Content-Type recusado', 422: 'o estado atual não permite'}.get(r.status_code, '')
    return f'{prefixo} (HTTP {r.status_code}): {str(det)[:300]}'.strip().rstrip(':')
