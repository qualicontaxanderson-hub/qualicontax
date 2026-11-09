# -*- coding: utf-8 -*-
"""Histórico da ficha em CARTÕES (09/11/2026).

Anderson (09/11/2026): "O histórico em CARDs tb... assim como está fica cansativo".
Antes a ficha mostrava o log cru ("Cobranca: PENDENTE → VIA_GRUPO", dicionários
Python inteiros, "Reservado: True"). Aqui cada linha de logs_sistema vira um
cartão: ícone e cor pelo assunto, título em português, quem e a hora, e as
mudanças como "antes → depois" com o valor do jeito que a tela mostra.

Dias agrupados (Hoje, Ontem, 05/11/2026 · quarta-feira). Os registros de
migração (Acessórias/Conexa) do mesmo dia e da mesma pessoa viram UM cartão,
com os detalhes recolhidos.

Só a ficha usa este módulo. A tela geral de Auditoria segue em auditoria_fmt.
"""
import ast
import json
import re
from datetime import date, timedelta

from markupsafe import Markup, escape

from utils.auditoria_fmt import _HIST_LABELS, _HIST_VERBO
from utils.atividade import CAMPOS_SENSIVEIS
from utils.texto import nome_proprio

# ---------------------------------------------------------------------------
# Rótulos e valores
# ---------------------------------------------------------------------------
_ROTULO = {
    'cobranca': 'Cobrança dos honorários', 'nire': 'NIRE', 'nire_data_registro': 'Data do registro (NIRE)',
    'ramos': 'Ramo de atividade', 'grupos': 'Grupo de empresas', 'regime': 'Regime tributário',
    'regime_tributario': 'Regime tributário', 'porte_empresa': 'Porte',
    'contabilidade_anterior': 'Contabilidade anterior', 'departamentos': 'Departamentos que recebe',
    'cargo': 'Cargo', 'principal': 'Contato principal', 'email': 'E-mail',
    'manifesta_ciencia': 'Ciência automática das notas', 'modo_automatico': 'Busca automática de notas',
    'emite_nf': 'Emite nota', 'avulso': 'Avulso', 'aberta_pela_casa': 'Aberta pelo escritório',
    'natureza_juridica': 'Natureza jurídica', 'natureza_codigo': 'Código da natureza',
    'endereco': 'Endereço', 'pasta': 'Pasta no Dropbox', 'numero_cliente': 'Número do cadastro',
    'receita_base_em': 'Base da Receita de', 'conexa_id': 'Código no Conexa',
    'situacao': 'Situação', 'telefone': 'Telefone', 'celular': 'Celular',
    'nome_fantasia': 'Nome fantasia', 'inscricao_municipal': 'Inscrição municipal',
    'inscricao_estadual': 'Inscrição estadual', 'avisos': 'Avisos', 'origem': 'Origem',
    'validade': 'Válido até', 'tipo_doc': 'Documento', 'procuracao': 'Por procuração',
}

# Chaves que nunca viram "mudança" no cartão (contexto, técnicas ou ruído).
_META = {
    'cliente_id', 'cliente_numero', 'cliente_nome', 'grupo_id', 'grupo_nome', 'socio_nome',
    'criado_por', 'criado_em', 'alterado_por', 'alterado_em', 'usuario_id', 'acao', 'fonte',
    'motivo', 'dropbox', 'caminhos_atualizados', 'caminhos_reapontados', 'dropbox_path',
    'autorizado_por', 'campos', 'empresa', 'numero', 'filtros', 'ok', 'job_id', 'versao',
    'contador_id', 'id', 'contato_id',
}

_SIM_NAO = {'procuracao', 'responsavel', 'principal', 'avulso', 'manifesta_ciencia', 'modo_automatico',
            'aberta_pela_casa', 'emite_nf', 'reservado'}
_LISTAS = {'departamentos', 'ramos', 'grupos'}
_DINHEIRO = {'capital_valor', 'pro_labore', 'valor_mensal'}
_NOMES = {'nome', 'contato', 'socio_nome', 'contabilidade_anterior', 'nome_razao_social', 'razao_social',
          'logradouro', 'bairro', 'cidade', 'grupos', 'nome_mae', 'empresa', 'endereco', 'nome_fantasia'}

