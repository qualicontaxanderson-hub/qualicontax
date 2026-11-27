# -*- coding: utf-8 -*-
"""
Relatório de TÍTULOS A RECEBER (17/11/2026) — PDF e Excel, montado numa caixa de filtros.

Anderson: "Precisamos de relatórios... colocar um Gerar Relatório com logotipo da qualicontax e bem
desenhado"; "ao clicar no relatório abra uma caixa e nela escolhemos o que queremos... pode ser que eu
precise retirar um extrato de tudo... com muitos filtros e dando a opção de combinar, se for os Pago serão
pagos, a vencer, vencido"; "consultas por Grupo, por periodo, por clientes ou avulsos... por algum serviço
especifico"; "detalhado por serviços"; "Por banco"; "tem que ser completo".

Como funciona
-------------
``coletar(f)`` lê TUDO do a receber e devolve FATOS — cada pagamento (baixa) e cada título em aberto ou
perdido — já com cliente, grupos, empresa do escritório, banco e as LINHAS DE SERVIÇO:

* Situação: ``pago`` (cada baixa; vale o dia em que o dinheiro entrou), ``avencer`` / ``vencido``
  (o saldo do título em aberto, pelo vencimento contra hoje) e ``perdido`` (cancelado com motivo).
* Serviço: o honorário cujo valor é o do contrato ativo do cliente se abre nos itens do contrato
  (Honorários contábeis, LMC, Loja de Conveniência...), na proporção de cada item; o resto fica com a
  categoria do título (Pró-labore recebido, Honorários avulsos...). Filtrar por serviço soma só a parte
  daquele serviço.
* Banco: no pago, o banco da linha do extrato amarrada (webhook = Efí; app da Efí; sem linha = manual);
  no aberto, o banco do boleto (ou "Sem boleto").

Os filtros combinam (E entre campos, OU dentro do campo). ``gerar_pdf`` / ``gerar_xlsx`` desenham.
"""
import io
import re
from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from xml.sax.saxutils import escape

from utils.db_helper import execute_query as q
from utils.titulos_receber import MES, MES_LONGO, hoje_br, _no_app, _nome

SITS = OrderedDict([
    ('pago', ('Pagos', '#15803D', '#ECFDF5')),
    ('avencer', ('A vencer', '#1D4ED8', '#EFF6FF')),
    ('vencido', ('Vencidos', '#B91C1C', '#FEF2F2')),
    ('perdido', ('Perdidos', '#334155', '#F1F5F9')),
])
SITS_ATRASO = ('vencido', 'perdido')          # 27/11/2026: o que quem não vê "Recebido / A receber" pode tirar no relatório
ORGANIZAR = OrderedDict([('sit', 'Situação'), ('cliente', 'Cliente'), ('grupo', 'Grupo'), ('servico', 'Serviço'),
                         ('banco', 'Banco'), ('mes', 'Mês'), ('dia', 'Dia'), ('empresa', 'Empresa do escritório')])
DATA_REF = {'mov': 'pago pela data do pagamento, em aberto pelo vencimento', 'venc': 'pelo vencimento', 'comp': 'pela competência'}
# 17/11/2026 — Anderson: "Os banco só os que tem cobranças". Banco = conta de cobrança (fin_cobranca_contas):
# hoje Efí e Bradesco. O pagamento que caiu noutra conta (Pix, depósito) fica como "Outra conta".
BANCO_COBRANCA = {'364': 'Efí', '237': 'Bradesco'}
OUTRA, SEM_EXTRATO, APP_EFI, SEM_BOLETO = 'outra', 'manual', 'app_efi', 'sem_boleto'
BANCO_ESPECIAL = {OUTRA: 'Outra conta (Pix/depósito)', SEM_EXTRATO: 'Baixa manual (sem extrato)',
                  APP_EFI: 'Marcado no app da Efí', SEM_BOLETO: 'Sem boleto'}
BANCO_NOME = dict(BANCO_COBRANCA)


def _banco(nome_cru):
    from utils.bancos_logo import codigo
    c = codigo(nome_cru)
    if c in BANCO_COBRANCA:
        return c, BANCO_COBRANCA[c]
    return (OUTRA, BANCO_ESPECIAL[OUTRA]) if (nome_cru or '').strip() else (SEM_EXTRATO, BANCO_ESPECIAL[SEM_EXTRATO])


def _serv_base(d):
    """'Honorários contábeis — Posto Marinheiro' -> 'Honorários contábeis'; 'Honorário Contábil' idem."""
    d = re.split(r'\s+[—–-]\s+', (d or '').strip())[0].strip() or 'Sem descrição'
    if re.fullmatch(r'(?i)honor[aá]rios?\s+cont[aá]be?is?|honor[aá]rio\s+cont[aá]bil', d):
        return 'Honorários contábeis'
    return d


def _comp_rot(d):
    return f'{MES[d.month]}/{d.year}' if d else ''


# ══════════════════════════════════════════════════════════════════════════════
# Dados
# ══════════════════════════════════════════════════════════════════════════════
_CACHE = {}


def _base(fresco=False):
    """Tudo do a receber. A caixa pede a prévia a cada clique: ela usa a leitura dos últimos 60 s. O
    relatório (fresco=True) sempre lê o banco agora."""
    import time
    c = _CACHE.get('b')
    if c and not fresco and time.time() - c[0] < 60:
        return c[1]
    r = _base_ler()
    _CACHE['b'] = (time.time(), r)
    return r


