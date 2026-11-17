# -*- coding: utf-8 -*-
"""Cobrança do GRUPO de empresas (rede), de uma vez — 17/11/2026.

Anderson: "estamos pesquisando por rede ATRASADOS, GRUPO M6... com essa seleção conseguimos enviar as Página do
Cliente de uma vez para o cliente... e não um a um!!! Quando for esses clientes de rede"; "o cliente tem uma
empresa Atrasada e outras Inadimplentes... queria mandar tudo no mesmo Página do Cliente... os inadimplentes, os
atrasados e até a vencer se tiver em aberto... e até os perdidos. A única coisa que tem que barrar seria se não
tiver a cobrança ativa, por exemplo o boleto está expirado... veja como resolver isso".

Como funciona:
- ``levantar(grupo_id)``: todas as empresas do grupo com honorário em aberto (a vencer, atrasado, inadimplente
  e perdido com boleto ainda ativo), cada honorário com o seu boleto mais novo e o que acontece com ele:
  ``na_pagina`` (boleto aguardando ou vencido: entra na página do grupo), ``expirado`` (precisa reemitir),
  ``sem_boleto`` (precisa emitir), ``pago``/``identificado`` (já resolvido, só não baixou ainda).
- ``pagina(grupo_id)``: UM link por grupo (/g/<token>, fin_paginas_grupo). A página é viva: na hora em que o
  cliente abre, lista os boletos ativos daquele momento, cada um com Pix, linha digitável e PDF.
- ``texto_whatsapp``: um texto só, com todas as empresas e o link do grupo.
- ``enviar_email``: um e-mail só, com a lista e o botão da página do grupo; carimba email_enviado_em em cada
  boleto que foi na página (a linha do tempo de cada honorário mostra o envio).
- Os contatos da rede são a união dos contatos das empresas, sem repetir e-mail/celular, com a lista de
  empresas em que cada um aparece; Financeiro primeiro.

O que fica de fora da página (boleto expirado, sem boleto) aparece na janela com o botão para resolver:
"Reemitir os expirados" com um vencimento só (routes/financeiro_grupo_cobranca.py).
"""
import secrets
from collections import OrderedDict, defaultdict
from datetime import date, datetime

from utils import titulos_receber as TR
from utils.cobranca_email import APP_URL, _brl, _data, _doc_fmt, _empresa, _itens, _telefone_empresa, garantir_token
from utils.db_helper import execute_query

ATIVOS = ('waiting', 'new', 'unpaid')          # boleto que o cliente ainda pode pagar: entra na página
_PRIORIDADE = {'waiting': 0, 'new': 0, 'unpaid': 0, 'identified': 1, 'paid': 2, 'settled': 2, 'expired': 3}


def _hoje():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo('America/Sao_Paulo')).date()


def grupo(grupo_id):
    return execute_query('SELECT id, nome, situacao FROM grupos_clientes WHERE id = %s', (grupo_id,), fetch=True, fetch_one=True)


def ids_dos_grupos(nomes):
    """{nome: id} dos grupos pelo nome (o catálogo dos filtros só tem o nome)."""
    nomes = [n for n in set(nomes or []) if n]
    if not nomes:
        return {}
    rows = execute_query(f"SELECT id, nome FROM grupos_clientes WHERE nome IN ({','.join(['%s'] * len(nomes))})",
                         tuple(nomes), fetch=True) or []
    return {r['nome']: r['id'] for r in rows}


def _boleto_do_titulo(boletos):
    """O boleto que vale para o honorário: o ativo mais novo; senão identificado/pago; senão o expirado mais novo."""
    if not boletos:
        return None
    return sorted(boletos, key=lambda b: (_PRIORIDADE.get(b['situacao'], 9), -b['id']))[0]


