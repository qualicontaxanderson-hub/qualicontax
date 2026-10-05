# -*- coding: utf-8 -*-
"""Envio de e-mail pelo Titan (app@qualicontax.com.br) — 05/10/2026.

Só o transporte. Quem monta o conteúdo é ``utils/relatorios_email.py``.

A senha NUNCA mora no código: vem da variável ``SMTP_PASSWORD`` do Railway.
Sem ela, ``configurado()`` é False e nada é enviado (o chamador registra a
falha com a mensagem certa, em vez de estourar).

A logo vai EMBUTIDA no e-mail (Content-ID ``logo``), não por link: o Outlook
bloqueia imagem externa por padrão, e o aviso chegaria com um buraco no topo.

Variáveis (todas com padrão, menos a senha):
  SMTP_HOST      smtp.titan.email
  SMTP_PORT      465  (SSL direto; 587 usa STARTTLS)
  SMTP_USER      app@qualicontax.com.br
  SMTP_PASSWORD  (obrigatória)
  SMTP_FROM_NOME Qualicontax
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

logger = logging.getLogger(__name__)

_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'static', 'images', 'logo.png')


def _cfg():
    return {
        'host': os.getenv('SMTP_HOST', 'smtp.titan.email').strip(),
        'port': int(os.getenv('SMTP_PORT', '465') or 465),
        'user': os.getenv('SMTP_USER', 'app@qualicontax.com.br').strip(),
        'senha': os.getenv('SMTP_PASSWORD', ''),
        'nome': os.getenv('SMTP_FROM_NOME', 'Qualicontax').strip() or 'Qualicontax',
    }


def configurado() -> bool:
    return bool(_cfg()['senha'])


def montar(para, assunto, html, texto):
    """EmailMessage pronta (texto + HTML + logo embutida). Separado do envio
    para o teste conferir a mensagem sem abrir conexão."""
    c = _cfg()
    msg = EmailMessage()
    msg['From'] = formataddr((c['nome'], c['user']))
    msg['To'] = ', '.join(para)
    msg['Subject'] = assunto
    msg['Message-ID'] = make_msgid(domain=c['user'].split('@')[-1] or None)
    msg.set_content(texto)
    msg.add_alternative(html, subtype='html')
    try:
        with open(_LOGO, 'rb') as f:
            msg.get_payload()[1].add_related(f.read(), 'image', 'png', cid='<logo>',
                                             filename='logo.png')
    except OSError:
        logger.warning('[email] logo não encontrada em %s; segue sem ela.', _LOGO)
    return msg


def enviar(para, assunto, html, texto):
    """Envia. Devolve ``(ok, erro)``. Nunca estoura."""
    para = [p for p in dict.fromkeys((e or '').strip().lower() for e in para) if p]
    if not para:
        return False, 'nenhum destinatário'
    c = _cfg()
    if not c['senha']:
        return False, 'SMTP_PASSWORD não configurada no Railway'
    try:
        msg = montar(para, assunto, html, texto)
        ctx = ssl.create_default_context()
        if c['port'] == 465:
            with smtplib.SMTP_SSL(c['host'], c['port'], context=ctx, timeout=30) as s:
                s.login(c['user'], c['senha'])
                s.send_message(msg)
        else:
            with smtplib.SMTP(c['host'], c['port'], timeout=30) as s:
                s.starttls(context=ctx)
                s.login(c['user'], c['senha'])
                s.send_message(msg)
        logger.info('[email] enviado "%s" para %d destinatário(s).', assunto, len(para))
        return True, None
    except smtplib.SMTPAuthenticationError:
        return False, 'o Titan recusou usuário/senha (confira SMTP_USER e SMTP_PASSWORD)'
    except Exception as exc:                       # rede, porta bloqueada, timeout…
        logger.exception('[email] falha ao enviar "%s".', assunto)
        return False, f'{type(exc).__name__}: {str(exc)[:300]}'
