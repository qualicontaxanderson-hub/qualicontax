# -*- coding: utf-8 -*-
"""FASE 1 / Parte 5 — reponta cte_documentos.xml_caminho para o local REAL.

Contexto
--------
Os XML de CT-e foram movidos de ``Fiscal/IMPORTADOS/{ano}/{empresa}/{mm.aaaa}``
para ``EMPRESAS/{empresa}/Fiscal/CTE/{ano}/{mm}`` e o banco nunca foi repontado:
4.542 linhas guardam um caminho que não existe mais.

Depois da Parte 2 (xml_raw preenchido em 100% dos CT-e) isso é REDUNDÂNCIA — o
olhinho/DACTE/zip já não passam pelo Dropbox. É exatamente por isso que a operação
é segura: se ela falhasse inteira, nada na tela quebraria. Mas um ponteiro errado
guardado no banco é dívida que cobra juros depois, então corrigimos.

Segurança
---------
1. O backup (cte_path_backup) é gravado e COMMITADO ANTES de qualquer UPDATE.
2. O UPDATE só toca a linha se o xml_caminho ATUAL ainda for exatamente o que foi
   registrado como antigo — se a captura alterou a linha nesse meio tempo, aquela
   linha é pulada em vez de sobrescrita.
3. atualizado_em é preservado: repontar ponteiro não é atualizar o documento.
4. Rollback exato a partir do backup (ver --rollback-sql).

Uso
---
    python scripts/repontar_cte_xml_caminho.py --dry-run       # não escreve
    python scripts/repontar_cte_xml_caminho.py --apply         # backup + update
    python scripts/repontar_cte_xml_caminho.py --rollback-sql  # imprime o SQL de volta
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

import mysql.connector
from config import Config

RAIZ_LOCAL = r'C:\Users\User\Dropbox'
LOTE = 200
MOTIVO = 'reorganizacao Dropbox: Fiscal/IMPORTADOS -> EMPRESAS/{empresa}/Fiscal/CTE'

DDL_BACKUP = """
CREATE TABLE IF NOT EXISTS cte_path_backup (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    cte_id         INT           NOT NULL,
    cliente_id     INT           NULL,
    caminho_antigo VARCHAR(300)  NOT NULL,
    caminho_novo   VARCHAR(300)  NOT NULL,
    motivo         VARCHAR(160)  NOT NULL,
    criado_em      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_ctepathbkp_cte (cte_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""
# Sem FK para cte_documentos de propósito: uma trilha de auditoria não pode
# desaparecer junto com a linha que ela documenta (o mesmo critério do
# dfe_certificados_path_backup, que sobreviveu à migração dos certificados).

SQL_INSERT_BACKUP = (
    "INSERT INTO cte_path_backup "
    "(cte_id, cliente_id, caminho_antigo, caminho_novo, motivo) "
    "VALUES (%s, %s, %s, %s, %s)"
)

SQL_UPDATE = (
    "UPDATE cte_documentos "
    "   SET xml_caminho = %s, atualizado_em = atualizado_em "
    " WHERE id = %s AND xml_caminho = %s"
)

SQL_ROLLBACK = """-- ROLLBACK da Parte 5 — devolve cada linha ao caminho anterior.
-- Só age onde o valor atual ainda é o que esta operação gravou.
UPDATE cte_documentos t
  JOIN cte_path_backup b ON b.cte_id = t.id
   SET t.xml_caminho = b.caminho_antigo,
       t.atualizado_em = t.atualizado_em
 WHERE b.motivo = '{motivo}'
   AND t.xml_caminho = b.caminho_novo;

-- Conferência (deve voltar a contar 4.542 linhas apontando para Fiscal/IMPORTADOS):
-- SELECT COUNT(*) FROM cte_documentos WHERE xml_caminho LIKE '%/Fiscal/IMPORTADOS/%';
""".format(motivo=MOTIVO)


def caminho_local(p):
    return os.path.join(RAIZ_LOCAL, p.lstrip('/').replace('/', os.sep))


def derivar(gravado):
    """Fiscal/IMPORTADOS/{ano}/{empresa}/{mm.aaaa}/{arq}
       -> EMPRESAS/{empresa}/Fiscal/CTE/{ano}/{mm}/{arq}."""
    p = gravado.split('/')
    if len(p) < 9 or p[3] != 'Fiscal' or p[4] != 'IMPORTADOS':
        return None
    raiz, ano, empresa, mmaaaa, arq = '/'.join(p[:3]), p[5], p[6], p[7], p[-1]
    if len(mmaaaa) < 2 or not mmaaaa[:2].isdigit():
        return None
    return f'{raiz}/EMPRESAS/{empresa}/Fiscal/CTE/{ano}/{mmaaaa[:2]}/{arq}'


def indexar_empresas():
    base = os.path.join(RAIZ_LOCAL, 'Aplicativos', 'QUALICONTAX', 'EMPRESAS')
    idx = defaultdict(list)
    for dirpath, _d, files in os.walk(base):
        for n in files:
            if n.lower().endswith('.xml'):
                idx[n.lower()].append(os.path.join(dirpath, n))
    return idx


def montar_mapa(cur):
    """[(cte_id, cliente_id, antigo, novo, como)] só das linhas QUEBRADAS."""
    cur.execute("SELECT id, cliente_id, xml_caminho FROM cte_documentos "
                "WHERE COALESCE(xml_caminho,'') <> '' ORDER BY id")
    linhas = cur.fetchall()
    idx = indexar_empresas()
    mapa, cont = [], Counter()
    for r in linhas:
        antigo = r['xml_caminho']
        if os.path.isfile(caminho_local(antigo)):
            cont['ja_correto'] += 1
            continue
        novo = derivar(antigo)
        if novo and os.path.isfile(caminho_local(novo)):
            cont['derivado'] += 1
            mapa.append((r['id'], r['cliente_id'], antigo, novo, 'derivado'))
            continue
        achados = idx.get(antigo.split('/')[-1].lower())
        if achados:
            novo = achados[0].replace(RAIZ_LOCAL, '').replace(os.sep, '/')
            cont['por_nome'] += 1
            mapa.append((r['id'], r['cliente_id'], antigo, novo, 'por-nome'))
            continue
        cont['sem_solucao'] += 1
    return mapa, cont, len(linhas)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--dry-run', action='store_true')
    g.add_argument('--apply', action='store_true')
    g.add_argument('--rollback-sql', action='store_true')
    args = ap.parse_args()

    if args.rollback_sql:
        print(SQL_ROLLBACK)
        return 0

    cn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD,
        connection_timeout=Config.DB_CONNECT_TIMEOUT, autocommit=False,
    )
    cur = cn.cursor(dictionary=True)
    print(f'MODO: {"APLICAR (escreve)" if args.apply else "DRY-RUN (nao escreve)"}\n')
    mapa, cont, total = montar_mapa(cur)

    print(f'   linhas com xml_caminho ......... {total}')
    print(f'   ja apontam certo (nao mexer) ... {cont["ja_correto"]}')
    print(f'   A REPONTAR ..................... {len(mapa)}')
    print(f'      por derivacao ............... {cont["derivado"]}')
    print(f'      por nome .................... {cont["por_nome"]}')
    print(f'   sem solucao (ficam como estao) . {cont["sem_solucao"]}')

    if not args.apply:
        print('\n   (dry-run: nada foi gravado)')
        cur.close(); cn.close()
        return 0

    if not mapa:
        print('\n   nada a fazer.')
        cur.close(); cn.close()
        return 0

    esc = cn.cursor()
    # ---- 1) tabela de backup + backup COMMITADO antes de qualquer UPDATE ----
    esc.execute(DDL_BACKUP)
    cn.commit()
    esc.executemany(SQL_INSERT_BACKUP,
                    [(i, c, a, n, MOTIVO) for i, c, a, n, _como in mapa])
    cn.commit()
    esc.execute("SELECT COUNT(*) FROM cte_path_backup WHERE motivo = %s", (MOTIVO,))
    n_bkp = esc.fetchone()[0]
    print(f'\n   BACKUP gravado e commitado: {n_bkp} linhas em cte_path_backup')
    if n_bkp < len(mapa):
        print('   ABORTADO: backup incompleto — nenhum UPDATE foi executado.')
        esc.close(); cur.close(); cn.close()
        return 1

    # ---- 2) UPDATE em lotes, só onde o valor atual ainda é o antigo ----
    feitos = pulados = 0
    for ini in range(0, len(mapa), LOTE):
        bloco = mapa[ini:ini + LOTE]
        for cte_id, _cli, antigo, novo, _como in bloco:
            esc.execute(SQL_UPDATE, (novo, cte_id, antigo))
            if esc.rowcount == 1:
                feitos += 1
            else:
                pulados += 1
        cn.commit()
        print(f'   lote {ini // LOTE + 1:>3} · repontados {feitos} · pulados {pulados}', flush=True)

    cur.execute("SELECT COUNT(*) n FROM cte_documentos "
                "WHERE xml_caminho LIKE '%/Fiscal/IMPORTADOS/%'")
    restam = cur.fetchone()['n']
    print('\n' + '=' * 68)
    print('RELATORIO — Parte 5 (APLICADO)')
    print('=' * 68)
    print(f'   backup em cte_path_backup ...... {n_bkp}')
    print(f'   repontados ..................... {feitos}')
    print(f'   pulados (linha mudou no meio) .. {pulados}')
    print(f'   ainda apontando p/ IMPORTADOS .. {restam}')
    esc.close(); cur.close(); cn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
