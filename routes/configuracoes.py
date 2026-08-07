"""Rotas do módulo Configurações — Usuários e Perfis de Acesso"""
import logging
import re

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from utils.auth_helper import login_required, admin_required, permission_required, hash_password
from utils.db_helper import execute_query, transacao
from utils.permissions import PERMISSION_CATALOG
from utils import cadastro_token

logger = logging.getLogger(__name__)

configuracoes = Blueprint('configuracoes', __name__, url_prefix='/configuracoes')


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------
@configuracoes.route('/')
@permission_required('configuracoes.index')
def index():
    # Espaço do Dropbox: SÓ para admin e SÓ leitura (users/get_space_usage), com
    # cache de 1h em app_config — o render normal não bate na API do Dropbox.
    # Consultado apenas para admin para que um refresh de não-admin nem chegue
    # perto de gastar rate limit.
    espaco = None
    if current_user.is_admin():
        from utils.dropbox_space import get_space
        espaco = get_space()
    return render_template('configuracoes/index.html', dropbox_espaco=espaco)


@configuracoes.route('/dropbox/espaco/atualizar', methods=['POST'])
@admin_required
def dropbox_espaco_atualizar():
    """Força a releitura do espaço do Dropbox, ignorando o TTL do cache.

    Continua SOMENTE LEITURA: o 'atualizar' é do cache local, não do Dropbox.
    """
    from utils.dropbox_space import get_space
    return jsonify(get_space(force=True)), 200


# ===========================================================================
# USUÁRIOS
# ===========================================================================

@configuracoes.route('/usuarios/')
@admin_required
def usuarios():
    rows = execute_query(
        """SELECT u.id, u.nome, u.login, u.email, u.tipo_usuario, u.situacao, u.cargo,
                  GROUP_CONCAT(pa.nome ORDER BY pa.nome SEPARATOR ', ') AS perfis
             FROM usuarios u
             LEFT JOIN usuario_perfis up ON up.usuario_id = u.id
             LEFT JOIN perfis_acesso pa  ON pa.id = up.perfil_id
            WHERE u.classe_conta = 'FUNCIONARIO'
            GROUP BY u.id
            ORDER BY u.nome""",
        fetch=True,
    ) or []
    return render_template('configuracoes/usuarios_lista.html', usuarios=rows,
                           links=cadastro_token.listar(limite=50),
                           pendentes=_cadastros_pendentes(),
                           perfis_disponiveis=_perfis_ativos(),
                           departamentos_disponiveis=_departamentos_ativos(),
                           tipos_usuario=TIPOS_USUARIO,
                           csrf_token=_qrobo_csrf_token())


# ===========================================================================
# Q-COLABORE Parte 2 — link de cadastro + fila de pendentes
#
# Extensão desta MESMA tela: quem administra usuários é quem convida gente
# nova e quem aprova. Módulo Configurações intocado no resto.
#
# CSRF: reusa o helper de sessão do portal do Q-Robô, como o painel do
# escrita_fiscal já faz — gerar/revogar link é ação sensível e o helper custa
# uma linha. (SESSION_COOKIE_SAMESITE='Lax' é só a primeira barreira.)
# ===========================================================================

VALIDADE_LINK_HORAS = 72

# Os mesmos tipos do ENUM de usuarios.tipo_usuario — o painel de aprovação
# oferece exatamente estes, e o servidor recusa qualquer outro valor.
TIPOS_USUARIO = ('ADMIN', 'GERENTE', 'CONTADOR', 'ASSISTENTE', 'ESTAGIARIO')


class AprovacaoErro(RuntimeError):
    """Recusa amigável de uma aprovação (login tomado, e-mail duplicado, etc.).

    Não é erro de servidor: sobe até a rota, vira mensagem no painel e a pendência
    continua na fila para o admin corrigir. Distinta de Exception genérica, que
    seria um 500.
    """


def _perfis_ativos():
    return execute_query(
        "SELECT id, nome FROM perfis_acesso WHERE situacao='ATIVO' ORDER BY nome",
        fetch=True) or []


def _departamentos_ativos():
    return execute_query(
        'SELECT id, nome FROM departamentos WHERE ativo = 1 ORDER BY nome',
        fetch=True) or []