def levantar(grupo_id, hoje=None, empresa_ids=None):
    """Tudo o que a janela e a página do grupo precisam. Devolve None se o grupo não existe."""
    from models.fin_empresa import FinEmpresa
    hoje = hoje or _hoje()
    g = grupo(grupo_id)
    if not g:
        return None
    validos = set(empresa_ids or FinEmpresa.ids_validos())
    cids = [r['cliente_id'] for r in execute_query('SELECT cliente_id FROM cliente_grupo_relacao WHERE grupo_id = %s',
                                                   (grupo_id,), fetch=True) or []]
    saida = {'grupo': g, 'empresas': [], 'contatos': [], 'hoje': hoje, 'n_empresas_grupo': len(cids),
             'total': 0.0, 'total_pagina': 0.0, 'n_titulos': 0, 'n_pagina': 0, 'n_vencidos': 0, 'n_expirados': 0,
             'n_sem_boleto': 0, 'n_perdidos': 0, 'cls': 'tx-hoje', 'rotulo': 'a vencer'}
    if not cids:
        return saida
    marc = ','.join(['%s'] * len(cids))
    titulos = execute_query(
        f"""SELECT t.id, t.cliente_id, t.empresa_id, t.descricao, t.competencia, t.vencimento, t.status, t.perdido_em,
                   t.valor, t.valor_baixado, t.valor - t.valor_baixado AS saldo,
                   c.numero_cliente, c.nome_razao_social, c.nome_fantasia, c.cpf_cnpj, c.email, c.celular, c.telefone
              FROM fin_titulos t JOIN clientes c ON c.id = t.cliente_id
             WHERE t.tipo = 'R' AND t.cliente_id IN ({marc})
               AND (t.status IN ('aberto', 'parcial') OR (t.status = 'cancelado' AND t.perdido_em IS NOT NULL))
             ORDER BY c.numero_cliente, c.nome_razao_social, t.vencimento, t.id""", tuple(cids), fetch=True) or []
    titulos = [t for t in titulos if t['empresa_id'] in validos]
    bols = defaultdict(list)
    if titulos:
        tids = [t['id'] for t in titulos]
        for b in execute_query(f"""SELECT * FROM fin_boletos WHERE titulo_id IN ({','.join(['%s'] * len(tids))})
                                    AND situacao NOT IN ('erro', 'canceled', 'rascunho') ORDER BY id""",
                               tuple(tids), fetch=True) or []:
            bols[b['titulo_id']].append(b)

    empresas = OrderedDict()
    for t in titulos:
        b = _boleto_do_titulo(bols.get(t['id']))
        perdido = t['status'] == 'cancelado'
        saldo = float(t['saldo'] or 0)
        if b and b['situacao'] in ATIVOS:
            estado = 'na_pagina'
        elif b and b['situacao'] == 'expired':
            estado = 'expirado'
        elif b and b['situacao'] in ('paid', 'settled'):
            estado = 'pago'
        elif b and b['situacao'] == 'identified':
            estado = 'identificado'
        else:
            estado = 'sem_boleto'
        if perdido and estado != 'na_pagina':
            continue                       # perdido só interessa enquanto o boleto dele ainda pode ser pago
        venc = t['vencimento']
        vencido = bool(venc and venc < hoje) and not perdido
        tag, cls = TR._status(venc, hoje) if venc else ('', '')
        if perdido:
            tag, cls = 'perdido', 'tx-perd'
        pagina_boleto = ''
        if estado == 'na_pagina':
            tok = garantir_token(b)
            pagina_boleto = f'{APP_URL}/c/{tok}' if tok else ''
        item = {'id': t['id'], 'desc': TR._desc(t), 'competencia': TR._comp(t.get('competencia')), 'vencimento': venc,
                'venc_txt': venc.strftime('%d/%m/%Y') if venc else '—', 'saldo': saldo, 'valor_txt': TR.brl(saldo),
                'tag': tag, 'cls': cls, 'vencido': vencido, 'perdido': perdido, 'estado': estado, 'boleto': b,
                'bol': TR._BOL.get(b['situacao'], 'sem') if b else 'sem', 'pagina': pagina_boleto,
                'nfse_id': (b or {}).get('nfse_id')}
        e = empresas.get(t['cliente_id'])
        if not e:
            doc = TR._digitos(t['cpf_cnpj'])
            e = empresas[t['cliente_id']] = {
                'cliente_id': t['cliente_id'], 'numero': t['numero_cliente'], 'nome': TR._nome(t['nome_razao_social']),
                'fantasia': TR._nome(t['nome_fantasia'] or ''), 'doc': doc, 'doc_txt': _doc_fmt(doc), 'empresa_id': t['empresa_id'],
                'titulos': [], 'total': 0.0, 'total_pagina': 0.0, 'n_vencidos': 0, 'n_pagina': 0, 'n_fora': 0}
        e['titulos'].append(item)
        e['total'] += saldo
        if vencido:
            e['n_vencidos'] += 1
        if estado == 'na_pagina':
            e['n_pagina'] += 1
            e['total_pagina'] += saldo
        else:
            e['n_fora'] += 1
        saida['n_titulos'] += 1
        saida['total'] += saldo
        saida['n_vencidos'] += 1 if vencido else 0
        saida['n_perdidos'] += 1 if perdido else 0
        saida['n_expirados'] += 1 if estado == 'expirado' else 0
        saida['n_sem_boleto'] += 1 if estado == 'sem_boleto' else 0
        if estado == 'na_pagina':
            saida['n_pagina'] += 1
            saida['total_pagina'] += saldo

    for e in empresas.values():
        if e['n_vencidos'] >= 2:
            e['sit'], e['sit_cls'] = 'inadimplente', 'ina'
        elif e['n_vencidos'] == 1:
            e['sit'], e['sit_cls'] = 'atrasado', 'atr'
        elif any(t['perdido'] for t in e['titulos']) and all(t['perdido'] for t in e['titulos']):
            e['sit'], e['sit_cls'] = 'perdido', 'perd'
        else:
            e['sit'], e['sit_cls'] = 'a vencer', 'abe'
        e['total_txt'] = TR.brl(e['total'])
        e['total_pagina_txt'] = TR.brl(e['total_pagina'])
    saida['empresas'] = list(empresas.values())
    # a cor da janela: a pior situação da rede
    if any(e['sit_cls'] == 'ina' for e in saida['empresas']):
        saida['cls'], saida['rotulo'] = 'tx-ina', '2 ou mais vencidos'
    elif any(e['sit_cls'] == 'atr' for e in saida['empresas']):
        saida['cls'], saida['rotulo'] = 'tx-venc', 'vencidos'
    saida['total_txt'] = TR.brl(saida['total'])
    saida['total_pagina_txt'] = TR.brl(saida['total_pagina'])
    saida['contatos'] = contatos_da_rede([e['cliente_id'] for e in saida['empresas']],
                                         {e['cliente_id']: (e['fantasia'] or e['nome']) for e in saida['empresas']})
    return saida


