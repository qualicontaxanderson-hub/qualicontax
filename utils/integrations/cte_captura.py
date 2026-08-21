# -*- coding: utf-8 -*-
"""
Motor de captura de CT-e via CTeDistribuicaoDFe.

SÓ CONSULTA E SALVA. **NUNCA MANIFESTA** (no CT-e nem existe manifestação: a
distribuição entrega o documento completo a todos os atores).

Reusa TODA a fundação do motor de NF-e (``dfe_captura``), importada explicitamente
em vez de copiada: certificado do cliente, mTLS em memória, parsing de datas em
BRT, UF do endereço principal, conexão dedicada por transação, arquivamento do XML
no Dropbox no padrão IMPORTADOS/ERROS, drenagem multi-lote e a disciplina de cota.

O que é PRÓPRIO do CT-e (e por quê):
  * **Cursor isolado** (``dfe_nsu_cte``): o CTeDistribuicaoDFe tem sequência de NSU
    e cota 656 independentes do NF-e. Compartilhar ``dfe_nsu`` embaralharia os dois
    cursores e faria cada serviço "pular" documentos do outro.
  * **Sem máquina de resumo**: não existe ``resCTe``. Todo CT-e entregue já vem
    completo → nada de incompleta=1, retry por chave ou upgrade resumo→completa.
    O papel de recuperação pontual fica com o ``consNSU`` (``buscar_nsu``).
  * **Lock próprio** (``dfe_captura_cte``): a rodada de CT-e pode correr em
    paralelo com a de NF-e sem uma segurar a outra.

Foco: o CT-e onde a empresa é TOMADORA (paga o frete). Como a distribuição também
entrega quando ela é remetente/destinatário/expedidor/recebedor, esses TAMBÉM são
gravados — com o papel real em ``papel_cliente``, sem fingir que são tomados.

Índice no banco (cte_documentos/cte_nfe), arquivo (XML) no Dropbox.
"""
import base64
import gzip
import logging
import os
import time
import xml.etree.ElementTree as ET

from utils.db_helper import execute_query
from utils.certificado_digital import (
    carregar_par_chave_cert, decifrar_senha, CertificadoError,
)
from models.cliente import Cliente
from models.dfe_certificado import DfeCertificado
from utils import dropbox_sync
from utils.integrations.cte_sefaz import (
    consultar, consultar_nsu, montar_sessao_mtls, cuf_autor, UfInvalidaError,
    _find, _text, _local,
)
from utils.integrations import dfe_log
from utils.cte_parser import parse_cte_xml, papel_do_cliente, _digitos
from utils.cte_import import _save_cte, marcar_cte_cancelado
# Fundação compartilhada com o motor de NF-e (mesma semântica, um só lugar).
from utils.integrations.dfe_captura import (
    _digitos, _to_int, _parse_dh, _hoje_brt, _uf_principal, _conectar_lock,
    _caminho_fiscal, _subir_xml, _pasta_dfe, _detalhe_403,
)

logger = logging.getLogger(__name__)

TP_CANCELAMENTO_CTE = '110111'   # tpEvento de cancelamento de CT-e

# cStat da RESPOSTA da SEFAZ que significam cancelamento ACEITO. Fonte única
# para os DOIS caminhos que cancelam CT-e (esta captura e o fiscal_ingest, que
# importa da _ENTRADA) — até 14/08/2026 eles divergiam, e o resultado era erro
# nas duas direções: aqui não se conferia NADA (pedido rejeitado cancelava), e
# lá só se aceitava {135,136} (cancelamento tardio legítimo era descartado).
#
# O 155 é "cancelamento homologado FORA DE PRAZO" — aceito, apenas tardio.
# Medido no lado NF-e em 14/08/2026: dos 110 eventos reais, 99 vieram 135 e
# **11 vieram 155**. Ignorá-lo descartaria 1 em cada 10 cancelamentos legítimos.
#
# RESSALVA HONESTA: no CT-e isso NÃO foi medido — os XML de evento de CT-e não
# ficam no banco, só no Dropbox, e não houve como varrer. A inclusão do 155 aqui
# é aposta fundamentada (a tabela de cStat da SEFAZ é largamente compartilhada
# entre os modelos), e o pior caso dela é inofensivo: se o CT-e nunca emitir
# 155, a constante simplesmente nunca casa. O log abaixo resolve a dúvida no
# primeiro evento que chegar fora do conjunto.
CSTAT_CANCELAMENTO_CTE_OK = {'135', '136', '155'}

# Scheduler: lock global próprio + prazo suave por rodada.
_LOCK_CAPTURA = 'dfe_captura_cte'
_PRAZO_SUAVE_SEG = int(os.getenv('CTE_SCHED_PRAZO_SEG', '480'))   # ~8 min

# Drenagem multi-lote (mesmo padrão do NF-e). Tetos conservadores: prefere-se
# drenar um backlog grande em alguns ciclos a segurar o worker numa rodada gigante.
_MAX_LOTES_CICLO = int(os.getenv('CTE_MAX_LOTES_CICLO', '60'))
#   _MAX_JANELAS_VAZIAS  teto de janelas VAZIAS (137 com fila à frente) encadeadas
#                        num mesmo ciclo — contador próprio, separado do de lotes.
#                        Ver o comentário longo no dfe_captura.py.
_MAX_JANELAS_VAZIAS = int(os.getenv('CTE_MAX_JANELAS_VAZIAS', '50'))
# Cooldown do 656. Configurável como o do NF-e, mas o DEFAULT segue 60 (o valor
# de hoje) de propósito: na medição de 06→07/08 o CT-e teve ZERO 656 em 248
# consultas — o cron dele já roda a ~60 min e não se atropela. Subir para 130
# só o deixaria mais lento sem resolver problema nenhum. Se um dia ele começar a
# tomar 656, é a env var que se mexe, não o código.
_COOLDOWN_656_MIN = int(os.getenv('CTE_COOLDOWN_656_MIN', '60'))
_MAX_SEG_EMPRESA = int(os.getenv('CTE_MAX_SEG_EMPRESA', '90'))
_INTERVALO_LOTE = float(os.getenv('CTE_INTERVALO_LOTE_SEG', '0.4'))

