# -*- coding: utf-8 -*-
"""Compromissos do Comercial por pessoa (26/11/2026).

Anderson: "Quando tiver um contrato, orçamento ou proposta pendente aparecer o sinal de pendente para o usuário aprovar
ou enviar e-mail!! Exemplo: Anderson aprova orçamento e Guilherme não enviou ainda, aparece pra ele esse compromisso!!"
"E pro Anderson também" (admin não fica de fora).

Duas listas, somadas no selo do menu:
- para quem APROVA (admin ou perfil com a permissão): o que espera aprovação — orçamentos, contratos e propostas;
- para quem MONTOU: o que foi aprovado e ainda não foi mandado (orçamento não enviado ao cliente, proposta não
  disparada, contrato não enviado para assinatura), a proposta aceita que ainda não virou contrato e o orçamento
  devolvido para corrigir.

Cada item: {'tipo', 'icone', 'cor', 'titulo', 'texto', 'acao', 'url', 'quando'}. Lista curta por pessoa, calculada
uma vez a cada PENDENCIAS_SEGUNDOS (o selo aparece em todas as páginas).
"""
import logging
import time
from decimal import Decimal

from flask import url_for

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
PENDENCIAS_SEGUNDOS = 45
_CACHE = {}          # usuario_id -> (quando, lista)
APROVAR_ORC = 'comercial.orcamentos.aprovar'
APROVAR_CTR = 'financeiro.lancamentos.aprovar'


def _brl(v):
    v = Decimal(str(v or 0)).quantize(Decimal('0.01'))
    s = '{:,.2f}'.format(v).replace(',', 'X').replace('.', ',').replace('X', '.')
    return 'R$ ' + s


def _q(sql, p=()):
    return execute_query(sql, p, fetch=True) or []


def pendencias(usuario, fresco=False):
    """Lista de compromissos da pessoa logada (vazia para quem não tem nada ou não é da equipe)."""
    uid = getattr(usuario, 'id', None)
    if not uid:
        return []
    agora = time.time()
    if not fresco and uid in _CACHE and agora - _CACHE[uid][0] < PENDENCIAS_SEGUNDOS:
        return _CACHE[uid][1]
    try:
        lista = _calcular(usuario)
    except Exception:
        logger.exception('[comercial] compromissos de %s', uid)
        lista = []
    _CACHE[uid] = (agora, lista)
    return lista


def esquecer(usuario_id=None):
    """Depois de aprovar/enviar, o selo muda na hora (sem esperar o cache)."""
    if usuario_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(usuario_id, None)


