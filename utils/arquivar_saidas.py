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

**ATENÇÃO — isto mudou em 12/09/2026.** Era verdade que "nada se perde nesse
caminho, porque ``nfe_importacoes.xml_raw`` já guarda o XML inteiro e o Dropbox
é a SEGUNDA cópia". Não é mais: com o expurgo de 3 meses (``utils/expurgo_xml``),
o banco esvazia o ``xml_raw`` de tudo que passa da janela e **o Dropbox vira a
ÚNICA cópia**. Uma falha de upload aqui não é um atraso, é um documento com uma
cópia só — e o expurgo não a apaga (exige ``xml_caminho``), mas ela também nunca
ganha a segunda. Por isso a repescagem abaixo existe.

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
PARALELO = max(1, min(32, int(os.getenv('QROBO_ARQ_PARALELO', '24'))))
#: Arquivos por chamada de lote ao Dropbox (upload_session_finish_batch aceita 1000).
LOTE_DROPBOX = max(50, min(1000, int(os.getenv('QROBO_ARQ_LOTE', '500'))))


#: Marcador em app_config: último id que o arquivador já processou. Sem ele a
#: consulta das pendentes percorria, em ordem de índice, TODAS as notas já
#: subidas antes de achar a primeira pendente — 36s medidos em 14/09/2026 com
#: 75 mil subidas, e piorando a cada rodada. Com a fronteira, ``id > fronteira``
#: é um range na chave primária e a consulta volta em milissegundos.
MARCA_FRONTEIRA = 'arquivador_fronteira'

#: REPESCAGEM — de quantas em quantas horas o arquivador volta ao corte para
#: reapanhar o que ficou para trás.
#:
#: Por que ela precisa existir: a fronteira avança pelo MAIOR id da FATIA
#: (``_gravar_fronteira(max(...))``), não pelo maior id SUBIDO. Então toda nota
#: que falha no upload fica ATRÁS da fronteira e nunca mais é olhada. Havia um
#: reset para o corte quando ``pendentes()`` voltasse vazia, mas ele
#: praticamente nunca dispara: o Q-Robô alimenta notas novas acima da fronteira
#: o tempo todo, então a fila nunca chega a zero no horário comercial.
#:
#: Medido em 16/09/2026: **6.843 saídas** de postos ATIVOS (Viaduto de Morato,
#: Pavão Castelinho, Jundiaí Mirim, Pavão 91, Westpark), emissão de janeiro a
#: junho, ids 684.254..1.683.642, estavam nesse buraco — sem cópia no Dropbox.
#:
#: **NÃO volte a fronteira para o corte.** Foi a primeira tentativa de conserto,
#: em 16/09/2026, e ela DERRUBA o arquivador — provado em produção em 68s:
#: ``pendentes()`` a partir do corte varre a chave primária inteira, estoura o
#: teto de 30s do ``db_helper`` (``max_execution_time``), e a consulta cortada
#: volta ``None`` -> ``[]``. Aí o arquivador conclui "não há nada pendente",
#: **com a fronteira já gravada no corte** — e como o reset só dispara com
#: ``fronteira > CORTE_ID``, ele nunca mais sai de lá. Fila parada, calada.
#: (O mesmo vale para o reset "volta ao corte quando a leva vier vazia" que
#: existia aqui: era a mesma armadilha, só que nunca chegou a disparar.)
#:
#: O conserto que funciona é varrer em JANELA: um pedaço pequeno do intervalo
#: por rodada, com um cursor próprio que anda do corte até a fronteira e volta
#: ao começo. Cada consulta é um range curto da chave primária, cabe folgada no
#: teto, e o custo se dilui em rodadas de 10 min. Também não paralisa em nota
#: que falhe sempre: a janela anda de qualquer jeito.
MARCA_REPESCA = 'arquivador_repesca'
#: Ids varridos por rodada. Medido em 16/09/2026 contra a produção: uma janela
#: de 50 mil ids leva de 5s (página quente) a 16s (fria) — perto demais do teto
#: de 30s. 25 mil deixa a margem em 2x. A volta completa (corte..fronteira,
#: ~1,65 milhão de ids) sai em ~66 rodadas de 10 min, ou seja, ~11h: duas
#: varreduras por dia, que é de sobra para uma rede de segurança.
REPESCA_JANELA = max(0, int(os.getenv('QROBO_ARQ_REPESCA_JANELA', '25000')))
#: Teto de notas que a repescagem sobe por rodada (o grosso é do fluxo normal).
REPESCA_MAX = max(1, int(os.getenv('QROBO_ARQ_REPESCA_MAX', '2000')))


