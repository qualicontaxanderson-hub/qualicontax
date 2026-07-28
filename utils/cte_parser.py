# -*- coding: utf-8 -*-
"""Parser de XML de CT-e (Conhecimento de Transporte Eletrônico).

Equivalente do ``utils/nfe_parser.py`` para o frete. Sem dependência de banco,
Flask ou rede — só XML entra, dict sai (mesma disciplina do nfe_parser, para
poder ser usado tanto pela captura SEFAZ quanto pelo Dropbox/upload depois).

Cobre:
  * ``cteProc`` / ``CTe``      — CT-e modelo 57 (o caso normal)
  * ``cteOSProc`` / ``CTeOS``  — CT-e Outros Serviços, modelo 67 (tomador direto)
  * ``GTVe``                   — best-effort (modelo 64; layout enxuto)

Diferenças estruturais em relação à NF-e que moldam o retorno:
  * CT-e NÃO tem itens/produtos. O "detalhe" são as NF-e transportadas
    (``infCTeNorm/infDoc/infNFe/chave``) — devolvidas em ``nfes``.
  * As partes são 5 (emitente=transportadora, remetente, expedidor, recebedor,
    destinatário) e o TOMADOR é uma delas, indicado por ``toma3/toma`` (0=rem,
    1=exped, 2=receb, 3=dest) ou informado à parte em ``toma4``. O parser já
    RESOLVE quem é o tomador e devolve ``tomador_cnpj``/``tomador_nome``/
    ``tomador_papel`` prontos — quem consome não precisa reinterpretar o código.
  * O valor que interessa é o do frete (``vPrest/vTPrest``), não vNF.

Busca por nome LOCAL de tag (ignora namespace) porque o mesmo parser recebe XML
do Dropbox (com ns declarado de formas variadas) e da SEFAZ.
"""
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

# Fuso de Brasília (offset fixo; Brasil sem horário de verão desde 2019).
# Duplicado do dfe_captura de propósito: este módulo é puro (sem deps de banco).
_TZ_BR = timezone(timedelta(hours=-3))

# toma (CT-e 57): quem é o tomador do serviço entre as partes do documento.
_TOMA_PARTE = {'0': 'rem', '1': 'exped', '2': 'receb', '3': 'dest'}
_TOMA_LABEL = {'0': 'Remetente', '1': 'Expedidor', '2': 'Recebedor',
               '3': 'Destinatário', '4': 'Outro'}

# Grupos de ICMS possíveis dentro de <imp><ICMS>. Só um vem preenchido.
_ICMS_GRUPOS = ('ICMS00', 'ICMS20', 'ICMS45', 'ICMS60', 'ICMS90',
                'ICMSOutraUF', 'ICMSSN')


# --------------------------------------------------------------------------
# Helpers de XML (nome local, namespace-agnóstico)
# --------------------------------------------------------------------------
def _local(tag):
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


def _child(node, name):
    """Primeiro filho DIRETO com esse nome local, ou None."""
    if node is None:
        return None
    for c in node:
        if _local(c.tag) == name:
            return c
    return None


def _txt(node, name, default=''):
    """Texto de um filho DIRETO (não desce na árvore — evita pegar o CNPJ errado)."""
    el = _child(node, name)
    return el.text.strip() if (el is not None and el.text) else default


def _deep(node, name):
    """Primeiro descendente com esse nome local, ou None."""
    if node is None:
        return None
    for e in node.iter():
        if _local(e.tag) == name:
            return e
    return None


def _deep_txt(node, name, default=''):
    el = _deep(node, name)
    return el.text.strip() if (el is not None and el.text) else default


def _deep_all(node, name):
    """Todos os descendentes com esse nome local."""
    if node is None:
        return []
    return [e for e in node.iter() if _local(e.tag) == name]


