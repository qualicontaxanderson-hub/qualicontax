import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Configurações da aplicação"""
    
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
    FLASK_ENV = os.getenv('FLASK_ENV', 'development')
    DEBUG = FLASK_ENV == 'development'
    
    # Database Railway MySQL
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = int(os.getenv('DB_PORT', 3306))
    DB_NAME = os.getenv('DB_NAME', 'railway')
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '')
    # Pool por processo Gunicorn. 4 workers × pool_size = conexões MySQL simultâneas.
    # Padrão 10: 4 × 10 = 40 conexões — seguro para a maioria dos planos Railway.
    # Suba via env DB_POOL_SIZE se o plano MySQL permitir mais (max 32 pelo conector).
    DB_POOL_SIZE = max(1, min(32, int(os.getenv('DB_POOL_SIZE', 10))))
    DB_CONNECT_TIMEOUT = max(1, int(os.getenv('DB_CONNECT_TIMEOUT', 3)))
    
    # SQLAlchemy
    SQLALCHEMY_DATABASE_URI = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False  # nunca logar SQL em produção
    
    # Session
    SESSION_COOKIE_SECURE = FLASK_ENV == 'production'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = 3600  # 1 hora
    
    # Upload
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max
    ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'jpg', 'jpeg', 'png'}
    
    # Pagination
    ITEMS_PER_PAGE = 10

    # Dropbox — OAuth2 com refresh token (recomendado)
    DROPBOX_APP_KEY = os.getenv('DROPBOX_APP_KEY', '').strip()
    DROPBOX_APP_SECRET = os.getenv('DROPBOX_APP_SECRET', '').strip()
    DROPBOX_REDIRECT_URI = os.getenv('DROPBOX_REDIRECT_URI', '').strip()
    DROPBOX_REFRESH_TOKEN = os.getenv('DROPBOX_REFRESH_TOKEN', '').strip()
    # Legado — access token de longa duração (mantido para compatibilidade)
    DROPBOX_ACCESS_TOKEN = os.getenv('DROPBOX_ACCESS_TOKEN', '')
    DROPBOX_XML_FOLDER = os.getenv('DROPBOX_XML_FOLDER', '/qualicontax/xml-compras')
    # Prefixo de pasta raiz para tokens com acesso Full Dropbox.
    # Deixe em branco (padrão) para tokens com escopo de App Folder.
    # Exemplo: /Aplicativos/ESCRITA FISCAL
    DROPBOX_ROOT_FOLDER = os.getenv('DROPBOX_ROOT_FOLDER', '').strip().rstrip('/')

    # Token de autenticação para o script local fiscal_importar.py.
    # Opcional — se não configurado, o endpoint aceita apenas sessão Flask-Login.
    IMPORT_API_TOKEN = os.getenv('IMPORT_API_TOKEN', '')
