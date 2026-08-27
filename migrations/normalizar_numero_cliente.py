# -*- coding: utf-8 -*-
"""Número do cliente vira só o inteiro: 007 → 7, 042 → 42 (27/08/2026).

O pedido do Anderson, ao pé da letra: "para que o cadastro fique correto
vamos padronizar de o numero ser considerado só o inteiro".

O número NÃO é só um campo — a pasta da empresa no Dropbox chama
``{numero} - {razão}``, e os XMLs/certificados arquivados guardam esse
caminho no banco. Trocar o número exige a mesma dança da tela de editar:

    1. renomear a pasta no Dropbox ANTES de gravar (Dropbox fora do ar =
       operação recusada, nunca cadastro e pasta desencontrados);
    2. gravar o número novo;
    3. reapontar os caminhos gravados nas 7 tabelas que guardam path.

Colisão é conferida antes ('7' já em uso por outro? pula e avisa).

    python migrations/normalizar_numero_cliente.py            # dry-run
    python migrations/normalizar_numero_cliente.py --apply
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402
from utils import dropbox_sync                     # noqa: E402

#: as mesmas 7 tabelas da tela de editar cliente — mudou lá, muda aqui
TABELAS_COM_CAMINHO = (
    ('nfse_capturadas', 'xml_path'),
    ('cte_documentos', 'xml_caminho'),
    ('dfe_documentos', 'xml_caminho'),
    ('dfe_eventos', 'xml_caminho'),
    ('nfe_importacoes', 'xml_caminho'),
    ('dfe_certificados', 'dropbox_path'),
    ('documentos', 'caminho_arquivo'),
)


def normalizar(numero):
    """'007' → '7'. Só mexe no que é todo dígito; o resto fica como está."""
    n = (numero or '').strip()
    return str(int(n)) if n.isdigit() else n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    tortos = execute_query(
        "SELECT id, numero_cliente, nome_razao_social FROM clientes "
        " WHERE numero_cliente REGEXP '^0+[0-9]'", fetch=True) or []

    if not tortos:
        print('Nenhum número com zero à esquerda — nada a fazer.')
        return

    print('%s cliente(s) com zero à esquerda:\n' % len(tortos))
    plano = []
    for c in tortos:
        novo = normalizar(c['numero_cliente'])
        dono = execute_query(
            'SELECT id, nome_razao_social FROM clientes '
            ' WHERE numero_cliente = %s AND id <> %s',
            (novo, c['id']), fetch=True, fetch_one=True)
        razao = c['nome_razao_social'] or ''
        pasta_antiga = dropbox_sync._build_empresa_folder(
            c['numero_cliente'], razao)
        pasta_nova = dropbox_sync._build_empresa_folder(novo, razao)
        print('  id=%-4s %r -> %r' % (c['id'], c['numero_cliente'], novo))
        print('     pasta: %s -> %s' % (pasta_antiga, pasta_nova))
        if dono:
            print('     PULADO: o numero %s ja pertence a id=%s (%s)' %
                  (novo, dono['id'], dono['nome_razao_social'][:40]))
            continue
        plano.append((c, novo, pasta_antiga, pasta_nova))

    if not args.apply:
        print('\nDry-run: nada foi feito. Rode com --apply para valer.')
        return

    print()
    for c, novo, pasta_antiga, pasta_nova in plano:
        # 1) A PASTA PRIMEIRO — Dropbox fora do ar recusa a operação inteira.
        st, de, para = dropbox_sync.renomear_pasta_empresa(pasta_antiga, pasta_nova)
        if st == 'erro':
            print('  id=%s: Dropbox indisponivel — RECUSADO, nada mudou.' % c['id'])
            continue
        if st == 'conflito':
            print('  id=%s: ja existe pasta "%s" — RECUSADO, resolva a mao.' %
                  (c['id'], pasta_nova))
            continue

        # 2) o número
        execute_query('UPDATE clientes SET numero_cliente = %s WHERE id = %s',
                      (novo, c['id']))

        # 3) os caminhos gravados (mesmo LIKE escapado da tela de editar)
        total = 0
        if st == 'movida' and de:
            like = (de.replace('\\', '\\\\')
                    .replace('%', '\\%').replace('_', '\\_')) + '/%'
            corte = len(de) + 1
            for t, col in TABELAS_COM_CAMINHO:
                n = execute_query(
                    f'UPDATE {t} SET {col} = CONCAT(%s, SUBSTRING({col}, %s)) '
                    f'WHERE {col} LIKE %s', (para, corte, like))
                total += n or 0

        print('  id=%-4s %r -> %r   pasta: %s   caminhos reapontados: %s' %
              (c['id'], c['numero_cliente'], novo, st, total))

    print('\nConferindo: sobrou zero a esquerda?')
    sobra = execute_query(
        "SELECT id, numero_cliente FROM clientes "
        " WHERE numero_cliente REGEXP '^0+[0-9]'", fetch=True) or []
    print('  %s' % (sobra if sobra else 'nenhum'))


if __name__ == '__main__':
    main()
