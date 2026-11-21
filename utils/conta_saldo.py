# -*- coding: utf-8 -*-
"""Saldo de cada conta bancária (21/11/2026).

Anderson: "preciso acompanhar os saldos das contas... como acompanhamos isso?". Até aqui o OFX trazia o saldo do
banco (LEDGERBAL) e o PDF do Bradesco tinha a coluna Saldo, mas o leitor jogava fora: só existia o saldo DIGITADO
por empresa no Fluxo de caixa (fin_saldos). Agora cada extrato que entra grava o saldo da conta naquela data em
fin_conta_saldos (um por conta e dia; o mais novo do dia vale).

Conferência: saldo anterior + lançamentos do sistema entre as duas datas = saldo novo. Não batendo, falta extrato
ou lançamento naquela conta (ou sobrou um duplicado) — a janela da conta avisa.

Sem a tabela (migração não aplicada) tudo aqui responde vazio e não quebra a leitura do extrato.
"""
import logging
from datetime import date, datetime
from decimal import Decimal

logger = logging.getLogger(__name__)


def _dt(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def gravar(conta_id, data, valor, origem, arquivo=None):
    """Guarda o saldo do banco daquela conta naquele dia. Nunca levanta: o extrato vale mesmo se isto falhar."""
    from utils.db_helper import execute_query
    d = _dt(data)
    if not conta_id or d is None or valor is None:
        return False
    try:
        execute_query(
            """INSERT INTO fin_conta_saldos (conta_id, data, valor, origem, arquivo) VALUES (%s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE valor = VALUES(valor), origem = VALUES(origem), arquivo = VALUES(arquivo),
                                       atualizado_em = NOW()""",
            (conta_id, d, Decimal(str(valor)), origem, (arquivo or '')[:255] or None), fetch=False)
        return True
    except Exception:
        logger.exception('[saldo] não consegui gravar o saldo da conta %s', conta_id)
        return False


def gravar_do_extrato(banco_id, conta, saldo, origem, arquivo=None):
    """saldo = {'valor', 'data'} como os leitores devolvem. Acha a conta cadastrada pelo banco + número."""
    if not saldo or saldo.get('valor') is None or not conta:
        return False
    from utils.extrato_ingest import achar_conta
    reg = achar_conta(banco_id, conta)
    if not reg:
        return False
    d = _dt(saldo.get('data'))            # o Bradesco às vezes manda 0000-00-00: sem data, não grava
    if d and d > date.today():
        d = date.today()                  # o Sicredi data o saldo no fim do período pedido (30/11): vale o de hoje
    return gravar(reg['id'], d, saldo['valor'], origem, arquivo)


def ultimos(conta_ids):
    """{conta_id: {'data', 'valor', 'origem', 'anterior': {...} ou None}} — o saldo mais novo e o anterior."""
    from utils.db_helper import execute_query
    ids = [int(i) for i in conta_ids if i]
    if not ids:
        return {}
    marks = ','.join(['%s'] * len(ids))
    try:
        rows = execute_query(
            f"""SELECT conta_id, data, valor, origem FROM (
                    SELECT conta_id, data, valor, origem,
                           ROW_NUMBER() OVER (PARTITION BY conta_id ORDER BY data DESC) AS rn
                      FROM fin_conta_saldos WHERE conta_id IN ({marks})) x
                 WHERE rn <= 2 ORDER BY conta_id, data DESC""", tuple(ids), fetch=True) or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        item = {'data': r['data'], 'valor': Decimal(str(r['valor'])), 'origem': r['origem']}
        if r['conta_id'] not in out:
            out[r['conta_id']] = dict(item, anterior=None)
        elif out[r['conta_id']]['anterior'] is None:
            out[r['conta_id']]['anterior'] = item
    return out


def conferir(conta, ult):
    """Saldo anterior + lançamentos (anterior, novo] = saldo novo? conta = linha de fin_contas.
    Devolve {'ok': bool, 'diferenca': Decimal, 'de', 'ate', 'lancado'} ou None sem dois saldos."""
    if not ult or not ult.get('anterior'):
        return None
    from utils.db_helper import execute_query
    from utils.extrato_ingest import conta_normalizada
    a, n = ult['anterior'], ult
    rows = execute_query(
        """SELECT conta, SUM(valor) AS v FROM extrato_lancamentos
            WHERE empresa_id = %s AND data > %s AND data <= %s GROUP BY conta""",
        (conta['empresa_id'], a['data'], n['data']), fetch=True) or []
    alvo = conta_normalizada(conta['conta'])
    lancado = sum((Decimal(str(r['v'] or 0)) for r in rows if conta_normalizada(r['conta']) == alvo), Decimal('0'))
    dif = (n['valor'] - a['valor']) - lancado
    return {'ok': abs(dif) <= Decimal('0.01'), 'diferenca': dif, 'de': a['data'], 'ate': n['data'], 'lancado': lancado}
