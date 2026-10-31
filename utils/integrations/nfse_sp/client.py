# -*- coding: utf-8 -*-
"""Cliente SOAP do Web Service da NFS-e de São Paulo — certificado, assinatura, chamada.

Manual de Utilização do Web Service, versão 3.3.4 (nov/2025). Endereço novo
``nfews.prefeitura.sp.gov.br``: o antigo ``nfe.prefeitura.sp.gov.br`` não aceita
o leiaute 2 (reforma tributária) e vai ser desligado.

O QUE É REAPROVEITADO
---------------------
* certificado: ``nfse_adn.client.resolver_certificado`` + ``abrir material``
  (mesmo .pfx, mesma senha cifrada, mesma sessão mTLS em memória do DFe);
* assinatura XML: o mesmo signxml do ``dfe_manifesto`` — a prefeitura também
  exige RSA-SHA1 + SHA-1, enveloped, C14N, só o certificado final (manual §3.2.3).

O QUE É DIFERENTE DO NACIONAL
-----------------------------
* SOAP 1.2, um método por operação, todos com a mesma cara:
  ``<Metodo>Request{VersaoSchema:int, MensagemXML:string}`` → ``RetornoXML``.
  A mensagem vai inteira dentro de CDATA (manual §4.3.1).
* A mensagem XML é assinada INTEIRA (referência vazia), não um nó por Id.
* O retorno sempre tem ``Cabecalho/Sucesso`` (true/false) e listas ``Erro`` /
  ``Alerta`` com ``Codigo`` e ``Descricao``. Alerta não impede o sucesso.

Este módulo não conhece banco nem campo fiscal: quem monta RPS é o
``rps_builder``; quem grava é o ``repositorio``.
"""
import logging
import re
from dataclasses import dataclass

from lxml import etree

from models.dfe_certificado import DfeCertificado
from utils import dropbox_sync
from utils.certificado_digital import (CertificadoError, carregar_par_chave_cert,
                                       decifrar_senha)
from utils.integrations.dfe_sefaz import montar_sessao_mtls
from utils.integrations.nfse_adn.client import (Certificado, SemCertificado,
                                                resolver_certificado)

logger = logging.getLogger(__name__)

URL = 'https://nfews.prefeitura.sp.gov.br/lotenfe.asmx'
NS = 'http://www.prefeitura.sp.gov.br/nfe'
NS_SOAP12 = 'http://www.w3.org/2003/05/soap-envelope'
TIMEOUT = 90

# Nome do método SOAP -> soapAction (do WSDL baixado em 31/10/2026).
METODOS = {
    'EnvioRPS': 'envioRPS',
    'EnvioLoteRPS': 'envioLoteRPS',
    'TesteEnvioLoteRPS': 'testeenvio',
    'ConsultaNFe': 'consultaNFe',
    'ConsultaNFeRecebidas': 'consultaNFeRecebidas',
    'ConsultaNFeEmitidas': 'consultaNFeEmitidas',
    'ConsultaLote': 'consultaLote',
    'ConsultaInformacoesLote': 'consultaInformacoesLote',
    'CancelamentoNFe': 'cancelamentoNFe',
    'ConsultaCNPJ': 'consultaCNPJ',
}


class NFSeSPError(Exception):
    """Falha de transporte ou de SOAP (não é rejeição de negócio)."""


@dataclass
class Retorno:
    """Resposta já aberta: ``sucesso`` + erros/alertas + a árvore para quem precisar."""
    sucesso: bool
    erros: list          # [(codigo, descricao)]
    alertas: list        # [(codigo, descricao)]
    xml: str
    raiz: 'etree._Element'

    def __str__(self):
        if self.sucesso and not self.alertas:
            return 'sucesso'
        partes = []
        for c, d in self.erros:
            partes.append(f'erro {c}: {d}')
        for c, d in self.alertas:
            partes.append(f'alerta {c}: {d}')
        return ('sucesso; ' if self.sucesso else 'FALHA; ') + ' | '.join(partes)


# --------------------------------------------------------------------------
# Certificado e sessão
# --------------------------------------------------------------------------

class SessaoSP:
    """Fábrica de sessões mTLS, uma por chamada — mesma razão da ``SessaoADN``:
    o adaptador pyOpenSSL não aceita reaproveitar o contexto depois de uma
    conexão, e o servidor fecha a conexão entre chamadas."""

    def __init__(self, cert: Certificado, x509, chave_priv, cadeia):
        self.cert = cert
        self.x509 = x509
        self.chave_priv = chave_priv
        self._cadeia = cadeia

    def post(self, data, headers):
        s = montar_sessao_mtls(self.x509, self.chave_priv, self._cadeia)
        try:
            return s.post(URL, data=data, headers=headers, timeout=TIMEOUT)
        finally:
            s.close()


