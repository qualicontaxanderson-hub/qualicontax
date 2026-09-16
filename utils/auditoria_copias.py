# -*- coding: utf-8 -*-
"""AUDITORIA DE CÓPIAS — toda nota expurgada tem mesmo o arquivo no Dropbox?

Por que isto existe
-------------------
Com a regra de 12/09/2026 (``utils/expurgo_xml``), o XML de tudo que passa de
3 meses sai do banco e **o Dropbox vira a ÚNICA cópia**. O expurgo já se protege
— só apaga com ``xml_caminho`` preenchido —, mas isso prova que ALGUÉM gravou um
caminho, não que o arquivo está lá AGORA. Quem prova é olhar a pasta.

Em 16/09/2026 essa conferência foi feita a mão, nas 380 pastas de notas antigas:
**1.136.066 notas expurgadas, todas com arquivo íntegro no Dropbox, nenhuma
perdida, nenhuma de 0 byte.** No caminho ela achou 6.843 notas que tinham UMA
cópia só (ver [arquivar_saidas] e a repescagem). Nenhum painel mostrava isso —
o ``fila_total`` da época dizia 0 porque a consulta vinha cortada pelo teto.

A lição que fez virar rotina: **medir que o banco esvaziou não é o mesmo que
provar que o documento existe.** Uma varredura que ninguém dispara é uma
garantia que depende de alguém desconfiar.

Como roda
---------
Uma fatia por rodada da manutenção, com cursor próprio, igual à confirmação:
anda por (cliente, tipo, ano, mês), lista a pasta uma vez e classifica as notas
daquele mês. Ao fechar a volta, guarda o resultado e **só recomeça depois de
``AUDITORIA_DIAS``** (default 30) — a volta inteira lista ~1,1 milhão de
arquivos, e não há motivo para repetir isso toda hora.

O que cada estado quer dizer
----------------------------
``ok``        expurgada e o arquivo está lá, com conteúdo. É o caso normal.
``PERDIDA``   expurgada e **não há arquivo**. É o único ALARME de verdade:
              documento sem nenhuma cópia. Nunca aconteceu até hoje.
``vazia``     expurgada e o arquivo tem 0 byte — existe, mas não serve.
``orfa``      ainda tem XML no banco e não está no Dropbox: falta a SEGUNDA
              cópia. Sem risco imediato; é a repescagem que resolve.
``no_banco``  ainda tem XML no banco e já está no Dropbox: duas cópias, normal
              para nota de menos de 3 meses.
"""
import logging
import os
import time
from datetime import date

logger = logging.getLogger(__name__)

#: Dias entre uma volta completa e a seguinte.
DIAS = max(1, int(os.getenv('AUDITORIA_DIAS', '30')))

#: Cursor + parciais da volta em andamento (gravado a cada rodada).
MARCA_ANDAMENTO = 'auditoria_copias_andamento'
#: Resultado da última volta FECHADA. A idade dele é o que decide recomeçar,
#: por isso ele não pode ser tocado no meio da volta.
MARCA_RESULTADO = 'auditoria_copias'

#: Amostra de chaves guardada quando algo dá errado — para o conserto começar
#: com o documento na mão, não com uma contagem.
AMOSTRA = 50


def _ler(chave):
    from utils.painel_cache import ler
    v, idade = ler(chave)
    return (v or {}), idade


def _guardar(chave, valor):
    from utils.painel_cache import guardar
    guardar(chave, valor)


def _zerado():
    return {'pastas': 0, 'nao_listadas': 0, 'notas': 0,
            'ok': 0, 'PERDIDA': 0, 'vazia': 0, 'orfa': 0, 'no_banco': 0,
            'perdidas_chaves': [], 'vazias_chaves': [], 'pastas_sem_lista': []}


def _grupos():
    """(cliente_id, tipo, ano, mes) das notas antigas — index-only (idx_lista)."""
    from utils.db_helper import execute_query
    from utils.expurgo_xml import SQL_CORTE
    return execute_query(
        f"SELECT cliente_id, tipo, YEAR(data_emissao) ano, MONTH(data_emissao) mes, COUNT(*) n "
        f"  FROM nfe_importacoes WHERE cliente_id IS NOT NULL AND data_emissao < {SQL_CORTE} "
        f" GROUP BY cliente_id, tipo, YEAR(data_emissao), MONTH(data_emissao) "
        f" ORDER BY cliente_id, tipo, ano, mes", fetch=True) or []


