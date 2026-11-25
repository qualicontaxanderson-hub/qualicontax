# -*- coding: utf-8 -*-
"""Comercial › Orçamentos (21/11/2026).

Anderson (20/11/2026): "o oficial teria que ter sido feito um Orçamento no Comercial, aprovado por nós, para que seja
feito e enviado o Orçamento. Aprovado e feito o serviço, chegou a hora de cobrar: o Gabriel entra no orçamento que ele
fez e manda para a Cobrança; já vem para Financeiro / Lançamentos com o histórico completo, e eu novamente aprovo
para cobrar". O atalho antigo (lançar direto em Lançamentos) continua valendo.

Caminho: RASCUNHO → (pedir aprovação) APROVACAO → (admin) APROVADO → (enviar ao cliente, PDF por e-mail) →
(serviço feito: mandar para a cobrança) COBRANCA = lançamento LANCADO em Financeiro › Lançamentos → (admin aprova lá)
CONCLUIDO. REPROVADO volta para a pessoa corrigir; CANCELADO encerra.
"""
import hashlib
import json
import logging
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from models.fin_lancamento import FinComissaoAcordo, FinLancamento
from models.orcamento import COMISSAO, COMO, STATUS, Orcamento
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
comercial = Blueprint('comercial', __name__)

EMP = 2
VER = 'comercial.orcamentos'
INCLUIR = 'comercial.orcamentos.incluir'
APROVAR = 'comercial.orcamentos.aprovar'


def _aprovador():
    return current_user.is_admin() or current_user.has_permission(APROVAR)


def _dono_ou_aprovador(o):
    return _aprovador() or o['criado_por'] == current_user.id


def _volta(oid=None):
    return redirect(url_for('comercial.orcamentos', abrir=oid) if oid else url_for('comercial.orcamentos'))


def _servicos():
    return execute_query("""SELECT id, nome, valor_padrao, emite_nota, comissao_tipo, comissao_valor
                              FROM nfse_servicos WHERE empresa_id = %s AND ativo = 1 ORDER BY nome""", (EMP,), fetch=True) or []


def _clientes():
    """Cadastrados e avulsos (Anderson: "a) cliente cadastrado ou b) avulso"), com a mensalidade para a caixa."""
    linhas = execute_query("""SELECT c.id, c.numero_cliente AS num, c.nome_razao_social AS nome, c.cpf_cnpj AS doc, COALESCE(c.avulso, 0) AS av,
                                     (SELECT SUM(k.valor) FROM fin_contratos k WHERE k.cliente_id = c.id AND k.ativo = 1) AS mensal
                                FROM clientes c WHERE COALESCE(c.situacao, 'ATIVO') <> 'INATIVO' ORDER BY c.nome_razao_social""", fetch=True) or []
    return [{'id': c['id'], 'num': str(c['num'] or ''), 'nome': c['nome'], 'doc': c['doc'] or '', 'av': bool(c['av']), 'mensal': float(c['mensal'] or 0)} for c in linhas]


def _comissao_padrao():
    """O % padrão de quem está logado (acordo geral de serviço), para a caixa mostrar."""
    a = next((x for x in FinComissaoAcordo.listar(usuario_id=current_user.id, so_ativos=True) if x['base'] == 'SERVICO' and not x['servico_id']), None)
    if not a:
        return None
    return {'tipo': a['tipo'], 'valor': float(a['valor'])}


def _texto_whatsapp(o, itens, link):
    """O texto pronto para colar no WhatsApp, no molde da cobrança (Anderson: "quero poder mandar assim para o cliente")."""
    quem = (' ' + o['solicitante_nome'].split(' ')[0].title()) if o.get('solicitante_nome') else ''
    linhas = ['Olá%s! Aqui é da *Qualicontax*. 😊' % (',' + quem if quem else ''), '',
              'Segue o orçamento *%s* para a *%s*%s.' % (o['numero'], o['cliente_nome'], (' — %s' % o['titulo']) if o.get('titulo') else ''), '']
    for i in itens:
        linhas.append('📌 %s – R$ %s%s' % (i['descricao'], _brl(i['valor_total']), '' if i['emite_nota'] else ' (taxa repassada)'))
    linhas.append('💰 *Total: R$ %s*%s' % (_brl(o['total']), (' · em %dx' % o['parcelas']) if o.get('parcelas') and o['parcelas'] > 1 else ''))
    if o.get('como'):
        linhas.append('💳 Pagamento: %s' % COMO[o['como']])
    if o.get('validade'):
        linhas.append('📅 Válido até %s' % o['validade'].strftime('%d/%m/%Y'))
    linhas += ['', '👉 Para ver a proposta completa e aceitar, é só tocar no link. Lá você confirma com um código que chega no seu e-mail.', link, '',
               'Qualquer dúvida é só responder por aqui. Obrigado pela parceria! 🙏']
    return '\n'.join(linhas)


