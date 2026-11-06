# -*- coding: utf-8 -*-
"""Conflitos da migração Acessórias — decisões do Anderson (06/11/2026):

  REGIME divergente (planilha diz um, app diz outro): "apaga o Regime e deixa em A CADASTRAR" → regime_tributario = NULL,
  a pendência "Regime" aparece em A CADASTRAR e a equipe confere.
  RAZÃO SOCIAL divergente: "tem que vir igual está no CNPJ" → nome_razao_social = razão social da Receita (BrasilAPI,
  fallback ReceitaWS). A pasta da empresa no Dropbox chama "{nº - razão}", então a pasta é renomeada JUNTO e os
  caminhos dos XMLs já arquivados são reapontados (mesma mecânica da edição da ficha e da renumeração da parte 1).
  COLISÃO de número: número da planilha do Acessórias que no app pertence a OUTRO CNPJ — só lista, ele decide.

Uso:  python scripts/correcao_conflitos_migracao.py            (confere e lista)
      python scripts/correcao_conflitos_migracao.py --aplicar  (grava, renomeia pastas e registra no log)
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402
from utils import cnpj_receita                       # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
S = os.environ.get('MIGRACAO_DIR') or os.path.join(
    os.environ.get('TEMP', ''), 'claude', 'bfaf016c5ad5231b', 'e89f5fc4-0cb5-42aa-bac0-a61a03c3e62d', 'scratchpad')
DIVERG = os.path.join(S, 'migracao_divergencias_clientes.csv')
JSON = os.path.join(S, 'migracao_in', 'migracao.json')
REAPONTAR = (('nfse_capturadas', 'xml_path'), ('cte_documentos', 'xml_caminho'), ('dfe_documentos', 'xml_caminho'),
             ('dfe_eventos', 'xml_caminho'), ('nfe_importacoes', 'xml_caminho'), ('dfe_certificados', 'dropbox_path'),
             ('documentos', 'caminho_arquivo'))


def so(x):
    return re.sub(r'\D', '', str(x or ''))


def norm(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip().upper()


def log(acao, cid, antes, depois):
    registrar_agente(acao, 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN, tabela='clientes', registro_id=cid,
                     depois=dict(depois, antes=antes))


def main(aplicar):
    div = list(csv.DictReader(open(DIVERG, encoding='utf-8-sig'), delimiter=';'))
    cli = execute_query('SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, regime_tributario, tipo_pessoa FROM clientes', fetch=True) or []
    by_num = {str(c['numero_cliente']): c for c in cli if c['numero_cliente']}

    # ---- 1. regimes -------------------------------------------------------------------------------
    regimes = []
    for r in div:
        c = by_num.get(str(r['numero_oficial']))
        if c and r['campo'].startswith('regime') and (c['regime_tributario'] or '') and (c['regime_tributario'] or '') != (r['planilha'] or ''):
            regimes.append((c, r['planilha']))
    print('1. REGIME divergente → apagar e mandar para A CADASTRAR: %d' % len(regimes))
    for c, pl in regimes:
        print('   %s %s | app %s x planilha %s' % (c['numero_cliente'], c['nome_razao_social'][:40], c['regime_tributario'], pl))
        if aplicar:
            execute_query('UPDATE clientes SET regime_tributario = NULL, alterado_por = %s, alterado_em = NOW() WHERE id = %s', (USUARIO_ID, c['id']), fetch=False)
            log('escrita.regime_conflito_limpo', c['id'], {'regime': c['regime_tributario']},
                {'regime': None, 'planilha_dizia': pl, 'motivo': 'conflito app x Acessórias: apaga e a equipe confere em A CADASTRAR'})

    # ---- 2. razões sociais ------------------------------------------------------------------------
    razoes = []
    for r in div:
        c = by_num.get(str(r['numero_oficial']))
        if c and r['campo'] == 'razao_social' and norm(c['nome_razao_social']) != norm(r['planilha']):
            razoes.append(c)
    print('2. RAZÃO SOCIAL divergente → igual ao CNPJ (Receita): %d' % len(razoes))
    from utils import dropbox_sync
    for c in razoes:
        try:
            dados, fonte = cnpj_receita.consultar(c['cpf_cnpj'])
        except Exception as e:
            print('   %s %s: Receita não respondeu (%s) — fica como está' % (c['numero_cliente'], c['nome_razao_social'][:40], e))
            continue
        receita = norm(dados.get('razao_social'))
        if not receita:
            print('   %s %s: Receita sem razão social — fica' % (c['numero_cliente'], c['nome_razao_social'][:40]))
            continue
        if receita == norm(c['nome_razao_social']):
            print('   %s %s: já é igual ao CNPJ' % (c['numero_cliente'], c['nome_razao_social'][:40]))
            continue
        de = dropbox_sync._build_empresa_folder(c['numero_cliente'], c['nome_razao_social'] or '')
        para = dropbox_sync._build_empresa_folder(c['numero_cliente'], receita)
        print('   %s "%s" → "%s" (%s) | pasta "%s" → "%s"' % (c['numero_cliente'], c['nome_razao_social'], receita, fonte, de, para))
        if not aplicar:
            continue
        st, cam_de, cam_para = dropbox_sync.renomear_pasta_empresa(de, para) if de != para else ('igual', None, None)
        if st in ('conflito', 'erro'):
            print('      Dropbox devolveu %s: nada alterado' % st)
            continue
        execute_query('UPDATE clientes SET nome_razao_social = %s, alterado_por = %s, alterado_em = NOW() WHERE id = %s', (receita, USUARIO_ID, c['id']), fetch=False)
        tot = 0
        if st == 'movida' and cam_de:
            like = cam_de.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '/%'
            corte = len(cam_de) + 1
            for t, col in REAPONTAR:
                n = execute_query(f'UPDATE {t} SET {col} = CONCAT(%s, SUBSTRING({col}, %s)) WHERE {col} LIKE %s', (cam_para, corte, like))
                tot += n or 0
        log('escrita.razao_social_cnpj', c['id'], {'razao_social': c['nome_razao_social']},
            {'razao_social': receita, 'fonte': fonte, 'pasta': para, 'dropbox': st, 'caminhos_reapontados': tot,
             'motivo': 'conflito app x Acessórias: vale a razão social do CNPJ'})
        time.sleep(1)

    # ---- 3. colisões de número --------------------------------------------------------------------
    d = json.load(open(JSON, encoding='utf-8'))
    emp = [dict(zip(d['empresas'][0], r)) for r in d['empresas'][1:]]
    col = []
    for e in emp:
        c = by_num.get(str(e['numero_oficial']))
        if c and so(c['cpf_cnpj']) != so(e['cnpj_cpf']):
            col.append((e['numero_oficial'], e['razao_social'], e['cnpj_cpf'], c['nome_razao_social'], c['cpf_cnpj']))
    print('3. COLISÃO de número (planilha e app com o mesmo nº e CNPJ diferente): %d' % len(col))
    for x in col:
        print('   nº %s | Acessórias: %s %s | app: %s %s' % x)
    print('pronto' if aplicar else 'modo conferência (--aplicar para gravar)')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--aplicar', action='store_true')
    main(ap.parse_args().aplicar)
