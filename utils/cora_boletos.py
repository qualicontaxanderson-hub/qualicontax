# -*- coding: utf-8 -*-
"""Boletos do Cora: emitir, consultar, cancelar, aviso de pagamento e baixa sozinha — 18/11/2026.

Anderson (18/11/2026): "Vamos montar o processo para o API do Cora para cobrança e extratos". Mesmo
caminho da Efí e do Bradesco: o título é o centro, ``registrar_baixa`` é o único escritor, e o boleto
aparece nas mesmas telas (Cobranças, janela da cobrança, e-mail, página do cliente).

* **Emitir** (``emitir``): o Cora devolve tudo pronto — linha digitável, código de barras, o PDF do
  boleto (``pdf_url`` público, como na Efí) e o Pix copia e cola. ``charge_id`` = id do Cora
  (``inv_...``); ``custom_id`` = 'QC-<id>' vai no campo ``code``.
* **Baixa**: três sinais, todos idempotentes (referência da baixa = 'cora:<inv_...>'):
  1. o aviso de pagamento (webhook ``/webhooks/cora/<segredo>``), cadastrado sozinho no Cora pela
     conta de cobrança (``garantir_webhook``) — o Cora aceita qualquer HTTPS;
  2. a consulta de hora em hora (7h-22h) de cada boleto em aberto + a lista de pagos dos últimos 10
     dias uma vez por dia (``rodar_de_hora_em_hora``). Desliga com CORA_BOLETOS_SYNC=0;
  3. o extrato pela API (``utils/cora_extrato.py``), que amarra o crédito à baixa.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

_ATOR = ('BOLETOS DO CORA (automático)', 'cora_boletos')
HORAS = range(7, 23)
ABERTOS = ('new', 'waiting', 'unpaid')
REF = 'cora:'


def _q(sql, p=(), one=False):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=True, fetch_one=one) if one else (execute_query(sql, p, fetch=True) or [])


def _x(sql, p=()):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=False)


def config(conta):
    """O que é só do Cora na conta de cobrança (coluna config_json): o aviso de pagamento cadastrado."""
    cfg = (conta or {}).get('config_json') or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except ValueError:
            cfg = {}
    return {'webhook_id': cfg.get('webhook_id'), 'webhook_url': cfg.get('webhook_url'),
            'webhook_em': cfg.get('webhook_em'), 'cert_validade': cfg.get('cert_validade')}


def _guardar_config(conta_id, **novos):
    c = _q('SELECT config_json FROM fin_cobranca_contas WHERE id = %s', (conta_id,), one=True) or {}
    cfg = c.get('config_json') or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except ValueError:
            cfg = {}
    cfg.update({k: v for k, v in novos.items()})
    _x('UPDATE fin_cobranca_contas SET config_json = %s, atualizado_em = NOW() WHERE id = %s',
       (json.dumps(cfg, ensure_ascii=False, default=str), conta_id))


# ---------------------------------------------------------------------------
# Emitir
# ---------------------------------------------------------------------------
def emitir(conta, boleto_id, pagador, itens, total, vencimento, mensagem=None):
    """Registra no Cora e grava a resposta no fin_boletos. Devolve (id do Cora, resposta).
    Levanta CoraErro/ValueError com a mensagem para a tela."""
    from models.fin_cobranca import FinCobrancaConta
    cora, c = FinCobrancaConta.cliente_cora(conta['id'])
    nomes = [str(i.get('nome') or '').strip() for i in (itens or []) if str(i.get('nome') or '').strip()]
    # o Cora imprime UM serviço: o nome é o 1º item (ou "Honorários e serviços") e a descrição lista os demais
    nome_servico = nomes[0] if len(nomes) == 1 else 'Honorários e serviços'
    descricao = ', '.join(nomes) if len(nomes) > 1 else (mensagem or '').replace('\n', ' ').strip()
    j = cora.criar_boleto(f'QC-{boleto_id}', pagador, nome_servico, descricao, total, vencimento,
                          juros_mes=c.get('juros_mes'), multa_pct=c.get('multa_pct'), com_pix=True,
                          email_aviso=((pagador.get('email') or '').strip() if c.get('avisa_por') == 'efi' else None))
    inv = str(j.get('id') or '')
    if not inv.startswith('inv_'):
        raise ValueError('O Cora respondeu sem o número do boleto. Consulte antes de emitir outro.')
    bs = (j.get('payment_options') or {}).get('bank_slip') or {}
    pix = ((j.get('pix') or {}).get('emv') or '').strip() or None
    linha = ''.join(ch for ch in str(bs.get('digitable') or '') if ch.isdigit()) or None
    barras = ''.join(ch for ch in str(bs.get('barcode') or '') if ch.isdigit()) or None
    url = (bs.get('url') or '')[:255] or None
    from utils.integrations.cora.client import situacao_da_consulta
    sit, _v, _d = situacao_da_consulta(j)
    _x("""UPDATE fin_boletos SET charge_id = %s, custom_id = %s, situacao = %s, linha_digitavel = %s,
                 codigo_barras = %s, link = %s, pdf_url = %s, pix_copia_cola = %s, pix_qrcode = NULL,
                 raw_resposta = %s, erro = NULL, atualizado_em = NOW()
           WHERE id = %s""",
       (inv, f'QC-{boleto_id}', 'waiting' if sit in ('new', 'waiting') else sit, linha, barras, url, url, pix,
        json.dumps(j, ensure_ascii=False), boleto_id))
    return inv, j


# ---------------------------------------------------------------------------
# Baixa
# ---------------------------------------------------------------------------
def _agente(acao, b, depois):
    from utils.atividade import registrar_agente
    registrar_agente(acao, 'financeiro', None, _ATOR[0], _ATOR[1], tabela='fin_titulos',
                     registro_id=b.get('titulo_id'), depois=dict(depois, boleto=b.get('charge_id')))


def _linha_do_extrato(b, valor, de, ate):
    """O crédito do boleto na conta Cora: mesmo valor, na janela, sem baixa, e só um."""
    from utils.extrato_ingest import mesma_conta
    conta = _q('SELECT fc.conta FROM fin_cobranca_contas c JOIN fin_contas fc ON fc.id = c.fin_conta_id WHERE c.id = %s',
               (b['conta_cobranca_id'],), one=True)
    if not conta or not conta.get('conta'):
        return None
    ls = [l for l in _q("""SELECT l.id, l.conta FROM extrato_lancamentos l
                            WHERE l.empresa_id = %s AND l.valor > 0 AND ABS(l.valor - %s) < 0.005
                              AND l.data BETWEEN %s AND %s
                              AND NOT EXISTS (SELECT 1 FROM fin_titulo_baixas x WHERE x.lancamento_id = l.id)""",
                        (b['empresa_id'], valor, de, ate)) if mesma_conta(l['conta'], conta['conta'])]
    return ls[0] if len(ls) == 1 else None


def _baixa_paga(b, valor_pago, pago_em):
    from utils.financeiro_core import registrar_baixa, BaixaInvalida
    t = _q('SELECT id, status, valor, valor_baixado FROM fin_titulos WHERE id = %s', (b['titulo_id'],), one=True)
    if not t or t['status'] == 'cancelado':
        return None
    saldo = Decimal(str(t['valor'])) - Decimal(str(t['valor_baixado'] or 0))
    ref = f"{REF}{b['charge_id']}"
    ja = _q('SELECT id, lancamento_id FROM fin_titulo_baixas WHERE titulo_id = %s AND referencia = %s', (t['id'], ref), one=True)
    if not ja:
        if saldo <= 0:
            return None
        pago = Decimal(str(valor_pago))
        valor, juros = (saldo, pago - saldo) if pago > saldo else (pago, Decimal('0'))
        try:
            r = registrar_baixa(t['id'], valor, pago_em, 'webhook', referencia=ref, juros=juros)
        except BaixaInvalida as exc:
            logger.warning('[cora-boletos] baixa do boleto %s recusada: %s', b['charge_id'], exc)
            return None
        ja = {'id': r['baixa_id'], 'lancamento_id': None}
        _agente('escrita.baixa_boleto_cora', b, {'valor': str(valor_pago), 'pago_em': str(pago_em),
                                                'status': r.get('status'), 'como': 'boleto pago no Cora'})
    if not ja['lancamento_id']:
        lanc = _linha_do_extrato(b, valor_pago, pago_em, pago_em + timedelta(days=3))
        if lanc:
            _x('UPDATE fin_titulo_baixas SET lancamento_id = %s, confirmado_extrato = 1 WHERE id = %s AND lancamento_id IS NULL',
               (lanc['id'], ja['id']))
    return ja['id']


def aplicar(b, atual, valor_pago=None, pago_em=None):
    """Aplica a situação do Cora ao boleto e ao título. Devolve 'baixa' ou None."""
    from models.fin_cobranca import FinBoleto
    feito = None
    if atual == 'paid' and b.get('titulo_id'):
        valor_pago = valor_pago or Decimal(str(b['valor']))
        pago_em = pago_em or date.today()
        feito = 'baixa' if _baixa_paga(b, valor_pago, pago_em) else None
    if atual != b.get('situacao') or feito:
        FinBoleto.atualizar_situacao(b['id'], atual, pago_em=pago_em if atual == 'paid' else None,
                                     valor_pago=valor_pago if atual == 'paid' else None)
    return feito


def consultar_e_aplicar(b, cora=None):
    """Consulta UM boleto no Cora e aplica. Devolve (situação, feito)."""
    from models.fin_cobranca import FinCobrancaConta
    from utils.integrations.cora.client import situacao_da_consulta
    if cora is None:
        cora, _ = FinCobrancaConta.cliente_cora(b['conta_cobranca_id'])
    sit, valor, pago_em = situacao_da_consulta(cora.consultar(b['charge_id']))
    if sit == 'waiting' and b['vencimento'] and b['vencimento'] < date.today():
        sit = 'unpaid'
    return sit, aplicar(b, sit, valor, pago_em)


def cancelar(b):
    from models.fin_cobranca import FinCobrancaConta
    from utils.integrations.cora.client import CoraErro
    cora, _ = FinCobrancaConta.cliente_cora(b['conta_cobranca_id'])
    try:
        return cora.cancelar(b['charge_id'])
    except CoraErro as exc:
        if exc.http == 422 and 'PAY-0005' in str(exc):
            # o estado não permite: já cancelado é idempotente para nós; pago não se cancela
            sit, _v, _d = _situacao_atual(cora, b)
            if sit == 'canceled':
                return {}
            raise CoraErro(f'O boleto não pode ser cancelado: ele está "{sit}" no Cora.', exc.http, exc.corpo) from exc
        raise


def _situacao_atual(cora, b):
    from utils.integrations.cora.client import situacao_da_consulta
    try:
        return situacao_da_consulta(cora.consultar(b['charge_id']))
    except Exception:
        return b.get('situacao'), None, None


# ---------------------------------------------------------------------------
# Aviso de pagamento (webhook)
# ---------------------------------------------------------------------------
def url_webhook(conta):
    from utils.cobranca_email import APP_URL
    return f"{APP_URL}/webhooks/cora/{conta['webhook_segredo']}"


def garantir_webhook(conta_id, cora=None):
    """Cadastra no Cora (uma vez) o endereço que recebe o aviso "boleto pago" desta conta. Devolve o id do
    endpoint no Cora ou None (sem erro: a consulta de hora em hora cobre)."""
    from models.fin_cobranca import FinCobrancaConta
    c = FinCobrancaConta.get(conta_id, com_segredos=True)
    if not c:
        return None
    url = url_webhook(c)
    cfg = config(c)
    if cfg.get('webhook_id') and cfg.get('webhook_url') == url:
        return cfg['webhook_id']
    if cora is None:
        cora, _ = FinCobrancaConta.cliente_cora(conta_id)
    try:
        for e in cora.endpoints():
            if (e.get('url') == url and str(e.get('resource') or '*') in ('invoice', '*')
                    and str(e.get('trigger') or '*').lower() in ('paid', '*')):
                _guardar_config(conta_id, webhook_id=e.get('id'), webhook_url=url, webhook_em=datetime.now().strftime('%Y-%m-%d %H:%M'))
                return e.get('id')
        j = cora.criar_endpoint(url, 'invoice', 'paid')
        _guardar_config(conta_id, webhook_id=j.get('id'), webhook_url=url, webhook_em=datetime.now().strftime('%Y-%m-%d %H:%M'))
        return j.get('id')
    except Exception as exc:
        logger.warning('[cora-boletos] conta %s: não cadastrou o aviso de pagamento: %s', conta_id, exc)
        return None


# ---------------------------------------------------------------------------
# De hora em hora
# ---------------------------------------------------------------------------
def sincronizar_conta(conta_id, com_lista=False):
    from models.fin_cobranca import FinCobrancaConta
    from utils.integrations.cora.client import CoraErro, situacao_da_consulta
    res = {'conta': conta_id, 'consultados': 0, 'baixas': 0, 'erros': []}
    cora, _ = FinCobrancaConta.cliente_cora(conta_id)
    abertos = _q(f"""SELECT * FROM fin_boletos WHERE conta_cobranca_id = %s AND provedor = 'cora'
                       AND situacao IN ({','.join(['%s'] * len(ABERTOS))}) AND charge_id IS NOT NULL""",
                 (conta_id, *ABERTOS))
    for b in abertos:
        try:
            _, feito = consultar_e_aplicar(b, cora)
            res['consultados'] += 1
            res['baixas'] += feito == 'baixa'
        except CoraErro as exc:
            res['erros'].append(f"{b['charge_id']}: {exc}")
    if com_lista:                                   # rede de segurança: pagos dos últimos 10 dias
        por_id = {b['charge_id']: b for b in abertos}
        try:
            for it in cora.pagos(date.today() - timedelta(days=10), date.today()):
                b = por_id.get(str(it.get('id') or ''))
                if not b:
                    continue
                sit, valor, pago_em = situacao_da_consulta(it)
                if sit == 'paid':
                    res['baixas'] += aplicar(dict(b, situacao='waiting'), 'paid', valor, pago_em) == 'baixa'
        except CoraErro as exc:
            res['erros'].append(f'lista de pagos: {exc}')
    return res


_CHAVE = 'cora_boletos_ultima_hora'
_CHAVE_DIA = 'cora_boletos_ultima_lista'


def rodar_de_hora_em_hora():
    """A cada tick do roteador: uma vez por hora, 7h-22h (Brasília), nas contas Cora ativas; a lista de pagos
    uma vez por dia; e confere que o aviso de pagamento está cadastrado. Desliga com CORA_BOLETOS_SYNC=0."""
    from zoneinfo import ZoneInfo
    if os.getenv('CORA_BOLETOS_SYNC', '1').strip() != '1':
        return None
    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    if agora.hour not in HORAS:
        return None
    contas = _q("SELECT id FROM fin_cobranca_contas WHERE provedor = 'cora' AND ativo = 1 AND COALESCE(so_consulta, 0) = 0")
    if not contas:
        return None
    marca = agora.strftime('%Y-%m-%d %H')
    r = _q('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE,), one=True)
    if r and r.get('valor') == marca:
        return None
    _x('INSERT INTO app_config (chave, valor) VALUES (%s, %s) ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()',
       (_CHAVE, marca))
    dia = agora.strftime('%Y-%m-%d')
    r = _q('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE_DIA,), one=True)
    com_lista = not (r and r.get('valor') == dia)
    if com_lista:
        _x('INSERT INTO app_config (chave, valor) VALUES (%s, %s) ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()',
           (_CHAVE_DIA, dia))
    saida = []
    for c in contas:
        try:
            res = sincronizar_conta(c['id'], com_lista=com_lista)
            if com_lista:
                garantir_webhook(c['id'])
        except Exception as exc:
            logger.exception('[cora-boletos] conta %s', c['id'])
            res = {'conta': c['id'], 'consultados': 0, 'baixas': 0, 'erros': [str(exc)]}
        saida.append(res)
        logger.warning('[cora-boletos] conta %s: %s consultado(s), %s baixa(s), %s erro(s)',
                       res['conta'], res['consultados'], res['baixas'], len(res['erros']))
    return saida
