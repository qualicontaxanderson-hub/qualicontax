# -*- coding: utf-8 -*-
"""Q-Robô — API de MÁQUINA para captura de SAÍDAS (NF-e 55 + NFC-e 65).

O robô .exe lê as pastas de XML do sistema do cliente e posta o XML inteiro aqui.
O robô é burro; toda a inteligência (titularidade, filtro de data, idempotência,
gravação) fica nesta nuvem. Autenticação por robo_token (Bearer), NÃO por sessão.

  POST /api/saidas         multipart, campo 'arquivo' = XML → grava a saída.
                           procEventoNFe (cancelamento) também entra por aqui
                           desde 26/09/2026 — ver _receber_evento_qrobo.
  GET  /api/saidas/config  → data_inicio_captura, ativo, reset_seq.

A página HUMANA de configuração (conf-saidas/q-robo) é separada; aqui é só a API.

Contrato de status (o robô reage pelo HTTP + status):
  200 salvo|duplicado|ignorado_data → robô marca ENVIADO (não reenvia).
  422 emitente_nao_confere|xml_invalido|arquivo_ausente → robô marca PULAR + loga.
  401 nao_autorizado / 403 desligado / 5xx / timeout → robô NÃO marca, tenta depois.
"""
import logging
import re
from datetime import date

from flask import Blueprint, request, jsonify

from models.robo_config import RoboConfig
from models.cliente import Cliente
from utils import dropbox_sync, qrobo_maquinas
from utils.db_helper import execute_query
from utils.nfe_parser import parse_nfe_xml
from utils.nfe_import import _save_nfe_dual
from utils.cte_parser import parse_cte_xml
from utils.cte_import import _save_cte
from utils.integrations.dfe_captura import _mesmo_titular, _digitos
from utils.xml_seguro import fromstring_seguro, XmlInseguroError

logger = logging.getLogger(__name__)

robo_saidas = Blueprint('robo_saidas', __name__)


# ---------------------------------------------------------------------------
# Auth Bearer (robo_token)
# ---------------------------------------------------------------------------
def _token_do_header():
    auth = request.headers.get('Authorization', '') or ''
    if auth.startswith('Bearer '):
        return auth[7:].strip()
    return None


def _robo_do_request():
    """Resolve o robo_config pelo Bearer. Devolve (robo, None) se ok; (None, resp)
    com a resposta 401 já pronta se o token faltar/for inválido."""
    token = _token_do_header()
    robo = RoboConfig.get_by_token(token) if token else None
    if not robo:
        return None, (jsonify({'status': 'nao_autorizado'}), 401)
    return robo, None


# ---------------------------------------------------------------------------
# Extração do cabeçalho da saída (após o gate anti-XXE)
# ---------------------------------------------------------------------------
def _cabecalho(parsed):
    """chave(44)/modelo(55|65)/emit_cnpj/data_emissao a partir do parse. Devolve
    dict ou None se não for uma NF-e/NFC-e reconhecível."""
    h = parsed.get('header') or {}
    chave = _digitos(h.get('chave_acesso') or parsed.get('chave') or '')
    if len(chave) != 44:
        return None
    modelo = chave[20:22]                       # cUF(2)AAMM(4)CNPJ(14)mod(2)...
    if modelo not in ('55', '65'):
        return None
    return {
        'chave': chave, 'modelo': modelo,
        'emit_cnpj': _digitos(h.get('emit_cnpj')),
        'data_emissao': h.get('data_emissao'),   # date | None (parser)
    }


# O `_arquivar_dropbox` que existia aqui SAIU em 12/09/2026 — ele subia o XML
# dentro da requisição e era o que travava o site (ver a nota em receber_saida).
# A mesma lógica, em lote e em paralelo, vive em utils/arquivar_saidas.py, e
# roda no cron do roteador. A convenção de pasta é a mesma:
#     EMPRESAS/{nº - razão}/FISCAL/SAIDAS/{ano}/{mês}/{chave}.xml


