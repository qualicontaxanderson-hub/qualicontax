-- Adiciona coluna 'nome' na tabela empresas (coluna estava faltando no banco de produção)
ALTER TABLE empresas
    ADD COLUMN IF NOT EXISTS nome VARCHAR(200) NOT NULL DEFAULT '' AFTER id;
