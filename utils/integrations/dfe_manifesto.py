# -*- coding: utf-8 -*-
"""Manifestação do destinatário — SOMENTE "Ciência da Operação" (210210).

POR QUE ESTE ARQUIVO EXISTE, E O QUE ELE NÃO FAZ
------------------------------------------------
A regra da casa é **nunca manifestar**: o ato é IRREVERSÍVEL, tem efeito fiscal
e aparece na SEFAZ como sendo do cliente, feito com o certificado dele. Em
02/09/2026 o Anderson abriu uma exceção nominal, depois de eu levantar a regra:
testar na empresa 152 (AUTO POSTO EASY PETRO) se a Ciência libera o XML
completo dos 13 resumos presos.

Daí as três travas deste módulo:

1. **só o evento 210210** ("Ciência da Operação"), a camada mais branda — ela
   declara apenas que a nota EXISTE e é do conhecimento do destinatário. Não
   confirma a operação, não reconhece mercadoria, não gera obrigação. Os outros
   três eventos (Confirmação 210200, Desconhecimento 210220, Operação não
   Realizada 210240) NÃO estão implementados e não devem ser;
2. **nada dispara sozinho.** Não há cron, não há laço, não há "para cada nota".
   Uma chamada, uma nota — quem chama é uma ação humana;
3. **montar e assinar são separados de enviar.** Dá para conferir o XML antes
   de ele existir na SEFAZ, que é o único momento em que ainda dá para voltar
   atrás.

POR QUE A CIÊNCIA DESTRAVA O XML
--------------------------------
Descoberto em 02/09/2026 com a resposta crua da SEFAZ: para o destinatário que
não manifestou, o ``consChNFe`` responde ``138 Documento localizado`` e devolve
um ``resNFe`` de 546 bytes — o resumo. A nota completa (``nfeProc``, com os
itens) só é entregue depois da manifestação. É isso que mantém 903 resumos
presos em 103 empresas.

ASSINATURA
----------
O evento vai assinado em XML-DSig sobre a tag ``infEvento``: enveloped, C14N
(sem comentários), SHA-1 no digest e RSA-SHA1 na assinatura — que é o que o
layout da NF-e ainda exige. Usa ``signxml`` (5.0.0, a última que convive com o
``cryptography==43.0.1`` que o projeto fixa; a 5.1 arrasta o cryptography para
50 e quebraria o carregamento de certificado de todas as empresas).
"""
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from lxml import etree

logger = logging.getLogger(__name__)

NS_NFE = 'http://www.portalfiscal.inf.br/nfe'

#: Ambiente Nacional. A manifestação NÃO vai para a SEFAZ do estado: o evento
#: do destinatário é recebido pelo AN, e por isso ``cOrgao`` é 91 e não a UF.
#: O serviço da NF-e 4.0 leva o sufixo "4" no nome — igual ao
#: NFeDistribuicaoDFe que a captura já usa. Sem ele, a SEFAZ responde HTTP 404
#: (foi o que aconteceu na primeira tentativa, em 02/09/2026: 404 = o evento
#: nem chegou a existir, que é a falha boa de se ter num ato irreversível).
ENDPOINT_EVENTO = ('https://www1.nfe.fazenda.gov.br/NFeRecepcaoEvento4/'
                   'NFeRecepcaoEvento4.asmx')
NS_WSDL_EVENTO = 'http://www.portalfiscal.inf.br/nfe/wsdl/NFeRecepcaoEvento4'
#: O nome da OPERAÇÃO não é o do serviço: o WSDL declara ``nfeRecepcaoEventoNF``
#: (com NF no fim). Mandar ``nfeRecepcaoEvento`` devolve HTTP 500 com
#: "the action ... was not recognized" — descoberto perguntando ao proprio
#: ?wsdl em 02/09/2026, em vez de adivinhar.
OPERACAO_EVENTO = 'nfeRecepcaoEventoNF'
C_ORGAO = '91'
TP_AMB = '1'                    # 1 = produção (o mesmo da captura)
VERSAO_EVENTO = '1.00'          # layout do envEvento

#: A ÚNICA manifestação que este módulo conhece. Deixar como constante, e não
#: como parâmetro, é proposital: um parâmetro convidaria alguém a passar
#: '210200' um dia, e essa é a diferença entre "sei que existe" e "confirmo a
#: compra".
TP_EVENTO_CIENCIA = '210210'
DESC_EVENTO_CIENCIA = 'Ciencia da Operacao'

