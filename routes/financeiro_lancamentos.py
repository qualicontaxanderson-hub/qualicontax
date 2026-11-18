# -*- coding: utf-8 -*-
"""Lançamentos de serviço, aprovação, cobrança avulsa e comissões (parte 6, etapa 2 — 06/11/2026).

Quem não é admin vê SÓ o que lançou (e só os totais dele). O admin (Anderson) vê tudo, aprova,
reprova, cadastra os acordos de comissão e registra o pagamento dos fechamentos.
Aprovar um lançamento AVULSO cobra na hora: nota (se o serviço emite nota) + boleto Efí + e-mail aos
contatos do Financeiro. Aprovar um MENSAL só o deixa pronto para o lote da competência.
"""
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import abort, flash, g, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user
from werkzeug.datastructures import MultiDict

from models.cliente_responsavel import ClienteResponsavel
from models.fin_contrato import EMPRESA_QUALICONTAX
from models.fin_lancamento import (COMISSAO, DESTINOS, STATUS, FinComissaoAcordo, FinComissaoFechamento,
                                   FinLancamento, FinLoteMensal)
from routes.financeiro import financeiro
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
PERM = 'financeiro.lancamentos'
EMP = EMPRESA_QUALICONTAX


def _dec(raw):
    s = (str(raw or '')).strip()
    if not s:
        return Decimal('0')
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    return Decimal(s).quantize(Decimal('0.01'))


def _data(raw):
    s = (raw or '').strip()
    return datetime.strptime(s, '%Y-%m-%d').date() if s else None


