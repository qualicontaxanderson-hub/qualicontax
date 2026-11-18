# -*- coding: utf-8 -*-
"""Mais de uma conta para receber por usuário (18/11/2026).

Anderson: "insira um campo para cadastrar um outro pra receber! A pessoa cadastra o nome e CPF de alguém e, no
usuário, escolhe qual ficará para emissão dos pagamentos, inclusive porque alguns gostam de receber na PJ".
``usuario_dados_bancarios`` passa a ter várias linhas por usuário: a própria conta, a de outra pessoa ou a da
empresa dele (CNPJ). ``recebe_pagamentos`` = 1 marca a que sai no pedido de comissão (Pix copia e cola, QR, texto).
``titular_cpf`` cresce para caber CNPJ. A linha mais nova de cada usuário vira a escolhida, para nada mudar hoje.

    python migrations/migracao_16_contas_recebimento.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

COLUNAS = {
    'apelido': "ADD COLUMN apelido VARCHAR(60) NULL COMMENT 'ex.: Minha conta, Conta da Elizangela, Minha empresa' AFTER usuario_id",
    'recebe_pagamentos': "ADD COLUMN recebe_pagamentos TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1 = e a conta que sai no pedido de comissao' AFTER pix_chave",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    faltam = [c for c in COLUNAS if not execute_query("SHOW COLUMNS FROM usuario_dados_bancarios LIKE %s", (c,), fetch=True)]
    tipo = (execute_query("SHOW COLUMNS FROM usuario_dados_bancarios LIKE 'titular_cpf'", fetch=True) or [{}])[0].get('Type', '')
    alarga = tipo.lower() not in ('varchar(18)',)
    # a tabela nasceu com UMA conta por usuário (índice único uq_bnc_usuario); agora são várias
    idx = {r['Key_name']: r['Non_unique'] for r in execute_query("SHOW INDEX FROM usuario_dados_bancarios", fetch=True) or []}
    solta = 'uq_bnc_usuario' in idx and not idx['uq_bnc_usuario']
    print('colunas a criar:', faltam or 'nenhuma', '| titular_cpf', tipo, '→ varchar(18)' if alarga else '(ok)',
          '| índice único por usuário:', 'tirar' if solta else 'já não há')
    if not faltam and not alarga and not solta:
        return
    if not a.apply:
        print('DRY-RUN. Rode com --apply.')
        return
    partes = [COLUNAS[c] for c in faltam]
    if alarga:
        partes.append("MODIFY COLUMN titular_cpf VARCHAR(18) NULL COMMENT 'CPF ou CNPJ do titular'")
    if solta:
        partes.append('DROP INDEX uq_bnc_usuario')
        if 'idx_bnc_usuario' not in idx:
            partes.append('ADD KEY idx_bnc_usuario (usuario_id, recebe_pagamentos)')
    execute_query('ALTER TABLE usuario_dados_bancarios ' + ', '.join(partes), fetch=False)
    if 'recebe_pagamentos' in faltam:        # a linha mais nova de cada usuário é a escolhida
        execute_query("""UPDATE usuario_dados_bancarios b JOIN (SELECT usuario_id, MAX(id) mid FROM usuario_dados_bancarios
                                                                 WHERE usuario_id IS NOT NULL GROUP BY usuario_id) m
                             ON m.mid = b.id SET b.recebe_pagamentos = 1""", fetch=False)
    print('pronto.', execute_query("SELECT COUNT(*) n FROM usuario_dados_bancarios WHERE recebe_pagamentos = 1", fetch=True))


if __name__ == '__main__':
    main()
