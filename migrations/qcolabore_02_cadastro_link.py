# -*- coding: utf-8 -*-
"""
Q-COLABORE Bloco 1 / Parte 1 — migration 02: tabela ``cadastro_link``.

O que é
-------
O link de USO ÚNICO que um admin gera e entrega a alguém. Dois tipos:

  CADASTRO  o destinatário ainda não é conta — o link abre o formulário de
            candidatura, que vira uma linha em cadastro_pendente.
  SENHA     o destinatário JÁ é conta — o link abre a troca de senha.

Não tem cliente_id nem contato_id: o Bloco 1 é cadastro de FUNCIONÁRIO. Quem
gera é sempre um admin (criado_por).

Decisões que valem explicação
-----------------------------
1) Guarda o HASH do token, nunca o token. Diferente do robo_config.robo_token,
   que grava em claro — lá se justifica (o robô reapresenta a chave); aqui o
   token vale UMA vez e nunca precisa ser relido. Um dump vazado não vira
   acesso. O token_prefixo existe só para o admin reconhecer o link na tela.

2) usado_em e revogado_em separados, em vez de uma coluna 'status'. É o que
   permite o uso-único ser um UPDATE atômico com WHERE usado_em IS NULL: quem
   fizer rowcount==1 ganhou a corrida. Com 'status' seria SELECT-depois-UPDATE,
   que tem janela.

3) Sem FOREIGN KEY, seguindo o precedente do roteador_log: a trilha de quem
   gerou qual link precisa sobreviver à exclusão do usuário que gerou.

Idempotente e reversível.

  python migrations/qcolabore_02_cadastro_link.py             # aplica
  python migrations/qcolabore_02_cadastro_link.py --reverter  # DROPa a tabela
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

TABELA = 'cadastro_link'


def _migrate(sql):
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {sql[:180]}')


def _tabela_existe(nome=TABELA):
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (nome,), fetch=True, fetch_one=True,
    ) or {}
    return r.get('cnt', 0) > 0


def migrate_cadastro_link():
    """Cria cadastro_link (idempotente)."""
    print(f'Iniciando migração: tabela {TABELA} ...')
    # usuario_id serve aos DOIS tipos: em SENHA é o dono do link; em CADASTRO
    # nasce NULL e recebe o id da conta no momento do consumo.
    # usado_ip em VARCHAR(45): cabe um IPv6 completo.
    _migrate("""
        CREATE TABLE IF NOT EXISTS cadastro_link (
            id            BIGINT AUTO_INCREMENT PRIMARY KEY,
            tipo          ENUM('CADASTRO','SENHA') NOT NULL,
            token_hash    CHAR(64)     NOT NULL,
            token_prefixo CHAR(8)      NOT NULL,
            destinatario  VARCHAR(255) NULL,
            usuario_id    INT          NULL,
            criado_por    INT          NOT NULL,
            criado_em     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expira_em     DATETIME     NOT NULL,
            usado_em      DATETIME     NULL,
            usado_ip      VARCHAR(45)  NULL,
            revogado_em   DATETIME     NULL,
            revogado_por  INT          NULL,
            UNIQUE KEY uq_link_token (token_hash),
            KEY ix_link_pendente (tipo, usado_em, expira_em),
            KEY ix_link_usuario (usuario_id),
            KEY ix_link_criador (criado_por)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    print(f'✓ Tabela {TABELA} pronta')
    return True


def rollback_cadastro_link():
    """Remove a tabela. Recusa se houver pendência dependendo dela."""
    print(f'Revertendo: tabela {TABELA} ...')
    if not _tabela_existe():
        print(f'✓ Tabela {TABELA} já não existe')
        return True

    if _tabela_existe('cadastro_pendente'):
        n = (execute_query(
            'SELECT COUNT(*) AS cnt FROM cadastro_pendente', fetch=True,
            fetch_one=True) or {}).get('cnt', 0)
        if n:
            raise RuntimeError(
                f'{n} linha(s) em cadastro_pendente apontam para link_id daqui. '
                'Reverta a migration 03 primeiro.')

    n = (execute_query(f'SELECT COUNT(*) AS cnt FROM {TABELA}', fetch=True,
                       fetch_one=True) or {}).get('cnt', 0)
    if n:
        print(f'  ATENÇÃO: {n} link(s) serão perdidos (inclusive os já usados, '
              'que são a trilha de quem convidou quem).')
    _migrate(f'DROP TABLE {TABELA}')
    print(f'✓ Tabela {TABELA} removida')
    return True


if __name__ == '__main__':
    try:
        if '--reverter' in sys.argv:
            rollback_cadastro_link()
        else:
            migrate_cadastro_link()
        print('\n✓ Migração concluída com sucesso!')
    except Exception as e:
        print(f'\n✗ Migração falhou: {e}')
        sys.exit(1)
