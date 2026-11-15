# -*- coding: utf-8 -*-
"""Rascunhos de Novidades a partir das publicações do sistema (git log) — 15/11/2026.

Cada publicação (commit) nova vira um RASCUNHO em Novidades › RASCUNHOS, que só o admin
vê: ele reescreve para a equipe (sem jargão) e publica, ou descarta. O texto do commit é
técnico de propósito; o rascunho é só o ponto de partida.

Roda no PC (precisa do .git; o servidor não tem). Não importa duas vezes: o commit fica
gravado (commit_hash) e o descartado também conta. Sem --desde, começa no commit mais
novo que já virou rascunho. Áreas sem efeito para a equipe (backup, scripts, testes) ficam
de fora.

    python scripts/novidades_rascunhos.py                    # mostra o que faria
    python scripts/novidades_rascunhos.py --apply
    python scripts/novidades_rascunhos.py --desde 2026-11-15 --apply
"""
import argparse
import os
import re
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

# prefixo do commit -> assunto em Novidades (None = não vira novidade)
AREAS = {
    'financeiro': 'Financeiro', 'clientes': 'Cadastros', 'clientes pf': 'Cadastros', 'grupos': 'Cadastros',
    'cadastro': 'Cadastros', 'nfse': 'Escrita Fiscal', 'escrita': 'Escrita Fiscal', 'fiscal': 'Escrita Fiscal',
    'dfe': 'Escrita Fiscal', 'captura': 'Escrita Fiscal', 'entradas': 'Escrita Fiscal', 'contabil': 'Contábil',
    'qrobo': 'Q-Robô', 'configuracoes': 'Configurações', 'relatorios': 'Relatórios', 'processos': 'Processos',
    'dp': 'DP / Legalização', 'login seguro': 'Todos', 'login': 'Todos', 'atendimento': 'Todos',
    'novidades': 'Todos', 'auditoria refeita': 'Configurações', 'auditoria': 'Configurações',
    'backup': None, 'scripts': None, 'test': None, 'testes': None, 'docs': None, 'migrations': None,
}


def commits(desde):
    fmt = '%h%x1f%ad%x1f%s%x1e'
    args = ['git', '-C', RAIZ, 'log', '--no-merges', '--date=short', f'--format={fmt}']
    if desde:
        args.append(f'--since={desde}')
    saida = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', check=True).stdout
    for bloco in saida.split('\x1e'):
        partes = bloco.strip().split('\x1f')
        if len(partes) == 3:
            yield partes[0][:12], partes[1], partes[2].lstrip('﻿')


def rascunho(assunto):
    """(area, titulo, texto) do assunto do commit; area None = fica de fora."""
    m = re.match(r'^([\w ]{2,30}?):\s*(.+)$', assunto, re.S)
    prefixo, corpo = (m.group(1).strip().lower(), m.group(2)) if m else ('', assunto)
    area = AREAS.get(prefixo, 'Todos')
    if area is None:
        return None, None, None
    corpo = re.split(r'\s+[-—]\s+Anderson \(', corpo)[0].strip()        # tira as falas do fim
    frases = re.split(r'(?<=[.!?])\s+', corpo, maxsplit=1)
    titulo = frases[0].rstrip('.')
    titulo = titulo[:1].upper() + titulo[1:]
    if len(titulo) > 150:
        titulo = titulo[:147].rsplit(' ', 1)[0] + '...'
    texto = (frases[1] if len(frases) > 1 else '').strip() or corpo
    if len(texto) > 1200:
        texto = texto[:1197].rsplit(' ', 1)[0] + '...'
    return area, titulo, texto


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--desde', help='AAAA-MM-DD; padrão: o commit mais novo que já virou rascunho')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    ja = {r['commit_hash'] for r in execute_query(
        "SELECT commit_hash FROM novidades WHERE commit_hash IS NOT NULL", fetch=True) or []}
    desde = a.desde
    if not desde:
        r = execute_query("SELECT MAX(data) d FROM novidades WHERE commit_hash IS NOT NULL",
                          fetch=True, fetch_one=True) or {}
        if not r.get('d'):
            sys.exit('Nenhum rascunho de commit ainda: informe --desde AAAA-MM-DD.')
        desde = r['d'].isoformat()
    novos = 0
    for h, data, assunto in commits(desde):
        if any(h.startswith(x) or x.startswith(h) for x in ja):
            continue
        area, titulo, texto = rascunho(assunto)
        if area is None:
            print(f'fora    {h} {data} {assunto[:70]}')
            continue
        novos += 1
        print(f'RASC.   {h} {data} [{area}] {titulo[:90]}')
        if a.apply:
            execute_query("""INSERT IGNORE INTO novidades (titulo, texto, area, data, situacao, commit_hash)
                             VALUES (%s, %s, %s, %s, 'rascunho', %s)""", (titulo, texto, area, data, h))
    print(f'\n{novos} rascunho(s)' + ('' if a.apply else ' — nada gravado; rode com --apply'))


if __name__ == '__main__':
    main()
