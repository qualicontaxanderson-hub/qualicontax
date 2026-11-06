# -*- coding: utf-8 -*-
"""Transporte da DPS nacional ao ISSWeb da Fiorilli (recepcionarDps / cancelarNFSe).

Devolve os MESMOS tipos de resultado do Sefin (``nfse_adn.emissao.Resultado`` e
``nfse_adn.eventos.Resultado``) para o motor (``utils/nfse_emissor.py``) tratar os dois
caminhos com o mesmo código. Nada de credencial em log.
"""
import logging
import re

import requests
from lxml import etree

from utils.integrations.nfse_adn.emissao import Resultado
from utils.integrations.nfse_adn.eventos import Resultado as ResultadoEvento

logger = logging.getLogger(__name__)

NS_FIO = 'http://www.fiorilli.com.br/nfse-nacional'
NS_NAC = 'http://www.sped.fazenda.gov.br/nfse'
HOMOLOGACAO = 'http://fi1.fiorilli.com.br:5663/IssWeb-ejb/IssWebWSNacional/IssWebWSNacionalPortType'
TIMEOUT = (15, 90)


class FiorilliErro(Exception):
    pass


def _sem_declaracao(xml):
    return re.sub(r'^\s*<\?xml[^>]*\?>', '', xml or '').strip()


def _post(url, acao, corpo):
    env = ('<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope '
           'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
           f'xmlns:nfn="{NS_FIO}"><soapenv:Header/><soapenv:Body>{corpo}</soapenv:Body></soapenv:Envelope>')
    try:
        r = requests.post(url, data=env.encode('utf-8'), timeout=TIMEOUT,
                          headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': acao})
    except requests.RequestException as exc:
        raise FiorilliErro(f'ISSWeb não respondeu: {exc.__class__.__name__}') from exc
    try:
        raiz = etree.fromstring(r.content)
    except etree.XMLSyntaxError as exc:
        raise FiorilliErro(f'Resposta do ISSWeb ilegível (HTTP {r.status_code})') from exc
    falha = raiz.find('.//{http://schemas.xmlsoap.org/soap/envelope/}Fault')
    if falha is not None:
        return r.status_code, raiz, [('SOAP', (falha.findtext('faultstring') or 'Fault').strip()[:500], '')]
    return r.status_code, raiz, None


def _mensagens(raiz):
    out = []
    for m in raiz.iter(f'{{{NS_FIO}}}mensagem'):
        out.append(((m.findtext(f'{{{NS_FIO}}}Codigo') or '').strip(),
                    (m.findtext(f'{{{NS_FIO}}}Mensagem') or '').strip(),
                    (m.findtext(f'{{{NS_FIO}}}Correcao') or '').strip()))
    return out


def enviar_dps(url, xml_dps_assinado) -> Resultado:
    """recepcionarDps: a DPS (assinada) vai como está; a NFS-e volta no layout nacional."""
    http, raiz, fault = _post(url, 'recepcionarDPS',
                              f'<nfn:RecepcionarDpsEnvio>{_sem_declaracao(xml_dps_assinado)}</nfn:RecepcionarDpsEnvio>')
    bruto = {'fiorilli': etree.tostring(raiz, encoding='unicode')[:20000]}
    if fault:
        return Resultado(ok=False, http=http, erros=fault, bruto=bruto)
    nfse = raiz.find(f'.//{{{NS_NAC}}}NFSe')
    msgs = _mensagens(raiz)
    if nfse is None:
        return Resultado(ok=False, http=http, erros=msgs or [('', 'ISSWeb não devolveu a NFS-e', '')], bruto=bruto)
    inf = nfse.find(f'{{{NS_NAC}}}infNFSe')
    chave = re.sub(r'\D', '', (inf.get('Id') if inf is not None else '') or '')
    return Resultado(ok=True, http=http, chave_acesso=chave,
                     xml_nfse='<?xml version="1.0" encoding="UTF-8"?>' + etree.tostring(nfse, encoding='unicode'),
                     alertas=msgs, bruto=bruto)


def cancelar(url, inscricao_municipal, xml_pedido_assinado, id_pedido='') -> ResultadoEvento:
    """cancelarNFSe: IM + o pedRegEvento nacional (e101101) assinado."""
    from xml.sax.saxutils import escape
    corpo = (f'<nfn:CancelarNFSeEnvio><nfn:IM>{escape(re.sub(r"[^0-9A-Za-z]", "", inscricao_municipal or ""))}</nfn:IM>'
             f'{_sem_declaracao(xml_pedido_assinado)}</nfn:CancelarNFSeEnvio>')
    http, raiz, fault = _post(url, 'cancelarNFSe', corpo)
    bruto = {'fiorilli': etree.tostring(raiz, encoding='unicode')[:20000]}
    if fault:
        return ResultadoEvento(ok=False, http=http, erros=fault, bruto=bruto)
    status = (raiz.findtext(f'.//{{{NS_FIO}}}status') or '').strip().upper()
    quando = (raiz.findtext(f'.//{{{NS_FIO}}}DataRecebimento') or '').strip()
    if status == 'CANCELADA':
        return ResultadoEvento(ok=True, http=http, xml_evento=bruto['fiorilli'], id_evento=id_pedido,
                               dh_proc=quando, bruto=bruto)
    return ResultadoEvento(ok=False, http=http, erros=_mensagens(raiz) or [('', f'status {status or "vazio"}', '')],
                           bruto=bruto)
