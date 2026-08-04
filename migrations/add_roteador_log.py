"""
Script de migração: tabela ``roteador_log`` — trilha e rede de REVERSÃO do
roteador de arquivos fiscais (cron_roteador.py).

No backlog local a reversão morava num CSV (_LOG_MOVIMENTACAO.csv). Na nuvem não
há disco persistente, então a trilha vira tabela: cada arquivo avaliado numa
rodada grava UMA linha, e reverter é reler as linhas ``resultado='MOVIDO'`` na
ordem inversa e chamar move_file(destino -> origem).

Idempotente e reversível.

  python migrations/add_roteador_log.py             # aplica
  python migrations/add_roteador_log.py --reverter  # DROPa a tabela
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query, get_last_db_error  # noqa: E402

TABELA = 'roteador_log'


def _migrate(sql):
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {sql[:180]}')


def _tabela_existe():
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABELA,), fetch=True, fetch_one=True,
    ) or {}
    return r.get('cnt', 0) > 0


def migrate_add_roteador_log():
    """Cria roteador_log (idempotente)."""
    print(f"Iniciando migração: tabela {TABELA}...")
    # origem/destino em VARCHAR(500): mesmo teto do dfe_certificados.dropbox_path.
    # Sem FK para clientes: a trilha precisa sobreviver à exclusão de um cliente
    # (senão o ON DELETE apagaria justamente o registro que permite reverter).
    _migrate("""
        CREATE TABLE IF NOT EXISTS roteador_log (
            id             BIGINT AUTO_INCREMENT PRIMARY KEY,
            rodada         CHAR(14)     NOT NULL,
            origem         VARCHAR(500) NOT NULL,
            destino        VARCHAR(500) NULL,
            resultado      VARCHAR(20)  NOT NULL,
            tipo_doc       VARCHAR(14)  NULL,
            empresa_numero VARCHAR(20)  NULL,
            motivo         VARCHAR(200) NULL,
            criado_em      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY ix_rot_rodada (rodada),
            KEY ix_rot_resultado (resultado),
            KEY ix_rot_criado (criado_em),
            KEY ix_rot_origem (origem(191))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    print(f"✓ Tabela {TABELA} pronta")
    return True


def rollback_roteador_log():
    """Remove a tabela. ATENÇÃO: perde a trilha de reversão."""
    print(f"Revertendo: tabela {TABELA}...")
    if not _tabela_existe():
        print(f"✓ Tabela {TABELA} já não existe")
        return True
    n = (execute_query(f"SELECT COUNT(*) AS cnt FROM {TABELA} WHERE resultado='MOVIDO'",
                       fetch=True, fetch_one=True) or {}).get('cnt', 0)
    if n:
        print(f"  ATENÇÃO: {n} movimentação(ões) perderão a trilha de reversão.")
    _migrate(f"DROP TABLE {TABELA}")
    print(f"✓ Tabela {TABELA} removida")
    return True


if __name__ == '__main__':
    try:
        if '--reverter' in sys.argv:
            rollback_roteador_log()
        else:
            migrate_add_roteador_log()
        print("\n✓ Migração concluída com sucesso!")
    except Exception as e:
        print(f"\n✗ Migração falhou: {e}")
        sys.exit(1)
