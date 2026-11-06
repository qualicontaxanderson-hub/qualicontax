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


def _meu_ou_admin(l):
    if not l or (not _admin() and l['lancado_por'] != current_user.id):
        abort(404)
    return l


def _servicos():
    return execute_query("SELECT id, nome, valor_padrao, emite_nota FROM nfse_servicos WHERE empresa_id = %s AND ativo = 1 ORDER BY ordem, nome",
                         (EMP,), fetch=True) or []


# ---------------------------------------------------------------------------
# Tela
# ---------------------------------------------------------------------------
@financeiro.route('/financeiro/lancamentos')
@permission_required(PERM)
def lancamentos():
    admin = _admin()
    aba = request.args.get('aba') or 'lancamentos'
    if aba in ('aprovar', 'acordos') and not admin:
        aba = 'lancamentos'
    busca = (request.args.get('busca') or '').strip() or None
    status = request.args.get('status') or None
    quem = None if admin else current_user.id
    FinLancamento.liberar_comissoes_pagas()
    ctx = dict(aba=aba, admin=admin, busca=busca, status=status, STATUS=STATUS, DESTINOS=DESTINOS, COMISSAO=COMISSAO,
               servicos=_servicos(), hoje=date.today(), competencia_aberta=FinLoteMensal.competencia_aberta(EMP),
               n_aprovar=len(FinLancamento.listar(EMP, status='LANCADO')) if admin else 0,
               situacao=FinLancamento.situacao_comissao)
    if aba == 'aprovar':
        ctx['lista'] = FinLancamento.listar(EMP, status='LANCADO', busca=busca, origem='SERVICO')
    elif aba == 'comissoes':
        uid = request.args.get('u', type=int) if admin else current_user.id
        ctx['uid'] = uid
        ctx['pessoas'] = ClienteResponsavel.usuarios_disponiveis() if admin else []
        ctx['resumo'] = FinLancamento.resumo_pessoa(EMP, uid) if uid else {}
        ctx['lista'] = [l for l in FinLancamento.listar(EMP, lancado_por=uid, status=('APROVADO', 'FATURADO'))
                        if l.get('comissao_status') and l['comissao_status'] != 'SEM'] if uid else []
    elif aba == 'fechamentos':
        ctx['fechamentos'] = FinComissaoFechamento.listar(usuario_id=quem)
        fid = request.args.get('f', type=int)
        if fid:
            f = FinComissaoFechamento.get(fid)
            if f and (admin or f['usuario_id'] == current_user.id):
                ctx['fechamento'] = f
                ctx['fech_itens'] = FinComissaoFechamento.itens(fid)
                ctx['fech_pagamentos'] = FinComissaoFechamento.pagamentos(fid)
    elif aba == 'acordos':
        ctx['acordos'] = FinComissaoAcordo.listar(so_ativos=False)
        ctx['pessoas'] = ClienteResponsavel.usuarios_disponiveis()
    else:
        ctx['lista'] = FinLancamento.listar(EMP, lancado_por=quem, status=status, busca=busca, origem='SERVICO')
        ctx['resumo'] = FinLancamento.resumo_pessoa(EMP, current_user.id) if not admin else None
    return render_template('financeiro/lancamentos.html', **ctx)


@financeiro.route('/financeiro/lancamentos/novo', methods=['POST'])
@permission_required(PERM)
def lancamento_novo():
    f = request.form
    try:
        cliente_id = f.get('cliente_id', type=int)
        servico_id = f.get('servico_id', type=int)
        if not cliente_id or not servico_id:
            raise ValueError('Escolha a empresa e o serviço.')
        s = execute_query('SELECT nome FROM nfse_servicos WHERE id = %s AND empresa_id = %s', (servico_id, EMP), fetch=True, fetch_one=True)
        if not s:
            raise ValueError('Serviço inválido.')
        lid = FinLancamento.criar(EMP, cliente_id, servico_id, (f.get('descricao') or s['nome']).strip() or s['nome'],
                                  _dec(f.get('quantidade') or '1'), _dec(f.get('valor_unitario')), f.get('destino'),
                                  current_user.id, vencimento=_data(f.get('vencimento')),
                                  observacao=(f.get('observacao') or '').strip() or None)
        l = FinLancamento.get(lid)
        registrar('escrita.lancou_servico', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
                  depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'valor': str(l['valor_total']),
                          'destino': l['destino'], 'competencia': str(l['competencia']), 'vencimento': str(l['vencimento'] or '')})
        if l['destino'] == 'MENSAL':
            flash('Lançado para a cobrança mensal de %s. Aguarda aprovação.' % l['competencia'].strftime('%m/%Y'), 'success')
        else:
            flash('Lançado como cobrança avulsa. Aguarda aprovação para o boleto sair.', 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    return redirect(url_for('financeiro.lancamentos'))


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
              'emite_nota': bool(l.get('emite_nota'))}]
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
    if not _admin():
        abort(403)
    l = FinLancamento.get(lid)
    if not l:
        abort(404)
    try:
        com = FinLancamento.aprovar(lid, current_user.id)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('financeiro.lancamentos', aba='aprovar'))
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
    return redirect(url_for('financeiro.lancamentos', aba='aprovar'))


@financeiro.route('/financeiro/lancamentos/<int:lid>/reprovar', methods=['POST'])
@permission_required(PERM)
def lancamento_reprovar(lid):
    if not _admin():
        abort(403)
    l = FinLancamento.get(lid)
    try:
        FinLancamento.reprovar(lid, current_user.id, request.form.get('motivo'))
        registrar('escrita.reprovou_lancamento', 'financeiro', tabela='fin_lancamentos', registro_id=lid,
                  depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'motivo': request.form.get('motivo')})
        flash('Lançamento reprovado.', 'info')
    except ValueError as e:
        flash(str(e), 'danger')
    return redirect(url_for('financeiro.lancamentos', aba='aprovar'))


@financeiro.route('/financeiro/lancamentos/<int:lid>/cancelar', methods=['POST'])
@permission_required(PERM)
def lancamento_cancelar(lid):
    l = _meu_ou_admin(FinLancamento.get(lid))
    try:
        if not _admin() and l['status'] != 'LANCADO':
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
        flash('Fechamento nº %d gerado: R$ %s em %d comissão(ões). Enviado para pagamento.' % (fid, f['total'], f['qtd_itens']), 'success')
        return redirect(url_for('financeiro.lancamentos', aba='fechamentos', f=fid))
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('financeiro.lancamentos', aba='comissoes'))


@financeiro.route('/financeiro/lancamentos/fechamentos/<int:fid>/pagar', methods=['POST'])
@permission_required(PERM)
def fechamento_pagar(fid):
    if not _admin():
        abort(403)
    try:
        st = FinComissaoFechamento.registrar_pagamento(fid, _data(request.form.get('data')) or date.today(),
                                                      _dec(request.form.get('valor')), current_user.id)
        registrar('escrita.pagou_comissao', 'financeiro', tabela='fin_comissao_fechamentos', registro_id=fid,
                  depois={'data': request.form.get('data'), 'valor': request.form.get('valor'), 'status': st})
        flash('Pagamento registrado. Fechamento %s.' % ('quitado' if st == 'PAGO' else 'pago em parte'), 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    return redirect(url_for('financeiro.lancamentos', aba='fechamentos', f=fid))


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
    return redirect(url_for('financeiro.lancamentos', aba='acordos'))


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
    return redirect(url_for('financeiro.lancamentos', aba='acordos'))