def _hoje_br():
    """Hoje no Brasil (o app roda em UTC: depois das 21h o date.today() já é amanhã)."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()


def _admin_real():
    return bool(getattr(current_user, 'is_admin', lambda: False)())


# ── "Entrar como" (18/11/2026 — Anderson: "quero somente no Financeiro ter uma opção pra MIM como Admin somente a mim
# eu entrar como o funcionário até para testar e até lançar por eles... Quando isso acontecer aparecer uma msg
# lançado pelo administrador"). Só o ADMIN; vale só nas telas de Lançamentos; o que for lançado fica no nome da
# pessoa e marcado lancado_pelo_admin; a Auditoria continua gravando o admin (quem de fato clicou).
def _eu():
    """A pessoa que a tela de Lançamentos está mostrando: o próprio usuário, ou o funcionário que o admin escolheu."""
    if 'fin_eu' not in g:
        u = None
        uid = session.get('fin_como') if _admin_real() else None
        if uid and uid != current_user.id:
            from models.usuario import Usuario
            u = Usuario.get_by_id(uid)
        g.fin_eu = u or current_user._get_current_object()
    return g.fin_eu


def _vendo_como():
    return _eu().id != current_user.id


def _admin():
    return _admin_real() and not _vendo_como()


def _aprovador():
    """Aprova as solicitações e paga as comissões: o admin ou quem tem a permissão (17/11/2026 — a Alessandra,
    que "não será ADMIN mas terá a prerrogativa de autorizar")."""
    return _admin() or _eu().has_permission('financeiro.lancamentos.aprovar')


def _meu_ou_admin(l):
    if not l or (not _aprovador() and l['lancado_por'] != _eu().id):
        abort(404)
    return l


def _servicos():
    return execute_query("SELECT id, nome, valor_padrao, emite_nota, comissao_tipo, comissao_valor FROM nfse_servicos "
                         "WHERE empresa_id = %s AND ativo = 1 ORDER BY ordem, nome", (EMP,), fetch=True) or []


PIX_TIPOS = {'cpf': 'CPF', 'cnpj': 'CNPJ', 'celular': 'Celular', 'email': 'E-mail', 'aleatoria': 'Chave aleatória'}
# 17/11/2026 — Anderson: "Quando os meninos se cadastraram tinham que por esses dados... era para colocar no CONFIG".
# A chave Pix é a que a pessoa deu no cadastro (usuario_dados_bancarios, o mesmo de Configurações → Usuários).
_PIX_DO_CADASTRO = {'CPF': 'cpf', 'CNPJ': 'cnpj', 'TELEFONE': 'celular', 'EMAIL': 'email', 'ALEATORIA': 'aleatoria'}
_PIX_PARA_CADASTRO = {v: k for k, v in _PIX_DO_CADASTRO.items()}


def _pix_de(usuario_id, nome_usuario=None):
    """{'pix_tipo': 'cpf'|'celular'|..., 'pix_chave', 'titular', 'banco', 'apelido', 'outro'} da CONTA QUE RECEBE da pessoa
    ({} sem chave). 18/11/2026: o Pix sai no nome do TITULAR da conta (a Elizangela, no caso do Guilherme), porque o banco
    do pagador compara o nome do código com o dono da chave e recusa quando não bate."""
    from utils import contas_recebimento as cr
    from utils.pix_brcode import chave_normalizada
    r = cr.escolhida(usuario_id)
    tipo = _PIX_DO_CADASTRO.get((r.get('pix_tipo') or '').upper())
    if not tipo or not (r.get('pix_chave') or '').strip():
        return {}
    if nome_usuario is None:
        u = execute_query('SELECT nome FROM usuarios WHERE id = %s', (usuario_id,), fetch=True, fetch_one=True) or {}
        nome_usuario = u.get('nome') or ''
    return {'pix_tipo': tipo, 'pix_chave': chave_normalizada(tipo, r['pix_chave']), 'titular': (r.get('titular_nome') or '').strip() or nome_usuario,
            'banco': r.get('banco_nome') or '', 'apelido': r.get('apelido') or '', 'outro': cr.titular_e_outro(r, nome_usuario)}


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------
FASES = ('apr', 'cob', 'pag', 'atr')


def _fase(l, hoje):
    """Em que quadro o lançamento fica: aguardando aprovação, em cobrança, pago ou atrasado (None = reprovado/cancelado)."""
    if l['status'] == 'LANCADO':
        return 'apr'
    if l['status'] in ('REPROVADO', 'CANCELADO'):
        return None
    if l.get('titulo_status') == 'liquidado':
        return 'pag'
    if l['status'] == 'FATURADO' and l.get('titulo_vencimento') and l['titulo_vencimento'] < hoje:
        return 'atr'
    return 'cob'


def _como(l):
    """Como o cliente é cobrado: 'Boleto + nota', 'Só boleto' ou 'Mensalidade 11/2026'."""
    if l['destino'] == 'MENSAL':
        return 'Mensalidade ' + l['competencia'].strftime('%m/%Y')
    nota = l['com_nota'] if l.get('com_nota') is not None else l.get('emite_nota')
    return 'Boleto + nota' if nota else 'Só boleto'


@financeiro.route('/financeiro/lancamentos')
@permission_required(PERM)
def lancamentos():
    """LANÇAMENTOS (17/11/2026, modelo aprovado pelo Anderson): a equipe SOLICITA a cobrança (boleto + nota, só
    boleto ou na mensalidade) com a comissão; Anderson ou Alessandra aprovam e aí a cobrança sai. Cada um vê só o
    que é seu; quem aprova vê tudo. Quadros: aguardando aprovação, em cobrança, pagos, atrasados e comissões."""
    import json
    aprov = _aprovador()
    hoje = date.today()
    FinLancamento.liberar_comissoes_pagas()
    try:            # pedido de comissão pago: o extrato que puxou a saída quita sozinho
        for fid, eid in FinComissaoFechamento.quitar_pelo_extrato():
            registrar('escrita.quitou_comissao_pelo_extrato', 'financeiro', tabela='fin_comissao_fechamentos', registro_id=fid,
                      depois={'extrato_id': eid})
    except Exception:
        logger.exception('[lancamentos] quitação de comissão pelo extrato')
    quem = None if aprov else _eu().id
    todos = FinLancamento.listar(EMP, lancado_por=quem, origem=None if not aprov else 'SERVICO', limite=1500)
    grupos = {k: [] for k in FASES}
    fora = []
    for l in todos:
        l['fase'] = _fase(l, hoje)
        l['como'] = _como(l)
        l['situacao_com'] = FinLancamento.situacao_comissao(l)
        (grupos[l['fase']] if l['fase'] else fora).append(l)
    for l in grupos['apr']:      # quanto a comissão dá se aprovar como foi pedida (e se o serviço é travado)
        l['com_prev'] = float(FinLancamento.comissao_calculada(l)[1])
        l['com_trava'] = FinLancamento.servico_travado(l['servico_id'])
    for l in grupos['cob']:      # 18/11/2026: aprovado e sem título = a cobrança não saiu (nota rejeitada, boleto recusado)
        if _sem_cobranca(l):
            l['motivo_falha'] = _motivo_da_falha(l)
    # CARTEIRA DE MENSALIDADE (17/11/2026 — Anderson: "Não pode colocar dentro dos lançamentos? E deixar junto com os
    # outros?"): a mensalidade de cada empresa da carteira da pessoa (Guilherme 25%) vira um cartão nos mesmos quadros
    # (em cobrança / pagos / atrasados) e, paga, entra nas comissões a pedir. Mês passado e este mês, pelo vencimento.
    from models.fin_lancamento import FinCarteira
    cart = []
    ini = hoje.replace(day=1)
    for ref in (date(ini.year - (ini.month == 1), (ini.month - 2) % 12 + 1, 1), ini):
        for ln in FinCarteira.mes(_eu().id, ref.year, ref.month):
            t, a = ln['titulo'], ln['acordo']
            if not t or t.get('lanc_id'):        # já virou lançamento de verdade: aparece por ele
                continue
            pago = t['status'] == 'liquidado'
            com = ln['comissao'] if pago else (FinComissaoAcordo.calcular(a, t['valor']) if a['tipo'] == 'PCT' else Decimal(str(a['valor'])))
            c = {'id': 'c%d_%d' % (a['id'], t['id']), 'carteira': True, 'numero_cliente': a['numero_cliente'],
                 'nome_razao_social': a['nome_razao_social'], 'servico_nome': 'Honorário',
                 'descricao': ('Honorário pago %s' % t['pago_em'].strftime('%d/%m')) if pago and t.get('pago_em') else ('Honorário vence %s' % t['vencimento'].strftime('%d/%m')),
                 'valor_total': t['valor'], 'destino': 'MENSAL', 'competencia': t['vencimento'].replace(day=1), 'titulo_id': t['id'],
                 'vencimento': t['vencimento'], 'titulo_vencimento': t['vencimento'], 'titulo_status': t['status'],
                 'status': 'FATURADO', 'lancado_por': _eu().id, 'lancado_por_nome': getattr(_eu(), 'nome', ''),
                 'comissao_status': 'LIBERADA' if pago else 'AGUARDANDO', 'comissao_valor': com,
                 'como': 'Mensalidade'}
            c['fase'] = 'pag' if pago else ('atr' if t['vencimento'] < hoje else 'cob')
            c['situacao_com'] = 'Pago · a pedir' if pago else ('Vencido' if c['fase'] == 'atr' else 'Aguardando pagamento')
            c['adiantavel'] = not pago                  # 18/11/2026: mensalidade em aberto (a vencer ou atrasada) pode ser pedida adiantada
            grupos[c['fase']].append(c)
            cart.append(c)
    soma = lambda ls: sum(float(x['valor_total'] or 0) for x in ls)
    meus_com = [l for l in todos if l.get('comissao_status') in ('LIBERADA', 'FECHADA', 'PAGA', 'AGUARDANDO')
                and (aprov is False or l['lancado_por'] == _eu().id)] + cart
    liberadas = [l for l in meus_com if l['comissao_status'] == 'LIBERADA' and l['lancado_por'] == _eu().id]
    # Esperando o cliente pagar: o que aguarda e o que foi ADIANTADO e o cliente ainda não pagou (18/11/2026 — Anderson:
    # "não sairá dos atrasados para ele ter ciência"). Serviço atrasado e ainda não pedido pode ser pedido adiantado.
    aguard = [l for l in meus_com if l['comissao_status'] == 'AGUARDANDO'
              or (l.get('comissao_adiantada') and not l.get('desconto_de') and l.get('titulo_status') != 'liquidado')]
    for l in aguard:
        if not l.get('carteira') and l['comissao_status'] == 'AGUARDANDO' and l['fase'] in ('cob', 'atr') and l.get('titulo_id') and l['lancado_por'] == _eu().id:
            l['adiantavel'] = True
    tem_adiantavel = any(l.get('adiantavel') for l in aguard)
    pedidos = FinComissaoFechamento.listar(usuario_id=None if aprov else _eu().id)
    a_pagar = [f for f in pedidos if f['status'] not in ('PAGO', 'CANCELADO')]
    acordos = [a for a in FinComissaoAcordo.listar(usuario_id=_eu().id) if a['base'] == 'SERVICO']
    clientes = execute_query(
        """SELECT id, numero_cliente AS num, nome_razao_social AS nome, cpf_cnpj AS doc, COALESCE(avulso, 0) AS av
             FROM clientes WHERE COALESCE(situacao, 'ATIVO') <> 'INATIVO' ORDER BY nome_razao_social""", fetch=True) or []
    mensal = {r['cliente_id']: float(r['v'] or 0) for r in execute_query(
        """SELECT cliente_id, SUM(valor) v FROM fin_contratos WHERE ativo = 1 AND COALESCE(situacao, 'ATIVO') = 'ATIVO'
            GROUP BY cliente_id""", fetch=True) or []}
    comp0 = FinLoteMensal.competencia_aberta(EMP)
    comps = [comp0]
    while len(comps) < 3:
        comps.append(date(comps[-1].year + (comps[-1].month == 12), comps[-1].month % 12 + 1, 1))
    caixa = {
        'clientes': [{'id': c['id'], 'num': str(c['num'] or ''), 'nome': c['nome'], 'doc': c['doc'] or '', 'av': bool(c['av']),
                      'mensal': mensal.get(c['id'], 0)} for c in clientes],
        'servicos': [{'id': s['id'], 'nome': s['nome'], 'valor': float(s['valor_padrao'] or 0), 'nota': bool(s['emite_nota']),
                      'ct': s.get('comissao_tipo') or '', 'cv': float(s.get('comissao_valor') or 0)} for s in _servicos()],
        'acordos': [{'servico_id': a['servico_id'], 'tipo': a['tipo'], 'valor': float(a['valor'])} for a in acordos],
        'comps': [{'v': c.isoformat(), 'rot': c.strftime('%m/%Y')} for c in comps],
        'hoje': hoje.isoformat(), 'eu': getattr(_eu(), 'nome', ''), 'aprovador': aprov,
    }
    return render_template(
        'financeiro/lancamentos.html', aprov=aprov, admin=_admin(), hoje=hoje, grupos=grupos, fora=fora[:30],
        tot={k: soma(v) for k, v in grupos.items()}, liberadas=liberadas, meus_com=meus_com, aguard=aguard, tem_adiantavel=tem_adiantavel,
        tot_liberadas=sum(float(l['comissao_valor'] or 0) for l in liberadas),
        pedidos=pedidos, a_pagar=a_pagar, tot_a_pagar=sum(float(f['total'] or 0) - float(f['valor_pago'] or 0) for f in a_pagar),
        aba=request.args.get('aba') if request.args.get('aba') in FASES + ('com',) else ('apr' if (aprov or grupos['apr']) else 'cob'),
        caixa_json=json.dumps(caixa, ensure_ascii=False), STATUS=STATUS, COMISSAO=COMISSAO,
        acordos_todos=FinComissaoAcordo.listar(so_ativos=True) if _admin() else [],
        pessoas=ClienteResponsavel.usuarios_disponiveis() if _admin() else [], servicos=_servicos(),
        eu=_eu(), vendo_como=_vendo_como(), admin_real=_admin_real(), acessos=_acessos_de(_eu()) if _vendo_como() else None,
        pessoas_como=[p for p in ClienteResponsavel.usuarios_disponiveis() if p['id'] != current_user.id] if _admin_real() else [],
        ctr_aprovar=(execute_query("SELECT COUNT(*) n FROM prest_contratos WHERE status = 'APROVACAO'", fetch=True, fetch_one=True) or {}).get('n', 0) if _aprovador() else 0,
        pix=_pix_de(_eu().id), PIX_TIPOS=PIX_TIPOS, contas_cobranca=_contas_cobranca() if aprov else [])


_TELAS = [   # (endpoint, rótulo) — as abas do Financeiro e as outras áreas do sistema
    ('financeiro.index', 'Início'), ('financeiro.titulos', 'Cobranças'), ('financeiro.honorarios', 'Honorários'),
    ('financeiro.honorarios_cadastrar', 'Cadastrar honorários'), ('financeiro.lancamentos', 'Lançamentos'),
    ('financeiro.lote', 'Lote mensal'), ('financeiro.contratos', 'Contratos'), ('financeiro.extrato', 'Extrato'),
    ('financeiro.contas', 'Contas'), ('financeiro.programacoes', 'Programações'), ('financeiro.fluxo', 'Fluxo de caixa'),
    ('financeiro.dre', 'DRE'), ('financeiro.categorias', 'Categorias'), ('financeiro.empresas', 'Empresas'),
    ('financeiro.cobrancas', 'Notas e boletos'), ('financeiro.comissao_regras', 'Regras de comissão'),
]
_AREAS = [('clientes.index', 'Cadastros (clientes)'), ('modulos.comercial', 'Comercial'), ('contabil.index', 'Contábil'),
          ('escrita_fiscal.index', 'Fiscal'), ('modulos.dp', 'DP'), ('modulos.legalizacao', 'Legalização'),
          ('modulos.analise', 'Análise'), ('relatorios.index', 'Relatórios'), ('atendimento.index', 'Atendimento'),
          ('configuracoes.index', 'Config.')]


def _acessos_de(u):
    """O que a pessoa consegue abrir (18/11/2026 — Anderson: "ver como Gabriel consigo saber se ele tem acesso a
    outras telas como extrato e outros?"). Mesma regra do portão (utils/acesso.py)."""
    from flask import current_app
    from utils.acesso import pode_rota_de
    vf = current_app.view_functions

    def lista(itens):
        return [{'rot': r, 'pode': pode_rota_de(u, ep)} for ep, r in itens if ep in vf]
    perfis = execute_query("""SELECT p.nome FROM usuario_perfis up JOIN perfis_acesso p ON p.id = up.perfil_id
                               WHERE up.usuario_id = %s ORDER BY p.nome""", (u.id,), fetch=True) or []
    extras = execute_query('SELECT permissao_codigo FROM usuario_permissoes_extra WHERE usuario_id = %s ORDER BY permissao_codigo',
                           (u.id,), fetch=True) or []
    return {'fin': lista(_TELAS), 'areas': lista(_AREAS), 'perfis': [p['nome'] for p in perfis],
            'extras': [e['permissao_codigo'] for e in extras]}


def _marcar_pelo_admin(lid):
    if lid and _vendo_como():
        execute_query('UPDATE fin_lancamentos SET lancado_pelo_admin = %s WHERE id = %s', (current_user.id, lid), fetch=False)


@financeiro.route('/financeiro/lancamentos/como', methods=['POST'])
@permission_required(PERM)
def lancamento_entrar_como():
    """Admin escolhe o funcionário para ver e lançar como ele (só nas telas de Lançamentos)."""
    if not _admin_real():
        abort(403)
    uid = None if request.form.get('sair') else request.form.get('usuario_id', type=int)
    if uid and uid != current_user.id:
        from models.usuario import Usuario
        u = Usuario.get_by_id(uid)
        if not u:
            abort(404)
        session['fin_como'] = uid
        registrar('leitura.entrou_como_funcionario', 'financeiro', tabela='usuarios', registro_id=uid, depois={'como': u.nome})
        flash('Você está vendo Lançamentos como %s. O que lançar fica no nome dele(a), marcado "lançado pelo administrador".' % u.nome, 'warning')
    else:
        session.pop('fin_como', None)
    return redirect(url_for('financeiro.lancamentos'))


@financeiro.route('/financeiro/lancamentos/minha-pix', methods=['POST'])
@permission_required(PERM)
def lancamento_minha_pix():
    if _vendo_como():
        flash('A chave Pix é da própria pessoa: saia do "ver como" para mexer na sua.', 'warning')
        return redirect(url_for('financeiro.lancamentos'))
    """A chave Pix de quem recebe comissão (17/11/2026): vai no pedido de pagamento, com o Pix copia e cola."""
    import re
    from utils.pix_brcode import chave_normalizada
    tipo = request.form.get('pix_tipo') or ''
    chave = (request.form.get('pix_chave') or '').strip()
    erro = None
    if tipo not in PIX_TIPOS or not chave:
        erro = 'Escolha o tipo e escreva a chave.'
    else:
        chave = chave_normalizada(tipo, chave)
        d = re.sub(r'\D', '', chave)
        if (tipo == 'cpf' and len(d) != 11) or (tipo == 'cnpj' and len(d) != 14) or (tipo == 'celular' and len(d) not in (12, 13)) \
                or (tipo == 'email' and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', chave)) or (tipo == 'aleatoria' and len(chave) < 30):
            erro = 'Essa chave não parece um ' + PIX_TIPOS[tipo] + ' válido.'
    if erro:
        flash(erro, 'danger')
    else:
        antes = _pix_de(current_user.id)
        from utils import contas_recebimento as cr
        ja = cr.escolhida(current_user.id) or None       # 18/11/2026: mexe na conta que recebe
        if ja:
            execute_query('UPDATE usuario_dados_bancarios SET pix_tipo = %s, pix_chave = %s, atualizado_por = %s WHERE id = %s',
                          (_PIX_PARA_CADASTRO[tipo], chave[:140], current_user.id, ja['id']), fetch=False)
        else:
            execute_query('INSERT INTO usuario_dados_bancarios (usuario_id, pix_tipo, pix_chave, atualizado_por) VALUES (%s, %s, %s, %s)',
                          (current_user.id, _PIX_PARA_CADASTRO[tipo], chave[:140], current_user.id), fetch=False)
        # dado bancário não vai para o log (regra do cadastro): só que trocou e o tipo
        registrar('escrita.alterou_chave_pix', 'financeiro', tabela='usuario_dados_bancarios', registro_id=current_user.id,
                  antes={'pix_tipo': antes.get('pix_tipo'), 'tinha_chave': bool(antes.get('pix_chave'))}, depois={'pix_tipo': tipo, 'tem_chave': True})
        flash('Chave Pix salva: os seus pedidos de comissão saem com ela.', 'success')
    return redirect(url_for('financeiro.lancamentos', aba='com'))


@financeiro.route('/financeiro/lancamentos/pedido/<int:fid>')
@permission_required(PERM)
def comissao_pedido(fid):
    """Pedido de pagamento de comissão (17/11/2026 — Anderson: "gerar um Documento para receber esse valor e enviar
    para anderson ou alessandra igual PÁGINA DO CLIENTE, o qual se ele cadastrou a chave dele PIX no CONFIG consigo
    copiar e pagar"). Quem pediu e quem aprova abrem; o Pix já vem com o valor que falta."""
    from utils.pix_brcode import copia_e_cola, codigo_pedido, qr_data_uri
    FinLancamento.liberar_comissoes_pagas()
    try:
        FinComissaoFechamento.quitar_pelo_extrato()
    except Exception:
        logger.exception('[lancamentos] quitação de comissão pelo extrato')
    f = FinComissaoFechamento.get(fid)
    if not f or (not _aprovador() and f['usuario_id'] != _eu().id):
        abort(404)
    itens = FinComissaoFechamento.itens(fid)
    pags = execute_query("""SELECT p.*, l.descricao AS extrato_desc, l.banco AS extrato_banco, u.nome AS por_nome
                             FROM fin_comissao_pagamentos p LEFT JOIN extrato_lancamentos l ON l.id = p.extrato_id
                             LEFT JOIN usuarios u ON u.id = p.registrado_por WHERE p.fechamento_id = %s ORDER BY p.data, p.id""",
                         (fid,), fetch=True) or []
    pix = _pix_de(f['usuario_id'])
    falta = float(f['total'] or 0) - float(f['valor_pago'] or 0)
    codigo = qr = None
    if pix.get('pix_chave') and falta > 0 and f['status'] != 'CANCELADO':
        codigo = copia_e_cola(pix['pix_chave'], pix.get('titular') or f['usuario_nome'], falta, mensagem='Pedido ' + codigo_pedido(fid))
        qr = qr_data_uri(codigo)
    from utils.comissao_pedido_doc import frase_resumo, link_whatsapp, texto_whatsapp
    ctx = {'f': f, 'itens': itens, 'pix': pix, 'falta': falta, 'codigo': codigo,
           'link': url_for('financeiro.comissao_pedido', fid=fid, _external=True)}
    texto = texto_whatsapp(ctx)
    return render_template('financeiro/comissao_pedido.html', f=f, itens=itens, pags=pags, pix=pix, PIX_TIPOS=PIX_TIPOS,
                           falta=falta, codigo=codigo, qr=qr, sigla=codigo_pedido(fid), aprov=_aprovador(), hoje=date.today(),
                           resumo=frase_resumo(itens), texto_wa=texto, link_wa=link_whatsapp(texto))


@financeiro.route('/financeiro/lancamentos/pedido/<int:fid>.<fmt>')
@permission_required(PERM)
def comissao_pedido_arquivo(fid, fmt):
    """O pedido em PDF ou em imagem PNG (18/11/2026 — Anderson: "no fechamento teremos imagem, pdf e até texto de
    whatsapp a ser enviado para o financeiro"). Mesmas pessoas que abrem a página."""
    import os
    from utils.comissao_pedido_doc import gerar_pdf, gerar_png
    from utils.pix_brcode import copia_e_cola, codigo_pedido
    if fmt not in ('pdf', 'png'):
        abort(404)
    f = FinComissaoFechamento.get(fid)
    if not f or (not _aprovador() and f['usuario_id'] != _eu().id):
        abort(404)
    itens = FinComissaoFechamento.itens(fid)
    pags = execute_query("""SELECT p.*, l.descricao AS extrato_desc, l.banco AS extrato_banco, u.nome AS por_nome
                             FROM fin_comissao_pagamentos p LEFT JOIN extrato_lancamentos l ON l.id = p.extrato_id
                             LEFT JOIN usuarios u ON u.id = p.registrado_por WHERE p.fechamento_id = %s ORDER BY p.data, p.id""",
                         (fid,), fetch=True) or []
    pix = _pix_de(f['usuario_id'])
    falta = float(f['total'] or 0) - float(f['valor_pago'] or 0)
    codigo = copia_e_cola(pix['pix_chave'], pix.get('titular') or f['usuario_nome'], falta, mensagem='Pedido ' + codigo_pedido(fid)) if pix.get('pix_chave') and falta > 0 else None
    logo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'images', 'logo.png')
    ctx = {'f': f, 'itens': itens, 'pags': pags, 'pix': pix, 'falta': falta, 'codigo': codigo,
           'link': url_for('financeiro.comissao_pedido', fid=fid, _external=True), 'logo_path': logo if os.path.exists(logo) else None}
    dados = gerar_pdf(ctx) if fmt == 'pdf' else gerar_png(ctx)
    nome = 'pedido-comissao-%d-%s.%s' % (fid, re.sub(r'\W+', '-', (f['usuario_nome'] or '').split()[0].lower()) if f.get('usuario_nome') else 'x', fmt)
    from io import BytesIO
    return send_file(BytesIO(dados), as_attachment=fmt == 'pdf', download_name=nome, mimetype='application/pdf' if fmt == 'pdf' else 'image/png')


@financeiro.route('/financeiro/lancamentos/regras')
@permission_required(PERM)
def comissao_regras():
    """REGRAS DE COMISSÃO (17/11/2026 — Anderson: "como farei o cadastramento do que tem Comissão e o que não tem e quem
    recebe ou não?"). Só o administrador. Duas partes: QUEM RECEBE (cada pessoa: liga/desliga e o % padrão; carteira de
    mensalidades, ex.: Guilherme 25% das empresas dele) e SERVIÇOS (o que tem comissão, quanto, e o que não tem)."""
    if not _admin():
        abort(403)
    import json
    acordos = FinComissaoAcordo.listar(so_ativos=True)
    pessoas = []
    for u in ClienteResponsavel.usuarios_disponiveis():
        meus = [a for a in acordos if a['usuario_id'] == u['id']]
        geral = next((a for a in meus if a['base'] == 'SERVICO' and not a['servico_id']), None)
        pessoas.append(dict(u, geral=geral, por_servico=[a for a in meus if a['base'] == 'SERVICO' and a['servico_id']],
                            carteira=[a for a in meus if a['base'] == 'HONORARIOS']))
    pessoas.sort(key=lambda p: (not p['geral'] and not p['carteira'], p['nome']))
    servicos = _servicos()
    clientes = execute_query("""SELECT c.id, c.numero_cliente AS num, c.nome_razao_social AS nome,
                                    (SELECT SUM(k.valor) FROM fin_contratos k WHERE k.cliente_id = c.id AND k.ativo = 1) AS mensal
                               FROM clientes c WHERE COALESCE(c.situacao, 'ATIVO') <> 'INATIVO' AND COALESCE(c.avulso, 0) = 0
                              ORDER BY c.nome_razao_social""", fetch=True) or []
    return render_template('financeiro/comissao_regras.html', pessoas=pessoas, servicos=servicos, hoje=date.today(),
                           aba=request.args.get('aba') if request.args.get('aba') in ('pessoas', 'servicos') else 'pessoas',
                           n_recebem=sum(1 for p in pessoas if p['geral']), n_carteira=sum(len(p['carteira']) for p in pessoas),
                           n_serv_regra=sum(1 for s in servicos if s.get('comissao_tipo')),
                           n_serv_sem=sum(1 for s in servicos if s.get('comissao_tipo') == 'SEM'),
                           clientes_json=json.dumps([{'id': c['id'], 'num': str(c['num'] or ''), 'nome': c['nome'],
                                                      'mensal': float(c['mensal'] or 0)} for c in clientes], ensure_ascii=False))


@financeiro.route('/financeiro/lancamentos/regras/pessoa', methods=['POST'])
@permission_required(PERM)
def comissao_regra_pessoa():
    """Liga/desliga a comissão de uma pessoa e o padrão dela (% ou valor fixo) — o acordo geral de serviço lançado.
    Trocar o valor encerra o acordo de hoje e abre outro (o histórico fica)."""
    if not _admin():
        abort(403)
    uid = request.form.get('usuario_id', type=int)
    recebe = request.form.get('recebe') == '1'
    tipo = request.form.get('tipo') if request.form.get('tipo') in ('PCT', 'FIXO') else 'PCT'
    try:
        valor = _dec(request.form.get('valor') or '10')
        if recebe and (valor <= 0 or (tipo == 'PCT' and valor > 100)):
            raise ValueError
    except (ValueError, InvalidOperation):
        flash('Comissão inválida.', 'danger')
        return redirect(url_for('financeiro.comissao_regras'))
    atuais = [a for a in FinComissaoAcordo.listar(usuario_id=uid) if a['base'] == 'SERVICO' and not a['servico_id']]
    igual = recebe and atuais and atuais[0]['tipo'] == tipo and Decimal(str(atuais[0]['valor'])) == valor
    if not igual:
        for a in atuais:
            FinComissaoAcordo.encerrar(a['id'])
        if recebe:
            FinComissaoAcordo.criar(uid, 'SERVICO', tipo, valor, _hoje_br(), criado_por=current_user.id, aprovado_por=current_user.id)
        nome = (execute_query('SELECT nome FROM usuarios WHERE id = %s', (uid,), fetch=True, fetch_one=True) or {}).get('nome')
        registrar('escrita.alterou_regra_comissao', 'financeiro', tabela='fin_comissao_acordos', registro_id=uid,
                  antes={'recebe': bool(atuais), 'tipo': atuais[0]['tipo'] if atuais else None, 'valor': str(atuais[0]['valor']) if atuais else None},
                  depois={'pessoa': nome, 'recebe': recebe, 'tipo': tipo if recebe else None, 'valor': str(valor) if recebe else None})
        flash(('%s recebe comissão: %s.' % (nome, ('%g%%' % valor) if tipo == 'PCT' else 'R$ %s fixo' % valor)) if recebe
              else '%s não recebe comissão de serviço.' % nome, 'success')
    return redirect(url_for('financeiro.comissao_regras') + '#p%d' % uid)


@financeiro.route('/financeiro/lancamentos/servico-comissao', methods=['POST'])
@permission_required(PERM)
def servico_comissao():
    """Comissão padrão de um serviço do catálogo (só o administrador): % / valor fixo / sem comissão / apagar — e o
    VALOR SUGERIDO do serviço (17/11/2026 — Anderson: "já fazemos uma relação dos serviços e sugestões de valores vamos
    colocar nos que tem comissões"), o que vem preenchido quando alguém escolhe o serviço numa cobrança."""
    if not _admin():
        abort(403)
    sid = request.form.get('servico_id', type=int)
    tipo = request.form.get('tipo') or ''
    s = execute_query('SELECT id, nome, comissao_tipo, comissao_valor, valor_padrao FROM nfse_servicos WHERE id = %s AND empresa_id = %s',
                      (sid, EMP), fetch=True, fetch_one=True)
    if not s or tipo not in ('', 'PCT', 'FIXO', 'SEM'):
        abort(400)
    try:
        valor = _dec(request.form.get('valor')) if tipo in ('PCT', 'FIXO') else None
        if tipo in ('PCT', 'FIXO') and (valor is None or valor <= 0 or (tipo == 'PCT' and valor > 100)):
            raise ValueError('Comissão inválida.')
        sugerido = s['valor_padrao']
        if 'valor_padrao' in request.form:      # vazio = sem valor sugerido
            vp = (request.form.get('valor_padrao') or '').strip()
            sugerido = _dec(vp) if vp else None
            if sugerido is not None and sugerido <= 0:
                sugerido = None
    except (ValueError, InvalidOperation):
        flash('Comissão ou valor sugerido inválido para %s.' % s['nome'], 'danger')
        if request.form.get('volta') == 'regras':
            return redirect(url_for('financeiro.comissao_regras', aba='servicos') + '#s%d' % sid)
        return redirect(url_for('financeiro.lancamentos', aba='com') + '#servicos-comissao')
    execute_query('UPDATE nfse_servicos SET comissao_tipo = %s, comissao_valor = %s, valor_padrao = %s WHERE id = %s',
                  (tipo or None, valor, sugerido, sid), fetch=False)
    registrar('escrita.alterou_comissao_servico', 'financeiro', tabela='nfse_servicos', registro_id=sid,
              antes={'comissao_tipo': s['comissao_tipo'], 'comissao_valor': str(s['comissao_valor'] or ''), 'valor_sugerido': str(s['valor_padrao'] or '')},
              depois={'servico': s['nome'], 'comissao_tipo': tipo or None, 'comissao_valor': str(valor or ''), 'valor_sugerido': str(sugerido or '')})
    flash('"%s" salvo.' % s['nome'], 'success')
    if request.form.get('volta') == 'regras':
        return redirect(url_for('financeiro.comissao_regras', aba='servicos') + '#s%d' % sid)
    return redirect(url_for('financeiro.lancamentos', aba='com') + '#servicos-comissao')


@financeiro.route('/financeiro/lancamentos/novo', methods=['POST'])
@permission_required(PERM)
def lancamento_novo():
    """Solicitação da caixa "Nova solicitação": como cobrar (boleto + nota / só boleto / na mensalidade), serviço,
    valor, vencimento e a comissão pedida. Nada sai agora: a cobrança é emitida na aprovação."""
    f = request.form
    try:
        cliente_id = f.get('cliente_id', type=int)
        servico_id = f.get('servico_id', type=int)
        if not cliente_id or not servico_id:
            raise ValueError('Escolha o cliente e o serviço.')
        s = execute_query('SELECT nome FROM nfse_servicos WHERE id = %s AND empresa_id = %s', (servico_id, EMP), fetch=True, fetch_one=True)
        if not s:
            raise ValueError('Serviço inválido.')
        como = f.get('como') or 'BOLETO_NOTA'
        if como not in ('BOLETO_NOTA', 'BOLETO', 'MENSAL'):
            raise ValueError('Escolha como cobrar.')
        destino = 'MENSAL' if como == 'MENSAL' else 'AVULSO'
        comp = _data(f.get('competencia')) if destino == 'MENSAL' else None
        lid = FinLancamento.criar(EMP, cliente_id, servico_id, (f.get('descricao') or s['nome']).strip() or s['nome'],
                                  Decimal('1'), _dec(f.get('valor')), destino, _eu().id,
                                  vencimento=_data(f.get('vencimento')) if destino == 'AVULSO' else None,
                                  observacao=(f.get('observacao') or '').strip() or None, competencia=comp,
                                  com_nota=None if destino == 'MENSAL' else (como == 'BOLETO_NOTA'),
                                  comissao_tipo='SEM' if FinLancamento.servico_travado(servico_id) else (f.get('comissao_tipo') or 'PADRAO'),
                                  comissao_valor=None if FinLancamento.servico_travado(servico_id) else (f.get('comissao_valor') or None))
        _marcar_pelo_admin(lid)
        l = FinLancamento.get(lid)
        registrar('escrita.lancou_servico', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
                  depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'valor': str(l['valor_total']),
                          'como': _como(l), 'vencimento': str(l['vencimento'] or ''),
                          'comissao_pedida': (l.get('comissao_pedida_tipo') or '') + (' ' + str(l['comissao_pedida_valor']) if l.get('comissao_pedida_valor') else '')})
        if _aprovador() and f.get('emitir') == '1':
            # 17/11/2026 — Anderson: "numa tela só para emissão imediata ou para incluir no mensal". Quem aprova não
            # precisa aprovar a própria: já sai aprovado — avulso emite agora, mensal entra no lote do mês.
            com = FinLancamento.aprovar(lid, _eu().id)
            registrar('escrita.aprovou_lancamento', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
                      depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'valor': str(l['valor_total']),
                              'destino': l['destino'], 'comissao': str(com), 'lancado_por': l['lancado_por_nome'], 'direto': True})
            if l['destino'] == 'AVULSO':
                ok, msgs = _cobrar_avulso(FinLancamento.get(lid), _eu().id, _conta_escolhida(f))
                for m in msgs:
                    flash(m, 'success' if ok else 'warning')
                if not ok:
                    flash('Ficou aprovado, mas a cobrança não saiu. Corrija e cobre pela tela Cobranças.', 'danger')
                return redirect(url_for('financeiro.lancamentos', aba='cob'))
            flash('Incluído na mensalidade de %s de %s: sai no boleto do lote do mês.' % (l['competencia'].strftime('%m/%Y'), l['nome_razao_social']), 'success')
            return redirect(url_for('financeiro.lancamentos', aba='cob'))
        flash('Solicitação enviada: %s · %s. Anderson ou Alessandra aprovam e a cobrança sai.' % (l['nome_razao_social'], _como(l)), 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    return redirect(url_for('financeiro.lancamentos', aba='apr'))


# ---------------------------------------------------------------------------
# Aprovação (admin) — AVULSO cobra na hora
# ---------------------------------------------------------------------------
def _contas_cobranca():
    """Contas de cobrança que emitem boleto (ativas, não só-consulta) da empresa — a padrão primeiro."""
    return execute_query("""SELECT id, apelido, provedor, ambiente FROM fin_cobranca_contas
                             WHERE empresa_id = %s AND ativo = 1 AND so_consulta = 0 AND ambiente = 'producao'
                             ORDER BY id""", (EMP,), fetch=True) or []   # só Efí e Bradesco de verdade (Anderson: "nada de teste ou produção")


def _sem_cobranca(l):
    """Lançamento avulso aprovado que ficou sem cobrança: a nota foi rejeitada ou o boleto não saiu na aprovação."""
    return l.get('status') == 'APROVADO' and l.get('destino') == 'AVULSO' and not l.get('titulo_id') and not l.get('carteira')


def _motivo_da_falha(l):
    """O erro da última nota rejeitada desse cliente e valor, emitida por lançamento (ex.: E0240, CEP do tomador)."""
    doc = re.sub(r'\D', '', l.get('cpf_cnpj') or '')
    if not doc:
        return None
    r = execute_query("""SELECT erro_ultimo, xmotivo FROM nfse_emitidas
                          WHERE situacao = 'rejeitada' AND origem = 'lancamento' AND tomador_doc = %s
                            AND ABS(valor_servico - %s) < 0.005 AND criado_em >= %s
                          ORDER BY id DESC LIMIT 1""",
                      (doc, l['valor_total'], l.get('aprovado_em') or l.get('criado_em')), fetch=True, fetch_one=True) or {}
    return (r.get('erro_ultimo') or r.get('xmotivo') or '')[:300] or None


def _conta_escolhida(form):
    """A conta do boleto escolhida na caixa (17/11/2026 — Anderson: "tem que ter a opção de escolher" o banco).
    Só vale conta de cobrança ativa da empresa; senão None (fica a padrão)."""
    cid = form.get('conta_cobranca_id', type=int)
    return next((c['id'] for c in _contas_cobranca() if c['id'] == cid), None) if cid else None


def _cobrar_avulso(l, usuario_id, conta_id=None):
    """Nota (se o serviço emite) + boleto Efí + e-mail aos contatos do Financeiro. Devolve (ok, mensagens)."""
    from models.fin_cobranca import FinBoleto, FinCobrancaConta
    from models.nfse_emissao import NfseConfig, NfseEmitida, tomador_do_cliente
    from routes.financeiro_cobranca import _pagador_do_tomador, emitir_boleto_avulso, gerar_boleto_da_nota, sugestao_boleto
    from utils import nfse_emissor
    msgs = []
    conta = FinCobrancaConta.get(conta_id, com_segredos=True) if conta_id else None   # como a padrão: com as credenciais
    if not conta or conta.get('empresa_id') != l['empresa_id'] or not conta.get('ativo') or conta.get('so_consulta'):
        conta = FinCobrancaConta.padrao_da_empresa(l['empresa_id'])
    if not conta:
        return False, ['Não há conta de cobrança ativa para emitir o boleto.']
    t = tomador_do_cliente(l['cliente_id'])
    if not t or not t.get('documento'):
        return False, ['O cadastro da empresa não tem CPF/CNPJ para o boleto.']
    vencimento = l['vencimento'] or (date.today())
    if vencimento < date.today():
        vencimento = date.today()
    competencia = l['competencia']
    _venc, mensagem = sugestao_boleto(conta, competencia)
    itens = [{'servico_id': l['servico_id'], 'nome': l['descricao'] or l['servico_nome'], 'valor': Decimal(str(l['valor_total'])),
              'emite_nota': bool(l['com_nota'] if l.get('com_nota') is not None else l.get('emite_nota'))}]
    cfg = NfseConfig.get(l['empresa_id']) if itens[0]['emite_nota'] else None
    nfse_id = boleto_id = titulo_id = None
    if cfg:
        serie = cfg.get('serie_nacional') if (cfg.get('emissor') or 'nacional') == 'nacional' else cfg.get('serie_sp')
        form = MultiDict([('serie', str(serie or '')), ('competencia', competencia.isoformat()),
                          ('tomador_cliente_id', str(l['cliente_id'])), ('descricao', l['descricao'] or l['servico_nome']),
                          ('item_servico_id', str(l['servico_id'])), ('item_valor', str(l['valor_total']))])
        try:
            pedido = nfse_emissor.pedido_do_form(form, l['empresa_id'], cfg)
            pedido.origem = 'lancamento'
            linha = nfse_emissor.emitir(pedido, cfg, usuario_id=usuario_id)
        except Exception as exc:
            logger.exception('[lancamento] nota do lançamento %s', l['id'])
            return False, ['A nota não saiu: %s' % exc]
        if not linha or linha['situacao'] != 'autorizada':
            # 18/11/2026: o motivo e o que fazer vêm no aviso (a Infinity voltou "rejeitada" sem dizer que era o CEP)
            from models.nfse_emissao import motivo_nfse
            m = motivo_nfse((linha or {}).get('erro_ultimo'))
            return False, [('O governo recusou a nota: %s%s %s' % (m['texto'] or 'sem detalhe', (' (%s).' % m['cod']) if m['cod'] else '.', m['fazer']))
                           if linha else 'A nota não chegou ao governo (falha no envio).',
                           'O lançamento continua aprovado, sem cobrança: corrija e use "Cobrar de novo" no cartão dele.']
        nfse_id = linha['id']
        msgs.append('Nota autorizada: série %s nº %s.' % (linha['serie'], linha['numero_dps']))
        n = NfseEmitida.get(nfse_id)
        ok, msg, aviso = gerar_boleto_da_nota(n, conta, vencimento, mensagem, usuario_id)
        msgs.append(msg)
        if aviso:
            msgs.append(aviso)
        b = FinBoleto.da_nota(nfse_id)
        boleto_id = (b or {}).get('id')
        titulo_id = (b or {}).get('titulo_id')
    else:
        boleto_id, ok, msg, aviso = emitir_boleto_avulso(l['empresa_id'], conta, _pagador_do_tomador(t), itens, itens[0]['valor'],
                                                         competencia, vencimento, mensagem, None, usuario_id)
        msgs.append(msg)
        if aviso:
            msgs.append(aviso)
        if boleto_id:
            titulo_id = (FinBoleto.get(boleto_id) or {}).get('titulo_id')
    if ok:
        FinLancamento.marcar_faturado(l['id'], titulo_id=titulo_id, nfse_id=nfse_id, boleto_id=boleto_id)
    return ok, msgs


@financeiro.route('/financeiro/lancamentos/<int:lid>/aprovar', methods=['POST'])
@permission_required(PERM)
def lancamento_aprovar(lid):
    if _vendo_como():
        flash('Você está vendo como outra pessoa: volte a ser você para aprovar, reprovar ou pagar.', 'warning')
        return redirect(url_for('financeiro.lancamentos'))
    if not _aprovador():
        abort(403)
    l = FinLancamento.get(lid)
    if not l:
        abort(404)
    ct = request.form.get('comissao_tipo') or None
    # 18/11/2026 — quem aprova acerta como cobrar e o vencimento antes de emitir (Anderson: "Tenho que ter essa
    # prerrogativa" e "Posso alterar tudo se é BOLETO + NF, só BOLETO ou alterar para a Mensalidade")
    como = request.form.get('como')
    if l['status'] == 'LANCADO' and como in ('BOLETO_NOTA', 'BOLETO', 'MENSAL'):
        try:
            if como == 'MENSAL':
                destino, com_nota, venc = 'MENSAL', None, None
                comp = FinLoteMensal.competencia_aberta(EMP, _data(request.form.get('competencia')) or l['competencia'])
            else:
                destino, com_nota = 'AVULSO', 1 if como == 'BOLETO_NOTA' else 0
                venc = _data(request.form.get('vencimento')) or l['vencimento']
                if not venc or venc < _hoje_br():
                    raise ValueError
                comp = l['competencia'] if l['destino'] == 'AVULSO' and l['competencia'] else _hoje_br().replace(day=1)
        except ValueError:
            flash('Vencimento inválido: escolha hoje ou uma data à frente.', 'danger')
            return redirect(url_for('financeiro.lancamentos', aba='apr'))
        antes = {'como': _como(l), 'vencimento': str(l['vencimento'] or '')}
        nota_antes = l['com_nota'] if l.get('com_nota') is not None else l.get('emite_nota')
        mudou = (destino != l['destino'] or venc != l['vencimento'] or comp != l['competencia']
                 or (destino == 'AVULSO' and bool(com_nota) != bool(nota_antes)))
        if mudou:
            execute_query("UPDATE fin_lancamentos SET destino = %s, com_nota = %s, vencimento = %s, competencia = %s "
                          "WHERE id = %s AND status = 'LANCADO'", (destino, com_nota, venc, comp, lid), fetch=False)
            l = FinLancamento.get(lid)
            registrar('escrita.alterou_cobranca_lancamento', 'financeiro', tabela='fin_lancamentos', registro_id=lid, antes=antes,
                      depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'como': _como(l),
                              'vencimento': str(l['vencimento'] or '')})
    try:
        com = FinLancamento.aprovar(lid, current_user.id, comissao_tipo=ct,
                                    comissao_valor=_dec(request.form.get('comissao_valor')) if ct in ('PCT', 'FIXO') else None)
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor da comissão inválido.', 'danger')
        return redirect(url_for('financeiro.lancamentos', aba='apr'))
    registrar('escrita.aprovou_lancamento', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
              depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'valor': str(l['valor_total']),
                      'destino': l['destino'], 'comissao': str(com), 'lancado_por': l['lancado_por_nome']})
    if l['destino'] == 'AVULSO':
        ok, msgs = _cobrar_avulso(FinLancamento.get(lid), current_user.id, _conta_escolhida(request.form))
        for m in msgs:
            flash(m, 'success' if ok else 'warning')
        if not ok:
            flash('Aprovado, mas a cobrança não saiu. Corrija e cobre pela tela Cobranças.', 'danger')
    else:
        flash('Aprovado: entra na cobrança mensal de %s.' % l['competencia'].strftime('%m/%Y'), 'success')
    return redirect(url_for('financeiro.lancamentos', aba='apr'))


