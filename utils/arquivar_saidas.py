# -*- coding: utf-8 -*-
"""Arquiva no Dropbox as saídas que o Q-Robô mandou — FORA da requisição.

POR QUE ESTE MÓDULO EXISTE
--------------------------
Até 12/09/2026 a rota ``POST /api/saidas`` subia o XML para o Dropbox **dentro
da requisição**, antes de responder ao robô. Medido em produção naquele dia:
cada upload levava **0,7 a 0,9 segundo** de espera de rede, e o Q-Robô mandava
**~60 notas por minuto** — 48 segundos de thread do gunicorn por minuto gastos
só esperando o Dropbox. Uma thread inteira, em tempo integral. O site ficava em
6 a 8 segundos para servir um arquivo CSS, com a CPU do contêiner em 0,0 vCPU:
não era falta de máquina, era espera.

E a conta ia piorar: eram **14 robôs ativos** com **200 a instalar**. A 15×, a
recepção precisaria de ~12 threads presas só para o Dropbox.

O QUE MUDOU
-----------
A rota grava no banco e responde. Quem arquiva é este módulo, chamado pelo cron
do ``roteador`` (que já roda a cada 5 min, fora do site). O arquivo chega ao
Dropbox minutos depois em vez de segundos — e ninguém olha aquela pasta com
pressa.

**Nada se perde nesse caminho**: ``nfe_importacoes.xml_raw`` já guarda o XML
inteiro (medido: ~7,5 KB por nota). O Dropbox é a SEGUNDA cópia, não a única.

O CORTE, e por que ele é obrigatório
------------------------------------
Há **203.044** saídas do Q-Robô no histórico, e nenhuma delas tem
``xml_caminho`` gravado — a rota antiga subia o arquivo e não anotava onde.
Então "sem caminho" NÃO significa "falta subir" para o que é antigo. Por isso o
arquivador só olha linhas com ``id`` acima de um corte (``QROBO_ARQ_DESDE_ID``,
default 293768 = o máximo no momento da mudança). Sem o corte, a primeira
rodada tentaria reenviar duzentas mil notas.

De agora em diante ``xml_caminho`` É gravado, e passa a ser a resposta honesta
para "onde está o arquivo desta nota?" — pergunta que hoje não tem resposta.
"""
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

logger = logging.getLogger(__name__)

#: Só linhas acima deste id entram. Ver "O CORTE" no topo.
CORTE_ID = int(os.getenv('QROBO_ARQ_DESDE_ID', '293768'))

#: Teto por rodada e prazo suave, no mesmo espírito dos outros crons da casa.
#:
#: O 600 inicial foi calculado para as 60 notas/min de antes do conserto. No
#: minuto em que o upload saiu da requisição o robô passou a despachar ~250/min
#: (medido em 12/09/2026: 2.503 notas em 10 min), e 600 por tick de 5 min são
#: 120/min — a fila CRESCIA. 2.000 por tick dão 400/min, que cobre a entrada
#: com folga. A conta do prazo: 2000 ÷ 12 paralelos × 0,8s ≈ 133s, dentro dos
#: 240s, e os 240 cabem no tick de 5 minutos.
MAX_POR_RODADA = max(1, int(os.getenv('QROBO_ARQ_MAX', '2000')))
PRAZO_SEG = max(10, int(os.getenv('QROBO_ARQ_PRAZO_SEG', '240')))

#: Uploads em PARALELO. Um upload é 0,8s de espera de REDE, não de CPU — então
#: subir vários ao mesmo tempo custa quase nada de processador e multiplica a
#: vazão. Não subo mais que isso porque o Dropbox limita (429), e a partir daí
#: paralelismo só produz recusa: ver o freio em ``limitado_por()``.
PARALELO = max(1, min(16, int(os.getenv('QROBO_ARQ_PARALELO', '12'))))


def pendentes(limite=None):
    """As saídas do Q-Robô que ainda não foram arquivadas, mais antigas antes.

    ``xml_raw`` vem na consulta porque é o conteúdo a subir — é a coluna mais
    pesada da tabela, então o LIMIT não é conforto, é necessidade.
    """
    from utils.db_helper import execute_query
    return execute_query(
        """SELECT i.id, i.chave_acesso, i.data_emissao, i.xml_raw,
                  c.numero_cliente, c.nome_razao_social
             FROM nfe_importacoes i
             JOIN clientes c ON c.id = i.cliente_id
            WHERE i.origem = 'Q-ROBO' AND i.tipo = 'saida'
              AND i.id > %s
              AND (i.xml_caminho IS NULL OR i.xml_caminho = '')
            ORDER BY i.id
            LIMIT %s""",
        (CORTE_ID, int(limite or MAX_POR_RODADA)), fetch=True) or []


