# -*- coding: utf-8 -*-
"""Notas fiscais de serviço do escritório — lista, configuração fiscal e emissão.

Mora no blueprint ``financeiro`` (mesma navegação, mesmos chips de empresa).
Toda rota exige ``financeiro.notas``: a permissão nasce sem perfil nenhum, então
até o admin marcar em Perfis só o admin entra (lição de 02/10/2026).

Separado de ``routes/financeiro.py`` só pelo tamanho daquele arquivo; é
importado no fim dele para as rotas entrarem no mesmo blueprint.
"""
import json
import logging
from datetime import date, timedelta

from flask import (Response, abort, flash, jsonify, redirect, render_template,
                   request, url_for)
from flask_login import current_user

from models.fin_empresa import FinEmpresa
from models.nfse_emissao import (CAMPOS_CONFIG, EMISSORES, IND_DEST, IND_FINAL, OP_SIMP_NAC, NfseServico, preencher_modelo,
                                 REG_AP_TRIB_SN, REG_ESP_TRIB, SITUACOES, TP_RET_ISSQN, TRIB_ISSQN,
                                 NfseConfig, NfseEmitida, buscar_clientes, titulos_para_nota,
                                 tomador_do_cliente)
from routes.financeiro import _empresas_ctx, financeiro
from utils import nfse_emissor
from utils.atividade import registrar
from utils.acesso import pode_rota
from utils.auth_helper import permission_required
from utils.db_helper import execute_query
from utils.integrations.nfse_adn import numeracao

logger = logging.getLogger(__name__)
PERM = 'financeiro.notas'

_LISTAS = dict(OP_SIMP_NAC=OP_SIMP_NAC, REG_AP_TRIB_SN=REG_AP_TRIB_SN, REG_ESP_TRIB=REG_ESP_TRIB,
               TRIB_ISSQN=TRIB_ISSQN, TP_RET_ISSQN=TP_RET_ISSQN, IND_DEST=IND_DEST, IND_FINAL=IND_FINAL,
               EMISSORES=EMISSORES, SITUACOES=SITUACOES)


def _empresa_unica(emps, sel):
    """A empresa das telas de uma empresa só (config, nova nota): a escolhida no ?emp=,
    senão a primeira que já tem configuração, senão a primeira do financeiro."""
    if sel:
        return sel[0]
    for e in emps:
        if NfseConfig.get(e['cliente_id']):
            return e['cliente_id']
    return emps[0]['cliente_id'] if emps else None


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------
@financeiro.route('/financeiro/notas')
@permission_required(PERM)
def notas():
    emps, sel, mapa = _empresas_ctx()
    situacao = request.args.get('situacao') or 'todas'
    de = request.args.get('de') or ''
    ate = request.args.get('ate') or ''
    busca = (request.args.get('busca') or '').strip()
    lista = NfseEmitida.listar(empresa_ids=sel, situacao=situacao, de=de or None, ate=ate or None,
                               busca=busca or None)
    configuradas = {e['cliente_id'] for e in emps if NfseConfig.get(e['cliente_id'])}
    return render_template(
        'financeiro/notas.html', notas=lista, resumo=NfseEmitida.resumo(sel),
        fin_empresas=emps, sel_empresas=sel, emp_mapa=mapa, configuradas=configuradas,
        filtros=dict(situacao=situacao, de=de, ate=ate, busca=busca, emp=','.join(map(str, sel))),
        hoje=date.today(), **_LISTAS)