def _float(node, name, default=0.0, deep=False):
    v = (_deep_txt if deep else _txt)(node, name, '')
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _digitos(v):
    return ''.join(ch for ch in str(v or '') if ch.isdigit())


def _parse_dh(s):
    """'2026-07-22T09:30:00-03:00' -> (date, 'YYYY-MM-DD HH:MM:SS' em BRT).

    Converte o offset para Brasília: a data que vai para a coluna sai da EMISSÃO
    do documento, não da hora da captura. Sem offset no XML, assume que já é BRT.
    """
    if not s:
        return None, None
    txt = s.strip()
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        try:
            dt = datetime.strptime(txt[:19], '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            try:
                return datetime.strptime(txt[:10], '%Y-%m-%d').date(), None
            except ValueError:
                return None, None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_TZ_BR)
    return dt.date(), dt.strftime('%Y-%m-%d %H:%M:%S')


# --------------------------------------------------------------------------
# Partes (emit / rem / exped / receb / dest / toma4)
# --------------------------------------------------------------------------
def _parte(node):
    """Extrai (cnpj, nome, uf) de um grupo de parte do CT-e.

    O CNPJ/CPF é filho DIRETO do grupo; a UF vive no ender* aninhado
    (enderEmit/enderReme/enderExped/enderReceb/enderDest/enderToma).
    """
    if node is None:
        return '', '', ''
    doc = _txt(node, 'CNPJ') or _txt(node, 'CPF')
    nome = _txt(node, 'xNome')
    uf = ''
    for c in node:
        if _local(c.tag).startswith('ender'):
            uf = _txt(c, 'UF')
            break
    if not uf:
        uf = _deep_txt(node, 'UF')
    return _digitos(doc), nome[:255], (uf or '')[:2]


def _resolver_tomador(ide, partes):
    """Descobre quem paga o frete.

    CT-e 57: ``toma3/toma`` aponta uma das partes (0=rem,1=exped,2=receb,3=dest);
    ``toma4`` traz o tomador à parte (CNPJ/xNome próprios).
    CT-e OS 67: ``ide/toma`` JÁ É o grupo do tomador (com CNPJ/xNome dentro).

    Devolve (toma_cod, cnpj, nome, label_do_papel).
    """
    toma3 = _child(ide, 'toma3')
    toma4 = _child(ide, 'toma4')
    toma = _child(ide, 'toma')

    if toma4 is not None:
        cnpj, nome, _uf = _parte(toma4)
        return (_txt(toma4, 'toma') or '4'), cnpj, nome, _TOMA_LABEL['4']

    if toma3 is not None:
        cod = _txt(toma3, 'toma')
        chave = _TOMA_PARTE.get(cod)
        cnpj, nome = partes.get(chave, ('', '', ''))[:2] if chave else ('', '')
        return cod, cnpj, nome, _TOMA_LABEL.get(cod, '')

    if toma is not None:
        # CT-e OS: o próprio grupo é o tomador. Se vier só <toma>3</toma> (layout
        # de CT-e normal que usa a tag sem o wrapper), cai no mapa das partes.
        cnpj, nome, _uf = _parte(toma)
        if cnpj or nome:
            return None, cnpj, nome, 'Tomador'
        cod = (toma.text or '').strip()
        chave = _TOMA_PARTE.get(cod)
        if chave:
            cnpj, nome = partes.get(chave, ('', '', ''))[:2]
            return cod, cnpj, nome, _TOMA_LABEL.get(cod, '')

    return None, '', '', ''


