# -*- coding: utf-8 -*-
"""Comercial › PROPOSTAS DE SERVIÇOS (25/11/2026). Anderson: "quero montar uma outra aba como PROPOSTA DE SERVIÇOS... a proposta
que seja feita no COMERCIAL, quando aprovada, o USUARIO DISPARA e acompanha... se aprovado ai o USUARIO CONVERTE para
CONTRATO tudo que precisa do Contrato os dados vai para Proposta é como adiantamento do Contrato".

Caminho: RASCUNHO → (equipe pede) APROVACAO → (escritório) APROVADO → (quem montou dispara) ENVIADO → o cliente ACEITO ou
RECUSADO pelo link → (quem montou) CONVERTIDO: o contrato nasce com todos os dados da proposta. CANCELADO com motivo.

A proposta é um prest_contratos com tipo PROPOSTA; o formulário é o mesmo do contrato (modo proposta) mais o bloco
dados['proposta'] (utils/proposta_servicos). Quem recebe o link são as pessoas do "A/C" (prest_assinantes, papel CLIENTE):
basta UMA aceitar. As rotas internas vivem no blueprint ``comercial``; as do cliente, no blueprint público ``proposta``
(/ps/<token>)."""
import json
import logging
import re
from datetime import date, datetime

from flask import Response, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user

from models.prest_contrato import AREAS, PrestContrato, brl
from routes.comercial_orcamentos import comercial
from routes.proposta_publica import proposta as publico
from routes import financeiro_contratos as FC
from utils import proposta_servicos as PS
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query
from utils.prest_contrato_pdf import sha256
from utils.texto import nome_proprio

logger = logging.getLogger(__name__)
MONTAR = FC.MONTAR
APROVAR = FC.APROVAR
_EVENTO = {'escrita.criou_proposta_servicos': ('', 'fa-pen-ruler', 'Rascunho montado por %s'),
           'escrita.alterou_proposta_servicos': ('', 'fa-pen', 'Rascunho alterado por %s'),
           'escrita.pediu_aprovacao_proposta': ('tj-laranja', 'fa-stamp', '%s mandou para aprovação'),
           'escrita.aprovou_proposta_servicos': ('tj-verde', 'fa-circle-check', 'Aprovada por %s'),
           'escrita.devolveu_proposta_servicos': ('tj-verm', 'fa-rotate-left', 'Devolvida para acertar por %s'),
           'escrita.enviou_proposta_servicos': ('tj-azul', 'fa-paper-plane', '%s disparou para o cliente'),
           'escrita.cancelou_proposta_servicos': ('tj-verm', 'fa-ban', 'Cancelada por %s'),
           'escrita.converteu_proposta_servicos': ('tj-verde', 'fa-file-contract', '%s converteu em contrato')}


# ───────────────────────── apoio ─────────────────────────
def _get(pid):
    p = PrestContrato.get(pid)
    return p if p and p.get('tipo') == 'PROPOSTA' else None


def _salvar_dados(p):
    execute_query('UPDATE prest_contratos SET dados = %s WHERE id = %s', (json.dumps(p['dados'], ensure_ascii=False, default=str), p['id']), fetch=False)


def _ler_proposta(f, dados):
    """Campos próprios da proposta (bloco dados['proposta']) e os destinatários (A/C) como assinantes CLIENTE."""
    base = PS.proposta_padrao(dados, (f.get('prop_segmento') or '').strip())
    vd = f.get('prop_validade', type=int) or 15
    itens = {}
    for k in AREAS:
        itens[k] = PS.linhas(f.get('prop_itens_' + k)) or list(PS.ITENS_PADRAO.get(k) or [])
    ac = [x.strip() for x in re.split(r'[,;\n]| e ', f.get('prop_ac') or '') if x.strip()] or base['ac']
    dados['proposta'] = {'validade_dias': vd if vd in PS.VALIDADES else 15, 'ac': ac[:6], 'itens': itens,
                         'consideracoes': PS.linhas(f.get('prop_consideracoes')), 'extras': PS.linhas(f.get('prop_extras')),
                         'segmento': (f.get('prop_segmento') or '').strip()[:60]}
    dados.setdefault('data_proposta', date.today().isoformat())
    # quem recebe o link: quem assina pela empresa e o "mais alguém" — viram assinantes CLIENTE; os do contrato ficam guardados
    emp = dados['empresas'][0]
    dados['assinantes_contrato'] = dados.get('assinantes') or []
    cli = [{'papel': 'CLIENTE', 'ordem': 1, 'nome': emp.get('rep_nome'), 'cpf': emp.get('rep_cpf'), 'documento': emp.get('rep_documento'), 'email': emp.get('rep_email'),
            'empresa': emp.get('razao'), 'celular': emp.get('rep_celular') or ''}]
    ex = dados.get('assinante_extra')
    if ex and ex.get('nome'):
        cli.append({'papel': 'CLIENTE', 'ordem': 2, 'nome': ex['nome'], 'cpf': ex.get('cpf'), 'documento': ex.get('documento'), 'email': ex.get('email'), 'empresa': emp.get('razao'), 'celular': ex.get('celular') or ''})
    dados['assinantes'] = cli
    return dados


