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
import re
from datetime import date, datetime

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user

from models.fin_contrato import FinContrato
from models.prest_contrato import (AREAS, QUALICONTAX, SIGNATARIOS_QUALICONTAX, PrestContrato, brl)
from routes.financeiro import financeiro
from utils import email_envio
from utils.texto import nome_proprio
from models.reajuste_indice import ReajusteIndice
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


SITUACOES_REDE = ('PAGA', 'VIA_GRUPO', 'SEM_MOVIMENTO')


_SUFIXOS = re.compile(r'\b(LTDA|ME|EPP|EIRELI|S/?A|SA|SS|SOCIEDADE SIMPLES|SOCIEDADE INDIVIDUAL DE ADVOCACIA|MEI|LIMITADA)\.?$', re.I)
_CONECTIVOS = {'E', 'DE', 'DA', 'DO', 'DAS', 'DOS', '&'}


def _parte_do_nome(c):
    """"AR TRANSPORTES E LOGISTICA LTDA - ME" → "AR TRANSPORTES": a parte do nome que identifica a empresa
    (apelido da rede quando há; senão as 3 primeiras palavras da razão social, sem LTDA/ME/EPP e sem sobrar "E")."""
    d = c['dados']
    nome = (d.get('apelido') or '').strip() or ((d.get('empresas') or [{}])[0].get('razao') or '')
    nome = re.split(r'\s[–-]\s', nome)[0]
    for _ in range(3):
        nome = _SUFIXOS.sub('', nome).strip(' ,.-')
    palavras = [p for p in nome.split() if p][:3]
    while palavras and palavras[-1].upper() in _CONECTIVOS:
        palavras.pop()
    return ' '.join(palavras).upper() or 'CLIENTE'


def _nome_arquivo(c, sufixo=''):
    """Anderson (25/11/2026): "Contrato tem que sair como salvo: Contr. Prestação Qualicontax x PARTE DO NOME"."""
    return 'Contr. Prestação Qualicontax x %s%s.pdf' % (_parte_do_nome(c), sufixo)


def _disposition(nome, inline=True):
    """Content-Disposition com acento: filename= só ASCII (navegador antigo) e filename*= em UTF-8 (RFC 5987)."""
    import unicodedata
    from urllib.parse import quote
    ascii_ = unicodedata.normalize('NFKD', nome).encode('ascii', 'ignore').decode().replace('"', '')
    return "%s; filename=\"%s\"; filename*=UTF-8''%s" % ('inline' if inline else 'attachment', ascii_, quote(nome))


def _dig(s):
    return ''.join(ch for ch in str(s or '') if ch.isdigit())


def _mes_iso(v):
    """'2028-01' (do input type=month) ou None."""
    v = (v or '').strip()[:7]
    return v if len(v) == 7 and v[:4].isdigit() and v[4] == '-' and v[5:7].isdigit() and 1 <= int(v[5:7]) <= 12 else None
# como a situação da empresa no contrato vira a cobrança do cadastro (clientes.cobranca) na aprovação
_COBRANCA_DA_REDE = {'PAGA': 'PROPRIA', 'VIA_GRUPO': 'VIA_GRUPO', 'SEM_MOVIMENTO': 'SEM_MOVIMENTO'}