def _base_ler():
    hoje = hoje_br()
    tit = q("""SELECT t.id, t.empresa_id, t.cliente_id, t.contraparte_nome, t.contraparte_doc, t.descricao,
                      t.competencia, t.vencimento, t.valor, t.valor_baixado, t.status, t.perdido_em, t.perdido_motivo,
                      c.numero_cliente, c.avulso, cat.nome AS categoria
                 FROM fin_titulos t LEFT JOIN clientes c ON c.id = t.cliente_id
                 LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
                WHERE t.tipo = 'R' AND (COALESCE(t.origem, '') <> 'extrato' OR cat.nome LIKE 'Honor%%')""", fetch=True) or []
    # 17/11/2026 — Anderson: "Deva 11 nunca foi cobrança!!". Só COBRANÇA entra: título de honorário/serviço
    # (Conexa, nota, boleto, lote, contrato, manual) ou o honorário que chegou pelo extrato. Fica fora o que o
    # extrato classificou como outra receita (Pró-labore recebido, Cashback, MA Comércio, Pedágios, DEVA11).
    T = {t['id']: t for t in tit}
    bx = q("""SELECT b.id, b.titulo_id, b.data_baixa, b.valor + b.juros + b.multa - b.desconto AS v, b.origem,
                     b.referencia, b.lancamento_id, l.banco AS banco_linha
                FROM fin_titulo_baixas b JOIN fin_titulos t ON t.id = b.titulo_id AND t.tipo = 'R'
                LEFT JOIN extrato_lancamentos l ON l.id = b.lancamento_id""", fetch=True) or []
    contratos = {}
    for r in q("""SELECT c.cliente_id, c.valor, i.descricao, i.valor AS iv FROM fin_contratos c
                    JOIN fin_contrato_itens i ON i.contrato_id = c.id
                   WHERE c.ativo = 1 AND COALESCE(c.situacao, 'ATIVO') = 'ATIVO'
                   ORDER BY c.cliente_id, i.ordem, i.id""", fetch=True) or []:
        k = contratos.setdefault(r['cliente_id'], {'valor': float(r['valor'] or 0), 'itens': []})
        k['itens'].append((_serv_base(r['descricao']), float(r['iv'] or 0)))
    grupos = defaultdict(list)
    for r in q("""SELECT r.cliente_id, g.id, g.nome FROM cliente_grupo_relacao r
                    JOIN grupos_clientes g ON g.id = r.grupo_id ORDER BY g.nome""", fetch=True) or []:
        grupos[r['cliente_id']].append((r['id'], r['nome']))
    emp = {r['cliente_id']: r['apelido'] for r in q("SELECT cliente_id, apelido FROM fin_empresas", fetch=True) or []}
    bol = {}
    for r in q("""SELECT titulo_id, provedor FROM fin_boletos
                   WHERE titulo_id IS NOT NULL AND situacao NOT IN ('erro', 'canceled', 'rascunho') ORDER BY id""",
               fetch=True) or []:
        bol[r['titulo_id']] = r['provedor']
    return hoje, T, bx, contratos, grupos, emp, bol


def _servicos(t, contratos, valor):
    """Linhas (serviço, valor) do fato. Honorário com o valor do contrato abre nos itens."""
    valor = float(valor)
    k = contratos.get(t['cliente_id'])
    cat = (t.get('categoria') or 'Sem categoria').strip()
    if k and k['itens'] and k['valor'] > 0 and abs(float(t['valor']) - k['valor']) < 0.01 and \
            re.match(r'(?i)honor', cat) and len(k['itens']) > 1:
        tot = sum(v for _, v in k['itens']) or k['valor']
        out, acum = [], 0.0
        for n, (s, v) in enumerate(k['itens']):
            parte = round(valor * v / tot, 2) if n < len(k['itens']) - 1 else round(valor - acum, 2)
            acum += parte
            out.append((s, parte))
        return out, True
    return [(_serv_base(cat) if re.match(r'(?i)honor[aá]rios?\s+cont', cat) else cat, float(valor))], False


