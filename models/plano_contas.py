"""Modelo de Plano de Contas"""
from utils.db_helper import execute_query, execute_many


class PlanoConta:
    """Gestão de grupos/planos de contas contábeis"""

    @staticmethod
    def get_all(grupo_id=None, situacao=None):
        """Retorna todos os planos, opcionalmente filtrados."""
        query = """
            SELECT pc.id, pc.nome, pc.descricao, pc.grupo_id, pc.situacao, pc.criado_em,
                   gc.nome AS grupo_nome,
                   COUNT(pci.id) AS total_contas
            FROM planos_contas pc
            LEFT JOIN grupos_clientes gc ON pc.grupo_id = gc.id
            LEFT JOIN plano_contas_itens pci ON pci.plano_id = pc.id
        """
        params = []
        conditions = []

        if grupo_id:
            conditions.append("pc.grupo_id = %s")
            params.append(grupo_id)
        if situacao:
            conditions.append("pc.situacao = %s")
            params.append(situacao)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " GROUP BY pc.id ORDER BY pc.nome"
        return execute_query(query, tuple(params) if params else None, fetch=True) or []

    @staticmethod
    def get_by_id(plano_id):
        """Busca plano por ID."""
        query = """
            SELECT pc.id, pc.nome, pc.descricao, pc.grupo_id, pc.situacao, pc.criado_em,
                   gc.nome AS grupo_nome
            FROM planos_contas pc
            LEFT JOIN grupos_clientes gc ON pc.grupo_id = gc.id
            WHERE pc.id = %s
        """
        return execute_query(query, (plano_id,), fetch=True, fetch_one=True)

    @staticmethod
    def create(nome, descricao=None, grupo_id=None, situacao='ATIVO'):
        """Cria novo plano de contas. Retorna ID do novo registro ou None."""
        query = """
            INSERT INTO planos_contas (nome, descricao, grupo_id, situacao)
            VALUES (%s, %s, %s, %s)
        """
        return execute_query(query, (nome, descricao, grupo_id, situacao))

    @staticmethod
    def update(plano_id, nome, descricao=None, grupo_id=None, situacao='ATIVO'):
        """Atualiza plano de contas."""
        query = """
            UPDATE planos_contas
            SET nome = %s, descricao = %s, grupo_id = %s, situacao = %s
            WHERE id = %s
        """
        return execute_query(query, (nome, descricao, grupo_id, situacao, plano_id))

    @staticmethod
    def delete(plano_id):
        """Remove plano de contas (cascata apaga os itens)."""
        query = "DELETE FROM planos_contas WHERE id = %s"
        return execute_query(query, (plano_id,))


class PlanoContaItem:
    """Gestão de contas individuais dentro de um plano de contas"""

    @staticmethod
    def get_all_by_plano(plano_id):
        """Retorna todos os itens de um plano, ordenados por código."""
        query = """
            SELECT id, plano_id, codigo, descricao, tipo, natureza, grupo_contabil, situacao, criado_em
            FROM plano_contas_itens
            WHERE plano_id = %s
            ORDER BY codigo
        """
        return execute_query(query, (plano_id,), fetch=True) or []

    @staticmethod
    def get_by_id(item_id):
        """Busca item por ID."""
        query = """
            SELECT id, plano_id, codigo, descricao, tipo, natureza, grupo_contabil, situacao, criado_em
            FROM plano_contas_itens
            WHERE id = %s
        """
        return execute_query(query, (item_id,), fetch=True, fetch_one=True)

    @staticmethod
    def create(plano_id, codigo, descricao, tipo, natureza, grupo_contabil, situacao='ATIVO'):
        """Cria novo item no plano de contas."""
        query = """
            INSERT INTO plano_contas_itens
                (plano_id, codigo, descricao, tipo, natureza, grupo_contabil, situacao)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        return execute_query(query, (plano_id, codigo, descricao, tipo, natureza, grupo_contabil, situacao))

    @staticmethod
    def import_batch(plano_id, itens):
        """
        Importa múltiplos itens de uma vez.

        Args:
            plano_id (int): ID do plano
            itens (list[dict]): Lista com chaves: codigo, descricao, tipo, natureza, grupo_contabil

        Returns:
            bool: True se importação ocorreu sem erros
        """
        query = """
            INSERT INTO plano_contas_itens
                (plano_id, codigo, descricao, tipo, natureza, grupo_contabil, situacao)
            VALUES (%s, %s, %s, %s, %s, %s, 'ATIVO')
        """
        data = [
            (plano_id, i['codigo'], i['descricao'], i['tipo'], i['natureza'], i['grupo_contabil'])
            for i in itens
        ]
        return execute_many(query, data)

    @staticmethod
    def delete(item_id):
        """Remove um item do plano de contas."""
        query = "DELETE FROM plano_contas_itens WHERE id = %s"
        return execute_query(query, (item_id,))

    @staticmethod
    def delete_all_by_plano(plano_id):
        """Remove todos os itens de um plano."""
        query = "DELETE FROM plano_contas_itens WHERE plano_id = %s"
        return execute_query(query, (plano_id,))
