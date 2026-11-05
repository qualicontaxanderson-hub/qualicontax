# -*- coding: utf-8 -*-
"""Migração de cadastros — parte 1: CLIENTES (aba ``empresas`` da planilha Migracao_Cadastros_App_Qualicontax.xlsx).

Regras combinadas com o Anderson (04/11/2026):
* chave = ``numero_oficial`` (ID do Acessórias) ↔ ``clientes.numero_cliente``; nenhuma numeração nova;
* o Acessórias manda no número: quando o app tem o MESMO CNPJ com outro número, o app é renumerado
  (pasta do Dropbox e os caminhos gravados acompanham, como na troca de número da tela);
* cliente que já existe: preenche SOMENTE os campos vazios; nunca sobrescreve; divergência vai para o
  relatório (CSV) sem alterar nada;
* tags do Conexa vão para Observações; a data do cadastro no Acessórias entra em data_inicio_contrato
  quando vazio; CPFs/CNPJs extras vão para cadastros_adicionais_clientes;
* cliente novo: nasce com o que a planilha traz (sem endereço e sem grupo: endereço e o resto vêm da
  Receita na parte 4, "Buscar CNPJ"; grupos ficam para depois);
* regime tributário e telefone do escritório (11 2523-1815) não entram: a Receita decide depois;
* e-mail principal: só se vazio, o ``email_principal_fallback`` (o primeiro, quando há vários);
* observações: os comentários do Acessórias, só se o app não tem.

Idempotente: rodar de novo não cria nem preenche nada que já esteja lá.

    python scripts/migracao_clientes.py --planilha caminho.xlsx            # dry-run (relatório + CSV)
    python scripts/migracao_clientes.py --planilha caminho.xlsx --apply
"""
import argparse
import csv
import io
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                        # noqa: E402,F401
from utils.db_helper import execute_query            # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'   # autor da migração nos logs
TEL_ESCRITORIO = {'1125231815', '01125231815'}
NL = chr(10)
REGIMES = (('SIMPLES NACIONAL', 'SIMPLES'), ('SIMPLES', 'SIMPLES'), ('LUCRO PRESUMIDO', 'LUCRO_PRESUMIDO'),
           ('LUCRO REAL', 'LUCRO_REAL'), ('MEI', 'MEI'))
REAPONTAR = (('nfse_capturadas', 'xml_path'), ('cte_documentos', 'xml_caminho'), ('dfe_documentos', 'xml_caminho'),
             ('dfe_eventos', 'xml_caminho'), ('nfe_importacoes', 'xml_caminho'), ('dfe_certificados', 'dropbox_path'),
             ('documentos', 'caminho_arquivo'))


# ----------------------------------------------------------------------------- utilidades
def txt(v):
    return re.sub(r'\s+', ' ', str(v if v is not None else '')).strip()


def dig(v):
    return re.sub(r'\D', '', str(v or ''))


def plain(v):
    return re.sub(r'[^A-Z0-9 ]', '', unicodedata.normalize('NFKD', txt(v).upper()).encode('ascii', 'ignore').decode()).strip()


def data_br(v):
    s = txt(v)
    m = re.match(r'^(\d{2})/(\d{2})/(\d{4})', s)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mth, d)
        except ValueError:
            return None
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def fmt_cnpj(d):
    d = dig(d)
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    if len(d) == 11:
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    return d


def regime_de(v):
    u = plain(v)
    for chave, val in REGIMES:
        if u.startswith(chave):
            return val
    return None


def telefone_de(v):
    primeiro = txt(v).split('/')[0].strip()
    if not primeiro or dig(primeiro) in TEL_ESCRITORIO:
        return None
    return primeiro[:20]


def email_de(v):
    s = txt(v).replace(',', ';').split(';')[0].strip().lower()
    return s[:255] if s and '@' in s else None


def ies_de(v):
    """'UF: GO - IE: 104282215 UF: MG - IE: 27841120036' -> [('GO', '104282215'), ('MG', '27841120036')]"""
    return [(uf.upper(), ie.strip()) for uf, ie in re.findall(r'UF:\s*(\w\w)\s*-\s*IE:\s*([\d./-]+)', txt(v))]


def cobranca_de(v):
    u = plain(v)
    if u.startswith('PROPRIA'):
        return 'PROPRIA'
    if u.startswith('VIA GRUPO'):
        return 'VIA_GRUPO'
    if u.startswith('PENDENTE'):
        return 'PENDENTE'
    return None


