# -*- coding: utf-8 -*-
"""Extrato bancário importado (Documento E, fase 4).

Tabela compartilhada com o futuro A2: ``empresa_id`` NULL = escritório
(Qualicontax); cliente da carteira quando o A2 chegar. A idempotência do
import mora no ``hash_dedup`` (UNIQUE) — quem monta a chave é
utils.ofx_parser.chave_dedup, a mesma para parser e gravação.
"""
import json
import re

from utils.db_helper import execute_query


class ExtratoLancamento:

    # Um lugar só monta o WHERE: a listagem e os cartões contam a MESMA
    # história (o cartão fala do filtro inteiro, a tabela mostra a página).
    @staticmethod
    def _where(empresa_ids=None, data_de=None, data_ate=None, conta=None,
               busca=None, classif=None, categoria_id=None, centro_id=None,
               tipo=None, documento=None, vmin=None, vmax=None):
        cond, params = ['1=1'], []
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond.append(f'e.empresa_id IN ({marks})')
            params += list(empresa_ids)
        if data_de:
            cond.append('e.data >= %s')
            params.append(data_de)
        if data_ate:
            cond.append('e.data <= %s')
            params.append(data_ate)
        if conta:
            cond.append("CONCAT(COALESCE(e.banco,''), ' · ', COALESCE(e.conta,'')) = %s")
            params.append(conta)
        if busca:
            cond.append('(e.descricao LIKE %s OR e.documento LIKE %s)')
            like = f'%{busca}%'
            params += [like, like]
        if documento:
            cond.append('e.documento LIKE %s')
            params.append(f'%{documento}%')
        if classif == 'conferir':
            cond.append('e.conferir = 1')
        elif classif == 'sim':
            cond.append('e.categoria_id IS NOT NULL')
        elif classif == 'nao':
            cond.append('e.categoria_id IS NULL')
        if categoria_id:
            cond.append('e.categoria_id = %s')
            params.append(categoria_id)
        if centro_id == 'sem':
            cond.append('e.centro_custo_id IS NULL')
        elif centro_id:
            cond.append('e.centro_custo_id = %s')
            params.append(centro_id)
        if tipo == 'credito':
            cond.append('e.valor >= 0')
        elif tipo == 'debito':
            cond.append('e.valor < 0')
        if vmin not in (None, ''):
            cond.append('ABS(e.valor) >= %s')
            params.append(vmin)
        if vmax not in (None, ''):
            cond.append('ABS(e.valor) <= %s')
            params.append(vmax)
        return ' AND '.join(cond), params

    @staticmethod
    def listar(limite=500, **f):
        where, params = ExtratoLancamento._where(**f)
        return execute_query(
            f"""SELECT e.id, e.empresa_id, e.banco, e.conta, e.data, e.valor,
                       e.tipo, e.descricao, e.documento, e.fitid, e.origem,
                       e.arquivo, e.criado_em, e.categoria_id, e.centro_custo_id,
                       e.memorizacao_id, e.conferir, c.nome AS categoria_nome,
                       c.grupo AS categoria_grupo, cc.nome AS centro_nome
                  FROM extrato_lancamentos e
                  LEFT JOIN fin_categorias c ON c.id = e.categoria_id
                  LEFT JOIN fin_centros_custo cc ON cc.id = e.centro_custo_id
                 WHERE {where}
                 ORDER BY e.data DESC, e.id DESC
                 LIMIT {int(limite)}""",
            tuple(params), fetch=True) or []

    @staticmethod
    def totais(**f):
        """Créditos, débitos, contagem e SEM CATEGORIA — do FILTRO inteiro.

        A contagem de sem-categoria ignora o filtro de classificação (senão,
        estando em "Sem categoria", o cartão repetiria o total da tela).
        """
        where, params = ExtratoLancamento._where(**f)
        base = execute_query(
            f"""SELECT COUNT(*) AS n,
                       COALESCE(SUM(CASE WHEN e.valor >= 0 THEN e.valor END), 0) AS creditos,
                       COALESCE(SUM(CASE WHEN e.valor < 0 THEN e.valor END), 0)  AS debitos
                  FROM extrato_lancamentos e WHERE {where}""",
            tuple(params), fetch=True, fetch_one=True) or {}
        f_sem = dict(f)
        f_sem['classif'] = 'nao'
        w2, p2 = ExtratoLancamento._where(**f_sem)
        sem = execute_query(
            f'SELECT COUNT(*) AS n FROM extrato_lancamentos e WHERE {w2}',
            tuple(p2), fetch=True, fetch_one=True) or {}
        base['sem_cat'] = int(sem.get('n') or 0)
        # A CONFERIR ignora o filtro de classificacao pelo mesmo motivo do
        # sem-categoria: estando na propria aba, o cartao repetiria o total.
        f_conf = dict(f)
        f_conf['classif'] = 'conferir'
        w3, p3 = ExtratoLancamento._where(**f_conf)
        conf = execute_query(
            f'SELECT COUNT(*) AS n FROM extrato_lancamentos e WHERE {w3}',
            tuple(p3), fetch=True, fetch_one=True) or {}
        base['a_conferir'] = int(conf.get('n') or 0)
        return base

    @staticmethod
    def contas(empresa_ids=None):
        cond, params = '1=1', ()
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond, params = f'empresa_id IN ({marks})', tuple(empresa_ids)
        rows = execute_query(
            f"""SELECT DISTINCT CONCAT(COALESCE(banco,''), ' · ',
                       COALESCE(conta,'')) AS rotulo
                  FROM extrato_lancamentos WHERE {cond} ORDER BY rotulo""",
            params, fetch=True) or []
        return [r['rotulo'] for r in rows]

    #: CPF (11) ou CNPJ (14) soltos no meio da descricao.
    _DOC = re.compile(r'\b(\d{11}|\d{14})\b')

    @staticmethod
    def ler_descricao(desc):
        """Quebra a descricao do banco em (documento, nome, antes).

        O extrato do Sicredi tem forma fixa — ``TIPO-CANAL  DOC  NOME`` — e e
        isso que permite mostrar o nome de quem esta do outro lado sem trocar a
        descricao por um resumo: a tela imprime ``antes`` em letra de maquina e
        ``nome`` em negrito, e o texto continua literal.

        Devolve dict com doc/nome/antes; todos '' quando nao ha documento
        (``CESTA DE RELACIONAMENTO-``, ``PAGAMENTO SEFAZ GO-IB0004``).

        O espaco e normalizado porque o extrato vem com espaco duplo
        (``PIX_DEB   00394460005887``): comparar sem normalizar fazia o mesmo
        texto nao casar consigo mesmo.
        """
        d = ExtratoLancamento.corrigir_acento(desc)
        d = re.sub(r'\s+', ' ', d or '').strip()
        m = ExtratoLancamento._DOC.search(d)
        if m:
            return {'doc': m.group(1),
                    'nome': d[m.end():].strip(),
                    'antes': d[:m.end()].strip()}

        # Sem CPF/CNPJ ainda pode haver nome: na tarifa de cobranca o codigo
        # do banco tem 9 digitos ("...COB000001 262005312 DISTRIBUIDORA DE
        # COMBUSTIVEIS SAARA"). Cai no ULTIMO bloco so-numeros; o que vem
        # depois dele e o nome.
        ult = None
        for n in re.finditer(r'\b\d{4,}\b', d):
            ult = n
        if ult and d[ult.end():].strip():
            return {'doc': '', 'nome': d[ult.end():].strip(),
                    'antes': d[:ult.end()].strip()}
        return {'doc': '', 'nome': '', 'antes': d}

    @staticmethod
    def corrigir_acento(texto):
        """Desfaz UTF-8 lido como Latin-1 (``RogÃ©rio`` -> ``Rogério``).

        Dois dos 511 lancamentos importados vieram assim. O conserto de raiz e
        na leitura do OFX; aqui e so para a tela nao mostrar lixo enquanto os
        antigos nao forem corrigidos, e e inofensivo em quem esta certo.
        """
        t = texto or ''
        if 'Ã' not in t and 'Â' not in t:
            return t
        try:
            return t.encode('latin-1').decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            return t

    @staticmethod
    def formatar_doc(doc):
        """CPF/CNPJ pontuado. Devolve o proprio texto se nao for nenhum dos dois."""
        d = (doc or '').strip()
        if len(d) == 14:
            return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
        if len(d) == 11:
            return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
        return d

    @staticmethod
    def contas_mapa():
        """conta_norm -> {apelido, agencia} do CADASTRO de contas.

        O extrato guarda o nome cru do OFX, e para o Sicredi ele vem como
        'CCPI DO CERRADO DE GO' — o nome da cooperativa, que ninguem no
        escritorio usa. Quem sabe o nome que a pessoa reconhece e fin_contas.
        """
        rows = execute_query(
            'SELECT conta_norm, conta, apelido, banco_nome, agencia '
            '  FROM fin_contas', fetch=True) or []
        mapa = {}
        for r in rows:
            dado = {'apelido': r['apelido'] or r['banco_nome'] or '',
                    'agencia': r['agencia'] or ''}
            for chave in (r['conta_norm'], r['conta']):
                if chave:
                    mapa[str(chave)] = dado
        return mapa

    @staticmethod
    def por_dia(lancamentos):
        """Agrupa a lista em dias, com o resumo do dia.

        A data aparece UMA vez, no alto do grupo — nunca dentro do lancamento.
        Montado aqui e nao no Jinja porque decidir 'mudou o dia?' no template
        exige comparar a linha com a anterior, e foi exatamente isso que
        quebrou a tela de categorias quando a ordem veio torta.
        """
        dias, atual = [], None
        for l in lancamentos:
            if atual is None or atual['data'] != l['data']:
                atual = {'data': l['data'], 'itens': [], 'entradas': 0, 'saidas': 0}
                dias.append(atual)
            atual['itens'].append(l)
            v = float(l['valor'] or 0)
            if v < 0:
                atual['saidas'] += v
            else:
                atual['entradas'] += v
        return dias

    @staticmethod
    def classificar(lanc_id, categoria_id, centro_custo_id=None,
                    memorizacao_id=None):
        execute_query(
            'UPDATE extrato_lancamentos SET categoria_id = %s, '
            'centro_custo_id = %s, memorizacao_id = %s WHERE id = %s',
            (categoria_id, centro_custo_id, memorizacao_id, lanc_id))
        r = execute_query('SELECT categoria_id FROM extrato_lancamentos '
                          'WHERE id = %s', (lanc_id,), fetch=True, fetch_one=True)
        return bool(r and r['categoria_id'] == categoria_id)

    @staticmethod
    def get(lanc_id):
        return execute_query('SELECT * FROM extrato_lancamentos WHERE id = %s',
                             (lanc_id,), fetch=True, fetch_one=True)

    @staticmethod
    def titulos_candidatos(lanc, limite=6):
        """Titulos em aberto que PODEM ser este pagamento, o melhor primeiro.

        Melhor = mesmo documento da contraparte, depois saldo que bate com o
        valor, depois vencimento mais perto da data do lancamento. So entram
        titulos da MESMA empresa, do tipo certo (credito quita a-receber,
        debito quita a-pagar) e com saldo que comporta o valor — casar num
        titulo menor criaria baixa maior que o titulo sem ninguem pedir
        juros, e isso e decisao humana, nao chute de ranking.
        """
        valor = abs(float(lanc.get('valor') or 0))
        tipo = 'R' if float(lanc.get('valor') or 0) >= 0 else 'P'
        doc = (ExtratoLancamento.ler_descricao(lanc.get('descricao'))
               .get('doc') or '')
        rows = execute_query(
            """SELECT t.id, t.contraparte_nome, t.contraparte_doc, t.descricao,
                      t.competencia, t.vencimento, t.valor, t.valor_baixado,
                      c.nome AS categoria_nome, c.grupo AS categoria_grupo
                 FROM fin_titulos t
                 JOIN fin_categorias c ON c.id = t.categoria_id
                WHERE t.empresa_id = %s AND t.tipo = %s
                  AND t.status IN ('aberto', 'parcial')
                  AND (t.valor - t.valor_baixado) >= %s - 0.01""",
            (lanc.get('empresa_id'), tipo, valor), fetch=True) or []

        data = lanc.get('data')

        def peso(t):
            saldo = float(t['valor']) - float(t['valor_baixado'] or 0)
            doc_bate = 0 if (doc and t['contraparte_doc'] == doc) else 1
            valor_bate = 0 if abs(saldo - valor) <= 0.01 else 1
            dist = abs((t['vencimento'] - data).days) if (t['vencimento'] and data) else 9999
            return (doc_bate, valor_bate, dist)

        rows.sort(key=peso)
        return rows[:limite]

    @staticmethod
    def conciliar(lanc, titulo_id=None, criar=None, usuario_id=None):
        """Amarra o lancamento a um titulo — casando ou criando ja quitado.

        A baixa passa por registrar_baixa, o UNICO escritor autorizado
        (regra de ouro do Documento E). A referencia e o hash_dedup do
        lancamento: e ele que torna a segunda tentativa inofensiva.

        ``criar`` (dict competencia/contraparte_nome/contraparte_doc/
        categoria_id/centro_custo_id/descricao) monta o titulo com origem
        'extrato' e chave_idem propria — reprocessar o mesmo lancamento nao
        duplica o titulo.

        Devolve (ok, motivo, titulo_id).
        """
        from models.fin_titulo import FinTitulo
        from utils.financeiro_core import registrar_baixa, BaixaInvalida

        valor = abs(float(lanc.get('valor') or 0))
        if valor <= 0:
            return False, 'Lançamento sem valor.', None

        if criar is not None:
            chave = 'extrato:%s' % lanc['id']
            ja = execute_query(
                'SELECT id FROM fin_titulos WHERE chave_idem = %s',
                (chave,), fetch=True, fetch_one=True)
            if ja:
                titulo_id = ja['id']       # reprocesso: o titulo ja existe
            else:
                titulo_id = FinTitulo.criar(
                    tipo='R' if float(lanc['valor']) >= 0 else 'P',
                    contraparte_nome=(criar.get('contraparte_nome')
                                      or 'sem contraparte')[:255],
                    categoria_id=criar['categoria_id'],
                    descricao=(criar.get('descricao')
                               or lanc.get('descricao') or '')[:255],
                    competencia=criar['competencia'],
                    emissao=lanc['data'], vencimento=lanc['data'],
                    valor=valor, empresa_id=lanc.get('empresa_id'),
                    contraparte_doc=(criar.get('contraparte_doc') or None),
                    centro_custo_id=criar.get('centro_custo_id'),
                    origem='extrato', chave_idem=chave)
                if not titulo_id or titulo_id is True:
                    return False, 'Não consegui criar o título.', None

        if not titulo_id:
            return False, 'Escolha o título.', None

        try:
            r = registrar_baixa(
                titulo_id=titulo_id, valor=valor, data_baixa=lanc['data'],
                origem='extrato', referencia=lanc.get('hash_dedup'),
                lancamento_id=lanc['id'], usuario_id=usuario_id)
        except BaixaInvalida as e:
            return False, str(e), titulo_id
        return True, ('nova' if r.get('criada') else 'ja-existia'), titulo_id

    @staticmethod
    def confirmar(lanc_id):
        """O humano olhou e disse "era isso mesmo": a marca sai, a
        classificacao fica, e o vinculo com a regra fica — confirmar nao e
        reclassificar a mao."""
        # Olha ANTES de agir: conferir o estado final diria "confirmei" para
        # um lancamento que ja estava confirmado — e a rota usa a resposta
        # para escolher a mensagem (pista do rowcount, 14/08: "ja estava
        # assim" e "nao existe" respondem igual depois do UPDATE).
        r = execute_query('SELECT conferir FROM extrato_lancamentos WHERE id = %s',
                          (lanc_id,), fetch=True, fetch_one=True)
        if not r or not r['conferir']:
            return False
        execute_query('UPDATE extrato_lancamentos SET conferir = 0 '
                      ' WHERE id = %s', (lanc_id,))
        return True

    @staticmethod
    def confirmar_da_regra(regra_id):
        """A regra acertou em todos: limpa a marca de tudo o que ela deixou
        esperando. Devolve quantos."""
        r = execute_query(
            'SELECT COUNT(*) n FROM extrato_lancamentos '
            ' WHERE memorizacao_id = %s AND conferir = 1',
            (regra_id,), fetch=True, fetch_one=True)
        n = int((r or {}).get('n') or 0)
        if n:
            execute_query('UPDATE extrato_lancamentos SET conferir = 0 '
                          ' WHERE memorizacao_id = %s AND conferir = 1', (regra_id,))
        return n

    @staticmethod
    def hashes_existentes(hashes):
        """Quais dessas chaves já estão gravadas (para contar repetido certo)."""
        achados = set()
        for i in range(0, len(hashes), 300):
            fatia = hashes[i:i + 300]
            marks = ','.join(['%s'] * len(fatia))
            rows = execute_query(
                f'SELECT hash_dedup FROM extrato_lancamentos '
                f'WHERE hash_dedup IN ({marks})', tuple(fatia), fetch=True) or []
            achados.update(r['hash_dedup'] for r in rows)
        return achados

    @staticmethod
    def inserir_lote(itens, banco, conta, arquivo, usuario_id,
                     empresa_id=None, origem='ofx'):
        """itens: [(hash, lancamento_do_parser)]. Insere em blocos (banco é
        remoto — um INSERT por linha seria um caracol). O UNIQUE uk_dedup é o
        guarda-costas contra corrida."""
        total = 0
        for i in range(0, len(itens), 200):
            fatia = itens[i:i + 200]
            valores, params = [], []
            for h, l in fatia:
                valores.append('(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)')
                params += [empresa_id, banco, conta, l['data'], l['valor'],
                           l['tipo'], l['descricao'], l['documento'],
                           l['fitid'], h, origem, arquivo]
            execute_query(
                'INSERT IGNORE INTO extrato_lancamentos '
                '(empresa_id, banco, conta, data, valor, tipo, descricao, '
                ' documento, fitid, hash_dedup, origem, arquivo) '
                'VALUES ' + ', '.join(valores), tuple(params))
            total += len(fatia)
        if usuario_id and itens:
            hashes = [h for h, _ in itens]
            for i in range(0, len(hashes), 300):
                fatia = hashes[i:i + 300]
                marks = ','.join(['%s'] * len(fatia))
                execute_query(
                    f'UPDATE extrato_lancamentos SET usuario_id = %s '
                    f'WHERE hash_dedup IN ({marks}) AND usuario_id IS NULL',
                    (usuario_id, *fatia))
        return total


