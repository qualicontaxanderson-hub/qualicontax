"""Natureza jurídica (tipo jurídico) da empresa — Fase A, 14/10/2026.

A Receita informa o CÓDIGO da tabela de naturezas (ex.: 2062 = Sociedade
Empresária Limitada). Aqui cada código vira um nome curto para o cartão e uma
FAMÍLIA para o filtro — é a família que diz o que a empresa tem de ter:
S/A tem acionistas, diretoria com mandato e AGO anual; Ltda tem sócios com
quotas e administrador; empresário individual tem só o titular.

A SLU (sociedade limitada unipessoal) vem da Receita com o MESMO código da
Ltda (2062) — a diferença é ter um sócio só; não há como separar pelo código.
"""

# código -> (nome curto do cartão, família)
NATUREZAS = {
    '2046': ('S/A Aberta', 'sa'),
    '2054': ('S/A Fechada', 'sa'),
    '2038': ('Sociedade de Economia Mista', 'sa'),
    '2097': ('Comandita por Ações', 'sa'),
    '2062': ('Ltda', 'ltda'),
    '2240': ('Sociedade Simples Ltda', 'ltda'),
    '2135': ('Empresário Individual', 'individual'),
    '2321': ('Soc. Unipessoal de Advocacia', 'individual'),
    '4014': ('Empresa Individual Imobiliária', 'individual'),
    '4120': ('Produtor Rural', 'individual'),
    '2305': ('Eireli', 'eireli'),
    '2313': ('Eireli (simples)', 'eireli'),
    '2232': ('Sociedade Simples', 'simples'),
    '2259': ('Soc. Simples em Nome Coletivo', 'simples'),
    '2267': ('Soc. Simples em Comandita', 'simples'),
    '2143': ('Cooperativa', 'coop'),
    '2330': ('Cooperativa de Consumo', 'coop'),
    '2070': ('Nome Coletivo', 'outros'),
    '2089': ('Comandita Simples', 'outros'),
    '2127': ('Conta de Participação', 'outros'),
    '2151': ('Consórcio de Sociedades', 'outros'),
    '2291': ('Consórcio Simples', 'outros'),
    '2348': ('Inova Simples', 'outros'),
    '3999': ('Associação', 'sem_fins'),
    '3069': ('Fundação Privada', 'sem_fins'),
    '3085': ('Condomínio Edilício', 'sem_fins'),
    '3034': ('Cartório', 'sem_fins'),
    '3131': ('Entidade Sindical', 'sem_fins'),
    '3220': ('Organização Religiosa', 'sem_fins'),
}

FAMILIAS = [
    ('sa', 'S/A'),
    ('ltda', 'Ltda'),
    ('individual', 'Empresário individual'),
    ('eireli', 'Eireli'),
    ('simples', 'Sociedade simples'),
    ('coop', 'Cooperativa'),
    ('sem_fins', 'Sem fins lucrativos'),
    ('outros', 'Outras'),
]


def classificar(codigo, descricao=None):
    """(nome curto, família) do código; sem código -> ('', ''); código fora
    da tabela usa a descrição da Receita e cai em 'outros' (ou 'sem_fins'
    para os 3xxx, que são todos de entidade sem fins lucrativos)."""
    cod = ''.join(ch for ch in str(codigo or '') if ch.isdigit())[:4]
    if not cod:
        return ('', '')
    if cod in NATUREZAS:
        return NATUREZAS[cod]
    if cod.startswith('1'):
        return (descricao or 'Administração pública', 'outros')
    return (descricao or f'Natureza {cod}', 'sem_fins' if cod.startswith('3') else 'outros')


def codigo_formatado(codigo):
    """'2062' -> '206-2' (como a Receita escreve)."""
    cod = ''.join(ch for ch in str(codigo or '') if ch.isdigit())
    return f'{cod[:3]}-{cod[3:]}' if len(cod) == 4 else cod
