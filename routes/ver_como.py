"""Ver como (20/11/2026): a caixa "Escolher pessoa" da barra de cima. Só o admin de verdade. Ver utils/ver_como.py."""
from urllib.parse import urlparse
from flask import Blueprint, request, session, redirect, flash, abort
from flask_login import login_required
from utils import ver_como as VC
from utils.atividade import registrar

ver_como = Blueprint('ver_como', __name__)


def _volta():
    r = request.form.get('volta') or request.referrer or '/'
    p = urlparse(r)
    if p.netloc and p.netloc != request.host:
        return '/'
    return (p.path or '/') + ('?' + p.query if p.query else '')


@ver_como.route('/ver-como', methods=['POST'])
@login_required
def trocar():
    a = VC.admin_real()
    if not a:
        abort(403)
    uid = None if request.form.get('sair') else request.form.get('usuario_id', type=int)
    if uid and uid != a.id:
        from models.usuario import Usuario
        u = Usuario.get_by_id(uid)
        if not u or u.senha_pendente:
            flash('Essa pessoa não tem acesso ativo ao sistema.', 'warning')
            return redirect(_volta())
        session[VC.CHAVE] = uid
        registrar('leitura.entrou_como_funcionario', 'acesso', tabela='usuarios', registro_id=uid, depois={'como': u.nome})
        flash('Agora você vê o sistema como %s: o menu, as abas e as telas que a pessoa abre. Só para olhar (em Lançamentos dá para lançar no nome dela).' % u.nome, 'info')
    else:
        session.pop(VC.CHAVE, None)
        session.pop('fin_como', None)
        flash('Você voltou a ser você.', 'success')
    return redirect(_volta())
