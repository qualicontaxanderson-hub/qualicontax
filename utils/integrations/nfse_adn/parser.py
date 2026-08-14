# -*- coding: utf-8 -*-
"""Tradução do XML da NFS-e para o nosso schema. ÚNICO lugar que conhece campo.

Nenhum outro módulo pode saber nome de elemento, XPath ou código do leiaute. Se
o leiaute mudar, muda-se este arquivo e mais nada.

REGRA ABSOLUTA: TODO CAMINHO É ABSOLUTO
---------------------------------------
Nada de ``iter()``, nada de busca por nome. O leiaute tem nomes REPETIDOS em
níveis diferentes, e ler o de baixo achando que é o de cima não quebra nada —
só fica errado em silêncio, que é o pior desfecho possível num dado fiscal:

    valores      NFSe/infNFSe/valores/          (calculado pela autoridade)
                 NFSe/infNFSe/DPS/infDPS/valores/  (declarado pelo contribuinte)

    IBSCBS       NFSe/infNFSe/IBSCBS/           (calculado)
                 NFSe/infNFSe/DPS/infDPS/IBSCBS/   (declarado)

    trib         .../DPS/infDPS/valores/trib/       (ISS, PIS/COFINS)
                 .../DPS/infDPS/IBSCBS/valores/trib/  (IBS/CBS)

    CST          .../valores/trib/tribFed/piscofins/CST   (PIS/COFINS)
                 .../IBSCBS/valores/trib/gIBSCBS/CST      (IBS/CBS)

    vBC          NFSe/infNFSe/valores/vBC          (base do ISS)
                 NFSe/infNFSe/IBSCBS/valores/vBC   (base do IBS/CBS)

E ainda ``vIBSUF``, ``vIBSMun`` e ``vCBS``, que existem no totalizador normal e
de novo em ``gTribCompraGov``. Por isso ``_no()`` desce filho a filho: uma busca
larga pegaria o primeiro que encontrasse, e "o primeiro" não é uma regra.

NAMESPACE
---------
``http://www.sped.fazenda.gov.br/nfse`` — não é o da NF-e. Em vez de registrar
prefixo e escrever XPath com ``{ns}``, compara-se o NOME LOCAL. Sobrevive a
mudança de namespace entre versões do leiaute, que já aconteceu antes.
"""
import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


class XmlInvalido(Exception):
    """XML ilegível ou que não é o documento esperado. -> quarentena."""


# ===========================================================================
# EVENTOS — o tipo é o NOME DO ELEMENTO, não um campo
#
# Verificado no XSD (tiposEventos_v1.01): a tag ``tpEvento`` NÃO EXISTE. O tipo
# é o elemento dentro de ``infPedReg``, num grupo de escolha (<xs:choice>). O
# envelope JSON do ADN traz ``TipoEvento`` como enum; usamos o envelope como
# OFICIAL e o nome do elemento como CONFERÊNCIA CRUZADA.
# ===========================================================================
EVENTO_ENUM_PARA_ELEMENTO = {
    'CANCELAMENTO':                            'e101101',
    'CANCELAMENTO_POR_SUBSTITUICAO':           'e105102',
    'SOLICITACAO_CANCELAMENTO_ANALISE_FISCAL': 'e101103',
    'CANCELAMENTO_DEFERIDO_ANALISE_FISCAL':    'e105104',
    'CANCELAMENTO_INDEFERIDO_ANALISE_FISCAL':  'e105105',
    'CONFIRMACAO_PRESTADOR':                   'e202201',
    'CONFIRMACAO_TOMADOR':                     'e203202',
    'CONFIRMACAO_INTERMEDIARIO':               'e204203',
    'CONFIRMACAO_TACITA':                      'e205204',
    'REJEICAO_PRESTADOR':                      'e202205',
    'REJEICAO_TOMADOR':                        'e203206',
    'REJEICAO_INTERMEDIARIO':                  'e204207',
    'ANULACAO_REJEICAO':                       'e205208',
    'CANCELAMENTO_POR_OFICIO':                 'e305101',
    'BLOQUEIO_POR_OFICIO':                     'e305102',
    'DESBLOQUEIO_POR_OFICIO':                  'e305103',
    # INCLUSAO_NFSE_DAN e TRIBUTOS_NFSE_RECOLHIDOS estão no enum do Swagger mas
    # NÃO têm elemento no Anexo II nem no XSD (18 no enum, 16 no schema). Se
    # aparecerem, caem em revisar=1 — que é o comportamento certo para algo que
    # a documentação ainda não descreve.
}
ELEMENTO_PARA_EVENTO_ENUM = {v: k for k, v in EVENTO_ENUM_PARA_ELEMENTO.items()}

