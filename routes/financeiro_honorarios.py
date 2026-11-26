# -*- coding: utf-8 -*-
"""Honorários — contratos mensais das empresas (06/11/2026, parte 6 da migração).

Mora no blueprint ``financeiro`` (mesma navegação). Tela única: todas as empresas com honorários
(valor, dia de vencimento, 13º, próximo reajuste, contatos do Financeiro que recebem a cobrança) e a
lista das que ainda não têm. Permissão ``financeiro.honorarios`` nasce sem perfil: só o admin entra
até ele marcar em Perfis. O fluxo "funcionário autorizado cadastra → Anderson aprova" (etapa 5)
vai usar a situação PENDENTE que o modelo já conhece.
"""
import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models.fin_contrato import (DECIMO_INICIOS, DECIMO_TIPOS, REAJUSTE_TIPOS, SERVICO_HONORARIOS, FinContrato,
                                 decimo_desde_de, modo_decimo_inicio, primeiro_reajuste)
from routes.financeiro import financeiro
from utils.atividade import registrar
from utils.auth_helper import permission_required
from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
PERM = 'financeiro.honorarios'


def _dec(raw):
    s = (str(raw or '')).strip()
    if not s:
        return Decimal('0')
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    return Decimal(s).quantize(Decimal('0.01'))


def _data(raw):
    s = (raw or '').strip()
    return datetime.strptime(s, '%Y-%m-%d').date() if s else None


def _servicos():
    """Serviços para a caixa da linha do contrato: com o valor padrão e em quantos contratos ativos aparece (19/11/2026)."""
    rows = execute_query("""SELECT s.id, s.nome, s.valor_padrao,
                                   (SELECT COUNT(*) FROM fin_contrato_itens i JOIN fin_contratos c ON c.id = i.contrato_id AND c.situacao = 'ATIVO'
                                     WHERE i.servico_id = s.id) AS n
                              FROM nfse_servicos s WHERE s.ativo = 1 AND s.nome <> 'Desconto' ORDER BY s.ordem, s.nome""", fetch=True) or []
    return [{'id': r['id'], 'nome': r['nome'], 'n': int(r['n'] or 0), 'valor_padrao': float(r['valor_padrao']) if r['valor_padrao'] is not None else None} for r in rows]


def _grupos():
    return execute_query("SELECT id, nome FROM grupos_clientes WHERE situacao = 'ATIVO' ORDER BY nome", fetch=True) or []


def _gravar_grupo(cliente_id, f):
    """O campo Grupo do contrato É o grupo do CADASTRO: trocar aqui troca lá (Anderson, 06/11/2026: "com a opção
    de alterar aí já altera até no cadastro em tudo"). Em branco não tira de grupo nenhum."""
    if 'grupo_id' not in f:
        return
    gid = f.get('grupo_id', type=int)
    if not gid:
        return
    from routes.clientes import _salvar_grupo
    mudou = _salvar_grupo(cliente_id, gid)
    if mudou:
        registrar('escrita.alterou_grupo_cliente', 'clientes', tabela='cliente_grupo_relacao', registro_id=cliente_id,
                  antes={'grupos': mudou[0]}, depois={'grupo': mudou[1], 'origem': 'contrato de honorários'})


def _desconto_form(f, valor):
    """19/11/2026 — desconto mensal do contrato (fixo ou %), motivo e validade, vindos da janela."""
    tipo = (f.get('desconto_tipo') or '').strip().upper() or None
    if tipo not in (None, 'FIXO', 'PCT'):
        raise ValueError('Tipo de desconto inválido.')
    if not tipo:
        return dict(desconto_tipo=None, desconto_valor=None, desconto_motivo=None, desconto_ate=None)
    v = _dec(f.get('desconto_valor'))
    if v is None or v <= 0:
        raise ValueError('Informe o valor do desconto mensal.')
    if tipo == 'PCT' and v >= 100:
        raise ValueError('O desconto em porcentagem precisa ser menor que 100%.')
    if tipo == 'FIXO' and v >= valor:
        raise ValueError('O desconto mensal não pode ser igual ou maior que os honorários.')
    return dict(desconto_tipo=tipo, desconto_valor=v, desconto_motivo=(f.get('desconto_motivo') or '').strip()[:200] or None,
                desconto_ate=_data(f.get('desconto_ate')))


def _foto(c):
    """O que vai para o log: só o que importa, em texto."""
    if not c:
        return None
    return {'valor': str(c.get('valor')), 'dia_vencimento': c.get('dia_vencimento'),
            'desconto': ('%s %s%s%s' % (c.get('desconto_tipo'), c.get('desconto_valor'), (' · ' + c['desconto_motivo']) if c.get('desconto_motivo') else '',
                                         (' · até ' + str(c['desconto_ate'])) if c.get('desconto_ate') else '')) if c.get('desconto_tipo') else None,
            'inicio': str(c.get('inicio') or ''), 'fim': str(c.get('fim') or ''), 'plano': c.get('plano'),
            'decimo_tipo': c.get('decimo_tipo'), 'decimo_parcelas': c.get('decimo_parcelas'), 'decimo_desde': str(c.get('decimo_desde') or ''),
            'reajuste_tipo': c.get('reajuste_tipo'), 'proximo_reajuste': str(c.get('proximo_reajuste') or ''),
            'reajuste_obs': c.get('reajuste_obs'), 'situacao': c.get('situacao'), 'banco_boleto': c.get('banco_boleto'),
            'gerar_nfse': c.get('gerar_nfse'),
            'itens': ['%s = %s' % (i['descricao'], i['valor']) for i in FinContrato.itens(c['id'])] if c.get('id') else None}


ABAS_CONTRATOS = ('com', 'sem', 'pendentes', 'enc')


# ---------------------------------------------------------------------------
# 19/11/2026 — 13º em BOLETOS PRÓPRIOS (Anderson: "a fast quer pagar o 13 em três parcelas mas não nos honorários").
# O contrato guarda as datas combinadas (o dia/mês repete todo ano); a parcela que vence no mês seguinte à competência
# sai pelo painel do mês como 2ª cobrança da empresa, boleto separado da mensalidade (utils/lote_mensal.previa).
# ---------------------------------------------------------------------------
def _parcelas_proprio(f):
    """[{'data': 'AAAA-MM-DD'}, ...] das datas digitadas na janela (até 6, em ordem, sem repetir)."""
    datas = sorted({d for d in (_data(x) for x in f.getlist('dec_data')) if d})
    if not datas:
        raise ValueError('Informe a data de cada parcela do 13º.')
    if len(datas) > 6:
        raise ValueError('O 13º em boletos próprios vai até 6 parcelas.')
    return [{'data': d.isoformat()} for d in datas]


