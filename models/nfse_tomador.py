# -*- coding: utf-8 -*-
"""Tomadores dos CLIENTES — para quem as empresas dos clientes emitem nota (08/11/2026).

Cada empresa emitente tem a sua lista (``nfse_tomadores.empresa_id`` = clientes.id de quem
emite). O mesmo CNPJ pode estar em várias empresas: cada uma guarda o seu e-mail/endereço.

Preencher um tomador novo, nesta ordem:
1. já está na lista desta empresa → usa;
2. é cliente do escritório → copia do nosso cadastro (o leitor é ``tomador_do_cliente``);
3. CNPJ → base da Receita (``utils/cnpj_receita.consultar``) + IBGE pelo CEP.
CPF não tem consulta pública: digita.

``como_tomador`` devolve o MESMO formato de ``tomador_do_cliente`` (com ``faltas``), para a
nota usar as mesmas regras de endereço. ``pedido_do_fiscal`` monta o ``Pedido`` do motor
único (``utils/nfse_emissor``); o envio é o mesmo do Financeiro.
"""
import re
from datetime import date
from decimal import Decimal

from utils.db_helper import execute_query

CAMPOS = ('nome', 'nome_fantasia', 'inscricao_municipal', 'email', 'fone', 'cep', 'logradouro', 'numero',
          'complemento', 'bairro', 'cidade', 'uf', 'municipio_ibge')
_TAM = {'nome': 150, 'nome_fantasia': 150, 'inscricao_municipal': 20, 'email': 255, 'fone': 20, 'cep': 8,
        'logradouro': 255, 'numero': 60, 'complemento': 156, 'bairro': 60, 'cidade': 80, 'uf': 2,
        'municipio_ibge': 7}
_DIGITOS = {'fone', 'cep', 'municipio_ibge'}
_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _dig(v):
    return re.sub(r'\D', '', str(v or ''))


def documento_valido(doc):
    """Dígitos verificadores de CPF (11) ou CNPJ (14)."""
    d = _dig(doc)
    if len(d) == 11:
        if d == d[0] * 11:
            return False
        for n in (9, 10):
            s = sum(int(d[i]) * (n + 1 - i) for i in range(n))
            if (s * 10 % 11) % 10 != int(d[n]):
                return False
        return True
    if len(d) == 14:
        if d == d[0] * 14:
            return False
        for n in (12, 13):
            pesos = list(range(n - 7, 1, -1)) + list(range(9, 1, -1))
            s = sum(int(d[i]) * pesos[i] for i in range(n))
            r = s % 11
            if (0 if r < 2 else 11 - r) != int(d[n]):
                return False
        return True
    return False


