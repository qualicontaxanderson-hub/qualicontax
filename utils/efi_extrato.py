# -*- coding: utf-8 -*-
"""Extrato diário da Efí pela API — entra sozinho e dá baixa no que dá (12/11/2026).

Anderson (12/11/2026): "Ao receber na conta é pra dar baixa !!"

Como funciona
-------------
A Efí gera todo dia, sozinha, o CNAB 240 do dia anterior (agendamento ativo
desde 04/09/2025; API Extratos, mTLS). Este módulo:

1. baixa os arquivos que ainda não entraram e lê os lançamentos (segmento E);
2. grava pelo MESMO núcleo do OFX (``processar_lancamentos``) — mesma
   deduplicação, mesmas regras de classificação, mesmo detector de pares
   (o Pix enviado ao Sicredi casa com a entrada lá);
3. para cada "Recebimento de cobrança":
   * se é boleto NOSSO que o webhook já baixou, só amarra o lançamento na baixa;
   * se o valor bate com UM ÚNICO título do Conexa em aberto, dá a baixa;
   * se bate com mais de um (ou nenhum), NÃO chuta: fica no Extrato sem título,
     para alguém escolher o cliente.

O que o CNAB não tem
--------------------
O nome de quem pagou. O OFX da Efí tem ("Recebimento de cobrança: 1069613642
de MEGA TERCEIRIZACAO ..."), e o protocolo do OFX é o MESMO número do CNAB.
Se depois alguém mandar o OFX, ``completar_pelo_ofx`` põe o nome no lançamento
que o CNAB criou (em vez de descartar a linha como repetida) e, com o nome,
resolve pelo CNPJ/nome o que o valor sozinho deixou empatado.

Nunca usa número do Conexa nem de planilha: o título é achado pelo valor e,
quando há nome, pelo cadastro (cliente_id) — regra de 06/11.
"""
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)

ORIGEM = 'efi'              # extrato_lancamentos.origem (varchar 10)
BANCO_ID = '364'
BANCO_NOME = 'Efí'
REF = 'efi-cnab:'           # referência da baixa: o protocolo do lançamento
JANELA_ANTES, JANELA_DEPOIS = 60, 35   # vencimento entre data-60 e data+35
_ATOR = ('EXTRATO EFÍ (automático)', 'extrato_efi')

# histórico do CNAB -> descrição no padrão do OFX da Efí (as regras do
# Extrato já classificam por esses textos: "Tarifa confirmação Boleto:" etc.)
_HIST = {
    'RECEBIMENTO DE COBRANCA': 'Recebimento de cobrança:',
    'TARIFA CONFIRMACAO BOLETO': 'Tarifa confirmação Boleto:',
    'TARIFA CONFIRMACAO BOLIX': 'Tarifa confirmação Bolix:',
    'TARIFA CONFIRMACAO LOTERI': 'Tarifa confirmação Lotérica:',
    'PIX ENVIADO VIA CHAVE': 'Pix enviado via chave:',
}


# ---------------------------------------------------------------------------
# Leitura do CNAB 240 (layout de extrato FEBRABAN, como a Efí escreve)
# ---------------------------------------------------------------------------
def _data(s):
    return date(int(s[4:8]), int(s[2:4]), int(s[0:2]))


