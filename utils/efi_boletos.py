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
    ja = _q('SELECT id, lancamento_id, valor, juros, data_baixa FROM fin_titulo_baixas WHERE titulo_id = %s AND referencia = %s',
            (t['id'], ref), one=True)
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
    else:
        _confirmar_identificada(b, t, ja, valor_pago, pago_em)
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


def _baixa_identificada(b, dia):
    """"Pagamento identificado" na Efí (16/11/2026): o cliente pagou e o banco ainda vai compensar. Anderson:
    "não pode aparecer como aguardando, mas já sair do atrasado ou a receber e ir pro recebido". Baixa o saldo
    do título no dia em que o banco recebeu, com a MESMA referência da baixa de pago ('efi:<boleto>'): quando
    a confirmação chegar, _baixa_paga acha esta baixa, completa data e juros e amarra o extrato — nada duplica."""
    from utils.financeiro_core import registrar_baixa, BaixaInvalida
    t, saldo = _saldo(b['titulo_id'])
    if not t or t['status'] == 'cancelado':
        return None
    ref = f"efi:{b['charge_id']}"
    ja = _q('SELECT id FROM fin_titulo_baixas WHERE titulo_id = %s AND referencia = %s', (t['id'], ref), one=True)
    if ja:
        return ja['id']
    if saldo <= 0:
        return None
    try:
        r = registrar_baixa(t['id'], saldo, dia, 'webhook', referencia=ref)
    except BaixaInvalida as exc:
        logger.warning('[efi-boletos] baixa do boleto identificado %s recusada: %s', b['charge_id'], exc)
        return None
    _agente('escrita.baixa_boleto_identificado_efi', b, {'valor': str(saldo), 'data': str(dia), 'status': r.get('status'),
                                                        'como': 'pagamento identificado na Efí (o banco ainda vai compensar)'})
    return r['baixa_id']


def _confirmar_identificada(b, t, ja, valor_pago, pago_em):
    """A confirmação (paid) chegou para uma baixa feita no "pagamento identificado": acerta a data do pagamento
    e lança como juros o que veio a mais do que o saldo baixado. Só mexe no que difere."""
    pago = Decimal(str(valor_pago or 0))
    valor = Decimal(str(ja.get('valor') or 0))
    juros = Decimal(str(ja.get('juros') or 0))
    novos = {}
    if pago_em and ja.get('data_baixa') != pago_em:
        novos['data_baixa'] = pago_em
    if pago > valor and juros == 0:
        novos['juros'] = pago - valor
    if not novos:
        return
    sets = ', '.join(f'{k} = %s' for k in novos)
    _x(f'UPDATE fin_titulo_baixas SET {sets} WHERE id = %s', tuple(novos.values()) + (ja['id'],))
    _agente('escrita.baixa_identificada_confirmada_efi', b, {'baixa_id': ja['id'], **{k: str(v) for k, v in novos.items()},
                                                           'como': 'a Efí confirmou o pagamento identificado'})


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
        elif atual == 'identified':
            feito = 'baixa' if _baixa_identificada(b, pago_em or quando or date.today()) else None
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
    if atual == 'identified':                 # o banco recebeu (received_by_bank_at) e ainda vai compensar
        return atual, None, _dia(p.get('received_by_bank_at')), None
    if atual != 'paid':
        return atual, None, None, None
    valor = (Decimal(int(p.get('paid_value') or c.get('paid_value') or c.get('total') or 0)) / 100).quantize(Decimal('0.01'))
    credito = _dia(p.get('paid_at'))
    pago = _dia(p.get('received_by_bank_at')) or credito
    return atual, valor, pago, credito


_PAGADOR = {}            # charge_id -> {'doc', 'nome', 'cliente_id'} (por processo)


def _cliente_pelo_doc(doc):
    """O cadastro com este CNPJ/CPF (um só); entre repetidos, o ativo com número de cliente."""
    if not doc:
        return None
    rs = _q("""SELECT id, numero_cliente, situacao, COALESCE(avulso, 0) AS avulso FROM clientes
                WHERE REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '/', ''), '-', '') = %s
                ORDER BY (situacao = 'ATIVO') DESC, (numero_cliente IS NULL), avulso, id""", (doc,))
    return rs[0]['id'] if rs else None


