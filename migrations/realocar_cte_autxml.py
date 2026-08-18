# -*- coding: utf-8 -*-
"""Devolve ao cliente certo os CT-e que chegaram pelo autXML do escritório.

O QUE ACONTECEU
---------------
O `autXML` do CT-e traz o CNPJ/CPF do contador, e é assim que o certificado do
escritório puxa da SEFAZ o frete que o cliente emitiu — é o desenho pretendido, e
funciona. O que faltava era o passo seguinte: a captura de CT-e grava SEMPRE em
``empresa['cliente_id']``, o dono do certificado que consultou. Então o CT-e da
HPA e da Brilho ia parar na Conferência da Qualicontax, e o cliente ficava sem.

A captura de NF-e já resolve isso e até nomeia o problema no próprio código:
"se o dono é apenas AUTORIZADO a baixar o XML (autXML) e não é o destinatário, a
nota NÃO vira entrada dele — era isso que criava a 'entrada fantasma da
Qualicontax'". Foi por isso que as saídas da KET migraram sozinhas para a KET
assim que ela foi cadastrada. O CT-e nunca ganhou a lógica equivalente.

A REGRA, definida pelo Anderson em 18/08/2026
---------------------------------------------
  1. escritório é PARTE do CT-e (tomador/destinatário/remetente) -> fica com ele
  2. chegou por autXML -> procura, entre as partes, quem é cliente cadastrado
       achou   -> a linha é do cliente, com o papel que ele tem no documento
       não achou -> FICA no escritório

O item 3 diverge da NF-e de propósito: ela descarta o autXML de terceiro puro;
aqui o frete que o escritório foi autorizado a ver continua interessando a ele.

ESTA MIGRAÇÃO É SÓ O PASSADO. Corrigir daqui para frente é mexer no
``cte_captura``, e é outro commit.

    python migrations/realocar_cte_autxml.py            # dry-run
    python migrations/realocar_cte_autxml.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

_D = "REPLACE(REPLACE(REPLACE(c.cpf_cnpj,'.',''),'/',''),'-','')"

# Candidatas: o dono do certificado NÃO é parte ('outro') e o EMITENTE do CT-e é
# cliente nosso. Medido em 18/08/2026: das 142 linhas com papel 'outro', 127 caem
# aqui e em nenhuma delas tomador/remetente/destinatário era cliente — por isso a
# busca é pelo emitente. Se um dia isso mudar, o SELECT ganha mais um OR.
SQL_CANDIDATAS = f"""
SELECT t.id, t.chave_acesso, t.num_cte, t.serie, t.data_emissao, t.valor_frete,
       t.cliente_id AS de_id, dono.numero_cliente AS de_num, dono.nome_razao_social AS de_nome,
       c.id AS para_id, c.numero_cliente AS para_num, c.nome_razao_social AS para_nome,
       EXISTS(SELECT 1 FROM cte_documentos x
               WHERE x.chave_acesso = t.chave_acesso AND x.cliente_id = c.id) AS ja_tem
  FROM cte_documentos t
  JOIN clientes c    ON {_D} = t.emit_cnpj
  LEFT JOIN clientes dono ON dono.id = t.cliente_id
 WHERE t.papel_cliente = 'outro'
 ORDER BY c.numero_cliente, t.data_emissao
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true', help='executa; sem isto é dry-run')
    args = ap.parse_args()

    linhas = execute_query(SQL_CANDIDATAS, fetch=True)
    if linhas is None:
        print('ERRO: a consulta falhou. Nada foi alterado.')
        return 1

    mover = [r for r in linhas if not r['ja_tem']]
    apagar = [r for r in linhas if r['ja_tem']]

    fica = execute_query(f"""
        SELECT COUNT(*) n FROM cte_documentos t
         WHERE t.papel_cliente = 'outro'
           AND NOT EXISTS (SELECT 1 FROM clientes c WHERE {_D} = t.emit_cnpj)
    """, fetch=True, fetch_one=True)

    print(f'{len(linhas)} CT-e chegaram por autXML e pertencem a cliente cadastrado')
    print()

    porcli = {}
    for r in mover:
        porcli.setdefault((r['para_num'], r['para_nome']), []).append(r)
    print(f'A MOVER ({len(mover)}):')
    for (num, nome), rs in sorted(porcli.items(), key=lambda x: -len(x[1])):
        de = rs[0]['de_num']
        print(f'   cliente {num} · {str(nome)[:40]:<40} <- vinha de {de}   {len(rs)} CT-e')
        for r in rs[:3]:
            print(f'        nº {r["num_cte"]}/{r["serie"]}  {r["data_emissao"]}  '
                  f'R$ {float(r["valor_frete"] or 0):,.2f}')
        if len(rs) > 3:
            print(f'        ... e mais {len(rs) - 3}')
    print()

    print(f'A APAGAR ({len(apagar)}) — o cliente JÁ TEM o CT-e (veio pelo Q-Colabore/upload),')
    print(f'   e a chave única é (chave_acesso, cliente_id), então mover daria conflito:')
    for r in apagar:
        print(f'   nº {r["num_cte"]}/{r["serie"]}  {r["data_emissao"]}  '
              f'{str(r["de_nome"])[:28]} -> já existe em {r["para_num"]}')
    print()
    print(f'FICAM COMO ESTÃO ({fica["n"]}) — autXML de transportadora que não é cliente.')
    print('   Regra do Anderson: na ausência de cadastro, fica no escritório.')
    print()

    if not args.apply:
        print('DRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        return 0

    n_upd = n_del = 0
    for r in mover:
        # papel_cliente vira 'emitente': o cliente É a transportadora que emitiu.
        ok = execute_query(
            'UPDATE cte_documentos SET cliente_id = %s, papel_cliente = %s WHERE id = %s',
            (r['para_id'], 'emitente', r['id']), fetch=False)
        if ok is None:
            print(f'   FALHOU ao mover id={r["id"]}; sigo para os próximos.')
        else:
            n_upd += 1
    for r in apagar:
        ok = execute_query('DELETE FROM cte_documentos WHERE id = %s', (r['id'],), fetch=False)
        if ok is None:
            print(f'   FALHOU ao apagar id={r["id"]}.')
        else:
            n_del += 1

    print(f'OK: {n_upd} movido(s), {n_del} apagado(s).')
    rest = execute_query("SELECT COUNT(*) n FROM cte_documentos WHERE papel_cliente='outro'",
                         fetch=True, fetch_one=True)
    print(f"Sobraram {rest['n']} linha(s) com papel 'outro' — devem ser as {fica['n']} de terceiro puro.")
    print()
    print('FALTA A CAPTURA: daqui para frente o cte_captura ainda grava no dono do')
    print('certificado. Sem isso, os próximos CT-e voltam a cair no escritório.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
