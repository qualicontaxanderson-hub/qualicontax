# -*- coding: utf-8 -*-
"""Orçamentos do Comercial (21/11/2026).

Anderson (20/11/2026): "quase 100% dos serviços serão feitos aqui no Comercial e migrado para o Financeiro, para
controle". O orçamento nasce aqui (cliente cadastrado ou avulso), vai para aprovação, o administrador aprova, a
pessoa manda ao cliente; feito o serviço, ela manda para a cobrança e o lançamento nasce em Financeiro › Lançamentos
com a linha do tempo inteira. Tabelas: com_orcamentos, com_orcamento_itens, com_orcamento_eventos.
"""
import hashlib
import logging
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from utils.db_helper import execute_query

# status → (rótulo, cor do molde tx-*, ícone)
STATUS = {
    'RASCUNHO':  ('Rascunho',          'tx-perd', 'fa-pen-ruler'),
    'APROVACAO': ('Para aprovar',      'tx-venc', 'fa-stamp'),
    'APROVADO':  ('Aprovado',          'tx-ok',   'fa-circle-check'),
    'ACEITO':    ('Aceito pelo cliente', 'tx-ok', 'fa-file-signature'),
    'COBRANCA':  ('Em cobrança',       'tx-hoje', 'fa-barcode'),
    'CONCLUIDO': ('Concluído',         'tx-fut',  'fa-flag-checkered'),
    'REPROVADO': ('Reprovado',         'tx-ina',  'fa-rotate-left'),
    'CANCELADO': ('Cancelado',         'tx-perd', 'fa-ban'),
}
COMO = {'BOLETO_NOTA': 'boleto + nota fiscal', 'BOLETO': 'só boleto', 'MENSAL': 'na mensalidade'}
COMISSAO = {'PADRAO': 'padrão da pessoa', 'PCT': '%', 'FIXO': 'valor fixo', 'SEM': 'sem comissão'}
_ALFA = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'      # sem 0/O e 1/I, para ler no telefone sem confundir


