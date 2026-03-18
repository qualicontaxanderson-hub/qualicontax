"""Modelo de Conciliação Bancária"""
from utils.db_helper import execute_query
from datetime import datetime


class ConciliacaoBancaria:
    """Classe para gestão de conciliações bancárias"""
    
    @staticmethod
    def get_all(cliente_id=None, grupo_id=None, status=None):
        """
        Retorna todas as conciliações bancárias.
        
        Args:
            cliente_id (int, optional): Filtrar por cliente
            grupo_id (int, optional): Filtrar por grupo
            status (str, optional): Filtrar por status
            
        Returns:
            list: Lista de conciliações
        """
        query = """
            SELECT 
                cb.id,
                cb.cliente_id,
                cb.grupo_id,
                cb.arquivo_ofx,
                cb.data_importacao,
                cb.periodo_inicial,
                cb.periodo_final,
                cb.saldo_inicial,
                cb.saldo_final,
                cb.status,
                cb.criado_em,
                c.nome_razao_social AS cliente_nome,
                g.nome AS grupo_nome
            FROM conciliacoes_bancarias cb
            LEFT JOIN clientes c ON cb.cliente_id = c.id
            LEFT JOIN grupos_clientes g ON cb.grupo_id = g.id
            WHERE 1=1
        """
        params = []
        
        if cliente_id:
            query += " AND cb.cliente_id = %s"
            params.append(cliente_id)
        
        if grupo_id:
            query += " AND cb.grupo_id = %s"
            params.append(grupo_id)
        
        if status:
            query += " AND cb.status = %s"
            params.append(status)
        
        query += " ORDER BY cb.data_importacao DESC"
        
        return execute_query(query, tuple(params) if params else None, fetch=True) or []
    
    @staticmethod
    def get_by_id(conciliacao_id):
        """
        Busca conciliação por ID.
        
        Args:
            conciliacao_id (int): ID da conciliação
            
        Returns:
            dict: Dados da conciliação ou None
        """
        query = """
            SELECT 
                cb.id,
                cb.cliente_id,
                cb.grupo_id,
                cb.arquivo_ofx,
                cb.data_importacao,
                cb.periodo_inicial,
                cb.periodo_final,
                cb.saldo_inicial,
                cb.saldo_final,
                cb.status,
                cb.criado_em,
                c.nome_razao_social AS cliente_nome,
                g.nome AS grupo_nome
            FROM conciliacoes_bancarias cb
            LEFT JOIN clientes c ON cb.cliente_id = c.id
            LEFT JOIN grupos_clientes g ON cb.grupo_id = g.id
            WHERE cb.id = %s
        """
        return execute_query(query, (conciliacao_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def create(cliente_id, grupo_id=None, arquivo_ofx=None, periodo_inicial=None,
               periodo_final=None, saldo_inicial=0.0, saldo_final=0.0, status='PENDENTE'):
        """
        Cria nova conciliação bancária.
        
        Args:
            cliente_id (int): ID do cliente
            grupo_id (int, optional): ID do grupo
            arquivo_ofx (str, optional): Nome do arquivo OFX
            periodo_inicial (date, optional): Data inicial
            periodo_final (date, optional): Data final
            saldo_inicial (float, optional): Saldo inicial
            saldo_final (float, optional): Saldo final
            status (str, optional): Status. Defaults to 'PENDENTE'
            
        Returns:
            int: ID da conciliação criada ou None
        """
        query = """
            INSERT INTO conciliacoes_bancarias 
            (cliente_id, grupo_id, arquivo_ofx, periodo_inicial, periodo_final,
             saldo_inicial, saldo_final, status, data_importacao)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        data_importacao = datetime.now()
        return execute_query(query, (
            cliente_id, grupo_id, arquivo_ofx, periodo_inicial, periodo_final,
            saldo_inicial, saldo_final, status, data_importacao
        ))
    
    @staticmethod
    def update_status(conciliacao_id, status):
        """
        Atualiza status da conciliação.
        
        Args:
            conciliacao_id (int): ID da conciliação
            status (str): Novo status
            
        Returns:
            int: ID da conciliação ou None
        """
        query = """
            UPDATE conciliacoes_bancarias
            SET status = %s
            WHERE id = %s
        """
        return execute_query(query, (status, conciliacao_id))
    
    @staticmethod
    def delete(conciliacao_id):
        """
        Remove conciliação.
        
        Args:
            conciliacao_id (int): ID da conciliação
            
        Returns:
            int: Resultado da operação
        """
        query = "DELETE FROM conciliacoes_bancarias WHERE id = %s"
        return execute_query(query, (conciliacao_id,))
