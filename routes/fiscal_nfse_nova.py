# -*- coding: utf-8 -*-
"""Fiscal → NOVA NFS-E: emissão das empresas dos CLIENTES (08/11/2026).

Só emite empresa com a matriz "Conferido e liberado" e sem trava HOJE (a lista é
recalculada na hora: certificado vence, série desliga). O tomador é da lista da PRÓPRIA
empresa (``nfse_tomadores`` — os clientes do cliente); a nota vai pelo MESMO motor do
Financeiro (``utils/nfse_emissor.emitir``) — nenhuma regra fiscal mora aqui.
Empresa do Financeiro (Qualicontax e grupo) continua emitindo pelo Financeiro.

Importado no fim de ``routes/fiscal_emissao.py`` (mesmo blueprint ``escrita_fiscal``).
"""
import json
import logging
from datetime import date

from flask import Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from models.nfse_emissao import (OP_SIMP_NAC, SITUACOES, NfseConfig, NfseEmitida, empresa_da_matriz,
                                 faltas_da_matriz, situacao_das_matrizes)
from models.nfse_tomador import LOCAIS, NfseTomador, como_tomador, pedido_do_fiscal, preencher_por_documento
from routes.escrita_fiscal import _get_empresas, escrita_fiscal
from routes.fiscal_emissao import _csrf_ok, _do_financeiro
from utils.acesso import pode_ver_empresa
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query
from utils.integrations.nfse_adn import numeracao

logger = logging.getLogger(__name__)
PERM = 'escrita_fiscal.nfse_emitir'
CAMPOS_TOMADOR = ('documento', 'nome', 'nome_fantasia', 'inscricao_municipal', 'email', 'fone', 'cep',
                  'logradouro', 'numero', 'complemento', 'bairro', 'cidade', 'uf', 'municipio_ibge')


def _pode_emitir(cliente_id):
    """(emp, cfg, series_ativas, travas, motivo). ``motivo`` preenchido = não emite."""
    emp = empresa_da_matriz(cliente_id)
    if not emp or not pode_ver_empresa(cliente_id):
        abort(404)
    cfg = NfseConfig.get(cliente_id)
    series = NfseEmitida.series(cliente_id)
    ativas = [s for s in series if s['ativo']]
    if _do_financeiro(cliente_id):
        return emp, cfg, ativas, [], 'financeiro'
    if not cfg:
        return emp, cfg, ativas, [], 'sem_matriz'
    travas = [f for f in faltas_da_matriz(emp, cfg, series) if f['nivel'] == 'trava']
    if not cfg.get('liberado_em'):
        return emp, cfg, ativas, travas, 'nao_liberada'
    if travas:
        return emp, cfg, ativas, travas, 'trava'
    return emp, cfg, ativas, [], ''


def _ids_financeiro():
    return {r['cliente_id'] for r in (execute_query('SELECT cliente_id FROM fin_empresas WHERE ativo = 1',
                                                    fetch=True) or [])}


def hub_ctx(aba, cliente_id=None, emp=None):
    """Contexto da cabeça única de NFS-e (_nfse_hub.html): empresas com a situação de cada uma
    e o cartão da empresa escolhida. ``emp`` já lido evita ler o cadastro duas vezes."""
    sit, fin = situacao_das_matrizes(), _ids_financeiro()
    empresas = []
    for e in _get_empresas():
        e = dict(e)
        e['situacao_nfse'] = 'financeiro' if e['id'] in fin else sit.get(e['id'], 'sem')
        empresas.append(e)
    card = None
    if cliente_id:
        card = dict(emp or empresa_da_matriz(cliente_id) or {})
        if card:
            card['situacao_nfse'] = 'financeiro' if cliente_id in fin else sit.get(cliente_id, 'sem')
        else:
            card = None
    return {'hub_aba': aba, 'hub_empresas': empresas, 'hub_emp': card}


def _tomador_no_pre(t):
    return {f't_{k}': (str(t.get(k)) if t.get(k) is not None else '') for k in CAMPOS_TOMADOR}


def _ids_dos_clientes():
    """Empresas que a pessoa vê e que emitem pelo Fiscal (sem as do Financeiro)."""
    fin = _ids_financeiro()
    return [e['id'] for e in _get_empresas() if e['id'] not in fin]


