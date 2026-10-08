# -*- coding: utf-8 -*-
"""Os cinco avisos por e-mail do escritório — 05/10/2026.

Modelos e textos aprovados pelo Anderson (artifact "E-mails do Qualicontax").
REGRA DOS TEXTOS: quem lê é a equipe toda (fiscal, DP, contábil, legalização).
Nada de SEFAZ, captura, _ENTRADA: dizer por que importa e o que fazer.

  cert_hoje       dias úteis 07:30  vence amanhã (sexta: até segunda), hoje, vencidos
  cert_semana     segunda 07:30     vencem nesta semana e na próxima
  cert_mes        1º dia útil 07:30 vencem no mês, vencidos, sem certificado
  cert_recebidos  dias úteis 18:30  certificados recebidos desde o último aviso
  cadastros       dias úteis 18:30  clientes e grupos NOVOS e alterações cadastrais desde o último aviso

QUEM RECEBE: o e-mail do DEPARTAMENTO (``departamentos.email``), marcado na
grade Config › E-mails automáticos (``email_relatorio_destino``). Nunca o
e-mail do usuário — o pessoal cadastra gmail e alias.

NUNCA DUAS VEZES: o envio de verdade reserva ``email_envio.chave_dia``
(relatorio|AAAA-MM-DD, UNIQUE) ANTES de mandar. Duas rodadas do cron ao mesmo
tempo: só uma ganha o INSERT. Teste não reserva nada.

EMPRESA SEM CERTIFICADO não é cobrada: só aparece no mensal.

Horário: Brasil sem horário de verão desde 2019 — UTC-3 fixo, igual ao
``time_zone='-03:00'`` das conexões. Feriado ainda não é considerado.
"""
import logging
import os
from datetime import date, datetime, time, timedelta, timezone

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

BRT = timezone(timedelta(hours=-3))
APP_URL = os.getenv('APP_URL', 'https://app.qualicontax.com.br').rstrip('/')
URL_CERT = APP_URL + '/escrita-fiscal/status-sefaz/#certificados'
URL_CLIENTES = APP_URL + '/clientes'

RELATORIOS = {
    'cert_hoje':      {'nome': 'Certificados que precisam de você', 'quando': 'dias úteis, 07:30', 'hora': time(7, 30)},
    'cert_semana':    {'nome': 'Certificados da semana',            'quando': 'segunda, 07:30',    'hora': time(7, 30)},
    'cert_mes':       {'nome': 'Certificados do mês',               'quando': '1º dia útil do mês, 07:30', 'hora': time(7, 30)},
    'cert_recebidos': {'nome': 'Certificados recebidos hoje',       'quando': 'dias úteis, 18:30', 'hora': time(18, 30)},
    'cadastros':      {'nome': 'Cadastros (novos e alterações)',    'quando': 'dias úteis, 18:30', 'hora': time(18, 30)},
}

DIAS = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']
DIAS_CURTO = ['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom']
MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho',
         'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']

COMO_RESOLVER = [
    'Peça ao cliente o certificado digital renovado (arquivo .pfx ou .p12) e a senha.',
    'Envie pelo Q-Colabore com a senha no nome do arquivo, por exemplo '
    '<i>empresa 483 senha 1234.pfx</i>, ou abra a ficha da empresa e use <i>Certificado</i>.',
    'Pronto. A empresa sai desta lista no mesmo dia.',
]


# ---------------------------------------------------------------- calendário
def agora():
    return datetime.now(BRT).replace(tzinfo=None)


def dia_util(d):
    return d.weekday() < 5


def proximo_dia_util(d):
    d += timedelta(days=1)
    while not dia_util(d):
        d += timedelta(days=1)
    return d


def primeiro_dia_util_do_mes(d):
    p = d.replace(day=1)
    while not dia_util(p):
        p += timedelta(days=1)
    return p


def devido_hoje(rel, hoje):
    """O relatório tem dia de sair hoje? (a hora é conferida por quem chama)"""
    if not dia_util(hoje):
        return False
    if rel == 'cert_semana':
        return hoje.weekday() == 0
    if rel == 'cert_mes':
        return hoje == primeiro_dia_util_do_mes(hoje)
    return True


def _fmt(d):
    return d.strftime('%d/%m/%Y') if d else '—'


def _dias_txt(n):
    return f'{n} dia' if abs(n) == 1 else f'{n} dias'


