# -*- coding: utf-8 -*-
"""Cliente HTTP do ADN — certificado, mTLS, retry e desempacotamento.

É o "crachá e a chave do prédio" do módulo: quem fala com o servidor do
governo. Não conhece banco, não conhece campo fiscal, não decide nada de
negócio. Quem lê campo é o ``parser``; quem grava é o ``repositorio``.

POR QUE ELE É FINO
------------------
A especificação descrevia um ``client.py`` a ser construído do zero. Metade
dele já existia e roda em produção há meses, na captura de NF-e/CT-e:

  * ``utils.certificado_digital``     — abre o .pfx, decifra a senha (Fernet),
                                        devolve (chave, cert, cadeia);
  * ``dfe_sefaz.montar_sessao_mtls``  — sessão ``requests`` com mTLS a partir
                                        desses objetos, com o certificado em
                                        MEMÓRIA (nada de .pem temporário no
                                        disco de um servidor compartilhado).

O certificado é o mesmo e a exigência de autenticação mútua é a mesma. O que
muda é só a camada de cima: a SEFAZ fala SOAP/XML, o ADN fala REST/JSON.
Reescrever o handshake aqui seria criar uma segunda implementação da parte
mais difícil de acertar — e a que ninguém iria lembrar de corrigir duas vezes.

A REGRA DO CERTIFICADO É OUTRA, E ESSA SIM É NOVA
-------------------------------------------------
No DFe, o fallback é para o certificado do CONTADOR vinculado. Aqui não: o
manual do ADN diz que a consulta pode usar certificado cuja **raiz de CNPJ**
(os 8 primeiros dígitos) coincida com a do contribuinte consultado, e que há um
parâmetro para consultar CNPJ diferente do certificado. Isso é o que torna a
carteira viável — 46 empresas com certificado válido, várias delas filiais da
mesma raiz. Exigir um e-CNPJ por estabelecimento seria inviável na prática.

O QUE VEM DA API
----------------
Toda resposta é um ``LoteDistribuicaoNSUResponse``:

    StatusProcessamento : DOCUMENTOS_LOCALIZADOS | NENHUM_DOCUMENTO_LOCALIZADO
                          | REJEICAO
    LoteDFe[]           : NSU, ChaveAcesso, TipoDocumento, TipoEvento,
                          ArquivoXml, DataHoraGeracao
    Alertas[] · Erros[] · TipoAmbiente · VersaoAplicativo · DataHoraProcessamento

``ArquivoXml`` vem **GZip + base64**: o JSON é envelope, o documento fiscal
inteiro está lá dentro. Nenhum campo fiscal aparece no JSON.

PARADA DO LOOP: por ``NENHUM_DOCUMENTO_LOCALIZADO``, nunca por lista vazia.
São coisas diferentes — ``REJEICAO`` também traz lista vazia e é ERRO, não fim
da fila. Confundir os dois faz o cursor parar cedo e em silêncio.
"""
import base64
import gzip
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

import requests

from models.dfe_certificado import DfeCertificado
from utils import dropbox_sync
from utils.certificado_digital import (CertificadoError, carregar_par_chave_cert,
                                       decifrar_senha)
from utils.db_helper import execute_query
from utils.integrations.dfe_sefaz import montar_sessao_mtls

logger = logging.getLogger(__name__)


class Ambiente(Enum):
    """Produção restrita é onde a Fase 0 roda. Só migrar depois do checklist."""
    PRODUCAO_RESTRITA = 'https://adn.producaorestrita.nfse.gov.br/contribuintes'
    PRODUCAO = 'https://adn.nfse.gov.br/contribuintes'

    @property
    def carimbo(self):
        """Como o ADN nomeia este ambiente no campo ``TipoAmbiente``."""
        return 'PRODUCAO' if self is Ambiente.PRODUCAO else 'HOMOLOGACAO'


class ADNError(Exception):
    """Falha de transporte ou resposta inesperada do ADN."""


class ADNAuthError(ADNError):
    """401/403 — certificado sem autorização para o CNPJ consultado.

    Separado de propósito: não se repete uma consulta recusada por autorização.
    Repetir não muda o resultado e só queima o ciclo das outras empresas.
    """


class SemCertificado(ADNError):
    """Nenhum certificado utilizável para o CNPJ — nem próprio, nem da raiz.

    CAPTURE ESTA ANTES DE ``ADNError``. Herda dela por conveniência, mas a
    reação é oposta: falta de certificado é problema de CADASTRO — desativa a
    empresa e avisa —, enquanto ADNError é transporte, que se tenta de novo no
    ciclo seguinte. Um ``except ADNError`` genérico à frente trataria certificado
    ausente como instabilidade de rede e ficaria repetindo para sempre.
    """


