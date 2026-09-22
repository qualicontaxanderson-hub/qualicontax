# -*- coding: utf-8 -*-
"""Vigia de CNPJ: consulta a Receita a cada 24 h até os dados mudarem.

Pedido do Anderson (22/09/2026): quem cadastra uma empresa cuja alteração
ainda não chegou na base pública (que é mensal) não pode ter que voltar
"daqui a dois dias" para clicar de novo — é onde entra o esquecimento. Então:
um botão arma a vigia; o cron de manutenção consulta uma vez por dia; quando
a foto da Receita muda, a vigia PARA, guarda o que mudou, e a tela do cliente
(e a lista de clientes) avisam. Ninguém aplica nada sozinho: a razão social
está no nome da pasta do Dropbox, no fiscal, em contrato — quem aplica é a
pessoa, pelo botão "Aplicar no formulário", e depois salva.

Tabela cnpj_vigia (init_db.py): uma linha por CNPJ.
  status: vigiando | mudou | aplicada | cancelada
  foto_json/foto_hash: a impressão digital no momento do clique
  novo_json/diff_json: o que a Receita passou a dizer, e o campo a campo
"""
import json
import logging
import time
from datetime import datetime

from utils.db_helper import execute_query
from utils import cnpj_receita as RF

logger = logging.getLogger(__name__)

INTERVALO_H = 24        # entre uma consulta e a seguinte, quando nada mudou
RETENTATIVA_H = 6       # quando a fonte falhou
LOTE = 20               # vigias por rodada do cron


def _j(v):
    try:
        return json.loads(v) if isinstance(v, str) and v else (v or {})
    except ValueError:
        return {}


def por_cnpj(cnpj):
    cnpj = RF.so_digitos(cnpj)
    if len(cnpj) != 14:
        return None
    r = execute_query("SELECT * FROM cnpj_vigia WHERE cnpj = %s", (cnpj,), fetch=True, fetch_one=True)
    return _serializa(r) if r else None


def _serializa(r):
    out = dict(r)
    for k in ('pedido_em', 'ultima_consulta_em', 'proxima_em', 'mudou_em', 'aplicado_em'):
        v = out.get(k)
        out[k] = v.strftime('%Y-%m-%d %H:%M') if isinstance(v, datetime) else (str(v) if v else None)
    out['diff'] = _j(out.pop('diff_json', None)) or []
    out['novo'] = _j(out.pop('novo_json', None)) or None
    out.pop('foto_json', None)
    return out


def armar(cnpj, cliente_id, usuario_id, usuario_nome):
    """Consulta AGORA (é a foto de referência) e arma/rearma a vigia.
    Devolve (vigia serializada, dados da consulta, fonte)."""
    cnpj = RF.so_digitos(cnpj)
    dados, fonte = RF.consultar(cnpj)
    foto, h = RF.impressao(dados)
    execute_query(
        """INSERT INTO cnpj_vigia (cnpj, cliente_id, pedido_por, pedido_por_nome, pedido_em,
                                   foto_json, foto_hash, foto_fonte, status, consultas, ultima_consulta_em,
                                   proxima_em, mudou_em, novo_json, diff_json, aplicado_em, erro_ultimo)
           VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s, 'vigiando', 0, NOW(),
                   DATE_ADD(NOW(), INTERVAL %s HOUR), NULL, NULL, NULL, NULL, NULL)
           ON DUPLICATE KEY UPDATE
                cliente_id = COALESCE(VALUES(cliente_id), cliente_id),
                pedido_por = VALUES(pedido_por), pedido_por_nome = VALUES(pedido_por_nome),
                pedido_em = NOW(), foto_json = VALUES(foto_json), foto_hash = VALUES(foto_hash),
                foto_fonte = VALUES(foto_fonte),
                status = 'vigiando', consultas = 0, ultima_consulta_em = NOW(),
                proxima_em = DATE_ADD(NOW(), INTERVAL %s HOUR),
                mudou_em = NULL, novo_json = NULL, diff_json = NULL, aplicado_em = NULL, erro_ultimo = NULL""",
        (cnpj, cliente_id, usuario_id, usuario_nome, json.dumps(foto, ensure_ascii=False), h, fonte,
         INTERVALO_H, INTERVALO_H), fetch=False)
    return por_cnpj(cnpj), dados, fonte


def mudar_status(cnpj, status):
    cnpj = RF.so_digitos(cnpj)
    col = 'aplicado_em = NOW(),' if status == 'aplicada' else ''
    execute_query(f"UPDATE cnpj_vigia SET {col} status = %s WHERE cnpj = %s", (status, cnpj), fetch=False)
    return por_cnpj(cnpj)


def vincular_cliente(cnpj, cliente_id):
    """Quando o cadastro novo é salvo, a vigia armada antes ganha o cliente_id."""
    cnpj = RF.so_digitos(cnpj)
    if len(cnpj) == 14 and cliente_id:
        execute_query("UPDATE cnpj_vigia SET cliente_id = %s WHERE cnpj = %s AND cliente_id IS NULL",
                      (int(cliente_id), cnpj), fetch=False)


