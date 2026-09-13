# -*- coding: utf-8 -*-
"""De onde vem o XML de uma NF-e: banco quando há, senão Dropbox (12/09/2026).

ETAPA 1 DA REGRA "DOCUMENTO NO DROPBOX, DADO NO BANCO"
------------------------------------------------------
Decisão do Anderson: até 3 meses pela EMISSÃO o XML fica no banco e no
Dropbox; do 4º mês em diante, só no Dropbox. Antes de qualquer expurgo, quem
LÊ o XML precisa saber buscá-lo no Dropbox — senão o olhinho de nota antiga
abriria vazio. Este módulo é esse "saber buscar", e é pré-requisito de tudo.

Os leitores da NF-e (olhinho/PDF, baixar XML, zip e PDF em lote) liam
``nfe_importacoes.xml_raw`` direto. O CT-e já tinha o fallback para o arquivo
(``_carregar_cte_export``); aqui é o mesmo desenho, com UMA diferença que a
medição impôs.

POR QUE NÃO BASTA O ``xml_caminho``
-----------------------------------
Medido em 12/09/2026: ``xml_caminho`` está NULL em toda linha antiga — SEFAZ,
Q-Robô, Q-Colabore, drop na _ENTRADA —, só o arquivador novo passou a gravá-lo.
Cada escritor subiu o arquivo e não anotou onde. Então o caminho tem de ser
DERIVADO da convenção, que todos os escritores compartilham:

    EMPRESAS/{numero - razão}/FISCAL/{ENTRADAS|SAIDAS}/{ano}/{MM}/{nome_arquivo}

e ``nome_arquivo`` é o nome EXATO do arquivo no Dropbox (medido nas quatro
origens): ``{chave}.xml`` na captura SEFAZ e no Q-Robô; o nome original
(``Nfe_3526..._114.xml``) no que passou pelo roteador. Sempre termina em .xml.

A pasta sai de ``pasta_fiscal()`` — a MESMA função que os escritores usam —
e nunca é montada à mão aqui: se a convenção mudar, muda num lugar só.

TRÊS RESPOSTAS DIFERENTES, e a diferença importa para quem está na tela
-----------------------------------------------------------------------
* **(xml, 'banco'/'dropbox')** — achou;
* **404 "não está mais no Dropbox"** — o arquivo sumiu (pasta apagada, movida).
  A nota continua na lista; o documento, não. É o caso "empresa que saiu e
  alguém apagou a pasta" — por isso a regra é DESATIVAR, nunca apagar;
* **502 "não foi possível ler agora"** — Dropbox fora ou credencial: passageiro,
  tente de novo. Confundir os dois faria alguém procurar um arquivo que existe.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

#: Downloads em paralelo nos lotes. Cada um é ~0,4s de espera de REDE, não de
#: CPU: em série, 50 notas antigas estourariam o tempo da requisição.
PARALELO = 8

_SENTIDO = {'entrada': 'ENTRADAS', 'saida': 'SAIDAS'}

MSG_SEM_XML = ('Esta nota não tem o XML guardado — nada a exportar.', 404)
MSG_SUMIU = ('O XML desta nota não está mais no Dropbox '
             '(o arquivo pode ter sido movido ou removido).', 404)
MSG_DROPBOX_FORA = ('Não foi possível ler o XML no Dropbox agora. '
                    'Tente de novo em instantes.', 502)

#: Colunas que o resolvedor precisa na linha. Quem monta o SELECT inclui estas
#: (com JOIN em clientes para numero_cliente e nome_razao_social).
COLUNAS = ('n.id, n.xml_raw, n.xml_caminho, n.nome_arquivo, n.chave_acesso, '
           'n.tipo, n.data_emissao, c.numero_cliente, c.nome_razao_social')
JOIN_CLIENTE = 'LEFT JOIN clientes c ON c.id = n.cliente_id'


def candidatos(nota):
    """Os caminhos onde o XML PODE estar, do mais provável ao menos.

    1. ``xml_caminho`` gravado (só o arquivador novo grava) — certeza;
    2. pasta pela convenção + ``nome_arquivo`` — o nome exato do arquivo;
    3. pasta pela convenção + ``{chave}.xml`` — reserva, para linha sem nome.
    Sem empresa ou sem data não há como derivar pasta: devolve só o (1).
    """
    out = []
    cam = (nota.get('xml_caminho') or '').strip()
    if cam:
        out.append(cam)

    dt = nota.get('data_emissao')
    sentido = _SENTIDO.get((nota.get('tipo') or '').lower())
    razao = nota.get('nome_razao_social')
    if not (dt and sentido and razao):
        return out
    try:
        from utils import dropbox_sync
        pasta = dropbox_sync._service.pasta_fiscal(
            razao, dt.year, dt.month, sentido,
            (str(nota.get('numero_cliente') or '').strip() or None))
    except Exception:
        logger.exception('[xml-fonte] falha ao derivar a pasta da nota id=%s',
                         nota.get('id'))
        return out

    nome = (nota.get('nome_arquivo') or '').strip()
    if nome:
        out.append(f'{pasta}/{nome}')
    chave = (nota.get('chave_acesso') or '').strip()
    if chave and nome != f'{chave}.xml':
        out.append(f'{pasta}/{chave}.xml')
    return out


def resolver(nota):
    """(xml, fonte, None) ou (None, None, (mensagem, status)) para UMA nota."""
    xml = (nota.get('xml_raw') or '').strip()
    if xml:
        return xml, 'banco', None

    from utils import dropbox_sync
    from utils.dropbox_sync import DropboxAuthError, DropboxError

    tentados = candidatos(nota)
    if not tentados:
        return None, None, MSG_SEM_XML
    for caminho in tentados:
        try:
            xml = dropbox_sync.download_xml(caminho)
        except (DropboxAuthError, DropboxError) as exc:
            logger.warning('[xml-fonte] Dropbox indisponível ao ler nota id=%s '
                           '(%s): %s', nota.get('id'), caminho, exc)
            return None, None, MSG_DROPBOX_FORA
        if xml and xml.strip():
            return xml, 'dropbox', None
    logger.info('[xml-fonte] XML da nota id=%s não achado em %s caminho(s).',
                nota.get('id'), len(tentados))
    return None, None, MSG_SUMIU


def resolver_lote(rows):
    """{id: xml} para várias notas, buscando no Dropbox EM PARALELO o que falta.

    Quem falhar fica de fora em vez de derrubar o lote inteiro — o mesmo
    comportamento do ``_xmls_cte`` que já existia.
    """
    prontos = {r['id']: (r.get('xml_raw') or '').strip()
               for r in rows if (r.get('xml_raw') or '').strip()}
    pendentes = [r for r in rows if r['id'] not in prontos]
    if not pendentes:
        return prontos

    def _um(r):
        xml, _fonte, _erro = resolver(r)
        return r['id'], xml

    with ThreadPoolExecutor(max_workers=PARALELO) as pool:
        for fut in as_completed([pool.submit(_um, r) for r in pendentes]):
            try:
                _id, xml = fut.result()
            except Exception:
                logger.exception('[xml-fonte] falha inesperada no lote')
                continue
            if xml:
                prontos[_id] = xml
    return prontos
