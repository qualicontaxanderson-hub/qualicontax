"""Modelo de Cadastro Adicional de Cliente"""
from utils.db_helper import execute_query


class CadastroAdicionalCliente:
    """Classe para gestão de cadastros adicionais de clientes"""

    @staticmethod
    def get_by_cliente(cliente_id):
        """Busca todos os cadastros adicionais de um cliente."""
        query = """
            SELECT id, cliente_id, tipo, campo, valor, data_referencia, observacoes, ativo, criado_em
            FROM cadastros_adicionais_clientes
            WHERE cliente_id = %s
            ORDER BY (data_referencia IS NULL), data_referencia DESC, id DESC
        """
        return execute_query(query, (cliente_id,), fetch=True) or []

    @staticmethod
    def get_by_id(cadastro_id):
        """Busca cadastro adicional por ID."""
        query = """
            SELECT id, cliente_id, tipo, campo, valor, data_referencia, observacoes, ativo, criado_em
            FROM cadastros_adicionais_clientes
            WHERE id = %s
        """
        return execute_query(query, (cadastro_id,), fetch=True, fetch_one=True)

    @staticmethod
    def create(cliente_id, tipo, campo, valor=None, data_referencia=None, observacoes=None, ativo=True):
        """Cria novo cadastro adicional."""
        query = """
            INSERT INTO cadastros_adicionais_clientes (
                cliente_id, tipo, campo, valor, data_referencia, observacoes, ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            cliente_id,
            (tipo or '').strip().upper() or None,
            (campo or '').strip().upper() or None,
            (valor or '').strip() or None,
            data_referencia or None,
            (observacoes or '').strip() or None,
            ativo
        )
        return execute_query(query, params)

    @staticmethod
    def delete(cadastro_id):
        """Remove cadastro adicional."""
        query = "DELETE FROM cadastros_adicionais_clientes WHERE id = %s"
        return execute_query(query, (cadastro_id,))

