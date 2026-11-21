# -*- coding: utf-8 -*-
"""Orçamentos do Comercial (21/11/2026).

Anderson (20/11/2026): "quase 100% dos serviços serão feitos aqui no Comercial e migrado para o Financeiro, para
controle". O orçamento nasce aqui (cliente cadastrado ou avulso), vai para aprovação, o administrador aprova, a
pessoa manda ao cliente; feito o serviço, ela manda para a cobrança e o lançamento nasce em Financeiro › Lançamentos
com a linha do tempo inteira. Tabelas: com_orcamentos, com_orcamento_itens, com_orcamento_eventos.
"""
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from utils.db_helper import execute_query

# status → (rótulo, cor do molde tx-*, ícone)
STATUS = {
    'RASCUNHO':  ('Rascunho',          'tx-perd', 'fa-pen-ruler'),
    'APROVACAO': ('Para aprovar',      'tx-venc', 'fa-stamp'),
    'APROVADO':  ('Aprovado',          'tx-ok',   'fa-circle-check'),
    'COBRANCA':  ('Em cobrança',       'tx-hoje', 'fa-barcode'),
    'CONCLUIDO': ('Concluído',         'tx-fut',  'fa-flag-checkered'),
    'REPROVADO': ('Reprovado',         'tx-ina',  'fa-rotate-left'),
    'CANCELADO': ('Cancelado',         'tx-perd', 'fa-ban'),
}
COMO = {'BOLETO_NOTA': 'boleto + nota fiscal', 'BOLETO': 'só boleto', 'MENSAL': 'na mensalidade'}
COMISSAO = {'PADRAO': 'padrão da pessoa', 'PCT': '%', 'FIXO': 'valor fixo', 'SEM': 'sem comissão'}


def _d(v):
    if v is None or v == '':
        return None
    if isinstance(v, Decimal):
        return v
    s = str(v).strip().replace('R$', '').replace(' ', '')
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    try:
        return Decimal(s).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise ValueError('Valor inválido: %s' % v)


