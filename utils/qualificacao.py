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
# Ficha cadastral em texto — formato de mensagem (WhatsApp). Anderson (17/11/2026): "Ficha seria para
# enviar pelo WhatsApp… se for sócio de empresas tem que puxar tudo… quero tudo muito elegante";
# "Melhorar muito muito mais". Regras: cabeçalho com resumo, frases em vez de rótulo: valor, nada
# repetido, empresas numeradas e compactas, endereço limpo, rodapé com separador. Nada interno do
# escritório vai (cobrança, responsáveis, nº de cadastro, raça/cor, instrução): a ficha sai para fora.
# ---------------------------------------------------------------------------
_TRACO = '━━━━━━━━━━━━━━━━━━━━'
_REGIME = {'SIMPLES': 'Simples Nacional', 'SIMPLES_NACIONAL': 'Simples Nacional', 'MEI': 'MEI',
           'LUCRO_PRESUMIDO': 'Lucro Presumido', 'LUCRO_REAL': 'Lucro Real', 'IMUNE': 'Imune/Isenta'}
# acentos que o cadastro em caixa alta perde — só para ENDEREÇO (nome de gente não se mexe)
_ACENTOS = {'SAO': 'São', 'GOIANIA': 'Goiânia', 'ANAPOLIS': 'Anápolis', 'BRASILIA': 'Brasília', 'ANDRE': 'André',
            'RIBEIRAO': 'Ribeirão', 'JOSE': 'José', 'FLORIANOPOLIS': 'Florianópolis', 'VITORIA': 'Vitória',
            'MACEIO': 'Maceió', 'BELEM': 'Belém', 'CUIABA': 'Cuiabá', 'MACAPA': 'Macapá', 'JOAO': 'João',
            'LUIS': 'Luís', 'GONCALO': 'Gonçalo', 'GONCALVES': 'Gonçalves', 'SEBASTIAO': 'Sebastião',
            'JUNDIAI': 'Jundiaí', 'UBERLANDIA': 'Uberlândia', 'JACAREI': 'Jacareí', 'NITEROI': 'Niterói',
            'CAMACARI': 'Camaçari', 'TABOAO': 'Taboão', 'MAUA': 'Mauá', 'GUARUJA': 'Guarujá', 'SANTANA': 'Santana',
            'PARAISO': 'Paraíso', 'JARDIM': 'Jardim', 'AMERICA': 'América', 'INDUSTRIAL': 'Industrial',
            'SETIMA': 'Sétima', 'ORQUIDEAS': 'Orquídeas', 'CRISANTEMOS': 'Crisântemos', 'ANTONIO': 'Antônio',
            'CONCEICAO': 'Conceição', 'ESTACAO': 'Estação', 'AVENIDA': 'Avenida', 'MONICA': 'Mônica', 'CANDIDO': 'Cândido'}


def _hoje():
    """Data de Brasília: o app roda em UTC e, depois das 21h, date.today() já é amanhã."""
    from utils.titulos_receber import hoje_br
    return hoje_br()


def _tel(t):
    d = _DIG(t)
    if len(d) == 11:
        return f'({d[:2]}) {d[2:7]}-{d[7:]}'
    if len(d) == 10:
        return f'({d[:2]}) {d[2:6]}-{d[6:]}'
    return (t or '').strip()


def _moeda(v):
    return 'R$ ' + f"{float(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def _pct(v):
    return f"{float(v):g}%".replace('.', ',') if v is not None else ''


def _lugar(t):
    """Texto de endereço em caixa normal, com os acentos de sempre e 'QUADRA41' → 'Quadra 41'."""
    t = ' '.join((t or '').split())
    if not t:
        return ''
    if t == t.upper():
        t = nome_proprio(t)
    t = re.sub(r'[A-Za-zÀ-ÿ]+', lambda m: _ACENTOS.get(m.group(0).upper(), m.group(0)), t)
    t = re.sub(r'(?i)\b(quadra|lote|sala|bloco|apto|apartamento|andar|casa|conjunto|conj|modulo|módulo|galpao|galpão|loja)(\d)', r'\1 \2', t)
    t = re.sub(r'(?i)\bs/n\b\.?', 's/nº', t)
    # 'QUADRA 41 Lote 17' (texto misto que o nome_proprio não toca): as palavras de endereço em caixa alta descem
    t = re.sub(r'\b(QUADRA|LOTE|SALA|BLOCO|APTO|APARTAMENTO|ANDAR|CASA|CONJUNTO|CONJ|GALPAO|GALPÃO|LOJA|FUNDOS|TERREO|TÉRREO|MODULO|MÓDULO|RESIDENCIAL|JARDIM|SETOR|VILA|PARQUE)\b',
               lambda m: m.group(0).capitalize(), t)
    return t