@financeiro.route('/financeiro/lancamentos/<int:lid>/cobrar', methods=['POST'])
@permission_required(PERM)
def lancamento_cobrar(lid):
    """Cobrar de novo um lançamento aprovado que ficou sem cobrança (18/11/2026 — a nota da Infinity Aragarças foi
    rejeitada pelo CEP do cadastro e o lançamento ficou "aprovado" sem título, sem caminho de volta). Quem aprova
    corrige o cadastro e cobra daqui: assim a nota, o boleto, o título e a comissão de quem lançou ficam presos ao
    lançamento. Reemitir pela tela Cobranças criaria tudo solto, e a comissão nunca liberaria."""
    if _vendo_como():
        flash('Você está vendo como outra pessoa: volte a ser você para cobrar.', 'warning')
        return redirect(url_for('financeiro.lancamentos'))
    if not _aprovador():
        abort(403)
    l = FinLancamento.get(lid)
    if not l:
        abort(404)
    if not _sem_cobranca(l):
        flash('Este lançamento já tem cobrança ou não está aprovado.', 'warning')
        return redirect(url_for('financeiro.lancamentos', aba='cob'))
    ok, msgs = _cobrar_avulso(l, current_user.id, _conta_escolhida(request.form))
    registrar('escrita.cobrou_lancamento_de_novo', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
              depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'valor': str(l['valor_total']),
                      'saiu': ok, 'mensagens': msgs})
    for m in msgs:
        flash(m, 'success' if ok else 'warning')
    flash('Cobrança emitida: o lançamento está em cobrança.' if ok else 'A cobrança ainda não saiu. Corrija o que o aviso pede e cobre de novo.',
          'success' if ok else 'danger')
    return redirect(url_for('financeiro.lancamentos', aba='cob'))


