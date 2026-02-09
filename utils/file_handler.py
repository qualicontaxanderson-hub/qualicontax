"""Handler para upload e download de arquivos"""
import os
from werkzeug.utils import secure_filename
from config import Config


def allowed_file(filename):
    """
    Verifica se a extensão do arquivo é permitida.
    
    Args:
        filename (str): Nome do arquivo
        
    Returns:
        bool: True se extensão permitida
    """
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in Config.ALLOWED_EXTENSIONS


def save_file(file, subfolder=''):
    """
    Salva arquivo no diretório de uploads.
    
    Args:
        file: Arquivo do request
        subfolder (str): Subpasta dentro de uploads
        
    Returns:
        str: Caminho relativo do arquivo salvo ou None se erro
    """
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        
        # Adiciona timestamp para evitar conflitos
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        name, ext = os.path.splitext(filename)
        filename = f"{name}_{timestamp}{ext}"
        
        # Cria diretório se não existir
        upload_path = os.path.join(Config.UPLOAD_FOLDER, subfolder)
        os.makedirs(upload_path, exist_ok=True)
        
        # Salva arquivo
        filepath = os.path.join(upload_path, filename)
        file.save(filepath)
        
        # Retorna caminho relativo
        return os.path.join('uploads', subfolder, filename)
    
    return None


def delete_file(filepath):
    """
    Deleta arquivo do sistema.
    
    Args:
        filepath (str): Caminho do arquivo
        
    Returns:
        bool: True se sucesso
    """
    try:
        full_path = os.path.join('static', filepath)
        if os.path.exists(full_path):
            os.remove(full_path)
            return True
    except Exception as e:
        print(f"Erro ao deletar arquivo: {e}")
    
    return False
