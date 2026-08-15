# -*- coding: utf-8 -*-
"""Orquestração da captura de NFS-e — quem percorre as empresas e move o cursor.

Amarra as três peças: o ``client`` fala com o ADN, o ``parser`` lê os campos, o
``repositorio`` grava. Aqui mora só o laço e as regras de quando parar.

A REGRA DE OURO
---------------
    O NSU só avança APÓS gravação confirmada no banco.

Documento a documento, nunca por lote. Se cair no meio, a próxima execução
retoma exatamente de onde parou — sem perder documento e sem reprocessar o que
já entrou. É por isso que ``LoteDFe.ultimo_nsu`` existe só para log: avançar por
lote adiantaria o cursor por cima de documentos que ainda não foram gravados, e
uma queda no meio deixaria um buraco que ninguém veria.

PAUSA ENTRE LOTES — medida, não chutada
---------------------------------------
O ADN devolve **429** depois de poucas chamadas seguidas. Descoberto em
14/08/2026, na primeira consulta real: com 6 segundos entre lotes, os três CNPJs
da Fase 0 percorreram o cursor inteiro sem uma única recusa. Sem pausa, o
segundo lote já era recusado.

ISOLAMENTO POR EMPRESA
----------------------
Exceção numa empresa NUNCA interrompe as demais — mesmo princípio do
``dfe_captura``. Certificado vencido, CNPJ sem autorização, XML corrompido: cada
um vira registro e a fila continua. O que derruba a rodada inteira é só falha de
banco, e aí é certo derrubar.

Spec: docs/NFSE_ADN_ESPECIFICACAO.md
"""
import argparse
import logging
import time
from datetime import datetime

from utils.db_helper import execute_query
from utils.integrations.nfse_adn import client, parser, repositorio

logger = logging.getLogger(__name__)

# Medido em 14/08/2026: com 6s entre lotes, zero 429 nos três CNPJs da Fase 0.
PAUSA_ENTRE_LOTES_S = 6

# Teto por execução de backfill. ~50 documentos por lote, então 200 lotes são
# ~10.000 documentos — o suficiente para a maioria dos históricos numa rodada, e
# baixo o bastante para não virar execução infinita.
LOTES_MAX_BACKFILL = 200

# Mesmo padrão do DFe. Com a pausa de 6s, é o que limita quantas empresas cabem
# num ciclo — por isso o incremental para de PEGAR empresa nova quando o tempo
# acaba, mas termina a que já começou.
DEADLINE_PADRAO_S = 960


def _log(empresa_id, cnpj, modo, **kw):
    """Uma linha em nfse_consulta_log. Best-effort: nunca derruba a captura."""
    try:
        execute_query(
            "INSERT INTO nfse_consulta_log "
            "(empresa_id, cnpj, modo, nsu_inicial, nsu_final, qtd_docs, qtd_salvos, "
            " qtd_duplicados, http_status, duracao_ms, erro) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (empresa_id, cnpj, modo, kw.get('nsu_inicial'), kw.get('nsu_final'),
             kw.get('qtd_docs', 0), kw.get('qtd_salvos', 0),
             kw.get('qtd_duplicados', 0), kw.get('http_status'),
             kw.get('duracao_ms'), kw.get('erro')), fetch=False)
    except Exception:
        logger.warning('[nfse-captura] falha ao gravar log', exc_info=True)


def _cursor(empresa_id, cnpj):
    """Linha do cursor, criando-a se for a primeira vez desta empresa."""
    r = execute_query(
        'SELECT ult_nsu, modo, ativo FROM dfe_nsu_nfse WHERE cnpj = %s',
        (cnpj,), fetch=True, fetch_one=True)
    if r:
        return r
    execute_query(
        'INSERT INTO dfe_nsu_nfse (empresa_id, cnpj, ult_nsu, modo) '
        'VALUES (%s, %s, 0, %s) ON DUPLICATE KEY UPDATE empresa_id=VALUES(empresa_id)',
        (empresa_id, cnpj, 'backfill'), fetch=False)
    return {'ult_nsu': 0, 'modo': 'backfill', 'ativo': 1}