# ---------------------------------------------------------------------------
# Configuração fiscal da empresa
# ---------------------------------------------------------------------------
@financeiro.route('/financeiro/notas/config', methods=['GET', 'POST'])
@permission_required(PERM)
def notas_config():
    emps, sel, mapa = _empresas_ctx()
    emp_id = _empresa_unica(emps, sel)
    if not emp_id:
        flash('Marque uma empresa em Minhas Empresas antes de configurar a emissão.', 'warning')
        return redirect(url_for('financeiro.empresas'))
    if request.method == 'POST':
        antes = NfseConfig.get(emp_id) or {}
        dados = {k: request.form.get(k, '') for k in CAMPOS_CONFIG}
        dados['iss_retido'] = '1' if request.form.get('iss_retido') else '0'
        NfseConfig.salvar(emp_id, dados, usuario_id=current_user.id)
        depois = NfseConfig.get(emp_id) or {}
        registrar('escrita.alterou_config_nfse', 'financeiro', tabela='nfse_emissao_config',
                  registro_id=depois.get('id'),
                  antes={k: str(antes.get(k)) for k in CAMPOS_CONFIG},
                  depois={k: str(depois.get(k)) for k in CAMPOS_CONFIG})
        flash('Configuração fiscal salva.', 'success')
        return redirect(url_for('financeiro.notas_config', emp=emp_id))
    cfg = NfseConfig.get(emp_id) or {}
    empresa = next((e for e in emps if e['cliente_id'] == emp_id), None)
    return render_template(
        'financeiro/notas_config.html', cfg=cfg, empresa=empresa, emp_id=emp_id,
        series=NfseEmitida.series(emp_id), servicos=NfseServico.listar(emp_id),
        fin_empresas=emps, sel_empresas=[emp_id], emp_mapa=mapa, **_LISTAS)


@financeiro.route('/financeiro/notas/config/servico', methods=['POST'])
@permission_required(PERM)
def notas_servico():
    """Catálogo de serviços da empresa: salvar (novo ou edição) ou ligar/desligar."""
    emp_id = request.form.get('emp', '')
    if not emp_id.isdigit() or int(emp_id) not in FinEmpresa.ids_validos():
        abort(400)
    emp_id = int(emp_id)
    acao = request.form.get('acao')
    sid = request.form.get('servico_id', '')
    sid = int(sid) if sid.isdigit() else None
    if acao == 'salvar':
        dados = {k: request.form.get(k, '') for k in NfseServico.CAMPOS}
        novo_id, erro = NfseServico.salvar(emp_id, dados, servico_id=sid, usuario_id=current_user.id)
        if erro:
            flash(erro, 'danger')
        else:
            registrar('escrita.salvou_servico_nfse', 'financeiro', tabela='nfse_servicos', registro_id=novo_id,
                      depois={'empresa_id': emp_id, **{k: dados[k] for k in ('nome', 'valor_padrao')}})
            flash(f'Serviço "{dados["nome"].strip()}" salvo.', 'success')
    elif acao == 'alternar' and sid:
        NfseServico.alternar(sid, emp_id)
        registrar('escrita.alternou_servico_nfse', 'financeiro', tabela='nfse_servicos', registro_id=sid)
        flash('Serviço atualizado.', 'success')
    return redirect(url_for('financeiro.notas_config', emp=emp_id) + '#servicos')


@financeiro.route('/financeiro/notas/servico/novo', methods=['POST'])
@permission_required(PERM)
def notas_servico_json():
    """Serviço novo criado de dentro da Nova nota (JSON): entra no catálogo e volta pronto para a lista."""
    d = request.get_json(silent=True) or {}
    emp = str(d.get('emp', ''))
    if not emp.isdigit() or int(emp) not in FinEmpresa.ids_validos():
        return jsonify({'ok': False, 'erro': 'Empresa inválida.'}), 400
    emp_id = int(emp)
    dados = {'nome': d.get('nome', ''), 'descricao': d.get('descricao', ''), 'emite_nota': d.get('emite_nota', 1),
             'ordem': '999'}
    novo_id, erro = NfseServico.salvar(emp_id, dados, usuario_id=current_user.id)
    if erro:
        return jsonify({'ok': False, 'erro': erro})
    registrar('escrita.salvou_servico_nfse', 'financeiro', tabela='nfse_servicos', registro_id=novo_id,
              depois={'empresa_id': emp_id, 'nome': dados['nome'], 'origem': 'nova nota'})
    s = next((x for x in NfseServico.listar(emp_id) if x['id'] == novo_id), None)
    return jsonify({'ok': True, 'servico': NfseServico.para_tela([s])[0] if s else None})


