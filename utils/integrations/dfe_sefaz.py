# -*- coding: utf-8 -*-
"""
Núcleo de acesso ao NFeDistribuicaoDFe (distDFeInt) da SEFAZ — NF-e.

SÓ CONSULTA E LÊ. **NUNCA MANIFESTA.** Só existe o caminho nfeDistDFeInteresse
(distribuição por NSU). Este módulo NÃO monta nem envia envEvento/recepcaoEvento,
nem os eventos de manifestação do destinatário (tpEvento 210200/210210/210220/
210240). É leitura pura.

mTLS com o certificado A1 em MEMÓRIA (PFX->PEM em RAM, nada em disco), via
pyOpenSSL (o mesmo declarado na Fase 1). Ambiente de PRODUÇÃO (tpAmb=1); não há
homologação útil para distribuição (tpAmb=2 não devolve os documentos reais).

Adaptado do motor do nh-transportes (scripts/consulta_sefaz.py), provado em produção.
"""
import ssl
import xml.etree.ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.contrib.pyopenssl import PyOpenSSLContext
from OpenSSL import crypto as ossl
import certifi

from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, NoEncryption,
)

# --------------------------------------------------------------------------
# Parâmetros fixos (produção)
# --------------------------------------------------------------------------
TP_AMB = "1"   # 1 = PRODUÇÃO (nunca homologação: distDFeInt tpAmb=2 não traz docs reais)

# Webservice NACIONAL (AN) de Distribuição de DFe — produção.
ENDPOINT = "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx"
NS_WSDL = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"
NS_NFE = "http://www.portalfiscal.inf.br/nfe"
VERSAO = "1.35"   # layout do distDFeInt
TIMEOUT = 60

# --------------------------------------------------------------------------
# UF (sigla) -> código IBGE (cUFAutor). A Fase 1 expõe a sigla crua; aqui converte.
# --------------------------------------------------------------------------
UF_PARA_IBGE = {
    'RO': '11', 'AC': '12', 'AM': '13', 'RR': '14', 'PA': '15', 'AP': '16', 'TO': '17',
    'MA': '21', 'PI': '22', 'CE': '23', 'RN': '24', 'PB': '25', 'PE': '26', 'AL': '27',
    'SE': '28', 'BA': '29', 'MG': '31', 'ES': '32', 'RJ': '33', 'SP': '35', 'PR': '41',
    'SC': '42', 'RS': '43', 'MS': '50', 'MT': '51', 'GO': '52', 'DF': '53',
}


class UfInvalidaError(Exception):
    """UF do cliente ausente/desconhecida — não dá pra montar o cUFAutor.

    O chamador deve PULAR a captura e sinalizar; cUFAutor errado dá rejeição
    na SEFAZ, então nunca se chuta a UF.
    """


def cuf_autor(uf):
    """Converte a sigla da UF (ex.: 'GO') no código IBGE do cUFAutor (ex.: '52')."""
    sigla = (uf or '').strip().upper()
    if not sigla:
        raise UfInvalidaError('cliente sem UF cadastrada')
    codigo = UF_PARA_IBGE.get(sigla)
    if not codigo:
        raise UfInvalidaError(f'UF não reconhecida: {sigla!r}')
    return codigo


def _so_digitos(v):
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def tag_interessado(documento):
    """<CPF> para 11 dígitos (e-CPF), <CNPJ> para 14 (e-CNPJ). Detecta pelo
    comprimento — o distDFeInt aceita os dois (choice do schema)."""
    d = _so_digitos(documento)
    return f"<CPF>{d}</CPF>" if len(d) == 11 else f"<CNPJ>{d}</CNPJ>"


# --------------------------------------------------------------------------
# mTLS em memória (PFX->PEM em RAM; nada em disco)
# --------------------------------------------------------------------------
class AdaptadorCertMemoria(HTTPAdapter):
    """HTTPAdapter que faz mTLS com cert+chave carregados de MEMÓRIA (pyOpenSSL).
    O CA de verificação do servidor vem do certifi (funciona no Windows também)."""

    def __init__(self, cert_pem, key_pem, chain_pems, **kw):
        self._cert_pem = cert_pem
        self._key_pem = key_pem
        self._chain_pems = chain_pems or []
        super().__init__(**kw)

    def _montar_ctx(self):
        ctx = PyOpenSSLContext(ssl.PROTOCOL_TLS_CLIENT)
        _c = ctx._ctx
        _c.use_certificate(ossl.load_certificate(ossl.FILETYPE_PEM, self._cert_pem))
        _c.use_privatekey(ossl.load_privatekey(ossl.FILETYPE_PEM, self._key_pem))
        _c.check_privatekey()  # confirma que cert e chave batem
        for extra in self._chain_pems:
            _c.add_extra_chain_cert(ossl.load_certificate(ossl.FILETYPE_PEM, extra))
        ctx.load_verify_locations(cafile=certifi.where())
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def init_poolmanager(self, *a, **kw):
        kw["ssl_context"] = self._montar_ctx()
        return super().init_poolmanager(*a, **kw)

    def proxy_manager_for(self, *a, **kw):
        kw["ssl_context"] = self._montar_ctx()
        return super().proxy_manager_for(*a, **kw)


