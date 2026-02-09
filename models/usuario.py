"""Modelo de Usuário"""
from flask_login import UserMixin
from utils.db_helper import execute_query


class Usuario(UserMixin):
    """Classe Usuario para autenticação com Flask-Login"""
    
    def __init__(self, id, nome, email, senha_hash, tipo_usuario='ASSISTENTE', situacao='ATIVO', 
                 cpf=None, telefone=None, departamento_id=None, cargo=None, 
                 capacidade_tarefas=10, data_admissao=None, foto_perfil=None):
        self.id = id
        self.nome = nome
        self.email = email
        self.senha_hash = senha_hash
        self.tipo_usuario = tipo_usuario
        self.situacao = situacao
        self.cpf = cpf
        self.telefone = telefone
        self.departamento_id = departamento_id
        self.cargo = cargo
        self.capacidade_tarefas = capacidade_tarefas
        self.data_admissao = data_admissao
        self.foto_perfil = foto_perfil
    
    def is_admin(self):
        """Verifica se o usuário é admin"""
        return self.tipo_usuario == 'ADMIN'
    
    def is_active(self):
        """Verifica se o usuário está ativo (requerido pelo Flask-Login)"""
        return self.situacao == 'ATIVO'
    
    def get_id(self):
        """Retorna o ID do usuário (requerido pelo Flask-Login)"""
        return str(self.id)
    
    @staticmethod
    def get_by_id(user_id):
        """
        Busca usuário por ID.
        
        Args:
            user_id (int): ID do usuário
            
        Returns:
            Usuario: Objeto Usuario ou None
        """
        query = """
            SELECT id, nome, email, senha_hash, tipo_usuario, situacao,
                   cpf, telefone, departamento_id, cargo, capacidade_tarefas,
                   data_admissao, foto_perfil
            FROM usuarios
            WHERE id = %s
        """
        result = execute_query(query, (user_id,), fetch=True, fetch_one=True)
        
        if result:
            return Usuario(
                id=result['id'],
                nome=result['nome'],
                email=result['email'],
                senha_hash=result['senha_hash'],
                tipo_usuario=result.get('tipo_usuario', 'ASSISTENTE'),
                situacao=result.get('situacao', 'ATIVO'),
                cpf=result.get('cpf'),
                telefone=result.get('telefone'),
                departamento_id=result.get('departamento_id'),
                cargo=result.get('cargo'),
                capacidade_tarefas=result.get('capacidade_tarefas', 10),
                data_admissao=result.get('data_admissao'),
                foto_perfil=result.get('foto_perfil')
            )
        return None
    
    @staticmethod
    def get_by_email(email):
        """
        Busca usuário por email.
        
        Args:
            email (str): Email do usuário
            
        Returns:
            Usuario: Objeto Usuario ou None
        """
        query = """
            SELECT id, nome, email, senha_hash, tipo_usuario, situacao,
                   cpf, telefone, departamento_id, cargo, capacidade_tarefas,
                   data_admissao, foto_perfil
            FROM usuarios
            WHERE email = %s
        """
        result = execute_query(query, (email,), fetch=True, fetch_one=True)
        
        if result:
            return Usuario(
                id=result['id'],
                nome=result['nome'],
                email=result['email'],
                senha_hash=result['senha_hash'],
                tipo_usuario=result.get('tipo_usuario', 'ASSISTENTE'),
                situacao=result.get('situacao', 'ATIVO'),
                cpf=result.get('cpf'),
                telefone=result.get('telefone'),
                departamento_id=result.get('departamento_id'),
                cargo=result.get('cargo'),
                capacidade_tarefas=result.get('capacidade_tarefas', 10),
                data_admissao=result.get('data_admissao'),
                foto_perfil=result.get('foto_perfil')
            )
        return None
    
    @staticmethod
    def create(nome, email, senha_hash, tipo_usuario='ASSISTENTE', cpf=None, telefone=None, 
               departamento_id=None, cargo=None, capacidade_tarefas=10):
        """
        Cria novo usuário.
        
        Args:
            nome (str): Nome do usuário
            email (str): Email do usuário
            senha_hash (str): Hash da senha
            tipo_usuario (str): Tipo do usuário (ADMIN/GERENTE/CONTADOR/ASSISTENTE/ESTAGIARIO)
            cpf (str): CPF do usuário
            telefone (str): Telefone do usuário
            departamento_id (int): ID do departamento
            cargo (str): Cargo do usuário
            capacidade_tarefas (int): Capacidade de tarefas simultâneas
            
        Returns:
            int: ID do usuário criado ou None
        """
        query = """
            INSERT INTO usuarios (nome, email, senha_hash, tipo_usuario, situacao, 
                                cpf, telefone, departamento_id, cargo, capacidade_tarefas)
            VALUES (%s, %s, %s, %s, 'ATIVO', %s, %s, %s, %s, %s)
        """
        return execute_query(query, (nome, email, senha_hash, tipo_usuario, 
                                    cpf, telefone, departamento_id, cargo, capacidade_tarefas))
    
    @staticmethod
    def get_all():
        """
        Retorna todos os usuários.
        
        Returns:
            list: Lista de dicionários com dados dos usuários
        """
        query = """
            SELECT id, nome, email, tipo_usuario, situacao, cargo, departamento_id, criado_em
            FROM usuarios
            ORDER BY nome
        """
        return execute_query(query, fetch=True) or []