def _decimo_proprio(c):
    """O que a janela mostra do 13º em boletos próprios: as datas do ciclo atual (o dia/mês repete todo ano), o valor
    de cada parcela e a situação de cada uma no painel (sai no painel de MM/AAAA, boleto emitido, pago, em atraso)."""
    hoje = date.today()
    ciclo = FinContrato.ciclo_decimo_proprio(c, hoje)
    if not ciclo:
        return {'ano': hoje.year, 'base': '0', 'ciclo': [], 'parcelas': []}
    ano = FinContrato.ano_decimo(ciclo[0])
    base = FinContrato.base_decimo(c, ano)
    vals = FinContrato.dividir_decimo(base, len(ciclo))
    comps = [date(d.year - (d.month == 1), (d.month - 2) % 12 + 1, 1) for d in ciclo]     # competência = mês anterior ao vencimento
    feitos = {}
    if c.get('id'):
        for r in execute_query("""SELECT li.status, lm.competencia, t.id AS titulo_id, t.status AS ts, t.vencimento AS tv
                                    FROM fin_lote_itens li JOIN fin_lotes_mensais lm ON lm.id = li.lote_id
                                    LEFT JOIN fin_titulos t ON t.id = li.titulo_id
                                   WHERE li.contrato_id = %s AND li.tipo = 'DECIMO' AND lm.competencia BETWEEN %s AND %s""",
                              (c['id'], comps[0], comps[-1]), fetch=True) or []:
            feitos[r['competencia']] = r
    parcelas = []
    for k, d in enumerate(ciclo):
        r = feitos.get(comps[k])
        if r and r['status'] == 'ok' and r.get('ts') and r['ts'] != 'cancelado':
            st, rot = ('pago', 'pago') if r['ts'] == 'liquidado' else (('atrasado', 'em atraso') if r['tv'] < hoje else ('emitido', 'boleto emitido'))
            venc = r['tv']
        elif r and r['status'] == 'erro':
            st, rot, venc = 'falhou', 'a cobrança não saiu (veja no painel)', d
        else:
            st, rot, venc = 'a_emitir', 'sai no painel de %s' % comps[k].strftime('%m/%Y'), d
        parcelas.append({'n': k + 1, 'de': len(ciclo), 'competencia': comps[k].strftime('%m/%Y'), 'vencimento': venc.isoformat(),
                         'valor': str(vals[k].quantize(Decimal('0.01'))), 'st': st, 'rot': rot, 'titulo_id': (r or {}).get('titulo_id')})
    return {'ano': ano, 'base': str(base), 'ciclo': [d.isoformat() for d in ciclo], 'parcelas': parcelas}


def _competencia_painel():
    from models.fin_lancamento import FinLoteMensal
    from models.fin_contrato import EMPRESA_QUALICONTAX
    raw = (request.args.get('competencia') or request.form.get('competencia') or '').strip()
    try:
        return datetime.strptime(raw, '%Y-%m').date().replace(day=1) if raw else FinLoteMensal.competencia_aberta(EMPRESA_QUALICONTAX)
    except ValueError:
        return FinLoteMensal.competencia_aberta(EMPRESA_QUALICONTAX)


def _painel():
    """Honorários + Lote mensal numa tela só (18/11/2026 — etapa 4). Anderson: "capricha muito no layout, quero algo
    que mostre as cobranças... e qual banco que está vinculado padrão". A lista antiga de contratos continua nas abas
    com/sem/pendentes (com a caixa de editar)."""
    from models.fin_lancamento import mes_seguinte
    from utils import honorarios_painel as HP, lote_mensal
    from utils.bancos_logo import url as logo_url
    comp = _competencia_painel()
    P = HP.painel(comp)
    ant = date(comp.year - (comp.month == 1), (comp.month - 2) % 12 + 1, 1)
    bj = {b['id']: {'nome': b['nome'], 'img': logo_url(b['cod']) if b['cod'] else ''} for b in P['bancos']}
    st = lote_mensal.status(P['lote']['id']) if P['lote'] else None
    return render_template('financeiro/honorarios_painel.html', painel=P, competencia=comp, resumo=FinContrato.resumo(), bancos_js=bj,
                           comp_ant=ant, comp_prox=mes_seguinte(comp), status_lote=st, hoje=date.today(),
                           # 26/11/2026: a janela do contrato abre dentro do painel (mesmas variáveis da página antiga)
                           modo='admin', servicos=_servicos(), grupos=_grupos(), DECIMO_TIPOS=DECIMO_TIPOS, DECIMO_INICIOS=DECIMO_INICIOS,
                           REAJUSTE_TIPOS=REAJUSTE_TIPOS, SERVICO_HONORARIOS=SERVICO_HONORARIOS, bancos=_bancos_cobranca())


@financeiro.route('/financeiro/honorarios')
@permission_required(PERM)
def honorarios():
    if request.args.get('aba') not in ABAS_CONTRATOS and not request.args.get('busca'):
        return _painel()
    aba = request.args.get('aba', 'com')
    busca = (request.args.get('busca') or '').strip() or None
    contratos = FinContrato.listar(busca=busca)
    sem = FinContrato.clientes_sem_contrato()
    if busca:
        b = busca.lower()
        sem = [c for c in sem if b in (c['nome_razao_social'] or '').lower() or b == str(c['numero_cliente'])]
    from models.cliente_responsavel import ClienteResponsavel
    pendentes = [c for c in contratos if c['situacao'] == 'PENDENTE']
    encerrados = sorted([c for c in contratos if c['situacao'] == 'ENCERRADO'], key=lambda c: c.get('fim') or date.min, reverse=True)
    contratos = [c for c in contratos if c['situacao'] == 'ATIVO']
    convites = execute_query(
        """SELECT v.*, cl.nome_razao_social, u.nome AS usuario_nome FROM fin_honorarios_convites v
             JOIN clientes cl ON cl.id = v.cliente_id LEFT JOIN usuarios u ON u.id = v.usuario_id
            WHERE v.usado_em IS NULL AND v.expira_em > NOW() ORDER BY v.id DESC""", fetch=True) or []
    return render_template('financeiro/honorarios.html', modo='admin', aba=aba, busca=busca, contratos=contratos, sem_contrato=sem,
                           pendentes=pendentes, encerrados=encerrados, convites=convites, pessoas=ClienteResponsavel.usuarios_disponiveis(),
                           resumo=FinContrato.resumo(), servicos=_servicos(), grupos=_grupos(), DECIMO_TIPOS=DECIMO_TIPOS, DECIMO_INICIOS=DECIMO_INICIOS,
                           REAJUSTE_TIPOS=REAJUSTE_TIPOS, hoje=date.today(), SERVICO_HONORARIOS=SERVICO_HONORARIOS,
                           bancos=_bancos_cobranca())


def _bancos_cobranca():
    """Bancos que emitem boleto, com o padrão marcado (a janela do contrato mostra e deixa trocar — 18/11/2026)."""
    try:
        from utils.honorarios_painel import contas
        return contas()
    except Exception:
        logger.exception('[honorarios] bancos da cobrança')
        return []