# Raízes de docZip que interessam. procCTe é o nome do SCHEMA; a tag raiz do
# documento é cteProc (e cteOSProc para o CT-e OS).
_RAIZ_CTE = frozenset({'cteProc', 'procCTe', 'CTe', 'cteOSProc', 'CTeOS', 'GTVe'})
_RAIZ_EVENTO = frozenset({'procEventoCTe', 'eventoCTe', 'retEventoCTe'})


# ==========================================================================
# SQLs — cursor/cota em dfe_nsu_cte (espelho do dfe_nsu, tabela isolada)
# ==========================================================================
# max_nsu=0 significa "a SEFAZ não informou" (o 656 não traz maxNSU), NUNCA
# "zero documentos" — o 0 preserva o valor atual.
_MAX_NSU = "max_nsu=IF(VALUES(max_nsu) > 0, VALUES(max_nsu), max_nsu)"

SQL_NSU_OK = (
    "INSERT INTO dfe_nsu_cte "
    "(cliente_id, cnpj, ult_nsu, max_nsu, ult_consulta, proximo_permitido, ult_status) "
    "VALUES (%s,%s,%s,%s,NOW(),NULL,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  ult_nsu=VALUES(ult_nsu), " + _MAX_NSU + ", "
    "  ult_consulta=NOW(), proximo_permitido=NULL, ult_status=VALUES(ult_status)"
)

SQL_NSU_656 = (
    "INSERT INTO dfe_nsu_cte "
    "(cliente_id, cnpj, ult_nsu, max_nsu, ult_consulta, proximo_permitido, ult_status) "
    f"VALUES (%s,%s,%s,%s,NOW(),NOW() + INTERVAL {_COOLDOWN_656_MIN} MINUTE,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  ult_nsu=VALUES(ult_nsu), " + _MAX_NSU + ", "
    f"  ult_consulta=NOW(), proximo_permitido=NOW() + INTERVAL {_COOLDOWN_656_MIN} MINUTE, "
    "  ult_status=VALUES(ult_status)"
)


_TABELAS_EXIGIDAS = ('cte_documentos', 'cte_nfe', 'dfe_nsu_cte')
_schema_ok = False   # cacheia só o resultado POSITIVO (ver _schema_pronto)


def _schema_pronto():
    """As tabelas de CT-e existem neste banco?

    Por que isto existe: o processo do CRON **não roda migrations** (de propósito —
    ver cron_captura_dfe.py; quem migra é o boot do serviço web). Num deploy, o Cron
    pode disparar ANTES de o web subir e migrar. Sem esta checagem, a rodada iria à
    SEFAZ, gastaria cota de verdade (que conta para o 656) e falharia na gravação de
    todo documento — o pior dos mundos: custo sem resultado.

    Cacheia apenas o TRUE: um FALSE é transitório (a migration vai rodar) e não deve
    ficar preso na memória do worker.
    """
    global _schema_ok
    if _schema_ok:
        return True
    # Blindado de propósito: este é um GUARD — se ele mesmo levantar exceção,
    # derruba a rodada que deveria proteger. Qualquer resposta inesperada do
    # execute_query vira "não pronto" (conservador: não vai à SEFAZ).
    try:
        row = execute_query(
            "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s, %s)",
            _TABELAS_EXIGIDAS, fetch=True, fetch_one=True,
        )
        if isinstance(row, dict) and int(row.get('cnt') or 0) == len(_TABELAS_EXIGIDAS):
            _schema_ok = True
            return True
    except Exception:
        logger.warning('[cte] checagem de schema falhou; tratando como não pronto.',
                       exc_info=True)
    return False


def _erro_schema():
    return {
        'ok': False, 'schema_pendente': True,
        'erro': 'As tabelas de CT-e ainda não existem neste banco '
                f'({", ".join(_TABELAS_EXIGIDAS)}). Elas são criadas no boot do '
                'serviço web (run_migrations) ou rodando '
                '"python migrations/add_cte_captura.py". A captura foi abortada '
                'ANTES de consultar a SEFAZ para não gastar cota sem ter onde gravar.',
    }


def _bloqueado_por_cota(cliente_id):
    """Linha com faltam_min se a SEFAZ ainda pediu para aguardar, ou None.
    Comparação 100% no relógio do banco (NOW()), nunca no do Python."""
    return execute_query(
        "SELECT proximo_permitido, "
        "TIMESTAMPDIFF(MINUTE, NOW(), proximo_permitido) AS faltam_min "
        "FROM dfe_nsu_cte WHERE cliente_id = %s "
        "AND proximo_permitido IS NOT NULL AND proximo_permitido > NOW()",
        (cliente_id,), fetch=True, fetch_one=True,
    )


def _ler_ult_nsu(cliente_id):
    row = execute_query(
        "SELECT ult_nsu FROM dfe_nsu_cte WHERE cliente_id = %s",
        (cliente_id,), fetch=True, fetch_one=True,
    )
    return int(row['ult_nsu']) if row and row.get('ult_nsu') is not None else 0


def _index_clientes():
    """{cpf_cnpj_só_dígitos: {id, numero, razao}} de todos os clientes.

    UMA consulta por rodada. A captura de NF-e usa ``Cliente.index_cpf_cnpj()``,
    que devolve só o id; aqui também é preciso número e razão, porque a pasta do
    Dropbox é montada com eles e o XML tem de ser arquivado na pasta do DONO do
    documento, não na de quem consultou.
    """
    rows = execute_query(
        "SELECT id, numero_cliente, nome_razao_social, "
        "  REPLACE(REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-',''),' ','') AS d "
        "FROM clientes WHERE avulso = 0 "          # avulso nao e cliente fiscal
        "  AND cpf_cnpj IS NOT NULL AND cpf_cnpj <> ''",
        fetch=True) or []
    return {r['d']: {'cliente_id': r['id'], 'numero': r['numero_cliente'],
                     'razao': r['nome_razao_social'], 'cnpj': r['d']}
            for r in rows if r['d'] and len(r['d']) >= 11}


