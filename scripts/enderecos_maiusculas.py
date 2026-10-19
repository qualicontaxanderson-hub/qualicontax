# -*- coding: utf-8 -*-
"""Endereços já gravados em MAIÚSCULAS (19/10/2026) — o mesmo que o
EnderecoCliente grava daqui para frente. Clientes (enderecos_clientes) e o
cadastro de funcionário (cadastro_pendente).

    python scripts/enderecos_maiusculas.py            # dry-run: só mostra
    python scripts/enderecos_maiusculas.py --apply

Só troca a caixa e o espaço repetido; a linha só é regravada se ainda estiver
como o script leu (AND <coluna> = valor lido), para não pisar em quem editou
no meio.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from utils.db_helper import execute_query     # noqa: E402
from models.endereco_cliente import _mai      # noqa: E402

COLS = ('logradouro', 'numero', 'complemento', 'bairro', 'cidade')
TABELAS = ('enderecos_clientes', 'cadastro_pendente')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    total = 0
    for t in TABELAS:
        rows = execute_query(f"SELECT id, {', '.join(COLS)} FROM {t} ORDER BY id", fetch=True) or []
        muda = []
        for r in rows:
            # Vazio ('') fica como está: o pedido é a caixa, não trocar '' por NULL.
            novo = {c: _mai(r[c]) for c in COLS if (r[c] or '').strip() and _mai(r[c]) != r[c]}
            if novo:
                muda.append((r, novo))
        print(f'== {t}: {len(muda)} de {len(rows)} linhas mudariam')
        for r, novo in muda:
            print(f"  id {r['id']}: " + ' | '.join(f'{c}: {r[c]!r} -> {v!r}' for c, v in novo.items()))
        total += len(muda)
        if a.apply:
            feitas = 0
            for r, novo in muda:
                sets = ', '.join(f'{c} = %s' for c in novo)
                onde = ' AND '.join(f'{c} = %s' for c in novo)
                n = execute_query(f'UPDATE {t} SET {sets} WHERE id = %s AND {onde}',
                                  (*novo.values(), r['id'], *(r[c] for c in novo)), fetch=False)
                feitas += 1 if n else 0
            print(f'   gravadas: {feitas}')
    if not a.apply:
        print(f'\nDRY-RUN: {total} linhas, nada gravado. Rode com --apply para aplicar.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
