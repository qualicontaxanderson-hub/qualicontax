# -*- coding: utf-8 -*-
"""FASE 1 / Parte 2 — backfill de ``cte_documentos.xml_raw`` a partir da pasta
local sincronizada do Dropbox.

Por que
-------
4.704 CT-e capturados da SEFAZ não têm cópia do XML no banco: dependem do arquivo
apontado por ``xml_caminho``. Como os arquivos já foram movidos para
``EMPRESAS/{empresa}/Fiscal/CTE/{ano}/{mm}/`` e o banco não foi repontado, 4.542
desses caminhos JÁ estão quebrados — o olhinho, o DACTE e o zip em lote desses
CT-e falham hoje. Trazer o XML para o banco conserta isso e, de quebra, torna o
documento independente de qualquer mudança de pasta futura.

Resolução do arquivo (escada de 3 degraus, nesta ordem)
------------------------------------------------------
  1. o ``xml_caminho`` gravado, direto;
  2. o caminho DERIVADO do gravado (mapeamento determinístico
     ``Fiscal/IMPORTADOS/{ano}/{empresa}/{mm.aaaa}`` → ``EMPRESAS/{empresa}/Fiscal/CTE/{ano}/{mm}``);
  3. busca pelo nome do arquivo (``{chave}.xml``) em toda a árvore EMPRESAS.

Trava de integridade: em QUALQUER degrau, só grava se a ``chave_acesso`` da linha
aparecer dentro do conteúdo do arquivo. Sem isso, o degrau 3 (que casa por nome)
poderia escrever o XML errado numa linha.

Uso
---
    python scripts/backfill_cte_xml_raw.py --dry-run    # resolve e relata, NÃO escreve
    python scripts/backfill_cte_xml_raw.py --apply      # grava, em lotes commitados

SÓ preenche linha vazia (``xml_raw IS NULL OR xml_raw=''``): nunca sobrescreve o
que já existe e pode ser reexecutado sem efeito na segunda vez.
"""
import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

import mysql.connector
from config import Config

RAIZ_LOCAL = r'C:\Users\User\Dropbox'
LOTE = 100                      # linhas por transação (~800 KB, teto do servidor: 64 MB)
MAX_XML = 16_000_000            # MEDIUMTEXT — igual ao _MAX_XML_SIZE do utils/cte_import.py
CSV_SAIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'backfill_cte_xml_raw.csv')


def caminho_local(p):
    """/Aplicativos/QUALICONTAX/... -> C:\\Users\\User\\Dropbox\\Aplicativos\\..."""
    return os.path.join(RAIZ_LOCAL, p.lstrip('/').replace('/', os.sep))


def derivar(gravado):
    """Fiscal/IMPORTADOS/{ano}/{empresa}/{mm.aaaa}/{arq}
       -> EMPRESAS/{empresa}/Fiscal/CTE/{ano}/{mm}/{arq}. None se a forma não bate."""
    p = gravado.split('/')
    if len(p) < 9 or p[3] != 'Fiscal' or p[4] != 'IMPORTADOS':
        return None
    raiz, ano, empresa, mmaaaa, arq = '/'.join(p[:3]), p[5], p[6], p[7], p[-1]
    if len(mmaaaa) < 2 or not mmaaaa[:2].isdigit():
        return None
    return f'{raiz}/EMPRESAS/{empresa}/Fiscal/CTE/{ano}/{mmaaaa[:2]}/{arq}'


def indexar_empresas():
    """{nome_minusculo: [caminhos]} sob EMPRESAS — só metadados, não hidrata."""
    base = os.path.join(RAIZ_LOCAL, 'Aplicativos', 'QUALICONTAX', 'EMPRESAS')
    idx = defaultdict(list)
    for dirpath, _d, files in os.walk(base):
        for n in files:
            if n.lower().endswith('.xml'):
                idx[n.lower()].append(os.path.join(dirpath, n))
    return idx


def ler_xml(caminho):
    """(conteudo, encoding_usado). UTF-8 estrito; latin-1 só como último recurso,
    e SINALIZADO — nada de errors='replace', que corromperia o XML em silêncio."""
    with open(caminho, 'rb') as fh:
        raw = fh.read()
    try:
        return raw.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError:
        return raw.decode('latin-1'), 'latin-1'