def _destinos_do_cte(header, empresa, cli_index):
    """Para QUEM este CT-e deve ser gravado. Devolve [(empresa_alvo, papel), ...].

    A REGRA (Anderson, 18/08/2026)
    ------------------------------
      1. o dono do certificado É PARTE do CT-e  -> linha dele, com o papel real
      2. chegou por autXML (dono não é parte)   -> procura, entre TODAS as partes,
         quem é cliente cadastrado; achou, a linha é DELE
      3. ninguém cadastrado                     -> FICA no dono do certificado

    Por que isto existe: o ``autXML`` do CT-e leva o CNPJ do contador, e é assim
    que o certificado do escritório puxa da SEFAZ o frete que o cliente emitiu.
    Sem este roteamento, a captura gravava tudo em ``empresa['cliente_id']`` e o
    CT-e da HPA ia parar na Conferência da Qualicontax — 126 documentos assim, em
    18/08/2026. A captura de NF-e já fazia o equivalente (o "dual-save"), e foi
    isso que fez as saídas da KET migrarem sozinhas quando ela foi cadastrada.

    DIVERGE DA NF-e no caso 3 de propósito: lá o autXML de terceiro puro é
    DESCARTADO; aqui fica com o escritório, porque frete que ele foi autorizado a
    ver continua interessando a ele.
    """
    destinos = []
    vistos = set()

    papel_dono = papel_do_cliente(header, empresa.get('cnpj') or '')
    if papel_dono != 'outro':
        destinos.append((empresa, papel_dono))
        vistos.add(empresa['cliente_id'])

    # Todas as partes, na mesma ordem de precedência do papel_do_cliente.
    for campo in ('tomador_cnpj', 'rem_cnpj', 'dest_cnpj', 'exped_cnpj',
                  'receb_cnpj', 'emit_cnpj'):
        alvo = cli_index.get(_digitos(header.get(campo)))
        if not alvo or alvo['cliente_id'] in vistos:
            continue
        destinos.append((alvo, papel_do_cliente(header, alvo['cnpj'])))
        vistos.add(alvo['cliente_id'])

    # Caso 3: ninguém reconhecido — fica com quem consultou.
    if not destinos:
        destinos.append((empresa, 'outro'))
    return destinos


# ==========================================================================
# Gravação de UM documento
# ==========================================================================
def _importar_cte(empresa, xml_bytes, nsu=None):
    """CT-e completo → ``cte_documentos``/``cte_nfe`` + XML arquivado no Dropbox
    (padrão IMPORTADOS/{ano}/{empresa}/{mês}, o MESMO do fluxo Dropbox).

    Devolve (resultado, n_nfes) com resultado in {'ok','upd','dup'}. Levanta em
    falha de parse/gravação — o chamador para o lote e não avança o cursor além
    deste documento.
    """
    xml_str = xml_bytes.decode('utf-8', 'replace')
    parsed = parse_cte_xml(xml_str)                  # pode levantar ValueError
    chave = parsed['chave']
    header = parsed['header']
    data_emissao = header.get('data_emissao')

    # QUEM é o dono deste CT-e — pode não ser quem consultou. Ver _destinos_do_cte.
    destinos = _destinos_do_cte(header, empresa,
                                empresa.get('cli_index') or _index_clientes())
    if len(destinos) > 1 or destinos[0][0]['cliente_id'] != empresa['cliente_id']:
        logger.info('[cte] chave=%s roteado para %s', chave,
                    [(d['cliente_id'], p) for d, p in destinos])

    res = None
    for alvo, papel in destinos:
        # A pasta é a do DONO da linha, não a de quem consultou: o XML da HPA tem
        # de ficar no arquivo fiscal da HPA.
        caminho = f"{_pasta_dfe(alvo, data_emissao, 'CTE')}/{chave}.xml"
        try:
            # xml_raw + xml_caminho: cinto E suspensório. O XML vai para o BANCO (o
            # olhinho/DACTE/zip preferem xml_raw e param de depender do arquivo — foi
            # o que quebrou 4.542 CT-e quando as pastas mudaram) e o caminho continua
            # gravado como ponteiro do arquivo arquivado. _save_cte trunca em 16 MB e
            # o UPDATE usa COALESCE, então reprocessar nunca apaga o que já existe.
            r = _save_cte(
                parsed, f'{chave}.xml', 'SEFAZ',
                cliente_id=alvo['cliente_id'], xml_raw=xml_str,
                xml_caminho=caminho, nsu=nsu,
                papel_cliente=papel, cnpj_cliente=alvo.get('cnpj'),
            )
        except Exception:
            # Gravação falhou → arquiva em ERROS (best-effort) e propaga (para o lote).
            try:
                _subir_xml(f"{_pasta_dfe(alvo, data_emissao, 'ERROS')}/{chave}.xml",
                           xml_bytes)
            except Exception:
                pass
            raise
        # Sucesso no banco → sobe o XML. Falha aqui aborta o doc; o retry re-grava
        # (o _save_cte vira 'upd', idempotente) e re-sobe.
        _subir_xml(caminho, xml_bytes)
        # O resultado que conta para a estatística é o do PRIMEIRO destino: os
        # demais são cópias do mesmo documento para outros clientes, e contá-las
        # inflaria o "n_cte" da rodada.
        if res is None:
            res = r
    return res, len(parsed.get('nfes') or [])


