# -*- coding: utf-8 -*-
"""Portal do Instalador Q-Robô — sessão de ESCOPO RESTRITO (Fase 2).

O colaborador roda isto na máquina do CLIENTE. Ele entra com a MESMA credencial
do app, mas a sessão nascida aqui é marcada como 'instalador' e, a partir daí,
só alcança os endpoints deste blueprint. Digitar /escrita-fiscal/... na barra
não leva a lugar nenhum — o gate abaixo é DENY BY DEFAULT e roda antes de todo
request do app, não só dos daqui.

TRÊS MECANISMOS, PROPOSITALMENTE SEPARADOS:

  1. ESCOPO — ``session['qrobo_escopo']='instalador'``. Vive na SESSÃO, não no
     usuário: o mesmo Rodrigo entra pelo /login no escritório e tem o app
     inteiro. Trocar de contexto exige sair do portal (não é bug, é o desenho).

  2. MORRER AO FECHAR O NAVEGADOR — cookie de sessão, sem Expires. Depende de
     ``session.permanent=False`` (explícito aqui) e de NUNCA usar remember.
     ATENÇÃO: usar PERMANENT_SESSION_LIFETIME para o timeout de 2h daria
     Expires ao cookie e ele SOBREVIVERIA ao fechar o navegador — o oposto do
     pedido. Por isso o timeout é carimbo próprio, e não config do Flask.

  3. 2H OCIOSO — ``session['qrobo_visto_em']`` renovado a cada request (janela
     deslizante). Usa time.time() (epoch, imune a fuso; o processo é UTC no
     Railway). O aviso na tela é cosmético: a autoridade é o servidor.

CSRF: o app não tem proteção CSRF hoje. Aqui, como geração de chave é ação
sensível, todo POST do portal exige um token de formulário por sessão.
"""
import hmac
import logging
import secrets
import time
from functools import wraps

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, jsonify)
from flask_login import login_user, logout_user, current_user

from models.usuario import Usuario
from utils.auth_helper import verify_password

logger = logging.getLogger(__name__)

qrobo = Blueprint('qrobo', __name__, url_prefix='/qrobo')

PERMISSAO = 'qrobo.instalador'
ESCOPO = 'instalador'

CHAVE_ESCOPO = 'qrobo_escopo'
CHAVE_VISTO = 'qrobo_visto_em'
CHAVE_CSRF = 'qrobo_csrf'

OCIOSO_SEGUNDOS = 2 * 60 * 60          # 2 horas

# Allowlist do escopo restrito. É PREFIXO de endpoint, não de URL: endpoint novo
# no app nasce BLOQUEADO para a sessão de instalador (deny by default).
#   'qrobo.'      -> tudo deste blueprint
#   'static'      -> css/js/imagem do próprio portal
#   'health'      -> health check do Railway (não tem sessão, mas é inofensivo)
#   'auth.logout' -> saída de emergência, nunca deixa o usuário preso
ENDPOINTS_LIBERADOS = ('qrobo.',)
ENDPOINTS_LIBERADOS_EXATOS = ('static', 'health', 'auth.logout')


# ---------------------------------------------------------------------------
# Sessão
# ---------------------------------------------------------------------------
def _sessao_instalador():
    return session.get(CHAVE_ESCOPO) == ESCOPO


def _endpoint_liberado(endpoint):
    """True se o endpoint é alcançável por uma sessão de escopo instalador.

    ``endpoint`` None (URL inexistente) é NEGADO de propósito: numa sessão
    restrita, um 403 conta menos sobre o app do que um 404.
    """
    if not endpoint:
        return False
    return (endpoint in ENDPOINTS_LIBERADOS_EXATOS
            or any(endpoint.startswith(p) for p in ENDPOINTS_LIBERADOS))


def _encerrar_sessao():
    """Derruba a sessão de verdade: Flask-Login (inclui o cookie remember) + sessão."""
    try:
        logout_user()
    except Exception:
        pass
    session.clear()


def _abrir_sessao_instalador(user):
    """Sessão nova, restrita e não-persistente."""
    session.clear()                    # mata o que houvesse antes (session fixation)
    login_user(user, remember=False)   # NUNCA remember na máquina do cliente
    session.permanent = False          # cookie de sessão: morre ao fechar o navegador
    session[CHAVE_ESCOPO] = ESCOPO
    session[CHAVE_VISTO] = time.time()


def segundos_ocioso():
    visto = session.get(CHAVE_VISTO)
    if not isinstance(visto, (int, float)):
        return None
    return max(0, int(time.time() - visto))


