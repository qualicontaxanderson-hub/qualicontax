# -*- coding: utf-8 -*-
"""“Este e-mail é da Qualicontax?” — página pública /v/<código> (27/11/2026).

Anderson: "podíamos colocar um botão de segurança que seria o cliente clicar no e-mail, exemplo ter certeza que esse
e-mail é da qualicontax, ao clicar chega um código no e-mail do cliente".

Todo e-mail para cliente sai com um código único no rodapé (utils/email_envio.enviar grava a linha em email_envios com
``token``). O cliente abre o link e vê: quando saiu, de qual caixa, para qual endereço (parte escondida), o assunto e um
resumo do documento (boleto: valor, vencimento, banco; orçamento, contrato, acordo: número e cliente). Para ter certeza,
pede um código de 6 dígitos — vai para o MESMO e-mail que recebeu a mensagem — e digita aqui: fica "Confirmado".

Sem login: só quem tem o código do link chega. Regras: código vale 15 minutos (relógio do BANCO, TIMESTAMPDIFF — o app
está em UTC), no máximo 3 códigos por e-mail, 1 por minuto, 5 tentativas de digitação por código. O código fica no banco
só como sha256. Nada aqui altera o documento: é consulta.
"""
import hashlib
import hmac
import logging
import secrets

from flask import Blueprint, abort, jsonify, render_template, request

from utils import email_envio
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
verifica = Blueprint('verifica', __name__)

SERVICO_NOME = {'Boleto': 'Cobrança de honorários (boleto)', 'Contrato': 'Contrato de prestação de serviços',
                'orcamento': 'Orçamento / proposta de serviços', 'Acordo': 'Acordo de pagamento'}
VALIDADE_SEG = 15 * 60
MAX_ENVIOS = 3
MAX_TENTATIVAS = 5


def _envio(token):
    if not token or len(token) > 40:
        return None
    return execute_query(
        'SELECT e.*, TIMESTAMPDIFF(SECOND, e.codigo_em, NOW()) AS idade_codigo, '
        "DATE_FORMAT(e.criado_em, '%d/%m/%Y') AS dia, DATE_FORMAT(e.criado_em, '%H:%i') AS hora, "
        "DATE_FORMAT(e.confirmado_em, '%d/%m/%Y às %H:%i') AS confirmado_txt "   # mysql-connector 9 (C): '%%' NÃO vira '%'; '%d' passa inteiro

        'FROM email_envios e WHERE e.token = %s AND e.ok = 1', (token,), fetch=True, fetch_one=True)


def _mascara(e):
    u, _, d = (e or '').partition('@')
    return (u[:2] + '***@' + d) if d else '***'


def _paras(e):
    return [p.strip() for p in (e.get('para') or '').replace(';', ',').split(',') if p.strip()]


def _hash(cod):
    return hashlib.sha256(('qc-verifica:' + cod).encode()).hexdigest()


