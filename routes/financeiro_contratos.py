# -*- coding: utf-8 -*-
"""Contrato de prestação de serviços: montar do cadastro, enviar para assinatura, assinar pelo link (06/11/2026).

Telas internas no blueprint ``financeiro``, SÓ ADMIN (Anderson, 06/11: "pro ADMIN aparece todos, para os demais não
aparece nada"). Nunca se digita um contrato: ele nasce de um cadastro COMPLETO de Cliente ou de Avulso; quem está em
A CADASTRAR não entra na lista nem passa no salvar. A assinatura é PÚBLICA (blueprint
``assinatura``): o token de 32 bytes na URL identifica o signatário; a identidade é confirmada por código de 6 dígitos
enviado ao e-mail dele. Ninguém assina sem e-mail.
"""
import json
import logging
from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user

from models.fin_contrato import FinContrato
from models.prest_contrato import (AREAS, QUALICONTAX, SIGNATARIOS_QUALICONTAX, PrestContrato, brl)
from routes.financeiro import financeiro
from utils import email_envio
from utils.atividade import registrar
from utils.auth_helper import admin_required, permission_required  # noqa: F401 (admin_required: compat)
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
    return email_envio.enviar(para, assunto, html, texto, remetente=_remetente(), anexos=anexos, servico='Contrato')


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
    # (d) mais um assinante pela contratante (22/11/2026 — Anderson, NH Goiatuba: "cadastro do posto, do sócio do posto + cadastro do Anderson")
    extra = None
    if (f.get('extra_nome') or '').strip():
        extra = {'nome': f.get('extra_nome').strip(), 'cpf': (f.get('extra_cpf') or '').strip(), 'documento': (f.get('extra_documento') or '').strip(),
                 'cargo': (f.get('extra_cargo') or 'representante autorizado(a)').strip(), 'email': (f.get('extra_email') or '').strip().lower(), 'celular': (f.get('extra_celular') or '').strip()}
        if not extra['email']:
            raise ValueError('O assinante extra (%s) precisa de e-mail: a assinatura vai por e-mail.' % extra['nome'])
    # (c) contrato em grupo: sócios iguais passam; diferentes pedem autorização do escritório (a aprovação autoriza)
    iguais, detalhe = PrestContrato.socios_iguais([e['cliente_id'] for e in empresas])
    # (b) carteira: quem tem a marca escolhe se este contrato entra na carteira dele
    carteira = None
    _pct = execute_query('SELECT carteira_pct FROM usuarios WHERE id = %s', (current_user.id,), fetch=True, fetch_one=True) or {}
    if _pct.get('carteira_pct') and f.get('carteira_para') == '1':
        carteira = {'usuario_id': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'pct': float(_pct['carteira_pct'])}
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
    if extra:
        assinantes.insert(1, {'papel': 'CONTRATANTE', 'ordem': 2, 'nome': extra['nome'], 'cpf': extra['cpf'], 'documento': extra['documento'], 'email': extra['email'],
                              'empresa': (extra.get('cargo') or 'autorizado(a)') + ' · ' + (empresas[0]['razao'] if len(empresas) == 1 else ((f.get('apelido') or '').strip().upper() or empresas[0]['razao']))})
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
        'assinante_extra': extra, 'carteira': carteira,
        'grupo': {'n': len(empresas), 'socios_iguais': iguais, 'detalhe': detalhe, 'autorizacao_necessaria': (len(empresas) > 1 and not iguais)},
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


# ── Contrato montado pela EQUIPE, com aprovação (18/11/2026) ─────────────────────────────────────────────
# Anderson: "sobre os Honorários ele e alguns terão acesso mas seria mediante o Contrato de Prestação de Serviços...
# com o Contrato apareceria para a Aprovação"; "Na nossa aprovação" o honorário passa a valer; "A equipe... verá só
# o que estiver na Comissão ou nos Lançamentos, tipo o Guilherme consegue ver os Ativos e os Cancelados e Inativados".
# Fluxo: RASCUNHO → (equipe) Enviar para aprovação → APROVACAO → (Anderson/Alessandra) Aprovar → APROVADO, e o
# honorário (fin_contratos) nasce ATIVO → Enviar para assinatura → ENVIADO → ASSINADO.
MONTAR = 'financeiro.contratos.montar'
APROVAR = 'financeiro.lancamentos.aprovar'
ROTULO = {'RASCUNHO': 'Rascunho', 'APROVACAO': 'Aguardando aprovação', 'APROVADO': 'Aprovado · pronto para enviar',
          'ENVIADO': 'Aguardando assinatura', 'ASSINADO': 'Assinado', 'CANCELADO': 'Cancelado'}
# cor e ícone da janela do contrato, no molde da janela da cobrança / do orçamento (23/11/2026)
STATUS_CLS = {'RASCUNHO': ('tx-perd', 'fa-pen-ruler'), 'APROVACAO': ('tx-venc', 'fa-stamp'), 'APROVADO': ('tx-ok', 'fa-circle-check'),
              'ENVIADO': ('tx-hoje', 'fa-hourglass-half'), 'ASSINADO': ('tx-fut', 'fa-file-signature'), 'CANCELADO': ('tx-perd', 'fa-ban')}