def _texto_financeiro(o, itens, lanc):
    """Aviso pronto para quem monta o orçamento mandar ao Financeiro aprovar a cobrança (22/11/2026 — Anderson: "temos que ter um
    texto para facilitar a vida deles lá... quanto mais fácil for para eles e para nós")."""
    quem = (o.get('criado_por_nome') or '').split(' ')[0].title()
    linhas = ['Oi! %s aqui. 🙂' % quem if quem else 'Oi!', '',
              'Serviço feito, pode aprovar a cobrança? 🙏', '',
              '🏢 *%s*' % o['cliente_nome'], '📄 Orçamento *%s*%s' % (o['numero'], (' — ' + o['titulo']) if o.get('titulo') else '')]
    for i in itens:
        linhas.append('📌 %s – R$ %s' % (i['descricao'], _brl(i['valor_total'])))
    linhas.append('💰 *Total: R$ %s*' % _brl(o['total']))
    if o.get('aceito_em'):
        linhas.append('✅ Aceito pelo cliente: %s em %s' % (o.get('aceito_nome') or '', o['aceito_em'].strftime('%d/%m/%Y %H:%M')))
    elif o.get('interno'):
        linhas.append('🔒 Controle interno (sem aceite do cliente)')
    como = COMO.get(lanc.get('como') or o.get('como') or '', '')
    venc = lanc.get('vencimento')
    linhas.append('💳 %s%s' % (como or 'cobrança', (' · vence %s' % venc.strftime('%d/%m/%Y')) if venc else ''))
    linhas += ['', '👉 Lançamento nº %s em Financeiro › Lançamentos › Para aprovar:' % lanc['id'],
               url_for('financeiro.lancamentos', aba='apr', _external=True) + '#l%s' % lanc['id'], '',
               '🔎 Histórico completo no orçamento:', url_for('comercial.orcamentos', abrir=o['id'], _external=True)]
    return '\n'.join(linhas)


def _wa_link(fone, texto):
    from urllib.parse import quote
    d = ''.join(ch for ch in str(fone or '') if ch.isdigit())
    if len(d) in (10, 11):
        d = '55' + d
    if len(d) not in (12, 13):
        return None
    return 'https://wa.me/%s?text=%s' % (d, quote(texto))


# ───────────────────────── início do Comercial: números reais (21/11/2026) ─────────────────────────
_HOME_CACHE, _HOME_TTL = {}, 60


def _home_payload():
    """Carrossel + contadores da home do Comercial, no formato de home_destaques.js. Só o que tem dado: sem número, sem card."""
    apr = _aprovador()
    quem = None if apr else current_user.id
    orcs = Orcamento.listar(EMP, criado_por=quem)
    hoje = date.today()
    cards, counters = [], {}
    andamento = [o for o in orcs if o['status'] in ('APROVACAO', 'APROVADO', 'ACEITO', 'COBRANCA')]
    if andamento:
        n_apr = sum(1 for o in andamento if o['status'] == 'APROVACAO')
        cards.append({'icone': 'fa-file-signature', 'titulo': 'Orçamentos em andamento (R$)', 'valor': int(round(sum(float(o['total'] or 0) for o in andamento))),
                      'apoio': '%d orçamento%s%s' % (len(andamento), 's' if len(andamento) != 1 else '', (' · %d para aprovar' % n_apr) if n_apr else ''),
                      'trend': {'tipo': 'neutro', 'rotulo': 'aberto'}})
    aceitos = [o for o in orcs if o.get('aceito_em') and o['aceito_em'].year == hoje.year and o['aceito_em'].month == hoje.month]
    if aceitos:
        cards.append({'icone': 'fa-circle-check', 'titulo': 'Aceitos pelo cliente no mês (R$)', 'valor': int(round(sum(float(o['total'] or 0) for o in aceitos))),
                      'apoio': '%d aceite%s em %s' % (len(aceitos), 's' if len(aceitos) != 1 else '', hoje.strftime('%m/%Y')), 'trend': {'tipo': 'neutro', 'rotulo': 'mês'}})
    por = {}
    for o in orcs:
        por[o['status']] = por.get(o['status'], 0) + 1
    itens = [{'rotulo': STATUS[k][0], 'valor': por[k]} for k in ('APROVACAO', 'APROVADO', 'ACEITO', 'COBRANCA', 'CONCLUIDO', 'RASCUNHO') if por.get(k)]
    if itens:
        cards.append({'tipo': 'lista', 'icone': 'fa-list-check', 'titulo': 'Orçamentos por situação', 'itens': itens, 'trend': {'tipo': 'neutro', 'rotulo': 'hoje'}})
    counters['orcamentos'] = len(andamento) if orcs else ''
    try:
        from models.prest_contrato import PrestContrato
        ctr = execute_query("SELECT status, COUNT(*) n FROM prest_contratos WHERE COALESCE(tipo, 'CONTRATO') = 'CONTRATO' GROUP BY status", fetch=True) or []
        rot = {'RASCUNHO': 'Rascunhos', 'APROVACAO': 'Para aprovar', 'APROVADO': 'Aprovados', 'ENVIADO': 'Aguardando assinatura', 'ASSINADO': 'Assinados', 'CANCELADO': 'Cancelados'}
        cm = {r['status']: int(r['n']) for r in ctr}
        li = [{'rotulo': rot.get(k, k), 'valor': cm[k]} for k in ('APROVACAO', 'ENVIADO', 'ASSINADO', 'APROVADO', 'RASCUNHO') if cm.get(k)]
        if li:
            cards.append({'tipo': 'lista', 'icone': 'fa-file-contract', 'titulo': 'Contratos de prestação', 'itens': li, 'trend': {'tipo': 'neutro', 'rotulo': 'hoje'}})
        counters['contratos'] = (cm.get('APROVACAO', 0) + cm.get('ENVIADO', 0)) or ''
        # propostas de serviços (25/11/2026)
        pr = execute_query("SELECT status, COUNT(*) n FROM prest_contratos WHERE tipo = 'PROPOSTA' GROUP BY status", fetch=True) or []
        rp = {'RASCUNHO': 'Rascunhos', 'APROVACAO': 'Para aprovar', 'APROVADO': 'Aprovadas', 'ENVIADO': 'Com o cliente', 'ACEITO': 'Aceitas', 'RECUSADO': 'Recusadas', 'CONVERTIDO': 'Convertidas', 'CANCELADO': 'Canceladas'}
        pm = {r['status']: int(r['n']) for r in pr}
        lp = [{'rotulo': rp.get(k, k), 'valor': pm[k]} for k in ('APROVACAO', 'APROVADO', 'ENVIADO', 'ACEITO', 'CONVERTIDO', 'RASCUNHO') if pm.get(k)]
        if lp:
            cards.append({'tipo': 'lista', 'icone': 'fa-file-lines', 'titulo': 'Propostas de serviços', 'itens': lp, 'trend': {'tipo': 'neutro', 'rotulo': 'hoje'}})
        counters['propostas'] = (pm.get('APROVACAO', 0) + pm.get('ENVIADO', 0) + pm.get('ACEITO', 0)) or ''
    except Exception:
        logger.exception('[comercial] home: contratos')
    try:
        cl = execute_query("""SELECT SUM(COALESCE(avulso, 0) = 0) AS emp, SUM(COALESCE(avulso, 0) = 1) AS av FROM clientes
                                WHERE COALESCE(situacao, 'ATIVO') <> 'INATIVO'""", fetch=True, fetch_one=True) or {}
        emp, av = int(cl.get('emp') or 0), int(cl.get('av') or 0)
        if emp or av:
            cards.append({'icone': 'fa-building', 'titulo': 'Clientes ativos', 'valor': emp, 'valor_sufixo': (' + %d avulsos' % av) if av else '', 'apoio': 'empresas no cadastro · avulsos à parte', 'trend': {'tipo': 'neutro', 'rotulo': 'hoje'}})
        counters['clientes'] = emp or ''
    except Exception:
        logger.exception('[comercial] home: clientes')
    import time as _t
    return {'cards': cards, 'counters': counters, 'gerado_em_ms': int(_t.time() * 1000)}   # home_destaques.js só mostra o carrossel se for fresco (< 5 min)


