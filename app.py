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
from routes.modulos import modulos
from routes.escrita_fiscal import escrita_fiscal as escrita_fiscal_bp

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
app.register_blueprint(modulos)
app.register_blueprint(escrita_fiscal_bp)


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

# Garante que as tabelas necessárias existem (cria se necessário)
from utils.db_helper import execute_query as _execute_query

# Tabelas do módulo Plano de Contas
_execute_query("""
    CREATE TABLE IF NOT EXISTS planos_contas (
        id INT AUTO_INCREMENT PRIMARY KEY,
        nome VARCHAR(255) NOT NULL,
        descricao TEXT NULL,
        grupo_id INT NULL,
        situacao ENUM('ATIVO', 'INATIVO') NOT NULL DEFAULT 'ATIVO',
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (grupo_id) REFERENCES grupos_clientes(id) ON DELETE SET NULL,
        INDEX idx_grupo (grupo_id),
        INDEX idx_situacao (situacao)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""", fetch=False)

_execute_query("""
    CREATE TABLE IF NOT EXISTS plano_contas_itens (
        id INT AUTO_INCREMENT PRIMARY KEY,
        plano_id INT NOT NULL,
        codigo VARCHAR(50) NOT NULL,
        descricao VARCHAR(255) NOT NULL,
        tipo ENUM('ANALITICA', 'SINTETICA') NOT NULL,
        natureza ENUM('DEVEDORA', 'CREDORA') NOT NULL,
        grupo_contabil ENUM('ATIVO', 'PASSIVO', 'PATRIMONIO_LIQUIDO', 'RECEITA', 'DESPESA') NOT NULL,
        situacao ENUM('ATIVO', 'INATIVO') NOT NULL DEFAULT 'ATIVO',
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (plano_id) REFERENCES planos_contas(id) ON DELETE CASCADE,
        INDEX idx_plano (plano_id),
        INDEX idx_codigo (codigo)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""", fetch=False)

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

# Tabela de Contas Correntes dos Clientes (Conciliação Bancária)
_execute_query("""
    CREATE TABLE IF NOT EXISTS contas_correntes (
        id INT AUTO_INCREMENT PRIMARY KEY,
        cliente_id INT NOT NULL,
        banco_nome VARCHAR(100) NOT NULL,
        banco_codigo VARCHAR(10) NOT NULL,
        agencia VARCHAR(20) NOT NULL,
        agencia_digito VARCHAR(2) DEFAULT '',
        numero_conta VARCHAR(30) NOT NULL,
        conta_digito VARCHAR(2) NOT NULL,
        tipo ENUM('CORRENTE', 'POUPANCA') NOT NULL DEFAULT 'CORRENTE',
        saldo DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
        ativa TINYINT(1) NOT NULL DEFAULT 1,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
        INDEX idx_cliente (cliente_id),
        INDEX idx_ativa (ativa)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""", fetch=False)

# Migração incremental: garantir colunas em contas_correntes caso a tabela
# já existia com schema antigo (sem alguns campos).
# Usa INFORMATION_SCHEMA para compatibilidade com todas as versões do MySQL.
_CONTAS_CORRENTES_COLS = [
    ("cliente_id",    "INT NULL"),
    ("banco_nome",    "VARCHAR(100) NOT NULL DEFAULT ''"),
    ("banco_codigo",  "VARCHAR(10) NOT NULL DEFAULT ''"),
    ("agencia",       "VARCHAR(20) NOT NULL DEFAULT ''"),
    ("agencia_digito","VARCHAR(2) DEFAULT ''"),
    ("numero_conta",  "VARCHAR(30) NOT NULL DEFAULT ''"),
    ("conta_digito",  "VARCHAR(2) NOT NULL DEFAULT ''"),
    ("tipo",          "ENUM('CORRENTE','POUPANCA') NOT NULL DEFAULT 'CORRENTE'"),
    ("saldo",         "DECIMAL(15,2) NOT NULL DEFAULT 0.00"),
    ("ativa",         "TINYINT(1) NOT NULL DEFAULT 1"),
    ("criado_em",     "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ("atualizado_em", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
]

for _col_name, _col_def in _CONTAS_CORRENTES_COLS:
    try:
        _exists = _execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'contas_correntes' "
            "AND COLUMN_NAME = %s",
            (_col_name,),
            fetch=True,
            fetch_one=True,
        )
        if _exists and _exists.get('cnt', 0) == 0:
            _execute_query(
                f"ALTER TABLE contas_correntes ADD COLUMN {_col_name} {_col_def}",
                fetch=False,
            )
    except Exception:
        pass