def resolver(row, idx):
    """(caminho, degrau) do arquivo real, ou (None, None)."""
    gravado = row['xml_caminho']
    direto = caminho_local(gravado)
    if os.path.isfile(direto):
        return direto, '1-gravado'
    derivado = derivar(gravado)
    if derivado:
        d = caminho_local(derivado)
        if os.path.isfile(d):
            return d, '2-derivado'
    achados = idx.get(gravado.split('/')[-1].lower())
    if achados:
        return achados[0], '3-por-nome'
    return None, None


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true', help='resolve e relata, sem escrever')
    g.add_argument('--apply', action='store_true', help='grava xml_raw em lotes')
    args = ap.parse_args()
    escrever = bool(args.apply)

    cn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT, autocommit=False,
    )
    cur = cn.cursor(dictionary=True)
    cur.execute("""SELECT id, chave_acesso, xml_caminho, cliente_id
                     FROM cte_documentos
                    WHERE (xml_raw IS NULL OR xml_raw = '')
                      AND COALESCE(xml_caminho,'') <> ''
                    ORDER BY id""")
    linhas = cur.fetchall()
    print(f'MODO: {"APLICAR (escreve)" if escrever else "DRY-RUN (nao escreve)"}')
    print(f'linhas candidatas (xml_raw vazio + tem caminho): {len(linhas)}\n')

    print('indexando a arvore EMPRESAS...', flush=True)
    idx = indexar_empresas()
    print(f'  {sum(len(v) for v in idx.values())} XMLs, {len(idx)} nomes distintos\n')

    cont = Counter()
    trilha = []
    pendentes = []          # (id, conteudo) do lote corrente
    upd = cn.cursor()
    SQL = ("UPDATE cte_documentos SET xml_raw = %s, atualizado_em = atualizado_em "
           " WHERE id = %s AND (xml_raw IS NULL OR xml_raw = '')")

    def flush(n_lote):
        if not pendentes:
            return
        if escrever:
            upd.executemany(SQL, [(c, i) for i, c in pendentes])
            cn.commit()
            cont['gravados'] += upd.rowcount if upd.rowcount and upd.rowcount > 0 else len(pendentes)
        pendentes.clear()
        print(f'  lote {n_lote:>3} · lidos {cont["resolvidos"]:>5} · '
              f'{"gravados " + str(cont["gravados"]) if escrever else "simulados"} · '
              f'nao achados {cont["nao_achado"]}', flush=True)

    n_lote = 0
    for row in linhas:
        caminho, degrau = resolver(row, idx)
        if not caminho:
            cont['nao_achado'] += 1
            trilha.append((row['id'], row['chave_acesso'], 'NAO_ACHADO', '', '', ''))
            continue
        try:
            conteudo, enc = ler_xml(caminho)
        except OSError as exc:
            cont['erro_leitura'] += 1
            trilha.append((row['id'], row['chave_acesso'], 'ERRO_LEITURA', caminho, '', str(exc)[:60]))
            continue

        if row['chave_acesso'] and row['chave_acesso'] not in conteudo:
            cont['rejeitado_chave'] += 1
            trilha.append((row['id'], row['chave_acesso'], 'REJEITADO_CHAVE', caminho, len(conteudo), ''))
            continue
        if len(conteudo.encode('utf-8')) > MAX_XML:
            cont['grande_demais'] += 1
            trilha.append((row['id'], row['chave_acesso'], 'GRANDE_DEMAIS', caminho, len(conteudo), ''))
            continue
        if enc != 'utf-8':
            cont['encoding_latin1'] += 1

        cont['resolvidos'] += 1
        cont[degrau] += 1
        trilha.append((row['id'], row['chave_acesso'], f'OK:{degrau}', caminho, len(conteudo), enc))
        pendentes.append((row['id'], conteudo))
        if len(pendentes) >= LOTE:
            n_lote += 1
            flush(n_lote)

    n_lote += 1
    flush(n_lote)

    with open(CSV_SAIDA, 'w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh, delimiter=';')
        w.writerow(['id', 'chave', 'veredito', 'caminho_real', 'bytes', 'obs'])
        w.writerows(trilha)

    print('\n' + '=' * 72)
    print('RELATORIO — Parte 2 ' + ('(APLICADO)' if escrever else '(DRY-RUN)'))
    print('=' * 72)
    print(f'   candidatas ................. {len(linhas):>6}')
    print(f'   resolvidas (arquivo achado)  {cont["resolvidos"]:>6}')
    print(f'      degrau 1 (gravado) ...... {cont["1-gravado"]:>6}')
    print(f'      degrau 2 (derivado) ..... {cont["2-derivado"]:>6}')
    print(f'      degrau 3 (por nome) ..... {cont["3-por-nome"]:>6}')
    print(f'   NAO ACHADOS (esperado 0) ... {cont["nao_achado"]:>6}')
    print(f'   rejeitados p/ chave ........ {cont["rejeitado_chave"]:>6}')
    print(f'   grandes demais (>16MB) ..... {cont["grande_demais"]:>6}')
    print(f'   erro de leitura ............ {cont["erro_leitura"]:>6}')
    print(f'   lidos em latin-1 ........... {cont["encoding_latin1"]:>6}')
    if escrever:
        print(f'   GRAVADOS ................... {cont["gravados"]:>6}')
        cur.execute("SELECT COUNT(*) n FROM cte_documentos WHERE xml_raw IS NULL OR xml_raw=''")
        print(f'   restam sem xml_raw ......... {cur.fetchone()["n"]:>6}')
    print(f'\nCSV: {CSV_SAIDA}')

    upd.close()
    cur.close()
    cn.close()
    return 0 if cont['nao_achado'] == 0 and cont['rejeitado_chave'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
