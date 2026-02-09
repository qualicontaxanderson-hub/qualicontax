"""Rotas de Documentos (placeholder)"""
from flask import Blueprint, request, flash, redirect, url_for, send_file
from utils.auth_helper import login_required
from utils.file_handler import save_upload_file, get_file_path
from utils.db_helper import execute_query
import os

documentos = Blueprint('documentos', __name__)


@documentos.route('/documentos/upload', methods=['POST'])
@login_required
def upload():
    """POST file upload"""
    if 'file' not in request.files:
        flash('Nenhum arquivo foi enviado.', 'danger')
        return redirect(request.referrer or url_for('dashboard.index'))
    
    file = request.files['file']
    
    if file.filename == '':
        flash('Nenhum arquivo selecionado.', 'danger')
        return redirect(request.referrer or url_for('dashboard.index'))
    
    # Dados adicionais
    cliente_id = request.form.get('cliente_id', type=int)
    processo_id = request.form.get('processo_id', type=int)
    tipo_documento = request.form.get('tipo_documento')
    descricao = request.form.get('descricao')
    
    # Salva o arquivo
    file_info = save_upload_file(file)
    
    if not file_info:
        flash('Erro ao salvar arquivo.', 'danger')
        return redirect(request.referrer or url_for('dashboard.index'))
    
    # Salva registro no banco
    query = """
        INSERT INTO documentos (
            cliente_id, processo_id, tipo_documento, nome_arquivo,
            caminho_arquivo, tamanho_arquivo, descricao, data_upload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
    """
    params = (
        cliente_id,
        processo_id,
        tipo_documento,
        file_info['filename'],
        file_info['path'],
        file_info['size'],
        descricao
    )
    
    documento_id = execute_query(query, params)
    
    if documento_id:
        flash('Documento enviado com sucesso!', 'success')
    else:
        flash('Erro ao registrar documento no banco.', 'danger')
    
    return redirect(request.referrer or url_for('dashboard.index'))


@documentos.route('/documentos/<int:id>/download')
@login_required
def download(id):
    """Download file"""
    # Busca documento no banco
    query = "SELECT nome_arquivo, caminho_arquivo FROM documentos WHERE id = %s"
    documento = execute_query(query, (id,), fetch=True, fetch_one=True)
    
    if not documento:
        flash('Documento não encontrado.', 'danger')
        return redirect(url_for('dashboard.index'))
    
    file_path = get_file_path(documento['caminho_arquivo'])
    
    if not os.path.exists(file_path):
        flash('Arquivo não encontrado no servidor.', 'danger')
        return redirect(url_for('dashboard.index'))
    
    return send_file(file_path, as_attachment=True, download_name=documento['nome_arquivo'])
