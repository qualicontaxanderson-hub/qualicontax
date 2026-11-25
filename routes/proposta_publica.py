# -*- coding: utf-8 -*-
"""Página pública do orçamento: o cliente lê e ACEITA pelo link (21/11/2026).

Anderson: "o cliente assina do jeito que me sugeriu aqui pelo sistema... quero saber quem assinou e quando". Mesmo mecanismo
da assinatura do contrato de prestação: token aleatório na URL, código de 6 dígitos por e-mail (vale 30 minutos, 5 tentativas),
nome e CPF de quem aceita, IP, navegador e a impressão digital (SHA-256) do PDF aceito. O registro vai na última página do PDF
e uma cópia segue por e-mail para o cliente e para quem montou o orçamento.
"""
import hashlib
import logging
from datetime import datetime

import secrets

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for

from models.orcamento import COMO, Orcamento
from utils.atividade import registrar
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
proposta = Blueprint('proposta', __name__)


def _orc(token):
    """(orçamento, erro). Só o que foi mandado ao cliente abre."""
    o = Orcamento.por_token(token) if token and len(token) >= 20 else None
    if not o:
        return None, 'Link inválido ou vencido.'
    if o.get('interno'):
        return o, 'Este orçamento é de controle interno e não foi mandado para aceite.'
    if o['status'] in ('CANCELADO', 'REPROVADO'):
        return o, 'Este orçamento foi cancelado pela Qualicontax.'
    if not o.get('enviado_em'):
        return o, 'Este orçamento ainda não foi enviado.'
    return o, None


def _emails_permitidos(o):
    lista = [e.strip().lower() for e in (o.get('enviado_para') or '').split(',') if e.strip()]
    if o.get('solicitante_email') and o['solicitante_email'].lower() not in lista:
        lista.append(o['solicitante_email'].lower())
    return lista


def _desafio(o):
    """Que parte do CPF pedir: 'ini' (3 primeiros) ou 'fim' (2 últimos), sorteada por visita e guardada na sessão. None = sem CPF
    conhecido no cadastro (não dá para conferir: fica só o e-mail)."""
    if not Orcamento.cpfs_conhecidos(o['cliente_id']):
        return None
    k = 'pd_%s' % o['id']
    if session.get(k) not in ('ini', 'fim'):
        session[k] = secrets.choice(('ini', 'fim'))
    return session[k]


def _confere_cpf(o, parte):
    """A parte digitada bate com algum CPF do cadastro? Devolve o CPF inteiro (só dígitos) ou None."""
    parte = ''.join(ch for ch in str(parte or '') if ch.isdigit())
    tipo = _desafio(o)
    for cpf in Orcamento.cpfs_conhecidos(o['cliente_id']):
        if (tipo == 'ini' and len(parte) == 3 and cpf.startswith(parte)) or (tipo == 'fim' and len(parte) == 2 and cpf.endswith(parte)):
            return cpf
    return None


def _mascara(e):
    u, _, d = (e or '').partition('@')
    return (u[:2] + '***@' + d) if d else '***'


def _pdf(o, com_link=True):
    from utils.orcamento_pdf import gerar_pdf
    link = url_for('proposta.ver', token=o['token'], _external=True) if com_link else None
    return gerar_pdf(o, Orcamento.itens(o['id']), link=link, data_doc=(o['enviado_em'] or o['criado_em']).date())


@proposta.route('/p/<token>')
def ver(token):
    o, erro = _orc(token)
    if not o:
        return render_template('proposta.html', erro=erro, qc=_qc()), 404
    vencido = bool(o.get('validade') and o['status'] == 'APROVADO' and o['validade'] < datetime.now().date())
    cod_ok = bool(o.get('codigo_enviado_em')) and not o.get('aceito_em') and session.get('pc_%s' % o['id'])
    return render_template('proposta.html', o=o, erro=erro, itens=Orcamento.itens(o['id']), COMO=COMO, vencido=vencido, qc=_qc(),
                           emails=[(e, _mascara(e)) for e in _emails_permitidos(o)], codigo_pedido=cod_ok, desafio=_desafio(o) if not erro else None)


@proposta.route('/p/<token>/pdf')
def pdf(token):
    o, erro = _orc(token)
    if not o or (erro and not o.get('enviado_em')):
        abort(404)
    return Response(_pdf(o), mimetype='application/pdf', headers={'Content-Disposition': 'inline; filename="orcamento-%s.pdf"' % o['numero']})


