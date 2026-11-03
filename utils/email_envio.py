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

REMETENTE POR CONTA (03/11/2026): o boleto e a nota saem da caixa do Financeiro
(``fin_cobranca_contas.email_*``, senha cifrada no banco), não da app@. Por isso
``montar``/``enviar`` aceitam ``remetente`` = {host, port, user, senha, nome};
sem ele, vale a configuração do Railway (relatórios). ``anexos`` = [(nome, bytes, mime)].
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


def montar(para, assunto, html, texto, remetente=None, anexos=None):
    """EmailMessage pronta (texto + HTML + logo embutida + anexos). Separado do envio
    para o teste conferir a mensagem sem abrir conexão."""
    c = remetente or _cfg()
    msg = EmailMessage()
    msg['From'] = formataddr((c['nome'], c['user']))
    msg['To'] = ', '.join(para)
    msg['Subject'] = assunto
    msg['Message-ID'] = make_msgid(domain=c['user'].split('@')[-1] or None)
    msg.set_content(texto)
    msg.add_alternative(html, subtype='html')
    try:
        if 'cid:logo' not in (html or ''):
            raise OSError('sem logo no html')     # e-mail só texto: nada de logo solta como anexo
        with open(_LOGO, 'rb') as f:
            # inline, não attachment: imagem "anexada" a pouco texto pesa no
            # filtro de spam (Cloudmark deu 100 no aviso de 06/10).
            msg.get_payload()[1].add_related(f.read(), 'image', 'png', cid='<logo>',
                                             disposition='inline', filename='logo.png')
    except OSError as exc:
        if 'sem logo' not in str(exc):
            logger.warning('[email] logo não encontrada em %s; segue sem ela.', _LOGO)
    for nome, conteudo, mime in (anexos or []):
        tipo, _, sub = (mime or 'application/octet-stream').partition('/')
        msg.add_attachment(conteudo, maintype=tipo, subtype=sub or 'octet-stream', filename=nome)
    return msg


def enviar(para, assunto, html, texto, remetente=None, anexos=None):
    """Envia. Devolve ``(ok, erro)``. Nunca estoura.
    ``remetente`` troca a caixa (e-mail de cobrança da conta); sem ele vale a do Railway."""
    para = [p for p in dict.fromkeys((e or '').strip().lower() for e in para) if p]
    if not para:
        return False, 'nenhum destinatário'
    c = remetente or _cfg()
    if not c.get('user'):
        return False, 'remetente sem endereço de e-mail'
    if not c.get('senha'):
        return False, ('a senha da caixa de e-mail não foi preenchida na conta de cobrança' if remetente
                       else 'SMTP_PASSWORD não configurada no Railway')
    try:
        msg = montar(para, assunto, html, texto, remetente=c, anexos=anexos)
        ctx = ssl.create_default_context()
        if int(c['port']) == 465:
            with smtplib.SMTP_SSL(c['host'], int(c['port']), context=ctx, timeout=30) as s:
                s.login(c['user'], c['senha'])
                s.send_message(msg)
        else:
            with smtplib.SMTP(c['host'], int(c['port']), timeout=30) as s:
                s.starttls(context=ctx)
                s.login(c['user'], c['senha'])
                s.send_message(msg)
        logger.info('[email] enviado "%s" para %d destinatário(s).', assunto, len(para))
        return True, None
    except smtplib.SMTPAuthenticationError:
        return False, (f"o servidor de e-mail recusou a senha de {c['user']}" if remetente
                       else 'o Titan recusou usuário/senha (confira SMTP_USER e SMTP_PASSWORD)')
    except Exception as exc:                       # rede, porta bloqueada, timeout…
        logger.exception('[email] falha ao enviar "%s".', assunto)
        return False, f'{type(exc).__name__}: {str(exc)[:300]}'
