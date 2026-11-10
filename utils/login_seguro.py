# -*- coding: utf-8 -*-
"""Login seguro (10/11/2026, pedido do Anderson).

Regras:
  * Ficou MAIS DE 24 HORAS sem usar o sistema neste navegador -> a sessão cai e,
    ao voltar, depois da senha vem um CÓDIGO DE 6 NÚMEROS enviado ao e-mail da
    pessoa (sai de app@qualicontax.com.br), que vale 10 minutos.
  * Navegador novo (nunca confirmado para esta pessoa) também pede o código.
  * O "Lembrar-me" de 1 ano deixou de existir: ninguém fica conectado sem usar.
  * O e-mail leva o NOME e o login da pessoa, e o código só serve para quem o
    pediu: o código da Talita não entra no login do Henrique.
  * O administrador pode LIBERAR SEM CÓDIGO (tela de Usuários): vale para a
    próxima entrada nas 24 horas seguintes. Para quando o e-mail não chega.
  * Grava último login, última atividade e o IP REAL (o Railway põe um proxy na
    frente; ``request.remote_addr`` era sempre 100.64.x.x, o endereço do proxy).

Freio de emergência: variável ``LOGIN_CODIGO=0`` no Railway desliga o código
(o resto continua). Sem ``SMTP_PASSWORD`` no serviço web também não se pede
código — trancar todos do lado de fora por falta de e-mail seria pior.
"""
import hashlib
import ipaddress
import logging
import os
import secrets
import time
from datetime import datetime

from flask import current_app, flash, g, redirect, request, session, url_for
from flask_login import current_user, logout_user
from itsdangerous import BadSignature, URLSafeSerializer

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

HORAS_SEM_USO = 24
MINUTOS_CODIGO = 10
TENTATIVAS = 5
COOKIE = 'qc_disp'
_SEG_SEM_USO = HORAS_SEM_USO * 3600


