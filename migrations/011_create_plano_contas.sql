-- Migration: Criação de tabelas para Plano de Contas
-- Data: 2026-03-25
-- Descrição: Cria tabelas para grupos/planos de contas e seus itens

-- Tabela de Planos de Contas (grupos)
CREATE TABLE IF NOT EXISTS planos_contas (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nome VARCHAR(255) NOT NULL,
    descricao TEXT NULL,
    grupo_id INT NULL,
    situacao ENUM('ATIVO', 'INATIVO') NOT NULL DEFAULT 'ATIVO',
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    FOREIGN KEY (grupo_id) REFERENCES grupos_clientes(id) ON DELETE SET NULL,

    INDEX idx_grupo (grupo_id),
    INDEX idx_situacao (situacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT 'Grupos/planos de contas contábeis vinculados a grupos de clientes';

-- Tabela de Itens do Plano de Contas
CREATE TABLE IF NOT EXISTS plano_contas_itens (
    id INT AUTO_INCREMENT PRIMARY KEY,
    plano_id INT NOT NULL,
    codigo VARCHAR(50) NOT NULL,
    descricao VARCHAR(255) NOT NULL,
    tipo ENUM('ANALITICA', 'SINTETICA') NOT NULL,
    natureza ENUM('DEVEDORA', 'CREDORA') NOT NULL,
    grupo_contabil ENUM('ATIVO', 'PASSIVO', 'PATRIMONIO_LIQUIDO', 'RECEITA', 'DESPESA') NOT NULL,
    situacao ENUM('ATIVO', 'INATIVO') NOT NULL DEFAULT 'ATIVO',
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (plano_id) REFERENCES planos_contas(id) ON DELETE CASCADE,

    INDEX idx_plano (plano_id),
    INDEX idx_codigo (codigo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT 'Contas individuais dentro de cada plano de contas';