def _ler_form(f):
    """Formulário → ``dados`` (JSON guardado no contrato). Levanta ValueError com a mensagem para a tela."""
    empresas = []
    sits = f.getlist('emp_sit'); socios_cpf = {}
    for i, cid in enumerate(f.getlist('emp_cliente_id')):
        if not (cid or '').isdigit():
            continue
        e = PrestContrato.empresa(int(cid))
        if not e:
            continue
        socios_cpf[int(cid)] = {_dig(s.get('cpf')) for s in (e.get('socios') or []) if _dig(s.get('cpf'))}
        sit = (sits[i] if i < len(sits) else '').strip().upper()
        empresas.append({'cliente_id': int(cid), 'razao': e['nome_razao_social'], 'cnpj': e['cpf_cnpj'], 'numero': e['numero_cliente'],
                         'endereco': (f.getlist('emp_endereco')[i] if i < len(f.getlist('emp_endereco')) else '') or e['endereco'],
                         'valor': _dec_str(f.getlist('emp_valor')[i]) if i < len(f.getlist('emp_valor')) else None,
                         'situacao': sit if sit in SITUACOES_REDE else ''})
    if not empresas:
        raise ValueError('Escolha ao menos uma empresa.')
    # rede (25/11/2026 — Anderson: "tem que ser escolhido quem e qual CNPJ pagará; Sem movimento não paga, Via grupo também;
    # se forem empresas pagantes, ter a opção de distribuir")
    if len(empresas) == 1:
        empresas[0]['situacao'] = 'PAGA'
    elif any(not e['situacao'] for e in empresas):
        raise ValueError('Diga como paga cada empresa da rede: Paga, Via grupo ou Sem movimento.')
    pagantes = [e for e in empresas if e['situacao'] == 'PAGA']
    if not pagantes:
        raise ValueError('Escolha ao menos uma empresa que paga os honorários.')
    for e in empresas:
        if e['situacao'] != 'PAGA' or len(pagantes) == 1:
            e['valor'] = None
    if len(pagantes) > 1 and any(not e['valor'] or float(e['valor']) <= 0 for e in pagantes):
        raise ValueError('Com mais de uma empresa pagante, informe o valor de cada uma (ou use "Dividir igualmente").')
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
    if len(pagantes) > 1:                      # distribuído: a soma das pagantes é o honorário do contrato
        valor = '%.2f' % sum(float(e['valor']) for e in pagantes)
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
    # rede (25/11/2026 — Anderson): o cartão de assinatura lista TODAS as empresas pelas quais a pessoa assina e avisa
    # quando ela não consta no quadro de sócios de alguma
    if len(empresas) > 1:
        for a in assinantes:
            if a['papel'] == 'CONTRATANTE':
                a['empresas_assina'] = [{'razao': e['razao'], 'cnpj': e['cnpj'], 'socio': _dig(a.get('cpf')) in socios_cpf.get(e['cliente_id'], set())} for e in empresas]
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
    # testemunha do cliente (25/11/2026): pessoa física de Clientes ou Avulsos; precisa de e-mail para assinar pelo link
    n0 = len([a for a in assinantes if a['papel'] == 'TESTEMUNHA'])
    for n, pid in enumerate(f.getlist('testemunha_pf'), n0 + 1):
        if not (pid or '').isdigit():
            continue
        p = PrestContrato.pessoa(int(pid))
        if not p:
            raise ValueError('A testemunha escolhida precisa estar cadastrada como pessoa física (Clientes ou Avulsos).')
        if not p.get('email'):
            raise ValueError('A testemunha %s não tem e-mail no cadastro.' % nome_proprio(p['nome']))
        assinantes.append({'papel': 'TESTEMUNHA', 'ordem': n, 'nome': p['nome'], 'cpf': p['cpf'], 'documento': p.get('documento') or '',
                           'email': p['email'], 'pf_cliente_id': p['id'], 'celular': p.get('celular') or ''})
    if len([a for a in assinantes if a['papel'] == 'TESTEMUNHA']) > 2:
        raise ValueError('No máximo duas testemunhas.')
    rt = f.get('reajuste_tipo') if f.get('reajuste_tipo') in ('SALARIO_MINIMO', 'INDICE', 'DATA_PROPRIA', 'ESCALONADO', 'SEM') else 'SALARIO_MINIMO'
    dados = {
        'empresas': empresas, 'apelido': (f.get('apelido') or '').strip().upper() or None, 'areas': areas, 'servicos_extras': extras,
        'valor': valor, 'valores_por_empresa': len(pagantes) > 1, 'dia_vencimento': int(f.get('dia_vencimento') or 10),
        # ramos cuja mensalidade sugerida a pessoa trouxe para o valor (24/11/2026): fica registrado como o valor foi composto
        'sugestoes': [{'ramo_id': int(x.split('|')[0]), 'nome': x.split('|')[1][:60], 'valor': x.split('|')[2]} for x in f.getlist('sugestao_ramo') if x.count('|') == 2 and x.split('|')[0].isdigit()],
        'reajuste_tipo': rt, 'reajuste_indice': (f.get('reajuste_indice') or '').strip().upper() or None if rt in ('INDICE', 'DATA_PROPRIA') else None,
        'reajuste_mes': (f.get('reajuste_mes') or '').strip().lower() or None if rt == 'DATA_PROPRIA' else None,
        'reajuste_proximo': _mes_iso(f.get('reajuste_proximo')) if rt in ('SALARIO_MINIMO', 'INDICE', 'DATA_PROPRIA') else None,
        'extras_cobrados': bool(f.get('extras_cobrados')),
        'reajuste_escala': (f.get('reajuste_escala') or '').strip() or None if rt == 'ESCALONADO' else None,
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
            # 24/11/2026 (Anderson: "acesso sempre por perfil"): quem aprova vem do PERFIL, não mais de usuario_permissoes_extra (zerada)
            """SELECT DISTINCT u.email FROM usuarios u WHERE u.email LIKE '%%@%%' AND u.situacao = 'ATIVO' AND (u.tipo_usuario = 'ADMIN'
                  OR u.id IN (SELECT up.usuario_id FROM usuario_perfis up JOIN perfis_acesso pa ON pa.id = up.perfil_id AND pa.situacao = 'ATIVO'
                               JOIN perfil_permissoes pp ON pp.perfil_id = up.perfil_id WHERE pp.permissao_codigo = %s))""",
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


def _bancos_cobranca():
    """Bancos que emitem boleto (mesma lista da tela de Honorários), com o padrão marcado."""
    try:
        from utils.honorarios_painel import contas
        return contas()
    except Exception:
        logger.exception('[contratos] bancos da cobrança')
        return []


def _banco_escolhido(valor):
    """A conta de cobrança que o aprovador escolheu (id de fin_cobranca_contas), ou None."""
    if not str(valor or '').isdigit():
        return None
    return next((b for b in _bancos_cobranca() if int(b['id']) == int(valor)), None)


def _honorario_do_contrato(c, aprovado_por, banco=None):
    """Na APROVAÇÃO o honorário passa a valer: cria o contrato de honorários (fin_contratos) ATIVO de cada empresa
    que ainda não tem um vigente. Quem já tem fica como está (devolve o aviso). Devolve (criados, avisos).
    ``banco``: a conta de cobrança escolhida na aprovação (26/11/2026); fora do padrão, grava em banco_boleto como a tela de Honorários."""
    from models.fin_contrato import REAJUSTE_TIPOS
    d = c['dados']
    try:
        inicio = date.fromisoformat((d.get('inicio') or '')[:10])
    except ValueError:
        inicio = date.today()
    reaj = d.get('reajuste_tipo') if d.get('reajuste_tipo') in REAJUSTE_TIPOS else 'SALARIO_MINIMO'
    empresas = d.get('empresas') or []
    pagantes = [e for e in empresas if (e.get('situacao') or 'PAGA') == 'PAGA']     # contrato antigo sem situação: todas pagam
    if d.get('valores_por_empresa') and len(pagantes) > 1:
        alvos = [(e['cliente_id'], e.get('valor') or '0', e['razao']) for e in pagantes]
    else:
        alvos = [(pagantes[0]['cliente_id'], d.get('valor'), pagantes[0]['razao'])] if pagantes else []
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
                                reajuste_obs=d.get('reajuste_escala') or d.get('reajuste_indice'), criado_por=c['criado_por'], aprovado_por=aprovado_por,
                                proximo_reajuste=date(int(d['reajuste_proximo'][:4]), int(d['reajuste_proximo'][5:7]), 1) if d.get('reajuste_proximo') else None,
                                observacao='Do contrato de prestação nº %s, aprovado no sistema' % (c.get('numero') or c['id']))
        if banco and not banco.get('padrao'):        # vazio = o padrão (mesma convenção de Honorários)
            FinContrato.atualizar(fid, {'banco_boleto': str(banco['id'])})
        criados.append(fid)
    return criados, avisos


