# -*- coding: utf-8 -*-
"""Q-Robô 0.4.0 — uma linha por MÁQUINA de cada posto.

ADITIVA e IDEMPOTENTE: só CREATE TABLE IF NOT EXISTS. Não toca em nenhuma
tabela existente — robo_config, tokens e notas seguem como estão.

Por que existe: a chave é do POSTO e 2 ou 3 PCs do mesmo posto usam a mesma.
Até a 0.3.x a nuvem só sabia que "o posto falou" (robo_config.robo_ultimo_contato),
então um caixa parado ficava escondido atrás dos outros. A partir da 0.4.0 o robô
manda X-QRobo-Maquina / X-QRobo-Nome / X-QRobo-Versao e cada PC vira uma linha aqui.

  maquina_id    uuid gerado pelo robô e guardado no config.json dele
  nome_pc       COMPUTERNAME (atualiza sozinho se o PC for renomeado)
  apelido       nome dado na tela Q-Robô ("Caixa 1"); NULL = mostra nome_pc
  envios_dia    POSTs de nota recebidos no dia_envios (zera na virada do dia)
  oculta        1 = removida da tela ("PC formatado"); volta a 0 se falar de novo

SEM foreign key de propósito, como a qrobo_auditoria: excluir cliente não pode
travar nem apagar isto por cascata.

ROLLBACK:  DROP TABLE robo_maquinas;   (o robô segue funcionando: o registro de
máquina é à prova de falha e a tabela ausente só é ignorada)

Uso:
    python migrations/add_robo_maquinas.py            # DRY-RUN (nada grava)
    python migrations/add_robo_maquinas.py --apply    # aplica
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                               # noqa: E402
from config import Config                            # noqa: E402

TABELA = 'robo_maquinas'

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABELA} (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id       INT          NOT NULL,
    maquina_id       CHAR(32)     NOT NULL,
    nome_pc          VARCHAR(64)  NULL,
    apelido          VARCHAR(64)  NULL,
    versao           VARCHAR(16)  NULL,
    primeiro_contato DATETIME     NOT NULL,
    ultimo_contato   DATETIME     NOT NULL,
    envios_dia       INT          NOT NULL DEFAULT 0,
    dia_envios       DATE         NULL,
    oculta           TINYINT(1)   NOT NULL DEFAULT 0,
    UNIQUE KEY uq_rm_cliente_maquina (cliente_id, maquina_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def main():
    aplicar = '--apply' in sys.argv
    cn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD)
    cur = cn.cursor()
    print(f"#  banco: {Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")
    cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = %s", (TABELA,))
    existe = cur.fetchone()[0] > 0
    print(f"#  {TABELA}: {'JÁ EXISTE — nada a fazer' if existe else 'não existe — será criada'}")
    print(DDL.strip())
    if existe:
        return
    if not aplicar:
        print("\n#  DRY-RUN: nada gravado. Rode com --apply para criar.")
        return
    cur.execute(DDL)
    cn.commit()
    print(f"\n#  APLICADO: {TABELA} criada.  Rollback: DROP TABLE {TABELA};")


if __name__ == '__main__':
    main()