def _end_linhas(e):
    """'Rua Jaime Taveira, 91 – Apartamento 32, Bloco A' / 'Chácara Cruzeiro do Sul – São Paulo/SP' / 'CEP 03732-060'."""
    if not e or not e.get('logradouro'):
        return []
    num = str(e.get('numero') or '').strip()
    num = 's/nº' if not num or num.upper().replace('/', '').replace('.', '').replace('º', '') in ('SN', 'SNO') else num
    l1 = _lugar(e['logradouro']) + ', ' + num + (' – ' + _complemento_limpo(e['complemento']) if e.get('complemento') else '')
    cid = _lugar(e.get('cidade') or '') + ('/' + e['estado'] if e.get('estado') else '')
    l2 = ' – '.join(x for x in (_lugar(e.get('bairro')), cid) if x)
    ls = [l1, l2] if l2 else [l1]
    if e.get('cep'):
        ls.append('CEP ' + _cep(e['cep']))
    return ls


def _complemento_limpo(c):
    partes = [p.strip() for p in re.split(r'\s+[-–|/]\s+|,', c or '') if p.strip()]
    return ', '.join(_lugar(p) for p in partes)


def _secao(icone, titulo, linhas):
    linhas = [x for x in linhas if x]
    return (f'{icone} *{titulo.upper()}*\n' + '\n'.join(linhas)) if linhas else ''


def _b(texto):
    return ('• ' + texto) if texto else ''


def _cabecalho(nome, doc_rotulo, doc, resumo, fantasia=''):
    ls = ['*FICHA CADASTRAL*', _TRACO, '*' + nome.upper() + '*']
    if fantasia:
        ls.append('_' + fantasia + '_')
    if doc:
        ls.append(f'{doc_rotulo} {doc}')
    if resumo:
        ls.append(resumo)
    return '\n'.join(ls)


def _rodape():
    return _TRACO + '\n_Qualicontax Assessoria Contábil_\n_Ficha emitida em ' + _hoje().strftime('%d/%m/%Y') + '_'


def _meios_da_propria(c):
    """Celulares, telefones e e-mails da própria pessoa: os do cadastro + os do contato com o nome dela, sem repetir."""
    from models.contato_cliente import ContatoCliente, _nome_chave
    cel, tel, em = [], [], []

    def add(lst, v, fmt=lambda x: x):
        v = (v or '').strip()
        if v and fmt(v) not in [fmt(x) for x in lst]:
            lst.append(v)
    add(cel, c.get('celular'), _DIG); add(tel, c.get('telefone'), _DIG); add(em, (c.get('email') or '').lower())
    chave = _nome_chave(c.get('nome_razao_social'))
    for ct in ContatoCliente.get_by_cliente(c['id']) or []:
        if not ct.get('ativo', 1) or _nome_chave(ct.get('nome')) != chave:
            continue
        for v in [ct.get('celular')] + list(ct.get('celulares_extra') or []):
            add(cel, v, _DIG)
        add(tel, ct.get('telefone'), _DIG)
        for v in [ct.get('email')] + list(ct.get('emails_extra') or []):
            add(em, (v or '').lower())
    return cel, tel, em


def _secao_contato(cel, tel, em, outros=()):
    """📞 CONTATO: os meios da pessoa/empresa e, abaixo, as pessoas de contato (empresa)."""
    ls = []
    if cel:
        ls.append(_b(('Celular: ' if len(cel) == 1 else 'Celulares: ') + ' · '.join(_tel(x) for x in cel)))
    if tel:
        ls.append(_b(('Telefone: ' if len(tel) == 1 else 'Telefones: ') + ' · '.join(_tel(x) for x in tel)))
    if em:
        ls.append(_b(('E-mail: ' if len(em) == 1 else 'E-mails: ') + ', '.join(em)))
    for ct in outros:
        meios = [_tel(x) for x in [ct.get('celular')] + list(ct.get('celulares_extra') or []) if x]
        if ct.get('telefone'):
            meios.append(_tel(ct['telefone']))
        meios += [x.strip().lower() for x in [ct.get('email')] + list(ct.get('emails_extra') or []) if x]
        ls.append(_b('*' + nome_proprio(ct.get('nome')) + '*' + (' (' + nome_proprio(ct['cargo']) + ')' if ct.get('cargo') else '')
                     + (': ' + ' · '.join(meios) if meios else '')))
    return _secao('📞', 'Contato', ls)