class RegraExtrato:
    """Regra de classificacao do extrato — a antiga "memorizacao", crescida.

    Uma regra responde tres perguntas, e as tres vieram do uso real:

    QUANDO ela vale
        ``termos``: uma LISTA de trechos que TODOS precisam aparecer na
        descricao. E o que resolve o numero que muda no meio::

            TARIFA COM R LIQUIDACAO-COB000001  262005312  DISTRIBUIDORA...
                                               ^^^^^^^^^ muda todo mes

        Com os dois trechos e o numero de fora, a regra pega as 4 tarifas do
        ano em vez de 1. Mais ``conta``, ``sinal`` e ``valor_exato`` como
        condicoes opcionais. O sinal nao e luxo: sem "so saidas", a regra do
        fornecedor pega tambem os recebimentos DELE, e receita cairia dentro
        de despesa.

    PARA QUEM ela vale
        ``escopo``: 'empresa' (uma), 'lista' (as escolhidas, em
        fin_regra_empresas) ou 'grupo' (todas as do grupo, resolvido na hora
        — empresa que entrar no grupo amanha ja herda a regra).

    COMO ela roda
        ``aplicar``: 'direto' classifica sozinha; 'aprovar' preenche e marca
        o lancamento como A CONFERIR. E a diferenca entre o salario que nunca
        e outra coisa e o deposito que pode ser salario ou comissao.

    Quando duas regras casam, vence a MAIS ESPECIFICA, nesta ordem:
    escopo mais estreito (empresa > lista > grupo), depois mais condicoes,
    depois o termo mais longo. E o que faz o conserto numa empresa valer sem
    precisar apagar a regra do grupo.
    """

    ESCOPOS = ('empresa', 'lista', 'grupo')
    APLICACOES = ('direto', 'aprovar')
    #: quanto mais baixo, mais especifico — decide quem vence o empate
    _PESO_ESCOPO = {'empresa': 0, 'lista': 1, 'grupo': 2}

    # ------------------------------------------------------------------ ler
    @staticmethod
    def _hidrata(r):
        """Deixa a linha do banco pronta para uso: termos como lista."""
        if not r:
            return r
        t = r.get('termos')
        if isinstance(t, str):
            try:
                t = json.loads(t)
            except ValueError:
                t = None
        if not t:
            t = [r['padrao']] if r.get('padrao') else []
        r['termos'] = [str(x) for x in t if str(x).strip()]
        return r

    @staticmethod
    def listar(apenas_ativas=False):
        cond = 'WHERE m.ativo = 1' if apenas_ativas else ''
        rows = execute_query(
            f"""SELECT m.*, c.nome AS categoria_nome, c.grupo AS categoria_grupo,
                       c.tipo AS categoria_tipo, cc.nome AS centro_nome,
                       g.nome AS grupo_nome
                  FROM fin_extrato_memorizacoes m
                  JOIN fin_categorias c ON c.id = m.categoria_id
                  LEFT JOIN fin_centros_custo cc ON cc.id = m.centro_custo_id
                  LEFT JOIN grupos_clientes g ON g.id = m.grupo_id
                {cond}
                 ORDER BY m.ativo DESC, m.usos DESC, m.padrao""",
            fetch=True) or []
        return [RegraExtrato._hidrata(r) for r in rows]

    @staticmethod
    def get(regra_id):
        return RegraExtrato._hidrata(execute_query(
            'SELECT * FROM fin_extrato_memorizacoes WHERE id = %s',
            (regra_id,), fetch=True, fetch_one=True))

    @staticmethod
    def empresas_da(regra):
        """De quais empresas esta regra cuida. None = de todas.

        No escopo 'grupo' a lista e resolvida NA HORA, e nao guardada: e por
        isso que a empresa que entrar no grupo amanha ja nasce com a regra.
        """
        escopo = regra.get('escopo') or 'empresa'
        if escopo == 'grupo' and regra.get('grupo_id'):
            rows = execute_query(
                'SELECT cliente_id FROM cliente_grupo_relacao WHERE grupo_id = %s',
                (regra['grupo_id'],), fetch=True) or []
            return {r['cliente_id'] for r in rows}
        if escopo == 'lista':
            rows = execute_query(
                'SELECT empresa_id FROM fin_regra_empresas WHERE regra_id = %s',
                (regra['id'],), fetch=True) or []
            return {r['empresa_id'] for r in rows}
        if regra.get('empresa_id'):
            return {regra['empresa_id']}
        return None                      # vale para todas

    # --------------------------------------------------------------- casar
    @staticmethod
    def _norma(t):
        """MAIUSCULA e espaco unico. O extrato vem com espaco duplo
        ("PIX_DEB   00394460005887"); comparar sem normalizar fazia o mesmo
        texto nao casar consigo mesmo."""
        return re.sub(r'\s+', ' ', (t or '').upper()).strip()

    @staticmethod
    def _chave(termos, conta, sinal, valor_exato, escopo, grupo_id, empresa_id):
        """O que faz duas regras serem A MESMA. Termos ordenados e
        normalizados: trocar a ordem dos trechos nao cria regra nova."""
        return (
            tuple(sorted(RegraExtrato._norma(t) for t in (termos or []))),
            str(conta or ''),
            (sinal or '').upper()[:1],
            None if valor_exato is None else round(float(valor_exato), 2),
            escopo or 'empresa',
            grupo_id or None,
            empresa_id or None,
        )

    @staticmethod
    def condicoes(regra):
        """Quantas condicoes a regra impoe alem do texto — o desempate."""
        return sum(1 for c in ('conta', 'sinal', 'valor_exato') if regra.get(c))

    @staticmethod
    def casa(regra, lanc, empresas=None):
        """A regra vale para ESTE lancamento?

        ``empresas`` entra pronto para nao consultar o banco por lancamento
        quando se varre uma lista inteira.
        """
        if not regra.get('ativo', 1):
            return False

        alvo = empresas if empresas is not None else RegraExtrato.empresas_da(regra)
        if alvo is not None and lanc.get('empresa_id') not in alvo:
            return False

        desc = RegraExtrato._norma(lanc.get('descricao'))
        termos = regra.get('termos') or []
        if not termos:
            return False
        for t in termos:
            if RegraExtrato._norma(t) not in desc:
                return False

        if regra.get('conta') and str(lanc.get('conta') or '') != str(regra['conta']):
            return False

        valor = float(lanc.get('valor') or 0)
        if regra.get('sinal'):
            saiu = valor < 0
            if (regra['sinal'].upper() == 'D') != saiu:
                return False

        if regra.get('valor_exato') is not None:
            if abs(abs(valor) - abs(float(regra['valor_exato']))) > 0.005:
                return False
        return True

    @staticmethod
    def melhor(lanc, regras, empresas_por_regra=None):
        """A regra vencedora para este lancamento, ou None.

        Vence a MAIS ESPECIFICA: escopo mais estreito, depois mais condicoes,
        depois o termo mais longo. Sem essa ordem, a regra do grupo inteiro
        atropelaria o conserto feito numa empresa so.
        """
        candidatas = []
        for r in regras:
            emp = (empresas_por_regra or {}).get(r['id'])
            if emp is None and empresas_por_regra is not None:
                emp = RegraExtrato.empresas_da(r)
                empresas_por_regra[r['id']] = emp
            if RegraExtrato.casa(r, lanc, emp):
                candidatas.append(r)
        if not candidatas:
            return None
        return sorted(candidatas, key=lambda r: (
            RegraExtrato._PESO_ESCOPO.get(r.get('escopo') or 'empresa', 9),
            -RegraExtrato.condicoes(r),
            -max((len(t) for t in r['termos']), default=0),
        ))[0]

    # ----------------------------------------------------------- sugerir
    #: bloco so-numeros com 4+ digitos: e o que costuma mudar de um mes para
    #: o outro (nosso numero do boleto, sequencia da guia, id da operacao)
    _SO_NUMERO = re.compile(r'^[0-9][0-9./-]{3,}$')

    @staticmethod
    def sugestoes(lanc, escopo='empresa', grupo_id=None, empresas=None):
        """Trechos propostos para virar regra, do mais util para o menos.

        Devolve lista de dicts com rotulo, termos, porque, n, saidas,
        entradas. A contagem vem de ``preve``, o MESMO caminho que aplica.

        As quatro leituras da descricao, e o que cada uma serve:

        sem o que muda   tira os blocos so-numeros. E a resposta do caso da
                         tarifa, onde o numero do boleto muda todo mes;
        so quem esta     o rabo em letras — o nome de quem recebeu ou pagou,
        do outro lado    sem o tipo da operacao. Amplo e util, mas e a que
                         costuma misturar entrada com saida do mesmo cliente;
        toda a familia   a cabeca em letras — todo lancamento desse tipo,
                         seja de quem for;
        esta descricao   tudo, numero incluido. Pega 1, e serve para o
                         lancamento que nao se repete.
        """
        desc = RegraExtrato._norma(lanc.get('descricao'))
        toks = desc.split(' ') if desc else []
        numeros = [t for t in toks if RegraExtrato._SO_NUMERO.match(t)]
        props, vistos = [], set()
        # UMA leitura para as quatro propostas.
        todos = RegraExtrato.universo(so_sem_categoria=False)

        def poe(rotulo, termos, porque, posto):
            termos = [t.strip() for t in termos if t and t.strip()]
            if not termos:
                return
            chave = tuple(sorted(RegraExtrato._norma(t) for t in termos))
            if chave in vistos:
                return
            vistos.add(chave)
            falso = {'id': 0, 'termos': termos, 'ativo': 1,
                     'escopo': escopo, 'grupo_id': grupo_id,
                     'empresa_id': lanc.get('empresa_id'),
                     'conta': None, 'sinal': None, 'valor_exato': None}
            achados = RegraExtrato.preve(falso, so_sem_categoria=False,
                                         universo=todos)
            saidas = sum(1 for a in achados if float(a['valor'] or 0) < 0)
            props.append({
                'rotulo': rotulo, 'termos': termos, 'porque': porque,
                'posto': posto, 'n': len(achados),
                'saidas': saidas, 'entradas': len(achados) - saidas,
                'exemplos': [a['descricao'][:60] for a in achados[:4]],
            })

        # 1. sem os numeros que mudam
        if numeros:
            corridos, atual = [], []
            for t in toks:
                if RegraExtrato._SO_NUMERO.match(t):
                    if atual:
                        corridos.append(' '.join(atual))
                        atual = []
                else:
                    atual.append(t)
            if atual:
                corridos.append(' '.join(atual))
            poe('Sem o que muda', corridos,
                'ignora ' + ', '.join(numeros) + ' — é o que muda de um mês '
                'para o outro', 0)

        # 2. so o nome de quem esta do outro lado (o rabo em letras)
        cauda = []
        for t in reversed(toks):
            if RegraExtrato._SO_NUMERO.match(t):
                break
            cauda.insert(0, t)
        if cauda and len(cauda) < len(toks):
            poe('Só quem está do outro lado', [' '.join(cauda)],
                'o nome de quem recebeu ou pagou, sem o tipo da operação', 2)

        # 3. so o tipo da operacao (a cabeca em letras)
        cabeca = []
        for t in toks:
            if RegraExtrato._SO_NUMERO.match(t):
                break
            cabeca.append(t)
        if cabeca and len(cabeca) < len(toks):
            poe('Toda a família', [' '.join(cabeca)],
                'todo lançamento desse tipo, seja de quem for — bem amplo', 3)

        # 4. a descricao inteira
        poe('Exatamente esta descrição', [desc],
            'pega só o que for idêntico, número incluído', 4)

        # A recomendada primeiro. Ordenar por quantidade poria "exatamente
        # esta descricao" (1) no topo — justo a que nao serve para memorizar.
        props.sort(key=lambda p: (p['posto'], p['n']))
        return [p for p in props if p['n'] > 0]

    # -------------------------------------------------------------- gravar
    @staticmethod
    def criar(termos, categoria_id, centro_custo_id=None, empresa_id=None,
              conta=None, sinal=None, valor_exato=None,
              escopo='empresa', grupo_id=None, empresas=None,
              aplicar='direto', criado_por=None):
        """Cria a regra. Devolve o id, ou None quando nao da.

        ``termos`` pode vir como texto (um trecho so) ou lista.
        """
        if isinstance(termos, str):
            termos = [termos]
        termos = [t.strip() for t in (termos or []) if t and t.strip()]
        if not termos or not categoria_id:
            return None
        if escopo not in RegraExtrato.ESCOPOS:
            escopo = 'empresa'
        if aplicar not in RegraExtrato.APLICACOES:
            aplicar = 'direto'
        if escopo == 'grupo' and not grupo_id:
            return None
        if escopo == 'lista' and not empresas:
            return None

        # REPETIDA? Duas regras so sao a mesma coisa quando tem os mesmos
        # termos, as mesmas condicoes E o mesmo escopo. Mesmo texto com conta
        # diferente e OUTRA regra — e e o caso do salario no Bradesco contra
        # a comissao no Sicredi, que precisa das duas coexistindo.
        chave = RegraExtrato._chave(termos, conta, sinal, valor_exato,
                                    escopo, grupo_id, empresa_id)
        for r in RegraExtrato.listar(apenas_ativas=True):
            if RegraExtrato._chave(r['termos'], r.get('conta'), r.get('sinal'),
                                   r.get('valor_exato'), r.get('escopo'),
                                   r.get('grupo_id'), r.get('empresa_id')) == chave:
                return None

        # padrao continua sendo o PRIMEIRO termo: a tela antiga de
        # memorizacoes le essa coluna, e uma regra sem padrao apareceria em
        # branco la ate ela ser refeita.
        regra_id = execute_query(
            'INSERT INTO fin_extrato_memorizacoes '
            '(empresa_id, padrao, termos, conta, sinal, valor_exato, escopo, '
            ' grupo_id, aplicar, categoria_id, centro_custo_id, criado_por) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (empresa_id if escopo == 'empresa' else None,
             termos[0][:160], json.dumps(termos, ensure_ascii=False),
             conta or None, (sinal or '').upper()[:1] or None, valor_exato,
             escopo, grupo_id if escopo == 'grupo' else None, aplicar,
             categoria_id, centro_custo_id, criado_por))
        if not regra_id or regra_id is True:
            return None

        if escopo == 'lista':
            for eid in sorted(set(int(e) for e in empresas)):
                execute_query(
                    'INSERT IGNORE INTO fin_regra_empresas (regra_id, empresa_id) '
                    'VALUES (%s, %s)', (regra_id, eid))
        return regra_id

    @staticmethod
    def contar_uso(regra_id, n=1):
        """Soma no contador da regra e marca a hora.

        Existe separado porque a PRIMEIRA classificacao — a que deu origem a
        regra — acontece fora do aplicar_em, na propria rota. Sem isto a
        regra nascia dizendo "nunca usada" logo depois de ser usada.
        """
        if n:
            execute_query('UPDATE fin_extrato_memorizacoes SET usos = usos + %s, '
                          'ultimo_uso = NOW() WHERE id = %s', (n, regra_id))

    @staticmethod
    def set_ativa(regra_id, ativa):
        execute_query('UPDATE fin_extrato_memorizacoes SET ativo = %s WHERE id = %s',
                      (1 if ativa else 0, regra_id))

    @staticmethod
    def desfazer(regra_id):
        """Devolve para "sem categoria" SO o que esta regra classificou.

        O que foi classificado a mao tem memorizacao_id NULL e nao e tocado —
        e por isso que a coluna existe.
        """
        r = execute_query(
            'SELECT COUNT(*) n FROM extrato_lancamentos WHERE memorizacao_id = %s',
            (regra_id,), fetch=True, fetch_one=True)
        n = int((r or {}).get('n') or 0)
        if n:
            execute_query(
                'UPDATE extrato_lancamentos SET categoria_id = NULL, '
                '       centro_custo_id = NULL, memorizacao_id = NULL, conferir = 0 '
                ' WHERE memorizacao_id = %s', (regra_id,))
        return n

    # -------------------------------------------------------------- aplicar
    @staticmethod
    def _grava_no_lancamento(regra, lanc_id):
        execute_query(
            'UPDATE extrato_lancamentos SET categoria_id = %s, centro_custo_id = %s, '
            '       memorizacao_id = %s, conferir = %s WHERE id = %s',
            (regra['categoria_id'], regra['centro_custo_id'], regra['id'],
             1 if (regra.get('aplicar') or 'direto') == 'aprovar' else 0,
             lanc_id))

    @staticmethod
    def _contabiliza(usos):
        for rid, n in usos.items():
            execute_query('UPDATE fin_extrato_memorizacoes SET usos = usos + %s, '
                          'ultimo_uso = NOW() WHERE id = %s', (n, rid))

    @staticmethod
    def aplicar_em(lancamentos):
        """Passa as regras por estes lancamentos (dicts ja lidos).

        Devolve (classificados, a_conferir). So mexe em quem esta sem
        categoria: decisao humana nao e sobrescrita por regra.
        """
        regras = RegraExtrato.listar(apenas_ativas=True)
        if not regras:
            return 0, 0
        cache = {}
        usos, direto, conferir = {}, 0, 0
        for l in lancamentos:
            if l.get('categoria_id'):
                continue
            r = RegraExtrato.melhor(l, regras, cache)
            if not r:
                continue
            RegraExtrato._grava_no_lancamento(r, l['id'])
            usos[r['id']] = usos.get(r['id'], 0) + 1
            if (r.get('aplicar') or 'direto') == 'aprovar':
                conferir += 1
            else:
                direto += 1
        RegraExtrato._contabiliza(usos)
        return direto, conferir

    @staticmethod
    def aplicar_em_ids(ids):
        """Compatibilidade: roda logo depois do import do OFX."""
        if not ids:
            return 0
        marks = ','.join(['%s'] * len(ids))
        rows = execute_query(
            f'SELECT id, empresa_id, conta, valor, descricao, categoria_id '
            f'  FROM extrato_lancamentos WHERE id IN ({marks}) '
            f'   AND categoria_id IS NULL', tuple(ids), fetch=True) or []
        direto, conferir = RegraExtrato.aplicar_em(rows)
        return direto + conferir

    @staticmethod
    def universo(so_sem_categoria=False):
        """Os lancamentos candidatos, lidos UMA vez.

        Existe para quem vai testar VARIAS regras contra o mesmo conjunto —
        as quatro sugestoes, por exemplo. Sem isto o assistente fazia quatro
        varreduras completas por abertura, e como o banco e remoto cada uma
        custa a ida e a volta: media de 5,5s so para abrir.
        """
        cond = ' WHERE categoria_id IS NULL' if so_sem_categoria else ''
        return execute_query(
            'SELECT id, empresa_id, conta, valor, data, descricao, categoria_id, '
            '       memorizacao_id, conferir '
            '  FROM extrato_lancamentos' + cond +
            ' ORDER BY data DESC, id DESC', fetch=True) or []

    @staticmethod
    def preve(regra, so_sem_categoria=True, limite=None, universo=None):
        """Quais lancamentos ESTA regra pegaria — antes de gravar nada.

        E o numero que a tela mostra ("pega 4") e a lista que ela exibe. Sem
        isso a pessoa cria a regra no escuro.

        ``universo`` evita reler a tabela quando varias regras sao testadas
        contra o mesmo conjunto.
        """
        empresas = RegraExtrato.empresas_da(regra)
        if empresas is not None and not empresas:
            return []
        rows = (RegraExtrato.universo(so_sem_categoria) if universo is None
                else universo)
        achados = []
        for l in rows:
            if so_sem_categoria and l.get('categoria_id'):
                continue
            if empresas is not None and l.get('empresa_id') not in empresas:
                continue
            if RegraExtrato.casa(regra, l, empresas):
                achados.append(l)
        return achados[:limite] if limite else achados

    @staticmethod
    def editar(regra_id, termos=None, categoria_id=None, centro_custo_id='manter',
               conta='manter', sinal='manter', valor_exato='manter',
               escopo=None, grupo_id='manter', empresas=None,
               aplicar=None, retroagir=False):
        """Muda a regra. ``retroagir`` decide o que fazer com o passado.

        retroagir=False  a mudanca vale so daqui para frente; o que a regra
                         ja classificou fica como esta.
        retroagir=True   devolve o que ela tinha classificado e reclassifica
                         com o criterio novo — o que sair do alcance da regra
                         nova volta para "sem categoria", e e isso mesmo que
                         se quer quando o criterio estava errado.

        O default e False de proposito: mexer no passado tem de ser um SIM
        explicito, nunca o silencio.

        Devolve (ok, motivo, mexidos).
        """
        atual = RegraExtrato.get(regra_id)
        if not atual:
            return False, 'Regra não encontrada.', 0

        if isinstance(termos, str):
            termos = [termos]
        if termos is not None:
            termos = [t.strip() for t in termos if t and t.strip()]
            if not termos:
                return False, 'A regra precisa de pelo menos um trecho.', 0

        novo = {
            'termos': termos if termos is not None else atual['termos'],
            'categoria_id': categoria_id or atual['categoria_id'],
            'centro_custo_id': (atual['centro_custo_id'] if centro_custo_id == 'manter'
                                else centro_custo_id),
            'conta': atual['conta'] if conta == 'manter' else (conta or None),
            'sinal': atual['sinal'] if sinal == 'manter' else ((sinal or '').upper()[:1] or None),
            'valor_exato': (atual['valor_exato'] if valor_exato == 'manter'
                            else valor_exato),
            'escopo': escopo or atual['escopo'] or 'empresa',
            'grupo_id': atual['grupo_id'] if grupo_id == 'manter' else grupo_id,
            'aplicar': aplicar or atual['aplicar'] or 'direto',
        }
        if novo['escopo'] not in RegraExtrato.ESCOPOS:
            return False, 'Escopo inválido.', 0
        if novo['aplicar'] not in RegraExtrato.APLICACOES:
            return False, 'Modo de aplicação inválido.', 0
        if novo['escopo'] == 'grupo' and not novo['grupo_id']:
            return False, 'Escopo de grupo exige o grupo.', 0
        if novo['escopo'] == 'lista' and empresas is None:
            empresas = sorted(RegraExtrato.empresas_da(atual) or [])
        if novo['escopo'] == 'lista' and not empresas:
            return False, 'Escolha ao menos uma empresa.', 0

        # O passado sai ANTES da troca: depois dela o criterio novo nao
        # reconheceria mais o que o criterio velho pegou.
        mexidos = RegraExtrato.desfazer(regra_id) if retroagir else 0

        execute_query(
            'UPDATE fin_extrato_memorizacoes SET padrao = %s, termos = %s, '
            '       conta = %s, sinal = %s, valor_exato = %s, escopo = %s, '
            '       grupo_id = %s, aplicar = %s, categoria_id = %s, '
            '       centro_custo_id = %s, empresa_id = %s WHERE id = %s',
            (novo['termos'][0][:160],
             json.dumps(novo['termos'], ensure_ascii=False),
             novo['conta'], novo['sinal'], novo['valor_exato'], novo['escopo'],
             novo['grupo_id'] if novo['escopo'] == 'grupo' else None,
             novo['aplicar'], novo['categoria_id'], novo['centro_custo_id'],
             atual['empresa_id'] if novo['escopo'] == 'empresa' else None,
             regra_id))

        execute_query('DELETE FROM fin_regra_empresas WHERE regra_id = %s', (regra_id,))
        if novo['escopo'] == 'lista':
            for eid in sorted(set(int(e) for e in empresas)):
                execute_query(
                    'INSERT IGNORE INTO fin_regra_empresas (regra_id, empresa_id) '
                    'VALUES (%s, %s)', (regra_id, eid))

        if retroagir:
            mexidos = RegraExtrato.aplicar_retroativa(regra_id)
        return True, '', mexidos

    @staticmethod
    def desativar(regra_id, devolver=False):
        """Tira a regra de circulação. ``devolver`` decide o passado.

        devolver=False  ela para de valer daqui para frente; o que ja
                        classificou continua classificado.
        devolver=True   o que ELA classificou volta para "sem categoria" —
                        o que foi feito a mao nunca e tocado, porque so o
                        que veio de regra tem memorizacao_id.

        Devolve quantos lancamentos voltaram.
        """
        RegraExtrato.set_ativa(regra_id, False)
        return RegraExtrato.desfazer(regra_id) if devolver else 0

    @staticmethod
    def aplicar_retroativa(regra_id):
        """Volta no tempo: pega os antigos ainda sem categoria."""
        r = RegraExtrato.get(regra_id)
        if not r or not r['ativo']:
            return 0
        alvos = RegraExtrato.preve(r, so_sem_categoria=True)
        for l in alvos:
            RegraExtrato._grava_no_lancamento(r, l['id'])
        if alvos:
            RegraExtrato._contabiliza({regra_id: len(alvos)})
        return len(alvos)


