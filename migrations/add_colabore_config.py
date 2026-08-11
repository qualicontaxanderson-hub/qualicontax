# -*- coding: utf-8 -*-
"""Q-Colabore F2 (nuvem): cria colabore_config — a chave por FUNCIONÁRIO.

O agente instalado na máquina do funcionário posta arquivos em /api/colabore/enviar
autenticado por uma chave PRÓPRIA (Bearer). Esta tabela é o cofre dessa chave —
uma linha por usuário (usuarios.id), espelhando robo_config (que é por cliente).

DIFERENÇA DELIBERADA em relação a robo_config: aqui a chave NUNCA fica em claro no
banco. Guardamos só o SHA-256 (token_hash, 64 hex) e o PREFIXO (8 chars) para a tela
reconhecer "qual chave é esta". O segredo em claro aparece UMA vez, na geração, e
some. (robo_config guarda robo_token em claro; F2 não repete isso.)

ADITIVA e IDEMPOTENTE — só CREATE TABLE IF NOT EXISTS. Não toca nenhuma tabela
existente. FK usuario_id -> usuarios(id) ON DELETE CASCADE: apagar o funcionário
leva junto a chave dele (mas NUNCA a trilha em logs_sistema, que é outra tabela).

ROLLBACK (impresso no fim do --apply):
    DROP TABLE colabore_config;

Uso:
    python migrations/add_colabore_config.py            # DRY-RUN (nada grava)
    python migrations/add_colabore_config.py --apply    # cria a tabela
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                               # noqa: E402
from config import Config                            # noqa: E402

TABELA = 'colabore_config'

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABELA} (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    usuario_id          INT NOT NULL,
    token_hash          VARCHAR(64) NULL,
    token_prefixo       VARCHAR(8)  NULL,
    versao              INT NOT NULL DEFAULT 0,
    data_inicio_captura DATE NULL,
    ativo               TINYINT(1) NOT NULL DEFAULT 1,
    ultimo_contato      DATETIME NULL,
    token_gerado_em     DATETIME NULL,
    token_gerado_por    INT NULL,
    criado_em           TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em       TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_colabore_usuario (usuario_id),
    UNIQUE KEY uk_colabore_hash (token_hash),
    CONSTRAINT fk_colabore_usuario FOREIGN KEY (usuario_id)
        REFERENCES usuarios(id) ON DELETE CASCADE,
    CONSTRAINT fk_colabore_gerou FOREIGN KEY (token_gerado_por)
        REFERENCES usuarios(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


def conectar():
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        charset='utf8mb4', autocommit=True,
    )


def tabela_existe(cur, nome):
    cur.execute("SELECT COUNT(*) c FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name=%s", (nome,))
    return cur.fetchone()['c'] > 0


def main():
    aplicar = '--apply' in sys.argv
    cnx = conectar()
    cur = cnx.cursor(dictionary=True)
    try:
        print("\n" + "#" * 80)
        print("#  Q-COLABORE F2 -- colabore_config (chave por funcionario)"
              f"{'  [--APPLY]' if aplicar else '  [DRY-RUN]'}")
        print(f"#  banco: {Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")
        print("#" * 80)

        existe = tabela_existe(cur, TABELA)
        print("\nESTADO ATUAL:")
        print(f"  tabela {TABELA} existe? .......... {'SIM' if existe else 'NAO'}")

        print("\nACAO QUE SERA EXECUTADA:")
        print(f"  {'(no-op, ja existe)' if existe else 'CREATE TABLE'} {TABELA} "
              "(usuario_id UNIQUE, token_hash UNIQUE, token_prefixo, versao, "
              "data_inicio_captura, ativo, ultimo_contato, FK->usuarios)")

        if not aplicar:
            print("\n" + "=" * 80)
            print("DRY-RUN -- nada foi alterado.")
            print("ROLLBACK (apos o --apply):  DROP TABLE " + TABELA + ";")
            print("Para aplicar:  python migrations/add_colabore_config.py --apply")
            print("=" * 80)
            return 0

        # ---- APPLY ----
        print("\n" + "=" * 80)
        print("APLICANDO")
        print("=" * 80)
        cur.execute(DDL)
        print(f"  {TABELA}: {'ja existia (no-op)' if existe else 'CRIADA'}")

        ok = tabela_existe(cur, TABELA)
        print("\nVERIFICACAO POS:")
        print(f"  {TABELA} existe? .......... {'SIM' if ok else 'NAO'}")
        if not ok:
            print("  ATENCAO: verificacao falhou -- revise antes de seguir.")
            return 1
        print("  OK.")
        print("\n" + "-" * 80)
        print("ROLLBACK MANUAL:  DROP TABLE " + TABELA + ";")
        return 0
    finally:
        cur.close()
        cnx.close()


if __name__ == '__main__':
    sys.exit(main())
