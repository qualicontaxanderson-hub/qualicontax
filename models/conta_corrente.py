"""Modelo de Conta Corrente / Conta Bancária"""
from utils.db_helper import execute_query


class ContaCorrente:
    """Gestão de contas bancárias dos clientes"""

    @staticmethod
    def get_all(cliente_id=None, banco=None, ativa=None):
        """
        Retorna todas as contas bancárias, com JOIN ao cliente.

        Args:
            cliente_id (int, optional): Filtrar por cliente
            banco (str, optional): Filtrar por nome do banco (LIKE)
            ativa (bool, optional): Filtrar por situação ativa/inativa

        Returns:
            list: Lista de contas
        """
        query = """
            SELECT cb.id, cb.cliente_id, cb.banco_nome, cb.banco_codigo,
                   cb.agencia, cb.agencia_digito, cb.numero_conta, cb.conta_digito,
                   cb.tipo, cb.saldo, cb.ativa, cb.criado_em,
                   c.nome_razao_social AS cliente_nome
            FROM contas_bancarias cb
            LEFT JOIN clientes c ON cb.cliente_id = c.id
            WHERE 1=1
        """
        params = []

        if cliente_id:
            query += " AND cb.cliente_id = %s"
            params.append(cliente_id)

        if banco:
            query += " AND cb.banco_nome LIKE %s"
            params.append(f"%{banco}%")

        if ativa is not None:
            query += " AND cb.ativa = %s"
            params.append(1 if ativa else 0)

        query += " ORDER BY c.nome_razao_social, cb.banco_nome"
        return execute_query(query, tuple(params) if params else None, fetch=True) or []

    @staticmethod
    def get_by_id(conta_id):
        """Busca conta por ID."""
        query = """
            SELECT cb.id, cb.cliente_id, cb.banco_nome, cb.banco_codigo,
                   cb.agencia, cb.agencia_digito, cb.numero_conta, cb.conta_digito,
                   cb.tipo, cb.saldo, cb.ativa, cb.criado_em,
                   c.nome_razao_social AS cliente_nome
            FROM contas_bancarias cb
            LEFT JOIN clientes c ON cb.cliente_id = c.id
            WHERE cb.id = %s
        """
        return execute_query(query, (conta_id,), fetch=True, fetch_one=True)

    @staticmethod
    def create(cliente_id, banco_nome, banco_codigo, agencia, agencia_digito,
               numero_conta, conta_digito, tipo, saldo_inicial=0.00):
        """
        Cria nova conta bancária.

        Returns:
            int: ID da conta criada, ou None em caso de erro
        """
        query = """
            INSERT INTO contas_bancarias
                (cliente_id, banco_nome, banco_codigo, agencia, agencia_digito,
                 numero_conta, conta_digito, tipo, saldo, ativa)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
        """
        return execute_query(query, (
            cliente_id, banco_nome, banco_codigo,
            agencia, agencia_digito or '',
            numero_conta, conta_digito,
            tipo, saldo_inicial,
        ))

    @staticmethod
    def update_saldo(conta_id, saldo):
        """Atualiza saldo da conta."""
        query = "UPDATE contas_bancarias SET saldo = %s WHERE id = %s"
        return execute_query(query, (saldo, conta_id))

    @staticmethod
    def set_ativa(conta_id, ativa):
        """Ativa ou desativa uma conta."""
        query = "UPDATE contas_bancarias SET ativa = %s WHERE id = %s"
        return execute_query(query, (1 if ativa else 0, conta_id))

    @staticmethod
    def delete(conta_id):
        """Remove uma conta bancária."""
        query = "DELETE FROM contas_bancarias WHERE id = %s"
        return execute_query(query, (conta_id,))