def _avancar_cursor(cnpj, nsu):
    """Move o cursor. Chamado SÓ depois de o documento estar gravado."""
    execute_query(
        'UPDATE dfe_nsu_nfse SET ult_nsu = %s, ultima_exec = NOW(), '
        '       ultimo_sucesso = NOW(), tentativas_falha = 0, ultimo_erro = NULL '
        ' WHERE cnpj = %s AND ult_nsu < %s',
        (nsu, cnpj, nsu), fetch=False)


def _marcar_erro(cnpj, erro, desativar=False):
    execute_query(
        'UPDATE dfe_nsu_nfse SET ultima_exec = NOW(), '
        '       tentativas_falha = tentativas_falha + 1, ultimo_erro = %s'
        + (', ativo = 0' if desativar else '') + ' WHERE cnpj = %s',
        (str(erro)[:500], cnpj), fetch=False)


def _virar_incremental(cnpj):
    execute_query(
        "UPDATE dfe_nsu_nfse SET modo = 'incremental', ultima_exec = NOW(), "
        "       ultimo_sucesso = NOW() WHERE cnpj = %s", (cnpj,), fetch=False)


def _processar_documento(d, empresa_id, cnpj):
    """UM documento do lote. Devolve 'salvo' | 'quarentena' | 'evento'.

    Quarentena NÃO é erro que trava: documento malformado não pode parar a fila
    de ninguém. O ``raw_json`` é gravado de qualquer forma, então nada se perde
    — o que se perde seria a fila inteira se um XML ruim abortasse a rodada.
    """
    tipo = (d.get('TipoDocumento') or '').upper()

    if tipo == 'EVENTO':
        try:
            xml = client.desempacotar_xml(d.get('ArquivoXml'))
            reg = parser.evento_para_registro(xml, d, empresa_id, cnpj)
        except (client.ADNError, parser.XmlInvalido) as exc:
            logger.warning('[nfse-captura] evento NSU %s em quarentena: %s',
                           d.get('NSU'), exc)
            return 'quarentena'
        if reg.get('divergencia'):
            # Envelope e XML discordam sobre o tipo: não se escolhe um, manda
            # para revisão. Divergência aqui significa leiaute mudando.
            reg['revisar'] = 1
        reg['raw_json'] = {k: v for k, v in d.items() if k != 'ArquivoXml'}
        repositorio.gravar_evento_completo(reg)

        # Bloqueio/desbloqueio de ofício vão para o eixo próprio, não para
        # situacao — a nota sob restrição segue ATIVA e VÁLIDA.
        if reg.get('elemento') in ('e305102', 'e305103'):
            r = parser.restricao_do_evento(xml)
            if r.get('restrito') is not None:
                repositorio.aplicar_restricao(
                    reg['chave_referenciada'], r['restrito'], r.get('codigos'))
        return 'evento'

    try:
        xml = client.desempacotar_xml(d.get('ArquivoXml'))
        reg = parser.para_registro(xml, d, empresa_id, cnpj)
    except (client.ADNError, parser.XmlInvalido) as exc:
        logger.warning('[nfse-captura] documento NSU %s em quarentena: %s',
                       d.get('NSU'), exc)
        return 'quarentena'

    if not reg.get('papel'):
        # O CNPJ do cursor não é prestador, tomador nem intermediário. Não se
        # inventa papel: sem ele a linha seria uma mentira no lugar de um
        # registro incompleto.
        logger.warning('[nfse-captura] NSU %s sem papel para %s — quarentena',
                       d.get('NSU'), cnpj)
        return 'quarentena'

    reg['raw_json'] = {k: v for k, v in d.items() if k != 'ArquivoXml'}
    repositorio.gravar_documento_completo(reg)
    return 'salvo'