# ---------------------------------------------------------------------------
# POST /api/saidas
# ---------------------------------------------------------------------------
# Chave (44 díg.) só para ROTEAR o modelo (55/65 NF-e vs 57 CT-e) ANTES do parse
# específico — o parse_nfe_xml quebra num cteProc. Pega do Id do infNFe/infCte
# ("...NFe<44>"/"...CTe<44>") ou da 1ª sequência isolada de 44 dígitos.
_RE_CHAVE_ID = re.compile(r'Id\s*=\s*["\'][^"\']*?(\d{44})["\']')
_RE_CHAVE_44 = re.compile(r'(?<!\d)(\d{44})(?!\d)')


def _modelo_do_xml(xml_str):
    """Modelo (2 díg., posição 21-22 da chave) só para roteamento. '' se não achar
    (cai no fluxo NF-e, que rejeita como sempre)."""
    m = _RE_CHAVE_ID.search(xml_str) or _RE_CHAVE_44.search(xml_str)
    ch = m.group(1) if m else ''
    return ch[20:22] if len(ch) >= 22 else ''


def _receber_cte_qrobo(xml_str, robo, cliente_id):
    """CT-e (modelo 57) do Q-Robô → grava a SAÍDA do emitente (papel='emitente',
    origem='Q-ROBO'), reaproveitando o MESMO core do upload manual (_save_cte). O
    dedup por (chave, cliente_id) é do próprio _save_cte. Titularidade: o emitente
    do CT-e tem que ser o cliente do token (mesmo raiz-check da NF-e)."""
    try:
        parsed = parse_cte_xml(xml_str)
    except Exception as exc:
        return jsonify({'status': 'xml_invalido', 'erro': str(exc)}), 422
    header = parsed['header']
    chave = header.get('chave_acesso') or ''
    modelo = header.get('modelo') or (chave[20:22] if len(chave) >= 22 else '')
    if len(chave) != 44 or modelo != '57':
        return jsonify({'status': 'xml_invalido',
                        'erro': 'não é CT-e (57) com chave de 44'}), 422

    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        return jsonify({'status': 'nao_autorizado'}), 401

    # Titularidade: o EMITENTE do CT-e = o cliente do token (raiz — matriz cobre filial).
    if not _mesmo_titular(header.get('emit_cnpj'), cliente.get('cpf_cnpj')):
        return jsonify({'status': 'emitente_nao_confere'}), 422

    # Filtro de data_inicio_captura (igual à NF-e; data_emissao é date).
    di = robo.get('data_inicio_captura')
    if di and header.get('data_emissao') and header['data_emissao'] < di:
        return jsonify({'status': 'ignorado_data'}), 200

    try:
        res = _save_cte(parsed, f"{chave}.xml", 'Q-ROBO', cliente_id=cliente_id,
                        xml_raw=xml_str, papel_cliente='emitente',
                        cnpj_cliente=_digitos(header.get('emit_cnpj')))
    except Exception as exc:
        logger.exception('[q-robo] falha ao gravar CT-e chave=%s', chave)
        return jsonify({'status': 'erro', 'erro': str(exc)}), 500

    # _save_cte: 'ok' = linha nova; 'upd'/'dup' = já existia → deduplicado.
    return jsonify({'status': 'salvo' if res == 'ok' else 'duplicado',
                    'chave': chave, 'modelo': '57'}), 200


# ---------------------------------------------------------------------------
# EVENTO da NF-e/NFC-e (cancelamento) — 26/09/2026
# ---------------------------------------------------------------------------
# Medido em 26/09/2026: 3,7 milhões de NFC-e do Q-Robô e NENHUMA marcada como
# cancelada. Posto cancela NFC-e todo dia; o que faltava era o caminho. O robô
# achava o procEventoNFe "incompleto" (não termina em </nfeProc>) e esta rota
# respondia xml_invalido — a venda cancelada ficava contando como venda.
#
# A regra é a MESMA da captura SEFAZ e do fiscal_ingest, lida das MESMAS
# constantes: o evento é sempre registrado em dfe_eventos, e a nota só é marcada
# quando o retEvento traz cStat de cancelamento ACEITO (135/136/155).
#
# Diferença para o fiscal_ingest.importar_evento: lá, evento de nota que o
# sistema não tem é recusado. Aqui ele é GUARDADO — o robô varre a pasta em
# qualquer ordem, e o cancelamento pode chegar antes da nota. Quem aplica o
# guardado é _aplicar_cancelamento_guardado, chamado quando a nota entra.
_RAIZES_EVENTO = ('procEventoNFe', 'evento')


