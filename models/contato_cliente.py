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


# 05/11/2026 (migração Acessórias): o que um contato RECEBE é a ligação contatos_departamentos com a
# tabela departamentos — estes sete, nesta ordem (é a ordem da planilha do Acessórias). Os demais
# departamentos (Atendimento, Comercial, Diretoria) são internos e não aparecem no contato.
DEPARTAMENTOS_CONTATO = ['Contábil', 'Fiscal', 'Pessoal', 'Financeiro', 'Legalização', 'Certificado Digital', 'Visita']
_SQL_DEPTOS = """(SELECT GROUP_CONCAT(d.nome ORDER BY FIELD(d.nome, 'Contábil', 'Fiscal', 'Pessoal', 'Financeiro',
                                                            'Legalização', 'Certificado Digital', 'Visita') SEPARATOR '|')
                    FROM contatos_departamentos cd JOIN departamentos d ON d.id = cd.departamento_id
                   WHERE cd.contato_id = c.id)"""


def _split_deptos(v):
    return [x for x in (v or '').split('|') if x]


class ContatoCliente:
    """Classe para gestão de contatos de clientes"""

    @staticmethod
    def departamentos_disponiveis():
        """[{id, nome}] dos departamentos que um contato pode receber, na ordem de DEPARTAMENTOS_CONTATO."""
        rows = execute_query('SELECT id, nome FROM departamentos WHERE ativo = 1', fetch=True) or []
        por_nome = {r['nome']: r for r in rows}
        return [por_nome[n] for n in DEPARTAMENTOS_CONTATO if n in por_nome]

    @staticmethod
    def set_departamentos(contato_id, departamento_ids):
        """Troca a lista do que o contato recebe (apaga e grava)."""
        execute_query('DELETE FROM contatos_departamentos WHERE contato_id = %s', (contato_id,), fetch=False)
        for did in sorted({int(d) for d in (departamento_ids or []) if str(d).isdigit()}):
            execute_query('INSERT IGNORE INTO contatos_departamentos (contato_id, departamento_id) VALUES (%s, %s)',
                          (contato_id, did), fetch=False)

    @staticmethod
    def por_celular(celular, exceto_cliente_id=None):
        """Contatos de OUTRAS empresas com este celular (05/11/2026, Anderson: "ao digitar o telefone
        do Anderson, o sistema pergunta: quer utilizar o e-mail do tal do Anderson?")."""
        d = ''.join(ch for ch in str(celular or '') if ch.isdigit())
        if len(d) < 10:
            return []
        rows = execute_query(f"""
            SELECT c.id, c.cliente_id, c.nome, c.cargo, c.email, c.celular, k.nome_razao_social AS empresa,
                   k.numero_cliente, {_SQL_DEPTOS} AS deptos
              FROM contatos_clientes c JOIN clientes k ON k.id = c.cliente_id
             WHERE c.ativo = 1 AND REGEXP_REPLACE(COALESCE(c.celular, ''), '[^0-9]', '') = %s
               AND (%s IS NULL OR c.cliente_id <> %s)
             ORDER BY c.id""", (d, exceto_cliente_id, exceto_cliente_id), fetch=True) or []
        for r in rows:
            r['departamentos'] = _split_deptos(r.pop('deptos'))
        return rows
    
    @staticmethod
    def get_by_cliente(cliente_id):
        """
        Busca todos os contatos de um cliente.
        
        Args:
            cliente_id (int): ID do cliente
            
        Returns:
            list: Lista de contatos
        """
        query = f"""
            SELECT c.id, c.cliente_id, c.nome, c.cargo, c.email, c.telefone, c.celular,
                   c.departamento, c.areas_atendimento, c.principal, c.ativo, {_SQL_DEPTOS} AS deptos
            FROM contatos_clientes c
            WHERE c.cliente_id = %s
            ORDER BY c.principal DESC, c.nome ASC
        """
        rows = execute_query(query, (cliente_id,), fetch=True) or []
        for r in rows:
            r['areas_atendimento'] = _parse_areas(r.get('areas_atendimento'))
            r['departamentos'] = _split_deptos(r.pop('deptos'))
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
        query = f"""
            SELECT c.id, c.cliente_id, c.nome, c.cargo, c.email, c.telefone, c.celular,
                   c.departamento, c.areas_atendimento, c.principal, c.ativo, {_SQL_DEPTOS} AS deptos
            FROM contatos_clientes c
            WHERE c.id = %s
        """
        row = execute_query(query, (contato_id,), fetch=True, fetch_one=True)
        if row:
            row['areas_atendimento'] = _parse_areas(row.get('areas_atendimento'))
            row['departamentos'] = _split_deptos(row.pop('deptos'))
        return row
    
    @staticmethod
    def create(cliente_id, nome, cargo=None, email=None, telefone=None,
               celular=None, departamento=None, areas_atendimento=None,
               principal=False, ativo=True, departamentos=None):
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
        cid = execute_query(query, params)
        if cid and departamentos is not None:
            ContatoCliente.set_departamentos(cid, departamentos)
        return cid
    
    @staticmethod
    def update(contato_id, nome, cargo=None, email=None, telefone=None,
               celular=None, departamento=None, areas_atendimento=None,
               principal=False, ativo=True, departamentos=None):
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
        r = execute_query(query, params)
        if departamentos is not None:
            ContatoCliente.set_departamentos(contato_id, departamentos)
        return r
    
    @staticmethod
    def delete(contato_id):
        """
        Remove contato.
        
        Args:
            contato_id (int): ID do contato
            
        Returns:
            int: ID do contato ou None
        """
        execute_query('DELETE FROM contatos_departamentos WHERE contato_id = %s', (contato_id,), fetch=False)
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