def _pessoas_de_contato(cliente_id, pular_nome=None):
    from models.contato_cliente import ContatoCliente, _nome_chave
    out = []
    for ct in ContatoCliente.get_by_cliente(cliente_id) or []:
        if not ct.get('ativo', 1):
            continue
        if pular_nome and _nome_chave(ct.get('nome')) == _nome_chave(pular_nome):
            continue
        out.append(ct)
    return out


def _docs_linhas(docs):
    hoje = _hoje()
    out = []
    for d in docs:
        orgao = '/'.join(x for x in (d.get('orgao'), d.get('uf')) if x)
        s = f"{d['tipo']} {d['numero']}" + (' ' + orgao if orgao else '')
        if d.get('validade'):
            s += (' ⚠️ vencida em ' if d['validade'] < hoje else ' (válida até ') + _data(d['validade']) + ('' if d['validade'] < hoje else ')')
        out.append(_b(s))
    return out


def _civil_frase(p, sexo):
    if not p.get('estado_civil'):
        return ''
    if p['estado_civil'] == 'UNIAO_ESTAVEL':
        ec = 'união estável'
    else:
        ec = _flex(_rot('estado_civil', p['estado_civil']), sexo)
    if p['estado_civil'] in COM_REGIME and p.get('regime_bens'):
        ec += ', ' + _flex(_rot('regime_bens', p['regime_bens']), sexo).lower()
    return ec


def _linhas_pessoa(c, p, docs, resumo=False):
    """Os dados pessoais em frases curtas. ``resumo`` = sócio dentro da ficha da empresa."""
    sexo = p.get('sexo') or ''
    idade = _idade(p.get('data_nascimento'))
    ls = []
    if p.get('data_nascimento'):
        nat = (', em ' + _lugar(p['naturalidade_cidade']) + ('/' + p['naturalidade_uf'] if p.get('naturalidade_uf') else '')) if p.get('naturalidade_cidade') else ''
        ls.append(_b(f"Nascimento: {_data(p['data_nascimento'])}{nat}" + (f" ({idade} anos)" if idade is not None else '')))
    elif p.get('naturalidade_cidade'):
        ls.append(_b('Natural de ' + _lugar(p['naturalidade_cidade']) + ('/' + p['naturalidade_uf'] if p.get('naturalidade_uf') else '')))
    if p.get('nacionalidade'):
        ls.append(_b('Nacionalidade: ' + _flex(p['nacionalidade'], sexo)))
    if not resumo:
        pais = [nome_proprio(x) for x in (p.get('nome_pai'), p.get('nome_mae')) if x]
        if pais:
            ls.append(_b('Filiação: ' + ' e '.join(pais)))
    civ = _civil_frase(p, sexo)
    if civ:
        ls.append(_b('Estado civil: ' + civ))
    if not resumo and (p.get('conjuge_nome') or p.get('conjuge_cpf')):
        ls.append(_b('Cônjuge: ' + nome_proprio(p.get('conjuge_nome') or '') + (f" (CPF {_cpf(p['conjuge_cpf'])})" if p.get('conjuge_cpf') else '')))
    if p.get('profissao'):
        ls.append(_b('Profissão: ' + _flex(p['profissao'], sexo)))
    if not resumo and p.get('pis_nit'):
        ls.append(_b('PIS/NIT: ' + p['pis_nit']))
    return ls


def _resumo_pessoa(p, sexo):
    idade = _idade(p.get('data_nascimento'))
    partes = [_flex(p['profissao'], sexo).capitalize() if p.get('profissao') else '',
              f'{idade} anos' if idade is not None else '',
              (_flex(_rot('estado_civil', p['estado_civil']), sexo) if p['estado_civil'] != 'UNIAO_ESTAVEL' else 'união estável') if p.get('estado_civil') else '']
    return ' · '.join(x for x in partes if x)


