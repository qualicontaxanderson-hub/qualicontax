"""Modelo de Documento"""
from utils.db_helper import execute_query


class Documento:
    """Classe Documento para gestão de documentos"""
    
    def __init__(self, id, cliente_id, processo_id, nome_arquivo, tipo,
                 caminho_arquivo, data_upload):
        self.id = id
        self.cliente_id = cliente_id
        self.processo_id = processo_id
        self.nome_arquivo = nome_arquivo
        self.tipo = tipo
        self.caminho_arquivo = caminho_arquivo
        self.data_upload = data_upload
    
    @staticmethod
    def get_by_id(documento_id):
        """
        Busca documento por ID.
        
        Args:
            documento_id (int): ID do documento
            
        Returns:
            dict: Dados do documento ou None
        """
        query = """
            SELECT id, cliente_id, processo_id, nome_arquivo, tipo,
                   caminho_arquivo, data_upload
            FROM documentos
            WHERE id = %s
        """
        return execute_query(query, (documento_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def get_by_cliente(cliente_id):
        """
        Busca todos os documentos de um cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de documentos do cliente
        """
        query = """
            SELECT id, cliente_id, processo_id, nome_arquivo, tipo,
                   caminho_arquivo, data_upload
            FROM documentos
            WHERE cliente_id = %s
            ORDER BY data_upload DESC
        """
        return execute_query(query, (cliente_id,), fetch=True) or []
    
    @staticmethod
    def create(data):
        """
        Cria novo documento.
        
        Args:
            data (dict): Dados do documento
            
        Returns:
            int: ID do documento criado ou None
        """
        query = """
            INSERT INTO documentos (
                cliente_id, processo_id, nome_arquivo, tipo,
                caminho_arquivo, data_upload
            )
            VALUES (%s, %s, %s, %s, %s, NOW())
        """
        params = (
            data.get('cliente_id'),
            data.get('processo_id'),
            data.get('nome_arquivo'),
            data.get('tipo'),
            data.get('caminho_arquivo')
        )
        return execute_query(query, params)
    
    @staticmethod
    def delete(documento_id):
        """
        Remove documento.
        
        Args:
            documento_id (int): ID do documento
            
        Returns:
            int: ID do documento ou None
        """
        query = "DELETE FROM documentos WHERE id = %s"
        return execute_query(query, (documento_id,))