def _cobranca_do_contrato(c, avisos):
    """Na aprovação, cada empresa do contrato fica no cadastro com a cobrança que o contrato diz (25/11/2026): quem paga
    → PROPRIA; VIA_GRUPO aponta a pagante principal em cobranca_pagador_id; SEM_MOVIMENTO fica sem cobrança até
    movimentar. É o que dá o filtro em Honorários › Sem honorários e em Clientes. Devolve [(cliente_id, de, para)]."""
    d = c['dados']
    empresas = d.get('empresas') or []
    pagantes = [e for e in empresas if (e.get('situacao') or 'PAGA') == 'PAGA']
    principal = pagantes[0]['cliente_id'] if pagantes else None
    mudou = []
    for e in empresas:
        sit = e.get('situacao') or 'PAGA'
        para = _COBRANCA_DA_REDE.get(sit, 'PROPRIA')
        pagador = principal if sit == 'VIA_GRUPO' else None
        atual = execute_query('SELECT cobranca, cobranca_pagador_id FROM clientes WHERE id = %s', (e['cliente_id'],), fetch=True, fetch_one=True) or {}
        de = atual.get('cobranca') or 'PENDENTE'
        if de == para and (atual.get('cobranca_pagador_id') or None) == pagador:
            continue
        execute_query('UPDATE clientes SET cobranca = %s, cobranca_pagador_id = %s, alterado_por = %s, alterado_em = CURRENT_TIMESTAMP WHERE id = %s',
                      (para, pagador, current_user.id, e['cliente_id']), fetch=False)
        registrar('escrita.alterou_cliente', 'cadastros', tabela='clientes', registro_id=e['cliente_id'],
                  antes={'cobranca': de}, depois={'cobranca': para, 'origem': 'contrato de prestação nº %s aprovado' % (c.get('numero') or c['id'])})
        mudou.append((e['cliente_id'], de, para))
    if mudou:
        from models.cliente import COBRANCA_CURTA
        avisos.append('Cobrança no cadastro: ' + '; '.join('%s → %s' % (next((x['razao'] for x in empresas if x['cliente_id'] == cid), cid), COBRANCA_CURTA.get(para, para)) for cid, _, para in mudou))
    return mudou


