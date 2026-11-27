# -*- coding: utf-8 -*-
"""Lote mensal de honorários (parte 6, etapa 3 — 06/11/2026).

Por volta do dia 25 o Anderson emite a cobrança do mês: para cada contrato ATIVO na competência,
honorários (as discriminações do contrato) + a parcela do 13º que cai nessa competência + os
lançamentos MENSAL aprovados → UMA nota (se o contrato emite nota) + UM boleto Efí + e-mail aos
contatos do Financeiro. Vencimento: o dia do contrato no mês seguinte à competência.

Idempotente por (lote, cliente) em ``fin_lote_itens``: rodar de novo só cobra quem ainda não foi.
A emissão roda numa thread (215 empresas levam minutos); a tela acompanha pelo status.
"""
import calendar
import json
import logging
import threading
import uuid
from datetime import date, datetime
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from models.fin_contrato import CATEGORIA_HONORARIOS, EMPRESA_QUALICONTAX, SERVICO_DECIMO_PROPRIO, SERVICO_HONORARIOS, FinContrato
from models.fin_lancamento import FinLancamento, FinLoteMensal, mes_seguinte
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
SERVICO_DECIMO = {('DUAS', 0): 2, ('DUAS', 1): 3, ('UNICA', 0): 4}     # nfse_servicos: 13º 1ª, 2ª, única
_rodando = {}


class JaEmitindo(ValueError):
    """Outra rodada deste lote está em andamento (em qualquer processo do servidor)."""


def _d(v):
    return Decimal(str(v or 0)).quantize(Decimal('0.01'))


def vencimento_para(dia, competencia, hoje=None):
    """Dia do contrato no mês seguinte à competência (31 em mês de 30 vira 30); nunca antes de hoje."""
    m = mes_seguinte(competencia)
    d = date(m.year, m.month, min(int(dia or 10), calendar.monthrange(m.year, m.month)[1]))
    return max(d, hoje or date.today())


def itens_da_empresa(contrato, competencia, itens_contrato=None, lancamentos=None):
    """O que a empresa paga nesta competência: honorários (discriminações), 13º e lançamentos aprovados.
    ``itens_contrato``/``lancamentos`` vêm pré-carregados pela prévia (215 empresas = 3 consultas, não 430)."""
    itens = []
    if itens_contrato is None:
        itens_contrato = FinContrato.itens(contrato['id'])
    for i in itens_contrato:
        itens.append({'origem': 'honorarios', 'servico_id': i['servico_id'] or SERVICO_HONORARIOS,
                      'nome': i['descricao'], 'valor': str(_d(i['valor']))})
    # 19/11/2026 — desconto mensal do contrato: linha própria, negativa; o total da empresa já sai líquido
    honor = sum((_d(i['valor']) for i in itens_contrato if (i['servico_id'] or SERVICO_HONORARIOS) == SERVICO_HONORARIOS), Decimal('0')) \
        or sum((_d(i['valor']) for i in itens_contrato), Decimal('0'))
    desc = FinContrato.desconto_de(contrato, honor, competencia)
    if desc > 0:
        itens.append({'origem': 'desconto', 'servico_id': None, 'valor': str(-desc),
                      'nome': 'Desconto mensal' + ((' — ' + contrato['desconto_motivo']) if contrato.get('desconto_motivo') else '')})
    tipo = contrato.get('decimo_tipo') or 'NENHUM'
    for n, (comp, valor) in enumerate(FinContrato.parcelas_decimo(contrato, competencia.year)):
        if comp == competencia and valor > 0:
            sid = SERVICO_DECIMO.get((tipo, n), 4 if tipo != 'DUAS' else (2 if n == 0 else 3))
            itens.append({'origem': 'decimo', 'servico_id': sid, 'nome': '13º honorário', 'valor': str(_d(valor))})
    if lancamentos is None:
        lancamentos = FinLancamento.para_o_mensal(contrato['empresa_id'], competencia, contrato['cliente_id'])
    for l in lancamentos:
        if l.get('origem') == 'DESCONTO':          # 19/11/2026: desconto lançado pela equipe, aprovado — linha negativa
            itens.append({'origem': 'desconto', 'servico_id': None, 'nome': l['descricao'] or 'Desconto',
                          'valor': str(-_d(l['valor_total'])), 'lancamento_id': l['id'], 'abate_de': l.get('desc_lancamento_id'),
                          'por': l.get('lancado_por_nome') or ''})
            continue
        itens.append({'origem': 'lancamento', 'servico_id': l['servico_id'], 'nome': l['descricao'] or l['servico_nome'],
                      'valor': str(_d(l['valor_total'])), 'lancamento_id': l['id'], 'por': l.get('lancado_por_nome') or ''})
    return itens


