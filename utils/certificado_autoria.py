# -*- coding: utf-8 -*-
"""Quem mandou cada certificado (05/10/2026).

O e-mail "Certificados recebidos hoje" diz, em cada linha, QUEM enviou. Até
aqui isso só existia para o vínculo feito pela ficha da empresa (registrar()
pega o usuário logado). O vínculo automático do roteador roda sem usuário, e o
caso mais comum — o funcionário larga o .pfx no Q-Colabore — ficava sem autor
(empresa 202 em 29/09/2026).

A LIGAÇÃO
---------
O Q-Colabore conhece o funcionário (a chave dele) e os bytes do arquivo. O
vínculo, minutos depois, baixa os MESMOS bytes. A impressão digital (SHA-256
do conteúdo) liga as duas pontas. NUNCA o nome do arquivo: ele traz a senha.

Ordem de decisão no vínculo:
  1. há usuário logado na requisição      -> FICHA, com o nome dele;
  2. a impressão foi enviada pelo Q-Colabore -> QCOLABORE, com quem enviou;
  3. senão                                 -> DROPBOX, sem autor.

Nada aqui estoura: autoria é informação, e a falta dela nunca pode impedir um
certificado de ser vinculado.
"""
import hashlib
import logging

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

EXT_CERT = ('.pfx', '.p12')


def impressao(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo or b'').hexdigest()


def registrar_envio(conteudo, usuario_id, usuario_nome, ext):
    """Chamado pelo Q-Colabore depois de gravar um .pfx/.p12 na _ENTRADA."""
    try:
        execute_query(
            "INSERT INTO certificado_envio (content_hash, usuario_id, usuario_nome, ext) "
            "VALUES (%s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE usuario_id = VALUES(usuario_id), "
            " usuario_nome = VALUES(usuario_nome), enviado_em = NOW()",
            (impressao(conteudo), usuario_id, (usuario_nome or '')[:120] or None,
             (ext or '')[:8] or None), fetch=False)
    except Exception:
        logger.exception('[certificado] não gravou o autor do envio (segue normal).')


def _usuario_logado():
    try:
        from flask import has_request_context
        from flask_login import current_user
        if has_request_context() and getattr(current_user, 'is_authenticated', False):
            return getattr(current_user, 'id', None), (getattr(current_user, 'nome', None) or '')[:120]
    except Exception:
        pass
    return None, None


def registrar_vinculo(cliente_id, cnpj, validade, validade_anterior, conteudo):
    """Grava a trilha do vínculo com a origem e o autor. Devolve a origem."""
    try:
        uid, nome = _usuario_logado()
        origem = 'FICHA' if uid else None
        if not origem:
            r = execute_query(
                "SELECT usuario_id, usuario_nome FROM certificado_envio WHERE content_hash = %s",
                (impressao(conteudo),), fetch=True, fetch_one=True)
            if r:
                origem, uid, nome = 'QCOLABORE', r.get('usuario_id'), r.get('usuario_nome')
            else:
                origem = 'DROPBOX'
        execute_query(
            "INSERT INTO certificado_vinculo (cliente_id, cnpj, validade, validade_anterior, "
            " origem, usuario_id, usuario_nome) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (cliente_id, cnpj, validade, validade_anterior, origem, uid, nome or None),
            fetch=False)
        return origem
    except Exception:
        logger.exception('[certificado] não gravou a trilha do vínculo (segue normal).')
        return None
