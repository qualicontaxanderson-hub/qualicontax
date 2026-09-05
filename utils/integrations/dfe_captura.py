# -*- coding: utf-8 -*-
"""
Motor de captura de NF-e via distDFeInt (lift-and-shift do nh-transportes).

SÓ CONSULTA E SALVA. **NUNCA MANIFESTA** (só distribuição por NSU).
Escopo: NF-e (nfeProc / resNFe / procEventoNFe) — este webservice distribui SÓ
documentos de NF-e. CT-e vem de outro serviço (CTeDistribuicaoDFe) e é capturado
por ``utils/integrations/cte_captura.py``, com cursor e cota independentes. Não
filtra por modelo (55/65): grava tudo o que vier, preenchendo a coluna ``modelo``.

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
from models.cliente_contador import ClienteContador
from utils.integrations.dfe_sefaz import (
    montar_sessao_mtls, consultar, consultar_chave, cuf_autor, UfInvalidaError,
    _find, _text, _local,
)
from utils.integrations import dfe_log
from utils.nfe_parser import parse_nfe_xml
from utils.nfe_import import _save_nfe_dual

logger = logging.getLogger(__name__)

# Fuso de Brasília (offset fixo; Brasil sem horário de verão desde 2019).
_TZ_BR = timezone(timedelta(hours=-3))

TP_CANCELAMENTO = "110111"  # tpEvento de cancelamento de NF-e

# cStat da RESPOSTA da SEFAZ que significam "cancelamento ACEITO". Só com um
# destes a nota pode ser marcada — um pedido REJEITADO (573 duplicidade, 594
# fora do prazo, ...) deixa a nota ATIVA na SEFAZ, e marcá-la aqui produz uma
# divergência que só aparece na fiscalização.
#
# O 155 está na lista de propósito: é "cancelamento homologado FORA DE PRAZO" —
# aceito, apenas registrado como atrasado. Medido em 13/08/2026 sobre os 110
# eventos já recebidos: 99 vieram 135 e 11 vieram 155. Cortar o 155 (como o lado
# CT-e faz, que só conhece 135/136) descancelaria 11 notas legitimamente
# canceladas — o erro oposto, e pior, porque faz apurar imposto de nota que não
# existe mais.
CSTAT_CANCELAMENTO_OK = {"135", "136", "155"}

# Scheduler (Fase 3): lock global + prazo suave por rodada.
_LOCK_CAPTURA = 'dfe_captura'
_PRAZO_SUAVE_SEG = int(os.getenv('DFE_SCHED_PRAZO_SEG', '960'))  # ~16 min

# Teto do xml_raw do evento — dfe_eventos.xml_raw é MEDIUMTEXT (16 MB). Mesmo
# valor do _MAX_XML_SIZE de utils/nfe_import.py e utils/cte_import.py. Na prática
# um procEventoNFe tem ~5 KB; o corte é rede de proteção, não caso esperado.
_MAX_XML_EVENTO = 16_000_000

# Teto de buscas por chave (consChNFe) por empresa por rodada. Cada busca é uma
# requisição à SEFAZ e conta na MESMA cota do 656 — o teto evita estourar. O que
# passar do teto fica como resumo pendente (incompleta=1) e é retomado no próximo
# ciclo pelo retry (_retry_pendentes).
_MAX_CHNFE_CICLO = int(os.getenv('DFE_MAX_CHNFE_CICLO', '30'))

# Drenagem multi-lote (padrão captura_massa do NH). Enquanto o distDFeInt devolver
# 138 (ainda há documentos: ultNSU < maxNSU), consulta em SEQUÊNCIA avançando o
# cursor, sem esperar o próximo tick do cron. Para em 137 (fim) ou 656 (cota real).
# O 656 é por NSU desalinhado / consulta ociosa, NÃO por drenar — enquanto vem 138
# a SEFAZ autoriza consultas consecutivas. Travas de segurança (conservadoras de
# propósito: prefere-se drenar um backlog grande em ALGUNS ciclos a segurar o worker
# numa rodada gigante):
#   _MAX_LOTES_CICLO   teto de lotes por empresa por rodada (60×50 = 3.000 docs/ciclo;
#                      ex.: um backlog de ~11 mil drena em ~4 ciclos do cron).
#   _MAX_SEG_EMPRESA   teto de tempo por empresa por rodada (90s: não trava o worker
#                      nem impede as outras empresas de rodar na mesma rodada).
#   _INTERVALO_LOTE    pausa de cortesia entre lotes (igual ao NH, ~0.4s).
# Bateu o teto → para e o resto drena no próximo ciclo (o cursor já ficou salvo).
_MAX_LOTES_CICLO = int(os.getenv('DFE_MAX_LOTES_CICLO', '60'))
#   _MAX_JANELAS_VAZIAS  teto de janelas VAZIAS (137 com fila à frente) encadeadas
#                        num mesmo ciclo. Contador PRÓPRIO, separado do de lotes:
#                        varrer vazio não é o mesmo trabalho que baixar documento,
#                        e misturar os dois orçamentos esconderia qual deles
#                        estourou. É o terceiro freio, junto do teto de lotes e do
#                        teto de tempo — nenhum laço contra a SEFAZ fica sem freio.
_MAX_JANELAS_VAZIAS = int(os.getenv('DFE_MAX_JANELAS_VAZIAS', '50'))
_MAX_SEG_EMPRESA = int(os.getenv('DFE_MAX_SEG_EMPRESA', '90'))
_INTERVALO_LOTE = float(os.getenv('DFE_INTERVALO_LOTE_SEG', '0.4'))


# ==========================================================================
# SQLs — destino é a conf-compras (nfe_importacoes/nfe_itens), origem='SEFAZ'.
# tipo fixo 'entrada' (ótica do interessado = dono do certificado) → 1 registro,
# NUNCA a entrada-dupla do fluxo manual. dfe_nsu (adiante) segue como cursor/cota.
#
# NOTA (Opção A): o caminho VIVO da nota COMPLETA passou a gravar pelo MESMO core
# do Dropbox (utils.nfe_import._save_nfe_dual) — ver _importar_nfe_completa. Os
# SQL_NOTA_* abaixo ficaram LEGADO: são usados só pela migração histórica
# migrations/backfill_dfe_para_conf_compras.py (dfe_documentos → conf-compras), que
# ainda os importa. SQL_RESUMO_UPSERT continua VIVO: grava o resumo PENDENTE
# (resNFe sem completa disponível) como incompleta=1 para retry posterior.
# ==========================================================================

# UNIQUE efetiva = (chave_acesso, 'entrada'). O IF(origem='SEFAZ', VALUES(x), x)
# em cada coluna GARANTE que a captura NUNCA sobrescreve uma linha manual
# (UPLOAD/DROPBOX) — a manual é mais rica; origem nunca muda no conflito. E
# incompleta=IF(origem='SEFAZ',0,...) promove resumo→completa (nunca ao contrário).
SQL_NOTA_UPSERT = (
    "INSERT INTO nfe_importacoes "
    "(cliente_id, tipo, nome_arquivo, chave_acesso, num_nota, serie, data_emissao, "
    " emit_cnpj, emit_nome, emit_uf, dest_cnpj, dest_nome, dest_uf, valor_total, "
    " cfop, natureza_operacao, origem, cancelada, xml_caminho, incompleta) "
    "VALUES (%s,'entrada',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SEFAZ',%s,%s,0) "
    "ON DUPLICATE KEY UPDATE "
    "  num_nota=IF(origem='SEFAZ',VALUES(num_nota),num_nota), "
    "  serie=IF(origem='SEFAZ',VALUES(serie),serie), "
    "  data_emissao=IF(origem='SEFAZ',VALUES(data_emissao),data_emissao), "
    "  emit_cnpj=IF(origem='SEFAZ',VALUES(emit_cnpj),emit_cnpj), "
    "  emit_nome=IF(origem='SEFAZ',VALUES(emit_nome),emit_nome), "
    "  emit_uf=IF(origem='SEFAZ',VALUES(emit_uf),emit_uf), "
    "  dest_cnpj=IF(origem='SEFAZ',VALUES(dest_cnpj),dest_cnpj), "
    "  dest_nome=IF(origem='SEFAZ',VALUES(dest_nome),dest_nome), "
    "  dest_uf=IF(origem='SEFAZ',VALUES(dest_uf),dest_uf), "
    "  valor_total=IF(origem='SEFAZ',VALUES(valor_total),valor_total), "
    "  cancelada=IF(origem='SEFAZ',VALUES(cancelada),cancelada), "
    "  xml_caminho=IF(origem='SEFAZ',VALUES(xml_caminho),xml_caminho), "
    "  incompleta=IF(origem='SEFAZ',0,incompleta)"
)

# resNFe → linha incompleta=1 (sem itens). No conflito é NO-OP: não rebaixa uma
# completa e não toca linha manual. (Mesma regra do resumo no motor NH.)
SQL_RESUMO_UPSERT = (
    "INSERT INTO nfe_importacoes "
    "(cliente_id, tipo, nome_arquivo, chave_acesso, num_nota, serie, data_emissao, "
    " emit_cnpj, emit_nome, emit_uf, dest_cnpj, dest_nome, dest_uf, valor_total, "
    " origem, cancelada, incompleta) "
    "VALUES (%s,'entrada',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SEFAZ',%s,1) "
    "ON DUPLICATE KEY UPDATE chave_acesso=chave_acesso"
)

# id + origem da linha: origem diz se a nota é NOSSA (SEFAZ) ou manual — só
# regravamos itens quando é SEFAZ (nunca mexemos nos itens de uma linha manual).
SQL_NOTA_ID = (
    "SELECT id, origem FROM nfe_importacoes WHERE chave_acesso = %s AND tipo = 'entrada'"
)

# Itens: nfe_itens não tem UNIQUE(nfe_id,n_item) → DELETE + re-INSERT (idempotente
# por nota). produto_catalogo_id fica NULL: o vínculo é feito depois, pela UI que
# já existe. cfop/impostos por item ficam vazios/0 nesta fase (MVP).
SQL_ITENS_DEL = "DELETE FROM nfe_itens WHERE nfe_id = %s"
# ATENÇÃO: caminho morto — se reativar, incluir custo_total_item e
# valor_unit_comercial. Hoje a captura grava item por _save_nfe_dual (utils/
# nfe_import), que já preenche os dois campos a partir do parser; este INSERT
# sobrou da fase MVP e só é importado pela migration histórica
# backfill_dfe_para_conf_compras.py.
SQL_ITEM_INS = (
    "INSERT INTO nfe_itens "
    "(nfe_id, num_item, codigo_produto, descricao, ncm, cfop, unidade, "
    " quantidade, valor_unitario, valor_total, produto_catalogo_id) "
    "VALUES (%s,%s,%s,%s,%s,'',%s,%s,%s,%s,NULL)"
)

# Cancelamento (procEventoNFe): reflete na flag da nota, marcando TODAS as linhas
# da chave — os dois lados (entrada do comprador + saída do vendedor, do dual-save)
# e qualquer origem (SEFAZ, DROPBOX, UPLOAD, Q-ROBO). A chave identifica a nota de
# forma única e o 110111 vem assinado pela SEFAZ: se ela cancelou, cancelou para
# todo mundo — não há linha "nossa" e linha "manual" nesse quesito. É o mesmo
# critério que o import do Dropbox já usa (_marcar_cancelada, em routes).
#
# O WHERE antigo (tipo='entrada' AND origem='SEFAZ') deixava passar:
#   * saídas (nenhuma das 35 mil linhas de saída jamais foi marcada);
#   * notas que entraram pelo Dropbox/Q-Robô, mesmo sendo a MESMA nota.
#
# Registro do que o WHERE novo NÃO resolve (ver o diagnóstico do dia):
#   * evento que chega ANTES da nota — o UPDATE não acha linha e vira no-op; e o
#     upgrade resumo→completa (DELETE+INSERT) regrava cancelada=0. Foi o que
#     aconteceu com 3 dos 4 cancelamentos já recebidos. A correção disso é
#     reconciliar por dfe_eventos (agora populado por gravar_evento), não aqui;
#   * saída só é marcada quando o evento de fato entra na nossa fila de DFe.
SQL_CANCELA_NOTA = (
    "UPDATE nfe_importacoes SET cancelada=1 WHERE chave_acesso = %s"
)

# Histórico auditável de TODO evento recebido (não só cancelamento): responde
# "quando/qual evento cancelou esta nota" e deixa rastro dos demais (CC-e,
# manifestações de terceiros nas notas do cliente, EPEC...). UNIQUE(chave_evento)
# → o mesmo evento redistribuído (outro NSU, outro stream) atualiza em vez de
# duplicar. cliente_id fica FORA do UPDATE de propósito: um evento pode chegar
# pelo stream de dois clientes (dual-save) e o primeiro que o viu é a referência
# estável — trocar a cada redistribuição só faria a coluna oscilar.
SQL_EVENTO_UPSERT = (
    "INSERT INTO dfe_eventos "
    "(cliente_id, chave_evento, ch_nfe, tp_evento, n_seq, descricao, dh_evento, "
    " nsu, schema_dfe, org_cnpj, xml_caminho, xml_raw) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  tp_evento=VALUES(tp_evento), n_seq=VALUES(n_seq), "
    "  descricao=VALUES(descricao), dh_evento=VALUES(dh_evento), "
    "  nsu=VALUES(nsu), schema_dfe=VALUES(schema_dfe), "
    "  org_cnpj=VALUES(org_cnpj), xml_caminho=VALUES(xml_caminho), "
    # COALESCE (mesmo padrão do _sql_update do CT-e): um reprocessamento que
    # venha sem XML NÃO apaga o xml_raw já gravado.
    "  xml_raw=COALESCE(VALUES(xml_raw), xml_raw)"
)

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

# COOLDOWNS — agora configuráveis, e medidos.
#
# Eram fixos no SQL: 65 min no 137, 60 min no 656. Com o cron */20, quem é selado
# em T fica elegível em T+65 e só é consultado no tique de T+80. Medição de
# 06→07/08: 36% das consultas de NF-e voltavam 656 (69 de 191), com os tiques
# pesados repetindo o ciclo de 80 min — 02:40, 04:00, 05:20, todos com ~50% de
# rejeição. O CT-e, mesmo código com cron de 60 min, teve ZERO 656 em 248
# consultas. Ou seja: 80 minutos não bastam para essa carteira.
#
# 130 min empurra a próxima tentativa para ~2h20 (o tique seguinte a T+130).
# Custo: nota que chegue logo após um 137 espera mais. Ganho: para de queimar
# 1/3 das consultas em rejeição — e consulta rejeitada não traz documento
# nenhum, então a espera EFETIVA de hoje já é de duas tentativas.
#
# Em env var para calibrar sem redeploy: se 130 não derrubar a taxa, o plano B
# é backoff progressivo por empresa (dobrar a cada 656 consecutivo, zerar no
# primeiro sucesso), que exige coluna de contador.
#
# ── MEDIÇÃO DE 15/08/2026: os 130 no 656 NÃO derrubaram a taxa. ──────────────
#
#   com 60 min (01–03/08)    52–57% de 656   ~30 consultas/empresa/dia
#   com 130 min (08–14/08)   48–54% de 656   ~10 consultas/empresa/dia
#
# Quatro pontos de melhora custaram DOIS TERÇOS da velocidade. E o preço não é
# distribuído por igual: quem está em dia não perde nada com dez consultas ao
# dia, mas quem está atrasado precisa de MUITAS rodadas para drenar o passado —
# e com uma tentativa a cada 2h20 não alcança nunca. Foi assim que o B2T (1.236
# atrasados) e o Serafim ficaram presos.
#
# OS DOIS CASOS PASSAM A SER DIFERENTES, porque significam coisas opostas:
#
#   137 = "não há nada novo". Perguntar de novo logo é inútil por definição:
#         não há documento para trazer. Fica em 130 — é a maioria das rodadas e
#         é onde a espera longa é gratuita.
#
#   656 = "recusei". Medido: em 51% dos casos a SEFAZ estava anunciando fila
#         REAL, que apareceu na consulta seguinte. Aqui esperar é caro — são
#         justamente as empresas atrasadas, as que precisam de mais tentativas,
#         não de menos. Vai para 75.
#
# ── POR QUE 75 E NÃO 60 (Nota Técnica 2014.002) ─────────────────────────────
#
# A NT manda esperar 1 HORA após um 137/656. E o detalhe que decide o número:
# consultar ANTES de completar a hora **ZERA o relógio e recomeça a contagem**.
# Quem tenta cedo demais não se atrasa um pouco — fica preso para sempre, e
# cada nova tentativa renova a própria punição.
#
# 60 é exatamente o limite: qualquer diferença de relógio entre nós e a SEFAZ,
# ou alguns segundos de fila no cron, e a consulta cai DENTRO da hora. 75 dá
# 15 minutos de margem e ainda é quase o dobro do ritmo dos 130.
#
# ── E O QUE O RELÓGIO REALMENTE MEDE ────────────────────────────────────────
#
# A NT 1.14 diz que o 656 devolve "o número da última consulta realizada para o
# CNPJ" — do CNPJ, não da aplicação. O contador e o relógio são COMPARTILHADOS
# por qualquer sistema que consulte com certificado daquele CNPJ.
#
# Consequência prática, medida em 15/08/2026: o app.novohorizonte.com.br tem
# captura própria dos MESMOS CNPJs. Quando ele consulta, o relógio zera; quando
# nós consultamos minutos depois, cai dentro da hora e leva 656 — e o nosso 656
# zera de novo, prejudicando o próximo. Os dois se atropelam indefinidamente.
#
# É por isso que o seed do Pavão NÃO resolveu (nunca foi o cursor) e por que 35
# empresas funcionam sem problema: são as que só nós consultamos. Margem de
# tempo AJUDA, mas não conserta CNPJ com dois consumidores — isso é decisão de
# quem captura o quê.
#
# Continua em env var: `DFE_COOLDOWN_656_MIN` ajusta sem redeploy.
_COOLDOWN_137_MIN = int(os.getenv('DFE_COOLDOWN_137_MIN', '130'))
_COOLDOWN_656_MIN = int(os.getenv('DFE_COOLDOWN_656_MIN', '75'))

SQL_NSU_656 = (
    "INSERT INTO dfe_nsu "
    "(cliente_id, cnpj, ult_nsu, max_nsu, ult_consulta, proximo_permitido, ult_status) "
    f"VALUES (%s,%s,%s,%s,NOW(),NOW() + INTERVAL {_COOLDOWN_656_MIN} MINUTE,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  ult_nsu=VALUES(ult_nsu), " + _MAX_NSU + ", "
    f"  ult_consulta=NOW(), proximo_permitido=NOW() + INTERVAL {_COOLDOWN_656_MIN} MINUTE, "
    "  ult_status=VALUES(ult_status)"
)

# 137 (fim de fila: nada novo / em dia). Grava o cursor IGUAL ao OK, mas com
# cooldown em vez de liberar (NULL). NÃO afeta o 138 (tem mais doc → continua
# drenando sem cooldown) nem a janela vazia (137 com fila à frente, que encadeia
# no mesmo ciclo e não passa por aqui).
SQL_NSU_137 = (
    "INSERT INTO dfe_nsu "
    "(cliente_id, cnpj, ult_nsu, max_nsu, ult_consulta, proximo_permitido, ult_status) "
    f"VALUES (%s,%s,%s,%s,NOW(),NOW() + INTERVAL {_COOLDOWN_137_MIN} MINUTE,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  ult_nsu=VALUES(ult_nsu), " + _MAX_NSU + ", "
    f"  ult_consulta=NOW(), proximo_permitido=NOW() + INTERVAL {_COOLDOWN_137_MIN} MINUTE, "
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


def _mesmo_titular(cnpj_a, cnpj_b):
    """True se os dois documentos são o MESMO titular — o mesmo casamento que a
    captura já usa para "matriz cobre filial":

      * CNPJ (14 díg.): compara pela RAIZ (8 primeiros dígitos). Matriz e filiais
        compartilham a raiz, então uma nota destinada a outra filial do MESMO grupo
        do dono do certificado ainda conta como entrada dele.
      * CPF (11 díg.): compara os 11 dígitos inteiros (não há "raiz").

    Documento vazio ou com menos de 11 dígitos NUNCA casa (retorna False) — é o que
    barra a nota só-autXML (dono do cert não é o destinatário)."""
    a = _digitos(cnpj_a)
    b = _digitos(cnpj_b)
    if len(a) < 11 or len(b) < 11:
        return False
    if len(a) == 14 and len(b) == 14:
        return a[:8] == b[:8]
    return a == b


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


def _detalhe_403(exc, validade):
    """Traduz o erro de transporte da SEFAZ para uma causa LEGÍVEL quando for HTTP
    403 (a SEFAZ recusou o certificado no handshake mTLS) E o certificado estiver
    VENCIDO. Nesse caso devolve 'Certificado vencido em DD/MM/AAAA' — o que o Painel
    de Status passa a mostrar no lugar do '403' cru (a causa real do 403 silencioso).

    Nos demais casos devolve ``str(exc)`` inalterado: não é 403 (outro erro de
    transporte), não temos a validade, ou é 403 com o cert ainda no prazo (aí o 403
    tem outra causa — revogação, CNPJ sem credenciamento — e não se mascara).

    ``validade`` é o ``dfe_certificados.validade`` (date) da empresa, ou None.
    """
    txt = str(exc)
    if '403' not in txt or not validade or not hasattr(validade, 'year'):
        return txt
    if validade < _hoje_brt().date():
        return f'Certificado vencido em {validade.strftime("%d/%m/%Y")}'
    return txt


# ==========================================================================
# Extração (resNFe / procEventoNFe). A nota COMPLETA (nfeProc) NÃO é mais extraída
# aqui: passa direto pelo parse_nfe_xml + _save_nfe do fluxo Dropbox (Opção A).
# ==========================================================================
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

    # cStat da RESPOSTA da SEFAZ — quem decide se o cancelamento vale.
    #
    # CUIDADO: um procEventoNFe tem DOIS infEvento, o do PEDIDO e o da RESPOSTA,
    # e o _find devolve o PRIMEIRO. Procurar cStat na raiz pega o bloco errado
    # (o pedido, que não tem cStat) e o valor viria vazio — o que, na guarda,
    # faria parar de cancelar TUDO. Por isso desce-se no retEvento antes.
    ret = _find(root, "retEvento")
    c_stat = _text(ret, "cStat") if ret is not None else None

    if not chave_evento:
        if tp_evento and ch_nfe and n_seq is not None:
            chave_evento = f"ID{tp_evento}{ch_nfe}{str(n_seq).zfill(2)}"
        else:
            raise ValueError("evento sem Id/chave_evento identificável")

    return {
        "chave_evento": chave_evento[:60], "ch_nfe": ch_nfe, "tp_evento": tp_evento,
        "n_seq": n_seq, "org_cnpj": org_cnpj, "descricao": descricao,
        "dh_txt": dh_txt, "ano": ano, "mes": mes, "c_stat": c_stat,
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
def _caminho_fiscal(empresa, ano, mes, nome, sentido):
    svc = dropbox_sync._service
    pasta = svc.pasta_fiscal(empresa["razao"], ano, mes, sentido, empresa.get("numero"))
    return f"{pasta}/{nome}.xml"


def _subir_xml(caminho, xml_bytes):
    """Sobe o XML no Dropbox ANTES de gravar no banco; falha aqui aborta o doc
    (o chamador para o lote e não avança o cursor além dele)."""
    if not dropbox_sync._service.upload_bytes(caminho, xml_bytes):
        raise RuntimeError(f"falha ao subir o XML no Dropbox: {caminho}")


# ==========================================================================
# Gravação (transação por documento, na conexão dedicada)
# ==========================================================================
CUF_TO_UF = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP",
    "17": "TO", "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB",
    "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
    "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
    "51": "MT", "52": "GO", "53": "DF",
}


def _uf_da_chave(chave):
    """UF do emitente pelos 2 primeiros dígitos (cUF) da chave de 44 — serve para
    nota completa E resumo (a chave sempre está presente)."""
    return CUF_TO_UF.get((chave or "")[:2])


def _data_de(dh_txt):
    """'YYYY-MM-DD HH:MM:SS' -> 'YYYY-MM-DD' (a coluna data_emissao é DATE)."""
    return (dh_txt or "")[:10] or None


# ==========================================================================
# Nota COMPLETA → MESMO core do Dropbox (Opção A). A SEFAZ vira só mais uma FONTE
# de XML: parse_nfe_xml + _save_nfe_dual gravam nfe_importacoes/nfe_itens com TODOS
# os campos (impostos, cfop, natureza) e o vínculo produto_catalogo_id por item.
# ==========================================================================
def _pasta_dfe(empresa, data_emissao, sentido):
    """Pasta destino DEFINITIVA por-empresa, na convenção ÚNICA do roteador:
    ``EMPRESAS/{nº - razão}/FISCAL/{SENTIDO}/{ano}/{mês}``.

    A captura conhece a EMPRESA e o SENTIDO no ato do write, então grava DIRETO no
    destino final em vez de passar pela ponte ``/Fiscal/IMPORTADOS`` (que o
    ``cron_roteador`` tinha de reclassificar e mover de novo). ``sentido`` ∈
    ``ENTRADAS | SAIDAS | CTE | EVENTOS | ERROS``. ano/mês saem da emissão da nota
    (fallback: hoje BRT)."""
    dt = data_emissao or _hoje_brt().date()
    svc = dropbox_sync._service
    return svc.pasta_fiscal(empresa["razao"], dt.year, dt.month, sentido,
                            empresa.get("numero"))


def _remover_sefaz_incompleto(chave):
    """Upgrade resumo→completa: apaga a linha resumo SEFAZ (incompleta=1) e seus
    itens para a completa poder entrar pelo core (_save_nfe retornaria 'dup' e a
    descartaria). Guard rígido origem='SEFAZ' AND incompleta=1 → NUNCA toca linha
    manual (UPLOAD/DROPBOX) nem uma completa já existente."""
    if not chave:
        return
    execute_query(
        "DELETE it FROM nfe_itens it JOIN nfe_importacoes n ON n.id = it.nfe_id "
        "WHERE n.chave_acesso=%s AND n.tipo='entrada' AND n.origem='SEFAZ' "
        "AND n.incompleta=1",
        (chave,), fetch=False,
    )
    execute_query(
        "DELETE FROM nfe_importacoes "
        "WHERE chave_acesso=%s AND tipo='entrada' AND origem='SEFAZ' AND incompleta=1",
        (chave,), fetch=False,
    )


def _importar_nfe_completa(empresa, xml_bytes, cli_index=None):
    """nfeProc COMPLETO → grava pelo MESMO fluxo do Dropbox e arquiva no padrão
    IMPORTADOS/ERROS. Devolve o nº de itens. Levanta em falha de parse/gravação
    (o chamador para o lote e não avança o cursor além deste doc).

    ``cli_index`` é o mapa {cnpj_dígitos: cliente_id} da rodada (Cliente.index_cpf_cnpj).

    ENTRADA (ótica do dono do certificado): SÓ é criada quando o DESTINATÁRIO da nota
    é REALMENTE o dono do cert (casa por raiz — matriz cobre filial). Se o dono é
    apenas AUTORIZADO a baixar o XML (autXML) e não é o destinatário, a nota NÃO vira
    entrada dele — era isso que criava a "entrada fantasma da Qualicontax".

    SAÍDA (ótica do emitente): quando o EMITENTE também é cliente cadastrado (≠ o dono
    do certificado), grava a linha de SAÍDA dele (dual-save) — a venda entre dois
    clientes que o distDFeInt entrega. Independe de a entrada ter sido criada ou não.

    Se a nota não é entrada do dono NEM saída de um cliente (autXML puro de terceiros),
    nada é gravado (e um eventual resumo fantasma anterior é removido); devolve 0."""
    xml_str = xml_bytes.decode("utf-8", "replace")
    parsed = parse_nfe_xml(xml_str)                 # pode levantar ValueError
    chave = parsed.get("chave") or parsed["header"].get("chave_acesso") or ""
    data_emissao = parsed["header"].get("data_emissao")

    # Guard do autXML: entrada do dono só quando o dest da nota é o próprio dono do
    # cert (raiz — matriz cobre filial). Caso contrário (só autXML), dest_cli=None.
    dest_cli = (empresa["cliente_id"]
                if _mesmo_titular(parsed["header"].get("dest_cnpj"), empresa["cnpj"])
                else None)

    # Emitente é cliente cadastrado (e não a própria empresa capturadora)? → saída.
    emit_cli = None
    if cli_index:
        emit_cli = cli_index.get(_digitos(parsed["header"].get("emit_cnpj")))
        if emit_cli == empresa["cliente_id"]:
            emit_cli = None

    # autXML puro: dono não é dest e emitente não é cliente → nada nosso. Remove um
    # eventual resumo fantasma (o resumo assume dono=dest) e sai sem gravar linha.
    if dest_cli is None and emit_cli is None:
        _remover_sefaz_incompleto(chave)
        logger.info("[dfe] nota autXML ignorada (dono do cert não é dest e emitente "
                    "não é cliente): chave=%s", chave)
        return 0

    # Se havia um RESUMO SEFAZ anterior (incompleta=1), guarda a data da 1ª
    # importação. Como o upgrade é DELETE+INSERT, a completa nova nasceria com
    # importado_em=agora; restaurando o valor original, o ON UPDATE marca
    # atualizado_em=agora → a conf-compras mostra "ATUALIZADO" (resumo→completa).
    # Só interessa quando de fato criamos a entrada do dono.
    importado_orig = None
    if dest_cli is not None and chave:
        _prev = execute_query(
            "SELECT importado_em FROM nfe_importacoes "
            "WHERE chave_acesso=%s AND tipo='entrada' AND origem='SEFAZ' AND incompleta=1 "
            "ORDER BY id LIMIT 1",
            (chave,), fetch=True, fetch_one=True,
        )
        importado_orig = _prev.get("importado_em") if _prev else None

    try:
        # Upgrade + gravação pelo core compartilhado. Entrada na ótica do dono do
        # certificado (só se dest_cli); + saída na ótica do emitente QUANDO cliente.
        # O _save_nfe_dual grava cada linha por (chave, tipo) — a entrada existente
        # vira 'dup' (intocada) e só a saída nova é inserida.
        _remover_sefaz_incompleto(chave)
        _save_nfe_dual(parsed, f"{chave}.xml", "SEFAZ", xml_str,
                       dest_cli=dest_cli, emit_cli=emit_cli)
        if dest_cli is not None and importado_orig is not None:
            execute_query(
                "UPDATE nfe_importacoes SET importado_em=%s "
                "WHERE chave_acesso=%s AND tipo='entrada' AND origem='SEFAZ'",
                (importado_orig, chave), fetch=False,
            )
    except Exception:
        # Importação falhou → arquiva em ERROS (best-effort) e propaga (para o lote).
        try:
            _subir_xml(f"{_pasta_dfe(empresa, data_emissao, 'ERROS')}/{chave or 'sem_chave'}.xml",
                       xml_bytes)
        except Exception:
            pass
        raise

    # Sucesso → arquiva em IMPORTADOS (mesmo padrão do Dropbox), sob o DONO do cert,
    # só quando a entrada dele foi criada. Falha aqui aborta o doc; o retry re-sobe
    # (o _save_nfe vira 'dup', idempotente).
    if dest_cli is not None:
        _subir_xml(f"{_pasta_dfe(empresa, data_emissao, 'ENTRADAS')}/{chave}.xml", xml_bytes)

    # Saída (emit é cliente) → arquiva TAMBÉM sob o emitente em .../SAIDAS/{mês}.
    # Best-effort: a linha no banco já entrou; falha aqui não aborta o doc (diferente
    # do arquivo da entrada acima, que é o principal).
    if emit_cli:
        emit_cliente = Cliente.get_by_id(emit_cli)
        if emit_cliente:
            empresa_emit = {
                "razao": emit_cliente.get("nome_razao_social") or "SEM_NOME",
                "numero": (emit_cliente.get("numero_cliente") or "").strip() or None,
            }
            try:
                _subir_xml(f"{_pasta_dfe(empresa_emit, data_emissao, 'SAIDAS')}/{chave}.xml", xml_bytes)
            except Exception:
                logger.warning("[dfe] saída gravada, mas falha ao arquivar XML sob o "
                               "emitente (cliente_id=%s, chave=%s)", emit_cli, chave)
    return len(parsed.get("itens", []))


def _docs_do_ret(ret):
    """Descompacta todos os docZip de um <retDistDFeInt> (consChNFe) em bytes XML."""
    lote = _find(ret, "loteDistDFeInt")
    out = []
    for e in (lote.iter() if lote is not None else []):
        if _local(e.tag) == "docZip" and e.text:
            try:
                out.append(gzip.decompress(base64.b64decode(e.text)))
            except Exception:
                continue
    return out


def _primeiro_nfeproc(ret):
    """Primeiro nfeProc (nota completa) dentro da resposta do consChNFe, ou None."""
    for xb in _docs_do_ret(ret):
        try:
            if _local(ET.fromstring(xb).tag) == "nfeProc":
                return xb
        except Exception:
            continue
    return None


def gravar_evento(conn, cur, empresa, ev, xml_bytes):
    """procEventoNFe → arquiva o XML no Dropbox, registra o evento em dfe_eventos
    (histórico auditável de TODOS os eventos) e, se for CANCELAMENTO, marca a nota
    (cancelada=1) em todas as linhas da chave.

    O INSERT do histórico e o UPDATE da flag vão no MESMO commit: ou o evento fica
    registrado com a nota marcada, ou nada entra."""
    ano = ev["ano"] or _hoje_brt().year
    mes = ev["mes"] or _hoje_brt().month
    # Arquiva pelo Id do evento (não pela chNFe, para não colidir com a nota),
    # na convenção única com SENTIDO=EVENTOS (mesma pasta do roteador).
    caminho = _caminho_fiscal(empresa, ano, mes, ev["chave_evento"], 'EVENTOS')
    _subir_xml(caminho, xml_bytes)

    # O XML também vai para o BANCO (xml_raw), não só para o Dropbox: o evento
    # era o último documento do sistema que dependia 100% do arquivo, e a janela
    # de 90 dias da SEFAZ (xml_expira_em) o apagaria de qualquer jeito.
    xml_evento = xml_bytes.decode("utf-8", "replace")[:_MAX_XML_EVENTO]

    # ch_nfe é NOT NULL na tabela; evento sem chNFe (não deveria existir em NF-e)
    # fica só no Dropbox, com aviso — melhor do que derrubar a fila de NSU.
    if ev["ch_nfe"]:
        cur.execute(SQL_EVENTO_UPSERT, (
            empresa["cliente_id"], ev["chave_evento"], ev["ch_nfe"], ev["tp_evento"],
            ev["n_seq"], ev["descricao"], ev["dh_txt"], ev.get("nsu"),
            (ev.get("schema_dfe") or None), ev["org_cnpj"], caminho[:300],
            xml_evento,
        ))
    else:
        logger.warning("[dfe] evento sem chNFe — só arquivado no Dropbox: %s",
                       ev["chave_evento"])

    if ev["tp_evento"] == TP_CANCELAMENTO and ev["ch_nfe"]:
        # GUARDA DE STATUS: só cancela se a SEFAZ tiver ACEITADO o pedido. O
        # evento fica registrado em dfe_eventos de qualquer jeito — o histórico
        # é de TODOS os eventos, inclusive dos recusados; o que a guarda decide
        # é apenas se a nota muda de estado.
        if ev.get("c_stat") in CSTAT_CANCELAMENTO_OK:
            cur.execute(SQL_CANCELA_NOTA, (ev["ch_nfe"],))
        else:
            logger.warning(
                "[dfe] cancelamento da nota %s NAO aceito pela SEFAZ "
                "(cStat=%s) — nota segue ATIVA. Evento %s registrado no "
                "historico.", ev["ch_nfe"], ev.get("c_stat") or "?",
                ev["chave_evento"])
    conn.commit()


def gravar_resumo_nota(conn, cur, empresa, res):
    """resNFe PENDENTE → nfe_importacoes com incompleta=1 (sem itens). Usado quando
    a completa ainda NÃO foi obtida (teto de cota do ciclo, 656 ou completa
    indisponível). Fica visível na conf-compras e é retomado pelo _retry_pendentes,
    que busca a completa por chave (consChNFe) e faz o upgrade. No-op se a chave já
    existe (não rebaixa completa nem toca manual).

    GUARD do autXML aqui: o resumo assume dono do cert = destinatário e grava a
    entrada dele. Diferente da completa, o schema resNFe NÃO carrega o destinatário
    (só chNFe, emitente, dhEmi, vNF, cSit) — não há dest_cnpj para checar. Na prática
    isso é seguro porque o distDFeInt só ENTREGA resNFe a quem é o destinatário da
    nota (autXML/emitente vêm como nfeProc completo, tratado com guard em
    _importar_nfe_completa). E se ainda assim um resumo fantasma escapar, ele se
    autocorrige no upgrade: _processar_resumo busca a completa por chave e chama
    _importar_nfe_completa, que — vendo dest ≠ dono — remove o resumo (via
    _remover_sefaz_incompleto) e NÃO recria a entrada."""
    cancelada = 1 if res["situacao"] == "cancelada" else 0
    cur.execute(SQL_RESUMO_UPSERT, (
        empresa["cliente_id"], f'{res["chave"]}.xml', res["chave"],
        res["numero"], res["serie"], _data_de(res["dh_txt"]),
        res["emit_cnpj"], res["emit_nome"], _uf_da_chave(res["chave"]),
        empresa["cnpj"], empresa["razao"], empresa.get("uf"),
        res["valor_total"], cancelada,
    ))
    conn.commit()


def _processar_resumo(conn, cur, empresa, root, ctx):
    """resNFe: tenta obter a nota COMPLETA por chave (consChNFe) e gravá-la pelo
    fluxo Dropbox. Se não der (teto de cota, 656 ou completa indisponível), grava o
    resumo PENDENTE (incompleta=1) para retry. Devolve (kind, n_itens)."""
    res = extrair_resumo_nota(root)
    chave = res["chave"]

    # Teto de buscas por chave no ciclo → não consulta; deixa pendente p/ próximo tick.
    if ctx["chnfe_usadas"] >= ctx["chnfe_max"]:
        gravar_resumo_nota(conn, cur, empresa, res)
        return "resumo", 0

    try:
        ret = consultar_chave(ctx["sess"], ctx["cnpj"], ctx["cuf"], chave)
    except RuntimeError:
        # Falha de transporte/HTTP: não queima o ciclo; deixa pendente.
        gravar_resumo_nota(conn, cur, empresa, res)
        return "resumo", 0
    ctx["chnfe_usadas"] += 1

    if _text(ret, "cStat") == "656":
        # Consumo indevido na busca por chave: liga o cooldown e CORTA o restante da
        # rodada desta empresa (não insiste). O resumo fica pendente.
        ctx["cooldown_656"] = True
        ctx["cortar"] = True
        ctx["motivo_corte"] = f"656 (consumo indevido) na busca por chave {chave}"
        gravar_resumo_nota(conn, cur, empresa, res)
        return "resumo", 0

    xmlc = _primeiro_nfeproc(ret)
    if xmlc is None:
        # A SEFAZ localizou a nota mas devolveu SÓ o resumo: para o destinatário
        # que não manifestou, a completa não sai. É isso que mantinha 903
        # resumos presos em 103 empresas (02/09/2026).
        #
        # A Ciência da Operação (210210) destrava — e só é aceita até 10 DIAS da
        # emissão. Por isso ela acontece AQUI, no momento em que o resumo chega,
        # e não num resgate posterior: depois do prazo não há o que fazer.
        #
        # SÓ para quem autorizou. Manifestar é ato fiscal em nome do cliente:
        # sem a autorização gravada, o resumo fica pendente como sempre ficou.
        if _autorizou_ciencia(empresa["cliente_id"]):
            xmlc = _manifestar_e_rebuscar(empresa, ctx, res["chave"])
        if xmlc is None:
            gravar_resumo_nota(conn, cur, empresa, res)
            return "resumo", 0

    ni = _importar_nfe_completa(empresa, xmlc, ctx.get("cli_index"))  # resumo VIRA completa
    return "nota", ni


def _autorizou_ciencia(cliente_id):
    """O cliente autorizou a Ciência da Operação em nome dele?

    Default NÃO: a coluna nasce 0 e a regra da casa continua sendo não
    manifestar. Quem liga é uma pessoa, na tela de Status SEFAZ, e fica
    registrado quem foi e quando.
    """
    r = execute_query(
        'SELECT manifesta_ciencia FROM dfe_certificados '
        ' WHERE cliente_id = %s AND ativo = 1 LIMIT 1',
        (cliente_id,), fetch=True, fetch_one=True) or {}
    return bool(r.get('manifesta_ciencia'))


def _manifestar_e_rebuscar(empresa, ctx, chave):
    """Ciência da Operação numa nota e nova busca da completa. (xml ou None)

    Uma manifestação, uma nota — nunca em lote. Falhar aqui NÃO derruba a
    captura: o resumo segue o caminho de sempre e a nota fica pendente, que é
    exatamente onde ela estaria se este código não existisse.

    O 596 ("apresentado após o prazo") não é erro nosso e não vira alarme: é a
    nota que envelheceu antes de a empresa ser autorizada.
    """
    from utils.integrations import dfe_manifesto
    from utils.certificado_digital import carregar_par_chave_cert, decifrar_senha

    cid = empresa["cliente_id"]
    try:
        cert_row = execute_query(
            'SELECT id, dropbox_path FROM dfe_certificados '
            ' WHERE cliente_id = %s AND ativo = 1 LIMIT 1',
            (cid,), fetch=True, fetch_one=True)
        # get_senha_cifrada recebe o CLIENTE, não o id da linha do certificado.
        # Passar cert_row['id'] devolvia a senha de OUTRA empresa e estourava
        # SenhaInvalidaError — e passou no meu teste porque a Easy Petro é uma
        # das 6 (de 181) em que os dois números coincidem. Testar na empresa
        # errada prova o código por acidente (04/09/2026).
        senha = decifrar_senha(DfeCertificado.get_senha_cifrada(cid))
        pfx = dropbox_sync._service.download_file(cert_row['dropbox_path'])
        chave_priv, cert, _cadeia = carregar_par_chave_cert(pfx, senha)

        xml, ide = dfe_manifesto.montar_evento_ciencia(ctx["cnpj"], chave)
        soap = dfe_manifesto.montar_soap_evento(
            dfe_manifesto.assinar_evento(xml, ide, chave_priv, cert))
        ret_ev = dfe_manifesto.enviar_evento(ctx["sess"], soap)
    except Exception as exc:
        # VISÍVEL, e não só no log do container. Em 04/09/2026 a manifestação
        # falhava em produção e funcionava aqui: sem ciencia_ok nem ciencia_nao,
        # o rastro morria no logger.exception que ninguém lê — o MESMO defeito
        # que fez o cStat 215 ficar dois dias escondido. Erro que não aparece
        # onde se procura é erro que não existe.
        logger.exception('[dfe] falha ao manifestar ciência de %s', chave)
        # O ÚLTIMO QUADRO da pilha vai junto: "AttributeError: ... ExtensionPolicy"
        # apareceu 1 vez em 10 (04/09/2026) e a mensagem sozinha não diz de onde
        # veio — assinar isolado funciona 5/5. Sem arquivo:linha, a próxima
        # ocorrência custaria outra rodada de adivinhação.
        import traceback as _tb
        _q = _tb.extract_tb(exc.__traceback__)
        _onde = ('%s:%s em %s' % (os.path.basename(_q[-1].filename), _q[-1].lineno,
                                  _q[-1].name)) if _q else '?'
        dfe_log.registrar('ciencia_erro', cid, empresa.get('cnpj'),
                          detalhe='%s: %s | %s' % (type(exc).__name__,
                                                   str(exc)[:150], _onde))
        return None

    inf = _find(ret_ev, 'infEvento')
    cst = _text(inf, 'cStat') if inf is not None else _text(ret_ev, 'cStat')
    # 135 = registrado agora; 573 = já havia um evento igual (serve do mesmo jeito)
    if cst not in ('135', '573'):
        dfe_log.registrar('ciencia_nao', cid, empresa.get('cnpj'), c_stat=cst,
                          x_motivo=_text(inf, 'xMotivo') if inf is not None else None,
                          detalhe='Ciência recusada para %s' % chave[:20])
        return None

    dfe_log.registrar('ciencia_ok', cid, empresa.get('cnpj'), c_stat=cst, notas=1,
                      detalhe='Ciência registrada (%s) — buscando a completa de %s'
                              % (_text(inf, 'nProt') if inf is not None else '', chave[:20]))
    # Com o evento registrado, a mesma consulta que devolvia resumo agora traz
    # o nfeProc. Uma tentativa só: se não vier, o resumo fica pendente.
    try:
        return _primeiro_nfeproc(consultar_chave(ctx["sess"], ctx["cnpj"],
                                                 ctx["cuf"], chave))
    except RuntimeError:
        return None


def processar_um_doc(conn, cur, empresa, d, ctx):
    """Processa e SALVA um docZip. LEVANTA se falhar ao descompactar/parsear OU
    ao gravar o que DEVE ser gravado — nesse caso o chamador NÃO avança o cursor
    além deste NSU. Retorna (kind, n_itens) com kind in {nota, resumo, evento, outro}.

    ``ctx`` carrega a sessão mTLS + cnpj/cuf (para o consChNFe do resumo) e o
    controle de cota do ciclo (chnfe_usadas/chnfe_max, cooldown_656, cortar)."""
    b64 = d.text or ""
    xml_bytes = gzip.decompress(base64.b64decode(b64))
    root = ET.fromstring(xml_bytes)
    raiz = _local(root.tag)

    if raiz == "nfeProc":
        ni = _importar_nfe_completa(empresa, xml_bytes, ctx.get("cli_index"))
        return "nota", ni

    if raiz == "procEventoNFe":
        ev = extrair_evento(root)
        # NSU/schema vivem no envelope docZip (não no XML do evento) — só existem
        # aqui, onde ``d`` está à mão. Vão para o histórico em dfe_eventos.
        ev["nsu"] = _to_int(d.get("NSU"))
        ev["schema_dfe"] = (d.get("schema") or "")[:40] or None
        gravar_evento(conn, cur, empresa, ev, xml_bytes)
        return "evento", 0

    if raiz == "resNFe":
        return _processar_resumo(conn, cur, empresa, root, ctx)

    # resEvento / schema desconhecido: conta e segue (não trava a fila de NSU).
    # CT-e NÃO cai aqui: o NFeDistribuicaoDFe distribui só documentos de NF-e.
    # A captura de CT-e vive em utils/integrations/cte_captura.py, contra o
    # CTeDistribuicaoDFe, com cursor (dfe_nsu_cte) e cota 656 próprios.
    return "outro", 0


def _retry_pendentes(empresa, ctx):
    """Retroativo/contínuo: para as linhas resumo SEFAZ ainda incompletas desta
    empresa, busca a completa por chave (consChNFe) dentro do orçamento de cota
    RESTANTE do ciclo e faz o upgrade. Resolve os resumos antigos (29) e os que
    ficaram pendentes por teto/erro. Devolve o nº de resumos promovidos a completa."""
    # LOG DE ENTRADA. Esta função era CEGA: não registrava nada, e por isso não
    # havia como saber se os resumos pendentes estavam sendo tentados ou
    # simplesmente ignorados. Em 15/08/2026 eram 243 resumos parados na base
    # (65 da CLIRA, 21 da Petrogoiás, 6 do Easy Petro desde 10/08) sem uma única
    # linha no log explicando o porquê. Diagnóstico no escuro é o que estas
    # linhas eliminam.
    cid = empresa["cliente_id"]
    if ctx.get("cooldown_656"):
        # Metade das rodadas leva 656 — e nessas o retry NUNCA acontece. Sem
        # este registro, um resumo pode ficar meses parado parecendo esquecido.
        _n_pend = (execute_query(
            "SELECT COUNT(*) AS c FROM nfe_importacoes WHERE cliente_id=%s "
            "AND tipo='entrada' AND origem='SEFAZ' AND incompleta=1",
            (cid,), fetch=True, fetch_one=True) or {}).get('c') or 0
        if _n_pend:
            dfe_log.registrar('retry_pulado', cid, empresa.get('cnpj'),
                              detalhe=f'{_n_pend} resumo(s) pendente(s) NÃO tentados: '
                                      f'a rodada terminou em 656 (cota da SEFAZ)')
        return 0
    restante = ctx["chnfe_max"] - ctx["chnfe_usadas"]
    if restante <= 0:
        _n_pend = (execute_query(
            "SELECT COUNT(*) AS c FROM nfe_importacoes WHERE cliente_id=%s "
            "AND tipo='entrada' AND origem='SEFAZ' AND incompleta=1",
            (cid,), fetch=True, fetch_one=True) or {}).get('c') or 0
        if _n_pend:
            dfe_log.registrar('retry_pulado', cid, empresa.get('cnpj'),
                              detalhe=f'{_n_pend} resumo(s) pendente(s) NÃO tentados: '
                                      f'a cota de consulta por chave do ciclo acabou '
                                      f'({ctx["chnfe_usadas"]}/{ctx["chnfe_max"]})')
        return 0
    # A IDADE entra na fila: a Ciência só é aceita até 10 dias da emissão, e o
    # que ainda dá tempo vem PRIMEIRO — com a cota do ciclo limitada, gastar as
    # tentativas numa nota de 40 dias é perder a de 3 que ainda tinha salvação.
    pend = execute_query(
        "SELECT chave_acesso, DATEDIFF(CURDATE(), data_emissao) AS dias "
        "  FROM nfe_importacoes "
        " WHERE cliente_id=%s AND tipo='entrada' AND origem='SEFAZ' AND incompleta=1 "
        " ORDER BY (DATEDIFF(CURDATE(), data_emissao) <= 10) DESC, id "
        " LIMIT %s",
        (empresa["cliente_id"], int(restante)), fetch=True,
    ) or []
    # Lido UMA vez por rodada: a autorização não muda no meio do laço.
    pode_ciencia = _autorizou_ciencia(cid)
    manifestadas = 0
    if not pend:
        return 0
    promovidas = 0
    sem_completa = 0
    motivos = {}            # cStat da recusa -> quantas vezes
    primeiro_motivo = ''    # o xMotivo por extenso, uma amostra basta
    for row in pend:
        if ctx["chnfe_usadas"] >= ctx["chnfe_max"] or ctx.get("cooldown_656"):
            break
        chave = row["chave_acesso"]
        try:
            ret = consultar_chave(ctx["sess"], ctx["cnpj"], ctx["cuf"], chave)
        except RuntimeError:
            continue
        ctx["chnfe_usadas"] += 1
        if _text(ret, "cStat") == "656":
            ctx["cooldown_656"] = True
            break
        xmlc = _primeiro_nfeproc(ret)
        # `dias` pode ser 0 (nota de HOJE) — e `0 or 999` daria 999 em Python,
        # justamente barrando a nota mais nova de todas. Por isso o teste é
        # contra None, não pela verdade do valor.
        _dias = row.get("dias")
        if xmlc is None and pode_ciencia and _dias is not None and _dias <= 10:
            # AQUI é que o passado recente se resolve. A chave da empresa está
            # ligada e a nota ainda está dentro dos 10 dias: manifesta e busca
            # de novo. Sem este trecho a chave só protegia o que ESTIVESSE
            # CHEGANDO — e as notas já presas, mesmo no prazo, ficavam
            # esperando envelhecer (achado em 03/09/2026, depois que o Anderson
            # ligou a chave em 9 empresas e nenhuma Ciência disparou).
            xmlc = _manifestar_e_rebuscar(empresa, ctx, chave)
            if xmlc is not None:
                manifestadas += 1
        if xmlc is None:
            # A SEFAZ respondeu, mas SEM a nota completa. É o caso que mantém o
            # resumo preso: contado à parte porque tentar-e-não-vir é diagnóstico
            # diferente de nem-tentar.
            #
            # E guarda O QUE ELA DISSE. Em 02/09/2026 havia 903 resumos presos em
            # 103 empresas e 33 mil consultas por chave em uma semana sem UMA
            # promoção — e nenhuma linha registrava o motivo da recusa. Sem o
            # cStat, "sem completa disponível" é um fato sem causa: nao da para
            # saber se falta manifestacao, se a nota saiu do prazo ou se e outra
            # coisa. O texto vai AGREGADO na linha de resumo que ja existe: uma
            # linha por nota triplicaria o log (10 mil/dia) para dizer a mesma
            # coisa.
            sem_completa += 1
            motivos[(_text(ret, "cStat") or '?')] =                 motivos.get((_text(ret, "cStat") or '?'), 0) + 1
            if not primeiro_motivo:
                primeiro_motivo = (_text(ret, "xMotivo") or '')[:60]
            continue
        try:
            _importar_nfe_completa(empresa, xmlc, ctx.get("cli_index"))
            promovidas += 1
        except Exception:
            logger.exception("[dfe] falha ao promover resumo pendente %s", chave)

    # LOG DE SAÍDA: o que foi tentado e o que aconteceu. Sem isto, "6 resumos
    # parados" é um fato sem causa — e não dá para saber se o remédio é esperar,
    # mexer na cota ou buscar por outro caminho.
    dfe_log.registrar(
        'retry_pendentes', cid, empresa.get('cnpj'), docs=len(pend),
        notas=promovidas,
        detalhe=(f'{len(pend)} resumo(s) tentado(s) por chave: '
                 f'{promovidas} promovido(s) a completa, '
                 f'{sem_completa} sem completa disponível na SEFAZ'
                 + (', 656 no meio (cota)' if ctx.get('cooldown_656') else '')
                 + (' | SEFAZ: ' + ', '.join(
                     f'{k}x{v}' for k, v in sorted(motivos.items(),
                                                   key=lambda kv: -kv[1]))
                    if motivos else '')
                 + (f' | "{primeiro_motivo}"' if primeiro_motivo else '')
                 + (f' | {manifestadas} destravada(s) por Ciência' if manifestadas else '')))
    return promovidas


# ==========================================================================
# Leituras de cota / cursor (comparação de tempo SEMPRE em SQL / NOW())
# ==========================================================================
def _bloqueado_por_cota(cliente_id):
    """Retorna a linha com faltam_min se a SEFAZ ainda pediu para aguardar
    (proximo_permitido > NOW()), ou None. Comparação 100% no relógio do banco.

    Traz também ``ult_status`` para o chamador distinguir o MOTIVO do cooldown na
    mensagem ao usuário: '137 ...' = em dia (nada novo); '656 ...' = consumo
    indevido. O HH:MM da liberação sai de ``proximo_permitido`` formatado em Python
    (evita '%H' num SQL parametrizado)."""
    return execute_query(
        "SELECT proximo_permitido, "
        "TIMESTAMPDIFF(MINUTE, NOW(), proximo_permitido) AS faltam_min, "
        "ult_status "
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


def _processar_lote(conn, cur, empresa, docs, ctx, nsu_inicial):
    """Processa UM lote de docZip em ordem de NSU. Avança nsu_ok só APÓS cada doc
    salvo (retoma de onde parou se cair). Para no 1º doc que falhar OU no corte de
    656 numa busca por chave (ctx['cortar']). Devolve dict com contagens/nsu_ok/parada."""
    r = {'n_nota': 0, 'n_resumo': 0, 'n_evento': 0, 'n_outro': 0, 'n_itens': 0,
         'nsu_ok': nsu_inicial, 'parada': None}
    for d in docs:
        nsu = _to_int(d.get('NSU'))
        try:
            kind, ni = processar_um_doc(conn, cur, empresa, d, ctx)
        except Exception as exc:
            conn.rollback()
            r['parada'] = f'NSU {nsu}: {exc}'
            break
        if kind == 'nota':
            r['n_nota'] += 1
            r['n_itens'] += ni
        elif kind == 'resumo':
            r['n_resumo'] += 1
        elif kind == 'evento':
            r['n_evento'] += 1
        else:
            r['n_outro'] += 1
        r['nsu_ok'] = nsu   # só avança a marca APÓS salvar com sucesso
        if ctx['cortar']:   # 656 numa busca por chave → para o lote (resumo pendente)
            r['parada'] = f'NSU {nsu}: {ctx["motivo_corte"]}'
            break
    return r


def _resolver_certificado(cliente_id):
    """Elege UM certificado para autenticar o mTLS deste cliente.

    Ordem:
      (i)  certificado PRÓPRIO do cliente — comportamento de sempre;
      (ii) certificado de um CONTADOR vinculado. O contador tem o cert DELE,
           vinculado normalmente no cadastro dele (titular == cadastro), então
           aqui é só uma LEITURA do vínculo existente — nada de terceiro é
           gravado em dfe_certificados do cliente.

    Com vários contadores, elege o PRIMEIRO com certificado na ordem de
    ``contadores_do_cliente`` (razão social) — determinístico, sem sortear.

    Devolve ``(vinc, dono_id, rotulo)``: ``rotulo`` é None no cert próprio e
    'contador 5002 - ALBERT ...' quando veio de terceiro. ``(None, None, None)``
    quando não há certificado utilizável.
    """
    vinc = DfeCertificado.get_by_cliente(cliente_id)
    if vinc and vinc.get('dropbox_path'):
        return vinc, cliente_id, None

    for ct in ClienteContador.contadores_do_cliente(cliente_id):
        if not ct.get('tem_certificado'):
            continue
        v = DfeCertificado.get_by_cliente(ct['id'])
        if v and v.get('dropbox_path'):
            rot = (f"contador {ct.get('numero_cliente') or ct['id']} - "
                   f"{ct.get('nome_razao_social')}")
            return v, ct['id'], rot
    return None, None, None


# ==========================================================================
# Orquestração — captura de UMA rodada para 1 cliente. DRENA em multi-lote
# (padrão captura_massa do NH): enquanto vier 138, consulta em sequência.
# ==========================================================================
def capturar_cliente(cliente_id, dry_run=False, origem='manual',
                     max_lotes=1, max_seg=0):
    """Consulta o distDFeInt e (fora do dry-run) grava os documentos.

    Drenagem: enquanto ``cStat==138`` e ``ultNSU<maxNSU``, consulta lotes em
    sequência (até ``max_lotes`` ou ``max_seg``), parando em 137 (fim) ou 656
    (cota → cooldown 1h). ``max_lotes=1`` (default) mantém o comportamento antigo
    de 1 lote (usado no caminho MANUAL/síncrono); o cron agendado passa os tetos
    de drenagem. NUNCA manifesta. ``origem`` ('manual'|'agendado') vai para o log.
    """
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        return {'ok': False, 'erro': 'Cliente não encontrado.'}

    numero = (cliente.get('numero_cliente') or '').strip() or None
    razao = cliente.get('nome_razao_social') or 'SEM_NOME'
    rotulo = f"{numero + ' - ' if numero else ''}{razao}"

    # Cert PRÓPRIO ou, na falta dele, o de um CONTADOR vinculado (Parte 2).
    vinc, cert_dono_id, cert_rotulo = _resolver_certificado(cliente_id)
    if not vinc:
        return {'ok': False,
                'erro': f'O cliente {rotulo} não tem certificado digital vinculado, '
                        f'nem contador vinculado com certificado. Vincule o .pfx do '
                        f'cliente, ou um contador que já tenha o certificado dele.'}

    def _det(txt):
        """Anexa a origem do certificado ao 'detalhe' do log — só quando de terceiro."""
        if not cert_rotulo:
            return txt
        return f'{txt} | cert: {cert_rotulo}' if txt else f'cert: {cert_rotulo}'

    if cert_rotulo:
        logger.info('[dfe] cliente_id=%s (%s) usando certificado de TERCEIRO: %s',
                    cliente_id, rotulo, cert_rotulo)

    # Interessado no distDFeInt = o CNPJ da PRÓPRIA empresa (cliente), não o do
    # certificado. Importa quando o cert é da MATRIZ e o cliente é FILIAL (mesma
    # raiz): autentica o mTLS com o cert da matriz, mas consulta os documentos da
    # FILIAL. Em vínculo exato, é o mesmo CNPJ.
    #
    # O fallback histórico "usa o CNPJ do cert" só é seguro com cert PRÓPRIO (mesma
    # entidade). Com cert de CONTADOR ele consultaria os documentos PESSOAIS do
    # contador como se fossem da empresa — por isso, nesse caso, exige documento
    # próprio em vez de cair no fallback.
    cnpj = _digitos(cliente.get('cpf_cnpj'))
    if not cnpj:
        if cert_rotulo:
            return {'ok': False,
                    'erro': f'O cliente {rotulo} está sem CPF/CNPJ cadastrado. Com '
                            f'certificado de contador, o documento da empresa é '
                            f'obrigatório — sem ele a consulta traria os documentos '
                            f'do contador. Cadastre o CPF/CNPJ da empresa.'}
        cnpj = _digitos(vinc.get('cnpj'))

    # UF -> cUFAutor (nunca chuta; mensagem específica de qual cliente + o que fazer).
    uf = _uf_principal(cliente_id)
    try:
        cuf = cuf_autor(uf)
    except UfInvalidaError:
        return {'ok': False, 'sem_uf': True,
                'erro': f'O cliente {rotulo} está sem UF no endereço principal. '
                        f'Cadastre o estado (UF) no endereço principal para ativar a captura.'}

    senha_cif = DfeCertificado.get_senha_cifrada(cert_dono_id)
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
        pp = bloqueio.get('proximo_permitido')
        libera = pp.strftime('%H:%M') if hasattr(pp, 'strftime') else None
        # '137 ...' = em dia (o cooldown novo); qualquer outro (656) = consumo
        # indevido. Mensagem clara para o caminho manual — nada de "erro seco".
        em_dia = (bloqueio.get('ult_status') or '').startswith('137')
        if not dry_run:
            dfe_log.registrar('pulado_cota', cliente_id, cnpj,
                              detalhe=_det(f'faltam ~{faltam} min'), origem=origem)
        if em_dia:
            quando = f'às {libera}' if libera else f'em ~{faltam} min'
            msg = (f'Empresa em dia — nenhum documento novo por enquanto. '
                   f'A SEFAZ libera nova consulta {quando}.')
        else:
            msg = (f'A SEFAZ pediu para aguardar (consumo indevido anterior). '
                   f'Faltam ~{faltam} min para a próxima consulta deste CNPJ.')
        return {'ok': False, 'bloqueado': True, 'faltam_min': faltam,
                'em_dia': em_dia, 'libera_hhmm': libera, 'erro': msg}

    ult_nsu = _ler_ult_nsu(cliente_id)

    # Primeiro lote (a drenagem multi-lote começa a partir dele).
    sess = montar_sessao_mtls(cert, chave_priv, cadeia)
    try:
        ret, _fmt = consultar(sess, cnpj, cuf, ult_nsu)
    except RuntimeError as exc:
        if not dry_run:
            # 403 + cert vencido → 'Certificado vencido em DD/MM/AAAA' no log/painel,
            # no lugar do 'HTTP 403' cru. É a 1ª requisição da rodada — onde um cert
            # vencido falha; a drenagem (multi-lote) só roda após esta dar 200.
            dfe_log.registrar('erro', cliente_id, cnpj, ult_nsu,
                              detalhe=_det(_detalhe_403(exc, vinc.get('validade'))),
                              origem=origem)
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
            'max_nsu': ret_max, 'docs': len(docs), 'faltam': max(0, ret_max - ret_ult),
            'cert_origem': cert_rotulo or 'certificado próprio'}

    # 656 = consumo indevido: registra o cooldown (proximo_permitido = NOW()+1h)
    # SEMPRE — inclusive no dry-run —, porque a cota foi consumida de verdade na
    # SEFAZ (o "teste" não é grátis do lado deles). Mantém o cursor (ult_nsu
    # inalterado, não avança nem regride) e NÃO grava documento. O ret_ult_nsu
    # (ultNSU que a SEFAZ mandou usar) já vai na resposta para alimentar o seed.
    if cStat == '656':
        execute_query(SQL_NSU_656, (cliente_id, cnpj, ult_nsu, ret_max, status_txt), fetch=False)
        dfe_log.registrar('consulta', cliente_id, cnpj, ult_nsu, cStat, xMotivo,
                          ret_ult, ret_max, len(docs),
                          detalhe=_det('dry-run' if dry_run else None), origem=origem)
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

    # 138 (documentos) / 137 (nada novo): DRENA em multi-lote. Contexto do ciclo
    # (sessão + cota do consChNFe) é ÚNICO para a rodada toda — o teto de 30 buscas
    # por chave vale para a rodada inteira, não por lote (constraint 5: o loop de
    # distribuição não dispara milhares de consChNFe).
    empresa = {'cliente_id': cliente_id, 'numero': numero, 'razao': razao,
               'cnpj': cnpj, 'uf': uf}
    ctx = {'sess': sess, 'cnpj': cnpj, 'cuf': cuf,
           'chnfe_usadas': 0, 'chnfe_max': _MAX_CHNFE_CICLO,
           'cooldown_656': False, 'cortar': False, 'motivo_corte': None,
           # Índice CNPJ→cliente_id da base, montado 1x por rodada: resolve o
           # emitente (saída entre clientes) sem 1 query por nota.
           'cli_index': Cliente.index_cpf_cnpj()}

    tot = {'n_nota': 0, 'n_resumo': 0, 'n_evento': 0, 'n_outro': 0, 'n_itens': 0}
    nsu_ok = ult_nsu
    parada = None
    n_lotes = 0
    n_vazias = 0          # janelas 137 encadeadas neste ciclo (teto próprio)
    limite = None
    inicio = time.monotonic()

    conn = _conectar()
    try:
        cur = conn.cursor(dictionary=True)
        while True:
            n_lotes += 1
            rl = _processar_lote(conn, cur, empresa, docs, ctx, nsu_ok)
            for k in tot:
                tot[k] += rl[k]
            nsu_ok = rl['nsu_ok']
            parada = rl['parada']

            # 137 com ultNSU À FRENTE: JANELA VAZIA, e o avanço tem de ser anotado.
            #
            # O _processar_lote só move nsu_ok depois de cada documento salvo. Num
            # 137 o lote vem vazio, então nsu_ok fica onde estava — e era isso que
            # travava o cursor: a empresa reenviava o MESMO ultNSU para sempre.
            #
            # Mas 137 com ultNSU > o enviado não é "não há nada": é a SEFAZ
            # dizendo "varri até ultNSU e não há nada TEU nessa janela; peça a
            # partir daqui". Ignorar isso é revarrer a mesma faixa eternamente e
            # nunca alcançar os documentos que estão adiante — foi o que aconteceu
            # com a 152 (0 -> 50/1771 por 12h), a 547 (cte, 0 -> 50/490, 145
            # consultas), a 5002 e a 5000 (esta destravada por seed manual).
            #
            # Só avança quando o lote fechou LIMPO: com parada (documento falhou)
            # ou 656 no meio, o cursor fica onde está — pular NSU não processado
            # perderia documento em silêncio, que é o oposto do que se quer aqui.
            if (cStat == '137' and parada is None and not ctx['cooldown_656']
                    and ret_ult > nsu_ok):
                nsu_ok = ret_ult

            # Constraint 6: grava e AVANÇA o cursor ANTES do próximo lote (cai no
            # meio → retoma daqui, não reprocessa). 656 numa busca por chave grava
            # cooldown; senão libera (proximo_permitido = NULL).
            if ctx['cooldown_656']:
                execute_query(SQL_NSU_656, (cliente_id, cnpj, nsu_ok, ret_max, status_txt), fetch=False)
            else:
                execute_query(SQL_NSU_OK, (cliente_id, cnpj, nsu_ok, ret_max, status_txt), fetch=False)

            # Fim da drenagem? doc falhou / 656 (consChNFe) / fim REAL da fila.
            if parada or ctx['cooldown_656']:
                break

            # ENCADEIA JANELA VAZIA NO MESMO CICLO.
            #
            # Antes, qualquer 137 encerrava o ciclo — mesmo com fila à frente. Com
            # o cursor já avançando (correção anterior), isso significava UMA
            # janela de 50 NSU por tick do cron: a 152 levaria ~36h para varrer
            # 1771 NSU vazios. Encadear aqui resolve em minutos.
            #
            # 137 com ret_ult < ret_max NÃO é fim de fila: é "esta faixa está
            # vazia, continue". Fim de fila é ret_ult >= ret_max, e continua
            # saindo do laço como sempre saiu.
            #
            # Qualquer outra coisa cai fora e volta ao comportamento de hoje: 138
            # segue pelo caminho normal (documentos), 656 já saiu acima, erro vira
            # 'parada'. As três travas abaixo valem para os dois casos.
            vazia = (cStat == '137' and ret_ult < ret_max)
            if vazia:
                n_vazias += 1
            if cStat not in ('137', '138') or ret_ult >= ret_max:
                break                                    # fim de fila / status inesperado
            # Travas de segurança (constraint 3): teto de lotes/tempo por empresa,
            # mais o teto próprio de janelas vazias.
            if n_lotes >= max_lotes:
                limite = f'teto de {max_lotes} lote(s) na rodada'
                break
            if max_seg and (time.monotonic() - inicio) >= max_seg:
                limite = f'teto de {max_seg}s na rodada'
                break
            if n_vazias >= _MAX_JANELAS_VAZIAS:
                limite = f'teto de {_MAX_JANELAS_VAZIAS} janela(s) vazia(s) na rodada'
                break

            # Próximo lote: pausa de cortesia (NH ~0.4s) e nova consulta a partir do
            # nsu_ok. Enquanto vem 138 a SEFAZ autoriza consecutivo — não é 656.
            # Na janela vazia o cursor ANDOU (ret_ult), então o pedido seguinte é
            # por uma faixa NOVA — não é reenvio do mesmo ultNSU, que é o que a
            # SEFAZ trata como consumo indevido. Se ainda assim vier 656, ele é
            # tratado logo abaixo, com cooldown, como sempre foi.
            time.sleep(_INTERVALO_LOTE)
            try:
                ret, _fmt = consultar(sess, cnpj, cuf, nsu_ok)
            except RuntimeError as exc:
                parada = f'lote {n_lotes + 1}: {exc}'
                break
            cStat = _text(ret, 'cStat')
            xMotivo = _text(ret, 'xMotivo')
            ret_ult = _to_int(_text(ret, 'ultNSU')) or 0
            ret_max = _to_int(_text(ret, 'maxNSU')) or 0
            status_txt = f"{cStat} {xMotivo}"[:255]
            if cStat == '656':
                # 656 no meio da drenagem: cursor mantido em nsu_ok, cooldown 1h.
                execute_query(SQL_NSU_656, (cliente_id, cnpj, nsu_ok, ret_max, status_txt), fetch=False)
                ctx['cooldown_656'] = True
                parada = '656 (consumo indevido) na distribuição'
                break
            lote = _find(ret, 'loteDistDFeInt')
            docs = [e for e in (lote.iter() if lote is not None else []) if _local(e.tag) == 'docZip']
            docs.sort(key=lambda e: _to_int(e.get('NSU')) or 0)
        cur.close()
    finally:
        conn.close()

    # Retry dos resumos PENDENTES (uma vez, no fim da rodada): busca a completa por
    # chave dentro do orçamento de cota RESTANTE do ciclo (mesmo teto de 30).
    promovidas = _retry_pendentes(empresa, ctx)
    tot['n_nota'] += promovidas
    if promovidas:
        tot['n_resumo'] = max(0, tot['n_resumo'] - promovidas)
    # Se o retry disparou 656, grava o cooldown (o último cursor por-lote era NULL).
    if ctx['cooldown_656']:
        execute_query(SQL_NSU_656, (cliente_id, cnpj, nsu_ok, ret_max, status_txt), fetch=False)
    # Fim de fila (137 / em dia, sem 656): sela um cooldown de 65 min para o cron
    # (*/20) não re-consultar em ~20 min e tomar 656. Só quando NÃO paramos por teto
    # de lotes/tempo (limite is None → aí ainda há doc a drenar, retoma no próximo
    # tick sem espera) E o cursor alcançou o maxNSU (ret_ult >= ret_max = em dia).
    # O último write por-lote foi SQL_NSU_OK (proximo_permitido=NULL); este re-grava
    # o MESMO cursor só trocando o cooldown para NOW()+65min.
    elif limite is None and ret_ult >= ret_max:
        execute_query(SQL_NSU_137, (cliente_id, cnpj, nsu_ok, ret_max, status_txt), fetch=False)

    tot_docs = tot['n_nota'] + tot['n_resumo'] + tot['n_evento'] + tot['n_outro']
    dfe_log.registrar('consulta', cliente_id, cnpj, ult_nsu, cStat, xMotivo,
                      ret_ult, ret_max, tot_docs, tot['n_nota'], tot['n_evento'],
                      detalhe=_det(parada or limite), origem=origem)

    base.update({'notas': tot['n_nota'], 'resumos': tot['n_resumo'],
                 'eventos': tot['n_evento'], 'outros': tot['n_outro'],
                 'itens': tot['n_itens'], 'docs': tot_docs, 'lotes': n_lotes,
                 'novo_ult_nsu': nsu_ok, 'max_nsu': ret_max,
                 'faltam': max(0, ret_max - nsu_ok),
                 'chnfe_usadas': ctx['chnfe_usadas'], 'resumos_promovidos': promovidas,
                 'cooldown_656_chave': ctx['cooldown_656'],
                 'limite_atingido': limite, 'parada': parada})
    return base


def capturar_por_chave(cliente_id, chave, origem='manual'):
    """Reconsulta UMA nota pela CHAVE (consChNFe) para promover resumo → completa.

    É o mesmo caminho que ``_processar_resumo`` já usa dentro da captura, exposto
    para o botão "Capturar documento" da tela: mesma resolução de certificado,
    mesmo mTLS, UMA requisição (nunca em loop) e a MESMA gravação
    (``_importar_nfe_completa``). **Não manifesta e não assina nada** — o consChNFe
    é leitura pura, o mesmo verbo do distDFeInt.

    Devolve dict com:
      ok=False + bloqueado/consumo_indevido → cota; o chamador pede para tentar
        mais tarde (a cota é consumida do lado da SEFAZ mesmo sem sucesso);
      ok=True  + completa=True  → a completa entrou (ou o resumo fantasma foi
        removido porque a nota não é da empresa — nos dois casos não há mais resumo);
      ok=True  + completa=False → a SEFAZ só tem o resumo; nada mudou.
    """
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        return {'ok': False, 'erro': 'Cliente não encontrado.'}
    numero = (cliente.get('numero_cliente') or '').strip() or None
    razao = cliente.get('nome_razao_social') or 'SEM_NOME'
    rotulo = f"{numero + ' - ' if numero else ''}{razao}"

    vinc, cert_dono_id, cert_rotulo = _resolver_certificado(cliente_id)
    if not vinc:
        return {'ok': False,
                'erro': f'O cliente {rotulo} não tem certificado digital vinculado, '
                        f'nem contador vinculado com certificado.'}

    # Interessado = CNPJ da EMPRESA (não o do certificado) — mesma regra do
    # capturar_cliente: com cert de contador, cair no CNPJ do cert consultaria os
    # documentos do contador.
    cnpj = _digitos(cliente.get('cpf_cnpj'))
    if not cnpj:
        if cert_rotulo:
            return {'ok': False,
                    'erro': f'O cliente {rotulo} está sem CPF/CNPJ cadastrado. Com '
                            f'certificado de contador, o documento da empresa é '
                            f'obrigatório. Cadastre o CPF/CNPJ da empresa.'}
        cnpj = _digitos(vinc.get('cnpj'))

    uf = _uf_principal(cliente_id)
    try:
        cuf = cuf_autor(uf)
    except UfInvalidaError:
        return {'ok': False, 'sem_uf': True,
                'erro': f'O cliente {rotulo} está sem UF no endereço principal.'}

    senha_cif = DfeCertificado.get_senha_cifrada(cert_dono_id)
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

    # A busca por chave consome a MESMA cota do distDFeInt: se a SEFAZ já mandou
    # aguardar, nem tenta (senão renova o castigo).
    bloqueio = _bloqueado_por_cota(cliente_id)
    if bloqueio:
        faltam = bloqueio.get('faltam_min')
        pp = bloqueio.get('proximo_permitido')
        libera = pp.strftime('%H:%M') if hasattr(pp, 'strftime') else None
        quando = f'às {libera}' if libera else f'em ~{faltam} min'
        return {'ok': False, 'bloqueado': True, 'faltam_min': faltam,
                'erro': f'A SEFAZ pediu para aguardar. Nova consulta liberada {quando}.'}

    sess = montar_sessao_mtls(cert, chave_priv, cadeia)
    try:
        ret = consultar_chave(sess, cnpj, cuf, chave)
    except RuntimeError as exc:
        dfe_log.registrar('erro', cliente_id, cnpj, detalhe=str(exc)[:300], origem=origem)
        return {'ok': False, 'erro': f'Falha ao consultar a SEFAZ: {exc}'}

    cStat = _text(ret, 'cStat')
    xMotivo = _text(ret, 'xMotivo')
    dfe_log.registrar('cons_nsu', cliente_id, cnpj, c_stat=cStat, x_motivo=xMotivo,
                      detalhe=f'capturar por chave {chave}'[:300], origem=origem)

    if cStat == '656':
        # A cota foi consumida de verdade: sela o cooldown com o cursor ATUAL
        # (max_nsu=0 preserva o valor existente — ver _MAX_NSU).
        execute_query(SQL_NSU_656,
                      (cliente_id, cnpj, _ler_ult_nsu(cliente_id), 0,
                       f'{cStat} {xMotivo}'[:255]), fetch=False)
        return {'ok': False, 'consumo_indevido': True,
                'erro': 'A SEFAZ pediu para aguardar (consumo indevido). '
                        'Tente novamente mais tarde.'}

    empresa = {'cliente_id': cliente_id, 'numero': numero, 'razao': razao,
               'cnpj': cnpj, 'uf': uf}

    xmlc = _primeiro_nfeproc(ret)
    if xmlc is None and _autorizou_ciencia(cliente_id):
        # O BOTAO faz o mesmo que o cron: se a empresa autorizou e a nota ainda
        # está no prazo, manifesta a Ciência e busca de novo — em vez de mandar
        # a pessoa ao portal resolver captcha para uma nota que o sistema
        # conseguiria sozinho. Pedido do Anderson em 05/09/2026: "clicar em
        # capturar e o sistema puxar, para o usuário não demorar a trabalhar".
        idade = execute_query(
            'SELECT DATEDIFF(CURDATE(), data_emissao) AS dias '
            '  FROM nfe_importacoes '
            ' WHERE chave_acesso = %s AND tipo = %s LIMIT 1',
            (chave, 'entrada'), fetch=True, fetch_one=True) or {}
        dias = idade.get('dias')
        if dias is not None and dias <= 10:
            ctx = {'sess': sess, 'cnpj': cnpj, 'cuf': cuf}
            xmlc = _manifestar_e_rebuscar(empresa, ctx, chave)

    if xmlc is None:
        # Segue sem completa: a tela cai no portal, que é o caminho manual.
        # Passando de 10 dias a Ciência é recusada com 596 — e aí só o portal
        # (ou a Confirmação da Operação, que é decisão de negócio).
        return {'ok': True, 'completa': False, 'cStat': cStat, 'xMotivo': xMotivo,
                'mensagem': 'A SEFAZ ainda só tem o resumo desta nota.'}
    try:
        n_itens = _importar_nfe_completa(empresa, xmlc)
    except Exception as exc:
        logger.exception('[dfe] falha ao importar a completa da chave %s', chave)
        return {'ok': False, 'erro': f'A nota veio da SEFAZ, mas falhou ao gravar: {exc}'}

    # Confirma no banco em vez de assumir: _importar_nfe_completa pode ter concluído
    # que a nota não é da empresa (autXML) e REMOVIDO o resumo — nos dois casos não
    # sobra resumo, que é o que a tela precisa saber.
    ainda = execute_query(
        "SELECT incompleta FROM nfe_importacoes WHERE chave_acesso = %s AND origem = 'SEFAZ'",
        (chave,), fetch=True, fetch_one=True)
    completa = (ainda is None) or (int(ainda.get('incompleta') or 0) == 0)
    return {'ok': True, 'completa': completa, 'itens': n_itens,
            'cStat': cStat, 'xMotivo': xMotivo}


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
                # Cron DRENA: multi-lote até 137 (fim), 656 (cota) ou os tetos de
                # segurança por empresa (lotes/tempo). O caminho manual continua 1 lote.
                r = capturar_cliente(cid, dry_run=False, origem='agendado',
                                     max_lotes=_MAX_LOTES_CICLO, max_seg=_MAX_SEG_EMPRESA)
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
                    # Falha SEM flag = o certificado não chegou a ser usado: .pfx
                    # que não baixa do Dropbox, senha que não decifra, PFX que não
                    # abre. Sem esta linha o ciclo pulava a empresa em silêncio —
                    # foi assim que 22 empresas ficaram 18h fora do ar sem deixar
                    # um único registro no dfe_consulta_log (o caminho do .pfx
                    # tinha mudado). Mesmo formato do ramo de exceção acima.
                    logger.warning('[dfe-sched] cliente_id=%s pulado: %s',
                                   cid, r.get('erro'))
                    dfe_log.registrar('erro', cid, _digitos(emp.get('cnpj')),
                                      origem='agendado',
                                      detalhe=(r.get('erro') or 'falha não identificada')[:300])
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
