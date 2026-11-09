"""
Catálogo central de permissões do sistema — por TELA, com Ver / Incluir / Alterar / Excluir.

POR QUE MUDOU (09/11/2026)
--------------------------
Fora de Clientes, cada tela tinha UMA permissão que abria, editava e excluía:
"Grupos de Clientes" excluía grupo, "Memorizações" excluía memorização, a
"Conferência de Entradas" ligava a Ciência da Operação (20 pessoas). Anderson:
"precisamos em tudo ter opções e ver se vamos dar poder de excluir porque o
usuário é devagar e pode excluir".

REGRA (Anderson, 09/11/2026): "sempre que criar um departamento ou uma atividade
colocar sempre INCLUIR e EDITAR [para] os usuários, mas EXCLUSÃO eu vou
liberando aos poucos". Então tela nova entra aqui com as quatro ações; o script
de perfis dá Ver/Incluir/Alterar a quem usa a tela; Excluir e as ações
irreversíveis (``risco``) nascem só com o administrador.

O código do VER é o código antigo da tela (``escrita_fiscal.conf_compras``...):
nenhum perfil cadastrado perde nada e os ``pode_rota``/``has_permission`` que já
existem continuam valendo. As ações novas são ``<código do ver>.incluir`` etc.

Quem ESCREVE a regra de cada rota é ``utils/acesso.py`` (MAPA). Este arquivo só
diz quais caixas existem e o que cada uma faz, na língua da equipe.
"""

