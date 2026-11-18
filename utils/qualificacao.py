"""Qualificação da pessoa por extenso e a FICHA CADASTRAL em texto (20/10/2026; refeitas em 17/11/2026).

* ``qualificacao(cliente_id)`` → (texto, faltam): o parágrafo que abre o contrato social, a
  alteração, a procuração. Concorda em gênero quando o cadastro diz o sexo ("brasileira,
  casada, empresária, portadora, inscrita, residente e domiciliada"); sem o sexo, fica
  "brasileiro(a)". Traz naturalidade, nascimento, filiação, estado civil com regime, profissão,
  documentos marcados para contrato, CPF, endereço e e-mail. Para empresa: natureza jurídica,
  CNPJ, NIRE, sede e "neste ato representada por" quem é responsável no quadro de sócios.
  O que falta no cadastro fica de fora e volta em ``faltam`` (a tela avisa antes de colar).

* ``ficha(cliente_id)`` → texto organizado em quadros (IDENTIFICAÇÃO, DOCUMENTOS, ENDEREÇOS,
  CONTATOS, SÓCIOS / SÓCIA EM, RESPONSÁVEIS NO ESCRITÓRIO, COBRANÇA), com tudo que o cadastro
  tem e nada do que não tem. Anderson (17/11/2026): "Copiar Qualificação e Copiar Ficha
  preciso que melhore 1000000%".
"""
import re
from datetime import date

from models.endereco_cliente import EnderecoCliente
from models.pessoa_dados import (PessoaDados, Documentos, COM_REGIME, ESTADOS_CIVIS, REGIMES_BENS, SEXOS,
                                 GRAUS_INSTRUCAO, RACAS_COR, NIVEIS_GOVBR, COM_VALIDADE)
from utils.db_helper import execute_query
from utils.texto import nome_proprio

_ROT = {'estado_civil': dict(ESTADOS_CIVIS), 'regime_bens': dict(REGIMES_BENS), 'sexo': dict(SEXOS),
        'grau_instrucao': dict(GRAUS_INSTRUCAO), 'raca_cor': dict(RACAS_COR), 'nivel_govbr': dict(NIVEIS_GOVBR)}
_DIG = lambda s: re.sub(r'\D', '', s or '')


def _rot(campo, valor):
    return _ROT.get(campo, {}).get(valor, valor or '')


def _flex(texto, sexo):
    """'Casado(a)' → 'casada' / 'casado' / 'casado(a)'; 'Brasileiro(a)' → 'brasileira'…"""
    t = (texto or '').strip()
    if not t:
        return ''
    if sexo == 'F':
        t = re.sub(r'o\(a\)', 'a', t)
        t = re.sub(r'\(a\)', 'a', t)
    elif sexo == 'M':
        t = re.sub(r'\(a\)', '', t)
    return t if t.isupper() else t[0].lower() + t[1:]


def _g(sexo, masc, fem, neutro=None):
    """Palavra concordada: _g(sexo, 'nascido', 'nascida') → 'nascido(a)' quando não se sabe."""
    return fem if sexo == 'F' else masc if sexo == 'M' else (neutro or (masc + '(a)'))


def _data(d):
    return d.strftime('%d/%m/%Y') if d else ''


def _idade(d):
    if not d:
        return None
    h = date.today()
    return h.year - d.year - ((h.month, h.day) < (d.month, d.day))


def _cep(c):
    d = _DIG(c)
    return f'{d[:5]}-{d[5:]}' if len(d) == 8 else (c or '')


def _cpf(c):
    d = _DIG(c)
    return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}' if len(d) == 11 else (c or '')


def _cnpj(c):
    d = _DIG(c)
    return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}' if len(d) == 14 else (c or '')


def _doc_fmt(c):
    d = _DIG(c)
    return _cnpj(c) if len(d) == 14 else _cpf(c) if len(d) == 11 else (c or '')