def _extrair_evento_cte(root):
    """Metadados mínimos de um procEventoCTe: Id, chCTe, tpEvento, nSeq, data."""
    inf = _find(root, 'infEvento')
    idv = (inf.get('Id') or inf.get('id') or '') if inf is not None else ''
    ch_cte = _digitos(_text(inf, 'chCTe')) if inf is not None else None
    ch_cte = ch_cte if (ch_cte and len(ch_cte) == 44) else None
    tp_evento = _text(inf, 'tpEvento') if inf is not None else None
    n_seq = _to_int(_text(inf, 'nSeqEvento')) if inf is not None else None
    _dh, ano, mes = _parse_dh(_text(inf, 'dhEvento') if inf is not None else None)

    # cStat da RESPOSTA da SEFAZ — quem decide se o cancelamento vale.
    #
    # CUIDADO: um procEventoCTe tem DOIS infEvento, o do PEDIDO e o da RESPOSTA,
    # e o _find devolve o PRIMEIRO. Procurar cStat na raiz pega o bloco do
    # pedido, que não tem cStat, e o valor viria vazio — o que faria a guarda
    # parar de cancelar TUDO. Por isso desce-se no retEventoCTe antes.
    ret = _find(root, 'retEventoCTe')
    c_stat = _text(ret, 'cStat') if ret is not None else None

    chave_evento = (idv or '').strip() or None
    if not chave_evento:
        if tp_evento and ch_cte and n_seq is not None:
            chave_evento = f'ID{tp_evento}{ch_cte}{str(n_seq).zfill(2)}'
        else:
            raise ValueError('evento de CT-e sem Id/chave identificável')
    return {'chave_evento': chave_evento[:60], 'ch_cte': ch_cte,
            'tp_evento': tp_evento, 'ano': ano, 'mes': mes, 'c_stat': c_stat}


def _gravar_evento(empresa, ev, xml_bytes):
    """procEventoCTe → arquiva o XML; se for CANCELAMENTO, marca o CT-e.
    Demais eventos (CC-e de CT-e, comprovante de entrega, EPEC…) ficam só no
    Dropbox: são histórico, não mudam a linha da conferência."""
    ano = ev['ano'] or _hoje_brt().year
    mes = ev['mes'] or _hoje_brt().month
    # Arquiva pelo Id do evento (não pela chCTe, para não colidir com o CT-e), na
    # convenção única com SENTIDO=EVENTOS (mesma pasta do roteador e da NF-e).
    _subir_xml(_caminho_fiscal(empresa, ano, mes, ev['chave_evento'], 'EVENTOS'),
               xml_bytes)
    if ev['tp_evento'] == TP_CANCELAMENTO_CTE and ev['ch_cte']:
        # GUARDA DE STATUS: só cancela se a SEFAZ tiver ACEITADO o pedido.
        # Antes daqui não saía guarda nenhuma — bastava o tpEvento ser 110111
        # para o CT-e ser marcado, e um pedido REJEITADO (573 duplicidade, 594
        # prazo, ...) cancelava um CT-e que segue ATIVO na SEFAZ. Em frete isso
        # vira crédito de ICMS que não deveria existir.
        c_stat = ev.get('c_stat')
        if c_stat in CSTAT_CANCELAMENTO_CTE_OK:
            marcar_cte_cancelado(ev['ch_cte'])
        else:
            # Este log é também o INSTRUMENTO que resolve a dúvida do 155 no
            # CT-e: cada cStat inesperado aparece aqui com a chave, e a
            # distribuição real se revela sem precisar varrer o Dropbox.
            logger.warning(
                '[cte] cancelamento do CT-e %s NAO aceito pela SEFAZ '
                '(cStat=%s) — CT-e segue ATIVO. Evento %s arquivado.',
                ev['ch_cte'], c_stat or '?', ev['chave_evento'])


def processar_um_doc(empresa, d):
    """Processa e SALVA um docZip. LEVANTA se falhar ao descompactar/parsear OU ao
    gravar — nesse caso o chamador NÃO avança o cursor além deste NSU.

    Devolve (kind, n_nfes) com kind in {cte, cte_dup, evento, outro}.
    """
    xml_bytes = gzip.decompress(base64.b64decode(d.text or ''))
    root = ET.fromstring(xml_bytes)
    raiz = _local(root.tag)
    nsu = _to_int(d.get('NSU'))

    if raiz in _RAIZ_CTE:
        res, n_nfes = _importar_cte(empresa, xml_bytes, nsu=nsu)
        return ('cte_dup' if res == 'dup' else 'cte'), n_nfes

    if raiz in _RAIZ_EVENTO:
        _gravar_evento(empresa, _extrair_evento_cte(root), xml_bytes)
        return 'evento', 0

    # resEvento / schema desconhecido: não trava a fila de NSU; conta e segue.
    return 'outro', 0


def _peek(d):
    """Descompacta o docZip só para o dry-run (tag raiz + chave), sem gravar."""
    try:
        xml_bytes = gzip.decompress(base64.b64decode(d.text or ''))
        root = ET.fromstring(xml_bytes)
        chave = None
        inf = _find(root, 'infCte')
        if inf is not None:
            ch = _digitos(inf.get('Id') or '')
            chave = ch[-44:] if len(ch) >= 44 else None
        if not chave:
            for e in root.iter():
                if _local(e.tag) in ('chCTe', 'chNFe', 'chDFe') and e.text:
                    chave = e.text.strip()
                    break
        return _local(root.tag), chave
    except Exception:
        return None, None


def _processar_lote(empresa, docs, nsu_inicial):
    """Processa UM lote de docZip em ordem de NSU. Avança nsu_ok só APÓS cada doc
    salvo (retoma de onde parou se cair). Para no 1º doc que falhar."""
    r = {'n_cte': 0, 'n_dup': 0, 'n_evento': 0, 'n_outro': 0, 'n_nfes': 0,
         'nsu_ok': nsu_inicial, 'parada': None}
    for d in docs:
        nsu = _to_int(d.get('NSU'))
        try:
            kind, n_nfes = processar_um_doc(empresa, d)
        except Exception as exc:
            r['parada'] = f'NSU {nsu}: {exc}'
            break
        if kind == 'cte':
            r['n_cte'] += 1
            r['n_nfes'] += n_nfes
        elif kind == 'cte_dup':
            r['n_dup'] += 1
        elif kind == 'evento':
            r['n_evento'] += 1
        else:
            r['n_outro'] += 1
        r['nsu_ok'] = nsu   # só avança a marca APÓS salvar com sucesso
    return r


