"""Página pública de assinatura do termo de acordo (26/11/2026, etapa 2). Mesmo comportamento da assinatura do contrato
(/assinar/<token>): link pessoal, dígitos do CPF, código de 6 dígitos no e-mail, "li e aceito". Regras em
utils/acordo_assinatura.py."""
import logging
import secrets

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for

from utils import acordo_assinatura as AS
from utils import acordos as AC

logger = logging.getLogger(__name__)
acordo_assinatura = Blueprint('acordo_assinatura', __name__)


def _qc():
    from models.prest_contrato import PrestContrato
    return PrestContrato.contratada()


def _assinante(token):
    s = AS.por_token(token)
    if not s:
        return None, None, 'Este link não existe.'
    a = AC.get(s['acordo_id'])
    if not a:
        return None, None, 'Este link não existe.'
    if a['situacao'] == 'CANCELADO':
        return s, a, 'Este acordo foi cancelado pela Qualicontax.'
    if a['situacao'] == 'FALTA_ENVIAR' or not a.get('pdf_sha256'):
        return s, a, 'Este termo ainda não foi enviado para assinatura.'
    if a['situacao'] == 'FALTA_ASSINAR' and not s.get('assinado_em') and AS.prazo_vencido(a):
        return s, a, 'O prazo para assinar este termo acabou (o primeiro vencimento já passou). Fale com a Qualicontax para refazer o acordo.'
    return s, a, None


def _cpf(s):
    return AS.so_digitos(s.get('cpf'))


def _desafio(s):
    """3 primeiros ('ini') ou 2 últimos ('fim') dígitos do CPF, sorteado por visita. None = sem CPF no cadastro."""
    if len(_cpf(s)) != 11:
        return None
    k = 'acd_%s' % s['id']
    if session.get(k) not in ('ini', 'fim'):
        session[k] = secrets.choice(('ini', 'fim'))
    return session[k]


def _confere_cpf(s, parte):
    parte = AS.so_digitos(parte)
    tipo, cpf = _desafio(s), _cpf(s)
    if (tipo == 'ini' and len(parte) == 3 and cpf.startswith(parte)) or (tipo == 'fim' and len(parte) == 2 and cpf.endswith(parte)):
        return cpf
    return None


def _mascara(e):
    u, _, d = (e or '').partition('@')
    return (u[:2] + '***@' + d) if d else '***'


@acordo_assinatura.route('/acordo/<token>')
def assinar(token):
    s, a, erro = _assinante(token)
    if not s:
        return render_template('acordo_assinar.html', erro=erro, qc=_qc()), 404
    desafio = _desafio(s) if not erro else None
    cod_ok = bool(s.get('codigo_enviado_em')) and not s.get('assinado_em') and (not desafio or bool(session.get('acc_%s' % s['id'])))
    return render_template('acordo_assinar.html', qc=_qc(), s=s, a=a, erro=erro, todos=AS.assinantes(a['id']), brl=AC.brl,
                           FORMA=AC.FORMA, ROMPE=AC.ROMPE, codigo_pedido=cod_ok, desafio=desafio, email_mascarado=_mascara(s.get('email')))


@acordo_assinatura.route('/acordo/<token>/pdf')
def assinar_pdf(token):
    s, a, erro = _assinante(token)
    if not s:
        abort(404)
    reg = [x for x in AS.assinantes(a['id']) if x.get('assinado_em')]
    feito = a['situacao'] not in ('FALTA_ENVIAR', 'FALTA_ASSINAR', 'CANCELADO') and reg and len(reg) == len(a.get('assinantes') or [])
    return Response(AS.termo_bytes(a, registro=reg if feito else None), mimetype='application/pdf',
                    headers={'Content-Disposition': 'inline; filename="Termo_de_Acordo_%s.pdf"' % a['numero']})


@acordo_assinatura.route('/acordo/<token>/codigo', methods=['POST'])
def assinar_codigo(token):
    s, a, erro = _assinante(token)
    if not s or erro or s.get('assinado_em'):
        return jsonify({'ok': False, 'erro': erro or 'Você já assinou.'}), 400
    dados = request.get_json(silent=True) or {}
    if _desafio(s):
        cpf = _confere_cpf(s, dados.get('cpf_parte') or request.form.get('cpf_parte'))
        if not cpf:
            return jsonify({'ok': False, 'erro': 'Os dígitos não conferem com o CPF de quem assina. Confira e tente de novo, ou fale com a Qualicontax.'}), 400
        session['acc_%s' % s['id']] = cpf
    idade = AS.segundos_do_codigo(s['id'])
    if idade is not None and idade < 60:
        return jsonify({'ok': False, 'erro': 'Código enviado há menos de um minuto. Confira a caixa de entrada e o spam.'}), 429
    codigo = AS.gerar_codigo(s['id'])
    ok, err = AS.email_codigo(a, s, codigo)
    if not ok:
        return jsonify({'ok': False, 'erro': 'Não consegui enviar o e-mail: %s' % err}), 500
    return jsonify({'ok': True, 'email': _mascara(s['email'])})


@acordo_assinatura.route('/acordo/<token>/confirmar', methods=['POST'])
def assinar_confirmar(token):
    s, a, erro = _assinante(token)
    if not s or erro:
        flash(erro or 'Link inválido.', 'danger')
        return redirect(url_for('acordo_assinatura.assinar', token=token))
    if s.get('assinado_em'):
        return redirect(url_for('acordo_assinatura.assinar', token=token))
    f = request.form
    cpf = session.get('acc_%s' % s['id']) or ''
    if cpf:
        cpf = '%s.%s.%s-%s' % (cpf[:3], cpf[3:6], cpf[6:9], cpf[9:])
    elif _desafio(s):
        flash('Confirme primeiro os dígitos do CPF e peça o código.', 'danger')
        return redirect(url_for('acordo_assinatura.assinar', token=token))
    if not f.get('aceito'):
        flash('Marque "li e aceito" para assinar.', 'danger')
        return redirect(url_for('acordo_assinatura.assinar', token=token))
    if not AS.conferir_codigo(s, (f.get('codigo') or '').strip()):
        flash('Código errado ou vencido. Peça outro código.', 'danger')
        return redirect(url_for('acordo_assinatura.assinar', token=token))
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
    try:
        ultima = AS.assinar(a, s, cpf, ip, request.headers.get('User-Agent', ''))
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('acordo_assinatura.assinar', token=token))
    session.pop('acc_%s' % s['id'], None)
    session.pop('acd_%s' % s['id'], None)
    flash('Assinatura registrada. ' + ('Todos assinaram: o acordo já está valendo e a via assinada foi para o seu e-mail.'
                                       if ultima else 'Obrigado!'), 'success')
    return redirect(url_for('acordo_assinatura.assinar', token=token))
