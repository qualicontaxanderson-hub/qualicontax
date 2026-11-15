# -*- coding: utf-8 -*-
"""Ligar a cobrança dentro do cartão da conta bancária — 15/11/2026.

Anderson (14/11/2026): "No cadastro da conta cadastramos a conta e dentro dela a Cobrança APIs";
"dois lugares para trabalhar e repetir informações" é péssimo. Então o banco, a agência, a conta,
a empresa e o CNPJ vêm do cadastro da conta (fin_contas); o certificado é o A1 do cadastro da
empresa (dfe_certificados); o e-mail é o do departamento. Aqui só se cola o que o portal do banco
mostrou (Client ID e Client Secret), a carteira e o ambiente. Ao salvar, já testa.

Por enquanto só o Bradesco (código 237). Os padrões são os do escritório, iguais aos da Efí:
sem juros e sem multa ("0 em tudo"), baixa 365 dias depois do vencimento (o máximo do Bradesco),
aviso ao cliente pelo nosso app, pela caixa do departamento Financeiro.

Rotas no blueprint ``financeiro``; permissão em ``utils/acesso.py`` (financeiro.cobranca.contas).
"""
import logging
import re

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

from models.fin_cobranca import FinCobrancaConta
from models.fin_empresa import FinEmpresa
from routes.financeiro import financeiro
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query
from utils.integrations.bradesco.client import BradescoErro

logger = logging.getLogger(__name__)
BANCOS_COM_COBRANCA = {'237': 'bradesco'}


def _num(s):
    return re.sub(r'\D', '', str(s or ''))


def _volta(fin_conta):
    return url_for('financeiro.contas', emp=fin_conta['empresa_id']) + '#contas'


def _dep_financeiro():
    """O departamento Financeiro (com e-mail) é o padrão do "Envia por"."""
    from routes.financeiro import _deps_envio
    deps = _deps_envio()
    for d in deps:
        if 'financ' in (d.get('nome') or '').lower():
            return d['id']
    return deps[0]['id'] if deps else None