# ==========================================================================
# Preparação da sessão (certificado + UF + cota) — comum a captura e consNSU
# ==========================================================================
def _preparar(cliente_id, origem, dry_run=False, checar_cota=True):
    """Valida cliente/certificado/UF, monta a sessão mTLS e checa a cota.

    Devolve (ctx, None) em sucesso ou (None, resposta_de_erro) — a resposta tem o
    mesmo formato das rotas ({'ok': False, 'erro': ...}), com as MESMAS mensagens
    orientadas a ação do motor de NF-e.
    """
    # Choke point único: schema antes de tudo. Vale para o caminho MANUAL e para o
    # AGENDADO, e roda antes do download do .pfx e de qualquer requisição à SEFAZ.
    if not _schema_pronto():
        return None, _erro_schema()

    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        return None, {'ok': False, 'erro': 'Cliente não encontrado.'}

    numero = (cliente.get('numero_cliente') or '').strip() or None
    razao = cliente.get('nome_razao_social') or 'SEM_NOME'
    rotulo = f"{numero + ' - ' if numero else ''}{razao}"

    vinc = DfeCertificado.get_by_cliente(cliente_id)
    if not vinc or not vinc.get('dropbox_path'):
        return None, {'ok': False,
                      'erro': f'O cliente {rotulo} não tem certificado digital vinculado. '
                              f'Vincule o .pfx antes de capturar.'}
    # Interessado = CNPJ da PRÓPRIA empresa (importa quando o cert é da matriz e o
    # cliente é filial). Fallback: o CNPJ do certificado.
    cnpj = _digitos(cliente.get('cpf_cnpj')) or _digitos(vinc.get('cnpj'))

    uf = _uf_principal(cliente_id)
    try:
        cuf = cuf_autor(uf)
    except UfInvalidaError:
        return None, {'ok': False, 'sem_uf': True,
                      'erro': f'O cliente {rotulo} está sem UF no endereço principal. '
                              f'Cadastre o estado (UF) no endereço principal para ativar a captura.'}

    senha_cif = DfeCertificado.get_senha_cifrada(cliente_id)
    if not senha_cif:
        return None, {'ok': False,
                      'erro': f'Certificado do cliente {rotulo} sem senha armazenada.'}
    try:
        senha = decifrar_senha(senha_cif)
    except CertificadoError as exc:
        return None, {'ok': False, 'erro': f'Falha ao decifrar a senha do certificado: {exc}'}

    pfx_bytes = dropbox_sync._service.download_file(vinc['dropbox_path'])
    if not pfx_bytes:
        return None, {'ok': False, 'erro': 'Falha ao baixar o .pfx do Dropbox.'}
    try:
        chave_priv, cert, cadeia = carregar_par_chave_cert(pfx_bytes, senha)
    except CertificadoError as exc:
        return None, {'ok': False, 'erro': f'Falha ao abrir o certificado: {exc}'}

    if checar_cota:
        bloqueio = _bloqueado_por_cota(cliente_id)
        if bloqueio:
            faltam = bloqueio.get('faltam_min')
            if not dry_run:
                dfe_log.registrar('pulado_cota', cliente_id, cnpj,
                                  detalhe=f'faltam ~{faltam} min', origem=origem,
                                  servico='cte')
            return None, {'ok': False, 'bloqueado': True, 'faltam_min': faltam,
                          'erro': 'A SEFAZ pediu para aguardar (consumo indevido anterior '
                                  'na distribuição de CT-e). '
                                  f'Faltam ~{faltam} min para a próxima consulta deste CNPJ.'}

    ctx = {
        'sess': montar_sessao_mtls(cert, chave_priv, cadeia),
        'cnpj': cnpj, 'cuf': cuf, 'rotulo': rotulo,
        'validade': vinc.get('validade'),   # p/ traduzir um 403 em 'cert vencido'
        # cli_index vai no contexto para ser montado UMA vez por rodada, e não
        # uma vez por documento — a rodada pode ter centenas de CT-e.
        'empresa': {'cliente_id': cliente_id, 'numero': numero, 'razao': razao,
                    'cnpj': cnpj, 'uf': uf, 'cli_index': _index_clientes()},
    }
    return ctx, None


