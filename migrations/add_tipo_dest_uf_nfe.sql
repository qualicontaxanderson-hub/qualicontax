-- Migration: Conferência de Saídas
-- Adiciona colunas tipo e dest_uf, troca UNIQUE de (chave_acesso) para (chave_acesso, tipo)
-- Execute manualmente se necessário; init_db.py faz este migration automaticamente.

ALTER TABLE nfe_importacoes
  ADD COLUMN tipo ENUM('entrada','saida') NOT NULL DEFAULT 'entrada' AFTER grupo_id,
  ADD COLUMN dest_uf VARCHAR(2) NULL AFTER dest_nome;

-- Registros existentes já ficam com tipo='entrada' pelo DEFAULT.

-- Substitui a chave única simples (chave_acesso) pela composta (chave_acesso, tipo)
-- para permitir que o mesmo XML exista como entrada E saída.
ALTER TABLE nfe_importacoes DROP INDEX chave_acesso;
ALTER TABLE nfe_importacoes ADD UNIQUE KEY uk_chave_tipo (chave_acesso, tipo);
