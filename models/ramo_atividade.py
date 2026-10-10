"""Modelo de Ramo de Atividade"""
import time
import threading
from utils.db_helper import execute_query
from utils.acesso import filtrar_lista

# Segmento: o nível acima do ramo (10/10/2026, pedido do Anderson). Todo ramo
# de empresa pertence a um; a empresa é "de Comércio" porque um ramo dela é —
# ninguém marca "Comércio" à parte. Ramo de pessoa física não tem segmento.
SEGMENTOS = [('COMERCIO', 'Comércio'), ('SERVICOS', 'Prestação de Serviços'),
             ('LOCACAO', 'Locação'), ('INDUSTRIA', 'Indústria')]
SEGMENTO_NOME = dict(SEGMENTOS)


def segmento_do_cnae(cnae):
    """Sugestão de segmento pelo CNAE principal da Receita (só sugestão: o
    usuário confere). Agro (01-03) e holding (64) ficam sem sugestão."""
    d = ''.join(ch for ch in str(cnae or '') if ch.isdigit())[:2]
    if len(d) < 2:
        return None
    n = int(d)
    if n in (45, 46, 47, 56):          # 56 = restaurante: Comércio, por decisão do Anderson
        return 'COMERCIO'
    if n in (68, 77):                  # aluguel de imóveis / de bens móveis
        return 'LOCACAO'
    if 5 <= n <= 33:
        return 'INDUSTRIA'
    if n <= 3 or n == 64:
        return None
    return 'SERVICOS'


_CACHE_TTL_SECONDS = 60
_cache_ativos = None
_cache_ativos_ts = 0.0
_cache_lock = threading.Lock()


def _invalidate_cache():
    global _cache_ativos, _cache_ativos_ts
    with _cache_lock:
        _cache_ativos = None
        _cache_ativos_ts = 0.0


