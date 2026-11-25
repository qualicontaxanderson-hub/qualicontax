# -*- coding: utf-8 -*-
"""Contrato de prestação de serviços gerado e assinado pelo app (06/11/2026).

O contrato nasce do cadastro (empresa, endereço, sócio que assina) e dos honorários (valor, vencimento, 13º, reajuste).
As cláusulas seguem o modelo que o escritório usa desde 2020 (Posto Líder, Infinity, Rede MM, adendo Masut/LGPD),
com as variações que o Anderson pediu: serviços por caixa (contábil, fiscal, pessoal, específicos), 13º cobrado ou
isento, várias empresas num contrato só, quem assina pela Qualicontax (Albert ou Anderson).

Assinatura eletrônica dentro do app: cada assinante recebe um link por e-mail, pede um código de 6 dígitos (vai por
e-mail), digita código + nome + CPF e aceita. Fica gravado o hash do PDF aceito, data/hora, IP e navegador. Quando
todos assinam, o PDF final ganha a página "Registro de assinaturas" e vai para a pasta da empresa no Dropbox.
"""
import hashlib
import json
import re
import secrets
from datetime import date, datetime
from decimal import Decimal

from utils.db_helper import execute_query
from utils.extenso import reais_extenso
from utils.texto import nome_proprio
from models.reajuste_indice import ReajusteIndice

QUALICONTAX = {
    'razao': 'QUALICONTAX ASSESSORIA CONTÁBIL LTDA – EPP',
    'endereco': 'Estrada de Mogi das Cruzes, 221 – Antigo 33 – 1º Andar, Jardim Soraia, na cidade São Paulo, no Estado de SP, 03890-100',
    'cnpj': '11.158.475/0001-27', 'crc': '2SP025.952/O-7',
    'cabecalho': ['Estrada de Mogi das Cruzes, 221 – Antigo 33 – 1º Andar', 'Jardim Soraia – São Paulo – SP – 03890-100',
                  'faleconosco@qualicontax.com.br', 'www.qualicontax.com.br'],
}
# quem assina pela Qualicontax ("pode escolher quem, Anderson ou Albert")
SIGNATARIOS_QUALICONTAX = {
    'albert': {'usuario_id': 70, 'nome': 'Albert Antunes Vieira', 'cpf': '267.849.298-29', 'documento': 'RG nº 28.486.559-X SSP/SP e CRC 1SP263.139/O-5',
               'email': 'albert@qualicontax.com.br', 'cargo': 'sócio e administrador'},
    'anderson': {'usuario_id': 62, 'nome': 'Anderson Antunes Vieira', 'cpf': '291.511.418-84', 'documento': 'RG nº 41.123.866 SSP/SP',
                 'email': 'anderson@m3a3.com.br', 'cargo': 'sócio e administrador'},
}
# áreas de serviço: título e itens (o texto que o escritório já usa)
AREAS = {
    'CONTABIL': ('CONTABILIDADE', [
        'Elaboração da Contabilidade de acordo com as Normas Brasileiras de Contabilidade;',
        'Emissão de balancetes; e',
        'Elaboração de Balanço Patrimonial e demais Demonstrações Contábeis obrigatórias.']),
    'FISCAL': ('ESCRITA FISCAL', [          # 25/11/2026 — Anderson: "Contabilidade, Escrita Fiscal e Departamento Pessoal"
        'Orientação e controle de aplicação dos dispositivos legais vigentes, sejam federais, estaduais ou municipais;',
        'Elaboração dos registros fiscais obrigatórios (EFD ICMS/IPI, GIA e outros), eletrônicos ou não, perante os órgãos municipais, '
        'estaduais e federais, bem como as demais obrigações que se fizerem necessárias; e',
        'Atendimento às demais exigências previstas na legislação, bem como aos eventuais procedimentos fiscais.']),
    'PESSOAL': ('DEPARTAMENTO PESSOAL', [
        'Registros de empregados e serviços correlatos;',
        'Elaboração da folha de pagamento dos empregados e de pró-labore, bem como das guias de recolhimento dos encargos sociais e tributos afins;',
        'Elaboração, orientação e controle da aplicação dos preceitos da Consolidação das Leis do Trabalho, bem como daqueles atinentes à '
        'Previdência Social e de outros aplicáveis às relações de trabalho mantidas pela contratante;']),
}
TITULOS = {'CONTABIL': 'CONTRATO DE PRESTAÇÃO DE SERVIÇOS CONTÁBEIS', 'FISCAL': 'CONTRATO DE PRESTAÇÃO DE SERVIÇOS FISCAIS',
           'PESSOAL': 'CONTRATO DE PRESTAÇÃO DE SERVIÇOS DE DEPARTAMENTO PESSOAL'}
STATUS = ('RASCUNHO', 'APROVACAO', 'APROVADO', 'ENVIADO', 'ASSINADO', 'CANCELADO')   # 18/11/2026: aprovação da equipe
REAJUSTE_TXT = {
    'SALARIO_MINIMO': 'Os honorários serão reajustados anualmente, no mês de janeiro, pelo mesmo percentual de reajuste do salário mínimo nacional.',
    'INDICE': 'Os honorários serão reajustados anualmente pela variação acumulada do {indice}, ou de outro índice oficial que venha a substituí-lo.',
    'DATA_PROPRIA': 'Os honorários serão reajustados anualmente, no mês de {mes}, {indice_txt}.',
    'SEM': 'Os honorários serão reajustados de comum acordo entre as partes.',
    'ESCALONADO': 'Os honorários seguirão a escala combinada entre as partes: {indice}.',
}


def _d(v):
    return Decimal(str(v or 0)).quantize(Decimal('0.01'))


def brl(v):
    return 'R$ ' + '{:,.2f}'.format(_d(v)).replace(',', 'X').replace('.', ',').replace('X', '.')


def data_extenso(d):
    meses = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']
    return '%d de %s de %d' % (d.day, meses[d.month - 1], d.year)


QUALICONTAX_CLIENTE_ID = 2        # a própria Qualicontax no cadastro de clientes (nº 1)


def _doc_texto(docs):
    """'RG nº 28.486.559-X SSP/SP e CRC 1SP263.139/O-5' a partir dos documentos da pessoa (o marcado para contrato primeiro)."""
    partes = []
    for d in docs:
        org = '/'.join(x for x in (d.get('orgao'), d.get('uf')) if x)
        if d['tipo'] in ('CRC', 'OAB', 'CRM', 'CREA', 'CRO', 'CRF', 'CRBM', 'CRA'):
            partes.append('%s %s' % (d['tipo'], d['numero']))
        else:
            partes.append('%s nº %s%s' % (d['tipo'], d['numero'], (' ' + org) if org else ''))
    return ' e '.join(partes)


def _celular_da_pessoa(pf_cliente_id, cpf=None):
    """Celular de quem assina: o do cadastro PF (celular, senão telefone); se é alguém do escritório, o do usuário."""
    if pf_cliente_id:
        c = execute_query('SELECT celular, telefone FROM clientes WHERE id = %s', (pf_cliente_id,), fetch=True, fetch_one=True) or {}
        if (c.get('celular') or c.get('telefone') or '').strip():
            return (c.get('celular') or c.get('telefone')).strip()
    dig = ''.join(ch for ch in (cpf or '') if ch.isdigit())
    if dig:
        u = execute_query('SELECT telefone FROM usuarios WHERE cpf = %s AND situacao = %s', (dig, 'ATIVO'), fetch=True, fetch_one=True) or {}
        if (u.get('telefone') or '').strip():                    # usuarios não tem coluna celular: o telefone é o celular
            return u['telefone'].strip()
    return ''


