# -*- coding: utf-8 -*-
"""Portal do Instalador Q-Robô — Fase 1: auditoria + autoria da chave.

ADITIVA e IDEMPOTENTE. Não altera nem apaga NENHUMA linha existente:

  1. CREATE TABLE qrobo_auditoria — trilha append-only (nunca UPDATE/DELETE) de
     cada chave gerada/regerada e cada download do instalador. Guarda SNAPSHOT
     de usuário/número/razão social (o histórico tem que continuar legível se o
     cadastro mudar) e só o PREFIXO do token (8 chars) — trilha não é cofre.
     SEM foreign key de propósito: excluir um cliente ou um usuário não pode
     apagar a auditoria dele.

  2. ALTER robo_config += token_gerado_em / token_gerado_por / token_versao.
     Respondem "quem gerou a chave VIGENTE" sem varrer a trilha. Todas as três
     são NULL/DEFAULT — as 3 linhas existentes seguem intactas e válidas.

  3. CREATE TABLE IF NOT EXISTS robo_config — só rede de proteção. A robo_config
     foi criada pela migration da Fase 1 do Q-Robô e existe em produção; aqui é
     no-op. Existe para que um banco novo (ou um restore) não quebre no ALTER
     do passo 2.

BACKUP: grava em qrobo_migracao_bkp o que existia ANTES (tabela já existia? quais
colunas foram criadas de fato). É o que torna o rollback exato — só se derruba o
que esta migration criou.

ROLLBACK (impresso no fim do --apply):
    DROP TABLE qrobo_auditoria;
    ALTER TABLE robo_config DROP COLUMN <só as criadas>;
  A robo_config e os robo_token em uso NÃO são tocados pelo rollback.

Uso:
    python migrations/add_qrobo_auditoria.py            # DRY-RUN (nada grava)
    python migrations/add_qrobo_auditoria.py --apply    # aplica
    python migrations/add_qrobo_auditoria.py --schema   # só mostra o schema atual
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                               # noqa: E402
from config import Config                            # noqa: E402

TABELA = 'qrobo_auditoria'
BKP = 'qrobo_migracao_bkp'
ROBO = 'robo_config'

DDL_AUDITORIA = f"""
CREATE TABLE IF NOT EXISTS {TABELA} (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    acao              ENUM('CHAVE_GERADA','CHAVE_REGERADA','DOWNLOAD_INSTALADOR') NOT NULL,
    origem            ENUM('PORTAL','ADMIN') NOT NULL DEFAULT 'PORTAL',
    usuario_id        INT           NULL,
    usuario_nome      VARCHAR(150)  NOT NULL,
    cliente_id        INT           NULL,
    numero_cliente    VARCHAR(20)   NULL,
    razao_social      VARCHAR(255)  NULL,
    token_prefixo     VARCHAR(12)   NULL,
    instalador_versao VARCHAR(20)   NULL,
    ip                VARCHAR(45)   NULL,
    user_agent        VARCHAR(255)  NULL,
    criado_em         DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_qra_usuario (usuario_id, criado_em),
    KEY idx_qra_cliente (cliente_id, criado_em),
    KEY idx_qra_acao    (acao, criado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# Espelha migrations/add_robo_config.py — rede de proteção para banco novo.
DDL_ROBO = f"""
CREATE TABLE IF NOT EXISTS {ROBO} (
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

COLUNAS_ROBO = [
    ('token_gerado_em',  'DATETIME NULL'),
    ('token_gerado_por', 'INT NULL'),
    ('token_versao',     'INT NOT NULL DEFAULT 1'),
]

DDL_BKP = f"""
CREATE TABLE IF NOT EXISTS {BKP} (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    tabela_existia    TINYINT(1),
    colunas_criadas   TEXT,
    aplicado_em       TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

L = 92


def conectar():
    # time_zone='-03:00' igual ao pool do app (utils/db_helper.py): o
    # DEFAULT CURRENT_TIMESTAMP da auditoria grava em BRT, não em UTC.
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        charset='utf8mb4', autocommit=True, time_zone='-03:00',
    )


def tabela_existe(cur, nome):
    cur.execute("SELECT COUNT(*) AS c FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = %s", (nome,))
    return cur.fetchone()['c'] > 0


def colunas(cur, tabela):
    cur.execute("SELECT column_name AS cn, column_type AS ct, is_nullable AS nu, "
                "       column_default AS df "
                "  FROM information_schema.columns "
                " WHERE table_schema = DATABASE() AND table_name = %s "
                " ORDER BY ordinal_position", (tabela,))
    return cur.fetchall()


def indices(cur, tabela):
    cur.execute("SELECT index_name AS ix, GROUP_CONCAT(column_name "
                "       ORDER BY seq_in_index) AS cols, MIN(non_unique) AS nu "
                "  FROM information_schema.statistics "
                " WHERE table_schema = DATABASE() AND table_name = %s "
                " GROUP BY index_name ORDER BY index_name", (tabela,))
    return cur.fetchall()


def mostra_schema(cur, tabela):
    if not tabela_existe(cur, tabela):
        print(f"    (tabela {tabela} não existe)")
        return
    for c in colunas(cur, tabela):
        nulo = 'NULL' if c['nu'] == 'YES' else 'NOT NULL'
        dflt = '' if c['df'] is None else f"  DEFAULT {c['df']}"
        print(f"    {c['cn']:<20} {c['ct']:<62} {nulo}{dflt}")
    for i in indices(cur, tabela):
        tipo = 'UNIQUE' if i['nu'] == 0 else 'KEY   '
        print(f"    -- {tipo} {i['ix']} ({i['cols']})")


def main():
    aplicar = '--apply' in sys.argv
    so_schema = '--schema' in sys.argv
    cnx = conectar()
    cur = cnx.cursor(dictionary=True)
    try:
        modo = '[--APPLY]' if aplicar else ('[--SCHEMA]' if so_schema else '[DRY-RUN]')
        print("#" * L)
        print(f"#  PORTAL DO INSTALADOR Q-ROBO — Fase 1 (auditoria)   {modo}")
        print(f"#  banco: {Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")
        cur.execute("SELECT NOW() AS brt, UTC_TIMESTAMP() AS utc, @@session.time_zone AS tz")
        t = cur.fetchone()
        print(f"#  agora: BRT {t['brt']}  |  UTC {t['utc']}  |  sessao tz={t['tz']}")
        print("#" * L)

        existe_aud = tabela_existe(cur, TABELA)
        existe_robo = tabela_existe(cur, ROBO)
        cols_robo = {c['cn'] for c in colunas(cur, ROBO)} if existe_robo else set()
        faltando = [(c, d) for c, d in COLUNAS_ROBO if c not in cols_robo]

        if so_schema:
            print(f"\nSCHEMA ATUAL — {ROBO}:")
            mostra_schema(cur, ROBO)
            print(f"\nSCHEMA ATUAL — {TABELA}:")
            mostra_schema(cur, TABELA)
            return 0

        print("\nESTADO ATUAL:")
        print(f"  tabela {TABELA} existe? ......... {'SIM' if existe_aud else 'NÃO'}")
        print(f"  tabela {ROBO} existe? ........... {'SIM' if existe_robo else 'NÃO'}")
        if existe_robo:
            cur.execute(f"SELECT COUNT(*) AS c FROM {ROBO}")
            print(f"  linhas em {ROBO} ................ {cur.fetchone()['c']} "
                  "(NÃO são tocadas por esta migration)")
        print(f"  colunas de autoria já em {ROBO}:  "
              f"{sorted(cols_robo & {c for c, _ in COLUNAS_ROBO}) or 'nenhuma'}")
        print(f"  colunas a criar ................. "
              f"{[c for c, _ in faltando] or 'nenhuma (já está tudo lá)'}")

        print("\nDDL EXATO QUE SERÁ EXECUTADO:")
        print(f"\n  1) {'(no-op, já existe) ' if existe_robo else ''}{DDL_ROBO.strip()}")
        print(f"\n  2) {'(no-op, já existe) ' if existe_aud else ''}{DDL_AUDITORIA.strip()}")
        print("\n  3) colunas de autoria em robo_config:")
        for c, d in COLUNAS_ROBO:
            marca = '(no-op, já existe)  ' if c not in {x for x, _ in faltando} else ''
            print(f"       {marca}ALTER TABLE {ROBO} ADD COLUMN {c} {d};")

        if not aplicar:
            print("\n" + "=" * L)
            print("DRY-RUN — NADA foi alterado no banco.")
            print("Aplicar:   python migrations/add_qrobo_auditoria.py --apply")
            print("=" * L)
            return 0

        # ---- APPLY ----
        print("\n" + "=" * L)
        print("APLICANDO")
        print("=" * L)

        cur.execute(DDL_BKP)
        cur.execute(f"INSERT INTO {BKP} (tabela_existia, colunas_criadas) VALUES (%s, %s)",
                    (1 if existe_aud else 0, ','.join(c for c, _ in faltando) or None))
        print(f"  0. estado anterior salvo em {BKP} "
              f"(tabela_existia={1 if existe_aud else 0}, "
              f"colunas_criadas={[c for c, _ in faltando] or 'nenhuma'})")

        cur.execute(DDL_ROBO)
        print(f"  1. {ROBO}: {'já existia (no-op)' if existe_robo else 'CRIADA'}")

        cur.execute(DDL_AUDITORIA)
        print(f"  2. {TABELA}: {'já existia (no-op)' if existe_aud else 'CRIADA'}")

        for c, d in faltando:
            cur.execute(f"ALTER TABLE {ROBO} ADD COLUMN {c} {d}")
            print(f"  3. {ROBO}.{c}: CRIADA ({d})")
        if not faltando:
            print(f"  3. {ROBO}: nenhuma coluna a criar (no-op)")

        # ---- VERIFICAÇÃO PÓS ----
        print("\nVERIFICAÇÃO PÓS:")
        ok_tab = tabela_existe(cur, TABELA)
        cols_depois = {c['cn'] for c in colunas(cur, ROBO)}
        ok_cols = all(c in cols_depois for c, _ in COLUNAS_ROBO)
        cur.execute(f"SELECT COUNT(*) AS c FROM {ROBO}")
        n_robo = cur.fetchone()['c']
        cur.execute(f"SELECT COUNT(*) AS c FROM {TABELA}")
        n_aud = cur.fetchone()['c']
        print(f"  {TABELA} existe? .............. {'SIM' if ok_tab else 'NÃO'}")
        print(f"  colunas de autoria presentes? .. {'SIM' if ok_cols else 'NÃO'}")
        print(f"  linhas em {ROBO} ............... {n_robo} (esperado: inalterado)")
        print(f"  linhas em {TABELA} ............. {n_aud} (esperado: 0, trilha começa vazia)")
        if not (ok_tab and ok_cols):
            print("  ATENÇÃO: verificação FALHOU — revise antes de seguir.")
            return 1
        print("  OK.")

        print(f"\nSCHEMA DEPOIS — {ROBO}:")
        mostra_schema(cur, ROBO)
        print(f"\nSCHEMA DEPOIS — {TABELA}:")
        mostra_schema(cur, TABELA)

        print("\nCONTEÚDO ATUAL DE robo_config (as 3 novas colunas nas linhas existentes):")
        cur.execute(f"SELECT r.cliente_id, c.numero_cliente, c.nome_razao_social, "
                    f"       r.ativo, r.data_inicio_captura, r.token_gerado_em, "
                    f"       r.token_gerado_por, r.token_versao "
                    f"  FROM {ROBO} r LEFT JOIN clientes c ON c.id = r.cliente_id "
                    f" ORDER BY r.cliente_id")
        for r in cur.fetchall():
            print(f"    cli {r['cliente_id']:>3} ({str(r['numero_cliente']):<5}) "
                  f"{str(r['nome_razao_social'])[:34]:<34} ativo={r['ativo']} "
                  f"inicio={r['data_inicio_captura']} gerado_em={r['token_gerado_em']} "
                  f"por={r['token_gerado_por']} versao={r['token_versao']}")

        print("\n" + "-" * L)
        print("ROLLBACK MANUAL (derruba SÓ o que esta migration criou):")
        if not existe_aud:
            print(f"  DROP TABLE {TABELA};")
        else:
            print(f"  -- {TABELA} já existia antes: NÃO derrube.")
        for c, _ in faltando:
            print(f"  ALTER TABLE {ROBO} DROP COLUMN {c};")
        if not faltando:
            print(f"  -- nenhuma coluna criada por esta execução em {ROBO}.")
        print(f"  DROP TABLE {BKP};   -- (por último, após concluir o rollback)")
        print("  -- robo_config, robo_token e as saídas capturadas NÃO são tocados.")
        print("-" * L)
        return 0
    finally:
        cur.close()
        cnx.close()


if __name__ == '__main__':
    sys.exit(main())
