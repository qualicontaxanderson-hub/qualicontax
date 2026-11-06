# -*- coding: utf-8 -*-
"""Contrato de prestação de serviços assinado pelo sistema (06/11/2026).

Anderson: contrato montado do cadastro + honorários (serviços por caixa: contábil, fiscal, pessoal e específicos;
quem assina pela empresa escolhido entre os sócios; pela Qualicontax o Albert ou o Anderson; 13º cobrado ou isento;
vencimento; várias empresas num contrato só, como a Rede MM). Assinatura DENTRO do app: link por e-mail, código de
6 dígitos, nome + CPF + "li e aceito", com registro (hash do PDF, data/hora, IP) — "ok" dele em 06/11.

    python migrations/migracao_13_prest_contratos.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CREATE = ["""
CREATE TABLE IF NOT EXISTS prest_contratos (
    id INT NOT NULL AUTO_INCREMENT,
    cliente_id INT NOT NULL COMMENT 'empresa principal (a 1a do contrato)',
    fin_contrato_id INT NULL COMMENT 'contrato de honorarios de onde vieram valor/vencimento/13o',
    tipo VARCHAR(10) NOT NULL DEFAULT 'CONTRATO' COMMENT 'CONTRATO|ADENDO',
    anterior_id INT NULL COMMENT 'ADENDO: o contrato que ele altera',
    titulo VARCHAR(120) NOT NULL,
    dados JSON NOT NULL COMMENT 'tudo o que o formulario escolheu (partes, servicos, clausulas, valores, signatarios)',
    status VARCHAR(12) NOT NULL DEFAULT 'RASCUNHO' COMMENT 'RASCUNHO|ENVIADO|ASSINADO|CANCELADO',
    pdf_sha256 CHAR(64) NULL COMMENT 'do PDF enviado para assinatura (o que todos assinam)',
    pdf_path VARCHAR(400) NULL COMMENT 'PDF assinado no Dropbox',
    criado_por INT NOT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    enviado_em DATETIME NULL,
    assinado_em DATETIME NULL,
    cancelado_em DATETIME NULL,
    PRIMARY KEY (id),
    KEY idx_pc_cliente (cliente_id, status),
    KEY idx_pc_fin (fin_contrato_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='contratos de prestacao de servicos gerados e assinados pelo app'
""", """
CREATE TABLE IF NOT EXISTS prest_contrato_empresas (
    id INT NOT NULL AUTO_INCREMENT,
    contrato_id INT NOT NULL,
    cliente_id INT NOT NULL,
    ordem INT NOT NULL DEFAULT 1,
    PRIMARY KEY (id),
    UNIQUE KEY uq_pce (contrato_id, cliente_id),
    KEY idx_pce_cliente (cliente_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='empresas de cada contrato (uma rede inteira pode assinar um so)'
""", """
CREATE TABLE IF NOT EXISTS prest_assinantes (
    id INT NOT NULL AUTO_INCREMENT,
    contrato_id INT NOT NULL,
    papel VARCHAR(12) NOT NULL COMMENT 'CONTRATANTE|CONTRATADA|TESTEMUNHA',
    ordem INT NOT NULL DEFAULT 1,
    nome VARCHAR(160) NOT NULL,
    cpf VARCHAR(14) NULL,
    documento VARCHAR(80) NULL COMMENT 'RG/CNH e orgao, como vai no contrato',
    email VARCHAR(160) NULL,
    usuario_id INT NULL COMMENT 'quando e alguem do escritorio',
    token VARCHAR(48) NOT NULL,
    codigo_hash CHAR(64) NULL COMMENT 'sha256 do codigo de 6 digitos enviado',
    codigo_enviado_em DATETIME NULL,
    codigo_tentativas INT NOT NULL DEFAULT 0,
    aviso_enviado_em DATETIME NULL,
    assinado_em DATETIME NULL,
    assinado_nome VARCHAR(160) NULL COMMENT 'como a pessoa digitou ao assinar',
    assinado_cpf VARCHAR(14) NULL,
    assinado_ip VARCHAR(64) NULL,
    assinado_agente VARCHAR(255) NULL,
    assinado_hash CHAR(64) NULL COMMENT 'sha256 do PDF que a pessoa viu e aceitou',
    PRIMARY KEY (id),
    UNIQUE KEY uq_pa_token (token),
    KEY idx_pa_contrato (contrato_id, papel)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='quem assina cada contrato e o registro da assinatura eletronica'
"""]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    for t in ('prest_contratos', 'prest_contrato_empresas', 'prest_assinantes'):
        print(t, 'já existe' if execute_query("SHOW TABLES LIKE %s", (t,), fetch=True) else 'não existe')
    if not a.apply:
        print('dry-run. Use --apply para criar.')
        return
    for sql in CREATE:
        execute_query(sql, fetch=False)
    print('ok')


if __name__ == '__main__':
    main()
