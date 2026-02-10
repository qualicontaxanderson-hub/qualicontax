"""Módulo de conexão com banco de dados Railway MySQL"""
import mysql.connector
from mysql.connector import Error
from config import Config
import logging

# Configurar logging
logger = logging.getLogger(__name__)


def get_db_connection():
    """
    Cria e retorna uma conexão com o banco de dados MySQL.
    
    Returns:
        connection: Objeto de conexão MySQL ou None em caso de erro
    """
    try:
        connection = mysql.connector.connect(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            database=Config.DB_NAME,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        
        if connection.is_connected():
            return connection
            
    except Error as e:
        logger.error(f"Erro ao conectar ao MySQL: {e}")
        print(f"Erro ao conectar ao MySQL: {e}")
        return None


def execute_query(query, params=None, fetch=False, fetch_one=False):
    """
    Executa uma query no banco de dados.
    
    Args:
        query (str): Query SQL a ser executada
        params (tuple): Parâmetros da query
        fetch (bool): Se True, retorna os resultados (SELECT)
        fetch_one (bool): Se True, retorna apenas um registro
        
    Returns:
        list/dict/int/None: 
            - Para SELECT: lista de dicts ou dict único
            - Para INSERT: lastrowid (ID do registro inserido)
            - Para UPDATE/DELETE: número de linhas afetadas
            - None em caso de erro
    """
    connection = get_db_connection()
    if not connection:
        logger.error("Não foi possível obter conexão com o banco de dados")
        return None
        
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(query, params or ())
        
        if fetch:
            result = cursor.fetchone() if fetch_one else cursor.fetchall()
            return result
        else:
            connection.commit()
            # Para INSERT, retorna lastrowid (ID do novo registro)
            # Para UPDATE/DELETE, retorna rowcount (número de linhas afetadas)
            # Se lastrowid > 0, é um INSERT, retorna o ID
            # Se lastrowid == 0, é UPDATE/DELETE, retorna rowcount (pode ser 0 se nada mudou)
            if cursor.lastrowid > 0:
                return cursor.lastrowid
            else:
                # Para UPDATE/DELETE, retorna rowcount ou True se foi executado com sucesso
                return cursor.rowcount if cursor.rowcount >= 0 else True
            
    except Error as e:
        logger.error(f"Erro ao executar query: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Params: {params}")
        print(f"Erro ao executar query: {e}")
        print(f"Query: {query}")
        if params:
            print(f"Params: {params}")
        connection.rollback()
        return None
        
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()


def execute_many(query, data_list):
    """
    Executa múltiplas inserções de uma vez.
    
    Args:
        query (str): Query SQL preparada
        data_list (list): Lista de tuplas com dados
        
    Returns:
        bool: True se sucesso, False se erro
    """
    connection = get_db_connection()
    if not connection:
        return False
        
    try:
        cursor = connection.cursor()
        cursor.executemany(query, data_list)
        connection.commit()
        return True
        
    except Error as e:
        print(f"Erro ao executar múltiplas queries: {e}")
        connection.rollback()
        return False
        
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
