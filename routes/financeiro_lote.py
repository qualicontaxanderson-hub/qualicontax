# -*- coding: utf-8 -*-
"""Lote mensal de honorários — prévia, emissão e acompanhamento (parte 6, etapa 3 — 06/11/2026).

Só o Anderson emite o mensal ("Os mensal é feito por mim!"): permissão ``financeiro.lote`` nasce sem
perfil. A prévia mostra, empresa por empresa, o que vai na cobrança da competência; "Emitir" roda em
segundo plano e a tela acompanha; rodar de novo só pega quem faltou.
"""
import json
import logging
from datetime import date, datetime
from decimal import Decimal

from flask import current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from models.fin_contrato import EMPRESA_QUALICONTAX
from models.fin_lancamento import FinLoteMensal
from routes.financeiro import financeiro
from utils import lote_mensal
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
PERM = 'financeiro.lote'
EMP = EMPRESA_QUALICONTAX


def _competencia():
    raw = (request.args.get('competencia') or request.form.get('competencia') or '').strip()
    try:
        return datetime.strptime(raw, '%Y-%m').date().replace(day=1) if raw else FinLoteMensal.competencia_aberta(EMP)
    except ValueError:
        return FinLoteMensal.competencia_aberta(EMP)


@financeiro.route('/financeiro/lote')
@permission_required(PERM)
def lote():
    # 18/11/2026: o Lote mensal mora dentro de HONORÁRIOS (etapa 4)
    if not request.args.get('antigo'):
        return redirect(url_for('financeiro.honorarios', competencia=_competencia().strftime('%Y-%m')))
    comp = _competencia()
    lote_row, linhas = lote_mensal.previa(EMP, comp)
    tot = {'empresas': len(linhas), 'valor': sum(l['total'] for l in linhas),
           'com_decimo': sum(1 for l in linhas if any(i['origem'] == 'decimo' for i in l['itens'])),
           'com_lanc': sum(1 for l in linhas if any(i['origem'] == 'lancamento' for i in l['itens'])),
           'sem_contato': sum(1 for l in linhas if not l['contatos']),
           'feitos': sum(1 for l in linhas if l['feito'] and l['feito']['status'] == 'ok'),
           'erros': sum(1 for l in linhas if l['feito'] and l['feito']['status'] == 'erro')}
    meses = []
    c = FinLoteMensal.competencia_aberta(EMP)
    for _ in range(3):
        meses.append(c)
        c = lote_mensal.mes_seguinte(c)
    return render_template('financeiro/lote.html', competencia=comp, lote=lote_row, linhas=linhas, tot=tot, meses=meses,
                           status=lote_mensal.status(lote_row['id']) if lote_row else None, hoje=date.today())


def _ids(fonte):
    return sorted({int(x) for x in fonte.getlist('cliente') if str(x).isdigit()})


def _conferencia(comp, ids, forcar=()):
    """Conferência antes de emitir (26/11/2026 — Anderson: "garantia para quem será enviado todos os boletos"): uma linha
    por empresa marcada, com o total (mensalidade + parcela do 13º em boleto próprio), quem recebe e a situação —
    'ok' | 'sem_email' | 'interno' (só e-mail da Qualicontax) | 'fora_lote' (já tem cobrança desta competência fora do
    lote) | 'forcado' (fora do lote, mas a pessoa mandou emitir mesmo assim). Quem já saiu neste lote não entra."""
    _lote, linhas = lote_mensal.previa(EMP, comp, so_clientes=ids, so_decimo=ids)
    por = {}
    for l in linhas:
        if l['feito'] and l['feito']['status'] == 'ok':
            continue
        e = por.setdefault(l['cliente_id'], {'cliente_id': l['cliente_id'], 'numero': l['numero'], 'empresa': l['empresa'], 'total': Decimal('0'),
                                             'tem13': False, 'nota': bool(l['nota']), 'vencimento': None, 'destinos': l['destinos'],
                                             'internos': l['internos'], 'bloqueio': l['bloqueio'], 'fora': None,
                                             'cadastro': url_for('clientes.detalhes', id=l['cliente_id'])})
        e['total'] += Decimal(str(l['total']))
        if (l.get('tipo') or 'MENSAL') == 'DECIMO':
            e['tem13'] = True
        else:
            e['fora'], e['vencimento'], e['nota'] = l.get('fora_lote'), l['vencimento'], bool(l['nota'])
    ordem = {'sem_email': 0, 'interno': 1, 'fora_lote': 2, 'forcado': 3, 'ok': 4}
    out = []
    for e in por.values():
        if e['bloqueio']:
            e['situacao'] = e['bloqueio']
        elif e['fora']:
            e['situacao'] = 'forcado' if e['cliente_id'] in forcar else 'fora_lote'
        else:
            e['situacao'] = 'ok'
        out.append(e)
    out.sort(key=lambda e: (ordem[e['situacao']], (e['numero'] is None), str(e['numero'] or '').zfill(6), e['empresa']))
    return out