def auditar(svc, max_pastas=12, prazo_seg=50):
    """Confere uma fatia das pastas. Devolve o andamento desta rodada.

    Só leitura: nunca escreve em ``nfe_importacoes``. O que ela produz é
    conhecimento, não conserto — quem conserta é a repescagem do arquivador.
    """
    from utils.db_helper import execute_query
    r = {'rodou': False}
    if not svc.is_configured():
        return r

    andamento, _ = _ler(MARCA_ANDAMENTO)
    pos = andamento.get('pos')
    parciais = andamento.get('parciais') or _zerado()
    inicio = andamento.get('inicio')

    if not pos:
        # Volta nova só depois do intervalo. A idade vem do relógio do BANCO.
        _, idade = _ler(MARCA_RESULTADO)
        if idade is not None and idade < DIAS * 86400:
            return {'rodou': False, 'proxima_volta_em_dias': round((DIAS * 86400 - idade) / 86400, 1)}
        parciais = _zerado()
        inicio = _agora_do_banco()      # fixado AQUI: a volta pode fechar já nesta rodada

    grupos = _grupos()
    if not grupos:
        return r
    clientes = {c['id']: c for c in (execute_query(
        "SELECT id, numero_cliente, nome_razao_social FROM clientes", fetch=True) or [])}

    t0 = time.monotonic()
    marca = tuple(pos) if pos else None
    feitas = 0
    ultima = None
    r['rodou'] = True

    for g in grupos:
        chave = (g['cliente_id'], g['tipo'], int(g['ano']), int(g['mes']))
        if marca and chave <= marca:
            continue
        if feitas >= max_pastas or (time.monotonic() - t0) > prazo_seg:
            break
        ultima = chave
        feitas += 1
        cli = clientes.get(g['cliente_id']) or {}
        rotulo = '%s %s %s-%02d' % ((cli.get('numero_cliente') or '-'),
                                    g['tipo'][:3], chave[2], chave[3])
        pasta = svc.pasta_fiscal(cli.get('nome_razao_social') or 'SEM_NOME', chave[2], chave[3],
                                 'SAIDAS' if g['tipo'] == 'saida' else 'ENTRADAS',
                                 (cli.get('numero_cliente') or '').strip() or None)
        try:
            itens = svc.list_folder(pasta)
        except Exception as exc:
            # Pasta que não existe NÃO é alarme por si só: pode ser mês que só
            # tem resumo de DFe (sem documento nenhum para arquivar). Vira
            # alarme se houver nota EXPURGADA nela — e isso o laço abaixo não
            # chega a ver, então conferimos aqui.
            parciais['nao_listadas'] += 1
            expurgadas = execute_query(
                "SELECT COUNT(*) n FROM nfe_importacoes "
                " WHERE cliente_id=%s AND tipo=%s AND YEAR(data_emissao)=%s "
                "   AND MONTH(data_emissao)=%s AND LENGTH(xml_raw)=0",
                (chave[0], chave[1], chave[2], chave[3]), fetch=True, fetch_one=True) or {}
            n_exp = int(expurgadas.get('n') or 0)
            if len(parciais['pastas_sem_lista']) < AMOSTRA:
                parciais['pastas_sem_lista'].append({'pasta': rotulo, 'notas': int(g['n']),
                                                     'expurgadas': n_exp})
            if n_exp:
                parciais['PERDIDA'] += n_exp
                logger.error('[auditoria] ALARME: %s nota(s) EXPURGADAS em pasta que não '
                             'existe no Dropbox (%s). Documento sem cópia.', n_exp, pasta)
            else:
                logger.info('[auditoria] pasta %s não listada (%s); sem nota expurgada, segue.',
                            pasta, str(exc)[:60])
            continue

        nomes = {i['name'].lower(): (i.get('size') or 0) for i in itens if i.get('is_file')}
        parciais['pastas'] += 1

        ini = date(chave[2], chave[3], 1)
        fim = date(chave[2] + (chave[3] == 12), (chave[3] % 12) + 1, 1)
        rows = execute_query(
            "SELECT chave_acesso, nome_arquivo, xml_caminho, (LENGTH(xml_raw)=0) vazio "
            "  FROM nfe_importacoes "
            " WHERE cliente_id=%s AND tipo=%s AND data_emissao>=%s AND data_emissao<%s",
            (chave[0], chave[1], ini, fim), fetch=True)
        if rows is None:          # consulta cortada pelo teto: NÃO é "zero notas"
            logger.warning('[auditoria] pasta %s: consulta das notas não voltou; '
                           'refaço na próxima volta.', rotulo)
            parciais['pastas'] -= 1
            continue

        for n in rows:
            parciais['notas'] += 1
            # O caminho GRAVADO é o candidato mais forte: é nele que o expurgo
            # confiou para apagar. Os outros dois cobrem quem foi arquivado
            # antes de o caminho passar a ser gravado.
            cands = []
            if n.get('xml_caminho'):
                cands.append(n['xml_caminho'].rsplit('/', 1)[-1].lower())
            cands.append(('%s.xml' % n['chave_acesso']).lower())
            if n.get('nome_arquivo'):
                cands.append(n['nome_arquivo'].strip().lower())
            tam = next((nomes[c] for c in cands if c in nomes), None)
            expurgada = bool(n['vazio'])
            if tam is None:
                if expurgada:
                    parciais['PERDIDA'] += 1
                    if len(parciais['perdidas_chaves']) < AMOSTRA:
                        parciais['perdidas_chaves'].append(n['chave_acesso'])
                    logger.error('[auditoria] ALARME: nota EXPURGADA sem arquivo no Dropbox. '
                                 'chave=%s pasta=%s caminho gravado=%s',
                                 n['chave_acesso'], rotulo, n.get('xml_caminho'))
                else:
                    parciais['orfa'] += 1
            elif tam == 0:
                parciais['vazia'] += 1
                if len(parciais['vazias_chaves']) < AMOSTRA:
                    parciais['vazias_chaves'].append(n['chave_acesso'])
                logger.error('[auditoria] ALARME: arquivo de 0 byte no Dropbox. chave=%s pasta=%s',
                             n['chave_acesso'], rotulo)
            else:
                parciais['ok' if expurgada else 'no_banco'] += 1

    if ultima is None:
        return r

    acabou = ultima == (grupos[-1]['cliente_id'], grupos[-1]['tipo'],
                        int(grupos[-1]['ano']), int(grupos[-1]['mes']))
    r.update({'pastas_nesta_rodada': feitas, 'ate': list(ultima), 'volta_fechada': acabou})
    r['parciais'] = {k: v for k, v in parciais.items() if not k.endswith('_chaves')
                     and k != 'pastas_sem_lista'}

    if acabou:
        final = dict(parciais)
        final['fechada_em'] = _agora_do_banco()
        final['iniciada_em'] = inicio
        final['veredito'] = ('TUDO NO LUGAR' if not (final['PERDIDA'] or final['vazia'])
                             else 'ALARME')
        _guardar(MARCA_RESULTADO, final)
        _guardar(MARCA_ANDAMENTO, {})        # zera: a próxima só começa no prazo
        nivel = logger.error if final['veredito'] == 'ALARME' else logger.warning
        nivel('[auditoria] VOLTA FECHADA — %s. %s pastas, %s notas: %s expurgadas com '
              'arquivo, %s PERDIDAS, %s de 0 byte, %s órfãs.',
              final['veredito'], final['pastas'], final['notas'], final['ok'],
              final['PERDIDA'], final['vazia'], final['orfa'])
    else:
        _guardar(MARCA_ANDAMENTO, {'pos': list(ultima), 'parciais': parciais,
                                   'inicio': inicio})
    return r


def _agora_do_banco():
    """Hora do BANCO (-03:00), não do container, que roda em UTC."""
    from utils.db_helper import execute_query
    r = execute_query("SELECT DATE_FORMAT(NOW(), '%Y-%m-%d %H:%i:%s') agora",
                      fetch=True, fetch_one=True) or {}
    return r.get('agora')
