# -*- coding: utf-8 -*-
"""Atualização automática do Q-Robô — o lado da nuvem (28/10/2026).

O robô 0.4.1+ lê o campo ``atualizacao`` da resposta do GET /api/saidas/config
e, se a versão oferecida for maior que a dele, baixa, confere e se troca
sozinho (q-robo/qrobo/atualizador.py). Aqui se decide O QUE e PARA QUEM.

O QUÊ: o MESMO pacote do Portal do Instalador (pasta Q-Robo do Dropbox,
qualicontax.zip + version.txt). Um lugar só: o que se instala na mão e o que
chega sozinho nunca divergem. A versão vem do version.txt; o robô confere o
content_hash do Dropbox e roda o exe novo antes de trocar, então um
version.txt errado não estraga nada — o pacote é recusado naquele PC.

PARA QUEM: o arquivo ``atualizar.txt`` na mesma pasta.
    (ausente ou vazio)   ninguém recebe — é o padrão;
    162                  só o posto de número 162 (o NH, para testar);
    162, 23              uma lista de números de cliente;
    todos                todos os robôs.
Número de CLIENTE (o que a equipe vê na tela), não o id interno.

Sem rota e sem escrita: só lê o Dropbox. A leitura é guardada por
CACHE_SEG — o /config é chamado por cada robô a cada minuto, e são 3
chamadas ao Dropbox por leitura (lista, metadados, version.txt).
"""
import logging
import threading
import time

from utils import dropbox_sync, qrobo_instalador

logger = logging.getLogger(__name__)

NOME_LISTA = 'atualizar.txt'
CACHE_SEG = 600          # 10 min; o link temporário do Dropbox vale 4 h

_lock = threading.Lock()
_cache = {'em': 0.0, 'oferta': None, 'quem': None}


def _tupla(versao):
    try:
        return tuple(int(x) for x in (versao or '').strip().split('.'))
    except ValueError:
        return ()


def ler_lista(texto):
    """'todos' -> 'todos'; '162, 23' -> {'162', '23'}; vazio -> set()."""
    termos = {t.strip() for t in (texto or '').replace(';', ',').replace('\n', ',').split(',')}
    termos.discard('')
    if any(t.lower() == 'todos' for t in termos):
        return 'todos'
    return {t.lstrip('0') or '0' for t in termos if t.isdigit()}


def _ler_dropbox():
    """(oferta, quem) lidos agora. oferta=None quando não há o que oferecer."""
    m = qrobo_instalador.manifesto()
    svc = dropbox_sync._service
    try:
        bruto = svc.download_file(f'{qrobo_instalador.pasta()}/{NOME_LISTA}')
        quem = ler_lista(bruto.decode('utf-8', 'replace')) if bruto else set()
    except Exception:
        quem = set()
    if not quem or not m.get('ok') or not _tupla(m.get('rotulo')) or not m.get('content_hash'):
        return None, quem
    url = svc.temporary_link(m['caminho'])
    if not url:
        return None, quem
    return {'versao': m['rotulo'], 'content_hash': m['content_hash'],
            'tamanho': int(m.get('tamanho') or 0), 'url': url}, quem


def _atual():
    with _lock:
        if time.time() - _cache['em'] < CACHE_SEG:
            return _cache['oferta'], _cache['quem']
    try:
        oferta, quem = _ler_dropbox()
    except Exception:
        # Falha também fica guardada: com o Dropbox fora, sem isto cada robô
        # tentaria de novo a cada minuto (68 postos = 68 tentativas/min).
        logger.exception('[qrobo-atualizacao] não li o Dropbox — sem oferta por %ds', CACHE_SEG)
        oferta, quem = None, set()
    with _lock:
        _cache.update(em=time.time(), oferta=oferta, quem=quem)
    if oferta:
        logger.info('[qrobo-atualizacao] oferecendo %s para %s', oferta['versao'],
                    quem if quem == 'todos' else sorted(quem))
    return oferta, quem


def oferta_para(numero_cliente, versao_robo):
    """O que vai no campo ``atualizacao`` do /config deste robô, ou None.

    Robô sem versão (anterior à 0.4.0) nem entenderia o campo — não recebe.
    Nunca levanta: atualização é extra, o /config não pode cair por ela."""
    try:
        if not _tupla(versao_robo):
            return None
        oferta, quem = _atual()
        if not oferta or _tupla(oferta['versao']) <= _tupla(versao_robo):
            return None
        num = (str(numero_cliente or '').strip().lstrip('0') or '0')
        if quem != 'todos' and num not in quem:
            return None
        return dict(oferta)
    except Exception:
        logger.exception('[qrobo-atualizacao] falha ao montar a oferta — /config segue sem ela')
        return None
