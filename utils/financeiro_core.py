# -*- coding: utf-8 -*-
"""Núcleo do financeiro do escritório (Documento E, 19/08/2026).

REGRA DE OURO do documento: o pagamento chega por DOIS caminhos ao mesmo
tempo (webhook do banco e extrato) e é o MESMO dinheiro — se os dois
escreverem, o título baixa duas vezes e o caixa fica errado. Por isso:

* ``registrar_baixa()`` é a ÚNICA função autorizada a gravar em
  fin_titulo_baixas. Idempotente por (titulo_id, data, valor, referencia):
  a segunda chamada com a mesma chave não cria nada, só complementa o que
  faltava (confirmação pelo extrato).
* ``recalcular_status()`` é o ÚNICO escritor de fin_titulos.status e
  valor_baixado — status é DERIVADO da soma das baixas, nunca escrito à mão.

Precedência da CONFIRMAÇÃO (não da criação): extrato é a verdade final (o
dinheiro entrou na conta); webhook é antecipação; manual é exceção e exige
usuário. Baixa de webhook nunca confirmada pelo extrato vira alerta — é onde
mora estorno, chargeback e erro de conciliação (``baixas_sem_confirmacao``).
"""
import logging
from decimal import Decimal

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

ORIGENS = ('webhook', 'extrato', 'manual')
STATUS_TITULO = ('aberto', 'parcial', 'liquidado', 'cancelado')


class BaixaInvalida(ValueError):
    """Baixa recusada por regra de negócio (mensagem própria para tela)."""


def _dec(v) -> Decimal:
    return Decimal(str(v if v not in (None, '') else 0))


def registrar_baixa(titulo_id, valor, data_baixa, origem, referencia=None,
                    juros=0, multa=0, desconto=0, lancamento_id=None,
                    usuario_id=None):
    """ÚNICA função autorizada a gravar em fin_titulo_baixas.

    origem: 'webhook' | 'extrato' | 'manual'

    Idempotente por (titulo_id, data_baixa, valor, referencia): webhook e
    extrato do MESMO pagamento produzem a MESMA chave — a segunda chamada não
    cria nada, só complementa (extrato confirma a baixa do webhook e anexa o
    lancamento_id). Sem ``referencia`` não há como reconhecer o repeteco:
    obrigatória para webhook/extrato, dispensada só na manual.

    Juros/multa não impedem a liquidação: recebido MAIOR que o título não é
    erro — ``valor`` é o que abate o título, a diferença vai em juros/multa.
    Desconto CONTA para liquidar (pagou 90 com 10 de desconto = título de 100
    liquidado).

    Devolve dict: {baixa_id, criada, confirmada_agora, status, valor_baixado}.
    """
    if origem not in ORIGENS:
        raise BaixaInvalida(f"origem '{origem}' inválida (webhook|extrato|manual)")
    if origem == 'manual' and not usuario_id:
        raise BaixaInvalida('baixa manual exige o usuário responsável')
    ref = (str(referencia).strip() if referencia not in (None, '') else '') or None
    if origem in ('webhook', 'extrato') and not ref:
        raise BaixaInvalida(f'baixa por {origem} exige referência '
                            '(nosso_numero / hash do lançamento)')
    valor = _dec(valor)
    if valor <= 0:
        raise BaixaInvalida('valor da baixa deve ser positivo')

    t = execute_query(
        'SELECT id, status, valor FROM fin_titulos WHERE id = %s',
        (titulo_id,), fetch=True, fetch_one=True)
    if not t:
        raise BaixaInvalida(f'título {titulo_id} não existe')
    if t['status'] == 'cancelado':
        raise BaixaInvalida('título cancelado não recebe baixa')

    # A chave de idempotência, com NULL tratado à mão (UNIQUE do MySQL deixa
    # NULLs repetirem; aqui até baixa manual sem referência é conferida).
    existente = execute_query(
        'SELECT id, origem, confirmado_extrato, lancamento_id '
        '  FROM fin_titulo_baixas '
        ' WHERE titulo_id = %s AND data_baixa = %s AND valor = %s '
        '   AND ((referencia IS NULL AND %s IS NULL) OR referencia = %s)',
        (titulo_id, data_baixa, valor, ref, ref), fetch=True, fetch_one=True)

    confirmada_agora = False
    if existente:
        baixa_id, criada = existente['id'], False
        if origem == 'extrato' and not existente['confirmado_extrato']:
            # O extrato CONFIRMA a baixa que o webhook antecipou.
            execute_query(
                'UPDATE fin_titulo_baixas SET confirmado_extrato = 1, '
                '       lancamento_id = COALESCE(%s, lancamento_id) '
                ' WHERE id = %s', (lancamento_id, baixa_id))
            confirmada_agora = True
            logger.info('[fin] baixa %s do título %s confirmada pelo extrato',
                        baixa_id, titulo_id)
        else:
            logger.info('[fin] baixa repetida ignorada (título %s, origem %s)',
                        titulo_id, origem)
    else:
        baixa_id = execute_query(
            'INSERT INTO fin_titulo_baixas '
            '(titulo_id, data_baixa, valor, juros, multa, desconto, origem, '
            ' referencia, lancamento_id, confirmado_extrato, usuario_id) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (titulo_id, data_baixa, valor, _dec(juros), _dec(multa),
             _dec(desconto), origem, ref, lancamento_id,
             1 if origem == 'extrato' else 0, usuario_id))
        criada = True
        confirmada_agora = origem == 'extrato'
        logger.info('[fin] baixa %s criada no título %s (origem %s, valor %s)',
                    baixa_id, titulo_id, origem, valor)

    status, valor_baixado = recalcular_status(titulo_id)
    return {'baixa_id': baixa_id, 'criada': criada,
            'confirmada_agora': confirmada_agora,
            'status': status, 'valor_baixado': valor_baixado}