# o que cada ato da Auditoria vira na linha do tempo: (cor, ícone, texto)
_EVENTO = {'escrita.criou_contrato_prestacao': ('', 'fa-pen-ruler', 'Rascunho montado por %s'),
           'escrita.alterou_contrato_prestacao': ('', 'fa-pen', 'Rascunho alterado por %s'),
           'escrita.pediu_aprovacao_contrato': ('tj-laranja', 'fa-stamp', '%s mandou para aprovação'),
           'escrita.aprovou_contrato_prestacao': ('tj-verde', 'fa-circle-check', 'Aprovado por %s'),
           'escrita.devolveu_contrato_prestacao': ('tj-verm', 'fa-rotate-left', 'Devolvido para acertar por %s'),
           'escrita.enviou_contrato_prestacao': ('tj-azul', 'fa-envelope', '%s enviou para assinatura'),
           'escrita.cancelou_contrato_prestacao': ('tj-verm', 'fa-ban', 'Cancelado por %s'),
           'escrita.concluiu_contrato_prestacao': ('tj-verde', 'fa-flag-checkered', 'Assinado por todos: via final guardada e enviada')}


def _agora():
    """Hora do BANCO (Brasília): o app roda em UTC no Railway e os atos da Auditoria usam NOW() do banco; a nota precisa casar."""
    r = execute_query('SELECT NOW() AS n', fetch=True, fetch_one=True) or {}
    return r.get('n') or datetime.now()


def _eventos(c, ass):
    """Linha do tempo do contrato: os atos da Auditoria (logs_sistema), as assinaturas e as anotações da equipe, em ordem."""
    import json as _json
    evs = []
    for lg in execute_query("""SELECT acao, usuario_nome, data_hora, dados_novos FROM logs_sistema
                                WHERE tabela_afetada = 'prest_contratos' AND registro_id = %s AND acao LIKE 'escrita.%%' ORDER BY id""", (c['id'],), fetch=True) or []:
        m = _EVENTO.get(lg['acao'])
        if not m or not lg.get('data_hora'):
            continue
        quem = (lg.get('usuario_nome') or '').split(' (')[0] or 'alguém'
        det = ''
        try:
            dn = _json.loads(lg['dados_novos']) if isinstance(lg.get('dados_novos'), str) else (lg.get('dados_novos') or {})
        except Exception:
            dn = {}
        if lg['acao'] == 'escrita.devolveu_contrato_prestacao' and dn.get('motivo'):
            det = '“%s”' % dn['motivo']
        elif lg['acao'] == 'escrita.cancelou_contrato_prestacao' and dn.get('motivo'):
            det = '“%s”' % dn['motivo']
        elif lg['acao'] == 'escrita.enviou_contrato_prestacao':
            env = dn.get('enviados') or []
            det = ('link por e-mail para ' + ', '.join(n.split(' ')[0].title() for n in env)) if env else 'ninguém recebeu o e-mail'
            if dn.get('falhas'):
                det += ' · falhou: ' + '; '.join(dn['falhas'])
        elif lg['acao'] == 'escrita.aprovou_contrato_prestacao' and dn.get('honorarios_criados'):
            det = 'o honorário passou a valer'
        evs.append({'cls': m[0], 'ico': m[1], 'texto': m[2] % quem if '%s' in m[2] else m[2], 'det': det, 'quando': lg['data_hora']})
    for a in ass:
        if a.get('assinado_em'):
            evs.append({'cls': 'tj-verde', 'ico': 'fa-signature', 'texto': '%s assinou' % a['nome'], 'det': '%s · IP %s' % ((a['papel'] or '').capitalize(), a.get('assinado_ip') or '?'), 'quando': a['assinado_em']})
    for n in (c['dados'].get('notas') or []):
        try:
            q = datetime.fromisoformat(n['em'])
        except Exception:
            continue
        evs.append({'cls': 'tj-azul', 'ico': 'fa-comment', 'texto': n.get('texto') or '', 'det': n.get('nome') or '', 'quando': q})
    if not evs:
        evs.append({'cls': '', 'ico': 'fa-pen-ruler', 'texto': 'Rascunho montado por %s' % (c.get('criado_por_nome') or ''), 'det': '', 'quando': c['criado_em']})
    evs.sort(key=lambda e: e['quando'])
    return evs


def _wa_link(fone, texto):
    from urllib.parse import quote
    d = ''.join(ch for ch in str(fone or '') if ch.isdigit())
    if len(d) in (10, 11):
        d = '55' + d
    if len(d) not in (12, 13):
        return None
    return 'https://wa.me/%s?text=%s' % (d, quote(texto))


