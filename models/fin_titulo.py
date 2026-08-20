# -*- coding: utf-8 -*-
"""Títulos do financeiro do ESCRITÓRIO (Documento E, fase 2).

Leitura e criação apenas. Baixa NÃO passa por aqui — o único escritor de
fin_titulo_baixas é utils.financeiro_core.registrar_baixa(), e status/
valor_baixado só quem escreve é recalcular_status(). Cancelar é a exceção
combinada no documento: ato humano, não derivado — e só em título sem baixa.
"""
from utils.db_helper import execute_query


class FinCentroCusto:
    """Centros de custo por estado (E2.2): GO, SP e GERAL.

    ``rateia=1`` (GERAL) divide em PARTES IGUAIS entre os centros normais
    ativos NA LEITURA do DRE — o título guarda o centro cru. Regra do
    Anderson: não há receita na GERAL, o rateio fica meio a meio.
    """

    @staticmethod
    def listar(apenas_ativos=True):
        cond = 'WHERE ativo = 1' if apenas_ativos else ''
        return execute_query(
            f'SELECT id, nome, rateia, ordem, ativo FROM fin_centros_custo '
            f'{cond} ORDER BY ordem, nome', fetch=True) or []

    @staticmethod
    def criar(nome, rateia=False):
        r = execute_query('SELECT COALESCE(MAX(ordem), 0) + 10 AS o '
                          'FROM fin_centros_custo', fetch=True, fetch_one=True)
        return execute_query(
            'INSERT INTO fin_centros_custo (nome, rateia, ordem) '
            'VALUES (%s, %s, %s)',
            (nome, 1 if rateia else 0, (r or {}).get('o') or 10))

    @staticmethod
    def renomear(cc_id, nome):
        execute_query('UPDATE fin_centros_custo SET nome = %s WHERE id = %s',
                      (nome, cc_id))
        r = execute_query('SELECT nome FROM fin_centros_custo WHERE id = %s',
                          (cc_id,), fetch=True, fetch_one=True)
        return bool(r and r['nome'] == nome)

    @staticmethod
    def set_ativo(cc_id, ativo):
        execute_query('UPDATE fin_centros_custo SET ativo = %s WHERE id = %s',
                      (1 if ativo else 0, cc_id))

    @staticmethod
    def usos():
        rows = execute_query(
            'SELECT centro_custo_id, COUNT(*) AS n FROM fin_titulos '
            'WHERE centro_custo_id IS NOT NULL GROUP BY centro_custo_id',
            fetch=True) or []
        return {r['centro_custo_id']: r['n'] for r in rows}


