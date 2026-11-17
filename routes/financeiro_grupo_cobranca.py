# -*- coding: utf-8 -*-
"""Cobrança do GRUPO de empresas (rede) de uma vez, na tela Títulos a receber — 17/11/2026.

Anderson: "estamos pesquisando por rede ATRASADOS, GRUPO M6... com essa seleção conseguimos enviar as Página do
Cliente de uma vez para o cliente... e não um a um!!!"; "o cliente tem uma empresa Atrasada e outras
Inadimplentes... queria mandar tudo no mesmo Página do Cliente... até a vencer se tiver em aberto... e até os
perdidos. A única coisa que tem que barrar seria se não tiver a cobrança ativa, por exemplo o boleto está
expirado... veja como resolver isso".

- GET  /financeiro/grupos/<id>/cobranca          janela do grupo (todas as empresas com honorário em aberto)
- POST /financeiro/grupos/<id>/cobranca/email    um e-mail só, com todas as cobranças e o link da página do grupo
- POST /financeiro/grupos/<id>/cobranca/reemitir reemite os boletos EXPIRADOS do grupo com um vencimento só
- GET  /g/<token>                                 página pública do grupo (sem login): cada boleto ativo com Pix,
                                                  linha digitável e PDF; o que foi pago some
A regra de quem pode: abrir e mandar o e-mail = quem chega na tela Títulos; reemitir = incluir boleto (acesso.py).
"""
import base64
import io
from urllib.parse import quote

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from models.fin_empresa import FinEmpresa
from routes.financeiro import financeiro
from routes.financeiro_cobranca import cobranca_publica, reemitir_expirado, _data_do_form, _hoje
from utils import cobranca_grupo as CG
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.cobranca_email import _empresa
from utils.db_helper import execute_query


def _volta(grupo_id):
    aba = request.values.get('aba') if request.values.get('aba') in ('rec', 'abe', 'atr', 'ina', 'perd') else 'abe'
    emp = request.values.get('emp') or None
    return url_for('financeiro.titulos', aba=aba, emp=emp) + f'#grupo={grupo_id}'


@financeiro.route('/financeiro/grupos/<int:grupo_id>/cobranca')
@permission_required('financeiro.titulos')
def grupo_cobranca(grupo_id):
    d = CG.levantar(grupo_id)
    if not d:
        abort(404)
    pag = CG.pagina(grupo_id, current_user.id) if d['n_pagina'] else CG.pagina(grupo_id, current_user.id, criar=False)
    link = CG.url_da_pagina(pag)
    empresa_nome = _empresa(d['empresas'][0]['empresa_id']) if d['empresas'] else 'Qualicontax'
    texto = CG.texto_whatsapp(d, link, empresa_nome) if link else ''
    whats = [p for p in d['contatos'] if p['cel']]
    g = d['grupo']['nome']
    iniciais = ''.join(p[0] for p in g.split()[:2]).upper() or 'G'
    return render_template('financeiro/_grupo_cobranca.html', d=d, g=d['grupo'], pag=pag, link=link, texto=texto,
                           texto_url=quote(texto), whats=whats, iniciais=iniciais, volta=_volta(grupo_id),
                           hoje_iso=_hoje().isoformat(), BOLETO_ROTULO=CG.TR.BOLETO_ROTULO)


@financeiro.route('/financeiro/grupos/<int:grupo_id>/cobranca/email', methods=['POST'])
@permission_required('financeiro.titulos')
def grupo_cobranca_email(grupo_id):
    para = ', '.join(p for p in request.form.getlist('para_chk') + [request.form.get('para') or ''] if p.strip()) or None
    ok, msg, d = CG.enviar_email(grupo_id, para, usuario_id=current_user.id)
    flash(msg, 'success' if ok else 'danger')
    registrar('escrita.enviou_email_cobranca_grupo', 'financeiro', tabela='grupos_clientes', registro_id=grupo_id,
              depois={'ok': ok, 'para': (para or '')[:200], 'msg': msg[:200], 'honorarios': (d or {}).get('n_pagina'),
                      'total': (d or {}).get('total_pagina_txt')})
    return redirect(_volta(grupo_id))


@financeiro.route('/financeiro/grupos/<int:grupo_id>/cobranca/reemitir', methods=['POST'])
@permission_required('financeiro.cobranca')
def grupo_reemitir(grupo_id):
    """Reemite TODOS os boletos expirados dos honorários em aberto do grupo, com um vencimento só.
    Cada um passa pela mesma função do botão Reemitir da janela do honorário (e cada um vai para a Auditoria)."""
    d = CG.levantar(grupo_id)
    if not d:
        abort(404)
    venc = _data_do_form('vencimento', _hoje())
    if not venc:
        flash('Informe o vencimento dos boletos novos (hoje ou depois).', 'danger')
        return redirect(_volta(grupo_id))
    feitos, erros = [], []
    for e in d['empresas']:
        for t in e['titulos']:
            if t['estado'] != 'expirado' or t['perdido']:
                continue
            ok, msg, novo_id, _aviso = reemitir_expirado(t['boleto'], venc, current_user.id)
            (feitos if ok else erros).append(f"{e['nome']} · {t['desc']}" + ('' if ok else f' ({msg})'))
    if feitos:
        flash(f"{len(feitos)} boleto{'s' if len(feitos) != 1 else ''} reemitido{'s' if len(feitos) != 1 else ''} com vencimento em "
              f"{venc.strftime('%d/%m/%Y')}: " + '; '.join(feitos), 'success')
    if erros:
        flash('Não consegui reemitir: ' + '; '.join(erros), 'danger')
    if not feitos and not erros:
        flash('Não há boleto expirado neste grupo.', 'warning')
    registrar('escrita.reemitiu_boletos_grupo', 'financeiro', tabela='grupos_clientes', registro_id=grupo_id,
              depois={'vencimento': str(venc), 'reemitidos': len(feitos), 'erros': erros[:5]})
    return redirect(_volta(grupo_id))


# ── a página do grupo (pública: só quem tem o link chega) ────────────────────────────────────────
@cobranca_publica.route('/g/<token>')
def pagina_grupo(token):
    pag = CG.pagina_por_token(token)
    if not pag:
        abort(404)
    d = CG.levantar(pag['grupo_id'])
    if not d:
        abort(404)
    CG.carimbar_abertura(pag)
    boletos = CG.boletos_da_pagina(d)
    try:
        import qrcode
        for b in boletos:
            if b['pix']:
                q = qrcode.QRCode(border=1, box_size=6)
                q.add_data(b['pix'])
                q.make(fit=True)
                buf = io.BytesIO()
                q.make_image().save(buf, format='PNG')
                b['qr'] = base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass
    empresa = _empresa(d['empresas'][0]['empresa_id']) if d['empresas'] else 'Qualicontax'
    n_emp = sum(1 for e in d['empresas'] if e['n_pagina'])
    return render_template('financeiro/pagar_grupo_publico.html', g=d['grupo'], boletos=boletos, empresa=empresa,
                           total=d['total_pagina_txt'], n=len(boletos), n_emp=n_emp, n_vencidos=sum(1 for b in boletos if b['vencido']),
                           slogan='a EMPRESA é sua, a PARCERIA é NOSSA')
