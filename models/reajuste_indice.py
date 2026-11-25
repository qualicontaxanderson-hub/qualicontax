# -*- coding: utf-8 -*-
"""Índices de reajuste dos contratos de prestação (25/11/2026).

Anderson: "clico em índice próprio e coloco o índice, e já temos que ter um campo no sistema para colocar esses
índices". Cadastro em Financeiro › SERVIÇOS › Índices de reajuste (só o administrador); o contrato guarda a SIGLA
em ``dados.reajuste_indice`` e o texto do contrato sai pelo ``texto()`` (sigla + nome por extenso).
"""
from utils.db_helper import execute_query

SITUACOES = ('ATIVO', 'INATIVO')


class ReajusteIndice:

    @staticmethod
    def listar(so_ativos=False):
        sql = "SELECT id, sigla, nome, descricao, situacao, ordem FROM reajuste_indices"
        if so_ativos:
            sql += " WHERE situacao = 'ATIVO'"
        return execute_query(sql + " ORDER BY ordem, sigla", fetch=True) or []

    @staticmethod
    def get(iid):
        return execute_query("SELECT id, sigla, nome, descricao, situacao, ordem FROM reajuste_indices WHERE id = %s", (iid,), fetch=True, fetch_one=True)

    @staticmethod
    def por_sigla(sigla):
        if not (sigla or '').strip():
            return None
        return execute_query("SELECT id, sigla, nome, descricao, situacao, ordem FROM reajuste_indices WHERE sigla = %s",
                             ((sigla or '').strip().upper(),), fetch=True, fetch_one=True)

    @staticmethod
    def texto(sigla):
        """Como o índice aparece no contrato: "IPCA (Índice Nacional de Preços ao Consumidor Amplo)". Sigla fora do
        cadastro (contrato antigo, digitado) volta como está."""
        i = ReajusteIndice.por_sigla(sigla)
        return '%s (%s)' % (i['sigla'], i['nome']) if i else (sigla or '').strip()

    @staticmethod
    def criar(sigla, nome, descricao=None):
        sigla = (sigla or '').strip().upper()[:20]
        nome = (nome or '').strip()[:120]
        if not sigla or not nome:
            raise ValueError('Sigla e nome são obrigatórios.')
        if ReajusteIndice.por_sigla(sigla):
            raise ValueError('Já existe um índice com a sigla %s.' % sigla)
        n = execute_query("SELECT COALESCE(MAX(ordem), 0) + 1 AS n FROM reajuste_indices", fetch=True, fetch_one=True)['n']
        return execute_query("INSERT INTO reajuste_indices (sigla, nome, descricao, ordem) VALUES (%s, %s, %s, %s)",
                             (sigla, nome, (descricao or '').strip()[:160] or None, n), fetch=False)

    @staticmethod
    def alterar(iid, nome, descricao=None):
        nome = (nome or '').strip()[:120]
        if not nome:
            raise ValueError('O nome é obrigatório.')
        execute_query("UPDATE reajuste_indices SET nome = %s, descricao = %s WHERE id = %s",
                      (nome, (descricao or '').strip()[:160] or None, iid), fetch=False)

    @staticmethod
    def set_situacao(iid, situacao):
        if situacao not in SITUACOES:
            raise ValueError('Situação inválida.')
        execute_query("UPDATE reajuste_indices SET situacao = %s WHERE id = %s", (situacao, iid), fetch=False)
