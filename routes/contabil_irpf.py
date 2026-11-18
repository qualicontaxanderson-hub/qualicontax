# -*- coding: utf-8 -*-
"""Contábil › Imposto de Renda PF — as declarações lidas do programa da Receita.

Rotas no blueprint ``contabil`` (nasce com Ver/Incluir/Alterar/Excluir no catálogo e no MAPA):
- /contabil/irpf/                       lista por exercício, com KPIs e filtros
- /contabil/irpf/<id>                   a declaração inteira (rendimentos, bens, dependentes...)
- /contabil/irpf/enviar          POST   .DEC/.REC pela tela (quem não tem o Q-Colabore)
- /contabil/irpf/<id>/vincular   POST   liga a uma pessoa do cadastro
- /contabil/irpf/<id>/criar-avulso POST cria o avulso PF com os dados da declaração e vincula
- /contabil/irpf/<id>/reler      POST   lê o arquivo guardado de novo
- /contabil/irpf/<id>/excluir    POST   apaga a leitura (o arquivo fica no Dropbox)
- /contabil/irpf/<id>/arquivo/<qual>    baixa o .DEC/.REC/PDF guardado
"""
import logging
import os
import re
from datetime import date

from flask import render_template, request, redirect, url_for, flash, abort, send_file
from flask_login import current_user

from routes.contabil import contabil
from utils.auth_helper import permission_required
from utils.db_helper import execute_query
from utils.atividade import registrar
from utils.irpf import ingest, tabelas, leitor

logger = logging.getLogger(__name__)


def _csrf_ok():
    from routes.qrobo import csrf_valido
    return csrf_valido()


def _csrf():
    from routes.qrobo import csrf_token
    return csrf_token()


def _q(sql, params=None, one=False):
    return execute_query(sql, params, fetch=True, fetch_one=one) or ({} if one else [])


def _dig(s):
    return re.sub(r'\D', '', str(s or ''))


def _exercicios():
    return [int(r['exercicio']) for r in _q("SELECT DISTINCT exercicio FROM irpf_declaracoes ORDER BY exercicio DESC")]


def _pode(perm):
    return current_user.is_admin() or current_user.has_permission(perm)