class FinCategoria:
    """Plano gerencial (grupo = linha do DRE)."""

    @staticmethod
    def listar(tipo=None, apenas_ativas=True):
        sql = ['SELECT id, pai_id, tipo, grupo, nome, ordem, ativo FROM fin_categorias']
        cond, params = [], []
        if tipo in ('R', 'P'):
            cond.append('tipo = %s')
            params.append(tipo)
        if apenas_ativas:
            cond.append('ativo = 1')
        if cond:
            sql.append('WHERE ' + ' AND '.join(cond))
        # Subcategoria herda a ordem do pai; o desempate cola a sub no pai.
        sql.append('ORDER BY ordem, COALESCE(pai_id, id), (pai_id IS NOT NULL), nome')
        return execute_query(' '.join(sql), tuple(params), fetch=True) or []

    @staticmethod
    def pais(tipo=None):
        """Só categorias de PRIMEIRO nível (candidatas a pai de sub)."""
        return [c for c in FinCategoria.listar(tipo=tipo) if not c['pai_id']]

    @staticmethod
    def grupos(tipo=None):
        """Grupos existentes (linhas do DRE), na ordem do DRE."""
        cond, params = '', ()
        if tipo in ('R', 'P'):
            cond, params = 'WHERE tipo = %s', (tipo,)
        return execute_query(
            f'SELECT tipo, grupo, MIN(ordem) AS ordem FROM fin_categorias '
            f'{cond} GROUP BY tipo, grupo ORDER BY ordem', params,
            fetch=True) or []

    @staticmethod
    def criar(tipo, grupo, nome, ordem=None, pai_id=None):
        """Nova categoria (ou SUBcategoria, se pai_id). Devolve o id, ou None
        se (tipo, grupo, nome) já existir (uk_cat).

        Sub herda tipo, grupo e ordem do pai — um nível só (sub de sub não).
        """
        if pai_id:
            pai = execute_query(
                'SELECT tipo, grupo, ordem, pai_id FROM fin_categorias '
                'WHERE id = %s', (pai_id,), fetch=True, fetch_one=True)
            if not pai or pai['pai_id']:
                return None                  # pai inexistente ou já é sub
            return execute_query(
                'INSERT INTO fin_categorias (pai_id, tipo, grupo, nome, ordem) '
                'VALUES (%s, %s, %s, %s, %s)',
                (pai_id, pai['tipo'], pai['grupo'], nome, pai['ordem']))
        if ordem is None:
            r = execute_query(
                'SELECT MAX(ordem) AS m FROM fin_categorias WHERE tipo = %s '
                'AND grupo = %s', (tipo, grupo), fetch=True, fetch_one=True)
            if r and r['m'] is not None:
                ordem = r['m'] + 1          # dentro do grupo existente
            else:
                r = execute_query(
                    'SELECT MAX(ordem) AS m FROM fin_categorias WHERE tipo = %s',
                    (tipo,), fetch=True, fetch_one=True)
                ordem = ((r and r['m']) or 0) + 10   # grupo novo, bloco novo
        return execute_query(
            'INSERT INTO fin_categorias (tipo, grupo, nome, ordem) '
            'VALUES (%s, %s, %s, %s)', (tipo, grupo, nome, ordem))

    @staticmethod
    def renomear(cat_id, nome):
        execute_query('UPDATE fin_categorias SET nome = %s WHERE id = %s',
                      (nome, cat_id))
        r = execute_query('SELECT nome FROM fin_categorias WHERE id = %s',
                          (cat_id,), fetch=True, fetch_one=True)
        return bool(r and r['nome'] == nome)

    @staticmethod
    def set_ativa(cat_id, ativa):
        execute_query('UPDATE fin_categorias SET ativo = %s WHERE id = %s',
                      (1 if ativa else 0, cat_id))

    @staticmethod
    def usos():
        """{categoria_id: qtde de títulos} numa consulta só."""
        rows = execute_query(
            'SELECT categoria_id, COUNT(*) AS n FROM fin_titulos '
            'GROUP BY categoria_id', fetch=True) or []
        return {r['categoria_id']: r['n'] for r in rows}

    @staticmethod
    def em_uso(cat_id):
        r = execute_query('SELECT COUNT(*) AS n FROM fin_titulos '
                          'WHERE categoria_id = %s', (cat_id,),
                          fetch=True, fetch_one=True)
        return int((r or {}).get('n') or 0)


class FinDre:
    """DRE gerencial (Documento E, seção 7).

    COMPETÊNCIA soma fin_titulos.valor pelo mês de t.competencia (tudo menos
    cancelado — o resultado do mês independe de já ter sido pago). CAIXA soma
    as baixas pelo mês do pagamento, em dinheiro que de fato circulou
    (valor + juros + multa; desconto não é dinheiro). A tela SEMPRE diz qual
    regime está mostrando.
    """

    @staticmethod
    def por_ano(ano, regime='competencia', empresa_ids=None):
        """Linhas (tipo, grupo, nome da categoria, mês 1-12, total)."""
        cond, extra = '', ()
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond = f'AND t.empresa_id IN ({marks})'
            extra = tuple(empresa_ids)
        # A subcategoria aparece como "Pai · Sub"; o centro sai CRU em cada
        # linha — o rateio da GERAL é aplicado depois, na leitura.
        rotulo = "COALESCE(CONCAT(p.nome, ' · ', c.nome), c.nome)"
        if regime == 'caixa':
            return execute_query(
                f"""SELECT c.tipo, c.grupo, {rotulo} AS nome,
                          MIN(c.ordem) AS ordem, t.centro_custo_id AS centro,
                          MONTH(b.data_baixa) AS mes,
                          SUM(b.valor + b.juros + b.multa) AS total
                     FROM fin_titulo_baixas b
                     JOIN fin_titulos t ON t.id = b.titulo_id
                     JOIN fin_categorias c ON c.id = t.categoria_id
                     LEFT JOIN fin_categorias p ON p.id = c.pai_id
                    WHERE YEAR(b.data_baixa) = %s {cond}
                    GROUP BY c.tipo, c.grupo, {rotulo}, t.centro_custo_id,
                             MONTH(b.data_baixa)""",
                (ano,) + extra, fetch=True) or []
        return execute_query(
            f"""SELECT c.tipo, c.grupo, {rotulo} AS nome,
                      MIN(c.ordem) AS ordem, t.centro_custo_id AS centro,
                      MONTH(t.competencia) AS mes, SUM(t.valor) AS total
                 FROM fin_titulos t
                 JOIN fin_categorias c ON c.id = t.categoria_id
                 LEFT JOIN fin_categorias p ON p.id = c.pai_id
                WHERE YEAR(t.competencia) = %s AND t.status <> 'cancelado' {cond}
                GROUP BY c.tipo, c.grupo, {rotulo}, t.centro_custo_id,
                         MONTH(t.competencia)""",
            (ano,) + extra, fetch=True) or []

    @staticmethod
    def anos_com_dado():
        rows = execute_query(
            """SELECT YEAR(competencia) AS a FROM fin_titulos
                UNION SELECT YEAR(data_baixa) FROM fin_titulo_baixas
                ORDER BY a DESC""", fetch=True) or []
        return [r['a'] for r in rows if r['a']]


