# -*- coding: utf-8 -*-
"""O que acontece com o boleto na Efí vira o que acontece com o título aqui (14/11/2026).

Anderson (14/11/2026): "se eu baixar ou cancelar um boleto no APP do Banco EFI, ele baixar aqui no nosso
APP e informar onde foi baixado"; "Consultar de hora em hora".

Um caminho só para o aviso da Efí (webhook), o botão "Consultar na Efí" e a consulta de hora em hora:

* **paid** (pago): baixa automática da Efí no título (origem 'webhook', referência 'efi:<boleto>') e
  amarra ao crédito do extrato do mesmo valor, se já chegou e for um só;
* **settled** (marcado como pago à mão no app da Efí): baixa no título com referência
  'efi-app:<boleto>' — a tela mostra "baixado no app da Efí"; nenhum dinheiro passou pela conta, então
  não procura linha no extrato e não vira alerta de "pago sem extrato";
* **canceled** (cancelado no app da Efí): o título vai para PERDIDOS com o motivo "Boleto cancelado no
  app da Efí" — some das cobranças, fica guardado, e "Voltar para em aberto" desfaz. Só quando o título
  não tem baixa nem outro boleto valendo, e só se o cancelamento NÃO foi feito por aqui (o botão
  Cancelar daqui já grava 'canceled' antes do aviso chegar).

Vale para os nossos boletos e para os do Conexa (conta de cobrança "só consulta" com as credenciais da
aplicação "Conexa" da Efí — ver migrations/fin_cobranca_contas_so_consulta_14_11.py).
"""
import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

_ATOR = ('BOLETOS DA EFÍ (automático)', 'efi_boletos')
FINAIS = ('paid', 'settled', 'canceled')
VALENDO = ('new', 'waiting', 'unpaid', 'link', 'paid', 'settled')
MESES = 16               # boleto do Conexa mais antigo em aberto foi criado em ago/2025
HORAS = range(7, 23)     # de hora em hora, das 7h às 22h (Brasília)


def _q(sql, p=(), one=False):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=True, fetch_one=one) if one else (execute_query(sql, p, fetch=True) or [])


def _x(sql, p=()):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=False)


def _dia(s):
    try:
        return date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def _agente(acao, b, depois):
    from utils.atividade import registrar_agente
    registrar_agente(acao, 'financeiro', None, _ATOR[0], _ATOR[1], tabela='fin_titulos',
                     registro_id=b.get('titulo_id'), depois=dict(depois, boleto=b.get('charge_id')))


# ---------------------------------------------------------------------------
# Crédito do extrato do boleto pago
# ---------------------------------------------------------------------------
def linha_do_extrato(empresa_id, de, ate, valor, charge_id):
    """O crédito da Efí deste boleto: valor igual, entre ``de`` e ``ate``, ainda sem baixa. Linha que já
    tem o número do boleto (veio do OFX) só serve se for o DELE; sem número, só se for uma só."""
    from utils.efi_extrato import cobranca_da_descricao
    ls = _q("""SELECT l.id, l.descricao FROM extrato_lancamentos l
                WHERE l.empresa_id = %s AND l.valor > 0 AND ABS(l.valor - %s) < 0.005 AND l.data BETWEEN %s AND %s
                  AND l.descricao LIKE 'Recebimento de cobran%%'
                  AND NOT EXISTS (SELECT 1 FROM fin_titulo_baixas b WHERE b.lancamento_id = l.id)""",
            (empresa_id, valor, de, ate))
    dele = [l for l in ls if cobranca_da_descricao(l['descricao']) == str(charge_id)]
    if dele:
        return dele[0]
    sem = [l for l in ls if not cobranca_da_descricao(l['descricao'])]
    return sem[0] if len(sem) == 1 else None


# ---------------------------------------------------------------------------
# O núcleo
# ---------------------------------------------------------------------------
def _saldo(titulo_id):
    t = _q('SELECT id, empresa_id, status, valor, valor_baixado, contraparte_nome, cliente_id FROM fin_titulos WHERE id = %s',
           (titulo_id,), one=True)
    if not t:
        return None, Decimal('0')
    return t, Decimal(str(t['valor'])) - Decimal(str(t['valor_baixado'] or 0))


