# -*- coding: utf-8 -*-
"""
Motor de captura de NF-e via distDFeInt (lift-and-shift do nh-transportes).

SÓ CONSULTA E SALVA. **NUNCA MANIFESTA** (só distribuição por NSU).
Escopo desta fase: NF-e (nfeProc / resNFe / procEventoNFe). CT-e (cteProc/resCTe)
é reconhecido mas NÃO gravado ainda (fase futura) — é contado como 'outro' para
não travar a fila de NSU. Não filtra por modelo (55/65): grava tudo o que vier,
preenchendo a coluna ``modelo``.

Cuidados de fuso:
  (1) proximo_permitido (cota do 656) é comparado SEMPRE em SQL (NOW()), nunca
      contra datetime.now() do Python.
  (2) o ano/mês da pasta Fiscal e o dh_emissao saem do dhEmi da NOTA convertido
      para -03:00 (não da hora da captura) — ver _parse_dh.

Índice no banco (dfe_documentos/itens/eventos), arquivo (XML) no Dropbox.
"""
import base64
import gzip
import logging
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import mysql.connector

from config import Config
from utils.db_helper import execute_query
from utils import dropbox_sync
from utils.certificado_digital import (
    carregar_par_chave_cert, decifrar_senha, CertificadoError,
)
from models.cliente import Cliente
from models.endereco_cliente import EnderecoCliente
from models.dfe_certificado import DfeCertificado
from utils.integrations.dfe_sefaz import (
    montar_sessao_mtls, consultar, cuf_autor, UfInvalidaError,
    _find, _text, _local,
)
from utils.integrations import dfe_log

logger = logging.getLogger(__name__)

# Fuso de Brasília (offset fixo; Brasil sem horário de verão desde 2019).
_TZ_BR = timezone(timedelta(hours=-3))

DIAS_RETENCAO = 90          # janela até xml_expira_em (expurgo em lote na fase futura)
TP_CANCELAMENTO = "110111"  # tpEvento de cancelamento de NF-e

# Scheduler (Fase 3): lock global + prazo suave por rodada.
_LOCK_CAPTURA = 'dfe_captura'
_PRAZO_SUAVE_SEG = int(os.getenv('DFE_SCHED_PRAZO_SEG', '960'))  # ~16 min


# ==========================================================================
# SQLs — copiados verbatim do motor NH (schema alinhado na Fase 1).
# ==========================================================================
SQL_DOC_UPSERT = (
    "INSERT INTO dfe_documentos "
    "(cliente_id, chave, tipo, nsu, schema_dfe, resumo, numero, serie, modelo, "
    " dh_emissao, emit_cnpj, emit_nome, dest_cnpj, valor_total, situacao, "
    " xml_caminho, xml_expira_em) "
    "VALUES (%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  nsu=VALUES(nsu), schema_dfe=VALUES(schema_dfe), numero=VALUES(numero), "
    "  serie=VALUES(serie), modelo=VALUES(modelo), dh_emissao=VALUES(dh_emissao), "
    "  emit_cnpj=VALUES(emit_cnpj), emit_nome=VALUES(emit_nome), "
    "  dest_cnpj=VALUES(dest_cnpj), valor_total=VALUES(valor_total), "
    "  xml_caminho=VALUES(xml_caminho), xml_expira_em=VALUES(xml_expira_em), "
    "  resumo=VALUES(resumo)"   # nota COMPLETA chegou: se a linha era resumo(1), vira 0
    # NÃO mexe em situacao no UPDATE: preserva 'cancelada' setada por evento.
)

# resNFe: grava resumo=1. Se a chave JÁ existe (nota completa ou outro resumo),
# NÃO sobrescreve nada — completa vale mais que resumo (nunca rebaixa).
SQL_RESUMO_UPSERT = (
    "INSERT INTO dfe_documentos "
    "(cliente_id, chave, tipo, nsu, schema_dfe, resumo, numero, serie, modelo, "
    " dh_emissao, emit_cnpj, emit_nome, dest_cnpj, valor_total, situacao, "
    " xml_caminho, xml_expira_em) "
    "VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL) "
    "ON DUPLICATE KEY UPDATE chave=chave"   # no-op: nunca rebaixa nota completa
)

SQL_DOC_ID = "SELECT id FROM dfe_documentos WHERE chave = %s"

