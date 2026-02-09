"""Módulo de autenticação e helpers de segurança"""
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from flask import redirect, url_for, flash
from flask_login import current_user


def hash_password(password):
    """
    Gera hash de senha usando Werkzeug.
    
    Args:
        password (str): Senha em texto plano
        
    Returns:
        str: Hash da senha
    """
    return generate_password_hash(password, method='pbkdf2:sha256')


def verify_password(password_hash, password):
    """
    Verifica se a senha corresponde ao hash.
    
    Args:
        password_hash (str): Hash armazenado
        password (str): Senha fornecida
        
    Returns:
        bool: True se a senha corresponde
    """
    return check_password_hash(password_hash, password)


def login_required(f):
    """
    Decorator para proteger rotas que requerem autenticação.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """
    Decorator para proteger rotas que requerem privilégios de admin.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('auth.login'))
        if not current_user.is_admin():
            flash('Você não tem permissão para acessar esta página.', 'danger')
            return redirect(url_for('dashboard.index'))
        return f(*args, **kwargs)
    return decorated_function