# ==========================================================================
# Orquestração — captura de UMA rodada para 1 cliente (drena em multi-lote)
# ==========================================================================
def capturar_cte_cliente(cliente_id, dry_run=False, origem='manual',
                         max_lotes=1, max_seg=0):
    """Consulta o CTeDistribuicaoDFe e (fora do dry-run) grava os CT-e.

    Drenagem: enquanto ``cStat==138`` e ``ultNSU<maxNSU``, consulta lotes em
    sequência (até ``max_lotes`` ou ``max_seg``), parando em 137 (fim) ou 656
    (cota → cooldown 1h). ``max_lotes=1`` (default) = 1 lote, usado no caminho
    MANUAL; o cron passa os tetos de drenagem. NUNCA manifesta.
    """
    ctx, erro = _preparar(cliente_id, origem, dry_run=dry_run)
    if erro:
        return erro

    empresa, cnpj, cuf, sess = ctx['empresa'], ctx['cnpj'], ctx['cuf'], ctx['sess']
    ult_nsu = _ler_ult_nsu(cliente_id)

    try:
        ret, _fmt = consultar(sess, cnpj, cuf, ult_nsu)
    except RuntimeError as exc:
        if not dry_run:
            # 403 + cert vencido → 'Certificado vencido em DD/MM/AAAA' (mesma
            # tradução da NF-e; o cursor de CT-e é próprio mas o cert é o mesmo).
            dfe_log.registrar('erro', cliente_id, cnpj, ult_nsu,
                              detalhe=_detalhe_403(exc, ctx['validade']),
                              origem=origem, servico='cte')
        return {'ok': False, 'erro': f'Falha ao consultar a SEFAZ (CT-e): {exc}'}

    cStat = _text(ret, 'cStat')
    xMotivo = _text(ret, 'xMotivo')
    ret_ult = _to_int(_text(ret, 'ultNSU')) or 0
    ret_max = _to_int(_text(ret, 'maxNSU')) or 0
    status_txt = f'{cStat} {xMotivo}'[:255]

    lote = _find(ret, 'loteDistDFeInt')
    docs = [e for e in (lote.iter() if lote is not None else []) if _local(e.tag) == 'docZip']
    docs.sort(key=lambda e: _to_int(e.get('NSU')) or 0)

    base = {'ok': True, 'servico': 'cte', 'cStat': cStat, 'xMotivo': xMotivo,
            'cnpj': cnpj, 'cliente': ctx['rotulo'], 'ult_nsu': ult_nsu,
            'ret_ult_nsu': ret_ult, 'max_nsu': ret_max, 'docs': len(docs),
            'faltam': max(0, ret_max - ret_ult)}

    # 656 = consumo indevido: registra o cooldown SEMPRE — inclusive no dry-run,
    # porque a cota foi consumida de verdade do lado da SEFAZ. Mantém o cursor.
    if cStat == '656':
        execute_query(SQL_NSU_656, (cliente_id, cnpj, ult_nsu, ret_max, status_txt),
                      fetch=False)
        dfe_log.registrar('consulta', cliente_id, cnpj, ult_nsu, cStat, xMotivo,
                          ret_ult, ret_max, len(docs),
                          detalhe='dry-run' if dry_run else None, origem=origem,
                          servico='cte')
        base.update({'consumo_indevido': True, 'dry_run': dry_run,
                     'mensagem': 'A SEFAZ pediu para aguardar ~1h (consumo indevido na '
                                 f'distribuição de CT-e). Ela informou ultNSU={ret_ult}. '
                                 'O cursor local foi mantido — a próxima consulta retoma daqui.'})
        return base

    # DRY-RUN (fora do 656): só lista o que viria. Não grava, não sobe, não avança.
    if dry_run:
        preview = []
        for d in docs:
            raiz, chave = _peek(d)
            preview.append({'nsu': _to_int(d.get('NSU')), 'schema': d.get('schema'),
                            'raiz': raiz, 'chave': chave})
        base.update({'dry_run': True, 'preview': preview})
        return base

    tot = {'n_cte': 0, 'n_dup': 0, 'n_evento': 0, 'n_outro': 0, 'n_nfes': 0}
    nsu_ok = ult_nsu
    parada = None
    limite = None
    n_lotes = 0
    n_vazias = 0          # janelas 137 encadeadas neste ciclo (teto próprio)
    inicio = time.monotonic()

    while True:
        n_lotes += 1
        rl = _processar_lote(empresa, docs, nsu_ok)
        for k in tot:
            tot[k] += rl[k]
        nsu_ok = rl['nsu_ok']
        parada = rl['parada']

        # 137 com ultNSU À FRENTE: JANELA VAZIA, e o avanço tem de ser anotado.
        # Mesma correção do dfe_captura.py — ver o comentário longo lá. Em
        # resumo: 137 com ultNSU > o enviado é a SEFAZ mandando pedir a partir
        # dali; sem anotar, o cursor revarre a mesma faixa para sempre. O caso
        # de prova deste arquivo é a 547 (CT-e preso em 0 -> 50/490, 145
        # consultas) e o lado CT-e da 152 (0 -> 50/304).
        #
        # Só avança com o lote LIMPO (sem parada): pular NSU não processado
        # perderia documento em silêncio.
        if cStat == '137' and parada is None and ret_ult > nsu_ok:
            nsu_ok = ret_ult

        # Grava e AVANÇA o cursor ANTES do próximo lote (cai no meio → retoma
        # daqui, não reprocessa).
        execute_query(SQL_NSU_OK, (cliente_id, cnpj, nsu_ok, ret_max, status_txt),
                      fetch=False)

        if parada:
            break

        # ENCADEIA JANELA VAZIA NO MESMO CICLO. Mesma lógica do dfe_captura.py —
        # ver o comentário longo lá. 137 com ret_ult < ret_max não é fim de fila:
        # é "esta faixa está vazia, continue". Fim de fila (ret_ult >= ret_max),
        # 138, 656 e erro seguem exatamente como hoje.
        vazia = (cStat == '137' and ret_ult < ret_max)
        if vazia:
            n_vazias += 1
        if cStat not in ('137', '138') or ret_ult >= ret_max:
            break                                    # fim de fila / status inesperado
        if n_lotes >= max_lotes:
            limite = f'teto de {max_lotes} lote(s) na rodada'
            break
        if max_seg and (time.monotonic() - inicio) >= max_seg:
            limite = f'teto de {max_seg}s na rodada'
            break
        if n_vazias >= _MAX_JANELAS_VAZIAS:
            limite = f'teto de {_MAX_JANELAS_VAZIAS} janela(s) vazia(s) na rodada'
            break

        time.sleep(_INTERVALO_LOTE)                  # cortesia entre lotes
        try:
            ret, _fmt = consultar(sess, cnpj, cuf, nsu_ok)
        except RuntimeError as exc:
            parada = f'lote {n_lotes + 1}: {exc}'
            break
        cStat = _text(ret, 'cStat')
        xMotivo = _text(ret, 'xMotivo')
        ret_ult = _to_int(_text(ret, 'ultNSU')) or 0
        ret_max = _to_int(_text(ret, 'maxNSU')) or 0
        status_txt = f'{cStat} {xMotivo}'[:255]
        if cStat == '656':
            # 656 no meio da drenagem: cursor mantido em nsu_ok, cooldown 1h.
            execute_query(SQL_NSU_656, (cliente_id, cnpj, nsu_ok, ret_max, status_txt),
                          fetch=False)
            parada = '656 (consumo indevido) na distribuição de CT-e'
            break
        lote = _find(ret, 'loteDistDFeInt')
        docs = [e for e in (lote.iter() if lote is not None else []) if _local(e.tag) == 'docZip']
        docs.sort(key=lambda e: _to_int(e.get('NSU')) or 0)

    tot_docs = tot['n_cte'] + tot['n_dup'] + tot['n_evento'] + tot['n_outro']
    dfe_log.registrar('consulta', cliente_id, cnpj, ult_nsu, cStat, xMotivo,
                      ret_ult, ret_max, tot_docs, tot['n_cte'], tot['n_evento'],
                      detalhe=(parada or limite), origem=origem, servico='cte')

    base.update({'ctes': tot['n_cte'], 'preservados': tot['n_dup'],
                 'eventos': tot['n_evento'], 'outros': tot['n_outro'],
                 'nfes_vinculadas': tot['n_nfes'], 'docs': tot_docs,
                 'lotes': n_lotes, 'novo_ult_nsu': nsu_ok, 'max_nsu': ret_max,
                 'faltam': max(0, ret_max - nsu_ok),
                 'limite_atingido': limite, 'parada': parada})
    return base


