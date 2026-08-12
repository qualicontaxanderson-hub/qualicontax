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

import hashlib                                         # noqa: E402
import re                                              # noqa: E402
import xml.etree.ElementTree as ET                     # noqa: E402
from datetime import datetime, timezone, timedelta     # noqa: E402

_TZ_BR = timezone(timedelta(hours=-3))
CHAVE_RE = re.compile(r'\d{44}')
NAO_DIGITO = re.compile(r'\D+')
MESES = {f'{i:02d}' for i in range(1, 13)}

PASTAS_ORIGEM = ['_ENTRADA']

# Ator de máquina do roteador na auditoria. usuario_id fica NULL: não existe
# linha em `usuarios` para um cron, e a auditoria não depende da FK — nome e
# login são copiados no ato (ver utils/atividade.py).
_ATOR_NOME = 'ROTEADOR (_ENTRADA)'
_ATOR_LOGIN = 'roteador'

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


def classificar(dados, nome_arquivo, empresas, dono_por_chave=None):
    """Devolve {tipo_doc, sentido, empresa_numero, empresa_razao, ano, mes,
    status, motivo}. status ∈ OK | REVISAR | SEM_MATCH.

    ``dono_por_chave`` (opcional): callable chave44 -> {'numero','razao'} | None,
    usado SÓ para evento. Sem ele o comportamento é exatamente o de antes.
    """
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
        # 1º) O DONO DA NOTA. A chave do evento É a chave da nota; se a nota está
        # no sistema, o evento é do cliente dela — mesmo que o emitente seja um
        # fornecedor de fora da base. Era exatamente isto que faltava: um
        # cancelamento de COMPRA é sempre emitido pelo fornecedor, então a regra
        # antiga (autor da chave) mandava todo cancelamento de entrada para
        # REVISAR, e a nota continuava aparecendo ativa na tela.
        dono = dono_por_chave(chave) if (dono_por_chave and chave) else None
        if dono:
            info.update(sentido='EVENTOS', empresa_numero=dono['numero'],
                        empresa_razao=dono['razao'], status='OK',
                        motivo='Evento da nota do cliente (dono pela chave)')
            return info
        # 2º) Fallback: o autor do evento é cliente (cancelamento da própria
        # SAÍDA, em que emitente e dono coincidem). Comportamento de sempre.
        autor = (chave[6:20] if chave and len(chave) >= 20 else emit)
        if autor and autor in empresas:
            num, razao = empresas[autor]
            info.update(sentido='EVENTOS', empresa_numero=num, empresa_razao=razao,
                        status='OK', motivo='Evento do proprio cliente (emitente)')
        else:
            info.update(status='REVISAR',
                        motivo='Evento de nota que nao esta no sistema')
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
# IDENTIDADE DO DOCUMENTO — lida de DENTRO do XML
#
# O NOME DO ARQUIVO NÃO DECIDE NADA. Nome é apelido: o usuário renomeia, o
# Windows põe " (1)", o agente do Q-Colabore gera nome livre quando o dele já
# está ocupado. Quem diz "é a mesma nota" é a chave de acesso que está no
# conteúdo — e, no caso de evento, o Id do próprio evento.
# ==========================================================================
def _elem(root, nome):
    """Primeiro elemento com esse nome local (ignora namespace)."""
    for e in root.iter():
        if local(e.tag) == nome:
            return e
    return None


def _attr_id(el):
    return ((el.get('Id') or el.get('id') or '').strip()) if el is not None else ''


def dv_ok(chave):
    """Dígito verificador da chave (módulo 11, pesos 2..9 da direita p/ esquerda).

    Pega chave truncada, digitada errada ou remontada de nome de arquivo. Sem
    isto, um XML corrompido entraria na pasta da empresa com identidade falsa.
    """
    if not chave or len(chave) != 44 or not chave.isdigit():
        return False
    soma, peso = 0, 2
    for d in reversed(chave[:43]):
        soma += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    dv = 0 if resto in (0, 1) else 11 - resto
    return dv == int(chave[43])


