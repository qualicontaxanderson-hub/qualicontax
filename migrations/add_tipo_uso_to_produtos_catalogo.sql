-- Adiciona campo tipo_uso à tabela nfe_produtos_catalogo
-- Valores possíveis: 'Compra para Revenda', 'Compra para Consumo',
--                    'Ativo Imobilizado', 'Uso e Consumo'
ALTER TABLE nfe_produtos_catalogo
    ADD COLUMN tipo_uso VARCHAR(50) NULL DEFAULT NULL
        COMMENT 'Finalidade fiscal da entrada: Compra para Revenda, Compra para Consumo, etc.'
        AFTER subcategoria;
