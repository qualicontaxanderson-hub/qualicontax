"""Cobranças › Acordos (26/11/2026; desenho e termo aprovados pelo Anderson). Regras em utils/acordos.py.

Etapa 1: montar o acordo (caixa Novo acordo), ver o termo em PDF, a aba Acordos, a janela do acordo, ativar
"sem assinatura" e cancelar. A assinatura por link e código (etapa 2), a parcela dentro do boleto do honorário
(etapa 3) e o rompimento automático (etapa 4) vêm depois."""
import logging
from datetime import date

from flask import abort, jsonify, make_response, render_template, request
from flask_login import current_user

from models.fin_empresa import FinEmpresa
from routes.financeiro import financeiro
from utils import acordos as AC
from utils.auth_helper import permission_required

logger = logging.getLogger(__name__)
PERM = 'financeiro.cobranca.vincular'       # quem vincula pagamento dá desconto (decisão de 14/11) e faz acordo


def _cliente_ou_404(cliente_id):
    cli = AC.dados_do_cliente(cliente_id)
    if not cli or cli['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    return cli


def _acordo_ou_404(aid):
    a = AC.get(aid)
    if not a or a['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    return a


def _form():
    j = request.get_json(silent=True) or {}
    out = dict(j)
    out['titulos'] = [int(i) for i in (j.get('titulos') or []) if str(i).isdigit()]
    return out


def _meses_inicio():
    h = AC.hoje()
    out = []
    for i in range(0, 4):
        a, m = AC._soma_mes(date(h.year, h.month, 1), i)
        out.append(('%04d-%02d' % (a, m), '%s/%s' % (AC.MES[m], str(a)[2:])))
    return out


@financeiro.route('/financeiro/acordos/novo/<int:cliente_id>')
@permission_required(PERM)
def acdiv_novo(cliente_id):
    """A caixa Novo acordo (vem por fetch e abre por cima de Cobranças)."""
    cli = _cliente_ou_404(cliente_id)
    qc_padrao = next((i for i, s in enumerate(cli['qc']) if (s.get('nome') or '').lower().startswith('anderson')), 0)
    return render_template('financeiro/_acordo_novo.html', cli=cli, qc_padrao=qc_padrao, brl=AC.brl, hoje=AC.hoje(), meses=_meses_inicio(),
                           ROMPE=AC.ROMPE, FORMA=AC.FORMA, pre=[int(i) for i in request.args.getlist('t') if i.isdigit()])


@financeiro.route('/financeiro/acordos/novo/<int:cliente_id>', methods=['POST'])
@permission_required(PERM)
def acdiv_criar(cliente_id):
    cli = _cliente_ou_404(cliente_id)
    try:
        v = AC.validar(_form(), cli)
        aid = AC.criar(v, cli, current_user)
    except AC.AcordoInvalido as exc:
        return jsonify(ok=False, erro=str(exc))
    a = AC.get(aid)
    from utils.atividade import registrar
    registrar('escrita.criou_acordo', 'financeiro', tabela='fin_acordos', registro_id=aid,
              depois={'numero': a['numero'], 'cliente_id': cliente_id, 'total': str(a['total']), 'parcelas': a['n_parcelas'],
                      'forma': a['forma'], 'assinatura': a['assinatura']})
    msg = {'ATIVO': 'Acordo %s criado e já valendo: os títulos foram renegociados e as parcelas estão no A receber.',
           'FALTA_ASSINAR': 'Acordo %s criado e o termo foi para assinar. Quando todos assinarem, ele começa a valer sozinho.',
           'FALTA_ENVIAR': 'Acordo %s guardado em "Falta enviar".'}.get(a['situacao'], 'Acordo %s criado.') % a['numero']
    return jsonify(ok=True, id=aid, msg=msg, avisos=v.get('avisos') or [])


@financeiro.route('/financeiro/acordos/previa.pdf', methods=['POST'])
@financeiro.route('/financeiro/acordos/previa/<int:cliente_id>.pdf', methods=['POST'])
@permission_required(PERM)
def acdiv_previa_pdf(cliente_id=None):
    """O termo como vai ficar, com o que está na caixa (nada é gravado)."""
    from utils.acordo_pdf import dados_pdf, gerar_pdf
    cli = _cliente_ou_404(cliente_id or int((request.get_json(silent=True) or {}).get('cliente_id') or 0))
    try:
        v = AC.validar(_form(), cli)
    except AC.AcordoInvalido as exc:
        return jsonify(ok=False, erro=str(exc)), 400
    pdf = gerar_pdf(dados_pdf(AC.proximo_numero(), cli['cliente'], cli['endereco'], v), rodape='PRÉVIA')
    r = make_response(pdf)
    r.headers['Content-Type'] = 'application/pdf'
    r.headers['Content-Disposition'] = 'inline; filename="previa_termo_de_acordo.pdf"'
    return r


@financeiro.route('/financeiro/acordos/<int:aid>')
@permission_required('financeiro.titulos')
def acdiv_janela(aid):
    a = _acordo_ou_404(aid)
    from utils import acordo_assinatura as AS
    links = {s['id']: {'link': AS.link_de(s), 'wa': AS.texto_whatsapp(a, s)} for s in a.get('assinantes') or []}
    return render_template('financeiro/_acordo_janela.html', a=a, brl=AC.brl, FORMA=AC.FORMA, ROMPE=AC.ROMPE,
                           ASSINATURA=AC.ASSINATURA, pode=current_user.has_permission(PERM), hoje=AC.hoje(), links=links,
                           prazo_vencido=AS.prazo_vencido(a))


@financeiro.route('/financeiro/acordos/<int:aid>/termo.pdf')
@permission_required('financeiro.titulos')
def acdiv_termo_pdf(aid):
    from utils.acordo_pdf import de_acordo, gerar_pdf
    a = _acordo_ou_404(aid)
    from utils import acordo_assinatura as AS
    reg = [s for s in a.get('assinantes') or [] if s.get('assinado_em')]
    feito = bool(reg) and len(reg) == len(a['assinantes'])         # etapa 2: todos assinaram = a via com o registro
    r = make_response(AS.termo_bytes(a, registro=reg) if feito else gerar_pdf(de_acordo(a)))
    r.headers['Content-Type'] = 'application/pdf'
    r.headers['Content-Disposition'] = '%s; filename="Termo_de_Acordo_%s.pdf"' % (
        'attachment' if request.args.get('baixar') else 'inline', a['numero'])
    return r


@financeiro.route('/financeiro/acordos/<int:aid>/ativar', methods=['POST'])
@permission_required(PERM)
def acdiv_ativar(aid):
    """Ativar sem esperar a assinatura (combinado por WhatsApp/telefone): exige o motivo."""
    a = _acordo_ou_404(aid)
    motivo = (request.form.get('motivo') or '').strip()[:200]
    if a['situacao'] not in ('FALTA_ENVIAR', 'FALTA_ASSINAR'):
        return jsonify(ok=False, erro='Este acordo já está valendo ou foi encerrado.')
    if len(motivo) < 3:
        return jsonify(ok=False, erro='Escreva como foi combinado com o cliente.')
    avisos = AC.ativar(aid, current_user, f'Sem assinatura, por {current_user.nome}: {motivo}')
    return jsonify(ok=True, msg='Acordo valendo: títulos renegociados e parcelas no A receber.', avisos=avisos)


@financeiro.route('/financeiro/acordos/<int:aid>/enviar', methods=['POST'])
@permission_required(PERM)
def acdiv_enviar(aid):
    """Etapa 2: manda (ou reenvia) o link do termo por e-mail a quem ainda não assinou."""
    _acordo_ou_404(aid)
    from utils import acordo_assinatura as AS
    try:
        enviados, falhas = AS.enviar(aid, current_user)
    except AC.AcordoInvalido as exc:
        return jsonify(ok=False, erro=str(exc))
    if not enviados:
        return jsonify(ok=False, erro='Não consegui mandar o e-mail: ' + '; '.join(falhas))
    return jsonify(ok=True, msg='Termo enviado para assinar: %s.%s' % (', '.join(enviados), (' Falhou: ' + '; '.join(falhas)) if falhas else ''))


@financeiro.route('/financeiro/acordos/<int:aid>/cancelar', methods=['POST'])
@permission_required(PERM)
def acdiv_cancelar(aid):
    _acordo_ou_404(aid)
    motivo = (request.form.get('motivo') or '').strip()[:200]
    if len(motivo) < 5:
        return jsonify(ok=False, erro='Escreva o motivo (pelo menos 5 letras).')
    try:
        AC.cancelar(aid, motivo, current_user)
    except AC.AcordoInvalido as exc:
        return jsonify(ok=False, erro=str(exc))
    return jsonify(ok=True, msg='Acordo cancelado: os títulos voltaram como estavam.')


@financeiro.route('/financeiro/acordos/<int:aid>/parcela/<int:pid>/boleto', methods=['POST'])
@permission_required(PERM)
def acdiv_boleto(aid, pid):
    """Etapa 3: emitir agora o boleto de uma parcela (sem esperar a data automática)."""
    _acordo_ou_404(aid)
    ok, msg = AC.emitir_uma(aid, pid, current_user.id)
    return jsonify(ok=ok, msg=msg, erro=None if ok else msg)


@financeiro.route('/financeiro/acordos/<int:aid>/anotar', methods=['POST'])
@permission_required(PERM)
def acdiv_anotar(aid):
    _acordo_ou_404(aid)
    texto = (request.form.get('texto') or '').strip()[:250]
    if len(texto) < 3:
        return jsonify(ok=False, erro='Escreva a anotação.')
    AC.evento(aid, f'{current_user.nome} anotou', texto, current_user.id, 'fa-note-sticky')
    return jsonify(ok=True, msg='Anotado.')