def chave_em(texto):
    """Primeira janela de 44 dígitos com DV VÁLIDO dentro de ``texto``. '' se não há.

    Por que janela deslizante e não ``\\d{44}``: o Id de um evento é
    ``ID`` + tpEvento(6) + chave(44) + nSeqEvento(2) — 52 dígitos seguidos. Um
    ``\\d{44}`` casa os 44 PRIMEIROS, que são ``110110`` + os 38 primeiros
    dígitos da chave: uma chave que não existe. Medido no acervo: essa leitura
    ingênua recusava 2.920 eventos e marcava 13.567 nomes como DV inválido.

    O DV é o que desambigua — a janela certa passa no módulo 11 e as vizinhas
    não. Custa nada: são poucas dezenas de posições numa string curta.
    """
    d = NAO_DIGITO.sub('', texto or '')
    for i in range(0, max(0, len(d) - 43)):
        cand = d[i:i + 44]
        if dv_ok(cand):
            return cand
    return ''


def identidade(root):
    """Identidade do DOCUMENTO. Devolve ``(ident, chave, erro)``.

    ``ident``  string que identifica ESTE documento (é o que dedupe compara);
    ``chave``  a chave de acesso de 44 dígitos (vai para log/auditoria);
    ``erro``   != None → o arquivo NÃO pode ser arquivado (vira REVISAR).

    EVENTO tem regra própria, e é por um motivo concreto: a chNFe de um evento é
    a chave da NOTA, não do evento. Duas Cartas de Correção da mesma nota
    (nSeqEvento 1 e 2) são documentos DIFERENTES que têm de coexistir na pasta —
    se a identidade deles fosse a chNFe, a segunda apagaria a primeira. Então a
    identidade do evento é o Id do infEvento (que embute tpEvento + chave + seq).
    Medido: hoje a pasta do cliente 106 tem 122 arquivos para 121 chaves, e a
    "chave repetida" é exatamente uma nota + a CC-e dela — documentos distintos.

    Pela mesma razão, a conferência "Id bate com o protocolo" só se aplica
    quando HÁ protocolo. Arquivo sem protNFe não é corrompido: é não-autorizado,
    e o PASSO 2 é que decide entre ele e um protocolado. Exigir os dois sempre
    mandaria todo XML sem protocolo para REVISAR.
    """
    raiz = local(root.tag).lower()

    if raiz.startswith('procevento') or raiz.startswith('retevento') \
            or raiz.startswith('evento'):
        inf = _elem(root, 'infEvento')
        idv = _attr_id(inf)
        # O Id É a fonte da verdade do OBJETO do evento — vem assinado pela
        # SEFAZ e tem forma fixa: ID + tpEvento(6) + chave(44) + nSeq. Daí a
        # fatia [6:50], conferida pelo DV.
        #
        # Não se pode escolher a chave pela tag: num procEventoCTe de
        # Comprovante de Entrega (110180) existe um <chNFe> que é a NOTA
        # TRANSPORTADA, não o CT-e do evento. Preferir chNFe fazia o evento ser
        # recusado — 2 casos no acervo do cliente 162.
        d_id = NAO_DIGITO.sub('', idv)
        chave = d_id[6:50] if (len(d_id) >= 50 and dv_ok(d_id[6:50])) else chave_em(idv)
        marcadas = [so_digitos(texto_local(root, t) or '')
                    for t in ('chCTe', 'chNFe')]
        marcadas = [c for c in marcadas if c]
        if chave and marcadas and chave not in marcadas:
            return None, chave, 'evento: chave do Id nao confere com chNFe/chCTe'
        if not chave:
            chave = marcadas[0] if marcadas else ''
        if not chave:
            return None, '', 'evento sem chave de 44 digitos'
        if not dv_ok(chave):
            return None, chave, 'digito verificador da chave invalido'
        if not idv:
            return None, chave, 'evento sem Id — nao ha como identifica-lo'
        return idv, chave, None

    inf = None
    for tag in ('infNFe', 'infCte', 'infCteOS', 'infGTVe'):
        inf = _elem(root, tag)
        if inf is not None:
            break
    ch_id = chave_em(_attr_id(inf))
    # A chave do protocolo sai de DENTRO do protNFe/protCTe, não de qualquer
    # lugar do documento: um CT-e lista as chNFe das notas que TRANSPORTA, e
    # comparar o Id do CT-e com a chave de uma nota transportada dava
    # divergência falsa — 78 CT-e recusados na medição contra o acervo.
    prot = _elem(root, 'protNFe') or _elem(root, 'protCTe')
    ch_prot = so_digitos(
        (texto_local(prot, 'chNFe') or texto_local(prot, 'chCTe') or '')
        if prot is not None else '')
    if not ch_id and not ch_prot:
        return None, '', 'sem chave de 44 digitos no conteudo (Id/protocolo)'
    if ch_id and ch_prot and ch_id != ch_prot:
        return None, ch_id, 'chave do Id difere da chave do protocolo'
    chave = ch_id or ch_prot
    if not dv_ok(chave):
        return None, chave, 'digito verificador da chave invalido'
    return chave, chave, None