def contratos_da_competencia(empresa_id, competencia):
    fim_mes = date(competencia.year, competencia.month, calendar.monthrange(competencia.year, competencia.month)[1])
    return [c for c in FinContrato.listar(empresa_id, situacao='ATIVO')
            if (c['inicio'] or competencia) <= fim_mes and (not c['fim'] or c['fim'] >= competencia)]


def escolhas(empresa_id, competencia):
    """Escolhas SÓ DESTA competência, empresa por empresa (18/11/2026): banco e nota. NULL = segue a regra do contrato."""
    try:
        r = execute_query('SELECT * FROM fin_honorarios_escolhas WHERE empresa_id = %s AND competencia = %s',
                          (empresa_id, competencia), fetch=True) or []
    except Exception:
        r = []
    return {x['cliente_id']: x for x in r}


def linha_honorarios(itens_contrato):
    """Índice da ÚNICA linha de honorários contábeis do contrato (a que o "Alterar" muda), ou None se houver zero ou várias
    — aí o valor só muda abrindo o contrato."""
    ks = [k for k, i in enumerate(itens_contrato or []) if (i.get('servico_id') or SERVICO_HONORARIOS) == SERVICO_HONORARIOS]
    return ks[0] if len(ks) == 1 else None


def regra_banco(contrato):
    """Banco gravado no contrato (fin_contratos.banco_boleto guarda o id da conta de cobrança) ou None = o padrão."""
    v = str(contrato.get('banco_boleto') or '').strip()
    return int(v) if v.isdigit() else None