def _complemento(c):
    """'APARTAMENTO 32 - BLOCO A' → 'Apartamento 32, Bloco A'."""
    partes = [p.strip() for p in re.split(r'\s+[-–|/]\s+|,', c or '') if p.strip()]
    return ', '.join(nome_proprio(p) for p in partes)


def _numero(n):
    n = str(n or '').strip()
    return 's/nº' if n.upper().replace('/', '').replace('.', '').replace('º', '') in ('SN', 'SNO', '') else 'nº ' + n


def _enderecos(cliente_id):
    ends = EnderecoCliente.get_by_cliente(cliente_id) or []
    return sorted(ends, key=lambda e: (not e.get('principal'), e.get('id') or 0))


def _endereco_extenso(e):
    """'Rua Jaime Taveira, nº 91, Apartamento 32, Bloco A, Chácara Cruzeiro do Sul, São Paulo/SP, CEP 03732-060'."""
    if not e or not e.get('logradouro'):
        return ''
    partes = [nome_proprio(e['logradouro']) + ', ' + _numero(e.get('numero')), _complemento(e.get('complemento')),
              nome_proprio(e.get('bairro')),
              nome_proprio(e.get('cidade') or '') + ('/' + e['estado'] if e.get('estado') else '')]
    if e.get('cep'):
        partes.append('CEP ' + _cep(e['cep']))
    return ', '.join(p for p in partes if p and p.strip())


def _preposicao_endereco(e):
    """'na Rua…', 'na Avenida…', 'no Largo…', 'na Alameda…'."""
    lg = (e.get('logradouro') or '').strip().lower()
    masc = ('largo', 'beco', 'parque', 'jardim', 'loteamento', 'conjunto', 'condomínio', 'condominio', 'sítio', 'sitio',
            'caminho', 'acesso', 'setor', 'núcleo', 'nucleo', 'distrito', 'morro', 'campo', 'bosque', 'trevo')
    return 'no' if lg.startswith(masc) else 'na'


# Para onde a ficha leva quem clica no que falta (17/11/2026, Anderson: "se tem pendência tem que aparecer o que é pendente").
ALVO_FALTA = {'nacionalidade': 'campo-pf-nacionalidade', 'estado civil': 'dados-pessoais', 'regime de bens': 'campo-pf-regime',
              'profissão': 'campo-pf-profissao', 'nascimento': 'campo-pf-nascimento', 'documento de identidade': 'dados-pessoais',
              'CPF': 'identificacao', 'CNPJ': 'identificacao', 'endereço': 'enderecos', 'sexo': 'campo-pf-sexo',
              'naturalidade': 'dados-pessoais', 'filiação': 'dados-pessoais', 'administrador(a)': 'socios'}

_DOC_NOME = {
    'RG': ('da', 'cédula de identidade RG'), 'CIN': ('da', 'Carteira de Identidade Nacional (CIN)'),
    'CNH': ('da', 'Carteira Nacional de Habilitação (CNH)'), 'PASSAPORTE': ('do', 'passaporte'),
    'RNM': ('do', 'Registro Nacional Migratório (RNM)'), 'TITULO': ('do', 'título de eleitor'), 'CTPS': ('da', 'CTPS'),
}
_CONSELHOS = ('OAB', 'CRC', 'CRM', 'CREA', 'CRO', 'CRF', 'CRBM', 'CRA')


