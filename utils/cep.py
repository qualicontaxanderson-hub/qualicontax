# -*- coding: utf-8 -*-
"""Endereço pelo CEP, com fonte reserva (19/10/2026).

Chamado do servidor do Railway, o ViaCEP falhava calado e as telas diziam "CEP
não encontrado" para CEP que existe (75602-272, Goiatuba). Agora são três
fontes em sequência — ViaCEP, BrasilAPI, OpenCEP —; se uma cair, a próxima
responde. Devolve sempre o formato do ViaCEP (logradouro, bairro, localidade,
uf), que é o que as telas já leem. CEP de cidade inteira (75600-000) vem só
com cidade e UF, e isso é o certo.
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)


class CepIndisponivel(Exception):
    """Nenhuma fonte respondeu (todas fora do ar) — diferente de "não existe"."""


def _viacep(j):
    return None if j.get('erro') else j


def _brasilapi(j):
    ibge = j.get('ibge')
    return {'cep': j.get('cep'), 'logradouro': j.get('street') or '', 'bairro': j.get('neighborhood') or '',
            'localidade': j.get('city') or '', 'uf': j.get('state') or '',
            'ibge': ibge.get('city', '') if isinstance(ibge, dict) else ''}


_FONTES = (('viacep', 'https://viacep.com.br/ws/{}/json/', _viacep),
           ('brasilapi', 'https://brasilapi.com.br/api/cep/v1/{}', _brasilapi),
           ('opencep', 'https://opencep.com/v1/{}', _viacep))


def consultar(cep):
    """dict no formato ViaCEP (+ 'fonte'), None se nenhuma fonte conhece o CEP,
    ou CepIndisponivel se todas falharam."""
    d = re.sub(r'\D', '', cep or '')
    if len(d) != 8:
        return None
    falhas = 0
    for nome, url, ler in _FONTES:
        try:
            r = requests.get(url.format(d), timeout=4, headers={'User-Agent': 'Qualicontax/1.0'})
            if r.status_code in (400, 404):
                continue                      # esta fonte não conhece o CEP: tenta a próxima
            r.raise_for_status()
            dados = ler(r.json())
            if dados and (dados.get('localidade') or dados.get('logradouro')):
                return dict(dados, fonte=nome)
        except (requests.RequestException, ValueError) as e:
            falhas += 1
            logger.warning('[cep] %s falhou para %s: %s', nome, d, e)
    if falhas == len(_FONTES):
        raise CepIndisponivel(d)
    return None
