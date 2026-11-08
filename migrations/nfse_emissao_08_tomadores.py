# -*- coding: utf-8 -*-
"""Emissor de NFS-e — TOMADORES dos clientes (os clientes dos nossos clientes), 08/11/2026.

Pedido do Anderson (06/11/2026): o cliente emite nota para os clientes DELE, que não estão
no nosso cadastro. Cada empresa emitente tem a sua lista (empresa_id = clientes.id de quem
emite); o mesmo CNPJ pode aparecer em várias empresas. E-mail é opcional (para mandar a nota).
As colunas de consentimento ficam prontas para o link do cliente (LGPD), que vem depois.

TABELA NOVA; nada que o Financeiro usa é alterado.

    python migrations/nfse_emissao_08_tomadores.py            # dry-run: só mostra
    python migrations/nfse_emissao_08_tomadores.py --apply    # executa

Rollback:
    DROP TABLE nfse_tomadores;
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS nfse_tomadores (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id          INT NOT NULL COMMENT 'clientes.id de QUEM EMITE a nota',
  documento           VARCHAR(14) NOT NULL COMMENT 'CNPJ ou CPF, só dígitos',
  nome                VARCHAR(150) NOT NULL,
  nome_fantasia       VARCHAR(150) NULL,
  inscricao_municipal VARCHAR(20) NULL,
  email               VARCHAR(255) NULL COMMENT 'opcional: para onde a nota vai',
  fone                VARCHAR(20) NULL,
  cep                 VARCHAR(8) NULL,
  logradouro          VARCHAR(255) NULL,
  numero              VARCHAR(60) NULL,
  complemento         VARCHAR(156) NULL,
  bairro              VARCHAR(60) NULL,
  cidade              VARCHAR(80) NULL,
  uf                  CHAR(2) NULL,
  municipio_ibge      VARCHAR(7) NULL,
  fonte               VARCHAR(20) NOT NULL DEFAULT 'manual' COMMENT 'receita|cadastro|manual|link',
  consentimento_em    DATETIME NULL COMMENT 'LGPD: aceite no link do cliente',
  consentimento_ip    VARCHAR(45) NULL,
  ativo               TINYINT(1) NOT NULL DEFAULT 1,
  ultima_nota_em      DATETIME NULL,
  criado_por          INT NULL,
  alterado_por        INT NULL,
  criado_em           DATETIME DEFAULT CURRENT_TIMESTAMP,
  atualizado_em       DATETIME NULL,
  UNIQUE KEY uk_empresa_documento (empresa_id, documento),
  INDEX idx_documento (documento)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def existe():
    r = execute_query("SELECT COUNT(*) n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                      "AND TABLE_NAME = 'nfse_tomadores'", fetch=True, fetch_one=True)
    return bool(r and r['n'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem ele só mostra')
    args = ap.parse_args()
    if existe():
        print('nfse_tomadores: já existe. Nada a fazer.')
        return 0
    print('nfse_tomadores: a criar ->')
    print(DDL)
    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0
    if execute_query(DDL) is None or not existe():
        print('FALHOU: tabela não criada.')
        return 1
    print('OK: nfse_tomadores criada.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
