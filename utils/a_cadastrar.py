# -*- coding: utf-8 -*-
"""A CADASTRAR (05/11/2026) — o que ainda falta nos cadastros, calculado AO VIVO pelo banco.

Três filas: EMPRESA PENDENTE (cliente PJ com número e algo faltando), SÓCIOS PENDENTES (a fila da
Receita, utils/socios_fila.py) e AVULSOS PENDENTES (avulso com algo faltando). Quem está completo fica
só em Clientes / Avulsos; quem tem pendência aparece lá E aqui. Nada é gravado: resolveu no cadastro,
some daqui sozinho.

As pendências da empresa seguem a coluna ``pendencias`` da planilha de migração (NIRE, Insc. Municipal,
Insc. Estadual, Sócios, Financeiro/cobrança, Contato com e-mail) mais os obrigatórios do próprio app
(grupo, celular, ramo, contador, origem).
"""
from utils.db_helper import execute_query

PENDENCIAS_EMPRESA = [
    # (chave, rótulo, cor, texto de ajuda)
    ('nire', 'NIRE', 'amb', 'número de registro na Junta Comercial'),
    # 12/11/2026 (Anderson): NIRE com tamanho errado entra no A CADASTRAR "com um lembrete que é o NIRE errado".
    ('nire_errado', 'Corrigir NIRE', 'verm', 'NIRE errado: o certo tem 11 números (XX.XXX.XXX.XXX) e este está com outro tamanho: conferir na ficha da Junta e corrigir'),
    ('im', 'Insc. Municipal', 'amb', 'inscrição municipal (CCM)'),
    ('ie', 'Insc. Estadual', 'amb', 'inscrição estadual, ou marcar isenta'),
    ('socios', 'Sócios', 'verm', 'nenhum sócio cadastrado'),
    ('regime', 'Regime', 'verm', 'regime tributário em branco: Simples, Lucro Presumido, Lucro Real ou MEI (a Receita só diz se é optante do Simples)'),
    ('cobranca', 'Financeiro (cobrança)', 'verm', 'como cobrar: própria, via grupo ou pendente'),
    ('email', 'Contato com e-mail', 'verm', 'nenhum contato com e-mail (é por eles que vão boletos e documentos)'),
    ('contato_celular', 'Celular do contato', 'amb', 'há contato sem celular (obrigatório desde 05/11/2026)'),
    ('contato', 'Contato', 'verm', 'pessoa física sem nenhum contato (nome + celular)'),
    # 09/11/2026 (Anderson): "O A CADASTRAR deve passar a cobrar o documento da pessoa física? SIM" — desde
    # que o Novo grava incompleto, o documento deixou de travar o salvar e passou a ser cobrado aqui.
    ('documento', 'Documento', 'verm', 'pessoa física sem documento de identificação (RG, CNH, CIN...)'),
    # 09/11/2026 (Anderson): produtor rural sem IE fica no A CADASTRAR ("SIM"); IE que a migração trouxe em
    # conflito com o cadastro precisa de alguém olhar no Sintegra.
    ('ie_rural', 'IE de produtor rural', 'verm', 'produtor rural sem inscrição estadual (uma por propriedade)'),
    ('ie_conferir', 'Conferir IE', 'verm', 'inscrição estadual em conflito: conferir no Sintegra e salvar ou excluir'),
    ('grupo', 'Grupo', 'cinza', 'grupo de empresas'),
    ('celular', 'Celular', 'cinza', 'celular de contato'),
    ('ramo', 'Ramo', 'cinza', 'ramo de atividade'),
    ('contador', 'Contador', 'cinza', 'contador responsável'),
    ('origem', 'Origem', 'cinza', 'aberta pela Qualicontax ou contabilidade anterior'),
]
PENDENCIAS_AVULSO = [
    ('email', 'E-mail', 'verm', 'sem e-mail'),
    ('regime', 'Regime', 'verm', 'PJ com regime tributário em branco: Simples, Lucro Presumido, Lucro Real ou MEI'),
    ('contato_celular', 'Celular do contato', 'amb', 'há contato sem celular (obrigatório desde 05/11/2026)'),
    ('celular', 'Celular', 'amb', 'sem celular'),
    ('grupo', 'Grupo', 'cinza', 'sem grupo de empresas'),
    ('ramo', 'Ramo', 'cinza', 'sem ramo de atividade'),
    ('conferir', 'Conferir', 'verm', 'há uma observação pedindo conferência'),
    ('ie_conferir', 'Conferir IE', 'verm', 'inscrição estadual em conflito: conferir no Sintegra e salvar ou excluir'),
]

