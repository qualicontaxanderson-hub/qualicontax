import uuid
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

    # 26/11/2026 — trava no banco (Anderson, antes do 1º lote pelo app: "trava para não enviar o mesmo novamente"). O
    # servidor tem 4 processos e a marca em memória (lote_mensal._rodando) não cruza processo: dois cliques ou duas abas
    # podiam emitir o mesmo lote em paralelo. A reserva vale ``minutos``; a rodada bate o relógio a cada empresa.
    @staticmethod
    def reservar(lote_id, usuario_id, minutos=30):
        """Marca o lote como "emitindo" por esta rodada. Devolve o token quando conseguiu; None quando outra rodada
        está em andamento (marca com menos de ``minutos``)."""
        token = uuid.uuid4().hex
        execute_query("""UPDATE fin_lotes_mensais SET rodando_desde = NOW(), rodando_por = %s, rodando_token = %s
                          WHERE id = %s AND (rodando_desde IS NULL OR rodando_desde < NOW() - INTERVAL %s MINUTE)""",
                      (usuario_id, token, lote_id, minutos), fetch=False)
        l = execute_query('SELECT rodando_token FROM fin_lotes_mensais WHERE id = %s', (lote_id,), fetch=True, fetch_one=True) or {}
        return token if l.get('rodando_token') == token else None

    @staticmethod
    def bater(lote_id, token):
        """A rodada avisa que continua viva (depois de cada empresa)."""
        execute_query('UPDATE fin_lotes_mensais SET rodando_desde = NOW() WHERE id = %s AND rodando_token = %s', (lote_id, token), fetch=False)

    @staticmethod
    def liberar(lote_id, token):
        execute_query("""UPDATE fin_lotes_mensais SET rodando_desde = NULL, rodando_por = NULL, rodando_token = NULL
                          WHERE id = %s AND rodando_token = %s""", (lote_id, token), fetch=False)

    @staticmethod
    def rodando(lote_id, minutos=30):
        """Quem está emitindo este lote agora, pelo banco (vale para todos os processos), ou None."""
        return execute_query("""SELECT l.rodando_desde, l.rodando_por, u.nome AS rodando_por_nome
                                  FROM fin_lotes_mensais l LEFT JOIN usuarios u ON u.id = l.rodando_por
                                 WHERE l.id = %s AND l.rodando_desde IS NOT NULL AND l.rodando_desde >= NOW() - INTERVAL %s MINUTE""",
                             (lote_id, minutos), fetch=True, fetch_one=True)

    # 27/11/2026 — Anderson: "fechei os honorários de 11/2026 do Auto Posto Cata Preta; se o usuário lançar uma
    # mensalidade, como 11/26 já foi fechado, nem aparece para ele escolher". O fechamento é por EMPRESA: o item do
    # lote que saiu (status ok) ou o honorário da competência emitido à mão (título de HONORÁRIOS, mesmo critério de
    # utils.lote_mensal._fora_do_lote). O lote EMITIDO (mês fechado para todos) continua em ``emitido``.
    _SQL_FECHADAS = """
        SELECT i.cliente_id, l.competencia FROM fin_lote_itens i JOIN fin_lotes_mensais l ON l.id = i.lote_id
         WHERE l.empresa_id = %s AND l.competencia BETWEEN %s AND %s AND i.status = 'ok' AND COALESCE(i.tipo, 'MENSAL') = 'MENSAL'{cli}
        UNION
        SELECT t.cliente_id, DATE_SUB(t.competencia, INTERVAL DAY(t.competencia) - 1 DAY) AS competencia FROM fin_titulos t
         WHERE t.empresa_id = %s AND t.tipo = 'R' AND DATE_SUB(t.competencia, INTERVAL DAY(t.competencia) - 1 DAY) BETWEEN %s AND %s
           AND t.categoria_id = %s
           AND t.origem IN ('boleto', 'nfse') AND t.status <> 'cancelado'
           AND SUBSTRING_INDEX(t.descricao, '— ', -1) LIKE '%%onor%%'
           AND SUBSTRING_INDEX(t.descricao, '— ', -1) NOT LIKE '13º%%'{clt}"""

    @staticmethod
    def fechadas(empresa_id, de, ate, cliente_id=None):
        """{cliente_id: {competências (dia 1) cujo honorário da empresa já saiu}} entre ``de`` e ``ate``."""
        from models.fin_contrato import CATEGORIA_HONORARIOS
        extra = (cliente_id,) if cliente_id else ()
        sql = FinLoteMensal._SQL_FECHADAS.format(cli=' AND i.cliente_id = %s' if cliente_id else '',
                                                 clt=' AND t.cliente_id = %s' if cliente_id else '')
        rows = execute_query(sql, (empresa_id, de, ate) + extra + (empresa_id, de, ate, CATEGORIA_HONORARIOS) + extra, fetch=True) or []
        out = {}
        for r in rows:
            out.setdefault(r['cliente_id'], set()).add(r['competencia'])
        return out

    @staticmethod
    def competencia_aberta(empresa_id, a_partir=None, cliente_id=None):
        """Primeira competência (dia 1) a partir de hoje cujo lote ainda não saiu — e, com ``cliente_id``, cujo
        honorário dessa empresa ainda não foi fechado (27/11/2026)."""
        c = (a_partir or date.today()).replace(day=1)
        fech = FinLoteMensal.fechadas(empresa_id, c, date(c.year + 4, c.month, 1), cliente_id).get(cliente_id, set()) if cliente_id else set()
        for _ in range(60):
            if not FinLoteMensal.emitido(empresa_id, c) and c not in fech:
                break
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
                       (c.abertura_de_pf_id IS NOT NULL AND c.abertura_concluida_em IS NULL) AS em_abertura,
                       u.nome AS lancado_por_nome, ua.nome AS aprovado_por_nome,
                       t.status AS titulo_status, t.vencimento AS titulo_vencimento, t.valor_baixado,
                       f.status AS fechamento_status,
                       pg.numero_cliente AS pagador_num, pg.nome_razao_social AS pagador_nome,
                       co.numero AS orcamento_numero
                  FROM fin_lancamentos l
                  JOIN clientes c ON c.id = l.cliente_id
                  LEFT JOIN clientes pg ON pg.id = l.pagador_cliente_id
                  LEFT JOIN com_orcamentos co ON co.id = l.orcamento_id
                  JOIN nfse_servicos s ON s.id = l.servico_id
                  JOIN usuarios u ON u.id = l.lancado_por
                  LEFT JOIN usuarios ua ON ua.id = l.aprovado_por
                  LEFT JOIN fin_titulos t ON t.id = l.titulo_id
                  LEFT JOIN fin_comissao_fechamentos f ON f.id = l.fechamento_id"""

    @staticmethod
    def listar(empresa_id, lancado_por=None, status=None, destino=None, competencia=None, cliente_id=None,
               busca=None, comissao_status=None, limite=500, origem=None):
        cond, params = ['l.empresa_id = %s'], [empresa_id]
        if isinstance(origem, (list, tuple, set)):
            cond.append('l.origem IN (%s)' % ','.join(['%s'] * len(origem))); params += list(origem)
        elif origem:
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
              vencimento=None, observacao=None, competencia=None, com_nota=None, comissao_tipo=None, comissao_valor=None,
              pagador_cliente_id=None):
        q, vu, competencia, cv = FinLancamento._validar(empresa_id, destino, quantidade, valor_unitario, vencimento,
                                                          competencia, comissao_tipo, comissao_valor, cliente_id)
        return execute_query(
            """INSERT INTO fin_lancamentos (empresa_id, cliente_id, servico_id, descricao, quantidade, valor_unitario,
                   valor_total, destino, competencia, vencimento, observacao, lancado_por, com_nota,
                   comissao_pedida_tipo, comissao_pedida_valor, pagador_cliente_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (empresa_id, cliente_id, servico_id, (descricao or '')[:200], q, vu, (q * vu).quantize(Decimal('0.01')),
             destino, competencia, vencimento, observacao, lancado_por,
             None if com_nota is None else (1 if com_nota else 0), comissao_tipo, cv,
             (pagador_cliente_id if destino == 'AVULSO' and pagador_cliente_id and int(pagador_cliente_id) != int(cliente_id) else None)), fetch=False)

    @staticmethod
    def _validar(empresa_id, destino, quantidade, valor_unitario, vencimento, competencia, comissao_tipo, comissao_valor, cliente_id=None):
        """As regras de um pedido (criar e editar usam as mesmas). Devolve (quantidade, valor, competência, comissão)."""
        if destino not in DESTINOS:
            raise ValueError('Escolha se a cobrança é avulsa ou entra no mensal.')
        q = _d(quantidade or 1)
        vu = _d(valor_unitario)
        if q <= 0 or vu <= 0:
            raise ValueError('Quantidade e valor precisam ser maiores que zero.')
        if destino == 'AVULSO' and not vencimento:
            raise ValueError('Informe o vencimento do boleto avulso.')
        if destino == 'MENSAL':
            # corte do mês: lote já emitido (ou o honorário desta empresa já fechado) empurra para a competência seguinte
            competencia = FinLoteMensal.competencia_aberta(empresa_id, competencia, cliente_id)
        else:
            competencia = (competencia or date.today()).replace(day=1)
        if comissao_tipo not in (None, 'PADRAO', 'PCT', 'FIXO', 'SEM'):
            raise ValueError('Escolha a comissão: padrão, outro %, valor fixo ou sem comissão.')
        cv = _d(comissao_valor) if comissao_tipo in ('PCT', 'FIXO') else None
        if comissao_tipo in ('PCT', 'FIXO') and (cv is None or cv <= 0):
            raise ValueError('Informe o % ou o valor da comissão.')
        if comissao_tipo == 'PCT' and cv > 100:
            raise ValueError('A comissão não pode passar de 100%.')
        return q, vu, competencia, cv

    @staticmethod
    def editar(lid, cliente_id, servico_id, descricao, valor_unitario, destino, vencimento=None, observacao=None,
               competencia=None, com_nota=None, comissao_tipo=None, comissao_valor=None, pagador_cliente_id=None,
               parcelas=1, parcelas_datas=None, desconto=False):
        """Quem pediu corrige o pedido enquanto ele aguarda aprovação ou depois de reprovado (25/11/2026 — Anderson:
        "quando o funcionário fizer um lançamento ele tem que ter o botão de edição até a aprovação e, mesmo que for
        revertida a aprovação, ele poderá editar novamente"). O pedido reprovado volta para "aguardando aprovação".
        ``desconto=True`` edita a linha de desconto ligada ao pedido (só a rota faz isso)."""
        l = FinLancamento.get(lid)
        if not l or l['status'] not in ('LANCADO', 'REPROVADO'):
            raise ValueError('Este pedido já foi aprovado: não dá mais para alterar.')
        if (l.get('origem') == 'DESCONTO') != bool(desconto) or l.get('parcela'):
            raise ValueError('Altere pelo pedido do serviço: o desconto vai junto.')
        q, vu, competencia, cv = FinLancamento._validar(l['empresa_id'], destino, 1, valor_unitario, vencimento,
                                                          competencia, comissao_tipo, comissao_valor, cliente_id)
        n = max(1, min(int(parcelas or 1), 12))
        execute_query(
            """UPDATE fin_lancamentos SET cliente_id = %s, servico_id = %s, descricao = %s, quantidade = 1, valor_unitario = %s,
                   valor_total = %s, destino = %s, competencia = %s, vencimento = %s, observacao = %s, com_nota = %s,
                   comissao_pedida_tipo = %s, comissao_pedida_valor = %s, pagador_cliente_id = %s, parcelas = %s, parcelas_datas = %s,
                   status = 'LANCADO', motivo = NULL, aprovado_por = NULL, aprovado_em = NULL
             WHERE id = %s AND status IN ('LANCADO', 'REPROVADO')""",
            (cliente_id, servico_id, (descricao or '')[:200], vu, (q * vu).quantize(Decimal('0.01')), destino, competencia,
             vencimento, observacao, None if com_nota is None else (1 if com_nota else 0), comissao_tipo, cv,
             (pagador_cliente_id if destino == 'AVULSO' and pagador_cliente_id and int(pagador_cliente_id) != int(cliente_id) else None),
             n if n > 1 else None, ','.join(d.isoformat() for d in parcelas_datas) if (n > 1 and parcelas_datas) else None, lid), fetch=False)
        return lid

    @staticmethod
    def comissao_calculada(l, tipo=None, valor=None, base=None):
        """(acordo, valor da comissão) — o que vale ao aprovar. Quem aprova pode mandar tipo/valor (ajuste); senão
        vale o que a pessoa pediu; "padrão" é o acordo dela para o serviço. Sem acordo (não comissionado, ex.: Jabes)
        só ganha se quem aprova der. ``base`` (19/11/2026): o valor líquido quando há desconto junto (comissão pelo líquido)."""
        if l.get('origem') == 'DESCONTO':
            return None, Decimal('0')
        if base is None:
            base = _d(l['valor_total']) - FinLancamento.desconto_total(l['id']) if l.get('id') else _d(l['valor_total'])
        l = dict(l, valor_total=max(_d(base), Decimal('0')))
        acordo = FinComissaoAcordo.vigente(l['lancado_por'], l['servico_id'], l['lancado_em'].date() if l.get('lancado_em') else None)
        if FinLancamento.servico_travado(l['servico_id']):
            # 17/11/2026 — Anderson: "é bom ter uma trava das que nunca pagamos comissão". Serviço marcado "Nunca"
            # (honorário mensal, 13º, taxas) não paga comissão de jeito nenhum: nem pedindo, nem aprovando.
            return acordo, Decimal('0')
        tipo = tipo or l.get('comissao_pedida_tipo') or 'PADRAO'
        if tipo == 'PADRAO' and acordo and not acordo.get('servico_id'):
            # 17/11/2026: o catálogo de serviços tem a comissão padrão do serviço (ex.: taxa da Junta = sem comissão);
            # vale para quem tem direito (acordo geral). Acordo da pessoa para AQUELE serviço vence o padrão.
            s = execute_query('SELECT comissao_tipo, comissao_valor FROM nfse_servicos WHERE id = %s', (l['servico_id'],),
                              fetch=True, fetch_one=True) or {}
            if s.get('comissao_tipo') == 'SEM':
                return acordo, Decimal('0')
            if s.get('comissao_tipo') in ('PCT', 'FIXO') and s.get('comissao_valor'):
                return acordo, FinComissaoAcordo.calcular({'tipo': s['comissao_tipo'], 'valor': s['comissao_valor']}, l['valor_total'])
        valor = valor if valor is not None else l.get('comissao_pedida_valor')
        if tipo == 'SEM':
            return acordo, Decimal('0')
        if tipo == 'PCT':
            return acordo, (_d(l['valor_total']) * _d(valor) / 100).quantize(Decimal('0.01'))
        if tipo == 'FIXO':
            return acordo, _d(valor)
        return acordo, FinComissaoAcordo.calcular(acordo, l['valor_total'])

    # ---------------- desconto lançado pela equipe (19/11/2026) ----------------
    @staticmethod
    def servico_desconto_id(empresa_id=2):
        """O serviço "Desconto" do catálogo (sem nota, sem comissão): é o que os lançamentos de desconto apontam.
        Cria se não existir."""
        r = execute_query("SELECT id FROM nfse_servicos WHERE empresa_id = %s AND nome = 'Desconto' LIMIT 1", (empresa_id,), fetch=True, fetch_one=True)
        if r:
            return r['id']
        return execute_query("""INSERT INTO nfse_servicos (empresa_id, nome, descricao, emite_nota, ordem, ativo, comissao_tipo)
                                VALUES (%s, 'Desconto', 'Desconto lançado pela equipe (sobre um serviço ou sobre a mensalidade)', 0, 999, 1, 'SEM')""",
                             (empresa_id,), fetch=False)

    @staticmethod
    def descontos_de(lid, status=('LANCADO', 'APROVADO')):
        """Os descontos ligados a este lançamento (ainda vivos)."""
        return execute_query(f"""{FinLancamento.SELECT} WHERE l.desc_lancamento_id = %s AND l.origem = 'DESCONTO'
                                  AND l.status IN ({','.join(['%s'] * len(status))}) ORDER BY l.id""", (lid,) + tuple(status), fetch=True) or []

    @staticmethod
    def desconto_total(lid):
        return sum((_d(d['valor_total']) for d in FinLancamento.descontos_de(lid)), Decimal('0'))

    @staticmethod
    def servico_travado(servico_id):
        """Serviço que NUNCA paga comissão (comissao_tipo = 'SEM' no catálogo)."""
        s = execute_query('SELECT comissao_tipo FROM nfse_servicos WHERE id = %s', (servico_id,), fetch=True, fetch_one=True) or {}
        return s.get('comissao_tipo') == 'SEM'

    @staticmethod
    def aprovar(lid, aprovado_por, comissao_tipo=None, comissao_valor=None):
        l = FinLancamento.get(lid)
        if not l or l['status'] != 'LANCADO':
            raise ValueError('Só lançamento aguardando aprovação pode ser aprovado.')
        acordo, com = FinLancamento.comissao_calculada(l, comissao_tipo, comissao_valor)
        if com > _d(l['valor_total']):
            raise ValueError('A comissão não pode ser maior que o valor cobrado.')
        execute_query(
            """UPDATE fin_lancamentos SET status = 'APROVADO', aprovado_por = %s, aprovado_em = NOW(),
                   comissao_acordo_id = %s, comissao_valor = %s, comissao_status = %s WHERE id = %s""",
            (aprovado_por, acordo['id'] if acordo else None, com if com > 0 else None,
             'AGUARDANDO' if com > 0 else 'SEM', lid), fetch=False)
        # 19/11/2026: o desconto pedido junto com o serviço é aprovado junto (uma aprovação só)
        execute_query("""UPDATE fin_lancamentos SET status = 'APROVADO', aprovado_por = %s, aprovado_em = NOW(), comissao_status = 'SEM'
                          WHERE desc_lancamento_id = %s AND origem = 'DESCONTO' AND status = 'LANCADO'""", (aprovado_por, lid), fetch=False)
        return com

    @staticmethod
    def _cascata(lid, status, motivo=None):
        """Reprovar/cancelar o serviço reprova/cancela o desconto pedido junto."""
        execute_query("UPDATE fin_lancamentos SET status = %s, motivo = COALESCE(%s, motivo) WHERE desc_lancamento_id = %s AND origem = 'DESCONTO' AND status IN ('LANCADO', 'APROVADO')",
                      (status, motivo, lid), fetch=False)

    @staticmethod
    def reprovar(lid, por, motivo):
        FinLancamento._cascata(lid, 'REPROVADO', (motivo or '')[:255] or None)
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
        FinLancamento._cascata(lid, 'CANCELADO', (motivo or '')[:255] or None)
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
        cond, params = ["l.empresa_id = %s", "l.status = 'APROVADO'", "l.destino = 'MENSAL'", "l.origem IN ('SERVICO', 'DESCONTO')", "l.competencia = %s"], [empresa_id, competencia]
        if cliente_id:
            cond.append('l.cliente_id = %s'); params.append(cliente_id)
        return execute_query(f"{FinLancamento.SELECT} WHERE {' AND '.join(cond)} ORDER BY l.cliente_id, l.id",
                             tuple(params), fetch=True) or []

    @staticmethod
    def adiantar(lid, usuario_id):
        """Pede a comissão de um serviço cuja cobrança está ATRASADA antes de o cliente pagar (18/11/2026 — Anderson:
        "vamos poder pagar as comissões posteriores também, mas não sairá dos atrasados para ele ter ciência").
        Só o próprio, só AGUARDANDO e só com o título em aberto (a vencer ou atrasado — Anderson, 18/11: "nos vencimento
        A vencer tem que ter a opção de antecipar também"). Devolve True se adiantou."""
        onde = """l.id = %s AND l.lancado_por = %s AND l.comissao_status = 'AGUARDANDO' AND l.status = 'FATURADO'
                  AND t.status NOT IN ('liquidado', 'cancelado')"""
        # execute_query devolve True em UPDATE mesmo sem linha (utils/db_helper.py): conferir antes
        if not execute_query('SELECT l.id FROM fin_lancamentos l JOIN fin_titulos t ON t.id = l.titulo_id WHERE ' + onde, (lid, usuario_id), fetch=True):
            return False
        execute_query("UPDATE fin_lancamentos l JOIN fin_titulos t ON t.id = l.titulo_id SET l.comissao_status = 'LIBERADA', l.comissao_adiantada = 1 WHERE " + onde,
                      (lid, usuario_id), fetch=False)
        return True

    @staticmethod
    def descontar_adiantadas_perdidas():
        """Comissão adiantada cuja cobrança foi dada como perdida (título cancelado) vira um DESCONTO: lançamento com a
        comissão negativa, LIBERADO, que entra no próximo pedido da pessoa e abate o total (18/11/2026 — Anderson:
        "no futuro se não recebermos será descontado nas comissões dos meses seguintes"). Uma vez por adiantado."""
        perdidas = execute_query(
            """SELECT l.* FROM fin_lancamentos l JOIN fin_titulos t ON t.id = l.titulo_id
                WHERE l.comissao_adiantada = 1 AND l.comissao_status IN ('FECHADA', 'PAGA') AND t.status = 'cancelado'
                  AND NOT EXISTS (SELECT 1 FROM fin_lancamentos d WHERE d.desconto_de = l.id)""", fetch=True) or []
        ids = []
        for l in perdidas:
            ids.append(execute_query(
                """INSERT INTO fin_lancamentos (empresa_id, cliente_id, servico_id, descricao, quantidade, valor_unitario, valor_total,
                       destino, origem, competencia, vencimento, status, lancado_por, aprovado_por, aprovado_em, titulo_id,
                       comissao_acordo_id, comissao_valor, comissao_status, comissao_adiantada, desconto_de)
                   VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, 'FATURADO', %s, %s, NOW(), %s, %s, %s, 'LIBERADA', 0, %s)""",
                (l['empresa_id'], l['cliente_id'], l['servico_id'], ('Desconto: não recebemos · ' + (l['descricao'] or ''))[:200],
                 l['valor_total'], l['valor_total'], l['destino'], l['origem'], l['competencia'], l['vencimento'], l['lancado_por'],
                 l['aprovado_por'], l['titulo_id'], l['comissao_acordo_id'], -_d(l['comissao_valor']), l['id']), fetch=False))
        return ids

    @staticmethod
    def liberar_comissoes_pagas():
        """AGUARDANDO → LIBERADA quando o título foi liquidado. Idempotente; roda antes de listar comissões."""
        execute_query("""UPDATE fin_lancamentos l JOIN fin_titulos t ON t.id = l.titulo_id
                            SET l.comissao_status = 'LIBERADA'
                          WHERE l.comissao_status = 'AGUARDANDO' AND t.status = 'liquidado'""", fetch=False)
        try:
            FinLancamento.descontar_adiantadas_perdidas()
        except Exception:        # coluna ainda não criada etc.: a tela não pode cair por isso
            pass

    @staticmethod
    def situacao_comissao(l):
        """Rótulo para quem lançou: aguardando pagamento / vencido / pago / fechada / paga / sem comissão."""
        cs = l.get('comissao_status')
        if l.get('desconto_de'):
            return {'LIBERADA': 'Desconto · entra no próximo pedido', 'FECHADA': 'Desconto · em pedido'}.get(cs, 'Desconto aplicado')
        if l.get('comissao_adiantada') and l.get('titulo_status') != 'liquidado':
            return {'LIBERADA': 'Adiantada · a pedir', 'FECHADA': 'Adiantada · em pedido', 'PAGA': 'Adiantada · paga'}.get(cs, COMISSAO.get(cs or 'SEM'))
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