_CODIGO = {
    'PENDENTE': 'Pendente', 'VIA_GRUPO': 'Via grupo', 'PROPRIA': 'Própria',
    'SIMPLES_NACIONAL': 'Simples Nacional', 'LUCRO_PRESUMIDO': 'Lucro Presumido', 'LUCRO_REAL': 'Lucro Real',
    'MEI': 'MEI', 'ME': 'ME', 'EPP': 'EPP', 'MEDIO': 'Médio', 'GRANDE': 'Grande', 'DEMAIS': 'Demais',
    'ATIVO': 'Ativa', 'INATIVO': 'Inativa', 'COMERCIAL': 'Comercial', 'RESIDENCIAL': 'Residencial',
    'CORRESPONDENCIA': 'Correspondência', 'COBRANCA': 'Cobrança', 'PF': 'Pessoa física', 'PJ': 'Pessoa jurídica',
    'CASA': 'Aberta pelo escritório', 'CANCELADO': 'Cancelada', 'SUSPENSO': 'Suspensa', 'IMUNE': 'Imune', 'ISENTA': 'Isenta',
}

_SEMANA = ('segunda-feira', 'terça-feira', 'quarta-feira', 'quinta-feira', 'sexta-feira', 'sábado', 'domingo')
_RE_DATA = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')
_RE_CODIGO = re.compile(r'^[A-Z]+(?:_[A-Z]+)+$')


def rotulo(k):
    if k in _ROTULO:
        return _ROTULO[k]
    if k in _HIST_LABELS:
        return _HIST_LABELS[k]
    if k and k[:1].isupper():          # chaves que já são nome (departamentos: "Fiscal", "Contábil")
        return k
    return (k or '').replace('_', ' ').capitalize()


def _vazio(v):
    return v is None or (isinstance(v, str) and v.strip() in ('', 'None', 'null', '[]', '{}')) \
        or (isinstance(v, (list, dict, tuple)) and not v)


def _talvez_estrutura(v):
    """'{'a': 1}' / '[...]' gravados como texto -> objeto. O resto volta como veio."""
    if isinstance(v, str) and v[:1] in '[{' and v[-1:] in ']}':
        for f in (json.loads, ast.literal_eval):
            try:
                return f(v)
            except Exception:
                pass
    return v


def valor(k, v):
    """Valor do jeito que a tela mostra, ou None quando vazio."""
    v = _talvez_estrutura(v)
    if _vazio(v):
        return None
    if k in _SIM_NAO or isinstance(v, bool) or (isinstance(v, str) and v in ('True', 'False')):
        if isinstance(v, str):
            return 'Sim' if v == 'True' else 'Não' if v == 'False' else v
        return 'Sim' if v else 'Não'
    if isinstance(v, (list, tuple)):
        itens = [valor(k, x) for x in v]
        return ', '.join(x for x in itens if x) or None
    if isinstance(v, dict):
        partes = ['%s: %s' % (rotulo(a), valor(a, b) or 'vazio') for a, b in v.items()]
        return ' · '.join(partes) or None
    if k in _DINHEIRO:
        try:
            return 'R$ ' + f'{float(v):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        except (TypeError, ValueError):
            return str(v)
    if k == 'percentual_participacao':
        try:
            return ('%g' % float(v)).replace('.', ',') + '%'
        except (TypeError, ValueError):
            return str(v)
    if k in ('estado_civil', 'regime_bens', 'nivel_govbr'):
        from models.pessoa_dados import PessoaDados
        return PessoaDados.rotulo(k, v)
    s = str(v).strip()
    m = _RE_DATA.match(s)
    if m and len(s) in (10, 19):
        return '%s/%s/%s' % (m.group(3), m.group(2), m.group(1))
    if s in _CODIGO:
        return _CODIGO[s]
    if _RE_CODIGO.match(s):
        return s.replace('_', ' ').capitalize()
    if k in _NOMES:
        return nome_proprio(s)
    return s


def _mud(k, antes, depois, rot=None):
    """Uma mudança 'antes → depois' (None quando nada mudou de fato)."""
    if k in CAMPOS_SENSIVEIS or str(antes) == 'alterado' or str(depois) == 'alterado':
        return {'rotulo': rot or rotulo(k), 'sensivel': True}
    a, d = valor(k, antes), valor(k, depois)
    if k in _LISTAS:      # "Contábil,Fiscal" e "Fiscal, Contábil" são a mesma lista
        a = ', '.join(x.strip() for x in a.split(',') if x.strip()) if a else a
        d = ', '.join(x.strip() for x in d.split(',') if x.strip()) if d else d
        if a and d and sorted(a.split(', ')) == sorted(d.split(', ')):
            return None
    if a == d:
        return None
    return {'rotulo': rot or rotulo(k), 'antes': a, 'depois': d}


