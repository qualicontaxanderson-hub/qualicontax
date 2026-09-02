# -*- coding: utf-8 -*-
"""Backup do banco — dump, verificação e envio ao Dropbox, sem depender do PC.

Por que este módulo existe
--------------------------
Até 01/09/2026 o backup era uma tarefa agendada no computador do Anderson. Em
29/08 o dump saiu vazio (a máquina perdeu a rede no meio) e de 30/08 a 01/09 a
tarefa nem disparou — os computadores estavam desligados. Deu quatro dias sem
backup **e sem ninguém saber**, porque a única testemunha era um arquivo de log
que ninguém lê.

Aqui o mesmo trabalho roda dentro da Railway, no horário do Cron, e o desfecho
é GRAVADO NO BANCO para a tela de Configurações mostrar. Backup que falha
calado não é backup.

DUMP E SÓ DUMP
--------------
NUNCA executa ``PURGE BINARY LOGS`` — regra firme do Anderson (07/08/2026): o
purge derruba a janela de recuperação point-in-time do Railway e é
irreversível. O ``bakup_qualicontax.bat`` do PC faz o purge; este módulo não
faz, e não deve passar a fazer.

Também **não apaga backup antigo**. A faxina de retenção continua sendo
decisão manual — apagar arquivo no Dropbox não tem volta.

A senha nunca vai na linha de comando
-------------------------------------
O ``mysqldump`` recebe as credenciais por ``--defaults-extra-file``, um arquivo
temporário criado com permissão 0600 e removido no ``finally``. Senha em
argumento apareceria na lista de processos do container para qualquer um que
abrisse um shell ali.

A verificação é o portão
------------------------
Num pipe, o código de saída pode ser o do compressor e não o do ``mysqldump``:
se o dump morre no meio, o ``.gz`` sai íntegro **porém truncado** — foi
exatamente assim que o arquivo de 29/08 nasceu com 52 bytes e cara de sucesso.
Por isso o portão tem três partes: código de saída do ``mysqldump``, CRC do
gzip lido de volta do disco e o marcador ``Dump completed on`` na última linha.
Só passando nas três o arquivo sobe para o Dropbox.
"""
import glob
import gzip
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

_CONFIG_KEY = 'backup_bd_status'
_TZ = ZoneInfo('America/Sao_Paulo')

# Pasta no Dropbox — a MESMA que o PC usa, para as duas cópias envelhecerem
# juntas no mesmo lugar. Caminho absoluto (o token é Full Dropbox), fora da
# app folder do sistema.
_PASTA_PADRAO = '/BANCOS/OFX/BACKUP/qualicontax'

# O nome diz de onde veio. O backup do PC continua saindo como
# ``qualicontax_<ts>.sql.gz``; o da nuvem leva ``_nuvem_`` para ninguém
# confundir as duas cópias na hora de restaurar.
_PREFIXO = 'qualicontax_nuvem_'

# Piso de sanidade, herdado do .bat: dump "ok" com menos de 1 MB é dump vazio.
_MIN_BYTES = 1024 * 1024

# Encolher demais de um dia para o outro não reprova o backup (o arquivo pode
# ser legítimo), mas fica anotado no status para alguém olhar.
_ENCOLHEU_PCT = 70

# Um backup diário que passou de 48 h significa que pelo menos uma rodada não
# aconteceu — e "não aconteceu" não gera registro de erro nenhum, some calado.
# É esse silêncio que o amarelo do card denuncia.
_LIMITE_VELHO_S = int(os.getenv('BACKUP_LIMITE_VELHO_H', '48')) * 3600

_MARCADOR = b'Dump completed on'
_CHUNK = 1024 * 1024


# ---------------------------------------------------------------------------
# Status em app_config — a mesma tabela chave/valor do horário do scheduler e
# do espaço do Dropbox. A idade sai do relógio do BANCO, nunca do Python.
# ---------------------------------------------------------------------------
def _gravar_status(payload: dict) -> None:
    try:
        execute_query(
            "INSERT INTO app_config (chave, valor) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()",
            (_CONFIG_KEY, json.dumps(payload, ensure_ascii=False)), fetch=False,
        )
    except Exception:
        logger.exception('[backup] falha ao gravar o status em app_config.')