# Tabelas derivadas em vez de subconsulta por linha: 5,2 s → 1,1 s no Railway, mesmo resultado (medido 06/11/2026).
_SQL_EMPRESAS = """
SELECT c.id, c.numero_cliente, c.nome_razao_social, c.nome_fantasia, c.cpf_cnpj, c.tipo_pessoa, c.situacao,
       c.nire, c.inscricao_municipal, c.inscricao_estadual, c.email, c.celular, c.cobranca, c.conexa_id,
       c.aberta_pela_casa, c.contabilidade_anterior_id, c.regime_tributario, c.observacoes,
       COALESCE(s.n, 0) AS n_socios,
       COALESCE(k.n_email, 0) AS n_contatos_email,
       COALESCE(k.n_sem_cel, 0) AS n_contatos_sem_celular,
       COALESCE(k.n, 0) AS n_contatos,
       COALESCE(g.n, 0) AS n_grupos,
       COALESCE(r.n, 0) AS n_ramos,
       COALESCE(k2.n, 0) AS n_contadores,
       COALESCE(ie.n, 0) AS n_ie_isenta,
       COALESCE(dc.n, 0) AS n_docs,
       COALESCE(iex.n_conf, 0) AS n_ie_conferir, COALESCE(iex.n_rural, 0) AS n_ie_rural,
       COALESCE(rr.n, 0) AS n_ramo_rural,
       e.cidade, e.uf
  FROM clientes c
  LEFT JOIN (SELECT cliente_id, COUNT(*) AS n FROM socios_clientes WHERE ativo = 1 GROUP BY cliente_id) s ON s.cliente_id = c.id
  LEFT JOIN (SELECT cliente_id, COUNT(*) AS n,
                    SUM(email IS NOT NULL AND email <> '') AS n_email,
                    SUM(celular IS NULL OR celular = '') AS n_sem_cel
               FROM contatos_clientes WHERE ativo = 1 GROUP BY cliente_id) k ON k.cliente_id = c.id
  LEFT JOIN (SELECT cliente_id, COUNT(*) AS n FROM cliente_grupo_relacao GROUP BY cliente_id) g ON g.cliente_id = c.id
  LEFT JOIN (SELECT cliente_id, COUNT(*) AS n FROM cliente_ramo_atividade_relacao GROUP BY cliente_id) r ON r.cliente_id = c.id
  LEFT JOIN (SELECT cliente_id, COUNT(*) AS n FROM cliente_contadores GROUP BY cliente_id) k2 ON k2.cliente_id = c.id
  LEFT JOIN (SELECT cliente_id, COUNT(*) AS n FROM clientes_inscricoes_estaduais WHERE isenta = 1 GROUP BY cliente_id) ie ON ie.cliente_id = c.id
  LEFT JOIN (SELECT cliente_id, COUNT(*) AS n FROM clientes_pf_documentos GROUP BY cliente_id) dc ON dc.cliente_id = c.id
  LEFT JOIN (SELECT cliente_id, SUM(conferir) AS n_conf, SUM(tipo = 'RURAL') AS n_rural
               FROM clientes_inscricoes_estaduais GROUP BY cliente_id) iex ON iex.cliente_id = c.id
  LEFT JOIN (SELECT x.cliente_id, COUNT(*) AS n FROM cliente_ramo_atividade_relacao x
               JOIN ramos_atividade ra ON ra.id = x.ramo_atividade_id
              WHERE ra.tipo_pessoa = 'PF' AND UPPER(ra.nome) LIKE '%%RURAL%%' GROUP BY x.cliente_id) rr ON rr.cliente_id = c.id
  LEFT JOIN (SELECT cliente_id, MAX(cidade) AS cidade, MAX(estado) AS uf FROM enderecos_clientes GROUP BY cliente_id) e ON e.cliente_id = c.id
 WHERE c.avulso = 0 AND c.numero_cliente IS NOT NULL AND c.situacao = 'ATIVO'
 ORDER BY CAST(c.numero_cliente AS UNSIGNED), c.numero_cliente
"""