def _nome_local(el):
    tag = el.tag if isinstance(el.tag, str) else ''
    return tag.rsplit('}', 1)[-1]


def _receber_evento_qrobo(root, xml_str, robo, cliente_id):
    from utils.db_helper import transacao
    from utils.integrations.dfe_captura import (
        CSTAT_CANCELAMENTO_OK, SQL_CANCELA_NOTA, SQL_EVENTO_UPSERT,
        TP_CANCELAMENTO, _MAX_XML_EVENTO, extrair_evento)

    try:
        ev = extrair_evento(root)
    except ValueError as exc:
        return jsonify({'status': 'xml_invalido', 'erro': str(exc)}), 422

    ch = ev.get('ch_nfe')
    if not ch or ch[20:22] not in ('55', '65'):
        # Evento de CT-e (procEventoCTe) não chega aqui: a raiz é outra.
        return jsonify({'status': 'xml_invalido',
                        'erro': 'evento sem chNFe de NF-e/NFC-e'}), 422

    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        return jsonify({'status': 'nao_autorizado'}), 401

    # Titularidade pela NOTA do evento: o emitente está na própria chave
    # (posições 7-20). Mesmo casamento por raiz da saída — o robô de um posto
    # não cancela nota de outro.
    if not _mesmo_titular(ch[6:20], cliente.get('cpf_cnpj')):
        return jsonify({'status': 'emitente_nao_confere'}), 422

    nota = execute_query(
        "SELECT id FROM nfe_importacoes WHERE chave_acesso=%s AND tipo='saida' LIMIT 1",
        (ch,), fetch=True, fetch_one=True)

    # Filtro de data só quando a nota NÃO está aqui: nota de antes do início da
    # captura nunca vai entrar, e o evento dela ficaria guardado para sempre.
    # Cancelamento de NFC-e sai em até 30 min da emissão, então a data do
    # evento serve como a da nota.
    di = robo.get('data_inicio_captura')
    if not nota and di and ev.get('dh_txt') and ev['dh_txt'][:10] < di.isoformat():
        return jsonify({'status': 'ignorado_data'}), 200

    ja = execute_query(
        "SELECT id FROM dfe_eventos WHERE chave_evento=%s LIMIT 1",
        (ev['chave_evento'],), fetch=True, fetch_one=True)

    cancela = (ev['tp_evento'] == TP_CANCELAMENTO
               and ev.get('c_stat') in CSTAT_CANCELAMENTO_OK)
    cancelou = 0
    try:
        with transacao() as cur:
            cur.execute(SQL_EVENTO_UPSERT, (
                cliente_id, ev['chave_evento'], ch, ev['tp_evento'],
                ev['n_seq'], ev['descricao'], ev['dh_txt'], None, None,
                ev['org_cnpj'], None, xml_str[:_MAX_XML_EVENTO],
            ))
            if cancela and nota:
                cur.execute(SQL_CANCELA_NOTA, (ch,))
                cancelou = int(cur.rowcount or 0)
    except Exception as exc:
        logger.exception('[q-robo] falha ao gravar evento %s', ev['chave_evento'])
        return jsonify({'status': 'erro', 'erro': str(exc)}), 500

    if cancela:
        logger.info('[q-robo] cancelamento %s (cStat=%s) da nota %s: %s.',
                    ev['chave_evento'], ev.get('c_stat'), ch,
                    f'{cancelou} linha(s) marcada(s)' if nota
                    else 'nota ainda não chegou — guardado')

    # 'salvo'/'duplicado' de propósito: são os status que o robô já sabe
    # marcar como ENVIADO (runner._MARCA_ENVIADO).
    return jsonify({'status': 'duplicado' if ja else 'salvo', 'tipo': 'evento',
                    'chave': ch, 'tp_evento': ev['tp_evento'],
                    'cancelada': bool(cancela and nota),
                    'guardado': bool(cancela and not nota)}), 200


