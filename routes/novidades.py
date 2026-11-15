# -*- coding: utf-8 -*-
"""Novidades: o que mudou no sistema, contado para a equipe (15/11/2026).

Anderson: "quero um outro item nessa barra que seriam as novidades ou alterações do sistema".

Todo logado lê. Só o admin escreve, publica, tira do ar e descarta rascunho. O aviso com
``tela_id`` aparece só para quem tem acesso àquela tela (não adianta contar ao DP uma
mudança numa tela que ele não abre) e ganha o atalho "Abrir". Abrir a página marca como
visto o que estava na tela; o selo NOVO fica naquela visita. A pessoa reage ("Entendi" ou
"Tenho dúvida" — a dúvida abre um chamado no Atendimento) e o admin vê quem já viu e quem
ainda não. Rascunhos nascem das publicações do sistema por scripts/novidades_rascunhos.py.
Tabelas em migrations/novidades_15_11.py.
"""
import logging
import re
from datetime import date, datetime, timedelta

from flask import Blueprint, Response, abort, jsonify, render_template, request, url_for
from flask_login import current_user
from markupsafe import Markup, escape
from werkzeug.routing import BuildError

from utils.auth_helper import admin_required, login_required
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

novidades = Blueprint('novidades', __name__, url_prefix='/novidades')

SELO_DIAS = 45          # o número no menu conta só o recente (quem chega não herda o histórico)
MESES = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez')
MESES_LONGO = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto',
               'setembro', 'outubro', 'novembro', 'dezembro')
# chave: (rótulo, ícone, classe)
TIPOS = {
    'novo':     ('Nova função', 'fa-wand-magic-sparkles', 'nv-t-novo'),
    'melhoria': ('Melhoria', 'fa-arrow-trend-up', 'nv-t-melh'),
    'correcao': ('Correção', 'fa-screwdriver-wrench', 'nv-t-corr'),
    'aviso':    ('Aviso', 'fa-circle-exclamation', 'nv-t-aviso'),
}
# assunto: (fundo, cor, ícone) — as cores dos itens do menu
AREAS_VISUAL = {
    'Todos':            ('#ffedd5', '#c2410c', 'fa-bullhorn'),
    'Cadastros':        ('#dbeafe', '#1d4ed8', 'fa-folder-open'),
    'Financeiro':       ('#dcfce7', '#15803d', 'fa-dollar-sign'),
    'Escrita Fiscal':   ('#e0f2fe', '#0369a1', 'fa-file-invoice'),
    'Contábil':         ('#ede9fe', '#6d28d9', 'fa-calculator'),
    'DP / Legalização': ('#fee2e2', '#b91c1c', 'fa-id-card'),
    'Q-Robô':           ('#f1f5f9', '#334155', 'fa-robot'),
    'Processos':        ('#fef3c7', '#b45309', 'fa-list-check'),
    'Relatórios':       ('#ccfbf1', '#0f766e', 'fa-chart-column'),
    'Configurações':    ('#f1f5f9', '#475569', 'fa-gear'),
}
REACOES = ('util', 'duvida')


def _q(sql, p=(), one=False):
    return execute_query(sql, p, fetch=True, fetch_one=one) if one else (execute_query(sql, p, fetch=True) or [])


def _w(sql, p=()):
    return execute_query(sql, p, fetch=False)


# ---------------------------------------------------------------------------
# Catálogo e equipe
# ---------------------------------------------------------------------------
def _catalogo():
    """{tela_id: (grupo, nome, codigo_ver)} e a lista de grupos, do catálogo de permissões."""
    from utils.permissions import DEPARTAMENTOS_PERMISSOES
    telas, grupos = {}, []
    for dep in DEPARTAMENTOS_PERMISSOES:
        grupos.append(dep['grupo'])
        for t in dep['telas']:
            telas[t['id']] = (dep['grupo'], t['nome'], (t.get('ver') or (None,))[0])
    return telas, grupos


def _codigo_da_tela(tela_id, telas):
    t = telas.get(tela_id or '')
    return t[2] if t else None


def _pode_ver(n, telas, perms=None, admin=None):
    """A pessoa vê este aviso? Sem tela, todos; com tela, quem tem o Ver dela (ou admin)."""
    cod = _codigo_da_tela(n.get('tela_id'), telas)
    if not cod:
        return True
    if perms is None:
        return current_user.is_admin() or current_user.has_permission(cod)
    return bool(admin) or cod in perms