def ler_planilha(caminho):
    from openpyxl import load_workbook
    wb = load_workbook(caminho, data_only=True, read_only=True)
    ws = wb['empresas']
    linhas = [list(r) for r in ws.iter_rows(values_only=True)]
    cab = [txt(c) for c in linhas[0]]
    out = []
    for r in linhas[1:]:
        if not any(v is not None and txt(v) for v in r):
            continue
        out.append(dict(zip(cab, list(r) + [None] * (len(cab) - len(r)))))
    return out


def registrar_log(acao, registro_id, depois):
    from utils.atividade import registrar_agente
    registrar_agente(acao, 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                     tabela='clientes', registro_id=registro_id, depois=depois)


# ----------------------------------------------------------------------------- renumeração
def renumerar(c, numero_novo, aplicar, rel):
    """App tem o CNPJ com outro número: passa a ser o número do Acessórias (pasta e caminhos juntos)."""
    from utils import dropbox_sync
    antigo = str(c['numero_cliente'])
    if execute_query('SELECT id FROM clientes WHERE numero_cliente = %s AND id <> %s', (numero_novo, c['id']), fetch=True):
        rel['erros'].append(f"renumerar {antigo}->{numero_novo} ({c['nome_razao_social']}): número {numero_novo} já em uso")
        return False
    de = dropbox_sync._build_empresa_folder(antigo, c['nome_razao_social'] or '')
    para = dropbox_sync._build_empresa_folder(numero_novo, c['nome_razao_social'] or '')
    print(f"  renumerar {antigo} -> {numero_novo}: {c['nome_razao_social']} | pasta '{de}' -> '{para}'")
    if not aplicar:
        return True
    st, cam_de, cam_para = dropbox_sync.renomear_pasta_empresa(de, para)
    if st in ('conflito', 'erro'):
        rel['erros'].append(f'renumerar {antigo}->{numero_novo}: Dropbox devolveu {st}; nada alterado')
        return False
    execute_query('UPDATE clientes SET numero_cliente = %s, alterado_por = %s, alterado_em = NOW() WHERE id = %s',
                  (numero_novo, USUARIO_ID, c['id']))
    tot = 0
    if st == 'movida' and cam_de:
        like = cam_de.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '/%'
        corte = len(cam_de) + 1
        for t, col in REAPONTAR:
            n = execute_query(f'UPDATE {t} SET {col} = CONCAT(%s, SUBSTRING({col}, %s)) WHERE {col} LIKE %s',
                              (cam_para, corte, like))
            tot += n or 0
    registrar_log('escrita.renumerou_cliente_migracao', c['id'],
                  {'de': antigo, 'para': numero_novo, 'pasta': para, 'dropbox': st, 'caminhos_reapontados': tot,
                   'motivo': 'migração Acessórias: o número oficial manda'})
    c['numero_cliente'] = numero_novo
    return True


