# -*- coding: utf-8 -*-
"""FASE 1 / Parte 2 — validação: o DACTE é gerado a partir do ``xml_raw`` do BANCO.

SÓ LEITURA. Não escreve no banco nem no Dropbox; o PDF fica em memória.

Prova o que interessa depois do backfill: o PDF do CT-e passa a ser gerado SEM
tocar no Dropbox. Usa a mesma lib e a mesma chamada da rota
``/escrita-fiscal/cte/<id>/pdf`` (``brazilfiscalreport.dacte.Dacte``).

As amostras saem de empresas e meses DIFERENTES e, por padrão, das linhas que
foram resolvidas pelo degrau 2 (o caso majoritário do backfill) — testar o degrau
1 não provaria nada, porque aquele caminho nunca esteve quebrado.
"""
import csv
import os
import sys

import mysql.connector
from config import Config

CSV_TRILHA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          'backfill_cte_xml_raw.csv')
QUANTAS = 3


def main():
    ids_degrau2 = [int(r['id']) for r in
                   csv.DictReader(open(CSV_TRILHA, encoding='utf-8'), delimiter=';')
                   if r['veredito'] == 'OK:2-derivado']
    if not ids_degrau2:
        print('CSV sem linhas do degrau 2 — rode o backfill antes.')
        return 1

    cn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT,
    )
    cur = cn.cursor(dictionary=True)

    # Uma amostra por empresa/mês distintos: pega a 1ª linha de cada combinação.
    ph = ','.join(['%s'] * len(ids_degrau2))
    cur.execute(f"""SELECT t.id, t.chave_acesso, t.num_cte, t.serie, t.modelo,
                           t.data_emissao, t.xml_caminho, LENGTH(t.xml_raw) bytes_xml,
                           c.numero_cliente, c.nome_razao_social
                      FROM cte_documentos t JOIN clientes c ON c.id = t.cliente_id
                     WHERE t.id IN ({ph}) AND t.modelo = '57'
                       AND t.xml_raw IS NOT NULL AND t.xml_raw <> ''
                     ORDER BY c.numero_cliente, t.data_emissao""", ids_degrau2)
    todos = cur.fetchall()

    escolhidas, vistos = [], set()
    for r in todos:
        chave_grupo = (r['numero_cliente'], str(r['data_emissao'])[:7])
        if chave_grupo in vistos:
            continue
        vistos.add(chave_grupo)
        escolhidas.append(r)
        if len(escolhidas) == QUANTAS:
            break

    from brazilfiscalreport.dacte import Dacte

    print('=' * 78)
    print('TESTE — DACTE gerado a partir do xml_raw do BANCO (sem tocar no Dropbox)')
    print('=' * 78)
    falhas = 0
    for i, r in enumerate(escolhidas, 1):
        cur.execute("SELECT xml_raw FROM cte_documentos WHERE id = %s", (r['id'],))
        xml = cur.fetchone()['xml_raw']          # <- vem do BANCO, não do arquivo
        print(f"\n[{i}] cte_id={r['id']}  CT-e {r['num_cte']}/{r['serie']}  "
              f"emissao {r['data_emissao']}")
        print(f"    empresa : {r['numero_cliente']} - {r['nome_razao_social'][:44]}")
        print(f"    chave   : {r['chave_acesso']}")
        print(f"    xml_raw : {len(xml)} bytes lidos do banco")
        try:
            pdf = bytes(Dacte(xml=xml).output())
        except Exception as exc:
            falhas += 1
            print(f"    RESULTADO: FALHOU ao gerar o DACTE — {exc}")
            continue
        assinatura_ok = pdf[:4] == b'%PDF'
        if not assinatura_ok or len(pdf) < 10_000:
            falhas += 1
            print(f"    RESULTADO: SUSPEITO — {len(pdf)} bytes, "
                  f"assinatura={'%PDF' if assinatura_ok else pdf[:4]!r}")
        else:
            print(f"    RESULTADO: OK — PDF de {len(pdf):,} bytes, assinatura %PDF")

    print('\n' + '=' * 78)
    print(f'   amostras testadas: {len(escolhidas)} | falhas: {falhas}')
    cur.close()
    cn.close()
    return 1 if falhas else 0


if __name__ == '__main__':
    sys.exit(main())
