"""Aplicação principal Flask - Qualicontax"""
import os
import sys
import time
import logging

# ---------------------------------------------------------------------------
# Logging — PRIMEIRA COISA do boot, antes de qualquer import que já emita log.
# O gunicorn configura apenas os loggers 'gunicorn.*' (com propagate=False);
# o root fica sem handler e TODO logger.info/warning da aplicação era descartado
# silenciosamente. force=True garante que esta config vença independente da
# ordem de import (não mexe nos handlers do gunicorn, que são loggers nomeados).
# Nível ajustável em runtime pela env LOG_LEVEL (default INFO).
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO),
    stream=sys.stdout,
    format='%(asctime)s %(levelname)s [pid=%(process)d] %(name)s: %(message)s',
    force=True,
)
try:
    # Railway captura stdout; sem line-buffering os logs de boot podem ficar
    # presos no buffer e só aparecerem muito depois (ou nunca, se o worker morrer).
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from flask import Flask, render_template, jsonify, url_for as flask_url_for
from flask_login import LoginManager
from flask_compress import Compress
from config import Config
from models.usuario import Usuario

_startup_time = time.time()
logger = logging.getLogger(__name__)

_INSECURE_KEY = 'dev-secret-key-change-in-production'
if Config.FLASK_ENV == 'production' and Config.SECRET_KEY == _INSECURE_KEY:
    raise RuntimeError(
        'SECRET_KEY não configurada. '
        'Defina a variável de ambiente SECRET_KEY no Railway antes de iniciar em produção.'
    )

# Inicializa Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Compressão gzip de respostas (reduz tráfego ~70% para HTML/CSS/JSON)
compress = Compress()
compress.init_app(app)

