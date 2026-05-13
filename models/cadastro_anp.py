"""Modelo para Cadastro ANP de clientes"""
from utils.db_helper import execute_query


class CadastroAnp:
    """Gestão dos dados da ANP vinculados a um cliente."""

    # ------------------------------------------------------------------
    # Cadastro principal
    # ------------------------------------------------------------------

    @staticmethod
    def get_by_cliente(cliente_id):
        """Retorna todos os cadastros ANP de um cliente."""
        query = """
            SELECT id, cliente_id, situacao, autorizacao, cnpj_anp, razao_social,
                   nome_fantasia, endereco, complemento, bairro, municipio_uf, cep,
                   nr_despacho, data_publicacao, bandeira, data_inicio_bandeira,
                   tipo_posto, pmqc, delivery, latitude, longitude,
                   data_emissao, fonte, criado_em, atualizado_em
            FROM cadastros_anp
            WHERE cliente_id = %s
            ORDER BY criado_em DESC
        """
        return execute_query(query, (cliente_id,), fetch=True) or []

    @staticmethod
    def get_by_id(anp_id):
        """Retorna um cadastro ANP pelo ID."""
        query = """
            SELECT id, cliente_id, situacao, autorizacao, cnpj_anp, razao_social,
                   nome_fantasia, endereco, complemento, bairro, municipio_uf, cep,
                   nr_despacho, data_publicacao, bandeira, data_inicio_bandeira,
                   tipo_posto, pmqc, delivery, latitude, longitude,
                   data_emissao, fonte, criado_em, atualizado_em
            FROM cadastros_anp
            WHERE id = %s
        """
        return execute_query(query, (anp_id,), fetch=True, fetch_one=True)

    @staticmethod
    def create(cliente_id, data):
        """Cria novo cadastro ANP. Retorna o ID inserido."""
        query = """
            INSERT INTO cadastros_anp (
                cliente_id, situacao, autorizacao, cnpj_anp, razao_social, nome_fantasia,
                endereco, complemento, bairro, municipio_uf, cep,
                nr_despacho, data_publicacao, bandeira, data_inicio_bandeira,
                tipo_posto, pmqc, delivery, latitude, longitude,
                data_emissao, fonte
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s
            )
        """
        params = (
            cliente_id,
            data.get('situacao'),
            data.get('autorizacao'),
            data.get('cnpj_anp'),
            data.get('razao_social'),
            data.get('nome_fantasia'),
            data.get('endereco'),
            data.get('complemento'),
            data.get('bairro'),
            data.get('municipio_uf'),
            data.get('cep'),
            data.get('nr_despacho'),
            data.get('data_publicacao') or None,
            data.get('bandeira'),
            data.get('data_inicio_bandeira') or None,
            data.get('tipo_posto'),
            data.get('pmqc'),
            data.get('delivery'),
            data.get('latitude'),
            data.get('longitude'),
            data.get('data_emissao') or None,
            data.get('fonte', 'MANUAL'),
        )
        return execute_query(query, params)

    @staticmethod
    def update(anp_id, data):
        """Atualiza cadastro ANP existente."""
        query = """
            UPDATE cadastros_anp SET
                situacao=%s, autorizacao=%s, cnpj_anp=%s, razao_social=%s, nome_fantasia=%s,
                endereco=%s, complemento=%s, bairro=%s, municipio_uf=%s, cep=%s,
                nr_despacho=%s, data_publicacao=%s, bandeira=%s, data_inicio_bandeira=%s,
                tipo_posto=%s, pmqc=%s, delivery=%s, latitude=%s, longitude=%s,
                data_emissao=%s, fonte=%s
            WHERE id=%s
        """
        params = (
            data.get('situacao'),
            data.get('autorizacao'),
            data.get('cnpj_anp'),
            data.get('razao_social'),
            data.get('nome_fantasia'),
            data.get('endereco'),
            data.get('complemento'),
            data.get('bairro'),
            data.get('municipio_uf'),
            data.get('cep'),
            data.get('nr_despacho'),
            data.get('data_publicacao') or None,
            data.get('bandeira'),
            data.get('data_inicio_bandeira') or None,
            data.get('tipo_posto'),
            data.get('pmqc'),
            data.get('delivery'),
            data.get('latitude'),
            data.get('longitude'),
            data.get('data_emissao') or None,
            data.get('fonte', 'MANUAL'),
            anp_id,
        )
        return execute_query(query, params)

    @staticmethod
    def delete(anp_id):
        """Remove cadastro ANP e seus sócios/produtos (CASCADE)."""
        return execute_query("DELETE FROM cadastros_anp WHERE id = %s", (anp_id,))

    # ------------------------------------------------------------------
    # Sócios ANP
    # ------------------------------------------------------------------

    @staticmethod
    def get_socios(cadastro_anp_id):
        """Retorna sócios de um cadastro ANP."""
        return execute_query(
            "SELECT id, cadastro_anp_id, nome FROM cadastros_anp_socios WHERE cadastro_anp_id = %s ORDER BY id",
            (cadastro_anp_id,), fetch=True,
        ) or []

    @staticmethod
    def delete_socios(cadastro_anp_id):
        """Remove todos os sócios de um cadastro ANP."""
        execute_query("DELETE FROM cadastros_anp_socios WHERE cadastro_anp_id = %s", (cadastro_anp_id,))

    @staticmethod
    def insert_socio(cadastro_anp_id, nome):
        """Insere sócio no cadastro ANP."""
        execute_query(
            "INSERT INTO cadastros_anp_socios (cadastro_anp_id, nome) VALUES (%s, %s)",
            (cadastro_anp_id, nome),
        )

    # ------------------------------------------------------------------
    # Produtos ANP
    # ------------------------------------------------------------------

    @staticmethod
    def get_produtos(cadastro_anp_id):
        """Retorna produtos de um cadastro ANP."""
        return execute_query(
            "SELECT id, cadastro_anp_id, produto, tancagem_m3, bicos "
            "FROM cadastros_anp_produtos WHERE cadastro_anp_id = %s ORDER BY id",
            (cadastro_anp_id,), fetch=True,
        ) or []

    @staticmethod
    def delete_produtos(cadastro_anp_id):
        """Remove todos os produtos de um cadastro ANP."""
        execute_query("DELETE FROM cadastros_anp_produtos WHERE cadastro_anp_id = %s", (cadastro_anp_id,))

    @staticmethod
    def insert_produto(cadastro_anp_id, produto, tancagem_m3=None, bicos=None):
        """Insere produto no cadastro ANP."""
        execute_query(
            "INSERT INTO cadastros_anp_produtos (cadastro_anp_id, produto, tancagem_m3, bicos) VALUES (%s,%s,%s,%s)",
            (cadastro_anp_id, produto, tancagem_m3, bicos),
        )

    # ------------------------------------------------------------------
    # Helpers: save_full (create/update + socios + produtos atomicamente)
    # ------------------------------------------------------------------

    @classmethod
    def save_full(cls, cliente_id, data, socios=None, produtos=None, anp_id=None):
        """
        Cria ou atualiza cadastro ANP + sócios + produtos.

        Args:
            cliente_id (int)
            data (dict): campos do cadastro ANP
            socios (list[str]): nomes dos sócios
            produtos (list[dict]): [{'produto': str, 'tancagem_m3': float, 'bicos': int}]
            anp_id (int|None): se fornecido, atualiza; senão, cria

        Returns:
            int: ID do cadastro ANP
        """
        if anp_id:
            cls.update(anp_id, data)
        else:
            anp_id = cls.create(cliente_id, data)
            if not anp_id:
                return None

        if socios is not None:
            cls.delete_socios(anp_id)
            for nome in socios:
                nome = (nome or '').strip()
                if nome:
                    cls.insert_socio(anp_id, nome)

        if produtos is not None:
            cls.delete_produtos(anp_id)
            for p in produtos:
                produto = (p.get('produto') or '').strip()
                if produto:
                    cls.insert_produto(
                        anp_id,
                        produto,
                        tancagem_m3=p.get('tancagem_m3'),
                        bicos=p.get('bicos'),
                    )

        return anp_id

    @staticmethod
    def find_by_cnpj(cnpj):
        """Busca cadastro ANP pelo CNPJ (somente dígitos para comparação)."""
        cnpj_digits = ''.join(c for c in (cnpj or '') if c.isdigit())
        if not cnpj_digits:
            return None
        query = """
            SELECT a.id, a.cliente_id, c.cpf_cnpj, c.nome_razao_social
            FROM cadastros_anp a
            JOIN clientes c ON c.id = a.cliente_id
            WHERE REPLACE(REPLACE(REPLACE(REPLACE(a.cnpj_anp, '.', ''), '/', ''), '-', ''), ' ', '') = %s
               OR REPLACE(REPLACE(REPLACE(REPLACE(c.cpf_cnpj, '.', ''), '/', ''), '-', ''), ' ', '') = %s
            ORDER BY a.criado_em DESC
            LIMIT 1
        """
        return execute_query(query, (cnpj_digits, cnpj_digits), fetch=True, fetch_one=True)
