# -*- coding: utf-8 -*-
"""Documentos de identificação da pessoa (19/10/2026): cria
``clientes_pf_documentos`` e copia o RG que estava em clientes_pf_dados
(rg, rg_orgao, rg_uf) como o documento do CONTRATO.

Quando o órgão é um conselho (CRBM, CRC, OAB...), o número é o registro no
conselho, não RG — vira documento daquele tipo. Sem número não vira nada.
As colunas antigas ficam como estão (histórico); a tela não as usa mais.

    python scripts/pessoa_documentos.py            # dry-run: só mostra
    python scripts/pessoa_documentos.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from utils.db_helper import execute_query                    # noqa: E402
from models.pessoa_dados import TIPOS_DOC, ORGAO_SUGERIDO    # noqa: E402

DDL = """CREATE TABLE IF NOT EXISTS clientes_pf_documentos (
    id INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id INT NOT NULL,
    tipo VARCHAR(12) NOT NULL,
    numero VARCHAR(30) NOT NULL,
    orgao VARCHAR(20) NULL,
    uf CHAR(2) NULL,
    contrato TINYINT(1) NOT NULL DEFAULT 0,
    alterado_por INT NULL,
    alterado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_pf_docs_cliente (cliente_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""

CONSELHOS = {t for t, _ in TIPOS_DOC if ORGAO_SUGERIDO.get(t) == t}


def existe_tabela():
    r = execute_query("SELECT COUNT(*) n FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                      "AND TABLE_NAME = 'clientes_pf_documentos'", fetch=True, fetch_one=True) or {}
    return int(r.get('n') or 0) > 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('DDL:\n  ' + DDL.replace('\n', '\n  ') + ';')
    print('  (tabela já existe)' if existe_tabela() else '  (tabela ainda não existe)')
    rows = execute_query("SELECT cliente_id, rg, rg_orgao, rg_uf FROM clientes_pf_dados "
                         "WHERE COALESCE(rg, '') <> '' OR COALESCE(rg_orgao, '') <> '' ORDER BY cliente_id",
                         fetch=True) or []
    ja = set()
    if existe_tabela():
        ja = {r['cliente_id'] for r in execute_query(
            "SELECT DISTINCT cliente_id FROM clientes_pf_documentos", fetch=True) or []}
    novos = []
    for r in rows:
        numero = ' '.join((r['rg'] or '').split()).upper()
        if r['cliente_id'] in ja:
            print(f"cliente {r['cliente_id']}: já tem documentos — não mexe")
            continue
        if not numero:
            print(f"cliente {r['cliente_id']}: órgão {r['rg_orgao']}/{r['rg_uf']} SEM número — não vira documento")
            continue
        tipo = r['rg_orgao'] if r['rg_orgao'] in CONSELHOS else 'RG'
        d = {'cliente_id': r['cliente_id'], 'tipo': tipo, 'numero': numero,
             'orgao': r['rg_orgao'], 'uf': r['rg_uf']}
        novos.append(d)
        print(f"cliente {r['cliente_id']}: {tipo} {numero} · {r['rg_orgao'] or '-'}/{r['rg_uf'] or '-'} (contrato)")
    print(f'\n{len(novos)} documentos a criar.')
    if not a.apply:
        print('DRY-RUN: nada gravado. Rode com --apply para aplicar.')
        return 0
    execute_query(DDL, fetch=False)
    for d in novos:
        execute_query("INSERT INTO clientes_pf_documentos (cliente_id, tipo, numero, orgao, uf, contrato) "
                      "VALUES (%s, %s, %s, %s, %s, 1)",
                      (d['cliente_id'], d['tipo'], d['numero'], d['orgao'], d['uf']), fetch=False)
    print('APLICADO. Conferência:')
    for r in execute_query("SELECT cliente_id, tipo, numero, orgao, uf, contrato FROM clientes_pf_documentos "
                           "ORDER BY cliente_id", fetch=True) or []:
        print(' ', r)
    return 0


if __name__ == '__main__':
    sys.exit(main())