def recalcular_status(titulo_id):
    """ÚNICO escritor de fin_titulos.status e valor_baixado.

    status deriva da soma das baixas: aberto | parcial | liquidado.
    Desconto conta para LIQUIDAR mas não entra em valor_baixado (que é o
    dinheiro que efetivamente abateu). Cancelado nunca é recalculado.
    Devolve (status, valor_baixado).
    """
    t = execute_query('SELECT status, valor FROM fin_titulos WHERE id = %s',
                      (titulo_id,), fetch=True, fetch_one=True)
    if not t:
        raise BaixaInvalida(f'título {titulo_id} não existe')
    if t['status'] == 'cancelado':
        return 'cancelado', None

    soma = execute_query(
        'SELECT COALESCE(SUM(valor), 0) AS v, COALESCE(SUM(desconto), 0) AS d '
        '  FROM fin_titulo_baixas WHERE titulo_id = %s',
        (titulo_id,), fetch=True, fetch_one=True)
    baixado, descontos = _dec(soma['v']), _dec(soma['d'])

    if baixado + descontos <= 0:
        status = 'aberto'
    elif baixado + descontos >= _dec(t['valor']):
        status = 'liquidado'
    else:
        status = 'parcial'

    execute_query('UPDATE fin_titulos SET status = %s, valor_baixado = %s '
                  'WHERE id = %s', (status, baixado, titulo_id))
    return status, baixado


def baixas_sem_confirmacao(dias=3):
    """Baixas de WEBHOOK que o extrato nunca confirmou após N dias — alerta.

    É onde mora estorno, chargeback e erro de conciliação: o banco avisou que
    pagou, mas o dinheiro não apareceu na conta.
    """
    return execute_query(
        """SELECT b.id AS baixa_id, b.titulo_id, b.data_baixa, b.valor,
                  b.referencia, t.tipo, t.contraparte_nome, t.descricao
             FROM fin_titulo_baixas b
             JOIN fin_titulos t ON t.id = b.titulo_id
            WHERE b.origem = 'webhook'
              AND b.confirmado_extrato = 0
              AND b.data_baixa <= CURDATE() - INTERVAL %s DAY
            ORDER BY b.data_baixa""",
        (int(dias),), fetch=True) or []
