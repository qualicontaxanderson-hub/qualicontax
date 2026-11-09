# -*- coding: utf-8 -*-
"""Amplia prestador_complemento e tomador_complemento de nfse_capturadas para 156.

POR QUE (09/11/2026)
--------------------
O xCpl do leiaute da NFS-e nacional aceita até 156 caracteres; as colunas tinham 80.
No mutirão do backfill uma nota da empresa 343 trouxe complemento maior e a gravação
caiu com "Data too long for column 'tomador_complemento'". Como o cursor só anda depois
de gravar (regra de ouro da captura), a empresa ficaria parada nessa nota para sempre.
``nfse_tomadores.complemento`` já é 156 — fica igual.

VARCHAR(80) -> VARCHAR(156) em utf8mb4 mantém o prefixo de 2 bytes: o MySQL 8 faz no
lugar, sem copiar a tabela.

    python migrations/ampliar_nfse_complemento.py            # dry-run
    python migrations/ampliar_nfse_complemento.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query  # noqa: E402

COLUNAS = ('prestador_complemento', 'tomador_complemento')


def tamanhos():
    rows = execute_query(
        "SELECT COLUMN_NAME, CHARACTER_MAXIMUM_LENGTH AS n FROM information_schema.COLUMNS "
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'nfse_capturadas' "
        "   AND COLUMN_NAME IN ('prestador_complemento', 'tomador_complemento')", fetch=True) or []
    return {r['COLUMN_NAME']: int(r['n']) for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    atuais = tamanhos()
    faltam = [c for c in COLUNAS if atuais.get(c, 0) < 156]
    print('tamanhos atuais:', atuais, '| a ampliar:', faltam)
    if not faltam:
        print('Nada a fazer.')
        return
    if not args.apply:
        print('DRY-RUN. Repita com --apply.')
        return
    partes = ', '.join(f'MODIFY COLUMN {c} VARCHAR(156) NULL' for c in faltam)
    execute_query(f'ALTER TABLE nfse_capturadas {partes}, ALGORITHM=INPLACE, LOCK=NONE', fetch=False)
    print('depois:', tamanhos())


if __name__ == '__main__':
    main()
