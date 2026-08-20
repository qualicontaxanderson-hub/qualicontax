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
        # O código da permissão continua conf_compras (não renomeado, para não
        # invalidar os perfis já cadastrados) — só o rótulo mudou.
        'escrita_fiscal.conf_compras':    'Conferência de Entradas',
        'escrita_fiscal.conf_cte':        'Conferência de CT-e',
        # Nasce SEM vínculo com perfil nenhum: o admin já enxerga por
        # has_permission, e liberar para um perfil vira um clique quando o
        # Anderson decidir. Nada de @admin_required aqui — foi a lição da Caixa
        # de entrada em 13/08/2026.
        'escrita_fiscal.conf_nfse':       'Conferência de NFS-e',
        'escrita_fiscal.conf_saidas':     'Conferência de Saídas',
        'escrita_fiscal.q_robo':          'Painel do Q-Robô',
        'escrita_fiscal.produtos_catalogo': 'Cadastro de Produtos',
        'escrita_fiscal.memorizacoes':    'Memorizações',
    },
    'Q-Robô': {
        # Portal do Instalador (/qrobo): quem pode entrar na sessão de escopo
        # restrito para baixar o instalador e gerar a chave do posto. NÃO dá
        # acesso a nenhuma tela fiscal — o gate em routes/qrobo.py bloqueia.
        'qrobo.instalador':               'Portal do Instalador',
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
        'financeiro.titulos':             'Contas a pagar e receber (escritório)',
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
        # A caixa era @admin_required, e admin não passa por perfil: para
        # delegar a alguém a tarefa de organizar o _ENTRADA, só havia o caminho
        # de fazer a pessoa admin — o que entregava junto usuários e perfis.
        # Esta permissão existe para haver o meio-termo.
        #
        # VER e RENOMEAR ficam na MESMA permissão, por decisão do Anderson em
        # 13/08/2026: quem entra na caixa entra para organizá-la, e separar em
        # duas criaria um perfil que enxerga o problema e não pode resolver.
        'configuracoes.caixa_entrada':    'Caixa de entrada (ver e renomear)',
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
