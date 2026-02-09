-- Migration script to update database for complete client module
-- Run this script if you have an existing database

-- Add new fields to clientes table if they don't exist
ALTER TABLE clientes 
ADD COLUMN IF NOT EXISTS nome_fantasia VARCHAR(255) AFTER nome_razao_social,
ADD COLUMN IF NOT EXISTS data_fim_contrato DATE AFTER data_inicio_contrato,
ADD COLUMN IF NOT EXISTS criado_por INT AFTER observacoes,
ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER criado_por,
ADD COLUMN IF NOT EXISTS atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP AFTER criado_em;

-- Update porte_empresa to ENUM if needed
ALTER TABLE clientes 
MODIFY COLUMN porte_empresa ENUM('MEI', 'ME', 'EPP', 'MEDIO', 'GRANDE');

-- Update situacao to include new values
ALTER TABLE clientes 
MODIFY COLUMN situacao ENUM('ATIVO', 'INATIVO', 'SUSPENSO', 'CANCELADO') DEFAULT 'ATIVO';

-- Add foreign key for criado_por if it doesn't exist
-- Note: This might fail if usuarios table doesn't have the right structure
-- ALTER TABLE clientes ADD CONSTRAINT fk_clientes_criado_por 
-- FOREIGN KEY (criado_por) REFERENCES usuarios(id) ON DELETE SET NULL;

-- Update enderecos_clientes table
ALTER TABLE enderecos_clientes
ADD COLUMN IF NOT EXISTS pais VARCHAR(100) DEFAULT 'Brasil' AFTER estado,
ADD COLUMN IF NOT EXISTS principal BOOLEAN DEFAULT FALSE AFTER pais;

-- Update tipo in enderecos_clientes to include CORRESPONDENCIA
ALTER TABLE enderecos_clientes
MODIFY COLUMN tipo ENUM('COMERCIAL', 'RESIDENCIAL', 'CORRESPONDENCIA') DEFAULT 'COMERCIAL';

-- Update contatos_clientes table
ALTER TABLE contatos_clientes
ADD COLUMN IF NOT EXISTS departamento VARCHAR(100) AFTER celular,
ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE AFTER principal;

-- Create grupos_clientes table if not exists
CREATE TABLE IF NOT EXISTS grupos_clientes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nome VARCHAR(255) NOT NULL,
    descricao TEXT,
    situacao ENUM('ATIVO', 'INATIVO') DEFAULT 'ATIVO',
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Create cliente_grupo_relacao table if not exists
CREATE TABLE IF NOT EXISTS cliente_grupo_relacao (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT NOT NULL,
    grupo_id INT NOT NULL,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
    FOREIGN KEY (grupo_id) REFERENCES grupos_clientes(id) ON DELETE CASCADE,
    UNIQUE KEY unique_cliente_grupo (cliente_id, grupo_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Update existing ATIVO/INATIVO values to uppercase if needed
UPDATE clientes SET situacao = 'ATIVO' WHERE situacao = 'Ativo' OR situacao = 'ativo';
UPDATE clientes SET situacao = 'INATIVO' WHERE situacao = 'Inativo' OR situacao = 'inativo';

SELECT 'Migration completed successfully!' as status;