class AmbienteDivergente(ADNError):
    """A resposta veio de ambiente diferente do que se pediu.

    Guarda contra o pior acidente possível aqui: rodar a aferição contra
    PRODUÇÃO achando que é produção restrita. O ADN carimba ``TipoAmbiente`` em
    toda resposta; conferir custa uma comparação e evita descobrir depois.
    """


class RejeicaoADN(ADNError):
    """StatusProcessamento = REJEICAO. NÃO é fim de fila; o cursor NÃO avança."""


# Status que o ADN devolve. Nomes fechados; qualquer outro é tratado como
# desconhecido e vira erro, em vez de ser silenciosamente lido como "acabou".
STATUS_COM_DOCS = 'DOCUMENTOS_LOCALIZADOS'
STATUS_VAZIO = 'NENHUM_DOCUMENTO_LOCALIZADO'
STATUS_REJEICAO = 'REJEICAO'

TIMEOUT = (10, 60)          # (conexão, leitura) — o lote pode ter 50 XMLs
TENTATIVAS = 3              # só para falha transitória; auth nunca repete
BACKOFF_BASE = 2            # 2s, 4s, 8s


@dataclass
class Certificado:
    """Certificado eleito para falar com o ADN em nome de um CNPJ."""
    cliente_id: int
    cnpj: str                       # do titular do certificado (14 dígitos)
    dropbox_path: str
    por_raiz: bool = False          # True quando veio de outra empresa da raiz
    dono_id: int = None             # de quem é o certificado, se != cliente_id

    @property
    def raiz(self):
        return (self.cnpj or '')[:8]


@dataclass
class LoteDFe:
    """Resposta crua de uma chamada. ``documentos`` são dicts como vieram."""
    status: str
    documentos: list = field(default_factory=list)
    alertas: list = field(default_factory=list)
    erros: list = field(default_factory=list)
    ambiente: str = None
    versao_aplicativo: str = None

    @property
    def vazio(self):
        """Fim da fila — e SÓ isso. Rejeição não passa por aqui."""
        return self.status == STATUS_VAZIO

    @property
    def ultimo_nsu(self):
        """Maior NSU do lote, ou None. Não assume que a API devolve ordenado.

        SÓ PARA LOG E PROGRESSO. **Não avance o cursor com este número.** A
        regra de ouro do módulo é que o NSU avança DOCUMENTO A DOCUMENTO, depois
        de gravação confirmada; usar o maior do lote adiantaria o cursor por
        cima de documentos que ainda não entraram, e uma queda no meio faria o
        buraco passar despercebido — que é justamente o que a regra evita.

        Existe porque quem orquestra precisa dizer "processando de 1200 a 1250".
        Sem ele, a próxima pessoa pegaria ``documentos[-1]['NSU']`` e assumiria
        que a API devolve ordenado.
        """
        nsus = [d.get('NSU') for d in self.documentos if d.get('NSU') is not None]
        return max(nsus) if nsus else None


def _so_digitos(v):
    return ''.join(c for c in str(v or '') if c.isdigit())


