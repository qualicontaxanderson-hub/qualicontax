"""
Script de migração: cadastro de CONTADOR e o vínculo N:N cliente <-> contador.

Modelo: "contador" não é uma entidade separada — é um CADASTRO de cliente (PF ou
PJ) marcado com ``is_contador=1``, com o certificado digital DELE (vinculação
normal, titular == cadastro). Um cliente pode apontar para VÁRIOS contadores.

- clientes.is_contador  TINYINT(1) NOT NULL DEFAULT 0
- cliente_contadores    (cliente_id, contador_id, finalidade)

Ambas as FKs apontam para clientes(id): o vínculo liga dois cadastros.
Idempotente e reversível.

  python migrations/add_cliente_contadores.py             # aplica
  python migrations/add_cliente_contadores.py --reverter  # desfaz
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query, get_last_db_error  # noqa: E402


def _migrate(sql):
    """Executa um DDL de forma FATAL (mesma semântica do init_db)."""
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {sql[:180]}')


def _coluna_existe(tabela, coluna):
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (tabela, coluna), fetch=True, fetch_one=True,
    ) or {}
    return r.get('cnt', 0) > 0


def _tabela_existe(tabela):
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (tabela,), fetch=True, fetch_one=True,
    ) or {}
    return r.get('cnt', 0) > 0


def migrate_add_cliente_contadores():
    """Cria clientes.is_contador + tabela cliente_contadores (idempotente)."""
    print("Iniciando migração: cadastro de contador + vínculo N:N...")

    if _coluna_existe('clientes', 'is_contador'):
        print("✓ Coluna clientes.is_contador já existe")
    else:
        _migrate("ALTER TABLE clientes ADD COLUMN is_contador TINYINT(1) NOT NULL DEFAULT 0")
        print("✓ Coluna clientes.is_contador adicionada (default 0)")

    # UNIQUE (cliente_id, contador_id): o mesmo contador não entra duas vezes na
    # mesma empresa. A finalidade é rótulo livre e NÃO faz parte da chave — trocar
    # a finalidade é editar/religar o vínculo, não criar um segundo.
    _migrate("""
        CREATE TABLE IF NOT EXISTS cliente_contadores (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id  INT          NOT NULL,
            contador_id INT          NOT NULL,
            finalidade  VARCHAR(30)  NULL,
            criado_em   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_cliente_contador (cliente_id, contador_id),
            KEY ix_cc_contador (contador_id),
            CONSTRAINT fk_cc_cliente  FOREIGN KEY (cliente_id)
                REFERENCES clientes(id) ON DELETE CASCADE,
            CONSTRAINT fk_cc_contador FOREIGN KEY (contador_id)
                REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    print("✓ Tabela cliente_contadores pronta")
    return True


def rollback_cliente_contadores():
    """Desfaz: DROPa a tabela de vínculos e a coluna is_contador."""
    print("Revertendo: cadastro de contador + vínculo N:N...")
    if _tabela_existe('cliente_contadores'):
        n = (execute_query("SELECT COUNT(*) AS cnt FROM cliente_contadores",
                           fetch=True, fetch_one=True) or {}).get('cnt', 0)
        if n:
            print(f"  ATENÇÃO: {n} vínculo(s) cliente<->contador serão PERDIDOS.")
        _migrate("DROP TABLE cliente_contadores")
        print("✓ Tabela cliente_contadores removida")
    else:
        print("✓ Tabela cliente_contadores já não existe")

    if _coluna_existe('clientes', 'is_contador'):
        n = (execute_query("SELECT COUNT(*) AS cnt FROM clientes WHERE is_contador = 1",
                           fetch=True, fetch_one=True) or {}).get('cnt', 0)
        if n:
            print(f"  ATENÇÃO: {n} cadastro(s) perderão a marca de contador.")
        _migrate("ALTER TABLE clientes DROP COLUMN is_contador")
        print("✓ Coluna clientes.is_contador removida")
    else:
        print("✓ Coluna clientes.is_contador já não existe")
    return True


if __name__ == '__main__':
    reverter = '--reverter' in sys.argv
    try:
        rollback_cliente_contadores() if reverter else migrate_add_cliente_contadores()
        print("\n✓ Migração concluída com sucesso!")
    except Exception as e:
        print(f"\n✗ Migração falhou: {e}")
        sys.exit(1)
