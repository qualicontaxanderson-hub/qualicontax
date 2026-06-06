#!/usr/bin/env python3
"""
Script local de importação de XMLs — Qualicontax Fiscal.

Envia XMLs da pasta NOVO do Dropbox para o servidor Qualicontax.
O servidor salva no banco e move o arquivo para IMPORTADOS ou ERROS.
Este script NÃO move arquivos localmente.

Configuração (variáveis de ambiente ou arquivo .env na mesma pasta):
    QUALICONTAX_URL       URL base do servidor (ex: https://app.qualicontax.com.br)
    IMPORT_API_TOKEN      Token de autenticação (mesmo valor em IMPORT_API_TOKEN no servidor)
    DROPBOX_LOCAL_DIR     Pasta local sincronizada com a pasta NOVO do Dropbox
    DROPBOX_NOVO_PATH     Caminho Dropbox da pasta NOVO (ex: /Fiscal/NOVO)
    DEPARTAMENTO          Departamento (padrão: Fiscal)

Uso:
    python fiscal_importar.py
    python fiscal_importar.py --dir /caminho/para/NOVO --dry-run
    python fiscal_importar.py --url https://app.qualicontax.com.br --token SEU_TOKEN
"""

import os
import sys
import json
import argparse
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except ImportError:
    pass

try:
    import requests
except ImportError:
    print('Erro: instale a dependência requests: pip install requests', file=sys.stderr)
    sys.exit(1)

ENDPOINT = '/escrita-fiscal/importar-xml-local'


def parse_args():
    p = argparse.ArgumentParser(description='Importa XMLs para o Qualicontax via API.')
    p.add_argument('--url',          default=os.getenv('QUALICONTAX_URL', ''),
                   help='URL base do servidor')
    p.add_argument('--token',        default=os.getenv('IMPORT_API_TOKEN', ''),
                   help='Token de autenticação (X-Import-Token)')
    p.add_argument('--dir',          default=os.getenv('DROPBOX_LOCAL_DIR', ''),
                   help='Pasta local com os XMLs (pasta NOVO sincronizada pelo Dropbox)')
    p.add_argument('--dropbox-novo', default=os.getenv('DROPBOX_NOVO_PATH', '/Fiscal/NOVO'),
                   help='Caminho da pasta NOVO no Dropbox (ex: /Fiscal/NOVO)')
    p.add_argument('--departamento', default=os.getenv('DEPARTAMENTO', 'Fiscal'),
                   help='Departamento (padrão: Fiscal)')
    p.add_argument('--dry-run',      action='store_true',
                   help='Lista os arquivos sem enviar ao servidor')
    p.add_argument('--timeout',      type=int, default=30,
                   help='Timeout por requisição em segundos (padrão: 30)')
    return p.parse_args()


def main():
    args = parse_args()

    if not args.url:
        print('Erro: informe a URL do servidor via --url ou QUALICONTAX_URL.', file=sys.stderr)
        sys.exit(1)
    if not args.token and not args.dry_run:
        print('Erro: informe o token via --token ou IMPORT_API_TOKEN.', file=sys.stderr)
        sys.exit(1)
    if not args.dir:
        print('Erro: informe a pasta via --dir ou DROPBOX_LOCAL_DIR.', file=sys.stderr)
        sys.exit(1)

    local_dir = Path(args.dir).expanduser().resolve()
    if not local_dir.is_dir():
        print(f'Erro: pasta não encontrada: {local_dir}', file=sys.stderr)
        sys.exit(1)

    xmls = sorted(local_dir.glob('*.xml'))
    if not xmls:
        print(f'Nenhum arquivo XML em {local_dir}')
        sys.exit(0)

    print(f'Encontrado(s) {len(xmls)} arquivo(s) em {local_dir}')
    if args.dry_run:
        for x in xmls:
            print(f'  {x.name}')
        print('(dry-run: nenhum arquivo enviado)')
        sys.exit(0)

    endpoint_url = args.url.rstrip('/') + ENDPOINT
    dropbox_novo = args.dropbox_novo.rstrip('/')

    totais = {'ok': 0, 'dup': 0, 'ignorado': 0, 'erro': 0}

    for xml_path in xmls:
        dropbox_path = f'{dropbox_novo}/{xml_path.name}'
        print(f'→ {xml_path.name} ... ', end='', flush=True)
        try:
            with open(xml_path, 'rb') as fh:
                resp = requests.post(
                    endpoint_url,
                    headers={'X-Import-Token': args.token},
                    data={
                        'dropbox_path': dropbox_path,
                        'departamento': args.departamento,
                    },
                    files={'arquivo': (xml_path.name, fh, 'application/xml')},
                    timeout=args.timeout,
                )
            data = resp.json()
        except requests.exceptions.Timeout:
            print('TIMEOUT')
            totais['erro'] += 1
            continue
        except Exception as exc:
            print(f'ERRO DE REDE: {exc}')
            totais['erro'] += 1
            continue

        if resp.status_code >= 400 and 'erro' in data:
            print(f'ERRO ({resp.status_code}): {data["erro"]}')
            totais['erro'] += 1
            continue

        resultado = data.get('resultado', '')

        if resultado == 'ok':
            empresa = data.get('empresa', '?')
            destino = data.get('dropbox_destino', '?')
            print(f'OK — {empresa}')
            print(f'       → {destino}')
            totais['ok'] += 1

        elif resultado == 'dup':
            empresa = data.get('empresa', '?')
            print(f'DUP — {empresa} (já importada, movida para IMPORTADOS)')
            totais['dup'] += 1

        elif resultado == 'evento_erros':
            empresa = data.get('empresa', '?')
            destino = data.get('dropbox_destino', '?')
            print(f'EVENTO→ERROS — {empresa}')
            print(f'       → {destino}')
            totais['ok'] += 1

        elif resultado == 'ignorado':
            motivo = data.get('motivo', '')
            empresa_xml = data.get('empresa_xml', '')
            cnpj_xml = data.get('cnpj_xml', '')
            detalhe = empresa_xml or cnpj_xml or motivo
            print(f'IGNORADO — {detalhe or motivo}')
            if empresa_xml or cnpj_xml:
                print(f'  (empresa não cadastrada: CNPJ {cnpj_xml} / {empresa_xml})')
            totais['ignorado'] += 1

        elif resultado == 'erro':
            motivo = data.get('motivo', '')
            destino_err = data.get('dropbox_destino')
            print(f'ERRO — {motivo}')
            if destino_err:
                print(f'  (movido para ERROS: {destino_err})')
            totais['erro'] += 1

        else:
            print(f'RESPOSTA INESPERADA: {json.dumps(data)}')
            totais['erro'] += 1

    print()
    print(f'Concluído: {totais["ok"]} ok, {totais["dup"]} dup, '
          f'{totais["ignorado"]} ignorado(s), {totais["erro"]} erro(s)')

    if totais['erro']:
        sys.exit(1)


if __name__ == '__main__':
    main()