def _dados_da_proposta(p):
    """A proposta aceita vira o ponto de partida do contrato (25/11/2026): mesmos dados, com os assinantes do contrato de volta."""
    d = dict(p.get('dados') or {})
    d['assinantes'] = d.get('assinantes_contrato') or []
    d.pop('titulo', None); d.pop('aprovacao', None); d.pop('pedido_aprovacao', None); d.pop('reprovado', None); d.pop('notas', None); d.pop('cancelado', None)
    d['inicio'] = date.today().isoformat()
    return d


def _form_ctx(cliente_id=None, fin_contrato_id=None, contrato=None, mais=None, proposta=None):
    """O que a tela do formulário precisa: empresa(s), sócios, honorários, testemunhas, signatários da Qualicontax.
    ``proposta``: o contrato nasce de uma proposta de serviços aceita, com tudo preenchido."""
    d = _dados_da_proposta(proposta) if proposta else ((contrato or {}).get('dados') or {})
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
    # empresas com que a tela nasce: a escolhida e, num contrato em grupo, as outras marcadas na caixa (cada uma com o honorário que tiver)
    emps_ini = []
    if emp and not contrato:
        emps_ini.append({'cliente_id': emp['id'], 'razao': emp['nome_razao_social'], 'numero': emp['numero_cliente'], 'cnpj': emp['cpf_cnpj'],
                         'endereco': emp['endereco'], 'valor': (fin or {}).get('valor') or '', 'situacao': 'PAGA'})
        for cid2 in (mais or []):
            e2 = PrestContrato.empresa(cid2)
            if not e2:
                continue
            f2 = (FinContrato.por_cliente(cid2) or [None])[0] if (_aprovador() or cid2 in _escopo()) else None
            emps_ini.append({'cliente_id': e2['id'], 'razao': e2['nome_razao_social'], 'numero': e2['numero_cliente'], 'cnpj': e2['cpf_cnpj'],
                             'endereco': e2['endereco'], 'valor': (f2 or {}).get('valor') or '', 'situacao': ''})
    pct = (execute_query('SELECT carteira_pct FROM usuarios WHERE id = %s', (current_user.id,), fetch=True, fetch_one=True) or {}).get('carteira_pct')
    # contrato novo nasce preenchido com o sócio administrador do cadastro (22/11/2026 — Anderson: "os campos quase nada preenchido, e está tudo no cadastro")
    rep0 = None
    if emp and not contrato and emp.get('socios'):
        s0 = emp['socios'][0]
        rep0 = {'rep_nome': (s0.get('nome') or '').strip(), 'rep_cpf': s0.get('cpf') or '', 'rep_documento': s0.get('documento') or '',
                'rep_email': s0.get('email') or '', 'rep_cargo': s0.get('cargo') or 'sócio administrador', 'rep_celular': s0.get('celular') or ''}
    return {'emp': emp, 'fin': fin, 'dados': d, 'contrato': contrato, 'AREAS': AREAS, 'SIGN_Q': PrestContrato.signatarios_qualicontax(),
            'contratada': PrestContrato.contratada(), 'rep0': rep0, 'emps_ini': emps_ini,
            'indices': [dict(i) for i in ReajusteIndice.listar(so_ativos=True)],
            'testemunhas': PrestContrato.testemunhas_disponiveis(), 'hoje': date.today(),
            'carteira_pct': float(pct) if pct else None, 'de_proposta': proposta}


