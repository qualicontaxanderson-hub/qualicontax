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


def _contrato_ativo(cliente_id):
    """19/11/2026 — "na mensalidade" só para quem tem contrato de honorários ATIVO (Anderson: "se não tem o MENSAL
    REGISTRADO então não haverá envio"). Sem contrato não existe boleto do mês para a cobrança entrar."""
    return bool(execute_query("SELECT 1 FROM fin_contratos WHERE cliente_id = %s AND empresa_id = %s AND situacao = 'ATIVO' LIMIT 1",
                              (cliente_id, EMP), fetch=True))


def _desconto_do_form(f):
    """19/11/2026 — o Desconto da Nova cobrança: {'tipo': FIXO|PCT, 'valor', 'sobre': SERVICO|MENSAL, 'motivo'} ou None."""
    tipo = (f.get('desc_tipo') or '').strip().upper()
    if not tipo:
        return None
    if tipo not in ('FIXO', 'PCT'):
        raise ValueError('Tipo de desconto inválido.')
    v = _dec(f.get('desc_valor'))
    if v is None or v <= 0:
        raise ValueError('Informe o valor do desconto.')
    if tipo == 'PCT' and v >= 100:
        raise ValueError('O desconto em porcentagem precisa ser menor que 100%.')
    motivo = (f.get('desc_motivo') or '').strip()
    if not motivo:
        raise ValueError('Diga o motivo do desconto (fica no controle dos descontos).')
    sobre = (f.get('desc_sobre') or 'SERVICO').strip().upper()
    return {'tipo': tipo, 'valor': v, 'sobre': 'MENSAL' if sobre == 'MENSAL' else 'SERVICO', 'motivo': motivo[:200]}


def _mensal_de(cliente_id):
    r = execute_query("SELECT COALESCE(SUM(valor), 0) v FROM fin_contratos WHERE cliente_id = %s AND empresa_id = %s AND situacao = 'ATIVO'",
                      (cliente_id, EMP), fetch=True, fetch_one=True) or {}
    return Decimal(str(r.get('v') or 0))


def _valor_desconto(desc, base, do_que):
    base = Decimal(str(base or 0))
    dv = (base * desc['valor'] / 100).quantize(Decimal('0.01')) if desc['tipo'] == 'PCT' else desc['valor']
    if base <= 0:
        raise ValueError('Não há %s para descontar.' % do_que)
    if dv > base:
        raise ValueError('O desconto (R$ %s) não pode ser maior que %s (R$ %s).' % (dv, do_que, base))
    return dv


SEM_MENSAL = ('Esta empresa não tem contrato de honorários ativo: não existe mensalidade para incluir a cobrança. '
              'Emita avulso (boleto agora ou na aprovação) ou cadastre os honorários em Honorários › Sem honorários.')


def _aprovador():
    """Aprova as solicitações e paga as comissões: o admin ou quem tem a permissão (17/11/2026 — a Alessandra,
    que "não será ADMIN mas terá a prerrogativa de autorizar")."""
    return _admin() or _eu().has_permission('financeiro.lancamentos.aprovar')


def _meu_ou_admin(l):
    if not l or (not _aprovador() and l['lancado_por'] != _eu().id):
        abort(404)
    return l


def _servicos():
    return execute_query("""SELECT s.id, s.nome, s.valor_padrao, s.emite_nota, s.comissao_tipo, s.comissao_valor,
                                   (SELECT COUNT(*) FROM fin_lancamentos x WHERE x.servico_id = s.id AND x.status <> 'CANCELADO') AS n
                              FROM nfse_servicos s WHERE s.empresa_id = %s AND s.ativo = 1 ORDER BY s.ordem, s.nome""", (EMP,), fetch=True) or []


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
    return 'Boleto + nota' if _sai_na_nota(l) else 'Só boleto'


def _sai_na_nota(l):
    """Se a cobrança avulsa deste lançamento leva nota fiscal. O que a pessoa pediu (com_nota) vale, MAS o cadastro do
    serviço manda: emite_nota = 0 (taxa da Junta, ressarcimento, cartório, Correios) é trava — nunca sai em nota, nem
    pedindo, nem aprovando (20/11/2026 — Anderson: "precisamos ter isso no cadastro pra travar")."""
    if not l.get('emite_nota', 1):
        return False
    return bool(l['com_nota'] if l.get('com_nota') is not None else l.get('emite_nota'))