def _atalho(tela_id, telas):
    t = telas.get(tela_id or '')
    if not t or not t[2]:
        return None
    try:
        return {'url': url_for(t[2]), 'rotulo': t[1]}
    except (BuildError, ValueError):
        return None


def _equipe():
    """Funcionários ativos com o conjunto de permissões de cada um (perfil + extra).
    [{id, nome, admin, perms}] — para saber quem pode ver cada aviso e quem já viu."""
    us = _q("""SELECT u.id, COALESCE(NULLIF(u.nick, ''), u.nome) AS nome, u.tipo_usuario,
                      d.nome AS departamento
                 FROM usuarios u LEFT JOIN departamentos d ON d.id = u.departamento_id
                WHERE u.situacao = 'ATIVO' AND u.classe_conta = 'FUNCIONARIO' ORDER BY u.nome""")
    perms = {}
    for r in _q("""SELECT up.usuario_id, pp.permissao_codigo FROM usuario_perfis up
                     JOIN perfil_permissoes pp ON pp.perfil_id = up.perfil_id"""):
        perms.setdefault(r['usuario_id'], set()).add(r['permissao_codigo'])
    for r in _q("SELECT usuario_id, permissao_codigo FROM usuario_permissoes_extra"):
        perms.setdefault(r['usuario_id'], set()).add(r['permissao_codigo'])
    return [{'id': u['id'], 'nome': u['nome'], 'departamento': u['departamento'],
             'admin': u['tipo_usuario'] == 'ADMIN', 'perms': perms.get(u['id'], set())} for u in us]


# ---------------------------------------------------------------------------
# Texto → blocos (parágrafo, lista, passos)
# ---------------------------------------------------------------------------
_NEG = re.compile(r'\*\*(.+?)\*\*')


def _linha(s):
    return Markup(_NEG.sub(r'<b>\1</b>', str(escape(s))))


def _blocos(texto):
    """Parágrafos separados por linha em branco; linhas "- item" viram lista e "1. passo"
    viram passo a passo; **assim** fica em negrito. Devolve [(tipo, conteúdo)]."""
    out = []
    for bloco in (texto or '').replace('\r', '').split('\n\n'):
        linhas = [l.rstrip() for l in bloco.split('\n') if l.strip()]
        if not linhas:
            continue
        if all(re.match(r'^\s*[-•*]\s+', l) for l in linhas):
            out.append(('ul', [_linha(re.sub(r'^\s*[-•*]\s+', '', l)) for l in linhas]))
        elif all(re.match(r'^\s*\d+[.)]\s+', l) for l in linhas):
            out.append(('ol', [_linha(re.sub(r'^\s*\d+[.)]\s+', '', l)) for l in linhas]))
        else:
            out.append(('p', Markup('<br>').join(_linha(l) for l in linhas)))
    return out


def _resumo(texto):
    """O primeiro parágrafo, em texto puro, para o cartão fechado."""
    for bloco in (texto or '').replace('\r', '').split('\n\n'):
        t = ' '.join(l.strip() for l in bloco.split('\n') if l.strip())
        if t:
            return re.sub(r'^\s*(?:[-•*]|\d+[.)])\s+', '', t.replace('**', ''))
    return ''

_SEL = """SELECT n.*, COALESCE(NULLIF(u.nick, ''), u.nome) AS por_nome
            FROM novidades n LEFT JOIN usuarios u ON u.id = COALESCE(n.publicada_por, n.criado_por)"""


def _periodo(d, hoje):
    """Cabeçalho do grupo na lista."""
    seg = hoje - timedelta(days=hoje.weekday())
    if d >= seg:
        return (0, 'Esta semana')
    if d >= seg - timedelta(days=7):
        return (1, 'Semana passada')
    if d.year == hoje.year and d.month == hoje.month:
        return (2, 'Antes, em ' + MESES_LONGO[d.month - 1])
    return (3 + (hoje.year - d.year) * 12 + (hoje.month - d.month),
            MESES_LONGO[d.month - 1].capitalize() + (f' de {d.year}' if d.year != hoje.year else ''))