def abrir_sessao(cliente_id: int) -> SessaoSP:
    """Certificado do cliente (ou da raiz do CNPJ) aberto e pronto para assinar e
    falar com a prefeitura. Levanta ``SemCertificado``."""
    cert = resolver_certificado(cliente_id)
    senha_cif = DfeCertificado.get_senha_cifrada(cert.dono_id or cert.cliente_id)
    if not senha_cif:
        raise SemCertificado(f'Certificado do cliente {cert.dono_id} sem senha armazenada.')
    try:
        senha = decifrar_senha(senha_cif)
    except CertificadoError as exc:
        raise SemCertificado(f'Falha ao decifrar a senha do certificado: {exc}') from exc
    pfx = dropbox_sync._service.download_file(cert.dropbox_path)
    if not pfx:
        raise SemCertificado(f'Falha ao baixar o .pfx de {cert.dropbox_path}.')
    try:
        chave_priv, x509, cadeia = carregar_par_chave_cert(pfx, senha)
    except CertificadoError as exc:
        raise SemCertificado(f'Falha ao abrir o certificado: {exc}') from exc
    return SessaoSP(cert, x509, chave_priv, cadeia)


# --------------------------------------------------------------------------
# Assinatura XML (manual §3.2.3): enveloped, RSA-SHA1, SHA-1, C14N, só o cert.
# --------------------------------------------------------------------------

def assinar_mensagem(xml: str, sessao: SessaoSP) -> str:
    """Assina a mensagem de pedido INTEIRA (referência vazia).

    Mesma receita do ``dfe_manifesto.assinar_evento`` — SHA-1 porque o manual
    da prefeitura exige, e sem prefixo ``ds:`` para o parser de lá achar a tag.
    """
    from utils.integrations.dfe_manifesto import _garantir_extension_policy
    _garantir_extension_policy()
    from signxml import XMLSigner, methods
    from signxml.util import namespaces as _ns_signxml
    from cryptography.hazmat.primitives import serialization

    class _Assinador(XMLSigner):
        def check_deprecated_methods(self):      # SHA-1 é exigência externa
            return None

    assinador = _Assinador(
        method=methods.enveloped,
        signature_algorithm='rsa-sha1',
        digest_algorithm='sha1',
        c14n_algorithm='http://www.w3.org/TR/2001/REC-xml-c14n-20010315',
    )
    assinador.excise_empty_xmlns_declarations = True
    assinador.namespaces = {None: _ns_signxml.ds}
    cert_pem = sessao.x509.public_bytes(serialization.Encoding.PEM).decode('ascii')
    raiz = etree.fromstring(xml.encode('utf-8'))
    assinado = assinador.sign(raiz, key=sessao.chave_priv, cert=cert_pem)
    return etree.tostring(assinado, encoding='unicode')


# --------------------------------------------------------------------------
# Chamada SOAP
# --------------------------------------------------------------------------

def _envelope(metodo: str, versao_schema: int, mensagem_xml: str) -> bytes:
    # CDATA: a mensagem já é XML assinado; escapar caractere a caractere seria
    # a outra opção do manual e qualquer deslize quebraria a assinatura.
    mensagem_xml = mensagem_xml.replace(']]>', ']]]]><![CDATA[>')
    corpo = (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<soap12:Envelope xmlns:soap12="{NS_SOAP12}">'
        f'<soap12:Body>'
        f'<{metodo}Request xmlns="{NS}">'
        f'<VersaoSchema>{int(versao_schema)}</VersaoSchema>'
        f'<MensagemXML><![CDATA[{mensagem_xml}]]></MensagemXML>'
        f'</{metodo}Request>'
        f'</soap12:Body>'
        f'</soap12:Envelope>'
    )
    return corpo.encode('utf-8')


def _abrir_retorno(xml_retorno: str) -> Retorno:
    raiz = etree.fromstring(xml_retorno.encode('utf-8'))

    def _loc(tag):
        return tag.split('}')[-1]

    def _lista(nome):
        out = []
        for el in raiz.iter():
            if _loc(el.tag) == nome:
                cod = desc = ''
                for f in el:
                    if _loc(f.tag) == 'Codigo':
                        cod = (f.text or '').strip()
                    elif _loc(f.tag) == 'Descricao':
                        desc = (f.text or '').strip()
                out.append((cod, desc))
        return out

    sucesso = False
    for el in raiz.iter():
        if _loc(el.tag) == 'Sucesso':
            sucesso = (el.text or '').strip().lower() == 'true'
            break
    return Retorno(sucesso=sucesso, erros=_lista('Erro'), alertas=_lista('Alerta'),
                   xml=xml_retorno, raiz=raiz)


