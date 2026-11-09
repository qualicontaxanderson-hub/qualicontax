# -*- coding: utf-8 -*-
"""Fiscal → EMISSÃO NFS-E: a matriz fiscal das empresas dos CLIENTES (06/11/2026).

Pedido do Anderson: "quero somente um lugar para codar". Por isso aqui NÃO há regra
fiscal nenhuma — só a tela:

* os campos são o mesmo pedaço de tela do Financeiro (``templates/nfse/_matriz_campos.html``);
* quem grava é o mesmo ``NfseConfig.salvar`` (e é ele que derruba a liberação quando algo muda);
* o que falta para emitir é ``faltas_da_matriz`` (models/nfse_emissao.py);
* série é ``numeracao.abrir_serie``, a mesma do Financeiro.

O Cadastro começa a ficha (CNPJ, regime, endereço, certificado); o Fiscal completa a
parte da emissão e marca "Conferido e liberado". Sem a marca, a empresa do cliente não
emite. A Qualicontax e as empresas do Financeiro continuam emitindo pelo Financeiro.
"""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from models.nfse_emissao import (CAMPOS_CONFIG, EMISSORES, IND_DEST, IND_FINAL, OP_SIMP_NAC, REG_AP_TRIB_SN,
                                 REG_ESP_TRIB, NfseConfig, NfseEmitida, empresa_da_matriz, faltas_da_matriz,
                                 liberar_matriz, revogar_matriz, situacao_das_matrizes, sugestao_da_matriz)
from routes.escrita_fiscal import _get_empresas, escrita_fiscal
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query
from utils.integrations.nfse_adn import numeracao

PERM = 'escrita_fiscal.nfse_matriz'
_LISTAS = dict(OP_SIMP_NAC=OP_SIMP_NAC, REG_AP_TRIB_SN=REG_AP_TRIB_SN, REG_ESP_TRIB=REG_ESP_TRIB,
               IND_DEST=IND_DEST, IND_FINAL=IND_FINAL, EMISSORES=EMISSORES)


def _csrf_ok():
    from routes.qrobo import csrf_valido
    return csrf_valido()


def _do_financeiro(cliente_id):
    r = execute_query('SELECT apelido FROM fin_empresas WHERE cliente_id = %s AND ativo = 1',
                      (cliente_id,), fetch=True, fetch_one=True)
    return (r or {}).get('apelido')


def _hub(aba, cliente_id, emp):
    from routes.fiscal_nfse_nova import hub_ctx
    return hub_ctx(aba, cliente_id, emp)


def _volta(cliente_id, ancora=''):
    return redirect(url_for('escrita_fiscal.emissao_nfse_empresa', cliente_id=cliente_id) + ancora)


@escrita_fiscal.route('/emissao-nfse/')
@permission_required(PERM)
def emissao_nfse():
    """Escolher a empresa + as que já têm matriz (liberadas e só configuradas)."""
    empresas = _get_empresas()                       # já sem as reservadas para quem não pode
    sit = situacao_das_matrizes()
    com = [dict(e, situacao=sit[e['id']]) for e in empresas if e['id'] in sit]
    com.sort(key=lambda e: (e['situacao'] != 'liberada', e.get('avulso') or 0,
                            int(e['numero_cliente']) if str(e.get('numero_cliente') or '').isdigit() else 10 ** 9))
    resumo = {'liberadas': sum(1 for e in com if e['situacao'] == 'liberada'),
              'configuradas': sum(1 for e in com if e['situacao'] == 'configurada'),
              'sem': len(empresas) - len(com)}
    from routes.fiscal_nfse_nova import hub_ctx
    return render_template('escrita_fiscal/emissao_nfse.html', empresas=empresas, com=com, resumo=resumo,
                           emp=None, **hub_ctx('config'))


@escrita_fiscal.route('/emissao-nfse/<int:cliente_id>')
@permission_required(PERM)
def emissao_nfse_empresa(cliente_id):
    emp = empresa_da_matriz(cliente_id)
    if not emp:
        abort(404)
    from routes.qrobo import csrf_token
    cfg = NfseConfig.get(cliente_id)
    series = NfseEmitida.series(cliente_id)
    sugestao = sugestao_da_matriz(emp)
    faltas = faltas_da_matriz(emp, cfg, series, sugestao=sugestao)
    travas = [f for f in faltas if f['nivel'] == 'trava']
    liberado_por = None
    if cfg and cfg.get('liberado_por'):
        r = execute_query('SELECT nome FROM usuarios WHERE id = %s', (cfg['liberado_por'],), fetch=True, fetch_one=True)
        liberado_por = (r or {}).get('nome')
    return render_template(
        'escrita_fiscal/emissao_nfse.html', empresas=_get_empresas(), emp=emp,
        cfg=cfg or sugestao, salva=bool(cfg), series=series, faltas=faltas, travas=travas,
        liberado_por=liberado_por, financeiro=_do_financeiro(cliente_id), csrf=csrf_token(), **_LISTAS,
        **_hub('config', cliente_id, emp))


