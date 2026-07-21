"""Modelo do vínculo de Certificado Digital (tabela dfe_certificados)."""
from utils.db_helper import execute_query


class DfeCertificado:
    """Vínculo do certificado digital (.pfx) de uma empresa."""

    @staticmethod
    def get_by_cliente(cliente_id):
        """Retorna o vínculo do certificado da empresa, ou None.

        Não retorna a senha cifrada (não é usada na tela).
        """
        query = """
            SELECT id, cliente_id, cnpj, tipo_doc, dropbox_path, validade,
                   criado_em, atualizado_em
            FROM dfe_certificados
            WHERE cliente_id = %s
        """
        return execute_query(query, (cliente_id,), fetch=True, fetch_one=True)

    @staticmethod
    def upsert(cliente_id, cnpj, tipo_doc, senha_cifrada, dropbox_path, validade=None):
        """Insere ou atualiza o vínculo (1 certificado vigente por empresa)."""
        query = """
            INSERT INTO dfe_certificados
                (cliente_id, cnpj, tipo_doc, senha_cifrada, dropbox_path, validade)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                cnpj = VALUES(cnpj),
                tipo_doc = VALUES(tipo_doc),
                senha_cifrada = VALUES(senha_cifrada),
                dropbox_path = VALUES(dropbox_path),
                validade = VALUES(validade)
        """
        return execute_query(
            query,
            (cliente_id, cnpj, tipo_doc, senha_cifrada, dropbox_path, validade),
        )