def _idade_fmt(seg: int) -> str:
    if seg < 60:
        return 'agora'
    if seg < 3600:
        return 'há %d min' % (seg // 60)
    if seg < 86400:
        return 'há %d h' % (seg // 3600)
    dias = seg // 86400
    return 'há %d dia%s' % (dias, '' if dias == 1 else 's')


def _bytes_fmt(n) -> str:
    if not n:
        return '—'
    for rot, base in (('GB', 1024 ** 3), ('MB', 1024 ** 2), ('KB', 1024)):
        if n >= base:
            return ('%.1f %s' % (n / float(base), rot)).replace('.', ',')
    return '%d B' % n


def ler_status() -> dict:
    """Último backup conhecido, pronto para o template. NUNCA levanta exceção.

    Devolve sempre um dict com ``registrado`` (bool). Havendo registro, traz
    ``ok``, ``erro``, ``etapa``, ``arquivo``, ``bytes_gz``, ``tamanho_fmt``,
    ``duracao_s``, ``idade_seg``, ``idade_fmt``, ``cor`` e ``alerta``.

    A cor não fala só do último resultado, fala da PROTEÇÃO: um backup que deu
    certo há três dias é tão preocupante quanto um que falhou ontem, e por isso
    envelhecer também pinta o card.
    """
    try:
        row = execute_query(
            "SELECT valor, TIMESTAMPDIFF(SECOND, updated_at, NOW()) AS idade "
            "FROM app_config WHERE chave = %s",
            (_CONFIG_KEY,), fetch=True, fetch_one=True,
        )
    except Exception:
        logger.warning('[backup] status ilegível.', exc_info=True)
        return {'registrado': False}

    if not row or not row.get('valor'):
        return {'registrado': False}

    try:
        st = json.loads(row['valor'])
    except Exception:
        return {'registrado': False}

    idade = int(row.get('idade') or 0)
    st['registrado'] = True
    st['idade_seg'] = idade
    st['idade_fmt'] = _idade_fmt(idade)
    st['tamanho_fmt'] = _bytes_fmt(st.get('bytes_gz'))

    if not st.get('ok'):
        st['cor'] = 'vermelho'
    elif idade >= _LIMITE_VELHO_S:
        st['cor'] = 'amarelo'
    else:
        st['cor'] = 'verde'
    return st


# ---------------------------------------------------------------------------
# As três etapas
# ---------------------------------------------------------------------------
def _credenciais() -> dict:
    return {
        'host': os.getenv('DB_HOST', ''),
        'port': os.getenv('DB_PORT', '3306'),
        'user': os.getenv('DB_USER', ''),
        'password': os.getenv('DB_PASSWORD', ''),
        'nome': os.getenv('DB_NAME', ''),
    }


def _escrever_cnf(pasta: str, cred: dict) -> str:
    """Arquivo [client] com permissão 0600 — a senha não passa por argv."""
    caminho = os.path.join(pasta, 'my_backup.cnf')
    fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write('[client]\n')
        fh.write('host=%s\n' % cred['host'])
        fh.write('port=%s\n' % cred['port'])
        fh.write('user=%s\n' % cred['user'])
        fh.write('password=%s\n' % cred['password'])
    return caminho


#: Lugares onde o cliente MySQL costuma cair quando NÃO está no PATH. O Nix
#: instala em ``/nix/store/<hash>-mysql-.../bin`` e o comando de start nem
#: sempre enxerga esse caminho — foi assim que a primeira rodada do cron
#: falhou em 02/09/2026, com o pacote instalado e o binário "inexistente".
_CANDIDATOS_DUMP = (
    '/usr/bin/mysqldump',
    '/usr/local/bin/mysqldump',
    '/usr/local/mysql/bin/mysqldump',
)


def _achar_mysqldump() -> str:
    """Caminho do binário, ou '' se não existir mesmo.

    Procura em quatro lugares, do mais explícito ao mais desesperado:
    ``BACKUP_MYSQLDUMP_BIN``, o PATH, os caminhos usuais de pacote apt e por
    fim o ``/nix/store``. Procurar é barato; uma rodada perdida por PATH custa
    um dia de backup.
    """
    forcado = os.getenv('BACKUP_MYSQLDUMP_BIN')
    if forcado:
        return forcado

    achado = shutil.which('mysqldump')
    if achado:
        return achado

    for caminho in _CANDIDATOS_DUMP:
        if os.path.exists(caminho):
            logger.info('[backup] mysqldump fora do PATH, achado em %s', caminho)
            return caminho

    for caminho in sorted(glob.glob('/nix/store/*/bin/mysqldump')):
        logger.info('[backup] mysqldump fora do PATH, achado em %s', caminho)
        return caminho

    return ''


def _diagnostico_dump() -> str:
    """O que existe no container — para o erro ACUSAR em vez de só reclamar.

    Sem isto, "não existe" manda a gente adivinhar qual variável de imagem
    usar, a dez minutos por tentativa. Com isto, a própria mensagem diz se o
    pacote foi instalado e onde.
    """
    try:
        no_store = [os.path.basename(c) for c in sorted(glob.glob('/nix/store/*mysql*'))[:4]]
    except Exception:
        no_store = []
    return 'PATH=%s | /nix/store com mysql: %s' % (
        (os.getenv('PATH', '') or '(vazio)')[:400], no_store or 'nada')


def _dump(binario: str, cnf: str, banco: str, destino: str) -> tuple:
    """Roda o mysqldump comprimindo direto para ``destino``. (ok, erro)."""
    cmd = [
        binario,
        '--defaults-extra-file=%s' % cnf,
        '--databases', banco,
        '--single-transaction',
        '--quick',
        '--max-allowed-packet=1G',
        '--routines', '--triggers',
        '--add-drop-database', '--add-drop-table',
        '--default-character-set=utf8mb4',
    ]
    cmd.extend(os.getenv('BACKUP_MYSQLDUMP_EXTRA', '').split())

    logger.info('[backup] dump: %s', ' '.join(
        a for a in cmd if not a.startswith('--defaults-extra-file')))

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with gzip.open(destino, 'wb', compresslevel=6) as saida:
            shutil.copyfileobj(proc.stdout, saida, _CHUNK)
    finally:
        proc.stdout.close()
        erro = (proc.stderr.read() or b'').decode('utf8', 'replace').strip()
        proc.stderr.close()
        rc = proc.wait()

    if rc != 0:
        return False, 'mysqldump terminou com código %d: %s' % (rc, erro[:400])
    if erro:
        # Aviso do mysqldump com dump bom não reprova (ex.: deprecações).
        logger.warning('[backup] mysqldump avisou: %s', erro[:400])
    return True, ''


def _verificar(caminho: str) -> tuple:
    """CRC do gzip + marcador de dump completo. (ok, erro, bytes_crus)."""
    if not os.path.exists(caminho):
        return False, 'arquivo não encontrado depois do dump', 0

    brutos = 0
    cauda = b''
    try:
        with gzip.open(caminho, 'rb') as fh:
            while True:
                bloco = fh.read(_CHUNK)
                if not bloco:
                    break
                brutos += len(bloco)
                cauda = (cauda + bloco)[-4096:]
    except Exception as exc:
        return False, 'gzip corrompido (%s)' % exc, 0

    linhas = [ln.strip() for ln in cauda.splitlines() if ln.strip()]
    ultima = linhas[-1] if linhas else b''
    if _MARCADOR not in ultima:
        return False, 'marcador "Dump completed on" ausente — dump TRUNCADO', brutos

    tam = os.path.getsize(caminho)
    if tam < _MIN_BYTES:
        return False, 'dump com %d bytes, abaixo do piso de 1 MB' % tam, brutos

    return True, '', brutos


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------
def executar() -> dict:
    """Faz o backup inteiro e devolve o status (o mesmo que fica gravado).

    Nunca levanta exceção: qualquer falha vira ``ok=False`` com ``etapa`` e
    ``erro``, gravados em ``app_config`` para a tela contar o que houve.
    """
    inicio = time.time()
    cred = _credenciais()
    agora = datetime.now(_TZ)
    nome = '%s%s.sql.gz' % (_PREFIXO, agora.strftime('%Y-%m-%d_%H%M'))
    pasta_dbx = os.getenv('BACKUP_DROPBOX_PASTA', _PASTA_PADRAO).rstrip('/')
    destino_dbx = '%s/%s' % (pasta_dbx, nome)

    st = {
        'ok': False, 'etapa': 'inicio', 'erro': '', 'arquivo': nome,
        'destino': destino_dbx, 'bytes_gz': 0, 'bytes_crus': 0,
        'duracao_s': 0, 'quando': agora.strftime('%d/%m/%Y %H:%M'),
        'alerta': '',
    }

    anterior = ler_status()
    tmp = tempfile.mkdtemp(prefix='backup_bd_')
    local = os.path.join(tmp, nome)
    cnf = ''
    try:
        if not cred['nome'] or not cred['host']:
            st['etapa'] = 'credenciais'
            st['erro'] = 'DB_HOST/DB_NAME ausentes no ambiente do serviço.'
            return _fechar(st, inicio)

        binario = _achar_mysqldump()
        if not binario:
            st['etapa'] = 'mysqldump'
            st['erro'] = ('mysqldump não existe neste container — instale o '
                          'cliente MySQL no serviço de Cron '
                          '(NIXPACKS_APT_PKGS=default-mysql-client) ou aponte '
                          'BACKUP_MYSQLDUMP_BIN. Diagnóstico: %s'
                          % _diagnostico_dump())
            return _fechar(st, inicio)

        cnf = _escrever_cnf(tmp, cred)

        st['etapa'] = 'dump'
        ok, erro = _dump(binario, cnf, cred['nome'], local)
        if not ok:
            st['erro'] = erro
            return _fechar(st, inicio)

        st['etapa'] = 'verificacao'
        ok, erro, brutos = _verificar(local)
        st['bytes_gz'] = os.path.getsize(local) if os.path.exists(local) else 0
        st['bytes_crus'] = brutos
        if not ok:
            st['erro'] = erro
            return _fechar(st, inicio)

        # Encolhimento não reprova, mas fica escrito no card.
        ant = (anterior or {}).get('bytes_gz') or 0
        if ant and st['bytes_gz'] * 100 < ant * _ENCOLHEU_PCT:
            st['alerta'] = ('o backup encolheu de %s para %s desde o anterior'
                            % (_bytes_fmt(ant), _bytes_fmt(st['bytes_gz'])))
            logger.warning('[backup] %s', st['alerta'])

        st['etapa'] = 'envio'
        from utils import dropbox_sync
        svc = dropbox_sync.DropboxService()
        if not svc.is_configured():
            st['erro'] = ('credenciais do Dropbox ausentes no serviço '
                          '(DROPBOX_APP_KEY/SECRET/REFRESH_TOKEN).')
            return _fechar(st, inicio)

        svc.ensure_folder(pasta_dbx)
        if not svc.upload_arquivo(local, destino_dbx):
            st['erro'] = 'o Dropbox recusou o envio (ver log da rodada).'
            return _fechar(st, inicio)

        st['ok'] = True
        st['etapa'] = 'concluido'
        return _fechar(st, inicio)

    except Exception as exc:
        logger.exception('[backup] falha inesperada na etapa %s', st['etapa'])
        st['erro'] = '%s: %s' % (type(exc).__name__, exc)
        return _fechar(st, inicio)
    finally:
        # O arquivo local é descartável — a cópia que vale está no Dropbox.
        try:
            if cnf and os.path.exists(cnf):
                os.remove(cnf)
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


def _fechar(st: dict, inicio: float) -> dict:
    st['duracao_s'] = int(time.time() - inicio)
    if st['ok']:
        logger.info('[backup] OK — %s (%s) em %ds',
                    st['destino'], _bytes_fmt(st['bytes_gz']), st['duracao_s'])
    else:
        logger.error('[backup] FALHOU na etapa "%s": %s',
                     st['etapa'], st['erro'])
    _gravar_status(st)
    return st
