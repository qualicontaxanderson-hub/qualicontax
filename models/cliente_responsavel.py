# -*- coding: utf-8 -*-
"""Responsável do escritório por empresa e departamento (migração Acessórias, 05/11/2026).

Tabela ``clientes_responsaveis`` (cliente_id, departamento_id, usuario_id): para cada empresa, quem
do escritório responde pelo Contábil, Fiscal, Pessoal, Financeiro, Legalização, Certificado Digital
e Visita. É diferente de ``cliente_contadores`` (o CONTADOR com certificado, para a captura) e de
``contatos_clientes`` (gente DO CLIENTE que recebe boleto e documento).
"""
from utils.db_helper import execute_query
from models.contato_cliente import DEPARTAMENTOS_CONTATO

# os mesmos 7 departamentos que o contato recebe são os que têm responsável no escritório
DEPARTAMENTOS_RESPONSAVEL = list(DEPARTAMENTOS_CONTATO)


class ClienteResponsavel:

    @staticmethod
    def _ordem():
        return ', '.join("'%s'" % d.replace("'", "''") for d in DEPARTAMENTOS_RESPONSAVEL)

    @staticmethod
    def por_cliente(cliente_id):
        """Os 7 departamentos, sempre, cada um com o responsável (ou None).
        [{departamento_id, departamento, usuario_id, usuario_nome, usuario_situacao}]"""
        ordem = ClienteResponsavel._ordem()
        return execute_query(
            f"""SELECT d.id AS departamento_id, d.nome AS departamento, r.usuario_id,
                       u.nome AS usuario_nome, u.situacao AS usuario_situacao
                  FROM departamentos d
             LEFT JOIN clientes_responsaveis r ON r.departamento_id = d.id AND r.cliente_id = %s
             LEFT JOIN usuarios u ON u.id = r.usuario_id
                 WHERE d.nome IN ({ordem})
              ORDER BY FIELD(d.nome, {ordem})""", (cliente_id,), fetch=True) or []

    @staticmethod
    def definir(cliente_id, departamento_id, usuario_id):
        """Grava (ou tira, se usuario_id vazio) o responsável de um departamento. Idempotente."""
        if usuario_id:
            return execute_query(
                """INSERT INTO clientes_responsaveis (cliente_id, departamento_id, usuario_id)
                   VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE usuario_id = VALUES(usuario_id)""",
                (cliente_id, departamento_id, usuario_id), fetch=False)
        return execute_query('DELETE FROM clientes_responsaveis WHERE cliente_id = %s AND departamento_id = %s',
                             (cliente_id, departamento_id), fetch=False)

    @staticmethod
    def usuarios_disponiveis():
        """Funcionários que podem ser responsáveis: conta de funcionário que não está INATIVA.
        [{id, nome, departamento_id, departamento}]"""
        return execute_query(
            """SELECT u.id, u.nome, u.departamento_id, COALESCE(d.nome, 'Sem departamento') AS departamento
                 FROM usuarios u LEFT JOIN departamentos d ON d.id = u.departamento_id
                WHERE u.classe_conta = 'FUNCIONARIO' AND u.situacao <> 'INATIVO'
             ORDER BY u.nome""", fetch=True) or []

    @staticmethod
    def carteira(usuario_id):
        """Quantas empresas (por departamento) este funcionário responde. [{departamento, n}]"""
        return execute_query(
            """SELECT d.nome AS departamento, COUNT(*) AS n
                 FROM clientes_responsaveis r JOIN departamentos d ON d.id = r.departamento_id
                WHERE r.usuario_id = %s GROUP BY d.nome ORDER BY n DESC""", (usuario_id,), fetch=True) or []

    @staticmethod
    def sem_responsavel():
        """Empresas ativas (não avulsas) com algum dos 7 departamentos sem responsável:
        [{cliente_id, numero_cliente, nome_razao_social, faltam}]"""
        ordem = ClienteResponsavel._ordem()
        return execute_query(
            f"""SELECT c.id AS cliente_id, c.numero_cliente, c.nome_razao_social,
                       GROUP_CONCAT(d.nome ORDER BY FIELD(d.nome, {ordem}) SEPARATOR ', ') AS faltam
                  FROM clientes c
                  JOIN departamentos d ON d.nome IN ({ordem})
             LEFT JOIN clientes_responsaveis r ON r.cliente_id = c.id AND r.departamento_id = d.id
                 WHERE c.situacao = 'ATIVO' AND c.avulso = 0 AND r.id IS NULL
              GROUP BY c.id, c.numero_cliente, c.nome_razao_social
              ORDER BY c.numero_cliente + 0""", fetch=True) or []