# --- Formatação SÓ para exibição no painel de análise. O banco continua com o
# valor cru (dígitos / datas ISO); estes helpers vestem os dados para o admin ler.
def _fmt_data(v):
    try:
        return v.strftime('%d/%m/%Y') if v is not None else None
    except Exception:
        return str(v) if v else None


def _fmt_datahora(v):
    try:
        return v.strftime('%d/%m/%Y %H:%M') if v is not None else None
    except Exception:
        return str(v) if v else None


def _fmt_cpf(v):
    d = re.sub(r'\D', '', v or '')
    return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}' if len(d) == 11 else (v or None)


def _fmt_cel(v):
    d = re.sub(r'\D', '', v or '')
    if len(d) == 11:                       # (XX) X XXXX-XXXX
        return f'({d[:2]}) {d[2]} {d[3:7]}-{d[7:]}'
    if len(d) == 10:                       # (XX) XXXX-XXXX
        return f'({d[:2]}) {d[2:6]}-{d[6:]}'
    return v or None


def _qrobo_csrf_token():
    from routes.qrobo import csrf_token
    return csrf_token()


def _qrobo_csrf_ok():
    from routes.qrobo import csrf_valido
    return csrf_valido()


def _cadastros_pendentes():
    """Candidaturas aguardando decisão. Nasce vazia — o formulário é a Parte 3."""
    return execute_query(
        """SELECT p.id, p.nome_completo, p.login_escolhido, p.nick_escolhido,
                  p.email_pessoal, p.email_corporativo, p.celular_corporativo,
                  p.modalidade_trabalho, p.ja_funcionario,
                  p.criado_em, p.status,
                  GROUP_CONCAT(d.nome ORDER BY d.nome SEPARATOR ', ') AS departamentos
             FROM cadastro_pendente p
             LEFT JOIN cadastro_pendente_departamentos pd ON pd.pendente_id = p.id
             LEFT JOIN departamentos d ON d.id = pd.departamento_id
            WHERE p.status = 'PENDENTE'
            GROUP BY p.id
            ORDER BY p.criado_em""",
        fetch=True,
    ) or []


@configuracoes.route('/usuarios/link/gerar', methods=['POST'])
@admin_required
def usuario_link_gerar():
    """Gera um link de cadastro de uso único e devolve a URL UMA vez.

    Responde JSON de propósito: a URL com o token não passa por flash/session
    nem pela barra de endereços — some quando o modal fecha. Depois disso só o
    prefixo existe, aqui e no banco.
    """
    if not _qrobo_csrf_ok():
        return jsonify(ok=False, msg='Formulário expirado. Recarregue a página.'), 400

    destinatario = (request.form.get('destinatario') or '').strip() or None
    try:
        # url_builder: o util não conhece url_for. Passamos o construtor para que
        # url_claro (a cópia reenviável) nasça no mesmo INSERT do hash. Vive só
        # enquanto o link é pendente — some no uso/revogação/expiração.
        token, link_id = cadastro_token.gerar(
            'CADASTRO', current_user.id,
            destinatario=destinatario, validade_horas=VALIDADE_LINK_HORAS,
            url_builder=lambda tok: url_for('cadastro.formulario', token=tok,
                                            _external=True))
    except ValueError as exc:
        return jsonify(ok=False, msg=str(exc)), 400
    except Exception:
        # NUNCA ecoar a exceção crua: ela pode carregar o SQL e o parâmetro.
        logger.exception('[qcolabore] falha ao gerar link de cadastro')
        return jsonify(ok=False, msg='Não foi possível gerar o link agora.'), 500

    # A rota pública agora EXISTE (Parte 3) — url_for no lugar do path montado
    # à mão, como estava combinado desde a Parte 2.
    url = url_for('cadastro.formulario', token=token, _external=True)
    # Log com o ID e o prefixo. O token inteiro não entra em log nenhum.
    logger.info('[qcolabore] link de cadastro id=%s prefixo=%s gerado por %s.',
                link_id, token[:8], current_user.id)
    return jsonify(ok=True, url=url, prefixo=token[:8],
                   validade_horas=VALIDADE_LINK_HORAS)


@configuracoes.route('/usuarios/link/<int:link_id>/revogar', methods=['POST'])
@admin_required
def usuario_link_revogar(link_id):
    """Mata um link ainda não usado."""
    if not _qrobo_csrf_ok():
        return jsonify(ok=False, msg='Formulário expirado. Recarregue a página.'), 400
    if cadastro_token.revogar(link_id, current_user.id):
        logger.info('[qcolabore] link id=%s revogado por %s.', link_id, current_user.id)
        return jsonify(ok=True)
    # rowcount 0: já usado, já revogado, ou inexistente — nenhum deles é erro
    # de servidor, e a tela precisa recarregar para mostrar o estado real.
    return jsonify(ok=False, msg='Este link já foi usado ou revogado.'), 409


