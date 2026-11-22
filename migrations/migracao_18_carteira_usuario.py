# -*- coding: utf-8 -*-
"""Quem pode ganhar a comissão de carteira (22/11/2026).

Anderson (20/11/2026): "Guilherme ganha 25% da carteira... eu queria que no cadastro de usuários tivéssemos como marcar quem
pode ganhar os 25% ou quem não; pode ser que o Gabriel ou o Rodrigo passem a ter". ``usuarios.carteira_pct``: NULL = não
pode; 25.00 = pode, nesse %. Ao montar um contrato de prestação, quem tem a marca escolhe se este contrato entra na
carteira dele ou não; na aprovação o acordo (fin_comissao_acordos HONORARIOS) nasce sozinho.

    python migrations/migracao_18_carteira_usuario.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402


def _tem_coluna(t, c):
    r = execute_query("SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s", (t, c), fetch=True, fetch_one=True) or {}
    return bool(r.get('n'))


def aplicar(so_ver=False):
    if _tem_coluna('usuarios', 'carteira_pct'):
        print('  usuarios.carteira_pct ja existe'); return
    print('  usuarios.carteira_pct %s' % ('criaria' if so_ver else 'criando'))
    if not so_ver:
        execute_query("ALTER TABLE usuarios ADD COLUMN carteira_pct DECIMAL(5,2) NULL COMMENT 'pode ganhar comissao da carteira de mensalidade: NULL = nao; 25.00 = sim, 25%' AFTER pix_chave", fetch=False)
        # Guilherme já recebe 25% (acordos HONORARIOS ativos): ganha a marca
        execute_query("""UPDATE usuarios u SET carteira_pct = 25.00
                          WHERE u.id IN (SELECT DISTINCT usuario_id FROM fin_comissao_acordos WHERE base = 'HONORARIOS' AND ativo = 1)""", fetch=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    aplicar(so_ver=not a.apply)
    print('ok' if a.apply else 'so olhando (use --apply)')


if __name__ == '__main__':
    main()