@proposta.route('/p/<token>/codigo', methods=['POST'])
def codigo(token):
    o, erro = _orc(token)
    if not o or erro or o.get('aceito_em'):
        return jsonify({'ok': False, 'erro': erro or 'Este orçamento já foi aceito.'}), 400
    if o.get('validade') and o['status'] == 'APROVADO' and o['validade'] < datetime.now().date():
        return jsonify({'ok': False, 'erro': 'A validade desta proposta venceu em %s. Fale com a Qualicontax para renovar.' % o['validade'].strftime('%d/%m/%Y')}), 400
    dados = request.get_json(silent=True) or {}
    tipo = _desafio(o)
    if tipo:
        cpf = _confere_cpf(o, dados.get('cpf_parte') or request.form.get('cpf_parte'))
        if not cpf:
            return jsonify({'ok': False, 'erro': 'Os dígitos não conferem com o CPF dos sócios do cadastro. Confira e tente de novo, ou fale com a Qualicontax.'}), 400
        session['pc_%s' % o['id']] = cpf
    para = (dados.get('email') or request.form.get('email') or '').strip().lower()
    permitidos = _emails_permitidos(o)
    if not permitidos:
        # mandado pelo WhatsApp sem e-mail cadastrado: quem tem o link informa o próprio e-mail (fica registrado no aceite)
        if '@' not in para or '.' not in para.split('@')[-1]:
            return jsonify({'ok': False, 'erro': 'Digite o seu e-mail para receber o código.'}), 400
    elif para not in permitidos:
        para = permitidos[0] if len(permitidos) == 1 else ''
    if not para:
        return jsonify({'ok': False, 'erro': 'Escolha para qual e-mail mandar o código.'}), 400
    seg = Orcamento.segundos_do_codigo(o['id'])
    if seg is not None and seg < 60:
        return jsonify({'ok': False, 'erro': 'Código enviado há menos de um minuto. Confira a caixa de entrada e o spam.'}), 429
    cod = Orcamento.gerar_codigo(o['id'], para)
    from utils import orcamento_email
    from utils.email_envio import enviar
    assunto, html, texto = orcamento_email.codigo(o, cod)
    ok, err = enviar([para], assunto, html, texto, servico='orcamento')
    if not ok:
        return jsonify({'ok': False, 'erro': 'Não consegui enviar o e-mail: %s' % err}), 500
    return jsonify({'ok': True, 'email': _mascara(para)})


@proposta.route('/p/<token>/aceitar', methods=['POST'])
def aceitar(token):
    o, erro = _orc(token)
    if not o or erro:
        flash(erro or 'Link inválido.', 'danger')
        return redirect(url_for('proposta.ver', token=token))
    if o.get('aceito_em'):
        return redirect(url_for('proposta.ver', token=token))
    if o.get('validade') and o['status'] == 'APROVADO' and o['validade'] < datetime.now().date():
        flash('A validade desta proposta venceu. Fale com a Qualicontax para renovar.', 'danger')
        return redirect(url_for('proposta.ver', token=token))
    f = request.form
    nome, cod = ((o.get('solicitante_nome') or '').strip() or (f.get('nome') or '').strip()), (f.get('codigo') or '').strip()   # 25/11/2026: o nome é de quem pediu; o cliente não muda
    cpf = session.get('pc_%s' % o['id']) or ''                 # o CPF conferido no passo 2 (o cliente não digita o CPF inteiro)
    if cpf:
        cpf = '%s.%s.%s-%s' % (cpf[:3], cpf[3:6], cpf[6:9], cpf[9:])
    if _desafio(o) and not cpf:
        flash('Confirme primeiro os dígitos do CPF e peça o código.', 'danger')
        return redirect(url_for('proposta.ver', token=token))
    if not f.get('aceito') or not nome or len(nome.split()) < 2:
        flash('Digite seu nome completo e marque "li e aceito".', 'danger')
        return redirect(url_for('proposta.ver', token=token))
    if not Orcamento.conferir_codigo(o, cod):
        flash('Código errado ou vencido. Peça outro código.', 'danger')
        return redirect(url_for('proposta.ver', token=token))
    pdf_hash = hashlib.sha256(_pdf(o)).hexdigest()
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
    Orcamento.registrar_aceite(o['id'], nome, cpf, o.get('codigo_para'), ip, request.headers.get('User-Agent', ''), pdf_hash)
    Orcamento.evento(o['id'], 'aceito', 'Aceito pelo cliente: %s%s · %s · pelo link, com código enviado a %s' % (nome, (' (CPF %s)' % cpf) if cpf else '', datetime.now().strftime('%d/%m/%Y %H:%M'), o.get('codigo_para') or ''))
    try:
        registrar('escrita.cliente_aceitou_orcamento', 'comercial', tabela='com_orcamentos', registro_id=o['id'],
                  depois={'nome': nome, 'cpf': cpf, 'email': o.get('codigo_para'), 'ip': ip, 'sha256': pdf_hash})
    except Exception:
        logger.exception('[proposta] log do aceite')
    session.pop('pc_%s' % o['id'], None); session.pop('pd_%s' % o['id'], None)
    _confirmar(Orcamento.get(o['id']))
    flash('Proposta aceita. Obrigado! Uma cópia com o registro do aceite foi enviada ao seu e-mail.', 'success')
    return redirect(url_for('proposta.ver', token=token))


def _confirmar(o):
    """PDF final (com a página de registro) para o cliente e para quem montou o orçamento."""
    try:
        from utils import orcamento_email
        from utils.email_envio import enviar
        pdf = _pdf(o); itens = Orcamento.itens(o['id'])
        para = [e for e in {o.get('aceito_email'), o.get('solicitante_email')} if e]
        quem = execute_query('SELECT email FROM usuarios WHERE id = %s', (o['criado_por'],), fetch=True, fetch_one=True) or {}
        copia = [quem['email']] if quem.get('email') else []
        anexo = [('Orcamento-%s-aceito.pdf' % o['numero'], pdf, 'application/pdf')]
        if para:
            a, h, t = orcamento_email.aceite(o, itens); enviar(para, a, h, t, anexos=anexo, servico='orcamento')
        if copia:
            a, h, t = orcamento_email.aceite(o, itens, para_equipe=True); enviar(copia, a, h, t, anexos=anexo, servico='orcamento')
    except Exception:
        logger.exception('[proposta] confirmação do aceite %s', o['id'])


def _qc():
    """Razão, CNPJ, endereço e e-mail da Qualicontax para o rodapé, do cadastro (24/11/2026)."""
    from models.prest_contrato import PrestContrato, QUALICONTAX
    try:
        return PrestContrato.contratada()
    except Exception:
        return dict(QUALICONTAX, email='faleconosco@qualicontax.com.br')
