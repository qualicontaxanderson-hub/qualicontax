"""Integração Dropbox para importação de XMLs por departamento.

Suporta OAuth 2 com refresh token (recomendado) e access token legado.
Estrutura de pastas por departamento:
  /{departamento}/NOVO/                                → arquivos para importar
  /{departamento}/IMPORTADOS/{empresa}/{ano}/{mes}/    → importados com sucesso
  /{departamento}/ERROS/{empresa}/{ano}/{mes}/         → com erro de importação
"""
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

DEPARTAMENTOS = [
    # Nomes usados na APP FOLDER do Dropbox (caminhos relativos à raiz do app)
    'Fiscal',
    'Contabil',
    'DP',
    'Financeiro',
    'Legalizacao',
    'Comercial',
    # Nomes legados — mantidos para compatibilidade com importações antigas
    'qualicontax-contabil',
    'qualicontax-fiscal',
    'qualicontax-dp',
    'qualicontax-financeiro',
    'qualicontax-legalizacao',
    'qualicontax-comercial',
]


def _sanitize_folder_name(name: str) -> str:
    """Remove caracteres inválidos para nome de pasta no Dropbox."""
    return re.sub(r'[/\\:*?"<>|]', '_', name).strip() or 'SEM_NOME'


class DropboxService:
    """Serviço Dropbox com OAuth2 refresh token e suporte a pastas por departamento."""

    def __init__(self):
        self._dbx = None

    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------
    def _client(self):
        if self._dbx is not None:
            return self._dbx
        from config import Config
        try:
            import dropbox as dropbox_sdk
            refresh_token = Config.DROPBOX_REFRESH_TOKEN
            app_key = Config.DROPBOX_APP_KEY
            app_secret = Config.DROPBOX_APP_SECRET
            if refresh_token and app_key:
                self._dbx = dropbox_sdk.Dropbox(
                    oauth2_refresh_token=refresh_token,
                    app_key=app_key,
                    app_secret=app_secret,
                )
                return self._dbx
            # Fallback para access token legado
            access_token = Config.DROPBOX_ACCESS_TOKEN
            if access_token:
                self._dbx = dropbox_sdk.Dropbox(access_token)
                return self._dbx
        except Exception as exc:
            logger.error('Erro ao criar cliente Dropbox: %s', exc)
        return None

    def is_configured(self) -> bool:
        from config import Config
        return bool(
            (Config.DROPBOX_REFRESH_TOKEN and Config.DROPBOX_APP_KEY)
            or Config.DROPBOX_ACCESS_TOKEN
        )

    # ------------------------------------------------------------------
    # Operações de arquivo
    # ------------------------------------------------------------------
    def list_folder(self, path: str) -> list:
        """Lista todos os itens de uma pasta (arquivos e sub-pastas)."""
        dbx = self._client()
        if not dbx:
            logger.error('list_folder(%s): cliente Dropbox não disponível (token não configurado?)', path)
            return []
        entries = []
        try:
            import dropbox as dropbox_sdk
            logger.info('Dropbox list_folder: path=%r', path)
            result = dbx.files_list_folder(path)
            while True:
                for entry in result.entries:
                    entries.append({
                        'name': entry.name,
                        'path': entry.path_lower,
                        'size': getattr(entry, 'size', 0),
                        'modified': str(getattr(entry, 'server_modified', '')),
                        'is_file': isinstance(entry, dropbox_sdk.files.FileMetadata),
                    })
                if not result.has_more:
                    break
                result = dbx.files_list_folder_continue(result.cursor)
            logger.info('Dropbox list_folder(%r): %d item(s) encontrado(s): %s',
                        path, len(entries), [e['name'] for e in entries])
        except Exception as exc:
            logger.error('Dropbox list_folder ERRO path=%r: %s', path, exc)
        return entries

    def list_xml_files(self, path: str) -> list:
        """Lista apenas arquivos .xml de uma pasta."""
        all_files = self.list_folder(path)
        xml_files = [
            f for f in all_files
            if f.get('is_file') and f['name'].lower().endswith('.xml')
        ]
        logger.info('Dropbox list_xml_files(%r): %d arquivo(s) .xml de %d item(s)',
                    path, len(xml_files), len(all_files))
        return xml_files

    def download_file(self, path: str):
        """Baixa um arquivo e retorna seu conteúdo como bytes, ou None em caso de erro."""
        dbx = self._client()
        if not dbx:
            return None
        try:
            _, response = dbx.files_download(path)
            return response.content
        except Exception as exc:
            logger.error('Erro ao baixar Dropbox %s: %s', path, exc)
            return None

    def ensure_folder(self, path: str) -> None:
        """Cria a pasta se não existir; ignora conflito de pasta já existente."""
        dbx = self._client()
        if not dbx:
            return
        try:
            dbx.files_create_folder_v2(path)
            logger.info('Pasta criada no Dropbox: %s', path)
        except Exception as exc:
            if 'path/conflict' not in str(exc):
                logger.warning('Falha ao criar pasta %s: %s', path, exc)

    def move_file(self, from_path: str, to_path: str) -> bool:
        """Move um arquivo de from_path para to_path (autorename se já existir)."""
        dbx = self._client()
        if not dbx:
            return False
        try:
            dbx.files_move_v2(from_path, to_path, autorename=True)
            logger.info('Arquivo movido: %s → %s', from_path, to_path)
            return True
        except Exception as exc:
            logger.error('Erro ao mover %s → %s: %s', from_path, to_path, exc)
            return False

    # ------------------------------------------------------------------
    # Helpers de caminho por departamento
    # ------------------------------------------------------------------
    def pasta_novo(self, departamento: str) -> str:
        return f'/{departamento}/NOVO'

    def pasta_importados(self, departamento: str, empresa_nome: str,
                         dt: datetime = None) -> str:
        dt = dt or datetime.now()
        nome = _sanitize_folder_name(empresa_nome)
        return f'/{departamento}/IMPORTADOS/{nome}/{dt.year}/{dt.month:02d}'

    def pasta_erros(self, departamento: str, empresa_nome: str,
                    dt: datetime = None) -> str:
        dt = dt or datetime.now()
        nome = _sanitize_folder_name(empresa_nome)
        return f'/{departamento}/ERROS/{nome}/{dt.year}/{dt.month:02d}'


# ---------------------------------------------------------------------------
# Instância global do serviço
# ---------------------------------------------------------------------------
_service = DropboxService()


# ---------------------------------------------------------------------------
# Funções de compatibilidade com código legado
# ---------------------------------------------------------------------------
def is_configured() -> bool:
    return _service.is_configured()


def list_xml_files(folder: str = None) -> list:
    from config import Config
    path = folder or Config.DROPBOX_XML_FOLDER
    return _service.list_xml_files(path)


def download_xml(path: str) -> str:
    raw = _service.download_file(path)
    if raw is None:
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('latin-1', errors='replace')
