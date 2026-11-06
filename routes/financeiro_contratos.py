# -*- coding: utf-8 -*-
"""Contrato de prestação de serviços: montar do cadastro, enviar para assinatura, assinar pelo link (06/11/2026).

Telas internas no blueprint ``financeiro``, SÓ ADMIN (Anderson, 06/11: "pro ADMIN aparece todos, para os demais não
aparece nada"). Nunca se digita um contrato: ele nasce de um cadastro COMPLETO de Cliente ou de Avulso; quem está em
A CADASTRAR não entra na lista nem passa no salvar. A assinatura é PÚBLICA (blueprint
``assinatura``): o token de 32 bytes na URL identifica o signatário; a identidade é confirmada por código de 6 dígitos
enviado ao e-mail dele. Ninguém assina sem e-mail.
"""
import logging
from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from models.fin_contrato import FinContrato
from models.prest_contrato import (AREAS, QUALICONTAX, SIGNATARIOS_QUALICONTAX, PrestContrato, brl)
from routes.financeiro import financeiro
from utils import email_envio
from utils.atividade import registrar
from utils.auth_helper import admin_required
from utils.formatters import format_cnpj, format_cpf
from utils.db_helper import execute_query
from utils.prest_contrato_pdf import gerar_pdf, sha256

logger = logging.getLogger(__name__)
assinatura = Blueprint('assinatura', __name__)


def _remetente():
    """Os e-mails do contrato saem de financeiro@qualicontax.com.br (Anderson, 06/11): a caixa da conta de cobrança
    padrão da Qualicontax. Sem ela configurada, cai na caixa do Railway (app@)."""
    try:
        from models.fin_cobranca import FinCobrancaConta
        conta = FinCobrancaConta.padrao_da_empresa(2)
        return FinCobrancaConta.remetente_email(conta['id']) if conta else None
    except Exception as e:
        logger.warning('[contratos] sem caixa financeiro@: %s', e)
        return None


def _enviar(para, assunto, html, texto, anexos=None):
    return email_envio.enviar(para, assunto, html, texto, remetente=_remetente(), anexos=anexos)


# ---------------------------------------------------------------- montagem do formulário
def _data(raw):
    s = (raw or '').strip()
    try:
        return datetime.strptime(s, '%Y-%m-%d').date() if s else None
    except ValueError:
        return None


def _dec_str(raw):
    s = (str(raw or '')).strip()
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    try:
        return '%.2f' % float(s)
    except ValueError:
        return '0.00'