def resolver_certificado(cliente_id: int) -> Certificado:
    """Elege o certificado para consultar este cliente. Levanta SemCertificado.

    Ordem, e o porquê de cada passo:
      1. certificado PRÓPRIO do cliente — sempre preferido, é o caso exato;
      2. certificado de QUALQUER empresa ativa com a MESMA RAIZ de CNPJ — o
         manual do ADN autoriza, e é o que evita exigir um e-CNPJ por filial.

    NÃO cai para certificado de contador (regra do DFe). Aqui o critério é raiz
    de CNPJ, que o ADN valida do lado dele: mandar um certificado de raiz
    diferente seria recusado com 401, gastando uma chamada para nada.

    OS DOIS PASSOS USAM O MESMO FILTRO — ``ativo=1`` e dentro da validade. A
    primeira versão filtrava só no passo 2, e o efeito era perverso: empresa com
    certificado PRÓPRIO vencido devolvia o vencido e nunca chegava ao passo 2,
    falhando no mTLS com um certificado válido da raiz disponível ao lado.
    Filtro assimétrico só aparece no dia em que algo vence — e aí parece
    problema do ADN.
    """
    proprio = execute_query(
        """SELECT cliente_id, cnpj, dropbox_path
             FROM dfe_certificados
            WHERE cliente_id = %s AND ativo = 1
              AND dropbox_path IS NOT NULL AND validade >= CURDATE()
            LIMIT 1""",
        (cliente_id,), fetch=True, fetch_one=True)
    if proprio:
        return Certificado(cliente_id=cliente_id,
                           cnpj=_so_digitos(proprio.get('cnpj')),
                           dropbox_path=proprio['dropbox_path'],
                           dono_id=cliente_id)

    alvo = execute_query(
        'SELECT cpf_cnpj FROM clientes WHERE id = %s',
        (cliente_id,), fetch=True, fetch_one=True) or {}
    raiz = _so_digitos(alvo.get('cpf_cnpj'))[:8]
    if len(raiz) < 8:
        raise SemCertificado(
            f'Cliente {cliente_id} sem CNPJ utilizável para buscar certificado da raiz.')

    # LPAD porque o CNPJ do cadastro pode vir com máscara ou sem zero à esquerda,
    # enquanto o do certificado é sempre 14 dígitos crus.
    linha = execute_query(
        """SELECT ce.cliente_id, ce.cnpj, ce.dropbox_path
             FROM dfe_certificados ce
             JOIN clientes c ON c.id = ce.cliente_id
            WHERE ce.ativo = 1
              AND ce.dropbox_path IS NOT NULL
              AND ce.validade >= CURDATE()
              AND c.situacao = 'ATIVO'
              AND LEFT(LPAD(REGEXP_REPLACE(ce.cnpj, '[^0-9]', ''), 14, '0'), 8) = %s
         ORDER BY ce.validade DESC
            LIMIT 1""",
        (raiz,), fetch=True, fetch_one=True)
    if not linha:
        raise SemCertificado(
            f'Cliente {cliente_id}: sem certificado próprio e nenhum da raiz {raiz}.')

    logger.info('[nfse-adn] cliente %s usará o certificado da raiz %s '
                '(titular: cliente %s).', cliente_id, raiz, linha['cliente_id'])
    return Certificado(cliente_id=cliente_id, cnpj=_so_digitos(linha['cnpj']),
                       dropbox_path=linha['dropbox_path'], por_raiz=True,
                       dono_id=linha['cliente_id'])


def abrir_sessao(cert: Certificado):
    """Sessão ``requests`` com mTLS pronta para o ADN.

    Reusa exatamente o caminho da captura de DFe: baixa o .pfx do Dropbox,
    decifra a senha guardada e monta a sessão com o certificado em memória.
    """
    senha_cif = DfeCertificado.get_senha_cifrada(cert.dono_id or cert.cliente_id)
    if not senha_cif:
        raise SemCertificado(
            f'Certificado do cliente {cert.dono_id} sem senha armazenada.')
    try:
        senha = decifrar_senha(senha_cif)
    except CertificadoError as exc:
        raise SemCertificado(f'Falha ao decifrar a senha do certificado: {exc}') from exc

    pfx = dropbox_sync._service.download_file(cert.dropbox_path)
    if not pfx:
        raise SemCertificado(f'Falha ao baixar o .pfx de {cert.dropbox_path}.')

    try:
        chave_priv, x509, cadeia = carregar_par_chave_cert(pfx, senha)
    except CertificadoError as exc:
        raise SemCertificado(f'Falha ao abrir o certificado: {exc}') from exc

    return montar_sessao_mtls(x509, chave_priv, cadeia)


def _get(sessao, url, params, tentativa_de=TENTATIVAS):
    """GET com retry só para falha TRANSITÓRIA. 401/403 não repete.

    Repetir uma recusa de autorização não muda o resultado — e, com dezenas de
    empresas por ciclo, três tentativas inúteis por empresa consomem o deadline
    que deveria ser das outras.
    """
    ultimo = None
    for n in range(tentativa_de):
        try:
            r = sessao.get(url, params=params, timeout=TIMEOUT,
                           headers={'Accept': 'application/json'})
        except requests.RequestException as exc:
            ultimo = exc
            logger.warning('[nfse-adn] falha de rede em %s (tentativa %d/%d): %s',
                           url, n + 1, tentativa_de, exc)
        else:
            if r.status_code in (401, 403):
                raise ADNAuthError(
                    f'ADN recusou o certificado para esta consulta (HTTP {r.status_code}). '
                    f'Provável: o certificado não tem a mesma raiz de CNPJ do consultado.')
            if r.status_code == 404:
                # 404 é resposta de negócio no ADN (devolve o mesmo schema),
                # não erro de transporte — quem interpreta é o chamador.
                return r
            if r.status_code < 500:
                return r
            ultimo = ADNError(f'ADN HTTP {r.status_code}')
            logger.warning('[nfse-adn] %s em %s (tentativa %d/%d)',
                           r.status_code, url, n + 1, tentativa_de)
        if n < tentativa_de - 1:
            time.sleep(BACKOFF_BASE ** (n + 1))
    raise ADNError(f'ADN indisponível após {tentativa_de} tentativas: {ultimo}')


