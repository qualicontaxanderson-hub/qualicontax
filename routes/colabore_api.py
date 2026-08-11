# -*- coding: utf-8 -*-
"""Q-Colabore F2 — API de MÁQUINA: o agente do funcionário posta arquivos.

Espelha routes/robo_saidas.py (o Q-Robô), mas a unidade é o FUNCIONÁRIO, não o
posto, e o destino é a caixa _ENTRADA do Dropbox (a mesma porta plana da F1).
Autenticação por CHAVE (Bearer), NÃO por sessão. O agente é burro; toda a
inteligência (validar, não sobrescrever, arquivar, logar) fica nesta nuvem.

  POST /api/colabore/enviar   multipart, campo 'arquivo' -> grava em _ENTRADA.
  GET  /api/colabore/config   -> data_inicio_captura, ativo, limite, extensões.

CONTRATO DE STATUS (o agente reage pelo HTTP):
  200 recebido      -> gravado; devolve o NOME FINAL (pode ter ganho sufixo).
  409 ja_existe     -> já havia arquivo idêntico (mesmo nome E mesmo tamanho).
  413 muito_grande  -> acima do limite; o agente não reenvia igual.
  415 ext_negada    -> extensão fora da lista; o agente pula.
  401 nao_autorizado / 403 revogada -> chave inválida/desligada; agente não insiste.
  5xx               -> falha temporária (Dropbox/servidor); o agente tenta depois.

SEGREDO: a chave nunca aparece em log, nem em claro, nem parcial além do prefixo
(a auth casa por hash — ver models/colabore_config). O NOME do arquivo NUNCA vai
para o log de auditoria (nomes de .pfx carregam a senha): loga-se só ação, módulo,
extensão e tamanho.
"""
import logging
import re
import unicodedata

from flask import Blueprint, request, jsonify

from models.colabore_config import ColaboreConfig
from utils import dropbox_sync
from utils.atividade import registrar_agente

logger = logging.getLogger(__name__)

colabore_api = Blueprint('colabore_api', __name__)


# ===========================================================================
# LIMITES E SEGURANÇA — um só lugar, fácil de mudar (item 4).
# ===========================================================================
# Tamanho máximo por arquivo. Começa em 200 MB. Mudar AQUI muda a API e o
# /config que o agente lê — não há segundo lugar.
TAMANHO_MAX_BYTES = 200 * 1024 * 1024            # 200 MB

# Extensões que a caixa aceita — lista EXPLÍCITA (nada de "qualquer coisa").
#   .xml  documentos fiscais            .pdf  DANFE/boletos/contratos
#   .pfx  certificado digital A1        .txt  layouts/retornos texto
#   .ofx  extrato bancário              .zip  lotes compactados
#   .dec/.rec  arquivos da contabilidade/declarações
EXTENSOES_PERMITIDAS = ('.xml', '.pdf', '.pfx', '.txt', '.ofx', '.zip', '.dec', '.rec')

# Caracteres que o Dropbox recusa em nome de arquivo (/ \ : ? * " < > |) + controle.
_CHARS_PROIBIDOS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_NOME_MAX = 200                                  # teto defensivo do nome final


# ===========================================================================
# Auth Bearer (a chave do funcionário)
# ===========================================================================
def _token_do_header():
    auth = request.headers.get('Authorization', '') or ''
    if auth.startswith('Bearer '):
        return auth[7:].strip()
    return None


def _colabore_do_request():
    """Resolve a chave -> config do funcionário. (cfg, None) se ok; (None, resp)
    com o 401 pronto se a chave faltar/for inválida."""
    token = _token_do_header()
    cfg = ColaboreConfig.get_by_token(token) if token else None
    if not cfg:
        return None, (jsonify({'status': 'nao_autorizado'}), 401)
    return cfg, None


# ===========================================================================
# Nome do arquivo — saneamento (sem barra, sem '..', sem char que o Dropbox recuse)
# ===========================================================================
def _sanitizar_nome(nome):
    """Devolve um nome de arquivo SEGURO e plano (fica na própria _ENTRADA), ou ''.

    - fica só com o basename (descarta qualquer caminho: 'a/b/c.xml' -> 'c.xml');
    - remove '..' e caracteres que o Dropbox recusa (troca por '_');
    - tira controle/UNC, apara pontos e espaços das pontas (o Dropbox recusa
      nome terminado em '.' ou espaço), e limita o tamanho preservando a extensão.
    '' significa "nome imprestável" -> o caller responde 415/400."""
    nome = (nome or '').strip()
    # normaliza acentuação exótica que às vezes chega decomposta do agente
    nome = unicodedata.normalize('NFC', nome)
    # basename à prova de barra dos dois mundos
    nome = nome.replace('\\', '/').split('/')[-1]
    nome = nome.replace('..', '_')
    nome = _CHARS_PROIBIDOS.sub('_', nome)
    nome = nome.strip(' .')                       # Dropbox recusa ponta '.'/' '
    if nome in ('', '.', '..'):
        return ''
    if len(nome) > _NOME_MAX:
        if '.' in nome:
            base, ext = nome.rsplit('.', 1)
            nome = base[:_NOME_MAX - len(ext) - 1] + '.' + ext
        else:
            nome = nome[:_NOME_MAX]
    return nome


def _extensao(nome):
    """'.xml' minúsculo (com o ponto), ou '' se não houver extensão."""
    return ('.' + nome.rsplit('.', 1)[-1].lower()) if '.' in nome else ''


