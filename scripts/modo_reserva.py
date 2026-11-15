# -*- coding: utf-8 -*-
"""Modo reserva: o app inteiro no PC, em cima da cópia local do banco, SÓ PARA CONSULTA (15/11/2026).

Anderson: "E tem como montar um sistema para rodar OFF Line"; "Vamos fazer a Opção A" (consulta, travado
contra gravação, robôs/e-mails/boletos/Dropbox desligados).

Para quando a Railway ou a internet caírem: abre o app em http://127.0.0.1:5055 com os dados da última
restauração (todo dia 08:00, scripts/restaurar_copia_local.py).

Travas (uma não depende da outra):
1. BANCO: entra com o usuário ``reserva``, que só tem SELECT em ``qualicontax_copia`` — o MySQL recusa
   qualquer gravação, venha de onde vier. Nunca fala com a produção: host/porta vêm do reserva.cnf e são
   conferidos (127.0.0.1:3310).
2. SEGREDOS: todas as chaves do .env são zeradas antes de importar o app (Dropbox, chave de cifra,
   Efí...) — sem elas nada externo funciona: não manda e-mail, não emite boleto, não consulta SEFAZ.
3. ROBÔS: o agendador do app não liga; envio de e-mail devolve "modo reserva".
4. TELA: qualquer envio de formulário (POST) fora do login é recusado com aviso, e uma faixa no topo
   diz "MODO RESERVA — só consulta — dados de dd/mm hh:mm".

    python scripts/modo_reserva.py          (ou o atalho "Qualicontax RESERVA" na área de trabalho)
"""
import configparser
import json
import os
import secrets
import sys
import threading
import webbrowser

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(RAIZ)

import copia_local_mysql as M  # noqa: E402

PORTA_APP = 5055
CNF_RESERVA = os.path.join(os.path.expanduser('~'), '.qualicontax', 'reserva.cnf')


def _preparar_ambiente():
    cp = configparser.ConfigParser()
    cp.read(CNF_RESERVA)
    c = cp['client']
    if c.get('host') not in ('127.0.0.1', 'localhost') or c.get('port') != str(M.PORTA):
        raise SystemExit('RECUSADO: o modo reserva só abre a cópia do PC (127.0.0.1:%d)' % M.PORTA)
    # zera TODA chave do .env (o load_dotenv do config.py não sobrescreve o que já existe)
    try:
        for linha in open(os.path.join(RAIZ, '.env'), encoding='utf-8'):
            k = linha.split('=', 1)[0].strip()
            if k and not k.startswith('#'):
                os.environ[k] = ''
    except FileNotFoundError:
        pass
    os.environ.update({
        'DB_HOST': '127.0.0.1', 'DB_PORT': str(M.PORTA), 'DB_NAME': 'qualicontax_copia',
        'DB_USER': c.get('user'), 'DB_PASSWORD': c.get('password'), 'DB_POOL_SIZE': '5', 'DB_CONNECT_TIMEOUT': '5',
        'FLASK_ENV': 'production', 'SECRET_KEY': secrets.token_hex(32),
        'MIGRATIONS_DONE': '1',      # nada de CREATE/ALTER na cópia
        'LOGIN_CODIGO': '0',         # sem internet não chega código por e-mail
        'QC_RESERVA': '1', 'SMTP_PASSWORD': '', 'DFE_SCHED_ATIVO': '0',
    })


def _dados_de():
    try:
        return json.load(open(M.INFO, encoding='utf-8')).get('dados_de') or '?'
    except Exception:
        return '?'


def _montar_app():
    import types
    sch = types.ModuleType('utils.scheduler')       # trava 3: nenhum robô (o agendador nem é importado)
    sch.init_scheduler = lambda app: False
    sch.get_scheduled_time = lambda: {'hora': 23, 'minuto': 59, 'texto': 'desligado no modo reserva'}
    sch.reschedule = lambda *a, **k: False
    sys.modules['utils.scheduler'] = sch
    import utils.email_envio as ee
    ee.enviar = lambda *a, **k: (False, 'modo reserva: o app do PC não envia e-mail')
    from flask import request
    from app import app

    quando = _dados_de()
    faixa = ('<div style="position:sticky;top:0;z-index:99999;background:#b45309;color:#fff;font:700 13px/1.4 '
             'system-ui,sans-serif;padding:7px 14px;text-align:center">MODO RESERVA — só consulta, nada é gravado'
             ' — dados de %s (cópia do backup no PC)</div>' % quando).encode()

    @app.before_request
    def _so_consulta():
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.path not in ('/login', '/logout'):
            return ('<p style="font:15px system-ui;padding:30px">Modo reserva: só consulta. Nada pode ser '
                    'gravado aqui — quando o app normal voltar, faça a alteração lá.</p>'
                    '<p style="padding:0 30px"><a href="javascript:history.back()">Voltar</a></p>'), 403

    @app.after_request
    def _faixa(resp):
        if resp.mimetype == 'text/html' and not resp.direct_passthrough:
            corpo = resp.get_data()
            i = corpo.find(b'<body')
            if i >= 0:
                j = corpo.find(b'>', i) + 1
                resp.set_data(corpo[:j] + faixa + corpo[j:])
        return resp

    return app


def main():
    print('Qualicontax — MODO RESERVA (só consulta)')
    _preparar_ambiente()
    print('Ligando o banco da cópia...')
    liguei = M.ligar()
    if liguei and hasattr(__import__('signal'), 'SIGBREAK'):
        import signal
        # fechar a janela no X manda CTRL_CLOSE: desliga o banco antes de sair (não fica segurando memória)
        signal.signal(signal.SIGBREAK, lambda *a: (M.desligar(), os._exit(0)))
    try:
        print('Abrindo o app (dados de %s)...' % _dados_de())
        app = _montar_app()
        from werkzeug.serving import make_server
        srv = make_server('127.0.0.1', PORTA_APP, app, threaded=True)
        url = 'http://127.0.0.1:%d/login' % PORTA_APP
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print('\nPronto: %s\nPara fechar o modo reserva, feche esta janela (ou Ctrl+C).' % url)
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if liguei:
            M.desligar()


if __name__ == '__main__':
    main()
