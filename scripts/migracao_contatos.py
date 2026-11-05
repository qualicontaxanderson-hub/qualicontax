# -*- coding: utf-8 -*-
"""Migração Acessórias+Conexa — PARTE 5b: CONTATOS das empresas (05/11/2026).

Decisão do Anderson (05/11): "O que temos no Acessórias vai ser o nosso padrão... o que tem lá vamos
cadastrar aqui, só os que não tiver [contato] é porque não manda honorários" e "já é para deixar tudo
cadastrado: se no Acessórias tem acesso a tudo, já deixar a escrita fiscal, contábil, DP, tudo pronto".

Fonte: aba ``contatos`` do migracao.json (857 linhas: numero_oficial, nome, cargo, telefone, email,
origem, recebe_<Departamento> SIM/NÃO x 7, preferencia_envio — todos TODOS, não vira coluna).

Regras:
  * empresa pelo numero_oficial = clientes.numero_cliente (avulso = 0); sem empresa → relata;
  * mesma empresa + mesmo e-mail na planilha = um contato só (flags somadas);
  * contato já existente no app com o mesmo e-mail (ou mesmo nome, sem e-mail) é COMPLETADO: só os
    campos vazios recebem valor; os departamentos passam a ser os do Acessórias (ele é o padrão);
  * telefone da planilha: 11 dígitos = celular, 10 = telefone fixo; o do escritório (11 2523-1815) e
    números quebrados não entram (aviso); cargo perde o código ("49-Sócio-Administrador" → "Sócio-Administrador");
  * contato sem e-mail e sem telefone ENTRA mesmo assim (ele quer tudo que está lá); o celular
    obrigatório vira pendência em A CADASTRAR;
  * idempotente: na 2ª rodada tudo "já existia"; log por contato escrita.contato_acessorias_migracao.

Uso:
    python scripts/migracao_contatos.py --json <migracao.json> [--csv saida.csv]   # dry-run
    python scripts/migracao_contatos.py --json <migracao.json> --apply
"""
import argparse
import csv
import io
import json
import os
import re
import sys
from collections import Counter, OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                        # noqa: E402,F401
from utils.db_helper import execute_query            # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
TEL_ESCRITORIO = {'1125231815'}
DEPTOS = OrderedDict([('recebe_Contábil', 'Contábil'), ('recebe_Fiscal', 'Fiscal'), ('recebe_Pessoal', 'Pessoal'),
                      ('recebe_Financeiro', 'Financeiro'), ('recebe_Legalização', 'Legalização'),
                      ('recebe_Certificado Digital', 'Certificado Digital'), ('recebe_Visita', 'Visita')])


def tx(v):
    s = str(v).strip() if v is not None else ''
    return '' if s in ('None', 'nan') else s


def dig(v):
    return re.sub(r'\D', '', tx(v))


def fone_fmt(d):
    if len(d) == 11:
        return f'({d[:2]}) {d[2:7]}-{d[7:]}'
    if len(d) == 10:
        return f'({d[:2]}) {d[2:6]}-{d[6:]}'
    return d


def cargo_limpo(v):
    return re.sub(r'^\s*\d+\s*-\s*', '', tx(v))[:100] or None


def ler(caminho):
    m = json.load(open(caminho, encoding='utf-8'))
    tab = m['contatos']
    head = [str(h) for h in tab[0]]
    return [dict(zip(head, r)) for r in tab[1:]]


