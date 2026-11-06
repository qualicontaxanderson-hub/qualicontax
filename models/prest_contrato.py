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
import secrets
from datetime import date, datetime
from decimal import Decimal

from utils.db_helper import execute_query
from utils.extenso import reais_extenso

QUALICONTAX = {
    'razao': 'QUALICONTAX ASSESSORIA CONTÁBIL LTDA – EPP',
    'endereco': 'Estrada de Mogi das Cruzes, 221, 1º Andar, Jardim Soraia, na cidade São Paulo, no Estado de SP, 03890-100',
    'cnpj': '11.158.475/0001-27', 'crc': '2SP025.952/O-7',
    'cabecalho': ['Estrada de Mogi das Cruzes, 221 – 1º Andar', 'Jardim Soraia – São Paulo – SP – 03890-100',
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
    'FISCAL': ('OBRIGAÇÕES FISCAIS', [
        'Orientação e controle de aplicação dos dispositivos legais vigentes, sejam federais, estaduais ou municipais;',
        'Elaboração dos registros fiscais obrigatórios (EFD ICMS/IPI, GIA e outros), eletrônicos ou não, perante os órgãos municipais, '
        'estaduais e federais, bem como as demais obrigações que se fizerem necessárias; e',
        'Atendimento às demais exigências previstas na legislação, bem como aos eventuais procedimentos fiscais.']),
    'PESSOAL': ('DEPARTAMENTO DE PESSOAL', [
        'Registros de empregados e serviços correlatos;',
        'Elaboração da folha de pagamento dos empregados e de pró-labore, bem como das guias de recolhimento dos encargos sociais e tributos afins;',
        'Elaboração, orientação e controle da aplicação dos preceitos da Consolidação das Leis do Trabalho, bem como daqueles atinentes à '
        'Previdência Social e de outros aplicáveis às relações de trabalho mantidas pela contratante;']),
}
TITULOS = {'CONTABIL': 'CONTRATO DE PRESTAÇÃO DE SERVIÇOS CONTÁBEIS', 'FISCAL': 'CONTRATO DE PRESTAÇÃO DE SERVIÇOS FISCAIS',
           'PESSOAL': 'CONTRATO DE PRESTAÇÃO DE SERVIÇOS DE DEPARTAMENTO PESSOAL'}
STATUS = ('RASCUNHO', 'ENVIADO', 'ASSINADO', 'CANCELADO')
REAJUSTE_TXT = {
    'SALARIO_MINIMO': 'Os honorários serão reajustados anualmente, no mês de janeiro, pelo mesmo percentual de reajuste do salário mínimo nacional.',
    'INDICE': 'Os honorários serão reajustados anualmente pelo índice {indice}.',
    'DATA_PROPRIA': 'Os honorários serão reajustados anualmente, no mês de {mes}, {indice}.',
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


class PrestContrato:

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
        c['cidade'] = (e.get('cidade') or '').strip()
        c['uf'] = (e.get('estado') or '').strip()
        c['socios'] = execute_query("""SELECT nome, cpf, qualificacao, responsavel, email FROM socios_clientes
                                        WHERE cliente_id = %s AND ativo = 1 ORDER BY responsavel DESC, nome""", (cliente_id,), fetch=True) or []
        return c

    @staticmethod
    def testemunhas_disponiveis():
        return execute_query("""SELECT id, nome, cpf, email FROM usuarios WHERE classe_conta = 'FUNCIONARIO' AND situacao = 'ATIVO'
                                 ORDER BY nome""", fetch=True) or []

    # ---------------- gravação ----------------
    @staticmethod
    def criar(cliente_id, dados, criado_por, fin_contrato_id=None, tipo='CONTRATO', anterior_id=None):
        titulo = dados.get('titulo') or PrestContrato.titulo_de(dados)
        cid = execute_query(
            """INSERT INTO prest_contratos (cliente_id, fin_contrato_id, tipo, anterior_id, titulo, dados, criado_por)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (cliente_id, fin_contrato_id, tipo, anterior_id, titulo[:120], json.dumps(dados, ensure_ascii=False, default=str), criado_por), fetch=False)
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
    def listar(status=None, cliente_id=None):
        cond, params = [], []
        if status:
            cond.append('p.status = %s'); params.append(status)
        if cliente_id:
            cond.append('(p.cliente_id = %s OR p.id IN (SELECT contrato_id FROM prest_contrato_empresas WHERE cliente_id = %s))'); params += [cliente_id, cliente_id]
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        rows = execute_query(
            f"""SELECT p.id, p.cliente_id, p.tipo, p.titulo, p.status, p.criado_em, p.enviado_em, p.assinado_em, p.pdf_path,
                       cl.numero_cliente, cl.nome_razao_social, u.nome AS criado_por_nome,
                       (SELECT COUNT(*) FROM prest_contrato_empresas e WHERE e.contrato_id = p.id) AS n_empresas,
                       (SELECT COUNT(*) FROM prest_assinantes a WHERE a.contrato_id = p.id) AS n_assinantes,
                       (SELECT COUNT(*) FROM prest_assinantes a WHERE a.contrato_id = p.id AND a.assinado_em IS NOT NULL) AS n_assinados
                  FROM prest_contratos p JOIN clientes cl ON cl.id = p.cliente_id LEFT JOIN usuarios u ON u.id = p.criado_por
                 {where} ORDER BY p.id DESC""", tuple(params), fetch=True) or []
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
        if not assinante.get('codigo_enviado_em') or (datetime.now() - assinante['codigo_enviado_em']).total_seconds() > 30 * 60:
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
        if len(empresas) == 1:
            e = empresas[0]
            partes.append('%s, estabelecida à %s, devidamente inscrita no CNPJ nº %s, neste ato representada por seu %s, %s, portador(a) %s e CPF nº %s, '
                          'ora e doravante denominada de %s.' % (e['razao'], e['endereco'], e['cnpj'], e.get('rep_cargo') or 'sócio administrador',
                                                                e['rep_nome'], e.get('rep_documento') or 'de documento de identidade', e.get('rep_cpf') or '', apelido))
        else:
            lista = '; '.join('%s, estabelecida à %s, devidamente inscrita no CNPJ nº %s' % (e['razao'], e['endereco'], e['cnpj']) for e in empresas)
            e = empresas[0]
            partes.append('%s; neste ato representadas por seu %s, %s, portador(a) %s e CPF nº %s, ora e doravante denominadas em conjunto de %s.'
                          % (lista, e.get('rep_cargo') or 'sócio administrador', e['rep_nome'], e.get('rep_documento') or 'de documento de identidade',
                             e.get('rep_cpf') or '', apelido))
        q = SIGNATARIOS_QUALICONTAX.get(dados.get('assina_qualicontax') or 'albert')
        partes.append('%s, estabelecida à %s, devidamente inscrita no CNPJ nº %s, CRC nº %s, neste ato representada por seu %s, %s, portador %s e CPF nº %s, '
                      'ora e doravante denominada de QUALICONTAX.' % (QUALICONTAX['razao'], QUALICONTAX['endereco'], QUALICONTAX['cnpj'], QUALICONTAX['crc'],
                                                                      q['cargo'], q['nome'], q['documento'], q['cpf']))
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
        hon = ['A %s pagará à QUALICONTAX pelos serviços prestados os honorários mensais de %s (%s), com vencimento todo dia %d (%s) do mês.'
               % (apelido, brl(valor), reais_extenso(valor), dia, _dia_extenso(dia))]
        if len(empresas) > 1 and dados.get('valores_por_empresa'):
            hon.append('PARÁGRAFO PRIMEIRO. Os honorários são compostos pelos valores de cada empresa: ' +
                       '; '.join('%s: %s' % (e['razao'], brl(e.get('valor'))) for e in empresas) + '.')
        reaj = REAJUSTE_TXT.get(dados.get('reajuste_tipo') or 'SALARIO_MINIMO', REAJUSTE_TXT['SALARIO_MINIMO'])
        reaj = reaj.format(indice=dados.get('reajuste_indice') or 'combinado entre as partes', mes=dados.get('reajuste_mes') or 'janeiro')
        hon.append('PARÁGRAFO %s. %s' % ('SEGUNDO' if len(hon) > 1 else 'PRIMEIRO', reaj))
        hon.append('PARÁGRAFO %s. Os honorários pagos após a data acarretarão à %s o acréscimo de multa de %s%% (%s por cento), sem prejuízo de juros moratórios de %s%% '
                   '(%s por cento) ao mês ou fração.' % ('TERCEIRO' if len(hon) > 2 else 'SEGUNDO', apelido, dados.get('multa') or '10', _pct_extenso(dados.get('multa') or '10'),
                                                          dados.get('juros') or '1', _pct_extenso(dados.get('juros') or '1')))
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
        return {'titulo': titulo, 'partes': partes, 'preambulo': preambulo, 'clausulas': clausulas, 'fecho': fecho, 'local_data': local,
                'assinantes': dados.get('assinantes') or [], 'empresas': empresas}


def _data(s):
    try:
        return datetime.strptime(s, '%Y-%m-%d').date() if s else None
    except Exception:
        return None


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
