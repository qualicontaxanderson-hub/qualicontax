# -*- coding: utf-8 -*-
"""Extrato bancário importado (Documento E, fase 4).

Tabela compartilhada com o futuro A2: ``empresa_id`` NULL = escritório
(Qualicontax); cliente da carteira quando o A2 chegar. A idempotência do
import mora no ``hash_dedup`` (UNIQUE) — quem monta a chave é
utils.ofx_parser.chave_dedup, a mesma para parser e gravação.
"""
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


class ExtratoMemorizacao:
    """Memorizações do extrato (E2.4): a primeira decisão humana vira regra.

    O ``padrao`` é um trecho da descrição (casamento por CONTÉM, sem caixa).
    Quando mais de um casa, vence o MAIS LONGO — "CONEXA ASSINATURA" ganha de
    "CONEXA". empresa_id NULL = vale para todas as minhas empresas.
    """

    @staticmethod
    def listar(apenas_ativas=False):
        cond = 'WHERE m.ativo = 1' if apenas_ativas else ''
        return execute_query(
            f"""SELECT m.*, c.nome AS categoria_nome, c.grupo AS categoria_grupo,
                       c.tipo AS categoria_tipo, cc.nome AS centro_nome
                  FROM fin_extrato_memorizacoes m
                  JOIN fin_categorias c ON c.id = m.categoria_id
                  LEFT JOIN fin_centros_custo cc ON cc.id = m.centro_custo_id
                {cond}
                 ORDER BY m.ativo DESC, m.usos DESC, m.padrao""",
            fetch=True) or []

    @staticmethod
    def criar(padrao, categoria_id, centro_custo_id=None, empresa_id=None,
              criado_por=None):
        """None se já existir memorização ATIVA com o mesmo padrão."""
        padrao = (padrao or '').strip().upper()[:160]
        if not padrao:
            return None
        dup = execute_query(
            'SELECT id FROM fin_extrato_memorizacoes '
            'WHERE ativo = 1 AND padrao = %s', (padrao,),
            fetch=True, fetch_one=True)
        if dup:
            return None
        return execute_query(
            'INSERT INTO fin_extrato_memorizacoes '
            '(empresa_id, padrao, categoria_id, centro_custo_id, criado_por) '
            'VALUES (%s, %s, %s, %s, %s)',
            (empresa_id, padrao, categoria_id, centro_custo_id, criado_por))

    @staticmethod
    def get(mem_id):
        return execute_query('SELECT * FROM fin_extrato_memorizacoes '
                             'WHERE id = %s', (mem_id,), fetch=True, fetch_one=True)

    @staticmethod
    def set_ativa(mem_id, ativa):
        execute_query('UPDATE fin_extrato_memorizacoes SET ativo = %s '
                      'WHERE id = %s', (1 if ativa else 0, mem_id))

    @staticmethod
    def _casa(descricao, memorizacoes, empresa_id=None):
        """A memorização vencedora para esta descrição (mais longa ganha)."""
        alvo = (descricao or '').upper()
        melhor = None
        for m in memorizacoes:
            if not m['ativo']:
                continue
            if m['empresa_id'] and empresa_id and m['empresa_id'] != empresa_id:
                continue
            if m['padrao'] in alvo:
                if melhor is None or len(m['padrao']) > len(melhor['padrao']):
                    melhor = m
        return melhor

    @staticmethod
    def aplicar_em_ids(ids):
        """Classifica os lançamentos (ainda sem categoria) dessa lista de ids.
        Devolve quantos classificou — roda logo depois do import."""
        if not ids:
            return 0
        mems = ExtratoMemorizacao.listar(apenas_ativas=True)
        if not mems:
            return 0
        marks = ','.join(['%s'] * len(ids))
        rows = execute_query(
            f'SELECT id, empresa_id, descricao FROM extrato_lancamentos '
            f'WHERE id IN ({marks}) AND categoria_id IS NULL',
            tuple(ids), fetch=True) or []
        aplicados, usos = 0, {}
        for r in rows:
            m = ExtratoMemorizacao._casa(r['descricao'], mems, r['empresa_id'])
            if not m:
                continue
            execute_query(
                'UPDATE extrato_lancamentos SET categoria_id = %s, '
                'centro_custo_id = %s, memorizacao_id = %s WHERE id = %s',
                (m['categoria_id'], m['centro_custo_id'], m['id'], r['id']))
            usos[m['id']] = usos.get(m['id'], 0) + 1
            aplicados += 1
        for mid, n in usos.items():
            execute_query('UPDATE fin_extrato_memorizacoes SET usos = usos + %s, '
                          'ultimo_uso = NOW() WHERE id = %s', (n, mid))
        return aplicados

    @staticmethod
    def aplicar_retroativa(mem_id):
        """Varre TODO lançamento antigo sem categoria que casa com o padrão —
        memorizou, o passado também se resolve. Devolve quantos pegou."""
        m = ExtratoMemorizacao.get(mem_id)
        if not m or not m['ativo']:
            return 0
        like = ('%' + m['padrao'].replace(chr(92), chr(92) * 2)
                .replace('%', chr(92) + '%').replace('_', chr(92) + '_') + '%')
        params_where = [like]
        cond_emp = ''
        if m['empresa_id']:
            cond_emp = 'AND empresa_id = %s'
            params_where.append(m['empresa_id'])
        antes = execute_query(
            f"SELECT COUNT(*) AS n FROM extrato_lancamentos "
            f"WHERE categoria_id IS NULL AND UPPER(descricao) LIKE %s {cond_emp}",
            tuple(params_where), fetch=True, fetch_one=True)
        n = int((antes or {}).get('n') or 0)
        if n:
            execute_query(
                f"UPDATE extrato_lancamentos SET categoria_id = %s, "
                f"centro_custo_id = %s, memorizacao_id = %s "
                f"WHERE categoria_id IS NULL AND UPPER(descricao) LIKE %s {cond_emp}",
                tuple([m['categoria_id'], m['centro_custo_id'], m['id']] + params_where))
            execute_query('UPDATE fin_extrato_memorizacoes SET usos = usos + %s, '
                          'ultimo_uso = NOW() WHERE id = %s', (n, m['id']))
        return n


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