def _ler_form(f):
    """Formulário → ``dados`` (JSON guardado no contrato). Levanta ValueError com a mensagem para a tela."""
    empresas = []
    for i, cid in enumerate(f.getlist('emp_cliente_id')):
        if not (cid or '').isdigit():
            continue
        e = PrestContrato.empresa(int(cid))
        if not e:
            continue
        empresas.append({'cliente_id': int(cid), 'razao': e['nome_razao_social'], 'cnpj': e['cpf_cnpj'], 'numero': e['numero_cliente'],
                         'endereco': (f.getlist('emp_endereco')[i] if i < len(f.getlist('emp_endereco')) else '') or e['endereco'],
                         'valor': _dec_str(f.getlist('emp_valor')[i]) if i < len(f.getlist('emp_valor')) else None})
    if not empresas:
        raise ValueError('Escolha ao menos uma empresa.')
    imp = PrestContrato.impedimentos([e['cliente_id'] for e in empresas])
    if imp:                                               # nunca contrato digitado: só cadastro completo
        raise ValueError('; '.join('%s: %s' % (e['razao'], imp[e['cliente_id']]) for e in empresas if e['cliente_id'] in imp)
                         + '. Complete o cadastro em A CADASTRAR antes de montar o contrato.')
    rep = {'rep_nome': (f.get('rep_nome') or '').strip(), 'rep_cpf': (f.get('rep_cpf') or '').strip(), 'rep_documento': (f.get('rep_documento') or '').strip(),
           'rep_cargo': (f.get('rep_cargo') or 'sócio administrador').strip(), 'rep_email': (f.get('rep_email') or '').strip().lower(),
           'rep_celular': (f.get('rep_celular') or '').strip()}
    if not rep['rep_nome'] or not rep['rep_email']:
        raise ValueError('Informe quem assina pela empresa e o e-mail dele (a assinatura vai por e-mail).')
    empresas[0].update(rep)
    areas = [a for a in f.getlist('areas') if a in AREAS]
    extras = [s.strip() for s in f.getlist('servico_extra') if (s or '').strip()]
    if not areas and not extras:
        raise ValueError('Marque ao menos um serviço.')
    valor = _dec_str(f.get('valor'))
    if float(valor) <= 0:
        raise ValueError('Informe o valor mensal dos honorários.')
    sign = PrestContrato.signatarios_qualicontax()
    assina_q = str(f.get('assina_qualicontax') or '')
    if assina_q not in sign:
        if not sign:
            raise ValueError('A Qualicontax não tem sócios no cadastro (cliente nº 1): cadastre quem assina por ela.')
        assina_q = next(iter(sign))
    q = dict(sign[assina_q])
    if not q.get('email'):
        raise ValueError('%s não tem e-mail no cadastro: a assinatura vai por e-mail.' % q['nome'])
    if q.get('docs_faltam'):
        raise ValueError('%s não tem documento (RG/CNH) marcado para contrato no cadastro PF.' % q['nome'])
    contratada = PrestContrato.contratada()
    assinantes = [{'papel': 'CONTRATANTE', 'ordem': 1, 'nome': rep['rep_nome'], 'cpf': rep['rep_cpf'], 'documento': rep['rep_documento'],
                   'email': rep['rep_email'], 'empresa': empresas[0]['razao'] if len(empresas) == 1 else (f.get('apelido') or '').strip().upper() or empresas[0]['razao']},
                  {'papel': 'CONTRATADA', 'ordem': 1, 'nome': q['nome'], 'cpf': q['cpf'], 'documento': q['documento'], 'email': q['email'], 'usuario_id': q['usuario_id']}]
    for n, uid in enumerate(f.getlist('testemunha_id'), 1):
        if not (uid or '').isdigit():
            continue
        u = execute_query('SELECT id, nome, cpf, email FROM usuarios WHERE id = %s', (int(uid),), fetch=True, fetch_one=True)
        if not u:
            continue
        if not u.get('email'):
            raise ValueError('A testemunha %s não tem e-mail no cadastro de usuários.' % u['nome'])
        cpf = u.get('cpf') or ''
        cpf = '%s.%s.%s-%s' % (cpf[:3], cpf[3:6], cpf[6:9], cpf[9:]) if len(cpf) == 11 else cpf
        assinantes.append({'papel': 'TESTEMUNHA', 'ordem': n, 'nome': u['nome'], 'cpf': cpf, 'email': u['email'], 'usuario_id': u['id']})
    dados = {
        'empresas': empresas, 'apelido': (f.get('apelido') or '').strip().upper() or None, 'areas': areas, 'servicos_extras': extras,
        'valor': valor, 'valores_por_empresa': bool(f.get('valores_por_empresa')), 'dia_vencimento': int(f.get('dia_vencimento') or 10),
        'reajuste_tipo': f.get('reajuste_tipo') or 'SALARIO_MINIMO', 'reajuste_indice': (f.get('reajuste_indice') or '').strip() or None,
        'reajuste_mes': (f.get('reajuste_mes') or '').strip() or None,
        'decimo': f.get('decimo') if f.get('decimo') in ('COBRADO', 'UNICA', 'ISENTO') else 'COBRADO',
        'multa': (f.get('multa') or '10').strip(), 'juros': (f.get('juros') or '1').strip(), 'aviso_previo': int(f.get('aviso_previo') or 30),
        'foro': (f.get('foro') or 'São Paulo/SP').strip(), 'local': (f.get('local') or 'São Paulo').strip(),
        'inicio': (f.get('inicio') or date.today().isoformat()), 'lgpd': bool(f.get('lgpd')), 'assina_qualicontax': assina_q,
        'assina': q, 'contratada': contratada,            # congelados: o contrato diz o que o cadastro dizia na hora
        'assinantes': assinantes, 'titulo': (f.get('titulo') or '').strip() or None,
    }
    return dados