class FinCarteira:
    """Comissão sobre a MENSALIDADE das empresas da carteira de uma pessoa (17/11/2026 — Guilherme Rocha, 25%).

    Anderson: "uma linha por empresa, com a mensalidade, o %, a comissão e se o cliente já pagou; 25% da mensalidade
    está correto". O mês é o do VENCIMENTO da mensalidade; a comissão é o % sobre o que o cliente pagou e só conta
    pago. A empresa entra na carteira "a partir de" um mês (vigencia_inicio): cliente novo pode começar no mês seguinte.
    O título é achado pelo cliente_id OU pelo CNPJ/CPF (muito honorário pago chegou pelo extrato sem cliente_id)."""

    @staticmethod
    def mes(usuario_id, ano, mes):
        ini = date(ano, mes, 1)
        fim = mes_seguinte(ini)
        acordos = execute_query(
            """SELECT a.*, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj
                 FROM fin_comissao_acordos a JOIN clientes c ON c.id = a.cliente_id
                WHERE a.usuario_id = %s AND a.base = 'HONORARIOS' AND a.ativo = 1 AND a.vigencia_inicio < %s
                  AND (a.vigencia_fim IS NULL OR a.vigencia_fim >= %s)
                ORDER BY c.nome_razao_social""", (usuario_id, fim, ini), fetch=True) or []
        linhas = []
        for a in acordos:
            doc = ''.join(ch for ch in (a['cpf_cnpj'] or '') if ch.isdigit())
            tits = execute_query(
                """SELECT t.id, t.valor, t.valor_baixado, t.status, t.vencimento, t.competencia, t.descricao,
                          (SELECT MAX(b.data_baixa) FROM fin_titulo_baixas b WHERE b.titulo_id = t.id) AS pago_em,
                          (SELECT l.id FROM fin_lancamentos l WHERE l.comissao_acordo_id = %s AND l.titulo_id = t.id LIMIT 1) AS lanc_id,
                          (SELECT l.comissao_status FROM fin_lancamentos l WHERE l.comissao_acordo_id = %s AND l.titulo_id = t.id LIMIT 1) AS lanc_st
                     FROM fin_titulos t LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
                    WHERE t.tipo = 'R' AND t.status <> 'cancelado' AND t.vencimento >= %s AND t.vencimento < %s
                      AND (t.cliente_id = %s OR (%s <> '' AND REPLACE(REPLACE(REPLACE(t.contraparte_doc, '.', ''), '/', ''), '-', '') = %s)
                           -- boleto pago que entrou pelo extrato sem cliente: "Recebimento de cobrança: <nº> de <RAZÃO SOCIAL>"
                           OR (t.cliente_id IS NULL AND t.descricao LIKE CONCAT('Recebimento de cobrança: %% de ', %s)))
                      AND (cat.nome LIKE 'Honor%%' OR t.descricao LIKE 'Honor%%')
                      AND t.descricao NOT LIKE 'Honorários e serviços —%%'
                    ORDER BY t.vencimento, t.id""",
                (a['id'], a['id'], ini, fim, a['cliente_id'], doc, doc, a['nome_razao_social'] or '-'), fetch=True) or []
            if a['vigencia_inicio'] and a['vigencia_inicio'] >= ini and a['vigencia_inicio'] < fim:
                novo = True
            else:
                novo = False
            if not tits:
                linhas.append({'acordo': a, 'titulo': None, 'mensal': None, 'pago': Decimal('0'), 'comissao': Decimal('0'),
                               'situacao': 'sem', 'novo': novo})
                continue
            for t in tits:
                pago = _d(t['valor_baixado'])
                com = FinComissaoAcordo.calcular(a, pago) if pago > 0 else Decimal('0')
                if a['tipo'] == 'FIXO':
                    com = _d(a['valor']) if t['status'] == 'liquidado' else Decimal('0')
                sit = ('pedida' if t['lanc_st'] in ('FECHADA', 'PAGA') else
                       'pago' if t['status'] == 'liquidado' else 'parcial' if pago > 0 else
                       'atrasado' if t['vencimento'] < date.today() else 'aberto')
                linhas.append({'acordo': a, 'titulo': t, 'mensal': _d(t['valor']), 'pago': pago, 'comissao': com,
                               'situacao': sit, 'lanc_st': t['lanc_st'], 'novo': novo})
        return linhas

    @staticmethod
    def liberar_um(usuario_id, acordo_id, titulo_id):
        """A comissão de UMA mensalidade paga da carteira (o cartão marcado): confere que o acordo é da pessoa e o
        título é da empresa da carteira, pago; grava o lançamento LIBERADO uma vez só. Devolve o id ou None."""
        a = execute_query("SELECT * FROM fin_comissao_acordos WHERE id = %s AND usuario_id = %s AND base = 'HONORARIOS' AND ativo = 1",
                          (acordo_id, usuario_id), fetch=True, fetch_one=True)
        t = execute_query('SELECT vencimento FROM fin_titulos WHERE id = %s', (titulo_id,), fetch=True, fetch_one=True)
        if not a or not t:
            return None
        for ln in FinCarteira.mes(usuario_id, t['vencimento'].year, t['vencimento'].month):
            if ln['acordo']['id'] == acordo_id and ln['titulo'] and ln['titulo']['id'] == titulo_id:
                return (FinCarteira._gravar(usuario_id, ln) or None)
        return None

    @staticmethod
    def adiantar_um(usuario_id, acordo_id, titulo_id):
        """A comissão de UMA mensalidade em aberto (a vencer ou atrasada) da carteira, pedida antes de o cliente pagar
        (18/11/2026): grava o lançamento LIBERADO e marcado como adiantado, pelo % sobre o valor da mensalidade. O cartão
        continua em "Esperando o cliente pagar". Devolve o id ou None."""
        a = execute_query("SELECT * FROM fin_comissao_acordos WHERE id = %s AND usuario_id = %s AND base = 'HONORARIOS' AND ativo = 1",
                          (acordo_id, usuario_id), fetch=True, fetch_one=True)
        t = execute_query('SELECT vencimento FROM fin_titulos WHERE id = %s', (titulo_id,), fetch=True, fetch_one=True)
        if not a or not t:
            return None
        for ln in FinCarteira.mes(usuario_id, t['vencimento'].year, t['vencimento'].month):
            tt = ln['titulo']
            if ln['acordo']['id'] != acordo_id or not tt or tt['id'] != titulo_id:
                continue
            if tt['status'] in ('liquidado', 'cancelado'):
                return None
            if tt['lanc_id']:
                return tt['lanc_id'] if tt['lanc_st'] == 'LIBERADA' else None
            com = _d(a['valor']) if a['tipo'] == 'FIXO' else FinComissaoAcordo.calcular(a, _d(tt['valor']))
            if com <= 0:
                return None
            return execute_query(
                """INSERT INTO fin_lancamentos (empresa_id, cliente_id, servico_id, descricao, quantidade, valor_unitario, valor_total,
                       destino, origem, competencia, vencimento, status, lancado_por, aprovado_por, aprovado_em, titulo_id,
                       comissao_acordo_id, comissao_valor, comissao_status, comissao_adiantada)
                   VALUES (2, %s, 1, %s, 1, %s, %s, 'MENSAL', 'HONORARIOS', %s, %s, 'FATURADO', %s, %s, NOW(), %s, %s, %s, 'LIBERADA', 1)""",
                (a['cliente_id'], ('Mensalidade · carteira · venc. %s · adiantada' % tt['vencimento'].strftime('%m/%Y'))[:200],
                 tt['valor'], tt['valor'], tt['vencimento'].replace(day=1), tt['vencimento'], usuario_id,
                 a.get('aprovado_por') or usuario_id, tt['id'], a['id'], com), fetch=False)
        return None

    @staticmethod
    def _gravar(usuario_id, ln):
        t, a = ln['titulo'], ln['acordo']
        if not t or t['status'] != 'liquidado' or ln['comissao'] <= 0:
            return None
        if t['lanc_id']:
            return t['lanc_id'] if t['lanc_st'] == 'LIBERADA' else None
        return execute_query(
            """INSERT INTO fin_lancamentos (empresa_id, cliente_id, servico_id, descricao, quantidade, valor_unitario, valor_total,
                   destino, origem, competencia, vencimento, status, lancado_por, aprovado_por, aprovado_em, titulo_id,
                   comissao_acordo_id, comissao_valor, comissao_status)
               VALUES (2, %s, 1, %s, 1, %s, %s, 'MENSAL', 'HONORARIOS', %s, %s, 'FATURADO', %s, %s, NOW(), %s, %s, %s, 'LIBERADA')""",
            (a['cliente_id'], ('Mensalidade · carteira · venc. %s' % t['vencimento'].strftime('%m/%Y'))[:200], ln['pago'], ln['pago'],
             t['vencimento'].replace(day=1), t['vencimento'], usuario_id, a.get('aprovado_por') or usuario_id, t['id'], a['id'],
             ln['comissao']), fetch=False)

    @staticmethod
    def liberar(usuario_id, ano, mes, aprovado_por=None):
        """Grava (uma vez por acordo × título) a comissão das mensalidades JÁ PAGAS do mês como lançamento LIBERADO,
        para entrar no pedido de pagamento (com Pix e quitação pelo extrato). Devolve os ids de lançamento liberados
        (novos ou já existentes ainda não pedidos)."""
        ids = []
        for ln in FinCarteira.mes(usuario_id, ano, mes):
            t, a = ln['titulo'], ln['acordo']
            if not t or t['status'] != 'liquidado' or ln['comissao'] <= 0:
                continue
            if t['lanc_id']:
                if t['lanc_st'] == 'LIBERADA':
                    ids.append(t['lanc_id'])
                continue
            lid = execute_query(
                """INSERT INTO fin_lancamentos (empresa_id, cliente_id, servico_id, descricao, quantidade, valor_unitario, valor_total,
                       destino, origem, competencia, vencimento, status, lancado_por, aprovado_por, aprovado_em, titulo_id,
                       comissao_acordo_id, comissao_valor, comissao_status)
                   VALUES (2, %s, 1, %s, 1, %s, %s, 'MENSAL', 'HONORARIOS', %s, %s, 'FATURADO', %s, %s, NOW(), %s, %s, %s, 'LIBERADA')""",
                (a['cliente_id'], ('Comissão sobre a mensalidade · %s' % t['vencimento'].strftime('%m/%Y'))[:200], ln['pago'], ln['pago'],
                 date(ano, mes, 1), t['vencimento'], usuario_id, aprovado_por or a.get('aprovado_por') or usuario_id, t['id'], a['id'],
                 ln['comissao']), fetch=False)
            ids.append(lid)
        return ids


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
        if total <= 0:
            raise ValueError('O desconto é maior que as comissões marcadas: marque mais comissões para o pedido ficar positivo.')
        fid = execute_query(
            'INSERT INTO fin_comissao_fechamentos (usuario_id, total, qtd_itens, observacao) VALUES (%s, %s, %s, %s)',
            (usuario_id, total, len(rows), observacao), fetch=False)
        execute_query(f"UPDATE fin_lancamentos SET comissao_status = 'FECHADA', fechamento_id = %s WHERE id IN ({marks})",
                      (fid,) + tuple(ids), fetch=False)
        return fid

    @staticmethod
    def cancelar(fid):
        """Desfaz um pedido ainda não pago (18/11/2026 — Anderson: "até pagar tem que ter a opção do próprio usuário
        excluir, só depois de pago que não"). As comissões voltam para "a pedir"; o que foi ADIANTADO e o cliente ainda
        não pagou volta a esperar o cliente (a linha da carteira criada só para o adiantamento é apagada). Devolve o
        número de itens devolvidos."""
        f = FinComissaoFechamento.get(fid)
        if not f or f['status'] in ('PAGO', 'CANCELADO') or _d(f['valor_pago']) > 0:
            raise ValueError('Este pedido já foi pago (ou pago em parte) e não pode mais ser cancelado.')
        itens = execute_query(
            """SELECT l.id, l.origem, l.comissao_adiantada, l.desconto_de, t.status AS titulo_status
                 FROM fin_lancamentos l LEFT JOIN fin_titulos t ON t.id = l.titulo_id WHERE l.fechamento_id = %s""", (fid,), fetch=True) or []
        for l in itens:
            if l['comissao_adiantada'] and l['titulo_status'] != 'liquidado':
                if l['origem'] == 'HONORARIOS':        # linha criada só para adiantar a mensalidade: some, o cartão da carteira volta
                    execute_query('DELETE FROM fin_lancamentos WHERE id = %s AND origem = %s AND comissao_adiantada = 1', (l['id'], 'HONORARIOS'), fetch=False)
                else:                                   # serviço: volta a esperar o cliente pagar
                    execute_query("UPDATE fin_lancamentos SET comissao_status = 'AGUARDANDO', comissao_adiantada = 0, fechamento_id = NULL WHERE id = %s", (l['id'],), fetch=False)
            else:
                execute_query("UPDATE fin_lancamentos SET comissao_status = 'LIBERADA', fechamento_id = NULL WHERE id = %s", (l['id'],), fetch=False)
        execute_query("UPDATE fin_comissao_fechamentos SET status = 'CANCELADO' WHERE id = %s", (fid,), fetch=False)
        return len(itens)

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
    def quitar_pelo_extrato():
        """Pedido de comissão em aberto + saída no extrato do valor exato que falta, depois do pedido, com o nome de
        quem recebe (ou a chave Pix) na descrição → registra o pagamento e quita (17/11/2026 — Anderson: "quando
        pago e o sistema EXTRATO puxar o Pagamento dar a Quitação"). Só quando há UMA saída que bate. Devolve os
        pedidos quitados [(fid, extrato_id)]."""
        import re
        import unicodedata
        abertos = execute_query(
            """SELECT f.id, f.usuario_id, f.total, f.valor_pago, f.criado_em, u.nome,
                      (SELECT b.pix_chave FROM usuario_dados_bancarios b WHERE b.usuario_id = f.usuario_id ORDER BY b.recebe_pagamentos DESC, b.id DESC LIMIT 1) AS pix_chave,
                      (SELECT b.titular_nome FROM usuario_dados_bancarios b WHERE b.usuario_id = f.usuario_id ORDER BY b.recebe_pagamentos DESC, b.id DESC LIMIT 1) AS titular
                 FROM fin_comissao_fechamentos f JOIN usuarios u ON u.id = f.usuario_id
                WHERE f.status IN ('ENVIADO', 'ABERTO', 'PAGO_PARCIAL')""", fetch=True) or []
        # 18/11/2026: 'ENVIADO' é o status com que o pedido nasce (padrão da tabela); sem ele a quitação nunca rodava.
        if not abertos:
            return []

        def norm(s):
            s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().upper()
            return re.sub(r'[^A-Z0-9@.]+', ' ', s)
        feitos = []
        for f in abertos:
            falta = _d(f['total']) - _d(f['valor_pago'])
            if falta <= 0:
                continue
            cands = execute_query(
                """SELECT l.id, l.data, l.descricao FROM extrato_lancamentos l
                    WHERE l.tipo IN ('debito', 'DEBIT') AND l.valor = %s AND l.data >= %s
                      AND NOT EXISTS (SELECT 1 FROM fin_comissao_pagamentos p WHERE p.extrato_id = l.id)""",
                (-falta, f['criado_em'].date()), fetch=True) or []
            nomes = [f['nome']] + ([f['titular']] if f.get('titular') else [])     # a pessoa OU o titular da conta que recebe
            chave = re.sub(r'\D', '', f.get('pix_chave') or '')
            chave_txt = norm(f.get('pix_chave') or '').strip()

            def casa(c):
                d = norm(c['descricao'])
                if chave and len(chave) >= 8 and chave in re.sub(r'\D', '', c['descricao'] or ''):
                    return True
                if chave_txt and len(chave_txt) >= 20 and chave_txt in d:          # chave aleatória / e-mail
                    return True
                for nome in nomes:
                    partes = [p for p in norm(nome).split() if len(p) > 2 and p not in ('DOS', 'DAS', 'DES')]
                    if len(partes) >= 2 and partes[0] in d.split() and sum(1 for p in partes[1:] if p in d.split()) >= 1:
                        return True
                return False
            bate = [c for c in cands if casa(c)]
            if len(bate) != 1:
                continue
            c = bate[0]
            FinComissaoFechamento.registrar_pagamento(f['id'], c['data'], falta, None, extrato_id=c['id'])
            feitos.append((f['id'], c['id']))
        return feitos

    @staticmethod
    def pagamentos(fid):
        return execute_query('SELECT * FROM fin_comissao_pagamentos WHERE fechamento_id = %s ORDER BY data, id', (fid,), fetch=True) or []
