# -*- coding: utf-8 -*-
"""Preenche nfe_cobranca / nfe_duplicatas com as COMPRAS que entraram antes da
leitura na chegada (utils/nfe_cobranca.registrar_entrada).

Mesmo molde do scripts/preencher_pagamentos.py: um cliente e um mês por vez
(nfe_cobranca_lote), do mês mais novo para o mais antigo. O XML vem do banco
(xml_raw, ~1 mês) ou do Dropbox pela API (utils/nfe_xml_fonte.resolver).
Só nota de entrada COMPLETA (resumo da SEFAZ não tem XML para ler).

RETOMA SOZINHO: a consulta só traz nota sem linha em nfe_cobranca (toda nota
lida ganha a linha, mesmo sem cobrança). Lote com erro fica sem concluido_em e
é refeito na próxima rodada. O mês corrente nunca é dado por concluído.

    python scripts/preencher_cobranca.py --dry --cliente 17       # só lê, não grava
    python scripts/preencher_cobranca.py --cliente 17             # só o Aliança
    python scripts/preencher_cobranca.py --mes 2026-10            # só outubro
    python scripts/preencher_cobranca.py                          # tudo

Uma instância por vez (trava em %LOCALAPPDATA%\\QualicontaxCobranca).
Log no mesmo lugar: preencher.log.
"""
import argparse
import logging
import msvcrt
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')
os.environ.setdefault('MIGRATIONS_DONE', '1')
os.environ.setdefault('DB_MAX_EXEC_MS', '300000')

import config  # noqa: E402,F401  (carrega o .env antes do dropbox_sync)
from utils.db_helper import execute_many, execute_query  # noqa: E402
from utils.nfe_cobranca import SQL_COBRANCA, SQL_DUPLICATA, ler_cobranca, tuplas  # noqa: E402
from utils.nfe_xml_fonte import resolver  # noqa: E402

PAGINA = 2000
PASTA = os.path.join(os.environ.get('LOCALAPPDATA', '.'), 'QualicontaxCobranca')
log = logging.getLogger('preencher_cobranca')

SQL_LOTES = """
SELECT cliente_id, DATE_FORMAT(data_emissao, '%Y-%m') AS ano_mes, COUNT(*) AS notas
  FROM nfe_importacoes
 WHERE tipo = 'entrada' AND COALESCE(incompleta, 0) = 0
   AND cliente_id IS NOT NULL AND data_emissao IS NOT NULL
 GROUP BY cliente_id, ano_mes
"""

SQL_NOTAS = """
SELECT n.id, n.xml_caminho, n.nome_arquivo, n.chave_acesso, n.tipo,
       n.data_emissao, n.cliente_id, c.numero_cliente, c.nome_razao_social
  FROM nfe_importacoes n
  LEFT JOIN clientes c ON c.id = n.cliente_id
  LEFT JOIN nfe_cobranca k ON k.chave_acesso = n.chave_acesso
 WHERE n.cliente_id = %s AND n.tipo = 'entrada' AND COALESCE(n.incompleta, 0) = 0
   AND n.data_emissao >= %s AND n.data_emissao < %s
   AND k.chave_acesso IS NULL
"""

SQL_LOTE = """
INSERT INTO nfe_cobranca_lote
    (cliente_id, ano_mes, arquivos, com_cobranca, sem_cobranca, erros, iniciado_em, concluido_em)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE arquivos = VALUES(arquivos), com_cobranca = VALUES(com_cobranca),
    sem_cobranca = VALUES(sem_cobranca), erros = VALUES(erros),
    iniciado_em = VALUES(iniciado_em), concluido_em = VALUES(concluido_em)
"""


def _agora():
    try:
        import pytz
        return datetime.now(pytz.timezone('America/Sao_Paulo')).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def _limites(ano_mes):
    a, m = int(ano_mes[:4]), int(ano_mes[5:])
    return date(a, m, 1), date(a + (m == 12), m % 12 + 1, 1)


def _q(sql, params=None):
    for tentativa in range(3):
        r = execute_query(sql, params, fetch=True)
        if r is not None:
            return r
        time.sleep(15 * (tentativa + 1))
    raise RuntimeError('consulta falhou (ver log do banco): ' + ' '.join(sql.split())[:120])


def _um(nota):
    """(nota, cobrança) | (nota, None) se o XML não foi achado/lido."""
    xml, _, erro = resolver(nota)
    if erro:
        return nota, None
    try:
        return nota, ler_cobranca(xml)
    except Exception:
        log.warning('XML ilegível: %s', nota['chave_acesso'])
        return nota, None