# ---------------------------------------------------------------------------
# Lista
# ---------------------------------------------------------------------------
@contabil.route('/irpf/')
@permission_required('contabil.irpf')
def irpf():
    exs = _exercicios()
    ex = request.args.get('ex', type=int) or (exs[0] if exs else date.today().year)
    sit = (request.args.get('sit') or 'todas').strip()
    modelo = (request.args.get('modelo') or '').strip()
    cad = (request.args.get('cad') or '').strip()
    busca = (request.args.get('q') or '').strip()
    cond, params = ['d.exercicio = %s'], [ex]
    if sit in ('restituir', 'pagar', 'zero'):
        cond.append('d.resultado = %s'); params.append(sit)
    elif sit == 'sem_cadastro':
        cond.append('d.cliente_id IS NULL')
    elif sit == 'avulsos':
        cond.append('COALESCE(c.avulso,0) = 1')
    if sit == 'retificadas':
        cond.append('d.vigente = 0')
    else:
        cond.append('d.vigente = 1')
    if modelo in ('completa', 'simplificada'):
        cond.append('d.completa = %s'); params.append(1 if modelo == 'completa' else 0)
    if cad == 'cliente':
        cond.append('c.id IS NOT NULL AND COALESCE(c.avulso,0) = 0')
    elif cad == 'avulso':
        cond.append('COALESCE(c.avulso,0) = 1')
    elif cad == 'sem':
        cond.append('d.cliente_id IS NULL')
    if busca:
        dgt = _dig(busca)
        if dgt and len(dgt) >= 3:
            cond.append('(d.cpf LIKE %s OR d.nome LIKE %s)'); params += [f'%{dgt}%', f'%{busca}%']
        else:
            cond.append('d.nome LIKE %s'); params.append(f'%{busca}%')
    linhas = _q(f"""SELECT d.*, c.nome_razao_social AS cad_nome, c.numero_cliente, c.avulso AS cad_avulso,
                           (SELECT COUNT(*) FROM irpf_dependentes x WHERE x.declaracao_id = d.id) n_dep
                    FROM irpf_declaracoes d LEFT JOIN clientes c ON c.id = d.cliente_id
                    WHERE {' AND '.join(cond)} ORDER BY d.transmitida_em DESC, d.nome, d.id DESC LIMIT 1000""", tuple(params))
    # grupos por dia de entrega (molde das Cobranças: data em pílula + total do dia)
    dias = {'Seg': 'segunda', 'Ter': 'terça', 'Qua': 'quarta', 'Qui': 'quinta', 'Sex': 'sexta', 'Sáb': 'sábado', 'Dom': 'domingo'}
    semana = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']
    grupos, idx = [], {}
    for d in linhas:
        k = d['transmitida_em'].date() if d.get('transmitida_em') else None
        if k not in idx:
            idx[k] = {'data': k.strftime('%d/%m') if k else 'sem recibo', 'sub': (semana[k.weekday()] + ' · ' + k.strftime('%Y')) if k else 'o .REC ainda não chegou',
                      'itens': [], 'n': 0, 'rest': 0, 'pag': 0}
            grupos.append(idx[k])
        g = idx[k]; g['itens'].append(d); g['n'] += 1
        g['rest'] += float(d['vr_restituir'] or 0) if d['resultado'] == 'restituir' else 0
        g['pag'] += float(d['vr_pagar'] or 0) if d['resultado'] == 'pagar' else 0
    k = _q("""SELECT COUNT(*) total, SUM(completa=1) completas,
                     SUM(resultado='restituir') n_rest, COALESCE(SUM(CASE WHEN resultado='restituir' THEN vr_restituir END),0) v_rest,
                     SUM(resultado='pagar') n_pag, COALESCE(SUM(CASE WHEN resultado='pagar' THEN vr_pagar END),0) v_pag,
                     SUM(resultado='pagar' AND COALESCE(quotas,1) > 1) n_quotas,
                     SUM(resultado='zero') n_zero,
                     SUM(cliente_id IS NULL) sem_cad, SUM(transmitida_em IS NULL) sem_recibo,
                     SUM(vinculo='avulso_novo') avulsos_novos,
                     COALESCE(SUM(vr_bens_atual),0) bens, COALESCE(SUM(vr_tributaveis),0) trib
              FROM irpf_declaracoes WHERE exercicio=%s AND vigente=1""", (ex,), one=True)
    retificadas = _q("SELECT COUNT(*) n FROM irpf_declaracoes WHERE exercicio=%s AND vigente=0", (ex,), one=True).get('n') or 0
    n_cli = _q("SELECT SUM(COALESCE(c.avulso,0)=0) cli, SUM(COALESCE(c.avulso,0)=1) av FROM irpf_declaracoes d JOIN clientes c ON c.id=d.cliente_id "
               "WHERE d.exercicio=%s AND d.vigente=1", (ex,), one=True)
    return render_template('contabil/irpf/index.html', grupos=grupos, n_linhas=len(linhas), k=k, ex=ex, exs=exs, sit=sit, busca=busca,
                           modelo=modelo, cad=cad, n_cli=int(n_cli.get('cli') or 0), n_av=int(n_cli.get('av') or 0),
                           retificadas=retificadas, csrf=_csrf(), tab=tabelas, hoje=date.today(),
                           pode_inc=_pode('contabil.irpf.incluir'), pode_alt=_pode('contabil.irpf.alterar'))


# ---------------------------------------------------------------------------
# Detalhe
# ---------------------------------------------------------------------------
def _decl_ou_404(decl_id):
    d = _q("""SELECT d.*, c.nome_razao_social AS cad_nome, c.numero_cliente, c.avulso AS cad_avulso, c.tipo_pessoa AS cad_tipo
              FROM irpf_declaracoes d LEFT JOIN clientes c ON c.id = d.cliente_id WHERE d.id=%s""", (decl_id,), one=True)
    if not d:
        abort(404)
    return d


