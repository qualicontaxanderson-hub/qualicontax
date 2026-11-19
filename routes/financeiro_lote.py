# -*- coding: utf-8 -*-
"""Lote mensal de honorários — prévia, emissão e acompanhamento (parte 6, etapa 3 — 06/11/2026).

Só o Anderson emite o mensal ("Os mensal é feito por mim!"): permissão ``financeiro.lote`` nasce sem
perfil. A prévia mostra, empresa por empresa, o que vai na cobrança da competência; "Emitir" roda em
segundo plano e a tela acompanha; rodar de novo só pega quem faltou.
"""
import json
import logging
from datetime import date, datetime

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


@financeiro.route('/financeiro/lote/emitir', methods=['POST'])
@permission_required(PERM)
def lote_emitir():
    comp = _competencia()
    marcados = request.form.getlist('cliente')
    so = [int(x) for x in marcados if str(x).isdigit()] if marcados else None
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
    try:
        lote_id = lote_mensal.rodar(current_app._get_current_object(), EMP, comp, current_user.id, so_clientes=so, vencimento=venc, so_decimo=sod)
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(url_for('financeiro.honorarios', competencia=comp.strftime('%Y-%m')))
    registrar('escrita.emitiu_lote_mensal', 'financeiro', tabela='fin_lotes_mensais', registro_id=lote_id,
              depois={'competencia': comp.strftime('%Y-%m'), 'so_clientes': so, 'so_decimo': sod, 'vencimento': venc.isoformat() if venc else 'dia do contrato'})
    flash('Cobrança de %s em emissão%s. Acompanhe no alto da tela; ela atualiza sozinha.'
          % (comp.strftime('%m/%Y'), (' com vencimento em ' + venc.strftime('%d/%m/%Y')) if venc else ''), 'success')
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