@financeiro.route('/financeiro/lancamentos/<int:lid>/reprovar', methods=['POST'])
@permission_required(PERM)
def lancamento_reprovar(lid):
    if _vendo_como():
        flash('Você está vendo como outra pessoa: volte a ser você para aprovar, reprovar ou pagar.', 'warning')
        return redirect(url_for('financeiro.lancamentos'))
    if not _aprovador():
        abort(403)
    l = FinLancamento.get(lid)
    try:
        FinLancamento.reprovar(lid, current_user.id, request.form.get('motivo'))
        registrar('escrita.reprovou_lancamento', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
                  depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'motivo': request.form.get('motivo')})
        flash('Lançamento reprovado.', 'info')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('financeiro.lancamentos', aba='apr'))


@financeiro.route('/financeiro/lancamentos/<int:lid>/cancelar', methods=['POST'])
@permission_required(PERM)
def lancamento_cancelar(lid):
    l = _meu_ou_admin(FinLancamento.get(lid))
    try:
        if not _aprovador() and l['status'] != 'LANCADO':
            raise ValueError('Só o que ainda aguarda aprovação pode ser cancelado por você.')
        FinLancamento.cancelar(lid, request.form.get('motivo'))
        registrar('escrita.cancelou_lancamento', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
                  depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'motivo': request.form.get('motivo')})
        flash('Lançamento cancelado.', 'info')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('financeiro.lancamentos'))


