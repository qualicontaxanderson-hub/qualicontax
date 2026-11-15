# -*- coding: utf-8 -*-
"""Atendimento: chamados da equipe ao suporte e acesso extra por pessoa (15/11/2026).

Anderson: "temos que criar algo como HELPDESK ou TICKET que seria atendimento aos usuários,
para não ficar sem atendimento, por que estão solicitando serviços pelo grupo de whatsapp".

- ``atend_chamados``: um chamado por pedido. A pessoa vê só os dela; atender é só do admin.
  Situação: novo, andamento, usuario (aguardando usuário), suporte (aguardando suporte),
  concluido. Nada conclui sozinho ("tudo tem que ser resolvido").
- ``atend_mensagens``: a conversa. ``interna=1`` é nota do suporte que a pessoa não vê.
  O print colado vai junto (pequeno, só da conversa — não é documento de cliente).
- ``usuario_permissoes_extra``: a tela liberada SÓ para aquela pessoa, sem mexer no perfil.
  ``Usuario.get_permissoes`` soma perfil + extra.

Só cria tabela; não altera nem apaga nada.

    python migrations/atendimento_15_11.py            # mostra o que faria
    python migrations/atendimento_15_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

TABELAS = {
    'atend_chamados': """
CREATE TABLE IF NOT EXISTS atend_chamados (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  usuario_id       INT NOT NULL COMMENT 'quem pediu',
  tipo             VARCHAR(12) NOT NULL COMMENT 'alterar, erro, acesso, duvida, sugestao',
  titulo           VARCHAR(160) NOT NULL,
  situacao         VARCHAR(12) NOT NULL DEFAULT 'novo' COMMENT 'novo, andamento, usuario, suporte, concluido',
  urgente          TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'está impedindo de trabalhar',
  tela_url         VARCHAR(500) NULL COMMENT 'onde a pessoa estava ao abrir',
  tela_nome        VARCHAR(200) NULL,
  cliente_id       INT NULL COMMENT 'empresa aberta na tela, se havia',
  acesso_codigos   TEXT NULL COMMENT 'JSON: códigos do catálogo pedidos (tipo acesso)',
  acesso_rotulo    VARCHAR(200) NULL COMMENT 'Financeiro › Contas a receber — Ver',
  avaliacao        TINYINT NULL COMMENT '1 resolveu, 0 não resolveu',
  ultima_msg_de    VARCHAR(8) NULL COMMENT 'usuario ou suporte',
  ultima_msg_em    DATETIME NULL,
  visto_usuario_em DATETIME NULL,
  concluido_em     DATETIME NULL,
  concluido_por    INT NULL,
  criado_em        DATETIME DEFAULT CURRENT_TIMESTAMP,
  atualizado_em    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_usuario (usuario_id, situacao),
  KEY idx_situacao (situacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    'atend_mensagens': """
CREATE TABLE IF NOT EXISTS atend_mensagens (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  chamado_id   INT NOT NULL,
  usuario_id   INT NULL COMMENT 'NULL = o próprio sistema',
  autor        VARCHAR(8) NOT NULL COMMENT 'usuario, suporte, sistema',
  texto        TEXT NULL,
  interna      TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'nota do suporte; a pessoa não vê',
  print_tipo   VARCHAR(40) NULL,
  print_dados  MEDIUMBLOB NULL COMMENT 'print colado (até 3 MB)',
  criado_em    DATETIME DEFAULT CURRENT_TIMESTAMP,
  KEY idx_chamado (chamado_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    'usuario_permissoes_extra': """
CREATE TABLE IF NOT EXISTS usuario_permissoes_extra (
  usuario_id       INT NOT NULL,
  permissao_codigo VARCHAR(100) NOT NULL,
  concedido_por    INT NULL,
  concedido_em     DATETIME DEFAULT CURRENT_TIMESTAMP,
  chamado_id       INT NULL COMMENT 'pedido de acesso que originou',
  PRIMARY KEY (usuario_id, permissao_codigo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
}


def tabela_existe(nome):
    return bool(execute_query('SHOW TABLES LIKE %s', (nome,), fetch=True))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    faltam = [n for n in TABELAS if not tabela_existe(n)]
    for n in TABELAS:
        print(('CRIAR  ' if n in faltam else 'já existe  ') + n)
    if not a.apply:
        print('\n(nada gravado; rode com --apply)')
        sys.exit(0)
    for n in faltam:
        execute_query(TABELAS[n], fetch=False)
        print('criada', n, '->', tabela_existe(n))
    # Mesmo padrão de texto das tabelas antigas (o UNION com perfil_permissoes exige).
    for n in TABELAS:
        execute_query(f'ALTER TABLE {n} CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci', fetch=False)
