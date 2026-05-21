"""Gerenciador leve de jobs de importação assíncrona em memória.

Cada job representa uma execução de importação Dropbox disparada pelo
usuário via modal. O job roda em uma daemon thread separada dos workers
do gunicorn, de modo que a importação não bloqueie requisições de outros
usuários.

Uso:
    job_id, state = create_job()
    threading.Thread(target=minha_funcao, args=(state, ...), daemon=True).start()
    ...
    job = get_job(job_id)   # leitura de progresso
    request_stop(job_id)    # solicita parada antecipada
"""
import threading
import time
import uuid

_jobs: dict = {}
_lock = threading.Lock()
_JOB_TTL = 3600  # segundos — jobs finalizados são removidos após 1 hora


def create_job() -> tuple[str, dict]:
    """Cria um job novo, registra no store e retorna (job_id, state dict)."""
    job_id = uuid.uuid4().hex[:16]
    state: dict = {
        'status': 'running',        # running | done | stopped | error
        'ok': 0,
        'dup': 0,
        'err': 0,
        'skipped': 0,
        'moved_ok': 0,
        'moved_err': 0,
        'msg': 'Iniciando...',
        'unregistered_companies': [],
        'imported_companies': [],
        'details': [],
        'stop_requested': False,
        'created_at': time.time(),
    }
    with _lock:
        _jobs[job_id] = state
        _cleanup_old_jobs()
    return job_id, state


def get_job(job_id: str) -> dict | None:
    """Retorna uma cópia snapshot do estado do job, ou None se não encontrado."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        # Retorna cópia rasa — listas são compartilhadas mas só a thread de
        # background as modifica, então a leitura pelo HTTP thread é segura.
        return dict(job)


def request_stop(job_id: str) -> bool:
    """Solicita parada do job. Retorna True se o job existia e estava rodando."""
    with _lock:
        job = _jobs.get(job_id)
        if job and job['status'] == 'running':
            job['stop_requested'] = True
            return True
        return False


def _cleanup_old_jobs() -> None:
    """Remove jobs terminados com mais de _JOB_TTL s. Deve ser chamado com _lock."""
    now = time.time()
    to_delete = [
        k for k, v in _jobs.items()
        if v['status'] != 'running' and now - v['created_at'] > _JOB_TTL
    ]
    for k in to_delete:
        del _jobs[k]
