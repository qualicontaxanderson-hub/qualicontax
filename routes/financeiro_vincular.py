"""Vincular pagamento que caiu em outra conta (Bradesco, Pix, transferência) aos títulos do cliente — 14/11/2026.

Anderson: "quando o cliente pagar no Bradesco ou em outra conta temos que ter o poder de vincular aí já baixa o
boleto e na cobrança ter essa informação"; "se o cliente devia 2040,00 mas pagou 2.000,00 ... a) dar a quitação
com desconto dos 40,00; b) fazer uma cobrança dos 40,00 e ou colocar para cobrar o mês seguinte ... Diferença de
Honorários ou do serviço, resíduo de tal cobrança". Decisões: só quem tem "vincular pagamento" dá desconto; o
mês da diferença é escolhido.

Um gesto, a partir de UMA linha de crédito do extrato:
  * baixa nos títulos marcados (o mais antigo primeiro), amarrada à linha — a mesma baixa 'extrato' de sempre,
    pelo registrar_baixa (único escritor);
  * pagou MENOS: desconto (com motivo, no título) ou a diferença vira um título próprio. O título original é
    DIVIDIDO (fica com o que foi pago; a diferença sai para o título novo, mesma categoria e competência) —
    assim o DRE do mês continua somando o valor cheio, sem contar a diferença duas vezes;
  * pagou MAIS: a sobra entra como juros no último título;
  * o boleto que ainda estava valendo é tirado do banco (Efí: marcado como pago), para o cliente não pagar
    de novo, e a tela do boleto passa a mostrar onde foi pago.
"""
import calendar
import logging
import re
from datetime import date
from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from models.fin_cobranca import FinBoleto, FinCobrancaConta
from models.fin_empresa import FinEmpresa
from routes.financeiro import financeiro
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query
from utils.integrations.efi.client import EfiErro

logger = logging.getLogger(__name__)
PERM = 'financeiro.cobranca.vincular'
CENTAVO = Decimal('0.01')
MESES_PT = ('', 'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
            'outubro', 'novembro', 'dezembro')


def _q(sql, params=(), one=False):
    r = execute_query(sql, params, fetch=True, fetch_one=one)
    return r if one else (r or [])


def _d(v):
    return Decimal(str(v or 0)).quantize(CENTAVO)


def _brl(v):
    s = f'{_d(v):,.2f}'
    return 'R$ ' + s.replace(',', '#').replace('.', ',').replace('#', '.')


def _hoje():
    from routes.financeiro_cobranca import _hoje as h
    return h()


def _linha(lanc_id):
    from models.extrato_lancamento import ExtratoLancamento
    l = ExtratoLancamento.get(lanc_id)
    if not l or l.get('empresa_id') not in FinEmpresa.ids_validos():
        abort(404)
    return l


def baixas_da_linha(lanc_id):
    return _q("""SELECT b.*, t.contraparte_nome, t.descricao, t.competencia, t.vencimento AS titulo_vencimento
                   FROM fin_titulo_baixas b JOIN fin_titulos t ON t.id = b.titulo_id
                  WHERE b.lancamento_id = %s ORDER BY t.vencimento, t.id""", (lanc_id,))


def _abertos(empresa_id, ids=None):
    cond, params = '', [empresa_id]
    if ids is not None:
        if not ids:
            return []
        cond = f"AND t.id IN ({','.join(['%s'] * len(ids))})"
        params += list(ids)
    return _q(f"""SELECT t.id, t.cliente_id, t.contraparte_nome, t.contraparte_doc, t.descricao, t.competencia,
                         t.vencimento, t.valor, t.valor_baixado, t.categoria_id, t.centro_custo_id, t.status,
                         t.observacao,
                         (SELECT COUNT(*) FROM fin_boletos b WHERE b.titulo_id = t.id
                             AND b.situacao IN ('waiting', 'unpaid')) AS boletos_valendo
                    FROM fin_titulos t
                   WHERE t.empresa_id = %s AND t.tipo = 'R' AND t.status IN ('aberto', 'parcial')
                     AND t.valor - t.valor_baixado > 0 {cond}
                   ORDER BY t.contraparte_nome, t.vencimento, t.id""", tuple(params))


