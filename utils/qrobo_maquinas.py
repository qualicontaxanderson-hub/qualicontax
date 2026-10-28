# -*- coding: utf-8 -*-
"""Máquinas de cada posto (Q-Robô 0.4.0+) — tabela robo_maquinas.

A chave é do POSTO; 2 ou 3 PCs do mesmo posto usam a mesma. O robô 0.4.0 manda
em toda chamada:
    X-QRobo-Maquina  uuid da máquina (32 hex), guardado no config.json dele
    X-QRobo-Nome     COMPUTERNAME
    X-QRobo-Versao   versão do robô
e aqui cada PC vira uma linha com o seu último contato. Robô anterior à 0.4.0
não manda nada disso e continua funcionando — só não aparece por máquina.

REGRA DE OURO: registrar máquina NUNCA derruba o recebimento de nota. Tabela
ausente (migration não aplicada), banco lento, cabeçalho torto: loga e segue.

FUSO: NOW()/CURDATE() no SQL, como o qrobo_status — o pool conecta em -03:00.
"""
import logging
import re

from utils.db_helper import execute_query
from utils.qrobo_status import classificar, ha

_log = logging.getLogger(__name__)

_RE_ID = re.compile(r'^[0-9a-f]{32}$')
_RE_TXT = re.compile(r'[^A-Za-z0-9._ -]')


def _limpo(texto, limite):
    return _RE_TXT.sub('_', (texto or '').strip())[:limite] or None


def identidade_do_request(headers):
    """(maquina_id, nome_pc, versao) dos cabeçalhos, ou None se o robô não
    mandou identidade válida (robô anterior à 0.4.0)."""
    mid = (headers.get('X-QRobo-Maquina') or '').strip().lower()
    if not _RE_ID.match(mid):
        return None
    return mid, _limpo(headers.get('X-QRobo-Nome'), 64), _limpo(headers.get('X-QRobo-Versao'), 16)


def registrar(cliente_id, headers, envio=False):
    """Marca o contato DESTA máquina. envio=True conta mais um POST de nota no dia.

    Um UPSERT por chamada, na chave (cliente_id, maquina_id). Em MySQL o SET do
    ON DUPLICATE roda da esquerda para a direita: envios_dia é calculado ANTES
    de dia_envios mudar — é o que zera o contador na virada do dia."""
    ident = identidade_do_request(headers)
    if not ident:
        return
    mid, nome, versao = ident
    inc = 1 if envio else 0
    try:
        execute_query(
            "INSERT INTO robo_maquinas "
            "  (cliente_id, maquina_id, nome_pc, versao, primeiro_contato, ultimo_contato, "
            "   envios_dia, dia_envios) "
            "VALUES (%s, %s, %s, %s, NOW(), NOW(), %s, CURDATE()) "
            "ON DUPLICATE KEY UPDATE "
            "  envios_dia = IF(dia_envios = CURDATE(), envios_dia + VALUES(envios_dia), VALUES(envios_dia)), "
            "  dia_envios = CURDATE(), "
            "  nome_pc = VALUES(nome_pc), versao = VALUES(versao), "
            "  ultimo_contato = NOW(), oculta = 0",
            (cliente_id, mid, nome, versao, inc), fetch=False)
    except Exception:
        _log.exception('[qrobo-maquinas] falha ao registrar maquina %s do cliente %s '
                       '(nota segue normalmente)', mid, cliente_id)
        return
    # 0.4.1: rodou pela tarefa SYSTEM? É o "roda sem ninguém logado" do quadro
    # de conferência. UPDATE à parte: sem a coluna (migração add_robo_maquinas_
    # sistema não aplicada) só este dado falha — o registro acima já entrou.
    if (headers.get('X-QRobo-Modo') or '').strip().lower() == 'sistema':
        try:
            execute_query("UPDATE robo_maquinas SET ultimo_sistema = NOW() "
                          "WHERE cliente_id = %s AND maquina_id = %s",
                          (cliente_id, mid), fetch=False)
        except Exception:
            _log.warning('[qrobo-maquinas] ultimo_sistema não gravado (coluna ausente?)')


