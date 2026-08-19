"""Modelo de Sócio de Cliente"""
from decimal import Decimal
from utils.db_helper import execute_query


class SocioCliente:
    """Classe para gestão de sócios de clientes"""

    @staticmethod
    def get_by_cliente(cliente_id):
        """Busca todos os sócios de um cliente.

        O sócio é um VÍNCULO com o cadastro PF (pf_cliente_id): nome, CPF,
        e-mail e telefone saem do cadastro VIVO — corrigiu lá, corrigiu em
        todas as sociedades. As colunas locais ficam de retrato/fallback
        (vínculo apagado do cadastro ainda exibe o que era).
        """
        query = """
            SELECT s.id, s.cliente_id, s.pf_cliente_id,
                   COALESCE(pf.nome_razao_social, s.nome) AS nome,
                   COALESCE(pf.cpf_cnpj, s.cpf) AS cpf,
                   COALESCE(NULLIF(pf.email, ''), s.email) AS email,
                   COALESCE(NULLIF(pf.celular, ''), NULLIF(pf.telefone, ''), s.telefone) AS telefone,
                   s.percentual_participacao, s.responsavel, s.ativo, s.criado_em
            FROM socios_clientes s
            LEFT JOIN clientes pf ON pf.id = s.pf_cliente_id
            WHERE s.cliente_id = %s
            ORDER BY s.responsavel DESC, nome ASC
        """
        return execute_query(query, (cliente_id,), fetch=True) or []

    @staticmethod
    def existe_pf(cliente_id, pf_cliente_id):
        """True se esta Pessoa Física já é sócia deste cliente."""
        row = execute_query(
            'SELECT id FROM socios_clientes WHERE cliente_id = %s AND pf_cliente_id = %s LIMIT 1',
            (cliente_id, pf_cliente_id), fetch=True, fetch_one=True)
        return bool(row)

    @staticmethod
    def get_by_id(socio_id):
        """Busca sócio por ID."""
        query = """
            SELECT id, cliente_id, pf_cliente_id, nome, cpf, email, telefone, percentual_participacao, responsavel, ativo, criado_em
            FROM socios_clientes
            WHERE id = %s
        """
        return execute_query(query, (socio_id,), fetch=True, fetch_one=True)

    @staticmethod
    def get_total_percentual(cliente_id):
        """Retorna o total percentual de participação dos sócios ativos do cliente."""
        query = """
            SELECT COALESCE(SUM(percentual_participacao), 0) AS total
            FROM socios_clientes
            WHERE cliente_id = %s AND ativo = TRUE
        """
        row = execute_query(query, (cliente_id,), fetch=True, fetch_one=True) or {}
        return Decimal(str(row.get('total') or 0))

    @staticmethod
    def create(cliente_id, nome, cpf, email=None, telefone=None, percentual_participacao=0, responsavel=False, ativo=True, pf_cliente_id=None):
        """Cria novo sócio (vinculado ao cadastro PF via pf_cliente_id)."""
        if responsavel:
            SocioCliente.set_responsavel(cliente_id, None)

        query = """
            INSERT INTO socios_clientes (
                cliente_id, pf_cliente_id, nome, cpf, email, telefone, percentual_participacao, responsavel, ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            cliente_id,
            pf_cliente_id,
            (nome or '').strip().upper(),
            (cpf or '').strip(),
            (email or '').strip() or None,
            (telefone or '').strip() or None,
            percentual_participacao,
            responsavel,
            ativo
        )
        return execute_query(query, params)

    @staticmethod
    def delete(socio_id):
        """Remove sócio."""
        query = "DELETE FROM socios_clientes WHERE id = %s"
        return execute_query(query, (socio_id,))

    @staticmethod
    def set_responsavel(cliente_id, socio_id):
        """Define um sócio como responsável e desmarca os demais."""
        query = """
            UPDATE socios_clientes
            SET responsavel = FALSE
            WHERE cliente_id = %s
        """
        execute_query(query, (cliente_id,))

        if socio_id:
            query = """
                UPDATE socios_clientes
                SET responsavel = TRUE
                WHERE id = %s AND cliente_id = %s
            """
            return execute_query(query, (socio_id, cliente_id)) is not None
        return True