@escrita_fiscal.route('/emissao-nfse/<int:cliente_id>/salvar', methods=['POST'])
@permission_required(PERM)
def emissao_nfse_salvar(cliente_id):
    if not _csrf_ok():
        flash('Formulário expirado. Abra a empresa de novo e salve.', 'danger')
        return _volta(cliente_id)
    if not empresa_da_matriz(cliente_id):
        abort(404)
    antes = NfseConfig.get(cliente_id) or {}
    dados = {k: request.form.get(k, '') for k in CAMPOS_CONFIG}
    dados['iss_retido'] = '1' if request.form.get('iss_retido') else '0'
    revogou = NfseConfig.salvar(cliente_id, dados, usuario_id=current_user.id)
    depois = NfseConfig.get(cliente_id) or {}
    registrar('escrita.alterou_config_nfse', 'fiscal', tabela='nfse_emissao_config',
              registro_id=depois.get('id'),
              antes={k: str(antes.get(k)) for k in CAMPOS_CONFIG},
              depois={k: str(depois.get(k)) for k in CAMPOS_CONFIG})
    if revogou:
        flash('Configuração salva. Como mudou, a liberação caiu: confira e libere de novo.', 'warning')
    else:
        flash('Configuração salva.', 'success')
    return _volta(cliente_id)


@escrita_fiscal.route('/emissao-nfse/<int:cliente_id>/liberar', methods=['POST'])
@permission_required(PERM)
def emissao_nfse_liberar(cliente_id):
    if not _csrf_ok():
        flash('Formulário expirado. Abra a empresa de novo.', 'danger')
        return _volta(cliente_id)
    emp = empresa_da_matriz(cliente_id)
    cfg = NfseConfig.get(cliente_id)
    if not emp or not cfg:
        flash('Salve a configuração antes de liberar.', 'warning')
        return _volta(cliente_id)
    if request.form.get('acao') == 'revogar':
        if revogar_matriz(cliente_id) is None:
            flash('O banco não aceitou (falta aplicar a migração nfse_emissao_05).', 'danger')
            return _volta(cliente_id)
        registrar('escrita.revogou_matriz_nfse', 'fiscal', tabela='nfse_emissao_config',
                  registro_id=cfg['id'], antes={'liberado_em': str(cfg.get('liberado_em'))})
        flash('Liberação retirada: a empresa não emite até ser conferida de novo.', 'success')
        return _volta(cliente_id)
    travas = [f for f in faltas_da_matriz(emp, cfg, NfseEmitida.series(cliente_id)) if f['nivel'] == 'trava']
    if travas:
        flash(f'Ainda falta(m) {len(travas)} item(ns) — a lista está no topo.', 'danger')
        return _volta(cliente_id)
    if liberar_matriz(cliente_id, current_user.id) is None:
        flash('O banco não aceitou a liberação (falta aplicar a migração nfse_emissao_05).', 'danger')
        return _volta(cliente_id)
    registrar('escrita.liberou_matriz_nfse', 'fiscal', tabela='nfse_emissao_config',
              registro_id=cfg['id'], depois={'liberado_por': current_user.id})
    flash('Conferido e liberado: a empresa pode emitir.', 'success')
    return _volta(cliente_id)


@escrita_fiscal.route('/emissao-nfse/<int:cliente_id>/serie', methods=['POST'])
@permission_required(PERM)
def emissao_nfse_serie(cliente_id):
    """Abre uma série (produção ou teste) ou liga/desliga — a MESMA numeração do Financeiro."""
    if not _csrf_ok():
        flash('Formulário expirado. Abra a empresa de novo.', 'danger')
        return _volta(cliente_id, '#series')
    if not empresa_da_matriz(cliente_id):
        abort(404)
    acao = request.form.get('acao')
    if acao == 'abrir':
        serie = (request.form.get('serie') or '').strip().upper()
        ambiente = 1 if request.form.get('ambiente') == '1' else 2
        inicial = (request.form.get('numero_inicial') or '0').strip()
        if not serie.isdigit():
            flash('A série do emissor nacional é só de dígitos (ex.: 1).', 'danger')
            return _volta(cliente_id, '#series')
        try:
            sid = numeracao.abrir_serie(cliente_id, serie, ambiente=ambiente,
                                        numero_inicial=int(inicial) if inicial.isdigit() else 0,
                                        observacao=(request.form.get('observacao') or '').strip()[:255] or None)
        except ValueError as exc:
            flash(str(exc), 'danger')
            return _volta(cliente_id, '#series')
        registrar('escrita.abriu_serie_nfse', 'fiscal', tabela='nfse_dps_controle', registro_id=sid,
                  depois={'empresa_id': cliente_id, 'serie': serie, 'ambiente': ambiente, 'inicial': inicial})
        flash(f"Série {serie} aberta ({'produção' if ambiente == 1 else 'teste'}).", 'success')
    elif acao == 'alternar':
        sid = request.form.get('serie_id', '')
        if sid.isdigit():
            execute_query('UPDATE nfse_dps_controle SET ativo = NOT ativo, atualizado_em = NOW() '
                          ' WHERE id = %s AND empresa_id = %s', (int(sid), cliente_id))
            registrar('escrita.alternou_serie_nfse', 'fiscal', tabela='nfse_dps_controle',
                      registro_id=int(sid))
            flash('Série atualizada.', 'success')
    return _volta(cliente_id, '#series')


# Nova nota + tomadores (08/11/2026): arquivo próprio, mesmo blueprint.
from routes import fiscal_nfse_nova  # noqa: E402,F401
