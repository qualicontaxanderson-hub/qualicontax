"""Modelo de Contato de Cliente"""
import json
from utils.db_helper import execute_query


# Áreas de atendimento disponíveis para seleção em um contato
AREAS_ATENDIMENTO = [
    ('FINANCEIRO',   'Financeiro',    'fa-dollar-sign',      '#3b82f6'),
    ('FISCAL',       'Fiscal',        'fa-file-invoice',     '#f59e0b'),
    ('CONTABIL',     'Contábil',      'fa-calculator',       '#8b5cf6'),
    ('PESSOAL',      'Pessoal / RH',  'fa-users',            '#ec4899'),
    ('SOCIETARIO',   'Societário',    'fa-building',         '#6b7280'),
    ('TRIBUTARIO',   'Tributário',    'fa-landmark',         '#ef4444'),
]


class ContatoCliente:
    """Classe para gestão de contatos de clientes"""
    
    @staticmethod
    def get_by_cliente(cliente_id):
        """
        Busca todos os contatos de um cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de contatos
        """
        query = """
            SELECT id, cliente_id, nome, cargo, email, telefone, celular,
                   departamento, areas_atendimento, principal, ativo
            FROM contatos_clientes
            WHERE cliente_id = %s
            ORDER BY principal DESC, nome ASC
        """
        rows = execute_query(query, (cliente_id,), fetch=True) or []
        for r in rows:
            r['areas_atendimento'] = _parse_areas(r.get('areas_atendimento'))
        return rows
    
    @staticmethod
    def get_by_id(contato_id):
        """
        Busca contato por ID.
        
        Args:
            contato_id (int): ID do contato
            
        Returns:
            dict: Dados do contato ou None
        """
        query = """
            SELECT id, cliente_id, nome, cargo, email, telefone, celular,
                   departamento, areas_atendimento, principal, ativo
            FROM contatos_clientes
            WHERE id = %s
        """
        row = execute_query(query, (contato_id,), fetch=True, fetch_one=True)
        if row:
            row['areas_atendimento'] = _parse_areas(row.get('areas_atendimento'))
        return row
    
    @staticmethod
    def create(cliente_id, nome, cargo=None, email=None, telefone=None,
               celular=None, departamento=None, areas_atendimento=None,
               principal=False, ativo=True):
        """
        Cria novo contato.
        
        Args:
            cliente_id (int): ID do cliente
            nome (str): Nome do contato
            cargo (str, optional): Cargo
            email (str, optional): Email
            telefone (str, optional): Telefone
            celular (str, optional): Celular
            departamento (str, optional): Departamento
            areas_atendimento (list, optional): Áreas de atendimento (lista de strings)
            principal (bool, optional): Se é contato principal. Defaults to False
            ativo (bool, optional): Se está ativo. Defaults to True
            
        Returns:
            int: ID do contato criado ou None
        """
        # Converter nome para MAIÚSCULAS
        nome = nome.upper() if nome else nome
        
        # Se for marcado como principal, desmarca os outros
        if principal:
            ContatoCliente.set_principal(cliente_id, None)
        
        areas_json = _serialize_areas(areas_atendimento)
        
        query = """
            INSERT INTO contatos_clientes (
                cliente_id, nome, cargo, email, telefone, celular,
                departamento, areas_atendimento, principal, ativo
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        params = (
            cliente_id, nome, cargo, email, telefone, celular,
            departamento, areas_json, principal, ativo
        )
        return execute_query(query, params)
    
    @staticmethod
    def update(contato_id, nome, cargo=None, email=None, telefone=None,
               celular=None, departamento=None, areas_atendimento=None,
               principal=False, ativo=True):
        """
        Atualiza dados do contato.
        
        Args:
            contato_id (int): ID do contato
            nome (str): Nome do contato
            cargo (str, optional): Cargo
            email (str, optional): Email
            telefone (str, optional): Telefone
            celular (str, optional): Celular
            departamento (str, optional): Departamento
            areas_atendimento (list, optional): Áreas de atendimento (lista de strings)
            principal (bool, optional): Se é contato principal
            ativo (bool, optional): Se está ativo
            
        Returns:
            int: ID do contato ou None
        """
        # Converter nome para MAIÚSCULAS
        nome = nome.upper() if nome else nome
        
        # Se for marcado como principal, desmarca os outros
        if principal:
            contato = ContatoCliente.get_by_id(contato_id)
            if contato:
                ContatoCliente.set_principal(contato['cliente_id'], contato_id)
        
        areas_json = _serialize_areas(areas_atendimento)
        
        query = """
            UPDATE contatos_clientes
            SET nome = %s, cargo = %s, email = %s, telefone = %s,
                celular = %s, departamento = %s, areas_atendimento = %s,
                principal = %s, ativo = %s
            WHERE id = %s
        """
        params = (
            nome, cargo, email, telefone, celular,
            departamento, areas_json, principal, ativo, contato_id
        )
        return execute_query(query, params)
    
    @staticmethod
    def delete(contato_id):
        """
        Remove contato.
        
        Args:
            contato_id (int): ID do contato
            
        Returns:
            int: ID do contato ou None
        """
        query = "DELETE FROM contatos_clientes WHERE id = %s"
        return execute_query(query, (contato_id,))
    
    @staticmethod
    def set_principal(cliente_id, contato_id):
        """
        Define um contato como principal e desmarca os outros.
        
        Args:
            cliente_id (int): ID do cliente
            contato_id (int): ID do contato a ser marcado como principal (None para desmarcar todos)
            
        Returns:
            bool: True se bem-sucedido
        """
        # Primeiro, desmarca todos os contatos do cliente
        query = """
            UPDATE contatos_clientes
            SET principal = FALSE
            WHERE cliente_id = %s
        """
        execute_query(query, (cliente_id,))
        
        # Depois, marca o contato especificado como principal
        if contato_id:
            query = """
                UPDATE contatos_clientes
                SET principal = TRUE
                WHERE id = %s AND cliente_id = %s
            """
            return execute_query(query, (contato_id, cliente_id)) is not None
        
        return True


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _parse_areas(value):
    """Converte JSON string (ou None) para lista de strings."""
    if not value:
        return []
    try:
        result = json.loads(value)
        return result if isinstance(result, list) else []
    except (ValueError, TypeError):
        return []


def _serialize_areas(areas):
    """Converte lista de áreas para JSON string (ou None se vazia)."""
    if not areas:
        return None
    cleaned = [a.upper().strip() for a in areas if a and a.strip()]
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None
