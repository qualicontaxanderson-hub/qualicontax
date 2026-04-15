"""Integração com Dropbox para importação automática de XML de NF-e."""
import logging
from config import Config

logger = logging.getLogger(__name__)


def _get_client():
    """Retorna um cliente Dropbox autenticado, ou None se não configurado."""
    token = Config.DROPBOX_ACCESS_TOKEN
    if not token:
        return None
    try:
        import dropbox
        return dropbox.Dropbox(token)
    except Exception as exc:
        logger.error('Erro ao criar cliente Dropbox: %s', exc)
        return None


def list_xml_files(folder: str = None) -> list:
    """
    Lista todos os arquivos .xml na pasta Dropbox configurada.

    Returns:
        list of dict: [{'name': str, 'path': str, 'size': int, 'modified': str}, ...]
    """
    dbx = _get_client()
    if not dbx:
        return []

    folder_path = folder or Config.DROPBOX_XML_FOLDER
    files = []
    try:
        import dropbox
        result = dbx.files_list_folder(folder_path)
        while True:
            for entry in result.entries:
                if (hasattr(entry, 'name') and
                        entry.name.lower().endswith('.xml')):
                    files.append({
                        'name': entry.name,
                        'path': entry.path_lower,
                        'size': getattr(entry, 'size', 0),
                        'modified': str(getattr(entry, 'server_modified', '')),
                    })
            if not result.has_more:
                break
            result = dbx.files_list_folder_continue(result.cursor)
    except Exception as exc:
        logger.error('Erro ao listar pasta Dropbox %s: %s', folder_path, exc)

    return files


def download_xml(path: str) -> str:
    """
    Baixa um arquivo XML do Dropbox e retorna seu conteúdo como string.

    Args:
        path: caminho do arquivo no Dropbox (ex: /qualicontax/xml-compras/nota.xml)

    Returns:
        Conteúdo XML como string UTF-8, ou None em caso de erro.
    """
    dbx = _get_client()
    if not dbx:
        return None
    try:
        _, response = dbx.files_download(path)
        raw = response.content
        # Tenta decodificar como UTF-8, depois latin-1 como fallback
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            return raw.decode('latin-1', errors='replace')
    except Exception as exc:
        logger.error('Erro ao baixar arquivo Dropbox %s: %s', path, exc)
        return None


def is_configured() -> bool:
    """Retorna True se o token Dropbox está configurado."""
    return bool(Config.DROPBOX_ACCESS_TOKEN)
