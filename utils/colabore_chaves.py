# -*- coding: utf-8 -*-
"""Q-Colabore F2 — serviço de chaves POR FUNCIONÁRIO (o agente da máquina dele).

É o irmão de ``utils/qrobo_chaves.py``, mas para pessoas em vez de postos: cada
funcionário instala um agente que posta arquivos em ``/api/colabore/enviar``
autenticado por uma chave própria. Este módulo gera/regenera/revoga essa chave.
Nenhuma rota aqui — só serviço; quem chama (a tela de Usuários) mostra a chave.

REAPROVEITA o núcleo do Q-Robô (não reescreve): ``TOKEN_BYTES``/``PREFIXO_LEN``
(o tamanho do segredo e do prefixo) e ``_normaliza_data_inicio`` (a data de corte,
recusando futuro). O ``secrets.token_hex`` é o mesmo gerador.

UMA DIFERENÇA DELIBERADA: a chave NUNCA vai em claro para o banco. O Q-Robô grava
``robo_token`` em claro; aqui gravamos só o **SHA-256** (``token_hash``) e o
**prefixo** (8 chars). O segredo em claro existe só no retorno de ``gerar_chave`` —
a tela o mostra UMA vez e ele some. Na autenticação, hasheia-se o Bearer recebido
e compara-se com ``token_hash`` (a chave em claro nunca é comparada nem guardada).

``data_inicio_captura`` é a data de CORTE: o agente não manda arquivo anterior a
ela. NUNCA fica NULL na prática (padrão = hoje).
"""
import hashlib
import secrets
from datetime import date

from utils.db_helper import execute_query, transacao
# Reaproveita o núcleo do Q-Robô — mesmo tamanho de segredo/prefixo e a mesma
# régua de data de corte (recusa data futura pelo relógio do banco, em BRT).
from utils.qrobo_chaves import (TOKEN_BYTES, PREFIXO_LEN, TENTATIVAS_TOKEN,
                                _normaliza_data_inicio, contexto_request)


def _hash_token(token):
    """SHA-256 hex (64 chars) do segredo. É o que — e SÓ o que — vai ao banco."""
    return hashlib.sha256((token or '').encode('utf-8')).hexdigest()


def _token_unico():
    """Segredo novo (claro) cujo HASH está livre em colabore_config.

    Colisão em 256 bits é impossível na prática; a checagem existe só para um
    acaso virar nova tentativa em vez de estourar o UNIQUE de token_hash no meio
    da transação. Devolve (token_claro, token_hash)."""
    for _ in range(TENTATIVAS_TOKEN):
        token = secrets.token_hex(TOKEN_BYTES)
        h = _hash_token(token)
        ja = execute_query("SELECT 1 AS x FROM colabore_config WHERE token_hash = %s",
                           (h,), fetch=True, fetch_one=True)
        if not ja:
            return token, h
    raise RuntimeError('Nao foi possivel gerar uma chave unica do Q-Colabore.')


def estado_colabore(usuario_id):
    """Estado da chave do funcionário (ou None). NUNCA devolve o segredo/hash —
    só o que a tela mostra: prefixo, versão, ativo, data de corte, último contato."""
    return execute_query(
        "SELECT c.usuario_id, c.ativo, c.versao, c.data_inicio_captura, "
        "       c.token_prefixo, c.ultimo_contato, c.criado_em, "
        "       c.token_gerado_em, c.token_gerado_por, "
        "       u.nome AS token_gerado_por_nome, "
        "       (c.token_hash IS NOT NULL AND c.token_hash <> '') AS tem_chave, "
        "       TIMESTAMPDIFF(MINUTE, c.ultimo_contato, NOW()) AS min_sem_contato "
        "  FROM colabore_config c "
        "  LEFT JOIN usuarios u ON u.id = c.token_gerado_por "
        " WHERE c.usuario_id = %s",
        (usuario_id,), fetch=True, fetch_one=True,
    )


def estado_por_usuario():
    """{usuario_id: estado} de TODOS os funcionários que já têm chave — uma query
    para a lista de Usuários pintar o prefixo/versão/ativo por linha."""
    linhas = execute_query(
        "SELECT usuario_id, ativo, versao, token_prefixo, data_inicio_captura, "
        "       ultimo_contato, "
        "       (token_hash IS NOT NULL AND token_hash <> '') AS tem_chave "
        "  FROM colabore_config",
        fetch=True) or []
    return {r['usuario_id']: r for r in linhas}


