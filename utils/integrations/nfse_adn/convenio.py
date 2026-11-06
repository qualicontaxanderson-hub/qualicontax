# -*- coding: utf-8 -*-
"""O município aceita o Emissor Nacional? (06/11/2026)

O Emissor Nacional (Sefin) só emite para prestador de município que ADERIU a ele.
Quem não aderiu recusa a DPS com E0037 (sem convênio) ou E0039 (não parametrizado
para os emissores nacionais) — provado com a Roma (Senador Canedo) e a NH BR 153
(Morrinhos, só no ambiente de teste). A regra é pública no ADN:

    GET https://adn.nfse.gov.br/parametrizacao/{IBGE}/convenio      (mTLS)
    -> parametrosConvenio.aderenteEmissorNacional  (1 sim, 0 não)

Produção e produção restrita têm parametrizações DIFERENTES (Morrinhos: prod 1, teste 0).
Medido em 06/11/2026: 312 dos 486 clientes ativos em município que aceita, 171 não.

Cache em memória por 12 h (a parametrização muda raramente; cada consulta abre mTLS).
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

ADN = {1: 'https://adn.nfse.gov.br', 2: 'https://adn.producaorestrita.nfse.gov.br'}
_TTL = 12 * 3600
_cache = {}          # (ibge, ambiente) -> (quando, resultado)
_sessao = {'s': None, 'quando': 0}


def _abrir():
    """Qualquer certificado válido consulta a parametrização; usa o do escritório."""
    from utils.integrations.nfse_adn import client as adn
    if not _sessao['s'] or time.time() - _sessao['quando'] > 3600:
        cid = int(os.getenv('NFSE_CERT_CONSULTA_CLIENTE', '2'))
        _sessao['s'] = adn.abrir_sessao(adn.resolver_certificado(cid))
        _sessao['quando'] = time.time()
    return _sessao['s']


def aceita_emissor_nacional(ibge, ambiente=1):
    """'sim' | 'nao' | 'sem_convenio' | None (não deu para consultar — quem chama só avisa)."""
    ibge = ''.join(ch for ch in str(ibge or '') if ch.isdigit())
    if len(ibge) != 7:
        return None
    chave = (ibge, int(ambiente))
    hit = _cache.get(chave)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    try:
        r = _abrir().get(f'{ADN[int(ambiente)]}/parametrizacao/{ibge}/convenio', timeout=20)
        j = (r.json() or {}) if 'json' in (r.headers.get('content-type') or '') else {}
        p = j.get('parametrosConvenio')
        if r.status_code == 404 or (r.status_code == 200 and not p):
            res = 'sem_convenio'
        elif r.status_code == 200:
            res = 'sim' if p.get('aderenteEmissorNacional') == 1 else 'nao'
        else:
            logger.warning('[nfse-convenio] %s respondeu HTTP %s', ibge, r.status_code)
            return None
    except Exception as exc:                          # noqa: BLE001 — rede/certificado: não trava ninguém
        logger.warning('[nfse-convenio] não consultei %s: %s', ibge, exc.__class__.__name__)
        return None
    _cache[chave] = (time.time(), res)
    return res