def _fronteira():
    from utils.painel_cache import ler
    v, _ = ler(MARCA_FRONTEIRA)
    return int((v or {}).get('id') or 0)


def _repesca_cursor():
    """Onde a varredura de trás parou. Anda em janelas, do corte à fronteira."""
    from utils.painel_cache import ler
    v, _ = ler(MARCA_REPESCA)
    return int((v or {}).get('id') or 0)


def _gravar_repesca_cursor(id_):
    from utils.painel_cache import guardar
    guardar(MARCA_REPESCA, {'id': int(id_)})


def _gravar_fronteira(id_):
    from utils.painel_cache import guardar
    guardar(MARCA_FRONTEIRA, {'id': int(id_)})


def pendentes(limite=None, desde=None, ate=None):
    """As saídas do Q-Robô que ainda não foram arquivadas, mais antigas antes.

    ``xml_raw`` vem na consulta porque é o conteúdo a subir — é a coluna mais
    pesada da tabela, então o LIMIT não é conforto, é necessidade.
    ``desde``: id a partir do qual procurar (a fronteira); default = o corte.
    ``ate``: id final, INCLUSIVE. Sem ele a consulta varre da posição até o fim
    da tabela; quando não há pendentes no caminho isso é a chave primária
    INTEIRA, e o teto de 30s do ``db_helper`` corta a consulta — que volta
    ``None`` e vira ``[]``, indistinguível de "não há nada". A repescagem usa
    ``ate`` justamente para nunca cair nisso. Ver MARCA_REPESCA.
    """
    from utils.db_helper import execute_query
    faixa = '' if ate is None else ' AND i.id <= %s'
    p = [max(CORTE_ID, int(desde or 0))]
    if ate is not None:
        p.append(int(ate))
    p.append(int(limite or MAX_POR_RODADA))
    return execute_query(
        f"""SELECT i.id, i.chave_acesso, i.data_emissao, i.xml_raw,
                   c.numero_cliente, c.nome_razao_social
             FROM nfe_importacoes i FORCE INDEX (PRIMARY)
             JOIN clientes c ON c.id = i.cliente_id
            WHERE i.id > %s{faixa}
              AND i.origem = 'Q-ROBO' AND i.tipo = 'saida'
              AND (i.xml_caminho IS NULL OR i.xml_caminho = '')
            ORDER BY i.id
            LIMIT %s""",
        tuple(p), fetch=True) or []


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


def _trava(nome='arquivador'):
    """Conexão dedicada segurando GET_LOCK: o arquivador roda no roteador E no
    serviço de manutenção (13/09/2026); sem trava os dois pegariam a mesma
    fatia mais antiga e subiriam a mesma nota duas vezes. Devolve (conn, ok)."""
    import mysql.connector
    from config import Config
    conn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT, autocommit=True, time_zone='-03:00')
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT GET_LOCK(%s, 0)", (nome,))
    ok = (cur.fetchone() or [0])[0] == 1
    cur.close()
    return conn, ok


