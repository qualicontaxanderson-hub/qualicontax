# -*- coding: utf-8 -*-
"""Lançamentos de serviço, aprovação, cobrança avulsa e comissões (parte 6, etapa 2 — 06/11/2026).

Quem não é admin vê SÓ o que lançou (e só os totais dele). O admin (Anderson) vê tudo, aprova,
reprova, cadastra os acordos de comissão e registra o pagamento dos fechamentos.
Aprovar um lançamento AVULSO cobra na hora: nota (se o serviço emite nota) + boleto Efí + e-mail aos
contatos do Financeiro. Aprovar um MENSAL só o deixa pronto para o lote da competência.
"""
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import abort, flash, redirect, render_template, request, url_for
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


def _admin():
    return bool(getattr(current_user, 'is_admin', lambda: False)())


def _aprovador():
    """Aprova as solicitações e paga as comissões: o admin ou quem tem a permissão (17/11/2026 — a Alessandra,
    que "não será ADMIN mas terá a prerrogativa de autorizar")."""
    return _admin() or current_user.has_permission('financeiro.lancamentos.aprovar')


def _meu_ou_admin(l):
    if not l or (not _aprovador() and l['lancado_por'] != current_user.id):
        abort(404)
    return l


def _servicos():
    return execute_query("SELECT id, nome, valor_padrao, emite_nota FROM nfse_servicos WHERE empresa_id = %s AND ativo = 1 ORDER BY ordem, nome",
                         (EMP,), fetch=True) or []


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
    quem = None if aprov else current_user.id
    todos = FinLancamento.listar(EMP, lancado_por=quem, origem=None if not aprov else 'SERVICO', limite=1500)
    grupos = {k: [] for k in FASES}
    fora = []
    for l in todos:
        l['fase'] = _fase(l, hoje)
        l['como'] = _como(l)
        l['situacao_com'] = FinLancamento.situacao_comissao(l)
        (grupos[l['fase']] if l['fase'] else fora).append(l)
    for l in grupos['apr']:      # quanto a comissão dá se aprovar como foi pedida
        l['com_prev'] = float(FinLancamento.comissao_calculada(l)[1])
    soma = lambda ls: sum(float(x['valor_total'] or 0) for x in ls)
    meus_com = [l for l in todos if l.get('comissao_status') in ('LIBERADA', 'FECHADA', 'PAGA', 'AGUARDANDO')
                and (aprov is False or l['lancado_por'] == current_user.id)]
    liberadas = [l for l in meus_com if l['comissao_status'] == 'LIBERADA' and l['lancado_por'] == current_user.id]
    pedidos = FinComissaoFechamento.listar(usuario_id=None if aprov else current_user.id)
    a_pagar = [f for f in pedidos if f['status'] not in ('PAGO', 'CANCELADO')]
    acordos = [a for a in FinComissaoAcordo.listar(usuario_id=current_user.id) if a['base'] == 'SERVICO']
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
        'servicos': [{'id': s['id'], 'nome': s['nome'], 'valor': float(s['valor_padrao'] or 0), 'nota': bool(s['emite_nota'])} for s in _servicos()],
        'acordos': [{'servico_id': a['servico_id'], 'tipo': a['tipo'], 'valor': float(a['valor'])} for a in acordos],
        'comps': [{'v': c.isoformat(), 'rot': c.strftime('%m/%Y')} for c in comps],
        'hoje': hoje.isoformat(), 'eu': getattr(current_user, 'nome', ''), 'aprovador': aprov,
    }
    return render_template(
        'financeiro/lancamentos.html', aprov=aprov, admin=_admin(), hoje=hoje, grupos=grupos, fora=fora[:30],
        tot={k: soma(v) for k, v in grupos.items()}, liberadas=liberadas, meus_com=meus_com,
        tot_liberadas=sum(float(l['comissao_valor'] or 0) for l in liberadas),
        pedidos=pedidos, a_pagar=a_pagar, tot_a_pagar=sum(float(f['total'] or 0) - float(f['valor_pago'] or 0) for f in a_pagar),
        aba=request.args.get('aba') if request.args.get('aba') in FASES + ('com',) else ('apr' if (aprov or grupos['apr']) else 'cob'),
        caixa_json=json.dumps(caixa, ensure_ascii=False), STATUS=STATUS, COMISSAO=COMISSAO,
        acordos_todos=FinComissaoAcordo.listar(so_ativos=True) if _admin() else [],
        pessoas=ClienteResponsavel.usuarios_disponiveis() if _admin() else [], servicos=_servicos())


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
                                  Decimal('1'), _dec(f.get('valor')), destino, current_user.id,
                                  vencimento=_data(f.get('vencimento')) if destino == 'AVULSO' else None,
                                  observacao=(f.get('observacao') or '').strip() or None, competencia=comp,
                                  com_nota=None if destino == 'MENSAL' else (como == 'BOLETO_NOTA'),
                                  comissao_tipo=f.get('comissao_tipo') or 'PADRAO', comissao_valor=f.get('comissao_valor') or None)
        l = FinLancamento.get(lid)
        registrar('escrita.lancou_servico', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
                  depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'valor': str(l['valor_total']),
                          'como': _como(l), 'vencimento': str(l['vencimento'] or ''),
                          'comissao_pedida': (l.get('comissao_pedida_tipo') or '') + (' ' + str(l['comissao_pedida_valor']) if l.get('comissao_pedida_valor') else '')})
        flash('Solicitação enviada: %s · %s. Anderson ou Alessandra aprovam e a cobrança sai.' % (l['nome_razao_social'], _como(l)), 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    return redirect(url_for('financeiro.lancamentos', aba='apr'))


# ---------------------------------------------------------------------------
# Aprovação (admin) — AVULSO cobra na hora
# ---------------------------------------------------------------------------
def _cobrar_avulso(l, usuario_id):
    """Nota (se o serviço emite) + boleto Efí + e-mail aos contatos do Financeiro. Devolve (ok, mensagens)."""
    from models.fin_cobranca import FinBoleto, FinCobrancaConta
    from models.nfse_emissao import NfseConfig, NfseEmitida, tomador_do_cliente
    from routes.financeiro_cobranca import _pagador_do_tomador, emitir_boleto_avulso, gerar_boleto_da_nota, sugestao_boleto
    from utils import nfse_emissor
    msgs = []
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
            return False, ['A nota não foi autorizada (%s). O lançamento continua aprovado; veja a nota em Cobranças.' % (linha or {}).get('situacao', 'falha')]
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
    if not _aprovador():
        abort(403)
    l = FinLancamento.get(lid)
    if not l:
        abort(404)
    ct = request.form.get('comissao_tipo') or None
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
        ok, msgs = _cobrar_avulso(FinLancamento.get(lid), current_user.id)
        for m in msgs:
            flash(m, 'success' if ok else 'warning')
        if not ok:
            flash('Aprovado, mas a cobrança não saiu. Corrija e cobre pela tela Cobranças.', 'danger')
    else:
        flash('Aprovado: entra na cobrança mensal de %s.' % l['competencia'].strftime('%m/%Y'), 'success')
    return redirect(url_for('financeiro.lancamentos', aba='apr'))


@financeiro.route('/financeiro/lancamentos/<int:lid>/reprovar', methods=['POST'])
@permission_required(PERM)
def lancamento_reprovar(lid):
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
@financeiro.route('/financeiro/lancamentos/fechar', methods=['POST'])
@permission_required(PERM)
def comissoes_fechar():
    try:
        fid = FinComissaoFechamento.criar(current_user.id, request.form.getlist('lanc'), (request.form.get('observacao') or '').strip() or None)
        f = FinComissaoFechamento.get(fid)
        registrar('escrita.fechou_comissoes', 'financeiro', tabela='fin_comissao_fechamentos', registro_id=fid,
                  depois={'total': str(f['total']), 'itens': f['qtd_itens']})
        flash('Pedido nº %d enviado: R$ %s em %d comissão(ões). Anderson ou Alessandra pagam na sua chave Pix.' % (fid, f['total'], f['qtd_itens']), 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('financeiro.lancamentos', aba='com'))


@financeiro.route('/financeiro/lancamentos/fechamentos/<int:fid>/pagar', methods=['POST'])
@permission_required(PERM)
def fechamento_pagar(fid):
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
    return redirect(url_for('financeiro.lancamentos', aba='com'))


# ---------------------------------------------------------------------------
# Acordos (admin)
# ---------------------------------------------------------------------------
@financeiro.route('/financeiro/lancamentos/acordos/novo', methods=['POST'])
@permission_required(PERM)
def acordo_novo():
    if not _admin():
        abort(403)
    f = request.form
    try:
        aid = FinComissaoAcordo.criar(
            f.get('usuario_id', type=int), f.get('base') or 'SERVICO', f.get('tipo') or 'PCT', _dec(f.get('valor')),
            _data(f.get('vigencia_inicio')) or date.today(), servico_id=f.get('servico_id', type=int) or None,
            cliente_id=f.get('cliente_id', type=int) or None, parcelas=f.get('parcelas', type=int) or 1,
            observacao=(f.get('observacao') or '').strip() or None, criado_por=current_user.id, aprovado_por=current_user.id)
        registrar('escrita.criou_acordo_comissao', 'financeiro', tabela='fin_comissao_acordos', registro_id=aid,
                  depois={k: f.get(k) for k in ('usuario_id', 'base', 'tipo', 'valor', 'servico_id', 'cliente_id', 'parcelas', 'vigencia_inicio')})
        flash('Acordo de comissão cadastrado.', 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
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
    return redirect(url_for('financeiro.lancamentos', aba='com') + '#acordos')
