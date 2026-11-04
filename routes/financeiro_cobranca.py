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
@financeiro.route('/financeiro/cobranca')
@permission_required(PERM)
def cobranca():
    emps, sel, mapa = _empresas_ctx()
    situacao = request.args.get('situacao') or 'todas'
    busca = (request.args.get('busca') or '').strip()
    de, ate = request.args.get('de') or '', request.args.get('ate') or ''
    contas = FinCobrancaConta.listar(empresa_ids=sel)
    from models.extrato_lancamento import FinContaBancaria
    bancarias = FinContaBancaria.listar(empresa_ids=[e['cliente_id'] for e in emps], apenas_ativas=True)
    boletos = FinBoleto.listar(empresa_ids=sel, situacao=situacao, de=de or None, ate=ate or None, busca=busca or None)
    for b in boletos:
        b['situacao_txt'] = SITUACOES.get(b['situacao'], b['situacao'])
    return render_template(
        'financeiro/cobranca.html', contas=contas, boletos=boletos, resumo=FinBoleto.resumo(sel),
        bancarias=bancarias, PROVEDORES=PROVEDORES, AMBIENTES=AMBIENTES, AVISA_POR=AVISA_POR, SITUACOES=SITUACOES,
        fin_empresas=emps, sel_empresas=sel, emp_mapa=mapa, hoje=date.today(),
        filtros=dict(situacao=situacao, busca=busca, de=de, ate=ate, emp=','.join(map(str, sel))),
        webhook_base=url_for('cobranca_webhook.efi', segredo='X', _external=True, _scheme='https').rsplit('X', 1)[0])


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
        return redirect(url_for('financeiro.cobranca', emp=emp_id))
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
    return redirect(url_for('financeiro.cobranca', emp=emp_id))


