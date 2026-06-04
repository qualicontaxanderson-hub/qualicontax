"""Rotas de integração com Dropbox - OAuth2 e importação de arquivos"""
from flask import Blueprint, request, redirect, jsonify, session
from flask_login import login_required
import requests
import os
import secrets
from urllib.parse import urlencode
import logging
from markupsafe import escape

dropbox_bp = Blueprint('dropbox', __name__)
logger = logging.getLogger(__name__)

DROPBOX_TOKEN_URL = 'https://api.dropboxapi.com/oauth2/token'
DROPBOX_AUTHORIZE_URL = 'https://www.dropbox.com/oauth2/authorize'
_DROPBOX_STATE_SESSION_KEY = 'dropbox_oauth_state'
_RAILWAY_ENDPOINTS = (
    'https://backboard.railway.com/graphql/v2',
    'https://backboard.railway.com/graphql',
)


def _dropbox_env(name: str, default: str = '') -> str:
    """Lê variável de ambiente Dropbox dinamicamente (sem cache estático)."""
    return (os.getenv(name, default) or default).strip()


def _railway_env(name: str, default: str = '') -> str:
    return (os.getenv(name, default) or default).strip()


def _railway_upsert_variable(name: str, value: str) -> tuple[bool, str]:
    """Salva/atualiza variável no Railway via GraphQL API."""
    api_token = _railway_env('RAILWAY_API_TOKEN') or _railway_env('RAILWAY_TOKEN')
    project_id = _railway_env('RAILWAY_PROJECT_ID')
    environment_id = _railway_env('RAILWAY_ENVIRONMENT_ID')
    service_id = _railway_env('RAILWAY_SERVICE_ID')

    if not api_token:
        return False, 'RAILWAY_API_TOKEN (ou RAILWAY_TOKEN) não configurado'
    if not project_id or not environment_id:
        return False, 'RAILWAY_PROJECT_ID e RAILWAY_ENVIRONMENT_ID são obrigatórios'

    # Valores "all/*/null" indicam variável no nível do ambiente (sem serviceId).
    if service_id.lower() in {'', 'null', 'none', 'all', '*'}:
        service_id = None

    mutation = """
    mutation UpsertVar($input: VariableUpsertInput!) {
      variableUpsert(input: $input) {
        id
        name
      }
    }
    """
    variables = {
        'input': {
            'projectId': project_id,
            'environmentId': environment_id,
            'serviceId': service_id,
            'name': name,
            'value': value,
        }
    }
    bearer = 'Bearer ' + api_token
    headers = {
        'Authorization': bearer,
        'Content-Type': 'application/json',
    }

    last_error = 'falha desconhecida ao comunicar com Railway'
    for endpoint in _RAILWAY_ENDPOINTS:
        try:
            response = requests.post(
                endpoint,
                json={'query': mutation, 'variables': variables},
                headers=headers,
                timeout=25,
            )
            if response.status_code != 200:
                logger.warning(
                    'Railway endpoint %s retornou %s: %s',
                    endpoint,
                    response.status_code,
                    response.text[:1000],
                )
                last_error = f'HTTP {response.status_code}'
                continue
            payload = response.json()
            errors = payload.get('errors')
            if errors:
                last_error = '; '.join(e.get('message', str(e)) for e in errors)
                continue
            data = payload.get('data', {}) or {}
            if data.get('variableUpsert'):
                return True, endpoint
            last_error = f'resposta inválida: {payload}'
        except Exception as exc:
            last_error = str(exc)
            continue

    return False, last_error


def _build_dropbox_authorize_url() -> str:
    app_key = _dropbox_env('DROPBOX_APP_KEY')
    redirect_uri = _dropbox_env('DROPBOX_REDIRECT_URI')
    state = secrets.token_urlsafe(32)
    session[_DROPBOX_STATE_SESSION_KEY] = state
    query = urlencode({
        'client_id': app_key,
        'token_access_type': 'offline',
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'state': state,
        'scope': 'files.metadata.read files.content.read files.content.write files.metadata.write',
    })
    return f'{DROPBOX_AUTHORIZE_URL}?{query}'


@dropbox_bp.route('/dropbox/auth')
@login_required
def dropbox_auth():
    """Redireciona para autorização OAuth do Dropbox (offline refresh token)."""
    app_key = _dropbox_env('DROPBOX_APP_KEY')
    redirect_uri = _dropbox_env('DROPBOX_REDIRECT_URI')
    if not app_key or not redirect_uri:
        return jsonify({
            'error': 'DROPBOX_APP_KEY e DROPBOX_REDIRECT_URI precisam estar configurados.'
        }), 400
    return redirect(_build_dropbox_authorize_url())


@dropbox_bp.route('/dropbox/auth-url')
@login_required
def dropbox_auth_url():
    """Compatibilidade com rota antiga de autorização."""
    return dropbox_auth()


