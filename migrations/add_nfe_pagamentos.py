# -*- coding: utf-8 -*-
"""Formas de pagamento das vendas — uma linha por <detPag> de cada nota de SAÍDA.

ADITIVA e IDEMPOTENTE: só CREATE TABLE IF NOT EXISTS. Não toca em nfe_importacoes
nem em nfe_itens.

Por que existe: o XML de cada venda (NFC-e 65 e NF-e 55) traz o pagamento forma
por forma — dinheiro, débito, crédito, PIX, cheque, a prazo —, com bandeira,
CNPJ da credenciadora (maquininha), autorização e troco. Até aqui o parser só
guardava o total da nota e o XML inteiro (que o expurgo tira do banco em ~1 mês).
Tabela separada de propósito: nfe_importacoes tem 5 milhões de linhas e mexer
nela pesa no banco e na conta do Railway.

nfe_pagamentos           uma linha por forma de pagamento da nota
  chave_acesso, n        a nota e a ordem do <detPag> (1, 2, ...) — a chave
                         primária: reler o mesmo XML não duplica
  cliente_id, data_emissao  copiados de nfe_importacoes para o relatório por
                         posto e período não precisar ler a tabela grande
  tpag                   código da NT (01 dinheiro, 03 crédito, 04 débito,
                         17/20 PIX, ...); o nome vem do código, na tela
  descricao              <xPag> — só vem no tpag 99 (Outros)
  valor                  <vPag>
  tband, credenciadora, autorizacao, integrado   <card>: bandeira, CNPJ da
                         maquininha, cAut (o TxID do PIX tem 35), tpIntegra
  troco                  <vTroco> da nota, só na linha n=1

nfe_pagamentos_lote      o preenchimento do passado (a partir do Dropbox), um
                         posto e um mês por linha: o script retoma de onde parou
                         e pula o que já tem concluido_em

SEM foreign key de propósito, como a robo_maquinas: o expurgo e a exclusão de
cliente não podem travar nem apagar isto por cascata. Nota cancelada continua
aqui; o relatório tira pela nfe_importacoes.cancelada.

ROLLBACK:  DROP TABLE nfe_pagamentos_lote; DROP TABLE nfe_pagamentos;

Uso:
    python migrations/add_nfe_pagamentos.py            # DRY-RUN (nada grava)
    python migrations/add_nfe_pagamentos.py --apply    # aplica
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                               # noqa: E402
from config import Config                            # noqa: E402

TABELAS = {
    'nfe_pagamentos': """
CREATE TABLE IF NOT EXISTS nfe_pagamentos (
    chave_acesso   VARCHAR(44)   NOT NULL,
    n              TINYINT       NOT NULL,
    cliente_id     INT           NULL,
    data_emissao   DATE          NULL,
    tpag           CHAR(2)       NOT NULL,
    descricao      VARCHAR(60)   NULL,
    valor          DECIMAL(15,2) NOT NULL DEFAULT 0,
    tband          CHAR(2)       NULL,
    credenciadora  CHAR(14)      NULL,
    autorizacao    VARCHAR(60)   NULL,
    integrado      TINYINT       NULL,
    troco          DECIMAL(15,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (chave_acesso, n),
    KEY idx_np_cliente_data (cliente_id, data_emissao, tpag)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
    'nfe_pagamentos_lote': """
CREATE TABLE IF NOT EXISTS nfe_pagamentos_lote (
    cliente_id     INT          NOT NULL,
    ano_mes        CHAR(7)      NOT NULL,
    arquivos       INT          NOT NULL DEFAULT 0,
    gravados       INT          NOT NULL DEFAULT 0,
    sem_pagamento  INT          NOT NULL DEFAULT 0,
    erros          INT          NOT NULL DEFAULT 0,
    iniciado_em    DATETIME     NULL,
    concluido_em   DATETIME     NULL,
    PRIMARY KEY (cliente_id, ano_mes)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
}


def main():
    aplicar = '--apply' in sys.argv
    cn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD)
    cur = cn.cursor()
    print(f"#  banco: {Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")
    criar = []
    for nome, ddl in TABELAS.items():
        cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = %s", (nome,))
        existe = cur.fetchone()[0] > 0
        print(f"#  {nome}: {'JÁ EXISTE — nada a fazer' if existe else 'não existe — será criada'}")
        if not existe:
            print(ddl.strip())
            criar.append(nome)
    if not criar:
        return
    if not aplicar:
        print("\n#  DRY-RUN: nada gravado. Rode com --apply para criar.")
        return
    for nome in criar:
        cur.execute(TABELAS[nome])
    cn.commit()
    print(f"\n#  APLICADO: {', '.join(criar)} criada(s).  "
          "Rollback: DROP TABLE nfe_pagamentos_lote; DROP TABLE nfe_pagamentos;")


if __name__ == '__main__':
    main()
