"""Qualificação da pessoa por extenso (20/10/2026) — o parágrafo que abre o
contrato social e as alterações: "FULANO, brasileiro(a), casado(a) sob o
regime de comunhão parcial de bens, empresário(a), nascido(a) em ...,
portador(a) do RG nº ..., inscrito(a) no CPF sob o nº ..., residente e
domiciliado(a) na ...".

Sai do cadastro (clientes, clientes_pf_dados, clientes_pf_documentos e o
endereço principal). O que falta no cadastro fica de fora do texto e volta
na lista `faltam`, para a tela avisar antes de alguém colar no contrato.
"""
from models.endereco_cliente import EnderecoCliente
from models.pessoa_dados import PessoaDados, Documentos, COM_REGIME
from utils.db_helper import execute_query


def _min1(t):
    """'Casado(a)' -> 'casado(a)'; sigla toda em maiúscula fica como está."""
    t = (t or '').strip()
    return t if not t or t.isupper() else t[0].lower() + t[1:]


def _endereco(cliente_id):
    ends = EnderecoCliente.get_by_cliente(cliente_id) or []
    e = next((x for x in ends if x.get('principal')), ends[0] if ends else None)
    if not e or not e.get('logradouro'):
        return ''
    num = str(e.get('numero') or '').strip()
    num = 's/nº' if num.upper().replace('/', '').replace('.', '') in ('SN', 'SNº', 'SNO', '') else 'nº ' + num
    partes = [e['logradouro'] + ', ' + num]
    partes += [e.get('complemento'), e.get('bairro'),
               (e.get('cidade') or '') + ('/' + e['estado'] if e.get('estado') else '')]
    if e.get('cep'):
        partes.append('CEP ' + e['cep'])
    return ', '.join(p for p in partes if p and p.strip())


def qualificacao(cliente_id):
    """(texto, faltam) do cadastro; ('', []) se o cadastro não existe."""
    c = execute_query("SELECT nome_razao_social, cpf_cnpj, tipo_pessoa FROM clientes WHERE id = %s",
                      (cliente_id,), fetch=True, fetch_one=True)
    if not c:
        return '', []
    nome = (c['nome_razao_social'] or '').strip()
    doc = (c['cpf_cnpj'] or '').strip()
    end = _endereco(cliente_id)
    faltam = []
    if c['tipo_pessoa'] == 'PJ':
        partes = [nome, 'pessoa jurídica de direito privado']
        partes.append('inscrita no CNPJ sob o nº ' + doc) if doc else faltam.append('CNPJ')
        partes.append('com sede na ' + end) if end else faltam.append('endereço')
        return ', '.join(partes), faltam

    p = PessoaDados.get(cliente_id)
    docs = Documentos.listar(cliente_id)
    partes = [nome]
    if p.get('nacionalidade'):
        partes.append(_min1(p['nacionalidade']))
    else:
        faltam.append('nacionalidade')
    if p.get('estado_civil'):
        ec = _min1(PessoaDados.rotulo('estado_civil', p['estado_civil']))
        if p['estado_civil'] in COM_REGIME:
            if p.get('regime_bens'):
                ec += ' sob o regime de ' + _min1(PessoaDados.rotulo('regime_bens', p['regime_bens']))
            else:
                faltam.append('regime de bens')
        partes.append(ec)
    else:
        faltam.append('estado civil')
    if p.get('profissao'):
        partes.append(_min1(p['profissao']))
    else:
        faltam.append('profissão')
    if p.get('data_nascimento'):
        partes.append('nascido(a) em ' + p['data_nascimento'].strftime('%d/%m/%Y'))
    else:
        faltam.append('nascimento')
    if docs:
        d = docs[0]                       # o marcado "contrato" vem primeiro
        orgao = '/'.join(x for x in (d.get('orgao'), d.get('uf')) if x)
        partes.append('portador(a) do ' + d['tipo'] + ' nº ' + d['numero'] + (' ' + orgao if orgao else ''))
    else:
        faltam.append('documento de identidade')
    partes.append('inscrito(a) no CPF sob o nº ' + doc) if doc else faltam.append('CPF')
    partes.append('residente e domiciliado(a) na ' + end) if end else faltam.append('endereço')
    return ', '.join(partes), faltam
