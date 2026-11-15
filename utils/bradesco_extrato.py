# -*- coding: utf-8 -*-
"""Extrato do Bradesco pela API — entra sozinho todo dia (15/11/2026).

Anderson (15/11/2026): "pode começar" (passo 5 do caminho aprovado: "Depois ligamos o extrato pela API").

Como funciona
-------------
A conta bancária que tem a cobrança do Bradesco ligada (fin_cobranca_contas.fin_conta_id, produção,
ativa) usa a MESMA credencial e o MESMO certificado A1 para a API Saldo e Extrato (assinatura de
produção aprovada em 13/11). De 30 em 30 minutos, das 7h às 22h — Anderson: "não pode puxar o dia
todo para ficar praticamente on line?"; "Pode puxar o Bradesco de 30 em 30 minutos". O roteador roda
de 5 em 5: BRADESCO_EXTRATO_MINUTOS muda o intervalo (mínimo 5):

1. pede ao banco os lançamentos dos últimos 7 dias ATÉ HOJE (só o "extratoPorPeriodo", que traz o que
   já foi lançado; os "últimos lançamentos" trazem tarifa com data futura e ficam de fora);
2. grava pelo MESMO núcleo do OFX (``processar_lancamentos``): a descrição sai igual à do OFX
   ("PIX RECEBIDO REM: FULANO 06/11" = descritivo + segunda linha), o documento é o mesmo, e a
   camada data + valor não deixa duplicar o que o OFX já trouxe (o Bradesco não tem FITID estável);
   regras de classificação e detector de pares valem igual;
3. para cada PIX RECEBIDO ainda sem título: se o nome de quem mandou é de UM cadastro e esse
   cadastro tem UM título em aberto com o saldo igual ao valor, dá a baixa. Baixa à mão do mesmo
   cliente e valor (sem linha do extrato) se amarra à linha em vez de baixar outro título. Qualquer
   dúvida (nome de mais de um cadastro, dois títulos, nenhum) fica no Extrato para o "Vincular".

Desliga com BRADESCO_EXTRATO_ATIVO=0. O último resultado fica em app_config
(``bradesco_extrato_ultimo``) e aparece no cartão da conta.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

ORIGEM = 'bradesco'         # extrato_lancamentos.origem (varchar 10)
BANCO_ID = '237'
BANCO_NOME = 'Bradesco'
REF = 'brad-extrato:'       # referência da baixa: documento + data do lançamento
JANELA_ANTES, JANELA_DEPOIS = 60, 35   # vencimento entre data-60 e data+35 (igual à Efí)
DIAS = 7
HORAS = range(7, 23)        # 7h às 22h (Brasília)
_CHAVE_HORA = 'bradesco_extrato_ultima_vez'
_CHAVE_RES = 'bradesco_extrato_ultimo'
_ATOR = ('EXTRATO BRADESCO (automático)', 'extrato_bradesco')


def _q(sql, p=(), one=False):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=True, fetch_one=one) if one else (execute_query(sql, p, fetch=True) or [])


def _x(sql, p=()):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=False)


def _num(s):
    return re.sub(r'\D', '', str(s or ''))


# ---------------------------------------------------------------------------
# Leitura da resposta da API
# ---------------------------------------------------------------------------
def _valor(s, sinal):
    v = Decimal(str(s or '0').replace('.', '').replace(',', '.'))
    return -v if (sinal or '').strip() == '-' else v


def ler(j):
    """Resposta da API -> lista de lançamentos no formato do parser de OFX (sem as linhas de saldo).

    Além do período, entra o grupo "Lancamentos Dia" dos últimos lançamentos: é onde o Pix recebido
    aparece NA HORA (15/11/2026, domingo: o Pix de R$ 100 do Anderson veio só ali, com a data do
    próximo dia útil, 16/11, e o período ainda não o tinha). Vem com a mesma data, documento e
    descrição que o período trará depois, então não duplica. A tarifa agendada ("Ultimos
    Lancamentos", data futura) continua de fora até o banco lançar."""
    por = (j or {}).get('extratoPorPeriodo') or {}
    ult = ((j or {}).get('extratoUltimosLancamentos') or {}).get('listaLancamentos') or {}
    dia = [l for g, ls in ult.items() if 'dia' in str(g).lower() for l in (ls or [])]
    out, vistos = [], set()
    for l in list(por.get('lstLancamentoMensal') or []) + dia:
        desc1 = re.sub(r'\s+', ' ', str(l.get('descritivoLancamentoCompleto') or l.get('descritivoLancamentoAbreviado') or '')).strip()
        if not desc1 or 'SALDO' in desc1.upper():
            continue
        desc2 = re.sub(r'\s+', ' ', str(l.get('segundaLinhalLancamento') or '')).strip()
        d = str(l.get('dataLancamento') or '')
        try:
            data = date(int(d[6:10]), int(d[3:5]), int(d[0:2]))
        except ValueError:
            continue
        valor = _valor(l.get('valorLancamento'), l.get('sinalLancamento'))
        if not valor:
            continue
        doc = _num(l.get('numeroDocumento')).lstrip('0') or None
        chave = (data, valor, doc, desc1, desc2)
        if chave in vistos:                       # o mesmo lançamento no período e no "do dia"
            continue
        vistos.add(chave)
        out.append({'data': data, 'valor': valor, 'tipo': 'CREDIT' if valor > 0 else 'DEBIT',
                    'descricao': f'{desc1} {desc2}'.strip()[:500], 'documento': doc, 'fitid': None})
    return out


# ---------------------------------------------------------------------------
# Contas
# ---------------------------------------------------------------------------
def contas(fin_conta_id=None):
    """Contas bancárias Bradesco com a cobrança de produção ligada (credencial + certificado)."""
    sql = """SELECT c.id AS cob_id, c.empresa_id, f.id AS fin_conta_id, f.agencia, f.conta
               FROM fin_cobranca_contas c JOIN fin_contas f ON f.id = c.fin_conta_id
              WHERE c.provedor = 'bradesco' AND c.ambiente = 'producao' AND c.ativo = 1
                AND COALESCE(c.so_consulta, 0) = 0 AND f.agencia IS NOT NULL AND f.conta IS NOT NULL"""
    p = ()
    if fin_conta_id:
        sql += ' AND f.id = %s'
        p = (fin_conta_id,)
    return _q(sql + ' ORDER BY c.id', p)


def ultimos():
    """{fin_conta_id: {em, de, ate, lidos, novos, baixados, pendentes, erro}} da última puxada."""
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
# Baixa do Pix recebido
# ---------------------------------------------------------------------------
def nome_do_pix(desc):
    """'PIX RECEBIDO REM: HENRIQUE SOUZA VICENT 06/11' -> 'HENRIQUE SOUZA VICENT' (o banco corta o nome)."""
    m = re.match(r'^PIX RECEBIDO\s+REM:\s*(.+?)(?:\s+\d{2}/\d{2})?\s*$', str(desc or ''), re.I)
    return m.group(1).strip() if m else None


def clientes_pelo_nome(nome):
    """Cadastros cujo nome começa com o que o banco escreveu (só com 10+ letras: nome curto casa demais)."""
    n = re.sub(r'\s+', ' ', nome or '').strip()
    if len(n) < 10:
        return []
    like = n.replace('\\', '').replace('%', '').replace('_', r'\_') + '%'
    rs = _q("SELECT id FROM clientes WHERE nome_razao_social LIKE %s COLLATE utf8mb4_unicode_ci "
            "   OR REPLACE(nome_razao_social, '.', '') LIKE %s COLLATE utf8mb4_unicode_ci", (like, like))
    return sorted({r['id'] for r in rs})


def _do_grupo():
    """Cadastros das empresas do próprio financeiro (empresa_id = clientes.id): Pix entre elas é transferência."""
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
    """Baixa dada à mão (sem linha do extrato) do mesmo cliente e valor, até 6 dias antes: o dinheiro é dela."""
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
    """Decide a baixa de UM lançamento. Devolve (situação, detalhe)."""
    valor = Decimal(str(lanc['valor']))
    nome = nome_do_pix(lanc.get('descricao'))
    if valor <= 0 or not nome:
        return 'ignorado', None
    if lanc.get('id') and _q('SELECT 1 x FROM fin_titulo_baixas WHERE lancamento_id = %s LIMIT 1', (lanc['id'],), one=True):
        return 'ja-amarrado', None
    clis = clientes_pelo_nome(nome)
    grupo = _do_grupo()
    if clis and all(c in grupo for c in clis):
        return 'transferencia', {'nome': nome}       # Pix entre as empresas do grupo não é recebimento
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
    ref = f"{REF}{lanc.get('documento') or ''}:{lanc['data']}"
    r = registrar_baixa(t['id'], valor, lanc['data'], 'extrato', referencia=ref, lancamento_id=lanc['id'])
    registrar_agente('escrita.baixa_bradesco_extrato', 'financeiro', None, _ATOR[0], _ATOR[1],
                     tabela='fin_titulos', registro_id=t['id'],
                     depois={'valor': str(valor), 'data': str(lanc['data']), 'quem_mandou': nome,
                             'lancamento_id': lanc['id'], 'status': (r or {}).get('status'),
                             'como': 'nome do Pix + valor igual ao saldo do único título aberto'})
    return 'baixado', {'titulo_id': t['id'], 'nome': t['contraparte_nome']}


# ---------------------------------------------------------------------------
# Rodada
# ---------------------------------------------------------------------------
def rodar(seco=True, de=None, ate=None, fin_conta_id=None):
    """Uma rodada. Em ``seco`` só consulta o banco e diz o que faria. Devolve {fin_conta_id: resumo}."""
    from models.fin_cobranca import FinCobrancaConta
    from utils.extrato_ingest import processar_lancamentos, ja_existe_por_data_valor
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
            brad, _c = FinCobrancaConta.cliente_bradesco(cc['cob_id'])
            lancs = ler(brad.extrato(cc['agencia'], cc['conta'], de, ate))
        except Exception as exc:
            logger.warning('[bradesco-extrato] conta %s: %s', cc['fin_conta_id'], exc)
            res['erro'] = str(exc)[:300]
            if not seco:
                _guardar(cc['fin_conta_id'], res)
            continue
        res['lidos'] = len(lancs)
        emp = cc['empresa_id']
        dados = {'banco_id': BANCO_ID, 'banco': BANCO_NOME, 'conta': cc['conta'], 'lancamentos': lancs}
        if seco:
            # mesma conta que o núcleo usaria; conta o que seria novo sem gravar
            from utils.extrato_ingest import achar_conta, conta_do_cadastro
            reg = achar_conta(BANCO_ID, cc['conta'])
            conta = (conta_do_cadastro(reg) if reg else None) or cc['conta']
            banco = (reg or {}).get('banco_nome') or BANCO_NOME
            rep, cand = {}, []
            for l in lancs:
                k = (l['data'], str(l['valor']), l['descricao'], l.get('documento'))
                cand.append((chave_dedup(emp, banco, conta, l, rep.get(k, 0)), l))
                rep[k] = rep.get(k, 0) + 1
            ja = ExtratoLancamento.hashes_existentes([h for h, _ in cand])
            novos, _r = ja_existe_por_data_valor(emp, conta, [(h, l) for h, l in cand if h not in ja],
                                                 ja_casados=[l for h, l in cand if h in ja])
            res['novos'] = len(novos)
            res['novos_lista'] = [{'data': str(l['data']), 'valor': str(l['valor']), 'descricao': l['descricao']}
                                  for _h, l in novos]
            alvo = [dict(l, empresa_id=emp, id=None) for l in lancs]
        else:
            r = processar_lancamentos(dados, emp, f'API Bradesco {de:%d/%m}-{ate:%d/%m}', usuario_id=None, origem=ORIGEM)
            res['novos'] = r['novos']
            # todo PIX RECEBIDO da conta na janela que ainda não tem título (venha da API ou do OFX)
            from utils.extrato_ingest import mesma_conta
            alvo = [l for l in _q("""SELECT * FROM extrato_lancamentos
                                       WHERE empresa_id = %s AND valor > 0 AND data BETWEEN %s AND %s
                                         AND descricao LIKE 'PIX RECEBIDO%%' ORDER BY data, id""", (emp, de, ate))
                    if mesma_conta(l['conta'], r['conta'])]
        for l in alvo:
            try:
                s, d = baixar(l, seco=seco)
            except Exception as exc:
                logger.exception('[bradesco-extrato] baixa falhou (lançamento %s)', l.get('id'))
                s, d = 'erro', {'erro': str(exc)[:200]}
            linha = {'data': str(l['data']), 'valor': str(l['valor']), 'nome': (d or {}).get('nome') or nome_do_pix(l.get('descricao'))}
            if s in ('baixado', 'baixaria', 'ja-baixado'):
                res['baixados'].append(dict(linha, titulo_id=(d or {}).get('titulo_id'), como=s))
            elif s in ('empate', 'sem-titulo', 'sem-cadastro', 'erro'):
                res['pendentes'].append(dict(linha, motivo=s))
        if not seco:
            _guardar(cc['fin_conta_id'], res)
    return saida


def rodar_de_hora_em_hora():
    """Chamado a cada tick do roteador (5 em 5 minutos): das 7h às 22h (Brasília), uma vez a cada
    BRADESCO_EXTRATO_MINUTOS (padrão 30). O nome ficou; o intervalo é o da variável."""
    import os
    from zoneinfo import ZoneInfo
    if os.getenv('BRADESCO_EXTRATO_ATIVO', '1').strip() != '1':
        return None
    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    if agora.hour not in HORAS:
        return None
    try:
        passo = max(5, int(os.getenv('BRADESCO_EXTRATO_MINUTOS', '30')))
    except ValueError:
        passo = 30
    minuto_do_dia = agora.hour * 60 + agora.minute
    marca = f"{agora:%Y-%m-%d} {minuto_do_dia // passo}"
    r = _q('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE_HORA,), one=True)
    if r and r.get('valor') == marca:
        return None
    if not contas():
        return None
    # marca ANTES de rodar: duas voltas do roteador na mesma fatia não puxam duas vezes
    _x('INSERT INTO app_config (chave, valor) VALUES (%s, %s) '
       'ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()', (_CHAVE_HORA, marca))
    hoje = agora.date()
    res = rodar(seco=False, de=hoje - timedelta(days=DIAS - 1), ate=hoje)
    for fid, x in res.items():
        if x['novos'] or x['baixados'] or x['erro']:
            logger.warning('[bradesco-extrato] conta %s: %s lido(s), %s novo(s), %s baixa(s), %s para escolher%s',
                           fid, x['lidos'], x['novos'], len(x['baixados']), len(x['pendentes']),
                           f" — ERRO {x['erro']}" if x['erro'] else '')
    return res