@financeiro.route('/comercial/contratos/socios-iguais')
@permission_required(MONTAR)
def contrato_socios_iguais():
    """Para a tela avisar na hora: as empresas escolhidas têm o mesmo quadro de sócios?"""
    ids = [x for x in (request.args.get('ids') or '').split(',') if x.isdigit()]
    iguais, detalhe = PrestContrato.socios_iguais(ids)
    return jsonify({'iguais': iguais, 'detalhe': detalhe, 'n': len(ids)})


@financeiro.route('/comercial/contratos/empresas')
@permission_required(MONTAR)
def contrato_empresas():
    """Outra empresa para o contrato em grupo (nome, CNPJ/CPF ou número). 26/11/2026: o formulário usava a busca do tomador
    da NFS-e (/financeiro/notas/buscar), que quem só monta contrato não pode abrir; esta é a mesma busca, pela porta do Comercial,
    e sem as empresas reservadas."""
    from models.nfse_emissao import buscar_clientes
    return jsonify(buscar_clientes(request.args.get('q') or ''))


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
        r['areas'] = [nome_proprio(AREAS[a][0]) for a in (d.get('areas') or []) if a in AREAS]
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
        cli.append({'id': s['id'], 'num': str(s['numero_cliente'] or ''), 'nome': s['nome_razao_social'], 'doc': s['cpf_cnpj'] or '', 'fant': s.get('nome_fantasia') or '',
                    'cid': s.get('cidade') or '', 'av': bool(s['avulso']), 'mensal': float(s['valor'] or 0) if (aprov or s['id'] in escopo) else 0.0})
    return render_template('financeiro/contratos.html', aba=aba, lista=lista, cont=cont, pessoas=pessoas, ROTULO=ROTULO, aprov=aprov,
                           clientes_json=json.dumps(cli, ensure_ascii=False), n_prontas=len(cli),
                           pend_empresas=pr['pendentes_empresas'], pend_avulsos=pr['pendentes_avulsos'], hoje=date.today())


@financeiro.route('/comercial/contratos/novo')
@permission_required(MONTAR)
def contrato_novo():
    """Só a partir de um cadastro completo: sem cliente_id, ou com pendência em A CADASTRAR, volta para a lista."""
    cid = request.args.get('cliente_id', type=int)
    pid = request.args.get('proposta_id', type=int)
    if pid:                                   # converter a proposta de serviços (25/11/2026): o formulário abre preenchido
        p = PrestContrato.get(pid)
        if not p or p.get('tipo') != 'PROPOSTA' or p['status'] not in ('ACEITO', 'APROVADO', 'ENVIADO'):
            flash('Só proposta aceita (ou aprovada) vira contrato.', 'warning')
            return redirect(url_for('comercial.propostas'))
        _so_meu_ou_aprov(p)
        return render_template('financeiro/contrato_form.html', **_form_ctx(proposta=p))
    if not cid:
        flash('O contrato nasce do cadastro: clique em Novo contrato e escolha a empresa. Nada é digitado do zero.', 'warning')
        return redirect(url_for('financeiro.contratos'))
    # 25/11/2026 — Anderson: "permitir que selecionemos mais de uma empresa para o contrato": as outras vêm em &mais=
    mais = []
    for x in (request.args.get('mais') or '').split(','):
        if x.strip().isdigit() and int(x) != cid and int(x) not in mais:
            mais.append(int(x))
    imp = PrestContrato.impedimentos([cid] + mais)
    if imp:
        for k, motivo in imp.items():
            emp = PrestContrato.empresa(k)
            flash('%s não pode receber contrato — %s.' % ((emp or {}).get('nome_razao_social') or 'A empresa', motivo), 'warning')
        return redirect(url_for('financeiro.contratos'))
    return render_template('financeiro/contrato_form.html', **_form_ctx(cid, request.args.get('fin_contrato_id', type=int), mais=mais))


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
            pid = f.get('proposta_id', type=int)
            if pid:
                dados['de_proposta'] = {'id': pid, 'numero': (PrestContrato.get(pid) or {}).get('numero')}
            cid = PrestContrato.criar(dados['empresas'][0]['cliente_id'], dados, current_user.id, fin_contrato_id=f.get('fin_contrato_id', type=int), anterior_id=pid)
            registrar('escrita.criou_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid,
                      depois={'empresa': dados['empresas'][0]['razao'], 'valor': dados['valor'], 'areas': dados['areas'], 'proposta_id': pid})
            if pid:                            # a proposta fica CONVERTIDA e aponta para o contrato (25/11/2026)
                from routes.comercial_propostas import marcar_convertida
                marcar_convertida(pid, PrestContrato.get(cid))
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
                           SIGN_Q=SIGNATARIOS_QUALICONTAX, bancos=_bancos_cobranca() if c['status'] in ('APROVACAO', 'RASCUNHO') else [])