def capturar_empresa(empresa_id, cnpj, ambiente=client.Ambiente.PRODUCAO_RESTRITA,
                     lotes_max=LOTES_MAX_BACKFILL, deadline_seg=None,
                     modo='backfill') -> dict:
    """Percorre o cursor de UMA empresa. Nunca levanta — devolve o resultado.

    Para em: fila vazia (fim), teto de lotes, deadline, limite de taxa do ADN,
    ou falha de gravação. Nos quatro primeiros o cursor fica onde chegou e a
    próxima execução continua; no último ele NÃO avança, de propósito.
    """
    t0 = time.monotonic()
    cur = _cursor(empresa_id, cnpj)
    nsu_inicial = int(cur['ult_nsu'] or 0)
    nsu = nsu_inicial
    salvos = eventos = quarentena = lotes = 0

    try:
        cert = client.resolver_certificado(empresa_id)
        sessao = client.abrir_sessao(cert)
    except client.SemCertificado as exc:
        # Problema de CADASTRO, não de transporte: desativa e avisa. Insistir
        # todo ciclo só encheria o log — o certificado não vai aparecer sozinho.
        _marcar_erro(cnpj, exc, desativar=True)
        _log(empresa_id, cnpj, modo, nsu_inicial=nsu_inicial, erro=str(exc)[:500])
        return {'ok': False, 'erro': str(exc), 'sem_certificado': True}

    parada = 'fim da fila'
    erro = None
    try:
        while lotes < lotes_max:
            if deadline_seg and (time.monotonic() - t0) > deadline_seg:
                parada = 'deadline do ciclo'
                break
            lote = client.buscar_lote(
                sessao, nsu, ambiente=ambiente,
                cnpj_consulta=cnpj if cert.por_raiz else None)
            lotes += 1
            if lote.vazio:
                break

            nsu_antes = nsu
            for d in lote.documentos:
                r = _processar_documento(d, empresa_id, cnpj)
                if r == 'salvo':
                    salvos += 1
                elif r == 'evento':
                    eventos += 1
                else:
                    quarentena += 1
                # AQUI: o documento já está gravado e commitado. Só agora o
                # cursor anda — e anda por ESTE documento, não pelo lote.
                # Documento em quarentena também avança: malformado não pode
                # travar a fila, e o raw_json já ficou guardado.
                d_nsu = d.get('NSU')
                if d_nsu is not None and int(d_nsu) > nsu:
                    nsu = int(d_nsu)
                    _avancar_cursor(cnpj, nsu)

            if nsu <= nsu_antes:
                # Lote veio com documentos mas o cursor não saiu do lugar: ou
                # todos os NSU eram <= o atual, ou nenhum veio com NSU. Seguir
                # pediria o MESMO NSU para sempre. Para e deixa o erro visível
                # em vez de rodar em círculo gastando cota do ADN.
                parada = 'NSU não avançou'
                erro = (f'lote com {len(lote.documentos)} documento(s) não moveu '
                        f'o cursor de {nsu}')
                logger.error('[nfse-captura] %s — %s', cnpj, erro)
                break
            time.sleep(PAUSA_ENTRE_LOTES_S)
        else:
            parada = 'teto de lotes'
    except client.LimiteDeTaxa as exc:
        parada, erro = 'limite de taxa do ADN', str(exc)
        _marcar_erro(cnpj, exc)
    except client.ADNAuthError as exc:
        parada, erro = 'sem autorização', str(exc)
        _marcar_erro(cnpj, exc)
    except client.ADNError as exc:
        parada, erro = 'erro do ADN', str(exc)
        _marcar_erro(cnpj, exc)
    except Exception as exc:                                  # noqa: BLE001
        parada, erro = 'falha ao gravar', str(exc)
        logger.exception('[nfse-captura] falha em %s', cnpj)
        _marcar_erro(cnpj, exc)

    if parada == 'fim da fila' and modo == 'backfill':
        _virar_incremental(cnpj)

    dur = int((time.monotonic() - t0) * 1000)
    # qtd_salvos conta TUDO que foi gravado (documento e evento); a quarentena é
    # a diferença para qtd_docs. Não vai em qtd_duplicados: aquela coluna quer
    # dizer "documento que já estava aqui", coisa diferente de "não deu para ler".
    nota = erro or (f'{quarentena} em quarentena' if quarentena else None)
    _log(empresa_id, cnpj, modo, nsu_inicial=nsu_inicial, nsu_final=nsu,
         qtd_docs=salvos + eventos + quarentena, qtd_salvos=salvos + eventos,
         duracao_ms=dur, erro=nota)
    return {'ok': erro is None, 'empresa_id': empresa_id, 'cnpj': cnpj,
            'nsu_inicial': nsu_inicial, 'nsu_final': nsu, 'lotes': lotes,
            'salvos': salvos, 'eventos': eventos, 'quarentena': quarentena,
            'parada': parada, 'erro': erro, 'duracao_s': round(dur / 1000, 1),
            'por_raiz': cert.por_raiz}