def ler_cnab(txt):
    """{'banco_id','banco','conta','lancamentos':[...]} no formato do parser
    de OFX. Posições conferidas no arquivo real de 10/11/2026."""
    conta, lancs = None, []
    for ln in (txt or '').splitlines():
        if len(ln) < 200 or ln[7:8] != '3' or ln[13:14] != 'E':
            if len(ln) >= 72 and ln[7:8] == '1' and not conta:
                conta = f"{int(ln[58:70])}-{ln[70:71]}"
            continue
        if not conta:
            conta = f"{int(ln[58:70])}-{ln[70:71]}"
        valor = Decimal(int(ln[150:168])) / 100
        sinal = ln[168:169]
        if sinal == 'D':
            valor = -valor
        hist = re.sub(r'\s+', ' ', ln[176:201]).strip()
        prot = ln[201:240].strip()
        desc = f"{_HIST.get(hist, hist.capitalize() + ':')} protocolo {prot}"
        lancs.append({'data': _data(ln[142:150]), 'valor': valor,
                      'tipo': 'CREDIT' if valor >= 0 else 'DEBIT',
                      'descricao': desc, 'documento': prot.zfill(15) if prot else None,
                      'fitid': None, 'historico': hist})
    return {'banco_id': BANCO_ID, 'banco': BANCO_NOME, 'conta': conta, 'lancamentos': lancs}


# ---------------------------------------------------------------------------
# Baixa
# ---------------------------------------------------------------------------
def _q(sql, p=(), one=False):
    from utils.db_helper import execute_query
    return execute_query(sql, p, fetch=True, fetch_one=one) if one else (execute_query(sql, p, fetch=True) or [])


def _ja_amarrado(lanc_id):
    """A baixa que já usa este lançamento (ou None)."""
    return _q("""SELECT b.titulo_id, b.origem, t.contraparte_nome AS nome FROM fin_titulo_baixas b
                   JOIN fin_titulos t ON t.id = b.titulo_id
                  WHERE b.lancamento_id = %s LIMIT 1""", (lanc_id,), one=True)


def _baixa_do_webhook(empresa_id, data, valor):
    """Boleto emitido pelo NOSSO sistema: o webhook já deu a baixa no dia.
    Devolve a baixa (sem lançamento amarrado) que é este dinheiro, se houver."""
    return _q("""SELECT b.id, b.titulo_id FROM fin_titulo_baixas b
                   JOIN fin_titulos t ON t.id = b.titulo_id
                  WHERE t.empresa_id = %s AND b.origem = 'webhook' AND b.lancamento_id IS NULL
                    AND b.data_baixa BETWEEN %s AND %s
                    AND ABS(b.valor + b.juros + b.multa - %s) < 0.005
                  ORDER BY b.id LIMIT 1""",
              (empresa_id, data - timedelta(days=3), data, valor), one=True)


_CORTE = {}


def corte_conexa():
    """Último dia de crédito que o histórico do Conexa já trouxe como pago
    (04/11/2026 na migração). Até ele, o recebimento é de uma cobrança que o
    Conexa já quitou: a rotina não mexe — entra pelo OFX, como sempre foi."""
    if 'd' not in _CORTE:
        r = _q("SELECT MAX(data_credito) d FROM fin_cobrancas_conexa WHERE status = 'Quitada'", one=True)
        _CORTE['d'] = (r or {}).get('d') or date(2000, 1, 1)
    return _CORTE['d']


JANELA_IRMAOS = 5   # dias para os lados


def irmaos(empresa_id, data, valor, lanc_id=None):
    """Outros recebimentos do MESMO valor, perto da data, ainda sem título.
    Havendo, o valor não diz quem é quem: ninguém leva baixa por chute
    (04/11: cinco postos pagaram R$ 2.191,14 no mesmo dia)."""
    r = _q("""SELECT COUNT(*) n FROM extrato_lancamentos l
               WHERE l.empresa_id = %s AND ABS(l.valor - %s) < 0.005
                 AND l.data BETWEEN %s AND %s AND l.id <> %s
                 AND l.descricao LIKE 'Recebimento de cobran%%'
                 AND NOT EXISTS (SELECT 1 FROM fin_titulo_baixas b WHERE b.lancamento_id = l.id)""",
           (empresa_id, valor, data - timedelta(days=JANELA_IRMAOS), data + timedelta(days=JANELA_IRMAOS),
            lanc_id or 0), one=True)
    return int((r or {}).get('n') or 0)