def _pdf_bytes(c):
    """PDF do contrato: sem registro enquanto não está ASSINADO; com a página de registro depois."""
    registro = None
    if c['status'] == 'ASSINADO':
        registro = [a for a in PrestContrato.assinantes(c['id']) if a['assinado_em']]
        por_quem = {(x['papel'], int(x.get('ordem') or 1)): x.get('empresas_assina') for x in (c['dados'].get('assinantes') or [])}
        for a in registro:                      # rede: o registro também lista as empresas pelas quais a pessoa assinou
            a['empresas_assina'] = por_quem.get((a['papel'], int(a.get('ordem') or 1)))
    return gerar_pdf(PrestContrato.montar(c['dados']), registro=registro, numero=c.get('numero'),
                     rodape='Contrato nº %s · %s' % (c.get('numero') or c['id'], 'assinado eletronicamente' if registro else 'minuta'))


@financeiro.route('/comercial/contratos/<int:cid>/pdf')
@permission_required(MONTAR)
def contrato_pdf(cid):
    c = PrestContrato.get(cid)
    if not _ve(c):
        abort(404)
    b = _pdf_bytes(c)
    return Response(b, mimetype='application/pdf', headers={'Content-Disposition': _disposition(_nome_arquivo(c), inline=not request.args.get('baixar'))})


def _esc_html(s):
    return str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ---------------------------------------------------------------- e-mails do contrato
# 25/11/2026 — Anderson: "e-mail HORRÍVEL, reformular". Os três e-mails do contrato (convite para assinar, código e via
# assinada) saem no mesmo molde dos e-mails de cobrança e orçamento (templates/emails/orcamento.html): logo, faixa verde
# com a empresa, saudação, números grandes, botão, passos numerados, slogan e rodapé.
PAPEL_TXT = {'CONTRATANTE': 'contratante', 'CONTRATADA': 'pela Qualicontax', 'TESTEMUNHA': 'testemunha'}
PAPEL_INTRO = {'CONTRATANTE': 'como <b>contratante</b>', 'CONTRATADA': 'pela Qualicontax, como <b>contratada</b>', 'TESTEMUNHA': 'como <b>testemunha</b>'}   # 25/11/2026, Anderson: "dá uma frisada nas palavras"


def _doc_txt(doc):
    dig = re.sub(r'\D', '', doc or '')
    return ('CPF ' + format_cpf(dig)) if len(dig) == 11 else (('CNPJ ' + format_cnpj(dig)) if len(dig) == 14 else (doc or ''))


def _email_contrato(c, **extra):
    """(html, cabeçalho) do e-mail de um contrato no molde da casa; ``extra`` completa ou troca as partes (saudacao, intro,
    kpis, botao, passos, aviso, codigo, anexos_txt, rodape)."""
    from utils.cobranca_email import _env
    dd = c['dados']; emps = dd.get('empresas') or []; emp = emps[0] if emps else {}
    numero = c.get('numero') or str(c['id'])
    d = {'empresa': 'Qualicontax', 'data_txt': date.today().strftime('%d/%m/%Y'), 'eyebrow': 'Assinatura eletrônica · contrato nº %s' % numero,
         'titulo': nome_proprio(c['titulo'] or 'Contrato de prestação de serviços'),
         'cliente_rotulo': 'Contratante' + ((' · e mais %d empresa%s da rede' % (len(emps) - 1, 's' if len(emps) > 2 else '')) if len(emps) > 1 else ''),
         'cliente_nome': nome_proprio(emp.get('razao') or ''), 'cliente_doc': _doc_txt(emp.get('cnpj')),
         'saudacao': 'Olá!', 'intro': '', 'codigo': '', 'kpis': [], 'itens': [], 'total': '', 'botao': None, 'passos': [], 'aviso': '', 'anexos_txt': '',
         'slogan': 'a EMPRESA é sua, a PARCERIA é NOSSA',
         'rodape': 'Enviado por Qualicontax Assessoria Contábil. Em caso de dúvida, responda este e-mail. Se não reconhece este documento, pode ignorar.'}
    d.update(extra)
    return _env().get_template('orcamento.html').render(d=d), d


