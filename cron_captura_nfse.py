# -*- coding: utf-8 -*-
"""Entrypoint do CRON de captura de NFS-e pelo ADN (Railway Cron).

Mesmo molde do ``cron_captura_dfe.py`` — e SERVIÇO SEPARADO dele, de propósito.

Por que não pendurar no cron de DFe
-----------------------------------
São dois provedores sem nada em comum: SEFAZ (SOAP, cota 656 por CNPJ, uma hora
de castigo) e ADN (REST, mTLS, 429 com Retry-After). Compartilhar o processo faria
o pior dos dois mundos: um backfill de NFS-e comendo o tick e atrasando a captura
de NF-e, ou um 656 arrastando a NFS-e junto. Serviço próprio, horário próprio,
falha própria.

Guarda de segurança
-------------------
Só roda com ``NFSE_SCHED_ATIVO=1`` no ambiente do serviço de Cron. Enquanto a flag
não existir, este script sai sem tocar em nada — é o que permite ele estar no repo
e no deploy antes de o módulo ser liberado.

Ambiente
--------
``NFSE_AMBIENTE=producao`` usa a produção do ADN; qualquer outro valor (ou a
ausência) usa **produção restrita**, que é o default seguro. Está explícito no log
de cada rodada porque errar isso significa gravar documento de um ambiente achando
que é do outro.

SOMENTE LEITURA
---------------
Este cron consulta e grava no banco local. NUNCA envia evento ao ADN — nem
manifestação, nem confirmação, nem cancelamento. O ADN aceita eventos; o
Qualicontax não emite nenhum. No fim da rodada ele também confere, por GET no
Sefin, se alguma nota EMITIDA pelo sistema foi cancelada fora dele (portal do
governo) e marca a situação aqui.
"""
import logging
import os
import sys

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


def main() -> int:
    if os.getenv('NFSE_SCHED_ATIVO') != '1':
        logger.warning('[cron-nfse] NFSE_SCHED_ATIVO != 1 — nada a fazer.')
        return 0

    try:
        from utils.integrations.nfse_adn import captura, client
    except Exception:
        logger.exception('[cron-nfse] Falha ao importar o módulo — abortando.')
        return 1

    amb = (client.Ambiente.PRODUCAO
           if os.getenv('NFSE_AMBIENTE', '').lower() == 'producao'
           else client.Ambiente.PRODUCAO_RESTRITA)
    prazo = int(os.getenv('NFSE_SCHED_PRAZO_SEG', captura.DEADLINE_PADRAO_S))

    logger.warning('[cron-nfse] >>> INICIADO (pid=%s, ambiente=%s, prazo=%ss).',
                   os.getpid(), amb.name, prazo)
    try:
        r = captura.executar_incremental(deadline_seg=prazo, ambiente=amb)
        logger.warning(
            '[cron-nfse] >>> CONCLUIDO: %s empresa(s), %s documento(s), %s evento(s), '
            '%s ficaram para o próximo ciclo, %ss.',
            r['empresas'], r['salvos'], r['eventos'], r['pendentes'], r['duracao_s'])
        for x in r['resultados']:
            if not x.get('ok'):
                logger.warning('[cron-nfse]   %s parou em %r: %s',
                               x.get('cnpj'), x.get('parada'), x.get('erro'))
    except Exception:
        # capturar_empresa já isola empresa a empresa; chegar aqui é falha do
        # ciclo (banco fora do ar, por exemplo) e merece execução marcada como
        # falha no Railway.
        logger.exception('[cron-nfse] Rodada falhou por inteiro.')
        return 1
    # Notas EMITIDAS por aqui: pergunta ao Sefin (GET, nada é enviado) se alguma foi cancelada
    # fora do sistema — pelo portal do governo, por exemplo. 18/11/2026: a Congonhas cancelou
    # duas notas no portal e o sistema seguiu mostrando "Autorizada".
    try:
        from utils import nfse_emissor
        c = nfse_emissor.conferir_cancelamentos()
        quais = ', '.join(f"nota {x['linha']['id']}" for x in c['canceladas'])
        logger.warning('[cron-nfse] >>> NOTAS EMITIDAS: %s conferida(s) com o Sefin, %s cancelada(s) fora do sistema%s, %s falha(s).',
                       c['conferidas'], len(c['canceladas']), f' ({quais})' if quais else '', len(c['falhas']))
        for nid, msg in c['falhas']:
            logger.warning('[cron-nfse]   nota %s não conferida: %s', nid, msg)
    except Exception:
        logger.exception('[cron-nfse] Conferência das notas emitidas falhou (a captura já tinha terminado).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