def _cliente(cliente_id):
    return execute_query(
        """SELECT c.id, c.numero_cliente, c.avulso, c.tipo_pessoa, c.nome_razao_social, c.nome_fantasia, c.cpf_cnpj,
                  c.inscricao_estadual, c.inscricao_municipal, c.nire, c.nire_data_registro, c.email, c.telefone, c.celular,
                  c.regime_tributario, c.porte_empresa, c.cnae_fiscal, c.cnae_fiscal_descricao, c.situacao,
                  c.data_inicio_atividade, c.data_inicio_contrato, c.cobranca, c.natureza_codigo, c.natureza_juridica,
                  c.observacoes,
                  (SELECT GROUP_CONCAT(DISTINCT g.nome ORDER BY g.nome SEPARATOR ', ') FROM cliente_grupo_relacao cgr
                     JOIN grupos_clientes g ON g.id = cgr.grupo_id WHERE cgr.cliente_id = c.id) AS grupos,
                  (SELECT GROUP_CONCAT(DISTINCT ra.nome ORDER BY ra.nome SEPARATOR ', ') FROM cliente_ramo_atividade_relacao r
                     JOIN ramos_atividade ra ON ra.id = r.ramo_atividade_id WHERE r.cliente_id = c.id) AS ramos,
                  p.nome_razao_social AS pagador_nome, p.numero_cliente AS pagador_numero
             FROM clientes c LEFT JOIN clientes p ON p.id = c.cobranca_pagador_id
            WHERE c.id = %s""", (cliente_id,), fetch=True, fetch_one=True)


# ---------------------------------------------------------------------------
# Qualificação
# ---------------------------------------------------------------------------
def _docs_contrato(docs, sexo):
    """'portadora da cédula de identidade RG nº 4845747, expedida pelo DETRAN/GO, e da CNH nº …'
    + os conselhos à parte ('inscrita na OAB/SP sob o nº …')."""
    no_contrato = [d for d in docs if d.get('contrato')] or [d for d in docs if d['tipo'] not in _CONSELHOS][:1]
    ids, conselhos = [], []
    for d in no_contrato:
        orgao = '/'.join(x for x in (d.get('orgao'), d.get('uf')) if x)
        if d['tipo'] in _CONSELHOS:
            conselhos.append(f"{_g(sexo, 'inscrito', 'inscrita')} {'na' if d['tipo'] == 'OAB' else 'no'} {d['tipo']}"
                             + (f"/{d['uf']}" if d.get('uf') else '') + f" sob o nº {d['numero']}")
            continue
        art, nome = _DOC_NOME.get(d['tipo'], ('do', d['tipo']))
        ids.append(f"{art} {nome} nº {d['numero']}" + (f", expedid{'a' if art == 'da' else 'o'} pel{'o' if orgao.split('/')[0] in ('DETRAN', 'IIRGD', 'IFP', 'DGPC', 'ITEP', 'IGP', 'DPF', 'DIC', 'DPT') else 'a'} {orgao}" if orgao else ''))
    port = ''
    if ids:
        port = f"{_g(sexo, 'portador', 'portadora')} " + (ids[0] if len(ids) == 1 else ', '.join(ids[:-1]) + ' e ' + ids[-1])
    return port, conselhos


def _qualificacao_pf(c, com_nome=True):
    p = PessoaDados.get(c['id'])
    docs = Documentos.listar(c['id'])
    sexo = p.get('sexo') or ''
    nome = (c['nome_razao_social'] or '').strip().upper()
    partes, faltam = ([nome] if com_nome else []), []
    if not sexo:
        faltam.append('sexo')
    partes.append(_flex(p['nacionalidade'], sexo)) if p.get('nacionalidade') else faltam.append('nacionalidade')
    if p.get('naturalidade_cidade'):
        partes.append('natural de ' + nome_proprio(p['naturalidade_cidade']) + ('/' + p['naturalidade_uf'] if p.get('naturalidade_uf') else ''))
    else:
        faltam.append('naturalidade')
    partes.append(f"{_g(sexo, 'nascido', 'nascida')} em {_data(p['data_nascimento'])}") if p.get('data_nascimento') else faltam.append('nascimento')
    pais = [nome_proprio(x) for x in (p.get('nome_pai'), p.get('nome_mae')) if x]
    if pais:
        partes.append(f"{_g(sexo, 'filho', 'filha')} de " + ' e de '.join(pais))
    else:
        faltam.append('filiação')
    if p.get('estado_civil'):
        if p['estado_civil'] == 'UNIAO_ESTAVEL':
            ec = 'convivente em união estável'
        else:
            ec = _flex(_rot('estado_civil', p['estado_civil']), sexo)
        if p['estado_civil'] in COM_REGIME:
            if p.get('regime_bens'):
                ec += ' sob o regime de ' + _flex(_rot('regime_bens', p['regime_bens']), sexo)
            else:
                faltam.append('regime de bens')
        partes.append(ec)
    else:
        faltam.append('estado civil')
    partes.append(_flex(p['profissao'], sexo)) if p.get('profissao') else faltam.append('profissão')
    port, conselhos = _docs_contrato(docs, sexo)
    partes += conselhos
    partes.append(port) if port else faltam.append('documento de identidade')
    partes.append(f"{_g(sexo, 'inscrito', 'inscrita')} no CPF/MF sob o nº {_cpf(c['cpf_cnpj'])}") if c.get('cpf_cnpj') else faltam.append('CPF')
    ends = _enderecos(c['id'])
    if ends and ends[0].get('logradouro'):
        partes.append(f"residente e {_g(sexo, 'domiciliado', 'domiciliada')} {_preposicao_endereco(ends[0])} {_endereco_extenso(ends[0])}")
    else:
        faltam.append('endereço')
    if c.get('email'):
        partes.append('endereço eletrônico ' + c['email'].strip().lower())
    return ', '.join(x for x in partes if x), faltam