def _eventos(p, dest):
    evs = []
    for lg in execute_query("""SELECT acao, usuario_nome, data_hora, dados_novos FROM logs_sistema
                                WHERE tabela_afetada = 'prest_contratos' AND registro_id = %s AND acao LIKE 'escrita.%%' ORDER BY id""", (p['id'],), fetch=True) or []:
        m = _EVENTO.get(lg['acao'])
        if not m or not lg.get('data_hora'):
            continue
        quem = (lg.get('usuario_nome') or '').split(' (')[0] or 'alguém'
        try:
            dn = json.loads(lg['dados_novos']) if isinstance(lg.get('dados_novos'), str) else (lg.get('dados_novos') or {})
        except Exception:
            dn = {}
        det = ''
        if lg['acao'] in ('escrita.devolveu_proposta_servicos', 'escrita.cancelou_proposta_servicos') and dn.get('motivo'):
            det = '“%s”' % dn['motivo']
        elif lg['acao'] == 'escrita.enviou_proposta_servicos':
            env = dn.get('enviados') or []
            det = ('link por e-mail para ' + ', '.join(n.split(' ')[0].title() for n in env)) if env else 'ninguém recebeu o e-mail'
            if dn.get('falhas'):
                det += ' · falhou: ' + '; '.join(dn['falhas'])
        elif lg['acao'] == 'escrita.converteu_proposta_servicos' and dn.get('contrato_numero'):
            det = 'contrato %s' % dn['contrato_numero']
        evs.append({'cls': m[0], 'ico': m[1], 'texto': m[2] % quem if '%s' in m[2] else m[2], 'det': det, 'quando': lg['data_hora']})
    for a in dest:
        if a.get('assinado_em'):
            evs.append({'cls': 'tj-verde', 'ico': 'fa-file-signature', 'texto': '%s aceitou a proposta' % nome_proprio(a.get('assinado_nome') or a['nome']), 'det': 'IP %s' % (a.get('assinado_ip') or '?'), 'quando': a['assinado_em']})
    rec = p['dados'].get('recusa')
    if rec:
        try:
            evs.append({'cls': 'tj-verm', 'ico': 'fa-thumbs-down', 'texto': '%s recusou a proposta' % nome_proprio(rec.get('nome') or 'O cliente'), 'det': ('“%s”' % rec['motivo']) if rec.get('motivo') else '', 'quando': datetime.fromisoformat(rec['em'])})
        except Exception:
            pass
    for n in (p['dados'].get('notas') or []):
        try:
            q = datetime.fromisoformat(n['em'])
        except Exception:
            continue
        evs.append({'cls': 'tj-azul', 'ico': 'fa-comment', 'texto': n.get('texto') or '', 'det': n.get('nome') or '', 'quando': q})
    if not evs:
        evs.append({'cls': '', 'ico': 'fa-pen-ruler', 'texto': 'Rascunho montado por %s' % (p.get('criado_por_nome') or ''), 'det': '', 'quando': p['criado_em']})
    evs.sort(key=lambda e: e['quando'])
    return evs


def _aceite(p):
    dest = PrestContrato.assinantes(p['id'])
    ok = [a for a in dest if a.get('assinado_em')]
    if not ok:
        return None
    a = ok[0]
    return {'nome': a.get('assinado_nome') or a['nome'], 'cpf': a.get('assinado_cpf') or '', 'em': a['assinado_em'], 'ip': a.get('assinado_ip'), 'hash': a.get('assinado_hash'), 'email': a.get('email')}


def _recusa(p):
    r = p['dados'].get('recusa')
    if not r:
        return None
    try:
        em = datetime.fromisoformat(r['em']).strftime('%d/%m/%Y às %H:%M')
    except Exception:
        em = r.get('em') or ''
    return dict(r, em_txt=em)


def _pdf_bytes(p):
    m = PS.montar(p['dados'])
    sit = {'ACEITO': 'aceita pelo cliente', 'RECUSADO': 'recusada', 'CONVERTIDO': 'convertida em contrato', 'CANCELADO': 'cancelada'}.get(p['status'])
    return PS.gerar_pdf(m, numero=p.get('numero'), aceite=_aceite(p) if p['status'] in ('ACEITO', 'CONVERTIDO') else None,
                        recusa=_recusa(p) if p['status'] == 'RECUSADO' else None, situacao_txt=sit)


def _nome_arquivo(p, sufixo=''):
    return 'Proposta de Serviços Qualicontax x %s%s.pdf' % (FC._parte_do_nome(p), sufixo)


def _texto_whatsapp(a, p, link):
    m = PS.montar(p['dados'])
    quem = (a['nome'] or '').split(' ')[0].title()
    return '\n'.join(['Olá, %s! Aqui é da *Qualicontax*. 😊' % quem, '',
                      'Preparamos a *proposta de serviços* para %s%s.' % ('o ' + m['destino'] if m['rede'] else 'a *' + m['destino'] + '*', (' (%d empresas)' % len(m['empresas'])) if m['rede'] else ''),
                      '📄 Proposta nº *%s* · válida até %s' % (p.get('numero') or p['id'], m['validade_ate'].strftime('%d/%m/%Y')),
                      '💰 Honorários: R$ %s por mês' % m['total_txt'].replace('R$', '').strip(), '',
                      '👉 Para ler a proposta completa e aceitar, é só tocar no link. Lá você pede um código que chega no seu e-mail (%s) e confirma o nome. Leva um minuto.' % (a.get('email') or 'o do cadastro'),
                      link, '', 'Qualquer dúvida é só responder por aqui. Obrigado! 🙏'])


