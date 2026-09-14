# -*- coding: utf-8 -*-
"""Etapa 2 da regra "documento no Dropbox, dado no banco" (decisão de 12/09/2026).

A REGRA
-------
O XML de uma nota fica no banco por **3 meses contados da data de emissão**.
Depois disso ele vive **só no Dropbox** — a linha da nota continua no banco
com todos os dados (número, valores, CFOP, emitente...), e quem precisa do
documento (olhinho, PDF, lote) já sabe buscá-lo no Dropbox desde a etapa 1
(``utils/nfe_xml_fonte.py``).

Por que: ``nfe_importacoes`` tinha 9,4 GB em 13/09/2026, 97% disso XML
inline, crescendo ~1 GB por noite com os robôs. Toda consulta que lê a linha
paga esse peso. Sem XML a linha tem ~200 bytes.

DUAS PEÇAS, NESTA ORDEM
-----------------------
1. **Confirmar** (``confirmar_pastas``): nota antiga sem ``xml_caminho`` NÃO é
   expurgada às cegas. Lista-se a pasta da convenção
   ``EMPRESAS/{n} - razão/FISCAL/{ENTRADAS|SAIDAS}/{ano}/{MM}`` UMA vez e
   casa-se ``{chave}.xml`` (robô) ou ``nome_arquivo`` (SEFAZ/roteador). Achou →
   grava ``xml_caminho``. Uma chamada ao Dropbox por pasta, não por nota.
2. **Expurgar** (``expurgar_nfe``/``_eventos``/``_cte``): só linha com
   ``xml_caminho`` preenchido E emissão anterior ao corte. Um UPDATE por lote
   que preenche ``hora_emissao`` (extraída do XML) e esvazia ``xml_raw`` na
   mesma instrução. A linha nunca é apagada.

Os dois andam por MARCADORES gravados em ``app_config`` (posição da última
pasta / último id), então cada rodada faz um pedaço e a seguinte continua;
ao chegar ao fim, recomeça — o que foi confirmado ou subiu pelo arquivador
depois entra na volta seguinte.

``dry=True`` (padrão) só conta e loga. Nada é escrito.
"""
import logging
import os
import time
from datetime import date

logger = logging.getLogger(__name__)

#: Meses de XML no banco, contados da EMISSÃO. Decisão (B) de 12/09/2026.
MESES = max(1, int(os.getenv('EXPURGO_MESES', '3')))
SQL_CORTE = f"(CURDATE() - INTERVAL {MESES} MONTH)"

#: Linhas lidas por página do expurgo e ids por UPDATE.
PAGINA = 2000
LOTE = 500

MARCA_CONFIRMACAO = 'manutencao_confirmacao'
MARCA_EXPURGO = {'nfe': 'manutencao_expurgo_nfe',
                 'eventos': 'manutencao_expurgo_eventos',
                 'cte': 'manutencao_expurgo_cte'}

#: Hora de emissão a partir do XML, no SQL: '<dhEmi>2026-09-12T14:33:00-03:00'
#: -> chars 12..19 = '14:33:00'. Sem <dhEmi> (resumo) fica NULL.
SQL_HORA_DO_XML = ("CASE WHEN LOCATE('<dhEmi>', xml_raw) > 0 THEN "
                   "SUBSTRING(SUBSTRING_INDEX(SUBSTRING_INDEX(xml_raw,'<dhEmi>',-1),'</dhEmi>',1),12,8) END")


def _ler(chave):
    from utils.painel_cache import ler
    v, _ = ler(chave)
    return v or {}


def _guardar(chave, valor):
    from utils.painel_cache import guardar
    guardar(chave, valor)


# ---------------------------------------------------------------------------
# 1) CONFIRMAR: pasta a pasta
# ---------------------------------------------------------------------------
def _grupos_antigos():
    """(cliente_id, tipo, ano, mes, n) das notas antigas — index-only (idx_lista)."""
    from utils.db_helper import execute_query
    return execute_query(
        f"SELECT cliente_id, tipo, YEAR(data_emissao) ano, MONTH(data_emissao) mes, COUNT(*) n "
        f"  FROM nfe_importacoes WHERE cliente_id IS NOT NULL AND data_emissao < {SQL_CORTE} "
        f" GROUP BY cliente_id, tipo, YEAR(data_emissao), MONTH(data_emissao) "
        f" ORDER BY cliente_id, tipo, ano, mes", fetch=True) or []


