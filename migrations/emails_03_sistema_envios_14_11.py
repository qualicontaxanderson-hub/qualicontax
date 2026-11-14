# -*- coding: utf-8 -*-
"""Departamento "Sistema" (app@) e o registro de todo e-mail enviado (14/11/2026).

Anderson: "Pode virar por que teremos e-mails enviados por ele e outros pelo departamento e constará
usuário que enviou"; "coloque para colocar a senha aqui para ficar padrão".

- Departamento "Sistema" com o e-mail da caixa do sistema (SMTP_USER, app@qualicontax.com.br): manda o
  código de acesso e os avisos automáticos. A senha se digita na tela dele; enquanto não tiver, vale a
  do Railway (SMTP_PASSWORD).
- ``email_envios``: uma linha por e-mail que o app tentou mandar — quando, por qual caixa, para quem,
  assunto, serviço, quem clicou (ou automático) e se deu certo.

    python migrations/emails_03_sistema_envios_14_11.py            # mostra o que faria
    python migrations/emails_03_sistema_envios_14_11.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

CRIAR = """
CREATE TABLE IF NOT EXISTS email_envios (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  criado_em    DATETIME DEFAULT CURRENT_TIMESTAMP,
  remetente    VARCHAR(120) NOT NULL COMMENT 'a caixa que mandou',
  para         VARCHAR(500) NOT NULL,
  assunto      VARCHAR(255) NULL,
  servico      VARCHAR(60) NULL COMMENT 'Boleto, Contrato, Código de acesso, Aviso automático, Teste...',
  usuario_id   INT NULL COMMENT 'quem clicou; NULL = automático',
  ok           TINYINT(1) NOT NULL DEFAULT 0,
  erro         VARCHAR(400) NULL,
  INDEX idx_remetente (remetente, criado_em),
  INDEX idx_criado (criado_em)
)"""

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    email = os.getenv('SMTP_USER', 'app@qualicontax.com.br').strip().lower() or 'app@qualicontax.com.br'
    tem_tab = bool(execute_query("SHOW TABLES LIKE 'email_envios'", fetch=True))
    dep = execute_query("SELECT id, nome, email FROM departamentos WHERE nome = 'Sistema'", fetch=True, fetch_one=True)
    print('tabela email_envios:', 'já existe' if tem_tab else 'CRIAR')
    print('departamento Sistema:', f'já existe (id {dep["id"]}, {dep["email"]})' if dep else f'CRIAR com {email}')
    if not a.apply:
        print('\n(nada gravado; rode com --apply)')
        sys.exit(0)
    if not tem_tab:
        execute_query(CRIAR, fetch=False)
        print('feito: email_envios')
    if not dep:
        execute_query("INSERT INTO departamentos (nome, email, email_nome, descricao, ativo) "
                      "VALUES ('Sistema', %s, 'Qualicontax', "
                      "'Caixa do próprio app: código de acesso e avisos automáticos', 1)", (email,), fetch=False)
        print('feito: departamento Sistema')