def com_motivo(notas):
    """Põe em cada nota o motivo curto da recusa (português) e o código do governo."""
    from models.nfse_emissao import motivo_curto
    for n in notas:
        n['motivo'], n['motivo_cod'] = motivo_curto(n.get('erro_ultimo')) if n.get('situacao') in ('rejeitada', 'erro') else ('', '')
    return notas


def painel_ctx():
    """O PAINEL de NFS-e (09/11/2026, repaginação no estilo de Clientes): o mesmo para a sub-aba
    Emitir e para a Configuração sem empresa escolhida. Só dado real: números do mês, empresas
    configuradas com os 5 passos, as prontas para começar e as últimas notas (sem as de teste)."""
    from models.nfse_emissao import PROVEDOR_DO_MUNICIPIO, REGIMES, cartoes_da_matriz
    from utils.nfse_servicos import CNAE_SERVICO
    empresas = _get_empresas()                       # já sem as reservadas para quem não pode
    sit, fin = situacao_das_matrizes(), _ids_financeiro()
    com = []
    for e in empresas:
        if e['id'] not in sit:
            continue
        emp = empresa_da_matriz(e['id']) or {}
        cfg = NfseConfig.get(e['id']) or {}
        cs = cartoes_da_matriz(faltas_da_matriz(emp, cfg, NfseEmitida.series(e['id']))) if emp else []
        ibge = ''.join(ch for ch in str(cfg.get('municipio_ibge') or emp.get('ibge') or '') if ch.isdigit())
        no_fin = e['id'] in fin
        com.append(dict(e, situacao='financeiro' if no_fin else sit[e['id']],
                        passos=[c['ok'] for c in cs], prontos=sum(1 for c in cs if c['ok']),
                        falta=next((c['travas'][0]['texto'] for c in cs if c['travas']), ''),
                        regime=emp.get('regime', ''), regime_rotulo=emp.get('regime_rotulo', ''),
                        cidade=emp.get('cidade', ''), uf=emp.get('uf', ''), doc_fmt=emp.get('doc_fmt', ''),
                        via='financeiro' if no_fin else ('prefeitura' if PROVEDOR_DO_MUNICIPIO.get(ibge) else 'nacional')))
    ordem = {'liberada': 0, 'configurada': 1, 'financeiro': 2}
    com.sort(key=lambda e: (ordem.get(e['situacao'], 3), e.get('avulso') or 0,
                            int(e['numero_cliente']) if str(e.get('numero_cliente') or '').isdigit() else 10 ** 9))
    # Prontas para começar: certificado válido, regime preenchido, PRESTA serviço pela atividade
    # principal (posto vende mercadoria), sem configuração e fora do Financeiro.
    ids_com = {e['id'] for e in com}
    cert_ok = {r['cliente_id'] for r in (execute_query(
        'SELECT DISTINCT cliente_id FROM dfe_certificados WHERE validade >= CURDATE()', fetch=True) or [])}
    dados = {r['id']: r for r in (execute_query(
        "SELECT id, regime_tributario, cnae_fiscal, cnae_fiscal_descricao FROM clientes "
        " WHERE situacao = 'ATIVO' AND regime_tributario IS NOT NULL", fetch=True) or [])}
    novas = [dict(e, regime=dados[e['id']]['regime_tributario'] or '',
                  regime_rotulo=REGIMES.get(dados[e['id']]['regime_tributario'] or '', ''),
                  atividade=dados[e['id']].get('cnae_fiscal_descricao') or '')
             for e in empresas
             if e['id'] not in ids_com and e['id'] not in fin and e['id'] in cert_ok and e['id'] in dados
             and (dados[e['id']].get('cnae_fiscal') or '') in CNAE_SERVICO and not e.get('avulso')]
    ids = [e['id'] for e in empresas if e['id'] not in fin]
    resumo = NfseEmitida.resumo(ids) if ids else {}
    recusas = {1: 0, 2: 0}
    if ids:
        for r in execute_query(
                "SELECT ambiente, COUNT(*) n FROM nfse_emitidas WHERE situacao IN ('rejeitada', 'erro') "
                " AND empresa_id IN (" + ','.join(['%s'] * len(ids)) + ") GROUP BY ambiente",
                tuple(ids), fetch=True) or []:
            recusas[int(r['ambiente'] or 1)] = int(r['n'])
    notas = [n for n in (NfseEmitida.listar(empresa_ids=ids, limite=40) if ids else []) if n['ambiente'] == 1][:8]
    kpis = {'liberadas': sum(1 for e in com if e['situacao'] == 'liberada'),
            'conferir': sum(1 for e in com if e['situacao'] == 'configurada'),
            'aptas': len(novas), 'sem': len(empresas) - len(com),
            'mes_qtd': int(resumo.get('mes_qtd') or 0), 'mes_valor': resumo.get('mes_valor') or 0,
            'recusadas': recusas[1], 'recusadas_teste': recusas[2]}
    return {'painel_com': com, 'painel_novas': novas[:9], 'painel_kpis': kpis, 'painel_notas': com_motivo(notas),
            'nomes': {e['id']: e for e in empresas}, 'SITUACOES': SITUACOES}