_SQL_AVULSOS = """
SELECT c.id, c.nome_razao_social, c.cpf_cnpj, c.tipo_pessoa, c.situacao, c.email, c.celular, c.telefone,
       c.avulso_em, c.conexa_id, c.emite_nf, c.cobranca, c.observacoes, c.regime_tributario,
       (SELECT COUNT(*) FROM contatos_clientes k3 WHERE k3.cliente_id = c.id AND k3.ativo = 1 AND (k3.celular IS NULL OR k3.celular = '')) AS n_contatos_sem_celular,
       (SELECT COUNT(*) FROM cliente_grupo_relacao g WHERE g.cliente_id = c.id) AS n_grupos,
       (SELECT COUNT(*) FROM cliente_ramo_atividade_relacao r WHERE r.cliente_id = c.id) AS n_ramos,
       (SELECT COUNT(*) FROM clientes_inscricoes_estaduais i WHERE i.cliente_id = c.id AND i.conferir = 1) AS n_ie_conferir
  FROM clientes c
 WHERE c.avulso = 1
 ORDER BY c.nome_razao_social
"""


def _vazio(v):
    return v is None or str(v).strip() == ''


def empresas_pendentes(ocultas=frozenset()):
    """Empresas (clientes PJ com número) com ao menos uma pendência, cada uma com a lista de chaves."""
    out = []
    for c in execute_query(_SQL_EMPRESAS, fetch=True) or []:
        if c['id'] in ocultas:
            continue
        p = _pend_empresa(c)
        if p:
            c['pendencias'] = p
            out.append(c)
    return out


def nire_errado(v):
    """NIRE preenchido que não tem 11 números (o da Junta é XX.XXX.XXX.XXX). Com letra não se julga."""
    s = str(v or '').strip()
    if not s or any(ch.isalpha() for ch in s):
        return False
    return len(''.join(ch for ch in s if ch.isdigit())) != 11


def _pend_empresa(c):
    """As chaves que faltam numa linha de empresa (mesma regra para a lista e para a ficha)."""
    pj = c['tipo_pessoa'] != 'PF'
    p = []
    if pj and _vazio(c['nire']):
        p.append('nire')
    elif pj and nire_errado(c['nire']):
        p.append('nire_errado')
    if pj and _vazio(c['inscricao_municipal']):
        p.append('im')
    if pj and _vazio(c['inscricao_estadual']) and not c['n_ie_isenta']:
        p.append('ie')
    if pj and not c['n_socios']:
        p.append('socios')
    if pj and _vazio(c['regime_tributario']):
        p.append('regime')
    if (c['cobranca'] or 'PENDENTE') == 'PENDENTE':
        p.append('cobranca')
    if pj and _vazio(c['email']) and not c['n_contatos_email']:
        p.append('email')
    if not pj and not c['n_contatos']:
        p.append('contato')          # PF: sem e-mail no cadastro, o contato é obrigatório (05/11/2026)
    if not pj and not c['n_docs']:
        p.append('documento')        # PF: documento de identificação (09/11/2026)
    if not pj and c['n_ramo_rural'] and not c['n_ie_rural']:
        p.append('ie_rural')         # PF produtor rural sem IE (09/11/2026)
    if c['n_ie_conferir']:
        p.append('ie_conferir')
    if c['n_contatos_sem_celular']:
        p.append('contato_celular')
    if not c['n_grupos']:
        p.append('grupo')
    if _vazio(c['celular']):
        p.append('celular')
    if not c['n_ramos']:
        p.append('ramo')
    if not c['n_contadores']:
        p.append('contador')
    if pj and not c['aberta_pela_casa'] and not c['contabilidade_anterior_id']:
        p.append('origem')
    return p


