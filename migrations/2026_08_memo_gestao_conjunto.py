"""
Migration D3.1 Etapa 3 — Gestão do conjunto de memorizações.

Idempotente (seguro rodar múltiplas vezes). Deliberadamente FORA do boot
(init_db.run_migrations NÃO a aplica) — roda por ordem explícita, não no deploy,
porque o Anderson quer revisar a sequência antes de a coluna/tabela existirem
no banco de verdade.

Aplica:
  1. memo_clone_set    + nome       VARCHAR(120) NULL   (nome do conjunto)
  2. memo_clone_membro + corte_data DATE NULL           (competência de emissão
                                                          escolhida ao incluir; NULL = todos)
  3. memo_desvinculo_op   (nova)  — âncora do op_id do desvincular
  4. memo_desvinculo_bkp  (nova)  — rede de proteção (Opção B): as linhas
                                    removidas vão para cá ANTES do DELETE

Como rodar (no serviço web, com o mesmo ambiente do app):
    python migrations/2026_08_memo_gestao_conjunto.py
Sai com código 0 em sucesso; imprime o que fez.
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.db_helper import execute_query   # noqa: E402


def _col_existe(tabela, coluna):
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (tabela, coluna), fetch=True, fetch_one=True,
    ) or {}
    return int(r.get('cnt', 0)) > 0


def aplicar():
    feitos = []

    # 1 + 2 — colunas incrementais (checa antes do ALTER).
    # departamento: escopo do conjunto. NOT NULL DEFAULT 'FISCAL' => o conjunto
    # existente (o dos 6 postos) é marcado FISCAL automaticamente no ADD COLUMN.
    for tabela, coluna, definicao in [
        ('memo_clone_set',    'nome',         'VARCHAR(120) NULL'),
        ('memo_clone_membro', 'corte_data',   'DATE NULL'),
        ('memo_clone_set',    'departamento', "VARCHAR(20) NOT NULL DEFAULT 'FISCAL'"),
    ]:
        if _col_existe(tabela, coluna):
            feitos.append(f'= {tabela}.{coluna} já existe')
        else:
            execute_query(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}", fetch=False)
            feitos.append(f'+ {tabela}.{coluna} adicionada')

    # 3 — operação de desvincular (âncora do op_id)
    execute_query("""
        CREATE TABLE IF NOT EXISTS memo_desvinculo_op (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            set_id     INT NULL,
            cliente_id INT NOT NULL,
            modo       VARCHAR(10) NOT NULL,
            corte_data DATE NULL,
            criado_em  TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            criado_por INT NULL,
            INDEX idx_set (set_id),
            INDEX idx_cliente (cliente_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)
    feitos.append('~ memo_desvinculo_op garantida (CREATE IF NOT EXISTS)')

    # 4 — rede de proteção do desvincular (Opção B)
    execute_query("""
        CREATE TABLE IF NOT EXISTS memo_desvinculo_bkp (
            id                    INT AUTO_INCREMENT PRIMARY KEY,
            op_id                 INT NOT NULL,
            vinculo_id            INT NULL,
            cliente_id            INT NOT NULL,
            grupo_id              INT NULL,
            ramo_atividade_id     INT NULL,
            emit_cnpj             VARCHAR(18) NOT NULL DEFAULT '',
            codigo_produto_xml    VARCHAR(60) NOT NULL DEFAULT '',
            descricao_produto_xml VARCHAR(500) NULL,
            produto_catalogo_id   INT NULL,
            tipo                  VARCHAR(10) NULL,
            removido_em           TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
            removido_por          INT NULL,
            INDEX idx_op (op_id),
            INDEX idx_cliente (cliente_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)
    feitos.append('~ memo_desvinculo_bkp garantida (CREATE IF NOT EXISTS)')

    return feitos


if __name__ == '__main__':
    print('D3.1 Etapa 3 — migration da gestão do conjunto')
    for linha in aplicar():
        print('  ', linha)
    print('OK — idempotente, rode de novo à vontade.')