@comercial.route('/comercial/api/home')
@permission_required('modulos.comercial')
def api_home():
    import time as _t
    uid = getattr(current_user, 'id', None); agora = _t.time()
    hit = _HOME_CACHE.get(uid)
    if hit and agora - hit[0] < _HOME_TTL:
        return jsonify(hit[1])
    payload = _home_payload()
    _HOME_CACHE[uid] = (agora, payload)
    return jsonify(payload)


# ───────────────────────── a tela ─────────────────────────
@comercial.route('/comercial/orcamentos')
@permission_required(VER)
def orcamentos():
    apr = _aprovador()
    quem = None if apr else current_user.id            # quem não aprova vê só os seus
    lista = Orcamento.listar(EMP, criado_por=quem)
    hoje = date.today()
    for o in lista:
        o['rot'], o['cls'], o['ico'] = STATUS.get(o['status'], (o['status'], 'tx-perd', 'fa-circle'))
        o['vencido'] = Orcamento.vencido(o)
    cont = {k: [0, 0.0] for k in STATUS}
    for o in lista:
        k = 'APROVADO' if o['status'] == 'ACEITO' else o['status']        # aceito pelo cliente conta como aprovado na aba
        cont[k][0] += 1; cont[k][1] += float(o['total'] or 0)
    pessoas = {}
    for o in lista:
        pessoas.setdefault(o['criado_por'], o['criado_por_nome'] or ('#%s' % o['criado_por']))
    abrir = request.args.get('abrir', type=int)
    return render_template('comercial/orcamentos.html', lista=lista, cont=cont, STATUS=STATUS, COMO=COMO, COMISSAO=COMISSAO, hoje=hoje,
                           aprovador=apr, pode_incluir=current_user.is_admin() or current_user.has_permission(INCLUIR),
                           pessoas=pessoas, abrir=abrir, aba=request.args.get('aba') or '',
                           clientes_json=json.dumps(_clientes(), ensure_ascii=False),
                           servicos_json=json.dumps([{'id': s['id'], 'nome': s['nome'], 'valor_padrao': float(s['valor_padrao'] or 0), 'nota': bool(s['emite_nota']),
                                                      'ct': s['comissao_tipo'] or '', 'cv': float(s['comissao_valor'] or 0)} for s in _servicos()], ensure_ascii=False),
                           comissao_padrao=_comissao_padrao())