# ---------------------------------------------------------------- dados
def _certificados():
    """Um registro por certificado (titular + validade), juntando as filiais
    que usam o mesmo — E. A. Energia 500 a 507 vira uma linha só."""
    rows = execute_query(
        "SELECT c.id, c.numero_cliente, c.nome_razao_social, d.cnpj, d.validade "
        "  FROM dfe_certificados d JOIN clientes c ON c.id = d.cliente_id "
        " WHERE c.situacao = 'ATIVO' AND d.validade IS NOT NULL "
        " ORDER BY d.validade, CAST(c.numero_cliente AS UNSIGNED), c.nome_razao_social",
        fetch=True) or []
    grupos = {}
    for r in rows:
        k = (r['cnpj'], r['validade'])
        grupos.setdefault(k, []).append(r)
    saida = []
    for (_cnpj, validade), itens in grupos.items():
        saida.append({'validade': validade, 'empresas': itens})
    saida.sort(key=lambda g: (g['validade'], g['empresas'][0]['nome_razao_social']))
    return saida


def _numeros(itens):
    nums = [i.get('numero_cliente') for i in itens if (i.get('numero_cliente') or '').strip()]
    if not nums:
        return '—'
    if len(nums) == 1:
        return nums[0]
    try:
        ints = sorted(int(n) for n in nums)
        if ints == list(range(ints[0], ints[-1] + 1)):
            return f'{ints[0]}–{ints[-1]}'
    except ValueError:
        pass
    return ', '.join(nums)


def _linha_cert(g, hoje, rotulo, tom, dt_curto=False):
    itens = g['empresas']
    sub = f'{len(itens)} estabelecimentos, mesmo certificado' if len(itens) > 1 else ''
    v = g['validade']
    dt = f"{DIAS_CURTO[v.weekday()]} {v.strftime('%d/%m')}" if dt_curto else _fmt(v)
    return {'n': _numeros(itens), 'emp': itens[0]['nome_razao_social'], 'sub': sub,
            'dt': dt, 'pill': rotulo, 'tom': tom,
            'url': f"{APP_URL}/clientes/{itens[0]['id']}", 'qtd': len(itens)}


def _qtd(linhas):
    return sum(l.get('qtd', 1) for l in linhas)


def _vencidos(certs, hoje):
    return [_linha_cert(g, hoje, f'vencido há {_dias_txt((hoje - g["validade"]).days)}', 'r')
            for g in sorted(certs, key=lambda g: g['validade']) if g['validade'] < hoje]


# ---------------------------------------------------------------- relatórios
def dados_cert_hoje(hoje, **_):
    certs = _certificados()
    ate = proximo_dia_util(hoje)            # sexta: sábado, domingo e segunda
    amanha = [_linha_cert(g, hoje, 'vence amanhã' if (g['validade'] - hoje).days == 1
                          else f"vence {DIAS[g['validade'].weekday()]}", 'a')
              for g in certs if hoje < g['validade'] <= ate]
    hoje_l = [_linha_cert(g, hoje, 'vence hoje', 'r') for g in certs if g['validade'] == hoje]
    venc = _vencidos(certs, hoje)
    n_am, n_hj, n_vc = _qtd(amanha), _qtd(hoje_l), _qtd(venc)
    rot_am = 'Vence amanhã' if ate == hoje + timedelta(days=1) else f'Vence até {DIAS[ate.weekday()]}'
    partes = []
    if n_am:
        partes.append(f"{n_am} empresa{'s' if n_am > 1 else ''} vence{'m' if n_am > 1 else ''} "
                      + ('amanhã' if ate == hoje + timedelta(days=1) else f'até {DIAS[ate.weekday()]}'))
    if n_hj:
        partes.append(f"{n_hj} vence{'m' if n_hj > 1 else ''} hoje")
    if n_vc:
        partes.append(f"{n_vc} {'estão vencidas' if n_vc > 1 else 'está vencida'}")
    # Só a primeira parte diz "empresa(s)": "1 empresa vence amanhã e 8 estão vencidas".
    if partes and ' empresa' not in partes[0]:
        n0, resto = partes[0].split(' ', 1)
        partes[0] = f"{n0} empresa{'s' if n0 != '1' else ''} {resto}"
    return {
        'itens': n_am + n_hj + n_vc,
        'assunto': 'Certificado digital: ' + (' e '.join([', '.join(partes[:-1]), partes[-1]])
                                              if len(partes) > 1 else (partes[0] if partes else 'nada pendente')),
        'titulo': 'Certificados digitais que precisam de você',
        'data_txt': f'{DIAS[hoje.weekday()]}, {_fmt(hoje)}',
        'intro': ('O certificado digital é a assinatura da empresa na internet. Sem ele válido, '
                  'o escritório não consegue entregar declarações, emitir guias, acessar o eSocial '
                  'e os portais do governo, nem buscar as notas fiscais da empresa. Se você atende '
                  'alguma das empresas abaixo, fale com o cliente hoje.'),
        'kpis': [(rot_am, n_am, 'a'), ('Vence hoje', n_hj, 'r' if n_hj else ''), ('Vencidos', n_vc, 'r')],
        'secoes': [s for s in [
            {'titulo': f'{rot_am}: peça a renovação hoje', 'linhas': amanha},
            {'titulo': 'Vence hoje: a empresa fica sem certificado amanhã', 'linhas': hoje_l},
            {'titulo': 'Vencido: a empresa está sem certificado', 'linhas': venc},
        ] if s['linhas']],
        'como_resolver': True, 'botao': ('Ver certificados no Qualicontax', URL_CERT),
    }


