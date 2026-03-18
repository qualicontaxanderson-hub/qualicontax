-- Migration: Criação de tabelas para Conciliação Bancária
-- Data: 2026-03-18
-- Descrição: Cria tabelas necessárias para o módulo de conciliação bancária

-- Tabela de Conciliações Bancárias
CREATE TABLE IF NOT EXISTS conciliacoes_bancarias (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT NOT NULL,
    grupo_id INT NULL,
    arquivo_ofx VARCHAR(255) NULL,
    data_importacao DATETIME NOT NULL,
    periodo_inicial DATE NULL,
    periodo_final DATE NULL,
    saldo_inicial DECIMAL(15, 2) DEFAULT 0.00,
    saldo_final DECIMAL(15, 2) DEFAULT 0.00,
    status VARCHAR(20) DEFAULT 'PENDENTE',
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
    FOREIGN KEY (grupo_id) REFERENCES grupos_clientes(id) ON DELETE SET NULL,
    
    INDEX idx_cliente (cliente_id),
    INDEX idx_grupo (grupo_id),
    INDEX idx_status (status),
    INDEX idx_data_importacao (data_importacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de Transações Bancárias
CREATE TABLE IF NOT EXISTS transacoes_bancarias (
    id INT AUTO_INCREMENT PRIMARY KEY,
    conciliacao_id INT NOT NULL,
    data_transacao DATE NOT NULL,
    descricao VARCHAR(500) NOT NULL,
    valor DECIMAL(15, 2) NOT NULL,
    tipo VARCHAR(10) NOT NULL, -- CREDITO, DEBITO
    categoria_contabil VARCHAR(100) NULL,
    conta_contabil VARCHAR(50) NULL,
    historico_padrao VARCHAR(500) NULL,
    status VARCHAR(20) DEFAULT 'PENDENTE', -- PENDENTE, CLASSIFICADA, EXPORTADA
    observacoes TEXT NULL,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (conciliacao_id) REFERENCES conciliacoes_bancarias(id) ON DELETE CASCADE,
    
    INDEX idx_conciliacao (conciliacao_id),
    INDEX idx_data_transacao (data_transacao),
    INDEX idx_tipo (tipo),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de Memorizações de Conciliação
CREATE TABLE IF NOT EXISTS memorizacoes_conciliacao (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tipo VARCHAR(20) NOT NULL, -- GRUPO, INDIVIDUAL
    grupo_id INT NULL,
    cliente_id INT NULL,
    palavra_chave VARCHAR(200) NOT NULL,
    categoria_contabil VARCHAR(100) NOT NULL,
    conta_contabil VARCHAR(50) NOT NULL,
    historico_padrao VARCHAR(500) NOT NULL,
    ativo BOOLEAN DEFAULT TRUE,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    
    FOREIGN KEY (grupo_id) REFERENCES grupos_clientes(id) ON DELETE CASCADE,
    FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
    
    INDEX idx_tipo (tipo),
    INDEX idx_grupo (grupo_id),
    INDEX idx_cliente (cliente_id),
    INDEX idx_palavra_chave (palavra_chave),
    INDEX idx_ativo (ativo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tabela de Exportações Contábeis
CREATE TABLE IF NOT EXISTS exportacoes_contabeis (
    id INT AUTO_INCREMENT PRIMARY KEY,
    conciliacao_id INT NOT NULL,
    data_exportacao DATETIME NOT NULL,
    formato VARCHAR(50) NOT NULL, -- DOMINIO, ALTERDATA, SAGE, CSV, etc
    arquivo_exportado VARCHAR(255) NULL,
    total_registros INT DEFAULT 0,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (conciliacao_id) REFERENCES conciliacoes_bancarias(id) ON DELETE CASCADE,
    
    INDEX idx_conciliacao (conciliacao_id),
    INDEX idx_data_exportacao (data_exportacao),
    INDEX idx_formato (formato)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Comentários das tabelas
ALTER TABLE conciliacoes_bancarias COMMENT 'Armazena as conciliações bancárias importadas via OFX';
ALTER TABLE transacoes_bancarias COMMENT 'Armazena as transações bancárias de cada conciliação';
ALTER TABLE memorizacoes_conciliacao COMMENT 'Armazena regras de classificação automática (grupo ou individual)';
ALTER TABLE exportacoes_contabeis COMMENT 'Registra as exportações realizadas para sistemas contábeis';
