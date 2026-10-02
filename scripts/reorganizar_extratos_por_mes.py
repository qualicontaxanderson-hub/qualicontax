# -*- coding: utf-8 -*-
"""Reorganiza os extratos já arquivados de EXTRATOS/{ano} para EXTRATOS/{ano}/{mês}.

POR QUE
-------
Até 02/10/2026 o robô guardava todo extrato solto na pasta do ano — uma pilha
de 20 arquivos onde não se achava nada. O padrão novo (o mesmo do Fiscal) é
ano/mês, e o extrato que cobre vários meses vai COPIADO em cada um
(``utils.extrato_ingest.arquivar_extrato``). Este script põe o que já existia
no padrão novo.

COMO DESCOBRE OS MESES DE CADA ARQUIVO
--------------------------------------
1. pelo período no NOME ("Jan-Ago.2026", "Set.2026", "Dez.2025-Fev.2026"),
   que o próprio robô escreveu ao arquivar;
2. sem período no nome, abre o arquivo e lê as datas dos lançamentos;
3. não conseguiu nenhum dos dois: o arquivo FICA onde está e sai no relatório.

O nome do arquivo não muda. Só arquivo solto na pasta do ano é tocado; o que
já está numa pasta de mês fica como está.

    python scripts/reorganizar_extratos_por_mes.py            # simulação
    python scripts/reorganizar_extratos_por_mes.py --apply
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                      # noqa: E402,F401  (carrega o .env antes do Dropbox)
from utils.db_helper import execute_query          # noqa: E402

_MES = {'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
        'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12}
_M = r'(Jan|Fev|Mar|Abr|Mai|Jun|Jul|Ago|Set|Out|Nov|Dez)'
_RE_DOIS_ANOS = re.compile(_M + r'\.(\d{4})-' + _M + r'\.(\d{4})', re.I)
_RE_MESMO_ANO = re.compile(_M + r'(?:-' + _M + r')?\.(\d{4})', re.I)


def datas_do_nome(nome):
    """Datas-sentinela (dia 1 do 1º e do último mês) a partir do período no nome."""
    m = _RE_DOIS_ANOS.search(nome)
    if m:
        a = (int(m.group(2)), _MES[m.group(1).lower()])
        b = (int(m.group(4)), _MES[m.group(3).lower()])
    else:
        m = _RE_MESMO_ANO.search(nome)
        if not m:
            return []
        ano = int(m.group(3))
        a = (ano, _MES[m.group(1).lower()])
        b = (ano, _MES[(m.group(2) or m.group(1)).lower()])
    return [f'{a[0]:04d}-{a[1]:02d}-01', f'{b[0]:04d}-{b[1]:02d}-01']


def datas_do_conteudo(svc, caminho, nome):
    from cron_extrato import _ler_previa
    dados = svc.download_file(caminho)
    if not dados:
        return []
    try:
        previa, formato = _ler_previa(nome, dados)
    except Exception:                               # noqa: BLE001
        return []
    datas = []
    if formato in ('ofx', 'csv', 'pdf') and previa:
        datas = [l['data'] for l in previa.get('lancamentos') or [] if l.get('data')]
    if not datas and nome.lower().endswith('.ofx'):
        # OFX sem lançamento (conta parada no período) ainda declara o período
        # no cabeçalho: <DTSTART> e <DTEND>.
        txt = dados.decode('latin-1', 'replace')
        datas = [f'{d[:4]}-{d[4:6]}-{d[6:8]}'
                 for d in re.findall(r'<DT(?:START|END)>(\d{8})', txt)]
    return datas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem isto é só simulação')
    args = ap.parse_args()

    from utils.dropbox_sync import _service as svc, _build_empresa_folder
    from utils.extrato_ingest import arquivar_extrato, meses_do_periodo

    clientes = execute_query(
        "SELECT DISTINCT c.id, c.numero_cliente, c.nome_razao_social FROM clientes c "
        " WHERE c.id IN (SELECT cliente_id FROM fin_empresas WHERE cliente_id IS NOT NULL) "
        "    OR c.id IN (SELECT DISTINCT empresa_id FROM extrato_lancamentos)",
        fetch=True) or []

    tot = {'arquivos': 0, 'copias': 0, 'ficaram': 0, 'falhas': 0}
    for c in clientes:
        base = svc._build_path('EMPRESAS', _build_empresa_folder(c['numero_cliente'], c['nome_razao_social']),
                               'FINANCEIRO', 'EXTRATOS')
        try:
            anos = [e for e in svc.list_folder(base) if not e['is_file'] and re.fullmatch(r'\d{4}', e['name'])]
        except Exception:                           # noqa: BLE001
            continue                                # empresa sem pasta de extratos
        print(f"\n== {c['numero_cliente']} - {c['nome_razao_social'].strip()}")
        for ano in sorted(anos, key=lambda e: e['name']):
            pasta_ano = f"{base}/{ano['name']}"
            soltos = [e for e in svc.list_folder(pasta_ano) if e['is_file']]
            for arq in sorted(soltos, key=lambda e: e['name']):
                nome = arq['name']
                caminho = f'{pasta_ano}/{nome}'
                datas = datas_do_nome(nome)
                fonte = 'nome'
                if not datas:
                    datas, fonte = datas_do_conteudo(svc, caminho, nome), 'conteúdo'
                meses = meses_do_periodo(datas)
                if not meses:
                    tot['ficaram'] += 1
                    print(f'   FICA (não sei o período): {nome}')
                    continue
                rotulo = ', '.join(f'{a}/{m:02d}' for a, m in meses)
                tot['arquivos'] += 1
                tot['copias'] += len(meses)
                if not args.apply:
                    print(f'   {nome}  ->  {rotulo}   [período pelo {fonte}]')
                    continue
                r = arquivar_extrato(svc, caminho, c['numero_cliente'], c['nome_razao_social'],
                                     datas, nome, mover=True)
                tot['falhas'] += len(r['falhas'])
                print(f"   {'OK ' if r['ok'] and not r['falhas'] else 'ERRO'} {nome}  ->  {rotulo}"
                      + (f"   falhas: {len(r['falhas'])}" if r['falhas'] else ''))

    print(f"\n{tot['arquivos']} arquivo(s) para {tot['copias']} pasta(s) de mês; "
          f"{tot['ficaram']} ficam na pasta do ano"
          + (f"; {tot['falhas']} falha(s)" if args.apply else '') + '.')
    if not args.apply:
        print('SIMULAÇÃO. Nada foi movido. Repita com --apply para executar.')
    return 1 if tot['falhas'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