def _quem_assina(c):
    ass = PrestContrato.assinantes(c['id'])
    partes = []
    for a in ass:
        partes.append('%s (<b>%s</b>%s)' % (_esc_html(nome_proprio(a['nome'])), PAPEL_TXT.get(a['papel'], a['papel'].lower()), ' · já assinou' if a.get('assinado_em') else ''))
    from markupsafe import Markup
    return Markup('Assinam este contrato: ' + '; '.join(partes) + '.') if partes else ''


def _email_assinatura(a, c, link):
    dd = c['dados']; emp = (dd.get('empresas') or [{}])[0]
    primeiro = nome_proprio((a.get('nome') or '').split()[0]) if (a.get('nome') or '').strip() else ''
    from markupsafe import Markup
    papel = PAPEL_INTRO.get(a['papel'], 'como <b>' + _esc_html(a['papel'].lower()) + '</b>')
    kpis = [('Contrato nº', c.get('numero') or str(c['id']))]
    try:
        if float(_dec_str(dd.get('valor'))) > 0:
            kpis.append(('Honorários mensais', brl(_dec_str(dd.get('valor')))))
    except (TypeError, ValueError):
        pass
    inicio = _data(dd.get('inicio'))
    if inicio:
        kpis.append(('Início', inicio.strftime('%d/%m/%Y')))
    html, d = _email_contrato(
        c, saudacao=('Olá, %s!' % primeiro) if primeiro else 'Olá!',
        intro=Markup('A Qualicontax preparou o contrato acima e ele está pronto para a sua assinatura eletrônica, %s. '
                     'Não precisa imprimir nem reconhecer firma: leva menos de dois minutos, pelo celular ou pelo computador.' % papel),
        kpis=kpis, botao=('Ver e assinar o contrato', link),
        passos=[('Abra o contrato.', 'Toque no botão acima e leia o documento completo; dá para baixar o PDF ali mesmo.'),
                ('Peça o código.', 'Ao tocar em Assinar, um código de 6 dígitos chega neste mesmo e-mail e vale por 30 minutos.'),
                ('Confirme.', 'Digite o código, o seu nome e o seu CPF. Quando todos assinarem, a via assinada chega por e-mail para cada um.')],
        aviso=_quem_assina(c),
        rodape='Enviado por Qualicontax Assessoria Contábil. O link deste e-mail é pessoal e só serve para você: não repasse. '
               'Em caso de dúvida, responda este e-mail. Se não reconhece este documento, pode ignorar.')
    sem_tags = lambda t: re.sub(r'<[^>]+>', '', str(t))          # a versão só texto do e-mail vai sem o negrito
    texto = '\n'.join([d['eyebrow'], d['titulo'] + ' · ' + d['cliente_nome'], '', d['saudacao'], sem_tags(d['intro']), '',
                       'Ver e assinar o contrato: ' + link, ''] + ['%d. %s %s' % (i + 1, t, x) for i, (t, x) in enumerate(d['passos'])] + ['', sem_tags(d['aviso']), '', d['rodape']])
    return _enviar([a['email']], 'Para assinar: %s · %s' % (d['titulo'], d['cliente_nome']), html, texto)


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
        flash('Contrato já aprovado: só quem aprova pode cancelar.', 'warning')
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
    flash('Enviado para aprovação. Aprovado, você recebe aviso e envia para o cliente assinar.', 'success')
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
    # 26/11/2026 — Anderson: "na aprovação do contrato tenho que ter aqui só o Aprovador escolher qual banco será cobrado,
    # com as logos... se não escolher não prossegue... senão tenho que ir no Honorário e fazer isso"
    banco = _banco_escolhido(request.form.get('banco'))
    if not banco:
        flash('Não aprovei: escolha o banco que vai cobrar este contrato.', 'danger')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    try:
        criados, avisos = _honorario_do_contrato(c, current_user.id, banco=banco)
    except ValueError as e:
        flash('Não aprovei: %s' % e, 'danger')
        return redirect(url_for('financeiro.contrato_ver', cid=cid))
    acordos = _carteira_do_contrato(c, current_user.id, avisos)
    cobrancas = _cobranca_do_contrato(c, avisos)
    c['dados']['aprovacao'] = {'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': datetime.now().isoformat(timespec='minutes'),
                               'honorarios_criados': criados, 'avisos': avisos, 'acordos_carteira': acordos, 'cobrancas': cobrancas,
                               'banco': {'id': banco['id'], 'nome': banco['nome'], 'cod': banco.get('cod')}}
    if (c['dados'].get('grupo') or {}).get('autorizacao_necessaria'):
        c['dados']['autorizacao'] = {'por': current_user.id, 'nome': getattr(current_user, 'nome', ''), 'em': datetime.now().isoformat(timespec='minutes'),
                                     'texto': 'Contrato em grupo com quadro de sócios diferente autorizado pelo escritório na aprovação'}
    execute_query('UPDATE prest_contratos SET dados = %s, fin_contrato_id = COALESCE(fin_contrato_id, %s) WHERE id = %s',
                  (__import__('json').dumps(c['dados'], ensure_ascii=False), criados[0] if criados else None, cid), fetch=False)
    PrestContrato.mudar_status(cid, 'APROVADO')
    registrar('escrita.aprovou_contrato_prestacao', 'financeiro', tabela='prest_contratos', registro_id=cid,
              depois={'empresa': c['dados']['empresas'][0]['razao'], 'valor': c['dados'].get('valor'), 'honorarios_criados': criados, 'avisos': avisos})
    flash('Contrato aprovado.%s%s Agora é enviar para o cliente assinar.' % (
        (' Honorário passou a valer (%d), cobrado pelo %s.' % (len(criados), banco['nome'])) if criados else '', (' ' + '; '.join(avisos) + '.') if avisos else ''), 'success')
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
        return render_template('assinar.html', erro=erro, qc=PrestContrato.contratada()), 404
    todos = PrestContrato.assinantes(c['id'])
    desafio = _desafio(a) if not erro else None
    cod_ok = bool(a.get('codigo_enviado_em')) and not a.get('assinado_em') and (not desafio or bool(session.get('ac_%s' % a['id'])))
    return render_template('assinar.html', qc=PrestContrato.contratada(), a=a, c=c, erro=erro, todos=todos, montado=PrestContrato.montar(c['dados']), AREAS=AREAS, brl=brl,
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
    return Response(_pdf_bytes(c), mimetype='application/pdf', headers={'Content-Disposition': _disposition(_nome_arquivo(c))})


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
    primeiro = nome_proprio((a.get('nome') or '').split()[0]) if (a.get('nome') or '').strip() else ''
    html, d = _email_contrato(c, saudacao=('Olá, %s!' % primeiro) if primeiro else 'Olá!', codigo=codigo,
                              intro='Aqui está o seu código para assinar o contrato acima. Volte à página do contrato e digite os 6 dígitos.',
                              rodape='Enviado por Qualicontax Assessoria Contábil. O código vale por 30 minutos e só serve para você. Se não foi você que pediu, ignore este e-mail.')
    ok, err = _enviar([a['email']], 'Seu código para assinar: %s' % codigo, html, 'Seu código para assinar o contrato nº %s: %s (vale 30 minutos).' % (c.get('numero') or c['id'], codigo))
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
    nome, codigo = (a.get('nome') or '').strip(), (f.get('codigo') or '').strip()      # 25/11/2026: o nome é o de quem assina; não se digita
    cpf = session.get('ac_%s' % a['id']) or ''                 # o CPF conferido no passo 2 (quem assina não digita o CPF inteiro)
    if cpf:
        cpf = '%s.%s.%s-%s' % (cpf[:3], cpf[3:6], cpf[6:9], cpf[9:])
    elif _desafio(a):
        flash('Confirme primeiro os dígitos do CPF e peça o código.', 'danger')
        return redirect(url_for('assinatura.assinar', token=token))
    if not f.get('aceito') or not nome:
        flash('Marque "li e aceito" para assinar.', 'danger')
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
    nome_arq = _nome_arquivo(c, ' - %s' % (c.get('numero') or c['id']))     # no Dropbox e no anexo o nº evita duas vias com o mesmo nome
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
    html, d = _email_contrato(c, eyebrow='Contrato assinado · nº %s' % (c.get('numero') or c['id']), saudacao='Olá!',
                              intro='O contrato acima foi assinado eletronicamente por todas as partes. A via assinada, com o registro de cada assinatura, está em anexo neste e-mail. Guarde-a com você.',
                              aviso=_quem_assina(c), anexos_txt='Em anexo: %s' % nome_arq)
    _enviar(para, 'Contrato assinado · %s' % d['cliente_nome'], html, 'Contrato nº %s assinado por todas as partes. Via assinada em anexo (%s).' % (c.get('numero') or c['id'], nome_arq),
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
