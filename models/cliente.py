"""Modelo de Cliente"""
from utils.db_helper import execute_query


class Cliente:
    """Classe Cliente para gestão de clientes"""
    
    def __init__(self, id, tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual=None,
                 inscricao_municipal=None, email=None, telefone=None, celular=None,
                 regime_tributario=None, porte_empresa=None, data_inicio_contrato=None,
                 situacao='Ativo', observacoes=None):
        self.id = id
        self.tipo_pessoa = tipo_pessoa
        self.nome_razao_social = nome_razao_social
        self.cpf_cnpj = cpf_cnpj
        self.inscricao_estadual = inscricao_estadual
        self.inscricao_municipal = inscricao_municipal
        self.email = email
        self.telefone = telefone
        self.celular = celular
        self.regime_tributario = regime_tributario
        self.porte_empresa = porte_empresa
        self.data_inicio_contrato = data_inicio_contrato
        self.situacao = situacao
        self.observacoes = observacoes
    
    @staticmethod
    def get_by_id(cliente_id):
        """
        Busca cliente por ID.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            dict: Dados do cliente ou None
        """
        query = """
            SELECT id, tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual,
                   inscricao_municipal, email, telefone, celular, regime_tributario,
                   porte_empresa, data_inicio_contrato, situacao, observacoes
            FROM clientes
            WHERE id = %s
        """
        return execute_query(query, (cliente_id,), fetch=True, fetch_one=True)
    
    @staticmethod
    def get_all(filters=None, page=1, per_page=10):
        """
        Retorna todos os clientes com paginação e filtros.
        
        Args:
            filters (dict): Filtros a serem aplicados
            page (int): Número da página
            per_page (int): Registros por página
            
        Returns:
            dict: Dicionário com 'clientes' e 'total'
        """
        filters = filters or {}
        conditions = []
        params = []
        
        if filters.get('tipo_pessoa'):
            conditions.append("tipo_pessoa = %s")
            params.append(filters['tipo_pessoa'])
        
        if filters.get('situacao'):
            conditions.append("situacao = %s")
            params.append(filters['situacao'])
        
        if filters.get('regime_tributario'):
            conditions.append("regime_tributario = %s")
            params.append(filters['regime_tributario'])
        
        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        
        count_query = f"SELECT COUNT(*) as total FROM clientes{where_clause}"
        total_result = execute_query(count_query, tuple(params), fetch=True, fetch_one=True)
        total = total_result['total'] if total_result else 0
        
        offset = (page - 1) * per_page
        params.extend([per_page, offset])
        
        query = f"""
            SELECT id, tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual,
                   inscricao_municipal, email, telefone, celular, regime_tributario,
                   porte_empresa, data_inicio_contrato, situacao, observacoes
            FROM clientes
            {where_clause}
            ORDER BY nome_razao_social
            LIMIT %s OFFSET %s
        """
        
        clientes = execute_query(query, tuple(params), fetch=True) or []
        
        return {
            'clientes': clientes,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        }
    
    @staticmethod
    def create(data):
        """
        Cria novo cliente.
        
        Args:
            data (dict): Dados do cliente
            
        Returns:
            int: ID do cliente criado ou None
        """
        query = """
            INSERT INTO clientes (
                tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual,
                inscricao_municipal, email, telefone, celular, regime_tributario,
                porte_empresa, data_inicio_contrato, situacao, observacoes, data_criacao
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """
        params = (
            data.get('tipo_pessoa'),
            data.get('nome_razao_social'),
            data.get('cpf_cnpj'),
            data.get('inscricao_estadual'),
            data.get('inscricao_municipal'),
            data.get('email'),
            data.get('telefone'),
            data.get('celular'),
            data.get('regime_tributario'),
            data.get('porte_empresa'),
            data.get('data_inicio_contrato'),
            data.get('situacao', 'Ativo'),
            data.get('observacoes')
        )
        return execute_query(query, params)
    
    @staticmethod
    def update(cliente_id, data):
        """
        Atualiza dados do cliente.
        
        Args:
            cliente_id (int): ID do cliente
            data (dict): Dados a serem atualizados
            
        Returns:
            int: ID do cliente ou None
        """
        query = """
            UPDATE clientes
            SET tipo_pessoa = %s, nome_razao_social = %s, cpf_cnpj = %s,
                inscricao_estadual = %s, inscricao_municipal = %s, email = %s,
                telefone = %s, celular = %s, regime_tributario = %s,
                porte_empresa = %s, data_inicio_contrato = %s, situacao = %s,
                observacoes = %s, data_atualizacao = NOW()
            WHERE id = %s
        """
        params = (
            data.get('tipo_pessoa'),
            data.get('nome_razao_social'),
            data.get('cpf_cnpj'),
            data.get('inscricao_estadual'),
            data.get('inscricao_municipal'),
            data.get('email'),
            data.get('telefone'),
            data.get('celular'),
            data.get('regime_tributario'),
            data.get('porte_empresa'),
            data.get('data_inicio_contrato'),
            data.get('situacao'),
            data.get('observacoes'),
            cliente_id
        )
        return execute_query(query, params)
    
    @staticmethod
    def delete(cliente_id):
        """
        Remove cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            int: ID do cliente ou None
        """
        query = "DELETE FROM clientes WHERE id = %s"
        return execute_query(query, (cliente_id,))
    
    @staticmethod
    def search(query_text):
        """
        Busca clientes por texto.
        
        Args:
            query_text (str): Texto de busca
            
        Returns:
            list: Lista de clientes encontrados
        """
        query = """
            SELECT id, tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual,
                   inscricao_municipal, email, telefone, celular, regime_tributario,
                   porte_empresa, data_inicio_contrato, situacao, observacoes
            FROM clientes
            WHERE nome_razao_social LIKE %s
               OR cpf_cnpj LIKE %s
               OR email LIKE %s
            ORDER BY nome_razao_social
            LIMIT 50
        """
        search_pattern = f"%{query_text}%"
        return execute_query(query, (search_pattern, search_pattern, search_pattern), fetch=True) or []