def _diff(ant, nov, fora=()):
    out = []
    for k in sorted((set(ant) | set(nov)) - _META - set(fora), key=lambda x: rotulo(x)):
        m = _mud(k, ant.get(k), nov.get(k))
        if m:
            out.append(m)
    return out


def _dados(d, fora=()):
    """Dados de uma criação: lista (rótulo, valor) só do que está preenchido."""
    out = []
    for k, v in d.items():
        if k in _META or k in fora or k in CAMPOS_SENSIVEIS:
            continue
        t = valor(k, v)
        if t:
            out.append((rotulo(k), t))
    return out


def _b(txt):
    return Markup('<b>%s</b>') % txt


def _titulo(*partes):
    return Markup('').join(p if isinstance(p, Markup) else escape(p) for p in partes)


# ---------------------------------------------------------------------------
# Um registro -> um cartão
# ---------------------------------------------------------------------------
# acao -> (categoria, ícone, cor)
_ASSUNTO = [
    ('contato', ('contatos', 'fa-address-book', 'azul')),
    ('responsav', ('responsaveis', 'fa-people-group', 'verde')),
    ('socio', ('socios', 'fa-users', 'roxo')),
    ('endereco', ('enderecos', 'fa-location-dot', 'ambar')),
    ('certificado', ('certificado', 'fa-certificate', 'verde')),
    ('ciencia', ('certificado', 'fa-cloud-arrow-down', 'azul')),
    ('captura', ('certificado', 'fa-cloud-arrow-down', 'azul')),
    ('contador', ('cadastro', 'fa-user-check', 'roxo')),
    ('acesso', ('acesso', 'fa-lock', 'cinza')),
    ('cobranca', ('cadastro', 'fa-coins', 'rosa')),
]


def _assunto(acao):
    for chave, a in _ASSUNTO:
        if chave in acao:
            return a
    return ('cadastro', 'fa-pen', 'azul')


def _ini(nome):
    p = [x for x in re.split(r'\s+', nome or '') if x and x[0].isalpha()]
    if not p:
        return '?'
    return (p[0][0] + (p[-1][0] if len(p) > 1 else '')).upper()


def _autor(row, nomes):
    a = row.get('usuario_nome') or nomes.get(row.get('usuario_id')) or 'usuário removido'
    extra = None
    m = re.match(r'^(.*?)\s*\((.+)\)\s*$', a)
    if m:
        a, extra = m.group(1), m.group(2)
    return a, extra


