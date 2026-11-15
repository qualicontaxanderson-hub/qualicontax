# -*- coding: utf-8 -*-
"""A cobrança de um honorário a receber, numa janela na tela Títulos — 15/11/2026.

Anderson: "ao clicar em cima tem que aparecer a cobrança"; "uma tela bonita a qual mostre o que está
envolvido naquele honorário, quais são as cobranças"; "baixar mediante depósito — só será baixado se
for localizado o depósito bancário que já está no nosso sistema, nada de baixar aleatório"; "tudo numa
tela só sem precisar ficar rolando". A janela mostra o cliente (com a régua dos últimos 12 meses), o
honorário, a LINHA DO TEMPO da cobrança (lançado, boleto emitido, e-mail, cliente abriu, venceu, pago),
o boleto mais novo e os outros honorários em aberto. Só leitura aqui: toda gravação continua nas rotas
de sempre (vincular pelo extrato, boleto, perdido), que voltam para esta tela com a janela aberta
(#cob=<id>).
"""
import json
from datetime import date, datetime, time

from flask import abort, render_template, request, url_for

from models.fin_empresa import FinEmpresa
from routes.financeiro import financeiro
from utils import titulos_receber as TR
from utils.auth_helper import permission_required
from utils.db_helper import execute_query
from utils.integrations.efi.client import SITUACOES


def _dt(v):
    if v is None:
        return None
    return v if isinstance(v, datetime) else datetime.combine(v, time.min)


def _quando(v):
    d = _dt(v)
    if d.time() == time.min:
        return d.strftime('%d/%m/%y')
    return d.strftime('%d/%m/%y %H:%M')