# O mapa mora AQUI, e não no repositório, porque a chave é o nome que a API usa
# — e nome de campo da API é assunto deste arquivo, por definição do módulo.
#
# TRÊS ESTADOS, não dois:
#   valor        -> altera situacao
#   None         -> evento CONHECIDO e sem efeito (manifestação, bloqueio)
#   AUSENTE      -> desconhecido, revisar=1
#
# Sem o estado None, toda Confirmação cairia em revisão e o painel viraria
# ruído — o jeito mais rápido de fazer um alerta ser ignorado.
MAPA_EVENTO_SITUACAO = {
    'CANCELAMENTO':                            'cancelada',
    'CANCELAMENTO_POR_SUBSTITUICAO':           'substituida',
    'CANCELAMENTO_DEFERIDO_ANALISE_FISCAL':    'cancelada',
    'CANCELAMENTO_POR_OFICIO':                 'cancelada',

    # PEDIDO não é DECISÃO. Mapear a solicitação como cancelamento cancelaria
    # nota que a prefeitura RECUSOU cancelar — é a armadilha do cStat da NF-e
    # em outra roupagem (ver commit fa24b7c).
    'SOLICITACAO_CANCELAMENTO_ANALISE_FISCAL': None,
    'CANCELAMENTO_INDEFERIDO_ANALISE_FISCAL':  None,

    # Manifestações: dizem o que as partes acham da nota, não o que ela é.
    'CONFIRMACAO_PRESTADOR':                   None,
    'CONFIRMACAO_TOMADOR':                     None,
    'CONFIRMACAO_INTERMEDIARIO':               None,
    'CONFIRMACAO_TACITA':                      None,
    'REJEICAO_PRESTADOR':                      None,
    'REJEICAO_TOMADOR':                        None,
    'REJEICAO_INTERMEDIARIO':                  None,
    'ANULACAO_REJEICAO':                       None,

    # Bloqueio/desbloqueio NÃO são estado da nota: o município restringe quais
    # eventos podem ser registrados dali em diante. A nota segue ATIVA e VÁLIDA.
    # Vão para restricao_eventos, eixo próprio. Ver eventos_restricao().
    'BLOQUEIO_POR_OFICIO':                     None,
    'DESBLOQUEIO_POR_OFICIO':                  None,
}

# O que o bloqueio de ofício pode restringir. Domínio FECHADO, verificado no
# XSD (TSCodigoEventoNFSe): são cinco, todos de cancelamento. Por isso
# "bloqueio" aqui significa "não pode ser cancelada", e não "nota inválida".
CODIGOS_RESTRINGIVEIS = ('e101101', 'e105102', 'e105104', 'e105105', 'e305101')

# cStat da NFS-e — GERAÇÃO, nunca cancelamento.
CSTAT_DESCRICAO = {
    '100': 'NFS-e Gerada',
    '101': 'NFS-e de Substituição Gerada',   # ESTA nota É a substituta
    '102': 'NFS-e de Decisão Judicial ou Administrativa',
    '103': 'NFS-e Avulsa',
    '107': 'NFS-e MEI',
}

# Os TRÊS atores do negócio. `dest` NÃO entra: o ADN distribui a quem figure
# como prestador, tomador ou intermediário, e criar papel por destinatário
# geraria cursor de empresa que nunca recebe a nota.
ATORES = (('emitente', 'prest'), ('tomador', 'toma'), ('intermediario', 'interm'))

# Documento de pessoa: CNPJ, CPF ou NIF são ALTERNATIVOS (coluna ELE = CE no
# leiaute, elemento de escolha). Exatamente um aparece — nunca os três.
DOCS_ALTERNATIVOS = ('CNPJ', 'CPF', 'NIF')