def arquivar_pendentes(limite=None, dry=True, prazo_seg=None):
    """Sobe o que falta e grava ``xml_caminho``. ``dry=True`` só conta.
    ``prazo_seg`` sobrepõe o orçamento padrão (o serviço de manutenção passa
    um orçamento maior que o do tick do roteador).

    Devolve o resumo da rodada. O prazo suave existe para a rodada caber no
    tick de 5 minutos do roteador: o que não couber vai no próximo.
    """
    from utils.db_helper import execute_query
    prazo = int(prazo_seg or PRAZO_SEG)
    fim = time.monotonic() + prazo
    conn_lock, ok = _trava()
    if not ok:
        conn_lock.close()
        logger.info('[arq-saidas] outra rodada do arquivador em andamento; pulando.')
        return {'pulado': 'outra rodada em andamento', 'subidos': 0, 'falhas': 0}
    try:
        return _arquivar(limite, dry, prazo, fim, execute_query)
    finally:
        try:
            cur = conn_lock.cursor(buffered=True)
            cur.execute("SELECT RELEASE_LOCK('arquivador')"); cur.fetchall(); cur.close()
        except Exception:
            pass
        try:
            conn_lock.close()
        except Exception:
            pass


def _subir_fatias(linhas, r, dry, fim, prazo, execute_query, marcar=None):
    """Sobe ``linhas`` em fatias e grava ``xml_caminho``.

    Usado pelo fluxo normal E pela repescagem — uma implementação só, para as
    duas não divergirem com o tempo (foi assim que as duas listas de ``cStat``
    do cancelamento divergiram).

    ``marcar``: callable(id) chamado com o maior id da fatia, para quem quiser
    avançar um cursor. A repescagem NÃO avança a fronteira principal.
    """
    from utils import dropbox_sync
    svc = dropbox_sync._service
    # Fatias de PARALELO em vez de mandar a leva inteira ao pool: assim o prazo
    # suave E o freio do 429 são conferidos entre as fatias, e a rodada para de
    # verdade em vez de enfileirar tudo de uma vez num pool que não olha mais.
    for i in range(0, len(linhas), LOTE_DROPBOX):
        if time.monotonic() > fim:
            logger.info('[arq-saidas] prazo (%ss) atingido; resto no próximo tick.',
                        prazo)
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
        fatia = linhas[i:i + LOTE_DROPBOX]
        itens, por_caminho = [], {}
        for linha in fatia:
            numero = (linha.get('numero_cliente') or '').strip() or None
            razao = linha.get('nome_razao_social') or 'SEM_NOME'
            dt = linha.get('data_emissao') or date.today()
            pasta = svc.pasta_fiscal(razao, dt.year, dt.month, 'SAIDAS', numero)
            caminho = f"{pasta}/{linha['chave_acesso']}.xml"
            dados = linha.get('xml_raw') or ''
            if not dados:
                # Sem XML no banco E sem caminho no Dropbox: o arquivador não
                # tem o que subir e a repescagem vai reencontrar esta linha para
                # sempre. Conta à parte para não se disfarçar de falha de rede.
                r['sem_xml'] = r.get('sem_xml', 0) + 1
                amostra = r.setdefault('sem_xml_ids', [])
                if len(amostra) < 20:      # o resumo vai para app_config: amostra, não lista
                    amostra.append(linha['id'])
                continue
            if isinstance(dados, str):
                dados = dados.encode('utf-8')
            itens.append((caminho, dados))
            por_caminho[caminho] = linha['id']
        resultado = svc.upload_lote(itens, paralelo=PARALELO) if itens else {}
        subidos = [(caminho[:255], por_caminho[caminho]) for caminho, ok in resultado.items() if ok]
        if subidos and not dry:
            # UM UPDATE para o lote inteiro (CASE por id): 64 UPDATEs soltos
            # levavam 13s de ida e volta; um só, uma ida.
            casos = ' '.join(['WHEN %s THEN %s'] * len(subidos))
            params = [x for cam, i in subidos for x in (i, cam)] + [i for _, i in subidos]
            execute_query(
                f"UPDATE nfe_importacoes SET xml_caminho = CASE id {casos} END "
                f" WHERE id IN ({','.join(['%s'] * len(subidos))})", tuple(params), fetch=False)
        r['subidos'] += len(subidos)
        recusados = len(itens) - len(subidos)
        if recusados:
            # Quem falha aqui fica ATRÁS do cursor que avança — e só a
            # repescagem o traz de volta. Sem este log a falha virava um número
            # no card e ninguém descobria QUAIS notas ficaram sem segunda
            # cópia: foi o que escondeu 6.843 delas até 16/09/2026.
            ok = {c for c, v in resultado.items() if v}
            perdidos = [por_caminho[c] for c in por_caminho if c not in ok]
            logger.warning('[arq-saidas] %s nota(s) recusadas pelo Dropbox nesta '
                           'fatia; ficam para a repescagem. ids: %s',
                           recusados, perdidos[:20])
            amostra = r.setdefault('recusados_ids', [])
            amostra.extend(perdidos[:max(0, 20 - len(amostra))])
        r['falhas'] += recusados
        if marcar is not None and not dry:
            marcar(max(l['id'] for l in fatia))
    return r


