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
from typing import Optional

logger = logging.getLogger(__name__)


class DropboxAuthError(Exception):
    """Raised when Dropbox returns an authentication/authorization error."""


class DropboxError(Exception):
    """Raised when a Dropbox operation fails for reasons other than auth (network, API error, etc.)."""

# NF-e access keys are exactly 44 digits; files are normally saved as
# "{44digits}.xml" but browsers (Edge, Chrome) sometimes strip the extension
# or save as ".html" / ".htm".  Some download tools also append a protocol
# number or sequence after the key, producing names longer than 44 digits
# (e.g. 52-digit names like "{44-digit-key}{8-digit-seq}.htm").
# We accept any file whose name *starts* with at least 44 consecutive digits.
_NFE_KEY_RE = re.compile(r'^\d{44}')

DEPARTAMENTO_ALIASES = {
    # Nome canônico → aliases aceitos na raiz da App Folder do Dropbox.
    'Fiscal': ['Fiscal', 'qualicontax-fiscal'],
    'Contabil': ['Contabil', 'qualicontax-contabil'],
    'DP': ['DP', 'qualicontax-dp'],
    'Financeiro': ['Financeiro', 'qualicontax-financeiro'],
    'Legalizacao': ['Legalizacao', 'qualicontax-legalizacao'],
    'Comercial': ['Comercial', 'qualicontax-comercial'],
}

DEPARTAMENTOS_CANONICOS = list(DEPARTAMENTO_ALIASES.keys())
DEPARTAMENTOS = [
    alias
    for aliases in DEPARTAMENTO_ALIASES.values()
    for alias in aliases
]
_DEPARTAMENTO_TO_CANONICAL = {
    alias: canonical
    for canonical, aliases in DEPARTAMENTO_ALIASES.items()
    for alias in aliases
}


def _sanitize_folder_name(name: str) -> str:
    """Remove caracteres inválidos para nome de pasta no Dropbox."""
    return re.sub(r'[/\\:*?"<>|]', '_', name).strip() or 'SEM_NOME'


def _build_empresa_folder(numero: Optional[str], nome: str) -> str:
    """Constrói o nome da pasta da empresa para o Dropbox.

    Quando o cliente possui ``numero_cliente``, o resultado é
    ``{numero} - {nome}`` (ex.: ``001 - PADARIA BELA VISTA``).
    Sem número, retorna apenas o nome sanitizado.
    """
    nome_san = _sanitize_folder_name(nome)
    if numero:
        num_san = _sanitize_folder_name(str(numero))
        return f'{num_san} - {nome_san}'
    return nome_san


