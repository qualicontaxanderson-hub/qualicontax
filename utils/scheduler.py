"""Tarefas agendadas — importação automática de XMLs do Dropbox em horário configurável.

Utiliza APScheduler com BackgroundScheduler.  Para evitar que o scheduler
seja iniciado em todos os workers do gunicorn (o que causaria execuções
duplicadas), usamos um arquivo de trava (lock file) no sistema de ficheiros.
O primeiro processo que adquirir o lock será o único a inicializar o scheduler.
O horário padrão é 23:59 (BRT) e pode ser alterado pela interface administrativa.
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
_app_ref = None  # referência ao Flask app para uso em reschedule()

_DEFAULT_HOUR = 23
_DEFAULT_MINUTE = 59
_JOB_ID = 'importar_dropbox_noturno'
_CONFIG_KEY = 'scheduler_horario'


def _get_saved_time() -> tuple[int, int]:
    """Lê hora/minuto salvo em app_config. Retorna (23, 59) se não configurado."""
    try:
        from utils.db_helper import execute_query
        row = execute_query(
            "SELECT valor FROM app_config WHERE chave = %s",
            (_CONFIG_KEY,), fetch=True, fetch_one=True,
        )
        if row and row.get('valor'):
            parts = row['valor'].split(':')
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return _DEFAULT_HOUR, _DEFAULT_MINUTE


def _save_time(hour: int, minute: int) -> None:
    """Persiste hora/minuto em app_config."""
    try:
        from utils.db_helper import execute_query
        execute_query(
            "INSERT INTO app_config (chave, valor) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()",
            (_CONFIG_KEY, f'{hour:02d}:{minute:02d}'), fetch=False,
        )
    except Exception:
        logger.exception('[scheduler] Falha ao salvar horário em app_config.')


def _job_importar_todos(app):
    """Job executado no horário agendado: importa XMLs de todos os departamentos."""
    from routes.escrita_fiscal import importar_departamento_background

    hour, minute = _get_saved_time()
    logger.info('[scheduler] Iniciando importação automática (%02d:%02d).', hour, minute)
    with app.app_context():
        for dep in dropbox_sync.DEPARTAMENTOS:
            try:
                result = importar_departamento_background(dep)
                logger.info('[scheduler] %s → %s', dep, result)
            except Exception:
                logger.exception('[scheduler] Erro no departamento %r', dep)
    logger.info('[scheduler] Importação automática concluída.')


def get_scheduled_time() -> dict:
    """Retorna o horário atual agendado como {'hora': int, 'minuto': int, 'texto': 'HH:MM'}."""
    hour, minute = _get_saved_time()
    return {'hora': hour, 'minuto': minute, 'texto': f'{hour:02d}:{minute:02d}'}


def reschedule(hour: int, minute: int) -> bool:
    """Reagenda o job para uma nova hora/minuto (0–23, 0–59).

    Persiste a configuração no banco e atualiza o scheduler em memória.
    Retorna True se o scheduler estava ativo e o job foi reagendado,
    False se o scheduler não está rodando neste processo (worker secundário).
    """
    global _scheduler

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f'Hora/minuto inválidos: {hour}:{minute:02d}')

    _save_time(hour, minute)

    if _scheduler is None or not _scheduler.running:
        logger.info('[scheduler] reschedule(%02d:%02d): scheduler não ativo neste worker — apenas persistido.', hour, minute)
        return False

    app = _app_ref
    if app is None:
        logger.warning('[scheduler] reschedule: _app_ref não disponível.')
        return False

    _scheduler.reschedule_job(
        _JOB_ID,
        trigger=CronTrigger(hour=hour, minute=minute, timezone='America/Sao_Paulo'),
    )
    logger.info('[scheduler] Job reagendado para %02d:%02d BRT.', hour, minute)
    return True


def init_scheduler(app) -> bool:
    """Inicializa o scheduler, garantindo que apenas um worker o execute.

    Retorna True se o scheduler foi inicializado por este processo,
    False se outro processo já detém o lock.
    """
    global _scheduler, _lock_fd, _app_ref

    _app_ref = app

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

    hour, minute = _get_saved_time()

    _scheduler = BackgroundScheduler(daemon=True, timezone='America/Sao_Paulo')
    _scheduler.add_job(
        _job_importar_todos,
        trigger=CronTrigger(hour=hour, minute=minute, timezone='America/Sao_Paulo'),
        args=[app],
        id=_JOB_ID,
        replace_existing=True,
        misfire_grace_time=600,  # 10 minutos de tolerância para misfire
    )
    _scheduler.start()
    logger.info('[scheduler] BackgroundScheduler iniciado (job às %02d:%02d BRT).', hour, minute)
    return True
