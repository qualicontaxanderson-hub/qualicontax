"""Modelo de Tarefa"""
from utils.db_helper import execute_query


class Tarefa:
    """Classe Tarefa para gestão de tarefas"""
    
    def __init__(self, id, processo_id, usuario_id, titulo, descricao=None, prazo=None,
                 status='Pendente', prioridade='Média'):
        self.id = id
        self.processo_id = processo_id
        self.usuario_id = usuario_id
        self.titulo = titulo
        self.descricao = descricao
        self.prazo = prazo
        self.status = status
        self.prioridade = prioridade
    
    @staticmethod
    def get_by_id(tarefa_id):
        """
        Busca tarefa por ID.
        
        Args:
            tarefa_id (int): ID da tarefa
            
        Returns:
            dict: Dados da tarefa ou None
        """
        query = """
            SELECT id, processo_id, usuario_id, titulo, descricao, prazo,
                   status, prioridade
            FROM tarefas
            WHERE id = %s
        """
        return execute_query(query, (tarefa_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def get_by_usuario(usuario_id):
        """
        Busca todas as tarefas de um usuário.
        
        Args:
            usuario_id (int): ID do usuário
            
        Returns:
            list: Lista de tarefas do usuário
        """
        query = """
            SELECT id, processo_id, usuario_id, titulo, descricao, prazo,
                   status, prioridade
            FROM tarefas
            WHERE usuario_id = %s
            ORDER BY prazo ASC, prioridade DESC
        """
        return execute_query(query, (usuario_id,), fetch=True) or []
    
    @staticmethod
    def create(data):
        """
        Cria nova tarefa.
        
        Args:
            data (dict): Dados da tarefa
            
        Returns:
            int: ID da tarefa criada ou None
        """
        query = """
            INSERT INTO tarefas (
                processo_id, usuario_id, titulo, descricao, prazo,
                status, prioridade, data_criacao
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """
        params = (
            data.get('processo_id'),
            data.get('usuario_id'),
            data.get('titulo'),
            data.get('descricao'),
            data.get('prazo'),
            data.get('status', 'Pendente'),
            data.get('prioridade', 'Média')
        )
        return execute_query(query, params)
    
    @staticmethod
    def update(tarefa_id, data):
        """
        Atualiza dados da tarefa.
        
        Args:
            tarefa_id (int): ID da tarefa
            data (dict): Dados a serem atualizados
            
        Returns:
            int: ID da tarefa ou None
        """
        query = """
            UPDATE tarefas
            SET processo_id = %s, usuario_id = %s, titulo = %s, descricao = %s,
                prazo = %s, status = %s, prioridade = %s, data_atualizacao = NOW()
            WHERE id = %s
        """
        params = (
            data.get('processo_id'),
            data.get('usuario_id'),
            data.get('titulo'),
            data.get('descricao'),
            data.get('prazo'),
            data.get('status'),
            data.get('prioridade'),
            tarefa_id
        )
        return execute_query(query, params)
    
    @staticmethod
    def complete(tarefa_id):
        """
        Marca tarefa como concluída.
        
        Args:
            tarefa_id (int): ID da tarefa
            
        Returns:
            int: ID da tarefa ou None
        """
        query = """
            UPDATE tarefas
            SET status = 'Concluída', data_conclusao = NOW(), data_atualizacao = NOW()
            WHERE id = %s
        """
        return execute_query(query, (tarefa_id,))