GRUPO_ICONE = {'01': 'house', '02': 'car', '03': 'building', '04': 'chart-line', '05': 'file-signature', '06': 'wallet',
               '07': 'layer-group', '08': 'coins', '99': 'box-open'}
GRUPO_COR = {'01': '#2563eb', '02': '#0891b2', '03': '#7c3aed', '04': '#16a34a', '05': '#ca8a04', '06': '#f97316',
             '07': '#0d9488', '08': '#db2777', '99': '#64748b'}


def _nomes_do_cadastro(docs):
    """{digitos: {'id', 'nome_razao_social', 'avulso', 'numero_cliente'}} dos CNPJ/CPF que existem no cadastro."""
    docs = {_dig(x) for x in docs if x and len(_dig(x)) in (11, 14)}
    if not docs:
        return {}
    from models.cliente import Cliente
    idx = Cliente.index_cpf_cnpj(com_avulsos=True)
    ids = {d: idx[d] for d in docs if d in idx}
    if not ids:
        return {}
    rows = _q(f"SELECT id, nome_razao_social, avulso, numero_cliente FROM clientes WHERE id IN ({','.join(['%s'] * len(ids))})", tuple(ids.values()))
    por_id = {r['id']: r for r in rows}
    return {d: por_id[i] for d, i in ids.items() if i in por_id}


def _idade(nasc, ref):
    if not nasc or not ref:
        return None
    return ref.year - nasc.year - ((ref.month, ref.day) < (nasc.month, nasc.day))


def _pct(parte, todo):
    try:
        v = round(float(parte) * 100 / float(todo), 1) if todo else 0
    except Exception:
        return 0
    return int(v) if v == int(v) else v


def _chave(v):
    return str(v or '').strip().lstrip('0')