@comercial.route('/comercial/orcamentos/<int:oid>/janela')
@permission_required(VER)
def orcamento_janela(oid):
    o = Orcamento.get(oid)
    if not o or not _dono_ou_aprovador(o):
        abort(404)
    o['rot'], o['cls'], o['ico'] = STATUS.get(o['status'], (o['status'], 'tx-perd', 'fa-circle'))
    itens = Orcamento.itens(oid)
    eventos = Orcamento.eventos(oid)
    lanc = FinLancamento.get(o['lancamento_id']) if o.get('lancamento_id') else None
    from utils.cobranca_email import destinatarios_cobranca
    try:
        dest = destinatarios_cobranca(o['cliente_id'], o.get('cliente_email'))
    except Exception:
        dest = []
    from datetime import timedelta
    hoje = date.today()
    datas_venc = [((hoje + timedelta(days=d)).isoformat(), 'em %d dias' % d, (hoje + timedelta(days=d)).strftime('%d/%m'), d == 7) for d in (3, 7, 10, 15, 30)]
    link = url_for('proposta.ver', token=o['token'], _external=True) if not o.get('interno') else None
    texto_wa = _texto_whatsapp(o, itens, link) if link else None
    texto_fin = _texto_financeiro(o, itens, lanc) if (o['status'] == 'COBRANCA' and lanc and lanc['status'] == 'LANCADO') else None
    # boleto cancelado: o lançamento voltou para "aprovado sem cobrança" (22/11/2026 — Anderson: "mostra que cancelou o boleto, e agora?")
    sem_cobranca = bool(lanc and lanc['status'] == 'APROVADO' and not lanc.get('titulo_id') and o['status'] == 'COBRANCA')
    _, bloqueio = _lancamento_vivo(o)
    editavel = o['status'] in Orcamento.EDITAVEIS and not bloqueio and (o['criado_por'] == current_user.id or _aprovador())
    return render_template('comercial/_orcamento_janela.html', texto_fin=texto_fin, sem_cobranca=sem_cobranca, bloqueio=bloqueio, editavel=editavel, o=o, itens=itens, eventos=eventos, lanc=lanc, dest=dest, hoje=hoje, datas_venc=datas_venc, link=link,
                           texto_wa=texto_wa, wa_link=_wa_link(o.get('solicitante_fone'), texto_wa) if texto_wa else None,
                           aprovador=_aprovador(), dono=o['criado_por'] == current_user.id, STATUS=STATUS, COMO=COMO, COMISSAO=COMISSAO,
                           vencido=Orcamento.vencido(o), n_nota=sum(1 for i in itens if i['emite_nota']),
                           total_nota=sum((Decimal(str(i['valor_total'])) for i in itens if i['emite_nota']), Decimal('0')))


def _lancamento_vivo(o):
    """O lançamento deste orçamento que ainda não virou cobrança (LANCADO ou APROVADO sem título): pode ser cancelado junto
    com o orçamento ou ao alterá-lo. Devolve (lancamento, bloqueio): bloqueio = texto quando há cobrança viva (boleto/título)."""
    if not o.get('lancamento_id'):
        return None, None
    l = FinLancamento.get(o['lancamento_id'])
    if not l or l['status'] in ('CANCELADO', 'REPROVADO'):
        return None, None
    if l['status'] == 'FATURADO' or l.get('titulo_id'):
        return l, 'A cobrança deste orçamento já saiu (lançamento nº %s). Cancele o boleto em Cobranças primeiro; o orçamento volta para "Em cobrança" e aí dá para alterar ou cancelar.' % l['id']
    return l, None


def _cancelar_lancamento_junto(o, motivo):
    l, bloqueio = _lancamento_vivo(o)
    if bloqueio:
        raise ValueError(bloqueio)
    if l:
        FinLancamento.cancelar(l['id'], motivo)
        registrar('escrita.cancelou_lancamento', 'financeiro', tabela='fin_lancamentos', registro_id=l['id'],
                  depois={'empresa': l['nome_razao_social'], 'servico': l['servico_nome'], 'motivo': motivo, 'orcamento': o['numero']})
        return l['id']
    return None


@comercial.route('/comercial/orcamentos/<int:oid>/dados')
@permission_required(VER)
def orcamento_dados(oid):
    """O orçamento em JSON para a caixa de edição (qualquer fase antes de a cobrança sair)."""
    o = Orcamento.get(oid)
    if not o or not _dono_ou_aprovador(o):
        abort(404)
    return jsonify({'id': o['id'], 'cliente_id': o['cliente_id'], 'titulo': o['titulo'] or '', 'como': o['como'] or '', 'parcelas': o['parcelas'] or 1,
                    'comissao_tipo': o['comissao_tipo'] or 'PADRAO', 'comissao_valor': float(o['comissao_valor'] or 0),
                    'validade': o['validade'].isoformat() if o['validade'] else '', 'observacao': o['observacao'] or '', 'nota_interna': o['nota_interna'] or '',
                    'solicitante_nome': o.get('solicitante_nome') or '', 'solicitante_email': o.get('solicitante_email') or '', 'solicitante_fone': o.get('solicitante_fone') or '', 'interno': int(o.get('interno') or 0),
                    'itens': [{'servico_id': i['servico_id'], 'descricao': i['descricao'], 'qtd': float(i['quantidade']), 'valor': float(i['valor_unitario'])} for i in Orcamento.itens(oid)]})


