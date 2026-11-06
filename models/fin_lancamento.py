# -*- coding: utf-8 -*-
"""Lançamento de serviço avulso, lote mensal e comissões (parte 6, etapa 2 — 06/11/2026).

Fluxo (Anderson, 05–06/11/2026):
1. Um funcionário LANÇA um serviço numa empresa e escolhe o destino: ``AVULSO`` (cobrança própria) ou
   ``MENSAL`` (entra na cobrança mensal da empresa, com honorários e 13º).
2. O Anderson APROVA (ou reprova). Aprovado AVULSO → boleto + nota saem na hora; aprovado MENSAL →
   espera o lote da competência.
3. Corte: se o lote da competência já foi EMITIDO, o lançamento MENSAL nasce na competência seguinte.
4. Comissão: calculada na aprovação pelo acordo vigente da pessoa (serviço × % ou valor fixo), mas só
   LIBERADA quando o título é pago. A pessoa fecha as liberadas num FECHAMENTO e manda ao Anderson.
"""
from datetime import date, datetime
from decimal import Decimal

from utils.db_helper import execute_query

DESTINOS = {'AVULSO': 'Cobrança avulsa (boleto só deste serviço)', 'MENSAL': 'Entra na cobrança mensal da empresa'}
STATUS = {'LANCADO': 'Aguardando aprovação', 'APROVADO': 'Aprovado', 'REPROVADO': 'Reprovado',
          'FATURADO': 'Cobrado', 'CANCELADO': 'Cancelado'}
COMISSAO = {'AGUARDANDO': 'Aguardando pagamento', 'LIBERADA': 'Pago · a fechar', 'FECHADA': 'Em fechamento',
            'PAGA': 'Comissão paga', 'SEM': 'Sem comissão'}


def _d(v):
    return Decimal(str(v or 0)).quantize(Decimal('0.01'))


def mes_seguinte(d):
    return date(d.year + (1 if d.month == 12 else 0), 1 if d.month == 12 else d.month + 1, 1)


class FinLoteMensal:
    """Um por competência; EMITIDO fecha o mês para os lançamentos MENSAL."""

    @staticmethod
    def get(empresa_id, competencia):
        return execute_query('SELECT * FROM fin_lotes_mensais WHERE empresa_id = %s AND competencia = %s',
                             (empresa_id, competencia), fetch=True, fetch_one=True)

    @staticmethod
    def emitido(empresa_id, competencia):
        l = FinLoteMensal.get(empresa_id, competencia)
        return bool(l and l['status'] == 'EMITIDO')

    @staticmethod
    def abrir(empresa_id, competencia):
        l = FinLoteMensal.get(empresa_id, competencia)
        if l:
            return l['id']
        return execute_query('INSERT INTO fin_lotes_mensais (empresa_id, competencia) VALUES (%s, %s)',
                             (empresa_id, competencia), fetch=False)

    @staticmethod
    def marcar_emitido(lote_id, usuario_id, qtd, total):
        execute_query("""UPDATE fin_lotes_mensais SET status = 'EMITIDO', emitido_por = %s, emitido_em = NOW(),
                         qtd_cobrancas = %s, total = %s WHERE id = %s""", (usuario_id, qtd, _d(total), lote_id), fetch=False)

    @staticmethod
    def competencia_aberta(empresa_id, a_partir=None):
        """Primeira competência (dia 1) a partir de hoje cujo lote ainda não saiu."""
        c = (a_partir or date.today()).replace(day=1)
        while FinLoteMensal.emitido(empresa_id, c):
            c = mes_seguinte(c)
        return c