def coletar(f, fresco=False, so_base=False):
    """f: dict de filtros já lidos (ler_filtros). Devolve (fatos, hoje); so_base = só situação e período."""
    hoje, T, bx, contratos, grupos, emp, bol = _base(fresco)
    fatos = []

    def fato(t, sit, valor, **extra):
        linhas, aberto = _servicos(t, contratos, valor)
        gs = grupos.get(t['cliente_id'], [])
        fatos.append({
            'sit': sit, 'titulo_id': t['id'], 'cliente_id': t['cliente_id'],
            'num': str(t['numero_cliente'] or '').strip(), 'nome': _nome(re.sub(r'(?i)^(de|do|da)\s+', '', (t['contraparte_nome'] or '').strip())) or '—',
            'doc': ''.join(ch for ch in (t['contraparte_doc'] or '') if ch.isdigit()),
            'tipo_cad': ('avulso' if t['avulso'] else 'cliente') if t['cliente_id'] else 'sem',
            'grupos': [n for _, n in gs], 'grupo_ids': [str(i) for i, _ in gs],
            'empresa': emp.get(t['empresa_id'], '—'), 'empresa_id': t['empresa_id'],
            'venc': t['vencimento'], 'comp': t['competencia'], 'categoria': t.get('categoria') or '',
            'descricao': t['descricao'] or '', 'linhas': linhas, 'aberto_contrato': aberto,
            'valor': float(valor), **extra})

    for b in bx:
        t = T.get(b['titulo_id'])
        if not t:
            continue
        if _no_app(b):
            bk, bn = APP_EFI, BANCO_ESPECIAL[APP_EFI]
        elif (b.get('banco_linha') or '').strip():
            bk, bn = _banco(b['banco_linha'])
        elif str(b.get('referencia') or '').startswith('bradesco:'):
            bk, bn = '237', 'Bradesco'    # 17/11/2026: boleto do Bradesco também baixa com origem 'webhook'
        elif str(b.get('referencia') or '').startswith('cora'):
            bk, bn = '403', 'Cora'        # 18/11/2026: boleto ou crédito do Cora
        elif b['origem'] == 'webhook' or b['origem'] == 'extrato':
            bk, bn = '364', 'Efí'
        else:
            bk, bn = SEM_EXTRATO, BANCO_ESPECIAL[SEM_EXTRATO]
        fato(t, 'pago', b['v'], data_mov=b['data_baixa'], banco_k=bk, banco=bn)
    # 26/11/2026 — o crédito PAGO EM DUPLICIDADE (o que resta dele) entra no Pago como linha própria, para o
    # relatório contar o dinheiro que entrou e dizer o que ele é (Anderson: "nos relatórios nunca vão entender")
    from utils.titulos_receber import duplicidades
    for x in duplicidades(faixa=(date(2000, 1, 1), date(2100, 1, 1))):
        t = T.get(x['titulo_id'])
        if not t:
            continue
        bk, bn = _banco(x['banco']) if (x.get('banco') or '').strip() else ('403', 'Cora')
        rot = 'Pago em duplicidade — ' + ('a devolver' if x['situacao'] == 'DEVOLVIDO' else 'a decidir')
        fato(t, 'pago', x['v'], data_mov=x['data'], banco_k=bk, banco=bn, dobro=True)
        fatos[-1].update(linhas=[(rot, round(float(x['v']), 2))], descricao=rot + ' · ' + (t['descricao'] or ''),
                         aberto_contrato=False)
    for t in T.values():
        saldo = float(t['valor']) - float(t['valor_baixado'] or 0)
        prov = bol.get(t['id'])
        bk, bn = ({'efi': ('364', 'Efí'), 'bradesco': ('237', 'Bradesco'), 'cora': ('403', 'Cora')}.get(prov, ('x:' + str(prov), str(prov)))
                  if prov else (SEM_BOLETO, BANCO_ESPECIAL[SEM_BOLETO]))
        if t['status'] in ('aberto', 'parcial') and saldo > 0.004:
            sit = 'vencido' if t['vencimento'] < hoje else 'avencer'
            fato(t, sit, saldo, data_mov=t['vencimento'], banco_k=bk, banco=bn,
                 dias=(hoje - t['vencimento']).days)
        elif t['status'] == 'cancelado' and t.get('perdido_em') and saldo > 0.004:
            fato(t, 'perdido', saldo, data_mov=t['vencimento'], banco_k=bk, banco=bn,
                 motivo=t.get('perdido_motivo') or '', perdido_em=t['perdido_em'])

    # data que vale para o período e a ordem
    for x in fatos:
        x['data'] = x['comp'] if f['data_ref'] == 'comp' else (x['venc'] if f['data_ref'] == 'venc' else x['data_mov'])
    base = [x for x in fatos if _passa_base(x, f)]
    return (base, hoje) if so_base else (filtrar(base, f), hoje)


def _passa_base(x, f):
    """Situação e período: o que define o universo do relatório."""
    if f['sit'] and x['sit'] not in f['sit']:
        return False
    if f['de'] and (not x['data'] or x['data'] < f['de']):
        return False
    if f['ate'] and (not x['data'] or x['data'] > f['ate']):
        return False
    return True


# Filtros "inteligentes" (17/11/2026 — Anderson: "Os filtros tem que ser os inteligente e ocultos"): cada um
# mostra só o que existe com os OUTROS marcados, com quantos lançamentos e quanto dá.
_SEL = {'cad': 'tipo_cad', 'grupos': 'grupos', 'clientes': 'clientes', 'servicos': 'servicos', 'bancos': 'bancos',
        'emp': 'empresas'}


def _chaves_dim(x, d):
    if d == 'cad':
        return [x['tipo_cad']]
    if d == 'grupos':
        return x['grupo_ids'] or ['sem']
    if d == 'clientes':
        return [str(x['cliente_id'])] if x['cliente_id'] else []
    if d == 'servicos':
        return [s for s, _ in x['linhas']]
    if d == 'bancos':
        return [x['banco_k']]
    return [str(x['empresa_id'])]


def filtrar(base, f, menos=None):
    """Os fatos da base que passam em todos os filtros (menos ``menos``). Serviço escolhido: o fato fica só com
    a parte daquele serviço (cópia — a base não muda)."""
    out = []
    for x in base:
        ok = True
        for d, k in _SEL.items():
            if d == menos or not f[k]:
                continue
            if not (set(_chaves_dim(x, d)) & f[k]):
                ok = False
                break
        if not ok:
            continue
        if f['servicos'] and menos != 'servicos':
            ls = [(s, v) for s, v in x['linhas'] if s in f['servicos']]
            x = dict(x, linhas=ls, valor=round(sum(v for _, v in ls), 2))
        if menos != 'valor':
            if f['vmin'] is not None and x['valor'] < f['vmin']:
                continue
            if f['vmax'] is not None and x['valor'] > f['vmax']:
                continue
        if f['busca'] and f['busca'] not in f"{x['num']} {x['nome']} {x['doc']}".lower():
            continue
        out.append(x)
    return out


def _nome_marcado(d, v):
    _, T, _, _, grupos, emp, _ = _base()
    if d == 'grupos':
        if v == 'sem':
            return 'Sem grupo'
        for gs in grupos.values():
            for i, n in gs:
                if str(i) == v:
                    return n
    if d == 'clientes':
        for t in T.values():
            if str(t['cliente_id']) == v:
                return _nome(t['contraparte_nome'])
    if d == 'bancos':
        return BANCO_NOME.get(v) or BANCO_ESPECIAL.get(v, v)
    if d == 'cad':
        return {'cliente': 'Clientes', 'avulso': 'Avulsos', 'sem': 'Sem cadastro'}.get(v, v)
    if d == 'emp':
        return {str(k): n for k, n in emp.items()}.get(v, v)
    return v


