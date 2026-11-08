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


def _empresas_liberadas():
    sit = situacao_das_matrizes()
    fin = {r['cliente_id'] for r in (execute_query('SELECT cliente_id FROM fin_empresas WHERE ativo = 1',
                                                   fetch=True) or [])}
    return [e for e in _get_empresas() if sit.get(e['id']) == 'liberada' and e['id'] not in fin]


def _tomador_no_pre(t):
    return {f't_{k}': (str(t.get(k)) if t.get(k) is not None else '') for k in CAMPOS_TOMADOR}


@escrita_fiscal.route('/nfse-emitir/')
@permission_required(PERM)
def nfse_emitir():
    """Escolher a empresa (só as liberadas) + as últimas notas delas."""
    empresas = _empresas_liberadas()
    ids = [e['id'] for e in empresas]
    notas = NfseEmitida.listar(empresa_ids=ids, limite=30) if ids else []
    return render_template('escrita_fiscal/nfse_nova.html', empresas=empresas, emp=None, notas=notas,
                           nomes={e['id']: e for e in empresas}, SITUACOES=SITUACOES)


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
        'escrita_fiscal/nfse_nova.html', empresas=_empresas_liberadas(), emp=emp, cfg=cfg, series=ativas,
        travas=travas, motivo=motivo, pre=pre, erro=erro, notas=NfseEmitida.listar(empresa_ids=[cliente_id], limite=15),
        nomes={emp['id']: {'numero_cliente': emp['numero_cliente'], 'nome_razao_social': emp['nome']}},
        n_tomadores=NfseTomador.contar(cliente_id), LOCAIS=LOCAIS, SITUACOES=SITUACOES,
        OP_SIMP_NAC=OP_SIMP_NAC, hoje=hoje, csrf=csrf_token())


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
    return render_template('escrita_fiscal/nfse_tomadores.html', emp=emp, busca=busca, inativos=inativos,
                           tomadores=NfseTomador.listar(cliente_id, busca, limite=1000, so_ativos=not inativos),
                           editar=editar, novo=request.args.get('novo') == '1',
                           liberada=not _pode_emitir(cliente_id)[4], csrf=csrf_token())


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
                           csrf=csrf_token())


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