def preencher_lote(cliente_id, ano_mes, threads, dry):
    ini, fim = _limites(ano_mes)
    iniciado = _agora()
    lidos = com = sem = erros = 0
    todas = sorted(_q(SQL_NOTAS, (cliente_id, ini, fim)), key=lambda r: r['id'])
    with ThreadPoolExecutor(threads) as ex:
        for i in range(0, len(todas), PAGINA):
            notas = todas[i:i + PAGINA]
            marcas = ','.join(['%s'] * len(notas))
            raw = {r['id']: r['xml_raw'] for r in _q(
                f"SELECT id, xml_raw FROM nfe_importacoes WHERE id IN ({marcas}) "
                "AND xml_raw IS NOT NULL AND xml_raw <> ''", [n['id'] for n in notas])}
            for n in notas:
                n['xml_raw'] = raw.get(n['id'])
            cabs, dups = [], []
            for nota, cob in ex.map(_um, notas):
                lidos += 1
                if cob is None:
                    erros += 1
                    continue
                cab, ds = tuplas(nota['chave_acesso'], cob)
                cabs.append(cab)
                dups += ds
                if ds:
                    com += 1
                else:
                    sem += 1
                if dry and ds:
                    log.info('  %s  %s', nota['chave_acesso'],
                             ', '.join(f"{d[3]} R$ {d[4]}" for d in ds))
            if not dry:
                # duplicatas ANTES do cabeçalho: se cair no meio, a nota fica sem
                # cabeçalho e é relida na próxima rodada (INSERT IGNORE não duplica)
                if dups and not execute_many(SQL_DUPLICATA, dups):
                    raise RuntimeError(f'falha ao gravar duplicatas do lote {cliente_id} {ano_mes}')
                if cabs and not execute_many(SQL_COBRANCA, cabs):
                    raise RuntimeError(f'falha ao gravar o lote {cliente_id} {ano_mes}')
            if dry:
                break
    corrente = ano_mes == _agora().strftime('%Y-%m')
    concluido = _agora() if not (erros or corrente or dry) else None
    if not dry:
        execute_query(SQL_LOTE, (cliente_id, ano_mes, lidos, com, sem, erros,
                                 iniciado, concluido), fetch=False)
    return lidos, com, sem, erros


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--threads', type=int, default=64)
    ap.add_argument('--mes', help='AAAA-MM: só este mês')
    ap.add_argument('--cliente', type=int, help='só este cliente_id')
    ap.add_argument('--lotes', type=int, help='parar depois de N lotes')
    ap.add_argument('--dry', action='store_true', help='lê 1 página por lote e não grava nada')
    a = ap.parse_args()

    os.makedirs(PASTA, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s',
                        handlers=[logging.FileHandler(os.path.join(PASTA, 'preencher.log'),
                                                      encoding='utf-8'),
                                  logging.StreamHandler(sys.stdout)])
    for nome in ('utils.dropbox_sync', 'utils.nfe_xml_fonte', 'dropbox', 'urllib3'):
        logging.getLogger(nome).setLevel(logging.ERROR)

    trava = open(os.path.join(PASTA, 'preencher.lock'), 'w')
    try:
        msvcrt.locking(trava.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        log.info('já existe outra rodada em andamento — saindo')
        return 0

    feitos = {(r['cliente_id'], r['ano_mes']) for r in _q(
        "SELECT cliente_id, ano_mes FROM nfe_cobranca_lote WHERE concluido_em IS NOT NULL")}
    lotes = [r for r in _q(SQL_LOTES)
             if (r['cliente_id'], r['ano_mes']) not in feitos
             and (not a.mes or r['ano_mes'] == a.mes)
             and (not a.cliente or r['cliente_id'] == a.cliente)]
    lotes.sort(key=lambda r: (r['ano_mes'], r['notas']), reverse=True)
    if a.lotes:
        lotes = lotes[:a.lotes]
    total = sum(r['notas'] for r in lotes)
    log.info('%s lotes a fazer (%s notas)%s', len(lotes), total, ' — DRY, nada grava' if a.dry else '')

    t0 = time.time()
    for i, r in enumerate(lotes, 1):
        t = time.time()
        lidos, com, sem, err = preencher_lote(r['cliente_id'], r['ano_mes'], a.threads, a.dry)
        log.info('[%s/%s] cliente %s %s: %s lidas, %s com vencimento, %s sem, %s erros (%.0fs)',
                 i, len(lotes), r['cliente_id'], r['ano_mes'], lidos, com, sem, err, time.time() - t)
    log.info('fim: %s lotes em %.1f min', len(lotes), (time.time() - t0) / 60)
    return 0


if __name__ == '__main__':
    sys.exit(main())
