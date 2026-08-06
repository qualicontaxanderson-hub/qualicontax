# -*- coding: utf-8 -*-
"""Entrypoint do CRON do ROTEADOR de arquivos fiscais (Railway Cron).

Por que existe
--------------
Desde a aposentadoria da ponte /Fiscal, as FONTES gravam DIRETO no destino final:
a captura SEFAZ, o Q-Robô e o import escrevem em EMPRESAS/.../FISCAL/{SENTIDO}. O
roteador deixou de ser a ponte de reclassificação e ficou como ÚNICO cérebro da
_ENTRADA — a caixa plana onde caem drops manuais de .xml. Recolhe o que estiver
nela, classifica com a MESMA lógica do classificar_fiscal.py já validada no
backlog local, LANÇA no banco e move para

    EMPRESAS/<nº - razão>/FISCAL/<ENTRADAS|SAIDAS|CTE|EVENTOS>/<ano>/<mês>

(O 'Fiscal/IMPORTADOS' saiu do PASTAS_ORIGEM quando a /Fiscal foi drenada e
arquivada: mantê-lo faria o ensure_folder RECRIAR a /Fiscal a cada tick.)

LANÇA no banco — a metade que faltava
-------------------------------------
Até aqui o roteador SÓ MOVIA: quem lançava em nfe_importacoes/cte_documentos
era o job do blueprint, que lê a pasta /Fiscal/NOVO. Um .xml jogado na _ENTRADA
era arquivado em EMPRESAS e NUNCA entrava no banco — buraco aberto desde o
go-live (04/08), que só não gerou dano porque ninguém havia usado a _ENTRADA.

Agora cada .xml passa por utils.fiscal_ingest.importar_xml ANTES do move, com o
MESMO core do upload manual. A ordem é o ponto: import falhou → o arquivo fica
na origem e volta no próximo tick; import passou e o move falhou → o tick
seguinte reimporta e o dedup por (chave, cliente, tipo) devolve 'dup'. Os dois
lados são idempotentes; o caminho impossível é arquivar sem lançar.

Kill switch: ROTEADOR_IMPORTA=0 volta ao comportamento antigo (só move).

Molde igual ao cron_captura_dfe.py: serviço de Cron próprio no Railway, Start
Command ``python cron_roteador.py``, schedule ``*/15 * * * *``. O processo sobe,
roda UMA rodada e SAI — o trigger é do Railway, não de uma thread viva.

Cold-start enxuto — de propósito
--------------------------------
NÃO importa ``app``: sem Flask, sem blueprints, sem migrations. db_helper,
models e dropbox_sync não tocam current_app/flask.g, então não é preciso
app_context.

Guardas de ambiente
-------------------
  ROTEADOR_ATIVO=1     obrigatório; sem isso a rodada aborta na primeira linha.
  ROTEADOR_DRYRUN=1    (DEFAULT) classifica e grava no roteador_log como
                       'SIMULADO', mas NUNCA chama move_file. O default é 1 de
                       propósito: esquecer de configurar não pode mover arquivo.
  ROTEADOR_MAX_ARQ     teto de arquivos por rodada (default 500).
  ROTEADOR_PRAZO_SEG   prazo suave em segundos (default 600); o resto vai no
                       próximo tick.

Travas
------
  * GET_LOCK('roteador', 0) numa conexão dedicada — dois ticks não se atropelam.
  * NÃO SOBRESCREVE: _path_exists(destino) antes de mover. Se existir, o arquivo
    FICA na origem e vira CONFLITO. (dropbox_sync.move_file sobrescreve por
    padrão — apaga o destino e repete o move. Esta checagem é o que impede isso.)
  * Dedupe intra-rodada: dois arquivos com o mesmo destino → o 2º vira CONFLITO.
  * REVISAR/SEM_MATCH ficam na origem, com o motivo registrado.
  * Exceção POR ARQUIVO é engolida (vira linha 'ERRO'): um XML corrompido não
    derruba a rodada.

Exit code
---------
0 salvo falha de BOOT (import/logging/lock). Erro de arquivo não muda o exit —
senão o Railway marcaria a execução como "failed" por um XML ruim.
"""
import logging
import os
import sys
import time

logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO),
    stream=sys.stdout,
    format='%(asctime)s %(levelname)s [pid=%(process)d] %(name)s: %(message)s',
    force=True,
)
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

logger = logging.getLogger(__name__)