def confirmar_pastas(svc, max_pastas=30, prazo_seg=150, dry=True):
    """Grava ``xml_caminho`` nas notas antigas cujo arquivo está na pasta da convenção.

    Devolve contagens: pastas vistas, notas conferidas, confirmadas, sem arquivo.
    """
    from utils.db_helper import execute_query, execute_many
    r = {'pastas': 0, 'conferidas': 0, 'confirmadas': 0, 'sem_arquivo': 0, 'fim_da_volta': False}
    if not svc.is_configured():
        return r
    t0 = time.monotonic()
    grupos = _grupos_antigos()
    if not grupos:
        return r
    marca = _ler(MARCA_CONFIRMACAO).get('pos')          # [cliente_id, tipo, ano, mes] da última pasta feita
    pos = tuple(marca) if marca else None
    clientes = {c['id']: c for c in (execute_query(
        "SELECT id, numero_cliente, nome_razao_social FROM clientes", fetch=True) or [])}
    feitas = 0
    ultima = None
    for g in grupos:
        chave = (g['cliente_id'], g['tipo'], int(g['ano']), int(g['mes']))
        if pos and chave <= pos:
            continue
        if feitas >= max_pastas or (time.monotonic() - t0) > prazo_seg:
            break
        ultima = chave
        feitas += 1
        cli = clientes.get(g['cliente_id'])
        if not cli:
            continue
        pasta = svc.pasta_fiscal(cli.get('nome_razao_social') or 'SEM_NOME', chave[2], chave[3],
                                 'SAIDAS' if g['tipo'] == 'saida' else 'ENTRADAS',
                                 (cli.get('numero_cliente') or '').strip() or None)
        try:
            nomes = {i['name'].lower(): i['path'] for i in svc.list_folder(pasta) if i.get('is_file')}
        except Exception as exc:                      # pasta inexistente ou Dropbox fora: fica para a próxima volta
            logger.info('[expurgo] pasta %s não listada (%s); segue.', pasta, str(exc)[:80])
            continue
        r['pastas'] += 1
        ini = date(chave[2], chave[3], 1)
        fim = date(chave[2] + (chave[3] == 12), (chave[3] % 12) + 1, 1)
        rows = execute_query(
            "SELECT id, chave_acesso, nome_arquivo FROM nfe_importacoes "
            " WHERE cliente_id = %s AND tipo = %s AND data_emissao >= %s AND data_emissao < %s "
            "   AND (xml_caminho IS NULL OR xml_caminho = '')",
            (g['cliente_id'], g['tipo'], ini, fim), fetch=True) or []
        achados = []
        for n in rows:
            r['conferidas'] += 1
            cands = [f"{n['chave_acesso']}.xml".lower()]
            if n.get('nome_arquivo'):
                cands.append(n['nome_arquivo'].strip().lower())
            caminho = next((nomes[c] for c in cands if c in nomes), None)
            if caminho:
                achados.append((caminho, n['id']))
            else:
                r['sem_arquivo'] += 1
        if achados and not dry:
            execute_many("UPDATE nfe_importacoes SET xml_caminho = %s WHERE id = %s", achados)
        r['confirmadas'] += len(achados)
    if ultima is not None:
        acabou = ultima == (grupos[-1]['cliente_id'], grupos[-1]['tipo'], int(grupos[-1]['ano']), int(grupos[-1]['mes']))
        r['fim_da_volta'] = acabou
        if not dry:
            _guardar(MARCA_CONFIRMACAO, {'pos': None if acabou else list(ultima)})
    return r


# ---------------------------------------------------------------------------
# 2) EXPURGAR: por id, em páginas
# ---------------------------------------------------------------------------
def _expurgar(tabela, col_data, marca_chave, prazo_seg, dry, sql_extra_set=''):
    """Motor comum: anda por id, esvazia ``xml_raw`` de quem tem ``xml_caminho``
    e ``col_data`` anterior ao corte. Devolve contagens e bytes liberados."""
    from utils.db_helper import execute_query
    r = {'lidas': 0, 'expurgadas': 0, 'mb': 0.0, 'fim_da_volta': False}
    t0 = time.monotonic()
    ultimo = int(_ler(marca_chave).get('id') or 0)
    while (time.monotonic() - t0) < prazo_seg:
        pagina = execute_query(
            f"SELECT id, LENGTH(xml_raw) l, (COALESCE(xml_caminho,'') <> '') c "
            f"  FROM {tabela} FORCE INDEX (PRIMARY) "
            f" WHERE id > %s AND {col_data} < {SQL_CORTE} ORDER BY id LIMIT {PAGINA}",
            (ultimo,), fetch=True) or []
        if not pagina:
            r['fim_da_volta'] = True
            ultimo = 0
            break
        r['lidas'] += len(pagina)
        elegiveis = [p for p in pagina if p['c'] and (p['l'] or 0) > 0]
        for i in range(0, len(elegiveis), LOTE):
            lote = elegiveis[i:i + LOTE]
            ids = [p['id'] for p in lote]
            if not dry:
                ph = ','.join(['%s'] * len(ids))
                execute_query(
                    f"UPDATE {tabela} SET {sql_extra_set}xml_raw = '' "
                    f" WHERE id IN ({ph}) AND COALESCE(xml_caminho,'') <> '' AND {col_data} < {SQL_CORTE}",
                    tuple(ids), fetch=False)
            r['expurgadas'] += len(ids)
            r['mb'] += sum(p['l'] or 0 for p in lote) / 1024 / 1024
        ultimo = pagina[-1]['id']
        if len(pagina) < PAGINA:
            r['fim_da_volta'] = True
            ultimo = 0
            break
    if not dry:
        _guardar(marca_chave, {'id': ultimo})
    r['mb'] = round(r['mb'], 1)
    return r


def expurgar_nfe(prazo_seg=200, dry=True):
    """NF-e: preenche ``hora_emissao`` a partir do XML e esvazia o XML."""
    return _expurgar('nfe_importacoes', 'data_emissao', MARCA_EXPURGO['nfe'], prazo_seg, dry,
                     sql_extra_set=f"hora_emissao = COALESCE(hora_emissao, {SQL_HORA_DO_XML}), ")


def expurgar_eventos(prazo_seg=60, dry=True):
    """dfe_eventos: pela data do EVENTO."""
    return _expurgar('dfe_eventos', 'dh_evento', MARCA_EXPURGO['eventos'], prazo_seg, dry)


def expurgar_cte(prazo_seg=60, dry=True):
    """cte_documentos: pela data de emissão do CT-e."""
    return _expurgar('cte_documentos', 'data_emissao', MARCA_EXPURGO['cte'], prazo_seg, dry)
