# -*- coding: utf-8 -*-
"""O painel da home do Fiscal é calculado pelo CRON, não pela tela (12/09/2026).

O QUE ESTAVA ERRADO
-------------------
``/api/home-destaques`` calculava o painel **dentro da requisição**, com um
cache em memória de 60 segundos POR WORKER e POR USUÁRIO. Enquanto a conta
levava um segundo isso passava. Hoje ela leva MINUTOS, porque a tabela
``nfe_importacoes`` ganhou 500 mil linhas num dia (750 mil linhas, 7 GB), e o
painel é feito de agregados sobre ela:

    COUNT(DISTINCT cliente_id) dos ultimos 8 dias   -> ~600 mil linhas
    SUM(...) de 8 colunas sobre a tabela INTEIRA    -> sem WHERE nenhum
    GROUP BY emit_cnpj de todas as entradas         -> varredura completa
    COUNT com DATEDIFF em origem/tipo/incompleta    -> varredura completa

Com validade de 60s e conta de 2 minutos, o cache virou o contrário de cache:
**manada**. Cada aba aberta, em cada worker, disparava a própria varredura, e
tudo vencia antes de terminar. Uma tela no celular e outra no PC eram quatro
varreduras simultâneas de 7 GB — e foi isso que deixou a home branca e o app
inutilizável em 12/09/2026.

COMO FICOU
----------
O cron do ``roteador`` (a cada 5 min, fora do site) calcula UMA vez e guarda o
JSON em ``app_config``. A tela só LÊ. Nenhuma requisição calcula nada — nem a
primeira, nem quando o cache vence, porque não vence: o que existe é o último
valor gravado, com a hora dele ao lado.

``app_config`` de propósito, e não tabela nova: ela já é a chave/valor da casa
(horário do scheduler, status do backup, espaço do Dropbox), já guarda JSON, e
**não precisa de migração** — que, depois do ALTER de 304s de hoje, conta.

Painel de indicadores não precisa ser exato ao segundo. Precisa ABRIR.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

CHAVE_HOME_FISCAL = 'painel_home_fiscal'


def guardar(chave, payload):
    """Grava o JSON do painel. Devolve True se gravou."""
    from utils.db_helper import execute_query
    try:
        execute_query(
            "INSERT INTO app_config (chave, valor) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()",
            (chave, json.dumps(payload, ensure_ascii=False, default=str)),
            fetch=False)
        return True
    except Exception:
        logger.exception('[painel] falha ao gravar %s em app_config.', chave)
        return False


def ler(chave):
    """(payload, idade_seg) do último cálculo, ou (None, None) se nunca houve.

    A idade sai do relógio do BANCO (``TIMESTAMPDIFF`` com ``NOW()``), nunca do
    ``datetime.now()`` desta máquina — é a mesma regra do resto do projeto, e
    existe porque o container pode estar em UTC enquanto o banco responde em
    -03:00. Misturar os dois faria o painel dizer "atualizado há 3 horas" um
    segundo depois de ter sido gravado.
    """
    from utils.db_helper import execute_query
    try:
        r = execute_query(
            "SELECT valor, TIMESTAMPDIFF(SECOND, updated_at, NOW()) AS idade "
            "  FROM app_config WHERE chave = %s", (chave,),
            fetch=True, fetch_one=True)
        if not r or not r.get('valor'):
            return None, None
        return json.loads(r['valor']), int(r.get('idade') or 0)
    except Exception:
        logger.exception('[painel] falha ao ler %s de app_config.', chave)
        return None, None


#: De quanto em quanto tempo vale recalcular. O tick do roteador é de 5 min, e
#: a conta MEDIDA em 12/09/2026 passa de 300 SEGUNDOS — sem este intervalo o
#: cron começaria uma conta nova antes de a anterior terminar, e eu teria
#: movido a manada de dentro do site para dentro do cron. Painel de indicador
#: com 15 minutos de idade é painel; painel que não abre é tela branca.
IDADE_MINIMA_SEG = max(60, int(os.getenv('PAINEL_HOME_INTERVALO_SEG', '900')))

#: Nome do lock no MySQL. O lock do `roteador` não serve: ele é liberado dentro
#: do rodar(), e o tick que o encontra ocupado RETORNA e segue para cá — então
#: dois ticks sobrepostos chegariam juntos nesta conta.
_LOCK = 'painel_home_fiscal'


def atualizar_home_fiscal(forcar=False):
    """Calcula o painel e guarda, se já passou ``IDADE_MINIMA_SEG``.

    Devolve o payload quando calculou, None quando não era hora ou quando
    outro processo já estava calculando.

    Importa a função de dentro de ``routes`` porque é lá que ela mora, junto
    dos helpers dela. Mover as 250 linhas para cá seria refatoração grande num
    dia que já teve demais — e o que importa agora é QUEM chama, não onde a
    função está escrita.
    """
    import mysql.connector
    from config import Config

    if not forcar:
        _, idade = ler(CHAVE_HOME_FISCAL)
        if idade is not None and idade < IDADE_MINIMA_SEG:
            return None                    # ainda fresco; não gasta o banco

    # Conexão DEDICADA para o lock: GET_LOCK é por sessão, e as conexões do
    # pool giram entre chamadas — o lock morreria no meio da conta.
    conn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT, autocommit=True,
        time_zone='-03:00')
    cur = conn.cursor(buffered=True)
    try:
        cur.execute("SELECT GET_LOCK(%s, 0)", (_LOCK,))
        if (cur.fetchone() or [0])[0] != 1:
            logger.info('[painel] outro processo já está calculando; pulando.')
            return None

        from routes.escrita_fiscal import _home_destaques_payload
        payload = _home_destaques_payload()
        if not payload:
            logger.warning('[painel] payload vazio; não gravei '
                           '(a tela segue com o anterior).')
            return None
        guardar(CHAVE_HOME_FISCAL, payload)
        return payload
    finally:
        try:
            cur.execute("SELECT RELEASE_LOCK(%s)", (_LOCK,))
            cur.fetchall()
        except Exception:
            pass
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