# ---------------------------------------------------------------------------
# Q-COLABORE Parte 5 — analisar / aprovar / recusar candidatura
# ---------------------------------------------------------------------------
@configuracoes.route('/usuarios/pendente/<int:pid>')
@admin_required
def usuario_pendente_detalhe(pid):
    """Todos os dados da candidatura para o painel de análise (JSON).

    Inclui os dados BANCÁRIOS: eles aparecem SÓ aqui, sob @admin_required, e nunca
    entram em log — por isso a resposta não é logada e o except de mais abaixo (nas
    rotas de decisão) não ecoa payload.
    """
    cand = execute_query(
        "SELECT * FROM cadastro_pendente WHERE id = %s AND status = 'PENDENTE'",
        (pid,), fetch=True, fetch_one=True)
    if not cand:
        return jsonify(ok=False, msg='Candidatura não encontrada ou já decidida.'), 404

    banco = execute_query(
        """SELECT banco_codigo, banco_nome, agencia, conta, conta_tipo,
                  titular_nome, titular_cpf, pix_tipo, pix_chave
             FROM usuario_dados_bancarios WHERE pendente_id = %s""",
        (pid,), fetch=True, fetch_one=True)

    deps = [r['departamento_id'] for r in (execute_query(
        "SELECT departamento_id FROM cadastro_pendente_departamentos WHERE pendente_id = %s",
        (pid,), fetch=True) or [])]

    campos = ('id', 'nome_completo', 'cpf', 'data_nascimento', 'login_escolhido',
              'nick_escolhido', 'email_pessoal', 'email_corporativo',
              'celular_pessoal', 'celular_corporativo', 'cep', 'logradouro',
              'numero', 'complemento', 'bairro', 'cidade', 'estado', 'pais',
              'modalidade_trabalho', 'ja_funcionario', 'criado_em')
    c = {k: cand.get(k) for k in campos}
    # Apresentação em padrão brasileiro (feita AQUI, no servidor, não no JS):
    c['data_nascimento'] = _fmt_data(cand.get('data_nascimento'))
    c['criado_em'] = _fmt_datahora(cand.get('criado_em'))
    c['cpf'] = _fmt_cpf(cand.get('cpf'))
    c['celular_pessoal'] = _fmt_cel(cand.get('celular_pessoal'))
    c['celular_corporativo'] = _fmt_cel(cand.get('celular_corporativo'))
    return jsonify(ok=True, cand=c, banco=banco, departamentos_pedidos=deps)