def _facetas(base, f):
    """{dim: [{v, rot, n, total, ...}]} — só opções com lançamento (as marcadas ficam mesmo zeradas)."""
    out = {}
    for d, k in _SEL.items():
        acc = {}
        for x in filtrar(base, f, menos=d):
            for v in _chaves_dim(x, d):
                o = acc.get(v)
                if o is None:
                    o = acc[v] = {'v': v, 'n': 0, 'total': 0.0}
                    if d == 'cad':
                        o['rot'] = {'cliente': 'Clientes', 'avulso': 'Avulsos', 'sem': 'Sem cadastro'}[v]
                    elif d == 'grupos':
                        o['rot'] = 'Sem grupo' if v == 'sem' else x['grupos'][x['grupo_ids'].index(v)]
                    elif d == 'clientes':
                        o['rot'], o['num'], o['av'], o['doc'] = x['nome'], x['num'], x['tipo_cad'] == 'avulso', x['doc']
                    elif d == 'bancos':
                        o['rot'] = x['banco']
                    elif d == 'emp':
                        o['rot'] = x['empresa']
                    else:
                        o['rot'] = v
                o['n'] += 1
                o['total'] += (dict(x['linhas']).get(v, 0.0) if d == 'servicos' else x['valor'])
        for v in f[k]:     # marcado e zerado: continua na lista (com o nome) para poder tirar
            if v not in acc:
                acc[v] = {'v': v, 'rot': _nome_marcado(d, v), 'n': 0, 'total': 0.0}
        lst = sorted(acc.values(), key=lambda o: (-o['n'], str(o['rot'])))
        if d == 'clientes':
            lst.sort(key=lambda o: (int(o['num']) if str(o.get('num', '')).isdigit() else 10 ** 9, str(o['rot'])))
        elif d == 'grupos':
            lst.sort(key=lambda o: (o['v'] == 'sem', str(o['rot'])))
        for o in lst:
            o['total'] = round(o['total'], 2)
        out[d] = lst
    return out


def ler_filtros(a):
    """a: request.args (ou dict). Listas vêm separadas por vírgula."""
    def lst(k):
        return {v for v in str(a.get(k) or '').split(',') if v.strip()}

    def dt(k):
        try:
            return date.fromisoformat(str(a.get(k) or '').strip()[:10])
        except ValueError:
            return None

    def num(k):
        try:
            v = str(a.get(k) or '').replace('.', '').replace(',', '.').strip()
            return float(v) if v else None
        except ValueError:
            return None
    org = a.get('org') if a.get('org') in ORGANIZAR else 'sit'
    return {'sit': lst('sit') & set(SITS) or set(SITS) - {'perdido'}, 'de': dt('de'), 'ate': dt('ate'),
            'data_ref': a.get('ref') if a.get('ref') in DATA_REF else 'mov',
            'tipo_cad': lst('cad') & {'cliente', 'avulso', 'sem'}, 'grupos': lst('grupos'), 'clientes': lst('clientes'),
            'servicos': lst('servicos'), 'bancos': lst('bancos'), 'empresas': lst('emp'),
            'vmin': num('vmin'), 'vmax': num('vmax'), 'busca': str(a.get('busca') or '').strip().lower()[:60],
            'org': org, 'det': 'resumo' if a.get('det') == 'resumo' else 'analitico',
            'servlinhas': a.get('servlinhas', '1') != '0'}


def previa(f, sits=None):
    base, _ = coletar(f, so_base=True)
    fatos = filtrar(base, f)
    por = {k: [0, 0.0] for k in (sits or SITS)}
    for x in fatos:
        if x['sit'] in por:
            por[x['sit']][0] += 1
            por[x['sit']][1] += x['valor']
    return {'n': len(fatos), 'total': round(sum(x['valor'] for x in fatos), 2),
            'clientes': len({x['cliente_id'] or x['nome'] for x in fatos}),
            'por': {k: [v[0], round(v[1], 2)] for k, v in por.items()},
            'facetas': _facetas(base, f)}


def descrever(f, rot):
    """Os filtros por escrito para o cabeçalho. rot: nomes de grupos/clientes/bancos/empresas por id."""
    p = ['Situação: ' + ', '.join(SITS[s][0] for s in SITS if s in f['sit'])]
    if f['de'] or f['ate']:
        de = f['de'].strftime('%d/%m/%Y') if f['de'] else 'o início'
        ate = f['ate'].strftime('%d/%m/%Y') if f['ate'] else 'hoje em diante'
        p.append(f'Período: {de} a {ate} ({DATA_REF[f["data_ref"]]})')
    else:
        p.append('Período: tudo')
    if f['tipo_cad']:
        p.append('Cadastro: ' + ', '.join({'cliente': 'clientes', 'avulso': 'avulsos', 'sem': 'sem cadastro'}[k] for k in sorted(f['tipo_cad'])))
    for chave, nome in (('grupos', 'Grupo'), ('clientes', 'Cliente'), ('bancos', 'Banco'), ('empresas', 'Empresa')):
        if f[chave]:
            vs = [rot.get(chave, {}).get(v, v) for v in f[chave]]
            p.append(f'{nome}: ' + (', '.join(sorted(vs)[:6]) + (f' e mais {len(vs) - 6}' if len(vs) > 6 else '')))
    if f['servicos']:
        p.append('Serviço: ' + ', '.join(sorted(f['servicos'])))
    if f['vmin'] is not None or f['vmax'] is not None:
        p.append('Valor: ' + (f'de {_brl(f["vmin"])} ' if f['vmin'] is not None else '') + (f'até {_brl(f["vmax"])}' if f['vmax'] is not None else ''))
    if f['busca']:
        p.append(f'Busca: "{f["busca"]}"')
    return p


