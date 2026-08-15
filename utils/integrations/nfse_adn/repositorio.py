# -*- coding: utf-8 -*-
"""Persistência da captura de NFS-e — e o ÚNICO escritor de ``situacao``.

Quem lê campo do XML é o ``parser``; quem fala com o ADN é o ``client``. Aqui
mora o que grava e, principalmente, a regra que decide se uma nota está ativa,
cancelada ou substituída.

A REGRA DE OURO
---------------
``situacao`` é campo DERIVADO. Ninguém escreve nele por inferência no momento da
captura — o documento entra SEMPRE como ``ativa``, e só evento muda isso, por
``recalcular_situacao()``. Nenhuma outra função deste módulo, ou de qualquer
outro, escreve nessa coluna. Se algum dia alguém precisar, a resposta é chamar
``recalcular_situacao()``, não fazer um UPDATE ao lado.

Motivo: o parser não sabe, olhando só o documento, se ele foi cancelado depois.
Se tentar adivinhar, erra — e erro de situação fiscal só aparece na fiscalização.

O EVENTO É GRAVADO EXISTA OU NÃO O DOCUMENTO
--------------------------------------------
Eventos chegam com NSU próprio, independente da nota. Três cenários, e os três
acontecem de verdade:

  * evento DEPOIS do documento  -> caso normal, aplica na hora
  * evento ANTES do documento   -> backfill que começou no meio: fica ÓRFÃO
  * documento nunca capturado   -> município fora do ADN: órfão para sempre

O bug clássico é aplicar evento só em linha existente e descartar o resto. Aí o
órfão desaparece e, quando o documento finalmente chega, entra como ``ativa`` —
permanentemente errado, e sem sinal nenhum de que está errado.

Spec: docs/NFSE_ADN_ESPECIFICACAO.md
"""
import json
import logging

from utils.db_helper import execute_query, transacao
from utils.integrations.nfse_adn.parser import MAPA_EVENTO_SITUACAO

logger = logging.getLogger(__name__)


class MapaVazioError(Exception):
    """``MAPA_EVENTO_SITUACAO`` vazio. Provável falha de import.

    É EXCEÇÃO e não retorno de erro por um motivo prático: um dict de erro pode
    ser ignorado por quem chama, e a função que existe para evitar falha
    silenciosa não pode falhar em silêncio. Sobe até a tela e até o log.
    """


# Precedência: uma vez fora de 'ativa', NÃO VOLTA. A ordem garante isso sem
# guardar estado — não há lógica de "desfazer" em lugar nenhum.
PRECEDENCIA = {'ativa': 0, 'cancelada': 1, 'substituida': 2}


def _guarda_mapa():
    """Tipos conhecidos, ou levanta. NÃO altera nada antes de estourar.

    Com o mapa vazio, ``tipo NOT IN (NULL)`` nunca é verdadeiro em SQL — a
    consulta voltaria ZERO linhas exatamente quando TUDO deveria ser rebaixado,
    e o retorno pareceria sucesso. Mapa vazio não é hipótese acadêmica: um erro
    de import deixando o dicionário sem carregar já basta.
    """
    tipos = tuple(MAPA_EVENTO_SITUACAO.keys())
    if not tipos:
        raise MapaVazioError(
            'MAPA_EVENTO_SITUACAO está vazio. Abortado sem alterar nada. '
            'Verifique o import do parser antes de rodar de novo.')
    return tipos


def existe_documento(chave_acesso) -> bool:
    r = execute_query(
        'SELECT 1 AS x FROM nfse_capturadas WHERE chave_acesso = %s LIMIT 1',
        (chave_acesso,), fetch=True, fetch_one=True)
    return bool(r)


def existem_eventos(chave_acesso) -> bool:
    r = execute_query(
        'SELECT 1 AS x FROM nfse_eventos WHERE chave_referenciada = %s LIMIT 1',
        (chave_acesso,), fetch=True, fetch_one=True)
    return bool(r)


