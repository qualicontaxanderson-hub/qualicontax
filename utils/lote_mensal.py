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
from datetime import date, datetime
from decimal import Decimal

from werkzeug.datastructures import MultiDict

from models.fin_contrato import EMPRESA_QUALICONTAX, SERVICO_DECIMO_PROPRIO, SERVICO_HONORARIOS, FinContrato
from models.fin_lancamento import FinLancamento, FinLoteMensal, mes_seguinte
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
SERVICO_DECIMO = {('DUAS', 0): 2, ('DUAS', 1): 3, ('UNICA', 0): 4}     # nfse_servicos: 13º 1ª, 2ª, única
_rodando = {}


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
        itens.append({'origem': 'lancamento', 'servico_id': l['servico_id'], 'nome': l['descricao'] or l['servico_nome'],
                      'valor': str(_d(l['valor_total'])), 'lancamento_id': l['id']})
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
    linhas = []
    for c in contratos:
        itens = itens_da_empresa(c, competencia, por_contrato.get(c['id'], []), por_cliente.get(c['cliente_id'], []))
        total = sum(_d(i['valor']) for i in itens)
        linhas.append({'contrato': c, 'cliente_id': c['cliente_id'], 'numero': c['numero_cliente'], 'empresa': c['nome_razao_social'],
                       'tipo': 'MENSAL', 'itens': itens, 'total': total, 'vencimento': vencimento_para(c['dia_vencimento'], competencia),
                       'nota': bool(c.get('gerar_nfse')), 'boleto': bool(c.get('gerar_boleto')),
                       'contatos': c.get('n_contatos_fin') or 0, 'feito': feitos.get((c['cliente_id'], 'MENSAL'))})
        # 18/11/2026 — Anderson: "teremos o Banco Padrão e poderemos decidir emitir por outro caso necessário" e
        # "temos que ter o controle dos que fazem NF e ter as opções de selecionar para ser regra e/ou desmarcar".
        # Ordem: escolha deste mês > regra do contrato > padrão.
        e = esc.get(c['cliente_id']) or {}
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
    return lote, linhas


def _abate_desconto(itens, desconto):
    """Itens que vão para a nota e para o boleto quando há desconto mensal: a linha dos honorários sai pelo LÍQUIDO e
    diz de onde veio ("Honorários contábeis (R$ 1.000,00 − desconto mensal R$ 33,55)"); se não couber nela, abate das
    outras. Banco e governo não aceitam linha negativa; a página do cliente e o e-mail mostram as duas linhas."""
    resta = _d(desconto)
    out = []
    ordem = sorted(range(len(itens)), key=lambda k: 0 if itens[k].get('origem') == 'honorarios' else 1)
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
            nome = '%s (R$ %s − desconto mensal R$ %s)' % (i['nome'], _brl(v), _brl(corte))
            out.append(dict(i, nome=nome[:255], valor=str((v - corte).quantize(Decimal('0.01')))))
        else:
            out.append(dict(i))
    return [i for i in out if _d(i['valor']) > 0]


def _brl(v):
    return ('%.2f' % _d(v)).replace('.', ',')


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
        execute_query("""UPDATE fin_lote_itens SET status = %s, erro = %s, titulo_id = %s, nfse_id = %s, boleto_id = %s, email = %s
                         WHERE lote_id = %s AND cliente_id = %s AND tipo = %s""",
                      (status, (erro or None), titulo_id, nfse_id, boleto_id, (email or '')[:255] or None, lote_id, linha['cliente_id'], tipo), fetch=False)
        return status

    if total <= 0:
        return fechar('pulado', 'sem valor a cobrar')
    t = tomador_do_cliente(linha['cliente_id'])
    if not t or not t.get('documento'):
        return fechar('erro', 'cadastro sem CPF/CNPJ')
    _v, mensagem = sugestao_boleto(conta, competencia)
    # 19/11/2026 — desconto mensal: a linha negativa vira abatimento na linha dos honorários (banco e nota só aceitam positivo)
    desconto = sum((-_d(i['valor']) for i in itens if i.get('origem') == 'desconto'), Decimal('0'))
    itens_lin = itens                                              # o que fica gravado no lote (com a linha do desconto)
    if desconto > 0:
        itens = _abate_desconto([i for i in itens if i.get('origem') != 'desconto'], desconto)
    emite_nota = bool(cfg) and linha['nota'] and any(i['servico_id'] for i in itens)
    nfse_id = boleto_id = titulo_id = None
    avisos = []
    try:
        if emite_nota:
            serie = cfg.get('serie_nacional') if (cfg.get('emissor') or 'nacional') == 'nacional' else cfg.get('serie_sp')
            form = MultiDict([('serie', str(serie or '')), ('competencia', competencia.isoformat()),
                              ('tomador_cliente_id', str(linha['cliente_id'])),
                              ('descricao', NfseConfig.descricao_para(cfg, competencia)
                               + (' Desconto mensal de R$ %s já abatido dos honorários.' % _brl(desconto) if desconto > 0 else ''))]
                             + [('item_servico_id', str(i['servico_id'])) for i in itens]
                             + [('item_valor', i['valor']) for i in itens])
            pedido = nfse_emissor.pedido_do_form(form, empresa_id, cfg)
            pedido.origem = 'lote'
            nota = nfse_emissor.emitir(pedido, cfg, usuario_id=usuario_id)
            if not nota or nota['situacao'] != 'autorizada':
                return fechar('erro', 'nota não autorizada: %s' % ((nota or {}).get('erro') or (nota or {}).get('situacao') or 'falha'),
                              nfse_id=(nota or {}).get('id'))
            nfse_id = nota['id']
            if linha['boleto']:
                n = NfseEmitida.get(nfse_id)
                ok, msg, aviso = gerar_boleto_da_nota(n, conta, venc, mensagem, usuario_id)
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


def rodar(app, empresa_id, competencia, usuario_id, so_clientes=None, vencimento=None, so_decimo=None, vencimentos=None):
    """Emite o lote inteiro (ou só ``so_clientes``) numa thread. Devolve o id do lote.
    ``vencimento`` (date): a pessoa escolheu outra data na janela de emissão — vale para todas as empresas desta
    rodada no lugar do dia do contrato (19/11/2026, Anderson: "escolher data do contrato ou outra data")."""
    from models.fin_cobranca import FinCobrancaConta
    from models.nfse_emissao import NfseConfig
    lote_id = FinLoteMensal.abrir(empresa_id, competencia)
    if _rodando.get(lote_id):
        return lote_id
    conta = FinCobrancaConta.padrao_da_empresa(empresa_id)
    cfg = NfseConfig.get(empresa_id)
    if not conta:
        raise ValueError('Não há conta de cobrança ativa.')
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
                    linha['competencia'] = competencia
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

    threading.Thread(target=alvo, name='lote-%s' % lote_id, daemon=True).start()
    return lote_id


def status(lote_id):
    r = execute_query("""SELECT COUNT(*) total, SUM(status = 'ok') ok, SUM(status = 'erro') erro, SUM(status = 'pendente') pendente,
                                SUM(status = 'pulado') pulado, COALESCE(SUM(CASE WHEN status = 'ok' THEN total END), 0) valor_ok
                           FROM fin_lote_itens WHERE lote_id = %s""", (lote_id,), fetch=True, fetch_one=True) or {}
    r['rodando'] = bool(_rodando.get(lote_id))
    return r