def listar(cliente_ids):
    """{cliente_id: [máquina, ...]} das máquinas visíveis, mais recente primeiro.

    Cada máquina: id, nome (apelido ou nome_pc), nome_pc, versao, status/rotulo
    do semáforo (MESMA régua do posto), ha, ultimo_contato, envios_hoje."""
    ids = [int(c) for c in cliente_ids if c]
    if not ids:
        return {}
    ph = ','.join(['%s'] * len(ids))
    try:
        rows = execute_query(
            "SELECT id, cliente_id, maquina_id, nome_pc, apelido, versao, ultimo_contato, "
            "       TIMESTAMPDIFF(MINUTE, ultimo_contato, NOW()) AS min_sem_contato, "
            "       IF(dia_envios = CURDATE(), envios_dia, 0) AS envios_hoje "
            f"  FROM robo_maquinas WHERE oculta = 0 AND cliente_id IN ({ph}) "
            " ORDER BY cliente_id, ultimo_contato DESC", tuple(ids), fetch=True) or []
    except Exception:
        _log.exception('[qrobo-maquinas] falha ao listar (tabela ausente?)')
        return {}
    out = {}
    for r in rows:
        cls, rotulo = classificar(r['min_sem_contato'])
        out.setdefault(r['cliente_id'], []).append({
            'id': r['id'],
            'nome': r['apelido'] or r['nome_pc'] or 'sem nome',
            'nome_pc': r['nome_pc'] or '',
            'apelido': r['apelido'] or '',
            'versao': r['versao'] or '',
            'status': cls,
            'rotulo': rotulo,
            'ha': ha(r['min_sem_contato']),
            'ultimo_contato': r['ultimo_contato'].strftime('%d/%m/%Y %H:%M') if r['ultimo_contato'] else None,
            'envios_hoje': int(r['envios_hoje'] or 0),
        })
    return out


def pior_status(maquinas):
    """Status do posto pela PIOR máquina: um caixa parado pinta o posto."""
    ordem = {'verde': 0, 'cinza': 1, 'amarelo': 2, 'vermelho': 3}
    return max((m['status'] for m in maquinas), key=lambda s: ordem.get(s, 0), default=None)


def _tupla(versao):
    """'0.4.10' -> (0, 4, 10). Texto fora do padrão vira () e perde de qualquer versão."""
    try:
        return tuple(int(x) for x in (versao or '').split('.'))
    except ValueError:
        return ()


def versao_mais_nova(maquinas_por_posto):
    """A maior versão que algum robô reportou — é ela que conta como "nova".

    Sai do dado e não de uma constante: quando a 0.4.1 entrar no ar, a 0.4.0
    passa a ser "desatualizada" sem ninguém mexer aqui."""
    vers = [m['versao'] for ms in maquinas_por_posto.values() for m in ms if _tupla(m['versao'])]
    return max(vers, key=_tupla, default=None)


def selo_versao(maquinas, mais_nova):
    """(classe, texto) da linha VERSÃO do cartão do posto.

    Robô anterior à 0.4.0 não manda versão nenhuma — e é justamente o que não
    envia cancelamento, por isso o texto diz o efeito e não só "sem versão".
    Com 2 PCs, vale a PIOR versão: um caixa antigo ainda deixa cancelamento de fora."""
    vers = [m['versao'] for m in maquinas if _tupla(m['versao'])]
    if not vers:
        return 'antiga', 'antiga · não manda cancelamento'
    pior = min(vers, key=_tupla)
    if mais_nova and _tupla(pior) >= _tupla(mais_nova):
        return 'nova', f'{pior} · nova'
    return 'velha', f'{pior} · desatualizada'


def renomear(maquina_pk, cliente_id, apelido):
    """Apelido da tela ("Caixa 1"). Vazio volta a mostrar o nome do PC."""
    apelido = (apelido or '').strip()[:64] or None
    execute_query("UPDATE robo_maquinas SET apelido = %s WHERE id = %s AND cliente_id = %s",
                  (apelido, maquina_pk, cliente_id), fetch=False)


def ocultar(maquina_pk, cliente_id):
    """Tira da tela (PC formatado / desativado). Se falar de novo, reaparece."""
    execute_query("UPDATE robo_maquinas SET oculta = 1 WHERE id = %s AND cliente_id = %s",
                  (maquina_pk, cliente_id), fetch=False)
