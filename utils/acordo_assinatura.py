"""Acordos, etapa 2 (26/11/2026): o termo vai por link e é assinado com código no e-mail.

Mesmo comportamento da assinatura do contrato de prestação (routes/financeiro_contratos.py, rota /assinar/<token>), com
tabela própria (fin_acordo_assinantes) para não mexer nos arquivos do Comercial: reaproveita só, por import, o molde do
e-mail (_email_contrato) e o envio (_enviar).

* Quem assina: a DEVEDORA (quem assina pela empresa, escolhido na caixa Novo acordo) e a CREDORA (o sócio da Qualicontax).
* Enviar = congela o termo (impressão digital SHA-256 do PDF) e manda a cada um o link pessoal por e-mail; a janela
  também dá o link para copiar e o WhatsApp.
* Na página: confere 3 primeiros ou 2 últimos dígitos do CPF, código de 6 dígitos no e-mail (30 minutos, 5 tentativas),
  "li e aceito". Fica registrado data, hora, IP, navegador e a impressão digital do termo aceito.
* Todos assinaram: o acordo começa a valer (acordos.ativar — títulos renegociados, parcelas e boleto da entrada), a via
  assinada (termo + página de registro) vai para o Dropbox e por e-mail para todos.
* O termo só pode ser assinado até o 1º vencimento: depois disso as datas já não servem e a equipe refaz o acordo.
"""
import hashlib
import logging
import re
import secrets

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
PAPEL = {'DEVEDORA': 'pela devedora', 'CREDORA': 'pela Qualicontax'}


def q(sql, params=(), one=False):
    r = execute_query(sql, params, fetch=True, fetch_one=one)
    return r if one else (r or [])


def x(sql, params=()):
    return execute_query(sql, params)


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def so_digitos(s):
    return re.sub(r'\D', '', str(s or ''))


# ─────────────────────────── quem assina ───────────────────────────
def assinantes(aid):
    return q("SELECT * FROM fin_acordo_assinantes WHERE acordo_id = %s ORDER BY FIELD(papel, 'DEVEDORA', 'CREDORA'), id", (aid,))


def por_token(token):
    if not token or len(token) < 20:
        return None
    return q('SELECT * FROM fin_acordo_assinantes WHERE token = %s', (token,), one=True)


def _email_qc(a):
    """E-mail do sócio da Qualicontax que assina (do cadastro, pelo CPF ou pelo nome)."""
    from models.prest_contrato import PrestContrato
    cpf, nome = so_digitos(a.get('qc_cpf')), (a.get('qc_nome') or '').strip().upper()
    for s in PrestContrato.signatarios_qualicontax().values():
        if (cpf and so_digitos(s.get('cpf')) == cpf) or (nome and (s.get('nome') or '').strip().upper() == nome):
            return s.get('email') or ''
    return ''