def gerar_chave(usuario_id, admin_id, *, regerar=False, data_inicio=None,
                origem=None, ip=None, user_agent=None):
    """Gera (ou regenera) a chave do funcionário e devolve o segredo UMA vez.

    ``regerar=False`` recusa se já existir chave (a tela precisa avisar que a
    anterior será invalidada e pedir a 2ª confirmação). Regenerar ROTACIONA a
    versão e RE-ATIVA (o funcionário volta a poder enviar). O segredo em claro
    só existe no retorno; no banco fica apenas o hash + prefixo.

    Devolve dict:
      ok=True  -> {'token': <segredo, mostrado UMA vez>, 'acao': 'gerada'|'regerada',
                   'prefixo', 'versao', 'data_inicio_captura'}
      ok=False -> {'erro': 'usuario_inexistente'|'ja_existe'|'data_invalida'|
                           'data_futura', ...}
    """
    usuario = execute_query(
        "SELECT id, nome, login FROM usuarios WHERE id = %s",
        (usuario_id,), fetch=True, fetch_one=True)
    if not usuario:
        return {'ok': False, 'erro': 'usuario_inexistente', 'usuario_id': usuario_id}

    try:
        data_txt = _normaliza_data_inicio(data_inicio)
    except ValueError as exc:
        return {'ok': False, 'erro': str(exc), 'valor': data_inicio}

    # Recusa data futura pelo relógio do BANCO (BRT) — mesma trava do Q-Robô: o
    # relógio do processo é UTC no Railway e de madrugada deixaria passar um
    # "amanhã" que ainda é futuro em BRT.
    if data_txt:
        futura = execute_query("SELECT (%s > CURDATE()) AS futura", (data_txt,),
                               fetch=True, fetch_one=True) or {}
        if futura.get('futura'):
            return {'ok': False, 'erro': 'data_futura', 'valor': data_txt}

    atual = estado_colabore(usuario_id)
    if atual and atual.get('tem_chave') and not regerar:
        return {'ok': False, 'erro': 'ja_existe', 'colabore': atual}

    token, token_hash = _token_unico()
    prefixo = token[:PREFIXO_LEN]
    acao = 'regerada' if atual else 'gerada'

    with transacao() as cur:
        if atual:
            # COALESCE: data informada > data que já estava > hoje. Regenerar
            # rotaciona a versão e RE-ATIVA (ativo=1).
            cur.execute(
                "UPDATE colabore_config "
                "   SET token_hash = %s, token_prefixo = %s, ativo = 1, "
                "       versao = COALESCE(versao, 0) + 1, "
                "       token_gerado_em = NOW(), token_gerado_por = %s, "
                "       data_inicio_captura = COALESCE(%s, data_inicio_captura, CURDATE()) "
                " WHERE usuario_id = %s",
                (token_hash, prefixo, admin_id, data_txt, usuario_id))
        else:
            cur.execute(
                "INSERT INTO colabore_config "
                "  (usuario_id, token_hash, token_prefixo, versao, ativo, "
                "   token_gerado_em, token_gerado_por, data_inicio_captura) "
                "VALUES (%s, %s, %s, 1, 1, NOW(), %s, COALESCE(%s, CURDATE()))",
                (usuario_id, token_hash, prefixo, admin_id, data_txt))

        cur.execute(
            "SELECT versao, ativo, data_inicio_captura "
            "  FROM colabore_config WHERE usuario_id = %s", (usuario_id,))
        gravado = cur.fetchone()

    return {'ok': True, 'token': token, 'acao': acao, 'prefixo': prefixo,
            'versao': int(gravado['versao'] or 1),
            'ativo': bool(gravado['ativo']),
            'data_inicio_captura': gravado['data_inicio_captura']}


def definir_corte(usuario_id, data_inicio):
    """Muda SÓ a data de corte do funcionário. NÃO toca na chave.

    Por que existe separado de ``gerar_chave``
    ------------------------------------------
    A data de corte só era editável no formulário que GERA a chave — e gerar
    chave invalida a anterior na hora, derrubando o agente que já está
    instalado. Na prática isso tornava a data imutável: ninguém vai derrubar o
    agente do funcionário só para dizer "quero desde 01/08".

    E é justamente o contrário do desenho: o agente relê o corte a cada ciclo
    (``GET /api/colabore/config``), então mudar aqui já vale no ciclo seguinte,
    sem ninguém tocar na máquina de ninguém.

    Devolve ``{'ok': True, 'data_inicio_captura': date}`` ou ``{'ok': False,
    'erro': ...}``. Não cria linha: se o funcionário ainda não tem chave, não há
    corte a definir — gere a chave primeiro.
    """
    if data_inicio is None:
        return {'ok': False, 'erro': 'data_ausente'}
    r = execute_query(
        "UPDATE colabore_config SET data_inicio_captura = %s WHERE usuario_id = %s",
        (data_inicio, usuario_id), fetch=False)
    if r is None:
        return {'ok': False, 'erro': 'falha_banco'}
    atual = execute_query(
        "SELECT data_inicio_captura FROM colabore_config WHERE usuario_id = %s",
        (usuario_id,), fetch=True, fetch_one=True)
    if not atual:
        return {'ok': False, 'erro': 'sem_chave'}
    return {'ok': True, 'data_inicio_captura': atual['data_inicio_captura']}


def revogar_chave(usuario_id):
    """Desliga a chave do funcionário (ativo=0). A linha e o prefixo continuam
    (histórico); o agente passa a receber 403. Devolve dict ok=True/False."""
    r = execute_query(
        "UPDATE colabore_config SET ativo = 0 WHERE usuario_id = %s AND ativo = 1",
        (usuario_id,), fetch=False)
    if r is None:
        return {'ok': False, 'erro': 'falha_banco'}
    return {'ok': True}
