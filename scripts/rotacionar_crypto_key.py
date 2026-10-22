# -*- coding: utf-8 -*-
"""Troca da DFE_CRYPTO_KEY sem parar a captura (22/10/2026, incidente da Railway).

A chave cifra a senha dos certificados (dfe_certificados.senha_cifrada) e a
cópia revelável da chave do Q-Colabore (colabore_config.token_cifrado). Trocar
só a variável deixaria tudo indecifrável; por isso a troca é em três passos:

  1. Em TODOS os serviços da Railway que têm a variável (qualicontax,
     captura-dfe, captura-nfse, roteador, Manutenção...) e no .env do PC:
         DFE_CRYPTO_KEY=<nova>,<velha>
     A primeira cifra, as duas decifram: nada quebra enquanto o banco é recifrado.
  2. python scripts/rotacionar_crypto_key.py            # dry-run: só conta
     python scripts/rotacionar_crypto_key.py --apply    # recifra com a <nova>
  3. python scripts/rotacionar_crypto_key.py --conferir  # tudo abre só com a <nova>?
     Só então DFE_CRYPTO_KEY=<nova> em todos os lugares.

    python scripts/rotacionar_crypto_key.py --gerar      # imprime uma chave nova

Nunca imprime senha nem chave do banco: só id e situação. A linha só é
regravada se ainda estiver como foi lida, para não pisar em quem trocou o
certificado no meio.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from cryptography.fernet import Fernet, InvalidToken    # noqa: E402

from utils.db_helper import execute_query               # noqa: E402
from utils.certificado_digital import _crypto_keys, _fernet   # noqa: E402

# (tabela, coluna, a coluna é binária?)
ALVOS = (('dfe_certificados', 'senha_cifrada', True),
         ('colabore_config', 'token_cifrado', False))


def _bytes(v):
    if v is None:
        return None
    return bytes(v) if isinstance(v, (bytes, bytearray)) else str(v).encode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--conferir', action='store_true',
                    help='confere que tudo abre só com a PRIMEIRA chave')
    ap.add_argument('--gerar', action='store_true')
    a = ap.parse_args()

    if a.gerar:
        print(Fernet.generate_key().decode())
        return 0

    chaves = _crypto_keys()
    nova = Fernet(chaves[0].encode())
    todas = _fernet()
    if not a.conferir and len(chaves) < 2:
        print('DFE_CRYPTO_KEY tem uma chave só. Para recifrar use "<nova>,<velha>".')
        return 2

    problemas = 0
    for tabela, coluna, binaria in ALVOS:
        rows = execute_query(f'SELECT id, {coluna} AS v FROM {tabela} '
                             f'WHERE {coluna} IS NOT NULL ORDER BY id', fetch=True) or []
        ja, mudar, quebradas = 0, [], []
        for r in rows:
            tok = _bytes(r['v'])
            try:
                nova.decrypt(tok)
                ja += 1
                continue
            except InvalidToken:
                pass
            if a.conferir:
                quebradas.append(r['id'])
                continue
            try:
                claro = todas.decrypt(tok)
            except InvalidToken:
                quebradas.append(r['id'])
                continue
            novo = todas.rotate(tok)
            if nova.decrypt(novo) != claro:          # paranoia: nunca grava o que não abre
                quebradas.append(r['id'])
                continue
            mudar.append((r, novo))

        print(f'== {tabela}.{coluna}: {len(rows)} cifradas | {ja} já na chave nova | '
              f'{len(mudar)} a recifrar | {len(quebradas)} não abrem com nenhuma chave')
        if quebradas:
            print(f'   ids que não abrem: {quebradas}')
        problemas += len(quebradas)

        if a.apply and mudar:
            feitas = 0
            for r, novo in mudar:
                valor = novo if binaria else novo.decode('ascii')
                n = execute_query(f'UPDATE {tabela} SET {coluna} = %s '
                                  f'WHERE id = %s AND {coluna} = %s',
                                  (valor, r['id'], r['v']), fetch=False)
                feitas += 1 if n else 0
            print(f'   gravadas: {feitas} de {len(mudar)}'
                  + ('' if feitas == len(mudar) else ' — rode de novo: alguém mexeu no meio'))

    if a.conferir:
        print('\nOK: tudo abre só com a chave nova. Pode tirar a velha do ambiente.'
              if not problemas else
              '\nNÃO tire a chave velha: há linhas que só ela abre (ou nenhuma).')
    elif not a.apply:
        print('\nDRY-RUN: nada gravado. Rode com --apply para recifrar.')
    return 1 if problemas else 0


if __name__ == '__main__':
    sys.exit(main())