def _email_destinatario(a, p, link):
    m = PS.montar(p['dados'])
    primeiro = nome_proprio((a.get('nome') or '').split()[0]) if (a.get('nome') or '').strip() else ''
    from markupsafe import Markup
    kpis = [('Proposta nº', p.get('numero') or str(p['id'])), ('Honorários mensais', m['total_txt']), ('Válida até', m['validade_ate'].strftime('%d/%m/%Y'))]
    html, d = FC._email_contrato(
        p, eyebrow='Proposta de serviços · nº %s' % (p.get('numero') or p['id']), titulo='Proposta de serviços',
        saudacao=('Olá, %s!' % primeiro) if primeiro else 'Olá!',
        intro=Markup('A Qualicontax preparou a proposta de serviços contábeis para <b>%s</b>. Ela está completa no link abaixo, com o que está incluído em cada área, '
                     'as condições e os honorários. Aceitando, preparamos o contrato para assinatura eletrônica.' % FC._esc_html(m['destino'])),
        kpis=kpis, botao=('Ver a proposta', link),
        passos=[('Abra a proposta.', 'Toque no botão acima e leia; dá para baixar o PDF ali mesmo.'),
                ('Peça o código.', 'Ao tocar em Aceitar, um código de 6 dígitos chega neste e-mail e vale por 30 minutos.'),
                ('Confirme.', 'Digite o código e o seu nome. A Qualicontax recebe o aceite na hora.')],
        rodape='Enviado por Qualicontax Assessoria Contábil. O link é pessoal: não repasse. Em caso de dúvida, responda este e-mail.')
    sem_tags = lambda t: re.sub(r'<[^>]+>', '', str(t))
    texto = '\n'.join([d['eyebrow'], d['titulo'] + ' · ' + d['cliente_nome'], '', d['saudacao'], sem_tags(d['intro']), '', 'Ver a proposta: ' + link, ''] +
                      ['%d. %s %s' % (i + 1, t, x) for i, (t, x) in enumerate(d['passos'])] + ['', d['rodape']])
    return FC._enviar([a['email']], 'Proposta de serviços · %s' % d['cliente_nome'], html, texto)


def _avisar_aprovadores(p):
    try:
        rows = execute_query(
            """SELECT DISTINCT u.email FROM usuarios u WHERE u.email LIKE '%%@%%' AND u.situacao = 'ATIVO' AND (u.tipo_usuario = 'ADMIN'
                  OR u.id IN (SELECT up.usuario_id FROM usuario_perfis up JOIN perfis_acesso pa ON pa.id = up.perfil_id AND pa.situacao = 'ATIVO'
                               JOIN perfil_permissoes pp ON pp.perfil_id = up.perfil_id WHERE pp.permissao_codigo = %s))""", (APROVAR,), fetch=True) or []
        m = PS.montar(p['dados'])
        link = url_for('comercial.proposta_ver', pid=p['id'], _external=True)
        html = ('<p><b>%s</b> montou a proposta de serviços para <b>%s</b> (honorários de %s por mês) e pediu a sua aprovação.</p>'
                '<p><a href="%s" style="background:#15803d;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;">Conferir e aprovar</a></p>'
                % (FC._esc_html(getattr(current_user, 'nome', '')), FC._esc_html(m['destino']), m['total_txt'], link))
        FC._enviar([r['email'] for r in rows], 'Proposta para aprovar – %s' % m['destino'], html,
                   '%s pediu a aprovação da proposta de %s. Abra: %s' % (getattr(current_user, 'nome', ''), m['destino'], link))
    except Exception:
        logger.exception('[propostas] aviso de aprovação')


