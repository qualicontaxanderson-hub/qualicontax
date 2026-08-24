"""Rotas de autenticação (login/logout)"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, current_user
from models.usuario import Usuario
from utils.auth_helper import verify_password
from utils.atividade import registrar, registrar_agente

auth = Blueprint('auth', __name__)


@auth.route('/login', methods=['GET', 'POST'])
def login():
    """Página de login"""
    # Se já está autenticado, redireciona para dashboard
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    
    if request.method == 'POST':
        login_name = request.form.get('login', '').strip()
        password = request.form.get('password')
        remember = request.form.get('remember', False)
        
        # Validações
        if not login_name or not password:
            flash('Por favor, preencha todos os campos.', 'danger')
            return render_template('login.html')
        
        # Busca usuário pelo login
        user = Usuario.get_by_login(login_name)

        # Conta aprovada mas ainda sem senha (Q-Colabore Parte 5): o acesso existe,
        # só falta a pessoa criar a senha (Parte 6). Mensagem própria — dizer
        # "senha incorreta" aqui seria mentira e deixaria a pessoa tentando à toa.
        # Checado ANTES de verify_password: com senha_hash NULL, verificar quebraria.
        if user and (user.senha_pendente or not user.senha_hash):
            flash('Seu acesso foi aprovado — falta definir sua senha. Aguarde o link.', 'info')
            return render_template('login.html')

        if user and verify_password(user.senha_hash, password):
            if not user.is_active():
                flash('Sua conta está desativada. Entre em contato com o administrador.', 'warning')
                return render_template('login.html')
            
            # Faz login
            login_user(user, remember=remember)
            # Login pela porta normal SEMPRE limpa o escopo do Portal do
            # Instalador: sem isso, quem tivesse usado /qrobo antes neste
            # navegador continuaria preso ao gate restrito no app completo.
            session.pop('qrobo_escopo', None)
            session.pop('qrobo_visto_em', None)
            # Depois do login_user ja ha sessao: o registrar normal pega o
            # autor sozinho, com IP e navegador.
            registrar('escrita.entrou', 'acesso',
                      tabela='usuarios', registro_id=user.id,
                      depois={'login': login_name, 'lembrar': bool(remember)})
            flash(f'Bem-vindo(a), {user.nome}!', 'success')
            
            # Redireciona para página solicitada ou dashboard
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('dashboard.index'))
        else:
            # Tentativa que falha NAO tem sessao — o registrar normal
            # desistiria. Aqui o ator e conhecido pelo login digitado, ainda
            # que ele nao exista: e o mesmo caminho do agente por chave.
            registrar_agente('escrita.entrada_recusada', 'acesso',
                             usuario_id=(user.id if user else None),
                             usuario_nome=(user.nome if user else 'desconhecido'),
                             usuario_login=login_name[:80] or None,
                             tabela='usuarios',
                             registro_id=(user.id if user else None),
                             depois={'login_tentado': login_name[:80],
                                     'motivo': ('senha incorreta' if user
                                                else 'login nao existe')})
            flash('Login ou senha incorretos.', 'danger')
    
    return render_template('login.html')


@auth.route('/logout')
def logout():
    """Faz logout do usuário"""
    # ANTES do logout_user: depois dele nao ha mais quem registrar.
    registrar('escrita.saiu', 'acesso', tabela='usuarios',
              registro_id=getattr(current_user, 'id', None))
    logout_user()
    flash('Você saiu da sua conta.', 'info')
    return redirect(url_for('auth.login'))
