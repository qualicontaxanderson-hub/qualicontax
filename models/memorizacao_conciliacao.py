"""Modelo de Memorização de Conciliação"""
from utils.db_helper import execute_query


class MemorizacaoConciliacao:
    """Classe para gestão de memorizações de conciliação"""
    
    @staticmethod
    def get_all(tipo=None, grupo_id=None, cliente_id=None, ativo=True):
        """
        Retorna todas as memorizações.
        
        Args:
            tipo (str, optional): Filtrar por tipo (GRUPO, INDIVIDUAL)
            grupo_id (int, optional): Filtrar por grupo
            cliente_id (int, optional): Filtrar por cliente
            ativo (bool, optional): Filtrar por ativo. Defaults to True
            
        Returns:
            list: Lista de memorizações
        """
        query = """
            SELECT 
                m.id,
                m.tipo,
                m.grupo_id,
                m.cliente_id,
                m.palavra_chave,
                m.categoria_contabil,
                m.conta_contabil,
                m.historico_padrao,
                m.ativo,
                m.criado_em,
                g.nome AS grupo_nome,
                c.nome_razao_social AS cliente_nome
            FROM memorizacoes_conciliacao m
            LEFT JOIN grupos_clientes g ON m.grupo_id = g.id
            LEFT JOIN clientes c ON m.cliente_id = c.id
            WHERE 1=1
        """
        params = []
        
        if tipo:
            query += " AND m.tipo = %s"
            params.append(tipo)
        
        if grupo_id:
            query += " AND m.grupo_id = %s"
            params.append(grupo_id)
        
        if cliente_id:
            query += " AND m.cliente_id = %s"
            params.append(cliente_id)
        
        if ativo is not None:
            query += " AND m.ativo = %s"
            params.append(ativo)
        
        query += " ORDER BY m.palavra_chave"
        
        return execute_query(query, tuple(params) if params else None, fetch=True) or []
    
    @staticmethod
    def get_by_id(memorizacao_id):
        """
        Busca memorização por ID.
        
        Args:
            memorizacao_id (int): ID da memorização
            
        Returns:
            dict: Dados da memorização ou None
        """
        query = """
            SELECT 
                m.id,
                m.tipo,
                m.grupo_id,
                m.cliente_id,
                m.palavra_chave,
                m.categoria_contabil,
                m.conta_contabil,
                m.historico_padrao,
                m.ativo,
                m.criado_em
            FROM memorizacoes_conciliacao m
            WHERE m.id = %s
        """
        return execute_query(query, (memorizacao_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def create(tipo, palavra_chave, categoria_contabil, conta_contabil, 
               historico_padrao, grupo_id=None, cliente_id=None, ativo=True):
        """
        Cria nova memorização.
        
        Args:
            tipo (str): Tipo (GRUPO ou INDIVIDUAL)
            palavra_chave (str): Palavra-chave para busca
            categoria_contabil (str): Categoria contábil
            conta_contabil (str): Conta contábil
            historico_padrao (str): Histórico padrão
            grupo_id (int, optional): ID do grupo (se tipo GRUPO)
            cliente_id (int, optional): ID do cliente (se tipo INDIVIDUAL)
            ativo (bool, optional): Se está ativo. Defaults to True
            
        Returns:
            int: ID da memorização criada ou None
        """
        query = """
            INSERT INTO memorizacoes_conciliacao 
            (tipo, grupo_id, cliente_id, palavra_chave, categoria_contabil,
             conta_contabil, historico_padrao, ativo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        return execute_query(query, (
            tipo, grupo_id, cliente_id, palavra_chave, categoria_contabil,
            conta_contabil, historico_padrao, ativo
        ))
    
    @staticmethod
    def update(memorizacao_id, palavra_chave, categoria_contabil, conta_contabil,
               historico_padrao, ativo=True):
        """
        Atualiza memorização.
        
        Args:
            memorizacao_id (int): ID da memorização
            palavra_chave (str): Palavra-chave para busca
            categoria_contabil (str): Categoria contábil
            conta_contabil (str): Conta contábil
            historico_padrao (str): Histórico padrão
            ativo (bool, optional): Se está ativo
            
        Returns:
            int: ID da memorização ou None
        """
        query = """
            UPDATE memorizacoes_conciliacao
            SET palavra_chave = %s,
                categoria_contabil = %s,
                conta_contabil = %s,
                historico_padrao = %s,
                ativo = %s
            WHERE id = %s
        """
        return execute_query(query, (
            palavra_chave, categoria_contabil, conta_contabil,
            historico_padrao, ativo, memorizacao_id
        ))
    
    @staticmethod
    def delete(memorizacao_id):
        """
        Remove memorização.
        
        Args:
            memorizacao_id (int): ID da memorização
            
        Returns:
            int: Resultado da operação
        """
        query = "DELETE FROM memorizacoes_conciliacao WHERE id = %s"
        return execute_query(query, (memorizacao_id,))
    
    @staticmethod
    def buscar_por_descricao(descricao, cliente_id, grupo_id=None, preferir_grupo=False):
        """
        Busca memorização que corresponde à descrição.
        
        Args:
            descricao (str): Descrição da transação
            cliente_id (int): ID do cliente
            grupo_id (int, optional): ID do grupo do cliente
            preferir_grupo (bool, optional): Se deve preferir memorizações do grupo
            
        Returns:
            dict: Memorização encontrada ou None
        """
        descricao_upper = descricao.upper()
        
        # Se preferir grupo e tem grupo, busca primeiro no grupo
        if preferir_grupo and grupo_id:
            query = """
                SELECT *
                FROM memorizacoes_conciliacao
                WHERE tipo = 'GRUPO'
                AND grupo_id = %s
                AND ativo = TRUE
                AND UPPER(%s) LIKE CONCAT('%', UPPER(palavra_chave), '%')
                ORDER BY LENGTH(palavra_chave) DESC
                LIMIT 1
            """
            resultado = execute_query(query, (grupo_id, descricao_upper), fetch=True, fetch_one=True)
            if resultado:
                return resultado
        
        # Busca nas memorizações individuais do cliente
        query = """
            SELECT *
            FROM memorizacoes_conciliacao
            WHERE tipo = 'INDIVIDUAL'
            AND cliente_id = %s
            AND ativo = TRUE
            AND UPPER(%s) LIKE CONCAT('%', UPPER(palavra_chave), '%')
            ORDER BY LENGTH(palavra_chave) DESC
            LIMIT 1
        """
        resultado = execute_query(query, (cliente_id, descricao_upper), fetch=True, fetch_one=True)
        if resultado:
            return resultado
        
        # Se não encontrou individual e tem grupo, busca no grupo
        if grupo_id and not preferir_grupo:
            query = """
                SELECT *
                FROM memorizacoes_conciliacao
                WHERE tipo = 'GRUPO'
                AND grupo_id = %s
                AND ativo = TRUE
                AND UPPER(%s) LIKE CONCAT('%', UPPER(palavra_chave), '%')
                ORDER BY LENGTH(palavra_chave) DESC
                LIMIT 1
            """
            return execute_query(query, (grupo_id, descricao_upper), fetch=True, fetch_one=True)
        
        return None