def _atividade_curta(e):
    d = (e.get('cnae_fiscal_descricao') or '').strip()
    if not d:
        return ''
    d = d.split(';')[0].strip()
    d = re.sub(r'\s+não especificad[oa]s? anteriormente$', '', d, flags=re.I)
    if len(d) > 75:
        d = d[:72].rsplit(' ', 1)[0] + '…'
    return d[0].upper() + d[1:]


def _fantasia_ok(e):
    f = (e.get('nome_fantasia') or '').strip()
    return bool(re.search(r'[A-Za-zÀ-ÿ]', f)) and nome_proprio(f).lower() != nome_proprio(e.get('nome_razao_social') or '').lower()


def _fantasia(e):
    """'MA PECAS E EQUIPAMENTOS-Matriz' → 'MA Pecas e Equipamentos-Matriz' (texto quase todo em caixa alta desce)."""
    f = ' '.join((e.get('nome_fantasia') or '').split())
    letras = [ch for ch in f if ch.isalpha()]
    if letras and sum(ch.isupper() for ch in letras) > 0.6 * len(letras):
        return nome_proprio(f.upper())
    return f


def _participacao_frase(s, sexo, quadro):
    """'Sócio único (100%) e administrador' / 'Sócio com 49% do capital (R$ 4.900,00 de R$ 10.000,00)'."""
    pct = s.get('percentual_participacao')
    socio = _g(sexo, 'Sócio', 'Sócia', 'Sócio(a)')
    adm = _g(sexo, 'administrador', 'administradora', 'administrador(a)')
    papel = (s.get('qualificacao') or '').strip().lower()
    if 'titular' in papel:
        socio = 'Titular'
    if pct is not None and float(pct) >= 100:
        txt = f'{socio} {_g(sexo, "único", "única")} (100%)' if socio != 'Titular' else 'Titular (100%)'
    elif pct is not None:
        txt = f'{socio} com {_pct(pct)} do capital'
    else:
        txt = socio
    if s.get('responsavel') or 'admin' in papel:
        txt += f' e {adm}'
    cap = s.get('capital_valor')
    total = sum(float(x.get('capital_valor') or 0) for x in quadro)
    if cap is not None and total and float(cap) < total:
        txt += f' ({_moeda(cap)} de {_moeda(total)})'
    elif cap is not None and float(cap) > 0:
        txt += f' · capital {_moeda(cap)}'
    if s.get('data_entrada'):
        txt += ' · desde ' + _data(s['data_entrada'])
    if s.get('data_saida'):
        txt += ' · saiu em ' + _data(s['data_saida'])
    return txt


def _bloco_empresa(n, cid, s, sexo):
    """A empresa em que a pessoa é sócia: identificação, atividade, sede, contato, a participação e os demais sócios."""
    from models.socio_cliente import SocioCliente
    e = _cliente(cid)
    if not e:
        return ''
    quadro = SocioCliente.get_by_cliente(cid) or []
    ls = [f'*{n}. {nome_proprio(e["nome_razao_social"])}*']
    if _fantasia_ok(e):
        ls.append('_' + _fantasia(e) + '_')
    ls.append(' · '.join(x for x in ('CNPJ ' + _cnpj(e['cpf_cnpj']) if e.get('cpf_cnpj') else '', e.get('porte_empresa') or '',
                                     _REGIME.get((e.get('regime_tributario') or '').upper(), (e.get('regime_tributario') or '').replace('_', ' ').title())) if x))
    nat = (e.get('natureza_juridica') or '').strip()
    inicio = ('desde ' + _data(e['data_inicio_atividade'])) if e.get('data_inicio_atividade') else ''
    ls.append(' · '.join(x for x in (nat, inicio) if x))
    at = _atividade_curta(e)
    if at:
        ls.append(at)
    if e.get('nire'):
        ls.append('NIRE ' + str(e['nire']))
    if (e.get('situacao') or '').upper() not in ('', 'ATIVO'):
        ls.append('Situação: ' + (e.get('situacao') or '').capitalize())
    ends = _enderecos(cid)
    el = _end_linhas(ends[0]) if ends else []
    if el:
        ls.append('📍 ' + el[0] + ('\n' + ' · '.join(el[1:]) if len(el) > 1 else ''))
    meios = [('☎️ ' + _tel(e['telefone'])) if e.get('telefone') else '', ('📱 ' + _tel(e['celular'])) if e.get('celular') and _DIG(e.get('celular')) != _DIG(e.get('telefone')) else '',
             ('✉️ ' + e['email'].strip().lower()) if e.get('email') else '']
    if any(meios):
        ls.append(' · '.join(x for x in meios if x))
    ls.append('▸ ' + _participacao_frase(s, sexo, quadro))
    outros = [x for x in quadro if _DIG(x.get('cpf')) != _DIG(s.get('_cpf_pessoa'))]
    if outros:
        ls.append('▸ ' + ('Demais sócios: ' if len(outros) > 1 else 'Outro sócio: ')
                  + '; '.join(nome_proprio(x.get('nome')) + (f" ({_pct(x['percentual_participacao'])}" + (', administrador' if x.get('responsavel') else '') + ')' if x.get('percentual_participacao') is not None else (' (administrador)' if x.get('responsavel') else ''))
                              for x in outros))
    return '\n'.join(x for x in ls if x)