#: A tela e as rotas ainda chamam pelo nome antigo. O apelido evita um
#: rename espalhado num commit que ja e grande — e o nome novo e o que
#: vale daqui para a frente.
ExtratoMemorizacao = RegraExtrato


class FinContaBancaria:
    """Contas cadastradas — a impressão digital de cada empresa."""

    @staticmethod
    def listar(empresa_ids=None, apenas_ativas=True):
        cond, params = [], []
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond.append(f'c.empresa_id IN ({marks})')
            params += list(empresa_ids)
        if apenas_ativas:
            cond.append('c.ativo = 1')
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        return execute_query(
            f"""SELECT c.*, cl.numero_cliente, cl.nome_razao_social
                  FROM fin_contas c
                  JOIN clientes cl ON cl.id = c.empresa_id
                {where}
                 ORDER BY cl.numero_cliente + 0, c.banco_nome, c.conta""",
            tuple(params), fetch=True) or []

    @staticmethod
    def get(conta_id):
        return execute_query('SELECT * FROM fin_contas WHERE id = %s',
                             (conta_id,), fetch=True, fetch_one=True)

    @staticmethod
    def set_ativa(conta_id, ativa):
        execute_query('UPDATE fin_contas SET ativo = %s WHERE id = %s',
                      (1 if ativa else 0, conta_id))

    @staticmethod
    def em_uso(conta_id):
        """Quantos lançamentos já entraram por esta conta."""
        c = FinContaBancaria.get(conta_id)
        if not c:
            return 0
        r = execute_query(
            'SELECT COUNT(*) AS n FROM extrato_lancamentos '
            'WHERE empresa_id = %s AND conta LIKE %s',
            (c['empresa_id'], f"%{c['conta']}%"), fetch=True, fetch_one=True)
        return int((r or {}).get('n') or 0)