@escrita_fiscal.route('/nfse-emitir/')
@permission_required(PERM)
def nfse_emitir():
    """Painel de NFS-e (o mesmo da Configuração sem empresa)."""
    return render_template('escrita_fiscal/nfse_nova.html', emp=None, **painel_ctx(), **hub_ctx('emitir'))


@escrita_fiscal.route('/nfse-emitir/escolher/<aba>')
@permission_required(PERM)
def nfse_escolher(aba):
    """Sub-aba que precisa de empresa (Tomadores) aberta sem empresa: pede para escolher."""
    if aba not in ('tomadores',):
        abort(404)
    return render_template('escrita_fiscal/nfse_escolher.html', **hub_ctx(aba))


@escrita_fiscal.route('/nfse-emitir/notas')
@permission_required(PERM)
def nfse_emitidas():
    """Todas as notas emitidas pelas empresas dos clientes, com filtro (empresa vem da cabeça)."""
    emp_raw = request.args.get('emp', '')
    cid = int(emp_raw) if emp_raw.isdigit() else None
    ids = _ids_dos_clientes()
    if cid:
        if cid not in ids:
            abort(404)
        ids = [cid]
    situacao = request.args.get('situacao', 'todas')
    if situacao not in ('todas',) + tuple(SITUACOES):
        situacao = 'todas'
    busca = (request.args.get('q') or '').strip()
    mes = request.args.get('mes', '')
    de = ate = None
    if len(mes) == 7 and mes[4] == '-' and (mes[:4] + mes[5:]).isdigit():
        import calendar
        a, m = int(mes[:4]), int(mes[5:])
        if 1 <= m <= 12:
            de, ate = date(a, m, 1), date(a, m, calendar.monthrange(a, m)[1])
    todas = NfseEmitida.listar(empresa_ids=ids, situacao=situacao, de=de, ate=ate, busca=busca or None,
                               limite=500) if ids else []
    # Testes escondidos por padrão (09/11/2026); "Mostrar testes" põe de volta, listrados.
    testes = request.args.get('testes') == '1'
    n_testes = sum(1 for n in todas if n['ambiente'] == 2)
    notas = com_motivo([n for n in todas if testes or n['ambiente'] != 2])
    dias = []
    for n in notas:
        d = (n['dh_emissao'] or n['criado_em']).date()
        if not dias or dias[-1][0] != d:
            dias.append((d, []))
        dias[-1][1].append(n)
    ctx = hub_ctx('emitidas', cid)
    return render_template('escrita_fiscal/nfse_emitidas.html', dias=dias, total=len(notas), n_testes=n_testes,
                           testes=testes, situacao=situacao, busca=busca,
                           mes=mes, resumo=NfseEmitida.resumo(ids) if ids else {}, SITUACOES=SITUACOES,
                           nomes={e['id']: e for e in ctx['hub_empresas']}, **ctx)


