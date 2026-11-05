# -*- coding: utf-8 -*-
"""Migração de cadastros — parte 2: AVULSOS (aba ``avulsos``: quem está no Conexa e não no Acessórias).

Regras (Anderson, 04-05/11/2026): "o que vem do Acessórias assume número; o que vem do Conexa vai tudo
para Clientes Avulsos" e "colocar o Alexandre Ferreira como avulso também".
* chave = CPF/CNPJ. Já existe no app -> só recebe o ``conexa_id`` (e o que estiver vazio: e-mail,
  celular, telefone, emite_nf, cobrança). Cliente com número continua cliente; cadastro SEM número e
  sem marca de avulso (o Alexandre) vira avulso.
* não existe -> nasce como AVULSO (sem número), PF ou PJ pelo tamanho do documento, com nome, documento,
  e-mail, celular, telefone, observações (cidade e tags do Conexa), emite_nf e cobrança
  (Própria se tem contrato ativo, senão Pendente). Pasta do Dropbox nasce depois, como em todo avulso.
* dois registros do Conexa com o MESMO CPF (Bruna/Bruno Brito): o primeiro liga; o segundo não cria
  duplicata — fica anotado nas observações para a turma conferir em A CADASTRAR.
Idempotente.

    python scripts/migracao_avulsos.py --planilha caminho.xlsx            # dry-run
    python scripts/migracao_avulsos.py --planilha caminho.xlsx --apply
"""
import argparse
import io
import os
import re
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                        # noqa: E402,F401
from utils.db_helper import execute_query            # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
NL = chr(10)
BS = chr(92)
# Decisões do Anderson (05/11/2026)
FORCAR_AVULSO = {'65403792000190'}      # Vertex (nº 163): "coloca como avulso porque ela saiu" — perde o número, pasta acompanha
NOTAS = {330: 'Nome veio do Conexa como o posto; este CPF pagou a constituição do Auto Posto Portal de Caucaia '
              '(cobrança 2323, R$ 1.500, 07/2026) – conferir o nome do titular'}
REAPONTAR = (('nfse_capturadas', 'xml_path'), ('cte_documentos', 'xml_caminho'), ('dfe_documentos', 'xml_caminho'),
             ('dfe_eventos', 'xml_caminho'), ('nfe_importacoes', 'xml_caminho'), ('dfe_certificados', 'dropbox_path'),
             ('documentos', 'caminho_arquivo'))


def tirar_numero(c, aplicar):
    """Cliente que saiu vira avulso: numero_cliente -> NULL e a pasta 'nº - razão' -> 'razão' (caminhos reapontados)."""
    from utils import dropbox_sync
    de = dropbox_sync._build_empresa_folder(str(c['numero_cliente']), c['nome_razao_social'] or '')
    para = dropbox_sync._build_empresa_folder(None, c['nome_razao_social'] or '')
    print(f"  vira avulso: nº {c['numero_cliente']} {c['nome_razao_social']} | pasta '{de}' -> '{para}'")
    if not aplicar:
        return True
    st, cam_de, cam_para = dropbox_sync.renomear_pasta_empresa(de, para)
    if st in ('conflito', 'erro'):
        print(f'  ERRO Dropbox ({st}) ao mover a pasta da {c["nome_razao_social"]}: nada alterado nela')
        return False
    execute_query('UPDATE clientes SET numero_cliente = NULL, avulso = 1, avulso_em = NOW(), alterado_por = %s, alterado_em = NOW() WHERE id = %s', (USUARIO_ID, c['id']))
    tot = 0
    if st == 'movida' and cam_de:
        like = cam_de.replace(BS, BS + BS).replace('%', BS + '%').replace('_', BS + '_') + '/%'
        for t, col in REAPONTAR:
            tot += execute_query(f'UPDATE {t} SET {col} = CONCAT(%s, SUBSTRING({col}, %s)) WHERE {col} LIKE %s', (cam_para, len(cam_de) + 1, like)) or 0
    log('escrita.virou_avulso_migracao', c['id'], {'numero_antigo': c['numero_cliente'], 'pasta': para, 'dropbox': st, 'caminhos_reapontados': tot, 'motivo': 'saiu do escritório (Anderson, 05/11/2026)'})
    return True


def txt(v):
    return re.sub(r'\s+', ' ', str(v if v is not None else '')).strip()


