# -*- coding: utf-8 -*-
"""Tradução de logs_sistema para a tela — rótulos PT, verbos, diff campo-a-campo.

Ponto ÚNICO usado pela aba Histórico da ficha do cliente (D3) e pela tela geral
de Auditoria (D4). Não duplicar tradução: quem precisar de rótulo/verbo/empresa
de um log vem por aqui.
"""
import json

from utils.atividade import CAMPOS_SENSIVEIS

# Antes desta data não há registro: a auditoria (D2) começou aqui.
AUDITORIA_INICIO = '11/08/2026'

# Rótulos em PORTUGUÊS — os mesmos do formulário de cadastro. NUNCA coluna crua.
_HIST_LABELS = {
    # clientes
    'numero_cliente': 'Número do cliente', 'tipo_pessoa': 'Tipo de pessoa',
    'nome_razao_social': 'Razão social', 'nome_fantasia': 'Nome fantasia',
    'cpf_cnpj': 'CPF/CNPJ', 'inscricao_estadual': 'Inscrição estadual',
    'inscricao_municipal': 'Inscrição municipal', 'email': 'E-mail',
    'telefone': 'Telefone', 'celular': 'Celular', 'regime_tributario': 'Regime tributário',
    'porte_empresa': 'Porte', 'cnae_fiscal': 'CNAE', 'cnae_fiscal_descricao': 'CNAE (descrição)',
    'situacao': 'Situação', 'observacoes': 'Observações', 'aberta_pela_casa': 'Aberta pela casa',
    'data_inicio_atividade': 'Início de atividade', 'data_inicio_contrato': 'Início do contrato',
    # endereço
    'tipo': 'Tipo', 'cep': 'CEP', 'logradouro': 'Logradouro', 'numero': 'Número',
    'complemento': 'Complemento', 'bairro': 'Bairro', 'cidade': 'Cidade', 'estado': 'Estado',
    # sócio
    'nome': 'Nome', 'cpf': 'CPF', 'percentual_participacao': 'Participação (%)',
    'responsavel': 'Responsável',
    # certificado
    'cnpj': 'CNPJ', 'tipo_doc': 'Tipo de documento', 'validade': 'Validade',
    'procuracao': 'Procuração', 'senha_cifrada': 'Senha', 'dropbox_path': 'Arquivo',
    # contrato
    'numero_contrato': 'Número do contrato', 'tipo_servico': 'Tipo de serviço',
    'valor_mensal': 'Valor mensal', 'data_inicio': 'Data de início', 'data_fim': 'Data de fim',
    # busca/consulta
    'termo': 'Termo', 'data_ini': 'De', 'data_fim': 'Até', 'formato': 'Formato',
    'relatorio': 'Relatório', 'escopo': 'Escopo',
}

# Chaves de CONTEXTO/meta — não são "campos alterados", ficam fora do campo-a-campo.
_HIST_META = {'cliente_id', 'cliente_numero', 'cliente_nome', 'grupo_id', 'grupo_nome',
              'criado_por', 'criado_em', 'alterado_por', 'alterado_em', 'item_id', 'nfe_id',
              'job_id', 'versao', 'produto_id', 'emit_cnpj', 'codigo', 'chave', 'origem',
              'salvar_regra', 'departamento', 'ok', 'duplicados', 'erros', 'arquivos',
              'total', 'marcadas', 'filtros', 'acao', 'usuario_id', 'numero',
              # caminho do .pfx: ruído no card (e concordância feia em "alterada");
              # a Senha já representa a troca do certificado.
              'dropbox_path'}

_HIST_ENTIDADE = {
    'clientes': 'o cadastro', 'enderecos_clientes': 'um endereço',
    'socios_clientes': 'um sócio', 'dfe_certificados': 'o certificado',
    'contratos': 'um contrato',
}

_HIST_VERBO = {
    'escrita.criou_cliente': 'criou o cadastro',
    'escrita.alterou_cliente': 'alterou o cadastro',
    'escrita.vinculou_certificado': 'vinculou o certificado',
    'escrita.criou_endereco': 'adicionou um endereço',
    'escrita.excluiu_endereco': 'excluiu um endereço',
    'escrita.criou_socio': 'adicionou um sócio',
    'escrita.excluiu_socio': 'excluiu um sócio',
    'escrita.criou_contrato': 'adicionou um contrato',
    'escrita.criou_grupo': 'criou um grupo',
    'escrita.alterou_grupo': 'alterou um grupo',
    'escrita.vinculou_produto': 'vinculou um produto',
    'escrita.desvinculou_produto': 'desvinculou um produto',
    'escrita.importou_manual': 'importou XML',
    'escrita.executou_importacao': 'rodou a importação (Executar Agora)',
    'escrita.gerou_chave_robo': 'gerou a chave do Q-Robô',
    'escrita.aprovou_cadastro_colabore': 'aprovou uma candidatura',
    'escrita.recusou_cadastro_colabore': 'recusou uma candidatura',
    'escrita.renomeou_caixa_entrada': 'renomeou arquivo da caixa de entrada',
    'leitura.abriu_ficha_cliente': 'abriu a ficha',
    'leitura.buscou_entradas': 'consultou as entradas',
    'leitura.buscou_saidas': 'consultou as saídas',
    'leitura.buscou_ctes': 'consultou os CT-e',
    'leitura.exportou_arquivo': 'exportou um arquivo',
    'leitura.consultou_sefaz': 'consultou a SEFAZ',
    'leitura.abriu_status_sefaz': 'abriu o Status SEFAZ',
    'leitura.abriu_cadastro_colabore': 'abriu uma candidatura',
}

