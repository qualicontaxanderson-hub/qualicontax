# -*- coding: utf-8 -*-
"""Liga e desliga o MySQL da cópia local (C:\\QualicontaxCopia, porta 3310) — 15/11/2026.

Não é serviço do Windows: fica DESLIGADO no dia a dia (segurava ~5 GB de memória à toa) e só liga
durante o teste de restauração (scripts/restaurar_copia_local.py) ou o modo reserva
(scripts/modo_reserva.py). Roda como processo do usuário, sem precisar de administrador.
"""
import os
import socket
import subprocess
import time

PASTA = r'C:\QualicontaxCopia'
MYSQLD = r'C:\Program Files\MySQL\MySQL Server 9.6\bin\mysqld.exe'
MYSQLADMIN = r'C:\Program Files\MySQL\MySQL Server 9.6\bin\mysqladmin.exe'
CNF = os.path.join(os.path.expanduser('~'), '.qualicontax', 'copia_local.cnf')
INFO = os.path.join(PASTA, 'copia_info.json')   # de quando são os dados da cópia (gravado pela restauração)
PORTA = 3310


def ligado():
    try:
        with socket.create_connection(('127.0.0.1', PORTA), timeout=1):
            return True
    except OSError:
        return False


def reserva_aberto():
    """O app do modo reserva (porta 5055) está no ar?"""
    try:
        with socket.create_connection(('127.0.0.1', 5055), timeout=1):
            return True
    except OSError:
        return False


def ligar(espera_s=180):
    """Liga se estiver desligado. Devolve True se FOI ESTE processo que ligou (então ele desliga no fim)."""
    if ligado():
        return False
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
    subprocess.Popen([MYSQLD, '--defaults-file=' + os.path.join(PASTA, 'my.ini')],
                     creationflags=flags, close_fds=True)
    fim = time.time() + espera_s
    while time.time() < fim:
        if ligado():
            return True
        time.sleep(1)
    raise RuntimeError('o MySQL da cópia não ligou em %d s (veja %s\\logs\\erro.log)' % (espera_s, PASTA))


def desligar(espera_s=60):
    if ligado():
        subprocess.run([MYSQLADMIN, '--defaults-extra-file=' + CNF, 'shutdown'], capture_output=True)
        fim = time.time() + espera_s
        while ligado() and time.time() < fim:
            time.sleep(1)