# ---------------------------------------------------------------------------
# Comissões: fechamento (quem lançou) e pagamento (admin)
# ---------------------------------------------------------------------------
@financeiro.route('/financeiro/lancamentos/<int:lid>/cobranca')
@permission_required(PERM)
def lancamento_cobranca(lid):
    """A janela da cobrança do que a pessoa lançou (18/11/2026 — Anderson: "Guilherme, Gabriel, Melke e quem mais
    enviar honorários poderá ter acesso ao boleto que mandou para eventual envio para o cliente"). Para quem não
    aprova sai RESTRITA: sem "O que fazer", sem "Página do cliente", sem o que mais o cliente deve; só o boleto, o
    texto da cobrança, o código Pix e a linha digitável."""
    l = FinLancamento.get(lid)
    _meu_ou_admin(l)
    if not l.get('titulo_id'):
        abort(404)
    from routes.financeiro_titulo_cobranca import janela
    return janela(l['titulo_id'], restrito=not _aprovador())


@financeiro.route('/financeiro/lancamentos/carteira/<int:tid>/cobranca')
@permission_required(PERM)
def carteira_cobranca(tid):
    """A janela da cobrança de uma MENSALIDADE da carteira da pessoa (18/11/2026 — Anderson: "aqui ele tem que ter a
    opção de enviar o boleto ao cliente"). O título tem de estar na carteira dela (FinCarteira.mes do mês do vencimento);
    quem aprova abre qualquer um. Para quem não aprova sai restrita, como a do serviço avulso."""
    from models.fin_lancamento import FinCarteira
    from routes.financeiro_titulo_cobranca import janela
    t = execute_query('SELECT id, vencimento FROM fin_titulos WHERE id = %s', (tid,), fetch=True, fetch_one=True)
    if not t:
        abort(404)
    if not _aprovador():
        meu = any(ln['titulo'] and ln['titulo']['id'] == tid for ln in FinCarteira.mes(_eu().id, t['vencimento'].year, t['vencimento'].month))
        if not meu:
            abort(404)
    return janela(tid, restrito=not _aprovador())


