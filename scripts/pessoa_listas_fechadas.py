# -*- coding: utf-8 -*-
"""Listas fechadas do cadastro da pessoa (19/10/2026): cria ``cadastro_opcoes``
e ``clientes_pf_dados.rg_uf`` e põe os cadastros que já existem na forma da
lista — "Brasileira" -> "Brasileiro(a)", "Empresária" -> "Empresário(a)",
"SSP/MG" -> órgão SSP + UF MG.

    python scripts/pessoa_listas_fechadas.py            # dry-run: só mostra
    python scripts/pessoa_listas_fechadas.py --apply

O DDL é o mesmo do init_db.py (idempotente). Valor que o script não sabe
traduzir fica como está: a tela o mostra "(fora da lista)" até alguém trocar.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query                    # noqa: E402
from models.pessoa_dados import OPCOES_INICIAIS, UFS, _chave  # noqa: E402

DDL_TABELA = """CREATE TABLE IF NOT EXISTS cadastro_opcoes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    tipo VARCHAR(20) NOT NULL,
    nome VARCHAR(80) NOT NULL,
    criado_por INT NULL,
    criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_cadastro_opcoes (tipo, nome)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
DDL_UF = "ALTER TABLE clientes_pf_dados ADD COLUMN rg_uf CHAR(2) NULL AFTER rg_orgao"


def tem_uf():
    r = execute_query(
        "SELECT COUNT(*) n FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'clientes_pf_dados' AND COLUMN_NAME = 'rg_uf'",
        fetch=True, fetch_one=True) or {}
    return int(r.get('n') or 0) > 0


def na_lista(tipo, valor):
    """O item da lista inicial que é o mesmo nome escrito de outro jeito."""
    if not valor:
        return None
    alvo = _chave(valor)
    return next((n for n in OPCOES_INICIAIS[tipo] if _chave(n) == alvo), None)


def orgao_e_uf(valor):
    """'SSP/MG' | 'SSP SP' | 'SSP-GO' | 'SSP' -> ('SSP', 'MG'|None)."""
    v = (valor or '').strip().upper()
    m = re.fullmatch(r'([A-Z]+)[\s/-]*([A-Z]{2})?', v)
    if not m:
        return None, None
    org, uf = m.group(1), m.group(2)
    if uf and uf not in UFS:
        return None, None
    if org not in OPCOES_INICIAIS['rg_orgao']:
        return None, None
    return org, uf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()

    print('DDL (idempotente):')
    print('  ' + DDL_TABELA.replace('\n', '\n  ') + ';')
    print('  ' + (DDL_UF + ';' if not tem_uf() else '-- rg_uf já existe'))
    print('  INSERT IGNORE INTO cadastro_opcoes: ' +
          '; '.join(f"{t} = {', '.join(n)}" for t, n in OPCOES_INICIAIS.items()))
    print()

    rows = execute_query("SELECT cliente_id, rg_orgao, nacionalidade, profissao FROM clientes_pf_dados "
                         "ORDER BY cliente_id", fetch=True) or []
    mudancas = []
    for r in rows:
        novo = {}
        nac = na_lista('nacionalidade', r['nacionalidade'])
        if nac and nac != r['nacionalidade']:
            novo['nacionalidade'] = nac
        pro = na_lista('profissao', r['profissao'])
        if pro and pro != r['profissao']:
            novo['profissao'] = pro
        org, uf = orgao_e_uf(r['rg_orgao'])
        if org and (org != r['rg_orgao'] or uf):
            novo['rg_orgao'] = org
            if uf:
                novo['rg_uf'] = uf
        if novo:
            mudancas.append((r, novo))
        fora = [c for c, v, ok in (('nacionalidade', r['nacionalidade'], nac),
                                   ('profissao', r['profissao'], pro),
                                   ('rg_orgao', r['rg_orgao'], org)) if v and not ok]
        print(f"cliente {r['cliente_id']}: órgão {r['rg_orgao']!r} | nac {r['nacionalidade']!r} | "
              f"prof {r['profissao']!r}  ->  {novo or 'nada'}" + (f"  FORA DA LISTA: {fora}" if fora else ''))
    print(f'\n{len(mudancas)} de {len(rows)} cadastros mudariam.')

    if not a.apply:
        print('\nDRY-RUN: nada gravado. Rode com --apply para aplicar.')
        return 0
    execute_query(DDL_TABELA, fetch=False)
    if not tem_uf():
        execute_query(DDL_UF, fetch=False)
    for t, nomes in OPCOES_INICIAIS.items():
        for n in nomes:
            execute_query("INSERT IGNORE INTO cadastro_opcoes (tipo, nome) VALUES (%s, %s)", (t, n), fetch=False)
    for r, novo in mudancas:
        sets = ', '.join(f'{c} = %s' for c in novo)
        execute_query(f'UPDATE clientes_pf_dados SET {sets} WHERE cliente_id = %s',
                      (*novo.values(), r['cliente_id']), fetch=False)
    print('\nAPLICADO. Conferência:')
    print(' opções:', execute_query("SELECT tipo, COUNT(*) n FROM cadastro_opcoes GROUP BY tipo", fetch=True))
    for r in execute_query("SELECT cliente_id, rg_orgao, rg_uf, nacionalidade, profissao "
                           "FROM clientes_pf_dados ORDER BY cliente_id", fetch=True) or []:
        print(' ', r)
    return 0


if __name__ == '__main__':
    sys.exit(main())