def _natureza_extenso(c):
    n = (c.get('natureza_juridica') or '').strip()
    if not n:
        return 'pessoa jurídica de direito privado'
    return ' '.join(w if (w.isupper() and len(w) > 1) else w.lower() for w in n.split())


def _qualificacao_pj(c):
    nome = (c['nome_razao_social'] or '').strip().upper()
    partes, faltam = [nome, _natureza_extenso(c)], []
    partes.append('inscrita no CNPJ/MF sob o nº ' + _cnpj(c['cpf_cnpj'])) if c.get('cpf_cnpj') else faltam.append('CNPJ')
    if c.get('nire'):
        partes.append('NIRE ' + str(c['nire']) + (f" (registro em {_data(c['nire_data_registro'])})" if c.get('nire_data_registro') else ''))
    ends = _enderecos(c['id'])
    if ends and ends[0].get('logradouro'):
        partes.append(f"com sede {_preposicao_endereco(ends[0])} {_endereco_extenso(ends[0])}")
    else:
        faltam.append('endereço')
    if c.get('email'):
        partes.append('endereço eletrônico ' + c['email'].strip().lower())
    texto = ', '.join(partes)
    # quem assina por ela: o responsável do quadro de sócios (administrador)
    adms = execute_query(
        """SELECT s.pf_cliente_id, COALESCE(pf.nome_razao_social, s.nome) AS nome, s.qualificacao
             FROM socios_clientes s LEFT JOIN clientes pf ON pf.id = s.pf_cliente_id
            WHERE s.cliente_id = %s AND s.ativo = 1 AND s.responsavel = 1 ORDER BY nome""", (c['id'],), fetch=True) or []
    if not adms:
        faltam.append('administrador(a)')
        return texto, faltam
    reps = []
    for a in adms:
        if a.get('pf_cliente_id'):
            pf = _cliente(a['pf_cliente_id'])
            q, f2 = _qualificacao_pf(pf, com_nome=True)
            faltam += [f'{x} de {nome_proprio(a["nome"])}' for x in f2]
            sexo = (PessoaDados.get(a['pf_cliente_id']) or {}).get('sexo') or ''
        else:
            q, sexo = (a['nome'] or '').upper(), ''
        papel = (a.get('qualificacao') or '').strip().lower()
        papel = 'sócio' + ('-administrador' if 'adm' in papel or not papel else '') if 'adm' in papel or 'sócio' in papel or 'socio' in papel or not papel else papel
        papel = papel.replace('sócio', _g(sexo, 'sócio', 'sócia', 'sócio(a)')).replace('administrador', _g(sexo, 'administrador', 'administradora', 'administrador(a)'))
        reps.append(f"{'sua' if sexo == 'F' else 'seu'} {papel} {q}")
    texto += ', neste ato representada por ' + (reps[0] if len(reps) == 1 else '; e por '.join(reps))
    return texto, faltam