def novo_numero():
    """ORC-7K3M9Q: 6 letras/números, único (Anderson: "não quero sequencial subindo")."""
    for _ in range(20):
        n = 'ORC-' + ''.join(secrets.choice(_ALFA) for _ in range(6))
        if not execute_query('SELECT id FROM com_orcamentos WHERE numero = %s', (n,), fetch=True):
            return n
    raise RuntimeError('não consegui um número livre')


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
                       COALESCE(c.avulso, 0) AS cliente_avulso, c.email AS cliente_email, c.telefone AS cliente_fone, c.tipo_pessoa AS cliente_tipo,
                       (SELECT GROUP_CONCAT(e.cliente_id ORDER BY e.ordem) FROM com_orcamento_empresas e WHERE e.orcamento_id = o.id AND e.cliente_id <> o.cliente_id) AS empresas_mais_csv,
                       (SELECT GROUP_CONCAT(cl.nome_razao_social ORDER BY e.ordem SEPARATOR '|') FROM com_orcamento_empresas e JOIN clientes cl ON cl.id = e.cliente_id
                         WHERE e.orcamento_id = o.id AND e.cliente_id <> o.cliente_id) AS empresas_mais_nomes,
                       (SELECT a.nome_razao_social FROM clientes a WHERE a.id = o.empresa_nova_id) AS empresa_nova_cliente,
                       (SELECT pg.nome_razao_social FROM clientes pg WHERE pg.id = o.pagador_cliente_id) AS pagador_nome,
                       u.nome AS criado_por_nome, u.email AS criado_por_email, u.telefone AS criado_por_fone, a.nome AS aprovado_por_nome
                  FROM com_orcamentos o
                  JOIN clientes c ON c.id = o.cliente_id
                  LEFT JOIN usuarios u ON u.id = o.criado_por
                  LEFT JOIN usuarios a ON a.id = o.aprovado_por"""

    # ---------------- leitura ----------------
    @staticmethod
    def get(oid):
        return Orcamento._enriquece(execute_query(Orcamento.SELECT + ' WHERE o.id = %s', (oid,), fetch=True, fetch_one=True))

    @staticmethod
    def _enriquece(o):
        """25/11/2026: as outras empresas do orçamento (além da 1ª, que é a da cobrança) prontas para as telas."""
        if not o:
            return o
        o['empresas_mais_ids'] = [int(x) for x in (o.get('empresas_mais_csv') or '').split(',') if str(x).strip().isdigit()]
        o['empresas_mais'] = [x for x in (o.get('empresas_mais_nomes') or '').split('|') if x]
        o['n_empresas'] = 1 + len(o['empresas_mais'])
        # cobrança do grupo (26/11/2026): um boleto para tudo (quem paga) ou um boleto por empresa
        o['empresas'] = [{'id': o['cliente_id'], 'nome': o.get('cliente_nome')}] + [{'id': i, 'nome': n} for i, n in zip(o['empresas_mais_ids'], o['empresas_mais'])]
        o['cobranca_modo'] = (o.get('cobranca_modo') or 'UNICA') if o['n_empresas'] > 1 else 'UNICA'
        o['por_empresa'] = o['cobranca_modo'] == 'POR_EMPRESA'
        o['pagador_id'] = None if o['por_empresa'] else (o.get('pagador_cliente_id') or o['cliente_id'])
        o['pagador_outro'] = bool(o['pagador_id'] and o['pagador_id'] != o['cliente_id'])
        if not o['pagador_outro']:
            o['pagador_nome'] = o.get('cliente_nome')
        return o

    @staticmethod
    def por_token(token):
        return Orcamento._enriquece(execute_query(Orcamento.SELECT + ' WHERE o.token = %s', (token,), fetch=True, fetch_one=True))

    @staticmethod
    def listar(empresa_id=2, criado_por=None, status=None):
        sql, p = Orcamento.SELECT + ' WHERE o.empresa_id = %s', [empresa_id]
        if criado_por:
            sql += ' AND o.criado_por = %s'; p.append(criado_por)
        if status:
            sql += ' AND o.status = %s'; p.append(status)
        sql += ' ORDER BY o.id DESC'
        linhas = execute_query(sql, tuple(p), fetch=True) or []
        for l in linhas:
            Orcamento._enriquece(l)
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
        return execute_query("""SELECT i.*, c.nome_razao_social AS empresa_nome FROM com_orcamento_itens i
                                  LEFT JOIN clientes c ON c.id = i.cliente_id WHERE i.orcamento_id = %s ORDER BY i.ordem, i.id""", (oid,), fetch=True) or []

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
        clis = f.getlist('item_cliente_id')          # de qual empresa é o serviço (cobrança por empresa, 26/11/2026)
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
                          'valor_total': (q * v).quantize(Decimal('0.01')), 'emite_nota': 0 if str(s.get('emite_nota', 1)) == '0' else 1,
                          'cliente_id': int(clis[i]) if i < len(clis) and str(clis[i]).strip().isdigit() else None})
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
        sol = ' '.join((f.get('solicitante_nome') or '').split())[:120] or None
        sol_email = (f.get('solicitante_email') or '').strip().lower()[:160] or None
        sol_fone = ' '.join((f.get('solicitante_fone') or '').split())[:40] or None
        interno = 1 if f.get('interno') == '1' else 0
        if not interno and not sol:
            raise ValueError('Diga quem pediu o serviço do lado do cliente (o solicitante). Se for só controle interno, marque essa opção.')
        if not interno and not sol_email:              # 25/11/2026 — Anderson: só e-mail de contato cadastrado, nada digitado
            raise ValueError('Quem pediu o serviço precisa ter e-mail no cadastro: escolha um contato com e-mail ou inclua o contato.')
        # mais de uma empresa (25/11/2026 — Anderson: "igual temos no contrato"): a 1ª fica em cliente_id (é dela a cobrança)
        mais = []
        for x in (f.get('empresas_mais') or '').split(','):
            if x.strip().isdigit() and int(x) != cid and int(x) not in mais:
                mais.append(int(x))
        nova = ' '.join((f.get('empresa_nova_nome') or '').split())[:160].upper() or None
        # cobrança do grupo (26/11/2026 — Anderson): um boleto para tudo (no nome de quem paga) ou um por empresa
        modo = 'POR_EMPRESA' if (f.get('cobranca_modo') == 'POR_EMPRESA' and mais) else 'UNICA'
        pagador = f.get('pagador_cliente_id', type=int)
        if modo == 'UNICA' and mais and pagador and pagador != cid and pagador not in mais:
            raise ValueError('Quem paga precisa ser uma das empresas do orçamento.')
        if modo != 'UNICA' or not mais or not pagador or pagador == cid:
            pagador = None
        return {'cliente_id': cid, 'cobranca_modo': modo, 'pagador_cliente_id': pagador, 'titulo': ' '.join((f.get('titulo') or '').split())[:160] or None, 'como': como, 'parcelas': parcelas,
                'comissao_tipo': ct, 'comissao_valor': cv, 'validade': validade, 'empresas_mais': mais, 'empresa_nova_nome': nova,
                'solicitante_nome': sol, 'solicitante_email': sol_email, 'solicitante_fone': sol_fone, 'interno': interno,
                'observacao': (f.get('observacao') or '').strip()[:4000].upper() or None,
                'nota_interna': (f.get('nota_interna') or '').strip()[:2000] or None}

    # Serviços que abrem um CNPJ novo (Anderson, 26/11/2026: "constituição de Posto e Abertura de empresas essas são as que
    # tem que aparecer para abertura de CNPJ"). Prefixo do nome do serviço, em maiúsculas; o template recebe a mesma lista
    # (abertura_prefixos) para a caixa reagir igual. "Abertura de Filial" fica de fora: a filial não é empresa nova.
    ABERTURA_PREFIXOS = ('ABERTURA DE EMPRESA', 'CONSTITUI')

    @staticmethod
    def servico_e_abertura(nome):
        n = (nome or '').strip().upper()
        return any(n.startswith(p) for p in Orcamento.ABERTURA_PREFIXOS)

    @staticmethod
    def _abertura_pf(cliente_id, itens):
        """Cliente pessoa física pedindo abertura de CNPJ (ABERTURA_PREFIXOS): o orçamento precisa do nome da empresa nova (25/11/2026)."""
        c = execute_query('SELECT tipo_pessoa, cpf_cnpj FROM clientes WHERE id = %s', (cliente_id,), fetch=True, fetch_one=True) or {}
        pf = c.get('tipo_pessoa') == 'PF' or len(''.join(ch for ch in (c.get('cpf_cnpj') or '') if ch.isdigit())) == 11
        if not pf or not itens:
            return False
        ids = [int(i['servico_id']) for i in itens if i.get('servico_id')]
        if not ids:
            return False
        rows = execute_query('SELECT nome FROM nfse_servicos WHERE id IN (%s)' % ','.join('%s' for _ in ids), tuple(ids), fetch=True) or []
        return any(Orcamento.servico_e_abertura(r['nome']) for r in rows)

    @staticmethod
    def _avulso_abertura(d, usuario):
        """O nome da empresa nova vira um avulso ligado ao CPF (função da sessão do cadastro; enquanto não existir, fica só o nome)."""
        if not d.get('empresa_nova_nome'):
            return None
        try:
            from models.cliente import Cliente
            fn = getattr(Cliente, 'criar_avulso_abertura', None)
            if fn:
                return fn(d['cliente_id'], d['empresa_nova_nome'], getattr(usuario, 'id', None))
        except Exception as e:
            logging.getLogger(__name__).warning('[orcamento] avulso da empresa nova: %s', e)
        return None

    @staticmethod
    def _confere_grupo(d, itens):
        """Um boleto por empresa: cada serviço diz de qual empresa é (uma das do orçamento). Um boleto só: o serviço não tem empresa."""
        ids = [d['cliente_id']] + list(d.get('empresas_mais') or [])
        if d.get('cobranca_modo') == 'POR_EMPRESA':
            for n, i in enumerate(itens, 1):
                if i.get('cliente_id') not in ids:
                    raise ValueError('Um boleto por empresa: diga de qual empresa é o serviço %d (%s).' % (n, i['descricao']))
        else:
            for i in itens:
                i['cliente_id'] = None

    @staticmethod
    def _gravar_empresas(oid, cliente_id, mais):
        execute_query('DELETE FROM com_orcamento_empresas WHERE orcamento_id = %s', (oid,), fetch=False)
        for n, cid in enumerate([cliente_id] + [x for x in (mais or []) if x != cliente_id], 1):
            execute_query('INSERT IGNORE INTO com_orcamento_empresas (orcamento_id, cliente_id, ordem) VALUES (%s, %s, %s)', (oid, cid, n), fetch=False)

    @staticmethod
    def criar(f, usuario, empresa_id=2):
        d = Orcamento._dados_do_form(f)
        itens = Orcamento._itens_do_form(f)
        Orcamento._confere_grupo(d, itens)
        if Orcamento._abertura_pf(d['cliente_id'], itens) and not d['empresa_nova_nome']:
            raise ValueError('Abertura de empresa para pessoa física: diga o nome da empresa nova.')
        total = sum((i['valor_total'] for i in itens), Decimal('0'))
        nova_id = Orcamento._avulso_abertura(d, usuario)
        oid = execute_query("""INSERT INTO com_orcamentos (empresa_id, numero, cliente_id, titulo, status, validade, como, parcelas, comissao_tipo,
                                   comissao_valor, total, observacao, nota_interna, token, criado_por, solicitante_nome, solicitante_email, solicitante_fone, interno,
                                   empresa_nova_nome, empresa_nova_id, cobranca_modo, pagador_cliente_id)
                               VALUES (%s, %s, %s, %s, 'RASCUNHO', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (empresa_id, novo_numero(), d['cliente_id'], d['titulo'], d['validade'], d['como'], d['parcelas'], d['comissao_tipo'],
                             d['comissao_valor'], total, d['observacao'], d['nota_interna'], secrets.token_urlsafe(24)[:40], usuario.id,
                             d['solicitante_nome'], d['solicitante_email'], d['solicitante_fone'], d['interno'], d['empresa_nova_nome'], nova_id,
                             d['cobranca_modo'], d['pagador_cliente_id']), fetch=False)
        Orcamento._gravar_empresas(oid, d['cliente_id'], d['empresas_mais'])
        Orcamento._gravar_itens(oid, itens)
        Orcamento.evento(oid, 'criado', 'Orçamento montado: %d serviço%s, total R$ %s' % (len(itens), 's' if len(itens) != 1 else '', _brl(total)), usuario)
        return oid

    EDITAVEIS = ('RASCUNHO', 'REPROVADO', 'APROVADO', 'ACEITO', 'COBRANCA')

    @staticmethod
    def atualizar(oid, f, usuario):
        """Alterar valores, incluir serviços... (22/11/2026 — Anderson: "editar: alterar valores, incluir serviços e assim vai").
        Vale em qualquer fase antes de a cobrança sair. Depois de aprovado/aceito, o orçamento volta para RASCUNHO: passa de
        novo pela aprovação e, se já tinha ido ao cliente, ele aceita de novo (o aceite antigo era de outro documento)."""
        o = Orcamento.get(oid)
        if not o or o['status'] not in Orcamento.EDITAVEIS:
            raise ValueError('Este orçamento não pode mais ser alterado: cancele-o e monte outro.')
        d = Orcamento._dados_do_form(f)
        itens = Orcamento._itens_do_form(f)
        Orcamento._confere_grupo(d, itens)
        if Orcamento._abertura_pf(d['cliente_id'], itens) and not d['empresa_nova_nome']:
            raise ValueError('Abertura de empresa para pessoa física: diga o nome da empresa nova.')
        total = sum((i['valor_total'] for i in itens), Decimal('0'))
        nova_id = o.get('empresa_nova_id') if (o.get('empresa_nova_id') and o.get('empresa_nova_nome') == d['empresa_nova_nome']) else Orcamento._avulso_abertura(d, usuario)
        execute_query("""UPDATE com_orcamentos SET cliente_id = %s, titulo = %s, validade = %s, como = %s, parcelas = %s, comissao_tipo = %s,
                             comissao_valor = %s, total = %s, observacao = %s, nota_interna = %s, status = 'RASCUNHO', motivo = NULL,
                             solicitante_nome = %s, solicitante_email = %s, solicitante_fone = %s, interno = %s, empresa_nova_nome = %s, empresa_nova_id = %s,
                             cobranca_modo = %s, pagador_cliente_id = %s,
                             aprovado_por = NULL, aprovado_em = NULL, enviado_em = NULL, enviado_para = NULL, pdf_sha256 = NULL,
                             codigo_hash = NULL, codigo_enviado_em = NULL, codigo_para = NULL, codigo_tentativas = 0,
                             aceito_em = NULL, aceito_nome = NULL, aceito_cpf = NULL, aceito_email = NULL, aceito_ip = NULL, aceito_agente = NULL, aceito_hash = NULL,
                             feito_em = NULL, lancamento_id = NULL WHERE id = %s""",
                      (d['cliente_id'], d['titulo'], d['validade'], d['como'], d['parcelas'], d['comissao_tipo'], d['comissao_valor'], total,
                       d['observacao'], d['nota_interna'], d['solicitante_nome'], d['solicitante_email'], d['solicitante_fone'], d['interno'], d['empresa_nova_nome'], nova_id,
                       d['cobranca_modo'], d['pagador_cliente_id'], oid), fetch=False)
        Orcamento._gravar_empresas(oid, d['cliente_id'], d['empresas_mais'])
        execute_query('DELETE FROM com_orcamento_itens WHERE orcamento_id = %s', (oid,), fetch=False)
        Orcamento._gravar_itens(oid, itens)
        extra = ''
        if o.get('aceito_em'):
            extra = ' · o aceite de %s (%s) não vale mais: o cliente precisa aceitar o orçamento novo' % (o.get('aceito_nome') or 'cliente', o['aceito_em'].strftime('%d/%m/%Y'))
        elif o['status'] in ('APROVADO', 'ACEITO', 'COBRANCA'):
            extra = ' · volta para rascunho e passa de novo pela aprovação'
        Orcamento.evento(oid, 'editado', 'Orçamento alterado: %d serviço%s, total R$ %s (antes R$ %s)%s' % (len(itens), 's' if len(itens) != 1 else '', _brl(total), _brl(o['total']), extra), usuario)
        return oid

    @staticmethod
    def _gravar_itens(oid, itens):
        for n, i in enumerate(itens, 1):
            execute_query("""INSERT INTO com_orcamento_itens (orcamento_id, cliente_id, servico_id, descricao, quantidade, valor_unitario, valor_total, emite_nota, ordem)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                          (oid, i.get('cliente_id'), i['servico_id'], i['descricao'], i['quantidade'], i['valor_unitario'], i['valor_total'], i['emite_nota'], n), fetch=False)

    @staticmethod
    def mudar(oid, status, usuario, tipo, texto, **campos):
        """Troca o status gravando os campos extras (aprovado_por, motivo...) e o evento da linha do tempo."""
        from utils.comercial_pendencias import esquecer
        esquecer()                                   # selo de compromissos no menu (26/11/2026)
        sets, vals = ['status = %s'], [status]
        for k, v in campos.items():
            if v == 'NOW()':                     # hora do banco (o app roda em UTC: datetime.now() daria 3 h a mais)
                sets.append('%s = NOW()' % k); continue
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

    # ---------------- aceite do cliente pelo link (igual à assinatura do contrato) ----------------
    @staticmethod
    def gerar_codigo(oid, para):
        codigo = '%06d' % secrets.randbelow(1000000)
        execute_query('UPDATE com_orcamentos SET codigo_hash = %s, codigo_enviado_em = NOW(), codigo_para = %s, codigo_tentativas = 0 WHERE id = %s',
                      (hashlib.sha256(codigo.encode()).hexdigest(), (para or '')[:160], oid), fetch=False)
        return codigo

    @staticmethod
    def segundos_do_codigo(oid):
        """Há quantos segundos o código foi enviado, medido PELO BANCO (o app roda em UTC e o banco em Brasília: comparar
        datetime.now() com a coluna dava 3 h a mais e todo código nascia "vencido"). None = nunca enviado."""
        r = execute_query('SELECT TIMESTAMPDIFF(SECOND, codigo_enviado_em, NOW()) AS s FROM com_orcamentos WHERE id = %s', (oid,), fetch=True, fetch_one=True) or {}
        return r.get('s')

    @staticmethod
    def cpfs_conhecidos(cliente_id):
        """CPFs que podem confirmar o aceite: os sócios ativos do cadastro (CPF do sócio ou do cadastro PF ligado) e, se o cliente
        for pessoa física, o próprio CPF. Só dígitos."""
        out = []
        for r in execute_query("""SELECT s.cpf, c.cpf_cnpj AS pf FROM socios_clientes s LEFT JOIN clientes c ON c.id = s.pf_cliente_id
                                    WHERE s.cliente_id = %s AND COALESCE(s.ativo, 1) = 1""", (cliente_id,), fetch=True) or []:
            for v in (r.get('pf'), r.get('cpf')):
                d = ''.join(ch for ch in str(v or '') if ch.isdigit())
                if len(d) == 11 and d not in out:
                    out.append(d)
        c = execute_query('SELECT cpf_cnpj FROM clientes WHERE id = %s', (cliente_id,), fetch=True, fetch_one=True) or {}
        d = ''.join(ch for ch in str(c.get('cpf_cnpj') or '') if ch.isdigit())
        if len(d) == 11 and d not in out:
            out.append(d)
        return out

    @staticmethod
    def conferir_codigo(o, codigo):
        """True se o código bate; 5 erradas ou 30 minutos (pelo relógio do banco) invalidam."""
        if not o.get('codigo_hash') or (o.get('codigo_tentativas') or 0) >= 5:
            return False
        s = Orcamento.segundos_do_codigo(o['id'])
        if s is None or s > 30 * 60:
            return False
        ok = hashlib.sha256((codigo or '').strip().encode()).hexdigest() == o['codigo_hash']
        if not ok:
            execute_query('UPDATE com_orcamentos SET codigo_tentativas = codigo_tentativas + 1 WHERE id = %s', (o['id'],), fetch=False)
        return ok

    @staticmethod
    def registrar_aceite(oid, nome, cpf, email, ip, agente, pdf_hash):
        execute_query("""UPDATE com_orcamentos SET status = 'ACEITO', aceito_em = NOW(), aceito_nome = %s, aceito_cpf = %s, aceito_email = %s, aceito_ip = %s,
                             aceito_agente = %s, aceito_hash = %s, codigo_hash = NULL WHERE id = %s AND aceito_em IS NULL""",
                      ((nome or '')[:160], (cpf or '')[:14], (email or '')[:160], (ip or '')[:64], (agente or '')[:255], pdf_hash, oid), fetch=False)


def _brl(v):
    """1234.5 → '1.234,50'."""
    return '{:,.2f}'.format(Decimal(str(v or 0))).replace(',', 'X').replace('.', ',').replace('X', '.')
