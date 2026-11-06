# -*- coding: utf-8 -*-
"""Cobrança das COMPRAS: condição, forma de pagamento e vencimentos lidos do XML
da nota de ENTRADA (tabelas nfe_cobranca e nfe_duplicatas).

Por que existe: muito cliente não manda extrato e o time não sabe quando vencem
os boletos das compras. O XML da NF-e traz isso:
  * <cobr><fat>   a fatura (número, valor líquido);
  * <cobr><dup>   uma por parcela — nDup, dVenc (vencimento) e vDup;
  * <pag><detPag> indPag (0 à vista, 1 a prazo) e tPag (15 boleto, 17 PIX...).
O XML sai do banco no expurgo (~1 mês); o que interessa fica aqui para sempre.

Sem <dup> não há vencimento — há fornecedor que diz "boleto" e não manda a data
(a Tabocão, no Aliança, em 11/2026). A tela/planilha mostra isso como está.

Gravar é INSERT IGNORE pela chave da nota: reler o mesmo XML não duplica. A
nota lida SEMPRE ganha a linha em nfe_cobranca, mesmo vazia: é o que diz ao
preenchimento do passado que ela já foi lida. Nunca levanta para quem chama.
"""
import logging
import re
from datetime import date, datetime

from utils.nfe_pagamentos import FORMAS
from utils.xml_seguro import fromstring_seguro

_log = logging.getLogger(__name__)

_RE_BLOCO = {nome: re.compile(rf'<{nome}\b[^>]*>.*?</{nome}>', re.S)
             for nome in ('cobr', 'pag')}
_RE_IDE_INDPAG = re.compile(r'<ide\b.*?<indPag>(\d)</indPag>.*?</ide>', re.S)
_RE_NS = re.compile(r'\sxmlns(:\w+)?="[^"]*"')

# Prazo: estas formas, sem <dup> e sem indPag, não dizem se é à vista.
_FORMAS_PRAZO = {'05', '14', '15'}

SQL_COBRANCA = (
    "INSERT IGNORE INTO nfe_cobranca (chave_acesso, ind_pag, tpags, n_fat, v_liq, qtd_dup) "
    "VALUES (%s,%s,%s,%s,%s,%s)"
)
SQL_DUPLICATA = (
    "INSERT IGNORE INTO nfe_duplicatas (chave_acesso, n, n_dup, d_venc, v_dup) "
    "VALUES (%s,%s,%s,%s,%s)"
)


def _txt(el, nome, tam=None):
    f = el.find(nome) if el is not None else None
    v = (f.text or '').strip() if f is not None else ''
    return (v[:tam] if tam else v) or None