# ---------------------------------------------------------------------------
# O ESCRITOR ÚNICO
# ---------------------------------------------------------------------------
def recalcular_situacao(chave_acesso: str, cur=None) -> str:
    """ÚNICA função autorizada a escrever ``nfse_capturadas.situacao``.

    ESCOPO DO UPDATE — intencional
    ------------------------------
    Atualiza TODAS as linhas desta ``chave_acesso``, sem filtrar por ``papel``.
    Se a nota tem 2 linhas (emitente e tomador, ambos na carteira), as duas
    recebem a mesma situação. É correto: o cancelamento é fato do DOCUMENTO,
    não da parte.

    NÃO USE ``rowcount`` PARA NADA AQUI
    -----------------------------------
      * 2 linhas afetadas é normal (nota com 2 papéis na carteira);
      * 0 linhas é normal — a situação já era a calculada, e o MySQL não conta
        linha inalterada como afetada;
      * 0 linhas também ocorre quando o documento ainda não foi capturado.
    O número não distingue sucesso de ausência. Para saber se existe, chame
    ``existe_documento()`` antes.

    Determinística e idempotente: recalcula do zero a partir dos eventos, então
    pode rodar quantas vezes for. É por isso que remover um tipo do mapa não
    exige lógica de "desfazer" — o efeito dele some sozinho do cálculo.
    """
    # LÊ PELO MESMO CURSOR quando está dentro de uma transação. Não é
    # preciosismo: ``execute_query`` pega OUTRA conexão do pool (autocommit), e
    # o evento acabado de inserir na transação ainda não teria commit — o
    # cálculo o ignoraria e a nota ficaria um passo atrasada. Foi exatamente
    # isso que o primeiro teste ponta a ponta pegou: o cancelamento deferido
    # só aparecia na gravação SEGUINTE.
    sel = ('SELECT tipo_evento, chave_substituta FROM nfse_eventos '
           'WHERE chave_referenciada = %s')
    if cur is not None:
        cur.execute(sel, (chave_acesso,))
        eventos = cur.fetchall() or []
    else:
        eventos = execute_query(sel, (chave_acesso,), fetch=True) or []

    situacao, substituta = 'ativa', None
    for ev in eventos:
        nova = MAPA_EVENTO_SITUACAO.get(ev['tipo_evento'])
        if nova is None:
            continue          # manifestação conhecida, ou tipo desconhecido
        if PRECEDENCIA[nova] > PRECEDENCIA[situacao]:
            situacao = nova
        if nova == 'substituida' and ev.get('chave_substituta'):
            substituta = ev['chave_substituta']

    sql = ('UPDATE nfse_capturadas SET situacao=%s, chave_substituta=%s '
           'WHERE chave_acesso=%s')
    if cur is not None:
        cur.execute(sql, (situacao, substituta, chave_acesso))
    else:
        execute_query(sql, (situacao, substituta, chave_acesso), fetch=False)
    return situacao


# ---------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------
_COLS_DOC = (
    'empresa_id', 'cnpj_interessado', 'nsu', 'chave_acesso', 'papel', 'tipo_doc',
    'numero', 'serie', 'data_emissao', 'data_processamento', 'competencia',
    'municipio_ibge', 'municipio_emissao',
    'prestador_doc', 'prestador_nome', 'tomador_doc', 'tomador_nome',
    'intermediario_doc', 'destinatario_doc', 'destinatario_nome',
    'codigo_servico', 'codigo_servico_mun', 'codigo_nbs', 'discriminacao',
    'valor_servicos', 'valor_desc_incond', 'valor_desc_cond', 'base_calculo',
    'aliquota_iss', 'valor_iss', 'total_retencoes', 'valor_liquido',
    'iss_retido', 'opt_simples', 'cstat', 'substitui_chave',
    'ibscbs_cst', 'ibscbs_cclasstrib', 'ibscbs_fin_nfse', 'ibscbs_cind_op',
    'ibscbs_ind_dest', 'ibscbs_bc',
    'ibs_uf_aliq_efet', 'ibs_uf_valor', 'ibs_uf_dif',
    'ibs_mun_aliq_efet', 'ibs_mun_valor', 'ibs_mun_dif', 'ibs_total',
    'cbs_aliq_efet', 'cbs_valor', 'cbs_dif',
    'ibs_cred_pres', 'cbs_cred_pres', 'valor_total_nf',
    'xml_path', 'raw_json',
)