def qualificacao(cliente_id):
    """(texto, faltam) do cadastro; ('', []) se o cadastro não existe."""
    c = _cliente(cliente_id)
    if not c:
        return '', []
    return _qualificacao_pj(c) if c['tipo_pessoa'] == 'PJ' else _qualificacao_pf(c)


# ---------------------------------------------------------------------------
# Ficha cadastral em texto — formato de mensagem (WhatsApp): *negrito*, quadros com ícone,
# linhas curtas. Anderson (17/11/2026): "Ficha seria para enviar pelo WhatsApp… se for sócio de
# empresas tem que puxar tudo… quero tudo muito elegante". Nada interno do escritório vai
# (cobrança, responsáveis, nº de cadastro): a ficha sai para fora.
# ---------------------------------------------------------------------------
def _tel(t):
    d = _DIG(t)
    if len(d) == 11:
        return f'({d[:2]}) {d[2:7]}-{d[7:]}'
    if len(d) == 10:
        return f'({d[:2]}) {d[2:6]}-{d[6:]}'
    return (t or '').strip()


def _moeda(v):
    return 'R$ ' + f"{float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def _kv(rotulo, valor):
    v = (str(valor).strip() if valor is not None else '')
    return f'{rotulo}: {v}' if v and v not in ('—', 'None') else ''


def _bloco(icone, titulo, linhas):
    linhas = [x for x in linhas if x]
    return (f'{icone} *{titulo}*\n' + '\n'.join(linhas)) if linhas else ''


def _end_linhas(e):
    """Duas linhas: 'Rua Jaime Taveira, 91, Apartamento 32, Bloco A' / 'Chácara Cruzeiro do Sul · São Paulo/SP · CEP 03732-060'."""
    if not e or not e.get('logradouro'):
        return []
    l1 = nome_proprio(e['logradouro']) + ', ' + (str(e.get('numero') or 's/nº')) + (', ' + _complemento(e['complemento']) if e.get('complemento') else '')
    l2 = ' · '.join(x for x in (nome_proprio(e.get('bairro')), nome_proprio(e.get('cidade') or '') + ('/' + e['estado'] if e.get('estado') else ''),
                                ('CEP ' + _cep(e['cep'])) if e.get('cep') else '') if x)
    return [l1, l2]


def _blocos_enderecos(cliente_id, icone='📍'):
    out = []
    for e in _enderecos(cliente_id):
        ls = _end_linhas(e)
        if ls:
            tit = 'Endereço ' + (e.get('tipo') or '').lower() + (' · principal' if e.get('principal') and len(_enderecos(cliente_id)) > 1 else '')
            out.append(_bloco(icone, tit.strip(), ls))
    return out


def _bloco_contatos(cliente_id, icone='📞'):
    from models.contato_cliente import ContatoCliente
    linhas = []
    for ct in ContatoCliente.get_by_cliente(cliente_id) or []:
        if not ct.get('ativo', 1):
            continue
        cab = '*' + nome_proprio(ct.get('nome')) + '*' + (' · ' + ct['cargo'] if ct.get('cargo') else '')
        meios = []
        for ce in [ct.get('celular')] + list(ct.get('celulares_extra') or []):
            if ce:
                meios.append('📱 ' + _tel(ce))
        if ct.get('telefone'):
            meios.append('☎️ ' + _tel(ct['telefone']))
        for em in [ct.get('email')] + list(ct.get('emails_extra') or []):
            if em:
                meios.append('✉️ ' + em.strip().lower())
        linhas.append(cab + ''.join('\n' + m for m in meios))
    return _bloco(icone, 'Contatos', linhas)


