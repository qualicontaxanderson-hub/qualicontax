# SEM --max-requests, e a ausencia e deliberada (12/09/2026).
#
# Ele reciclava o worker a cada ~1.000 requisicoes, e app.py roda as MIGRATIONS
# no import — uma vez por PROCESSO, porque nao usamos --preload. Com o Q-Robo
# mandando 433 notas/min, cada worker batia as mil em 3 a 9 minutos e pagava a
# rodada inteira de migrations antes de atender qualquer coisa: medido, picos
# isolados de 28 e 40 SEGUNDOS num arquivo estatico, com o resto das
# requisicoes em 0,4s. O --max-requests existe para conter vazamento de
# memoria, e a memoria deste app e plana em 200 MB: nao comprava nada.
#
# E NAO usar --preload tambem e deliberado: seria o jeito de as migrations
# rodarem uma vez so, mas com --preload o APScheduler sobe no mestre e as
# threads dele NAO sobrevivem ao fork — a importacao automatica noturna nunca
# mais rodaria. Ver o comentario em app.py:283 e utils/scheduler.py.
web: gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --timeout 60 --graceful-timeout 20 --worker-class gthread --workers 4 --threads 8