def candidatos(empresa_id, data, valor, cliente_ids=None):
    """Títulos do Conexa em aberto com saldo IGUAL ao valor e vencimento perto."""
    sql = """SELECT t.id, t.cliente_id, t.contraparte_nome, t.vencimento, t.valor, t.valor_baixado
               FROM fin_titulos t
              WHERE t.empresa_id = %s AND t.tipo = 'R' AND t.origem = 'conexa'
                AND t.status IN ('aberto', 'parcial')
                AND ABS((t.valor - t.valor_baixado) - %s) < 0.005
                AND t.vencimento BETWEEN %s AND %s"""
    p = [empresa_id, valor, data - timedelta(days=JANELA_ANTES), data + timedelta(days=JANELA_DEPOIS)]
    if cliente_ids is not None:
        if not cliente_ids:
            return []
        sql += f" AND t.cliente_id IN ({','.join(['%s'] * len(cliente_ids))})"
        p += list(cliente_ids)
    return _q(sql + ' ORDER BY t.vencimento', tuple(p))


def _clientes_pelo_nome(nome):
    """Cadastro(s) cujo nome é o do pagador que o OFX escreveu."""
    if not nome:
        return []
    n = re.sub(r'\s+', ' ', nome).strip()
    rs = _q('SELECT id FROM clientes WHERE nome_razao_social = %s COLLATE utf8mb4_unicode_ci', (n,))
    if not rs:
        # o OFX escreve o MEI sem pontos ("51878059 LEANDRO ..."); o cadastro, com
        dig = re.match(r'^(\d{8})\s+(.*)$', n)
        if dig:
            rs = [r for r in _q("SELECT id, nome_razao_social FROM clientes WHERE REPLACE(nome_razao_social, '.', '') = %s COLLATE utf8mb4_unicode_ci",
                                (f'{dig.group(1)} {dig.group(2)}',))]
    return [r['id'] for r in rs]


def _nome_na_descricao(lanc, prot, nome):
    """O CNAB não diz quem pagou; o título diz — a tela do Extrato mostra o nome
    (no mesmo formato do OFX). Só reescreve a linha que veio do CNAB."""
    if lanc.get('id') and nome and 'protocolo' in str(lanc.get('descricao') or ''):
        from utils.db_helper import execute_query
        execute_query('UPDATE extrato_lancamentos SET descricao = %s WHERE id = %s AND origem = %s',
                      (f"Recebimento de cobrança: {prot} de {nome}"[:500], lanc['id'], ORIGEM), fetch=False)