def _subir_um(linha):
    """Sobe UMA nota e devolve (id, caminho) ou (id, None) se falhou.

    Best-effort por linha, como era na rota: a nota já está no banco, e uma
    falha de Dropbox não pode parar a fila — ela volta na próxima rodada,
    porque ``xml_caminho`` continua vazio.
    """
    from utils import dropbox_sync
    try:
        svc = dropbox_sync._service
        numero = (linha.get('numero_cliente') or '').strip() or None
        razao = linha.get('nome_razao_social') or 'SEM_NOME'
        dt = linha.get('data_emissao') or date.today()
        pasta = svc.pasta_fiscal(razao, dt.year, dt.month, 'SAIDAS', numero)
        caminho = f"{pasta}/{linha['chave_acesso']}.xml"
        dados = (linha.get('xml_raw') or '')
        if not dados:
            logger.warning('[arq-saidas] id=%s sem xml_raw — nada a subir.',
                           linha['id'])
            return linha['id'], None
        if isinstance(dados, str):
            dados = dados.encode('utf-8')
        if svc.upload_bytes(caminho, dados):
            return linha['id'], caminho
        return linha['id'], None
    except Exception:
        logger.warning('[arq-saidas] falha ao arquivar id=%s chave=%s',
                       linha.get('id'), linha.get('chave_acesso'))
        return linha.get('id'), None


def arquivar_pendentes(limite=None, dry=True):
    """Sobe o que falta e grava ``xml_caminho``. ``dry=True`` só conta.

    Devolve o resumo da rodada. O prazo suave existe para a rodada caber no
    tick de 5 minutos do roteador: o que não couber vai no próximo.
    """
    from utils.db_helper import execute_query

    fim = time.monotonic() + PRAZO_SEG
    linhas = pendentes(limite)
    r = {'pendentes_nesta_leva': len(linhas), 'subidos': 0, 'falhas': 0,
         'dry': dry, 'corte_id': CORTE_ID, 'paralelo': PARALELO}
    if dry or not linhas:
        total = execute_query(
            """SELECT COUNT(*) n FROM nfe_importacoes
                WHERE origem = 'Q-ROBO' AND tipo = 'saida' AND id > %s
                  AND (xml_caminho IS NULL OR xml_caminho = '')""",
            (CORTE_ID,), fetch=True, fetch_one=True) or {}
        r['fila_total'] = total.get('n') or 0
        return r

    # Fatias de PARALELO em vez de mandar a leva inteira ao pool: assim o prazo
    # suave E o freio do 429 são conferidos entre as fatias, e a rodada para de
    # verdade em vez de enfileirar tudo de uma vez num pool que não olha mais.
    from utils import dropbox_sync
    svc = dropbox_sync._service
    for i in range(0, len(linhas), PARALELO):
        if time.monotonic() > fim:
            logger.info('[arq-saidas] prazo (%ss) atingido; resto no próximo tick.',
                        PRAZO_SEG)
            break
        # FREIO DO 429: com o Dropbox limitando, continuar é bater mais forte
        # numa porta fechada — e pior, gastar a leva inteira em recusa, o que
        # faria a rodada seguinte encontrar a mesma fila e repetir. Para aqui e
        # volta no próximo tick, quando a janela dele já passou.
        falta = svc.limitado_por()
        if falta > 0:
            logger.warning('[arq-saidas] Dropbox limitando por mais %.0fs — '
                           'parando a rodada com %s subida(s).', falta, r['subidos'])
            r['freado_por_429'] = round(falta)
            break
        fatia = linhas[i:i + PARALELO]
        with ThreadPoolExecutor(max_workers=PARALELO) as pool:
            for lid, caminho in pool.map(_subir_um, fatia):
                if caminho:
                    execute_query(
                        'UPDATE nfe_importacoes SET xml_caminho = %s WHERE id = %s',
                        (caminho[:255], lid), fetch=False)
                    r['subidos'] += 1
                else:
                    r['falhas'] += 1
    return r
