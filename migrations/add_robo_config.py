# -*- coding: utf-8 -*-
"""Q-Robô Fase 1 (nuvem): cria robo_config e habilita origem='Q-ROBO'.

ADITIVA e IDEMPOTENTE — não toca nenhuma linha existente nem as outras fontes:

  1. CREATE TABLE robo_config (1:1 por cliente_id, espelhando dfe_certificados):
     data_inicio_captura, robo_token (único, indexado), robo_reset_seq, ativo,
     robo_ultimo_contato + timestamps. FK -> clientes(id) ON DELETE CASCADE.

  2. Estende o ENUM: nfe_importacoes.origem += 'Q-ROBO' (mesmo padrão do SEFAZ em
     init_db.py). Só um ALTER MODIFY; os valores UPLOAD/DROPBOX/SEFAZ e todas as
     linhas atuais ficam intactos. Guardado por checagem (não re-altera se já tem).

BACKUP: grava em robo_config_migracao_bkp o estado ANTERIOR (definição do ENUM
origem e se a robo_config já existia) — é o que o rollback usa para voltar o ENUM
exatamente ao que era.

ROLLBACK (impresso no fim do --apply):
    DROP TABLE robo_config;
    ALTER TABLE nfe_importacoes MODIFY origem <enum_anterior>;   -- do backup
  ATENÇÃO: o MODIFY de volta só é seguro se NENHUMA linha usar 'Q-ROBO' ainda
  (senão o valor não caberia no ENUM antigo). O script confere isso e avisa.

Uso:
    python migrations/add_robo_config.py            # DRY-RUN (nada grava)
    python migrations/add_robo_config.py --apply    # cria tabela + estende ENUM
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                               # noqa: E402
from config import Config                            # noqa: E402

TABELA = 'robo_config'
BKP = 'robo_config_migracao_bkp'
ENUM_NOVO = "ENUM('UPLOAD','DROPBOX','SEFAZ','Q-ROBO') NOT NULL DEFAULT 'UPLOAD'"

DDL_ROBO = f"""
CREATE TABLE IF NOT EXISTS {TABELA} (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id          INT NOT NULL,
    data_inicio_captura DATE NULL,
    robo_token          VARCHAR(64) NULL,
    robo_reset_seq      INT NOT NULL DEFAULT 0,
    ativo               TINYINT(1) NOT NULL DEFAULT 1,
    robo_ultimo_contato DATETIME NULL,
    criado_em           TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em       TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_robo_cliente (cliente_id),
    UNIQUE KEY uk_robo_token (robo_token),
    CONSTRAINT fk_robo_cliente FOREIGN KEY (cliente_id)
        REFERENCES clientes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

DDL_BKP = f"""
CREATE TABLE IF NOT EXISTS {BKP} (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    origem_enum_antes TEXT,
    robo_config_existia TINYINT(1),
    aplicado_em       TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
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


def enum_origem(cur):
    cur.execute("SELECT COLUMN_TYPE t FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name='nfe_importacoes' "
                "AND column_name='origem'")
    row = cur.fetchone()
    return row['t'] if row else None


def linhas_qrobo(cur):
    cur.execute("SELECT COUNT(*) c FROM nfe_importacoes WHERE origem='Q-ROBO'")
    return cur.fetchone()['c']


def main():
    aplicar = '--apply' in sys.argv
    cnx = conectar()
    cur = cnx.cursor(dictionary=True)
    try:
        print("\n" + "#" * 80)
        print("#  Q-ROBO Fase 1 — robo_config + origem='Q-ROBO'"
              f"{'  [--APPLY]' if aplicar else '  [DRY-RUN]'}")
        print(f"#  banco: {Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")
        print("#" * 80)

        existe = tabela_existe(cur, TABELA)
        enum_atual = enum_origem(cur) or '(coluna origem não encontrada)'
        tem_qrobo = 'Q-ROBO' in (enum_atual or '')

        print("\nESTADO ATUAL:")
        print(f"  tabela {TABELA} existe? .......... {'SIM' if existe else 'NÃO'}")
        print(f"  origem (ENUM) .................... {enum_atual}")
        print(f"  já contém 'Q-ROBO'? ............. {'SIM' if tem_qrobo else 'NÃO'}")

        print("\nAÇÕES QUE SERÃO EXECUTADAS:")
        print(f"  1. {'(no-op, já existe)' if existe else 'CREATE TABLE'} {TABELA} "
              "(cliente_id UNIQUE, robo_token UNIQUE, ativo, FK->clientes)")
        print(f"  2. {'(no-op, já tem Q-ROBO)' if tem_qrobo else 'ALTER MODIFY origem -> ' + ENUM_NOVO}")

        if not aplicar:
            print("\n" + "=" * 80)
            print("DRY-RUN — nada foi alterado.")
            print("ROLLBACK (após o --apply): DROP TABLE " + TABELA + "; "
                  "ALTER TABLE nfe_importacoes MODIFY origem <enum anterior do backup>;")
            print("Para aplicar:  python migrations/add_robo_config.py --apply")
            print("=" * 80)
            return 0

        # ---- APPLY ----
        print("\n" + "=" * 80)
        print("APLICANDO")
        print("=" * 80)

        # Backup do estado anterior (para o rollback do ENUM ser exato).
        cur.execute(DDL_BKP)
        cur.execute(f"INSERT INTO {BKP} (origem_enum_antes, robo_config_existia) "
                    "VALUES (%s, %s)", (enum_atual, 1 if existe else 0))
        print(f"  0. backup do estado anterior -> {BKP} (enum: {enum_atual})")

        cur.execute(DDL_ROBO)
        print(f"  1. {TABELA}: {'já existia (no-op)' if existe else 'CRIADA'}")

        if tem_qrobo:
            print("  2. origem: já continha 'Q-ROBO' (no-op)")
        else:
            cur.execute("ALTER TABLE nfe_importacoes MODIFY origem " + ENUM_NOVO)
            print("  2. origem: ALTER aplicado")

        # Verificação pós.
        ok_tab = tabela_existe(cur, TABELA)
        ok_enum = 'Q-ROBO' in (enum_origem(cur) or '')
        print("\nVERIFICAÇÃO PÓS:")
        print(f"  {TABELA} existe? .......... {'SIM' if ok_tab else 'NÃO'}")
        print(f"  origem contém 'Q-ROBO'? .. {'SIM' if ok_enum else 'NÃO'}")
        if not (ok_tab and ok_enum):
            print("  ATENÇÃO: verificação falhou — revise antes de seguir.")
            return 1
        print("  OK.")

        # Rollback impresso.
        n_qrobo = linhas_qrobo(cur)
        print("\n" + "-" * 80)
        print("ROLLBACK MANUAL:")
        print(f"  DROP TABLE {TABELA};")
        if n_qrobo == 0:
            print("  ALTER TABLE nfe_importacoes MODIFY origem "
                  f"{enum_atual} NOT NULL DEFAULT 'UPLOAD';")
        else:
            print(f"  -- {n_qrobo} linha(s) já usam 'Q-ROBO': NÃO reverta o ENUM sem antes")
            print("  --   reatribuir/remover essas linhas (senão o valor não cabe no ENUM antigo).")
        print(f"  DROP TABLE {BKP};   -- (após concluir o rollback)")
        return 0
    finally:
        cur.close()
        cnx.close()


if __name__ == '__main__':
    sys.exit(main())