def _linhas_pessoa(c, p, docs, resumo=False):
    """Os dados pessoais em linhas. ``resumo`` = sócio dentro da ficha da empresa (sem filiação, eSocial, cônjuge)."""
    sexo = p.get('sexo') or ''
    idade = _idade(p.get('data_nascimento'))
    ls = [
        _kv('CPF', _cpf(c['cpf_cnpj'])),
        _kv('Nascimento', (_data(p['data_nascimento']) + (f' · {idade} anos' if idade is not None else '')) if p.get('data_nascimento') else ''),
        _kv('Naturalidade', (nome_proprio(p['naturalidade_cidade']) + ('/' + p['naturalidade_uf'] if p.get('naturalidade_uf') else '')) if p.get('naturalidade_cidade') else ''),
        _kv('Nacionalidade', _flex(p.get('nacionalidade'), sexo).capitalize() if p.get('nacionalidade') else ''),
    ]
    if not resumo:
        ls += [_kv('Mãe', nome_proprio(p.get('nome_mae'))), _kv('Pai', nome_proprio(p.get('nome_pai')))]
    ls.append(_kv('Estado civil', (_flex(_rot('estado_civil', p['estado_civil']), sexo).capitalize()
                                   + (' · ' + _flex(_rot('regime_bens', p['regime_bens']), sexo).lower() if p.get('regime_bens') and p['estado_civil'] in COM_REGIME else ''))
                  if p.get('estado_civil') else ''))
    if not resumo:
        ls.append(_kv('Cônjuge', (nome_proprio(p.get('conjuge_nome')) + (f" · CPF {_cpf(p['conjuge_cpf'])}" if p.get('conjuge_cpf') else '')) if p.get('conjuge_nome') or p.get('conjuge_cpf') else ''))
    ls.append(_kv('Profissão', _flex(p.get('profissao'), sexo).capitalize() if p.get('profissao') else ''))
    if not resumo:
        ls.append(_kv('PIS/NIT', p.get('pis_nit')))
    ls += [_kv('Celular', _tel(c.get('celular'))), _kv('Telefone', _tel(c.get('telefone'))), _kv('E-mail', (c.get('email') or '').lower())]
    return ls


def _linhas_docs(docs):
    hoje = date.today()
    out = []
    for d in docs:
        orgao = '/'.join(x for x in (d.get('orgao'), d.get('uf')) if x)
        s = f"{d['tipo']} {d['numero']}" + (' · ' + orgao if orgao else '')
        if d.get('validade'):
            s += (' · ⚠️ vencida em ' if d['validade'] < hoje else ' · válida até ') + _data(d['validade'])
        out.append(s)
    return out


def _fantasia_ok(e):
    f = (e.get('nome_fantasia') or '').strip()
    return bool(re.search(r'[A-Za-zÀ-ÿ]', f)) and nome_proprio(f).lower() != nome_proprio(e.get('nome_razao_social') or '').lower()


def _bloco_empresa(cid, participacao, outros=True):
    """A empresa em que a pessoa é sócia, com os dados dela: identificação, sede, contato,
    a participação da pessoa e os demais sócios."""
    from models.socio_cliente import SocioCliente
    e = _cliente(cid)
    if not e:
        return ''
    linhas = [_kv('CNPJ', _cnpj(e['cpf_cnpj']))]
    if _fantasia_ok(e):
        linhas.insert(0, _kv('Nome fantasia', nome_proprio(e['nome_fantasia'])))
    nat = ' · '.join(x for x in ((e.get('natureza_juridica') or ''), (e.get('porte_empresa') or ''),
                                 (e.get('regime_tributario') or '').replace('_', ' ').title()) if x)
    linhas.append(_kv('Natureza', nat))
    linhas.append(_kv('Atividade', ((e.get('cnae_fiscal') or '') + ' · ' + (e.get('cnae_fiscal_descricao') or '')).strip(' ·')) if e.get('cnae_fiscal_descricao') else '')
    linhas.append(_kv('Início das atividades', _data(e.get('data_inicio_atividade'))))
    linhas.append(_kv('NIRE', e.get('nire')))
    linhas.append(_kv('Situação', (e.get('situacao') or '').capitalize()) if (e.get('situacao') or '').upper() != 'ATIVO' else '')
    ends = _enderecos(cid)
    if ends:
        ls = _end_linhas(ends[0])
        if ls:
            linhas.append('Sede: ' + ls[0] + '\n' + ls[1])
    linhas += [_kv('Telefone', _tel(e.get('telefone') or e.get('celular'))), _kv('E-mail', (e.get('email') or '').lower())]
    if participacao:
        linhas.append('▸ ' + participacao)
    if outros:
        quadro = SocioCliente.get_by_cliente(cid) or []
        ls = []
        for s in quadro:
            pct = s.get('percentual_participacao')
            ls.append(nome_proprio(s.get('nome')) + (f" ({float(pct):g}%)" if pct is not None else '') + (' · responsável' if s.get('responsavel') else ''))
        cap = sum(float(s.get('capital_valor') or 0) for s in quadro)
        if ls:
            linhas.append('Quadro societário: ' + '; '.join(ls) + (f' · capital social {_moeda(cap)}' if cap else ''))
    return _bloco('🏢', nome_proprio(e['nome_razao_social']), linhas)


