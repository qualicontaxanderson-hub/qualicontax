# -*- coding: utf-8 -*-
"""Q-Robô — API de MÁQUINA para captura de SAÍDAS (NF-e 55 + NFC-e 65).

O robô .exe lê as pastas de XML do sistema do cliente e posta o XML inteiro aqui.
O robô é burro; toda a inteligência (titularidade, filtro de data, idempotência,
gravação) fica nesta nuvem. Autenticação por robo_token (Bearer), NÃO por sessão.

  POST /api/saidas         multipart, campo 'arquivo' = XML → grava a saída.
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
from utils import dropbox_sync
from utils.db_helper import execute_query
from utils.nfe_parser import parse_nfe_xml
from utils.nfe_import import _save_nfe_dual
from utils.cte_parser import parse_cte_xml
from utils.cte_import import _save_cte
from utils.integrations.dfe_captura import _mesmo_titular, _digitos
from utils.xml_seguro import fromstring_seguro, XmlInseguroError

logger = logging.getLogger(__name__)

robo_saidas = Blueprint('robo_saidas', __name__)

# Departamento do Dropbox onde as saídas são arquivadas (mesmo do fluxo Fiscal).
_DEPARTAMENTO = 'Fiscal'


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


def _arquivar_dropbox(cliente, chave, data_emissao, xml_bytes):
    """Sobe o XML da saída no Dropbox sob o EMITENTE (pasta_saidas). Best-effort: a
    linha no banco já entrou; falha aqui não derruba o request (só loga)."""
    try:
        svc = dropbox_sync._service
        numero = (cliente.get('numero_cliente') or '').strip() or None
        razao = cliente.get('nome_razao_social') or 'SEM_NOME'
        dt = data_emissao or date.today()
        pasta = svc.pasta_saidas(_DEPARTAMENTO, razao, dt, numero)
        svc.upload_bytes(f"{pasta}/{chave}.xml", xml_bytes)
    except Exception:
        logger.warning('[q-robo] saída gravada, mas falha ao arquivar XML no Dropbox '
                       '(cliente_id=%s, chave=%s)', cliente.get('id'), chave)


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


@robo_saidas.route('/api/saidas', methods=['POST'])
def receber_saida():
    # (1) token → cliente
    robo, err = _robo_do_request()
    if err:
        return err
    cliente_id = robo['cliente_id']

    # (2) todo contato marca robo_ultimo_contato (inclusive se estiver desligado).
    RoboConfig.touch_ultimo_contato(cliente_id)
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
        fromstring_seguro(xml_bytes)             # gate anti-XXE (recusa DTD/entidade)
    except XmlInseguroError as exc:
        return jsonify({'status': 'xml_invalido', 'erro': str(exc)}), 422
    xml_str = xml_bytes.decode('utf-8', 'replace')

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

    _arquivar_dropbox(cliente, dados['chave'], dados['data_emissao'], xml_bytes)

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
    cliente = Cliente.get_by_id(robo['cliente_id'])
    di = robo.get('data_inicio_captura')
    return jsonify({
        'empresa': (cliente.get('nome_razao_social') if cliente else None),
        'ativo': bool(robo['ativo']),
        'data_inicio_captura': di.isoformat() if di else None,
        'reset_seq': int(robo['robo_reset_seq'] or 0),
    }), 200
