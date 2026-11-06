# -*- coding: utf-8 -*-
"""Honorários — contratos mensais das empresas (06/11/2026, parte 6 da migração).

Mora no blueprint ``financeiro`` (mesma navegação). Tela única: todas as empresas com honorários
(valor, dia de vencimento, 13º, próximo reajuste, contatos do Financeiro que recebem a cobrança) e a
lista das que ainda não têm. Permissão ``financeiro.honorarios`` nasce sem perfil: só o admin entra
até ele marcar em Perfis. O fluxo "funcionário autorizado cadastra → Anderson aprova" (etapa 5)
vai usar a situação PENDENTE que o modelo já conhece.
"""
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from models.fin_contrato import (DECIMO_TIPOS, REAJUSTE_TIPOS, SERVICO_HONORARIOS, FinContrato,
                                 primeiro_reajuste)
from routes.financeiro import financeiro
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
PERM = 'financeiro.honorarios'


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


def _servicos():
    return execute_query("SELECT id, nome FROM nfse_servicos WHERE ativo = 1 ORDER BY ordem, nome", fetch=True) or []


def _foto(c):
    """O que vai para o log: só o que importa, em texto."""
    if not c:
        return None
    return {'valor': str(c.get('valor')), 'dia_vencimento': c.get('dia_vencimento'),
            'inicio': str(c.get('inicio') or ''), 'fim': str(c.get('fim') or ''), 'plano': c.get('plano'),
            'decimo_tipo': c.get('decimo_tipo'), 'decimo_parcelas': c.get('decimo_parcelas'),
            'reajuste_tipo': c.get('reajuste_tipo'), 'proximo_reajuste': str(c.get('proximo_reajuste') or ''),
            'reajuste_obs': c.get('reajuste_obs'), 'situacao': c.get('situacao'),
            'itens': ['%s = %s' % (i['descricao'], i['valor']) for i in FinContrato.itens(c['id'])] if c.get('id') else None}


@financeiro.route('/financeiro/honorarios')
@permission_required(PERM)
def honorarios():
    aba = request.args.get('aba', 'com')
    busca = (request.args.get('busca') or '').strip() or None
    contratos = FinContrato.listar(busca=busca)
    sem = FinContrato.clientes_sem_contrato()
    if busca:
        b = busca.lower()
        sem = [c for c in sem if b in (c['nome_razao_social'] or '').lower() or b == str(c['numero_cliente'])]
    return render_template('financeiro/honorarios.html', aba=aba, busca=busca, contratos=contratos, sem_contrato=sem,
                           resumo=FinContrato.resumo(), servicos=_servicos(), DECIMO_TIPOS=DECIMO_TIPOS,
                           REAJUSTE_TIPOS=REAJUSTE_TIPOS, hoje=date.today(), SERVICO_HONORARIOS=SERVICO_HONORARIOS)


@financeiro.route('/financeiro/honorarios/<int:cid>/json')
@permission_required(PERM)
def honorarios_json(cid):
    c = FinContrato.get(cid)
    if not c:
        return jsonify({'erro': 'não encontrado'}), 404
    out = {k: (v.isoformat() if hasattr(v, 'isoformat') else (str(v) if isinstance(v, Decimal) else v)) for k, v in c.items()}
    out['itens'] = [{'descricao': i['descricao'], 'valor': str(i['valor']), 'servico_id': i['servico_id']} for i in FinContrato.itens(cid)]
    return jsonify(out)


@financeiro.route('/financeiro/honorarios/salvar', methods=['POST'])
@permission_required(PERM)
def honorarios_salvar():
    f = request.form
    cid = f.get('contrato_id', type=int)
    volta = redirect(url_for('financeiro.honorarios', aba=f.get('aba') or 'com'))
    try:
        itens = []
        for d, v, s in zip(f.getlist('item_desc'), f.getlist('item_valor'), f.getlist('item_servico')):
            d = (d or '').strip()
            if not d and not (v or '').strip():
                continue
            itens.append({'descricao': d, 'valor': _dec(v), 'servico_id': int(s) if (s or '').isdigit() else None})
        if not itens:
            raise ValueError('Informe ao menos uma discriminação com valor.')
        valor = sum(i['valor'] for i in itens)
        dia = f.get('dia_vencimento', type=int)
        if not dia or not 1 <= dia <= 31:
            raise ValueError('Dia de vencimento entre 1 e 31.')
        inicio = _data(f.get('inicio'))
        if not inicio:
            raise ValueError('Informe o início do contrato.')
        decimo_tipo = f.get('decimo_tipo') or 'NENHUM'
        parcelas = None
        if decimo_tipo == 'PERSONALIZADO':
            parcelas = [{'mes': int(m), 'pct': float(_dec(p))} for m, p in zip(f.getlist('dec_mes'), f.getlist('dec_pct'))
                        if (m or '').isdigit() and (p or '').strip()]
        reajuste_tipo = f.get('reajuste_tipo') or 'SALARIO_MINIMO'
        prox = _data(f.get('proximo_reajuste'))
        if reajuste_tipo == 'SALARIO_MINIMO' and not prox:
            prox = primeiro_reajuste(inicio)
        dados = dict(valor=valor, dia_vencimento=dia, inicio=inicio, fim=_data(f.get('fim')),
                     plano=(f.get('plano') or '').strip() or None, decimo_tipo=decimo_tipo, decimo_parcelas=parcelas,
                     reajuste_tipo=reajuste_tipo, proximo_reajuste=prox,
                     reajuste_obs=(f.get('reajuste_obs') or '').strip() or None,
                     gerar_nfse=1 if f.get('gerar_nfse') else 0, gerar_boleto=1 if f.get('gerar_boleto') else 0)
        if cid:
            antes = FinContrato.get(cid)
            if not antes:
                raise ValueError('Contrato não encontrado.')
            FinContrato.atualizar(cid, dados, itens)
            depois = FinContrato.get(cid)
            registrar('escrita.alterou_contrato_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
                      antes=_foto(antes), depois=_foto(depois))
            flash('Contrato de %s salvo.' % depois['nome_razao_social'], 'success')
        else:
            cliente_id = f.get('cliente_id', type=int)
            if not cliente_id:
                raise ValueError('Escolha a empresa.')
            cid = FinContrato.criar(cliente_id=cliente_id, itens=itens, criado_por=current_user.id,
                                    aprovado_por=current_user.id, situacao='ATIVO', **dados)
            novo = FinContrato.get(cid)
            registrar('escrita.criou_contrato_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
                      depois=dict(_foto(novo), cliente_id=cliente_id, empresa=novo['nome_razao_social']))
            flash('Contrato de honorários de %s criado.' % novo['nome_razao_social'], 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    return volta


@financeiro.route('/financeiro/honorarios/<int:cid>/encerrar', methods=['POST'])
@permission_required(PERM)
def honorarios_encerrar(cid):
    c = FinContrato.get(cid)
    if not c:
        flash('Contrato não encontrado.', 'danger')
        return redirect(url_for('financeiro.honorarios'))
    fim = _data(request.form.get('fim')) or date.today()
    FinContrato.atualizar(cid, {'situacao': 'ENCERRADO', 'fim': fim})
    registrar('escrita.encerrou_contrato_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
              antes=_foto(c), depois={'situacao': 'ENCERRADO', 'fim': str(fim), 'empresa': c['nome_razao_social']})
    flash('Contrato de %s encerrado em %s. O 13º sai proporcional aos meses de casa.' % (c['nome_razao_social'], fim.strftime('%d/%m/%Y')), 'success')
    return redirect(url_for('financeiro.honorarios'))
