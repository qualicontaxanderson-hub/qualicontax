# -*- coding: utf-8 -*-
"""Portão central de acesso — PERFIL e EMPRESA, negando por padrão.

POR QUE EXISTE (02/10/2026)
---------------------------
Antes de abrir o sistema à equipe, a auditoria achou 148 rotas que só
conferiam "está logado": o menu escondia o Financeiro de quem não tinha o
perfil, mas a URL digitada abria. A permissão estava certa onde havia
``@permission_required``; o defeito era depender de cada rota lembrar.

Este módulo roda ANTES de toda requisição (``app.before_request``) e decide
num lugar só, com duas regras pedidas pelo Anderson:

1. PERFIL FIEL — quem só tem Contábil entra só no Contábil. Toda rota tem de
   estar classificada: no ``MAPA`` abaixo ou pelo próprio decorador
   (``@permission_required`` / ``@admin_required`` deixam uma marca que o
   portão lê). Rota sem classificação é SÓ ADMIN — rota nova nasce fechada,
   nunca aberta. ``test_acesso_portao.py`` falha se aparecer uma.

2. EMPRESA RESERVADA — ``clientes.acesso_reservado = 1`` some para todo
   usuário que não é ADMIN, salvo quem está em ``cliente_acesso_excecao``
   para aquela empresa. O portão barra a URL com o id da empresa (ou de uma
   nota/CT-e/NFS-e/documento dela); as LISTAS usam ``empresas_ocultas()`` e
   ``sql_sem_ocultas()`` no ponto em que montam o WHERE.

ADMIN passa por tudo, como sempre passou (``has_permission`` já fazia assim).
"""
import logging

from flask import (abort, flash, g, has_request_context, jsonify, redirect,
                   request, url_for)
from flask_login import current_user

from utils.db_helper import execute_query, get_last_db_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regra 1 — o que cada rota exige
# ---------------------------------------------------------------------------
# Valores possíveis:
#   'LOGIN'        qualquer usuário logado (tela inicial, CEP, município...)
#   'ADMIN'        só administrador
#   'codigo'       a permissão do catálogo (utils/permissions.py)
#   ('a', 'b')     QUALQUER uma delas (ex.: o XML serve a Entradas e Saídas;
#                  a rota confere a exata pelo tipo da nota)
#
# Só entram aqui as rotas cujo decorador NÃO diz o bastante (só login). As de
# @permission_required / @admin_required o portão lê da marca do decorador.

BLUEPRINTS_PUBLICOS = {
    'auth',          # login/logout
    'cadastro',      # formulário público por token (Q-Colabore)
    'senha',         # definir senha por token
    'programa',      # download do instalador por link de uso único
    'manuais',       # manuais de instalação
    'colabore_api',  # API de máquina (chave Bearer por funcionário)
    'robo_saidas',   # API de máquina do Q-Robô (chave do posto)
    'qrobo',         # Portal do Instalador — login e gate próprios
    'cobranca_webhook',  # aviso de pagamento da Efí (segredo aleatório na URL; os dados vêm de consulta nossa)
    'cobranca_publica',  # links do e-mail de cobrança ao cliente (código aleatório do boleto na URL)
    'assinatura',        # assinatura eletrônica do contrato de prestação (token do signatário na URL + código por e-mail)
}
ENDPOINTS_PUBLICOS = {'static', 'health'}

_FISCAL_NOTA = ('escrita_fiscal.conf_compras', 'escrita_fiscal.conf_saidas')