def _resumo(e):
    """O que o e-mail levava, pelo documento de origem: lista de (rótulo, valor). Nunca estoura."""
    tipo, rid = e.get('referencia_tipo'), e.get('referencia_id')
    if not tipo or not rid:
        return []
    try:
        from utils import cobranca_email as ce
        if tipo == 'boleto':
            b = execute_query('SELECT b.*, cc.apelido AS conta_apelido, cc.provedor FROM fin_boletos b '
                              'LEFT JOIN fin_cobranca_contas cc ON cc.id = b.conta_cobranca_id WHERE b.id = %s',
                              (rid,), fetch=True, fetch_one=True)
            if not b:
                return []
            pago = b.get('situacao') in ('paid', 'settled', 'identified')
            linhas = [('Cobrança para', ce.nome_proprio_pagador(b)), ('Valor', ce._brl(b['valor'])),
                      ('Vencimento', ce._data(b['vencimento']))]
            if b.get('conta_apelido') or b.get('provedor'):
                linhas.append(('Banco do boleto', b.get('conta_apelido') or str(b.get('provedor') or '').title()))
            if pago:
                linhas.append(('Situação', 'Pagamento já recebido'))
            elif b.get('situacao') == 'canceled':
                linhas.append(('Situação', 'Boleto cancelado'))
            return linhas
        if tipo == 'grupo':
            g = execute_query('SELECT g.nome FROM fin_paginas_grupo p JOIN grupos_clientes g ON g.id = p.grupo_id WHERE p.id = %s',
                              (rid,), fetch=True, fetch_one=True)
            return [('Cobrança do grupo', g['nome'])] if g else []
        if tipo == 'orcamento':
            from models.orcamento import Orcamento
            o = Orcamento.get(rid)
            if not o:
                return []
            linhas = [('Orçamento nº', str(o.get('numero') or rid)), ('Para', o.get('cliente_nome') or '')]
            if o.get('total') is not None:
                linhas.append(('Total', ce._brl(o['total'])))
            return linhas
        if tipo == 'contrato':
            from models.prest_contrato import PrestContrato
            c = PrestContrato.get(rid)
            if not c:
                return []
            d = c.get('dados') or {}
            emp = (d.get('empresas') or [{}])[0]
            linhas = [('Contrato nº', str(c.get('numero') or rid)), ('Cliente', emp.get('razao') or '')]
            if d.get('valor'):
                linhas.append(('Honorários', ce._brl(d['valor']) + ' por mês'))
            return linhas
        if tipo == 'acordo':
            a = execute_query('SELECT a.numero, a.total, a.parcelas, a.valor_parcela, c.nome_razao_social AS cliente '
                              'FROM fin_acordos a JOIN clientes c ON c.id = a.cliente_id WHERE a.id = %s', (rid,), fetch=True, fetch_one=True)
            if not a:
                return []
            linhas = [('Acordo nº', str(a['numero'])), ('Cliente', a['cliente'] or ''), ('Total', ce._brl(a['total']))]
            if a.get('parcelas'):
                linhas.append(('Parcelas', '%d de %s' % (a['parcelas'], ce._brl(a['valor_parcela']))))
            return linhas
    except Exception:
        logger.exception('[verifica] resumo do envio %s (%s %s)', e.get('id'), tipo, rid)
    return []


@verifica.route('/v/<token>')
def ver(token):
    e = _envio(token)
    if not e:
        abort(404)
    if not e.get('aberto_em'):
        execute_query('UPDATE email_envios SET aberto_em = NOW() WHERE id = %s AND aberto_em IS NULL', (e['id'],), fetch=False)
    return render_template('verificar_email.html', e=e, servico=SERVICO_NOME.get(e.get('servico'), e.get('servico') or 'E-mail'),
                           para=[_mascara(p) for p in _paras(e)], resumo=_resumo(e),
                           confirmado=bool(e.get('confirmado_em')), pode_codigo=(e.get('codigo_envios') or 0) < MAX_ENVIOS,
                           slogan='a EMPRESA é sua, a PARCERIA é NOSSA')


@verifica.route('/v/<token>/codigo', methods=['POST'])
def codigo(token):
    e = _envio(token)
    if not e:
        abort(404)
    if e.get('confirmado_em'):
        return jsonify({'ok': True, 'confirmado': True})
    if (e.get('codigo_envios') or 0) >= MAX_ENVIOS:
        return jsonify({'ok': False, 'erro': 'Já foram enviados %d códigos para este e-mail. Confira a caixa de entrada e o spam; '
                                             'se não chegou, responda o e-mail que você recebeu.' % MAX_ENVIOS}), 429
    if e.get('idade_codigo') is not None and e['idade_codigo'] < 60:
        return jsonify({'ok': False, 'erro': 'Código enviado há menos de um minuto. Confira a caixa de entrada e o spam.'}), 429
    paras = _paras(e)
    if not paras:
        return jsonify({'ok': False, 'erro': 'Este envio não tem destinatário registrado.'}), 400
    cod = '%06d' % secrets.randbelow(10 ** 6)
    execute_query('UPDATE email_envios SET codigo = %s, codigo_em = NOW(), codigo_envios = codigo_envios + 1, codigo_tentativas = 0 '
                  'WHERE id = %s', (_hash(cod), e['id']), fetch=False)
    html, texto = _email_codigo(e, cod)
    ok, err = email_envio.enviar(paras, 'Seu código de confirmação: %s' % cod, html, texto,
                                 servico='Confirmação de e-mail', cliente=False, usuario_id=None)
    if not ok:
        return jsonify({'ok': False, 'erro': 'Não consegui enviar o código: %s' % err}), 500
    return jsonify({'ok': True, 'email': ', '.join(_mascara(p) for p in paras)})