def previa(empresa_id, competencia, so_clientes=None, so_decimo=None):
    """Linhas da tela: uma por empresa (tipo MENSAL), com itens, total, vencimento e o que já saiu neste lote; e, para
    quem tem o 13º em boletos próprios, a 2ª linha da empresa (tipo DECIMO) com a parcela que vence no mês seguinte.
    ``so_clientes``/``so_decimo`` = None: sem filtro; lista: só essas empresas (mensalidade / parcela do 13º)."""
    lote = FinLoteMensal.get(empresa_id, competencia)
    feitos = {}
    if lote:
        for r in execute_query('SELECT * FROM fin_lote_itens WHERE lote_id = %s', (lote['id'],), fetch=True) or []:
            feitos[(r['cliente_id'], r.get('tipo') or 'MENSAL')] = r
    contratos = contratos_da_competencia(empresa_id, competencia)
    if so_clientes is not None or so_decimo is not None:
        quer = set(so_clientes or []) | set(so_decimo or [])
        contratos = [c for c in contratos if c['cliente_id'] in quer]
    por_contrato, por_cliente = {}, {}
    if contratos:
        ids = [c['id'] for c in contratos]
        for i in execute_query('SELECT * FROM fin_contrato_itens WHERE contrato_id IN (%s) ORDER BY ordem, id' % ','.join(['%s'] * len(ids)),
                               tuple(ids), fetch=True) or []:
            por_contrato.setdefault(i['contrato_id'], []).append(i)
        for l in FinLancamento.para_o_mensal(empresa_id, competencia):
            por_cliente.setdefault(l['cliente_id'], []).append(l)
    esc = escolhas(empresa_id, competencia)
    # 26/11/2026 — Acordo, etapa 3: parcelas "junto com o honorário" que vencem até o fim do mês deste boleto
    try:
        from utils import acordos
        fim = mes_seguinte(competencia)
        acp = acordos.parcelas_junto(empresa_id, date(fim.year, fim.month, calendar.monthrange(fim.year, fim.month)[1]))
    except Exception:
        logger.exception('[lote] parcelas de acordo')
        acp = {}
    linhas = []
    for c in contratos:
        e = esc.get(c['cliente_id']) or {}
        # 25/11/2026 — "Alterar as marcadas": honorários e vencimento SÓ DESTA cobrança (fin_honorarios_escolhas)
        ic = por_contrato.get(c['id'], [])
        k_hon = linha_honorarios(ic)
        hon_mes = e.get('valor_honorarios') is not None and k_hon is not None
        if hon_mes:
            ic = [dict(i, valor=e['valor_honorarios']) if k == k_hon else i for k, i in enumerate(ic)]
        itens = itens_da_empresa(c, competencia, ic, por_cliente.get(c['cliente_id'], []))
        itens += acp.get(c['cliente_id'], [])          # parcela do acordo: só no boleto, fora da nota
        total = sum(_d(i['valor']) for i in itens)
        venc = vencimento_para(c['dia_vencimento'], competencia)
        venc_mes = bool(e.get('vencimento')) and e['vencimento'] != venc
        if venc_mes:
            venc = max(e['vencimento'], date.today())
        linhas.append({'contrato': c, 'cliente_id': c['cliente_id'], 'numero': c['numero_cliente'], 'empresa': c['nome_razao_social'],
                       'tipo': 'MENSAL', 'itens': itens, 'total': total, 'vencimento': venc, 'venc_mes': venc_mes,
                       'honorarios': _d(ic[k_hon]['valor']) if k_hon is not None else None, 'hon_mes': hon_mes,
                       'hon_contrato': _d(por_contrato.get(c['id'], [])[k_hon]['valor']) if k_hon is not None else None,
                       'nota': bool(c.get('gerar_nfse')), 'boleto': bool(c.get('gerar_boleto')),
                       'contatos': c.get('n_contatos_fin') or 0, 'feito': feitos.get((c['cliente_id'], 'MENSAL'))})
        # 18/11/2026 — Anderson: "teremos o Banco Padrão e poderemos decidir emitir por outro caso necessário" e
        # "temos que ter o controle dos que fazem NF e ter as opções de selecionar para ser regra e/ou desmarcar".
        # Ordem: escolha deste mês > regra do contrato > padrão.
        l = linhas[-1]
        l['nota_regra'] = l['nota']
        l['nota_mes'] = e.get('gerar_nfse') is not None and bool(e['gerar_nfse']) != l['nota_regra']
        if e.get('gerar_nfse') is not None:
            l['nota'] = bool(e['gerar_nfse'])
        l['conta_regra'] = regra_banco(c)
        l['conta_mes'] = e.get('conta_cobranca_id')
        l['conta_id'] = l['conta_mes'] or l['conta_regra']      # None = o banco padrão
        # 19/11/2026 — 13º em boletos próprios: a parcela que vence no mês seguinte é a 2ª cobrança da empresa, boleto
        # separado da mensalidade (Anderson: a FAST paga o 13º em 3 parcelas "mas não nos honorários"). Mesmo banco e
        # mesma regra de nota da mensalidade; vencimento é o dia combinado (ou hoje, se já passou).
        p = FinContrato.parcela_decimo_proprio(c, competencia)
        if p and (so_decimo is None or c['cliente_id'] in so_decimo):
            linhas.append(dict(l, tipo='DECIMO', parcela=p, boleto=True, total=p['valor'], vencimento=max(p['vencimento'], date.today()),
                               itens=[{'origem': 'decimo', 'servico_id': SERVICO_DECIMO_PROPRIO,
                                       'nome': '13º honorário %d · parcela %d/%d' % (p['ano'], p['n'], p['de']), 'valor': str(p['valor'])}],
                               feito=feitos.get((c['cliente_id'], 'DECIMO'))))
        if so_clientes is not None and c['cliente_id'] not in so_clientes:
            linhas.remove(l)                                   # só a parcela do 13º foi marcada; a mensalidade não
    # 26/11/2026 — Anderson, antes do 1º lote pelo app: "garantia para quem será enviado todos os boletos"; "os que não
    # tem e-mails não enviar e os que tiver @qualicontax.com.br tb não enviar!! Precisa ter no cadastro o contato
    # comercial". Cada linha ganha os destinatários reais (mesma regra do e-mail: destinatarios_detalhe), o motivo do
    # bloqueio quando não há para quem mandar, e o aviso de cobrança desta competência já feita FORA do lote.
    from utils.cobranca_email import destinatarios_detalhe
    ids = sorted({l['cliente_id'] for l in linhas})
    det = destinatarios_detalhe(ids) if ids else {}
    fora = _fora_do_lote(empresa_id, competencia, ids, lote['id'] if lote else None)
    for l in linhas:
        d = det.get(l['cliente_id']) or {'destinos': [], 'internos': [], 'falsos': []}
        l['destinos'], l['internos'] = d['destinos'], d['internos']
        l['contatos'] = len(d['destinos'])                     # a etiqueta do cartão conta quem recebe de verdade
        l['bloqueio'] = None if d['destinos'] else ('interno' if d['internos'] else 'sem_email')
        l['fora_lote'] = fora.get(l['cliente_id']) if (l.get('tipo') or 'MENSAL') == 'MENSAL' else None
    return lote, linhas


