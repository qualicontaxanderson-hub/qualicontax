# -*- coding: utf-8 -*-
"""Lote mensal de honorários (parte 6, etapa 3 — 06/11/2026).

Por volta do dia 25 o Anderson emite a cobrança do mês: para cada contrato ATIVO na competência,
honorários (as discriminações do contrato) + a parcela do 13º que cai nessa competência + os
lançamentos MENSAL aprovados → UMA nota (se o contrato emite nota) + UM boleto Efí + e-mail aos
contatos do Financeiro. Vencimento: o dia do contrato no mês seguinte à competência.

Idempotente por (lote, cliente) em ``fin_lote_itens``: rodar de novo só cobra quem ainda não foi.
A emissão roda numa thread (215 empresas levam minutos); a tela acompanha pelo status.
"""
import calendar
import json
import logging
import threading
from datetime import date, datetime
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from models.fin_contrato import EMPRESA_QUALICONTAX, SERVICO_HONORARIOS, FinContrato
from models.fin_lancamento import FinLancamento, FinLoteMensal, mes_seguinte
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
SERVICO_DECIMO = {('DUAS', 0): 2, ('DUAS', 1): 3, ('UNICA', 0): 4}     # nfse_servicos: 13º 1ª, 2ª, única
_rodando = {}


def _d(v):
    return Decimal(str(v or 0)).quantize(Decimal('0.01'))


def vencimento_para(dia, competencia, hoje=None):
    """Dia do contrato no mês seguinte à competência (31 em mês de 30 vira 30); nunca antes de hoje."""
    m = mes_seguinte(competencia)
    d = date(m.year, m.month, min(int(dia or 10), calendar.monthrange(m.year, m.month)[1]))
    return max(d, hoje or date.today())


def itens_da_empresa(contrato, competencia, itens_contrato=None, lancamentos=None):
    """O que a empresa paga nesta competência: honorários (discriminações), 13º e lançamentos aprovados.
    ``itens_contrato``/``lancamentos`` vêm pré-carregados pela prévia (215 empresas = 3 consultas, não 430)."""
    itens = []
    if itens_contrato is None:
        itens_contrato = FinContrato.itens(contrato['id'])
    for i in itens_contrato:
        itens.append({'origem': 'honorarios', 'servico_id': i['servico_id'] or SERVICO_HONORARIOS,
                      'nome': i['descricao'], 'valor': str(_d(i['valor']))})
    tipo = contrato.get('decimo_tipo') or 'NENHUM'
    for n, (comp, valor) in enumerate(FinContrato.parcelas_decimo(contrato, competencia.year)):
        if comp == competencia and valor > 0:
            sid = SERVICO_DECIMO.get((tipo, n), 4 if tipo != 'DUAS' else (2 if n == 0 else 3))
            itens.append({'origem': 'decimo', 'servico_id': sid, 'nome': '13º honorário', 'valor': str(_d(valor))})
    if lancamentos is None:
        lancamentos = FinLancamento.para_o_mensal(contrato['empresa_id'], competencia, contrato['cliente_id'])
    for l in lancamentos:
        itens.append({'origem': 'lancamento', 'servico_id': l['servico_id'], 'nome': l['descricao'] or l['servico_nome'],
                      'valor': str(_d(l['valor_total'])), 'lancamento_id': l['id']})
    return itens


def contratos_da_competencia(empresa_id, competencia):
    fim_mes = date(competencia.year, competencia.month, calendar.monthrange(competencia.year, competencia.month)[1])
    return [c for c in FinContrato.listar(empresa_id, situacao='ATIVO')
            if (c['inicio'] or competencia) <= fim_mes and (not c['fim'] or c['fim'] >= competencia)]