MAPA = {
    # --- comuns a todo usuário logado ---------------------------------------
    'dashboard.index':                 'LOGIN',
    'dashboard.get_stats':             'LOGIN',
    'api.get_chart_data':              'LOGIN',
    'api.search_municipios':           'LOGIN',
    'api.search_clientes':             'LOGIN',   # a lista respeita a empresa reservada
    'clientes.buscar_cep':             'LOGIN',
    'clientes.consultar_cnpj':         'LOGIN',   # dado público da Receita, usado no cadastro

    # --- Cadastros: clientes ------------------------------------------------
    'clientes.novo':                   'clientes.create',
    'clientes.detalhes':               'clientes.index',
    'clientes.historico':              'clientes.index',
    'clientes.relatorio':              'clientes.index',   # PDF/Excel dos cartões da tela
    'clientes.socios_buscar_pf':       'clientes.index',
    'clientes.anp_detalhe':            'clientes.index',
    'clientes.cnpj_vigia_status':      'clientes.index',
    'clientes.editar':                 'clientes.edit',
    'clientes.salvar_campos':          'clientes.edit',   # tela única: o clique no valor grava (08/11/2026)
    'clientes.inativar':               'clientes.inativar',
    'clientes.adicionar_grupo':        'clientes.edit',
    'clientes.remover_grupo':          'clientes.edit',
    'clientes.novo_endereco':          'clientes.edit',
    'clientes.excluir_endereco':       'clientes.excluir_itens',
    'clientes.novo_contato':           'clientes.edit',
    'clientes.editar_contato':         'clientes.edit',
    'clientes.salvar_responsaveis':    'clientes.edit',
    # link dos responsáveis (06/11/2026): gerar = quem edita o cliente; responder = qualquer logado (o token diz a empresa,
    # e a rota só deixa escolher o próprio departamento)
    'clientes.responsaveis_convite_novo':    'clientes.edit',
    'clientes.responsaveis_convite':         'LOGIN',
    'clientes.responsaveis_convite_salvar':  'LOGIN',
    'clientes.excluir_contato':        'clientes.excluir_itens',
    'clientes.api_contato_por_celular': 'clientes.edit',   # reaproveitar contato pelo celular (05/11/2026)
    'clientes.novo_socio':             'clientes.edit',
    'clientes.excluir_socio':          'clientes.excluir_itens',
    'clientes.saida_socio':            'clientes.edit',   # ex-sócio com data de saída
    'clientes.a_cadastrar':            'clientes.index',  # A CADASTRAR: empresa pendente (05/11/2026)
    'clientes.a_cadastrar_avulsos':    'clientes.index',  # A CADASTRAR: avulsos pendentes
    'clientes.socios_fila':            'clientes.index',  # A CADASTRAR: sócios pendentes (ex-"Sócios a cadastrar")
    'clientes.socios_fila_vincular':   'clientes.edit',
    'clientes.socios_fila_ignorar':    'clientes.edit',
    'clientes.novo_cadastro_adicional':    'clientes.edit',
    'clientes.excluir_cadastro_adicional': 'clientes.excluir_itens',
    'clientes.vincular_contador':      'clientes.edit',
    'clientes.desvincular_contador':   'clientes.edit',
    'clientes.certificado_buscar':     'clientes.edit',
    'clientes.certificado_vincular':   'clientes.edit',
    'clientes.anp_novo':               'clientes.edit',
    'clientes.anp_editar':             'clientes.edit',
    'clientes.anp_salvar':             'clientes.edit',
    'clientes.anp_importar_pdf':       'clientes.edit',
    'clientes.anp_excluir':            'clientes.excluir_itens',
    'clientes.cnpj_vigia_armar':       'clientes.edit',
    'clientes.cnpj_vigia_acao':        'clientes.edit',
    'clientes.delete':                 'clientes.delete',
    # ajudantes do formulário de cliente (07/10/2026) — sem esta linha caíam em
    # "só admin" e a equipe não conseguia criar grupo/ramo nem buscar sócio
    'clientes.cadastros_buscar':       ('clientes.create', 'clientes.edit'),
    'clientes.socios_da_receita':      ('clientes.create', 'clientes.edit'),
    'clientes.socios_da_raiz':         ('clientes.create', 'clientes.edit'),   # quadro da raiz (matriz/filiais)
    'clientes.proximo_numero_pf':      ('clientes.create', 'clientes.edit'),
    'clientes.ramo_rapido':            ('clientes.create', 'clientes.edit'),
    'clientes.grupo_rapido':           ('clientes.create', 'clientes.edit', 'grupos.index'),
    'contabilidades.rapido':           ('clientes.create', 'clientes.edit'),  # "Novo escritório" no cadastro
    'adicionais.index':                'clientes.index',
    'adicionais.sync_dropbox_anp':     'clientes.edit',
    'documentos.download':             'clientes.index',
    'documentos.upload':               'clientes.edit',

    # --- Cadastros: grupos, ramos, municípios -------------------------------
    'grupos.detalhes':                 'grupos.index',
    'grupos.novo':                     'grupos.incluir',
    'grupos.editar':                   'grupos.alterar',
    'grupos.deletar':                  'grupos.excluir',
    'grupos.adicionar_cliente':        'grupos.alterar',
    'grupos.remover_cliente':          'grupos.alterar',
    'ramos_atividade.index':           'cadastros.tabelas',
    'ramos_atividade.detalhes':        'cadastros.tabelas',
    'ramos_atividade.novo':            'cadastros.tabelas.incluir',
    'ramos_atividade.editar':          'cadastros.tabelas.alterar',
    'ramos_atividade.deletar':         'cadastros.tabelas.excluir',
    'ramos_atividade.adicionar_cliente': 'cadastros.tabelas.alterar',
    'ramos_atividade.remover_cliente': 'cadastros.tabelas.alterar',
    'municipios.index':                'cadastros.tabelas',
    'municipios.novo':                 'cadastros.tabelas.incluir',
    'municipios.editar':               'cadastros.tabelas.alterar',
    'municipios.deletar':              'cadastros.tabelas.excluir',
    'contratos.create':                'LOGIN',   # redireciona para financeiro.contrato_novo (06/11/2026)
    'contratos.list_contratos':        'LOGIN',   # redireciona para financeiro.contratos
    'processos.list_processos':        'processos.index',
    'processos.view':                  'processos.index',
    'processos.create':                'processos.incluir',

    # --- Captura manual na SEFAZ (gasta a cota do CNPJ) ----------------------
    'dfe.dfe_consultar':               'clientes.dfe_captura',
    'dfe.dfe_capturar':                'clientes.dfe_captura',
    'dfe.cte_consultar':               'clientes.dfe_captura',
    'dfe.cte_capturar':                'clientes.dfe_captura',
    'dfe.cte_buscar_nsu':              'clientes.dfe_captura',
    'dfe.dfe_seed_nsu':                'ADMIN',
    'dfe.cte_seed_nsu':                'ADMIN',
    'dfe.dfe_sched_status':            'ADMIN',

    # --- Infraestrutura -----------------------------------------------------
    'dropbox.dropbox_auth':            'ADMIN',
    'dropbox.dropbox_auth_url':        'ADMIN',
    'dropbox.dropbox_callback':        'ADMIN',
    'dropbox.dropbox_test':            'ADMIN',
    'configuracoes.colabore_baixar':   'ADMIN',
    'configuracoes.colabore_instalador_info': 'ADMIN',

    # --- Contábil -----------------------------------------------------------
    'contabil.criar_plano_contas':     'contabil.plano_contas.incluir',
    'contabil.ver_plano_contas':       'contabil.plano_contas',
    'contabil.nova_conta_plano':       'contabil.plano_contas.incluir',
    'contabil.excluir_conta_plano':    'contabil.plano_contas.excluir',
    'contabil.excluir_plano_contas':   'contabil.plano_contas.excluir',
    'contabil.template_csv_plano_contas': 'contabil.plano_contas',
    'contabil.importar_plano_contas':  'contabil.plano_contas.incluir',
    'contabil.nova_conciliacao':       'contabil.conciliacoes.incluir',
    'contabil.ver_conciliacao':        'contabil.conciliacoes',
    'contabil.extrato_conciliacao':    'contabil.conciliacoes',
    'contabil.contas_correntes':       'contabil.conciliacoes',
    'contabil.memorizacoes':           'contabil.conciliacoes',
    'contabil.nova_memorizacao':       'contabil.conciliacoes.incluir',
    'contabil.editar_memorizacao':     'contabil.conciliacoes.alterar',
    'contabil.excluir_memorizacao':    'contabil.conciliacoes.excluir',
    'contabil.api_cliente_grupos':     'contabil.conciliacoes',
    'contabil.importar_ofx':           'contabil.importar_ofx',
    'contabil.importar_pdf':           'contabil.importar_pdf',
    'contabil.confirmar_importacao_pdf': 'contabil.importar_pdf',

    # --- Financeiro ---------------------------------------------------------
    'financeiro.recebimento_excluir':      'financeiro.recebimento.excluir',
    'financeiro.recebimento_excluir_lote': 'financeiro.recebimento.excluir',
    # Honorários (06/11/2026): a permissão nasce sem perfil — só admin até marcar em Perfis
    'financeiro.honorarios':               'financeiro.honorarios',
    'financeiro.honorarios_json':          'financeiro.honorarios',
    'financeiro.honorarios_salvar':        'financeiro.honorarios.alterar',
    'financeiro.honorarios_encerrar':      'financeiro.honorarios.alterar',
    'financeiro.honorarios_reajuste':      'financeiro.honorarios.alterar',
    'financeiro.honorarios_reajuste_aplicar': 'financeiro.honorarios.alterar',
    'financeiro.honorarios_reajuste_desfazer': 'financeiro.honorarios.desfazer',
    'financeiro.honorarios_aprovar':       'financeiro.honorarios.aprovar',
    'financeiro.honorarios_reprovar':      'financeiro.honorarios.aprovar',
    'financeiro.honorarios_convite_novo':  'financeiro.honorarios.alterar',
    # quem cadastra honorários da empresa que cadastrou (Melke, Gabriel, Guilherme, Alessandra): nasce PENDENTE
    'financeiro.honorarios_cadastrar':     'financeiro.honorarios_cadastrar',
    'financeiro.honorarios_cadastrar_salvar': 'financeiro.honorarios_cadastrar',
    # o link de convite exige só login: é o token que diz a empresa e, se houver, a pessoa
    'financeiro.honorarios_convite':       'LOGIN',
    'financeiro.honorarios_convite_salvar': 'LOGIN',
    'financeiro.honorarios_sugestoes':     'LOGIN',   # a rota confere permissão ou convite da empresa
    # Lançamentos de serviço e comissões (06/11/2026): quem lança vê só o seu; aprovar/pagar/acordos = admin na rota
    'financeiro.lancamentos':              'financeiro.lancamentos',
    'financeiro.lancamento_novo':          'financeiro.lancamentos.incluir',
    'financeiro.lancamento_aprovar':       'financeiro.lancamentos',
    'financeiro.lancamento_reprovar':      'financeiro.lancamentos',
    'financeiro.lancamento_cancelar':      'financeiro.lancamentos.excluir',
    'financeiro.comissoes_fechar':         'financeiro.lancamentos',
    'financeiro.fechamento_pagar':         'financeiro.lancamentos',
    'financeiro.acordo_novo':              'financeiro.lancamentos',
    'financeiro.acordo_encerrar':          'financeiro.lancamentos',
    # Lote mensal (06/11/2026): só o Anderson emite o mensal — permissão sem perfil
    'financeiro.lote':                     'financeiro.lote',
    # Contrato de prestação: SÓ ADMIN — "pro ADMIN aparece todos, para os demais não aparece nada" (Anderson, 06/11/2026)
    'financeiro.contratos':                        'ADMIN',
    'financeiro.contrato_novo':                    'ADMIN',
    'financeiro.contrato_editar':                  'ADMIN',
    'financeiro.contrato_salvar':                  'ADMIN',
    'financeiro.contrato_ver':                     'ADMIN',
    'financeiro.contrato_pdf':                     'ADMIN',
    'financeiro.contrato_enviar':                  'ADMIN',
    'financeiro.contrato_cancelar':                'ADMIN',
    'financeiro.lote_emitir':              'financeiro.lote.incluir',
    'financeiro.lote_status':              'financeiro.lote',
    'financeiro.lote_reabrir':             'financeiro.lote.reabrir',

    # --- Relatórios ---------------------------------------------------------
    'relatorios.processos_report':     'relatorios.processos',
    'relatorios.obrigacoes_report':    'relatorios.index',

    # --- Escrita Fiscal: Entradas (conf_compras) ----------------------------
    'escrita_fiscal.api_notas':                'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_opcoes_filtros':       'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_itens':                'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_sugestao_produto':     'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_vincular_produto':     'escrita_fiscal.conf_compras.alterar',
    'escrita_fiscal.api_vincular_todos':       'escrita_fiscal.conf_compras.alterar',
    'escrita_fiscal.api_por_emissor':          'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_por_produto':          'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_resumo_produtos':      'escrita_fiscal.conf_compras',
    'escrita_fiscal.importar_xml':             'escrita_fiscal.conf_compras.incluir',
    'escrita_fiscal.sync_dropbox':             'escrita_fiscal.conf_compras.incluir',
    'escrita_fiscal.api_importar_dropbox':        'escrita_fiscal.conf_compras.incluir',
    'escrita_fiscal.api_importar_dropbox_start':  'escrita_fiscal.conf_compras.incluir',
    'escrita_fiscal.api_importar_dropbox_status': 'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_importar_dropbox_stop':   'escrita_fiscal.conf_compras.incluir',
    'escrita_fiscal.api_log_importacoes':      'escrita_fiscal.conf_compras',
    'escrita_fiscal.relatorio_entradas':       'escrita_fiscal.conf_compras',
    'escrita_fiscal.lote_xml_compras':         'escrita_fiscal.conf_compras.lote',
    'escrita_fiscal.lote_pdf_compras':         'escrita_fiscal.conf_compras.lote',
    'escrita_fiscal.api_historico':            'escrita_fiscal.index',
    'escrita_fiscal.nota_xml':                 _FISCAL_NOTA,
    'escrita_fiscal.nota_pdf':                 _FISCAL_NOTA,
    'escrita_fiscal.nota_capturar':            _FISCAL_NOTA,
    # a rota confere a exata pelo tipo da nota (Entradas ou Saídas)
    'escrita_fiscal.excluir_nfe':              ('escrita_fiscal.conf_compras.excluir', 'escrita_fiscal.conf_saidas.excluir'),
    'escrita_fiscal.excluir_lote':             'escrita_fiscal.conf_compras.excluir',
    'escrita_fiscal.api_horario_agendado':           'ADMIN',
    'escrita_fiscal.api_configurar_horario_agendado': 'ADMIN',
    'escrita_fiscal.api_executar_importacao_agendada': 'ADMIN',
    'escrita_fiscal.api_executar_agendada_status':   'ADMIN',

    # --- Escrita Fiscal: Saídas ---------------------------------------------
    'escrita_fiscal.api_notas_saidas':         'escrita_fiscal.conf_saidas',
    'escrita_fiscal.api_opcoes_filtros_saidas': 'escrita_fiscal.conf_saidas',
    'escrita_fiscal.api_por_destinatario':     'escrita_fiscal.conf_saidas',
    'escrita_fiscal.api_por_produto_saidas':   'escrita_fiscal.conf_saidas',
    'escrita_fiscal.relatorio_saidas':         'escrita_fiscal.conf_saidas',
    'escrita_fiscal.lote_xml_saidas':          'escrita_fiscal.conf_saidas.lote',
    'escrita_fiscal.lote_pdf_saidas':          'escrita_fiscal.conf_saidas.lote',
    'escrita_fiscal.excluir_lote_saidas':      'escrita_fiscal.conf_saidas.excluir',

    # --- Escrita Fiscal: CT-e -----------------------------------------------
    'escrita_fiscal.api_ctes':                 'escrita_fiscal.conf_cte',
    'escrita_fiscal.api_cte_nfes':             'escrita_fiscal.conf_cte',
    'escrita_fiscal.api_opcoes_filtros_cte':   'escrita_fiscal.conf_cte',
    'escrita_fiscal.cte_xml':                  'escrita_fiscal.conf_cte',
    'escrita_fiscal.cte_pdf':                  'escrita_fiscal.conf_cte',
    'escrita_fiscal.importar_cte_xml':         'escrita_fiscal.conf_cte.incluir',
    'escrita_fiscal.lote_xml_cte':             'escrita_fiscal.conf_cte.lote',
    'escrita_fiscal.lote_pdf_cte':             'escrita_fiscal.conf_cte.lote',
    'escrita_fiscal.relatorio_cte':            'escrita_fiscal.conf_cte',
    'escrita_fiscal.excluir_cte':              'escrita_fiscal.conf_cte.excluir',
    'escrita_fiscal.excluir_lote_cte':         'escrita_fiscal.conf_cte.excluir',

    # --- Escrita Fiscal: NFS-e ----------------------------------------------
    'escrita_fiscal.api_nfse':                 'escrita_fiscal.conf_nfse',
    'escrita_fiscal.api_nfse_detalhe':         'escrita_fiscal.conf_nfse',
    'escrita_fiscal.api_opcoes_filtros_nfse':  'escrita_fiscal.conf_nfse',
    'escrita_fiscal.nfse_xml':                 'escrita_fiscal.conf_nfse',
    'escrita_fiscal.nfse_pdf':                 'escrita_fiscal.conf_nfse',
    'escrita_fiscal.lote_xml_nfse':            'escrita_fiscal.conf_nfse.lote',
    'escrita_fiscal.lote_pdf_nfse':            'escrita_fiscal.conf_nfse.lote',
    'escrita_fiscal.relatorio_nfse':           'escrita_fiscal.conf_nfse',

    # --- Escrita Fiscal: produtos e memorizações ----------------------------
    'escrita_fiscal.api_produtos_catalogo':    'escrita_fiscal.produtos_catalogo',
    'escrita_fiscal.produtos_catalogo_salvar': ('escrita_fiscal.produtos_catalogo.incluir', 'escrita_fiscal.produtos_catalogo.alterar'),
    'escrita_fiscal.produtos_catalogo_excluir': 'escrita_fiscal.produtos_catalogo.excluir',
    'escrita_fiscal.categoria_criar':          'escrita_fiscal.produtos_catalogo.incluir',
    'escrita_fiscal.categoria_excluir':        'escrita_fiscal.produtos_catalogo.excluir',
    'escrita_fiscal.subcategoria_criar':       'escrita_fiscal.produtos_catalogo.incluir',
    'escrita_fiscal.subcategoria_excluir':     'escrita_fiscal.produtos_catalogo.excluir',
    'escrita_fiscal.memorizacoes_empresas':    'escrita_fiscal.memorizacoes',
    'escrita_fiscal.memorizacoes_editar':      'escrita_fiscal.memorizacoes.alterar',
    'escrita_fiscal.memorizacoes_excluir':     'escrita_fiscal.memorizacoes.excluir',

    # --- Ações por tela: Ver / Incluir / Alterar / Excluir (09/11/2026) ---------
    # Antes estas rotas valiam pela marca do decorador (a permissão de VER a tela)
    # ou eram só do admin. Agora cada uma pede a ação que faz; utils/permissions.py
    # diz o que cada código é. Excluir e irreversível nascem sem perfil.
    # configuracoes
    'configuracoes.caixa_entrada_renomear': 'configuracoes.caixa_entrada.alterar',
    # contabilidades
    'contabilidades.index': 'cadastros.tabelas',
    'contabilidades.salvar': ('cadastros.tabelas.incluir', 'cadastros.tabelas.alterar'),
    'contabilidades.situacao': 'cadastros.tabelas.alterar',
    # escrita_fiscal
    'escrita_fiscal.emissao_nfse_liberar': 'escrita_fiscal.nfse_matriz.liberar',
    'escrita_fiscal.emissao_nfse_salvar': 'escrita_fiscal.nfse_matriz.alterar',
    'escrita_fiscal.emissao_nfse_serie': 'escrita_fiscal.nfse_matriz.alterar',
    'escrita_fiscal.memorizacoes_clone_aplicar': 'escrita_fiscal.memorizacoes.incluir',
    'escrita_fiscal.memorizacoes_clone_preview': 'escrita_fiscal.memorizacoes.incluir',
    'escrita_fiscal.memorizacoes_conjunto_desvincular_aplicar': 'escrita_fiscal.memorizacoes.excluir',
    'escrita_fiscal.memorizacoes_conjunto_desvincular_preview': 'escrita_fiscal.memorizacoes.excluir',
    'escrita_fiscal.memorizacoes_conjunto_incluir_aplicar': 'escrita_fiscal.memorizacoes.incluir',
    'escrita_fiscal.memorizacoes_conjunto_incluir_preview': 'escrita_fiscal.memorizacoes.incluir',
    'escrita_fiscal.memorizacoes_conjunto_nomear': 'escrita_fiscal.memorizacoes.alterar',
    'escrita_fiscal.nfse_emitir_post': 'escrita_fiscal.nfse_emitir.incluir',
    'escrita_fiscal.nfse_nota_cancelar': 'escrita_fiscal.nfse_emitir.excluir',
    'escrita_fiscal.nfse_nova': 'escrita_fiscal.nfse_emitir.incluir',
    'escrita_fiscal.nfse_tomadores_salvar': 'escrita_fiscal.nfse_emitir.alterar',
    'escrita_fiscal.q_robo_gerar': 'escrita_fiscal.q_robo.gerar',
    'escrita_fiscal.q_robo_maquina': 'escrita_fiscal.q_robo.alterar',
    'escrita_fiscal.q_robo_resolver': ('escrita_fiscal.q_robo.revelar', 'escrita_fiscal.q_robo.gerar'),
    'escrita_fiscal.q_robo_revelar': 'escrita_fiscal.q_robo.revelar',
    'escrita_fiscal.status_sefaz': 'escrita_fiscal.status_sefaz',
    'escrita_fiscal.status_sefaz_captura': 'escrita_fiscal.status_sefaz.captura',
    'escrita_fiscal.status_sefaz_empresa_log': 'escrita_fiscal.status_sefaz',
    # financeiro
    'financeiro.boleto_cancelar': 'financeiro.cobranca.excluir',
    'financeiro.boleto_enviar_email': 'financeiro.cobranca.alterar',
    'financeiro.boleto_sincronizar': 'financeiro.cobranca.alterar',
    'financeiro.categoria_alternar': 'financeiro.categorias.alterar',
    'financeiro.categoria_editar': 'financeiro.categorias.alterar',
    'financeiro.categoria_nova': 'financeiro.categorias.incluir',
    'financeiro.categoria_nova_inline': 'financeiro.categorias.incluir',
    'financeiro.categoria_renomear': 'financeiro.categorias.alterar',
    'financeiro.centro_alternar': 'financeiro.categorias.alterar',
    'financeiro.centro_editar': 'financeiro.categorias.alterar',
    'financeiro.centro_novo': 'financeiro.categorias.incluir',
    'financeiro.cobranca_alternar': 'financeiro.cobranca.contas',
    'financeiro.cobranca_salvar': 'financeiro.cobranca.contas',
    'financeiro.cobranca_testar': 'financeiro.cobranca.contas',
    'financeiro.cobranca_testar_email': 'financeiro.cobranca.contas',
    'financeiro.conta_alternar': 'financeiro.contas.alterar',
    'financeiro.conta_nova': 'financeiro.contas.incluir',
    'financeiro.contas': ('financeiro.contas', 'financeiro.extrato', 'financeiro.cobranca'),
    'financeiro.empresa_atualizar': 'financeiro.empresas.alterar',
    'financeiro.empresa_marcar': 'financeiro.empresas.alterar',
    'financeiro.extrato_apagar_periodo': 'financeiro.extrato.excluir',
    'financeiro.extrato_classificar': 'financeiro.extrato.alterar',
    'financeiro.extrato_confirmar': 'financeiro.extrato.alterar',
    'financeiro.extrato_confirmar_todos': 'financeiro.extrato.alterar',
    'financeiro.extrato_importar': 'financeiro.extrato.incluir',
    'financeiro.extrato_lote': 'financeiro.extrato.alterar',
    'financeiro.extrato_lote_titulos': 'financeiro.extrato.alterar',
    'financeiro.extrato_memorizacao_alternar': 'financeiro.extrato.alterar',
    'financeiro.extrato_regra_editar': 'financeiro.extrato.alterar',
    'financeiro.extrato_regra_retroagir': 'financeiro.extrato.alterar',
    'financeiro.extrato_transferencias_aprovar': 'financeiro.extrato.transferencias',
    'financeiro.extrato_transferencias_aprovar_lado': 'financeiro.extrato.transferencias',
    'financeiro.extrato_transferencias_liberar': 'financeiro.extrato.transferencias',
    'financeiro.extrato_transferencias_negar': 'financeiro.extrato.transferencias',
    'financeiro.fluxo_saldo': 'financeiro.fluxo.alterar',
    'financeiro.nota_boleto': 'financeiro.cobranca.incluir',
    'financeiro.nota_cancelar': 'financeiro.notas.excluir',
    'financeiro.nota_emitir': 'financeiro.notas.incluir',
    'financeiro.nota_nova': 'financeiro.notas.incluir',
    'financeiro.notas_config': 'financeiro.notas.configurar',
    'financeiro.notas_serie': 'financeiro.notas.configurar',
    'financeiro.notas_servico': 'financeiro.notas.configurar',
    'financeiro.notas_servico_json': 'financeiro.notas.incluir',
    'financeiro.pendencia_resolver': 'financeiro.extrato.alterar',
    'financeiro.programacao_atualizar': 'financeiro.programacoes.alterar',
    'financeiro.programacao_nova': 'financeiro.programacoes.incluir',
    'financeiro.programacoes_gerar': 'financeiro.programacoes.gerar',
    'financeiro.titulo_baixar': 'financeiro.titulos.alterar',
    'financeiro.titulo_cancelar': 'financeiro.titulos.excluir',
    'financeiro.titulo_excluir': 'financeiro.titulos.excluir',
    'financeiro.titulo_novo': 'financeiro.titulos.incluir',
    # grupos
    'grupos.desativar': 'grupos.excluir',
    'grupos.incluir_empresa': 'grupos.alterar',
    'grupos.mover_empresa': 'grupos.alterar',
    'grupos.reativar': 'grupos.alterar',
    'grupos.renomear': 'grupos.alterar',
}