# NÃO entra no UPDATE: 'situacao' (só recalcular_situacao escreve),
# 'chave_substituta' (idem) e 'restricao_*' (eixo do evento de ofício).
# Reprocessar um documento não pode ressuscitar uma nota cancelada.
_UPD_DOC = ', '.join(f'{c}=VALUES({c})' for c in _COLS_DOC
                     if c not in ('chave_acesso', 'papel'))

SQL_DOC_UPSERT = (
    f"INSERT INTO nfse_capturadas ({', '.join(_COLS_DOC)}) "
    f"VALUES ({', '.join(['%s'] * len(_COLS_DOC))}) "
    f"ON DUPLICATE KEY UPDATE {_UPD_DOC}"
)


def salvar_documento(reg: dict, cur=None) -> None:
    """Grava/atualiza a NFS-e. Entra SEMPRE como 'ativa' (o default da coluna).

    Não escreve ``situacao``: quem decide isso é o evento, via
    ``recalcular_situacao()``. E o UPDATE do upsert exclui essa coluna de
    propósito — reprocessar um documento não pode ressuscitar nota cancelada.
    """
    valores = []
    for c in _COLS_DOC:
        v = reg.get(c)
        if c == 'raw_json' and v is not None and not isinstance(v, str):
            v = json.dumps(v, default=str)
        valores.append(v)
    if cur is not None:
        cur.execute(SQL_DOC_UPSERT, tuple(valores))
    else:
        execute_query(SQL_DOC_UPSERT, tuple(valores), fetch=False)


SQL_EVT_UPSERT = (
    "INSERT INTO nfse_eventos "
    "(chave_referenciada, tipo_evento, sequencia, empresa_id_origem, cnpj_origem, "
    " nsu_origem, data_evento, motivo, chave_substituta, revisar, orfao, raw_json) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE "
    # revisar É recalculado: o mapa pode ter ganhado o tipo desde a 1ª entrega.
    "  revisar=VALUES(revisar), "
    # COALESCE: um reprocessamento sem a chave não apaga a que já estava.
    "  chave_substituta=COALESCE(VALUES(chave_substituta), chave_substituta), "
    "  raw_json=VALUES(raw_json)"
    # NÃO sobrescreve empresa_id_origem/cnpj_origem/nsu_origem: são a
    # proveniência do PRIMEIRO cursor, por definição.
    # NÃO sobrescreve aplicado/orfao: quem manda neles é o fluxo de aplicação.
)


def salvar_evento(reg: dict, cur=None) -> None:
    """Grava o evento — EXISTA OU NÃO o documento. Ver o cabeçalho do módulo."""
    orfao = 0 if existe_documento(reg.get('chave_referenciada')) else 1
    raw = reg.get('raw_json')
    if raw is not None and not isinstance(raw, str):
        raw = json.dumps(raw, default=str)
    params = (
        reg.get('chave_referenciada'), reg.get('tipo_evento'),
        reg.get('sequencia'), reg.get('empresa_id_origem'), reg.get('cnpj_origem'),
        reg.get('nsu_origem'), reg.get('data_evento'), reg.get('motivo'),
        reg.get('chave_substituta'), int(reg.get('revisar') or 0), orfao, raw,
    )
    if cur is not None:
        cur.execute(SQL_EVT_UPSERT, params)
    else:
        execute_query(SQL_EVT_UPSERT, params, fetch=False)