def chamar(sessao: SessaoSP, metodo: str, mensagem_xml: str, versao_schema: int = 1,
           assinar: bool = True) -> Retorno:
    """Assina (se pedido), envelopa e chama o método. Devolve o ``Retorno`` aberto.

    Falha de HTTP/SOAP levanta ``NFSeSPError``; rejeição de negócio NÃO levanta:
    vem em ``Retorno.sucesso=False`` com os códigos, porque é dado a gravar.
    """
    if metodo not in METODOS:
        raise ValueError(f'Método desconhecido: {metodo}')
    if assinar:
        mensagem_xml = assinar_mensagem(mensagem_xml, sessao)
    headers = {
        'Content-Type': 'application/soap+xml; charset=utf-8',
        'SOAPAction': f'{NS}/ws/{METODOS[metodo]}',
    }
    try:
        resp = sessao.post(_envelope(metodo, versao_schema, mensagem_xml), headers)
    except Exception as exc:                       # rede, TLS, timeout
        raise NFSeSPError(f'{metodo}: {exc.__class__.__name__}: {exc}') from exc
    if resp.status_code != 200:
        raise NFSeSPError(f'{metodo}: HTTP {resp.status_code}: {resp.text[:600]}')

    # <RetornoXML> é string: o XML de retorno vem escapado dentro do SOAP.
    env = etree.fromstring(resp.content)
    ret = None
    for el in env.iter():
        if el.tag.split('}')[-1] == 'RetornoXML':
            ret = el.text
            break
    if not ret:
        raise NFSeSPError(f'{metodo}: resposta sem RetornoXML: {resp.text[:600]}')
    return _abrir_retorno(ret)


# --------------------------------------------------------------------------
# Pedidos prontos (só leitura)
# --------------------------------------------------------------------------

def _cnpj_tag(doc: str) -> str:
    doc = re.sub(r'\D', '', doc or '')
    return f'<CNPJ>{doc}</CNPJ>' if len(doc) == 14 else f'<CPF>{doc}</CPF>'


def _pedido(nome: str, miolo: str) -> str:
    """Raiz no namespace da prefeitura e filhos SEM namespace.

    Os XSD são ``elementFormDefault="unqualified"``: só o elemento raiz é
    qualificado. Com ``xmlns="..."`` na raiz os filhos herdariam o namespace e o
    servidor responde 1001 "invalid child element 'Cabecalho' ... expected:
    'Cabecalho'" (os dois iguais no texto, diferentes no namespace). A própria
    resposta dele vem com ``<Cabecalho xmlns="">``. Prefixar a raiz resolve.
    """
    return f'<nfe:{nome} xmlns:nfe="{NS}">{miolo}</nfe:{nome}>'


def consultar_emitidas(sessao: SessaoSP, cnpj_prestador: str, inscricao_municipal: str,
                       data_ini: str, data_fim: str, pagina: int = 1,
                       versao: int = 1) -> Retorno:
    """NFS-e emitidas pelo prestador no período (AAAA-MM-DD). 50 por página.

    ``CPFCNPJRemetente`` é o dono do certificado; ``CPFCNPJ`` é o prestador
    consultado. Para a própria empresa, os dois são iguais.
    """
    im = re.sub(r'\D', '', inscricao_municipal or '')
    xml = _pedido('PedidoConsultaNFePeriodo', (
        f'<Cabecalho Versao="{int(versao)}">'
        f'<CPFCNPJRemetente>{_cnpj_tag(sessao.cert.cnpj)}</CPFCNPJRemetente>'
        f'<CPFCNPJ>{_cnpj_tag(cnpj_prestador)}</CPFCNPJ>'
        f'<Inscricao>{im}</Inscricao>'
        f'<dtInicio>{data_ini}</dtInicio>'
        f'<dtFim>{data_fim}</dtFim>'
        f'<NumeroPagina>{int(pagina)}</NumeroPagina>'
        f'</Cabecalho>'
    ))
    return chamar(sessao, 'ConsultaNFeEmitidas', xml, versao_schema=versao)


def consultar_cnpj(sessao: SessaoSP, cnpj: str, versao: int = 1) -> Retorno:
    """Inscrições municipais (CCM) de um CNPJ e se emite NFS-e. Não exige IM."""
    xml = _pedido('PedidoConsultaCNPJ', (
        f'<Cabecalho Versao="{int(versao)}">'
        f'<CPFCNPJRemetente>{_cnpj_tag(sessao.cert.cnpj)}</CPFCNPJRemetente>'
        f'</Cabecalho>'
        f'<CNPJContribuinte>{_cnpj_tag(cnpj)}</CNPJContribuinte>'
    ))
    return chamar(sessao, 'ConsultaCNPJ', xml, versao_schema=versao)
