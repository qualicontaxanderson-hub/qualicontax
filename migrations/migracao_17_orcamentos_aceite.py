# -*- coding: utf-8 -*-
"""Orçamentos: solicitante, controle interno e aceite do cliente pelo link (21/11/2026).

Anderson: "não vejo nome de quem solicitou o serviço"; "de início os meninos poderão preencher aqui para controle, e não
enviar para ninguém assinar, então temos que ter essa opção"; "o cliente assina pelo sistema... quero saber quem assinou e
quando"; "não quero sequencial subindo, quero ORC-código com letras e números".

    python migrations/migracao_17_orcamentos_aceite.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

COLUNAS = [   # (coluna, definição) — todas em com_orcamentos
    ('solicitante_nome', "VARCHAR(120) NULL COMMENT 'quem pediu o servico, do lado do cliente' AFTER titulo"),
    ('solicitante_email', "VARCHAR(160) NULL AFTER solicitante_nome"),
    ('solicitante_fone', "VARCHAR(40) NULL AFTER solicitante_email"),
    ('interno', "TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 = so controle interno: nao vai ao cliente aceitar' AFTER solicitante_fone"),
    ('enviado_para', "VARCHAR(600) NULL COMMENT 'e-mails para onde o orcamento foi mandado (o codigo de aceite vai para um deles)' AFTER enviado_em"),
    ('pdf_sha256', "CHAR(64) NULL COMMENT 'impressao digital do PDF enviado ao cliente' AFTER enviado_para"),
    ('codigo_hash', "CHAR(64) NULL AFTER pdf_sha256"),
    ('codigo_enviado_em', "DATETIME NULL AFTER codigo_hash"),
    ('codigo_para', "VARCHAR(160) NULL AFTER codigo_enviado_em"),
    ('codigo_tentativas', "TINYINT NOT NULL DEFAULT 0 AFTER codigo_para"),
    ('aceito_em', "DATETIME NULL COMMENT 'quando o cliente aceitou pelo link' AFTER codigo_tentativas"),
    ('aceito_nome', "VARCHAR(160) NULL AFTER aceito_em"),
    ('aceito_cpf', "VARCHAR(14) NULL AFTER aceito_nome"),
    ('aceito_email', "VARCHAR(160) NULL AFTER aceito_cpf"),
    ('aceito_ip', "VARCHAR(64) NULL AFTER aceito_email"),
    ('aceito_agente', "VARCHAR(255) NULL AFTER aceito_ip"),
    ('aceito_hash', "CHAR(64) NULL COMMENT 'SHA-256 do PDF aceito' AFTER aceito_agente"),
]


def _tem_coluna(t, c):
    r = execute_query("SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s", (t, c), fetch=True, fetch_one=True) or {}
    return bool(r.get('n'))


def aplicar(so_ver=False):
    for c, ddl in COLUNAS:
        if _tem_coluna('com_orcamentos', c):
            print('  coluna %-20s ja existe' % c); continue
        print('  coluna %-20s %s' % (c, 'criaria' if so_ver else 'criando'))
        if not so_ver:
            execute_query('ALTER TABLE com_orcamentos ADD COLUMN %s %s' % (c, ddl), fetch=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    aplicar(so_ver=not a.apply)
    print('ok' if a.apply else 'so olhando (use --apply)')


if __name__ == '__main__':
    main()
