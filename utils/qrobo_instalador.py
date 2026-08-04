# -*- coding: utf-8 -*-
"""Instalador do Q-Robô servido a partir da Dropbox (Fase 4).

O Anderson coloca o pacote VALIDADO em:

    C:\\Users\\...\\Dropbox\\Aplicativos\\QUALICONTAX\\Q-Robo\\qualicontax.zip

O app só LÊ. Nada de Actions publicando, nada de segredo novo: a raiz da app no
Dropbox JÁ é ``/Aplicativos/QUALICONTAX`` (é lá que ``Certificados/`` e
``EMPRESAS/`` são gravados hoje), então o caminho sai de ``_build_path('Q-Robo')``
— que funciona igual em token com escopo de App Folder ou Full Dropbox.

VERSÃO — duas fontes, e a que importa não depende de ninguém lembrar:

  content_hash (Dropbox)  impressão digital determinística do pacote. É a
                          verdade sobre QUAL arquivo foi baixado.
  version.txt (opcional)  rótulo humano ("3.4") que o Anderson escreve ao lado
                          do pacote quando valida. Some ou fica velho? A
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
NOME_PADRAO = 'qualicontax.zip'
NOME_VERSAO = 'version.txt'
NOME_MANUAL = 'Manual_Instalacao_Q-Robo.pdf'
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


def _escolher(entradas, sufixo, canonico):
    """Escolhe UM arquivo da pasta: o de nome canônico, senão o mais recente.

    Melhor servir algo identificado (avisando) do que falhar calado quando o
    Anderson renomeia o arquivo.
    """
    achados = [e for e in entradas if e.get('is_file')
               and e.get('name', '').lower().endswith(sufixo)]
    if not achados:
        return None, []
    exato = next((e for e in achados if e['name'].lower() == canonico.lower()), None)
    if exato:
        extras = [e['name'] for e in achados if e is not exato]
        avisos = ([f'Há outros {sufixo} na pasta ({", ".join(extras)}); '
                   f'servindo o {canonico}.'] if extras else [])
        return exato, avisos
    recente = sorted(achados, key=lambda e: e.get('modified') or '')[-1]
    return recente, [f'Não achei {canonico}; servindo o {sufixo} mais recente '
                     f'({recente["name"]}).']


def _escolhe_pacote(entradas):
    return _escolher(entradas, '.zip', NOME_PADRAO)


def _manual_da_pasta(entradas, caminho_pasta):
    """Manual em PDF da pasta, ou None. NUNCA derruba o instalador: se não tem
    PDF lá, o portal simplesmente não mostra o botão."""
    arquivo, avisos = _escolher(entradas, '.pdf', NOME_MANUAL)
    if not arquivo:
        return None
    return {'nome': arquivo['name'],
            'caminho': f'{caminho_pasta}/{arquivo["name"]}',
            'tamanho': arquivo.get('size') or 0,
            'avisos': avisos}


def manifesto():
    """Retrato do que o app enxerga na pasta. SOMENTE LEITURA, nunca levanta.

    ok=True  -> {'nome','caminho','tamanho','modificado','content_hash',
                 'hash_curto','rotulo','versao','nome_download','avisos','manual'}
    ok=False -> {'erro': <texto para a tela>, 'detalhe': <técnico>, 'caminho',
                 'manual'}

    ``manual`` (PDF de instruções) sai da MESMA listagem — uma chamada só ao
    Dropbox por carregamento de página — e é independente do .exe: pasta sem
    instalador ainda pode ter manual, e vice-versa.
    """
    caminho_pasta = None
    try:
        svc = dropbox_sync._service
        if not svc.is_configured():
            return {'ok': False, 'caminho': None, 'manual': None,
                    'erro': 'Dropbox não configurado neste ambiente.',
                    'detalhe': 'DROPBOX_REFRESH_TOKEN/APP_KEY/APP_SECRET ausentes.'}

        caminho_pasta = pasta()
        entradas = svc.list_folder(caminho_pasta) or []
        manual = _manual_da_pasta(entradas, caminho_pasta)
        arquivo, avisos = _escolhe_pacote(entradas)
        if not arquivo:
            return {'ok': False, 'caminho': caminho_pasta, 'manual': manual,
                    'erro': f'Nenhum pacote (.zip) em {caminho_pasta}.',
                    'detalhe': f'{len(entradas)} item(ns): '
                               f'{[e.get("name") for e in entradas]}'}

        caminho = f'{caminho_pasta}/{arquivo["name"]}'
        md = svc.file_metadata(caminho) or {}
        content_hash = md.get('content_hash') or ''
        hash_curto = content_hash[:HASH_CURTO] if content_hash else ''
        rotulo = _rotulo_version_txt()

        versao = f'{rotulo or SEM_VERSAO}+{hash_curto}'[:VERSAO_MAX] if hash_curto \
            else (rotulo or SEM_VERSAO)[:VERSAO_MAX]
        # Baixa com o nome canônico intacto ('qualicontax.zip'): assim o "Extrair
        # tudo" do Windows cria C:\qualicontax. A versão (content_hash+version.txt)
        # continua na auditoria — não precisa ir no nome do arquivo.
        nome_download = arquivo['name']

        if not content_hash:
            avisos.append('Dropbox não devolveu content_hash — a auditoria fica '
                          'só com o rótulo.')
        return {'ok': True, 'caminho': caminho, 'nome': arquivo['name'],
                'tamanho': md.get('size') or arquivo.get('size') or 0,
                'modificado': md.get('modified'),
                'content_hash': content_hash, 'hash_curto': hash_curto,
                'rotulo': rotulo, 'versao': versao,
                'nome_download': nome_download, 'avisos': avisos,
                'manual': manual}
    except Exception as exc:
        logger.warning('[qrobo] falha ao ler o instalador no Dropbox: %s', exc)
        return {'ok': False, 'caminho': caminho_pasta, 'manual': None,
                'erro': 'Não consegui ler a pasta do instalador no Dropbox.',
                'detalhe': str(exc)[:300]}


def baixar(caminho):
    """Bytes do pacote, ou None. O caminho vem do manifesto — nunca do usuário."""
    try:
        return dropbox_sync._service.download_file(caminho)
    except Exception as exc:
        logger.warning('[qrobo] falha ao baixar %s: %s', caminho, exc)
        return None
