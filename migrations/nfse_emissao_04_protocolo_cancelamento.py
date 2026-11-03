# -*- coding: utf-8 -*-
"""Emissor de NFS-e nacional — protocolo do cancelamento com 62 posições (02/11/2026).

O Id do evento que o Sefin devolve é ``EVT`` + chave (50) + tipo do evento (6) + sequência (3)
= 62 caracteres. A coluna ``nfse_emitidas.cancelamento_protocolo`` nasceu com VARCHAR(60) e o
MySQL cortou os dois últimos dígitos (a sequência "001" virou "0") no primeiro cancelamento de
prova (nota 99-13, id 21, homologação). Alarga para 80 e regrava, a partir do ``raw_resposta``
guardado, o protocolo das notas canceladas que ficaram cortadas.

    python migrations/nfse_emissao_04_protocolo_cancelamento.py            # dry-run: só lista
    python migrations/nfse_emissao_04_protocolo_cancelamento.py --apply    # altera e corrige
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

TAMANHO = 80


def tipo_coluna():
    r = execute_query(
        "SELECT COLUMN_TYPE t FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'nfse_emitidas' AND COLUMN_NAME = 'cancelamento_protocolo'",
        fetch=True, fetch_one=True)
    return (r or {}).get('t') or ''


def protocolo_do_raw(raw):
    """Id do infEvento guardado na resposta bruta do Sefin (eventoXmlGZipB64)."""
    try:
        j = json.loads(raw) if isinstance(raw, str) and raw.startswith('{') else {}
        if not j.get('eventoXmlGZipB64'):
            return ''
        from utils.integrations.nfse_adn.emissao import desempacotar
        from utils.integrations.nfse_adn.eventos import _id_do_evento
        return _id_do_evento(desempacotar(j['eventoXmlGZipB64']))
    except Exception as exc:                        # noqa: BLE001
        print('   (não li o raw_resposta:', exc.__class__.__name__, ')')
        return ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem ele só lista')
    args = ap.parse_args()

    atual = tipo_coluna()
    precisa_ddl = atual.lower() != f'varchar({TAMANHO})'
    print(f'cancelamento_protocolo: {atual or "(coluna não existe)"} ->', f'varchar({TAMANHO})' if precisa_ddl else 'já está certo')

    cortadas = execute_query(
        "SELECT id, serie, numero_dps, cancelamento_protocolo, raw_resposta FROM nfse_emitidas "
        "WHERE situacao = 'cancelada' AND cancelamento_protocolo IS NOT NULL", fetch=True) or []
    corrigir = []
    for n in cortadas:
        certo = protocolo_do_raw(n.get('raw_resposta'))
        if certo and certo != n['cancelamento_protocolo']:
            corrigir.append((n['id'], certo))
            print(f"   nota {n['serie']}-{n['numero_dps']} (id {n['id']}): {n['cancelamento_protocolo']} -> {certo}")
    print(f'{len(cortadas)} nota(s) cancelada(s), {len(corrigir)} com protocolo a regravar')

    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0
    if precisa_ddl:
        if execute_query(f'ALTER TABLE nfse_emitidas MODIFY cancelamento_protocolo VARCHAR({TAMANHO}) NULL') is None:
            print('FALHOU: coluna não alterada.')
            return 1
        print('OK: coluna alargada para', tipo_coluna())
    for nid, certo in corrigir:
        if execute_query('UPDATE nfse_emitidas SET cancelamento_protocolo = %s WHERE id = %s', (certo, nid)) is None:
            print(f'FALHOU: nota {nid} não regravada.')
            return 1
    print(f'OK: {len(corrigir)} protocolo(s) regravado(s).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
