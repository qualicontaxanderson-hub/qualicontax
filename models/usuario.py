"""Modelo de Usuário"""
from flask_login import UserMixin
from utils.db_helper import execute_query


class Usuario(UserMixin):
    """Classe Usuario para autenticação com Flask-Login"""
    
    def __init__(self, id, nome, email, senha_hash, tipo='usuario', ativo=True):
        self.id = id
        self.nome = nome
        self.email = email
        self.senha_hash = senha_hash
        self.tipo = tipo
        self.ativo = ativo
    
    def is_admin(self):
        """Verifica se o usuário é admin"""
        return self.tipo == 'admin'
    
    def is_active(self):
        """Verifica se o usuário está ativo (requerido pelo Flask-Login)"""
        return self.ativo
    
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
            SELECT id, nome, email, senha_hash, tipo, ativo
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
                tipo=result.get('tipo', 'usuario'),
                ativo=result.get('ativo', True)
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
            SELECT id, nome, email, senha_hash, tipo, ativo
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
                tipo=result.get('tipo', 'usuario'),
                ativo=result.get('ativo', True)
            )
        return None
    
    @staticmethod
    def create(nome, email, senha_hash, tipo='usuario'):
        """
        Cria novo usuário.
        
        Args:
            nome (str): Nome do usuário
            email (str): Email do usuário
            senha_hash (str): Hash da senha
            tipo (str): Tipo do usuário (admin/usuario)
            
        Returns:
            int: ID do usuário criado ou None
        """
        query = """
            INSERT INTO usuarios (nome, email, senha_hash, tipo, ativo, data_criacao)
            VALUES (%s, %s, %s, %s, TRUE, NOW())
        """
        return execute_query(query, (nome, email, senha_hash, tipo))
    
    @staticmethod
    def get_all():
        """
        Retorna todos os usuários.
        
        Returns:
            list: Lista de dicionários com dados dos usuários
        """
        query = """
            SELECT id, nome, email, tipo, ativo, data_criacao
            FROM usuarios
            ORDER BY nome
        """
        return execute_query(query, fetch=True) or []
