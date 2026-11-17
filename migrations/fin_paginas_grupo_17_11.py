# -*- coding: utf-8 -*-
"""Página de pagamento do GRUPO (17/11/2026).

Anderson: "com essa seleção [Atrasados + Grupo M6] conseguimos enviar as Página do Cliente de uma vez para o
cliente... e não um a um!!! Quando for esses clientes de rede"; "o cliente tem uma empresa Atrasada e outras
Inadimplentes... queria mandar tudo no mesmo Página do Cliente... os inadimplentes, os atrasados e até a vencer
se tiver em aberto... e até os perdidos. A única coisa que tem que barrar seria se não tiver a cobrança ativa,
por exemplo o boleto está expirado".

``fin_paginas_grupo``: UM código por grupo de empresas (/g/<token>, sem login). A página é VIVA: mostra, na hora
em que o cliente abre, todos os boletos ativos (aguardando ou vencidos) dos honorários em aberto das empresas
do grupo, cada um com Pix, linha digitável e PDF. O que foi pago some; boleto expirado ou sem boleto não entra
(tem que reemitir antes). Guarda quando o cliente abriu e quando o e-mail saiu.

Só cria tabela; não altera nem apaga nada.

    python migrations/fin_paginas_grupo_17_11.py            # mostra o que faria
    python migrations/fin_paginas_grupo_17_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

SQL = """
CREATE TABLE IF NOT EXISTS fin_paginas_grupo (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  grupo_id           BIGINT UNSIGNED NOT NULL COMMENT 'grupos_clientes.id',
  token              VARCHAR(48) NOT NULL COMMENT 'codigo do link /g/<token>; so quem tem o link chega',
  criado_por         INT NULL,
  criado_em          DATETIME DEFAULT CURRENT_TIMESTAMP,
  aberto_em          DATETIME NULL COMMENT 'ultima vez que o cliente abriu a pagina',
  aberto_vezes       INT NOT NULL DEFAULT 0,
  email_enviado_em   DATETIME NULL,
  email_enviado_para VARCHAR(500) NULL,
  UNIQUE KEY uq_grupo (grupo_id),
  UNIQUE KEY uq_token (token)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""


def tabela_existe(nome):
    return bool(execute_query('SHOW TABLES LIKE %s', (nome,), fetch=True))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    if tabela_existe('fin_paginas_grupo'):
        print('fin_paginas_grupo já existe: nada a fazer')
    elif a.apply:
        execute_query(SQL, fetch=False)
        print('criada fin_paginas_grupo')
    else:
        print('faltaria criar fin_paginas_grupo (rode com --apply)')
