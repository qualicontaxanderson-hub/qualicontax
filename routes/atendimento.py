# -*- coding: utf-8 -*-
"""Atendimento: chamados da equipe ao suporte (15/11/2026).

Anderson: "estão solicitando serviços pelo grupo de whatsapp e não estão sendo possível
atender todo mundo"; "a Graziella ia ver só os tickets dela"; "solicitar o acesso e a gente
liberar". Decisões dele: extra só para a pessoa existe; só o ADMIN atende; nada conclui
sozinho ("tudo tem que ser resolvido").

Quem vê o quê: todo logado abre chamado e vê SÓ os seus (conferido aqui, em
``_chamado_ou_404``); o admin vê todos. Mudar situação, liberar acesso e tirar extra são
``@admin_required``. Tabelas em migrations/atendimento_15_11.py.
"""
import base64
import json
import logging
import re

from flask import (Blueprint, Response, abort, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user

from utils.auth_helper import admin_required, login_required
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

atendimento = Blueprint('atendimento', __name__, url_prefix='/atendimento')

TIPOS = {
    'alterar':  ('Alterar ou corrigir algo', 'fa-pen', ''),
    'erro':     ('Erro na tela', 'fa-bug', 'at-tipo-erro'),
    'acesso':   ('Pedido de acesso', 'fa-key', 'at-tipo-acesso'),
    'duvida':   ('Dúvida', 'fa-circle-question', ''),
    'sugestao': ('Sugestão', 'fa-lightbulb', ''),
}
# chave: (rótulo para o suporte, rótulo para quem pediu, classe)
SITUACOES = {
    'novo':      ('Novo', 'Enviado', 'novo'),
    'andamento': ('Em atendimento', 'Em atendimento', 'and'),
    'usuario':   ('Aguardando usuário', 'Aguardando você', 'usu'),
    'suporte':   ('Aguardando suporte', 'Aguardando suporte', 'sup'),
    'concluido': ('Concluído', 'Resolvido', 'ok'),
}
ACOES_ROTULO = {'ver': 'Ver', 'inc': 'Incluir', 'alt': 'Alterar'}
PRINT_MAX = 3 * 1024 * 1024
PRINT_TIPOS = ('image/png', 'image/jpeg', 'image/webp', 'image/gif')


def _q(sql, p=(), one=False):
    return execute_query(sql, p, fetch=True, fetch_one=one) if one else (execute_query(sql, p, fetch=True) or [])


def _w(sql, p=()):
    return execute_query(sql, p, fetch=False)


def _admin():
    return current_user.is_authenticated and current_user.is_admin()


def _chamado_ou_404(cid):
    """O chamado, só para quem pediu ou para o admin. Dos outros: 404 (nem existe)."""
    c = _q("""SELECT c.*, u.nome AS usuario_nome, d.nome AS departamento,
                     cl.nome_razao_social AS cliente_nome, cl.numero_cliente AS cliente_numero,
                     TIMESTAMPDIFF(MINUTE, COALESCE(c.ultima_msg_em, c.criado_em), NOW()) AS espera_min
                FROM atend_chamados c
                JOIN usuarios u ON u.id = c.usuario_id
                LEFT JOIN departamentos d ON d.id = u.departamento_id
                LEFT JOIN clientes cl ON cl.id = c.cliente_id
               WHERE c.id = %s""", (cid,), one=True)
    if not c or (not _admin() and c['usuario_id'] != current_user.id):
        abort(404)
    return c


def _espera(minutos):
    m = int(minutos or 0)
    if m < 60:
        return f'{m} min'
    h, m = divmod(m, 60)
    if h < 24:
        return f'{h} h {m:02d}' if m else f'{h} h'
    d = h // 24
    return f'{d} dia' + ('s' if d > 1 else '')


def _ler_print():
    """(tipo, bytes) do print colado (base64) ou escolhido (arquivo); (None, None) sem print.
    Levanta ValueError com texto para a equipe quando não serve."""
    f = request.files.get('print')
    if f and f.filename:
        dados, tipo = f.read(), (f.mimetype or '')
    else:
        b64 = request.form.get('print_b64') or ''
        m = re.match(r'^data:(image/[\w.+-]+);base64,(.+)$', b64, re.S)
        if not m:
            return None, None
        tipo, dados = m.group(1), base64.b64decode(m.group(2))
    if tipo not in PRINT_TIPOS:
        raise ValueError('O print precisa ser uma imagem (PNG, JPG ou WEBP).')
    if len(dados) > PRINT_MAX:
        raise ValueError('O print passou de 3 MB. Recorte só a parte que importa e cole de novo.')
    return tipo, dados


def _mensagem(cid, autor, texto=None, interna=0, ptipo=None, pdados=None, usuario_id=None):
    _w("""INSERT INTO atend_mensagens (chamado_id, usuario_id, autor, texto, interna, print_tipo, print_dados)
          VALUES (%s, %s, %s, %s, %s, %s, %s)""",
       (cid, usuario_id if autor != 'sistema' else None, autor, texto, interna, ptipo, pdados))
    if autor in ('usuario', 'suporte') and not interna:
        _w("UPDATE atend_chamados SET ultima_msg_de = %s, ultima_msg_em = NOW() WHERE id = %s", (autor, cid))


def _nome_curto(nome):
    return (nome or '').split(' ')[0].title() or 'Alguém'


# ---------------------------------------------------------------------------
# Catálogo: as telas que a pessoa pode pedir
# ---------------------------------------------------------------------------
def _telas_para(usuario_perms):
    """[{grupo, telas:[{id, nome, acoes:[{k, rotulo, codigo, tem}]}]}] — só telas com
    alguma ação (Ver/Incluir/Alterar) que a pessoa ainda não tem. Excluir não se pede:
    o Anderson libera um a um."""
    from utils.permissions import DEPARTAMENTOS_PERMISSOES
    out = []
    for dep in DEPARTAMENTOS_PERMISSOES:
        telas = []
        for t in dep['telas']:
            acoes = [{'k': k, 'rotulo': ACOES_ROTULO[k], 'codigo': t[k][0], 'faz': t[k][1],
                      'tem': t[k][0] in usuario_perms}
                     for k in ('ver', 'inc', 'alt') if t.get(k)]
            if acoes and not all(a['tem'] for a in acoes):
                telas.append({'id': t['id'], 'nome': t['nome'], 'acoes': acoes})
        if telas:
            out.append({'grupo': dep['grupo'], 'telas': telas})
    return out


def _tela_do_catalogo(tela_id):
    from utils.permissions import DEPARTAMENTOS_PERMISSOES
    for dep in DEPARTAMENTOS_PERMISSOES:
        for t in dep['telas']:
            if t['id'] == tela_id:
                return dep, t
    return None, None


def _perfis_que_dao(codigos):
    """Perfis ativos que já dão TODOS os códigos pedidos, com o que mais trariam junto."""
    from utils.permissions import get_flat_catalog
    rotulo = get_flat_catalog()
    por = {}
    for r in _q("""SELECT pa.id, pa.nome, pp.permissao_codigo FROM perfis_acesso pa
                     JOIN perfil_permissoes pp ON pp.perfil_id = pa.id WHERE pa.situacao = 'ATIVO'"""):
        por.setdefault((r['id'], r['nome']), set()).add(r['permissao_codigo'])
    out = []
    for (pid, nome), cods in por.items():
        if set(codigos) <= cods:
            extra = sorted(rotulo.get(c, c) for c in cods - set(codigos))
            out.append({'id': pid, 'nome': nome, 'traz': extra})
    out.sort(key=lambda p: len(p['traz']))
    return out[:4]


# ---------------------------------------------------------------------------
# Sinal no menu
# ---------------------------------------------------------------------------
@atendimento.app_context_processor
def _sinal_atendimento():
    """Número no ícone do menu. Admin: chamados esperando o suporte (novos e respondidos).
    Demais: chamados com resposta do suporte que a pessoa ainda não abriu."""
    if not current_user.is_authenticated or request.endpoint in (None, 'static'):
        return {}
    try:
        if current_user.is_admin():
            r = _q("SELECT COUNT(*) n FROM atend_chamados WHERE situacao IN ('novo','suporte')", one=True)
        else:
            r = _q("""SELECT COUNT(*) n FROM atend_chamados
                       WHERE usuario_id = %s AND ultima_msg_de = 'suporte'
                         AND (visto_usuario_em IS NULL OR visto_usuario_em < ultima_msg_em)""",
                   (current_user.id,), one=True)
        return {'atend_sinal': int((r or {}).get('n') or 0)}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Telas
# ---------------------------------------------------------------------------
@atendimento.route('/')
@login_required
def index():
    ver = request.args.get('ver', '')
    base = """SELECT c.id, c.usuario_id, c.tipo, c.titulo, c.situacao, c.urgente, c.tela_nome, c.cliente_id,
                     c.avaliacao, c.ultima_msg_de, c.ultima_msg_em, c.visto_usuario_em, c.criado_em, c.concluido_em,
                     u.nome AS usuario_nome, d.nome AS departamento,
                     cl.nome_razao_social AS cliente_nome, cl.numero_cliente AS cliente_numero,
                     TIMESTAMPDIFF(MINUTE, COALESCE(c.ultima_msg_em, c.criado_em), NOW()) AS espera_min,
                     (SELECT m.texto FROM atend_mensagens m WHERE m.chamado_id = c.id AND m.interna = 0
                         AND m.autor <> 'sistema' ORDER BY m.id DESC LIMIT 1) AS ultimo_texto,
                     (SELECT COUNT(*) FROM atend_mensagens m WHERE m.chamado_id = c.id
                         AND m.print_dados IS NOT NULL AND m.interna = 0) AS prints
                FROM atend_chamados c
                JOIN usuarios u ON u.id = c.usuario_id
                LEFT JOIN departamentos d ON d.id = u.departamento_id
                LEFT JOIN clientes cl ON cl.id = c.cliente_id"""
    if _admin():
        if ver == 'extras':
            from utils.permissions import get_flat_catalog
            rot = get_flat_catalog()
            extras = _q("""SELECT e.usuario_id, e.permissao_codigo, e.concedido_em, e.chamado_id,
                                  u.nome AS usuario_nome, a.nome AS por_nome
                             FROM usuario_permissoes_extra e
                             JOIN usuarios u ON u.id = e.usuario_id
                             LEFT JOIN usuarios a ON a.id = e.concedido_por
                            ORDER BY u.nome, e.permissao_codigo""")
            for e in extras:
                e['rotulo'] = rot.get(e['permissao_codigo'], e['permissao_codigo'])
            return render_template('atendimento/painel.html', ver='extras', extras=extras,
                                   chamados=[], kpi={}, **_ctx())
        if ver == 'concluidos':
            chamados = _q(base + " WHERE c.situacao = 'concluido' ORDER BY c.concluido_em DESC LIMIT 200")
        else:
            chamados = _q(base + " WHERE c.situacao <> 'concluido' ORDER BY c.urgente DESC, espera_min DESC")
        kpi = _q("""SELECT SUM(situacao='novo') novo, SUM(situacao='suporte') suporte,
                           SUM(situacao='andamento') andamento, SUM(situacao='usuario') usuario,
                           SUM(situacao='concluido' AND DATE(concluido_em) = CURDATE()) hoje,
                           SUM(situacao <> 'concluido') abertos
                      FROM atend_chamados""", one=True) or {}
        return render_template('atendimento/painel.html', ver=ver or 'painel', chamados=chamados,
                               kpi=kpi, extras=[], **_ctx())
    chamados = _q(base + " WHERE c.usuario_id = %s ORDER BY c.situacao = 'concluido', "
                         "COALESCE(c.ultima_msg_em, c.criado_em) DESC LIMIT 300", (current_user.id,))
    media = _q("""SELECT AVG(TIMESTAMPDIFF(MINUTE, criado_em, concluido_em)) m FROM atend_chamados
                   WHERE usuario_id = %s AND situacao = 'concluido'""", (current_user.id,), one=True) or {}
    return render_template('atendimento/meus.html', chamados=chamados,
                           media=_espera(media['m']) if media.get('m') is not None else '—', **_ctx())


def _ctx():
    return {'TIPOS': TIPOS, 'SITUACOES': SITUACOES, 'espera': _espera, 'nome_curto': _nome_curto,
            'eh_admin': _admin()}


@atendimento.route('/<int:cid>')
@login_required
def chamado(cid):
    c = _chamado_ou_404(cid)
    if _admin() and c['situacao'] == 'novo':
        # O admin abriu: deixa de ser "novo" e a pessoa vê que alguém pegou.
        _w("UPDATE atend_chamados SET situacao = 'andamento' WHERE id = %s AND situacao = 'novo'", (cid,))
        _mensagem(cid, 'sistema', f'{_nome_curto(current_user.nome)} abriu o chamado')
        c = _chamado_ou_404(cid)
    if c['usuario_id'] == current_user.id:
        _w("UPDATE atend_chamados SET visto_usuario_em = NOW() WHERE id = %s", (cid,))
    msgs = _q("""SELECT m.id, m.autor, m.texto, m.interna, m.criado_em, m.print_tipo,
                        (m.print_dados IS NOT NULL) AS tem_print, u.nome AS usuario_nome
                   FROM atend_mensagens m LEFT JOIN usuarios u ON u.id = m.usuario_id
                  WHERE m.chamado_id = %s ORDER BY m.id""", (cid,))
    if not _admin():
        msgs = [m for m in msgs if not m['interna']]
    liberar = None
    if _admin() and c['tipo'] == 'acesso' and c['situacao'] != 'concluido':
        codigos = json.loads(c['acesso_codigos'] or '[]')
        from models.usuario import Usuario
        dono = Usuario.get_by_id(c['usuario_id'])
        ja = dono.get_permissoes() if dono else set()
        liberar = {'codigos': codigos, 'falta': [x for x in codigos if x not in ja],
                   'perfis': _perfis_que_dao(codigos)}
    perfis_dela = None
    if _admin():
        perfis_dela = [r['nome'] for r in _q(
            """SELECT pa.nome FROM usuario_perfis up JOIN perfis_acesso pa ON pa.id = up.perfil_id
                WHERE up.usuario_id = %s ORDER BY pa.nome""", (c['usuario_id'],))]
    return render_template('atendimento/chamado.html', c=c, msgs=msgs, liberar=liberar,
                           perfis_dela=perfis_dela, **_ctx())


@atendimento.route('/telas')
@login_required
def telas():
    """Telas que a pessoa ainda não tem (janela "Pedir acesso")."""
    return jsonify({'deps': _telas_para(current_user.get_permissoes() if not _admin() else set())})


@atendimento.route('/novo', methods=['POST'])
@login_required
def novo():
    tipo = request.form.get('tipo', '')
    texto = (request.form.get('texto') or '').strip()
    if tipo not in TIPOS:
        return jsonify({'ok': False, 'erro': 'Escolha o que você precisa.'}), 400
    try:
        ptipo, pdados = _ler_print()
    except ValueError as e:
        return jsonify({'ok': False, 'erro': str(e)}), 400

    acesso_codigos = acesso_rotulo = None
    if tipo == 'acesso':
        dep, t = _tela_do_catalogo(request.form.get('tela_id', ''))
        if not t:
            return jsonify({'ok': False, 'erro': 'Escolha a tela que você precisa.'}), 400
        ja = current_user.get_permissoes()
        pedidas = request.form.getlist('acoes')
        acoes = [k for k in ('ver', 'inc', 'alt')
                 if t.get(k) and t[k][0] not in ja and (k == 'ver' or k in pedidas)]
        if not acoes:
            return jsonify({'ok': False, 'erro': 'Você já tem esse acesso. Escolha Incluir ou Alterar, '
                                                 'ou abra um chamado de "Alterar ou corrigir algo".'}), 400
        acesso_codigos = json.dumps([t[k][0] for k in acoes])
        acesso_rotulo = f"{dep['grupo']} › {t['nome']} — {' e '.join(ACOES_ROTULO[k] for k in acoes)}"
        titulo = f"Acesso: {dep['grupo']} › {t['nome']}"
    else:
        if len(texto) < 5:
            return jsonify({'ok': False, 'erro': 'Conte em poucas palavras o que aconteceu.'}), 400
        titulo = texto.splitlines()[0][:157] + ('...' if len(texto.splitlines()[0]) > 157 else '')

    # 'empresa_tela', não 'cliente_id': o portão lê cliente_id do formulário e negaria o chamado inteiro
    cliente_id = request.form.get('empresa_tela') or None
    if cliente_id and not str(cliente_id).isdigit():
        cliente_id = None
    if cliente_id:
        from utils.acesso import pode_ver_empresa
        if not pode_ver_empresa(int(cliente_id)):
            cliente_id = None
    cid = execute_query(
        """INSERT INTO atend_chamados (usuario_id, tipo, titulo, situacao, urgente, tela_url, tela_nome,
                                       cliente_id, acesso_codigos, acesso_rotulo, ultima_msg_de, ultima_msg_em)
           VALUES (%s, %s, %s, 'novo', %s, %s, %s, %s, %s, %s, 'usuario', NOW())""",
        (current_user.id, tipo, titulo, 1 if request.form.get('urgente') else 0,
         (request.form.get('tela_url') or '')[:500] or None, (request.form.get('tela_nome') or '')[:200] or None,
         cliente_id, acesso_codigos, acesso_rotulo), fetch=False)
    if not cid or cid is True:
        return jsonify({'ok': False, 'erro': 'Não consegui gravar o chamado. Tente de novo.'}), 500
    if tipo == 'acesso':
        _mensagem(cid, 'sistema', f'{_nome_curto(current_user.nome)} pediu acesso a {acesso_rotulo}')
    if texto or pdados:
        _mensagem(cid, 'usuario', texto or None, 0, ptipo, pdados, current_user.id)
    _registrar('escrita.chamado_aberto', cid, {'tipo': tipo, 'titulo': titulo})
    return jsonify({'ok': True, 'id': cid, 'url': url_for('atendimento.chamado', cid=cid)})


@atendimento.route('/<int:cid>/responder', methods=['POST'])
@login_required
def responder(cid):
    c = _chamado_ou_404(cid)
    texto = (request.form.get('texto') or '').strip()
    acao = request.form.get('acao', 'responder')
    try:
        ptipo, pdados = _ler_print()
    except ValueError as e:
        return jsonify({'ok': False, 'erro': str(e)}), 400
    admin_aqui = _admin() and c['usuario_id'] != current_user.id
    if not texto and not pdados and not (admin_aqui and acao == 'concluir'):
        return jsonify({'ok': False, 'erro': 'Escreva a resposta.'}), 400
    if admin_aqui:
        if acao == 'interna':
            _mensagem(cid, 'suporte', texto, 1, ptipo, pdados, current_user.id)
            return jsonify({'ok': True})
        if texto or pdados:
            _mensagem(cid, 'suporte', texto or None, 0, ptipo, pdados, current_user.id)
        nova = {'aguardar': 'usuario', 'concluir': 'concluido'}.get(acao, 'andamento')
        _situacao(c, nova)
    else:
        _mensagem(cid, 'usuario', texto or None, 0, ptipo, pdados, current_user.id)
        if c['situacao'] != 'novo':
            _situacao(c, 'suporte', aviso=c['situacao'] == 'concluido')
        _w("UPDATE atend_chamados SET visto_usuario_em = NOW() WHERE id = %s", (cid,))
    return jsonify({'ok': True})


def _situacao(c, nova, aviso=True):
    if c['situacao'] == nova:
        return
    if nova == 'concluido':
        _w("""UPDATE atend_chamados SET situacao = 'concluido', concluido_em = NOW(), concluido_por = %s,
                     avaliacao = NULL WHERE id = %s""", (current_user.id, c['id']))
    else:
        _w("UPDATE atend_chamados SET situacao = %s, concluido_em = NULL WHERE id = %s", (nova, c['id']))
    if aviso:
        if c['situacao'] == 'concluido':
            txt = f'{_nome_curto(current_user.nome)} reabriu o chamado'
        else:
            txt = f'{_nome_curto(current_user.nome)} mudou para {SITUACOES[nova][0]}'
        _mensagem(c['id'], 'sistema', txt)
    _registrar('escrita.chamado_situacao', c['id'], {'situacao': nova}, {'situacao': c['situacao']})


@atendimento.route('/<int:cid>/situacao', methods=['POST'])
@admin_required
def situacao(cid):
    c = _chamado_ou_404(cid)
    nova = request.form.get('situacao', '')
    if nova not in SITUACOES or nova == 'novo':
        return jsonify({'ok': False, 'erro': 'Situação inválida.'}), 400
    _situacao(c, nova)
    return jsonify({'ok': True})


@atendimento.route('/<int:cid>/avaliar', methods=['POST'])
@login_required
def avaliar(cid):
    c = _chamado_ou_404(cid)
    if c['usuario_id'] != current_user.id or c['situacao'] != 'concluido':
        return jsonify({'ok': False, 'erro': 'Só quem pediu avalia, depois de resolvido.'}), 400
    resolveu = request.form.get('resolveu') == '1'
    _w("UPDATE atend_chamados SET avaliacao = %s WHERE id = %s", (1 if resolveu else 0, cid))
    if resolveu:
        _mensagem(cid, 'sistema', f'{_nome_curto(current_user.nome)} confirmou: resolveu')
    else:
        _mensagem(cid, 'sistema', f'{_nome_curto(current_user.nome)} disse que NÃO resolveu — o chamado voltou ao suporte')
        _w("UPDATE atend_chamados SET situacao = 'suporte', concluido_em = NULL WHERE id = %s", (cid,))
    _registrar('escrita.chamado_avaliado', cid, {'resolveu': resolveu})
    return jsonify({'ok': True})


@atendimento.route('/<int:cid>/liberar', methods=['POST'])
@admin_required
def liberar(cid):
    c = _chamado_ou_404(cid)
    if c['tipo'] != 'acesso':
        return jsonify({'ok': False, 'erro': 'Este chamado não é pedido de acesso.'}), 400
    from utils.permissions import normalizar_selecao, get_flat_catalog
    codigos = sorted(normalizar_selecao(json.loads(c['acesso_codigos'] or '[]')))
    if not codigos:
        return jsonify({'ok': False, 'erro': 'O pedido não tem tela válida do catálogo.'}), 400
    modo = request.form.get('modo', 'extra')
    if modo == 'perfil':
        pid = request.form.get('perfil_id', '')
        perfil = _q("SELECT id, nome FROM perfis_acesso WHERE id = %s AND situacao = 'ATIVO'",
                    (pid,), one=True) if str(pid).isdigit() else None
        if not perfil:
            return jsonify({'ok': False, 'erro': 'Escolha um perfil.'}), 400
        _w("INSERT IGNORE INTO usuario_perfis (usuario_id, perfil_id) VALUES (%s, %s)",
           (c['usuario_id'], perfil['id']))
        como = f'pelo perfil {perfil["nome"]}'
        depois = {'perfil': perfil['nome']}
    else:
        for cod in codigos:
            _w("""INSERT IGNORE INTO usuario_permissoes_extra (usuario_id, permissao_codigo, concedido_por, chamado_id)
                  VALUES (%s, %s, %s, %s)""", (c['usuario_id'], cod, current_user.id, cid))
        como = f'só para {_nome_curto(c["usuario_nome"])} (extra)'
        rot = get_flat_catalog()
        depois = {'extras': [rot.get(x, x) for x in codigos]}
    texto = (request.form.get('texto') or '').strip()
    _mensagem(cid, 'suporte', texto or f'Acesso liberado: {c["acesso_rotulo"]}. Já aparece no seu menu.',
              0, None, None, current_user.id)
    _mensagem(cid, 'sistema', f'{_nome_curto(current_user.nome)} liberou o acesso {como}')
    _situacao(c, 'concluido', aviso=False)
    try:
        from utils.atividade import registrar
        registrar('escrita.acesso_liberado', 'acesso', tabela='usuarios', registro_id=c['usuario_id'],
                  depois=dict(depois, chamado=cid, usuario=c['usuario_nome']))
    except Exception:
        logger.exception('[atendimento] auditoria do acesso liberado falhou')
    return jsonify({'ok': True})


@atendimento.route('/extras/remover', methods=['POST'])
@admin_required
def extra_remover():
    uid, cod = request.form.get('usuario_id', ''), request.form.get('codigo', '')
    if not uid.isdigit() or not cod:
        return jsonify({'ok': False, 'erro': 'Pedido incompleto.'}), 400
    _w("DELETE FROM usuario_permissoes_extra WHERE usuario_id = %s AND permissao_codigo = %s", (uid, cod))
    try:
        from utils.atividade import registrar
        registrar('escrita.acesso_extra_removido', 'acesso', tabela='usuarios', registro_id=int(uid),
                  antes={'extra': cod})
    except Exception:
        pass
    return jsonify({'ok': True})


@atendimento.route('/print/<int:mid>', endpoint='print')
@login_required
def ver_print(mid):
    m = _q("SELECT chamado_id, interna, print_tipo, print_dados FROM atend_mensagens WHERE id = %s",
           (mid,), one=True)
    if not m or not m['print_dados']:
        abort(404)
    _chamado_ou_404(m['chamado_id'])
    if m['interna'] and not _admin():
        abort(404)
    return Response(bytes(m['print_dados']), mimetype=m['print_tipo'] or 'image/png',
                    headers={'Cache-Control': 'private, max-age=86400'})


def _registrar(acao, cid, depois, antes=None):
    try:
        from utils.atividade import registrar
        registrar(acao, 'atendimento', tabela='atend_chamados', registro_id=cid, antes=antes, depois=depois)
    except Exception:
        pass