def _render_nova(cliente_id, pre=None, erro=None):
    from routes.qrobo import csrf_token
    emp, cfg, ativas, travas, motivo = _pode_emitir(cliente_id)
    cfg = cfg or {}
    hoje = date.today()
    pre = dict(pre or {})
    pre.setdefault('serie', str(cfg.get('serie_nacional') or ''))
    pre.setdefault('competencia', hoje.isoformat())
    pre.setdefault('descricao', NfseConfig.descricao_para(cfg, hoje) if cfg else '')
    pre.setdefault('local', 'empresa')
    pre.setdefault('iss_retido', '1' if cfg.get('iss_retido') else '')
    return render_template(
        'escrita_fiscal/nfse_nova.html', emp=emp, cfg=cfg, series=ativas,
        travas=travas, motivo=motivo, pre=pre, erro=erro, notas=com_motivo(NfseEmitida.listar(empresa_ids=[cliente_id], limite=8)),
        n_tomadores=NfseTomador.contar(cliente_id), LOCAIS=LOCAIS, SITUACOES=SITUACOES,
        OP_SIMP_NAC=OP_SIMP_NAC, hoje=hoje, csrf=csrf_token(), **hub_ctx('emitir', cliente_id, emp))


@escrita_fiscal.route('/nfse-emitir/<int:cliente_id>')
@permission_required(PERM)
def nfse_nova(cliente_id):
    pre = {}
    de = request.args.get('de', '')
    if de.isdigit():                       # corrigir uma recusada: nasce com o que foi mandado
        n = NfseEmitida.get(int(de))
        dj = n.get('dps_json') if n and n['empresa_id'] == cliente_id else None
        try:
            dj = json.loads(dj) if isinstance(dj, str) else (dj or {})
        except ValueError:
            dj = {}
        if dj:
            pre = {k: str(dj[k]) for k in ('serie', 'competencia', 'descricao', 'valor_servico', 'ret_pis',
                                           'ret_cofins', 'ret_inss', 'ret_irrf', 'ret_csll')
                   if dj.get(k) not in (None, '', '0', '0.00')}
            pre['iss_retido'] = '1' if dj.get('iss_retido') else ''
            if dj.get('aliquota_iss') not in (None, '', 'None'):
                pre['aliquota_iss'] = str(dj['aliquota_iss'])
            pre['ind_final'] = str(dj.get('ibscbs_ind_final') or 0)
            cfg = NfseConfig.get(cliente_id) or {}
            pre['local'] = ('empresa' if str(dj.get('loc_prestacao') or '') == str(cfg.get('municipio_ibge') or '')
                            else 'tomador')
            t = NfseTomador.por_documento(cliente_id, dj.get('tomador_doc'))
            if t:
                pre.update(_tomador_no_pre(t))
    tid = request.args.get('tomador', '')
    if tid.isdigit():
        t = NfseTomador.get(int(tid), cliente_id)
        if t:
            pre.update(_tomador_no_pre(t))
    return _render_nova(cliente_id, pre)