def _fone_do_assinante(a, c):
    """Celular de quem assina: o que o formulário guardou (contratante e assinante extra) ou o do usuário (Qualicontax e testemunhas)."""
    d = c['dados']
    if a['papel'] == 'CONTRATANTE':
        if a['ordem'] == 1:
            return (d['empresas'][0].get('rep_celular') or '').strip()
        return ((d.get('assinante_extra') or {}).get('celular') or '').strip()
    if a.get('usuario_id'):
        u = execute_query('SELECT telefone FROM usuarios WHERE id = %s', (a['usuario_id'],), fetch=True, fetch_one=True) or {}
        return (u.get('telefone') or '').strip()
    return ''


def _texto_whatsapp_assinatura(a, c, link):
    """Texto pronto para mandar o link de assinatura pelo WhatsApp, no molde da cobrança: curto, com UM link (23/11/2026)."""
    d = c['dados']
    emp = d['empresas'][0]['razao']
    quem = a['nome'].split(' ')[0].title()
    linhas = ['Olá, %s! Aqui é da *Qualicontax*. 😊' % quem, '',
              'Está pronto para a sua assinatura o *%s* da *%s*%s.' % (c['titulo'].capitalize(), emp, (' (+ %d empresas)' % (len(d['empresas']) - 1)) if len(d['empresas']) > 1 else ''),
              '📄 Contrato nº *%s*' % (c.get('numero') or c['id']), '💰 Honorários: R$ %s por mês, dia %s' % (brl(d.get('valor')).replace('R$', '').strip(), d.get('dia_vencimento')), '',
              '👉 Para ler o contrato inteiro e assinar, é só tocar no link. Lá você pede um código que chega no seu e-mail (%s), confirma o nome e o CPF e pronto. Leva menos de dois minutos.' % (a.get('email') or 'o do cadastro'),
              link, '', 'Qualquer dúvida é só responder por aqui. Obrigado pela parceria! 🙏']
    return '\n'.join(linhas)


def _aprovador():
    return current_user.is_admin() or current_user.has_permission(APROVAR)


def _escopo():
    """Empresas que a pessoa (que não aprova) enxerga: as dos lançamentos dela e as da carteira (qualquer situação)."""
    rows = execute_query(
        """SELECT cliente_id FROM fin_lancamentos WHERE lancado_por = %s AND cliente_id IS NOT NULL
           UNION SELECT cliente_id FROM fin_comissao_acordos WHERE usuario_id = %s AND cliente_id IS NOT NULL""",
        (current_user.id, current_user.id), fetch=True) or []
    return {r['cliente_id'] for r in rows}


def _ve(c, escopo=None):
    """A pessoa pode ver este contrato? Quem aprova vê todos; a equipe, os que montou e os das empresas do escopo."""
    if not c:
        return False
    if _aprovador() or c.get('criado_por') == current_user.id:
        return True
    escopo = _escopo() if escopo is None else escopo
    ids = {c['cliente_id']} | {e.get('cliente_id') for e in ((c.get('dados') or {}).get('empresas') or [])}
    return bool(ids & escopo)


def _so_meu_ou_aprov(c):
    if not c or not (_aprovador() or c.get('criado_por') == current_user.id):
        abort(404)


def _avisar_aprovadores(c):
    try:
        rows = execute_query(
            """SELECT DISTINCT u.email FROM usuarios u WHERE u.email LIKE '%%@%%' AND (u.tipo_usuario = 'ADMIN'
                  OR u.id IN (SELECT usuario_id FROM usuario_permissoes_extra WHERE permissao_codigo = %s))""",
            (APROVAR,), fetch=True) or []
        emp = c['dados']['empresas'][0]['razao']
        link = url_for('financeiro.contrato_ver', cid=c['id'], _external=True)
        html = ('<p><b>%s</b> montou o contrato de prestação de serviços de <b>%s</b> (honorários de %s por mês) e pediu a sua aprovação.</p>'
                '<p><a href="%s" style="background:#15803d;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;">Conferir e aprovar</a></p>'
                % (_esc_html(getattr(current_user, 'nome', '')), _esc_html(emp), brl(c['dados'].get('valor')), link))
        _enviar([r['email'] for r in rows], 'Contrato para aprovar – %s' % emp, html,
                '%s pediu a aprovação do contrato de %s. Abra: %s' % (getattr(current_user, 'nome', ''), emp, link))
    except Exception:
        logger.exception('[contratos] aviso de aprovação')


_DECIMO = {'COBRADO': 'DUAS', 'UNICA': 'UNICA', 'ISENTO': 'NENHUM'}


