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
    'Comercial':        ('#ffedd5', '#c2410c', 'fa-handshake'),
    'Escrita Fiscal':   ('#e0f2fe', '#0369a1', 'fa-file-invoice'),
    'Contábil':         ('#ede9fe', '#6d28d9', 'fa-calculator'),
    'DP / Legalização': ('#fee2e2', '#b91c1c', 'fa-id-card'),
    'Q-Robô':           ('#f1f5f9', '#334155', 'fa-robot'),
    'Processos':        ('#fef3c7', '#b45309', 'fa-list-check'),
    'Relatórios':       ('#ccfbf1', '#0f766e', 'fa-chart-column'),
    'Configurações':    ('#f1f5f9', '#475569', 'fa-gear'),
}
REACOES = ('util', 'duvida')


def _hoje_br():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()


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


def _codigos_da_area(area):
    """Os códigos de Ver de todas as telas de um assunto do catálogo (vazio para 'Todos')."""
    from utils.permissions import DEPARTAMENTOS_PERMISSOES
    for dep in DEPARTAMENTOS_PERMISSOES:
        if dep['grupo'] == area:
            return {t['ver'][0] for t in dep['telas'] if t.get('ver')}
    return set()


def _pode_ver(n, telas, perms=None, admin=None):
    """A pessoa vê este aviso? Vale a TELA, não o departamento (Anderson, 15/11/2026: quem lança
    contrato no Financeiro não ganha, por isso, as novidades de Cobrança e Títulos).

    - com tela: quem tem o Ver daquela tela;
    - sem tela, mas com assunto (ex.: Financeiro): quem tem o Ver de ALGUMA tela do assunto;
    - 'Para todos': todo mundo. Admin vê tudo."""
    cod = _codigo_da_tela(n.get('tela_id'), telas)
    precisa = {cod} if cod else _codigos_da_area(n.get('area') or '')
    if not precisa:
        return True
    if perms is None:
        return current_user.is_admin() or any(current_user.has_permission(c) for c in precisa)
    return bool(admin) or bool(precisa & perms)


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
    blocos = [[l.strip() for l in b.split('\n') if l.strip()]
              for b in (texto or '').replace('\r', '').split('\n\n')]
    blocos = [b for b in blocos if b]
    if not blocos:
        return ''
    limpa = lambda s: re.sub(r'^\s*(?:[-•*]|\d+[.)])\s+', '', s.replace('**', ''))  # noqa: E731
    t = ' '.join(limpa(l) for l in blocos[0])
    # Primeiro parágrafo curto que termina em ":" e é seguido de lista: o resumo leva os itens.
    if len(blocos) > 1 and (len(t) < 90 or t.endswith(':')) and re.match(r'^\s*(?:[-•*]|\d+[.)])\s+', blocos[1][0]):
        t = t.rstrip(':') + ': ' + '; '.join(limpa(l).rstrip('.;') for l in blocos[1]) + '.'
    return t

_SEL = """SELECT n.*, COALESCE(NULLIF(u.nick, ''), u.nome) AS por_nome
            FROM novidades n LEFT JOIN usuarios u ON u.id = COALESCE(n.publicada_por, n.criado_por)"""


DIAS_SEMANA = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']


def _quando(d, hoje):
    """'hoje', 'ontem', 'há 3 dias', 'há 2 semanas', 'há 3 meses' — para o cabeçalho do dia."""
    dif = (hoje - d).days
    if dif <= 0:
        return 'hoje'
    if dif == 1:
        return 'ontem'
    if dif < 7:
        return f'há {dif} dias'
    if dif < 30:
        s = dif // 7
        return 'há 1 semana' if s == 1 else f'há {s} semanas'
    m = (hoje.year - d.year) * 12 + hoje.month - d.month
    return 'há 1 mês' if m <= 1 else f'há {m} meses'


