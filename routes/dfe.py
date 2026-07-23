# -*- coding: utf-8 -*-
"""
Rotas de captura de DFe (NF-e) por cliente — disparo MANUAL.

SÓ LEITURA. NUNCA MANIFESTA (só distribuição por NSU / distDFeInt).
- POST /clientes/<id>/dfe/consultar  -> DRY-RUN (não grava/sobe/avança cursor)
- POST /clientes/<id>/dfe/capturar   -> captura real (1 lote)

O scheduler automático fica para a fase seguinte.
"""
import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user

from utils.auth_helper import login_required
from models.cliente import Cliente
from utils.integrations import dfe_captura

dfe_bp = Blueprint('dfe', __name__)
logger = logging.getLogger(__name__)


def _executar(cliente_id, dry_run):
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        return jsonify({'ok': False, 'erro': 'Cliente não encontrado.'}), 404
    try:
        resultado = dfe_captura.capturar_cliente(cliente_id, dry_run=dry_run)
    except Exception as exc:  # rede/cert/parse inesperado — não deixa vazar stacktrace
        logger.exception('[dfe] captura falhou (cliente_id=%s, dry_run=%s)', cliente_id, dry_run)
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
    if not getattr(current_user, 'is_admin', lambda: False)():
        return jsonify({'ok': False, 'erro': 'Apenas administradores podem semear o cursor de NSU.'}), 403
    data = request.get_json(silent=True) or request.form
    quem = (getattr(current_user, 'nome', None) or getattr(current_user, 'login', None)
            or getattr(current_user, 'email', None) or '?')
    resultado = dfe_captura.seed_ult_nsu(id, data.get('nsu'), usuario_label=quem)
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