@contabil.route('/irpf/<int:decl_id>')
@permission_required('contabil.irpf')
def irpf_declaracao(decl_id):
    d = _decl_ou_404(decl_id)
    ano_base = int(d.get('ano_base') or (d['exercicio'] - 1))
    fim_ano = date(ano_base, 12, 31)
    rend = _q("SELECT * FROM irpf_rendimentos WHERE declaracao_id=%s ORDER BY bloco, valor DESC, id", (decl_id,))
    deps = _q("SELECT * FROM irpf_dependentes WHERE declaracao_id=%s ORDER BY id", (decl_id,))
    bens = _q("SELECT * FROM irpf_bens WHERE declaracao_id=%s ORDER BY grupo, vr_atual DESC, id", (decl_id,))
    divs = _q("SELECT * FROM irpf_dividas WHERE declaracao_id=%s ORDER BY vr_atual DESC, id", (decl_id,))
    pags = _q("SELECT * FROM irpf_pagamentos WHERE declaracao_id=%s ORDER BY codigo, valor DESC, id", (decl_id,))
    cad = _nomes_do_cadastro([x.get('fonte_doc') for x in rend] + [x.get('cpf_cnpj') for x in bens]
                             + [x.get('beneficiario_doc') for x in pags] + [x.get('cpf') for x in deps])
    dep_por_chave = {_chave(x.get('chave')): x for x in deps}
    dep_por_cpf = {x['cpf']: x for x in deps if x.get('cpf')}
    for x in deps:
        x['rotulo'] = tabelas.dependente(x['codigo']).rstrip('.'); x['idade'] = _idade(x.get('nascimento'), fim_ano)
        x['ini'] = ''.join(p[0] for p in (x.get('nome') or '?').split()[:2])
        x['cad'] = cad.get(_dig(x.get('cpf')))
        meus = [p for p in pags if p.get('tipo') == 'D' and _chave(p.get('dependente_chave')) == _chave(x.get('chave'))]
        x['n_pag'] = len(meus); x['v_pag'] = sum((p['valor'] or 0) for p in meus)
        x['v_rend'] = sum((r['valor'] or 0) for r in rend if r.get('dependente_cpf') and r['dependente_cpf'] == x.get('cpf'))
    blocos = []
    for b in ('pj_titular', 'pj_dependente', 'pf_exterior', 'isento', 'exclusivo', 'exclusivo_detalhe'):
        itens = [r for r in rend if r['bloco'] == b]
        if not itens:
            continue
        total = sum((r['valor'] or 0) for r in itens)
        for r in itens:
            if b == 'isento':
                r['rotulo'] = tabelas.isento(r['codigo']).rstrip('.')
            elif b in ('exclusivo', 'exclusivo_detalhe'):
                r['rotulo'] = tabelas.exclusivo(r['codigo']).rstrip('.')
            elif b == 'pf_exterior':
                r['rotulo'] = ('Mês %02d/%s' % (int(r['mes']), ano_base)) if r.get('mes') else 'Mês'
            else:
                r['rotulo'] = r.get('fonte_nome') or '-'
            r['cad'] = cad.get(_dig(r.get('fonte_doc')))
            r['dep'] = dep_por_cpf.get(r.get('dependente_cpf'))
            r['pct'] = _pct(r['valor'] or 0, total)
            r['ret_pct'] = _pct(r.get('imposto_retido') or 0, r['valor'] or 0) if r.get('imposto_retido') else 0
            r['liquido'] = ((r['valor'] or 0) - (r.get('previdencia') or 0) - (r.get('imposto_retido') or 0)) if b in ('pj_titular', 'pj_dependente') else None
        blocos.append({'chave': b, 'titulo': tabelas.BLOCOS[b], 'itens': itens, 'total': total,
                       'irrf': sum((r['imposto_retido'] or 0) for r in itens), 'prev': sum((r.get('previdencia') or 0) for r in itens),
                       'dec': sum((r.get('decimo_terceiro') or 0) for r in itens)})
    tot_bens = sum((b['vr_atual'] or 0) for b in bens); tot_bens_ant = sum((b['vr_anterior'] or 0) for b in bens)
    grupos, por_grupo = [], {}
    for b in bens:
        b['rotulo'] = tabelas.bem(b['grupo'], b['codigo']); b['grupo_nome'] = tabelas.grupo_bem(b['grupo'])
        b['pais_nome'] = tabelas.pais(b['pais']) if b.get('exterior') else None
        b['cad'] = cad.get(_dig(b.get('cpf_cnpj')))
        b['delta'] = (b['vr_atual'] or 0) - (b['vr_anterior'] or 0)
        b['novo'] = not (b['vr_anterior'] or 0) and (b['vr_atual'] or 0) > 0
        b['saiu'] = (b['vr_anterior'] or 0) > 0 and not (b['vr_atual'] or 0)
        b['pct'] = _pct(b['vr_atual'] or 0, tot_bens)
        b['icone'] = GRUPO_ICONE.get(b['grupo'] or '', 'box-open'); b['cor'] = GRUPO_COR.get(b['grupo'] or '', '#64748b')
        g = por_grupo.get(b['grupo'])
        if not g:
            g = por_grupo[b['grupo']] = {'codigo': b['grupo'], 'nome': b['grupo_nome'], 'icone': b['icone'], 'cor': b['cor'], 'itens': [], 'atual': 0, 'anterior': 0}
            grupos.append(g)
        g['itens'].append(b); g['atual'] += (b['vr_atual'] or 0); g['anterior'] += (b['vr_anterior'] or 0)
    for g in grupos:
        g['pct'] = _pct(g['atual'], tot_bens); g['delta'] = g['atual'] - g['anterior']
    grupos.sort(key=lambda g: -g['atual'])
    pag_grupos, por_cod = [], {}
    for x in pags:
        x['rotulo'] = tabelas.pagamento(x['codigo']).rstrip('.'); x['para'] = tabelas.PAGAMENTO_TIPO.get(x['tipo'] or '', '')
        x['dep'] = dep_por_chave.get(_chave(x.get('dependente_chave'))) if x.get('tipo') == 'D' else None
        x['cad'] = cad.get(_dig(x.get('beneficiario_doc')))
        x['dedutivel'] = (x['valor'] or 0) - (x.get('vr_nao_dedutivel') or 0)
        g = por_cod.get(x['codigo'])
        if not g:
            g = por_cod[x['codigo']] = {'codigo': x['codigo'], 'nome': x['rotulo'], 'itens': [], 'total': 0, 'nao_ded': 0}
            pag_grupos.append(g)
        g['itens'].append(x); g['total'] += (x['valor'] or 0); g['nao_ded'] += (x.get('vr_nao_dedutivel') or 0)
    tot_pag = sum(g['total'] for g in pag_grupos)
    for g in pag_grupos:
        g['pct'] = _pct(g['total'], tot_pag)
    pag_grupos.sort(key=lambda g: -g['total'])
    for x in divs:
        x['rotulo'] = tabelas.divida(x['codigo']).rstrip('.'); x['delta'] = (x['vr_atual'] or 0) - (x['vr_anterior'] or 0)
    renda_total = (d['vr_tributaveis'] or 0) + (d['vr_isentos'] or 0) + (d['vr_exclusivos'] or 0)
    var_bens = (d['vr_bens_atual'] or 0) - (d['vr_bens_anterior'] or 0)
    var_div = sum(x['delta'] for x in divs)
    leitura = {'renda': renda_total, 'var_bens': var_bens, 'var_div': var_div, 'liquido': var_bens - var_div,
               'pct': _pct(var_bens - var_div, renda_total) if renda_total else None,
               'alerta': renda_total > 0 and (var_bens - var_div) > renda_total}
    outras = _q("SELECT id, exercicio, retificadora, vigente, resultado, vr_restituir, vr_pagar, transmitida_em, vr_bens_atual, vr_tributaveis "
                "FROM irpf_declaracoes WHERE cpf=%s AND id<>%s ORDER BY exercicio DESC, id DESC", (d['cpf'], decl_id))
    sugestoes = []
    if not d['cliente_id']:
        cid = ingest.cliente_por_cpf(d['cpf'])
        if cid:
            sugestoes = _q("SELECT id, nome_razao_social, cpf_cnpj, avulso, tipo_pessoa FROM clientes WHERE id=%s", (cid,))
        elif d.get('nome'):
            primeiro = d['nome'].split()[0]
            sugestoes = _q("SELECT id, nome_razao_social, cpf_cnpj, avulso, tipo_pessoa FROM clientes WHERE tipo_pessoa='PF' "
                           "AND nome_razao_social LIKE %s ORDER BY nome_razao_social LIMIT 8", (primeiro + '%',))
    return render_template('contabil/irpf/declaracao.html', d=d, blocos=blocos, deps=deps, bens=bens, grupos=grupos, divs=divs,
                           pags=pags, pag_grupos=pag_grupos, tot_pag=tot_pag, outras=outras, sugestoes=sugestoes, csrf=_csrf(), tab=tabelas,
                           tot_bens=tot_bens, tot_bens_ant=tot_bens_ant, leitura=leitura, ano_base=ano_base,
                           pode_alt=_pode('contabil.irpf.alterar'), pode_exc=_pode('contabil.irpf.excluir'))