class NfseTomador:

    @staticmethod
    def listar(empresa_id, busca='', limite=300, so_ativos=True):
        cond, params = ['empresa_id = %s'], [empresa_id]
        if so_ativos:
            cond.append('ativo = 1')
        b = (busca or '').strip()
        if b:
            like = f'%{b}%'
            sub = '(nome LIKE %s OR nome_fantasia LIKE %s OR email LIKE %s'
            params += [like, like, like]
            if len(_dig(b)) >= 3:
                sub += ' OR documento LIKE %s'
                params.append(f'%{_dig(b)}%')
            cond.append(sub + ')')
        return execute_query(
            f"SELECT * FROM nfse_tomadores WHERE {' AND '.join(cond)} "
            " ORDER BY (ultima_nota_em IS NULL), ultima_nota_em DESC, nome LIMIT %s",
            tuple(params) + (int(limite),), fetch=True) or []

    @staticmethod
    def get(tomador_id, empresa_id):
        return execute_query('SELECT * FROM nfse_tomadores WHERE id = %s AND empresa_id = %s',
                             (tomador_id, empresa_id), fetch=True, fetch_one=True)

    @staticmethod
    def por_documento(empresa_id, doc):
        return execute_query('SELECT * FROM nfse_tomadores WHERE empresa_id = %s AND documento = %s',
                             (empresa_id, _dig(doc)), fetch=True, fetch_one=True)

    @staticmethod
    def contar(empresa_id):
        r = execute_query('SELECT COUNT(*) n FROM nfse_tomadores WHERE empresa_id = %s AND ativo = 1',
                          (empresa_id,), fetch=True, fetch_one=True)
        return (r or {}).get('n') or 0

    @staticmethod
    def limpar(dados):
        """(dict limpo, erro). Documento e nome obrigatórios; e-mail, se vier, tem que ser e-mail."""
        doc = _dig(dados.get('documento'))
        if not documento_valido(doc):
            return None, 'CPF/CNPJ do tomador inválido.'
        out = {'documento': doc}
        for k in CAMPOS:
            v = dados.get(k)
            v = (v.strip() if isinstance(v, str) else (v or ''))
            if k in _DIGITOS:
                v = _dig(v)
            if k == 'uf':
                v = v.upper()
            if k == 'email':
                v = v.lower()
            out[k] = str(v)[:_TAM[k]] or None
        if not out.get('nome'):
            return None, 'Informe o nome (razão social) do tomador.'
        if out.get('email') and not _EMAIL.match(out['email']):
            return None, f'E-mail do tomador inválido: {out["email"]}'
        if out.get('municipio_ibge') and len(out['municipio_ibge']) != 7:
            out['municipio_ibge'] = None
        return out, None

    @staticmethod
    def salvar(empresa_id, dados, usuario_id=None, fonte='manual', tomador_id=None):
        """Cria ou atualiza (pelo id, senão pelo documento dentro da empresa). (id, erro)."""
        limpos, erro = NfseTomador.limpar(dados)
        if erro:
            return None, erro
        atual = NfseTomador.get(tomador_id, empresa_id) if tomador_id else None
        if tomador_id and not atual:
            return None, 'Tomador não encontrado nesta empresa.'
        mesmo_doc = NfseTomador.por_documento(empresa_id, limpos['documento'])
        if atual and mesmo_doc and mesmo_doc['id'] != atual['id']:
            return None, 'Já existe outro tomador com esse CPF/CNPJ nesta empresa.'
        atual = atual or mesmo_doc
        if atual:
            sets = ', '.join(f'{k} = %s' for k in ('documento',) + CAMPOS)
            r = execute_query(f'UPDATE nfse_tomadores SET {sets}, ativo = 1, alterado_por = %s, atualizado_em = NOW() '
                              ' WHERE id = %s AND empresa_id = %s',
                              tuple(limpos[k] for k in ('documento',) + CAMPOS) + (usuario_id, atual['id'], empresa_id))
            return (atual['id'], None) if r is not None else (None, 'Não foi possível salvar o tomador.')
        cols = ('empresa_id', 'documento') + CAMPOS + ('fonte', 'criado_por', 'alterado_por')
        vals = (empresa_id, limpos['documento']) + tuple(limpos[k] for k in CAMPOS) + (fonte[:20], usuario_id, usuario_id)
        r = execute_query(f"INSERT INTO nfse_tomadores ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", vals)
        return (r, None) if r else (None, 'Não foi possível salvar o tomador.')

    @staticmethod
    def alternar(tomador_id, empresa_id):
        return execute_query('UPDATE nfse_tomadores SET ativo = NOT ativo, atualizado_em = NOW() '
                             ' WHERE id = %s AND empresa_id = %s', (tomador_id, empresa_id))

    @staticmethod
    def marcar_nota(tomador_id):
        execute_query('UPDATE nfse_tomadores SET ultima_nota_em = NOW() WHERE id = %s', (tomador_id,))


def como_tomador(row):
    """A linha de nfse_tomadores no formato de ``tomador_do_cliente`` (com ``faltas``)."""
    from models.nfse_emissao import faltas_do_tomador, formatar_documento
    doc = _dig(row.get('documento'))
    t = {
        'tomador_id': row.get('id'), 'cliente_id': None,
        'tipo_pessoa': 'PF' if len(doc) == 11 else 'PJ',
        'documento': doc, 'doc_fmt': formatar_documento(doc), 'nome': row.get('nome') or '',
        'nome_fantasia': row.get('nome_fantasia') or '',
        'ind_final': 1 if len(doc) == 11 else 0,
        'inscricao_municipal': row.get('inscricao_municipal') or '',
        'email': row.get('email') or '', 'fone': _dig(row.get('fone')),
        'cep': _dig(row.get('cep')), 'logradouro': row.get('logradouro') or '', 'numero': row.get('numero') or '',
        'complemento': row.get('complemento') or '', 'bairro': row.get('bairro') or '',
        'cidade': row.get('cidade') or '', 'uf': row.get('uf') or '',
        'ibge': _dig(row.get('municipio_ibge')), 'municipio_ibge': _dig(row.get('municipio_ibge')),
    }
    t['faltas'] = faltas_do_tomador(t)
    return t