# Migração incremental: adicionar empresa_id em conciliacoes_bancarias
# para suportar o relatório de conferência de despesas por empresa.
try:
    _emp_col_exists = _execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'conciliacoes_bancarias' "
        "AND COLUMN_NAME = 'empresa_id'",
        fetch=True,
        fetch_one=True,
    )
    if _emp_col_exists and _emp_col_exists.get('cnt', 0) == 0:
        _execute_query(
            "ALTER TABLE conciliacoes_bancarias "
            "ADD COLUMN empresa_id INT NULL, "
            "ADD INDEX idx_empresa (empresa_id)",
            fetch=False,
        )
except Exception:
    pass


# ---- NF-e (Conferência de Compras) ----
_execute_query("""
    CREATE TABLE IF NOT EXISTS nfe_importacoes (
        id INT AUTO_INCREMENT PRIMARY KEY,
        cliente_id INT NULL,
        grupo_id INT NULL,
        nome_arquivo VARCHAR(500) NOT NULL,
        chave_acesso VARCHAR(44) UNIQUE NOT NULL,
        num_nota VARCHAR(20) DEFAULT '',
        serie VARCHAR(5) DEFAULT '',
        data_emissao DATE NULL,
        emit_cnpj VARCHAR(18) DEFAULT '',
        emit_nome VARCHAR(255) DEFAULT '',
        emit_uf VARCHAR(2) DEFAULT '',
        dest_cnpj VARCHAR(18) DEFAULT '',
        dest_nome VARCHAR(255) DEFAULT '',
        valor_total DECIMAL(15,2) NOT NULL DEFAULT 0.00,
        valor_icms DECIMAL(15,2) NOT NULL DEFAULT 0.00,
        valor_pis DECIMAL(15,2) NOT NULL DEFAULT 0.00,
        valor_cofins DECIMAL(15,2) NOT NULL DEFAULT 0.00,
        valor_ipi DECIMAL(15,2) NOT NULL DEFAULT 0.00,
        cfop VARCHAR(10) DEFAULT '',
        natureza_operacao VARCHAR(255) DEFAULT '',
        xml_raw MEDIUMTEXT,
        origem ENUM('UPLOAD','DROPBOX') NOT NULL DEFAULT 'UPLOAD',
        importado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_chave (chave_acesso),
        INDEX idx_emit_cnpj (emit_cnpj),
        INDEX idx_data (data_emissao),
        INDEX idx_dest_cnpj (dest_cnpj),
        INDEX idx_cliente (cliente_id),
        INDEX idx_grupo (grupo_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""", fetch=False)

# Incremental: add cliente_id / grupo_id columns if table was created before this migration
for _col, _defn in [('cliente_id', 'INT NULL'), ('grupo_id', 'INT NULL')]:
    _exists = _execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_importacoes' AND COLUMN_NAME = %s",
        (_col,), fetch=True, fetch_one=True,
    ) or {}
    if _exists.get('cnt', 0) == 0:
        _execute_query(f"ALTER TABLE nfe_importacoes ADD COLUMN {_col} {_defn}", fetch=False)

