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
from flask_login import current_user, login_required

from models.fin_contrato import (DECIMO_INICIOS, DECIMO_TIPOS, REAJUSTE_TIPOS, SERVICO_HONORARIOS, FinContrato,
                                 decimo_desde_de, modo_decimo_inicio, primeiro_reajuste)
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


def _grupos():
    return execute_query("SELECT id, nome FROM grupos_clientes WHERE situacao = 'ATIVO' ORDER BY nome", fetch=True) or []


def _gravar_grupo(cliente_id, f):
    """O campo Grupo do contrato É o grupo do CADASTRO: trocar aqui troca lá (Anderson, 06/11/2026: "com a opção
    de alterar aí já altera até no cadastro em tudo"). Em branco não tira de grupo nenhum."""
    if 'grupo_id' not in f:
        return
    gid = f.get('grupo_id', type=int)
    if not gid:
        return
    from routes.clientes import _salvar_grupo
    mudou = _salvar_grupo(cliente_id, gid)
    if mudou:
        registrar('escrita.alterou_grupo_cliente', 'clientes', tabela='cliente_grupo_relacao', registro_id=cliente_id,
                  antes={'grupos': mudou[0]}, depois={'grupo': mudou[1], 'origem': 'contrato de honorários'})


def _foto(c):
    """O que vai para o log: só o que importa, em texto."""
    if not c:
        return None
    return {'valor': str(c.get('valor')), 'dia_vencimento': c.get('dia_vencimento'),
            'inicio': str(c.get('inicio') or ''), 'fim': str(c.get('fim') or ''), 'plano': c.get('plano'),
            'decimo_tipo': c.get('decimo_tipo'), 'decimo_parcelas': c.get('decimo_parcelas'), 'decimo_desde': str(c.get('decimo_desde') or ''),
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
    from models.cliente_responsavel import ClienteResponsavel
    pendentes = [c for c in contratos if c['situacao'] == 'PENDENTE']
    contratos = [c for c in contratos if c['situacao'] not in ('REPROVADO', 'PENDENTE')]
    convites = execute_query(
        """SELECT v.*, cl.nome_razao_social, u.nome AS usuario_nome FROM fin_honorarios_convites v
             JOIN clientes cl ON cl.id = v.cliente_id LEFT JOIN usuarios u ON u.id = v.usuario_id
            WHERE v.usado_em IS NULL AND v.expira_em > NOW() ORDER BY v.id DESC""", fetch=True) or []
    return render_template('financeiro/honorarios.html', modo='admin', aba=aba, busca=busca, contratos=contratos, sem_contrato=sem,
                           pendentes=pendentes, convites=convites, pessoas=ClienteResponsavel.usuarios_disponiveis(),
                           resumo=FinContrato.resumo(), servicos=_servicos(), grupos=_grupos(), DECIMO_TIPOS=DECIMO_TIPOS, DECIMO_INICIOS=DECIMO_INICIOS,
                           REAJUSTE_TIPOS=REAJUSTE_TIPOS, hoje=date.today(), SERVICO_HONORARIOS=SERVICO_HONORARIOS)


@financeiro.route('/financeiro/honorarios/<int:cid>/json')
@permission_required(PERM)
def honorarios_json(cid):
    c = FinContrato.get(cid)
    if not c:
        return jsonify({'erro': 'não encontrado'}), 404
    out = {k: (v.isoformat() if hasattr(v, 'isoformat') else (str(v) if isinstance(v, Decimal) else v)) for k, v in c.items()}
    out['itens'] = [{'descricao': i['descricao'], 'valor': str(i['valor']), 'servico_id': i['servico_id']} for i in FinContrato.itens(cid)]
    out['decimo_inicio'] = modo_decimo_inicio(c.get('decimo_desde'), c.get('inicio'))
    from models.cliente import Cliente
    g = Cliente.get_grupos(c['cliente_id'])
    out['grupo_id'] = g[0]['id'] if g else None
    return jsonify(out)


@financeiro.route('/financeiro/honorarios/sugestoes/<int:cliente_id>')
@login_required
def honorarios_sugestoes(cliente_id):
    """Valores da rede para o modal (grupo do cadastro ou, sem grupo, o plano do Conexa). Quem cadastra por
    link só tem login: o convite válido daquela empresa é o que libera."""
    pode = current_user.is_admin() or current_user.has_permission(PERM) or current_user.has_permission(PERM_CAD)
    if not pode:
        pode = bool(execute_query(
            """SELECT 1 FROM fin_honorarios_convites WHERE cliente_id = %s AND usado_em IS NULL AND expira_em > NOW()
                  AND (usuario_id IS NULL OR usuario_id = %s) LIMIT 1""", (cliente_id, current_user.id), fetch=True))
    if not pode:
        return jsonify({'erro': 'sem acesso'}), 403
    return jsonify(FinContrato.sugestoes(cliente_id))


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
                     decimo_tipo=decimo_tipo, decimo_parcelas=parcelas,
                     decimo_desde=decimo_desde_de(f.get('decimo_inicio') or 'PROPORCIONAL', inicio, _data(f.get('decimo_desde'))),
                     reajuste_tipo=reajuste_tipo, proximo_reajuste=prox,
                     reajuste_obs=(f.get('reajuste_obs') or '').strip() or None,
                     gerar_nfse=1 if f.get('gerar_nfse') else 0, gerar_boleto=1 if f.get('gerar_boleto') else 0)
        if cid:
            antes = FinContrato.get(cid)
            if not antes:
                raise ValueError('Contrato não encontrado.')
            FinContrato.atualizar(cid, dados, itens)
            _gravar_grupo(antes['cliente_id'], f)
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
            _gravar_grupo(cliente_id, f)
            novo = FinContrato.get(cid)
            registrar('escrita.criou_contrato_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
                      depois=dict(_foto(novo), cliente_id=cliente_id, empresa=novo['nome_razao_social']))
            flash('Contrato de honorários de %s criado.' % novo['nome_razao_social'], 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    return volta


# ---------------- reajuste de janeiro (06/11/2026) ----------------
def _proximo_janeiro(hoje=None):
    """Janeiro corrente enquanto estamos em janeiro; depois disso, o próximo."""
    hoje = hoje or date.today()
    return date(hoje.year, 1, 1) if hoje.month == 1 else date(hoje.year + 1, 1, 1)


@financeiro.route('/financeiro/honorarios/reajuste')
@permission_required(PERM)
def honorarios_reajuste():
    """O Anderson digita o percentual do salário mínimo e aplica a todos do padrão de uma vez (decisão de 05/11)."""
    ate = _data((request.args.get('ate') or '') + ('-01' if len(request.args.get('ate') or '') == 7 else '')) or _proximo_janeiro()
    lista = FinContrato.a_reajustar(ate)
    padrao = [c for c in lista if c['reajuste_tipo'] == 'SALARIO_MINIMO']
    proprios = [c for c in lista if c['reajuste_tipo'] != 'SALARIO_MINIMO']
    lote = 'SM-%s' % ate.strftime('%Y-%m')
    return render_template('financeiro/honorarios_reajuste.html', ate=ate, padrao=padrao, proprios=proprios, lote=lote,
                           aplicados=FinContrato.reajustes_aplicados(lote), REAJUSTE_TIPOS=REAJUSTE_TIPOS, hoje=date.today())


@financeiro.route('/financeiro/honorarios/reajuste/aplicar', methods=['POST'])
@permission_required(PERM)
def honorarios_reajuste_aplicar():
    f = request.form
    vig = f.get('vigencia') or ''
    vigencia = _data(vig + '-01') if len(vig) == 7 else _data(vig)
    volta = redirect(url_for('financeiro.honorarios_reajuste', ate=vigencia.strftime('%Y-%m') if vigencia else None))
    try:
        pct = _dec(f.get('percentual'))
        if not vigencia:
            raise ValueError('Informe a partir de quando vale.')
        if not (Decimal('0') < pct <= Decimal('100')):
            raise ValueError('Percentual entre 0 e 100.')
        ids = [int(i) for i in f.getlist('ids') if (i or '').isdigit()]
        if not ids:
            raise ValueError('Marque ao menos um contrato.')
        lote = 'SM-%s' % vigencia.strftime('%Y-%m')
        n, antes_t, depois_t = 0, Decimal('0'), Decimal('0')
        for cid in ids:
            r = FinContrato.aplicar_reajuste(cid, pct, vigencia, current_user.id, lote)
            if not r:
                continue
            antes, depois = r
            n += 1; antes_t += _dec(antes['valor']); depois_t += _dec(depois['valor'])
            registrar('escrita.reajustou_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
                      antes={'valor': str(antes['valor']), 'proximo_reajuste': str(antes.get('proximo_reajuste') or '')},
                      depois={'valor': str(depois['valor']), 'proximo_reajuste': str(depois.get('proximo_reajuste') or ''),
                              'percentual': str(pct), 'vigencia': str(vigencia), 'empresa': depois['nome_razao_social']})
        flash('%d contratos reajustados em %s%% a partir de %s: de R$ %s para R$ %s por mês.'
              % (n, str(pct).rstrip('0').rstrip('.').replace('.', ','), vigencia.strftime('%m/%Y'),
                 '{:,.2f}'.format(antes_t).replace(',', 'X').replace('.', ',').replace('X', '.'),
                 '{:,.2f}'.format(depois_t).replace(',', 'X').replace('.', ',').replace('X', '.')), 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Percentual inválido.', 'danger')
    return volta


@financeiro.route('/financeiro/honorarios/reajuste/<int:rid>/desfazer', methods=['POST'])
@permission_required(PERM)
def honorarios_reajuste_desfazer(rid):
    r = FinContrato.desfazer_reajuste(rid, current_user.id)
    if not r:
        flash('Este reajuste já foi desfeito ou não existe.', 'warning')
        return redirect(url_for('financeiro.honorarios_reajuste'))
    c = FinContrato.get(r['contrato_id'])
    registrar('escrita.desfez_reajuste_honorarios', 'financeiro', tabela='fin_contratos', registro_id=r['contrato_id'],
              antes={'valor': str(r['valor_depois'])}, depois={'valor': str(r['valor_antes']), 'lote': r['lote'], 'empresa': c['nome_razao_social']})
    flash('Reajuste de %s desfeito: voltou a R$ %s.' % (c['nome_razao_social'], '{:,.2f}'.format(r['valor_antes']).replace(',', 'X').replace('.', ',').replace('X', '.')), 'info')
    return redirect(url_for('financeiro.honorarios_reajuste', ate=r['vigencia'].strftime('%Y-%m')))


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


# ---------------------------------------------------------------------------
# Etapa 5 (06/11/2026): funcionário autorizado cadastra (PENDENTE), Anderson aprova; convite por link
# ---------------------------------------------------------------------------
PERM_CAD = 'financeiro.honorarios_cadastrar'


def _ler_form(f):
    """(itens, dados) do formulário do contrato — o mesmo para o admin, o autorizado e o convite."""
    itens = []
    for d, v, s in zip(f.getlist('item_desc'), f.getlist('item_valor'), f.getlist('item_servico')):
        d = (d or '').strip()
        if not d and not (v or '').strip():
            continue
        itens.append({'descricao': d, 'valor': _dec(v), 'servico_id': int(s) if (s or '').isdigit() else None})
    if not itens:
        raise ValueError('Informe ao menos uma discriminação com valor.')
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
    dados = dict(valor=sum(i['valor'] for i in itens), dia_vencimento=dia, inicio=inicio, fim=_data(f.get('fim')),
                 decimo_tipo=decimo_tipo, decimo_parcelas=parcelas,
                 decimo_desde=decimo_desde_de(f.get('decimo_inicio') or 'PROPORCIONAL', inicio, _data(f.get('decimo_desde'))), reajuste_tipo=reajuste_tipo, proximo_reajuste=prox,
                 reajuste_obs=(f.get('reajuste_obs') or '').strip() or None,
                 gerar_nfse=1 if f.get('gerar_nfse') else 0, gerar_boleto=1 if f.get('gerar_boleto') else 0,
                 observacao=(f.get('observacao') or '').strip() or None)
    return itens, dados


def _criar_pendente(cliente_id, f):
    itens, dados = _ler_form(f)
    cid = FinContrato.criar(cliente_id=cliente_id, itens=itens, criado_por=current_user.id, aprovado_por=None,
                            situacao='PENDENTE', **dados)
    _gravar_grupo(cliente_id, f)
    novo = FinContrato.get(cid)
    registrar('escrita.cadastrou_honorarios_pendente', 'financeiro', tabela='fin_contratos', registro_id=cid,
              depois=dict(_foto(novo), cliente_id=cliente_id, empresa=novo['nome_razao_social']))
    return novo


@financeiro.route('/financeiro/honorarios/cadastrar')
@permission_required(PERM_CAD)
def honorarios_cadastrar():
    """A tela de quem cadastra: as empresas que ELE cadastrou e ainda não têm honorários, e o que já
    enviou (só a situação, sem valores — 'para eles essa tela não aparecerá mais com os valores')."""
    minhas = [c for c in FinContrato.clientes_sem_contrato() if c.get('criado_por') == current_user.id]
    enviados = FinContrato.listar(criado_por=current_user.id)
    convites = execute_query(
        """SELECT v.token, v.expira_em, cl.nome_razao_social, cl.numero_cliente FROM fin_honorarios_convites v
             JOIN clientes cl ON cl.id = v.cliente_id
            WHERE v.usado_em IS NULL AND v.expira_em > NOW() AND (v.usuario_id = %s OR v.usuario_id IS NULL)
            ORDER BY v.id DESC""", (current_user.id,), fetch=True) or []
    aba = request.args.get('aba') or 'sem'
    return render_template('financeiro/honorarios.html', modo='cadastrar', aba=aba, busca=None, contratos=enviados,
                           sem_contrato=minhas, convites_meus=convites, resumo={}, servicos=_servicos(), grupos=_grupos(),
                           DECIMO_TIPOS=DECIMO_TIPOS, DECIMO_INICIOS=DECIMO_INICIOS, REAJUSTE_TIPOS=REAJUSTE_TIPOS, hoje=date.today(),
                           SERVICO_HONORARIOS=SERVICO_HONORARIOS, acao_form=url_for('financeiro.honorarios_cadastrar_salvar'))


@financeiro.route('/financeiro/honorarios/cadastrar/salvar', methods=['POST'])
@permission_required(PERM_CAD)
def honorarios_cadastrar_salvar():
    cliente_id = request.form.get('cliente_id', type=int)
    try:
        ok = any(c['id'] == cliente_id and c.get('criado_por') == current_user.id for c in FinContrato.clientes_sem_contrato())
        if not ok:
            raise ValueError('Você só cadastra honorários das empresas que você mesmo cadastrou e que ainda não têm contrato.')
        novo = _criar_pendente(cliente_id, request.form)
        flash('Honorários de %s enviados para aprovação.' % novo['nome_razao_social'], 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    return redirect(url_for('financeiro.honorarios_cadastrar'))


@financeiro.route('/financeiro/honorarios/<int:cid>/aprovar', methods=['POST'])
@permission_required(PERM)
def honorarios_aprovar(cid):
    c = FinContrato.get(cid)
    if not c or c['situacao'] != 'PENDENTE':
        flash('Este contrato não está aguardando aprovação.', 'danger')
        return redirect(url_for('financeiro.honorarios', aba='pendentes'))
    FinContrato.atualizar(cid, {'situacao': 'ATIVO', 'aprovado_por': current_user.id})
    registrar('escrita.aprovou_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
              antes={'situacao': 'PENDENTE'}, depois=dict(_foto(FinContrato.get(cid)), empresa=c['nome_razao_social']))
    flash('Honorários de %s aprovados: entram no próximo lote.' % c['nome_razao_social'], 'success')
    return redirect(url_for('financeiro.honorarios', aba='pendentes'))


@financeiro.route('/financeiro/honorarios/<int:cid>/reprovar', methods=['POST'])
@permission_required(PERM)
def honorarios_reprovar(cid):
    c = FinContrato.get(cid)
    if not c or c['situacao'] != 'PENDENTE':
        flash('Este contrato não está aguardando aprovação.', 'danger')
        return redirect(url_for('financeiro.honorarios', aba='pendentes'))
    motivo = (request.form.get('motivo') or '').strip()
    FinContrato.atualizar(cid, {'situacao': 'REPROVADO', 'observacao': ('Reprovado: ' + motivo)[:255] if motivo else 'Reprovado'})
    registrar('escrita.reprovou_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
              antes={'situacao': 'PENDENTE'}, depois={'situacao': 'REPROVADO', 'motivo': motivo, 'empresa': c['nome_razao_social']})
    flash('Honorários de %s reprovados. A empresa volta para "Sem honorários".' % c['nome_razao_social'], 'info')
    return redirect(url_for('financeiro.honorarios', aba='pendentes'))


@financeiro.route('/financeiro/honorarios/convite', methods=['POST'])
@permission_required(PERM)
def honorarios_convite_novo():
    import secrets
    cliente_id = request.form.get('cliente_id', type=int)
    usuario_id = request.form.get('usuario_id', type=int) or None
    dias = request.form.get('dias', type=int) or 7
    cl = execute_query('SELECT id, nome_razao_social FROM clientes WHERE id = %s', (cliente_id,), fetch=True, fetch_one=True)
    if not cl:
        flash('Empresa não encontrada.', 'danger')
        return redirect(url_for('financeiro.honorarios', aba='sem'))
    token = secrets.token_urlsafe(24)
    execute_query('INSERT INTO fin_honorarios_convites (token, cliente_id, usuario_id, criado_por, expira_em) '
                  'VALUES (%s, %s, %s, %s, DATE_ADD(NOW(), INTERVAL %s DAY))',
                  (token, cliente_id, usuario_id, current_user.id, dias), fetch=False)
    link = url_for('financeiro.honorarios_convite', token=token, _external=True)
    registrar('escrita.gerou_convite_honorarios', 'financeiro', tabela='fin_honorarios_convites', registro_id=None,
              depois={'cliente_id': cliente_id, 'empresa': cl['nome_razao_social'], 'usuario_id': usuario_id, 'dias': dias})
    flash('Link para cadastrar os honorários de %s (vale %d dias, uso único, exige login): %s' % (cl['nome_razao_social'], dias, link), 'success')
    return redirect(url_for('financeiro.honorarios', aba='sem', busca=cl['nome_razao_social'][:20]))


def _convite_valido(token):
    v = execute_query('SELECT * FROM fin_honorarios_convites WHERE token = %s', (token,), fetch=True, fetch_one=True)
    if not v:
        return None, 'Este link não existe.'
    if v['usado_em']:
        return None, 'Este link já foi usado: os honorários foram cadastrados e aguardam aprovação.'
    if v['expira_em'] < datetime.now():
        return None, 'Este link venceu. Peça outro ao Anderson.'
    if v['usuario_id'] and v['usuario_id'] != current_user.id:
        return None, 'Este link foi feito para outra pessoa.'
    return v, None


@financeiro.route('/financeiro/honorarios/convite/<token>')
@login_required
def honorarios_convite(token):
    """Só login: é o token que diz a empresa (e, se houver, a pessoa)."""
    v, erro = _convite_valido(token)
    if erro:
        flash(erro, 'warning')
        return redirect('/')
    cl = execute_query('SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE id = %s', (v['cliente_id'],), fetch=True, fetch_one=True)
    if FinContrato.por_cliente(v['cliente_id']):
        flash('%s já tem contrato de honorários cadastrado.' % cl['nome_razao_social'], 'info')
        return redirect('/')
    return render_template('financeiro/honorarios.html', modo='convite', aba='sem', busca=None, contratos=[], sem_contrato=[],
                           convite=dict(v, nome_razao_social=cl['nome_razao_social'], numero_cliente=cl['numero_cliente']),
                           resumo={}, servicos=_servicos(), grupos=_grupos(), DECIMO_TIPOS=DECIMO_TIPOS, DECIMO_INICIOS=DECIMO_INICIOS, REAJUSTE_TIPOS=REAJUSTE_TIPOS,
                           hoje=date.today(), SERVICO_HONORARIOS=SERVICO_HONORARIOS,
                           acao_form=url_for('financeiro.honorarios_convite_salvar', token=token))


@financeiro.route('/financeiro/honorarios/convite/<token>/salvar', methods=['POST'])
@login_required
def honorarios_convite_salvar(token):
    v, erro = _convite_valido(token)
    if erro:
        flash(erro, 'warning')
        return redirect('/')
    try:
        novo = _criar_pendente(v['cliente_id'], request.form)
        execute_query('UPDATE fin_honorarios_convites SET usado_em = NOW(), usado_por = %s, contrato_id = %s WHERE id = %s',
                      (current_user.id, novo['id'], v['id']), fetch=False)
        flash('Honorários de %s enviados para aprovação do Anderson. Obrigado!' % novo['nome_razao_social'], 'success')
        return redirect('/')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
        return redirect(url_for('financeiro.honorarios_convite', token=token))