# ---------------------------------------------------------------------------
# Ações
# ---------------------------------------------------------------------------
@contabil.route('/irpf/enviar', methods=['POST'])
@permission_required('contabil.irpf.incluir')
def irpf_enviar():
    if not _csrf_ok():
        flash('O formulário expirou. Tente de novo.', 'warning'); return redirect(url_for('contabil.irpf'))
    arquivos = [f for f in request.files.getlist('arquivos') if f and f.filename]
    if not arquivos:
        flash('Escolha ao menos um arquivo .DEC ou .REC.', 'warning'); return redirect(url_for('contabil.irpf'))
    novas, repetidas, recibos, esperando, erros, ultimo_id, ex = 0, 0, 0, 0, [], None, None
    decs = sorted(arquivos, key=lambda f: not f.filename.lower().endswith('.dec'))      # .DEC antes do .REC
    for f in decs:
        nome = os.path.basename(f.filename)
        ext = os.path.splitext(nome)[1].lower()
        conteudo = f.read()
        if ext == '.dec':
            r = ingest.gravar_dec(conteudo, nome, origem='upload', usuario_id=current_user.id)
            if r['situacao'] == 'nova':
                novas += 1; ultimo_id = r['id']; ex = r['resumo']['exercicio']
                registrar('escrita.irpf_lida', 'contabil', tabela='irpf_declaracoes', registro_id=r['id'],
                          depois={'exercicio': ex, 'origem': 'upload', 'vinculo': r['resumo']['vinculo']})
            elif r['situacao'] == 'repetida':
                repetidas += 1; ultimo_id = r['id']
            else:
                erros.append(f"{nome}: {r['mensagem']}")
        elif ext == '.rec':
            r = ingest.gravar_rec(conteudo, nome)
            if r['situacao'] == 'ok':
                recibos += 1
            elif r['situacao'] == 'aguardando':
                esperando += 1
            else:
                erros.append(f"{nome}: {r['mensagem']}")
        else:
            erros.append(f'{nome}: só .DEC e .REC por enquanto (o PDF da declaração vem na próxima etapa).')
    partes = []
    if novas:
        partes.append(f'{novas} declaração{"" if novas == 1 else "ões"} lida{"" if novas == 1 else "s"}')
    if recibos:
        partes.append(f'{recibos} recibo{"" if recibos == 1 else "s"}')
    if repetidas:
        partes.append(f'{repetidas} já estava{"" if repetidas == 1 else "m"} no sistema')
    if esperando:
        partes.append(f'{esperando} recibo{"" if esperando == 1 else "s"} sem a declaração (.DEC) correspondente')
    if partes:
        flash('. '.join(partes) + '.', 'success' if (novas or recibos) else 'info')
    for e in erros[:5]:
        flash(e, 'danger')
    if novas == 1 and ultimo_id and not erros:
        return redirect(url_for('contabil.irpf_declaracao', decl_id=ultimo_id))
    return redirect(url_for('contabil.irpf', ex=ex) if ex else url_for('contabil.irpf'))