@escrita_fiscal.route('/nfse-emitir/<int:cliente_id>', methods=['POST'])
@permission_required(PERM)
def nfse_emitir_post(cliente_id):
    from utils import nfse_emissor
    pre = request.form.to_dict()
    pre.pop('csrf_token', None)
    if not _csrf_ok():
        return _render_nova(cliente_id, pre, 'O formulário expirou. Confira os dados e clique em Emitir de novo.')
    emp, cfg, ativas, travas, motivo = _pode_emitir(cliente_id)
    if motivo:
        return _render_nova(cliente_id, pre, 'Esta empresa não está liberada para emitir agora.')
    dados_t = {k: request.form.get(f't_{k}', '') for k in CAMPOS_TOMADOR}
    tid, erro = NfseTomador.salvar(cliente_id, dados_t, usuario_id=current_user.id,
                                   fonte=(request.form.get('t_fonte') or 'manual'))
    if erro:
        return _render_nova(cliente_id, pre, erro)
    tomador = como_tomador(NfseTomador.get(tid, cliente_id))
    try:
        pedido = pedido_do_fiscal(request.form, cliente_id, cfg, tomador, ativas)
        linha = nfse_emissor.emitir(pedido, cfg, usuario_id=current_user.id)
    except nfse_emissor.PedidoInvalido as exc:
        return _render_nova(cliente_id, pre, str(exc))
    except (numeracao.SerieNaoCadastrada, numeracao.SerieInativa) as exc:
        return _render_nova(cliente_id, pre, f'Série indisponível: {exc}')
    except Exception as exc:               # certificado, banco — antes de reservar número
        logger.exception('[nfse-fiscal] falha antes de reservar número (empresa %s)', cliente_id)
        return _render_nova(cliente_id, pre, f'Não foi possível emitir: {exc}')
    if not linha:
        return _render_nova(cliente_id, pre, 'A nota não foi gravada. Tente de novo.')
    registrar('escrita.emitiu_nfse', 'fiscal', tabela='nfse_emitidas', registro_id=linha['id'],
              depois={'empresa_id': cliente_id, 'serie': linha['serie'], 'numero_dps': linha['numero_dps'],
                      'situacao': linha['situacao'], 'tomador': linha.get('tomador_nome'), 'tomador_id': tid,
                      'valor': str(linha.get('valor_servico')), 'emissor': linha.get('emissor'),
                      'ambiente': linha.get('ambiente'), 'chave': linha.get('chave_acesso')})
    if linha['situacao'] == 'autorizada':
        NfseTomador.marcar_nota(tid)
        flash(f"Nota autorizada: série {linha['serie']} nº {linha['numero_dps']}"
              + (f", NFS-e nº {linha['numero_nfse']}" if linha.get('numero_nfse') else '')
              + (' (ambiente de TESTE)' if linha['ambiente'] == 2 else '') + '.', 'success')
    elif linha['situacao'] == 'rejeitada':
        flash('A nota foi recusada. O motivo está abaixo: corrija e emita de novo '
              '(o número ficou registrado como recusado).', 'danger')
    else:
        flash('Falha ao enviar a nota. O número ficou registrado; o erro está abaixo.', 'danger')
    return redirect(url_for('escrita_fiscal.nfse_nota', nota_id=linha['id']))


@escrita_fiscal.route('/nfse-emitir/<int:cliente_id>/tomador')
@permission_required(PERM)
def nfse_tomador_doc(cliente_id):
    """Digitou o CPF/CNPJ: já é tomador? é cliente do escritório? senão, Receita."""
    if not pode_ver_empresa(cliente_id):
        abort(404)
    return jsonify(preencher_por_documento(cliente_id, request.args.get('doc', '')))


@escrita_fiscal.route('/nfse-emitir/<int:cliente_id>/tomadores.json')
@permission_required(PERM)
def nfse_tomadores_json(cliente_id):
    if not pode_ver_empresa(cliente_id):
        abort(404)
    return jsonify([como_tomador(r) for r in NfseTomador.listar(cliente_id, request.args.get('q', ''), limite=15)])


@escrita_fiscal.route('/nfse-emitir/cep/<cep>')
@permission_required(PERM)
def nfse_cep(cep):
    from utils.cep import CepIndisponivel, consultar
    try:
        r = consultar(cep)
    except CepIndisponivel:
        return jsonify({'erro': 'Serviço de CEP fora do ar'}), 503
    if not r:
        return jsonify({'erro': 'CEP não encontrado'}), 404
    return jsonify({'logradouro': r.get('logradouro') or '', 'bairro': r.get('bairro') or '',
                    'cidade': r.get('localidade') or '', 'uf': r.get('uf') or '',
                    'ibge': ''.join(ch for ch in str(r.get('ibge') or '') if ch.isdigit())})


# --- Tomadores (lista da empresa) ------------------------------------------------
@escrita_fiscal.route('/nfse-emitir/<int:cliente_id>/tomadores')
@permission_required(PERM)
def nfse_tomadores(cliente_id):
    from routes.qrobo import csrf_token
    emp = empresa_da_matriz(cliente_id)
    if not emp or not pode_ver_empresa(cliente_id):
        abort(404)
    busca = request.args.get('q', '')
    inativos = request.args.get('inativos') == '1'
    editar = None
    eid = request.args.get('editar', '')
    if eid.isdigit():
        editar = NfseTomador.get(int(eid), cliente_id)
    from models.nfse_tomador import numeros_por_tomador
    return render_template('escrita_fiscal/nfse_tomadores.html', modo='uma', emp=emp, busca=busca, inativos=inativos,
                           tomadores=NfseTomador.listar(cliente_id, busca, limite=1000, so_ativos=not inativos),
                           numeros=numeros_por_tomador(cliente_id),
                           editar=editar, novo=request.args.get('novo') == '1',
                           liberada=not _pode_emitir(cliente_id)[4], csrf=csrf_token(),
                           **hub_ctx('tomadores', cliente_id, emp))


