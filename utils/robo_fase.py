# -*- coding: utf-8 -*-
"""Em que fase está cada robô, e se AGORA ele pode mandar (12/09/2026).

O PROBLEMA QUE ISTO RESOLVE
---------------------------
São 14 robôs instalados e **200 a instalar**, e cada instalação puxa desde
01/01/2026. O volume real, medido em 12/09/2026 e muito acima do que eu
estimei: os 14 robôs somam **706 mil notas**, média de **50 mil por empresa**,
com a Westpark em 157 mil e a Pavão Castelinho em 191 mil (posto emite NFC-e
por bomba). Só neste dia entraram **503 mil**, a ~1.250 por minuto. Tudo isso
chegando no meio do dia é o que travou o site em 11/09.

O desenho é do Anderson, e é melhor que qualquer coisa só de servidor:

1. **Instalou?** manda umas 100 e PARA — já provou que o robô funciona, e isso
   pode ser de dia, leva minutos.
2. **O retroativo desce à NOITE**, das 22:00 às 05:00, quando ninguém está
   usando o sistema.
3. **Quando alcançar o dia de hoje**, libera e passa a mandar em tempo real.

POR QUE ISTO NÃO PRECISA DE BUILD NOVO DO .EXE
----------------------------------------------
O agente já pergunta ao servidor no começo de cada ciclo
(``GET /api/saidas/config``) e obedece:

    if not body.get('ativo', False):
        log.info('robô desligado na nuvem (ativo=false) — nada enviado')
        → aborta o ciclo

Então basta o ``ativo`` da resposta ser calculado com mais critério. E o que
NÃO se pode fazer é recusar nota por nota: o ``else`` do ``runner.py`` é
retryable e **continua o laço**, então um "fora_da_janela" por nota faria o
agente despejar as milhares e levar recusa em cada uma. A decisão tem de ser
no ``/config``, antes de ele varrer.

NENHUMA COLUNA NOVA
-------------------
A fase se deduz do que já está no banco:

* quantas notas daquele robô já chegaram  -> distingue TESTE do resto;
* a maior ``data_emissao`` já recebida    -> distingue RETROATIVO de TEMPO REAL.

Guardar a fase numa coluna criaria um dado que envelhece: bastaria alguém
apagar notas para a fase ficar mentindo. Deduzir custa duas contagens por
ciclo do agente — e o agente chama isso uma vez por ciclo, não por nota.
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_TZ = ZoneInfo('America/Sao_Paulo')

#: Quantas notas provam que a instalação funcionou. Passado isso, o retroativo
#: deixa o horário comercial em paz.
COTA_TESTE = max(1, int(os.getenv('QROBO_COTA_TESTE', '100')))

#: A janela do retroativo. ATRAVESSA a meia-noite de propósito (22 -> 5), e é
#: por isso que o teste de "estou dentro" é OR e não AND.
JANELA_INICIO = int(os.getenv('QROBO_JANELA_INICIO', '22'))
JANELA_FIM = int(os.getenv('QROBO_JANELA_FIM', '5'))

#: Quão recente a última nota precisa ser para o robô ser considerado "em dia".
#: Sete dias, e não um: empresa que emite uma vez por semana estaria em dia, e
#: tratá-la como retroativo a prenderia na janela da noite para sempre.
DIAS_EM_DIA = max(1, int(os.getenv('QROBO_DIAS_EM_DIA', '7')))


def dentro_da_janela(agora=None):
    """Estamos na janela noturna? (22:00–05:00 por padrão)."""
    h = (agora or datetime.now(_TZ)).hour
    if JANELA_INICIO == JANELA_FIM:
        return True                       # janela de 24h: sempre liberado
    if JANELA_INICIO > JANELA_FIM:        # atravessa a meia-noite
        return h >= JANELA_INICIO or h < JANELA_FIM
    return JANELA_INICIO <= h < JANELA_FIM


def proxima_abertura(agora=None):
    """Quando a janela abre de novo — para a tela dizer "volta às 22:00"."""
    ag = agora or datetime.now(_TZ)
    alvo = ag.replace(hour=JANELA_INICIO, minute=0, second=0, microsecond=0)
    if alvo <= ag:
        alvo += timedelta(days=1)
    return alvo


def situacao(cliente_id, ativo_cadastro=True, agora=None):
    """A fase do robô e se ele pode mandar AGORA.

    Devolve dict com ``fase``, ``liberado``, ``motivo`` (frase pronta para a
    tela) e os números que embasaram a decisão — porque indicador sem o dado
    que o gerou é indicador que ninguém confere.
    """
    from utils.db_helper import execute_query

    ag = agora or datetime.now(_TZ)

    # DUAS consultas, e não um COUNT(*) com MAX junto — a diferença é grande na
    # escala real. A Westpark tem 157 mil notas e a Pavão Castelinho 191 mil:
    # contar tudo levava 0,7s POR CICLO DE CADA ROBÔ, mesmo com o índice, porque
    # percorrer 157 mil entradas custa. E eu não preciso do total: só preciso
    # saber se JÁ PASSOU de 100. O LIMIT para na centésima linha.
    #
    # O MAX sai de graça: com o índice (cliente_id, origem, data_emissao) o
    # MySQL vai direto na última entrada, sem percorrer nada.
    lim = execute_query(
        """SELECT COUNT(*) AS n FROM (
               SELECT 1 FROM nfe_importacoes
                WHERE cliente_id = %s AND origem = 'Q-ROBO'
                LIMIT %s) x""",
        (cliente_id, COTA_TESTE), fetch=True, fetch_one=True) or {}
    # O nome carrega o limite de proposito: expor isto como 'recebidas'
    # faria o painel dizer "100 notas" para quem tem 157 mil.
    recebidas_ate_cota = int(lim.get('n') or 0)
    ultima = (execute_query(
        """SELECT MAX(data_emissao) AS u FROM nfe_importacoes
            WHERE cliente_id = %s AND origem = 'Q-ROBO'""",
        (cliente_id,), fetch=True, fetch_one=True) or {}).get('u')

    hoje = ag.date()
    em_dia = bool(ultima and (hoje - ultima).days <= DIAS_EM_DIA)

    if not ativo_cadastro:
        return {'fase': 'desligado', 'liberado': False, 'recebidas_ate_cota': recebidas_ate_cota,
                'ultima_emissao': ultima,
                'motivo': 'Desligado no cadastro — ninguém manda nada.'}

    if recebidas_ate_cota < COTA_TESTE:
        return {'fase': 'teste', 'liberado': True, 'recebidas_ate_cota': recebidas_ate_cota,
                'ultima_emissao': ultima,
                'motivo': (f'Teste da instalação: {recebidas_ate_cota} de {COTA_TESTE} '
                           'notas. Enquanto não provar que funciona, manda a '
                           'qualquer hora.')}

    if em_dia:
        return {'fase': 'tempo_real', 'liberado': True, 'recebidas_ate_cota': recebidas_ate_cota,
                'ultima_emissao': ultima,
                'motivo': (f'Em dia (última nota de {ultima:%d/%m/%Y}) — '
                           'mandando em tempo real.')}

    if dentro_da_janela(ag):
        return {'fase': 'retroativo', 'liberado': True, 'recebidas_ate_cota': recebidas_ate_cota,
                'ultima_emissao': ultima,
                'motivo': (f'Retroativo, dentro da janela '
                           f'({JANELA_INICIO:02d}:00–{JANELA_FIM:02d}:00). '
                           f'Chegou até {ultima:%d/%m/%Y}.')}

    volta = proxima_abertura(ag)
    return {'fase': 'retroativo', 'liberado': False, 'recebidas_ate_cota': recebidas_ate_cota,
            'ultima_emissao': ultima,
            'motivo': (f'Retroativo fora da janela — volta às '
                       f'{volta:%H:%M}. Chegou até '
                       f'{ultima:%d/%m/%Y}.' if ultima else
                       f'Retroativo fora da janela — volta às {volta:%H:%M}.')}