# Cada tela:
#   id       — identificador curto (usado só na tela de Perfis)
#   nome     — como a equipe chama a tela
#   ver      — (código, o que a pessoa vê)            ou None
#   inc/alt  — (código, o que a pessoa faz)           ou None
#   exc      — lista de (código, o que apaga/cancela)
#   outros   — lista de (código, ação, risco)  risco=True: irreversível, nasce sem perfil
#   trava    — ações que NENHUM perfil recebe (só administrador), só para mostrar
DEPARTAMENTOS_PERMISSOES = [
    {'grupo': 'Escrita Fiscal', 'sigla': 'EF', 'cor': 'ef', 'telas': [
        {'id': 'ef', 'nome': 'Acessar a Escrita Fiscal',
         'ver': ('escrita_fiscal.index', 'abrir o painel')},
        {'id': 'ent', 'nome': 'Entradas (NF-e)',
         'ver': ('escrita_fiscal.conf_compras', 'notas, itens e relatórios'),
         'inc': ('escrita_fiscal.conf_compras.incluir', 'importar XML e Dropbox'),
         'alt': ('escrita_fiscal.conf_compras.alterar', 'vincular produto'),
         'exc': [('escrita_fiscal.conf_compras.excluir', 'excluir nota e lote')],
         'outros': [('escrita_fiscal.conf_compras.lote', 'baixar XML/PDF em lote', False)]},
        {'id': 'sef', 'nome': 'Status da captura SEFAZ',
         'ver': ('escrita_fiscal.status_sefaz', 'painel por empresa'),
         'outros': [('escrita_fiscal.status_sefaz.captura', 'ligar/desligar a captura', True)],
         'trava': ['Ciência da Operação']},
        {'id': 'sai', 'nome': 'Saídas',
         'ver': ('escrita_fiscal.conf_saidas', 'notas e relatórios'),
         'exc': [('escrita_fiscal.conf_saidas.excluir', 'excluir lote')],
         'outros': [('escrita_fiscal.conf_saidas.lote', 'baixar XML/PDF em lote', False)]},
        {'id': 'cte', 'nome': 'CT-e',
         'ver': ('escrita_fiscal.conf_cte', 'CT-e e relatórios'),
         'inc': ('escrita_fiscal.conf_cte.incluir', 'importar XML'),
         'exc': [('escrita_fiscal.conf_cte.excluir', 'excluir CT-e e lote')],
         'outros': [('escrita_fiscal.conf_cte.lote', 'baixar XML/PDF em lote', False)]},
        {'id': 'nfr', 'nome': 'NFS-e recebidas',
         'ver': ('escrita_fiscal.conf_nfse', 'notas e relatórios'),
         'outros': [('escrita_fiscal.conf_nfse.lote', 'baixar XML/PDF em lote', False)]},
        {'id': 'prod', 'nome': 'Cadastro de Produtos',
         'ver': ('escrita_fiscal.produtos_catalogo', 'catálogo'),
         'inc': ('escrita_fiscal.produtos_catalogo.incluir', 'produto e categoria'),
         'alt': ('escrita_fiscal.produtos_catalogo.alterar', 'editar produto'),
         'exc': [('escrita_fiscal.produtos_catalogo.excluir', 'produto, categoria e subcategoria')]},
        {'id': 'mem', 'nome': 'Memorizações',
         'ver': ('escrita_fiscal.memorizacoes', 'regras por empresa'),
         'inc': ('escrita_fiscal.memorizacoes.incluir', 'clonar e incluir no conjunto'),
         'alt': ('escrita_fiscal.memorizacoes.alterar', 'editar e nomear conjunto'),
         'exc': [('escrita_fiscal.memorizacoes.excluir', 'excluir e desvincular')]},
        {'id': 'qr', 'nome': 'Painel do Q-Robô',
         'ver': ('escrita_fiscal.q_robo', 'postos e máquinas'),
         'alt': ('escrita_fiscal.q_robo.alterar', 'renomear e ocultar máquina'),
         'outros': [('escrita_fiscal.q_robo.revelar', 'revelar a chave atual', False),
                    ('escrita_fiscal.q_robo.gerar', 'gerar chave nova', True)]},
        {'id': 'nfe', 'nome': 'Emitir NFS-e dos clientes',
         'ver': ('escrita_fiscal.nfse_emitir', 'notas emitidas, PDF e XML'),
         'inc': ('escrita_fiscal.nfse_emitir.incluir', 'emitir nota'),
         'alt': ('escrita_fiscal.nfse_emitir.alterar', 'tomadores'),
         'exc': [('escrita_fiscal.nfse_emitir.excluir', 'cancelar nota')]},
        {'id': 'nfm', 'nome': 'Configurar emissão de NFS-e',
         'ver': ('escrita_fiscal.nfse_matriz', 'matriz da empresa'),
         'alt': ('escrita_fiscal.nfse_matriz.alterar', 'matriz e série'),
         'outros': [('escrita_fiscal.nfse_matriz.liberar', 'liberar a emissão', True)]},
    ]},
    {'grupo': 'Q-Robô', 'sigla': 'QR', 'cor': 'qr', 'telas': [
        # Portal do Instalador (/qrobo): sessão de escopo restrito para baixar o
        # instalador e gerar a chave do posto. NÃO abre tela fiscal nenhuma.
        {'id': 'inst', 'nome': 'Portal do Instalador',
         'ver': ('qrobo.instalador', 'baixar o instalador e gerar a chave do posto')},
    ]},
    {'grupo': 'Cadastros', 'sigla': 'CA', 'cor': 'cad', 'telas': [
        {'id': 'cli', 'nome': 'Clientes',
         'ver': ('clientes.index', 'lista, ficha e A CADASTRAR'),
         'inc': ('clientes.create', 'nova empresa'),
         'alt': ('clientes.edit', 'editar a ficha'),
         'exc': [('clientes.delete', 'excluir a empresa'),
                 ('clientes.excluir_itens', 'sócio, contato, endereço, ANP')],
         'outros': [('clientes.inativar', 'inativar e reativar', False),
                    ('clientes.dfe_captura', 'captura manual na SEFAZ', False)]},
        {'id': 'gru', 'nome': 'Grupos de Clientes',
         'ver': ('grupos.index', 'lista e painel'),
         'inc': ('grupos.incluir', 'novo grupo'),
         'alt': ('grupos.alterar', 'renomear, incluir e mover empresa'),
         'exc': [('grupos.excluir', 'excluir e desativar')]},
        {'id': 'tab', 'nome': 'Ramos, municípios e contabilidades',
         'ver': ('cadastros.tabelas', 'listas'),
         'inc': ('cadastros.tabelas.incluir', 'novo'),
         'alt': ('cadastros.tabelas.alterar', 'editar'),
         'exc': [('cadastros.tabelas.excluir', 'excluir')]},
    ]},
    {'grupo': 'Contábil', 'sigla': 'CT', 'cor': 'ct', 'telas': [
        {'id': 'ct', 'nome': 'Acessar o Contábil', 'ver': ('contabil.index', 'abrir o painel')},
        {'id': 'pc', 'nome': 'Plano de Contas',
         'ver': ('contabil.plano_contas', 'planos e contas'),
         'inc': ('contabil.plano_contas.incluir', 'plano, conta e importar CSV'),
         'exc': [('contabil.plano_contas.excluir', 'excluir conta e plano')]},
        {'id': 'con', 'nome': 'Conciliações Bancárias',
         'ver': ('contabil.conciliacoes', 'conciliações, extrato e memorizações'),
         'inc': ('contabil.conciliacoes.incluir', 'nova conciliação e memorização'),
         'alt': ('contabil.conciliacoes.alterar', 'contas correntes e memorizações'),
         'exc': [('contabil.conciliacoes.excluir', 'excluir memorização')]},
        {'id': 'ofx', 'nome': 'Importar OFX', 'ver': ('contabil.importar_ofx', 'importar extrato OFX')},
        {'id': 'pdf', 'nome': 'Importar PDF', 'ver': ('contabil.importar_pdf', 'importar extrato PDF')},
    ]},
    {'grupo': 'Financeiro', 'sigla': 'FI', 'cor': 'fin', 'telas': [
        {'id': 'fin', 'nome': 'Acessar o Financeiro', 'ver': ('financeiro.index', 'abrir o painel')},
        {'id': 'tit', 'nome': 'Contas a pagar e receber',
         'ver': ('financeiro.titulos', 'títulos'),
         'inc': ('financeiro.titulos.incluir', 'novo título'),
         'alt': ('financeiro.titulos.alterar', 'dar baixa'),
         'exc': [('financeiro.titulos.excluir', 'cancelar e excluir título')]},
        {'id': 'rec', 'nome': 'Recebimentos',
         'ver': ('financeiro.recebimento', 'recebimentos'),
         'exc': [('financeiro.recebimento.excluir', 'excluir um e em lote')]},
        {'id': 'ext', 'nome': 'Extrato bancário',
         'ver': ('financeiro.extrato', 'extrato e pendências'),
         'inc': ('financeiro.extrato.incluir', 'importar arquivo'),
         'alt': ('financeiro.extrato.alterar', 'classificar, confirmar e regras'),
         'exc': [('financeiro.extrato.excluir', 'apagar período')],
         'outros': [('financeiro.extrato.transferencias', 'aprovar transferências do grupo', True)]},
        {'id': 'cb', 'nome': 'Contas bancárias',
         'ver': ('financeiro.contas', 'contas'),
         'inc': ('financeiro.contas.incluir', 'nova conta'),
         'alt': ('financeiro.contas.alterar', 'ativar e desativar')},
        {'id': 'cat', 'nome': 'Categorias e centros de custo',
         'ver': ('financeiro.categorias', 'plano'),
         'inc': ('financeiro.categorias.incluir', 'nova'),
         'alt': ('financeiro.categorias.alterar', 'renomear e desativar')},
        {'id': 'dre', 'nome': 'DRE gerencial', 'ver': ('financeiro.dre', 'relatório')},
        {'id': 'flu', 'nome': 'Fluxo de caixa',
         'ver': ('financeiro.fluxo', 'relatório'),
         'alt': ('financeiro.fluxo.alterar', 'saldo inicial')},
        {'id': 'emp', 'nome': 'Minhas empresas do financeiro',
         'ver': ('financeiro.empresas', 'lista'),
         'alt': ('financeiro.empresas.alterar', 'marcar e atualizar')},
        {'id': 'prg', 'nome': 'Programações (contas do mês)',
         'ver': ('financeiro.programacoes', 'programações'),
         'inc': ('financeiro.programacoes.incluir', 'nova'),
         'alt': ('financeiro.programacoes.alterar', 'editar'),
         'outros': [('financeiro.programacoes.gerar', 'gerar as contas do mês', False)]},
        {'id': 'nts', 'nome': 'Notas fiscais de serviço',
         'ver': ('financeiro.notas', 'notas, PDF e XML'),
         'inc': ('financeiro.notas.incluir', 'emitir nota'),
         'exc': [('financeiro.notas.excluir', 'cancelar nota')],
         'outros': [('financeiro.notas.configurar', 'configurar série e serviços', False)]},
        {'id': 'cob', 'nome': 'Cobrança e boletos',
         'ver': ('financeiro.cobranca', 'boletos e saldo'),
         'inc': ('financeiro.cobranca.incluir', 'gerar boleto'),
         'alt': ('financeiro.cobranca.alterar', 'reenviar e-mail e sincronizar'),
         'exc': [('financeiro.cobranca.excluir', 'cancelar boleto')],
         'outros': [('financeiro.cobranca.contas', 'contas Efí/Bradesco', True)]},
        {'id': 'hon', 'nome': 'Honorários de todas as empresas',
         'ver': ('financeiro.honorarios', 'contratos'),
         'alt': ('financeiro.honorarios.alterar', 'editar, reajustar e encerrar'),
         'outros': [('financeiro.honorarios.aprovar', 'aprovar e reprovar', False),
                    ('financeiro.honorarios.desfazer', 'desfazer reajuste', True)]},
        {'id': 'honc', 'nome': 'Honorários da empresa que cadastrei',
         'inc': ('financeiro.honorarios_cadastrar', 'cadastrar (vai para aprovação)')},
        {'id': 'lan', 'nome': 'Serviço avulso e comissões',
         'ver': ('financeiro.lancamentos', 'meus lançamentos'),
         'inc': ('financeiro.lancamentos.incluir', 'lançar serviço'),
         'exc': [('financeiro.lancamentos.excluir', 'cancelar lançamento')]},
        {'id': 'lot', 'nome': 'Lote mensal de honorários',
         'ver': ('financeiro.lote', 'lotes'),
         'inc': ('financeiro.lote.incluir', 'emitir o lote'),
         'outros': [('financeiro.lote.reabrir', 'reabrir lote', True)]},
    ]},
    {'grupo': 'Processos', 'sigla': 'PR', 'cor': 'pro', 'telas': [
        {'id': 'pro', 'nome': 'Processos',
         'ver': ('processos.index', 'lista e detalhe'),
         'inc': ('processos.incluir', 'novo processo')},
    ]},
    {'grupo': 'Relatórios', 'sigla': 'RE', 'cor': 'rel', 'telas': [
        {'id': 'rel', 'nome': 'Acessar Relatórios', 'ver': ('relatorios.index', 'abrir')},
        {'id': 'rcl', 'nome': 'Relatório de Clientes', 'ver': ('relatorios.clientes', 'gerar')},
        {'id': 'rpr', 'nome': 'Relatório de Processos', 'ver': ('relatorios.processos', 'gerar')},
        {'id': 'rcd', 'nome': 'Conferência de Despesas', 'ver': ('relatorios.conf_despesas', 'gerar')},
    ]},
    {'grupo': 'DP / Legalização', 'sigla': 'DP', 'cor': 'dp', 'telas': [
        {'id': 'dp', 'nome': 'DP', 'ver': ('modulos.dp', 'abrir')},
        {'id': 'leg', 'nome': 'Legalização', 'ver': ('modulos.legalizacao', 'abrir')},
        {'id': 'ana', 'nome': 'Análise', 'ver': ('modulos.analise', 'abrir')},
        {'id': 'com', 'nome': 'Comercial', 'ver': ('modulos.comercial', 'abrir')},
    ]},
    {'grupo': 'Configurações', 'sigla': 'CF', 'cor': 'cfg', 'telas': [
        # Usuários e Perfis são só do administrador (MAPA/decorador): a caixa
        # que existia aqui não fazia nada e saiu em 09/11/2026.
        {'id': 'cfg', 'nome': 'Acessar Configurações',
         'ver': ('configuracoes.index', 'abrir'),
         'trava': ['Usuários', 'Perfis de Acesso']},
        # 13/08/2026 a caixa era ver+renomear numa permissão só; desde 09/11 o
        # renomear é o "Alterar" — e quem tinha a caixa ganhou os dois.
        {'id': 'cx', 'nome': 'Caixa de entrada',
         'ver': ('configuracoes.caixa_entrada', 'arquivos que chegaram'),
         'alt': ('configuracoes.caixa_entrada.alterar', 'renomear')},
    ]},
]