# ---------------------------------------------------------------------------
# Sinal no menu
# ---------------------------------------------------------------------------
@novidades.app_context_processor
def _sinal_novidades():
    """Número verde no menu: avisos publicados nos últimos SELO_DIAS que a pessoa ainda não viu."""
    if not current_user.is_authenticated or request.endpoint in (None, 'static'):
        return {}
    try:
        rows = _q("""SELECT n.id, n.tela_id FROM novidades n
                      LEFT JOIN novidades_lidas l ON l.novidade_id = n.id AND l.usuario_id = %s
                     WHERE n.situacao = 'publicada' AND l.novidade_id IS NULL
                       AND n.data >= CURDATE() - INTERVAL %s DAY""", (current_user.id, SELO_DIAS))
        if not rows:
            return {'novid_sinal': 0}
        telas, _ = _catalogo()
        return {'novid_sinal': sum(1 for r in rows if _pode_ver(r, telas))}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# A página
# ---------------------------------------------------------------------------
@novidades.route('/')
@login_required
def index():
    telas, grupos = _catalogo()
    admin = current_user.is_admin()
    ver = request.args.get('ver', '') if admin else ''
    hoje = date.today()
    if ver == 'rascunhos':
        lista = _q(_SEL + " WHERE n.situacao = 'rascunho' ORDER BY n.data DESC, n.id DESC")
    else:
        lista = _q(_SEL + " WHERE n.situacao = 'publicada' ORDER BY n.data DESC, n.publicada_em DESC, n.id DESC LIMIT 300")
    for n in lista:
        n.pop('imagem_dados', None)
    minhas = {r['novidade_id']: r for r in _q(
        "SELECT novidade_id, lida_em, reacao FROM novidades_lidas WHERE usuario_id = %s", (current_user.id,))}

    out = []
    for n in lista:
        if not _pode_ver(n, telas):
            continue
        m = minhas.get(n['id'])
        n['nova'] = ver != 'rascunhos' and not m
        n['minha_reacao'] = (m or {}).get('reacao')
        n['atalho'] = _atalho(n.get('tela_id'), telas)
        n['tela_nome'] = telas.get(n.get('tela_id') or '', (None, None))[1]
        d = n['data']
        n['dia'], n['mes'] = d.day, MESES[d.month - 1] + ('' if d.year == hoje.year else f' {d.year % 100:02d}')
        n['blocos'] = _blocos(n['texto'])
        n['resumo'] = _resumo(n['texto'])
        n['tipo_v'] = TIPOS.get(n['tipo'], TIPOS['melhoria'])
        n['area_v'] = AREAS_VISUAL.get(n['area'], AREAS_VISUAL['Todos'])
        n['periodo'] = _periodo(d, hoje)
        n['tem_imagem'] = bool(n.get('imagem_tipo'))
        out.append(n)

    # Dois níveis (Anderson: "quero que apareça o tema e ao clicar traz as modificações"):
    # sem ?tema= a página mostra os TEMAS (a tela do catálogo, ou o assunto); com ?tema= mostra
    # as mudanças daquele tema. Só abrir o tema conta como visto.
    tema = (request.args.get('tema') or '').strip()
    for n in out:
        n['tema'] = _tema_de(n, telas)
    modo = 'lista' if (ver == 'rascunhos' or tema) else 'temas'
    tema_info = None
    if tema and ver != 'rascunhos':
        out = [n for n in out if n['tema'][0] == tema]
        if out:
            tema_info = {'chave': tema, 'nome': out[0]['tema'][1], 'area': out[0]['tema'][2],
                         'atalho': out[0]['atalho'], 'area_v': out[0]['area_v']}
        else:
            tema_info = {'chave': tema, 'nome': _nome_tema(tema, telas), 'area': '', 'atalho': None,
                         'area_v': AREAS_VISUAL['Todos']}
    novas = [n['id'] for n in out if n['nova']]
    if novas and modo == 'lista' and ver != 'rascunhos':   # abriu o tema = viu as mudanças dele
        try:
            _w("INSERT IGNORE INTO novidades_lidas (usuario_id, novidade_id) VALUES "
               + ','.join(['(%s,%s)'] * len(novas)),
               tuple(v for i in novas for v in (current_user.id, i)))
        except Exception:
            logger.exception('novidades: não marcou como visto')

    # --- números: só dado real
    seg = hoje - timedelta(days=hoje.weekday())
    kpi = {
        'novas': len(novas),
        'semana': sum(1 for n in out if n['data'] >= seg),
        'mes': sum(1 for n in out if n['data'].year == hoje.year and n['data'].month == hoje.month),
        'total': len(out),
        'rascunhos': 0,
    }
    equipe, leitura, equipe_leitura = [], {}, []
    if admin:
        kpi['rascunhos'] = (_q("SELECT COUNT(*) n FROM novidades WHERE situacao = 'rascunho'", one=True) or {}).get('n') or 0
        if ver != 'rascunhos' and out:
            equipe = _equipe()
            ids = [n['id'] for n in out]
            vistos = {}
            for r in _q("SELECT novidade_id, usuario_id, reacao FROM novidades_lidas WHERE novidade_id IN (%s)"
                        % ','.join(['%s'] * len(ids)), tuple(ids)):
                vistos.setdefault(r['novidade_id'], {})[r['usuario_id']] = r['reacao']
            por_pessoa = {u['id']: [0, 0] for u in equipe}      # viu, podia ver
            for n in out:
                podem = [u for u in equipe if _pode_ver(n, telas, u['perms'], u['admin'])]
                v = vistos.get(n['id'], {})
                viram = [u for u in podem if u['id'] in v]
                n['leitura'] = {'podem': len(podem), 'viram': len(viram),
                                'util': sum(1 for u in viram if v[u['id']] == 'util'),
                                'duvida': sum(1 for u in viram if v[u['id']] == 'duvida')}
                for u in podem:
                    por_pessoa[u['id']][1] += 1
                    if u['id'] in v:
                        por_pessoa[u['id']][0] += 1
            equipe_leitura = sorted(
                [{'id': u['id'], 'nome': u['nome'], 'departamento': u['departamento'],
                  'viu': por_pessoa[u['id']][0], 'podia': por_pessoa[u['id']][1]}
                 for u in equipe if u['id'] != current_user.id and por_pessoa[u['id']][1]],
                key=lambda x: (x['viu'] / x['podia'], x['nome']))
    # Dúvidas: admin vê as da equipe; a pessoa, as que ela mesma marcou.
    kpi['duvidas'] = sum(1 for n in out if (n.get('leitura') or {}).get('duvida')) if admin \
        else sum(1 for n in out if n['minha_reacao'] == 'duvida')
    areas, tipos_n = {}, {}
    for n in out:
        areas[n['area']] = areas.get(n['area'], 0) + 1
        tipos_n[n['tipo']] = tipos_n.get(n['tipo'], 0) + 1
    temas = _agrupar_temas(out, admin) if modo == 'temas' else []
    kpi['temas'] = len(temas)
    kpi['temas_novos'] = sum(1 for t in temas if t['novas'])
    return render_template('novidades/index.html', lista=out, ver=ver or 'publicadas', kpi=kpi,
                           modo=modo, temas=temas, tema_info=tema_info,
                           areas=areas, tipos_n=tipos_n, TIPOS=TIPOS, AREAS_VISUAL=AREAS_VISUAL,
                           eh_admin=admin, grupos=grupos, telas_por_grupo=_telas_por_grupo(telas),
                           equipe_leitura=equipe_leitura, hoje=hoje.isoformat())


