"""Tela "Títulos a receber" em 3 abas (Anderson, 12/11/2026).

Recebido (baixas do mês), A receber (em aberto, menos inadimplentes) e
Inadimplentes (cliente com 2 ou mais títulos vencidos). Só leitura: a baixa
continua indo por registrar_baixa() nas rotas de sempre.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from utils.db_helper import execute_query as q

MES = ['', 'jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
MES_LONGO = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho',
             'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
SEM = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']
CORTE_EFI = '2026-11-05'     # antes disso os pagamentos vinham do Conexa


def hoje_br():
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()


def brl(v):
    return 'R$ ' + f'{float(v or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _comp(d):
    return f'{MES[d.month]}/{d.year}' if d else ''


def _no_app(b):
    """Boleto marcado como pago à mão no app da Efí (14/11/2026): não é baixa automática nem da equipe."""
    return str(b.get('referencia') or '').startswith('efi-app:')


def _auto(b):
    """Baixa com lastro no banco: veio do extrato/webhook OU está amarrada a uma linha do extrato
    (15/11/2026: as do Conexa amarradas ao Sicredi/Efí deixam de aparecer como "manual")."""
    return (b['origem'] in ('extrato', 'webhook') or bool(b.get('lancamento_id'))) and not _no_app(b)


def _banco_baixa(b):
    """Nome curto do banco onde o dinheiro caiu (pela linha do extrato amarrada; webhook = Efí)."""
    bl = (b.get('banco_linha') or '').strip()
    if bl:
        return {'EFI': 'Efí'}.get(bl.upper(), bl)
    if str(b.get('referencia') or '').startswith('bradesco:'):
        return 'Bradesco'                 # 17/11/2026: baixa pelo boleto do Bradesco também grava origem 'webhook'
    if str(b.get('referencia') or '').startswith('cora'):
        return 'Cora'                     # 18/11/2026: 'cora:<inv>' (boleto) e 'cora-extrato:' (crédito do extrato)
    return 'Efí' if b['origem'] in ('extrato', 'webhook') else ''


def _nome(n):
    n = (n or '').strip()
    if n.isupper() or n.islower():
        n = n.title()
        for a, b in ((' Ltda', ' LTDA'), (' Me', ' ME'), (' Epp', ' EPP'), (' S.a.', ' S.A.'),
                     (' S/a', ' S/A'), (' Eireli', ' EIRELI'), (' De ', ' de '), (' Da ', ' da '),
                     (' Do ', ' do '), (' Dos ', ' dos '), (' Das ', ' das '), (' E ', ' e ')):
            n = n.replace(a, b)
    return n


def _desc(t):
    d = (t.get('descricao') or '').strip()
    # 17/11/2026: "Honorários e serviços — Inscrição estadual ST" é SERVIÇO avulso, não o honorário do mês: mostra o
    # serviço (antes saía "Honorários nov/2026" para a cobrança avulsa da MIDAS).
    import re as _re
    m = _re.match(r'(?i)honor[aá]rios e servi[cç]os\s*[—–-]\s*(.+)$', d)
    if m and not _re.match(r'(?i)honor[aá]rios', m.group(1).strip()):
        s = m.group(1).strip()
        return (s[:34] + '…') if len(s) > 35 else s
    if d.lower().startswith('honor'):
        return 'Honorários ' + _comp(t['competencia'])
    return (d[:34] + '…') if len(d) > 35 else (d or _comp(t['competencia']))


def _digitos(s):
    return ''.join(c for c in (s or '') if c.isdigit())


def _emp_cond(empresa_ids, alias='t'):
    if not empresa_ids:
        return '', []
    return f' AND {alias}.empresa_id IN ({",".join(["%s"] * len(empresa_ids))})', list(empresa_ids)


def _status(venc, hoje):
    d = (venc - hoje).days
    if d < -30:
        return 'venceu ' + venc.strftime('%d/%m/%Y'), 'tx-venc'
    if d < 0:
        return 'venceu há %d dia%s' % (-d, '' if d == -1 else 's'), 'tx-venc'
    if d == 0:
        return 'vence hoje', 'tx-hoje'
    if d == 1:
        return 'vence amanhã', 'tx-hoje'
    return 'vence ' + venc.strftime('%d/%m'), 'tx-fut'


def _faixas_de_mes(grupos, chave='venc'):
    """20/11/2026 — Anderson: "só queria que algo me informasse nessa tela que é Nov e a partir de tal é Dezembro".
    Marca o primeiro grupo de cada mês com a faixa ('Novembro de 2026', total e quantidade do mês inteiro).
    Vale para os grupos por dia (A receber e Atrasados recentes); grupo que já é mensal não ganha faixa."""
    tot, n = defaultdict(float), defaultdict(int)
    for g in grupos:
        k = g.get('_mes')
        if k:
            tot[k] += sum(float(i['saldo']) for i in g['itens']); n[k] += len(g['itens'])
    antes = None
    for g in grupos:
        k = g.pop('_mes', None)
        if k and k != antes:
            y, m = k
            g['mes'] = {'rot': '%s de %d' % (MES_LONGO[m], y), 'tot': brl(tot[k]), 'n': n[k], 'k': '%04d-%02d' % (y, m)}
        antes = k or antes


def _cab(d, hoje):
    """Cabeçalho de data dos grupos: ('15/11', 'sábado · em 3 dias')."""
    n = (d - hoje).days
    rel = ('hoje' if n == 0 else 'amanhã' if n == 1 else 'ontem' if n == -1 else
           ('em %d dias' % n) if n > 0 else ('há %d dias' % -n))
    return d.strftime('%d/%m'), SEM[d.weekday()] + ' · ' + rel


def _msg_cobranca(titulos, total):
    meses = ', '.join(_comp(t['competencia']) for t in titulos)
    return ('Olá! Aqui é da Qualicontax. Consta em aberto %s (%s), total de %s. '
            'Pode nos enviar o comprovante ou, se preferir, mandamos o boleto atualizado.'
            % ('o honorário de' if len(titulos) == 1 else 'os honorários de', meses, brl(total)))


def _cel(c):
    c = _digitos(c)
    return ('55' + c) if len(c) in (10, 11) else ''


# ── Filtros (15/11/2026) — Anderson: "vamos meter filtros... para consultar empresas, grupo,
#    valores, serviços". Cada cartão leva o grupo, o serviço, a competência, o boleto e o valor;
#    a tela filtra sem recarregar. ─────────────────────────────────────────────────────────────
BOLETO_ROTULO = {'aberto': 'Boleto aguardando', 'vencido': 'Boleto vencido', 'expirado': 'Boleto expirado',
                 'identificado': 'Pagamento identificado', 'pago': 'Boleto pago', 'sem': 'Sem boleto'}
_BOL = {'waiting': 'aberto', 'new': 'aberto', 'unpaid': 'vencido', 'expired': 'expirado', 'paid': 'pago',
        'identified': 'identificado'}      # a Efí viu o pagamento e ainda vai confirmar (16/11/2026)


def _servico(t):
    d = (t.get('descricao') or '').strip().lower()
    if d.startswith('honor') and 'servi' in d[:30]:
        return 'Honorários e serviços'
    if d.startswith('servi'):
        return 'Serviços'
    return (t.get('categoria_nome') or 'Honorários').strip()


def _comp_chave(d):
    return d.strftime('%Y-%m') if d else ''


def _grupos_dos_clientes(ids):
    """{cliente_id: ['GRUPO A', ...]} — grupos_clientes pela cliente_grupo_relacao."""
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    out = defaultdict(list)
    for r in q(f"""SELECT r.cliente_id, g.nome FROM cliente_grupo_relacao r JOIN grupos_clientes g ON g.id = r.grupo_id
                    WHERE r.cliente_id IN ({','.join(['%s'] * len(ids))}) ORDER BY g.nome""", tuple(ids), fetch=True) or []:
        out[r['cliente_id']].append(r['nome'])
    return out


def _boletos_dos_titulos(ids):
    """{titulo_id: 'aberto'|'vencido'|'expirado'|'pago'} pelo boleto mais novo do título (sem contar erro/cancelado)."""
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    out = {}
    for r in q(f"""SELECT titulo_id, situacao FROM fin_boletos
                    WHERE titulo_id IN ({','.join(['%s'] * len(ids))}) AND situacao NOT IN ('erro', 'canceled', 'rascunho')
                    ORDER BY id""", tuple(ids), fetch=True) or []:
        out[r['titulo_id']] = _BOL.get(r['situacao'], 'sem')
    return out


def _marcas(t, grupos, bols):
    """Os dados do filtro de um título (vão nos data-* do cartão)."""
    return {'grupos': grupos.get(t.get('cliente_id'), []), 'serv': _servico(t),
            'comp': _comp_chave(t.get('competencia')), 'bol': bols.get(t.get('id'), 'sem')}


class _Catalogo:
    """Junta as opções de cada filtro para a tela (sem contar: a tela conta pelo painel aberto)."""

    def __init__(self):
        self.grupos, self.servs, self.comps, self.bols = set(), set(), set(), set()

    def add(self, m):
        self.grupos.update(m['grupos']); self.servs.add(m['serv'])
        if m['comp']:
            self.comps.add(m['comp'])
        self.bols.add(m['bol'])

    def saida(self):
        return {'grupos': sorted(self.grupos), 'servs': sorted(self.servs),
                'comps': [(c, f'{MES[int(c[5:])]}/{c[:4]}') for c in sorted(self.comps, reverse=True)],
                'bols': [(b, BOLETO_ROTULO[b]) for b in ('aberto', 'vencido', 'expirado', 'identificado', 'pago', 'sem') if b in self.bols]}


def periodo(per, de=None, ate=None, hoje=None):
    """O filtro Período da tela (17/11/2026, Anderson: "incluir buscar por mês atual, mês anterior, ano, ou
    selecionar datas"). Devolve (de, ate, rótulo curto, rótulo com 'em'/'de') ou None quando não há período.
    No Recebido vale a DATA DA BAIXA; nas outras abas, o VENCIMENTO."""
    hoje = hoje or hoje_br()
    if per == 'mes':
        de, ate = date(hoje.year, hoje.month, 1), date(hoje.year + (hoje.month == 12), hoje.month % 12 + 1, 1) - timedelta(days=1)
        rot = f'{MES_LONGO[de.month]}/{de.year}'
        return de, ate, rot, 'em ' + rot.lower()
    if per == 'ant':
        fim = date(hoje.year, hoje.month, 1) - timedelta(days=1)
        de, ate = date(fim.year, fim.month, 1), fim
        rot = f'{MES_LONGO[de.month]}/{de.year}'
        return de, ate, rot, 'em ' + rot.lower()
    if per == 'ano':
        de, ate = date(hoje.year, 1, 1), date(hoje.year, 12, 31)
        return de, ate, str(hoje.year), f'em {hoje.year}'
    if per == 'datas' and (de or ate):
        if de and ate and de > ate:
            de, ate = ate, de
        if not ate:
            rot = 'a partir de ' + de.strftime('%d/%m/%Y')
            return de, date(2099, 12, 31), rot, rot
        if not de:
            rot = 'até ' + ate.strftime('%d/%m/%Y')
            return date(2000, 1, 1), ate, rot, rot
        a, b = de.strftime('%d/%m/%Y'), ate.strftime('%d/%m/%Y')
        if de.year == ate.year:
            a = de.strftime('%d/%m')
        return de, ate, f'{a} a {b}', f'de {a} a {b}'
    return None


def duplicidades(ec='', ep=(), faixa=None):
    """Créditos PAGO EM DUPLICIDADE que entraram na faixa [de, ate) e ainda têm valor (a decidir ou a devolver):
    o que resta = valor - o que já foi usado em cobranças. Cada um vem no formato de uma baixa do Recebido."""
    try:
        rows = q(f"""SELECT d.id AS dup_id, d.situacao, d.lancamento_id, d.valor AS dup_valor, l.data, l.banco,
                            t.id AS titulo_id, t.contraparte_nome, t.descricao, t.competencia, t.cliente_id, t.vencimento,
                            c.numero_cliente, cat.nome AS categoria_nome,
                            (SELECT COALESCE(SUM(x.valor + x.juros + x.multa), 0) FROM fin_titulo_baixas x
                              WHERE x.lancamento_id = d.lancamento_id) AS usado
                       FROM fin_pagamentos_duplicados d
                       JOIN extrato_lancamentos l ON l.id = d.lancamento_id
                       LEFT JOIN fin_titulos t ON t.id = d.titulo_id
                       LEFT JOIN clientes c ON c.id = t.cliente_id
                       LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
                      WHERE d.situacao IN ('A_DECIDIR', 'DEVOLVIDO') AND l.data >= %s AND l.data < %s{ec}
                      ORDER BY l.data DESC, d.id DESC""", tuple(faixa) + tuple(ep), fetch=True) or []
    except Exception:                       # tabela ainda não criada
        return []
    out = []
    for r in rows:
        from decimal import Decimal
        v = Decimal(str(r['dup_valor'] or 0)) - Decimal(str(r['usado'] or 0))
        if v > Decimal('0.004'):
            out.append(dict(r, v=v, origem='extrato', referencia='', data_baixa=r['data']))
    return out


def _linhas_em_duplicidade(lanc_ids):
    """Das linhas do extrato das baixas, quais são crédito em duplicidade (a baixa foi paga com esse crédito)."""
    ids = [i for i in set(lanc_ids) if i]
    if not ids:
        return []
    try:
        return [r['lancamento_id'] for r in q(f"""SELECT lancamento_id FROM fin_pagamentos_duplicados
                                                   WHERE lancamento_id IN ({','.join(['%s'] * len(ids))})""",
                                              tuple(ids), fetch=True) or []]
    except Exception:
        return []


def montar(empresa_ids=None, mes=None, per=None):
    hoje = hoje_br()
    if mes is None:
        mes = date(hoje.year, hoje.month, 1)
    prox = date(mes.year + (mes.month == 12), mes.month % 12 + 1, 1)
    ec, ep = _emp_cond(empresa_ids)
    # período escolhido na barra de filtros: manda no Recebido (baixa) e nas outras abas (vencimento)
    p_de, p_ate = (per[0], per[1]) if per else (None, None)

    def no_periodo(d):
        return not per or (p_de <= d <= p_ate)

    # ── Recebido no mês (ou no período) ─────────────────────────────
    rec = q(f"""SELECT b.id, b.data_baixa, b.valor + b.juros + b.multa - b.desconto AS v, b.origem, b.referencia,
                       b.lancamento_id, l.banco AS banco_linha,
                       t.id AS titulo_id, t.contraparte_nome, t.descricao, t.competencia, t.cliente_id, c.numero_cliente,
                       cat.nome AS categoria_nome
                  FROM fin_titulo_baixas b JOIN fin_titulos t ON t.id = b.titulo_id
                  LEFT JOIN clientes c ON c.id = t.cliente_id
                  LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
                  LEFT JOIN extrato_lancamentos l ON l.id = b.lancamento_id
                 WHERE t.tipo = 'R' AND b.data_baixa >= %s AND b.data_baixa < %s{ec}
                 ORDER BY b.data_baixa DESC, b.id DESC""",
            ((p_de, p_ate + timedelta(days=1)) if per else (mes, prox)) + tuple(ep), fetch=True) or []
    cat = _Catalogo()
    rec_grupos = _grupos_dos_clientes([b['cliente_id'] for b in rec])
    rec_bols = _boletos_dos_titulos([b['titulo_id'] for b in rec])
    # 26/11/2026 — Anderson: "qual a informação para o meu financeiro que foi pago em duplicidade? Se não estiver ali,
    # e nos relatórios nunca vão entender". O crédito a mais é um cartão PRÓPRIO no dia em que entrou (o que resta dele,
    # a decidir ou a devolver); usado inteiro numa cobrança, quem aparece é a baixa daquela cobrança.
    dups = duplicidades(ec.replace('t.', 'd.'), ep, (p_de, p_ate + timedelta(days=1)) if per else (mes, prox))
    dup_lancs = {x['lancamento_id'] for x in dups} | set(_linhas_em_duplicidade([b['lancamento_id'] for b in rec]))
    rec_grupos.update(_grupos_dos_clientes([x['cliente_id'] for x in dups]))
    por_dia = defaultdict(list)
    for b in rec:
        por_dia[b['data_baixa']].append(b)
    for x in dups:
        por_dia[x['data']].append(x)
    rec_g = []
    for d in sorted(por_dia, reverse=True):
        dt, sub = _cab(d, hoje)
        itens_d = []
        for b in por_dia[d]:
            mk = _marcas({'cliente_id': b['cliente_id'], 'descricao': b['descricao'], 'categoria_nome': b['categoria_nome'],
                          'competencia': b['competencia'], 'id': b['titulo_id']}, rec_grupos, rec_bols)
            cat.add(mk)
            if b.get('dup_id'):
                itens_d.append({'id': b['titulo_id'], 'dup': b['dup_id'], 'dup_sit': b['situacao'], 'num': b['numero_cliente'] or '—',
                                'nome': _nome(b['contraparte_nome']), 'nome_full': b['contraparte_nome'] or '',
                                'valor': brl(b['v']), 'v': '%.2f' % float(b['v']), 'desc': _desc(b),
                                'dup_txt': ('boleto venc. ' + b['vencimento'].strftime('%d/%m') if b.get('vencimento') else 'boleto') + ' pago 2 vezes',
                                'cliente_id': b['cliente_id'], 'auto': True, 'app': False, 'banco': b.get('banco') or '', **mk})
                continue
            itens_d.append({'id': b['titulo_id'], 'num': b['numero_cliente'] or '—', 'nome': _nome(b['contraparte_nome']),
                            'nome_full': b['contraparte_nome'] or '',
                            'valor': brl(b['v']), 'v': '%.2f' % float(b['v']), 'desc': _desc(b), 'cliente_id': b['cliente_id'],
                            'auto': _auto(b), 'app': _no_app(b), 'com_credito': b.get('lancamento_id') in dup_lancs,
                            'banco': _banco_baixa(b), **mk})
        rec_g.append({'data': dt, 'sub': sub, 'tot': brl(sum(b['v'] for b in por_dia[d])),
                      'n': len(por_dia[d]), 'itens': itens_d})
    rec = rec + dups                       # o total e a contagem do Recebido levam o crédito em duplicidade

    # ── Em aberto ──────────────────────────────────────────────────
    abe = q(f"""SELECT t.id, t.cliente_id, t.contraparte_doc, t.contraparte_nome, t.descricao, t.competencia,
                       t.vencimento, t.valor - t.valor_baixado AS v, t.valor_baixado, t.status, t.origem,
                       t.programacao_id, p.variavel AS prog_variavel, c.numero_cliente, c.celular,
                       (SELECT kk.celular FROM contatos_clientes kk WHERE kk.cliente_id = c.id AND kk.ativo = 1 AND COALESCE(kk.celular, '') <> ''
                         ORDER BY (EXISTS (SELECT 1 FROM contatos_departamentos dd WHERE dd.contato_id = kk.id AND dd.departamento_id = 8)) DESC, kk.principal DESC, kk.id
                         LIMIT 1) AS contato_celular,
                       cat.nome AS categoria_nome
                  FROM fin_titulos t LEFT JOIN clientes c ON c.id = t.cliente_id
                  LEFT JOIN fin_programacoes p ON p.id = t.programacao_id
                  LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
                 WHERE t.tipo = 'R' AND t.status IN ('aberto', 'parcial'){ec}
                 ORDER BY t.vencimento, t.id""", tuple(ep), fetch=True) or []
    abe_grupos = _grupos_dos_clientes([t['cliente_id'] for t in abe])
    abe_bols = _boletos_dos_titulos([t['id'] for t in abe])

    def item(t):
        tag, cls = _status(t['vencimento'], hoje)
        mk = _marcas(t, abe_grupos, abe_bols)
        cat.add(mk)
        return {'id': t['id'], **mk, 'num': t['numero_cliente'] or '—', 'nome': _nome(t['contraparte_nome']),
                'nome_full': t['contraparte_nome'] or '', 'doc': _digitos(t['contraparte_doc']),
                'valor': brl(t['v']), 'saldo': '%.2f' % float(t['v']), 'desc': _desc(t),
                'descricao': t['descricao'] or '', 'tag': tag, 'cls': cls,
                'venc': t['vencimento'].isoformat(), 'cliente_id': t['cliente_id'],
                'prog': 'v' if t['prog_variavel'] else ('f' if t['programacao_id'] else ''),
                'cancelavel': t['status'] == 'aberto' and not t['valor_baixado'],
                'excluivel': t['status'] == 'aberto' and not t['valor_baixado'] and t['origem'] == 'manual',
                        'cel': _cel(t.get('contato_celular') or t['celular']), 'msg': _msg_cobranca([t], t['v']),
                'dias': (hoje - t['vencimento']).days}

    por_cli = defaultdict(list)
    for t in abe:
        por_cli[t['cliente_id'] or _digitos(t['contraparte_doc']) or t['contraparte_nome']].append(t)

    # Régua de 12 meses: competências pagas do cliente (pelo cliente_id; sem ele, pelo documento)
    comps = []
    y, m = hoje.year, hoje.month
    for _ in range(12):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        comps.insert(0, (y, m))
    ini = date(*comps[0], 1)
    pagos = defaultdict(set)
    for r in q(f"""SELECT t.cliente_id, t.contraparte_doc, t.contraparte_nome, t.competencia FROM fin_titulos t
                    WHERE t.tipo = 'R' AND t.status = 'liquidado' AND t.competencia >= %s{ec}""",
               (ini, *ep), fetch=True) or []:
        pagos[r['cliente_id'] or _digitos(r['contraparte_doc']) or r['contraparte_nome']].add(
            (r['competencia'].year, r['competencia'].month))

    # A classificação (a vencer / 1 vencido / 2+ vencidos) olha TUDO do cliente; o período só escolhe
    # o que aparece: título pelo vencimento, inadimplente se algum vencido dele cai no período.
    ina, atr, futuros = [], [], []
    for k, ts in por_cli.items():
        venc = [t for t in ts if t['vencimento'] < hoje]
        futuros += [t for t in ts if t['vencimento'] >= hoje and no_periodo(t['vencimento'])]
        if len(venc) == 1 and no_periodo(venc[0]['vencimento']):
            atr.append(venc[0])
        if len(venc) < 2 or not any(no_periodo(t['vencimento']) for t in venc):
            continue
        venc.sort(key=lambda t: t['competencia'])
        ab = {(t['competencia'].year, t['competencia'].month) for t in venc}
        total = sum(t['v'] for t in venc)
        ina.append({'num': ts[0]['numero_cliente'] or '—', 'nome': _nome(ts[0]['contraparte_nome']),
                    'nome_full': ts[0]['contraparte_nome'] or '', 'doc': _digitos(ts[0]['contraparte_doc']),
                    'cliente_id': ts[0]['cliente_id'], 'v': total, 'valor': brl(total), 'qtd': len(venc),
                    'desde': _comp(venc[0]['competencia']), 'antigo': venc[0]['vencimento'].isoformat(),
                    'cel': _cel(ts[0].get('contato_celular') or ts[0]['celular']), 'msg': _msg_cobranca(venc, total),
                    'regua': [('v' if c in ab else ('p' if c in pagos.get(k, ()) else ''),
                               '%s/%d' % (MES[c[1]], c[0])) for c in comps],
                    'titulos': [item(t) for t in venc],
                    'grupos': abe_grupos.get(ts[0]['cliente_id'], []),
                    'servs': sorted({_servico(t) for t in venc}),
                    'comps': sorted({_comp_chave(t['competencia']) for t in venc}),
                    'bols': sorted({abe_bols.get(t['id'], 'sem') for t in venc}),
                    'futuros': len(ts) - len(venc)})
    ina.sort(key=lambda x: -x['v'])

    # A receber: tudo o que ainda vai vencer, agrupado pela DATA do vencimento
    itens = [item(t) for t in sorted(futuros, key=lambda t: (t['vencimento'], -t['v']))]
    fim_sem = hoje.toordinal() + (6 - hoje.weekday())
    abe_g, por_venc = [], defaultdict(list)
    for i in itens:
        por_venc[i['venc']].append(i)
    for v in sorted(por_venc):
        d = date.fromisoformat(v)
        dt, sub = _cab(d, hoje)
        faixa = 'sem' if d.toordinal() <= fim_sem else ('mes' if (d.year, d.month) == (hoje.year, hoje.month) else 'dep')
        for i in por_venc[v]:
            i['per'] = faixa
        abe_g.append({'data': dt, 'sub': sub, 'n': len(por_venc[v]), 'hoje': d == hoje, '_mes': (d.year, d.month),
                      'tot': brl(sum(float(i['saldo']) for i in por_venc[v])), 'itens': por_venc[v]})
    _faixas_de_mes(abe_g)
    abe_f = [('todos', 'Todos', len(itens)),
             ('sem', 'Esta semana', sum(1 for i in itens if i['per'] == 'sem')),
             ('mes', 'Resto do mês', sum(1 for i in itens if i['per'] == 'mes')),
             ('dep', 'Depois', sum(1 for i in itens if i['per'] == 'dep'))]

    # Atrasados: cliente com UM só vencido — cobrar logo, antes de virar inadimplente.
    # Até 60 dias, um grupo por data; mais antigo que isso, um grupo por mês.
    atr_i = [item(t) for t in sorted(atr, key=lambda t: (t['vencimento'], -t['v']), reverse=True)]
    atr_g, chave = [], None
    for i in atr_i:
        d = date.fromisoformat(i['venc'])
        i['faixa'] = 'a30' if i['dias'] <= 30 else ('a90' if i['dias'] <= 90 else 'mais')
        k = i['venc'] if i['dias'] <= 60 else d.strftime('%Y-%m')
        if k != chave:
            chave = k
            if i['dias'] <= 60:
                dt, sub = _cab(d, hoje)
            else:
                dt, sub = f'{MES[d.month]}/{d.year}', 'venceu no mês'
            atr_g.append({'data': dt, 'sub': sub, 'n': 0, 'v': 0, 'itens': [], '_mes': (d.year, d.month) if i['dias'] <= 60 else None})
        g = atr_g[-1]
        g['itens'].append(i); g['n'] += 1; g['v'] += float(i['saldo'])
    _faixas_de_mes(atr_g)
    for g in atr_g:
        g['tot'] = brl(g.pop('v'))
    atr_f = [('todos', 'Todos', len(atr_i)),
             ('a30', 'Até 30 dias', sum(1 for i in atr_i if i['faixa'] == 'a30')),
             ('a90', '31 a 90 dias', sum(1 for i in atr_i if i['faixa'] == 'a90')),
             ('mais', 'Mais de 90 dias', sum(1 for i in atr_i if i['faixa'] == 'mais'))]

    # Perdidos: cancelados com motivo (não vão pagar), guardados para o registro
    perd = perdidos(ec, ep, hoje, per=per)
    for x in perd:
        cat.add(x)
    # Cancelados (22/11/2026 — Anderson: "ainda não temos como consultar os cancelados"): boleto/título cancelado, não perdido
    canc = cancelados(ec, ep, hoje, per=per)
    for x in canc:
        cat.add(x)

    # Pagamentos da Efí que entraram e não acharam dono (valor igual ao de outros clientes)
    esp = q("""SELECT COUNT(*) n, COALESCE(SUM(l.valor), 0) v FROM extrato_lancamentos l
                WHERE l.descricao LIKE 'Recebimento de cobran%%' AND l.data >= %s AND l.valor > 0
                  AND NOT EXISTS (SELECT 1 FROM fin_titulo_baixas b WHERE b.lancamento_id = l.id)""",
            (CORTE_EFI,), fetch=True, fetch_one=True) or {'n': 0, 'v': 0}

    ant = date(mes.year - (mes.month == 1), (mes.month - 2) % 12 + 1, 1)
    mes_rot = f'{MES_LONGO[mes.month]}/{mes.year}'
    return dict(
        hoje=hoje, mes=mes, mes_rot=mes_rot,
        mes_ant=ant.strftime('%Y-%m'), mes_prox=prox.strftime('%Y-%m') if prox <= hoje else '',
        per_rot=per[2] if per else '', rec_sub=per[3] if per else 'em ' + mes_rot.lower(),
        per_de=p_de.isoformat() if per else '', per_ate=p_ate.isoformat() if per else '',
        rec=rec_g, rec_tot=brl(sum(b['v'] for b in rec)), rec_n=len(rec),
        rec_auto=sum(1 for b in rec if _auto(b)),
        abe=abe_g, abe_f=abe_f, abe_tot=brl(sum(float(i['saldo']) for i in itens)), abe_n=len(itens),
        abe_cli=len({i['cliente_id'] or i['nome_full'] for i in itens}),
        atr=atr_g, atr_f=atr_f, atr_tot=brl(sum(float(i['saldo']) for i in atr_i)), atr_n=len(atr_i),
        ina=ina, ina_tot=brl(sum(x['v'] for x in ina)), ina_cli=len(ina),
        perd=perd, perd_tot=brl(sum(x['v'] for x in perd)), perd_n=len(perd),
        canc=canc, canc_tot=brl(sum(x['v'] for x in canc)), canc_n=len(canc),
        ina_tit=sum(x['qtd'] for x in ina),
        regua_ini=_comp(date(*comps[0], 1)), regua_fim=_comp(date(*comps[-1], 1)),
        filtros=cat.saida(),
        esp_n=esp['n'], esp_tot=brl(esp['v']), esp_desde=CORTE_EFI)


def cancelados(ec='', ep=(), hoje=None, per=None):
    """Títulos a receber cancelados que NÃO são perdidos (o boleto foi cancelado e o título junto, ou o título foi cancelado
    à mão). Diz quem cancelou e quando (pelo registro do boleto em logs_sistema). ``per`` = (de, ate, ...) pelo vencimento."""
    rows = q(f"""SELECT t.id, t.cliente_id, t.contraparte_nome, t.contraparte_doc, t.descricao, t.competencia,
                        t.vencimento, t.valor AS v, t.criado_em, c.numero_cliente, cat.nome AS categoria_nome,
                        (SELECT b.id FROM fin_boletos b WHERE b.titulo_id = t.id ORDER BY b.id DESC LIMIT 1) AS boleto_id,
                        (SELECT b.atualizado_em FROM fin_boletos b WHERE b.titulo_id = t.id ORDER BY b.id DESC LIMIT 1) AS boleto_em,
                        (SELECT l.orcamento_id FROM fin_lancamentos l WHERE l.titulo_id = t.id OR (l.boleto_id IS NOT NULL AND l.boleto_id = (SELECT b2.id FROM fin_boletos b2 WHERE b2.titulo_id = t.id ORDER BY b2.id DESC LIMIT 1)) LIMIT 1) AS orcamento_id
                   FROM fin_titulos t LEFT JOIN clientes c ON c.id = t.cliente_id
                   LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
                  WHERE t.tipo = 'R' AND t.status = 'cancelado' AND t.perdido_em IS NULL{ec}
                  ORDER BY t.id DESC""", tuple(ep), fetch=True) or []
    if not rows:
        return []
    bids = [r['boleto_id'] for r in rows if r['boleto_id']]
    quem = {}
    if bids:
        for lg in q("""SELECT registro_id, usuario_nome, data_hora FROM logs_sistema WHERE acao = 'escrita.cancelou_boleto' AND tabela_afetada = 'fin_boletos'
                         AND registro_id IN (%s) ORDER BY id""" % ','.join(str(b) for b in bids), fetch=True) or []:
            quem[int(lg['registro_id'])] = lg
    out = []
    grupos = _grupos_dos_clientes([t['cliente_id'] for t in rows])
    bols = _boletos_dos_titulos([t['id'] for t in rows])
    for t in rows:
        if per:
            v = t['vencimento']
            v = date.fromisoformat(str(v)[:10]) if not isinstance(v, date) else v
            if not (per[0] <= v <= per[1]):
                continue
        lg = quem.get(int(t['boleto_id'])) if t['boleto_id'] else None
        quando = (lg['data_hora'] if lg else t['boleto_em'])
        out.append({'id': t['id'], **_marcas(t, grupos, bols),
                    'num': t['numero_cliente'] or '—', 'nome': _nome(t['contraparte_nome']),
                    'nome_full': t['contraparte_nome'] or '', 'doc': _digitos(t['contraparte_doc']),
                    'cliente_id': t['cliente_id'], 'v': float(t['v']), 'valor': brl(t['v']), 'desc': _desc(t),
                    'venc': t['vencimento'].strftime('%d/%m/%Y'),
                    'motivo': ('Boleto nº %s cancelado no banco; o título a receber foi cancelado junto.' % t['boleto_id']) if t['boleto_id'] else 'Título cancelado.',
                    'quando': quando.strftime('%d/%m/%Y %H:%M') if quando else '', 'por': ((lg or {}).get('usuario_nome') or '').split(' ')[0],
                    'orcamento_id': t['orcamento_id']})
    return out


def _tem_colunas_perdido():
    try:
        return bool(q("SHOW COLUMNS FROM fin_titulos LIKE 'perdido_em'", fetch=True))
    except Exception:
        return False


def perdidos(ec='', ep=(), hoje=None, per=None):
    """Títulos a receber marcados como perdidos (status cancelado + perdido_em). ``per`` = (de, ate, ...) do
    filtro Período, pelo vencimento."""
    if not _tem_colunas_perdido():
        return []
    rows = q(f"""SELECT t.id, t.cliente_id, t.contraparte_nome, t.contraparte_doc, t.descricao, t.competencia,
                        t.vencimento, t.valor - t.valor_baixado AS v, t.perdido_em, t.perdido_motivo,
                        c.numero_cliente, u.nome AS por, cat.nome AS categoria_nome
                   FROM fin_titulos t LEFT JOIN clientes c ON c.id = t.cliente_id
                   LEFT JOIN usuarios u ON u.id = t.perdido_por
                   LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
                  WHERE t.tipo = 'R' AND t.status = 'cancelado' AND t.perdido_em IS NOT NULL{ec}
                  ORDER BY t.perdido_em DESC, t.vencimento""", tuple(ep), fetch=True) or []
    out = []
    grupos = _grupos_dos_clientes([t['cliente_id'] for t in rows])
    bols = _boletos_dos_titulos([t['id'] for t in rows])
    for t in rows:
        if per:
            v = t['vencimento']
            v = date.fromisoformat(str(v)[:10]) if not isinstance(v, date) else v
            if not (per[0] <= v <= per[1]):
                continue
        out.append({'id': t['id'], **_marcas(t, grupos, bols),
                    'num': t['numero_cliente'] or '—', 'nome': _nome(t['contraparte_nome']),
                    'nome_full': t['contraparte_nome'] or '', 'doc': _digitos(t['contraparte_doc']),
                    'cliente_id': t['cliente_id'], 'v': float(t['v']), 'valor': brl(t['v']), 'desc': _desc(t),
                    'venc': t['vencimento'].strftime('%d/%m/%Y'), 'motivo': t['perdido_motivo'] or '',
                    'quando': t['perdido_em'].strftime('%d/%m/%Y') if t['perdido_em'] else '',
                    'por': (t['por'] or '').split(' ')[0]})
    return out