@financeiro.route('/financeiro/lancamentos/fechar', methods=['POST'])
@permission_required(PERM)
def comissoes_fechar():
    try:
        from models.fin_lancamento import FinCarteira
        ids, adiantadas = [], 0
        for v in request.form.getlist('lanc'):
            m = re.fullmatch(r'c(\d+)_(\d+)', v or '')
            ma = re.fullmatch(r'a(\d+)', v or '')
            if m:      # mensalidade da carteira: paga → libera; atrasada → adiantada (18/11/2026)
                lid = FinCarteira.liberar_um(_eu().id, int(m.group(1)), int(m.group(2)))
                if not lid:
                    lid = FinCarteira.adiantar_um(_eu().id, int(m.group(1)), int(m.group(2)))
                    adiantadas += bool(lid)
                if lid:
                    ids.append(lid)
            elif ma:   # serviço com cobrança atrasada, pedido antes de o cliente pagar
                if FinLancamento.adiantar(int(ma.group(1)), _eu().id):
                    ids.append(int(ma.group(1))); adiantadas += 1
            elif str(v).isdigit():
                ids.append(int(v))
        fid = FinComissaoFechamento.criar(_eu().id, ids, (request.form.get('observacao') or '').strip() or None)
        f = FinComissaoFechamento.get(fid)
        registrar('escrita.fechou_comissoes', 'financeiro', tabela='fin_comissao_fechamentos', registro_id=fid,
                  depois={'total': str(f['total']), 'itens': f['qtd_itens'], 'adiantadas': adiantadas})
        flash('Pedido nº %d criado: R$ %s em %d comissão(ões)%s. Mande para Anderson ou Alessandra pelo WhatsApp, em PDF ou em imagem.'
              % (fid, f['total'], f['qtd_itens'], (' · %d adiantada(s): o cliente ainda não pagou' % adiantadas) if adiantadas else ''), 'success')
        return redirect(url_for('financeiro.comissao_pedido', fid=fid))
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('financeiro.lancamentos', aba='com'))