@financeiro.route('/financeiro/notas/config/serie', methods=['POST'])
@permission_required(PERM)
def notas_serie():
    """Abre uma série nova ou liga/desliga uma existente."""
    emp_id = request.form.get('emp', '')
    if not emp_id.isdigit() or int(emp_id) not in FinEmpresa.ids_validos():
        abort(400)
    emp_id = int(emp_id)
    acao = request.form.get('acao')
    if acao == 'abrir':
        serie = (request.form.get('serie') or '').strip().upper()
        ambiente = int(request.form.get('ambiente') or 2)
        inicial = request.form.get('numero_inicial') or '0'
        try:
            sid = numeracao.abrir_serie(emp_id, serie, ambiente=ambiente,
                                        numero_inicial=int(inicial) if inicial.isdigit() else 0,
                                        observacao=(request.form.get('observacao') or '').strip()[:255] or None)
            registrar('escrita.abriu_serie_nfse', 'financeiro', tabela='nfse_dps_controle', registro_id=sid,
                      depois={'empresa_id': emp_id, 'serie': serie, 'ambiente': ambiente, 'inicial': inicial})
            flash(f'Série {serie} aberta.', 'success')
        except ValueError as exc:
            flash(str(exc), 'danger')
    elif acao == 'alternar':
        sid = request.form.get('serie_id', '')
        if sid.isdigit():
            execute_query('UPDATE nfse_dps_controle SET ativo = NOT ativo, atualizado_em = NOW() '
                          ' WHERE id = %s AND empresa_id = %s', (int(sid), emp_id))
            registrar('escrita.alternou_serie_nfse', 'financeiro', tabela='nfse_dps_controle', registro_id=int(sid))
            flash('Série atualizada.', 'success')
    return redirect(url_for('financeiro.notas_config', emp=emp_id))


# ---------------------------------------------------------------------------
# Nova nota (Pessoas · Serviço · Valores · Revisão) e emissão
# ---------------------------------------------------------------------------
@financeiro.route('/financeiro/notas/nova')
@permission_required(PERM)
def nota_nova():
    emps, sel, mapa = _empresas_ctx()
    emp_id = _empresa_unica(emps, sel)
    if not emp_id:
        flash('Marque uma empresa em Minhas Empresas antes de emitir.', 'warning')
        return redirect(url_for('financeiro.empresas'))
    cfg = NfseConfig.get(emp_id)
    if not cfg:
        flash('Preencha a configuração fiscal da empresa antes da primeira nota.', 'warning')
        return redirect(url_for('financeiro.notas_config', emp=emp_id))
    series = [s for s in NfseEmitida.series(emp_id) if s['ativo']]
    for s in series:
        s['emissor'] = nfse_emissor.emissor_da_serie(cfg, s['serie'])
    # série sugerida: a do emissor configurado, em produção
    sugerida = next((s['serie'] for s in series
                     if s['emissor'] == cfg['emissor'] and s['ambiente'] == 1), None) \
        or (series[0]['serie'] if series else '')
    # ``pre`` = o que o formulário nasce mostrando: o padrão da empresa, coberto
    # pela nota anterior (?de=, para corrigir uma rejeitada) e pelo título (?titulo=).
    competencia = date.today()
    pre = {
        'serie': sugerida, 'competencia': competencia.isoformat(),
        'cod_trib_nac': cfg.get('cod_trib_nac') or '', 'cod_trib_mun': cfg.get('cod_trib_mun') or '',
        'cod_nbs': cfg.get('cod_nbs') or '', 'cod_servico_sp': cfg.get('cod_servico_sp') or '',
        'loc_prestacao': cfg.get('municipio_ibge') or '',
        'trib_issqn': 1, 'iss_retido': bool(cfg.get('iss_retido')),
        'aliquota_iss': cfg.get('aliquota_iss') if cfg.get('op_simp_nac') == 1 else '',
        'ibscbs_cst': cfg.get('ibscbs_cst') or '000', 'ibscbs_cclasstrib': cfg.get('ibscbs_cclasstrib') or '000001',
        'ibscbs_cind_op': cfg.get('ibscbs_cind_op') or '100301',
        'ibscbs_ind_dest': cfg.get('ibscbs_ind_dest') or 0, 'ibscbs_ind_final': cfg.get('ibscbs_ind_final') or 0,
    }
    de = request.args.get('de', '')
    if de.isdigit():
        anterior = NfseEmitida.get(int(de))
        dj = anterior.get('dps_json') if anterior and anterior['empresa_id'] == emp_id else None
        if isinstance(dj, str):
            try:
                dj = json.loads(dj)
            except ValueError:
                dj = None
        if dj:
            pre.update({k: v for k, v in dj.items() if v not in (None, '') and k not in ('empresa_id', 'itens')})
            pre['de'] = int(de)
            pre['itens'] = [{'sid': i.get('servico_id') or '', 'valor': i.get('valor')} for i in (dj.get('itens') or [])]
            if not pre.get('tomador_cliente_id'):      # nota antiga, sem vínculo: a busca já nasce com o documento
                pre['tomador_busca'] = pre.get('tomador_doc') or pre.get('tomador_nome') or ''
    titulo = None
    tid = request.args.get('titulo', '')
    if tid.isdigit():
        titulo = execute_query('SELECT * FROM fin_titulos WHERE id = %s AND empresa_id = %s',
                               (int(tid), emp_id), fetch=True, fetch_one=True)
    if titulo:
        pre['fin_titulo_id'] = titulo['id']
        pre['valor_servico'] = titulo['valor']
        pre['itens'] = [{'sid': '', 'valor': str(titulo['valor'])}]
        if titulo.get('competencia'):
            competencia = titulo['competencia']
            pre['competencia'] = competencia.isoformat()
        if titulo.get('cliente_id'):
            pre['tomador_cliente_id'] = titulo['cliente_id']
        else:                                            # título sem cadastro ligado: a busca nasce preenchida
            pre['tomador_busca'] = titulo.get('contraparte_doc') or titulo.get('contraparte_nome') or ''
    if not pre.get('descricao'):
        pre['descricao'] = NfseConfig.descricao_para(cfg, date.fromisoformat(str(pre['competencia'])[:10]))
    empresa = next((e for e in emps if e['cliente_id'] == emp_id), None)
    return render_template(
        'financeiro/nota_nova.html', cfg=cfg, empresa=empresa, emp_id=emp_id, series=series, pre=pre,
        titulos=titulos_para_nota(emp_id), titulo=titulo,
        servicos=NfseServico.para_tela(NfseServico.listar(emp_id, so_ativos=True)),
        pre_itens=pre.get('itens') or ([{'sid': '', 'valor': str(pre['valor_servico'])}] if pre.get('valor_servico') else []),
        fin_empresas=emps, sel_empresas=[emp_id], emp_mapa=mapa, hoje=date.today(), **_LISTAS)


