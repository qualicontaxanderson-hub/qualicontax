"""Vínculo N:N entre um cliente e os CONTADORES responsáveis por ele.

"Contador" não é uma tabela à parte: é um cadastro em ``clientes`` com
``is_contador=1``, que tem o certificado digital DELE vinculado do jeito normal
(titular == cadastro). Isso evita duplicar o mesmo .pfx por empresa e dispensa
afrouxar a validação de titular no upload de certificado.

A CAPTURA não usa esta tabela ainda (Parte 2).
"""
from utils.db_helper import execute_query


class ClienteContador:
    """Vínculos cliente <-> contador (tabela cliente_contadores)."""

    # Colunas do contador reaproveitadas nas duas listagens (com o estado do
    # certificado dele: sem certificado, o vínculo não captura nada na Parte 2).
    _COLS_CONTADOR = """
        c.id, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj, c.tipo_pessoa,
        c.situacao,
        (dc.id IS NOT NULL) AS tem_certificado,
        dc.tipo_doc AS cert_tipo_doc, dc.cnpj AS cert_doc, dc.validade AS cert_validade
    """

    @staticmethod
    def listar_contadores():
        """Cadastros marcados como contador (is_contador=1), ATIVOS.

        Alimenta o dropdown "adicionar contador". Traz o estado do certificado
        para a tela poder avisar quando o contador ainda não tem .pfx vinculado.
        """
        query = f"""
            SELECT {ClienteContador._COLS_CONTADOR}
            FROM clientes c
            LEFT JOIN dfe_certificados dc ON dc.cliente_id = c.id
            WHERE c.is_contador = 1 AND c.situacao = 'ATIVO'
            ORDER BY c.nome_razao_social
        """
        return execute_query(query, fetch=True) or []

    @staticmethod
    def contadores_do_cliente(cliente_id):
        """Contadores vinculados a este cliente (+ finalidade e estado do cert)."""
        query = f"""
            SELECT cc.id AS vinculo_id, cc.finalidade, cc.criado_em,
                   {ClienteContador._COLS_CONTADOR}
            FROM cliente_contadores cc
            JOIN clientes c ON c.id = cc.contador_id
            LEFT JOIN dfe_certificados dc ON dc.cliente_id = c.id
            WHERE cc.cliente_id = %s
            ORDER BY c.nome_razao_social
        """
        return execute_query(query, (cliente_id,), fetch=True) or []

    @staticmethod
    def vincular_contador(cliente_id, contador_id, finalidade=None):
        """Liga um contador ao cliente. Devolve ``{'ok': bool, 'erro': str|None}``.

        GUARDAS (nesta ordem):
          1. não vincula o cadastro a ele mesmo;
          2. o alvo precisa existir e ter ``is_contador=1``;
          3. o UNIQUE impede duplicar — aqui vira mensagem, não erro 500.
        """
        try:
            cliente_id, contador_id = int(cliente_id), int(contador_id)
        except (TypeError, ValueError):
            return {'ok': False, 'erro': 'Identificadores inválidos.'}

        if cliente_id == contador_id:
            return {'ok': False,
                    'erro': 'Um cadastro não pode ser contador de si mesmo.'}

        alvo = execute_query(
            "SELECT id, nome_razao_social, is_contador FROM clientes WHERE id = %s",
            (contador_id,), fetch=True, fetch_one=True,
        )
        if not alvo:
            return {'ok': False, 'erro': 'Contador não encontrado.'}
        if not alvo.get('is_contador'):
            return {'ok': False,
                    'erro': f'O cadastro "{alvo["nome_razao_social"]}" não está marcado '
                            'como Contador. Marque-o na tela do cadastro antes de vincular.'}

        ja = execute_query(
            "SELECT id FROM cliente_contadores WHERE cliente_id = %s AND contador_id = %s",
            (cliente_id, contador_id), fetch=True, fetch_one=True,
        )
        if ja:
            return {'ok': False, 'erro': 'Esse contador já está vinculado a este cliente.'}

        rid = execute_query(
            "INSERT INTO cliente_contadores (cliente_id, contador_id, finalidade) "
            "VALUES (%s, %s, %s)",
            (cliente_id, contador_id, (finalidade or '').strip()[:30] or None),
        )
        if rid is None:
            return {'ok': False, 'erro': 'Falha ao gravar o vínculo.'}
        return {'ok': True, 'erro': None, 'contador': alvo['nome_razao_social']}

    @staticmethod
    def desvincular_contador(cliente_id, contador_id):
        """Remove o vínculo. Idempotente: remover o que não existe não é erro."""
        r = execute_query(
            "DELETE FROM cliente_contadores WHERE cliente_id = %s AND contador_id = %s",
            (cliente_id, contador_id),
        )
        return {'ok': r is not None,
                'erro': None if r is not None else 'Falha ao remover o vínculo.'}

    @staticmethod
    def marcar_como_contador(cliente_id, valor):
        """Liga/desliga ``clientes.is_contador``.

        GUARDA ao DESMARCAR: se o cadastro ainda for contador de alguém, recusa —
        senão sobrariam vínculos apontando para um não-contador (e a Parte 2
        passaria a ler lixo). Desvincule dos clientes primeiro.
        """
        valor = 1 if str(valor).strip().lower() in ('1', 'true', 'on', 'sim') else 0

        if valor == 0:
            n = (execute_query(
                "SELECT COUNT(*) AS cnt FROM cliente_contadores WHERE contador_id = %s",
                (cliente_id,), fetch=True, fetch_one=True,
            ) or {}).get('cnt', 0)
            if n:
                return {'ok': False,
                        'erro': f'Este cadastro ainda é contador de {n} cliente(s). '
                                'Remova os vínculos antes de desmarcar.'}

        r = execute_query("UPDATE clientes SET is_contador = %s WHERE id = %s",
                          (valor, cliente_id))
        return {'ok': r is not None, 'valor': valor,
                'erro': None if r is not None else 'Falha ao gravar.'}
