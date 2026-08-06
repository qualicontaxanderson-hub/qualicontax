"""Integração Dropbox para importação de XMLs por departamento.

Suporta OAuth 2 com refresh token (recomendado) e access token legado.
Estrutura de pastas por departamento:
  /{departamento}/NOVO/                                → arquivos para importar
  /{departamento}/IMPORTADOS/{empresa}/{ano}/{mes}/    → importados com sucesso
  /{departamento}/ERROS/{empresa}/{ano}/{mes}/         → com erro de importação
"""
import logging
import re
import os
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
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
    # SÓ Fiscal permanece: é o único departamento com fluxo — a captura SEFAZ, o
    # Q-Robô e o import escrevem apenas nele (robo_saidas._DEPARTAMENTO='Fiscal',
    # dfe_captura._DEPARTAMENTO_DFE default 'Fiscal'). Contabil/DP/Financeiro/
    # Legalizacao/Comercial nunca receberam fluxo e suas pastas de raiz serão
    # arquivadas na limpeza do Dropbox. Removê-los daqui faz o job noturno e o
    # "Executar todos" pararem de iterá-los (não recriam mais /Contabil etc.) e
    # invalida um departamento não-Fiscal na API de import. CANONICOS,
    # DEPARTAMENTOS e normalize_departamento derivam deste dict e seguem juntos.
    'Fiscal': ['Fiscal', 'qualicontax-fiscal'],
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
        self._client_lock = threading.Lock()  # protege criação/renovação do cliente

    # ------------------------------------------------------------------
    # Autenticação
    # ------------------------------------------------------------------
    def _cfg(self, name: str, default: str = '') -> str:
        """Lê config Dropbox dinamicamente para permitir atualização em runtime."""
        value = os.getenv(name, default) or default
        return value.strip() if isinstance(value, str) else value

    def _client(self):
        # Fast path: sem lock se cliente já existe (evita contenção desnecessária)
        if self._dbx is not None:
            return self._dbx
        # Slow path: apenas um thread cria/renova o cliente por vez
        with self._client_lock:
            if self._dbx is not None:  # double-check após adquirir lock
                return self._dbx
            try:
                import dropbox as dropbox_sdk
                refresh_token = self._cfg('DROPBOX_REFRESH_TOKEN')
                app_key = self._cfg('DROPBOX_APP_KEY')
                app_secret = self._cfg('DROPBOX_APP_SECRET')
                if refresh_token and app_key and app_secret:
                    self._dbx = dropbox_sdk.Dropbox(
                        oauth2_refresh_token=refresh_token,
                        app_key=app_key,
                        app_secret=app_secret,
                        timeout=60,
                    )
                    return self._dbx
            except Exception as exc:
                logger.error('Erro ao criar cliente Dropbox: %s', exc)
            return None

    def is_configured(self) -> bool:
        return bool(
            self._cfg('DROPBOX_REFRESH_TOKEN')
            and self._cfg('DROPBOX_APP_KEY')
            and self._cfg('DROPBOX_APP_SECRET')
        )

    # ------------------------------------------------------------------
    # Operações de arquivo
    # ------------------------------------------------------------------
    def _root_folder(self) -> str:
        """Retorna o prefixo raiz para todos os caminhos Dropbox.

        Vazio para tokens com escopo de App Folder (padrão).
        Defina ``DROPBOX_ROOT_FOLDER`` (ex.: ``/Aplicativos/ESCRITA FISCAL``)
        quando o token tiver acesso Full Dropbox.
        """
        return self._cfg('DROPBOX_ROOT_FOLDER').rstrip('/')

    def _build_path(self, *parts: str) -> str:
        """Monta um caminho Dropbox absoluto com o prefixo raiz configurado."""
        root = self._root_folder()
        segments = [root.strip('/')] + [p.strip('/') for p in parts if p]
        return '/' + '/'.join(s for s in segments if s)

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

        Tenta automaticamente uma segunda vez se o access token estiver expirado,
        forçando renovação silenciosa via refresh token.

        Raises:
            DropboxAuthError: credenciais definitivamente inválidas (falhou após retry).
            DropboxError: qualquer outro erro (rede, API, token não configurado).
        """
        for _attempt in range(2):
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
                return entries
            except (DropboxAuthError, DropboxError):
                raise
            except Exception as exc:
                if self._is_auth_error(exc):
                    with self._client_lock:
                        self._dbx = None
                    if _attempt == 0:
                        logger.info('Dropbox list_folder: token expirado, renovando silenciosamente...')
                        continue
                    logger.error('Dropbox list_folder ERRO path=%r: %s', path, exc)
                    raise DropboxAuthError(
                        'Credenciais Dropbox inválidas ou expiradas. '
                        'Verifique DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY e DROPBOX_APP_SECRET.'
                    ) from exc
                logger.error('Dropbox list_folder ERRO path=%r: %s', path, exc)
                raise DropboxError(f'Erro ao listar pasta Dropbox {path!r}: {exc}') from exc

    def file_metadata(self, path: str):
        """Metadados de UM arquivo, ou None se não existir.

        Diferente de ``list_folder``, devolve o ``content_hash`` — a impressão
        digital determinística do conteúdo, que o Portal do Instalador usa para
        registrar na auditoria QUAL binário foi servido (não dá para ficar
        desatualizada como um rótulo de versão escrito à mão).
        """
        for _attempt in range(2):
            dbx = self._client()
            if not dbx:
                raise DropboxError(
                    'Cliente Dropbox não disponível. '
                    'Verifique se DROPBOX_REFRESH_TOKEN e DROPBOX_APP_KEY estão configurados.'
                )
            try:
                md = dbx.files_get_metadata(path)
                return {
                    'name': md.name,
                    'path': md.path_display or md.path_lower,
                    'size': getattr(md, 'size', 0),
                    'modified': getattr(md, 'server_modified', None),
                    'content_hash': getattr(md, 'content_hash', None),
                }
            except Exception as exc:
                if self._is_auth_error(exc):
                    with self._client_lock:
                        self._dbx = None
                    if _attempt == 0:
                        logger.info('Dropbox file_metadata: token expirado, renovando...')
                        continue
                    raise DropboxAuthError(
                        'Credenciais Dropbox inválidas ou expiradas. '
                        'Verifique DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY e DROPBOX_APP_SECRET.'
                    ) from exc
                if 'not_found' in str(exc):
                    return None
                raise DropboxError(f'Erro ao ler metadados de {path!r}: {exc}') from exc

    def _path_exists(self, path: str) -> bool:
        """Retorna True se o caminho existe no Dropbox."""
        for _attempt in range(2):
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
                    with self._client_lock:
                        self._dbx = None
                    if _attempt == 0:
                        logger.info('Dropbox _path_exists: token expirado, renovando silenciosamente...')
                        continue
                    raise DropboxAuthError(
                        'Credenciais Dropbox inválidas ou expiradas. '
                        'Verifique DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY e DROPBOX_APP_SECRET.'
                    ) from exc
                if 'not_found' in str(exc):
                    return False
                logger.warning(
                    'Dropbox _path_exists(%r): erro inesperado ao consultar metadata: %s',
                    path, exc, exc_info=True,
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
        """Baixa um arquivo e retorna seu conteúdo como bytes, ou None em caso de erro.

        Retry automático (até 3 tentativas, espera 2 s entre elas) para erros
        transientes de rede: timeout e conexão abortada.
        """
        import time

        _RETRYABLE = ('timed out', 'timeout', 'remoteconnection', 'connection aborted',
                      'connection reset', 'broken pipe', 'eof occurred')

        def _is_retryable(exc: Exception) -> bool:
            return any(k in str(exc).lower() for k in _RETRYABLE)

        _MAX_NETWORK_TRIES = 3
        _RETRY_WAIT = 2  # segundos entre tentativas

        for _net_try in range(_MAX_NETWORK_TRIES):
            for _auth_try in range(2):
                dbx = self._client()
                if not dbx:
                    return None
                try:
                    _, response = dbx.files_download(path)
                    return response.content
                except Exception as exc:
                    if self._is_auth_error(exc):
                        with self._client_lock:
                            self._dbx = None  # força renovação; lock garante que só 1 thread invalida
                        if _auth_try == 0:
                            logger.info('Dropbox download_file: token expirado, renovando silenciosamente...')
                            continue
                        raise DropboxAuthError('Credenciais Dropbox inválidas ou expiradas.') from exc
                    # Erro de rede transiente → retry no loop externo
                    if _is_retryable(exc) and _net_try < _MAX_NETWORK_TRIES - 1:
                        logger.warning('Dropbox download_file: erro transiente (tentativa %d/%d): %s',
                                       _net_try + 1, _MAX_NETWORK_TRIES, exc)
                        time.sleep(_RETRY_WAIT)
                        break  # sai do loop de auth e tenta novamente
                    logger.error('Erro ao baixar Dropbox %s (tentativa %d/%d): %s',
                                 path, _net_try + 1, _MAX_NETWORK_TRIES, exc)
                    return None
        return None

    def ensure_folder(self, path: str) -> None:
        """Cria a pasta se não existir; ignora conflito de pasta já existente."""
        for _attempt in range(2):
            dbx = self._client()
            if not dbx:
                return
            try:
                dbx.files_create_folder_v2(path)
                logger.info('Pasta criada no Dropbox: %s', path)
                return
            except Exception as exc:
                if self._is_auth_error(exc):
                    with self._client_lock:
                        self._dbx = None
                    if _attempt == 0:
                        logger.info('Dropbox ensure_folder: token expirado, renovando silenciosamente...')
                        continue
                    raise DropboxAuthError('Credenciais Dropbox inválidas ou expiradas.') from exc
                if 'conflict' not in str(exc):
                    logger.warning('Falha ao criar pasta %s: %s', path, exc)
                return

    def move_file(self, from_path: str, to_path: str) -> bool:
        """Move um arquivo de from_path para to_path, sobrescrevendo se já existir."""
        for _attempt in range(2):
            dbx = self._client()
            if not dbx:
                return False
            try:
                try:
                    dbx.files_move_v2(from_path, to_path, autorename=False)
                except Exception as _e:
                    if 'conflict' not in str(_e).lower():
                        raise
                    # Destino já existe: apaga e repete o move.
                    # O arquivo fonte permanece em from_path até o segundo move ter êxito.
                    dbx.files_delete_v2(to_path)
                    dbx.files_move_v2(from_path, to_path, autorename=False)
                    logger.info('Arquivo movido (sobrescrito): %s → %s', from_path, to_path)
                    return True
                logger.info('Arquivo movido: %s → %s', from_path, to_path)
                return True
            except Exception as exc:
                if self._is_auth_error(exc):
                    with self._client_lock:
                        self._dbx = None
                    if _attempt == 0:
                        logger.info('Dropbox move_file: token expirado, renovando silenciosamente...')
                        continue
                    raise DropboxAuthError('Credenciais Dropbox inválidas ou expiradas.') from exc
                logger.error('Erro ao mover %s → %s: %s', from_path, to_path, exc)
                return False

    def copy_file(self, from_path: str, to_path: str) -> bool:
        """Copia um arquivo de from_path para to_path, sobrescrevendo se já existir."""
        for _attempt in range(2):
            dbx = self._client()
            if not dbx:
                return False
            try:
                try:
                    dbx.files_copy_v2(from_path, to_path, autorename=False)
                except Exception as _e:
                    if 'conflict' not in str(_e).lower():
                        raise
                    dbx.files_delete_v2(to_path)
                    dbx.files_copy_v2(from_path, to_path, autorename=False)
                    logger.info('Arquivo copiado (sobrescrito): %s → %s', from_path, to_path)
                    return True
                logger.info('Arquivo copiado: %s → %s', from_path, to_path)
                return True
            except Exception as exc:
                if self._is_auth_error(exc):
                    with self._client_lock:
                        self._dbx = None
                    if _attempt == 0:
                        logger.info('Dropbox copy_file: token expirado, renovando silenciosamente...')
                        continue
                    raise DropboxAuthError('Credenciais Dropbox inválidas ou expiradas.') from exc
                logger.error('Erro ao copiar %s → %s: %s', from_path, to_path, exc)
                return False

    def upload_bytes(self, path: str, content: bytes) -> bool:
        """Envia ``content`` (bytes) para ``path`` no Dropbox, sobrescrevendo.

        Usa ``files_upload`` com ``WriteMode.overwrite`` (idempotente: reenviar
        o mesmo XML de DFe não gera duplicata). Faz retry silencioso uma vez se
        o access token estiver expirado, renovando via refresh token.

        Raises:
            DropboxAuthError: credenciais definitivamente inválidas (após retry).
        """
        for _attempt in range(2):
            dbx = self._client()
            if not dbx:
                return False
            try:
                import dropbox as dropbox_sdk
                dbx.files_upload(
                    content, path, mode=dropbox_sdk.files.WriteMode.overwrite)
                logger.info('Arquivo enviado ao Dropbox: %s (%d bytes)', path, len(content))
                return True
            except Exception as exc:
                if self._is_auth_error(exc):
                    with self._client_lock:
                        self._dbx = None
                    if _attempt == 0:
                        logger.info('Dropbox upload_bytes: token expirado, renovando silenciosamente...')
                        continue
                    raise DropboxAuthError('Credenciais Dropbox inválidas ou expiradas.') from exc
                logger.error('Erro ao enviar %s ao Dropbox: %s', path, exc)
                return False
        return False

    # ------------------------------------------------------------------
    # Conta / quota (SOMENTE LEITURA)
    # ------------------------------------------------------------------
    def get_space_usage(self) -> dict:
        """Espaço usado/total da conta via ``users_get_space_usage``.

        SÓ LEITURA — não escreve, não apaga, não move nada. Devolve bytes crus
        (a formatação é de quem exibe):

            {'usado': int, 'total': int, 'tipo': 'individual'|'team'|'desconhecido'}

        ``total`` vem 0 quando a conta não tem quota informada (ex.: allocation
        do tipo 'other' em contas Business antigas) — quem exibe deve tratar
        como "sem limite conhecido" em vez de dividir por zero.

        Numa conta de EQUIPE, ``usado`` é o consumo do usuário do token e
        ``total`` é a alocação da equipe.

        Raises:
            DropboxAuthError: credenciais inválidas (após 1 retry silencioso).
            DropboxError: qualquer outro erro (rede, API, token não configurado).
        """
        for _attempt in range(2):
            dbx = self._client()
            if not dbx:
                raise DropboxError(
                    'Cliente Dropbox não disponível. '
                    'Verifique se DROPBOX_REFRESH_TOKEN e DROPBOX_APP_KEY estão configurados.'
                )
            try:
                uso = dbx.users_get_space_usage()
                usado = int(getattr(uso, 'used', 0) or 0)
                aloc = getattr(uso, 'allocation', None)
                total, tipo = 0, 'desconhecido'
                if aloc is not None:
                    if aloc.is_individual():
                        total = int(getattr(aloc.get_individual(), 'allocated', 0) or 0)
                        tipo = 'individual'
                    elif aloc.is_team():
                        eq = aloc.get_team()
                        total = int(getattr(eq, 'allocated', 0) or 0)
                        tipo = 'team'
                logger.info('Dropbox space usage: usado=%s total=%s tipo=%s',
                            usado, total, tipo)
                return {'usado': usado, 'total': total, 'tipo': tipo}
            except (DropboxAuthError, DropboxError):
                raise
            except Exception as exc:
                if self._is_auth_error(exc):
                    with self._client_lock:
                        self._dbx = None
                    if _attempt == 0:
                        logger.info('Dropbox get_space_usage: token expirado, renovando...')
                        continue
                    logger.error('Dropbox get_space_usage ERRO: %s', exc)
                    raise DropboxAuthError(
                        'Credenciais Dropbox inválidas ou expiradas. '
                        'Verifique DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY e DROPBOX_APP_SECRET.'
                    ) from exc
                logger.error('Dropbox get_space_usage ERRO: %s', exc)
                raise DropboxError(f'Erro ao consultar o espaço do Dropbox: {exc}') from exc

    # ------------------------------------------------------------------
    # Helpers de caminho por departamento
    # ------------------------------------------------------------------
    def resolve_departamento_root(self, departamento: str) -> str:
        canonical = normalize_departamento(departamento)

        if canonical in self._departamento_root_cache:
            return self._departamento_root_cache[canonical]

        for candidate in departamento_aliases(canonical):
            try:
                if self._path_exists(self._build_path(candidate)):
                    self._departamento_root_cache[canonical] = candidate
                    logger.info(
                        'Dropbox resolve_departamento_root(%r): usando %r (root=%r)',
                        departamento,
                        candidate,
                        self._root_folder(),
                    )
                    return candidate
            except Exception as e:
                logger.warning(
                    'Dropbox resolve_departamento_root: erro ao testar %s: %s',
                    self._build_path(candidate),
                    e,
                )

        # Fallback: nenhum alias encontrado via _path_exists, usa o canônico diretamente
        logger.warning(
            'Dropbox resolve_departamento_root(%r): nenhuma pasta encontrada via _path_exists '
            '(possível erro de conexão ou permissão). Usando canônico %r como fallback.',
            departamento, canonical)
        self._departamento_root_cache[canonical] = canonical
        return canonical

    def pasta_novo(self, departamento: str) -> str:
        root = self.resolve_departamento_root(departamento)
        return self._build_path(root, 'NOVO')

    def pasta_importados(self, departamento: str, empresa_nome: str,
                         dt: datetime = None, empresa_numero: str = None) -> str:
        dt = dt or datetime.now(ZoneInfo('America/Sao_Paulo'))
        pasta_empresa = _build_empresa_folder(empresa_numero, empresa_nome)
        root = self.resolve_departamento_root(departamento)
        return self._build_path(root, 'IMPORTADOS', str(dt.year), pasta_empresa, f'{dt.month:02d}.{dt.year}')

    def pasta_erros(self, departamento: str, empresa_nome: str,
                    dt: datetime = None, empresa_numero: str = None) -> str:
        dt = dt or datetime.now(ZoneInfo('America/Sao_Paulo'))
        pasta_empresa = _build_empresa_folder(empresa_numero, empresa_nome)
        root = self.resolve_departamento_root(departamento)
        return self._build_path(root, 'ERROS', str(dt.year), pasta_empresa, f'{dt.month:02d}.{dt.year}')

    def pasta_saidas(self, departamento: str, empresa_nome: str,
                     dt: datetime = None, empresa_numero: str = None) -> str:
        """Saídas (NF-e emitidas pela empresa) capturadas via SEFAZ, aninhadas sob a
        pasta do EMITENTE, separadas das entradas:
        ``{root}/IMPORTADOS/{ano}/{numero - razão}/SAIDAS/{mês.ano}/``.
        """
        dt = dt or datetime.now(ZoneInfo('America/Sao_Paulo'))
        pasta_empresa = _build_empresa_folder(empresa_numero, empresa_nome)
        root = self.resolve_departamento_root(departamento)
        return self._build_path(root, 'IMPORTADOS', str(dt.year), pasta_empresa,
                                'SAIDAS', f'{dt.month:02d}.{dt.year}')

    # ------------------------------------------------------------------
    # Helpers de caminho para Certificados Digitais (.pfx)
    # ------------------------------------------------------------------
    def pasta_cert_novo(self) -> str:
        """Caixa de entrada onde os .pfx novos são depositados: ``{ROOT}/_ENTRADA``.

        A _ENTRADA é a porta ÚNICA e PLANA do sistema (sem subpastas): todo
        arquivo chega nela e o app separa por TIPO. Quem consome o quê:
          * ``.xml`` → cron_roteador arquiva em EMPRESAS/{empresa}/FISCAL/...;
          * ``.pfx`` → fica aqui até alguém clicar em "Vincular Certificado" na
            tela da empresa, que então move para EMPRESAS/{empresa}/CERTIFICADO.
        O roteador ignora tudo que não é .xml, então o certificado à espera de
        vínculo não corre risco de ser movido por ele.

        Era ``Certificados/NOVO`` antes da reorganização do Dropbox.
        Usa ``_build_path`` — funciona tanto em App Folder (caminho relativo)
        quanto em Full Dropbox (prefixado por ``DROPBOX_ROOT_FOLDER``).
        """
        return self._build_path('_ENTRADA')

    def pasta_cert_importados(self, empresa_nome: str, empresa_numero: str = None) -> str:
        """Pasta destino do certificado vinculado, DENTRO da pasta da empresa:
        ``{ROOT}/EMPRESAS/{numero} - {razão}/CERTIFICADO``.

        Era ``Certificados/IMPORTADOS/{numero} - {razão}`` até a reorganização do
        Dropbox (raiz só com _ENTRADA e EMPRESAS). Os 24 certificados vigentes já
        foram movidos para cá na mão; sem esta mudança, um vínculo novo cairia na
        pasta antiga, fora da convenção. Não repõe caminho de registro existente —
        ``dfe_certificados.dropbox_path`` guarda o caminho de cada um e já aponta
        para EMPRESAS.

        A pasta de DEPÓSITO (``pasta_cert_novo``) segue em ``Certificados/NOVO``:
        é caixa de entrada, não arquivo da empresa.
        """
        pasta_empresa = _build_empresa_folder(empresa_numero, empresa_nome)
        return self._build_path('EMPRESAS', pasta_empresa, 'CERTIFICADO')

    # ------------------------------------------------------------------
    # Helpers de caminho para Documentos Fiscais (DFe / XML)
    # ------------------------------------------------------------------
    def pasta_fiscal(self, empresa_nome: str, ano, mes, sentido: str,
                     empresa_numero: str = None) -> str:
        """Pasta de documentos fiscais (XML de DFe) da empresa, na convenção ÚNICA
        do ``cron_roteador``:
        ``{ROOT}/EMPRESAS/{numero} - {razão}/FISCAL/{SENTIDO}/{ano}/{mes}``,
        com ``sentido`` ∈ ``ENTRADAS | SAIDAS | CTE | EVENTOS``.

        Antes gravava o path PLANO ``.../Fiscal/{ano}/{mes}`` (sem SENTIDO): todo
        doc que também passava pelo roteador virava um par duplicado (flat +
        SENTIDO). Unificar aqui mata a FONTE das duplicatas — o ``sentido`` é
        obrigatório e o path sai idêntico ao que o roteador grava (mesmo
        ``EMPRESAS``, ``FISCAL`` maiúsculo, ano e mês de 2 dígitos). No Dropbox o
        ``FISCAL`` cai na pasta ``Fiscal`` existente (case-insensitive), como já
        acontece com os arquivos do roteador.

        Reusa ``_build_empresa_folder`` (mesmo ``001 - RAZÃO`` dos certificados) e
        ``_build_path`` (prefixo raiz).
        """
        pasta_empresa = _build_empresa_folder(empresa_numero, empresa_nome)
        return self._build_path(
            'EMPRESAS', pasta_empresa, 'FISCAL', sentido, str(ano), f'{int(mes):02d}')


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
    path = folder or os.getenv('DROPBOX_XML_FOLDER', '/qualicontax/xml-compras')
    return _service.list_xml_files(path)


def download_xml(path: str) -> str:
    raw = _service.download_file(path)
    if raw is None:
        return None
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('latin-1', errors='replace')