def marcar_eventos_aplicados(chave_acesso, cur=None):
    """Marca como aplicados SÓ os eventos de tipo CONHECIDO desta chave.

    O ``tipo_evento IN (mapa)`` não é detalhe: sem ele, um evento de tipo
    desconhecido (``revisar=1``) seria achatado como aplicado e o sinal de
    revisão morreria calado — dentro da função escrita para não perder sinal.
    """
    tipos = _guarda_mapa()
    ph = ','.join(['%s'] * len(tipos))
    sql = (f'UPDATE nfse_eventos SET aplicado=1, orfao=0 '
           f'WHERE chave_referenciada=%s AND tipo_evento IN ({ph})')
    params = (chave_acesso, *tipos)
    if cur is not None:
        cur.execute(sql, params)
    else:
        execute_query(sql, params, fetch=False)


# ---------------------------------------------------------------------------
# Fluxos completos — os dois sentidos cobertos
# ---------------------------------------------------------------------------
def gravar_documento_completo(reg: dict) -> str:
    """Documento + aplicação dos eventos órfãos que já esperavam por ele.

    Devolve a situação resultante. Tudo numa transação: ou a nota entra com a
    situação certa, ou não entra.
    """
    chave = reg.get('chave_acesso')
    with transacao() as cur:
        salvar_documento(reg, cur=cur)
        situacao = 'ativa'
        if existem_eventos(chave):
            situacao = recalcular_situacao(chave, cur=cur)
            marcar_eventos_aplicados(chave, cur=cur)
    return situacao


def gravar_evento_completo(reg: dict) -> str | None:
    """Evento + aplicação imediata, se o documento já existir.

    Devolve a situação recalculada, ou None quando o evento ficou órfão — o que
    NÃO é erro: é o caso previsto de evento que chegou antes da nota.
    """
    chave = reg.get('chave_referenciada')
    tem_doc = existe_documento(chave)
    with transacao() as cur:
        salvar_evento(reg, cur=cur)
        if not tem_doc:
            return None
        situacao = recalcular_situacao(chave, cur=cur)
        marcar_eventos_aplicados(chave, cur=cur)
    return situacao


def aplicar_restricao(chave_acesso, restrito: bool, codigos=None, cur=None):
    """Bloqueio/desbloqueio de ofício — eixo PRÓPRIO, nunca ``situacao``.

    O ``codEvento`` tem domínio fechado de cinco valores, todos de cancelamento
    (verificado no XSD, ``TSCodigoEventoNFSe``): o município está impedindo que
    a nota seja CANCELADA. Ela segue ATIVA, VÁLIDA e vale como documento fiscal.
    Chamar isso de "bloqueada" faria o escritório tratar como problema o que é
    procedimento do município.
    """
    sql = ('UPDATE nfse_capturadas SET restricao_eventos=%s, restricao_codigos=%s, '
           'restricao_em=IF(%s, NOW(), NULL) WHERE chave_acesso=%s')
    cods = ','.join(codigos) if codigos else None
    params = (1 if restrito else 0, cods, 1 if restrito else 0, chave_acesso)
    if cur is not None:
        cur.execute(sql, params)
    else:
        execute_query(sql, params, fetch=False)