class FinFluxo:
    """Fluxo de caixa projetado (Documento E, seção 6).

    Saldo REAL (informado à mão até a fase 4 ligar o extrato) numa linha;
    PROJEÇÃO (títulos em aberto por vencimento) na outra — o documento manda
    nunca misturar os dois num número só.
    """

    @staticmethod
    def saldos_vigentes(empresa_ids=None):
        """O último saldo informado de CADA empresa (lista, pode ser vazia).

        Multiempresa (E2.1): o saldo real é POR EMPRESA; a tela soma os
        vigentes das empresas selecionadas.
        """
        cond, params = '', ()
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond = f'WHERE s.empresa_id IN ({marks})'
            params = tuple(empresa_ids)
        return execute_query(
            f"""SELECT s.id, s.empresa_id, s.data, s.valor, s.origem
                  FROM fin_saldos s
                  JOIN (SELECT empresa_id, MAX(id) AS mid
                          FROM fin_saldos GROUP BY empresa_id) u
                    ON u.mid = s.id
                {cond}
                 ORDER BY s.empresa_id""", params, fetch=True) or []

    @staticmethod
    def saldo_vigente():
        """Compatibilidade: o vigente mais recente entre todas (ou None)."""
        vs = FinFluxo.saldos_vigentes()
        return vs[-1] if vs else None

    @staticmethod
    def registrar_saldo(data, valor, usuario_id, empresa_id, origem='manual'):
        """Informe novo = linha nova (histórico completo, nunca sobrescreve)."""
        return execute_query(
            'INSERT INTO fin_saldos (empresa_id, data, valor, origem, usuario_id) '
            'VALUES (%s, %s, %s, %s, %s)',
            (empresa_id, data, valor, origem, usuario_id))

    @staticmethod
    def abertos_por_vencimento(empresa_ids=None):
        """Saldo devedor dos títulos em aberto, agrupado por dia e tipo."""
        cond, params = '', ()
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond = f'AND empresa_id IN ({marks})'
            params = tuple(empresa_ids)
        return execute_query(
            f"""SELECT vencimento, tipo, SUM(valor - valor_baixado) AS total
                 FROM fin_titulos
                WHERE status IN ('aberto', 'parcial') {cond}
                GROUP BY vencimento, tipo
                ORDER BY vencimento""", params, fetch=True) or []