def pagador_da_cobranca(charge_id, empresa_id=None):
    """Quem pagou a cobrança nº ``charge_id``, pela API da Efí: {'doc' (só dígitos), 'nome', 'cliente_id'} ou None.

    26/11/2026 — Anderson: "essas vinculações não deveria ser pelo nome e sim pelo CNPJ, para não ficar nisso". O OFX/CNAB
    só traz o nome que o banco escreve (razão social antiga, erro de grafia, matriz e filial iguais); a cobrança na Efí
    traz o CNPJ/CPF do pagador. Pergunta primeiro à aplicação "Conexa" (só consulta), depois às contas de produção."""
    import re
    cid = str(charge_id or '').strip()
    if not cid.isdigit():
        return None
    if cid in _PAGADOR:
        return _PAGADOR[cid]
    from models.fin_cobranca import FinCobrancaConta
    contas = _q("""SELECT id FROM fin_cobranca_contas WHERE provedor = 'efi' AND ambiente = 'producao' AND ativo = 1
                    %s ORDER BY so_consulta DESC, id""" % ('AND empresa_id = %s' if empresa_id else ''), (empresa_id,) if empresa_id else ())
    achado = None
    for c in contas:
        try:
            efi, _ = FinCobrancaConta.cliente_efi(c['id'])
            dados = efi.consultar(cid)
        except Exception:
            continue
        cust = dados.get('customer') or {}
        jp = cust.get('juridical_person') or {}
        doc = re.sub(r'\D', '', jp.get('cnpj') or cust.get('cpf') or '')
        if doc:
            achado = {'doc': doc, 'nome': (jp.get('corporate_name') or cust.get('name') or '').strip(), 'cliente_id': _cliente_pelo_doc(doc), 'conta': c['id']}
            break
    _PAGADOR[cid] = achado
    return achado


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
        res['erros'].append(f'{type(e).__name__}: {e}'[:300])
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
    inicio = datetime.now()
    for c in contas:
        res = sincronizar_conta(c['id'])
        saida.append(res)
        logger.warning('[efi-boletos] conta %s: %s boleto(s) lidos, %s mudaram, %s baixa(s), %s baixa(s) no app, '
                       '%s perdido(s), %s erro(s)', res['conta'], res['boletos'], res['mudaram'], res['baixas'],
                       res['baixas_app'], res['perdidos'], len(res['erros']))
        for e in res['erros']:
            logger.warning('[efi-boletos]   %s', e)
    _guardar_resumo(agora, inicio, saida)
    return saida


def _guardar_resumo(agora, inicio, saida):
    """O que a rodada fez fica no banco (app_config efi_boletos_ultimo) e, se deu erro, na Auditoria —
    o log do Railway não se lê do PC. 16/11/2026: 11 boletos do Conexa pagos de manhã e a aba Recebido
    vazia à noite; a rotina rodava toda hora em 4 segundos e ninguém via o porquê."""
    import json
    resumo = {'em': agora.strftime('%d/%m/%Y %H:%M'), 'segundos': int((datetime.now() - inicio).total_seconds()),
              'versao': (os.getenv('RAILWAY_GIT_COMMIT_SHA') or '')[:7],
              'contas': [{k: v for k, v in r.items()} for r in saida]}
    try:
        _x('INSERT INTO app_config (chave, valor) VALUES (%s, %s) ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()',
           ('efi_boletos_ultimo', json.dumps(resumo, ensure_ascii=False)[:4000]))
    except Exception:
        logger.exception('[efi-boletos] não gravou o resumo da rodada')
    erros = [(r['conta'], e) for r in saida for e in r['erros']]
    if erros:
        from utils.atividade import registrar_agente
        registrar_agente('erro.consulta_boletos_efi', 'financeiro', None, _ATOR[0], _ATOR[1], tabela='fin_cobranca_contas',
                         registro_id=erros[0][0], depois={'erros': [f'conta {c}: {e}' for c, e in erros][:5],
                                                          'segundos': resumo['segundos'], 'versao': resumo['versao']})