@financeiro.route('/financeiro/honorarios/relatorio')
@permission_required(PERM)
def honorarios_relatorio():
    """PDF de conferência da cobrança do mês (25/11/2026 — Anderson: "como eu conseguirei consultar o que estaremos
    cobrando do AUTO POSTO XINGU e de todas as outras empresas?... um relatório em PDF... com logo do banco e tudo
    mais"). Parte do mesmo painel da tela, com os filtros dela (quadro aberto, banco, nota, grupo, 13º, lançamentos,
    valor e busca). Desenho em utils/relatorio_honorarios.py."""
    import os
    from io import BytesIO
    from zoneinfo import ZoneInfo
    from flask import send_file
    from utils import honorarios_painel as HP, relatorio_honorarios as RH
    comp = _competencia_painel()
    P = HP.painel(comp)
    f = RH.ler_filtros(request.args)
    linhas, filtros = RH.filtrar(P, f)
    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    logo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'images', 'logo.png')
    pdf = RH.gerar_pdf({'competencia': comp, 'venc_mes': P['venc_mes'], 'linhas': linhas, 'bancos': P['bancos'], 'filtros': filtros,
                        'gerado_em': agora.strftime('%d/%m/%Y %H:%M'), 'gerado_por': getattr(current_user, 'nome', '') or '',
                        'logo_path': logo if os.path.exists(logo) else None})
    registrar('leitura.exportou_arquivo', 'financeiro', tabela='fin_contratos',
              depois={'relatorio': 'Cobrança de honorários %s' % comp.strftime('%m/%Y'), 'formato': 'pdf', 'empresas': len(linhas), 'filtros': ' · '.join(filtros)[:400]})
    nome = 'honorarios-%s-%s.pdf' % (comp.strftime('%Y-%m'), agora.strftime('%Y-%m-%d-%H%M'))
    return send_file(BytesIO(pdf), as_attachment=request.args.get('baixar') == '1', download_name=nome, mimetype='application/pdf')


@financeiro.route('/financeiro/honorarios/<int:cid>/json')
@permission_required(PERM)
def honorarios_json(cid):
    c = FinContrato.get(cid)
    if not c:
        return jsonify({'erro': 'não encontrado'}), 404
    out = {k: (v.isoformat() if hasattr(v, 'isoformat') else (str(v) if isinstance(v, Decimal) else v)) for k, v in c.items()}
    out['itens'] = [{'descricao': i['descricao'], 'valor': str(i['valor']), 'servico_id': i['servico_id']} for i in FinContrato.itens(cid)]
    out['decimo_inicio'] = modo_decimo_inicio(c.get('decimo_desde'), c.get('inicio'))
    from models.cliente import Cliente
    g = Cliente.get_grupos(c['cliente_id'])
    out['grupo_id'] = g[0]['id'] if g else None
    out['extra'] = _extra_contrato(c, g[0]['nome'] if g else None)      # 18/11/2026: o que a janela mostra além dos campos
    out['decimo_proprio'] = _decimo_proprio(c)                             # 19/11/2026: 13º em boletos próprios
    return jsonify(out)


def _extra_contrato(c, grupo_nome=None):
    """O que a janela do contrato mostra de contexto (18/11/2026 — Anderson: "dá uma melhorada boa, está simples demais"):
    como a empresa paga (12 meses pelo vencimento), quem recebe a cobrança, reajustes aplicados e o histórico do contrato."""
    from utils.honorarios_painel import _historico, MESES
    from models.fin_lancamento import mes_seguinte
    hoje = date.today()
    cid = c['cliente_id']
    # 12 vencimentos: os 11 anteriores + o da competência atual (vence no mês que vem)
    venc = mes_seguinte(hoje.replace(day=1))
    meses = []
    m = venc
    for _ in range(12):
        meses.insert(0, m)
        m = date(m.year - (m.month == 1), (m.month - 2) % 12 + 1, 1)
    h = _historico([{'cliente_id': cid, 'cpf_cnpj': c.get('cpf_cnpj'), 'nome': c.get('nome_razao_social')}], meses[0], mes_seguinte(venc)).get(cid, {})
    linha = []
    for x in meses:
        r = h.get(x.strftime('%Y-%m')) or {'status': 'sem'}
        linha.append({'mes': MESES[x.month - 1], 'ano': x.year, 'status': r['status'], 'valor': float(r.get('valor') or 0),
                      'pago': float(r.get('pago') or 0), 'id': r.get('id')})
    passados = [x for x in linha[:-1] if x['status'] != 'sem']
    em_dia = sum(1 for x in passados if x['status'] == 'pago')
    atrasado = sum(x['valor'] - x['pago'] for x in linha[:-1] if x['status'] in ('atrasado', 'parcial'))
    contatos = execute_query(
        """SELECT ct.nome, ct.email, ct.celular FROM contatos_clientes ct JOIN contatos_departamentos cd ON cd.contato_id = ct.id
            WHERE ct.cliente_id = %s AND ct.ativo = 1 AND cd.departamento_id = 8 AND ct.email <> '' ORDER BY ct.nome""", (cid,), fetch=True) or []
    reaj = execute_query(
        """SELECT vigencia, percentual, valor_antes, valor_depois, aplicado_em, desfeito_em, lote FROM fin_contrato_reajustes
            WHERE contrato_id = %s ORDER BY vigencia DESC, id DESC LIMIT 6""", (c['id'],), fetch=True) or []
    logs = execute_query(
        """SELECT acao, data_hora, usuario_nome FROM logs_sistema WHERE registro_id = %s AND acao LIKE '%%contrato_honorarios%%'
            ORDER BY id DESC LIMIT 6""", (c['id'],), fetch=True) or []
    rot = {'escrita.criou_contrato_honorarios': 'Contrato criado', 'escrita.alterou_contrato_honorarios': 'Contrato alterado',
           'escrita.encerrou_contrato_honorarios': 'Contrato encerrado', 'escrita.aprovou_contrato_honorarios': 'Contrato aprovado',
           'escrita.reprovou_contrato_honorarios': 'Contrato reprovado', 'escrita.contrato_conexa_correcao': 'Ajuste da migração'}
    quem = execute_query('SELECT id, nome FROM usuarios WHERE id IN (%s, %s)', (c.get('criado_por') or 0, c.get('aprovado_por') or 0), fetch=True) or []
    nomes = {u['id']: u['nome'] for u in quem}
    return {
        'cpf_cnpj': c.get('cpf_cnpj'), 'grupo_nome': grupo_nome, 'situacao': c.get('situacao'), 'plano': c.get('plano'),
        'criado_em': c['criado_em'].strftime('%d/%m/%Y') if c.get('criado_em') else None, 'criado_por': nomes.get(c.get('criado_por')),
        'aprovado_por': nomes.get(c.get('aprovado_por')), 'conexa': bool(c.get('conexa_contrato_id')),
        'meses': linha, 'em_dia': em_dia, 'cobrados': len(passados), 'atrasado': float(atrasado),
        'contatos': [{'nome': x['nome'], 'email': x['email'], 'celular': x.get('celular')} for x in contatos],
        'reajustes': [{'vigencia': r['vigencia'].strftime('%m/%Y') if r['vigencia'] else '', 'pct': float(r['percentual'] or 0),
                       'antes': float(r['valor_antes'] or 0), 'depois': float(r['valor_depois'] or 0), 'desfeito': bool(r['desfeito_em']),
                       'lote': r.get('lote')} for r in reaj],
        'historico': [{'o_que': rot.get(l['acao'], l['acao'].replace('escrita.', '').replace('_', ' ')), 'quando': l['data_hora'].strftime('%d/%m/%Y %H:%M') if l['data_hora'] else '',
                       'quem': l['usuario_nome']} for l in logs],
    }