# ---------------------------------------------------------------------------
# Navegação — absoluta, filho a filho
# ---------------------------------------------------------------------------
def _local(tag):
    return tag.rsplit('}', 1)[-1] if '}' in str(tag) else str(tag)


def _no(elem, caminho):
    """Desce por caminho ABSOLUTO comparando nome local. None se faltar algo.

    Deliberadamente NÃO usa ``iter()``/``findall`` recursivo: com nomes
    repetidos em níveis diferentes, busca larga pega o primeiro que aparecer, e
    "o primeiro" não é regra nenhuma. Ver o cabeçalho do módulo.
    """
    atual = elem
    for parte in caminho.split('/'):
        if atual is None:
            return None
        prox = None
        for filho in atual:
            if _local(filho.tag) == parte:
                prox = filho
                break
        atual = prox
    return atual


def _txt(elem, caminho):
    """Texto do nó no caminho absoluto. Ausente ou vazio -> None, nunca ''."""
    no = _no(elem, caminho) if caminho else elem
    if no is None or no.text is None:
        return None
    v = no.text.strip()
    return v or None


def _dec(elem, caminho):
    """Monetário/percentual como ``Decimal``. NUNCA float.

    O leiaute usa ``1-15V2`` (duas casas). Em float, 0.1 + 0.2 não dá 0.3 — num
    campo de imposto isso vira divergência de centavo que ninguém rastreia.
    """
    v = _txt(elem, caminho)
    if v is None:
        return None
    try:
        return Decimal(v)
    except (InvalidOperation, ValueError):
        logger.warning('[nfse-parser] valor não numérico em %s: %r', caminho, v[:30])
        return None


def _int(elem, caminho):
    v = _txt(elem, caminho)
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _data(elem, caminho):
    """``dCompet`` e afins: AAAA-MM-DD -> date."""
    v = _txt(elem, caminho)
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        logger.warning('[nfse-parser] data inválida em %s: %r', caminho, v[:30])
        return None


def _datahora(elem, caminho):
    """AAAA-MM-DDThh:mm:ssTZD -> datetime SEM fuso.

    O resto do sistema guarda horário de Brasília sem tzinfo (mesma convenção do
    DFe). Converter aqui e descartar o offset evita comparar aware com naive
    depois, que estoura em runtime e só no dia em que alguém compara.
    """
    v = _txt(elem, caminho)
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        logger.warning('[nfse-parser] data/hora inválida em %s: %r', caminho, v[:40])
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is None else dt.astimezone().replace(tzinfo=None)


def _doc_de(elem, caminho_grupo):
    """CNPJ, CPF ou NIF do grupo — o que estiver presente. Só dígitos no CNPJ/CPF."""
    grupo = _no(elem, caminho_grupo)
    if grupo is None:
        return None
    for tag in DOCS_ALTERNATIVOS:
        v = _txt(grupo, tag)
        if v:
            return ''.join(c for c in v if c.isdigit()) if tag != 'NIF' else v
    return None


def _raiz(xml_texto, esperada):
    """Parseia e confere a raiz. Levanta XmlInvalido -> quarentena."""
    try:
        root = ET.fromstring(xml_texto)
    except ET.ParseError as exc:
        raise XmlInvalido(f'XML ilegível: {exc}') from exc
    if _local(root.tag) != esperada:
        raise XmlInvalido(
            f'Esperava raiz <{esperada}> e veio <{_local(root.tag)}>.')
    return root


# ---------------------------------------------------------------------------
# Papel
# ---------------------------------------------------------------------------
def extrair_papel(root, cnpj_interessado: str):
    """'emitente' | 'tomador' | 'intermediario', ou None -> quarentena.

    "emitente" no NOSSO schema significa **prestador do serviço**. O nome ficou
    por compatibilidade, mas a origem é ``prest/``, NUNCA ``emit/``: o leiaute
    tem ``tpEmit`` justamente porque a DPS pode ser emitida por quem não prestou
    o serviço. Comparar contra ``emit`` classificaria como prestador uma
    empresa que só digitou a nota.

    Prioridade prestador > tomador > intermediário quando o mesmo CNPJ aparece
    em mais de um papel (acontece em operação entre filiais da mesma raiz).
    """
    alvo = ''.join(c for c in str(cnpj_interessado or '') if c.isdigit())
    if not alvo:
        return None
    base = 'infNFSe/DPS/infDPS'
    for papel, no in ATORES:
        if _doc_de(root, f'{base}/{no}') == alvo:
            return papel
    return None


