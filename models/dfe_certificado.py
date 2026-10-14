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

    # ------------------------------------------------------------------ RAIZ
    # Matriz e filiais (14/10/2026): a SEFAZ aceita autenticar com o certificado
    # de QUALQUER CNPJ da mesma raiz (8 dígitos) e consultar o CNPJ da filial —
    # é o que a NFS-e ADN já fazia (nfse_adn/client.py resolver_certificado).
    # Filial sem certificado próprio captura NF-e e CT-e pelo da raiz; os
    # botões dela (captura e Ciência) moram em dfe_captura_raiz.

    _SQL_RAIZ = """
        SELECT ce.id, ce.cliente_id, ce.cnpj, ce.tipo_doc, ce.procuracao, ce.dropbox_path,
               ce.validade, ce.manifesta_ciencia, d.numero_cliente AS dono_numero,
               d.nome_razao_social AS dono_nome
          FROM dfe_certificados ce
          JOIN clientes d ON d.id = ce.cliente_id
         WHERE ce.ativo = 1 AND ce.dropbox_path IS NOT NULL AND ce.validade >= CURDATE()
           AND ce.cliente_id <> %s
           AND LEFT(LPAD(REGEXP_REPLACE(ce.cnpj, '[^0-9]', ''), 14, '0'), 8) = %s
      ORDER BY SUBSTRING(LPAD(REGEXP_REPLACE(ce.cnpj, '[^0-9]', ''), 14, '0'), 9, 4) = '0001' DESC,
               ce.validade DESC
         LIMIT 1"""

    @staticmethod
    def cert_da_raiz(cliente_id):
        """Certificado VÁLIDO de outra empresa com a mesma raiz de CNPJ (a
        matriz primeiro), ou None. O titular é a própria raiz — procuração
        (titular = contador) nunca casa, porque o filtro é pelo CNPJ do .pfx."""
        alvo = execute_query('SELECT cpf_cnpj, tipo_pessoa FROM clientes WHERE id = %s',
                             (cliente_id,), fetch=True, fetch_one=True) or {}
        dig = ''.join(ch for ch in str(alvo.get('cpf_cnpj') or '') if ch.isdigit())
        if alvo.get('tipo_pessoa') != 'PJ' or len(dig) != 14:
            return None
        return execute_query(DfeCertificado._SQL_RAIZ, (cliente_id, dig[:8]), fetch=True, fetch_one=True)

    @staticmethod
    def config_raiz(cliente_id, dono=None):
        """Botões da filial que captura pela raiz. Sem linha = padrão: captura
        LIGADA e Ciência igual à do certificado que ela usa (o da matriz)."""
        r = execute_query('SELECT modo_automatico, manifesta_ciencia, manifesta_por, manifesta_em '
                          '  FROM dfe_captura_raiz WHERE cliente_id = %s',
                          (cliente_id,), fetch=True, fetch_one=True) or {}
        dono = dono if dono is not None else (DfeCertificado.cert_da_raiz(cliente_id) or {})
        mc = r.get('manifesta_ciencia')
        return {'modo_automatico': True if r.get('modo_automatico') is None else bool(r['modo_automatico']),
                'manifesta_ciencia': bool(dono.get('manifesta_ciencia')) if mc is None else bool(mc),
                'manifesta_por': r.get('manifesta_por'), 'manifesta_em': r.get('manifesta_em')}

    @staticmethod
    def gravar_config_raiz(cliente_id, usuario_id, modo_automatico=None, manifesta_ciencia=None, usuario_nome=None):
        """Liga/desliga um botão da filial (cria a linha na primeira vez)."""
        if modo_automatico is not None:
            execute_query('INSERT INTO dfe_captura_raiz (cliente_id, modo_automatico, alterado_por, alterado_em) '
                          'VALUES (%s, %s, %s, NOW()) ON DUPLICATE KEY UPDATE '
                          'modo_automatico = VALUES(modo_automatico), alterado_por = VALUES(alterado_por), '
                          'alterado_em = NOW()', (cliente_id, 1 if modo_automatico else 0, usuario_id))
        if manifesta_ciencia is not None:
            execute_query('INSERT INTO dfe_captura_raiz (cliente_id, manifesta_ciencia, manifesta_por, manifesta_em, '
                          '                              alterado_por, alterado_em) '
                          'VALUES (%s, %s, %s, NOW(), %s, NOW()) ON DUPLICATE KEY UPDATE '
                          'manifesta_ciencia = VALUES(manifesta_ciencia), manifesta_por = VALUES(manifesta_por), '
                          'manifesta_em = NOW(), alterado_por = VALUES(alterado_por), alterado_em = NOW()',
                          (cliente_id, 1 if manifesta_ciencia else 0, (usuario_nome or '')[:120] or None, usuario_id))

    @staticmethod
    def listar_filiais_pela_raiz(servico='nfe'):
        """Filiais (PJ sem certificado próprio ativo) com certificado válido na
        raiz — o MESMO formato de listar_para_captura. ``servico='cte'`` ignora o
        botão de captura (ele é da NF-e; o CT-e não é afetado)."""
        rows = execute_query(
            """SELECT ce.id, c.id AS cliente_id, ce.cnpj, ce.tipo_doc, ce.procuracao,
                      ce.dropbox_path, ce.validade, COALESCE(r.modo_automatico, 1) AS modo_automatico,
                      ce.ativo, c.numero_cliente, c.nome_razao_social,
                      (SELECT e.estado FROM enderecos_clientes e WHERE e.cliente_id = c.id
                        ORDER BY e.principal DESC, e.id ASC LIMIT 1) AS uf,
                      n.ult_consulta, n.proximo_permitido, n.ult_nsu, n.max_nsu,
                      1 AS pela_raiz, ce.cliente_id AS raiz_dono_id
                 FROM clientes c
                 JOIN dfe_certificados ce
                   ON ce.ativo = 1 AND ce.dropbox_path IS NOT NULL AND ce.validade >= CURDATE()
                  AND ce.cliente_id <> c.id
                  AND LEFT(LPAD(REGEXP_REPLACE(ce.cnpj, '[^0-9]', ''), 14, '0'), 8)
                      = LEFT(REGEXP_REPLACE(c.cpf_cnpj, '[^0-9]', ''), 8)
                 LEFT JOIN dfe_certificados prop ON prop.cliente_id = c.id AND prop.ativo = 1
                 LEFT JOIN dfe_captura_raiz r ON r.cliente_id = c.id
                 LEFT JOIN dfe_nsu n ON n.cliente_id = c.id
                WHERE c.tipo_pessoa = 'PJ' AND prop.id IS NULL
                  AND LENGTH(REGEXP_REPLACE(c.cpf_cnpj, '[^0-9]', '')) = 14
             ORDER BY c.id, SUBSTRING(LPAD(REGEXP_REPLACE(ce.cnpj, '[^0-9]', ''), 14, '0'), 9, 4) = '0001' DESC,
                      ce.validade DESC""", fetch=True) or []
        vistos, out = set(), []
        for r in rows:                      # uma linha por filial (o 1º certificado da raiz)
            if r['cliente_id'] in vistos:
                continue
            vistos.add(r['cliente_id'])
            if servico != 'cte' and not r['modo_automatico']:
                continue
            out.append(r)
        return out

    @staticmethod
    def listar_para_captura(servico='nfe'):
        """Lista os certificados elegíveis para a captura automática de DFe.

        Inclui as FILIAIS sem certificado próprio que capturam pelo da raiz
        (``pela_raiz=1``). ``servico='cte'`` ignora o botão da NF-e.

        Um registro por empresa com a captura ligada (``modo_automatico = 1`` e
        ``ativo = 1``) e cliente ATIVO — ou inativo com o certificado ainda
        válido (a captura segue enquanto houver certificado), já com os dados da empresa (número, razão,
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
                   ec.estado AS uf, n.ult_consulta, n.proximo_permitido,
                   n.ult_nsu, n.max_nsu
            FROM dfe_certificados dc
            JOIN clientes c ON c.id = dc.cliente_id
            LEFT JOIN enderecos_clientes ec ON ec.id = (
                SELECT e.id FROM enderecos_clientes e
                WHERE e.cliente_id = dc.cliente_id
                ORDER BY e.principal DESC, e.id ASC
                LIMIT 1
            )
            LEFT JOIN dfe_nsu n ON n.cliente_id = dc.cliente_id
            WHERE {filtro_botao}dc.ativo = 1
              -- empresa INATIVA continua capturando enquanto o certificado
              -- valer (08/10/2026): inativar é para obrigações e listas
              AND (c.situacao = 'ATIVO' OR dc.validade >= CURDATE())
            ORDER BY COALESCE(n.ult_consulta, '1970-01-01') ASC, c.numero_cliente
        """
        # O botão "Captura NF-e" (modo_automatico) é da NF-e: o CT-e não é
        # afetado (14/10/2026 — antes desligava os dois, contra o que a tela dizia).
        query = query.replace('{filtro_botao}', '' if servico == 'cte' else 'dc.modo_automatico = 1 AND ')
        proprias = execute_query(query, fetch=True) or []
        # + filiais sem certificado próprio, pelo certificado da raiz (14/10/2026)
        try:
            filiais = DfeCertificado.listar_filiais_pela_raiz(servico)
        except Exception:                      # tabela nova ainda não migrada: segue sem elas
            filiais = []
        todas = proprias + filiais
        todas.sort(key=lambda r: (str(r.get('ult_consulta') or '1970-01-01'), str(r.get('numero_cliente') or '')))
        return todas

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