def contatos_da_rede(cids, apelidos):
    """União dos contatos ativos das empresas, sem repetir pessoa (mesmo e-mail ou mesmo celular).
    Cada um sabe em quais empresas aparece; quem é do Financeiro em alguma vem primeiro e já marcado."""
    if not cids:
        return []
    marc = ','.join(['%s'] * len(cids))
    rows = execute_query(
        f"""SELECT c.id, c.cliente_id, c.nome, c.cargo, c.email, c.celular, c.principal,
                   EXISTS(SELECT 1 FROM contatos_departamentos cd WHERE cd.contato_id = c.id AND cd.departamento_id = 8) AS financeiro,
                   (SELECT GROUP_CONCAT(m.valor SEPARATOR ',') FROM contatos_meios m WHERE m.contato_id = c.id AND m.tipo = 'EMAIL') AS mais
              FROM contatos_clientes c WHERE c.cliente_id IN ({marc}) AND c.ativo = 1
             ORDER BY financeiro DESC, c.principal DESC, c.nome, c.id""", tuple(cids), fetch=True) or []
    pessoas, por_chave = [], {}
    for r in rows:
        emails = []
        for e in [r['email'] or ''] + (r['mais'] or '').split(','):
            e = e.strip().lower()
            if '@' in e and e not in emails:
                emails.append(e)
        cel = TR._cel(r['celular'])
        chaves = [('e', e) for e in emails] + ([('c', cel)] if cel else [])
        p = next((por_chave[k] for k in chaves if k in por_chave), None)
        if not p:
            if not chaves:
                continue                # sem e-mail e sem celular: não há como cobrar por aqui
            p = {'nome': TR._nome(r['nome']), 'cargo': r['cargo'] or '', 'emails': [], 'celular': '', 'cel': '',
                 'financeiro': False, 'principal': False, 'empresas': []}
            pessoas.append(p)
        for e in emails:
            if e not in p['emails']:
                p['emails'].append(e)
        if cel and not p['cel']:
            p['cel'], p['celular'] = cel, r['celular']
        p['financeiro'] = p['financeiro'] or bool(r['financeiro'])
        p['principal'] = p['principal'] or bool(r['principal'])
        ap = apelidos.get(r['cliente_id']) or str(r['cliente_id'])
        if ap not in p['empresas']:
            p['empresas'].append(ap)
        for k in chaves:
            por_chave.setdefault(k, p)
    tem_fin = any(p['financeiro'] for p in pessoas)
    for p in pessoas:
        p['padrao'] = p['financeiro'] if tem_fin else p['principal']
    if not any(p['padrao'] for p in pessoas):
        for p in pessoas:
            if p['emails']:
                p['padrao'] = True
                break
    pessoas.sort(key=lambda p: (not p['financeiro'], not p['principal'], p['nome']))
    return pessoas


