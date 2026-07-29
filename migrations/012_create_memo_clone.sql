-- Fase 3a — Clone de memorizações entre empresas "iguais".
--
-- Mecanismo PRÓPRIO, independente de grupos_clientes: um "set de clone" agrupa
-- empresas cujas memorizações (nfe_produto_vinculo, escopo empresa) devem ser
-- idênticas. Cada empresa participa de no máximo um set (UNIQUE em cliente_id).
--
-- memo_clone_op e as tabelas *_bkp_fase3a existem para o rollback: toda
-- clonagem registra o que criou e o que sobrescreveu.

CREATE TABLE IF NOT EXISTS memo_clone_set (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    criado_em TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS memo_clone_membro (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    set_id     INT NOT NULL,
    cliente_id INT NOT NULL,
    criado_em  TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_membro_cliente (cliente_id),
    INDEX idx_set (set_id),
    CONSTRAINT fk_memo_clone_membro_set
        FOREIGN KEY (set_id) REFERENCES memo_clone_set(id) ON DELETE CASCADE,
    CONSTRAINT fk_memo_clone_membro_cliente
        FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Uma linha por execução de clonagem — é a âncora do rollback.
CREATE TABLE IF NOT EXISTS memo_clone_op (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    set_id    INT NOT NULL,
    criado_em TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_set (set_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Regras criadas (acao='INSERT') ou sobrescritas (acao='UPDATE') pela clonagem.
CREATE TABLE IF NOT EXISTS memo_clone_regras_bkp_fase3a (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    op_id              INT NOT NULL,
    acao               VARCHAR(10) NOT NULL,
    vinculo_id         INT NULL,
    cliente_id         INT NOT NULL,
    emit_cnpj          VARCHAR(18) NOT NULL,
    codigo_produto_xml VARCHAR(60) NOT NULL,
    produto_antes      INT NULL,
    produto_depois     INT NOT NULL,
    backup_em          TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_op (op_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Itens reclassificados pelo retroativo da clonagem.
CREATE TABLE IF NOT EXISTS memo_clone_itens_bkp_fase3a (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    op_id          INT NOT NULL,
    item_id        INT NOT NULL,
    produto_antes  INT NULL,
    produto_depois INT NOT NULL,
    cliente_id     INT NULL,
    emit_cnpj      VARCHAR(18) NULL,
    codigo_produto VARCHAR(60) NULL,
    backup_em      TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_op (op_id),
    INDEX idx_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