@financeiro.route('/financeiro/notas/emitir', methods=['POST'])
@permission_required(PERM)
def nota_emitir():
    emp_raw = request.form.get('emp', '')
    if not emp_raw.isdigit() or int(emp_raw) not in FinEmpresa.ids_validos():
        abort(400)
    emp_id = int(emp_raw)
    cfg = NfseConfig.get(emp_id)
    if not cfg:
        flash('Empresa sem configuração fiscal.', 'danger')
        return redirect(url_for('financeiro.notas_config', emp=emp_id))
    try:
        pedido = nfse_emissor.pedido_do_form(request.form, emp_id, cfg)
        linha = nfse_emissor.emitir(pedido, cfg, usuario_id=current_user.id)
    except nfse_emissor.PedidoInvalido as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('financeiro.nota_nova', emp=emp_id,
                                titulo=request.form.get('fin_titulo_id') or None))
    except (numeracao.SerieNaoCadastrada, numeracao.SerieInativa) as exc:
        flash(f'Série indisponível: {exc}', 'danger')
        return redirect(url_for('financeiro.notas_config', emp=emp_id))
    except Exception as exc:                 # certificado ausente, banco fora...
        logger.exception('[nfse-emissao] falha antes de reservar número')
        flash(f'Não foi possível emitir: {exc}', 'danger')
        return redirect(url_for('financeiro.nota_nova', emp=emp_id))
    if not linha:
        flash('A nota não foi gravada. Tente de novo.', 'danger')
        return redirect(url_for('financeiro.nota_nova', emp=emp_id))
    registrar('escrita.emitiu_nfse', 'financeiro', tabela='nfse_emitidas', registro_id=linha['id'],
              depois={'empresa_id': emp_id, 'serie': linha['serie'], 'numero_dps': linha['numero_dps'],
                      'situacao': linha['situacao'], 'tomador': linha.get('tomador_nome'),
                      'valor': str(linha.get('valor_servico')), 'emissor': linha.get('emissor'),
                      'ambiente': linha.get('ambiente'), 'chave': linha.get('chave_acesso')})
    if linha['situacao'] == 'autorizada':
        flash(f"Nota autorizada — série {linha['serie']} nº {linha['numero_dps']}"
              + (f", NFS-e nº {linha['numero_nfse']}" if linha.get('numero_nfse') else '')
              + (' (ambiente de teste)' if linha['ambiente'] == 2 else '') + '.', 'success')
    elif linha['situacao'] == 'rejeitada':
        flash('A nota foi recusada. Veja o motivo abaixo, corrija e emita de novo '
              '(o número ficou registrado como rejeitado).', 'danger')
    else:
        flash('Falha ao enviar a nota. O número ficou registrado; veja o erro abaixo.', 'danger')
    return redirect(url_for('financeiro.nota_detalhe', nota_id=linha['id']))