@escrita_fiscal.route('/nfse-emitir/tomadores')
@permission_required(PERM)
def nfse_tomadores_todos():
    """Tomadores de TODAS as empresas que a pessoa vê (09/11/2026) — visão do escritório:
    o mesmo CNPJ agrupado, com o contato que CADA empresa cadastrou (o contato de uma não
    aparece na tela de outra). Empresa do Financeiro fica de fora, como no resto do Fiscal."""
    from models.nfse_tomador import tomadores_de_todas
    busca = (request.args.get('q') or '').strip()
    volta = request.args.get('emp', '')
    volta_emp = empresa_da_matriz(int(volta)) if volta.isdigit() and pode_ver_empresa(int(volta)) else None
    grupos = tomadores_de_todas(_ids_dos_clientes(), busca)
    return render_template('escrita_fiscal/nfse_tomadores.html', modo='todas', emp=None, busca=busca,
                           grupos=grupos, volta_emp=volta_emp, **hub_ctx('tomadores'))


@escrita_fiscal.route('/nfse-emitir/<int:cliente_id>/tomadores', methods=['POST'])
@permission_required(PERM)
def nfse_tomadores_salvar(cliente_id):
    if not pode_ver_empresa(cliente_id) or not empresa_da_matriz(cliente_id):
        abort(404)
    volta = url_for('escrita_fiscal.nfse_tomadores', cliente_id=cliente_id)
    if not _csrf_ok():
        flash('Formulário expirado. Tente de novo.', 'danger')
        return redirect(volta)
    tid = request.form.get('tomador_id', '')
    if request.form.get('acao') == 'alternar' and tid.isdigit():
        NfseTomador.alternar(int(tid), cliente_id)
        registrar('escrita.alternou_tomador_nfse', 'fiscal', tabela='nfse_tomadores', registro_id=int(tid))
        flash('Tomador atualizado.', 'success')
        return redirect(volta)
    antes = NfseTomador.get(int(tid), cliente_id) if tid.isdigit() else None
    dados = {k: request.form.get(f't_{k}', '') for k in CAMPOS_TOMADOR}
    novo_id, erro = NfseTomador.salvar(cliente_id, dados, usuario_id=current_user.id,
                                       fonte=request.form.get('t_fonte') or 'manual',
                                       tomador_id=int(tid) if tid.isdigit() else None)
    if erro:
        flash(erro, 'danger')
        return redirect(volta + (f'?editar={tid}' if tid.isdigit() else '?novo=1'))
    depois = NfseTomador.get(novo_id, cliente_id) or {}
    registrar('escrita.salvou_tomador_nfse', 'fiscal', tabela='nfse_tomadores', registro_id=novo_id,
              antes={k: str(antes.get(k)) for k in CAMPOS_TOMADOR} if antes else None,
              depois={k: str(depois.get(k)) for k in CAMPOS_TOMADOR})
    flash('Tomador salvo.', 'success')
    return redirect(volta)


# --- A nota emitida -------------------------------------------------------------------
def _nota_ou_404(nota_id):
    n = NfseEmitida.get(nota_id)
    if not n or not pode_ver_empresa(n['empresa_id']) or _do_financeiro(n['empresa_id']):
        abort(404)
    for k in ('raw_resposta', 'dps_json'):
        if isinstance(n.get(k), str):
            try:
                n[k] = json.loads(n[k])
            except ValueError:
                pass
    return n


@escrita_fiscal.route('/nfse-emitir/nota/<int:nota_id>')
@permission_required(PERM)
def nfse_nota(nota_id):
    from routes.qrobo import csrf_token
    from utils.integrations.nfse_adn.eventos import MOTIVOS_CANCELAMENTO
    n = _nota_ou_404(nota_id)
    raw = n.get('raw_resposta') if isinstance(n.get('raw_resposta'), dict) else {}
    return render_template('escrita_fiscal/nfse_nota.html', n=n, erros=raw.get('erros') or [],
                           alertas=raw.get('alertas') or [], SITUACOES=SITUACOES, MOTIVOS=MOTIVOS_CANCELAMENTO,
                           csrf=csrf_token(), **hub_ctx('emitidas', n['empresa_id']))


