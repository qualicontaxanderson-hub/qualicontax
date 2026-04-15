-- Adiciona ramo_atividade_id em nfe_produto_vinculo para escopo por setor
ALTER TABLE nfe_produto_vinculo
    ADD COLUMN IF NOT EXISTS ramo_atividade_id INT NULL AFTER grupo_id,
    ADD CONSTRAINT IF NOT EXISTS fk_vinculo_ramo
        FOREIGN KEY (ramo_atividade_id) REFERENCES ramos_atividade(id) ON DELETE SET NULL;
