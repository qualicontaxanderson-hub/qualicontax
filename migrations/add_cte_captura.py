"""
Script de migração: infraestrutura de captura de CT-e (fretes).

Fatia 1 do CT-e: tabela PRÓPRIA (``cte_documentos``), independente de
``nfe_importacoes`` — CT-e não tem itens/produtos, tem partes (tomador, remetente,
destinatário, expedidor, recebedor) e as NF-e transportadas.

- Cria ``cte_documentos``  (cabeçalho do CT-e; 1 linha por chave POR cliente).
- Cria ``cte_nfe``         (as NF-e transportadas por cada CT-e).
- Cria ``dfe_nsu_cte``     (cursor/cota do CTeDistribuicaoDFe — ISOLADO do dfe_nsu).
- Adiciona ``servico`` em ``dfe_consulta_log`` ('nfe' | 'cte').

Por que o cursor é uma tabela separada e não uma coluna em ``dfe_nsu``: o
CTeDistribuicaoDFe é OUTRO webservice, com sequência de NSU e cota (656) próprias.
Reaproveitar ``dfe_nsu`` exigiria trocar a ``UNIQUE KEY uq_nsu_cliente`` e reescrever
os SQL_NSU_OK/SQL_NSU_656 do motor de NF-e, que está em produção drenando. Tabela
isolada = risco zero no que já funciona.

Idempotente — seguro rodar múltiplas vezes. Espelha o que ``run_migrations()``
aplica no boot; existe para rodar isoladamente (``python migrations/add_cte_captura.py``).
"""

from utils.db_helper import execute_query, get_last_db_error


def _migrate(sql, fetch=False):
    """Executa um DDL de migration de forma FATAL (mesma semântica do init_db)."""
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        primeira = next((ln.strip() for ln in sql.splitlines() if ln.strip()), sql.strip())
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {primeira[:180]}')