class RamoAtividade:
    """Classe para gestão de ramos de atividade dos clientes"""
    
    @staticmethod
    def get_all(situacao=None):
        """
        Retorna todos os ramos de atividade.
        
        Args:
            situacao (str, optional): Filtrar por situação (ATIVO, INATIVO)
            
        Returns:
            list: Lista de ramos de atividade
        """
        global _cache_ativos, _cache_ativos_ts

        query = """
            SELECT id, nome, tipo_pessoa, segmento, descricao, situacao
            FROM ramos_atividade
        """
        params = []
        if situacao:
            query += " WHERE situacao = %s"
            params.append(situacao)
        query += " ORDER BY nome"

        if situacao == 'ATIVO':
            with _cache_lock:
                if _cache_ativos is not None and (time.time() - _cache_ativos_ts) < _CACHE_TTL_SECONDS:
                    return _cache_ativos
                result = execute_query(query, tuple(params), fetch=True) or []
                _cache_ativos = result
                _cache_ativos_ts = time.time()
                return result

        return execute_query(query, tuple(params) if params else None, fetch=True) or []
    
    @staticmethod
    def get_by_id(ramo_id):
        """
        Busca ramo de atividade por ID.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            
        Returns:
            dict: Dados do ramo ou None
        """
        query = """
            SELECT id, nome, tipo_pessoa, segmento, descricao, situacao
            FROM ramos_atividade
            WHERE id = %s
        """
        return execute_query(query, (ramo_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def create(nome, descricao=None, situacao='ATIVO', tipo_pessoa='PJ', segmento=None):
        """
        Cria novo ramo de atividade.
        
        Args:
            nome (str): Nome do ramo de atividade
            descricao (str, optional): Descrição
            situacao (str, optional): Situação. Defaults to 'ATIVO'
            
        Returns:
            int: ID do ramo criado ou None
        """
        query = """
            INSERT INTO ramos_atividade (nome, tipo_pessoa, segmento, descricao, situacao)
            VALUES (%s, %s, %s, %s, %s)
        """
        segmento = None if tipo_pessoa == 'PF' else segmento
        result = execute_query(query, (nome, tipo_pessoa, segmento, descricao, situacao))
        if result is not None:
            _invalidate_cache()
        return result
    
    @staticmethod
    def update(ramo_id, nome, descricao=None, situacao='ATIVO', tipo_pessoa=None, segmento=None):
        """
        Atualiza dados do ramo de atividade.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            nome (str): Nome do ramo
            descricao (str, optional): Descrição
            situacao (str, optional): Situação
            
        Returns:
            int: Número de linhas afetadas ou None
        """
        query = """
            UPDATE ramos_atividade
            SET nome = %s, tipo_pessoa = COALESCE(%s, tipo_pessoa),
                segmento = IF(COALESCE(%s, tipo_pessoa) = 'PF', NULL, COALESCE(%s, segmento)),
                descricao = %s, situacao = %s
            WHERE id = %s
        """
        result = execute_query(query, (nome, tipo_pessoa, tipo_pessoa, segmento, descricao, situacao, ramo_id),
                               fetch=False)
        if result is not None:
            _invalidate_cache()
        return result
    
    @staticmethod
    def delete(ramo_id):
        """
        Remove ramo de atividade.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            
        Returns:
            int: Número de linhas afetadas ou None
        """
        query = """
            DELETE FROM ramos_atividade
            WHERE id = %s
        """
        result = execute_query(query, (ramo_id,), fetch=False)
        if result is not None:
            _invalidate_cache()
        return result
    
    @staticmethod
    def add_cliente(ramo_id, cliente_id):
        """
        Adiciona cliente ao ramo de atividade.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            cliente_id (int): ID do cliente
            
        Returns:
            int: ID da relação criada ou None
        """
        query = """
            INSERT INTO cliente_ramo_atividade_relacao (cliente_id, ramo_atividade_id)
            VALUES (%s, %s)
        """
        try:
            return execute_query(query, (cliente_id, ramo_id))
        except:
            # Pode falhar se já existir a relação (UNIQUE constraint)
            return None
    
    @staticmethod
    def remove_cliente(ramo_id, cliente_id):
        """
        Remove cliente do ramo de atividade.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            cliente_id (int): ID do cliente
            
        Returns:
            int: Número de linhas afetadas ou None
        """
        query = """
            DELETE FROM cliente_ramo_atividade_relacao
            WHERE cliente_id = %s AND ramo_atividade_id = %s
        """
        return execute_query(query, (cliente_id, ramo_id), fetch=False)
    
    @staticmethod
    def get_clientes(ramo_id):
        """
        Retorna clientes do ramo de atividade.
        
        Args:
            ramo_id (int): ID do ramo de atividade
            
        Returns:
            list: Lista de clientes
        """
        query = """
            SELECT c.id, c.numero_cliente, c.tipo_pessoa, c.nome_razao_social, c.cpf_cnpj, 
                   c.email, c.telefone, c.celular, c.situacao
            FROM clientes c
            INNER JOIN cliente_ramo_atividade_relacao crar ON c.id = crar.cliente_id
            WHERE crar.ramo_atividade_id = %s
            ORDER BY c.nome_razao_social
        """
        # Empresa reservada não aparece para quem não a vê.
        return filtrar_lista(execute_query(query, (ramo_id,), fetch=True) or [])
    
    @staticmethod
    def get_by_cliente(cliente_id):
        """
        Retorna ramos de atividade do cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de ramos de atividade
        """
        query = """
            SELECT ra.id, ra.nome, ra.tipo_pessoa, ra.segmento, ra.descricao, ra.situacao
            FROM ramos_atividade ra
            INNER JOIN cliente_ramo_atividade_relacao crar ON ra.id = crar.ramo_atividade_id
            WHERE crar.cliente_id = %s
            ORDER BY ra.nome
        """
        return execute_query(query, (cliente_id,), fetch=True) or []
