# -*- coding: utf-8 -*-
"""Semáforo do Q-Robô — regra ÚNICA, usada pelo painel admin e pelo portal.

Os limiares e os rótulos viviam só em routes/escrita_fiscal.py. Com o portal
mostrando status também, duas cópias divergiriam no primeiro ajuste — então a
regra mora aqui e os dois lados importam daqui.

O que o portal enxerga é só OPERACIONAL: quando o robô deu o último sinal de
vida e se está ligado. Nada de nota, valor ou contagem de captura — o portal
não tem porta para dado fiscal e este módulo não abre uma.

FUSO: a conta é ``TIMESTAMPDIFF(..., NOW())`` no SQL. O pool conecta em
``-03:00`` (utils/db_helper.py), então ``robo_ultimo_contato`` e ``NOW()``
estão ambos em BRT. O relógio do processo (UTC no Railway) não entra na conta.
"""
from utils.db_helper import execute_query

# Limiares do semáforo, em minutos sem contato. O robô fala com a nuvem a cada
# ciclo — mesmo desligado ou sem nota nova (routes/robo_saidas.py). Silêncio
# aqui significa processo parado, não falta de venda.
LIMIAR_VERDE = 15       # 🟢 até 15 min
LIMIAR_LARANJA = 180    # 🟠 15 min a 3h · 🔴 acima de 3h

ACOES_CHAVE = ('CHAVE_GERADA', 'CHAVE_REGERADA')

#: Atos que fazem um posto ser "seu" para efeito de ver o status dele.
#:
#: Inclui CHAVE_REVELADA de propósito, e a conta fecha assim: quem revela a
#: chave **já está com a credencial do posto na mão** — comparado a isso, saber
#: há quanto tempo aquele robô não dá sinal é informação menor. Sem isto, o
#: técnico que vai instalar a SEGUNDA máquina (e que não foi quem gerou a
#: chave) tomaria 403 no "Verificar agora" da própria tela que acabou de
#: mostrar a chave para ele.
#:
#: O que se perde, dito por inteiro: antes, bisbilhotar o status de um posto
#: alheio custava REGERAR a chave dele, ou seja, derrubar o robô de um cliente.
#: Agora custa uma revelação. O controle deixou de ser o estrago e passou a ser
#: só o rastro — toda revelação grava nome, IP e hora na qrobo_auditoria.
ACOES_MEU_POSTO = ACOES_CHAVE + ('CHAVE_REVELADA',)


def ha(minutos):
    """'agora' / 'há 12 min' / 'há 6h20' / 'há 3 dias' a partir de minutos."""
    if minutos is None:
        return '—'
    minutos = int(minutos)
    if minutos < 1:
        return 'agora'
    if minutos < 60:
        return f'há {minutos} min'
    if minutos < 2880:                       # até 48h mostra hora cheia
        h, m = divmod(minutos, 60)
        return f'há {h}h{m:02d}'
    return f'há {minutos // 1440} dias'


def classificar(min_sem_contato):
    """(classe_css, rótulo) do semáforo a partir dos minutos sem contato."""
    if min_sem_contato is None:
        return 'cinza', 'nunca contatou'
    if min_sem_contato < LIMIAR_VERDE:
        return 'verde', 'ativo'
    if min_sem_contato < LIMIAR_LARANJA:
        return 'amarelo', f'atenção — {ha(min_sem_contato)}'
    return 'vermelho', f'parado {ha(min_sem_contato)}'


def _monta(linha):
    """Linha de robo_config + cliente -> dict de status para a tela/JSON.

    ``confirmado`` é o que o instalador realmente quer saber: o robô falou com
    a nuvem DEPOIS da chave atual ter sido gerada. Sem isso, um posto que
    acabou de ter a chave trocada apareceria 'ativo' por causa do contato feito
    com a chave ANTIGA — exatamente a leitura errada na hora da instalação.
    """
    minutos = linha.get('min_sem_contato')
    cls, rotulo = classificar(minutos)
    contato = linha.get('robo_ultimo_contato')
    gerada = linha.get('token_gerado_em')

    if gerada is None:
        # Chave anterior ao portal: não dá para saber se o contato é posterior
        # a ela. Vale o semáforo puro.
        confirmado = None
    else:
        confirmado = bool(contato and contato > gerada)

    if confirmado is False:
        cls_portal, rotulo_portal = 'cinza', 'aguardando o primeiro contato'
    else:
        cls_portal, rotulo_portal = cls, rotulo

    return {
        'cliente_id': linha['cliente_id'],
        'numero_cliente': linha.get('numero_cliente') or '',
        'razao_social': linha.get('nome_razao_social') or '',
        'ativo': bool(linha.get('ativo')),
        'status': cls_portal,
        'rotulo': rotulo_portal,
        'status_bruto': cls,
        'min_sem_contato': None if minutos is None else int(minutos),
        'ultimo_contato': contato.strftime('%d/%m/%Y %H:%M') if contato else None,
        'ha': ha(minutos),
        'ja_deu_sinal': contato is not None,
        'confirmado': confirmado,
        'chave_gerada_em': gerada.strftime('%d/%m/%Y %H:%M') if gerada else None,
    }


