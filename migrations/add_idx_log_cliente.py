"""
Script de migração: índice (cliente_id, momento) em ``dfe_consulta_log`` (aditiva).

Por que existe
--------------
O Status SEFAZ (``/escrita-fiscal/status-sefaz/``) passou a mostrar, por
empresa: a última consulta à SEFAZ, o último sucesso, a taxa de 656 em 7 dias,
quando cada empresa foi capturada e o histórico de 30 dias. Tudo isso agrega
``dfe_consulta_log`` POR cliente_id — ~120k linhas em 30 dias — e a tabela só
tinha índice por ``momento``, ``c_stat`` e ``(origem, evento)``. Cada empresa
virava um full scan: a tela levava 26 s e a lista de travadas 15 s (06/09/2026).

Índice
------
``dfe_consulta_log.ix_log_cliente_momento (cliente_id, momento)``

ADITIVA e idempotente: só cria se faltar (INFORMATION_SCHEMA.STATISTICS), não
reescreve linha nenhuma. Espelha o que ``run_migrations()`` aplica no boot;
existe para rodar isoladamente (``python migrations/add_idx_log_cliente.py``).
"""

from utils.db_helper import execute_query, get_last_db_error

TABELA, INDICE, COLUNAS = 'dfe_consulta_log', 'ix_log_cliente_momento', 'cliente_id, momento'


def migrate_add_idx_log_cliente():
    """Cria o índice (cliente_id, momento) do log de consultas, se faltar."""
    print('Iniciando migração: índice ix_log_cliente_momento em dfe_consulta_log...')

    existe = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
        (TABELA, INDICE), fetch=True, fetch_one=True,
    ) or {}
    if existe.get('cnt', 0) > 0:
        print(f'  OK  {TABELA}.{INDICE} ja existe — nada a fazer.')
        return True

    ddl = f"ALTER TABLE {TABELA} ADD INDEX {INDICE} ({COLUNAS})"
    if execute_query(ddl, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexao?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {ddl}')
    print(f'  OK  {TABELA}.{INDICE} criado ({COLUNAS})')
    return True


if __name__ == '__main__':
    import sys
    try:
        migrate_add_idx_log_cliente()
        print('\nMigracao concluida com sucesso.')
    except Exception as e:
        print(f'\nMigracao FALHOU: {e}')
        sys.exit(1)
