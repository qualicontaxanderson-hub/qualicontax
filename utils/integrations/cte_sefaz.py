# -*- coding: utf-8 -*-
"""
Núcleo de acesso ao CTeDistribuicaoDFe (distDFeInt do CT-e) da SEFAZ.

SÓ CONSULTA E LÊ. Não existe manifestação no CT-e — a distribuição já entrega o
documento COMPLETO para todos os atores (emitente, remetente, expedidor,
recebedor, destinatário e tomador). Este módulo não monta nem envia nenhum evento.

Por que é um módulo separado do ``dfe_sefaz`` (NF-e)
---------------------------------------------------
O ``NFeDistribuicaoDFe`` distribui SÓ documentos de NF-e. CT-e tem webservice
próprio (NT 2015.002 do CT-e), com endpoint, namespace, sequência de NSU e cota
(656) INDEPENDENTES. Como o motor de NF-e está em produção drenando backlog, ele
não é parametrizado: aqui só muda o envelope SOAP e o endpoint. Todo o resto —
mTLS em memória, conversão UF→cUFAutor, helpers de parsing — é IMPORTADO do
``dfe_sefaz``, então não há duplicação da parte crítica.

Diferenças de leiaute em relação à NF-e:
  * modos de consulta: ``distNSU`` (fila por NSU) e ``consNSU`` (um NSU pontual
    que ficou faltando). NÃO existe consulta por chave (``consChCTe``) — logo não
    existe o equivalente do ``consChNFe``: como o CT-e vem completo de primeira,
    o papel de "recuperar o que faltou" fica com o ``consNSU``.
  * docZip traz ``procCTe`` (CT-e completo) e ``procEventoCTe``. NÃO existe
    documento de RESUMO (não há regime de manifestação), então a captura nunca
    grava linha incompleta.
"""
import os
import xml.etree.ElementTree as ET

# Reuso EXPLÍCITO da fundação já provada em produção (mTLS em memória, cUFAutor,
# tag do interessado e helpers de parsing). Nada disso é reescrito aqui.
from utils.integrations.dfe_sefaz import (          # noqa: F401 (reexport)
    TP_AMB, UF_PARA_IBGE, UfInvalidaError, cuf_autor, tag_interessado,
    montar_sessao_mtls, _find, _text, _local, _so_digitos,
)

# --------------------------------------------------------------------------
# Parâmetros fixos (produção). Endpoint sobrescrevível por env para o caso de
# contingência/SVRS sem precisar de deploy de código.
# --------------------------------------------------------------------------
ENDPOINT = os.getenv(
    'CTE_DIST_ENDPOINT',
    'https://www1.cte.fazenda.gov.br/CTeDistribuicaoDFe/CTeDistribuicaoDFe.asmx',
)
NS_WSDL = 'http://www.portalfiscal.inf.br/cte/wsdl/CTeDistribuicaoDFe'
NS_CTE = 'http://www.portalfiscal.inf.br/cte'
VERSAO = '1.00'   # layout do distDFeInt do CT-e
TIMEOUT = 60

_ACTION = f'{NS_WSDL}/cteDistDFeInteresse'


def _envelope(documento, cuf, consulta_xml):
    """Envelope SOAP 1.2 do cteDistDFeInteresse. ``consulta_xml`` é o <distNSU>
    ou o <consNSU> já montado (choice do schema)."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap12:Envelope xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">'
        '<soap12:Body>'
        f'<cteDistDFeInteresse xmlns="{NS_WSDL}">'
        '<cteDadosMsg>'
        f'<distDFeInt xmlns="{NS_CTE}" versao="{VERSAO}">'
        f'<tpAmb>{TP_AMB}</tpAmb>'
        f'<cUFAutor>{cuf}</cUFAutor>'
        f'{tag_interessado(documento)}'
        f'{consulta_xml}'
        '</distDFeInt>'
        '</cteDadosMsg>'
        '</cteDistDFeInteresse>'
        '</soap12:Body>'
        '</soap12:Envelope>'
    )


def montar_soap(documento, cuf, ult_nsu):
    """SOAP no modo distNSU (fila por NSU). ult_nsu zero-pad 15."""
    ult_nsu_fmt = str(int(ult_nsu)).zfill(15)
    return _envelope(documento, cuf,
                     f'<distNSU><ultNSU>{ult_nsu_fmt}</ultNSU></distNSU>'), ult_nsu_fmt


def montar_soap_nsu(documento, cuf, nsu):
    """SOAP no modo consNSU: busca UM NSU específico que ficou faltando.

    É o análogo do ``consChNFe`` da NF-e no papel de recuperação pontual — o CT-e
    não permite consulta por chave, mas permite pedir o NSU que faltou.
    """
    nsu_fmt = str(int(nsu)).zfill(15)
    return _envelope(documento, cuf, f'<consNSU><NSU>{nsu_fmt}</NSU></consNSU>')


def _post_dist(sess, soap):
    """POST único ao CTeDistribuicaoDFe e parse do <retDistDFeInt>.
    Levanta RuntimeError em erro de transporte/HTTP/parse."""
    headers = {
        'Content-Type': f'application/soap+xml; charset=utf-8; action="{_ACTION}"',
        'User-Agent': 'qualicontax/cte-captura (leitura, nunca manifesta)',
    }
    try:
        r = sess.post(ENDPOINT, data=soap.encode('utf-8'), headers=headers,
                      timeout=TIMEOUT)
    except Exception as exc:
        raise RuntimeError(f'falha no transporte HTTPS/mTLS com a SEFAZ (CT-e): {exc}') from exc
    if r.status_code != 200:
        raise RuntimeError(f'SEFAZ (CT-e) respondeu HTTP {r.status_code}')
    try:
        env = ET.fromstring(r.content)
    except Exception as exc:
        raise RuntimeError(f'resposta da SEFAZ (CT-e) não é XML válido: {exc}') from exc
    ret = _find(env, 'retDistDFeInt')
    if ret is None:
        raise RuntimeError('resposta da SEFAZ (CT-e) sem <retDistDFeInt>')
    return ret


def consultar(sess, documento, cuf, ult_nsu):
    """UMA requisição no modo distNSU. Devolve (<retDistDFeInt>, ult_nsu_fmt).
    NUNCA faz loop (a drenagem é decidida pelo chamador)."""
    soap, ult_nsu_fmt = montar_soap(documento, cuf, ult_nsu)
    return _post_dist(sess, soap), ult_nsu_fmt


def consultar_nsu(sess, documento, cuf, nsu):
    """UMA requisição no modo consNSU (recuperação pontual de um NSU)."""
    return _post_dist(sess, montar_soap_nsu(documento, cuf, nsu))
