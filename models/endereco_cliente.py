"""Modelo de Endereço de Cliente"""
from utils.db_helper import execute_query


class EnderecoCliente:
    """Classe para gestão de endereços de clientes"""
    
    @staticmethod
    def get_by_cliente(cliente_id):
        """
        Busca todos os endereços de um cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de endereços
        """
        query = """
            SELECT id, cliente_id, tipo, cep, logradouro, numero, complemento,
                   bairro, cidade, estado, pais, principal
            FROM enderecos_clientes
            WHERE cliente_id = %s
            ORDER BY principal DESC, id DESC
        """
        return execute_query(query, (cliente_id,), fetch=True) or []
    
    @staticmethod
    def get_by_id(endereco_id):
        """
        Busca endereço por ID.
        
        Args:
            endereco_id (int): ID do endereço
            
        Returns:
            dict: Dados do endereço ou None
        """
        query = """
            SELECT id, cliente_id, tipo, cep, logradouro, numero, complemento,
                   bairro, cidade, estado, pais, principal
            FROM enderecos_clientes
            WHERE id = %s
        """
        return execute_query(query, (endereco_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def create(cliente_id, tipo, cep, logradouro, numero, complemento=None,
               bairro=None, cidade=None, estado=None, pais='Brasil', principal=False):
        """
        Cria novo endereço.
        
        Args:
            cliente_id (int): ID do cliente
            tipo (str): Tipo de endereço (COMERCIAL, RESIDENCIAL, CORRESPONDENCIA)
            cep (str): CEP
            logradouro (str): Logradouro
            numero (str): Número
            complemento (str, optional): Complemento
            bairro (str, optional): Bairro
            cidade (str, optional): Cidade
            estado (str, optional): Estado
            pais (str, optional): País. Defaults to 'Brasil'
            principal (bool, optional): Se é endereço principal. Defaults to False
            
        Returns:
            int: ID do endereço criado ou None
        """
        # Se for marcado como principal, desmarca os outros
        if principal:
            EnderecoCliente.set_principal(cliente_id, None)
        
        query = """
            INSERT INTO enderecos_clientes (
                cliente_id, tipo, cep, logradouro, numero, complemento,
                bairro, cidade, estado, pais, principal
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            cliente_id, tipo, cep, logradouro, numero, complemento,
            bairro, cidade, estado, pais, principal
        )
        return execute_query(query, params)
    
    @staticmethod
    def update(endereco_id, tipo, cep, logradouro, numero, complemento=None,
               bairro=None, cidade=None, estado=None, pais='Brasil', principal=False):
        """
        Atualiza dados do endereço.
        
        Args:
            endereco_id (int): ID do endereço
            tipo (str): Tipo de endereço
            cep (str): CEP
            logradouro (str): Logradouro
            numero (str): Número
            complemento (str, optional): Complemento
            bairro (str, optional): Bairro
            cidade (str, optional): Cidade
            estado (str, optional): Estado
            pais (str, optional): País
            principal (bool, optional): Se é endereço principal
            
        Returns:
            int: ID do endereço ou None
        """
        # Se for marcado como principal, desmarca os outros
        if principal:
            endereco = EnderecoCliente.get_by_id(endereco_id)
            if endereco:
                EnderecoCliente.set_principal(endereco['cliente_id'], endereco_id)
        
        query = """
            UPDATE enderecos_clientes
            SET tipo = %s, cep = %s, logradouro = %s, numero = %s,
                complemento = %s, bairro = %s, cidade = %s, estado = %s,
                pais = %s, principal = %s
            WHERE id = %s
        """
        params = (
            tipo, cep, logradouro, numero, complemento,
            bairro, cidade, estado, pais, principal, endereco_id
        )
        return execute_query(query, params)
    
    @staticmethod
    def delete(endereco_id):
        """
        Remove endereço.
        
        Args:
            endereco_id (int): ID do endereço
            
        Returns:
            int: ID do endereço ou None
        """
        query = "DELETE FROM enderecos_clientes WHERE id = %s"
        return execute_query(query, (endereco_id,))
    
    @staticmethod
    def set_principal(cliente_id, endereco_id):
        """
        Define um endereço como principal e desmarca os outros.
        
        Args:
            cliente_id (int): ID do cliente
            endereco_id (int): ID do endereço a ser marcado como principal (None para desmarcar todos)
            
        Returns:
            bool: True se bem-sucedido
        """
        # Primeiro, desmarca todos os endereços do cliente
        query = """
            UPDATE enderecos_clientes
            SET principal = FALSE
            WHERE cliente_id = %s
        """
        execute_query(query, (cliente_id,))
        
        # Depois, marca o endereço especificado como principal
        if endereco_id:
            query = """
                UPDATE enderecos_clientes
                SET principal = TRUE
                WHERE id = %s AND cliente_id = %s
            """
            return execute_query(query, (endereco_id, cliente_id)) is not None
        
        return True
