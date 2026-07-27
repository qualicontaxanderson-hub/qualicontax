# -*- coding: utf-8 -*-
"""Reprocessa as notas SEFAZ já capturadas para o fluxo NOVO (Opção A).

Contexto: até aqui a captura SEFAZ gravava a nota com uma gravação PRÓPRIA (campos
parciais: sem impostos/cfop/natureza, itens sem vínculo, XML só como arquivo em
EMPRESAS/.../Fiscal). A Opção A fez a captura passar a gravar pelo MESMO core do
Dropbox (parse_nfe_xml + _save_nfe), com todos os campos e no padrão IMPORTADOS.

Este script alinha o que JÁ estava no banco:

  (A) COMPLETAS antigas (origem='SEFAZ', incompleta=0, com xml_caminho):
      lê o nfeProc JÁ ARQUIVADO no Dropbox (xml_caminho) e regrava pelo core novo
      (_importar_nfe_completa). NÃO consulta a SEFAZ → não gasta cota. Enriquece a
      linha (impostos, cfop, natureza, xml_raw, itens com vínculo) e arquiva o XML
      em {Fiscal}/IMPORTADOS/... . Depois disso a linha fica com xml_caminho=NULL
      (o core não usa xml_caminho) — o que a torna idempotente: numa 2ª execução
      ela não é mais selecionada.

  (B) RESUMOS antigos (origem='SEFAZ', incompleta=1): NÃO são resolvidos aqui — a
      completa deles só vem por consChNFe (rede + cota + certificado). O próprio
      fluxo de captura faz isso automaticamente (dfe_captura._retry_pendentes) na
      próxima rodada de cada empresa, respeitando o cooldown do 656. Aqui só CONTA.

Segurança: todo DELETE é guardado por origem='SEFAZ' — NUNCA toca linha manual
(UPLOAD/DROPBOX). O DRY-RUN (default) só lê e conta.

Uso (no Console do Railway, após o deploy do código):
    PYTHONPATH=/app python migrations/reprocessar_sefaz_fluxo_dropbox.py            # DRY-RUN
    PYTHONPATH=/app python migrations/reprocessar_sefaz_fluxo_dropbox.py --apply    # aplica
"""
import sys

from utils.db_helper import execute_query
from utils import dropbox_sync
from utils.integrations.dfe_captura import _importar_nfe_completa


# Completas antigas do fluxo próprio: origem SEFAZ, completas (incompleta=0) e ainda
# apontando o XML por arquivo (xml_caminho preenchido = não passou pelo core novo).
SQL_COMPLETAS = """
    SELECT n.id, n.chave_acesso, n.xml_caminho, n.cliente_id,
           c.nome_razao_social AS razao, c.numero_cliente AS numero
    FROM nfe_importacoes n
    JOIN clientes c ON c.id = n.cliente_id
    WHERE n.origem = 'SEFAZ' AND n.tipo = 'entrada'
      AND COALESCE(n.incompleta, 0) = 0
      AND n.xml_caminho IS NOT NULL AND n.xml_caminho <> ''
    ORDER BY n.id
"""

SQL_CONTA_RESUMOS = """
    SELECT COUNT(*) AS c FROM nfe_importacoes
    WHERE origem = 'SEFAZ' AND tipo = 'entrada' AND incompleta = 1
"""

# DELETE guardado por origem='SEFAZ' (nunca manual). Remove a completa antiga +
# itens para o core reinserir a versão rica.
SQL_DEL_ITENS = (
    "DELETE it FROM nfe_itens it JOIN nfe_importacoes n ON n.id = it.nfe_id "
    "WHERE n.chave_acesso = %s AND n.tipo = 'entrada' AND n.origem = 'SEFAZ'"
)
SQL_DEL_NOTA = (
    "DELETE FROM nfe_importacoes "
    "WHERE chave_acesso = %s AND tipo = 'entrada' AND origem = 'SEFAZ'"
)


def dry_run():
    completas = execute_query(SQL_COMPLETAS, fetch=True) or []
    resumos = (execute_query(SQL_CONTA_RESUMOS, fetch=True, fetch_one=True) or {}).get('c', 0)
    sem_dbx = 0
    if dropbox_sync.is_configured():
        # Amostra a existência do XML de até 200 completas para estimar quantas o
        # --apply conseguiria reprocessar (o resto vira "XML não encontrado").
        for d in completas[:200]:
            if not dropbox_sync._service.download_file(d['xml_caminho']):
                sem_dbx += 1
    print("=" * 66)
    print("REPROCESSAR SEFAZ -> fluxo Dropbox    (DRY-RUN — nada gravado)")
    print("=" * 66)
    print(f"  (A) COMPLETAS a reprocessar (origem=SEFAZ, com XML arquivado): {len(completas)}")
    print(f"        - do XML já arquivado, sem consultar a SEFAZ (0 cota)")
    if dropbox_sync.is_configured():
        print(f"        - amostra (até 200): {sem_dbx} com XML NÃO encontrado no Dropbox")
    else:
        print("        - Dropbox não configurado aqui: não deu p/ amostrar os XML")
    print(f"  (B) RESUMOS pendentes (origem=SEFAZ, incompleta=1)..........: {resumos}")
    print("        - NÃO reprocessados aqui; a captura busca a completa por chave")
    print("          (consChNFe) automaticamente no próximo ciclo de cada empresa.")
    print("=" * 66)
    print("  Para APLICAR o item (A): rode de novo com  --apply")


def apply():
    if not dropbox_sync.is_configured():
        print("ABORTADO: Dropbox não configurado — o --apply precisa baixar os XML "
              "arquivados (xml_caminho). Defina as credenciais e rode de novo.")
        return 1

    completas = execute_query(SQL_COMPLETAS, fetch=True) or []
    svc = dropbox_sync._service
    ok = sem_xml = falhou = 0
    total_itens = 0
    for d in completas:
        chave = d['chave_acesso']
        xml_bytes = svc.download_file(d['xml_caminho'])
        if not xml_bytes:
            sem_xml += 1
            print(f"  ! {chave}: XML não encontrado em {d['xml_caminho']} — pulado")
            continue
        try:
            # Remove a completa antiga (só SEFAZ) e regrava pelo core novo, que
            # também arquiva em IMPORTADOS. empresa = dono do certificado (dest).
            execute_query(SQL_DEL_ITENS, (chave,), fetch=False)
            execute_query(SQL_DEL_NOTA, (chave,), fetch=False)
            empresa = {'cliente_id': d['cliente_id'],
                       'numero': (d.get('numero') or None),
                       'razao': d['razao']}
            n_itens = _importar_nfe_completa(empresa, xml_bytes)
            total_itens += n_itens
            ok += 1
        except Exception as exc:
            falhou += 1
            print(f"  ! {chave}: falha ao reprocessar — {exc}")

    resumos = (execute_query(SQL_CONTA_RESUMOS, fetch=True, fetch_one=True) or {}).get('c', 0)
    print("=" * 66)
    print("REPROCESSAMENTO APLICADO  (completas SEFAZ -> fluxo Dropbox)")
    print("=" * 66)
    print(f"  completas reprocessadas............: {ok}")
    print(f"  itens gravados (nfe_itens).........: {total_itens}")
    print(f"  XML não encontrado (pulados).......: {sem_xml}")
    print(f"  falhas.............................: {falhou}")
    print(f"  resumos pendentes (via captura)....: {resumos}  (resolvidos no próximo ciclo)")
    return 0


if __name__ == '__main__':
    if '--apply' in sys.argv:
        sys.exit(apply())
    dry_run()
