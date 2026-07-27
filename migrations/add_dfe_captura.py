"""
Script de migração: infraestrutura de captura automática de DFe (Fase 1).

ATIVAS: ``dfe_nsu`` (cursor/cota) e ``dfe_consulta_log`` (observabilidade) — o
motor ainda usa as duas. As demais (``dfe_documentos``/``dfe_itens``/``dfe_eventos``)
são LEGADO: na Fase 3 o motor passou a gravar a nota DIRETO na conf-compras
(``nfe_importacoes``/``nfe_itens``, origem='SEFAZ') — ver dfe_captura.SQL_NOTA_UPSERT.
Ficam criadas (sem drop) mas não recebem mais escrita.

- Adiciona ``modo_automatico``/``ativo`` em ``dfe_certificados`` (opt-in ligado).
- Cria ``dfe_nsu``          (controle de NSU + trava de cota proximo_permitido). [ATIVA]
- Cria ``dfe_documentos``   (legado — não mais escrita; motor grava em nfe_importacoes).
- Cria ``dfe_itens``        (legado — idem).
- Cria ``dfe_eventos``      (legado — idem; cancelamento vira flag em nfe_importacoes).
- Cria ``dfe_consulta_log`` (observabilidade: 1 linha por rodada/consulta). [ATIVA]

CT-e fica para fase futura (dfe_cte/dfe_cte_nfe NÃO são criadas aqui).
Idempotente — seguro rodar múltiplas vezes. Espelha o que ``run_migrations()``
aplica no boot; existe para rodar isoladamente (``python migrations/add_dfe_captura.py``).
"""

from utils.db_helper import execute_query, get_last_db_error


def _migrate(sql, fetch=False):
    """Executa um DDL de migration de forma FATAL (mesma semântica do init_db).

    Se o DDL falhar, levanta RuntimeError com a query + erro real do banco, em vez
    de deixar o execute_query engolir e o script imprimir "✓ pronta" mentindo.
    """
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


