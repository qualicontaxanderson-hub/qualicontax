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
        if classif == 'sim':
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
                       e.memorizacao_id, c.nome AS categoria_nome,
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
    def preve(regra, so_sem_categoria=True, limite=None):
        """Quais lancamentos ESTA regra pegaria — antes de gravar nada.

        E o numero que a tela mostra ("pega 4") e a lista que ela exibe. Sem
        isso a pessoa cria a regra no escuro.
        """
        empresas = RegraExtrato.empresas_da(regra)
        cond = ['1=1']
        params = []
        if so_sem_categoria:
            cond.append('categoria_id IS NULL')
        if empresas is not None:
            if not empresas:
                return []
            cond.append('empresa_id IN (%s)' % ','.join(['%s'] * len(empresas)))
            params += sorted(empresas)
        rows = execute_query(
            'SELECT id, empresa_id, conta, valor, data, descricao, categoria_id '
            '  FROM extrato_lancamentos WHERE ' + ' AND '.join(cond) +
            ' ORDER BY data DESC, id DESC', tuple(params), fetch=True) or []
        achados = [l for l in rows if RegraExtrato.casa(regra, l, empresas)]
        return achados[:limite] if limite else achados

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