def _ibge_do_cep(cep):
    try:
        from utils.cep import consultar
        r = consultar(cep) if len(_dig(cep)) == 8 else None
        return _dig((r or {}).get('ibge'))
    except Exception:
        return ''


def preencher_por_documento(empresa_id, doc):
    """O que a tela recebe ao digitar o CPF/CNPJ: {'origem', 'dados'|'erro'}.
    origem: lista (já é tomador desta empresa) | cadastro (cliente do escritório) | receita | nada."""
    d = _dig(doc)
    if not documento_valido(d):
        return {'origem': 'nada', 'erro': 'CPF/CNPJ inválido (confira os dígitos).'}
    ja = NfseTomador.por_documento(empresa_id, d)
    if ja:
        return {'origem': 'lista', 'dados': como_tomador(ja)}
    c = execute_query(
        "SELECT id FROM clientes WHERE REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '/', ''), '-', '') = %s "
        " ORDER BY (situacao = 'ATIVO') DESC, id LIMIT 1", (d,), fetch=True, fetch_one=True)
    if c:
        from models.nfse_emissao import tomador_do_cliente
        t = tomador_do_cliente(c['id'])
        if t:
            t = dict(t, municipio_ibge=t.get('ibge') or '', nome_fantasia='', tomador_id=None)
            t.pop('cliente_id', None)
            return {'origem': 'cadastro', 'dados': t}
    if len(d) == 11:
        return {'origem': 'nada', 'dados': {'documento': d}, 'aviso': 'CPF não tem consulta pública: preencha os dados.'}
    from utils import cnpj_receita
    try:
        r, _fonte = cnpj_receita.consultar(d, completar=True)
    except cnpj_receita.CnpjNaoEncontrado:
        return {'origem': 'nada', 'dados': {'documento': d}, 'erro': 'CNPJ não encontrado na Receita.'}
    except Exception:
        return {'origem': 'nada', 'dados': {'documento': d},
                'aviso': 'A consulta da Receita não respondeu agora: preencha os dados ou tente de novo.'}
    fone = r.get('ddd_telefone_1') or r.get('ddd_telefone_2') or ''
    dados = {
        'documento': d, 'nome': r.get('razao_social') or '', 'nome_fantasia': r.get('nome_fantasia') or '',
        'email': (r.get('email') or '').lower(), 'fone': fone, 'cep': _dig(r.get('cep')),
        'logradouro': r.get('logradouro') or '', 'numero': r.get('numero') or '',
        'complemento': r.get('complemento') or '', 'bairro': r.get('bairro') or '',
        'cidade': r.get('municipio') or '', 'uf': r.get('uf') or '',
        'municipio_ibge': _ibge_do_cep(r.get('cep')), 'situacao_receita': r.get('situacao_cadastral') or '',
    }
    return {'origem': 'receita', 'dados': dados}


# ---------------------------------------------------------------------------
# O pedido da nota do Fiscal — monta o Pedido do motor ÚNICO (utils/nfse_emissor)
# ---------------------------------------------------------------------------
LOCAIS = {'empresa': 'No município da empresa', 'tomador': 'No município do tomador'}


