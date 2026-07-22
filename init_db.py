"""
Script de inicialização do banco de dados.

Funções disponíveis:
- run_migrations(): Cria/atualiza todas as tabelas (CREATE TABLE IF NOT EXISTS + ALTER TABLE incrementais).
- create_admin_user(): Garante que o usuário admin padrão exista.
- dedup_nfe_vinculo(): Remove duplicatas de nfe_produto_vinculo (execute só quando necessário, nunca no boot).

Como usar via CLI:
    python init_db.py              # roda run_migrations() + create_admin_user()
    python init_db.py --dedup      # também executa dedup_nfe_vinculo()
"""
import sys
import mysql.connector
from config import Config
from utils.db_helper import execute_query, get_last_db_error
from utils.auth_helper import hash_password

# Advisory lock que serializa as migrations entre os workers do gunicorn.
_MIGRATION_LOCK = 'qualicontax_migrations'
_MIGRATION_LOCK_TIMEOUT = 120  # segundos que um worker espera pelo lock


def _migrate(sql, fetch=False):
    """Executa um DDL de migration de forma FATAL.

    Ao contrário do ``execute_query`` (que loga o erro e devolve ``None`` para o
    resto do app seguir), aqui uma falha de ``CREATE``/``ALTER`` levanta
    ``RuntimeError`` com a query e o erro real do banco — abortando o boot em vez
    de subir um deploy "com sucesso" e schema incompleto (o que aconteceu na
    Fase 1). O parâmetro ``fetch`` é aceito só por compatibilidade com o formato
    das chamadas existentes; migration é sempre DDL não-fetch.
    """
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        primeira = next((ln.strip() for ln in sql.splitlines() if ln.strip()), sql.strip())
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {primeira[:180]}')


def run_migrations():
    """Serializa as migrations entre os workers via advisory lock do MySQL.

    Sem ``--preload``, cada um dos 4 workers do gunicorn chama isto no boot
    (``MIGRATIONS_DONE`` é env por-processo). O ``GET_LOCK`` garante que só UM roda
    o DDL por vez — os outros esperam a liberação e seguem (o corpo é idempotente,
    então a 2ª passagem é no-op rápido). Isso mata a corrida de CREATE/ALTER
    simultâneos que gerava contenção de metadata lock no boot.

    O lock vive numa conexão DEDICADA (``GET_LOCK`` é por sessão; não pode usar o
    pool, cujas conexões giram entre chamadas). Se o lock não vier no timeout,
    outro worker está migrando — seguimos sem reexecutar.
    """
    conn = None
    got_lock = False
    try:
        conn = mysql.connector.connect(
            host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
            user=Config.DB_USER, password=Config.DB_PASSWORD,
            connection_timeout=Config.DB_CONNECT_TIMEOUT, autocommit=True,
        )
        cur = conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, %s)", (_MIGRATION_LOCK, _MIGRATION_LOCK_TIMEOUT))
        got_lock = (cur.fetchone() or [0])[0] == 1
        cur.close()
        if not got_lock:
            print(f"⚠ Migrations: lock '{_MIGRATION_LOCK}' não obtido em "
                  f"{_MIGRATION_LOCK_TIMEOUT}s — outro worker está migrando. Seguindo.")
            return
        _apply_migrations()
    finally:
        if conn is not None:
            try:
                if got_lock:
                    _c = conn.cursor()
                    _c.execute("SELECT RELEASE_LOCK(%s)", (_MIGRATION_LOCK,))
                    _c.fetchall()
                    _c.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass


