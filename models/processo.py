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
    def get_all(filters=None, page=1, per_page=10):
        """
        Retorna todos os processos com filtros opcionais e paginação.
        
        Args:
            filters (dict): Filtros a serem aplicados
            page (int): Número da página
            per_page (int): Registros por página
            
        Returns:
            dict: Dicionário com 'processos' e 'total'
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
        
        count_query = f"SELECT COUNT(*) as total FROM processos{where_clause}"
        total_result = execute_query(count_query, tuple(params), fetch=True, fetch_one=True)
        total = total_result['total'] if total_result else 0
        
        offset = (page - 1) * per_page
        params.extend([per_page, offset])
        
        query = f"""
            SELECT id, cliente_id, numero_processo, tipo, status, data_abertura,
                   data_conclusao, descricao
            FROM processos
            {where_clause}
            ORDER BY data_abertura DESC
            LIMIT %s OFFSET %s
        """
        
        processos = execute_query(query, tuple(params), fetch=True) or []
        
        return {
            'processos': processos,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        }
    
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