def baixas_soltas_perto(lanc):
    """Baixa dada À MÃO, sem linha do extrato, na mesma empresa e perto da data: pode ser este mesmo dinheiro
    (Central do Ar: o Pix de R$ 800 de 21/09 quitou julho à mão). Vincular de novo contaria duas vezes."""
    return _q("""SELECT b.id, b.titulo_id, b.data_baixa, b.valor, b.referencia, t.contraparte_nome, t.descricao
                   FROM fin_titulo_baixas b JOIN fin_titulos t ON t.id = b.titulo_id
                  WHERE t.empresa_id = %s AND t.tipo = 'R' AND b.lancamento_id IS NULL AND b.origem = 'manual'
                    AND b.data_baixa BETWEEN %s - INTERVAL 5 DAY AND %s + INTERVAL 5 DAY AND b.valor <= %s + 0.005
                    AND (b.referencia IS NULL OR (b.referencia NOT LIKE 'efi-settle:%%' AND b.referencia NOT LIKE 'efi-app:%%'))
                  ORDER BY ABS(DATEDIFF(b.data_baixa, %s)), b.id""",
              (lanc['empresa_id'], lanc['data'], lanc['data'], lanc['valor'], lanc['data']))


def _quem_pagou(lanc):
    """Nome para filtrar os títulos: o que o banco escreveu, sem o "PIX RECEBIDO REM:" e sem a data do fim."""
    from models.extrato_lancamento import ExtratoLancamento
    lido = ExtratoLancamento.ler_descricao(lanc.get('descricao')) or {}
    if lido.get('nome'):
        return lido['nome'], lido.get('doc') or ''
    d = str(lanc.get('descricao') or '')
    d = re.sub(r'^.*?\b(REM|DE|ORIGEM|PAGADOR)\s*:\s*', '', d, flags=re.I)
    d = re.sub(r'^(PIX|TED|DOC|TRANSF\w*)\s+(RECEBID[OA]|CREDITO)?\s*', '', d, flags=re.I)
    d = re.sub(r'\s+\d{2}/\d{2}(/\d{2,4})?\s*$', '', d).strip()
    m = re.search(r'Recebimento de cobrança: \d+ de (.+)$', d)
    return (m.group(1) if m else d)[:60], lido.get('doc') or ''


def _proximos_meses(n=7):
    h = _hoje().replace(day=1)
    out = []
    for i in range(n):
        a, m = h.year + (h.month - 1 + i) // 12, (h.month - 1 + i) % 12 + 1
        out.append((f'{a}-{m:02d}', f'{MESES_PT[m]} de {a}'))
    return out


def _dia_no_mes(ano, mes, dia):
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def _descricao_residuo(t):
    base = re.sub(r'\s*\(Conexa \d+\)\s*$', '', t['descricao'] or '').strip()
    tipo = 'honorários' if 'honor' in base.lower() else 'serviço'
    comp = t['competencia'].strftime('%m/%Y') if t.get('competencia') else ''
    return (f'Diferença de {tipo} — resíduo de {base}' + (f' ({comp})' if comp and comp not in base else ''))[:255]


def _anotar(titulo_id, texto):
    execute_query("UPDATE fin_titulos SET observacao = CONCAT_WS('\n', NULLIF(observacao, ''), %s) WHERE id = %s",
                  (texto[:1000], titulo_id))