ACOES = (('ver', 'Ver'), ('inc', 'Incluir'), ('alt', 'Alterar'))


def _itens_da_tela(t):
    """[(codigo, rotulo_curto, acao, risco)] de uma tela, na ordem da tela de Perfis."""
    out = []
    for chave, _ in ACOES:
        if t.get(chave):
            out.append((t[chave][0], t[chave][1], chave, False))
    for cod, txt in t.get('exc', []):
        out.append((cod, txt, 'exc', True))
    for cod, txt, risco in t.get('outros', []):
        out.append((cod, txt, 'outro', bool(risco)))
    return out


def codigos_da_tela(t):
    return [c for c, *_ in _itens_da_tela(t)]


def _monta_catalogo():
    cat = {}
    rotulo_acao = {'ver': 'ver', 'inc': 'incluir', 'alt': 'alterar', 'exc': 'excluir', 'outro': ''}
    for dep in DEPARTAMENTOS_PERMISSOES:
        g = cat.setdefault(dep['grupo'], {})
        for t in dep['telas']:
            for cod, txt, acao, _ in _itens_da_tela(t):
                pre = rotulo_acao[acao]
                g[cod] = f"{t['nome']}: {pre + ' — ' if pre else ''}{txt}"
    return cat


# Formato antigo {grupo: {codigo: rótulo}} — continua sendo o que o resto do
# sistema lê (quem_ve_empresa, test_acesso_portao, gravação do perfil).
PERMISSION_CATALOG = _monta_catalogo()


def get_flat_catalog():
    """Retorna dict plano {codigo: label} com todas as permissões."""
    result = {}
    for _grp_perms in PERMISSION_CATALOG.values():
        result.update(_grp_perms)
    return result


def codigos_de_risco():
    """Excluir e ações irreversíveis: nascem sem perfil, o admin libera um a um."""
    return {cod for dep in DEPARTAMENTOS_PERMISSOES for t in dep['telas']
            for cod, _, _, risco in _itens_da_tela(t) if risco}


def ver_da_tela():
    """{codigo: codigo_do_ver_da_mesma_tela} — marcar uma ação marca o Ver junto."""
    out = {}
    for dep in DEPARTAMENTOS_PERMISSOES:
        for t in dep['telas']:
            if not t.get('ver'):
                continue
            for cod in codigos_da_tela(t):
                out[cod] = t['ver'][0]
    return out


def normalizar_selecao(codigos):
    """O que o formulário mandou, limpo: só códigos do catálogo e, para cada
    ação marcada, o Ver da tela junto (o servidor garante, não só o JS)."""
    validos = set(get_flat_catalog())
    vers = ver_da_tela()
    sel = {c for c in codigos if c in validos}
    sel |= {vers[c] for c in sel if c in vers}
    return sel
