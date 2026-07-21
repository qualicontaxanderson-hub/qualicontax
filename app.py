"""Aplicação principal Flask - Qualicontax"""
import os
import time
import logging
from flask import Flask, render_template, jsonify
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
    """Carrega usuário para Flask-Login"""
    return Usuario.get_by_id(int(user_id))


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


# Template filters
from utils.formatters import format_cpf, format_cnpj, format_phone, format_currency, format_date

app.jinja_env.filters['format_cpf'] = format_cpf
app.jinja_env.filters['format_cnpj'] = format_cnpj
app.jinja_env.filters['format_phone'] = format_phone
app.jinja_env.filters['format_currency'] = format_currency
app.jinja_env.filters['format_date'] = format_date


# Cache de arquivos estáticos — 1 ano em produção para CSS/JS/imagens
if not Config.DEBUG:
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 365 * 24 * 60 * 60  # 1 ano em segundos


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


# ---------------------------------------------------------------------------
# TEMP diagnóstico de latência do banco — REMOVER após investigação.
# Separa o custo de handshake (acquire) do RTT de rede (SELECT 1 reusando a
# MESMA conexão) e mostra o host REAL em runtime + se há TLS na sessão.
# ---------------------------------------------------------------------------
@app.route('/db-diag')
def db_diag():
    import time as _t
    from utils.db_helper import get_db_connection

    info = {
        'db_host': Config.DB_HOST,
        'db_port': Config.DB_PORT,
        'db_name': Config.DB_NAME,
        'pool_size': Config.DB_POOL_SIZE,
    }

    # 1) Tempo de AQUISIÇÃO da conexão do pool (inclui handshake se for nova).
    t = _t.perf_counter()
    conn = get_db_connection()
    info['acquire_ms'] = round((_t.perf_counter() - t) * 1000, 1)
    if conn is None:
        info['error'] = 'sem conexão'
        return jsonify(info), 500

    try:
        cur = conn.cursor()
        # TLS em uso? (cipher vazio = conexão sem TLS)
        cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        row = cur.fetchone()
        info['ssl_cipher'] = (row[1] if row and len(row) > 1 else '') or '(nenhum)'
        # Host que o servidor enxerga (confirma para onde conectou de fato).
        cur.execute("SELECT @@hostname")
        h = cur.fetchone()
        info['server_hostname'] = h[0] if h else None
        # 2) RTT puro: 3x SELECT 1 REUSANDO a MESMA conexão.
        #    [360,360,360] => latência de rede real ao host.
        #    [360,2,2]     => 1o execute pagou algo; custo é por-conexão.
        times = []
        for _ in range(3):
            t = _t.perf_counter()
            cur.execute("SELECT 1")
            cur.fetchall()
            times.append(round((_t.perf_counter() - t) * 1000, 1))
        info['select1_ms_x3'] = times
        cur.close()
    finally:
        conn.close()

    logger.warning('DBDIAG %s', info)
    return jsonify(info)


# ---------------------------------------------------------------------------
# TEMP diagnóstico de REDE — REMOVER após investigação.
# (1) Como o mysql.railway.internal resolve: IPv6 privado (fd..) ou IPv4 público?
# (2) Custa TLS? Compara select1_ms_x3 com ssl_disabled=True vs False (conexão nova).
# ---------------------------------------------------------------------------
@app.route('/net-diag')
def net_diag():
    import socket, time as _t
    import mysql.connector

    host = Config.DB_HOST
    port = int(Config.DB_PORT)
    out = {'host': host, 'port': port}

    # 1) Resolução DNS: famílias e IPs retornados.
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addrs = []
        for fam, _type, _proto, _canon, sockaddr in infos:
            fam_name = ('IPv6' if fam == socket.AF_INET6
                        else 'IPv4' if fam == socket.AF_INET else str(fam))
            addrs.append({'family': fam_name, 'addr': sockaddr[0]})
        out['resolved'] = addrs
        out['has_ipv6'] = any(a['family'] == 'IPv6' for a in addrs)
        out['has_ipv4'] = any(a['family'] == 'IPv4' for a in addrs)
        # IPv6 privado do Railway começa com fd..; público começa com 2xxx.
        out['ipv6_privado'] = any(
            a['family'] == 'IPv6' and a['addr'].lower().startswith('fd')
            for a in addrs
        )
    except Exception as exc:
        out['resolve_error'] = str(exc)

    # 2) Benchmark com e sem TLS (conexão NOVA, 3x SELECT 1 reusando).
    def _bench(ssl_disabled):
        t = _t.perf_counter()
        conn = mysql.connector.connect(
            host=host, port=port, database=Config.DB_NAME,
            user=Config.DB_USER, password=Config.DB_PASSWORD,
            connection_timeout=Config.DB_CONNECT_TIMEOUT,
            ssl_disabled=ssl_disabled,
        )
        acquire = round((_t.perf_counter() - t) * 1000, 1)
        cur = conn.cursor()
        cur.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        r = cur.fetchone()
        cipher = (r[1] if r and len(r) > 1 else '') or '(nenhum)'
        times = []
        for _ in range(3):
            t = _t.perf_counter()
            cur.execute("SELECT 1")
            cur.fetchall()
            times.append(round((_t.perf_counter() - t) * 1000, 1))
        cur.close()
        conn.close()
        return {'acquire_ms': acquire, 'ssl_cipher': cipher, 'select1_ms_x3': times}

    try:
        out['sem_tls'] = _bench(ssl_disabled=True)
    except Exception as exc:
        out['sem_tls_error'] = str(exc)
    try:
        out['com_tls'] = _bench(ssl_disabled=False)
    except Exception as exc:
        out['com_tls_error'] = str(exc)

    logger.warning('NETDIAG %s', out)
    return jsonify(out)


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
        _logging.getLogger(__name__).exception('Falha ao executar migrations.')


# Inicia o scheduler de tarefas agendadas (importação automática às 23:59).
# A função init_scheduler usa um lock de arquivo para garantir que apenas um
# dos workers do gunicorn execute o scheduler.
try:
    from utils.scheduler import init_scheduler
    init_scheduler(app)
except Exception:
    import logging as _logging
    _logging.getLogger(__name__).exception('Falha ao iniciar o scheduler.')


if __name__ == '__main__':
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=5000)
