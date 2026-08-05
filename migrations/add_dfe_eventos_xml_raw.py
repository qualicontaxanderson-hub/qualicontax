"""
Script de migração: coluna ``xml_raw`` em ``dfe_eventos`` (aditiva).

Por que existe
--------------
O evento de DFe (procEventoNFe/procEventoCTe) era o ÚNICO documento do sistema
sem cópia do XML no banco: ``dfe_eventos`` guardava só ``xml_caminho``, um
ponteiro para o arquivo no Dropbox. Isso significa que mover a pasta — ou a
janela de 90 dias da SEFAZ registrada em ``xml_expira_em`` — apaga o documento
na prática, sem nada no banco para reconstituí-lo.

NF-e (``nfe_importacoes``, ``nfe_eventos``) e CT-e (``cte_documentos``) já
guardam ``xml_raw MEDIUMTEXT NULL``. Esta migration põe o evento no mesmo
patamar; o backfill das linhas existentes é feito em separado.

ADITIVA e idempotente: só acrescenta a coluna (NULL, sem default), não reescreve
linha nenhuma e não muda comportamento de código. Seguro rodar múltiplas vezes.
Espelha o que ``run_migrations()`` aplica no boot; existe para rodar isoladamente
(``python migrations/add_dfe_eventos_xml_raw.py``).
"""

from utils.db_helper import execute_query, get_last_db_error

TABELA = 'dfe_eventos'
COLUNA = 'xml_raw'
DEFINICAO = 'MEDIUMTEXT NULL'


def migrate_add_dfe_eventos_xml_raw():
    """Adiciona dfe_eventos.xml_raw se ainda não existir."""
    print(f"Iniciando migração: coluna {TABELA}.{COLUNA}...")

    existe = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABELA, COLUNA), fetch=True, fetch_one=True,
    ) or {}

    if existe.get('cnt', 0) > 0:
        print(f"✓ Coluna {TABELA}.{COLUNA} já existe — nada a fazer.")
        return True

    if execute_query(f"ALTER TABLE {TABELA} ADD COLUMN {COLUNA} {DEFINICAO}",
                     fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | '
                           f'DDL: ALTER TABLE {TABELA} ADD COLUMN {COLUNA} {DEFINICAO}')

    print(f"✓ Coluna {TABELA}.{COLUNA} adicionada ({DEFINICAO})")
    return True


if __name__ == '__main__':
    import sys
    try:
        migrate_add_dfe_eventos_xml_raw()
        print("\n✓ Migração concluída com sucesso!")
    except Exception as e:
        print(f"\n✗ Migração falhou: {e}")
        sys.exit(1)
