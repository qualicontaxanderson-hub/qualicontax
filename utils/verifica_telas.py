# -*- coding: utf-8 -*-
"""Abre TODA tela de leitura do sistema e diz qual estoura.

Por que existe
--------------
Em 25/08/2026 eu troquei uma classe e cortei o arquivo dela ate o fim, sem
ver que duas outras classes vinham depois. Foi para producao assim: a tela de
Contas deu 500 e o cron do extrato importaria quebrado. O Anderson achou pela
tela; nenhum teste meu tinha chance de achar, porque eu so testava o que
estava mexendo.

Este script abre todas as rotas GET sem parametro e reporta o codigo de cada
uma. Sai com 1 se alguma estourar. Roda em segundos e teria pego aquele erro
antes do push.

    python utils/verifica_telas.py            # so as que quebram
    python utils/verifica_telas.py --tudo     # todas, com o codigo

Usa o test_client com uma sessao montada na mao: e a mesma pilha do Flask,
sem precisar de servidor de pe nem de senha.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Rotas GET que MUDAM estado ou disparam trabalho pesado — nao sao telas, e
#: abrir por varredura seria disparar coisa em producao.
PULAR = {
    'auth.logout',
    'financeiro.extrato_exportar',
}
PREFIXOS_PULADOS = ('/static', '/health')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tudo', action='store_true', help='lista todas, nao so as quebradas')
    args = ap.parse_args()

    from app import app                                    # noqa: E402
    from utils.db_helper import execute_query              # noqa: E402

    u = execute_query("SELECT id, nome FROM usuarios WHERE tipo_usuario = 'ADMIN' "
                      " ORDER BY id LIMIT 1", fetch=True, fetch_one=True)
    if not u:
        print('Nenhum usuario ADMIN — nao da para abrir tela nenhuma.')
        return 1

    app.config['WTF_CSRF_ENABLED'] = False
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['_user_id'] = str(u['id'])
        sess['_fresh'] = True

    alvos = []
    for regra in app.url_map.iter_rules():
        if 'GET' not in regra.methods:
            continue
        if regra.arguments:                 # precisa de id: nao da para adivinhar
            continue
        if regra.endpoint in PULAR:
            continue
        if str(regra).startswith(PREFIXOS_PULADOS):
            continue
        alvos.append((regra.endpoint, str(regra)))

    alvos.sort(key=lambda x: x[1])
    quebradas, ok, redirecionadas = [], [], []

    for endpoint, caminho in alvos:
        try:
            r = c.get(caminho)
            cod = r.status_code
        except Exception as e:              # noqa: BLE001 — o objetivo e achar isto
            quebradas.append((caminho, endpoint, 'EXCECAO: %s' % str(e)[:90]))
            continue
        if cod >= 500:
            # a mensagem util esta no traceback do log, mas o corpo ja ajuda
            corpo = r.get_data(as_text=True)
            pista = ''
            for linha in reversed(corpo.splitlines()):
                if 'Error' in linha or 'error' in linha:
                    pista = linha.strip()[:90]
                    break
            quebradas.append((caminho, endpoint, '%s %s' % (cod, pista)))
        elif cod in (301, 302):
            redirecionadas.append((caminho, endpoint, cod))
        else:
            ok.append((caminho, endpoint, cod))

    print('Telas abertas: %s' % len(alvos))
    print('  ok:            %s' % len(ok))
    print('  redirecionam:  %s' % len(redirecionadas))
    print('  QUEBRADAS:     %s' % len(quebradas))

    if quebradas:
        print('\nQUEBRADAS:')
        for caminho, endpoint, msg in quebradas:
            print('  %-52s %s' % (caminho, endpoint))
            print('      %s' % msg)

    if args.tudo:
        print('\nOK:')
        for caminho, endpoint, cod in ok:
            print('  %-52s %s' % (caminho, cod))
        print('\nREDIRECIONAM (login, filtro obrigatorio, etc):')
        for caminho, endpoint, cod in redirecionadas:
            print('  %-52s %s' % (caminho, cod))

    return 1 if quebradas else 0


if __name__ == '__main__':
    sys.exit(main())