def _add_column_if_missing(table, column, definition):
    """Adiciona uma coluna se ela ainda não existir (idempotente)."""
    exists = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column), fetch=True, fetch_one=True,
    ) or {}
    if exists.get('cnt', 0) == 0:
        _migrate(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        print(f"✓ Coluna {table}.{column} adicionada")
    else:
        print(f"✓ Coluna {table}.{column} já existe")


# ==========================================================================
# DDL — compartilhado com init_db.run_migrations() (a fonte no boot).
# Mantenha os dois em sincronia: este arquivo é o espelho para rodar à mão.
# ==========================================================================

# Cabeçalho do CT-e. UNIQUE (chave_acesso, cliente_id): o mesmo CT-e pode
# interessar a DOIS clientes do escritório (ex.: a transportadora é cliente e o
# tomador também) — cada um tem a sua linha, com o papel real registrado em
# papel_cliente. Sem FK para clientes (mesmo padrão de nfe_importacoes, que usa
# só INDEX): cliente_id NULL = CT-e ainda não atribuído a uma empresa.
SQL_CTE_DOCUMENTOS = """
CREATE TABLE IF NOT EXISTS cte_documentos (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id        INT           NULL,
    grupo_id          INT           NULL,
    papel_cliente     VARCHAR(14)   NOT NULL DEFAULT 'tomador',
    chave_acesso      CHAR(44)      NOT NULL,
    modelo            VARCHAR(2)    NULL,
    num_cte           VARCHAR(20)   DEFAULT '',
    serie             VARCHAR(6)    DEFAULT '',
    data_emissao      DATE          NULL,
    dh_emissao        DATETIME      NULL,
    cfop              VARCHAR(10)   DEFAULT '',
    natureza_operacao VARCHAR(255)  DEFAULT '',
    tp_cte            VARCHAR(1)    NULL,
    tp_serv           VARCHAR(1)    NULL,
    modal             VARCHAR(2)    NULL,
    emit_cnpj         VARCHAR(18)   DEFAULT '',
    emit_nome         VARCHAR(255)  DEFAULT '',
    emit_uf           VARCHAR(2)    DEFAULT '',
    rem_cnpj          VARCHAR(18)   DEFAULT '',
    rem_nome          VARCHAR(255)  DEFAULT '',
    rem_uf            VARCHAR(2)    DEFAULT '',
    dest_cnpj         VARCHAR(18)   DEFAULT '',
    dest_nome         VARCHAR(255)  DEFAULT '',
    dest_uf           VARCHAR(2)    DEFAULT '',
    exped_cnpj        VARCHAR(18)   DEFAULT '',
    exped_nome        VARCHAR(255)  DEFAULT '',
    receb_cnpj        VARCHAR(18)   DEFAULT '',
    receb_nome        VARCHAR(255)  DEFAULT '',
    toma_cod          VARCHAR(1)    NULL,
    tomador_cnpj      VARCHAR(18)   DEFAULT '',
    tomador_nome      VARCHAR(255)  DEFAULT '',
    tomador_papel     VARCHAR(14)   DEFAULT '',
    uf_ini            VARCHAR(2)    DEFAULT '',
    mun_ini           VARCHAR(120)  DEFAULT '',
    uf_fim            VARCHAR(2)    DEFAULT '',
    mun_fim           VARCHAR(120)  DEFAULT '',
    valor_frete       DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    valor_receber     DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    valor_bc_icms     DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    valor_icms        DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    aliq_icms         DECIMAL(7,4)  NOT NULL DEFAULT 0.0000,
    cst_icms          VARCHAR(2)    DEFAULT '',
    valor_tot_trib    DECIMAL(15,2) NOT NULL DEFAULT 0.00,
    cancelado         TINYINT(1)    NOT NULL DEFAULT 0,
    protocolo         VARCHAR(20)   DEFAULT '',
    nsu               BIGINT        NULL,
    origem            ENUM('UPLOAD','DROPBOX','SEFAZ') NOT NULL DEFAULT 'SEFAZ',
    nome_arquivo      VARCHAR(500)  NOT NULL DEFAULT '',
    xml_raw           MEDIUMTEXT    NULL,
    xml_caminho       VARCHAR(300)  NULL,
    importado_em      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_cte_chave_cliente (chave_acesso, cliente_id),
    KEY ix_cte_cliente (cliente_id),
    KEY ix_cte_grupo (grupo_id),
    KEY ix_cte_chave (chave_acesso),
    KEY ix_cte_emit (emit_cnpj),
    KEY ix_cte_tomador (tomador_cnpj),
    KEY ix_cte_data (data_emissao),
    KEY ix_cte_origem (origem)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# NF-e transportadas pelo CT-e (infCTeNorm/infDoc/infNFe — e o infNF antigo).
# É o "detalhe" do CT-e: permite cruzar frete × nota da Conferência de Compras
# por chave_nfe. DELETE+re-INSERT por CT-e (idempotente) no core de gravação.
SQL_CTE_NFE = """
CREATE TABLE IF NOT EXISTS cte_nfe (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    cte_id     INT           NOT NULL,
    chave_nfe  CHAR(44)      NULL,
    num_nota   VARCHAR(20)   DEFAULT '',
    serie      VARCHAR(6)    DEFAULT '',
    valor      DECIMAL(15,2) NULL,
    criado_em  TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_cte_nfe (cte_id, chave_nfe),
    KEY ix_ctenfe_chave (chave_nfe),
    CONSTRAINT fk_ctenfe_cte FOREIGN KEY (cte_id)
        REFERENCES cte_documentos(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# Cursor/cota do CTeDistribuicaoDFe — cópia fiel do dfe_nsu, tabela separada.
SQL_DFE_NSU_CTE = """
CREATE TABLE IF NOT EXISTS dfe_nsu_cte (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id        INT           NOT NULL,
    cnpj              VARCHAR(14)   NOT NULL,
    ult_nsu           BIGINT        NOT NULL DEFAULT 0,
    max_nsu           BIGINT        NOT NULL DEFAULT 0,
    ult_consulta      DATETIME      NULL,
    proximo_permitido DATETIME      NULL,
    ult_status        VARCHAR(255)  NULL,
    atualizado_em     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_nsucte_cliente (cliente_id),
    KEY ix_nsucte_cnpj (cnpj),
    CONSTRAINT fk_dfensucte_cliente FOREIGN KEY (cliente_id)
        REFERENCES clientes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def migrate_add_cte_captura():
    """Aplica a infra de captura de CT-e (tabelas + cursor + coluna de log)."""

    print("Iniciando migração: infraestrutura de captura de CT-e...")

    _migrate(SQL_CTE_DOCUMENTOS, fetch=False)
    print("✓ Tabela cte_documentos pronta")

    _migrate(SQL_CTE_NFE, fetch=False)
    print("✓ Tabela cte_nfe pronta")

    _migrate(SQL_DFE_NSU_CTE, fetch=False)
    print("✓ Tabela dfe_nsu_cte pronta")

    # Log compartilhado entre as duas capturas: 'nfe' (default, preserva o
    # histórico já gravado) | 'cte'. O dfe_log tem fallback para o schema antigo,
    # então o cron continua logando mesmo antes desta coluna existir.
    _add_column_if_missing('dfe_consulta_log', 'servico',
                           "VARCHAR(4) NOT NULL DEFAULT 'nfe'")

    return True


if __name__ == '__main__':
    import sys
    try:
        migrate_add_cte_captura()
        print("\n✓ Migração concluída com sucesso!")
    except Exception as e:
        print(f"\n✗ Migração falhou: {e}")
        sys.exit(1)
