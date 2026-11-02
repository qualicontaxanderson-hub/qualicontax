# -*- coding: utf-8 -*-
"""Cobrança: contas de cobrança (Efí, Bradesco) e boletos — 02/11/2026.

``FinCobrancaConta`` guarda as credenciais CIFRADAS (``utils.cobranca_segredos``)
e devolve o cliente da Efí já autenticado; a tela nunca vê o segredo.
``FinBoleto`` é uma linha por boleto, ligada ao título e à nota.
"""
import json
import logging
from datetime import date, datetime
from decimal import Decimal

from utils.db_helper import execute_query
from utils import cobranca_segredos as seg

logger = logging.getLogger(__name__)

PROVEDORES = {'efi': 'Efí', 'bradesco': 'Bradesco (em breve)'}
AMBIENTES = {'homologacao': 'Teste (homologação)', 'producao': 'Produção — o boleto vale'}


class FinCobrancaConta:

    CAMPOS_TEXTO = ('apelido', 'provedor', 'ambiente', 'fin_conta_id', 'chave_pix', 'juros_mes', 'multa_pct',
                    'dias_baixa', 'dia_vencimento', 'mensagem_padrao')

    @staticmethod
    def listar(empresa_ids=None, apenas_ativas=False):
        cond, params = [], []
        if empresa_ids:
            cond.append(f"c.empresa_id IN ({','.join(['%s'] * len(empresa_ids))})")
            params += list(empresa_ids)
        if apenas_ativas:
            cond.append('c.ativo = 1')
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        linhas = execute_query(
            f"""SELECT c.*, fc.banco_nome, fc.conta AS conta_bancaria, cl.nome_razao_social, cl.numero_cliente,
                       (c.client_id_cif IS NOT NULL) AS tem_client_id,
                       (c.client_secret_cif IS NOT NULL) AS tem_secret,
                       (c.certificado_cif IS NOT NULL) AS tem_certificado
                  FROM fin_cobranca_contas c
                  LEFT JOIN fin_contas fc ON fc.id = c.fin_conta_id
                  JOIN clientes cl ON cl.id = c.empresa_id
                {where}
                 ORDER BY c.ativo DESC, cl.nome_razao_social, c.apelido""", tuple(params), fetch=True) or []
        for l in linhas:
            l['client_id_mascara'] = ''
            if l['tem_client_id']:
                try:
                    l['client_id_mascara'] = seg.mascarar(seg.decifrar_texto(l['client_id_cif']))
                except Exception:
                    l['client_id_mascara'] = 'ilegível (chave mudou?)'
            for k in ('client_id_cif', 'client_secret_cif', 'certificado_cif', 'certificado_senha_cif'):
                l.pop(k, None)
        return linhas

    @staticmethod
    def get(conta_id, com_segredos=False):
        c = execute_query('SELECT * FROM fin_cobranca_contas WHERE id = %s', (conta_id,), fetch=True, fetch_one=True)
        if c and not com_segredos:
            for k in ('client_id_cif', 'client_secret_cif', 'certificado_cif', 'certificado_senha_cif'):
                c.pop(k, None)
        return c

    @staticmethod
    def salvar(empresa_id, dados, conta_id=None, usuario_id=None, client_id=None, client_secret=None,
               certificado=None, certificado_nome=None, certificado_senha=None):
        """Cria ou altera. Segredos só são trocados quando vêm preenchidos (campo vazio = mantém).
        Devolve (id, erro)."""
        apelido = (dados.get('apelido') or '').strip()[:60]
        if not apelido:
            return None, 'Dê um nome à conta de cobrança (ex.: Efí Qualicontax).'
        provedor = dados.get('provedor') if dados.get('provedor') in PROVEDORES else 'efi'
        ambiente = 'producao' if dados.get('ambiente') == 'producao' else 'homologacao'

        def dec(k, padrao):
            v = str(dados.get(k) or '').strip().replace(',', '.')
            try:
                return Decimal(v) if v else Decimal(padrao)
            except Exception:
                return Decimal(padrao)
        fin_conta = dados.get('fin_conta_id')
        fin_conta = int(fin_conta) if str(fin_conta or '').isdigit() else None
        dia = dados.get('dia_vencimento')
        dia = int(dia) if str(dia or '').isdigit() and 1 <= int(dia) <= 31 else None
        dias_baixa = dados.get('dias_baixa')
        dias_baixa = max(0, min(120, int(dias_baixa))) if str(dias_baixa or '').isdigit() else 60
        base = (apelido, provedor, ambiente, fin_conta, (dados.get('chave_pix') or '').strip()[:80] or None,
                dec('juros_mes', '0.00'), dec('multa_pct', '0.00'), dias_baixa, dia,
                (dados.get('mensagem_padrao') or '').strip()[:400] or None)
        sets_seg, vals_seg = [], []
        if client_id:
            sets_seg.append('client_id_cif = %s'); vals_seg.append(seg.cifrar(client_id.strip()))
        if client_secret:
            sets_seg.append('client_secret_cif = %s'); vals_seg.append(seg.cifrar(client_secret.strip()))
        if certificado:
            sets_seg += ['certificado_cif = %s', 'certificado_nome = %s', 'certificado_senha_cif = %s']
            vals_seg += [seg.cifrar(certificado), (certificado_nome or 'certificado.p12')[:120],
                         seg.cifrar(certificado_senha or '')]
        if conta_id:
            sql = ('UPDATE fin_cobranca_contas SET apelido = %s, provedor = %s, ambiente = %s, fin_conta_id = %s, '
                   'chave_pix = %s, juros_mes = %s, multa_pct = %s, dias_baixa = %s, dia_vencimento = %s, '
                   'mensagem_padrao = %s' + ''.join(', ' + s for s in sets_seg)
                   + ', atualizado_em = NOW(), alterado_por = %s WHERE id = %s AND empresa_id = %s')
            r = execute_query(sql, base + tuple(vals_seg) + (usuario_id, conta_id, empresa_id))
            return (conta_id, None) if r is not None else (None, 'Não foi possível salvar.')
        if not client_id or not client_secret:
            return None, 'Client ID e Client Secret são obrigatórios na primeira vez.'
        cols = ['empresa_id', 'apelido', 'provedor', 'ambiente', 'fin_conta_id', 'chave_pix', 'juros_mes',
                'multa_pct', 'dias_baixa', 'dia_vencimento', 'mensagem_padrao', 'webhook_segredo', 'alterado_por']
        vals = [empresa_id, *base, seg.novo_segredo_webhook(), usuario_id]
        for s, v in zip(sets_seg, vals_seg):
            cols.append(s.split(' = ')[0]); vals.append(v)
        r = execute_query(f"INSERT INTO fin_cobranca_contas ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                          tuple(vals))
        return (r, None) if r else (None, 'Não foi possível salvar.')

    @staticmethod
    def alternar(conta_id, empresa_id):
        return execute_query('UPDATE fin_cobranca_contas SET ativo = NOT ativo, atualizado_em = NOW() '
                             ' WHERE id = %s AND empresa_id = %s', (conta_id, empresa_id))

    @staticmethod
    def registrar_teste(conta_id, resultado):
        execute_query('UPDATE fin_cobranca_contas SET testado_em = NOW(), teste_resultado = %s WHERE id = %s',
                      ((resultado or '')[:255], conta_id))

    @staticmethod
    def cliente_efi(conta_id):
        """Cliente da Efí autenticado para esta conta. Decifra só aqui, só na hora de usar."""
        from utils.integrations.efi.client import ContaEfi, Efi
        c = FinCobrancaConta.get(conta_id, com_segredos=True)
        if not c:
            raise ValueError('Conta de cobrança não encontrada.')
        if c['provedor'] != 'efi':
            raise ValueError('Esta conta não é da Efí.')
        conta = ContaEfi(client_id=seg.decifrar_texto(c['client_id_cif']),
                         client_secret=seg.decifrar_texto(c['client_secret_cif']),
                         ambiente=c['ambiente'], p12=seg.decifrar_bytes(c['certificado_cif']),
                         p12_senha=seg.decifrar_texto(c['certificado_senha_cif']))
        return Efi(conta), c

    @staticmethod
    def por_webhook(segredo):
        return execute_query('SELECT * FROM fin_cobranca_contas WHERE webhook_segredo = %s AND ativo = 1',
                             (segredo,), fetch=True, fetch_one=True)

    @staticmethod
    def padrao_da_empresa(empresa_id):
        """A conta ativa da empresa (produção antes de homologação)."""
        return execute_query(
            "SELECT * FROM fin_cobranca_contas WHERE empresa_id = %s AND ativo = 1 "
            " ORDER BY (ambiente = 'producao') DESC, id LIMIT 1", (empresa_id,), fetch=True, fetch_one=True)


class FinBoleto:

    @staticmethod
    def listar(empresa_ids=None, situacao=None, de=None, ate=None, busca=None, limite=300):
        cond, params = [], []
        if empresa_ids:
            cond.append(f"b.empresa_id IN ({','.join(['%s'] * len(empresa_ids))})")
            params += list(empresa_ids)
        if situacao and situacao != 'todas':
            if situacao == 'abertos':
                cond.append("b.situacao IN ('waiting', 'unpaid', 'new')")
            else:
                cond.append('b.situacao = %s'); params.append(situacao)
        if de:
            cond.append('b.vencimento >= %s'); params.append(de)
        if ate:
            cond.append('b.vencimento <= %s'); params.append(ate)
        if busca:
            cond.append('(b.pagador_nome LIKE %s OR b.pagador_doc LIKE %s OR b.charge_id LIKE %s)')
            params += [f'%{busca}%', f'%{busca}%', f'%{busca}%']
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        return execute_query(
            f"""SELECT b.*, c.apelido AS conta_apelido, n.serie, n.numero_dps, n.numero_nfse,
                       cl.nome_razao_social AS empresa_nome
                  FROM fin_boletos b
                  JOIN fin_cobranca_contas c ON c.id = b.conta_cobranca_id
                  JOIN clientes cl ON cl.id = b.empresa_id
                  LEFT JOIN nfse_emitidas n ON n.id = b.nfse_id
                {where}
                 ORDER BY b.vencimento DESC, b.id DESC LIMIT %s""", tuple(params) + (int(limite),), fetch=True) or []

    @staticmethod
    def get(boleto_id):
        return execute_query(
            """SELECT b.*, c.apelido AS conta_apelido, c.webhook_segredo
                 FROM fin_boletos b JOIN fin_cobranca_contas c ON c.id = b.conta_cobranca_id
                WHERE b.id = %s""", (boleto_id,), fetch=True, fetch_one=True)

    @staticmethod
    def da_nota(nfse_id):
        return execute_query("SELECT * FROM fin_boletos WHERE nfse_id = %s AND situacao <> 'erro' ORDER BY id DESC LIMIT 1",
                             (nfse_id,), fetch=True, fetch_one=True)

    @staticmethod
    def por_charge(provedor, ambiente, charge_id):
        return execute_query('SELECT * FROM fin_boletos WHERE provedor = %s AND ambiente = %s AND charge_id = %s',
                             (provedor, ambiente, str(charge_id)), fetch=True, fetch_one=True)

    @staticmethod
    def rascunho(empresa_id, conta, titulo_id, nfse_id, valor, vencimento, pagador, itens, mensagem, usuario_id):
        return execute_query(
            """INSERT INTO fin_boletos (empresa_id, conta_cobranca_id, titulo_id, nfse_id, provedor, ambiente,
                                        situacao, valor, vencimento, pagador_doc, pagador_nome, pagador_email,
                                        itens_json, mensagem, criado_por)
               VALUES (%s, %s, %s, %s, %s, %s, 'rascunho', %s, %s, %s, %s, %s, %s, %s, %s)""",
            (empresa_id, conta['id'], titulo_id, nfse_id, conta['provedor'], conta['ambiente'], valor, vencimento,
             pagador.get('documento'), pagador.get('nome'), pagador.get('email'),
             json.dumps(itens, ensure_ascii=False), mensagem, usuario_id))

    @staticmethod
    def gravar_resposta(boleto_id, data, custom_id):
        """Depois do one-step: o que a Efí devolveu."""
        pix = data.get('pix') or {}
        pdf = data.get('pdf') or {}
        execute_query(
            """UPDATE fin_boletos SET charge_id = %s, custom_id = %s, situacao = %s, linha_digitavel = %s,
                      link = %s, pdf_url = %s, pix_copia_cola = %s, pix_qrcode = %s, raw_resposta = %s,
                      erro = NULL, atualizado_em = NOW()
                WHERE id = %s""",
            (str(data.get('charge_id') or ''), custom_id, data.get('status') or 'waiting', data.get('barcode'),
             data.get('billet_link') or data.get('link'), pdf.get('charge'), pix.get('qrcode'),
             pix.get('qrcode_image'), json.dumps(data, ensure_ascii=False), boleto_id))

    @staticmethod
    def gravar_erro(boleto_id, erro):
        execute_query("UPDATE fin_boletos SET situacao = 'erro', erro = %s, atualizado_em = NOW() WHERE id = %s",
                      ((erro or '')[:2000], boleto_id))

    @staticmethod
    def atualizar_situacao(boleto_id, situacao, pago_em=None, valor_pago=None, evento_id=None, token=None):
        execute_query(
            """UPDATE fin_boletos SET situacao = %s, pago_em = COALESCE(%s, pago_em),
                      valor_pago = COALESCE(%s, valor_pago), ultimo_evento_id = COALESCE(%s, ultimo_evento_id),
                      notificacao_token = COALESCE(%s, notificacao_token), atualizado_em = NOW()
                WHERE id = %s""", (situacao, pago_em, valor_pago, evento_id, token, boleto_id))

    @staticmethod
    def resumo(empresa_ids=None):
        cond, params = '', ()
        if empresa_ids:
            cond = f"WHERE empresa_id IN ({','.join(['%s'] * len(empresa_ids))})"
            params = tuple(empresa_ids)
        r = execute_query(
            f"""SELECT SUM(situacao IN ('waiting', 'new')) AS abertos,
                       SUM(situacao = 'unpaid') AS vencidos,
                       SUM(situacao = 'paid' AND pago_em >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')) AS pagos_mes,
                       COALESCE(SUM(CASE WHEN situacao IN ('waiting', 'new', 'unpaid') THEN valor END), 0) AS valor_aberto,
                       COALESCE(SUM(CASE WHEN situacao = 'paid' AND pago_em >= DATE_FORMAT(CURDATE(), '%%Y-%%m-01')
                                         THEN COALESCE(valor_pago, valor) END), 0) AS valor_pago_mes
                  FROM fin_boletos {cond}""", params, fetch=True, fetch_one=True) or {}
        return {k: (r.get(k) or 0) for k in ('abertos', 'vencidos', 'pagos_mes', 'valor_aberto', 'valor_pago_mes')}
