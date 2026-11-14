# -*- coding: utf-8 -*-
"""Boletos do Bradesco: emitir, montar o PDF, consultar, cancelar e dar baixa sozinho — 14/11/2026.

Anderson (12/11/2026): Bradesco igual à Efí — "emitir boleto, puxar o extrato e dar baixa sozinho";
"Convivem juntos, ele será poucos boletos"; juros, multa e prazo de baixa "idêntico" ao da Efí.
(14/11/2026): sem juros e sem multa; "os prazos de 120 dias é o limite do EFI, o Bradesco se tiver
mais datas vamos seguir" → o prazo de baixa vai ao máximo do Bradesco (365 dias, decurso de prazo).

* **Emitir** (``emitir``): nosso número = id do boleto (11 números, único por construção); seu número
  'QC-<id>'. O banco devolve linha digitável e Pix copia-e-cola; ``pdf_url`` recebe a marca
  ``bradesco:interno`` — o PDF é montado por nós (``utils/boleto_pdf.py``) toda vez que alguém abre,
  então o caminho de sempre (tela, e-mail, link do cliente) serve sem mudança.
* **Baixa** (``rodar_de_hora_em_hora``): o aviso (webhook) do Bradesco exige certificado SSL EV/OV de
  1 a 3 anos, que o Railway não tem. Então, de hora em hora (7h-22h), consultamos cada boleto em
  aberto (o "pago no dia" aparece em até 20 min) e, uma vez por dia, a lista de liquidados dos últimos
  10 dias como rede de segurança. Pago = ``registrar_baixa`` (origem 'webhook', referência
  'bradesco:<nosso número>') e amarra ao crédito do extrato da conta Bradesco, quando é um só.
  Desliga com BRADESCO_BOLETOS_SYNC=0.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

MARCA_PDF = 'bradesco:interno'
_ATOR = ('BOLETOS DO BRADESCO (automático)', 'bradesco_boletos')
HORAS = range(7, 23)
ABERTOS = ('new', 'waiting', 'unpaid')
TELEFONE_PADRAO = '(11) 2523-1815'


def _q(sql, p=(), one=False):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=True, fetch_one=one) if one else (execute_query(sql, p, fetch=True) or [])


def _x(sql, p=()):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=False)


def _num(s):
    import re
    return re.sub(r'\D', '', str(s or ''))


def _doc_fmt(doc):
    d = _num(doc)
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    if len(d) == 11:
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    return doc or ''


def config(conta):
    """O que é só do Bradesco na conta de cobrança (coluna config_json)."""
    cfg = conta.get('config_json') or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except ValueError:
            cfg = {}
    return {'carteira': _num(cfg.get('carteira')) or '09', 'agencia': _num(cfg.get('agencia')),
            'conta': _num(cfg.get('conta')), 'telefone': (cfg.get('telefone') or TELEFONE_PADRAO).strip()}


def nosso_numero(boleto_id):
    return str(int(boleto_id)).zfill(11)


# ---------------------------------------------------------------------------
# Emitir
# ---------------------------------------------------------------------------
def emitir(conta, boleto_id, pagador, total, vencimento):
    """Registra no Bradesco e grava a resposta no fin_boletos. Devolve (nosso número, resposta).
    Levanta BradescoErro/ValueError com a mensagem para a tela."""
    from models.fin_cobranca import FinCobrancaConta
    from utils.boleto_pdf import barras_da_linha
    brad, c = FinCobrancaConta.cliente_bradesco(conta['id'])
    nn = nosso_numero(boleto_id)
    j = brad.gerar_boleto(nn, f'QC-{boleto_id}', pagador, total, vencimento,
                          dias_baixa=c.get('dias_baixa'), juros_mes=c.get('juros_mes'), multa_pct=c.get('multa_pct'))
    nn_banco = str(j.get('ctitloCobrCdent') or nn)
    linha = _num(j.get('linhaDig10'))
    if len(linha) != 47:
        raise ValueError('O Bradesco registrou, mas não devolveu a linha digitável. Consulte o boleto antes de emitir outro.')
    pix = (j.get('wqrcdPdraoMercd') or '').replace(' ', '') or None
    _x("""UPDATE fin_boletos SET charge_id = %s, custom_id = %s, situacao = 'waiting', linha_digitavel = %s,
                 codigo_barras = %s, link = NULL, pdf_url = %s, pix_copia_cola = %s, pix_qrcode = NULL,
                 raw_resposta = %s, erro = NULL, atualizado_em = NOW()
           WHERE id = %s""",
       (nn_banco.zfill(11), f'QC-{boleto_id}', linha, barras_da_linha(linha), MARCA_PDF, pix,
        json.dumps(j, ensure_ascii=False), boleto_id))
    return nn_banco, j


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _endereco_txt(e):
    if not e:
        return ''
    rua = ', '.join(x for x in [e.get('logradouro'), e.get('numero'), e.get('complemento')] if x)
    cid = '/'.join(x for x in [e.get('cidade'), e.get('uf') or e.get('estado')] if x)
    return ' - '.join(x for x in [rua, e.get('bairro'), cid, e.get('cep')] if x)


def _beneficiario(empresa_id):
    cl = _q('SELECT nome_razao_social, cpf_cnpj FROM clientes WHERE id = %s', (empresa_id,), one=True) or {}
    e = _q('SELECT * FROM enderecos_clientes WHERE cliente_id = %s ORDER BY principal DESC, id LIMIT 1',
           (empresa_id,), one=True)
    end = _endereco_txt({k: (e or {}).get(k) for k in ('logradouro', 'numero', 'complemento', 'bairro', 'cidade', 'estado', 'cep')})
    return {'nome': cl.get('nome_razao_social') or '', 'doc': _doc_fmt(cl.get('cpf_cnpj')), 'endereco': end}


def _pagador_do_boleto(b):
    """Nome, documento e endereço de quem paga (o cadastro do cliente do título; senão o que o boleto guardou)."""
    doc = _num(b.get('pagador_doc'))
    end = ''
    cid = None
    if b.get('titulo_id'):
        t = _q('SELECT cliente_id FROM fin_titulos WHERE id = %s', (b['titulo_id'],), one=True)
        cid = (t or {}).get('cliente_id')
    if cid:
        e = _q('SELECT * FROM enderecos_clientes WHERE cliente_id = %s ORDER BY principal DESC, id LIMIT 1', (cid,), one=True)
        if e:
            end = _endereco_txt({k: e.get(k) for k in ('logradouro', 'numero', 'complemento', 'bairro', 'cidade', 'estado', 'cep')})
    return {'nome': b.get('pagador_nome') or '', 'doc': _doc_fmt(doc), 'doc_rotulo': 'CPF' if len(doc) == 11 else 'CNPJ',
            'endereco': end}


def pdf(b):
    """Bytes do PDF do boleto ``b`` (linha de fin_boletos)."""
    from models.fin_cobranca import FinCobrancaConta
    from utils import boleto_pdf as bp
    conta = FinCobrancaConta.get(b['conta_cobranca_id']) or {}
    cfg = config(conta)
    itens = b.get('itens_json') or []
    if isinstance(itens, str):
        try:
            itens = json.loads(itens)
        except ValueError:
            itens = []
    venc = b['vencimento']
    dias = int(conta.get('dias_baixa') or 0)
    juros, multa = Decimal(str(conta.get('juros_mes') or 0)), Decimal(str(conta.get('multa_pct') or 0))
    instr = []
    if not juros and not multa:
        instr.append('Não cobrar juros nem multa.')
    else:
        if multa:
            instr.append(f'Após o vencimento, multa de {str(multa).replace(".", ",")}%.')
        if juros:
            instr.append(f'Após o vencimento, juros de {str(juros).replace(".", ",")}% ao mês.')
    limite = None
    if dias:
        limite = venc + timedelta(days=dias)
        instr.append(f'Não receber após {limite.strftime("%d/%m/%Y")} ({dias} dias do vencimento).')
    instr.append('Pague pelo código de barras ou pelo Pix (QR Code no recibo acima).')
    criado = b.get('criado_em')
    emissao = criado.date() if isinstance(criado, datetime) else date.today()
    nn = _num(b.get('charge_id')) or nosso_numero(b['id'])
    ben = _beneficiario(b['empresa_id'])
    ben['telefone'] = cfg['telefone']
    rodape = f'Emitido em {emissao.strftime("%d/%m/%Y")}.' + (' Não cobrar juros nem multa.' if not juros and not multa else '') \
        + (f' Não receber após {limite.strftime("%d/%m/%Y")}.' if limite else '')
    return bp.gerar({
        'beneficiario': ben, 'pagador': _pagador_do_boleto(b),
        'itens': [{'nome': i.get('nome') or 'Serviço', 'valor': i.get('valor') or 0} for i in itens],
        'valor': b['valor'], 'vencimento': venc, 'emissao': emissao, 'numero_documento': b.get('custom_id') or f"QC-{b['id']}",
        'nosso_numero': f"{cfg['carteira'].zfill(2)}/{nn.zfill(11)}-{bp.dv_nosso_numero_bradesco(cfg['carteira'], nn)}",
        'carteira': cfg['carteira'].zfill(2),
        'agencia_codigo': f"{cfg['agencia'].zfill(4)} / {cfg['conta'].zfill(7)}" if cfg['agencia'] else '',
        'linha_digitavel': b.get('linha_digitavel'), 'pix': b.get('pix_copia_cola') or '',
        'instrucoes': instr, 'rodape_recibo': rodape,
        'modelo': (b.get('ambiente') != 'producao'),
    })


# ---------------------------------------------------------------------------
# Baixa
# ---------------------------------------------------------------------------
def _agente(acao, b, depois):
    from utils.atividade import registrar_agente
    registrar_agente(acao, 'financeiro', None, _ATOR[0], _ATOR[1], tabela='fin_titulos',
                     registro_id=b.get('titulo_id'), depois=dict(depois, boleto=b.get('charge_id')))


def _linha_do_extrato(b, valor, de, ate):
    """O crédito do boleto na conta Bradesco: mesmo valor, na janela, sem baixa, e só um."""
    conta = _q('SELECT fc.conta FROM fin_cobranca_contas c JOIN fin_contas fc ON fc.id = c.fin_conta_id WHERE c.id = %s',
               (b['conta_cobranca_id'],), one=True)
    if not conta or not conta.get('conta'):
        return None
    ls = _q("""SELECT l.id FROM extrato_lancamentos l
                WHERE l.empresa_id = %s AND l.conta = %s AND l.valor > 0 AND ABS(l.valor - %s) < 0.005
                  AND l.data BETWEEN %s AND %s AND l.descricao NOT LIKE 'PIX RECEBIDO REM%%'
                  AND NOT EXISTS (SELECT 1 FROM fin_titulo_baixas x WHERE x.lancamento_id = l.id)""",
            (b['empresa_id'], conta['conta'], valor, de, ate))
    return ls[0] if len(ls) == 1 else None


def _baixa_paga(b, valor_pago, pago_em):
    from utils.financeiro_core import registrar_baixa, BaixaInvalida
    t = _q('SELECT id, status, valor, valor_baixado FROM fin_titulos WHERE id = %s', (b['titulo_id'],), one=True)
    if not t or t['status'] == 'cancelado':
        return None
    saldo = Decimal(str(t['valor'])) - Decimal(str(t['valor_baixado'] or 0))
    ref = f"bradesco:{b['charge_id']}"
    ja = _q('SELECT id, lancamento_id FROM fin_titulo_baixas WHERE titulo_id = %s AND referencia = %s', (t['id'], ref), one=True)
    if not ja:
        if saldo <= 0:
            return None
        pago = Decimal(str(valor_pago))
        valor, juros = (saldo, pago - saldo) if pago > saldo else (pago, Decimal('0'))
        try:
            r = registrar_baixa(t['id'], valor, pago_em, 'webhook', referencia=ref, juros=juros)
        except BaixaInvalida as exc:
            logger.warning('[bradesco-boletos] baixa do boleto %s recusada: %s', b['charge_id'], exc)
            return None
        ja = {'id': r['baixa_id'], 'lancamento_id': None}
        _agente('escrita.baixa_boleto_bradesco', b, {'valor': str(valor_pago), 'pago_em': str(pago_em),
                                                    'status': r.get('status'), 'como': 'boleto pago no Bradesco'})
    if not ja['lancamento_id']:
        lanc = _linha_do_extrato(b, valor_pago, pago_em, pago_em + timedelta(days=3))
        if lanc:
            _x('UPDATE fin_titulo_baixas SET lancamento_id = %s, confirmado_extrato = 1 WHERE id = %s AND lancamento_id IS NULL',
               (lanc['id'], ja['id']))
    return ja['id']


def aplicar(b, atual, valor_pago=None, pago_em=None):
    """Aplica a situação do Bradesco ao boleto e ao título. Devolve 'baixa' ou None."""
    from models.fin_cobranca import FinBoleto
    feito = None
    if atual == 'paid' and b.get('titulo_id') and valor_pago and pago_em:
        feito = 'baixa' if _baixa_paga(b, valor_pago, pago_em) else None
    if atual != b.get('situacao') or feito:
        FinBoleto.atualizar_situacao(b['id'], atual, pago_em=pago_em if atual == 'paid' else None,
                                     valor_pago=valor_pago if atual == 'paid' else None)
    return feito


def consultar_e_aplicar(b, brad=None):
    """Consulta UM boleto no Bradesco e aplica. Devolve (situação, feito)."""
    from models.fin_cobranca import FinCobrancaConta
    from utils.integrations.bradesco.client import situacao_da_consulta
    if brad is None:
        brad, _ = FinCobrancaConta.cliente_bradesco(b['conta_cobranca_id'])
    sit, valor, pago_em = situacao_da_consulta(brad.consultar(b['charge_id']))
    if sit == 'waiting' and b['vencimento'] and b['vencimento'] < date.today():
        sit = 'unpaid'
    return sit, aplicar(b, sit, valor, pago_em)


def cancelar(b):
    from models.fin_cobranca import FinCobrancaConta
    brad, _ = FinCobrancaConta.cliente_bradesco(b['conta_cobranca_id'])
    return brad.cancelar(b['charge_id'])


def sincronizar_conta(conta_id, com_lista=False):
    from models.fin_cobranca import FinCobrancaConta
    from utils.integrations.bradesco.client import BradescoErro, data_ddmmaaaa
    res = {'conta': conta_id, 'consultados': 0, 'baixas': 0, 'erros': []}
    brad, _ = FinCobrancaConta.cliente_bradesco(conta_id)
    abertos = _q(f"""SELECT * FROM fin_boletos WHERE conta_cobranca_id = %s AND provedor = 'bradesco'
                       AND situacao IN ({','.join(['%s'] * len(ABERTOS))}) AND charge_id IS NOT NULL""",
                 (conta_id, *ABERTOS))
    for b in abertos:
        try:
            _, feito = consultar_e_aplicar(b, brad)
            res['consultados'] += 1
            res['baixas'] += feito == 'baixa'
        except BradescoErro as exc:
            res['erros'].append(f"{b['charge_id']}: {exc}")
    if com_lista:                                   # rede de segurança: liquidados dos últimos 10 dias
        por_nn = {str(int(_num(b['charge_id']))): b for b in abertos if _num(b['charge_id'])}
        try:
            for t in brad.liquidados(date.today() - timedelta(days=10), date.today()):
                b = por_nn.get(str(int(_num(t.get('nossoNumero')) or 0)))
                if not b:
                    continue
                valor = Decimal(int(t.get('valorPagamento') or 0)) / 100
                feito = aplicar(dict(b, situacao='waiting'), 'paid', valor, data_ddmmaaaa(t.get('dataPagamento')))
                res['baixas'] += feito == 'baixa'
        except BradescoErro as exc:
            res['erros'].append(f'lista de liquidados: {exc}')
    return res


_CHAVE = 'bradesco_boletos_ultima_hora'
_CHAVE_DIA = 'bradesco_boletos_ultima_lista'


def rodar_de_hora_em_hora():
    """A cada tick do roteador: uma vez por hora, 7h-22h (Brasília), nas contas Bradesco de produção ativas;
    a lista de liquidados uma vez por dia. Desliga com BRADESCO_BOLETOS_SYNC=0."""
    from zoneinfo import ZoneInfo
    if os.getenv('BRADESCO_BOLETOS_SYNC', '1').strip() != '1':
        return None
    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    if agora.hour not in HORAS:
        return None
    contas = _q("SELECT id FROM fin_cobranca_contas WHERE provedor = 'bradesco' AND ambiente = 'producao' AND ativo = 1")
    if not contas:
        return None
    marca = agora.strftime('%Y-%m-%d %H')
    r = _q('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE,), one=True)
    if r and r.get('valor') == marca:
        return None
    _x('INSERT INTO app_config (chave, valor) VALUES (%s, %s) ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()',
       (_CHAVE, marca))
    dia = agora.strftime('%Y-%m-%d')
    r = _q('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE_DIA,), one=True)
    com_lista = not (r and r.get('valor') == dia)
    if com_lista:
        _x('INSERT INTO app_config (chave, valor) VALUES (%s, %s) ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()',
           (_CHAVE_DIA, dia))
    saida = []
    for c in contas:
        try:
            res = sincronizar_conta(c['id'], com_lista=com_lista)
        except Exception as exc:
            logger.exception('[bradesco-boletos] conta %s', c['id'])
            res = {'conta': c['id'], 'consultados': 0, 'baixas': 0, 'erros': [str(exc)]}
        saida.append(res)
        logger.warning('[bradesco-boletos] conta %s: %s consultado(s), %s baixa(s), %s erro(s)',
                       res['conta'], res['consultados'], res['baixas'], len(res['erros']))
    return saida
