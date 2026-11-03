# -*- coding: utf-8 -*-
"""Eventos da NFS-e nacional pelo Sefin — hoje só o CANCELAMENTO (e101101).

Swagger oficial (``sefin_SefinNacional_swagger_docs_v1.json``, 31/10/2026):

  POST /nfse/{chaveAcesso}/eventos        {"pedidoRegistroEventoXmlGZipB64": ...}
                                          -> 201 {tipoAmbiente, versaoAplicativo, dataHoraProcessamento, eventoXmlGZipB64}
                                          400/401 ResponseErro {erros[{codigo, descricao, complemento}]}
  GET  /nfse/{chaveAcesso}/eventos/{tipoEvento}/{numSeqEvento}
                                          -> 200 {..., eventos[{chaveAcesso, tipoEvento, numeroPedidoRegistroEvento,
                                                              dataHoraRecebimento, arquivoXml}]}

Leiaute (XSD oficial v1.01, pacote NFSe-ESQUEMAS_XSD-v1.01-20260209, lido em 02/11/2026) e um evento
real de cancelamento lido do Sefin na mesma data:

  <pedRegEvento versao="1.01" xmlns="http://www.sped.fazenda.gov.br/nfse">
    <infPedReg Id="PRE" + chave(50) + "101101">          -- TSIdPedRegEvt: PRE[0-9]{56}
      <tpAmb>1|2</tpAmb> <verAplic>..</verAplic> <dhEvento>AAAA-MM-DDThh:mm:ss-03:00</dhEvento>
      <CNPJAutor>..</CNPJAutor> <chNFSe>..</chNFSe>
      <e101101><xDesc>Cancelamento de NFS-e</xDesc><cMotivo>1|2|9</cMotivo><xMotivo>15..255</xMotivo></e101101>
    </infPedReg>
    <Signature/>                                          -- referência #Id do infPedReg (como a DPS)
  </pedRegEvento>

O que o contribuinte manda é o PEDIDO (pedRegEvento); o ``evento`` que volta é o registro do Sefin com
o pedido dentro. ``nPedRegEvento`` NÃO existe no XSD 1.01 (o Id termina no tipo do evento).

Rejeição de negócio NÃO levanta exceção: volta em ``Resultado.ok=False`` com os códigos, para gravar.
Só rede/TLS levantam ``ADNError``.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .client import SessaoADN, ADNError
from .emissao import SEFIN, TIMEOUT, empacotar, desempacotar, _msgs

logger = logging.getLogger(__name__)

NS = 'http://www.sped.fazenda.gov.br/nfse'
VERSAO = '1.01'
VER_APLIC = 'Qualicontax 1.0'
FUSO = timezone(timedelta(hours=-3))

CANCELAMENTO = '101101'
XDESC_CANCELAMENTO = 'Cancelamento de NFS-e'          # enumeração fechada no XSD
MOTIVOS_CANCELAMENTO = {1: 'Erro na emissão', 2: 'Serviço não prestado', 9: 'Outros'}


@dataclass
class Resultado:
    ok: bool
    http: int
    xml_evento: str = ''
    id_evento: str = ''
    dh_proc: str = ''
    erros: list = field(default_factory=list)      # [(codigo, descricao, complemento)]
    bruto: dict = field(default_factory=dict)

    def __str__(self):
        if self.ok:
            return f'registrado: {self.id_evento or "evento"} em {self.dh_proc}'
        return f'HTTP {self.http}; ' + ' | '.join(f'{c}: {d} {x or ""}'.strip() for c, d, x in self.erros)


def _esc(v):
    return (str(v or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def id_pedido(chave: str, tipo: str = CANCELAMENTO) -> str:
    return f'PRE{chave}{tipo}'


def validar_motivo(c_motivo, x_motivo) -> str:
    """Devolve o xMotivo limpo ou levanta ValueError com a frase para a tela."""
    try:
        c = int(c_motivo)
    except (TypeError, ValueError):
        c = 0
    if c not in MOTIVOS_CANCELAMENTO:
        raise ValueError('Escolha o motivo: erro na emissão, serviço não prestado ou outros.')
    x = ' '.join(str(x_motivo or '').split())
    if len(x) < 15:
        raise ValueError('Explique o motivo com pelo menos 15 caracteres (o governo exige).')
    return x[:255]


def xml_pedido_cancelamento(chave: str, cnpj_autor: str, tp_amb: int, c_motivo: int, x_motivo: str,
                            quando: datetime = None) -> tuple[str, str]:
    """(xml sem assinatura, Id do infPedReg). A ordem dos elementos é a do XSD."""
    quando = quando or datetime.now(FUSO).replace(microsecond=0)
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=FUSO)
    pid = id_pedido(chave, CANCELAMENTO)
    xml = (
        f'<pedRegEvento versao="{VERSAO}" xmlns="{NS}">'
        f'<infPedReg Id="{pid}">'
        f'<tpAmb>{int(tp_amb)}</tpAmb>'
        f'<verAplic>{_esc(VER_APLIC)}</verAplic>'
        f'<dhEvento>{quando.isoformat()}</dhEvento>'
        f'<CNPJAutor>{cnpj_autor}</CNPJAutor>'
        f'<chNFSe>{chave}</chNFSe>'
        f'<e101101>'
        f'<xDesc>{XDESC_CANCELAMENTO}</xDesc>'
        f'<cMotivo>{int(c_motivo)}</cMotivo>'
        f'<xMotivo>{_esc(x_motivo)}</xMotivo>'
        f'</e101101>'
        f'</infPedReg>'
        f'</pedRegEvento>'
    )
    return xml, pid


def assinar_pedido(xml: str, id_ped: str, chave_priv, x509) -> str:
    """Assinatura dentro de ``<pedRegEvento>``, referência ``#Id`` do infPedReg — a mesma rotina da DPS."""
    from utils.integrations.dfe_manifesto import assinar_evento
    return '<?xml version="1.0" encoding="UTF-8"?>' + assinar_evento(xml, id_ped, chave_priv, x509)


