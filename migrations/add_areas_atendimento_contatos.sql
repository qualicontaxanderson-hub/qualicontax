-- Adiciona campo de áreas de atendimento aos contatos de clientes
-- Cada contato pode receber documentos de uma ou mais áreas específicas.
-- O valor é armazenado como JSON array, ex: ["FINANCEIRO","FISCAL"]

ALTER TABLE contatos_clientes
    ADD COLUMN IF NOT EXISTS areas_atendimento TEXT NULL AFTER departamento;