def montar_sessao_mtls(cert, chave_priv, cadeia):
    """Sessão requests com mTLS a partir do par (cert, chave, cadeia) devolvido
    por carregar_par_chave_cert() (objetos cryptography)."""
    cert_pem = cert.public_bytes(Encoding.PEM)
    key_pem = chave_priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    chain_pems = [c.public_bytes(Encoding.PEM) for c in (cadeia or [])]
    sess = requests.Session()
    sess.mount("https://", AdaptadorCertMemoria(cert_pem, key_pem, chain_pems))
    return sess


# --------------------------------------------------------------------------
# Montagem do SOAP (distDFeInt por NSU) + UMA requisição
# --------------------------------------------------------------------------
def montar_soap(documento, cuf, ult_nsu):
    """SOAP do nfeDistDFeInteresse (distDFeInt/distNSU). ult_nsu zero-pad 15."""
    ult_nsu_fmt = str(int(ult_nsu)).zfill(15)
    corpo = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        '<soap12:Body>'
        f'<nfeDistDFeInteresse xmlns="{NS_WSDL}">'
        '<nfeDadosMsg>'
        f'<distDFeInt xmlns="{NS_NFE}" versao="{VERSAO}">'
        f'<tpAmb>{TP_AMB}</tpAmb>'
        f'<cUFAutor>{cuf}</cUFAutor>'
        f'{tag_interessado(documento)}'
        f'<distNSU><ultNSU>{ult_nsu_fmt}</ultNSU></distNSU>'
        '</distDFeInt>'
        '</nfeDadosMsg>'
        '</nfeDistDFeInteresse>'
        '</soap12:Body>'
        '</soap12:Envelope>'
    )
    return corpo, ult_nsu_fmt


def montar_soap_chave(documento, cuf, chave):
    """SOAP do nfeDistDFeInteresse no modo consChNFe (busca por CHAVE, não por NSU).

    Devolve o nfeProc COMPLETO da nota, quando a SEFAZ o tem disponível — SEM
    manifestar (é o mesmo distDFeInt, só troca <distNSU> por <consChNFe>). É o
    caminho que converte um resumo (resNFe) na nota completa."""
    corpo = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        '<soap12:Body>'
        f'<nfeDistDFeInteresse xmlns="{NS_WSDL}">'
        '<nfeDadosMsg>'
        f'<distDFeInt xmlns="{NS_NFE}" versao="{VERSAO}">'
        f'<tpAmb>{TP_AMB}</tpAmb>'
        f'<cUFAutor>{cuf}</cUFAutor>'
        f'{tag_interessado(documento)}'
        f'<consChNFe><chNFe>{_so_digitos(chave)}</chNFe></consChNFe>'
        '</distDFeInt>'
        '</nfeDadosMsg>'
        '</nfeDistDFeInteresse>'
        '</soap12:Body>'
        '</soap12:Envelope>'
    )
    return corpo


def _post_dist(sess, soap):
    """POST único ao distDFeInt e parse do <retDistDFeInt>. Compartilhado por
    consultar()/consultar_chave(). Levanta RuntimeError em erro de transporte/HTTP/parse."""
    headers = {
        "Content-Type": ('application/soap+xml; charset=utf-8; '
                         f'action="{NS_WSDL}/nfeDistDFeInteresse"'),
        "User-Agent": "qualicontax/dfe-captura (leitura, nunca manifesta)",
    }
    try:
        r = sess.post(ENDPOINT, data=soap.encode("utf-8"), headers=headers, timeout=TIMEOUT)
    except Exception as exc:
        raise RuntimeError(f'falha no transporte HTTPS/mTLS com a SEFAZ: {exc}') from exc
    if r.status_code != 200:
        raise RuntimeError(f'SEFAZ respondeu HTTP {r.status_code}')
    try:
        env = ET.fromstring(r.content)
    except Exception as exc:
        raise RuntimeError(f'resposta da SEFAZ não é XML válido: {exc}') from exc
    ret = _find(env, "retDistDFeInt")
    if ret is None:
        raise RuntimeError('resposta da SEFAZ sem <retDistDFeInt>')
    return ret


def consultar(sess, documento, cuf, ult_nsu):
    """Faz UMA requisição ao distDFeInt (distNSU) e devolve (<retDistDFeInt>, ult_nsu_fmt).
    NUNCA faz loop. Levanta RuntimeError em erro de transporte/HTTP/parse."""
    soap, ult_nsu_fmt = montar_soap(documento, cuf, ult_nsu)
    return _post_dist(sess, soap), ult_nsu_fmt


def consultar_chave(sess, documento, cuf, chave):
    """Faz UMA requisição ao distDFeInt no modo consChNFe (por chave) e devolve o
    <retDistDFeInt>. NUNCA manifesta. Levanta RuntimeError em erro de transporte."""
    return _post_dist(sess, montar_soap_chave(documento, cuf, chave))


# --------------------------------------------------------------------------
# Helpers de parsing (ignora namespaces; busca por nome local)
# --------------------------------------------------------------------------
def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find(el, nome):
    for e in el.iter():
        if _local(e.tag) == nome:
            return e
    return None


def _text(el, nome):
    achado = _find(el, nome)
    return achado.text.strip() if (achado is not None and achado.text) else None
