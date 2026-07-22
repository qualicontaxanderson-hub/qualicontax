# -*- coding: utf-8 -*-
"""
Rotas de captura de DFe (NF-e) por cliente — disparo MANUAL.

SÓ LEITURA. NUNCA MANIFESTA (só distribuição por NSU / distDFeInt).
- POST /clientes/<id>/dfe/consultar  -> DRY-RUN (não grava/sobe/avança cursor)
- POST /clientes/<id>/dfe/capturar   -> captura real (1 lote)

O scheduler automático fica para a fase seguinte.
"""
import logging

from flask import Blueprint, jsonify

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