@financeiro.route('/financeiro/titulos/<int:titulo_id>/cobranca')
@permission_required('financeiro.titulos')
def titulo_cobranca(titulo_id):
    t = execute_query(
        """SELECT t.*, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj, c.email, c.celular, c.telefone,
                  cat.nome AS categoria_nome
             FROM fin_titulos t
             LEFT JOIN clientes c ON c.id = t.cliente_id
             LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
            WHERE t.id = %s AND t.tipo = 'R'""", (titulo_id,), fetch=True, fetch_one=True)
    if not t or t['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    hoje = TR.hoje_br()
    aberto = t['status'] in ('aberto', 'parcial')
    saldo = float(t['valor'] or 0) - float(t['valor_baixado'] or 0)

    boletos = execute_query(
        """SELECT b.*, cc.apelido AS conta_apelido
             FROM fin_boletos b JOIN fin_cobranca_contas cc ON cc.id = b.conta_cobranca_id
            WHERE b.titulo_id = %s ORDER BY b.id DESC""", (titulo_id,), fetch=True) or []
    vivos = [b for b in boletos if b['situacao'] not in ('erro', 'canceled', 'rascunho')]
    boleto = (vivos or boletos or [None])[0]
    if boleto and isinstance(boleto.get('itens_json'), str):
        try:
            boleto['itens_json'] = json.loads(boleto['itens_json'])
        except ValueError:
            boleto['itens_json'] = []

    nota = None
    nfse_id = t.get('nfse_id') or next((b['nfse_id'] for b in boletos if b.get('nfse_id')), None)
    if nfse_id:
        nota = execute_query('SELECT id, numero_nfse, numero_dps, situacao, dh_emissao FROM nfse_emitidas WHERE id = %s',
                             (nfse_id,), fetch=True, fetch_one=True)

    from routes.financeiro_vincular import pagamentos_do_titulo
    pagtos = pagamentos_do_titulo(titulo_id)

    # ── o mesmo cliente: outros em aberto e a régua dos últimos 12 meses ──
    cid = t['cliente_id']
    doc = TR._digitos(t['contraparte_doc'])
    cond, par = ('t.cliente_id = %s', [cid]) if cid else ('t.contraparte_doc = %s', [t['contraparte_doc'] or ''])
    outros = execute_query(
        f"""SELECT t.id, t.descricao, t.competencia, t.vencimento, t.valor - t.valor_baixado AS v, t.status,
                   cat.nome AS categoria_nome
              FROM fin_titulos t LEFT JOIN fin_categorias cat ON cat.id = t.categoria_id
             WHERE t.tipo = 'R' AND t.status IN ('aberto', 'parcial') AND {cond} AND t.id <> %s
             ORDER BY t.vencimento""", (*par, titulo_id), fetch=True) or []
    bols = TR._boletos_dos_titulos([o['id'] for o in outros])
    for o in outros:
        o['tag'], o['cls'] = TR._status(o['vencimento'], hoje)
        o['bol'] = bols.get(o['id'], 'sem')
        o['valor'] = TR.brl(o['v'])
        o['desc'] = TR._desc(o)
    cli_tot = sum(float(o['v']) for o in outros) + (saldo if aberto else 0)
    cli_n = len(outros) + (1 if aberto else 0)

    comps = []
    y, m = hoje.year, hoje.month + 1
    for _ in range(12):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        comps.insert(0, (y, m))
    mapa = {}
    for r in execute_query(f"""SELECT t.competencia, t.status FROM fin_titulos t
                                WHERE t.tipo = 'R' AND {cond} AND t.competencia >= %s""",
                           (*par, date(*comps[0], 1)), fetch=True) or []:
        if not r['competencia']:
            continue
        k = (r['competencia'].year, r['competencia'].month)
        if r['status'] in ('aberto', 'parcial'):
            mapa[k] = 'v'
        elif r['status'] == 'liquidado' and mapa.get(k) != 'v':
            mapa[k] = 'p'
    regua = [(mapa.get(c, ''), f'{TR.MES[c[1]]}/{c[0]}') for c in comps]

    # ── linha do tempo da cobrança ──
    ev = []

    def add(dt, cor, ico, txt, sub=''):
        if dt:
            ev.append({'dt': _dt(dt), 'q': _quando(dt), 'cor': cor, 'ico': ico, 'txt': txt, 'sub': sub,
                       'fut': _dt(dt).date() > hoje})

    origem = {'lote': 'pelo lote mensal', 'manual': 'à mão', 'importacao': 'importado', 'programacao': 'pela programação',
              'contrato': 'pelo contrato'}.get((t.get('origem') or '').lower(), t.get('origem') or '')
    add(t.get('criado_em') or t.get('emissao'), 'cinza', 'fa-file-circle-plus', 'Honorário lançado', origem)
    for b in reversed(boletos):
        num = f"nº {b['charge_id']}" if b.get('charge_id') else f"#{b['id']}"
        if b['situacao'] == 'erro':
            add(b.get('criado_em'), 'verm', 'fa-triangle-exclamation', 'Boleto recusado pelo banco', (b.get('erro') or '')[:80])
            continue
        add(b.get('criado_em'), 'azul', 'fa-barcode', f'Boleto emitido {num}',
            f"{TR.brl(b['valor'])} · vence {b['vencimento'].strftime('%d/%m/%Y')}")
        add(b.get('email_enviado_em'), 'azul', 'fa-envelope', 'E-mail enviado ao cliente', b.get('email_enviado_para') or '')
        add(b.get('email_visto_em'), 'cinza', 'fa-eye', 'E-mail visto', 'a imagem do e-mail carregou')
        add(b.get('abriu_boleto_em'), 'verde', 'fa-file-pdf', 'Cliente abriu o boleto', '')
        if b['situacao'] == 'paid':
            add(b.get('pago_em'), 'verde', 'fa-circle-check', 'Boleto pago', TR.brl(b.get('valor_pago') or b['valor']))
        elif b['situacao'] == 'expired':
            add(b['vencimento'], 'laranja', 'fa-hourglass-end', 'Boleto expirou sem pagamento', num)
        elif b['situacao'] == 'unpaid':
            add(b['vencimento'], 'laranja', 'fa-clock', 'Boleto venceu', 'ainda pode ser pago')
        elif b['situacao'] == 'canceled':
            add(b.get('atualizado_em'), 'verm', 'fa-ban', 'Boleto cancelado', num)
    for p in pagtos:
        add(p['data_baixa'], 'verde', 'fa-building-columns', 'Baixa de ' + TR.brl(p['valor']), p.get('onde') or '')
    if t.get('perdido_em'):
        add(t['perdido_em'], 'verm', 'fa-user-slash', 'Marcado como perdido', t.get('perdido_motivo') or '')
    if aberto and t.get('vencimento'):
        d = (hoje - t['vencimento']).days
        add(t['vencimento'], 'laranja' if d > 0 else 'azul', 'fa-calendar-day',
            'Venceu há %d dia%s' % (d, '' if d == 1 else 's') if d > 0 else ('Vence hoje' if d == 0 else 'Vence em %d dia%s' % (-d, '' if d == -1 else 's')),
            'sem pagamento até agora' if d > 0 else '')
    ev.sort(key=lambda e: e['dt'])

    if aberto:
        tag, cls = TR._status(t['vencimento'], hoje)
    elif t['status'] == 'liquidado':
        tag, cls = 'pago', 'tx-ok'
    elif t.get('perdido_em'):
        tag, cls = 'perdido', 'tx-perd'
    else:
        tag, cls = 'cancelado', 'tx-perd'

    aba = request.args.get('aba') if request.args.get('aba') in ('rec', 'abe', 'atr', 'ina', 'perd') else 'abe'
    emp = request.args.get('emp') or None
    volta = url_for('financeiro.titulos', aba=aba, emp=emp) + f'#cob={titulo_id}'
    cel = TR._cel(t.get('celular'))
    nome = TR._nome(t['contraparte_nome'])
    return render_template(
        'financeiro/_titulo_cobranca.html', t=t, aberto=aberto, saldo=saldo, saldo_txt=TR.brl(saldo), valor_txt=TR.brl(t['valor']),
        tag=tag, cls=cls, desc=TR._desc(t), servico=TR._servico(t), comp=TR._comp(t.get('competencia')),
        grupos=TR._grupos_dos_clientes([cid]).get(cid, []) if cid else [], doc=doc, nome=nome,
        iniciais=''.join(p[0] for p in nome.split()[:2]).upper(),
        boleto=boleto, boletos=boletos, nota=nota, outros=outros, outros_tot=TR.brl(sum(float(o['v']) for o in outros)),
        cli_n=cli_n, cli_tot=TR.brl(cli_tot), regua=regua, eventos=ev, pagtos=pagtos,
        BOLETO_ROTULO=TR.BOLETO_ROTULO, SIT_BOLETO=SITUACOES, volta=volta, email_padrao=t.get('email') or '',
        cel=cel, msg=TR._msg_cobranca([t], saldo) if cel else '',
        cancelavel=t['status'] == 'aberto' and not t['valor_baixado'], hoje=hoje, hoje_iso=hoje.isoformat())
