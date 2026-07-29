# -*- coding: utf-8 -*-
"""Retroativo (Escopo A): cria a linha ``tipo='saida'`` das NF-e mod. 55 ENTRE
clientes cadastrados que a captura SEFAZ já baixou como entrada e antes descartava.

Contexto: o distDFeInt entrega a nota ao DESTINATÁRIO. Quando o EMITENTE também é
cliente, a venda dele (saída) já está no XML capturado, mas o motor só gravava a
entrada do comprador. A partir de agora o motor grava as duas (ver
``dfe_captura._importar_nfe_completa``); esta migração faz o mesmo para o histórico
JÁ capturado, re-parseando o ``xml_raw`` guardado.

ADITIVO e IDEMPOTENTE:
  * só INSERE linhas ``tipo='saida'`` (via ``_save_nfe``, que devolve 'dup' se a
    (chave, 'saida') já existir) — re-rodar NÃO duplica;
  * NÃO altera nenhuma linha de entrada;
  * pula candidata sem ``xml_raw`` (não dá para reconstruir os itens) e loga.

ROLLBACK (as saídas criadas aqui têm tipo='saida' AND origem='SEFAZ'):
    DELETE it FROM nfe_itens it JOIN nfe_importacoes n ON n.id = it.nfe_id
     WHERE n.tipo='saida' AND n.origem='SEFAZ';
    DELETE FROM nfe_importacoes WHERE tipo='saida' AND origem='SEFAZ';

Uso:
    python migrations/backfill_saidas_sefaz.py            # DRY-RUN (não grava nada)
    python migrations/backfill_saidas_sefaz.py --apply    # grava as saídas
"""
import sys

from utils.db_helper import execute_query
from utils.nfe_parser import parse_nfe_xml
from utils.nfe_import import _save_nfe

# Normalização de dígitos em SQL (igual à usada no resto do sistema).
_DIG = "REPLACE(REPLACE(REPLACE(REPLACE({c},'.',''),'/',''),'-',''),' ','')"

_ROLLBACK = (
    "Rollback:\n"
    "  DELETE it FROM nfe_itens it JOIN nfe_importacoes n ON n.id = it.nfe_id\n"
    "   WHERE n.tipo='saida' AND n.origem='SEFAZ';\n"
    "  DELETE FROM nfe_importacoes WHERE tipo='saida' AND origem='SEFAZ';"
)


def _candidatas():
    """Entradas SEFAZ cujo emitente é um cliente cadastrado diferente do dono."""
    demit = _DIG.format(c='n.emit_cnpj')
    dcli = _DIG.format(c='c.cpf_cnpj')
    return execute_query(
        "SELECT n.id, n.chave_acesso, n.num_nota, n.valor_total, n.xml_raw, "
        "       c.id AS emit_cli, c.numero_cliente AS emit_num, "
        "       c.nome_razao_social AS emit_nome "
        "FROM nfe_importacoes n "
        f"JOIN clientes c ON {demit} = {dcli} "
        "WHERE n.origem='SEFAZ' AND n.tipo='entrada' AND c.id <> n.cliente_id "
        "ORDER BY n.id",
        fetch=True,
    ) or []


def main():
    aplicar = '--apply' in sys.argv
    rows = _candidatas()
    print("=" * 78)
    print(f"Candidatas (entradas SEFAZ cujo EMITENTE e cliente != dono): {len(rows)}")
    print("=" * 78)

    criar = criadas = dup = semxml = erro = 0
    for r in rows:
        chave = r['chave_acesso'] or ''
        ja = execute_query(
            "SELECT id FROM nfe_importacoes WHERE chave_acesso=%s AND tipo='saida'",
            (chave,), fetch=True, fetch_one=True,
        )
        tem_xml = bool(r.get('xml_raw'))
        if ja:
            estado = 'JA EXISTE (idempotente)'
        elif not tem_xml:
            estado = 'SEM xml_raw -> PULA'
        else:
            estado = 'CRIAR' if aplicar else 'CRIARIA'
        print(f"  nf={r['num_nota']:>8}  R${r['valor_total']:>10}  emit=#{r['emit_num']} "
              f"{(r['emit_nome'] or '')[:26]:26}  chave=...{chave[-6:]}  -> {estado}")

        if ja:
            dup += 1
            continue
        if not tem_xml:
            semxml += 1
            continue
        if not aplicar:
            criar += 1
            continue
        try:
            parsed = parse_nfe_xml(r['xml_raw'])
            res = _save_nfe(parsed, f"{chave}.xml", 'SEFAZ', r['xml_raw'],
                            cliente_id=r['emit_cli'], tipo='saida')
            if res == 'ok':
                criadas += 1
            else:                       # 'dup' — corrida/idempotência
                dup += 1
        except Exception as exc:        # parse/gravação — não aborta o lote
            erro += 1
            print(f"      ! ERRO ao gravar saida da chave ...{chave[-6:]}: {exc}")

    print("-" * 78)
    modo = 'APPLY' if aplicar else 'DRY-RUN'
    if aplicar:
        print(f"[{modo}] criadas={criadas}  ja_existiam={dup}  sem_xml={semxml}  erro={erro}")
    else:
        print(f"[{modo}] criaria={criar}  ja_existiam={dup}  sem_xml={semxml}  (NADA gravado)")
        print("Rode novamente com --apply para gravar as saidas.")
    print(_ROLLBACK)


if __name__ == '__main__':
    main()
