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
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from io import BytesIO

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, session, jsonify, send_file)
from flask_login import login_user, logout_user, current_user

from models.usuario import Usuario
from utils import qrobo_chaves, qrobo_instalador, qrobo_status
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


def _rotaciona_csrf():
    """Queima o token atual e emite outro.

    Usado depois de gerar uma chave: F5 na página do resultado reenviaria o
    POST e geraria uma chave NOVA, invalidando a que o instalador acabou de
    colar no robô. Com o token queimado, o reenvio morre em 400.
    """
    session[CHAVE_CSRF] = secrets.token_urlsafe(32)


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


def _hoje_brt():
    """Data de hoje em BRT (o processo roda em UTC no Railway — date.today()
    viraria 'amanhã' depois das 21h e o serviço recusaria como data futura)."""
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()


def _contexto_base():
    ocioso = segundos_ocioso() or 0
    return {'usuario': current_user,
            'restam_min': max(0, (OCIOSO_SEGUNDOS - ocioso) // 60),
            'escopo_restrito': _sessao_instalador()}


@qrobo.route('/')
@instalador_required
def index():
    """Painel do instalador: baixar o .exe, gerar chave e o histórico DELE."""
    return render_template(
        'qrobo/index.html',
        historico=qrobo_chaves.historico_usuario(current_user.id, limite=30),
        ultimo_download=qrobo_chaves.ultimo_download(current_user.id),
        instalador=qrobo_instalador.manifesto(),
        meus_postos=qrobo_status.meus_postos(current_user.id),
        **_contexto_base())


# --- Status ao vivo do posto (só operacional, nada fiscal) ----------------
@qrobo.route('/status/<int:cliente_id>')
@instalador_required
def status(cliente_id):
    """Sinal de vida do robô daquele posto: quando foi o último contato.

    Só responde por posto em que ESTE colaborador gerou chave — senão o portal
    viraria uma sonda para descobrir quem tem robô e há quanto tempo está
    parado. Devolve tempo e semáforo; nenhuma nota, valor ou contagem.
    """
    if not qrobo_status.pode_ver_status(current_user.id, cliente_id):
        logger.warning('[qrobo] %s pediu status do cliente_id=%s sem ter gerado chave.',
                       current_user.id, cliente_id)
        return jsonify({'status': 'fora_do_escopo'}), 403

    st = qrobo_status.status_posto(cliente_id)
    if not st:
        return jsonify({'status': 'sem_config', 'cliente_id': cliente_id}), 404
    return jsonify(st)


# --- Instalador: download autenticado e auditado --------------------------
@qrobo.route('/instalador')
@instalador_required
def instalador():
    """Serve o qualicontax.zip da pasta /Q-Robo da Dropbox.

    Nunca sai por /static (seria público e mataria a auditoria). O caminho vem
    SEMPRE do manifesto — nada do que o usuário manda vira caminho de arquivo.
    """
    m = qrobo_instalador.manifesto()
    if not m['ok']:
        logger.warning('[qrobo] download indisponível: %s | %s',
                       m.get('erro'), m.get('detalhe'))
        flash(f"{m['erro']} Avise o escritório — não dá para instalar agora.", 'danger')
        return redirect(url_for('qrobo.index'))

    dados = qrobo_instalador.baixar(m['caminho'])
    if not dados:
        flash('O instalador está na pasta, mas não consegui baixá-lo agora. '
              'Tente de novo em instantes.', 'danger')
        return redirect(url_for('qrobo.index'))

    # Audita ANTES de enviar: download interrompido no meio ainda é um download
    # entregue ao instalador. Melhor sobrar registro do que faltar.
    ip, ua = qrobo_chaves.contexto_request()
    qrobo_chaves.registrar_download(current_user.id, current_user.nome,
                                    versao=m['versao'],
                                    origem=qrobo_chaves.ORIGEM_PORTAL,
                                    ip=ip, user_agent=ua)
    logger.info('[qrobo] instalador %s (%s bytes, versao=%s) baixado por %s (id=%s)',
                m['nome'], len(dados), m['versao'], current_user.nome, current_user.id)
    return send_file(BytesIO(dados), as_attachment=True,
                     download_name=m['nome_download'],
                     mimetype='application/zip')


@qrobo.route('/manual')
@instalador_required
def manual():
    """Serve o PDF de instruções da mesma pasta /Q-Robo.

    Sem auditoria de propósito: manual é instrução, não é ação sobre o posto —
    a trilha existe para chave gerada e instalador baixado. Fica só a linha de
    log. Se não houver PDF na pasta, o portal nem mostra o botão; quem chegar
    aqui pela URL leva um aviso, não um erro.
    """
    m = (qrobo_instalador.manifesto() or {}).get('manual')
    if not m:
        flash('O manual ainda não está disponível na pasta do instalador.', 'warning')
        return redirect(url_for('qrobo.index'))

    dados = qrobo_instalador.baixar(m['caminho'])
    if not dados:
        flash('Não consegui baixar o manual agora. Tente de novo em instantes.', 'danger')
        return redirect(url_for('qrobo.index'))

    logger.info('[qrobo] manual %s (%s bytes) baixado por %s (id=%s)',
                m['nome'], len(dados), current_user.nome, current_user.id)
    return send_file(BytesIO(dados), as_attachment=True,
                     download_name=m['nome'], mimetype='application/pdf')


@qrobo.route('/instalador/status')
@instalador_required
def instalador_status():
    """Diagnóstico read-only: o que o app enxerga na pasta do Dropbox.

    Não devolve o arquivo — só nome, tamanho, data, content_hash e avisos. É por
    aqui que se confere o caminho em produção sem precisar baixar 11 MB.
    """
    m = qrobo_instalador.manifesto()
    m['pasta'] = qrobo_instalador.pasta()
    if m.get('modificado') is not None:
        m['modificado'] = str(m['modificado'])
    return jsonify(m), (200 if m['ok'] else 503)


# --- Passo 1: número -> confere a razão social (NÃO gera nada) -------------
_ERRO_RESOLVER = {
    'numero_vazio': 'Informe o número do cliente.',
    'nao_encontrado': 'Nenhum cliente com o número {n}. Confira no cadastro — '
                      'o número é exato ("023" não é o mesmo que "23").',
    'ambiguo': 'Existe mais de um cliente com o número {n}. Fale com o escritório '
               'antes de instalar — não gerei nada.',
}


@qrobo.route('/buscar')
@instalador_required
def buscar():
    """Busca de empresa por número, nome ou CNPJ — SÓ IDENTIDADE.

    Endpoint PRÓPRIO do portal, de propósito: o /api/clientes/search do app
    está fora da allowlist do escopo restrito (e devolve mais campos do que o
    instalador precisa ver). Aqui saem apenas número, razão social, CNPJ e
    situação, com teto de resultados e mínimo de caracteres.
    """
    termo = (request.args.get('q') or '').strip()
    achados = qrobo_chaves.buscar_clientes(termo)
    return jsonify({
        'termo': termo,
        'total': len(achados),
        'clientes': [{'cliente_id': c['cliente_id'],
                      'numero_cliente': c['numero_cliente'] or '',
                      'razao_social': c['razao_social'],
                      'cpf_cnpj': c['cpf_cnpj'] or '',
                      'situacao': c['situacao'],
                      'tem_robo': bool(c['tem_robo'])} for c in achados],
    })


@qrobo.route('/resolver', methods=['POST'])
@instalador_required
@exige_csrf
def resolver():
    """Mostra a razão social para conferência. NÃO gera nada.

    Aceita o número digitado OU o cliente_id escolhido na busca. Devolve SÓ a
    identidade do cliente da vez (número, razão social, CNPJ, situação) e o
    estado do robô dele. Nada de nota, valor ou movimento — o portal não tem
    por onde ver dado fiscal.
    """
    numero = (request.form.get('numero') or '').strip()
    cliente_id = (request.form.get('cliente_id') or '').strip()

    # Escolha na lista de busca não tem ambiguidade a resolver: a identidade
    # já veio cravada. Digitar o número mantém a trava do número exato.
    if cliente_id:
        res = qrobo_chaves.resolver_cliente_id(cliente_id)
    else:
        res = qrobo_chaves.resolver_numero(numero)

    if not res['ok']:
        logger.info('[qrobo] resolver %r/%r recusado: %s (user=%s)',
                    numero, cliente_id, res['erro'], current_user.id)
        flash(_ERRO_RESOLVER.get(res['erro'], 'Não foi possível localizar o cliente.')
              .format(n=numero), 'danger')
        return redirect(url_for('qrobo.index'))

    return render_template('qrobo/confirmar.html',
                           cliente=res['cliente'], robo=res['robo'],
                           hoje=_hoje_brt().isoformat(), **_contexto_base())


# --- Passo 2: confirmado -> gera (ou regera com dupla confirmação) ---------
_ERRO_GERAR = {
    'cliente_inexistente': 'Cliente não encontrado. Recomece pela busca.',
    'data_invalida': 'Data de início inválida. Use o seletor de data do campo.',
    'data_futura': 'A data de início não pode ser no futuro — o robô ignoraria '
                   'tudo que fosse emitido até lá.',
}


@qrobo.route('/gerar', methods=['POST'])
@instalador_required
@exige_csrf
def gerar():
    numero = (request.form.get('numero') or '').strip()
    cliente_id = (request.form.get('cliente_id') or '').strip()
    data_inicio = (request.form.get('data_inicio') or '').strip()
    confirmou = request.form.get('confirmar_regeracao') == '1'

    # Integridade: o cliente TEM que continuar sendo o que apareceu na tela de
    # conferência. Reconfere pelo id e exige que o número ainda bata com o que
    # foi mostrado — pega formulário adulterado e cadastro alterado no meio.
    res = qrobo_chaves.resolver_cliente_id(cliente_id)
    if not res['ok'] or (res['cliente']['numero_cliente'] or '') != numero:
        logger.warning('[qrobo] gerar abortado: cliente_id=%r não confere com numero=%r '
                       '(user=%s)', cliente_id, numero, current_user.id)
        flash('A conferência não bateu com o cadastro. Recomece a busca — '
              'nada foi gerado.', 'danger')
        return redirect(url_for('qrobo.index'))

    cliente, robo = res['cliente'], res['robo']
    ja_tem_chave = robo is not None

    # Dupla confirmação obrigatória para regerar: a chave atual morre e o robô
    # instalado para de enviar (401) até ser reconfigurado.
    if ja_tem_chave and not confirmou:
        flash('Este posto já tem chave. Marque a confirmação para substituir.', 'warning')
        return render_template('qrobo/confirmar.html', cliente=cliente, robo=robo,
                               hoje=_hoje_brt().isoformat(),
                               faltou_confirmar=True, **_contexto_base())

    ip, ua = qrobo_chaves.contexto_request()
    r = qrobo_chaves.gerar_chave(
        cliente['cliente_id'], current_user.id, current_user.nome,
        regerar=ja_tem_chave, data_inicio=data_inicio or None,
        origem=qrobo_chaves.ORIGEM_PORTAL, ip=ip, user_agent=ua)

    if not r['ok']:
        # 'ja_existe' aqui = alguém criou a config entre a conferência e o envio.
        if r['erro'] == 'ja_existe':
            flash('Este posto passou a ter uma chave agora há pouco. Confira e '
                  'confirme de novo se quiser substituir.', 'warning')
            return render_template('qrobo/confirmar.html', cliente=cliente,
                                   robo=r['robo'], hoje=_hoje_brt().isoformat(),
                                   **_contexto_base())
        logger.warning('[qrobo] gerar falhou para cliente_id=%s: %s',
                       cliente['cliente_id'], r['erro'])
        flash(_ERRO_GERAR.get(r['erro'], 'Não foi possível gerar a chave.'), 'danger')
        return render_template('qrobo/confirmar.html', cliente=cliente, robo=robo,
                               hoje=_hoje_brt().isoformat(), **_contexto_base())

    # Queima o token do formulário: F5 nesta página não gera outra chave.
    _rotaciona_csrf()
    logger.info('[qrobo] %s por %s (id=%s) para cliente_id=%s versao=%s',
                r['acao'], current_user.nome, current_user.id,
                cliente['cliente_id'], r['versao'])
    return render_template('qrobo/chave.html', resultado=r, cliente=cliente,
                           regerada=(r['acao'] == qrobo_chaves.ACAO_REGERADA),
                           st=qrobo_status.status_posto(cliente['cliente_id']),
                           **_contexto_base())


@qrobo.route('/sair')
def sair():
    _encerrar_sessao()
    flash('Você saiu do Portal do Instalador.', 'info')
    return redirect(url_for('qrobo.login'))
