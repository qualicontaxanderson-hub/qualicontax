"""Tarefas agendadas — importação automática de XMLs do Dropbox às 23:59.

Utiliza APScheduler com BackgroundScheduler.  Para evitar que o scheduler
seja iniciado em todos os workers do gunicorn (o que causaria execuções
duplicadas), usamos um arquivo de trava (lock file) no sistema de ficheiros.
O primeiro processo que adquirir o lock será o único a inicializar o scheduler.
"""
import fcntl
import logging
import os
import tempfile

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from utils import dropbox_sync

logger = logging.getLogger(__name__)

_LOCK_FILE = os.path.join(tempfile.gettempdir(), 'qualicontax_scheduler.lock')
_scheduler: BackgroundScheduler | None = None
_lock_fd = None  # mantém o file descriptor vivo para não liberar o lock por GC


def _job_importar_todos(app):
    """Job executado às 23:59: importa XMLs de todos os departamentos."""
    from routes.escrita_fiscal import importar_departamento_background

    logger.info('[scheduler] Iniciando importação automática (23:59).')
    with app.app_context():
        for dep in dropbox_sync.DEPARTAMENTOS:
            try:
                result = importar_departamento_background(dep)
                logger.info('[scheduler] %s → %s', dep, result)
            except Exception:
                logger.exception('[scheduler] Erro no departamento %r', dep)
    logger.info('[scheduler] Importação automática concluída.')


def init_scheduler(app) -> bool:
    """Inicializa o scheduler, garantindo que apenas um worker o execute.

    Retorna True se o scheduler foi inicializado por este processo,
    False se outro processo já detém o lock.
    """
    global _scheduler, _lock_fd

    # Tenta adquirir lock exclusivo não-bloqueante.
    # No Windows fcntl não existe; nesses ambientes (dev local) iniciamos sem lock.
    try:
        lock_fd = open(_LOCK_FILE, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd = lock_fd  # guarda referência para evitar GC liberar o lock
    except (ImportError, AttributeError):
        _lock_fd = None  # Windows — sem lock, aceita inicialização direta
    except OSError:
        logger.info('[scheduler] Outro worker já detém o lock — scheduler não iniciado aqui.')
        return False

    _scheduler = BackgroundScheduler(daemon=True, timezone='America/Sao_Paulo')
    _scheduler.add_job(
        _job_importar_todos,
        trigger=CronTrigger(hour=23, minute=59, timezone='America/Sao_Paulo'),
        args=[app],
        id='importar_dropbox_noturno',
        replace_existing=True,
        misfire_grace_time=600,  # 10 minutos de tolerância para misfire
    )
    _scheduler.start()
    logger.info('[scheduler] BackgroundScheduler iniciado (job às 23:59 BRT).')
    return True
