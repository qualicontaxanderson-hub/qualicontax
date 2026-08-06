# -*- coding: utf-8 -*-
"""
Q-COLABORE Bloco 1 / Parte 1 — migration 04: ``usuario_dados_bancarios``.

Por que tabela À PARTE
----------------------
Conta bancária e chave PIX não são "mais um campo do cadastro": são o dado mais
sensível que o Bloco 1 coleta. Numa tabela separada dá para (a) fechar o acesso
por gate de admin/Financeiro sem depender de projeção de coluna em cada SELECT
de usuário, e (b) garantir que uma listagem de funcionários nunca traga a conta
junto por descuido de um ``SELECT *``.

REGRAS DE USO (comportamento, cobrado nas Partes 3/5 — registradas aqui para
não se perderem entre as fatias):
  • leitura e escrita só com gate admin/Financeiro;
  • NUNCA logar o conteúdo destas colunas — nem em log de erro, nem em
    auditoria, nem em mensagem de exceção. Auditar o ACESSO, não o VALOR.

usuario_id e pendente_id são ambos nullable de propósito: a linha nasce colada
na candidatura (pendente_id) e migra para a conta (usuario_id) na aprovação. O
UNIQUE em usuario_id é parcial na prática — o MySQL aceita N linhas com
usuario_id NULL, que é exatamente o estado "ainda é candidatura".

Idempotente e reversível.

  python migrations/qcolabore_04_dados_bancarios.py             # aplica
  python migrations/qcolabore_04_dados_bancarios.py --reverter  # DROPa a tabela
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

TABELA = 'usuario_dados_bancarios'


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


def migrate_dados_bancarios():
    """Cria usuario_dados_bancarios (idempotente)."""
    print(f'Iniciando migração: tabela {TABELA} ...')
    # pix_chave em VARCHAR(140): a chave aleatória tem 36 chars, e-mail vai até
    # 77 pelo padrão do BACEN — 140 dá folga sem estourar índice.
    _migrate("""
        CREATE TABLE IF NOT EXISTS usuario_dados_bancarios (
            id             BIGINT AUTO_INCREMENT PRIMARY KEY,
            usuario_id     INT          NULL,
            pendente_id    BIGINT       NULL,
            banco_codigo   VARCHAR(5)   NULL,
            banco_nome     VARCHAR(120) NULL,
            agencia        VARCHAR(15)  NULL,
            conta          VARCHAR(25)  NULL,
            conta_tipo     ENUM('CORRENTE','POUPANCA','SALARIO','PAGAMENTO') NULL,
            titular_nome   VARCHAR(255) NULL,
            titular_cpf    VARCHAR(14)  NULL,
            pix_tipo       ENUM('CPF','CNPJ','EMAIL','TELEFONE','ALEATORIA') NULL,
            pix_chave      VARCHAR(140) NULL,
            criado_em      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            atualizado_em  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                        ON UPDATE CURRENT_TIMESTAMP,
            atualizado_por INT          NULL,
            UNIQUE KEY uq_bnc_usuario (usuario_id),
            KEY ix_bnc_pendente (pendente_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    print(f'✓ Tabela {TABELA} pronta')
    return True


def rollback_dados_bancarios():
    """Remove a tabela."""
    print(f'Revertendo: tabela {TABELA} ...')
    if not _tabela_existe():
        print(f'✓ Tabela {TABELA} já não existe')
        return True
    n = (execute_query(f'SELECT COUNT(*) AS cnt FROM {TABELA}', fetch=True,
                       fetch_one=True) or {}).get('cnt', 0)
    if n:
        # Só a CONTAGEM. Nunca o conteúdo — nem aqui.
        print(f'  ATENÇÃO: {n} linha(s) de dados bancários serão apagadas.')
    _migrate(f'DROP TABLE {TABELA}')
    print(f'✓ Tabela {TABELA} removida')
    return True


if __name__ == '__main__':
    try:
        if '--reverter' in sys.argv:
            rollback_dados_bancarios()
        else:
            migrate_dados_bancarios()
        print('\n✓ Migração concluída com sucesso!')
    except Exception as e:
        print(f'\n✗ Migração falhou: {e}')
        sys.exit(1)
