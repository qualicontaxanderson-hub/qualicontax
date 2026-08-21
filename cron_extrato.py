# -*- coding: utf-8 -*-
"""Roteador de EXTRATO — a _ENTRADA vira lançamento sozinha (21/08/2026).

Mesma ideia do ``cron_roteador.py`` (que faz isso com .xml desde agosto): o
usuário NÃO importa nada. Salvou o arquivo do banco na pasta — veio do
WhatsApp, do e-mail, do internet banking —, o Q-Colabore leva para a
``_ENTRADA`` e este cron lê, lança e arquiva. Decisão do Anderson em
20/08/2026: "não quero importar, igual faz com o XML".

REGRA DE FERRO, igual à do roteador de XML: este cron só toca em arquivo de
EXTRATO — hoje ``.ofx``. Whitelist, não lista de proibidos: .pfx, .pdf, .xml
e o que aparecer amanhã já nascem ignorados. Cada tipo tem o seu consumidor e
ninguém pisa no do outro.

Errou? Não há o que "desfazer com cuidado": o usuário apaga o período na tela
do Extrato e manda o arquivo de novo. A idempotência (hash_dedup) garante que
reenviar o mesmo arquivo não duplica nada.

Variáveis:
    EXTRATO_ATIVO=1     liga (nasce desligado)
    EXTRATO_DRYRUN=1    só diz o que faria (padrão SIM)
    EXTRATO_MAX_ARQ=50  teto por rodada
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s')
logger = logging.getLogger('cron_extrato')

PASTA_ORIGEM = '_ENTRADA'
EXTENSOES = ('.ofx',)          # whitelist — ver REGRA DE FERRO no topo
_ATOR_NOME = 'ROTEADOR (extrato)'
_ATOR_LOGIN = 'roteador_extrato'

ATIVO = os.getenv('EXTRATO_ATIVO', '0').strip() == '1'
DRYRUN = os.getenv('EXTRATO_DRYRUN', '1').strip() == '1'
MAX_ARQ = max(1, int(os.getenv('EXTRATO_MAX_ARQ', '50')))
PRAZO_SEG = max(30, int(os.getenv('EXTRATO_PRAZO_SEG', '600')))


def _tmp(nome):
    import tempfile
    return os.path.join(tempfile.gettempdir(), 'qc_extrato_' + nome)


def rodar(dryrun=None, limite=None):
    """Varre a _ENTRADA. Devolve o resumo (e imprime o que fez)."""
    from utils.dropbox_sync import _service
    from utils.extrato_ingest import (identificar_empresa, processar_ofx,
                                      nome_arquivo_final, pasta_destino,
                                      banco_curto, numero_empresa_do_nome)
    from utils.ofx_parser import parse_ofx, OfxInvalido
    from utils.atividade import registrar_agente

    seco = DRYRUN if dryrun is None else dryrun
    teto = limite or MAX_ARQ
    prazo = time.monotonic() + PRAZO_SEG

    svc = _service
    base = svc._build_path(PASTA_ORIGEM)
    # list_folder (generico), NAO list_xml_files: aquele peneira .xml e nao
    # devolveria nenhum .ofx. Sem recursivo: a _ENTRADA e plana por definicao.
    itens = svc.list_folder(base, recursive=False) or []

    resumo = {'lidos': 0, 'lancados': 0, 'novos': 0, 'repetidos': 0,
              'classificados': 0, 'ignorados': 0, 'erros': 0, 'detalhes': []}

    for item in itens:
        if time.monotonic() > prazo or resumo['lidos'] >= teto:
            logger.info('[extrato] prazo/teto atingido; o resto vai no próximo tick.')
            break
        nome = item.get('name') or ''
        if not item.get('is_file') or not nome.lower().endswith(EXTENSOES):
            resumo['ignorados'] += 1
            continue

        origem = item.get('path')
        resumo['lidos'] += 1
        linha = {'arquivo': nome}
        try:
            dados = svc.download_file(origem)
            if not dados:
                raise RuntimeError('não consegui baixar do Dropbox')

            # Lê o arquivo ANTES de decidir o dono: é a CONTA que manda.
            previa = parse_ofx(dados)
            banco = banco_curto(previa.get('banco_id'), previa.get('banco'))
            cliente, motivo = identificar_empresa(
                nome, banco_id=previa.get('banco_id'),
                conta=previa.get('conta'), banco_nome=banco)
            linha['empresa'] = (cliente or {}).get('nome_razao_social')
            linha['motivo'] = motivo
            linha['banco'] = banco
            linha['conta'] = previa.get('conta')
            linha['lancamentos'] = len(previa['lancamentos'])
            if not cliente:
                # Sem dono não se lança nada — o arquivo FICA na _ENTRADA e
                # vira PENDÊNCIA. Com número no nome ela nasce amarrada àquela
                # empresa (aparece quando alguém abrir a 148); sem número,
                # nasce órfã esperando alguém dizer de quem é.
                if not seco:
                    from models.extrato_lancamento import FinExtratoPendencia
                    from utils.extrato_ingest import rotulo_periodo
                    num_nome = numero_empresa_do_nome(nome)
                    dono_nome = None
                    if num_nome:
                        from utils.db_helper import execute_query as _q
                        e = _q('SELECT id FROM clientes WHERE numero_cliente = %s',
                               (num_nome,), fetch=True, fetch_one=True)
                        dono_nome = (e or {}).get('id')
                    FinExtratoPendencia.anotar(
                        caminho=origem, arquivo=nome, motivo=motivo,
                        empresa_id=dono_nome, numero_no_nome=num_nome,
                        banco_id=previa.get('banco_id'), banco_nome=banco,
                        conta=previa.get('conta'),
                        qtd=len(previa['lancamentos']),
                        periodo=rotulo_periodo([l['data'] for l in previa['lancamentos']]))
                linha['resultado'] = 'PENDENTE: ' + motivo
                resumo['erros'] += 1
                resumo['detalhes'].append(linha)
                logger.warning('[extrato] %s pendente: %s', nome, motivo)
                continue

            caminho = _tmp(nome)
            with open(caminho, 'wb') as fh:
                fh.write(dados)
            try:
                datas = [l['data'] for l in previa['lancamentos']]
                ano = (max(datas)[:4] if datas else str(__import__('datetime').date.today().year))
                destino_pasta = pasta_destino(
                    cliente['numero_cliente'], cliente['nome_razao_social'], ano)
                nome_final = nome_arquivo_final(banco, previa.get('conta'),
                                                datas, 'ofx')
                linha['destino'] = f'{destino_pasta}/{nome_final}'

                if seco:
                    linha['resultado'] = 'SIMULACAO — nada gravado, nada movido'
                    resumo['detalhes'].append(linha)
                    continue

                r = processar_ofx(caminho, cliente['id'], usuario_id=None)
                resumo['novos'] += r['novos']
                resumo['repetidos'] += r['repetidos']
                resumo['classificados'] += r['classificados']
                resumo['lancados'] += 1
                linha.update({'novos': r['novos'], 'repetidos': r['repetidos'],
                              'classificados': r['classificados']})

                # Arquiva SÓ depois de gravar: arquivo movido sem lançamento
                # seria perda silenciosa. MOVER (e não subir+apagar) é uma
                # operação só — não existe instante em que o arquivo esteja
                # nos dois lugares nem em nenhum.
                svc.ensure_folder(destino_pasta)
                if svc.move_file(origem, f'{destino_pasta}/{nome_final}'):
                    linha['resultado'] = 'lançado e arquivado'
                else:
                    linha['resultado'] = ('lançado; ARQUIVAMENTO FALHOU — '
                                          'o arquivo ficou na _ENTRADA')
                from models.extrato_lancamento import FinExtratoPendencia
                FinExtratoPendencia.limpar_resolvidas([origem])
                registrar_agente(
                    'escrita.importou_extrato', 'financeiro',
                    usuario_id=None, usuario_nome=_ATOR_NOME,
                    usuario_login=_ATOR_LOGIN, tabela='extrato_lancamentos',
                    depois={'arquivo': nome, 'empresa_id': cliente['id'],
                            'banco': banco, 'novos': r['novos'],
                            'repetidos': r['repetidos'], 'origem': 'roteador'})
            finally:
                try:
                    os.unlink(caminho)
                except OSError:
                    pass
        except OfxInvalido as e:
            linha['resultado'] = f'RECUSADO: {e}'
            resumo['erros'] += 1
            logger.warning('[extrato] %s recusado: %s', nome, e)
        except Exception as e:
            linha['resultado'] = f'ERRO: {type(e).__name__}: {e}'
            resumo['erros'] += 1
            logger.exception('[extrato] falha em %s', nome)
        resumo['detalhes'].append(linha)

    return resumo


def processar_um(caminho_dropbox, usuario_id=None):
    """Processa UM arquivo, pelo caminho — nada além dele.

    Existe porque responder uma pendência NÃO pode disparar varredura geral:
    em 21/08/2026 um teste chamou a rota de resposta e ela varreu a pasta
    inteira, movendo 5 arquivos reais que ninguém tinha mandado mover. Quem
    responde uma pergunta mexe só no que foi perguntado.
    """
    from utils.dropbox_sync import _service
    from utils.extrato_ingest import (identificar_empresa, processar_ofx,
                                      nome_arquivo_final, pasta_destino,
                                      banco_curto)
    from utils.ofx_parser import parse_ofx
    from models.extrato_lancamento import FinExtratoPendencia

    svc = _service
    dados = svc.download_file(caminho_dropbox)
    if not dados:
        return {'ok': False, 'motivo': 'arquivo não está mais na pasta'}

    previa = parse_ofx(dados)
    banco = banco_curto(previa.get('banco_id'), previa.get('banco'))
    nome = os.path.basename(caminho_dropbox)
    cliente, motivo = identificar_empresa(
        nome, banco_id=previa.get('banco_id'), conta=previa.get('conta'),
        banco_nome=banco)
    if not cliente:
        return {'ok': False, 'motivo': motivo}

    caminho = _tmp(nome)
    with open(caminho, 'wb') as fh:
        fh.write(dados)
    try:
        r = processar_ofx(caminho, cliente['id'], usuario_id=usuario_id)
    finally:
        try:
            os.unlink(caminho)
        except OSError:
            pass

    datas = [l['data'] for l in previa['lancamentos']]
    ano = max(datas)[:4] if datas else str(__import__('datetime').date.today().year)
    destino = pasta_destino(cliente['numero_cliente'],
                            cliente['nome_razao_social'], ano)
    svc.ensure_folder(destino)
    final = nome_arquivo_final(banco, previa.get('conta'), datas, 'ofx')
    movido = svc.move_file(caminho_dropbox, f'{destino}/{final}')
    FinExtratoPendencia.limpar_resolvidas([caminho_dropbox])
    return {'ok': True, 'empresa': cliente['nome_razao_social'],
            'banco': banco, 'novos': r['novos'], 'repetidos': r['repetidos'],
            'classificados': r['classificados'], 'arquivado': movido,
            'destino': f'{destino}/{final}'}


def main():
    if not ATIVO:
        logger.info('[extrato] EXTRATO_ATIVO != 1 — nada a fazer.')
        return 0
    r = rodar()
    logger.info('[extrato] %s lido(s) | %s lançado(s) | %s novo(s) | '
                '%s repetido(s) | %s classificado(s) | %s erro(s)',
                r['lidos'], r['lancados'], r['novos'], r['repetidos'],
                r['classificados'], r['erros'])
    for d in r['detalhes']:
        logger.info('   %s -> %s', d.get('arquivo'), d.get('resultado'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
