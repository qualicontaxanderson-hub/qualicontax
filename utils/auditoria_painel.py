# -*- coding: utf-8 -*-
"""Painel da Auditoria (Configurações → Auditoria), refeito em 11/11/2026.

O registro continua ATO A ATO (logs_sistema, um ato por linha). O que muda é
como se OLHA: o dia de cada pessoa vira um cartão pequeno (hora da primeira e da
última ação, quantas alterações/consultas, em quais empresas e os sinais de
atenção); clicar abre o dia inteiro, que sai em PDF.

Este módulo não grava nada. Ele:
  * entende a BUSCA em português ("anderson ontem #92 e-mail") -> filtros SQL;
  * agrupa as linhas por pessoa+dia e por empresa;
  * calcula os SINAIS DE ATENÇÃO (senha errada, exclusão, contato de cliente
    trocado, exportação em massa, fora do expediente, IP novo, acessos mexidos).

A tradução de cada ato (verbo, empresa, campos) continua em utils/auditoria_fmt.
"""
import re
import time
import unicodedata
from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta

from utils.db_helper import execute_query
from utils.auditoria_fmt import hist_preparar, coletar_cliente_ids, MODULO_LABEL

DIAS_SEMANA = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo']
MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']

# Expediente: antes das 7h, a partir das 20h ou fim de semana = fora.
EXPEDIENTE_INI, EXPEDIENTE_FIM = 7, 20
# Teto de linhas que a visão agrupada lê de uma vez (o período padrão é 7 dias,
# ~150 linhas/dia de gente; a migração de 06/11 fez 1.600 num dia só).
TETO_LINHAS = 8000

_ESTRUTURA = ('_migracao', '_correcao')          # atos em lote (scripts)