@comercial.route('/comercial/orcamentos/contatos/<int:cid>')
@permission_required(VER)
def orcamento_contatos(cid):
    """Contatos do cliente, para escolher o solicitante na caixa (quem pediu o serviço do lado do cliente)."""
    rows = execute_query("""SELECT nome, cargo, email, COALESCE(NULLIF(celular, ''), telefone) AS fone, principal FROM contatos_clientes
                               WHERE cliente_id = %s AND COALESCE(ativo, 1) = 1 ORDER BY principal DESC, nome""", (cid,), fetch=True) or []
    return jsonify([{'nome': r['nome'], 'cargo': r['cargo'] or '', 'email': r['email'] or '', 'fone': r['fone'] or '', 'principal': bool(r['principal'])} for r in rows])


# ───────────────────────── montar e alterar ─────────────────────────
@comercial.route('/comercial/orcamentos/novo', methods=['POST'])
@permission_required(INCLUIR)
def orcamento_novo():
    f = request.form
    oid = f.get('orcamento_id', type=int)
    try:
        if oid:
            o = Orcamento.get(oid)
            if not o or not _dono_ou_aprovador(o):
                abort(404)
            if o['status'] not in Orcamento.EDITAVEIS:
                raise ValueError('Este orçamento não pode mais ser alterado: cancele-o e monte outro.')
            lid = _cancelar_lancamento_junto(o, 'Orçamento %s alterado: o lançamento vai nascer de novo ao mandar para a cobrança' % o['numero'])
            Orcamento.atualizar(oid, f, current_user)
            if lid:
                Orcamento.evento(oid, 'editado', 'Lançamento nº %s cancelado em Financeiro › Lançamentos: ao mandar para a cobrança de novo, nasce outro' % lid, current_user)
            if o.get('aceito_em') or o.get('enviado_em'):
                flash('Orçamento alterado: ele volta para rascunho e, depois de aprovado, precisa ir ao cliente de novo (o aceite anterior era de outro documento).', 'info')
            registrar('escrita.alterou_orcamento', 'comercial', tabela='com_orcamentos', registro_id=oid, depois={'total': str(Orcamento.get(oid)['total'])})
        else:
            oid = Orcamento.criar(f, current_user, EMP)
            registrar('escrita.criou_orcamento', 'comercial', tabela='com_orcamentos', registro_id=oid, depois={'cliente_id': f.get('cliente_id'), 'total': str(Orcamento.get(oid)['total'])})
    except ValueError as e:
        flash(str(e), 'danger')
        return _volta(oid)
    if f.get('acao') == 'aprovacao':
        return _pedir(oid)
    flash('Orçamento %s salvo como rascunho.' % Orcamento.get(oid)['numero'], 'success')
    return _volta(oid)


def _pedir(oid):
    o = Orcamento.get(oid)
    if o['status'] not in ('RASCUNHO', 'REPROVADO'):
        flash('Este orçamento já foi mandado para aprovação.', 'info')
        return _volta(oid)
    if _aprovador():
        # quem aprova e monta ao mesmo tempo: já nasce aprovado (como em Lançamentos, o administrador não pede para si)
        Orcamento.mudar(oid, 'APROVADO', current_user, 'aprovado', 'Aprovado por %s ao montar (quem aprova não precisa pedir)' % current_user.nome,
                        aprovado_por=current_user.id, aprovado_em='NOW()', motivo=None)
        flash(('Orçamento %s aprovado. É controle interno: agora é fazer o serviço e, feito, mandar para a cobrança.' if o.get('interno') else 'Orçamento %s aprovado. Agora é mandar ao cliente aceitar.') % o['numero'], 'success')
    else:
        Orcamento.mudar(oid, 'APROVACAO', current_user, 'aprovacao', 'Mandado para aprovação por %s' % current_user.nome, motivo=None)
        _avisar_aprovadores(o)
        flash('Orçamento %s mandado para aprovação. Você recebe aviso quando for aprovado.' % o['numero'], 'success')
    registrar('escrita.pediu_aprovacao_orcamento', 'comercial', tabela='com_orcamentos', registro_id=oid)
    return _volta(oid)


@comercial.route('/comercial/orcamentos/<int:oid>/pedir-aprovacao', methods=['POST'])
@permission_required(INCLUIR)
def orcamento_pedir(oid):
    o = Orcamento.get(oid)
    if not o or not _dono_ou_aprovador(o):
        abort(404)
    return _pedir(oid)