@contabil.route('/irpf/<int:decl_id>/vincular', methods=['POST'])
@permission_required('contabil.irpf.alterar')
def irpf_vincular(decl_id):
    d = _decl_ou_404(decl_id)
    if not _csrf_ok():
        flash('O formulário expirou. Tente de novo.', 'warning'); return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))
    cid = request.form.get('cliente_id', type=int)
    c = _q("SELECT id, nome_razao_social, cpf_cnpj FROM clientes WHERE id=%s", (cid,), one=True) if cid else None
    if not c:
        flash('Escolha a pessoa do cadastro.', 'warning'); return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))
    if _dig(c['cpf_cnpj']) and _dig(c['cpf_cnpj']) != d['cpf'] and request.form.get('confirmo') != '1':
        flash(f"O CPF do cadastro ({c['cpf_cnpj']}) é diferente do CPF da declaração. Marque \"sei que o CPF é diferente\" para vincular mesmo assim.", 'warning')
        return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))
    ingest.vincular(decl_id, c['id'], manual=True)
    registrar('escrita.irpf_vinculada', 'contabil', tabela='irpf_declaracoes', registro_id=decl_id,
              antes={'cliente_id': d['cliente_id']}, depois={'cliente_id': c['id']})
    flash(f"Declaração vinculada a {c['nome_razao_social']}.", 'success')
    return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))


@contabil.route('/irpf/<int:decl_id>/criar-avulso', methods=['POST'])
@permission_required('contabil.irpf.alterar')
def irpf_criar_avulso(decl_id):
    d = _decl_ou_404(decl_id)
    if not _csrf_ok():
        flash('O formulário expirou. Tente de novo.', 'warning'); return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))
    if d['cliente_id']:
        flash('Esta declaração já tem cadastro.', 'info'); return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))
    try:
        cid = ingest.criar_avulso(d, usuario_id=current_user.id)
    except Exception as exc:
        logger.exception('[irpf] criar avulso falhou')
        flash(f'Não consegui criar o avulso: {exc}', 'danger'); return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))
    ingest.vincular(decl_id, cid, manual=True)
    registrar('escrita.irpf_avulso_criado', 'contabil', tabela='clientes', registro_id=cid,
              depois={'declaracao_id': decl_id, 'nome': d.get('nome')})
    flash('Avulso criado com os dados da declaração e vinculado. Complete o cadastro quando puder.', 'success')
    return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))


