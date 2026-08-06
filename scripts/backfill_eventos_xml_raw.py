# -*- coding: utf-8 -*-
"""FASE 1 / Parte 3 — backfill de ``dfe_eventos.xml_raw`` a partir da pasta local
sincronizada do Dropbox.

Mais simples que o backfill de CT-e: a Fase 0 provou que os 232 caminhos de
``dfe_eventos.xml_caminho`` RESOLVEM (232/232) — os eventos já nascem em
``EMPRESAS/{empresa}/Fiscal/{ano}/{mm}`` via ``pasta_fiscal()``, e nunca passaram
pela mudança de pasta que quebrou os CT-e. Então é leitura direta do caminho
gravado, sem escada de degraus.

A trava de integridade continua: só grava se a ``chave_evento`` da linha aparecer
dentro do conteúdo do arquivo.

Uso
---
    python scripts/backfill_eventos_xml_raw.py --dry-run
    python scripts/backfill_eventos_xml_raw.py --apply

SÓ preenche linha vazia (``xml_raw IS NULL OR xml_raw=''``) — reexecutável.
"""
import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

import mysql.connector
from config import Config

RAIZ_LOCAL = r'C:\Users\User\Dropbox'
LOTE = 100
MAX_XML = 16_000_000            # MEDIUMTEXT
CSV_SAIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'backfill_eventos_xml_raw.csv')


def caminho_local(p):
    return os.path.join(RAIZ_LOCAL, p.lstrip('/').replace('/', os.sep))


def indexar_empresas():
    """Rede de segurança, igual à do backfill de CT-e: {nome: [caminhos]}."""
    base = os.path.join(RAIZ_LOCAL, 'Aplicativos', 'QUALICONTAX', 'EMPRESAS')
    idx = defaultdict(list)
    for dirpath, _d, files in os.walk(base):
        for n in files:
            if n.lower().endswith('.xml'):
                idx[n.lower()].append(os.path.join(dirpath, n))
    return idx


def ler_xml(caminho):
    with open(caminho, 'rb') as fh:
        raw = fh.read()
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        return raw.decode('latin-1'), 'latin-1'


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    escrever = bool(args.apply)

    cn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT, autocommit=False,
    )
    cur = cn.cursor(dictionary=True)
    cur.execute("""SELECT id, chave_evento, ch_nfe, xml_caminho
                     FROM dfe_eventos
                    WHERE (xml_raw IS NULL OR xml_raw = '')
                      AND COALESCE(xml_caminho,'') <> ''
                    ORDER BY id""")
    linhas = cur.fetchall()
    print(f'MODO: {"APLICAR (escreve)" if escrever else "DRY-RUN (nao escreve)"}')
    print(f'eventos candidatos (xml_raw vazio + tem caminho): {len(linhas)}\n')

    idx = None      # índice só é montado se algum caminho falhar
    cont = Counter()
    trilha, pendentes = [], []
    upd = cn.cursor()
    SQL = ("UPDATE dfe_eventos SET xml_raw = %s, atualizado_em = atualizado_em "
           " WHERE id = %s AND (xml_raw IS NULL OR xml_raw = '')")

    def flush(n_lote):
        if not pendentes:
            return
        if escrever:
            upd.executemany(SQL, [(c, i) for i, c in pendentes])
            cn.commit()
            cont['gravados'] += len(pendentes)
        pendentes.clear()
        print(f'  lote {n_lote:>3} · resolvidos {cont["resolvidos"]:>4} · '
              f'{"gravados " + str(cont["gravados"]) if escrever else "simulados"} · '
              f'nao achados {cont["nao_achado"]}', flush=True)

    n_lote = 0
    for row in linhas:
        caminho = caminho_local(row['xml_caminho'])
        degrau = '1-gravado'
        if not os.path.isfile(caminho):
            if idx is None:
                print('  (algum caminho falhou — indexando EMPRESAS como rede de seguranca)')
                idx = indexar_empresas()
            achados = idx.get(row['xml_caminho'].split('/')[-1].lower())
            if not achados:
                cont['nao_achado'] += 1
                trilha.append((row['id'], row['chave_evento'], 'NAO_ACHADO', '', ''))
                continue
            caminho, degrau = achados[0], '2-por-nome'
        try:
            conteudo, enc = ler_xml(caminho)
        except OSError as exc:
            cont['erro_leitura'] += 1
            trilha.append((row['id'], row['chave_evento'], 'ERRO_LEITURA', caminho, str(exc)[:60]))
            continue

        # A chave_evento é o Id do procEvento — tem que estar dentro do XML.
        if row['chave_evento'] and row['chave_evento'] not in conteudo:
            cont['rejeitado_chave'] += 1
            trilha.append((row['id'], row['chave_evento'], 'REJEITADO_CHAVE', caminho, len(conteudo)))
            continue
        if len(conteudo.encode('utf-8')) > MAX_XML:
            cont['grande_demais'] += 1
            trilha.append((row['id'], row['chave_evento'], 'GRANDE_DEMAIS', caminho, len(conteudo)))
            continue
        if enc != 'utf-8':
            cont['encoding_latin1'] += 1

        cont['resolvidos'] += 1
        cont[degrau] += 1
        trilha.append((row['id'], row['chave_evento'], f'OK:{degrau}', caminho, len(conteudo)))
        pendentes.append((row['id'], conteudo))
        if len(pendentes) >= LOTE:
            n_lote += 1
            flush(n_lote)

    n_lote += 1
    flush(n_lote)

    with open(CSV_SAIDA, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(['id', 'chave_evento', 'veredito', 'caminho_real', 'bytes'])
        w.writerows(trilha)

    print('\n' + '=' * 68)
    print('RELATORIO — Parte 3 ' + ('(APLICADO)' if escrever else '(DRY-RUN)'))
    print('=' * 68)
    print(f'   candidatos ................. {len(linhas):>5}')
    print(f'   resolvidos ................. {cont["resolvidos"]:>5}')
    print(f'      caminho gravado ......... {cont["1-gravado"]:>5}')
    print(f'      rede de seguranca ....... {cont["2-por-nome"]:>5}')
    print(f'   NAO ACHADOS (esperado 0) ... {cont["nao_achado"]:>5}')
    print(f'   rejeitados p/ chave ........ {cont["rejeitado_chave"]:>5}')
    print(f'   grandes demais (>16MB) ..... {cont["grande_demais"]:>5}')
    print(f'   erro de leitura ............ {cont["erro_leitura"]:>5}')
    print(f'   lidos em latin-1 ........... {cont["encoding_latin1"]:>5}')
    if escrever:
        print(f'   GRAVADOS ................... {cont["gravados"]:>5}')
        cur.execute("SELECT COUNT(*) n FROM dfe_eventos WHERE xml_raw IS NULL OR xml_raw=''")
        print(f'   restam sem xml_raw ......... {cur.fetchone()["n"]:>5}')
    print(f'\nCSV: {CSV_SAIDA}')

    upd.close()
    cur.close()
    cn.close()
    return 0 if cont['nao_achado'] == 0 and cont['rejeitado_chave'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
