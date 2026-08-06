"""
Script de migração: índices em ``importado_em`` (aditiva).

Por que existe
--------------
A home do Fiscal (painel do escritório — HOME VERDE+BRANCO, Parte 3) agrega,
via ``/escrita-fiscal/api/home-destaques``:

    - "capturadas hoje": COUNT por ``DATE(importado_em)=CURDATE()``
    - sparkline de 9 dias: COUNT por dia em ``importado_em``

nas tabelas ``nfe_importacoes`` (~40k linhas) e ``cte_documentos`` (~5k). Hoje
``importado_em`` NÃO tem índice em nenhuma das duas — a agregação vira full scan.
Com o cache de 60s do endpoint o custo é tolerável, mas o índice deixa o "hoje"
e o sparkline à prova de F5 em pico. As janelas por ``data_emissao`` já são
cobertas por ``idx_data`` / ``ix_cte_data`` e ficam de fora daqui.

Índices
-------
``nfe_importacoes.idx_importado    (importado_em)``
``cte_documentos.ix_cte_importado  (importado_em)``

ADITIVA e idempotente: só cria índice se faltar (checa INFORMATION_SCHEMA.
STATISTICS, como a migração do valor comercial), não reescreve linha nenhuma e
não muda comportamento de código. Espelha o que ``run_migrations()`` aplica no
boot; existe para rodar isoladamente
(``python migrations/add_idx_importado_em.py``).
"""

from utils.db_helper import execute_query, get_last_db_error

# (tabela, nome_do_indice, coluna)
INDICES = [
    ('nfe_importacoes', 'idx_importado',    'importado_em'),
    ('cte_documentos',  'ix_cte_importado', 'importado_em'),
]


def migrate_add_idx_importado_em():
    """Cria os índices de importado_em (nfe + cte), se faltarem."""
    print('Iniciando migração: índices importado_em (nfe + cte)...')

    for tabela, indice, coluna in INDICES:
        existe = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
            (tabela, indice), fetch=True, fetch_one=True,
        ) or {}

        if existe.get('cnt', 0) > 0:
            print(f'  OK  {tabela}.{indice} ja existe — nada a fazer.')
            continue

        if execute_query(f"ALTER TABLE {tabela} ADD INDEX {indice} ({coluna})",
                         fetch=False) is None:
            erro = get_last_db_error() or 'sem detalhe do driver (falha de conexao?)'
            raise RuntimeError(
                f'Migration abortada — {erro} | '
                f'DDL: ALTER TABLE {tabela} ADD INDEX {indice} ({coluna})')
        print(f'  OK  {tabela}.{indice} criado ({coluna})')

    return True


if __name__ == '__main__':
    import sys
    try:
        migrate_add_idx_importado_em()
        print('\nMigracao concluida com sucesso.')
    except Exception as e:
        print(f'\nMigracao FALHOU: {e}')
        sys.exit(1)