# ── a página do grupo: um código por grupo ──────────────────────────────────────────────────────
def pagina(grupo_id, usuario_id=None, criar=True):
    """A linha de fin_paginas_grupo do grupo (cria no primeiro uso). None se a tabela ainda não existe."""
    try:
        r = execute_query('SELECT * FROM fin_paginas_grupo WHERE grupo_id = %s', (grupo_id,), fetch=True, fetch_one=True)
    except Exception:
        return None
    if r or not criar:
        return r
    tok = secrets.token_urlsafe(24)
    execute_query('INSERT INTO fin_paginas_grupo (grupo_id, token, criado_por) VALUES (%s, %s, %s)', (grupo_id, tok, usuario_id))
    return execute_query('SELECT * FROM fin_paginas_grupo WHERE grupo_id = %s', (grupo_id,), fetch=True, fetch_one=True)


def pagina_por_token(token):
    if not token or len(token) > 48:
        return None
    try:
        return execute_query('SELECT * FROM fin_paginas_grupo WHERE token = %s', (token,), fetch=True, fetch_one=True)
    except Exception:
        return None


def url_da_pagina(pag):
    return f"{APP_URL}/g/{pag['token']}" if pag else ''


def carimbar_abertura(pag):
    execute_query('UPDATE fin_paginas_grupo SET aberto_em = NOW(), aberto_vezes = aberto_vezes + 1 WHERE id = %s', (pag['id'],))


