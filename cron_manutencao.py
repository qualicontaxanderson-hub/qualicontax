# -*- coding: utf-8 -*-
"""Cron de MANUTENÇÃO do banco — serviço próprio no Railway (decisão de 13/09/2026).

O que roda, nesta ordem, a cada 10 minutos:
  0. arquivador do Q-Robô   (utils.arquivar_saidas.arquivar_pendentes, orçamento grande)
  1. confirmação por pasta  (utils.expurgo_xml.confirmar_pastas)
  2. expurgo do XML de NF-e (expurgar_nfe), eventos (expurgar_eventos) e CT-e (expurgar_cte)

Por que um serviço separado: o tick do roteador já carrega roteamento de XML,
extrato, arquivador do Q-Robô (2.000 uploads) e três painéis. Isto aqui é
trabalho de faxina, com orçamento próprio, sem disputar o tick de 5 minutos.

Variáveis (Railway → serviço manutencao):
  MANUTENCAO_ATIVO=1        liga (nasce DESLIGADO: sem ela o cron sai na 1ª linha)
  EXPURGO_DRYRUN=1          só conta e loga (padrão). Trocar para 0 para expurgar de verdade.
  MANUTENCAO_PRAZO_SEG=540  orçamento total da rodada (cabe nos 10 min)
  QROBO_ARQ_MAX=12000       notas do Q-Robô por passada do arquivador (duas por rodada)
  QROBO_ARQ_PARALELO=16     uploads simultâneos ao Dropbox
  EXPURGO_MESES=3           meses de XML no banco, pela emissão

Trava: GET_LOCK('manutencao') numa conexão dedicada — duas rodadas nunca se
atropelam. O resumo de cada rodada vai para app_config (manutencao_status),
para um card no Config mostrar o andamento.
"""
import logging
import os
import sys
import time
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s [pid=%(process)d] %(name)s: %(message)s')
logger = logging.getLogger('manutencao')

ATIVO = os.getenv('MANUTENCAO_ATIVO', '0').strip() == '1'
DRY = os.getenv('EXPURGO_DRYRUN', '1').strip() != '0'
PRAZO = max(60, int(os.getenv('MANUTENCAO_PRAZO_SEG', '540')))
LOCK = 'manutencao'


def _conectar_lock():
    import mysql.connector
    from config import Config
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT, autocommit=True, time_zone='-03:00')


def main() -> int:
    if not ATIVO:
        logger.info('[manutencao] MANUTENCAO_ATIVO != 1 — nada a fazer.')
        return 0
    logger.warning('[manutencao] >>> rodada INICIADA (dry=%s, prazo=%ss, corte=%s meses).',
                   DRY, PRAZO, os.getenv('EXPURGO_MESES', '3'))
    conn = _conectar_lock()
    cur = conn.cursor(buffered=True)
    try:
        cur.execute("SELECT GET_LOCK(%s, 0)", (LOCK,))
        if (cur.fetchone() or [0])[0] != 1:
            logger.warning('[manutencao] outra rodada em andamento; saindo.')
            return 0
        from utils import dropbox_sync
        from utils.expurgo_xml import confirmar_pastas, expurgar_nfe, expurgar_eventos, expurgar_cte
        from utils.painel_cache import guardar

        t0 = time.monotonic()
        # hora do BANCO (-03:00), nao do container (UTC): o resumo e lido ao lado de NOW().
        cur.execute("SELECT DATE_FORMAT(NOW(), '%Y-%m-%d %H:%i:%s')")
        status = {'inicio': (cur.fetchone() or [None])[0], 'dry': DRY}

        # O arquivador do Q-Robô vem PRIMEIRO e com o maior orçamento: medido em
        # 13/09/2026, no tick do roteador ele subia ~4.400 notas/hora contra uma
        # fila de 892 mil e ~28 mil/hora entrando à noite — nunca alcançaria.
        # Subir é seguro (nada é apagado), por isso ignora EXPURGO_DRYRUN.
        # Orçamento: 55% arquivador, 15% confirmação, 20% NF-e, o resto eventos/CT-e.
        from utils.arquivar_saidas import arquivar_pendentes
        for nome, fn, kw in (
            ('arquivador', arquivar_pendentes, dict(limite=int(os.getenv('QROBO_ARQ_MAX', '12000')),
                                                     dry=False, prazo_seg=int(PRAZO * 0.55))),
            ('confirmacao', confirmar_pastas, dict(svc=dropbox_sync._service, max_pastas=40,
                                                   prazo_seg=int(PRAZO * 0.15), dry=DRY)),
            ('nfe', expurgar_nfe, dict(prazo_seg=int(PRAZO * 0.20), dry=DRY)),
            ('eventos', expurgar_eventos, dict(prazo_seg=int(PRAZO * 0.06), dry=DRY)),
            ('cte', expurgar_cte, dict(prazo_seg=int(PRAZO * 0.04), dry=DRY)),
        ):
            t1 = time.monotonic()
            try:
                res = fn(**kw)
            except Exception:
                logger.exception('[manutencao] etapa %s falhou; segue para a próxima.', nome)
                res = {'erro': True}
            res['seg'] = round(time.monotonic() - t1, 1)
            status[nome] = res
            logger.warning('[manutencao] %s: %s', nome, res)
            if (time.monotonic() - t0) > PRAZO:
                logger.warning('[manutencao] orçamento esgotado; o resto fica para a próxima.')
                break
        # Segunda passada do arquivador com o que SOBROU do orçamento: na
        # madrugada de 14/09 as outras etapas levavam ~180s e o arquivador
        # parava nos seus 300s com fila de 737 mil. Sobra vai para a fila.
        sobra = PRAZO - (time.monotonic() - t0)
        if sobra > 45:
            t1 = time.monotonic()
            try:
                res = arquivar_pendentes(limite=int(os.getenv('QROBO_ARQ_MAX', '12000')),
                                         dry=False, prazo_seg=int(sobra) - 15)
            except Exception:
                logger.exception('[manutencao] arquivador (2a passada) falhou.')
                res = {'erro': True}
            res['seg'] = round(time.monotonic() - t1, 1)
            status['arquivador2'] = res
            logger.warning('[manutencao] arquivador2: %s', res)
        status['seg_total'] = round(time.monotonic() - t0, 1)
        guardar('manutencao_status', status)
        logger.warning('[manutencao] >>> rodada CONCLUÍDA em %ss.', status['seg_total'])
        return 0
    finally:
        for fn in (lambda: (cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK,)), cur.fetchall()),
                   cur.close, conn.close):
            try:
                fn()
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(main())
