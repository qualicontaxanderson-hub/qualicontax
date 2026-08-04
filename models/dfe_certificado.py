"""Modelo do vínculo de Certificado Digital (tabela dfe_certificados)."""
import os
from datetime import datetime, timezone, timedelta

from utils.db_helper import execute_query

# Fuso de Brasília (mesma base do motor de captura, dfe_captura._hoje_brt): a
# classificação de validade usa a data de -03:00, não a do servidor (UTC no Railway).
_TZ_BR = timezone(timedelta(hours=-3))


class DfeCertificado:
    """Vínculo do certificado digital (.pfx) de uma empresa."""

    @staticmethod
    def classificar_validade(validade):
        """Fonte ÚNICA da regra de alerta de validade (detalhe do cliente, painel de
        status, bloqueio de vínculo): 🔴 ``vencido`` (validade < hoje) · 🟠 ``laranja``
        (vence em <= ``CERT_ALERTA_DIAS``, default 30) · ✅ ``ok``. ``hoje`` é a data de
        Brasília (-03:00). Um cert VENCIDO faz a SEFAZ recusar o mTLS (HTTP 403), então
        os três consumidores dessa regra partem daqui para não divergir.

        Devolve ``{'dias': int|None, 'nivel': str, 'laranja': int}``. ``dias`` é
        negativo quando já venceu e None quando não há validade utilizável
        (``nivel='sem_data'``).
        """
        laranja = max(0, int(os.getenv('CERT_ALERTA_DIAS', '30')))
        if not validade or not hasattr(validade, 'year'):
            return {'dias': None, 'nivel': 'sem_data', 'laranja': laranja}
        dias = (validade - datetime.now(_TZ_BR).date()).days
        if dias < 0:
            nivel = 'vencido'
        elif dias <= laranja:
            nivel = 'laranja'
        else:
            nivel = 'ok'
        return {'dias': dias, 'nivel': nivel, 'laranja': laranja}

    @staticmethod
    def get_by_cliente(cliente_id):
        """Retorna o vínculo do certificado da empresa, ou None.

        Não retorna a senha cifrada (não é usada na tela).
        """
        query = """
            SELECT id, cliente_id, cnpj, tipo_doc, procuracao, dropbox_path,
                   validade, criado_em, atualizado_em
            FROM dfe_certificados
            WHERE cliente_id = %s
        """
        return execute_query(query, (cliente_id,), fetch=True, fetch_one=True)

    @staticmethod
    def get_senha_cifrada(cliente_id):
        """Retorna a senha cifrada (token Fernet, bytes) do certificado, ou None.

        Isolado de :func:`get_by_cliente` (que nunca expõe a senha) porque só a
        rotina de captura precisa decifrá-la para autenticar o mTLS com a SEFAZ.
        """
        row = execute_query(
            "SELECT senha_cifrada FROM dfe_certificados WHERE cliente_id = %s",
            (cliente_id,), fetch=True, fetch_one=True,
        )
        return row['senha_cifrada'] if row else None

    @staticmethod
    def listar_para_captura():
        """Lista os certificados elegíveis para a captura automática de DFe.

        Um registro por empresa com a captura ligada (``modo_automatico = 1`` e
        ``ativo = 1``) e cliente ATIVO, já com os dados da empresa (número, razão,
        CNPJ) e a UF do endereço principal.

        A UF (``uf``) vem ``None`` quando o cliente não tem endereço com estado
        cadastrado. Nesse caso o chamador deve PULAR a captura e sinalizar
        ("cliente sem UF, cadastre para ativar a captura") — o ``cUFAutor`` errado
        causa rejeição na SEFAZ, então nunca se chuta a UF.
        """
        query = """
            SELECT dc.id, dc.cliente_id, dc.cnpj, dc.tipo_doc, dc.procuracao,
                   dc.dropbox_path, dc.validade, dc.modo_automatico, dc.ativo,
                   c.numero_cliente, c.nome_razao_social,
                   ec.estado AS uf, n.ult_consulta, n.proximo_permitido
            FROM dfe_certificados dc
            JOIN clientes c ON c.id = dc.cliente_id
            LEFT JOIN enderecos_clientes ec ON ec.id = (
                SELECT e.id FROM enderecos_clientes e
                WHERE e.cliente_id = dc.cliente_id
                ORDER BY e.principal DESC, e.id ASC
                LIMIT 1
            )
            LEFT JOIN dfe_nsu n ON n.cliente_id = dc.cliente_id
            WHERE dc.modo_automatico = 1
              AND dc.ativo = 1
              AND c.situacao = 'ATIVO'
            ORDER BY COALESCE(n.ult_consulta, '1970-01-01') ASC, c.numero_cliente
        """
        return execute_query(query, fetch=True) or []

    @staticmethod
    def upsert(cliente_id, cnpj, tipo_doc, senha_cifrada, dropbox_path,
               validade=None, procuracao=0):
        """Insere ou atualiza o vínculo (1 certificado vigente por empresa).

        ``procuracao=1`` marca que o titular do .pfx NÃO é a empresa (contador /
        terceiro autorizado). O UNIQUE (cliente_id) mantém 1 certificado por
        empresa: um segundo vínculo SUBSTITUI o anterior (ver certificado_vincular,
        que avisa na tela o que saiu de cena).

        ``modo_automatico``: procuração nasce com 0 (captura desligada — teste
        controlado, liga-se na mão depois). Certificado normal nasce com 1, e num
        REVÍNCULO o valor anterior é PRESERVADO (não religa o que foi desligado).
        """
        query = """
            INSERT INTO dfe_certificados
                (cliente_id, cnpj, tipo_doc, senha_cifrada, dropbox_path, validade,
                 procuracao, modo_automatico)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                cnpj = VALUES(cnpj),
                tipo_doc = VALUES(tipo_doc),
                senha_cifrada = VALUES(senha_cifrada),
                dropbox_path = VALUES(dropbox_path),
                validade = VALUES(validade),
                procuracao = VALUES(procuracao),
                modo_automatico = IF(VALUES(procuracao) = 1, 0, modo_automatico)
        """
        proc = int(procuracao or 0)
        return execute_query(
            query,
            (cliente_id, cnpj, tipo_doc, senha_cifrada, dropbox_path, validade,
             proc, 0 if proc else 1),
        )