_TZ = ZoneInfo('America/Sao_Paulo')


def _so_digitos(txt):
    return re.sub(r'\D', '', txt or '')


def montar_evento_ciencia(cnpj, chave, n_seq=1, quando=None):
    """XML do ``<evento>`` de Ciência da Operação, AINDA SEM ASSINATURA.

    Devolve (xml_str, id_evento). O ``Id`` segue o layout — 'ID' + tpEvento +
    chave + nSeqEvento com dois dígitos — e é ele que a assinatura referencia.

    Args:
        cnpj: CNPJ do DESTINATÁRIO (quem manifesta), só dígitos ou formatado.
        chave: chave de acesso da NF-e (44 dígitos).
        n_seq: sequência do evento. 1 na primeira vez; só muda se a SEFAZ
            recusar por duplicidade (573) e for preciso repetir.
        quando: datetime; o padrão é agora no fuso de São Paulo. A SEFAZ recusa
            evento com data futura, e recusa também quem manda sem fuso.
    """
    cnpj = _so_digitos(cnpj)
    chave = _so_digitos(chave)
    if len(chave) != 44:
        raise ValueError('chave de acesso precisa ter 44 dígitos, veio %d'
                         % len(chave))
    if len(cnpj) != 14:
        raise ValueError('CNPJ do destinatário precisa ter 14 dígitos')

    dh = (quando or datetime.now(_TZ)).replace(microsecond=0).isoformat()
    id_evento = 'ID%s%s%02d' % (TP_EVENTO_CIENCIA, chave, int(n_seq))

    xml = (
        f'<evento xmlns="{NS_NFE}" versao="{VERSAO_EVENTO}">'
        f'<infEvento Id="{id_evento}">'
        f'<cOrgao>{C_ORGAO}</cOrgao>'
        f'<tpAmb>{TP_AMB}</tpAmb>'
        f'<CNPJ>{cnpj}</CNPJ>'
        f'<chNFe>{chave}</chNFe>'
        f'<dhEvento>{dh}</dhEvento>'
        f'<tpEvento>{TP_EVENTO_CIENCIA}</tpEvento>'
        f'<nSeqEvento>{int(n_seq)}</nSeqEvento>'
        f'<verEvento>{VERSAO_EVENTO}</verEvento>'
        f'<detEvento versao="{VERSAO_EVENTO}">'
        f'<descEvento>{DESC_EVENTO_CIENCIA}</descEvento>'
        f'</detEvento>'
        f'</infEvento>'
        f'</evento>'
    )
    return xml, id_evento


def assinar_evento(xml_evento, id_evento, chave_privada, certificado):
    """Assina o ``infEvento`` em XML-DSig e devolve o ``<evento>`` assinado.

    A referência aponta para o ``Id`` do ``infEvento`` (não para o documento
    inteiro): é o que o layout da NF-e manda, e assinar o nó errado produz um
    XML que passa aqui e é recusado lá.
    """
    from signxml import XMLSigner, methods

    class _AssinadorNFe(XMLSigner):
        """XMLSigner que aceita SHA-1 — porque o layout da NF-e exige SHA-1.

        O signxml recusa SHA-1 por padrão, e com razão: é fraco. Mas aqui a
        escolha não é nossa — a SEFAZ valida a assinatura do evento contra o
        layout, que ainda especifica RSA-SHA1 e digest SHA-1. Assinar com
        SHA-256 produziria um XML mais forte e REJEITADO.

        A biblioteca deixa essa porta aberta de propósito: ``check_deprecated_methods``
        é um método justamente para poder ser sobrescrito por quem tem um
        requisito externo. É a saída documentada, não um contorno.
        """

        def check_deprecated_methods(self):
            return None

    # O signxml quer o certificado em PEM (texto), nao o objeto do
    # cryptography que o resto do projeto carrega. A conversao mora aqui para
    # quem chama continuar passando o mesmo objeto que ja usa na sessao mTLS.
    from cryptography.hazmat.primitives import serialization
    if not isinstance(certificado, (str, bytes, list, tuple)):
        certificado = certificado.public_bytes(
            serialization.Encoding.PEM).decode('ascii')

    raiz = etree.fromstring(xml_evento.encode('utf-8'))
    assinador = _AssinadorNFe(
        method=methods.enveloped,
        signature_algorithm='rsa-sha1',
        digest_algorithm='sha1',
        c14n_algorithm='http://www.w3.org/TR/2001/REC-xml-c14n-20010315',
    )
    # O layout da NF-e não usa KeyValue nem os elementos extras que o signxml
    # inclui por padrão — só o X509Certificate. Sobrando tag, a SEFAZ recusa.
    assinador.excise_empty_xmlns_declarations = True
    # ASSINATURA SEM PREFIXO: o layout pede <Signature xmlns="...xmldsig#">, e o
    # signxml gera <ds:Signature> por padrão. Com o prefixo, a SEFAZ responde
    # HTTP 500 "Object reference not set" — o parser dela procura a tag pelo
    # nome sem prefixo e não acha (02/09/2026). A própria biblioteca documenta
    # {None: ds} como a forma de pedir isso.
    from signxml.util import namespaces as _ns_signxml
    assinador.namespaces = {None: _ns_signxml.ds}
    assinado = assinador.sign(
        raiz,
        key=chave_privada,
        cert=certificado,
        reference_uri='#' + id_evento,
    )
    return etree.tostring(assinado, encoding='unicode')