# ---------------------------------------------------------------------------
# Documento
# ---------------------------------------------------------------------------
def para_registro(xml_texto: str, envelope: dict, empresa_id: int,
                  cnpj_interessado: str) -> dict:
    """XML da NFS-e -> dict pronto para ``nfse_capturadas``.

    ``envelope`` é o item de ``LoteDFe`` (NSU, ChaveAcesso, ...). A chave vem
    DELE, não do ``infNFSe/@Id``: o Id tem 53 caracteres (prefixo + chave de 50)
    e usar o Id inteiro geraria chave que não casa com a de nenhum evento.

    ``papel`` pode voltar None — quem chama manda para quarentena em vez de
    inventar um papel.
    """
    root = _raiz(xml_texto, 'NFSe')
    inf = 'infNFSe'
    dps = 'infNFSe/DPS/infDPS'

    # calculado (autoridade) x declarado (contribuinte) — caminhos distintos,
    # e a diferença entre eles é justamente o que a fiscalização olha.
    val_calc = f'{inf}/valores'
    val_decl = f'{dps}/valores'
    ibs_calc = f'{inf}/IBSCBS'
    ibs_decl = f'{dps}/IBSCBS'

    reg = {
        'empresa_id': empresa_id,
        'cnpj_interessado': ''.join(c for c in str(cnpj_interessado or '') if c.isdigit()),
        'nsu': envelope.get('NSU'),
        'chave_acesso': envelope.get('ChaveAcesso'),
        'papel': extrair_papel(root, cnpj_interessado),
        'tipo_doc': 'nfse',

        'numero': _txt(root, f'{inf}/nNFSe'),
        'serie': _txt(root, f'{dps}/serie'),
        'data_emissao': _datahora(root, f'{dps}/dhEmi'),
        'data_processamento': _datahora(root, f'{inf}/dhProc'),
        # dCompet é a competência fiscal. dhEmi é quando a nota foi emitida —
        # serviço de dezembro emitido em janeiro pertence a dezembro.
        'competencia': _data(root, f'{dps}/dCompet'),
        'municipio_ibge': _txt(root, f'{inf}/cLocIncid'),
        'municipio_emissao': _txt(root, f'{dps}/cLocEmi'),

        'prestador_doc': _doc_de(root, f'{dps}/prest'),
        'prestador_nome': _txt(root, f'{dps}/prest/xNome'),
        'tomador_doc': _doc_de(root, f'{dps}/toma'),
        'tomador_nome': _txt(root, f'{dps}/toma/xNome'),
        'intermediario_doc': _doc_de(root, f'{dps}/interm'),

        'codigo_servico': _txt(root, f'{dps}/serv/cServ/cTribNac'),
        'codigo_servico_mun': _txt(root, f'{dps}/serv/cServ/cTribMun'),
        'codigo_nbs': _txt(root, f'{dps}/serv/cServ/cNBS'),
        'discriminacao': _txt(root, f'{dps}/serv/cServ/xDescServ'),

        'valor_servicos': _dec(root, f'{val_decl}/vServPrest/vServ'),
        'valor_desc_incond': _dec(root, f'{val_decl}/vDescCondIncond/vDescIncond'),
        'valor_desc_cond': _dec(root, f'{val_decl}/vDescCondIncond/vDescCond'),
        # vBC do ISS. NÃO confundir com o vBC de IBSCBS/valores/ — nomes iguais,
        # bases diferentes.
        'base_calculo': _dec(root, f'{val_calc}/vBC'),
        'aliquota_iss': _dec(root, f'{val_calc}/pAliqAplic'),
        'valor_iss': _dec(root, f'{val_calc}/vISSQN'),
        'total_retencoes': _dec(root, f'{val_calc}/vTotalRet'),
        'valor_liquido': _dec(root, f'{val_calc}/vLiq'),
        'iss_retido': _iss_retido(root, val_decl),
        'opt_simples': _int(root, f'{dps}/prest/regTrib/opSimpNac'),

        'cstat': _int(root, f'{inf}/cStat'),
        # subst/chSubstda: qual nota ESTA substituiu. Sentido OPOSTO ao
        # chSubstituta do evento (que fica na nota velha e aponta para a nova).
        # Gravado para verificação cruzada; NUNCA escreve situacao.
        'substitui_chave': _txt(root, f'{dps}/subst/chSubstda'),

        # dest é o QUARTO ator e vira DADO, nunca papel.
        'destinatario_doc': _doc_de(root, f'{ibs_decl}/dest'),
        'destinatario_nome': _txt(root, f'{ibs_decl}/dest/xNome'),

        # --- IBS/CBS declarado ---
        'ibscbs_cst': _txt(root, f'{ibs_decl}/valores/trib/gIBSCBS/CST'),
        'ibscbs_cclasstrib': _txt(root, f'{ibs_decl}/valores/trib/gIBSCBS/cClassTrib'),
        'ibscbs_fin_nfse': _int(root, f'{ibs_decl}/finNFSe'),
        'ibscbs_cind_op': _txt(root, f'{ibs_decl}/cIndOp'),
        'ibscbs_ind_dest': _int(root, f'{ibs_decl}/indDest'),

        # --- IBS/CBS calculado. UF e Município SEPARADOS: entes diferentes,
        # alíquotas e diferimentos diferentes. Somar destrói a apuração. ---
        'ibscbs_bc': _dec(root, f'{ibs_calc}/valores/vBC'),
        'ibs_uf_aliq_efet': _dec(root, f'{ibs_calc}/valores/uf/pAliqEfetUF'),
        'ibs_uf_valor': _dec(root, f'{ibs_calc}/totCIBS/gIBS/gIBSUFTot/vIBSUF'),
        'ibs_uf_dif': _dec(root, f'{ibs_calc}/totCIBS/gIBS/gIBSUFTot/vDifUF'),
        'ibs_mun_aliq_efet': _dec(root, f'{ibs_calc}/valores/mun/pAliqEfetMun'),
        'ibs_mun_valor': _dec(root, f'{ibs_calc}/totCIBS/gIBS/gIBSMunTot/vIBSMun'),
        'ibs_mun_dif': _dec(root, f'{ibs_calc}/totCIBS/gIBS/gIBSMunTot/vDifMun'),
        'ibs_total': _dec(root, f'{ibs_calc}/totCIBS/gIBS/vIBSTot'),
        'cbs_aliq_efet': _dec(root, f'{ibs_calc}/valores/fed/pAliqEfetCBS'),
        'cbs_valor': _dec(root, f'{ibs_calc}/totCIBS/gCBS/vCBS'),
        'cbs_dif': _dec(root, f'{ibs_calc}/totCIBS/gCBS/vDifCBS'),
        'ibs_cred_pres': _dec(root, f'{ibs_calc}/totCIBS/gIBS/gIBSCredPres/vCredPresIBS'),
        'cbs_cred_pres': _dec(root, f'{ibs_calc}/totCIBS/gCBS/gCBSCredPres/vCredPresCBS'),
        'valor_total_nf': _dec(root, f'{ibs_calc}/totCIBS/vTotNF'),
    }
    return reg


