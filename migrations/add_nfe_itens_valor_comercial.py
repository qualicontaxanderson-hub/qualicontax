"""
Script de migração: VALOR COMERCIAL por item em ``nfe_itens`` (aditiva).

Por que existe
--------------
Para combustível (e para qualquer mercadoria com ICMS-ST), o custo real por
unidade NÃO é o ``vUnCom`` da nota: o ICMS-ST, o IPI, o frete, o seguro e as
despesas acessórias entram no custo mas ficam FORA do ``vProd``; o desconto sai.

Exemplo real — NF 1002, INTEGRACAO COMBUSTIVEIS (nfe_id 62230)::

    qCom   = 3.000,0000        vUnCom = 2,6279794128      vProd = 7.883,94
    vICMSST=   576,06
    custo_total_item     = 7.883,94 + 576,06 = 8.460,00
    valor_unit_comercial = 8.460,00 / 3.000  = 2,8200      <- o custo de verdade

Hoje ``nfe_itens`` só guarda ``vUnCom`` (``valor_unitario``) e ``vProd``
(``valor_total``); os demais valores existem apenas dentro do ``xml_raw``. Estas
duas colunas passam a materializar o cálculo.

Colunas
-------
``custo_total_item      DECIMAL(15,2) NULL``
    vProd + vICMSST + vIPI + vFrete + vSeg + vOutro - vDesc (campos do ITEM;
    ausente = 0). Mesma precisão de ``valor_total``.
``valor_unit_comercial  DECIMAL(15,4) NULL``
    custo_total_item / qCom. Mesma precisão de ``valor_unitario`` — 4 casas, que
    é o que a tela precisa mostrar (2,8200 e não 2,82).

NULL significa "ainda não calculado" (item anterior ao backfill), NUNCA "custo
zero" — por isso as colunas nascem NULL e sem DEFAULT.

ADITIVA e idempotente: só acrescenta colunas, não reescreve linha nenhuma e não
muda comportamento de código. Espelha o que ``run_migrations()`` aplica no boot;
existe para rodar isoladamente
(``python migrations/add_nfe_itens_valor_comercial.py``).
"""

from utils.db_helper import execute_query, get_last_db_error

TABELA = 'nfe_itens'
COLUNAS = [
    ('custo_total_item',     'DECIMAL(15,2) NULL'),
    ('valor_unit_comercial', 'DECIMAL(15,4) NULL'),
]


def migrate_add_nfe_itens_valor_comercial():
    """Adiciona as colunas de valor comercial em nfe_itens, se faltarem."""
    print(f'Iniciando migração: valor comercial em {TABELA}...')

    for coluna, definicao in COLUNAS:
        existe = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (TABELA, coluna), fetch=True, fetch_one=True,
        ) or {}

        if existe.get('cnt', 0) > 0:
            print(f'  OK  {TABELA}.{coluna} ja existe — nada a fazer.')
            continue

        if execute_query(f"ALTER TABLE {TABELA} ADD COLUMN {coluna} {definicao}",
                         fetch=False) is None:
            erro = get_last_db_error() or 'sem detalhe do driver (falha de conexao?)'
            raise RuntimeError(
                f'Migration abortada — {erro} | '
                f'DDL: ALTER TABLE {TABELA} ADD COLUMN {coluna} {definicao}')
        print(f'  OK  {TABELA}.{coluna} adicionada ({definicao})')

    return True


if __name__ == '__main__':
    import sys
    try:
        migrate_add_nfe_itens_valor_comercial()
        print('\nMigracao concluida com sucesso.')
    except Exception as e:
        print(f'\nMigracao FALHOU: {e}')
        sys.exit(1)