def dig(v):
    return re.sub(r'\D', '', str(v or ''))


def plain(v):
    return txt(v).upper()


def fmt_doc(d):
    d = dig(d)
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    if len(d) == 11:
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    return d


def email_de(v):
    s = txt(v).replace(',', ';').split(';')[0].strip().lower()
    return s[:255] if s and '@' in s else None


def ler(caminho):
    from openpyxl import load_workbook
    ws = load_workbook(caminho, data_only=True, read_only=True)['avulsos']
    linhas = [list(r) for r in ws.iter_rows(values_only=True)]
    cab = [txt(c) for c in linhas[0]]
    return [dict(zip(cab, list(r) + [None] * (len(cab) - len(r)))) for r in linhas[1:] if any(v is not None and txt(v) for v in r)]


def log(acao, registro_id, depois):
    from utils.atividade import registrar_agente
    registrar_agente(acao, 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN, tabela='clientes', registro_id=registro_id, depois=depois)


def observacoes_de(a):
    partes = []
    if txt(a.get('obs')):
        partes.append(txt(a['obs']))
    if txt(a.get('cidade')):
        partes.append('Cidade (Conexa): ' + txt(a['cidade']))
    if txt(a.get('tags')):
        partes.append('Tags Conexa: ' + txt(a['tags']))
    return NL.join(partes) or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--planilha', required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    aplicar = args.apply
    av = ler(args.planilha)
    app = execute_query('SELECT id, numero_cliente, avulso, nome_razao_social, cpf_cnpj, email, celular, telefone, emite_nf, cobranca, conexa_id, observacoes FROM clientes', fetch=True) or []
    por_doc, por_conexa = {}, {}
    for c in app:
        if dig(c['cpf_cnpj']):
            por_doc.setdefault(dig(c['cpf_cnpj']), []).append(c)
        if c['conexa_id']:
            por_conexa[int(c['conexa_id'])] = c
    rel = Counter(); linhas = []
    vistos = {}
    # CPF repetido na planilha (Bruna/Bruno): quem tem o MESMO NOME do cadastro do app vai primeiro e liga;
    # o outro fica anotado. Sem cadastro no app, a ordem da planilha vale.
    def ordem(a):
        d = dig(a['cpf_cnpj']); cands = por_doc.get(d, [])
        casa_nome = any(plain(c['nome_razao_social']) == plain(a['nome']) for c in cands)
        return (0 if casa_nome else 1)
    av = sorted(av, key=ordem)
    print(f'planilha: {len(av)} avulsos ({Counter(a["tipo"] for a in av)}) | app: {len(app)} cadastros')
    for a in av:
        cid_conexa = int(a['conexa_id']); d = dig(a['cpf_cnpj']); nome = txt(a['nome'])
        if not d or len(d) not in (11, 14):
            rel['pulado sem documento'] += 1; linhas.append(f'PULADO conexa {cid_conexa} {nome}: documento inválido "{a["cpf_cnpj"]}"'); continue
        c = por_conexa.get(cid_conexa)
        if not c and d in vistos:                 # mesmo CPF já tratado nesta rodada (Bruna/Bruno)
            alvo = vistos[d]
            nota = f"Conexa ids {alvo.get('conexa_id') or '?'} e {cid_conexa} (um grafado '{nome}') são esta mesma pessoa"
            rel['duplicado no Conexa (anotado)'] += 1
            linhas.append(f'DUPLICADO conexa {cid_conexa} {nome}: mesmo CPF de "{alvo["nome_razao_social"]}" (app {alvo["id"]}) — anoto nas observações, não crio')
            if aplicar and nota not in txt(alvo.get('observacoes')):
                execute_query('UPDATE clientes SET observacoes = CONCAT(COALESCE(observacoes, ""), %s), alterado_por = %s, alterado_em = NOW() WHERE id = %s',
                              ((NL if txt(alvo.get('observacoes')) else '') + nota, USUARIO_ID, alvo['id']))
                log('escrita.anotou_duplicado_conexa_migracao', alvo['id'], {'conexa_id': cid_conexa, 'nome': nome})
            continue
        if not c:
            cands = por_doc.get(d, [])
            c = cands[0] if cands else None
        if c:
            vistos[d] = c
            if d in FORCAR_AVULSO and c['numero_cliente'] and not c['avulso']:
                if not tirar_numero(c, aplicar):
                    continue
                c = dict(c, numero_cliente=None, avulso=1)
            sets, vals, dep = [], [], {}
            if not c['conexa_id']:
                sets.append('conexa_id = %s'); vals.append(cid_conexa); dep['conexa_id'] = cid_conexa
            vira_avulso = not c['numero_cliente'] and not c['avulso']
            if vira_avulso:
                sets.append('avulso = 1'); sets.append('avulso_em = NOW()'); dep['avulso'] = True
            for col, val in (('email', email_de(a['email_financeiro'])), ('celular', txt(a['celular'])[:20] or None), ('telefone', txt(a['telefone'])[:20] or None),
                             ('emite_nf', {'SIM': 1, 'NÃO': 0, 'NAO': 0}.get(plain(a['emite_nf']))),
                             ('cobranca', 'PROPRIA' if plain(a['tem_contrato_ativo']) == 'SIM' else 'PENDENTE')):
                if val is not None and val != '' and c.get(col) in (None, ''):
                    sets.append(f'{col} = %s'); vals.append(val); dep[col] = val
            papel = 'vira AVULSO' if vira_avulso else ('cliente nº ' + str(c['numero_cliente']) if c['numero_cliente'] else 'avulso')
            rel['existentes ligados'] += 1
            linhas.append(f"LIGA conexa {cid_conexa} {nome} -> app {c['id']} ({papel}): {', '.join(dep.keys()) or 'nada a preencher'}")
            if sets and aplicar:
                sets += ['alterado_por = %s', 'alterado_em = NOW()']; vals.append(USUARIO_ID)
                execute_query(f"UPDATE clientes SET {', '.join(sets)} WHERE id = %s", tuple(vals + [c['id']]))
                log('escrita.ligou_conexa_migracao', c['id'], dict(dep, nome=nome))
            vistos[d] = dict(c, conexa_id=c['conexa_id'] or cid_conexa)
            continue
        # novo avulso
        obs = observacoes_de(a)
        if cid_conexa in NOTAS:
            obs = (obs + NL if obs else '') + NOTAS[cid_conexa]
        dados = {'numero_cliente': None, 'tipo_pessoa': 'PF' if len(d) == 11 else 'PJ', 'nome_razao_social': nome[:255], 'cpf_cnpj': fmt_doc(d),
                 'email': email_de(a['email_financeiro']), 'celular': txt(a['celular'])[:20] or None, 'telefone': txt(a['telefone'])[:20] or None,
                 'situacao': 'ATIVO', 'observacoes': obs, 'criado_por': USUARIO_ID, 'avulso': True}
        extras = {'conexa_id': cid_conexa, 'emite_nf': {'SIM': 1, 'NÃO': 0, 'NAO': 0}.get(plain(a['emite_nf'])),
                  'cobranca': 'PROPRIA' if plain(a['tem_contrato_ativo']) == 'SIM' else 'PENDENTE'}
        rel['novos avulsos ' + dados['tipo_pessoa']] += 1
        linhas.append(f"NOVO conexa {cid_conexa} {dados['tipo_pessoa']} {nome[:40]} | {dados['cpf_cnpj']} | {dados['email'] or 'sem e-mail'}")
        if aplicar:
            from models.cliente import Cliente
            cid = Cliente.create(dados)
            if not cid:
                rel['ERRO create'] += 1; linhas.append(f'ERRO conexa {cid_conexa}: Cliente.create falhou'); continue
            ext = {k: v for k, v in extras.items() if v is not None}
            execute_query('UPDATE clientes SET ' + ', '.join(f'{k} = %s' for k in ext) + ' WHERE id = %s', tuple(ext.values()) + (cid,))
            log('escrita.criou_avulso_migracao', cid, {**{k: str(v) for k, v in dados.items() if v is not None and k != 'observacoes'}, **ext, 'origem': 'Conexa'})
            vistos[d] = {'id': cid, 'nome_razao_social': nome, 'observacoes': dados['observacoes'], 'conexa_id': cid_conexa}
    for l in linhas:
        print(' ', l)
    print(NL + '== RESUMO ==', dict(rel))
    print('MODO:', 'APLICADO' if aplicar else 'DRY-RUN (nada gravado)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
