"""Rotas de autenticação (login/logout)"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, current_user
from models.usuario import Usuario
from utils.auth_helper import verify_password
from utils.atividade import registrar, registrar_agente
from utils import login_seguro as LS
import time

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
        # "Lembrar-me" (1 ano conectado) saiu em 10/11/2026: ver utils/login_seguro.py
        
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
            if not user.is_active:
                flash('Sua conta está desativada. Entre em contato com o administrador.', 'warning')
                return render_template('login.html')
            
            next_page = request.args.get('next')
            # 10/11/2026: mais de 24 h sem usar (ou navegador novo) -> código no e-mail.
            motivo = LS.motivo_sem_codigo(user)
            if motivo is None:
                ok, erro = LS.enviar_codigo(user)
                session['qc_pend'] = {'u': user.id, 't': int(time.time()), 'n': next_page,
                                      'env': int(time.time())}
                registrar_agente('escrita.codigo_enviado', 'acesso', usuario_id=user.id,
                                 usuario_nome=user.nome, usuario_login=user.login,
                                 tabela='usuarios', registro_id=user.id,
                                 depois={'para': LS.mascarar(user.email), 'enviado': bool(ok),
                                         'erro': (str(erro)[:200] if not ok else None)})
                if not ok:
                    flash('Não consegui enviar o código para o seu e-mail agora. Tente "Reenviar" '
                          'em 1 minuto ou peça ao administrador para liberar seu acesso.', 'danger')
                return redirect(url_for('auth.login_codigo'))
            return _concluir_login(user, next_page, via=motivo)
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


def _concluir_login(user, next_page, via):
    login_user(user, remember=False)
    # Login pela porta normal SEMPRE limpa o escopo do Portal do
    # Instalador: sem isso, quem tivesse usado /qrobo antes neste
    # navegador continuaria preso ao gate restrito no app completo.
    session.pop('qrobo_escopo', None)
    session.pop('qrobo_visto_em', None)
    session.pop('qc_pend', None)
    LS.gravar_entrada(user.id)
    # Depois do login_user ja ha sessao: o registrar normal pega o
    # autor sozinho, com IP e navegador.
    registrar('escrita.entrou', 'acesso', tabela='usuarios', registro_id=user.id,
              depois={'login': user.login, 'via': via, 'ip': LS.ip_cliente()})
    flash(f'Bem-vindo(a), {user.nome}!', 'success')
    # So caminho interno: evita mandar para fora depois do login.
    seguro = bool(next_page) and next_page.startswith('/') and not next_page.startswith('//')
    destino = next_page if seguro else url_for('dashboard.index')
    return LS.renovar_navegador(redirect(destino), user.id)


def _pendente():
    p = session.get('qc_pend') or {}
    if not p.get('u') or time.time() - p.get('t', 0) > 15 * 60:
        session.pop('qc_pend', None)
        return None, None
    user = Usuario.get_by_id(int(p['u']))
    if not user or not user.is_active or user.senha_pendente:
        session.pop('qc_pend', None)
        return None, None
    return p, user


@auth.route('/login/codigo', methods=['GET', 'POST'])
def login_codigo():
    """2a etapa: o código de 6 números que foi para o e-mail (10/11/2026)."""
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    p, user = _pendente()
    if not user:
        flash('Comece de novo: digite seu usuário e senha.', 'warning')
        return redirect(url_for('auth.login'))
    if request.method == 'POST':
        r = LS.conferir_codigo(user.id, request.form.get('codigo'))
        if r == 'ok':
            return _concluir_login(user, p.get('n'), via='codigo_email')
        registrar_agente('escrita.codigo_recusado', 'acesso', usuario_id=user.id,
                         usuario_nome=user.nome, usuario_login=user.login,
                         tabela='usuarios', registro_id=user.id, depois={'resultado': r})
        if r == 'vencido':
            flash('Este código venceu ou foi digitado errado muitas vezes. Clique em "Reenviar código".', 'danger')
        else:
            flash(f'Código errado. Confira se abriu o e-mail certo: o código vale só para {user.nome}.', 'danger')
    return render_template('login.html', etapa='codigo', pend_nome=user.nome,
                           pend_email=LS.mascarar(user.email), minutos=LS.MINUTOS_CODIGO)


@auth.route('/login/codigo/reenviar', methods=['POST'])
def login_codigo_reenviar():
    p, user = _pendente()
    if not user:
        return redirect(url_for('auth.login'))
    if time.time() - p.get('env', 0) < 60:
        flash('Espere 1 minuto para pedir outro código.', 'warning')
        return redirect(url_for('auth.login_codigo'))
    ok, erro = LS.enviar_codigo(user)
    p['env'] = int(time.time())
    p['t'] = int(time.time())
    session['qc_pend'] = p
    registrar_agente('escrita.codigo_enviado', 'acesso', usuario_id=user.id,
                     usuario_nome=user.nome, usuario_login=user.login,
                     tabela='usuarios', registro_id=user.id,
                     depois={'para': LS.mascarar(user.email), 'enviado': bool(ok), 'reenvio': True,
                             'erro': (str(erro)[:200] if not ok else None)})
    flash('Enviamos um código novo.' if ok else
          'Não consegui enviar o código. Peça ao administrador para liberar seu acesso.',
          'success' if ok else 'danger')
    return redirect(url_for('auth.login_codigo'))


@auth.route('/logout')
def logout():
    """Faz logout do usuário"""
    # ANTES do logout_user: depois dele nao ha mais quem registrar.
    registrar('escrita.saiu', 'acesso', tabela='usuarios',
              registro_id=getattr(current_user, 'id', None))
    logout_user()
    flash('Você saiu da sua conta.', 'info')
    return redirect(url_for('auth.login'))