# ---------------------------------------------------------------------------
# Manutenção — o mapa é CÓDIGO, os eventos são DADOS
# ---------------------------------------------------------------------------
def sincronizar_revisar(limite=1000) -> dict:
    """Reconcilia ``revisar`` com o ``MAPA_EVENTO_SITUACAO`` atual.

    DUAS DIREÇÕES, porque a flag e o mapa divergem nos dois sentidos:
      promover  — ``revisar=1`` e o tipo AGORA está no mapa
      rebaixar  — ``revisar=0`` e o tipo NÃO está mais no mapa

    Rodar depois de QUALQUER alteração no mapa. Idempotente.

    COMMIT POR CHAVE, com rollback e continue: uma chave problemática não
    derruba o lote — mesmo espírito do isolamento por empresa na captura. E o
    progresso parcial fica preservado se estourar no meio.
    """
    tipos = _guarda_mapa()
    ph = ','.join(['%s'] * len(tipos))

    # limite+1 para DETECTAR o truncamento em vez de silenciá-lo.
    promover = execute_query(
        f'SELECT id, chave_referenciada FROM nfse_eventos '
        f'WHERE revisar = 1 AND tipo_evento IN ({ph}) LIMIT %s',
        (*tipos, limite + 1), fetch=True) or []
    rebaixar = execute_query(
        f'SELECT id, chave_referenciada FROM nfse_eventos '
        f'WHERE revisar = 0 AND tipo_evento NOT IN ({ph}) LIMIT %s',
        (*tipos, limite + 1), fetch=True) or []

    truncado = len(promover) > limite or len(rebaixar) > limite
    promover, rebaixar = promover[:limite], rebaixar[:limite]

    por_chave = {}
    for ev in promover:
        por_chave.setdefault(ev['chave_referenciada'], {'prom': [], 'reb': []})['prom'].append(ev['id'])
    for ev in rebaixar:
        por_chave.setdefault(ev['chave_referenciada'], {'prom': [], 'reb': []})['reb'].append(ev['id'])

    n_prom = n_reb = n_chaves = 0
    falhas = []
    for chave, g in por_chave.items():
        try:
            tem_doc = existe_documento(chave)
            with transacao() as cur:
                if g['prom']:
                    ph_ids = ','.join(['%s'] * len(g['prom']))
                    cur.execute(
                        f'UPDATE nfse_eventos SET revisar=0, orfao=%s, aplicado=%s '
                        f'WHERE id IN ({ph_ids})',
                        (0 if tem_doc else 1, 1 if tem_doc else 0, *g['prom']))
                    n_prom += len(g['prom'])
                if g['reb']:
                    ph_ids = ','.join(['%s'] * len(g['reb']))
                    cur.execute(
                        f'UPDATE nfse_eventos SET revisar=1, aplicado=0 '
                        f'WHERE id IN ({ph_ids})', tuple(g['reb']))
                    n_reb += len(g['reb'])
                # recalcular_situacao ignora tipo fora do mapa, então o efeito
                # do tipo removido some sozinho — sem lógica de "desfazer".
                if tem_doc:
                    recalcular_situacao(chave, cur=cur)
            n_chaves += 1
        except Exception as exc:
            logger.warning('[nfse-repo] falha ao sincronizar %s: %s', chave, exc)
            falhas.append({'chave': chave, 'erro': str(exc)[:200]})
            continue

    return {'promovidos': n_prom, 'rebaixados': n_reb,
            'chaves_recalculadas': n_chaves, 'truncado': truncado,
            'falhas': falhas}


def recalcular_lote(tipos_alterados, limite=5000) -> dict:
    """Recalcula a situação dos documentos afetados por tipos cujo MAPEAMENTO
    mudou — não cujo tipo foi adicionado.

    É o caso que ``sincronizar_revisar`` NÃO pega: se o valor de um tipo já
    conhecido for corrigido, as linhas afetadas têm ``revisar=0, aplicado=1`` e
    são invisíveis para ela. Aqui o operador informa o que mudou; não há como
    inferir.
    """
    if not tipos_alterados:
        raise MapaVazioError('recalcular_lote exige a lista de tipos alterados.')
    ph = ','.join(['%s'] * len(tipos_alterados))
    chaves = execute_query(
        f'SELECT DISTINCT chave_referenciada FROM nfse_eventos '
        f'WHERE tipo_evento IN ({ph}) LIMIT %s',
        (*tipos_alterados, limite + 1), fetch=True) or []
    truncado = len(chaves) > limite
    chaves = chaves[:limite]

    n = 0
    falhas = []
    for row in chaves:
        chave = row['chave_referenciada']
        try:
            recalcular_situacao(chave)
            n += 1
        except Exception as exc:
            falhas.append({'chave': chave, 'erro': str(exc)[:200]})
    return {'chaves_recalculadas': n, 'truncado': truncado, 'falhas': falhas,
            'mensagem': ('Limite atingido — rodar de novo até truncado=False'
                         if truncado else 'Concluído')}