@financeiro.route('/financeiro/honorarios/sugestoes/<int:cliente_id>')
@login_required
def honorarios_sugestoes(cliente_id):
    """Valores da rede para o modal (grupo do cadastro ou, sem grupo, o plano do Conexa). Quem cadastra por
    link só tem login: o convite válido daquela empresa é o que libera."""
    pode = current_user.is_admin() or current_user.has_permission(PERM) or current_user.has_permission(PERM_CAD)
    if not pode:
        pode = bool(execute_query(
            """SELECT 1 FROM fin_honorarios_convites WHERE cliente_id = %s AND usado_em IS NULL AND expira_em > NOW()
                  AND (usuario_id IS NULL OR usuario_id = %s) LIMIT 1""", (cliente_id, current_user.id), fetch=True))
    if not pode:
        return jsonify({'erro': 'sem acesso'}), 403
    return jsonify(FinContrato.sugestoes(cliente_id))


def _volta_painel(f):
    """19/11/2026: contrato aberto pelos cartões (Cobrança do mês) volta para os cartões, no mesmo mês."""
    if f.get('volta') != 'painel':
        return None
    comp = (f.get('competencia') or '').strip()
    try:
        datetime.strptime(comp, '%Y-%m')
    except ValueError:
        comp = None
    return redirect(url_for('financeiro.honorarios', competencia=comp))


