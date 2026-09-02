# -*- coding: utf-8 -*-
"""Entrypoint do CRON de BACKUP do banco (Railway Cron).

Mesmo molde do ``cron_captura_dfe.py``: serviço de Cron próprio no Railway,
apontando para ESTE repo, com Start Command ``python cron_backup.py`` e um
Cron Schedule só dele.

Por que serviço separado
------------------------
Backup não pode depender de nada que não seja backup. Pendurado no serviço web
ele morreria junto num deploy ruim; pendurado num cron de captura, um 656 da
SEFAZ arrastaria o backup junto. Serviço próprio, horário próprio, falha
própria — e a falha aparece na tela de Configurações, não só no log.

E por que existe
----------------
Porque até 01/09/2026 o backup morava numa tarefa agendada do PC do Anderson e
ficou quatro dias sem rodar com os computadores desligados, sem avisar ninguém.
O backup do PC continua ligado de propósito: duas cópias, em dois caminhos
independentes, é o ponto.

Guarda de segurança
-------------------
Só roda com ``BACKUP_ATIVO=1`` no ambiente do serviço de Cron. Sem a flag, sai
sem tocar em nada — é o que permite este arquivo estar no repo e no deploy
antes de o serviço existir.

Dependência de imagem
---------------------
O container precisa do ``mysqldump``, que a imagem padrão da Railway não traz:
defina ``NIXPACKS_PKGS=mysql80`` **no serviço de Cron** (só nele; o serviço web
não muda). Sem o binário o backup falha com a frase pronta em vez de traceback.

NUNCA faz purge de binlog nem apaga backup antigo — ver ``utils/backup_bd.py``.
"""
import logging
import os
import sys

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger(__name__)


def main() -> int:
    if os.getenv('BACKUP_ATIVO') != '1':
        logger.warning('[cron-backup] BACKUP_ATIVO != 1 — nada a fazer.')
        return 0

    try:
        from utils import backup_bd
    except Exception:
        logger.exception('[cron-backup] Falha ao importar o módulo — abortando.')
        return 1

    st = backup_bd.executar()

    # O código de saída importa: é por ele que a Railway marca a execução como
    # falha e mostra vermelho na lista de rodadas do serviço.
    if st.get('ok'):
        logger.info('[cron-backup] Concluído: %s (%s bytes) em %ss.',
                    st.get('destino'), st.get('bytes_gz'), st.get('duracao_s'))
        return 0

    logger.error('[cron-backup] FALHOU na etapa "%s": %s',
                 st.get('etapa'), st.get('erro'))
    return 1


if __name__ == '__main__':
    sys.exit(main())