def _aplicar_cancelamento_guardado(chave):
    """A nota acabou de entrar: havia cancelamento guardado para ela?

    Uma consulta pelo índice ix_ev_chnfe por nota salva — quase sempre volta
    vazia. O cStat não tem coluna em dfe_eventos, então se relê do XML guardado:
    é o mesmo extrair_evento, a mesma guarda.
    """
    from utils.integrations.dfe_captura import (
        CSTAT_CANCELAMENTO_OK, SQL_CANCELA_NOTA, TP_CANCELAMENTO, extrair_evento)

    evs = execute_query(
        "SELECT chave_evento, xml_raw FROM dfe_eventos WHERE ch_nfe=%s AND tp_evento=%s",
        (chave, TP_CANCELAMENTO), fetch=True) or []
    for e in evs:
        try:
            ev = extrair_evento(fromstring_seguro((e['xml_raw'] or '').encode('utf-8')))
        except Exception:
            continue
        if ev.get('c_stat') in CSTAT_CANCELAMENTO_OK:
            execute_query(SQL_CANCELA_NOTA, (chave,))
            logger.info('[q-robo] nota %s chegou depois do cancelamento %s — '
                        'marcada como cancelada.', chave, e['chave_evento'])
            return True
    return False


@robo_saidas.route('/api/saidas', methods=['POST'])
def receber_saida():
    # (1) token → cliente
    robo, err = _robo_do_request()
    if err:
        return err
    cliente_id = robo['cliente_id']

    # (2) todo contato marca robo_ultimo_contato (inclusive se estiver desligado).
    RoboConfig.touch_ultimo_contato(cliente_id)
    # (2b) e o contato DESTA máquina (robô 0.4.0+; à prova de falha).
    qrobo_maquinas.registrar(cliente_id, request.headers, envio=True)
    if not robo['ativo']:
        return jsonify({'status': 'desligado'}), 403

    arq = request.files.get('arquivo')
    if arq is None:
        return jsonify({'status': 'arquivo_ausente'}), 422
    xml_bytes = arq.read()
    if not xml_bytes:
        return jsonify({'status': 'arquivo_ausente'}), 422

    # (3) leitura SEGURA (XXE off) — comum aos dois modelos.
    try:
        root = fromstring_seguro(xml_bytes)      # gate anti-XXE (recusa DTD/entidade)
    except XmlInseguroError as exc:
        return jsonify({'status': 'xml_invalido', 'erro': str(exc)}), 422
    xml_str = xml_bytes.decode('utf-8', 'replace')

    # (3a) EVENTO (cancelamento) — ANTES de rotear pelo modelo: o Id do evento
    #      ("ID110111<chave>01") engana o _modelo_do_xml.
    if _nome_local(root) in _RAIZES_EVENTO:
        return _receber_evento_qrobo(root, xml_str, robo, cliente_id)

    # (3b) CT-e (modelo 57) tem parser/gravação próprios (reaproveita a Parte 1 do
    #      upload). Roteia pelos dígitos 21-22 da chave; 55/65 seguem o fluxo abaixo,
    #      INTOCADO.
    if _modelo_do_xml(xml_str) == '57':
        return _receber_cte_qrobo(xml_str, robo, cliente_id)

    # (3c) NF-e/NFC-e: parse pleno.
    try:
        parsed = parse_nfe_xml(xml_str)          # parse pleno (já sem DTD/entidade)
    except ValueError as exc:
        return jsonify({'status': 'xml_invalido', 'erro': str(exc)}), 422

    dados = _cabecalho(parsed)
    if dados is None:
        return jsonify({'status': 'xml_invalido',
                        'erro': 'não é NF-e/NFC-e (55/65) com chave de 44'}), 422

    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        return jsonify({'status': 'nao_autorizado'}), 401

    # (4) titularidade: o emitente do XML tem que ser o cliente do token (raiz).
    if not _mesmo_titular(dados['emit_cnpj'], cliente.get('cpf_cnpj')):
        return jsonify({'status': 'emitente_nao_confere'}), 422

    # (5) filtro de data_inicio_captura (quando definida).
    di = robo.get('data_inicio_captura')
    if di and dados['data_emissao'] and dados['data_emissao'] < di:
        return jsonify({'status': 'ignorado_data'}), 200

    # (6) idempotência por (chave, tipo='saida').
    ja = execute_query(
        "SELECT id FROM nfe_importacoes WHERE chave_acesso=%s AND tipo='saida'",
        (dados['chave'],), fetch=True, fetch_one=True,
    )
    if ja:
        return jsonify({'status': 'duplicado', 'chave': dados['chave'],
                        'modelo': dados['modelo']}), 200

    # (7) grava a SAÍDA reaproveitando o core (origem='Q-ROBO'; NUNCA _importar_nfe_
    #     completa, que crava 'SEFAZ'). dest_cli=None → só a linha de saída do emit.
    try:
        _save_nfe_dual(parsed, f"{dados['chave']}.xml", 'Q-ROBO', xml_str,
                       dest_cli=None, emit_cli=cliente_id)
    except Exception as exc:
        logger.exception('[q-robo] falha ao gravar saída chave=%s', dados['chave'])
        return jsonify({'status': 'erro', 'erro': str(exc)}), 500

    # (7a) O cancelamento pode ter chegado ANTES da nota (ver _receber_evento_qrobo).
    #      Falhar aqui não desfaz a nota já gravada — só loga.
    try:
        _aplicar_cancelamento_guardado(dados['chave'])
    except Exception:
        logger.exception('[q-robo] falha ao aplicar cancelamento guardado da nota %s',
                         dados['chave'])

    # (7b) O ARQUIVAMENTO NO DROPBOX SAIU DAQUI em 12/09/2026, e não é detalhe.
    #
    # Este upload levava 0,7 a 0,9 segundo de espera de rede DENTRO da
    # requisição. Com o Q-Robô mandando ~60 notas por minuto, eram 48 segundos
    # de thread do gunicorn por minuto gastos esperando o Dropbox — uma thread
    # inteira, em tempo integral — e o site ia a 6-8 segundos para servir um
    # CSS, com a CPU do contêiner em 0,0 vCPU. Não era falta de máquina: era
    # espera. E eram 14 robôs, com 200 a instalar.
    #
    # Nada se perde: `xml_raw` já guardou o XML inteiro no banco. O Dropbox é a
    # segunda cópia, e quem a faz agora é utils/arquivar_saidas.py, chamado
    # pelo cron do roteador a cada 5 min — fora do site, e em paralelo.
    #
    # (8) ok
    return jsonify({'status': 'salvo', 'chave': dados['chave'],
                    'modelo': dados['modelo']}), 200