def _servico_sem_nota(servico_id):
    """Serviço travado no cadastro: nunca sai em nota fiscal (nfse_servicos.emite_nota = 0)."""
    s = execute_query('SELECT emite_nota FROM nfse_servicos WHERE id = %s', (servico_id,), fetch=True, fetch_one=True) or {}
    return not s.get('emite_nota', 1)


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
    # pedido de comissão pago: o extrato que puxou a saída quita sozinho e (19/11/2026) a saída vai para o DRE
    from utils import usuario_dre
    usuario_dre.comissoes_pelo_extrato()
    quem = None if aprov else _eu().id
    todos = FinLancamento.listar(EMP, lancado_por=quem, origem=None if not aprov else ('SERVICO', 'DESCONTO'), limite=1500)
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
    # 19/11/2026: quem ainda não tem a comissão vinculada no DRE — o cartão pede o vínculo antes do "Registrar pago"
    sem_dre = usuario_dre.sem_comissao(f['usuario_id'] for f in pedidos if f['status'] != 'CANCELADO') if aprov else set()
    cats_dre = usuario_dre.categorias_despesa() if sem_dre else []
    acordos = [a for a in FinComissaoAcordo.listar(usuario_id=_eu().id) if a['base'] == 'SERVICO']
    clientes = execute_query(
        """SELECT id, numero_cliente AS num, nome_razao_social AS nome, cpf_cnpj AS doc, COALESCE(avulso, 0) AS av, cobranca_pagador_id AS pag
             FROM clientes WHERE COALESCE(situacao, 'ATIVO') <> 'INATIVO' ORDER BY nome_razao_social""", fetch=True) or []
    mensal = {r['cliente_id']: float(r['v'] or 0) for r in execute_query(
        """SELECT cliente_id, SUM(valor) v FROM fin_contratos WHERE ativo = 1 AND COALESCE(situacao, 'ATIVO') = 'ATIVO'
            GROUP BY cliente_id""", fetch=True) or []}
    comp0 = FinLoteMensal.competencia_aberta(EMP)
    comps = [comp0]
    while len(comps) < 12:          # 18/11/2026: parcelas na mensalidade escolhem o mês de cada uma
        comps.append(date(comps[-1].year + (comps[-1].month == 12), comps[-1].month % 12 + 1, 1))
    caixa = {
        'clientes': [{'id': c['id'], 'num': str(c['num'] or ''), 'nome': c['nome'], 'doc': c['doc'] or '', 'av': bool(c['av']),
                      'mensal': mensal.get(c['id'], 0), 'pag': c.get('pag') or None} for c in clientes],
        'servicos': [{'id': s['id'], 'nome': s['nome'], 'valor': float(s['valor_padrao'] or 0), 'nota': bool(s['emite_nota']),
                      'ct': s.get('comissao_tipo') or '', 'cv': float(s.get('comissao_valor') or 0), 'n': int(s.get('n') or 0)}
                     for s in _servicos() if s['nome'] != 'Desconto'],
        'acordos': [{'servico_id': a['servico_id'], 'tipo': a['tipo'], 'valor': float(a['valor'])} for a in acordos],
        'comps': [{'v': c.isoformat(), 'rot': c.strftime('%m/%Y')} for c in comps],
        'hoje': hoje.isoformat(), 'eu': getattr(_eu(), 'nome', ''), 'aprovador': aprov,
    }
    return render_template(
        'financeiro/lancamentos.html', aprov=aprov, admin=_admin(), hoje=hoje, grupos=grupos, fora=fora[:30],
        tot={k: soma(v) for k, v in grupos.items()}, liberadas=liberadas, meus_com=meus_com, aguard=aguard, tem_adiantavel=tem_adiantavel,
        tot_liberadas=sum(float(l['comissao_valor'] or 0) for l in liberadas),
        pedidos=pedidos, a_pagar=a_pagar, sem_dre=sem_dre, cats_dre=cats_dre, tot_a_pagar=sum(float(f['total'] or 0) - float(f['valor_pago'] or 0) for f in a_pagar),
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
    ('financeiro.cobrancas', 'Notas e boletos'), ('financeiro.servicos', 'Serviços'),
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
    from utils import usuario_dre
    usuario_dre.comissoes_pelo_extrato()
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
    """Endereço antigo das Regras de comissão: desde 20/11/2026 é a aba SERVIÇOS do Financeiro (links antigos seguem valendo)."""
    return redirect(url_for('financeiro.servicos', aba=request.args.get('aba')), code=301)


@financeiro.route('/financeiro/servicos')
@permission_required(PERM)
def servicos():
    """SERVIÇOS — aba do Financeiro (20/11/2026 — Anderson: "Criar uma aba de serviços e dentro temos a opção de ver
    quem recebe comissão"). Nasceu como "Regras de comissão" dentro de Lançamentos (17/11/2026: "como farei o cadastramento
    do que tem Comissão e o que não tem e quem recebe ou não?"). Só o administrador. Duas partes: SERVIÇOS (o cadastro do que
    cobramos: valor sugerido, comissão e a trava de nota fiscal) e QUEM RECEBE COMISSÃO (cada pessoa: liga/desliga e o %
    padrão; carteira de mensalidades, ex.: Guilherme 25% das empresas dele)."""
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
    clientes = execute_query("""SELECT c.id, c.numero_cliente AS num, c.nome_razao_social AS nome, c.cpf_cnpj AS doc,
                                    (SELECT SUM(k.valor) FROM fin_contratos k WHERE k.cliente_id = c.id AND k.ativo = 1) AS mensal
                               FROM clientes c WHERE COALESCE(c.situacao, 'ATIVO') <> 'INATIVO' AND COALESCE(c.avulso, 0) = 0
                              ORDER BY c.nome_razao_social""", fetch=True) or []
    return render_template('financeiro/comissao_regras.html', pessoas=pessoas, servicos=servicos, hoje=date.today(),
                           aba=request.args.get('aba') if request.args.get('aba') in ('pessoas', 'servicos') else 'servicos',
                           n_recebem=sum(1 for p in pessoas if p['geral']), n_carteira=sum(len(p['carteira']) for p in pessoas),
                           n_serv_regra=sum(1 for s in servicos if s.get('comissao_tipo')),
                           n_serv_sem=sum(1 for s in servicos if s.get('comissao_tipo') == 'SEM'),
                           n_serv_sem_nota=sum(1 for s in servicos if not s.get('emite_nota')),
                           clientes_json=json.dumps([{'id': c['id'], 'num': str(c['num'] or ''), 'nome': c['nome'], 'doc': c['doc'] or '',
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
        return redirect(url_for('financeiro.servicos', aba='pessoas'))
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
    return redirect(url_for('financeiro.servicos', aba='pessoas') + '#p%d' % uid)


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
    s = execute_query('SELECT id, nome, comissao_tipo, comissao_valor, valor_padrao, emite_nota FROM nfse_servicos WHERE id = %s AND empresa_id = %s',
                      (sid, EMP), fetch=True, fetch_one=True)
    if not s or tipo not in ('', 'PCT', 'FIXO', 'SEM'):
        abort(400)
    # 20/11/2026 — trava de NOTA FISCAL no cadastro do serviço: 0 = nunca sai em nota (só boleto), 1 = sai na nota
    emite = s['emite_nota']
    if request.form.get('emite_nota') in ('0', '1'):
        emite = int(request.form.get('emite_nota'))
    # 20/11/2026 — Anderson: "Como editar os nomes?": o nome do serviço se edita no próprio cartão (antes, só em Configuração fiscal)
    nome = ' '.join((request.form.get('nome') or '').split())[:80] or s['nome']
    if nome != s['nome'] and execute_query('SELECT id FROM nfse_servicos WHERE empresa_id = %s AND LOWER(nome) = LOWER(%s) AND id <> %s',
                                           (EMP, nome, sid), fetch=True):
        flash('Já existe um serviço chamado "%s". Escolha outro nome.' % nome, 'danger')
        return redirect(url_for('financeiro.servicos', aba='servicos') + '#s%d' % sid)
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
            return redirect(url_for('financeiro.servicos', aba='servicos') + '#s%d' % sid)
        return redirect(url_for('financeiro.lancamentos', aba='com') + '#servicos-comissao')
    execute_query('UPDATE nfse_servicos SET nome = %s, comissao_tipo = %s, comissao_valor = %s, valor_padrao = %s, emite_nota = %s WHERE id = %s',
                  (nome, tipo or None, valor, sugerido, emite, sid), fetch=False)
    destravados = 0
    if not emite and s['emite_nota']:
        # travou agora: os pedidos ainda não aprovados que pediam nota passam a "só boleto"
        pend = execute_query("SELECT id FROM fin_lancamentos WHERE servico_id = %s AND status = 'LANCADO' AND destino = 'AVULSO' AND com_nota = 1",
                             (sid,), fetch=True) or []
        if pend:
            execute_query("UPDATE fin_lancamentos SET com_nota = 0 WHERE id IN (%s)" % ','.join(str(p['id']) for p in pend), fetch=False)
            destravados = len(pend)
    registrar('escrita.alterou_comissao_servico', 'financeiro', tabela='nfse_servicos', registro_id=sid,
              antes={'nome': s['nome'], 'comissao_tipo': s['comissao_tipo'], 'comissao_valor': str(s['comissao_valor'] or ''), 'valor_sugerido': str(s['valor_padrao'] or ''),
                     'emite_nota': int(s['emite_nota'])},
              depois={'servico': s['nome'], 'nome': nome, 'comissao_tipo': tipo or None, 'comissao_valor': str(valor or ''), 'valor_sugerido': str(sugerido or ''),
                      'emite_nota': int(emite), 'pedidos_passados_para_so_boleto': destravados or None})
    if int(emite) != int(s['emite_nota']):
        flash(('"%s" não sai mais em nota fiscal: só boleto, para todo mundo.' % s['nome']) + ((' %d pedido%s aguardando aprovação passaram para só boleto.' % (destravados, 's' if destravados > 1 else '')) if destravados else '')
              if not emite else '"%s" volta a sair na nota fiscal.' % s['nome'], 'success')
    elif nome != s['nome']:
        flash('"%s" agora se chama "%s".' % (s['nome'], nome), 'success')
    else:
        flash('"%s" salvo.' % s['nome'], 'success')
    if request.form.get('volta') == 'regras':
        return redirect(url_for('financeiro.servicos', aba='servicos') + '#s%d' % sid)
    return redirect(url_for('financeiro.lancamentos', aba='com') + '#servicos-comissao')


@financeiro.route('/financeiro/servicos/novo', methods=['POST'])
@permission_required(PERM)
def servico_novo():
    """Serviço novo no catálogo, pela aba SERVIÇOS (20/11/2026 — Anderson: "e os Novos?"). Só o administrador. Nome,
    nota fiscal (sai / nunca sai), comissão (padrão da pessoa, %, R$ fixo ou nunca) e valor sugerido. A nota usa o código de
    tributação padrão da Configuração fiscal, como todos os outros serviços do catálogo."""
    if not _admin():
        abort(403)
    f = request.form
    volta = url_for('financeiro.servicos', aba='servicos')
    nome = ' '.join((f.get('nome') or '').split())[:80]
    tipo = f.get('tipo') if f.get('tipo') in ('', 'PCT', 'FIXO', 'SEM') else ''
    emite = 0 if f.get('emite_nota') == '0' else 1
    if not nome:
        flash('Dê um nome ao serviço.', 'danger')
        return redirect(volta)
    if execute_query('SELECT id FROM nfse_servicos WHERE empresa_id = %s AND LOWER(nome) = LOWER(%s)', (EMP, nome), fetch=True):
        flash('Já existe um serviço chamado "%s".' % nome, 'danger')
        return redirect(volta)
    try:
        valor = _dec(f.get('valor')) if tipo in ('PCT', 'FIXO') else None
        if tipo in ('PCT', 'FIXO') and (valor is None or valor <= 0 or (tipo == 'PCT' and valor > 100)):
            raise ValueError('Comissão inválida.')
        vp = (f.get('valor_padrao') or '').strip()
        sugerido = _dec(vp) if vp else None
        if sugerido is not None and sugerido <= 0:
            sugerido = None
    except (ValueError, InvalidOperation):
        flash('Comissão ou valor sugerido inválido para "%s".' % nome, 'danger')
        return redirect(volta)
    ordem = (execute_query('SELECT COALESCE(MAX(ordem), 0) + 1 AS o FROM nfse_servicos WHERE empresa_id = %s AND ordem < 999', (EMP,),
                           fetch=True, fetch_one=True) or {}).get('o') or 1
    sid = execute_query("""INSERT INTO nfse_servicos (empresa_id, nome, emite_nota, ordem, ativo, comissao_tipo, comissao_valor, valor_padrao, alterado_por)
                            VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s)""",
                        (EMP, nome, emite, ordem, tipo or None, valor, sugerido, current_user.id), fetch=False)
    registrar('escrita.criou_servico', 'financeiro', tabela='nfse_servicos', registro_id=sid,
              depois={'nome': nome, 'emite_nota': emite, 'comissao_tipo': tipo or None, 'comissao_valor': str(valor or ''),
                      'valor_sugerido': str(sugerido or ''), 'origem': 'aba Serviços'})
    flash('Serviço "%s" criado.' % nome + (' Nunca sai em nota fiscal: entra só no boleto.' if not emite else ''), 'success')
    return redirect(volta + '#s%s' % sid)


@financeiro.route('/financeiro/lancamentos/novo', methods=['POST'])
@permission_required(PERM)
def lancamento_novo():
    """Solicitação da caixa "Nova solicitação": como cobrar (boleto + nota / só boleto / na mensalidade), serviço,
    valor, vencimento e a comissão pedida. Nada sai agora: a cobrança é emitida na aprovação."""
    f = request.form
    try:
        cliente_id = f.get('cliente_id', type=int)
        servico_id = f.get('servico_id', type=int)
        como = f.get('como') or 'BOLETO_NOTA'
        if como not in ('BOLETO_NOTA', 'BOLETO', 'MENSAL'):
            raise ValueError('Escolha como cobrar.')
        destino = 'MENSAL' if como == 'MENSAL' else 'AVULSO'
        desc = _desconto_do_form(f)          # 19/11/2026: Desconto junto (sobre o serviço) ou só o desconto (na mensalidade)
        if not cliente_id:
            raise ValueError('Escolha o cliente.')
        so_desconto = bool(desc and desc['sobre'] == 'MENSAL' and not servico_id)
        if not servico_id and not so_desconto:
            raise ValueError('Escolha o serviço.')
        if desc and destino == 'AVULSO':
            desc['sobre'] = 'SERVICO'             # avulso: o desconto é sempre sobre o serviço desta cobrança
        if so_desconto and destino != 'MENSAL':
            raise ValueError('Desconto na mensalidade só com "Incluir na mensalidade".')
        s = execute_query('SELECT nome FROM nfse_servicos WHERE id = %s AND empresa_id = %s', (servico_id, EMP), fetch=True, fetch_one=True) if servico_id else None
        if servico_id and not s:
            raise ValueError('Serviço inválido.')
        if destino == 'MENSAL' and not _contrato_ativo(cliente_id):
            raise ValueError(SEM_MENSAL)
        comp = _data(f.get('competencia')) if destino == 'MENSAL' else None
        venc = _data(f.get('vencimento')) if destino == 'AVULSO' else None
        valor = _dec(f.get('valor')) if servico_id else Decimal('0')
        # 19/11/2026 — "Quem paga": outra empresa paga este serviço (só avulso). O serviço fica na dona; boleto e nota saem no nome de quem paga.
        pagador = f.get('pagador_cliente_id', type=int) if destino == 'AVULSO' else None
        if pagador and pagador != cliente_id:
            if not execute_query("SELECT 1 FROM clientes WHERE id = %s AND COALESCE(situacao, 'ATIVO') <> 'INATIVO'", (pagador,), fetch=True):
                raise ValueError('A empresa que paga não está no cadastro (ou está inativa).')
        else:
            pagador = None
        dv = None
        if desc:
            dv = _valor_desconto(desc, valor if desc['sobre'] == 'SERVICO' else _mensal_de(cliente_id),
                                 'o serviço' if desc['sobre'] == 'SERVICO' else 'a mensalidade')
        lid = did = None
        n, datas = 1, None
        if servico_id:
            lid = FinLancamento.criar(EMP, cliente_id, servico_id, (f.get('descricao') or s['nome']).strip() or s['nome'],
                                      Decimal('1'), valor, destino, _eu().id, vencimento=venc,
                                      observacao=(f.get('observacao') or '').strip() or None, competencia=comp,
                                      com_nota=None if destino == 'MENSAL' else (como == 'BOLETO_NOTA' and not _servico_sem_nota(servico_id)),
                                      comissao_tipo='SEM' if FinLancamento.servico_travado(servico_id) else (f.get('comissao_tipo') or 'PADRAO'),
                                      comissao_valor=None if FinLancamento.servico_travado(servico_id) else (f.get('comissao_valor') or None),
                                      pagador_cliente_id=pagador)
            _marcar_pelo_admin(lid)
            n = _parcelas_do_form(f)
            datas = _datas_parcelas(f, n, destino == 'MENSAL')
            if n > 1:
                execute_query('UPDATE fin_lancamentos SET parcelas = %s, parcelas_datas = %s WHERE id = %s',
                              (n, ','.join(d.isoformat() for d in datas) if datas else None, lid), fetch=False)
        if desc:
            rot = ('Desconto s/ ' + (s['nome'] if desc['sobre'] == 'SERVICO' and s else 'a mensalidade')) + ' — ' + desc['motivo']
            did = FinLancamento.criar(EMP, cliente_id, FinLancamento.servico_desconto_id(EMP), rot[:200], Decimal('1'), dv, destino, _eu().id,
                                      vencimento=venc, observacao=desc['motivo'], competencia=comp,
                                      com_nota=None if destino == 'MENSAL' else False, comissao_tipo='SEM', pagador_cliente_id=pagador)
            execute_query("UPDATE fin_lancamentos SET origem = 'DESCONTO', desc_lancamento_id = %s, desc_sobre = %s WHERE id = %s", (lid, desc['sobre'], did), fetch=False)
            _marcar_pelo_admin(did)
            dl = FinLancamento.get(did)
            registrar('escrita.lancou_desconto', 'financeiro', tabela='fin_lancamentos', registro_id=did,
                      depois={'empresa': dl['nome_razao_social'], 'desconto': str(dv), 'tipo': desc['tipo'], 'pedido': str(desc['valor']),
                              'sobre': desc['sobre'], 'servico': s['nome'] if s else None, 'motivo': desc['motivo'], 'como': _como(dl),
                              'ligado_ao_lancamento': lid})
        l = FinLancamento.get(lid or did)
        registrar('escrita.lancou_servico', 'financeiro', tabela='fin_lancamentos', registro_id=l['id'],
                  depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'valor': str(l['valor_total']),
                          'como': _como(l), 'vencimento': str(l['vencimento'] or ''),
                          'comissao_pedida': (l.get('comissao_pedida_tipo') or '') + (' ' + str(l['comissao_pedida_valor']) if l.get('comissao_pedida_valor') else ''),
                          'parcelas': n, 'desconto': str(dv) if dv else None, 'quem_paga': l.get('pagador_nome')})
        if _aprovador() and f.get('emitir') == '1':
            # 17/11/2026 — Anderson: "numa tela só para emissão imediata ou para incluir no mensal". Quem aprova não
            # precisa aprovar a própria: já sai aprovado — avulso emite agora, mensal entra no lote do mês.
            if lid:
                parcelas, com, ok, msgs = _aprovar_e_cobrar(lid, _eu().id, None, None, n, _conta_escolhida(f), datas)
            else:                                  # só o desconto, na mensalidade
                FinLancamento.aprovar(did, _eu().id); parcelas, com, ok, msgs = [FinLancamento.get(did)], Decimal('0'), True, []
            registrar('escrita.aprovou_lancamento', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
                      depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'valor': str(l['valor_total']),
                              'destino': l['destino'], 'comissao': str(com), 'lancado_por': l['lancado_por_nome'], 'direto': True,
                              'parcelas': len(parcelas)})
            if l['destino'] == 'AVULSO':
                for m in msgs:
                    flash(m, 'success' if ok else 'warning')
                if not ok:
                    flash('Ficou aprovado, mas alguma cobrança não saiu. Corrija e use "Cobrar de novo" no cartão.', 'danger')
                return redirect(url_for('financeiro.lancamentos', aba='cob'))
            flash(('Desconto de R$ %s incluído na mensalidade de %s de %s.' % (dv, ', '.join(p['competencia'].strftime('%m/%Y') for p in parcelas), l['nome_razao_social'])) if so_desconto
                  else ('Incluído na mensalidade de %s de %s%s: sai no boleto do lote do mês.' % (', '.join(p['competencia'].strftime('%m/%Y') for p in parcelas), l['nome_razao_social'],
                                                                                              (' com desconto de R$ %s' % dv) if dv else '')), 'success')
            return redirect(url_for('financeiro.lancamentos', aba='cob'))
        flash('Solicitação enviada: %s · %s%s. Anderson ou Alessandra aprovam e a cobrança sai.' % (l['nome_razao_social'], _como(l), (' · desconto de R$ %s' % dv) if dv else ''), 'success')
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
    paga = l.get('pagador_cliente_id') or l['cliente_id']        # 19/11/2026: "Quem paga" — boleto e nota em nome de quem paga
    t = tomador_do_cliente(paga)
    if not t or not t.get('documento'):
        return False, ['O cadastro da empresa %s não tem CPF/CNPJ para o boleto.' % ('que paga' if paga != l['cliente_id'] else '')]
    dona = (' — serviço de %s' % l['nome_razao_social']) if paga != l['cliente_id'] else ''
    vencimento = l['vencimento'] or (date.today())
    if vencimento < date.today():
        vencimento = date.today()
    competencia = l['competencia']
    _venc, mensagem = sugestao_boleto(conta, competencia)
    itens = [{'servico_id': l['servico_id'], 'nome': (l['descricao'] or l['servico_nome']) + dona, 'valor': Decimal(str(l['valor_total'])),
              'emite_nota': _sai_na_nota(l), 'origem': 'honorarios'}]
    total = Decimal(str(l['valor_total']))
    # 19/11/2026: desconto pedido junto (aprovado): a linha do serviço sai pelo líquido dizendo de onde veio; a página do
    # cliente e o e-mail mostram as duas linhas
    descs = FinLancamento.descontos_de(l['id'], status=('APROVADO',))
    desc_total = sum((Decimal(str(d['valor_total'])) for d in descs), Decimal('0'))
    desc_nome = (descs[0]['descricao'] or 'Desconto').split(' — ')[0] if descs else None
    if desc_total > 0:
        from utils.lote_mensal import _abate_desconto
        itens = _abate_desconto(itens, desc_total, 'desconto')
        total = total - desc_total
        if total <= 0 or not itens:
            return False, ['O desconto cobre o serviço inteiro: não há o que cobrar.']
    cfg = NfseConfig.get(l['empresa_id']) if itens[0]['emite_nota'] else None
    nfse_id = boleto_id = titulo_id = None
    if cfg:
        serie = cfg.get('serie_nacional') if (cfg.get('emissor') or 'nacional') == 'nacional' else cfg.get('serie_sp')
        form = MultiDict([('serie', str(serie or '')), ('competencia', competencia.isoformat()),
                          ('tomador_cliente_id', str(paga)), ('descricao', (l['descricao'] or l['servico_nome']) + dona + ((' Desconto de R$ %s já abatido.' % desc_total) if desc_total > 0 else '')),
                          ('item_servico_id', str(l['servico_id'])), ('item_valor', str(itens[0]['valor']))])
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
        boleto_id, ok, msg, aviso = emitir_boleto_avulso(l['empresa_id'], conta, _pagador_do_tomador(t), itens, total,
                                                         competencia, vencimento, mensagem, None, usuario_id,
                                                         desconto=desc_total if desc_total > 0 else None, desconto_nome=desc_nome)
        msgs.append(msg)
        if aviso:
            msgs.append(aviso)
        if boleto_id:
            titulo_id = (FinBoleto.get(boleto_id) or {}).get('titulo_id')
    if ok:
        FinLancamento.marcar_faturado(l['id'], titulo_id=titulo_id, nfse_id=nfse_id, boleto_id=boleto_id)
        for d in descs:
            FinLancamento.marcar_faturado(d['id'], titulo_id=titulo_id, nfse_id=nfse_id, boleto_id=boleto_id)
    return ok, msgs


def _parcelas_do_form(form):
    try:
        n = int(form.get('parcelas') or 1)
    except ValueError:
        n = 1
    return max(1, min(n, 12))


def _datas_parcelas(form, n, mensal):
    """As datas de cada parcela escolhidas na caixa (Anderson: "tem que ter a opção de escolher as datas tipo
    05/12/2026 20/12/2026 05/01/2027" e "incluir nas mensalidades 11/26 12/26 01/27"). None = mês a mês."""
    if n <= 1:
        return None
    raw = [x for x in form.getlist('parc_comp' if mensal else 'parc_venc') if x]
    if len(raw) != n:
        return None
    ds = [_data(x) for x in raw]
    if mensal:
        return [d.replace(day=1) for d in ds]
    for i, d in enumerate(ds):
        if d < _hoje_br():
            raise ValueError('A data da %dª parcela (%s) já passou.' % (i + 1, d.strftime('%d/%m/%Y')))
    return ds


def _somar_meses(d, k):
    import calendar
    m = d.month - 1 + k
    a, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=a, month=m, day=min(d.day, calendar.monthrange(a, m)[1]))


def _repartir(total, n):
    """total em n partes de centavos inteiros; a sobra dos centavos fica na 1ª."""
    from decimal import ROUND_DOWN
    total = Decimal(str(total))
    base = (total / n).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    return [total - base * (n - 1)] + [base] * (n - 1)


def _dividir(l, n, datas=None):
    """Divide o lançamento (ainda LANCADO) em n parcelas — 18/11/2026, Anderson: "quando precisarmos de serviços
    parcelados... 1500,00... dividir em 2 ou 3 parcelas". Cada parcela é um lançamento próprio (uma nota e um boleto
    por parcela, ou um pedaço em cada mensalidade), ligado pela parcela_grupo (o id da 1ª). Avulso vence mês a mês a
    partir do vencimento escolhido; mensalidade vai para os meses seguintes. Devolve os ids na ordem."""
    base = re.sub(r'\s*\(parcela \d+/\d+\)$', '', (l['descricao'] or l['servico_nome'] or '').strip())
    valores = _repartir(l['valor_total'], n)
    pt, pv = l.get('comissao_pedida_tipo'), l.get('comissao_pedida_valor')
    pvs = _repartir(pv, n) if pt == 'FIXO' and pv else [pv] * n
    mensal = l['destino'] == 'MENSAL'
    if datas and len(datas) == n:
        vencs = [None] * n if mensal else datas
        comps = [FinLoteMensal.competencia_aberta(l['empresa_id'], d) for d in datas] if mensal else [l['competencia']] * n
    else:
        vencs = [None] * n if mensal else [_somar_meses(l['vencimento'], k) for k in range(n)]
        comps = [_somar_meses(l['competencia'], k) for k in range(n)] if mensal else [l['competencia']] * n
    execute_query("""UPDATE fin_lancamentos SET quantidade = 1, valor_unitario = %s, valor_total = %s, descricao = %s,
                         comissao_pedida_valor = %s, parcela = 1, parcelas = %s, parcela_grupo = id, vencimento = %s, competencia = %s
                      WHERE id = %s AND status = 'LANCADO'""",
                  (valores[0], valores[0], ('%s (parcela 1/%d)' % (base, n))[:200], pvs[0], n, vencs[0], comps[0], l['id']), fetch=False)
    ids = [l['id']]
    for k in range(1, n):
        nid = FinLancamento.criar(
            l['empresa_id'], l['cliente_id'], l['servico_id'], '%s (parcela %d/%d)' % (base, k + 1, n), Decimal('1'), valores[k],
            l['destino'], l['lancado_por'],
            vencimento=vencs[k], observacao=l.get('observacao'), competencia=comps[k],
            com_nota=l.get('com_nota'), comissao_tipo=pt, comissao_valor=pvs[k], pagador_cliente_id=l.get('pagador_cliente_id'))
        execute_query("""UPDATE fin_lancamentos SET parcela = %s, parcelas = %s, parcela_grupo = %s, lancado_pelo_admin = %s
                          WHERE id = %s""", (k + 1, n, l['id'], l.get('lancado_pelo_admin'), nid), fetch=False)
        ids.append(nid)
    # 19/11/2026: o desconto pedido junto acompanha as parcelas, na mesma proporção
    for d in FinLancamento.descontos_de(l['id']):
        partes = _repartir(d['valor_total'], n)
        bd = re.sub(r'\s*\(parcela \d+/\d+\)$', '', (d['descricao'] or '').strip())
        execute_query("""UPDATE fin_lancamentos SET valor_unitario = %s, valor_total = %s, descricao = %s, parcela = 1, parcelas = %s,
                             parcela_grupo = id, vencimento = %s, competencia = %s WHERE id = %s""",
                      (partes[0], partes[0], ('%s (parcela 1/%d)' % (bd, n))[:200], n, vencs[0], comps[0], d['id']), fetch=False)
        for k in range(1, n):
            nd = FinLancamento.criar(d['empresa_id'], d['cliente_id'], d['servico_id'], '%s (parcela %d/%d)' % (bd, k + 1, n), Decimal('1'), partes[k],
                                     d['destino'], d['lancado_por'], vencimento=vencs[k], observacao=d.get('observacao'), competencia=comps[k],
                                     com_nota=d.get('com_nota'), comissao_tipo='SEM', pagador_cliente_id=d.get('pagador_cliente_id'))
            execute_query("""UPDATE fin_lancamentos SET origem = 'DESCONTO', parcela = %s, parcelas = %s, parcela_grupo = %s, desc_lancamento_id = %s, desc_sobre = %s,
                                 status = %s, lancado_pelo_admin = %s WHERE id = %s""",
                          (k + 1, n, d['id'], ids[k], d.get('desc_sobre'), d['status'], d.get('lancado_pelo_admin'), nd), fetch=False)
    return ids


def _aprovar_e_cobrar(lid, usuario_id, ct=None, cv=None, n=1, conta_id=None, datas=None):
    """Aprova (dividindo em n parcelas se pedirem) e cobra o avulso. A comissão é a do TOTAL, repartida entre as
    parcelas (acordo fixo de R$ 100 não vira R$ 100 por parcela) e cada pedaço libera quando aquela parcela é paga.
    Devolve (lista de lançamentos, comissão total, ok, mensagens)."""
    l = FinLancamento.get(lid)
    acordo, com = FinLancamento.comissao_calculada(l, ct, cv)
    if com > Decimal(str(l['valor_total'])):
        raise ValueError('A comissão não pode ser maior que o valor cobrado.')
    if n > 1 and not l.get('parcela'):
        ids = _dividir(l, n, datas)
        coms = _repartir(com, n)
        for i, pid in enumerate(ids):
            FinLancamento.aprovar(pid, usuario_id, comissao_tipo='FIXO' if coms[i] > 0 else 'SEM', comissao_valor=coms[i] if coms[i] > 0 else None)
    else:
        ids = [lid]
        com = FinLancamento.aprovar(lid, usuario_id, comissao_tipo=ct, comissao_valor=cv)
    ok, msgs = True, []
    if l['destino'] == 'AVULSO':
        for i, pid in enumerate(ids):
            o, m = _cobrar_avulso(FinLancamento.get(pid), usuario_id, conta_id)
            ok = ok and o
            msgs += [('Parcela %d/%d: ' % (i + 1, len(ids)) + x) if len(ids) > 1 else x for x in m]
    return [FinLancamento.get(i) for i in ids], com, ok, msgs


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
                if not _contrato_ativo(l['cliente_id']):
                    flash(SEM_MENSAL, 'danger')
                    return redirect(url_for('financeiro.lancamentos', aba='apr'))
                destino, com_nota, venc = 'MENSAL', None, None
                comp = FinLoteMensal.competencia_aberta(EMP, _data(request.form.get('competencia')) or l['competencia'])
            else:
                destino, com_nota = 'AVULSO', 1 if como == 'BOLETO_NOTA' else 0
                if com_nota and not l.get('emite_nota', 1):
                    com_nota = 0            # trava do cadastro do serviço: nunca sai em nota
                    flash('"%s" não entra na nota fiscal (trava do cadastro do serviço): sozinho sai só com boleto; na mensalidade entra no boleto e fica fora da nota.' % l['servico_nome'], 'info')
                venc = _data(request.form.get('vencimento')) or l['vencimento']
                if not venc or venc < _hoje_br():
                    raise ValueError
                comp = l['competencia'] if l['destino'] == 'AVULSO' and l['competencia'] else _hoje_br().replace(day=1)
        except ValueError:
            flash('Vencimento inválido: escolha hoje ou uma data à frente.', 'danger')
            return redirect(url_for('financeiro.lancamentos', aba='apr'))
        antes = {'como': _como(l), 'vencimento': str(l['vencimento'] or '')}
        nota_antes = _sai_na_nota(l)
        mudou = (destino != l['destino'] or venc != l['vencimento'] or comp != l['competencia']
                 or (destino == 'AVULSO' and bool(com_nota) != bool(nota_antes)))
        if mudou:
            execute_query("UPDATE fin_lancamentos SET destino = %s, com_nota = %s, vencimento = %s, competencia = %s, "
                          "pagador_cliente_id = IF(%s = 'MENSAL', NULL, pagador_cliente_id) WHERE id = %s AND status = 'LANCADO'",
                          (destino, com_nota, venc, comp, destino, lid), fetch=False)
            execute_query("UPDATE fin_lancamentos SET destino = %s, com_nota = %s, vencimento = %s, competencia = %s "
                          "WHERE desc_lancamento_id = %s AND origem = 'DESCONTO' AND status = 'LANCADO'",
                          (destino, None if destino == 'MENSAL' else 0, venc, comp, lid), fetch=False)
            l = FinLancamento.get(lid)
            registrar('escrita.alterou_cobranca_lancamento', 'financeiro', tabela='fin_lancamentos', registro_id=lid, antes=antes,
                      depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'como': _como(l),
                              'vencimento': str(l['vencimento'] or '')})
    n = _parcelas_do_form(request.form)
    try:
        datas = _datas_parcelas(request.form, n, (request.form.get('como') or ('MENSAL' if l['destino'] == 'MENSAL' else '')) == 'MENSAL')
        parcelas, com, ok, msgs = _aprovar_e_cobrar(lid, current_user.id, ct,
                                                    _dec(request.form.get('comissao_valor')) if ct in ('PCT', 'FIXO') else None,
                                                    n, _conta_escolhida(request.form), datas)
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor da comissão inválido.', 'danger')
        return redirect(url_for('financeiro.lancamentos', aba='apr'))
    registrar('escrita.aprovou_lancamento', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
              depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'valor': str(l['valor_total']),
                      'destino': l['destino'], 'comissao': str(com), 'lancado_por': l['lancado_por_nome'],
                      'parcelas': len(parcelas)})
    if l['destino'] == 'AVULSO':
        for m in msgs:
            flash(m, 'success' if ok else 'warning')
        if not ok:
            flash('Aprovado, mas alguma cobrança não saiu. Corrija e use "Cobrar de novo" no cartão.', 'danger')
    elif len(parcelas) > 1:
        flash('Aprovado em %d parcelas: entram nas mensalidades de %s.' % (len(parcelas), ', '.join(p['competencia'].strftime('%m/%Y') for p in parcelas)), 'success')
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


