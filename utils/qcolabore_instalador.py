# -*- coding: utf-8 -*-
"""Instalador do Q-Colabore servido a partir do Dropbox.

O FUNCIONÁRIO nunca abre o Dropbox: ele baixa pelo sistema. Quem põe o pacote
lá é o Anderson, em

    Dropbox/Aplicativos/QUALICONTAX/Q-Colabore/

O app só LÊ. Mesma raiz e mesmo mecanismo do instalador do Q-Robô — este
módulo REUSA os helpers de ``qrobo_instalador`` (escolha de arquivo na pasta e
leitura do manual) em vez de copiá-los. O que muda é só o específico:

VERSÃO — sai do NOME do arquivo, não de um version.txt
    O pacote do Q-Robô se chama sempre ``qualicontax.zip`` e o rótulo vem de um
    ``version.txt`` ao lado. Aqui o nome JÁ carrega a versão
    (``qcolabore-0.3.0.zip``), o que é melhor: não há um segundo arquivo para
    alguém esquecer de atualizar, e duas versões podem conviver na pasta
    enquanto se valida a nova. Havendo mais de uma, serve a MAIOR por ordem
    numérica — não a mais recente por data, porque recompilar a 0.2.0 depois de
    publicar a 0.3.0 faria a antiga ganhar.

O ``content_hash`` do Dropbox continua sendo a verdade sobre QUAL arquivo foi
baixado; o rótulo é conveniência humana.

Este módulo NÃO grava nada no Dropbox e não tem rota — quem serve é
routes/configuracoes.
"""
import logging
import re

from utils import dropbox_sync
from utils.qrobo_instalador import _escolher, HASH_CURTO

logger = logging.getLogger(__name__)

SUBPASTA = 'Q-Colabore'
PREFIXO_ZIP = 'qcolabore-'
NOME_MANUAL = 'Manual_Instalacao_QColabore.pdf'
NOME_LEIAME = 'COMO INSTALAR.txt'
SEM_VERSAO = 's/v'

_RE_VERSAO = re.compile(r'(\d+(?:\.\d+)*)')


def pasta():
    """Caminho da pasta do instalador no escopo do app."""
    return dropbox_sync._service._build_path(SUBPASTA)


def _chave_versao(nome):
    """(1, (0,3,0)) para 'qcolabore-0.3.0.zip'. Sem número → ordena por último.

    Tupla de inteiros para 0.10.0 vir DEPOIS de 0.9.0 — comparar como texto
    poria a 0.10.0 antes, e a pessoa baixaria a versão velha sem perceber.
    """
    m = _RE_VERSAO.search(nome or '')
    if not m:
        return (0, ())
    return (1, tuple(int(p) for p in m.group(1).split('.')))


def _escolhe_pacote(entradas):
    """O .zip de MAIOR versão da pasta. Devolve (arquivo, avisos)."""
    zips = [e for e in entradas
            if e.get('is_file') and (e.get('name') or '').lower().endswith('.zip')]
    if not zips:
        return None, []
    nossos = [e for e in zips
              if (e.get('name') or '').lower().startswith(PREFIXO_ZIP)]
    if not nossos:
        # Nome fora do padrão: serve mesmo assim, avisando — melhor entregar
        # algo identificado do que falhar calado porque alguém renomeou.
        escolhido, avisos = _escolher(zips, '.zip', '')
        return escolhido, (avisos or []) + [
            f'Nenhum .zip começa com "{PREFIXO_ZIP}"; servindo {escolhido["name"]}.']
    nossos.sort(key=lambda e: _chave_versao(e.get('name')))
    escolhido = nossos[-1]
    outros = [e['name'] for e in nossos if e is not escolhido]
    avisos = ([f'Há outras versões na pasta ({", ".join(outros)}); '
               f'servindo a maior, {escolhido["name"]}.'] if outros else [])
    return escolhido, avisos


def _arquivo_extra(entradas, caminho_pasta, sufixo, canonico):
    """Manual (.pdf) ou leia-me (.txt) da pasta, ou None. NUNCA derruba nada:
    pasta sem o arquivo apenas não mostra o botão dele."""
    arquivo, avisos = _escolher(entradas, sufixo, canonico)
    if not arquivo:
        return None
    return {'nome': arquivo['name'],
            'caminho': f'{caminho_pasta}/{arquivo["name"]}',
            'tamanho': arquivo.get('size') or 0,
            'avisos': avisos}


def manifesto():
    """Retrato do que o app enxerga na pasta. SOMENTE LEITURA, nunca levanta.

    Uma única listagem do Dropbox por chamada — pacote, manual e leia-me saem
    todos dela.
    """
    caminho_pasta = None
    try:
        svc = dropbox_sync._service
        if not svc.is_configured():
            return {'ok': False, 'caminho': None, 'manual': None, 'leiame': None,
                    'erro': 'Dropbox não configurado neste ambiente.',
                    'detalhe': 'DROPBOX_REFRESH_TOKEN/APP_KEY/APP_SECRET ausentes.'}

        caminho_pasta = pasta()
        entradas = svc.list_folder(caminho_pasta) or []
        manual = _arquivo_extra(entradas, caminho_pasta, '.pdf', NOME_MANUAL)
        leiame = _arquivo_extra(entradas, caminho_pasta, '.txt', NOME_LEIAME)
        arquivo, avisos = _escolhe_pacote(entradas)
        if not arquivo:
            return {'ok': False, 'caminho': caminho_pasta,
                    'manual': manual, 'leiame': leiame,
                    'erro': f'Nenhum pacote (.zip) em {caminho_pasta}.',
                    'detalhe': f'{len(entradas)} item(ns): '
                               f'{[e.get("name") for e in entradas]}'}

        caminho = f'{caminho_pasta}/{arquivo["name"]}'
        md = svc.file_metadata(caminho) or {}
        content_hash = md.get('content_hash') or ''
        hash_curto = content_hash[:HASH_CURTO] if content_hash else ''

        m = _RE_VERSAO.search(arquivo['name'])
        rotulo = m.group(1) if m else None
        if not rotulo:
            avisos.append('O nome do pacote não traz número de versão.')

        return {'ok': True, 'caminho': caminho, 'nome': arquivo['name'],
                'tamanho': md.get('size') or arquivo.get('size') or 0,
                'modificado': md.get('modified'),
                'content_hash': content_hash, 'hash_curto': hash_curto,
                'rotulo': rotulo or SEM_VERSAO,
                'nome_download': arquivo['name'],
                'avisos': avisos, 'manual': manual, 'leiame': leiame}
    except Exception as exc:
        logger.warning('[q-colabore] falha ao ler a pasta do instalador: %s', exc)
        return {'ok': False, 'caminho': caminho_pasta, 'manual': None, 'leiame': None,
                'erro': 'Não consegui ler a pasta do instalador no Dropbox.',
                'detalhe': str(exc)[:300]}


def baixar(caminho):
    """Bytes do arquivo, ou None. O caminho vem do MANIFESTO — nunca do usuário.

    Isso é a trava: quem escolhe o que pode ser baixado é o manifesto, montado
    da listagem da pasta. Aceitar caminho da query string deixaria a rota ler
    qualquer arquivo do Dropbox da empresa.
    """
    try:
        return dropbox_sync._service.download_file(caminho)
    except Exception as exc:
        logger.warning('[q-colabore] falha ao baixar %s: %s', caminho, exc)
        return None