# ───────────────────────── telas internas ─────────────────────────
@comercial.route('/comercial/propostas')
@permission_required(MONTAR)
def propostas():
    aprov = FC._aprovador()
    aba = request.args.get('aba') or ('APROVACAO' if aprov and execute_query("SELECT 1 FROM prest_contratos WHERE tipo = 'PROPOSTA' AND status = 'APROVACAO' LIMIT 1", fetch=True) else '')
    escopo = None if aprov else FC._escopo()
    lista = PrestContrato.listar(tipo='PROPOSTA')
    if not aprov:
        lista = [r for r in lista if r.get('criado_por') == current_user.id or r['cliente_id'] in escopo or (escopo & set(r.get('empresas_ids') or []))]
    cont = {s: [0, 0.0] for s in PS.ROTULO}
    pessoas = {}
    aceites = {x['contrato_id']: x['em'] for x in execute_query("SELECT a.contrato_id, MIN(a.assinado_em) em FROM prest_assinantes a JOIN prest_contratos p ON p.id = a.contrato_id WHERE p.tipo = 'PROPOSTA' AND a.assinado_em IS NOT NULL GROUP BY a.contrato_id", fetch=True) or []}
    for r in lista:
        r['aceite_em'] = aceites.get(r['id'])
        d = r.get('dados') or {}
        try:
            r['valor'] = float(str(d.get('valor') or '0').replace(',', '.'))
        except ValueError:
            r['valor'] = 0.0
        m = PS.montar(d)
        r['areas'] = [nome_proprio(AREAS[a][0]) for a in (d.get('areas') or []) if a in AREAS]
        r['extras'] = [x for x in (d.get('servicos_extras') or []) if x]
        r['destino'] = m['destino']; r['ac'] = m['ac_txt']; r['validade'] = m['validade_ate']; r['vencida'] = r['status'] in ('ENVIADO', 'APROVADO') and m['validade_ate'] < date.today()
        r['grupo_n'] = int((d.get('grupo') or {}).get('n') or 0)
        r['cancel_motivo'] = (d.get('cancelado') or {}).get('motivo') or ''
        r['recusa_motivo'] = (d.get('recusa') or {}).get('motivo') or ''
        r['contrato'] = d.get('convertido') or {}
        r['cls'], r['ico'] = PS.STATUS_CLS.get(r['status'], ('tx-perd', 'fa-circle'))
        cont.setdefault(r['status'], [0, 0.0]); cont[r['status']][0] += 1; cont[r['status']][1] += r['valor']
        if r.get('criado_por'):
            pessoas[r['criado_por']] = r.get('criado_por_nome') or ''
    pr = PrestContrato.prontas(tipo='PROPOSTA')
    cli = [{'id': s['id'], 'num': str(s['numero_cliente'] or ''), 'nome': s['nome_razao_social'], 'doc': s['cpf_cnpj'] or '', 'fant': s.get('nome_fantasia') or '',
            'cid': s.get('cidade') or '', 'av': bool(s['avulso']), 'mensal': float(s['valor'] or 0) if (aprov or s['id'] in escopo) else 0.0} for s in pr['lista']]
    return render_template('comercial/propostas.html', aba=aba, lista=lista, cont=cont, pessoas=pessoas, ROTULO=PS.ROTULO, aprov=aprov,
                           clientes_json=json.dumps(cli, ensure_ascii=False), n_prontas=len(cli), hoje=date.today())


@comercial.route('/comercial/propostas/novo')
@permission_required(MONTAR)
def proposta_novo():
    cid = request.args.get('cliente_id', type=int)
    if not cid:
        flash('A proposta nasce do cadastro: clique em Nova proposta e escolha a empresa.', 'warning')
        return redirect(url_for('comercial.propostas'))
    mais = []
    for x in (request.args.get('mais') or '').split(','):
        if x.strip().isdigit() and int(x) != cid and int(x) not in mais:
            mais.append(int(x))
    imp = PrestContrato.impedimentos([cid] + mais)
    if imp:
        for k, motivo in imp.items():
            emp = PrestContrato.empresa(k)
            flash('%s não pode receber proposta — %s.' % ((emp or {}).get('nome_razao_social') or 'A empresa', motivo), 'warning')
        return redirect(url_for('comercial.propostas'))
    ctx = FC._form_ctx(cid, request.args.get('fin_contrato_id', type=int), mais=mais)
    seg = ''
    if ctx.get('emp') and ctx['emp'].get('ramos'):
        seg = (ctx['emp']['ramos'][0].get('nome') or '').lower()
    ctx['prop'] = PS.proposta_padrao({'empresas': [dict(ctx['emps_ini'][0], rep_nome=(ctx.get('rep0') or {}).get('rep_nome', ''))] if ctx.get('emps_ini') else []}, seg)
    return render_template('financeiro/contrato_form.html', modo='proposta', PS=PS, **ctx)


@comercial.route('/comercial/propostas/<int:pid>/editar')
@permission_required(MONTAR)
def proposta_editar(pid):
    p = _get(pid)
    FC._so_meu_ou_aprov(p)
    if p['status'] != 'RASCUNHO':
        flash('Só rascunho pode ser alterado.', 'warning')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    ctx = FC._form_ctx(contrato=p)
    ctx['prop'] = p['dados'].get('proposta') or PS.proposta_padrao(p['dados'])
    return render_template('financeiro/contrato_form.html', modo='proposta', PS=PS, **ctx)


@comercial.route('/comercial/propostas/salvar', methods=['POST'])
@permission_required(MONTAR)
def proposta_salvar():
    f = request.form
    pid = f.get('proposta_id', type=int)
    try:
        dados = FC._ler_form(f)
        FC._guardar_contato(dados, f)
        dados = _ler_proposta(f, dados)
        dados['titulo'] = 'PROPOSTA DE SERVIÇOS'
        if pid:
            p = _get(pid); FC._so_meu_ou_aprov(p)
            dados['data_proposta'] = p['dados'].get('data_proposta') or dados['data_proposta']
            PrestContrato.atualizar(pid, dados)
            registrar('escrita.alterou_proposta_servicos', 'comercial', tabela='prest_contratos', registro_id=pid, depois={'empresa': dados['empresas'][0]['razao']})
        else:
            pid = PrestContrato.criar(dados['empresas'][0]['cliente_id'], dados, current_user.id, tipo='PROPOSTA')
            registrar('escrita.criou_proposta_servicos', 'comercial', tabela='prest_contratos', registro_id=pid,
                      depois={'empresa': dados['empresas'][0]['razao'], 'valor': dados['valor'], 'areas': dados['areas']})
        flash('Rascunho salvo. Confira o PDF e %s.' % ('dispare para o cliente' if FC._aprovador() else 'mande para aprovação'), 'success')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(request.referrer or url_for('comercial.propostas'))


