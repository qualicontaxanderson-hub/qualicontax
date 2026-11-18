"""Quem da EQUIPE está abrindo a página de pagamento do cliente (/c/<token>) — 18/11/2026.

Anderson: "eles vão enviar para o cliente e podem ter a curiosidade de abrir... se ele fizer isso queria que
chegasse um e-mail informando que ele abriu um boleto do cliente e não deve ser feito isso, por conta do registro
de auditoria do recebimento do cliente".

A página do cliente não pede login. Para saber que quem abriu é da equipe:
  1. a pessoa está logada no sistema naquele navegador (current_user); ou
  2. o navegador já foi usado por ela para entrar no sistema: a cada página do sistema aberta logada, o aparelho
     ganha um cookie assinado de 1 ano com o id da pessoa (fica mesmo depois do Sair).
Navegador onde a pessoa nunca entrou no sistema (ex.: o do WhatsApp no celular) não tem como ser reconhecido.

Abertura feita pela equipe NÃO conta como "cliente abriu" e gera o aviso por e-mail (com intervalo, para não
mandar um e-mail por clique).
"""
import logging
import time

from flask import current_app, has_request_context, request
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer

logger = logging.getLogger(__name__)

COOKIE = 'qcx_eq'
_SAL = 'qcx-equipe-aparelho'
_UM_ANO = 365 * 24 * 3600
_avisados = {}          # (usuario_id, boleto_id) -> quando avisou (intervalo de 6 h por processo)
_INTERVALO = 6 * 3600


def _ser():
    return URLSafeSerializer(current_app.secret_key, salt=_SAL)


def marcar(resp):
    """after_app_request: aparelho de quem está logado (funcionário, não conta de cliente) ganha o cookie."""
    try:
        if not getattr(current_user, 'is_authenticated', False):
            return resp
        if getattr(current_user, 'is_cliente', lambda: False)():
            return resp
        if (request.path or '').startswith(('/c/', '/g/', '/static/', '/webhooks/')):
            return resp
        valor = _ser().dumps({'u': int(current_user.id)})
        if request.cookies.get(COOKIE) != valor:
            resp.set_cookie(COOKIE, valor, max_age=_UM_ANO, secure=request.is_secure, httponly=True, samesite='Lax')
    except Exception:
        logger.exception('[equipe_aparelho] marcar')
    return resp


def quem():
    """(usuario_id, como) de quem da equipe está abrindo, ou None se não der para dizer que é da equipe."""
    if not has_request_context():
        return None
    try:
        if getattr(current_user, 'is_authenticated', False) and not getattr(current_user, 'is_cliente', lambda: False)():
            return int(current_user.id), 'logado no sistema neste navegador'
        c = request.cookies.get(COOKIE)
        if c:
            return int(_ser().loads(c)['u']), 'aparelho onde a pessoa já entrou no sistema'
    except (BadSignature, KeyError, ValueError, TypeError):
        return None
    except Exception:
        logger.exception('[equipe_aparelho] quem')
    return None


def _aprovadores():
    from utils.db_helper import execute_query
    return execute_query(
        """SELECT DISTINCT u.id, u.nome, u.email FROM usuarios u
            WHERE u.email LIKE '%%@%%' AND (u.tipo_usuario = 'ADMIN'
               OR u.id IN (SELECT usuario_id FROM usuario_permissoes_extra WHERE permissao_codigo = 'financeiro.lancamentos.aprovar'))""",
        fetch=True) or []


