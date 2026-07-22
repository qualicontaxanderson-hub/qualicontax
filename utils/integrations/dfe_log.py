# -*- coding: utf-8 -*-
"""
Log de OBSERVABILIDADE da captura de DFe: uma linha por RODADA/consulta em
``dfe_consulta_log`` (schema alinhado ao dfe_log do NH na Fase 1).

Existe porque ``dfe_nsu`` guarda UMA linha por cliente, sobrescrita a cada
rodada — depois do fato não dá pra saber o que cada consulta fez, qual cliente
falhou e por quê.

Gravar log NUNCA pode derrubar a captura: ``registrar()`` é best-effort e engole
qualquer exceção (devolve False). Usa ``execute_query`` (pool, autocommit, fuso
-03:00) — ``momento`` vem de ``NOW()`` do banco.
"""
from utils.db_helper import execute_query

_SQL = (
    "INSERT INTO dfe_consulta_log "
    "(momento, origem, evento, cliente_id, cnpj, ult_nsu_env, c_stat, x_motivo, "
    " ret_ult_nsu, ret_max_nsu, docs, notas, eventos, lote, detalhe) "
    "VALUES (NOW(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
)


def _corta(v, n):
    return None if v is None else str(v)[:n]


def registrar(evento, cliente_id=None, cnpj=None, ult_nsu_env=None, c_stat=None,
              x_motivo=None, ret_ult_nsu=None, ret_max_nsu=None, docs=0, notas=0,
              eventos=0, lote=None, detalhe=None, origem='manual'):
    """Grava UMA linha do log. Best-effort: devolve True/False, nunca levanta.

    evento: 'consulta' (foi à SEFAZ), 'pulado_cota' (não consultou), 'erro'.
    origem: 'manual' nesta fase (o scheduler entra na próxima).
    """
    try:
        execute_query(
            _SQL,
            (
                _corta(origem, 12), _corta(evento, 14), cliente_id, _corta(cnpj, 14),
                ult_nsu_env, _corta(c_stat, 6), _corta(x_motivo, 300),
                ret_ult_nsu, ret_max_nsu,
                int(docs or 0), int(notas or 0), int(eventos or 0), lote,
                _corta(detalhe, 300),
            ),
            fetch=False,
        )
        return True
    except Exception:
        return False
