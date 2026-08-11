"""Modelo de Cliente"""
from utils.db_helper import execute_query


class Cliente:
    """Classe Cliente para gestão de clientes"""
    
    def __init__(self, id, tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual=None,
                 inscricao_municipal=None, email=None, telefone=None, celular=None,
                 regime_tributario=None, porte_empresa=None, cnae_fiscal=None, cnae_fiscal_descricao=None,
                 data_inicio_atividade=None, data_inicio_contrato=None, situacao='Ativo', observacoes=None):
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
        self.cnae_fiscal = cnae_fiscal
        self.cnae_fiscal_descricao = cnae_fiscal_descricao
        self.data_inicio_atividade = data_inicio_atividade
        self.data_inicio_contrato = data_inicio_contrato
        self.situacao = situacao
        self.observacoes = observacoes

    @staticmethod
    def index_cpf_cnpj():
        """Mapa {cpf_cnpj_só_dígitos: id} de todos os clientes.

        Usado pela captura SEFAZ para resolver o EMITENTE de uma nota contra a base
        de clientes UMA vez por rodada (evita 1 query por nota). Ignora CPF/CNPJ
        vazios ou com menos de 11 dígitos.
        """
        rows = execute_query(
            "SELECT id, "
            "  REPLACE(REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-',''),' ','') AS d "
            "FROM clientes WHERE cpf_cnpj IS NOT NULL AND cpf_cnpj <> ''",
            fetch=True,
        ) or []
        return {r['d']: r['id'] for r in rows if r.get('d') and len(r['d']) >= 11}

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
            SELECT id, numero_cliente, tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual,
                   inscricao_municipal, email, telefone, celular, regime_tributario,
                   porte_empresa, cnae_fiscal, cnae_fiscal_descricao, data_inicio_atividade, data_inicio_contrato,
                   situacao, observacoes, is_contador, aberta_pela_casa
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
        need_ramo_join = False  # only True when filtering by ramo de atividade

        if filters.get('tipo_pessoa'):
            conditions.append("c.tipo_pessoa = %s")
            params.append(filters['tipo_pessoa'])

        if filters.get('situacao'):
            conditions.append("c.situacao = %s")
            params.append(filters['situacao'])

        if filters.get('regime_tributario'):
            conditions.append("c.regime_tributario = %s")
            params.append(filters['regime_tributario'])

        if filters.get('grupo_id'):
            conditions.append("cgr.grupo_id = %s")
            params.append(filters['grupo_id'])

        if filters.get('ramo_id'):
            need_ramo_join = True
            conditions.append("crar.ramo_atividade_id = %s")
            params.append(filters['ramo_id'])

        # Busca restrita ao campo escolhido pelo usuário
        busca = filters.get('busca', '').strip()
        busca_tipo = filters.get('busca_tipo', 'nome')  # 'nome' | 'numero' | 'ramo' | 'grupo' | 'cpf_cnpj' | 'email'
        if busca:
            search_term = busca.replace('%', '\\%').replace('_', '\\_')
            search_pattern = f"%{search_term}%"
            if busca_tipo == 'numero':
                conditions.append("c.numero_cliente LIKE %s")
                params.append(search_pattern)
            elif busca_tipo == 'ramo':
                need_ramo_join = True
                conditions.append("ra.nome LIKE %s")
                params.append(search_pattern)
            elif busca_tipo == 'grupo':
                conditions.append("g.nome LIKE %s")
                params.append(search_pattern)
            elif busca_tipo == 'cpf_cnpj':
                conditions.append("c.cpf_cnpj LIKE %s")
                params.append(search_pattern)
            elif busca_tipo == 'email':
                conditions.append("c.email LIKE %s")
                params.append(search_pattern)
            else:  # nome (default)
                conditions.append("c.nome_razao_social LIKE %s")
                params.append(search_pattern)

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

        # The count query needs the same JOINs used by active filters.
        count_ramo_join = """
                LEFT JOIN cliente_grupo_relacao cgr ON c.id = cgr.cliente_id
                LEFT JOIN grupos_clientes g ON cgr.grupo_id = g.id"""
        if need_ramo_join:
            count_ramo_join += """
                LEFT JOIN cliente_ramo_atividade_relacao crar ON c.id = crar.cliente_id
                LEFT JOIN ramos_atividade ra ON crar.ramo_atividade_id = ra.id"""

        # Single query that returns filtered count + global stats in one DB round-trip,
        # avoiding a separate get_stats() call from the route.
        # global_com_cert / global_cert_vencendo: estatísticas CADASTRAIS de
        # certificado para a fita de KPIs da lista. Contam CLIENTES distintos com
        # certificado ativo; "vencendo" usa a mesma janela do classificar_validade
        # (0..30 dias, CURDATE() já em horário de Brasília pelo pool). Aditivo:
        # não altera nenhuma contagem que já existia.
        count_query = f"""
            SELECT
                COUNT(DISTINCT c.id)                                                AS total,
                (SELECT COUNT(*) FROM clientes)                                     AS global_total,
                (SELECT COUNT(*) FROM clientes WHERE situacao = 'ATIVO')            AS global_ativos,
                (SELECT COUNT(*) FROM clientes WHERE situacao = 'INATIVO')          AS global_inativos,
                (SELECT COUNT(*) FROM clientes WHERE tipo_pessoa = 'PF')            AS global_pf,
                (SELECT COUNT(*) FROM clientes WHERE tipo_pessoa = 'PJ')            AS global_pj,
                (SELECT COUNT(DISTINCT cliente_id) FROM dfe_certificados
                   WHERE ativo = 1)                                                 AS global_com_cert,
                (SELECT COUNT(DISTINCT cliente_id) FROM dfe_certificados
                   WHERE ativo = 1 AND validade IS NOT NULL
                     AND validade >= CURDATE()
                     AND validade <= CURDATE() + INTERVAL 30 DAY)                   AS global_cert_vencendo
            FROM clientes c{count_ramo_join}{where_clause}"""
        count_result = execute_query(count_query, tuple(params), fetch=True, fetch_one=True)
        total = int(count_result['total']) if count_result else 0
        stats = {
            'total':          int(count_result.get('global_total',    0) or 0),
            'ativos':         int(count_result.get('global_ativos',   0) or 0),
            'inativos':       int(count_result.get('global_inativos', 0) or 0),
            'pf':             int(count_result.get('global_pf',       0) or 0),
            'pj':             int(count_result.get('global_pj',       0) or 0),
            'com_certificado':int(count_result.get('global_com_cert', 0) or 0),
            'cert_vencendo':  int(count_result.get('global_cert_vencendo', 0) or 0),
        } if count_result else {'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0,
                                'pj': 0, 'com_certificado': 0, 'cert_vencendo': 0}

        # Ordenação configurável: sort_by in ('nome', 'numero'), sort_dir in ('asc', 'desc')
        sort_by  = filters.get('sort_by',  'numero')
        sort_dir = filters.get('sort_dir', 'asc').lower()
        if sort_dir not in ('asc', 'desc'):
            sort_dir = 'asc'
        if sort_by == 'numero':
            numero_sort_expr = (
                "CASE "
                "WHEN c.numero_cliente REGEXP '^[0-9]+$' THEN CAST(c.numero_cliente AS UNSIGNED) "
                "ELSE NULL "
                "END"
            )
            # Numeric sort so 1 < 2 < 162 < 211 < 5000 (not lexicographic)
            order_clause = f"ORDER BY {numero_sort_expr} IS NULL, {numero_sort_expr} {sort_dir.upper()}, c.nome_razao_social ASC"
        else:
            order_clause = f"ORDER BY c.nome_razao_social {sort_dir.upper()}"

        offset = (page - 1) * per_page
        params.extend([per_page, offset])

        # GROUP BY c.id + GROUP_CONCAT ensures one row per client even when the
        # client has multiple ramos de atividade in the many-to-many relation.
        query = f"""
            SELECT c.id, c.numero_cliente, c.tipo_pessoa, c.nome_razao_social, c.cpf_cnpj, c.inscricao_estadual,
                   c.inscricao_municipal, c.email, c.telefone, c.celular, c.regime_tributario,
                   c.porte_empresa, c.cnae_fiscal, c.cnae_fiscal_descricao, c.data_inicio_atividade, c.data_inicio_contrato, c.situacao, c.observacoes,
                   cert.cert_validade,
                   MAX(ender.cidade) AS end_cidade, MAX(ender.estado) AS end_uf,
                   GROUP_CONCAT(DISTINCT ra.nome ORDER BY ra.nome SEPARATOR ', ') as ramo_atividade_nome,
                   GROUP_CONCAT(DISTINCT g.nome ORDER BY g.nome SEPARATOR ', ') as grupo_nome
            FROM clientes c
            LEFT JOIN cliente_grupo_relacao cgr ON c.id = cgr.cliente_id
            LEFT JOIN grupos_clientes g ON cgr.grupo_id = g.id
            LEFT JOIN cliente_ramo_atividade_relacao crar ON c.id = crar.cliente_id
            LEFT JOIN ramos_atividade ra ON crar.ramo_atividade_id = ra.id
            LEFT JOIN (SELECT cliente_id, MAX(validade) AS cert_validade
                         FROM dfe_certificados WHERE ativo = 1
                        GROUP BY cliente_id) cert ON cert.cliente_id = c.id
            -- endereço principal (cidade/UF) para o subtítulo da célula Cliente;
            -- MAX agrega quando há mais de um principal. Aditivo, read-only.
            LEFT JOIN enderecos_clientes ender ON ender.cliente_id = c.id AND ender.principal = 1
            {where_clause}
            GROUP BY c.id, c.numero_cliente, c.tipo_pessoa, c.nome_razao_social, c.cpf_cnpj, c.inscricao_estadual,
                     c.inscricao_municipal, c.email, c.telefone, c.celular, c.regime_tributario,
                     c.porte_empresa, c.cnae_fiscal, c.cnae_fiscal_descricao, c.data_inicio_atividade, c.data_inicio_contrato, c.situacao, c.observacoes,
                     cert.cert_validade
            {order_clause}
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
            'total_pages': (total + per_page - 1) // per_page if total > 0 else 0,
            'stats': stats,
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
        data_inicio_atividade = data.get('data_inicio_atividade') or None
        data_inicio_contrato = data.get('data_inicio_contrato') or None
        cnae_fiscal = data.get('cnae_fiscal') or None
        cnae_fiscal_descricao = data.get('cnae_fiscal_descricao') or None
        
        query = """
            INSERT INTO clientes (
                numero_cliente, tipo_pessoa, nome_razao_social, cpf_cnpj, inscricao_estadual,
                inscricao_municipal, email, telefone, celular, regime_tributario,
                porte_empresa, cnae_fiscal, cnae_fiscal_descricao, data_inicio_atividade,
                data_inicio_contrato, situacao, observacoes, aberta_pela_casa, criado_por
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            data.get('numero_cliente') or None,
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
            cnae_fiscal,
            cnae_fiscal_descricao,
            data_inicio_atividade,
            data_inicio_contrato,
            data.get('situacao', 'ATIVO'),
            data.get('observacoes') or None,
            1 if data.get('aberta_pela_casa') else 0,
            data.get('criado_por') or None      # AUTORIA (D2): quem criou
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
        data_inicio_atividade = data.get('data_inicio_atividade') or None
        data_inicio_contrato = data.get('data_inicio_contrato') or None
        cnae_fiscal = data.get('cnae_fiscal') or None
        cnae_fiscal_descricao = data.get('cnae_fiscal_descricao') or None
        
        query = """
            UPDATE clientes
            SET numero_cliente = %s, tipo_pessoa = %s, nome_razao_social = %s, cpf_cnpj = %s,
                inscricao_estadual = %s, inscricao_municipal = %s, email = %s,
                telefone = %s, celular = %s, regime_tributario = %s,
                porte_empresa = %s, cnae_fiscal = %s, cnae_fiscal_descricao = %s,
                data_inicio_atividade = %s, data_inicio_contrato = %s, situacao = %s,
                observacoes = %s, aberta_pela_casa = %s,
                alterado_por = %s, alterado_em = CURRENT_TIMESTAMP
            WHERE id = %s
        """
        params = (
            data.get('numero_cliente') or None,
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
            cnae_fiscal,
            cnae_fiscal_descricao,
            data_inicio_atividade,
            data_inicio_contrato,
            data.get('situacao'),
            data.get('observacoes') or None,
            1 if data.get('aberta_pela_casa') else 0,
            data.get('alterado_por') or None,    # AUTORIA (D2): quem alterou
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
    def get_by_cnpj_digits(cnpj_digits):
        """
        Busca cliente pelo CNPJ usando apenas os dígitos numéricos para comparação.
        Funciona independentemente de como o CNPJ está formatado no banco.

        Args:
            cnpj_digits (str): CNPJ apenas com dígitos (ex: '36142094000180')

        Returns:
            dict|None: Dados do cliente ou None
        """
        if not cnpj_digits:
            return None
        query = """
            SELECT id, tipo_pessoa, nome_razao_social, cpf_cnpj, email, situacao
            FROM clientes
            WHERE REPLACE(REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '/', ''), '-', ''), ' ', '') = %s
            LIMIT 1
        """
        return execute_query(query, (cnpj_digits,), fetch=True, fetch_one=True)

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
    def existe_numero_cliente(numero_cliente, cliente_id=None):
        """
        Verifica se número do cliente já está cadastrado.
        
        Args:
            numero_cliente (str): Número do cliente a verificar
            cliente_id (int, optional): ID do cliente para excluir da verificação (usado em edições)
            
        Returns:
            bool: True se já existe, False caso contrário
        """
        if not numero_cliente:
            return False
            
        query = "SELECT id FROM clientes WHERE numero_cliente = %s"
        params = [numero_cliente]
        
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