@contabil.route('/irpf/<int:decl_id>/reler', methods=['POST'])
@permission_required('contabil.irpf.alterar')
def irpf_reler(decl_id):
    _decl_ou_404(decl_id)
    if not _csrf_ok():
        flash('O formulário expirou. Tente de novo.', 'warning'); return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))
    r = ingest.reler(decl_id)
    flash(r['mensagem'], 'success' if r['situacao'] == 'ok' else 'danger')
    return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))


@contabil.route('/irpf/<int:decl_id>/excluir', methods=['POST'])
@permission_required('contabil.irpf.excluir')
def irpf_excluir(decl_id):
    d = _decl_ou_404(decl_id)
    if not _csrf_ok():
        flash('O formulário expirou. Tente de novo.', 'warning'); return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))
    ingest.excluir(decl_id)
    registrar('escrita.irpf_excluida', 'contabil', tabela='irpf_declaracoes', registro_id=decl_id,
              antes={'cpf': d['cpf'], 'exercicio': d['exercicio'], 'arquivo': d.get('arquivo_dec')})
    flash('Leitura excluída. O arquivo continua guardado no Dropbox.', 'success')
    return redirect(url_for('contabil.irpf', ex=d['exercicio']))


@contabil.route('/irpf/<int:decl_id>/arquivo/<qual>')
@permission_required('contabil.irpf')
def irpf_arquivo(decl_id, qual):
    d = _decl_ou_404(decl_id)
    col = {'dec': 'arquivo_dec', 'rec': 'arquivo_rec', 'pdf': 'arquivo_pdf'}.get(qual)
    path = d.get(col) if col else None
    if not path:
        abort(404)
    conteudo = ingest._svc().download_file(path)
    if not conteudo:
        flash('Não consegui baixar o arquivo do Dropbox agora.', 'danger')
        return redirect(url_for('contabil.irpf_declaracao', decl_id=decl_id))
    import io
    return send_file(io.BytesIO(conteudo), as_attachment=True, download_name=os.path.basename(path),
                     mimetype='application/pdf' if qual == 'pdf' else 'text/plain')


# ---------------------------------------------------------------------------
# Home do Contábil
# ---------------------------------------------------------------------------
def resumo_para_home():
    """{'counter': n, 'cards': [...]} para o carrossel/contadores da home. Sem dado, sem cartão."""
    exs = _exercicios()
    if not exs:
        return {'counter': 0, 'cards': []}
    ex = exs[0]
    k = _q("""SELECT COUNT(*) total, SUM(resultado='restituir') n_rest, SUM(resultado='pagar') n_pag,
                     SUM(cliente_id IS NULL) sem_cad, COALESCE(SUM(vr_restituir),0) v_rest, COALESCE(SUM(vr_pagar),0) v_pag
              FROM irpf_declaracoes WHERE exercicio=%s AND vigente=1""", (ex,), one=True)
    total = int(k.get('total') or 0)
    cards = [{'titulo': f'Declarações IRPF {ex}', 'icone': 'fa-file-invoice', 'valor': total,
              'apoio': f"{int(k.get('n_rest') or 0)} a restituir · {int(k.get('n_pag') or 0)} a pagar · {int(k.get('sem_cad') or 0)} sem cadastro",
              'trend': {'tipo': 'neutro', 'rotulo': 'lidas'}}]
    if k.get('v_rest'):
        cards.append({'titulo': 'A restituir (R$)', 'icone': 'fa-hand-holding-dollar', 'valor': int(round(float(k['v_rest']))),
                      'apoio': f"soma das declarações {ex} com restituição", 'trend': {'tipo': 'neutro', 'rotulo': 'soma'}})
    if k.get('v_pag'):
        cards.append({'titulo': 'A pagar (R$)', 'icone': 'fa-money-bill-transfer', 'valor': int(round(float(k['v_pag']))),
                      'apoio': f"soma das declarações {ex} com imposto a pagar", 'trend': {'tipo': 'neutro', 'rotulo': 'soma'}})
    return {'counter': total, 'cards': cards}
