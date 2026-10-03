# -*- coding: utf-8 -*-
"""``colabore_config.token_cifrado`` — a chave do Q-Colabore, CIFRADA, para revelar.

POR QUE
-------
Até 02/10/2026 a chave do agente ia ao banco só como SHA-256: ninguém, nem o
sistema, conseguia mostrá-la de novo. Funcionário que trabalha em 2-3 máquinas
precisava da MESMA chave em todas, e a única saída era gerar outra — o que
invalida a atual e obriga a trocar em todas as máquinas.

Decisão do Anderson (02/10/2026): guardar também a chave cifrada com Fernet
(a mesma ``DFE_CRYPTO_KEY`` das senhas de certificado), para o ADMIN poder
revelá-la ("Ver chave atual"). O hash continua sendo o que autentica.

Chaves geradas ANTES desta coluna ficam com NULL: não há como recuperá-las.

    python migrations/add_colabore_token_cifrado.py            # dry-run
    python migrations/add_colabore_token_cifrado.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

SQLS = [
    'ALTER TABLE colabore_config ADD COLUMN token_cifrado VARCHAR(512) NULL, ALGORITHM=INSTANT',
    'ALTER TABLE colabore_config ADD COLUMN token_cifrado VARCHAR(512) NULL, ALGORITHM=INPLACE, LOCK=NONE',
]


def existe():
    r = execute_query(
        'SELECT COUNT(*) n FROM INFORMATION_SCHEMA.COLUMNS '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s',
        ('colabore_config', 'token_cifrado'), fetch=True, fetch_one=True) or {}
    return int(r.get('n') or 0) > 0


def main():
    ap = argparse.ArgumentParser(description='token_cifrado em colabore_config.')
    ap.add_argument('--apply', action='store_true', help='executa; sem isto é só dry-run')
    args = ap.parse_args()
    if existe():
        print('colabore_config.token_cifrado já existe — nada a fazer.')
        return 0
    if not args.apply:
        print('SQL QUE SERIA EXECUTADO (o segundo só se o primeiro for recusado):')
        for s in SQLS:
            print('   ' + s + ';')
        print('\nROLLBACK, se precisar:\n   ALTER TABLE colabore_config DROP COLUMN token_cifrado;')
        print('\nDRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        return 0
    for s in SQLS:
        print('EXECUTANDO: ' + s)
        if execute_query(s, fetch=False) is not None:
            break
        print('   recusado; tentando a forma seguinte.')
    ok = existe()
    print('OK.' if ok else 'FALHOU: a coluna não existe.')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