# ----------------------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--planilha', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--csv', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'migracao_divergencias_clientes.csv'))
    args = ap.parse_args()
    aplicar = args.apply
    if not execute_query("SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clientes' AND COLUMN_NAME = 'conexa_id'", fetch=True):
        print('As colunas novas ainda não existem: rode migrations/migracao_01_clientes_colunas.py --apply antes.')
        if aplicar:
            return 1
    tem_ie_tab = bool(execute_query("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'clientes_inscricoes_estaduais'", fetch=True))

    emp = ler_planilha(args.planilha)
    app = execute_query('SELECT * FROM clientes', fetch=True) or []
    por_num = {str(c['numero_cliente']).strip(): c for c in app if c['numero_cliente']}
    por_doc = defaultdict(list)
    for c in app:
        if dig(c['cpf_cnpj']):
            por_doc[dig(c['cpf_cnpj'])].append(c)
    plan_por_num = {str(e['numero_oficial']).strip(): e for e in emp}
    rel = {'erros': [], 'diverg': [], 'preenche': Counter(), 'novos': 0, 'existentes': 0, 'renumerados': 0, 'pulados': []}
    print(f'planilha: {len(emp)} empresas | app: {len(app)} cadastros ({len(por_num)} com número)')

    # 1) renumerações (o Acessórias manda). Primeiro quem está ocupando um número que a planilha dá a OUTRO CNPJ.
    print('\n== 1. Renumerações ==')
    fila = []
    for e in emp:
        n = str(e['numero_oficial']).strip()
        d = dig(e['cnpj_cpf'])
        c = por_num.get(n)
        if c and dig(c['cpf_cnpj']) and dig(c['cpf_cnpj']) != d:
            # o app tem outro CNPJ nesse número: esse cadastro do app deve ir para o número que a planilha dá a ele
            alvo = next((str(x['numero_oficial']).strip() for x in emp if dig(x['cnpj_cpf']) == dig(c['cpf_cnpj'])), None)
            if alvo and alvo != n:
                fila.append((c, alvo, 'número ocupado por outro CNPJ'))
            else:
                rel['erros'].append(f"nº {n}: app tem {c['nome_razao_social']} ({c['cpf_cnpj']}) e a planilha {e['razao_social']} ({e['cnpj_cpf']}); CNPJ do app não está na planilha — não mexo")
    for e in emp:
        n = str(e['numero_oficial']).strip()
        d = dig(e['cnpj_cpf'])
        if n in por_num or not d:
            continue
        outros = [o for o in por_doc.get(d, []) if not o['avulso'] and str(o['numero_cliente'] or '') != n]
        if len(outros) == 1 and all(o is not outros[0] for o, _, _ in fila):
            fila.append((outros[0], n, 'mesmo CNPJ com outro número'))
    for c, novo, motivo in fila:
        if renumerar(c, novo, aplicar, rel):
            rel['renumerados'] += 1
    if fila and aplicar:
        app = execute_query('SELECT * FROM clientes', fetch=True) or []
        por_num = {str(c['numero_cliente']).strip(): c for c in app if c['numero_cliente']}
    elif fila:
        for c, novo, _ in fila:          # no dry-run, simula o número novo para o resto do relatório
            por_num.pop(str(c['numero_cliente']), None)
            por_num[novo] = dict(c, numero_cliente=novo)
    if not fila:
        print('  nenhuma')

    # 2) existentes: preencher só vazios + divergências; 3) novos
    print('\n== 2. Existentes e 3. Novos ==')
    ies_novas = 0
    for e in emp:
        n = str(e['numero_oficial']).strip()
        d = dig(e['cnpj_cpf'])
        c = por_num.get(n)
        if c:
            if dig(c['cpf_cnpj']) and d and dig(c['cpf_cnpj']) != d:
                rel['pulados'].append(f"nº {n}: CNPJ do app {c['cpf_cnpj']} ≠ planilha {e['cnpj_cpf']} — não toco")
                continue
            rel['existentes'] += 1
            sets, vals, log = [], [], {}

            def quer(col, valor, rotulo=None, comparar=None):
                """Preenche se o app está vazio; se os dois têm valor e diferem, vai para o CSV."""
                if valor in (None, ''):
                    return
                atual = c.get(col)
                if atual in (None, ''):
                    sets.append(f'{col} = %s'); vals.append(valor); log[col] = str(valor)[:120]
                    rel['preenche'][rotulo or col] += 1
                else:
                    a, b = (comparar or (lambda x: txt(x)))(valor), (comparar or (lambda x: txt(x)))(atual)
                    if a != b:
                        rel['diverg'].append((n, rotulo or col, str(valor)[:120], str(atual)[:120]))

            fant = txt(e['nome_fantasia'])
            if fant and plain(fant) != plain(e['razao_social']):
                quer('nome_fantasia', fant[:255])
            quer('inscricao_municipal', txt(e['insc_municipal'])[:20] or None, comparar=dig)
            ies = ies_de(e['insc_estaduais'])
            if ies:
                quer('inscricao_estadual', ies[0][1][:20], rotulo='inscricao_estadual (principal)', comparar=dig)
            quer('data_inicio_atividade', data_br(e['data_abertura']), comparar=lambda x: str(x)[:10])
            quer('telefone', telefone_de(e['telefone']), comparar=dig)
            quer('email', email_de(e['email_principal_fallback']), comparar=lambda x: txt(x).lower())
            quer('observacoes', txt(e['observacoes']) or None, comparar=plain)
            quer('nire', txt(e['nire'])[:20] or None, comparar=dig)
            quer('conexa_id', int(e['conexa_id']) if txt(e['conexa_id']).isdigit() else None, comparar=lambda x: str(x))
            quer('emite_nf', {'SIM': 1, 'NAO': 0}.get(plain(e['emite_nf'])), comparar=lambda x: str(int(x)))
            quer('cobranca', cobranca_de(e['cobranca']))
            # data do cadastro no Acessórias = "cliente desde", no campo que já existe, só se vazio
            quer('data_inicio_contrato', data_br(e['cadastro_s3d']), rotulo='data_inicio_contrato (cadastro Acessórias)', comparar=lambda x: str(x)[:10])
            # tags do Conexa vão para Observações (acrescenta no fim, sem apagar o que há)
            tags = txt(e['tags_conexa'])
            if tags and 'Tags Conexa:' not in txt(c.get('observacoes')):
                if 'observacoes' in log:
                    i = sets.index('observacoes = %s'); vals[i] = vals[i] + NL + 'Tags Conexa: ' + tags
                else:
                    obs_nova = (txt(c.get('observacoes')) + NL if txt(c.get('observacoes')) else '') + 'Tags Conexa: ' + tags
                    sets.append('observacoes = %s'); vals.append(obs_nova)
                log['tags'] = tags[:120]; rel['preenche']['tags (em observacoes)'] += 1
            # razão social e regime: só relatório
            if plain(e['razao_social']) != plain(c['nome_razao_social']):
                rel['diverg'].append((n, 'razao_social', txt(e['razao_social'])[:120], txt(c['nome_razao_social'])[:120]))
            rg = regime_de(e['regime_s3d'])
            if rg and c.get('regime_tributario') and rg != c['regime_tributario']:
                rel['diverg'].append((n, 'regime_tributario (não altera)', rg, c['regime_tributario']))
            if sets and aplicar:
                sets.append('alterado_por = %s'); vals.append(USUARIO_ID); sets.append('alterado_em = NOW()')
                execute_query(f"UPDATE clientes SET {', '.join(sets)} WHERE id = %s", tuple(vals + [c['id']]))
                registrar_log('escrita.completou_cliente_migracao', c['id'], dict(log, origem='Acessórias/Conexa'))
            ies_novas += gravar_ies(c['id'], ies, c.get('inscricao_estadual'), plain(e['ie_isenta']) == 'SIM', e, aplicar, tem_ie_tab)
            rel['preenche']['identificadores extras (cadastros_adicionais)'] += gravar_identificadores(c['id'], e, aplicar)
            continue

        # ---- novo
        if not d:
            rel['pulados'].append(f"nº {n}: {e['razao_social']} sem CNPJ na planilha — não crio")
            continue
        if any(not o['avulso'] for o in por_doc.get(d, [])) and not aplicar:
            rel['pulados'].append(f"nº {n}: CNPJ {e['cnpj_cpf']} já existe no app com outro número e não entrou na renumeração")
            continue
        rel['novos'] += 1
        ies = ies_de(e['insc_estaduais'])
        dados = {
            'numero_cliente': n, 'tipo_pessoa': 'PF' if len(d) == 11 else 'PJ',
            'nome_razao_social': txt(e['razao_social'])[:255], 'cpf_cnpj': fmt_cnpj(d),
            'inscricao_estadual': (ies[0][1][:20] if ies else None), 'inscricao_municipal': txt(e['insc_municipal'])[:20] or None,
            'email': email_de(e['email_principal_fallback']), 'telefone': telefone_de(e['telefone']),
            'regime_tributario': regime_de(e['regime_s3d']), 'data_inicio_atividade': data_br(e['data_abertura']),
            'situacao': 'ATIVO', 'observacoes': txt(e['observacoes']) or None, 'criado_por': USUARIO_ID,
        }
        if txt(e['tags_conexa']):
            dados['observacoes'] = ((dados['observacoes'] + NL) if dados['observacoes'] else '') + 'Tags Conexa: ' + txt(e['tags_conexa'])
        dados['data_inicio_contrato'] = data_br(e['cadastro_s3d'])
        extras = {
            'nome_fantasia': (txt(e['nome_fantasia'])[:255] if plain(e['nome_fantasia']) != plain(e['razao_social']) else None),
            'nire': txt(e['nire'])[:20] or None,
            'conexa_id': int(e['conexa_id']) if txt(e['conexa_id']).isdigit() else None,
            'emite_nf': {'SIM': 1, 'NAO': 0}.get(plain(e['emite_nf'])), 'cobranca': cobranca_de(e['cobranca']),
        }
        print(f"  novo nº {n}: {dados['nome_razao_social'][:45]} | {dados['cpf_cnpj']} | {dados['regime_tributario'] or 'sem regime'}"
              + (f" | conexa {extras['conexa_id']}" if extras['conexa_id'] else ''))
        if aplicar:
            from models.cliente import Cliente
            cid = Cliente.create(dados)
            if not cid:
                rel['erros'].append(f'nº {n}: Cliente.create falhou')
                continue
            ext = {k: v for k, v in extras.items() if v is not None}
            if ext:
                execute_query('UPDATE clientes SET ' + ', '.join(f'{k} = %s' for k in ext) + ' WHERE id = %s', tuple(ext.values()) + (cid,))
            registrar_log('escrita.criou_cliente_migracao', cid, {**{k: str(v) for k, v in dados.items() if v is not None and k != 'observacoes'}, 'origem': 'Acessórias/Conexa'})
            ies_novas += gravar_ies(cid, ies, None, plain(e['ie_isenta']) == 'SIM', e, aplicar, tem_ie_tab)
            gravar_identificadores(cid, e, aplicar)
        else:
            ies_novas += len(ies) + (1 if not ies and plain(e['ie_isenta']) == 'SIM' else 0)

    # relatório
    print('\n== RESUMO ==')
    print(f"renumerados: {rel['renumerados']} | existentes tratados: {rel['existentes']} | novos: {rel['novos']} | pulados: {len(rel['pulados'])} | IEs {'gravadas' if aplicar else 'a gravar'}: {ies_novas}")
    print('campos preenchidos nos existentes:', dict(rel['preenche'].most_common()))
    print(f"divergências (app diferente da planilha, nada alterado): {len(rel['diverg'])} ->", Counter(x[1] for x in rel['diverg']).most_common())
    for p in rel['pulados']:
        print('  PULADO:', p)
    for er in rel['erros']:
        print('  ERRO:', er)
    with open(args.csv, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['numero_oficial', 'campo', 'planilha', 'app'])
        w.writerows(rel['diverg'])
    print('CSV de divergências:', os.path.abspath(args.csv))
    print('MODO:', 'APLICADO' if aplicar else 'DRY-RUN (nada gravado)')
    return 0