def avulsos_pendentes(ocultas=frozenset()):
    out = []
    for c in execute_query(_SQL_AVULSOS, fetch=True) or []:
        if c['id'] in ocultas:
            continue
        p = _pend_avulso(c)
        if p:  # avulso completo fica só na aba Avulsos (Anderson, 05/11/2026)
            c['pendencias'] = p
            out.append(c)
    return out


def _pend_avulso(c):
    p = []
    if _vazio(c['email']):
        p.append('email')
    if c['tipo_pessoa'] != 'PF' and _vazio(c['regime_tributario']):
        p.append('regime')
    if c['n_contatos_sem_celular']:
        p.append('contato_celular')
    if _vazio(c['celular']):
        p.append('celular')
    if not c['n_grupos']:
        p.append('grupo')
    if not c['n_ramos']:
        p.append('ramo')
    if 'conferir' in (c['observacoes'] or '').lower():
        p.append('conferir')
    if c['n_ie_conferir']:
        p.append('ie_conferir')
    return p


def pendencias_de(ids):
    """{cliente_id: [rótulos]} das pendências dos cadastros pedidos (Clientes e Avulsos). Quem não está no dicionário
    está completo. Usado pelo contrato de prestação: "se tiver no A CADASTRAR esquece" (Anderson, 06/11/2026)."""
    ids = set(ids)
    rot_e = {k: r for k, r, *_ in PENDENCIAS_EMPRESA}
    rot_a = {k: r for k, r, *_ in PENDENCIAS_AVULSO}
    out = {}
    for c in empresas_pendentes():
        if c['id'] in ids:
            out[c['id']] = [rot_e[k] for k in c['pendencias']]
    for c in avulsos_pendentes():
        if c['id'] in ids:
            out[c['id']] = [rot_a[k] for k in c['pendencias']]
    return out


def contadores(lista, catalogo):
    """{chave: quantos cartões têm essa pendência} para as pílulas de filtro."""
    n = {k: 0 for k, *_ in catalogo}
    for c in lista:
        for k in c['pendencias']:
            n[k] += 1
    return n


# ── Tela única do cadastro (Modelo C, 08/11/2026) ─────────────────────────────────────────────
# Onde cada pendência se resolve na ficha: a âncora para onde a página rola e, quando é campo, o campo
# que abre já editável. O A CADASTRAR usa o mesmo mapa: clicar no NIRE leva ao NIRE.
ALVO = {
    'nire': 'campo-nire', 'nire_errado': 'campo-nire', 'im': 'campo-im', 'ie': 'campo-ie', 'regime': 'campo-regime',
    'cobranca': 'campo-cobranca', 'celular': 'campo-celular', 'ramo': 'campo-ramos',
    'origem': 'campo-origem', 'conferir': 'campo-observacoes',
    'grupo': 'campo-grupo', 'socios': 'socios', 'email': 'contatos', 'contato_celular': 'contatos',
    'contato': 'contatos', 'contador': 'contadores', 'documento': 'dados-pessoais',
    'ie_rural': 'ies-rural', 'ie_conferir': 'campo-ie',
}