def tirar_boletos_do_banco(titulo_id):
    """Boleto que ainda podia ser pago: marca como pago na Efí (o cliente não paga de novo). 'settled' é gravado
    ANTES, para o aviso da Efí que chega em seguida achar tudo igual e não baixar outra vez. Devolve os avisos."""
    avisos = []
    for r in _q("SELECT id FROM fin_boletos WHERE titulo_id = %s AND situacao IN ('waiting', 'unpaid')", (titulo_id,)):
        b = FinBoleto.get(r['id'])
        if not b:
            continue
        if b['provedor'] != 'efi':
            avisos.append(f"O boleto {FinBoleto.numero(b)} é do {b['provedor'].capitalize()}: cancele lá para o cliente não pagar de novo.")
            continue
        FinBoleto.atualizar_situacao(b['id'], 'settled')
        try:
            efi, _ = FinCobrancaConta.cliente_efi(b['conta_cobranca_id'])
            efi.marcar_pago(b['charge_id'])
            registrar('escrita.marcou_boleto_pago_efi', 'financeiro', tabela='fin_boletos', registro_id=b['id'],
                      depois={'charge_id': b['charge_id'], 'titulo_id': titulo_id, 'como': 'pagamento vinculado do extrato'})
        except (EfiErro, ValueError) as exc:
            FinBoleto.atualizar_situacao(b['id'], b['situacao'])
            avisos.append(f"A Efí não marcou o boleto {b['charge_id']} como pago ({exc}). Use \"Marcar como pago na Efí\" na tela do boleto.")
        except Exception as exc:                  # rede
            logger.exception('[vincular] marcar pago %s', b['charge_id'])
            FinBoleto.atualizar_situacao(b['id'], b['situacao'])
            avisos.append(f"A Efí não respondeu para o boleto {b['charge_id']} ({exc}). Use \"Marcar como pago na Efí\" na tela do boleto.")
    return avisos


# ---------------------------------------------------------------------------
# Na tela do boleto: "Como foi pago"
# ---------------------------------------------------------------------------
@financeiro.app_template_global('pagamentos_do_titulo')
def pagamentos_do_titulo(titulo_id):
    """As baixas do título com o que se sabe de cada uma: banco e data da linha do extrato, quem vinculou."""
    if not titulo_id:
        return []
    rows = _q("""SELECT b.id, b.data_baixa, b.valor, b.juros, b.multa, b.desconto, b.origem, b.referencia,
                        b.lancamento_id, l.banco, l.data AS data_extrato, l.valor AS valor_linha, l.descricao AS desc_linha,
                        u.nome AS quem
                   FROM fin_titulo_baixas b
                   LEFT JOIN extrato_lancamentos l ON l.id = b.lancamento_id
                   LEFT JOIN usuarios u ON u.id = b.usuario_id
                  WHERE b.titulo_id = %s ORDER BY b.data_baixa, b.id""", (titulo_id,))
    for r in rows:
        ref = r.get('referencia') or ''
        if r['lancamento_id']:
            r['onde'] = f"{r.get('banco') or 'banco'} (extrato)"
        elif ref.startswith('efi:'):
            r['onde'] = 'boleto pago (Efí)'
        elif ref.startswith('efi-app:'):
            r['onde'] = 'marcado como pago no app da Efí'
        elif ref.startswith('efi-settle:'):
            r['onde'] = 'marcado como pago por aqui (pago por fora)'
        elif ref.startswith('pix-bradesco:'):
            r['onde'] = 'Pix no Bradesco (baixa manual)'
        elif r['origem'] == 'manual':
            r['onde'] = 'baixa manual'
        else:
            r['onde'] = r['origem']
    return rows


@financeiro.app_template_global('titulo_em_aberto')
def titulo_em_aberto(titulo_id):
    return bool(titulo_id and _q("SELECT 1 x FROM fin_titulos WHERE id = %s AND status IN ('aberto', 'parcial')",
                                 (titulo_id,), one=True))


@financeiro.app_template_global('linhas_com_baixa')
def linhas_com_baixa(lancamentos):
    """Ids das linhas do extrato (da lista na tela) que já estão amarradas a algum título — uma consulta só."""
    ids = [l['id'] for l in (lancamentos or []) if float(l.get('valor') or 0) > 0]
    if not ids:
        return set()
    rows = _q(f"SELECT DISTINCT lancamento_id FROM fin_titulo_baixas WHERE lancamento_id IN ({','.join(['%s'] * len(ids))})",
              tuple(ids))
    return {r['lancamento_id'] for r in rows}