def _iss_retido(root, val_decl):
    """1 quando há retenção de ISS, 0 quando não, None quando o campo não veio.

    ``tpRetISSQN`` distingue QUEM retém; para a conferência importa se houve
    retenção. Os três estados são diferentes: 'não retido' e 'não informado'
    não podem virar o mesmo valor.
    """
    v = _int(root, f'{val_decl}/trib/tribMun/tpRetISSQN')
    if v is None:
        return None
    return 1 if v in (1, 2) else 0


# ---------------------------------------------------------------------------
# Evento
# ---------------------------------------------------------------------------
def _grupo_especifico(root):
    """(nome_do_elemento, nó) do grupo de escolha dentro de infPedReg.

    O tipo do evento É o nome deste elemento — não existe tag ``tpEvento``.
    """
    inf = _no(root, 'pedRegEvento/infPedReg')
    if inf is None:
        return None, None
    for filho in inf:
        nome = _local(filho.tag)
        if nome in ELEMENTO_PARA_EVENTO_ENUM:
            return nome, filho
    return None, None


def evento_para_registro(xml_texto: str, envelope: dict, empresa_id: int,
                         cnpj_interessado: str) -> dict:
    """XML do evento -> dict pronto para ``nfse_eventos``.

    O tipo vem do ENVELOPE (``TipoEvento``, enum do ADN), que é a fonte oficial.
    O nome do elemento serve de CONFERÊNCIA CRUZADA: divergência entre os dois
    devolve ``divergencia`` preenchido, e quem chama manda para quarentena. Duas
    fontes concordando é confirmação; discordando é sinal de que o leiaute mudou
    debaixo de nós.
    """
    root = _raiz(xml_texto, 'evento')
    inf_pr = 'pedRegEvento/infPedReg'

    tipo_envelope = envelope.get('TipoEvento')
    elemento, grupo = _grupo_especifico(root)
    tipo_elemento = ELEMENTO_PARA_EVENTO_ENUM.get(elemento) if elemento else None

    divergencia = None
    if tipo_envelope and tipo_elemento and tipo_envelope != tipo_elemento:
        divergencia = (f'envelope diz {tipo_envelope} e o XML traz '
                       f'{elemento} ({tipo_elemento})')
        logger.warning('[nfse-parser] divergência de tipo de evento: %s', divergencia)

    tipo = tipo_envelope or tipo_elemento

    reg = {
        'empresa_id_origem': empresa_id,
        'cnpj_origem': ''.join(c for c in str(cnpj_interessado or '') if c.isdigit()),
        'nsu_origem': envelope.get('NSU'),

        'chave_referenciada': _txt(root, f'{inf_pr}/chNFSe'),
        'tipo_evento': tipo,
        # nSeqEvento é obrigatório (3 dígitos) e existe de verdade — nada de
        # default 0. Cancelamento é sempre 001; tipos repetíveis são numerados.
        'sequencia': _int(root, 'infEvento/nSeqEvento'),
        'data_evento': _datahora(root, f'{inf_pr}/dhEvento'),
        'motivo': _txt(grupo, 'xMotivo') if grupo is not None else None,

        # chSubstituta: no EVENTO da nota velha, apontando para a SUBSTITUTA
        # (nota nova). Sentido OPOSTO ao subst/chSubstda do documento. Nomes
        # quase idênticos, sentidos invertidos — trocar cancela a nota errada.
        'chave_substituta': (_txt(grupo, 'chSubstituta')
                             if elemento == 'e105102' else None),

        # AUSENTE do mapa -> revisar=1. Presente com None é conhecido e sem
        # efeito; são coisas diferentes.
        'revisar': 0 if tipo in MAPA_EVENTO_SITUACAO else 1,
        'divergencia': divergencia,
        'elemento': elemento,
    }
    return reg