@financeiro.route('/financeiro/contas/<int:fin_conta_id>/ligar-cobranca', methods=['POST'])
@permission_required('financeiro.cobranca')
def ligar_cobranca(fin_conta_id):
    fc = execute_query('SELECT * FROM fin_contas WHERE id = %s', (fin_conta_id,), fetch=True, fetch_one=True)
    if not fc or fc['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    provedor = BANCOS_COM_COBRANCA.get(_num(fc.get('banco_id')).lstrip('0'))
    if not provedor:
        flash('A cobrança pela API ainda não existe para este banco.', 'danger')
        return redirect(_volta(fc))
    f = request.form
    emp_id = fc['empresa_id']

    atual_id = (FinCobrancaConta.da_conta_bancaria([fin_conta_id]).get(fin_conta_id) or {}).get('id')
    atual = FinCobrancaConta.get(atual_id) if atual_id else None
    if atual and atual['provedor'] != provedor:
        atual = None
    from utils.bradesco_boletos import config
    cfg_atual = config(atual) if atual else {}
    # agência: do cadastro da conta; se o cadastro não tem, a já guardada na cobrança, ou digitada aqui
    # UMA vez. Fica SÓ na cobrança: gravá-la no cadastro mudava a grafia da conta ('16865' virava
    # '2505/16865'), o Extrato abria um cartão novo e as regras da conta paravam de valer (15/11/2026).
    agencia = _num(fc.get('agencia')) or cfg_atual.get('agencia') or _num(f.get('agencia'))[:4]
    if len(agencia) != 4:
        flash('Informe a agência (4 números, sem o dígito).', 'danger')
        return redirect(_volta(fc))
    conta_cob = _num(f.get('conta_cobranca'))[:7] or _num(fc.get('conta'))[:7]
    carteira = _num(f.get('carteira'))[:3] or '09'
    ambiente = 'producao' if f.get('ambiente') != 'homologacao' else 'homologacao'

    emp_nome = (FinEmpresa.mapa().get(emp_id) or 'empresa')
    # o que a tela não pergunta continua como está (ou nasce com o padrão do escritório)
    dados = {
        'apelido': (atual or {}).get('apelido') or f'Bradesco {emp_nome}'[:60],
        'provedor': provedor, 'ambiente': ambiente, 'fin_conta_id': str(fin_conta_id),
        'chave_pix': (atual or {}).get('chave_pix') or '',
        'juros_mes': str((atual or {}).get('juros_mes') or '0'), 'multa_pct': str((atual or {}).get('multa_pct') or '0'),
        'dias_baixa': str((atual or {}).get('dias_baixa') or 365),
        'dia_vencimento': str((atual or {}).get('dia_vencimento') or ''),
        'mensagem_padrao': (atual or {}).get('mensagem_padrao') or '',
        'avisa_por': (atual or {}).get('avisa_por') or 'app',
        'departamento_id': str((atual or {}).get('departamento_id') or _dep_financeiro() or ''),
        'brad_carteira': carteira, 'brad_agencia': agencia, 'brad_conta': conta_cob,
        'brad_telefone': cfg_atual.get('telefone') or '',
    }
    client_id = (f.get('client_id') or '').strip() or None
    client_secret = (f.get('client_secret') or '').strip() or None
    novo_id, erro = FinCobrancaConta.salvar(emp_id, dados, conta_id=(atual or {}).get('id'),
                                            usuario_id=current_user.id, client_id=client_id,
                                            client_secret=client_secret)
    if erro:
        flash(erro, 'danger')
        return redirect(_volta(fc))
    registrar('escrita.ligou_cobranca_na_conta', 'financeiro', tabela='fin_cobranca_contas', registro_id=novo_id,
              depois={'fin_conta_id': fin_conta_id, 'provedor': provedor, 'ambiente': ambiente, 'carteira': carteira,
                      'agencia': agencia, 'conta': conta_cob, 'trocou_chaves': bool(client_secret),
                      'certificado': 'cadastro da empresa'})
    resultado = testar(novo_id)
    flash(('Cobrança ligada e testada: ' if resultado.startswith('OK') else 'Cobrança salva, mas o teste falhou: ')
          + resultado, 'success' if resultado.startswith('OK') else 'danger')
    return redirect(_volta(fc))


@financeiro.route('/financeiro/contas/<int:fin_conta_id>/testar-cobranca', methods=['POST'])
@permission_required('financeiro.cobranca')
def testar_cobranca_da_conta(fin_conta_id):
    fc = execute_query('SELECT * FROM fin_contas WHERE id = %s', (fin_conta_id,), fetch=True, fetch_one=True)
    if not fc or fc['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    c = FinCobrancaConta.da_conta_bancaria([fin_conta_id]).get(fin_conta_id)
    if not c:
        abort(404)
    resultado = testar(c['id'])
    flash(f'Teste da cobrança: {resultado}', 'success' if resultado.startswith('OK') else 'danger')
    return redirect(_volta(fc))


@financeiro.route('/financeiro/contas/<int:fin_conta_id>/puxar-extrato', methods=['POST'])
@permission_required('financeiro.cobranca')
def puxar_extrato_da_conta(fin_conta_id):
    """Puxa agora o extrato pela API (os últimos 7 dias até hoje), igual à rodada automática (30 em 30 minutos)."""
    fc = execute_query('SELECT * FROM fin_contas WHERE id = %s', (fin_conta_id,), fetch=True, fetch_one=True)
    if not fc or fc['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    from utils import bradesco_extrato
    if not bradesco_extrato.contas(fin_conta_id):
        flash('Esta conta não tem a cobrança do Bradesco ligada em produção.', 'danger')
        return redirect(_volta(fc))
    res = bradesco_extrato.rodar(seco=False, fin_conta_id=fin_conta_id).get(fin_conta_id) or {}
    registrar('escrita.puxou_extrato_api', 'financeiro', tabela='fin_contas', registro_id=fin_conta_id,
              depois={k: (len(res.get(k) or []) if k in ('baixados', 'pendentes') else res.get(k))
                      for k in ('de', 'ate', 'lidos', 'novos', 'baixados', 'pendentes', 'erro')})
    if res.get('erro'):
        flash(f"O extrato não veio: {res['erro']}", 'danger')
    else:
        flash(f"Extrato do Bradesco puxado ({res.get('lidos', 0)} lido(s) de {res.get('de', '')[8:10]}/{res.get('de', '')[5:7]} "
              f"a {res.get('ate', '')[8:10]}/{res.get('ate', '')[5:7]}): {res.get('novos', 0)} novo(s), "
              f"{len(res.get('baixados') or [])} baixa(s), {len(res.get('pendentes') or [])} Pix sem título para escolher.",
              'success')
    return redirect(_volta(fc))


_CHAVE_VISIVEL = 'contas_com_cobranca_visivel'


def contas_com_cobranca_visivel():
    """fin_contas.id em que a chave "Cobrança" foi ligada (app_config, lista JSON)."""
    import json
    r = execute_query('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE_VISIVEL,), fetch=True, fetch_one=True)
    try:
        return {int(x) for x in json.loads((r or {}).get('valor') or '[]')}
    except (ValueError, TypeError):
        return set()


@financeiro.route('/financeiro/contas/<int:fin_conta_id>/cobranca-visivel', methods=['POST'])
@permission_required('financeiro.cobranca')
def cobranca_visivel_na_conta(fin_conta_id):
    """Liga/desliga a cobrança no cartão da conta (Anderson, 15/11/2026: "tem que ter um botão de liga e
    desliga ... se a gente ligar conseguimos usar se não ligar não aparece"). Só mostra ou esconde: a
    conta com a cobrança já ligada ao banco se desliga na aba Cobrança."""
    import json
    fc = execute_query('SELECT * FROM fin_contas WHERE id = %s', (fin_conta_id,), fetch=True, fetch_one=True)
    if not fc or fc['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    ligar = request.form.get('ligar') == '1'
    ids = contas_com_cobranca_visivel()
    antes = fin_conta_id in ids
    (ids.add if ligar else ids.discard)(fin_conta_id)
    execute_query('INSERT INTO app_config (chave, valor) VALUES (%s, %s) '
                  'ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()',
                  (_CHAVE_VISIVEL, json.dumps(sorted(ids))))
    registrar('escrita.cobranca_visivel_na_conta', 'financeiro', tabela='fin_contas', registro_id=fin_conta_id,
              antes={'cobranca': 'ligada' if antes else 'desligada'},
              depois={'cobranca': 'ligada' if ligar else 'desligada'})
    return redirect(_volta(fc))


def testar(conta_id):
    """Mesmo teste do botão Testar da aba Cobrança: grava o resultado e audita. Devolve o texto."""
    try:
        brad, _ = FinCobrancaConta.cliente_bradesco(conta_id)
        resultado = 'OK: ' + brad.testar()
    except (BradescoErro, ValueError) as exc:
        resultado = f'FALHOU: {exc}'
    except Exception as exc:                      # chave mestra, rede, Dropbox
        logger.exception('[cobranca] teste da conta %s', conta_id)
        resultado = f'FALHOU: {exc}'
    FinCobrancaConta.registrar_teste(conta_id, resultado)
    registrar('escrita.testou_conta_cobranca', 'financeiro', tabela='fin_cobranca_contas', registro_id=conta_id,
              depois={'resultado': resultado[:200]})
    return resultado
