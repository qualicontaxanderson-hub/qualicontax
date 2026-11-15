# -*- coding: utf-8 -*-
"""A cobrança de um título a receber, numa gaveta na tela Títulos — 15/11/2026.

Anderson: "ao clicar em cima tem que aparecer a cobrança". Clicar em qualquer cartão de
Títulos abre, do lado, o cliente, o título, o boleto (o MESMO cartão da aba Cobranças, com
PDF, linha digitável, Pix, e-mail, vencimento, "o cliente abriu?") e os outros títulos em
aberto do mesmo cliente. Só leitura aqui: toda gravação continua nas rotas de sempre
(baixa, boleto, perdido), que voltam para esta tela com a gaveta aberta (#cob=<id>).
"""
import json

from flask import abort, render_template, request, url_for
from flask_login import current_user

from models.fin_empresa import FinEmpresa
from routes.financeiro import financeiro
from utils import titulos_receber as TR
from utils.auth_helper import permission_required
from utils.db_helper import execute_query
from utils.integrations.efi.client import SITUACOES


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

    if t['status'] in ('aberto', 'parcial'):
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
    return render_template(
        'financeiro/_titulo_cobranca.html', t=t, saldo=saldo, saldo_txt=TR.brl(saldo), valor_txt=TR.brl(t['valor']),
        tag=tag, cls=cls, desc=TR._desc(t), servico=TR._servico(t), comp=TR._comp(t.get('competencia')),
        grupos=TR._grupos_dos_clientes([cid]).get(cid, []) if cid else [], doc=doc,
        nome=TR._nome(t['contraparte_nome']), boleto=boleto, boletos_n=len(boletos), outros=outros,
        outros_tot=TR.brl(sum(float(o['v']) for o in outros)), BOLETO_ROTULO=TR.BOLETO_ROTULO,
        SIT_BOLETO=SITUACOES, volta=volta, email_padrao=t.get('email') or '',
        cel=cel, msg=TR._msg_cobranca([t], saldo) if cel else '',
        pode_baixar=current_user.has_permission('financeiro.titulos.alterar'),
        pode_perd=current_user.has_permission('financeiro.titulos.excluir'),
        cancelavel=t['status'] == 'aberto' and not t['valor_baixado'],
        prog='v' if t.get('programacao_id') and _variavel(t['programacao_id']) else ('f' if t.get('programacao_id') else ''),
        hoje=hoje)


def _variavel(programacao_id):
    r = execute_query('SELECT variavel FROM fin_programacoes WHERE id = %s', (programacao_id,), fetch=True, fetch_one=True)
    return bool(r and r.get('variavel'))
