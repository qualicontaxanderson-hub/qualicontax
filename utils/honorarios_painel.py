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


def _historico(clientes, de, ate):
    """{cliente_id: {'AAAA-MM': {'status', 'valor', 'pago', 'venc', 'id'}}} das mensalidades por VENCIMENTO."""
    if not clientes:
        return {}
    por_doc = {_so_digitos(c['cpf_cnpj']): c['cliente_id'] for c in clientes if _so_digitos(c['cpf_cnpj'])}
    # boleto pago que entrou pelo extrato sem cliente: "Recebimento de cobrança: <nº> de <RAZÃO SOCIAL>"
    por_nome = {(c.get('nome') or '').strip().upper(): c['cliente_id'] for c in clientes if c.get('nome')}
    ids = [c['cliente_id'] for c in clientes]
    ph = ','.join(['%s'] * len(ids))
    tits = execute_query(
        f"""SELECT t.id, t.cliente_id, t.contraparte_doc, t.contraparte_nome, t.descricao, t.valor, t.valor_baixado, t.status, t.vencimento
              FROM fin_titulos t LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
             WHERE t.tipo = 'R' AND t.status <> 'cancelado' AND t.vencimento >= %s AND t.vencimento < %s
               AND (cat.nome LIKE 'Honor%%' OR t.descricao LIKE 'Honor%%')
               AND t.descricao NOT LIKE 'Honorários e serviços —%%'
               AND (t.cliente_id IN ({ph}) OR t.cliente_id IS NULL)
             ORDER BY t.vencimento, t.id""", (de, ate, *ids), fetch=True) or []
    hoje = date.today()
    out = defaultdict(dict)
    for t in tits:
        cid = t['cliente_id'] or por_doc.get(_so_digitos(t['contraparte_doc']))
        if not cid and (t['descricao'] or '').startswith('Recebimento de cobrança:') and ' de ' in t['descricao']:
            cid = por_nome.get(t['descricao'].split(':', 1)[1].split(' de ', 1)[-1].strip().upper())
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
    for l in linhas:
        conta = por_id.get(l.get('conta_id'))
        l['banco'] = conta or padrao
        l['banco_trocado'] = bool(conta and padrao and conta['id'] != padrao['id'])
        l['banco_mes'] = bool(l.get('conta_mes') and conta)          # só nesta competência
        h = hist.get(l['cliente_id'], {})
        l['meses'] = [{'mes': MESES[x.month - 1], 'ano': x.year, **(h.get(x.strftime('%Y-%m')) or {'status': 'sem'})} for x in meses]
        atual = h.get(venc_mes.strftime('%Y-%m'))
        l['atual'] = atual
        l['atrasos'] = [x for x in l['meses'][:-1] if x['status'] in ('atrasado', 'parcial')]
        feito = l.get('feito') or {}
        if feito.get('status') == 'erro':
            fase = 'erro'
        elif atual:
            fase = {'pago': 'pagas', 'atrasado': 'atrasadas', 'parcial': 'atrasadas'}.get(atual['status'], 'cobradas')
        else:
            fase = 'emitir'
        l['fase'] = fase
        tot[fase]['n'] += 1
        tot[fase]['valor'] += (atual['valor'] if atual else l['total'])
        if fase in ('emitir', 'erro') and l['banco']:
            por_banco[l['banco']['id']]['n'] += 1
            por_banco[l['banco']['id']]['valor'] += l['total']
    # atrasados de meses anteriores (quem deve mensalidade velha), separado da cobrança do mês
    devendo = [l for l in linhas if l['atrasos']]
    n_nota = sum(1 for l in linhas if l['nota'])
    return {'lote': lote, 'linhas': linhas, 'bancos': bancos, 'padrao': padrao, 'tot': tot, 'por_banco': por_banco,
            'venc_mes': venc_mes, 'meses': meses, 'devendo': devendo, 'n_nota': n_nota, 'n_so_boleto': len(linhas) - n_nota,
            # 18/11/2026: quadro "Nota fiscal" ao lado dos bancos (Anderson: "um retângulo que mostre o que é com e sem NF")
            'v_nota': sum((l['total'] for l in linhas if l['nota']), Decimal('0')),
            'v_so_boleto': sum((l['total'] for l in linhas if not l['nota']), Decimal('0')),
            'devendo_valor': sum((x['valor'] - x['pago'] for l in devendo for x in l['atrasos']), Decimal('0'))}