@verifica.route('/v/<token>/confirmar', methods=['POST'])
def confirmar(token):
    e = _envio(token)
    if not e:
        abort(404)
    if e.get('confirmado_em'):
        return jsonify({'ok': True, 'quando': e.get('confirmado_txt')})
    dig = ''.join(ch for ch in (request.form.get('codigo') or '') if ch.isdigit())
    if len(dig) != 6:
        return jsonify({'ok': False, 'erro': 'Digite os 6 dígitos do código.'}), 400
    if not e.get('codigo') or e.get('idade_codigo') is None:
        return jsonify({'ok': False, 'erro': 'Peça o código primeiro.'}), 400
    if e['idade_codigo'] > VALIDADE_SEG:
        return jsonify({'ok': False, 'erro': 'O código venceu (vale 15 minutos). Peça outro.'}), 400
    if (e.get('codigo_tentativas') or 0) >= MAX_TENTATIVAS:
        return jsonify({'ok': False, 'erro': 'Muitas tentativas com este código. Peça outro.'}), 429
    execute_query('UPDATE email_envios SET codigo_tentativas = codigo_tentativas + 1 WHERE id = %s', (e['id'],), fetch=False)
    if not hmac.compare_digest(_hash(dig), e['codigo'] or ''):
        return jsonify({'ok': False, 'erro': 'Código errado. Confira os 6 dígitos do e-mail.'}), 400
    execute_query('UPDATE email_envios SET confirmado_em = NOW(), codigo = NULL WHERE id = %s', (e['id'],), fetch=False)
    e2 = _envio(token) or {}
    return jsonify({'ok': True, 'quando': e2.get('confirmado_txt')})


def _email_codigo(e, cod):
    """O e-mail do código, no molde dos e-mails do sistema (templates/emails/orcamento.html)."""
    from datetime import date
    from utils.cobranca_email import _env
    d = {'titulo': 'Código de confirmação', 'eyebrow': 'Este e-mail é da Qualicontax?', 'data_txt': date.today().strftime('%d/%m/%Y'),
         'cliente_nome': (e.get('assunto') or 'E-mail da Qualicontax')[:120], 'cliente_doc': '',
         'cliente_rotulo': 'E-mail que você está conferindo', 'saudacao': 'Olá!',
         'intro': 'Você pediu para confirmar que um e-mail que recebeu é mesmo da Qualicontax. Aqui está o seu código: volte à '
                  'página de verificação e digite os 6 dígitos. Ele vale por 15 minutos.',
         'kpis': [('Enviado em', '%s %s' % (e.get('dia') or '', e.get('hora') or '')), ('Pela caixa', e.get('remetente') or '')],
         'itens': [], 'total': '', 'botao': None, 'passos': [], 'codigo': cod,
         'aviso': 'Se não foi você que pediu este código, ignore este e-mail: nada acontece sem ele ser digitado.',
         'anexos_txt': '', 'slogan': 'a EMPRESA é sua, a PARCERIA é NOSSA',
         'rodape': 'Enviado por Qualicontax Assessoria Contábil. Em caso de dúvida, responda este e-mail.'}
    html = _env().get_template('orcamento.html').render(d=d).replace('vale por 30 minutos', 'vale por 15 minutos')
    texto = '\n'.join([d['titulo'], '', d['intro'], '', 'CÓDIGO: ' + cod + ' (vale 15 minutos)', '',
                       'E-mail conferido: ' + d['cliente_nome'], 'Enviado em: ' + d['kpis'][0][1], 'Pela caixa: ' + d['kpis'][1][1], '',
                       d['aviso'], '', d['slogan'], '', d['rodape']])
    return html, texto