def _cartao(row, nomes, ref):
    """Linha de logs_sistema -> dict do cartão. `ref` = nomes resolvidos em lote
    (contadores, contatos) para títulos com o nome de quem foi mexido."""
    def js(v):
        try:
            d = json.loads(v) if v else {}
        except Exception:
            d = {}
        return d if isinstance(d, dict) else {}
    ant, nov = js(row.get('dados_anteriores')), js(row.get('dados_novos'))
    acao = row.get('acao') or ''
    cat, ico, cor = _assunto(acao)
    autor, autor_extra = _autor(row, nomes)
    dh = row.get('data_hora')
    ev = {'cat': cat, 'ico': ico, 'cor': cor, 'autor': autor, 'autor_extra': autor_extra, 'ini': _ini(autor),
          'dia': dh.date() if hasattr(dh, 'date') else None, 'hora': dh.strftime('%H:%M') if dh else '',
          'mudancas': [], 'dados': [], 'tags': [], 'nota': None, 'acao': acao,
          'migracao': ('_migracao' in acao or 'acessorias' in acao)}
    nome_pessoa = nome_proprio(str(nov.get('nome') or nov.get('contato') or nov.get('socio_nome')
                                   or ant.get('nome') or ant.get('socio_nome') or ''))

    # --- casos com cara própria ------------------------------------------------
    if acao == 'escrita.alterou_acesso_reservado':
        res = nov.get('reservado') in (True, 'True', 1, '1')
        ev['titulo'] = _titulo('Tornou a empresa ', _b('reservada'), ' (só administradores veem)') if res \
            else _titulo(_b('Liberou'), ' a empresa para toda a equipe')
        ev['mudancas'] = [{'rotulo': 'Acesso', 'antes': valor('a', nov.get('antes')) or None,
                           'depois': 'reservada' if res else 'liberada'}]
        exc = _talvez_estrutura(nov.get('excecoes'))
        if isinstance(exc, (list, tuple)) and exc:
            ev['tags'] = [nome_proprio(str(x.get('nome') if isinstance(x, dict) else x)) for x in exc]
        elif res:
            ev['nota'] = 'sem exceções'
    elif acao in ('escrita.responsavel_acessorias_migracao', 'escrita.alterou_responsaveis'):
        ev['titulo'] = _titulo('Definiu os ', _b('responsáveis por departamento'))
        resp = _talvez_estrutura(nov.get('responsaveis'))
        if isinstance(resp, dict):
            for dep, x in resp.items():
                if isinstance(x, dict):
                    m = _mud('nome', x.get('antes'), x.get('depois'), rot=dep)
                else:
                    m = _mud('nome', None, x, rot=dep)
                if m:
                    ev['mudancas'].append(m)
        else:
            ev['mudancas'] = _diff(ant, nov)
    elif 'contato' in acao:
        if acao.startswith('escrita.excluiu'):
            exc = nov.get('excluido') if isinstance(nov.get('excluido'), dict) else ant
            n = nome_proprio(str(exc.get('contato') or exc.get('nome') or ''))
            ev['titulo'] = _titulo('Excluiu o contato ', _b(n or '—'))
            ev['dados'] = _dados(exc, fora=('contato', 'nome'))
            ev['cor'] = 'verm'
            ev['nota'] = nov.get('motivo')
        elif acao == 'escrita.contato_acessorias_migracao' and nov.get('acao') == 'completado':
            ev['titulo'] = _titulo('Completou o contato ', _b(nome_pessoa or '—'))
            ev['mudancas'] = [m for m in [_mud('departamentos', nov.get('departamentos_antes'),
                                                nov.get('departamentos'))] if m]
        elif acao.startswith('escrita.alterou'):
            n = nome_pessoa or ref['contatos'].get(row.get('registro_id')) or ''
            ev['titulo'] = _titulo('Alterou o contato ', _b(n or '—'))
            ev['mudancas'] = _diff(ant, nov)
        else:
            n = nome_pessoa or ref['contatos'].get(row.get('registro_id')) or ''
            ev['titulo'] = _titulo('Adicionou o contato ', _b(n or '—'))
            ev['dados'] = _dados(nov, fora=('contato', 'nome', 'departamentos_antes'))
    elif acao == 'escrita.vinculou_contador':
        n = ref['contadores'].get(str(nov.get('contador_id'))) or 'contador'
        ev['titulo'] = _titulo('Vinculou o contador ', _b(n))
    elif acao == 'escrita.vinculou_certificado':
        ev['titulo'] = _titulo('Vinculou o ', _b('certificado digital'))
        val = valor('validade', nov.get('validade'))
        if val:
            ev['tags'] = ['válido até ' + val]
        ev['dados'] = _dados(nov, fora=('validade', 'cnpj', 'tipo_doc'))
        if nov.get('cnpj'):
            ev['dados'].insert(0, (nov.get('tipo_doc') or 'Documento', str(nov.get('cnpj'))))
    elif acao == 'escrita.email_cnpj_migracao':
        ev['titulo'] = _titulo('Trouxe o ', _b('e-mail do CNPJ'), ' da Receita')
        ev['ico'], ev['cor'] = 'fa-envelope', 'verde'
        ev['mudancas'] = [m for m in [_mud('email', nov.get('email_antes'), nov.get('email'), rot='E-mail')] if m]
        ev['nota'] = nov.get('fonte')
    elif acao == 'escrita.limpou_inicio_contrato_migracao':
        ev['titulo'] = _titulo('Limpou o ', _b('início do contrato'))
        ev['ico'], ev['cor'] = 'fa-eraser', 'ambar'
        ev['mudancas'] = [m for m in [_mud('data_inicio_contrato', nov.get('data_inicio_contrato_antes'),
                                           nov.get('data_inicio_contrato'))] if m]
        ev['nota'] = nov.get('motivo')
    elif acao == 'escrita.regime_conflito_limpo':
        antes = nov.get('antes') if isinstance(nov.get('antes'), dict) else {}
        ev['titulo'] = _titulo('Limpou o ', _b('regime tributário'), ' para a equipe conferir')
        ev['ico'], ev['cor'] = 'fa-eraser', 'ambar'
        ev['mudancas'] = [m for m in [_mud('regime', antes.get('regime'), nov.get('regime'))] if m]
        ev['nota'] = nov.get('motivo')
        if nov.get('planilha_dizia'):
            ev['dados'] = [('A planilha dizia', valor('regime', nov.get('planilha_dizia')))]
    elif acao == 'escrita.razao_social_cnpj':
        antes = nov.get('antes') if isinstance(nov.get('antes'), dict) else {}
        ev['titulo'] = _titulo('Corrigiu a ', _b('razão social'), ' pelo CNPJ')
        ev['mudancas'] = [m for m in [_mud('razao_social', antes.get('razao_social'), nov.get('razao_social'),
                                           rot='Razão social')] if m]
        ev['nota'] = nov.get('motivo')
    elif acao == 'escrita.renomeou_pasta_empresa':
        ev['titulo'] = _titulo('Renomeou a ', _b('pasta no Dropbox'))
        ev['ico'], ev['cor'] = 'fa-folder', 'ambar'
        ev['mudancas'] = [m for m in [_mud('pasta', ant.get('pasta'), nov.get('pasta'))] if m]
    elif acao == 'escrita.renumerou_cliente_migracao':
        ev['titulo'] = _titulo('Trocou o ', _b('número do cadastro'))
        ev['ico'] = 'fa-hashtag'
        ev['mudancas'] = [m for m in [_mud('numero_cliente', nov.get('de'), nov.get('para'))] if m]
        ev['nota'] = nov.get('motivo')
    elif acao == 'escrita.converteu_avulso':
        ev['titulo'] = _titulo('Converteu de ', _b('avulso em cliente'))
        ev['ico'], ev['cor'] = 'fa-right-left', 'verde'
        ev['mudancas'] = [m for m in [_mud('numero_cliente', ant.get('numero_cliente'), nov.get('numero_cliente'))] if m]
    elif acao == 'escrita.virou_avulso_migracao':
        ev['titulo'] = _titulo('Passou a ', _b('avulso'), ' (saiu do escritório)')
        ev['ico'], ev['cor'] = 'fa-right-left', 'ambar'
        ev['mudancas'] = [m for m in [_mud('numero_cliente', nov.get('numero_antigo'), None)] if m]
        ev['nota'] = nov.get('motivo')
    elif acao.startswith('escrita.criou_cliente') or acao == 'escrita.criou_avulso_migracao':
        ev['titulo'] = _titulo(_b('Criou o cadastro'), ' (migração)' if ev['migracao'] else '')
        ev['ico'], ev['cor'] = 'fa-circle-plus', 'verde'
        ev['dados'] = _dados(nov)
    elif acao == 'escrita.buscou_cnpj_migracao':
        ev['titulo'] = _titulo('Atualizou os dados pela ', _b('Receita'), ' (CNPJ)')
        ev['ico'], ev['cor'] = 'fa-building-columns', 'verde'
        ev['dados'] = _dados(nov, fora=('receita_base_em',))
    elif acao == 'escrita.completou_cliente_migracao':
        ev['titulo'] = _titulo('Completou o cadastro com a ', _b('Acessórias/Conexa'))
        ev['dados'] = _dados(nov)
    elif acao == 'escrita.ligou_conexa_migracao':
        ev['titulo'] = _titulo('Ligou o cadastro ao ', _b('Conexa'), ' (financeiro)')
        ev['dados'] = _dados(nov, fora=('nome',))
    elif acao == 'escrita.endereco_conexa_migracao':
        ev['titulo'] = _titulo('Trouxe o ', _b('endereço'), ' do Conexa')
        ev['cat'], ev['ico'], ev['cor'] = 'enderecos', 'fa-location-dot', 'ambar'
        ev['dados'] = _dados(nov)
    elif acao in ('escrita.alterou_ciencia_automatica', 'escrita.alterou_captura_automatica'):
        lig = nov.get('manifesta_ciencia', nov.get('modo_automatico')) in (True, 1, 'True')
        alvo = 'ciência automática das notas' if 'ciencia' in acao else 'busca automática de notas'
        ev['titulo'] = _titulo('Ligou a ' if lig else 'Desligou a ', _b(alvo))
        if nov.get('autorizado_por'):
            ev['nota'] = 'autorizado por ' + nome_proprio(str(nov['autorizado_por']))
    else:
        # --- genérico ----------------------------------------------------------
        is_exc = acao.startswith('escrita.excluiu') or (ant and not nov)
        is_alt = bool(ant) and bool(nov)
        verbo = _HIST_VERBO.get(acao)
        if 'socio' in acao and nome_pessoa:
            if acao == 'escrita.criou_socio':
                verbo = _titulo('Adicionou o sócio ', _b(nome_pessoa))
            elif acao == 'escrita.excluiu_socio':
                verbo = _titulo('Excluiu o sócio ', _b(nome_pessoa))
            elif acao == 'escrita.saida_socio':
                verbo = _titulo('Registrou a saída do sócio ', _b(nome_pessoa))
            else:
                verbo = _titulo('Alterou o sócio ', _b(nome_pessoa))
        if is_alt:
            ev['mudancas'] = _diff(ant, nov)
            if len(ev['mudancas']) == 1 and (not verbo or acao == 'escrita.alterou_cliente'):
                verbo = _titulo('Alterou ', _b(ev['mudancas'][0]['rotulo']))
                ev['mudancas'][0]['so_valor'] = True     # o título já diz o campo
                if 'cobran' in ev['mudancas'][0]['rotulo'].lower():
                    ev['ico'], ev['cor'] = 'fa-coins', 'rosa'
        elif is_exc:
            ev['dados'] = _dados(ant, fora=('nome',))
            ev['cor'] = 'verm'
        else:
            ev['dados'] = _dados(nov, fora=('nome',) if nome_pessoa else ())
        # salvar sem mudar nada grava {} -> {}: não vira cartão
        ev['descartar'] = (is_alt or acao.startswith('escrita.alterou')) and not ev['mudancas'] \
            and not ev['dados'] and 'socio' not in acao
        if not verbo:
            if is_exc:
                verbo = 'Excluiu um registro'
            elif is_alt:
                verbo = 'Alterou o cadastro'
            else:
                verbo = 'Adicionou um registro'
        if isinstance(verbo, str) and not isinstance(verbo, Markup):
            verbo = verbo[:1].upper() + verbo[1:]
        ev['titulo'] = _titulo(verbo)

    ev['texto'] = _texto_busca(ev)
    return ev


