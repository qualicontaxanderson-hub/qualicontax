"""Módulo de conexão com banco de dados Railway MySQL"""
import threading
import mysql.connector
from mysql.connector import Error, pooling
from config import Config
import logging

# Configurar logging
logger = logging.getLogger(__name__)

# Thread-local storage for the last DB error (surfaced in UI responses)
_last_db_error = threading.local()


def get_last_db_error() -> str | None:
    """Retorna a mensagem do último erro de banco ocorrido nesta thread."""
    return getattr(_last_db_error, 'message', None)


def _set_last_db_error(msg: str) -> None:
    _last_db_error.message = msg

# Pool de conexões reutilizadas entre requisições – elimina o overhead de
# abrir/fechar uma conexão TCP+autenticação MySQL a cada query.
_pool: pooling.MySQLConnectionPool | None = None


def _get_pool() -> pooling.MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name='qualicontax_pool',
            pool_size=10,
            pool_reset_session=True,
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            database=Config.DB_NAME,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci',
        )
    return _pool


def get_db_connection():
    """
    Retorna uma conexão do pool MySQL.
    Chamar .close() na conexão a devolve ao pool (não fecha de verdade).

    Returns:
        connection: Objeto de conexão MySQL ou None em caso de erro
    """
    try:
        return _get_pool().get_connection()
    except Error as e:
        logger.error(f"Erro ao obter conexão do pool MySQL: {e}")
        print(f"Erro ao obter conexão do pool MySQL: {e}")
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
        
    cursor = None
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
                # Para UPDATE/DELETE, sempre retorna True para indicar sucesso
                # Mesmo se rowcount for 0 (nenhuma linha afetada), o UPDATE foi executado sem erro
                return True
            
    except Error as e:
        _set_last_db_error(str(e))
        logger.error(f"Erro ao executar query: {e}")
        logger.error(f"Query: {query}")
        logger.error(f"Params: {params}")
        print(f"Erro ao executar query: {e}")
        print(f"Query: {query}")
        if params:
            print(f"Params: {params}")
        try:
            connection.rollback()
        except Exception:
            pass
        return None
        
    finally:
        try:
            if cursor is not None:
                cursor.close()
            if connection.is_connected():
                connection.close()
        except Exception:
            pass


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
        
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.executemany(query, data_list)
        connection.commit()
        return True
        
    except Error as e:
        print(f"Erro ao executar múltiplas queries: {e}")
        try:
            connection.rollback()
        except Exception:
            pass
        return False
        
    finally:
        try:
            if cursor is not None:
                cursor.close()
            if connection.is_connected():
                connection.close()
        except Exception:
            pass