def baixar(lanc, seco=True, nome_pagador=None, irmaos_seco=0):
    """Decide a baixa de UM lançamento de recebimento. Devolve (situação, detalhe).

    'antes-do-corte' = recebimento que o Conexa já deu como pago: quem chamou
    segue o caminho de sempre. ``irmaos_seco``: na simulação os lançamentos
    ainda não estão no banco, quem chama conta os de mesmo valor."""
    from utils.financeiro_core import registrar_baixa
    valor = Decimal(str(lanc['valor']))
    if valor <= 0 or not str(lanc.get('descricao') or '').startswith('Recebimento de cobrança'):
        return 'ignorado', None
    if lanc['data'] <= corte_conexa():
        return 'antes-do-corte', None
    amarrado = _ja_amarrado(lanc['id']) if lanc.get('id') else None
    if amarrado:
        return 'ja-amarrado', amarrado
    prot = (lanc.get('documento') or '').lstrip('0')
    ref = REF + prot

    # baixa que já existe com esta referência (dada à mão pelo mesmo protocolo)
    ja = _q("""SELECT b.id, b.titulo_id, t.contraparte_nome AS nome FROM fin_titulo_baixas b
                 JOIN fin_titulos t ON t.id = b.titulo_id WHERE b.referencia = %s LIMIT 1""", (ref,), one=True)
    if ja:
        if not seco and lanc.get('id'):
            from utils.db_helper import execute_query
            execute_query('UPDATE fin_titulo_baixas SET lancamento_id = %s, confirmado_extrato = 1 '
                          'WHERE id = %s AND lancamento_id IS NULL', (lanc['id'], ja['id']), fetch=False)
            _nome_na_descricao(lanc, prot, ja['nome'])
        return 'ja-baixado', {'titulo_id': ja['titulo_id'], 'nome': ja['nome']}

    wh = _baixa_do_webhook(lanc['empresa_id'], lanc['data'], valor)
    if wh:
        if not seco:
            from utils.db_helper import execute_query
            execute_query('UPDATE fin_titulo_baixas SET lancamento_id = %s, confirmado_extrato = 1 WHERE id = %s',
                          (lanc['id'], wh['id']), fetch=False)
        return 'webhook', {'titulo_id': wh['titulo_id']}

    cli = _clientes_pelo_nome(nome_pagador) if nome_pagador else None
    cands = candidatos(lanc['empresa_id'], lanc['data'], valor, cli)
    if cli is None and cands:
        # sem nome, só o valor: outro recebimento igual por perto = empate
        outros = irmaos_seco if seco else irmaos(lanc['empresa_id'], lanc['data'], valor, lanc.get('id'))
        if outros:
            return 'empate', {'candidatos': [c['id'] for c in cands], 'irmaos': outros}
    if len(cands) != 1:
        return ('empate' if cands else 'sem-titulo'), {'candidatos': [c['id'] for c in cands]}
    t = cands[0]
    if seco:
        return 'baixaria', {'titulo_id': t['id'], 'nome': t['contraparte_nome']}
    r = registrar_baixa(t['id'], valor, lanc['data'], 'extrato', referencia=ref, lancamento_id=lanc['id'])
    if not nome_pagador:
        _nome_na_descricao(lanc, prot, t['contraparte_nome'])
    from utils.atividade import registrar_agente
    registrar_agente('escrita.baixa_efi_extrato', 'financeiro', None, _ATOR[0], _ATOR[1],
                     tabela='fin_titulos', registro_id=t['id'],
                     depois={'valor': str(valor), 'data': str(lanc['data']), 'protocolo': prot,
                             'lancamento_id': lanc['id'], 'status': r.get('status'),
                             'como': 'nome do OFX + valor' if nome_pagador else 'valor único entre os títulos abertos'})
    return 'baixado', {'titulo_id': t['id'], 'nome': t['contraparte_nome']}


# ---------------------------------------------------------------------------
# O OFX chegou depois do CNAB: completa o nome e tenta de novo o que empatou
# ---------------------------------------------------------------------------
def completar_pelo_ofx(empresa_id, conta, linhas_ofx):
    """Para cada linha do OFX cujo protocolo já entrou pelo CNAB: grava a
    descrição com o nome e o FITID (a linha do OFX não é inserida de novo) e,
    se for recebimento ainda sem título, tenta a baixa com o nome. Devolve
    quantas completou."""
    from utils.db_helper import execute_query
    from utils.extrato_ingest import mesma_conta
    por_doc = {(l.get('documento') or '').lstrip('0'): l for l in linhas_ofx if l.get('documento')}
    if not por_doc:
        return 0
    docs = list(por_doc)
    feitos = 0
    for i in range(0, len(docs), 300):
        fatia = docs[i:i + 300]
        rows = _q(f"""SELECT * FROM extrato_lancamentos
                       WHERE empresa_id = %s AND origem = %s
                         AND TRIM(LEADING '0' FROM documento) IN ({','.join(['%s'] * len(fatia))})""",
                  (empresa_id, ORIGEM, *fatia))
        for r in rows:
            if not mesma_conta(r['conta'], conta):
                continue
            l = por_doc.get((r['documento'] or '').lstrip('0'))
            if not l:
                continue
            execute_query('UPDATE extrato_lancamentos SET descricao = %s, fitid = COALESCE(fitid, %s) WHERE id = %s',
                          (l['descricao'][:500], l.get('fitid'), r['id']), fetch=False)
            feitos += 1
            m = re.search(r' de (.+)$', l['descricao'] or '')
            if r['valor'] > 0 and m and not _ja_amarrado(r['id']):
                r = dict(r, descricao=l['descricao'])
                try:
                    baixar(r, seco=False, nome_pagador=m.group(1))
                except Exception:
                    logger.exception('[efi-extrato] baixa pelo nome do OFX falhou (lançamento %s)', r['id'])
    return feitos


