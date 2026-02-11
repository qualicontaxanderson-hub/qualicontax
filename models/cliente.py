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
            filters (dict): Filtros a serem aplicados (tipo_pessoa, situacao, regime_tributario, busca)
            page (int): Número da página
            per_page (int): Registros por página
            
        Returns:
            dict: Dicionário com 'clientes', 'total', 'page', 'per_page', 'total_pages'
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
        
        # Busca por nome, CPF/CNPJ ou email (usando parameterized queries para prevenir SQL injection)
        if filters.get('busca'):
            conditions.append("(nome_razao_social LIKE %s OR cpf_cnpj LIKE %s OR email LIKE %s)")
            # Sanitize special characters that have meaning in LIKE patterns
            search_term = filters['busca'].replace('%', '\\%').replace('_', '\\_')
            search_pattern = f"%{search_term}%"
            params.extend([search_pattern, search_pattern, search_pattern])
        
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
        
        clientes = execute_query(query, tuple(params), fetch=True)
        
        # Garantir que sempre retorna uma lista, mesmo que vazia
        if clientes is None:
            clientes = []
        
        return {
            'clientes': clientes,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page if total > 0 else 0
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
        # Converter nome para MAIÚSCULAS
        nome_razao_social = data.get('nome_razao_social', '').upper()
        
        # Handle regime_tributario based on tipo_pessoa
        # Valid ENUM values in DB: SIMPLES, LUCRO_PRESUMIDO, LUCRO_REAL, MEI (NOT 'OUTROS')
        tipo_pessoa = data.get('tipo_pessoa')
        if tipo_pessoa == 'PF':
            # PF doesn't have regime, use SIMPLES as default (most common)
            regime_tributario = 'SIMPLES'
        else:
            # PJ can have regime, use provided or default to SIMPLES
            regime_tributario = data.get('regime_tributario') or 'SIMPLES'
        
        # Converter strings vazias para None em campos opcionais (exceto regime_tributario)
        porte_empresa = data.get('porte_empresa') or None
        data_inicio_contrato = data.get('data_inicio_contrato') or None
        
        query = """
            INSERT INTO clientes (
                tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual,
                inscricao_municipal, email, telefone, celular, regime_tributario,
                porte_empresa, data_inicio_contrato, situacao, observacoes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            data.get('tipo_pessoa'),
            nome_razao_social,
            data.get('cpf_cnpj'),
            data.get('inscricao_estadual') or None,
            data.get('inscricao_municipal') or None,
            data.get('email') or None,
            data.get('telefone') or None,
            data.get('celular') or None,
            regime_tributario,
            porte_empresa,
            data_inicio_contrato,
            data.get('situacao', 'ATIVO'),
            data.get('observacoes') or None
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
        # Converter nome para MAIÚSCULAS
        nome_razao_social = data.get('nome_razao_social', '').upper()
        
        # Handle regime_tributario based on tipo_pessoa
        # Valid ENUM values in DB: SIMPLES, LUCRO_PRESUMIDO, LUCRO_REAL, MEI (NOT 'OUTROS')
        tipo_pessoa = data.get('tipo_pessoa')
        if tipo_pessoa == 'PF':
            # PF doesn't have regime, use SIMPLES as default (most common)
            regime_tributario = 'SIMPLES'
        else:
            # PJ can have regime, use provided or default to SIMPLES
            regime_tributario = data.get('regime_tributario') or 'SIMPLES'
        
        # Converter strings vazias para None em campos opcionais (exceto regime_tributario)
        porte_empresa = data.get('porte_empresa') or None
        data_inicio_contrato = data.get('data_inicio_contrato') or None
        
        query = """
            UPDATE clientes
            SET tipo_pessoa = %s, nome_razao_social = %s, cpf_cnpj = %s,
                inscricao_estadual = %s, inscricao_municipal = %s, email = %s,
                telefone = %s, celular = %s, regime_tributario = %s,
                porte_empresa = %s, data_inicio_contrato = %s,
                situacao = %s, observacoes = %s
            WHERE id = %s
        """
        params = (
            data.get('tipo_pessoa'),
            nome_razao_social,
            data.get('cpf_cnpj'),
            data.get('inscricao_estadual') or None,
            data.get('inscricao_municipal') or None,
            data.get('email') or None,
            data.get('telefone') or None,
            data.get('celular') or None,
            regime_tributario,
            porte_empresa,
            data_inicio_contrato,
            data.get('situacao'),
            data.get('observacoes') or None,
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
    
    @staticmethod
    def get_stats():
        """
        Retorna estatísticas sobre os clientes.
        
        Returns:
            dict: Dicionário com estatísticas
        """
        query = """
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN situacao = 'ATIVO' THEN 1 ELSE 0 END) as ativos,
                SUM(CASE WHEN situacao = 'INATIVO' THEN 1 ELSE 0 END) as inativos,
                SUM(CASE WHEN tipo_pessoa = 'PF' THEN 1 ELSE 0 END) as pf,
                SUM(CASE WHEN tipo_pessoa = 'PJ' THEN 1 ELSE 0 END) as pj
            FROM clientes
        """
        result = execute_query(query, fetch=True, fetch_one=True)
        return result if result else {
            'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0, 'pj': 0
        }
    
    @staticmethod
    def existe_cpf_cnpj(cpf_cnpj, cliente_id=None):
        """
        Verifica se CPF/CNPJ já está cadastrado.
        
        Args:
            cpf_cnpj (str): CPF ou CNPJ a verificar
            cliente_id (int, optional): ID do cliente para excluir da verificação (usado em edições)
            
        Returns:
            bool: True se já existe, False caso contrário
        """
        query = "SELECT id FROM clientes WHERE cpf_cnpj = %s"
        params = [cpf_cnpj]
        
        if cliente_id:
            query += " AND id != %s"
            params.append(cliente_id)
        
        result = execute_query(query, tuple(params), fetch=True, fetch_one=True)
        return result is not None
    
    @staticmethod
    def update_situacao(cliente_id, situacao):
        """
        Atualiza situação do cliente.
        
        Args:
            cliente_id (int): ID do cliente
            situacao (str): Nova situação (ATIVO, INATIVO, SUSPENSO, CANCELADO)
            
        Returns:
            bool: True se bem-sucedido
        """
        query = """
            UPDATE clientes
            SET situacao = %s
            WHERE id = %s
        """
        return execute_query(query, (situacao, cliente_id)) is not None
    
    @staticmethod
    def get_grupos(cliente_id):
        """
        Retorna grupos do cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de grupos
        """
        query = """
            SELECT g.id, g.nome, g.descricao, g.situacao
            FROM grupos_clientes g
            INNER JOIN cliente_grupo_relacao cgr ON g.id = cgr.grupo_id
            WHERE cgr.cliente_id = %s
            ORDER BY g.nome
        """
        return execute_query(query, (cliente_id,), fetch=True) or []
    
    @staticmethod
    def get_processos(cliente_id):
        """
        Retorna processos do cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de processos
        """
        # TODO: Implementar quando tabela processos estiver disponível
        # Query atual não funciona pois a coluna 'tipo' não existe na tabela processos
        # query = """
        #     SELECT id, numero_processo, status, data_abertura, data_conclusao, descricao
        #     FROM processos
        #     WHERE cliente_id = %s
        #     ORDER BY data_abertura DESC
        # """
        # return execute_query(query, (cliente_id,), fetch=True) or []
        return []
    
    @staticmethod
    def get_tarefas(cliente_id):
        """
        Retorna tarefas relacionadas ao cliente (através de processos).
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de tarefas
        """
        # TODO: Implementar quando tabela tarefas estiver disponível
        # Query atual não funciona pois a coluna 'prazo' não existe (provavelmente 'data_vencimento')
        # query = """
        #     SELECT t.id, t.titulo, t.descricao, t.data_vencimento, t.status, t.prioridade,
        #            p.numero_processo
        #     FROM tarefas t
        #     INNER JOIN processos p ON t.processo_id = p.id
        #     WHERE p.cliente_id = %s
        #     ORDER BY t.data_vencimento ASC
        # """
        # return execute_query(query, (cliente_id,), fetch=True) or []
        return []
    
    @staticmethod
    def get_obrigacoes(cliente_id):
        """
        Retorna obrigações do cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de obrigações
        """
        # TODO: Implementar quando tabela obrigacoes estiver disponível
        # Query tinha erro de sintaxe com alias 'to' (palavra reservada MySQL)
        # Deve usar alias diferente como 'tpo' ou 'tipo_ob'
        # query = """
        #     SELECT o.id, o.descricao, o.vencimento, o.valor, o.status, o.pago,
        #            tpo.nome as tipo_obrigacao
        #     FROM obrigacoes o
        #     LEFT JOIN tipos_obrigacoes tpo ON o.tipo_obrigacao_id = tpo.id
        #     WHERE o.cliente_id = %s
        #     ORDER BY o.vencimento ASC
        # """
        # return execute_query(query, (cliente_id,), fetch=True) or []
        return []