# ---------------------------------------------------------------------------
# Detalhe, XML e PDF
# ---------------------------------------------------------------------------
def _nota_ou_404(nota_id):
    n = NfseEmitida.get(nota_id)
    if not n or n['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    if isinstance(n.get('raw_resposta'), str):
        try:
            n['raw_resposta'] = json.loads(n['raw_resposta'])
        except ValueError:
            pass
    if isinstance(n.get('dps_json'), str):
        try:
            n['dps_json'] = json.loads(n['dps_json'])
        except ValueError:
            pass
    return n


@financeiro.route('/financeiro/notas/<int:nota_id>')
@permission_required(PERM)
def nota_detalhe(nota_id):
    n = _nota_ou_404(nota_id)
    emps, sel, mapa = _empresas_ctx()
    raw = n.get('raw_resposta') if isinstance(n.get('raw_resposta'), dict) else {}
    erros = raw.get('erros') or []
    alertas = raw.get('alertas') or []
    titulo = None
    if n.get('fin_titulo_id'):
        titulo = execute_query('SELECT id, descricao, valor, vencimento, status FROM fin_titulos WHERE id = %s',
                               (n['fin_titulo_id'],), fetch=True, fetch_one=True)
    # Boleto (02/11/2026): o que já existe desta nota, ou o que precisa para gerar um
    from models.fin_cobranca import FinBoleto, FinCobrancaConta
    from utils.integrations.efi.client import SITUACOES as SIT_BOLETO
    boleto = FinBoleto.da_nota(nota_id)
    if boleto and isinstance(boleto.get('itens_json'), str):
        try:
            boleto['itens_json'] = json.loads(boleto['itens_json'])
        except ValueError:
            boleto['itens_json'] = []
    contas_cobranca = [c for c in FinCobrancaConta.listar(empresa_ids=[n['empresa_id']], apenas_ativas=True)]
    from routes.financeiro_cobranca import SLOGAN, _competencia as _comp_nota
    dj = n.get('dps_json')
    if isinstance(dj, str):
        try:
            dj = json.loads(dj)
        except ValueError:
            dj = {}
    itens = (dj or {}).get('itens') or []
    valor_boleto = (dj or {}).get('valor_boleto') or n.get('valor_servico')
    hoje = date.today()
    conta_padrao = next((c for c in contas_cobranca if c['ambiente'] == 'producao'), contas_cobranca[0] if contas_cobranca else None)
    # Observação do boleto: a mensagem padrão da conta com {mes}/{ano}/{competencia} preenchidos ou o slogan;
    # a nota fiscal entra sozinha na última linha.
    msg_sugerida = preencher_modelo((conta_padrao or {}).get('mensagem_padrao') or '', _comp_nota(n)) or SLOGAN
    if conta_padrao and conta_padrao.get('dia_vencimento'):
        d = int(conta_padrao['dia_vencimento'])
        venc = date(hoje.year + (hoje.month == 12), hoje.month % 12 + 1, min(d, 28)) if hoje.day >= d else date(hoje.year, hoje.month, min(d, 28))
    else:
        venc = hoje + timedelta(days=7)
    return render_template('financeiro/nota_detalhe.html', n=n, erros=erros, alertas=alertas, titulo=titulo,
                           boleto=boleto, contas_cobranca=contas_cobranca, conta_padrao=conta_padrao,
                           itens=itens, valor_boleto=valor_boleto, venc_sugerido=venc, SIT_BOLETO=SIT_BOLETO,
                           msg_sugerida=msg_sugerida,
                           pode_cobranca=pode_rota('financeiro.cobranca'),
                           fin_empresas=emps, sel_empresas=sel, emp_mapa=mapa, **_LISTAS)


@financeiro.route('/financeiro/notas/<int:nota_id>/xml')
@permission_required(PERM)
def nota_xml(nota_id):
    n = _nota_ou_404(nota_id)
    qual = request.args.get('qual', 'nfse')
    if qual == 'dps':
        xml, nome = n.get('xml_dps') or '', f"DPS_{n['serie']}_{n['numero_dps']}.xml"
    else:
        xml = nfse_emissor.xml_nfse_da_linha(n) if n['situacao'] in ('autorizada', 'cancelada') else ''
        nome = f"NFSe_{n.get('chave_acesso') or n.get('numero_nfse') or n['id']}.xml"
    if not xml:
        return ('Esta nota não tem esse XML (ainda não foi autorizada ou o arquivo não está disponível).', 404)
    registrar('leitura.baixou_xml_nfse', 'financeiro', tabela='nfse_emitidas', registro_id=nota_id)
    return Response(xml, mimetype='application/xml',
                    headers={'Content-Disposition': f'attachment; filename="{nome}"'})


@financeiro.route('/financeiro/notas/<int:nota_id>/pdf')
@permission_required(PERM)
def nota_pdf(nota_id):
    """DANFSe da nota nacional, montado do XML (mesmo gerador da conferência de NFS-e).
    Nota da prefeitura de SP: o PDF é o da prefeitura — a tela mostra o código de
    verificação e o link da consulta pública."""
    n = _nota_ou_404(nota_id)
    if n['situacao'] not in ('autorizada', 'cancelada'):
        return ('A nota ainda não foi autorizada.', 404)
    if n.get('emissor') == 'sp':
        return redirect('https://nfe.prefeitura.sp.gov.br/publico/verificacao.aspx')
    xml = nfse_emissor.xml_nfse_da_linha(n)
    if not xml:
        return ('O XML desta NFS-e não está disponível.', 404)
    try:
        from brazilfiscalreport.danfse import Danfse
        pdf = bytes(Danfse(xml=xml).output())
    except Exception:
        logger.exception('[nfse-emissao] falha ao gerar o DANFSe da nota %s', nota_id)
        return ('Não foi possível montar o PDF desta nota. Baixe o XML por enquanto.', 422)
    registrar('leitura.baixou_pdf_nfse', 'financeiro', tabela='nfse_emitidas', registro_id=nota_id)
    inline = request.args.get('inline') in ('1', 'true', 'sim')
    nome = f"NFSe_{n['serie']}_{n['numero_dps']}_{(n.get('tomador_nome') or '')[:40]}.pdf".replace(' ', '_')
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'{"inline" if inline else "attachment"}; filename="{nome}"'})


# ---------------------------------------------------------------------------
# Ajudantes da tela (JSON)
# ---------------------------------------------------------------------------
@financeiro.route('/financeiro/notas/buscar')
@permission_required(PERM)
def notas_buscar():
    return jsonify(buscar_clientes(request.args.get('q', '')))


@financeiro.route('/financeiro/notas/tomador/<int:cliente_id>')
@permission_required(PERM)
def notas_tomador(cliente_id):
    t = tomador_do_cliente(cliente_id)
    if not t:
        abort(404)
    t['url_cadastro'] = url_for('clientes.editar', id=cliente_id)
    return jsonify(t)


@financeiro.route('/financeiro/notas/cep/<cep>')
@permission_required(PERM)
def notas_cep(cep):
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