@financeiro.route('/financeiro/lancamentos/fechamentos/<int:fid>/pagar', methods=['POST'])
@permission_required(PERM)
def fechamento_pagar(fid):
    if _vendo_como():
        flash('Você está vendo como outra pessoa: volte a ser você para aprovar, reprovar ou pagar.', 'warning')
        return redirect(url_for('financeiro.lancamentos'))
    if not _aprovador():
        abort(403)
    try:
        st = FinComissaoFechamento.registrar_pagamento(fid, _data(request.form.get('data')) or date.today(),
                                                      _dec(request.form.get('valor')), current_user.id)
        registrar('escrita.pagou_comissao', 'financeiro', tabela='fin_comissao_fechamentos', registro_id=fid,
                  depois={'data': request.form.get('data'), 'valor': request.form.get('valor'), 'status': st})
        flash('Pagamento registrado. Pedido %s.' % ('quitado' if st == 'PAGO' else 'pago em parte'), 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    if request.form.get('volta') == 'pedido':      # pagou de dentro da página do pedido
        return redirect(url_for('financeiro.comissao_pedido', fid=fid))
    return redirect(url_for('financeiro.lancamentos', aba='com'))


# ---------------------------------------------------------------------------
# Acordos (admin)
# ---------------------------------------------------------------------------
@financeiro.route('/financeiro/lancamentos/fechamentos/<int:fid>/cancelar', methods=['POST'])
@permission_required(PERM)
def fechamento_cancelar(fid):
    """Quem pediu cancela o próprio pedido enquanto não foi pago; quem aprova cancela qualquer um (18/11/2026 —
    Anderson: "Cliquei errado, desfazer... até pagar tem que ter a opção do próprio usuário excluir")."""
    f = FinComissaoFechamento.get(fid)
    if not f or (not _aprovador() and f['usuario_id'] != _eu().id):
        abort(404)
    if _vendo_como():
        flash('Você está vendo como outra pessoa: volte a ser você para cancelar o pedido.', 'warning')
        return redirect(url_for('financeiro.lancamentos', aba='com'))
    try:
        n = FinComissaoFechamento.cancelar(fid)
        registrar('escrita.cancelou_pedido_comissao', 'financeiro', tabela='fin_comissao_fechamentos', registro_id=fid,
                  depois={'de': f['usuario_nome'], 'total': str(f['total']), 'itens': n})
        flash('Pedido nº %d cancelado. As comissões voltaram para a sua tela; o que foi adiantado volta a esperar o cliente pagar.' % fid, 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('financeiro.lancamentos', aba='com'))


@financeiro.route('/financeiro/lancamentos/acordos/novo', methods=['POST'])
@permission_required(PERM)
def acordo_novo():
    if not _admin():
        abort(403)
    f = request.form
    try:
        aid = FinComissaoAcordo.criar(
            f.get('usuario_id', type=int), f.get('base') or 'SERVICO', f.get('tipo') or 'PCT', _dec(f.get('valor')),
            _data(f.get('vigencia_inicio')) or (_data((f.get('a_partir') or '') + '-01') if f.get('a_partir') else None) or _hoje_br(),
            servico_id=f.get('servico_id', type=int) or None,
            cliente_id=f.get('cliente_id', type=int) or None, parcelas=f.get('parcelas', type=int) or 1,
            observacao=(f.get('observacao') or '').strip() or None, criado_por=current_user.id, aprovado_por=current_user.id)
        registrar('escrita.criou_acordo_comissao', 'financeiro', tabela='fin_comissao_acordos', registro_id=aid,
                  depois={k: f.get(k) for k in ('usuario_id', 'base', 'tipo', 'valor', 'servico_id', 'cliente_id', 'parcelas', 'vigencia_inicio')})
        flash('Acordo de comissão cadastrado.', 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    if f.get('volta') == 'regras':
        return redirect(url_for('financeiro.comissao_regras') + ('#p%s' % f.get('usuario_id') if f.get('usuario_id') else ''))
    return redirect(url_for('financeiro.lancamentos', aba='com') + '#acordos')


@financeiro.route('/financeiro/lancamentos/acordos/<int:aid>/encerrar', methods=['POST'])
@permission_required(PERM)
def acordo_encerrar(aid):
    if not _admin():
        abort(403)
    a = FinComissaoAcordo.get(aid)
    if a:
        FinComissaoAcordo.encerrar(aid)
        registrar('escrita.encerrou_acordo_comissao', 'financeiro', tabela='fin_comissao_acordos', registro_id=aid,
                  antes={'usuario_id': a['usuario_id'], 'base': a['base'], 'valor': str(a['valor'])}, depois={'ativo': 0})
        flash('Acordo encerrado.', 'info')
    if request.form.get('volta') == 'regras':
        return redirect(url_for('financeiro.comissao_regras') + ('#p%s' % a['usuario_id'] if a else ''))
    return redirect(url_for('financeiro.lancamentos', aba='com') + '#acordos')