def reconciliar_situacoes(limite=500) -> dict:
    """Aplica eventos órfãos cujos documentos já apareceram.

    Rede de segurança contra drift, para rodar depois do ciclo incremental.
    NÃO toca em evento de tipo desconhecido: ele permanece ``revisar=1`` até
    análise humana.
    """
    tipos = _guarda_mapa()
    ph = ','.join(['%s'] * len(tipos))
    chaves = execute_query(
        f'SELECT DISTINCT e.chave_referenciada FROM nfse_eventos e '
        f'JOIN nfse_capturadas c ON c.chave_acesso = e.chave_referenciada '
        f'WHERE e.orfao = 1 AND e.tipo_evento IN ({ph}) LIMIT %s',
        (*tipos, limite + 1), fetch=True) or []
    truncado = len(chaves) > limite
    chaves = chaves[:limite]

    n = 0
    falhas = []
    for row in chaves:
        chave = row['chave_referenciada']
        try:
            with transacao() as cur:
                recalcular_situacao(chave, cur=cur)
                marcar_eventos_aplicados(chave, cur=cur)
            n += 1
        except Exception as exc:
            falhas.append({'chave': chave, 'erro': str(exc)[:200]})
    return {'chaves': n, 'truncado': truncado, 'falhas': falhas}


def conferir_substituicoes(limite=500) -> list:
    """ALERTA: nota X declara substituir Y, mas Y não tem evento de substituição.

    NÃO corrige nada — só sinaliza divergência entre as duas fontes. Detecção de
    falha silenciosa de graça, aproveitando redundância que o leiaute já oferece:
    a mesma substituição chega pelo documento (``subst/chSubstda``) e pelo evento
    (``e105102/chSubstituta``), em sentidos opostos.

    Divergência significa evento perdido ou nota fora do ADN — os dois valem
    investigação, e nenhum vale correção automática.
    """
    return execute_query(
        "SELECT c.chave_acesso AS substituta, c.substitui_chave AS substituida, "
        "       c.empresa_id, c.competencia "
        "  FROM nfse_capturadas c "
        " WHERE c.substitui_chave IS NOT NULL "
        "   AND NOT EXISTS (SELECT 1 FROM nfse_eventos e "
        "                    WHERE e.chave_referenciada = c.substitui_chave "
        "                      AND e.tipo_evento = 'CANCELAMENTO_POR_SUBSTITUICAO') "
        " LIMIT %s", (limite,), fetch=True) or []


def metricas_saude() -> dict:
    """Os dois números do painel. SEPARADOS, porque significam coisas diferentes.

    orfaos  — evento de tipo CONHECIDO cujo documento nunca chegou. Esperado
              quando o município está fora do ADN. Crescimento persistente é
              sinal de bug no mapeamento da chave_referenciada.
    revisar — tipo NÃO mapeado. SEMPRE requer ação humana; qualquer valor > 0
              merece olhada.
    """
    r = execute_query(
        'SELECT SUM(orfao = 1 AND revisar = 0) AS orfaos, '
        '       SUM(revisar = 1) AS revisar '
        '  FROM nfse_eventos', fetch=True, fetch_one=True) or {}
    return {'orfaos': int(r.get('orfaos') or 0),
            'revisar': int(r.get('revisar') or 0)}
