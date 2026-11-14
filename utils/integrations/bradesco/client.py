# -*- coding: utf-8 -*-
"""Bradesco — API "Cobrança com QR Code" (boleto híbrido: código de barras + Pix) — 14/11/2026.

Portal: developers.bradesco.com.br (produto "Cobrança com QR Code" 1.8.3). Manual e collection
baixados em 12/11/2026; o sandbox foi provado de verdade (token, gerarBoleto, reservarLoc, listar).

* **Autenticação**: mTLS em TODA requisição (certificado A1 e-CNPJ em produção, autoassinado no
  sandbox — o .pem público fica cadastrado na credencial do portal). Token:
  ``POST /auth/server-mtls/v2/token`` com ``grant_type=client_credentials&client_id&client_secret``
  (form); Bearer por 3600 s.
* **Hosts**: produção ``openapi.bradesco.com.br``; sandbox ``openapisandbox.prebanco.com.br``.
  Os caminhos são os mesmos.
* **Endpoints usados**:
  - ``boleto-hibrido/cobranca-registro/v1/gerarBoleto`` — registra e devolve linha digitável,
    código de barras (em barras "NnWw", não em números) e o Pix copia-e-cola (``wqrcdPdraoMercd``).
    A API NÃO devolve PDF: o boleto é montado por nós (``utils/boleto_pdf.py``).
  - ``boleto-hibrido/cobranca-consulta-titulo/v1/consultar`` — situação (``codStatus``), pagamento.
  - ``boleto/cobranca-baixa/v1/baixar`` — cancelamento (código 57 = "conforme seu pedido").
  - ``boleto-hibrido/cobranca-lista/v1/listar`` — boletos LIQUIDADOS por data de pagamento (até 60
    dias, D-1). É por ele que sai a baixa automática: o aviso (webhook) do Bradesco exige certificado
    SSL EV/OV de 1 a 3 anos e o do Railway é Let's Encrypt de 90 dias.

Nada de credencial em log: as exceções carregam só o status HTTP e a mensagem do banco.
"""
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import requests

logger = logging.getLogger(__name__)

HOSTS = {'producao': 'https://openapi.bradesco.com.br', 'homologacao': 'https://openapisandbox.prebanco.com.br'}
TOKEN_PATH = '/auth/server-mtls/v2/token'
P_GERAR = '/boleto-hibrido/cobranca-registro/v1/gerarBoleto'
P_CONSULTAR = '/boleto-hibrido/cobranca-consulta-titulo/v1/consultar'
P_BAIXAR = '/boleto/cobranca-baixa/v1/baixar'
P_LISTAR = '/boleto-hibrido/cobranca-lista/v1/listar'
TIMEOUT = (10, 45)
COD_BAIXA_PEDIDO = 57                   # "conforme seu pedido"
DIAS_BAIXA_MAX = 365                    # decurso de prazo: até 365 dias no boleto com QR (manual 1.8.3)

# codStatus da consulta → nossa situação (a mesma palavra da Efí, para as telas e o núcleo)
STATUS_PAGO = {13, 61, 62, 66, 67}                       # pago no dia / pago / pago em cartório / crédito
STATUS_BAIXADO_MANUAL = {68, 69}                         # cobrado por baixa manual
STATUS_CANCELADO = {11, 52, 53, 54, 55, 57, 59, 60, 98}  # baixa solicitada / decurso / seu pedido / devolvido


class BradescoErro(Exception):
    def __init__(self, mensagem, http=None, corpo=None):
        super().__init__(mensagem)
        self.http = http
        self.corpo = corpo


@dataclass
class ContaBradesco:
    client_id: str
    client_secret: str
    cnpj: str                               # beneficiário (14 números)
    agencia: str                            # 4 números, sem dígito
    conta: str                              # até 7 números, sem dígito
    carteira: str = '09'
    ambiente: str = 'homologacao'
    pfx: bytes = b''                        # certificado do mTLS (.pfx/.p12)
    pfx_senha: str = ''
    _token: dict = field(default_factory=dict, repr=False)

    def __repr__(self):
        return f'ContaBradesco(ambiente={self.ambiente!r}, agencia={self.agencia!r}, cert={"sim" if self.pfx else "não"})'

    # partes do CNPJ do beneficiário no formato do banco
    @property
    def raiz(self):
        return int(_num(self.cnpj)[:8])

    @property
    def filial(self):
        return int(_num(self.cnpj)[8:12] or 0)

    @property
    def controle(self):
        return int(_num(self.cnpj)[12:14] or 0)

    @property
    def negociacao11(self):
        """Agência (4) + conta (7), sem dígitos — formato da lista e da baixa."""
        return int(_num(self.agencia).zfill(4)[-4:] + _num(self.conta).zfill(7)[-7:])

    @property
    def negociacao18(self):
        """Agência (4) + 0000000 + conta (7) — formato do registro (cnegocCobr)."""
        return int(_num(self.agencia).zfill(4)[-4:] + '0000000' + _num(self.conta).zfill(7)[-7:])