def _tema_de(n, telas):
    """(chave, nome, área) do tema de uma mudança: a tela do catálogo, ou o assunto inteiro."""
    t = telas.get(n.get('tela_id') or '')
    if t:
        return (n['tela_id'], t[1], t[0])
    area = n['area']
    return ('a:' + area, 'Para todos' if area == 'Todos' else area + ' (geral)', area)


def _nome_tema(chave, telas):
    if chave.startswith('a:'):
        return 'Para todos' if chave[2:] == 'Todos' else chave[2:] + ' (geral)'
    return telas.get(chave, (None, chave))[1]


def _agrupar_temas(out, admin):
    """Um cartão por tema, com o que a pessoa precisa para decidir se abre."""
    temas = {}
    for n in out:              # out vem da mais nova para a mais antiga
        k, nome, area = n['tema']
        t = temas.setdefault(k, {
            'chave': k, 'nome': nome, 'area': area, 'area_v': n['area_v'], 'atalho': n['atalho'],
            'n': 0, 'novas': 0, 'duvidas': 0, 'semana': 0, 'ultima': n['data'], 'titulos': [],
            'tipos': {}, 'podem': 0, 'viram': 0, 'util': 0,
        })
        t['n'] += 1
        t['novas'] += 1 if n['nova'] else 0
        t['semana'] += 1 if n['periodo'][0] == 0 else 0
        t['tipos'][n['tipo']] = t['tipos'].get(n['tipo'], 0) + 1
        if len(t['titulos']) < 3:
            t['titulos'].append({'id': n['id'], 'titulo': n['titulo'], 'data': n['data'], 'nova': n['nova'],
                                 'tipo_v': n['tipo_v']})
        if admin and n.get('leitura'):
            t['podem'] += n['leitura']['podem']; t['viram'] += n['leitura']['viram']
            t['util'] += n['leitura']['util']; t['duvidas'] += n['leitura']['duvida']
        elif n['minha_reacao'] == 'duvida':
            t['duvidas'] += 1
    lista = list(temas.values())
    for t in lista:
        t['pct'] = int(round(100 * t['viram'] / t['podem'])) if t['podem'] else 0
        t['tipos_txt'] = ', '.join(f"{c} {TIPOS[k][0].lower()}{'s' if c > 1 and k != 'novo' else ''}"
                                   for k, c in sorted(t['tipos'].items(), key=lambda x: -x[1]))
        if t['tipos'].get('novo', 0) > 1:
            t['tipos_txt'] = t['tipos_txt'].replace('nova função', 'novas funções')
        t['busca'] = (t['nome'] + ' ' + t['area'] + ' ' + ' '.join(x['titulo'] for x in t['titulos'])).lower()
    lista.sort(key=lambda t: (t['novas'] > 0, t['ultima'], t['n']), reverse=True)
    return lista