# Inicializa Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Por favor, faça login para acessar esta página.'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    """Carrega usuário para Flask-Login.

    Conta com senha PENDENTE (nunca definida, ou redefinição com bloqueio) não
    sustenta sessão: devolver None faz o current_user cair já na PRÓXIMA
    requisição — quem estava logado é derrubado na hora que o admin bloqueia, e
    cai no /login (onde a rota mostra 'falta definir a senha'). É o que impede a
    pessoa de continuar navegando após um bloqueio.
    """
    u = Usuario.get_by_id(int(user_id))
    if u and u.senha_pendente:
        return None
    return u


# Registra Blueprints
from routes.auth import auth
from routes.dashboard import dashboard
from routes.clientes import clientes
from routes.grupos import grupos
from routes.ramos_atividade import ramos_atividade
from routes.contratos import contratos
from routes.processos import processos
from routes.relatorios import relatorios
from routes.documentos import documentos
from routes.api import api
from routes.contabil import contabil
from routes.municipios import municipios
from routes.financeiro import financeiro
from routes.dropbox import dropbox_bp
from routes.modulos import modulos
from routes.escrita_fiscal import escrita_fiscal as escrita_fiscal_bp
from routes.configuracoes import configuracoes as configuracoes_bp
from routes.adicionais import adicionais
from routes.dfe import dfe_bp
from routes.robo_saidas import robo_saidas
from routes.qrobo import qrobo, gate_escopo_instalador
# Q-Colabore: formulário PÚBLICO de cadastro (/cadastro/<token>). Sem
# login_required — quem chega não tem conta; a credencial é o token da URL.
from routes.cadastro import cadastro_bp
# Q-Colabore Parte 6: definição PÚBLICA de senha (/senha/<token>). Também sem
# login_required — a credencial é o token; destrava a conta senha_pendente.
from routes.senha import senha_bp

app.register_blueprint(auth)
app.register_blueprint(dashboard)
app.register_blueprint(clientes)
app.register_blueprint(grupos)
app.register_blueprint(ramos_atividade)
app.register_blueprint(contratos)
app.register_blueprint(processos)
app.register_blueprint(relatorios)
app.register_blueprint(documentos)
app.register_blueprint(api)
app.register_blueprint(contabil)
app.register_blueprint(municipios)
app.register_blueprint(financeiro)
app.register_blueprint(dropbox_bp)
app.register_blueprint(modulos)
app.register_blueprint(escrita_fiscal_bp)
app.register_blueprint(configuracoes_bp)
app.register_blueprint(adicionais)
app.register_blueprint(dfe_bp)
app.register_blueprint(robo_saidas)
app.register_blueprint(cadastro_bp)
app.register_blueprint(senha_bp)
app.register_blueprint(qrobo)

# Gate do Portal do Instalador — DENY BY DEFAULT para a sessão de escopo
# restrito. Registrado no APP (não no blueprint) de propósito: precisa rodar
# antes de TODO request, senão bastaria digitar /escrita-fiscal/... na barra
# para escapar. Sessão sem o escopo não é tocada — o app segue igual para todos.
app.before_request(gate_escopo_instalador)


# Template filters
from utils.formatters import format_cpf, format_cnpj, format_phone, format_currency, format_date

app.jinja_env.filters['format_cpf'] = format_cpf
app.jinja_env.filters['format_cnpj'] = format_cnpj
app.jinja_env.filters['format_phone'] = format_phone
app.jinja_env.filters['format_currency'] = format_currency
app.jinja_env.filters['format_date'] = format_date


# Cache de arquivos estáticos — 1 ano em produção para CSS/JS/imagens.
# Só é seguro por causa do cache-busting logo abaixo: a URL carrega a versão do
# arquivo, então "1 ano" vale para o conteúdo daquela versão, não para o caminho.
if not Config.DEBUG:
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 365 * 24 * 60 * 60  # 1 ano em segundos


# ---------------------------------------------------------------------------
# CACHE-BUSTING DOS ESTÁTICOS
#
# Por que existe: os estáticos são servidos com max-age de 1 ano e o navegador
# NÃO revalida nesse período. Sem versão na URL, um deploy que muda CSS/JS não
# chega em quem já visitou o site — foi assim que a home do fiscal quebrou
# depois que o CSS dela saiu do template para o identidade.css (revert 7d919b5).
#
# Como funciona: sobrescreve o url_for QUE OS TEMPLATES ENXERGAM. Toda chamada
# url_for('static', filename=...) ganha ?v=<mtime do arquivo>, no sistema
# inteiro de uma vez — não há nada para lembrar de atualizar arquivo por
# arquivo. Arquivo mudou → mtime muda → URL muda → o navegador é obrigado a
# baixar. Arquivo igual → URL igual → cache de 1 ano aproveitado por inteiro.
#
# O valor NÃO é escrito à mão de propósito: número manual é exatamente o que se
# esquece de atualizar, e aí o problema volta calado.
# ---------------------------------------------------------------------------
def _versao_estatico(filename):
    """mtime do arquivo em static/, como string. None se o arquivo não existir."""
    try:
        caminho = os.path.join(app.static_folder, filename)
        return str(int(os.stat(caminho).st_mtime))
    except (OSError, TypeError, ValueError):
        # Arquivo ausente ou nome estranho: devolve a URL sem ?v= em vez de
        # quebrar a página. Perde-se o cache-busting daquele arquivo, só isso.
        return None


def url_for_versionado(endpoint, **values):
    if endpoint == 'static' and 'v' not in values:
        versao = _versao_estatico(values.get('filename'))
        if versao:
            values['v'] = versao
    return flask_url_for(endpoint, **values)


@app.context_processor
def _injeta_url_for_versionado():
    """Faz os templates usarem a versão com ?v= no lugar do url_for do Flask."""
    return {'url_for': url_for_versionado}


# ---------------------------------------------------------------------------
# Health check — sem autenticação, usado pelo Railway para zero-downtime deploy.
# Verifica conectividade com o banco antes de sinalizar "pronto".
# ---------------------------------------------------------------------------
@app.route('/health')
def health():
    from utils.db_helper import execute_query
    try:
        execute_query("SELECT 1", fetch=True)
        db_ok = True
        db_msg = 'ok'
    except Exception as exc:
        db_ok = False
        db_msg = str(exc)[:120]
        logger.warning('Health check: DB indisponível — %s', db_msg)

    uptime = round(time.time() - _startup_time, 1)
    status = 'ok' if db_ok else 'degraded'
    code   = 200  if db_ok else 503
    return jsonify({'status': status, 'db': db_msg, 'uptime_s': uptime}), code


# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    """Página de erro 404"""
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    """Página de erro 500"""
    return render_template('errors/500.html'), 500


# Cria diretórios necessários
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join('static', 'uploads'), exist_ok=True)

# Executa as migrations do banco de dados uma única vez por processo.
# Em ambiente Gunicorn com --preload, roda apenas no processo mestre antes
# de fazer fork dos workers. Sem --preload, cada worker executa uma vez
# ao iniciar, mas a variável de ambiente evita re-execução dentro do mesmo processo.
if os.environ.get('MIGRATIONS_DONE') != '1':
    try:
        from init_db import run_migrations as _run_migrations
        _run_migrations()
        os.environ['MIGRATIONS_DONE'] = '1'
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).exception(
            'Falha FATAL nas migrations — abortando o boot (schema incompleto).')
        # Re-levanta: melhor o worker morrer e o deploy falhar RUIDOSAMENTE do que
        # subir "com sucesso" com schema incompleto (lição da Fase 1).
        raise


# Inicia o scheduler de tarefas agendadas.
#
# Roda in-process no worker do gunicorn que adquire o file-lock. Cobre o job
# NOTURNO (importação automática 1x/dia). A captura de DFe (*/20) NÃO depende mais
# deste scheduler: ela é disparada pelo Railway Cron (cron_captura_dfe.py). Por
# isso o job de DFe in-process fica DESLIGADO no serviço web via DFE_SCHED_ATIVO=0
# (evita disparo duplo com o Cron).
try:
    from utils.scheduler import init_scheduler
    init_scheduler(app)
except Exception:
    import logging as _logging
    _logging.getLogger(__name__).exception('Falha ao iniciar o scheduler.')


if __name__ == '__main__':
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=5000)