BLOQUEIOS = {'sem_email': 'sem contato com e-mail: cadastre o contato comercial da empresa',
             'interno': 'só tem e-mail da própria Qualicontax: cadastre o contato comercial da empresa'}


def motivo_bloqueio(linha):
    m = BLOQUEIOS.get(linha.get('bloqueio') or '')
    if linha.get('bloqueio') == 'interno' and linha.get('internos'):
        m = 'só tem e-mail da própria Qualicontax (%s): cadastre o contato comercial da empresa' % ', '.join(linha['internos'])
    return m


def _fora_do_lote(empresa_id, competencia, ids, lote_id):
    """Títulos a receber de HONORÁRIOS desta competência que NÃO nasceram neste lote — a mensalidade emitida à mão em Nova
    cobrança, por exemplo (26/11/2026: "honorário da mesma empresa e competência já emitido fora do lote aparece como
    aviso na conferência, não emite de novo"). Serviço avulso cobrado na mesma competência (abertura de empresa, DRE,
    alteração contratual...) NÃO conta: a descrição do título do lote é "Honorários e serviços — Honorários contábeis",
    então só entra o título cuja parte depois do travessão fala em honorário (e não é parcela do 13º).
    {cliente_id: título mais recente}."""
    if not ids:
        return {}
    ph = ','.join(['%s'] * len(ids))
    rows = execute_query(
        f"""SELECT t.id, t.cliente_id, t.valor, t.origem, t.vencimento, t.status, t.descricao, t.boleto_id, t.nfse_id
              FROM fin_titulos t
             WHERE t.empresa_id = %s AND t.tipo = 'R' AND t.competencia = %s AND t.categoria_id = %s
               AND t.origem IN ('boleto', 'nfse') AND t.status <> 'cancelado' AND t.cliente_id IN ({ph})
               AND SUBSTRING_INDEX(t.descricao, '— ', -1) LIKE '%%onor%%'
               AND SUBSTRING_INDEX(t.descricao, '— ', -1) NOT LIKE '13º%%'
               AND t.id NOT IN (SELECT COALESCE(titulo_id, 0) FROM fin_lote_itens WHERE lote_id = %s)
             ORDER BY t.id DESC""", (empresa_id, competencia, CATEGORIA_HONORARIOS, *ids, lote_id or 0), fetch=True) or []
    out = {}
    for r in rows:
        out.setdefault(r['cliente_id'], r)
    return out


def _abate_desconto(itens, desconto, rotulo='desconto mensal', primeiro=None):
    """Itens que vão para a nota e para o boleto quando há desconto mensal: a linha dos honorários sai pelo LÍQUIDO e
    diz de onde veio ("Honorários contábeis (R$ 1.000,00 − desconto mensal R$ 33,55)"); se não couber nela, abate das
    outras. Banco e governo não aceitam linha negativa; a página do cliente e o e-mail mostram as duas linhas."""
    resta = _d(desconto)
    out = []
    ordem = sorted(range(len(itens)), key=lambda k: (0 if k == primeiro else 1, 0 if itens[k].get('origem') == 'honorarios' else 1))
    novos = {}
    for k in ordem:
        v = _d(itens[k]['valor'])
        if resta <= 0 or v <= 0:
            continue
        corte = min(v, resta)
        resta -= corte
        novos[k] = (v, corte)
    for k, i in enumerate(itens):
        if k in novos:
            v, corte = novos[k]
            nome = '%s (R$ %s − %s R$ %s)' % (i['nome'], _brl(v), rotulo, _brl(corte))
            out.append(dict(i, nome=nome[:255], valor=str((v - corte).quantize(Decimal('0.01')))))
        else:
            out.append(dict(i))
    return [i for i in out if _d(i['valor']) > 0]