def avisar(eq, b, qual):
    """A equipe abriu a página/boleto/nota do cliente: e-mail para a pessoa, com cópia para quem aprova
    (Anderson e Alessandra). Quem aprova abrindo não gera e-mail (só não conta como cliente)."""
    try:
        from utils.db_helper import execute_query
        from utils import email_envio
        uid, como = eq
        aprov = _aprovadores()
        if uid in {a['id'] for a in aprov}:
            return
        chave = (uid, b['id'])
        agora = time.time()
        if agora - _avisados.get(chave, 0) < _INTERVALO:
            return
        _avisados[chave] = agora
        u = execute_query('SELECT id, nome, email FROM usuarios WHERE id = %s', (uid,), fetch=True, fetch_one=True) or {}
        cli = execute_query(
            """SELECT COALESCE(c.nome_razao_social, t.contraparte_nome, %s) AS nome FROM fin_titulos t
                 LEFT JOIN clientes c ON c.id = t.cliente_id WHERE t.id = %s""",
            (b.get('pagador_nome') or 'cliente', b.get('titulo_id') or 0), fetch=True, fetch_one=True) or {}
        nome_cli = cli.get('nome') or b.get('pagador_nome') or 'cliente'
        from utils.qrobo_chaves import contexto_request
        _ip, agente = contexto_request()
        aparelho = 'celular' if any(k in (agente or '') for k in ('iPhone', 'Android', 'Mobile')) else 'computador'
        o_que = {'boleto': 'a página de pagamento / o boleto', 'nota': 'a nota fiscal'}.get(qual, 'a página de pagamento')
        venc = b['vencimento'].strftime('%d/%m/%Y') if b.get('vencimento') else '—'
        from datetime import datetime
        from zoneinfo import ZoneInfo
        quando = datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y às %H:%M')
        nome_u = u.get('nome') or 'Alguém da equipe'
        primeiro = nome_u.split()[0]
        nboleto = b.get('charge_id') or b['id']
        assunto = f'Aviso: {nome_u} abriu a cobrança do cliente {nome_cli}'
        # 18/11/2026 — Anderson: "Melhore o e-mail com logo e tudo mais e não mencione valor, só número de boleto e
        # data de vencimento"
        texto = (f'{primeiro}, você abriu {o_que} do cliente {nome_cli} em {quando}, pelo {aparelho}.\n'
                 f'Boleto nº {nboleto} · vencimento {venc}.\n\n'
                 'Esse link é do cliente: o sistema usa a abertura dele como registro de que o cliente recebeu e abriu a '
                 'cobrança. Por isso a equipe não deve abrir o link. Para mandar a cobrança, use em Lançamentos o botão '
                 '"Cobrança" e copie o texto, o código Pix ou a linha digitável.\n\n'
                 f'Esta abertura NÃO foi contada como do cliente. Reconhecido como da equipe por: {como}.')

        def lin(rot, val):
            return (f'<tr><td style="padding:9px 14px;font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;'
                    f'border-bottom:1px solid #eef2f0;white-space:nowrap">{rot}</td><td style="padding:9px 14px;font-size:14px;'
                    f'font-weight:bold;color:#0f172a;border-bottom:1px solid #eef2f0">{val}</td></tr>')
        html = f"""<div style="background:#f3f6f4;padding:24px 12px;font-family:Arial,Helvetica,sans-serif">
<div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e3ece7">
  <div style="padding:18px 22px;border-bottom:1px solid #eef2f0"><img src="cid:logo" alt="Qualicontax" style="height:46px;display:block"></div>
  <div style="background:#b45309;background:linear-gradient(135deg,#d97706,#b45309);padding:18px 22px;color:#ffffff">
    <div style="font-size:12px;letter-spacing:1px;text-transform:uppercase;opacity:.85">Aviso de segurança</div>
    <div style="font-size:19px;font-weight:bold;margin-top:4px">A cobrança do cliente foi aberta pela equipe</div>
  </div>
  <div style="padding:20px 22px;color:#0f172a">
    <p style="font-size:15px;margin:0 0 14px"><b>{primeiro}</b>, você abriu {o_que} do cliente <b>{nome_cli}</b>.</p>
    <table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;border:1px solid #eef2f0;border-radius:10px;margin:0 0 16px">
      {lin('Quem abriu', nome_u)}{lin('Quando', quando)}{lin('Aparelho', aparelho.capitalize())}
      {lin('Boleto nº', nboleto)}{lin('Vencimento', venc)}
    </table>
    <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:12px 14px;font-size:13.5px;line-height:1.5;color:#78350f">
      <b>Esse link é do cliente.</b> O sistema usa a abertura dele como registro de que o cliente recebeu e abriu a
      cobrança, por isso a equipe não deve abrir o link.</div>
    <p style="font-size:13.5px;line-height:1.5;margin:14px 0 0">Para mandar a cobrança ao cliente, use em <b>Financeiro → Lançamentos</b>
      o botão <b>Cobrança</b> e copie o texto, o código Pix ou a linha digitável.</p>
    <p style="font-size:13px;margin:14px 0 0;color:#15803d"><b>Esta abertura não foi contada como do cliente.</b></p>
  </div>
  <div style="padding:12px 22px;background:#f8fbf9;border-top:1px solid #eef2f0;font-size:11.5px;color:#94a3b8">
    Reconhecido como da equipe por: {como}. Este aviso vai para quem abriu, com cópia para o Anderson e a Alessandra.</div>
</div></div>"""
        para = [u.get('email')] + [a['email'] for a in aprov]
        email_envio.enviar(para, assunto, html, texto, usuario_id=uid, servico='Aviso de abertura pela equipe')
        logger.info('[equipe_aparelho] %s abriu %s do boleto %s (%s)', uid, qual, b['id'], como)
    except Exception:
        logger.exception('[equipe_aparelho] avisar')
