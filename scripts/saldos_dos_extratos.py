# -*- coding: utf-8 -*-
"""Recupera o saldo das contas relendo os extratos JÁ ARQUIVADOS (21/11/2026).

Os arquivos lidos até hoje estão em EMPRESAS/{nº - razão}/FINANCEIRO/EXTRATOS/{ano}/{mês}. O leitor jogava o
saldo fora; aqui cada arquivo é relido (só leitura no Dropbox) e o saldo do banco que ele traz vai para
fin_conta_saldos — o histórico do saldo de cada conta. Nada de lançamento é tocado.

    python scripts/saldos_dos_extratos.py              # dry-run: só mostra o que gravaria
    python scripts/saldos_dos_extratos.py --apply      # grava (precisa da tabela fin_conta_saldos)
    python scripts/saldos_dos_extratos.py --ano 2026
"""
import argparse
import os
import sys

os.environ.setdefault('MIGRATIONS_DONE', '1')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--ano', type=int, default=None)
    ap.add_argument('--json', default=None, help='grava a lista em JSON (conta_id -> [[data, valor], ...])')
    a = ap.parse_args()
    from app import app
    with app.app_context():
        from datetime import date
        from utils.dropbox_sync import _service as svc, _build_empresa_folder
        from utils.extrato_formatos import ler_arquivo, ArquivoDesconhecido
        from utils.extrato_ingest import achar_conta
        from utils.conta_saldo import gravar
        from models.fin_empresa import FinEmpresa
        anos = [a.ano] if a.ano else [date.today().year - 1, date.today().year]
        achados = {}          # (conta_id, data) -> (valor, arquivo)
        for e in FinEmpresa.listar(apenas_ativas=False):
            pasta = _build_empresa_folder(e['numero_cliente'], e['nome_razao_social'])
            for ano in anos:
                base = svc._build_path('EMPRESAS', pasta, 'FINANCEIRO', 'EXTRATOS', str(ano))
                itens = svc.list_folder(base, recursive=True) or []
                for it in itens:
                    nome = it.get('name') or ''
                    if not it.get('is_file') or not nome.lower().endswith(('.ofx', '.pdf')):
                        continue
                    print('  lendo', nome, flush=True)
                    raw = svc.download_file(it['path'])
                    if not raw:
                        continue
                    try:
                        previa, formato = ler_arquivo(nome, raw, [])
                    except ArquivoDesconhecido:
                        continue
                    except Exception as exc:
                        print('  não li', nome, type(exc).__name__)
                        continue
                    s = (previa or {}).get('saldo')
                    if formato not in ('ofx', 'pdf') or not s or s.get('valor') is None:
                        continue
                    reg = achar_conta(previa.get('banco_id'), previa.get('conta'))
                    if not reg or str(s.get('data') or '').startswith('0000'):
                        continue
                    k = (reg['id'], min(str(s.get('data'))[:10], date.today().isoformat()))
                    achados[k] = (s['valor'], nome)
        por_conta = {}
        for (cid, d), (v, nome) in sorted(achados.items()):
            por_conta.setdefault(cid, []).append((d, v, nome))
        for cid, lst in por_conta.items():
            print(f'conta {cid}: {len(lst)} saldo(s); último {lst[-1][0]} = {lst[-1][1]}')
            for d, v, nome in lst:
                if a.apply:
                    gravar(cid, d, v, 'arquivo', nome)
        if a.json:
            import json
            json.dump({cid: [[d, float(v)] for d, v, _n in lst] for cid, lst in por_conta.items()}, open(a.json, 'w'))
        print(('GRAVADO' if a.apply else 'DRY-RUN'), sum(len(x) for x in por_conta.values()), 'saldo(s) em', len(por_conta), 'conta(s)')


if __name__ == '__main__':
    main()