def _carteira_do_contrato(c, aprovado_por, avisos):
    """Na aprovação, a comissão de carteira de quem montou (se marcou) nasce sozinha: um acordo HONORARIOS por empresa do contrato,
    a partir do mês de início. Quem já tinha acordo vigente na empresa fica como está."""
    from models.fin_lancamento import FinComissaoAcordo
    d = c['dados']
    k = d.get('carteira')
    if not k or not k.get('usuario_id') or not k.get('pct'):
        return []
    try:
        inicio = date.fromisoformat((d.get('inicio') or '')[:10]).replace(day=1)
    except ValueError:
        inicio = date.today().replace(day=1)
    feitos = []
    for e in d.get('empresas') or []:
        cid = e['cliente_id']
        ja = [a for a in FinComissaoAcordo.listar(usuario_id=k['usuario_id'], so_ativos=True) if a['base'] == 'HONORARIOS' and a.get('cliente_id') == cid]
        if ja:
            avisos.append('%s já estava na carteira de %s' % (e['razao'], k['nome'].split(' ')[0])); continue
        aid = FinComissaoAcordo.criar(k['usuario_id'], 'HONORARIOS', 'PCT', k['pct'], inicio, cliente_id=cid,
                                      observacao='Do contrato de prestação nº %s (aprovado no sistema)' % (c.get('numero') or c['id']),
                                      criado_por=c['criado_por'], aprovado_por=aprovado_por)
        feitos.append(aid)
    if feitos:
        avisos.append('%s entra na carteira de %s com %g%% da mensalidade de %d empresa%s' % ('Comissão' if len(feitos) == 1 else 'Comissões', k['nome'].split(' ')[0], k['pct'], len(feitos), 's' if len(feitos) != 1 else ''))
    return feitos


def _honorario_do_contrato(c, aprovado_por):
    """Na APROVAÇÃO o honorário passa a valer: cria o contrato de honorários (fin_contratos) ATIVO de cada empresa
    que ainda não tem um vigente. Quem já tem fica como está (devolve o aviso). Devolve (criados, avisos)."""
    from models.fin_contrato import REAJUSTE_TIPOS
    d = c['dados']
    try:
        inicio = date.fromisoformat((d.get('inicio') or '')[:10])
    except ValueError:
        inicio = date.today()
    reaj = d.get('reajuste_tipo') if d.get('reajuste_tipo') in REAJUSTE_TIPOS else 'SALARIO_MINIMO'
    empresas = d.get('empresas') or []
    if d.get('valores_por_empresa'):
        alvos = [(e['cliente_id'], e.get('valor') or '0', e['razao']) for e in empresas]
    else:
        alvos = [(empresas[0]['cliente_id'], d.get('valor'), empresas[0]['razao'])]
    criados, avisos = [], []
    for cid, valor, razao in alvos:
        if not valor or float(valor) <= 0:
            avisos.append('%s: sem valor no contrato, honorário não criado' % razao); continue
        vig = FinContrato.por_cliente(cid)
        if vig:
            avisos.append('%s já tinha honorário vigente de %s: ficou como estava (mude em Honorários, se precisar)' % (razao, brl(vig[0]['valor'])))
            continue
        fid = FinContrato.criar(cid, valor, int(d.get('dia_vencimento') or 10), inicio,
                                [{'descricao': 'Honorários contábeis', 'valor': valor}],
                                decimo_tipo=_DECIMO.get(d.get('decimo'), 'DUAS'), reajuste_tipo=reaj,
                                reajuste_obs=d.get('reajuste_indice'), criado_por=c['criado_por'], aprovado_por=aprovado_por,
                                observacao='Do contrato de prestação nº %s, aprovado no sistema' % (c.get('numero') or c['id']))
        criados.append(fid)
    return criados, avisos


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
    if fin and not _aprovador() and emp and emp['id'] not in _escopo():
        fin = None                     # honorário de empresa fora dos lançamentos/carteira da pessoa: não mostra
    if fin:
        fin['itens'] = FinContrato.itens(fin['id'])
    pct = (execute_query('SELECT carteira_pct FROM usuarios WHERE id = %s', (current_user.id,), fetch=True, fetch_one=True) or {}).get('carteira_pct')
    # contrato novo nasce preenchido com o sócio administrador do cadastro (22/11/2026 — Anderson: "os campos quase nada preenchido, e está tudo no cadastro")
    rep0 = None
    if emp and not contrato and emp.get('socios'):
        s0 = emp['socios'][0]
        rep0 = {'rep_nome': (s0.get('nome') or '').strip(), 'rep_cpf': s0.get('cpf') or '', 'rep_documento': s0.get('documento') or '',
                'rep_email': s0.get('email') or '', 'rep_cargo': s0.get('cargo') or 'sócio administrador', 'rep_celular': s0.get('celular') or ''}
    return {'emp': emp, 'fin': fin, 'dados': d, 'contrato': contrato, 'AREAS': AREAS, 'SIGN_Q': PrestContrato.signatarios_qualicontax(),
            'contratada': PrestContrato.contratada(), 'rep0': rep0,
            'testemunhas': PrestContrato.testemunhas_disponiveis(), 'hoje': date.today(),
            'carteira_pct': float(pct) if pct else None}


@financeiro.route('/comercial/contratos/socios-iguais')
@permission_required(MONTAR)
def contrato_socios_iguais():
    """Para a tela avisar na hora: as empresas escolhidas têm o mesmo quadro de sócios?"""
    ids = [x for x in (request.args.get('ids') or '').split(',') if x.isdigit()]
    iguais, detalhe = PrestContrato.socios_iguais(ids)
    return jsonify({'iguais': iguais, 'detalhe': detalhe, 'n': len(ids)})