def _json_ok(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.strftime('%d/%m/%Y')
    if isinstance(v, dict):
        return {k: _json_ok(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json_ok(x) for x in v]
    return v


@financeiro.route('/financeiro/lote/conferir', methods=['POST'])
@permission_required(PERM)
def lote_conferir():
    """A lista "quem recebe" da caixa Emitir (JSON). Vem com os links do PDF e do Excel da mesma lista."""
    from utils import relatorio_lote_destinatarios as RL
    comp = _competencia()
    ids = _ids(request.form)
    forcar = {int(x) for x in request.form.getlist('forcar') if str(x).isdigit()}
    linhas = _conferencia(comp, ids, forcar) if ids else []
    r = RL.resumo(linhas)
    q = {'competencia': comp.strftime('%Y-%m'), 'cliente': ids}
    return jsonify({'linhas': _json_ok(linhas), 'resumo': _json_ok(r),
                    'pdf': url_for('financeiro.lote_conferir_arquivo', fmt='pdf', **q),
                    'xlsx': url_for('financeiro.lote_conferir_arquivo', fmt='xlsx', **q)})


@financeiro.route('/financeiro/lote/conferir.<fmt>')
@permission_required(PERM)
def lote_conferir_arquivo(fmt):
    """A mesma conferência em PDF ou Excel, para guardar como garantia de para quem foi cada cobrança."""
    import os
    from io import BytesIO
    from zoneinfo import ZoneInfo
    from flask import abort, send_file
    from utils import relatorio_lote_destinatarios as RL
    if fmt not in ('pdf', 'xlsx'):
        abort(404)
    comp = _competencia()
    ids = _ids(request.args)
    forcar = {int(x) for x in request.args.getlist('forcar') if str(x).isdigit()}
    linhas = _conferencia(comp, ids, forcar) if ids else []
    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    logo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'images', 'logo.png')
    ctx = {'competencia': comp, 'linhas': linhas, 'gerado_em': agora.strftime('%d/%m/%Y %H:%M'),
           'gerado_por': getattr(current_user, 'nome', '') or '', 'logo_path': logo if os.path.exists(logo) else None}
    dados = RL.gerar_pdf(ctx) if fmt == 'pdf' else RL.gerar_xlsx(ctx)
    registrar('leitura.exportou_arquivo', 'financeiro', tabela='fin_lotes_mensais',
              depois={'relatorio': 'Quem recebe a cobrança de %s' % comp.strftime('%m/%Y'), 'formato': fmt, 'empresas': len(linhas)})
    nome = 'quem-recebe-%s-%s.%s' % (comp.strftime('%Y-%m'), agora.strftime('%Y-%m-%d-%H%M'), fmt)
    mime = 'application/pdf' if fmt == 'pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return send_file(BytesIO(dados), as_attachment=(fmt == 'xlsx' or request.args.get('baixar') == '1'), download_name=nome, mimetype=mime)


@financeiro.route('/financeiro/lote/emitir', methods=['POST'])
@permission_required(PERM)
def lote_emitir():
    comp = _competencia()
    marcados = request.form.getlist('cliente')
    so = [int(x) for x in marcados if str(x).isdigit()] if marcados else None
    # 26/11/2026 — "emitir mesmo assim" da conferência, para quem já tem cobrança desta competência fora do lote
    forcar = [int(x) for x in request.form.getlist('forcar') if str(x).isdigit()]
    # 19/11/2026: marcar a empresa emite a mensalidade E a parcela do 13º em boleto próprio (Anderson: "um painel só e
    # quando emitir emite os dois"); cada uma só sai se ainda não saiu neste lote
    sod = so
    # 19/11/2026 — vencimento escolhido na janela ("outra data"); vazio = dia do contrato
    venc, raw = None, (request.form.get('vencimento') or '').strip()
    if raw:
        try:
            venc = datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            flash('Data de vencimento inválida.', 'danger')
            return redirect(url_for('financeiro.honorarios', competencia=comp.strftime('%Y-%m')))
        if venc < date.today():
            flash('O vencimento escolhido (%s) já passou. Escolha hoje ou uma data à frente.' % venc.strftime('%d/%m/%Y'), 'danger')
            return redirect(url_for('financeiro.honorarios', competencia=comp.strftime('%Y-%m')))
    # 19/11/2026 — "Data por empresa": venc_<cliente_id> para cada marcada (Anderson: "escolher a mesma data nos dois e
    # escolher diferentes" sem fazer dois procedimentos)
    vencs = {}
    for k, v in request.form.items():
        if k.startswith('venc_') and k[5:].isdigit() and (v or '').strip():
            try:
                d = datetime.strptime(v.strip(), '%Y-%m-%d').date()
            except ValueError:
                flash('Data de vencimento inválida numa das empresas.', 'danger')
                return redirect(url_for('financeiro.honorarios', competencia=comp.strftime('%Y-%m')))
            if d < date.today():
                flash('O vencimento escolhido para uma das empresas (%s) já passou.' % d.strftime('%d/%m/%Y'), 'danger')
                return redirect(url_for('financeiro.honorarios', competencia=comp.strftime('%Y-%m')))
            vencs[int(k[5:])] = d
    try:
        lote_id = lote_mensal.rodar(current_app._get_current_object(), EMP, comp, current_user.id, so_clientes=so, vencimento=venc, so_decimo=sod,
                                    vencimentos=vencs or None, forcar=forcar)
    except lote_mensal.JaEmitindo as e:                          # 26/11/2026: trava no banco — segundo clique não emite de novo
        flash(str(e), 'info')
        return redirect(url_for('financeiro.honorarios', competencia=comp.strftime('%Y-%m')))
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('financeiro.honorarios', competencia=comp.strftime('%Y-%m')))
    registrar('escrita.emitiu_lote_mensal', 'financeiro', tabela='fin_lotes_mensais', registro_id=lote_id,
              depois={'competencia': comp.strftime('%Y-%m'), 'so_clientes': so, 'so_decimo': sod, 'vencimento': venc.isoformat() if venc else ('por empresa' if vencs else 'dia do contrato'),
                      'vencimentos': {str(k): v.isoformat() for k, v in vencs.items()} or None, 'forcar': forcar or None})
    flash('Cobrança de %s em emissão%s. Acompanhe no alto da tela; ela atualiza sozinha.'
          % (comp.strftime('%m/%Y'), (' com vencimento em ' + venc.strftime('%d/%m/%Y')) if venc else (', cada empresa na data escolhida' if vencs else '')), 'success')
    return redirect(url_for('financeiro.honorarios', competencia=comp.strftime('%Y-%m')))


@financeiro.route('/financeiro/lote/<int:lote_id>/status')
@permission_required(PERM)
def lote_status(lote_id):
    s = lote_mensal.status(lote_id)
    return jsonify({k: (float(v) if hasattr(v, 'quantize') else v) for k, v in s.items()})


@financeiro.route('/financeiro/lote/<int:lote_id>/reabrir', methods=['POST'])
@permission_required(PERM)
def lote_reabrir(lote_id):
    """Volta o lote para ABERTO (ex.: emitiu cedo demais e ainda vai entrar lançamento no mês)."""
    execute_query("UPDATE fin_lotes_mensais SET status = 'ABERTO' WHERE id = %s", (lote_id,), fetch=False)
    registrar('escrita.reabriu_lote_mensal', 'financeiro', tabela='fin_lotes_mensais', registro_id=lote_id, depois={'status': 'ABERTO'})
    flash('Lote reaberto: lançamentos novos voltam a cair nesta competência.', 'info')
    return redirect(url_for('financeiro.honorarios'))
