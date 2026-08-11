# -*- coding: utf-8 -*-
"""FRENTE D2 — ponto ÚNICO de registro de atividade (auditoria).

Uma função pública: ``registrar(...)``. Ela pega o usuário da sessão sozinha,
aplica a lista negra, calcula o diff (só os campos que mudaram) e grava em
``logs_sistema``. NUNCA estoura exceção para fora: se o log falhar, a operação
do usuário não pode quebrar por causa disso (try/except engolindo + print).

Chamada EXPLÍCITA nas rotas — nada de decorator mágico.

CONVENÇÃO DO CAMPO ``acao`` (separar ESCRITA de LEITURA — item 6):
    escrita.<verbo>_<entidade>   ex.: escrita.criou_cliente, escrita.vinculou_produto
    leitura.<verbo>_<entidade>   ex.: leitura.abriu_ficha_cliente, leitura.buscou_entradas
A auditoria de "quem errou" filtra por  acao LIKE 'escrita.%'.

MÓDULOS: fiscal | cadastros | contabil | dp | colabore

RETENÇÃO (item 8): as linhas de ESCRITA ficam PARA SEMPRE (são a auditoria). As
linhas de LEITURA poderão ser limpas após 180 dias — mas NÃO há rotina automática
de limpeza, e nenhum DELETE deve rodar sem autorização explícita do Anderson.
Se um dia existir, seria algo como:
    DELETE FROM logs_sistema WHERE acao LIKE 'leitura.%' AND data_hora < NOW()-INTERVAL 180 DAY
"""
import json
import logging

from flask import has_request_context, request
from flask_login import current_user

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# LISTA NEGRA — nomes de coluna cujo VALOR nunca entra no log, nem no antes nem
# no depois. Se o campo mudar, grava-se só {campo: "alterado"} (o nome do campo
# e a palavra "alterado"), nunca o valor. Filtro por NOME EXATO de coluna, lista
# explícita — não por adivinhação. ESTE é o único lugar; adicionar campos
# sensíveis novos AQUI no futuro.
# --------------------------------------------------------------------------
CAMPOS_SENSIVEIS = {
    # credenciais e tokens
    'senha', 'senha_hash', 'token', 'link_token', 'hash',
    # chaves / pix
    'chave', 'chave_pix', 'pix',
    # dados bancários
    'banco', 'agencia', 'conta', 'conta_corrente', 'conta_bancaria',
    # certificado (.pfx): senha, conteúdo e caminho — NADA de dentro do .pfx
    'senha_cifrada', 'senha_certificado', 'senha_pfx',
    'conteudo', 'conteudo_certificado', 'pfx', 'arquivo_pfx',
    'dropbox_path', 'caminho_certificado',
}

# Campos de CONTROLE/autoria: NUNCA entram no antes/depois. Quem fez já é o dono
# da linha de log (usuario_id/nome/login) — repetir criado_por/alterado_por no
# corpo seria ruído. Removidos ANTES do diff, dos dois lados.
CAMPOS_CONTROLE = {'criado_por', 'criado_em', 'alterado_por', 'alterado_em',
                   'atualizado_em', 'data_criacao'}

_MASCARA = 'alterado'   # o que grava no lugar do valor sensível


def _sem_controle(d):
    """Remove os campos de controle/autoria do dict (dos dois lados do diff)."""
    if not d:
        return d
    return {k: v for k, v in d.items() if k not in CAMPOS_CONTROLE}


def _mascarar(d):
    """Troca o VALOR de todo campo sensível pela palavra 'alterado'."""
    if not d:
        return d
    return {k: (_MASCARA if k in CAMPOS_SENSIVEIS else v) for k, v in d.items()}


def _diff(antes, depois):
    """Só os campos que MUDARAM. Retorna (antes_mudou, depois_mudou) já mascarados.

    - edição  (antes e depois dicts): compara chave a chave, mantém só diferenças.
    - criação (só depois): grava o depois inteiro.
    - exclusão(só antes):  grava o antes inteiro.
    - leitura (nenhum dos dois ou depois=contexto de busca): grava o que vier.
    Campos de controle (criado_por/alterado_por/...) saem sempre, dos dois lados.
    """
    antes = _sem_controle(antes)
    depois = _sem_controle(depois)
    if antes and depois:
        chaves = set(depois) | set(antes)
        mud = [k for k in chaves if antes.get(k) != depois.get(k)]
        a = {k: antes.get(k) for k in mud}
        d = {k: depois.get(k) for k in mud}
        return _mascarar(a), _mascarar(d)
    return _mascarar(antes), _mascarar(depois)