def regra_da_rota(endpoint, view):
    """O que a rota exige: do MAPA, senão da marca do decorador, senão None.

    None = rota não classificada → o portão trata como SÓ ADMIN.
    """
    if endpoint in MAPA:
        return MAPA[endpoint]
    marca = getattr(view, '_acesso', None)
    if marca:
        tipo, codigo = marca
        if tipo == 'perm':
            return codigo
        if tipo == 'admin':
            return 'ADMIN'
    return None


def pode_rota(endpoint):
    """Para os TEMPLATES: o portão deixaria este usuário abrir a rota?

    Mesma regra do portão — esconder o botão e barrar a URL não podem
    discordar (foi a discordância que abriu as 148 rotas)."""
    from flask import current_app
    if not current_user.is_authenticated:
        return False
    if current_user.is_admin():
        return True
    if endpoint in ENDPOINTS_PUBLICOS or endpoint.split('.', 1)[0] in BLUEPRINTS_PUBLICOS:
        return True
    return _atende(regra_da_rota(endpoint, current_app.view_functions.get(endpoint)))


def _atende(regra):
    if regra == 'LOGIN':
        return True
    if regra == 'ADMIN' or regra is None:
        return current_user.is_admin()
    if isinstance(regra, tuple):
        return any(current_user.has_permission(c) for c in regra)
    return current_user.has_permission(regra)


