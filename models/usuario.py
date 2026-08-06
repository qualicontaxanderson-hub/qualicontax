"""Modelo de Usuário"""
from flask_login import UserMixin
from utils.db_helper import execute_query


# Colunas que os getters trazem, em UM lugar só.
#
# Antes do Q-Colabore este SELECT estava copiado nos três getters, e o bloco que
# constrói o Usuario a partir da linha, também. Adicionar uma coluna significava
# editar seis pontos e torcer para não esquecer nenhum — e esquecer um aqui não
# dá erro: o parâmetro cai no default do __init__ e a conta entra com o valor
# errado, calada. Com classe_conta no jogo isso deixaria de ser desconforto e
# viraria falha de segurança (uma conta CLIENTE lida como FUNCIONARIO).
_CAMPOS = """id, nome, nick, email, login, senha_hash, tipo_usuario, classe_conta,
             situacao, cpf, telefone, departamento_id, cargo, capacidade_tarefas,
             data_admissao, foto_perfil"""


class Usuario(UserMixin):
    """Classe Usuario para autenticação com Flask-Login"""

    def __init__(self, id, nome, email, senha_hash, tipo_usuario='ASSISTENTE', situacao='ATIVO',
                 cpf=None, telefone=None, departamento_id=None, cargo=None,
                 capacidade_tarefas=10, data_admissao=None, foto_perfil=None, login=None,
                 classe_conta='FUNCIONARIO', nick=None):
        self.id = id
        self.nome = nome
        self.nick = nick
        self.email = email
        self.login = login
        self.senha_hash = senha_hash
        self.tipo_usuario = tipo_usuario
        self.classe_conta = classe_conta
        self.situacao = situacao
        self.cpf = cpf
        self.telefone = telefone
        self.departamento_id = departamento_id
        self.cargo = cargo
        self.capacidade_tarefas = capacidade_tarefas
        self.data_admissao = data_admissao
        self.foto_perfil = foto_perfil
        # Cached permission set – populated lazily
        self._permissoes = None

    @classmethod
    def _from_row(cls, row):
        """Constrói um Usuario a partir de uma linha de _CAMPOS. None → None.

        Ponto ÚNICO de tradução linha→objeto. Os três getters passam por aqui.
        """
        if not row:
            return None
        return cls(
            id=row['id'],
            nome=row['nome'],
            nick=row.get('nick'),
            email=row['email'],
            login=row.get('login'),
            senha_hash=row['senha_hash'],
            tipo_usuario=row.get('tipo_usuario', 'ASSISTENTE'),
            classe_conta=row.get('classe_conta', 'FUNCIONARIO'),
            situacao=row.get('situacao', 'ATIVO'),
            cpf=row.get('cpf'),
            telefone=row.get('telefone'),
            departamento_id=row.get('departamento_id'),
            cargo=row.get('cargo'),
            capacidade_tarefas=row.get('capacidade_tarefas', 10),
            data_admissao=row.get('data_admissao'),
            foto_perfil=row.get('foto_perfil'),
        )

    def is_admin(self):
        """Verifica se o usuário é admin"""
        return self.tipo_usuario == 'ADMIN'

    def is_cliente(self):
        """True quando a conta é de CLIENTE, não de funcionário do escritório.

        Eixo NOVO e ortogonal ao tipo_usuario: 'que cargo tem' e 'de que lado
        está' são perguntas diferentes. is_admin() segue valendo exatamente o
        que valia — quem precisa negar acesso a conta de cliente checa ISTO,
        não mexe naquilo.
        """
        return self.classe_conta == 'CLIENTE'

    def is_active(self):
        """Verifica se o usuário está ativo (requerido pelo Flask-Login)"""
        return self.situacao == 'ATIVO'
    
    def get_id(self):
        """Retorna o ID do usuário (requerido pelo Flask-Login)"""
        return str(self.id)

    # ------------------------------------------------------------------
    # Permissões
    # ------------------------------------------------------------------

    def get_permissoes(self):
        """Retorna set de codigos de permissão do usuário (via perfis)."""
        if self._permissoes is None:
            rows = execute_query(
                """SELECT pp.permissao_codigo
                     FROM usuario_perfis up
                     JOIN perfil_permissoes pp ON pp.perfil_id = up.perfil_id
                    WHERE up.usuario_id = %s""",
                (self.id,), fetch=True,
            ) or []
            self._permissoes = {r['permissao_codigo'] for r in rows}
        return self._permissoes

    def has_permission(self, codigo):
        """Retorna True se o usuário tem a permissão informada.
        
        ADMINs sempre têm acesso total.
        """
        if self.is_admin():
            return True
        return codigo in self.get_permissoes()

    def get_empresas_permitidas(self):
        """Retorna set de cliente_ids que o usuário pode visualizar.
        
        Retorna None quando sem restrições (vê todas as empresas).
        """
        rows = execute_query(
            "SELECT cliente_id FROM usuario_empresas_permitidas WHERE usuario_id = %s",
            (self.id,), fetch=True,
        ) or []
        if not rows:
            return None  # sem restrição
        return {r['cliente_id'] for r in rows}
    
    @staticmethod
    def get_by_id(user_id):
        """
        Busca usuário por ID.
        
        Args:
            user_id (int): ID do usuário
            
        Returns:
            Usuario: Objeto Usuario ou None
        """
        return Usuario._from_row(execute_query(
            f'SELECT {_CAMPOS} FROM usuarios WHERE id = %s',
            (user_id,), fetch=True, fetch_one=True))


    @staticmethod
    def get_by_email(email):
        """
        Busca usuário por email.
        
        Args:
            email (str): Email do usuário
            
        Returns:
            Usuario: Objeto Usuario ou None
        """
        return Usuario._from_row(execute_query(
            f'SELECT {_CAMPOS} FROM usuarios WHERE email = %s',
            (email,), fetch=True, fetch_one=True))


    @staticmethod
    def get_by_login(login):
        """
        Busca usuário pelo nome de login.

        Args:
            login (str): Login do usuário

        Returns:
            Usuario: Objeto Usuario ou None
        """
        return Usuario._from_row(execute_query(
            f'SELECT {_CAMPOS} FROM usuarios WHERE login = %s',
            (login,), fetch=True, fetch_one=True))


    @staticmethod
    def create(nome, email, senha_hash, tipo_usuario='ASSISTENTE', cpf=None, telefone=None,
               departamento_id=None, cargo=None, capacidade_tarefas=10,
               classe_conta='FUNCIONARIO', nick=None):
        """
        Cria novo usuário.

        Args:
            nome (str): Nome do usuário
            email (str): Email do usuário
            senha_hash (str): Hash da senha
            tipo_usuario (str): Tipo do usuário (ADMIN/GERENTE/CONTADOR/ASSISTENTE/ESTAGIARIO)
            cpf (str): CPF do usuário
            telefone (str): Telefone do usuário
            departamento_id (int): ID do departamento
            cargo (str): Cargo do usuário
            capacidade_tarefas (int): Capacidade de tarefas simultâneas
            classe_conta (str): FUNCIONARIO (default) ou CLIENTE
            nick (str): Apelido exibido na UI

        Returns:
            int: ID do usuário criado ou None
        """
        # classe_conta com default 'FUNCIONARIO': toda chamada existente segue
        # criando funcionário sem precisar mudar uma linha.
        query = """
            INSERT INTO usuarios (nome, nick, email, senha_hash, tipo_usuario, classe_conta,
                                situacao, cpf, telefone, departamento_id, cargo,
                                capacidade_tarefas)
            VALUES (%s, %s, %s, %s, %s, %s, 'ATIVO', %s, %s, %s, %s, %s)
        """
        return execute_query(query, (nome, nick, email, senha_hash, tipo_usuario,
                                    classe_conta, cpf, telefone, departamento_id,
                                    cargo, capacidade_tarefas))

    @staticmethod
    def get_all(classe_conta='FUNCIONARIO'):
        """
        Retorna todos os usuários de uma classe de conta.

        Default 'FUNCIONARIO': as telas de equipe que já existem continuam
        listando exatamente o que listavam, sem passar a mostrar conta de
        cliente quando elas surgirem. ``classe_conta=None`` traz todas.

        Returns:
            list: Lista de dicionários com dados dos usuários
        """
        query = """
            SELECT id, nome, nick, email, tipo_usuario, classe_conta, situacao,
                   cargo, departamento_id, criado_em
            FROM usuarios
        """
        params = None
        if classe_conta:
            query += ' WHERE classe_conta = %s'
            params = (classe_conta,)
        query += ' ORDER BY nome'
        return execute_query(query, params, fetch=True) or []