# ---------------------------------------------------------------------------
# Rodada
# ---------------------------------------------------------------------------
def contas_efi():
    """Contas de cobrança Efí de PRODUÇÃO, ativas, com certificado (a API
    Extratos exige mTLS)."""
    return _q("""SELECT id, empresa_id FROM fin_cobranca_contas
                  WHERE provedor = 'efi' AND ambiente = 'producao' AND ativo = 1
                    AND certificado_cif IS NOT NULL""")


_ADIANDO = {'on': False}


def adiando():
    """Durante a gravação dos arquivos a baixa espera: só depois de TODOS os
    dias entrarem dá para ver os recebimentos de mesmo valor juntos."""
    return _ADIANDO['on']


def rodar(seco=True, desde=None, conta_id=None):
    """Uma rodada. ``desde`` (date) limita pelo dia do lançamento; nunca entra
    o que é do Conexa (até ``corte_conexa()``). Devolve o resumo; em ``seco``
    não grava nada."""
    from models.fin_cobranca import FinCobrancaConta
    from utils.extrato_ingest import achar_conta, processar_lancamentos, documentos_existentes
    resumo = {'arquivos': 0, 'lancamentos': 0, 'novos': 0, 'baixados': [], 'webhook': 0,
              'pendentes': [], 'erros': []}
    corte = corte_conexa()
    lotes = []                                     # (nome, dados, empresa_id)
    contas = [{'id': conta_id}] if conta_id else contas_efi()
    for cc in contas:
        try:
            efi, _c = FinCobrancaConta.cliente_efi(cc['id'])
            arqs = efi.extrato_arquivos()
        except Exception as e:
            resumo['erros'].append(f'conta {cc["id"]}: {e}')
            logger.warning('[efi-extrato] conta %s: %s', cc['id'], e)
            continue
        piso = max(corte, (desde - timedelta(days=1)) if desde else corte)
        arqs = sorted((a for a in arqs if (a.get('periodo_inicio') or '')[:10] >= (piso - timedelta(days=1)).isoformat()),
                      key=lambda a: a.get('periodo_inicio') or '')
        for a in arqs:
            nome = a['nome']
            try:
                dados = ler_cnab(efi.extrato_baixar(nome))
            except Exception as e:
                resumo['erros'].append(f'{nome}: {e}')
                continue
            dados['lancamentos'] = [l for l in dados['lancamentos']
                                    if l['data'] > corte and (not desde or l['data'] >= desde)]
            if not dados['lancamentos']:
                continue
            reg = achar_conta(BANCO_ID, dados['conta'])
            if not reg:
                resumo['erros'].append(f'{nome}: conta {dados["conta"]} não está cadastrada em Financeiro → Contas')
                continue
            lotes.append((nome, dados, reg['empresa_id']))
            resumo['arquivos'] += 1
            resumo['lancamentos'] += len(dados['lancamentos'])

    if seco:
        todos = [l for _n, d, _e in lotes for l in d['lancamentos']]
        for _nome, dados, emp in lotes:
            ja = documentos_existentes(emp, dados['conta'], [l['documento'] for l in dados['lancamentos']])
            for l in dados['lancamentos']:
                resumo['novos'] += int((l['documento'] or '').lstrip('0') not in ja)
                viz = sum(1 for o in todos if o is not l and o['valor'] == l['valor']
                          and abs((o['data'] - l['data']).days) <= JANELA_IRMAOS
                          and o['descricao'].startswith('Recebimento de cobran'))
                s, d = baixar(dict(l, empresa_id=emp, id=None), seco=True, irmaos_seco=viz)
                _anota(resumo, s, d, l)
        return resumo

    # 1) grava TODOS os dias (a baixa espera); 2) só então decide as baixas
    _ADIANDO['on'] = True
    try:
        for nome, dados, emp in lotes:
            r = processar_lancamentos(dados, emp, nome, usuario_id=None, origem=ORIGEM)
            resumo['novos'] += r['novos']
    finally:
        _ADIANDO['on'] = False
    for _nome, dados, emp in lotes:
        docs = [l['documento'].lstrip('0') for l in dados['lancamentos'] if l['valor'] > 0 and l['documento']]
        if not docs:
            continue
        rows = _q(f"""SELECT * FROM extrato_lancamentos WHERE empresa_id = %s AND valor > 0
                        AND TRIM(LEADING '0' FROM documento) IN ({','.join(['%s'] * len(docs))})
                      ORDER BY data, id""", (emp, *docs))
        for row in rows:
            try:
                s, d = baixar(row, seco=False)
            except Exception as e:
                logger.exception('[efi-extrato] baixa falhou (lançamento %s)', row['id'])
                resumo['erros'].append(f'lançamento {row["id"]}: {e}')
                continue
            _anota(resumo, s, d, row)
    return resumo