@comercial.route('/comercial/orcamentos/<int:oid>/aprovar', methods=['POST'])
@permission_required(APROVAR)
def orcamento_aprovar(oid):
    o = Orcamento.get(oid)
    if not o or o['status'] != 'APROVACAO':
        flash('Este orçamento não está aguardando aprovação.', 'info')
        return _volta(oid)
    Orcamento.mudar(oid, 'APROVADO', current_user, 'aprovado', 'Aprovado por %s' % current_user.nome, aprovado_por=current_user.id, aprovado_em='NOW()', motivo=None)
    registrar('escrita.aprovou_orcamento', 'comercial', tabela='com_orcamentos', registro_id=oid, depois={'total': str(o['total'])})
    _avisar_dono(o, 'Orçamento %s aprovado' % o['numero'], 'O orçamento %s (%s, R$ %s) foi aprovado por %s. Agora é mandar ao cliente e, feito o serviço, mandar para a cobrança.'
                 % (o['numero'], o['cliente_nome'], _brl(o['total']), current_user.nome))
    flash('Orçamento %s aprovado.' % o['numero'], 'success')
    return _volta(oid)


@comercial.route('/comercial/orcamentos/<int:oid>/reprovar', methods=['POST'])
@permission_required(APROVAR)
def orcamento_reprovar(oid):
    o = Orcamento.get(oid)
    motivo = ' '.join((request.form.get('motivo') or '').split())[:400]
    if not o or o['status'] != 'APROVACAO':
        flash('Este orçamento não está aguardando aprovação.', 'info')
        return _volta(oid)
    if not motivo:
        flash('Diga o motivo: quem montou precisa saber o que corrigir.', 'danger')
        return _volta(oid)
    Orcamento.mudar(oid, 'REPROVADO', current_user, 'reprovado', 'Reprovado por %s: %s' % (current_user.nome, motivo), motivo=motivo)
    registrar('escrita.reprovou_orcamento', 'comercial', tabela='com_orcamentos', registro_id=oid, depois={'motivo': motivo})
    _avisar_dono(o, 'Orçamento %s voltou para correção' % o['numero'], 'O orçamento %s (%s) foi reprovado por %s. Motivo: %s. Corrija e mande de novo para aprovação.'
                 % (o['numero'], o['cliente_nome'], current_user.nome, motivo))
    flash('Orçamento %s devolvido para correção.' % o['numero'], 'success')
    return _volta(oid)


@comercial.route('/comercial/orcamentos/<int:oid>/cancelar', methods=['POST'])
@permission_required(INCLUIR)
def orcamento_cancelar(oid):
    o = Orcamento.get(oid)
    motivo = ' '.join((request.form.get('motivo') or '').split())[:400]
    if not o or not _dono_ou_aprovador(o):
        abort(404)
    if o['status'] == 'CANCELADO':
        flash('Este orçamento já está cancelado.', 'info')
        return _volta(oid)
    if not motivo:
        flash('Diga o motivo do cancelamento: fica no histórico.', 'danger')
        return _volta(oid)
    try:
        lid = _cancelar_lancamento_junto(o, 'Orçamento %s cancelado: %s' % (o['numero'], motivo))
    except ValueError as e:
        flash(str(e), 'danger')
        return _volta(oid)
    Orcamento.mudar(oid, 'CANCELADO', current_user, 'cancelado', 'Cancelado por %s: %s%s' % (current_user.nome, motivo, (' · lançamento nº %s cancelado junto' % lid) if lid else ''),
                    motivo=motivo, cancelado_em='NOW()')
    registrar('escrita.cancelou_orcamento', 'comercial', tabela='com_orcamentos', registro_id=oid, depois={'motivo': motivo, 'lancamento_cancelado': lid})
    flash('Orçamento %s cancelado.' % o['numero'], 'success')
    return _volta(oid)


@comercial.route('/comercial/orcamentos/<int:oid>/nota', methods=['POST'])
@permission_required(VER)
def orcamento_nota(oid):
    """Anotação interna na linha do tempo (a equipe conversa dentro do orçamento)."""
    o = Orcamento.get(oid)
    texto = ' '.join((request.form.get('texto') or '').split())[:400]
    if not o or not _dono_ou_aprovador(o):
        abort(404)
    if texto:
        Orcamento.evento(oid, 'nota', texto, current_user)
    return _volta(oid)


