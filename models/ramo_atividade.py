"""Modelo de Ramo de Atividade"""
from utils.db_helper import execute_query


class RamoAtividade:
    """Classe para gestão de ramos de atividade dos clientes"""
    
    @staticmethod
    def get_all(situacao=None):
        """
        Retorna todos os ramos de atividade.
        
        Args:
            situacao (str, optional): Filtrar por situação (ATIVO, INATIVO)
            
        Returns:
            list: Lista de ramos de atividade
        """
        query = """
            SELECT id, nome, descricao, situacao
            FROM ramos_atividade
        """
        params = []
        
        if situacao:
            query += " WHERE situacao = %s"
            params.append(situacao)
        
        query += " ORDER BY nome"
        
        return execute_query(query, tuple(params) if params else None, fetch=True) or []
    
    @staticmethod
    def get_by_id(ramo_id):
        """
        Busca ramo de atividade por ID.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            
        Returns:
            dict: Dados do ramo ou None
        """
        query = """
            SELECT id, nome, descricao, situacao
            FROM ramos_atividade
            WHERE id = %s
        """
        return execute_query(query, (ramo_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def create(nome, descricao=None, situacao='ATIVO'):
        """
        Cria novo ramo de atividade.
        
        Args:
            nome (str): Nome do ramo de atividade
            descricao (str, optional): Descrição
            situacao (str, optional): Situação. Defaults to 'ATIVO'
            
        Returns:
            int: ID do ramo criado ou None
        """
        query = """
            INSERT INTO ramos_atividade (nome, descricao, situacao)
            VALUES (%s, %s, %s)
        """
        return execute_query(query, (nome, descricao, situacao))
    
    @staticmethod
    def update(ramo_id, nome, descricao=None, situacao='ATIVO'):
        """
        Atualiza dados do ramo de atividade.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            nome (str): Nome do ramo
            descricao (str, optional): Descrição
            situacao (str, optional): Situação
            
        Returns:
            int: Número de linhas afetadas ou None
        """
        query = """
            UPDATE ramos_atividade
            SET nome = %s, descricao = %s, situacao = %s
            WHERE id = %s
        """
        return execute_query(query, (nome, descricao, situacao, ramo_id), fetch=False)
    
    @staticmethod
    def delete(ramo_id):
        """
        Remove ramo de atividade.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            
        Returns:
            int: Número de linhas afetadas ou None
        """
        query = """
            DELETE FROM ramos_atividade
            WHERE id = %s
        """
        return execute_query(query, (ramo_id,), fetch=False)
    
    @staticmethod
    def add_cliente(ramo_id, cliente_id):
        """
        Adiciona cliente ao ramo de atividade.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            cliente_id (int): ID do cliente
            
        Returns:
            int: ID da relação criada ou None
        """
        query = """
            INSERT INTO cliente_ramo_atividade_relacao (cliente_id, ramo_atividade_id)
            VALUES (%s, %s)
        """
        try:
            return execute_query(query, (cliente_id, ramo_id))
        except:
            # Pode falhar se já existir a relação (UNIQUE constraint)
            return None
    
    @staticmethod
    def remove_cliente(ramo_id, cliente_id):
        """
        Remove cliente do ramo de atividade.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            cliente_id (int): ID do cliente
            
        Returns:
            int: Número de linhas afetadas ou None
        """
        query = """
            DELETE FROM cliente_ramo_atividade_relacao
            WHERE cliente_id = %s AND ramo_atividade_id = %s
        """
        return execute_query(query, (cliente_id, ramo_id), fetch=False)
    
    @staticmethod
    def get_clientes(ramo_id):
        """
        Retorna clientes do ramo de atividade.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            
        Returns:
            list: Lista de clientes
        """
        query = """
            SELECT c.id, c.tipo_pessoa, c.nome_razao_social, c.cpf_cnpj, 
                   c.email, c.telefone, c.celular, c.situacao
            FROM clientes c
            INNER JOIN cliente_ramo_atividade_relacao crar ON c.id = crar.cliente_id
            WHERE crar.ramo_atividade_id = %s
            ORDER BY c.nome_razao_social
        """
        return execute_query(query, (ramo_id,), fetch=True) or []
    
    @staticmethod
    def get_by_cliente(cliente_id):
        """
        Retorna ramos de atividade do cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de ramos de atividade
        """
        query = """
            SELECT ra.id, ra.nome, ra.descricao, ra.situacao
            FROM ramos_atividade ra
            INNER JOIN cliente_ramo_atividade_relacao crar ON ra.id = crar.ramo_atividade_id
            WHERE crar.cliente_id = %s
            ORDER BY ra.nome
        """
        return execute_query(query, (cliente_id,), fetch=True) or []
