"""Honorários + Lote mensal numa tela só (etapa 4 do pedido de 17/11/2026 — feito em 18/11/2026).

Anderson: "Esse pode juntar e capricha muito no layout, quero algo que mostre as cobranças... e qual banco que está
vinculado padrão, por que teremos o Banco Padrão e poderemos decidir emitir por outro caso necessário!!"

Uma linha por contrato ativo na competência (a mesma prévia do lote: honorários + 13º + lançamentos do mês), com:
  - a cobrança DESTA competência (vence no mês seguinte): a emitir, emitida a vencer, paga, atrasada ou com erro;
  - as mensalidades dos últimos 6 meses (bolinhas), para ver de relance quem paga em dia;
  - o banco que vai emitir: o do contrato (fin_contratos.banco_boleto = id da conta de cobrança) ou o PADRÃO.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from models.fin_contrato import EMPRESA_QUALICONTAX
from models.fin_lancamento import mes_seguinte
from utils import lote_mensal
from utils.db_helper import execute_query

# código do banco para o logo
BANCO_COD = {'efi': 364, 'bradesco': 237, 'cora': 403}
BANCO_NOME = {'efi': 'Efí', 'bradesco': 'Bradesco', 'cora': 'Cora'}
MESES = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez')


def _d(v):
    return Decimal(str(v or 0)).quantize(Decimal('0.01'))


def _so_digitos(s):
    return ''.join(ch for ch in (s or '') if ch.isdigit())


def contas(empresa_id=EMPRESA_QUALICONTAX):
    """Bancos que emitem boleto (produção, ativos, não só consulta), com o padrão marcado."""
    tem = execute_query("SHOW COLUMNS FROM fin_cobranca_contas LIKE 'padrao'", fetch=True)
    col = 'COALESCE(padrao, 0)' if tem else '0'
    linhas = execute_query(
        f"""SELECT id, apelido, provedor, {col} AS padrao FROM fin_cobranca_contas
             WHERE empresa_id = %s AND ativo = 1 AND so_consulta = 0 AND ambiente = 'producao'
             ORDER BY id""", (empresa_id,), fetch=True) or []
    if linhas and not any(c['padrao'] for c in linhas):
        linhas[0]['padrao'] = 1        # sem marcação: a mais antiga (a Efí) é a padrão, como o lote faz hoje
    for c in linhas:
        c['cod'] = BANCO_COD.get(c['provedor'])
        c['nome'] = BANCO_NOME.get(c['provedor'], c['apelido'])
    return linhas


def _nome_chave(nome):
    """Razão social só com letras e números maiúsculos, sem acento: 'LTDA.' e 'LTDA' viram a mesma coisa."""
    import re, unicodedata
    s = unicodedata.normalize('NFD', str(nome or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Z0-9]+', '', s.upper())


def _historico(clientes, de, ate):
    """{cliente_id: {'AAAA-MM': {'status', 'valor', 'pago', 'venc', 'id'}}} das mensalidades por VENCIMENTO."""
    if not clientes:
        return {}
    por_doc = {_so_digitos(c['cpf_cnpj']): c['cliente_id'] for c in clientes if _so_digitos(c['cpf_cnpj'])}
    # boleto pago que entrou pelo extrato sem cliente: "Recebimento de cobrança: <nº> de <RAZÃO SOCIAL>"
    # 19/11/2026: o extrato escreve "AUTO POSTO SIM DE ITU LTDA" e o cadastro tem "LTDA." (ponto no fim) — compara só letras e números
    por_nome = {_nome_chave(c.get('nome')): c['cliente_id'] for c in clientes if c.get('nome')}
    ids = [c['cliente_id'] for c in clientes]
    ph = ','.join(['%s'] * len(ids))
    tits = execute_query(
        f"""SELECT t.id, t.cliente_id, t.contraparte_doc, t.contraparte_nome, t.descricao, t.valor, t.valor_baixado, t.status, t.vencimento
              FROM fin_titulos t LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
             WHERE t.tipo = 'R' AND t.status <> 'cancelado' AND t.vencimento >= %s AND t.vencimento < %s
               AND (cat.nome LIKE 'Honor%%' OR t.descricao LIKE 'Honor%%')
               -- 27/11/2026: o título que o PRÓPRIO LOTE tira também se chama "Honorários e serviços — Honorários Contábeis - Mês/Ano";
               -- sem esta exceção a empresa seguia "a emitir" depois de emitida (Phoenix, lote 7). O avulso de Nova cobrança continua fora.
               AND (t.descricao NOT LIKE 'Honorários e serviços —%%'
                    OR t.id IN (SELECT li.titulo_id FROM fin_lote_itens li WHERE li.titulo_id IS NOT NULL AND li.tipo = 'MENSAL'))
               AND (t.cliente_id IN ({ph}) OR t.cliente_id IS NULL)
             ORDER BY t.vencimento, t.id""", (de, ate, *ids), fetch=True) or []
    hoje = date.today()
    out = defaultdict(dict)
    for t in tits:
        cid = t['cliente_id'] or por_doc.get(_so_digitos(t['contraparte_doc']))
        if not cid and (t['descricao'] or '').startswith('Recebimento de cobrança:') and ' de ' in t['descricao']:
            cid = por_nome.get(_nome_chave(t['descricao'].split(':', 1)[1].split(' de ', 1)[-1]))
        if not cid:
            continue
        k = t['vencimento'].strftime('%Y-%m')
        st = ('pago' if t['status'] == 'liquidado' else 'parcial' if _d(t['valor_baixado']) > 0 else
              'atrasado' if t['vencimento'] < hoje else 'aberto')
        ant = out[cid].get(k)
        if ant:                       # duas no mesmo mês: soma; a pior situação vence
            ordem = ('pago', 'aberto', 'parcial', 'atrasado')
            ant['valor'] += _d(t['valor']); ant['pago'] += _d(t['valor_baixado'])
            if ordem.index(st) > ordem.index(ant['status']):
                ant['status'] = st
            continue
        out[cid][k] = {'status': st, 'valor': _d(t['valor']), 'pago': _d(t['valor_baixado']), 'venc': t['vencimento'], 'id': t['id']}
    return out


def _titulo_da_parcela(feito):
    """19/11/2026: a parcela do 13º em boleto próprio tem o título dela (não entra no histórico das mensalidades)."""
    if not feito or feito.get('status') != 'ok' or not feito.get('titulo_id'):
        return None
    t = execute_query('SELECT id, valor, valor_baixado, status, vencimento FROM fin_titulos WHERE id = %s', (feito['titulo_id'],),
                      fetch=True, fetch_one=True)
    if not t or t['status'] == 'cancelado':
        return None
    st = ('pago' if t['status'] == 'liquidado' else 'parcial' if _d(t['valor_baixado']) > 0 else
          'atrasado' if t['vencimento'] < date.today() else 'aberto')
    return {'status': st, 'valor': _d(t['valor']), 'pago': _d(t['valor_baixado']), 'venc': t['vencimento'], 'id': t['id']}


def painel(competencia, empresa_id=EMPRESA_QUALICONTAX):
    lote, linhas = lote_mensal.previa(empresa_id, competencia)
    bancos = contas(empresa_id)
    por_id = {c['id']: c for c in bancos}
    padrao = next((c for c in bancos if c['padrao']), None)
    venc_mes = mes_seguinte(competencia)                       # a cobrança da competência vence no mês seguinte
    # 6 meses de vencimento: os 5 anteriores + o desta cobrança
    m = venc_mes
    meses = [m]
    for _ in range(5):
        m = date(m.year - (m.month == 1), (m.month - 2) % 12 + 1, 1)
        meses.insert(0, m)
    clientes = [{'cliente_id': l['cliente_id'], 'cpf_cnpj': l['contrato'].get('cpf_cnpj'), 'nome': l['empresa']} for l in linhas]
    hist = _historico(clientes, meses[0], mes_seguinte(venc_mes))
    tot = defaultdict(lambda: {'n': 0, 'valor': Decimal('0')})
    por_banco = defaultdict(lambda: {'n': 0, 'valor': Decimal('0')})
    # grupo de cada empresa, para o filtro Grupo da barra (18/11/2026)
    ids = [l['cliente_id'] for l in linhas]
    grupos = {}
    if ids:
        for r in execute_query(f"""SELECT r.cliente_id, g.nome FROM cliente_grupo_relacao r JOIN grupos_clientes g ON g.id = r.grupo_id
                                    WHERE r.cliente_id IN ({','.join(['%s'] * len(ids))})""", tuple(ids), fetch=True) or []:
            grupos.setdefault(r['cliente_id'], r['nome'])
    for l in linhas:
        l['grupo'] = grupos.get(l['cliente_id']) or (l['contrato'].get('plano') or '').replace('Mensalidade ', '').strip() or ''
        l['decimo'] = (l['contrato'].get('decimo_tipo') or 'NENHUM') != 'NENHUM'
        conta = por_id.get(l.get('conta_id'))
        l['banco'] = conta or padrao
        l['banco_trocado'] = bool(conta and padrao and conta['id'] != padrao['id'])
        l['banco_mes'] = bool(l.get('conta_mes') and conta)          # só nesta competência
        if l.get('tipo') == 'DECIMO':
            # parcela do 13º em boleto próprio: situação pelo título dela; sem as bolinhas das mensalidades
            atual = _titulo_da_parcela(l.get('feito'))
            l['meses'] = []
            l['atrasos'] = [dict(atual, mes=MESES[atual['venc'].month - 1], ano=atual['venc'].year)] if atual and atual['status'] in ('atrasado', 'parcial') else []
        else:
            h = hist.get(l['cliente_id'], {})
            l['meses'] = [{'mes': MESES[x.month - 1], 'ano': x.year, **(h.get(x.strftime('%Y-%m')) or {'status': 'sem'})} for x in meses]
            atual = h.get(venc_mes.strftime('%Y-%m'))
            l['atrasos'] = [x for x in l['meses'][:-1] if x['status'] in ('atrasado', 'parcial')]
        l['atual'] = atual
        feito = l.get('feito') or {}
        if feito.get('status') == 'erro':
            fase = 'erro'
        elif atual:
            fase = {'pago': 'pagas', 'atrasado': 'atrasadas', 'parcial': 'atrasadas'}.get(atual['status'], 'cobradas')
        else:
            fase = 'emitir'
        l['fase'] = fase
        e13 = l.get('tipo') == 'DECIMO'                     # a parcela do 13º soma no valor, não na conta de empresas
        tot[fase]['n'] += 0 if e13 else 1
        tot[fase]['valor'] += (atual['valor'] if atual else l['total'])
        if fase in ('emitir', 'erro') and l['banco']:
            por_banco[l['banco']['id']]['n'] += 0 if e13 else 1
            por_banco[l['banco']['id']]['valor'] += l['total']
    # 25/11/2026 — Anderson: "quando os meninos lançarem no Lançamento uma despesa que é incluída no Honorário, aparecer
    # no Honorários uma janelinha ROXA": o que a equipe lançou para esta mensalidade. Aprovado entra na cobrança (já está
    # nos itens); aguardando aprovação só avisa (não soma) — quem aprova precisa aprovar antes de emitir.
    from models.fin_lancamento import FinLancamento
    pend = defaultdict(list)
    # Pedido parcelado ainda não aprovado: a divisão só acontece na aprovação, então aqui mostra SÓ a parcela deste mês
    # (Anderson, 25/11/2026: "o Auto Posto Xingu é parcelado e está aparecendo 1.500,00; está tudo programado para ir
    # 500,00 em cada mês?"). As datas escolhidas (parcelas_datas) ou mês a mês a partir da competência do pedido.
    for p in FinLancamento.listar(empresa_id, status='LANCADO', destino='MENSAL', origem=('SERVICO', 'DESCONTO'), limite=2000):
        n = int(p.get('parcelas') or 1)
        if n > 1 and not p.get('parcela'):
            datas = [date.fromisoformat(x) for x in (p.get('parcelas_datas') or '').split(',') if x]
            if len(datas) != n:
                datas = [mes_seguinte(p['competencia']) if k else p['competencia'] for k in range(n)]
                for k in range(2, n):
                    datas[k] = mes_seguinte(datas[k - 1])
            datas = [d.replace(day=1) for d in datas]
            if competencia not in datas:
                continue
            k = datas.index(competencia)
            base = (_d(p['valor_total']) / n).quantize(Decimal('0.01'), rounding='ROUND_DOWN')
            valor = _d(p['valor_total']) - base * (n - 1) if k == 0 else base
            nome = '%s (parcela %d/%d)' % (p['descricao'] or p['servico_nome'], k + 1, n)
        elif p['competencia'] != competencia:
            continue
        else:
            valor, nome = _d(p['valor_total']), p['descricao'] or p['servico_nome']
        pend[p['cliente_id']].append({'nome': nome, 'origem': p.get('origem'), 'id': p['id'],
                                      'valor': -valor if p.get('origem') == 'DESCONTO' else valor,
                                      'por': p.get('lancado_por_nome') or ''})
    for l in linhas:
        l['lancs'] = [] if l.get('tipo') == 'DECIMO' else [i for i in l['itens'] if i.get('lancamento_id')]
        l['v_lanc'] = sum((_d(i['valor']) for i in l['lancs']), Decimal('0'))
        l['pendentes'] = [] if l.get('tipo') == 'DECIMO' else pend.get(l['cliente_id'], [])
        l['v_pend'] = sum((x['valor'] for x in l['pendentes']), Decimal('0'))
    # 19/11/2026 — Anderson: "não queria dois painéis, queria só um e quando emitir emite os dois": a parcela do 13º em
    # boleto próprio fica DENTRO do cartão da empresa (l['dec13']); marcar a empresa emite a mensalidade e a parcela.
    dec = {l['cliente_id']: l for l in linhas if l.get('tipo') == 'DECIMO'}
    linhas = [l for l in linhas if l.get('tipo') != 'DECIMO']
    for l in linhas:
        d = dec.get(l['cliente_id'])
        if d:
            d['pendente'] = d['fase'] in ('emitir', 'erro')
        l['dec13'] = d
        l['pendente'] = l['fase'] in ('emitir', 'erro') or bool(d and d['pendente'])
    # atrasados de meses anteriores (quem deve mensalidade velha), separado da cobrança do mês
    devendo = [l for l in linhas if l['atrasos']]
    n_nota = sum(1 for l in linhas if l['nota'])
    return {'lote': lote, 'linhas': linhas, 'bancos': bancos, 'padrao': padrao, 'tot': tot, 'por_banco': por_banco,
            'venc_mes': venc_mes, 'meses': meses, 'devendo': devendo, 'n_nota': n_nota, 'n_so_boleto': len(linhas) - n_nota,
            # 18/11/2026: quadro "Nota fiscal" ao lado dos bancos (Anderson: "um retângulo que mostre o que é com e sem NF")
            'v_nota': sum((l['total'] for l in linhas if l['nota']), Decimal('0')),
            'v_so_boleto': sum((l['total'] for l in linhas if not l['nota']), Decimal('0')),
            'n_lanc': sum(1 for l in linhas if l['lancs']), 'v_lanc': sum((l['v_lanc'] for l in linhas), Decimal('0')),
            'n_pend': sum(len(l['pendentes']) for l in linhas), 'v_pend': sum((l['v_pend'] for l in linhas), Decimal('0')),
            'devendo_valor': sum((x['valor'] - x['pago'] for l in devendo for x in l['atrasos']), Decimal('0'))}