@financeiro.route('/financeiro/honorarios/salvar', methods=['POST'])
@permission_required(PERM)
def honorarios_salvar():
    f = request.form
    cid = f.get('contrato_id', type=int)
    volta = _volta_painel(f) or redirect(url_for('financeiro.honorarios', aba=f.get('aba') or 'com'))
    try:
        itens = []
        for d, v, s in zip(f.getlist('item_desc'), f.getlist('item_valor'), f.getlist('item_servico')):
            d = (d or '').strip()
            if not d and not (v or '').strip():
                continue
            itens.append({'descricao': d, 'valor': _dec(v), 'servico_id': int(s) if (s or '').isdigit() else None})
        if not itens:
            raise ValueError('Informe ao menos uma discriminação com valor.')
        valor = sum(i['valor'] for i in itens)
        dia = f.get('dia_vencimento', type=int)
        if not dia or not 1 <= dia <= 31:
            raise ValueError('Dia de vencimento entre 1 e 31.')
        inicio = _data(f.get('inicio'))
        if not inicio:
            raise ValueError('Informe o início do contrato.')
        decimo_tipo = f.get('decimo_tipo') or 'NENHUM'
        parcelas = _parcelas_proprio(f) if decimo_tipo == 'PROPRIO' else None
        reajuste_tipo = f.get('reajuste_tipo') or 'SALARIO_MINIMO'
        prox = _data(f.get('proximo_reajuste'))
        if reajuste_tipo == 'SALARIO_MINIMO' and not prox:
            prox = primeiro_reajuste(inicio)
        dados = dict(valor=valor, dia_vencimento=dia, inicio=inicio, fim=_data(f.get('fim')),
                     decimo_tipo=decimo_tipo, decimo_parcelas=parcelas, **_desconto_form(f, valor),
                     decimo_desde=decimo_desde_de(f.get('decimo_inicio') or 'PROPORCIONAL', inicio, _data(f.get('decimo_desde'))),
                     reajuste_tipo=reajuste_tipo, proximo_reajuste=prox,
                     reajuste_obs=(f.get('reajuste_obs') or '').strip() or None,
                     gerar_nfse=1 if f.get('gerar_nfse') else 0, gerar_boleto=1 if f.get('gerar_boleto') else 0)
        if 'banco_boleto' in f:        # 18/11/2026 — Anderson: "incluir em qual banco está cadastrado e permitir alterar"; vazio = o padrão
            dados['banco_boleto'] = f.get('banco_boleto') if (f.get('banco_boleto') or '').isdigit() else None
        if cid:
            antes = FinContrato.get(cid)
            if not antes:
                raise ValueError('Contrato não encontrado.')
            FinContrato.atualizar(cid, dados, itens)
            _gravar_grupo(antes['cliente_id'], f)
            depois = FinContrato.get(cid)
            registrar('escrita.alterou_contrato_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
                      antes=_foto(antes), depois=_foto(depois))
            flash('Contrato de %s salvo.' % depois['nome_razao_social'], 'success')
        else:
            cliente_id = f.get('cliente_id', type=int)
            if not cliente_id:
                raise ValueError('Escolha a empresa.')
            banco_novo = dados.pop('banco_boleto', None)
            cid = FinContrato.criar(cliente_id=cliente_id, itens=itens, criado_por=current_user.id,
                                    aprovado_por=current_user.id, situacao='ATIVO', **dados)
            if banco_novo:
                FinContrato.atualizar(cid, {'banco_boleto': banco_novo})
            _gravar_grupo(cliente_id, f)
            novo = FinContrato.get(cid)
            registrar('escrita.criou_contrato_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
                      depois=dict(_foto(novo), cliente_id=cliente_id, empresa=novo['nome_razao_social']))
            flash('Contrato de honorários de %s criado.' % novo['nome_razao_social'], 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    return volta


# ---------------- reajuste de janeiro (06/11/2026) ----------------
def _proximo_janeiro(hoje=None):
    """Janeiro corrente enquanto estamos em janeiro; depois disso, o próximo."""
    hoje = hoje or date.today()
    return date(hoje.year, 1, 1) if hoje.month == 1 else date(hoje.year + 1, 1, 1)


@financeiro.route('/financeiro/honorarios/reajuste')
@permission_required(PERM)
def honorarios_reajuste():
    """O Anderson digita o percentual do salário mínimo e aplica a todos do padrão de uma vez (decisão de 05/11)."""
    ate = _data((request.args.get('ate') or '') + ('-01' if len(request.args.get('ate') or '') == 7 else '')) or _proximo_janeiro()
    lista = FinContrato.a_reajustar(ate)
    padrao = [c for c in lista if c['reajuste_tipo'] == 'SALARIO_MINIMO']
    proprios = [c for c in lista if c['reajuste_tipo'] != 'SALARIO_MINIMO']
    lote = 'SM-%s' % ate.strftime('%Y-%m')
    return render_template('financeiro/honorarios_reajuste.html', ate=ate, padrao=padrao, proprios=proprios, lote=lote,
                           aplicados=FinContrato.reajustes_aplicados(lote), REAJUSTE_TIPOS=REAJUSTE_TIPOS, hoje=date.today())


@financeiro.route('/financeiro/honorarios/reajuste/aplicar', methods=['POST'])
@permission_required(PERM)
def honorarios_reajuste_aplicar():
    f = request.form
    vig = f.get('vigencia') or ''
    vigencia = _data(vig + '-01') if len(vig) == 7 else _data(vig)
    volta = redirect(url_for('financeiro.honorarios_reajuste', ate=vigencia.strftime('%Y-%m') if vigencia else None))
    try:
        pct = _dec(f.get('percentual'))
        if not vigencia:
            raise ValueError('Informe a partir de quando vale.')
        if not (Decimal('0') < pct <= Decimal('100')):
            raise ValueError('Percentual entre 0 e 100.')
        ids = [int(i) for i in f.getlist('ids') if (i or '').isdigit()]
        if not ids:
            raise ValueError('Marque ao menos um contrato.')
        lote = 'SM-%s' % vigencia.strftime('%Y-%m')
        n, antes_t, depois_t = 0, Decimal('0'), Decimal('0')
        for cid in ids:
            r = FinContrato.aplicar_reajuste(cid, pct, vigencia, current_user.id, lote)
            if not r:
                continue
            antes, depois = r
            n += 1; antes_t += _dec(antes['valor']); depois_t += _dec(depois['valor'])
            registrar('escrita.reajustou_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
                      antes={'valor': str(antes['valor']), 'proximo_reajuste': str(antes.get('proximo_reajuste') or '')},
                      depois={'valor': str(depois['valor']), 'proximo_reajuste': str(depois.get('proximo_reajuste') or ''),
                              'percentual': str(pct), 'vigencia': str(vigencia), 'empresa': depois['nome_razao_social']})
        flash('%d contratos reajustados em %s%% a partir de %s: de R$ %s para R$ %s por mês.'
              % (n, str(pct).rstrip('0').rstrip('.').replace('.', ','), vigencia.strftime('%m/%Y'),
                 '{:,.2f}'.format(antes_t).replace(',', 'X').replace('.', ',').replace('X', '.'),
                 '{:,.2f}'.format(depois_t).replace(',', 'X').replace('.', ',').replace('X', '.')), 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Percentual inválido.', 'danger')
    return volta


@financeiro.route('/financeiro/honorarios/reajuste/<int:rid>/desfazer', methods=['POST'])
@permission_required(PERM)
def honorarios_reajuste_desfazer(rid):
    r = FinContrato.desfazer_reajuste(rid, current_user.id)
    if not r:
        flash('Este reajuste já foi desfeito ou não existe.', 'warning')
        return redirect(url_for('financeiro.honorarios_reajuste'))
    c = FinContrato.get(r['contrato_id'])
    registrar('escrita.desfez_reajuste_honorarios', 'financeiro', tabela='fin_contratos', registro_id=r['contrato_id'],
              antes={'valor': str(r['valor_depois'])}, depois={'valor': str(r['valor_antes']), 'lote': r['lote'], 'empresa': c['nome_razao_social']})
    flash('Reajuste de %s desfeito: voltou a R$ %s.' % (c['nome_razao_social'], '{:,.2f}'.format(r['valor_antes']).replace(',', 'X').replace('.', ',').replace('X', '.')), 'info')
    return redirect(url_for('financeiro.honorarios_reajuste', ate=r['vigencia'].strftime('%Y-%m')))


@financeiro.route('/financeiro/honorarios/<int:cid>/encerrar', methods=['POST'])
@permission_required(PERM)
def honorarios_encerrar(cid):
    c = FinContrato.get(cid)
    if not c:
        flash('Contrato não encontrado.', 'danger')
        return redirect(url_for('financeiro.honorarios'))
    fim = _data(request.form.get('fim')) or date.today()
    FinContrato.atualizar(cid, {'situacao': 'ENCERRADO', 'fim': fim})
    registrar('escrita.encerrou_contrato_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
              antes=_foto(c), depois={'situacao': 'ENCERRADO', 'fim': str(fim), 'empresa': c['nome_razao_social']})
    flash('Contrato de %s encerrado em %s. O 13º sai proporcional aos meses de casa.' % (c['nome_razao_social'], fim.strftime('%d/%m/%Y')), 'success')
    return _volta_painel(request.form) or redirect(url_for('financeiro.honorarios', aba='com'))


# ---------------------------------------------------------------------------
# 18/11/2026 — banco e nota de cada empresa: "só esta cobrança" (fin_honorarios_escolhas) ou "sempre" (no contrato).
# Anderson: "temos que ter o controle dos que fazem NF e ter as opções de selecionar para ser regra e/ou para desmarcar"
# ---------------------------------------------------------------------------
def _contas_emissoras():
    from utils.honorarios_painel import contas
    return {c['id']: c for c in contas()}


@financeiro.route('/financeiro/honorarios/escolha', methods=['POST'])
@permission_required('financeiro.lote')
def honorarios_escolha():
    from models.fin_contrato import EMPRESA_QUALICONTAX as EMP
    f = request.form
    cliente_id = f.get('cliente_id', type=int)
    campo = f.get('campo')
    vale = f.get('vale')
    comp = _competencia_painel()
    if campo not in ('banco', 'nota') or vale not in ('mes', 'sempre') or not cliente_id:
        return jsonify({'ok': False, 'erro': 'pedido inválido'}), 400
    c = next((x for x in (FinContrato.por_cliente(cliente_id) or []) if x['situacao'] == 'ATIVO'), None)
    if not c:
        return jsonify({'ok': False, 'erro': 'empresa sem contrato ativo'}), 404
    nome = execute_query('SELECT nome_razao_social FROM clientes WHERE id = %s', (cliente_id,), fetch=True, fetch_one=True) or {}
    quando = 'sempre (no contrato)' if vale == 'sempre' else 'só ' + comp.strftime('%m/%Y')
    if campo == 'banco':
        contas = _contas_emissoras()
        conta_id = f.get('valor', type=int)
        if conta_id not in contas:
            return jsonify({'ok': False, 'erro': 'banco inválido'}), 400
        padrao = next((x['id'] for x in contas.values() if x['padrao']), None)
        regra = int(c['banco_boleto']) if str(c.get('banco_boleto') or '').isdigit() else None
        if vale == 'sempre':
            novo = None if conta_id == padrao else conta_id          # escolher o padrão = seguir o padrão
            execute_query('UPDATE fin_contratos SET banco_boleto = %s WHERE id = %s', (str(novo) if novo else None, c['id']), fetch=False)
            mes = None
        else:
            mes = None if conta_id == (regra or padrao) else conta_id
        depois = {'banco': contas[conta_id]['nome'], 'vale': quando}
        col = 'conta_cobranca_id'
    else:
        nota = f.get('valor') == '1'
        if vale == 'sempre':
            FinContrato.atualizar(c['id'], {'gerar_nfse': 1 if nota else 0})
            mes = None
        else:
            mes = None if nota == bool(c.get('gerar_nfse')) else (1 if nota else 0)
        depois = {'nota': 'nota + boleto' if nota else 'só boleto', 'vale': quando}
        col = 'gerar_nfse'
    # a escolha do mês: grava/limpa só o campo pedido ("sempre" limpa a do mês, que perderia o sentido)
    execute_query(
        f"""INSERT INTO fin_honorarios_escolhas (empresa_id, competencia, cliente_id, {col}, usuario_id) VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE {col} = VALUES({col}), usuario_id = VALUES(usuario_id)""",
        (EMP, comp, cliente_id, mes, current_user.id), fetch=False)
    depois['empresa'] = nome.get('nome_razao_social') or cliente_id
    registrar('escrita.honorarios_escolha', 'financeiro', tabela='fin_contratos', registro_id=c['id'], depois=depois)
    return jsonify({'ok': True})


# 25/11/2026 — Anderson: "no que marcar ter um botão de alterar... nome de empresa / vencimento se alterar, perguntar só essa ou
# todas? / Banco / Valor se alterar alterar até os honorários, lógico com confirmação". Uma janela para as empresas marcadas;
# cada campo mudado vale "só esta cobrança" (fin_honorarios_escolhas) ou "sempre" (no contrato). Só quem ainda não foi emitido.
def _brl_alt(v):
    return ('{:,.2f}'.format(v)).replace(',', 'X').replace('.', ',').replace('X', '.')


def _dec_alt(raw):
    t = str(raw or '').strip()
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    return Decimal(t).quantize(Decimal('0.01'))


@financeiro.route('/financeiro/honorarios/alterar', methods=['POST'])
@permission_required('financeiro.lote')
def honorarios_alterar():
    import json
    from models.fin_contrato import EMPRESA_QUALICONTAX as EMP
    from utils import lote_mensal
    comp = _competencia_painel()
    try:
        pedidos = json.loads(request.form.get('mudancas') or '[]')
        assert isinstance(pedidos, list) and pedidos
    except (ValueError, AssertionError):
        return jsonify({'ok': False, 'erro': 'Nada para alterar.'}), 400
    _lote, linhas = lote_mensal.previa(EMP, comp, so_clientes=[int(x.get('cliente_id') or 0) for x in pedidos])
    por_cli = {l['cliente_id']: l for l in linhas if l['tipo'] == 'MENSAL'}
    contas = _contas_emissoras()
    padrao = next((x['id'] for x in contas.values() if x['padrao']), None)
    hoje = date.today()
    sc = ' (só %s)' % comp.strftime('%m/%Y')
    feitas, erros = [], []
    for p in pedidos:
        cid = int(p.get('cliente_id') or 0)
        l = por_cli.get(cid)
        if not l:
            erros.append('Empresa %s sem contrato ativo nesta competência.' % cid)
            continue
        if (l.get('feito') or {}).get('status') not in (None, 'erro'):
            erros.append('%s: a cobrança de %s já foi emitida; não mudei nada.' % (l['empresa'], comp.strftime('%m/%Y')))
            continue
        c = l['contrato']
        esc, dados, antes, depois = {}, {}, {}, {}
        # vencimento
        if p.get('venc'):
            try:
                nova = datetime.strptime(p['venc'], '%Y-%m-%d').date()
            except ValueError:
                erros.append('%s: data inválida.' % l['empresa'])
                continue
            if nova < hoje:
                erros.append('%s: o vencimento não pode ser antes de hoje.' % l['empresa'])
                continue
            antes['vencimento'] = l['vencimento'].strftime('%d/%m/%Y')
            if p.get('venc_vale') == 'sempre':
                dados['dia_vencimento'] = nova.day
                esc['vencimento'] = None if nova == lote_mensal.vencimento_para(nova.day, comp) else nova
                antes['dia do contrato'] = c['dia_vencimento']
                depois['dia do contrato'] = nova.day
                depois['vencimento'] = nova.strftime('%d/%m/%Y')
            else:
                esc['vencimento'] = nova
                depois['vencimento'] = nova.strftime('%d/%m/%Y') + sc
        # banco
        if p.get('banco'):
            conta_id = int(p['banco'])
            if conta_id not in contas:
                erros.append('%s: banco inválido.' % l['empresa'])
                continue
            regra = lote_mensal.regra_banco(c)
            antes['banco'] = (contas.get(l.get('conta_id') or padrao) or {}).get('nome')
            if p.get('banco_vale') == 'sempre':
                dados['banco_boleto'] = None if conta_id == padrao else str(conta_id)
                esc['conta_cobranca_id'] = None
                depois['banco'] = contas[conta_id]['nome'] + ' (sempre)'
            else:
                esc['conta_cobranca_id'] = None if conta_id == (regra or padrao) else conta_id
                depois['banco'] = contas[conta_id]['nome'] + sc
        # honorários
        novo_hon = None
        if p.get('valor'):
            try:
                novo_hon = _dec_alt(p['valor'])
            except (InvalidOperation, ValueError):
                erros.append('%s: valor inválido.' % l['empresa'])
                continue
            if l.get('honorarios') is None:
                erros.append('%s: o contrato tem mais de uma linha de honorários; mude o valor abrindo o contrato.' % l['empresa'])
                continue
            if novo_hon <= 0:
                erros.append('%s: o valor precisa ser maior que zero.' % l['empresa'])
                continue
            antes['honorários'] = 'R$ ' + _brl_alt(l['honorarios'])
            if p.get('valor_vale') == 'sempre':
                esc['valor_honorarios'] = None
                depois['honorários'] = 'R$ %s (no contrato, daqui em diante)' % _brl_alt(novo_hon)
            else:
                esc['valor_honorarios'] = None if novo_hon == l['hon_contrato'] else novo_hon
                depois['honorários'] = 'R$ ' + _brl_alt(novo_hon) + sc
        if not antes:
            continue
        if dados:
            FinContrato.atualizar(c['id'], dados)
        if novo_hon is not None and p.get('valor_vale') == 'sempre' and novo_hon != l['hon_contrato']:
            itens = FinContrato.itens(c['id'])
            k = lote_mensal.linha_honorarios(itens)
            novos = [{'descricao': i['descricao'], 'valor': (novo_hon if n == k else i['valor']), 'servico_id': i['servico_id']}
                     for n, i in enumerate(itens)]
            total = sum((Decimal(str(i['valor'])) for i in novos), Decimal('0'))
            # fica no histórico de reajustes do contrato (tipo MANUAL), com o antes, para desfazer
            execute_query(
                """INSERT INTO fin_contrato_reajustes (contrato_id, cliente_id, lote, tipo, vigencia, percentual, valor_antes, valor_depois,
                                                       itens_antes, proximo_antes, aplicado_por)
                   VALUES (%s, %s, %s, 'MANUAL', %s, %s, %s, %s, %s, %s, %s)""",
                (c['id'], c['cliente_id'], 'ALT-' + datetime.now().strftime('%Y%m%d%H%M%S'), comp,
                 ((total / Decimal(str(c['valor'])) - 1) * 100).quantize(Decimal('0.0001')) if c['valor'] else 0, c['valor'], total,
                 json.dumps([{'descricao': i['descricao'], 'valor': str(i['valor']), 'servico_id': i['servico_id']} for i in itens]),
                 c.get('proximo_reajuste'), current_user.id), fetch=False)
            FinContrato.atualizar(c['id'], {'valor': total}, novos)
        if esc:
            cols = list(esc)
            execute_query(
                'INSERT INTO fin_honorarios_escolhas (empresa_id, competencia, cliente_id, usuario_id, %s) VALUES (%%s, %%s, %%s, %%s, %s) '
                'ON DUPLICATE KEY UPDATE usuario_id = VALUES(usuario_id), %s'
                % (', '.join(cols), ', '.join(['%s'] * len(cols)), ', '.join('%s = VALUES(%s)' % (k, k) for k in cols)),
                (EMP, comp, cid, current_user.id) + tuple(esc[k] for k in cols), fetch=False)
        antes['empresa'] = depois['empresa'] = l['empresa']
        registrar('escrita.honorarios_alterar', 'financeiro', tabela='fin_contratos', registro_id=c['id'], antes=antes, depois=depois)
        feitas.append(l['empresa'])
    if feitas:
        flash(('Alterado: %s.' % ', '.join(feitas)) if len(feitas) <= 3 else '%d empresas alteradas.' % len(feitas), 'success')
    return jsonify({'ok': bool(feitas) or not erros, 'feitas': len(feitas), 'erros': erros})


@financeiro.route('/financeiro/honorarios/banco-padrao', methods=['POST'])
@permission_required('financeiro.lote')
def honorarios_banco_padrao():
    from models.fin_contrato import EMPRESA_QUALICONTAX as EMP
    conta_id = request.form.get('conta_id', type=int)
    contas = _contas_emissoras()
    if conta_id not in contas:
        flash('Banco inválido.', 'danger')
    else:
        execute_query('UPDATE fin_cobranca_contas SET padrao = (id = %s) WHERE empresa_id = %s', (conta_id, EMP), fetch=False)
        registrar('escrita.banco_padrao_cobranca', 'financeiro', tabela='fin_cobranca_contas', registro_id=conta_id,
                  depois={'padrao': contas[conta_id]['nome']})
        flash('%s agora é o banco padrão: as cobranças sem banco escolhido saem por ele.' % contas[conta_id]['nome'], 'success')
    return redirect(url_for('financeiro.honorarios', competencia=request.form.get('competencia') or None))


# ---------------------------------------------------------------------------
# Etapa 5 (06/11/2026): funcionário autorizado cadastra (PENDENTE), Anderson aprova; convite por link
# ---------------------------------------------------------------------------
PERM_CAD = 'financeiro.honorarios_cadastrar'


def _ler_form(f):
    """(itens, dados) do formulário do contrato — o mesmo para o admin, o autorizado e o convite."""
    itens = []
    for d, v, s in zip(f.getlist('item_desc'), f.getlist('item_valor'), f.getlist('item_servico')):
        d = (d or '').strip()
        if not d and not (v or '').strip():
            continue
        itens.append({'descricao': d, 'valor': _dec(v), 'servico_id': int(s) if (s or '').isdigit() else None})
    if not itens:
        raise ValueError('Informe ao menos uma discriminação com valor.')
    dia = f.get('dia_vencimento', type=int)
    if not dia or not 1 <= dia <= 31:
        raise ValueError('Dia de vencimento entre 1 e 31.')
    inicio = _data(f.get('inicio'))
    if not inicio:
        raise ValueError('Informe o início do contrato.')
    decimo_tipo = f.get('decimo_tipo') or 'NENHUM'
    parcelas = _parcelas_proprio(f) if decimo_tipo == 'PROPRIO' else None
    reajuste_tipo = f.get('reajuste_tipo') or 'SALARIO_MINIMO'
    prox = _data(f.get('proximo_reajuste'))
    if reajuste_tipo == 'SALARIO_MINIMO' and not prox:
        prox = primeiro_reajuste(inicio)
    dados = dict(valor=sum(i['valor'] for i in itens), dia_vencimento=dia, inicio=inicio, fim=_data(f.get('fim')),
                 decimo_tipo=decimo_tipo, decimo_parcelas=parcelas, **_desconto_form(f, sum(i['valor'] for i in itens)),
                 decimo_desde=decimo_desde_de(f.get('decimo_inicio') or 'PROPORCIONAL', inicio, _data(f.get('decimo_desde'))), reajuste_tipo=reajuste_tipo, proximo_reajuste=prox,
                 reajuste_obs=(f.get('reajuste_obs') or '').strip() or None,
                 gerar_nfse=1 if f.get('gerar_nfse') else 0, gerar_boleto=1 if f.get('gerar_boleto') else 0,
                 observacao=(f.get('observacao') or '').strip() or None)
    return itens, dados


def _criar_pendente(cliente_id, f):
    itens, dados = _ler_form(f)
    cid = FinContrato.criar(cliente_id=cliente_id, itens=itens, criado_por=current_user.id, aprovado_por=None,
                            situacao='PENDENTE', **dados)
    _gravar_grupo(cliente_id, f)
    novo = FinContrato.get(cid)
    registrar('escrita.cadastrou_honorarios_pendente', 'financeiro', tabela='fin_contratos', registro_id=cid,
              depois=dict(_foto(novo), cliente_id=cliente_id, empresa=novo['nome_razao_social']))
    return novo


@financeiro.route('/financeiro/honorarios/cadastrar')
@permission_required(PERM_CAD)
def honorarios_cadastrar():
    """A tela de quem cadastra: as empresas que ELE cadastrou e ainda não têm honorários, e o que já
    enviou (só a situação, sem valores — 'para eles essa tela não aparecerá mais com os valores')."""
    minhas = [c for c in FinContrato.clientes_sem_contrato() if c.get('criado_por') == current_user.id]
    enviados = FinContrato.listar(criado_por=current_user.id)
    convites = execute_query(
        """SELECT v.token, v.expira_em, cl.nome_razao_social, cl.numero_cliente FROM fin_honorarios_convites v
             JOIN clientes cl ON cl.id = v.cliente_id
            WHERE v.usado_em IS NULL AND v.expira_em > NOW() AND (v.usuario_id = %s OR v.usuario_id IS NULL)
            ORDER BY v.id DESC""", (current_user.id,), fetch=True) or []
    aba = request.args.get('aba') or 'sem'
    return render_template('financeiro/honorarios.html', modo='cadastrar', aba=aba, busca=None, contratos=enviados,
                           sem_contrato=minhas, convites_meus=convites, resumo={}, servicos=_servicos(), grupos=_grupos(),
                           DECIMO_TIPOS=DECIMO_TIPOS, DECIMO_INICIOS=DECIMO_INICIOS, REAJUSTE_TIPOS=REAJUSTE_TIPOS, hoje=date.today(),
                           SERVICO_HONORARIOS=SERVICO_HONORARIOS, acao_form=url_for('financeiro.honorarios_cadastrar_salvar'))


@financeiro.route('/financeiro/honorarios/cadastrar/salvar', methods=['POST'])
@permission_required(PERM_CAD)
def honorarios_cadastrar_salvar():
    cliente_id = request.form.get('cliente_id', type=int)
    try:
        ok = any(c['id'] == cliente_id and c.get('criado_por') == current_user.id for c in FinContrato.clientes_sem_contrato())
        if not ok:
            raise ValueError('Você só cadastra honorários das empresas que você mesmo cadastrou e que ainda não têm contrato.')
        novo = _criar_pendente(cliente_id, request.form)
        flash('Honorários de %s enviados para aprovação.' % novo['nome_razao_social'], 'success')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
    return redirect(url_for('financeiro.honorarios_cadastrar'))


@financeiro.route('/financeiro/honorarios/<int:cid>/aprovar', methods=['POST'])
@permission_required(PERM)
def honorarios_aprovar(cid):
    c = FinContrato.get(cid)
    if not c or c['situacao'] != 'PENDENTE':
        flash('Este contrato não está aguardando aprovação.', 'danger')
        return redirect(url_for('financeiro.honorarios', aba='pendentes'))
    FinContrato.atualizar(cid, {'situacao': 'ATIVO', 'aprovado_por': current_user.id})
    registrar('escrita.aprovou_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
              antes={'situacao': 'PENDENTE'}, depois=dict(_foto(FinContrato.get(cid)), empresa=c['nome_razao_social']))
    flash('Honorários de %s aprovados: entram no próximo lote.' % c['nome_razao_social'], 'success')
    return redirect(url_for('financeiro.honorarios', aba='pendentes'))


@financeiro.route('/financeiro/honorarios/<int:cid>/reprovar', methods=['POST'])
@permission_required(PERM)
def honorarios_reprovar(cid):
    c = FinContrato.get(cid)
    if not c or c['situacao'] != 'PENDENTE':
        flash('Este contrato não está aguardando aprovação.', 'danger')
        return redirect(url_for('financeiro.honorarios', aba='pendentes'))
    motivo = (request.form.get('motivo') or '').strip()
    FinContrato.atualizar(cid, {'situacao': 'REPROVADO', 'observacao': ('Reprovado: ' + motivo)[:255] if motivo else 'Reprovado'})
    registrar('escrita.reprovou_honorarios', 'financeiro', tabela='fin_contratos', registro_id=cid,
              antes={'situacao': 'PENDENTE'}, depois={'situacao': 'REPROVADO', 'motivo': motivo, 'empresa': c['nome_razao_social']})
    flash('Honorários de %s reprovados. A empresa volta para "Sem honorários".' % c['nome_razao_social'], 'info')
    return redirect(url_for('financeiro.honorarios', aba='pendentes'))


@financeiro.route('/financeiro/honorarios/convite', methods=['POST'])
@permission_required(PERM)
def honorarios_convite_novo():
    import secrets
    cliente_id = request.form.get('cliente_id', type=int)
    usuario_id = request.form.get('usuario_id', type=int) or None
    dias = request.form.get('dias', type=int) or 7
    cl = execute_query('SELECT id, nome_razao_social FROM clientes WHERE id = %s', (cliente_id,), fetch=True, fetch_one=True)
    if not cl:
        flash('Empresa não encontrada.', 'danger')
        return redirect(url_for('financeiro.honorarios', aba='sem'))
    token = secrets.token_urlsafe(24)
    execute_query('INSERT INTO fin_honorarios_convites (token, cliente_id, usuario_id, criado_por, expira_em) '
                  'VALUES (%s, %s, %s, %s, DATE_ADD(NOW(), INTERVAL %s DAY))',
                  (token, cliente_id, usuario_id, current_user.id, dias), fetch=False)
    link = url_for('financeiro.honorarios_convite', token=token, _external=True)
    registrar('escrita.gerou_convite_honorarios', 'financeiro', tabela='fin_honorarios_convites', registro_id=None,
              depois={'cliente_id': cliente_id, 'empresa': cl['nome_razao_social'], 'usuario_id': usuario_id, 'dias': dias})
    flash('Link para cadastrar os honorários de %s (vale %d dias, uso único, exige login): %s' % (cl['nome_razao_social'], dias, link), 'success')
    return redirect(url_for('financeiro.honorarios', aba='sem', busca=cl['nome_razao_social'][:20]))


def _convite_valido(token):
    v = execute_query('SELECT * FROM fin_honorarios_convites WHERE token = %s', (token,), fetch=True, fetch_one=True)
    if not v:
        return None, 'Este link não existe.'
    if v['usado_em']:
        return None, 'Este link já foi usado: os honorários foram cadastrados e aguardam aprovação.'
    if v['expira_em'] < datetime.now():
        return None, 'Este link venceu. Peça outro ao Anderson.'
    if v['usuario_id'] and v['usuario_id'] != current_user.id:
        return None, 'Este link foi feito para outra pessoa.'
    return v, None


@financeiro.route('/financeiro/honorarios/convite/<token>')
@login_required
def honorarios_convite(token):
    """Só login: é o token que diz a empresa (e, se houver, a pessoa)."""
    v, erro = _convite_valido(token)
    if erro:
        flash(erro, 'warning')
        return redirect('/')
    cl = execute_query('SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE id = %s', (v['cliente_id'],), fetch=True, fetch_one=True)
    if FinContrato.por_cliente(v['cliente_id']):
        flash('%s já tem contrato de honorários cadastrado.' % cl['nome_razao_social'], 'info')
        return redirect('/')
    return render_template('financeiro/honorarios.html', modo='convite', aba='sem', busca=None, contratos=[], sem_contrato=[],
                           convite=dict(v, nome_razao_social=cl['nome_razao_social'], numero_cliente=cl['numero_cliente']),
                           resumo={}, servicos=_servicos(), grupos=_grupos(), DECIMO_TIPOS=DECIMO_TIPOS, DECIMO_INICIOS=DECIMO_INICIOS, REAJUSTE_TIPOS=REAJUSTE_TIPOS,
                           hoje=date.today(), SERVICO_HONORARIOS=SERVICO_HONORARIOS,
                           acao_form=url_for('financeiro.honorarios_convite_salvar', token=token))


@financeiro.route('/financeiro/honorarios/convite/<token>/salvar', methods=['POST'])
@login_required
def honorarios_convite_salvar(token):
    v, erro = _convite_valido(token)
    if erro:
        flash(erro, 'warning')
        return redirect('/')
    try:
        novo = _criar_pendente(v['cliente_id'], request.form)
        execute_query('UPDATE fin_honorarios_convites SET usado_em = NOW(), usado_por = %s, contrato_id = %s WHERE id = %s',
                      (current_user.id, novo['id'], v['id']), fetch=False)
        flash('Honorários de %s enviados para aprovação do Anderson. Obrigado!' % novo['nome_razao_social'], 'success')
        return redirect('/')
    except (ValueError, InvalidOperation) as e:
        flash(str(e) if isinstance(e, ValueError) else 'Valor inválido.', 'danger')
        return redirect(url_for('financeiro.honorarios_convite', token=token))