def montar_soap_evento(xml_evento_assinado, id_lote=1):
    """Envelope SOAP do ``nfeRecepcaoEvento`` com UM evento no lote.

    Um por lote de propósito: lote com N eventos é N atos irreversíveis num
    clique só, e este módulo existe para o caso em que a pessoa decide nota a
    nota.
    """
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        '<soap12:Body>'
        # SEM EMBRULHO: o WSDL deste serviço declara a mensagem como
        # `<wsdl:part name="nfeDadosMsg" element="tns:nfeDadosMsg"/>` — o corpo
        # do SOAP é o nfeDadosMsg DIRETO. O serviço de distribuição usa
        # embrulho (<nfeDistDFeInteresse>), e copiar aquele formato para cá
        # rendeu HTTP 500 "Object reference not set" (02/09/2026): o parser
        # procurava nfeDadosMsg na raiz do corpo e achava outra tag.
        f'<nfeDadosMsg xmlns="{NS_WSDL_EVENTO}">'
        f'<envEvento xmlns="{NS_NFE}" versao="{VERSAO_EVENTO}">'
        f'<idLote>{int(id_lote)}</idLote>'
        f'{xml_evento_assinado}'
        '</envEvento>'
        '</nfeDadosMsg>'
        '</soap12:Body>'
        '</soap12:Envelope>'
    )


def enviar_evento(sess, soap, timeout=60):
    """POST ÚNICO ao RecepcaoEvento. Devolve o ``<retEnvEvento>``.

    UMA requisição, sem laço e sem retry: repetir um evento por conta própria é
    repetir um ato irreversível. Se a SEFAZ recusar, quem decide tentar de novo
    é a pessoa, olhando o motivo.

    Levanta RuntimeError em erro de transporte/HTTP/parse — o chamador precisa
    distinguir "não chegou" de "chegou e foi recusado", porque no primeiro caso
    o evento NÃO existe na SEFAZ e no segundo pode existir.
    """
    import xml.etree.ElementTree as ET
    from utils.integrations.dfe_sefaz import _find

    headers = {
        'Content-Type': ('application/soap+xml; charset=utf-8; '
                         f'action="{NS_WSDL_EVENTO}/{OPERACAO_EVENTO}"'),
        'User-Agent': 'qualicontax/manifesto (ciencia da operacao, 210210)',
    }
    try:
        r = sess.post(ENDPOINT_EVENTO, data=soap.encode('utf-8'),
                      headers=headers, timeout=timeout)
    except Exception as exc:
        raise RuntimeError('falha no transporte HTTPS/mTLS com a SEFAZ: %s' % exc) from exc
    if r.status_code != 200:
        raise RuntimeError('SEFAZ respondeu HTTP %s' % r.status_code)
    try:
        env = ET.fromstring(r.content)
    except Exception as exc:
        raise RuntimeError('resposta da SEFAZ não é XML válido: %s' % exc) from exc
    ret = _find(env, 'retEnvEvento')
    if ret is None:
        raise RuntimeError('resposta da SEFAZ sem <retEnvEvento>')
    return ret