class FinTitulo:

    @staticmethod
    def listar(tipo=None, status='abertos', venc_de=None, venc_ate=None,
               categoria_id=None, busca=None, empresa_ids=None, centro_id=None):
        """Lista títulos com a categoria junto, em ordem de vencimento.

        status: 'abertos' (aberto+parcial, o dia a dia da tela) | 'todos' |
        um status exato (aberto|parcial|liquidado|cancelado).
        """
        cond, params = [], []
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond.append(f't.empresa_id IN ({marks})')
            params += list(empresa_ids)
        if tipo in ('R', 'P'):
            cond.append('t.tipo = %s')
            params.append(tipo)
        if status == 'abertos':
            cond.append("t.status IN ('aberto', 'parcial')")
        elif status and status != 'todos':
            cond.append('t.status = %s')
            params.append(status)
        if venc_de:
            cond.append('t.vencimento >= %s')
            params.append(venc_de)
        if venc_ate:
            cond.append('t.vencimento <= %s')
            params.append(venc_ate)
        if categoria_id:
            cond.append('t.categoria_id = %s')
            params.append(categoria_id)
        if centro_id == 'sem':
            cond.append('t.centro_custo_id IS NULL')
        elif centro_id:
            cond.append('t.centro_custo_id = %s')
            params.append(centro_id)
        if busca:
            cond.append('(t.contraparte_nome LIKE %s OR t.descricao LIKE %s '
                        'OR t.contraparte_doc LIKE %s)')
            like = f'%{busca}%'
            params += [like, like, like]
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        return execute_query(
            f"""SELECT t.*, c.grupo AS categoria_grupo, c.nome AS categoria_nome,
                       cc.nome AS centro_nome,
                       (t.vencimento < CURDATE()
                        AND t.status IN ('aberto', 'parcial')) AS vencido
                  FROM fin_titulos t
                  JOIN fin_categorias c ON c.id = t.categoria_id
                  LEFT JOIN fin_centros_custo cc ON cc.id = t.centro_custo_id
                {where}
                 ORDER BY t.vencimento, t.id""",
            tuple(params), fetch=True) or []

    @staticmethod
    def resumo(empresa_ids=None):
        """Números dos cartões — só dado real, por tipo (e por empresa).

        Devolve {'R': {...}, 'P': {...}} com aberto (valor - baixado dos em
        aberto), vencido e a vencer nos próximos 7 dias.
        """
        filtro, params = '', ()
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            filtro = f'AND empresa_id IN ({marks})'
            params = tuple(empresa_ids)
        rows = execute_query(
            f"""SELECT tipo,
                      COALESCE(SUM(valor - valor_baixado), 0)                  AS em_aberto,
                      SUM(1)                                                    AS qtd,
                      COALESCE(SUM(CASE WHEN vencimento < CURDATE()
                                        THEN valor - valor_baixado END), 0)     AS vencido,
                      COALESCE(SUM(CASE WHEN vencimento < CURDATE()
                                        THEN 1 ELSE 0 END), 0)                  AS qtd_vencida,
                      COALESCE(SUM(CASE WHEN vencimento BETWEEN CURDATE()
                                        AND CURDATE() + INTERVAL 7 DAY
                                        THEN valor - valor_baixado END), 0)     AS vence_7d
                 FROM fin_titulos
                WHERE status IN ('aberto', 'parcial') {filtro}
                GROUP BY tipo""",
            params, fetch=True) or []
        base = {'em_aberto': 0, 'qtd': 0, 'vencido': 0, 'qtd_vencida': 0, 'vence_7d': 0}
        out = {'R': dict(base), 'P': dict(base)}
        for r in rows:
            if r['tipo'] in out:
                out[r['tipo']] = r
        return out

    @staticmethod
    def get_by_id(titulo_id):
        return execute_query(
            'SELECT t.*, c.grupo AS categoria_grupo, c.nome AS categoria_nome '
            '  FROM fin_titulos t JOIN fin_categorias c ON c.id = t.categoria_id '
            ' WHERE t.id = %s', (titulo_id,), fetch=True, fetch_one=True)

    @staticmethod
    def criar(tipo, contraparte_nome, categoria_id, descricao, competencia,
              emissao, vencimento, valor, empresa_id, contraparte_doc=None,
              cliente_id=None, observacao=None, origem='manual', chave_idem=None,
              centro_custo_id=None):
        return execute_query(
            """INSERT INTO fin_titulos
               (empresa_id, tipo, contraparte_doc, contraparte_nome, cliente_id,
                categoria_id, centro_custo_id, descricao, competencia, emissao,
                vencimento, valor, origem, chave_idem, observacao)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (empresa_id, tipo, contraparte_doc, contraparte_nome, cliente_id,
             categoria_id, centro_custo_id, descricao, competencia, emissao,
             vencimento, valor, origem, chave_idem, observacao))

    @staticmethod
    def cancelar(titulo_id):
        """Cancela SÓ título sem nenhum centavo baixado. Devolve linhas afetadas.

        Único lugar além de recalcular_status() que escreve em status — exceção
        prevista no documento: cancelamento é ato humano, não derivado, e
        recalcular_status() nunca mexe em cancelado.
        """
        # execute_query devolve True para QUALQUER update executado, mesmo
        # pegando 0 linhas — a resposta confiável é reler o estado.
        execute_query(
            "UPDATE fin_titulos SET status = 'cancelado' "
            " WHERE id = %s AND status = 'aberto' AND valor_baixado = 0 "
            "   AND NOT EXISTS (SELECT 1 FROM fin_titulo_baixas b "
            "                    WHERE b.titulo_id = fin_titulos.id)",
            (titulo_id,))
        t = execute_query('SELECT status FROM fin_titulos WHERE id = %s',
                          (titulo_id,), fetch=True, fetch_one=True)
        return bool(t and t['status'] == 'cancelado')

    @staticmethod
    def excluir(titulo_id):
        """Exclui SÓ título aberto, sem baixa e lançado à mão (erro de digitação).

        Título gerado por contrato/importação não some — cancela-se, para o
        histórico contar a história.
        """
        execute_query(
            "DELETE FROM fin_titulos "
            " WHERE id = %s AND status = 'aberto' AND valor_baixado = 0 "
            "   AND origem = 'manual' "
            "   AND NOT EXISTS (SELECT 1 FROM fin_titulo_baixas b "
            "                    WHERE b.titulo_id = fin_titulos.id)",
            (titulo_id,))
        t = execute_query('SELECT id FROM fin_titulos WHERE id = %s',
                          (titulo_id,), fetch=True, fetch_one=True)
        return t is None

    @staticmethod
    def baixas(titulo_id):
        return execute_query(
            'SELECT * FROM fin_titulo_baixas WHERE titulo_id = %s '
            'ORDER BY data_baixa, id', (titulo_id,), fetch=True) or []