@financeiro.app_template_global('titulos_pagos_em_dobro')
def titulos_pagos_em_dobro(grupos):
    """{titulo_id: 'R$ x,xx' a mais} dos títulos da aba Recebidos cujo boleto foi pago mais de uma vez."""
    ids = [t['id'] for g in (grupos or []) for t in (g.get('itens') or []) if t.get('id')]
    if not ids:
        return {}
    try:
        rs = _q(f"""SELECT titulo_id, SUM(valor) v FROM fin_pagamentos_duplicados
                     WHERE titulo_id IN ({','.join(['%s'] * len(ids))}) GROUP BY titulo_id""", tuple(ids))
    except Exception:
        return {}
    return {r['titulo_id']: 'R$ ' + ('{:,.2f}'.format(float(r['v'])).replace(',', 'X').replace('.', ',').replace('X', '.')) for r in rs}


@financeiro.app_template_global('recebimentos_das_linhas')
def recebimentos_das_linhas(lancamentos):
    """25/11/2026 — Anderson: o crédito do boleto pago "tem que ligar automaticamente" e o pagamento repetido tem
    de aparecer como "pago duplicado". Devolve {lanc_id: {'tipo': 'baixa'|'dobro', 'titulo_id', 'nome', 'texto'}}
    para os créditos da tela, em duas consultas."""
    ids = [l['id'] for l in (lancamentos or []) if float(l.get('valor') or 0) > 0]
    if not ids:
        return {}
    marks = ','.join(['%s'] * len(ids))
    out = {}
    for r in _q(f"""SELECT x.lancamento_id, x.titulo_id, t.contraparte_nome AS nome, t.descricao, t.vencimento
                      FROM fin_titulo_baixas x JOIN fin_titulos t ON t.id = x.titulo_id
                     WHERE x.lancamento_id IN ({marks}) ORDER BY x.id""", tuple(ids)):
        if r['lancamento_id'] not in out:
            out[r['lancamento_id']] = {'tipo': 'baixa', 'titulo_id': r['titulo_id'], 'nome': r['nome'],
                                       'texto': r['descricao'] or '', 'venc': r['vencimento']}
    try:
        dups = _q(f"""SELECT d.lancamento_id, d.titulo_id, d.situacao, t.contraparte_nome AS nome, t.descricao, t.vencimento
                        FROM fin_pagamentos_duplicados d LEFT JOIN fin_titulos t ON t.id = d.titulo_id
                       WHERE d.lancamento_id IN ({marks})""", tuple(ids))
    except Exception:                       # tabela ainda não criada (antes da migração)
        dups = []
    for r in dups:
        out[r['lancamento_id']] = {'tipo': 'dobro', 'titulo_id': r['titulo_id'], 'nome': r['nome'], 'situacao': r['situacao'],
                                   'texto': r['descricao'] or '', 'venc': r['vencimento']}
    return out


# ---------------------------------------------------------------------------
# A partir do título/boleto: achar o pagamento no extrato
# ---------------------------------------------------------------------------
@financeiro.route('/financeiro/vincular/achar/<int:titulo_id>')
@permission_required(PERM)
def vincular_achar(titulo_id):
    t = _q('SELECT * FROM fin_titulos WHERE id = %s', (titulo_id,), one=True)
    if not t or t['empresa_id'] not in FinEmpresa.ids_validos():
        abort(404)
    saldo = _d(t['valor']) - _d(t['valor_baixado'])
    nome = (t['contraparte_nome'] or '').upper()
    palavras = [p for p in re.findall(r'[A-Z0-9]{4,}', nome) if p not in ('LTDA', 'EIRELI', 'SERVICOS', 'COMERCIO')][:2]
    linhas = _q("""SELECT l.* FROM extrato_lancamentos l
                    WHERE l.empresa_id = %s AND l.valor > 0 AND l.data >= %s - INTERVAL 30 DAY
                      AND NOT EXISTS (SELECT 1 FROM fin_titulo_baixas b WHERE b.lancamento_id = l.id)
                      AND COALESCE(l.par_estado, '') NOT IN ('casado', 'suspeito', 'aprovado')
                    ORDER BY l.data DESC LIMIT 1500""", (t['empresa_id'], t['vencimento'] or _hoje()))

    def nota(l):
        desc = (l['descricao'] or '').upper()
        bate_nome = sum(1 for p in palavras if p in desc)
        bate_doc = 1 if t.get('contraparte_doc') and t['contraparte_doc'] in re.sub(r'\D', '', desc) else 0
        bate_valor = 1 if abs(_d(l['valor']) - saldo) <= Decimal('0.05') else 0
        perto = 1 if abs(_d(l['valor']) - saldo) <= saldo * Decimal('0.1') else 0
        return bate_doc * 4 + bate_nome * 2 + bate_valor * 2 + perto

    cands = [(nota(l), l) for l in linhas]
    cands = [l for n, l in sorted(cands, key=lambda x: (-x[0], -x[1]['data'].toordinal())) if n >= 2][:30]
    return render_template('financeiro/vincular.html', modo='achar', titulo=t, saldo=saldo, candidatos=cands,
                           brl=_brl)


