"""Modelo de Contato de Cliente"""
from utils.db_helper import execute_query


class ContatoCliente:
    """Classe para gestão de contatos de clientes"""
    
    @staticmethod
    def get_by_cliente(cliente_id):
        """
        Busca todos os contatos de um cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de contatos
        """
        query = """
            SELECT id, cliente_id, nome, cargo, email, telefone, celular,
                   departamento, principal, ativo
            FROM contatos_clientes
            WHERE cliente_id = %s
            ORDER BY principal DESC, nome ASC
        """
        return execute_query(query, (cliente_id,), fetch=True) or []
    
    @staticmethod
    def get_by_id(contato_id):
        """
        Busca contato por ID.
        
        Args:
            contato_id (int): ID do contato
            
        Returns:
            dict: Dados do contato ou None
        """
        query = """
            SELECT id, cliente_id, nome, cargo, email, telefone, celular,
                   departamento, principal, ativo
            FROM contatos_clientes
            WHERE id = %s
        """
        return execute_query(query, (contato_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def create(cliente_id, nome, cargo=None, email=None, telefone=None,
               celular=None, departamento=None, principal=False, ativo=True):
        """
        Cria novo contato.
        
        Args:
            cliente_id (int): ID do cliente
            nome (str): Nome do contato
            cargo (str, optional): Cargo
            email (str, optional): Email
            telefone (str, optional): Telefone
            celular (str, optional): Celular
            departamento (str, optional): Departamento
            principal (bool, optional): Se é contato principal. Defaults to False
            ativo (bool, optional): Se está ativo. Defaults to True
            
        Returns:
            int: ID do contato criado ou None
        """
        # Converter nome para MAIÚSCULAS
        nome = nome.upper() if nome else nome
        
        # Se for marcado como principal, desmarca os outros
        if principal:
            ContatoCliente.set_principal(cliente_id, None)
        
        query = """
            INSERT INTO contatos_clientes (
                cliente_id, nome, cargo, email, telefone, celular,
                departamento, principal, ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            cliente_id, nome, cargo, email, telefone, celular,
            departamento, principal, ativo
        )
        return execute_query(query, params)
    
    @staticmethod
    def update(contato_id, nome, cargo=None, email=None, telefone=None,
               celular=None, departamento=None, principal=False, ativo=True):
        """
        Atualiza dados do contato.
        
        Args:
            contato_id (int): ID do contato
            nome (str): Nome do contato
            cargo (str, optional): Cargo
            email (str, optional): Email
            telefone (str, optional): Telefone
            celular (str, optional): Celular
            departamento (str, optional): Departamento
            principal (bool, optional): Se é contato principal
            ativo (bool, optional): Se está ativo
            
        Returns:
            int: ID do contato ou None
        """
        # Converter nome para MAIÚSCULAS
        nome = nome.upper() if nome else nome
        
        # Se for marcado como principal, desmarca os outros
        if principal:
            contato = ContatoCliente.get_by_id(contato_id)
            if contato:
                ContatoCliente.set_principal(contato['cliente_id'], contato_id)
        
        query = """
            UPDATE contatos_clientes
            SET nome = %s, cargo = %s, email = %s, telefone = %s,
                celular = %s, departamento = %s, principal = %s, ativo = %s
            WHERE id = %s
        """
        params = (
            nome, cargo, email, telefone, celular,
            departamento, principal, ativo, contato_id
        )
        return execute_query(query, params)
    
    @staticmethod
    def delete(contato_id):
        """
        Remove contato.
        
        Args:
            contato_id (int): ID do contato
            
        Returns:
            int: ID do contato ou None
        """
        query = "DELETE FROM contatos_clientes WHERE id = %s"
        return execute_query(query, (contato_id,))
    
    @staticmethod
    def set_principal(cliente_id, contato_id):
        """
        Define um contato como principal e desmarca os outros.
        
        Args:
            cliente_id (int): ID do cliente
            contato_id (int): ID do contato a ser marcado como principal (None para desmarcar todos)
            
        Returns:
            bool: True se bem-sucedido
        """
        # Primeiro, desmarca todos os contatos do cliente
        query = """
            UPDATE contatos_clientes
            SET principal = FALSE
            WHERE cliente_id = %s
        """
        execute_query(query, (cliente_id,))
        
        # Depois, marca o contato especificado como principal
        if contato_id:
            query = """
                UPDATE contatos_clientes
                SET principal = TRUE
                WHERE id = %s AND cliente_id = %s
            """
            return execute_query(query, (contato_id, cliente_id)) is not None
        
        return True