import re                                              # noqa: E402
import xml.etree.ElementTree as ET                     # noqa: E402
from datetime import datetime, timezone, timedelta     # noqa: E402

_TZ_BR = timezone(timedelta(hours=-3))
CHAVE_RE = re.compile(r'\d{44}')
NAO_DIGITO = re.compile(r'\D+')
MESES = {f'{i:02d}' for i in range(1, 13)}

PASTAS_ORIGEM = ['_ENTRADA']

# REGRA DE FERRO: este cron só toca em arquivo .xml. NADA MAIS.
#
# A _ENTRADA é a caixa de entrada ÚNICA e PLANA do sistema — chega de tudo nela e
# cada tipo tem seu consumidor. Em especial, o .pfx de um certificado digital fica
# na _ENTRADA até alguém clicar em "Vincular Certificado" na tela da empresa (é a
# rota clientes.certificado_vincular que o move para EMPRESAS/{empresa}/CERTIFICADO,
# depois de validar a senha e conferir o titular).
#
# Portanto: qualquer extensão diferente de .xml (.pfx, .pdf, .zip, ...) é IGNORADA
# — não move, não renomeia, não apaga, não baixa. A checagem é uma WHITELIST (só
# passa o que termina em .xml), e não uma lista de proibidos: extensão nova que
# apareça amanhã já nasce ignorada, sem precisar de manutenção aqui.
EXTENSAO_PROCESSADA = '.xml'

ATIVO = os.getenv('ROTEADOR_ATIVO', '0').strip() == '1'
DRYRUN = os.getenv('ROTEADOR_DRYRUN', '1').strip() == '1'
# Kill switch do LANÇAMENTO. Com IMPORTA=0 o roteador volta a ser o que era —
# só move — e o buraco reabre. Existe para desarmar em produção sem redeploy.
IMPORTA = os.getenv('ROTEADOR_IMPORTA', '1').strip() == '1'
MAX_ARQ = max(1, int(os.getenv('ROTEADOR_MAX_ARQ', '500')))
PRAZO_SEG = max(30, int(os.getenv('ROTEADOR_PRAZO_SEG', '600')))


# ==========================================================================
# Classificação — PORTE FIEL do classificar_fiscal.py (validado no backlog).
# Única diferença: opera sobre BYTES (download_file) em vez de caminho local,
# e monta caminho de Dropbox ('/') em vez de os.path.join ('\' no Windows).
# ==========================================================================
def so_digitos(s):
    return NAO_DIGITO.sub('', s or '')


def local(tag):
    return tag.split('}')[-1] if tag else tag


def parse_xml_bytes(dados):
    try:
        return ET.fromstring(dados)
    except Exception:
        try:
            i = dados.find(b'<')
            return ET.fromstring(dados[i:] if i > 0 else dados)
        except Exception:
            return None


def texto_local(root, nome):
    for e in root.iter():
        if local(e.tag) == nome and e.text:
            return e.text.strip()
    return None


def cnpj_do_grupo(root, grupo_local):
    for e in root.iter():
        if local(e.tag) == grupo_local:
            for c in e.iter():
                if local(c.tag) in ('CNPJ', 'CPF'):
                    d = so_digitos(c.text)
                    if d:
                        return d
    return None


def todos_cnpjs(root):
    out = []
    for c in root.iter():
        if local(c.tag) in ('CNPJ', 'CPF'):
            d = so_digitos(c.text)
            if d:
                out.append(d)
    return out


def achar_chave(root, nome_arquivo):
    if root is not None:
        for tag in ('chNFe', 'chCTe', 'chMDFe'):
            v = texto_local(root, tag)
            if v:
                m = CHAVE_RE.search(v)
                if m:
                    return m.group()
        for e in root.iter():
            idv = e.get('Id') if hasattr(e, 'get') else None
            if idv:
                m = CHAVE_RE.search(idv)
                if m:
                    return m.group()
    m = CHAVE_RE.search(nome_arquivo or '')
    return m.group() if m else None


def ano_mes(dh, chave):
    if dh and len(dh) >= 7 and dh[4] == '-':
        return dh[0:4], dh[5:7]
    if chave and len(chave) >= 6 and chave[4:6] in MESES:
        return '20' + chave[2:4], chave[4:6]
    return 'SEM_DATA', 'SEM_DATA'


def modelo_do(root, chave):
    m = texto_local(root, 'mod') if root is not None else None
    if m:
        return so_digitos(m)[:2]
    return chave[20:22] if chave and len(chave) >= 22 else ''


