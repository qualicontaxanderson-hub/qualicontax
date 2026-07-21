"""
Script de migração: cria a tabela dfe_certificados.

Guarda o vínculo do certificado digital (.pfx) de cada empresa:
documento (CNPJ/CPF), senha cifrada (Fernet), caminho do arquivo no Dropbox
e validade. Idempotente — seguro rodar múltiplas vezes.
"""

from utils.db_helper import execute_query


def migrate_add_dfe_certificados():
    """Cria a tabela dfe_certificados se ainda não existir."""

    print("Iniciando migração: criar tabela dfe_certificados...")

    check_query = """
        SELECT COUNT(*) as count
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
        AND TABLE_NAME = 'dfe_certificados'
    """

    result = execute_query(check_query, fetch=True, fetch_one=True)

    if result and result['count'] > 0:
        print("✓ Tabela dfe_certificados já existe")
        return True

    create_query = """
        CREATE TABLE IF NOT EXISTS dfe_certificados (
            id             INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id     INT NOT NULL,
            cnpj           VARCHAR(14) NOT NULL,
            tipo_doc       ENUM('CNPJ','CPF') NOT NULL DEFAULT 'CNPJ',
            senha_cifrada  VARBINARY(512) NOT NULL,
            dropbox_path   VARCHAR(500) NOT NULL,
            validade       DATE NULL,
            criado_em      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_cliente (cliente_id),
            INDEX idx_cnpj (cnpj),
            CONSTRAINT fk_dfecert_cliente FOREIGN KEY (cliente_id)
                REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """

    try:
        execute_query(create_query)
        print("✓ Tabela dfe_certificados criada com sucesso!")
        return True
    except Exception as e:
        print(f"✗ Erro ao criar tabela dfe_certificados: {str(e)}")
        return False


if __name__ == '__main__':
    success = migrate_add_dfe_certificados()
    if success:
        print("\n✓ Migração concluída com sucesso!")
    else:
        print("\n✗ Migração falhou. Verifique os erros acima.")