def _num(s):
    return re.sub(r'\D', '', str(s or ''))


def _ddmmaaaa(d):
    return int(d.strftime('%d%m%Y')) if d else 0


def _txt(s, n):
    """Texto do jeito que o Bradesco aceita: sem acento, maiúsculo, cortado."""
    import unicodedata
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    s = re.sub(r'[^A-Za-z0-9 .,/\-]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip().upper()[:n]


def _cent(v):
    return int((Decimal(str(v)) * 100).quantize(Decimal('1')))


class _CertTemp:
    """Arquivo PEM temporário (requests só aceita caminho), apagado ao sair."""

    def __init__(self, pfx, senha):
        self.pfx, self.senha, self.caminho = pfx, senha, None

    def __enter__(self):
        if not self.pfx:
            raise BradescoErro('Esta conta não tem o certificado (.pfx): o Bradesco exige em toda chamada.')
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12
        try:
            key, cert, extra = pkcs12.load_key_and_certificates(self.pfx, (self.senha or '').encode() or None)
        except Exception as exc:
            raise BradescoErro(f'Certificado inválido ou senha errada ({exc}).') from exc
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


class Bradesco:
    """Uma sessão por conta de cobrança. Token em memória até expirar."""

    def __init__(self, conta: ContaBradesco):
        self.c = conta
        self.host = HOSTS['producao' if conta.ambiente == 'producao' else 'homologacao']

    # ------------------------------------------------------------------ http
    def _token(self, cert):
        t = self.c._token
        if t and t['vence'] > time.time() + 30:
            return t['token']
        r = requests.post(self.host + TOKEN_PATH, cert=cert, timeout=TIMEOUT,
                          data={'grant_type': 'client_credentials', 'client_id': self.c.client_id,
                                'client_secret': self.c.client_secret})
        if r.status_code != 200:
            raise BradescoErro(_msg(r, 'O Bradesco não aceitou as credenciais'), r.status_code, _json(r))
        j = r.json()
        self.c._token = {'token': j['access_token'], 'vence': time.time() + int(j.get('expires_in') or 3600)}
        return j['access_token']

    def _post(self, caminho, corpo):
        with _CertTemp(self.c.pfx, self.c.pfx_senha) as cert:
            tok = self._token(cert)
            try:
                r = requests.post(self.host + caminho, json=corpo, cert=cert, timeout=TIMEOUT,
                                  headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'})
            except requests.RequestException as exc:
                raise BradescoErro(f'O Bradesco não respondeu ({exc.__class__.__name__}).') from exc
        j = _json(r)
        if r.status_code != 200 or (isinstance(j, dict) and str(j.get('statusHttp') or j.get('status') or 200) not in ('200', '201')):
            raise BradescoErro(_msg(r, 'O Bradesco recusou'), r.status_code, j)
        return j

    # ------------------------------------------------------------------ uso
    def testar(self):
        """Prova certificado + credenciais (token) e, em produção, a negociação: pede a lista de boletos
        pagos dos últimos 5 dias (só consulta, não emite nada). Carteira/agência/conta errada ou cobrança
        não liberada para o CNPJ = o banco recusa e a mensagem dele vai para a tela. Os indicadores 175/182
        (boleto com QR) só o registro de um boleto prova."""
        with _CertTemp(self.c.pfx, self.c.pfx_senha) as cert:
            self.c._token = {}
            self._token(cert)
        if self.c.ambiente != 'producao':
            return 'credenciais e certificado aceitos pelo Bradesco (sandbox)'
        hoje = date.today()
        try:
            n = len(self.liquidados(date.fromordinal(hoje.toordinal() - 5), hoje))
        except BradescoErro as exc:
            raise BradescoErro(f'a chave entrou, mas a cobrança recusou a carteira {self.c.carteira} / agência '
                               f'{self.c.agencia} / conta {self.c.conta}: {exc}', exc.http, exc.corpo) from exc
        return (f'credenciais, certificado e negociação aceitos (carteira {self.c.carteira}, agência {self.c.agencia}, '
                f'conta {self.c.conta}; {n} boleto(s) pago(s) nos últimos 5 dias)')

    def gerar_boleto(self, nosso_numero, seu_numero, pagador, valor, vencimento, emissao=None,
                     dias_baixa=None, juros_mes=None, multa_pct=None):
        """Registra o boleto com QR. ``pagador``: {documento, nome, endereco{cep, logradouro, numero,
        complemento, bairro, cidade, uf}, email}. Devolve a resposta do banco (dict)."""
        emissao = emissao or date.today()
        doc = _num(pagador.get('documento'))
        end = pagador.get('endereco') or {}
        cep = _num(end.get('cep')).zfill(8)
        juros = Decimal(str(juros_mes or 0))
        multa = Decimal(str(multa_pct or 0))
        dias = int(dias_baixa or 0)
        corpo = {
            'ctitloCobrCdent': int(_num(nosso_numero) or 0), 'registrarTitulo': 1, 'codUsuario': 'APISERVIC',
            'nroCpfCnpjBenef': self.c.raiz, 'filCpfCnpjBenef': str(self.c.filial), 'digCpfCnpjBenef': self.c.controle,
            'tipoAcesso': 2, 'cpssoaJuridContr': '', 'ctpoContrNegoc': '', 'nseqContrNegoc': '',
            'cidtfdProdCobr': int(_num(self.c.carteira) or 9), 'cnegocCobr': self.c.negociacao18, 'filler': '',
            'eNseqContrNegoc': '', 'tipoRegistro': 1, 'codigoBanco': 237, 'cprodtServcOper': '',
            'ctitloCliCdent': _txt(seu_numero, 25), 'demisTitloCobr': emissao.strftime('%d.%m.%Y'),
            'dvctoTitloCobr': vencimento.strftime('%d.%m.%Y'), 'cidtfdTpoVcto': '', 'vnmnalTitloCobr': _cent(valor),
            'cindcdEconmMoeda': 9, 'qmoedaNegocTitlo': 0, 'cespceTitloCobr': 2, 'cindcdAceitSacdo': 'N',
            'codNegativacao': 0, 'diasNegativacao': 0,
            'ctpoProteTitlo': 0, 'ctpoPrzProte': 0, 'ctpoProteDecurs': 0, 'ctpoPrzDecurs': 0,
            'cctrlPartcTitlo': 0, 'cformaEmisPplta': '02', 'cindcdPgtoParcial': 'N', 'qtdePgtoParcial': 0,
            'filler1': '',
            'ptxJuroVcto': float(round(juros / 30, 5)) if juros else 0, 'vdiaJuroMora': 0,
            'qdiaInicJuro': 1,                                  # manual: mínimo 1, mesmo sem juros
            'pmultaAplicVcto': float(round(multa, 5)) if multa else 0, 'vmultaAtrsoPgto': 0,
            'qdiaInicMulta': 1,
            'pdescBonifPgto01': 0, 'vdescBonifPgto01': 0, 'dlimDescBonif1': '',
            'pdescBonifPgto02': 0, 'vdescBonifPgto02': 0, 'dlimDescBonif2': '',
            'pdescBonifPgto03': 0, 'vdescBonifPgto03': 0, 'dlimDescBonif3': '',
            'ctpoPrzCobr': 0, 'pdescBonifPgto': 0, 'vdescBonifPgto': 0, 'dlimBonifPgto': '',
            'vabtmtTitloCobr': 0, 'viofPgtoTitlo': 0, 'filler2': '',
            'isacdoTitloCobr': _txt(pagador.get('nome'), 70),
            'elogdrSacdoTitlo': _txt(end.get('logradouro'), 40), 'enroLogdrSacdo': _txt(end.get('numero') or 'SN', 10),
            'ecomplLogdrSacdo': _txt(end.get('complemento'), 15),
            'ccepSacdoTitlo': int(cep[:5]), 'ccomplCepSacdo': int(cep[5:8]),
            'ebairoLogdrSacdo': _txt(end.get('bairro'), 40), 'imunSacdoTitlo': _txt(end.get('cidade'), 30),
            'csglUfSacdo': _txt(end.get('uf'), 2),
            'indCpfCnpjSacdo': 1 if len(doc) == 11 else 2, 'nroCpfCnpjSacdo': int(doc or 0),
            'renderEletrSacdo': (pagador.get('email') or '')[:70], 'cdddFoneSacdo': 0, 'cfoneSacdoTitlo': 0,
            'bancoDeb': 0, 'agenciaDeb': 0, 'agenciaDebDv': 0, 'contaDeb': 0, 'bancoCentProt': 0, 'agenciaDvCentPr': 0,
            'isacdrAvalsTitlo': '', 'elogdrSacdrAvals': '', 'enroLogdrSacdr': '', 'ecomplLogdrSacdr': '',
            'ccepSacdrTitlo': 0, 'ccomplCepSacdr': 0, 'ebairoLogdrSacdr': '', 'imunSacdrAvals': '', 'csglUfSacdr': '',
            'indCpfCnpjSacdr': 0, 'nroCpfCnpjSacdr': 0, 'renderEletrSacdr': '', 'cdddFoneSacdr': 0,
            'cfoneSacdrTitlo': 0, 'filler3': '', 'fase': 1, 'cindcdCobrMisto': 'S', 'ialiasAdsaoCta': '',
            'iconcPgtoSpi': '', 'caliasAdsaoCta': '', 'ilinkGeracQrcd': '', 'wqrcdPdraoMercd': '',
            'validadeAposVencimento': 0, 'filler4': '', 'idLoc': '',
        }
        if dias:
            corpo['qtdDecurPrz'] = min(dias, DIAS_BAIXA_MAX)
        return self._post(P_GERAR, corpo)

    def consultar(self, nosso_numero):
        return self._post(P_CONSULTAR, {
            'cpfCnpjUsuario': self.c.raiz, 'filialCnpjUsuario': self.c.filial,
            'controleCpfCnpjUsuario': self.c.controle, 'idProduto': int(_num(self.c.carteira) or 9),
            'contaProduto': self.c.negociacao11, 'nossoNumero': int(_num(nosso_numero)), 'seqTitulo': 0,
            'status': 0, 'nomePersonalizado': ''})

    def cancelar(self, nosso_numero):
        return self._post(P_BAIXAR, {
            'cpfCnpj': {'cpfCnpj': str(self.c.raiz), 'filial': str(self.c.filial), 'controle': str(self.c.controle).zfill(2)},
            'produto': int(_num(self.c.carteira) or 9), 'negociacao': self.c.negociacao11,
            'nossoNumero': str(_num(nosso_numero)), 'sequencia': 0, 'codigoBaixa': COD_BAIXA_PEDIDO})

    def liquidados(self, de, ate):
        """Boletos pagos entre ``de`` e ``ate`` (data do pagamento; até 60 dias; D-1). Lista de dicts."""
        out, pagina = [], 0
        for _ in range(40):                                  # 50 por página; trava contra laço
            j = self._post(P_LISTAR, {
                'cpfCnpj': {'cpfCnpj': self.c.raiz, 'filial': self.c.filial, 'controle': self.c.controle},
                'produto': int(_num(self.c.carteira) or 9), 'negociacao': self.c.negociacao11,
                'dataMovimentoDe': 0, 'dataMovimentoAte': 0,
                'dataPagamentoDe': _ddmmaaaa(de), 'dataPagamentoAte': _ddmmaaaa(ate),
                'origemPagamento': 0, 'valorTituloDe': 0, 'valorTituloAte': 0, 'paginaAnterior': pagina})
            out += j.get('titulos') or j.get('Titulos') or []
            mais = j.get('indMaisPagina') or j.get('IndicadorMaisPaginas') or j.get('indicadorMaisPaginas') or 'N'
            prox = int(j.get('pagina') or j.get('Pagina') or 0)
            if str(mais).upper() != 'S' or not prox or prox == pagina:
                break
            pagina = prox
        return out


# ---------------------------------------------------------------------------
# Leitura das respostas
# ---------------------------------------------------------------------------
def situacao_da_consulta(j):
    """(situação no nosso vocabulário, valor_pago, pago_em) a partir da consulta do título."""
    cod = int(j.get('codStatus') or 0)
    valor = Decimal(int(j.get('vlrPagto') or 0)) / 100 if j.get('vlrPagto') else None
    pago_em = data_ddmmaaaa(j.get('dtPagto'))
    if cod in STATUS_PAGO or (valor and pago_em):
        return 'paid', valor, pago_em
    if cod in STATUS_BAIXADO_MANUAL:
        return 'settled', None, None
    if cod in STATUS_CANCELADO:
        return 'canceled', None, None
    return 'waiting', None, None


def data_ddmmaaaa(v):
    s = _num(v)
    if not s or int(s) == 0:
        return None
    s = s.zfill(8)
    try:
        return date(int(s[4:8]), int(s[2:4]), int(s[0:2]))
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
        det = (j.get('mensagem') or j.get('message') or j.get('error_description') or j.get('error')
               or j.get('causa') or j.get('texto') or '')
        erros = j.get('erros') or j.get('errors') or []
        if erros and isinstance(erros, list):
            e = erros[0]
            det = f"{det} {e.get('mensagem') or e.get('message') or e}".strip() if isinstance(e, dict) else f'{det} {e}'
    return f'{prefixo} (HTTP {r.status_code}): {str(det)[:300]}'.strip()