_SELECT_STATUS = (
    "SELECT r.cliente_id, r.ativo, r.robo_ultimo_contato, r.token_gerado_em, "
    "       TIMESTAMPDIFF(MINUTE, r.robo_ultimo_contato, NOW()) AS min_sem_contato, "
    "       c.numero_cliente, c.nome_razao_social "
    "  FROM robo_config r "
    "  LEFT JOIN clientes c ON c.id = r.cliente_id "
)


def status_posto(cliente_id):
    """Status ao vivo de UM posto, ou None se não tem robo_config."""
    linha = execute_query(_SELECT_STATUS + " WHERE r.cliente_id = %s",
                          (cliente_id,), fetch=True, fetch_one=True)
    return _monta(linha) if linha else None


def pode_ver_status(usuario_id, cliente_id):
    """Só quem TRABALHOU naquele posto vê o status dele pelo portal.

    Trabalhou = gerou, substituiu ou revelou a chave (ACOES_MEU_POSTO). Sem
    esta trava, o portal viraria uma sonda para descobrir qual cliente tem robô
    e há quanto tempo está parado — informação que o instalador não precisa dos
    postos em que não mexeu.
    """
    marcas = ','.join(['%s'] * len(ACOES_MEU_POSTO))
    achou = execute_query(
        "SELECT 1 AS x FROM qrobo_auditoria "
        f" WHERE usuario_id = %s AND cliente_id = %s AND acao IN ({marcas}) LIMIT 1",
        (usuario_id, cliente_id) + ACOES_MEU_POSTO, fetch=True, fetch_one=True)
    return bool(achou)


def meus_postos(usuario_id, limite=20):
    """Postos em que ESTE colaborador mexeu na chave, com o status atual.

    Gerou, substituiu ou revelou (ACOES_MEU_POSTO) — a revelação entra porque
    quem foi buscar a chave foi instalar uma máquina ali, e vai querer conferir
    o posto depois. Ordenado pelo ato mais recente. Um posto aparece uma vez
    só, mesmo com vários atos — a data mostrada é a do último.
    """
    marcas = ','.join(['%s'] * len(ACOES_MEU_POSTO))
    linhas = execute_query(
        "SELECT a.cliente_id, MAX(a.criado_em) AS gerada_em, COUNT(*) AS vezes "
        "  FROM qrobo_auditoria a "
        f" WHERE a.usuario_id = %s AND a.cliente_id IS NOT NULL AND a.acao IN ({marcas}) "
        " GROUP BY a.cliente_id "
        " ORDER BY gerada_em DESC LIMIT %s",
        (usuario_id,) + ACOES_MEU_POSTO + (int(limite),), fetch=True) or []
    if not linhas:
        return []

    ids = [l['cliente_id'] for l in linhas]
    marcas_ids = ','.join(['%s'] * len(ids))
    status = {s['cliente_id']: s for s in (
        execute_query(_SELECT_STATUS + f" WHERE r.cliente_id IN ({marcas_ids})",
                      tuple(ids), fetch=True) or [])}

    saida = []
    for l in linhas:
        base = status.get(l['cliente_id'])
        item = _monta(base) if base else {
            'cliente_id': l['cliente_id'], 'numero_cliente': '', 'razao_social': '',
            'ativo': False, 'status': 'cinza', 'rotulo': 'sem configuração',
            'status_bruto': 'cinza', 'min_sem_contato': None, 'ultimo_contato': None,
            'ha': '—', 'ja_deu_sinal': False, 'confirmado': None,
            'chave_gerada_em': None}
        item['gerada_em'] = l['gerada_em'].strftime('%d/%m/%Y %H:%M') if l['gerada_em'] else None
        item['vezes'] = int(l['vezes'] or 1)
        saida.append(item)
    return saida