def previa(empresa_id, competencia, so_clientes=None):
    """Linhas da tela: uma por empresa, com itens, total, vencimento e o que já saiu neste lote."""
    lote = FinLoteMensal.get(empresa_id, competencia)
    feitos = {}
    if lote:
        for r in execute_query('SELECT * FROM fin_lote_itens WHERE lote_id = %s', (lote['id'],), fetch=True) or []:
            feitos[r['cliente_id']] = r
    contratos = contratos_da_competencia(empresa_id, competencia)
    if so_clientes:
        contratos = [c for c in contratos if c['cliente_id'] in so_clientes]
    por_contrato, por_cliente = {}, {}
    if contratos:
        ids = [c['id'] for c in contratos]
        for i in execute_query('SELECT * FROM fin_contrato_itens WHERE contrato_id IN (%s) ORDER BY ordem, id' % ','.join(['%s'] * len(ids)),
                               tuple(ids), fetch=True) or []:
            por_contrato.setdefault(i['contrato_id'], []).append(i)
        for l in FinLancamento.para_o_mensal(empresa_id, competencia):
            por_cliente.setdefault(l['cliente_id'], []).append(l)
    linhas = []
    for c in contratos:
        itens = itens_da_empresa(c, competencia, por_contrato.get(c['id'], []), por_cliente.get(c['cliente_id'], []))
        total = sum(_d(i['valor']) for i in itens)
        linhas.append({'contrato': c, 'cliente_id': c['cliente_id'], 'numero': c['numero_cliente'], 'empresa': c['nome_razao_social'],
                       'itens': itens, 'total': total, 'vencimento': vencimento_para(c['dia_vencimento'], competencia),
                       'nota': bool(c.get('gerar_nfse')), 'boleto': bool(c.get('gerar_boleto')),
                       'contatos': c.get('n_contatos_fin') or 0, 'feito': feitos.get(c['cliente_id'])})
    return lote, linhas


def _cobrar(linha, lote_id, usuario_id, conta, cfg, empresa_id):
    """Nota + boleto + e-mail de UMA empresa. Grava a linha em fin_lote_itens. Devolve o status."""
    from models.fin_cobranca import FinBoleto
    from models.nfse_emissao import NfseConfig, NfseEmitida, tomador_do_cliente
    from routes.financeiro_cobranca import _pagador_do_tomador, emitir_boleto_avulso, gerar_boleto_da_nota, sugestao_boleto
    from utils import nfse_emissor
    itens, total, venc = linha['itens'], linha['total'], linha['vencimento']
    competencia = linha['competencia']
    rid = execute_query(
        """INSERT INTO fin_lote_itens (lote_id, cliente_id, contrato_id, itens, total, vencimento, status)
           VALUES (%s, %s, %s, %s, %s, %s, 'pendente')
           ON DUPLICATE KEY UPDATE itens = VALUES(itens), total = VALUES(total), vencimento = VALUES(vencimento), id = LAST_INSERT_ID(id)""",
        (lote_id, linha['cliente_id'], linha['contrato']['id'], json.dumps(itens, ensure_ascii=False), total, venc), fetch=False)

    def fechar(status, erro=None, titulo_id=None, nfse_id=None, boleto_id=None, email=None):
        execute_query("""UPDATE fin_lote_itens SET status = %s, erro = %s, titulo_id = %s, nfse_id = %s, boleto_id = %s, email = %s
                         WHERE lote_id = %s AND cliente_id = %s""",
                      (status, (erro or None), titulo_id, nfse_id, boleto_id, (email or '')[:255] or None, lote_id, linha['cliente_id']), fetch=False)
        return status

    if total <= 0:
        return fechar('pulado', 'sem valor a cobrar')
    t = tomador_do_cliente(linha['cliente_id'])
    if not t or not t.get('documento'):
        return fechar('erro', 'cadastro sem CPF/CNPJ')
    _v, mensagem = sugestao_boleto(conta, competencia)
    emite_nota = bool(cfg) and linha['nota'] and any(i['servico_id'] for i in itens)
    nfse_id = boleto_id = titulo_id = None
    avisos = []
    try:
        if emite_nota:
            serie = cfg.get('serie_nacional') if (cfg.get('emissor') or 'nacional') == 'nacional' else cfg.get('serie_sp')
            form = MultiDict([('serie', str(serie or '')), ('competencia', competencia.isoformat()),
                              ('tomador_cliente_id', str(linha['cliente_id'])),
                              ('descricao', NfseConfig.descricao_para(cfg, competencia))]
                             + [('item_servico_id', str(i['servico_id'])) for i in itens]
                             + [('item_valor', i['valor']) for i in itens])
            pedido = nfse_emissor.pedido_do_form(form, empresa_id, cfg)
            pedido.origem = 'lote'
            nota = nfse_emissor.emitir(pedido, cfg, usuario_id=usuario_id)
            if not nota or nota['situacao'] != 'autorizada':
                return fechar('erro', 'nota não autorizada: %s' % ((nota or {}).get('erro') or (nota or {}).get('situacao') or 'falha'),
                              nfse_id=(nota or {}).get('id'))
            nfse_id = nota['id']
            if linha['boleto']:
                n = NfseEmitida.get(nfse_id)
                ok, msg, aviso = gerar_boleto_da_nota(n, conta, venc, mensagem, usuario_id)
                if not ok:
                    return fechar('erro', 'nota ok, boleto não: ' + msg, nfse_id=nfse_id)
                b = FinBoleto.da_nota(nfse_id)
                boleto_id, titulo_id = (b or {}).get('id'), (b or {}).get('titulo_id')
                avisos.append(aviso or '')
        else:
            itens_b = [{'servico_id': i['servico_id'], 'nome': i['nome'], 'valor': Decimal(i['valor']), 'emite_nota': False} for i in itens]
            boleto_id, ok, msg, aviso = emitir_boleto_avulso(empresa_id, conta, _pagador_do_tomador(t), itens_b, total,
                                                             competencia, venc, mensagem, None, usuario_id)
            if not ok:
                return fechar('erro', msg)
            titulo_id = (FinBoleto.get(boleto_id) or {}).get('titulo_id')
            avisos.append(aviso or '')
    except Exception as exc:
        logger.exception('[lote] empresa %s', linha['cliente_id'])
        return fechar('erro', str(exc)[:1000], nfse_id=nfse_id, boleto_id=boleto_id)
    for i in itens:
        if i.get('lancamento_id'):
            FinLancamento.marcar_faturado(i['lancamento_id'], titulo_id=titulo_id, nfse_id=nfse_id, boleto_id=boleto_id, lote_id=lote_id)
    # comissão de honorários (cliente "dele") e de indicação: nasce aqui, liberada quando o título for pago
    honor = sum(_d(i['valor']) for i in itens if i['origem'] == 'honorarios')
    try:
        FinLancamento.comissoes_do_lote(empresa_id, linha['cliente_id'], competencia, titulo_id, lote_id, honor, nfse_id, boleto_id)
    except Exception:
        logger.exception('[lote] comissões da empresa %s', linha['cliente_id'])
    return fechar('ok', titulo_id=titulo_id, nfse_id=nfse_id, boleto_id=boleto_id, email='; '.join(a for a in avisos if a))