def pasta_empresa(numero, razao):
    razao_limpa = re.sub(r'[\\/:*?"<>|]', '', (razao or '')).strip()
    return f'{numero} - {razao_limpa}'


def classificar(dados, nome_arquivo, empresas):
    """Devolve {tipo_doc, sentido, empresa_numero, empresa_razao, ano, mes,
    status, motivo}. status ∈ OK | REVISAR | SEM_MATCH."""
    info = {'tipo_doc': '', 'sentido': '', 'empresa_numero': '', 'empresa_razao': '',
            'ano': '', 'mes': '', 'status': '', 'motivo': ''}
    root = parse_xml_bytes(dados)
    if root is None:
        info.update(status='REVISAR', motivo='XML ilegivel/corrompido')
        return info

    raiz = local(root.tag)
    chave = achar_chave(root, nome_arquivo)
    modelo = modelo_do(root, chave)
    emit = cnpj_do_grupo(root, 'emit')
    dest = cnpj_do_grupo(root, 'dest')
    dh = (texto_local(root, 'dhEmi') or texto_local(root, 'dEmi')
          or texto_local(root, 'dhEvento'))
    info['ano'], info['mes'] = ano_mes(dh, chave)

    if raiz.lower().startswith('procevento') or raiz.lower() == 'evento':
        info['tipo_doc'] = 'EVENTO'
        autor = (chave[6:20] if chave and len(chave) >= 20 else emit)
        if autor and autor in empresas:
            num, razao = empresas[autor]
            info.update(sentido='EVENTOS', empresa_numero=num, empresa_razao=razao,
                        status='OK', motivo='Evento do proprio cliente (emitente)')
        else:
            info.update(status='REVISAR',
                        motivo='Evento de nota de terceiro (definir pela chave da nota)')
        return info

    if 'inut' in raiz.lower():
        info.update(tipo_doc='INUTILIZACAO', status='REVISAR',
                    motivo='Inutilizacao de numeracao')
        return info

    if 'cte' in raiz.lower() or modelo == '57':
        info['tipo_doc'] = 'CTE'
        donos = [c for c in todos_cnpjs(root) if c in empresas]
        if donos:
            num, razao = empresas[donos[0]]
            info.update(sentido='CTE', empresa_numero=num, empresa_razao=razao,
                        status='OK', motivo='CT-e (cliente e uma das partes)')
        else:
            info.update(status='SEM_MATCH', motivo='Nenhuma parte do CT-e e cliente')
        return info

    info['tipo_doc'] = 'NFCE' if modelo == '65' else 'NFE'
    dono = sentido = None
    if dest and dest in empresas:
        dono, sentido = dest, 'ENTRADAS'
    elif emit and emit in empresas:
        dono, sentido = emit, 'SAIDAS'
    else:
        donos = [c for c in todos_cnpjs(root) if c in empresas]
        if donos:
            dono = donos[0]
            sentido = 'SAIDAS' if dono == emit else 'ENTRADAS'
    if modelo == '65' and emit and emit in empresas:
        dono, sentido = emit, 'SAIDAS'

    if dono:
        num, razao = empresas[dono]
        info.update(sentido=sentido, empresa_numero=num, empresa_razao=razao,
                    status='OK', motivo=f'{info["tipo_doc"]} {sentido.lower()}')
    else:
        info.update(status='SEM_MATCH',
                    motivo='Emitente e destinatario nao sao clientes')
    return info


# ==========================================================================
# Infra: mapa do BANCO, log, lock
# ==========================================================================
def _mapa_empresas():
    """{cnpj_só_dígitos: (numero_cliente, razão)} direto do banco — sem CSV."""
    from utils.db_helper import execute_query
    rows = execute_query(
        "SELECT numero_cliente, cpf_cnpj, nome_razao_social FROM clientes "
        "WHERE cpf_cnpj IS NOT NULL AND cpf_cnpj <> ''", fetch=True) or []
    mapa = {}
    for r in rows:
        d = so_digitos(r.get('cpf_cnpj'))
        if len(d) >= 11:
            mapa[d] = ((r.get('numero_cliente') or '').strip(),
                       r.get('nome_razao_social') or '')
    return mapa


