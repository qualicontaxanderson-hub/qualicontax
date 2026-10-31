# -*- coding: utf-8 -*-
"""Numeração da DPS — o único lugar que anda ``nfse_dps_controle.ultimo_numero``.

Uma DPS é identificada por (emitente, série, nDPS). Número repetido é rejeição
certa no ADN; número pulado é buraco na sequência que o fisco vai perguntar.
Por isso:

* **FOR UPDATE.** Dois pedidos ao mesmo tempo (duas abas, tela + contrato)
  travam a linha da série e saem em fila: o segundo só lê depois que o primeiro
  gravou. Sem a trava, os dois leem 41 e os dois tentam 42.

* **Número e rascunho na MESMA transação.** ``proximo_numero_dps`` recebe o
  cursor da transação em vez de abrir a sua: quem reserva o número insere a
  linha de ``nfse_emitidas`` com o mesmo cursor, e se o INSERT falhar o
  rollback devolve o número. Reservar numa transação e inserir em outra
  deixaria buraco a cada erro. ``reservar_dps`` faz os dois passos para quem
  não quer compor a transação à mão.

* **Série não se cria sozinha.** Série nova é decisão (a Qualicontax já tem a
  do Conexa; a daqui é outra), então ``abrir_serie`` é chamada explícita e um
  número pedido para série inexistente levanta ``SerieNaoCadastrada`` em vez de
  nascer do nada por um erro de digitação.

Uso:

    from utils.db_helper import transacao
    with transacao() as cur:
        numero = proximo_numero_dps(cur, empresa_id=2, serie='1')
        cur.execute('INSERT INTO nfse_emitidas (..., numero_dps, ...) VALUES (...)')

    # ou, direto:
    emitida_id, numero = reservar_dps(2, '1', cnpj_emitente='...', criado_por=71)
"""
from utils.db_helper import execute_query, transacao


class SerieNaoCadastrada(Exception):
    """Pediu número de (empresa, série) que não existe em nfse_dps_controle."""


class SerieInativa(Exception):
    """A série existe mas foi desligada (ativo = 0)."""


def proximo_numero_dps(cur, empresa_id, serie):
    """Reserva e devolve o próximo nDPS de (empresa_id, serie).

    ``cur`` é o cursor de um bloco ``transacao()`` já aberto: a trava do
    FOR UPDATE dura até o commit/rollback desse bloco, e é ISSO que garante que
    o número só é consumido se o resto do bloco (o INSERT em nfse_emitidas)
    também gravar.
    """
    serie = str(serie).strip()
    cur.execute(
        "SELECT id, ultimo_numero, ativo FROM nfse_dps_controle "
        " WHERE empresa_id = %s AND serie = %s FOR UPDATE",
        (empresa_id, serie))
    linha = cur.fetchone()
    if not linha:
        raise SerieNaoCadastrada(
            f'Série {serie} da empresa {empresa_id} não está em nfse_dps_controle; '
            f'abra com abrir_serie().')
    if not linha['ativo']:
        raise SerieInativa(f'Série {serie} da empresa {empresa_id} está inativa.')
    numero = int(linha['ultimo_numero']) + 1
    cur.execute(
        "UPDATE nfse_dps_controle SET ultimo_numero = %s, atualizado_em = NOW() "
        " WHERE id = %s AND ultimo_numero = %s",
        (numero, linha['id'], linha['ultimo_numero']))
    if cur.rowcount != 1:
        # Com o FOR UPDATE isso não acontece; a conferência fica como rede.
        raise RuntimeError(
            f'nfse_dps_controle id {linha["id"]}: o número mudou sob a trava.')
    return numero


def reservar_dps(empresa_id, serie, cnpj_emitente, criado_por=None,
                 origem='manual', fin_titulo_id=None, dps_json=None):
    """Número + rascunho em ``nfse_emitidas`` numa transação só.

    Devolve ``(id_da_emitida, numero_dps)``. ``ambiente`` é copiado da série
    na hora da reserva: a DPS nasce sabendo para que ambiente vai.
    """
    serie = str(serie).strip()
    with transacao() as cur:
        numero = proximo_numero_dps(cur, empresa_id, serie)
        cur.execute(
            "SELECT ambiente FROM nfse_dps_controle WHERE empresa_id = %s AND serie = %s",
            (empresa_id, serie))
        ambiente = cur.fetchone()['ambiente']
        cur.execute(
            "INSERT INTO nfse_emitidas (empresa_id, cnpj_emitente, ambiente, serie, "
            " numero_dps, situacao, origem, fin_titulo_id, dps_json, criado_por) "
            "VALUES (%s, %s, %s, %s, %s, 'rascunho', %s, %s, %s, %s)",
            (empresa_id, cnpj_emitente, ambiente, serie, numero, origem,
             fin_titulo_id, dps_json, criado_por))
        return cur.lastrowid, numero


def abrir_serie(empresa_id, serie, ambiente=2, numero_inicial=0, observacao=None):
    """Cadastra (empresa, série). Idempotente: se já existe, não mexe e devolve o id.

    ``numero_inicial`` é o ÚLTIMO número já usado fora daqui (0 = série virgem);
    a primeira DPS sai com ``numero_inicial + 1``. ``ambiente`` 1 = produção,
    2 = produção restrita; série de teste é outra série.
    """
    serie = str(serie).strip()
    if not serie.isdigit() or len(serie) > 5:
        raise ValueError(f'Série inválida: {serie!r} (até 5 dígitos).')
    if ambiente not in (1, 2):
        raise ValueError('ambiente deve ser 1 (produção) ou 2 (produção restrita).')
    existente = execute_query(
        "SELECT id FROM nfse_dps_controle WHERE empresa_id = %s AND serie = %s",
        (empresa_id, serie), fetch=True, fetch_one=True)
    if existente:
        return existente['id']
    return execute_query(
        "INSERT INTO nfse_dps_controle (empresa_id, serie, ambiente, ultimo_numero, observacao) "
        "VALUES (%s, %s, %s, %s, %s)",
        (empresa_id, serie, ambiente, int(numero_inicial), observacao))


def situacao_series(empresa_id=None):
    """Lista as séries (de uma empresa ou de todas) com o último número — para a tela."""
    where, params = '', ()
    if empresa_id is not None:
        where, params = ' WHERE c.empresa_id = %s', (empresa_id,)
    return execute_query(
        "SELECT c.*, cl.nome_razao_social, cl.numero_cliente "
        "  FROM nfse_dps_controle c JOIN clientes cl ON cl.id = c.empresa_id"
        + where + " ORDER BY cl.numero_cliente, c.serie", params, fetch=True) or []
