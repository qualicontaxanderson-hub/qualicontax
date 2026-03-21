"""Aplicação principal Flask - Qualicontax"""
from flask import Flask, render_template
from flask_login import LoginManager
from config import Config
from models.usuario import Usuario
import os

# Inicializa Flask app
app = Flask(__name__)
app.config.from_object(Config)

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


# Template filters
from utils.formatters import format_cpf, format_cnpj, format_phone, format_currency, format_date

app.jinja_env.filters['format_cpf'] = format_cpf
app.jinja_env.filters['format_cnpj'] = format_cnpj
app.jinja_env.filters['format_phone'] = format_phone
app.jinja_env.filters['format_currency'] = format_currency
app.jinja_env.filters['format_date'] = format_date


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

# Garante que a tabela de municípios existe (cria se necessário)
from utils.db_helper import execute_query as _execute_query
_execute_query("""
    CREATE TABLE IF NOT EXISTS municipios (
        id INT AUTO_INCREMENT PRIMARY KEY,
        nome VARCHAR(100) NOT NULL,
        uf CHAR(2) NOT NULL,
        site_prefeitura VARCHAR(500) DEFAULT NULL,
        situacao ENUM('ATIVO', 'INATIVO') NOT NULL DEFAULT 'ATIVO',
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_municipio_uf_nome (uf, nome)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""", fetch=False)


if __name__ == '__main__':
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=5000)