SQL_ITEM_UPSERT = (
    "INSERT INTO dfe_itens "
    "(documento_id, n_item, produto_xml, cprod_fornecedor, cean, cod_anp, "
    " produto_id, ncm, unidade, quantidade, valor_unitario, valor_total) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  produto_xml=VALUES(produto_xml), cprod_fornecedor=VALUES(cprod_fornecedor), "
    "  cean=VALUES(cean), cod_anp=VALUES(cod_anp), produto_id=VALUES(produto_id), "
    "  ncm=VALUES(ncm), unidade=VALUES(unidade), quantidade=VALUES(quantidade), "
    "  valor_unitario=VALUES(valor_unitario), valor_total=VALUES(valor_total)"
)

SQL_EVENTO_UPSERT = (
    "INSERT INTO dfe_eventos "
    "(cliente_id, chave_evento, ch_nfe, tp_evento, n_seq, descricao, dh_evento, "
    " nsu, schema_dfe, org_cnpj, xml_caminho, xml_expira_em) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  descricao=VALUES(descricao), dh_evento=VALUES(dh_evento), nsu=VALUES(nsu), "
    "  schema_dfe=VALUES(schema_dfe), org_cnpj=VALUES(org_cnpj), "
    "  xml_caminho=VALUES(xml_caminho), xml_expira_em=VALUES(xml_expira_em)"
)

SQL_CANCELA_NOTA = "UPDATE dfe_documentos SET situacao='cancelada' WHERE chave = %s"

# NSU: max_nsu=0 significa "a SEFAZ não informou" (o 656 não traz maxNSU), NUNCA
# "zero documentos" — o 0 preserva o valor atual; só um maxNSU real (>0) atualiza.
_MAX_NSU = "max_nsu=IF(VALUES(max_nsu) > 0, VALUES(max_nsu), max_nsu)"

SQL_NSU_OK = (
    "INSERT INTO dfe_nsu "
    "(cliente_id, cnpj, ult_nsu, max_nsu, ult_consulta, proximo_permitido, ult_status) "
    "VALUES (%s,%s,%s,%s,NOW(),NULL,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  ult_nsu=VALUES(ult_nsu), " + _MAX_NSU + ", "
    "  ult_consulta=NOW(), proximo_permitido=NULL, ult_status=VALUES(ult_status)"
)

SQL_NSU_656 = (
    "INSERT INTO dfe_nsu "
    "(cliente_id, cnpj, ult_nsu, max_nsu, ult_consulta, proximo_permitido, ult_status) "
    "VALUES (%s,%s,%s,%s,NOW(),NOW() + INTERVAL 1 HOUR,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  ult_nsu=VALUES(ult_nsu), " + _MAX_NSU + ", "
    "  ult_consulta=NOW(), proximo_permitido=NOW() + INTERVAL 1 HOUR, "
    "  ult_status=VALUES(ult_status)"
)


# ==========================================================================
# Helpers de parsing
# ==========================================================================
def _digitos(v):
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _to_int(v):
    d = _digitos(v)
    return int(d) if d else None