def _log(rodada, origem, destino, resultado, tipo_doc=None,
         empresa_numero=None, motivo=None):
    """Uma linha por arquivo avaliado. Best-effort: logar NUNCA derruba a rodada."""
    from utils.db_helper import execute_query
    try:
        execute_query(
            "INSERT INTO roteador_log (rodada, origem, destino, resultado, "
            "  tipo_doc, empresa_numero, motivo) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (rodada, origem[:500], (destino or None) and destino[:500], resultado,
             (tipo_doc or None), (empresa_numero or None),
             (motivo or None) and motivo[:200]))
    except Exception:
        logger.exception('[roteador] falha ao gravar roteador_log (segue a rodada)')


def _conectar_lock():
    """Conexão dedicada para segurar o GET_LOCK durante toda a rodada."""
    import mysql.connector
    from config import Config
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT,
        autocommit=True, time_zone='-03:00')


# ==========================================================================
# A rodada
# ==========================================================================
def rodar():
    from utils import dropbox_sync

    logger.warning('[roteador] >>> rodada INVOCADA (pid=%s, dry_run=%s).',
                   os.getpid(), DRYRUN)
    if not ATIVO:
        logger.warning('[roteador] ABORTADO: ROTEADOR_ATIVO != 1.')
        return

    svc = dropbox_sync._service
    if not svc.is_configured():
        logger.error('[roteador] Dropbox nao configurado — nada a fazer.')
        return

    conn = _conectar_lock()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT GET_LOCK('roteador', 0)")
    if (cur.fetchone() or [0])[0] != 1:
        logger.info('[roteador] lock ocupado — outro tick em andamento; pulando.')
        cur.close()
        conn.close()
        return

    rodada = datetime.now(_TZ_BR).strftime('%Y%m%d%H%M%S')
    prazo = time.monotonic() + PRAZO_SEG
    n = {'MOVIDO': 0, 'SIMULADO': 0, 'CONFLITO': 0,
         'REVISAR': 0, 'SEM_MATCH': 0, 'ERRO': 0,
         # não-XML deixados em paz (o .pfx à espera de vínculo cai aqui)
         'IGNORADO': 0}
    n_imp = {}              # ok/dup/skip/off — placar do LANÇAMENTO
    vistos = set()          # dedupe intra-rodada por destino
    total = 0

    try:
        empresas = _mapa_empresas()
        logger.info('[roteador] mapa: %d CNPJs de clientes.', len(empresas))

        # Cache de clientes por documento, UMA vez por rodada (o mesmo que o
        # upload manual monta). Só é necessário quando vamos lançar.
        doc_cache = {}
        importar_xml = None
        if IMPORTA and not DRYRUN:
            from utils.fiscal_ingest import _build_cliente_doc_cache, importar_xml
            doc_cache = _build_cliente_doc_cache()
            logger.info('[roteador] import LIGADO — cache de %d documentos.',
                        len(doc_cache))
        else:
            logger.warning('[roteador] import DESLIGADO (importa=%s, dry_run=%s) '
                           '— arquivos serao apenas ARQUIVADOS, sem lancamento.',
                           IMPORTA, DRYRUN)

        for rel in PASTAS_ORIGEM:
            base = svc._build_path(*rel.split('/'))
            svc.ensure_folder(base)          # _ENTRADA pode ainda não existir
            try:
                itens = svc.list_folder(base, recursive=True)
            except Exception:
                logger.exception('[roteador] falha ao listar %s — pulando a pasta.', base)
                continue

            for item in itens:
                if total >= MAX_ARQ:
                    logger.info('[roteador] teto de %d arquivos atingido; '
                                'o resto vai no proximo tick.', MAX_ARQ)
                    break
                if time.monotonic() > prazo:
                    logger.info('[roteador] prazo suave (%ds) atingido; '
                                'o resto vai no proximo tick.', PRAZO_SEG)
                    break
                nome = item.get('name') or ''
                if not item.get('is_file'):
                    continue
                # REGRA DE FERRO (ver EXTENSAO_PROCESSADA no topo): não é .xml,
                # passa longe. O .pfx aguardando vínculo mora aqui na _ENTRADA e
                # NÃO pode ser tocado por este cron.
                if not nome.lower().endswith(EXTENSAO_PROCESSADA):
                    n['IGNORADO'] += 1
                    continue
                origem = item.get('path')
                total += 1

                # Exceção POR ARQUIVO: um XML ruim vira linha 'ERRO' e a rodada segue.
                try:
                    dados = svc.download_file(origem)
                    if not dados:
                        n['ERRO'] += 1
                        _log(rodada, origem, None, 'ERRO', motivo='download vazio/falhou')
                        continue

                    info = classificar(dados, nome, empresas)
                    if info['status'] != 'OK':
                        n[info['status']] += 1
                        _log(rodada, origem, None, info['status'],
                             tipo_doc=info['tipo_doc'], motivo=info['motivo'])
                        continue

                    # LANÇAMENTO ANTES DO ARQUIVAMENTO.
                    #
                    # A ordem é o coração da correção. Se o import falha, o
                    # arquivo FICA na origem e volta no próximo tick — nunca
                    # some do radar. Se o import passa e o move falha, o tick
                    # seguinte reimporta e o dedup devolve 'dup', sem dano.
                    # O caminho impossível é o antigo: arquivar sem lançar.
                    #
                    # Vem ANTES até do teste de CONFLITO de propósito: um
                    # homônimo no destino significa que o ARQUIVO é duplicado,
                    # não que a NOTA já esteja no banco.
                    imp = 'off'
                    if importar_xml is not None:
                        imp, imp_motivo = importar_xml(nome, dados, doc_cache)
                        if imp == 'erro':
                            n['ERRO'] += 1
                            _log(rodada, origem, None, 'ERRO',
                                 tipo_doc=info['tipo_doc'],
                                 empresa_numero=info['empresa_numero'],
                                 motivo=f'import: {imp_motivo}')
                            continue        # NÃO arquiva o que não foi lançado

                    destino_dir = svc._build_path(
                        'EMPRESAS',
                        pasta_empresa(info['empresa_numero'], info['empresa_razao']),
                        'FISCAL', info['sentido'], info['ano'], info['mes'])
                    destino = f'{destino_dir}/{nome}'

                    # TRAVA (a): move_file SOBRESCREVE por padrão. Sem esta
                    # checagem, um homônimo no destino seria APAGADO.
                    if destino.lower() in vistos or svc._path_exists(destino):
                        n['CONFLITO'] += 1
                        _log(rodada, origem, destino, 'CONFLITO',
                             tipo_doc=info['tipo_doc'],
                             empresa_numero=info['empresa_numero'],
                             motivo=f'destino ja existe — ficou na origem [import={imp}]')
                        continue

                    if DRYRUN:
                        n['SIMULADO'] += 1
                        _log(rodada, origem, destino, 'SIMULADO',
                             tipo_doc=info['tipo_doc'],
                             empresa_numero=info['empresa_numero'],
                             motivo=info['motivo'])
                        vistos.add(destino.lower())
                        continue

                    svc.ensure_folder(destino_dir)
                    if svc.move_file(origem, destino):
                        n['MOVIDO'] += 1
                        n_imp[imp] = n_imp.get(imp, 0) + 1
                        vistos.add(destino.lower())
                        _log(rodada, origem, destino, 'MOVIDO',
                             tipo_doc=info['tipo_doc'],
                             empresa_numero=info['empresa_numero'],
                             motivo=f"{info['motivo']} [import={imp}]")
                    else:
                        n['ERRO'] += 1
                        _log(rodada, origem, destino, 'ERRO',
                             motivo='move_file retornou False')
                except Exception as exc:
                    n['ERRO'] += 1
                    logger.exception('[roteador] erro no arquivo %s', origem)
                    _log(rodada, origem, None, 'ERRO', motivo=str(exc)[:200])
            else:
                continue
            break        # respeita teto/prazo saindo das duas pastas

        logger.warning('[roteador] rodada %s: %d avaliados | movidos=%d simulados=%d '
                       'conflitos=%d revisar=%d sem_match=%d erros=%d | '
                       'ignorados_nao_xml=%d | lancamento=%s',
                       rodada, total, n['MOVIDO'], n['SIMULADO'], n['CONFLITO'],
                       n['REVISAR'], n['SEM_MATCH'], n['ERRO'], n['IGNORADO'],
                       dict(sorted(n_imp.items())) or '{}')
    finally:
        try:
            cur.execute("SELECT RELEASE_LOCK('roteador')")
            cur.fetchall()
        except Exception:
            pass
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def main() -> int:
    logger.warning('[roteador] >>> Cron do roteador INICIADO (pid=%s).', os.getpid())
    try:
        rodar()
    except Exception:
        # Erro de BOOT/lock/mapa: loga e sai != 0 para o Railway marcar "failed".
        logger.exception('[roteador] rodada abortada por erro de infraestrutura.')
        return 1
    logger.warning('[roteador] >>> Cron do roteador CONCLUIDO (pid=%s).', os.getpid())
    return 0


if __name__ == '__main__':
    sys.exit(main())
