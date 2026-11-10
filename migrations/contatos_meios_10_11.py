# -*- coding: utf-8 -*-
"""Contato com mais de um celular e mais de um e-mail (10/11/2026).

Anderson (10/11/2026): "vamos supor que o contato tem 2 Celulares ou 3 e-mail dar a opção de incluir mais".
O celular e o e-mail PRINCIPAIS continuam em contatos_clientes (celular, email) — é o que as telas, o
A CADASTRAR e o WhatsApp leem. Os demais ficam aqui, um por linha. Os e-mails extras também recebem o
que os departamentos marcados no contato mandam (utils/cobranca_email.py).

    python migrations/contatos_meios_10_11.py            # mostra o que faria
    python migrations/contatos_meios_10_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CREATE = """
CREATE TABLE IF NOT EXISTS contatos_meios (
    id INT AUTO_INCREMENT PRIMARY KEY,
    contato_id INT NOT NULL,
    tipo VARCHAR(10) NOT NULL,            -- CELULAR | EMAIL
    valor VARCHAR(255) NOT NULL,
    criado_em TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_contato_meio (contato_id, tipo, valor),
    KEY idx_meio_valor (tipo, valor)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    existe = execute_query("SHOW TABLES LIKE 'contatos_meios'", fetch=True)
    print('contatos_meios já existe' if existe else 'contatos_meios: vai ser criada')
    if a.apply and not existe:
        execute_query(CREATE, fetch=False)
        print('criada')
