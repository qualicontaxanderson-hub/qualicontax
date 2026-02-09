"""Modelo de Obrigação"""
from utils.db_helper import execute_query


class Obrigacao:
    """Classe Obrigacao para gestão de obrigações fiscais"""
    
    def __init__(self, id, cliente_id, tipo_obrigacao_id, descricao, vencimento,
                 valor=None, status='Pendente', pago=False):
        self.id = id
        self.cliente_id = cliente_id
        self.tipo_obrigacao_id = tipo_obrigacao_id
        self.descricao = descricao
        self.vencimento = vencimento
        self.valor = valor
        self.status = status
        self.pago = pago
    
    @staticmethod
    def get_by_id(obrigacao_id):
        """
        Busca obrigação por ID.
        
        Args:
            obrigacao_id (int): ID da obrigação
            
        Returns:
            dict: Dados da obrigação ou None
        """
        query = """
            SELECT id, cliente_id, tipo_obrigacao_id, descricao, vencimento,
                   valor, status, pago
            FROM obrigacoes
            WHERE id = %s
        """
        return execute_query(query, (obrigacao_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def get_by_cliente(cliente_id):
        """
        Busca todas as obrigações de um cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de obrigações do cliente
        """
        query = """
            SELECT id, cliente_id, tipo_obrigacao_id, descricao, vencimento,
                   valor, status, pago
            FROM obrigacoes
            WHERE cliente_id = %s
            ORDER BY vencimento ASC
        """
        return execute_query(query, (cliente_id,), fetch=True) or []
    
    @staticmethod
    def get_pendentes():
        """
        Busca todas as obrigações pendentes.
        
        Returns:
            list: Lista de obrigações pendentes
        """
        query = """
            SELECT id, cliente_id, tipo_obrigacao_id, descricao, vencimento,
                   valor, status, pago
            FROM obrigacoes
            WHERE pago = FALSE AND status = 'Pendente'
            ORDER BY vencimento ASC
        """
        return execute_query(query, fetch=True) or []
    
    @staticmethod
    def create(data):
        """
        Cria nova obrigação.
        
        Args:
            data (dict): Dados da obrigação
            
        Returns:
            int: ID da obrigação criada ou None
        """
        query = """
            INSERT INTO obrigacoes (
                cliente_id, tipo_obrigacao_id, descricao, vencimento,
                valor, status, pago, data_criacao
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """
        params = (
            data.get('cliente_id'),
            data.get('tipo_obrigacao_id'),
            data.get('descricao'),
            data.get('vencimento'),
            data.get('valor'),
            data.get('status', 'Pendente'),
            data.get('pago', False)
        )
        return execute_query(query, params)
    
    @staticmethod
    def update(obrigacao_id, data):
        """
        Atualiza dados da obrigação.
        
        Args:
            obrigacao_id (int): ID da obrigação
            data (dict): Dados a serem atualizados
            
        Returns:
            int: ID da obrigação ou None
        """
        query = """
            UPDATE obrigacoes
            SET cliente_id = %s, tipo_obrigacao_id = %s, descricao = %s,
                vencimento = %s, valor = %s, status = %s, pago = %s,
                data_atualizacao = NOW()
            WHERE id = %s
        """
        params = (
            data.get('cliente_id'),
            data.get('tipo_obrigacao_id'),
            data.get('descricao'),
            data.get('vencimento'),
            data.get('valor'),
            data.get('status'),
            data.get('pago'),
            obrigacao_id
        )
        return execute_query(query, params)
