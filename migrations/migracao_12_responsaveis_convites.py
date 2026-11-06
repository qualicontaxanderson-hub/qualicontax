# -*- coding: utf-8 -*-
"""Link para os departamentos elegerem o responsável da empresa (06/11/2026).

Anderson: "ao cadastrar vamos gerar um link e cada um dos departamentos irá salvar; o Miguel manda o link e os
departamentos elegem quem ficará como responsável"; "quando enviar tem que acessar o sistema com Login e Senha".
Cada pessoa só escolhe o responsável do PRÓPRIO departamento; quem gerou o link, a Diretoria e o admin escolhem
todos. O link vale 7 dias e morre quando os 7 departamentos estão preenchidos.

    python migrations/migracao_12_responsaveis_convites.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CREATE = ["""
CREATE TABLE IF NOT EXISTS clientes_responsaveis_convites (
    id INT NOT NULL AUTO_INCREMENT,
    token VARCHAR(48) NOT NULL,
    cliente_id INT NOT NULL,
    criado_por INT NOT NULL,
    expira_em DATETIME NOT NULL,
    concluido_em DATETIME NULL COMMENT 'quando os 7 departamentos ficaram preenchidos',
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_rc_token (token),
    KEY idx_rc_cliente (cliente_id, concluido_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='link (login obrigatorio) para cada departamento eleger o responsavel da empresa'
""", """
CREATE TABLE IF NOT EXISTS clientes_responsaveis_respostas (
    id INT NOT NULL AUTO_INCREMENT,
    convite_id INT NOT NULL,
    departamento_id INT NOT NULL,
    usuario_id INT NOT NULL COMMENT 'quem respondeu',
    escolhido_id INT NOT NULL COMMENT 'o responsavel eleito',
    respondido_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_rr_convite (convite_id, departamento_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='quem respondeu o que em cada link de responsaveis'
"""]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    for t in ('clientes_responsaveis_convites', 'clientes_responsaveis_respostas'):
        print(t, 'já existe' if execute_query("SHOW TABLES LIKE %s", (t,), fetch=True) else 'não existe')
    if not a.apply:
        print('dry-run. Use --apply para criar.')
        return
    for sql in CREATE:
        execute_query(sql, fetch=False)
    print('ok')


if __name__ == '__main__':
    main()
