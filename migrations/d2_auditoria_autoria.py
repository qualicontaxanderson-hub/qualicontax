# -*- coding: utf-8 -*-
"""FRENTE D2 — Auditoria e registro de atividade: colunas de AUTORIA na linha.

Aditivo e idempotente. NÃO roda no boot (rodar à mão ANTES de deployar o código
que usa as colunas — DDL antes do código):

    python migrations/d2_auditoria_autoria.py            # aplica (idempotente)
    python migrations/d2_auditoria_autoria.py --reverter # desfaz (DROP das colunas novas)

O que faz:
  1) logs_sistema: ADD modulo VARCHAR(20)  (fiscal|cadastros|contabil|dp|colabore)
  2) tabelas de cadastro: garante o par de autoria criado_por/criado_em/
     alterado_por/alterado_em. Só ADICIONA o que falta em cada tabela — as linhas
     antigas ficam NULL (não se inventa autor para o que já existe).

Todas as colunas nascem NULL/aditivas: não quebram o código atual.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query, get_last_db_error  # noqa: E402

# ---- ALVO: {tabela: [(coluna, definicao_DDL), ...]} ------------------------
# Só entram colunas que PODEM faltar; a checagem por information_schema torna
# cada uma idempotente. `criado_em` só é listado onde a tabela não tem NENHUMA
# marca de criação (grupos_clientes); contratos já tem data_criacao, as demais
# já tem criado_em — nesses casos não duplicamos o timestamp de criação.
AUTORIA = {
    'clientes':              [('alterado_por', 'INT NULL'),
                              ('alterado_em', 'TIMESTAMP NULL DEFAULT NULL')],
    'grupos_clientes':       [('criado_por', 'INT NULL'),
                              ('criado_em', 'TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP'),
                              ('alterado_por', 'INT NULL'),
                              ('alterado_em', 'TIMESTAMP NULL DEFAULT NULL')],
    'ramos_atividade':       [('criado_por', 'INT NULL'),
                              ('alterado_por', 'INT NULL'),
                              ('alterado_em', 'TIMESTAMP NULL DEFAULT NULL')],
    'contratos':             [('criado_por', 'INT NULL'),
                              ('alterado_por', 'INT NULL'),
                              ('alterado_em', 'TIMESTAMP NULL DEFAULT NULL')],
    'dfe_certificados':      [('criado_por', 'INT NULL'),
                              ('alterado_por', 'INT NULL'),
                              ('alterado_em', 'TIMESTAMP NULL DEFAULT NULL')],
    'nfe_produtos_catalogo': [('criado_por', 'INT NULL'),
                              ('alterado_por', 'INT NULL'),
                              ('alterado_em', 'TIMESTAMP NULL DEFAULT NULL')],
    'enderecos_clientes':    [('criado_por', 'INT NULL'),
                              ('alterado_por', 'INT NULL'),
                              ('alterado_em', 'TIMESTAMP NULL DEFAULT NULL')],
    'socios_clientes':       [('criado_por', 'INT NULL'),
                              ('alterado_por', 'INT NULL'),
                              ('alterado_em', 'TIMESTAMP NULL DEFAULT NULL')],
    # logs_sistema ganha o módulo do registro de atividade + o NOME e o LOGIN do
    # autor copiados no momento da ação. A auditoria NÃO pode depender da FK
    # usuario_id (ON DELETE SET NULL): apagar o usuário zera o id, mas o nome e o
    # login gravados aqui continuam sendo a verdade de quem fez.
    'logs_sistema':          [('modulo', 'VARCHAR(20) NULL'),
                              ('usuario_nome', 'VARCHAR(120) NULL'),
                              ('usuario_login', 'VARCHAR(80) NULL')],
}


def _migrate(sql):
    """DDL fatal (mesma semântica de add_procuracao_certificado)."""
    if execute_query(sql, fetch=False) is None:
        erro = get_last_db_error() or 'sem detalhe do driver (falha de conexão?)'
        raise RuntimeError(f'Migration abortada — {erro} | DDL: {sql[:180]}')


def _coluna_existe(tabela, coluna):
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (tabela, coluna), fetch=True, fetch_one=True) or {}
    return r.get('cnt', 0) > 0


def aplicar():
    print("Aplicando D2 - colunas de autoria (idempotente)...\n")
    for tabela, colunas in AUTORIA.items():
        for coluna, definicao in colunas:
            if _coluna_existe(tabela, coluna):
                print(f"  [ja existe] {tabela}.{coluna}")
                continue
            _migrate(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")
            print(f"  [+] {tabela}.{coluna} ({definicao})")
    print("\n[ok] Concluido.")


def reverter():
    print("Revertendo D2 - DROP das colunas de autoria...\n")
    for tabela, colunas in AUTORIA.items():
        for coluna, _definicao in colunas:
            if not _coluna_existe(tabela, coluna):
                print(f"  [ja nao existe] {tabela}.{coluna}")
                continue
            _migrate(f"ALTER TABLE {tabela} DROP COLUMN {coluna}")
            print(f"  [-] {tabela}.{coluna} removida")
    print("\n[ok] Revertido.")


if __name__ == '__main__':
    try:
        reverter() if '--reverter' in sys.argv else aplicar()
    except Exception as e:
        print(f"\n[x] Migracao falhou: {e}")
        sys.exit(1)
