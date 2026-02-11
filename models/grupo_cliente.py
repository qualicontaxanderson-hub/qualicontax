"""Modelo de Grupo de Clientes"""
from utils.db_helper import execute_query


class GrupoCliente:
    """Classe para gestão de grupos de clientes"""
    
    @staticmethod
    def get_all(situacao=None):
        """
        Retorna todos os grupos.
        
        Args:
            situacao (str, optional): Filtrar por situação (ATIVO, INATIVO)
            
        Returns:
            list: Lista de grupos
        """
        query = """
            SELECT id, nome, descricao, situacao
            FROM grupos_clientes
        """
        params = []
        
        if situacao:
            query += " WHERE situacao = %s"
            params.append(situacao)
        
        query += " ORDER BY nome"
        
        return execute_query(query, tuple(params) if params else None, fetch=True) or []
    
    @staticmethod
    def get_by_id(grupo_id):
        """
        Busca grupo por ID.
        
        Args:
            grupo_id (int): ID do grupo
            
        Returns:
            dict: Dados do grupo ou None
        """
        query = """
            SELECT id, nome, descricao, situacao
            FROM grupos_clientes
            WHERE id = %s
        """
        return execute_query(query, (grupo_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def create(nome, descricao=None, situacao='ATIVO'):
        """
        Cria novo grupo.
        
        Args:
            nome (str): Nome do grupo
            descricao (str, optional): Descrição
            situacao (str, optional): Situação. Defaults to 'ATIVO'
            
        Returns:
            int: ID do grupo criado ou None
        """
        query = """
            INSERT INTO grupos_clientes (nome, descricao, situacao)
            VALUES (%s, %s, %s)
        """
        return execute_query(query, (nome, descricao, situacao))
    
    @staticmethod
    def update(grupo_id, nome, descricao=None, situacao='ATIVO'):
        """
        Atualiza dados do grupo.
        
        Args:
            grupo_id (int): ID do grupo
            nome (str): Nome do grupo
            descricao (str, optional): Descrição
            situacao (str, optional): Situação
            
        Returns:
            int: ID do grupo ou None
        """
        query = """
            UPDATE grupos_clientes
            SET nome = %s, descricao = %s, situacao = %s
            WHERE id = %s
        """
        return execute_query(query, (nome, descricao, situacao, grupo_id))
    
    @staticmethod
    def delete(grupo_id):
        """
        Remove grupo.
        
        Args:
            grupo_id (int): ID do grupo
            
        Returns:
            int: ID do grupo ou None
        """
        query = "DELETE FROM grupos_clientes WHERE id = %s"
        return execute_query(query, (grupo_id,))
    
    @staticmethod
    def add_cliente(grupo_id, cliente_id):
        """
        Adiciona cliente ao grupo.
        
        Args:
            grupo_id (int): ID do grupo
            cliente_id (int): ID do cliente
            
        Returns:
            int: ID da relação criada ou None
        """
        # Verifica se já existe a relação
        check_query = """
            SELECT id FROM cliente_grupo_relacao
            WHERE cliente_id = %s AND grupo_id = %s
        """
        existing = execute_query(check_query, (cliente_id, grupo_id), fetch=True, fetch_one=True)
        
        if existing:
            return existing['id']
        
        query = """
            INSERT INTO cliente_grupo_relacao (cliente_id, grupo_id)
            VALUES (%s, %s)
        """
        return execute_query(query, (cliente_id, grupo_id))
    
    @staticmethod
    def remove_cliente(grupo_id, cliente_id):
        """
        Remove cliente do grupo.
        
        Args:
            grupo_id (int): ID do grupo
            cliente_id (int): ID do cliente
            
        Returns:
            int: Resultado da operação
        """
        query = """
            DELETE FROM cliente_grupo_relacao
            WHERE cliente_id = %s AND grupo_id = %s
        """
        return execute_query(query, (cliente_id, grupo_id))
    
    @staticmethod
    def get_clientes(grupo_id):
        """
        Retorna clientes de um grupo.
        
        Args:
            grupo_id (int): ID do grupo
            
        Returns:
            list: Lista de clientes do grupo
        """
        query = """
            SELECT c.id, c.tipo_pessoa, c.nome_razao_social, c.cpf_cnpj,
                   c.email, c.telefone, c.situacao
            FROM clientes c
            INNER JOIN cliente_grupo_relacao cgr ON c.id = cgr.cliente_id
            WHERE cgr.grupo_id = %s
            ORDER BY c.nome_razao_social
        """
        return execute_query(query, (grupo_id,), fetch=True) or []