@financeiro.route('/financeiro/cobranca/<int:conta_id>/testar', methods=['POST'])
@permission_required(PERM)
def cobranca_testar(conta_id):
    c = FinCobrancaConta.get(conta_id)
    if not c or c['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    try:
        efi, _ = FinCobrancaConta.cliente_efi(conta_id)
        resultado = 'OK: ' + efi.testar()
        flash(f'{c["apelido"]}: {resultado}', 'success')
    except (EfiErro, ValueError) as exc:
        resultado = f'FALHOU: {exc}'
        flash(f'{c["apelido"]}: {resultado}', 'danger')
    except Exception as exc:                      # chave mestra, rede
        logger.exception('[cobranca] teste da conta %s', conta_id)
        resultado = f'FALHOU: {exc}'
        flash(f'{c["apelido"]}: {resultado}', 'danger')
    FinCobrancaConta.registrar_teste(conta_id, resultado)
    registrar('escrita.testou_conta_cobranca', 'financeiro', tabela='fin_cobranca_contas', registro_id=conta_id,
              depois={'resultado': resultado[:200]})
    return redirect(url_for('financeiro.cobranca', emp=c['empresa_id']))


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
        return redirect(url_for('financeiro.cobranca', emp=c['empresa_id']))
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
    return redirect(url_for('financeiro.cobranca', emp=c['empresa_id']))


@financeiro.route('/financeiro/cobranca/<int:conta_id>/alternar', methods=['POST'])
@permission_required(PERM)
def cobranca_alternar(conta_id):
    c = FinCobrancaConta.get(conta_id)
    if not c or c['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    FinCobrancaConta.alternar(conta_id, c['empresa_id'])
    registrar('escrita.alternou_conta_cobranca', 'financeiro', tabela='fin_cobranca_contas', registro_id=conta_id)
    flash('Conta de cobrança atualizada.', 'success')
    return redirect(url_for('financeiro.cobranca', emp=c['empresa_id']))


@financeiro.route('/financeiro/cobranca/<int:conta_id>/saldo')
@permission_required(PERM)
def cobranca_saldo(conta_id):
    """Saldo e Pix recebidos nos últimos 7 dias (API Pix; exige o certificado)."""
    c = FinCobrancaConta.get(conta_id)
    if not c or c['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
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

    itens, total, dj = _itens_da_nota(n)
    if total <= 0:
        flash('A nota não tem valor para cobrar.', 'danger')
        return redirect(url_for('financeiro.nota_detalhe', nota_id=nota_id))
    pagador = _pagador_da_nota(n, dj)
    # Observação do boleto (Efí: até 4 linhas de 100): o slogan (ou a mensagem da conta / da tela, '|'
    # quebra a linha) e, com nota fiscal autorizada, a última linha é sempre a nota vinculada.
    mensagem = preencher_modelo((request.form.get('mensagem') or conta.get('mensagem_padrao') or '').strip(),
                                _competencia(n)) or SLOGAN
    linhas = [l.strip()[:100] for l in mensagem.replace('|', '\n').split('\n') if l.strip()]
    linha_nota = [f"NFS-e nº {n['numero_nfse']} - chave {n.get('chave_acesso') or ''}".strip(' -')] if n.get('numero_nfse') else []
    mensagem = '\n'.join(linhas[:4 - len(linha_nota)] + linha_nota)
    itens_efi = _itens_para_efi(itens)
    descricao = 'Honorários e serviços — ' + ', '.join(i['nome'] for i in itens)
    titulo_id = _titulo_para(n, pagador, total, vencimento, descricao)
    itens_json = [{'nome': i['nome'], 'valor': str(i['valor']), 'emite_nota': i['emite_nota']} for i in itens]
    boleto_id = FinBoleto.rascunho(n['empresa_id'], conta, titulo_id, nota_id, total, vencimento, pagador,
                                   itens_json, mensagem or None, current_user.id)
    if not boleto_id:
        flash('Não foi possível registrar o boleto.', 'danger')
        return redirect(url_for('financeiro.nota_detalhe', nota_id=nota_id))
    custom_id = f'QC-{boleto_id}'
    try:
        efi, _c = FinCobrancaConta.cliente_efi(conta['id'])
        data = efi.criar_boleto(
            itens_efi, pagador, vencimento, custom_id=custom_id,
            notification_url=url_for('cobranca_webhook.efi', segredo=conta['webhook_segredo'], _external=True, _scheme='https'),
            juros_mes=conta.get('juros_mes'), multa_pct=conta.get('multa_pct'), dias_baixa=conta.get('dias_baixa'),
            mensagem=mensagem or None, efi_avisa_por_email=(conta.get('avisa_por') == 'efi'))
    except (EfiErro, ValueError) as exc:
        FinBoleto.gravar_erro(boleto_id, str(exc))
        flash(f'A Efí não emitiu o boleto: {exc}', 'danger')
        return redirect(url_for('financeiro.nota_detalhe', nota_id=nota_id))
    except Exception as exc:
        logger.exception('[cobranca] boleto da nota %s', nota_id)
        FinBoleto.gravar_erro(boleto_id, str(exc))
        flash(f'Falha ao emitir o boleto: {exc}', 'danger')
        return redirect(url_for('financeiro.nota_detalhe', nota_id=nota_id))
    FinBoleto.gravar_resposta(boleto_id, data, custom_id)
    if titulo_id:
        execute_query('UPDATE fin_titulos SET boleto_id = %s WHERE id = %s', (boleto_id, titulo_id))
    # Quem avisa o cliente é o nosso app (avisa_por = 'app'): o e-mail sai na hora, pela caixa da conta.
    aviso_email = None
    if (conta.get('avisa_por') or 'app') == 'app' and conta.get('email_remetente'):
        from utils import cobranca_email
        ok_mail, aviso_email = cobranca_email.enviar_boleto(boleto_id, usuario_id=current_user.id)
        if not ok_mail:
            aviso_email = 'O boleto saiu, mas o e-mail não: ' + aviso_email
    registrar('escrita.emitiu_boleto', 'financeiro', tabela='fin_boletos', registro_id=boleto_id,
              depois={'nfse_id': nota_id, 'charge_id': data.get('charge_id'), 'valor': str(total),
                      'vencimento': vencimento.isoformat(), 'ambiente': conta['ambiente'], 'titulo_id': titulo_id})
    flash(f"Boleto emitido na Efí (nº {data.get('charge_id')}) — R$ {total:.2f}, vence {vencimento.strftime('%d/%m/%Y')}"
          .replace('.', ',', 1) + (' (ambiente de teste)' if conta['ambiente'] != 'producao' else '') + '.', 'success')
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
                                          if b.get('nfse_id') else url_for('financeiro.cobranca', emp=b['empresa_id']))
    return redirect(volta)


@financeiro.route('/financeiro/boletos/<int:boleto_id>/cancelar', methods=['POST'])
@permission_required(PERM)
def boleto_cancelar(boleto_id):
    b = FinBoleto.get(boleto_id)
    if not b or b['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    volta = request.form.get('volta') or url_for('financeiro.cobranca', emp=b['empresa_id'])
    if b['situacao'] not in ('new', 'waiting', 'unpaid'):
        flash('Só boleto em aberto pode ser cancelado.', 'warning')
        return redirect(volta)
    try:
        efi, _ = FinCobrancaConta.cliente_efi(b['conta_cobranca_id'])
        efi.cancelar(b['charge_id'])
    except (EfiErro, ValueError) as exc:
        flash(f'A Efí não cancelou: {exc}', 'danger')
        return redirect(volta)
    FinBoleto.atualizar_situacao(boleto_id, 'canceled')
    registrar('escrita.cancelou_boleto', 'financeiro', tabela='fin_boletos', registro_id=boleto_id,
              depois={'charge_id': b['charge_id']})
    flash('Boleto cancelado na Efí.', 'success')
    return redirect(volta)


@financeiro.route('/financeiro/boletos/<int:boleto_id>/sincronizar', methods=['POST'])
@permission_required(PERM)
def boleto_sincronizar(boleto_id):
    """Consulta a Efí e aplica o que mudou (para quando o aviso não chegou)."""
    b = FinBoleto.get(boleto_id)
    if not b or b['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    volta = request.form.get('volta') or url_for('financeiro.cobranca', emp=b['empresa_id'])
    try:
        efi, _ = FinCobrancaConta.cliente_efi(b['conta_cobranca_id'])
        d = efi.consultar(b['charge_id'])
    except (EfiErro, ValueError) as exc:
        flash(f'A Efí não respondeu: {exc}', 'danger')
        return redirect(volta)
    sit = d.get('status') or b['situacao']
    pago_em, valor_pago = None, None
    if sit == 'paid':
        pag = d.get('payment') or {}
        valor_pago = Decimal(str(d.get('total') or 0)) / 100
        pago_em = (pag.get('paid_at') or pag.get('created_at') or '')[:10] or date.today().isoformat()
        _baixar(b, valor_pago, pago_em)
    FinBoleto.atualizar_situacao(boleto_id, sit, pago_em=pago_em, valor_pago=valor_pago)
    flash(f'Boleto {b["charge_id"]}: {SITUACOES.get(sit, sit)}.', 'success')
    return redirect(volta)


def _baixar(b, valor_pago, pago_em):
    """A baixa do título pelo pagamento do boleto — pelo ÚNICO escritor (financeiro_core)."""
    if not b.get('titulo_id'):
        return None
    from utils.financeiro_core import registrar_baixa, BaixaInvalida
    try:
        valor_titulo = Decimal(str(b['valor']))
        pago = Decimal(str(valor_pago))
        abate = min(pago, valor_titulo)
        juros = pago - abate if pago > abate else Decimal('0')
        return registrar_baixa(b['titulo_id'], abate, pago_em, 'webhook', referencia=f"efi:{b['charge_id']}",
                               juros=juros)
    except BaixaInvalida as exc:
        logger.warning('[cobranca] baixa do boleto %s recusada: %s', b['id'], exc)
        return None


# ---------------------------------------------------------------------------
# Webhook da Efí (público; o segredo da URL identifica a conta)
# ---------------------------------------------------------------------------
@cobranca_publica.route('/c/<token>/boleto')
def abrir_boleto(token):
    """Cliente clicou em "Abrir o boleto": carimba e manda para a pagina da Efi (ou o PDF)."""
    from utils import cobranca_email
    b = cobranca_email.registrar_abertura(token, 'boleto')
    if not b or not (b.get('link') or b.get('pdf_url')):
        abort(404)
    return redirect(b.get('link') or b['pdf_url'])


@cobranca_publica.route('/c/<token>/boleto/pdf')
def baixar_boleto(token):
    """Cliente clicou em "Baixar o boleto": carimba e entrega o PDF da Efi como download."""
    from flask import Response
    from utils import cobranca_email
    b = cobranca_email.registrar_abertura(token, 'boleto')
    if not b or not b.get('pdf_url'):
        abort(404)
    pdf = cobranca_email.pdf_do_boleto(b)
    if not pdf:
        return redirect(b['pdf_url'])          # a Efi nao respondeu: manda direto para o PDF dela
    nome, _ = cobranca_email.nomes_dos_pdfs(b, None, cobranca_email._empresa(b['empresa_id']))
    return Response(pdf, mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename="{nome}"', 'Cache-Control': 'private, no-store'})


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
    return _pdf_da_nota_do_token(token, 'attachment')


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
            pago_em = (ev.get('received_by_bank_at') or ev.get('created_at') or '')[:10] or date.today().isoformat()
            _baixar(b, valor_pago, pago_em)
        FinBoleto.atualizar_situacao(b['id'], atual, pago_em=pago_em, valor_pago=valor_pago,
                                     evento_id=int(ev.get('id') or 0), token=token)
        registrar('escrita.webhook_boleto', 'financeiro', tabela='fin_boletos', registro_id=b['id'],
                  depois={'charge_id': charge_id, 'de': (ev.get('status') or {}).get('previous'), 'para': atual,
                          'valor': str(valor_pago) if valor_pago is not None else None})
        tratados += 1
    return jsonify({'ok': True, 'eventos': tratados}), 200