def _guardar_contato(dados, f):
    """Quem assina é um terceiro? Com a caixa marcada, a pessoa entra nos CONTATOS da empresa (Anderson, 06/11: "ter
    opção para colocar o nome e os dados no cadastro da empresa para puxar de lá, nos contatos")."""
    if not f.get('guardar_contato'):
        return
    e = dados['empresas'][0]
    ja = execute_query('SELECT id FROM contatos_clientes WHERE cliente_id = %s AND ativo = 1 AND LOWER(email) = %s', (e['cliente_id'], e['rep_email']), fetch=True)
    if ja:
        return
    from models.contato_cliente import ContatoCliente
    try:
        ContatoCliente.create(e['cliente_id'], e['rep_nome'], cargo=(e.get('rep_cargo') or 'Representante')[:60], email=e['rep_email'],
                             celular=(f.get('rep_celular') or '').strip() or None)
        registrar('escrita.criou_contato_pelo_contrato', 'clientes', tabela='contatos_clientes', registro_id=e['cliente_id'],
                  depois={'nome': e['rep_nome'], 'email': e['rep_email'], 'empresa': e['razao']})
    except Exception as ex:                                   # contato é cortesia: não derruba o contrato
        logger.warning('[contratos] não guardou o contato: %s', ex)


def _form_ctx(cliente_id=None, fin_contrato_id=None, contrato=None):
    """O que a tela do formulário precisa: empresa(s), sócios, honorários, testemunhas, signatários da Qualicontax."""
    d = (contrato or {}).get('dados') or {}
    emp = None
    if d.get('empresas'):
        emp = PrestContrato.empresa(d['empresas'][0]['cliente_id'])
    elif cliente_id:
        emp = PrestContrato.empresa(cliente_id)
    fin = None
    if fin_contrato_id:
        fin = FinContrato.get(fin_contrato_id)
    elif emp and not contrato:
        fin = (FinContrato.por_cliente(emp['id']) or [None])[0]
    if fin:
        fin['itens'] = FinContrato.itens(fin['id'])
    return {'emp': emp, 'fin': fin, 'dados': d, 'contrato': contrato, 'AREAS': AREAS, 'SIGN_Q': PrestContrato.signatarios_qualicontax(),
            'contratada': PrestContrato.contratada(),
            'testemunhas': PrestContrato.testemunhas_disponiveis(), 'hoje': date.today()}


# ---------------------------------------------------------------- telas internas
@financeiro.route('/financeiro/contratos')
@admin_required
def contratos():
    aba = request.args.get('aba') or 'todos'
    lista = PrestContrato.listar(status=None if aba == 'todos' else aba.upper())
    res = {s: 0 for s in ('RASCUNHO', 'ENVIADO', 'ASSINADO', 'CANCELADO')}
    for r in PrestContrato.listar():
        res[r['status']] = res.get(r['status'], 0) + 1
    pr = PrestContrato.prontas()
    for s in pr['lista']:
        d = ''.join(ch for ch in (s['cpf_cnpj'] or '') if ch.isdigit())
        s['doc'] = format_cnpj(d) if len(d) == 14 else (format_cpf(d) if len(d) == 11 else (s['cpf_cnpj'] or ''))
    grupos = sorted({s['grupo'] for s in pr['lista'] if s['grupo']})
    return render_template('financeiro/contratos.html', aba=aba, lista=lista, res=res, prontas=pr['lista'], grupos=grupos,
                           pend_empresas=pr['pendentes_empresas'], pend_avulsos=pr['pendentes_avulsos'], hoje=date.today())


@financeiro.route('/financeiro/contratos/novo')
@admin_required
def contrato_novo():
    """Só a partir de um cadastro completo: sem cliente_id, ou com pendência em A CADASTRAR, volta para a lista."""
    cid = request.args.get('cliente_id', type=int)
    if not cid:
        flash('O contrato nasce do cadastro: escolha a empresa na lista "Prontas para contrato". Nada é digitado do zero.', 'warning')
        return redirect(url_for('financeiro.contratos') + '#pc-prontas')
    imp = PrestContrato.impedimentos([cid])
    if cid in imp:
        emp = PrestContrato.empresa(cid)
        flash('%s não pode receber contrato ainda — %s. Complete o cadastro em A CADASTRAR e volte aqui.'
              % ((emp or {}).get('nome_razao_social') or 'A empresa', imp[cid]), 'warning')
        return redirect(url_for('financeiro.contratos') + '#pc-prontas')
    return render_template('financeiro/contrato_form.html', **_form_ctx(cid, request.args.get('fin_contrato_id', type=int)))