def _quer_json():
    return (request.is_json or '/api/' in request.path
            or request.accept_mimetypes.best == 'application/json'
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest')


def _negar(msg, status=403):
    if _quer_json():
        return jsonify({'error': msg, 'acesso_negado': True}), status
    flash(msg, 'danger')
    destino = url_for('dashboard.index')
    # Negar o próprio dashboard (não acontece: é 'LOGIN') daria laço.
    if request.endpoint == 'dashboard.index':
        abort(status)
    return redirect(destino)


# ---------------------------------------------------------------------------
# Regra 2 — empresa reservada
# ---------------------------------------------------------------------------
class AcessoIndisponivel(Exception):
    """O banco não respondeu quem pode ver o quê. Falha FECHADA."""


# Telas de CADASTRO onde a empresa reservada continua visível (09/11/2026). Anderson: "o
# Acesso ao Cadastro todos podem ter, só o Fiscal, Contábil, Financeiro e outros que não
# terão". Aqui a reserva não esconde a empresa; no resto do sistema (Fiscal, Contábil,
# Financeiro, DP, notas, downloads, Q-Robô, histórico, certificado) ela segue escondida.
# Rota nova de cadastro NÃO entra sozinha: fica fechada até ser posta aqui (nega por padrão).
RESERVA_VISIVEL_NO_CADASTRO = frozenset({
    'clientes.index', 'clientes.relatorio', 'clientes.detalhes', 'clientes.salvar_campos',
    'clientes.editar', 'clientes.avulsos', 'clientes.converter_avulso', 'clientes.inativar',
    'clientes.novo_endereco', 'clientes.excluir_endereco', 'clientes.salvar_responsaveis',
    'clientes.responsaveis_convite_novo', 'clientes.novo_contato', 'clientes.editar_contato',
    'clientes.excluir_contato', 'clientes.novo_cadastro_adicional', 'clientes.excluir_cadastro_adicional',
    'clientes.socios_buscar_pf', 'clientes.novo_socio', 'clientes.saida_socio', 'clientes.excluir_socio',
    'clientes.vincular_contador', 'clientes.desvincular_contador', 'clientes.cadastros_buscar',
    'clientes.socios_da_raiz', 'clientes.socios_da_receita', 'clientes.a_cadastrar',
    'clientes.a_cadastrar_avulsos', 'clientes.socios_fila', 'clientes.socios_fila_vincular',
    'clientes.socios_fila_ignorar', 'clientes.adicionar_grupo', 'clientes.remover_grupo',
    'clientes.anp_importar_pdf', 'clientes.anp_salvar', 'clientes.anp_detalhe', 'clientes.anp_editar',
    'clientes.anp_excluir', 'clientes.anp_novo',
    'grupos.index', 'grupos.novo', 'grupos.detalhes', 'grupos.editar', 'grupos.adicionar_cliente',
    'grupos.remover_cliente', 'grupos.buscar_empresas', 'grupos.incluir_empresa', 'grupos.mover_empresa',
    'grupos.renomear',
})


def empresa_reservada_para_mim(cliente_id):
    """True se a empresa é reservada e o usuário NÃO é admin nem exceção — vale em
    QUALQUER tela, inclusive nas de cadastro. A ficha usa para esconder Financeiro,
    certificado, notas recebidas, acompanhamento e histórico."""
    try:
        return int(cliente_id) in _ocultas_reais()
    except (TypeError, ValueError):
        return False


def empresas_ocultas():
    """frozenset dos cliente_id que o usuário da requisição NÃO pode ver NESTA tela.
    Nas telas de cadastro (RESERVA_VISIVEL_NO_CADASTRO) a reserva não esconde nada."""
    if has_request_context() and request.endpoint in RESERVA_VISIVEL_NO_CADASTRO:
        return frozenset()
    return _ocultas_reais()


def _ocultas_reais():
    """frozenset dos cliente_id reservados que o usuário da requisição não vê.

    Vazio para admin, para quem não está logado (as rotas públicas não listam
    empresas e as outras o portão já mandou para o login) e fora de requisição
    (cron, scripts). Calculado uma vez por requisição.
    """
    if not has_request_context():
        return frozenset()
    if 'acesso_ocultas' in g:
        return g.acesso_ocultas
    if not current_user.is_authenticated or current_user.is_admin():
        g.acesso_ocultas = frozenset()
        return g.acesso_ocultas
    rows = execute_query(
        "SELECT c.id FROM clientes c "
        " WHERE c.acesso_reservado = 1 "
        "   AND NOT EXISTS (SELECT 1 FROM cliente_acesso_excecao e "
        "                    WHERE e.cliente_id = c.id AND e.usuario_id = %s)",
        (current_user.id,), fetch=True)
    if rows is None:
        erro = (get_last_db_error() or '').lower()
        # Antes da migração rodar não existe empresa reservada: nada a ocultar.
        if 'acesso_reservado' in erro or 'cliente_acesso_excecao' in erro:
            logger.warning('[acesso] migração add_acesso_reservado ainda não aplicada.')
            g.acesso_ocultas = frozenset()
            return g.acesso_ocultas
        raise AcessoIndisponivel(erro)
    g.acesso_ocultas = frozenset(int(r['id']) for r in rows)
    return g.acesso_ocultas


def pode_ver_empresa(cliente_id):
    try:
        cid = int(cliente_id)
    except (TypeError, ValueError):
        return True     # sem empresa definida: não há o que ocultar
    return cid not in empresas_ocultas()


def sql_sem_ocultas(coluna, params):
    """Cláusula ``coluna NOT IN (...)`` com os ids ocultos, ou None.

    EMPILHA os params no ponto da chamada (mesma convenção do _clausula_in do
    fiscal). Linha com a coluna NULL também sai — documento sem empresa
    definida não é mostrado a quem tem empresa oculta (o vincular_orfas dá a
    empresa a ele em até 10 min e ele reaparece).
    """
    ocultas = empresas_ocultas()
    if not ocultas:
        return None
    ids = sorted(ocultas)
    params.extend(ids)
    return f"{coluna} NOT IN ({', '.join(['%s'] * len(ids))})"


def filtrar_lista(linhas, chave='id'):
    """Tira das linhas (dicts) as empresas ocultas. Para listas já carregadas."""
    ocultas = empresas_ocultas()
    if not ocultas:
        return linhas
    return [r for r in linhas if r.get(chave) not in ocultas]


# De qual empresa é o id que está na URL. Por NOME do argumento quando o nome
# já diz (nfe_id é sempre nota), por ENDPOINT quando o nome é genérico ('id').
# None = o próprio valor já é o cliente_id.
_EMPRESA_POR_ARG = {
    'cliente_id': None,
    'nfe_id':  'SELECT cliente_id FROM nfe_importacoes WHERE id = %s',
    'cte_id':  'SELECT cliente_id FROM cte_documentos WHERE id = %s',
    'nfse_id': 'SELECT empresa_id AS cliente_id FROM nfse_capturadas WHERE id = %s',
}
_EMPRESA_POR_ENDPOINT = {
    'clientes.detalhes':             ('id', None),
    'clientes.editar':               ('id', None),
    'clientes.salvar_campos':        ('id', None),
    'clientes.salvar_responsaveis':  ('id', None),
    'clientes.responsaveis_convite_novo': ('id', None),
    'clientes.inativar':             ('id', None),
    'clientes.delete':               ('id', None),
    'clientes.historico':            ('id', None),
    'clientes.converter_avulso':     ('id', None),
    'clientes.certificado_buscar':   ('id', None),
    'clientes.certificado_vincular': ('id', None),
    'clientes.excluir_endereco':     ('id', 'SELECT cliente_id FROM enderecos_clientes WHERE id = %s'),
    'clientes.excluir_contato':      ('id', 'SELECT cliente_id FROM contatos_clientes WHERE id = %s'),
    'clientes.editar_contato':       ('id', 'SELECT cliente_id FROM contatos_clientes WHERE id = %s'),
    'clientes.excluir_socio':        ('id', 'SELECT cliente_id FROM socios_clientes WHERE id = %s'),
    'clientes.saida_socio':          ('id', 'SELECT cliente_id FROM socios_clientes WHERE id = %s'),
    'clientes.excluir_cadastro_adicional':
        ('id', 'SELECT cliente_id FROM cadastros_adicionais_clientes WHERE id = %s'),
    'documentos.download':           ('id', 'SELECT cliente_id FROM documentos WHERE id = %s'),
    'processos.view':                ('id', 'SELECT cliente_id FROM processos WHERE id = %s'),
    'dfe.dfe_consultar':             ('id', None),
    'dfe.dfe_capturar':              ('id', None),
    'dfe.dfe_seed_nsu':              ('id', None),
    'dfe.cte_consultar':             ('id', None),
    'dfe.cte_capturar':              ('id', None),
    'dfe.cte_seed_nsu':              ('id', None),
    'dfe.cte_buscar_nsu':            ('id', None),
    'escrita_fiscal.memorizacoes_empresas':
        ('vid', 'SELECT cliente_id FROM nfe_produto_vinculo WHERE id = %s'),
    'escrita_fiscal.memorizacoes_editar':
        ('vid', 'SELECT cliente_id FROM nfe_produto_vinculo WHERE id = %s'),
    'escrita_fiscal.memorizacoes_excluir':
        ('vid', 'SELECT cliente_id FROM nfe_produto_vinculo WHERE id = %s'),
}


def empresas_da_url(endpoint, view_args):
    """Lista de cliente_id que a URL toca (pode ser vazia)."""
    achados = []
    pares = []
    for nome, valor in (view_args or {}).items():
        if nome in _EMPRESA_POR_ARG:
            pares.append((_EMPRESA_POR_ARG[nome], valor))
    if endpoint in _EMPRESA_POR_ENDPOINT:
        nome, sql = _EMPRESA_POR_ENDPOINT[endpoint]
        if nome in (view_args or {}):
            pares.append((sql, view_args[nome]))
    for sql, valor in pares:
        if sql is None:
            achados.append(valor)
            continue
        r = execute_query(sql, (valor,), fetch=True, fetch_one=True)
        if r and r.get('cliente_id') is not None:
            achados.append(r['cliente_id'])
    return achados


def empresas_do_pedido():
    """``cliente_id`` vindo na query string, no formulário ou no corpo JSON.

    Rede de segurança para as rotas que recebem a empresa FORA da URL
    (``?cliente_id=`` dos filtros do Fiscal, o formulário do grupo...). O
    Financeiro fica de fora: lá ``cliente_id`` não existe e as empresas são as
    do escritório.
    """
    valores = list(request.args.getlist('cliente_id'))
    if request.method != 'GET':
        valores += request.form.getlist('cliente_id')
        valores += request.form.getlist('cliente_ids')
        corpo = request.get_json(silent=True) if request.is_json else None
        if isinstance(corpo, dict):
            if corpo.get('cliente_id') is not None:
                valores.append(corpo['cliente_id'])
            if isinstance(corpo.get('cliente_ids'), list):
                valores += corpo['cliente_ids']
    return [v for v in valores if str(v).strip().isdigit()]


# ---------------------------------------------------------------------------
# O portão
# ---------------------------------------------------------------------------
def portao():
    """before_request: None segue; qualquer outra coisa é a resposta."""
    from flask import current_app
    endpoint = request.endpoint
    if endpoint is None:                       # 404/405: o Flask responde
        return None
    if endpoint in ENDPOINTS_PUBLICOS or endpoint.split('.', 1)[0] in BLUEPRINTS_PUBLICOS:
        return None
    if not current_user.is_authenticated:
        if _quer_json():
            return jsonify({'error': 'Sessão expirada. Faça login de novo.'}), 401
        flash('Por favor, faça login para acessar esta página.', 'warning')
        return redirect(url_for('auth.login', next=request.full_path))
    if current_user.is_admin():
        return None

    view = current_app.view_functions.get(endpoint)
    regra = regra_da_rota(endpoint, view)
    if regra is None:
        logger.warning('[acesso] rota SEM classificação negada a não-admin: %s', endpoint)
    if not _atende(regra):
        logger.info('[acesso] negado perfil: user=%s endpoint=%s regra=%s',
                    current_user.id, endpoint, regra)
        return _negar('Você não tem permissão para acessar esta página.')

    try:
        tocadas = empresas_da_url(endpoint, request.view_args)
        if not endpoint.startswith('financeiro.'):
            tocadas += empresas_do_pedido()
        for cid in tocadas:
            if not pode_ver_empresa(cid):
                logger.info('[acesso] negado empresa reservada: user=%s endpoint=%s cliente=%s',
                            current_user.id, endpoint, cid)
                # 404, não 403: não confirma que a empresa existe.
                return _negar('Registro não encontrado.', status=404)
    except AcessoIndisponivel:
        logger.exception('[acesso] não consegui conferir empresa reservada')
        return _negar('Não consegui conferir suas permissões agora. Tente de novo.', status=503)
    return None


# ---------------------------------------------------------------------------
# Reserva da empresa — leitura e gravação (aba "Acesso" da ficha, só admin)
# ---------------------------------------------------------------------------
def dados_reserva(cliente_id):
    """Estado da reserva para a aba Acesso: marcada? quem é exceção? quem pode
    virar exceção (funcionário ativo que não é admin — admin já vê tudo)."""
    cli = execute_query(
        "SELECT acesso_reservado FROM clientes WHERE id = %s",
        (cliente_id,), fetch=True, fetch_one=True)
    if cli is None:
        return None                        # migração ausente ou cliente sumiu
    excecoes = {r['usuario_id'] for r in (execute_query(
        "SELECT usuario_id FROM cliente_acesso_excecao WHERE cliente_id = %s",
        (cliente_id,), fetch=True) or [])}
    usuarios = execute_query(
        "SELECT id, nome, login FROM usuarios "
        " WHERE situacao = 'ATIVO' AND tipo_usuario <> 'ADMIN' "
        "   AND COALESCE(classe_conta, 'FUNCIONARIO') <> 'CLIENTE' "
        " ORDER BY nome", fetch=True) or []
    return {'reservado': bool(cli.get('acesso_reservado')),
            'excecoes': excecoes, 'usuarios': usuarios}


def salvar_reserva(cliente_id, reservado, usuario_ids):
    """Grava a marca e SUBSTITUI a lista de exceções. Devolve (antes, depois)
    para a auditoria. Exceção só vale enquanto a empresa está reservada, mas é
    guardada mesmo desmarcada: remarcar não obriga a escolher todo mundo de novo.
    """
    antes = dados_reserva(cliente_id) or {'reservado': False, 'excecoes': set()}
    validos = {u['id'] for u in antes.get('usuarios', [])}
    if reservado:
        novos = {int(u) for u in usuario_ids if str(u).isdigit() and int(u) in validos}
    else:
        # Desmarcada, a tela desabilita a lista e o navegador não a envia:
        # mantém as exceções como estão para a próxima vez que reservar.
        novos = set(antes['excecoes'])

    execute_query("UPDATE clientes SET acesso_reservado = %s WHERE id = %s",
                  (1 if reservado else 0, cliente_id), fetch=False)
    sai = antes['excecoes'] - novos
    entra = novos - antes['excecoes']
    for uid in sai:
        execute_query("DELETE FROM cliente_acesso_excecao WHERE cliente_id = %s AND usuario_id = %s",
                      (cliente_id, uid), fetch=False)
    autor = getattr(current_user, 'id', None) if has_request_context() else None
    for uid in entra:
        execute_query("INSERT IGNORE INTO cliente_acesso_excecao (cliente_id, usuario_id, criado_por) "
                      "VALUES (%s, %s, %s)", (cliente_id, uid, autor), fetch=False)
    return ({'reservado': antes['reservado'], 'excecoes': sorted(antes['excecoes'])},
            {'reservado': bool(reservado), 'excecoes': sorted(novos)})


# ---------------------------------------------------------------------------
# Quem vê esta empresa, por departamento (09/11/2026)
# ---------------------------------------------------------------------------
# Anderson (09/11/2026): "queria que me mostrasse quem tem acesso... Cadastro, Fiscal,
# Financeiro... Só para eu ter a garantia que não estão tendo acesso ao fiscal de uma ou
# outra empresa". A resposta junta as DUAS regras do portão: o perfil (o que a pessoa abre)
# e a reserva (se esta empresa aparece para ela). Mesma fonte de verdade do portão:
# perfil_permissoes + catálogo, reserva + exceção. Nada é inventado aqui.
DEPARTAMENTOS = [
    ('Cadastros', 'Cadastro', 'fa-address-card'),
    ('Escrita Fiscal', 'Fiscal', 'fa-file-invoice'),
    ('Contábil', 'Contábil', 'fa-book'),
    ('Financeiro', 'Financeiro', 'fa-coins'),
    ('DP / Legalização', 'DP e Legalização', 'fa-briefcase'),
    ('Processos', 'Processos', 'fa-list-check'),
    ('Relatórios', 'Relatórios', 'fa-chart-column'),
    ('Q-Robô', 'Q-Robô', 'fa-robot'),
    ('Configurações', 'Configurações', 'fa-gear'),
]


def quem_ve_empresa(cliente_id):
    """Uma linha por usuário ativo da equipe (admins primeiro): vê a empresa? e, se vê,
    em quais departamentos e com quais permissões. None se a migração da reserva faltar."""
    from utils.permissions import PERMISSION_CATALOG
    reserva = dados_reserva(cliente_id)
    if reserva is None:
        return None
    usuarios = execute_query(
        "SELECT u.id, u.nome, u.login, u.tipo_usuario FROM usuarios u "
        " WHERE u.situacao = 'ATIVO' AND COALESCE(u.classe_conta, 'FUNCIONARIO') <> 'CLIENTE' "
        " ORDER BY (u.tipo_usuario = 'ADMIN') DESC, u.nome", fetch=True) or []
    perms, perfis = {}, {}
    for r in execute_query(
            "SELECT up.usuario_id, pa.nome AS perfil, pp.permissao_codigo "
            "  FROM usuario_perfis up JOIN perfis_acesso pa ON pa.id = up.perfil_id "
            "  LEFT JOIN perfil_permissoes pp ON pp.perfil_id = pa.id "
            " WHERE pa.situacao = 'ATIVO'", fetch=True) or []:
        perfis.setdefault(r['usuario_id'], set()).add(r['perfil'])
        if r.get('permissao_codigo'):
            perms.setdefault(r['usuario_id'], set()).add(r['permissao_codigo'])
    out = []
    for u in usuarios:
        admin = u['tipo_usuario'] == 'ADMIN'
        ve = admin or not reserva['reservado'] or u['id'] in reserva['excecoes']
        meus = perms.get(u['id'], set())
        deps = []
        for grupo, rotulo, ico in DEPARTAMENTOS:
            cat = PERMISSION_CATALOG.get(grupo, {})
            tem = sorted((cat[c] for c in cat if c in meus), key=str.lower)
            tem_dep = admin or bool(tem)
            # Reservada (09/11/2026): o Cadastro continua aberto; o resto só para exceção.
            deps.append({'rotulo': rotulo, 'ico': ico, 'tem': tem_dep,
                         'aberto': tem_dep and (ve or grupo == 'Cadastros'),
                         'permissoes': ['tudo (administrador)'] if admin else tem})
        so_cadastro = not ve and deps[0]['aberto']
        out.append({'id': u['id'], 'nome': u['nome'], 'login': u['login'], 'admin': admin, 'so_cadastro': so_cadastro,
                    've': ve, 'excecao': u['id'] in reserva['excecoes'],
                    'perfis': sorted(perfis.get(u['id'], set())), 'deps': deps,
                    'sem_perfil': not admin and not meus})
    return out