# ---------------------------------------------------------------------------
# A tela do vínculo (a partir de UMA linha do extrato)
# ---------------------------------------------------------------------------
@financeiro.route('/financeiro/vincular/<int:lanc_id>')
@permission_required(PERM)
def vincular_pagamento(lanc_id):
    lanc = _linha(lanc_id)
    if float(lanc['valor'] or 0) <= 0:
        flash('Só crédito (dinheiro que entrou) é vinculado a título a receber.', 'warning')
        return redirect(url_for('financeiro.extrato'))
    feitas = baixas_da_linha(lanc_id)
    titulos = [] if feitas else _abertos(lanc['empresa_id'])
    for t in titulos:
        t['saldo'] = _d(t['valor']) - _d(t['valor_baixado'])
    nome, doc = _quem_pagou(lanc)
    pre = request.args.get('titulo', type=int)
    if pre:
        alvo = next((t for t in titulos if t['id'] == pre), None)
        if alvo:
            nome = alvo['contraparte_nome']
    elif doc and any(t['contraparte_doc'] == doc for t in titulos):
        nome = next(t['contraparte_nome'] for t in titulos if t['contraparte_doc'] == doc)
    t0 = _hoje()
    soltas = [] if feitas else baixas_soltas_perto(lanc)
    palavras = [w for w in re.findall(r'[A-Z0-9]{4,}', (nome or '').upper()) if w not in ('LTDA', 'PIX', 'RECEBIDO')]
    for b in soltas:
        b['mesmo_nome'] = bool(palavras) and all(w in (b['contraparte_nome'] or '').upper() for w in palavras[:2])
    soltas = [b for b in soltas if b['mesmo_nome']] or []
    return render_template('financeiro/vincular.html', modo='linha', lanc=lanc, feitas=feitas, titulos=titulos, soltas=soltas,
                           sugestao=nome, pre=pre, meses=_proximos_meses(), hoje=t0, brl=_brl,
                           empresa_nome=FinEmpresa.mapa().get(lanc['empresa_id'], ''))