def _repescar(fronteira, dry, fim, prazo, execute_query):
    """UMA JANELA de ids atrás da fronteira, por rodada.

    A fronteira avança pelo maior id da FATIA, não pelo id SUBIDO: quem falha
    fica atrás dela para sempre. Esta varredura é quem os traz de volta. Ela
    anda em janelas curtas justamente para nunca estourar o teto de 30s do
    ``db_helper`` — ver MARCA_REPESCA no topo, e o que aconteceu em 16/09/2026
    quando a primeira versão do conserto tentou varrer do corte de uma vez.
    """
    if dry or REPESCA_JANELA <= 0 or fronteira <= CORTE_ID:
        return None
    desde = _repesca_cursor()
    if desde < CORTE_ID or desde >= fronteira:
        desde = CORTE_ID                      # primeira volta, ou deu a volta
    ate = min(desde + REPESCA_JANELA, fronteira)
    linhas = pendentes(limite=REPESCA_MAX, desde=desde, ate=ate)
    # O cursor avança SEMPRE, ache ou não ache: é o que garante que a janela não
    # empaque numa nota que falha sempre.
    _gravar_repesca_cursor(ate)
    r = {'janela': [desde, ate], 'achadas': len(linhas), 'subidos': 0, 'falhas': 0,
         # Andamento da volta, em %: é o número honesto que substituiu o
         # "fila_total" (que vinha sempre cortado pelo teto, publicando 0).
         'volta_pct': round(100.0 * (ate - CORTE_ID) / max(1, fronteira - CORTE_ID), 1)}
    if linhas:
        logger.warning('[arq-saidas] REPESCAGEM: %s nota(s) sem cópia no Dropbox '
                       'na janela %s..%s; subindo.', len(linhas), desde, ate)
        _subir_fatias(linhas, r, dry, fim, prazo, execute_query, marcar=None)
    return r


def _arquivar(limite, dry, prazo, fim, execute_query):
    fronteira = _fronteira()
    linhas = pendentes(limite, desde=fronteira)
    r = {'pendentes_nesta_leva': len(linhas), 'subidos': 0, 'falhas': 0,
         'dry': dry, 'corte_id': CORTE_ID, 'paralelo': PARALELO}
    # NÃO volte a fronteira para o corte quando a leva vier vazia. Ver
    # MARCA_REPESCA: a consulta sem limite superior estoura o teto de 30s, volta
    # vazia, e o arquivador fica preso no corte achando que não há nada. Quem
    # varre para trás é a repescagem, em janelas.
    #
    # Aqui havia um `SELECT COUNT(*) ... WHERE id > CORTE_ID AND xml_caminho
    # vazio` para publicar "fila_total" no card. Ele era CORTADO pelo teto em
    # toda rodada (varre a PK inteira) e publicava 0 — fila cheia aparecendo
    # como zero, que foi parte do que escondeu as 6.843. Ninguém lia esse número
    # fora daqui, e não há como computá-lo barato; saiu. O andamento honesto é
    # o da repescagem, abaixo.
    if not dry and linhas:
        _subir_fatias(linhas, r, dry, fim, prazo, execute_query, marcar=_gravar_fronteira)
    repesca = _repescar(fronteira, dry, fim, prazo, execute_query)
    if repesca:
        r['repescagem'] = repesca
    return r
