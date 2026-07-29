"""Módulo de conexão com banco de dados Railway MySQL"""
import threading
import os
import atexit
from contextlib import contextmanager
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
            pool_name=f'qualicontax_pool_{os.getpid()}',
            pool_size=Config.DB_POOL_SIZE,
            pool_reset_session=False,
            # autocommit=True: cada SELECT deixa de abrir uma transação que ficava
            # VIVA na conexão devolvida ao pool (Sleep) segurando metadata lock em
            # clientes — o que travava o CREATE ... FK REFERENCES clientes por
            # ~29min. Com autocommit, não há transação órfã. É a correção-raiz.
            autocommit=True,
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            database=Config.DB_NAME,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            connection_timeout=Config.DB_CONNECT_TIMEOUT,
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci',
            # Fuso de Brasília em TODA sessão do pool: NOW()/CURRENT_TIMESTAMP
            # passam a gravar/ler em horário local, não UTC. Offset fixo '-03:00'
            # (não 'America/Sao_Paulo'): nomes de fuso exigem as tabelas
            # mysql.time_zone carregadas, o que a imagem Docker do MySQL normalmente
            # NÃO tem; e o Brasil não tem horário de verão desde 2019, então o
            # offset é fixo. Feito na conexão (não SET GLOBAL): sobrevive a restart
            # do container do MySQL.
            time_zone='-03:00',
        )
    return _pool


def close_pool():
    """Fecha de verdade todas as conexões do pool (chamado no shutdown).

    mysql.connector não expõe um close() público no pool, então drenamos a fila
    interna e fechamos cada conexão. Registrado via atexit: cobre a reciclagem de
    worker do gunicorn (--max-requests) e a troca de container (SIGTERM/graceful),
    liberando as conexões em vez de deixá-las em Sleep até o wait_timeout (8h).
    Best-effort e idempotente — nunca levanta.
    """
    global _pool
    pool, _pool = _pool, None
    if pool is None:
        return
    fechadas = 0
    try:
        fila = getattr(pool, '_cnx_queue', None)
        while fila is not None and not fila.empty():
            try:
                cnx = fila.get_nowait()
            except Exception:
                break
            try:
                cnx.close()  # conexão "crua" do pool: close() aqui fecha o socket
                fechadas += 1
            except Exception:
                pass
    except Exception:
        pass
    logger.info('Pool MySQL fechado no shutdown: %d conexão(ões) encerrada(s).', fechadas)


# Fecha o pool quando o processo (worker gunicorn ou script) encerra normalmente.
atexit.register(close_pool)


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


@contextmanager
def transacao():
    """Cursor dentro de UMA transação — para operações multi-statement.

    ``execute_query`` não serve para isso: o pool é ``autocommit=True`` e cada
    chamada pega uma conexão diferente, então um passo não pode ser desfeito
    quando o seguinte falha. Aqui a conexão é a mesma do início ao fim, commita
    no final do bloco e faz rollback em qualquer exceção.

    A transação é aberta com ``start_transaction()`` (START TRANSACTION), e NÃO
    mexendo no atributo ``autocommit``. O motivo é concreto: ``get_db_connection()``
    devolve um ``PooledMySQLConnection``, que delega leitura de atributos e
    chamadas de método para a conexão real mas NÃO delega atribuição — um
    ``cnx.autocommit = False`` gravaria o valor no objeto wrapper e a sessão
    MySQL continuaria em autocommit. O bloco pareceria transacional, cada
    statement commitaria sozinho e o ``rollback()`` não desfaria nada (validado
    contra o banco real antes desta correção).

    Como START TRANSACTION se encerra no commit/rollback, a sessão volta para o
    pool exatamente como veio — importante porque o pool usa
    ``pool_reset_session=False`` e não limpa nada entre usos.

    Uso:
        with transacao() as cur:
            cur.execute('INSERT ...', params)
            cur.execute('UPDATE ...', params)
    """
    connection = get_db_connection()
    if connection is None:
        raise RuntimeError('Não foi possível obter conexão com o banco de dados')

    cursor = None
    try:
        # Defensivo: se a conexão veio do pool com transação aberta, encerra
        # antes — START TRANSACTION sobre transação aberta levanta.
        try:
            if connection.in_transaction:
                connection.rollback()
        except Exception:
            pass

        connection.start_transaction()
        cursor = connection.cursor(dictionary=True)
        yield cursor
        connection.commit()
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            if cursor is not None:
                cursor.close()
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass


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
            # close() devolve a conexão ao pool com segurança; sem is_connected()
            # (COM_PING) por query — era um round-trip extra desnecessário.
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
            # close() devolve a conexão ao pool com segurança; sem is_connected()
            # (COM_PING) — era um round-trip extra desnecessário.
            connection.close()
        except Exception:
            pass