def dados_cert_semana(hoje, **_):
    certs = _certificados()
    seg = hoje - timedelta(days=hoje.weekday())
    dom1, seg2, dom2 = seg + timedelta(days=6), seg + timedelta(days=7), seg + timedelta(days=13)

    def faixa(a, b, tom_perto):
        out = []
        for g in certs:
            if max(a, hoje) <= g['validade'] <= b:
                n = (g['validade'] - hoje).days
                out.append(_linha_cert(g, hoje, 'vence hoje' if n == 0 else f'em {_dias_txt(n)}',
                                       tom_perto if n <= 6 else 'n', dt_curto=True))
        return out
    esta, prox = faixa(seg, dom1, 'a'), faixa(seg2, dom2, 'a')
    n1, n2 = _qtd(esta), _qtd(prox)
    total = n1 + n2
    n_venc = _qtd(_vencidos(certs, hoje))
    return {
        'itens': total,
        'assunto': (f"Certificado digital: {total} empresa{'s' if total != 1 else ''} "
                    f"vence{'m' if total != 1 else ''} nas próximas duas semanas") if total
                   else 'Certificado digital: nenhum vencimento nas próximas duas semanas',
        'titulo': 'Vencem nas próximas duas semanas',
        'data_txt': f"semana de {seg.strftime('%d/%m')} a {dom2.strftime('%d/%m')}",
        'intro': ('Ainda dá tempo de renovar sem a empresa ficar travada. Avise o cliente agora: '
                  'a renovação pode levar alguns dias entre compra, agendamento e validação.'
                  + (' As empresas já vencidas aparecem no aviso diário.' if n_venc else '')),
        'kpis': [('Esta semana', n1, 'a'), ('Próxima', n2, '')],
        'secoes': [s for s in [
            {'titulo': f"Esta semana · {seg.strftime('%d')} a {dom1.strftime('%d/%m')}", 'linhas': esta},
            {'titulo': f"Próxima semana · {seg2.strftime('%d')} a {dom2.strftime('%d/%m')}", 'linhas': prox},
        ] if s['linhas']],
        'vazio': 'Nenhuma empresa tem certificado vencendo nestas duas semanas.',
        'como_resolver': bool(total), 'botao': ('Ver certificados no Qualicontax', URL_CERT),
    }