def _participacao(s, sexo=''):
    pct, cap = s.get('percentual_participacao'), s.get('capital_valor')
    papel = (s.get('qualificacao') or '').strip()
    if papel and sexo == 'F':
        papel = papel.replace('Sócio', 'Sócia').replace('sócio', 'sócia').replace('Administrador', 'Administradora').replace('administrador', 'administradora')
    partes = [papel, f"{float(pct):g}% do capital" if pct is not None else '', ('capital ' + _moeda(cap)) if cap is not None else '',
              'responsável pela empresa' if s.get('responsavel') else '',
              ('desde ' + _data(s['data_entrada'])) if s.get('data_entrada') else '',
              ('saiu em ' + _data(s['data_saida'])) if s.get('data_saida') else ('' if s.get('ativo', 1) else 'ex-sócio(a)')]
    return ' · '.join(x for x in partes if x)


def _ficha_pf(c):
    p = PessoaDados.get(c['id'])
    docs = Documentos.listar(c['id'])
    sexo = p.get('sexo') or ''
    cab = ('*FICHA CADASTRAL*\n*' + nome_proprio(c['nome_razao_social']) + '*' + (f"\n_{nome_proprio(p['nome_social'])}_" if p.get('nome_social') else '')
           + ('\nCPF ' + _cpf(c['cpf_cnpj']) if c.get('cpf_cnpj') else ''))
    blocos = [cab, _bloco('👤', 'Dados pessoais', _linhas_pessoa(c, p, docs)[1:]), _bloco('🪪', 'Documentos', _linhas_docs(docs))]
    blocos += _blocos_enderecos(c['id'])
    blocos.append(_bloco_contatos(c['id']))
    socs = execute_query(
        """SELECT s.cliente_id, s.qualificacao, s.percentual_participacao, s.responsavel, s.data_entrada, s.data_saida, s.ativo, s.capital_valor
             FROM socios_clientes s JOIN clientes c ON c.id = s.cliente_id
            WHERE s.pf_cliente_id = %s ORDER BY s.ativo DESC, c.nome_razao_social""", (c['id'],), fetch=True) or []
    atuais = [s for s in socs if s.get('ativo')]
    if atuais:
        blocos.append(f"*{_g(sexo, 'Sócio', 'Sócia', 'Sócio(a)')} em {len(atuais)} empresa{'s' if len(atuais) > 1 else ''}*")
        for s in atuais:
            blocos.append(_bloco_empresa(s['cliente_id'], _participacao(s, sexo)))
    antigas = [s for s in socs if not s.get('ativo')]
    if antigas:
        blocos.append(_bloco('🕓', 'Sociedades anteriores', [nome_proprio((_cliente(s['cliente_id']) or {}).get('nome_razao_social')) + ' · ' + _participacao(s, sexo) for s in antigas]))
    blocos.append(f"_Qualicontax · {date.today().strftime('%d/%m/%Y')}_")
    return '\n\n'.join(b for b in blocos if b)