@financeiro.route('/comercial/contratos/pessoas')
@permission_required(MONTAR)
def contrato_pessoas():
    """Pessoas físicas do cadastro (nome ou CPF) para puxar quem assina, com documento, e-mail e celular."""
    return jsonify(PrestContrato.pessoas(request.args.get('q') or ''))


# ---------------------------------------------------------------- telas internas
@financeiro.route('/comercial/contratos')
@permission_required(MONTAR)
def contratos():
    """Lista no molde da lista de Orçamentos (24/11/2026, Anderson: "está horrível e não tem que aparecer nenhum cadastro"):
    só os contratos, em abas por situação; a empresa é escolhida na caixa ao clicar em Novo contrato, nunca numa lista solta."""
    aba = request.args.get('aba') or ('APROVACAO' if _aprovador() and execute_query(
        "SELECT 1 FROM prest_contratos WHERE status = 'APROVACAO' LIMIT 1", fetch=True) else '')
    aprov = _aprovador()
    escopo = None if aprov else _escopo()
    lista = PrestContrato.listar()
    if not aprov:
        lista = [r for r in lista if r.get('criado_por') == current_user.id or r['cliente_id'] in escopo
                 or (escopo & set(r.get('empresas_ids') or []))]
    cont = {s: [0, 0.0] for s in ROTULO}
    pessoas = {}
    for r in lista:
        d = r.get('dados') or {}
        try:
            r['valor'] = float(str(d.get('valor') or '0').replace(',', '.'))
        except ValueError:
            r['valor'] = 0.0
        r['areas'] = [AREAS[a][0].capitalize() for a in (d.get('areas') or []) if a in AREAS]
        r['extras'] = [x for x in (d.get('servicos_extras') or d.get('extras') or []) if x]
        r['carteira'] = (d.get('carteira') or {}).get('nome') or ''
        r['grupo_n'] = int((d.get('grupo') or {}).get('n') or 0)
        r['inicio'] = d.get('inicio') or ''
        r['cancel_motivo'] = (d.get('cancelado') or {}).get('motivo') or ''
        r['cls'], r['ico'] = STATUS_CLS.get(r['status'], ('tx-perd', 'fa-circle'))
        cont.setdefault(r['status'], [0, 0.0]); cont[r['status']][0] += 1; cont[r['status']][1] += r['valor']
        if r.get('criado_por'):
            pessoas[r['criado_por']] = r.get('criado_por_nome') or ''
    # a caixa "Novo contrato" só oferece cadastro completo (Clientes ou Avulsos) sem contrato vivo — PrestContrato.prontas()
    pr = PrestContrato.prontas()
    cli = []
    for s in pr['lista']:
        cli.append({'id': s['id'], 'num': str(s['numero_cliente'] or ''), 'nome': s['nome_razao_social'], 'doc': s['cpf_cnpj'] or '',
                    'av': bool(s['avulso']), 'mensal': float(s['valor'] or 0) if (aprov or s['id'] in escopo) else 0.0})
    return render_template('financeiro/contratos.html', aba=aba, lista=lista, cont=cont, pessoas=pessoas, ROTULO=ROTULO, aprov=aprov,
                           clientes_json=json.dumps(cli, ensure_ascii=False), n_prontas=len(cli),
                           pend_empresas=pr['pendentes_empresas'], pend_avulsos=pr['pendentes_avulsos'], hoje=date.today())


@financeiro.route('/comercial/contratos/novo')
@permission_required(MONTAR)
def contrato_novo():
    """Só a partir de um cadastro completo: sem cliente_id, ou com pendência em A CADASTRAR, volta para a lista."""
    cid = request.args.get('cliente_id', type=int)
    if not cid:
        flash('O contrato nasce do cadastro: clique em Novo contrato e escolha a empresa. Nada é digitado do zero.', 'warning')
        return redirect(url_for('financeiro.contratos'))
    imp = PrestContrato.impedimentos([cid])
    if cid in imp:
        emp = PrestContrato.empresa(cid)
        flash('%s não pode receber contrato ainda — %s. Complete o cadastro em A CADASTRAR e volte aqui.'
              % ((emp or {}).get('nome_razao_social') or 'A empresa', imp[cid]), 'warning')
        return redirect(url_for('financeiro.contratos'))
    return render_template('financeiro/contrato_form.html', **_form_ctx(cid, request.args.get('fin_contrato_id', type=int)))