def migrate_add_dfe_captura():
    """Aplica a infra de captura de DFe (flags + tabelas do motor NH)."""

    print("Iniciando migração: infraestrutura de captura de DFe...")

    # 1) Flags de captura automática em dfe_certificados (opt-in ligado).
    _add_column_if_missing('dfe_certificados', 'modo_automatico', 'TINYINT(1) NOT NULL DEFAULT 1')
    _add_column_if_missing('dfe_certificados', 'ativo', 'TINYINT(1) NOT NULL DEFAULT 1')

    # 2) Controle de NSU por empresa (proximo_permitido = trava de cota 656).
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_nsu (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id        INT           NOT NULL,
            cnpj              VARCHAR(14)   NOT NULL,
            ult_nsu           BIGINT        NOT NULL DEFAULT 0,
            max_nsu           BIGINT        NOT NULL DEFAULT 0,
            ult_consulta      DATETIME      NULL,
            proximo_permitido DATETIME      NULL,
            ult_status        VARCHAR(255)  NULL,
            atualizado_em     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_nsu_cliente (cliente_id),
            KEY ix_nsu_cnpj (cnpj),
            CONSTRAINT fk_dfensu_cliente FOREIGN KEY (cliente_id)
                REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)
    print("✓ Tabela dfe_nsu pronta")

    # 3) Documentos DFe (SÓ notas; eventos vão em dfe_eventos). UNIQUE(chave):
    #    resumo + completa viram UMA linha; upsert nunca rebaixa completa→resumo.
    #    expurgado/expurgado_em: aditivos do Qualicontax p/ o expurgo em lote.
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_documentos (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id        INT           NOT NULL,
            chave             CHAR(44)      NOT NULL,
            tipo              VARCHAR(6)    NOT NULL,
            nsu               BIGINT        NULL,
            schema_dfe        VARCHAR(40)   NULL,
            resumo            TINYINT(1)    NOT NULL DEFAULT 0,
            numero            VARCHAR(20)   NULL,
            serie             VARCHAR(6)    NULL,
            modelo            VARCHAR(4)    NULL,
            dh_emissao        DATETIME      NULL,
            emit_cnpj         VARCHAR(14)   NULL,
            emit_nome         VARCHAR(160)  NULL,
            dest_cnpj         VARCHAR(14)   NULL,
            valor_total       DECIMAL(14,2) NULL,
            situacao          VARCHAR(20)   NOT NULL DEFAULT 'autorizado',
            manifesto         VARCHAR(20)   NULL,
            xml_caminho       VARCHAR(300)  NULL,
            xml_expira_em     DATE          NULL,
            expurgado         TINYINT(1)    NOT NULL DEFAULT 0,
            expurgado_em      DATE          NULL,
            conferido         TINYINT(1)    NOT NULL DEFAULT 0,
            criado_em         TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_doc_chave (chave),
            KEY ix_doc_cliente (cliente_id),
            KEY ix_doc_tipo (tipo),
            KEY ix_doc_emit (emit_cnpj),
            KEY ix_doc_dh (dh_emissao),
            KEY ix_doc_situacao (situacao),
            KEY ix_doc_expira (xml_expira_em),
            CONSTRAINT fk_dfedoc_cliente FOREIGN KEY (cliente_id)
                REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)
    print("✓ Tabela dfe_documentos pronta")

    # 4) Itens/produtos de cada nota. cod_anp/produto_id (combustível NH) ficam
    #    NULL aqui, mas mantidos p/ o INSERT do motor casar sem reescrita.
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_itens (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            documento_id      INT            NOT NULL,
            n_item            INT            NOT NULL,
            produto_xml       VARCHAR(160)   NULL,
            cprod_fornecedor  VARCHAR(60)    NULL,
            cean              VARCHAR(20)    NULL,
            cod_anp           VARCHAR(12)    NULL,
            produto_id        INT            NULL,
            ncm               VARCHAR(10)    NULL,
            unidade           VARCHAR(6)     NULL,
            quantidade        DECIMAL(15,4)  NULL,
            valor_unitario    DECIMAL(15,6)  NULL,
            valor_total       DECIMAL(14,2)  NULL,
            criado_em         TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_item (documento_id, n_item),
            KEY ix_item_anp (cod_anp),
            KEY ix_item_produto (produto_id),
            CONSTRAINT fk_dfeitem_doc FOREIGN KEY (documento_id)
                REFERENCES dfe_documentos(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)
    print("✓ Tabela dfe_itens pronta")

    # 5) Eventos (procEventoNFe). Isolada — uma nota tem VÁRIOS eventos.
    #    Liga na nota por ch_nfe (valor, não FK): evento pode chegar antes da nota.
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_eventos (
            id            INT           AUTO_INCREMENT PRIMARY KEY,
            cliente_id    INT           NOT NULL,
            chave_evento  VARCHAR(60)   NOT NULL,
            ch_nfe        CHAR(44)      NOT NULL,
            tp_evento     VARCHAR(6)    NULL,
            n_seq         INT           NULL,
            descricao     VARCHAR(160)  NULL,
            dh_evento     DATETIME      NULL,
            nsu           BIGINT        NULL,
            schema_dfe    VARCHAR(40)   NULL,
            org_cnpj      VARCHAR(14)   NULL,
            xml_caminho   VARCHAR(300)  NULL,
            xml_expira_em DATE          NULL,
            criado_em     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_chave_evento (chave_evento),
            KEY ix_ev_chnfe (ch_nfe),
            KEY ix_ev_tp (tp_evento),
            KEY ix_ev_expira (xml_expira_em),
            CONSTRAINT fk_dfeev_cliente FOREIGN KEY (cliente_id)
                REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)
    print("✓ Tabela dfe_eventos pronta")

    # 6) Log de consultas (schema idêntico ao dfe_log do NH). Sem FK: logar NUNCA
    #    pode derrubar a captura (best-effort).
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_consulta_log (
            id            BIGINT       AUTO_INCREMENT PRIMARY KEY,
            momento       DATETIME     NOT NULL,
            origem        VARCHAR(12)  NOT NULL,
            evento        VARCHAR(14)  NOT NULL,
            cliente_id    INT          NULL,
            cnpj          CHAR(14)     NULL,
            ult_nsu_env   BIGINT       NULL,
            c_stat        VARCHAR(6)   NULL,
            x_motivo      VARCHAR(300) NULL,
            ret_ult_nsu   BIGINT       NULL,
            ret_max_nsu   BIGINT       NULL,
            docs          INT          NOT NULL DEFAULT 0,
            notas         INT          NOT NULL DEFAULT 0,
            eventos       INT          NOT NULL DEFAULT 0,
            lote          INT          NULL,
            detalhe       VARCHAR(300) NULL,
            criado_em     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY ix_log_momento (momento),
            KEY ix_log_cstat (c_stat),
            KEY ix_log_origem_evento (origem, evento)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)
    print("✓ Tabela dfe_consulta_log pronta")

    return True


if __name__ == '__main__':
    import sys
    try:
        migrate_add_dfe_captura()
        print("\n✓ Migração concluída com sucesso!")
    except Exception as e:
        print(f"\n✗ Migração falhou: {e}")
        sys.exit(1)
