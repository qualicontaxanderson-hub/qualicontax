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

E-MAIL PARA CLIENTE (27/11/2026 — Anderson: "tudo que sair para os clientes ir uma cópia para o app@qualicontax.com.br"
e "um botão de segurança... ter certeza que esse e-mail é da Qualicontax, ao clicar chega um código no e-mail do cliente"):
todo e-mail cujo serviço é de cliente (``SERVICOS_CLIENTE``, ou ``cliente=True``) sai com CÓPIA OCULTA para a caixa do
sistema e ganha no fim o bloco "Este e-mail é da Qualicontax?" com um link único (/v/<código>, routes/email_verifica.py).
O código, a cópia e o documento de origem (``referencia`` = ('boleto'|'grupo'|'orcamento'|'contrato'|'acordo', id))
ficam na linha de ``email_envios``. Avisos à equipe dentro desses serviços passam ``cliente=False``.
"""
import logging
import os
import re
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

logger = logging.getLogger(__name__)

_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'static', 'images', 'logo.png')

APP_URL = os.getenv('APP_URL', 'https://app.qualicontax.com.br').rstrip('/')
COPIA_CLIENTE = (os.getenv('EMAIL_COPIA_CLIENTE', 'app@qualicontax.com.br') or '').strip().lower()
SERVICOS_CLIENTE = ('Boleto', 'Contrato', 'orcamento', 'Acordo')


def para_cliente(servico, cliente=None):
    """O e-mail é para CLIENTE? Quem chama decide com ``cliente``; sem ele, vale o serviço."""
    if cliente is not None:
        return bool(cliente)
    return (servico or '') in SERVICOS_CLIENTE


def _interno(email):
    """Endereço da própria Qualicontax (mesmo domínio da caixa de cópia)."""
    e = (email or '').strip().lower()
    return '@' in e and '@' in COPIA_CLIENTE and e.rsplit('@', 1)[1] == COPIA_CLIENTE.rsplit('@', 1)[1]


def link_verificacao(token):
    return f'{APP_URL}/v/{token}'


def rodape_verificacao(token, largura='760'):
    """(html, texto) do bloco "Este e-mail é da Qualicontax?" — mesma moldura dos e-mails (fundo #eef3ee, cartão branco)."""
    link = link_verificacao(token)
    html = (
        '\n<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#eef3ee;">'
        '<tr><td align="left" style="padding:0 12px 18px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:{largura}px;'
        'background:#ffffff;border-radius:14px;font-family:\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;">'
        '<tr><td style="padding:16px 24px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        '<td valign="middle" style="font-size:14px;line-height:1.5;color:#344054;padding-right:16px;">'
        '<b style="color:#1e4d2b;">Este e-mail é da Qualicontax?</b><br>'
        'Confira em segundos: a página mostra a data, o remetente e o que foi enviado, e você pode pedir um código '
        'no seu próprio e-mail para confirmar.</td>'
        '<td valign="middle" align="right" style="white-space:nowrap;">'
        f'<a href="{link}" style="display:inline-block;background:#ffffff;color:#1e4d2b;border:2px solid #2f8f3e;text-decoration:none;'
        'font-weight:800;font-size:14px;padding:10px 16px;border-radius:10px;">Confirmar</a></td>'
        '</tr></table>'
        f'<div style="font-size:12px;color:#667085;margin-top:8px;word-break:break-all;">ou copie o endereço: {link}</div>'
        '</td></tr></table></td></tr></table>\n')
    texto = f'— Este e-mail é da Qualicontax? Confira em: {link}'
    return html, texto


def _com_rodape(html, texto, token):
    """Encaixa o bloco antes do </body> (ou no fim), na largura do cartão do e-mail (max-width do modelo)."""
    html = html or ''
    m = re.search(r'style="[^"]*max-width:\s*(\d{3,4})px', html)      # a do cartão (style=), não a do @media do <style>
    bloco, linha = rodape_verificacao(token, m.group(1) if m else '760')
    i = html.lower().rfind('</body>')
    html = (html[:i] + bloco + html[i:]) if i >= 0 else html + bloco
    texto = (texto or '').rstrip() + '\n\n' + linha + '\n'
    return html, texto


def _cfg():
    return {
        'host': os.getenv('SMTP_HOST', 'smtp.titan.email').strip(),
        'port': int(os.getenv('SMTP_PORT', '465') or 465),
        'user': os.getenv('SMTP_USER', 'app@qualicontax.com.br').strip(),
        'senha': os.getenv('SMTP_PASSWORD', ''),
        'nome': os.getenv('SMTP_FROM_NOME', 'Qualicontax').strip() or 'Qualicontax',
    }


def _cfg_sistema():
    """A caixa do sistema (app@): a senha digitada no departamento "Sistema" (email_caixas) vale antes da do
    Railway (14/11/2026: "coloque para colocar a senha aqui para ficar padrão"). Sem senha lá, o Railway."""
    env = _cfg()
    try:
        from utils import email_caixas
        cx = email_caixas.cifrada(env['user'])
        if cx:
            return dict(env, host=cx['host'] or env['host'], port=int(cx['porta'] or env['port']),
                        senha=cx['senha'], nome=cx.get('nome') or env['nome'], _do_banco=True)
    except Exception:
        logger.exception('[email] caixa do sistema no banco; sigo com a do Railway')
    return env


def configurado() -> bool:
    return bool(_cfg_sistema()['senha'])


def _usuario_da_requisicao():
    try:
        from flask import has_request_context
        from flask_login import current_user
        if has_request_context() and getattr(current_user, 'is_authenticated', False):
            return int(current_user.id)
    except Exception:
        pass
    return None


def _registrar(c, para, assunto, servico, usuario_id, ok, erro, copia=None, token=None, referencia=None):
    """Uma linha em email_envios por tentativa. Nunca atrapalha o envio."""
    base = ((c.get('user') or '').lower()[:120], ', '.join(para)[:500], (assunto or '')[:255],
            (servico or 'E-mail')[:60], usuario_id, 1 if ok else 0, (erro or '')[:400] or None)
    try:
        from utils.db_helper import execute_query
        try:
            rt, rid = (referencia or (None, None))
            execute_query(
                'INSERT INTO email_envios (remetente, para, assunto, servico, usuario_id, ok, erro, copia, token, referencia_tipo, referencia_id) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                base + ((copia or None), (token or None), (str(rt)[:20] if rt else None), (int(rid) if rid else None)), fetch=False)
        except Exception:
            # banco ainda sem as colunas de 27/11/2026: registra o essencial
            execute_query(
                'INSERT INTO email_envios (remetente, para, assunto, servico, usuario_id, ok, erro) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s)', base, fetch=False)
    except Exception:
        logger.warning('[email] não registrei o envio de "%s" (tabela email_envios?)', assunto)


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


def enviar(para, assunto, html, texto, remetente=None, anexos=None, servico=None, usuario_id=None, cliente=None, referencia=None):
    """Envia e registra em email_envios (quem, para quem, se deu certo). Devolve ``(ok, erro)``. Nunca estoura.
    ``remetente`` troca a caixa (a do departamento); sem ele vale a do sistema (app@).
    ``usuario_id``: quem clicou; sem ele, o usuário logado da requisição; fora de requisição = automático.
    ``cliente``: True/False força "é e-mail para cliente" (cópia oculta + rodapé de verificação); None = pelo serviço.
    ``referencia``: (tipo, id) do documento que o e-mail leva — a página de verificação mostra o resumo dele."""
    para = [p for p in dict.fromkeys((e or '').strip().lower() for e in para) if p]
    if not para:
        return False, 'nenhum destinatário'
    if usuario_id is None:
        usuario_id = _usuario_da_requisicao()
    c = remetente or _cfg_sistema()
    token = copia = None
    if para_cliente(servico, cliente):
        token = secrets.token_urlsafe(18)
        html, texto = _com_rodape(html, texto, token)
        if COPIA_CLIENTE and COPIA_CLIENTE not in para and any(not _interno(p) for p in para):
            copia = COPIA_CLIENTE
    ok, erro = _enviar(para, assunto, html, texto, c, remetente, anexos, copia)
    if not ok and not remetente and c.get('_do_banco'):
        # senha digitada no departamento Sistema recusada: não deixa ninguém sem o código de acesso
        logger.warning('[email] caixa do sistema (banco) falhou: %s; tento a do Railway', erro)
        c2 = _cfg()
        ok2, erro2 = _enviar(para, assunto, html, texto, c2, None, anexos, copia)
        if ok2:
            ok, erro, c = True, None, c2
    _registrar(c, para, assunto, servico, usuario_id, ok, erro, copia=copia, token=token, referencia=referencia)
    return ok, erro


def _enviar(para, assunto, html, texto, c, remetente, anexos, copia=None):
    if not c.get('user'):
        return False, 'remetente sem endereço de e-mail'
    if not c.get('senha'):
        return False, ('a senha da caixa de e-mail não foi preenchida no departamento' if remetente
                       else 'SMTP_PASSWORD não configurada no Railway')
    try:
        msg = montar(para, assunto, html, texto, remetente=c, anexos=anexos)
        # a cópia oculta vai só no envelope (to_addrs): não aparece em nenhum cabeçalho para o cliente
        destinos = list(para) + ([copia] if copia else [])
        ctx = ssl.create_default_context()
        if int(c['port']) == 465:
            with smtplib.SMTP_SSL(c['host'], int(c['port']), context=ctx, timeout=30) as s:
                s.login(c['user'], c['senha'])
                s.send_message(msg, to_addrs=destinos)
        else:
            with smtplib.SMTP(c['host'], int(c['port']), timeout=30) as s:
                s.starttls(context=ctx)
                s.login(c['user'], c['senha'])
                s.send_message(msg, to_addrs=destinos)
        logger.info('[email] enviado "%s" para %d destinatário(s)%s.', assunto, len(para), ' + cópia oculta' if copia else '')
        return True, None
    except smtplib.SMTPAuthenticationError:
        return False, (f"o servidor de e-mail recusou a senha de {c['user']}" if remetente
                       else 'o Titan recusou usuário/senha (confira SMTP_USER e SMTP_PASSWORD)')
    except Exception as exc:                       # rede, porta bloqueada, timeout…
        logger.exception('[email] falha ao enviar "%s".', assunto)
        return False, f'{type(exc).__name__}: {str(exc)[:300]}'
