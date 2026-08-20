# -*- coding: utf-8 -*-
"""Empresas do financeiro (E2.1) — quem participa do multiempresa.

``cliente_id`` aponta para o cadastro de clientes: as empresas do Anderson
(e a PF dele) JÁ existem lá — aqui só se marca quem entra no financeiro,
com apelido curto para os chips e a regra do consolidado.
"""
from utils.db_helper import execute_query


class FinEmpresa:

    @staticmethod
    def listar(apenas_ativas=True):
        cond = 'WHERE e.ativo = 1' if apenas_ativas else ''
        return execute_query(
            f"""SELECT e.id, e.cliente_id, e.apelido, e.ordem,
                       e.no_consolidado, e.ativo,
                       c.nome_razao_social, c.numero_cliente, c.tipo_pessoa
                  FROM fin_empresas e
                  JOIN clientes c ON c.id = e.cliente_id
                {cond}
                 ORDER BY e.ordem, e.apelido""", fetch=True) or []

    @staticmethod
    def mapa():
        """{cliente_id: apelido} das ativas — para rotular linhas nas telas."""
        return {e['cliente_id']: e['apelido'] for e in FinEmpresa.listar()}

    @staticmethod
    def ids_validos():
        return {e['cliente_id'] for e in FinEmpresa.listar()}

    @staticmethod
    def marcar(cliente_id, apelido, ordem=None):
        """Coloca um cadastro no financeiro. None se já estiver (uk_cliente)."""
        if ordem is None:
            r = execute_query('SELECT COALESCE(MAX(ordem), 0) + 10 AS o '
                              'FROM fin_empresas', fetch=True, fetch_one=True)
            ordem = (r or {}).get('o') or 10
        return execute_query(
            'INSERT INTO fin_empresas (cliente_id, apelido, ordem) '
            'VALUES (%s, %s, %s)', (cliente_id, apelido, ordem))

    @staticmethod
    def atualizar(emp_id, apelido=None, no_consolidado=None, ativo=None):
        sets, params = [], []
        if apelido is not None:
            sets.append('apelido = %s')
            params.append(apelido)
        if no_consolidado is not None:
            sets.append('no_consolidado = %s')
            params.append(1 if no_consolidado else 0)
        if ativo is not None:
            sets.append('ativo = %s')
            params.append(1 if ativo else 0)
        if not sets:
            return
        params.append(emp_id)
        execute_query(f"UPDATE fin_empresas SET {', '.join(sets)} WHERE id = %s",
                      tuple(params))

    @staticmethod
    def get(emp_id):
        return execute_query('SELECT * FROM fin_empresas WHERE id = %s',
                             (emp_id,), fetch=True, fetch_one=True)

    @staticmethod
    def tem_movimento(cliente_id):
        """True se a empresa já tem título/saldo/extrato — não se desmarca."""
        for t, c in (('fin_titulos', 'empresa_id'),
                     ('fin_saldos', 'empresa_id'),
                     ('extrato_lancamentos', 'empresa_id')):
            r = execute_query(f'SELECT 1 FROM {t} WHERE {c} = %s LIMIT 1',
                              (cliente_id,), fetch=True, fetch_one=True)
            if r:
                return True
        return False
