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

    # ---------------- link para os departamentos elegerem (06/11/2026) ----------------
    @staticmethod
    def convite_criar(cliente_id, criado_por, dias=7):
        import secrets
        token = secrets.token_urlsafe(24)
        cid = execute_query(
            'INSERT INTO clientes_responsaveis_convites (token, cliente_id, criado_por, expira_em) '
            'VALUES (%s, %s, %s, DATE_ADD(NOW(), INTERVAL %s DAY))', (token, cliente_id, criado_por, dias), fetch=False)
        return cid, token

    @staticmethod
    def convite_por_token(token):
        return execute_query(
            """SELECT v.*, c.numero_cliente, c.nome_razao_social, u.nome AS criado_por_nome
                 FROM clientes_responsaveis_convites v JOIN clientes c ON c.id = v.cliente_id
            LEFT JOIN usuarios u ON u.id = v.criado_por WHERE v.token = %s""", (token,), fetch=True, fetch_one=True)

    @staticmethod
    def convite_ativo(cliente_id):
        """O link em aberto da empresa (não concluído e não vencido), com quem já respondeu o quê."""
        v = execute_query(
            """SELECT v.*, u.nome AS criado_por_nome FROM clientes_responsaveis_convites v LEFT JOIN usuarios u ON u.id = v.criado_por
                WHERE v.cliente_id = %s AND v.concluido_em IS NULL AND v.expira_em > NOW() ORDER BY v.id DESC LIMIT 1""",
            (cliente_id,), fetch=True, fetch_one=True)
        if v:
            v['respostas'] = ClienteResponsavel.convite_respostas(v['id'])
        return v

    @staticmethod
    def convite_respostas(convite_id):
        return execute_query(
            """SELECT r.departamento_id, d.nome AS departamento, r.respondido_em, u.nome AS usuario_nome, e.nome AS escolhido_nome
                 FROM clientes_responsaveis_respostas r JOIN departamentos d ON d.id = r.departamento_id
                 JOIN usuarios u ON u.id = r.usuario_id JOIN usuarios e ON e.id = r.escolhido_id
                WHERE r.convite_id = %s ORDER BY r.id""", (convite_id,), fetch=True) or []

    @staticmethod
    def departamentos_que_pode(usuario, convite):
        """Pelo link, cada um escolhe só o responsável do PRÓPRIO departamento. Quem gerou o link, a Diretoria e o
        admin escolhem todos. A Legalização também responde por Certificado Digital e Visita (são dela)."""
        if usuario.is_admin() or convite['criado_por'] == usuario.id:
            return None                                  # None = todos
        dep = getattr(usuario, 'departamento_id', None)
        nome = execute_query('SELECT nome FROM departamentos WHERE id = %s', (dep,), fetch=True, fetch_one=True) if dep else None
        nome = (nome or {}).get('nome')
        if nome == 'Diretoria':
            return None
        if nome == 'Legalização':
            ids = execute_query("SELECT id FROM departamentos WHERE nome IN ('Legalização', 'Certificado Digital', 'Visita')", fetch=True) or []
            return {r['id'] for r in ids}
        return {dep} if dep else set()

    @staticmethod
    def convite_responder(convite, departamento_id, escolhido_id, usuario_id):
        ClienteResponsavel.definir(convite['cliente_id'], departamento_id, escolhido_id)
        execute_query('INSERT INTO clientes_responsaveis_respostas (convite_id, departamento_id, usuario_id, escolhido_id) VALUES (%s, %s, %s, %s)',
                      (convite['id'], departamento_id, usuario_id, escolhido_id), fetch=False)

    @staticmethod
    def convite_fechar_se_completo(convite):
        if all(r['usuario_id'] for r in ClienteResponsavel.por_cliente(convite['cliente_id'])):
            execute_query('UPDATE clientes_responsaveis_convites SET concluido_em = NOW() WHERE id = %s', (convite['id'],), fetch=False)
            return True
        return False

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
