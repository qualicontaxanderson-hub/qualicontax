"""Modelo de Município"""
from utils.db_helper import execute_query


class Municipio:
    """Classe para gestão de municípios e seus portais da prefeitura"""

    @staticmethod
    def get_all(uf=None, situacao=None, busca=None):
        """
        Retorna todos os municípios com filtros opcionais.

        Args:
            uf (str, optional): Filtrar por UF
            situacao (str, optional): Filtrar por situação (ATIVO, INATIVO)
            busca (str, optional): Filtrar por nome

        Returns:
            list: Lista de municípios
        """
        conditions = []
        params = []

        if uf:
            conditions.append("uf = %s")
            params.append(uf.upper())

        if situacao:
            conditions.append("situacao = %s")
            params.append(situacao)

        if busca:
            search_term = busca.replace('%', '\\%').replace('_', '\\_')
            conditions.append("(nome LIKE %s OR uf LIKE %s)")
            pattern = f"%{search_term}%"
            params.extend([pattern, pattern])

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        query = f"""
            SELECT id, nome, uf, site_prefeitura, situacao
            FROM municipios{where_clause}
            ORDER BY uf, nome
        """
        return execute_query(query, tuple(params) if params else None, fetch=True) or []

    @staticmethod
    def get_by_id(municipio_id):
        """
        Busca município por ID.

        Args:
            municipio_id (int): ID do município

        Returns:
            dict: Dados do município ou None
        """
        query = """
            SELECT id, nome, uf, site_prefeitura, situacao
            FROM municipios
            WHERE id = %s
        """
        return execute_query(query, (municipio_id,), fetch=True, fetch_one=True)

    @staticmethod
    def create(nome, uf, site_prefeitura=None, situacao='ATIVO'):
        """
        Cria novo município.

        Args:
            nome (str): Nome do município
            uf (str): UF (sigla do estado)
            site_prefeitura (str, optional): URL do portal da prefeitura
            situacao (str, optional): Situação. Defaults to 'ATIVO'

        Returns:
            int: ID do município criado ou None
        """
        query = """
            INSERT INTO municipios (nome, uf, site_prefeitura, situacao)
            VALUES (%s, %s, %s, %s)
        """
        return execute_query(query, (nome.upper(), uf.upper(), site_prefeitura or None, situacao))

    @staticmethod
    def update(municipio_id, nome, uf, site_prefeitura=None, situacao='ATIVO'):
        """
        Atualiza dados do município.

        Args:
            municipio_id (int): ID do município
            nome (str): Nome do município
            uf (str): UF do município
            site_prefeitura (str, optional): URL do portal
            situacao (str, optional): Situação

        Returns:
            bool: True se bem-sucedido, None se erro
        """
        query = """
            UPDATE municipios
            SET nome = %s, uf = %s, site_prefeitura = %s, situacao = %s
            WHERE id = %s
        """
        return execute_query(query, (nome.upper(), uf.upper(), site_prefeitura or None, situacao, municipio_id), fetch=False)

    @staticmethod
    def delete(municipio_id):
        """
        Remove município.

        Args:
            municipio_id (int): ID do município

        Returns:
            bool: True se bem-sucedido, None se erro
        """
        query = "DELETE FROM municipios WHERE id = %s"
        return execute_query(query, (municipio_id,), fetch=False)

    @staticmethod
    def search(query_text, uf=None):
        """
        Busca municípios por texto (nome ou UF).

        Args:
            query_text (str): Texto de busca
            uf (str, optional): Filtrar por UF

        Returns:
            list: Lista de municípios encontrados
        """
        search_term = query_text.replace('%', '\\%').replace('_', '\\_')
        pattern = f"%{search_term}%"

        conditions = ["situacao = 'ATIVO'", "(nome LIKE %s OR uf LIKE %s)"]
        params = [pattern, pattern]

        if uf:
            conditions.append("uf = %s")
            params.append(uf.upper())

        where_clause = " WHERE " + " AND ".join(conditions)
        query = f"""
            SELECT id, nome, uf, site_prefeitura
            FROM municipios{where_clause}
            ORDER BY uf, nome
            LIMIT 50
        """
        return execute_query(query, tuple(params), fetch=True) or []