@financeiro.route('/financeiro/lancamentos/dre-comissao/<int:uid>', methods=['POST'])
@permission_required(PERM)
def comissao_vincular_dre(uid):
    """Vincula a COMISSÃO da pessoa a uma categoria do DRE (19/11/2026 — Anderson: "quando fazer a primeira vez obrigar
    a gente a vincular no usuário e após isso não precisa mais"). Grava no usuário (o mesmo de Configurações → Usuários
    → No DRE) e já manda para o DRE as saídas de comissão dela que estavam sem categoria."""
    if _vendo_como():
        flash('Você está vendo como outra pessoa: volte a ser você para alterar.', 'warning')
        return redirect(url_for('financeiro.lancamentos', aba='com'))
    if not _aprovador():
        abort(403)
    from utils import usuario_dre
    try:
        antes = usuario_dre.categoria(uid, 'COMISSAO')
        if not request.form.get('categoria_id'):
            raise ValueError('Escolha a conta do DRE.')
        usuario_dre.salvar(uid, 'COMISSAO', request.form.get('categoria_id'), current_user.id)
        registrar('escrita.vinculou_dre_usuario', 'financeiro', tabela='fin_usuario_dre', registro_id=uid,
                  antes={'COMISSAO': antes}, depois={'COMISSAO': int(request.form.get('categoria_id'))})
        feitos = usuario_dre.classificar_comissoes_pendentes(uid)
        for eid, cid, res in feitos:
            registrar('escrita.classificou_comissao_no_dre', 'financeiro', tabela='extrato_lancamentos', registro_id=eid,
                      depois={'categoria_id': cid, 'conciliacao': res})
        msg = 'Pronto: as comissões dessa pessoa vão sozinhas para essa conta do DRE.'
        if feitos:
            msg += ' %d pagamento%s já lançado%s.' % (len(feitos), 's' if len(feitos) > 1 else '', 's' if len(feitos) > 1 else '')
        flash(msg, 'success')
    except ValueError as e:
        flash(str(e), 'danger')
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
        return redirect(url_for('financeiro.servicos', aba='pessoas') + ('#p%s' % f.get('usuario_id') if f.get('usuario_id') else ''))
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
        return redirect(url_for('financeiro.servicos', aba='pessoas') + ('#p%s' % a['usuario_id'] if a else ''))
    return redirect(url_for('financeiro.lancamentos', aba='com') + '#acordos')