def preparar(a):
    """Cria as duas linhas (devedora e Qualicontax) se ainda não existem. Devolve a lista."""
    if not assinantes(a['id']):
        for papel, nome, cpf, cargo, email, cel in (
                ('DEVEDORA', a.get('assina_nome'), a.get('assina_cpf'), a.get('assina_cargo'), a.get('assina_email'), a.get('assina_celular')),
                ('CREDORA', a.get('qc_nome'), a.get('qc_cpf'), a.get('qc_cargo'), _email_qc(a), None)):
            if not (nome or '').strip():
                continue
            x("""INSERT INTO fin_acordo_assinantes (acordo_id, papel, nome, cpf, cargo, email, celular, token)
                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
              (a['id'], papel, nome.strip()[:150], (cpf or '')[:20] or None, (cargo or '')[:80] or None,
               (email or '').strip()[:150] or None, (cel or '')[:30] or None, secrets.token_urlsafe(32)))
    return assinantes(a['id'])


# ─────────────────────────── o PDF ───────────────────────────
def termo_bytes(a, registro=None):
    """O termo como o cliente vê (sem registro) ou a via assinada (com a página de registro)."""
    from utils.acordo_pdf import de_acordo, gerar_pdf
    return gerar_pdf(de_acordo(a), registro=registro,
                     rodape='assinado eletronicamente' if registro else None)


def prazo_vencido(a):
    """True se o 1º vencimento do acordo já passou (o termo não pode mais ser assinado)."""
    from utils.acordos import hoje
    vs = [p['vencimento'] for p in a['parcelas'] if p.get('vencimento')]
    return bool(vs) and min(vs) < hoje()


# ─────────────────────────── e-mails (molde do contrato) ───────────────────────────
def _c_fake(a):
    """O dict que _email_contrato espera, com os dados do acordo."""
    return {'id': a['id'], 'numero': a['numero'], 'titulo': 'Termo de acordo e confissão de dívida',
            'dados': {'empresas': [{'razao': a['nome_razao_social'], 'cnpj': a.get('cpf_cnpj') or ''}]}}


def _email(a, **extra):
    from routes.financeiro_contratos import _email_contrato
    extra.setdefault('eyebrow', 'Assinatura eletrônica · acordo nº %s' % a['numero'])
    extra.setdefault('cliente_rotulo', 'Devedora')
    return _email_contrato(_c_fake(a), **extra)


def _enviar(para, assunto, html, texto, anexos=None, referencia=None):
    from routes.financeiro_contratos import _enviar as enviar_contrato
    return enviar_contrato(para, assunto, html, texto, anexos=anexos, referencia=referencia)


def _quem_assina_txt(a):
    from markupsafe import Markup
    from utils.texto import nome_proprio
    partes = ['%s (<b>%s</b>%s)' % (nome_proprio(s['nome']), PAPEL[s['papel']], ' · já assinou' if s.get('assinado_em') else '')
              for s in assinantes(a['id'])]
    return Markup('Assinam este termo: ' + '; '.join(partes) + '.') if partes else ''


def _sem_tags(t):
    return re.sub(r'<[^>]+>', '', str(t))


def email_convite(a, s, link):
    from markupsafe import Markup
    from utils.acordos import brl
    from utils.texto import nome_proprio
    primeiro = nome_proprio((s.get('nome') or '').split()[0]) if (s.get('nome') or '').strip() else ''
    papel = 'pela empresa <b>devedora</b>' if s['papel'] == 'DEVEDORA' else 'pela Qualicontax, como <b>credora</b>'
    ent = next((p for p in a['parcelas'] if p['entrada']), None)
    kpis = [('Acordo nº', a['numero']), ('Total do acordo', brl(a['total'])),
            ('Parcelas', (('entrada + ' if ent else '') + '%dx de %s' % (a['n_parcelas'], brl(a['valor_parcela']))))]
    html, d = _email(
        a, saudacao=('Olá, %s!' % primeiro) if primeiro else 'Olá!',
        intro=Markup('A Qualicontax preparou o termo do acordo acima e ele está pronto para a sua assinatura eletrônica, %s. '
                     'Não precisa imprimir nem reconhecer firma: leva menos de dois minutos, pelo celular ou pelo computador.' % papel),
        kpis=kpis, botao=('Ver e assinar o termo', link),
        passos=[('Abra o termo.', 'Toque no botão acima e leia o acordo completo; dá para baixar o PDF ali mesmo.'),
                ('Peça o código.', 'Confirme os dígitos do seu CPF e um código de 6 dígitos chega neste e-mail; vale por 30 minutos.'),
                ('Confirme.', 'Digite o código e assine. Quando todos assinarem, o acordo começa a valer e a via assinada chega por e-mail.')],
        aviso=_quem_assina_txt(a),
        rodape='Enviado por Qualicontax Assessoria Contábil. O link deste e-mail é pessoal e só serve para você: não repasse. '
               'Em caso de dúvida, responda este e-mail. Se não reconhece este documento, pode ignorar.')
    texto = '\n'.join([d['eyebrow'], d['titulo'] + ' · ' + d['cliente_nome'], '', d['saudacao'], _sem_tags(d['intro']), '',
                       'Ver e assinar o termo: ' + link, ''] + ['%d. %s %s' % (i + 1, t, tx) for i, (t, tx) in enumerate(d['passos'])]
                      + ['', _sem_tags(d['aviso']), '', d['rodape']])
    return _enviar([s['email']], 'Para assinar: termo do acordo %s · %s' % (a['numero'], d['cliente_nome']), html, texto, referencia=('acordo', a['id']))


def link_de(s):
    from flask import url_for
    return url_for('acordo_assinatura.assinar', token=s['token'], _external=True)


def texto_whatsapp(a, s):
    from utils.texto import nome_proprio
    primeiro = nome_proprio((s.get('nome') or '').split()[0]) if (s.get('nome') or '').strip() else ''
    return ('Olá%s! Aqui é da Qualicontax. Segue o termo do acordo %s para você assinar pelo celular: %s\n'
            'Ao abrir, confirme os dígitos do seu CPF e digite o código que chega no seu e-mail.'
            % ((', ' + primeiro) if primeiro else '', a['numero'], link_de(s)))


# ─────────────────────────── enviar ───────────────────────────
def enviar(aid, usuario):
    """Congela o termo e manda o link a quem ainda não assinou. Devolve (enviados, falhas). Levanta AcordoInvalido."""
    from utils.acordos import AcordoInvalido, evento, get
    a = get(aid)
    if not a or a['situacao'] not in ('FALTA_ENVIAR', 'FALTA_ASSINAR'):
        raise AcordoInvalido('Este acordo não está esperando assinatura.')
    if prazo_vencido(a):
        raise AcordoInvalido('O 1º vencimento do acordo já passou: as datas do termo não servem mais. Cancele e monte o acordo de novo.')
    ss = preparar(a)
    sem = [s['nome'] for s in ss if not s.get('email')]
    if sem:
        raise AcordoInvalido('Sem e-mail: %s. Todo mundo que assina precisa de e-mail (o código chega nele).' % ', '.join(sem))
    if not any(s['papel'] == 'DEVEDORA' for s in ss):
        raise AcordoInvalido('Falta quem assina pela empresa devedora.')
    if not a.get('pdf_sha256'):
        x('UPDATE fin_acordos SET pdf_sha256 = %s, enviado_em = NOW() WHERE id = %s', (sha256(termo_bytes(a)), aid))
    enviados, falhas = [], []
    for s in ss:
        if s.get('assinado_em'):
            continue
        try:
            ok, erro = email_convite(a, s, link_de(s))
        except Exception as exc:
            logger.exception('[acordo] e-mail de assinatura %s', s['id'])
            ok, erro = False, str(exc)
        if ok:
            x('UPDATE fin_acordo_assinantes SET aviso_enviado_em = NOW() WHERE id = %s', (s['id'],))
            enviados.append(s['nome'])
        else:
            falhas.append('%s (%s)' % (s['nome'], erro))
    if enviados:
        x("UPDATE fin_acordos SET situacao = 'FALTA_ASSINAR' WHERE id = %s", (aid,))
    quem = getattr(usuario, 'nome', None) or 'O sistema'
    evento(aid, f'{quem} enviou o termo para assinar' if enviados else 'O termo não foi enviado',
           ('para ' + ', '.join(enviados) if enviados else '') + ((' · falhou: ' + '; '.join(falhas)) if falhas else ''),
           getattr(usuario, 'id', None), 'fa-paper-plane')
    from utils.atividade import registrar
    registrar('escrita.enviou_termo_acordo', 'financeiro', tabela='fin_acordos', registro_id=aid,
              depois={'numero': a['numero'], 'enviados': enviados, 'falhas': falhas})
    return enviados, falhas


# ─────────────────────────── código e assinatura ───────────────────────────
def gerar_codigo(sid):
    codigo = '%06d' % secrets.randbelow(1000000)
    x('UPDATE fin_acordo_assinantes SET codigo_hash = %s, codigo_enviado_em = NOW(), codigo_tentativas = 0 WHERE id = %s',
      (sha256(codigo.encode()), sid))
    return codigo


def segundos_do_codigo(sid):
    """Idade do último código pelo relógio do banco (o app roda em UTC e o banco em Brasília)."""
    r = q('SELECT TIMESTAMPDIFF(SECOND, codigo_enviado_em, NOW()) AS s FROM fin_acordo_assinantes WHERE id = %s', (sid,), one=True) or {}
    return r.get('s')


def conferir_codigo(s, codigo):
    if not s.get('codigo_hash') or (s.get('codigo_tentativas') or 0) >= 5:
        return False
    idade = segundos_do_codigo(s['id'])
    if idade is None or idade > 30 * 60:
        return False
    ok = sha256((codigo or '').strip().encode()) == s['codigo_hash']
    if not ok:
        x('UPDATE fin_acordo_assinantes SET codigo_tentativas = codigo_tentativas + 1 WHERE id = %s', (s['id'],))
    return ok


def email_codigo(a, s, codigo):
    from utils.texto import nome_proprio
    primeiro = nome_proprio((s.get('nome') or '').split()[0]) if (s.get('nome') or '').strip() else ''
    html, d = _email(a, saudacao=('Olá, %s!' % primeiro) if primeiro else 'Olá!', codigo=codigo,
                     intro='Aqui está o seu código para assinar o termo do acordo acima. Volte à página do termo e digite os 6 dígitos.',
                     rodape='Enviado por Qualicontax Assessoria Contábil. O código vale por 30 minutos e só serve para você. '
                            'Se não foi você que pediu, ignore este e-mail.')
    return _enviar([s['email']], 'Seu código para assinar: %s' % codigo, html,
                   'Seu código para assinar o termo do acordo nº %s: %s (vale 30 minutos).' % (a['numero'], codigo), referencia=('acordo', a['id']))


def assinar(a, s, cpf, ip, agente):
    """Registra a assinatura (o código já foi conferido). Devolve True se foi a última (o acordo começou a valer)."""
    from utils.acordos import evento
    h = sha256(termo_bytes(a))
    if a.get('pdf_sha256') and h != a['pdf_sha256']:
        logger.error('[acordo] o termo do %s mudou entre o envio e a assinatura', a['numero'])
        raise ValueError('O termo mudou depois do envio. Avise a Qualicontax.')
    x("""UPDATE fin_acordo_assinantes SET assinado_em = NOW(), assinado_nome = %s, assinado_cpf = %s, assinado_ip = %s,
                assinado_agente = %s, assinado_hash = %s, codigo_hash = NULL WHERE id = %s AND assinado_em IS NULL""",
      (s['nome'][:150], (cpf or '')[:20], (ip or '')[:64], (agente or '')[:255], h, s['id']))
    evento(a['id'], f'{s["nome"].title()} assinou o termo', f'{PAPEL[s["papel"]]} · IP {ip}', None, 'fa-pen-nib')
    from utils.atividade import registrar
    registrar('escrita.assinou_termo_acordo', 'financeiro', tabela='fin_acordo_assinantes', registro_id=s['id'],
              depois={'acordo': a['numero'], 'papel': s['papel'], 'nome': s['nome'], 'cpf': cpf, 'ip': ip, 'sha256': h})
    r = q('SELECT COUNT(*) n, SUM(assinado_em IS NOT NULL) ok FROM fin_acordo_assinantes WHERE acordo_id = %s', (a['id'],), one=True)
    if r and r['n'] and int(r['ok'] or 0) == int(r['n']):
        concluir(a['id'])
        return True
    return False


def concluir(aid):
    """Todos assinaram: o acordo começa a valer, via assinada no Dropbox e por e-mail para todos."""
    from utils import acordos as AC
    a = AC.get(aid)
    nomes = ', '.join(s['nome'].title() for s in assinantes(aid))
    avisos = AC.ativar(aid, None, f'Termo assinado por todos ({nomes})')
    a = AC.get(aid)
    reg = [s for s in assinantes(aid) if s.get('assinado_em')]
    pdf = termo_bytes(a, registro=reg)
    nome_arq = 'Termo de Acordo %s - %s.pdf' % (a['numero'], re.sub(r'[\\/:*?"<>|]', ' ', a['nome_razao_social'])[:80].strip())
    path = None
    try:
        from utils import dropbox_sync
        svc = dropbox_sync.DropboxService()
        pasta = dropbox_sync._build_empresa_folder(a.get('numero_cliente'), a['nome_razao_social'] or '')
        path = svc._build_path('EMPRESAS', pasta, 'ACORDOS', nome_arq)
        if not svc.upload_bytes(path, pdf):
            path = None
    except Exception as exc:                       # Dropbox fora do ar não desfaz a assinatura
        logger.warning('[acordo] via assinada não foi para o Dropbox: %s', exc)
        path = None
    if path:
        x('UPDATE fin_acordos SET pdf_path = %s WHERE id = %s', (path[:400], aid))
    para = [s['email'] for s in reg if s.get('email')]
    html, d = _email(a, eyebrow='Termo assinado · acordo nº %s' % a['numero'], saudacao='Olá!',
                     intro='O termo do acordo acima foi assinado eletronicamente por todas as partes e o acordo já está valendo. '
                           'A via assinada, com o registro de cada assinatura, está em anexo neste e-mail. Guarde-a com você.',
                     aviso=_quem_assina_txt(a), anexos_txt='Em anexo: %s' % nome_arq)
    try:
        _enviar(para, 'Termo assinado · acordo %s · %s' % (a['numero'], d['cliente_nome']), html,
                'Termo do acordo nº %s assinado por todas as partes. Via assinada em anexo (%s).' % (a['numero'], nome_arq),
                anexos=[(nome_arq, pdf, 'application/pdf')], referencia=('acordo', a['id']))
    except Exception:
        logger.exception('[acordo] e-mail da via assinada %s', a['numero'])
    AC.evento(aid, 'Via assinada enviada a todos', ('Dropbox: ' + path) if path else 'Dropbox indisponível: a via está na janela',
              None, 'fa-file-signature')
    for m in avisos or []:
        if m.startswith('Boleto da'):              # o boleto emitido já está na linha do tempo
            continue
        AC.evento(aid, 'Aviso ao começar a valer', m[:250], None, 'fa-circle-info')
    return avisos