# --------------------------------------------------------------------------
# NF-e transportadas
# --------------------------------------------------------------------------
def _nfes_transportadas(inf_cte):
    """Documentos transportados pelo CT-e.

    * ``infNFe/chave``  — NF-e eletrônica (o caso atual). ATENÇÃO: a tag é
      ``chave``, NÃO ``chNFe`` como na NF-e.
    * ``infNF``         — nota em papel/modelo antigo (série/nº/valor, sem chave).

    Deduplica por chave preservando a ordem (o UNIQUE(cte_id, chave_nfe) do banco
    rejeitaria o segundo, e um CT-e mal formado pode repetir a mesma chave).
    """
    out, vistas = [], set()
    for el in _deep_all(inf_cte, 'infNFe'):
        chave = _digitos(_txt(el, 'chave'))
        if len(chave) != 44 or chave in vistas:
            continue
        vistas.add(chave)
        out.append({'chave_nfe': chave, 'num_nota': '', 'serie': '', 'valor': None})

    for el in _deep_all(inf_cte, 'infNF'):
        num = _txt(el, 'nDoc')
        serie = _txt(el, 'serie')
        marca = f'NF{serie}-{num}'
        if marca in vistas:
            continue
        vistas.add(marca)
        out.append({'chave_nfe': None, 'num_nota': num[:20], 'serie': serie[:6],
                    'valor': _float(el, 'vNF', None) or None})
    return out