def _abrir(resp) -> Resultado:
    try:
        j = resp.json() if resp.content else {}
    except ValueError:
        j = {'_texto': resp.text[:1000]}
    if resp.status_code in (200, 201) and j.get('eventoXmlGZipB64'):
        xml = desempacotar(j.get('eventoXmlGZipB64'))
        return Resultado(ok=True, http=resp.status_code, xml_evento=xml, id_evento=_id_do_evento(xml),
                         dh_proc=str(j.get('dataHoraProcessamento') or ''), bruto=j)
    erros = _msgs(j.get('erros') or ([j.get('erro')] if j.get('erro') else []))
    if not erros:
        erros = [('', j.get('_texto') or j.get('message') or resp.reason or f'HTTP {resp.status_code}', '')]
    return Resultado(ok=False, http=resp.status_code, erros=erros, bruto=j)


def _id_do_evento(xml_evento: str) -> str:
    try:
        from lxml import etree
        raiz = etree.fromstring(xml_evento.encode('utf-8'))
        inf = raiz.find(f'{{{NS}}}infEvento')
        return (inf.get('Id') if inf is not None else '') or ''
    except Exception:
        return ''


def registrar(sessao: SessaoADN, chave: str, xml_pedido_assinado: str, tp_amb: int) -> Resultado:
    """POST /nfse/{chave}/eventos. ``tp_amb`` escolhe o host (tem que bater com o tpAmb do pedido)."""
    url = f'{SEFIN[int(tp_amb)]}/nfse/{chave}/eventos'
    try:
        resp = sessao.post(url, data=json.dumps({'pedidoRegistroEventoXmlGZipB64': empacotar(xml_pedido_assinado)}),
                           headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                           timeout=TIMEOUT)
    except Exception as exc:
        raise ADNError(f'Sefin POST /nfse/{{chave}}/eventos: {exc.__class__.__name__}: {exc}') from exc
    return _abrir(resp)


def consultar(sessao: SessaoADN, chave: str, tp_amb: int, tipo: str = CANCELAMENTO, n_seq: int = 1) -> Resultado:
    """GET /nfse/{chave}/eventos/{tipo}/{nSeq}: o evento já registrado (para conferir depois de um timeout)."""
    url = f'{SEFIN[int(tp_amb)]}/nfse/{chave}/eventos/{int(tipo)}/{int(n_seq)}'
    try:
        resp = sessao.get(url, headers={'Accept': 'application/json'}, timeout=TIMEOUT)
    except Exception as exc:
        raise ADNError(f'Sefin GET eventos: {exc.__class__.__name__}: {exc}') from exc
    try:
        j = resp.json() if resp.content else {}
    except ValueError:
        j = {'_texto': resp.text[:1000]}
    evs = j.get('eventos') or []
    if resp.status_code == 200 and evs:
        b = evs[0].get('arquivoXml') or ''
        # vem base64(base64(gzip)) — lido do Sefin em 02/11/2026; aceita também o simples
        import base64, gzip
        raw = base64.b64decode(b)
        if raw[:2] != b'\x1f\x8b':
            raw = base64.b64decode(raw)
        xml = gzip.decompress(raw).decode('utf-8')
        return Resultado(ok=True, http=200, xml_evento=xml, id_evento=_id_do_evento(xml),
                         dh_proc=str(evs[0].get('dataHoraRecebimento') or ''), bruto=j)
    erros = _msgs(j.get('erros') or [])
    if not erros:
        erros = [('', j.get('_texto') or j.get('message') or resp.reason or f'HTTP {resp.status_code}', '')]
    return Resultado(ok=False, http=resp.status_code, erros=erros, bruto=j)