@comercial.route('/comercial/propostas/<int:pid>')
@permission_required(MONTAR)
def proposta_ver(pid):
    p = _get(pid)
    if not FC._ve(p):
        abort(404)
    dest = PrestContrato.assinantes(pid)
    for a in dest:
        a['link'] = url_for('proposta.ps_ver', token=a['token'], _external=True)
        a['fone'] = (a.get('celular') or '')
        if not a['fone']:
            a['fone'] = FC._fone_do_assinante(dict(a, papel='CONTRATANTE'), p)
        if p['status'] == 'ENVIADO' and not a['assinado_em']:
            a['texto_wa'] = _texto_whatsapp(a, p, a['link'])
            a['wa_link'] = FC._wa_link(a['fone'], a['texto_wa'])
    cls, ico = PS.STATUS_CLS.get(p['status'], ('tx-perd', 'fa-circle'))
    m = PS.montar(p['dados'])
    contrato = None
    if (p['dados'].get('convertido') or {}).get('contrato_id'):
        contrato = PrestContrato.get(p['dados']['convertido']['contrato_id'])
    return render_template('comercial/proposta_ver.html', p=p, dest=dest, ROTULO=PS.ROTULO, aprov=FC._aprovador(), cls=cls, ico=ico, m=m,
                           eventos=_eventos(p, dest), aceite=_aceite(p), recusa=_recusa(p), contrato=contrato,
                           meu=p.get('criado_por') == current_user.id, hoje=date.today(), AREAS=AREAS, brl=brl, vencida=m['validade_ate'] < date.today())


@comercial.route('/comercial/propostas/<int:pid>/pdf')
@permission_required(MONTAR)
def proposta_pdf(pid):
    p = _get(pid)
    if not FC._ve(p):
        abort(404)
    return Response(_pdf_bytes(p), mimetype='application/pdf', headers={'Content-Disposition': FC._disposition(_nome_arquivo(p), inline=not request.args.get('baixar'))})


@comercial.route('/comercial/propostas/<int:pid>/nota', methods=['POST'])
@permission_required(MONTAR)
def proposta_nota(pid):
    p = _get(pid)
    if not FC._ve(p):
        abort(404)
    texto = (request.form.get('texto') or '').strip()[:400]
    if texto:
        p['dados'].setdefault('notas', []).append({'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': FC._agora().isoformat(timespec='seconds'), 'texto': texto})
        _salvar_dados(p)
    return redirect(url_for('comercial.proposta_ver', pid=pid))


@comercial.route('/comercial/propostas/<int:pid>/pedir-aprovacao', methods=['POST'])
@permission_required(MONTAR)
def proposta_pedir_aprovacao(pid):
    p = _get(pid)
    FC._so_meu_ou_aprov(p)
    if p['status'] != 'RASCUNHO':
        flash('Só rascunho vai para aprovação.', 'warning')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    p['dados'].pop('reprovado', None)
    p['dados']['pedido_aprovacao'] = {'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': FC._agora().isoformat(timespec='minutes')}
    _salvar_dados(p)
    PrestContrato.mudar_status(pid, 'APROVACAO')
    registrar('escrita.pediu_aprovacao_proposta', 'comercial', tabela='prest_contratos', registro_id=pid, depois={'empresa': p['dados']['empresas'][0]['razao'], 'valor': p['dados'].get('valor')})
    _avisar_aprovadores(PrestContrato.get(pid))
    flash('Enviada para aprovação. Aprovada, você recebe aviso e dispara para o cliente.', 'success')
    return redirect(url_for('comercial.proposta_ver', pid=pid))


@comercial.route('/comercial/propostas/<int:pid>/aprovar', methods=['POST'])
@permission_required(APROVAR)
def proposta_aprovar(pid):
    p = _get(pid)
    if not p or p['status'] not in ('APROVACAO', 'RASCUNHO'):
        flash('Só proposta em rascunho ou aguardando aprovação pode ser aprovada.', 'warning')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    p['dados']['aprovacao'] = {'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': FC._agora().isoformat(timespec='minutes')}
    if (p['dados'].get('grupo') or {}).get('autorizacao_necessaria'):
        p['dados']['autorizacao'] = {'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': FC._agora().isoformat(timespec='minutes'),
                                     'texto': 'Proposta em grupo com quadro de sócios diferente autorizada pelo escritório na aprovação'}
    _salvar_dados(p)
    PrestContrato.mudar_status(pid, 'APROVADO')
    registrar('escrita.aprovou_proposta_servicos', 'comercial', tabela='prest_contratos', registro_id=pid, depois={'empresa': p['dados']['empresas'][0]['razao'], 'valor': p['dados'].get('valor')})
    flash('Proposta aprovada. Agora é disparar para o cliente.', 'success')
    return redirect(url_for('comercial.proposta_ver', pid=pid))


