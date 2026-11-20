"""Ver como (20/11/2026). O admin escolhe uma pessoa na barra de cima, ao lado da busca, e o sistema inteiro
passa a se comportar como ela: menu, abas, portão das telas, empresas reservadas. Anderson: "ESCOLHER PESSOA quero
que fique lá em cima ao LADO da BUSCA... quero ver tudo que a pessoa pode ver ou não... em todas as ABAS".

Como funciona:
- session['ver_como'] = id da pessoa. Só vale quando quem está LOGADO (session _user_id) é admin.
- O load_user do Flask-Login devolve a PESSOA como current_user e guarda o admin em g.admin_real.
- É só para olhar: POST/PUT/DELETE são recusados, menos em Lançamentos (lá o admin já lançava pela pessoa desde
  18/11, marcado "lançado pelo administrador"), trocar/sair do ver-como e sair do sistema.
- A Auditoria e o "último acesso" continuam no nome do admin (utils/atividade.py, utils/login_seguro.py).
"""
from flask import g, session, has_request_context, request, flash, redirect, jsonify
from flask_login import current_user

CHAVE = 'ver_como'
_LIVRES = ('ver_como.trocar', 'auth.logout', 'static')
_PREFIXO_LIVRE = ('/financeiro/lancamentos',)


def carregar(u):
    """Chamado pelo load_user: troca o admin pela pessoa escolhida (se houver)."""
    if not (u and has_request_context()):
        return u
    uid = session.get(CHAVE)
    if not uid:
        return u
    try:
        admin = u.is_admin()
    except Exception:
        admin = False
    if not admin or int(uid) == u.id:
        session.pop(CHAVE, None)
        return u
    from models.usuario import Usuario
    t = Usuario.get_by_id(int(uid))
    if not t or t.senha_pendente:
        session.pop(CHAVE, None)
        return u
    g.admin_real = u
    return t


def admin_real():
    """O admin de verdade (vendo como alguém ou não); None se quem está logado não é admin."""
    a = g.get('admin_real') if has_request_context() else None
    if a:
        return a
    try:
        if current_user.is_authenticated and current_user.is_admin():
            return current_user._get_current_object()
    except Exception:
        pass
    return None


def vendo_como():
    return bool(has_request_context() and g.get('admin_real'))


def trava_escrita():
    """before_request: no ver-como, nada se grava (fora as exceções). Roda antes do portão."""
    if request.method in ('GET', 'HEAD', 'OPTIONS') or request.endpoint in _LIVRES:
        return None
    if not getattr(current_user, 'is_authenticated', False) or not vendo_como():
        return None
    if request.path.startswith(_PREFIXO_LIVRE):
        return None
    msg = 'Você está vendo como %s: aqui é só para olhar, nada é gravado. Clique em "Voltar a ser eu" para mexer.' % (current_user.nome or 'outra pessoa')
    if request.is_json or request.accept_mimetypes.best == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': False, 'erro': msg, 'error': msg}), 403
    flash(msg, 'warning')
    return redirect(request.referrer or '/')


# O que a pessoa abre — a mesma regra do portão (utils/acesso.py)
_AREAS = [('dashboard.index', 'Início'), ('clientes.index', 'Cadastros'), ('modulos.comercial', 'Comercial'),
          ('contabil.index', 'Contábil'), ('escrita_fiscal.index', 'Fiscal'), ('modulos.dp', 'DP'),
          ('modulos.legalizacao', 'Legalização'), ('financeiro.index', 'Financeiro'), ('modulos.analise', 'Análise'),
          ('relatorios.index', 'Relatórios'), ('atendimento.index', 'Atendimento'), ('novidades.index', 'Novidades'),
          ('configuracoes.index', 'Config.')]
_FIN = [('financeiro.index', 'Início'), ('financeiro.titulos', 'Cobranças'), ('financeiro.honorarios', 'Honorários'),
        ('financeiro.servicos', 'Serviços'), ('financeiro.lancamentos', 'Lançamentos'), ('financeiro.contratos', 'Contratos'),
        ('financeiro.extrato', 'Extrato'), ('financeiro.contas', 'Contas'), ('financeiro.programacoes', 'Programações'),
        ('financeiro.fluxo', 'Fluxo de caixa'), ('financeiro.dre', 'DRE'), ('financeiro.categorias', 'Categorias'),
        ('financeiro.empresas', 'Empresas'), ('financeiro.cobrancas', 'Notas e boletos')]


def acessos_de(u):
    from flask import current_app
    from utils.acesso import pode_rota_de
    from utils.db_helper import execute_query
    vf = current_app.view_functions

    def lista(itens):
        return [{'rot': r, 'pode': pode_rota_de(u, ep)} for ep, r in itens if ep in vf]
    perfis = execute_query("""SELECT p.nome FROM usuario_perfis up JOIN perfis_acesso p ON p.id = up.perfil_id
                               WHERE up.usuario_id = %s ORDER BY p.nome""", (u.id,), fetch=True) or []
    extras = execute_query('SELECT permissao_codigo FROM usuario_permissoes_extra WHERE usuario_id = %s ORDER BY permissao_codigo',
                           (u.id,), fetch=True) or []
    return {'areas': lista(_AREAS), 'fin': lista(_FIN), 'perfis': [p['nome'] for p in perfis],
            'extras': [e['permissao_codigo'] for e in extras]}


def contexto():
    """Para o base.html: a caixa da barra de cima (só para o admin de verdade)."""
    if not has_request_context() or request.endpoint in (None, 'static'):
        return {}
    if 'vc_ctx' in g:
        return {'vc': g.vc_ctx}
    a = admin_real()
    if not a:
        g.vc_ctx = None
        return {'vc': None}
    from models.cliente_responsavel import ClienteResponsavel
    pessoas = [p for p in ClienteResponsavel.usuarios_disponiveis() if p['id'] != a.id]
    vendo = vendo_como()
    g.vc_ctx = {'admin': a, 'vendo': vendo, 'pessoa': current_user._get_current_object() if vendo else None, 'pessoas': pessoas,
                'acessos': acessos_de(current_user) if vendo else None}
    return {'vc': g.vc_ctx}