class FinExtratoPendencia:
    """Arquivo que chegou e não se identificou — espera alguém dizer de quem é.

    Com número da empresa no nome, a pendência nasce AMARRADA a ela (aparece
    quando alguém abrir aquela empresa); sem número, nasce órfã.
    """

    @staticmethod
    def listar(empresa_ids=None, status='aberta', ver_orfas=False):
        """``ver_orfas`` só para ADMIN.

        Arquivo que chegou SEM número da empresa no nome e com conta
        desconhecida é sinal de funcionário que não seguiu o combinado — quem
        vê é quem cobra (decisão do Anderson em 21/08/2026). Para o resto da
        equipe a fila mostra só o que está amarrado a uma empresa: o que eles
        podem, de fato, resolver.
        """
        cond, params = [], []
        if status:
            cond.append('p.status = %s')
            params.append(status)
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            if ver_orfas:
                cond.append(f'(p.empresa_id IN ({marks}) OR p.empresa_id IS NULL)')
            else:
                cond.append(f'p.empresa_id IN ({marks})')
            params += list(empresa_ids)
        elif not ver_orfas:
            cond.append('p.empresa_id IS NOT NULL')
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        return execute_query(
            f"""SELECT p.*, cl.numero_cliente, cl.nome_razao_social
                  FROM fin_extrato_pendencias p
                  LEFT JOIN clientes cl ON cl.id = p.empresa_id
                {where}
                 ORDER BY p.empresa_id IS NULL, p.visto_em DESC""",
            tuple(params), fetch=True) or []

    @staticmethod
    def quantas(empresa_ids=None, ver_orfas=False):
        return len(FinExtratoPendencia.listar(empresa_ids, ver_orfas=ver_orfas))

    @staticmethod
    def get(pid):
        return execute_query('SELECT * FROM fin_extrato_pendencias WHERE id = %s',
                             (pid,), fetch=True, fetch_one=True)

    @staticmethod
    def anotar(caminho, arquivo, motivo, empresa_id=None, numero_no_nome=None,
               banco_id=None, banco_nome=None, agencia=None, conta=None,
               qtd=0, periodo=None):
        """Cria ou atualiza (o mesmo arquivo pode ser visto em várias rodadas)."""
        execute_query(
            """INSERT INTO fin_extrato_pendencias
               (arquivo, caminho, empresa_id, numero_no_nome, banco_id,
                banco_nome, agencia, conta, qtd_lancamentos, periodo, motivo)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                 motivo = VALUES(motivo), visto_em = NOW(), status = 'aberta',
                 empresa_id = VALUES(empresa_id), conta = VALUES(conta),
                 banco_id = VALUES(banco_id), banco_nome = VALUES(banco_nome),
                 qtd_lancamentos = VALUES(qtd_lancamentos),
                 periodo = VALUES(periodo)""",
            (arquivo, caminho, empresa_id, numero_no_nome, banco_id, banco_nome,
             agencia, conta, qtd, periodo, motivo))

    @staticmethod
    def get_por_caminho(caminho):
        return execute_query(
            'SELECT * FROM fin_extrato_pendencias WHERE caminho = %s',
            (caminho,), fetch=True, fetch_one=True)

    @staticmethod
    def resolver(pid):
        execute_query("UPDATE fin_extrato_pendencias SET status = 'resolvida' "
                      "WHERE id = %s", (pid,))

    @staticmethod
    def limpar_resolvidas(caminhos):
        """O arquivo saiu da _ENTRADA (foi lançado): a pendência morre."""
        if not caminhos:
            return
        marks = ','.join(['%s'] * len(caminhos))
        execute_query(
            f"UPDATE fin_extrato_pendencias SET status = 'resolvida' "
            f"WHERE caminho IN ({marks}) AND status = 'aberta'",
            tuple(caminhos))
