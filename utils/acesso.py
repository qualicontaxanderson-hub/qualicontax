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
    'clientes.socios_buscar_pf':       'clientes.index',
    'clientes.anp_detalhe':            'clientes.index',
    'clientes.cnpj_vigia_status':      'clientes.index',
    'clientes.editar':                 'clientes.edit',
    'clientes.inativar':               'clientes.edit',
    'clientes.adicionar_grupo':        'clientes.edit',
    'clientes.remover_grupo':          'clientes.edit',
    'clientes.novo_endereco':          'clientes.edit',
    'clientes.excluir_endereco':       'clientes.edit',
    'clientes.novo_contato':           'clientes.edit',
    'clientes.excluir_contato':        'clientes.edit',
    'clientes.novo_socio':             'clientes.edit',
    'clientes.excluir_socio':          'clientes.edit',
    'clientes.novo_cadastro_adicional':    'clientes.edit',
    'clientes.excluir_cadastro_adicional': 'clientes.edit',
    'clientes.vincular_contador':      'clientes.edit',
    'clientes.desvincular_contador':   'clientes.edit',
    'clientes.certificado_buscar':     'clientes.edit',
    'clientes.certificado_vincular':   'clientes.edit',
    'clientes.anp_novo':               'clientes.edit',
    'clientes.anp_editar':             'clientes.edit',
    'clientes.anp_salvar':             'clientes.edit',
    'clientes.anp_importar_pdf':       'clientes.edit',
    'clientes.anp_excluir':            'clientes.edit',
    'clientes.cnpj_vigia_armar':       'clientes.edit',
    'clientes.cnpj_vigia_acao':        'clientes.edit',
    'clientes.delete':                 'clientes.delete',
    'adicionais.index':                'clientes.index',
    'adicionais.sync_dropbox_anp':     'clientes.edit',
    'documentos.download':             'clientes.index',
    'documentos.upload':               'clientes.edit',

    # --- Cadastros: grupos, ramos, municípios -------------------------------
    'grupos.detalhes':                 'grupos.index',
    'grupos.novo':                     'grupos.index',
    'grupos.editar':                   'grupos.index',
    'grupos.deletar':                  'grupos.index',
    'grupos.adicionar_cliente':        'grupos.index',
    'grupos.remover_cliente':          'grupos.index',
    'ramos_atividade.index':           'clientes.index',
    'ramos_atividade.detalhes':        'clientes.index',
    'ramos_atividade.novo':            'clientes.edit',
    'ramos_atividade.editar':          'clientes.edit',
    'ramos_atividade.deletar':         'clientes.edit',
    'ramos_atividade.adicionar_cliente': 'clientes.edit',
    'ramos_atividade.remover_cliente': 'clientes.edit',
    'municipios.index':                'clientes.index',
    'municipios.novo':                 'clientes.edit',
    'municipios.editar':               'clientes.edit',
    'municipios.deletar':              'clientes.edit',
    'contratos.create':                'contratos.create_contrato',
    'processos.list_processos':        'processos.index',
    'processos.view':                  'processos.index',
    'processos.create':                'processos.index',

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
    'contabil.criar_plano_contas':     'contabil.plano_contas',
    'contabil.ver_plano_contas':       'contabil.plano_contas',
    'contabil.nova_conta_plano':       'contabil.plano_contas',
    'contabil.excluir_conta_plano':    'contabil.plano_contas',
    'contabil.excluir_plano_contas':   'contabil.plano_contas',
    'contabil.template_csv_plano_contas': 'contabil.plano_contas',
    'contabil.importar_plano_contas':  'contabil.plano_contas',
    'contabil.nova_conciliacao':       'contabil.conciliacoes',
    'contabil.ver_conciliacao':        'contabil.conciliacoes',
    'contabil.extrato_conciliacao':    'contabil.conciliacoes',
    'contabil.contas_correntes':       'contabil.conciliacoes',
    'contabil.memorizacoes':           'contabil.conciliacoes',
    'contabil.nova_memorizacao':       'contabil.conciliacoes',
    'contabil.editar_memorizacao':     'contabil.conciliacoes',
    'contabil.excluir_memorizacao':    'contabil.conciliacoes',
    'contabil.api_cliente_grupos':     'contabil.conciliacoes',
    'contabil.importar_ofx':           'contabil.importar_ofx',
    'contabil.importar_pdf':           'contabil.importar_pdf',
    'contabil.confirmar_importacao_pdf': 'contabil.importar_pdf',

    # --- Financeiro ---------------------------------------------------------
    'financeiro.recebimento_excluir':      'financeiro.recebimento',
    'financeiro.recebimento_excluir_lote': 'financeiro.recebimento',

    # --- Relatórios ---------------------------------------------------------
    'relatorios.processos_report':     'relatorios.processos',
    'relatorios.obrigacoes_report':    'relatorios.index',

    # --- Escrita Fiscal: Entradas (conf_compras) ----------------------------
    'escrita_fiscal.api_notas':                'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_opcoes_filtros':       'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_itens':                'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_sugestao_produto':     'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_vincular_produto':     'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_vincular_todos':       'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_por_emissor':          'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_por_produto':          'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_resumo_produtos':      'escrita_fiscal.conf_compras',
    'escrita_fiscal.importar_xml':             'escrita_fiscal.conf_compras',
    'escrita_fiscal.sync_dropbox':             'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_importar_dropbox':        'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_importar_dropbox_start':  'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_importar_dropbox_status': 'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_importar_dropbox_stop':   'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_log_importacoes':      'escrita_fiscal.conf_compras',
    'escrita_fiscal.relatorio_entradas':       'escrita_fiscal.conf_compras',
    'escrita_fiscal.lote_xml_compras':         'escrita_fiscal.conf_compras',
    'escrita_fiscal.lote_pdf_compras':         'escrita_fiscal.conf_compras',
    'escrita_fiscal.api_historico':            'escrita_fiscal.index',
    'escrita_fiscal.nota_xml':                 _FISCAL_NOTA,
    'escrita_fiscal.nota_pdf':                 _FISCAL_NOTA,
    'escrita_fiscal.nota_capturar':            _FISCAL_NOTA,
    'escrita_fiscal.excluir_nfe':              'ADMIN',
    'escrita_fiscal.excluir_lote':             'ADMIN',
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
    'escrita_fiscal.lote_xml_saidas':          'escrita_fiscal.conf_saidas',
    'escrita_fiscal.lote_pdf_saidas':          'escrita_fiscal.conf_saidas',
    'escrita_fiscal.excluir_lote_saidas':      'ADMIN',

    # --- Escrita Fiscal: CT-e -----------------------------------------------
    'escrita_fiscal.api_ctes':                 'escrita_fiscal.conf_cte',
    'escrita_fiscal.api_cte_nfes':             'escrita_fiscal.conf_cte',
    'escrita_fiscal.api_opcoes_filtros_cte':   'escrita_fiscal.conf_cte',
    'escrita_fiscal.cte_xml':                  'escrita_fiscal.conf_cte',
    'escrita_fiscal.cte_pdf':                  'escrita_fiscal.conf_cte',
    'escrita_fiscal.importar_cte_xml':         'escrita_fiscal.conf_cte',
    'escrita_fiscal.lote_xml_cte':             'escrita_fiscal.conf_cte',
    'escrita_fiscal.lote_pdf_cte':             'escrita_fiscal.conf_cte',
    'escrita_fiscal.relatorio_cte':            'escrita_fiscal.conf_cte',
    'escrita_fiscal.excluir_cte':              'ADMIN',
    'escrita_fiscal.excluir_lote_cte':         'ADMIN',

    # --- Escrita Fiscal: NFS-e ----------------------------------------------
    'escrita_fiscal.api_nfse':                 'escrita_fiscal.conf_nfse',
    'escrita_fiscal.api_nfse_detalhe':         'escrita_fiscal.conf_nfse',
    'escrita_fiscal.api_opcoes_filtros_nfse':  'escrita_fiscal.conf_nfse',
    'escrita_fiscal.nfse_xml':                 'escrita_fiscal.conf_nfse',
    'escrita_fiscal.nfse_pdf':                 'escrita_fiscal.conf_nfse',
    'escrita_fiscal.lote_xml_nfse':            'escrita_fiscal.conf_nfse',
    'escrita_fiscal.lote_pdf_nfse':            'escrita_fiscal.conf_nfse',
    'escrita_fiscal.relatorio_nfse':           'escrita_fiscal.conf_nfse',

    # --- Escrita Fiscal: produtos e memorizações ----------------------------
    'escrita_fiscal.api_produtos_catalogo':    'escrita_fiscal.produtos_catalogo',
    'escrita_fiscal.produtos_catalogo_salvar': 'escrita_fiscal.produtos_catalogo',
    'escrita_fiscal.produtos_catalogo_excluir': 'escrita_fiscal.produtos_catalogo',
    'escrita_fiscal.categoria_criar':          'escrita_fiscal.produtos_catalogo',
    'escrita_fiscal.categoria_excluir':        'escrita_fiscal.produtos_catalogo',
    'escrita_fiscal.subcategoria_criar':       'escrita_fiscal.produtos_catalogo',
    'escrita_fiscal.subcategoria_excluir':     'escrita_fiscal.produtos_catalogo',
    'escrita_fiscal.memorizacoes_empresas':    'escrita_fiscal.memorizacoes',
    'escrita_fiscal.memorizacoes_editar':      'escrita_fiscal.memorizacoes',
    'escrita_fiscal.memorizacoes_excluir':     'escrita_fiscal.memorizacoes',
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


def empresas_ocultas():
    """frozenset dos cliente_id que o usuário da requisição NÃO pode ver.

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
    'clientes.inativar':             ('id', None),
    'clientes.delete':               ('id', None),
    'clientes.historico':            ('id', None),
    'clientes.converter_avulso':     ('id', None),
    'clientes.certificado_buscar':   ('id', None),
    'clientes.certificado_vincular': ('id', None),
    'clientes.excluir_endereco':     ('id', 'SELECT cliente_id FROM enderecos_clientes WHERE id = %s'),
    'clientes.excluir_contato':      ('id', 'SELECT cliente_id FROM contatos_clientes WHERE id = %s'),
    'clientes.excluir_socio':        ('id', 'SELECT cliente_id FROM socios_clientes WHERE id = %s'),
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
