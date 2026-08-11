# -*- coding: utf-8 -*-
"""Config por FUNCIONÁRIO do Q-Colabore (agente na máquina da pessoa).

Tabela colabore_config (1:1 por usuario_id) — criada por
migrations/add_colabore_config.py. Espelha models/robo_config.py, mas o segredo
NUNCA fica em claro: guarda-se só ``token_hash`` (SHA-256) e ``token_prefixo``.
A autenticação da API hasheia o Bearer recebido e compara com ``token_hash``.
"""
import hashlib

from utils.db_helper import execute_query


class ColaboreConfig:

    @staticmethod
    def _hash(token):
        """SHA-256 hex do segredo. Idêntico ao de utils/colabore_chaves."""
        return hashlib.sha256((token or '').encode('utf-8')).hexdigest()

    @staticmethod
    def get_by_token(token):
        """Linha do colabore_config pelo Bearer em claro (auth da API), ou None.

        Hasheia o token e casa pelo token_hash — a chave em claro nunca toca o
        banco. Token vazio nunca casa. Traz nome/login do funcionário para a
        auditoria da API atribuir o ato à pessoa dona da chave."""
        if not token:
            return None
        return execute_query(
            "SELECT c.id, c.usuario_id, c.ativo, c.versao, c.data_inicio_captura, "
            "       c.token_prefixo, c.ultimo_contato, "
            "       u.nome AS usuario_nome, u.login AS usuario_login "
            "  FROM colabore_config c "
            "  LEFT JOIN usuarios u ON u.id = c.usuario_id "
            " WHERE c.token_hash = %s",
            (ColaboreConfig._hash(token),), fetch=True, fetch_one=True,
        )

    @staticmethod
    def get_by_usuario(usuario_id):
        return execute_query(
            "SELECT id, usuario_id, ativo, versao, data_inicio_captura, "
            "       token_prefixo, ultimo_contato "
            "  FROM colabore_config WHERE usuario_id = %s",
            (usuario_id,), fetch=True, fetch_one=True,
        )

    @staticmethod
    def touch_ultimo_contato(usuario_id):
        """Marca ultimo_contato = agora (todo contato do agente, mesmo revogado)."""
        execute_query(
            "UPDATE colabore_config SET ultimo_contato = NOW() WHERE usuario_id = %s",
            (usuario_id,), fetch=False,
        )