@financeiro.route('/financeiro/vincular/<int:lanc_id>', methods=['POST'])
@permission_required(PERM)
def vincular_gravar(lanc_id):
    from models.fin_titulo import FinTitulo
    from utils.financeiro_core import registrar_baixa, BaixaInvalida
    lanc = _linha(lanc_id)
    volta = url_for('financeiro.vincular_pagamento', lanc_id=lanc_id)
    pago = _d(lanc['valor'])
    if pago <= 0:
        abort(400)
    if baixas_da_linha(lanc_id):
        flash('Este pagamento já está vinculado.', 'warning')
        return redirect(volta)

    ids = [int(x) for x in request.form.getlist('titulo') if str(x).isdigit()]
    ts = _abertos(lanc['empresa_id'], ids)
    if not ts or len(ts) != len(set(ids)):
        flash('Marque o(s) título(s) deste pagamento — algum deles já não está em aberto.', 'danger')
        return redirect(volta)
    ts.sort(key=lambda t: (t['vencimento'] or date.max, t['id']))
    for t in ts:
        t['saldo'] = _d(t['valor']) - _d(t['valor_baixado'])
    soma = sum(t['saldo'] for t in ts)
    antes_do_ultimo = soma - ts[-1]['saldo']
    if len(ts) > 1 and pago <= antes_do_ultimo:
        flash(f'Com {_brl(pago)} não chega a pagar o último título marcado ({_brl(antes_do_ultimo)} já vão nos anteriores). '
              'Desmarque o último.', 'danger')
        return redirect(volta)
    dif = soma - pago                                   # > 0 pagou menos; < 0 pagou mais
    acao = (request.form.get('diferenca') or '').strip()
    motivo = (request.form.get('motivo') or '').strip()[:200]
    venc_residuo = None
    if dif > 0:
        if acao not in ('desconto', 'cobrar', 'mes'):
            flash(f'Faltam {_brl(dif)}: escolha o que fazer com a diferença.', 'danger')
            return redirect(volta)
        if acao == 'desconto' and len(motivo) < 3:
            flash('Para dar desconto, escreva o motivo.', 'danger')
            return redirect(volta)
        if acao == 'cobrar':
            try:
                venc_residuo = date.fromisoformat(request.form.get('vencimento') or '')
            except ValueError:
                venc_residuo = None
            if not venc_residuo or venc_residuo < _hoje():
                flash('Informe o vencimento da cobrança da diferença (hoje ou depois).', 'danger')
                return redirect(volta)
        if acao == 'mes':
            m = re.fullmatch(r'(\d{4})-(\d{2})', request.form.get('mes') or '')
            if not m or (int(m.group(1)), int(m.group(2))) < (_hoje().year, _hoje().month):
                flash('Escolha o mês em que a diferença vai ser cobrada.', 'danger')
                return redirect(volta)
            dia = (ts[-1]['vencimento'] or date(2000, 1, 10)).day
            venc_residuo = _dia_no_mes(int(m.group(1)), int(m.group(2)), dia)
    elif dif < 0 and acao != 'juros':
        flash(f'Sobraram {_brl(-dif)}: confirme que entram como juros, ou marque mais um título do cliente.', 'danger')
        return redirect(volta)

    ref = lanc.get('hash_dedup') or f'extrato:{lanc_id}'
    quem = getattr(current_user, 'nome', None) or getattr(current_user, 'username', '') or f'usuário {current_user.id}'
    quando_txt = lanc['data'].strftime('%d/%m/%Y')
    feito, residuo_id, avisos = [], None, []
    try:
        for i, t in enumerate(ts):
            ultimo = i == len(ts) - 1
            valor, juros, desconto = t['saldo'], Decimal('0'), Decimal('0')
            if ultimo and dif < 0:
                juros = -dif
            elif ultimo and dif > 0:
                valor = t['saldo'] - dif
                if acao == 'desconto':
                    desconto = dif
                    _anotar(t['id'], f'{_hoje().strftime("%d/%m/%Y")} — desconto de {_brl(dif)} dado por {quem} '
                                     f'ao vincular o pagamento de {_brl(pago)} de {quando_txt}: {motivo}')
                else:
                    chave = f'residuo:{t["id"]}:{lanc_id}'
                    ja = _q('SELECT id FROM fin_titulos WHERE chave_idem = %s', (chave,), one=True)
                    residuo_id = ja['id'] if ja else FinTitulo.criar(
                        tipo='R', contraparte_nome=t['contraparte_nome'], categoria_id=t['categoria_id'],
                        descricao=_descricao_residuo(t), competencia=t['competencia'], emissao=_hoje(),
                        vencimento=venc_residuo, valor=dif, empresa_id=lanc['empresa_id'],
                        contraparte_doc=t['contraparte_doc'], cliente_id=t['cliente_id'],
                        observacao=(f'Resíduo do título {t["id"]} ({_brl(t["valor"])}): o cliente pagou {_brl(pago)} '
                                    f'em {quando_txt}; vinculado por {quem}' + (f' — {motivo}' if motivo else '')),
                        origem='residuo', chave_idem=chave, centro_custo_id=t['centro_custo_id'])
                    if not residuo_id or residuo_id is True:
                        raise BaixaInvalida('não consegui criar o título da diferença')
                    if not ja:
                        execute_query('UPDATE fin_titulos SET valor = valor - %s WHERE id = %s', (dif, t['id']))
                        _anotar(t['id'], f'{_hoje().strftime("%d/%m/%Y")} — valor era {_brl(t["valor"])}; a diferença de '
                                         f'{_brl(dif)} foi para o título {residuo_id} (vence {venc_residuo.strftime("%d/%m/%Y")}), '
                                         f'por {quem}')
            r = registrar_baixa(t['id'], valor, lanc['data'], 'extrato', referencia=ref, juros=juros,
                                desconto=desconto, lancamento_id=lanc_id, usuario_id=current_user.id)
            feito.append({'titulo_id': t['id'], 'valor': str(valor), 'juros': str(juros), 'desconto': str(desconto),
                          'status': r.get('status')})
            avisos += tirar_boletos_do_banco(t['id'])
    except BaixaInvalida as exc:
        flash(f'Parou no meio: {exc}. O que já foi feito: {len(feito)} título(s). Confira antes de tentar de novo.', 'danger')
        logger.warning('[vincular] linha %s: %s (feito=%s)', lanc_id, exc, feito)
        return redirect(volta)

    registrar('escrita.vinculou_pagamento', 'financeiro', tabela='extrato_lancamentos', registro_id=lanc_id,
              depois={'valor_pago': str(pago), 'titulos': feito, 'diferenca': str(dif), 'acao': acao or None,
                      'motivo': motivo or None, 'residuo_titulo_id': residuo_id,
                      'residuo_vencimento': str(venc_residuo) if venc_residuo else None})

    msg = f'Pagamento de {_brl(pago)} vinculado a {len(ts)} título(s).'
    if dif > 0 and acao == 'desconto':
        msg += f' Desconto de {_brl(dif)}.'
    elif dif > 0:
        msg += f' A diferença de {_brl(dif)} virou o título {residuo_id}, vence {venc_residuo.strftime("%d/%m/%Y")}.'
    elif dif < 0:
        msg += f' {_brl(-dif)} entraram como juros.'
    flash(msg, 'success')

    if residuo_id and acao == 'cobrar' and request.form.get('boleto') == '1':
        ok, aviso = _boleto_do_residuo(residuo_id, venc_residuo)
        flash(aviso, 'success' if ok else 'warning')
    for a in avisos:
        flash(a, 'warning')
    return redirect(volta)