# Módulo -> rótulo PT (para a tela de auditoria).
MODULO_LABEL = {'fiscal': 'Fiscal', 'cadastros': 'Cadastros', 'contabil': 'Contábil',
                'dp': 'DP', 'colabore': 'Colabore', 'configuracoes': 'Configurações'}


def _hist_val(v):
    """Valor legível: nulo/vazio/'None'/'null' viram '(vazio)'."""
    if v is None:
        return '(vazio)'
    s = str(v).strip()
    if s == '' or s in ('None', 'null'):
        return '(vazio)'
    return s


def _hist_dt(dt):
    try:
        return dt.strftime('%d/%m/%Y %H:%M')
    except Exception:
        return str(dt or '')


def _hist_campo(k, antes, depois, modo=None):
    """Um campo do card: rótulo PT + tipo (sensivel/novo/removido/alterado)."""
    label = _HIST_LABELS.get(k) or k.replace('_', ' ').capitalize()
    # lista negra: valor é 'alterado' ou nome de coluna sensível -> só o nome.
    if k in CAMPOS_SENSIVEIS or str(depois) == 'alterado' or str(antes) == 'alterado':
        return {'label': label, 'tipo': 'sensivel'}
    if modo == 'novo':
        return {'label': label, 'tipo': 'novo', 'depois': _hist_val(depois)}
    if modo == 'removido':
        return {'label': label, 'tipo': 'removido', 'antes': _hist_val(antes)}
    return {'label': label, 'tipo': 'alterado', 'antes': _hist_val(antes), 'depois': _hist_val(depois)}


def _br(d):
    """'YYYY-MM-DD...' -> 'DD/MM/YYYY'. Deixa passar o que não for data ISO."""
    s = str(d or '')[:10]
    try:
        y, m, dd = s.split('-')
        return '%s/%s/%s' % (dd, m, y)
    except ValueError:
        return str(d)


def _json_dict(v):
    try:
        d = json.loads(v) if v else {}
    except Exception:
        d = {}
    return d if isinstance(d, dict) else {}


def _cliente_id_do_log(ant, nov, row):
    """cliente_id que a ação referencia — do JSON (topo ou filtros) ou do
    registro_id quando a ação é sobre a própria empresa. Int ou None."""
    for src in (nov, nov.get('filtros') if isinstance(nov.get('filtros'), dict) else {}, ant):
        if isinstance(src, dict) and src.get('cliente_id'):
            try:
                return int(src['cliente_id'])
            except (TypeError, ValueError):
                pass
    if row.get('tabela_afetada') in ('clientes', 'dfe_certificados') and row.get('registro_id'):
        try:
            return int(row['registro_id'])
        except (TypeError, ValueError):
            pass
    return None


def coletar_cliente_ids(rows):
    """Todos os cliente_id referenciados pela PÁGINA (registro_id + JSON). O
    chamador resolve #número/nome em UMA consulta, não uma por linha."""
    ids = set()
    for row in rows:
        cid = _cliente_id_do_log(_json_dict(row.get('dados_anteriores')),
                                 _json_dict(row.get('dados_novos')), row)
        if cid is not None:
            ids.add(cid)
    return ids


def _empresa_do_log(ant, nov, row, emp_map=None):
    """'#num nome' da empresa a que o log se refere. (a) número/nome já gravados
    no log; (b) senão, resolve o cliente_id pelo emp_map (lote da página)."""
    for src in (nov, nov.get('filtros') if isinstance(nov.get('filtros'), dict) else {}, ant):
        if not isinstance(src, dict):
            continue
        num = src.get('cliente_numero') or src.get('numero_cliente') or src.get('numero')
        nome = src.get('cliente_nome') or src.get('nome_razao_social') or src.get('nome')
        if num or nome:
            nome = str(nome or '').strip()
            return ('#%s %s' % (num, nome)).strip() if num else (nome or None)
    if emp_map:
        cid = _cliente_id_do_log(ant, nov, row)
        if cid is not None:
            return emp_map.get(cid)
    return None