_execute_query("""
    CREATE TABLE IF NOT EXISTS nfe_itens (
        id INT AUTO_INCREMENT PRIMARY KEY,
        nfe_id INT NOT NULL,
        num_item INT NOT NULL DEFAULT 1,
        codigo_produto VARCHAR(60) DEFAULT '',
        descricao VARCHAR(255) DEFAULT '',
        ncm VARCHAR(8) DEFAULT '',
        cfop VARCHAR(4) DEFAULT '',
        unidade VARCHAR(6) DEFAULT '',
        quantidade DECIMAL(15,4) NOT NULL DEFAULT 0.0000,
        valor_unitario DECIMAL(15,4) NOT NULL DEFAULT 0.0000,
        valor_total DECIMAL(15,2) NOT NULL DEFAULT 0.00,
        valor_icms DECIMAL(15,2) NOT NULL DEFAULT 0.00,
        valor_pis DECIMAL(15,2) NOT NULL DEFAULT 0.00,
        valor_cofins DECIMAL(15,2) NOT NULL DEFAULT 0.00,
        produto_catalogo_id INT NULL,
        FOREIGN KEY (nfe_id) REFERENCES nfe_importacoes(id) ON DELETE CASCADE,
        INDEX idx_nfe (nfe_id),
        INDEX idx_ncm (ncm),
        INDEX idx_produto (codigo_produto(20))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""", fetch=False)

# Incremental: add produto_catalogo_id to nfe_itens
_pcat_exists = _execute_query(
    "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_itens' AND COLUMN_NAME = 'produto_catalogo_id'",
    fetch=True, fetch_one=True,
) or {}
if _pcat_exists.get('cnt', 0) == 0:
    _execute_query("ALTER TABLE nfe_itens ADD COLUMN produto_catalogo_id INT NULL", fetch=False)

# ---- Catálogo de Produtos (por empresa/grupo) ----
_execute_query("""
    CREATE TABLE IF NOT EXISTS nfe_produtos_catalogo (
        id INT AUTO_INCREMENT PRIMARY KEY,
        cliente_id INT NULL,
        grupo_id INT NULL,
        codigo VARCHAR(60) DEFAULT '',
        nome VARCHAR(255) NOT NULL,
        categoria VARCHAR(100) DEFAULT '',
        subcategoria VARCHAR(100) DEFAULT '',
        unidade VARCHAR(6) DEFAULT '',
        ativo TINYINT(1) NOT NULL DEFAULT 1,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_cli (cliente_id),
        INDEX idx_grp (grupo_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""", fetch=False)

# ---- Regras de vínculo automático (emit_cnpj + cod_xml → produto_catalogo) ----
_execute_query("""
    CREATE TABLE IF NOT EXISTS nfe_produto_vinculo (
        id INT AUTO_INCREMENT PRIMARY KEY,
        cliente_id INT NULL,
        grupo_id INT NULL,
        emit_cnpj VARCHAR(18) NOT NULL DEFAULT '',
        codigo_produto_xml VARCHAR(60) NOT NULL DEFAULT '',
        produto_catalogo_id INT NOT NULL,
        criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_vinculo (cliente_id, grupo_id, emit_cnpj, codigo_produto_xml),
        INDEX idx_lookup (emit_cnpj, codigo_produto_xml)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""", fetch=False)

# Incremental: add ramo_atividade_id and descricao_produto_xml to nfe_produto_vinculo
for _col_name, _col_def in [
    ('ramo_atividade_id', 'INT NULL AFTER grupo_id'),
    ('descricao_produto_xml', 'VARCHAR(500) NULL AFTER codigo_produto_xml'),
]:
    _col_exists = _execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_produto_vinculo' AND COLUMN_NAME = %s",
        (_col_name,), fetch=True, fetch_one=True,
    ) or {}
    if _col_exists.get('cnt', 0) == 0:
        _execute_query(
            f"ALTER TABLE nfe_produto_vinculo ADD COLUMN {_col_name} {_col_def}",
            fetch=False,
        )


if __name__ == '__main__':
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=5000)