def _parse_dh(s):
    """'2026-07-22T09:30:00-03:00' -> ('2026-07-22 09:30:00', 2026, 7), CONVERTENDO
    o offset para Brasília (-03:00). Assim o dh_emissao grava em BRT e o ano/mês
    (pasta Fiscal) saem da NOTA convertida, não da hora da captura. Sem offset no
    XML, assume que já está em BRT."""
    if not s:
        return None, None, None
    txt = s.strip()
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        try:
            dt = datetime.strptime(txt[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None, None, None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_TZ_BR)
    return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.year, dt.month


def _chave_de_infnfe(infnfe_el):
    """Extrai a chave 44 do atributo Id (ex.: 'NFe5226...') do infNFe."""
    if infnfe_el is None:
        return None
    idv = infnfe_el.get("Id") or infnfe_el.get("id") or ""
    ch = _digitos(idv)
    return ch if len(ch) == 44 else (ch[-44:] if len(ch) > 44 else None)


def _hoje_brt():
    return datetime.now(_TZ_BR)


# ==========================================================================
# Extração (nfeProc / resNFe / procEventoNFe)
# ==========================================================================
def extrair_nota(root):
    infnfe = _find(root, "infNFe")
    chave = _chave_de_infnfe(infnfe)
    if not chave:
        chave = _digitos(_text(root, "chNFe"))
        chave = chave if len(chave) == 44 else None
    if not chave:
        raise ValueError("nota sem chave de 44 dígitos (infNFe/protNFe)")

    ide = _find(root, "ide")
    numero = _text(ide, "nNF") if ide is not None else None
    serie = _text(ide, "serie") if ide is not None else None
    modelo = _text(ide, "mod") if ide is not None else None
    dh_txt, ano, mes = _parse_dh(_text(ide, "dhEmi") if ide is not None else None)

    emit = _find(root, "emit")
    emit_cnpj = _digitos(_text(emit, "CNPJ")) if emit is not None else None
    emit_nome = _text(emit, "xNome") if emit is not None else None
    if emit_nome:
        emit_nome = emit_nome[:160]

    dest = _find(root, "dest")
    dest_cnpj = None
    if dest is not None:
        dest_cnpj = _digitos(_text(dest, "CNPJ") or _text(dest, "CPF")) or None

    total = _find(root, "ICMSTot")
    valor_total = _text(total, "vNF") if total is not None else _text(root, "vNF")

    prot = _find(root, "protNFe")
    cstat_prot = _text(prot, "cStat") if prot is not None else None
    situacao = "denegada" if cstat_prot in ("110", "301", "302", "303") else "autorizado"

    itens = []
    for det in root.iter():
        if _local(det.tag) != "det":
            continue
        prod = _find(det, "prod")
        if prod is None:
            continue
        itens.append({
            "n_item": _to_int(det.get("nItem")) or (len(itens) + 1),
            "produto_xml": (_text(prod, "xProd") or "")[:160] or None,
            "cprod_fornecedor": (_text(prod, "cProd") or "")[:60] or None,
            "cean": (_text(prod, "cEAN") or "")[:20] or None,
            "cod_anp": _text(prod, "cProdANP"),   # só combustível
            "ncm": _text(prod, "NCM"),
            "unidade": (_text(prod, "uCom") or "")[:6] or None,
            "quantidade": _text(prod, "qCom"),
            "valor_unitario": _text(prod, "vUnCom"),
            "valor_total": _text(prod, "vProd"),
        })

    return {
        "chave": chave, "tipo": "NFe", "numero": numero, "serie": serie,
        "modelo": modelo, "dh_txt": dh_txt, "ano": ano, "mes": mes,
        "emit_cnpj": emit_cnpj, "emit_nome": emit_nome, "dest_cnpj": dest_cnpj,
        "valor_total": valor_total, "situacao": situacao, "itens": itens,
    }


def extrair_evento(root):
    inf = _find(root, "infEvento")
    idv = (inf.get("Id") or inf.get("id") or "") if inf is not None else ""
    chave_evento = idv.strip() or None

    ch_nfe = _digitos(_text(inf, "chNFe")) if inf is not None else None
    ch_nfe = ch_nfe if (ch_nfe and len(ch_nfe) == 44) else None
    tp_evento = _text(inf, "tpEvento") if inf is not None else None
    n_seq = _to_int(_text(inf, "nSeqEvento")) if inf is not None else None
    org_cnpj = _digitos(_text(inf, "CNPJ")) if inf is not None else None
    dh_txt, ano, mes = _parse_dh(_text(inf, "dhEvento") if inf is not None else None)

    descricao = _text(root, "descEvento") or _text(root, "xEvento")
    if descricao:
        descricao = descricao[:160]

    if not chave_evento:
        if tp_evento and ch_nfe and n_seq is not None:
            chave_evento = f"ID{tp_evento}{ch_nfe}{str(n_seq).zfill(2)}"
        else:
            raise ValueError("evento sem Id/chave_evento identificável")

    return {
        "chave_evento": chave_evento[:60], "ch_nfe": ch_nfe, "tp_evento": tp_evento,
        "n_seq": n_seq, "org_cnpj": org_cnpj, "descricao": descricao,
        "dh_txt": dh_txt, "ano": ano, "mes": mes,
    }


def extrair_resumo_nota(root):
    chave = _digitos(_text(root, "chNFe"))
    if len(chave) != 44:
        raise ValueError("resNFe sem chNFe de 44 dígitos")
    # chave = cUF(2) AAMM(4) CNPJ(14) mod(2) serie(3) nNF(9) tpEmis(1) cNF(8) cDV(1)
    modelo = chave[20:22]
    serie = chave[22:25].lstrip("0") or "0"
    numero = chave[25:34].lstrip("0") or "0"

    dh_txt, ano, mes = _parse_dh(_text(root, "dhEmi"))
    emit_cnpj = _digitos(_text(root, "CNPJ") or _text(root, "CPF")) or None
    emit_nome = _text(root, "xNome")
    if emit_nome:
        emit_nome = emit_nome[:160]
    valor_total = _text(root, "vNF")

    csit = _text(root, "cSitNFe")
    situacao = {"1": "autorizado", "2": "denegada", "3": "cancelada"}.get(csit, "autorizado")

    return {
        "chave": chave, "tipo": "NFe", "numero": numero, "serie": serie,
        "modelo": modelo, "dh_txt": dh_txt, "ano": ano, "mes": mes,
        "emit_cnpj": emit_cnpj, "emit_nome": emit_nome,
        "valor_total": valor_total, "situacao": situacao,
    }


# ==========================================================================
# Dropbox: caminho Fiscal (ano/mês da nota) + upload
# ==========================================================================
def _caminho_fiscal(empresa, ano, mes, nome):
    svc = dropbox_sync._service
    pasta = svc.pasta_fiscal(empresa["razao"], ano, mes, empresa.get("numero"))
    return f"{pasta}/{nome}.xml"


def _subir_xml(caminho, xml_bytes):
    """Sobe o XML no Dropbox ANTES de gravar no banco; falha aqui aborta o doc
    (o chamador para o lote e não avança o cursor além dele)."""
    if not dropbox_sync._service.upload_bytes(caminho, xml_bytes):
        raise RuntimeError(f"falha ao subir o XML no Dropbox: {caminho}")


# ==========================================================================
# Gravação (transação por documento, na conexão dedicada)
# ==========================================================================
def gravar_nota(conn, cur, empresa, nota, xml_bytes, nsu, schema, expira):
    ano = nota["ano"] or _hoje_brt().year
    mes = nota["mes"] or _hoje_brt().month
    caminho = _caminho_fiscal(empresa, ano, mes, nota["chave"])
    _subir_xml(caminho, xml_bytes)

    cur.execute(SQL_DOC_UPSERT, (
        empresa["cliente_id"], nota["chave"], nota["tipo"], nsu, schema,
        nota["numero"], nota["serie"], nota["modelo"], nota["dh_txt"],
        nota["emit_cnpj"], nota["emit_nome"], nota["dest_cnpj"],
        nota["valor_total"], nota["situacao"], caminho, expira,
    ))
    cur.execute(SQL_DOC_ID, (nota["chave"],))
    row = cur.fetchone()
    documento_id = row["id"] if row else None
    if not documento_id:
        raise RuntimeError("não recuperou documento_id após upsert")

    n_itens = 0
    for it in nota["itens"]:
        cur.execute(SQL_ITEM_UPSERT, (
            documento_id, it["n_item"], it["produto_xml"],
            it["cprod_fornecedor"], it["cean"], it["cod_anp"],
            None, it["ncm"], it["unidade"], it["quantidade"],
            it["valor_unitario"], it["valor_total"],
        ))
        n_itens += 1

    conn.commit()
    return n_itens


def gravar_evento(conn, cur, empresa, ev, xml_bytes, nsu, schema, expira):
    ano = ev["ano"] or _hoje_brt().year
    mes = ev["mes"] or _hoje_brt().month
    # Arquiva pelo Id do evento (não pela chNFe, para não colidir com a nota).
    caminho = _caminho_fiscal(empresa, ano, mes, ev["chave_evento"])
    _subir_xml(caminho, xml_bytes)

    cur.execute(SQL_EVENTO_UPSERT, (
        empresa["cliente_id"], ev["chave_evento"], ev["ch_nfe"], ev["tp_evento"],
        ev["n_seq"], ev["descricao"], ev["dh_txt"], nsu, schema,
        ev["org_cnpj"], caminho, expira,
    ))
    if ev["tp_evento"] == TP_CANCELAMENTO and ev["ch_nfe"]:
        cur.execute(SQL_CANCELA_NOTA, (ev["ch_nfe"],))
    conn.commit()


def gravar_resumo_nota(conn, cur, empresa, res, nsu, schema):
    # dest = o próprio interessado (o resNFe não traz o destinatário).
    cur.execute(SQL_RESUMO_UPSERT, (
        empresa["cliente_id"], res["chave"], res["tipo"], nsu, schema,
        res["numero"], res["serie"], res["modelo"], res["dh_txt"],
        res["emit_cnpj"], res["emit_nome"], empresa["cnpj"],
        res["valor_total"], res["situacao"],
    ))
    conn.commit()


def processar_um_doc(conn, cur, empresa, d, expira):
    """Processa e SALVA um docZip. LEVANTA se falhar ao descompactar/parsear OU
    ao gravar o que DEVE ser gravado — nesse caso o chamador NÃO avança o cursor
    além deste NSU. Retorna (kind, n_itens) com kind in {nota, resumo, evento, outro}."""
    schema = d.get("schema") or None
    nsu = _to_int(d.get("NSU"))
    b64 = d.text or ""
    xml_bytes = gzip.decompress(base64.b64decode(b64))
    root = ET.fromstring(xml_bytes)
    raiz = _local(root.tag)

    if raiz == "nfeProc":
        nota = extrair_nota(root)
        ni = gravar_nota(conn, cur, empresa, nota, xml_bytes, nsu, schema, expira)
        return "nota", ni

    if raiz == "procEventoNFe":
        ev = extrair_evento(root)
        gravar_evento(conn, cur, empresa, ev, xml_bytes, nsu, schema, expira)
        return "evento", 0

    if raiz == "resNFe":
        res = extrair_resumo_nota(root)
        gravar_resumo_nota(conn, cur, empresa, res, nsu, schema)
        return "resumo", 0

    # cteProc / resCTe / resEvento / outros: NÃO modelados nesta fase (CT-e depois).
    # Não levanta (não trava a fila de NSU); conta e segue.
    return "outro", 0


# ==========================================================================
# Leituras de cota / cursor (comparação de tempo SEMPRE em SQL / NOW())
# ==========================================================================
def _bloqueado_por_cota(cliente_id):
    """Retorna a linha com faltam_min se a SEFAZ ainda pediu para aguardar
    (proximo_permitido > NOW()), ou None. Comparação 100% no relógio do banco."""
    return execute_query(
        "SELECT proximo_permitido, "
        "TIMESTAMPDIFF(MINUTE, NOW(), proximo_permitido) AS faltam_min "
        "FROM dfe_nsu WHERE cliente_id = %s "
        "AND proximo_permitido IS NOT NULL AND proximo_permitido > NOW()",
        (cliente_id,), fetch=True, fetch_one=True,
    )


def _ler_ult_nsu(cliente_id):
    row = execute_query(
        "SELECT ult_nsu FROM dfe_nsu WHERE cliente_id = %s",
        (cliente_id,), fetch=True, fetch_one=True,
    )
    return int(row["ult_nsu"]) if row and row.get("ult_nsu") is not None else 0


def _uf_principal(cliente_id):
    """Primeira UF (estado) não vazia entre os endereços (principal primeiro)."""
    for e in EnderecoCliente.get_by_cliente(cliente_id):
        if (e.get("estado") or "").strip():
            return e["estado"]
    return None


def _conectar():
    """Conexão dedicada para as transações por-documento (autocommit OFF, fuso
    -03:00). Curta — aberta só depois da resposta da SEFAZ e fechada no fim."""
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT,
        autocommit=False, time_zone='-03:00',
    )


