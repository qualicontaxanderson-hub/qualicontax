-- Migration: Add data_inicio_atividade field to separate company foundation date from contract date
-- Created: 2026-02-21
-- Description: Separates the concept of "Data de Início da Atividade" (company foundation from CNPJ)
--              from "Data Início do Contrato" (service contract start date)

-- Add new column for company foundation date
ALTER TABLE clientes 
ADD COLUMN IF NOT EXISTS data_inicio_atividade DATE 
COMMENT 'Data de início das atividades da empresa (obtida do CNPJ)';

-- The existing data_inicio_contrato remains for contract dates
-- No data migration needed as this is a new concept being separated

-- Verify column was added
SELECT 
    COLUMN_NAME, 
    DATA_TYPE, 
    IS_NULLABLE, 
    COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS 
WHERE TABLE_NAME = 'clientes' 
AND COLUMN_NAME IN ('data_inicio_atividade', 'data_inicio_contrato')
ORDER BY COLUMN_NAME;
