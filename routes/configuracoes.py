"""Rotas do módulo Configurações — Usuários e Perfis de Acesso"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from utils.auth_helper import login_required, admin_required
from utils.db_helper import execute_query
from utils.auth_helper import hash_password
from utils.permissions import PERMISSION_CATALOG

configuracoes = Blueprint('configuracoes', __name__, url_prefix='/configuracoes')


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------
@configuracoes.route('/')
@login_required
def index():
    return render_template('configuracoes/index.html')


# ===========================================================================
# USUÁRIOS
# ===========================================================================

@configuracoes.route('/usuarios/')
@admin_required
def usuarios():
    rows = execute_query(
        """SELECT u.id, u.nome, u.email, u.tipo_usuario, u.situacao, u.cargo,
                  GROUP_CONCAT(pa.nome ORDER BY pa.nome SEPARATOR ', ') AS perfis
             FROM usuarios u
             LEFT JOIN usuario_perfis up ON up.usuario_id = u.id
             LEFT JOIN perfis_acesso pa  ON pa.id = up.perfil_id
            GROUP BY u.id
            ORDER BY u.nome""",
        fetch=True,
    ) or []
    return render_template('configuracoes/usuarios_lista.html', usuarios=rows)


@configuracoes.route('/usuarios/novo', methods=['GET', 'POST'])
@admin_required
def usuario_novo():
    perfis = execute_query(
        "SELECT id, nome FROM perfis_acesso WHERE situacao='ATIVO' ORDER BY nome",
        fetch=True,
    ) or []
    clientes = execute_query(
        "SELECT id, nome_razao_social FROM clientes WHERE situacao='ATIVO' ORDER BY nome_razao_social",
        fetch=True,
    ) or []

    if request.method == 'POST':
        nome     = request.form.get('nome', '').strip()
        email    = request.form.get('email', '').strip().lower()
        senha    = request.form.get('senha', '').strip()
        tipo     = request.form.get('tipo_usuario', 'ASSISTENTE')
        cargo    = request.form.get('cargo', '').strip()
        telefone = request.form.get('telefone', '').strip()
        perfis_sel = request.form.getlist('perfis')
        empresas_sel = request.form.getlist('empresas')

        if not nome or not email or not senha:
            flash('Nome, e-mail e senha são obrigatórios.', 'danger')
            return render_template('configuracoes/usuario_form.html',
                                   perfis=perfis, clientes=clientes, usuario=None,
                                   perfis_usuario=set(), empresas_usuario=set())

        # Verifica e-mail duplicado
        existe = execute_query(
            "SELECT id FROM usuarios WHERE email = %s", (email,), fetch=True, fetch_one=True
        )
        if existe:
            flash('Já existe um usuário com este e-mail.', 'danger')
            return render_template('configuracoes/usuario_form.html',
                                   perfis=perfis, clientes=clientes, usuario=None,
                                   perfis_usuario=set(), empresas_usuario=set())

        uid = execute_query(
            """INSERT INTO usuarios (nome, email, senha_hash, tipo_usuario, situacao, cargo, telefone)
               VALUES (%s, %s, %s, %s, 'ATIVO', %s, %s)""",
            (nome, email, hash_password(senha), tipo, cargo, telefone),
        )

        if not uid:
            flash('Erro ao criar o usuário. Verifique os dados e tente novamente.', 'danger')
            return render_template('configuracoes/usuario_form.html',
                                   perfis=perfis, clientes=clientes, usuario=None,
                                   perfis_usuario=set(), empresas_usuario=set())

        # Perfis
        for pid in perfis_sel:
            execute_query(
                "INSERT IGNORE INTO usuario_perfis (usuario_id, perfil_id) VALUES (%s, %s)",
                (uid, pid), fetch=False,
            )

        # Empresas permitidas
        for cid in empresas_sel:
            execute_query(
                "INSERT IGNORE INTO usuario_empresas_permitidas (usuario_id, cliente_id) VALUES (%s, %s)",
                (uid, cid), fetch=False,
            )

        flash(f'Usuário "{nome}" criado com sucesso.', 'success')
        return redirect(url_for('configuracoes.usuarios'))

    return render_template('configuracoes/usuario_form.html',
                           perfis=perfis, clientes=clientes, usuario=None)


@configuracoes.route('/usuarios/<int:uid>/editar', methods=['GET', 'POST'])
@admin_required
def usuario_editar(uid):
    usuario = execute_query(
        "SELECT * FROM usuarios WHERE id = %s", (uid,), fetch=True, fetch_one=True
    )
    if not usuario:
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('configuracoes.usuarios'))

    perfis = execute_query(
        "SELECT id, nome FROM perfis_acesso WHERE situacao='ATIVO' ORDER BY nome",
        fetch=True,
    ) or []
    clientes = execute_query(
        "SELECT id, nome_razao_social FROM clientes WHERE situacao='ATIVO' ORDER BY nome_razao_social",
        fetch=True,
    ) or []
    perfis_usuario = {
        r['perfil_id']
        for r in (execute_query(
            "SELECT perfil_id FROM usuario_perfis WHERE usuario_id = %s", (uid,), fetch=True,
        ) or [])
    }
    empresas_usuario = {
        r['cliente_id']
        for r in (execute_query(
            "SELECT cliente_id FROM usuario_empresas_permitidas WHERE usuario_id = %s",
            (uid,), fetch=True,
        ) or [])
    }

    if request.method == 'POST':
        nome     = request.form.get('nome', '').strip()
        email    = request.form.get('email', '').strip().lower()
        tipo     = request.form.get('tipo_usuario', 'ASSISTENTE')
        cargo    = request.form.get('cargo', '').strip()
        telefone = request.form.get('telefone', '').strip()
        situacao = request.form.get('situacao', 'ATIVO')
        nova_senha = request.form.get('nova_senha', '').strip()
        perfis_sel   = request.form.getlist('perfis')
        empresas_sel = request.form.getlist('empresas')

        if not nome or not email:
            flash('Nome e e-mail são obrigatórios.', 'danger')
            return render_template('configuracoes/usuario_form.html',
                                   perfis=perfis, clientes=clientes, usuario=usuario,
                                   perfis_usuario=perfis_usuario,
                                   empresas_usuario=empresas_usuario)

        # Verifica e-mail duplicado (excluindo o próprio)
        existe = execute_query(
            "SELECT id FROM usuarios WHERE email = %s AND id != %s",
            (email, uid), fetch=True, fetch_one=True,
        )
        if existe:
            flash('Já existe outro usuário com este e-mail.', 'danger')
            return render_template('configuracoes/usuario_form.html',
                                   perfis=perfis, clientes=clientes, usuario=usuario,
                                   perfis_usuario=perfis_usuario,
                                   empresas_usuario=empresas_usuario)

        if nova_senha:
            execute_query(
                """UPDATE usuarios SET nome=%s, email=%s, senha_hash=%s, tipo_usuario=%s,
                                       situacao=%s, cargo=%s, telefone=%s
                    WHERE id=%s""",
                (nome, email, hash_password(nova_senha), tipo, situacao, cargo, telefone, uid),
                fetch=False,
            )
        else:
            execute_query(
                """UPDATE usuarios SET nome=%s, email=%s, tipo_usuario=%s,
                                       situacao=%s, cargo=%s, telefone=%s
                    WHERE id=%s""",
                (nome, email, tipo, situacao, cargo, telefone, uid),
                fetch=False,
            )

        # Atualiza perfis
        execute_query("DELETE FROM usuario_perfis WHERE usuario_id = %s", (uid,), fetch=False)
        for pid in perfis_sel:
            execute_query(
                "INSERT IGNORE INTO usuario_perfis (usuario_id, perfil_id) VALUES (%s, %s)",
                (uid, pid), fetch=False,
            )

        # Atualiza empresas
        execute_query(
            "DELETE FROM usuario_empresas_permitidas WHERE usuario_id = %s", (uid,), fetch=False,
        )
        for cid in empresas_sel:
            execute_query(
                "INSERT IGNORE INTO usuario_empresas_permitidas (usuario_id, cliente_id) VALUES (%s, %s)",
                (uid, cid), fetch=False,
            )

        flash(f'Usuário "{nome}" atualizado com sucesso.', 'success')
        return redirect(url_for('configuracoes.usuarios'))

    return render_template('configuracoes/usuario_form.html',
                           perfis=perfis, clientes=clientes, usuario=usuario,
                           perfis_usuario=perfis_usuario,
                           empresas_usuario=empresas_usuario)


@configuracoes.route('/usuarios/<int:uid>/toggle', methods=['POST'])
@admin_required
def usuario_toggle(uid):
    """Ativa/desativa um usuário."""
    u = execute_query("SELECT situacao FROM usuarios WHERE id=%s", (uid,), fetch=True, fetch_one=True)
    if not u:
        return jsonify(ok=False, msg='Usuário não encontrado.'), 404
    nova = 'INATIVO' if u['situacao'] == 'ATIVO' else 'ATIVO'
    execute_query("UPDATE usuarios SET situacao=%s WHERE id=%s", (nova, uid), fetch=False)
    return jsonify(ok=True, situacao=nova)


# ===========================================================================
# PERFIS DE ACESSO
# ===========================================================================

@configuracoes.route('/perfis/')
@admin_required
def perfis():
    rows = execute_query(
        """SELECT pa.id, pa.nome, pa.descricao, pa.situacao,
                  COUNT(DISTINCT up.usuario_id) AS qtd_usuarios,
                  COUNT(DISTINCT pp.permissao_codigo) AS qtd_permissoes
             FROM perfis_acesso pa
             LEFT JOIN usuario_perfis up ON up.perfil_id = pa.id
             LEFT JOIN perfil_permissoes pp ON pp.perfil_id = pa.id
            GROUP BY pa.id
            ORDER BY pa.nome""",
        fetch=True,
    ) or []
    return render_template('configuracoes/perfis_lista.html', perfis=rows)


@configuracoes.route('/perfis/novo', methods=['GET', 'POST'])
@admin_required
def perfil_novo():
    if request.method == 'POST':
        nome      = request.form.get('nome', '').strip()
        descricao = request.form.get('descricao', '').strip()
        perms_sel = request.form.getlist('permissoes')

        if not nome:
            flash('O nome do perfil é obrigatório.', 'danger')
            return render_template('configuracoes/perfil_form.html',
                                   catalog=PERMISSION_CATALOG, perfil=None, perms_perfil=set())

        pid = execute_query(
            "INSERT INTO perfis_acesso (nome, descricao) VALUES (%s, %s)",
            (nome, descricao),
        )
        for cod in perms_sel:
            execute_query(
                "INSERT IGNORE INTO perfil_permissoes (perfil_id, permissao_codigo) VALUES (%s, %s)",
                (pid, cod), fetch=False,
            )

        flash(f'Perfil "{nome}" criado com sucesso.', 'success')
        return redirect(url_for('configuracoes.perfis'))

    return render_template('configuracoes/perfil_form.html',
                           catalog=PERMISSION_CATALOG, perfil=None, perms_perfil=set())


@configuracoes.route('/perfis/<int:pid>/editar', methods=['GET', 'POST'])
@admin_required
def perfil_editar(pid):
    perfil = execute_query(
        "SELECT * FROM perfis_acesso WHERE id = %s", (pid,), fetch=True, fetch_one=True,
    )
    if not perfil:
        flash('Perfil não encontrado.', 'danger')
        return redirect(url_for('configuracoes.perfis'))

    perms_perfil = {
        r['permissao_codigo']
        for r in (execute_query(
            "SELECT permissao_codigo FROM perfil_permissoes WHERE perfil_id = %s",
            (pid,), fetch=True,
        ) or [])
    }

    if request.method == 'POST':
        nome      = request.form.get('nome', '').strip()
        descricao = request.form.get('descricao', '').strip()
        situacao  = request.form.get('situacao', 'ATIVO')
        perms_sel = request.form.getlist('permissoes')

        if not nome:
            flash('O nome do perfil é obrigatório.', 'danger')
            return render_template('configuracoes/perfil_form.html',
                                   catalog=PERMISSION_CATALOG, perfil=perfil,
                                   perms_perfil=perms_perfil)

        execute_query(
            "UPDATE perfis_acesso SET nome=%s, descricao=%s, situacao=%s WHERE id=%s",
            (nome, descricao, situacao, pid), fetch=False,
        )
        execute_query(
            "DELETE FROM perfil_permissoes WHERE perfil_id = %s", (pid,), fetch=False,
        )
        for cod in perms_sel:
            execute_query(
                "INSERT IGNORE INTO perfil_permissoes (perfil_id, permissao_codigo) VALUES (%s, %s)",
                (pid, cod), fetch=False,
            )

        flash(f'Perfil "{nome}" atualizado com sucesso.', 'success')
        return redirect(url_for('configuracoes.perfis'))

    return render_template('configuracoes/perfil_form.html',
                           catalog=PERMISSION_CATALOG, perfil=perfil, perms_perfil=perms_perfil)


@configuracoes.route('/perfis/<int:pid>/excluir', methods=['POST'])
@admin_required
def perfil_excluir(pid):
    p = execute_query("SELECT nome FROM perfis_acesso WHERE id=%s", (pid,), fetch=True, fetch_one=True)
    if not p:
        flash('Perfil não encontrado.', 'danger')
        return redirect(url_for('configuracoes.perfis'))
    execute_query("DELETE FROM perfis_acesso WHERE id=%s", (pid,), fetch=False)
    flash(f'Perfil "{p["nome"]}" excluído.', 'success')
    return redirect(url_for('configuracoes.perfis'))
