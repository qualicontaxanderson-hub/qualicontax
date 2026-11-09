# -*- coding: utf-8 -*-
"""Marca a VISITA de todas as empresas como "Sem visita" (09/11/2026).

Anderson (09/11/2026): "Visita não são todas empresas que tem, então tem que ter a opção SEM VISITA,
e tem que fazer o seguinte, deixar todos SEM VISITA, e o Rodrigo depois vem colocando qual de fato
ele visita... Por que para ele ir em todas que ele não visita e alterar é mais trabalhoso".

1. ``clientes_responsaveis.usuario_id`` passa a aceitar NULL (linha com NULL = "ninguém responde").
2. Cópia de TODAS as linhas de Visita de hoje em ``clientes_responsaveis_bkp_visita_20261109``
   (a migração da Acessórias tinha posto o Rodrigo em ~400 empresas; dá para voltar se precisar).
3. Visita = NULL (Sem visita) em toda empresa não avulsa, com uma linha de histórico por empresa
   que mudou.

    python scripts/visita_todos_sem_visita.py            # dry-run
    python scripts/visita_todos_sem_visita.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query  # noqa: E402
from utils.atividade import registrar_agente  # noqa: E402

BKP = 'clientes_responsaveis_bkp_visita_20261109'
AUTOR = (62, 'Anderson Antunes Vieira (pedido no chat, aplicado pelo Claude)', 'anderson')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    dep = execute_query("SELECT id FROM departamentos WHERE nome = 'Visita'", fetch=True, fetch_one=True)['id']
    alvo = execute_query(
        "SELECT c.id, c.numero_cliente, c.nome_razao_social, r.usuario_id, u.nome AS usuario_nome, (r.id IS NOT NULL) AS tem "
        "  FROM clientes c "
        "  LEFT JOIN clientes_responsaveis r ON r.cliente_id = c.id AND r.departamento_id = %s "
        "  LEFT JOIN usuarios u ON u.id = r.usuario_id "
        " WHERE COALESCE(c.avulso, 0) = 0", (dep,), fetch=True) or []
    muda = [x for x in alvo if not (x['tem'] and x['usuario_id'] is None)]
    com_alguem = [x for x in muda if x['usuario_id']]
    print(f'empresas não avulsas: {len(alvo)} | vão para "Sem visita": {len(muda)} '
          f'(tinham alguém: {len(com_alguem)}; sem nada: {len(muda) - len(com_alguem)})')
    por = {}
    for x in com_alguem:
        por[x['usuario_nome']] = por.get(x['usuario_nome'], 0) + 1
    print('quem estava na Visita:', por)
    nulo = execute_query("SELECT IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                         "AND TABLE_NAME = 'clientes_responsaveis' AND COLUMN_NAME = 'usuario_id'", fetch=True, fetch_one=True)
    print('usuario_id aceita NULL hoje:', nulo['IS_NULLABLE'])
    if not a.apply:
        print('DRY-RUN. Repita com --apply.')
        return
    if nulo['IS_NULLABLE'] != 'YES':
        execute_query('ALTER TABLE clientes_responsaveis MODIFY usuario_id INT NULL', fetch=False)
        print('coluna usuario_id agora aceita NULL')
    execute_query(f'CREATE TABLE IF NOT EXISTS {BKP} AS SELECT * FROM clientes_responsaveis WHERE departamento_id = %s',
                  (dep,), fetch=False)
    print('cópia:', execute_query(f'SELECT COUNT(*) n FROM {BKP}', fetch=True, fetch_one=True)['n'], 'linhas em', BKP)
    for x in muda:
        execute_query("INSERT INTO clientes_responsaveis (cliente_id, departamento_id, usuario_id) VALUES (%s, %s, NULL) "
                      "ON DUPLICATE KEY UPDATE usuario_id = NULL", (x['id'], dep), fetch=False)
        registrar_agente('escrita.alterou_responsaveis', 'cadastros', *AUTOR, tabela='clientes', registro_id=x['id'],
                         depois={'cliente_id': x['id'], 'responsaveis': {'Visita': {'antes': x['usuario_nome'], 'depois': 'Sem visita'}}})
    print('feito:', len(muda), 'empresas em "Sem visita"')


if __name__ == '__main__':
    main()
