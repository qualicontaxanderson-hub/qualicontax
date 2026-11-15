# -*- coding: utf-8 -*-
"""Teste de restauração do backup: monta o banco inteiro no MySQL do PC (15/11/2026).

Anderson: "não podemos fazer um sistema aqui no meu PC e de vez em quando pegar o backup e subir";
"Domingo 04:00", depois "Domingo as 08:00 ... certeza que estara ligado".

Conferir o arquivo (CRC + "Dump completed on", que o cron já faz) não prova que ele VOLTA. Este script
pega o backup mais recente da nuvem (``qualicontax_nuvem_*.sql.gz`` na pasta do Dropbox), monta o banco
``qualicontax_copia`` no MySQL SEPARADO do PC (serviço QualicontaxCopia, porta 3310, só 127.0.0.1) e confere:

- o mysql terminou sem erro, o gzip fechou com o CRC certo e a última linha é "Dump completed on";
- toda tabela criada no arquivo existe na cópia, e toda tabela que tinha dados ficou com dados;
- quantas linhas cada tabela tem, comparadas com a produção: o MAIOR id (lido pelo índice, leve) e a
  estimativa do information_schema (não conta nada, não pesa).

O resultado vai para ``app_config.backup_restauracao_status`` (a única escrita na produção) e aparece no
cartão "Backup do banco" em Configurações. O log fica em C:\\QualicontaxCopia\\logs\\restauracao.log.

TRAVA: o arquivo de backup começa com DROP DATABASE. O script só fala com 127.0.0.1:3310 (lido do
~/.qualicontax/copia_local.cnf) e troca o nome do banco do arquivo (``railway``) por ``qualicontax_copia``.
Qualquer outro host/porta e ele recusa antes de abrir o arquivo.

    python scripts/restaurar_copia_local.py              # restaura o mais recente e grava o resultado
    python scripts/restaurar_copia_local.py --sem-gravar # restaura e só mostra (não escreve na produção)
"""
import argparse
import configparser
import glob
import gzip
import json
import os
import re
import subprocess
import sys
import time
import zlib
from datetime import datetime

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

PASTA_BACKUP = r'C:\Users\User\Dropbox\BANCOS\OFX\BACKUP\qualicontax'
CNF = os.path.join(os.path.expanduser('~'), '.qualicontax', 'copia_local.cnf')
MYSQL = r'C:\Program Files\MySQL\MySQL Server 9.6\bin\mysql.exe'
LOG = r'C:\QualicontaxCopia\logs\restauracao.log'
BANCO = 'qualicontax_copia'
CHAVE = 'backup_restauracao_status'
HOSTS_OK = ('127.0.0.1', 'localhost')
PORTA_OK = '3310'


def log(msg):
    linha = '[%s] %s' % (datetime.now().strftime('%d/%m/%Y %H:%M:%S'), msg)
    print(linha, flush=True)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(linha + '\n')
    except Exception:
        pass


def trava():
    """Recusa qualquer destino que não seja o MySQL do PC."""
    if not os.path.exists(CNF):
        raise SystemExit('sem %s' % CNF)
    cp = configparser.ConfigParser()
    cp.read(CNF)
    host = cp.get('client', 'host', fallback='')
    porta = cp.get('client', 'port', fallback='')
    if host not in HOSTS_OK or porta != PORTA_OK:
        raise SystemExit('RECUSADO: o destino tem que ser 127.0.0.1:3310, o cnf diz %s:%s' % (host, porta))


def mysql_local(sql):
    r = subprocess.run([MYSQL, '--defaults-extra-file=' + CNF, '-N', '-B', '-e', sql],
                       capture_output=True, text=True, encoding='utf-8')
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:400])
    return [l.split('\t') for l in r.stdout.splitlines() if l]