# ───────────────────────── enviar ao cliente ─────────────────────────
@comercial.route('/comercial/orcamentos/<int:oid>/enviar', methods=['POST'])
@permission_required(INCLUIR)
def orcamento_enviar(oid):
    """Manda o PDF do orçamento por e-mail aos contatos escolhidos e marca "enviado ao cliente"."""
    o = Orcamento.get(oid)
    if not o or not _dono_ou_aprovador(o):
        abort(404)
    if o['status'] not in ('APROVADO', 'ACEITO'):
        flash('Só orçamento aprovado vai para o cliente.', 'info')
        return _volta(oid)
    if o.get('interno'):
        flash('Este orçamento é de controle interno: não vai ao cliente. Altere-o se quiser mandar para aceite.', 'info')
        return _volta(oid)
    paras = [e.strip() for e in request.form.getlist('para') if e and '@' in e]
    extra = (request.form.get('outro') or '').strip()
    if extra and '@' in extra:
        paras.append(extra)
    if not paras:
        flash('Escolha ao menos um e-mail para mandar o orçamento.', 'danger')
        return _volta(oid)
    from utils.email_envio import enviar
    from utils.orcamento_pdf import gerar_pdf
    if not o.get('enviado_em'):                                   # a data do documento congela no 1º envio (impressão digital estável)
        execute_query('UPDATE com_orcamentos SET enviado_em = NOW() WHERE id = %s', (oid,), fetch=False); o = Orcamento.get(oid)
    link = url_for('proposta.ver', token=o['token'], _external=True)
    itens = Orcamento.itens(oid)
    pdf = gerar_pdf(o, itens, link=link, data_doc=o['enviado_em'].date())
    from utils import orcamento_email
    assunto, html, texto = orcamento_email.proposta(o, itens, link)
    ok, erro = False, ''
    try:
        ok, erro = enviar(paras, assunto, html, texto, anexos=[('Orcamento-%s.pdf' % o['numero'], pdf, 'application/pdf')], servico='orcamento', usuario_id=current_user.id)
    except Exception as exc:
        logger.exception('[orcamento] e-mail do orçamento %s', oid); erro = str(exc)
    if not ok:
        flash('O e-mail não saiu (%s). Baixe o PDF e mande por fora, ou tente de novo.' % (erro or 'falha no envio'), 'danger')
        return _volta(oid)
    ja = [e.strip().lower() for e in (o.get('enviado_para') or '').split(',') if e.strip()]
    todos = ja + [p.lower() for p in paras if p.lower() not in ja]
    execute_query('UPDATE com_orcamentos SET enviado_para = %s, pdf_sha256 = %s WHERE id = %s', (','.join(todos)[:600], hashlib.sha256(pdf).hexdigest(), oid), fetch=False)
    Orcamento.evento(oid, 'enviado', 'Mandado ao cliente por e-mail (%s) por %s' % (', '.join(paras), current_user.nome), current_user)
    registrar('escrita.enviou_orcamento', 'comercial', tabela='com_orcamentos', registro_id=oid, depois={'para': paras})
    flash('Orçamento %s mandado para %s.' % (o['numero'], ', '.join(paras)), 'success')
    return _volta(oid)


@comercial.route('/comercial/orcamentos/<int:oid>/whatsapp', methods=['POST'])
@permission_required(INCLUIR)
def orcamento_whatsapp(oid):
    """O texto do WhatsApp foi copiado (ou o WhatsApp aberto): o orçamento conta como mandado ao cliente, o link passa a abrir."""
    o = Orcamento.get(oid)
    if not o or not _dono_ou_aprovador(o):
        abort(404)
    if o.get('interno') or o['status'] not in ('APROVADO', 'ACEITO'):
        return jsonify({'ok': False, 'erro': 'Só orçamento aprovado, e não interno, vai ao cliente.'}), 400
    novo = not o.get('enviado_em')
    if novo:
        execute_query('UPDATE com_orcamentos SET enviado_em = NOW() WHERE id = %s', (oid,), fetch=False); o = Orcamento.get(oid)
        from utils.orcamento_pdf import gerar_pdf
        pdf = gerar_pdf(o, Orcamento.itens(oid), link=url_for('proposta.ver', token=o['token'], _external=True), data_doc=o['enviado_em'].date())
        execute_query('UPDATE com_orcamentos SET pdf_sha256 = %s WHERE id = %s', (hashlib.sha256(pdf).hexdigest(), oid), fetch=False)
    Orcamento.evento(oid, 'enviado', 'Texto do WhatsApp copiado por %s%s' % (current_user.nome, ' (mandado por fora: o link do aceite já abre)' if novo else ''), current_user)
    registrar('escrita.enviou_orcamento', 'comercial', tabela='com_orcamentos', registro_id=oid, depois={'por': 'whatsapp'})
    return jsonify({'ok': True, 'novo': novo})


@comercial.route('/comercial/orcamentos/<int:oid>/pdf')
@permission_required(VER)
def orcamento_pdf(oid):
    o = Orcamento.get(oid)
    if not o or not _dono_ou_aprovador(o):
        abort(404)
    from io import BytesIO
    from utils.orcamento_pdf import gerar_pdf
    link = url_for('proposta.ver', token=o['token'], _external=True) if not o.get('interno') else None
    pdf = gerar_pdf(o, Orcamento.itens(oid), link=link, data_doc=(o['enviado_em'] or o['criado_em']).date() if (o.get('enviado_em') or o.get('aceito_em')) else None)
    return send_file(BytesIO(pdf), mimetype='application/pdf', as_attachment=request.args.get('baixar') == '1',
                     download_name='Orcamento-%s.pdf' % o['numero'])