def _ficha_pj(c):
    from models.socio_cliente import SocioCliente
    cab = ('*FICHA CADASTRAL*\n*' + nome_proprio(c['nome_razao_social']) + '*' + (f"\n_{nome_proprio(c['nome_fantasia'])}_" if _fantasia_ok(c) else '')
           + ('\nCNPJ ' + _cnpj(c['cpf_cnpj']) if c.get('cpf_cnpj') else ''))
    ident = [
        _kv('Natureza jurídica', c.get('natureza_juridica')),
        _kv('Porte', c.get('porte_empresa')),
        _kv('Regime tributário', (c.get('regime_tributario') or '').replace('_', ' ').title()),
        _kv('Atividade principal', ((c.get('cnae_fiscal') or '') + ' · ' + (c.get('cnae_fiscal_descricao') or '')).strip(' ·')),
        _kv('Início das atividades', _data(c.get('data_inicio_atividade'))),
        _kv('NIRE', (str(c['nire']) + (f" · registro em {_data(c['nire_data_registro'])}" if c.get('nire_data_registro') else '')) if c.get('nire') else ''),
        _kv('Inscrição municipal', c.get('inscricao_municipal')),
        _kv('Situação', (c.get('situacao') or '').capitalize()) if (c.get('situacao') or '').upper() != 'ATIVO' else '',
        _kv('Telefone', _tel(c.get('telefone'))), _kv('Celular', _tel(c.get('celular'))), _kv('E-mail', (c.get('email') or '').lower()),
    ]
    ies = execute_query("SELECT uf, ie, tipo, situacao FROM clientes_inscricoes_estaduais WHERE cliente_id = %s ORDER BY principal DESC, uf", (c['id'],), fetch=True) or []
    for ie in ies:
        ident.append(_kv('Inscrição estadual ' + (ie.get('uf') or ''), (ie.get('ie') or '') + (' · ' + ie['tipo'].lower() if ie.get('tipo') else '')))
    if not ies and c.get('inscricao_estadual'):
        ident.append(_kv('Inscrição estadual', c['inscricao_estadual']))
    blocos = [cab, _bloco('🏢', 'Empresa', ident)] + _blocos_enderecos(c['id']) + [_bloco_contatos(c['id'])]
    quadro = SocioCliente.get_by_cliente(c['id']) or []
    cap_total = sum(float(s.get('capital_valor') or 0) for s in quadro)
    if quadro:
        blocos.append('*Quadro societário*' + (f' · capital social {_moeda(cap_total)}' if cap_total else ''))
        for s in quadro:
            pfid = s.get('pf_cliente_id')
            sexo = ''
            linhas = []
            if pfid:
                pc, p, docs = _cliente(pfid), PessoaDados.get(pfid), Documentos.listar(pfid)
                sexo = p.get('sexo') or ''
                if pc:
                    linhas = _linhas_pessoa(pc, p, docs, resumo=True)
                    dl = _linhas_docs(docs)
                    if dl:
                        linhas.append('Documentos: ' + ' | '.join(dl))
                    ends = _enderecos(pfid)
                    el = _end_linhas(ends[0]) if ends else []
                    if el:
                        linhas.append('Endereço: ' + el[0] + '\n' + el[1])
            else:
                linhas = [_kv('CPF/CNPJ', _doc_fmt(s.get('cpf'))), _kv('Telefone', _tel(s.get('telefone'))), _kv('E-mail', (s.get('email') or '').lower())]
            linhas.insert(0, '▸ ' + _participacao(s, sexo))
            blocos.append(_bloco('👤', nome_proprio(s.get('nome')), linhas))
    blocos.append(f"_Qualicontax · {date.today().strftime('%d/%m/%Y')}_")
    return '\n\n'.join(b for b in blocos if b)


def ficha(cliente_id):
    """A ficha cadastral inteira em texto de mensagem, pronta para o WhatsApp ou e-mail."""
    c = _cliente(cliente_id)
    if not c:
        return ''
    return _ficha_pj(c) if c['tipo_pessoa'] == 'PJ' else _ficha_pf(c)