def _ficha_pf(c):
    p = PessoaDados.get(c['id'])
    docs = Documentos.listar(c['id'])
    sexo = p.get('sexo') or ''
    cab = _cabecalho(nome_proprio(c['nome_razao_social']), 'CPF', _cpf(c['cpf_cnpj']) if c.get('cpf_cnpj') else '',
                     _resumo_pessoa(p, sexo), nome_proprio(p['nome_social']) if p.get('nome_social') else '')
    blocos = [cab, _secao('👤', 'Dados pessoais', _linhas_pessoa(c, p, docs)), _secao('🪪', 'Documentos', _docs_linhas(docs))]
    ends = _enderecos(c['id'])
    for e in ends:
        el = _end_linhas(e)
        if el:
            tit = 'Endereço ' + (e.get('tipo') or '').lower() + (' (principal)' if e.get('principal') and len(ends) > 1 else '')
            blocos.append(_secao('📍', tit, el))
    cel, tel, em = _meios_da_propria(c)
    blocos.append(_secao_contato(cel, tel, em, _pessoas_de_contato(c['id'], pular_nome=c.get('nome_razao_social'))))
    socs = execute_query(
        """SELECT s.cliente_id, s.qualificacao, s.percentual_participacao, s.responsavel, s.data_entrada, s.data_saida, s.ativo, s.capital_valor
             FROM socios_clientes s JOIN clientes c ON c.id = s.cliente_id
            WHERE s.pf_cliente_id = %s ORDER BY s.ativo DESC, s.data_entrada, c.nome_razao_social""", (c['id'],), fetch=True) or []
    for s in socs:
        s['_cpf_pessoa'] = c.get('cpf_cnpj')
    atuais = [s for s in socs if s.get('ativo')]
    if atuais:
        n = len(atuais)
        tit = f"Empresas ({_g(sexo, 'sócio', 'sócia', 'sócio(a)')} em {n})" if n > 1 else 'Empresa'
        blocos.append(_secao('🏢', tit, []) or f'🏢 *{tit.upper()}*')
        for i, s in enumerate(atuais, 1):
            blocos.append(_bloco_empresa(i, s['cliente_id'], s, sexo))
    antigas = [s for s in socs if not s.get('ativo')]
    if antigas:
        blocos.append(_secao('🕓', 'Sociedades anteriores', [_b(nome_proprio((_cliente(s['cliente_id']) or {}).get('nome_razao_social')) + ' — ' + _participacao_frase(s, sexo, [])) for s in antigas]))
    blocos.append(_rodape())
    return '\n\n'.join(b for b in blocos if b)


