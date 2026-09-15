# -*- coding: utf-8 -*-
"""Roteador de EXTRATO — a _ENTRADA vira lançamento sozinha (21/08/2026).

Mesma ideia do ``cron_roteador.py`` (que faz isso com .xml desde agosto): o
usuário NÃO importa nada. Salvou o arquivo do banco na pasta — veio do
WhatsApp, do e-mail, do internet banking —, o Q-Colabore leva para a
``_ENTRADA`` e este cron lê, lança e arquiva. Decisão do Anderson em
20/08/2026: "não quero importar, igual faz com o XML".

REGRA DE FERRO, igual à do roteador de XML: este cron só toca em arquivo de
EXTRATO — ``.ofx`` e, desde 14/09/2026, o ``.pdf`` de extrato do C6 (o OFX
do C6 não diz para quem foi o Pix; o PDF diz e vale sozinho — os dois se
encaixam sem duplicar; ``utils/extrato_pdf_c6``).
Whitelist, não lista de proibidos: .pfx, .xml e o que aparecer amanhã já
nascem ignorados. Um .pdf que NÃO é extrato do C6 fica onde está, intocado.
Cada tipo tem o seu consumidor e ninguém pisa no do outro.

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
EXTENSOES = ('.ofx', '.pdf', '.csv', '.xls', '.xlsx')   # whitelist — ver REGRA DE FERRO no topo
_ATOR_NOME = 'ROTEADOR (extrato)'
_ATOR_LOGIN = 'roteador_extrato'

ATIVO = os.getenv('EXTRATO_ATIVO', '0').strip() == '1'
DRYRUN = os.getenv('EXTRATO_DRYRUN', '1').strip() == '1'
MAX_ARQ = max(1, int(os.getenv('EXTRATO_MAX_ARQ', '50')))
PRAZO_SEG = max(30, int(os.getenv('EXTRATO_PRAZO_SEG', '600')))


def _tmp(nome):
    import tempfile
    return os.path.join(tempfile.gettempdir(), 'qc_extrato_' + nome)


def _ler_previa(nome, dados, senhas_extra=()):
    """(previa, formato) via ``utils.extrato_formatos.ler_arquivo`` — 'ofx',
    'csv' ou 'pdf' quando leu; 'pdf-senha', 'pdf-outro', 'csv-outro' quando
    vira pendência (a previa traz o motivo); (None, None) quando o arquivo
    não é extrato: fica onde está, intocado."""
    from utils.extrato_formatos import ler_arquivo, ArquivoDesconhecido
    senhas = list(x for x in (senhas_extra or ()) if x)
    if (nome or '').lower().endswith('.pdf'):
        from utils.extrato_pdf_c6 import senhas_candidatas
        senhas += senhas_candidatas(nome)
    try:
        return ler_arquivo(nome, dados, senhas)
    except ArquivoDesconhecido:
        return None, None


def _pendencia_pdf(origem, nome, seco, formato, previa=None):
    """PDF que não abriu, ou extrato em PDF de outro banco: entra na fila de
    Contas, na empresa que o nome do arquivo indicar (ou órfã, para o
    admin). Sem o nome no log."""
    if seco:
        return
    from models.extrato_lancamento import FinExtratoPendencia
    from utils.extrato_ingest import numero_empresa_do_nome
    from utils.extrato_pdf_c6 import (ROTULO_PDF_SENHA, MOTIVO_PDF_SENHA,
                                      ROTULO_PDF_OUTRO, MOTIVO_PDF_OUTRO)
    if formato == 'csv-outro':
        rotulo, motivo = 'CSV de outro layout', (previa or {}).get('motivo') or 'CSV que eu não sei ler.'
    elif formato == 'pdf-outro':
        rotulo, motivo = ROTULO_PDF_OUTRO, (previa or {}).get('motivo') or MOTIVO_PDF_OUTRO
    else:
        rotulo, motivo = ROTULO_PDF_SENHA, MOTIVO_PDF_SENHA
    num = numero_empresa_do_nome(nome)
    dono = None
    if num:
        from utils.db_helper import execute_query as _q
        e = _q('SELECT id FROM clientes WHERE numero_cliente = %s', (num,),
               fetch=True, fetch_one=True)
        dono = (e or {}).get('id')
    FinExtratoPendencia.anotar(caminho=origem, arquivo=nome, motivo=motivo,
                               empresa_id=dono, numero_no_nome=num,
                               banco_nome=rotulo, qtd=0)


def _gravar(formato, caminho, previa, empresa_id, usuario_id=None):
    """Grava o que o arquivo traz. OFX cria lançamentos (e encaixa no que o
    PDF já criou); PDF do C6 completa o que existe e cria o que falta."""
    from utils.extrato_ingest import processar_ofx, processar_lancamentos
    if formato in ('pdf', 'csv', 'planilha'):
        # Com identificador (fitid ou documento) o arquivo entra pelo núcleo,
        # que deduplica por id. Sem identificador (PDF do C6/Nubank/Sicredi,
        # CSV do Cora/C6) entra pelo casamento por data + valor, que completa
        # o que existe e cria o que falta.
        lancs = previa.get('lancamentos') or []
        com_id = sum(1 for l in lancs if l.get('fitid') or l.get('documento'))
        tem_ids = bool(lancs) and com_id >= 0.8 * len(lancs)
        if tem_ids:
            r = processar_lancamentos(previa, empresa_id, os.path.basename(caminho),
                                      usuario_id=usuario_id, origem=formato)
            r.setdefault('completadas', 0)
            return r
        from utils.extrato_pdf_c6 import processar_pdf
        c = processar_pdf(empresa_id, previa, arquivo=os.path.basename(caminho),
                          usuario_id=usuario_id, dry=False)
        return {'novos': c['novos'], 'repetidos': c['repetidos'],
                'classificados': c['classificados'], 'completadas': c['completadas'],
                'no_pdf': c['no_pdf'], 'ambiguos': c['ambiguos'], 'travados': c['travados']}
    return processar_ofx(caminho, empresa_id, usuario_id=usuario_id)


def _dono_pelo_pdf(previa):
    """Cliente cujo CPF é o do titular impresso no PDF, ou None."""
    cpf = (previa or {}).get('cpf')
    if not cpf:
        return None
    from models.cliente import Cliente
    return Cliente.index_cpf_cnpj().get(cpf)


def rodar(dryrun=None, limite=None):
    """Varre a _ENTRADA. Devolve o resumo (e imprime o que fez)."""
    from utils.dropbox_sync import _service
    from utils.extrato_ingest import (identificar_empresa,
                                      nome_arquivo_final, pasta_destino,
                                      banco_curto, numero_empresa_do_nome)
    from utils.ofx_parser import OfxInvalido
    from utils.extrato_pdf_c6 import PdfInvalido
    from utils.atividade import registrar_agente

    seco = DRYRUN if dryrun is None else dryrun
    teto = limite or MAX_ARQ
    prazo = time.monotonic() + PRAZO_SEG

    svc = _service
    base = svc._build_path(PASTA_ORIGEM)
    # list_folder (generico), NAO list_xml_files: aquele peneira .xml e nao
    # devolveria nenhum .ofx. Sem recursivo: a _ENTRADA e plana por definicao.
    itens = svc.list_folder(base, recursive=False) or []
    # Pendencia cujo arquivo nao esta mais na pasta (renomeado/apagado) sai da
    # tela; a versao renomeada, se houver, entra como pendencia nova abaixo.
    if not seco:
        try:
            from models.extrato_lancamento import FinExtratoPendencia as _Pend
            _n = _Pend.encerrar_sumidas([x.get('path') for x in itens if x.get('is_file')])
            if _n:
                logger.info('[extrato] %d pendencia(s) encerrada(s): arquivo saiu da pasta.', _n)
        except Exception:
            logger.exception('[extrato] falha ao encerrar pendencias sumidas (segue).')

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
            previa, formato = _ler_previa(nome, dados)
            if formato in ('pdf-senha', 'pdf-outro', 'csv-outro'):
                _pendencia_pdf(origem, nome, seco, formato, previa)
                linha['resultado'] = {'pdf-senha': 'PENDENTE: PDF com senha que nenhum documento abriu',
                                      'pdf-outro': 'PENDENTE: extrato em PDF de outro banco (mande o OFX)',
                                      'csv-outro': 'PENDENTE: ' + (previa or {}).get('motivo', 'colunas que eu não conheço')}[formato]
                resumo['erros'] += 1
                resumo['detalhes'].append(linha)
                logger.warning('[extrato] %s; está na fila de Contas.',
                               {'pdf-senha': 'um PDF com senha não abriu',
                                'pdf-outro': 'um extrato em PDF de outro banco',
                                'csv-outro': 'uma tabela de layout desconhecido'}[formato])
                continue
            if not formato:
                resumo['lidos'] -= 1
                resumo['ignorados'] += 1
                continue
            banco = banco_curto(previa.get('banco_id'), previa.get('banco'))
            if formato == 'ofx':
                cliente, motivo = identificar_empresa(
                    nome, banco_id=previa.get('banco_id'),
                    conta=previa.get('conta'), banco_nome=banco)
            else:
                # CSV e PDF: a conta pode vir no arquivo, nos documentos já
                # gravados ou no nome do arquivo — identificar_arquivo decide.
                from utils.extrato_ingest import identificar_arquivo
                cliente, conta_str, motivo = identificar_arquivo(nome, previa)
                if conta_str:
                    previa['conta'] = conta_str
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
                    if not num_nome:
                        # Primeiro arquivo de uma conta precisa dizer de quem e:
                        # numero do cadastro no comeco ou CPF/CNPJ no nome. Sem
                        # isso a pendencia nasce orfa (so o admin ve) e o aviso
                        # diz o que falta.
                        motivo += (' Coloque o número do cadastro ou o CPF/CNPJ '
                                   'no nome do arquivo para ela aparecer na '
                                   'empresa certa.')
                    dono_nome = None
                    if num_nome:
                        from utils.db_helper import execute_query as _q
                        e = _q('SELECT id FROM clientes WHERE numero_cliente = %s',
                               (num_nome,), fetch=True, fetch_one=True)
                        dono_nome = (e or {}).get('id')
                    if not dono_nome and formato == 'pdf':
                        # O PDF do C6 imprime o CPF do titular: a pendência
                        # já nasce na tela da pessoa certa.
                        dono_nome = _dono_pelo_pdf(previa)
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
                nome_final = nome_arquivo_final(banco, previa.get('conta'), datas,
                                                nome.lower().rsplit('.', 1)[-1])
                linha['destino'] = f'{destino_pasta}/{nome_final}'

                if seco:
                    linha['resultado'] = 'SIMULACAO — nada gravado, nada movido'
                    resumo['detalhes'].append(linha)
                    continue

                r = _gravar(formato, caminho, previa, cliente['id'], None)
                resumo['novos'] += r['novos']
                resumo['repetidos'] += r['repetidos']
                resumo['classificados'] += r['classificados']
                resumo['lancados'] += 1
                linha.update({'novos': r['novos'], 'repetidos': r['repetidos'],
                              'classificados': r['classificados']})
                if formato == 'pdf':
                    linha['completadas'] = r['completadas']
                    resumo['completadas'] = resumo.get('completadas', 0) + r['completadas']
                else:
                    if r.get('encaixados'):
                        linha['encaixados'] = r['encaixados']
                    from utils.extrato_pdf_c6 import aviso_ofx
                    aviso = aviso_ofx(previa.get('banco_id'), previa.get('banco'))
                    if aviso:
                        linha['aviso'] = aviso
                        logger.info('[extrato] OFX do C6 lançado; o completo é o PDF.')

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
                            'repetidos': r['repetidos'], 'origem': 'roteador',
                            'formato': formato,
                            'completadas': r.get('completadas'),
                            'aviso': linha.get('aviso')})
            finally:
                try:
                    os.unlink(caminho)
                except OSError:
                    pass
        except (OfxInvalido, PdfInvalido) as e:
            linha['resultado'] = f'RECUSADO: {e}'
            resumo['erros'] += 1
            logger.warning('[extrato] %s recusado: %s', nome, e)
        except Exception as e:
            linha['resultado'] = f'ERRO: {type(e).__name__}: {e}'
            resumo['erros'] += 1
            logger.exception('[extrato] falha em %s', nome)
        resumo['detalhes'].append(linha)

    return resumo


def processar_um(caminho_dropbox, usuario_id=None, senha_extra=None):
    """Processa UM arquivo, pelo caminho — nada além dele.

    Existe porque responder uma pendência NÃO pode disparar varredura geral:
    em 21/08/2026 um teste chamou a rota de resposta e ela varreu a pasta
    inteira, movendo 5 arquivos reais que ninguém tinha mandado mover. Quem
    responde uma pergunta mexe só no que foi perguntado.
    """
    from utils.dropbox_sync import _service
    from utils.extrato_ingest import (identificar_empresa,
                                      nome_arquivo_final, pasta_destino,
                                      banco_curto)
    from models.extrato_lancamento import FinExtratoPendencia

    svc = _service
    dados = svc.download_file(caminho_dropbox)
    if not dados:
        return {'ok': False, 'motivo': 'arquivo não está mais na pasta'}

    nome = os.path.basename(caminho_dropbox)
    previa, formato = _ler_previa(nome, dados, [senha_extra] if senha_extra else ())
    if formato == 'pdf-senha':
        from utils.extrato_pdf_c6 import MOTIVO_PDF_SENHA
        return {'ok': False, 'motivo': 'Ainda não abriu. ' + MOTIVO_PDF_SENHA, 'pdf_senha': True}
    if formato in ('pdf-outro', 'csv-outro'):
        return {'ok': False, 'motivo': (previa or {}).get('motivo') or 'arquivo que eu não sei ler'}
    if not formato:
        return {'ok': False, 'motivo': 'O arquivo abriu, mas não é um extrato que eu saiba ler.'}
    banco = banco_curto(previa.get('banco_id'), previa.get('banco'))
    if formato == 'ofx':
        cliente, motivo = identificar_empresa(
            nome, banco_id=previa.get('banco_id'), conta=previa.get('conta'),
            banco_nome=banco)
    else:
        from utils.extrato_ingest import identificar_arquivo
        cliente, conta_str, motivo = identificar_arquivo(nome, previa)
        if conta_str:
            previa['conta'] = conta_str
    if not cliente:
        from utils.extrato_ingest import rotulo_periodo
        return {'ok': False, 'motivo': motivo, 'formato': formato,
                'previa': {'banco_id': previa.get('banco_id'), 'banco': banco,
                           'conta': previa.get('conta'), 'cpf': previa.get('cpf'),
                           'qtd': len(previa['lancamentos']),
                           'periodo': rotulo_periodo([l['data'] for l in previa['lancamentos']])}}

    caminho = _tmp(nome)
    with open(caminho, 'wb') as fh:
        fh.write(dados)
    try:
        r = _gravar(formato, caminho, previa, cliente['id'], usuario_id)
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
    final = nome_arquivo_final(banco, previa.get('conta'), datas,
                               nome.lower().rsplit('.', 1)[-1])
    movido = svc.move_file(caminho_dropbox, f'{destino}/{final}')
    FinExtratoPendencia.limpar_resolvidas([caminho_dropbox])
    from utils.extrato_pdf_c6 import aviso_ofx
    aviso = aviso_ofx(previa.get('banco_id'), previa.get('banco')) if formato == 'ofx' else ''
    return {'ok': True, 'empresa': cliente['nome_razao_social'], 'aviso': aviso or None,
            'banco': banco, 'novos': r['novos'], 'repetidos': r['repetidos'],
            'classificados': r['classificados'], 'arquivado': movido,
            'formato': formato, 'completadas': r.get('completadas'),
            'encaixados': r.get('encaixados'), 'destino': f'{destino}/{final}'}


def _conectar_lock():
    """Conexão dedicada para segurar o GET_LOCK durante toda a varredura.

    Cópia deliberada de ``cron_roteador._conectar_lock`` — mesma forma, para os
    dois não divergirem quando um for corrigido.
    """
    import mysql.connector
    from config import Config
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT,
        autocommit=True, time_zone='-03:00')


def main():
    if not ATIVO:
        logger.info('[extrato] EXTRATO_ATIVO != 1 — nada a fazer.')
        return 0

    # LOCK PRÓPRIO, com nome SEPARADO do 'roteador'. O motivo é concreto: o
    # tick do roteador que encontra o lock dele ocupado RETORNA e segue para
    # esta varredura — então dois ticks sobrepostos chegariam aqui juntos e
    # leriam a mesma _ENTRADA. Nome separado (e não o mesmo lock) porque os
    # dois cérebros da _ENTRADA não disputam arquivo nenhum: .xml é de um,
    # .ofx é do outro, por whitelist.
    conn = _conectar_lock()
    cur = conn.cursor(buffered=True)
    cur.execute("SELECT GET_LOCK('roteador_extrato', 0)")
    if (cur.fetchone() or [0])[0] != 1:
        logger.info('[extrato] lock ocupado — outra varredura em andamento; pulando.')
        cur.close()
        conn.close()
        return 0

    try:
        r = rodar()
        logger.info('[extrato] %s lido(s) | %s lançado(s) | %s novo(s) | '
                    '%s repetido(s) | %s classificado(s) | %s erro(s)',
                    r['lidos'], r['lancados'], r['novos'], r['repetidos'],
                    r['classificados'], r['erros'])
        for d in r['detalhes']:
            logger.info('   %s -> %s', d.get('arquivo'), d.get('resultado'))
    finally:
        try:
            cur.execute("SELECT RELEASE_LOCK('roteador_extrato')")
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
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