def _valor(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _data(v):
    try:
        return datetime.strptime((v or '')[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def ler_cobranca(xml):
    """XML da nota (str/bytes) -> dict com ind_pag, tpags, n_fat, v_liq e dups
    (lista de dicts n_dup/d_venc/v_dup, na ordem do XML). Lê só <ide>, <cobr>
    e <pag>. Aceita o leiaute 4.00 (indPag no detPag) e o 3.10 (indPag no ide)."""
    if isinstance(xml, (bytes, bytearray)):
        xml = xml.decode('utf-8', 'replace')
    xml = xml or ''
    blocos = ''.join(_RE_BLOCO['cobr'].findall(xml) + _RE_BLOCO['pag'].findall(xml))
    raiz = fromstring_seguro('<r>' + _RE_NS.sub('', blocos) + '</r>') if blocos else None

    ind_pag, tpags = None, []
    m = _RE_IDE_INDPAG.search(xml)
    if m:
        ind_pag = int(m.group(1))
    if raiz is not None:
        for pag in raiz.findall('pag'):
            for dp in pag.findall('detPag') or [pag]:
                ip = _txt(dp, 'indPag')
                if ip and ip.isdigit() and ind_pag is None:
                    ind_pag = int(ip)
                tp = _txt(dp, 'tPag')
                if tp:
                    tp = tp.zfill(2)[:2]
                    if tp not in tpags:
                        tpags.append(tp)

    n_fat = v_liq = None
    dups = []
    cobr = raiz.find('cobr') if raiz is not None else None
    if cobr is not None:
        fat = cobr.find('fat')
        n_fat = _txt(fat, 'nFat', 60)
        v_liq = _valor(_txt(fat, 'vLiq'))
        for d in cobr.findall('dup'):
            dups.append({'n_dup': _txt(d, 'nDup', 60),
                         'd_venc': _data(_txt(d, 'dVenc')),
                         'v_dup': _valor(_txt(d, 'vDup')) or 0.0})
    return {'ind_pag': ind_pag, 'tpags': ','.join(tpags)[:40] or None,
            'n_fat': n_fat, 'v_liq': v_liq, 'dups': dups}


def tuplas(chave, cob):
    """(linha de nfe_cobranca, linhas de nfe_duplicatas) para os SQL_*."""
    cab = (chave, cob['ind_pag'], cob['tpags'], cob['n_fat'], cob['v_liq'], len(cob['dups']))
    dups = [(chave, n, d['n_dup'], d['d_venc'], d['v_dup'])
            for n, d in enumerate(cob['dups'], 1)]
    return cab, dups


def registrar_entrada(xml_raw, chave):
    """Chamado na chegada de cada nota de ENTRADA completa. Nunca levanta."""
    try:
        if not xml_raw or not chave:
            return
        cab, dups = tuplas(chave, ler_cobranca(xml_raw))
        from utils.db_helper import execute_many
        if dups:
            execute_many(SQL_DUPLICATA, dups)
        execute_many(SQL_COBRANCA, [cab])
    except Exception:
        _log.warning('cobrança da nota %s não gravada', chave, exc_info=True)


# ── leitura para a tela / relatório ────────────────────────────────────────

def carregar(chaves):
    """{chave: {'ind_pag', 'tpags', 'dups': [(n_dup, d_venc, v_dup), ...]}} das
    chaves pedidas (só as que já foram lidas)."""
    from utils.db_helper import execute_query
    chaves = [c for c in dict.fromkeys(chaves) if c]
    out = {}
    for i in range(0, len(chaves), 1000):
        parte = chaves[i:i + 1000]
        ph = ','.join(['%s'] * len(parte))
        for r in execute_query(
                f"SELECT chave_acesso, ind_pag, tpags FROM nfe_cobranca WHERE chave_acesso IN ({ph})",
                tuple(parte), fetch=True) or []:
            out[r['chave_acesso']] = {'ind_pag': r['ind_pag'], 'tpags': r['tpags'], 'dups': []}
        for r in execute_query(
                f"SELECT chave_acesso, n_dup, d_venc, v_dup FROM nfe_duplicatas "
                f"WHERE chave_acesso IN ({ph}) ORDER BY chave_acesso, n",
                tuple(parte), fetch=True) or []:
            if r['chave_acesso'] in out:
                out[r['chave_acesso']]['dups'].append(
                    (r['n_dup'], r['d_venc'], float(r['v_dup'] or 0)))
    return out


def formas_rotulo(tpags):
    return ' + '.join(FORMAS.get(t, t) for t in (tpags or '').split(',') if t) or '—'


def condicao(cob, data_emissao):
    """'A prazo' | 'À vista' | 'Sem pagamento' | 'Não informada' | '—' (não lida)."""
    if not cob:
        return '—'
    emis = data_emissao if isinstance(data_emissao, date) else None
    vencs = [d for _, d, _ in cob['dups'] if d]
    if vencs:
        return 'A prazo' if (emis is None or max(vencs) > emis) else 'À vista'
    if cob['ind_pag'] == 1:
        return 'A prazo'
    tps = set((cob['tpags'] or '').split(',')) - {''}
    if tps == {'90'}:
        return 'Sem pagamento'
    if cob['ind_pag'] == 0:
        return 'À vista'
    if not tps or tps & _FORMAS_PRAZO:
        return 'Não informada'
    return 'À vista'


def vencimentos_rotulo(cob, condicao_txt):
    """Texto da coluna Vencimento(s) do Excel."""
    if not cob:
        return 'Nota ainda não lida'
    dups = [(n, d, v) for n, d, v in cob['dups'] if d]
    if dups:
        if len(dups) == 1:
            return dups[0][1].strftime('%d/%m/%Y')
        return '; '.join(f"{d.strftime('%d/%m/%Y')} (R$ {v:,.2f})"
                         .replace(',', 'X').replace('.', ',').replace('X', '.')
                         for _, d, v in dups)
    if condicao_txt == 'A prazo' or (
            condicao_txt == 'Não informada'
            and set((cob['tpags'] or '').split(',')) & _FORMAS_PRAZO):
        return 'Vencimento não informado na nota'
    return '—'