def _ficha_pj(c):
    from models.socio_cliente import SocioCliente
    regime = _REGIME.get((c.get('regime_tributario') or '').upper(), (c.get('regime_tributario') or '').replace('_', ' ').title())
    resumo = ' · '.join(x for x in ((c.get('natureza_juridica') or ''), c.get('porte_empresa') or '', regime) if x)
    cab = _cabecalho(nome_proprio(c['nome_razao_social']), 'CNPJ', _cnpj(c['cpf_cnpj']) if c.get('cpf_cnpj') else '', resumo,
                     _fantasia(c) if _fantasia_ok(c) else '')
    emp = [
        _b('Atividade principal: ' + _atividade_curta(c)) if c.get('cnae_fiscal_descricao') else '',
        _b('Início das atividades: ' + _data(c['data_inicio_atividade'])) if c.get('data_inicio_atividade') else '',
        _b('NIRE ' + str(c['nire']) + (f", registrado em {_data(c['nire_data_registro'])}" if c.get('nire_data_registro') else '')) if c.get('nire') else '',
        _b('Inscrição municipal: ' + str(c['inscricao_municipal'])) if c.get('inscricao_municipal') else '',
        _b('Situação: ' + (c.get('situacao') or '').capitalize()) if (c.get('situacao') or '').upper() not in ('', 'ATIVO') else '',
    ]
    ies = execute_query("SELECT uf, ie, tipo FROM clientes_inscricoes_estaduais WHERE cliente_id = %s ORDER BY principal DESC, uf", (c['id'],), fetch=True) or []
    for ie in ies:
        emp.append(_b(f"Inscrição estadual {ie.get('uf') or ''}: {ie.get('ie') or ''}" + (f" ({ie['tipo'].lower()})" if ie.get('tipo') else '')))
    if not ies and c.get('inscricao_estadual'):
        emp.append(_b('Inscrição estadual: ' + str(c['inscricao_estadual'])))
    blocos = [cab, _secao('🏢', 'Empresa', emp)]
    ends = _enderecos(c['id'])
    for e in ends:
        el = _end_linhas(e)
        if el:
            tit = ('Sede' if (e.get('tipo') or '').upper() in ('COMERCIAL', 'SEDE', '') else 'Endereço ' + (e.get('tipo') or '').lower()) + (' (principal)' if e.get('principal') and len(ends) > 1 else '')
            blocos.append(_secao('📍', tit, el))
    cel = [c['celular']] if c.get('celular') else []
    tel = [c['telefone']] if c.get('telefone') and _DIG(c['telefone']) not in [_DIG(x) for x in cel] else []
    em = [c['email'].strip().lower()] if c.get('email') else []
    blocos.append(_secao_contato(cel, tel, em, _pessoas_de_contato(c['id'])))
    quadro = SocioCliente.get_by_cliente(c['id']) or []
    if quadro:
        total = sum(float(s.get('capital_valor') or 0) for s in quadro)
        blocos.append(f"👥 *QUADRO SOCIETÁRIO*" + (f"\nCapital social {_moeda(total)}" if total else ''))
        for i, s in enumerate(quadro, 1):
            pfid = s.get('pf_cliente_id')
            sexo, ls = '', []
            if pfid:
                pc, p, docs = _cliente(pfid), PessoaDados.get(pfid), Documentos.listar(pfid)
                sexo = p.get('sexo') or ''
                if pc:
                    ls += _linhas_pessoa(pc, p, docs, resumo=True)
                    if docs:
                        ls.append(_b('Documento: ' + ' | '.join(x[2:] for x in _docs_linhas(docs))))
                    pe = _enderecos(pfid)
                    el = _end_linhas(pe[0]) if pe else []
                    if el:
                        ls.append(_b('Endereço: ' + el[0] + (' – ' + ' · '.join(el[1:]) if len(el) > 1 else '')))
                    cel2, tel2, em2 = _meios_da_propria(pc)
                    meios = [_tel(x) for x in cel2] + [_tel(x) for x in tel2 if _DIG(x) not in [_DIG(y) for y in cel2]] + em2
                    if meios:
                        ls.append(_b('Contato: ' + ' · '.join(meios)))
            else:
                meios = [x for x in (_tel(s.get('telefone')), (s.get('email') or '').lower()) if x]
                if meios:
                    ls.append(_b('Contato: ' + ' · '.join(meios)))
            doc = s.get('cpf')
            cab_s = f"*{i}. {nome_proprio(s.get('nome'))}*" + (f"\n{'CNPJ' if len(_DIG(doc)) == 14 else 'CPF'} {_doc_fmt(doc)}" if doc else '')
            blocos.append(cab_s + '\n▸ ' + _participacao_frase(s, sexo, quadro) + (('\n' + '\n'.join(ls)) if ls else ''))
    blocos.append(_rodape())
    return '\n\n'.join(b for b in blocos if b)


def ficha(cliente_id):
    """A ficha cadastral inteira em texto de mensagem, pronta para o WhatsApp ou e-mail."""
    c = _cliente(cliente_id)
    if not c:
        return ''
    return _ficha_pj(c) if c['tipo_pessoa'] == 'PJ' else _ficha_pf(c)