def _anota(resumo, s, d, l):
    linha = {'data': str(l['data']), 'valor': str(l['valor']), 'protocolo': (l.get('documento') or '').lstrip('0')}
    if s == 'ja-amarrado':
        s = 'webhook' if (d or {}).get('origem') == 'webhook' else 'baixado'
    if s in ('baixado', 'baixaria', 'ja-baixado'):
        resumo['baixados'].append(dict(linha, titulo_id=(d or {}).get('titulo_id'), nome=(d or {}).get('nome')))
    elif s == 'webhook':
        resumo['webhook'] += 1
    elif s in ('empate', 'sem-titulo'):
        resumo['pendentes'].append(dict(linha, motivo=s, candidatos=len((d or {}).get('candidatos') or [])))


# ---------------------------------------------------------------------------
# Uma vez por dia, de dentro do cron de 5 minutos
# ---------------------------------------------------------------------------
_CHAVE = 'efi_extrato_ultimo_dia'
HORA_MINIMA = 7      # a Efí gera o arquivo ~04:00 de Brasília; 07:00 dá folga


def rodar_do_dia():
    """Chamado a cada tick do roteador: roda UMA vez por dia, depois das 7h
    (Brasília). Desliga com EFI_EXTRATO_ATIVO=0."""
    import os
    from zoneinfo import ZoneInfo
    from utils.db_helper import execute_query
    if os.getenv('EFI_EXTRATO_ATIVO', '1').strip() != '1':
        return None
    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    if agora.hour < HORA_MINIMA:
        return None
    hoje = agora.date().isoformat()
    r = _q('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE,), one=True)
    if r and r.get('valor') == hoje:
        return None
    # marca ANTES de rodar: um erro não vira 288 tentativas por dia; amanhã
    # tenta de novo e a deduplicação cobre o dia perdido (a Efí guarda ~30).
    execute_query('INSERT INTO app_config (chave, valor) VALUES (%s, %s) '
                  'ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()',
                  (_CHAVE, hoje), fetch=False)
    desde = agora.date() - timedelta(days=7)
    res = rodar(seco=False, desde=desde)
    logger.warning('[efi-extrato] %s arquivo(s), %s novo(s), %s baixa(s), %s do webhook, %s para escolher, %s erro(s)',
                   res['arquivos'], res['novos'], len(res['baixados']), res['webhook'],
                   len(res['pendentes']), len(res['erros']))
    for e in res['erros']:
        logger.warning('[efi-extrato]   %s', e)
    return res