def _texto_busca(ev):
    partes = [Markup(ev['titulo']).striptags(), ev['autor'], ev.get('nota') or '']
    for m in ev['mudancas']:
        partes += [m['rotulo'], m.get('antes') or '', m.get('depois') or '']
    for r, v in ev['dados']:
        partes += [r, v]
    partes += ev['tags']
    return ' '.join(str(p) for p in partes if p).lower()


# ---------------------------------------------------------------------------
# Página inteira: cartões + migração agrupada + dias
# ---------------------------------------------------------------------------
def _quando(d, hoje):
    """Dia curto no cartão: Hoje, Ontem, ou 05/11/2026 (o dia da semana vai no title)."""
    if d is None:
        return ''
    if d == hoje:
        return 'Hoje'
    if d == hoje - timedelta(days=1):
        return 'Ontem'
    return d.strftime('%d/%m/%Y')


def _agrupar_migracao(evs):
    """Registros de migração seguidos, do mesmo dia e da mesma pessoa -> 1 cartão."""
    out, i = [], 0
    while i < len(evs):
        e = evs[i]
        if not e['migracao']:
            out.append(e)
            i += 1
            continue
        j = i
        while j + 1 < len(evs) and evs[j + 1]['migracao'] and evs[j + 1]['dia'] == e['dia'] \
                and evs[j + 1]['autor'] == e['autor']:
            j += 1
        bloco = evs[i:j + 1]
        if len(bloco) == 1:
            out.append(e)
        else:
            tags = []
            for b in bloco:
                t = Markup(b['titulo']).striptags()
                if t not in tags:
                    tags.append(t)
            horas = sorted(b['hora'] for b in bloco)
            cats = sorted({b['cat'] for b in bloco} | {'migracao'})
            out.append({
                'grupo': True, 'cat': 'migracao', 'cats': cats, 'ico': 'fa-file-import', 'cor': 'azul',
                'titulo': _titulo(_b('Migração da Acessórias/Conexa'), ': %d mudanças' % len(bloco)),
                'autor': e['autor'], 'autor_extra': e['autor_extra'], 'ini': e['ini'], 'dia': e['dia'],
                'hora': horas[0] if horas[0] == horas[-1] else '%s – %s' % (horas[0], horas[-1]),
                'tags': tags[:6], 'mais_tags': max(0, len(tags) - 6), 'filhos': bloco,
                'mudancas': [], 'dados': [], 'nota': None,
                'texto': ' '.join(b['texto'] for b in bloco) + ' migração acessórias conexa',
            })
        i = j + 1
    return out


