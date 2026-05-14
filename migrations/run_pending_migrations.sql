-- Script para aplicar todas as migrations pendentes no banco de produção
-- Execute via DBeaver ou Railway CLI

-- Migration 1: coluna nome na tabela empresas
ALTER TABLE empresas
    ADD COLUMN IF NOT EXISTS nome VARCHAR(200) NOT NULL DEFAULT '' AFTER id;

-- Migration 2: coluna areas_atendimento na tabela contatos_clientes
ALTER TABLE contatos_clientes
    ADD COLUMN IF NOT EXISTS areas_atendimento TEXT NULL AFTER departamento;

-- Migration 3: tabela cadastros_adicionais_clientes
CREATE TABLE IF NOT EXISTS cadastros_adicionais_clientes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT NOT NULL,
    tipo VARCHAR(100) NOT NULL,
    campo VARCHAR(150) NOT NULL,
    valor VARCHAR(255) NULL,
    data_referencia DATE NULL,
    observacoes TEXT NULL,
    ativo TINYINT(1) NOT NULL DEFAULT 1,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
    INDEX idx_cliente_id (cliente_id),
    INDEX idx_tipo (tipo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Migration 4: tabela socios_clientes
CREATE TABLE IF NOT EXISTS socios_clientes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT NOT NULL,
    nome VARCHAR(255) NOT NULL,
    cpf VARCHAR(14) NOT NULL,
    email VARCHAR(255) NULL,
    telefone VARCHAR(20) NULL,
    percentual_participacao DECIMAL(5,2) NOT NULL,
    responsavel TINYINT(1) NOT NULL DEFAULT 0,
    ativo TINYINT(1) NOT NULL DEFAULT 1,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
    INDEX idx_cliente_id (cliente_id),
    INDEX idx_cpf (cpf)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