def _brl(v):
    return ('%.2f' % _d(v)).replace('.', ',')


def _aplicar_descontos(itens):
    """(itens cobráveis já abatidos, total de desconto). O desconto lançado "sobre X" abate a linha de X; o desconto mensal
    e o "sobre a mensalidade" abatem os honorários (depois as outras linhas)."""
    descs = [i for i in itens if i.get('origem') == 'desconto']
    cobr = [dict(i) for i in itens if i.get('origem') != 'desconto']
    total = Decimal('0')
    for d in descs:
        v = -_d(d['valor'])
        if v <= 0:
            continue
        total += v
        alvo = [k for k, i in enumerate(cobr) if d.get('abate_de') and i.get('lancamento_id') == d.get('abate_de')]
        cobr = _abate_desconto(cobr, v, 'desconto' if d.get('lancamento_id') else 'desconto mensal', primeiro=alvo[0] if alvo else None)
    return cobr, total


def _cobrar(linha, lote_id, usuario_id, conta, cfg, empresa_id):
    """Nota + boleto + e-mail de UMA empresa. Grava a linha em fin_lote_itens. Devolve o status."""
    from models.fin_cobranca import FinBoleto
    from models.nfse_emissao import NfseConfig, NfseEmitida, tomador_do_cliente
    from routes.financeiro_cobranca import _pagador_do_tomador, emitir_boleto_avulso, gerar_boleto_da_nota, sugestao_boleto
    from utils import nfse_emissor
    itens, total, venc = linha['itens'], linha['total'], linha['vencimento']
    competencia = linha['competencia']
    tipo = linha.get('tipo') or 'MENSAL'           # MENSAL = a mensalidade; DECIMO = parcela do 13º em boleto próprio
    execute_query(
        """INSERT INTO fin_lote_itens (lote_id, cliente_id, tipo, contrato_id, itens, total, vencimento, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'pendente')
           ON DUPLICATE KEY UPDATE itens = VALUES(itens), total = VALUES(total), vencimento = VALUES(vencimento), id = LAST_INSERT_ID(id)""",
        (lote_id, linha['cliente_id'], tipo, linha['contrato']['id'], json.dumps(itens, ensure_ascii=False), total, venc), fetch=False)

    def fechar(status, erro=None, titulo_id=None, nfse_id=None, boleto_id=None, email=None):
        execute_query("""UPDATE fin_lote_itens SET status = %s, erro = %s, titulo_id = %s, nfse_id = %s, boleto_id = %s, email = %s,
                                                   reserva = NULL, atualizado_em = NOW()
                         WHERE lote_id = %s AND cliente_id = %s AND tipo = %s""",
                      (status, (erro or None), titulo_id, nfse_id, boleto_id, (email or '')[:255] or None, lote_id, linha['cliente_id'], tipo), fetch=False)
        return status

    # 26/11/2026 — reserva da linha no banco: só a rodada que conseguir marcar 'emitindo' segue; outra rodada em paralelo
    # (outro processo, outra aba) encontra a linha ocupada e não mexe. Uma reserva parada há mais de 20 min (servidor
    # reiniciou no meio) pode ser retomada — e a retomada reaproveita o que já saiu (abaixo).
    reserva = uuid.uuid4().hex
    execute_query("""UPDATE fin_lote_itens SET status = 'emitindo', reserva = %s, atualizado_em = NOW()
                      WHERE lote_id = %s AND cliente_id = %s AND tipo = %s
                        AND (status IN ('pendente', 'erro', 'pulado')
                             OR (status = 'emitindo' AND atualizado_em < NOW() - INTERVAL 20 MINUTE))""",
                  (reserva, lote_id, linha['cliente_id'], tipo), fetch=False)
    r = execute_query('SELECT reserva, status FROM fin_lote_itens WHERE lote_id = %s AND cliente_id = %s AND tipo = %s',
                      (lote_id, linha['cliente_id'], tipo), fetch=True, fetch_one=True) or {}
    if r.get('reserva') != reserva:
        return 'ocupado'                                        # outra rodada está com esta empresa (ou ela já saiu)

    if total <= 0:
        return fechar('pulado', 'sem valor a cobrar')
    if linha.get('bloqueio'):                                   # sem para quem mandar: não emite (Anderson, 26/11/2026)
        return fechar('pulado', motivo_bloqueio(linha))
    if linha.get('fora_lote') and not linha.get('forcar'):
        f = linha['fora_lote']
        return fechar('pulado', 'já existe cobrança de honorários desta competência fora do lote (título %s, R$ %s, vence %s): '
                                'confira antes de emitir; na conferência dá para emitir mesmo assim'
                      % (f['id'], _brl(f['valor']), f['vencimento'].strftime('%d/%m/%Y') if f.get('vencimento') else '—'))
    # 26/11/2026 — retomada sem repetir: o que uma tentativa anterior desta linha já tirou é reaproveitado
    feito = linha.get('feito') or {}
    if feito.get('boleto_id'):
        b0 = FinBoleto.get(feito['boleto_id'])
        if b0 and b0.get('situacao') not in ('erro', 'canceled', 'rascunho'):
            return fechar('ok', titulo_id=b0.get('titulo_id'), nfse_id=b0.get('nfse_id') or feito.get('nfse_id'), boleto_id=b0['id'],
                          email='boleto já tinha saído numa tentativa anterior; nada foi repetido')
        if b0 and b0.get('situacao') == 'rascunho':
            return fechar('erro', 'o boleto %s ficou em rascunho numa tentativa interrompida: confira no banco se ele existe lá; '
                                  'se não existir, cancele o rascunho antes de emitir de novo' % b0['id'],
                          nfse_id=feito.get('nfse_id'), boleto_id=b0['id'])
    nota_pronta = None
    if feito.get('nfse_id'):
        n0 = NfseEmitida.get(feito['nfse_id'])
        if n0 and n0.get('situacao') == 'autorizada':
            nota_pronta = n0
    t = tomador_do_cliente(linha['cliente_id'])
    if not t or not t.get('documento'):
        return fechar('erro', 'cadastro sem CPF/CNPJ')
    _v, mensagem = sugestao_boleto(conta, competencia)
    # 19/11/2026 — desconto mensal: a linha negativa vira abatimento na linha dos honorários (banco e nota só aceitam positivo)
    itens_lin = itens                                              # o que fica gravado no lote (com as linhas de desconto)
    # 26/11/2026 — parcela de acordo: fora da nota e fora do desconto mensal (vai inteira, só no boleto)
    acordo_its = [i for i in itens if i.get('origem') == 'acordo']
    itens, desconto = _aplicar_descontos([i for i in itens if i.get('origem') != 'acordo'])
    itens = itens + acordo_its
    emite_nota = (bool(cfg) and linha['nota'] and any(i['servico_id'] for i in itens)) or bool(nota_pronta)
    nfse_id = boleto_id = titulo_id = None
    avisos = []
    try:
        if emite_nota and nota_pronta:
            nfse_id = nota_pronta['id']                        # a nota já estava autorizada: só falta o boleto
            avisos.append('nota já autorizada numa tentativa anterior, reaproveitada')
        elif emite_nota:
            serie = cfg.get('serie_nacional') if (cfg.get('emissor') or 'nacional') == 'nacional' else cfg.get('serie_sp')
            # 21/11/2026: o texto da nota discrimina cada linha (bruto) e o desconto como linha própria; o valor
            # declarado continua o líquido dos itens já abatidos (itens).
            from models.nfse_emissao import NfseServico
            emite = {s['id'] for s in NfseServico.listar(empresa_id) if str(s.get('emite_nota', 1)) != '0'}
            linhas_nota = [[i['nome'], str(_d(i['valor']))] for i in itens_lin
                           if (i.get('servico_id') in emite) or (i.get('origem') == 'desconto')]
            form = MultiDict([('serie', str(serie or '')), ('competencia', competencia.isoformat()),
                              ('tomador_cliente_id', str(linha['cliente_id'])),
                              ('descricao', NfseConfig.descricao_para(cfg, competencia)),
                              ('linhas_nota', json.dumps(linhas_nota, ensure_ascii=False))]
                             + [('item_servico_id', str(i['servico_id'])) for i in itens if i.get('origem') != 'acordo']
                             + [('item_valor', i['valor']) for i in itens if i.get('origem') != 'acordo'])
            pedido = nfse_emissor.pedido_do_form(form, empresa_id, cfg)
            pedido.origem = 'lote'
            nota = nfse_emissor.emitir(pedido, cfg, usuario_id=usuario_id)
            if not nota or nota['situacao'] != 'autorizada':
                return fechar('erro', 'nota não autorizada: %s' % ((nota or {}).get('erro') or (nota or {}).get('situacao') or 'falha'),
                              nfse_id=(nota or {}).get('id'))
            nfse_id = nota['id']
        if emite_nota:
            if linha['boleto']:
                n = NfseEmitida.get(nfse_id)
                ok, msg, aviso = gerar_boleto_da_nota(n, conta, venc, mensagem, usuario_id, extras=[
                    {'nome': i['nome'], 'valor': Decimal(i['valor']), 'emite_nota': False} for i in acordo_its])
                if not ok:
                    return fechar('erro', 'nota ok, boleto não: ' + msg, nfse_id=nfse_id)
                b = FinBoleto.da_nota(nfse_id)
                boleto_id, titulo_id = (b or {}).get('id'), (b or {}).get('titulo_id')
                avisos.append(aviso or '')
        else:
            itens_b = [{'servico_id': i['servico_id'], 'nome': i['nome'], 'valor': Decimal(i['valor']), 'emite_nota': False} for i in itens]
            boleto_id, ok, msg, aviso = emitir_boleto_avulso(empresa_id, conta, _pagador_do_tomador(t), itens_b, total,
                                                             competencia, venc, mensagem, None, usuario_id,
                                                             desconto=desconto if desconto > 0 else None)
            if not ok:
                return fechar('erro', msg)
            titulo_id = (FinBoleto.get(boleto_id) or {}).get('titulo_id')
            avisos.append(aviso or '')
    except Exception as exc:
        logger.exception('[lote] empresa %s', linha['cliente_id'])
        return fechar('erro', str(exc)[:1000], nfse_id=nfse_id, boleto_id=boleto_id)
    # a parcela passa a ser cobrada pelo boleto do mês — só se o boleto saiu com ela (valor = total da linha)
    if titulo_id and boleto_id and acordo_its and _d((FinBoleto.get(boleto_id) or {}).get('valor')) == _d(total):
        from utils import acordos
        acordos.absorver([i['acordo_parcela_id'] for i in acordo_its], titulo_id,
                         'boleto do honorário de %02d/%d' % (competencia.month, competencia.year))
    for i in itens_lin:
        if i.get('lancamento_id'):
            FinLancamento.marcar_faturado(i['lancamento_id'], titulo_id=titulo_id, nfse_id=nfse_id, boleto_id=boleto_id, lote_id=lote_id)
    # comissão de honorários (cliente "dele") e de indicação: nasce aqui, liberada quando o título for pago — pelo líquido
    honor = sum(_d(i['valor']) for i in itens if i['origem'] == 'honorarios')
    if tipo == 'MENSAL':                           # o 13º não tem comissão
        try:
            FinLancamento.comissoes_do_lote(empresa_id, linha['cliente_id'], competencia, titulo_id, lote_id, honor, nfse_id, boleto_id)
        except Exception:
            logger.exception('[lote] comissões da empresa %s', linha['cliente_id'])
    return fechar('ok', titulo_id=titulo_id, nfse_id=nfse_id, boleto_id=boleto_id, email='; '.join(a for a in avisos if a))