# ══════════════════════════════════════════════════════════════════════════════
# Agrupar
# ══════════════════════════════════════════════════════════════════════════════
def _brl(v):
    s = f'{float(v or 0):,.2f}'
    return 'R$ ' + s.replace(',', 'X').replace('.', ',').replace('X', '.')


def _dbr(d):
    return d.strftime('%d/%m/%Y') if d else '—'


def _chaves(x, org):
    """Seção(ões) do fato: [(chave de ordem, rótulo, valor da parte)]. Serviço reparte o fato nas linhas."""
    if org == 'sit':
        return [(list(SITS).index(x['sit']), SITS[x['sit']][0], x['valor'])]
    if org == 'cliente':
        return [((x['nome'].lower(), x['num']), (f"{x['num']} · " if x['num'] else '') + x['nome'], x['valor'])]
    if org == 'grupo':
        g = x['grupos'][0] if x['grupos'] else None
        return [((g is None, (g or '').lower()), g or 'Sem grupo', x['valor'])]
    if org == 'servico':
        return [((s.lower(),), s, v) for s, v in x['linhas']]
    if org == 'banco':
        return [((x['banco_k'] in (SEM_EXTRATO, APP_EFI, SEM_BOLETO), x['banco'].lower()), x['banco'], x['valor'])]
    if org == 'empresa':
        return [((x['empresa'].lower(),), x['empresa'], x['valor'])]
    d = x['data']
    if not d:
        return [((date.max,), 'Sem data', x['valor'])]
    if org == 'mes':
        return [((d.year, d.month), f'{MES_LONGO[d.month]}/{d.year}', x['valor'])]
    return [((d,), d.strftime('%d/%m/%Y'), x['valor'])]


def secoes(fatos, org):
    """[{'rot', 'itens': [(fato, valor_da_parte)], 'tot', 'por': {sit: [n, v]}}] na ordem certa."""
    S = {}
    for x in fatos:
        for k, rot, v in _chaves(x, org):
            s = S.setdefault(k, {'rot': rot, 'itens': [], 'tot': 0.0, 'por': {s: [0, 0.0] for s in SITS}})
            s['itens'].append((x, v)); s['tot'] += v
            s['por'][x['sit']][0] += 1; s['por'][x['sit']][1] += v
    ordem = sorted(S, key=lambda k: k)
    if org in ('cliente', 'grupo', 'servico', 'banco', 'empresa'):
        ordem = sorted(S, key=lambda k: -S[k]['tot']) if org in ('servico', 'banco') else ordem
    if org in ('mes', 'dia'):
        ordem = sorted(S)
    out = [S[k] for k in ordem]
    for s in out:
        s['itens'].sort(key=lambda z: (z[0]['data'] or date.max, z[0]['nome']))
    return out


def _resumo_por(fatos, campo):
    """[(rótulo, n, total)] por serviço ou banco, maior primeiro."""
    acc = defaultdict(lambda: [0, 0.0])
    for x in fatos:
        if campo == 'servico':
            for s, v in x['linhas']:
                acc[s][0] += 1; acc[s][1] += v
        else:
            acc[x['banco']][0] += 1; acc[x['banco']][1] += x['valor']
    return sorted(((k, n, v) for k, (n, v) in acc.items()), key=lambda z: -z[2])