@comercial.route('/comercial/propostas/<int:pid>/reprovar', methods=['POST'])
@permission_required(APROVAR)
def proposta_reprovar(pid):
    p = _get(pid)
    if not p or p['status'] != 'APROVACAO':
        flash('Só proposta aguardando aprovação pode ser devolvida.', 'warning')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    motivo = (request.form.get('motivo') or '').strip()[:500]
    p['dados']['reprovado'] = {'por': getattr(current_user, 'nome', ''), 'em': FC._agora().isoformat(timespec='minutes'), 'motivo': motivo}
    _salvar_dados(p)
    PrestContrato.mudar_status(pid, 'RASCUNHO')
    registrar('escrita.devolveu_proposta_servicos', 'comercial', tabela='prest_contratos', registro_id=pid, depois={'motivo': motivo})
    flash('Devolvida para quem montou, com o motivo.', 'info')
    return redirect(url_for('comercial.propostas'))


@comercial.route('/comercial/propostas/<int:pid>/enviar', methods=['POST'])
@permission_required(MONTAR)
def proposta_enviar(pid):
    """Disparar: e-mail com o link para cada pessoa do A/C; o PDF congela (sha256). Reenvio vale para quem ainda não aceitou."""
    p = _get(pid)
    FC._so_meu_ou_aprov(p)
    if not p or p['status'] not in ('RASCUNHO', 'APROVADO', 'ENVIADO') or (p['status'] == 'RASCUNHO' and not FC._aprovador()):
        flash('Esta proposta não pode ser disparada%s.' % (' antes de aprovada' if p and p['status'] in ('RASCUNHO', 'APROVACAO') else ''), 'warning')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    dest = PrestContrato.assinantes(pid)
    sem = [a['nome'] for a in dest if not a.get('email')]
    if sem:
        flash('Sem e-mail: %s. Quem recebe a proposta precisa de e-mail.' % ', '.join(sem), 'danger')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    if p['status'] in ('RASCUNHO', 'APROVADO'):
        p['dados']['data_proposta'] = date.today().isoformat()        # a data e a validade contam do disparo
        _salvar_dados(p)
        p = PrestContrato.get(pid)
        PrestContrato.mudar_status(pid, 'ENVIADO', pdf_sha256=sha256(_pdf_bytes(p)))
        p = PrestContrato.get(pid)
    enviados, falhas = [], []
    for a in dest:
        if a.get('assinado_em'):
            continue
        ok, erro = _email_destinatario(a, p, url_for('proposta.ps_ver', token=a['token'], _external=True))
        if ok:
            execute_query('UPDATE prest_assinantes SET aviso_enviado_em = NOW() WHERE id = %s', (a['id'],), fetch=False); enviados.append(a['nome'])
        else:
            falhas.append('%s (%s)' % (a['nome'], erro))
    registrar('escrita.enviou_proposta_servicos', 'comercial', tabela='prest_contratos', registro_id=pid, depois={'enviados': enviados, 'falhas': falhas, 'sha256': p.get('pdf_sha256')})
    flash('Proposta disparada: %s.%s' % (', '.join(enviados) or 'ninguém', (' Falhou: ' + '; '.join(falhas)) if falhas else ''), 'success' if not falhas else 'warning')
    return redirect(url_for('comercial.proposta_ver', pid=pid))


@comercial.route('/comercial/propostas/<int:pid>/cancelar', methods=['POST'])
@permission_required(MONTAR)
def proposta_cancelar(pid):
    p = _get(pid)
    FC._so_meu_ou_aprov(p)
    if p['status'] in ('APROVADO', 'ENVIADO', 'ACEITO') and not FC._aprovador():
        flash('Proposta já aprovada: só quem aprova pode cancelar.', 'warning')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    if p['status'] in ('CONVERTIDO', 'CANCELADO'):
        flash('Esta proposta não se cancela mais.', 'warning')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    motivo = (request.form.get('motivo') or '').strip()[:400]
    if not motivo:
        flash('Diga o motivo do cancelamento.', 'warning')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    p['dados']['cancelado'] = {'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': FC._agora().isoformat(timespec='minutes'), 'motivo': motivo}
    _salvar_dados(p)
    PrestContrato.mudar_status(pid, 'CANCELADO')
    registrar('escrita.cancelou_proposta_servicos', 'comercial', tabela='prest_contratos', registro_id=pid, antes={'status': p['status']}, depois={'status': 'CANCELADO', 'motivo': motivo})
    flash('Proposta cancelada, com o motivo registrado.', 'info')
    return redirect(url_for('comercial.proposta_ver', pid=pid))