def escolher_arquivo():
    """O backup da nuvem mais recente que já terminou de baixar (tamanho parado há 1 min)."""
    arqs = sorted(glob.glob(os.path.join(PASTA_BACKUP, 'qualicontax_nuvem_*.sql.gz')))
    for a in reversed(arqs):
        t1 = os.path.getsize(a)
        if t1 < 100 * 1024 * 1024:
            log('pulo %s: só %d bytes' % (os.path.basename(a), t1))
            continue
        time.sleep(60)
        if os.path.getsize(a) == t1:
            return a
        log('pulo %s: o Dropbox ainda está baixando' % os.path.basename(a))
    raise RuntimeError('nenhum backup da nuvem pronto na pasta')


RX_BANCO = re.compile(rb'^(/\*!40000 DROP DATABASE IF EXISTS |CREATE DATABASE /\*!32312 IF NOT EXISTS\*/ |USE )`railway`')
RX_CRIA = re.compile(rb'^CREATE TABLE `([^`]+)`')
RX_INS = re.compile(rb'^INSERT INTO `([^`]+)`')


def restaurar(arquivo):
    """Manda o arquivo para o mysql do PC trocando o nome do banco. (criadas, com_dados, marcador_ok)"""
    mysql_local('DROP DATABASE IF EXISTS `%s`' % BANCO)
    p = subprocess.Popen([MYSQL, '--defaults-extra-file=' + CNF, '--max_allowed_packet=1G',
                          '--default-character-set=utf8mb4', '--binary-mode'],
                         stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    criadas, com_dados, ultima = [], set(), b''
    novo = ('`%s`' % BANCO).encode()
    lidos, prox = 0, 1 << 30
    try:
        with gzip.open(arquivo, 'rb') as f:
            for linha in f:
                lidos += len(linha)
                if linha.startswith(b'SET @@GLOBAL.GTID_PURGED'):
                    continue  # servidor do PC sem GTID; a linha não tem dado
                if RX_BANCO.match(linha):
                    linha = linha.replace(b'`railway`', novo, 1)
                elif linha.startswith(b'INSERT'):
                    m = RX_INS.match(linha)
                    if m:
                        com_dados.add(m.group(1).decode().lower())
                elif linha.startswith(b'CREATE TABLE'):
                    m = RX_CRIA.match(linha)
                    if m:
                        criadas.append(m.group(1).decode().lower())
                if linha.strip():
                    ultima = linha
                p.stdin.write(linha)
                if lidos >= prox:
                    log('  ... %.1f GB lidos, %d tabelas' % (lidos / 1e9, len(criadas)))
                    prox += 1 << 30
    except BrokenPipeError:
        pass  # o mysql parou no meio; o motivo sai no stderr logo abaixo
    except (zlib.error, EOFError, gzip.BadGzipFile, OSError) as e:
        p.kill()
        raise RuntimeError('arquivo corrompido ao ler (%s)' % e)
    finally:
        try:
            p.stdin.close()
        except Exception:
            pass
    err = p.stderr.read().decode('utf-8', 'replace').strip()
    rc = p.wait()
    if rc != 0:
        raise RuntimeError('o mysql parou com erro: %s' % err[:400])
    return criadas, com_dados, ultima.startswith(b'-- Dump completed on'), lidos


def conferir(criadas, com_dados):
    """Linhas de cada tabela na cópia x produção (maior id pelo índice + estimativa, sem contar nada lá)."""
    import config  # noqa: F401
    from utils.db_helper import execute_query
    local = {}
    for nome, n in mysql_local("SELECT table_name, 0 FROM information_schema.tables WHERE table_schema='%s'" % BANCO):
        local[nome.lower()] = None
    faltam = [t for t in criadas if t not in local]
    linhas, vazias, tabs = 0, [], []
    est = {r['t'].lower(): int(r['n'] or 0) for r in execute_query(
        "SELECT table_name t, table_rows n FROM information_schema.tables WHERE table_schema = DATABASE()",
        fetch=True) or []}
    com_id = {r['t'].lower() for r in execute_query(
        "SELECT table_name t FROM information_schema.columns WHERE table_schema = DATABASE() "
        "AND column_name = 'id' AND column_key = 'PRI'", fetch=True) or []}
    for t in sorted(local):
        n = int(mysql_local('SELECT COUNT(*) FROM `%s`.`%s`' % (BANCO, t))[0][0])
        linhas += n
        if t in com_dados and n == 0:
            vazias.append(t)
        item = {'t': t, 'copia': n, 'prod_estimado': est.get(t)}
        if t in com_id:
            item['max_id_copia'] = int((mysql_local('SELECT COALESCE(MAX(id),0) FROM `%s`.`%s`' % (BANCO, t)) or [[0]])[0][0])
            r = execute_query('SELECT COALESCE(MAX(id),0) m FROM `%s`' % t, fetch=True, fetch_one=True) or {}
            item['max_id_prod'] = int(r.get('m') or 0)
        tabs.append(item)
    return {'tabelas': len(local), 'tabelas_no_arquivo': len(criadas), 'faltam': faltam,
            'vazias': vazias, 'linhas': linhas, 'detalhe': tabs}


def gravar(payload):
    import config  # noqa: F401
    from utils.db_helper import execute_query
    execute_query("INSERT INTO app_config (chave, valor) VALUES (%s, %s) "
                  "ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()",
                  (CHAVE, json.dumps(payload, ensure_ascii=False)), fetch=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sem-gravar', action='store_true')
    a = ap.parse_args()
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    trava()
    ini = time.time()
    st = {'ok': False, 'quando': datetime.now().strftime('%d/%m/%Y %H:%M'), 'maquina': os.getenv('COMPUTERNAME', '')}
    try:
        arq = escolher_arquivo()
        st['arquivo'] = os.path.basename(arq)
        st['bytes_gz'] = os.path.getsize(arq)
        log('INICIO restauração de %s (%.2f GB)' % (st['arquivo'], st['bytes_gz'] / 1e9))
        criadas, com_dados, marcador, lidos = restaurar(arq)
        st['bytes_crus'] = lidos
        log('montado em %d min; conferindo' % ((time.time() - ini) / 60))
        c = conferir(criadas, com_dados)
        grandes = sorted(c['detalhe'], key=lambda x: -x['copia'])[:8]
        st.update({k: c[k] for k in ('tabelas', 'tabelas_no_arquivo', 'faltam', 'vazias', 'linhas')})
        st['maiores'] = [{'t': x['t'], 'n': x['copia']} for x in grandes]
        problemas = []
        if not marcador:
            problemas.append('o arquivo não termina com "Dump completed on"')
        if c['faltam']:
            problemas.append('%d tabela(s) não voltaram: %s' % (len(c['faltam']), ', '.join(c['faltam'][:5])))
        if c['vazias']:
            problemas.append('%d tabela(s) voltaram vazias: %s' % (len(c['vazias']), ', '.join(c['vazias'][:5])))
        st['ok'] = not problemas
        st['erro'] = '; '.join(problemas)
        for x in c['detalhe']:
            if x.get('max_id_prod') is not None:
                log('  %-40s cópia %10d  maior id cópia %10d  produção %10d' %
                    (x['t'], x['copia'], x['max_id_copia'], x['max_id_prod']))
            else:
                log('  %-40s cópia %10d' % (x['t'], x['copia']))
    except Exception as e:
        st['erro'] = str(e)[:400]
    st['duracao_s'] = int(time.time() - ini)
    log('%s em %d min: %s' % ('OK' if st['ok'] else 'FALHOU', st['duracao_s'] / 60,
                               st.get('erro') or '%s tabelas, %s linhas' % (st.get('tabelas'), st.get('linhas'))))
    if not a.sem_gravar:
        try:
            gravar(st)
        except Exception as e:
            log('não gravei o resultado na produção: %s' % e)
    return 0 if st['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
