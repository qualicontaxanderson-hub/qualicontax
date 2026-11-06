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
    ('im', 'Insc. Municipal', 'amb', 'inscrição municipal (CCM)'),
    ('ie', 'Insc. Estadual', 'amb', 'inscrição estadual, ou marcar isenta'),
    ('socios', 'Sócios', 'verm', 'nenhum sócio cadastrado'),
    ('regime', 'Regime', 'verm', 'regime tributário em branco: Simples, Lucro Presumido, Lucro Real ou MEI (a Receita só diz se é optante do Simples)'),
    ('cobranca', 'Financeiro (cobrança)', 'verm', 'como cobrar: própria, via grupo ou pendente'),
    ('email', 'Contato com e-mail', 'verm', 'nenhum contato com e-mail (é por eles que vão boletos e documentos)'),
    ('contato_celular', 'Celular do contato', 'amb', 'há contato sem celular (obrigatório desde 05/11/2026)'),
    ('contato', 'Contato', 'verm', 'pessoa física sem nenhum contato (nome + celular)'),
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
  LEFT JOIN (SELECT cliente_id, MAX(cidade) AS cidade, MAX(estado) AS uf FROM enderecos_clientes GROUP BY cliente_id) e ON e.cliente_id = c.id
 WHERE c.avulso = 0 AND c.numero_cliente IS NOT NULL AND c.situacao = 'ATIVO'
 ORDER BY CAST(c.numero_cliente AS UNSIGNED), c.numero_cliente
"""

_SQL_AVULSOS = """
SELECT c.id, c.nome_razao_social, c.cpf_cnpj, c.tipo_pessoa, c.situacao, c.email, c.celular, c.telefone,
       c.avulso_em, c.conexa_id, c.emite_nf, c.cobranca, c.observacoes, c.regime_tributario,
       (SELECT COUNT(*) FROM contatos_clientes k3 WHERE k3.cliente_id = c.id AND k3.ativo = 1 AND (k3.celular IS NULL OR k3.celular = '')) AS n_contatos_sem_celular,
       (SELECT COUNT(*) FROM cliente_grupo_relacao g WHERE g.cliente_id = c.id) AS n_grupos,
       (SELECT COUNT(*) FROM cliente_ramo_atividade_relacao r WHERE r.cliente_id = c.id) AS n_ramos
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
        pj = c['tipo_pessoa'] != 'PF'
        p = []
        if pj and _vazio(c['nire']):
            p.append('nire')
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
        if p:
            c['pendencias'] = p
            out.append(c)
    return out


def avulsos_pendentes(ocultas=frozenset()):
    out = []
    for c in execute_query(_SQL_AVULSOS, fetch=True) or []:
        if c['id'] in ocultas:
            continue
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
        if p:  # avulso completo fica só na aba Avulsos (Anderson, 05/11/2026)
            c['pendencias'] = p
            out.append(c)
    return out


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