@comercial.route('/comercial/propostas/<int:pid>/converter', methods=['POST'])
@permission_required(MONTAR)
def proposta_converter(pid):
    """Converter: abre o Montar contrato já preenchido com tudo da proposta; ao salvar, o contrato nasce ligado (anterior_id)."""
    p = _get(pid)
    FC._so_meu_ou_aprov(p)
    if p['status'] not in ('ACEITO', 'APROVADO', 'ENVIADO'):
        flash('Só proposta aceita (ou aprovada) vira contrato.', 'warning')
        return redirect(url_for('comercial.proposta_ver', pid=pid))
    return redirect(url_for('financeiro.contrato_novo', proposta_id=pid))


def marcar_convertida(pid, contrato):
    """Chamada por contrato_salvar quando o contrato nasce de uma proposta."""
    p = _get(pid)
    if not p:
        return
    p['dados']['convertido'] = {'contrato_id': contrato['id'], 'contrato_numero': contrato.get('numero'), 'por': current_user.id,
                                'nome': getattr(current_user, 'nome', ''), 'em': FC._agora().isoformat(timespec='minutes')}
    _salvar_dados(p)
    PrestContrato.mudar_status(pid, 'CONVERTIDO')
    registrar('escrita.converteu_proposta_servicos', 'comercial', tabela='prest_contratos', registro_id=pid,
              depois={'contrato_id': contrato['id'], 'contrato_numero': contrato.get('numero')})


# ───────────────────────── página do cliente (/ps/<token>) ─────────────────────────
def _dest(token):
    a = PrestContrato.assinante_por_token(token)
    if not a:
        return None, None, 'Este link não existe.'
    p = _get(a['contrato_id'])
    if not p:
        return None, None, 'Este link não existe.'
    if p['status'] == 'CANCELADO':
        return a, p, 'Esta proposta foi cancelada pela Qualicontax.'
    if p['status'] in ('RASCUNHO', 'APROVACAO', 'APROVADO'):
        return a, p, 'Esta proposta ainda não foi enviada.'
    return a, p, None


@publico.route('/ps/<token>')
def ps_ver(token):
    a, p, erro = _dest(token)
    if erro and not a:
        return render_template('proposta_servicos.html', erro=erro, qc=PrestContrato.contratada()), 404
    m = PS.montar(p['dados'])
    desafio = FC._desafio(a) if not erro else None
    cod_ok = bool(a.get('codigo_enviado_em')) and not a.get('assinado_em') and (not desafio or bool(session.get('ac_%s' % a['id'])))
    vencida = p['status'] == 'ENVIADO' and m['validade_ate'] < date.today()
    return render_template('proposta_servicos.html', qc=PrestContrato.contratada(), a=a, p=p, m=m, erro=erro, aceite=_aceite(p), recusa=_recusa(p),
                           codigo_pedido=cod_ok, desafio=desafio, email_mascarado=FC._mascara(a.get('email')), vencida=vencida, brl=brl)


@publico.route('/ps/<token>/pdf')
def ps_pdf(token):
    a, p, erro = _dest(token)
    if not a:
        abort(404)
    return Response(_pdf_bytes(p), mimetype='application/pdf', headers={'Content-Disposition': FC._disposition(_nome_arquivo(p))})


@publico.route('/ps/<token>/codigo', methods=['POST'])
def ps_codigo(token):
    a, p, erro = _dest(token)
    if not a or erro or a['assinado_em'] or p['status'] != 'ENVIADO':
        return jsonify({'ok': False, 'erro': erro or 'Esta proposta já foi respondida.'}), 400
    s = (execute_query('SELECT TIMESTAMPDIFF(SECOND, codigo_enviado_em, NOW()) AS s FROM prest_assinantes WHERE id = %s', (a['id'],), fetch=True, fetch_one=True) or {}).get('s')
    dados = request.get_json(silent=True) or {}
    if FC._desafio(a):
        cpf = FC._confere_cpf(a, dados.get('cpf_parte') or request.form.get('cpf_parte'))
        if not cpf:
            return jsonify({'ok': False, 'erro': 'Os dígitos não conferem com o CPF de quem recebe a proposta. Confira e tente de novo, ou fale com a Qualicontax.'}), 400
        session['ac_%s' % a['id']] = cpf
    if s is not None and s < 60:
        return jsonify({'ok': False, 'erro': 'Código enviado há menos de um minuto. Confira a caixa de entrada e o spam.'}), 429
    codigo = PrestContrato.gerar_codigo(a['id'])
    primeiro = nome_proprio((a.get('nome') or '').split()[0]) if (a.get('nome') or '').strip() else ''
    html, d = FC._email_contrato(p, eyebrow='Proposta de serviços · nº %s' % (p.get('numero') or p['id']), titulo='Proposta de serviços',
                                 saudacao=('Olá, %s!' % primeiro) if primeiro else 'Olá!', codigo=codigo,
                                 intro='Aqui está o seu código para aceitar a proposta. Volte à página da proposta e digite os 6 dígitos.',
                                 rodape='Enviado por Qualicontax Assessoria Contábil. O código vale por 30 minutos e só serve para você. Se não foi você que pediu, ignore este e-mail.')
    ok, err = FC._enviar([a['email']], 'Seu código para aceitar a proposta: %s' % codigo, html, 'Seu código para aceitar a proposta nº %s: %s (vale 30 minutos).' % (p.get('numero') or p['id'], codigo))
    if not ok:
        return jsonify({'ok': False, 'erro': 'Não consegui enviar o e-mail: %s' % err}), 500
    return jsonify({'ok': True, 'email': FC._mascara(a['email'])})


