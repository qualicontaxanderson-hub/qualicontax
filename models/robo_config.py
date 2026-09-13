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
    def listar_painel():
        """Uma linha por posto com robo_config, para o Monitor do painel.

        SOMENTE LEITURA. A matemática de tempo fica no SQL de propósito: o pool
        conecta com time_zone='-03:00' (utils/db_helper.py), então NOW(),
        robo_ultimo_contato (DATETIME gravado com NOW()) e importado_em
        (TIMESTAMP convertido na leitura) estão todos em BRT e são comparáveis
        entre si. Fazer a conta em Python usaria o relógio do processo — UTC no
        Railway — e daria 3h de erro no status."""
        # O agregado por robô (total de saídas, última captura) NÃO é mais
        # calculado aqui. Até 12/09/2026 era um GROUP BY sobre nfe_importacoes
        # dentro desta consulta — com 700 mil linhas de Q-ROBO levou 413
        # SEGUNDOS, duas abas ao mesmo tempo, com o site fora do ar. Agora o
        # cron do roteador calcula (utils/painel_cache.atualizar_painel_qrobo) e
        # esta função só LÊ o resultado e junta em Python. O que fica ao vivo
        # no SQL é só o barato: robo_config e clientes.
        #
        # A matemática de tempo continua no relógio do BANCO: ``agora`` vem de
        # NOW() na mesma conexão (-03:00), e a ultima_captura foi gravada por
        # NOW() do banco também — comparáveis entre si, sem o relógio do
        # processo (UTC no Railway, 3h de erro).
        from datetime import datetime
        from utils.painel_cache import ler, CHAVE_PAINEL_QROBO

        rows = execute_query(
            "SELECT r.id, r.cliente_id, r.ativo, r.data_inicio_captura, "
            "       r.robo_reset_seq, r.robo_ultimo_contato, "
            "       TIMESTAMPDIFF(MINUTE, r.robo_ultimo_contato, NOW()) AS min_sem_contato, "
            "       c.numero_cliente, c.nome_razao_social, NOW() AS agora "
            "  FROM robo_config r "
            "  LEFT JOIN clientes c ON c.id = r.cliente_id "
            " ORDER BY c.nome_razao_social",
            fetch=True,
        ) or []
        painel, _idade = ler(CHAVE_PAINEL_QROBO)
        painel = painel or {}
        for r in rows:
            p = painel.get(str(r['cliente_id'])) or {}
            r['total_saidas'] = int(p.get('total_saidas') or 0)
            ult = p.get('ultima_captura')
            if isinstance(ult, str) and ult:
                try:
                    ult = datetime.strptime(ult[:19], '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    ult = None
            r['ultima_captura'] = ult
            agora = r.pop('agora', None)
            r['min_ultima_captura'] = (
                int((agora - ult).total_seconds() // 60)
                if (ult and agora) else None)
        return rows

    @staticmethod
    def touch_ultimo_contato(cliente_id):
        """Marca robo_ultimo_contato = agora (todo contato do robô, mesmo desligado)."""
        execute_query(
            "UPDATE robo_config SET robo_ultimo_contato = NOW() WHERE cliente_id = %s",
            (cliente_id,), fetch=False,
        )