def _nome_livre(svc, base_dir, nome):
    """Nome que NÃO colide em base_dir. Se 'x.pfx' existe, tenta 'x (1).pfx',
    'x (2).pfx'... Devolve o primeiro livre. Best-effort com _path_exists (o
    upload é overwrite, então garantir o nome livre aqui é o que evita perder o
    arquivo de alguém)."""
    if not svc._path_exists(f"{base_dir}/{nome}"):
        return nome
    if '.' in nome:
        raiz, ext = nome.rsplit('.', 1)
        sufixo = lambda i: f"{raiz} ({i}).{ext}"
    else:
        sufixo = lambda i: f"{nome} ({i})"
    for i in range(1, 1000):
        cand = sufixo(i)
        if not svc._path_exists(f"{base_dir}/{cand}"):
            return cand
    return None                                   # 999 homônimos: desiste (5xx)


# ===========================================================================
# POST /api/colabore/enviar
# ===========================================================================
@colabore_api.route('/api/colabore/enviar', methods=['POST'])
def enviar():
    # (1) chave -> funcionário
    cfg, err = _colabore_do_request()
    if err:
        return err

    # (2) todo contato marca ultimo_contato (mesmo se estiver revogada).
    ColaboreConfig.touch_ultimo_contato(cfg['usuario_id'])
    if not cfg['ativo']:
        return jsonify({'status': 'revogada'}), 403

    arq = request.files.get('arquivo')
    if arq is None or not (arq.filename or '').strip():
        return jsonify({'status': 'arquivo_ausente'}), 422

    nome = _sanitizar_nome(arq.filename)
    ext = _extensao(nome)
    if not nome or ext not in EXTENSOES_PERMITIDAS:
        return jsonify({'status': 'ext_negada',
                        'extensoes': list(EXTENSOES_PERMITIDAS)}), 415

    # (3) tamanho — lê o conteúdo e corta no limite (uma só fonte: TAMANHO_MAX_BYTES).
    conteudo = arq.read()
    tamanho = len(conteudo)
    if tamanho == 0:
        return jsonify({'status': 'arquivo_ausente'}), 422
    if tamanho > TAMANHO_MAX_BYTES:
        return jsonify({'status': 'muito_grande',
                        'limite_bytes': TAMANHO_MAX_BYTES}), 413

    # (4) Dropbox — a caixa _ENTRADA (mesma porta plana da F1).
    svc = dropbox_sync._service
    if not svc.is_configured():
        # Sem credencial NÃO há onde gravar: 5xx para o agente tentar de novo.
        logger.error('[q-colabore] Dropbox nao configurado — envio recusado (5xx).')
        return jsonify({'status': 'indisponivel'}), 503

    base_dir = svc.pasta_cert_novo()              # {ROOT}/_ENTRADA
    try:
        # (4a) mesmo nome + mesmo tamanho já lá = idêntico -> 409 (não regrava).
        meta = svc.file_metadata(f"{base_dir}/{nome}")
        if meta and int(meta.get('size') or -1) == tamanho:
            return jsonify({'status': 'ja_existe', 'nome': nome}), 409
        # (4b) mesmo nome, conteúdo diferente: NÃO sobrescreve — acha nome livre.
        nome_final = _nome_livre(svc, base_dir, nome)
        if not nome_final:
            return jsonify({'status': 'erro'}), 500
        if not svc.upload_bytes(f"{base_dir}/{nome_final}", conteudo):
            return jsonify({'status': 'erro'}), 500
    except dropbox_sync.DropboxAuthError:
        logger.error('[q-colabore] credenciais Dropbox invalidas no envio.')
        return jsonify({'status': 'indisponivel'}), 503
    except dropbox_sync.DropboxError as exc:
        logger.warning('[q-colabore] erro Dropbox no envio: %s', str(exc)[:160])
        return jsonify({'status': 'erro'}), 500
    except Exception:
        logger.exception('[q-colabore] falha inesperada ao gravar envio.')
        return jsonify({'status': 'erro'}), 500

    # (5) AUDITORIA — atribuída ao FUNCIONÁRIO dono da chave. SEM o nome do
    #     arquivo (nomes de .pfx trazem a senha): só extensão + tamanho.
    registrar_agente('escrita.enviou_arquivo', 'colabore', cfg['usuario_id'],
                     cfg.get('usuario_nome'), cfg.get('usuario_login'),
                     tabela=None,
                     depois={'ext': ext, 'tamanho_bytes': tamanho,
                             'renomeado': nome_final != nome})

    # (6) ok — devolve o nome FINAL gravado (pode ter ganho sufixo).
    return jsonify({'status': 'recebido', 'nome': nome_final}), 200


# ===========================================================================
# GET /api/colabore/config
# ===========================================================================
@colabore_api.route('/api/colabore/config', methods=['GET'])
def config():
    cfg, err = _colabore_do_request()
    if err:
        return err
    ColaboreConfig.touch_ultimo_contato(cfg['usuario_id'])
    di = cfg.get('data_inicio_captura')
    return jsonify({
        # 'funcionario': dono da chave — o agente mostra no "Testar conexão" para a
        # pessoa confirmar que é a chave certa (como o Q-Robô mostra a razão social).
        'funcionario': cfg.get('usuario_nome'),
        'ativo': bool(cfg['ativo']),
        'data_inicio_captura': di.isoformat() if di else None,
        'tamanho_max_bytes': TAMANHO_MAX_BYTES,
        'extensoes_permitidas': list(EXTENSOES_PERMITIDAS),
    }), 200