_SQL_UM = """
SELECT c.id, c.avulso, c.tipo_pessoa, c.nire, c.inscricao_municipal, c.inscricao_estadual, c.email, c.celular,
       c.cobranca, c.aberta_pela_casa, c.contabilidade_anterior_id, c.regime_tributario, c.observacoes,
       (SELECT COUNT(*) FROM socios_clientes s WHERE s.cliente_id = c.id AND s.ativo = 1) AS n_socios,
       (SELECT COALESCE(SUM(s.percentual_participacao), 0) FROM socios_clientes s WHERE s.cliente_id = c.id AND s.ativo = 1) AS pct_socios,
       (SELECT COUNT(*) FROM contatos_clientes k WHERE k.cliente_id = c.id AND k.ativo = 1) AS n_contatos,
       (SELECT COUNT(*) FROM contatos_clientes k WHERE k.cliente_id = c.id AND k.ativo = 1 AND k.email IS NOT NULL AND k.email <> '') AS n_contatos_email,
       (SELECT COUNT(*) FROM contatos_clientes k WHERE k.cliente_id = c.id AND k.ativo = 1 AND (k.celular IS NULL OR k.celular = '')) AS n_contatos_sem_celular,
       (SELECT GROUP_CONCAT(g.nome ORDER BY g.nome SEPARATOR ', ') FROM cliente_grupo_relacao r JOIN grupos_clientes g ON g.id = r.grupo_id WHERE r.cliente_id = c.id) AS grupos,
       (SELECT COUNT(*) FROM cliente_grupo_relacao r WHERE r.cliente_id = c.id) AS n_grupos,
       (SELECT GROUP_CONCAT(ra.nome ORDER BY ra.nome SEPARATOR ', ') FROM cliente_ramo_atividade_relacao r JOIN ramos_atividade ra ON ra.id = r.ramo_atividade_id WHERE r.cliente_id = c.id) AS ramos,
       (SELECT COUNT(*) FROM cliente_ramo_atividade_relacao r WHERE r.cliente_id = c.id) AS n_ramos,
       (SELECT GROUP_CONCAT(k2.nome_razao_social SEPARATOR ', ') FROM cliente_contadores cc JOIN clientes k2 ON k2.id = cc.contador_id WHERE cc.cliente_id = c.id) AS contadores,
       (SELECT COUNT(*) FROM cliente_contadores cc WHERE cc.cliente_id = c.id) AS n_contadores,
       (SELECT COUNT(*) FROM clientes_inscricoes_estaduais ie WHERE ie.cliente_id = c.id AND ie.isenta = 1) AS n_ie_isenta,
       (SELECT COUNT(*) FROM clientes_pf_documentos d WHERE d.cliente_id = c.id) AS n_docs,
       (SELECT COUNT(*) FROM clientes_inscricoes_estaduais i WHERE i.cliente_id = c.id AND i.conferir = 1) AS n_ie_conferir,
       (SELECT COUNT(*) FROM clientes_inscricoes_estaduais i WHERE i.cliente_id = c.id AND i.tipo = 'RURAL') AS n_ie_rural,
       (SELECT COUNT(*) FROM cliente_ramo_atividade_relacao x JOIN ramos_atividade ra ON ra.id = x.ramo_atividade_id
         WHERE x.cliente_id = c.id AND ra.tipo_pessoa = 'PF' AND UPPER(ra.nome) LIKE '%%RURAL%%') AS n_ramo_rural,
       (SELECT nome FROM contabilidades ca WHERE ca.id = c.contabilidade_anterior_id) AS contab_nome
  FROM clientes c
 WHERE c.id = %s
"""

_REGIMES = {'SIMPLES': 'Simples', 'LUCRO_PRESUMIDO': 'Lucro Presumido', 'LUCRO_REAL': 'Lucro Real', 'MEI': 'MEI'}
_COBR = {'PROPRIA': 'própria', 'VIA_GRUPO': 'via grupo'}