def com_novidade():
    """Vigias que acharam mudança e ninguém aplicou/ignorou — para a lista de clientes."""
    rows = execute_query(
        """SELECT v.cnpj, v.cliente_id, v.mudou_em, v.diff_json, c.numero_cliente, c.nome_razao_social
             FROM cnpj_vigia v LEFT JOIN clientes c ON c.id = v.cliente_id
            WHERE v.status = 'mudou' ORDER BY v.mudou_em DESC""", fetch=True) or []
    out = []
    for r in rows:
        d = _j(r.get('diff_json')) or []
        out.append({'cnpj': r['cnpj'], 'cliente_id': r['cliente_id'], 'numero': r.get('numero_cliente'),
                    'nome': r.get('nome_razao_social') or r['cnpj'],
                    'mudou_em': r['mudou_em'].strftime('%d/%m/%Y') if isinstance(r['mudou_em'], datetime) else '',
                    'campos': [x['rotulo'] for x in d][:4], 'n_campos': len(d)})
    return out


def vigiar(prazo_seg=30, lote=LOTE, dry=False):
    """Uma passada pelas vigias vencidas. Resumo para o manutencao_status."""
    t0 = time.monotonic()
    res = {'consultadas': 0, 'mudaram': 0, 'falhas': 0, 'dry': bool(dry)}
    pend = execute_query(
        """SELECT id, cnpj, foto_json, foto_hash, foto_fonte FROM cnpj_vigia
            WHERE status = 'vigiando' AND (proxima_em IS NULL OR proxima_em <= NOW())
            ORDER BY proxima_em LIMIT %s""", (int(lote),), fetch=True)
    if pend is None:
        res['erro'] = 'consulta das vigias cortada'
        return res
    res['pendentes'] = len(pend)
    for v in pend:
        if time.monotonic() - t0 > prazo_seg:
            res['sem_tempo'] = True
            break
        try:
            dados, fonte = RF.consultar(v['cnpj'])
        except RF.CnpjNaoEncontrado:
            # a base pública não conhece mais o CNPJ (baixa?) — é mudança, e das grandes
            dados, fonte = {'situacao_cadastral': 'NÃO ENCONTRADO NA BASE'}, 'nenhuma'
        except Exception as e:
            res['falhas'] += 1
            if not dry:
                execute_query(
                    "UPDATE cnpj_vigia SET erro_ultimo = %s, ultima_consulta_em = NOW(), "
                    "proxima_em = DATE_ADD(NOW(), INTERVAL %s HOUR) WHERE id = %s",
                    (str(e)[:250], RETENTATIVA_H, v['id']), fetch=False)
            continue
        res['consultadas'] += 1
        foto, h = RF.impressao(dados)
        if fonte != 'nenhuma' and v.get('foto_fonte') and fonte != v['foto_fonte']:
            # Fonte diferente da foto de referência: a BrasilAPI oculta endereço
            # e contato de MEI, a ReceitaWS não — comparar as duas daria um
            # "mudou" falso. Não julga; conta a consulta e espera o dia seguinte.
            res['fonte_diferente'] = res.get('fonte_diferente', 0) + 1
            if not dry:
                execute_query(
                    """UPDATE cnpj_vigia SET consultas = consultas + 1, ultima_consulta_em = NOW(),
                              proxima_em = DATE_ADD(NOW(), INTERVAL %s HOUR),
                              erro_ultimo = %s WHERE id = %s""",
                    (INTERVALO_H, 'fonte %s respondeu; a foto e da %s - sem comparar' % (fonte, v['foto_fonte']), v['id']),
                    fetch=False)
            time.sleep(1.0)
            continue
        if h != v['foto_hash']:
            diff = RF.diferencas(_j(v['foto_json']), foto)
            res['mudaram'] += 1
            logger.warning('[vigia] CNPJ %s MUDOU na Receita (%s): %s', v['cnpj'], fonte,
                           ', '.join(d['rotulo'] for d in diff))
            if not dry:
                execute_query(
                    """UPDATE cnpj_vigia SET status = 'mudou', mudou_em = NOW(), consultas = consultas + 1,
                              ultima_consulta_em = NOW(), proxima_em = NULL, erro_ultimo = NULL,
                              novo_json = %s, diff_json = %s WHERE id = %s""",
                    (json.dumps(dados, ensure_ascii=False), json.dumps(diff, ensure_ascii=False), v['id']),
                    fetch=False)
        elif not dry:
            execute_query(
                """UPDATE cnpj_vigia SET consultas = consultas + 1, ultima_consulta_em = NOW(),
                          proxima_em = DATE_ADD(NOW(), INTERVAL %s HOUR), erro_ultimo = NULL WHERE id = %s""",
                (INTERVALO_H, v['id']), fetch=False)
        time.sleep(1.0)   # gentileza com as fontes públicas
    res['seg'] = round(time.monotonic() - t0, 1)
    return res