@dropbox_bp.route('/dropbox/callback')
@login_required
def dropbox_callback():
    """Callback OAuth2 do Dropbox: troca code por refresh_token e salva no Railway."""
    code = request.args.get('code')
    error = request.args.get('error')
    state = request.args.get('state', '')

    if error:
        logger.warning('Dropbox OAuth retornou erro no callback: %s', error)
        return '<h2>Erro na autorização Dropbox.</h2>', 400

    if not code:
        return '<h2>Código de autorização não recebido.</h2>', 400

    expected_state = session.pop(_DROPBOX_STATE_SESSION_KEY, None)
    if not expected_state or not state or not secrets.compare_digest(state, expected_state):
        return '<h2>Estado OAuth inválido. Refaça a autenticação.</h2>', 400

    app_key = _dropbox_env('DROPBOX_APP_KEY')
    app_secret = _dropbox_env('DROPBOX_APP_SECRET')
    redirect_uri = _dropbox_env('DROPBOX_REDIRECT_URI')
    if not app_key or not app_secret or not redirect_uri:
        return '<h2>Configuração incompleta: DROPBOX_APP_KEY, DROPBOX_APP_SECRET e DROPBOX_REDIRECT_URI são obrigatórios.</h2>', 400

    response = requests.post(DROPBOX_TOKEN_URL, data={
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri,
    }, auth=(app_key, app_secret))

    if response.status_code != 200:
        logger.warning('Falha ao obter token Dropbox (HTTP %s): %s',
                       response.status_code, response.text[:500])
        return '<h2>Erro ao obter token do Dropbox. Tente novamente.</h2>', 400

    data = response.json()
    refresh_token = data.get('refresh_token', '')
    account_id = data.get('account_id', '')
    scope = data.get('scope', '(não retornado)')
    if not refresh_token:
        logger.warning('Dropbox callback sem refresh_token. Resposta: %s', data)
        return '<h2>Dropbox não retornou refresh token. Revise as permissões do app.</h2>', 400

    ok, save_status = _railway_upsert_variable('DROPBOX_REFRESH_TOKEN', refresh_token)
    if not ok:
        logger.warning('Falha ao salvar DROPBOX_REFRESH_TOKEN no Railway: %s', save_status)
    os.environ['DROPBOX_REFRESH_TOKEN'] = refresh_token
    try:
        from utils.dropbox_sync import _service
        _service._dbx = None
    except Exception:
        logger.exception('Falha ao resetar cliente Dropbox após atualizar refresh token.')

    railway_msg = (
        '<p><strong>Railway:</strong> ✅ DROPBOX_REFRESH_TOKEN atualizado com sucesso.</p>'
        if ok
        else (
            '<p><strong>Railway:</strong> ⚠️ não foi possível salvar automaticamente.</p>'
            '<p>Configure RAILWAY_API_TOKEN, RAILWAY_PROJECT_ID e RAILWAY_ENVIRONMENT_ID '
            'para habilitar o salvamento automático.</p>'
        )
    )
    token_len = len(refresh_token)
    if token_len > 14:
        masked = f'{refresh_token[:8]}...{refresh_token[-6:]}'
    else:
        masked = '*' * token_len
    account_id_safe = escape(account_id)
    scope_safe = escape(scope)
    masked_safe = escape(masked)

    return f'''
    <html>
    <head><title>Dropbox Autorizado</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 700px; margin: 60px auto; padding: 20px; }}
        .box {{ background: #f0f9f0; border: 2px solid #28a745; border-radius: 8px; padding: 20px; margin: 20px 0; }}
        .token {{ background: #fff; border: 1px solid #ccc; border-radius: 4px; padding: 12px;
                  font-family: monospace; font-size: 13px; word-break: break-all; }}
        h2 {{ color: #28a745; }}
        .warn {{ background: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; padding: 12px; margin-top: 16px; }}
    </style>
    </head>
    <body>
    <h2>&#x2705; Dropbox Autorizado com Sucesso!</h2>
    <p>Account ID: <strong>{account_id_safe}</strong></p>
    <p>Scopes concedidos: <code>{scope_safe}</code></p>
    {railway_msg}
    <div class="box">
        <strong>DROPBOX_REFRESH_TOKEN gerado:</strong><br><br>
        <div class="token">{masked_safe}</div>
    </div>
    <div class="warn">&#x26A0;&#xFE0F; Se o salvamento automático no Railway falhar, adicione manualmente a variável <code>DROPBOX_REFRESH_TOKEN</code>.</div>
    </body>
    </html>
    '''


@dropbox_bp.route('/dropbox/test')
@login_required
def dropbox_test():
    """Testa a conexão com o Dropbox e retorna diagnóstico detalhado."""
    from utils.dropbox_sync import _service
    app_key = os.environ.get('DROPBOX_APP_KEY', '').strip()
    app_secret = os.environ.get('DROPBOX_APP_SECRET', '').strip()
    refresh_token = os.environ.get('DROPBOX_REFRESH_TOKEN', '').strip()

    diag = {
        'DROPBOX_APP_KEY': 'CONFIGURADO' if app_key else 'NÃO CONFIGURADO',
        'DROPBOX_APP_SECRET': 'CONFIGURADO' if app_secret else 'NÃO CONFIGURADO',
        'DROPBOX_REFRESH_TOKEN': f'CONFIGURADO ({len(refresh_token)} chars)' if refresh_token else 'NÃO CONFIGURADO',
    }

    try:
        _service._dbx = None  # Força nova conexão
        dbx = _service._client()
        if not dbx:
            diag['resultado'] = 'FALHA: cliente Dropbox não pôde ser criado (variáveis não configuradas?)'
            return jsonify(diag), 400
        account = dbx.users_get_current_account()
        diag['resultado'] = 'OK'
        diag['conta'] = account.name.display_name
        diag['email'] = account.email
        return jsonify(diag), 200
    except Exception as exc:
        diag['resultado'] = 'ERRO'
        diag['erro_tipo'] = type(exc).__name__
        diag['erro_detalhe'] = str(exc)
        return jsonify(diag), 401