def _peek(d):
    """Descompacta o docZip só para o dry-run (tag raiz + chave), sem gravar nada."""
    try:
        xml_bytes = gzip.decompress(base64.b64decode(d.text or ""))
        root = ET.fromstring(xml_bytes)
        chave = None
        for e in root.iter():
            if _local(e.tag) in ("chNFe", "chCTe", "chDFe") and e.text:
                chave = e.text.strip()
                break
        return _local(root.tag), chave
    except Exception:
        return None, None


# ==========================================================================
# Orquestração — captura de UMA rodada (1 requisição) para 1 cliente.
# ==========================================================================
def capturar_cliente(cliente_id, dry_run=False, origem='manual'):
    """Executa UMA consulta ao distDFeInt e (fora do dry-run) grava o lote.

    Devolve um dict estruturado ('ok' + campos). NUNCA faz loop nem manifesta.
    ``origem`` ('manual' | 'agendado') vai para o dfe_consulta_log.
    """
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        return {'ok': False, 'erro': 'Cliente não encontrado.'}

    numero = (cliente.get('numero_cliente') or '').strip() or None
    razao = cliente.get('nome_razao_social') or 'SEM_NOME'
    rotulo = f"{numero + ' - ' if numero else ''}{razao}"

    vinc = DfeCertificado.get_by_cliente(cliente_id)
    if not vinc or not vinc.get('dropbox_path'):
        return {'ok': False,
                'erro': f'O cliente {rotulo} não tem certificado digital vinculado. '
                        f'Vincule o .pfx antes de capturar.'}
    # Interessado no distDFeInt = o CNPJ da PRÓPRIA empresa (cliente), não o do
    # certificado. Importa quando o cert é da MATRIZ e o cliente é FILIAL (mesma
    # raiz): autentica o mTLS com o cert da matriz, mas consulta os documentos da
    # FILIAL. Em vínculo exato, é o mesmo CNPJ. Fallback: o CNPJ do cert.
    cnpj = _digitos(cliente.get('cpf_cnpj')) or _digitos(vinc.get('cnpj'))

    # UF -> cUFAutor (nunca chuta; mensagem específica de qual cliente + o que fazer).
    uf = _uf_principal(cliente_id)
    try:
        cuf = cuf_autor(uf)
    except UfInvalidaError:
        return {'ok': False, 'sem_uf': True,
                'erro': f'O cliente {rotulo} está sem UF no endereço principal. '
                        f'Cadastre o estado (UF) no endereço principal para ativar a captura.'}

    senha_cif = DfeCertificado.get_senha_cifrada(cliente_id)
    if not senha_cif:
        return {'ok': False, 'erro': f'Certificado do cliente {rotulo} sem senha armazenada.'}
    try:
        senha = decifrar_senha(senha_cif)
    except CertificadoError as exc:
        return {'ok': False, 'erro': f'Falha ao decifrar a senha do certificado: {exc}'}

    pfx_bytes = dropbox_sync._service.download_file(vinc['dropbox_path'])
    if not pfx_bytes:
        return {'ok': False, 'erro': 'Falha ao baixar o .pfx do Dropbox.'}
    try:
        chave_priv, cert, cadeia = carregar_par_chave_cert(pfx_bytes, senha)
    except CertificadoError as exc:
        return {'ok': False, 'erro': f'Falha ao abrir o certificado: {exc}'}

    # Cota (comparação em SQL / NOW()): se a SEFAZ mandou aguardar, nem consulta.
    bloqueio = _bloqueado_por_cota(cliente_id)
    if bloqueio:
        faltam = bloqueio.get('faltam_min')
        if not dry_run:
            dfe_log.registrar('pulado_cota', cliente_id, cnpj,
                              detalhe=f'faltam ~{faltam} min', origem=origem)
        return {'ok': False, 'bloqueado': True, 'faltam_min': faltam,
                'erro': f'A SEFAZ pediu para aguardar (consumo indevido anterior). '
                        f'Faltam ~{faltam} min para a próxima consulta deste CNPJ.'}

    ult_nsu = _ler_ult_nsu(cliente_id)

    # UMA requisição.
    sess = montar_sessao_mtls(cert, chave_priv, cadeia)
    try:
        ret, _fmt = consultar(sess, cnpj, cuf, ult_nsu)
    except RuntimeError as exc:
        if not dry_run:
            dfe_log.registrar('erro', cliente_id, cnpj, ult_nsu, detalhe=str(exc), origem=origem)
        return {'ok': False, 'erro': f'Falha ao consultar a SEFAZ: {exc}'}

    cStat = _text(ret, 'cStat')
    xMotivo = _text(ret, 'xMotivo')
    ret_ult = _to_int(_text(ret, 'ultNSU')) or 0
    ret_max = _to_int(_text(ret, 'maxNSU')) or 0
    status_txt = f"{cStat} {xMotivo}"[:255]

    lote = _find(ret, 'loteDistDFeInt')
    docs = [e for e in (lote.iter() if lote is not None else []) if _local(e.tag) == 'docZip']
    docs.sort(key=lambda e: _to_int(e.get('NSU')) or 0)

    base = {'ok': True, 'cStat': cStat, 'xMotivo': xMotivo, 'cnpj': cnpj,
            'cliente': rotulo, 'ult_nsu': ult_nsu, 'ret_ult_nsu': ret_ult,
            'max_nsu': ret_max, 'docs': len(docs), 'faltam': max(0, ret_max - ret_ult)}

    # 656 = consumo indevido: registra o cooldown (proximo_permitido = NOW()+1h)
    # SEMPRE — inclusive no dry-run —, porque a cota foi consumida de verdade na
    # SEFAZ (o "teste" não é grátis do lado deles). Mantém o cursor (ult_nsu
    # inalterado, não avança nem regride) e NÃO grava documento. O ret_ult_nsu
    # (ultNSU que a SEFAZ mandou usar) já vai na resposta para alimentar o seed.
    if cStat == '656':
        execute_query(SQL_NSU_656, (cliente_id, cnpj, ult_nsu, ret_max, status_txt), fetch=False)
        dfe_log.registrar('consulta', cliente_id, cnpj, ult_nsu, cStat, xMotivo,
                          ret_ult, ret_max, len(docs),
                          detalhe='dry-run' if dry_run else None, origem=origem)
        base.update({'consumo_indevido': True, 'dry_run': dry_run,
                     'mensagem': 'A SEFAZ pediu para aguardar ~1h (consumo indevido). '
                                 f'Ela informou ultNSU={ret_ult}. O cursor local foi mantido — '
                                 'a próxima consulta retoma daqui.'})
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

    # 138 (documentos) / 137 (nada novo): processa em ordem de NSU, avança só até
    # o último NSU efetivamente salvo (nsu_ok). Se um doc falha, PARA o lote ali.
    empresa = {'cliente_id': cliente_id, 'numero': numero, 'razao': razao, 'cnpj': cnpj}
    expira = (_hoje_brt() + timedelta(days=DIAS_RETENCAO)).date()
    n_nota = n_resumo = n_evento = n_outro = n_itens = 0
    nsu_ok = ult_nsu
    parada = None

    conn = _conectar()
    try:
        cur = conn.cursor(dictionary=True)
        for d in docs:
            nsu = _to_int(d.get('NSU'))
            try:
                kind, ni = processar_um_doc(conn, cur, empresa, d, expira)
            except Exception as exc:
                conn.rollback()
                parada = f'NSU {nsu}: {exc}'
                break
            if kind == 'nota':
                n_nota += 1
                n_itens += ni
            elif kind == 'resumo':
                n_resumo += 1
            elif kind == 'evento':
                n_evento += 1
            else:
                n_outro += 1
            nsu_ok = nsu   # só avança a marca APÓS salvar com sucesso
        cur.close()
    finally:
        conn.close()

    # Avança ult_nsu só até nsu_ok; proximo_permitido = NULL (libera).
    execute_query(SQL_NSU_OK, (cliente_id, cnpj, nsu_ok, ret_max, status_txt), fetch=False)
    dfe_log.registrar('consulta', cliente_id, cnpj, ult_nsu, cStat, xMotivo,
                      ret_ult, ret_max, len(docs), n_nota, n_evento, detalhe=parada,
                      origem=origem)

    base.update({'notas': n_nota, 'resumos': n_resumo, 'eventos': n_evento,
                 'outros': n_outro, 'itens': n_itens, 'novo_ult_nsu': nsu_ok,
                 'parada': parada})
    return base