def pedido_do_fiscal(f, empresa_id, cfg, tomador, series_ativas):
    """``Pedido`` a partir do formulário do Fiscal. O tomador já vem salvo (``como_tomador``).
    Os códigos do serviço e o regime vêm SEMPRE da matriz conferida e liberada; a tela só
    escolhe valor, descrição, competência, local, ISS retido e retenções."""
    from utils.nfse_emissor import Pedido, PedidoInvalido, _dec

    def g(k, padrao=''):
        v = f.get(k)
        return v.strip() if isinstance(v, str) and v.strip() else padrao

    serie = g('serie', str(cfg.get('serie_nacional') or '')).upper()
    if serie not in {str(s['serie']).upper() for s in series_ativas}:
        raise PedidoInvalido('Escolha uma série aberta e ligada desta empresa.')
    try:
        comp = date.fromisoformat(g('competencia') or date.today().isoformat())
    except ValueError:
        raise PedidoInvalido('Competência inválida.')
    if comp > date.today():
        raise PedidoInvalido('A competência não pode ser depois de hoje.')
    obrig = [x['texto'] for x in tomador['faltas'] if x['nivel'] == 'obrigatorio']
    if obrig:
        raise PedidoInvalido('Falta no tomador: ' + ' e '.join(obrig) + '.')
    endereco_ok = not any(x['nivel'] == 'endereco' for x in tomador['faltas'])
    end = (lambda k: (tomador.get(k) or '')) if endereco_ok else (lambda k: '')
    valor = _dec(g('valor_servico'), 'Valor do serviço', Decimal('0.01'))
    descricao = g('descricao')
    if not descricao:
        raise PedidoInvalido('Informe a descrição do serviço.')
    local = g('local', 'empresa')
    loc = _dig(cfg.get('municipio_ibge'))
    if local == 'tomador':
        if len(tomador.get('ibge') or '') != 7:
            raise PedidoInvalido('O município do tomador não está preenchido: complete o endereço ou escolha '
                                 '"no município da empresa".')
        loc = tomador['ibge']
    aliq = None
    if cfg.get('op_simp_nac') == 1 and g('aliquota_iss'):
        aliq = _dec(g('aliquota_iss'), 'Alíquota do ISS', Decimal('0'))
    rets = {k: _dec(g(k), rot, Decimal('0')) for k, rot in
            (('ret_pis', 'PIS retido'), ('ret_cofins', 'COFINS retida'), ('ret_inss', 'INSS retido'),
             ('ret_irrf', 'IR retido'), ('ret_csll', 'CSLL retida'))}
    if sum(rets.values()) >= valor:
        raise PedidoInvalido('As retenções somam mais que o valor da nota.')
    ind_final = g('ind_final', str(tomador.get('ind_final', 0)))
    return Pedido(
        empresa_id=int(empresa_id), serie=serie, competencia=comp,
        tomador_doc=tomador['documento'], tomador_nome=tomador['nome'][:150],
        tomador_ibge=tomador['ibge'] if len(tomador.get('ibge') or '') == 7 else '',
        tomador_cep=tomador['cep'] if endereco_ok else '',
        tomador_logradouro=end('logradouro')[:255], tomador_numero=(end('numero') or 'S/N')[:60],
        tomador_complemento=end('complemento')[:156], tomador_bairro=end('bairro')[:60],
        tomador_uf=(tomador.get('uf') or '')[:2].upper(), tomador_email=(tomador.get('email') or '')[:80],
        tomador_fone=_dig(tomador.get('fone')), tomador_im=_dig(tomador.get('inscricao_municipal')),
        descricao=descricao[:1000],
        cod_trib_nac=_dig(cfg.get('cod_trib_nac')), cod_trib_mun=_dig(cfg.get('cod_trib_mun')),
        cod_nbs=_dig(cfg.get('cod_nbs')), cod_servico_sp='',
        loc_prestacao=loc, valor_servico=valor, trib_issqn=1,
        iss_retido=g('iss_retido') in ('1', 'on', 'true'), aliquota_iss=aliq, **rets,
        ibscbs_cst=_dig(cfg.get('ibscbs_cst') or '000').zfill(3),
        ibscbs_cclasstrib=_dig(cfg.get('ibscbs_cclasstrib') or '000001').zfill(6),
        ibscbs_cind_op=_dig(cfg.get('ibscbs_cind_op') or '100301').zfill(6),
        ibscbs_ind_dest=int(cfg.get('ibscbs_ind_dest') or 0),
        ibscbs_ind_final=1 if str(ind_final) == '1' else 0,
        origem='fiscal',
    )