def _telas_por_grupo(telas):
    g = {}
    for tid, (grupo, nome, cod) in telas.items():
        g.setdefault(grupo, []).append({'id': tid, 'nome': nome})
    return g


# ---------------------------------------------------------------------------
# Quem lê
# ---------------------------------------------------------------------------
@novidades.route('/<int:nid>/reagir', methods=['POST'])
@login_required
def reagir(nid):
    """Entendi (util) / Tenho dúvida (duvida) / nada. A dúvida também abre chamado, pela tela."""
    r = request.form.get('reacao') or None
    if r is not None and r not in REACOES:
        return jsonify(ok=False, erro='Reação inválida.'), 400
    n = _q("SELECT id, tela_id, situacao FROM novidades WHERE id = %s", (nid,), one=True)
    if not n or n['situacao'] != 'publicada' or not _pode_ver(n, _catalogo()[0]):
        abort(404)
    _w("""INSERT INTO novidades_lidas (usuario_id, novidade_id, reacao) VALUES (%s, %s, %s)
          ON DUPLICATE KEY UPDATE reacao = VALUES(reacao)""", (current_user.id, nid, r))
    return jsonify(ok=True, reacao=r)


@novidades.route('/<int:nid>/quem-viu')
@admin_required
def quem_viu(nid):
    n = _q("SELECT id, titulo, tela_id FROM novidades WHERE id = %s", (nid,), one=True)
    if not n:
        abort(404)
    telas, _ = _catalogo()
    v = {r['usuario_id']: r for r in _q(
        "SELECT usuario_id, lida_em, reacao FROM novidades_lidas WHERE novidade_id = %s", (nid,))}
    viram, faltam = [], []
    for u in _equipe():
        if not _pode_ver(n, telas, u['perms'], u['admin']):
            continue
        if u['id'] in v:
            r = v[u['id']]
            viram.append({'nome': u['nome'], 'departamento': u['departamento'], 'reacao': r['reacao'],
                          'quando': r['lida_em'].strftime('%d/%m %H:%M') if r['lida_em'] else ''})
        else:
            faltam.append({'nome': u['nome'], 'departamento': u['departamento']})
    return jsonify(ok=True, titulo=n['titulo'], viram=viram, faltam=faltam)


@novidades.route('/<int:nid>/imagem')
@login_required
def imagem(nid):
    n = _q("SELECT tela_id, situacao, imagem_tipo, imagem_dados FROM novidades WHERE id = %s", (nid,), one=True)
    if not n or not n['imagem_dados']:
        abort(404)
    if not current_user.is_admin() and (n['situacao'] != 'publicada' or not _pode_ver(n, _catalogo()[0])):
        abort(404)
    return Response(n['imagem_dados'], mimetype=n['imagem_tipo'] or 'image/png',
                    headers={'Cache-Control': 'private, max-age=86400'})