@publico.route('/ps/<token>/aceitar', methods=['POST'])
def ps_aceitar(token):
    a, p, erro = _dest(token)
    if not a or erro:
        flash(erro or 'Link inválido.', 'danger')
        return redirect(url_for('proposta.ps_ver', token=token))
    if a['assinado_em'] or p['status'] != 'ENVIADO':
        return redirect(url_for('proposta.ps_ver', token=token))
    f = request.form
    nome, codigo = (f.get('nome') or '').strip(), (f.get('codigo') or '').strip()
    cpf = session.get('ac_%s' % a['id']) or ''
    if cpf:
        cpf = '%s.%s.%s-%s' % (cpf[:3], cpf[3:6], cpf[6:9], cpf[9:])
    elif FC._desafio(a):
        flash('Confirme primeiro os dígitos do CPF e peça o código.', 'danger')
        return redirect(url_for('proposta.ps_ver', token=token))
    if not f.get('aceito') or not nome or len(nome.split()) < 2:
        flash('Digite seu nome completo e marque "li e aceito".', 'danger')
        return redirect(url_for('proposta.ps_ver', token=token))
    if not PrestContrato.conferir_codigo(a, codigo):
        flash('Código errado ou vencido. Peça outro código.', 'danger')
        return redirect(url_for('proposta.ps_ver', token=token))
    pdf_hash = sha256(_pdf_bytes(p))
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
    PrestContrato.registrar_assinatura(a['id'], nome, cpf, ip, request.headers.get('User-Agent', ''), pdf_hash)
    PrestContrato.mudar_status(p['id'], 'ACEITO')
    registrar('escrita.aceitou_proposta_servicos', 'comercial', tabela='prest_assinantes', registro_id=a['id'],
              depois={'proposta_id': p['id'], 'nome': nome, 'cpf': cpf, 'ip': ip, 'sha256': pdf_hash})
    session.pop('ac_%s' % a['id'], None); session.pop('ad_%s' % a['id'], None)
    _avisar_aceite(PrestContrato.get(p['id']), nome)
    flash('Proposta aceita. Obrigado! A Qualicontax já recebeu e vai preparar o contrato.', 'success')
    return redirect(url_for('proposta.ps_ver', token=token))


@publico.route('/ps/<token>/recusar', methods=['POST'])
def ps_recusar(token):
    a, p, erro = _dest(token)
    if not a or erro or a['assinado_em'] or p['status'] != 'ENVIADO':
        return redirect(url_for('proposta.ps_ver', token=token))
    motivo = (request.form.get('motivo') or '').strip()[:400]
    nome = (request.form.get('nome') or a.get('nome') or '').strip()
    p['dados']['recusa'] = {'nome': nome, 'motivo': motivo, 'em': FC._agora().isoformat(timespec='minutes'), 'destinatario_id': a['id']}
    _salvar_dados(p)
    PrestContrato.mudar_status(p['id'], 'RECUSADO')
    registrar('escrita.recusou_proposta_servicos', 'comercial', tabela='prest_contratos', registro_id=p['id'], depois={'nome': nome, 'motivo': motivo})
    _avisar_aceite(PrestContrato.get(p['id']), nome, recusou=True, motivo=motivo)
    flash('Resposta registrada. Obrigado pelo retorno.', 'info')
    return redirect(url_for('proposta.ps_ver', token=token))


def _avisar_aceite(p, nome, recusou=False, motivo=''):
    """Quem montou (e quem aprova) fica sabendo na hora que o cliente respondeu."""
    try:
        m = PS.montar(p['dados'])
        rows = execute_query('SELECT email FROM usuarios WHERE id = %s AND email LIKE %s', (p.get('criado_por'), '%@%'), fetch=True) or []
        para = [r['email'] for r in rows]
        if not para:
            return
        link = url_for('comercial.proposta_ver', pid=p['id'], _external=True)
        if recusou:
            html = '<p><b>%s</b> recusou a proposta nº %s (%s)%s.</p><p><a href="%s">Abrir a proposta</a></p>' % (FC._esc_html(nome), p.get('numero'), FC._esc_html(m['destino']), (': "%s"' % FC._esc_html(motivo)) if motivo else '', link)
            FC._enviar(para, 'Proposta recusada – %s' % m['destino'], html, '%s recusou a proposta nº %s. %s' % (nome, p.get('numero'), link))
        else:
            html = ('<p><b>%s</b> aceitou a proposta nº %s (%s, honorários de %s por mês).</p>'
                    '<p><a href="%s" style="background:#15803d;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;">Converter em contrato</a></p>'
                    % (FC._esc_html(nome), p.get('numero'), FC._esc_html(m['destino']), m['total_txt'], link))
            FC._enviar(para, 'Proposta aceita – %s' % m['destino'], html, '%s aceitou a proposta nº %s. Converta em contrato: %s' % (nome, p.get('numero'), link))
    except Exception:
        logger.exception('[propostas] aviso de aceite')