def montar(rows, nomes, ref, hoje=None):
    """rows (logs_sistema, mais novo primeiro) -> (cartões, contagem por categoria, nº de cartões).
    Uma grade só, sem quebra por dia (dia com 1 registro deixava a linha vazia): o dia
    vai no próprio cartão, ao lado da hora."""
    hoje = hoje or date.today()
    evs = _agrupar_migracao([e for e in (_cartao(r, nomes, ref) for r in rows) if not e.get('descartar')])
    cont = {}
    for e in evs:
        e.setdefault('cats', [e['cat']])
        for c in e['cats']:
            cont[c] = cont.get(c, 0) + 1
        e['quando'] = _quando(e['dia'], hoje)
        e['semana'] = _SEMANA[e['dia'].weekday()] if e['dia'] else ''
    return evs, cont, len(evs)


def nomes_referenciados(rows, execute_query):
    """Nomes de contadores e contatos citados pela página, em 2 consultas no máximo."""
    ctd, cts = set(), set()
    for r in rows:
        acao = r.get('acao') or ''
        if acao == 'escrita.vinculou_contador':
            try:
                ctd.add(int(json.loads(r.get('dados_novos') or '{}').get('contador_id')))
            except Exception:
                pass
        elif r.get('tabela_afetada') == 'contatos_clientes' and r.get('registro_id'):
            cts.add(int(r['registro_id']))
    ref = {'contadores': {}, 'contatos': {}}
    if ctd:
        ph = ','.join(['%s'] * len(ctd))
        for x in execute_query(f"SELECT id, nome_razao_social FROM clientes WHERE id IN ({ph})",
                               tuple(ctd), fetch=True) or []:
            ref['contadores'][str(x['id'])] = nome_proprio(x['nome_razao_social'] or '')
    if cts:
        ph = ','.join(['%s'] * len(cts))
        for x in execute_query(f"SELECT id, nome FROM contatos_clientes WHERE id IN ({ph})",
                               tuple(cts), fetch=True) or []:
            ref['contatos'][x['id']] = nome_proprio(x['nome'] or '')
    return ref