# ───────────────────────── serviço feito: mandar para a cobrança ─────────────────────────
@comercial.route('/comercial/orcamentos/<int:oid>/cobrar', methods=['POST'])
@permission_required(INCLUIR)
def orcamento_cobrar(oid):
    """Cria o lançamento em Financeiro › Lançamentos (status LANCADO) ligado ao orçamento: o administrador aprova lá e a
    cobrança sai. Um lançamento só, com todos os itens: a nota leva os que entram na nota, o boleto leva todos."""
    o = Orcamento.get(oid)
    f = request.form
    if not o or not _dono_ou_aprovador(o):
        abort(404)
    if o['status'] not in ('APROVADO', 'ACEITO'):
        flash('Só orçamento aprovado vai para a cobrança.', 'info')
        return _volta(oid)
    itens = Orcamento.itens(oid)
    como = f.get('como') if f.get('como') in COMO else (o['como'] or 'BOLETO_NOTA')
    tem_nota = any(i['emite_nota'] for i in itens)
    if como == 'BOLETO_NOTA' and not tem_nota:
        como = 'BOLETO'
    destino = 'MENSAL' if como == 'MENSAL' else 'AVULSO'
    venc = None
    if destino == 'AVULSO':
        try:
            venc = date.fromisoformat(f.get('vencimento') or '')
        except ValueError:
            flash('Escolha o vencimento do boleto.', 'danger')
            return _volta(oid)
    if destino == 'MENSAL':
        from routes.financeiro_lancamentos import _contrato_ativo
        if not _contrato_ativo(o['cliente_id']):
            flash('Esta empresa não tem honorários mensais: não há mensalidade para incluir. Cobre com boleto.', 'danger')
            return _volta(oid)
    principal = next((i for i in itens if i['emite_nota']), itens[0])
    total = sum((Decimal(str(i['valor_total'])) for i in itens), Decimal('0'))
    descricao = ' + '.join(i['descricao'] for i in itens)[:200]
    ct = o['comissao_tipo'] if o['comissao_tipo'] in ('PADRAO', 'PCT', 'FIXO', 'SEM') else 'PADRAO'
    try:
        lid = FinLancamento.criar(EMP, o['cliente_id'], principal['servico_id'], descricao, 1, total, destino, o['criado_por'],
                                  vencimento=venc, observacao=('Orçamento %s' % o['numero']) + ((' · ' + (o['nota_interna'] or ''))[:300] if o['nota_interna'] else ''),
                                  com_nota=None if destino == 'MENSAL' else (como == 'BOLETO_NOTA'),
                                  comissao_tipo=ct, comissao_valor=o['comissao_valor'] if ct in ('PCT', 'FIXO') else None)
    except ValueError as e:
        flash(str(e), 'danger')
        return _volta(oid)
    execute_query('UPDATE fin_lancamentos SET orcamento_id = %s WHERE id = %s', (oid, lid), fetch=False)
    Orcamento.mudar(oid, 'COBRANCA', current_user, 'cobranca', 'Serviço feito: mandado para a cobrança por %s (%s%s). Aguardando aprovação em Financeiro › Lançamentos.'
                    % (current_user.nome, COMO[como], (' · vence %s' % venc.strftime('%d/%m/%Y')) if venc else ''),
                    feito_em='NOW()', lancamento_id=lid, como=como)
    registrar('escrita.orcamento_para_cobranca', 'comercial', tabela='com_orcamentos', registro_id=oid, depois={'lancamento_id': lid, 'como': como, 'total': str(total)})
    flash('Orçamento %s mandado para a cobrança: lançamento nº %s aguardando aprovação em Financeiro › Lançamentos.' % (o['numero'], lid), 'success')
    return _volta(oid)


# ───────────────────────── avisos por e-mail ─────────────────────────
def _emails_de(usuario_ids):
    if not usuario_ids:
        return []
    ids = ','.join(str(int(u)) for u in usuario_ids)
    return [r['email'] for r in execute_query("SELECT email FROM usuarios WHERE id IN (%s) AND email LIKE '%%@%%' AND situacao = 'ATIVO'" % ids, fetch=True) or []]


def _avisar(paras, assunto, texto):
    if not paras:
        return
    try:
        from utils.email_envio import enviar
        enviar(paras, assunto, texto.replace('\n', '<br>'), texto, servico='orcamento', usuario_id=current_user.id)
    except Exception:
        logger.exception('[orcamento] aviso por e-mail')


def _avisar_dono(o, assunto, texto):
    _avisar(_emails_de([o['criado_por']]), assunto, texto)


def _avisar_aprovadores(o):
    rows = execute_query("""SELECT DISTINCT u.email FROM usuarios u WHERE u.email LIKE '%%@%%' AND u.situacao = 'ATIVO'
                              AND (u.tipo_usuario = 'ADMIN' OR u.id IN (
                                     SELECT up.usuario_id FROM usuario_perfis up
                                       JOIN perfis_acesso pa ON pa.id = up.perfil_id AND pa.situacao = 'ATIVO'
                                       JOIN perfil_permissoes pp ON pp.perfil_id = up.perfil_id
                                      WHERE pp.permissao_codigo = %s))""",   # pelo PERFIL (24/11/2026)
                         (APROVAR,), fetch=True) or []
    _avisar([r['email'] for r in rows], 'Orçamento %s para aprovar' % o['numero'],
            '%s mandou o orçamento %s (%s, R$ %s) para aprovação.\n\nAbra Comercial › Orçamentos › Para aprovar.' % (current_user.nome, o['numero'], o['cliente_nome'], _brl(o['total'])))


def _brl(v):
    return '{:,.2f}'.format(Decimal(str(v or 0))).replace(',', 'X').replace('.', ',').replace('X', '.')
