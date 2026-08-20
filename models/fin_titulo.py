# -*- coding: utf-8 -*-
"""Títulos do financeiro do ESCRITÓRIO (Documento E, fase 2).

Leitura e criação apenas. Baixa NÃO passa por aqui — o único escritor de
fin_titulo_baixas é utils.financeiro_core.registrar_baixa(), e status/
valor_baixado só quem escreve é recalcular_status(). Cancelar é a exceção
combinada no documento: ato humano, não derivado — e só em título sem baixa.
"""
from utils.db_helper import execute_query


class FinCategoria:
    """Plano gerencial (grupo = linha do DRE)."""

    @staticmethod
    def listar(tipo=None, apenas_ativas=True):
        sql = ['SELECT id, tipo, grupo, nome, ordem, ativo FROM fin_categorias']
        cond, params = [], []
        if tipo in ('R', 'P'):
            cond.append('tipo = %s')
            params.append(tipo)
        if apenas_ativas:
            cond.append('ativo = 1')
        if cond:
            sql.append('WHERE ' + ' AND '.join(cond))
        sql.append('ORDER BY ordem, nome')
        return execute_query(' '.join(sql), tuple(params), fetch=True) or []


class FinTitulo:

    @staticmethod
    def listar(tipo=None, status='abertos', venc_de=None, venc_ate=None,
               categoria_id=None, busca=None):
        """Lista títulos com a categoria junto, em ordem de vencimento.

        status: 'abertos' (aberto+parcial, o dia a dia da tela) | 'todos' |
        um status exato (aberto|parcial|liquidado|cancelado).
        """
        cond, params = [], []
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
        if busca:
            cond.append('(t.contraparte_nome LIKE %s OR t.descricao LIKE %s '
                        'OR t.contraparte_doc LIKE %s)')
            like = f'%{busca}%'
            params += [like, like, like]
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        return execute_query(
            f"""SELECT t.*, c.grupo AS categoria_grupo, c.nome AS categoria_nome,
                       (t.vencimento < CURDATE()
                        AND t.status IN ('aberto', 'parcial')) AS vencido
                  FROM fin_titulos t
                  JOIN fin_categorias c ON c.id = t.categoria_id
                {where}
                 ORDER BY t.vencimento, t.id""",
            tuple(params), fetch=True) or []

    @staticmethod
    def resumo():
        """Números dos cartões — só dado real, por tipo.

        Devolve {'R': {...}, 'P': {...}} com aberto (valor - baixado dos em
        aberto), vencido e a vencer nos próximos 7 dias.
        """
        rows = execute_query(
            """SELECT tipo,
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
                WHERE status IN ('aberto', 'parcial')
                GROUP BY tipo""",
            fetch=True) or []
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
              emissao, vencimento, valor, contraparte_doc=None, cliente_id=None,
              observacao=None, origem='manual', chave_idem=None):
        return execute_query(
            """INSERT INTO fin_titulos
               (tipo, contraparte_doc, contraparte_nome, cliente_id, categoria_id,
                descricao, competencia, emissao, vencimento, valor, origem,
                chave_idem, observacao)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (tipo, contraparte_doc, contraparte_nome, cliente_id, categoria_id,
             descricao, competencia, emissao, vencimento, valor, origem,
             chave_idem, observacao))

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
