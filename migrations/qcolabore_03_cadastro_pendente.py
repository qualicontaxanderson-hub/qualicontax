# -*- coding: utf-8 -*-
"""
Q-COLABORE Bloco 1 / Parte 1 — migration 03: ``cadastro_pendente`` + join de
departamentos.

O que é
-------
A candidatura preenchida por quem consumiu um link tipo CADASTRO. Fica em
status PENDENTE até um admin APROVAR (vira conta em usuarios) ou RECUSAR. A
decisão é registrada com quem e quando — reprovar alguém não pode ser um evento
anônimo.

Decisões que valem explicação
-----------------------------
1) UNIQUE (link_id). Link de uso único ⇒ no máximo UMA pendência por link. É o
   segundo cinto do consumir(): mesmo que a corrida do UPDATE atômico
   escapasse, o UNIQUE barra a segunda inserção. Reenvio do formulário é UPDATE
   na mesma linha, nunca linha nova.

2) login_escolhido / cpf / e-mails NÃO têm UNIQUE aqui, de propósito.
   usuarios.login, usuarios.email e usuarios.cpf já são UNIQUE lá. Duas pessoas
   podem PEDIR o mesmo login; quem decide é a aprovação, e é lá que a colisão
   tem que virar mensagem decente para o admin — não erro de driver na cara do
   candidato enquanto ele preenche o formulário.

3) Endereço com a MESMA convenção de enderecos_clientes (cep, logradouro,
   numero, complemento, bairro, cidade, estado, pais) para o util de CEP ser o
   mesmo dos dois lados.

4) Sem FK, pelo mesmo motivo do roteador_log: a candidatura recusada é trilha e
   precisa sobreviver à exclusão do admin que a recusou.

Idempotente e reversível.

  python migrations/qcolabore_03_cadastro_pendente.py             # aplica
  python migrations/qcolabore_03_cadastro_pendente.py --reverter  # DROPa as duas
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# O console do Windows abre em cp1252 e os simbolos de status (checkmark) do
# padrao das migrations deste repo estouram UnicodeEncodeError NO PRINT — depois
# de o DDL ja ter rodado, deixando a migration pela metade e parecendo que
# falhou. Forca UTF-8 na saida para que rodar daqui seja igual a rodar no Railway.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from utils.db_helper import execute_query, get_last_db_error  # noqa: E402

TABELA = 'cadastro_pendente'
TABELA_DEP = 'cadastro_pendente_departamentos'


def _migrate(sql):
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {sql[:180]}')


def _tabela_existe(nome):
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (nome,), fetch=True, fetch_one=True,
    ) or {}
    return r.get('cnt', 0) > 0


def migrate_cadastro_pendente():
    """Cria cadastro_pendente e a join de departamentos (idempotente)."""
    print(f'Iniciando migração: tabela {TABELA} ...')
    _migrate("""
        CREATE TABLE IF NOT EXISTS cadastro_pendente (
            id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
            link_id             BIGINT       NOT NULL,
            ja_funcionario      TINYINT(1)   NOT NULL DEFAULT 0,

            nome_completo       VARCHAR(255) NOT NULL,
            cpf                 VARCHAR(14)  NULL,
            data_nascimento     DATE         NULL,
            login_escolhido     VARCHAR(100) NOT NULL,
            nick_escolhido      VARCHAR(60)  NULL,

            email_pessoal       VARCHAR(255) NULL,
            email_corporativo   VARCHAR(255) NULL,
            celular_pessoal     VARCHAR(20)  NULL,
            celular_corporativo VARCHAR(20)  NULL,

            cep                 VARCHAR(9)   NULL,
            logradouro          VARCHAR(255) NULL,
            numero              VARCHAR(20)  NULL,
            complemento         VARCHAR(100) NULL,
            bairro              VARCHAR(100) NULL,
            cidade              VARCHAR(100) NULL,
            estado              CHAR(2)      NULL,
            pais                VARCHAR(60)  NOT NULL DEFAULT 'Brasil',

            status              ENUM('PENDENTE','APROVADO','RECUSADO')
                                NOT NULL DEFAULT 'PENDENTE',
            decidido_por        INT          NULL,
            decidido_em         DATETIME     NULL,
            decisao_motivo      VARCHAR(255) NULL,
            usuario_id          INT          NULL,

            criado_em           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                             ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_pend_link (link_id),
            KEY ix_pend_status (status, criado_em),
            KEY ix_pend_login (login_escolhido),
            KEY ix_pend_cpf (cpf),
            KEY ix_pend_usuario (usuario_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    print(f'✓ Tabela {TABELA} pronta')

    print(f'Iniciando migração: tabela {TABELA_DEP} ...')
    # PK composta: um departamento aparece no máximo uma vez por candidatura.
    _migrate("""
        CREATE TABLE IF NOT EXISTS cadastro_pendente_departamentos (
            pendente_id     BIGINT NOT NULL,
            departamento_id INT    NOT NULL,
            PRIMARY KEY (pendente_id, departamento_id),
            KEY ix_cpd_dep (departamento_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    print(f'✓ Tabela {TABELA_DEP} pronta')
    return True


def rollback_cadastro_pendente():
    """Remove as duas tabelas (a join primeiro)."""
    print(f'Revertendo: {TABELA_DEP} e {TABELA} ...')
    if _tabela_existe(TABELA):
        n = (execute_query(
            f"SELECT COUNT(*) AS cnt FROM {TABELA} WHERE status = 'PENDENTE'",
            fetch=True, fetch_one=True) or {}).get('cnt', 0)
        if n:
            print(f'  ATENÇÃO: {n} candidatura(s) ainda PENDENTE(s) serão perdidas.')

    if _tabela_existe(TABELA_DEP):
        _migrate(f'DROP TABLE {TABELA_DEP}')
        print(f'✓ Tabela {TABELA_DEP} removida')
    else:
        print(f'✓ Tabela {TABELA_DEP} já não existe')

    if _tabela_existe(TABELA):
        _migrate(f'DROP TABLE {TABELA}')
        print(f'✓ Tabela {TABELA} removida')
    else:
        print(f'✓ Tabela {TABELA} já não existe')
    return True


if __name__ == '__main__':
    try:
        if '--reverter' in sys.argv:
            rollback_cadastro_pendente()
        else:
            migrate_cadastro_pendente()
        print('\n✓ Migração concluída com sucesso!')
    except Exception as e:
        print(f'\n✗ Migração falhou: {e}')
        sys.exit(1)