def _baixa_paga(b, valor_pago, pago_em, credito_em):
    from utils.financeiro_core import registrar_baixa, BaixaInvalida
    t, saldo = _saldo(b['titulo_id'])
    if not t or t['status'] == 'cancelado':
        return None
    ref = f"efi:{b['charge_id']}"
    ja = _q('SELECT id, lancamento_id FROM fin_titulo_baixas WHERE titulo_id = %s AND referencia = %s', (t['id'], ref), one=True)
    if not ja:
        if saldo <= 0:
            return None
        pago = Decimal(str(valor_pago))
        valor, juros = (saldo, pago - saldo) if pago > saldo else (pago, Decimal('0'))
        try:
            r = registrar_baixa(t['id'], valor, pago_em, 'webhook', referencia=ref, juros=juros)
        except BaixaInvalida as exc:
            logger.warning('[efi-boletos] baixa do boleto %s recusada: %s', b['charge_id'], exc)
            return None
        ja = {'id': r['baixa_id'], 'lancamento_id': None}
        _agente('escrita.baixa_boleto_efi', b, {'valor': str(valor_pago), 'pago_em': str(pago_em),
                                               'status': r.get('status'), 'como': 'boleto pago na Efí'})
    if not ja['lancamento_id']:
        ate = (credito_em or pago_em) + timedelta(days=1)
        lanc = linha_do_extrato(t['empresa_id'], pago_em, ate, valor_pago, b['charge_id'])
        if lanc:
            _x('UPDATE fin_titulo_baixas SET lancamento_id = %s, confirmado_extrato = 1 WHERE id = %s AND lancamento_id IS NULL',
               (lanc['id'], ja['id']))
            if 'protocolo' in str(lanc['descricao']):     # linha do CNAB: ganha o nome como no OFX
                _x('UPDATE extrato_lancamentos SET descricao = %s WHERE id = %s',
                   (f"Recebimento de cobrança: {b['charge_id']} de {t['contraparte_nome']}"[:500], lanc['id']))
    return ja['id']


def _baixa_no_app(b, quando):
    from utils.financeiro_core import registrar_baixa, BaixaInvalida
    t, saldo = _saldo(b['titulo_id'])
    if not t or t['status'] == 'cancelado' or saldo <= 0:
        return None
    try:
        r = registrar_baixa(t['id'], saldo, quando, 'webhook', referencia=f"efi-app:{b['charge_id']}")
    except BaixaInvalida as exc:
        logger.warning('[efi-boletos] baixa no app do boleto %s recusada: %s', b['charge_id'], exc)
        return None
    _agente('escrita.baixa_boleto_no_app_efi', b, {'valor': str(saldo), 'data': str(quando), 'status': r.get('status'),
                                                  'como': 'boleto marcado como pago no app da Efí'})
    return r['baixa_id']


def _cancelado_no_app(b):
    t, _ = _saldo(b['titulo_id'])
    if not t or t['status'] != 'aberto' or Decimal(str(t['valor_baixado'] or 0)) > 0:
        return False
    outro = _q(f"""SELECT 1 x FROM fin_boletos WHERE titulo_id = %s AND id <> %s
                     AND situacao IN ({','.join(['%s'] * len(VALENDO))}) LIMIT 1""",
               (t['id'], b['id'], *VALENDO), one=True)
    if outro:
        return False
    from models.fin_titulo import FinTitulo
    if not FinTitulo.cancelar(t['id']):
        return False
    _x('UPDATE fin_titulos SET perdido_em = NOW(), perdido_por = NULL, perdido_motivo = %s WHERE id = %s',
       ('Boleto cancelado no app da Efí', t['id']))
    _agente('escrita.titulo_perdido_boleto_cancelado_efi', b, {'motivo': 'Boleto cancelado no app da Efí'})
    return True


def aplicar(b, atual, valor_pago=None, pago_em=None, credito_em=None, quando=None, evento_id=None, token=None):
    """Aplica a situação ``atual`` da Efí ao boleto ``b`` (linha de fin_boletos) e ao título.
    Devolve o que fez: 'baixa', 'baixa-app', 'perdido' ou None."""
    from models.fin_cobranca import FinBoleto
    antes = b.get('situacao')
    feito = None
    if b.get('titulo_id'):
        if atual == 'paid' and valor_pago and pago_em:
            feito = 'baixa' if _baixa_paga(b, valor_pago, pago_em, credito_em) else None
        elif atual == 'settled' and antes != 'settled':
            feito = 'baixa-app' if _baixa_no_app(b, quando or date.today()) else None
        elif atual == 'canceled' and antes != 'canceled':
            feito = 'perdido' if _cancelado_no_app(b) else None
    FinBoleto.atualizar_situacao(b['id'], atual, pago_em=pago_em if atual == 'paid' else None,
                                 valor_pago=valor_pago if atual == 'paid' else None,
                                 evento_id=evento_id, token=token)
    return feito


# ---------------------------------------------------------------------------
# Consulta (botão e de hora em hora)
# ---------------------------------------------------------------------------
def _quando_do_historico(efi, charge_id):
    """Dia do último evento do boleto (para 'marcado como pago' / 'cancelado' sem data na lista)."""
    try:
        h = (efi.consultar(charge_id).get('history') or [])
        return _dia(h[-1].get('created_at')) if h else None
    except Exception:
        return None


def do_boleto_efi(c):
    """(situação, valor_pago, pago_em, credito_em) de um boleto como a Efí devolve (lista ou consulta)."""
    p = c.get('payment') or {}
    atual = c.get('status')
    if atual != 'paid':
        return atual, None, None, None
    valor = (Decimal(int(p.get('paid_value') or c.get('paid_value') or c.get('total') or 0)) / 100).quantize(Decimal('0.01'))
    credito = _dia(p.get('paid_at'))
    pago = _dia(p.get('received_by_bank_at')) or credito
    return atual, valor, pago, credito