def rodar(app, empresa_id, competencia, usuario_id, so_clientes=None, vencimento=None, so_decimo=None, vencimentos=None, forcar=None):
    """Emite o lote inteiro (ou só ``so_clientes``) numa thread. Devolve o id do lote.
    ``vencimento`` (date): a pessoa escolheu outra data na janela de emissão — vale para todas as empresas desta
    rodada no lugar do dia do contrato (19/11/2026, Anderson: "escolher data do contrato ou outra data").
    ``forcar``: empresas que a pessoa mandou emitir mesmo já tendo cobrança desta competência fora do lote (26/11/2026).
    Levanta ``JaEmitindo`` quando outra rodada deste lote está em andamento (trava no banco, vale para os 4 processos)."""
    from models.fin_cobranca import FinCobrancaConta
    from models.nfse_emissao import NfseConfig
    lote_id = FinLoteMensal.abrir(empresa_id, competencia)
    quem = FinLoteMensal.rodando(lote_id)
    if _rodando.get(lote_id) or quem:
        raise JaEmitindo(_msg_ja_emitindo(competencia, quem))
    conta = FinCobrancaConta.padrao_da_empresa(empresa_id)
    cfg = NfseConfig.get(empresa_id)
    if not conta:
        raise ValueError('Não há conta de cobrança ativa.')
    token = FinLoteMensal.reservar(lote_id, usuario_id)
    if not token:
        raise JaEmitindo(_msg_ja_emitindo(competencia, FinLoteMensal.rodando(lote_id)))
    forcar = set(forcar or [])
    contas = {}

    def conta_da(linha):
        """O banco da empresa (escolha do mês ou regra do contrato) se ainda servir para emitir; senão o padrão."""
        cid = linha.get('conta_id')
        if not cid or cid == conta['id']:
            return conta
        if cid not in contas:
            c = FinCobrancaConta.get(cid, com_segredos=True)
            contas[cid] = c if (c and c.get('ativo') and not c.get('so_consulta') and c.get('empresa_id') == empresa_id) else conta
        return contas[cid]

    def alvo():
        _rodando[lote_id] = True
        try:
            with app.app_context(), app.test_request_context('https://app.qualicontax.com.br/'):
                _lote, linhas = previa(empresa_id, competencia, so_clientes=so_clientes, so_decimo=so_decimo)
                for linha in linhas:
                    if linha['feito'] and linha['feito']['status'] == 'ok':
                        continue
                    FinLoteMensal.bater(lote_id, token)                                   # a reserva continua viva
                    linha['competencia'] = competencia
                    linha['forcar'] = linha['cliente_id'] in forcar
                    if (linha.get('tipo') or 'MENSAL') == 'MENSAL':                      # a parcela do 13º fica na data combinada
                        if vencimentos and linha['cliente_id'] in vencimentos:          # "Data por empresa" (19/11/2026)
                            linha['vencimento'] = vencimentos[linha['cliente_id']]
                        elif vencimento:
                            linha['vencimento'] = vencimento
                    _cobrar(linha, lote_id, usuario_id, conta_da(linha), cfg, empresa_id)
                r = execute_query("SELECT COUNT(*) n, COALESCE(SUM(total), 0) t FROM fin_lote_itens WHERE lote_id = %s AND status = 'ok'",
                                  (lote_id,), fetch=True, fetch_one=True)
                FinLoteMensal.marcar_emitido(lote_id, usuario_id, r['n'], r['t'])
        except Exception:
            logger.exception('[lote] rodada do lote %s', lote_id)
        finally:
            _rodando.pop(lote_id, None)
            try:
                with app.app_context():
                    FinLoteMensal.liberar(lote_id, token)
            except Exception:
                logger.exception('[lote] liberar a trava do lote %s', lote_id)

    threading.Thread(target=alvo, name='lote-%s' % lote_id, daemon=True).start()
    return lote_id


