"""
Catálogo central de permissões do sistema.

Adicionar novas permissões aqui para que apareçam automaticamente
na tela de edição de perfis de acesso.
"""

# Formato:  'codigo.da.permissao': 'Rótulo legível'
# Os códigos seguem o padrão  <modulo>.<funcionalidade>

PERMISSION_CATALOG = {
    'Escrita Fiscal': {
        'escrita_fiscal.index':           'Acessar Escrita Fiscal',
        'escrita_fiscal.conf_compras':    'Conferência de Compras',
        'escrita_fiscal.produtos_catalogo': 'Cadastro de Produtos',
        'escrita_fiscal.memorizacoes':    'Memorizações',
    },
    'Contábil': {
        'contabil.index':                 'Acessar Contábil',
        'contabil.conciliacoes':          'Conciliações Bancárias',
        'contabil.plano_contas':          'Plano de Contas',
        'contabil.importar_ofx':          'Importar OFX',
        'contabil.importar_pdf':          'Importar PDF',
    },
    'Financeiro': {
        'financeiro.index':               'Acessar Financeiro',
        'financeiro.recebimento':         'Recebimentos',
    },
    'Cadastros': {
        'clientes.index':                 'Listar Clientes',
        'clientes.create':                'Criar Clientes',
        'clientes.edit':                  'Editar Clientes',
        'contratos.list_contratos':       'Listar Contratos',
        'contratos.create_contrato':      'Criar Contratos',
        'grupos.index':                   'Grupos de Clientes',
    },
    'Relatórios': {
        'relatorios.index':               'Acessar Relatórios',
        'relatorios.clientes':            'Relatório de Clientes',
        'relatorios.processos':           'Relatório de Processos',
        'relatorios.conf_despesas':       'Conferência de Despesas',
    },
    'Configurações': {
        'configuracoes.index':            'Acessar Configurações',
        'configuracoes.usuarios':         'Gerenciar Usuários',
        'configuracoes.perfis':           'Gerenciar Perfis de Acesso',
    },
    'Dashboard': {
        'dashboard.index':                'Acessar Dashboard',
    },
    'DP / Legalização': {
        'modulos.dp':                     'Acessar DP',
        'modulos.legalizacao':            'Acessar Legalização',
        'modulos.analise':                'Acessar Análise',
        'modulos.comercial':              'Acessar Comercial',
    },
}


def get_flat_catalog():
    """Retorna dict plano {codigo: label} com todas as permissões."""
    result = {}
    for _grp_perms in PERMISSION_CATALOG.values():
        result.update(_grp_perms)
    return result