def seed_ult_nsu(cliente_id, nsu, usuario_label='?'):
    """Semeia MANUALMENTE o cursor de NSU de uma empresa (ação consciente do admin).

    Caso de uso: o CNPJ já foi consumido por outro sistema até um NSU alto, a
    ``dfe_nsu`` local está em 0, e a SEFAZ devolve 656 mandando usar o ultNSU que
    ela informou. Começar desse NSU PULA os documentos anteriores — quem precisa
    deles obtém por outro meio (importação manual do XML). Por isso NÃO é
    automático (nem entra no handler geral do 656, que preserva o cursor): é este
    caminho separado, explícito, com confirmação e rastro.

    Grava ``ult_nsu = nsu`` e limpa ``proximo_permitido``; deixa rastro em
    ``dfe_consulta_log`` (evento='seed_manual', quem fez e o valor).
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

    # Upsert do cursor: ult_nsu = nsu, proximo_permitido = NULL (libera). max_nsu
    # preservado (o 0 no INSERT só vale se a linha ainda não existir).
    execute_query(
        "INSERT INTO dfe_nsu "
        "(cliente_id, cnpj, ult_nsu, max_nsu, ult_consulta, proximo_permitido, ult_status) "
        "VALUES (%s,%s,%s,0,NOW(),NULL,%s) "
        "ON DUPLICATE KEY UPDATE ult_nsu=VALUES(ult_nsu), "
        "  proximo_permitido=NULL, ult_status=VALUES(ult_status)",
        (cliente_id, cnpj, nsu, f'seed manual por {usuario_label}: ultNSU={nsu}'[:255]),
        fetch=False,
    )
    dfe_log.registrar('seed_manual', cliente_id, cnpj, ult_nsu_env=nsu,
                      detalhe=f'seed manual por {usuario_label}: ult_nsu={nsu} '
                              f'(pula documentos anteriores a esse NSU)')
    return {'ok': True, 'nsu': nsu, 'cliente': rotulo,
            'mensagem': f'Cursor de NSU do cliente {rotulo} definido em {nsu}. '
                        f'A próxima captura começa a partir daí (documentos anteriores '
                        f'não serão capturados).'}


def _conectar_lock():
    """Conexão dedicada (autocommit, fuso -03:00) para segurar o GET_LOCK global
    durante toda a rodada agendada."""
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT,
        autocommit=True, time_zone='-03:00',
    )


def capturar_agendado():
    """Rodada agendada (Fase 3): consulta as empresas com captura ligada, uma por
    vez, na ordem 'mais atrasada primeiro' (listar_para_captura), respeitando o
    cooldown do 656 e um prazo suave. O que não deu tempo vai no próximo tick.

    Protegida por GET_LOCK('dfe_captura', 0) — uma consulta simultânea por deploy
    (a 2ª camada, além do file-lock por container do scheduler). Loga a rodada e
    cada empresa em dfe_consulta_log com origem='agendado'. NUNCA manifesta.

    Só roda com DFE_SCHED_ATIVO=1 (defesa; normalmente o job nem é registrado).
    """
    # PRIMEIRA linha, antes do guard DFE_SCHED_ATIVO e do GET_LOCK: prova de que
    # a rodada foi invocada. Se _job_captura_dfe loga o disparo mas ESTA linha
    # não aparece, o problema está entre o job e aqui (import/app_context/exceção).
    # WARNING para sobreviver a qualquer LOG_LEVEL.
    logger.warning('[dfe-sched] >>> capturar_agendado INVOCADO (pid=%s).', os.getpid())
    if os.getenv('DFE_SCHED_ATIVO', '0').strip() != '1':
        logger.warning('[dfe-sched] capturar_agendado ABORTADO: DFE_SCHED_ATIVO != 1.')
        return

    lock_conn = None
    got_lock = False
    try:
        lock_conn = _conectar_lock()
        cur = lock_conn.cursor()
        cur.execute("SELECT GET_LOCK(%s, 0)", (_LOCK_CAPTURA,))
        got_lock = (cur.fetchone() or [0])[0] == 1
        cur.close()
        if not got_lock:
            dfe_log.registrar('pulado_lock', origem='agendado',
                              detalhe='outro worker/deploy já está capturando')
            logger.info('[dfe-sched] lock ocupado — pulando este tick.')
            return

        empresas = DfeCertificado.listar_para_captura()
        if not empresas:
            logger.info('[dfe-sched] nenhuma empresa com captura ligada.')
            return

        prazo = time.monotonic() + _PRAZO_SUAVE_SEG
        n_proc = n_bloq = n_semuf = n_erro = 0
        tot_notas = tot_docs = 0
        for emp in empresas:
            if time.monotonic() > prazo:
                logger.info('[dfe-sched] prazo suave (%ds) atingido; resto no próximo tick.',
                            _PRAZO_SUAVE_SEG)
                break
            cid = emp['cliente_id']
            # Pré-check de cooldown (SQL barato): pula quem está em espera sem
            # baixar o .pfx nem montar o mTLS.
            if _bloqueado_por_cota(cid):
                n_bloq += 1
                continue
            try:
                r = capturar_cliente(cid, dry_run=False, origem='agendado')
            except Exception as exc:
                n_erro += 1
                logger.exception('[dfe-sched] erro no cliente_id=%s', cid)
                dfe_log.registrar('erro', cid, _digitos(emp.get('cnpj')),
                                  origem='agendado', detalhe=str(exc)[:300])
                continue
            if not r.get('ok'):
                if r.get('bloqueado'):
                    n_bloq += 1
                elif r.get('sem_uf'):
                    n_semuf += 1
                    logger.info('[dfe-sched] cliente_id=%s pulado: sem UF no endereço.', cid)
                else:
                    n_erro += 1
                continue
            if r.get('consumo_indevido'):
                n_bloq += 1
                continue
            n_proc += 1
            tot_notas += r.get('notas', 0) or 0
            tot_docs += r.get('docs', 0) or 0

        total = len(empresas)
        if total > 0 and n_bloq == total:
            dfe_log.registrar('pulado_cota', origem='agendado',
                              detalhe=f'todas as {total} empresas em cooldown')
            logger.info('[dfe-sched] todas as %d empresas em cooldown — ciclo pulado.', total)
        logger.info('[dfe-sched] rodada: %d empresa(s) | proc=%d bloq=%d semUF=%d erro=%d '
                    '| notas=%d docs=%d', total, n_proc, n_bloq, n_semuf, n_erro,
                    tot_notas, tot_docs)
    except Exception:
        logger.exception('[dfe-sched] falha inesperada na rodada agendada.')
    finally:
        if lock_conn is not None:
            try:
                if got_lock:
                    _c = lock_conn.cursor()
                    _c.execute("SELECT RELEASE_LOCK(%s)", (_LOCK_CAPTURA,))
                    _c.fetchall()
                    _c.close()
            except Exception:
                pass
            try:
                lock_conn.close()
            except Exception:
                pass