def _aprovar_pendente(pid, tipo, perfis, deps, admin_id):
    """Cria o usuário a partir da candidatura, em UMA transação (tudo ou nada).

    Devolve o id do usuário criado. Qualquer AprovacaoErro faz a transação inteira
    reverter (transacao() dá rollback em exceção) — nem usuário, nem perfis, nem
    mudança de status sobrevivem a uma falha no meio.
    """
    cand = execute_query('SELECT * FROM cadastro_pendente WHERE id = %s',
                         (pid,), fetch=True, fetch_one=True)
    if not cand or cand['status'] != 'PENDENTE':
        raise AprovacaoErro('Esta candidatura já foi decidida.')

    login = (cand['login_escolhido'] or '').strip().lower()
    email = ((cand.get('email_corporativo') or cand.get('email_pessoal') or '')
             .strip().lower()) or None
    if not email:
        raise AprovacaoErro('O candidato não informou e-mail — não dá para criar o '
                            'acesso sem um. Peça que complete o cadastro.')
    nick = cand.get('nick_escolhido')
    cpf = cand.get('cpf') or None
    telefone = cand.get('celular_corporativo') or cand.get('celular_pessoal')
    dep_primario = deps[0] if deps else None

    with transacao() as cur:
        # a) o login ainda está livre? (pode ter sido tomado desde a candidatura)
        cur.execute('SELECT id FROM usuarios WHERE login = %s', (login,))
        if cur.fetchone():
            raise AprovacaoErro(f'O login "{login}" já foi tomado. Ajuste o login da '
                                'candidatura e aprove de novo.')
        # e-mail e CPF são UNIQUE em usuarios: barra aqui com mensagem decente em
        # vez de deixar estourar erro de driver no meio do INSERT.
        cur.execute('SELECT id FROM usuarios WHERE email = %s', (email,))
        if cur.fetchone():
            raise AprovacaoErro(f'Já existe um usuário com o e-mail {email}.')
        if cpf:
            cur.execute('SELECT id FROM usuarios WHERE cpf = %s', (cpf,))
            if cur.fetchone():
                raise AprovacaoErro('Já existe um usuário com este CPF.')

        # b) cria a conta SEM senha e com login bloqueado (senha_pendente=1). A
        # Parte 6 manda o link de senha e zera a flag.
        cur.execute(
            """INSERT INTO usuarios
                 (nome, nick, email, login, senha_hash, senha_pendente,
                  tipo_usuario, classe_conta, situacao, cpf, telefone, departamento_id)
               VALUES (%s, %s, %s, %s, NULL, 1, %s, 'FUNCIONARIO', 'ATIVO', %s, %s, %s)""",
            (cand['nome_completo'], nick, email, login, tipo, cpf, telefone, dep_primario))
        uid = cur.lastrowid

        # perfis escolhidos pelo admin
        for pf in perfis:
            cur.execute('INSERT IGNORE INTO usuario_perfis (usuario_id, perfil_id) '
                        'VALUES (%s, %s)', (uid, pf))

        # e) departamentos DEFINITIVOS: reescreve o conjunto na tabela da pendência,
        # que segue ligada ao usuário (usuario_id) — é onde o conjunto completo mora
        # (usuarios só tem um departamento_id, gravado como primário acima).
        cur.execute('DELETE FROM cadastro_pendente_departamentos WHERE pendente_id = %s',
                    (pid,))
        for d in deps:
            cur.execute('INSERT INTO cadastro_pendente_departamentos '
                        '(pendente_id, departamento_id) VALUES (%s, %s)', (pid, d))

        # c) move os bancários da pendência para o usuário recém-criado
        cur.execute('UPDATE usuario_dados_bancarios SET usuario_id = %s '
                    'WHERE pendente_id = %s', (uid, pid))

        # d) marca a pendência aprovada. O WHERE status='PENDENTE' + rowcount==1
        # fecha a corrida: dois admins aprovando ao mesmo tempo → um só vence.
        cur.execute(
            """UPDATE cadastro_pendente
                  SET status = 'APROVADO', decidido_por = %s, decidido_em = NOW(),
                      usuario_id = %s
                WHERE id = %s AND status = 'PENDENTE'""",
            (admin_id, uid, pid))
        if cur.rowcount != 1:
            raise AprovacaoErro('Esta candidatura já foi decidida por outra pessoa.')

    return uid


@configuracoes.route('/usuarios/pendente/<int:pid>/aprovar', methods=['POST'])
@admin_required
def usuario_pendente_aprovar(pid):
    if not _qrobo_csrf_ok():
        return jsonify(ok=False, msg='Formulário expirado. Recarregue a página.'), 400

    tipo = (request.form.get('tipo_usuario') or '').strip().upper()
    if tipo not in TIPOS_USUARIO:
        return jsonify(ok=False, msg='Selecione um tipo de conta válido.'), 400
    perfis = [int(p) for p in request.form.getlist('perfis') if p.isdigit()]
    deps = [int(d) for d in request.form.getlist('departamentos') if d.isdigit()]

    try:
        uid = _aprovar_pendente(pid, tipo, perfis, deps, current_user.id)
    except AprovacaoErro as exc:
        # Falha esperada: pendência segue na fila para o admin ajustar.
        return jsonify(ok=False, msg=str(exc)), 409
    except Exception:
        # NUNCA ecoar a exceção crua — a transação pode carregar dados bancários.
        logger.exception('[qcolabore] falha ao aprovar pendência %s', pid)
        return jsonify(ok=False, msg='Não foi possível aprovar agora. Tente novamente.'), 500

    logger.info('[qcolabore] pendência %s aprovada por %s → usuário %s.',
                pid, current_user.id, uid)
    return jsonify(ok=True, usuario_id=uid)


