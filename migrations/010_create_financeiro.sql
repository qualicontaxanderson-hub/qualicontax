-- =========================================================
-- Migration 010: Módulo Financeiro - Recebimentos
-- =========================================================

-- Tabela de empresas (usado como filtro nos lançamentos)
CREATE TABLE IF NOT EXISTS empresas (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    nome        VARCHAR(200)   NOT NULL,
    cnpj        VARCHAR(18)    DEFAULT NULL,
    situacao    ENUM('ATIVO','INATIVO') NOT NULL DEFAULT 'ATIVO',
    criado_em   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de contas bancárias / caixas
CREATE TABLE IF NOT EXISTS contas_bancarias (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    empresa_id  INT            NOT NULL,
    descricao   VARCHAR(150)   NOT NULL,
    banco       VARCHAR(100)   DEFAULT NULL,
    agencia     VARCHAR(20)    DEFAULT NULL,
    conta       VARCHAR(30)    DEFAULT NULL,
    situacao    ENUM('ATIVO','INATIVO') NOT NULL DEFAULT 'ATIVO',
    criado_em   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_empresa (empresa_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de formas de recebimento
CREATE TABLE IF NOT EXISTS formas_recebimento (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    descricao   VARCHAR(100)   NOT NULL,
    situacao    ENUM('ATIVO','INATIVO') NOT NULL DEFAULT 'ATIVO',
    criado_em   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Insere formas de recebimento padrão (se não existir)
INSERT IGNORE INTO formas_recebimento (id, descricao) VALUES
(1, 'Dinheiro'),
(2, 'Cartão de Crédito'),
(3, 'Cartão de Débito'),
(4, 'Transferência Bancária'),
(5, 'PIX'),
(6, 'Boleto'),
(7, 'Cheque');

-- Tabela principal de lançamentos de recebimento
CREATE TABLE IF NOT EXISTS lancamentos_recebimento (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    empresa_id            INT            NOT NULL,
    conta_id              INT            NOT NULL,
    forma_recebimento_id  INT            DEFAULT NULL,
    descricao             VARCHAR(255)   NOT NULL,
    valor                 DECIMAL(12,2)  NOT NULL DEFAULT 0.00,
    data_lancamento       DATE           NOT NULL,
    data_vencimento       DATE           DEFAULT NULL,
    data_pagamento        DATE           DEFAULT NULL,
    status                ENUM('PENDENTE','RECEBIDO','CANCELADO','ESTORNADO')
                          NOT NULL DEFAULT 'PENDENTE',
    observacao            TEXT           DEFAULT NULL,
    criado_em             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em         TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_empresa    (empresa_id),
    INDEX idx_conta      (conta_id),
    INDEX idx_status     (status),
    INDEX idx_data       (data_lancamento)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