def rodar(app, empresa_id, competencia, usuario_id, so_clientes=None):
    """Emite o lote inteiro (ou só ``so_clientes``) numa thread. Devolve o id do lote."""
    from models.fin_cobranca import FinCobrancaConta
    from models.nfse_emissao import NfseConfig
    lote_id = FinLoteMensal.abrir(empresa_id, competencia)
    if _rodando.get(lote_id):
        return lote_id
    conta = FinCobrancaConta.padrao_da_empresa(empresa_id)
    cfg = NfseConfig.get(empresa_id)
    if not conta:
        raise ValueError('Não há conta de cobrança ativa.')

    def alvo():
        _rodando[lote_id] = True
        try:
            with app.app_context(), app.test_request_context('https://app.qualicontax.com.br/'):
                _lote, linhas = previa(empresa_id, competencia, so_clientes=so_clientes)
                for linha in linhas:
                    if linha['feito'] and linha['feito']['status'] == 'ok':
                        continue
                    linha['competencia'] = competencia
                    _cobrar(linha, lote_id, usuario_id, conta, cfg, empresa_id)
                r = execute_query("SELECT COUNT(*) n, COALESCE(SUM(total), 0) t FROM fin_lote_itens WHERE lote_id = %s AND status = 'ok'",
                                  (lote_id,), fetch=True, fetch_one=True)
                FinLoteMensal.marcar_emitido(lote_id, usuario_id, r['n'], r['t'])
        except Exception:
            logger.exception('[lote] rodada do lote %s', lote_id)
        finally:
            _rodando.pop(lote_id, None)

    threading.Thread(target=alvo, name='lote-%s' % lote_id, daemon=True).start()
    return lote_id


def status(lote_id):
    r = execute_query("""SELECT COUNT(*) total, SUM(status = 'ok') ok, SUM(status = 'erro') erro, SUM(status = 'pendente') pendente,
                                SUM(status = 'pulado') pulado, COALESCE(SUM(CASE WHEN status = 'ok' THEN total END), 0) valor_ok
                           FROM fin_lote_itens WHERE lote_id = %s""", (lote_id,), fetch=True, fetch_one=True) or {}
    r['rodando'] = bool(_rodando.get(lote_id))
    return r
