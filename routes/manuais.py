# -*- coding: utf-8 -*-
"""Manuais de instalação servidos como PÁGINA, não como PDF.

Por que
-------
Os dois manuais eram PDFs soltos na pasta do Dropbox, montados fora do
controle de versão. Divergiram (marcas diferentes) e envelheceram — o do
Q-Colabore descrevia a versão 0.1.0 e mandava buscar o programa no Dropbox,
meses depois de o download passar a sair pelo próprio sistema.

Servindo como página, o manual fica junto do código que ele documenta: quem
mexe no agente edita o HTML no mesmo commit. E some o passo de "gerar o PDF e
lembrar de subir na pasta" — quem quiser PDF imprime pelo navegador (Ctrl+P),
com o CSS de impressão já preparado.

PÚBLICO, sem login, de propósito: quem recebe o link de uso único do instalador
não tem conta no sistema e precisa conseguir ler o manual. Não há segredo aqui
— a chave é que é secreta, e ela não aparece em manual nenhum.

A VERSÃO exibida sai do pacote publicado, não de um número escrito à mão: é o
que impede o manual de voltar a dizer uma versão que não existe mais. Se a
consulta falhar, o manual aparece SEM o selo de versão em vez de mentir.
"""
import logging

from flask import Blueprint, render_template

logger = logging.getLogger(__name__)

manuais = Blueprint('manuais', __name__, url_prefix='/manual')


def _versao_colabore():
    """Versão do pacote do Q-Colabore publicado hoje, ou None. Nunca levanta."""
    try:
        from utils import qcolabore_instalador
        m = qcolabore_instalador.manifesto()
        return m.get('rotulo') if m.get('ok') else None
    except Exception:
        logger.info('[manuais] versao do Q-Colabore indisponivel', exc_info=True)
        return None


def _versao_qrobo():
    """Rótulo da versão validada do Q-Robô, ou None. Nunca levanta."""
    try:
        from utils import qrobo_instalador
        m = qrobo_instalador.manifesto()
        return m.get('rotulo') if m.get('ok') else None
    except Exception:
        logger.info('[manuais] versao do Q-Robo indisponivel', exc_info=True)
        return None


@manuais.route('/qcolabore')
def qcolabore():
    v = _versao_colabore()
    return render_template('manuais/qcolabore.html', versao=v, tem_download=True)


@manuais.route('/qrobo')
def qrobo():
    return render_template('manuais/qrobo.html', versao=_versao_qrobo())