def _agrupar_dias(lista, hoje):
    """Como os vencimentos em Títulos a receber: um cabeçalho por dia e os cartões daquele dia.
    A lista já vem da mais nova para a mais antiga."""
    dias = []
    for n in lista:
        d = n['data']
        if not dias or dias[-1]['data'] != d:
            dias.append({'data': d, 'chave': d.isoformat(),
                         'rot': d.strftime('%d/%m') + ('' if d.year == hoje.year else f'/{d.year}'),
                         'sub': DIAS_SEMANA[d.weekday()] + ' · ' + _quando(d, hoje), 'hoje': d == hoje, 'itens': []})
        dias[-1]['itens'].append(n)
    return dias


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
        rows = _q("""SELECT n.id, n.tela_id, n.area FROM novidades n
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
    """Uma página só, no molde de Financeiro › Títulos a receber (Anderson, 16/11: "a ideia de lá está
    mais interessante"): os quadros coloridos de cima são as ABAS (Novas para você, Esta semana, Todas,
    Com dúvida e, para o admin, Rascunhos); embaixo a linha de TEMAS filtra, e as mudanças vêm agrupadas
    por dia. Ler a mudança (abrir a caixa) ou marcar Entendi é o que conta como visto."""
    telas, grupos = _catalogo()
    admin = current_user.is_admin()
    ver = request.args.get('ver', '') if admin else ''
    hoje = _hoje_br()          # o app roda em UTC: às 21h de domingo já seria "segunda" e o dia viraria "semana passada"
    lista = _q(_SEL + " WHERE n.situacao = 'publicada' ORDER BY n.data DESC, n.publicada_em DESC, n.id DESC LIMIT 300")
    if admin:
        lista += _q(_SEL + " WHERE n.situacao = 'rascunho' ORDER BY n.data DESC, n.id DESC")
    for n in lista:
        n.pop('imagem_dados', None)
    minhas = {r['novidade_id']: r for r in _q(
        "SELECT novidade_id, lida_em, reacao FROM novidades_lidas WHERE usuario_id = %s", (current_user.id,))}

    out, rasc = [], []
    for n in lista:
        publicada = n['situacao'] == 'publicada'
        if publicada and not _pode_ver(n, telas):
            continue
        m = minhas.get(n['id'])
        n['nova'] = publicada and not m
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
        n['tema'] = _tema_de(n, telas)
        n['zap'] = _texto_zap(n)
        (out if publicada else rasc).append(n)

    # Acabou de publicar? A página volta com ?zap=<id> e oferece o texto pronto do WhatsApp.
    zap_pub = None
    try:
        zid = int(request.args.get('zap') or 0)
    except ValueError:
        zid = 0
    if zid:
        zap_pub = next((n for n in out if n['id'] == zid), None)
        if zap_pub and admin:
            # Quem vê no sistema (sem os admins): é para essas pessoas que o WhatsApp deve ir.
            zap_pub['quem'] = [u['nome'] for u in _equipe()
                               if not u['admin'] and _pode_ver(zap_pub, telas, u['perms'], u['admin'])]
            zap_pub['para_todos'] = not zap_pub.get('tela_id') and zap_pub['area'] == 'Todos'

    # --- números: só dado real
    novas = [n['id'] for n in out if n['nova']]
    seg = hoje - timedelta(days=hoje.weekday())
    kpi = {
        'novas': len(novas),
        'semana': sum(1 for n in out if n['data'] >= seg),
        'total': len(out),
        'temas': len({n['tema'][0] for n in out}),
        'rascunhos': len(rasc),
    }
    equipe, equipe_leitura = [], []
    if admin and out:
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
    kpi['duvidas'] = sum(1 for n in out if (n.get('leitura') or {}).get('duvida')) if admin         else sum(1 for n in out if n['minha_reacao'] == 'duvida')

    # A linha de temas (a tela do catálogo ou o assunto) e os dias, como os vencimentos em Títulos.
    temas = _agrupar_temas(out + rasc, admin)
    tema = (request.args.get('tema') or '').strip()
    if tema and not any(t['chave'] == tema for t in temas):
        tema = ''
    if ver == 'rascunhos':
        aba = 'rasc'
    elif tema or zap_pub or not novas:
        aba = 'todas'
    else:
        aba = 'nova'
    return render_template('novidades/index.html', lista=out, rasc=rasc, kpi=kpi, zap_pub=zap_pub,
                           dias=_agrupar_dias(out, hoje), dias_rasc=_agrupar_dias(rasc, hoje),
                           temas=temas, tema_sel=tema, aba_ini=aba,
                           TIPOS=TIPOS, AREAS_VISUAL=AREAS_VISUAL,
                           eh_admin=admin, grupos=grupos, telas_por_grupo=_telas_por_grupo(telas),
                           equipe_leitura=equipe_leitura, hoje=hoje.isoformat())


ZAP_EMOJI = {'novo': '✨', 'melhoria': '📈', 'correcao': '🔧', 'aviso': '⚠️'}
ZAP_AREA = {'Financeiro': '💰', 'Comercial': '🤝', 'Cadastros': '📁', 'Escrita Fiscal': '🧾', 'Contábil': '📊', 'DP / Legalização': '🪪',
            'Q-Robô': '🤖', 'Processos': '✅', 'Relatórios': '📈', 'Configurações': '⚙️'}


def _texto_zap(n):
    """Mensagem pronta para o WhatsApp (negrito com *asterisco*, itálico com _sublinhado_):
    título, o primeiro parágrafo, a primeira lista (até 4 itens) e o caminho no sistema.
    Anderson: "copiar um texto bem elegante para eu colocar no WhatsApp"."""
    blocos = []
    for bloco in (n['texto'] or '').replace('\r', '').split('\n\n'):
        linhas = [l.strip() for l in bloco.split('\n') if l.strip()]
        if linhas:
            blocos.append(linhas)
    corpo = []
    if blocos:
        corpo.append(' '.join(blocos[0]).replace('**', '*'))
        for linhas in blocos[1:2]:
            if all(re.match(r'^(?:[-•*]|\d+[.)])\s+', l) for l in linhas):
                itens = [re.sub(r'^(?:[-•*])\s+', '• ', l).replace('**', '*') for l in linhas[:4]]
                if len(linhas) > 4:
                    itens.append('• …')
                corpo.append('\n'.join(itens))
    tipo_rotulo = TIPOS.get(n['tipo'], TIPOS['melhoria'])[0]
    tema_nome = n['tema'][1] if n.get('tema') else ('Para todos' if n['area'] == 'Todos' else n['area'])
    onde = 'Para todos' if n['area'] == 'Todos' else n['area']
    if n.get('tela_nome'):
        onde += ' › ' + n['tela_nome']
    if n.get('tela_nome'):
        vale = f"👥 Para quem usa *{n['tela_nome']}*. Não usa? Pode ignorar."
    elif n['area'] != 'Todos':
        vale = f"👥 Para quem usa as telas de *{n['area']}*. Não usa? Pode ignorar."
    else:
        vale = '👥 Para *toda a equipe*.'
    linha = '━━━━━━━━━━━━━━━━━━━━'
    # O departamento em destaque, logo abaixo do título (Anderson: "deixar mais claro qual o
    # departamento que está sendo alterado").
    if n['area'] == 'Todos':
        cabeca = ['🏢 Departamento: *TODOS* (o sistema inteiro)']
    else:
        cabeca = [f"{ZAP_AREA.get(n['area'], '🏢')} Departamento: *{n['area'].upper()}*"]
        if n.get('tela_nome'):
            cabeca.append(f"🖥️ Tela: *{n['tela_nome']}*")
    partes = [
        '📢 *NOVIDADE NO QUALICONTAX*',
        *cabeca,
        linha,
        f"{ZAP_EMOJI.get(n['tipo'], '✨')} *{n['titulo']}*",
        f'🏷️ {tipo_rotulo}',
        vale,
        '',
        '\n\n'.join(corpo),
        '',
        f'📣 *Leia com calma em Novidades › {tema_nome}* (menu da esquerda): texto completo, passo a passo e print. '
        'Ao terminar, toque em *Entendi*.',
        '',
        '🎧 *Dúvida ou algo não funcionou?* Clique em *Pedir ajuda*, o botão verde de qualquer tela. Nada de grupo: '
        'o chamado já vai com a tela e o print, e a resposta chega em *Atendimento*.',
        linha,
        '_Bom trabalho!_ 💚',
    ]
    return '\n'.join(partes)


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
    n = _q("SELECT id, tela_id, area, situacao FROM novidades WHERE id = %s", (nid,), one=True)
    if not n or n['situacao'] != 'publicada' or not _pode_ver(n, _catalogo()[0]):
        abort(404)
    _w("""INSERT INTO novidades_lidas (usuario_id, novidade_id, reacao) VALUES (%s, %s, %s)
          ON DUPLICATE KEY UPDATE reacao = VALUES(reacao)""", (current_user.id, nid, r))
    return jsonify(ok=True, reacao=r)


@novidades.route('/<int:nid>/quem-viu')
@admin_required
def quem_viu(nid):
    n = _q("SELECT id, titulo, tela_id, area FROM novidades WHERE id = %s", (nid,), one=True)
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
    n = _q("SELECT tela_id, area, situacao, imagem_tipo, imagem_dados FROM novidades WHERE id = %s", (nid,), one=True)
    if not n or not n['imagem_dados']:
        abort(404)
    if not current_user.is_admin() and (n['situacao'] != 'publicada' or not _pode_ver(n, _catalogo()[0])):
        abort(404)
    return Response(n['imagem_dados'], mimetype=n['imagem_tipo'] or 'image/png',
                    headers={'Cache-Control': 'private, max-age=86400'})


# ---------------------------------------------------------------------------
# Admin: escrever, publicar, tirar do ar
# ---------------------------------------------------------------------------
@novidades.route('/previa', methods=['POST'])
@admin_required
def previa():
    """Prévia ao vivo do editor: o cartão como a equipe vê, o texto do WhatsApp e PARA QUEM vai
    aparecer (nomes), calculados pelo servidor — uma regra só, a mesma da publicação."""
    f = request.form
    telas, grupos = _catalogo()
    tela_id = (f.get('tela_id') or '').strip() or None
    if tela_id and tela_id not in telas:
        tela_id = None
    area = telas[tela_id][0] if tela_id else (f.get('area') if f.get('area') in grupos else 'Todos')
    n = {'id': 0, 'titulo': (f.get('titulo') or '').strip() or 'Título da novidade',
         'texto': (f.get('texto') or '').strip(), 'tipo': f.get('tipo') if f.get('tipo') in TIPOS else 'melhoria',
         'area': area, 'tela_id': tela_id, 'tela_nome': telas.get(tela_id or '', (None, None))[1]}
    n['tema'] = _tema_de(n, telas)
    quem = [u['nome'] for u in _equipe() if not u['admin'] and _pode_ver(n, telas, u['perms'], u['admin'])]
    blocos = _blocos(n['texto'])
    html = ''.join(
        f'<p>{c}</p>' if t == 'p' else
        ('<ul>' if t == 'ul' else '<ol>') + ''.join(f'<li>{i}</li>' for i in c) + ('</ul>' if t == 'ul' else '</ol>')
        for t, c in blocos)
    return jsonify(ok=True, resumo=_resumo(n['texto']), html=html, zap=_texto_zap(n), quem=quem,
                   para_todos=not tela_id and area == 'Todos', area=area, tela_nome=n['tela_nome'],
                   tipo_rotulo=TIPOS[n['tipo']][0], tema=n['tema'][1])



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
        data = _hoje_br()
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
    return jsonify(ok=True, id=nid, ir=url_for('novidades.index', zap=nid) if publicar
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