def _para_lote(resposta, ambiente: 'Ambiente' = None) -> LoteDFe:
    """Traduz o JSON para LoteDFe. Levanta em REJEICAO e em status desconhecido.

    Confere também o ``TipoAmbiente`` contra o ambiente pedido — ver
    ``AmbienteDivergente``.
    """
    try:
        d = resposta.json() or {}
    except ValueError as exc:
        raise ADNError(f'ADN devolveu resposta que não é JSON: {exc}') from exc

    status = d.get('StatusProcessamento')
    lote = LoteDFe(
        status=status,
        documentos=d.get('LoteDFe') or [],
        alertas=d.get('Alertas') or [],
        erros=d.get('Erros') or [],
        ambiente=d.get('TipoAmbiente'),
        versao_aplicativo=d.get('VersaoAplicativo'),
    )
    if status == STATUS_REJEICAO:
        motivos = '; '.join(
            f"{e.get('Codigo')}: {e.get('Descricao')}" for e in lote.erros) or '(sem detalhe)'
        raise RejeicaoADN(f'ADN rejeitou a consulta — {motivos}')
    if status not in (STATUS_COM_DOCS, STATUS_VAZIO):
        raise ADNError(f'StatusProcessamento desconhecido: {status!r}')

    # Confere DEPOIS do status: uma rejeição já traz a causa, e o ambiente da
    # resposta a um pedido recusado não é informação confiável.
    if ambiente is not None and lote.ambiente and lote.ambiente != ambiente.carimbo:
        raise AmbienteDivergente(
            f'Pedi {ambiente.name} ({ambiente.carimbo}) e a resposta veio de '
            f'{lote.ambiente}. Conferir a URL antes de gravar qualquer coisa.')

    if lote.alertas:
        logger.info('[nfse-adn] alertas na resposta: %s',
                    [a.get('Descricao') for a in lote.alertas])
    return lote


def buscar_lote(sessao, nsu: int, ambiente: Ambiente = Ambiente.PRODUCAO_RESTRITA,
                cnpj_consulta: str = None) -> LoteDFe:
    """``GET /DFe/{NSU}`` — os documentos a partir do NSU informado.

    ``lote=true`` (default da API) traz o conjunto, não um documento por
    chamada. ``cnpj_consulta`` permite consultar CNPJ diferente do certificado,
    desde que a raiz coincida — é o que faz uma matriz servir às filiais.
    """
    params = {'lote': 'true'}
    if cnpj_consulta:
        params['cnpjConsulta'] = _so_digitos(cnpj_consulta)
    r = _get(sessao, f'{ambiente.value}/DFe/{int(nsu)}', params)
    return _para_lote(r, ambiente)


def buscar_eventos(sessao, chave_acesso: str,
                   ambiente: Ambiente = Ambiente.PRODUCAO_RESTRITA) -> LoteDFe:
    """``GET /NFSe/{ChaveAcesso}/Eventos`` — eventos vinculados a uma NFS-e.

    Mesmo schema de resposta da distribuição. Complementa o cursor: o evento
    também chega por NSU, mas por aqui dá para conferir uma nota específica.
    """
    chave = _so_digitos(chave_acesso)
    r = _get(sessao, f'{ambiente.value}/NFSe/{chave}/Eventos', None)
    return _para_lote(r, ambiente)


def desempacotar_xml(arquivo_xml: str) -> str:
    """``ArquivoXml`` (GZip + base64) -> texto XML.

    Levanta ADNError com a causa separada — base64 e gzip falham por motivos
    diferentes e o tratamento na quarentena é o mesmo, mas o diagnóstico não.
    """
    if not arquivo_xml:
        raise ADNError('ArquivoXml vazio.')
    try:
        comprimido = base64.b64decode(arquivo_xml)
    except Exception as exc:
        raise ADNError(f'Falha na decodificação base64 do ArquivoXml: {exc}') from exc
    try:
        bruto = gzip.decompress(comprimido)
    except Exception as exc:
        raise ADNError(f'Estrutura descompactada mal formada (gzip): {exc}') from exc
    try:
        return bruto.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ADNError(f'XML não está utilizando codificação UTF-8: {exc}') from exc
