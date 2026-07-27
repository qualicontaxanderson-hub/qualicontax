# -*- coding: utf-8 -*-
"""Entrypoint do CRON de captura de DFe (Opção C — Railway Cron).

Por que existe
--------------
O scheduler in-process (APScheduler dentro de um worker do gunicorn) NÃO é
confiável para o job */20 de captura de DFe: o worker dono do file-lock é
reciclado por ``--max-requests`` e pode ser suspenso pelo Railway quando ocioso,
então a thread do APScheduler para de ser escalonada (sintoma: zero linhas
origem='agendado', next_run_time congelado). O job noturno 1x/dia tolera isso por
sorte estatística; o */20 não.

A fonte do disparo passa a ser o **Railway Cron**: um serviço separado, apontando
para ESTE mesmo repo, com Start Command ``python cron_captura_dfe.py`` e um Cron
Schedule (ex.: ``*/20 * * * *``). O Railway sobe o processo, ele roda UMA rodada
e SAI. O trigger é garantido pelo Railway, não por uma thread viva.

Cold-start enxuto — de propósito
--------------------------------
Este script NÃO importa ``app`` e portanto NÃO:
  * sobe o Flask inteiro (blueprints, login manager, filtros de template);
  * roda as migrations (o serviço web já as roda no boot).
``capturar_agendado()`` e toda a cadeia que ele usa (db_helper, models, dropbox,
dfe_sefaz) são independentes de Flask — não tocam ``current_app``/``flask.g`` —,
então não é preciso um app_context. Só configuramos o logging e chamamos a função.

Guarda de segurança
-------------------
``capturar_agendado()`` aborta se ``DFE_SCHED_ATIVO != 1`` (defesa interna). Por
isso o SERVIÇO DE CRON precisa de ``DFE_SCHED_ATIVO=1`` no ambiente. No serviço
WEB, ao contrário, ``DFE_SCHED_ATIVO=0`` mantém o job de DFe in-process DESLIGADO,
evitando disparo duplo. O Cron é a única fonte.

Exit code
---------
``capturar_agendado()`` engole as próprias exceções (loga e segue), então a
rodada em si sempre retorna. Só saímos com código != 0 se o BOOT falhar
(import/logging) — aí o Railway marca a execução do Cron como "failed" de verdade.
"""
import logging
import os
import sys

# Logging idêntico ao do app.py: o Railway captura stdout; sem isto os
# logger.warning('>>> ...') da captura seriam descartados (root sem handler).
# Nível ajustável por LOG_LEVEL (default INFO). Line-buffered para os logs de
# boot não ficarem presos no buffer se o processo sair rápido.
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO),
    stream=sys.stdout,
    format='%(asctime)s %(levelname)s [pid=%(process)d] %(name)s: %(message)s',
    force=True,
)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

logger = logging.getLogger(__name__)


def main() -> int:
    logger.warning('[cron-dfe] >>> Cron de captura de DFe INICIADO (pid=%s).', os.getpid())
    try:
        from utils.integrations import dfe_captura
    except Exception:
        logger.exception('[cron-dfe] Falha ao importar dfe_captura — abortando.')
        return 1
    # capturar_agendado() trata suas próprias exceções internamente; não relança.
    dfe_captura.capturar_agendado()
    logger.warning('[cron-dfe] >>> Cron de captura de DFe CONCLUIDO (pid=%s).', os.getpid())
    return 0


if __name__ == '__main__':
    sys.exit(main())
