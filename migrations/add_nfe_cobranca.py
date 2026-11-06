# -*- coding: utf-8 -*-
"""Cobrança das COMPRAS — condição, forma e vencimentos das notas de ENTRADA.

ADITIVA e IDEMPOTENTE: só CREATE TABLE IF NOT EXISTS. Não toca em nfe_importacoes
(5 milhões de linhas) nem em nfe_pagamentos (vendas).

Por que existe: o time precisa saber quando vencem os boletos das compras dos
clientes que não mandam extrato. O XML da entrada traz as duplicatas (<cobr><dup>:
nDup, dVenc, vDup) e a condição (<indPag>), mas o expurgo tira o XML do banco
em ~1 mês. Pedido do Anderson em 06/11/2026, testado no Aliança (cliente 23).

nfe_cobranca       uma linha por nota de entrada LIDA (mesmo sem cobrança: a
                   linha vazia diz que a nota já foi lida)
  chave_acesso     a nota (é única por tipo em nfe_importacoes; aqui só entrada)
  ind_pag          0 à vista, 1 a prazo, NULL não veio
  tpags            códigos <tPag> da nota, separados por vírgula ("15", "03,17")
  n_fat, v_liq     <fat>: número da fatura e valor líquido
  qtd_dup          quantas duplicatas a nota tem

nfe_duplicatas     uma linha por parcela (<dup>)
  chave_acesso, n  a nota e a ordem da parcela no XML — reler não duplica
  n_dup, d_venc, v_dup   número, vencimento e valor da parcela
  idx_nd_venc      para a lista "o que vence esta semana"

nfe_cobranca_lote  o preenchimento do passado (scripts/preencher_cobranca.py),
                   um cliente e um mês por linha, como nfe_pagamentos_lote

SEM foreign key de propósito (como nfe_pagamentos): expurgo e exclusão não
podem travar nem apagar por cascata. Empresa e emissão vêm de nfe_importacoes
pela chave — a nota órfã que depois ganha empresa continua certa.

ROLLBACK:  DROP TABLE nfe_cobranca_lote; DROP TABLE nfe_duplicatas; DROP TABLE nfe_cobranca;

Uso:
    python migrations/add_nfe_cobranca.py            # DRY-RUN (nada grava)
    python migrations/add_nfe_cobranca.py --apply    # aplica
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                               # noqa: E402
from config import Config                            # noqa: E402

TABELAS = {
    'nfe_cobranca': """
CREATE TABLE IF NOT EXISTS nfe_cobranca (
    chave_acesso   VARCHAR(44)   NOT NULL,
    ind_pag        TINYINT       NULL,
    tpags          VARCHAR(40)   NULL,
    n_fat          VARCHAR(60)   NULL,
    v_liq          DECIMAL(15,2) NULL,
    qtd_dup        SMALLINT      NOT NULL DEFAULT 0,
    lido_em        DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chave_acesso)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
    'nfe_duplicatas': """
CREATE TABLE IF NOT EXISTS nfe_duplicatas (
    chave_acesso   VARCHAR(44)   NOT NULL,
    n              SMALLINT      NOT NULL,
    n_dup          VARCHAR(60)   NULL,
    d_venc         DATE          NULL,
    v_dup          DECIMAL(15,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (chave_acesso, n),
    KEY idx_nd_venc (d_venc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
    'nfe_cobranca_lote': """
CREATE TABLE IF NOT EXISTS nfe_cobranca_lote (
    cliente_id     INT          NOT NULL,
    ano_mes        CHAR(7)      NOT NULL,
    arquivos       INT          NOT NULL DEFAULT 0,
    com_cobranca   INT          NOT NULL DEFAULT 0,
    sem_cobranca   INT          NOT NULL DEFAULT 0,
    erros          INT          NOT NULL DEFAULT 0,
    iniciado_em    DATETIME     NULL,
    concluido_em   DATETIME     NULL,
    PRIMARY KEY (cliente_id, ano_mes)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
}
ROLLBACK = "DROP TABLE nfe_cobranca_lote; DROP TABLE nfe_duplicatas; DROP TABLE nfe_cobranca;"


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
    print(f"\n#  APLICADO: {', '.join(criar)} criada(s).  Rollback: {ROLLBACK}")


if __name__ == '__main__':
    main()
