# -*- coding: utf-8 -*-
"""ETAPA 3 — backfill do VALOR COMERCIAL nos itens de ENTRADA já importados.

O que faz
---------
Lê o ``xml_raw`` das notas de ENTRADA e recalcula, item a item, o custo comercial
que a Etapa 2 passou a gravar na importação:

    custo_total_item     = vProd + vICMSST + vIPI + vFrete + vSeg + vOutro - vDesc
    valor_unit_comercial = custo_total_item / qCom

IMPORTA a fórmula de ``utils.nfe_parser`` (``_valor_comercial`` e ``_dec``) em vez
de copiá-la. Isso é deliberado: fórmula duplicada é fórmula que diverge — se a
regra mudar amanhã, o backfill tem que mudar junto, sem ninguém precisar lembrar.

Escopo
------
SÓ ``tipo='entrada'``. Saídas e CT-e ficam de fora (decisão de projeto).
SÓ linhas com os dois campos NULL — nunca sobrescreve o que a importação já gravou.

Casamento item↔XML
------------------
Por ``num_item`` (o ``nItem`` do XML), que é único dentro da nota. Como guarda, o
``codigo_produto`` do banco tem que bater com o ``cProd`` do XML; se não bater, a
linha é REJEITADA em vez de receber o valor do item errado.

Uso
---
    python scripts/backfill_valor_comercial.py --dry-run   # SÓ IMPRIME
    python scripts/backfill_valor_comercial.py --apply     # (etapa seguinte)
"""
import argparse
import sys
from collections import Counter

import mysql.connector
from config import Config
from utils.nfe_parser import parse_nfe_xml

LOTE_NOTAS = 200          # notas lidas por vez (o xml_raw é pesado: ~9 KB cada)
LOTE_UPDATE = 500         # itens por transação no --apply
AMOSTRA = 10
# Caso de CONTROLE, sempre exibido: NF 1002 da INTEGRACAO COMBUSTIVEIS, onde o
# vUnCom é 2,6280 mas o custo real é 8.460,00 / 3.000 = 2,8200. Se esta linha
# sair diferente disso, a fórmula regrediu.
NFE_CONTROLE = 62230
MAX_DIVERGENTES = 5       # exemplos de codigo_produto divergente a listar

SQL_UPDATE = (
    "UPDATE nfe_itens "
    "   SET custo_total_item = %s, valor_unit_comercial = %s "
    " WHERE id = %s AND custo_total_item IS NULL AND valor_unit_comercial IS NULL"
)


