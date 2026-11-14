# -*- coding: utf-8 -*-
"""Cobrança — contas de cobrança (Efí, Bradesco) e boletos — 02/11/2026.

Mora no blueprint ``financeiro`` (mesma navegação e chips de empresa), atrás de
``financeiro.cobranca``, que nasce sem perfil: só o admin entra até marcar.
Importado no fim de ``routes/financeiro.py``, como ``financeiro_notas``.

O webhook da Efí é um blueprint PRÓPRIO (``cobranca_webhook``), público para o
portão (``utils/acesso.py``): a Efí não tem login. O que a protege é o segredo
aleatório na URL, um por conta de cobrança, e o fato de que a URL só recebe um
token — os dados vêm de uma consulta nossa à Efí, autenticada.

Decisões do Anderson (02/11/2026): tudo pelo nosso app (a Efí não manda
e-mail), Pix no boleto, webhook obrigatório (é ele que dá a baixa no título),
credenciais cifradas no banco por empresa, nunca no Railway.
"""
import json
import logging
from datetime import date, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from models.fin_cobranca import AMBIENTES, AVISA_POR, PROVEDORES, FinBoleto, FinCobrancaConta
from models.fin_empresa import FinEmpresa
from models.nfse_emissao import MESES, NfseEmitida, preencher_modelo, tomador_do_cliente
from routes.financeiro import _empresas_ctx, financeiro
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query
from utils.integrations.efi.client import SITUACOES, EfiErro
from utils import efi_boletos
from utils import bradesco_boletos
from utils.integrations.bradesco.client import BradescoErro

logger = logging.getLogger(__name__)
PERM = 'financeiro.cobranca'
CATEGORIA_HONORARIOS = 1          # fin_categorias: Receita de honorários → Honorários contábeis

cobranca_webhook = Blueprint('cobranca_webhook', __name__)
# Links do e-mail de cobranca (04/11/2026): sem login, o codigo do boleto e a chave. Publico
# em utils/acesso.py. Anota a hora em que o cliente abriu e segue para a Efi / entrega a nota.
cobranca_publica = Blueprint('cobranca_publica', __name__)


def _emp_ok(emp_raw):
    return emp_raw.isdigit() and int(emp_raw) in FinEmpresa.ids_validos()


# ---------------------------------------------------------------------------
# Tela: contas de cobrança + boletos
# ---------------------------------------------------------------------------
def _volta_contas(emp_id=None):
    """As contas de cobrança moram em Contas Bancárias, aba Cobrança (04/11/2026)."""
    return url_for('financeiro.contas', emp=emp_id) + '#cobranca'


@financeiro.route('/financeiro/cobranca')
@permission_required(PERM)
def cobranca():
    """Endereço antigo: as contas de cobrança foram para Contas → Cobrança; os boletos, para Cobranças."""
    emp = request.args.get('emp') or None
    if request.args.get('situacao') or request.args.get('busca') or request.args.get('de'):
        return redirect(url_for('financeiro.cobrancas', ver='boletos', **{k: v for k, v in request.args.items()}))
    return redirect(_volta_contas(emp))


@financeiro.route('/financeiro/cobranca/salvar', methods=['POST'])
@permission_required(PERM)
def cobranca_salvar():
    emp_raw = request.form.get('empresa_id', '')
    if not _emp_ok(emp_raw):
        abort(400)
    emp_id = int(emp_raw)
    cid = request.form.get('conta_id', '')
    cid = int(cid) if cid.isdigit() else None
    if cid and (FinCobrancaConta.get(cid) or {}).get('empresa_id') != emp_id:
        abort(404)
    arq = request.files.get('certificado')
    cert_bytes = arq.read() if arq and arq.filename else None
    if cert_bytes and len(cert_bytes) > 200_000:
        flash('O certificado da API deve ser o .p12 da Efí (poucos KB).', 'danger')
        return redirect(_volta_contas(emp_id))
    dados = {k: request.form.get(k, '') for k in FinCobrancaConta.CAMPOS_TEXTO}
    novo_id, erro = FinCobrancaConta.salvar(
        emp_id, dados, conta_id=cid, usuario_id=current_user.id,
        client_id=request.form.get('client_id') or None, client_secret=request.form.get('client_secret') or None,
        certificado=cert_bytes, certificado_nome=(arq.filename if cert_bytes else None),
        certificado_senha=request.form.get('certificado_senha') or '',
        email_senha=request.form.get('email_senha') or None)
    if erro:
        flash(erro, 'danger')
    else:
        registrar('escrita.salvou_conta_cobranca', 'financeiro', tabela='fin_cobranca_contas', registro_id=novo_id,
                  depois={'empresa_id': emp_id, 'apelido': dados['apelido'], 'provedor': dados['provedor'],
                          'ambiente': dados['ambiente'], 'trocou_segredo': bool(request.form.get('client_secret')),
                          'trocou_certificado': bool(cert_bytes), 'trocou_senha_email': bool(request.form.get('email_senha')),
                          'email_remetente': dados.get('email_remetente'), 'avisa_por': dados.get('avisa_por')})
        flash(f'Conta de cobrança "{dados["apelido"].strip()}" salva. Clique em Testar para provar as credenciais'
              + (' e em Testar e-mail para provar a caixa de e-mail.' if dados.get('email_remetente') else '.'), 'success')
    return redirect(_volta_contas(emp_id))