class FinComissaoAcordo:

    @staticmethod
    def listar(usuario_id=None, so_ativos=True):
        cond, params = [], []
        if usuario_id:
            cond.append('a.usuario_id = %s'); params.append(usuario_id)
        if so_ativos:
            cond.append('a.ativo = 1')
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        return execute_query(
            f"""SELECT a.*, u.nome AS usuario_nome, s.nome AS servico_nome, c.nome_razao_social AS cliente_nome
                  FROM fin_comissao_acordos a
                  JOIN usuarios u ON u.id = a.usuario_id
                  LEFT JOIN nfse_servicos s ON s.id = a.servico_id
                  LEFT JOIN clientes c ON c.id = a.cliente_id
                 {where}
                 ORDER BY u.nome, a.base, s.nome, c.nome_razao_social""", tuple(params), fetch=True) or []

    @staticmethod
    def get(acordo_id):
        return execute_query('SELECT * FROM fin_comissao_acordos WHERE id = %s', (acordo_id,), fetch=True, fetch_one=True)

    @staticmethod
    def vigente(usuario_id, servico_id, quando=None, base='SERVICO', cliente_id=None):
        """O acordo que vale: o do serviço exato antes do genérico (servico_id NULL); HONORARIOS/INDICACAO
        por cliente."""
        quando = quando or date.today()
        if base == 'SERVICO':
            return execute_query(
                """SELECT * FROM fin_comissao_acordos
                    WHERE usuario_id = %s AND base = 'SERVICO' AND ativo = 1
                      AND (servico_id = %s OR servico_id IS NULL)
                      AND vigencia_inicio <= %s AND (vigencia_fim IS NULL OR vigencia_fim >= %s)
                    ORDER BY (servico_id IS NULL), vigencia_inicio DESC LIMIT 1""",
                (usuario_id, servico_id, quando, quando), fetch=True, fetch_one=True)
        return execute_query(
            """SELECT * FROM fin_comissao_acordos
                WHERE usuario_id = %s AND base = %s AND ativo = 1 AND cliente_id = %s
                  AND vigencia_inicio <= %s AND (vigencia_fim IS NULL OR vigencia_fim >= %s)
                ORDER BY vigencia_inicio DESC LIMIT 1""", (usuario_id, base, cliente_id, quando, quando),
            fetch=True, fetch_one=True)

    @staticmethod
    def calcular(acordo, valor):
        if not acordo:
            return Decimal('0')
        if acordo['tipo'] == 'FIXO':
            return _d(acordo['valor'])
        return (_d(valor) * _d(acordo['valor']) / 100).quantize(Decimal('0.01'))

    @staticmethod
    def criar(usuario_id, base, tipo, valor, vigencia_inicio, servico_id=None, cliente_id=None, parcelas=1,
              observacao=None, criado_por=None, aprovado_por=None):
        if tipo not in ('PCT', 'FIXO') or base not in ('SERVICO', 'HONORARIOS', 'INDICACAO'):
            raise ValueError('Tipo ou base do acordo inválidos.')
        if _d(valor) <= 0:
            raise ValueError('Informe o valor ou o percentual da comissão.')
        if base != 'SERVICO' and not cliente_id:
            raise ValueError('Comissão de honorários ou indicação precisa da empresa.')
        return execute_query(
            """INSERT INTO fin_comissao_acordos (usuario_id, base, servico_id, cliente_id, tipo, valor, parcelas,
                   vigencia_inicio, observacao, criado_por, aprovado_por, aprovado_em)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (usuario_id, base, servico_id, cliente_id, tipo, _d(valor), int(parcelas or 1), vigencia_inicio,
             observacao, criado_por, aprovado_por, datetime.now() if aprovado_por else None), fetch=False)

    @staticmethod
    def encerrar(acordo_id, fim=None):
        execute_query('UPDATE fin_comissao_acordos SET ativo = 0, vigencia_fim = %s WHERE id = %s',
                      (fim or date.today(), acordo_id), fetch=False)


class FinLancamento:

    SELECT = """SELECT l.*, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj, s.nome AS servico_nome, s.emite_nota,
                       u.nome AS lancado_por_nome, ua.nome AS aprovado_por_nome,
                       t.status AS titulo_status, t.vencimento AS titulo_vencimento, t.valor_baixado,
                       f.status AS fechamento_status
                  FROM fin_lancamentos l
                  JOIN clientes c ON c.id = l.cliente_id
                  JOIN nfse_servicos s ON s.id = l.servico_id
                  JOIN usuarios u ON u.id = l.lancado_por
                  LEFT JOIN usuarios ua ON ua.id = l.aprovado_por
                  LEFT JOIN fin_titulos t ON t.id = l.titulo_id
                  LEFT JOIN fin_comissao_fechamentos f ON f.id = l.fechamento_id"""

    @staticmethod
    def listar(empresa_id, lancado_por=None, status=None, destino=None, competencia=None, cliente_id=None,
               busca=None, comissao_status=None, limite=500, origem=None):
        cond, params = ['l.empresa_id = %s'], [empresa_id]
        if origem:
            cond.append('l.origem = %s'); params.append(origem)
        if lancado_por:
            cond.append('l.lancado_por = %s'); params.append(lancado_por)
        if status:
            if isinstance(status, (list, tuple, set)):
                cond.append('l.status IN (%s)' % ','.join(['%s'] * len(status))); params += list(status)
            else:
                cond.append('l.status = %s'); params.append(status)
        if destino:
            cond.append('l.destino = %s'); params.append(destino)
        if competencia:
            cond.append('l.competencia = %s'); params.append(competencia)
        if cliente_id:
            cond.append('l.cliente_id = %s'); params.append(cliente_id)
        if comissao_status:
            cond.append('l.comissao_status = %s'); params.append(comissao_status)
        if busca:
            cond.append('(c.nome_razao_social LIKE %s OR c.numero_cliente = %s OR l.descricao LIKE %s)')
            params += ['%%%s%%' % busca, busca, '%%%s%%' % busca]
        return execute_query(f"{FinLancamento.SELECT} WHERE {' AND '.join(cond)} ORDER BY l.id DESC LIMIT %s",
                             tuple(params) + (int(limite),), fetch=True) or []

    @staticmethod
    def get(lid):
        return execute_query(f'{FinLancamento.SELECT} WHERE l.id = %s', (lid,), fetch=True, fetch_one=True)

    @staticmethod
    def criar(empresa_id, cliente_id, servico_id, descricao, quantidade, valor_unitario, destino, lancado_por,
              vencimento=None, observacao=None, competencia=None):
        if destino not in DESTINOS:
            raise ValueError('Escolha se a cobrança é avulsa ou entra no mensal.')
        q = _d(quantidade or 1)
        vu = _d(valor_unitario)
        if q <= 0 or vu <= 0:
            raise ValueError('Quantidade e valor precisam ser maiores que zero.')
        if destino == 'AVULSO' and not vencimento:
            raise ValueError('Informe o vencimento do boleto avulso.')
        if destino == 'MENSAL':
            # corte do mês: lote já emitido empurra para a competência seguinte
            competencia = FinLoteMensal.competencia_aberta(empresa_id, competencia)
        else:
            competencia = (competencia or date.today()).replace(day=1)
        return execute_query(
            """INSERT INTO fin_lancamentos (empresa_id, cliente_id, servico_id, descricao, quantidade, valor_unitario,
                   valor_total, destino, competencia, vencimento, observacao, lancado_por)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (empresa_id, cliente_id, servico_id, (descricao or '')[:200], q, vu, (q * vu).quantize(Decimal('0.01')),
             destino, competencia, vencimento, observacao, lancado_por), fetch=False)

    @staticmethod
    def aprovar(lid, aprovado_por):
        l = FinLancamento.get(lid)
        if not l or l['status'] != 'LANCADO':
            raise ValueError('Só lançamento aguardando aprovação pode ser aprovado.')
        acordo = FinComissaoAcordo.vigente(l['lancado_por'], l['servico_id'], l['lancado_em'].date() if l.get('lancado_em') else None)
        com = FinComissaoAcordo.calcular(acordo, l['valor_total'])
        execute_query(
            """UPDATE fin_lancamentos SET status = 'APROVADO', aprovado_por = %s, aprovado_em = NOW(),
                   comissao_acordo_id = %s, comissao_valor = %s, comissao_status = %s WHERE id = %s""",
            (aprovado_por, acordo['id'] if acordo else None, com if acordo else None,
             'AGUARDANDO' if acordo and com > 0 else 'SEM', lid), fetch=False)
        return com

    @staticmethod
    def reprovar(lid, por, motivo):
        l = FinLancamento.get(lid)
        if not l or l['status'] != 'LANCADO':
            raise ValueError('Só lançamento aguardando aprovação pode ser reprovado.')
        execute_query("UPDATE fin_lancamentos SET status = 'REPROVADO', aprovado_por = %s, aprovado_em = NOW(), motivo = %s WHERE id = %s",
                      (por, (motivo or '')[:255] or None, lid), fetch=False)

    @staticmethod
    def cancelar(lid, motivo):
        l = FinLancamento.get(lid)
        if not l or l['status'] not in ('LANCADO', 'APROVADO'):
            raise ValueError('Lançamento já cobrado não se cancela aqui: cancele a cobrança.')
        execute_query("UPDATE fin_lancamentos SET status = 'CANCELADO', motivo = %s WHERE id = %s",
                      ((motivo or '')[:255] or None, lid), fetch=False)

    @staticmethod
    def marcar_faturado(lid, titulo_id=None, nfse_id=None, boleto_id=None, lote_id=None):
        execute_query("""UPDATE fin_lancamentos SET status = 'FATURADO', titulo_id = COALESCE(%s, titulo_id),
                         nfse_id = COALESCE(%s, nfse_id), boleto_id = COALESCE(%s, boleto_id), lote_id = COALESCE(%s, lote_id)
                         WHERE id = %s""", (titulo_id, nfse_id, boleto_id, lote_id, lid), fetch=False)

    @staticmethod
    def comissoes_do_lote(empresa_id, cliente_id, competencia, titulo_id, lote_id, valor_honorarios, nfse_id=None, boleto_id=None):
        """Grava as comissões de HONORARIOS e INDICACAO da empresa nesta competência, uma linha por acordo
        vigente (idempotente por acordo × competência). A comissão fica AGUARDANDO até o título ser pago."""
        criados = []
        acordos = execute_query(
            """SELECT * FROM fin_comissao_acordos WHERE cliente_id = %s AND ativo = 1 AND base IN ('HONORARIOS', 'INDICACAO')
                  AND vigencia_inicio <= %s AND (vigencia_fim IS NULL OR vigencia_fim >= %s)""",
            (cliente_id, competencia, competencia), fetch=True) or []
        for a in acordos:
            ja = execute_query('SELECT id FROM fin_lancamentos WHERE comissao_acordo_id = %s AND competencia = %s',
                               (a['id'], competencia), fetch=True)
            if ja:
                continue
            if a['base'] == 'INDICACAO':
                n = execute_query('SELECT COUNT(*) n FROM fin_lancamentos WHERE comissao_acordo_id = %s', (a['id'],), fetch=True, fetch_one=True)['n']
                if n >= int(a.get('parcelas') or 1):
                    continue
            com = FinComissaoAcordo.calcular(a, valor_honorarios)
            if a['base'] == 'INDICACAO' and a['tipo'] == 'FIXO' and int(a.get('parcelas') or 1) > 1:
                com = (_d(a['valor']) / int(a['parcelas'])).quantize(Decimal('0.01'))
            if com <= 0:
                continue
            lid = execute_query(
                """INSERT INTO fin_lancamentos (empresa_id, cliente_id, servico_id, descricao, quantidade, valor_unitario, valor_total,
                       destino, origem, competencia, status, lancado_por, aprovado_por, aprovado_em, titulo_id, nfse_id, boleto_id, lote_id,
                       comissao_acordo_id, comissao_valor, comissao_status)
                   VALUES (%s, %s, 1, %s, 1, %s, %s, 'MENSAL', %s, %s, 'FATURADO', %s, %s, NOW(), %s, %s, %s, %s, %s, %s, 'AGUARDANDO')""",
                (empresa_id, cliente_id, ('Comissão sobre honorários' if a['base'] == 'HONORARIOS' else 'Indicação de cliente') + ' · ' + competencia.strftime('%m/%Y'),
                 _d(valor_honorarios), _d(valor_honorarios), a['base'], competencia, a['usuario_id'], a.get('aprovado_por') or a['usuario_id'],
                 titulo_id, nfse_id, boleto_id, lote_id, a['id'], com), fetch=False)
            criados.append(lid)
        return criados

    @staticmethod
    def para_o_mensal(empresa_id, competencia, cliente_id=None):
        """Aprovados, destino MENSAL, da competência, ainda não cobrados — o que o lote junta."""
        cond, params = ["l.empresa_id = %s", "l.status = 'APROVADO'", "l.destino = 'MENSAL'", "l.origem = 'SERVICO'", "l.competencia = %s"], [empresa_id, competencia]
        if cliente_id:
            cond.append('l.cliente_id = %s'); params.append(cliente_id)
        return execute_query(f"{FinLancamento.SELECT} WHERE {' AND '.join(cond)} ORDER BY l.cliente_id, l.id",
                             tuple(params), fetch=True) or []

    @staticmethod
    def liberar_comissoes_pagas():
        """AGUARDANDO → LIBERADA quando o título foi liquidado. Idempotente; roda antes de listar comissões."""
        execute_query("""UPDATE fin_lancamentos l JOIN fin_titulos t ON t.id = l.titulo_id
                            SET l.comissao_status = 'LIBERADA'
                          WHERE l.comissao_status = 'AGUARDANDO' AND t.status = 'liquidado'""", fetch=False)

    @staticmethod
    def situacao_comissao(l):
        """Rótulo para quem lançou: aguardando pagamento / vencido / pago / fechada / paga / sem comissão."""
        cs = l.get('comissao_status')
        if cs in ('FECHADA', 'PAGA', 'SEM') or not cs:
            return COMISSAO.get(cs or 'SEM')
        if l.get('titulo_status') == 'liquidado' or cs == 'LIBERADA':
            return COMISSAO['LIBERADA']
        if l.get('titulo_vencimento') and l['titulo_vencimento'] < date.today():
            return 'Vencido'
        return COMISSAO['AGUARDANDO']

    @staticmethod
    def resumo_pessoa(empresa_id, usuario_id):
        FinLancamento.liberar_comissoes_pagas()
        return execute_query(
            """SELECT SUM(status = 'LANCADO') aguardando_aprovacao,
                      SUM(comissao_status = 'AGUARDANDO') aguardando_pagamento,
                      COALESCE(SUM(CASE WHEN comissao_status = 'AGUARDANDO' THEN comissao_valor END), 0) v_aguardando,
                      SUM(comissao_status = 'LIBERADA') liberadas,
                      COALESCE(SUM(CASE WHEN comissao_status = 'LIBERADA' THEN comissao_valor END), 0) v_liberadas,
                      COALESCE(SUM(CASE WHEN comissao_status = 'FECHADA' THEN comissao_valor END), 0) v_fechadas,
                      COALESCE(SUM(CASE WHEN comissao_status = 'PAGA' THEN comissao_valor END), 0) v_pagas
                 FROM fin_lancamentos WHERE empresa_id = %s AND lancado_por = %s""",
            (empresa_id, usuario_id), fetch=True, fetch_one=True) or {}