def listar_tudo(efi, meses=MESES):
    """Todos os boletos que a aplicação criou nos últimos ``meses`` (mês a mês, 100 por página): {id: boleto}."""
    hoje = date.today()
    a, m = hoje.year, hoje.month - meses
    while m <= 0:
        a, m = a - 1, m + 12
    bol, ini = {}, date(a, m, 1)
    while ini <= hoje:
        fim = min(date(ini.year + (ini.month == 12), ini.month % 12 + 1, 1) - timedelta(days=1), hoje)
        pagina = 1
        while True:
            lote = efi.listar_boletos(ini, fim, pagina)
            for c in lote:
                bol[c['id']] = c
            if len(lote) < 100:
                break
            pagina += 1
        ini = fim + timedelta(days=1)
    return bol


def sincronizar_conta(conta_id, efi=None):
    """Lista os boletos da conta na Efí e aplica o que mudou. Devolve o resumo."""
    from models.fin_cobranca import FinCobrancaConta, FinBoleto
    res = {'conta': conta_id, 'boletos': 0, 'mudaram': 0, 'baixas': 0, 'baixas_app': 0, 'perdidos': 0, 'erros': []}
    try:
        if efi is None:
            efi, conta = FinCobrancaConta.cliente_efi(conta_id)
        else:
            conta = FinCobrancaConta.get(conta_id)
        hoje = date.today()
        bol = listar_tudo(efi)
        res['boletos'] = len(bol)
        # os boletos da conta de uma vez (uma consulta por boleto levava minutos com o banco longe)
        nossos = {str(r['charge_id']): r for r in _q('SELECT * FROM fin_boletos WHERE conta_cobranca_id = %s AND charge_id IS NOT NULL',
                                                     (conta_id,))}
        # pago há pouco e ainda pendente aqui: título com saldo, ou baixa da Efí sem o crédito do extrato
        # amarrado (o extrato chega no dia seguinte). O pago que já foi quitado por outro caminho (relatório
        # do Conexa, Pix) não é revisto toda hora — 84 boletos passavam pela rotina a cada hora (14/11).
        abertos = {r['id'] for r in _q("""SELECT DISTINCT t.id FROM fin_titulos t JOIN fin_boletos b ON b.titulo_id = t.id
                                            WHERE b.conta_cobranca_id = %s AND t.status IN ('aberto', 'parcial')""",
                                         (conta_id,))}
        sem_linha = {r['referencia'] for r in _q("""SELECT referencia FROM fin_titulo_baixas WHERE referencia LIKE 'efi:%%'
                                                      AND lancamento_id IS NULL AND data_baixa >= %s""",
                                                   (hoje - timedelta(days=12),))}
        for c in bol.values():
            b = nossos.get(str(c['id']))
            if not b:
                continue
            atual, valor, pago, credito = do_boleto_efi(c)
            pendente = (atual == 'paid' and b.get('titulo_id') and pago and pago >= hoje - timedelta(days=10)
                        and (b['titulo_id'] in abertos or f"efi:{c['id']}" in sem_linha))
            if atual == b['situacao'] and not pendente:
                continue
            quando = _quando_do_historico(efi, c['id']) if atual in ('settled', 'canceled') else None
            feito = aplicar(b, atual, valor, pago, credito, quando)
            res['mudaram'] += int(atual != b['situacao'])
            res['baixas'] += int(feito == 'baixa')
            res['baixas_app'] += int(feito == 'baixa-app')
            res['perdidos'] += int(feito == 'perdido')
    except Exception as e:
        logger.exception('[efi-boletos] conta %s', conta_id)
        res['erros'].append(str(e)[:300])
    return res


_CHAVE = 'efi_boletos_ultima_hora'


def rodar_de_hora_em_hora():
    """Chamado a cada tick do roteador: uma vez por hora, das 7h às 22h (Brasília), em todas as contas Efí de
    produção ativas. Desliga com EFI_BOLETOS_SYNC=0."""
    from zoneinfo import ZoneInfo
    if os.getenv('EFI_BOLETOS_SYNC', '1').strip() != '1':
        return None
    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    if agora.hour not in HORAS:
        return None
    marca = agora.strftime('%Y-%m-%d %H')
    r = _q('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE,), one=True)
    if r and r.get('valor') == marca:
        return None
    _x('INSERT INTO app_config (chave, valor) VALUES (%s, %s) ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()',
       (_CHAVE, marca))
    contas = _q("SELECT id FROM fin_cobranca_contas WHERE provedor = 'efi' AND ambiente = 'producao' AND ativo = 1")
    saida = []
    for c in contas:
        res = sincronizar_conta(c['id'])
        saida.append(res)
        logger.warning('[efi-boletos] conta %s: %s boleto(s) lidos, %s mudaram, %s baixa(s), %s baixa(s) no app, '
                       '%s perdido(s), %s erro(s)', res['conta'], res['boletos'], res['mudaram'], res['baixas'],
                       res['baixas_app'], res['perdidos'], len(res['erros']))
        for e in res['erros']:
            logger.warning('[efi-boletos]   %s', e)
    return saida