@configuracoes.route('/usuarios/pendente/<int:pid>/recusar', methods=['POST'])
@admin_required
def usuario_pendente_recusar(pid):
    if not _qrobo_csrf_ok():
        return jsonify(ok=False, msg='Formulário expirado. Recarregue a página.'), 400

    motivo = (request.form.get('motivo') or '').strip()[:255]
    if not motivo:
        return jsonify(ok=False, msg='Diga um motivo curto para a recusa.'), 400

    # Não cria nada. A linha fica no banco como histórico (status RECUSADO) — some
    # só da FILA, que filtra por PENDENTE. rowcount==1 confirma que era pendente.
    with transacao() as cur:
        cur.execute(
            """UPDATE cadastro_pendente
                  SET status = 'RECUSADO', decisao_motivo = %s,
                      decidido_por = %s, decidido_em = NOW()
                WHERE id = %s AND status = 'PENDENTE'""",
            (motivo, current_user.id, pid))
        if cur.rowcount != 1:
            return jsonify(ok=False, msg='Candidatura já decidida ou inexistente.'), 409

    logger.info('[qcolabore] pendência %s recusada por %s.', pid, current_user.id)
    return jsonify(ok=True)


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
        login    = request.form.get('login', '').strip().lower().replace(' ', '')
        email    = request.form.get('email', '').strip().lower()
        senha    = request.form.get('senha', '').strip()
        tipo     = request.form.get('tipo_usuario', 'ASSISTENTE')
        cargo    = request.form.get('cargo', '').strip()
        telefone = request.form.get('telefone', '').strip()
        perfis_sel = request.form.getlist('perfis')
        empresas_sel = request.form.getlist('empresas')

        if not nome or not login or not email or not senha:
            flash('Nome, login, e-mail e senha são obrigatórios.', 'danger')
            return render_template('configuracoes/usuario_form.html',
                                   perfis=perfis, clientes=clientes, usuario=None,
                                   perfis_usuario=set(), empresas_usuario=set())

        # Verifica login duplicado
        existe_login = execute_query(
            "SELECT id FROM usuarios WHERE login = %s", (login,), fetch=True, fetch_one=True
        )
        if existe_login:
            flash('Já existe um usuário com este login.', 'danger')
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
            """INSERT INTO usuarios (nome, login, email, senha_hash, tipo_usuario, situacao, cargo, telefone)
               VALUES (%s, %s, %s, %s, %s, 'ATIVO', %s, %s)""",
            (nome, login, email, hash_password(senha), tipo, cargo, telefone),
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
                           perfis=perfis, clientes=clientes, usuario=None,
                           perfis_usuario=set(), empresas_usuario=set())


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
        login    = request.form.get('login', '').strip().lower().replace(' ', '')
        email    = request.form.get('email', '').strip().lower()
        tipo     = request.form.get('tipo_usuario', 'ASSISTENTE')
        cargo    = request.form.get('cargo', '').strip()
        telefone = request.form.get('telefone', '').strip()
        situacao = request.form.get('situacao', 'ATIVO')
        nova_senha = request.form.get('nova_senha', '').strip()
        perfis_sel   = request.form.getlist('perfis')
        empresas_sel = request.form.getlist('empresas')

        if not nome or not login or not email:
            flash('Nome, login e e-mail são obrigatórios.', 'danger')
            return render_template('configuracoes/usuario_form.html',
                                   perfis=perfis, clientes=clientes, usuario=usuario,
                                   perfis_usuario=perfis_usuario,
                                   empresas_usuario=empresas_usuario)

        # Verifica login duplicado (excluindo o próprio)
        existe_login = execute_query(
            "SELECT id FROM usuarios WHERE login = %s AND id != %s",
            (login, uid), fetch=True, fetch_one=True,
        )
        if existe_login:
            flash('Já existe outro usuário com este login.', 'danger')
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
                """UPDATE usuarios SET nome=%s, login=%s, email=%s, senha_hash=%s, tipo_usuario=%s,
                                       situacao=%s, cargo=%s, telefone=%s
                    WHERE id=%s""",
                (nome, login, email, hash_password(nova_senha), tipo, situacao, cargo, telefone, uid),
                fetch=False,
            )
        else:
            execute_query(
                """UPDATE usuarios SET nome=%s, login=%s, email=%s, tipo_usuario=%s,
                                       situacao=%s, cargo=%s, telefone=%s
                    WHERE id=%s""",
                (nome, login, email, tipo, situacao, cargo, telefone, uid),
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
