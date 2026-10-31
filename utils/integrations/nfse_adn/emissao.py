# -*- coding: utf-8 -*-
"""Emissão pelo Sefin Nacional — POST da DPS e leitura da resposta.

Swagger oficial (baixado com certificado em 31/10/2026 de
``/SefinNacional/swagger/docs/v1``):

  POST /nfse                 {"dpsXmlGZipB64": ...}  -> 201 NFSePostResponseSucesso
                             {idDps, chaveAcesso, nfseXmlGZipB64, alertas[]}
                             400/403/500 NFSePostResponseErro {idDPS, erros[{codigo, descricao, complemento}]}
  GET  /dps/{id}             chave de acesso da NFS-e gerada por este Id (idempotência)
  GET  /nfse/{chaveAcesso}   a NFS-e (nfseXmlGZipB64)

A sessão é a mesma ``SessaoADN`` da captura (certificado em memória, uma
conexão por chamada). O que muda é o host: a captura fala com o ADN, a emissão
com o Sefin.

Rejeição de negócio NÃO levanta exceção: volta em ``Resultado.ok=False`` com os
códigos, porque é dado a gravar em ``nfse_emitidas``. Só rede/TLS levantam.
"""
import base64
import gzip
import json
import logging
from dataclasses import dataclass, field

from .client import SessaoADN, ADNError

logger = logging.getLogger(__name__)

SEFIN = {
    1: 'https://sefin.nfse.gov.br/SefinNacional',
    2: 'https://sefin.producaorestrita.nfse.gov.br/SefinNacional',
}
TIMEOUT = 90


@dataclass
class Resultado:
    ok: bool
    http: int
    id_dps: str = ''
    chave_acesso: str = ''
    xml_nfse: str = ''
    erros: list = field(default_factory=list)      # [(codigo, descricao, complemento)]
    alertas: list = field(default_factory=list)
    bruto: dict = field(default_factory=dict)

    def __str__(self):
        if self.ok:
            return f'autorizada: chave {self.chave_acesso}' + (f' | alertas: {self.alertas}' if self.alertas else '')
        return f'HTTP {self.http}; ' + ' | '.join(f'{c}: {d} {x or ""}'.strip() for c, d, x in self.erros)


def empacotar(xml: str) -> str:
    return base64.b64encode(gzip.compress(xml.encode('utf-8'))).decode('ascii')


def desempacotar(b64: str) -> str:
    return gzip.decompress(base64.b64decode(b64)).decode('utf-8') if b64 else ''


def _msgs(lista):
    out = []
    for m in lista or []:
        if isinstance(m, dict):
            out.append((m.get('codigo') or m.get('Codigo') or '', m.get('descricao') or m.get('Descricao') or '',
                        m.get('complemento') or m.get('Complemento') or ''))
        else:
            out.append(('', str(m), ''))
    return out


def _abrir(resp) -> Resultado:
    try:
        j = resp.json() if resp.content else {}
    except ValueError:
        j = {'_texto': resp.text[:1000]}
    if resp.status_code == 201:
        return Resultado(ok=True, http=201, id_dps=j.get('idDps') or j.get('idDPS') or '',
                         chave_acesso=j.get('chaveAcesso') or '', xml_nfse=desempacotar(j.get('nfseXmlGZipB64')),
                         alertas=_msgs(j.get('alertas')), bruto=j)
    erros = _msgs(j.get('erros') or ([j.get('erro')] if j.get('erro') else []))
    if not erros:
        erros = [('', j.get('_texto') or j.get('message') or resp.reason or '', '')]
    return Resultado(ok=False, http=resp.status_code, id_dps=j.get('idDPS') or j.get('idDps') or '',
                     erros=erros, bruto=j)


def emitir(sessao: SessaoADN, xml_dps_assinado: str, tp_amb: int) -> Resultado:
    """POST /nfse. ``tp_amb`` escolhe o host (tem que bater com o tpAmb da DPS)."""
    url = SEFIN[int(tp_amb)] + '/nfse'
    try:
        resp = sessao.post(url, data=json.dumps({'dpsXmlGZipB64': empacotar(xml_dps_assinado)}),
                           headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
                           timeout=TIMEOUT)
    except Exception as exc:
        raise ADNError(f'Sefin POST /nfse: {exc.__class__.__name__}: {exc}') from exc
    return _abrir(resp)


def consultar_por_id_dps(sessao: SessaoADN, id_dps: str, tp_amb: int) -> Resultado:
    """GET /dps/{id}: se a DPS já virou NFS-e (reenvio sem duplicar)."""
    url = f'{SEFIN[int(tp_amb)]}/dps/{id_dps}'
    try:
        resp = sessao.get(url, headers={'Accept': 'application/json'}, timeout=TIMEOUT)
    except Exception as exc:
        raise ADNError(f'Sefin GET /dps: {exc.__class__.__name__}: {exc}') from exc
    if resp.status_code == 200:
        j = resp.json()
        return Resultado(ok=True, http=200, id_dps=j.get('idDps', ''), chave_acesso=j.get('chaveAcesso', ''), bruto=j)
    return _abrir(resp)


def consultar_nfse(sessao: SessaoADN, chave_acesso: str, tp_amb: int) -> Resultado:
    """GET /nfse/{chave}: o XML da NFS-e."""
    url = f'{SEFIN[int(tp_amb)]}/nfse/{chave_acesso}'
    try:
        resp = sessao.get(url, headers={'Accept': 'application/json'}, timeout=TIMEOUT)
    except Exception as exc:
        raise ADNError(f'Sefin GET /nfse: {exc.__class__.__name__}: {exc}') from exc
    if resp.status_code == 200:
        j = resp.json()
        return Resultado(ok=True, http=200, chave_acesso=j.get('chaveAcesso', ''),
                         xml_nfse=desempacotar(j.get('nfseXmlGZipB64')), bruto=j)
    return _abrir(resp)
