# -*- coding: utf-8 -*-
"""D1 — cards de ATIVIDADE da home, a partir de logs_sistema (e roteador_log).

Cada home mostra a atividade DO SEU MÓDULO (por atividade, não por lotação).
Regra da casa: SEM dado real, o card não existe — retorna None. Nada de
placeholder, "0 ações" ou nome inventado. Uma consulta agregada (GROUP BY) por
card; índice idx_logs_modulo_dh(modulo, data_hora) cobre o filtro.
"""
from utils.db_helper import execute_query


def card_quem_entregou(modulo):
    """Lista: no mês corrente, ESCRITAS por usuário no módulo. Rótulo = nome + %.

    Denominador = soma das escritas dos usuários listados (LIMIT 15). No escritório
    real são poucos usuários, então a soma é o total do módulo no período.
    """
    rows = execute_query(
        "SELECT usuario_nome, COUNT(*) AS n FROM logs_sistema "
        "WHERE modulo = %s AND acao LIKE 'escrita.%%' AND usuario_nome IS NOT NULL "
        "AND data_hora >= DATE_FORMAT(CURDATE(), '%Y-%m-01') "
        "GROUP BY usuario_nome ORDER BY n DESC LIMIT 15",
        (modulo,), fetch=True) or []
    if not rows:
        return None
    total = sum(int(r['n']) for r in rows) or 1
    itens = [{'valor': int(r['n']),
              'rotulo': '%s · %d%%' % (r['usuario_nome'], round(100 * int(r['n']) / total))}
             for r in rows]
    return {'id': 'quem_entregou', 'tipo': 'lista', 'icone': 'fa-trophy',
            'titulo': 'Quem mais entregou', 'itens': itens,
            'trend': {'tipo': 'neutro', 'rotulo': 'no mês'}}


def card_trabalhando_agora(modulo):
    """Lista: quem teve QUALQUER ação nos últimos 10 min no módulo. 'há N min'.

    Sem ninguém ativo -> None (seção sem conteúdo não existe).
    """
    rows = execute_query(
        "SELECT usuario_nome, TIMESTAMPDIFF(MINUTE, MAX(data_hora), NOW()) AS min_atras "
        "FROM logs_sistema WHERE modulo = %s AND usuario_nome IS NOT NULL "
        "AND data_hora >= NOW() - INTERVAL 10 MINUTE "
        "GROUP BY usuario_nome ORDER BY MAX(data_hora) DESC LIMIT 15",
        (modulo,), fetch=True) or []
    if not rows:
        return None
    def _quando(m):
        m = int(m or 0)
        return 'agora' if m <= 0 else ('há %d min' % m)
    itens = [{'valor': _quando(r['min_atras']), 'rotulo': r['usuario_nome'], 'barra': 0}
             for r in rows]
    return {'id': 'trabalhando', 'tipo': 'lista', 'icone': 'fa-user-clock',
            'titulo': 'Trabalhando agora', 'itens': itens,
            'trend': {'tipo': 'neutro', 'rotulo': '10 min'}}


def card_chegando_cliente():
    """Lista (só Fiscal): documentos entregues pelo canal do cliente nos últimos
    7 dias, por empresa. Fonte: roteador_log (o cron move da _ENTRADA; resultado
    'MOVIDO' = entrega real). Sem entrega no período -> None.
    """
    rows = execute_query(
        "SELECT rl.empresa_numero AS num, MAX(c.nome_razao_social) AS nome, COUNT(*) AS n "
        "FROM roteador_log rl "
        "LEFT JOIN clientes c ON c.numero_cliente = rl.empresa_numero "
        "WHERE rl.resultado = 'MOVIDO' AND rl.criado_em >= NOW() - INTERVAL 7 DAY "
        "GROUP BY rl.empresa_numero ORDER BY n DESC LIMIT 15",
        fetch=True) or []
    if not rows:
        return None
    def _rot(r):
        nome = (r['nome'] or '').strip()
        num = (r['num'] or '').strip()
        return ('#%s %s' % (num, nome)).strip() if num else (nome or '—')
    itens = [{'valor': int(r['n']), 'rotulo': _rot(r)} for r in rows]
    return {'id': 'chegando', 'tipo': 'lista', 'icone': 'fa-inbox',
            'titulo': 'Chegando do cliente', 'itens': itens,
            'trend': {'tipo': 'neutro', 'rotulo': '7 dias'}}
