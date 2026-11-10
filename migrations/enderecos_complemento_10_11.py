# -*- coding: utf-8 -*-
"""Tipos de complemento do endereço (10/11/2026).

Anderson (10/11/2026): "Complemento, ter a opção de ir incluindo exemplo: + Quadra 79 + Lote 17 + Apartamento
408-B ... a turma vai digitar de tudo, então podemos criar os complementos mais usados e ter o botão de incluir
novos, e tudo isso com registro de quem está criando".

A lista começa com o que já aparece nos 578 endereços gravados (SALA 159, LOTE 101, QUADRA 78, ANDAR 30, KM 29,
LOJA 23, BLOCO 15, CONJUNTO 13, CASA 12...). O complemento continua gravado como TEXTO em
enderecos_clientes.complemento ("QUADRA 79 - LOTE 17 - APARTAMENTO 408-B"): é o que a nota, o boleto e o
contrato leem. Esta tabela só alimenta os botões da tela. criado_por NULL = veio na lista inicial.

    python migrations/enderecos_complemento_10_11.py            # mostra o que faria
    python migrations/enderecos_complemento_10_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CREATE = """
CREATE TABLE IF NOT EXISTS enderecos_complemento_tipos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nome VARCHAR(30) NOT NULL,
    ordem INT NOT NULL DEFAULT 100,
    ativo TINYINT(1) NOT NULL DEFAULT 1,
    criado_por INT NULL,
    criado_em TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_compl_tipo_nome (nome)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# na ordem em que aparecem os botões (os 7 primeiros ficam à vista, o resto em "mais")
INICIAIS = ['QUADRA', 'LOTE', 'SALA', 'APARTAMENTO', 'BLOCO', 'ANDAR', 'CASA',
            'LOJA', 'KM', 'CONJUNTO', 'EDIFÍCIO', 'CONDOMÍNIO', 'TORRE', 'BOX', 'GALPÃO',
            'MÓDULO', 'SETOR', 'ÁREA', 'CHÁCARA', 'FAZENDA', 'ANEXO', 'FUNDOS', 'TÉRREO', 'SOBRELOJA']

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    existe = execute_query("SHOW TABLES LIKE 'enderecos_complemento_tipos'", fetch=True)
    print('tabela já existe' if existe else 'enderecos_complemento_tipos: vai ser criada')
    if a.apply:
        if not existe:
            execute_query(CREATE, fetch=False)
            print('criada')
        for i, nome in enumerate(INICIAIS):
            execute_query('INSERT IGNORE INTO enderecos_complemento_tipos (nome, ordem) VALUES (%s, %s)',
                          (nome, (i + 1) * 10), fetch=False)
        print(execute_query('SELECT COUNT(*) AS n FROM enderecos_complemento_tipos', fetch=True, fetch_one=True))
    else:
        print('tipos iniciais:', ', '.join(INICIAIS))