def _msg_ja_emitindo(competencia, quem):
    q = quem or {}
    por = (' por %s' % q['rodando_por_nome'].split()[0]) if q.get('rodando_por_nome') else ''
    desde = (' desde %s' % q['rodando_desde'].strftime('%H:%M')) if q.get('rodando_desde') else ''
    return ('A cobrança de %s já está sendo emitida%s%s. Nada foi emitido de novo: espere terminar, a tela acompanha sozinha.'
            % (competencia.strftime('%m/%Y'), por, desde))


def status(lote_id):
    r = execute_query("""SELECT COUNT(*) total, SUM(status = 'ok') ok, SUM(status = 'erro') erro,
                                SUM(status IN ('pendente', 'emitindo')) pendente, SUM(status = 'pulado') pulado,
                                COALESCE(SUM(CASE WHEN status = 'ok' THEN total END), 0) valor_ok
                           FROM fin_lote_itens WHERE lote_id = %s""", (lote_id,), fetch=True, fetch_one=True) or {}
    quem = FinLoteMensal.rodando(lote_id)                       # 26/11/2026: pelo banco, vale para os 4 processos
    r['rodando'] = bool(_rodando.get(lote_id)) or bool(quem)
    r['rodando_por'] = (quem or {}).get('rodando_por_nome') or ''
    return r