def agrupar(linhas):
    """Uma entrada por (empresa, e-mail) — ou por (empresa, nome) quando não há e-mail."""
    grupos = OrderedDict()
    for r in linhas:
        num = tx(r.get('numero_oficial')).split('.')[0]
        email = tx(r.get('email')).lower()
        nome = tx(r.get('nome'))
        if not num.isdigit() or not nome:
            continue
        chave = (num, email) if email else (num, 'nome:' + nome.upper())
        g = grupos.setdefault(chave, {'num': num, 'nome': nome, 'email': email or None, 'cargo': None,
                                      'celular': None, 'telefone': None, 'deptos': set(), 'avisos': [], 'origem': set()})
        g['cargo'] = g['cargo'] or cargo_limpo(r.get('cargo'))
        g['origem'].add(tx(r.get('origem')))
        d = dig(r.get('telefone'))
        if d:
            d = d[1:] if len(d) == 12 and d.startswith('0') else d
            d = d[2:] if len(d) == 13 and d.startswith('55') else d
            if d in TEL_ESCRITORIO:
                g['avisos'].append('telefone do escritório descartado')
            elif len(d) == 11 and not g['celular']:
                g['celular'] = fone_fmt(d)
            elif len(d) == 10 and not g['telefone']:
                g['telefone'] = fone_fmt(d)
            elif len(d) not in (10, 11):
                g['avisos'].append(f'telefone quebrado "{tx(r.get("telefone"))}" descartado')
        for col, nome_d in DEPTOS.items():
            if tx(r.get(col)).upper() == 'SIM':
                g['deptos'].add(nome_d)
    return list(grupos.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--csv')
    a = ap.parse_args()
    from models.contato_cliente import ContatoCliente

    deps = {d['nome']: d['id'] for d in execute_query('SELECT id, nome FROM departamentos', fetch=True) or []}
    faltam = [n for n in DEPTOS.values() if n not in deps]
    if faltam:
        print('departamentos que não existem no app:', faltam, '— rode migrations/migracao_03_contatos_departamentos.py')
        return
    clientes = {str(c['numero_cliente']): c for c in execute_query(
        "SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE avulso = 0 AND numero_cliente REGEXP '^[0-9]+$'", fetch=True) or []}
    existentes = {}
    for c in execute_query("""SELECT c.id, c.cliente_id, c.nome, c.cargo, c.email, c.telefone, c.celular,
                                     (SELECT GROUP_CONCAT(d.nome ORDER BY d.nome SEPARATOR ', ') FROM contatos_departamentos cd
                                        JOIN departamentos d ON d.id = cd.departamento_id WHERE cd.contato_id = c.id) AS deptos
                                FROM contatos_clientes c""", fetch=True) or []:
        existentes.setdefault(c['cliente_id'], []).append(c)

    grupos = agrupar(ler(a.json))
    tot, linhas = Counter(), []
    print(f"{'APLICANDO' if a.apply else 'DRY-RUN'}: {len(grupos)} contatos (de 857 linhas) para {len({g['num'] for g in grupos})} empresas")
    for g in grupos:
        cli = clientes.get(g['num'])
        rot = f"{g['num']} {g['nome'][:30]}"
        if not cli:
            tot['empresa não está no app (número)'] += 1
            linhas.append((g['num'], '', g['nome'], g['email'] or '', g['celular'] or g['telefone'] or '', '', 'empresa não está no app'))
            continue
        if not g['email'] and not g['celular'] and not g['telefone']:
            # 05/11/2026 (Anderson, vendo a aba): "Esses que estão dessa forma nem tem que aparecer aqui...
            # Só quem tem e-mail e celular ou um ou outro." Contato só com nome não entra.
            tot['ignorado: só nome (sem e-mail e sem telefone)'] += 1
            linhas.append((g['num'], cli['nome_razao_social'][:40], g['nome'], '', '', '', 'ignorado: só nome'))
            continue
        meus = existentes.get(cli['id'], [])
        ex = next((c for c in meus if g['email'] and (c['email'] or '').strip().lower() == g['email']), None) \
            or (next((c for c in meus if not g['email'] and (c['nome'] or '').strip().upper() == g['nome'].upper()), None))
        deptos_ids = [deps[n] for n in DEPTOS.values() if n in g['deptos']]
        deptos_txt = ', '.join(n for n in DEPTOS.values() if n in g['deptos']) or '(nenhum)'
        if ex:
            antes = {k: ex[k] for k in ('cargo', 'celular', 'telefone')}
            novos = {'cargo': ex['cargo'] or g['cargo'], 'celular': ex['celular'] or g['celular'], 'telefone': ex['telefone'] or g['telefone']}
            mudou_campos = {k: v for k, v in novos.items() if v != antes[k]}
            deptos_antes = ex['deptos'] or ''
            # 2ª rodada de 05/11 marcou 761 'completado: departamentos' falsos: o GROUP_CONCAT vinha com ','
            # e aqui se comparava com ', '. Agora os dois lados usam ', ' e a mesma ordem.
            mudou_deptos = sorted((deptos_antes or '').split(', ')) != sorted(g['deptos']) if (deptos_antes or g['deptos']) else False
            if not mudou_campos and not mudou_deptos:
                tot['já existia igual'] += 1
                continue
            tot['completado (já existia)'] += 1
            linhas.append((g['num'], cli['nome_razao_social'][:40], g['nome'], g['email'] or '', g['celular'] or g['telefone'] or '', deptos_txt, 'completado: ' + ', '.join(list(mudou_campos) + (['departamentos'] if mudou_deptos else []))))
            if a.apply:
                execute_query('UPDATE contatos_clientes SET cargo = %s, celular = %s, telefone = %s WHERE id = %s',
                              (novos['cargo'], novos['celular'], novos['telefone'], ex['id']), fetch=False)
                ContatoCliente.set_departamentos(ex['id'], deptos_ids)
                registrar_agente('escrita.contato_acessorias_migracao', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                                 tabela='contatos_clientes', registro_id=ex['id'],
                                 depois={'cliente_id': cli['id'], 'contato': ex['nome'], 'acao': 'completado',
                                         'campos': mudou_campos, 'departamentos_antes': deptos_antes, 'departamentos': deptos_txt, 'fonte': 'Acessórias'})
            continue
        tot['criado'] += 1
        for av in g['avisos']:
            tot['aviso: ' + av.split('"')[0].strip()] += 1
        linhas.append((g['num'], cli['nome_razao_social'][:40], g['nome'], g['email'] or '', g['celular'] or g['telefone'] or '', deptos_txt, 'criado' + ('; ' + '; '.join(g['avisos']) if g['avisos'] else '')))
        if not g['email'] and not g['celular'] and not g['telefone']:
            tot['criado SEM e-mail e SEM telefone (só nome)'] += 1
        elif not g['celular']:
            tot['criado sem celular (pendência)'] += 1
        if a.apply:
            cid = ContatoCliente.create(cli['id'], g['nome'], cargo=g['cargo'], email=g['email'], telefone=g['telefone'],
                                        celular=g['celular'], departamentos=deptos_ids)
            # o contato novo entra no dicionário para a 2ª rodada deste mesmo script ser idempotente
            existentes.setdefault(cli['id'], []).append({'id': cid, 'cliente_id': cli['id'], 'nome': g['nome'].upper(), 'cargo': g['cargo'],
                                                          'email': g['email'], 'telefone': g['telefone'], 'celular': g['celular'],
                                                          'deptos': ', '.join(sorted(g['deptos']))})
            registrar_agente('escrita.contato_acessorias_migracao', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='contatos_clientes', registro_id=cid,
                             depois={'cliente_id': cli['id'], 'contato': g['nome'], 'acao': 'criado', 'email': g['email'],
                                     'celular': g['celular'], 'telefone': g['telefone'], 'departamentos': deptos_txt,
                                     'origem': '/'.join(sorted(o for o in g['origem'] if o)), 'avisos': g['avisos'] or None})
    print('TOTAIS:')
    for k, v in tot.most_common():
        print(f'  {v:4} {k}')
    if a.csv:
        with open(a.csv, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['nº', 'empresa', 'contato', 'e-mail', 'celular/telefone', 'departamentos', 'ação'])
            w.writerows(linhas)
        print('CSV:', a.csv)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    main()