# --------------------------------------------------------------------------
# Entrada pública
# --------------------------------------------------------------------------
def parse_cte_xml(xml_content: str) -> dict:
    """Parseia um XML de CT-e.

    Returns:
        dict com ``chave``, ``header`` (dict pronto para o INSERT) e ``nfes``
        (lista das NF-e transportadas).
    Raises:
        ValueError se o XML não for um CT-e válido.
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise ValueError(f'XML inválido: {exc}') from exc

    raiz = _local(root.tag)

    # Aceita o documento processado (cteProc/cteOSProc) ou o CT-e nu.
    inf_cte = None
    for nome in ('CTe', 'CTeOS', 'GTVe'):
        doc = root if raiz == nome else _child(root, nome)
        if doc is not None:
            inf_cte = _child(doc, 'infCte') or _child(doc, 'infCTeOS') or _child(doc, 'infGTVe')
            if inf_cte is not None:
                break
    if inf_cte is None:
        inf_cte = _deep(root, 'infCte')
    if inf_cte is None:
        raise ValueError(f'Nó <infCte> não encontrado (raiz={raiz!r}) — não é um CT-e')

    # Chave: atributo Id ('CTe' + 44 dígitos); fallback no protocolo.
    chave = _digitos(inf_cte.get('Id') or inf_cte.get('id') or '')
    if len(chave) > 44:
        chave = chave[-44:]
    if len(chave) != 44:
        chave = _digitos(_deep_txt(root, 'chCTe'))
    if len(chave) != 44:
        raise ValueError('CT-e sem chave de acesso de 44 dígitos')

    ide = _child(inf_cte, 'ide')
    if ide is None:
        raise ValueError('CT-e sem grupo <ide>')

    data_emissao, dh_emissao = _parse_dh(_txt(ide, 'dhEmi') or _txt(ide, 'dEmi'))

    # ---- partes ----
    p_emit = _parte(_child(inf_cte, 'emit'))
    p_rem = _parte(_child(inf_cte, 'rem'))
    p_dest = _parte(_child(inf_cte, 'dest'))
    p_exped = _parte(_child(inf_cte, 'exped'))
    p_receb = _parte(_child(inf_cte, 'receb'))
    partes = {'rem': p_rem, 'dest': p_dest, 'exped': p_exped, 'receb': p_receb}
    toma_cod, toma_cnpj, toma_nome, toma_label = _resolver_tomador(ide, partes)

    # ---- valores do frete ----
    vprest = _child(inf_cte, 'vPrest')
    valor_frete = _float(vprest, 'vTPrest')
    valor_receber = _float(vprest, 'vRec')

    # ---- ICMS: só um dos grupos de <imp><ICMS> vem preenchido ----
    imp = _child(inf_cte, 'imp')
    icms_grp = None
    icms = _child(imp, 'ICMS') if imp is not None else None
    for nome in _ICMS_GRUPOS:
        icms_grp = _child(icms, nome)
        if icms_grp is not None:
            break

    # ---- protocolo de autorização ----
    prot = _deep(root, 'infProt')
    cstat = _txt(prot, 'cStat') if prot is not None else ''

    header = {
        'chave_acesso': chave,
        'modelo': (_txt(ide, 'mod') or chave[20:22])[:2],
        'num_cte': (_txt(ide, 'nCT') or _txt(ide, 'nGTV'))[:20],
        'serie': _txt(ide, 'serie')[:6],
        'data_emissao': data_emissao,
        'dh_emissao': dh_emissao,
        'cfop': _txt(ide, 'CFOP')[:10],
        'natureza_operacao': _txt(ide, 'natOp')[:255],
        'tp_cte': (_txt(ide, 'tpCTe') or None),
        'tp_serv': (_txt(ide, 'tpServ') or None),
        'modal': (_txt(ide, 'modal') or None),

        'emit_cnpj': p_emit[0], 'emit_nome': p_emit[1], 'emit_uf': p_emit[2],
        'rem_cnpj': p_rem[0],   'rem_nome': p_rem[1],   'rem_uf': p_rem[2],
        'dest_cnpj': p_dest[0], 'dest_nome': p_dest[1], 'dest_uf': p_dest[2],
        'exped_cnpj': p_exped[0], 'exped_nome': p_exped[1],
        'receb_cnpj': p_receb[0], 'receb_nome': p_receb[1],

        'toma_cod': toma_cod,
        'tomador_cnpj': toma_cnpj,
        'tomador_nome': toma_nome,
        'tomador_papel': (toma_label or '')[:14],

        'uf_ini': _txt(ide, 'UFIni')[:2],
        'mun_ini': _txt(ide, 'xMunIni')[:120],
        'uf_fim': _txt(ide, 'UFFim')[:2],
        'mun_fim': _txt(ide, 'xMunFim')[:120],

        'valor_frete': valor_frete,
        'valor_receber': valor_receber,
        'valor_bc_icms': _float(icms_grp, 'vBC'),
        'valor_icms': _float(icms_grp, 'vICMS'),
        'aliq_icms': _float(icms_grp, 'pICMS'),
        'cst_icms': _txt(icms_grp, 'CST')[:2],
        'valor_tot_trib': _float(imp, 'vTotTrib', deep=True),

        'protocolo': (_txt(prot, 'nProt') if prot is not None else '')[:20],
        # cStat 101 = cancelamento homologado. O cancelamento normal chega por
        # evento (procEventoCTe) e é aplicado pela captura, não aqui.
        'cancelado': 1 if cstat == '101' else 0,
    }

    return {'chave': chave, 'header': header, 'nfes': _nfes_transportadas(inf_cte)}


def papel_do_cliente(header: dict, cnpj_cliente: str) -> str:
    """Em que papel a empresa aparece neste CT-e.

    O foco do módulo é o TOMADOR (quem paga o frete), mas a distribuição da SEFAZ
    entrega o CT-e para todos os atores — então o papel real fica registrado em vez
    de assumir 'tomador' para tudo. Ordem de precedência: tomador primeiro (é o que
    importa fiscalmente), emitente por último (aí o cliente é a transportadora).

    Devolve 'tomador'|'remetente'|'destinatario'|'expedidor'|'recebedor'|
    'emitente'|'outro' (cabe em VARCHAR(14)).
    """
    alvo = _digitos(cnpj_cliente)
    if not alvo:
        return 'outro'
    for papel, campo in (
        ('tomador', 'tomador_cnpj'), ('remetente', 'rem_cnpj'),
        ('destinatario', 'dest_cnpj'), ('expedidor', 'exped_cnpj'),
        ('recebedor', 'receb_cnpj'), ('emitente', 'emit_cnpj'),
    ):
        if _digitos(header.get(campo)) == alvo:
            return papel
    return 'outro'