# ── textos ──────────────────────────────────────────────────────────────────────────────────────
def texto_whatsapp(d, link, empresa_nome):
    """Um texto só para o WhatsApp da rede: cada empresa com os seus honorários, o total e UM link."""
    g = d['grupo']['nome']
    emps = [e for e in d['empresas'] if e['n_pagina']]
    if not emps or not link:
        return ''
    txt = [f'Olá! Aqui é da *{empresa_nome}*. 😊', '']
    if d['n_vencidos']:
        txt += [f'Passando para lembrar dos honorários em aberto das empresas do grupo *{g}*. '
                'Se algum pagamento já foi feito, pode desconsiderar. 🙂', '']
    else:
        txt += [f'Seguem as cobranças de honorários das empresas do grupo *{g}*.', '']
    txt.append('📋 *O que está em aberto*')
    for e in emps:
        txt.append(f"🏢 *{e['nome']}*")
        for t in e['titulos']:
            if t['estado'] != 'na_pagina':
                continue
            quando = f"venceu em {t['venc_txt']}" if t['vencido'] else f"vence {t['venc_txt']}"
            txt.append(f"• {t['desc']} — {t['valor_txt']} — {quando}")
    n_e, n_t = len(emps), d['n_pagina']
    txt += ['', f"💰 Total: *{d['total_pagina_txt']}* · {n_e} empresa{'s' if n_e != 1 else ''} · {n_t} honorário{'s' if n_t != 1 else ''}", '']
    txt += [f"👉 Para {'regularizar' if d['n_vencidos'] else 'pagar'}, é só tocar no link. Lá estão todas as cobranças, "
            'cada uma com o *Pix* ou a *linha digitável* para copiar e colar no app do banco, e o PDF do boleto.',
            link, '', 'Qualquer dúvida é só responder por aqui. Obrigado pela parceria! 🙏']
    return '\n'.join(txt)


def montar_email(d, link, empresa_nome, telefone=''):
    """(assunto, html, texto) do e-mail único da rede, no mesmo molde do e-mail de cobrança."""
    from utils.cobranca_email import _env
    g = d['grupo']['nome']
    emps = [e for e in d['empresas'] if e['n_pagina']]
    linhas = []
    for e in emps:
        for t in e['titulos']:
            if t['estado'] == 'na_pagina':
                linhas.append({'empresa': e['nome'], 'doc': e['doc_txt'], 'honorario': t['desc'], 'venc': t['venc_txt'],
                               'vencido': t['vencido'], 'valor': t['valor_txt'], 'pagina': t['pagina']})
    n_e, n_t = len(emps), len(linhas)
    titulo = 'Honorários em aberto' if d['n_vencidos'] else 'Cobrança de honorários'
    intro = (f'Aqui está a cobrança de {empresa_nome} para as empresas do grupo {g}: '
             f"{n_e} empresa{'s' if n_e != 1 else ''} e {n_t} honorário{'s' if n_t != 1 else ''}"
             + (', alguns já vencidos' if d['n_vencidos'] else '') + '. Abaixo, cada um deles; o botão abre a página '
             'de pagamento, com o Pix, a linha digitável e o PDF de cada boleto.')
    dd = {
        'empresa': empresa_nome, 'data_txt': date.today().strftime('%d/%m/%Y'), 'titulo': titulo,
        'eyebrow': f'Cobrança · Grupo {g}', 'grupo': g, 'intro': intro, 'saudacao': 'Olá!',
        'kpis': [('Total em aberto', d['total_pagina_txt']), ('Empresas', str(n_e)), ('Honorários', str(n_t))],
        'linhas': linhas, 'total': d['total_pagina_txt'], 'link': link,
        'slogan': 'a EMPRESA é sua, a PARCERIA é NOSSA',
        'rodape': (f'Enviado por {empresa_nome}. Em caso de dúvida, responda este e-mail'
                   + (f' ou entre em contato pelo telefone {telefone}' if telefone else '') + '.'),
    }
    html = _env().get_template('cobranca_grupo.html').render(d=dd)
    t = [f'{titulo} — Grupo {g}', '', 'Olá!', intro, '', f"Total em aberto: {dd['total']}", '', 'HONORÁRIOS EM ABERTO']
    for l in linhas:
        t.append(f"  {l['empresa']} · {l['honorario']} · {'venceu em' if l['vencido'] else 'vence'} {l['venc']}: {l['valor']}")
    t += ['', f'Página de pagamento: {link}', '', dd['slogan'], '', dd['rodape']]
    assunto = f'{titulo} · Grupo {g} · {n_t} honorário{"s" if n_t != 1 else ""}'
    return assunto, html, '\n'.join(t)


