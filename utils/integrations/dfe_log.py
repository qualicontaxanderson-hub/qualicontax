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

Serve as DUAS capturas: ``servico`` distingue 'nfe' (default) de 'cte'. A coluna é
recente, então há um FALLBACK para o schema antigo — o cron de CT-e pode rodar num
container que ainda não passou pela migration e o log não pode simplesmente sumir.
"""
from utils.db_helper import execute_query

_COLS = (
    "momento, origem, evento, cliente_id, cnpj, ult_nsu_env, c_stat, x_motivo, "
    "ret_ult_nsu, ret_max_nsu, docs, notas, eventos, lote, detalhe"
)
_VALS = "NOW(),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s"

_SQL = f"INSERT INTO dfe_consulta_log ({_COLS}, servico) VALUES ({_VALS},%s)"
# Schema anterior à coluna ``servico`` (ver docstring).
_SQL_LEGADO = f"INSERT INTO dfe_consulta_log ({_COLS}) VALUES ({_VALS})"

# Uma vez detectado que a coluna não existe, não insiste a cada linha.
_sem_coluna_servico = False


def _corta(v, n):
    return None if v is None else str(v)[:n]


def registrar(evento, cliente_id=None, cnpj=None, ult_nsu_env=None, c_stat=None,
              x_motivo=None, ret_ult_nsu=None, ret_max_nsu=None, docs=0, notas=0,
              eventos=0, lote=None, detalhe=None, origem='manual', servico='nfe'):
    """Grava UMA linha do log. Best-effort: devolve True/False, nunca levanta.

    evento: 'consulta' (foi à SEFAZ), 'pulado_cota'/'pulado_lock' (não consultou),
    'cons_nsu' (busca pontual), 'seed_manual', 'erro'.
    origem: 'manual' | 'agendado'.
    servico: 'nfe' (distDFeInt de NF-e) | 'cte' (CTeDistribuicaoDFe).
    """
    global _sem_coluna_servico
    base = (
        _corta(origem, 12), _corta(evento, 14), cliente_id, _corta(cnpj, 14),
        ult_nsu_env, _corta(c_stat, 6), _corta(x_motivo, 300),
        ret_ult_nsu, ret_max_nsu,
        int(docs or 0), int(notas or 0), int(eventos or 0), lote,
        _corta(detalhe, 300),
    )
    try:
        if not _sem_coluna_servico:
            if execute_query(_SQL, base + (_corta(servico, 4),), fetch=False) is not None:
                return True
            # execute_query engole o erro e devolve None. A causa mais provável
            # aqui é a coluna ``servico`` ainda não existir neste banco.
            _sem_coluna_servico = True
        return execute_query(_SQL_LEGADO, base, fetch=False) is not None
    except Exception:
        return False