class Orcamento:
    SELECT = """SELECT o.*, c.nome_razao_social AS cliente_nome, c.numero_cliente AS cliente_num, c.cpf_cnpj AS cliente_doc,
                       COALESCE(c.avulso, 0) AS cliente_avulso, c.email AS cliente_email, c.telefone AS cliente_fone,
                       u.nome AS criado_por_nome, a.nome AS aprovado_por_nome
                  FROM com_orcamentos o
                  JOIN clientes c ON c.id = o.cliente_id
                  LEFT JOIN usuarios u ON u.id = o.criado_por
                  LEFT JOIN usuarios a ON a.id = o.aprovado_por"""

    # ---------------- leitura ----------------
    @staticmethod
    def get(oid):
        return execute_query(Orcamento.SELECT + ' WHERE o.id = %s', (oid,), fetch=True, fetch_one=True)

    @staticmethod
    def por_token(token):
        return execute_query(Orcamento.SELECT + ' WHERE o.token = %s', (token,), fetch=True, fetch_one=True)

    @staticmethod
    def listar(empresa_id=2, criado_por=None, status=None):
        sql, p = Orcamento.SELECT + ' WHERE o.empresa_id = %s', [empresa_id]
        if criado_por:
            sql += ' AND o.criado_por = %s'; p.append(criado_por)
        if status:
            sql += ' AND o.status = %s'; p.append(status)
        sql += ' ORDER BY o.id DESC'
        linhas = execute_query(sql, tuple(p), fetch=True) or []
        if linhas:
            ids = ','.join(str(l['id']) for l in linhas)
            itens = execute_query('SELECT * FROM com_orcamento_itens WHERE orcamento_id IN (%s) ORDER BY orcamento_id, ordem, id' % ids, fetch=True) or []
            por = {}
            for i in itens:
                por.setdefault(i['orcamento_id'], []).append(i)
            for l in linhas:
                l['itens'] = por.get(l['id'], [])
        return linhas

    @staticmethod
    def itens(oid):
        return execute_query('SELECT * FROM com_orcamento_itens WHERE orcamento_id = %s ORDER BY ordem, id', (oid,), fetch=True) or []

    @staticmethod
    def eventos(oid):
        return execute_query('SELECT * FROM com_orcamento_eventos WHERE orcamento_id = %s ORDER BY quando, id', (oid,), fetch=True) or []

    # ---------------- escrita ----------------
    @staticmethod
    def evento(oid, tipo, texto, usuario=None):
        uid = getattr(usuario, 'id', None) if usuario is not None else None
        nome = getattr(usuario, 'nome', None) if usuario is not None else None
        execute_query('INSERT INTO com_orcamento_eventos (orcamento_id, usuario_id, usuario_nome, tipo, texto) VALUES (%s, %s, %s, %s, %s)',
                      (oid, uid, nome, tipo, (texto or '')[:400]), fetch=False)

    @staticmethod
    def _itens_do_form(f):
        """item_servico_id[], item_descricao[], item_qtd[], item_valor[] → lista de itens validada (com emite_nota do catálogo)."""
        from models.nfse_emissao import NfseServico
        ids, descs, qtds, vals = (f.getlist('item_servico_id'), f.getlist('item_descricao'), f.getlist('item_qtd'), f.getlist('item_valor'))
        catalogo = {s['id']: s for s in NfseServico.listar(2)}
        itens = []
        for i in range(max(len(ids), len(vals))):
            sid = ids[i] if i < len(ids) else ''
            val = vals[i] if i < len(vals) else ''
            if not str(sid).strip() and not str(val).strip():
                continue
            s = catalogo.get(int(sid)) if str(sid).isdigit() else None
            if not s:
                raise ValueError('Item %d: escolha o serviço na lista.' % (i + 1))
            q = _d(qtds[i] if i < len(qtds) and str(qtds[i]).strip() else '1') or Decimal('1')
            v = _d(val)
            if v is None or v <= 0:
                raise ValueError('Item %d (%s): informe o valor.' % (i + 1, s['nome']))
            desc = ' '.join((descs[i] if i < len(descs) else '').split())[:200] or s['nome']
            itens.append({'servico_id': s['id'], 'descricao': desc, 'quantidade': q, 'valor_unitario': v,
                          'valor_total': (q * v).quantize(Decimal('0.01')), 'emite_nota': 0 if str(s.get('emite_nota', 1)) == '0' else 1})
        if not itens:
            raise ValueError('Ponha ao menos um serviço no orçamento.')
        return itens

    @staticmethod
    def _dados_do_form(f):
        cid = f.get('cliente_id', type=int)
        if not cid:
            raise ValueError('Escolha a empresa (cliente cadastrado ou avulso).')
        como = f.get('como') if f.get('como') in COMO else None
        parcelas = f.get('parcelas', type=int) or None
        if parcelas and not (1 <= parcelas <= 12):
            raise ValueError('Parcelas: de 1 a 12.')
        ct = f.get('comissao_tipo') if f.get('comissao_tipo') in COMISSAO else 'PADRAO'
        cv = _d(f.get('comissao_valor')) if ct in ('PCT', 'FIXO') else None
        if ct in ('PCT', 'FIXO') and (cv is None or cv <= 0 or (ct == 'PCT' and cv > 100)):
            raise ValueError('Informe o %% ou o valor da comissão pedida.')
        dias = f.get('validade_dias', type=int)
        validade = date.today() + timedelta(days=dias) if dias else None
        return {'cliente_id': cid, 'titulo': ' '.join((f.get('titulo') or '').split())[:160] or None, 'como': como, 'parcelas': parcelas,
                'comissao_tipo': ct, 'comissao_valor': cv, 'validade': validade,
                'observacao': (f.get('observacao') or '').strip()[:4000].upper() or None,
                'nota_interna': (f.get('nota_interna') or '').strip()[:2000] or None}

    @staticmethod
    def criar(f, usuario, empresa_id=2):
        d = Orcamento._dados_do_form(f)
        itens = Orcamento._itens_do_form(f)
        total = sum((i['valor_total'] for i in itens), Decimal('0'))
        oid = execute_query("""INSERT INTO com_orcamentos (empresa_id, cliente_id, titulo, status, validade, como, parcelas, comissao_tipo,
                                   comissao_valor, total, observacao, nota_interna, token, criado_por)
                               VALUES (%s, %s, %s, 'RASCUNHO', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (empresa_id, d['cliente_id'], d['titulo'], d['validade'], d['como'], d['parcelas'], d['comissao_tipo'],
                             d['comissao_valor'], total, d['observacao'], d['nota_interna'], secrets.token_urlsafe(24)[:40], usuario.id), fetch=False)
        execute_query('UPDATE com_orcamentos SET numero = %s WHERE id = %s', ('ORC-%06d' % oid, oid), fetch=False)
        Orcamento._gravar_itens(oid, itens)
        Orcamento.evento(oid, 'criado', 'Orçamento montado: %d serviço%s, total R$ %s' % (len(itens), 's' if len(itens) != 1 else '', _brl(total)), usuario)
        return oid

    @staticmethod
    def atualizar(oid, f, usuario):
        o = Orcamento.get(oid)
        if not o or o['status'] not in ('RASCUNHO', 'REPROVADO'):
            raise ValueError('Só rascunho ou reprovado pode ser alterado.')
        d = Orcamento._dados_do_form(f)
        itens = Orcamento._itens_do_form(f)
        total = sum((i['valor_total'] for i in itens), Decimal('0'))
        execute_query("""UPDATE com_orcamentos SET cliente_id = %s, titulo = %s, validade = %s, como = %s, parcelas = %s, comissao_tipo = %s,
                             comissao_valor = %s, total = %s, observacao = %s, nota_interna = %s, status = 'RASCUNHO', motivo = NULL WHERE id = %s""",
                      (d['cliente_id'], d['titulo'], d['validade'], d['como'], d['parcelas'], d['comissao_tipo'], d['comissao_valor'], total,
                       d['observacao'], d['nota_interna'], oid), fetch=False)
        execute_query('DELETE FROM com_orcamento_itens WHERE orcamento_id = %s', (oid,), fetch=False)
        Orcamento._gravar_itens(oid, itens)
        Orcamento.evento(oid, 'editado', 'Orçamento alterado: %d serviço%s, total R$ %s' % (len(itens), 's' if len(itens) != 1 else '', _brl(total)), usuario)
        return oid

    @staticmethod
    def _gravar_itens(oid, itens):
        for n, i in enumerate(itens, 1):
            execute_query("""INSERT INTO com_orcamento_itens (orcamento_id, servico_id, descricao, quantidade, valor_unitario, valor_total, emite_nota, ordem)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                          (oid, i['servico_id'], i['descricao'], i['quantidade'], i['valor_unitario'], i['valor_total'], i['emite_nota'], n), fetch=False)

    @staticmethod
    def mudar(oid, status, usuario, tipo, texto, **campos):
        """Troca o status gravando os campos extras (aprovado_por, motivo...) e o evento da linha do tempo."""
        sets, vals = ['status = %s'], [status]
        for k, v in campos.items():
            sets.append('%s = %%s' % k); vals.append(v)
        vals.append(oid)
        execute_query('UPDATE com_orcamentos SET %s WHERE id = %%s' % ', '.join(sets), tuple(vals), fetch=False)
        Orcamento.evento(oid, tipo, texto, usuario)

    # ---------------- apoio ----------------
    @staticmethod
    def contagens(empresa_id=2, criado_por=None):
        sql, p = 'SELECT status, COUNT(*) n, COALESCE(SUM(total), 0) v FROM com_orcamentos WHERE empresa_id = %s', [empresa_id]
        if criado_por:
            sql += ' AND criado_por = %s'; p.append(criado_por)
        sql += ' GROUP BY status'
        return {r['status']: (int(r['n']), float(r['v'])) for r in execute_query(sql, tuple(p), fetch=True) or []}

    @staticmethod
    def vencido(o):
        return bool(o.get('validade') and o['status'] in ('APROVADO',) and o['validade'] < date.today())


def _brl(v):
    """1234.5 → '1.234,50'."""
    return '{:,.2f}'.format(Decimal(str(v or 0))).replace(',', 'X').replace('.', ',').replace('X', '.')
