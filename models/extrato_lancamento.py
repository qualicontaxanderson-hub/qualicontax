# -*- coding: utf-8 -*-
"""Extrato bancário importado (Documento E, fase 4).

Tabela compartilhada com o futuro A2: ``empresa_id`` NULL = escritório
(Qualicontax); cliente da carteira quando o A2 chegar. A idempotência do
import mora no ``hash_dedup`` (UNIQUE) — quem monta a chave é
utils.ofx_parser.chave_dedup, a mesma para parser e gravação.
"""
from utils.db_helper import execute_query


class ExtratoLancamento:

    @staticmethod
    def listar(empresa_ids=None, data_de=None, data_ate=None, conta=None,
               busca=None, limite=500):
        cond, params = ['1=1'], []
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond.append(f'empresa_id IN ({marks})')
            params += list(empresa_ids)
        if data_de:
            cond.append('data >= %s')
            params.append(data_de)
        if data_ate:
            cond.append('data <= %s')
            params.append(data_ate)
        if conta:
            cond.append("CONCAT(COALESCE(banco,''), ' · ', COALESCE(conta,'')) = %s")
            params.append(conta)
        if busca:
            cond.append('(descricao LIKE %s OR documento LIKE %s)')
            like = f'%{busca}%'
            params += [like, like]
        where = ' AND '.join(cond)
        return execute_query(
            f"""SELECT id, empresa_id, banco, conta, data, valor, tipo, descricao,
                       documento, fitid, origem, arquivo, criado_em
                  FROM extrato_lancamentos
                 WHERE {where}
                 ORDER BY data DESC, id DESC
                 LIMIT {int(limite)}""",
            tuple(params), fetch=True) or []

    @staticmethod
    def totais(empresa_ids=None, data_de=None, data_ate=None, conta=None,
               busca=None):
        """Créditos, débitos e contagem DO FILTRO (não da página)."""
        cond, params = ['1=1'], []
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond.append(f'empresa_id IN ({marks})')
            params += list(empresa_ids)
        if data_de:
            cond.append('data >= %s')
            params.append(data_de)
        if data_ate:
            cond.append('data <= %s')
            params.append(data_ate)
        if conta:
            cond.append("CONCAT(COALESCE(banco,''), ' · ', COALESCE(conta,'')) = %s")
            params.append(conta)
        if busca:
            cond.append('(descricao LIKE %s OR documento LIKE %s)')
            like = f'%{busca}%'
            params += [like, like]
        where = ' AND '.join(cond)
        return execute_query(
            f"""SELECT COUNT(*) AS n,
                       COALESCE(SUM(CASE WHEN valor >= 0 THEN valor END), 0) AS creditos,
                       COALESCE(SUM(CASE WHEN valor < 0 THEN valor END), 0)  AS debitos
                  FROM extrato_lancamentos WHERE {where}""",
            tuple(params), fetch=True, fetch_one=True) or {}

    @staticmethod
    def contas(empresa_ids=None):
        cond, params = '1=1', ()
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond, params = f'empresa_id IN ({marks})', tuple(empresa_ids)
        rows = execute_query(
            f"""SELECT DISTINCT CONCAT(COALESCE(banco,''), ' · ',
                       COALESCE(conta,'')) AS rotulo
                  FROM extrato_lancamentos WHERE {cond} ORDER BY rotulo""",
            params, fetch=True) or []
        return [r['rotulo'] for r in rows]

    @staticmethod
    def hashes_existentes(hashes):
        """Quais dessas chaves já estão gravadas (para contar repetido certo)."""
        achados = set()
        for i in range(0, len(hashes), 300):
            fatia = hashes[i:i + 300]
            marks = ','.join(['%s'] * len(fatia))
            rows = execute_query(
                f'SELECT hash_dedup FROM extrato_lancamentos '
                f'WHERE hash_dedup IN ({marks})', tuple(fatia), fetch=True) or []
            achados.update(r['hash_dedup'] for r in rows)
        return achados

    @staticmethod
    def inserir_lote(itens, banco, conta, arquivo, usuario_id,
                     empresa_id=None, origem='ofx'):
        """itens: [(hash, lancamento_do_parser)]. Insere em blocos (banco é
        remoto — um INSERT por linha seria um caracol). O UNIQUE uk_dedup é o
        guarda-costas contra corrida."""
        total = 0
        for i in range(0, len(itens), 200):
            fatia = itens[i:i + 200]
            valores, params = [], []
            for h, l in fatia:
                valores.append('(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)')
                params += [empresa_id, banco, conta, l['data'], l['valor'],
                           l['tipo'], l['descricao'], l['documento'],
                           l['fitid'], h, origem, arquivo]
            execute_query(
                'INSERT IGNORE INTO extrato_lancamentos '
                '(empresa_id, banco, conta, data, valor, tipo, descricao, '
                ' documento, fitid, hash_dedup, origem, arquivo) '
                'VALUES ' + ', '.join(valores), tuple(params))
            total += len(fatia)
        if usuario_id and itens:
            hashes = [h for h, _ in itens]
            for i in range(0, len(hashes), 300):
                fatia = hashes[i:i + 300]
                marks = ','.join(['%s'] * len(fatia))
                execute_query(
                    f'UPDATE extrato_lancamentos SET usuario_id = %s '
                    f'WHERE hash_dedup IN ({marks}) AND usuario_id IS NULL',
                    (usuario_id, *fatia))
        return total
