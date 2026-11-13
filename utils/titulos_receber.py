"""Tela "Títulos a receber" em 3 abas (Anderson, 12/11/2026).

Recebido (baixas do mês), A receber (em aberto, menos inadimplentes) e
Inadimplentes (cliente com 2 ou mais títulos vencidos). Só leitura: a baixa
continua indo por registrar_baixa() nas rotas de sempre.
"""
from collections import defaultdict
from datetime import date, datetime
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


def montar(empresa_ids=None, mes=None):
    hoje = hoje_br()
    if mes is None:
        mes = date(hoje.year, hoje.month, 1)
    prox = date(mes.year + (mes.month == 12), mes.month % 12 + 1, 1)
    ec, ep = _emp_cond(empresa_ids)

    # ── Recebido no mês ─────────────────────────────────────────────
    rec = q(f"""SELECT b.id, b.data_baixa, b.valor + b.juros + b.multa - b.desconto AS v, b.origem,
                       t.contraparte_nome, t.descricao, t.competencia, t.cliente_id, c.numero_cliente
                  FROM fin_titulo_baixas b JOIN fin_titulos t ON t.id = b.titulo_id
                  LEFT JOIN clientes c ON c.id = t.cliente_id
                 WHERE t.tipo = 'R' AND b.data_baixa >= %s AND b.data_baixa < %s{ec}
                 ORDER BY b.data_baixa DESC, b.id DESC""",
            (mes, prox, *ep), fetch=True) or []
    por_dia = defaultdict(list)
    for b in rec:
        por_dia[b['data_baixa']].append(b)
    rec_g = []
    for d in sorted(por_dia, reverse=True):
        dt, sub = _cab(d, hoje)
        rec_g.append({'data': dt, 'sub': sub, 'tot': brl(sum(b['v'] for b in por_dia[d])),
                      'n': len(por_dia[d]),
                      'itens': [{'num': b['numero_cliente'] or '—', 'nome': _nome(b['contraparte_nome']),
                                 'valor': brl(b['v']), 'desc': _desc(b), 'cliente_id': b['cliente_id'],
                                 'auto': b['origem'] in ('extrato', 'webhook')} for b in por_dia[d]]})

    # ── Em aberto ──────────────────────────────────────────────────
    abe = q(f"""SELECT t.id, t.cliente_id, t.contraparte_doc, t.contraparte_nome, t.descricao, t.competencia,
                       t.vencimento, t.valor - t.valor_baixado AS v, t.valor_baixado, t.status, t.origem,
                       t.programacao_id, p.variavel AS prog_variavel, c.numero_cliente, c.celular
                  FROM fin_titulos t LEFT JOIN clientes c ON c.id = t.cliente_id
                  LEFT JOIN fin_programacoes p ON p.id = t.programacao_id
                 WHERE t.tipo = 'R' AND t.status IN ('aberto', 'parcial'){ec}
                 ORDER BY t.vencimento, t.id""", tuple(ep), fetch=True) or []

    def item(t):
        tag, cls = _status(t['vencimento'], hoje)
        return {'id': t['id'], 'num': t['numero_cliente'] or '—', 'nome': _nome(t['contraparte_nome']),
                'nome_full': t['contraparte_nome'] or '', 'doc': _digitos(t['contraparte_doc']),
                'valor': brl(t['v']), 'saldo': '%.2f' % float(t['v']), 'desc': _desc(t),
                'descricao': t['descricao'] or '', 'tag': tag, 'cls': cls,
                'venc': t['vencimento'].isoformat(), 'cliente_id': t['cliente_id'],
                'prog': 'v' if t['prog_variavel'] else ('f' if t['programacao_id'] else ''),
                'cancelavel': t['status'] == 'aberto' and not t['valor_baixado'],
                'excluivel': t['status'] == 'aberto' and not t['valor_baixado'] and t['origem'] == 'manual',
                        'cel': _cel(t['celular']), 'msg': _msg_cobranca([t], t['v']),
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

    ina, atr, futuros = [], [], []
    for k, ts in por_cli.items():
        venc = [t for t in ts if t['vencimento'] < hoje]
        futuros += [t for t in ts if t['vencimento'] >= hoje]
        if len(venc) == 1:
            atr.append(venc[0])
        if len(venc) < 2:
            continue
        venc.sort(key=lambda t: t['competencia'])
        ab = {(t['competencia'].year, t['competencia'].month) for t in venc}
        total = sum(t['v'] for t in venc)
        ina.append({'num': ts[0]['numero_cliente'] or '—', 'nome': _nome(ts[0]['contraparte_nome']),
                    'nome_full': ts[0]['contraparte_nome'] or '', 'doc': _digitos(ts[0]['contraparte_doc']),
                    'cliente_id': ts[0]['cliente_id'], 'v': total, 'valor': brl(total), 'qtd': len(venc),
                    'desde': _comp(venc[0]['competencia']), 'antigo': venc[0]['vencimento'].isoformat(),
                    'cel': _cel(ts[0]['celular']), 'msg': _msg_cobranca(venc, total),
                    'regua': [('v' if c in ab else ('p' if c in pagos.get(k, ()) else ''),
                               '%s/%d' % (MES[c[1]], c[0])) for c in comps],
                    'titulos': [item(t) for t in venc],
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
        per = 'sem' if d.toordinal() <= fim_sem else ('mes' if (d.year, d.month) == (hoje.year, hoje.month) else 'dep')
        for i in por_venc[v]:
            i['per'] = per
        abe_g.append({'data': dt, 'sub': sub, 'n': len(por_venc[v]), 'hoje': d == hoje,
                      'tot': brl(sum(float(i['saldo']) for i in por_venc[v])), 'itens': por_venc[v]})
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
            atr_g.append({'data': dt, 'sub': sub, 'n': 0, 'v': 0, 'itens': []})
        g = atr_g[-1]
        g['itens'].append(i); g['n'] += 1; g['v'] += float(i['saldo'])
    for g in atr_g:
        g['tot'] = brl(g.pop('v'))
    atr_f = [('todos', 'Todos', len(atr_i)),
             ('a30', 'Até 30 dias', sum(1 for i in atr_i if i['faixa'] == 'a30')),
             ('a90', '31 a 90 dias', sum(1 for i in atr_i if i['faixa'] == 'a90')),
             ('mais', 'Mais de 90 dias', sum(1 for i in atr_i if i['faixa'] == 'mais'))]

    # Pagamentos da Efí que entraram e não acharam dono (valor igual ao de outros clientes)
    esp = q("""SELECT COUNT(*) n, COALESCE(SUM(l.valor), 0) v FROM extrato_lancamentos l
                WHERE l.descricao LIKE 'Recebimento de cobran%%' AND l.data >= %s AND l.valor > 0
                  AND NOT EXISTS (SELECT 1 FROM fin_titulo_baixas b WHERE b.lancamento_id = l.id)""",
            (CORTE_EFI,), fetch=True, fetch_one=True) or {'n': 0, 'v': 0}

    ant = date(mes.year - (mes.month == 1), (mes.month - 2) % 12 + 1, 1)
    return dict(
        hoje=hoje, mes=mes, mes_rot=f'{MES_LONGO[mes.month]}/{mes.year}',
        mes_ant=ant.strftime('%Y-%m'), mes_prox=prox.strftime('%Y-%m') if prox <= hoje else '',
        rec=rec_g, rec_tot=brl(sum(b['v'] for b in rec)), rec_n=len(rec),
        rec_auto=sum(1 for b in rec if b['origem'] in ('extrato', 'webhook')),
        abe=abe_g, abe_f=abe_f, abe_tot=brl(sum(float(i['saldo']) for i in itens)), abe_n=len(itens),
        abe_cli=len({i['cliente_id'] or i['nome_full'] for i in itens}),
        atr=atr_g, atr_f=atr_f, atr_tot=brl(sum(float(i['saldo']) for i in atr_i)), atr_n=len(atr_i),
        ina=ina, ina_tot=brl(sum(x['v'] for x in ina)), ina_cli=len(ina),
        ina_tit=sum(x['qtd'] for x in ina),
        regua_ini=_comp(date(*comps[0], 1)), regua_fim=_comp(date(*comps[-1], 1)),
        esp_n=esp['n'], esp_tot=brl(esp['v']), esp_desde=CORTE_EFI)