def sem_acento(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    return ''.join(c for c in s if not unicodedata.combining(c)).lower().strip()


def eh_lote(nome, acao):
    """Ato aplicado por script (migração/correção) — não é trabalho de balcão."""
    n = (nome or '')
    return ('Claude' in n or 'pedido no chat' in n or 'ROTEADOR' in n
            or (acao or '').endswith(_ESTRUTURA))


def dia_rotulo(d):
    hoje = date.today()
    if d == hoje:
        pre = 'Hoje'
    elif d == hoje - timedelta(days=1):
        pre = 'Ontem'
    else:
        pre = DIAS_SEMANA[d.weekday()].capitalize()
    return '%s · %02d/%02d/%04d' % (pre, d.day, d.month, d.year)


# ───────────────────────────────────────────────────────────── busca inteligente
_ATALHOS = [
    # (palavras, tipo, valor, rótulo do chip)
    (('alterou', 'alteracao', 'alteracoes', 'editou', 'mudou', 'mudancas'), 'tipo', 'escrita', 'Só alterações'),
    (('consultou', 'consulta', 'consultas', 'leitura', 'olhou'), 'tipo', 'leitura', 'Só consultas'),
    (('excluiu', 'exclusao', 'exclusoes', 'apagou', 'removeu'), 'acao', 'excluiu', 'Exclusões'),
    (('criou', 'cadastrou', 'incluiu', 'adicionou'), 'acao', 'criou', 'Inclusões'),
    (('entrou', 'entrada', 'entradas', 'login', 'logins', 'acesso', 'acessos'), 'modulo', 'acesso', 'Entradas no sistema'),
    (('senha', 'senhas', 'recusada', 'recusadas', 'bloqueio'), 'acao', 'recusada', 'Senha/código errado'),
    (('exportou', 'exportacao', 'baixou', 'download', 'pdf', 'excel', 'xlsx'), 'acao', 'exportou', 'Exportações e downloads'),
    (('fiscal',), 'modulo', 'fiscal', 'Módulo Fiscal'),
    (('cadastro', 'cadastros'), 'modulo', 'cadastros', 'Módulo Cadastros'),
    (('financeiro', 'boleto', 'boletos'), 'modulo', 'financeiro', 'Módulo Financeiro'),
    (('dp',), 'modulo', 'dp', 'Módulo DP'),
    (('contabil',), 'modulo', 'contabil', 'Módulo Contábil'),
    (('colabore',), 'modulo', 'colabore', 'Q-Colabore'),
    (('configuracoes', 'config', 'usuarios', 'permissoes', 'perfil', 'perfis'), 'modulo', 'configuracoes', 'Configurações'),
    (('email', 'e-mail', 'emails', 'e-mails'), 'campo', 'email', 'Mexeu em e-mail'),
    (('celular', 'telefone', 'telefones', 'whatsapp'), 'campo', 'fone', 'Mexeu em telefone'),
    (('endereco', 'enderecos', 'cep'), 'campo', 'endereco', 'Mexeu em endereço'),
    (('socio', 'socios'), 'campo', 'socio', 'Sócios'),
    (('certificado', 'certificados'), 'campo', 'certificado', 'Certificado'),
    (('atencao', 'alerta', 'alertas', 'suspeito', 'estranho'), 'atencao', '1', 'Só com sinal de atenção'),
    (('lote', 'lotes', 'migracao', 'script'), 'lote', '1', 'Só atos em lote'),
    (('madrugada', 'noite', 'fds'), 'fora', '1', 'Fora do expediente'),
]
_FRASES = [('fora do horario', 'fora', '1', 'Fora do expediente'),
           ('fora do expediente', 'fora', '1', 'Fora do expediente'),
           ('fim de semana', 'fora', '1', 'Fora do expediente'),
           ('sem lote', 'sem_lote', '1', 'Sem os atos em lote'),
           ('esta semana', 'periodo', 'semana', None), ('este mes', 'periodo', 'mes', None),
           ('mes passado', 'periodo', 'mes_passado', None)]
_PALAVRAS_PERIODO = {'hoje': 'hoje', 'ontem': 'ontem', 'anteontem': 'anteontem',
                     'semana': 'semana', 'mes': 'mes'}
_VAZIAS = {'de', 'do', 'da', 'dos', 'das', 'o', 'a', 'os', 'as', 'em', 'no', 'na', 'nos', 'nas',
           'que', 'quem', 'e', 'com', 'por', 'para', 'pra', 'um', 'uma', 'ate', 'so', 'tudo', 'todos'}

_CAMPOS_JSON = {
    'email': ('email', 'email_cobranca', 'emails'),
    'fone': ('celular', 'telefone', 'celulares', 'telefones'),
    'endereco': ('cep', 'logradouro', 'numero', 'complemento', 'bairro', 'cidade'),
    'socio': ('socio_nome', 'percentual_participacao', 'qualificacao'),
    'certificado': ('validade', 'senha_cifrada', 'tipo_doc'),
}

_cache_pessoas = {'t': 0, 'v': []}


def pessoas_conhecidas():
    """Nomes que aparecem na auditoria (cache 5 min)."""
    if time.time() - _cache_pessoas['t'] > 300:
        rows = execute_query("SELECT DISTINCT usuario_nome FROM logs_sistema "
                             "WHERE usuario_nome IS NOT NULL ORDER BY usuario_nome", fetch=True) or []
        _cache_pessoas['v'] = [r['usuario_nome'] for r in rows]
        _cache_pessoas['t'] = time.time()
    return _cache_pessoas['v']


def _periodo(chave, hoje):
    if chave == 'hoje':
        return hoje, hoje
    if chave == 'ontem':
        d = hoje - timedelta(days=1); return d, d
    if chave == 'anteontem':
        d = hoje - timedelta(days=2); return d, d
    if chave == 'semana':
        return hoje - timedelta(days=hoje.weekday()), hoje
    if chave == 'mes':
        return hoje.replace(day=1), hoje
    if chave == 'mes_passado':
        fim = hoje.replace(day=1) - timedelta(days=1)
        return fim.replace(day=1), fim
    m = re.fullmatch(r'(\d+)d', chave or '')
    if m:
        return hoje - timedelta(days=int(m.group(1)) - 1), hoje
    return None


def _data_br(txt, hoje):
    p = txt.split('/')
    try:
        dd, mm = int(p[0]), int(p[1])
        aa = int(p[2]) if len(p) > 2 and p[2] else hoje.year
        if aa < 100:
            aa += 2000
        return date(aa, mm, dd)
    except (ValueError, IndexError):
        return None


def interpretar(q, hoje=None):
    """Texto livre -> (filtros, chips). Cada chip = {'rotulo', 'tira'}: 'tira' é o
    pedaço do texto que some quando a pessoa clica no X do chip."""
    hoje = hoje or date.today()
    f = {'pessoas': [], 'empresa_ids': [], 'empresa_nums': [], 'tipo': '', 'acoes': [],
         'modulos': [], 'campos': [], 'atencao': False, 'lote': False, 'sem_lote': False,
         'fora': False, 'textos': [], 'data_ini': None, 'data_fim': None}
    chips = []
    if not (q or '').strip():
        return f, chips
    resto = ' ' + sem_acento(q) + ' '

    def tirar(trecho):
        nonlocal resto
        resto = resto.replace(' ' + trecho + ' ', ' ', 1)

    # 1) frases
    for frase, tipo, valor, rot in _FRASES:
        if ' %s ' % frase in resto:
            tirar(frase)
            if tipo == 'periodo':
                f['data_ini'], f['data_fim'] = _periodo(valor, hoje)
                chips.append({'rotulo': 'Período: ' + frase, 'tira': frase})
            else:
                f[tipo] = True
                chips.append({'rotulo': rot, 'tira': frase})
    # 2) "7 dias", "30 dias"
    for m in re.finditer(r' (\d{1,3}) dias? ', resto):
        n = int(m.group(1))
        f['data_ini'], f['data_fim'] = _periodo('%dd' % n, hoje)
        chips.append({'rotulo': 'Últimos %d dias' % n, 'tira': m.group(0).strip()})
    resto = re.sub(r' \d{1,3} dias? ', ' ', resto)
    # 3) datas dd/mm[/aaaa] (uma = o dia; duas = de/até)
    datas = re.findall(r'\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b', resto)
    ds = [d for d in (_data_br(t, hoje) for t in datas) if d]
    if ds:
        f['data_ini'], f['data_fim'] = min(ds), max(ds)
        rot = ds[0].strftime('%d/%m/%Y') if len(ds) == 1 else '%s a %s' % (
            min(ds).strftime('%d/%m'), max(ds).strftime('%d/%m/%Y'))
        for t in datas:
            tirar(t)
        chips.append({'rotulo': 'Período: ' + rot, 'tira': ' '.join(datas)})
    # 4) empresa por número (#92) ou CNPJ/CPF
    for m in re.findall(r'#\s?(\d{1,5})\b', resto):
        f['empresa_nums'].append(m)
        tirar('#' + m)
    for m in re.findall(r'\b[\d.\-/]{11,18}\b', resto):
        dig = re.sub(r'\D', '', m)
        if len(dig) in (11, 14):
            emp = execute_query("SELECT id, numero_cliente, nome_razao_social FROM clientes "
                                "WHERE REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'-',''),'/','') = %s",
                                (dig,), fetch=True) or []
            for e in emp:
                f['empresa_ids'].append(e['id'])
                chips.append({'rotulo': 'Empresa: %s' % e['nome_razao_social'], 'tira': m})
            if not emp:
                f['textos'].append(dig)
                chips.append({'rotulo': 'Documento: %s (sem cadastro)' % m, 'tira': m})
            tirar(m)
    if f['empresa_nums']:
        emp = execute_query("SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE numero_cliente IN (%s)"
                            % ','.join(['%s'] * len(f['empresa_nums'])), tuple(f['empresa_nums']), fetch=True) or []
        for e in emp:
            f['empresa_ids'].append(e['id'])
        nomes = {str(e['numero_cliente']): e['nome_razao_social'] for e in emp}
        for n in f['empresa_nums']:
            chips.append({'rotulo': 'Empresa: #%s %s' % (n, nomes.get(n, '(não achei)')), 'tira': '#' + n})
    # 5) período por palavra
    for w, chave in _PALAVRAS_PERIODO.items():
        if ' %s ' % w in resto:
            f['data_ini'], f['data_fim'] = _periodo(chave, hoje)
            tirar(w)
            chips.append({'rotulo': 'Período: ' + {'mes': 'este mês', 'semana': 'esta semana'}.get(w, w), 'tira': w})
    # 6) pessoas: palavra = primeiro nome (ou nome inteiro)
    for nome in pessoas_conhecidas():
        n = sem_acento(nome)
        primeiro = n.split(' ')[0] if n else ''
        if ' %s ' % n in resto:
            f['pessoas'].append(nome); continue
        if primeiro and len(primeiro) >= 3 and ' %s ' % primeiro in resto:
            f['pessoas'].append(nome)
    if f['pessoas']:
        usados = set()
        for nome in f['pessoas']:
            n = sem_acento(nome)
            t = n if ' %s ' % n in resto else n.split(' ')[0]
            usados.add(t)
        for t in usados:
            tirar(t)
            quem = [p for p in f['pessoas'] if sem_acento(p).startswith(t)]
            rot = quem[0] if len(quem) == 1 else '%s (%d nomes)' % (quem[0].split(' (')[0], len(quem))
            chips.append({'rotulo': 'Pessoa: ' + rot, 'tira': t})
    # 7) atalhos de uma palavra
    for palavras, tipo, valor, rot in _ATALHOS:
        for w in palavras:
            if ' %s ' % w in resto:
                tirar(w)
                if tipo == 'tipo':
                    f['tipo'] = valor
                elif tipo == 'acao':
                    f['acoes'].append(valor)
                elif tipo == 'modulo':
                    f['modulos'].append(valor)
                elif tipo == 'campo':
                    f['campos'].append(valor)
                else:
                    f[tipo] = True
                chips.append({'rotulo': rot, 'tira': w})
                break
    # 8) sobra: nome de empresa (todas as palavras) ou texto livre
    sobra = [w for w in resto.split() if w not in _VAZIAS and len(w) >= 2]
    if sobra:
        cond = ' AND '.join(["(nome_razao_social LIKE %s OR COALESCE(nome_fantasia,'') LIKE %s)"] * len(sobra))
        par = []
        for w in sobra:
            par += ['%' + w + '%', '%' + w + '%']
        emp = execute_query("SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE " + cond +
                            " ORDER BY numero_cliente LIMIT 60", tuple(par), fetch=True) or []
        frase = ' '.join(sobra)
        if emp:
            f['empresa_ids'] += [e['id'] for e in emp]
            f['empresa_nums'] += [str(e['numero_cliente']) for e in emp if e.get('numero_cliente')]
            rot = emp[0]['nome_razao_social'] if len(emp) == 1 else '"%s" (%d empresas)' % (frase, len(emp))
            chips.append({'rotulo': 'Empresa: ' + rot, 'tira': frase})
        else:
            f['textos'].append(frase)
            chips.append({'rotulo': 'Contém: "%s"' % frase, 'tira': frase})
    return f, chips


def tirar_do_texto(q, trecho):
    """O texto da busca sem o trecho de um chip (para o X do chip)."""
    alvo = sem_acento(trecho).split()
    palavras = (q or '').split()
    norm = [sem_acento(p) for p in palavras]
    n = len(alvo)
    for i in range(len(norm) - n + 1):
        if norm[i:i + n] == alvo or ('#' + ' '.join(norm[i:i + n]).lstrip('#')) == ('#' + ' '.join(alvo).lstrip('#')):
            return ' '.join(palavras[:i] + palavras[i + n:])
    return q


# ───────────────────────────────────────────────────────────── WHERE
_FORA_SQL = ("(HOUR(l.data_hora) < %d OR HOUR(l.data_hora) >= %d OR DAYOFWEEK(l.data_hora) IN (1,7))"
             % (EXPEDIENTE_INI, EXPEDIENTE_FIM))
_LOTE_SQL = ("(COALESCE(l.usuario_nome,'') LIKE '%%Claude%%' OR COALESCE(l.usuario_nome,'') LIKE '%%pedido no chat%%' "
             "OR COALESCE(l.usuario_nome,'') LIKE 'ROTEADOR%%' OR l.acao LIKE '%%\\_migracao' OR l.acao LIKE '%%\\_correcao')")
_ACOES_SQL = {
    'excluiu': "(l.acao LIKE 'escrita.excluiu%%' OR l.acao LIKE 'escrita.apagou%%' OR l.acao LIKE 'escrita.cancelou%%')",
    'criou': "(l.acao LIKE 'escrita.criou%%' OR l.acao LIKE 'escrita.adicionou%%' OR l.acao LIKE 'escrita.cadastrou%%')",
    'recusada': "l.acao IN ('escrita.entrada_recusada','escrita.codigo_recusado')",
    'exportou': "(l.acao LIKE 'leitura.exportou%%' OR l.acao LIKE 'leitura.baixou%%')",
}


def montar_where(f, legado=None):
    """(cond, params). `legado` = filtros antigos da URL (pessoa/modulo/tipo/
    empresa/ocultar_teste) — continuam valendo para links já salvos."""
    cond = ["l.data_hora >= %s", "l.data_hora <= %s"]
    params = [f['data_ini'].isoformat() + ' 00:00:00', f['data_fim'].isoformat() + ' 23:59:59']
    legado = legado or {}
    if legado.get('ocultar_teste'):
        cond.append("COALESCE(l.usuario_nome,'') NOT LIKE 'ZZ TESTE%%'")
    pessoas = list(f['pessoas']) + ([legado['pessoa']] if legado.get('pessoa') else [])
    if pessoas:
        cond.append("l.usuario_nome IN (%s)" % ','.join(['%s'] * len(pessoas))); params += pessoas
    modulos = list(f['modulos']) + ([legado['modulo']] if legado.get('modulo') else [])
    if modulos:
        cond.append("l.modulo IN (%s)" % ','.join(['%s'] * len(modulos))); params += modulos
    tipo = f['tipo'] or legado.get('tipo')
    if tipo == 'escrita':
        cond.append("l.acao LIKE 'escrita.%%'")
    elif tipo == 'leitura':
        cond.append("l.acao LIKE 'leitura.%%'")
    if f['acoes']:
        cond.append('(' + ' OR '.join(_ACOES_SQL[a] for a in f['acoes']) + ')')
    if f['campos']:
        ors = []
        for c in f['campos']:
            for k in _CAMPOS_JSON[c]:
                ors.append("JSON_CONTAINS_PATH(l.dados_novos,'one','$.%s')" % k)
                ors.append("JSON_CONTAINS_PATH(l.dados_anteriores,'one','$.%s')" % k)
        if 'socio' in f['campos']:
            ors.append("l.tabela_afetada = 'socios_clientes'")
        if 'endereco' in f['campos']:
            ors.append("l.tabela_afetada = 'enderecos_clientes'")
        if 'certificado' in f['campos']:
            ors.append("l.tabela_afetada = 'dfe_certificados'")
        cond.append('(' + ' OR '.join(ors) + ')')
    if f['fora']:
        cond.append(_FORA_SQL)
    if f['lote']:
        cond.append(_LOTE_SQL)
    elif f['sem_lote']:
        cond.append('NOT ' + _LOTE_SQL)
    for t in f['textos']:
        cond.append("(CAST(l.dados_novos AS CHAR) LIKE %s OR CAST(l.dados_anteriores AS CHAR) LIKE %s "
                    "OR COALESCE(l.usuario_nome,'') LIKE %s)")
        params += ['%' + t + '%'] * 3
    ids = list(f['empresa_ids'])
    nums = list(f['empresa_nums'])
    if legado.get('empresa'):
        emp = execute_query("SELECT id, numero_cliente FROM clientes WHERE numero_cliente = %s "
                            "OR nome_razao_social LIKE %s", (legado['empresa'], '%' + legado['empresa'] + '%'),
                            fetch=True) or []
        ids += [e['id'] for e in emp]
        nums += [str(e['numero_cliente']) for e in emp if e.get('numero_cliente')]
        if not emp:
            cond.append('1=0')
    if ids or nums:
        ors, p2 = [], []
        if ids:
            ph = ','.join(['%s'] * len(ids))
            ors.append("(l.tabela_afetada IN ('clientes','dfe_certificados') AND l.registro_id IN (%s))" % ph)
            p2 += ids
            for path in ('$.cliente_id', '$.filtros.cliente_id'):
                ors.append("CAST(JSON_UNQUOTE(JSON_EXTRACT(l.dados_novos,'%s')) AS CHAR) IN (%s)" % (path, ph))
                p2 += [str(i) for i in ids]
        if nums:
            ph = ','.join(['%s'] * len(nums))
            for path in ('$.cliente_numero', '$.filtros.cliente_numero', '$.numero_cliente'):
                ors.append("JSON_UNQUOTE(JSON_EXTRACT(l.dados_novos,'%s')) IN (%s)" % (path, ph))
                p2 += nums
        cond.append('(' + ' OR '.join(ors) + ')')
        params += p2
    elif f['empresa_nums']:
        cond.append('1=0')                       # número digitado não existe
    return ' AND '.join(cond), params


SELECT_ATOS = ("SELECT l.id, l.data_hora, l.usuario_id, l.usuario_nome, l.acao, l.modulo, l.tabela_afetada, "
               "l.registro_id, l.dados_anteriores, l.dados_novos, l.ip_address FROM logs_sistema l WHERE ")


def resolver(rows):
    """{usuario_id: nome} p/ linhas antigas + {cliente_id: '#num nome'} da página."""
    nomes = {}
    faltam = {r['usuario_id'] for r in rows if not r.get('usuario_nome') and r.get('usuario_id')}
    if faltam:
        for u in execute_query("SELECT id, nome FROM usuarios WHERE id IN (%s)" % ','.join(['%s'] * len(faltam)),
                               tuple(faltam), fetch=True) or []:
            nomes[u['id']] = u['nome']
    cids = coletar_cliente_ids(rows)
    emp = {}
    if cids:
        for c in execute_query("SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE id IN (%s)"
                               % ','.join(['%s'] * len(cids)), tuple(cids), fetch=True) or []:
            num, nome = c.get('numero_cliente'), (c.get('nome_razao_social') or '').strip()
            emp[c['id']] = ('#%s %s' % (num, nome)).strip() if num else nome
    return nomes, emp


def preparar(rows):
    """Linhas cruas -> atos prontos (hist_preparar + data/hora/lote)."""
    nomes, emp = resolver(rows)
    atos = []
    for r in rows:
        it = hist_preparar(r, nomes, emp)
        dh = r.get('data_hora')
        it['dt'] = dh
        it['dia'] = dh.date() if dh else None
        it['hora'] = dh.strftime('%H:%M') if dh else ''
        it['lote'] = eh_lote(r.get('usuario_nome'), r.get('acao'))
        it['escrita'] = (r.get('acao') or '').startswith('escrita.')
        it['ip'] = it.get('ip') or r.get('ip_address')
        it['id'] = r.get('id')
        it['modulo_rot'] = MODULO_LABEL.get(r.get('modulo'), r.get('modulo') or '')
        it['fora'] = bool(dh) and (dh.hour < EXPEDIENTE_INI or dh.hour >= EXPEDIENTE_FIM or dh.weekday() >= 5)
        atos.append(it)
    return atos


# ───────────────────────────────────────────────────────────── sinais de atenção
_CONTATO_LABELS = {'E-mail', 'Celular', 'Telefone'}
_ACESSOS = {'escrita.alterou_usuario', 'escrita.criou_usuario', 'escrita.alterou_perfil',
            'escrita.criou_perfil', 'escrita.excluiu_perfil', 'escrita.gerou_link_senha',
            'escrita.liberou_sem_codigo', 'escrita.alterou_acesso_reservado', 'usuario_perfil_adicionado'}


def ips_conhecidos(antes_de):
    """{nome: set(ips)} das entradas dos 60 dias ANTES do período (para 'IP novo')."""
    rows = execute_query(
        "SELECT usuario_nome, JSON_UNQUOTE(JSON_EXTRACT(dados_novos,'$.ip')) AS ip FROM logs_sistema "
        "WHERE acao = 'escrita.entrou' AND data_hora < %s AND data_hora >= %s - INTERVAL 60 DAY",
        (antes_de.isoformat() + ' 00:00:00', antes_de.isoformat() + ' 00:00:00'), fetch=True) or []
    out = {}
    for r in rows:
        if r.get('ip'):
            out.setdefault(r['usuario_nome'], set()).add(r['ip'])
    return out


def sinais(atos, ips_antes=None, ips_dia=None):
    """Sinais de atenção de um conjunto de atos (o dia de uma pessoa).
    Cada sinal: {'nivel': 'bad'|'warn'|'info', 'texto', 'hora'}."""
    out = []
    humano = [a for a in atos if not a['lote']]
    rec = [a for a in atos if a['acao'] == 'escrita.entrada_recusada']
    if rec:
        out.append({'nivel': 'bad' if len(rec) >= 3 else 'warn',
                    'texto': '%d senha%s errada%s' % (len(rec), 's' if len(rec) > 1 else '', 's' if len(rec) > 1 else ''),
                    'hora': rec[-1]['hora']})
    cod = [a for a in atos if a['acao'] == 'escrita.codigo_recusado']
    if cod:
        out.append({'nivel': 'bad' if len(cod) >= 3 else 'warn',
                    'texto': '%d código%s errado%s' % (len(cod), 's' if len(cod) > 1 else '', 's' if len(cod) > 1 else ''),
                    'hora': cod[-1]['hora']})
    exc = [a for a in humano if a['acao'].startswith(('escrita.excluiu', 'escrita.apagou', 'escrita.cancelou'))]
    if exc:
        out.append({'nivel': 'warn', 'texto': 'excluiu/cancelou %d' % len(exc), 'hora': exc[-1]['hora']})
    cont = [a for a in humano if a['escrita'] and a['acao'] != 'escrita.criou_cliente'
            and any(c['label'] in _CONTATO_LABELS and c.get('antes') not in (None, '(vazio)')
                    and c['tipo'] in ('alterado', 'removido') for c in a['campos'])]
    if cont:
        out.append({'nivel': 'warn', 'texto': 'trocou contato de cliente (%d)' % len(cont), 'hora': cont[-1]['hora']})
    exp = [a for a in humano if a['acao'].startswith(('leitura.exportou', 'leitura.baixou'))]
    if len(exp) >= 20:
        out.append({'nivel': 'warn', 'texto': 'exportou/baixou %d arquivos' % len(exp), 'hora': exp[-1]['hora']})
    ace = [a for a in humano if a['acao'] in _ACESSOS]
    if ace:
        out.append({'nivel': 'info', 'texto': 'mexeu em acessos (%d)' % len(ace), 'hora': ace[-1]['hora']})
    if ips_dia:
        novos = [ip for ip in ips_dia if ips_antes is not None and ip not in ips_antes]
        if novos and ips_antes:
            out.append({'nivel': 'warn', 'texto': 'entrou de IP novo', 'hora': ''})
    fora = [a for a in humano if a['fora'] and (a['modulo'] != 'acesso' or a['acao'] == 'escrita.entrou')]
    if fora:
        out.append({'nivel': 'info', 'texto': 'fora do expediente (%s)' % fora[-1]['hora'], 'hora': fora[-1]['hora']})
    return out


# ───────────────────────────────────────────────────────────── agrupamentos
def _iniciais(nome):
    p = [x for x in re.split(r'\s+', (nome or '').split(' (')[0]) if x and x[0].isalpha()]
    if not p:
        return '?'
    return (p[0][0] + (p[-1][0] if len(p) > 1 else '')).upper()


def _cor(nome):
    return sum(ord(c) for c in (nome or '')) % 8


def por_dia(atos, ips_antes=None):
    """[{dia, rotulo, n, pessoas, cartoes:[...]}], dia mais recente primeiro;
    dentro do dia, quem trabalhou por último primeiro (como o posto NH)."""
    grupos = OrderedDict()
    for a in atos:                                   # atos vêm do mais novo ao mais velho
        # o lote (script) da mesma pessoa no mesmo dia vira OUTRO cartão: não
        # mistura 1.600 atos de migração com o trabalho de balcão dela
        grupos.setdefault(a['dia'], OrderedDict()).setdefault((a['autor'], a['lote']), []).append(a)
    dias = []
    for d, pessoas in grupos.items():
        cartoes = []
        for (nome, _lote), lst in pessoas.items():
            cartoes.append(cartao_pessoa_dia(nome, d, lst, (ips_antes or {}).get(nome)))
        dias.append({'dia': d, 'rotulo': dia_rotulo(d), 'n': sum(c['n'] for c in cartoes),
                     'pessoas': len(cartoes), 'cartoes': cartoes,
                     'atencao': sum(1 for c in cartoes if c['sinais'])})
    return dias


def cartao_pessoa_dia(nome, d, lst, ips_antes=None):
    lst_cron = list(reversed(lst))                   # do primeiro ao último
    esc = [a for a in lst if a['escrita'] and a['modulo'] != 'acesso']
    lei = [a for a in lst if not a['escrita']]
    entradas = [a for a in lst_cron if a['acao'] == 'escrita.entrou']
    ips = OrderedDict((a['ip'], 1) for a in entradas if a.get('ip'))
    emp = Counter(a['empresa'] for a in lst if a.get('empresa'))
    mods = Counter(a['modulo_rot'] for a in lst if a.get('modulo_rot'))
    lote = all(a['lote'] for a in lst)
    # as últimas coisas que a pessoa MUDOU (o que mais interessa a quem audita)
    ultimas = []
    for a in esc[:3]:
        ultimas.append({'hora': a['hora'], 'verbo': a['verbo'], 'empresa': a.get('empresa') or ''})
    if not ultimas:
        for a in lei[:2]:
            ultimas.append({'hora': a['hora'], 'verbo': a['verbo'], 'empresa': a.get('empresa') or ''})
    return {
        'nome': nome, 'nome_curto': 'Login não reconhecido' if nome == 'desconhecido' else nome.split(' (')[0], 'obs': nome[len(nome.split(' (')[0]):].strip(' ()'),
        'iniciais': _iniciais(nome), 'cor': _cor(nome), 'dia': d, 'dia_iso': d.isoformat() if d else '',
        'ini': lst_cron[0]['hora'], 'fim': lst_cron[-1]['hora'], 'n': len(lst),
        'escritas': len(esc), 'leituras': len(lei), 'empresas': len(emp),
        'top_empresas': [e for e, _ in emp.most_common(2)], 'mais_empresas': max(0, len(emp) - 2),
        'modulos': [m for m, _ in mods.most_common(3) if m != 'Acesso'],
        'entradas': len(entradas), 'ips': list(ips), 'lote': lote,
        'ultimas': ultimas,
        'sinais': [] if lote else sinais(lst_cron, ips_antes, list(ips)),
    }


def por_empresa(atos, limite=60):
    grupos = {}
    for a in atos:
        if not a.get('empresa'):
            continue
        g = grupos.setdefault(a['empresa'], {'empresa': a['empresa'], 'n': 0, 'escritas': 0, 'leituras': 0,
                                             'pessoas': Counter(), 'ultimo': a, 'lote': 0})
        g['n'] += 1
        g['escritas' if a['escrita'] else 'leituras'] += 1
        g['pessoas'][a['autor'].split(' (')[0]] += 1
        g['lote'] += 1 if a['lote'] else 0
    lst = sorted(grupos.values(), key=lambda g: (-g['n'], g['empresa']))
    for g in lst:
        g['pessoas'] = g['pessoas'].most_common(4)
        m = re.match(r'#(\d+)', g['empresa'])
        g['num'] = m.group(1) if m else ''
    return lst[:limite], max(0, len(lst) - limite)


def resumo_pessoas(atos):
    """Pessoas do período (pílulas que filtram)."""
    c = Counter(a['autor'] for a in atos)
    tot = sum(c.values()) or 1
    return [{'nome': n, 'n': k, 'pct': round(100 * k / tot), 'cor': _cor(n)} for n, k in c.most_common()]


def blocos_do_dia(atos_cron):
    """Linha do tempo de UM dia: atos seguidos na mesma empresa viram um bloco
    ("06:07–06:08 · #92 SERVICOS... · 3 atos")."""
    def _assin(a):
        return (a['verbo'], a.get('busca'), a.get('detalhe'),
                tuple((c['label'], c.get('antes'), c.get('depois')) for c in a.get('campos') or []))

    blocos = []
    for a in atos_cron:
        a = dict(a, vezes=1, hora_fim=a['hora'])
        chave = a.get('empresa') or ('__acesso' if a['modulo'] == 'acesso' else '__geral')
        if blocos and blocos[-1]['chave'] == chave:
            ult = blocos[-1]['atos'][-1]
            if _assin(ult) == _assin(a):         # o mesmo ato repetido vira "×N"
                ult['vezes'] += 1
                ult['hora_fim'] = a['hora']
            else:
                blocos[-1]['atos'].append(a)
            blocos[-1]['fim'] = a['hora']
            blocos[-1]['n'] += 1
        else:
            blocos.append({'chave': chave, 'empresa': a.get('empresa'), 'acesso': a['modulo'] == 'acesso',
                           'ini': a['hora'], 'fim': a['hora'], 'atos': [a], 'n': 1})
    return blocos