def rotulo_empresa(cliente_id=None, grupo_id=None):
    """Resolve NÚMERO e NOME da empresa (e nome do grupo) para o log se explicar
    sozinho — sem obrigar a cruzar com a tabela de clientes na tela de auditoria.

    Regra da casa: empresa se identifica pelo NÚMERO do cliente, nunca pelo id
    interno. Query LEVE por id e SÓ quando o id vem preenchido; chamar apenas no
    caminho que já vai gravar log (não adicionar consulta onde hoje não há).
    Nunca estoura: em erro devolve o que tiver.
    """
    out = {}
    try:
        if cliente_id:
            r = execute_query(
                "SELECT numero_cliente, nome_razao_social FROM clientes WHERE id = %s",
                (cliente_id,), fetch=True, fetch_one=True)
            if r:
                out['cliente_numero'] = r.get('numero_cliente')
                out['cliente_nome'] = r.get('nome_razao_social')
        if grupo_id:
            r = execute_query("SELECT nome FROM grupos_clientes WHERE id = %s",
                              (grupo_id,), fetch=True, fetch_one=True)
            if r:
                out['grupo_nome'] = r.get('nome')
    except Exception as e:
        try:
            print(f"[atividade] rotulo_empresa falhou: {e}")
        except Exception:
            pass
    return out


def _json(d):
    """Serializa para a coluna JSON. default=str cobre date/Decimal/etc."""
    if d is None:
        return None
    try:
        return json.dumps(d, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({'_erro_serializacao': True})


def registrar(acao, modulo, tabela=None, registro_id=None, antes=None, depois=None):
    """Grava uma linha de atividade em logs_sistema. NUNCA estoura para fora.

    Args:
        acao: 'escrita.*' ou 'leitura.*' (ver convenção no topo do módulo).
        modulo: fiscal | cadastros | contabil | dp | colabore.
        tabela: tabela afetada (opcional).
        registro_id: id do registro afetado (opcional).
        antes/depois: dicts. Só os campos MUDADOS vão para o log; campos da lista
                      negra entram como {campo: "alterado"}.
    """
    try:
        # Só grava o que uma PESSOA LOGADA pediu. Ação de robô/scheduler (sem
        # request ou sem usuário autenticado) não gera log — item 7.
        if not has_request_context():
            return
        if not getattr(current_user, 'is_authenticated', False):
            return
        usuario_id = getattr(current_user, 'id', None)
        # Nome e login COPIADOS no momento da ação: a auditoria não pode depender
        # da FK usuario_id (ON DELETE SET NULL). Apagar o usuário zera o id, mas
        # estes dois continuam dizendo quem fez.
        usuario_nome = (getattr(current_user, 'nome', None) or '')[:120] or None
        usuario_login = (getattr(current_user, 'login', None)
                         or getattr(current_user, 'nick', None) or '')[:80] or None

        a_json, d_json = _diff(antes, depois)
        execute_query(
            "INSERT INTO logs_sistema "
            "(usuario_id, usuario_nome, usuario_login, acao, modulo, tabela_afetada, "
            " registro_id, dados_anteriores, dados_novos, ip_address, user_agent) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (usuario_id, usuario_nome, usuario_login,
             acao[:100], (modulo or '')[:20], tabela, registro_id,
             _json(a_json), _json(d_json),
             (request.remote_addr or '')[:45],
             (request.headers.get('User-Agent') or '')[:255]),
            fetch=False)
    except Exception as e:                       # engole tudo — nunca quebra o fluxo
        try:
            print(f"[atividade] falha ao registrar '{acao}': {e}")
            logger.warning("atividade.registrar falhou (%s): %s", acao, e)
        except Exception:
            pass


def registrar_agente(acao, modulo, usuario_id, usuario_nome, usuario_login=None,
                     tabela=None, registro_id=None, depois=None):
    """Igual a ``registrar``, mas para um ATOR DE MÁQUINA que se identifica sozinho.

    O ``registrar`` normal pega o autor da SESSÃO e some quando não há usuário
    logado (item 7) — de propósito. Mas o agente do Q-Colabore posta arquivos
    autenticado por chave (Bearer), sem sessão, E o ato TEM um dono conhecido: o
    funcionário cuja chave foi usada. Este atalho grava a mesma linha em
    logs_sistema atribuindo-a a essa pessoa. Mesmas garantias: NUNCA estoura,
    aplica a lista negra, e — como o caller já monta o ``depois`` — a regra de
    "nunca o NOME do arquivo" fica com quem chama (aqui só se mascara valor de
    campo sensível por nome de coluna).
    """
    try:
        # request pode ou não existir (a API roda em request; um teste pode não).
        try:
            in_req = has_request_context()
        except Exception:
            in_req = False
        ip = (request.remote_addr or '')[:45] if in_req else None
        ua = (request.headers.get('User-Agent') or '')[:255] if in_req else None

        _antes, d_json = _diff(None, depois)
        execute_query(
            "INSERT INTO logs_sistema "
            "(usuario_id, usuario_nome, usuario_login, acao, modulo, tabela_afetada, "
            " registro_id, dados_anteriores, dados_novos, ip_address, user_agent) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (usuario_id, (usuario_nome or '')[:120] or None,
             (usuario_login or '')[:80] or None,
             acao[:100], (modulo or '')[:20], tabela, registro_id,
             None, _json(d_json), ip, ua),
            fetch=False)
    except Exception as e:                       # engole tudo — nunca quebra o fluxo
        try:
            print(f"[atividade] falha ao registrar_agente '{acao}': {e}")
            logger.warning("atividade.registrar_agente falhou (%s): %s", acao, e)
        except Exception:
            pass
