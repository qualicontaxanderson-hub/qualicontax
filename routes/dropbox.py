"""Rotas de integração com Dropbox - OAuth2 e importação de arquivos"""
from flask import Blueprint, request, redirect, jsonify
from flask_login import login_required
import requests
import os

dropbox_bp = Blueprint('dropbox', __name__)

DROPBOX_TOKEN_URL = 'https://api.dropboxapi.com/oauth2/token'


@dropbox_bp.route('/dropbox/callback')
def dropbox_callback():
    """Callback OAuth2 do Dropbox - troca code por refresh_token"""
    code = request.args.get('code')
    error = request.args.get('error')

    if error:
        return f'<h2>Erro na autorização Dropbox: {error}</h2>', 400

    if not code:
        return '<h2>Código de autorização não recebido.</h2>', 400

    app_key = os.environ.get('DROPBOX_APP_KEY', '').strip()
    app_secret = os.environ.get('DROPBOX_APP_SECRET', '').strip()
    redirect_uri = os.environ.get('DROPBOX_REDIRECT_URI', '').strip()

    response = requests.post(DROPBOX_TOKEN_URL, data={
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': redirect_uri,
    }, auth=(app_key, app_secret))

    if response.status_code != 200:
        return f'<h2>Erro ao obter token: {response.text}</h2>', 400

    data = response.json()
    refresh_token = data.get('refresh_token', '')
    account_id = data.get('account_id', '')
    scope = data.get('scope', '(não retornado)')

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
    <p>Account ID: <strong>{account_id}</strong></p>
    <p>Scopes concedidos: <code>{scope}</code></p>
    <div class="box">
        <strong>DROPBOX_REFRESH_TOKEN (copie e salve no Railway):</strong><br><br>
        <div class="token">{refresh_token}</div>
    </div>
    <div class="warn">
        &#x26A0;&#xFE0F; Copie o token acima, acesse Railway &rarr; qualicontax &rarr; Variables e adicione:<br><br>
        <code>DROPBOX_REFRESH_TOKEN = {refresh_token}</code>
    </div>
    </body>
    </html>
    '''


@dropbox_bp.route('/dropbox/auth-url')
@login_required
def dropbox_auth_url():
    """Redireciona para a URL de autorização OAuth do Dropbox"""
    app_key = os.environ.get('DROPBOX_APP_KEY', '').strip()
    redirect_uri = os.environ.get('DROPBOX_REDIRECT_URI', '').strip()
    url = (
        f'https://www.dropbox.com/oauth2/authorize'
        f'?client_id={app_key}'
        f'&token_access_type=offline'
        f'&response_type=code'
        f'&redirect_uri={redirect_uri}'
        f'&scope=files.metadata.read+files.content.read+files.content.write+files.metadata.write'
    )
    return redirect(url)


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