def executar_incremental(deadline_seg=DEADLINE_PADRAO_S,
                         ambiente=client.Ambiente.PRODUCAO_RESTRITA) -> dict:
    """O ciclo do cron: todas as empresas ativas, na ordem de quem esperou mais.

    PEGA TAMBÉM QUEM ESTÁ EM BACKFILL. Backfill não é um estado especial, é só
    "ainda não cheguei ao fim da fila" — e um 429 no meio de um backfill deixa a
    empresa exatamente assim. Se o ciclo olhasse só ``modo='incremental'``, essa
    empresa ficaria parada para sempre esperando alguém rodar o comando na mão.
    Aqui ela apenas leva alguns ciclos para drenar, retomando de onde parou.

    O deadline é do CICLO, não de cada empresa: cada uma recebe o tempo que
    ainda resta. Sem isso, a primeira da fila poderia consumir os 960s inteiros
    e o ciclo terminaria tendo atendido uma só.

    Quem já começou termina — cortar no meio manteria o cursor coerente (a regra
    de ouro garante), mas jogaria fora a sessão mTLS já aberta.
    """
    t0 = time.monotonic()
    empresas = execute_query(
        "SELECT n.empresa_id, n.cnpj, n.modo FROM dfe_nsu_nfse n "
        " WHERE n.ativo = 1 "
        " ORDER BY n.ultimo_sucesso IS NOT NULL, n.ultimo_sucesso", fetch=True) or []

    resultados = []
    for e in empresas:
        restante = deadline_seg - (time.monotonic() - t0)
        if restante <= 0:
            logger.info('[nfse-captura] deadline: %d empresa(s) ficaram para o '
                        'próximo ciclo', len(empresas) - len(resultados))
            break
        resultados.append(capturar_empresa(
            e['empresa_id'], e['cnpj'], ambiente=ambiente,
            deadline_seg=restante, modo=e.get('modo') or 'incremental'))

    return {'empresas': len(resultados), 'pendentes': len(empresas) - len(resultados),
            'salvos': sum(r.get('salvos', 0) for r in resultados),
            'eventos': sum(r.get('eventos', 0) for r in resultados),
            'duracao_s': round(time.monotonic() - t0, 1),
            'resultados': resultados}


def main():
    ap = argparse.ArgumentParser(description='Captura de NFS-e pelo ADN')
    ap.add_argument('--modo', choices=('backfill', 'incremental'), default='incremental')
    ap.add_argument('--empresa', type=int, help='cliente_id (obrigatório no backfill)')
    ap.add_argument('--producao', action='store_true',
                    help='usa PRODUÇÃO em vez de produção restrita')
    ap.add_argument('--lotes-max', type=int, default=LOTES_MAX_BACKFILL)
    args = ap.parse_args()

    amb = client.Ambiente.PRODUCAO if args.producao else client.Ambiente.PRODUCAO_RESTRITA
    print(f'ambiente: {amb.name}')

    if args.modo == 'backfill':
        if not args.empresa:
            ap.error('--empresa é obrigatório no backfill (uma por vez, de propósito)')
        c = execute_query('SELECT cpf_cnpj, nome_razao_social FROM clientes WHERE id=%s',
                          (args.empresa,), fetch=True, fetch_one=True)
        if not c:
            ap.error(f'cliente {args.empresa} não encontrado')
        cnpj = ''.join(ch for ch in (c['cpf_cnpj'] or '') if ch.isdigit())
        print(f'backfill: {c["nome_razao_social"]} ({cnpj})')
        r = capturar_empresa(args.empresa, cnpj, ambiente=amb,
                             lotes_max=args.lotes_max, modo='backfill')
    else:
        r = executar_incremental(ambiente=amb)

    for k, v in r.items():
        if k != 'resultados':
            print(f'  {k}: {v}')
    print(f'  {datetime.now():%d/%m/%Y %H:%M:%S}')


if __name__ == '__main__':
    main()