def buscar_nsu(cliente_id, nsu, origem='manual'):
    """Recupera UM NSU específico via consNSU (o análogo do consChNFe da NF-e).

    Serve para o buraco na fila: um NSU que falhou no processamento e ficou para
    trás, ou que a SEFAZ reporta mas não veio no lote. NÃO mexe no cursor — é
    pontual, consciente, e consome 1 requisição da cota.
    """
    ctx, erro = _preparar(cliente_id, origem)
    if erro:
        return erro
    try:
        nsu = int(nsu)
    except (TypeError, ValueError):
        return {'ok': False, 'erro': 'NSU inválido (informe um número).'}

    try:
        ret = consultar_nsu(ctx['sess'], ctx['cnpj'], ctx['cuf'], nsu)
    except RuntimeError as exc:
        return {'ok': False, 'erro': f'Falha ao consultar a SEFAZ (CT-e): {exc}'}

    cStat = _text(ret, 'cStat')
    xMotivo = _text(ret, 'xMotivo')
    if cStat == '656':
        execute_query(SQL_NSU_656,
                      (cliente_id, ctx['cnpj'], _ler_ult_nsu(cliente_id), 0,
                       f'{cStat} {xMotivo}'[:255]), fetch=False)
        return {'ok': False, 'consumo_indevido': True, 'cStat': cStat,
                'erro': 'A SEFAZ pediu para aguardar ~1h (consumo indevido).'}

    lote = _find(ret, 'loteDistDFeInt')
    docs = [e for e in (lote.iter() if lote is not None else []) if _local(e.tag) == 'docZip']
    res = {'n_cte': 0, 'n_dup': 0, 'n_evento': 0, 'n_outro': 0, 'n_nfes': 0}
    erros = []
    for d in docs:
        try:
            kind, n_nfes = processar_um_doc(ctx['empresa'], d)
        except Exception as exc:
            erros.append(str(exc))
            continue
        if kind == 'cte':
            res['n_cte'] += 1
            res['n_nfes'] += n_nfes
        elif kind == 'cte_dup':
            res['n_dup'] += 1
        elif kind == 'evento':
            res['n_evento'] += 1
        else:
            res['n_outro'] += 1

    dfe_log.registrar('cons_nsu', cliente_id, ctx['cnpj'], nsu, cStat, xMotivo,
                      docs=len(docs), notas=res['n_cte'], eventos=res['n_evento'],
                      detalhe='; '.join(erros)[:300] or None, origem=origem,
                      servico='cte')
    return {'ok': True, 'servico': 'cte', 'nsu': nsu, 'cStat': cStat,
            'xMotivo': xMotivo, 'docs': len(docs), 'ctes': res['n_cte'],
            'preservados': res['n_dup'], 'eventos': res['n_evento'],
            'outros': res['n_outro'], 'nfes_vinculadas': res['n_nfes'],
            'erros': erros}


def seed_ult_nsu_cte(cliente_id, nsu, usuario_label='?'):
    """Semeia MANUALMENTE o cursor de NSU de CT-e de uma empresa.

    Mesmo caso de uso (e mesmas ressalvas) do ``dfe_captura.seed_ult_nsu``: o CNPJ
    já foi consumido por outro sistema até um NSU alto e a SEFAZ devolve 656
    mandando usar o ultNSU que ela informou. Começar desse NSU **PULA** os
    documentos anteriores — quem precisa deles obtém por outro meio. Por isso é
    explícito, com confirmação e rastro em ``dfe_consulta_log``.
    """
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        return {'ok': False, 'erro': 'Cliente não encontrado.'}
    try:
        nsu = int(nsu)
    except (TypeError, ValueError):
        return {'ok': False, 'erro': 'NSU inválido (informe um número).'}
    if nsu < 0:
        return {'ok': False, 'erro': 'NSU deve ser um número não negativo.'}

    cnpj = _digitos(cliente.get('cpf_cnpj'))
    if not cnpj:
        return {'ok': False, 'erro': 'Cliente sem CNPJ para semear o cursor de NSU.'}

    numero = (cliente.get('numero_cliente') or '').strip() or None
    razao = cliente.get('nome_razao_social') or 'SEM_NOME'
    rotulo = f"{numero + ' - ' if numero else ''}{razao}"

    if not _schema_pronto():
        return _erro_schema()

    # O retorno do execute_query é VERIFICADO: ele engole o erro do driver e devolve
    # None. Sem esta checagem, um INSERT que falhou (tabela ausente, FK de
    # cliente_id) devolveria "cursor definido em N" com nada gravado — e a captura
    # seguiria do zero, com o operador achando que semeou.
    if execute_query(
        "INSERT INTO dfe_nsu_cte "
        "(cliente_id, cnpj, ult_nsu, max_nsu, ult_consulta, proximo_permitido, ult_status) "
        "VALUES (%s,%s,%s,0,NOW(),NULL,%s) "
        "ON DUPLICATE KEY UPDATE ult_nsu=VALUES(ult_nsu), "
        "  proximo_permitido=NULL, ult_status=VALUES(ult_status)",
        (cliente_id, cnpj, nsu, f'seed manual (CT-e) por {usuario_label}: ultNSU={nsu}'[:255]),
        fetch=False,
    ) is None:
        from utils.db_helper import get_last_db_error
        erro = get_last_db_error() or 'sem detalhe do driver'
        logger.error('[cte] seed do cursor falhou (cliente_id=%s, nsu=%s): %s',
                     cliente_id, nsu, erro)
        return {'ok': False,
                'erro': f'Falha ao gravar o cursor de NSU de CT-e no banco: {erro}'}

    # Confirmação por LEITURA: só respondemos "definido em N" depois de ver o valor
    # persistido (o upsert pode não ter afetado linha por outro motivo).
    conf = execute_query(
        "SELECT ult_nsu FROM dfe_nsu_cte WHERE cliente_id = %s",
        (cliente_id,), fetch=True, fetch_one=True,
    )
    if not conf or int(conf.get('ult_nsu') or -1) != nsu:
        gravado = conf.get('ult_nsu') if conf else None
        return {'ok': False,
                'erro': f'O cursor de CT-e não ficou com o valor esperado '
                        f'(esperado {nsu}, no banco {gravado!r}). Nada foi assumido.'}

    dfe_log.registrar('seed_manual', cliente_id, cnpj, ult_nsu_env=nsu,
                      detalhe=f'seed manual de CT-e por {usuario_label}: ult_nsu={nsu} '
                              f'(pula documentos anteriores a esse NSU)',
                      servico='cte')
    return {'ok': True, 'servico': 'cte', 'nsu': nsu, 'cliente': rotulo,
            'mensagem': f'Cursor de NSU de CT-e do cliente {rotulo} definido em {nsu}. '
                        f'A próxima captura começa a partir daí (CT-e anteriores não '
                        f'serão capturados).'}