def gravar_identificadores(cliente_id, e, aplicar):
    """CPFs/CNPJs extras do Acessórias -> cadastros_adicionais_clientes (tipo IDENTIFICADOR). Devolve quantos entram."""
    docs = [d for d in re.split(r'[\s;,/]+', txt(e.get('cpfs_outros_identificadores'))) if dig(d) and len(dig(d)) in (11, 14)]
    n = 0
    for d in docs:
        valor = fmt_cnpj(d)
        if aplicar:
            if execute_query('SELECT id FROM cadastros_adicionais_clientes WHERE cliente_id = %s AND tipo = %s AND valor = %s',
                             (cliente_id, 'IDENTIFICADOR', valor), fetch=True):
                continue
            execute_query('INSERT INTO cadastros_adicionais_clientes (cliente_id, tipo, campo, valor, observacoes, ativo) VALUES (%s, %s, %s, %s, %s, 1)',
                          (cliente_id, 'IDENTIFICADOR', 'CPF' if len(dig(d)) == 11 else 'CNPJ', valor, 'migração Acessórias'))
        n += 1
    return n


def gravar_ies(cliente_id, ies, ie_app, isenta, e, aplicar, tem_tab):
    """Todas as IEs da planilha (e a que o app já tinha) na tabela própria. Devolve quantas entram."""
    if not tem_tab:
        return 0
    linhas = []
    uf_cli = txt(e.get('uf')).upper()[:2] or None
    if ie_app and txt(ie_app):
        linhas.append((uf_cli, txt(ie_app)[:20], 0, 1, 'app'))
    for i, (uf, ie) in enumerate(ies):
        principal = 1 if (not ie_app and i == 0) else 0
        if any(dig(ie) == dig(l[1]) for l in linhas):
            continue
        linhas.append((uf, ie[:20], 0, principal, 'acessorias'))
    if not linhas and isenta:
        linhas.append((uf_cli, 'ISENTA', 1, 1, 'acessorias'))
    n = 0
    for uf, ie, isen, princ, origem in linhas:
        if aplicar:
            ja = execute_query('SELECT id FROM clientes_inscricoes_estaduais WHERE cliente_id = %s AND ie = %s AND (uf <=> %s)',
                               (cliente_id, ie, uf), fetch=True)
            if ja:
                continue
            execute_query('INSERT INTO clientes_inscricoes_estaduais (cliente_id, uf, ie, isenta, principal, origem) VALUES (%s, %s, %s, %s, %s, %s)',
                          (cliente_id, uf, ie, isen, princ, origem))
        n += 1
    return n


if __name__ == '__main__':
    sys.exit(main())