# ==========================================================================
# QUALIDADE — qual das duas cópias fica (PASSO 2)
# ==========================================================================
_RE_CSTAT_100 = re.compile(r'<cStat>\s*100\s*</cStat>')
_RE_DET = re.compile(r'<det\b')


def qualidade(dados):
    """(autorizado, assinado, completa) — tupla comparável: MAIOR ganha.

    A ordem dos campos É a ordem de desempate pedida:
      1. protocolo de autorização com cStat 100;
      2. assinatura digital presente;
      3. nota completa (tem <det>) em vez de resumo.
    Empate em tudo → fica a que já está arquivada (o chamador decide).
    """
    t = dados.decode('utf-8', 'replace')
    autorizado = 1 if (('<protNFe' in t or '<protCTe' in t)
                       and _RE_CSTAT_100.search(t)) else 0
    return (autorizado, 1 if '<Signature' in t else 0,
            1 if _RE_DET.search(t) else 0)


def rotulo_criterio(q_novo, q_atual):
    """Qual critério decidiu — para a auditoria dizer POR QUE substituiu."""
    for i, nome in enumerate(('protocolo', 'assinatura', 'completa')):
        if q_novo[i] != q_atual[i]:
            return nome
    return 'empate'


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


def _auditar_substituicao(chave, criterio, b_atual, b_novo, sha_atual, sha_novo,
                          empresa_numero):
    """Auditoria da SUBSTITUIÇÃO de um arquivo já arquivado.

    Só é chamada quando o conteúdo guardado MUDA. O porquê: o banco não é
    tocado (é a mesma nota, o 'dup' está certo), então a pasta passa a contar
    uma história diferente da que contava — e isso não pode acontecer em
    silêncio.

    NUNCA o nome do arquivo: a linha identifica o DOCUMENTO pela chave.
    Best-effort — falhar aqui não desfaz a substituição nem derruba a rodada.
    """
    try:
        from utils.atividade import registrar_agente
        registrar_agente(
            'escrita.substituiu_arquivo_arquivado', 'fiscal',
            usuario_id=None, usuario_nome=_ATOR_NOME, usuario_login=_ATOR_LOGIN,
            tabela=None, registro_id=None,
            depois={'chave_acesso': chave, 'criterio': criterio,
                    'empresa_numero': empresa_numero,
                    'tamanho_anterior': len(b_atual), 'tamanho_novo': len(b_novo),
                    'sha256_anterior': sha_atual, 'sha256_novo': sha_novo})
    except Exception:
        logger.exception('[roteador] falha ao auditar substituicao de %s '
                         '(a substituicao VALE).', chave)


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
# ÍNDICE DA PASTA DE DESTINO — chave -> arquivos já lá
#
# Custo (PASSO 4): UM list_folder por pasta de destino tocada na rodada, e
# reuso pelo resto da rodada. NÃO se baixa a pasta inteira: o nome do arquivo
# serve de PISTA para achar o candidato, e só o candidato é baixado — e é o
# CONTEÚDO dele que decide. A pista nunca decide sozinha.
#
# Arquivo cujo nome não traz chave não tem pista: esse precisa ser baixado para
# entrar no índice. Medido na árvore inteira do Dropbox: 66.589 .xml, ZERO sem
# chave no nome — então hoje esse custo é zero. O contador existe para o dia em
# que deixar de ser.
# ==========================================================================
def indice_destino(svc, destino_dir, cache):
    """{chave44: [caminhos]} da pasta, montado uma vez por rodada."""
    if destino_dir in cache:
        return cache[destino_dir]
    idx = {}
    sem_pista = []
    try:
        itens = svc.list_folder(destino_dir, recursive=False)
    except Exception:
        # Pasta nova (ainda não existe) ou falha de listagem: índice vazio. O
        # _path_exists do fluxo normal continua sendo a rede de segurança.
        itens = []
    for it in itens:
        if not it.get('is_file'):
            continue
        nome = it.get('name') or ''
        if not nome.lower().endswith(EXTENSAO_PROCESSADA):
            continue
        # chave_em, não CHAVE_RE: o nome de evento é ID<tpEvento><chave><seq>,
        # e um \d{44} cru extrai a janela errada — o índice ficaria com uma
        # chave inexistente e NUNCA casaria com o documento.
        ch_nome = chave_em(nome)
        if ch_nome:
            idx.setdefault(ch_nome, []).append(it['path'])
        else:
            sem_pista.append(it['path'])
    for caminho in sem_pista:
        try:
            b = svc.download_file(caminho)
            r = parse_xml_bytes(b) if b else None
            if r is not None:
                _ident, ch, err = identidade(r)
                if ch and not err:
                    idx.setdefault(ch, []).append(caminho)
        except Exception:
            logger.exception('[roteador] falha ao indexar %s', caminho)
    if sem_pista:
        logger.info('[roteador] indice de %s: %d arquivo(s) sem chave no nome '
                    'precisaram ser lidos.', destino_dir, len(sem_pista))
    cache[destino_dir] = idx
    return idx


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
         # SUBSTITUIDO: o mesmo documento já estava lá e ficou UMA cópia (a de
         # melhor integridade). DESCARTADO: a que já estava era melhor/igual e
         # a nova saiu da _ENTRADA para a quarentena.
         'SUBSTITUIDO': 0, 'DESCARTADO': 0,
         # não-XML deixados em paz (o .pfx à espera de vínculo cai aqui)
         'IGNORADO': 0}
    n_imp = {}              # ok/dup/skip/off — placar do LANÇAMENTO
    # Placar do DRYRUN. São contadores PRÓPRIOS, de propósito: o vocabulário de
    # roteador_log.resultado continua sendo SIMULADO na simulação — a decisão
    # que SERIA tomada vai no motivo e aqui, não num resultado novo.
    sim = {'substituiria': 0, 'descartaria': 0, 'novos': 0}
    vistos = set()          # dedupe intra-rodada por CAMINHO de destino
    idents_vistos = set()   # dedupe intra-rodada pela IDENTIDADE do documento
    idx_cache = {}          # {pasta_destino: {chave: [caminhos]}} — 1x por rodada
    total = 0

    try:
        empresas = _mapa_empresas()
        logger.info('[roteador] mapa: %d CNPJs de clientes.', len(empresas))

        # Cache de clientes por documento, UMA vez por rodada (o mesmo que o
        # upload manual monta). Só é necessário quando vamos lançar.
        # Dono do EVENTO pela chave da nota. Consulta o banco, então é montado
        # aqui e passado ao classificar() — que segue puro para quem não o usa.
        # Memoiza por rodada: um lote pode trazer vários eventos da mesma nota.
        from utils.fiscal_ingest import dono_da_nota
        _cache_dono = {}

        def dono_por_chave(chave):
            if chave not in _cache_dono:
                try:
                    _cache_dono[chave] = dono_da_nota(chave)
                except Exception:
                    logger.exception('[roteador] falha ao resolver dono da chave %s', chave)
                    _cache_dono[chave] = None
            return _cache_dono[chave]

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

                    info = classificar(dados, nome, empresas, dono_por_chave)
                    if info['status'] != 'OK':
                        n[info['status']] += 1
                        _log(rodada, origem, None, info['status'],
                             tipo_doc=info['tipo_doc'], motivo=info['motivo'])
                        continue

                    # IDENTIDADE PELO CONTEÚDO — antes de lançar e antes de
                    # arquivar. Arquivo cuja identidade não se sustenta (chave do
                    # Id divergindo do protocolo, dígito verificador errado) NÃO
                    # entra na pasta da empresa nem no banco: vira REVISAR e fica
                    # na origem para alguém olhar.
                    _root = parse_xml_bytes(dados)
                    ident, chave, err_ident = (
                        identidade(_root) if _root is not None
                        else (None, '', 'XML ilegivel'))
                    if err_ident or not ident:
                        n['REVISAR'] += 1
                        logger.warning('[roteador] identidade recusada em %s: %s',
                                       origem, err_ident)
                        _log(rodada, origem, None, 'REVISAR',
                             tipo_doc=info['tipo_doc'],
                             empresa_numero=info['empresa_numero'],
                             motivo=f'identidade: {err_ident}')
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

                    # ------------------------------------------------------
                    # NUNCA DUPLICAR: procura o MESMO DOCUMENTO já arquivado.
                    #
                    # A busca é pela IDENTIDADE lida do conteúdo, não pelo
                    # nome. Isto pega o furo que não aparecia como conflito
                    # nenhum: a mesma nota chegando com nome diferente era
                    # arquivada duas vezes, calada.
                    # ------------------------------------------------------
                    if ident in idents_vistos:
                        n['CONFLITO'] += 1
                        _log(rodada, origem, None, 'CONFLITO',
                             tipo_doc=info['tipo_doc'],
                             empresa_numero=info['empresa_numero'],
                             motivo='documento repetido na MESMA rodada — '
                                    f'ficou na origem [import={imp}]')
                        continue

                    existente = None          # (caminho, bytes) do já arquivado
                    ilegivel = None
                    for cand in indice_destino(svc, destino_dir, idx_cache).get(chave, []):
                        b_cand = svc.download_file(cand)
                        r_cand = parse_xml_bytes(b_cand) if b_cand else None
                        if r_cand is None:
                            # NUNCA decidir por ausência de informação: destino
                            # que não se consegue ler vira CONFLITO, não
                            # sobrescrita.
                            ilegivel = cand
                            break
                        id_cand, _ch, err_cand = identidade(r_cand)
                        if err_cand or id_cand is None:
                            ilegivel = cand
                            break
                        if id_cand == ident:
                            existente = (cand, b_cand)
                            break

                    if ilegivel:
                        n['CONFLITO'] += 1
                        logger.warning('[roteador] destino ILEGIVEL (%s) — nao '
                                       'sobrescrevo; %s fica na origem.', ilegivel, chave)
                        _log(rodada, origem, ilegivel, 'CONFLITO',
                             tipo_doc=info['tipo_doc'],
                             empresa_numero=info['empresa_numero'],
                             motivo=f'destino ilegivel — nao sobrescrito [import={imp}]')
                        continue

                    # Colisão de NOME sem ser o mesmo documento: o caminho de
                    # destino está ocupado por OUTRA nota. Sobrescrever aqui
                    # apagaria documento bom — é o caso que mantém o CONFLITO.
                    if existente is None and (destino.lower() in vistos
                                              or svc._path_exists(destino)):
                        n['CONFLITO'] += 1
                        _log(rodada, origem, destino, 'CONFLITO',
                             tipo_doc=info['tipo_doc'],
                             empresa_numero=info['empresa_numero'],
                             motivo='nome ja usado por OUTRO documento — '
                                    f'ficou na origem [import={imp}]')
                        continue

                    if DRYRUN:
                        # SIMULAÇÃO NÃO DEIXA RASTRO NO DROPBOX. Daqui não sai
                        # move, rename nem ensure_folder — nem a pasta de
                        # quarentena é criada. E NADA em logs_sistema: auditoria
                        # registra o que ACONTECEU, não o que aconteceria.
                        #
                        # O que se faz é só decidir e ANOTAR a decisão, para o
                        # placar responder "quantos seriam substituídos" ANTES
                        # de qualquer arquivo se mexer.
                        n['SIMULADO'] += 1
                        if existente:
                            alvo_sim, b_atual = existente
                            q_novo, q_atual = qualidade(dados), qualidade(b_atual)
                            if q_novo > q_atual:
                                sim['substituiria'] += 1
                                motivo_sim = ('simulado: substituiria — criterio '
                                              + rotulo_criterio(q_novo, q_atual))
                            elif (hashlib.sha256(dados).hexdigest()
                                  == hashlib.sha256(b_atual).hexdigest()):
                                sim['substituiria'] += 1
                                motivo_sim = 'simulado: substituiria — copia identica'
                            else:
                                sim['descartaria'] += 1
                                motivo_sim = ('simulado: descartaria — '
                                              'ja ha copia melhor')
                            destino_sim = alvo_sim
                        else:
                            sim['novos'] += 1
                            motivo_sim = ('simulado: arquivaria novo — '
                                          + info['motivo'])
                            destino_sim = destino
                        _log(rodada, origem, destino_sim, 'SIMULADO',
                             tipo_doc=info['tipo_doc'],
                             empresa_numero=info['empresa_numero'],
                             motivo=motivo_sim)
                        vistos.add(destino.lower())
                        idents_vistos.add(ident)
                        continue

                    # ------------------------------------------------------
                    # CAMINHO NOVO: já existe o mesmo documento. Fica UM.
                    # ------------------------------------------------------
                    if existente:
                        alvo, b_atual = existente
                        q_novo, q_atual = qualidade(dados), qualidade(b_atual)
                        sha_novo = hashlib.sha256(dados).hexdigest()
                        sha_atual = hashlib.sha256(b_atual).hexdigest()

                        if q_novo > q_atual:
                            # O novo é melhor: sobrescreve NO CAMINHO DO QUE JÁ
                            # ESTÁ (não no nome novo) — senão sobrariam dois.
                            if not svc.move_file(origem, alvo):
                                n['ERRO'] += 1
                                _log(rodada, origem, alvo, 'ERRO',
                                     motivo='move_file retornou False na substituicao')
                                continue
                            criterio = rotulo_criterio(q_novo, q_atual)
                            n['SUBSTITUIDO'] += 1
                            n_imp[imp] = n_imp.get(imp, 0) + 1
                            vistos.add(alvo.lower())
                            idents_vistos.add(ident)
                            _auditar_substituicao(chave, criterio, b_atual, dados,
                                                  sha_atual, sha_novo,
                                                  info['empresa_numero'])
                            _log(rodada, origem, alvo, 'SUBSTITUIDO',
                                 tipo_doc=info['tipo_doc'],
                                 empresa_numero=info['empresa_numero'],
                                 motivo=f'SUBSTITUIDO conteudo diferente '
                                        f'({criterio}) [import={imp}]')
                            continue

                        if sha_novo == sha_atual:
                            # Byte-idênticos: sobrescrever é inócuo e esvazia a
                            # _ENTRADA sem apagar nada. Nada mudou de fato →
                            # sem auditoria.
                            if not svc.move_file(origem, alvo):
                                n['ERRO'] += 1
                                _log(rodada, origem, alvo, 'ERRO',
                                     motivo='move_file retornou False na copia identica')
                                continue
                            n['SUBSTITUIDO'] += 1
                            n_imp[imp] = n_imp.get(imp, 0) + 1
                            vistos.add(alvo.lower())
                            idents_vistos.add(ident)
                            _log(rodada, origem, alvo, 'SUBSTITUIDO',
                                 tipo_doc=info['tipo_doc'],
                                 empresa_numero=info['empresa_numero'],
                                 motivo=f'copia identica [import={imp}]')
                            continue

                        # O que já está é melhor (ou empata sem ser idêntico):
                        # fica ele. O novo tem de sair da _ENTRADA, mas NÃO se
                        # apaga nada — o dropbox_sync nem tem delete. Vai para
                        # _DESCARTADOS, de onde dá para conferir e recuperar.
                        quarentena_dir = svc._build_path(
                            '_DESCARTADOS', info['ano'], info['mes'])
                        svc.ensure_folder(quarentena_dir)
                        alvo_q = f'{quarentena_dir}/{nome}'
                        if svc._path_exists(alvo_q):
                            n['CONFLITO'] += 1
                            _log(rodada, origem, alvo_q, 'CONFLITO',
                                 tipo_doc=info['tipo_doc'],
                                 empresa_numero=info['empresa_numero'],
                                 motivo='quarentena ja ocupada — ficou na origem')
                            continue
                        if not svc.move_file(origem, alvo_q):
                            n['ERRO'] += 1
                            _log(rodada, origem, alvo_q, 'ERRO',
                                 motivo='move_file retornou False na quarentena')
                            continue
                        n['DESCARTADO'] += 1
                        n_imp[imp] = n_imp.get(imp, 0) + 1
                        idents_vistos.add(ident)
                        _log(rodada, origem, alvo_q, 'DESCARTADO',
                             tipo_doc=info['tipo_doc'],
                             empresa_numero=info['empresa_numero'],
                             motivo=f'ja havia copia melhor/igual no destino '
                                    f'[import={imp}]')
                        continue

                    svc.ensure_folder(destino_dir)
                    if svc.move_file(origem, destino):
                        n['MOVIDO'] += 1
                        n_imp[imp] = n_imp.get(imp, 0) + 1
                        vistos.add(destino.lower())
                        idents_vistos.add(ident)
                        # O índice da rodada tem de aprender o que acabou de
                        # entrar — senão dois arquivos do MESMO documento no
                        # mesmo lote seriam ambos arquivados.
                        idx_cache.get(destino_dir, {}).setdefault(
                            chave, []).append(destino)
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

        logger.warning('[roteador] rodada %s: %d avaliados | movidos=%d '
                       'substituidos=%d descartados=%d simulados=%d '
                       'conflitos=%d revisar=%d sem_match=%d erros=%d | '
                       'ignorados_nao_xml=%d | lancamento=%s',
                       rodada, total, n['MOVIDO'], n['SUBSTITUIDO'],
                       n['DESCARTADO'], n['SIMULADO'], n['CONFLITO'],
                       n['REVISAR'], n['SEM_MATCH'], n['ERRO'], n['IGNORADO'],
                       dict(sorted(n_imp.items())) or '{}')
        if DRYRUN:
            # A linha que responde "o que aconteceria" sem nada ter acontecido.
            logger.warning('[roteador] rodada %s SIMULADA: simulados=%d '
                           '(substituiria=%d, descartaria=%d, conflitos=%d, '
                           'revisar=%d, novos=%d)',
                           rodada, n['SIMULADO'], sim['substituiria'],
                           sim['descartaria'], n['CONFLITO'], n['REVISAR'],
                           sim['novos'])
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
