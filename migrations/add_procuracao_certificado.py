"""
Script de migração: marca vínculos de certificado por PROCURAÇÃO.

Adiciona ``procuracao`` em ``dfe_certificados``: 1 = o titular do .pfx NÃO é a
empresa (e-CPF do contador / e-CNPJ do escritório autorizado); 0 = o titular é a
própria empresa (todo o comportamento anterior).

DEFAULT 0 e ADITIVO: os vínculos existentes continuam significando exatamente o
que significavam. Idempotente — seguro rodar múltiplas vezes.

  python migrations/add_procuracao_certificado.py             # aplica
  python migrations/add_procuracao_certificado.py --reverter  # desfaz (DROP COLUMN)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query, get_last_db_error  # noqa: E402

TABELA = 'dfe_certificados'
COLUNA = 'procuracao'
DEFINICAO = 'TINYINT(1) NOT NULL DEFAULT 0'


def _migrate(sql):
    """Executa um DDL de forma FATAL (mesma semântica do init_db/add_dfe_captura)."""
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {sql[:180]}')


def _coluna_existe():
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABELA, COLUNA), fetch=True, fetch_one=True,
    ) or {}
    return r.get('cnt', 0) > 0


def migrate_add_procuracao():
    """Adiciona dfe_certificados.procuracao (idempotente)."""
    print(f"Iniciando migração: {TABELA}.{COLUNA}...")
    if _coluna_existe():
        print(f"✓ Coluna {TABELA}.{COLUNA} já existe")
        return True
    _migrate(f"ALTER TABLE {TABELA} ADD COLUMN {COLUNA} {DEFINICAO}")
    print(f"✓ Coluna {TABELA}.{COLUNA} adicionada (default 0)")
    return True


def rollback_procuracao():
    """Remove a coluna. Só o marcador se perde — nenhum certificado é afetado."""
    print(f"Revertendo: {TABELA}.{COLUNA}...")
    if not _coluna_existe():
        print(f"✓ Coluna {TABELA}.{COLUNA} já não existe")
        return True
    marcados = (execute_query(
        f"SELECT COUNT(*) AS cnt FROM {TABELA} WHERE {COLUNA} = 1",
        fetch=True, fetch_one=True,
    ) or {}).get('cnt', 0)
    if marcados:
        print(f"  ATENÇÃO: {marcados} vínculo(s) marcados como procuração perderão a marca.")
    _migrate(f"ALTER TABLE {TABELA} DROP COLUMN {COLUNA}")
    print(f"✓ Coluna {TABELA}.{COLUNA} removida")
    return True


if __name__ == '__main__':
    reverter = '--reverter' in sys.argv
    try:
        rollback_procuracao() if reverter else migrate_add_procuracao()
        print("\n✓ Migração concluída com sucesso!")
    except Exception as e:
        print(f"\n✗ Migração falhou: {e}")
        sys.exit(1)