@escrita_fiscal.route('/nfse-emitir/nota/<int:nota_id>/xml')
@permission_required(PERM)
def nfse_nota_xml(nota_id):
    from utils import nfse_emissor
    n = _nota_ou_404(nota_id)
    if request.args.get('qual') == 'dps':
        xml, nome = n.get('xml_dps') or '', f"DPS_{n['serie']}_{n['numero_dps']}.xml"
    else:
        xml = nfse_emissor.xml_nfse_da_linha(n) if n['situacao'] in ('autorizada', 'cancelada') else ''
        nome = f"NFSe_{n.get('chave_acesso') or n.get('numero_nfse') or n['id']}.xml"
    if not xml:
        return ('Esta nota não tem esse XML (ainda não foi autorizada ou o arquivo não está disponível).', 404)
    registrar('leitura.baixou_xml_nfse', 'fiscal', tabela='nfse_emitidas', registro_id=nota_id)
    return Response(xml, mimetype='application/xml', headers={'Content-Disposition': f'attachment; filename="{nome}"'})


@escrita_fiscal.route('/nfse-emitir/nota/<int:nota_id>/pdf')
@permission_required(PERM)
def nfse_nota_pdf(nota_id):
    from utils import nfse_emissor
    n = _nota_ou_404(nota_id)
    if n['situacao'] not in ('autorizada', 'cancelada'):
        return ('A nota ainda não foi autorizada.', 404)
    xml = nfse_emissor.xml_nfse_da_linha(n)
    if not xml:
        return ('O XML desta NFS-e não está disponível.', 404)
    try:
        from brazilfiscalreport.danfse import Danfse
        pdf = bytes(Danfse(xml=xml).output())
    except Exception:
        logger.exception('[nfse-fiscal] falha ao gerar o DANFSe da nota %s', nota_id)
        return ('Não foi possível montar o PDF desta nota. Baixe o XML por enquanto.', 422)
    registrar('leitura.baixou_pdf_nfse', 'fiscal', tabela='nfse_emitidas', registro_id=nota_id)
    inline = request.args.get('inline') in ('1', 'true', 'sim')
    nome = f"NFSe_{n['serie']}_{n['numero_dps']}_{(n.get('tomador_nome') or '')[:40]}.pdf".replace(' ', '_')
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'{"inline" if inline else "attachment"}; filename="{nome}"'})


@escrita_fiscal.route('/nfse-emitir/nota/<int:nota_id>/cancelar', methods=['POST'])
@permission_required(PERM)
def nfse_nota_cancelar(nota_id):
    from utils import nfse_emissor
    n = _nota_ou_404(nota_id)
    volta = redirect(url_for('escrita_fiscal.nfse_nota', nota_id=nota_id))
    if not _csrf_ok():
        flash('Formulário expirado. Tente de novo.', 'danger')
        return volta
    try:
        linha = nfse_emissor.cancelar_nota(nota_id, request.form.get('c_motivo'), request.form.get('x_motivo'),
                                           usuario_id=current_user.id)
    except ValueError as exc:
        flash(str(exc), 'warning')
        return volta
    except nfse_emissor.CancelamentoRecusado as exc:
        flash(f'O governo não cancelou a nota: {exc}', 'danger')
        return volta
    except Exception as exc:
        logger.exception('[nfse-fiscal] cancelamento da nota %s', nota_id)
        flash(f'Falha ao cancelar: {exc}', 'danger')
        return volta
    registrar('escrita.cancelou_nfse', 'fiscal', tabela='nfse_emitidas', registro_id=nota_id,
              depois={'chave': n.get('chave_acesso'), 'protocolo': linha.get('cancelamento_protocolo'),
                      'motivo': linha.get('cancelamento_motivo'), 'ambiente': n.get('ambiente')})
    flash('Nota cancelada no governo.', 'success')
    return volta