# --------------------------------------------------------------------- IP real
def _privado(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return a.is_private or a.is_loopback or a.is_link_local or a in ipaddress.ip_network('100.64.0.0/10')


def ip_cliente():
    """IP de quem está acessando. Pega, no X-Forwarded-For, o último endereço
    PÚBLICO (o que o proxy do Railway anotou); o que vem antes dele o próprio
    navegador pode inventar. Sem cabeçalho, fica o remote_addr."""
    try:
        cadeia = [x.strip() for x in (request.headers.get('X-Forwarded-For') or '').split(',') if x.strip()]
        for ip in reversed(cadeia):
            if not _privado(ip):
                return ip[:45]
        real = (request.headers.get('X-Real-IP') or '').strip()
        if real and not _privado(real):
            return real[:45]
        return (request.remote_addr or '')[:45]
    except Exception:
        return ''


# ------------------------------------------------------- navegador confirmado
def _ser():
    return URLSafeSerializer(current_app.secret_key, salt='qc-dispositivo')


def _dispositivos():
    try:
        d = _ser().loads(request.cookies.get(COOKIE, ''))
        return {str(k): int(v) for k, v in (d or {}).items()}
    except (BadSignature, ValueError, TypeError, AttributeError):
        return {}


def navegador_confirmado(uid):
    t = _dispositivos().get(str(uid))
    return bool(t) and time.time() - t <= _SEG_SEM_USO


def renovar_navegador(resp, uid):
    """Grava/renova a marca 'este navegador já confirmou o código desta pessoa'."""
    d = {k: v for k, v in _dispositivos().items() if time.time() - v <= _SEG_SEM_USO}
    d[str(uid)] = int(time.time())
    d = dict(sorted(d.items(), key=lambda kv: -kv[1])[:5])
    resp.set_cookie(COOKIE, _ser().dumps(d), max_age=30 * 86400, httponly=True, samesite='Lax',
                    secure=current_app.config.get('SESSION_COOKIE_SECURE', False))
    return resp


# ---------------------------------------------------------------- o código
def codigo_ligado():
    if os.getenv('LOGIN_CODIGO', '1').strip() == '0':
        return False
    from utils import email_envio
    if not email_envio.configurado():
        logger.warning('[login] SMTP_PASSWORD ausente no serviço: login segue SEM código.')
        return False
    return True


def motivo_sem_codigo(user):
    """None = precisa de código. Senão, a razão de dispensar (vai para o log)."""
    if navegador_confirmado(user.id):
        return 'navegador_confirmado'
    lib = execute_query("SELECT login_liberado_ate FROM usuarios WHERE id = %s",
                        (user.id,), fetch=True, fetch_one=True) or {}
    if lib.get('login_liberado_ate') and lib['login_liberado_ate'] >= datetime.now():
        return 'liberado_pelo_admin'
    if not codigo_ligado():
        return 'codigo_desligado'
    return None


def mascarar(email):
    nome, _, dom = (email or '').partition('@')
    if not dom:
        return email or ''
    return (nome[:2] + '•' * max(len(nome) - 2, 2)) + '@' + dom


def enviar_codigo(user):
    """Gera o código, guarda só o hash e manda o e-mail. Devolve (ok, erro)."""
    from utils import email_envio
    codigo = '%06d' % secrets.randbelow(1000000)
    ip = ip_cliente()
    execute_query(
        "UPDATE login_codigos SET usado_em = NOW(), motivo = 'substituido' "
        " WHERE usuario_id = %s AND usado_em IS NULL", (user.id,), fetch=False)
    execute_query(
        "INSERT INTO login_codigos (usuario_id, codigo_hash, expira_em, ip, user_agent) "
        "VALUES (%s, %s, NOW() + INTERVAL %s MINUTE, %s, %s)",
        (user.id, hashlib.sha256(codigo.encode()).hexdigest(), MINUTOS_CODIGO, ip,
         (request.headers.get('User-Agent') or '')[:255]), fetch=False)
    agora = datetime.now().strftime('%d/%m/%Y às %H:%M')
    nome = user.nome
    assunto = f'{nome}, seu código de acesso ao Qualicontax: {codigo}'
    texto = (f'Olá, {nome} (login {user.login}).\n\n'
             f'Seu código para entrar no Qualicontax é: {codigo}\n'
             f'Vale por {MINUTOS_CODIGO} minutos.\n\n'
             f'Este código é só de {nome}: não funciona no login de outra pessoa.\n'
             f'Pedido em {agora}, do endereço (IP) {ip or "desconhecido"}.\n'
             'Se não foi você, não passe o código para ninguém e avise o Anderson.')
    html = f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:0 auto;color:#0f172a">
  <img src="cid:logo" alt="Qualicontax" style="height:48px;margin:8px 0 16px">
  <p style="font-size:15px;margin:0 0 6px">Olá, <b>{nome}</b> <span style="color:#64748b">(login <b>{user.login}</b>)</span>.</p>
  <p style="font-size:14px;margin:0 0 14px">Seu código para entrar no Qualicontax é:</p>
  <div style="font-size:34px;font-weight:800;letter-spacing:8px;background:#f0fdf4;border:1px solid #bbf7d0;color:#0f3d2e;
              border-radius:12px;padding:14px 0;text-align:center">{codigo}</div>
  <p style="font-size:13px;color:#475569;margin:14px 0 4px">Vale por <b>{MINUTOS_CODIGO} minutos</b>.</p>
  <p style="font-size:13px;color:#b45309;margin:0 0 14px"><b>Este código é só de {nome}</b>: não funciona no login de outra pessoa.</p>
  <p style="font-size:12px;color:#94a3b8;margin:0">Pedido em {agora}, do endereço (IP) {ip or 'desconhecido'}.<br>
     Se não foi você, não passe o código para ninguém e avise o Anderson.</p>
</div>"""
    try:
        ok, erro = email_envio.enviar([user.email], assunto, html, texto)
    except Exception as e:                      # nunca estoura no login
        ok, erro = False, str(e)
    if not ok:
        logger.warning('[login] código de %s não foi enviado: %s', user.login, erro)
    return ok, erro


def conferir_codigo(uid, codigo):
    """'ok' | 'errado' | 'vencido' (sem código válido, venceu ou estourou as tentativas)."""
    c = execute_query(
        "SELECT id, codigo_hash, tentativas, expira_em FROM login_codigos "
        " WHERE usuario_id = %s AND usado_em IS NULL ORDER BY id DESC LIMIT 1",
        (uid,), fetch=True, fetch_one=True)
    if not c or c['expira_em'] < datetime.now() or c['tentativas'] >= TENTATIVAS:
        return 'vencido'
    digitado = ''.join(ch for ch in (codigo or '') if ch.isdigit())
    if hashlib.sha256(digitado.encode()).hexdigest() != c['codigo_hash']:
        execute_query("UPDATE login_codigos SET tentativas = tentativas + 1 WHERE id = %s",
                      (c['id'],), fetch=False)
        return 'vencido' if c['tentativas'] + 1 >= TENTATIVAS else 'errado'
    execute_query("UPDATE login_codigos SET usado_em = NOW(), motivo = 'entrou' WHERE id = %s",
                  (c['id'],), fetch=False)
    return 'ok'


def gravar_entrada(uid):
    execute_query(
        "UPDATE usuarios SET ultimo_login = NOW(), ultimo_acesso = NOW(), ultimo_ip = %s, "
        "       login_liberado_ate = NULL WHERE id = %s", (ip_cliente(), uid), fetch=False)
    session['qc_vis'] = int(time.time())
    session['qc_db'] = int(time.time())


# ------------------------------------------------- vigia de cada requisição
def vigia_sessao():
    """before_request: derruba a sessão parada há mais de 24 h e anota o uso."""
    if request.endpoint in (None, 'static') or not current_user.is_authenticated:
        return None
    agora = int(time.time())
    visto = session.get('qc_vis')
    if visto is None and session.get('_fresh') is False:
        # Sessão ressuscitada pelo cookie do antigo "Lembrar-me" (1 ano): não vale mais.
        visto = 0
    if visto is not None and agora - int(visto) > _SEG_SEM_USO:
        uid, unome, ulogin = current_user.id, getattr(current_user, 'nome', None), getattr(current_user, 'login', None)
        logout_user()
        session.pop('qc_vis', None)
        session.pop('qc_db', None)
        try:
            from utils.atividade import registrar_agente
            registrar_agente('escrita.sessao_expirou', 'acesso', usuario_id=uid, usuario_nome=unome, usuario_login=ulogin,
                             tabela='usuarios', registro_id=uid,
                             depois={'motivo': 'mais de %d horas sem usar' % HORAS_SEM_USO})
        except Exception:
            pass
        flash('Você ficou mais de 24 horas sem usar o sistema. Entre de novo.', 'warning')
        if request.method == 'GET' and not request.path.startswith('/api'):
            return redirect(url_for('auth.login', next=request.full_path))
        return redirect(url_for('auth.login'))
    if visto is None or agora - int(visto) >= 60:
        session['qc_vis'] = agora
    if agora - int(session.get('qc_db') or 0) >= 300:
        session['qc_db'] = agora
        g.qc_renovar = current_user.id
        try:
            execute_query("UPDATE usuarios SET ultimo_acesso = NOW(), ultimo_ip = %s WHERE id = %s",
                          (ip_cliente(), current_user.id), fetch=False)
        except Exception as e:
            logger.warning('[login] não gravou o último acesso: %s', e)
    return None


def renovar_depois(resp):
    """after_request: quem está usando mantém o navegador confirmado."""
    uid = g.pop('qc_renovar', None)
    if uid:
        try:
            renovar_navegador(resp, uid)
        except Exception:
            pass
    return resp