@financeiro.route('/financeiro/cobranca/<int:conta_id>/testar', methods=['POST'])
@permission_required(PERM)
def cobranca_testar(conta_id):
    c = FinCobrancaConta.get(conta_id)
    if not c or c['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    try:
        if c['provedor'] == 'bradesco':
            brad, _ = FinCobrancaConta.cliente_bradesco(conta_id)
            resultado = 'OK: ' + brad.testar()
        else:
            efi, _ = FinCobrancaConta.cliente_efi(conta_id)
            resultado = 'OK: ' + efi.testar()
        flash(f'{c["apelido"]}: {resultado}', 'success')
    except (EfiErro, BradescoErro, ValueError) as exc:
        resultado = f'FALHOU: {exc}'
        flash(f'{c["apelido"]}: {resultado}', 'danger')
    except Exception as exc:                      # chave mestra, rede
        logger.exception('[cobranca] teste da conta %s', conta_id)
        resultado = f'FALHOU: {exc}'
        flash(f'{c["apelido"]}: {resultado}', 'danger')
    FinCobrancaConta.registrar_teste(conta_id, resultado)
    registrar('escrita.testou_conta_cobranca', 'financeiro', tabela='fin_cobranca_contas', registro_id=conta_id,
              depois={'resultado': resultado[:200]})
    return redirect(_volta_contas(c['empresa_id']))


@financeiro.route('/financeiro/cobranca/<int:conta_id>/testar-email', methods=['POST'])
@permission_required(PERM)
def cobranca_testar_email(conta_id):
    """Manda um e-mail de prova pela caixa de cobrança da conta para quem clicou."""
    from utils import email_envio
    c = FinCobrancaConta.get(conta_id)
    if not c or c['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    para = (getattr(current_user, 'email', '') or '').strip()
    if not para:
        flash('O seu usuário não tem e-mail cadastrado para receber a prova.', 'warning')
        return redirect(_volta_contas(c['empresa_id']))
    try:
        rem = FinCobrancaConta.remetente_email(conta_id)
        assunto = f'Teste do e-mail de cobrança — {c["apelido"]}'
        texto = (f'Este é um teste da caixa {rem["user"]} feito pelo Qualicontax em '
                 f'{date.today().strftime("%d/%m/%Y")}.\n'
                 'Se chegou, os boletos e as notas desta conta sairão por ela.')
        html = ('<p>Este é um <b>teste da caixa de e-mail de cobrança</b> da conta '
                f'<b>{c["apelido"]}</b> ({rem["user"]}), feito pelo Qualicontax.</p>'
                '<p>Se chegou, os boletos e as notas fiscais desta conta sairão por ela, '
                'e as respostas dos clientes caem nessa caixa.</p>')
        ok, erro = email_envio.enviar([para], assunto, html, texto, remetente=rem)
        resultado = f'OK: enviado de {rem["user"]} para {para}' if ok else f'FALHOU: {erro}'
    except ValueError as exc:
        resultado = f'FALHOU: {exc}'
    except Exception as exc:                      # chave mestra
        logger.exception('[cobranca] teste de e-mail da conta %s', conta_id)
        resultado = f'FALHOU: {exc}'
    FinCobrancaConta.registrar_teste_email(conta_id, resultado)
    flash(f'{c["apelido"]}: {resultado}', 'success' if resultado.startswith('OK') else 'danger')
    registrar('escrita.testou_email_cobranca', 'financeiro', tabela='fin_cobranca_contas', registro_id=conta_id,
              depois={'resultado': resultado[:200]})
    return redirect(_volta_contas(c['empresa_id']))


@financeiro.route('/financeiro/cobranca/<int:conta_id>/alternar', methods=['POST'])
@permission_required(PERM)
def cobranca_alternar(conta_id):
    c = FinCobrancaConta.get(conta_id)
    if not c or c['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    FinCobrancaConta.alternar(conta_id, c['empresa_id'])
    registrar('escrita.alternou_conta_cobranca', 'financeiro', tabela='fin_cobranca_contas', registro_id=conta_id)
    flash('Conta de cobrança atualizada.', 'success')
    return redirect(_volta_contas(c['empresa_id']))


@financeiro.route('/financeiro/cobranca/<int:conta_id>/saldo')
@permission_required(PERM)
def cobranca_saldo(conta_id):
    """Saldo e Pix recebidos nos últimos 7 dias (API Pix; exige o certificado)."""
    c = FinCobrancaConta.get(conta_id)
    if not c or c['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    if c['provedor'] != 'efi':
        return jsonify({'ok': False, 'erro': 'Saldo pela tela só na Efí; o do Bradesco vem pelo extrato.'})
    try:
        efi, _ = FinCobrancaConta.cliente_efi(conta_id)
        s = efi.saldo()
        hoje = date.today()
        pix = efi.pix_recebidos(hoje - timedelta(days=7), hoje)
        recebidos = [{'horario': p.get('horario'), 'valor': p.get('valor'), 'pagador': (p.get('pagador') or {}).get('nome')
                      or p.get('infoPagador') or '', 'txid': p.get('txid'), 'e2e': p.get('endToEndId')} for p in pix]
        return jsonify({'ok': True, 'saldo': s['saldo'], 'bloqueado': s['bloqueado'], 'pix': recebidos})
    except (EfiErro, ValueError) as exc:
        return jsonify({'ok': False, 'erro': str(exc)})
    except Exception as exc:
        logger.exception('[cobranca] saldo da conta %s', conta_id)
        return jsonify({'ok': False, 'erro': str(exc)})


# ---------------------------------------------------------------------------
# Boleto a partir de uma nota
# ---------------------------------------------------------------------------
SLOGAN = 'a EMPRESA é sua, a PARCERIA é NOSSA'


def _itens_para_efi(itens, maximo=5):
    """A Efí imprime só 5 itens no PDF. Até 5: como estão. Acima: os 4 primeiros e a 5ª linha
    'Demais serviços (N): nome, nome' com a soma — o cliente lê tudo no papel."""
    if len(itens) <= maximo:
        return itens
    resto = itens[maximo - 1:]
    soma = sum((i['valor'] for i in resto), Decimal('0'))
    nome = f"Demais serviços ({len(resto)}): " + ', '.join(i['nome'] for i in resto)
    return list(itens[:maximo - 1]) + [{'nome': nome[:255], 'valor': soma, 'emite_nota': True}]


def _competencia(n):
    c = n.get('competencia')
    if isinstance(c, str):
        try:
            c = date.fromisoformat(c[:10])
        except ValueError:
            c = None
    return c or date.today()


def periodo_da_nota(n):
    """'Novembro/2026' — a competência da nota, como o boleto do Conexa mostra."""
    c = _competencia(n)
    return f'{MESES[c.month - 1].capitalize()}/{c.year}'


def _servicos_periodicos(empresa_id, ids):
    """Serviços do catálogo cuja descrição muda com a data: {id: 'mes' | 'ano'}. Com {mes} ou
    {competencia} o item do boleto leva 'Novembro/2026'; só com {ano} (13º) leva '2026'; avulso, nada."""
    ids = [int(i) for i in ids if str(i or '').isdigit()]
    if not ids:
        return {}
    rows = execute_query('SELECT id, descricao FROM nfse_servicos WHERE empresa_id = %s AND id IN ('
                         + ','.join(['%s'] * len(ids)) + ')', tuple([empresa_id] + ids), fetch=True) or []
    out = {}
    for r in rows:
        d = r['descricao'] or ''
        if '{mes}' in d or '{competencia}' in d:
            out[r['id']] = 'mes'
        elif '{ano}' in d:
            out[r['id']] = 'ano'
    return out


def _itens_da_nota(n):
    """Itens cobrados: os da nota (nota + só boleto) ou, sem itens, a própria nota.
    Item periódico (honorários) sai como 'Honorários contábeis - Novembro/2026'."""
    dj = n.get('dps_json')
    if isinstance(dj, str):
        try:
            dj = json.loads(dj)
        except ValueError:
            dj = {}
    dj = dj or {}
    brutos = dj.get('itens') or []
    periodicos = _servicos_periodicos(n['empresa_id'], [i.get('servico_id') for i in brutos])
    periodo = periodo_da_nota(n)
    ano = str(_competencia(n).year)
    itens = []
    for i in brutos:
        nome = (i.get('nome') or 'Serviço').replace(' — ', ' - ')
        sid = i.get('servico_id')
        tipo = periodicos.get(int(sid)) if str(sid or '').isdigit() else None
        if tipo == 'mes' and periodo not in nome:
            nome = f'{nome} - {periodo}'
        elif tipo == 'ano' and ano not in nome:
            nome = f'{nome} - {ano}'
        itens.append({'nome': nome, 'valor': Decimal(str(i.get('valor') or 0)),
                      'emite_nota': bool(i.get('emite_nota', True))})
    if not itens:
        itens = [{'nome': f"Honorários contábeis - {periodo}",
                  'valor': Decimal(str(n.get('valor_servico') or 0)), 'emite_nota': True}]
    total = sum((i['valor'] for i in itens), Decimal('0'))
    return itens, total, dj


def _pagador_da_nota(n, dj):
    cid = dj.get('tomador_cliente_id')
    t = tomador_do_cliente(int(cid), consultar_cep=False) if str(cid or '').isdigit() else None
    if t:
        return {'documento': t['documento'], 'nome': t['nome'], 'email': t.get('email'), 'telefone': t.get('fone'),
                'endereco': {'cep': t.get('cep'), 'logradouro': t.get('logradouro'), 'numero': t.get('numero'),
                             'complemento': t.get('complemento'), 'bairro': t.get('bairro'),
                             'cidade': t.get('cidade'), 'uf': t.get('uf')}, 'cliente_id': t['cliente_id']}
    return {'documento': n.get('tomador_doc'), 'nome': n.get('tomador_nome'), 'email': n.get('tomador_email'),
            'telefone': '', 'endereco': {}, 'cliente_id': None}


def _titulo_para(n, pagador, total, vencimento, descricao):
    """O título é o centro (Documento E): reusa o da nota ou cria um a receber."""
    if n.get('fin_titulo_id'):
        return n['fin_titulo_id']
    from models.fin_titulo import FinTitulo
    comp = n.get('competencia') or date.today()
    tid = FinTitulo.criar('R', pagador['nome'] or 'Tomador', CATEGORIA_HONORARIOS, descricao[:255], comp,
                          date.today(), vencimento, total, n['empresa_id'], contraparte_doc=pagador.get('documento'),
                          cliente_id=pagador.get('cliente_id'), origem='nfse',
                          chave_idem=f"nfse:{n['id']}:boleto")
    if tid:
        execute_query('UPDATE nfse_emitidas SET fin_titulo_id = %s WHERE id = %s AND fin_titulo_id IS NULL', (tid, n['id']))
        execute_query('UPDATE fin_titulos SET nfse_id = %s WHERE id = %s', (n['id'], tid))
    return tid


def _pagador_do_tomador(t):
    """Pagador do boleto a partir do cadastro (tomador_do_cliente)."""
    return {'documento': t['documento'], 'nome': t['nome'], 'email': t.get('email'), 'telefone': t.get('fone'),
            'endereco': {'cep': t.get('cep'), 'logradouro': t.get('logradouro'), 'numero': t.get('numero'),
                         'complemento': t.get('complemento'), 'bairro': t.get('bairro'),
                         'cidade': t.get('cidade'), 'uf': t.get('uf')}, 'cliente_id': t['cliente_id']}


def sugestao_boleto(conta_padrao, competencia):
    """(vencimento sugerido, observação sugerida) para as telas que geram boleto: o dia de vencimento
    da conta (ou +7 dias) e a mensagem padrão com {mes}/{ano}/{competencia} preenchidos (ou o slogan)."""
    hoje = date.today()
    msg = preencher_modelo((conta_padrao or {}).get('mensagem_padrao') or '', competencia or hoje) or SLOGAN
    if conta_padrao and conta_padrao.get('dia_vencimento'):
        d = int(conta_padrao['dia_vencimento'])
        venc = date(hoje.year + (hoje.month == 12), hoje.month % 12 + 1, min(d, 28)) if hoje.day >= d else date(hoje.year, hoje.month, min(d, 28))
    else:
        venc = hoje + timedelta(days=7)
    return venc, msg


def _mensagem_do_boleto(mensagem, competencia, conta, n=None):
    """Observação da Efí (até 4 linhas de 100): a mensagem pedida (ou a padrão da conta, ou o slogan),
    '|' quebra a linha; com nota autorizada a última linha é sempre a nota."""
    mensagem = preencher_modelo((mensagem or conta.get('mensagem_padrao') or '').strip(), competencia) or SLOGAN
    linhas = [l.strip()[:100] for l in mensagem.replace('|', '\n').split('\n') if l.strip()]
    linha_nota = []
    if n and n.get('numero_nfse'):
        linha_nota = [f"NFS-e nº {n['numero_nfse']} - chave {n.get('chave_acesso') or ''}".strip(' -')]
    return '\n'.join(linhas[:4 - len(linha_nota)] + linha_nota)


def _emitir_na_efi(conta, boleto_id, itens, pagador, vencimento, mensagem, titulo_id, nota_id, total, usuario_id):
    """O passo comum de todo boleto (da nota ou avulso): chama a Efí, grava a resposta, liga o título,
    manda o e-mail (quando a conta avisa pelo app) e registra. Devolve (ok, msg, aviso_email)."""
    custom_id = f'QC-{boleto_id}'
    if conta['provedor'] == 'bradesco':
        return _emitir_no_bradesco(conta, boleto_id, pagador, vencimento, titulo_id, nota_id, total, usuario_id)
    try:
        efi, _c = FinCobrancaConta.cliente_efi(conta['id'])
        data = efi.criar_boleto(
            _itens_para_efi(itens), pagador, vencimento, custom_id=custom_id,
            notification_url=url_for('cobranca_webhook.efi', segredo=conta['webhook_segredo'], _external=True, _scheme='https'),
            juros_mes=conta.get('juros_mes'), multa_pct=conta.get('multa_pct'), dias_baixa=conta.get('dias_baixa'),
            mensagem=mensagem or None, efi_avisa_por_email=(conta.get('avisa_por') == 'efi'))
    except (EfiErro, ValueError) as exc:
        FinBoleto.gravar_erro(boleto_id, str(exc))
        return False, f'A Efí não emitiu o boleto: {exc}', None
    except Exception as exc:
        logger.exception('[cobranca] boleto %s', boleto_id)
        FinBoleto.gravar_erro(boleto_id, str(exc))
        return False, f'Falha ao emitir o boleto: {exc}', None
    FinBoleto.gravar_resposta(boleto_id, data, custom_id)
    if titulo_id:
        execute_query('UPDATE fin_titulos SET boleto_id = %s WHERE id = %s', (boleto_id, titulo_id))
    # Quem avisa o cliente é o nosso app (avisa_por = 'app'): o e-mail sai na hora, pela caixa da conta.
    aviso_email = None
    if (conta.get('avisa_por') or 'app') == 'app' and conta.get('email_remetente'):
        from utils import cobranca_email
        ok_mail, aviso_email = cobranca_email.enviar_boleto(boleto_id, usuario_id=usuario_id)
        if not ok_mail:
            aviso_email = 'O boleto saiu, mas o e-mail não: ' + aviso_email
    registrar('escrita.emitiu_boleto', 'financeiro', tabela='fin_boletos', registro_id=boleto_id,
              depois={'nfse_id': nota_id, 'charge_id': data.get('charge_id'), 'valor': str(total),
                      'vencimento': vencimento.isoformat(), 'ambiente': conta['ambiente'], 'titulo_id': titulo_id})
    msg = (f"Boleto emitido na Efí (nº {data.get('charge_id')}) — R$ {total:.2f}, vence {vencimento.strftime('%d/%m/%Y')}"
           .replace('.', ',', 1) + (' (ambiente de teste)' if conta['ambiente'] != 'producao' else '') + '.')
    return True, msg, aviso_email


def _emitir_no_bradesco(conta, boleto_id, pagador, vencimento, titulo_id, nota_id, total, usuario_id):
    """Mesmo passo comum, no Bradesco (14/11/2026): registra, grava, liga o título, manda o e-mail.
    O PDF é o nosso (utils/boleto_pdf.py), com a lista dos serviços e o QR do Pix."""
    try:
        nn, _j = bradesco_boletos.emitir(conta, boleto_id, pagador, total, vencimento)
    except (BradescoErro, ValueError) as exc:
        FinBoleto.gravar_erro(boleto_id, str(exc))
        return False, f'O Bradesco não emitiu o boleto: {exc}', None
    except Exception as exc:
        logger.exception('[cobranca] boleto Bradesco %s', boleto_id)
        FinBoleto.gravar_erro(boleto_id, str(exc))
        return False, f'Falha ao emitir o boleto: {exc}', None
    if titulo_id:
        execute_query('UPDATE fin_titulos SET boleto_id = %s WHERE id = %s', (boleto_id, titulo_id))
    aviso_email = None
    if (conta.get('avisa_por') or 'app') == 'app' and conta.get('email_remetente'):
        from utils import cobranca_email
        ok_mail, aviso_email = cobranca_email.enviar_boleto(boleto_id, usuario_id=usuario_id)
        if not ok_mail:
            aviso_email = 'O boleto saiu, mas o e-mail não: ' + aviso_email
    registrar('escrita.emitiu_boleto', 'financeiro', tabela='fin_boletos', registro_id=boleto_id,
              depois={'nfse_id': nota_id, 'charge_id': nn, 'valor': str(total), 'provedor': 'bradesco',
                      'vencimento': vencimento.isoformat(), 'ambiente': conta['ambiente'], 'titulo_id': titulo_id})
    msg = (f"Boleto emitido no Bradesco (nosso número {nn}) — R$ {total:.2f}, vence {vencimento.strftime('%d/%m/%Y')}"
           .replace('.', ',', 1) + (' (ambiente de teste)' if conta['ambiente'] != 'producao' else '') + '.')
    return True, msg, aviso_email


def gerar_boleto_da_nota(n, conta, vencimento, mensagem, usuario_id):
    """Boleto de uma nota autorizada. Devolve (ok, msg, aviso_email)."""
    itens, total, dj = _itens_da_nota(n)
    if total <= 0:
        return False, 'A nota não tem valor para cobrar.', None
    pagador = _pagador_da_nota(n, dj)
    mensagem = _mensagem_do_boleto(mensagem, _competencia(n), conta, n)
    descricao = 'Honorários e serviços — ' + ', '.join(i['nome'] for i in itens)
    titulo_id = _titulo_para(n, pagador, total, vencimento, descricao)
    itens_json = [{'nome': i['nome'], 'valor': str(i['valor']), 'emite_nota': i['emite_nota']} for i in itens]
    boleto_id = FinBoleto.rascunho(n['empresa_id'], conta, titulo_id, n['id'], total, vencimento, pagador,
                                   itens_json, mensagem or None, usuario_id)
    if not boleto_id:
        return False, 'Não foi possível registrar o boleto.', None
    return _emitir_na_efi(conta, boleto_id, itens, pagador, vencimento, mensagem, titulo_id, n['id'], total, usuario_id)


def itens_avulsos_do_form(f, empresa_id, competencia):
    """Itens de um boleto SEM nota: serviço do catálogo (nome + período, como na nota) ou descrição livre.
    Devolve (itens, total); levanta ValueError com a mensagem para a tela."""
    ids, vals, nomes = f.getlist('item_servico_id'), f.getlist('item_valor'), f.getlist('item_nome')
    from models.nfse_emissao import NfseServico
    catalogo = {s['id']: s for s in NfseServico.listar(empresa_id)}
    periodicos = _servicos_periodicos(empresa_id, ids)
    periodo = f'{MESES[competencia.month - 1].capitalize()}/{competencia.year}'
    itens = []
    for i, sid in enumerate(ids):
        v = (vals[i] if i < len(vals) else '').strip()
        nome_livre = (nomes[i] if i < len(nomes) else '').strip()
        if not v and not str(sid).strip() and not nome_livre:
            continue
        s = catalogo.get(int(sid)) if str(sid).isdigit() else None
        try:
            valor = Decimal(v.replace('.', '').replace(',', '.')) if ',' in v else Decimal(v or '0')
        except Exception:
            raise ValueError(f'Valor do item {i + 1} inválido.')
        if valor <= 0:
            raise ValueError(f'Informe o valor do item {i + 1}.')
        nome = (s['nome'] if s else nome_livre).replace(' — ', ' - ')
        if not nome:
            raise ValueError(f'Descreva o item {i + 1} ou escolha um serviço da lista.')
        tipo = periodicos.get(s['id']) if s else None
        if tipo == 'mes' and periodo not in nome:
            nome = f'{nome} - {periodo}'
        elif tipo == 'ano' and str(competencia.year) not in nome:
            nome = f'{nome} - {competencia.year}'
        itens.append({'nome': nome[:255], 'valor': valor, 'emite_nota': False, 'servico_id': s['id'] if s else None})
    if not itens:
        raise ValueError('Informe ao menos um item com valor.')
    return itens, sum((i['valor'] for i in itens), Decimal('0'))


def emitir_boleto_avulso(empresa_id, conta, pagador, itens, total, competencia, vencimento, mensagem, titulo_id, usuario_id):
    """Boleto SEM nota fiscal (Anderson, 04/11/2026: "só o boleto para a Brilho"). O título é o centro:
    reusa o título escolhido ou cria um a receber. Devolve (boleto_id, ok, msg, aviso_email)."""
    mensagem = _mensagem_do_boleto(mensagem, competencia, conta)
    itens_json = [{'nome': i['nome'], 'valor': str(i['valor']), 'emite_nota': False} for i in itens]
    boleto_id = FinBoleto.rascunho(empresa_id, conta, titulo_id, None, total, vencimento, pagador,
                                   itens_json, mensagem or None, usuario_id)
    if not boleto_id:
        return None, False, 'Não foi possível registrar o boleto.', None
    titulo_novo = False
    if not titulo_id:
        from models.fin_titulo import FinTitulo
        descricao = 'Honorários e serviços — ' + ', '.join(i['nome'] for i in itens)
        titulo_id = FinTitulo.criar('R', pagador['nome'] or 'Cliente', CATEGORIA_HONORARIOS, descricao[:255], competencia,
                                    date.today(), vencimento, total, empresa_id, contraparte_doc=pagador.get('documento'),
                                    cliente_id=pagador.get('cliente_id'), origem='boleto', chave_idem=f'boleto:{boleto_id}')
        titulo_novo = bool(titulo_id)
        if titulo_id:
            execute_query('UPDATE fin_boletos SET titulo_id = %s WHERE id = %s', (titulo_id, boleto_id))
    ok, msg, aviso = _emitir_na_efi(conta, boleto_id, itens, pagador, vencimento, mensagem, titulo_id, None, total, usuario_id)
    if not ok and titulo_novo:
        from models.fin_titulo import FinTitulo
        FinTitulo.cancelar(titulo_id)            # a Efí recusou: o título que nasceu para este boleto não fica aberto
    return boleto_id, ok, msg, aviso


@financeiro.route('/financeiro/boletos/<int:boleto_id>/pdf')
@permission_required(PERM)
def boleto_pdf(boleto_id):
    """PDF do boleto para o ESCRITÓRIO (Anderson, 04/11/2026: 'salvar com o nome certinho'): ?baixar=1
    entrega como download, senão abre na tela. Nome: '{Cliente} - Boleto venc dd-mm-aaaa.pdf'. Não carimba
    'o cliente abriu' — quem está abrindo é o escritório."""
    from flask import Response
    from utils import cobranca_email
    from utils.texto import nome_proprio
    b = FinBoleto.get(boleto_id)
    if not b or b['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    if not (b.get('pdf_url') or b.get('link')):
        abort(404)
    pdf = cobranca_email.pdf_do_boleto(b) if b.get('pdf_url') else None
    if not pdf:
        return redirect(b.get('pdf_url') or b['link'])
    nome, _ = cobranca_email.nomes_dos_pdfs(b, None, nome_proprio(b.get('pagador_nome') or '') or 'Cliente')
    disp = 'attachment' if request.args.get('baixar') else 'inline'
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'{disp}; filename="{nome}"', 'Cache-Control': 'private, no-store'})


@financeiro.route('/financeiro/boletos/<int:boleto_id>')
@permission_required(PERM)
def boleto_detalhe(boleto_id):
    """Página do boleto (o boleto da nota mora na tela da nota; esta serve ao boleto SEM nota)."""
    b = FinBoleto.get(boleto_id)
    if not b or b['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    if b.get('nfse_id'):
        return redirect(url_for('financeiro.nota_detalhe', nota_id=b['nfse_id']) + '#boleto')
    if isinstance(b.get('itens_json'), str):
        try:
            b['itens_json'] = json.loads(b['itens_json'])
        except ValueError:
            b['itens_json'] = []
    emps, sel, mapa = _empresas_ctx()
    titulo = None
    if b.get('titulo_id'):
        titulo = execute_query('SELECT id, descricao, valor, valor_baixado, vencimento, competencia, status, cliente_id '
                               'FROM fin_titulos WHERE id = %s', (b['titulo_id'],), fetch=True, fetch_one=True)
    cliente = None
    cid = (titulo or {}).get('cliente_id')
    if cid:
        cliente = execute_query('SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, email, celular, telefone '
                                'FROM clientes WHERE id = %s', (cid,), fetch=True, fetch_one=True)
    return render_template('financeiro/boleto_detalhe.html', boleto=b, titulo=titulo, cliente=cliente,
                           SIT_BOLETO=SITUACOES, empresa_nome=mapa.get(b['empresa_id'], ''),
                           fin_empresas=emps, sel_empresas=sel, emp_mapa=mapa)


@financeiro.route('/financeiro/notas/<int:nota_id>/boleto', methods=['POST'])
@permission_required(PERM)
def nota_boleto(nota_id):
    n = NfseEmitida.get(nota_id)
    if not n or n['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    if n['situacao'] != 'autorizada':
        flash('Só nota autorizada gera boleto.', 'warning')
        return redirect(url_for('financeiro.nota_detalhe', nota_id=nota_id))
    if FinBoleto.da_nota(nota_id):
        flash('Esta nota já tem boleto.', 'warning')
        return redirect(url_for('financeiro.nota_detalhe', nota_id=nota_id))
    cid = request.form.get('conta_id', '')
    conta = FinCobrancaConta.get(int(cid)) if cid.isdigit() else FinCobrancaConta.padrao_da_empresa(n['empresa_id'])
    if not conta or conta['empresa_id'] != n['empresa_id'] or not conta['ativo']:
        flash('Cadastre e teste uma conta de cobrança da empresa em Financeiro → Cobrança.', 'danger')
        return redirect(url_for('financeiro.nota_detalhe', nota_id=nota_id))
    try:
        vencimento = date.fromisoformat(request.form.get('vencimento') or '')
    except ValueError:
        flash('Informe o vencimento do boleto.', 'danger')
        return redirect(url_for('financeiro.nota_detalhe', nota_id=nota_id))
    if vencimento < date.today():
        flash('O vencimento precisa ser hoje ou depois.', 'danger')
        return redirect(url_for('financeiro.nota_detalhe', nota_id=nota_id))

    ok, msg, aviso_email = gerar_boleto_da_nota(n, conta, vencimento, request.form.get('mensagem'), current_user.id)
    flash(msg, 'success' if ok else 'danger')
    if aviso_email:
        flash(aviso_email, 'success' if aviso_email.startswith('E-mail enviado') else 'warning')
    return redirect(url_for('financeiro.nota_detalhe', nota_id=nota_id))


@financeiro.route('/financeiro/boletos/<int:boleto_id>/enviar-email', methods=['POST'])
@permission_required(PERM)
def boleto_enviar_email(boleto_id):
    """Manda (ou reenvia) o e-mail de cobrança do boleto: itens completos, PDFs da nota e do boleto."""
    from utils import cobranca_email
    b = FinBoleto.get(boleto_id)
    if not b or b['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    ok, msg = cobranca_email.enviar_boleto(boleto_id, para=request.form.get('para'), usuario_id=current_user.id)
    flash(msg, 'success' if ok else 'danger')
    registrar('escrita.enviou_email_boleto', 'financeiro', tabela='fin_boletos', registro_id=boleto_id,
              depois={'ok': ok, 'para': (request.form.get('para') or b.get('pagador_email') or '')[:200], 'msg': msg[:200]})
    volta = request.form.get('volta') or (url_for('financeiro.nota_detalhe', nota_id=b['nfse_id']) + '#boleto'
                                          if b.get('nfse_id') else url_for('financeiro.boleto_detalhe', boleto_id=boleto_id))
    return redirect(volta)


@financeiro.route('/financeiro/boletos/<int:boleto_id>/cancelar', methods=['POST'])
@permission_required(PERM)
def boleto_cancelar(boleto_id):
    b = FinBoleto.get(boleto_id)
    if not b or b['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    volta = request.form.get('volta') or url_for('financeiro.cobrancas', emp=b['empresa_id'], ver='boletos')
    if b['situacao'] not in ('new', 'waiting', 'unpaid'):
        flash('Só boleto em aberto pode ser cancelado.', 'warning')
        return redirect(volta)
    try:
        if b['provedor'] == 'bradesco':
            bradesco_boletos.cancelar(b)
        else:
            efi, _ = FinCobrancaConta.cliente_efi(b['conta_cobranca_id'])
            efi.cancelar(b['charge_id'])
    except (EfiErro, BradescoErro, ValueError) as exc:
        flash(f"{'O Bradesco' if b['provedor'] == 'bradesco' else 'A Efí'} não cancelou: {exc}", 'danger')
        return redirect(volta)
    FinBoleto.atualizar_situacao(boleto_id, 'canceled')
    titulo_cancelado = False
    if not b.get('nfse_id') and b.get('titulo_id'):      # boleto sem nota: o título nasceu com ele e vai junto
        from models.fin_titulo import FinTitulo
        titulo_cancelado = bool(FinTitulo.cancelar(b['titulo_id']))
    registrar('escrita.cancelou_boleto', 'financeiro', tabela='fin_boletos', registro_id=boleto_id,
              depois={'charge_id': b['charge_id'], 'titulo_cancelado': titulo_cancelado})
    flash(f"Boleto cancelado {'no Bradesco' if b['provedor'] == 'bradesco' else 'na Efí'}." + (' O título a receber foi cancelado junto.' if titulo_cancelado else ''), 'success')
    return redirect(volta)


@financeiro.route('/financeiro/boletos/<int:boleto_id>/sincronizar', methods=['POST'])
@permission_required(PERM)
def boleto_sincronizar(boleto_id):
    """Consulta a Efí e aplica o que mudou (para quando o aviso não chegou)."""
    b = FinBoleto.get(boleto_id)
    if not b or b['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    volta = request.form.get('volta') or url_for('financeiro.cobrancas', emp=b['empresa_id'], ver='boletos')
    if b['provedor'] == 'bradesco':
        try:
            sit, feito = bradesco_boletos.consultar_e_aplicar(b)
        except (BradescoErro, ValueError) as exc:
            flash(f'O Bradesco não respondeu: {exc}', 'danger')
            return redirect(volta)
        flash(f'Boleto {b["charge_id"]}: {SITUACOES.get(sit, sit)}.' + (' Título baixado.' if feito == 'baixa' else ''), 'success')
        return redirect(volta)
    try:
        efi, _ = FinCobrancaConta.cliente_efi(b['conta_cobranca_id'])
        d = efi.consultar(b['charge_id'])
    except (EfiErro, ValueError) as exc:
        flash(f'A Efí não respondeu: {exc}', 'danger')
        return redirect(volta)
    sit, valor_pago, pago_em, credito_em = efi_boletos.do_boleto_efi(d)
    sit = sit or b['situacao']
    h = d.get('history') or []
    quando = date.fromisoformat(h[-1]['created_at'][:10]) if h and h[-1].get('created_at') else date.today()
    feito = efi_boletos.aplicar(b, sit, valor_pago, pago_em, credito_em, quando)
    extra = {'baixa': ' Título baixado.', 'baixa-app': ' Título baixado (marcado como pago no app da Efí).',
             'perdido': ' Título foi para Perdidos (boleto cancelado no app da Efí).'}.get(feito, '')
    flash(f'Boleto {b["charge_id"]}: {SITUACOES.get(sit, sit)}.{extra}', 'success')
    return redirect(volta)


# ---------------------------------------------------------------------------
# Mudar vencimento / Marcar como pago na Efí / Reemitir (14/11/2026)
# Anderson: "se eu precisar baixar um boleto ou alterar eu ter a opção de fazer tudo aqui"; "Quando os
# boletos expirar no EFI não é para baixar é para deixar EXPIRADOS, e teremos a opção de reemiti-los".
# O vencimento do TÍTULO não muda em nenhum dos três: ele guarda desde quando o cliente deve (Atrasados /
# Inadimplentes contam dali); só o boleto ganha a data nova.
# ---------------------------------------------------------------------------
def _hoje():
    """Hoje em Brasília: o servidor roda em UTC e depois das 21h já seria amanhã."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()


@financeiro.app_template_global('hoje_iso')
def _hoje_iso():
    return _hoje().isoformat()


def _boleto_e_volta(boleto_id):
    b = FinBoleto.get(boleto_id)
    if not b or b['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    volta = request.form.get('volta') or url_for('financeiro.boleto_detalhe', boleto_id=boleto_id)
    return b, volta


def _data_do_form(campo, minimo=None):
    try:
        d = date.fromisoformat(request.form.get(campo) or '')
    except ValueError:
        return None
    return d if minimo is None or d >= minimo else None


@financeiro.route('/financeiro/boletos/<int:boleto_id>/vencimento', methods=['POST'])
@permission_required(PERM)
def boleto_vencimento(boleto_id):
    b, volta = _boleto_e_volta(boleto_id)
    if b['situacao'] not in ('waiting', 'unpaid'):
        flash('Só boleto aguardando pagamento ou vencido pode mudar de vencimento.', 'warning')
        return redirect(volta)
    if b['provedor'] == 'bradesco':
        flash('No Bradesco, por enquanto: cancele este boleto e emita outro com o vencimento novo.', 'warning')
        return redirect(volta)
    novo = _data_do_form('vencimento', _hoje())
    if not novo:
        flash('Informe o novo vencimento (hoje ou depois).', 'danger')
        return redirect(volta)
    try:
        efi, _ = FinCobrancaConta.cliente_efi(b['conta_cobranca_id'])
        efi.alterar_vencimento(b['charge_id'], novo)
    except (EfiErro, ValueError) as exc:
        flash(f'A Efí não mudou o vencimento: {exc}', 'danger')
        return redirect(volta)
    execute_query("UPDATE fin_boletos SET vencimento = %s, situacao = 'waiting', atualizado_em = NOW() WHERE id = %s",
                  (novo, boleto_id))
    registrar('escrita.mudou_vencimento_boleto', 'financeiro', tabela='fin_boletos', registro_id=boleto_id,
              antes={'vencimento': str(b['vencimento'])},
              depois={'vencimento': str(novo), 'charge_id': b['charge_id'], 'titulo_id': b.get('titulo_id')})
    flash(f"Vencimento do boleto mudado na Efí para {novo.strftime('%d/%m/%Y')}. O mesmo código de barras vale.", 'success')
    return redirect(volta)


@financeiro.route('/financeiro/boletos/<int:boleto_id>/marcar-pago', methods=['POST'])
@permission_required(PERM)
def boleto_marcar_pago(boleto_id):
    """Pago por fora (Pix, dinheiro): marca o boleto como pago na Efí (não pode mais ser pago) e, se o título
    ainda tem saldo, dá a baixa manual na data informada."""
    b, volta = _boleto_e_volta(boleto_id)
    if b['situacao'] not in ('waiting', 'unpaid'):
        flash('Só boleto aguardando pagamento ou vencido pode ser marcado como pago.', 'warning')
        return redirect(volta)
    quando = _data_do_form('data') or _hoje()
    if quando > _hoje():
        flash('A data do pagamento não pode ser depois de hoje.', 'danger')
        return redirect(volta)
    try:
        if b['provedor'] == 'bradesco':           # o Bradesco não tem "marcar como pago": a baixa no banco tira o boleto
            bradesco_boletos.cancelar(b)
        else:
            efi, _ = FinCobrancaConta.cliente_efi(b['conta_cobranca_id'])
            efi.marcar_pago(b['charge_id'])
    except (EfiErro, BradescoErro, ValueError) as exc:
        flash(f"{'O Bradesco' if b['provedor'] == 'bradesco' else 'A Efí'} não marcou como pago: {exc}", 'danger')
        return redirect(volta)
    # 'settled' ANTES do aviso da Efí chegar: o aviso vê que já estava assim e não baixa de novo
    FinBoleto.atualizar_situacao(boleto_id, 'settled')
    baixou = None
    if b.get('titulo_id'):
        from utils.financeiro_core import registrar_baixa, BaixaInvalida
        t = execute_query('SELECT status, valor, valor_baixado FROM fin_titulos WHERE id = %s', (b['titulo_id'],),
                          fetch=True, fetch_one=True) or {}
        saldo = Decimal(str(t.get('valor') or 0)) - Decimal(str(t.get('valor_baixado') or 0))
        if t.get('status') in ('aberto', 'parcial') and saldo > 0:
            try:
                baixou = registrar_baixa(b['titulo_id'], saldo, quando, 'manual',
                                         referencia=f"efi-settle:{b['charge_id']}", usuario_id=current_user.id)
            except BaixaInvalida as exc:
                flash(f'O boleto foi marcado como pago na Efí, mas a baixa do título não entrou: {exc}', 'warning')
    registrar('escrita.marcou_boleto_pago_efi', 'financeiro', tabela='fin_boletos', registro_id=boleto_id,
              depois={'charge_id': b['charge_id'], 'titulo_id': b.get('titulo_id'), 'data': str(quando),
                      'baixa_titulo': (baixou or {}).get('status')})
    flash('Boleto marcado como pago na Efí — não pode mais ser pago.'
          + (f" Título baixado em {quando.strftime('%d/%m/%Y')}." if baixou else ' O título já estava quitado.'), 'success')
    return redirect(volta)


@financeiro.route('/financeiro/boletos/<int:boleto_id>/reemitir', methods=['POST'])
@permission_required(PERM)
def boleto_reemitir(boleto_id):
    """Boleto EXPIRADO (não pode mais ser pago): emite um novo pela nossa conta, para o MESMO título, com o
    saldo dele. O expirado fica como está, guardado."""
    import re
    b, volta = _boleto_e_volta(boleto_id)
    if b['situacao'] != 'expired':
        flash('Só boleto expirado é reemitido. Vencido ainda pode ser pago: use Mudar vencimento.', 'warning')
        return redirect(volta)
    t = execute_query('SELECT id, empresa_id, cliente_id, descricao, competencia, status, valor, valor_baixado '
                      'FROM fin_titulos WHERE id = %s', (b.get('titulo_id') or 0,), fetch=True, fetch_one=True)
    if not t or t['status'] not in ('aberto', 'parcial'):
        flash('O título deste boleto não está em aberto — nada a cobrar.', 'warning')
        return redirect(volta)
    saldo = Decimal(str(t['valor'])) - Decimal(str(t['valor_baixado'] or 0))
    venc = _data_do_form('vencimento', _hoje())
    if not venc or saldo <= 0:
        flash('Informe o vencimento do boleto novo (hoje ou depois).', 'danger')
        return redirect(volta)
    conta = FinCobrancaConta.padrao_da_empresa(t['empresa_id'])
    if not conta:
        flash('Não há conta de cobrança para emitir o boleto novo.', 'danger')
        return redirect(volta)
    tom = tomador_do_cliente(t['cliente_id'], consultar_cep=False) if t.get('cliente_id') else None
    if not tom or not tom.get('documento'):
        flash('O cadastro do cliente não tem CPF/CNPJ para o boleto.', 'danger')
        return redirect(volta)
    nome = re.sub(r'\s*\(Conexa \d+\)\s*$', '', t['descricao'] or '').strip() or 'Honorários'
    itens = [{'nome': nome[:255], 'valor': saldo, 'emite_nota': False, 'servico_id': None}]
    competencia = t.get('competencia') or _hoje()
    _v, mensagem = sugestao_boleto(conta, competencia)
    novo_id, ok, msg, aviso = emitir_boleto_avulso(t['empresa_id'], conta, _pagador_do_tomador(tom), itens, saldo,
                                                   competencia, venc, mensagem, t['id'], current_user.id)
    registrar('escrita.reemitiu_boleto', 'financeiro', tabela='fin_boletos', registro_id=boleto_id,
              depois={'expirado': b['charge_id'], 'novo_boleto_id': novo_id, 'ok': ok, 'titulo_id': t['id'],
                      'valor': str(saldo), 'vencimento': str(venc)})
    flash(msg, 'success' if ok else 'danger')
    if aviso:
        flash(aviso, 'success' if aviso.startswith('E-mail enviado') else 'warning')
    return redirect(url_for('financeiro.boleto_detalhe', boleto_id=novo_id) if ok and novo_id else volta)


# ---------------------------------------------------------------------------
# Webhook da Efí (público; o segredo da URL identifica a conta)
# ---------------------------------------------------------------------------
@cobranca_publica.route('/c/<token>/boleto')
def abrir_boleto(token):
    """Cliente clicou em "Abrir o boleto": carimba e mostra o PDF do boleto no navegador (nosso endereco,
    nome certo). A pagina "Boleto Digital" da Efi so entra como socorro, quando ela nao entrega o PDF
    (Anderson, 04/11/2026: "abrir boleto esta abrindo assim e nao quero assim nao, quero o nosso boleto")."""
    return _pdf_do_boleto_do_token(token, 'inline')


def _pdf_do_boleto_do_token(token, disposicao):
    from flask import Response
    from utils import cobranca_email
    b = cobranca_email.registrar_abertura(token, 'boleto')
    if not b or not (b.get('pdf_url') or b.get('link')):
        abort(404)
    pdf = cobranca_email.pdf_do_boleto(b) if b.get('pdf_url') else None
    if not pdf:
        return redirect(b.get('pdf_url') or b['link'])   # a Efi nao respondeu: manda direto para o dela
    nome, _ = cobranca_email.nomes_dos_pdfs(b, None, cobranca_email._empresa(b['empresa_id']))
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'{disposicao}; filename="{nome}"', 'Cache-Control': 'private, no-store'})


def _disposicao_download():
    """'attachment' baixa no computador; no iPhone/iPad o Safari ignora e abre uma tela sem o nome do
    arquivo (Anderson, 04/11/2026: 'tela inutil'). La vai 'inline': abre o visualizador e o botao
    Compartilhar salva com o nome certo (Qualicontax - NFS-e N - Mes-Ano.pdf)."""
    ua = request.headers.get('User-Agent', '')
    return 'inline' if ('iPhone' in ua or 'iPad' in ua or 'iPod' in ua) else 'attachment'


@cobranca_publica.route('/c/<token>/boleto/pdf')
def baixar_boleto(token):
    """Cliente clicou em "Baixar o boleto": carimba e entrega o PDF como download (inline no iPhone)."""
    return _pdf_do_boleto_do_token(token, _disposicao_download())


def _pdf_da_nota_do_token(token, disposicao):
    from flask import Response
    from utils import cobranca_email
    b = cobranca_email.registrar_abertura(token, 'nota')
    if not b or not b.get('nfse_id'):
        abort(404)
    nota = NfseEmitida.get(b['nfse_id'])
    pdf = cobranca_email.pdf_da_nota(nota)
    if not pdf:
        abort(404)
    _, nome = cobranca_email.nomes_dos_pdfs(b, nota, cobranca_email._empresa(b['empresa_id']))
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'{disposicao}; filename="{nome}"', 'Cache-Control': 'private, no-store'})


@cobranca_publica.route('/c/<token>/nota')
def abrir_nota(token):
    """Cliente clicou em "Ver a nota fiscal": carimba e abre o PDF da nota DESTE boleto no navegador."""
    return _pdf_da_nota_do_token(token, 'inline')


@cobranca_publica.route('/c/<token>/nota/pdf')
def baixar_nota(token):
    """Cliente clicou em "Baixar a nota": carimba e entrega o PDF como download."""
    return _pdf_da_nota_do_token(token, _disposicao_download())


@cobranca_publica.route('/c/<token>/pixel.png')
def pixel(token):
    """Imagem de 1 px do e-mail: carimba "visto" (indicio) e devolve um GIF transparente."""
    from flask import Response
    from utils import cobranca_email
    cobranca_email.registrar_abertura(token, 'pixel')
    gif = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
    return Response(gif, mimetype='image/gif', headers={'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0'})


@cobranca_webhook.route('/webhooks/efi/<segredo>', methods=['POST', 'GET'])
def efi(segredo):
    if request.method == 'GET':                      # a Efí valida a URL com um GET ao cadastrar
        return jsonify({'ok': True}), 200
    conta = FinCobrancaConta.por_webhook(segredo)
    if not conta:
        return jsonify({'ok': False}), 404
    token = request.form.get('notification') or (request.get_json(silent=True) or {}).get('notification')
    if not token:
        return jsonify({'ok': False, 'erro': 'sem token'}), 400
    try:
        efi_cli, _ = FinCobrancaConta.cliente_efi(conta['id'])
        eventos = efi_cli.notificacao(token)
    except Exception as exc:
        logger.warning('[cobranca] webhook da conta %s: %s', conta['id'], exc)
        return jsonify({'ok': False}), 500          # a Efí tenta de novo
    tratados = 0
    for ev in eventos:
        if ev.get('type') not in ('charge', 'subscription_charge', 'carnet_charge'):
            continue
        charge_id = (ev.get('identifiers') or {}).get('charge_id')
        if not charge_id:
            continue
        b = FinBoleto.por_charge(conta['provedor'], conta['ambiente'], charge_id)
        if not b or (b.get('ultimo_evento_id') or 0) >= int(ev.get('id') or 0):
            continue
        atual = (ev.get('status') or {}).get('current') or b['situacao']
        pago_em, valor_pago = None, None
        if atual == 'paid':
            valor_pago = Decimal(str(ev.get('value') or 0)) / 100
            pago_em = date.fromisoformat((ev.get('received_by_bank_at') or ev.get('created_at') or '')[:10]
                                         or date.today().isoformat())
        # pago, baixado no app da Efí ou cancelado no app da Efí: utils/efi_boletos.aplicar (14/11/2026)
        quando = date.fromisoformat((ev.get('created_at') or date.today().isoformat())[:10])
        feito = efi_boletos.aplicar(b, atual, valor_pago, pago_em, None, quando,
                                    evento_id=int(ev.get('id') or 0), token=token)
        registrar('escrita.webhook_boleto', 'financeiro', tabela='fin_boletos', registro_id=b['id'],
                  depois={'charge_id': charge_id, 'de': (ev.get('status') or {}).get('previous'), 'para': atual,
                          'valor': str(valor_pago) if valor_pago is not None else None, 'feito': feito})
        tratados += 1
    return jsonify({'ok': True, 'eventos': tratados}), 200
