# -*- coding: utf-8 -*-
"""Cron dos AVISOS POR E-MAIL — serviço próprio no Railway (05/10/2026).

A cada rodada, pergunta a ``utils.relatorios_email.pendentes()`` quais avisos
já deviam ter saído hoje e ainda não saíram, e manda cada um:

  07:30  cert_hoje (dias úteis), cert_semana (segunda), cert_mes (1º dia útil)
  18:30  cert_recebidos e cadastros (dias úteis)

Se o cron atrasar (deploy, Railway parado), o aviso sai na rodada seguinte do
mesmo dia — nunca duas vezes: ``email_envio.chave_dia`` é UNIQUE.

Agenda sugerida no Railway (UTC; o Brasil é UTC-3):
  */30 10-23 * * 1-5      → de 07:00 a 20:30, de segunda a sexta

Variáveis (Railway → serviço emails):
  EMAILS_ATIVO=1     liga (nasce DESLIGADO: sem ela o cron sai na 1ª linha)
  SMTP_PASSWORD=...  senha da caixa app@qualicontax.com.br (Titan)
  + as do banco, iguais às dos outros crons

Trava: GET_LOCK('emails') numa conexão dedicada — duas rodadas não se atropelam.
"""
import logging
import os
import sys

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s [pid=%(process)d] %(name)s: %(message)s')
logger = logging.getLogger('emails')

ATIVO = os.getenv('EMAILS_ATIVO', '0').strip() == '1'
LOCK = 'emails'


def _conectar_lock():
    import mysql.connector
    from config import Config
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT, autocommit=True, time_zone='-03:00')


def main() -> int:
    if not ATIVO:
        logger.info('[emails] EMAILS_ATIVO != 1 — nada a fazer.')
        return 0
    conn = _conectar_lock()
    cur = conn.cursor(buffered=True)
    try:
        cur.execute("SELECT GET_LOCK(%s, 0)", (LOCK,))
        if (cur.fetchone() or [0])[0] != 1:
            logger.warning('[emails] outra rodada em andamento; saindo.')
            return 0
        from utils import relatorios_email as R
        pend = R.pendentes()
        if not pend:
            logger.info('[emails] nada pendente agora (%s).', R.agora().strftime('%a %d/%m %H:%M'))
        for rel in pend:
            try:
                r = R.enviar(rel)
            except Exception:
                logger.exception('[emails] %s: erro inesperado; segue para o próximo.', rel)
                continue
            logger.warning('[emails] %s: %s · %s itens · para %s%s', rel, r['status'], r['itens'],
                           ', '.join(r['para']) or '—', f" · erro: {r['erro']}" if r.get('erro') else '')
        return 0
    finally:
        try:
            cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK,))
        except Exception:
            pass
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