# ---------------------------------------------------------------------------
# GET /api/saidas/config
# ---------------------------------------------------------------------------
@robo_saidas.route('/api/saidas/config', methods=['GET'])
def config_saida():
    robo, err = _robo_do_request()
    if err:
        return err
    RoboConfig.touch_ultimo_contato(robo['cliente_id'])
    qrobo_maquinas.registrar(robo['cliente_id'], request.headers)
    cliente = Cliente.get_by_id(robo['cliente_id'])
    di = robo.get('data_inicio_captura')

    # O `ativo` que vai na resposta é o EFETIVO: o botão do cadastro E a fase E
    # a janela da noite. É aqui que a política mora, e não na recusa por nota —
    # o agente lê este campo ANTES de varrer a máquina (runner.py, passo 1) e
    # aborta o ciclo inteiro quando vem false. Recusar nota por nota faria ele
    # despejar as milhares e levar recusa em cada uma.
    #
    # O campo `ativo` do BANCO continua sendo só a vontade do Anderson: não é
    # sobrescrito. Quem some é a distinção na resposta, e por isso `fase` e
    # `motivo` vão junto — para o painel dizer "ligado, em retroativo, volta às
    # 22:00" em vez de "desligado", que seria mentira.
    from utils.robo_fase import situacao
    try:
        sit = situacao(robo['cliente_id'], ativo_cadastro=bool(robo['ativo']))
    except Exception:
        # Falha ao decidir a fase NÃO pode calar o robô: na dúvida ele manda,
        # que é o comportamento de antes desta mudança.
        logger.exception('[q-robo] falha ao calcular a fase (cliente_id=%s) — '
                         'liberando como antes.', robo['cliente_id'])
        sit = {'fase': 'indefinida', 'liberado': bool(robo['ativo']),
               'motivo': 'não consegui calcular a fase; liberado por garantia'}

    return jsonify({
        'empresa': (cliente.get('nome_razao_social') if cliente else None),
        'ativo': bool(sit['liberado']),
        'ativo_cadastro': bool(robo['ativo']),
        'fase': sit['fase'],
        'motivo': sit['motivo'],
        'data_inicio_captura': di.isoformat() if di else None,
        'reset_seq': int(robo['robo_reset_seq'] or 0),
    }), 200