@financeiro.route('/comercial/contratos/<int:cid>/editar')
@permission_required(MONTAR)
def contrato_editar(cid):
    c = PrestContrato.get(cid)
    _so_meu_ou_aprov(c)
    if not c or c['status'] != 'RASCUNHO':
        flash('Só rascunho pode ser editado.', 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    return render_template('financeiro/contrato_form.html', **_form_ctx(contrato=c))


@financeiro.route('/comercial/contratos/salvar', methods=['POST'])
@permission_required(MONTAR)
def contrato_salvar():
    f = request.form
    cid = f.get('contrato_id', type=int)
    try:
        dados = _ler_form(f)
        _guardar_contato(dados, f)
        if cid:
            _so_meu_ou_aprov(PrestContrato.get(cid))
            PrestContrato.atualizar(cid, dados)
            registrar('escrita.alterou_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid, depois={'empresa': dados['empresas'][0]['razao']})
        else:
            cid = PrestContrato.criar(dados['empresas'][0]['cliente_id'], dados, current_user.id, fin_contrato_id=f.get('fin_contrato_id', type=int))
            registrar('escrita.criou_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid,
                      depois={'empresa': dados['empresas'][0]['razao'], 'valor': dados['valor'], 'areas': dados['areas']})
        flash('Rascunho salvo. Confira o PDF e %s.' % ('envie para assinatura' if _aprovador() else 'envie para aprovação'), 'success')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    except ValueError as e:
        flash(str(e), 'danger')
        return redirect(request.referrer or url_for('financeiro.contratos'))


@financeiro.route('/comercial/contratos/<int:cid>')
@permission_required(MONTAR)
def contrato_ver(cid):
    c = PrestContrato.get(cid)
    if not _ve(c):
        abort(404)
    ass = PrestContrato.assinantes(cid)
    for a in ass:
        a['link'] = url_for('assinatura.assinar', token=a['token'], _external=True)
        a['fone'] = _fone_do_assinante(a, c)
        if c['status'] == 'ENVIADO' and not a['assinado_em']:
            a['texto_wa'] = _texto_whatsapp_assinatura(a, c, a['link'])
            a['wa_link'] = _wa_link(a['fone'], a['texto_wa'])
    cls, ico = STATUS_CLS.get(c['status'], ('tx-perd', 'fa-circle'))
    return render_template('financeiro/contrato_ver.html', c=c, assinantes=ass, ROTULO=ROTULO, aprov=_aprovador(), cls=cls, ico=ico,
                           eventos=_eventos(c, ass), n_assinados=sum(1 for a in ass if a['assinado_em']),
                           meu=c.get('criado_por') == current_user.id, montado=PrestContrato.montar(c['dados']), hoje=date.today(), AREAS=AREAS, brl=brl,
                           SIGN_Q=SIGNATARIOS_QUALICONTAX)


def _pdf_bytes(c):
    """PDF do contrato: sem registro enquanto não está ASSINADO; com a página de registro depois."""
    registro = None
    if c['status'] == 'ASSINADO':
        registro = [a for a in PrestContrato.assinantes(c['id']) if a['assinado_em']]
    return gerar_pdf(PrestContrato.montar(c['dados']), registro=registro, numero=c.get('numero'),
                     rodape='Contrato nº %s · %s' % (c.get('numero') or c['id'], 'assinado eletronicamente' if registro else 'minuta'))


@financeiro.route('/comercial/contratos/<int:cid>/pdf')
@permission_required(MONTAR)
def contrato_pdf(cid):
    c = PrestContrato.get(cid)
    if not _ve(c):
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


@financeiro.route('/comercial/contratos/<int:cid>/enviar', methods=['POST'])
@permission_required(MONTAR)
def contrato_enviar(cid):
    c = PrestContrato.get(cid)
    _so_meu_ou_aprov(c)
    if not c or c['status'] not in ('RASCUNHO', 'APROVADO', 'ENVIADO') or (c['status'] == 'RASCUNHO' and not _aprovador()):
        flash('Este contrato não pode ser enviado%s.' % (' antes de aprovado' if c and c['status'] in ('RASCUNHO', 'APROVACAO') else ''), 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    ass = PrestContrato.assinantes(cid)
    sem_email = [a['nome'] for a in ass if not a.get('email')]
    if sem_email:
        flash('Sem e-mail: %s. Todo mundo que assina precisa de e-mail.' % ', '.join(sem_email), 'danger')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    if c['status'] in ('RASCUNHO', 'APROVADO'):
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


@financeiro.route('/comercial/contratos/<int:cid>/cancelar', methods=['POST'])
@permission_required(MONTAR)
def contrato_cancelar(cid):
    c = PrestContrato.get(cid)
    _so_meu_ou_aprov(c)
    if c['status'] in ('APROVADO', 'ENVIADO') and not _aprovador():
        flash('Contrato já aprovado: só o Anderson ou a Alessandra cancelam.', 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    if not c or c['status'] == 'ASSINADO':
        flash('Contrato assinado não se cancela aqui: faça um distrato ou adendo.', 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    motivo = (request.form.get('motivo') or '').strip()[:400]
    if not motivo:                                    # regra do Anderson (22/11/2026): tudo que cancela precisa de motivo
        flash('Diga o motivo do cancelamento.', 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    c['dados']['cancelado'] = {'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': _agora().isoformat(timespec='minutes'), 'motivo': motivo}
    execute_query('UPDATE prest_contratos SET dados = %s WHERE id = %s', (__import__('json').dumps(c['dados'], ensure_ascii=False), cid), fetch=False)
    PrestContrato.mudar_status(cid, 'CANCELADO')
    registrar('escrita.cancelou_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid, antes={'status': c['status']}, depois={'status': 'CANCELADO', 'motivo': motivo})
    flash('Contrato cancelado, com o motivo registrado.', 'info')
    return redirect(url_for('financeiro.contrato_ver', cid=cid))


@financeiro.route('/comercial/contratos/<int:cid>/nota', methods=['POST'])
@permission_required(MONTAR)
def contrato_nota(cid):
    """Anotação da equipe na linha do tempo do contrato (só quem vê o contrato; fica em dados.notas)."""
    c = PrestContrato.get(cid)
    if not _ve(c):
        abort(404)
    texto = (request.form.get('texto') or '').strip()[:400]
    if texto:
        notas = c['dados'].setdefault('notas', [])
        notas.append({'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': _agora().isoformat(timespec='seconds'), 'texto': texto})
        execute_query('UPDATE prest_contratos SET dados = %s WHERE id = %s', (__import__('json').dumps(c['dados'], ensure_ascii=False), cid), fetch=False)
    return redirect(url_for('financeiro.contrato_ver', cid=cid))


@financeiro.route('/comercial/contratos/<int:cid>/pedir-aprovacao', methods=['POST'])
@permission_required(MONTAR)
def contrato_pedir_aprovacao(cid):
    c = PrestContrato.get(cid)
    _so_meu_ou_aprov(c)
    if c['status'] != 'RASCUNHO':
        flash('Só rascunho vai para aprovação.', 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    c['dados'].pop('reprovado', None)
    c['dados']['pedido_aprovacao'] = {'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': datetime.now().isoformat(timespec='minutes')}
    execute_query('UPDATE prest_contratos SET dados = %s WHERE id = %s', (__import__('json').dumps(c['dados'], ensure_ascii=False), cid), fetch=False)
    PrestContrato.mudar_status(cid, 'APROVACAO')
    registrar('escrita.pediu_aprovacao_contrato', 'financeiro', tabela='prest_contratos', registro_id=cid,
              depois={'empresa': c['dados']['empresas'][0]['razao'], 'valor': c['dados'].get('valor')})
    _avisar_aprovadores(PrestContrato.get(cid))
    flash('Enviado para aprovação. Anderson ou Alessandra conferem; aprovado, você envia para o cliente assinar.', 'success')
    return redirect(url_for('financeiro.contrato_ver', cid=cid))


@financeiro.route('/comercial/contratos/<int:cid>/aprovar', methods=['POST'])
@permission_required(APROVAR)
def contrato_aprovar(cid):
    """Aprovar: o honorário passa a valer (Anderson, 18/11/2026: "Na nossa aprovação, a aprovação ela tem a presunção
    para conferirmos se não está errado")."""
    c = PrestContrato.get(cid)
    if not c or c['status'] not in ('APROVACAO', 'RASCUNHO'):
        flash('Só contrato em rascunho ou aguardando aprovação pode ser aprovado.', 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    try:
        criados, avisos = _honorario_do_contrato(c, current_user.id)
    except ValueError as e:
        flash('Não aprovei: %s' % e, 'danger')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    acordos = _carteira_do_contrato(c, current_user.id, avisos)
    c['dados']['aprovacao'] = {'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': datetime.now().isoformat(timespec='minutes'),
                               'honorarios_criados': criados, 'avisos': avisos, 'acordos_carteira': acordos}
    if (c['dados'].get('grupo') or {}).get('autorizacao_necessaria'):
        c['dados']['autorizacao'] = {'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': datetime.now().isoformat(timespec='minutes'),
                                     'texto': 'Contrato em grupo com quadro de sócios diferente autorizado pelo escritório na aprovação'}
    execute_query('UPDATE prest_contratos SET dados = %s, fin_contrato_id = COALESCE(fin_contrato_id, %s) WHERE id = %s',
                  (__import__('json').dumps(c['dados'], ensure_ascii=False), criados[0] if criados else None, cid), fetch=False)
    PrestContrato.mudar_status(cid, 'APROVADO')
    registrar('escrita.aprovou_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid,
              depois={'empresa': c['dados']['empresas'][0]['razao'], 'valor': c['dados'].get('valor'), 'honorarios_criados': criados, 'avisos': avisos})
    flash('Contrato aprovado.%s%s Agora é enviar para o cliente assinar.' % (
        (' Honorário passou a valer (%d).' % len(criados)) if criados else '', (' ' + '; '.join(avisos) + '.') if avisos else ''), 'success')
    return redirect(url_for('financeiro.contrato_ver', cid=cid))


@financeiro.route('/comercial/contratos/<int:cid>/reprovar', methods=['POST'])
@permission_required(APROVAR)
def contrato_reprovar(cid):
    c = PrestContrato.get(cid)
    if not c or c['status'] != 'APROVACAO':
        flash('Só contrato aguardando aprovação pode ser devolvido.', 'warning')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    motivo = (request.form.get('motivo') or '').strip()[:500]
    c['dados']['reprovado'] = {'por': getattr(current_user, 'nome', ''), 'em': datetime.now().isoformat(timespec='minutes'), 'motivo': motivo}
    execute_query('UPDATE prest_contratos SET dados = %s WHERE id = %s', (__import__('json').dumps(c['dados'], ensure_ascii=False), cid), fetch=False)
    PrestContrato.mudar_status(cid, 'RASCUNHO')
    registrar('escrita.devolveu_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid, depois={'motivo': motivo})
    flash('Devolvido para quem montou, com o motivo. Ele acerta e manda de novo.', 'info')
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
    desafio = _desafio(a) if not erro else None
    cod_ok = bool(a.get('codigo_enviado_em')) and not a.get('assinado_em') and (not desafio or bool(session.get('ac_%s' % a['id'])))
    return render_template('assinar.html', a=a, c=c, erro=erro, todos=todos, montado=PrestContrato.montar(c['dados']), AREAS=AREAS, brl=brl,
                           codigo_pedido=cod_ok, desafio=desafio, email_mascarado=_mascara(a.get('email')))


def _cpf_digitos(a):
    return ''.join(ch for ch in str(a.get('cpf') or '') if ch.isdigit())


def _desafio(a):
    """Que parte do CPF pedir a quem assina: 'ini' (3 primeiros) ou 'fim' (2 últimos), sorteada por visita e guardada na sessão
    (mesmo molde da página do orçamento, 23/11/2026). None = assinante sem CPF no contrato (não dá para conferir: fica só o e-mail)."""
    if len(_cpf_digitos(a)) != 11:
        return None
    k = 'ad_%s' % a['id']
    if session.get(k) not in ('ini', 'fim'):
        import secrets
        session[k] = secrets.choice(('ini', 'fim'))
    return session[k]


def _confere_cpf(a, parte):
    """A parte digitada bate com o CPF de quem assina? Devolve o CPF inteiro (só dígitos) ou None."""
    parte = ''.join(ch for ch in str(parte or '') if ch.isdigit())
    tipo, cpf = _desafio(a), _cpf_digitos(a)
    if (tipo == 'ini' and len(parte) == 3 and cpf.startswith(parte)) or (tipo == 'fim' and len(parte) == 2 and cpf.endswith(parte)):
        return cpf
    return None


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
    _s = (execute_query('SELECT TIMESTAMPDIFF(SECOND, codigo_enviado_em, NOW()) AS s FROM prest_assinantes WHERE id = %s', (a['id'],), fetch=True, fetch_one=True) or {}).get('s')
    dados = request.get_json(silent=True) or {}
    if _desafio(a):
        cpf = _confere_cpf(a, dados.get('cpf_parte') or request.form.get('cpf_parte'))
        if not cpf:
            return jsonify({'ok': False, 'erro': 'Os dígitos não conferem com o CPF de quem assina. Confira e tente de novo, ou fale com a Qualicontax.'}), 400
        session['ac_%s' % a['id']] = cpf
    if _s is not None and _s < 60:      # relógio do banco (app em UTC)
        return jsonify({'ok': False, 'erro': 'Código enviado há menos de um minuto. Confira a caixa de entrada e o spam.'}), 429
    codigo = PrestContrato.gerar_codigo(a['id'])
    html = ('<p>Seu código para assinar o <b>%s nº %s</b>:</p><p style="font-size:28px;letter-spacing:6px;font-weight:800;">%s</p>'
            '<p>Vale por 30 minutos. Se não foi você, ignore.</p>' % (c['titulo'].capitalize(), c.get('numero') or c['id'], codigo))
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
    nome, codigo = (f.get('nome') or '').strip(), (f.get('codigo') or '').strip()
    cpf = session.get('ac_%s' % a['id']) or ''                 # o CPF conferido no passo 2 (quem assina não digita o CPF inteiro)
    if cpf:
        cpf = '%s.%s.%s-%s' % (cpf[:3], cpf[3:6], cpf[6:9], cpf[9:])
    elif _desafio(a):
        flash('Confirme primeiro os dígitos do CPF e peça o código.', 'danger')
        return redirect(url_for('assinatura.assinar', token=token))
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
    session.pop('ac_%s' % a['id'], None); session.pop('ad_%s' % a['id'], None)
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


# ---------------------------------------------------------------- caminho antigo (24/11/2026, Anderson: "os contratos é
# para aparecer somente no COMERCIAL e não no Financeiro"): tudo que era /financeiro/contratos... vive em /comercial/contratos...
@financeiro.route('/financeiro/contratos', defaults={'resto': ''})
@financeiro.route('/financeiro/contratos/<path:resto>')
def contratos_antigo(resto):
    url = '/comercial/contratos' + ('/' + resto if resto else '')
    if request.query_string:
        url += '?' + request.query_string.decode('utf-8', 'ignore')
    return redirect(url, code=301)
