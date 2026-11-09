# -*- coding: utf-8 -*-
"""Serviços da NFS-e em palavras normais — atividade do CNPJ → serviço da nota (08/11/2026).

Pedido do Anderson: "ter que escolher as atividades que tem no cadastro não serve? Pelo CNPJ não
puxa qual é o correto?". A Receita informa só a ATIVIDADE (CNAE); o código da nota (cTribNac,
6 dígitos) e a NBS vêm de tabelas oficiais da NFS-e Nacional, guardadas em
``static/nfse/servicos_nacionais.json`` (a tela também lê o mesmo arquivo, para a escolha manual):

* Anexo B v1.01 (22/01/2026): lista de serviço nacional — cTribNac e descrição;
* Anexo VIII v1.02.00 (26/10/2026): correlação item da LC 116 × NBS (+ cIndOp, cClassTrib).
  Combinação serviço × NBS fora dela é recusada pelo governo — por isso a NBS só pode ser uma
  das permitidas para o item.

Não existe tabela oficial CNAE × serviço: ``CNAE_SERVICO`` abaixo é nossa, feita para as
atividades dos clientes a partir da lista da LC 116. O Fiscal confere ao liberar.
"""
import json
import os
import re
from functools import lru_cache

_ARQ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'nfse', 'servicos_nacionais.json')

# CNAE (7 dígitos) → serviços da nota (cTribNac), o primeiro é o sugerido.
CNAE_SERVICO = {
    '6920601': ['171901'],                       # contabilidade
    '8219999': ['170201'], '8211300': ['170201'], '8299799': ['170201'],   # apoio administrativo
    '4930201': ['160201'], '4930202': ['160201'], '4930203': ['160201'],   # transporte (municipal)
    '7810800': ['170401', '170501'],             # seleção / fornecimento de mão de obra
    '7490104': ['100501', '100202'],             # intermediação de negócios
    '6630400': ['150101'],                       # administração de fundos
    '6204000': ['010601'], '6209100': ['010701'], '6201501': ['010101'], '6202300': ['010101', '010501'],
    '6319400': ['010301', '010901'],             # TI / conteúdo
    '5223100': ['110101'], '5211799': ['110401'],
    '7500100': ['050101'],
    '7020400': ['170101'],                       # consultoria
    '7112000': ['070101'], '7490103': ['070101'],
    '7410202': ['230101'],
    '4520007': ['140101', '140601'], '9512600': ['140101', '140201'], '9511800': ['140101', '140201'],
    '6622300': ['100102'],                       # corretagem de seguros
    '6822600': ['171201'],                       # administração de bens de terceiros
    '4612500': ['100901'], '4618401': ['100901'],   # representação comercial
    '5320202': ['260101'],
    '4330499': ['070201', '070501'], '4399103': ['070201'], '4330404': ['070501'], '4322301': ['070201'],
    '7420001': ['130301'], '5912099': ['130301'], '5911102': ['130301', '170601'],
    '8593700': ['080201'], '8599699': ['080201'],
    '1821100': ['130501'],
    '8630504': ['041201'],
    '6821802': ['100501'],
    '7319002': ['170601', '100202'],
    '5620102': ['171101'],
    '5510801': ['090101'],
    '9313100': ['060401'],
    # Aluguel (08/11/2026): fora da lista do ISS, mas sai em NFS-e com IBS/CBS a partir da reforma —
    # código nacional 99.01.01 "Serviços sem a incidência de ISSQN e ICMS". O Fiscal confere.
    '6810202': ['990101'], '7711000': ['990101'],
}
# Quando o item não tem correlação no Anexo VIII (ex.: 99.01.01), as NBS sugeridas na tela.
NBS_SUGERIDAS = {'990101': ['110021000', '110022000', '110011100', '110011290']}
# Atividades que não são prestação de serviço da LC 116 (aluguel de imóvel próprio, holding,
# venda de mercadoria...): a tela mostra apagado, com o motivo.
_SEM_SERVICO = {
    '6810201': 'Compra e venda de imóvel próprio não é serviço',
    '6462000': 'Holding: participação em outras empresas, não é serviço',
    '6463800': 'Participação em outras empresas, não é serviço',
}


@lru_cache(maxsize=1)
def tabelas():
    with open(_ARQ, encoding='utf-8') as f:
        return json.load(f)


def _dig(v):
    return re.sub(r'\D', '', str(v or ''))


def fmt_ctrib(c):
    c = _dig(c).zfill(6)
    return f'{c[:2]}.{c[2:4]}.{c[4:]}'


def fmt_nbs(n):
    n = _dig(n)
    return f'{n[0]}.{n[1:5]}.{n[5:7]}.{n[7:]}' if len(n) == 9 else n


def fmt_cnae(c):
    c = _dig(c)
    return f'{c[:2]}.{c[2:4]}-{c[4]}-{c[5:]}' if len(c) == 7 else c


def nome_servico(ctrib):
    return tabelas()['servicos'].get(_dig(ctrib).zfill(6), '')


def nbs_permitidas(ctrib):
    """[(nbs, descrição)] que o governo aceita para o serviço (Anexo VIII), pelo item (4 dígitos)."""
    return [tuple(x) for x in tabelas()['nbs_por_item'].get(_dig(ctrib).zfill(6)[:4], [])]


def nome_nbs(nbs):
    return tabelas().get('nbs', {}).get(_dig(nbs), '')


