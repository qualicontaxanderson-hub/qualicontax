"""Modelo de Processo"""
from utils.db_helper import execute_query


class Processo:
    """Classe Processo para gestão de processos"""
    
    def __init__(self, id, cliente_id, numero_processo, tipo, status, data_abertura,
                 data_conclusao=None, descricao=None):
        self.id = id
        self.cliente_id = cliente_id
        self.numero_processo = numero_processo
        self.tipo = tipo
        self.status = status
        self.data_abertura = data_abertura
        self.data_conclusao = data_conclusao
        self.descricao = descricao
    
    @staticmethod
    def get_by_id(processo_id):
        """
        Busca processo por ID.
        
        Args:
            processo_id (int): ID do processo
            
        Returns:
            dict: Dados do processo ou None
        """
        query = """
            SELECT id, cliente_id, numero_processo, tipo, status, data_abertura,
                   data_conclusao, descricao
            FROM processos
            WHERE id = %s
        """
        return execute_query(query, (processo_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def get_by_cliente(cliente_id):
        """
        Busca todos os processos de um cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de processos do cliente
        """
        query = """
            SELECT id, cliente_id, numero_processo, tipo, status, data_abertura,
                   data_conclusao, descricao
            FROM processos
            WHERE cliente_id = %s
            ORDER BY data_abertura DESC
        """
        return execute_query(query, (cliente_id,), fetch=True) or []
    
    @staticmethod
    def get_all(filters=None):
        """
        Retorna todos os processos com filtros opcionais.
        
        Args:
            filters (dict): Filtros a serem aplicados
            
        Returns:
            list: Lista de processos
        """
        filters = filters or {}
        conditions = []
        params = []
        
        if filters.get('cliente_id'):
            conditions.append("cliente_id = %s")
            params.append(filters['cliente_id'])
        
        if filters.get('tipo'):
            conditions.append("tipo = %s")
            params.append(filters['tipo'])
        
        if filters.get('status'):
            conditions.append("status = %s")
            params.append(filters['status'])
        
        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        
        query = f"""
            SELECT id, cliente_id, numero_processo, tipo, status, data_abertura,
                   data_conclusao, descricao
            FROM processos
            {where_clause}
            ORDER BY data_abertura DESC
        """
        
        return execute_query(query, tuple(params), fetch=True) or []
    
    @staticmethod
    def create(data):
        """
        Cria novo processo.
        
        Args:
            data (dict): Dados do processo
            
        Returns:
            int: ID do processo criado ou None
        """
        query = """
            INSERT INTO processos (
                cliente_id, numero_processo, tipo, status, data_abertura,
                data_conclusao, descricao, data_criacao
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """
        params = (
            data.get('cliente_id'),
            data.get('numero_processo'),
            data.get('tipo'),
            data.get('status', 'Em Andamento'),
            data.get('data_abertura'),
            data.get('data_conclusao'),
            data.get('descricao')
        )
        return execute_query(query, params)
    
    @staticmethod
    def update(processo_id, data):
        """
        Atualiza dados do processo.
        
        Args:
            processo_id (int): ID do processo
            data (dict): Dados a serem atualizados
            
        Returns:
            int: ID do processo ou None
        """
        query = """
            UPDATE processos
            SET cliente_id = %s, numero_processo = %s, tipo = %s, status = %s,
                data_abertura = %s, data_conclusao = %s, descricao = %s,
                data_atualizacao = NOW()
            WHERE id = %s
        """
        params = (
            data.get('cliente_id'),
            data.get('numero_processo'),
            data.get('tipo'),
            data.get('status'),
            data.get('data_abertura'),
            data.get('data_conclusao'),
            data.get('descricao'),
            processo_id
        )
        return execute_query(query, params)
