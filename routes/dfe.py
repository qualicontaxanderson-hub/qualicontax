# -*- coding: utf-8 -*-
"""
Rotas de captura de DFe por cliente — disparo MANUAL.

SÓ LEITURA. NUNCA MANIFESTA (só distribuição por NSU).

NF-e (NFeDistribuicaoDFe):
- POST /clientes/<id>/dfe/consultar  -> DRY-RUN (não grava/sobe/avança cursor)
- POST /clientes/<id>/dfe/capturar   -> captura real (1 lote)
- POST /clientes/<id>/dfe/seed-nsu   -> semeia o cursor (admin)

CT-e (CTeDistribuicaoDFe — outro webservice, cursor e cota independentes):
- POST /clientes/<id>/cte/consultar  -> DRY-RUN
- POST /clientes/<id>/cte/capturar   -> captura real (1 lote)
- POST /clientes/<id>/cte/seed-nsu   -> semeia o cursor de CT-e (admin)
- POST /clientes/<id>/cte/buscar-nsu -> consNSU pontual, um NSU que faltou (admin)
"""
import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user

from utils.auth_helper import login_required
from models.cliente import Cliente
from utils.integrations import dfe_captura
from utils.integrations import cte_captura

dfe_bp = Blueprint('dfe', __name__)
logger = logging.getLogger(__name__)


def _is_admin():
    return getattr(current_user, 'is_admin', lambda: False)()


def _quem():
    return (getattr(current_user, 'nome', None) or getattr(current_user, 'login', None)
            or getattr(current_user, 'email', None) or '?')


def _executar(cliente_id, dry_run, servico='nfe'):
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        return jsonify({'ok': False, 'erro': 'Cliente não encontrado.'}), 404
    captura = (cte_captura.capturar_cte_cliente if servico == 'cte'
               else dfe_captura.capturar_cliente)
    try:
        resultado = captura(cliente_id, dry_run=dry_run)
    except Exception as exc:  # rede/cert/parse inesperado — não deixa vazar stacktrace
        logger.exception('[%s] captura falhou (cliente_id=%s, dry_run=%s)',
                         servico, cliente_id, dry_run)
        return jsonify({'ok': False, 'erro': f'Erro inesperado na captura: {exc}'}), 500
    return jsonify(resultado), 200


@dfe_bp.route('/clientes/<int:id>/dfe/consultar', methods=['POST'])
@login_required
def dfe_consultar(id):
    """DRY-RUN: consulta e mostra o que viria, sem gravar nada nem avançar o NSU."""
    return _executar(id, dry_run=True)


@dfe_bp.route('/clientes/<int:id>/dfe/capturar', methods=['POST'])
@login_required
def dfe_capturar(id):
    """Captura real de UM lote: grava documentos/itens/eventos e avança o cursor."""
    return _executar(id, dry_run=False)


@dfe_bp.route('/clientes/<int:id>/dfe/seed-nsu', methods=['POST'])
@login_required
def dfe_seed_nsu(id):
    """Semeia manualmente o cursor de NSU (só ADMIN). Ação consciente e rastreada
    para o caso de fresh-start com histórico já consumido por outro sistema."""
    if not _is_admin():
        return jsonify({'ok': False, 'erro': 'Apenas administradores podem semear o cursor de NSU.'}), 403
    data = request.get_json(silent=True) or request.form
    resultado = dfe_captura.seed_ult_nsu(id, data.get('nsu'), usuario_label=_quem())
    return jsonify(resultado), (200 if resultado.get('ok') else 400)


# ---------------------------------------------------------------------------
# CT-e — CTeDistribuicaoDFe. Cursor (dfe_nsu_cte) e cota 656 INDEPENDENTES da
# NF-e: capturar CT-e não gasta a cota de NF-e e vice-versa.
# ---------------------------------------------------------------------------
@dfe_bp.route('/clientes/<int:id>/cte/consultar', methods=['POST'])
@login_required
def cte_consultar(id):
    """DRY-RUN de CT-e: mostra o que viria (NSU, schema, raiz, chave), sem gravar
    nada nem avançar o cursor. Consome 1 requisição da cota de CT-e."""
    return _executar(id, dry_run=True, servico='cte')


@dfe_bp.route('/clientes/<int:id>/cte/capturar', methods=['POST'])
@login_required
def cte_capturar(id):
    """Captura real de UM lote de CT-e: grava cte_documentos/cte_nfe, arquiva os
    XML no Dropbox e avança o cursor."""
    return _executar(id, dry_run=False, servico='cte')


@dfe_bp.route('/clientes/<int:id>/cte/seed-nsu', methods=['POST'])
@login_required
def cte_seed_nsu(id):
    """Semeia manualmente o cursor de NSU de CT-e (só ADMIN). PULA os documentos
    anteriores ao NSU informado — mesma ressalva do seed de NF-e."""
    if not _is_admin():
        return jsonify({'ok': False, 'erro': 'Apenas administradores podem semear o cursor de NSU.'}), 403
    data = request.get_json(silent=True) or request.form
    resultado = cte_captura.seed_ult_nsu_cte(id, data.get('nsu'), usuario_label=_quem())
    return jsonify(resultado), (200 if resultado.get('ok') else 400)


@dfe_bp.route('/clientes/<int:id>/cte/buscar-nsu', methods=['POST'])
@login_required
def cte_buscar_nsu(id):
    """consNSU: recupera UM NSU de CT-e que ficou para trás (só ADMIN).

    É o análogo do consChNFe da NF-e no papel de recuperação — o CT-e não permite
    consulta por chave. Não mexe no cursor; consome 1 requisição da cota.
    """
    if not _is_admin():
        return jsonify({'ok': False, 'erro': 'Apenas administradores podem buscar um NSU pontual.'}), 403
    data = request.get_json(silent=True) or request.form
    resultado = cte_captura.buscar_nsu(id, data.get('nsu'))
    return jsonify(resultado), (200 if resultado.get('ok') else 400)


@dfe_bp.route('/dfe/sched-status', methods=['GET'])
@login_required
def dfe_sched_status():
    """Diagnóstico do scheduler (só ADMIN): o que ESTE worker enxerga.

    Responde três perguntas, em ordem de confiança:
      1. A env DFE_SCHED_ATIVO chegou no container? (confiável em qualquer worker)
      2. O scheduler está rodando neste worker? (só verdade no dono do lock)
      3. Quais jobs estão registrados e quando disparam? (idem)

    Com --workers 4 no Procfile, a requisição cai em um worker aleatório:
    recarregue algumas vezes e observe o campo `pid`.
    """
    if not getattr(current_user, 'is_admin', lambda: False)():
        return jsonify({'ok': False, 'erro': 'Apenas administradores.'}), 403
    from utils import scheduler as _sched
    return jsonify({'ok': True, **_sched.status()}), 200