class FinComissaoFechamento:

    @staticmethod
    def listar(usuario_id=None, status=None):
        cond, params = [], []
        if usuario_id:
            cond.append('f.usuario_id = %s'); params.append(usuario_id)
        if status:
            cond.append('f.status = %s'); params.append(status)
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        return execute_query(
            f"""SELECT f.*, u.nome AS usuario_nome FROM fin_comissao_fechamentos f JOIN usuarios u ON u.id = f.usuario_id
                 {where} ORDER BY f.id DESC""", tuple(params), fetch=True) or []

    @staticmethod
    def get(fid):
        return execute_query('SELECT f.*, u.nome AS usuario_nome FROM fin_comissao_fechamentos f JOIN usuarios u ON u.id = f.usuario_id WHERE f.id = %s',
                             (fid,), fetch=True, fetch_one=True)

    @staticmethod
    def itens(fid):
        return execute_query(f'{FinLancamento.SELECT} WHERE l.fechamento_id = %s ORDER BY l.id', (fid,), fetch=True) or []

    @staticmethod
    def criar(usuario_id, lancamento_ids, observacao=None):
        """Fecha só as comissões LIBERADAS (título pago) da própria pessoa. Devolve o id."""
        FinLancamento.liberar_comissoes_pagas()
        ids = [int(i) for i in lancamento_ids if str(i).isdigit()]
        if not ids:
            raise ValueError('Escolha ao menos uma comissão liberada.')
        marks = ','.join(['%s'] * len(ids))
        rows = execute_query(
            f"""SELECT id, comissao_valor FROM fin_lancamentos
                 WHERE id IN ({marks}) AND lancado_por = %s AND comissao_status = 'LIBERADA' AND fechamento_id IS NULL""",
            tuple(ids) + (usuario_id,), fetch=True) or []
        if len(rows) != len(ids):
            raise ValueError('Alguma comissão escolhida não está liberada, já foi fechada ou não é sua.')
        total = sum(_d(r['comissao_valor']) for r in rows)
        fid = execute_query(
            'INSERT INTO fin_comissao_fechamentos (usuario_id, total, qtd_itens, observacao) VALUES (%s, %s, %s, %s)',
            (usuario_id, total, len(rows), observacao), fetch=False)
        execute_query(f"UPDATE fin_lancamentos SET comissao_status = 'FECHADA', fechamento_id = %s WHERE id IN ({marks})",
                      (fid,) + tuple(ids), fetch=False)
        return fid

    @staticmethod
    def registrar_pagamento(fid, data, valor, registrado_por, titulo_id=None, extrato_id=None):
        f = FinComissaoFechamento.get(fid)
        if not f or f['status'] in ('PAGO', 'CANCELADO'):
            raise ValueError('Fechamento não está aberto para pagamento.')
        v = _d(valor)
        if v <= 0 or _d(f['valor_pago']) + v > _d(f['total']) + Decimal('0.01'):
            raise ValueError('Valor do pagamento inválido ou maior que o que falta.')
        execute_query('INSERT INTO fin_comissao_pagamentos (fechamento_id, data, valor, titulo_id, extrato_id, registrado_por) VALUES (%s, %s, %s, %s, %s, %s)',
                      (fid, data, v, titulo_id, extrato_id, registrado_por), fetch=False)
        pago = _d(f['valor_pago']) + v
        status = 'PAGO' if pago >= _d(f['total']) else 'PAGO_PARCIAL'
        execute_query('UPDATE fin_comissao_fechamentos SET valor_pago = %s, status = %s WHERE id = %s', (pago, status, fid), fetch=False)
        if status == 'PAGO':
            execute_query("UPDATE fin_lancamentos SET comissao_status = 'PAGA' WHERE fechamento_id = %s", (fid,), fetch=False)
        return status

    @staticmethod
    def pagamentos(fid):
        return execute_query('SELECT * FROM fin_comissao_pagamentos WHERE fechamento_id = %s ORDER BY data, id', (fid,), fetch=True) or []