def restricao_do_evento(xml_texto: str) -> dict:
    """Bloqueio/desbloqueio de ofício -> eixo ``restricao_eventos``.

    NÃO É ESTADO FISCAL. O ``codEvento`` tem domínio fechado de cinco valores,
    todos de cancelamento (verificado no XSD, ``TSCodigoEventoNFSe``): o
    município está impedindo que a nota seja CANCELADA. Ela segue ativa, válida
    e vale como documento fiscal.

    Devolve ``{'restrito': bool, 'codigos': [...], 'ref': str|None}``.
    ``ref`` é o id do bloqueio que o desbloqueio anula.
    """
    root = _raiz(xml_texto, 'evento')
    elemento, grupo = _grupo_especifico(root)
    if elemento == 'e305102':
        codigos = [c for c in (_txt(grupo, 'codEvento') or '').split(',')
                   if c.strip() in CODIGOS_RESTRINGIVEIS]
        desconhecidos = [c.strip() for c in (_txt(grupo, 'codEvento') or '').split(',')
                         if c.strip() and c.strip() not in CODIGOS_RESTRINGIVEIS]
        if desconhecidos:
            # Domínio fechado: valor fora dele é leiaute novo, não dado sujo.
            logger.warning('[nfse-parser] codEvento fora do dominio conhecido: %s',
                           desconhecidos)
        return {'restrito': True, 'codigos': codigos, 'ref': None}
    if elemento == 'e305103':
        return {'restrito': False, 'codigos': [],
                'ref': _txt(grupo, 'idBloqOfic')}
    return {'restrito': None, 'codigos': [], 'ref': None}