# ==========================================================================
# Rodada agendada (cron)
# ==========================================================================
def capturar_cte_agendado():
    """Rodada agendada de CT-e: as empresas com captura ligada, uma por vez, na
    ordem 'mais atrasada primeiro', respeitando o cooldown do 656 e um prazo suave.
    O que não deu tempo vai no próximo tick.

    Protegida por GET_LOCK('dfe_captura_cte', 0) — lock PRÓPRIO, então a rodada de
    CT-e não disputa com a de NF-e. Loga cada empresa em dfe_consulta_log com
    servico='cte'. NUNCA manifesta.

    Só roda com CTE_SCHED_ATIVO=1 (defesa; o cron decide se chama).
    """
    logger.warning('[cte-sched] >>> capturar_cte_agendado INVOCADO (pid=%s).', os.getpid())
    if os.getenv('CTE_SCHED_ATIVO', '0').strip() != '1':
        logger.warning('[cte-sched] ABORTADO: CTE_SCHED_ATIVO != 1.')
        return

    # Schema antes do lock e antes de qualquer empresa: se as tabelas ainda não
    # existem (Cron subiu antes do web migrar), aborta a rodada INTEIRA sem gastar
    # cota. O próximo tick pega o banco já migrado.
    if not _schema_pronto():
        dfe_log.registrar('pulado_schema', origem='agendado', servico='cte',
                          detalhe='tabelas de CT-e ausentes; aguardando a migration')
        logger.warning('[cte-sched] ABORTADO: tabelas de CT-e ainda não existem '
                       '(%s). O boot do web cria; próximo tick tenta de novo.',
                       ', '.join(_TABELAS_EXIGIDAS))
        return

    lock_conn = None
    got_lock = False
    try:
        lock_conn = _conectar_lock()
        cur = lock_conn.cursor()
        cur.execute('SELECT GET_LOCK(%s, 0)', (_LOCK_CAPTURA,))
        got_lock = (cur.fetchone() or [0])[0] == 1
        cur.close()
        if not got_lock:
            dfe_log.registrar('pulado_lock', origem='agendado', servico='cte',
                              detalhe='outro worker/deploy já está capturando CT-e')
            logger.info('[cte-sched] lock ocupado — pulando este tick.')
            return

        empresas = DfeCertificado.listar_para_captura()
        if not empresas:
            logger.info('[cte-sched] nenhuma empresa com captura ligada.')
            return

        prazo = time.monotonic() + _PRAZO_SUAVE_SEG
        n_proc = n_bloq = n_semuf = n_erro = 0
        tot_ctes = tot_docs = 0
        for emp in empresas:
            if time.monotonic() > prazo:
                logger.info('[cte-sched] prazo suave (%ds) atingido; resto no próximo tick.',
                            _PRAZO_SUAVE_SEG)
                break
            cid = emp['cliente_id']
            # Pré-check de cooldown (SQL barato): pula quem está em espera sem
            # baixar o .pfx nem montar o mTLS.
            if _bloqueado_por_cota(cid):
                n_bloq += 1
                continue
            try:
                r = capturar_cte_cliente(cid, dry_run=False, origem='agendado',
                                         max_lotes=_MAX_LOTES_CICLO,
                                         max_seg=_MAX_SEG_EMPRESA)
            except Exception as exc:
                n_erro += 1
                logger.exception('[cte-sched] erro no cliente_id=%s', cid)
                dfe_log.registrar('erro', cid, _digitos(emp.get('cnpj')),
                                  origem='agendado', servico='cte',
                                  detalhe=str(exc)[:300])
                continue
            if not r.get('ok'):
                if r.get('bloqueado'):
                    n_bloq += 1
                elif r.get('sem_uf'):
                    n_semuf += 1
                    logger.info('[cte-sched] cliente_id=%s pulado: sem UF no endereço.', cid)
                else:
                    n_erro += 1
                continue
            if r.get('consumo_indevido'):
                n_bloq += 1
                continue
            n_proc += 1
            tot_ctes += r.get('ctes', 0) or 0
            tot_docs += r.get('docs', 0) or 0

        total = len(empresas)
        if total > 0 and n_bloq == total:
            dfe_log.registrar('pulado_cota', origem='agendado', servico='cte',
                              detalhe=f'todas as {total} empresas em cooldown (CT-e)')
            logger.info('[cte-sched] todas as %d empresas em cooldown — ciclo pulado.', total)
        logger.info('[cte-sched] rodada: %d empresa(s) | proc=%d bloq=%d semUF=%d erro=%d '
                    '| ctes=%d docs=%d', total, n_proc, n_bloq, n_semuf, n_erro,
                    tot_ctes, tot_docs)
    except Exception:
        logger.exception('[cte-sched] falha inesperada na rodada agendada.')
    finally:
        if lock_conn is not None:
            try:
                if got_lock:
                    _c = lock_conn.cursor()
                    _c.execute('SELECT RELEASE_LOCK(%s)', (_LOCK_CAPTURA,))
                    _c.fetchall()
                    _c.close()
            except Exception:
                pass
            try:
                lock_conn.close()
            except Exception:
                pass
