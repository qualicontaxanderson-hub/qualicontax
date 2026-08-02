# -*- coding: utf-8 -*-
"""Instalador do Q-Robô servido a partir da Dropbox (Fase 4).

O Anderson coloca o .exe VALIDADO em:

    C:\\Users\\...\\Dropbox\\Aplicativos\\QUALICONTAX\\Q-Robo\\qrobo.exe

O app só LÊ. Nada de Actions publicando, nada de segredo novo: a raiz da app no
Dropbox JÁ é ``/Aplicativos/QUALICONTAX`` (é lá que ``Certificados/`` e
``EMPRESAS/`` são gravados hoje), então o caminho sai de ``_build_path('Q-Robo')``
— que funciona igual em token com escopo de App Folder ou Full Dropbox.

VERSÃO — duas fontes, e a que importa não depende de ninguém lembrar:

  content_hash (Dropbox)  impressão digital determinística do binário. É a
                          verdade sobre QUAL arquivo foi baixado.
  version.txt (opcional)  rótulo humano ("3.4") que o Anderson escreve ao lado
                          do .exe quando valida. Some ou fica velho? A
                          impressão digital continua identificando.

Na auditoria grava ``3.4+a1b2c3d4`` (ou ``s/v+a1b2c3d4`` sem rótulo), que cabe
no VARCHAR(20) de qrobo_auditoria.

Este módulo NÃO grava nada no Dropbox e não tem rota — quem serve é routes/qrobo.
"""
import logging
import re

from utils import dropbox_sync

logger = logging.getLogger(__name__)

SUBPASTA = 'Q-Robo'
NOME_PADRAO = 'qrobo.exe'
NOME_VERSAO = 'version.txt'
HASH_CURTO = 8
VERSAO_MAX = 20            # tamanho da coluna instalador_versao
SEM_VERSAO = 's/v'


def pasta():
    """Caminho da pasta do instalador no escopo do app."""
    return dropbox_sync._service._build_path(SUBPASTA)


def _rotulo_version_txt():
    """Conteúdo do version.txt (1ª linha, enxuta), ou None. Nunca levanta."""
    try:
        bruto = dropbox_sync._service.download_file(f'{pasta()}/{NOME_VERSAO}')
        if not bruto:
            return None
        linha = bruto.decode('utf-8', 'replace').strip().splitlines()[0].strip()
        # Só o PRIMEIRO termo: "3.4 (validada em 02/08)" -> "3.4". Colar tudo
        # daria rótulos como "3.4validadaem" na tela e na auditoria.
        termo = linha.split()[0] if linha.split() else ''
        # E só o que serve de rótulo: dígitos, letras, ponto, hífen e underscore.
        return re.sub(r'[^0-9A-Za-z.\-_]', '', termo)[:10] or None
    except Exception as exc:
        logger.info('[qrobo] sem %s legível (%s) — seguindo só com o content_hash.',
                    NOME_VERSAO, exc)
        return None


def _escolhe_exe(entradas):
    """qrobo.exe é o nome canônico. Se não estiver lá, pega o .exe mais recente
    e avisa — melhor servir algo identificado do que falhar calado."""
    exes = [e for e in entradas if e.get('is_file')
            and e.get('name', '').lower().endswith('.exe')]
    if not exes:
        return None, []
    canonico = next((e for e in exes if e['name'].lower() == NOME_PADRAO), None)
    if canonico:
        extras = [e['name'] for e in exes if e is not canonico]
        avisos = ([f'Há outros .exe na pasta ({", ".join(extras)}); '
                   f'servindo o {NOME_PADRAO}.'] if extras else [])
        return canonico, avisos
    recente = sorted(exes, key=lambda e: e.get('modified') or '')[-1]
    return recente, [f'Não achei {NOME_PADRAO}; servindo o .exe mais recente '
                     f'({recente["name"]}).']


def manifesto():
    """Retrato do que o app enxerga na pasta. SOMENTE LEITURA, nunca levanta.

    ok=True  -> {'nome','caminho','tamanho','modificado','content_hash',
                 'hash_curto','rotulo','versao','nome_download','avisos'}
    ok=False -> {'erro': <texto para a tela>, 'detalhe': <técnico>, 'caminho'}
    """
    caminho_pasta = None
    try:
        svc = dropbox_sync._service
        if not svc.is_configured():
            return {'ok': False, 'caminho': None,
                    'erro': 'Dropbox não configurado neste ambiente.',
                    'detalhe': 'DROPBOX_REFRESH_TOKEN/APP_KEY/APP_SECRET ausentes.'}

        caminho_pasta = pasta()
        entradas = svc.list_folder(caminho_pasta) or []
        arquivo, avisos = _escolhe_exe(entradas)
        if not arquivo:
            return {'ok': False, 'caminho': caminho_pasta,
                    'erro': f'Nenhum .exe em {caminho_pasta}.',
                    'detalhe': f'{len(entradas)} item(ns): '
                               f'{[e.get("name") for e in entradas]}'}

        caminho = f'{caminho_pasta}/{arquivo["name"]}'
        md = svc.file_metadata(caminho) or {}
        content_hash = md.get('content_hash') or ''
        hash_curto = content_hash[:HASH_CURTO] if content_hash else ''
        rotulo = _rotulo_version_txt()

        versao = f'{rotulo or SEM_VERSAO}+{hash_curto}'[:VERSAO_MAX] if hash_curto \
            else (rotulo or SEM_VERSAO)[:VERSAO_MAX]
        base = arquivo['name'][:-4] if arquivo['name'].lower().endswith('.exe') \
            else arquivo['name']
        nome_download = f'{base}-{rotulo}.exe' if rotulo else arquivo['name']

        if not content_hash:
            avisos.append('Dropbox não devolveu content_hash — a auditoria fica '
                          'só com o rótulo.')
        return {'ok': True, 'caminho': caminho, 'nome': arquivo['name'],
                'tamanho': md.get('size') or arquivo.get('size') or 0,
                'modificado': md.get('modified'),
                'content_hash': content_hash, 'hash_curto': hash_curto,
                'rotulo': rotulo, 'versao': versao,
                'nome_download': nome_download, 'avisos': avisos}
    except Exception as exc:
        logger.warning('[qrobo] falha ao ler o instalador no Dropbox: %s', exc)
        return {'ok': False, 'caminho': caminho_pasta,
                'erro': 'Não consegui ler a pasta do instalador no Dropbox.',
                'detalhe': str(exc)[:300]}


def baixar(caminho):
    """Bytes do .exe, ou None. O caminho vem do manifesto — nunca do usuário."""
    try:
        return dropbox_sync._service.download_file(caminho)
    except Exception as exc:
        logger.warning('[qrobo] falha ao baixar %s: %s', caminho, exc)
        return None