def enviar_email(grupo_id, para, usuario_id=None):
    """Manda o e-mail único da rede. Devolve (ok, mensagem para a tela, dados)."""
    from models.fin_cobranca import FinCobrancaConta
    from utils import email_envio
    d = levantar(grupo_id)
    if not d:
        return False, 'Grupo não encontrado.', None
    if not d['n_pagina']:
        return False, 'Nenhum boleto ativo neste grupo: emita ou reemita antes de enviar.', d
    destinos = [p.strip().lower() for p in str(para or '').replace(';', ',').split(',') if p.strip()]
    if not destinos:
        return False, 'Marque um contato ou informe um e-mail.', d
    if any('@' not in p for p in destinos):
        return False, 'Há um endereço de e-mail inválido.', d
    pag = pagina(grupo_id, usuario_id)
    if not pag:
        return False, 'A página do grupo ainda não está disponível (tabela fin_paginas_grupo).', d
    link = url_da_pagina(pag)
    primeiro = next(t['boleto'] for e in d['empresas'] for t in e['titulos'] if t['estado'] == 'na_pagina')
    try:
        rem = FinCobrancaConta.remetente_email(primeiro['conta_cobranca_id'])
    except ValueError as exc:
        return False, f'{exc} Preencha o bloco "E-mail de cobrança" na conta de cobrança.', d
    empresa_nome = _empresa(primeiro['empresa_id'])
    assunto, html, texto = montar_email(d, link, empresa_nome, telefone=_telefone_empresa(primeiro['empresa_id']))
    ok, erro = email_envio.enviar(destinos, assunto, html, texto, remetente=rem, servico='Boleto', usuario_id=usuario_id)
    if not ok:
        return False, f'O e-mail não saiu: {erro}', d
    para_txt = ', '.join(destinos)[:500]
    ids = [t['boleto']['id'] for e in d['empresas'] for t in e['titulos'] if t['estado'] == 'na_pagina']
    execute_query(f"""UPDATE fin_boletos SET email_enviado_em = NOW(), email_enviado_para = %s, atualizado_em = NOW()
                       WHERE id IN ({','.join(['%s'] * len(ids))})""", (para_txt, *ids))
    execute_query('UPDATE fin_paginas_grupo SET email_enviado_em = NOW(), email_enviado_para = %s WHERE id = %s', (para_txt, pag['id']))
    return True, f"E-mail enviado para {', '.join(destinos)} com {len(ids)} honorário{'s' if len(ids) != 1 else ''} de {sum(1 for e in d['empresas'] if e['n_pagina'])} empresa(s).", d


def boletos_da_pagina(d):
    """Para a página pública: cada boleto ativo com Pix, linha, links e o que está sendo cobrado."""
    from utils.cobranca_email import links_do_email
    out = []
    for e in d['empresas']:
        for t in e['titulos']:
            if t['estado'] != 'na_pagina':
                continue
            b = t['boleto']
            itens = [{'nome': i.get('nome') or '', 'valor': _brl(i.get('valor'))} for i in _itens(b) if i.get('nome')]
            if not itens:
                itens = [{'nome': t['desc'], 'valor': _brl(b['valor'])}]
            out.append({'empresa': e['nome'], 'doc': e['doc_txt'], 'numero': e['numero'], 'honorario': t['desc'],
                        'competencia': t['competencia'], 'valor': _brl(b['valor']), 'venc': _data(b['vencimento']),
                        'vencido': t['vencido'], 'charge_id': b.get('charge_id'), 'itens': itens,
                        'pix': (b.get('pix_copia_cola') or '').strip(), 'linha': (b.get('linha_digitavel') or '').strip(),
                        'urls': links_do_email(b), 'tem_pdf': bool(b.get('pdf_url') or b.get('link')), 'nfse_id': b.get('nfse_id')})
    return out
