# -*- coding: utf-8 -*-
"""Cofre das credenciais de cobrança (Efí, Bradesco) — 02/11/2026.

Mesma chave mestra do certificado digital (``DFE_CRYPTO_KEY``, Fernet, aceita
"nova,velha" na rotação). O que entra aqui NUNCA vai para log nem para a tela:
a tela mostra só "preenchido" e os últimos 4 caracteres do Client ID.

Regra do Anderson (02/11/2026): credencial de banco mora no banco de dados,
cifrada, por empresa — não no Railway. Assim pôr outra empresa, ou um cliente,
a cobrar pela conta dele é preencher a tela, sem variável nem deploy.
"""
import secrets

from utils.certificado_digital import _fernet, CertificadoError


class SegredoError(Exception):
    pass


def cifrar(valor) -> bytes:
    """str ou bytes → token Fernet (bytes). Vazio vira None (nada a guardar)."""
    if valor in (None, '', b''):
        return None
    if isinstance(valor, str):
        valor = valor.encode('utf-8')
    return _fernet().encrypt(valor)


def decifrar_texto(token) -> str:
    """token Fernet → str ('' se não há token)."""
    b = decifrar_bytes(token)
    return b.decode('utf-8') if b else ''


def decifrar_bytes(token) -> bytes:
    if not token:
        return b''
    if isinstance(token, str):
        token = token.encode()
    try:
        return _fernet().decrypt(bytes(token))
    except CertificadoError:
        raise
    except Exception as exc:          # InvalidToken: chave trocada sem recifrar
        raise SegredoError('Não consegui decifrar a credencial (a chave mestra mudou?).') from exc


def novo_segredo_webhook() -> str:
    """Pedaço aleatório da URL que a Efí chama: 32 caracteres url-safe."""
    return secrets.token_urlsafe(24)[:32]


def mascarar(valor: str, mostrar=4) -> str:
    """'Client_Id_abc123...wxyz' → '•••• wxyz'. Para a tela dizer que está preenchido."""
    v = valor or ''
    return ('•••• ' + v[-mostrar:]) if len(v) > mostrar else ('preenchido' if v else '')