def nbs_sugeridas(ctrib):
    """O que a tela oferece: as permitidas (Anexo VIII) ou, sem correlação, as sugeridas."""
    lista = nbs_permitidas(ctrib)
    if lista:
        return lista
    return [(n, nome_nbs(n)) for n in NBS_SUGERIDAS.get(_dig(ctrib).zfill(6), []) if nome_nbs(n)]


def nbs_ok(ctrib, nbs):
    lista = nbs_permitidas(ctrib)
    return (not lista) or _dig(nbs) in {n for n, _ in lista}


def meta_do_item(ctrib):
    return tabelas()['item_meta'].get(_dig(ctrib).zfill(6)[:4], {}) or {}


def eh_mercadoria(cnae):
    """Comércio e indústria: venda de mercadoria (ICMS), não sai em nota de serviço."""
    c = _dig(cnae)
    if c in CNAE_SERVICO:
        return False
    return c[:2] in {'45', '46', '47'} or ('10' <= c[:2] <= '33')


def servico_da_atividade(cnae, descricao='', principal=False):
    """O cartão de uma atividade: o que ela vira na nota (ou por que não vira)."""
    c = _dig(cnae)
    cods = [x for x in CNAE_SERVICO.get(c, []) if nome_servico(x)]
    item = {'cnae': c, 'cnae_fmt': fmt_cnae(c), 'descricao': descricao or '', 'principal': principal,
            'servicos': [{'ctrib': x, 'ctrib_fmt': fmt_ctrib(x), 'nome': nome_servico(x)} for x in cods]}
    if cods:
        item['tipo'] = 'servico'
    elif c in _SEM_SERVICO:
        item['tipo'], item['motivo'] = 'nao', _SEM_SERVICO[c]
    elif eh_mercadoria(c):
        item['tipo'], item['motivo'] = 'nao', 'Venda de mercadoria: não sai em nota de serviço'
    else:
        item['tipo'], item['motivo'] = 'manual', 'Sem sugestão automática: escolha o serviço na lista'
    return item


def buscar_servicos(q, limite=20):
    """Lista oficial de serviços por palavra ou código — para a escolha manual."""
    q = (q or '').strip().lower()
    d = _dig(q)
    out = []
    for c, nome in tabelas()['servicos'].items():
        if (d and len(d) >= 2 and c.startswith(d)) or (q and not d and q in nome.lower()):
            out.append({'ctrib': c, 'ctrib_fmt': fmt_ctrib(c), 'nome': nome})
            if len(out) >= limite:
                break
    return out


# ---------------------------------------------------------------------------
# Atividades da empresa e o histórico de notas
# ---------------------------------------------------------------------------
_CACHE_SEC = {}          # cnpj -> (quando, [(cnae, descrição)]) — secundárias da Receita, 12 h


def _secundarias(cnpj):
    import time
    d = _dig(cnpj)
    agora = time.time()
    if d in _CACHE_SEC and agora - _CACHE_SEC[d][0] < 12 * 3600:
        return _CACHE_SEC[d][1]
    lista = []
    try:
        from utils import cnpj_receita
        r, _f = cnpj_receita.consultar(d)
        for x in r.get('cnaes_secundarios') or []:
            cod = _dig(x.get('codigo'))
            if len(cod) == 7:
                lista.append((cod, x.get('descricao') or ''))
    except Exception:
        return []            # Receita fora: mostra só a principal, sem guardar a falha
    _CACHE_SEC[d] = (agora, lista)
    return lista


def atividades_da_empresa(cnpj, cnae_principal, descricao_principal=''):
    """Cartões das atividades: a principal (cadastro) e as secundárias (Receita)."""
    out, vistos = [], set()
    if len(_dig(cnae_principal)) == 7:
        out.append(servico_da_atividade(cnae_principal, descricao_principal, principal=True))
        vistos.add(_dig(cnae_principal))
    if len(_dig(cnpj)) == 14:
        for cod, desc in _secundarias(cnpj):
            if cod not in vistos:
                out.append(servico_da_atividade(cod, desc))
                vistos.add(cod)
    return out


def historico_de_notas(empresa_id, cnpj):
    """[(ctrib, nbs, qtd)] das notas que a empresa EMITIU (capturadas do governo), do mais usado."""
    from utils.db_helper import execute_query
    rows = execute_query(
        "SELECT codigo_servico c, codigo_nbs n, COUNT(*) q FROM nfse_capturadas "
        " WHERE empresa_id = %s AND prestador_doc = %s AND codigo_servico IS NOT NULL "
        " GROUP BY codigo_servico, codigo_nbs ORDER BY q DESC LIMIT 5",
        (empresa_id, _dig(cnpj)), fetch=True) or []
    return [(_dig(r['c']).zfill(6), _dig(r['n']), r['q']) for r in rows if _dig(r['c'])]


def sugerir_servico(empresa_id, cnpj, cnae_principal):
    """(ctrib, nbs) para o primeiro preenchimento: o histórico manda (se a NBS dele é aceita);
    senão o primeiro serviço da atividade principal, com a NBS só quando a tabela admite uma."""
    for c, n, _q in historico_de_notas(empresa_id, cnpj):
        if nome_servico(c):
            ok = len(n) == 9 and n != '999999999' and nbs_ok(c, n)
            if ok:
                return c, n
            lista = nbs_sugeridas(c)
            return c, (lista[0][0] if len(lista) == 1 else '')
    cods = [x for x in CNAE_SERVICO.get(_dig(cnae_principal), []) if nome_servico(x)]
    if cods:
        lista = nbs_sugeridas(cods[0])
        return cods[0], (lista[0][0] if len(lista) == 1 else '')
    return '', ''
