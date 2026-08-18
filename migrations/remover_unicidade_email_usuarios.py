# -*- coding: utf-8 -*-
"""Remove a unicidade de usuarios.email — o identificador do sistema é o LOGIN.

DECISÃO DO ANDERSON (18/08/2026): "muitos usam o mesmo e-mail... vamos permitir
e-mails duplicados e o nosso acesso sempre será com LOGIN". O caso concreto:
candidatura do Rodrigo recusada porque legalizacao@qualicontax.com.br já
pertencia a outro usuário — e-mail de SETOR, compartilhado de propósito.

POR QUE É SEGURO
----------------
Mapeado antes de mexer (18/08/2026):
  * o login autentica por LOGIN (Usuario.get_by_login) — e-mail não loga;
  * a (re)definição de senha é por ID do usuário, link individual gerado pelo
    admin — não existe "esqueci a senha" por e-mail que ficaria ambíguo;
  * get_by_email existe no model e NINGUÉM chama.
O que continua único: login (idx_login) e cpf — identidade de verdade.

O ÍNDICE SOME, JUNTO COM TRÊS CHECAGENS NO CÓDIGO
--------------------------------------------------
Este script cuida do banco. As checagens de aplicação (aprovação de
candidatura, criar usuário, editar usuário) saem no mesmo commit — sem elas o
ALTER sozinho não muda o comportamento da tela.

    python migrations/remover_unicidade_email_usuarios.py            # dry-run
    python migrations/remover_unicidade_email_usuarios.py --apply

ROLLBACK: recriar o índice só funciona se não houver duplicados —
    ALTER TABLE usuarios ADD UNIQUE KEY email (email);
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402


def _indice_existe():
    r = execute_query(
        "SELECT COUNT(*) n FROM INFORMATION_SCHEMA.STATISTICS "
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usuarios' "
        "   AND INDEX_NAME = 'email' AND NON_UNIQUE = 0",
        fetch=True, fetch_one=True)
    return bool(r and r['n'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true', help='executa; sem isto é dry-run')
    args = ap.parse_args()

    print('índices únicos de usuarios hoje:')
    for r in execute_query("SHOW INDEX FROM usuarios WHERE Non_unique=0", fetch=True) or []:
        print(f"   {r['Key_name']}: {r['Column_name']}")
    print()

    if not _indice_existe():
        print("O índice único 'email' já não existe. Nada a fazer.")
        return 0

    sql = 'ALTER TABLE usuarios DROP INDEX email'
    print('A EXECUTAR:', sql)
    if not args.apply:
        print()
        print('DRY-RUN. Nada foi alterado. Repita com --apply.')
        return 0

    if execute_query(sql, fetch=False) is None:
        print('FALHOU. Nada foi alterado.')
        return 1
    if _indice_existe():
        print('FALHOU: o índice ainda está lá.')
        return 1
    print('OK: usuarios.email deixou de ser único. Login e CPF continuam únicos.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
