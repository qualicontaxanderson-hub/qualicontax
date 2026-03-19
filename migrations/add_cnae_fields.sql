-- Migration to add CNAE fields to clientes table
-- Run this to add CNAE fiscal and description fields

-- Add CNAE fields if they don't exist
ALTER TABLE clientes 
ADD COLUMN IF NOT EXISTS cnae_fiscal VARCHAR(10) AFTER porte_empresa,
ADD COLUMN IF NOT EXISTS cnae_fiscal_descricao VARCHAR(500) AFTER cnae_fiscal;

-- Add index for CNAE fiscal for faster queries
CREATE INDEX IF NOT EXISTS idx_cnae_fiscal ON clientes(cnae_fiscal);

SELECT 'CNAE fields migration completed successfully!' as status;