# Rótulos curtos dos filtros de busca (linha discreta). Empresa/grupo/termo/datas
# saem daqui — têm tratamento próprio.
_FILTRO_CURTO = {
    'cfop': 'CFOP', 'emit_uf': 'UF', 'dest_uf': 'UF', 'uf_ini': 'UF orig', 'uf_fim': 'UF dest',
    'emit_cnpj': 'emit.', 'dest_cnpj': 'dest.', 'tomador_cnpj': 'tomador', 'origem': 'origem',
    'cancelado': 'cancelado', 'modelo': 'modelo', 'papel': 'papel', 'vinc_status': 'vínculo',
    'num_nota': 'nº', 'num_cte': 'nº CT-e', 'vmin': 'valor ≥', 'vmax': 'valor ≤',
}
_FILTRO_IGNORA = {'cliente_id', 'grupo_id', 'cliente_numero', 'cliente_nome', 'grupo_nome',
                  'termo', 'data_ini', 'data_fim'}


def _leitura_resumo(nov):
    """(busca, detalhe) de uma ação de LEITURA. busca = termo em destaque;
    detalhe = período (BR) + filtros preenchidos, compacto e discreto."""
    flt = nov.get('filtros') if isinstance(nov.get('filtros'), dict) else {}
    termo = nov.get('termo') or flt.get('termo')
    partes = []
    di = flt.get('data_ini') or nov.get('data_ini')
    df = flt.get('data_fim') or nov.get('data_fim')
    if di and df:
        partes.append('período %s a %s' % (_br(di), _br(df)))
    elif di:
        partes.append('desde %s' % _br(di))
    elif df:
        partes.append('até %s' % _br(df))
    if nov.get('formato'):
        partes.append(str(nov['formato']).upper())
    if nov.get('escopo') and str(nov['escopo']) not in ('cliente', 'grupo', 'todas'):
        partes.append(str(nov['escopo']))
    if nov.get('relatorio'):
        partes.append(str(nov['relatorio']))
    for k, v in flt.items():
        if k in _FILTRO_IGNORA or not v:
            continue
        partes.append('%s %s' % (_FILTRO_CURTO.get(k, k), v))
    return (termo or None, ' · '.join(partes) if partes else None)


def hist_preparar(row, nomes, emp_map=None):
    """Uma linha de logs_sistema -> dict pronto para o cartão da tela.

    Retorna data_hora, autor, verbo, acao, modulo, empresa e campos[] (o
    antes/depois já traduzido). `nomes` = {usuario_id: nome} para linhas antigas
    sem usuario_nome; `emp_map` = {registro_id: '#num nome'} para resolver a
    empresa quando a ação é sobre a própria empresa (opcional).
    """
    try:
        ant = json.loads(row['dados_anteriores']) if row.get('dados_anteriores') else {}
    except Exception:
        ant = {}
    try:
        nov = json.loads(row['dados_novos']) if row.get('dados_novos') else {}
    except Exception:
        nov = {}
    if not isinstance(ant, dict):
        ant = {}
    if not isinstance(nov, dict):
        nov = {}
    acao = row.get('acao') or ''
    autor = row.get('usuario_nome') or nomes.get(row.get('usuario_id')) or 'usuário removido'
    tab = row.get('tabela_afetada')
    verbo = _HIST_VERBO.get(acao)
    modulo = row.get('modulo')
    empresa = _empresa_do_log(ant, nov, row, emp_map)
    campos = []

    if acao.startswith('leitura.'):
        if not verbo:
            verbo = 'consultou'
        # A empresa é o destaque; o termo buscado (se houver) também. Período e
        # filtros viram uma linha discreta — não duas linhas com "De:/Até:".
        busca, detalhe = _leitura_resumo(nov)
        return {'data_hora': _hist_dt(row.get('data_hora')), 'autor': autor,
                'verbo': verbo, 'acao': acao, 'modulo': modulo, 'empresa': empresa,
                'busca': busca, 'detalhe': detalhe, 'campos': []}

    # escrita
    if not verbo:
        if acao.startswith('escrita.excluiu'):
            verbo = 'excluiu ' + _HIST_ENTIDADE.get(tab, 'um registro')
        elif acao.startswith('escrita.alterou'):
            verbo = 'alterou'
        else:
            verbo = 'adicionou ' + _HIST_ENTIDADE.get(tab, 'um registro')

    is_exclusao = acao.startswith('escrita.excluiu') or (ant and not nov)
    is_alteracao = acao.startswith('escrita.alterou') and ant and nov

    if is_alteracao:
        for k in sorted((set(nov) | set(ant)) - _HIST_META):
            campo = _hist_campo(k, ant.get(k), nov.get(k))
            # Ex.: null -> "None" normalizam ambos para "(vazio)": nada mudou de
            # fato para quem lê — não polui o card com "(vazio) → (vazio)".
            if campo['tipo'] == 'alterado' and campo.get('antes') == campo.get('depois'):
                continue
            campos.append(campo)
    elif is_exclusao:
        for k, v in ant.items():
            if k not in _HIST_META:
                campos.append(_hist_campo(k, v, None, modo='removido'))
    else:  # criação / vínculo
        for k, v in nov.items():
            if k not in _HIST_META:
                campos.append(_hist_campo(k, None, v, modo='novo'))

    return {'data_hora': _hist_dt(row.get('data_hora')), 'autor': autor,
            'verbo': verbo, 'acao': acao, 'modulo': modulo, 'empresa': empresa,
            'busca': None, 'detalhe': None, 'campos': campos}
