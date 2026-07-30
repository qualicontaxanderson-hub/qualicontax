# -*- coding: utf-8 -*-
"""Config por empresa do Q-Robô (captura de saídas via robô .exe na pista).

Tabela robo_config (1:1 por cliente_id) — criada por migrations/add_robo_config.py.
Espelha dfe_certificados: uma linha só para quem roda o robô. O ``robo_token`` é o
segredo Bearer que autentica o POST /api/saidas e o GET /api/saidas/config."""
from utils.db_helper import execute_query


class RoboConfig:

    @staticmethod
    def get_by_token(token):
        """Linha do robo_config pelo token Bearer (auth da API do robô), ou None.
        Token vazio nunca casa (evita autenticar linhas com robo_token NULL)."""
        if not token:
            return None
        return execute_query(
            "SELECT id, cliente_id, data_inicio_captura, robo_token, robo_reset_seq, "
            "       ativo, robo_ultimo_contato "
            "FROM robo_config WHERE robo_token = %s",
            (token,), fetch=True, fetch_one=True,
        )

    @staticmethod
    def get_by_cliente(cliente_id):
        return execute_query(
            "SELECT id, cliente_id, data_inicio_captura, robo_token, robo_reset_seq, "
            "       ativo, robo_ultimo_contato "
            "FROM robo_config WHERE cliente_id = %s",
            (cliente_id,), fetch=True, fetch_one=True,
        )

    @staticmethod
    def touch_ultimo_contato(cliente_id):
        """Marca robo_ultimo_contato = agora (todo contato do robô, mesmo desligado)."""
        execute_query(
            "UPDATE robo_config SET robo_ultimo_contato = NOW() WHERE cliente_id = %s",
            (cliente_id,), fetch=False,
        )
