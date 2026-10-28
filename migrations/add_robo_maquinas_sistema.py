# -*- coding: utf-8 -*-
"""Q-Robô 0.4.1 — quando cada máquina falou pela última vez COMO SISTEMA.

ADITIVA e IDEMPOTENTE: um ADD COLUMN, só se a coluna ainda não existir.

Por que existe: o robô 0.4.1 manda X-QRobo-Modo ('sistema' pela tarefa SYSTEM,
'usuario' pela janela ou pela tarefa por usuário). "Roda sem ninguém logado"
é o item do quadro de conferência do instalador, e não dá para guardar só o
último modo: com a janela aberta, ela e a tarefa falam alternadas e o item
piscaria. Guarda-se a hora do último contato como sistema; o quadro olha se
ela é recente.

  ultimo_sistema   DATETIME NULL — NULL = nunca falou como SYSTEM

ROLLBACK:  ALTER TABLE robo_maquinas DROP COLUMN ultimo_sistema;
(o registro é à prova de falha: sem a coluna, só esse dado deixa de ser gravado)

Uso:
    python migrations/add_robo_maquinas_sistema.py            # DRY-RUN (nada grava)
    python migrations/add_robo_maquinas_sistema.py --apply    # aplica
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                               # noqa: E402
from config import Config                            # noqa: E402

TABELA = 'robo_maquinas'
COLUNA = 'ultimo_sistema'
DDL = f"ALTER TABLE {TABELA} ADD COLUMN {COLUNA} DATETIME NULL AFTER ultimo_contato"


def main():
    aplicar = '--apply' in sys.argv
    cn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD)
    cur = cn.cursor()
    print(f"#  banco: {Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")
    cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() "
                "AND table_name = %s AND column_name = %s", (TABELA, COLUNA))
    existe = cur.fetchone()[0] > 0
    print(f"#  {TABELA}.{COLUNA}: {'JÁ EXISTE — nada a fazer' if existe else 'não existe — será criada'}")
    print(DDL)
    if existe:
        return
    if not aplicar:
        print("\n#  DRY-RUN: nada gravado. Rode com --apply para criar.")
        return
    cur.execute(DDL)
    cn.commit()
    print(f"\n#  APLICADO.  Rollback: ALTER TABLE {TABELA} DROP COLUMN {COLUNA};")


if __name__ == '__main__':
    main()