def dados_cert_mes(hoje, **_):
    certs = _certificados()
    fim = (hoje.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    mes = MESES[hoje.month - 1]
    no_mes = []
    for g in certs:
        if hoje <= g['validade'] <= fim:
            n = (g['validade'] - hoje).days
            no_mes.append(_linha_cert(g, hoje, 'vence hoje' if n == 0 else f'em {_dias_txt(n)}',
                                      'a' if n <= 7 else 'n'))
    venc = _vencidos(certs, hoje)
    sem = execute_query(
        "SELECT c.id, c.numero_cliente, c.nome_razao_social FROM clientes c "
        "  LEFT JOIN dfe_certificados d ON d.cliente_id = c.id "
        " WHERE d.id IS NULL AND c.avulso = 0 AND c.situacao = 'ATIVO' "
        " ORDER BY CAST(c.numero_cliente AS UNSIGNED), c.nome_razao_social", fetch=True) or []
    sem_l = [{'n': r['numero_cliente'] or '—', 'emp': r['nome_razao_social'], 'sub': '', 'dt': '',
              'pill': 'sem certificado', 'tom': 'n', 'url': f"{APP_URL}/clientes/{r['id']}"} for r in sem]
    n1, n2, n3 = _qtd(no_mes), _qtd(venc), len(sem_l)
    return {
        'itens': n1 + n2 + n3,
        'assunto': (f'Certificados de {mes}: {n1} empresa{"s" if n1 != 1 else ""} vence{"m" if n1 != 1 else ""} '
                    f'no mês, {n2} vencida{"s" if n2 != 1 else ""} e {n3} sem certificado'),
        'titulo': f'Certificados digitais em {mes}',
        'data_txt': f'{mes} de {hoje.year}',
        'intro': ('O resumo do mês para planejar as renovações. Confira se as suas empresas estão '
                  'aqui e fale com o cliente com antecedência.'),
        'kpis': [('Vencem no mês', n1, 'a'), ('Vencidos', n2, 'r'), ('Sem certificado', n3, '')],
        'secoes': [s for s in [
            {'titulo': f'Vencem em {mes}: programe a renovação', 'linhas': no_mes},
            {'titulo': 'Vencidos, ainda sem renovação', 'linhas': venc},
            {'titulo': f'Sem certificado no Qualicontax · {n3}', 'linhas': sem_l,
             'nota': ('Estas empresas nunca tiveram certificado enviado para o sistema. Se a empresa '
                      'tem certificado digital, peça ao cliente e envie. Assim o escritório passa a '
                      'fazer por ela o que hoje depende de pedir ao cliente.')},
        ] if s['linhas']],
        'como_resolver': True, 'botao': ('Ver certificados no Qualicontax', URL_CERT),
    }


def _janela(rel, ate):
    """Desde o último aviso deste relatório (enviado ou vazio) até agora — assim
    o que entra depois das 18:30 aparece no aviso seguinte, e o de segunda cobre
    o fim de semana. Sem histórico: desde o começo do dia. Nunca mais de 4 dias."""
    r = execute_query(
        "SELECT MAX(concluido_em) t FROM email_envio "
        " WHERE relatorio = %s AND teste = 0 AND status IN ('ENVIADO','VAZIO')",
        (rel,), fetch=True, fetch_one=True) or {}
    ini = r.get('t') or datetime.combine(ate.date(), time(0, 0))
    return max(ini, ate - timedelta(days=4)), ate


def _desde_txt(ini, fim):
    if ini.date() == fim.date():
        return f'{DIAS[fim.weekday()]}, {_fmt(fim.date())}'
    return f"desde {DIAS[ini.weekday()]} {ini.strftime('%d/%m %H:%M')}"


def dados_cert_recebidos(hoje, momento=None, rel='cert_recebidos', **_):
    momento = momento or agora()
    ini, fim = _janela(rel, momento)
    rows = execute_query(
        "SELECT v.vinculado_em, v.origem, v.usuario_nome, v.validade, v.validade_anterior, "
        "       c.id, c.numero_cliente, c.nome_razao_social "
        "  FROM certificado_vinculo v JOIN clientes c ON c.id = v.cliente_id "
        " WHERE v.vinculado_em >= %s AND v.vinculado_em < %s ORDER BY v.vinculado_em",
        (ini, fim), fetch=True) or []
    linhas, ficha, arq = [], 0, 0
    for r in rows:
        h = r['vinculado_em'].strftime('%H:%M' if ini.date() == fim.date() else '%d/%m %H:%M')
        if r['origem'] == 'FICHA':
            ficha += 1
            como = f"enviado por {r['usuario_nome'] or 'usuário não identificado'}, pela ficha da empresa"
        elif r['origem'] == 'QCOLABORE':
            arq += 1
            como = f"enviado por {r['usuario_nome'] or 'usuário não identificado'}, pelo Q-Colabore"
        else:
            arq += 1
            como = 'chegou por arquivo no Dropbox · quem enviou não foi registrado'
        sub = f'{h} · {como}'
        if r.get('validade_anterior'):
            sub += f" · substitui o que vencia em {_fmt(r['validade_anterior'])}"
        linhas.append({'n': r['numero_cliente'] or '—', 'emp': r['nome_razao_social'], 'sub': sub,
                       'dt': '', 'pill': f"válido até {_fmt(r['validade'])}", 'tom': 'g',
                       'url': f"{APP_URL}/clientes/{r['id']}"})
    n = len(linhas)
    return {
        'itens': n, 'janela': (ini, fim),
        'assunto': f"{n} empresa{'s' if n != 1 else ''} com certificado novo"
                   + (' hoje' if ini.date() == fim.date() else ''),
        'titulo': 'Certificados recebidos hoje' if ini.date() == fim.date() else 'Certificados recebidos',
        'data_txt': _desde_txt(ini, fim),
        'intro': ('Estas empresas estão com o certificado em dia no Qualicontax e saem dos avisos '
                  'de vencimento. Confira se alguma é sua.'),
        'kpis': [('Recebidos', n, 'g'), ('Pela ficha', ficha, ''), ('Por arquivo', arq, '')],
        'secoes': [{'titulo': 'Recebidos', 'linhas': linhas}] if linhas else [],
        'como_resolver': False, 'botao': ('Ver certificados no Qualicontax', URL_CERT),
    }


def _fmt_doc(d):
    d = ''.join(ch for ch in (d or '') if ch.isdigit())
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    if len(d) == 11:
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    return d


# O que mudou, em palavras da equipe (08/10/2026). Campo de ``alterou_cliente``
# fora desta lista não aparece (motivo, origem e afins são detalhe do log).
_CAMPO_ROTULO = {
    'numero_cliente': 'número', 'tipo_pessoa': 'tipo de pessoa', 'nome_razao_social': 'razão social',
    'cpf_cnpj': 'CNPJ/CPF', 'inscricao_estadual': 'inscrição estadual',
    'inscricao_municipal': 'inscrição municipal', 'email': 'e-mail', 'telefone': 'telefone',
    'celular': 'celular', 'regime_tributario': 'regime', 'porte_empresa': 'porte',
    'cnae_fiscal': 'CNAE', 'cnae_fiscal_descricao': 'CNAE', 'situacao': 'situação',
    'observacoes': 'observações', 'aberta_pela_casa': 'aberta pela Qualicontax',
    'data_inicio_contrato': 'início do contrato', 'data_fim_contrato': 'fim do contrato',
}
# ação do log -> o que mudou (as de alterou_cliente saem dos campos)
_ACAO_ROTULO = {
    'escrita.alterou_grupo_cliente': 'grupo', 'escrita.criou_socio': 'sócios',
    'escrita.excluiu_socio': 'sócios', 'escrita.alterou_socio': 'sócios',
    'escrita.criou_endereco': 'endereço', 'escrita.excluiu_endereco': 'endereço',
    'escrita.alterou_endereco': 'endereço', 'escrita.alterou_ramos_cliente': 'ramo de atividade',
    'escrita.vinculou_contador': 'contador', 'escrita.alterou_acesso_reservado': 'acesso',
}


def _plural(q, um, varios):
    return f'{q} {um if q == 1 else varios}'


def _alteracoes(ini, fim, ignorar):
    """Empresas alteradas na janela, uma linha por empresa com tudo o que mudou.
    ``ignorar``: ids que já aparecem como novos (o cadastro novo não é alteração)."""
    import json
    acoes = ['escrita.alterou_cliente'] + list(_ACAO_ROTULO)
    rows = execute_query(
        "SELECT acao, tabela_afetada, registro_id, dados_anteriores, dados_novos, usuario_nome, data_hora "
        "  FROM logs_sistema WHERE data_hora >= %s AND data_hora < %s AND acao IN (%s) ORDER BY id"
        % ('%s', '%s', ','.join(['%s'] * len(acoes))), (ini, fim, *acoes), fetch=True) or []
    por = {}
    for r in rows:
        try:
            antes = json.loads(r['dados_anteriores'] or '{}') or {}
            depois = json.loads(r['dados_novos'] or '{}') or {}
        except (TypeError, ValueError):
            antes, depois = {}, {}
        if r['tabela_afetada'] == 'clientes':
            cid = r['registro_id']
        else:   # sócio e endereço: o cliente vem dentro do registro
            cid = depois.get('cliente_id') or antes.get('cliente_id')
        if not cid or cid in ignorar:
            continue
        e = por.setdefault(cid, {'valores': {}, 'fixos': [], 'quem': [], 'quando': r['data_hora']})
        # O que dá para comparar fica como (valor do início, valor do fim) da
        # janela: mudou e voltou no mesmo período não é alteração.
        if r['acao'] == 'escrita.alterou_cliente':
            pares = {('c', k): (_CAMPO_ROTULO[k], antes.get(k), depois.get(k))
                     for k in depois if k in _CAMPO_ROTULO}
        elif r['acao'] == 'escrita.alterou_endereco':
            pares = {('e', k): ('endereço', antes.get(k), depois.get(k)) for k in depois}
        elif r['acao'] == 'escrita.alterou_grupo_cliente':
            pares = {('g',): ('grupo', sorted(antes.get('grupos') or []), sorted(depois.get('grupos') or []))}
        elif r['acao'] == 'escrita.alterou_ramos_cliente':
            pares = {('r',): ('ramo de atividade', antes.get('ramos'), depois.get('ramos'))}
        else:
            pares = {}
            if _ACAO_ROTULO[r['acao']] not in e['fixos']:
                e['fixos'].append(_ACAO_ROTULO[r['acao']])
        for k, (rot, v0, v1) in pares.items():
            if k in e['valores']:
                e['valores'][k][2] = v1
            else:
                e['valores'][k] = [rot, v0, v1]
        if r['usuario_nome'] and r['usuario_nome'] not in e['quem']:
            e['quem'].append(r['usuario_nome'])
        e['quando'] = r['data_hora']
    for e in por.values():
        o_que = []
        for rot, v0, v1 in e['valores'].values():
            if v0 != v1 and rot not in o_que:
                o_que.append(rot)
        e['o_que'] = o_que + [f for f in e['fixos'] if f not in o_que]
    por = {cid: e for cid, e in por.items() if e['o_que']}
    if not por:
        return []
    cad = {c['id']: c for c in execute_query(
        "SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE id IN (%s)"
        % ','.join(['%s'] * len(por)), tuple(por), fetch=True) or []}
    return [(cad[cid], e) for cid, e in por.items() if cid in cad]


def _grupos_novos(ini, fim):
    """Grupos criados na janela que ainda existem (o log diz quem criou)."""
    return execute_query(
        "SELECT g.id, g.nome, l.usuario_nome, l.data_hora, "
        "       (SELECT COUNT(*) FROM cliente_grupo_relacao r WHERE r.grupo_id = g.id) AS n_emp "
        "  FROM logs_sistema l JOIN grupos_clientes g ON g.id = l.registro_id "
        " WHERE l.acao = 'escrita.criou_grupo' AND l.data_hora >= %s AND l.data_hora < %s "
        " ORDER BY l.id", (ini, fim), fetch=True) or []


def dados_cadastros(hoje, momento=None, rel='cadastros', **_):
    """Cadastros do dia (08/10/2026, modelo do Anderson): clientes e grupos
    NOVOS e as alterações cadastrais, dizendo o que mudou em cada empresa.
    Assunto: "Cadastros: 1 cliente (NOVO) | 1 Alteração Cadastral | 1 Grupo (NOVO)"."""
    momento = momento or agora()
    ini, fim = _janela(rel, momento)
    um_dia = ini.date() == fim.date()
    hfmt = '%H:%M' if um_dia else '%d/%m %H:%M'
    q = ("SELECT c.id, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj, c.criado_em, "
         "       c.avulso, c.avulso_em, c.virou_cliente_em, u.nome AS criado_por_nome "
         "  FROM clientes c LEFT JOIN usuarios u ON u.id = c.criado_por ")
    novos = execute_query(q + " WHERE c.criado_em >= %s AND c.criado_em < %s AND c.avulso = 0 "
                              "   AND c.virou_cliente_em IS NULL ORDER BY c.criado_em",
                          (ini, fim), fetch=True) or []
    avulsos = execute_query(q + " WHERE c.avulso = 1 AND c.avulso_em >= %s AND c.avulso_em < %s "
                                " ORDER BY c.avulso_em", (ini, fim), fetch=True) or []
    conv = execute_query(q + " WHERE c.virou_cliente_em >= %s AND c.virou_cliente_em < %s "
                             " ORDER BY c.virou_cliente_em", (ini, fim), fetch=True) or []
    # Quem CONVERTEU está na auditoria (o criado_por é de quem criou o avulso).
    quem_conv = {}
    if conv:
        ids = [r['id'] for r in conv]
        for r in execute_query(
                "SELECT registro_id, usuario_nome FROM logs_sistema "
                " WHERE acao = 'escrita.converteu_avulso' AND registro_id IN (%s) ORDER BY id"
                % ','.join(['%s'] * len(ids)), tuple(ids), fetch=True) or []:
            quem_conv[r['registro_id']] = r['usuario_nome']
    # quem foi criado na janela não conta como alteração (o cadastro novo já
    # aparece; o resto do formulário salvo junto é parte de "novo")
    criados = execute_query("SELECT id FROM clientes WHERE criado_em >= %s AND criado_em < %s",
                            (ini, fim), fetch=True) or []
    ignorar = {r['id'] for r in criados} | {r['id'] for r in avulsos} | {r['id'] for r in conv}
    alter = _alteracoes(ini, fim, ignorar)
    grupos = _grupos_novos(ini, fim)

    def linha(r, quando, quem, pill, tom):
        sub = ' · '.join(x for x in [_fmt_doc(r['cpf_cnpj']), quando.strftime(hfmt) if quando else '',
                                     quem or 'quem cadastrou não foi registrado'] if x)
        return {'n': r['numero_cliente'] or '—', 'emp': r['nome_razao_social'], 'sub': sub, 'dt': '',
                'pill': pill, 'tom': tom, 'url': f"{APP_URL}/clientes/{r['id']}"}
    l1 = [linha(r, r['criado_em'], r['criado_por_nome'], 'NOVO', 'g') for r in novos]
    l2 = [linha(r, r['avulso_em'], r['criado_por_nome'], 'avulso NOVO', 'n') for r in avulsos]
    l3 = [linha(r, r['virou_cliente_em'], quem_conv.get(r['id']), 'virou cliente', 'g') for r in conv]
    lg = [{'n': '—', 'emp': g['nome'], 'dt': '', 'pill': 'NOVO', 'tom': 'g', 'url': f'{APP_URL}/grupos',
           'sub': ' · '.join(x for x in [_plural(g['n_emp'], 'empresa', 'empresas'),
                                         g['data_hora'].strftime(hfmt),
                                         g['usuario_nome'] or 'quem criou não foi registrado'] if x)}
          for g in grupos]
    la = [{'n': c['numero_cliente'] or '—', 'emp': c['nome_razao_social'], 'dt': '',
           'pill': 'alterado', 'tom': 'a', 'url': f"{APP_URL}/clientes/{c['id']}",
           'sub': 'mudou: ' + ', '.join(e['o_que']) + ' · ' + e['quando'].strftime(hfmt) + ' · '
                  + (', '.join(e['quem']) or 'quem alterou não foi registrado')}
          for c, e in alter]
    n = len(l1) + len(l2) + len(l3) + len(lg) + len(la)
    partes = [p for q_, p in [
        (len(l1), f"{len(l1)} cliente (NOVO)" if len(l1) == 1 else f"{len(l1)} clientes (NOVOS)"),
        (len(l2), f"{len(l2)} avulso (NOVO)" if len(l2) == 1 else f"{len(l2)} avulsos (NOVOS)"),
        (len(l3), _plural(len(l3), 'avulso virou cliente', 'avulsos viraram clientes')),
        (len(la), _plural(len(la), 'Alteração Cadastral', 'Alterações Cadastrais')),
        (len(lg), f"{len(lg)} Grupo (NOVO)" if len(lg) == 1 else f"{len(lg)} Grupos (NOVOS)"),
    ] if q_]
    kpis = [('Clientes novos', len(l1), 'g'), ('Grupos novos', len(lg), 'g'), ('Alterações', len(la), 'a')]
    if l2:
        kpis.append(('Avulsos', len(l2), ''))
    if l3:
        kpis.append(('Viraram clientes', len(l3), 'g'))
    return {
        'itens': n, 'janela': (ini, fim),
        'assunto': 'Cadastros: ' + (' | '.join(partes) or 'nenhum'),
        'titulo': 'Cadastros',
        'data_txt': _desde_txt(ini, fim),
        'intro': ('O que entrou e o que mudou nos cadastros' + (' hoje' if um_dia else '') + '. '
                  'Se alguma empresa nova vai ficar com você, confira o cadastro e já peça ao cliente '
                  'o certificado digital.'),
        'kpis': kpis,
        'secoes': [s for s in [
            {'titulo': 'Clientes NOVOS', 'linhas': l1},
            {'titulo': 'Grupos NOVOS', 'linhas': lg},
            {'titulo': 'Avulsos NOVOS (ainda não são clientes)', 'linhas': l2},
            {'titulo': 'Avulsos que viraram clientes', 'linhas': l3},
            {'titulo': 'Alterações cadastrais', 'linhas': la},
        ] if s['linhas']],
        'como_resolver': False, 'botao': ('Ver clientes no Qualicontax', URL_CLIENTES),
    }


MONTADORES = {
    'cert_hoje': dados_cert_hoje, 'cert_semana': dados_cert_semana, 'cert_mes': dados_cert_mes,
    'cert_recebidos': dados_cert_recebidos, 'cadastros': dados_cadastros,
}
# Sai mesmo sem nada? (o semanal e o mensal dizem "nenhum"; os outros calam)
SAI_VAZIO = {'cert_semana', 'cert_mes'}


# ---------------------------------------------------------------- render
def _env():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return Environment(loader=FileSystemLoader(os.path.join(raiz, 'templates', 'emails')),
                       autoescape=select_autoescape(['html']))


def render(d):
    """(html, texto) a partir do dicionário de um montador."""
    html = _env().get_template('aviso.html').render(d=d, como=COMO_RESOLVER)
    t = [d['titulo'], d['data_txt'], '', d['intro'], '']
    for s in d['secoes']:
        t.append(s['titulo'].upper())
        for l in s['linhas']:
            t.append(f"  {l['n']}  {l['emp']}  {l.get('dt') or ''}  {l['pill']}".rstrip())
            if l.get('sub'):
                t.append(f"        {l['sub']}")
        t.append('')
    if not d['secoes'] and d.get('vazio'):
        t += [d['vazio'], '']
    if d.get('como_resolver'):
        t.append('COMO RESOLVER')
        import re
        t += [f'  {i}. ' + re.sub(r'<[^>]+>', '', c) for i, c in enumerate(COMO_RESOLVER, 1)]
        t.append('')
    t += [f"{d['botao'][0]}: {d['botao'][1]}", '',
          'Aviso automático do Qualicontax para a equipe do escritório. Não responda a este e-mail.']
    return html, '\n'.join(t)


# ---------------------------------------------------------------- destinos e envio
def destinatarios(rel):
    rows = execute_query(
        "SELECT d.email FROM email_relatorio_destino x JOIN departamentos d ON d.id = x.departamento_id "
        " WHERE x.relatorio = %s AND d.ativo = 1 AND COALESCE(d.email,'') <> ''",
        (rel,), fetch=True) or []
    return list(dict.fromkeys(r['email'].strip().lower() for r in rows))


def _fechar(eid, status, para, assunto, itens, erro=None):
    execute_query(
        "UPDATE email_envio SET status=%s, destinatarios=%s, assunto=%s, itens=%s, erro=%s, "
        " concluido_em=NOW() WHERE id=%s",
        (status, ', '.join(para) or None, (assunto or '')[:255], itens, (erro or '')[:500] or None, eid),
        fetch=False)


def enviar(rel, hoje=None, teste_para=None, usuario_id=None, momento=None):
    """Monta e envia um relatório. ``teste_para`` (lista) = envio de teste: vai só
    para esses endereços, não reserva o dia e não mexe na janela dos avisos.
    Devolve dict ``{status, itens, para, erro, assunto}``."""
    from utils import email_envio
    momento = momento or agora()
    hoje = hoje or momento.date()
    teste = bool(teste_para)
    para = list(teste_para) if teste else destinatarios(rel)
    chave = None if teste else f'{rel}|{hoje.isoformat()}'

    # Reserva ANTES de montar: duas rodadas do cron juntas, só uma passa.
    eid = execute_query(
        "INSERT IGNORE INTO email_envio (relatorio, data_ref, chave_dia, teste, enviado_por) "
        "VALUES (%s, %s, %s, %s, %s)", (rel, hoje, chave, 1 if teste else 0, usuario_id))
    if eid is None:                 # erro de banco, não "já enviado"
        return {'status': 'FALHOU', 'itens': 0, 'para': para, 'assunto': None,
                'erro': 'não consegui registrar o envio no banco'}
    if not eid:                     # INSERT IGNORE caiu no UNIQUE: o dia já saiu
        return {'status': 'JA_ENVIADO', 'itens': 0, 'para': [], 'erro': None, 'assunto': None}

    try:
        d = MONTADORES[rel](hoje, momento=momento, rel=rel)
    except Exception as exc:
        logger.exception('[email] falha ao montar %s.', rel)
        _fechar(eid, 'FALHOU', para, None, 0, f'falha ao montar: {exc}')
        return {'status': 'FALHOU', 'itens': 0, 'para': para, 'erro': str(exc), 'assunto': None}

    if not teste and not d['itens'] and rel not in SAI_VAZIO:
        _fechar(eid, 'VAZIO', [], d['assunto'], 0)
        return {'status': 'VAZIO', 'itens': 0, 'para': [], 'erro': None, 'assunto': d['assunto']}
    if not para:
        _fechar(eid, 'FALHOU', [], d['assunto'], d['itens'], 'nenhum departamento marcado com e-mail')
        return {'status': 'FALHOU', 'itens': d['itens'], 'para': [], 'assunto': d['assunto'],
                'erro': 'nenhum departamento marcado com e-mail'}

    assunto = ('[TESTE] ' if teste else '') + d['assunto']
    html, texto = render(d)
    ok, erro = email_envio.enviar(para, assunto, html, texto)
    _fechar(eid, 'ENVIADO' if ok else 'FALHOU', para, assunto, d['itens'], erro)
    return {'status': 'ENVIADO' if ok else 'FALHOU', 'itens': d['itens'], 'para': para,
            'erro': erro, 'assunto': assunto}


def pendentes(momento=None):
    """Relatórios que já deviam ter saído hoje e ainda não saíram."""
    momento = momento or agora()
    hoje = momento.date()
    feitos = {r['relatorio'] for r in (execute_query(
        "SELECT relatorio FROM email_envio WHERE data_ref = %s AND teste = 0", (hoje,), fetch=True) or [])}
    return [rel for rel, cfg in RELATORIOS.items()
            if rel not in feitos and devido_hoje(rel, hoje) and momento.time() >= cfg['hora']]


def ultimos_envios():
    """Último envio de verdade e último teste de cada relatório, para a tela."""
    rows = execute_query(
        "SELECT e.* FROM email_envio e JOIN ("
        "  SELECT relatorio, teste, MAX(id) mid FROM email_envio GROUP BY relatorio, teste"
        ") m ON m.mid = e.id", fetch=True) or []
    out = {}
    for r in rows:
        out.setdefault(r['relatorio'], {})['teste' if r['teste'] else 'real'] = r
    return out