def pendencias_do_cadastro(cliente_id):
    """O "O que falta" da ficha: a MESMA regra do A CADASTRAR, para um cadastro só.

    Devolve {'faltam': [{chave, rotulo, cor, ajuda, alvo}], 'ok': [{chave, rotulo, valor}], 'avulso': bool}.
    Antes o "Antes de salvar" do Editar tinha uma lista própria: a Qualicontax aparecia toda verde lá e com
    NIRE e Financeiro pendentes no A CADASTRAR (Anderson, 07/11/2026: "ajustar bem")."""
    c = execute_query(_SQL_UM, (cliente_id,), fetch=True, fetch_one=True)
    if not c:
        return {'faltam': [], 'ok': [], 'avulso': False}
    avulso = bool(c['avulso'])
    catalogo = PENDENCIAS_AVULSO if avulso else PENDENCIAS_EMPRESA
    p = set(_pend_avulso(c) if avulso else _pend_empresa(c))
    pj = c['tipo_pessoa'] != 'PF'

    def valor(k):
        if k in ('nire', 'nire_errado'):
            return c['nire']
        if k == 'im':
            return c['inscricao_municipal']
        if k == 'ie':
            return c['inscricao_estadual'] or ('isenta' if c['n_ie_isenta'] else '')
        if k == 'socios':
            return f"{c['n_socios']}, somando {float(c['pct_socios'] or 0):g}%"
        if k == 'regime':
            return _REGIMES.get(c['regime_tributario'], c['regime_tributario'])
        if k == 'cobranca':
            return _COBR.get(c['cobranca'])
        if k == 'email':
            return c['email'] if avulso or c['email'] else f"{c['n_contatos_email']} de {c['n_contatos']} contatos"
        if k == 'contato_celular':
            return 'todos têm' if c['n_contatos'] else 'nenhum contato'
        if k == 'contato':
            return f"{c['n_contatos']} contato(s)"
        if k == 'documento':
            return f"{c['n_docs']} documento(s)"
        if k == 'ie_rural':
            return f"{c['n_ie_rural']} inscrição(ões)"
        if k == 'ie_conferir':
            return 'conferidas'
        if k == 'grupo':
            return c['grupos']
        if k == 'celular':
            return c['celular']
        if k == 'ramo':
            return c['ramos']
        if k == 'contador':
            return c['contadores']
        if k == 'origem':
            return 'aberta pela Qualicontax' if c['aberta_pela_casa'] else c['contab_nome']
        return ''

    # O que não se aplica a este cadastro (ex.: NIRE de pessoa física) não entra em nenhuma das listas.
    so_pj = {'nire', 'nire_errado', 'im', 'ie', 'socios', 'regime', 'email', 'origem'} if not avulso else {'regime'}
    so_pf = {'contato', 'documento', 'ie_rural'} if not avulso else set()
    faltam, ok = [], []
    for k, rotulo, cor, ajuda in catalogo:
        if (k in so_pj and not pj) or (k in so_pf and pj):
            continue
        if k in ('conferir', 'ie_conferir', 'nire_errado') and k not in p:
            continue
        if k == 'ie_rural' and not c['n_ramo_rural']:
            continue                      # só o produtor rural precisa de IE
        if k in p:
            if k == 'nire_errado':
                ajuda = 'NIRE errado: o gravado (%s) tem %d números e o certo são 11 (XX.XXX.XXX.XXX): conferir na ficha da Junta e corrigir' % (
                    c['nire'], len(''.join(ch for ch in str(c['nire']) if ch.isdigit())))
            faltam.append({'chave': k, 'rotulo': rotulo, 'cor': cor, 'ajuda': ajuda, 'alvo': ALVO.get(k, '')})
        else:
            ok.append({'chave': k, 'rotulo': rotulo, 'valor': valor(k) or ''})
    # vermelho primeiro, depois âmbar, depois cinza (o que trava mais aparece antes)
    ordem = {'verm': 0, 'amb': 1, 'cinza': 2}
    faltam.sort(key=lambda f: ordem.get(f['cor'], 3))
    return {'faltam': faltam, 'ok': ok, 'avulso': avulso}
