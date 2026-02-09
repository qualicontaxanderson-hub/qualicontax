"""Rotas de autenticação (login/logout)"""
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, current_user
from models.usuario import Usuario
from utils.auth_helper import verify_password

auth = Blueprint('auth', __name__)


@auth.route('/login', methods=['GET', 'POST'])
def login():
    """Página de login"""
    # Se já está autenticado, redireciona para dashboard
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        remember = request.form.get('remember', False)
        
        # Validações
        if not email or not password:
            flash('Por favor, preencha todos os campos.', 'danger')
            return render_template('login.html')
        
        # Busca usuário
        user = Usuario.get_by_email(email)
        
        if user and verify_password(user.senha_hash, password):
            if not user.is_active():
                flash('Sua conta está desativada. Entre em contato com o administrador.', 'warning')
                return render_template('login.html')
            
            # Faz login
            login_user(user, remember=remember)
            flash(f'Bem-vindo(a), {user.nome}!', 'success')
            
            # Redireciona para página solicitada ou dashboard
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard.index'))
        else:
            flash('Email ou senha incorretos.', 'danger')
    
    return render_template('login.html')


@auth.route('/logout')
def logout():
    """Faz logout do usuário"""
    logout_user()
    flash('Você saiu da sua conta.', 'info')
    return redirect(url_for('auth.login'))