@financeiro.route('/financeiro/contratos/<int:cid>/editar')
@admin_required
def contrato_editar(cid):
    c = PrestContrato.get(cid)
    if not c or c['status'] != 'RASCUNHO':
        flash('Só rascunho pode ser editado.', 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    return render_template('financeiro/contrato_form.html', **_form_ctx(contrato=c))


@financeiro.route('/financeiro/contratos/salvar', methods=['POST'])
@admin_required
def contrato_salvar():
    f = request.form
    cid = f.get('contrato_id', type=int)
    try:
        dados = _ler_form(f)
        _guardar_contato(dados, f)
        if cid:
            PrestContrato.atualizar(cid, dados)
            registrar('escrita.alterou_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid, depois={'empresa': dados['empresas'][0]['razao']})
        else:
            cid = PrestContrato.criar(dados['empresas'][0]['cliente_id'], dados, current_user.id, fin_contrato_id=f.get('fin_contrato_id', type=int))
            registrar('escrita.criou_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid,
                      depois={'empresa': dados['empresas'][0]['razao'], 'valor': dados['valor'], 'areas': dados['areas']})
        flash('Rascunho salvo. Confira o PDF e envie para assinatura.', 'success')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(request.referrer or url_for('financeiro.contratos'))


@financeiro.route('/financeiro/contratos/<int:cid>')
@admin_required
def contrato_ver(cid):
    c = PrestContrato.get(cid)
    if not c:
        abort(404)
    ass = PrestContrato.assinantes(cid)
    for a in ass:
        a['link'] = url_for('assinatura.assinar', token=a['token'], _external=True)
    return render_template('financeiro/contrato_ver.html', c=c, assinantes=ass, montado=PrestContrato.montar(c['dados']), hoje=date.today(), AREAS=AREAS, brl=brl)


def _pdf_bytes(c):
    """PDF do contrato: sem registro enquanto não está ASSINADO; com a página de registro depois."""
    registro = None
    if c['status'] == 'ASSINADO':
        registro = [a for a in PrestContrato.assinantes(c['id']) if a['assinado_em']]
    return gerar_pdf(PrestContrato.montar(c['dados']), registro=registro, numero=c.get('numero'),
                     rodape='Contrato nº %s · %s' % (c.get('numero') or c['id'], 'assinado eletronicamente' if registro else 'minuta'))


@financeiro.route('/financeiro/contratos/<int:cid>/pdf')
@admin_required
def contrato_pdf(cid):
    c = PrestContrato.get(cid)
    if not c:
        abort(404)
    b = _pdf_bytes(c)
    return Response(b, mimetype='application/pdf', headers={'Content-Disposition': 'inline; filename="contrato_%s.pdf"' % (c.get('numero') or cid)})


def _esc_html(s):
    return str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _email_assinatura(a, c, link):
    emp = c['dados']['empresas'][0]['razao']
    html = ('<p>Olá, %s.</p><p>A <b>Qualicontax</b> enviou para sua assinatura eletrônica o documento <b>%s</b> (%s), contrato nº <b>%s</b>.</p>'
            '<p><a href="%s" style="background:#15803d;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;">Ver e assinar o contrato</a></p>'
            '<p>Ao abrir o link, você verá o contrato completo, pedirá um código de 6 dígitos (que chega neste e-mail) e confirmará com seu nome e CPF. '
            'Leva menos de dois minutos.</p><p style="color:#64748b;font-size:12px;">Se não reconhece este documento, ignore este e-mail.</p>'
            % (_esc_html(a['nome']), _esc_html(c['titulo'].title()), _esc_html(emp), _esc_html(c.get('numero') or ''), link))
    texto = 'Olá, %s. A Qualicontax enviou para sua assinatura eletrônica o documento %s (%s). Abra o link para ver e assinar: %s' % (a['nome'], c['titulo'].title(), emp, link)
    return _enviar([a['email']], 'Assinatura: %s – %s' % (c['titulo'].title(), emp), html, texto)


@financeiro.route('/financeiro/contratos/<int:cid>/enviar', methods=['POST'])
@admin_required
def contrato_enviar(cid):
    c = PrestContrato.get(cid)
    if not c or c['status'] not in ('RASCUNHO', 'ENVIADO'):
        flash('Este contrato não pode ser enviado.', 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    ass = PrestContrato.assinantes(cid)
    sem_email = [a['nome'] for a in ass if not a.get('email')]
    if sem_email:
        flash('Sem e-mail: %s. Todo mundo que assina precisa de e-mail.' % ', '.join(sem_email), 'danger')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    if c['status'] == 'RASCUNHO':
        c['dados']['data_assinatura'] = date.today().isoformat()      # a data do fecho congela no envio
        execute_query('UPDATE prest_contratos SET dados = %s WHERE id = %s', (__import__('json').dumps(c['dados'], ensure_ascii=False), cid), fetch=False)
        c = PrestContrato.get(cid)
        PrestContrato.mudar_status(cid, 'ENVIADO', pdf_sha256=sha256(_pdf_bytes(c)))
        c = PrestContrato.get(cid)
    enviados, falhas = [], []
    for a in ass:
        if a['assinado_em']:
            continue
        ok, erro = _email_assinatura(a, c, url_for('assinatura.assinar', token=a['token'], _external=True))
        if ok:
            execute_query('UPDATE prest_assinantes SET aviso_enviado_em = NOW() WHERE id = %s', (a['id'],), fetch=False)
            enviados.append(a['nome'])
        else:
            falhas.append('%s (%s)' % (a['nome'], erro))
    registrar('escrita.enviou_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid,
              depois={'enviados': enviados, 'falhas': falhas, 'sha256': c['pdf_sha256']})
    flash('Enviado para assinatura: %s.%s' % (', '.join(enviados) or 'ninguém', (' Falhou: ' + '; '.join(falhas)) if falhas else ''), 'success' if not falhas else 'warning')
    return redirect(url_for('financeiro.contrato_ver', cid=cid))


@financeiro.route('/financeiro/contratos/<int:cid>/cancelar', methods=['POST'])
@admin_required
def contrato_cancelar(cid):
    c = PrestContrato.get(cid)
    if not c or c['status'] == 'ASSINADO':
        flash('Contrato assinado não se cancela aqui: faça um distrato ou adendo.', 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    PrestContrato.mudar_status(cid, 'CANCELADO')
    registrar('escrita.cancelou_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid, antes={'status': c['status']}, depois={'status': 'CANCELADO'})
    flash('Contrato cancelado.', 'info')
    return redirect(url_for('financeiro.contratos'))


# ---------------------------------------------------------------- assinatura (público, por token)
def _assinante(token):
    a = PrestContrato.assinante_por_token(token)
    if not a:
        return None, None, 'Este link não existe.'
    c = PrestContrato.get(a['contrato_id'])
    if c['status'] == 'CANCELADO':
        return a, c, 'Este contrato foi cancelado pela Qualicontax.'
    if c['status'] == 'RASCUNHO':
        return a, c, 'Este contrato ainda não foi enviado para assinatura.'
    return a, c, None


@assinatura.route('/assinar/<token>')
def assinar(token):
    a, c, erro = _assinante(token)
    if erro and not a:
        return render_template('assinar.html', erro=erro), 404
    todos = PrestContrato.assinantes(c['id'])
    return render_template('assinar.html', a=a, c=c, erro=erro, todos=todos, montado=PrestContrato.montar(c['dados']), AREAS=AREAS, brl=brl,
                           codigo_pedido=bool(a.get('codigo_enviado_em')) and not a.get('assinado_em'))


@assinatura.route('/assinar/<token>/pdf')
def assinar_pdf(token):
    a, c, erro = _assinante(token)
    if not a:
        abort(404)
    return Response(_pdf_bytes(c), mimetype='application/pdf', headers={'Content-Disposition': 'inline; filename="contrato.pdf"'})


@assinatura.route('/assinar/<token>/codigo', methods=['POST'])
def assinar_codigo(token):
    a, c, erro = _assinante(token)
    if not a or erro or a['assinado_em']:
        return jsonify({'ok': False, 'erro': erro or 'já assinado'}), 400
    if a.get('codigo_enviado_em') and (datetime.now() - a['codigo_enviado_em']).total_seconds() < 60:
        return jsonify({'ok': False, 'erro': 'Código enviado há menos de um minuto. Confira a caixa de entrada e o spam.'}), 429
    codigo = PrestContrato.gerar_codigo(a['id'])
    html = ('<p>Seu código para assinar <b>%s</b>:</p><p style="font-size:28px;letter-spacing:6px;font-weight:800;">%s</p>'
            '<p>Vale por 30 minutos. Se não foi você, ignore.</p>' % (c['titulo'].title(), codigo))
    ok, err = _enviar([a['email']], 'Código de assinatura: %s' % codigo, html, 'Seu código para assinar: %s (vale 30 minutos).' % codigo)
    if not ok:
        return jsonify({'ok': False, 'erro': 'Não consegui enviar o e-mail: %s' % err}), 500
    return jsonify({'ok': True, 'email': _mascara(a['email'])})


def _mascara(e):
    u, _, d = (e or '').partition('@')
    return (u[:2] + '***@' + d) if d else '***'


@assinatura.route('/assinar/<token>/confirmar', methods=['POST'])
def assinar_confirmar(token):
    a, c, erro = _assinante(token)
    if not a or erro:
        flash(erro or 'Link inválido.', 'danger')
        return redirect(url_for('assinatura.assinar', token=token))
    if a['assinado_em']:
        return redirect(url_for('assinatura.assinar', token=token))
    f = request.form
    nome, cpf, codigo = (f.get('nome') or '').strip(), (f.get('cpf') or '').strip(), (f.get('codigo') or '').strip()
    if not f.get('aceito') or not nome or len(nome.split()) < 2:
        flash('Digite seu nome completo e marque "li e aceito".', 'danger')
        return redirect(url_for('assinatura.assinar', token=token))
    if not PrestContrato.conferir_codigo(a, codigo):
        flash('Código errado ou vencido. Peça outro código.', 'danger')
        return redirect(url_for('assinatura.assinar', token=token))
    pdf_hash = sha256(_pdf_bytes(c))
    if c.get('pdf_sha256') and pdf_hash != c['pdf_sha256']:
        logger.error('[assinatura] hash do PDF mudou entre o envio e a assinatura (contrato %s)', c['id'])
        flash('O documento mudou depois do envio. Avise a Qualicontax.', 'danger')
        return redirect(url_for('assinatura.assinar', token=token))
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
    PrestContrato.registrar_assinatura(a['id'], nome, cpf, ip, request.headers.get('User-Agent', ''), pdf_hash)
    registrar('escrita.assinou_contrato_prestacao', 'financeiro', tabela='prest_assinantes', registro_id=a['id'],
              depois={'contrato_id': c['id'], 'papel': a['papel'], 'nome': nome, 'cpf': cpf, 'ip': ip, 'sha256': pdf_hash})
    if PrestContrato.todos_assinaram(c['id']):
        _concluir(c)
    flash('Assinatura registrada. Obrigado!', 'success')
    return redirect(url_for('assinatura.assinar', token=token))


def _concluir(c):
    """Todos assinaram: PDF final com a página de registro, Dropbox e e-mail de cópia para todos."""
    PrestContrato.mudar_status(c['id'], 'ASSINADO')
    c = PrestContrato.get(c['id'])
    pdf = _pdf_bytes(c)
    emp = c['dados']['empresas'][0]
    nome_arq = 'Contrato %s - %s - %s.pdf' % (c.get('numero') or c['id'], c['assinado_em'].strftime('%Y-%m-%d') if c.get('assinado_em') else date.today().isoformat(),
                                             (emp.get('razao') or '')[:60].replace('/', '-'))
    path = None
    try:
        from utils import dropbox_sync
        svc = dropbox_sync.DropboxService()
        pasta = dropbox_sync._build_empresa_folder(emp.get('numero'), emp.get('razao') or '')
        path = svc._build_path('EMPRESAS', pasta, 'CONTRATOS', nome_arq)
        if not svc.upload_bytes(path, pdf):
            path = None
    except Exception as e:                                             # Dropbox fora do ar não desfaz a assinatura
        logger.warning('[assinatura] não guardou no Dropbox: %s', e)
        path = None
    if path:
        PrestContrato.mudar_status(c['id'], 'ASSINADO', pdf_path=path)
    ass = PrestContrato.assinantes(c['id'])
    para = [a['email'] for a in ass if a.get('email')]
    html = ('<p>O documento <b>%s</b> (%s) foi assinado eletronicamente por todas as partes. Segue a via assinada em anexo, com o registro das assinaturas.</p>'
            % (c['titulo'].title(), emp.get('razao')))
    _enviar(para, 'Contrato assinado – %s' % emp.get('razao'), html, 'Contrato assinado por todas as partes. Via em anexo.',
            anexos=[(nome_arq, pdf, 'application/pdf')])
    registrar('escrita.concluiu_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=c['id'],
              depois={'empresa': emp.get('razao'), 'dropbox': path, 'copias_para': para})