# ══════════════════════════════════════════════════════════════════════════════
# PDF
# ══════════════════════════════════════════════════════════════════════════════
def gerar_pdf(ctx):
    """ctx: fatos, f, filtros (lista de textos), gerado_em, gerado_por, logo_path, titulo."""
    from reportlab.graphics.shapes import Drawing, Rect
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, KeepTogether, SimpleDocTemplate, Spacer, Table, TableStyle
    from utils.relatorio_clientes import CINZA, LINHA, VERDE, VERDE_ESC, ZEBRA, _CanvasNumerado, _p

    fatos, f = ctx['fatos'], ctx['f']
    TEXTO = colors.HexColor('#1E293B')
    total = sum(x['valor'] for x in fatos)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), topMargin=8 * mm, bottomMargin=13 * mm,
                            leftMargin=9 * mm, rightMargin=9 * mm, title=ctx.get('titulo') or 'Relatório de cobranças',
                            author='Qualicontax')
    L = doc.width
    el = []

    # ── Cabeçalho ──
    logo = ''
    if ctx.get('logo_path'):
        try:
            from reportlab.lib.utils import ImageReader
            iw, ih = ImageReader(ctx['logo_path']).getSize()
            logo = Image(ctx['logo_path'], width=15 * mm * iw / ih, height=15 * mm)
        except Exception:
            logo = ''
    org_rot = ORGANIZAR[f['org']]
    chip = Table([[_p(('ANALÍTICO' if f['det'] == 'analitico' else 'RESUMIDO') + ' · POR ' + org_rot.upper(), 6.8, colors.white, bold=True, align=TA_CENTER)]],
                 colWidths=[46 * mm], rowHeights=[5.2 * mm])
    chip.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), VERDE), ('ROUNDEDCORNERS', [3, 3, 3, 3]),
                              ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 0),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
    lt = Table([[_p(escape(ctx.get('titulo') or 'Relatório de cobranças'), 17, VERDE_ESC, bold=True, leading=20), chip]],
               colWidths=[L - 34 * mm - 52 * mm - 50 * mm, 50 * mm])
    lt.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    meio = [lt, _p('Qualicontax Assessoria Contábil — honorários e serviços a receber', 8.3, CINZA)]
    meta = _p(f"Gerado em <b>{ctx['gerado_em']}</b>" + (f"<br/>por {escape(ctx['gerado_por'])}" if ctx.get('gerado_por') else ''),
              7.8, CINZA, align=TA_RIGHT, leading=10.5)
    head = Table([[logo, meio, meta]], colWidths=[34 * mm, L - 34 * mm - 52 * mm, 52 * mm])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LINEBELOW', (0, 0), (-1, -1), 2, VERDE),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 7), ('LEFTPADDING', (0, 0), (0, 0), 0)]))
    el += [head, Spacer(1, 5)]

    # ── Filtros ──
    filt = Table([[_p('<b>O QUE ENTROU</b>', 6.8, colors.HexColor('#15803D')),
                   _p('&nbsp;&nbsp;·&nbsp;&nbsp;'.join(escape(t) for t in ctx['filtros']), 7.8, colors.HexColor('#334155'), leading=10.5)]],
                 colWidths=[24 * mm, L - 24 * mm])
    filt.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F7FBF8')), ('BOX', (0, 0), (-1, -1), 0.6, LINHA),
                              ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 5),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 5), ('ROUNDEDCORNERS', [5, 5, 5, 5])]))
    el += [filt, Spacer(1, 6)]

    # ── Quadros: uma caixa por situação marcada + total ──
    por = {k: [0, 0.0] for k in SITS}
    for x in fatos:
        por[x['sit']][0] += 1; por[x['sit']][1] += x['valor']
    cards = [(SITS[k][0], por[k], SITS[k][1], SITS[k][2]) for k in SITS if k in f['sit']]
    cards.append(('Total do relatório', [len(fatos), total], '#0F3D2E', '#F0FDF4'))
    gap = 2.4 * mm
    cw = (L - gap * (len(cards) - 1)) / len(cards)
    cel = []
    for rot, (n, v), cor, fundo in cards:
        ult = rot.startswith('Total')
        t = Table([[_p(rot.upper(), 6.6, colors.white if ult else colors.HexColor(cor), bold=True)],
                   [_p(f'<b>{_brl(v)}</b>', 13, colors.white if ult else colors.HexColor(cor), leading=15)],
                   [_p(f'{n} lançamento{"s" if n != 1 else ""}', 6.6, colors.HexColor('#D1FAE5') if ult else CINZA)]], colWidths=[cw])
        t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(cor) if ult else colors.HexColor(fundo)),
                               ('LINEABOVE', (0, 0), (-1, 0), 2.2, colors.HexColor(cor)),
                               ('LEFTPADDING', (0, 0), (-1, -1), 7), ('TOPPADDING', (0, 0), (-1, 0), 5),
                               ('TOPPADDING', (0, 1), (-1, -1), 1), ('BOTTOMPADDING', (0, -1), (-1, -1), 5),
                               ('BOTTOMPADDING', (0, 0), (-1, -2), 0)]))
        cel += [t, '']
    krow = Table([cel[:-1]], colWidths=sum([[cw, gap] for _ in cards], [])[:-1])
    krow.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                              ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    el += [krow, Spacer(1, 7)]

    if not fatos:
        el.append(Spacer(1, 20))
        el.append(_p('Nenhum lançamento com esses filtros.', 11, CINZA, align=TA_CENTER))
        doc.build(el, canvasmaker=lambda *a, **k: _CanvasNumerado(*a, rodape='Qualicontax · ' + ctx['gerado_em'], **k))
        return buf.getvalue()

    hd = lambda t, a=0: _p(t, 6.8, colors.white, bold=True, align=a)
    sits_on = [k for k in SITS if k in f['sit']]

    def barra(v, tot, cor, larg=30 * mm):
        d = Drawing(larg, 6)
        d.add(Rect(0, 1, larg, 4, fillColor=colors.HexColor('#EEF2F0'), strokeColor=None))
        if tot and v > 0:
            d.add(Rect(0, 1, max(1.2, larg * v / tot), 4, fillColor=colors.HexColor(cor), strokeColor=None))
        return d

    def estilo(t, n_linhas, sub=True):
        st = [('BACKGROUND', (0, 0), (-1, 0), VERDE_ESC), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
              ('TOPPADDING', (0, 0), (-1, -1), 2.6), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.6),
              ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
              ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINHA), ('BOX', (0, 0), (-1, -1), 0.6, LINHA)]
        for r in range(1, n_linhas + 1):
            if r % 2 == 0:
                st.append(('BACKGROUND', (0, r), (-1, r), ZEBRA))
        if sub:
            st += [('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#F0FDF4')), ('LINEABOVE', (0, -1), (-1, -1), 0.9, VERDE)]
        t.setStyle(TableStyle(st))
        return t

    # ── Resumo por <organizar>: matriz seção × situação ──
    secs = secoes(fatos, f['org'])
    el.append(_p(f'RESUMO POR {org_rot.upper()}', 8, VERDE_ESC, bold=True)); el.append(Spacer(1, 3))
    cab = [hd(org_rot)] + [hd(SITS[k][0], TA_RIGHT) for k in sits_on] + [hd('Total', TA_RIGHT), hd('% do total'), hd('Qtd', TA_RIGHT)]
    lin = []
    for s in secs:
        lin.append([_p(f'<b>{escape(s["rot"])}</b>', 7.4, TEXTO)]
                   + [_p(_brl(s['por'][k][1]) if s['por'][k][0] else '—', 7.3, colors.HexColor(SITS[k][1]), align=TA_RIGHT) for k in sits_on]
                   + [_p(f'<b>{_brl(s["tot"])}</b>', 7.5, VERDE_ESC, align=TA_RIGHT),
                      barra(s['tot'], total, '#16A34A'),
                      _p(str(sum(v[0] for v in s['por'].values())), 7.2, CINZA, align=TA_RIGHT)])
    tot_l = [_p('<b>Total</b>', 7.6, VERDE_ESC)] + [_p(f'<b>{_brl(por[k][1])}</b>', 7.4, colors.HexColor(SITS[k][1]), align=TA_RIGHT) for k in sits_on] \
        + [_p(f'<b>{_brl(total)}</b>', 7.8, VERDE_ESC, align=TA_RIGHT), '', _p(f'<b>{len(fatos)}</b>', 7.2, VERDE_ESC, align=TA_RIGHT)]
    larg = [70] + [26] * len(sits_on) + [28, 34, 12]
    el.append(estilo(Table([cab] + lin + [tot_l], colWidths=[L * w / sum(larg) for w in larg], repeatRows=1), len(lin)))
    el.append(Spacer(1, 8))

    # ── Por serviço e por banco (sempre: "detalhado por serviços", "por banco") ──
    def quadro(tit, linhas, w):
        corpo = [[_p(tit.upper(), 7, VERDE_ESC, bold=True), '', '', '']]
        for rot, n, v in linhas[:14]:
            corpo.append([_p(escape(rot), 7.2, TEXTO), barra(v, total, '#0F766E', w * .26), _p(f'<b>{_brl(v)}</b>', 7.3, VERDE_ESC, align=TA_RIGHT),
                          _p(f'{100 * v / total:.0f}%' if total else '—', 6.8, CINZA, align=TA_RIGHT)])
        if len(linhas) > 14:
            resto = linhas[14:]
            corpo.append([_p(f'Outros {len(resto)}', 7, CINZA), '', _p(_brl(sum(z[2] for z in resto)), 7, CINZA, align=TA_RIGHT), ''])
        t = Table(corpo, colWidths=[w * .42, w * .27, w * .21, w * .10])
        t.setStyle(TableStyle([('SPAN', (0, 0), (-1, 0)), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                               ('LINEBELOW', (0, 0), (-1, 0), 0.8, LINHA), ('BOX', (0, 0), (-1, -1), 0.8, LINHA),
                               ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                               ('BOTTOMPADDING', (0, 0), (-1, 0), 4), ('ROUNDEDCORNERS', [6, 6, 6, 6])]))
        return t
    qs = [(t, c) for t, c in (('Por serviço', 'servico'), ('Por banco', 'banco')) if c != f['org']]   # o resumo de cima já é esse
    qw = (L - gap) / 2
    cels = [quadro(t, _resumo_por(fatos, c), qw) for t, c in qs]
    dois = Table([[cels[0], '', cels[1]]] if len(cels) == 2 else [[cels[0], '', '']], colWidths=[qw, gap, qw])
    dois.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    el += [dois, Spacer(1, 9)]

    # ── Lista (analítico) ──
    if f['det'] == 'analitico':
        el.append(_p('LANÇAMENTOS', 8, VERDE_ESC, bold=True)); el.append(Spacer(1, 3))
        cab = [hd('Data'), hd('Nº'), hd('Cliente'), hd('Serviço'), hd('Compet.'), hd('Situação'), hd('Banco'), hd('Valor', TA_RIGHT)]
        larg = [17, 10, 66, 60, 14, 30, 30, 22]
        rot_data = {'mov': 'Data', 'venc': 'Vencimento', 'comp': 'Competência'}[f['data_ref']]
        cab[0] = hd(rot_data)
        for s in secs:
            cor = SITS[s['itens'][0][0]['sit']][1] if f['org'] == 'sit' else '#15803D'
            fundo = SITS[s['itens'][0][0]['sit']][2] if f['org'] == 'sit' else '#F0FDF4'
            faixa = Table([[_p(f'<b>{escape(s["rot"])}</b>', 8.4, colors.HexColor(cor)),
                            _p(f'{len(s["itens"])} lançamento{"s" if len(s["itens"]) != 1 else ""} · <b>{_brl(s["tot"])}</b>', 7.8, colors.HexColor(cor), align=TA_RIGHT)]],
                          colWidths=[L * .6, L * .4])
            faixa.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(fundo)), ('LINEBEFORE', (0, 0), (0, -1), 2.6, colors.HexColor(cor)),
                                       ('TOPPADDING', (0, 0), (-1, -1), 3.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5),
                                       ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 6)]))
            linhas = []
            for x, v in s['itens']:
                nome, cs, _ = SITS[x['sit']]
                if x['sit'] == 'pago':
                    sit_t = f'<font color="{cs}"><b>Pago</b></font> {_dbr(x["data_mov"])}'
                elif x['sit'] == 'vencido':
                    sit_t = f'<font color="{cs}"><b>Vencido</b></font> há {x["dias"]} d'
                elif x['sit'] == 'avencer':
                    sit_t = f'<font color="{cs}"><b>A vencer</b></font> {_dbr(x["venc"])}'
                else:
                    sit_t = f'<font color="{cs}"><b>Perdido</b></font>'
                if f['org'] == 'servico':
                    serv = escape(s['rot'])
                elif len(x['linhas']) > 1 and f['servlinhas']:
                    serv = '<br/>'.join(f'{escape(n)} <font color="#64748B">{_brl(val)}</font>' for n, val in x['linhas'])
                else:
                    serv = escape(x['linhas'][0][0]) if x['linhas'] else '—'
                grupo = f'<br/><font color="#15803D" size="6.2">{escape(", ".join(x["grupos"]))}</font>' if x['grupos'] and f['org'] != 'grupo' else ''
                tag = ' <font color="#B45309" size="6">AVULSO</font>' if x['tipo_cad'] == 'avulso' else ''
                linhas.append([_p(_dbr(x['data']), 7, TEXTO), _p(f'<b>{escape(x["num"] or "—")}</b>', 7, VERDE_ESC),
                               _p(escape(x['nome']) + tag + grupo, 7, TEXTO, leading=8.6), _p(serv, 6.7, TEXTO, leading=8.4),
                               _p(_comp_rot(x['comp']), 6.8, CINZA), _p(sit_t, 6.8, TEXTO), _p(escape(x['banco']), 6.6, CINZA),
                               _p(f'<b>{_brl(v)}</b>', 7.2, colors.HexColor(cs), align=TA_RIGHT)])
            sub = ['', '', _p(f'<b>Subtotal — {escape(s["rot"])}</b>', 7, VERDE_ESC), '', '', '', '', _p(f'<b>{_brl(s["tot"])}</b>', 7.4, VERDE_ESC, align=TA_RIGHT)]
            t = estilo(Table([cab] + linhas + [sub], colWidths=[L * w / sum(larg) for w in larg], repeatRows=1), len(linhas))
            if len(linhas) <= 10:
                el += [KeepTogether([faixa, Spacer(1, 2), t]), Spacer(1, 6)]
            else:
                el += [faixa, Spacer(1, 2), t, Spacer(1, 6)]
        fim = Table([[_p('<b>Total do relatório</b>', 9.5, VERDE_ESC), _p(f'<b>{_brl(total)}</b>', 11, VERDE_ESC, align=TA_RIGHT)]],
                    colWidths=[L * .6, L * .4])
        fim.setStyle(TableStyle([('LINEABOVE', (0, 0), (-1, 0), 1.4, VERDE), ('TOPPADDING', (0, 0), (-1, -1), 5)]))
        el.append(fim)

    rodape = 'Qualicontax · ' + (ctx.get('titulo') or 'Cobranças') + ' · ' + ctx['gerado_em']
    doc.build(el, canvasmaker=lambda *a, **k: _CanvasNumerado(*a, rodape=rodape, **k))
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# Excel — o "extrato de tudo": uma linha por serviço de cada lançamento
# ══════════════════════════════════════════════════════════════════════════════
def gerar_xlsx(ctx):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    fatos, f = ctx['fatos'], ctx['f']
    wb = Workbook()
    ws = wb.active; ws.title = 'Lançamentos'
    verde = PatternFill('solid', fgColor='0F3D2E'); branco = Font(bold=True, color='FFFFFF')
    ws['A1'] = ctx.get('titulo') or 'Relatório de cobranças'; ws['A1'].font = Font(bold=True, size=14, color='0F3D2E')
    ws['A2'] = 'Gerado em ' + ctx['gerado_em'] + (' por ' + ctx['gerado_por'] if ctx.get('gerado_por') else '')
    ws['A3'] = ' · '.join(ctx['filtros']); ws['A3'].alignment = Alignment(wrap_text=False)
    cab = ['Situação', 'Data', 'Vencimento', 'Pago em', 'Competência', 'Nº', 'Cliente', 'CNPJ/CPF', 'Cadastro', 'Grupo',
           'Serviço', 'Categoria do título', 'Banco', 'Empresa do escritório', 'Dias de atraso', 'Valor', 'Título']
    ws.append([]); ws.append(cab)
    for c in range(1, len(cab) + 1):
        cel = ws.cell(row=5, column=c); cel.fill = verde; cel.font = branco
    for x in sorted(fatos, key=lambda z: (list(SITS).index(z['sit']), z['data'] or date.max, z['nome'])):
        for s, v in x['linhas']:
            ws.append([SITS[x['sit']][0], x['data'], x['venc'], x['data_mov'] if x['sit'] == 'pago' else None,
                       x['comp'].strftime('%m/%Y') if x['comp'] else '', int(x['num']) if x['num'].isdigit() else x['num'],
                       x['nome'], x['doc'], {'cliente': 'Cliente', 'avulso': 'Avulso', 'sem': 'Sem cadastro'}[x['tipo_cad']],
                       ', '.join(x['grupos']), s, x['categoria'], x['banco'], x['empresa'],
                       x.get('dias') if x['sit'] == 'vencido' else None, round(v, 2), x['titulo_id']])
    for r in ws.iter_rows(min_row=6):
        for c in (1, 2, 3):
            r[c].number_format = 'DD/MM/YYYY'
        r[15].number_format = '#,##0.00'
    ws.freeze_panes = 'A6'; ws.auto_filter.ref = f'A5:{get_column_letter(len(cab))}{ws.max_row}'
    for i, w in enumerate([11, 11, 11, 11, 11, 7, 44, 17, 12, 22, 30, 24, 22, 18, 9, 13, 8], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    rs = wb.create_sheet('Resumo')
    rs['A1'] = 'Resumo por ' + ORGANIZAR[f['org']]; rs['A1'].font = Font(bold=True, size=13, color='0F3D2E')
    sits_on = [k for k in SITS if k in f['sit']]
    rs.append([ORGANIZAR[f['org']]] + [SITS[k][0] for k in sits_on] + ['Total', 'Qtd'])
    for c in range(1, len(sits_on) + 4):
        rs.cell(row=2, column=c).fill = verde; rs.cell(row=2, column=c).font = branco
    for s in secoes(fatos, f['org']):
        rs.append([s['rot']] + [round(s['por'][k][1], 2) for k in sits_on] + [round(s['tot'], 2), sum(v[0] for v in s['por'].values())])
    for titulo, campo in (('Por serviço', 'servico'), ('Por banco', 'banco')):
        rs.append([]); rs.append([titulo]); rs.cell(row=rs.max_row, column=1).font = Font(bold=True, color='0F3D2E')
        for rot, n, v in _resumo_por(fatos, campo):
            rs.append([rot, n, round(v, 2)])
    for r in rs.iter_rows(min_row=3):
        for c in r[1:]:
            if isinstance(c.value, float):
                c.number_format = '#,##0.00'
    rs.column_dimensions['A'].width = 42
    for i in range(2, 9):
        rs.column_dimensions[get_column_letter(i)].width = 15
    out = io.BytesIO(); wb.save(out)
    return out.getvalue()