def _apply_migrations():
    """
    Cria ou atualiza todas as tabelas do banco de dados.
    Usa CREATE TABLE IF NOT EXISTS + ALTER TABLE incremental para ser idempotente.
    Seguro executar múltiplas vezes.
    """

    # ---- Tabela principal de Usuários ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL UNIQUE,
            senha_hash VARCHAR(512) NOT NULL,
            tipo_usuario ENUM('ADMIN','GERENTE','CONTADOR','ASSISTENTE','ESTAGIARIO') NOT NULL DEFAULT 'ASSISTENTE',
            situacao ENUM('ATIVO','INATIVO') NOT NULL DEFAULT 'ATIVO',
            cpf VARCHAR(14) NULL,
            telefone VARCHAR(20) NULL,
            departamento_id INT NULL,
            cargo VARCHAR(100) NULL,
            capacidade_tarefas INT NOT NULL DEFAULT 10,
            data_admissao DATE NULL,
            foto_perfil VARCHAR(500) NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_email (email),
            INDEX idx_tipo (tipo_usuario),
            INDEX idx_situacao (situacao)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # Incremental: garante colunas que podem estar ausentes em DBs mais antigos.
    for _col_name, _col_def in [
        ('cpf',               'VARCHAR(14) NULL'),
        ('telefone',          'VARCHAR(20) NULL'),
        ('departamento_id',   'INT NULL'),
        ('cargo',             'VARCHAR(100) NULL'),
        ('capacidade_tarefas','INT NOT NULL DEFAULT 10'),
        ('data_admissao',     'DATE NULL'),
        ('foto_perfil',       'VARCHAR(500) NULL'),
        ('tipo_usuario',      "ENUM('ADMIN','GERENTE','CONTADOR','ASSISTENTE','ESTAGIARIO') NOT NULL DEFAULT 'ASSISTENTE'"),
        ('situacao',          "ENUM('ATIVO','INATIVO') NOT NULL DEFAULT 'ATIVO'"),
        ('criado_em',         'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
        ('atualizado_em',     'TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'),
        ('login',             'VARCHAR(100) NULL'),
    ]:
        try:
            _col_exists = execute_query(
                "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usuarios' AND COLUMN_NAME = %s",
                (_col_name,), fetch=True, fetch_one=True,
            ) or {}
            if _col_exists.get('cnt', 0) == 0:
                execute_query(
                    f"ALTER TABLE usuarios ADD COLUMN {_col_name} {_col_def}",
                    fetch=False,
                )
        except Exception:
            pass

    # Unique index on login
    try:
        _idx_exists = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usuarios' AND INDEX_NAME = 'idx_login'",
            fetch=True, fetch_one=True,
        ) or {}
        if _idx_exists.get('cnt', 0) == 0:
            execute_query(
                "ALTER TABLE usuarios ADD UNIQUE INDEX idx_login (login)",
                fetch=False,
            )
    except Exception:
        pass

    # Backfill: garante login='admin' no admin padrão se estiver NULL
    try:
        execute_query(
            "UPDATE usuarios SET login='admin' WHERE tipo_usuario='ADMIN' AND (login IS NULL OR login='')",
            fetch=False,
        )
    except Exception:
        pass

    # ---- Planos de Contas ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS planos_contas (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(255) NOT NULL,
            descricao TEXT NULL,
            grupo_id INT NULL,
            situacao ENUM('ATIVO', 'INATIVO') NOT NULL DEFAULT 'ATIVO',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (grupo_id) REFERENCES grupos_clientes(id) ON DELETE SET NULL,
            INDEX idx_grupo (grupo_id),
            INDEX idx_situacao (situacao)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    _migrate("""
        CREATE TABLE IF NOT EXISTS plano_contas_itens (
            id INT AUTO_INCREMENT PRIMARY KEY,
            plano_id INT NOT NULL,
            codigo VARCHAR(50) NOT NULL,
            descricao VARCHAR(255) NOT NULL,
            tipo ENUM('ANALITICA', 'SINTETICA') NOT NULL,
            natureza ENUM('DEVEDORA', 'CREDORA') NOT NULL,
            grupo_contabil ENUM('ATIVO', 'PASSIVO', 'PATRIMONIO_LIQUIDO', 'RECEITA', 'DESPESA') NOT NULL,
            situacao ENUM('ATIVO', 'INATIVO') NOT NULL DEFAULT 'ATIVO',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (plano_id) REFERENCES planos_contas(id) ON DELETE CASCADE,
            INDEX idx_plano (plano_id),
            INDEX idx_codigo (codigo)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Municípios ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS municipios (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            uf CHAR(2) NOT NULL,
            site_prefeitura VARCHAR(500) DEFAULT NULL,
            situacao ENUM('ATIVO', 'INATIVO') NOT NULL DEFAULT 'ATIVO',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_municipio_uf_nome (uf, nome)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Contas Correntes (Conciliação Bancária) ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS contas_correntes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NOT NULL,
            banco_nome VARCHAR(100) NOT NULL,
            banco_codigo VARCHAR(10) NOT NULL,
            agencia VARCHAR(20) NOT NULL,
            agencia_digito VARCHAR(2) DEFAULT '',
            numero_conta VARCHAR(30) NOT NULL,
            conta_digito VARCHAR(2) NOT NULL,
            tipo ENUM('CORRENTE', 'POUPANCA') NOT NULL DEFAULT 'CORRENTE',
            saldo DECIMAL(15, 2) NOT NULL DEFAULT 0.00,
            ativa TINYINT(1) NOT NULL DEFAULT 1,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
            INDEX idx_cliente (cliente_id),
            INDEX idx_ativa (ativa)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # Incremental: colunas contas_correntes
    for _col_name, _col_def in [
        ("cliente_id",    "INT NULL"),
        ("banco_nome",    "VARCHAR(100) NOT NULL DEFAULT ''"),
        ("banco_codigo",  "VARCHAR(10) NOT NULL DEFAULT ''"),
        ("agencia",       "VARCHAR(20) NOT NULL DEFAULT ''"),
        ("agencia_digito","VARCHAR(2) DEFAULT ''"),
        ("numero_conta",  "VARCHAR(30) NOT NULL DEFAULT ''"),
        ("conta_digito",  "VARCHAR(2) NOT NULL DEFAULT ''"),
        ("tipo",          "ENUM('CORRENTE','POUPANCA') NOT NULL DEFAULT 'CORRENTE'"),
        ("saldo",         "DECIMAL(15,2) NOT NULL DEFAULT 0.00"),
        ("ativa",         "TINYINT(1) NOT NULL DEFAULT 1"),
        ("criado_em",     "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ("atualizado_em", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    ]:
        try:
            _exists = execute_query(
                "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'contas_correntes' "
                "AND COLUMN_NAME = %s",
                (_col_name,),
                fetch=True,
                fetch_one=True,
            )
            if _exists and _exists.get('cnt', 0) == 0:
                execute_query(
                    f"ALTER TABLE contas_correntes ADD COLUMN {_col_name} {_col_def}",
                    fetch=False,
                )
        except Exception:
            pass

    # Incremental: empresa_id em conciliacoes_bancarias
    try:
        _tbl_exists = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'conciliacoes_bancarias'",
            fetch=True,
            fetch_one=True,
        )
        if _tbl_exists and _tbl_exists.get('cnt', 0) > 0:
            _emp_col_exists = execute_query(
                "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = 'conciliacoes_bancarias' "
                "AND COLUMN_NAME = 'empresa_id'",
                fetch=True,
                fetch_one=True,
            )
            if _emp_col_exists and _emp_col_exists.get('cnt', 0) == 0:
                execute_query(
                    "ALTER TABLE conciliacoes_bancarias "
                    "ADD COLUMN empresa_id INT NULL, "
                    "ADD INDEX idx_empresa (empresa_id)",
                    fetch=False,
                )
    except Exception:
        pass

    # ---- NF-e (Conferência de Compras) ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS nfe_importacoes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NULL,
            grupo_id INT NULL,
            nome_arquivo VARCHAR(500) NOT NULL,
            chave_acesso VARCHAR(44) UNIQUE NOT NULL,
            num_nota VARCHAR(20) DEFAULT '',
            serie VARCHAR(5) DEFAULT '',
            data_emissao DATE NULL,
            emit_cnpj VARCHAR(18) DEFAULT '',
            emit_nome VARCHAR(255) DEFAULT '',
            emit_uf VARCHAR(2) DEFAULT '',
            dest_cnpj VARCHAR(18) DEFAULT '',
            dest_nome VARCHAR(255) DEFAULT '',
            valor_total DECIMAL(15,2) NOT NULL DEFAULT 0.00,
            valor_icms DECIMAL(15,2) NOT NULL DEFAULT 0.00,
            valor_pis DECIMAL(15,2) NOT NULL DEFAULT 0.00,
            valor_cofins DECIMAL(15,2) NOT NULL DEFAULT 0.00,
            valor_ipi DECIMAL(15,2) NOT NULL DEFAULT 0.00,
            cfop VARCHAR(10) DEFAULT '',
            natureza_operacao VARCHAR(255) DEFAULT '',
            xml_raw MEDIUMTEXT,
            origem ENUM('UPLOAD','DROPBOX') NOT NULL DEFAULT 'UPLOAD',
            importado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_chave (chave_acesso),
            INDEX idx_emit_cnpj (emit_cnpj),
            INDEX idx_data (data_emissao),
            INDEX idx_dest_cnpj (dest_cnpj),
            INDEX idx_cliente (cliente_id),
            INDEX idx_grupo (grupo_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # Incremental: cliente_id / grupo_id em nfe_importacoes
    for _col, _defn in [('cliente_id', 'INT NULL'), ('grupo_id', 'INT NULL')]:
        _exists = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_importacoes' AND COLUMN_NAME = %s",
            (_col,), fetch=True, fetch_one=True,
        ) or {}
        if _exists.get('cnt', 0) == 0:
            execute_query(f"ALTER TABLE nfe_importacoes ADD COLUMN {_col} {_defn}", fetch=False)

    # Incremental: cancelada em nfe_importacoes (flag de cancelamento por evento)
    try:
        _can_exists = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_importacoes' AND COLUMN_NAME = 'cancelada'",
            fetch=True, fetch_one=True,
        ) or {}
        if _can_exists.get('cnt', 0) == 0:
            execute_query(
                "ALTER TABLE nfe_importacoes ADD COLUMN cancelada TINYINT(1) NOT NULL DEFAULT 0",
                fetch=False,
            )
    except Exception:
        pass

    # Incremental: tipo e dest_uf em nfe_importacoes (Conferência de Saídas)
    for _col, _defn in [
        ('tipo',    "ENUM('entrada','saida') NOT NULL DEFAULT 'entrada'"),
        ('dest_uf', 'VARCHAR(2) NULL'),
    ]:
        try:
            _exists = execute_query(
                "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_importacoes' AND COLUMN_NAME = %s",
                (_col,), fetch=True, fetch_one=True,
            ) or {}
            if _exists.get('cnt', 0) == 0:
                execute_query(f"ALTER TABLE nfe_importacoes ADD COLUMN {_col} {_defn}", fetch=False)
        except Exception:
            pass

    # Incremental: troca UNIQUE (chave_acesso) → (chave_acesso, tipo)
    try:
        _new_uk = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_importacoes' "
            "AND INDEX_NAME = 'uk_chave_tipo'",
            fetch=True, fetch_one=True,
        ) or {}
        if _new_uk.get('cnt', 0) == 0:
            try:
                execute_query("ALTER TABLE nfe_importacoes DROP INDEX chave_acesso", fetch=False)
            except Exception:
                pass
            execute_query(
                "ALTER TABLE nfe_importacoes ADD UNIQUE KEY uk_chave_tipo (chave_acesso, tipo)",
                fetch=False,
            )
    except Exception:
        pass

    _migrate("""
        CREATE TABLE IF NOT EXISTS nfe_itens (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nfe_id INT NOT NULL,
            num_item INT NOT NULL DEFAULT 1,
            codigo_produto VARCHAR(60) DEFAULT '',
            descricao VARCHAR(255) DEFAULT '',
            ncm VARCHAR(8) DEFAULT '',
            cfop VARCHAR(4) DEFAULT '',
            unidade VARCHAR(6) DEFAULT '',
            quantidade DECIMAL(15,4) NOT NULL DEFAULT 0.0000,
            valor_unitario DECIMAL(15,4) NOT NULL DEFAULT 0.0000,
            valor_total DECIMAL(15,2) NOT NULL DEFAULT 0.00,
            valor_icms DECIMAL(15,2) NOT NULL DEFAULT 0.00,
            valor_pis DECIMAL(15,2) NOT NULL DEFAULT 0.00,
            valor_cofins DECIMAL(15,2) NOT NULL DEFAULT 0.00,
            produto_catalogo_id INT NULL,
            FOREIGN KEY (nfe_id) REFERENCES nfe_importacoes(id) ON DELETE CASCADE,
            INDEX idx_nfe (nfe_id),
            INDEX idx_ncm (ncm),
            INDEX idx_produto (codigo_produto(20))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # Incremental: produto_catalogo_id em nfe_itens
    _pcat_exists = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_itens' AND COLUMN_NAME = 'produto_catalogo_id'",
        fetch=True, fetch_one=True,
    ) or {}
    if _pcat_exists.get('cnt', 0) == 0:
        execute_query("ALTER TABLE nfe_itens ADD COLUMN produto_catalogo_id INT NULL", fetch=False)

    # Incremental: índice composto em nfe_itens
    _idx_vinc_exists = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_itens' AND INDEX_NAME = 'idx_nfe_pcat'",
        fetch=True, fetch_one=True,
    ) or {}
    if _idx_vinc_exists.get('cnt', 0) == 0:
        execute_query(
            "ALTER TABLE nfe_itens ADD INDEX idx_nfe_pcat (nfe_id, produto_catalogo_id)",
            fetch=False,
        )

    # ---- Catálogo de Produtos ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS nfe_produtos_catalogo (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NULL,
            grupo_id INT NULL,
            codigo VARCHAR(60) DEFAULT '',
            nome VARCHAR(255) NOT NULL,
            categoria VARCHAR(100) DEFAULT '',
            subcategoria VARCHAR(100) DEFAULT '',
            tipo_uso VARCHAR(50) NULL DEFAULT NULL,
            unidade VARCHAR(6) DEFAULT '',
            ativo TINYINT(1) NOT NULL DEFAULT 1,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_cli (cliente_id),
            INDEX idx_grp (grupo_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # Incremental: tipo_uso em nfe_produtos_catalogo
    _col_exists = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_produtos_catalogo' AND COLUMN_NAME = 'tipo_uso'",
        fetch=True, fetch_one=True,
    ) or {}
    if _col_exists.get('cnt', 0) == 0:
        execute_query(
            "ALTER TABLE nfe_produtos_catalogo ADD COLUMN tipo_uso VARCHAR(50) NULL DEFAULT NULL AFTER subcategoria",
            fetch=False,
        )

    # ---- Categorias e Sub-Categorias de Produtos ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS nfe_produto_categorias (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            ordem INT NOT NULL DEFAULT 0,
            UNIQUE KEY uk_cat_nome (nome)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    _migrate("""
        CREATE TABLE IF NOT EXISTS nfe_produto_subcategorias (
            id INT AUTO_INCREMENT PRIMARY KEY,
            categoria_id INT NOT NULL,
            nome VARCHAR(100) NOT NULL,
            ordem INT NOT NULL DEFAULT 0,
            UNIQUE KEY uk_subcat (categoria_id, nome),
            FOREIGN KEY (categoria_id) REFERENCES nfe_produto_categorias(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # Seed das categorias/subcategorias padrão (apenas se a tabela estiver vazia)
    _cat_count = execute_query(
        "SELECT COUNT(*) AS cnt FROM nfe_produto_categorias",
        fetch=True, fetch_one=True,
    ) or {}
    if _cat_count.get('cnt', 0) == 0:
        _DEFAULT_CATS = [
            ('Combustíveis', 0, [
                'Gasolina Comum', 'Gasolina Aditivada',
                'Etanol Comum', 'Etanol Aditivado',
                'Diesel S-500 Comum', 'Diesel S-500 Aditivado',
                'Diesel S10 Comum', 'Diesel S10 Aditivado',
            ]),
            ('Loja de Conveniência', 1, ['Cigarros', 'Sorvetes', 'Salgados', 'Outros']),
            ('Lubrificantes e Aditivos', 2, ['Lubrificantes', 'Aditivos', 'Outros']),
            ('Insumos e Despesas', 3, []),
        ]
        for _cat_nome, _cat_ordem, _subs in _DEFAULT_CATS:
            _cat_id = execute_query(
                "INSERT INTO nfe_produto_categorias (nome, ordem) VALUES (%s, %s)",
                (_cat_nome, _cat_ordem),
            )
            for _sub_i, _sub_nome in enumerate(_subs):
                execute_query(
                    "INSERT INTO nfe_produto_subcategorias (categoria_id, nome, ordem) VALUES (%s, %s, %s)",
                    (_cat_id, _sub_nome, _sub_i),
                )

    # ---- Eventos NF-e (CC-e, cancelamento, manifestação) ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS nfe_eventos (
            id               INT AUTO_INCREMENT PRIMARY KEY,
            nfe_id           INT NULL,
            chave_nfe        VARCHAR(44) NOT NULL DEFAULT '',
            tp_evento        VARCHAR(6)  NOT NULL DEFAULT '',
            descricao_evento VARCHAR(150) DEFAULT '',
            seq_evento       INT NOT NULL DEFAULT 1,
            dh_evento        DATETIME NULL,
            xml_raw          MEDIUMTEXT,
            nome_arquivo     VARCHAR(500) DEFAULT '',
            criado_em        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (nfe_id) REFERENCES nfe_importacoes(id) ON DELETE SET NULL,
            INDEX idx_chave_nfe (chave_nfe),
            INDEX idx_nfe_id (nfe_id),
            INDEX idx_tp_evento (tp_evento)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Regras de vínculo automático (emit_cnpj + cod_xml → produto_catalogo) ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS nfe_produto_vinculo (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NULL,
            grupo_id INT NULL,
            emit_cnpj VARCHAR(18) NOT NULL DEFAULT '',
            codigo_produto_xml VARCHAR(60) NOT NULL DEFAULT '',
            produto_catalogo_id INT NOT NULL,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_vinculo (cliente_id, grupo_id, emit_cnpj, codigo_produto_xml),
            INDEX idx_lookup (emit_cnpj, codigo_produto_xml)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # Incremental: ramo_atividade_id e descricao_produto_xml em nfe_produto_vinculo
    for _col_name, _col_def in [
        ('ramo_atividade_id', 'INT NULL AFTER grupo_id'),
        ('descricao_produto_xml', 'VARCHAR(500) NULL AFTER codigo_produto_xml'),
    ]:
        _col_exists = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfe_produto_vinculo' AND COLUMN_NAME = %s",
            (_col_name,), fetch=True, fetch_one=True,
        ) or {}
        if _col_exists.get('cnt', 0) == 0:
            execute_query(
                f"ALTER TABLE nfe_produto_vinculo ADD COLUMN {_col_name} {_col_def}",
                fetch=False,
            )

    # ---- Cadastros Adicionais de Clientes ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS cadastros_adicionais_clientes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NOT NULL,
            tipo VARCHAR(100) NOT NULL,
            campo VARCHAR(150) NOT NULL,
            valor VARCHAR(255) NULL,
            data_referencia DATE NULL,
            observacoes TEXT NULL,
            ativo TINYINT(1) NOT NULL DEFAULT 1,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
            INDEX idx_cliente_id (cliente_id),
            INDEX idx_tipo (tipo)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Sócios de Clientes ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS socios_clientes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NOT NULL,
            nome VARCHAR(255) NOT NULL,
            cpf VARCHAR(14) NOT NULL,
            email VARCHAR(255) NULL,
            telefone VARCHAR(20) NULL,
            percentual_participacao DECIMAL(5,2) NOT NULL,
            responsavel TINYINT(1) NOT NULL DEFAULT 0,
            ativo TINYINT(1) NOT NULL DEFAULT 1,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
            INDEX idx_cliente_id (cliente_id),
            INDEX idx_cpf (cpf)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # Incremental: coluna telefone em socios_clientes
    try:
        _telefone_socio_exists = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'socios_clientes' AND COLUMN_NAME = 'telefone'",
            fetch=True, fetch_one=True,
        ) or {}
        if _telefone_socio_exists.get('cnt', 0) == 0:
            execute_query(
                "ALTER TABLE socios_clientes ADD COLUMN telefone VARCHAR(20) NULL AFTER email",
                fetch=False,
            )
    except Exception:
        pass

    # ---- Cadastros ANP ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS cadastros_anp (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NOT NULL,
            situacao VARCHAR(100) NULL,
            autorizacao VARCHAR(100) NULL,
            cnpj_anp VARCHAR(18) NULL,
            razao_social VARCHAR(255) NULL,
            nome_fantasia VARCHAR(255) NULL,
            endereco VARCHAR(255) NULL,
            complemento VARCHAR(100) NULL,
            bairro VARCHAR(100) NULL,
            municipio_uf VARCHAR(100) NULL,
            cep VARCHAR(10) NULL,
            nr_despacho VARCHAR(50) NULL,
            data_publicacao DATE NULL,
            bandeira VARCHAR(100) NULL,
            data_inicio_bandeira DATE NULL,
            tipo_posto VARCHAR(20) NULL,
            pmqc VARCHAR(50) NULL,
            delivery VARCHAR(10) NULL,
            latitude VARCHAR(30) NULL,
            longitude VARCHAR(30) NULL,
            data_emissao DATETIME NULL,
            fonte ENUM('PDF','MANUAL','DROPBOX') NOT NULL DEFAULT 'MANUAL',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
            INDEX idx_cliente_id (cliente_id),
            INDEX idx_autorizacao (autorizacao)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Sócios do Cadastro ANP ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS cadastros_anp_socios (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cadastro_anp_id INT NOT NULL,
            nome VARCHAR(255) NOT NULL,
            FOREIGN KEY (cadastro_anp_id) REFERENCES cadastros_anp(id) ON DELETE CASCADE,
            INDEX idx_cadastro_anp_id (cadastro_anp_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Produtos do Cadastro ANP ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS cadastros_anp_produtos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cadastro_anp_id INT NOT NULL,
            produto VARCHAR(255) NOT NULL,
            tancagem_m3 DECIMAL(10,3) NULL,
            bicos INT NULL,
            FOREIGN KEY (cadastro_anp_id) REFERENCES cadastros_anp(id) ON DELETE CASCADE,
            INDEX idx_cadastro_anp_id (cadastro_anp_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Contratos ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS contratos (
            id INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id INT NOT NULL,
            numero_contrato VARCHAR(50) NOT NULL,
            tipo_servico VARCHAR(100) NOT NULL,
            valor_mensal DECIMAL(10,2) NULL,
            data_inicio DATE NULL,
            data_fim DATE NULL,
            situacao VARCHAR(20) NOT NULL DEFAULT 'Ativo',
            observacoes TEXT NULL,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_cliente_id (cliente_id),
            INDEX idx_situacao (situacao)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Controle de Acesso ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS perfis_acesso (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(100) NOT NULL,
            descricao TEXT NULL,
            situacao ENUM('ATIVO','INATIVO') NOT NULL DEFAULT 'ATIVO',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_perfil_nome (nome)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    _migrate("""
        CREATE TABLE IF NOT EXISTS perfil_permissoes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            perfil_id INT NOT NULL,
            permissao_codigo VARCHAR(100) NOT NULL,
            FOREIGN KEY (perfil_id) REFERENCES perfis_acesso(id) ON DELETE CASCADE,
            UNIQUE KEY uk_perm (perfil_id, permissao_codigo)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    _migrate("""
        CREATE TABLE IF NOT EXISTS usuario_perfis (
            id INT AUTO_INCREMENT PRIMARY KEY,
            usuario_id INT NOT NULL,
            perfil_id INT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
            FOREIGN KEY (perfil_id) REFERENCES perfis_acesso(id) ON DELETE CASCADE,
            UNIQUE KEY uk_usuario_perfil (usuario_id, perfil_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    _migrate("""
        CREATE TABLE IF NOT EXISTS usuario_empresas_permitidas (
            id INT AUTO_INCREMENT PRIMARY KEY,
            usuario_id INT NOT NULL,
            cliente_id INT NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
            FOREIGN KEY (cliente_id) REFERENCES clientes(id) ON DELETE CASCADE,
            UNIQUE KEY uk_usuario_empresa (usuario_id, cliente_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    _migrate("""
        CREATE TABLE IF NOT EXISTS scheduler_import_log (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            iniciado_em DATETIME NOT NULL,
            concluido_em DATETIME,
            departamento VARCHAR(60) NOT NULL,
            origem      VARCHAR(20) NOT NULL DEFAULT 'agendado',
            usuario_id  INT,
            ok          INT NOT NULL DEFAULT 0,
            dup         INT NOT NULL DEFAULT 0,
            err         INT NOT NULL DEFAULT 0,
            moved_ok    INT NOT NULL DEFAULT 0,
            moved_err   INT NOT NULL DEFAULT 0,
            skipped     INT NOT NULL DEFAULT 0,
            detalhes    LONGTEXT,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    _migrate("""
        CREATE TABLE IF NOT EXISTS app_config (
            chave   VARCHAR(100) PRIMARY KEY,
            valor   TEXT NOT NULL,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Vínculo de Certificado Digital (.pfx) por empresa ----
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_certificados (
            id             INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id     INT NOT NULL,
            cnpj           VARCHAR(14) NOT NULL,
            tipo_doc       ENUM('CNPJ','CPF') NOT NULL DEFAULT 'CNPJ',
            senha_cifrada  VARBINARY(512) NOT NULL,
            dropbox_path   VARCHAR(500) NOT NULL,
            validade       DATE NULL,
            criado_em      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            atualizado_em  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_cliente (cliente_id),
            INDEX idx_cnpj (cnpj),
            CONSTRAINT fk_dfecert_cliente FOREIGN KEY (cliente_id)
                REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # Incremental: flags de captura automática de DFe (opt-in ligado por padrão).
    # Fatal via _migrate: o pré-check no INFORMATION_SCHEMA mantém a idempotência
    # (só altera se a coluna faltar); uma falha REAL do ALTER aborta o boot.
    for _col_name, _col_def in [
        ('modo_automatico', 'TINYINT(1) NOT NULL DEFAULT 1'),
        ('ativo',           'TINYINT(1) NOT NULL DEFAULT 1'),
    ]:
        _col_exists = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'dfe_certificados' "
            "AND COLUMN_NAME = %s",
            (_col_name,), fetch=True, fetch_one=True,
        ) or {}
        if _col_exists.get('cnt', 0) == 0:
            _migrate(f"ALTER TABLE dfe_certificados ADD COLUMN {_col_name} {_col_def}")

    # ---- Controle de NSU consumido por empresa (schema idêntico ao motor NH) ----
    # Colunas ult_consulta/proximo_permitido/ult_status batem com o SQL_NSU_OK/656
    # do NH: proximo_permitido é a trava de cota (SEFAZ pede aguardar 1h no 656).
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_nsu (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id        INT           NOT NULL,
            cnpj              VARCHAR(14)   NOT NULL,
            ult_nsu           BIGINT        NOT NULL DEFAULT 0,
            max_nsu           BIGINT        NOT NULL DEFAULT 0,
            ult_consulta      DATETIME      NULL,
            proximo_permitido DATETIME      NULL,
            ult_status        VARCHAR(255)  NULL,
            atualizado_em     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_nsu_cliente (cliente_id),
            KEY ix_nsu_cnpj (cnpj),
            CONSTRAINT fk_dfensu_cliente FOREIGN KEY (cliente_id)
                REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Documentos DFe (SÓ notas; eventos vão em dfe_eventos) ----------------
    # Schema idêntico ao motor NH (colunas tipo/xml_caminho/manifesto/conferido).
    # UNIQUE(chave): resumo + completa da mesma nota viram UMA linha; o upsert
    # (Fase 2) nunca rebaixa completa→resumo e NÃO mexe em situacao (preserva
    # 'cancelada' setada por evento). Cabeçalho no banco p/ a Conferência de
    # Compras listar por emissor/valor sem abrir o XML (arquivo fica no Dropbox).
    # expurgado/expurgado_em: aditivos do Qualicontax p/ o expurgo em lote
    # (apaga o XML, mantém o registro e marca quando foi apagado).
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_documentos (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            cliente_id        INT           NOT NULL,
            chave             CHAR(44)      NOT NULL,
            tipo              VARCHAR(6)    NOT NULL,
            nsu               BIGINT        NULL,
            schema_dfe        VARCHAR(40)   NULL,
            resumo            TINYINT(1)    NOT NULL DEFAULT 0,
            numero            VARCHAR(20)   NULL,
            serie             VARCHAR(6)    NULL,
            modelo            VARCHAR(4)    NULL,
            dh_emissao        DATETIME      NULL,
            emit_cnpj         VARCHAR(14)   NULL,
            emit_nome         VARCHAR(160)  NULL,
            dest_cnpj         VARCHAR(14)   NULL,
            valor_total       DECIMAL(14,2) NULL,
            situacao          VARCHAR(20)   NOT NULL DEFAULT 'autorizado',
            manifesto         VARCHAR(20)   NULL,
            xml_caminho       VARCHAR(300)  NULL,
            xml_expira_em     DATE          NULL,
            expurgado         TINYINT(1)    NOT NULL DEFAULT 0,
            expurgado_em      DATE          NULL,
            conferido         TINYINT(1)    NOT NULL DEFAULT 0,
            criado_em         TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_doc_chave (chave),
            KEY ix_doc_cliente (cliente_id),
            KEY ix_doc_tipo (tipo),
            KEY ix_doc_emit (emit_cnpj),
            KEY ix_doc_dh (dh_emissao),
            KEY ix_doc_situacao (situacao),
            KEY ix_doc_expira (xml_expira_em),
            CONSTRAINT fk_dfedoc_cliente FOREIGN KEY (cliente_id)
                REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Itens/produtos de cada documento (schema idêntico ao motor NH) -------
    # cod_anp/produto_id são específicos do NH (combustível): ficam NULL aqui,
    # mas mantidos para o INSERT do motor da Fase 2 casar sem reescrita.
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_itens (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            documento_id      INT            NOT NULL,
            n_item            INT            NOT NULL,
            produto_xml       VARCHAR(160)   NULL,
            cprod_fornecedor  VARCHAR(60)    NULL,
            cean              VARCHAR(20)    NULL,
            cod_anp           VARCHAR(12)    NULL,
            produto_id        INT            NULL,
            ncm               VARCHAR(10)    NULL,
            unidade           VARCHAR(6)     NULL,
            quantidade        DECIMAL(15,4)  NULL,
            valor_unitario    DECIMAL(15,6)  NULL,
            valor_total       DECIMAL(14,2)  NULL,
            criado_em         TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_item (documento_id, n_item),
            KEY ix_item_anp (cod_anp),
            KEY ix_item_produto (produto_id),
            CONSTRAINT fk_dfeitem_doc FOREIGN KEY (documento_id)
                REFERENCES dfe_documentos(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Eventos (procEventoNFe): 1 linha por evento, histórico com data/nSeq --
    # Tabela isolada (uma nota tem VÁRIOS eventos). UNIQUE(chave_evento). Liga na
    # nota por ch_nfe (valor, não FK): o evento pode chegar ANTES da nota. O
    # cancelamento também faz UPDATE dfe_documentos SET situacao='cancelada'.
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_eventos (
            id            INT           AUTO_INCREMENT PRIMARY KEY,
            cliente_id    INT           NOT NULL,
            chave_evento  VARCHAR(60)   NOT NULL,
            ch_nfe        CHAR(44)      NOT NULL,
            tp_evento     VARCHAR(6)    NULL,
            n_seq         INT           NULL,
            descricao     VARCHAR(160)  NULL,
            dh_evento     DATETIME      NULL,
            nsu           BIGINT        NULL,
            schema_dfe    VARCHAR(40)   NULL,
            org_cnpj      VARCHAR(14)   NULL,
            xml_caminho   VARCHAR(300)  NULL,
            xml_expira_em DATE          NULL,
            criado_em     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_chave_evento (chave_evento),
            KEY ix_ev_chnfe (ch_nfe),
            KEY ix_ev_tp (tp_evento),
            KEY ix_ev_expira (xml_expira_em),
            CONSTRAINT fk_dfeev_cliente FOREIGN KEY (cliente_id)
                REFERENCES clientes(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    # ---- Log de consultas de DFe (schema idêntico ao dfe_log do NH) -----------
    # Uma linha por RODADA (inclusive as puladas por cota/lock): sem FK e best-
    # effort, pra logar NUNCA derrubar a captura. Sem FK em cliente_id de propósito.
    _migrate("""
        CREATE TABLE IF NOT EXISTS dfe_consulta_log (
            id            BIGINT       AUTO_INCREMENT PRIMARY KEY,
            momento       DATETIME     NOT NULL,
            origem        VARCHAR(12)  NOT NULL,
            evento        VARCHAR(14)  NOT NULL,
            cliente_id    INT          NULL,
            cnpj          CHAR(14)     NULL,
            ult_nsu_env   BIGINT       NULL,
            c_stat        VARCHAR(6)   NULL,
            x_motivo      VARCHAR(300) NULL,
            ret_ult_nsu   BIGINT       NULL,
            ret_max_nsu   BIGINT       NULL,
            docs          INT          NOT NULL DEFAULT 0,
            notas         INT          NOT NULL DEFAULT 0,
            eventos       INT          NOT NULL DEFAULT 0,
            lote          INT          NULL,
            detalhe       VARCHAR(300) NULL,
            criado_em     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            KEY ix_log_momento (momento),
            KEY ix_log_cstat (c_stat),
            KEY ix_log_origem_evento (origem, evento)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """, fetch=False)

    print("✓ Migrations concluídas")


def dedup_nfe_vinculo():
    """
    Remove linhas duplicadas de nfe_produto_vinculo mantendo a mais recente (maior id).

    NÃO executar no boot automático — pode ser lento em tabelas grandes.
    Chamar manualmente via: python init_db.py --dedup
    """
    execute_query(
        """DELETE v_old
             FROM nfe_produto_vinculo v_old
             JOIN nfe_produto_vinculo v_new
               ON v_new.emit_cnpj          = v_old.emit_cnpj
              AND v_new.codigo_produto_xml  = v_old.codigo_produto_xml
              AND v_new.cliente_id         <=> v_old.cliente_id
              AND v_new.grupo_id           <=> v_old.grupo_id
              AND v_new.ramo_atividade_id  <=> v_old.ramo_atividade_id
              AND v_new.id > v_old.id""",
        fetch=False,
    )
    print("✓ Deduplicação de nfe_produto_vinculo concluída")


def create_admin_user():
    """Cria o usuário admin padrão se ainda não existir."""

    print("\nCriando usuário admin padrão...")

    # Verifica se já existe um admin usando o schema real (tipo_usuario)
    check_query = "SELECT id FROM usuarios WHERE tipo_usuario = 'ADMIN' LIMIT 1"
    existing_admin = execute_query(check_query, fetch=True, fetch_one=True)

    if existing_admin:
        print("✓ Já existe um usuário admin no sistema")
        return True

    # Cria admin padrão com o schema atual da tabela usuarios
    admin_email = "admin@qualicontax.com"
    admin_password = "admin123"
    admin_hash = hash_password(admin_password)

    insert_query = """
        INSERT INTO usuarios (nome, login, email, senha_hash, tipo_usuario, situacao)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    result = execute_query(insert_query, ("Administrador", "admin", admin_email, admin_hash, "ADMIN", "ATIVO"))

    if result:
        print("\n✓ Usuário admin criado com sucesso!")
        print(f"  Login: admin")
        print(f"  Email: {admin_email}")
        print(f"  Senha: {admin_password}")
        print("\n  ⚠️  IMPORTANTE: Altere a senha após o primeiro login!")
        return True
    else:
        print("✗ Erro ao criar usuário admin")
        return False


def main():
    """Função principal"""
    print("=" * 60)
    print("QUALICONTAX - Inicialização do Banco de Dados")
    print("=" * 60)
    print()

    run_migrations()

    if not create_admin_user():
        print("\n✗ Erro ao criar usuário admin.")
        sys.exit(1)

    if '--dedup' in sys.argv:
        dedup_nfe_vinculo()

    print("\n" + "=" * 60)
    print("✓ Inicialização concluída com sucesso!")
    print("=" * 60)
    print("\nVocê pode agora executar a aplicação com: gunicorn app:app --timeout 300")


if __name__ == '__main__':
    main()
