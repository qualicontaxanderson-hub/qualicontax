# -*- coding: utf-8 -*-
"""Extrato do Cora pela API — entra sozinho de 30 em 30 minutos (18/11/2026).

Anderson (18/11/2026): "Vamos montar o processo para o API do Cora para cobrança e extratos".

Como funciona (igual ao Bradesco, ``utils/bradesco_extrato.py``)
---------------------------------------------------------------
A conta bancária que tem a cobrança do Cora ligada (fin_cobranca_contas.fin_conta_id, ativa) usa a MESMA
credencial (certificado + chave + client id) para ``GET /bank-statement/statement``. De 30 em 30 minutos,
das 7h às 22h (CORA_EXTRATO_MINUTOS muda o intervalo; mínimo 5, a volta do roteador):

1. pede ao banco os lançamentos dos últimos 7 dias até hoje;
2. grava pelo MESMO núcleo do OFX (``processar_lancamentos``): a descrição sai no formato do OFX do Cora
   ("Transf Pix recebida - FULANO - 000.000.000-00"), então regras de classificação e a camada data +
   valor que não deixa duplicar o que o OFX já trouxe valem igual;
3. para cada crédito ainda sem título: primeiro procura UM boleto nosso do Cora (mesma conta, mesmo
   valor, mesmo CPF/CNPJ do pagador) e baixa/amarra por ele; senão, o CPF/CNPJ de quem mandou (o Cora
   escreve o documento, não só o nome) acha o cadastro e, se há UM título aberto com saldo igual, dá a
   baixa. Qualquer dúvida fica no Extrato para o "Vincular".

Desliga com CORA_EXTRATO_ATIVO=0. O último resultado fica em app_config (``cora_extrato_ultimo``) e
aparece no cartão da conta.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

ORIGEM = 'cora'             # extrato_lancamentos.origem (varchar 10)
BANCO_ID = '403'
BANCO_NOME = 'Cora'
REF = 'cora-extrato:'       # referência da baixa: id do lançamento no Cora
JANELA_ANTES, JANELA_DEPOIS = 60, 35
DIAS = 7
HORAS = range(7, 23)
_CHAVE_HORA = 'cora_extrato_ultima_vez'
_CHAVE_RES = 'cora_extrato_ultimo'
_ATOR = ('EXTRATO CORA (automático)', 'extrato_cora')


def _q(sql, p=(), one=False):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=True, fetch_one=one) if one else (execute_query(sql, p, fetch=True) or [])


def _x(sql, p=()):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=False)


def _num(s):
    return re.sub(r'\D', '', str(s or ''))


def _doc_fmt(doc):
    d = _num(doc)
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    if len(d) == 11:
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    return d


# ---------------------------------------------------------------------------
# Leitura da resposta da API
# ---------------------------------------------------------------------------
def _data_local(s):
    from utils.integrations.cora.client import _dia
    return _dia(s)


def ler(j):
    """Resposta da API -> lista de lançamentos no formato do parser de OFX. BLOCK/UNBLOCK (bloqueio
    judicial, reserva) não são movimento e ficam de fora. A descrição segue o OFX do Cora:
    '<descrição> - <nome> - <CPF/CNPJ formatado>'."""
    out = []
    for e in (j or {}).get('entries') or []:
        tipo = str(e.get('type') or '').upper()
        if tipo not in ('CREDIT', 'DEBIT'):
            continue
        cent = int(e.get('amount') or 0)
        if not cent:
            continue
        valor = (Decimal(cent) / 100).quantize(Decimal('0.01'))
        if tipo == 'DEBIT':
            valor = -valor
        data = _data_local(e.get('createdAt'))
        if not data:
            continue
        tr = e.get('transaction') or {}
        cp = tr.get('counterParty') or {}
        partes = [re.sub(r'\s+', ' ', str(tr.get('description') or '')).strip()]
        if cp.get('name'):
            partes.append(re.sub(r'\s+', ' ', str(cp['name'])).strip())
        if _num(cp.get('identity')):
            partes.append(_doc_fmt(cp['identity']))
        desc = ' - '.join(p for p in partes if p) or (tr.get('type') or tipo)
        out.append({'data': data, 'valor': valor, 'tipo': 'CREDIT' if valor > 0 else 'DEBIT',
                    'descricao': desc[:500], 'documento': None, 'fitid': None,
                    '_cora_id': e.get('id'), '_doc': _num(cp.get('identity')), '_tipo_tx': tr.get('type')})
    return out


def documento_da_descricao(desc):
    """'Transf Pix recebida - FULANO - 123.456.789-00' -> '12345678900' (CPF ou CNPJ de quem mandou)."""
    m = re.search(r'(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2})\s*$', str(desc or ''))
    return _num(m.group(1)) if m else None


def nome_da_descricao(desc):
    partes = [p.strip() for p in str(desc or '').split(' - ')]
    return partes[1] if len(partes) >= 2 else None


def e_recebimento(desc):
    d = str(desc or '').lower()
    return 'recebid' in d or 'boleto' in d and 'pag' in d


# ---------------------------------------------------------------------------
# Contas
# ---------------------------------------------------------------------------
def contas(fin_conta_id=None):
    """Contas bancárias Cora com a cobrança ligada (credencial + certificado)."""
    sql = """SELECT c.id AS cob_id, c.empresa_id, c.ambiente, f.id AS fin_conta_id, f.agencia, f.conta
               FROM fin_cobranca_contas c JOIN fin_contas f ON f.id = c.fin_conta_id
              WHERE c.provedor = 'cora' AND c.ativo = 1 AND COALESCE(c.so_consulta, 0) = 0 AND f.conta IS NOT NULL"""
    p = ()
    if fin_conta_id:
        sql += ' AND f.id = %s'
        p = (fin_conta_id,)
    return _q(sql + ' ORDER BY c.id', p)


def ultimos():
    r = _q('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE_RES,), one=True)
    try:
        return {int(k): v for k, v in json.loads((r or {}).get('valor') or '{}').items()}
    except (ValueError, TypeError):
        return {}


def _guardar(fin_conta_id, res):
    tudo = ultimos()
    tudo[fin_conta_id] = res
    _x('INSERT INTO app_config (chave, valor) VALUES (%s, %s) '
       'ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()',
       (_CHAVE_RES, json.dumps({str(k): v for k, v in tudo.items()}, ensure_ascii=False, default=str)))


# ---------------------------------------------------------------------------
# Baixa do crédito
# ---------------------------------------------------------------------------
def _boleto_da_linha(lanc, doc):
    """O crédito é de UM boleto nosso do Cora? Mesma conta, mesmo valor, mesmo CPF/CNPJ do pagador, emitido até
    a data da linha e ainda sem linha amarrada. Devolve o boleto (com baixa_id/lancamento_id) ou None."""
    from utils.extrato_ingest import mesma_conta
    bs = _q("""SELECT b.*, fc.conta AS conta_fin, x.id AS baixa_id, x.lancamento_id AS baixa_lanc
                 FROM fin_boletos b
                 JOIN fin_cobranca_contas c ON c.id = b.conta_cobranca_id
                 JOIN fin_contas fc ON fc.id = c.fin_conta_id
                 LEFT JOIN fin_titulo_baixas x ON x.titulo_id = b.titulo_id
                      AND x.referencia COLLATE utf8mb4_unicode_ci = CONCAT('cora:', b.charge_id) COLLATE utf8mb4_unicode_ci
                WHERE b.provedor = 'cora' AND b.empresa_id = %s AND b.titulo_id IS NOT NULL
                  AND b.situacao IN ('new', 'waiting', 'unpaid', 'paid') AND ABS(b.valor - %s) < 0.005
                  AND DATE(b.criado_em) <= %s AND (x.id IS NULL OR x.lancamento_id IS NULL)""",
            (lanc['empresa_id'], Decimal(str(lanc['valor'])), lanc['data']))
    achados = [b for b in bs if mesma_conta(lanc['conta'], b['conta_fin'])
               and (not doc or not _num(b.get('pagador_doc')) or _num(b.get('pagador_doc')) == doc)]
    if doc:
        exatos = [b for b in achados if _num(b.get('pagador_doc')) == doc]
        if exatos:
            achados = exatos
    return achados[0] if len(achados) == 1 else None


def _baixar_pelo_boleto(lanc, b, seco):
    from utils.atividade import registrar_agente
    if seco:
        return 'baixaria', {'titulo_id': b['titulo_id'], 'nome': b.get('pagador_nome'), 'boleto': b['charge_id']}
    if b.get('baixa_id'):
        _x('UPDATE fin_titulo_baixas SET lancamento_id = %s, confirmado_extrato = 1 WHERE id = %s AND lancamento_id IS NULL',
           (lanc['id'], b['baixa_id']))
        como = 'ja-baixado'
    else:
        from utils.cora_boletos import aplicar
        feito = aplicar(b, 'paid', Decimal(str(lanc['valor'])), lanc['data'])
        if feito != 'baixa':
            return 'empate', {'nome': b.get('pagador_nome'), 'boleto': b['charge_id'], 'motivo': 'baixa recusada'}
        como = 'baixado'
    _x("""UPDATE extrato_lancamentos SET par_estado = 'liberado', par_liberado_motivo = %s, par_liberado_em = NOW()
           WHERE id = %s AND par_estado IN ('casado', 'suspeito')""",
       (f"Recebimento do boleto Cora {b['charge_id']} (título {b['titulo_id']})", lanc['id']))
    registrar_agente('escrita.baixa_cora_extrato', 'financeiro', None, _ATOR[0], _ATOR[1],
                     tabela='fin_titulos', registro_id=b['titulo_id'],
                     depois={'valor': str(lanc['valor']), 'data': str(lanc['data']), 'quem_mandou': b.get('pagador_nome'),
                             'lancamento_id': lanc['id'], 'boleto': b['charge_id'],
                             'como': 'crédito do boleto: mesma conta, valor e pagador' + (' (baixa já existia; só amarrou)' if como == 'ja-baixado' else '')})
    return como, {'titulo_id': b['titulo_id'], 'nome': b.get('pagador_nome'), 'boleto': b['charge_id']}


def clientes_pelo_documento(doc):
    d = _num(doc)
    if len(d) not in (11, 14):
        return []
    rs = _q("SELECT id FROM clientes WHERE REGEXP_REPLACE(COALESCE(cpf_cnpj, ''), '[^0-9]', '') = %s "
            "   AND COALESCE(situacao, 'ATIVO') <> 'INATIVO'", (d,))
    return sorted({r['id'] for r in rs})


def _do_grupo():
    from models.fin_empresa import FinEmpresa
    return set(FinEmpresa.ids_validos())


def candidatos(empresa_id, data, valor, cliente_ids):
    if not cliente_ids:
        return []
    marks = ','.join(['%s'] * len(cliente_ids))
    return _q(f"""SELECT t.id, t.cliente_id, t.contraparte_nome, t.vencimento FROM fin_titulos t
                   WHERE t.empresa_id = %s AND t.tipo = 'R' AND t.status IN ('aberto', 'parcial')
                     AND ABS((t.valor - t.valor_baixado) - %s) < 0.005
                     AND t.vencimento BETWEEN %s AND %s AND t.cliente_id IN ({marks})
                   ORDER BY t.vencimento""",
              (empresa_id, valor, data - timedelta(days=JANELA_ANTES), data + timedelta(days=JANELA_DEPOIS), *cliente_ids))


def _baixas_a_mao(empresa_id, data, valor, cliente_ids):
    if not cliente_ids:
        return []
    marks = ','.join(['%s'] * len(cliente_ids))
    return _q(f"""SELECT b.id, b.titulo_id, t.contraparte_nome AS nome FROM fin_titulo_baixas b
                    JOIN fin_titulos t ON t.id = b.titulo_id
                   WHERE t.empresa_id = %s AND b.origem = 'manual' AND b.lancamento_id IS NULL
                     AND b.data_baixa BETWEEN %s AND %s AND t.cliente_id IN ({marks})
                     AND ABS(b.valor + b.juros + b.multa - b.desconto - %s) < 0.005""",
              (empresa_id, data - timedelta(days=6), data, *cliente_ids, valor))


def baixar(lanc, seco=True):
    """Decide a baixa de UM crédito. Devolve (situação, detalhe)."""
    valor = Decimal(str(lanc['valor']))
    desc = lanc.get('descricao') or ''
    if valor <= 0 or not e_recebimento(desc):
        return 'ignorado', None
    doc = documento_da_descricao(desc)
    nome = nome_da_descricao(desc)
    if lanc.get('id') and _q('SELECT 1 x FROM fin_titulo_baixas WHERE lancamento_id = %s LIMIT 1', (lanc['id'],), one=True):
        return 'ja-amarrado', None
    b = _boleto_da_linha(lanc, doc)                  # o boleto é a prova mais forte
    if b:
        return _baixar_pelo_boleto(lanc, b, seco)
    clis = clientes_pelo_documento(doc)
    grupo = _do_grupo()
    if clis and all(c in grupo for c in clis):
        return 'transferencia', {'nome': nome}
    clis = [c for c in clis if c not in grupo]
    if not clis:
        return 'sem-cadastro', {'nome': nome}
    mao = _baixas_a_mao(lanc['empresa_id'], lanc['data'], valor, clis)
    if len(mao) == 1:
        if not seco and lanc.get('id'):
            _x('UPDATE fin_titulo_baixas SET lancamento_id = %s, confirmado_extrato = 1 '
               'WHERE id = %s AND lancamento_id IS NULL', (lanc['id'], mao[0]['id']))
        return 'ja-baixado', {'titulo_id': mao[0]['titulo_id'], 'nome': mao[0]['nome']}
    if mao:
        return 'empate', {'nome': nome, 'baixas_a_mao': len(mao)}
    cands = candidatos(lanc['empresa_id'], lanc['data'], valor, clis)
    if len(cands) != 1:
        return ('empate' if cands else 'sem-titulo'), {'nome': nome, 'candidatos': len(cands)}
    t = cands[0]
    if seco:
        return 'baixaria', {'titulo_id': t['id'], 'nome': t['contraparte_nome']}
    from utils.financeiro_core import registrar_baixa
    from utils.atividade import registrar_agente
    ref = f"{REF}{lanc.get('_cora_id') or lanc.get('id')}:{lanc['data']}"
    r = registrar_baixa(t['id'], valor, lanc['data'], 'extrato', referencia=ref, lancamento_id=lanc['id'])
    registrar_agente('escrita.baixa_cora_extrato', 'financeiro', None, _ATOR[0], _ATOR[1],
                     tabela='fin_titulos', registro_id=t['id'],
                     depois={'valor': str(valor), 'data': str(lanc['data']), 'quem_mandou': nome, 'documento': doc,
                             'lancamento_id': lanc['id'], 'status': (r or {}).get('status'),
                             'como': 'CPF/CNPJ de quem mandou + valor igual ao saldo do único título aberto'})
    return 'baixado', {'titulo_id': t['id'], 'nome': t['contraparte_nome']}


# ---------------------------------------------------------------------------
# Rodada
# ---------------------------------------------------------------------------
def rodar(seco=True, de=None, ate=None, fin_conta_id=None):
    """Uma rodada. Em ``seco`` só consulta o banco e diz o que faria. Devolve {fin_conta_id: resumo}."""
    from models.fin_cobranca import FinCobrancaConta
    from utils.extrato_ingest import processar_lancamentos, ja_existe_por_data_valor, mesma_conta
    from utils.ofx_parser import chave_dedup
    from models.extrato_lancamento import ExtratoLancamento
    hoje = date.today()
    ate = ate or hoje
    de = de or (ate - timedelta(days=DIAS - 1))
    saida = {}
    for cc in contas(fin_conta_id):
        res = {'em': datetime.now().strftime('%Y-%m-%d %H:%M'), 'de': str(de), 'ate': str(ate), 'lidos': 0,
               'novos': 0, 'baixados': [], 'pendentes': [], 'erro': None, 'seco': seco}
        saida[cc['fin_conta_id']] = res
        try:
            cora, _c = FinCobrancaConta.cliente_cora(cc['cob_id'])
            lancs = ler(cora.extrato(de, ate))
        except Exception as exc:
            logger.warning('[cora-extrato] conta %s: %s', cc['fin_conta_id'], exc)
            res['erro'] = str(exc)[:300]
            if not seco:
                _guardar(cc['fin_conta_id'], res)
            continue
        res['lidos'] = len(lancs)
        emp = cc['empresa_id']
        limpos = [{k: v for k, v in l.items() if not k.startswith('_')} for l in lancs]
        dados = {'banco_id': BANCO_ID, 'banco': BANCO_NOME, 'conta': cc['conta'], 'lancamentos': limpos}
        if seco:
            from utils.extrato_ingest import achar_conta, conta_do_cadastro
            reg = achar_conta(BANCO_ID, cc['conta'])
            conta = (conta_do_cadastro(reg) if reg else None) or cc['conta']
            banco = (reg or {}).get('banco_nome') or BANCO_NOME
            rep, cand = {}, []
            for l in limpos:
                k = (l['data'], str(l['valor']), l['descricao'], l.get('documento'))
                cand.append((chave_dedup(emp, banco, conta, l, rep.get(k, 0)), l))
                rep[k] = rep.get(k, 0) + 1
            ja = ExtratoLancamento.hashes_existentes([h for h, _ in cand])
            novos, _r = ja_existe_por_data_valor(emp, conta, [(h, l) for h, l in cand if h not in ja],
                                                 ja_casados=[l for h, l in cand if h in ja])
            res['novos'] = len(novos)
            res['novos_lista'] = [{'data': str(l['data']), 'valor': str(l['valor']), 'descricao': l['descricao']}
                                  for _h, l in novos]
            alvo = [dict(l, empresa_id=emp, id=None, conta=conta) for l in lancs]
        else:
            r = processar_lancamentos(dados, emp, f'API Cora {de:%d/%m}-{ate:%d/%m}', usuario_id=None, origem=ORIGEM)
            res['novos'] = r['novos']
            alvo = [l for l in _q("""SELECT * FROM extrato_lancamentos
                                       WHERE empresa_id = %s AND valor > 0 AND data BETWEEN %s AND %s
                                       ORDER BY data, id""", (emp, de, ate))
                    if mesma_conta(l['conta'], r['conta'])]
        for l in alvo:
            try:
                s, d = baixar(l, seco=seco)
            except Exception as exc:
                logger.exception('[cora-extrato] baixa falhou (lançamento %s)', l.get('id'))
                s, d = 'erro', {'erro': str(exc)[:200]}
            if s == 'ignorado':
                continue
            linha = {'data': str(l['data']), 'valor': str(l['valor']), 'nome': (d or {}).get('nome') or nome_da_descricao(l.get('descricao'))}
            if s in ('baixado', 'baixaria', 'ja-baixado'):
                res['baixados'].append(dict(linha, titulo_id=(d or {}).get('titulo_id'), como=s))
            elif s in ('empate', 'sem-titulo', 'sem-cadastro', 'erro'):
                res['pendentes'].append(dict(linha, motivo=s))
        if not seco:
            _guardar(cc['fin_conta_id'], res)
    return saida


def rodar_de_hora_em_hora():
    """Chamado a cada tick do roteador (5 em 5 minutos): das 7h às 22h (Brasília), uma vez a cada
    CORA_EXTRATO_MINUTOS (padrão 30)."""
    import os
    from zoneinfo import ZoneInfo
    if os.getenv('CORA_EXTRATO_ATIVO', '1').strip() != '1':
        return None
    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    if agora.hour not in HORAS:
        return None
    try:
        passo = max(5, int(os.getenv('CORA_EXTRATO_MINUTOS', '30')))
    except ValueError:
        passo = 30
    minuto_do_dia = agora.hour * 60 + agora.minute
    marca = f"{agora:%Y-%m-%d} {minuto_do_dia // passo}"
    r = _q('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE_HORA,), one=True)
    if r and r.get('valor') == marca:
        return None
    if not contas():
        return None
    _x('INSERT INTO app_config (chave, valor) VALUES (%s, %s) '
       'ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()', (_CHAVE_HORA, marca))
    hoje = agora.date()
    res = rodar(seco=False, de=hoje - timedelta(days=DIAS - 1), ate=hoje)
    for fid, x in res.items():
        if x['novos'] or x['baixados'] or x['erro']:
            logger.warning('[cora-extrato] conta %s: %s lido(s), %s novo(s), %s baixa(s), %s para escolher%s',
                           fid, x['lidos'], x['novos'], len(x['baixados']), len(x['pendentes']),
                           f" — ERRO {x['erro']}" if x['erro'] else '')
    return res