def _boleto_do_residuo(titulo_id, vencimento):
    """Boleto avulso da diferença pela conta padrão da empresa — o mesmo caminho do Reemitir."""
    from models.nfse_emissao import tomador_do_cliente
    from routes.financeiro_cobranca import _pagador_do_tomador, emitir_boleto_avulso, sugestao_boleto
    t = _q('SELECT * FROM fin_titulos WHERE id = %s', (titulo_id,), one=True)
    conta = FinCobrancaConta.padrao_da_empresa(t['empresa_id'])
    if not conta:
        return False, 'O título da diferença foi criado, mas não há conta de cobrança para o boleto.'
    tom = tomador_do_cliente(t['cliente_id'], consultar_cep=False) if t.get('cliente_id') else None
    if not tom or not tom.get('documento'):
        return False, 'O título da diferença foi criado, mas o cadastro do cliente não tem CPF/CNPJ para o boleto.'
    itens = [{'nome': t['descricao'][:255], 'valor': _d(t['valor']), 'emite_nota': False, 'servico_id': None}]
    competencia = t.get('competencia') or _hoje()
    _v, mensagem = sugestao_boleto(conta, competencia)
    novo_id, ok, msg, aviso = emitir_boleto_avulso(t['empresa_id'], conta, _pagador_do_tomador(tom), itens,
                                                   _d(t['valor']), competencia, vencimento, mensagem, t['id'],
                                                   current_user.id)
    return ok, msg + (f' {aviso}' if aviso else '')