def _cargo_do_socio(s):
    """'sócio administrador', 'sócia administradora', 'diretor'... a partir da qualificação do quadro (ou do responsável)."""
    q = (s.get('qualificacao') or '').strip().lower().replace('-', ' ').replace('  ', ' ')
    if q in ('', 'socio', 'sócio', 'socio administrador', 'sócio administrador', 'administrador'):
        return 'sócio administrador' if (s.get('responsavel') or q.endswith('administrador')) else 'sócio'
    return q


def _email_da_pessoa(pf_cliente_id, cpf):
    """E-mail de quem assina: o do USUÁRIO (se é alguém do escritório, pelo CPF), senão o do cadastro PF."""
    dig = ''.join(ch for ch in (cpf or '') if ch.isdigit())
    if dig:
        u = execute_query('SELECT id, email FROM usuarios WHERE cpf = %s AND situacao = %s', (dig, 'ATIVO'), fetch=True, fetch_one=True)
        if u and u.get('email'):
            return u['email'], u['id']
    if pf_cliente_id:
        c = execute_query('SELECT email FROM clientes WHERE id = %s', (pf_cliente_id,), fetch=True, fetch_one=True)
        if c and c.get('email'):
            return c['email'], None
    return None, None


class PrestContrato:

    # ---------------- a CONTRATADA e quem assina por ela, do cadastro ----------------
    @staticmethod
    def contratada():
        """Razão, endereço, CNPJ e CRC da Qualicontax, do cadastro de clientes (nº 1). Sem o cadastro, cai nos fixos."""
        e = PrestContrato.empresa(QUALICONTAX_CLIENTE_ID)
        if not e:
            return dict(QUALICONTAX, cabecalho=QUALICONTAX['cabecalho'], cliente_id=None)
        crc = execute_query("SELECT valor FROM cadastros_adicionais_clientes WHERE cliente_id = %s AND campo = 'CRC' AND ativo = 1 ORDER BY id DESC LIMIT 1",
                            (QUALICONTAX_CLIENTE_ID,), fetch=True, fetch_one=True)
        end = e.get('endereco_dict') or {}
        from utils.texto import nome_proprio
        cidade = (nome_proprio(end.get('cidade') or '') or 'São Paulo').replace('Sao ', 'São ')
        linha1 = ', '.join(x for x in [nome_proprio(end.get('logradouro') or ''), (end.get('numero') or '')] if x)
        if end.get('complemento'):
            # "ANDAR 1" (peça do cadastro) vira "1º Andar" no papel
            linha1 += ' – ' + re.sub(r'\bAndar (\d+)\b', r'\1º Andar', nome_proprio(end['complemento']).replace(' - ', ' – '))
        linha2 = ' – '.join(x for x in [nome_proprio(end.get('bairro') or ''), cidade, end.get('estado') or '', end.get('cep') or ''] if x)
        return {'cliente_id': e['id'], 'razao': e['nome_razao_social'], 'cnpj': e['cpf_cnpj'], 'endereco': e['endereco'], 'crc': (crc or {}).get('valor') or '',
                'email': e.get('email') or '', 'telefone': e.get('telefone') or '',
                'cidade': cidade, 'uf': (end.get('estado') or '').strip() or 'SP',
                'cabecalho': [x for x in (linha1, linha2, e.get('email') or '', 'www.qualicontax.com.br') if x]}

    @staticmethod
    def signatarios_qualicontax():
        """Os sócios da Qualicontax no cadastro (Albert, Anderson), cada um com CPF, documentos e e-mail. {pf_cliente_id: {...}}"""
        from models.pessoa_dados import Documentos
        out = {}
        for s in execute_query("""SELECT nome, cpf, pf_cliente_id, responsavel FROM socios_clientes WHERE cliente_id = %s AND ativo = 1
                                     ORDER BY responsavel DESC, nome""", (QUALICONTAX_CLIENTE_ID,), fetch=True) or []:
            pf = s.get('pf_cliente_id')
            docs = Documentos.listar(pf) if pf else []
            email, uid = _email_da_pessoa(pf, s.get('cpf'))
            chave = str(pf or s['nome'])
            out[chave] = {'pf_cliente_id': pf, 'usuario_id': uid, 'nome': (s['nome'] or '').title(), 'cpf': s.get('cpf') or '', 'documento': _doc_texto(docs),
                          'email': email or '', 'cargo': 'sócio e administrador' if s.get('responsavel') else 'sócio', 'docs_faltam': not docs}
        return out

    # ---------------- dados do cadastro para pré-preencher ----------------
    @staticmethod
    def empresa(cliente_id):
        c = execute_query('SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, tipo_pessoa, email, natureza_juridica FROM clientes WHERE id = %s',
                          (cliente_id,), fetch=True, fetch_one=True)
        if not c:
            return None
        e = execute_query("""SELECT logradouro, numero, complemento, bairro, cidade, estado, cep FROM enderecos_clientes
                              WHERE cliente_id = %s ORDER BY principal DESC, id LIMIT 1""", (cliente_id,), fetch=True, fetch_one=True) or {}
        partes = [p for p in (' '.join(x for x in [(e.get('logradouro') or '').strip(), (e.get('numero') or '').strip()] if x),
                              (e.get('complemento') or '').strip(), (e.get('bairro') or '').strip(),
                              ' – '.join(x for x in [(e.get('cidade') or '').strip(), (e.get('estado') or '').strip()] if x),
                              (e.get('cep') or '').strip()) if p]
        c['endereco'] = ' – '.join(partes)
        c['endereco_dict'] = e
        c['cidade'] = (e.get('cidade') or '').strip()
        c['uf'] = (e.get('estado') or '').strip()
        c['socios'] = execute_query("""SELECT nome, cpf, qualificacao, responsavel, email, pf_cliente_id FROM socios_clientes
                                        WHERE cliente_id = %s AND ativo = 1 ORDER BY responsavel DESC, nome""", (cliente_id,), fetch=True) or []
        from models.pessoa_dados import Documentos
        if not c['socios'] and (c.get('tipo_pessoa') == 'PF' or len(''.join(ch for ch in (c.get('cpf_cnpj') or '') if ch.isdigit())) == 11):
            # pessoa física contratante: quem assina é ela mesma
            c['socios'] = [{'nome': c['nome_razao_social'], 'cpf': c.get('cpf_cnpj') or '', 'qualificacao': 'titular', 'responsavel': 1, 'email': '', 'pf_cliente_id': c['id']}]
        for s in c['socios']:                              # documento marcado para contrato, e-mail, celular e cargo vêm do cadastro PF do sócio
            pf = s.get('pf_cliente_id')
            s['documento'] = _doc_texto(Documentos.listar(pf)) if pf else ''
            if not s.get('email'):
                s['email'] = _email_da_pessoa(pf, s.get('cpf'))[0] or ''
            s['celular'] = _celular_da_pessoa(pf, s.get('cpf'))
            s['cargo'] = _cargo_do_socio(s)
        # quem assina pode ser um terceiro: os CONTATOS da empresa também viram atalho (Anderson, 06/11)
        # ramos de atividade da empresa, com a mensalidade sugerida de cada um (24/11/2026): o formulário do contrato oferece como sugestão de honorários
        from models.ramo_atividade import RamoAtividade
        c['ramos'] = [{'id': r['id'], 'nome': r['nome'], 'segmento': r.get('segmento') or '', 'sugestao': float(r['mensalidade_sugerida']) if r.get('mensalidade_sugerida') else 0.0}
                      for r in RamoAtividade.get_by_cliente(c['id'])]
        c['contatos'] = execute_query("""SELECT id, nome, cargo, email, celular FROM contatos_clientes
                                          WHERE cliente_id = %s AND ativo = 1 ORDER BY principal DESC, nome""", (cliente_id,), fetch=True) or []
        nomes_socios = {(s.get('nome') or '').strip().upper() for s in c['socios']}
        c['contatos'] = [k for k in c['contatos'] if (k.get('nome') or '').strip().upper() not in nomes_socios]   # quem já é sócio não repete como contato
        return c

    @staticmethod
    def pessoas(termo, limite=8):
        """Pessoas físicas do cadastro, por nome ou CPF, com documento, e-mail e celular: para puxar quem assina sem digitar
        (22/11/2026 — Anderson: "os campos quase nada preenchido, e está tudo no cadastro")."""
        termo = (termo or '').strip()
        if len(termo) < 2:
            return []
        dig = ''.join(ch for ch in termo if ch.isdigit())
        cond, params = ['c.nome_razao_social LIKE %s'], ['%' + termo + '%']
        if dig and len(dig) >= 3 and dig == termo.replace('.', '').replace('-', '').replace(' ', ''):
            cond = ["REPLACE(REPLACE(c.cpf_cnpj, '.', ''), '-', '') LIKE %s"]; params = ['%' + dig + '%']
        rows = execute_query("""SELECT c.id, c.nome_razao_social AS nome, c.cpf_cnpj AS cpf, c.email, c.celular, c.telefone, COALESCE(c.avulso, 0) AS avulso
                                   FROM clientes c WHERE c.tipo_pessoa = 'PF' AND (%s)
                                  ORDER BY c.situacao = 'ATIVO' DESC, c.nome_razao_social LIMIT %%s""" % ' OR '.join(cond),
                             tuple(params) + (int(limite),), fetch=True) or []
        from models.pessoa_dados import Documentos
        out = []
        for r in rows:
            email, _ = _email_da_pessoa(r['id'], r.get('cpf'))
            out.append({'id': r['id'], 'nome': (r['nome'] or '').strip(), 'cpf': r.get('cpf') or '', 'avulso': bool(r.get('avulso')), 'documento': _doc_texto(Documentos.listar(r['id'])),
                        'email': (email or r.get('email') or '').strip().lower(), 'celular': _celular_da_pessoa(r['id'], r.get('cpf'))})
        return out

    @staticmethod
    def pessoa(cliente_id):
        """Uma pessoa física do cadastro (cliente ou avulso), com e-mail, celular e documento: testemunha do cliente
        (25/11/2026 — Anderson: "alguém que deverá estar cadastrado como Cliente ou Avulso, sendo obrigatório Pessoa Física")."""
        r = execute_query("""SELECT c.id, c.nome_razao_social AS nome, c.cpf_cnpj AS cpf, c.email, COALESCE(c.avulso, 0) AS avulso
                                 FROM clientes c WHERE c.id = %s AND c.tipo_pessoa = 'PF'""", (cliente_id,), fetch=True, fetch_one=True)
        if not r:
            return None
        from models.pessoa_dados import Documentos
        email, _ = _email_da_pessoa(r['id'], r.get('cpf'))
        return {'id': r['id'], 'nome': (r['nome'] or '').strip(), 'cpf': r.get('cpf') or '', 'avulso': bool(r.get('avulso')),
                'documento': _doc_texto(Documentos.listar(r['id'])), 'email': (email or r.get('email') or '').strip().lower(),
                'celular': _celular_da_pessoa(r['id'], r.get('cpf'))}

    @staticmethod
    def socios_iguais(cliente_ids):
        """Contrato em grupo (22/11/2026 — Anderson: "individual ou em grupo, desde que os sócios sejam os mesmos; se não for,
        precisamos de autorização"). Compara o quadro de sócios ativos (por CPF) de cada empresa. Devolve (iguais, detalhe)."""
        ids = [int(c) for c in (cliente_ids or []) if str(c).isdigit()]
        if len(ids) < 2:
            return True, ''
        quadros = {}
        for cid in ids:
            rows = execute_query("""SELECT s.nome, s.cpf, c.cpf_cnpj AS pf FROM socios_clientes s LEFT JOIN clientes c ON c.id = s.pf_cliente_id
                                      WHERE s.cliente_id = %s AND COALESCE(s.ativo, 1) = 1""", (cid,), fetch=True) or []
            quadros[cid] = {(''.join(ch for ch in str(r.get('pf') or r.get('cpf') or '') if ch.isdigit()) or (r.get('nome') or '').strip().upper()) for r in rows}
        base = quadros[ids[0]]
        dif = [cid for cid in ids[1:] if quadros[cid] != base]
        if not dif:
            return True, 'os mesmos sócios em todas as empresas'
        nomes = {r['id']: r['nome_razao_social'] for r in execute_query('SELECT id, nome_razao_social FROM clientes WHERE id IN (%s)' % ','.join(str(i) for i in ids), fetch=True) or []}
        return False, 'quadro de sócios diferente em: ' + ', '.join(nomes.get(c, str(c)) for c in dif)

    @staticmethod
    def testemunhas_disponiveis():
        return execute_query("""SELECT id, nome, cpf, email FROM usuarios WHERE classe_conta = 'FUNCIONARIO' AND situacao = 'ATIVO'
                                   AND login <> 'admin' ORDER BY nome""", fetch=True) or []      # a conta Administrador não é pessoa

    # ---------------- gravação ----------------
    @staticmethod
    def gerar_numero():
        """CTR-XXXXXX, no mesmo molde do orçamento (ORC-XXXXXX): seis caracteres aleatórios sem letras ambíguas (0/O, 1/I),
        único na tabela. Era QC-, que confundia com o número dos boletos (24/11/2026, Anderson: "numeração igual aos orçamentos")."""
        alfabeto = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        for _ in range(20):
            n = 'CTR-' + ''.join(secrets.choice(alfabeto) for _ in range(6))
            if not execute_query('SELECT 1 FROM prest_contratos WHERE numero = %s', (n,), fetch=True):
                return n
        raise RuntimeError('não consegui um número livre')

    @staticmethod
    def criar(cliente_id, dados, criado_por, fin_contrato_id=None, tipo='CONTRATO', anterior_id=None):
        titulo = dados.get('titulo') or PrestContrato.titulo_de(dados)
        cid = execute_query(
            """INSERT INTO prest_contratos (numero, cliente_id, fin_contrato_id, tipo, anterior_id, titulo, dados, criado_por)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (PrestContrato.gerar_numero(), cliente_id, fin_contrato_id, tipo, anterior_id, titulo[:120], json.dumps(dados, ensure_ascii=False, default=str), criado_por), fetch=False)
        PrestContrato._gravar_empresas(cid, dados)
        PrestContrato._gravar_assinantes(cid, dados)
        return cid

    @staticmethod
    def atualizar(cid, dados):
        c = PrestContrato.get(cid)
        if not c or c['status'] != 'RASCUNHO':
            raise ValueError('Só rascunho pode ser alterado. Cancele e gere outro.')
        titulo = dados.get('titulo') or PrestContrato.titulo_de(dados)
        execute_query('UPDATE prest_contratos SET titulo = %s, dados = %s WHERE id = %s',
                      (titulo[:120], json.dumps(dados, ensure_ascii=False, default=str), cid), fetch=False)
        PrestContrato._gravar_empresas(cid, dados)
        PrestContrato._gravar_assinantes(cid, dados)

    @staticmethod
    def _gravar_empresas(cid, dados):
        execute_query('DELETE FROM prest_contrato_empresas WHERE contrato_id = %s', (cid,), fetch=False)
        for n, e in enumerate(dados.get('empresas') or [], 1):
            if e.get('cliente_id'):
                execute_query('INSERT IGNORE INTO prest_contrato_empresas (contrato_id, cliente_id, ordem) VALUES (%s, %s, %s)',
                              (cid, int(e['cliente_id']), n), fetch=False)

    @staticmethod
    def _gravar_assinantes(cid, dados):
        """Assinantes vêm do formulário; mantém o token de quem já existia (mesmo papel e ordem)."""
        atuais = {(a['papel'], a['ordem']): a for a in PrestContrato.assinantes(cid)}
        execute_query('DELETE FROM prest_assinantes WHERE contrato_id = %s AND assinado_em IS NULL', (cid,), fetch=False)
        for a in dados.get('assinantes') or []:
            chave = (a['papel'], int(a.get('ordem') or 1))
            ex = atuais.get(chave)
            if ex and ex['assinado_em']:
                continue
            execute_query(
                """INSERT INTO prest_assinantes (contrato_id, papel, ordem, nome, cpf, documento, email, usuario_id, token)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (cid, a['papel'], chave[1], (a.get('nome') or '')[:160], (a.get('cpf') or '')[:14] or None, (a.get('documento') or '')[:80] or None,
                 (a.get('email') or '').strip().lower()[:160] or None, a.get('usuario_id') or None,
                 ex['token'] if ex else secrets.token_urlsafe(24)), fetch=False)

    @staticmethod
    def get(cid):
        c = execute_query(
            """SELECT p.*, cl.numero_cliente, cl.nome_razao_social, u.nome AS criado_por_nome
                 FROM prest_contratos p JOIN clientes cl ON cl.id = p.cliente_id LEFT JOIN usuarios u ON u.id = p.criado_por
                WHERE p.id = %s""", (cid,), fetch=True, fetch_one=True)
        if c:
            c['dados'] = json.loads(c['dados']) if isinstance(c['dados'], str) else (c['dados'] or {})
        return c

    @staticmethod
    def prontas():
        """Empresas que PODEM receber contrato. Era "só cadastro completo" (06/11/2026); em 24/11/2026 o Anderson, vendo
        só 9 de 600 na caixa ("não estou entendendo por que está aparecendo só algumas sugestões"), mandou abrir: todo
        cliente ATIVO com número e todo avulso, sem contrato ENVIADO/ASSINADO. O que faltar no cadastro (sócio, endereço)
        a pessoa completa no próprio formulário do contrato. Cada um diz se tem honorários (fin_contratos ATIVO)."""
        rows = execute_query(
            """SELECT c.id, c.numero_cliente, c.nome_razao_social, c.nome_fantasia, c.cpf_cnpj, c.tipo_pessoa, c.avulso, c.regime_tributario,
                      (SELECT MAX(e.cidade) FROM enderecos_clientes e WHERE e.cliente_id = c.id) AS cidade,
                      (SELECT MAX(e.estado) FROM enderecos_clientes e WHERE e.cliente_id = c.id) AS uf,
                      (SELECT g.nome FROM cliente_grupo_relacao r JOIN grupos_clientes g ON g.id = r.grupo_id
                        WHERE r.cliente_id = c.id ORDER BY r.id LIMIT 1) AS grupo,
                      (SELECT fc.valor FROM fin_contratos fc WHERE fc.cliente_id = c.id AND fc.situacao = 'ATIVO' AND fc.empresa_id = 2
                        ORDER BY fc.id DESC LIMIT 1) AS valor,
                      (SELECT COUNT(*) FROM socios_clientes s WHERE s.cliente_id = c.id AND s.ativo = 1) AS n_socios,
                      (SELECT COUNT(*) FROM prest_contratos p WHERE p.status = 'RASCUNHO'
                         AND (p.cliente_id = c.id OR p.id IN (SELECT contrato_id FROM prest_contrato_empresas e WHERE e.cliente_id = c.id))) AS n_rascunhos
                 FROM clientes c
                WHERE ((c.avulso = 0 AND c.numero_cliente IS NOT NULL AND c.situacao = 'ATIVO') OR c.avulso = 1)
                  AND NOT EXISTS (SELECT 1 FROM prest_contratos p WHERE p.status IN ('ENVIADO', 'ASSINADO')
                                     AND (p.cliente_id = c.id OR p.id IN (SELECT contrato_id FROM prest_contrato_empresas e WHERE e.cliente_id = c.id)))
                ORDER BY c.avulso, CAST(c.numero_cliente AS UNSIGNED), c.nome_razao_social""", fetch=True) or []
        return {'lista': rows, 'pendentes_empresas': 0, 'pendentes_avulsos': 0}

    @staticmethod
    def impedimentos(ids):
        """{cliente_id: 'motivo'} do que impede montar contrato: só cadastro inexistente ou inativo (24/11/2026: a pendência
        em A CADASTRAR deixou de barrar). Quem não está no dicionário pode receber contrato."""
        ids = [int(i) for i in ids]
        out = {}
        if not ids:
            return out
        marks = ','.join(['%s'] * len(ids))
        rows = execute_query(f"SELECT id, avulso, numero_cliente, situacao FROM clientes WHERE id IN ({marks})", tuple(ids), fetch=True) or []
        achados = {r['id']: r for r in rows}
        for i in ids:
            r = achados.get(i)
            if not r:
                out[i] = 'cadastro não encontrado'
            elif not r['avulso'] and (r['numero_cliente'] is None or r['situacao'] != 'ATIVO'):
                out[i] = 'cadastro sem número de cliente ou inativo'
        return out

    @staticmethod
    def listar(status=None, cliente_id=None):
        cond, params = [], []
        if status:
            cond.append('p.status = %s'); params.append(status)
        if cliente_id:
            cond.append('(p.cliente_id = %s OR p.id IN (SELECT contrato_id FROM prest_contrato_empresas WHERE cliente_id = %s))'); params += [cliente_id, cliente_id]
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        rows = execute_query(
            f"""SELECT p.id, p.numero, p.cliente_id, p.tipo, p.titulo, p.status, p.criado_em, p.enviado_em, p.assinado_em, p.pdf_path, p.dados,
                       p.criado_por, (SELECT GROUP_CONCAT(e.cliente_id) FROM prest_contrato_empresas e WHERE e.contrato_id = p.id) AS empresas_csv,
                       cl.numero_cliente, cl.nome_razao_social, u.nome AS criado_por_nome,
                       (SELECT COUNT(*) FROM prest_contrato_empresas e WHERE e.contrato_id = p.id) AS n_empresas,
                       (SELECT COUNT(*) FROM prest_assinantes a WHERE a.contrato_id = p.id) AS n_assinantes,
                       (SELECT COUNT(*) FROM prest_assinantes a WHERE a.contrato_id = p.id AND a.assinado_em IS NOT NULL) AS n_assinados
                  FROM prest_contratos p JOIN clientes cl ON cl.id = p.cliente_id LEFT JOIN usuarios u ON u.id = p.criado_por
                 {where} ORDER BY p.id DESC""", tuple(params), fetch=True) or []
        for r in rows:
            r['empresas_ids'] = [int(x) for x in (r.get('empresas_csv') or '').split(',') if x.strip().isdigit()]
            try:
                r['dados'] = json.loads(r['dados']) if isinstance(r['dados'], str) else (r['dados'] or {})
            except ValueError:
                r['dados'] = {}
        return rows

    @staticmethod
    def assinado_do_cliente(cliente_id):
        """O contrato ASSINADO vigente da empresa (condição da comissão de honorários)."""
        return execute_query(
            """SELECT p.id, p.assinado_em FROM prest_contratos p
                WHERE p.status = 'ASSINADO' AND (p.cliente_id = %s OR p.id IN (SELECT contrato_id FROM prest_contrato_empresas WHERE cliente_id = %s))
                ORDER BY p.assinado_em DESC LIMIT 1""", (cliente_id, cliente_id), fetch=True, fetch_one=True)

    @staticmethod
    def assinantes(cid):
        return execute_query('SELECT * FROM prest_assinantes WHERE contrato_id = %s ORDER BY FIELD(papel, %s, %s, %s), ordem',
                             (cid, 'CONTRATANTE', 'CONTRATADA', 'TESTEMUNHA'), fetch=True) or []

    @staticmethod
    def assinante_por_token(token):
        a = execute_query('SELECT * FROM prest_assinantes WHERE token = %s', (token,), fetch=True, fetch_one=True)
        return a

    @staticmethod
    def mudar_status(cid, status, pdf_sha256=None, pdf_path=None):
        col = {'ENVIADO': 'enviado_em', 'ASSINADO': 'assinado_em', 'CANCELADO': 'cancelado_em'}.get(status)
        sets, params = ['status = %s'], [status]
        if col:
            sets.append(f'{col} = NOW()')
        if pdf_sha256:
            sets.append('pdf_sha256 = %s'); params.append(pdf_sha256)
        if pdf_path:
            sets.append('pdf_path = %s'); params.append(pdf_path)
        params.append(cid)
        execute_query(f"UPDATE prest_contratos SET {', '.join(sets)} WHERE id = %s", tuple(params), fetch=False)

    # ---------------- assinatura ----------------
    @staticmethod
    def gerar_codigo(assinante_id):
        codigo = '%06d' % secrets.randbelow(1000000)
        execute_query('UPDATE prest_assinantes SET codigo_hash = %s, codigo_enviado_em = NOW(), codigo_tentativas = 0 WHERE id = %s',
                      (hashlib.sha256(codigo.encode()).hexdigest(), assinante_id), fetch=False)
        return codigo

    @staticmethod
    def conferir_codigo(assinante, codigo):
        """True se o código bate; conta tentativas (5 erradas invalidam o código)."""
        if not assinante.get('codigo_hash') or assinante.get('codigo_tentativas', 0) >= 5:
            return False
        # 21/11/2026: medido pelo banco — o app roda em UTC e o banco em Brasília; datetime.now() dava 3 h a mais e o código nascia vencido
        r = execute_query('SELECT TIMESTAMPDIFF(SECOND, codigo_enviado_em, NOW()) AS s FROM prest_assinantes WHERE id = %s', (assinante['id'],), fetch=True, fetch_one=True) or {}
        if r.get('s') is None or r['s'] > 30 * 60:
            return False
        ok = hashlib.sha256((codigo or '').strip().encode()).hexdigest() == assinante['codigo_hash']
        if not ok:
            execute_query('UPDATE prest_assinantes SET codigo_tentativas = codigo_tentativas + 1 WHERE id = %s', (assinante['id'],), fetch=False)
        return ok

    @staticmethod
    def registrar_assinatura(assinante_id, nome, cpf, ip, agente, pdf_hash):
        execute_query(
            """UPDATE prest_assinantes SET assinado_em = NOW(), assinado_nome = %s, assinado_cpf = %s, assinado_ip = %s,
                      assinado_agente = %s, assinado_hash = %s, codigo_hash = NULL WHERE id = %s AND assinado_em IS NULL""",
            (nome[:160], (cpf or '')[:14], (ip or '')[:64], (agente or '')[:255], pdf_hash, assinante_id), fetch=False)

    @staticmethod
    def todos_assinaram(cid):
        r = execute_query('SELECT COUNT(*) n, SUM(assinado_em IS NOT NULL) ok FROM prest_assinantes WHERE contrato_id = %s', (cid,), fetch=True, fetch_one=True)
        return bool(r and r['n'] and int(r['ok'] or 0) == int(r['n']))

    # ---------------- texto do contrato ----------------
    @staticmethod
    def titulo_de(dados):
        areas = dados.get('areas') or []
        if 'CONTABIL' in areas:
            return TITULOS['CONTABIL']
        if 'FISCAL' in areas and 'PESSOAL' not in areas:
            return TITULOS['FISCAL']
        if 'PESSOAL' in areas and 'FISCAL' not in areas:
            return TITULOS['PESSOAL']
        return 'CONTRATO DE PRESTAÇÃO DE SERVIÇOS'

    @staticmethod
    def montar(dados):
        """Devolve o contrato pronto para o PDF: {'titulo', 'partes': [...], 'preambulo', 'clausulas': [(titulo, [paragrafos])],
        'fecho', 'local_data', 'assinaturas': [...]}. ``dados`` é o JSON do formulário."""
        empresas = dados.get('empresas') or []
        apelido = (dados.get('apelido') or (empresas[0].get('apelido') if empresas else '') or 'CONTRATANTE').strip().upper()
        # ---- partes ----
        partes = []
        def _pf(e):
            return len(''.join(ch for ch in (e.get('cnpj') or '') if ch.isdigit())) == 11
        if len(empresas) == 1 and _pf(empresas[0]):
            e = empresas[0]                                 # pessoa física contratante: assina por si
            if (e.get('rep_nome') or '').strip().upper() == (e['razao'] or '').strip().upper() or (e.get('rep_cargo') or '') == 'titular':
                partes.append('%s, residente e domiciliado(a) à %s, portador(a) %s e inscrito(a) no CPF nº %s, ora e doravante denominado(a) de %s.'
                              % (e['razao'], e['endereco'], e.get('rep_documento') or 'de documento de identidade', e['cnpj'], apelido))
            else:
                partes.append('%s, residente e domiciliado(a) à %s, inscrito(a) no CPF nº %s, neste ato representado(a) por seu(sua) %s, %s, portador(a) %s e CPF nº %s, '
                              'ora e doravante denominado(a) de %s.' % (e['razao'], e['endereco'], e['cnpj'], e.get('rep_cargo') or 'procurador(a)',
                                                                       e['rep_nome'], e.get('rep_documento') or 'de documento de identidade', e.get('rep_cpf') or '', apelido))
        elif len(empresas) == 1:
            e = empresas[0]
            partes.append('%s, estabelecida à %s, devidamente inscrita no CNPJ nº %s, neste ato representada por seu %s, %s, portador(a) %s e CPF nº %s, '
                          'ora e doravante denominada de %s.' % (e['razao'], e['endereco'], e['cnpj'], e.get('rep_cargo') or 'sócio administrador',
                                                                e['rep_nome'], e.get('rep_documento') or 'de documento de identidade', e.get('rep_cpf') or '', apelido))
        else:
            lista = '; '.join('%s, estabelecida à %s, devidamente inscrita no %s nº %s' % (e['razao'], e['endereco'], 'CPF' if _pf(e) else 'CNPJ', e['cnpj']) for e in empresas)
            e = empresas[0]
            partes.append('%s; neste ato representadas por seu %s, %s, portador(a) %s e CPF nº %s, ora e doravante denominadas em conjunto de %s.'
                          % (lista, e.get('rep_cargo') or 'sócio administrador', e['rep_nome'], e.get('rep_documento') or 'de documento de identidade',
                             e.get('rep_cpf') or '', apelido))
        ex = dados.get('assinante_extra')
        if ex and ex.get('nome'):
            partes[-1] = partes[-1].rstrip('.') + ', e ainda por %s, %s, portador(a) %s e CPF nº %s, devidamente autorizado(a) a assinar o presente instrumento.' % (
                ex['nome'], ex.get('cargo') or 'representante autorizado(a)', ex.get('documento') or 'de documento de identidade', ex.get('cpf') or '')
        qc = dados.get('contratada') or PrestContrato.contratada()
        q = dados.get('assina') or PrestContrato.signatarios_qualicontax().get(str(dados.get('assina_qualicontax'))) or SIGNATARIOS_QUALICONTAX['albert']
        partes.append('%s, estabelecida à %s, devidamente inscrita no CNPJ nº %s%s, neste ato representada por seu %s, %s, portador(a) %s e CPF nº %s, '
                      'ora e doravante denominada de QUALICONTAX.' % (qc['razao'], qc['endereco'], qc['cnpj'], (', CRC nº ' + qc['crc']) if qc.get('crc') else '',
                                                                      q['cargo'], q['nome'], q.get('documento') or 'de documento de identidade', q['cpf']))
        titulo = dados.get('titulo') or PrestContrato.titulo_de(dados)
        preambulo = 'Celebram entre si um %s, nos seguintes termos:' % titulo
        # ---- cláusulas ----
        cl = []
        areas = [a for a in ('CONTABIL', 'FISCAL', 'PESSOAL') if a in (dados.get('areas') or [])]
        servicos = []
        n = 0
        for a in areas:
            n += 1
            nome, itens = AREAS[a]
            servicos.append('%d. %s' % (n, nome))
            servicos += ['%d.%d. %s' % (n, i + 1, t) for i, t in enumerate(itens)]
        for extra in dados.get('servicos_extras') or []:
            if (extra or '').strip():
                n += 1
                servicos.append('%d. %s' % (n, extra.strip()))
        cl.append(('CLÁUSULA PRIMEIRA', ['A QUALICONTAX obriga-se a prestar seus serviços profissionais à %s, nas seguintes áreas:' % apelido] + servicos))
        cl.append(('CLÁUSULA SEGUNDA', ['A QUALICONTAX assume inteira responsabilidade pelos serviços técnicos a que se obrigou, assim como pelas orientações que prestar.']))
        cl.append(('CLÁUSULA TERCEIRA', [
            'A %s se obriga a preparar, mensalmente, toda a documentação fisco-contábil e de pessoal, que deverá ser disponibilizada à QUALICONTAX em tempo hábil, '
            'conforme cronograma pactuado entre as partes, a fim de que possa executar seus serviços na conformidade com o citado neste instrumento.' % apelido,
            'PARÁGRAFO PRIMEIRO. Responsabilizar-se-á a QUALICONTAX por todos os documentos a ela entregues pela %s, enquanto permanecerem sob sua guarda para a '
            'consecução dos serviços pactuados, salvo comprovados casos fortuitos e motivos de força maior.' % apelido,
            'PARÁGRAFO SEGUNDO. A %s tem ciência da Lei 9.613/98, alterada pela Lei 12.683/2012, especificamente no que trata da lavagem de dinheiro, '
            'regulamentada pela Resolução CFC n.º 1.445/13 do Conselho Federal de Contabilidade.' % apelido]))
        if 'CONTABIL' in areas:
            cl.append(('CLÁUSULA QUARTA', ['A QUALICONTAX se obriga, antes do encerramento do exercício social, a fornecer à %s a Carta de Responsabilidade da Administração.' % apelido]))
        cl.append(('CLÁUSULA', ['As orientações dadas pela QUALICONTAX deverão ser seguidas pela %s, eximindo-se a primeira das consequências da não observância do seu cumprimento.' % apelido]))
        entrega = ('os balancetes, o Balanço Patrimonial e as demais demonstrações contábeis, documentos' if 'CONTABIL' in areas
                   else 'as informações das obrigações acessórias e documentos')
        cl.append(('CLÁUSULA', [
            'A QUALICONTAX se obriga a entregar à contratante, mediante protocolo eletrônico ou físico, com tempo hábil, %s necessários para que esta efetue os devidos '
            'pagamentos e recolhimentos obrigatórios, bem como comprovante de entrega das obrigações acessórias.' % entrega,
            'PARÁGRAFO ÚNICO. As multas decorrentes da entrega fora do prazo contratado das obrigações previstas no caput desta cláusula, ou que forem decorrentes '
            'da imperfeição ou inexecução dos serviços por parte da QUALICONTAX, serão de sua responsabilidade.']))
        valor = _d(dados.get('valor'))
        dia = int(dados.get('dia_vencimento') or 10)
        # 25/11/2026 — rede: quem paga (PAGA), quem está coberta (VIA_GRUPO) e quem não movimenta (SEM_MOVIMENTO)
        pagantes = [e for e in empresas if (e.get('situacao') or 'PAGA') == 'PAGA']    # contrato antigo sem situação: todas pagam
        via = [e for e in empresas if e.get('situacao') == 'VIA_GRUPO']
        sem = [e for e in empresas if e.get('situacao') == 'SEM_MOVIMENTO']
        venc = 'com vencimento todo dia %d (%s) do mês' % (dia, _dia_extenso(dia))
        if len(empresas) > 1 and len(pagantes) == 1 and (via or sem):
            p0 = pagantes[0]
            todas = 'a todas as contratantes acima qualificadas' if apelido == 'CONTRATANTE' else 'a todas as empresas do grupo %s' % apelido
            hon = ['Pelos serviços prestados %s, a contratante %s, inscrita no %s nº %s, pagará à QUALICONTAX os honorários mensais de %s (%s), %s.'
                   % (todas, p0['razao'], 'CPF' if _pf(p0) else 'CNPJ', p0['cnpj'], brl(valor), reais_extenso(valor), venc)]
        elif len(empresas) > 1 and len(pagantes) > 1 and dados.get('valores_por_empresa'):
            hon = ['A %s pagará à QUALICONTAX pelos serviços prestados os honorários mensais de %s (%s), %s, assim distribuídos entre as empresas pagantes: %s.'
                   % (apelido, brl(valor), reais_extenso(valor), venc, '; '.join('%s: %s' % (e['razao'], brl(e.get('valor'))) for e in pagantes))]
        else:
            hon = ['A %s pagará à QUALICONTAX pelos serviços prestados os honorários mensais de %s (%s), %s.' % (apelido, brl(valor), reais_extenso(valor), venc)]
        paras = []
        if via:
            paras.append('Os serviços prestados a %s estão abrangidos pelos honorários acima, sem cobrança adicional, por integrar%s o mesmo grupo econômico da empresa pagante.'
                         % (_nomes(via), 'em' if len(via) > 1 else ''))
        if sem:
            paras.append('%s, atualmente sem movimento, não ter%s cobrança de honorários enquanto permanecer%s nessa situação; ao iniciar%s suas atividades, os honorários '
                         'correspondentes serão fixados por aditivo a este contrato.' % (_nomes(sem), 'ão' if len(sem) > 1 else 'á', 'em' if len(sem) > 1 else '', 'em' if len(sem) > 1 else ''))
        rt = dados.get('reajuste_tipo') or 'SALARIO_MINIMO'
        reaj = REAJUSTE_TXT.get(rt, REAJUSTE_TXT['SALARIO_MINIMO'])
        # 25/11/2026: o índice vem do cadastro (sigla + nome por extenso); escalonado tem o próprio texto
        idx = ReajusteIndice.texto(dados.get('reajuste_indice')) if rt in ('INDICE', 'DATA_PROPRIA') else ''
        escala = dados.get('reajuste_escala') or dados.get('reajuste_indice') or 'combinada entre as partes'
        reaj = reaj.format(indice=escala if rt == 'ESCALONADO' else (idx or 'índice combinado entre as partes'), mes=dados.get('reajuste_mes') or 'janeiro',
                           indice_txt=('pela variação acumulada do %s, ou de outro índice oficial que venha a substituí-lo' % idx) if idx
                           else 'pelo mesmo percentual de reajuste do salário mínimo nacional')
        paras.append(reaj)
        paras.append('Os honorários pagos após a data acarretarão à %s o acréscimo de multa de %s%% (%s por cento), sem prejuízo de juros moratórios de %s%% '
                     '(%s por cento) ao mês ou fração.' % (apelido, dados.get('multa') or '10', _pct_extenso(dados.get('multa') or '10'),
                                                            dados.get('juros') or '1', _pct_extenso(dados.get('juros') or '1')))
        ordp = ['PRIMEIRO', 'SEGUNDO', 'TERCEIRO', 'QUARTO', 'QUINTO', 'SEXTO']
        hon += ['PARÁGRAFO %s. %s' % (ordp[i], p) for i, p in enumerate(paras)]
        cl.append(('CLÁUSULA', hon))
        if dados.get('decimo') == 'COBRADO':
            cl.append(('CLÁUSULA', ['Nos meses de novembro e dezembro de cada ano, será cobrado o equivalente a 1 (um) honorário mensal, dividido em duas parcelas iguais, '
                                     'a serem pagas junto com os honorários vencíveis em novembro e dezembro, por conta do encerramento do Balanço Patrimonial e demais obrigações anuais.']))
        elif dados.get('decimo') == 'UNICA':
            cl.append(('CLÁUSULA', ['No mês de dezembro de cada ano, será cobrado o equivalente a 1 (um) honorário mensal, a ser pago junto com os honorários vencíveis em dezembro, '
                                     'por conta do encerramento do Balanço Patrimonial e demais obrigações anuais.']))
        else:
            cl.append(('CLÁUSULA', ['Fica dispensada a cobrança do 13º honorário referente ao encerramento do Balanço Patrimonial e demais obrigações anuais.']))
        cl.append(('CLÁUSULA', ['Todos os serviços extraordinários não contratados que forem necessários ou solicitados pela contratante serão cobrados à parte, com preços previamente convencionados.']))
        cl.append(('CLÁUSULA', ['No caso de atraso no pagamento dos honorários, por período de 2 (dois) meses, a QUALICONTAX poderá rescindir o contrato, por motivo justificado, '
                                 'eximindo-se de qualquer responsabilidade a partir da data da rescisão.']))
        inicio = dados.get('inicio')
        inicio_d = datetime.strptime(inicio, '%Y-%m-%d').date() if isinstance(inicio, str) and inicio else (inicio or date.today())
        aviso = int(dados.get('aviso_previo') or 30)
        cl.append(('CLÁUSULA', [
            'Este instrumento é feito por tempo indeterminado, iniciando-se em %s, podendo ser rescindido em qualquer época, por qualquer uma das partes, mediante Aviso Prévio '
            'de %d (%s) dias, por escrito.' % (inicio_d.strftime('%d/%m/%Y'), aviso, _dia_extenso(aviso)),
            'PARÁGRAFO PRIMEIRO. A parte que não comunicar por escrito a intenção de rescindir o contrato ou efetuá-la de forma sumária fica obrigada ao pagamento de multa '
            'compensatória no valor de uma parcela mensal dos honorários vigentes à época.',
            'PARÁGRAFO SEGUNDO. O rompimento do vínculo contratual obriga as partes à celebração de distrato com a especificação da cessação das responsabilidades dos contratantes.',
            'PARÁGRAFO TERCEIRO. A QUALICONTAX obriga-se a entregar os documentos, Livros Contábeis e Fiscais e/ou arquivos eletrônicos à contratante ou a outro profissional da '
            'Contabilidade por ela indicado, após a assinatura do distrato entre as partes.']))
        if dados.get('lgpd', True):
            cl.append(('CLÁUSULA', [
                'A Lei Geral de Proteção de Dados (LGPD) será obedecida, em todos os termos, pela QUALICONTAX, obrigando-se ela a tratar os dados da %s que forem eventualmente '
                'coletados, conforme sua necessidade ou obrigatoriedade (Art. 7º, LGPD).' % apelido,
                'PARÁGRAFO PRIMEIRO. Conforme prevê a Lei Geral de Proteção de Dados, obriga-se a QUALICONTAX a executar os seus trabalhos e tratar os dados da %s respeitando os '
                'princípios da finalidade, adequação, transparência, livre acesso, segurança, prevenção e não discriminação (Art. 6º, LGPD).' % apelido,
                'PARÁGRAFO SEGUNDO. A QUALICONTAX obriga-se a garantir a confidencialidade dos dados coletados da %s por meio de uma política interna de privacidade, a fim de '
                'respeitar, por si, seus funcionários e seus prepostos, o objetivo do presente termo (Art. 5º, LGPD).' % apelido,
                'PARÁGRAFO TERCEIRO. Eventuais dados coletados pela QUALICONTAX serão arquivados por esta somente pelo tempo necessário para execução dos serviços contratados. '
                'Ao seu fim, os dados coletados serão permanentemente eliminados, excetuando-se os que se enquadrarem no disposto no artigo 16, I da Lei Geral de Proteção de Dados (Art. 15, LGPD).']))
        cl.append(('CLÁUSULA', ['Os casos omissos serão resolvidos de comum acordo.',
                                 'PARÁGRAFO ÚNICO. Em caso de impasse, as partes submeterão a solução do conflito a procedimento arbitral nos termos da Lei n.º 9.307/96.']))
        cl.append(('CLÁUSULA', ['Fica eleito o foro da comarca de %s, para nele dirimir eventuais conflitos decorrentes do presente contrato, em detrimento a qualquer outro foro.'
                                 % (dados.get('foro') or 'São Paulo/SP')]))
        cl.append(('CLÁUSULA', ['Este contrato é celebrado e assinado eletronicamente pelas partes e testemunhas, por meio do sistema da QUALICONTAX, com confirmação de identidade '
                                 'por código enviado ao e-mail de cada signatário, nos termos do art. 10, § 2º, da Medida Provisória nº 2.200-2/2001 e do art. 4º da Lei nº 14.063/2020. '
                                 'O registro das assinaturas (data, hora, e-mail, endereço IP e a impressão digital SHA-256 do documento) integra este instrumento.']))
        # numeração das cláusulas
        ordinais = ['PRIMEIRA', 'SEGUNDA', 'TERCEIRA', 'QUARTA', 'QUINTA', 'SEXTA', 'SÉTIMA', 'OITAVA', 'NONA', 'DÉCIMA', 'DÉCIMA PRIMEIRA', 'DÉCIMA SEGUNDA',
                    'DÉCIMA TERCEIRA', 'DÉCIMA QUARTA', 'DÉCIMA QUINTA', 'DÉCIMA SEXTA', 'DÉCIMA SÉTIMA', 'DÉCIMA OITAVA']
        clausulas = [('CLÁUSULA %s.' % ordinais[i], paras) for i, (_, paras) in enumerate(cl)]
        n_test = len([a for a in (dados.get('assinantes') or []) if a.get('papel') == 'TESTEMUNHA'])
        fecho = ('E, por estarem justos e contratados, assinam o presente eletronicamente, em uma única via digital de igual teor e para um só efeito'
                 + (', na presença de %d (%s) testemunha%s.' % (n_test, {1: 'uma', 2: 'duas'}.get(n_test, _dia_extenso(n_test)), 's' if n_test > 1 else '') if n_test else '.'))
        # a data do fecho é a da assinatura (quando o contrato é fechado); antes disso, a de hoje
        local = '%s, %s' % (dados.get('local') or 'São Paulo', data_extenso(_data(dados.get('data_assinatura')) or date.today()))
        areas_txt = ', '.join(nome_proprio(AREAS[a][0]) for a in areas) + ((' + ' + '; '.join(dados.get('servicos_extras'))) if dados.get('servicos_extras') else '')
        decimo_txt = {'COBRADO': '1 honorário mensal, em duas parcelas (novembro e dezembro)', 'UNICA': '1 honorário mensal, em dezembro'}.get(dados.get('decimo'), 'isento')
        resumo = [('Objeto', areas_txt or '—'), ('Honorários mensais', '%s, com vencimento todo dia %d' % (brl(valor), dia) + ((' · paga %s' % pagantes[0]['razao']) if len(empresas) > 1 and len(pagantes) == 1 else '')),
                  ('Início', inicio_d.strftime('%d/%m/%Y')), ('13º honorário', decimo_txt),
                  ('Reajuste', {'SALARIO_MINIMO': 'salário mínimo, todo janeiro', 'INDICE': 'índice próprio', 'DATA_PROPRIA': 'data própria', 'SEM': 'de comum acordo', 'ESCALONADO': 'escalonado'}.get(dados.get('reajuste_tipo') or 'SALARIO_MINIMO')),
                  ('Aviso prévio', '%d dias' % aviso), ('Foro', dados.get('foro') or 'São Paulo/SP')]
        # ficha estruturada para a folha de rosto do PDF (23/11/2026: "PDF elegante como o do orçamento")
        rt = dados.get('reajuste_tipo') or 'SALARIO_MINIMO'
        reaj_curto = {'SALARIO_MINIMO': ('salário mínimo', 'todo janeiro'), 'INDICE': ('pelo %s' % (dados.get('reajuste_indice') or 'índice combinado'), 'anual'),
                      'DATA_PROPRIA': ('em %s' % (dados.get('reajuste_mes') or 'data própria'), dados.get('reajuste_indice') or 'salário mínimo'), 'SEM': ('de comum acordo', ''),
                      'ESCALONADO': ('escalonado', dados.get('reajuste_escala') or dados.get('reajuste_indice') or '')}.get(rt, ('de comum acordo', ''))
        decimo_curto = {'COBRADO': ('em 2 parcelas', 'novembro e dezembro'), 'UNICA': ('em dezembro', '1 honorário mensal')}.get(dados.get('decimo'), ('isento', ''))
        e0 = empresas[0] if empresas else {}
        ficha = {'valor': valor, 'dia': dia, 'inicio': inicio_d, 'areas': [(AREAS[a][0], list(AREAS[a][1])) for a in areas],
                 'extras': [x.strip() for x in (dados.get('servicos_extras') or []) if (x or '').strip()],
                 'reajuste': reaj_curto, 'decimo': decimo_curto, 'aviso': aviso, 'foro': dados.get('foro') or 'São Paulo/SP',
                 'multa': str(dados.get('multa') or '10').replace('.', ','), 'juros': str(dados.get('juros') or '1').replace('.', ','),
                 'valores_por_empresa': bool(len(empresas) > 1 and dados.get('valores_por_empresa') and len(pagantes) > 1),
                 'pagantes': [e['razao'] for e in pagantes], 'via_grupo': [e['razao'] for e in via], 'sem_movimento': [e['razao'] for e in sem],
                 'representante': {'nome': e0.get('rep_nome') or '', 'cargo': e0.get('rep_cargo') or '', 'cpf': e0.get('rep_cpf') or ''},
                 'extra': ex if (ex and ex.get('nome')) else None, 'assina': q, 'n_testemunhas': n_test}
        return {'titulo': titulo, 'partes': partes, 'preambulo': preambulo, 'clausulas': clausulas, 'fecho': fecho, 'local_data': local,
                'assinantes': dados.get('assinantes') or [], 'empresas': empresas, 'apelido': apelido, 'resumo': resumo, 'ficha': ficha,
                'contratada': qc, 'cabecalho': qc.get('cabecalho') or QUALICONTAX['cabecalho']}


def _data(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date() if s else None
    except Exception:
        return None


def _nomes(empresas):
    """'A', 'A e B', 'A, B e C' — razões sociais para o texto do contrato."""
    r = [e['razao'] for e in empresas]
    return r[0] if len(r) == 1 else ', '.join(r[:-1]) + ' e ' + r[-1]


def _dia_extenso(n):
    from utils.extenso import inteiro_extenso
    return inteiro_extenso(int(n))


def _pct_extenso(p):
    from utils.extenso import inteiro_extenso
    s = str(p).replace(',', '.')
    try:
        v = Decimal(s)
    except Exception:
        return str(p)
    if v == int(v):
        return inteiro_extenso(int(v))
    return str(p).replace('.', ',')
