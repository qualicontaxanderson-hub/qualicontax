"""Modelo de Lançamentos de Recebimento"""
from utils.db_helper import execute_query


class LancamentoRecebimento:
    """Representa um lançamento de recebimento financeiro."""

    def __init__(self, id, empresa_id, conta_id, forma_recebimento_id,
                 descricao, valor, data_lancamento, data_vencimento,
                 data_pagamento, status, observacao, criado_em,
                 empresa_nome=None, conta_descricao=None,
                 forma_descricao=None):
        self.id = id
        self.empresa_id = empresa_id
        self.conta_id = conta_id
        self.forma_recebimento_id = forma_recebimento_id
        self.descricao = descricao
        self.valor = valor
        self.data_lancamento = data_lancamento
        self.data_vencimento = data_vencimento
        self.data_pagamento = data_pagamento
        self.status = status
        self.observacao = observacao
        self.criado_em = criado_em
        self.empresa_nome = empresa_nome
        self.conta_descricao = conta_descricao
        self.forma_descricao = forma_descricao

    # ------------------------------------------------------------------
    # Helpers de setup (garantem que as tabelas existam)
    # ------------------------------------------------------------------
    @staticmethod
    def ensure_tables():
        """Cria as tabelas do módulo financeiro se não existirem."""
        queries = [
            """CREATE TABLE IF NOT EXISTS empresas (
                id INT AUTO_INCREMENT PRIMARY KEY,
                nome VARCHAR(200) NOT NULL,
                cnpj VARCHAR(18) DEFAULT NULL,
                situacao ENUM('ATIVO','INATIVO') NOT NULL DEFAULT 'ATIVO',
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

            """CREATE TABLE IF NOT EXISTS contas_bancarias (
                id INT AUTO_INCREMENT PRIMARY KEY,
                empresa_id INT NOT NULL,
                descricao VARCHAR(150) NOT NULL,
                banco VARCHAR(100) DEFAULT NULL,
                agencia VARCHAR(20) DEFAULT NULL,
                conta VARCHAR(30) DEFAULT NULL,
                situacao ENUM('ATIVO','INATIVO') NOT NULL DEFAULT 'ATIVO',
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_empresa (empresa_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

            """CREATE TABLE IF NOT EXISTS formas_recebimento (
                id INT AUTO_INCREMENT PRIMARY KEY,
                descricao VARCHAR(100) NOT NULL,
                situacao ENUM('ATIVO','INATIVO') NOT NULL DEFAULT 'ATIVO',
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",

            """INSERT IGNORE INTO formas_recebimento (id, descricao) VALUES
               (1,'Dinheiro'),(2,'Cartão de Crédito'),(3,'Cartão de Débito'),
               (4,'Transferência Bancária'),(5,'PIX'),(6,'Boleto'),(7,'Cheque')""",

            """CREATE TABLE IF NOT EXISTS lancamentos_recebimento (
                id INT AUTO_INCREMENT PRIMARY KEY,
                empresa_id INT NOT NULL,
                conta_id INT NOT NULL,
                forma_recebimento_id INT DEFAULT NULL,
                descricao VARCHAR(255) NOT NULL,
                valor DECIMAL(12,2) NOT NULL DEFAULT 0.00,
                data_lancamento DATE NOT NULL,
                data_vencimento DATE DEFAULT NULL,
                data_pagamento DATE DEFAULT NULL,
                status ENUM('PENDENTE','RECEBIDO','CANCELADO','ESTORNADO')
                       NOT NULL DEFAULT 'PENDENTE',
                observacao TEXT DEFAULT NULL,
                criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_empresa (empresa_id),
                INDEX idx_conta (conta_id),
                INDEX idx_status (status),
                INDEX idx_data (data_lancamento)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
        ]
        for q in queries:
            execute_query(q, fetch=False)

    # ------------------------------------------------------------------
    # Listagem com filtros
    # ------------------------------------------------------------------
    @staticmethod
    def listar(empresa_id=None, conta_id=None, forma_recebimento_id=None,
               status=None, data_inicio=None, data_fim=None, descricao=None):
        """Retorna lista de lançamentos aplicando filtros opcionais."""
        sql = """
            SELECT
                lr.id,
                lr.empresa_id,
                lr.conta_id,
                lr.forma_recebimento_id,
                lr.descricao,
                lr.valor,
                lr.data_lancamento,
                lr.data_vencimento,
                lr.data_pagamento,
                lr.status,
                lr.observacao,
                lr.criado_em,
                e.nome           AS empresa_nome,
                cb.descricao     AS conta_descricao,
                fr.descricao     AS forma_descricao
            FROM lancamentos_recebimento lr
            LEFT JOIN empresas          e  ON e.id  = lr.empresa_id
            LEFT JOIN contas_bancarias  cb ON cb.id = lr.conta_id
            LEFT JOIN formas_recebimento fr ON fr.id = lr.forma_recebimento_id
            WHERE 1=1
        """
        params = []

        if empresa_id:
            sql += " AND lr.empresa_id = %s"
            params.append(int(empresa_id))
        if conta_id:
            sql += " AND lr.conta_id = %s"
            params.append(int(conta_id))
        if forma_recebimento_id:
            sql += " AND lr.forma_recebimento_id = %s"
            params.append(int(forma_recebimento_id))
        if status:
            sql += " AND lr.status = %s"
            params.append(status)
        if data_inicio:
            sql += " AND lr.data_lancamento >= %s"
            params.append(data_inicio)
        if data_fim:
            sql += " AND lr.data_lancamento <= %s"
            params.append(data_fim)
        if descricao:
            sql += " AND lr.descricao LIKE %s"
            params.append(f"%{descricao}%")

        sql += " ORDER BY lr.data_lancamento DESC, lr.id DESC"

        rows = execute_query(sql, params, fetch=True) or []
        return [LancamentoRecebimento(**r) for r in rows]

    # ------------------------------------------------------------------
    # Busca por ID
    # ------------------------------------------------------------------
    @staticmethod
    def get_by_id(lancamento_id):
        """Retorna um lançamento pelo ID."""
        sql = "SELECT * FROM lancamentos_recebimento WHERE id = %s"
        row = execute_query(sql, (lancamento_id,), fetch=True, fetch_one=True)
        if row:
            return LancamentoRecebimento(**row)
        return None

    # ------------------------------------------------------------------
    # Excluir um lançamento
    # ------------------------------------------------------------------
    @staticmethod
    def excluir(lancamento_id):
        """Exclui um lançamento pelo ID. Retorna True em sucesso."""
        result = execute_query(
            "DELETE FROM lancamentos_recebimento WHERE id = %s",
            (lancamento_id,),
            fetch=False
        )
        return result is not None

    # ------------------------------------------------------------------
    # Excluir em lote
    # ------------------------------------------------------------------
    @staticmethod
    def excluir_lote(ids):
        """Exclui múltiplos lançamentos. ids deve ser uma lista de inteiros positivos."""
        if not ids:
            return False
        # Ensure every element is a positive integer (defensive guard)
        validated = [int(i) for i in ids if str(i).isdigit() and int(i) > 0]
        if not validated:
            return False
        placeholders = ",".join(["%s"] * len(validated))
        result = execute_query(
            f"DELETE FROM lancamentos_recebimento WHERE id IN ({placeholders})",
            tuple(validated),
            fetch=False
        )
        return result is not None

    # ------------------------------------------------------------------
    # Totais para o painel de resumo
    # ------------------------------------------------------------------
    @staticmethod
    def totais(empresa_id=None, conta_id=None):
        """Retorna contagens e somatórios para cartões de resumo."""
        params = []
        where = "WHERE 1=1"
        if empresa_id:
            where += " AND empresa_id = %s"
            params.append(int(empresa_id))
        if conta_id:
            where += " AND conta_id = %s"
            params.append(int(conta_id))

        sql = f"""
            SELECT
                COUNT(*)                                                   AS total,
                SUM(valor)                                                 AS valor_total,
                SUM(CASE WHEN status='PENDENTE'  THEN valor ELSE 0 END)   AS valor_pendente,
                SUM(CASE WHEN status='RECEBIDO'  THEN valor ELSE 0 END)   AS valor_recebido,
                COUNT(CASE WHEN status='PENDENTE'  THEN 1 END)            AS qtd_pendente,
                COUNT(CASE WHEN status='RECEBIDO'  THEN 1 END)            AS qtd_recebido
            FROM lancamentos_recebimento
            {where}
        """
        return execute_query(sql, params, fetch=True, fetch_one=True) or {}

    # ------------------------------------------------------------------
    # Lookups para os filtros
    # ------------------------------------------------------------------
    @staticmethod
    def listar_empresas():
        return execute_query(
            "SELECT id, nome FROM empresas WHERE situacao='ATIVO' ORDER BY nome",
            fetch=True
        ) or []

    @staticmethod
    def listar_contas(empresa_id=None):
        if empresa_id:
            return execute_query(
                "SELECT id, descricao FROM contas_bancarias "
                "WHERE situacao='ATIVO' AND empresa_id=%s ORDER BY descricao",
                (int(empresa_id),), fetch=True
            ) or []
        return execute_query(
            "SELECT id, descricao FROM contas_bancarias "
            "WHERE situacao='ATIVO' ORDER BY descricao",
            fetch=True
        ) or []

    @staticmethod
    def listar_formas_recebimento():
        return execute_query(
            "SELECT id, descricao FROM formas_recebimento "
            "WHERE situacao='ATIVO' ORDER BY descricao",
            fetch=True
        ) or []