def conectar():
    return mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT, autocommit=False,
    )


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true', help='só imprime, não escreve')
    g.add_argument('--apply', action='store_true', help='grava (só depois do ok no dry-run)')
    args = ap.parse_args()
    escrever = bool(args.apply)

    cn = conectar()
    cur = cn.cursor(dictionary=True)

    # ---- panorama antes de qualquer coisa ----
    cur.execute("""SELECT COUNT(*) itens,
                          COUNT(DISTINCT n.id) notas,
                          SUM(i.custo_total_item IS NULL
                              AND i.valor_unit_comercial IS NULL) pendentes,
                          SUM(i.custo_total_item IS NOT NULL) ja_preenchidos,
                          SUM(COALESCE(n.xml_raw,'') = '') sem_xml
                     FROM nfe_itens i JOIN nfe_importacoes n ON n.id = i.nfe_id
                    WHERE n.tipo = 'entrada'""")
    pano = cur.fetchone()
    print(f'MODO: {"APLICAR (escreve)" if escrever else "DRY-RUN (nao escreve nada)"}\n')
    print('PANORAMA — itens de ENTRADA')
    print(f'   itens ....................... {pano["itens"]}')
    print(f'   em {pano["notas"]} notas')
    print(f'   ja preenchidos (import novo)  {int(pano["ja_preenchidos"] or 0)}')
    print(f'   PENDENTES (os dois NULL) .... {int(pano["pendentes"] or 0)}')
    print(f'   itens cuja nota nao tem xml_raw {int(pano["sem_xml"] or 0)}\n')

    # ---- notas de entrada que têm item pendente ----
    cur.execute("""SELECT DISTINCT n.id
                     FROM nfe_importacoes n JOIN nfe_itens i ON i.nfe_id = n.id
                    WHERE n.tipo = 'entrada'
                      AND i.custo_total_item IS NULL
                      AND i.valor_unit_comercial IS NULL
                    ORDER BY n.id""")
    nota_ids = [r['id'] for r in cur.fetchall()]
    print(f'notas a processar: {len(nota_ids)}\n')

    cont = Counter()
    amostra = []
    amostra_controle = []     # NF 1002, sempre exibida
    divergentes = []          # exemplos de codigo_produto que não bate
    pendentes_update = []
    upd = cn.cursor()

    for ini in range(0, len(nota_ids), LOTE_NOTAS):
        bloco = nota_ids[ini:ini + LOTE_NOTAS]
        ph = ','.join(['%s'] * len(bloco))
        cur.execute(f"SELECT id, num_nota, emit_nome, xml_raw "
                    f"  FROM nfe_importacoes WHERE id IN ({ph})", bloco)
        notas = {r['id']: r for r in cur.fetchall()}
        cur.execute(f"""SELECT id, nfe_id, num_item, codigo_produto, descricao,
                               quantidade, valor_unitario, valor_total
                          FROM nfe_itens
                         WHERE nfe_id IN ({ph})
                           AND custo_total_item IS NULL
                           AND valor_unit_comercial IS NULL
                         ORDER BY nfe_id, num_item""", bloco)
        itens_db = cur.fetchall()

        por_nota = {}
        for it in itens_db:
            por_nota.setdefault(it['nfe_id'], []).append(it)

        for nfe_id, itens in por_nota.items():
            nota = notas.get(nfe_id) or {}
            xml = nota.get('xml_raw') or ''
            if not xml:
                cont['sem_xml'] += len(itens)
                continue
            try:
                parsed = parse_nfe_xml(xml)
            except Exception as exc:
                cont['xml_invalido'] += len(itens)
                if len(amostra) < AMOSTRA:
                    amostra.append(('XML INVALIDO', nota.get('num_nota'), str(exc)[:50]))
                continue

            do_xml = {i['num_item']: i for i in parsed.get('itens', [])}
            for it in itens:
                px = do_xml.get(it['num_item'])
                if px is None:
                    cont['item_nao_achado_no_xml'] += 1
                    continue
                # Guarda: o código do produto tem que bater — senão seria o
                # valor de OUTRO item entrando nesta linha.
                if (it['codigo_produto'] or '') != (px['codigo_produto'] or ''):
                    cont['codigo_divergente'] += 1
                    if len(divergentes) < MAX_DIVERGENTES:
                        divergentes.append((
                            nota.get('num_nota'), it['num_item'],
                            it['codigo_produto'], px['codigo_produto'],
                            (it['descricao'] or '')[:28], (px['descricao'] or '')[:28],
                        ))
                    continue
                custo, unit = px['custo_total_item'], px['valor_unit_comercial']
                if custo is None or unit is None:
                    cont['sem_qcom'] += 1     # qCom 0/ausente -> fica NULL de propósito
                    continue
                cont['calculaveis'] += 1
                pendentes_update.append((custo, unit, it['id']))
                linha = (nota.get('num_nota'), (it['descricao'] or '')[:30],
                         it['quantidade'], it['valor_unitario'], it['valor_total'],
                         custo, unit)
                if nfe_id == NFE_CONTROLE:
                    amostra_controle.append(linha)
                elif len(amostra) < AMOSTRA:
                    amostra.append(linha)

        if escrever and len(pendentes_update) >= LOTE_UPDATE:
            upd.executemany(SQL_UPDATE, pendentes_update)
            cn.commit()
            cont['gravados'] += len(pendentes_update)
            pendentes_update.clear()
            print(f'   ... {cont["gravados"]} gravados', flush=True)

    if escrever and pendentes_update:
        upd.executemany(SQL_UPDATE, pendentes_update)
        cn.commit()
        cont['gravados'] += len(pendentes_update)

    # ---- relatório ----
    print('=' * 78)
    print('RELATORIO — Etapa 3 ' + ('(APLICADO)' if escrever else '(DRY-RUN)'))
    print('=' * 78)
    print(f'   CALCULAVEIS (receberiam valor) .... {cont["calculaveis"]}')
    print(f'   ficariam NULL, por motivo:')
    print(f'      qCom 0/ausente no XML .......... {cont["sem_qcom"]}')
    print(f'      nota sem xml_raw ............... {cont["sem_xml"]}')
    print(f'      xml_raw invalido ............... {cont["xml_invalido"]}')
    print(f'      num_item nao existe no XML ..... {cont["item_nao_achado_no_xml"]}')
    print(f'      codigo_produto divergente ...... {cont["codigo_divergente"]}')
    if escrever:
        print(f'   GRAVADOS .......................... {cont["gravados"]}')
        cur.execute("""SELECT SUM(i.custo_total_item IS NULL) ainda_null
                         FROM nfe_itens i JOIN nfe_importacoes n ON n.id = i.nfe_id
                        WHERE n.tipo='entrada'""")
        print(f'   itens de entrada ainda NULL ....... {int(cur.fetchone()["ainda_null"] or 0)}')

    cab = (f'   {"NF":<10}{"descricao":<32}{"qtd":>12}{"vUnCom":>14}'
           f'{"vProd":>12}{"custo":>12}{"COMERCIAL":>12}')

    def _linha(a):
        nf, desc, qtd, vun, vprod, custo, unit = a
        print(f'   {str(nf):<10}{desc:<32}{str(qtd):>12}{str(vun):>14}'
              f'{str(vprod):>12}{str(custo):>12}{str(unit):>12}')

    if amostra_controle:
        print(f'\nCASO DE CONTROLE — NF 1002 INTEGRACAO (esperado 8460.00 / 2.8200)')
        print(cab)
        for a in amostra_controle:
            _linha(a)
            ok = str(a[5]) == '8460.00' and str(a[6]) == '2.8200'
            print(f'   >>> CONFERE: {ok}')
    else:
        print('\nCASO DE CONTROLE — NF 1002 nao apareceu entre os pendentes '
              '(ja preenchida? fora do escopo?)')

    print(f'\nAMOSTRA — antes/depois ({len(amostra)} linhas)')
    print(cab)
    for a in amostra:
        if a and a[0] == 'XML INVALIDO':
            print(f'   NF {a[1]}: XML invalido — {a[2]}')
            continue
        _linha(a)

    if divergentes:
        print(f'\nREJEITADOS por codigo_produto divergente '
              f'(ate {MAX_DIVERGENTES} exemplos de {cont["codigo_divergente"]}):')
        for nf, ni, cod_db, cod_xml, desc_db, desc_xml in divergentes:
            print(f'   NF {nf} · item {ni}')
            print(f'      banco: codigo={cod_db!r:<16} descricao={desc_db!r}')
            print(f'      XML  : cProd ={cod_xml!r:<16} xProd    ={desc_xml!r}')
    elif cont['codigo_divergente']:
        print(f'\n   {cont["codigo_divergente"]} rejeitados por codigo divergente '
              f'(sem exemplos capturados)')

    if not escrever:
        print('\n   (dry-run: NENHUM UPDATE foi executado)')

    upd.close(); cur.close(); cn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