# ---------------------------------------------------------------------------
# Admin: escrever, publicar, tirar do ar
# ---------------------------------------------------------------------------
@novidades.route('/salvar', methods=['POST'])
@admin_required
def salvar():
    from routes.atendimento import _ler_print
    f = request.form
    titulo = (f.get('titulo') or '').strip()[:160]
    texto = (f.get('texto') or '').strip()
    if not titulo or not texto:
        return jsonify(ok=False, erro='Preencha o título e o texto.'), 400
    telas, grupos = _catalogo()
    tipo = f.get('tipo') if f.get('tipo') in TIPOS else 'melhoria'
    tela_id = (f.get('tela_id') or '').strip() or None
    if tela_id and tela_id not in telas:
        tela_id = None
    area = telas[tela_id][0] if tela_id else (f.get('area') if f.get('area') in grupos else 'Todos')
    try:
        data = datetime.strptime(f.get('data') or '', '%Y-%m-%d').date()
    except ValueError:
        data = date.today()
    try:
        img_tipo, img_dados = _ler_print()
    except ValueError as e:
        return jsonify(ok=False, erro=str(e)), 400
    tirar_imagem = f.get('tirar_imagem') == '1'
    publicar = f.get('acao') == 'publicar'
    nid = int(f.get('id') or 0)
    antes = _q("SELECT id, titulo, situacao, publicada_em FROM novidades WHERE id = %s", (nid,), one=True) if nid else None
    if nid and not antes:
        return jsonify(ok=False, erro='Esta novidade não existe mais.'), 404
    if antes:
        sit = 'publicada' if publicar or antes['situacao'] == 'publicada' else 'rascunho'
        sets, vals = ['titulo=%s', 'texto=%s', 'tipo=%s', 'area=%s', 'tela_id=%s', 'data=%s', 'situacao=%s'], \
                     [titulo, texto, tipo, area, tela_id, data, sit]
        if img_dados:
            sets += ['imagem_tipo=%s', 'imagem_dados=%s']; vals += [img_tipo, img_dados]
        elif tirar_imagem:
            sets += ['imagem_tipo=NULL', 'imagem_dados=NULL']
        if sit == 'publicada' and not antes['publicada_em']:
            sets += ['publicada_por=%s', 'publicada_em=NOW()']; vals += [current_user.id]
        vals.append(nid)
        _w(f"UPDATE novidades SET {', '.join(sets)} WHERE id = %s", tuple(vals))
    else:
        nid = _w("""INSERT INTO novidades (titulo, texto, tipo, area, tela_id, imagem_tipo, imagem_dados, data,
                                          situacao, criado_por, publicada_por, publicada_em)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, IF(%s, NOW(), NULL))""",
                 (titulo, texto, tipo, area, tela_id, img_tipo, img_dados, data,
                  'publicada' if publicar else 'rascunho', current_user.id,
                  current_user.id if publicar else None, publicar))
    _registrar('escrita.novidade_publicada' if publicar else 'escrita.novidade_salva', nid,
               {'titulo': titulo, 'area': area, 'tipo': tipo}, {'titulo': antes['titulo']} if antes else None)
    return jsonify(ok=True, id=nid, ir=url_for('novidades.index') if publicar
                   else url_for('novidades.index', ver='rascunhos'))


@novidades.route('/<int:nid>/situacao', methods=['POST'])
@admin_required
def situacao(nid):
    """Publicar um rascunho como está, tirar do ar (volta a rascunho) ou descartar."""
    nova = request.form.get('situacao')
    if nova not in ('publicada', 'rascunho', 'descartada'):
        return jsonify(ok=False, erro='Situação inválida.'), 400
    n = _q("SELECT id, titulo, situacao, publicada_em FROM novidades WHERE id = %s", (nid,), one=True)
    if not n:
        return jsonify(ok=False, erro='Esta novidade não existe mais.'), 404
    if nova == 'publicada' and not n['publicada_em']:
        _w("UPDATE novidades SET situacao = %s, publicada_por = %s, publicada_em = NOW() WHERE id = %s",
           (nova, current_user.id, nid))
    else:
        _w("UPDATE novidades SET situacao = %s WHERE id = %s", (nova, nid))
    _registrar('escrita.novidade_' + nova, nid, {'situacao': nova, 'titulo': n['titulo']}, {'situacao': n['situacao']})
    return jsonify(ok=True)


def _registrar(acao, nid, depois, antes=None):
    try:
        from utils.atividade import registrar
        registrar(acao, 'novidades', tabela='novidades', registro_id=nid, antes=antes, depois=depois)
    except Exception:
        logger.debug('novidades: auditoria não registrou', exc_info=True)