class DropboxService:
    """Serviço Dropbox com OAuth2 refresh token e suporte a pastas por departamento."""

    def __init__(self):
        self._dbx = None
        self._departamento_root_cache: dict[str, str] = {}

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
    def _is_auth_error(self, exc: Exception) -> bool:
        """Retorna True se a exceção é um erro de autenticação/autorização do Dropbox."""
        exc_type = type(exc).__name__
        exc_str = str(exc)
        return (
            'AuthError' in exc_type
            or 'BadInputError' in exc_type
            or 'invalid_access_token' in exc_str
            or 'expired_access_token' in exc_str
            or 'invalid_grant' in exc_str
            or 'missing_scope' in exc_str
            or 'insufficient_scope' in exc_str
        )

    def list_folder(self, path: str, recursive: bool = False) -> list:
        """Lista itens de uma pasta (arquivos e sub-pastas).

        Raises:
            DropboxAuthError: credenciais inválidas/expiradas.
            DropboxError: qualquer outro erro (rede, API, token não configurado).
        """
        dbx = self._client()
        if not dbx:
            raise DropboxError(
                'Cliente Dropbox não disponível. '
                'Verifique se DROPBOX_REFRESH_TOKEN e DROPBOX_APP_KEY estão configurados.'
            )
        entries = []
        try:
            import dropbox as dropbox_sdk
            logger.info('Dropbox list_folder: path=%r recursive=%s', path, recursive)
            result = dbx.files_list_folder(path, recursive=recursive)
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
            logger.info('Dropbox list_folder(%r, recursive=%s): %d item(s) encontrado(s): %s',
                        path, recursive, len(entries), [e['name'] for e in entries])
        except (DropboxAuthError, DropboxError):
            raise
        except Exception as exc:
            logger.error('Dropbox list_folder ERRO path=%r: %s', path, exc)
            if self._is_auth_error(exc):
                self._dbx = None  # Limpa cache para tentar renovar token na próxima chamada
                raise DropboxAuthError(
                    'Credenciais Dropbox inválidas ou expiradas. '
                    'Verifique DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY e DROPBOX_APP_SECRET.'
                ) from exc
            raise DropboxError(f'Erro ao listar pasta Dropbox {path!r}: {exc}') from exc
        return entries

    def _path_exists(self, path: str) -> bool:
        """Retorna True se o caminho existe no Dropbox."""
        dbx = self._client()
        if not dbx:
            raise DropboxError(
                'Cliente Dropbox não disponível. '
                'Verifique se DROPBOX_REFRESH_TOKEN e DROPBOX_APP_KEY estão configurados.'
            )
        try:
            dbx.files_get_metadata(path)
            return True
        except Exception as exc:
            if self._is_auth_error(exc):
                self._dbx = None
                raise DropboxAuthError(
                    'Credenciais Dropbox inválidas ou expiradas. '
                    'Verifique DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY e DROPBOX_APP_SECRET.'
                ) from exc

            if 'not_found' in str(exc):
                return False

            logger.warning(
                'Dropbox _path_exists(%r): erro ao consultar metadata: %s',
                path,
                exc,
            )
            return False

    def list_xml_files(self, path: str) -> list:
        """Lista arquivos .xml/.htm/.html e arquivos cujo nome começa com a chave NF-e (≥44 dígitos).

        Navegadores como Edge/Chrome às vezes salvam arquivos XML de NF-e sem a
        extensão .xml ou com extensão .html/.htm.  Algumas ferramentas de download
        salvam o nome como "{44 dígitos da chave}{dígitos adicionais}.htm", resultando
        em nomes com mais de 44 dígitos.  Para não perder esses arquivos, aceitamos
        qualquer arquivo cujo nome inicie com pelo menos 44 dígitos consecutivos.
        """
        # Inclui subpastas dentro de NOVO para suportar organização por empresa.
        all_files = self.list_folder(path, recursive=True)
        xml_files = [
            f for f in all_files
            if f.get('is_file') and (
                f['name'].lower().endswith('.xml')
                or f['name'].lower().endswith('.htm')
                or f['name'].lower().endswith('.html')
                or _NFE_KEY_RE.match(f['name'])
            )
        ]
        logger.info('Dropbox list_xml_files(%r): %d arquivo(s) xml/nfe de %d item(s)',
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
            if self._is_auth_error(exc):
                self._dbx = None
                raise DropboxAuthError(
                    'Credenciais Dropbox inválidas ou expiradas.'
                ) from exc
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
            if self._is_auth_error(exc):
                self._dbx = None
                raise DropboxAuthError(
                    'Credenciais Dropbox inválidas ou expiradas.'
                ) from exc
            if 'conflict' not in str(exc):
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
            if self._is_auth_error(exc):
                self._dbx = None
                raise DropboxAuthError(
                    'Credenciais Dropbox inválidas ou expiradas.'
                ) from exc
            return False

    # ------------------------------------------------------------------
    # Helpers de caminho por departamento
    # ------------------------------------------------------------------
    def resolve_departamento_root(self, departamento: str) -> str:
        canonical = normalize_departamento(departamento)

        if canonical in self._departamento_root_cache:
            return self._departamento_root_cache[canonical]

        for candidate in departamento_aliases(canonical):
            try:
                if self._path_exists(f'/{candidate}'):
                    self._departamento_root_cache[canonical] = candidate
                    logger.info(
                        'Dropbox resolve_departamento_root(%r): usando %r',
                        departamento,
                        candidate,
                    )
                    return candidate
            except Exception as e:
                logger.warning(
                    'Dropbox resolve_departamento_root: erro ao testar /%s: %s',
                    candidate,
                    e,
                )

        self._departamento_root_cache[canonical] = canonical
        logger.warning(
            'Dropbox resolve_departamento_root(%r): nenhuma pasta existente encontrada; usando fallback %r',
            departamento,
            canonical,
        )
        return canonical

    def pasta_novo(self, departamento: str) -> str:
        root = self.resolve_departamento_root(departamento)
        return f'/{root}/NOVO'

    def pasta_importados(self, departamento: str, empresa_nome: str,
                         dt: datetime = None, empresa_numero: str = None) -> str:
        dt = dt or datetime.now()
        pasta_empresa = _build_empresa_folder(empresa_numero, empresa_nome)
        root = self.resolve_departamento_root(departamento)
        return f'/{root}/IMPORTADOS/{pasta_empresa}/{dt.year}/{dt.month:02d}'

    def pasta_erros(self, departamento: str, empresa_nome: str,
                    dt: datetime = None, empresa_numero: str = None) -> str:
        dt = dt or datetime.now()
        pasta_empresa = _build_empresa_folder(empresa_numero, empresa_nome)
        root = self.resolve_departamento_root(departamento)
        return f'/{root}/ERROS/{pasta_empresa}/{dt.year}/{dt.month:02d}'


def normalize_departamento(departamento: str) -> str:
    """Converte aliases legados para o nome canônico do departamento."""
    return _DEPARTAMENTO_TO_CANONICAL.get((departamento or '').strip(), (departamento or '').strip())


def departamento_aliases(departamento: str) -> list[str]:
    """Retorna aliases válidos do departamento, com o nome canônico primeiro."""
    canonical = normalize_departamento(departamento)
    return DEPARTAMENTO_ALIASES.get(canonical, [canonical])


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