# ---------------------------------------------------------------------------
# GATE GLOBAL — registrado em app.py, roda antes de TODO request
# ---------------------------------------------------------------------------
def gate_escopo_instalador():
    """Bloqueia a sessão de instalador fora do portal e aplica o ocioso de 2h.

    Devolve None para seguir o fluxo normal. Sessão sem o escopo não é tocada:
    o app inteiro continua exatamente como era para todo mundo.
    """
    if not _sessao_instalador():
        return None

    endpoint = request.endpoint or ''
    liberado = _endpoint_liberado(endpoint)

    # 1) Inatividade: sessão morta é tratada como deslogado, não como 403.
    ocioso = segundos_ocioso()
    if ocioso is None or ocioso > OCIOSO_SEGUNDOS:
        logger.info('[qrobo] sessão de instalador expirada (ocioso=%ss) — encerrando.', ocioso)
        _encerrar_sessao()
        if endpoint == 'qrobo.login':
            return None                # já está na tela certa; sem redirect em loop
        return redirect(url_for('qrobo.login', expirado=1))

    # 2) Deny by default.
    if not liberado:
        logger.warning('[qrobo] sessão de instalador BLOQUEADA em %s (endpoint=%s, user=%s)',
                       request.path, endpoint or '-',
                       getattr(current_user, 'id', None))
        if request.path.startswith('/api/') or request.is_json:
            return jsonify({'status': 'fora_do_escopo',
                            'erro': 'Sessão do Portal do Instalador não acessa esta área.'}), 403
        return render_template('qrobo/bloqueado.html', destino=request.path), 403

    # 3) Janela deslizante.
    session[CHAVE_VISTO] = time.time()
    return None


# ---------------------------------------------------------------------------
# CSRF (escopo do portal)
# ---------------------------------------------------------------------------
def csrf_token():
    tok = session.get(CHAVE_CSRF)
    if not tok:
        tok = secrets.token_urlsafe(32)
        session[CHAVE_CSRF] = tok
    return tok


def csrf_valido():
    enviado = (request.form.get('csrf_token')
               or request.headers.get('X-CSRF-Token') or '')
    guardado = session.get(CHAVE_CSRF) or ''
    # compare_digest: comparação de tempo constante, não vaza o token por timing.
    return bool(guardado) and bool(enviado) and hmac.compare_digest(enviado, guardado)


def exige_csrf(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.method == 'POST' and not csrf_valido():
            logger.warning('[qrobo] POST %s recusado: CSRF inválido/ausente.', request.path)
            if request.is_json:
                return jsonify({'status': 'csrf_invalido'}), 400
            flash('Formulário expirado ou inválido. Refaça a operação.', 'danger')
            return render_template('qrobo/bloqueado.html', csrf=True), 400
        return f(*args, **kwargs)
    return wrapper


@qrobo.context_processor
def _injeta_csrf():
    """Disponibiliza csrf_token() nos templates do portal."""
    return {'csrf_token': csrf_token}


# ---------------------------------------------------------------------------
# Acesso
# ---------------------------------------------------------------------------
def instalador_required(f):
    """Exige login + permissão do portal. Manda para o login DO PORTAL (não o do app)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('qrobo.login'))
        if not current_user.has_permission(PERMISSAO):
            logger.warning('[qrobo] usuário %s sem permissão %s tentou o portal.',
                           current_user.id, PERMISSAO)
            return render_template('qrobo/bloqueado.html', sem_permissao=True), 403
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------
@qrobo.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if not csrf_valido():
            flash('Formulário expirado. Tente novamente.', 'danger')
            return render_template('qrobo/login.html'), 400

        nome = (request.form.get('login') or '').strip()
        senha = request.form.get('password') or ''
        user = Usuario.get_by_login(nome) if nome else None

        if not user or not verify_password(user.senha_hash, senha):
            logger.info('[qrobo] login recusado para %r (credencial).', nome)
            flash('Login ou senha incorretos.', 'danger')
            return render_template('qrobo/login.html'), 401
        if not user.is_active():
            flash('Sua conta está desativada. Fale com o administrador.', 'warning')
            return render_template('qrobo/login.html'), 403
        if not user.has_permission(PERMISSAO):
            # Credencial correta, mas sem o perfil Instalador: NÃO abre sessão.
            logger.warning('[qrobo] %s (id=%s) autenticou mas não tem %s.',
                           nome, user.id, PERMISSAO)
            flash('Sua conta não tem acesso ao Portal do Instalador.', 'danger')
            return render_template('qrobo/login.html'), 403

        _abrir_sessao_instalador(user)
        logger.info('[qrobo] sessão de instalador aberta para %s (id=%s).', nome, user.id)
        return redirect(url_for('qrobo.index'))

    # GET — já logado e com permissão vai direto para o portal.
    if current_user.is_authenticated and current_user.has_permission(PERMISSAO):
        return redirect(url_for('qrobo.index'))
    return render_template('qrobo/login.html',
                           expirado=bool(request.args.get('expirado')))


@qrobo.route('/')
@instalador_required
def index():
    """Painel do instalador. Fase 2 entrega a casca; as ações vêm nas fases 3 e 4."""
    ocioso = segundos_ocioso() or 0
    return render_template(
        'qrobo/index.html',
        usuario=current_user,
        restam_min=max(0, (OCIOSO_SEGUNDOS - ocioso) // 60),
        escopo_restrito=_sessao_instalador(),
    )


@qrobo.route('/sair')
def sair():
    _encerrar_sessao()
    flash('Você saiu do Portal do Instalador.', 'info')
    return redirect(url_for('qrobo.login'))
