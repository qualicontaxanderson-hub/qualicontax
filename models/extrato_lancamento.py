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
               conta_pares=None,
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
        if conta_pares:
            # Uma conta escolhida pode ter várias grafias no extrato (o OFX
            # escreveu de um jeito, o PDF de outro): todas entram no OU.
            pedacos = []
            for empresa_id, contas in conta_pares:
                marks = ','.join(['%s'] * len(contas))
                pedacos.append(f'(e.empresa_id = %s AND e.conta IN ({marks}))')
                params.append(empresa_id)
                params += list(contas)
            cond.append('(' + ' OR '.join(pedacos) + ')')
        elif conta:
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
                       e.memorizacao_id, e.conferir, e.par_estado, e.par_id,
                       c.nome AS categoria_nome,
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
    def por_conta_painel(**f):
        """Uma linha por CONTA, com o resumo e a série diária do minigráfico.

        É a faixa do topo do extrato — a que o Anderson pediu em 17/09/2026
        apontando para o relatório do posto: número grande, entrou/saiu, o que
        falta classificar e um gráfico do movimento. Ela responde "onde está o
        meu serviço" antes de a pessoa rolar a tela.

        A SÉRIE É LANÇAMENTOS POR DIA, não valor. Medido antes de desenhar: a
        série de valores do C6 tinha dois picos (19 mil e 12 mil) e o resto
        rasteiro — um gráfico de duas barras e um fio. Contagem distribui
        melhor E é o que interessa aqui, que é quanto trabalho cada dia traz.

        Conta com menos de ``MIN_SERIE`` dias de movimento volta sem série: com
        três barras soltas o gráfico finge ser uma série e não é.

        Usa o MESMO ``_where`` de ``totais`` — duas contagens da mesma tela que
        filtrassem diferente seria a tela se contradizendo.
        """
        where, params = ExtratoLancamento._where(**f)
        linhas = execute_query(
            f"""SELECT e.empresa_id, e.conta, MIN(e.banco) AS banco, COUNT(*) AS n,
                       COALESCE(SUM(CASE WHEN e.valor >= 0 THEN e.valor END), 0) AS creditos,
                       COALESCE(SUM(CASE WHEN e.valor < 0 THEN -e.valor END), 0) AS debitos
                  FROM extrato_lancamentos e WHERE {where}
                 GROUP BY e.empresa_id, e.conta ORDER BY n DESC""",
            tuple(params), fetch=True)
        if linhas is None:            # cortada pelo teto: NÃO é "nenhuma conta"
            return None

        f_sem = dict(f)
        f_sem['classif'] = 'nao'
        w2, p2 = ExtratoLancamento._where(**f_sem)
        sem = execute_query(
            f'SELECT e.empresa_id, e.conta, COUNT(*) AS n FROM extrato_lancamentos e '
            f' WHERE {w2} GROUP BY e.empresa_id, e.conta', tuple(p2), fetch=True) or []
        mapa_sem = {(r['empresa_id'], r['conta']): int(r['n'] or 0) for r in sem}

        dias = execute_query(
            f"""SELECT e.empresa_id, e.conta, e.data, COUNT(*) AS n
                  FROM extrato_lancamentos e WHERE {where}
                 GROUP BY e.empresa_id, e.conta, e.data ORDER BY e.data""",
            tuple(params), fetch=True) or []
        series = {}
        for r in dias:
            series.setdefault((r['empresa_id'], r['conta']), []).append(int(r['n'] or 0))

        MIN_SERIE = 6
        for l in linhas:
            chave = (l['empresa_id'], l['conta'])
            l['sem_cat'] = mapa_sem.get(chave, 0)
            serie = series.get(chave) or []
            if len(serie) < MIN_SERIE:
                l['serie'] = None     # poucos dias: sem gráfico, e a tela diz isso
            else:
                serie = serie[-30:]
                topo = max(serie) or 1
                # altura em % da caixa; o piso de 8 existe para o dia de UM
                # lançamento continuar visível ao lado de um pico de 170
                l['serie'] = [max(8, round(100.0 * v / topo)) for v in serie]
        return linhas

    @staticmethod
    def por_empresa(**f):
        """Uma linha por empresa do filtro: contagem, sem categoria e contas.

        Alimenta a faixa do topo do extrato, que responde "onde está o meu
        serviço" antes de a pessoa rolar a tela. Em 17/09/2026 ela mostrava o
        que a lista sozinha escondia: a PF tinha 93 lançamentos e 51 sem
        categoria, enquanto o escritório tinha 645 e 38 — o trabalho estava no
        lado pequeno.

        Usa o MESMO ``_where`` de ``totais``: duas contagens da mesma tela que
        filtrassem diferente seria a tela se contradizendo. E o sem-categoria
        ignora o filtro de classificação pela mesma razão que lá — estando na
        aba "Sem categoria", a faixa repetiria o total da lista.
        """
        where, params = ExtratoLancamento._where(**f)
        linhas = execute_query(
            f"""SELECT e.empresa_id, COUNT(*) AS n,
                       COUNT(DISTINCT e.conta) AS contas,
                       COALESCE(SUM(CASE WHEN e.valor >= 0 THEN e.valor END), 0) AS creditos,
                       COALESCE(SUM(CASE WHEN e.valor < 0 THEN e.valor END), 0)  AS debitos
                  FROM extrato_lancamentos e WHERE {where}
                 GROUP BY e.empresa_id ORDER BY n DESC""",
            tuple(params), fetch=True)
        if linhas is None:            # cortada pelo teto: NÃO é "nenhuma empresa"
            return None
        f_sem = dict(f)
        f_sem['classif'] = 'nao'
        w2, p2 = ExtratoLancamento._where(**f_sem)
        sem = execute_query(
            f'SELECT e.empresa_id, COUNT(*) AS n FROM extrato_lancamentos e '
            f' WHERE {w2} GROUP BY e.empresa_id', tuple(p2), fetch=True)
        mapa = {r['empresa_id']: int(r['n'] or 0) for r in (sem or [])}
        for l in linhas:
            l['sem_cat'] = mapa.get(l['empresa_id'], 0)
        return linhas

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

    @staticmethod
    def contas_filtro(empresa_ids=None):
        """As contas do filtro, do jeito que a pessoa reconhece.

        O extrato guarda o nome que o ARQUIVO escreveu — '0237', 'CCPI DO
        CERRADO DE GO', 'NU PAGAMENTOS S.A.' — e a mesma conta aparece com
        grafias diferentes conforme o formato que chegou. Quem sabe o nome
        bom é o CADASTRO: cada conta cadastrada vira UMA opção, levando
        junto todas as grafias que existem no extrato (``variantes``), e o
        filtro procura por todas elas.

        Sobrou lançamento de conta que ninguém cadastrou? Vira opção também,
        com o rótulo cru — melhor aparecer torto do que sumir da tela.
        """
        from utils.extrato_ingest import mesma_conta, banco_curto
        cond, params = '1=1', ()
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond, params = f'e.empresa_id IN ({marks})', tuple(empresa_ids)
        grupos = execute_query(
            f"""SELECT e.empresa_id, e.banco, e.conta, COUNT(*) AS n,
                       MIN(e.data) AS de, MAX(e.data) AS ate,
                       cl.numero_cliente, cl.nome_razao_social
                  FROM extrato_lancamentos e
                  JOIN clientes cl ON cl.id = e.empresa_id
                 WHERE {cond}
                 GROUP BY e.empresa_id, e.banco, e.conta, cl.numero_cliente,
                          cl.nome_razao_social""",
            params, fetch=True) or []
        cadastro = execute_query(
            'SELECT id, empresa_id, banco_id, banco_nome, agencia, conta, apelido '
            '  FROM fin_contas WHERE ativo = 1', fetch=True) or []

        opcoes, por_id = [], {}
        for g in grupos:
            reg = next((c for c in cadastro
                        if c['empresa_id'] == g['empresa_id']
                        and mesma_conta(c['conta'], g['conta'])), None)
            if reg:
                chave = f"c{reg['id']}"
                banco = reg['apelido'] or banco_curto(reg['banco_id'], reg['banco_nome'] or g['banco'])
                numero = str(reg['conta']).split('/')[-1]
                # 'ag 1' é ruído: no C6 e no Cora esse 1 é TIPO de conta, não
                # agência. Agência de verdade tem quatro dígitos.
                agencia = (reg['agencia'] or '').strip()
                agencia = agencia if len(re.sub(r'\D', '', agencia)) >= 3 else ''
            else:
                chave = f"x{g['empresa_id']}:{g['banco']} · {g['conta']}"
                banco = banco_curto(None, g['banco']) or (g['banco'] or 'Banco')
                numero = str(g['conta'] or '').split('/')[-1]
                agencia = ''
            o = por_id.get(chave)
            if not o:
                o = {'valor': chave, 'banco': banco, 'conta': numero,
                     'agencia': agencia, 'empresa_id': g['empresa_id'],
                     'numero_cliente': g['numero_cliente'],
                     'empresa': g['nome_razao_social'],
                     'cadastrada': bool(reg), 'n': 0, 'de': None, 'ate': None,
                     'variantes': []}
                por_id[chave] = o
                opcoes.append(o)
            o['n'] += int(g['n'] or 0)
            o['variantes'].append(g['conta'])
            o['de'] = min(x for x in (o['de'], g['de']) if x)
            o['ate'] = max(x for x in (o['ate'], g['ate']) if x)
        opcoes.sort(key=lambda o: (str(o['numero_cliente'] or '').zfill(6),
                                   o['banco'].lower(), o['conta']))
        return opcoes

    @staticmethod
    def contas_do_filtro(valores, empresa_ids=None):
        """Os pares (empresa_id, conta) que os valores escolhidos representam."""
        if not valores:
            return []
        escolhidos = set(valores)
        pares = []
        for o in ExtratoLancamento.contas_filtro(empresa_ids=empresa_ids):
            if o['valor'] in escolhidos:
                pares.append((o['empresa_id'], o['variantes']))
        return pares

    #: CPF (11) ou CNPJ (14) soltos no meio da descricao.
    _DOC = re.compile(r'\b(\d{11}|\d{14})\b')

    #: O mesmo documento PONTUADO, que e como o Cora e o Nubank escrevem.
    _DOC_PONTUADO = re.compile(
        r'\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}')

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
            return {'doc': m.group(1), 'nome': d[m.end():].strip(),
                    'antes': d[:m.end()].strip(), 'depois': ''}

        # O OUTRO desenho, e ele e a maioria fora do Sicredi: o nome vem ANTES
        # do documento, entre travessoes — 'Transf Pix enviada - Anderson
        # Antunes Vieira - 291.511.418-84' (Cora), 'Transferencia enviada pelo
        # Pix - NH GTBA - 33.503.987/0001-16 - COOP SICREDI ...' (Nubank).
        # Sem isto o leitor caia no bloco de numeros la no fim e mostrava '-6'
        # (o digito da conta) como se fosse o nome de quem recebeu.
        m = ExtratoLancamento._DOC_PONTUADO.search(d)
        if m:
            cabeca = d[:m.start()].rstrip(' -')
            corte = cabeca.rfind(' - ')
            if corte > 0:
                return {'doc': re.sub(r'\D', '', m.group(0)),
                        'nome': cabeca[corte + 3:].strip(),
                        'antes': cabeca[:corte + 3],
                        'depois': ' ' + d[m.start():].strip()}

        # Sem CPF/CNPJ ainda pode haver nome: na tarifa de cobranca o codigo
        # do banco tem 9 digitos ("...COB000001 262005312 DISTRIBUIDORA DE
        # COMBUSTIVEIS SAARA"). Cai no ULTIMO bloco so-numeros; o que vem
        # depois dele e o nome.
        ult = None
        for n in re.finditer(r'\b\d{4,}\b', d):
            ult = n
        rabo = d[ult.end():].strip() if ult else ''
        # '-6', '-22', '-0': digito verificador da conta, nao nome de gente.
        if rabo and len(re.sub(r'[^A-Za-zÀ-ÿ]', '', rabo)) >= 3:
            return {'doc': '', 'nome': rabo, 'antes': d[:ult.end()].strip(), 'depois': ''}
        return {'doc': '', 'nome': '', 'antes': d, 'depois': ''}

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
            'SELECT conta_norm, conta, apelido, banco_nome, banco_id, agencia '
            '  FROM fin_contas', fetch=True) or []
        mapa = {}
        for r in rows:
            # banco_id entra aqui porque e a chave da COR da marca
            # (utils/extrato_formatos.cores). Sem ele a tela sabia o nome do
            # banco e nao sabia a cor — todo ponto saia cinza.
            dado = {'apelido': r['apelido'] or r['banco_nome'] or '',
                    'banco_nome': r['banco_nome'] or '',
                    'banco_id': str(r['banco_id'] or ''),
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
            # a referencia nunca pode faltar: sem ela a baixa e recusada e o
            # titulo recem-criado fica pela metade
            r = registrar_baixa(
                titulo_id=titulo_id, valor=valor, data_baixa=lanc['data'],
                origem='extrato',
                referencia=lanc.get('hash_dedup') or ('extrato:%s' % lanc['id']),
                lancamento_id=lanc['id'], usuario_id=usuario_id)
        except BaixaInvalida as e:
            return False, str(e), titulo_id
        return True, ('nova' if r.get('criada') else 'ja-existia'), titulo_id

    @staticmethod
    def conciliar_automatico(lanc, usuario_id=None):
        """Casa-ou-cria sem perguntar — o mesmo criterio do gesto em lote.

        Casa quando ha titulo aberto do MESMO documento com saldo IGUAL
        (e o que impede duplicar a conta quando uma programacao ja gerou o
        titulo do mes); senao cria ja quitado com a competencia do mes do
        lancamento. Transferencia nao concilia. Ja amarrado, nao repete.

        Devolve 'casou', 'criou', 'pulado' ou 'erro'.
        """
        cat = execute_query('SELECT tipo, nome FROM fin_categorias WHERE id = %s',
                            (lanc.get('categoria_id'),), fetch=True, fetch_one=True)
        if not cat or cat['tipo'] == 'T':
            return 'pulado'
        ja = execute_query(
            'SELECT 1 FROM fin_titulo_baixas WHERE lancamento_id = %s '
            'UNION SELECT 1 FROM fin_titulos WHERE chave_idem = %s LIMIT 1',
            (lanc['id'], 'extrato:%s' % lanc['id']), fetch=True, fetch_one=True)
        if ja:
            return 'pulado'

        leitura = ExtratoLancamento.ler_descricao(lanc.get('descricao'))
        for cand in ExtratoLancamento.titulos_candidatos(lanc, limite=3):
            saldo = float(cand['valor']) - float(cand['valor_baixado'] or 0)
            if (leitura['doc'] and cand['contraparte_doc'] == leitura['doc']
                    and abs(saldo - abs(float(lanc['valor']))) <= 0.01):
                ok, _, _ = ExtratoLancamento.conciliar(
                    lanc, titulo_id=cand['id'], usuario_id=usuario_id)
                return 'casou' if ok else 'erro'

        ok, _, _ = ExtratoLancamento.conciliar(
            lanc, criar={'competencia': lanc['data'].replace(day=1),
                         'contraparte_nome': leitura['nome'] or cat['nome'],
                         'contraparte_doc': leitura['doc'],
                         'categoria_id': lanc['categoria_id'],
                         'centro_custo_id': lanc.get('centro_custo_id')},
            usuario_id=usuario_id)
        return 'criou' if ok else 'erro'

    @staticmethod
    def desconciliar(lanc_id):
        """Desfaz o que conciliar_automatico fez por ESTE lancamento.

        Remove a baixa apontando o lancamento e, se o titulo nasceu DELE
        (chave extrato:<id>) e ficou sem nenhuma baixa, o titulo sai junto —
        senao o DRE guardaria um custo cuja origem foi desfeita. Titulo que
        ja existia (programacao, manual) fica: so a baixa sai e o status e
        recalculado.
        """
        from utils.financeiro_core import recalcular_status
        # o meio-nascido: titulo criado a partir DESTE lancamento que ficou
        # sem nenhuma baixa (a quitacao falhou no meio) tambem tem de sair.
        execute_query(
            'DELETE FROM fin_titulos WHERE chave_idem = %s '
            '  AND NOT EXISTS (SELECT 1 FROM fin_titulo_baixas b '
            '                   WHERE b.titulo_id = fin_titulos.id)',
            ('extrato:%s' % lanc_id,))
        baixas = execute_query(
            'SELECT id, titulo_id FROM fin_titulo_baixas WHERE lancamento_id = %s',
            (lanc_id,), fetch=True) or []
        for b in baixas:
            execute_query('DELETE FROM fin_titulo_baixas WHERE id = %s', (b['id'],))
            t = execute_query(
                'SELECT id, chave_idem FROM fin_titulos WHERE id = %s',
                (b['titulo_id'],), fetch=True, fetch_one=True)
            if not t:
                continue
            resto = execute_query(
                'SELECT COUNT(*) n FROM fin_titulo_baixas WHERE titulo_id = %s',
                (t['id'],), fetch=True, fetch_one=True)['n']
            if t['chave_idem'] == 'extrato:%s' % lanc_id and not resto:
                execute_query('DELETE FROM fin_titulos WHERE id = %s', (t['id'],))
            else:
                recalcular_status(t['id'])
        return len(baixas)

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

    #: Palavras que o BANCO escreve (o tipo da operação) e palavras que toda
    #: empresa tem no nome. Nenhuma delas identifica a contraparte, e foi
    #: exatamente por elas que a primeira tentativa de "regra por nome" saiu
    #: empatada na medição de 15/09/2026: +111 adotados, -110 roubados.
    _NAO_IDENTIFICA = {
        # o jeito de falar de cada banco
        'PAGAMENTO', 'PAGTO', 'PIX', 'ENVIADO', 'ENVIADA', 'RECEBIDO', 'RECEBIDA',
        'DES', 'REM', 'TRANSF', 'TRANSFERENCIA', 'CONTAS', 'CONTA', 'TED', 'DOC',
        'ELET', 'DISP', 'LIQUIDACAO', 'BOLETO', 'TARIFA', 'BANCARIA', 'DEBITO',
        'CREDITO', 'COBRANCA', 'PARA', 'DEB', 'CRED', 'SALDO', 'RENDIMENTO',
        # nome de banco
        'SICREDI', 'BRADESCO', 'CORA', 'NUBANK', 'SANTANDER', 'ITAU', 'CAIXA', 'BANCO',
        # o que toda empresa tem no nome
        'LTDA', 'EIRELI', 'EPP', 'MEI', 'SERVICOS', 'SERVICO', 'SERV', 'PRESTACAO',
        'PREST', 'COMERCIO', 'COMERCIAL', 'INDUSTRIA', 'DISTRIBUIDORA', 'EMPRESA',
        'SOCIEDADE', 'GESTAO', 'ADMINISTRACAO', 'ADMIN', 'TECNOLOGIA', 'ASSESSORIA',
    }

    #: CPF/CNPJ na descricao, com ou sem pontuacao. O MESMO documento chega
    #: escrito de dois jeitos conforme o banco: o Sicredi manda
    #: '75704250149' e o Cora manda '757.042.501-49'.
    _DOC_NA_DESC = re.compile(
        r'(?<!\d)(\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{11}|\d{14})(?!\d)')

    @staticmethod
    def documentos_em(texto):
        """Os CPFs e CNPJs de um texto, so digitos."""
        return {re.sub(r'\D', '', d) for d in RegraExtrato._DOC_NA_DESC.findall(texto or '')}

    @staticmethod
    def e_documento(termo):
        """O termo é um CPF/CNPJ (e não um pedaço de texto qualquer)?"""
        t = (termo or '').strip()
        so = re.sub(r'\D', '', t)
        # Só dígitos e a pontuação de documento: '757.042.501-49' vale,
        # 'PIX 75704250149 FULANO' não — ali o documento é parte de um texto.
        return len(so) in (11, 14) and not re.search(r'[^\d./\- ]', t)

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
        docs = None
        for t in termos:
            tn = RegraExtrato._norma(t)
            if RegraExtrato.e_documento(tn):
                # Documento casa por DÍGITO: a pontuação é escolha do banco,
                # não parte da identidade de quem está do outro lado.
                if docs is None:
                    docs = RegraExtrato.documentos_em(desc)
                if re.sub(r'\D', '', tn) not in docs:
                    return False
            elif tn not in desc:
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

    #: Numero COLADO em letra. O _SO_NUMERO acima so ve o token que COMECA com
    #: digito, entao em "LIQUIDACAO BOLETO SICREDI-264388416" ele nao enxergava
    #: nada e o numero do boleto — que muda todo mes — entrava na regra. A regra
    #: nascia casando com um lancamento so, o Questor voltava todo mes para
    #: classificar e uma regra nova nascia junto (02/09/2026, achado pelo
    #: Anderson: duas regras do Questor, uma por boleto).
    _NUM_COLADO = re.compile(r'\d{4,}')

    @staticmethod
    def _sem_numero(tok):
        """'SICREDI-264388416' -> 'SICREDI';  '79011862000170' -> ''.

        Devolver vazio quer dizer "aqui so tinha numero" — e esse ponto vira
        divisor de trecho, igual ao que o _SO_NUMERO ja fazia.
        """
        if not RegraExtrato._NUM_COLADO.search(tok or ''):
            return tok or ''          # sem numero, devolve intacto: mexer no
                                      # hifen de "LIQUIDACAO BOLETO-" mudaria
                                      # o trecho das regras que ja funcionam
        limpo = RegraExtrato._NUM_COLADO.sub('', tok)
        return limpo.strip(' .-/')

    @staticmethod
    def _frequencia(universo):
        """Em quantas descrições cada palavra aparece.

        Palavra comum não identifica: 'SILVA' está em meio mundo, 'ROCHA'
        não. É essa conta que decide quais pedaços do nome viram regra.
        """
        import collections
        freq = collections.Counter()
        for l in universo:
            vistos = set()
            for tok in re.split(r'[^A-Z0-9ÁÂÃÀÉÊÍÓÔÕÚÇ]+',
                                RegraExtrato._norma(l.get('descricao'))):
                if len(tok) >= 3 and tok not in vistos:
                    vistos.add(tok)
                    freq[tok] += 1
        return freq

    @staticmethod
    def pedacos_que_identificam(texto, freq, teto, quantos=2):
        """Os pedaços MAIS RAROS do texto — os que sobrevivem à abreviação.

        O banco corta o nome onde quer ('Guilherme Rocha de So'), então a
        regra não pode depender do nome inteiro. Pega as palavras menos
        comuns da base: são as que apontam para uma pessoa só.
        """
        cand = []
        for tok in re.split(r'[^A-Z0-9ÁÂÃÀÉÊÍÓÔÕÚÇ]+', RegraExtrato._norma(texto)):
            if len(tok) >= 3 and tok not in RegraExtrato._NAO_IDENTIFICA                     and not tok.isdigit() and tok not in cand:
                cand.append(tok)
        cand.sort(key=lambda t: freq.get(t, 0))
        escolha = cand[:quantos]
        if len(escolha) < 2 or freq.get(escolha[0], 0) > teto:
            return []                 # sem nada raro o bastante: não arrisca
        return escolha

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
        # Cada token sem o numero que muda. Vazio = so tinha numero ali.
        limpos = [RegraExtrato._sem_numero(t) for t in toks]
        numeros = []
        for t in toks:
            numeros.extend(RegraExtrato._NUM_COLADO.findall(t))
        props, vistos = [], set()
        # UMA leitura para todas as propostas.
        todos = RegraExtrato.universo(so_sem_categoria=False)
        freq = RegraExtrato._frequencia(todos)

        def poe(rotulo, termos, porque, posto, tipo='texto'):
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
            entradas = len(achados) - saidas
            # Pega entrada E saida da mesma contraparte? Entao o sinal nao e
            # detalhe: sem ele, o Pix que a pessoa MANDOU para voce entraria
            # como despesa (o caso do Guilherme, 19/06, +60,00).
            este_saiu = float(lanc.get('valor') or 0) < 0
            props.append({
                'rotulo': rotulo, 'termos': termos, 'porque': porque,
                'posto': posto, 'tipo': tipo, 'n': len(achados),
                'saidas': saidas, 'entradas': entradas,
                'sinal_sugerido': ('D' if este_saiu else 'C') if (saidas and entradas) else None,
                'exemplos': [a['descricao'][:60] for a in achados[:4]],
            })

        # 0. O CPF/CNPJ de quem esta do outro lado. E a PRIMEIRA proposta
        # porque e a unica identidade que nao abrevia, nao muda de banco para
        # banco e nao tem xara: o Sicredi escreve '75704250149', o Cora
        # escreve '757.042.501-49', e para a regra e o mesmo documento.
        # Medido em 15/09/2026: 18% dos lancamentos trazem documento, e
        # sozinho ele resolveria 68 dos orfaos da base.
        for doc in sorted(RegraExtrato.documentos_em(desc)):
            bonito = ExtratoLancamento.formatar_doc(doc)
            quem = 'CNPJ' if len(doc) == 14 else 'CPF'
            poe(f'Pelo {quem} {bonito}', [doc],
                'o documento é o que não muda: abreviação de nome, jeito de '
                'escrever de cada banco e conta de origem deixam de importar',
                -1, tipo='documento')

        # 0b. A PESSOA, escrita de qualquer jeito. Só os pedaços raros do
        # nome, porque cada banco abrevia onde quer ('Guilherme Rocha de
        # Sousa' vira 'Guilherme Rocha de So' no Bradesco) e porque pedaço
        # comum rouba lançamento de outra categoria ('SILVA' pega meio mundo).
        teto = max(20, len(todos) // 100)
        identificam = RegraExtrato.pedacos_que_identificam(desc, freq, teto)
        if identificam:
            poe('A pessoa, escrita de qualquer jeito', identificam,
                'os pedaços do nome que aparecem pouco na sua base (' +
                ', '.join(f'{t} em {freq.get(t, 0)}' for t in identificam) +
                ') — sobrevivem à abreviação de cada banco', -1, tipo='pessoa')

        # 1. sem os numeros que mudam
        if numeros:
            corridos, atual = [], []
            for limpo in limpos:
                if not limpo:
                    if atual:
                        corridos.append(' '.join(atual))
                        atual = []
                else:
                    atual.append(limpo)
            if atual:
                corridos.append(' '.join(atual))
            poe('Sem o que muda', corridos,
                'tira os números (' + ', '.join(numeros) + ') — é o que '
                'costuma mudar de um lançamento para o outro', 0)

        # 2. so o nome de quem esta do outro lado (o rabo em letras)
        cauda = []
        for limpo in reversed(limpos):
            if not limpo:
                break
            cauda.insert(0, limpo)
        if cauda and len(cauda) < len(toks):
            poe('Só quem está do outro lado', [' '.join(cauda)],
                'o nome de quem recebeu ou pagou, sem o tipo da operação', 2)

        # 3. so o tipo da operacao (a cabeca em letras)
        cabeca = []
        for limpo in limpos:
            if not limpo:
                break
            cabeca.append(limpo)
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
    def reprocessar(regra_id):
        """A regra nova decide DE NOVO so o que era dela.

        Devolve tudo que esta preso (titulos da regra saem junto, via
        desfazer) e reclassifica os que ainda casam com o desenho novo.
        Quem nao casa mais fica em Sem categoria — e o combinado da tela.
        Devolve (devolvidos, reclassificados).
        """
        ids = {a['id'] for a in execute_query(
            'SELECT id FROM extrato_lancamentos WHERE memorizacao_id = %s',
            (regra_id,), fetch=True) or []}
        if not ids:
            return 0, 0
        devolvidos = RegraExtrato.desfazer(regra_id)
        r = RegraExtrato.get(regra_id)
        if not r or not r.get('ativo'):
            return devolvidos, 0
        alvos = [l for l in RegraExtrato.preve(r, so_sem_categoria=True)
                 if l['id'] in ids]
        direta = (r.get('aplicar') or 'direto') == 'direto'
        for l in alvos:
            RegraExtrato._grava_no_lancamento(r, l['id'])
            if direta:
                l2 = dict(l, categoria_id=r['categoria_id'],
                          centro_custo_id=r['centro_custo_id'])
                ExtratoLancamento.conciliar_automatico(l2)
        if alvos:
            RegraExtrato._contabiliza({regra_id: len(alvos)})
        return devolvidos, len(alvos)

    @staticmethod
    def desfazer(regra_id):
        """Devolve para "sem categoria" SO o que esta regra classificou.

        O que foi classificado a mao tem memorizacao_id NULL e nao e tocado —
        e por isso que a coluna existe.
        """
        alvos = execute_query(
            'SELECT id FROM extrato_lancamentos WHERE memorizacao_id = %s',
            (regra_id,), fetch=True) or []
        for a in alvos:
            # o titulo que a regra criou sai junto — senao o DRE guardaria
            # um custo cuja origem acabou de ser desfeita
            ExtratoLancamento.desconciliar(a['id'])
        if alvos:
            execute_query(
                'UPDATE extrato_lancamentos SET categoria_id = NULL, '
                '       centro_custo_id = NULL, memorizacao_id = NULL, conferir = 0 '
                ' WHERE memorizacao_id = %s', (regra_id,))
        return len(alvos)

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
                # Pedido do Anderson (27/08): "quando for aparecendo vai
                # informando no DRE". A direta concilia sozinha; a de
                # aprovacao espera o CONFIRMAR — titulo nasce do aval.
                l2 = dict(l, categoria_id=r['categoria_id'],
                          centro_custo_id=r['centro_custo_id'])
                ExtratoLancamento.conciliar_automatico(l2)
        RegraExtrato._contabiliza(usos)
        return direto, conferir

    @staticmethod
    def aplicar_em_ids(ids):
        """Compatibilidade: roda logo depois do import do OFX."""
        if not ids:
            return 0
        marks = ','.join(['%s'] * len(ids))
        # A trava vale aqui tambem, e aqui e o caminho mais perigoso: este
        # metodo roda logo depois do import, sem ninguem olhando. Sem a
        # condicao, uma regra ampla classificaria a ponta da transferencia
        # antes de o par sequer ser detectado.
        from utils.extrato_par import SQL_FORA_DA_TRAVA
        rows = execute_query(
            f'SELECT id, empresa_id, conta, valor, data, descricao, categoria_id, '
            f'       hash_dedup '
            f'  FROM extrato_lancamentos WHERE id IN ({marks}) '
            f'   AND categoria_id IS NULL AND {SQL_FORA_DA_TRAVA}',
            tuple(ids), fetch=True) or []
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
        # A TRAVA entra nos DOIS casos, com ou sem so_sem_categoria: ponta de
        # transferencia nao e pega por regra nenhuma — nem para classificar,
        # nem para CONTAR na previa. Se contasse na previa, a tela prometeria
        # "pega 8" e pegaria 6, e o numero errado e pior que numero nenhum.
        from utils.extrato_par import SQL_FORA_DA_TRAVA
        onde = [SQL_FORA_DA_TRAVA]
        if so_sem_categoria:
            onde.append('categoria_id IS NULL')
        cond = ' WHERE ' + ' AND '.join(onde)
        # hash_dedup vai junto: e a REFERENCIA da baixa. Sem ele o
        # registrar_baixa recusa e o titulo nasce pela metade.
        return execute_query(
            'SELECT id, empresa_id, conta, valor, data, descricao, categoria_id, '
            '       memorizacao_id, conferir, hash_dedup '
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

        # REPETIDA? Mesmo criterio do criar: virar copia de outra regra
        # ativa e recusado — duas iguais brigariam pela mesma descricao.
        emp_chave = atual['empresa_id'] if novo['escopo'] == 'empresa' else None
        chave = RegraExtrato._chave(novo['termos'], novo['conta'], novo['sinal'],
                                    novo['valor_exato'], novo['escopo'],
                                    novo['grupo_id'], emp_chave)
        for outra in RegraExtrato.listar(apenas_ativas=True):
            if outra['id'] == regra_id:
                continue
            if RegraExtrato._chave(outra['termos'], outra.get('conta'),
                                   outra.get('sinal'), outra.get('valor_exato'),
                                   outra.get('escopo'), outra.get('grupo_id'),
                                   outra.get('empresa_id')) == chave:
                return False, 'Já existe outra regra exatamente igual.', 0

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
        direta = (r.get('aplicar') or 'direto') == 'direto'
        for l in alvos:
            RegraExtrato._grava_no_lancamento(r, l['id'])
            if direta:
                l2 = dict(l, categoria_id=r['categoria_id'],
                          centro_custo_id=r['centro_custo_id'])
                ExtratoLancamento.conciliar_automatico(l2)
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
    def cobertura(ano, empresa_ids=None):
        """Por conta: quantos lançamentos em cada mês do ano e o último dia
        com lançamento. É a resposta a "quais extratos já vieram e quais
        faltam" (Anderson, 14/09/2026: "assim não conseguimos finalizar").

        Conta cadastrada sem lançamento aparece com os meses vazios; conta
        com lançamento mas sem cadastro aparece marcada — as duas situações
        são as que escondem o extrato que falta.
        """
        from utils.extrato_ingest import conta_normalizada, banco_curto
        cond, params = '', [int(ano)]
        if empresa_ids:
            marks = ','.join(['%s'] * len(empresa_ids))
            cond = f' AND l.empresa_id IN ({marks})'
            params += list(empresa_ids)
        rows = execute_query(
            'SELECT l.empresa_id, l.banco, l.conta, MONTH(l.data) AS m, COUNT(*) AS n, '
            '       MAX(l.data) AS ult FROM extrato_lancamentos l '
            f' WHERE YEAR(l.data) = %s{cond} '
            ' GROUP BY l.empresa_id, l.banco, l.conta, MONTH(l.data)',
            tuple(params), fetch=True) or []
        contas = {}
        for r in rows:
            k = (r['empresa_id'], conta_normalizada(r['conta']))
            c = contas.setdefault(k, {'empresa_id': r['empresa_id'],
                                      'banco': banco_curto(None, r['banco']), 'conta': r['conta'],
                                      'meses': {}, 'ultimo': None, 'total': 0, 'cadastrada': False})
            c['meses'][int(r['m'])] = int(r['n'])
            c['total'] += int(r['n'])
            if r['ult'] and (c['ultimo'] is None or r['ult'] > c['ultimo']):
                c['ultimo'] = r['ult']
        for reg in FinContaBancaria.listar(empresa_ids=empresa_ids, apenas_ativas=True):
            k = (reg['empresa_id'], conta_normalizada(reg['conta']))
            c = contas.get(k)
            if c is None:
                contas[k] = {'empresa_id': reg['empresa_id'], 'banco': reg.get('banco_nome') or 'Banco',
                             'conta': reg['conta'], 'meses': {}, 'ultimo': None, 'total': 0,
                             'cadastrada': True}
            else:
                c['cadastrada'] = True
                if reg.get('banco_nome'):
                    c['banco'] = reg['banco_nome']
        return sorted(contas.values(), key=lambda c: (c['empresa_id'], str(c['banco']), str(c['conta'])))

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
    def encerrar_sumidas(caminhos_presentes):
        """Fecha as pendências abertas cujo arquivo não está mais na _ENTRADA
        (foi renomeado ou apagado). Sem isto, renomear "X.ofx" para "5000.ofx"
        deixava DUAS pendências na tela — a velha, órfã, e a nova (14/09/2026).
        Devolve quantas fechou."""
        abertas = execute_query(
            "SELECT id, caminho FROM fin_extrato_pendencias WHERE status = 'aberta'",
            fetch=True) or []
        presentes = {(c or '').lower() for c in caminhos_presentes}
        sumidas = [a['id'] for a in abertas if (a['caminho'] or '').lower() not in presentes]
        if sumidas:
            execute_query(
                f"UPDATE fin_extrato_pendencias SET status = 'sumiu', visto_em = NOW() "
                f" WHERE id IN ({','.join(['%s'] * len(sumidas))})", tuple(sumidas), fetch=False)
        return len(sumidas)

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