CATEGORIAS = [('cadastro', 'Cadastro'), ('contatos', 'Contatos'), ('socios', 'Sócios'),
              ('responsaveis', 'Responsáveis'), ('enderecos', 'Endereços'), ('certificado', 'Certificado'),
              ('acesso', 'Acesso'), ('migracao', 'Migração')]


def consultas(rows, nomes, hoje=None):
    """Visão Consultas (só admin): quem abriu a ficha / buscou notas desta empresa."""
    from utils.auditoria_fmt import hist_preparar
    hoje = hoje or date.today()
    evs = []
    for r in rows:
        it = hist_preparar(r, nomes)
        autor, extra = _autor(r, nomes)
        dh = r.get('data_hora')
        acao = r.get('acao') or ''
        ico = 'fa-eye' if 'abriu' in acao else 'fa-file-export' if 'export' in acao else 'fa-magnifying-glass'
        v = it['verbo'] or 'consultou'
        ev = {'cat': 'consulta', 'cats': ['consulta'], 'ico': ico, 'cor': 'cinza', 'autor': autor,
              'autor_extra': extra, 'ini': _ini(autor), 'hora': dh.strftime('%H:%M') if dh else '',
              'titulo': _titulo(v[:1].upper() + v[1:]), 'mudancas': [], 'dados': [], 'tags': [],
              'nota': ' · '.join(x for x in (('buscou: ' + it['busca']) if it.get('busca') else None,
                                             it.get('detalhe')) if x) or None}
        ev['texto'] = _texto_busca(ev)
        d = dh.date() if hasattr(dh, 'date') else None
        ev['quando'], ev['semana'] = _quando(d, hoje), (_SEMANA[d.weekday()] if d else '')
        evs.append(ev)
    return evs, {}, len(evs)