def _calcular(u):
    uid = u.id
    admin = u.is_admin()
    aprova_orc = admin or u.has_permission(APROVAR_ORC)
    aprova_ctr = admin or u.has_permission(APROVAR_CTR)
    out = []
    # ── para quem aprova ──
    if aprova_orc:
        for o in _q("""SELECT o.id, o.numero, o.total, o.criado_em, c.nome_razao_social AS empresa, u.nome AS quem
                         FROM com_orcamentos o JOIN clientes c ON c.id = o.cliente_id LEFT JOIN usuarios u ON u.id = o.criado_por
                        WHERE o.status = 'APROVACAO' ORDER BY o.criado_em"""):
            out.append({'tipo': 'orcamento', 'icone': 'fa-stamp', 'cor': 'laranja', 'titulo': 'Aprovar o orçamento %s' % o['numero'],
                        'texto': '%s · %s · montado por %s' % (o['empresa'], _brl(o['total']), (o['quem'] or '').split(' ')[0]),
                        'acao': 'Aprovar', 'url': url_for('comercial.orcamentos', abrir=o['id'], aba='APROVACAO'), 'quando': o['criado_em']})
    if aprova_ctr:
        for c in _q("""SELECT p.id, p.numero, p.tipo, p.criado_em, c.nome_razao_social AS empresa, u.nome AS quem,
                              JSON_UNQUOTE(JSON_EXTRACT(p.dados, '$.valor')) AS valor
                         FROM prest_contratos p JOIN clientes c ON c.id = p.cliente_id LEFT JOIN usuarios u ON u.id = p.criado_por
                        WHERE p.status = 'APROVACAO' ORDER BY p.criado_em"""):
            prop = (c['tipo'] or 'CONTRATO') == 'PROPOSTA'
            out.append({'tipo': 'proposta' if prop else 'contrato', 'icone': 'fa-stamp', 'cor': 'laranja',
                        'titulo': 'Aprovar %s %s' % ('a proposta' if prop else 'o contrato', c['numero'] or c['id']),
                        'texto': '%s%s · montado por %s' % (c['empresa'], (' · %s/mês' % _brl(c['valor'])) if c.get('valor') else '', (c['quem'] or '').split(' ')[0]),
                        'acao': 'Aprovar', 'url': url_for('comercial.proposta_ver', pid=c['id']) if prop else url_for('financeiro.contrato_ver', cid=c['id']), 'quando': c['criado_em']})
    # ── para quem montou (o admin também) ──
    for o in _q("""SELECT o.id, o.numero, o.status, o.total, o.motivo, COALESCE(o.aprovado_em, o.atualizado_em, o.criado_em) AS quando, c.nome_razao_social AS empresa
                     FROM com_orcamentos o JOIN clientes c ON c.id = o.cliente_id
                    WHERE o.criado_por = %s AND ((o.status = 'APROVADO' AND o.interno = 0 AND o.enviado_em IS NULL) OR o.status = 'REPROVADO')
                    ORDER BY quando""", (uid,)):
        if o['status'] == 'REPROVADO':
            out.append({'tipo': 'orcamento', 'icone': 'fa-rotate-left', 'cor': 'vermelho', 'titulo': 'Corrigir o orçamento %s' % o['numero'],
                        'texto': '%s · devolvido%s' % (o['empresa'], (': ' + o['motivo']) if o.get('motivo') else ''), 'acao': 'Corrigir',
                        'url': url_for('comercial.orcamentos', abrir=o['id']), 'quando': o['quando']})
        else:
            out.append({'tipo': 'orcamento', 'icone': 'fa-paper-plane', 'cor': 'azul', 'titulo': 'Mandar ao cliente o orçamento %s' % o['numero'],
                        'texto': '%s · %s · aprovado, o cliente ainda não recebeu' % (o['empresa'], _brl(o['total'])), 'acao': 'Enviar',
                        'url': url_for('comercial.orcamentos', abrir=o['id'], aba='APROVADO'), 'quando': o['quando']})
    for c in _q("""SELECT p.id, p.numero, p.tipo, p.status, COALESCE(p.atualizado_em, p.criado_em) AS quando, c.nome_razao_social AS empresa
                     FROM prest_contratos p JOIN clientes c ON c.id = p.cliente_id
                    WHERE p.criado_por = %s AND (p.status = 'APROVADO' OR (COALESCE(p.tipo, 'CONTRATO') = 'PROPOSTA' AND p.status = 'ACEITO'))
                    ORDER BY quando""", (uid,)):
        prop = (c['tipo'] or 'CONTRATO') == 'PROPOSTA'
        num = c['numero'] or c['id']
        if prop and c['status'] == 'ACEITO':
            out.append({'tipo': 'proposta', 'icone': 'fa-file-contract', 'cor': 'roxo', 'titulo': 'Converter em contrato a proposta %s' % num,
                        'texto': '%s · o cliente aceitou' % c['empresa'], 'acao': 'Converter', 'url': url_for('comercial.proposta_ver', pid=c['id']), 'quando': c['quando']})
        elif prop:
            out.append({'tipo': 'proposta', 'icone': 'fa-paper-plane', 'cor': 'azul', 'titulo': 'Disparar ao cliente a proposta %s' % num,
                        'texto': '%s · aprovada, ainda não foi' % c['empresa'], 'acao': 'Disparar', 'url': url_for('comercial.proposta_ver', pid=c['id']), 'quando': c['quando']})
        else:
            out.append({'tipo': 'contrato', 'icone': 'fa-file-signature', 'cor': 'azul', 'titulo': 'Enviar para assinatura o contrato %s' % num,
                        'texto': '%s · aprovado, ninguém recebeu o link ainda' % c['empresa'], 'acao': 'Enviar', 'url': url_for('financeiro.contrato_ver', cid=c['id']), 'quando': c['quando']})
    out.sort(key=lambda x: x.get('quando') or 0)
    return out
