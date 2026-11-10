# -*- coding: utf-8 -*-
"""Emissão de NFS-e — o que a tela lê e grava em ``nfse_emissao_config`` e
``nfse_emitidas``.

Regra combinada com o Anderson (31/10/2026): **o cadastro da empresa guarda o
padrão, cada nota pode mudar.** ``NfseConfig`` é o padrão; a tela de emissão
nasce preenchida com ele e o que o usuário alterar vale só para aquela nota.

Nada aqui fala com o Sefin ou com a prefeitura — isso é ``utils/nfse_emissor.py``.
"""
import re
from datetime import date

from utils.db_helper import execute_query

# Campos que a tela de configuração edita (ordem = ordem da tela).
CAMPOS_CONFIG = (
    'emissor', 'serie_nacional', 'serie_sp', 'leiaute_sp',
    'inscricao_municipal', 'municipio_ibge', 'tributacao_sp', 'cod_servico_sp',
    'cod_trib_nac', 'cod_trib_mun', 'cod_nbs',
    'op_simp_nac', 'reg_ap_trib_sn', 'reg_esp_trib', 'aliquota_iss', 'iss_retido',
    'piscofins_cst', 'aliquota_pis', 'aliquota_cofins', 'ws_url',
    'ibscbs_cst', 'ibscbs_cclasstrib', 'ibscbs_cind_op', 'ibscbs_ind_dest', 'ibscbs_ind_final',
    'email_remetente', 'descricao_padrao',
)
_INTEIROS = {'leiaute_sp', 'op_simp_nac', 'reg_ap_trib_sn', 'reg_esp_trib', 'iss_retido',
             'ibscbs_ind_dest', 'ibscbs_ind_final'}

# Rótulos das listas fechadas (os códigos são os do leiaute nacional).
OP_SIMP_NAC = {1: 'Não optante pelo Simples', 2: 'MEI', 3: 'ME ou EPP do Simples Nacional'}
REG_AP_TRIB_SN = {1: 'Regime de apuração do Simples', 2: 'Simples com ISS fora (sublimite)', 3: 'Simples com ISS e ICMS fora'}
REG_ESP_TRIB = {0: 'Nenhum', 1: 'Ato cooperado', 2: 'Estimativa', 3: 'Microempresa municipal',
                4: 'Notário ou registrador', 5: 'Profissional autônomo', 6: 'Sociedade de profissionais'}
TRIB_ISSQN = {1: 'Tributável', 2: 'Imunidade', 3: 'Exportação de serviço', 4: 'Não incidência'}
TP_RET_ISSQN = {1: 'ISS não retido', 2: 'Retido pelo tomador', 3: 'Retido pelo intermediário'}
IND_DEST = {0: 'Destinatário é o tomador', 1: 'Destinatário é outro'}
IND_FINAL = {0: 'Não é consumidor final', 1: 'Consumidor final'}
EMISSORES = {'nacional': 'Emissor Nacional (Sefin)', 'fiorilli': 'ISSWeb da prefeitura (Fiorilli)',
             'sp': 'Prefeitura de São Paulo (RPS)'}
# Recusa do governo em português, para o cartão da nota (09/11/2026, "texto para toda a
# equipe"). Só os códigos que já apareceram nas nossas notas; o resto mostra o texto do governo.
MOTIVOS_RECUSA = {
    'E0037': 'A cidade da empresa não tem convênio com o Emissor Nacional',
    'E0039': 'A cidade da empresa não está preparada para o Emissor Nacional',
    'E0116': 'Falta a inscrição municipal da empresa',
    'E0120': 'A cidade da empresa não aceita a inscrição municipal na nota',
    'E0322': 'Faltou o tipo de serviço (NBS)',
    'E2243': 'O CPF da empresa não pode consultar por este caminho',
}


def motivo_curto(erro):
    """(texto curto em português, código do governo) a partir de ``erro_ultimo``."""
    import re
    erro = (erro or '').strip()
    if not erro:
        return '', ''
    m = re.match(r'\s*([A-Z]{1,2}\d{2,4})\s*[:\-–]\s*(.*)', erro, re.S)
    cod, resto = (m.group(1), m.group(2)) if m else ('', erro)
    texto = MOTIVOS_RECUSA.get(cod) or ' '.join(resto.split())
    return (texto[:140] + '…') if len(texto) > 140 else texto, cod


SITUACOES = {'rascunho': 'Rascunho', 'autorizada': 'Autorizada', 'rejeitada': 'Rejeitada',
             'erro': 'Falha no envio', 'cancelada': 'Cancelada'}


def _dig(v):
    return re.sub(r'\D', '', str(v or ''))


def _mesmo_numero(a, b):
    """2.0000 do banco e '2' do formulário são o mesmo valor (alíquota, inteiros)."""
    try:
        from decimal import Decimal
        return a is not None and b is not None and Decimal(str(a)) == Decimal(str(b))
    except Exception:
        return False


class NfseConfig:

    @staticmethod
    def get(empresa_id):
        return execute_query('SELECT * FROM nfse_emissao_config WHERE empresa_id = %s',
                             (empresa_id,), fetch=True, fetch_one=True)

    @staticmethod
    def salvar(empresa_id, dados, usuario_id=None):
        """Cria ou atualiza a linha da empresa. ``dados`` só com chaves de CAMPOS_CONFIG."""
        limpos = {}
        for k in CAMPOS_CONFIG:
            if k not in dados:
                continue
            v = dados[k]
            v = v.strip() if isinstance(v, str) else v
            if k in _INTEIROS:
                v = int(v) if str(v).strip().lstrip('-').isdigit() else (0 if k in ('iss_retido', 'ibscbs_ind_dest', 'ibscbs_ind_final', 'reg_esp_trib') else None)
            elif k in ('aliquota_iss', 'aliquota_pis', 'aliquota_cofins'):
                v = str(v or '0').replace(',', '.') or '0'
            elif k in ('serie_nacional', 'serie_sp'):
                v = (v or '').upper() or None
            elif v == '':
                v = None
            limpos[k] = v
        if not limpos:
            return False
        atual = NfseConfig.get(empresa_id)
        if atual:
            sets = ', '.join(f'{k} = %s' for k in limpos)
            # Mudou algo numa empresa LIBERADA? A liberação cai: o Fiscal confere de novo
            # (06/11/2026). Vale para quem gravar — Fiscal ou Financeiro — porque a regra
            # mora aqui, no único escritor da matriz.
            # Vazio e zero são o mesmo valor (08/11/2026): o formulário manda '' e a gravação guarda 0 —
            # sem isso a primeira gravação dos cartões derrubaria a liberação sem nada ter mudado.
            def _vz(x):
                return x is None or (str(x).strip() in ('', '0') or _mesmo_numero(x, 0))
            revoga = bool(atual.get('liberado_em')) and any(
                str(atual.get(k) if atual.get(k) is not None else '') != str(v if v is not None else '')
                and not _mesmo_numero(atual.get(k), v) and not (_vz(atual.get(k)) and _vz(v))
                for k, v in limpos.items())
            if revoga:
                sets += ', liberado_em = NULL, liberado_por = NULL'
            execute_query(f'UPDATE nfse_emissao_config SET {sets}, atualizado_em = NOW(), alterado_por = %s '
                          ' WHERE empresa_id = %s', tuple(limpos.values()) + (usuario_id, empresa_id))
            return revoga
        else:
            cols = ', '.join(limpos) + ', empresa_id, alterado_por'
            marks = ', '.join(['%s'] * (len(limpos) + 2))
            execute_query(f'INSERT INTO nfse_emissao_config ({cols}) VALUES ({marks})',
                          tuple(limpos.values()) + (empresa_id, usuario_id))
            return False

    @staticmethod
    def descricao_para(cfg, competencia: date):
        """``descricao_padrao`` com {mes} e {ano} preenchidos para a competência."""
        return preencher_modelo((cfg or {}).get('descricao_padrao') or 'Serviços prestados em {mes} de {ano}',
                                competencia)


MESES = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto',
         'setembro', 'outubro', 'novembro', 'dezembro')


def preencher_modelo(modelo, competencia: date):
    """{mes}, {ano} e {competencia} (mm/aaaa) preenchidos — o mesmo que a tela faz em JS."""
    return ((modelo or '').replace('{mes}', MESES[competencia.month - 1])
            .replace('{ano}', str(competencia.year))
            .replace('{competencia}', f'{competencia.month:02d}/{competencia.year}'))


class NfseServico:
    """Catálogo de serviços da empresa (``nfse_servicos``, 02/11/2026): "Honorários
    contábeis", "Balanço", "Abertura de empresa"... A nota escolhe um; o nome vai na lista,
    a descrição vira o texto da nota e códigos/valor cobrem o padrão quando preenchidos."""

    CAMPOS = ('nome', 'descricao', 'cod_trib_nac', 'cod_trib_mun', 'cod_nbs', 'cod_servico_sp',
              'valor_padrao', 'ordem', 'emite_nota')

    @staticmethod
    def listar(empresa_id, so_ativos=False):
        extra = ' AND ativo = 1' if so_ativos else ''
        return execute_query(
            f'SELECT * FROM nfse_servicos WHERE empresa_id = %s{extra} ORDER BY ativo DESC, ordem, nome',
            (empresa_id,), fetch=True) or []

    @staticmethod
    def para_tela(servicos):
        """Lista serializável para o JS da nota (Decimal vira texto)."""
        return [{'id': s['id'], 'nome': s['nome'], 'descricao': s['descricao'] or '',
                 'cod_trib_nac': s.get('cod_trib_nac') or '', 'cod_trib_mun': s.get('cod_trib_mun') or '',
                 'cod_nbs': s.get('cod_nbs') or '', 'cod_servico_sp': s.get('cod_servico_sp') or '',
                 'valor_padrao': (f"{s['valor_padrao']:.2f}" if s.get('valor_padrao') else ''),
                 'emite_nota': 0 if str(s.get('emite_nota', 1)) in ('0', 'False') else 1}
                for s in servicos]

    @staticmethod
    def salvar(empresa_id, dados, servico_id=None, usuario_id=None):
        """Cria (servico_id None) ou altera. Devolve (id, erro)."""
        nome = (dados.get('nome') or '').strip()[:80]
        descricao = (dados.get('descricao') or '').strip()[:1000]
        if not nome or not descricao:
            return None, 'Nome e descrição são obrigatórios.'
        valor = (dados.get('valor_padrao') or '').strip()
        if valor:
            try:
                from decimal import Decimal
                valor = Decimal(valor.replace('.', '').replace(',', '.')) if ',' in valor else Decimal(valor)
            except Exception:
                return None, 'Valor padrão inválido.'
            if valor <= 0:
                valor = None
        else:
            valor = None
        ordem = dados.get('ordem') or '0'
        emite = 0 if str(dados.get('emite_nota', '1')).strip() in ('0', 'false', 'False', 'nao', 'não') else 1
        vals = (nome, descricao, _dig(dados.get('cod_trib_nac'))[:6] or None, _dig(dados.get('cod_trib_mun'))[:3] or None,
                _dig(dados.get('cod_nbs'))[:9] or None, _dig(dados.get('cod_servico_sp'))[:5] or None,
                valor, int(ordem) if str(ordem).lstrip('-').isdigit() else 0, emite)
        dup = execute_query('SELECT id FROM nfse_servicos WHERE empresa_id = %s AND nome = %s AND id <> %s',
                            (empresa_id, nome, servico_id or 0), fetch=True, fetch_one=True)
        if dup:
            return None, f'Já existe um serviço chamado "{nome}" nesta empresa.'
        if servico_id:
            r = execute_query(
                'UPDATE nfse_servicos SET nome = %s, descricao = %s, cod_trib_nac = %s, cod_trib_mun = %s, '
                '  cod_nbs = %s, cod_servico_sp = %s, valor_padrao = %s, ordem = %s, emite_nota = %s, '
                '  atualizado_em = NOW(), alterado_por = %s WHERE id = %s AND empresa_id = %s',
                vals + (usuario_id, servico_id, empresa_id))
            return (servico_id, None) if r is not None else (None, 'Não foi possível salvar.')
        r = execute_query(
            'INSERT INTO nfse_servicos (empresa_id, nome, descricao, cod_trib_nac, cod_trib_mun, cod_nbs, '
            '  cod_servico_sp, valor_padrao, ordem, emite_nota, alterado_por) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (empresa_id,) + vals + (usuario_id,))
        return (r, None) if r else (None, 'Não foi possível salvar.')

    @staticmethod
    def alternar(servico_id, empresa_id):
        return execute_query('UPDATE nfse_servicos SET ativo = NOT ativo, atualizado_em = NOW() '
                             ' WHERE id = %s AND empresa_id = %s', (servico_id, empresa_id))


class NfseEmitida:

    @staticmethod
    def listar(empresa_ids=None, situacao='todas', de=None, ate=None, busca=None, limite=400):
        cond, params = [], []
        if empresa_ids:
            cond.append('n.empresa_id IN (' + ','.join(['%s'] * len(empresa_ids)) + ')')
            params += list(empresa_ids)
        if situacao and situacao != 'todas':
            cond.append('n.situacao = %s')
            params.append(situacao)
        if de:
            cond.append('COALESCE(n.dh_emissao, n.criado_em) >= %s')
            params.append(de)
        if ate:
            cond.append('COALESCE(n.dh_emissao, n.criado_em) < DATE_ADD(%s, INTERVAL 1 DAY)')
            params.append(ate)
        if busca:
            like = f'%{busca}%'
            cond.append('(n.tomador_nome LIKE %s OR n.tomador_doc LIKE %s OR n.descricao LIKE %s '
                        'OR n.numero_nfse LIKE %s OR n.chave_acesso LIKE %s)')
            params += [like] * 5
        where = ('WHERE ' + ' AND '.join(cond)) if cond else ''
        return execute_query(
            f"""SELECT n.id, n.empresa_id, n.ambiente, n.emissor, n.serie, n.numero_dps, n.situacao,
                       n.dh_emissao, n.competencia, n.criado_em, n.numero_nfse, n.chave_acesso,
                       n.tomador_doc, n.tomador_nome, n.descricao, n.valor_servico, n.valor_liquido,
                       n.ibs_total, n.cbs_valor, n.erro_ultimo, n.fin_titulo_id, n.xml_nfse_path,
                       n.cancelada_em,
                       b.id AS boleto_id, b.situacao AS boleto_situacao, b.vencimento AS boleto_vencimento,
                       b.pago_em AS boleto_pago_em, b.valor_pago AS boleto_valor_pago, b.ambiente AS boleto_ambiente,
                       b.email_enviado_em, b.email_visto_em, b.abriu_boleto_em, b.abriu_nota_em
                  FROM nfse_emitidas n
                  LEFT JOIN fin_boletos b
                    ON b.id = (SELECT MAX(x.id) FROM fin_boletos x WHERE x.nfse_id = n.id AND x.situacao <> 'erro')
                {where}
                 ORDER BY COALESCE(n.dh_emissao, n.criado_em) DESC, n.id DESC
                 LIMIT %s""", tuple(params) + (int(limite),), fetch=True) or []

    @staticmethod
    def get(nota_id):
        return execute_query(
            'SELECT n.*, c.nome_razao_social AS empresa_nome, c.numero_cliente AS empresa_numero '
            '  FROM nfse_emitidas n JOIN clientes c ON c.id = n.empresa_id WHERE n.id = %s',
            (nota_id,), fetch=True, fetch_one=True)

    @staticmethod
    def resumo(empresa_ids=None):
        """Números do mês corrente, só dado real: autorizadas (qtd e valor), rejeitadas e
        rascunhos; e o total de autorizadas de produção no ano."""
        filtro, params = '', []
        if empresa_ids:
            filtro = ' AND empresa_id IN (' + ','.join(['%s'] * len(empresa_ids)) + ')'
            params = list(empresa_ids)
        r = execute_query(
            f"""SELECT
                  SUM(situacao = 'autorizada' AND ambiente = 1
                      AND YEAR(dh_emissao) = YEAR(CURDATE()) AND MONTH(dh_emissao) = MONTH(CURDATE())) AS mes_qtd,
                  COALESCE(SUM(CASE WHEN situacao = 'autorizada' AND ambiente = 1
                      AND YEAR(dh_emissao) = YEAR(CURDATE()) AND MONTH(dh_emissao) = MONTH(CURDATE())
                      THEN valor_servico END), 0) AS mes_valor,
                  SUM(situacao = 'rejeitada' OR situacao = 'erro') AS pendentes,
                  SUM(situacao = 'rascunho') AS rascunhos,
                  SUM(situacao = 'autorizada' AND ambiente = 2) AS testes,
                  SUM(situacao = 'autorizada' AND ambiente = 1 AND YEAR(dh_emissao) = YEAR(CURDATE())) AS ano_qtd
                 FROM nfse_emitidas WHERE 1 = 1 {filtro}""", tuple(params), fetch=True, fetch_one=True) or {}
        return {k: (r.get(k) or 0) for k in ('mes_qtd', 'mes_valor', 'pendentes', 'rascunhos', 'testes', 'ano_qtd')}

    @staticmethod
    def series(empresa_id):
        """Séries da empresa com o último número e para que ambiente vão."""
        return execute_query(
            'SELECT id, serie, ambiente, ultimo_numero, ativo, observacao FROM nfse_dps_controle '
            ' WHERE empresa_id = %s ORDER BY ambiente, serie', (empresa_id,), fetch=True) or []


# ---------------------------------------------------------------------------
# Tomador: quem recebe a nota vem do cadastro de clientes
# ---------------------------------------------------------------------------
_TIPOS_LOGRADOURO = {
    'RUA': 'R', 'R': 'R', 'AVENIDA': 'AV', 'AV': 'AV', 'ALAMEDA': 'AL', 'AL': 'AL',
    'TRAVESSA': 'TV', 'TV': 'TV', 'ESTRADA': 'EST', 'EST': 'EST', 'RODOVIA': 'ROD', 'ROD': 'ROD',
    'PRACA': 'PC', 'PRAÇA': 'PC', 'PC': 'PC', 'LARGO': 'LGO', 'VIELA': 'VLA', 'VIA': 'VIA',
    'QUADRA': 'Q', 'Q': 'Q', 'AREA': 'AR', 'ÁREA': 'AR',
}


def separar_logradouro(logradouro):
    """('AV', 'QUARTA AVENIDA') a partir de 'AVENIDA QUARTA AVENIDA' — a prefeitura de SP
    pede o tipo separado (até 3 letras). Sem tipo reconhecido devolve ('', texto)."""
    txt = (logradouro or '').strip()
    if not txt:
        return '', ''
    partes = txt.split(None, 1)
    tipo = _TIPOS_LOGRADOURO.get(partes[0].upper().rstrip('.'))
    if tipo and len(partes) == 2:
        return tipo, partes[1].strip()
    return '', txt


def buscar_clientes(q, limite=12):
    """Clientes por nome, CNPJ/CPF ou número — para escolher o tomador."""
    q = (q or '').strip()
    if len(q) < 2:
        return []
    like = f'%{q}%'
    dig = _dig(q)
    cond = '(c.nome_razao_social LIKE %s OR c.nome_fantasia LIKE %s OR c.numero_cliente LIKE %s'
    params = [like, like, like]
    if dig and len(dig) >= 3:   # "B2T" não é busca por CNPJ
        cond += " OR REPLACE(REPLACE(REPLACE(c.cpf_cnpj, '.', ''), '/', ''), '-', '') LIKE %s"
        params.append(f'%{dig}%')
    cond += ')'
    return execute_query(
        f"""SELECT c.id, c.numero_cliente, c.nome_razao_social AS nome, c.cpf_cnpj, c.tipo_pessoa, c.situacao,
                  COALESCE(c.avulso, 0) AS avulso
              FROM clientes c
             WHERE {cond}
             ORDER BY (c.situacao = 'ATIVO') DESC, c.nome_razao_social
             LIMIT %s""", tuple(params) + (int(limite),), fetch=True) or []


def ibge_do_documento(doc):
    """Código IBGE do município pelo que as notas já capturadas dizem desse CNPJ/CPF
    (como tomador, senão como prestador). É o município MAIS FREQUENTE, não o da última
    nota: quem emite costuma errar o município do tomador (o Anderson aparece em
    Goiânia na nota de um laboratório). Só serve de reserva quando não há CEP."""
    d = _dig(doc)
    if not d:
        return None
    r = execute_query(
        """SELECT ibge FROM (
             SELECT tomador_municipio_ibge AS ibge, data_emissao d FROM nfse_capturadas
              WHERE tomador_doc = %s AND tomador_municipio_ibge IS NOT NULL
             UNION ALL
             SELECT prestador_municipio_ibge, data_emissao FROM nfse_capturadas
              WHERE prestador_doc = %s AND prestador_municipio_ibge IS NOT NULL
           ) x GROUP BY ibge ORDER BY COUNT(*) DESC, MAX(d) DESC LIMIT 1""", (d, d), fetch=True, fetch_one=True)
    return (r or {}).get('ibge')


def formatar_documento(doc):
    d = _dig(doc)
    if len(d) == 11:
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    return d


def tomador_do_cliente(cliente_id, consultar_cep=True):
    """Dados do tomador a partir do cadastro (cliente ou avulso): documento, nome,
    endereço principal, e-mail e o IBGE do município (pelo CEP; senão pelas notas).
    É a ÚNICA fonte do tomador (02/11/2026): a tela mostra, o pedido relê daqui."""
    c = execute_query(
        'SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, inscricao_municipal, email, telefone, celular, '
        '       tipo_pessoa, situacao, COALESCE(avulso, 0) AS avulso '
        '  FROM clientes WHERE id = %s', (cliente_id,), fetch=True, fetch_one=True)
    if not c:
        return None
    e = execute_query(
        'SELECT cep, logradouro, numero, complemento, bairro, cidade, estado FROM enderecos_clientes '
        " WHERE cliente_id = %s ORDER BY principal DESC, (tipo = 'COMERCIAL') DESC, id LIMIT 1",
        (cliente_id,), fetch=True, fetch_one=True) or {}
    # Município: o CEP do cadastro manda (o IBGE vem dos Correios/IBGE via ViaCEP);
    # as notas capturadas são só reserva, porque o emitente erra o município do tomador.
    ibge, fonte_ibge = None, ''
    if consultar_cep and e.get('cep'):
        try:
            from utils.cep import consultar
            r = consultar(e['cep'])
            if r and r.get('ibge'):
                ibge, fonte_ibge = _dig(r['ibge']), 'cep'
        except Exception:
            pass
    if not ibge:
        ibge = ibge_do_documento(c['cpf_cnpj'])
        fonte_ibge = 'notas' if ibge else ''
    doc = _dig(c['cpf_cnpj'])
    t = {
        'cliente_id': c['id'], 'numero_cliente': c['numero_cliente'],
        'avulso': bool(c.get('avulso')), 'tipo_pessoa': c.get('tipo_pessoa') or ('PF' if len(doc) == 11 else 'PJ'),
        'situacao': c.get('situacao') or '',
        'documento': doc, 'doc_fmt': formatar_documento(doc), 'nome': c['nome_razao_social'],
        # Reforma: pessoa física é consumidor final; empresa toma o serviço para a atividade dela.
        # É a sugestão da tela; quem emite pode mudar na etapa Valores.
        'ind_final': 1 if len(doc) == 11 else 0,
        'inscricao_municipal': c.get('inscricao_municipal') or '',
        'email': c.get('email') or '', 'fone': _dig(c.get('telefone') or c.get('celular') or ''),
        'cep': _dig(e.get('cep')), 'logradouro': e.get('logradouro') or '', 'numero': e.get('numero') or '',
        'complemento': e.get('complemento') or '', 'bairro': e.get('bairro') or '',
        'cidade': e.get('cidade') or '', 'uf': e.get('estado') or '',
        'ibge': ibge or '', 'fonte_ibge': fonte_ibge,
    }
    t['faltas'] = faltas_do_tomador(t)
    return t


def faltas_do_tomador(t):
    """O que falta no cadastro para a nota sair inteira. Cada item: {campo, nivel, texto}.
    nivel 'obrigatorio' trava a emissão; 'endereco' faz a nota sair sem endereço (o governo
    exige o bloco completo ou nada); 'opcional' é só aviso."""
    f = []
    if len(t.get('documento') or '') not in (11, 14):
        f.append({'campo': 'documento', 'nivel': 'obrigatorio', 'texto': 'CPF/CNPJ'})
    if not (t.get('nome') or '').strip():
        f.append({'campo': 'nome', 'nivel': 'obrigatorio', 'texto': 'nome'})
    if not t.get('cep'):
        f.append({'campo': 'cep', 'nivel': 'endereco', 'texto': 'CEP'})
    else:
        if not (t.get('logradouro') or '').strip():
            f.append({'campo': 'logradouro', 'nivel': 'endereco', 'texto': 'rua'})
        if not (t.get('numero') or '').strip():
            f.append({'campo': 'numero', 'nivel': 'endereco', 'texto': 'número'})
        if not (t.get('bairro') or '').strip():
            f.append({'campo': 'bairro', 'nivel': 'endereco', 'texto': 'bairro'})
        if len(t.get('ibge') or '') != 7:
            f.append({'campo': 'ibge', 'nivel': 'endereco', 'texto': 'município (código IBGE)'})
    if not (t.get('email') or '').strip():
        f.append({'campo': 'email', 'nivel': 'opcional', 'texto': 'e-mail'})
    return f


def titulos_para_nota(empresa_id, dias=120):
    """Títulos a receber da empresa, sem nota ainda, dos últimos ``dias`` — para a nota
    nascer do título (contraparte, valor, competência)."""
    return execute_query(
        """SELECT t.id, t.contraparte_nome, t.contraparte_doc, t.cliente_id, t.descricao,
                  t.competencia, t.vencimento, t.valor, t.status
             FROM fin_titulos t
            WHERE t.empresa_id = %s AND t.tipo = 'R' AND t.nfse_id IS NULL
              AND t.status IN ('aberto', 'parcial', 'liquidado')
              AND t.emissao >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            ORDER BY t.vencimento DESC, t.id DESC LIMIT 200""",
        (empresa_id, int(dias)), fetch=True) or []


# ---------------------------------------------------------------------------
# Matriz fiscal da empresa — o que falta para emitir e a marca de liberação
# (06/11/2026). UM lugar só: o Fiscal (empresas dos clientes) e o Financeiro (as
# do escritório) leem e gravam a MESMA linha de nfse_emissao_config.
# ---------------------------------------------------------------------------
# Regime do cadastro -> opSimpNac do leiaute nacional.
REGIME_PARA_OP = {'SIMPLES': 3, 'MEI': 2, 'LUCRO_PRESUMIDO': 1, 'LUCRO_REAL': 1}
REGIMES = {'SIMPLES': 'Simples Nacional', 'MEI': 'MEI', 'LUCRO_PRESUMIDO': 'Lucro Presumido',
           'LUCRO_REAL': 'Lucro Real'}
# Só o que já saiu AUTORIZADO pelo Sefin de produção: Simples (op 3, Qualicontax 04/11) e fora do
# Simples (op 1, NH Goiatuba Lucro Real, NFS-e 1 da série 900, 06/11/2026, cancelada em seguida —
# Presumido é o mesmo caminho com outras alíquotas). MEI (op 2) ainda não.
OP_PROVADOS = {1, 3}
# Municípios fora do Emissor Nacional cujo sistema próprio já fala com o motor (DPS nacional no
# webservice do município). Senador Canedo = ISSWeb/Fiorilli (provado na homologação, 06/11/2026).
PROVEDOR_DO_MUNICIPIO = {'5220454': 'fiorilli'}
# Prefeituras que RECUSAM a nota sem a inscrição municipal do prestador (provado pela recusa).
# Goiatuba: E0116 em 06/11/2026 (NH Goiatuba). Entra aqui só município com recusa vista.
MUNICIPIOS_EXIGEM_IM = {'5209101': 'Goiatuba'}


def empresa_da_matriz(cliente_id):
    """O que o CADASTRO diz da empresa (o Cadastro começa, o Fiscal termina)."""
    c = execute_query(
        'SELECT id, numero_cliente, nome_razao_social, nome_fantasia, cpf_cnpj, inscricao_municipal, email, '
        '       regime_tributario, situacao, COALESCE(avulso, 0) AS avulso, cnae_fiscal, cnae_fiscal_descricao '
        '  FROM clientes WHERE id = %s', (cliente_id,), fetch=True, fetch_one=True)
    if not c:
        return None
    t = tomador_do_cliente(cliente_id) or {}        # mesmo leitor do endereço/IBGE do tomador
    from models.dfe_certificado import DfeCertificado
    cert = DfeCertificado.get_by_cliente(cliente_id) or None
    if cert:
        cert = dict(cert)
        cert['status'] = DfeCertificado.classificar_validade(cert.get('validade'))
        cert['cnpj_dig'] = _dig(cert.get('cnpj'))
    doc = _dig(c['cpf_cnpj'])
    return {
        'id': c['id'], 'numero_cliente': c['numero_cliente'], 'nome': c['nome_razao_social'],
        'fantasia': c.get('nome_fantasia') or '', 'documento': doc, 'doc_fmt': formatar_documento(doc),
        'im': c.get('inscricao_municipal') or '', 'email': c.get('email') or '',
        'regime': c.get('regime_tributario') or '',
        'regime_rotulo': REGIMES.get(c.get('regime_tributario') or '', 'não preenchido'),
        'situacao': c.get('situacao') or '', 'avulso': bool(c.get('avulso')),
        'cidade': t.get('cidade') or '', 'uf': t.get('uf') or '', 'ibge': t.get('ibge') or '',
        'certificado': cert,
        'cnae': _dig(c.get('cnae_fiscal')), 'cnae_descricao': c.get('cnae_fiscal_descricao') or '',
    }


def sugestao_da_matriz(emp):
    """Primeiro preenchimento: o que dá para tirar do cadastro (regime, município, IM,
    e-mail) + o padrão da reforma que domina as notas (CST 000 / 000001 / 100301)."""
    op = REGIME_PARA_OP.get(emp.get('regime') or '')
    from utils.nfse_servicos import sugerir_servico
    ctrib, nbs = sugerir_servico(emp.get('id'), emp.get('documento'), emp.get('cnae'))
    return {
        'cod_trib_nac': ctrib, 'cod_nbs': nbs,
        'emissor': PROVEDOR_DO_MUNICIPIO.get(emp.get('ibge') or '', 'nacional'),
        'municipio_ibge': emp.get('ibge') or '',
        'inscricao_municipal': emp.get('im') or '', 'email_remetente': emp.get('email') or '',
        'op_simp_nac': op, 'reg_ap_trib_sn': 1 if op == 3 else None, 'reg_esp_trib': 0,
        'aliquota_iss': 0, 'iss_retido': 0,
        # PIS/COFINS próprio fora do Simples: Presumido cumulativo 0,65/3; Real não cumulativo 1,65/7,6.
        'piscofins_cst': '01' if op == 1 else None,
        'aliquota_pis': {'LUCRO_PRESUMIDO': '0.65', 'LUCRO_REAL': '1.65'}.get(emp.get('regime') or '', '0'),
        'aliquota_cofins': {'LUCRO_PRESUMIDO': '3.00', 'LUCRO_REAL': '7.60'}.get(emp.get('regime') or '', '0'),
        'ibscbs_cst': '000', 'ibscbs_cclasstrib': '000001', 'ibscbs_cind_op': '100301',
        'ibscbs_ind_dest': 0, 'ibscbs_ind_final': 0,
        'descricao_padrao': 'Serviços prestados em {mes} de {ano}',
    }


CARTOES = (('empresa', 'A empresa'), ('prefeitura', 'Prefeitura'), ('servico', 'Serviço'),
           ('impostos', 'Impostos'), ('numeracao', 'Numeração'))


def faltas_da_matriz(emp, cfg, series, sugestao=None, aceita=False):
    """Tudo o que impede (``trava``) ou merece atenção (``aviso``) antes de liberar.
    Cada item: {nivel, onde, cartao, texto}. ``onde``: cadastro | certificado | matriz | serie
    (quem resolve); ``cartao``: empresa | prefeitura | servico | impostos | numeracao (onde a
    tela de cartões mostra — 08/11/2026). Sem configuração salva, confere a ``sugestao`` (o que
    a tela mostra) para a lista já sair inteira na primeira visita — e trava por não estar salva.
    ``aceita``: resultado já consultado de ``aceita_emissor_nacional`` (False = consultar aqui)."""
    f = []
    _cartao = {'cadastro': 'empresa', 'certificado': 'empresa', 'serie': 'numeracao', 'matriz': 'prefeitura'}

    def trava(onde, texto, cartao=None):
        f.append({'nivel': 'trava', 'onde': onde, 'cartao': cartao or _cartao[onde], 'texto': texto})

    def aviso(onde, texto, cartao=None):
        f.append({'nivel': 'aviso', 'onde': onde, 'cartao': cartao or _cartao[onde], 'texto': texto})

    # --- cadastro (o Cadastro resolve) ---
    if len(emp['documento']) == 11:
        trava('cadastro', 'Pessoa física: a emissão pelo CPF ainda não existe no sistema.')
    elif len(emp['documento']) != 14:
        trava('cadastro', 'CNPJ do cadastro incompleto.')
    if emp['situacao'] != 'ATIVO':
        trava('cadastro', f"Empresa {(emp['situacao'] or 'sem situação').lower()} no cadastro.")
    if emp['regime'] not in REGIME_PARA_OP:
        trava('cadastro', 'Regime tributário não preenchido no cadastro.')

    # --- certificado (é ele que assina a nota; o CNPJ do prestador sai dele) ---
    cert = emp.get('certificado')
    if not cert:
        trava('certificado', 'Sem certificado digital da empresa.')
    else:
        st = cert.get('status') or {}
        if st.get('nivel') == 'vencido':
            trava('certificado', 'Certificado digital vencido.')
        elif st.get('nivel') == 'sem_data':
            trava('certificado', 'Certificado sem validade conhecida: suba o arquivo de novo.')
        elif st.get('nivel') == 'laranja':
            aviso('certificado', f"Certificado vence em {st.get('dias')} dia(s).")
        if not cert.get('dropbox_path'):
            trava('certificado', 'O arquivo do certificado não está guardado.')
        if cert.get('cnpj_dig') and cert['cnpj_dig'] != emp['documento']:
            trava('certificado', 'O certificado é de outro CNPJ (procuração ou matriz): a nota sairia '
                                 'no CNPJ errado. Precisa do e-CNPJ desta empresa.')

    # --- matriz (o Fiscal resolve) ---
    if not cfg:
        trava('matriz', 'Configuração ainda não salva.', 'empresa')
        if not sugestao:
            return f
        cfg = sugestao
    op = cfg.get('op_simp_nac')
    esperado = REGIME_PARA_OP.get(emp['regime'])
    if esperado and op != esperado:
        trava('matriz', f"O regime da configuração ({OP_SIMP_NAC.get(op, 'vazio')}) não bate "
                        f"com o do cadastro ({emp['regime_rotulo']}).", 'impostos')
    if op not in OP_PROVADOS:
        trava('matriz', f"{OP_SIMP_NAC.get(op, 'Este regime')}: ainda não foi provado no emissor.", 'impostos')
    if op == 3 and not cfg.get('reg_ap_trib_sn'):
        trava('matriz', 'Falta a apuração no Simples.', 'impostos')
    if op == 1 and (len(_dig(cfg.get('piscofins_cst'))) != 2 or not float(cfg.get('aliquota_pis') or 0) > 0
                    or not float(cfg.get('aliquota_cofins') or 0) > 0):
        trava('matriz', 'Fora do Simples: preencha o PIS e a COFINS da empresa.', 'impostos')
    emissor = cfg.get('emissor') or 'nacional'
    if emissor == 'sp':
        trava('matriz', 'Pela prefeitura de São Paulo só a Qualicontax foi provada: use o Emissor Nacional.')
    if emissor == 'fiorilli':
        if not (cfg.get('ws_url') or '').strip().lower().startswith('http'):
            trava('matriz', 'Falta o endereço do sistema da prefeitura (a prefeitura informa).')
        if not _dig(cfg.get('inscricao_municipal')):
            trava('matriz', 'A inscrição municipal é obrigatória para o sistema da prefeitura.')
        aviso('matriz', 'A prefeitura precisa ter credenciado a empresa para emitir por sistema, e o serviço '
                        'tem que estar no cadastro municipal dela.')
    if len(_dig(cfg.get('municipio_ibge'))) != 7:
        trava('matriz', 'Falta o município da empresa (código IBGE).')
    else:
        # O Emissor Nacional só emite onde a prefeitura aderiu (E0037/E0039, 06/11/2026).
        if aceita is False:
            from utils.integrations.nfse_adn.convenio import aceita_emissor_nacional
            aceita = aceita_emissor_nacional(cfg.get('municipio_ibge'), ambiente=1)
        cidade = emp.get('cidade') or _dig(cfg.get('municipio_ibge'))
        if _dig(cfg.get('municipio_ibge')) in MUNICIPIOS_EXIGEM_IM and not _dig(cfg.get('inscricao_municipal')):
            trava('matriz', f"{MUNICIPIOS_EXIGEM_IM[_dig(cfg.get('municipio_ibge'))]} exige a inscrição "
                            'municipal da empresa: sem ela a prefeitura recusa a nota. Preencha a inscrição '
                            'municipal (está no alvará ou no cadastro da prefeitura).')
        elif (_dig(cfg.get('municipio_ibge')) != '3550308' and not _dig(cfg.get('inscricao_municipal'))
                and aceita == 'sim'):
            aviso('matriz', 'Sem inscrição municipal: algumas prefeituras recusam a nota sem ela.')
        if aceita in ('nao', 'sem_convenio') and emissor == 'fiorilli':
            pass                     # o município não aceita o nacional, mas a nota vai pelo ISSWeb dele
        elif aceita in ('nao', 'sem_convenio') and _dig(cfg.get('municipio_ibge')) in PROVEDOR_DO_MUNICIPIO:
            trava('matriz', f'{cidade} não aceita o Emissor Nacional: a nota sai pelo sistema da prefeitura. '
                            'Escolha esse caminho e preencha o endereço que a prefeitura informar.')
        elif aceita in ('nao', 'sem_convenio'):
            trava('matriz', f'{cidade} não aceita o Emissor Nacional, e a ligação com o sistema dessa '
                            'prefeitura ainda não foi feita.')
        elif aceita is None:
            aviso('matriz', f'Não consegui consultar agora se {cidade} aceita o Emissor Nacional; '
                            'a conferência é refeita ao abrir de novo.')
    ctrib = _dig(cfg.get('cod_trib_nac'))
    nbs = _dig(cfg.get('cod_nbs'))
    if len(ctrib) != 6:
        trava('matriz', 'Escolha o serviço que a empresa presta.', 'servico')
    else:
        from utils.nfse_servicos import nbs_ok, nome_servico
        if not nome_servico(ctrib):
            trava('matriz', f'O código de serviço {ctrib} não está na lista oficial.', 'servico')
        if len(nbs) != 9 or nbs == '999999999':
            trava('matriz', 'Escolha o tipo de serviço (NBS) na lista.', 'servico')
        elif not nbs_ok(ctrib, nbs):
            trava('matriz', 'A NBS escolhida não é uma das aceitas pelo governo para esse serviço '
                            '(tabela oficial): escolha uma da lista.', 'servico')
    if not (cfg.get('email_remetente') or '').strip():
        aviso('matriz', 'Sem e-mail da empresa: a nota sai sem contato do prestador.')

    # --- numeração (série de produção) ---
    serie = (cfg.get('serie_nacional') or '').strip().upper()
    if not serie:
        trava('serie', 'Falta criar a numeração das notas.')
    else:
        s = next((x for x in series if str(x['serie']).upper() == serie), None)
        if not s:
            trava('serie', f'A numeração (série {serie}) ainda não foi criada.')
        elif not s['ativo']:
            trava('serie', f'A numeração (série {serie}) está desligada.')
        elif int(s['ambiente']) != 1:
            trava('serie', f'A série {serie} é de TESTE: a numeração padrão tem que ser de produção.')
    return f


def cartoes_da_matriz(faltas):
    """[{chave, rotulo, travas, avisos, ok}] na ordem da tela; ``prontos`` = quantos sem trava."""
    out = []
    for k, rot in CARTOES:
        tv = [x for x in faltas if x['cartao'] == k and x['nivel'] == 'trava']
        av = [x for x in faltas if x['cartao'] == k and x['nivel'] == 'aviso']
        out.append({'chave': k, 'rotulo': rot, 'travas': tv, 'avisos': av, 'ok': not tv})
    return out


def liberar_matriz(empresa_id, usuario_id):
    """Marca 'Conferido e liberado'. Quem chama confere antes que não há trava.
    Devolve False se o banco recusou (ex.: migração nfse_emissao_05 ainda não aplicada)."""
    return execute_query('UPDATE nfse_emissao_config SET liberado_em = NOW(), liberado_por = %s WHERE empresa_id = %s',
                  (usuario_id, empresa_id))


def revogar_matriz(empresa_id):
    return execute_query('UPDATE nfse_emissao_config SET liberado_em = NULL, liberado_por = NULL WHERE empresa_id = %s',
                  (empresa_id,))


def situacao_das_matrizes():
    """{empresa_id: 'liberada' | 'configurada'} para a lista do Fiscal (sem linha = sem configuração)."""
    rows = execute_query('SELECT * FROM nfse_emissao_config', fetch=True) or []
    return {r['empresa_id']: ('liberada' if r.get('liberado_em') else 'configurada') for r in rows}


def resumo_das_notas(cliente_id):
    """Cartão "Notas desta empresa" da Configuração (09/11/2026): só PRODUÇÃO (ambiente 1)."""
    r = execute_query(
        "SELECT SUM(situacao = 'autorizada') autorizadas, SUM(situacao = 'cancelada') canceladas, "
        "SUM(situacao IN ('rejeitada', 'erro')) recusadas, MAX(criado_em) ultima_em "
        "FROM nfse_emitidas WHERE empresa_id = %s AND ambiente = 1", (cliente_id,), fetch=True, fetch_one=True) or {}
    u = execute_query(
        "SELECT situacao, numero_nfse, criado_em, erro_ultimo FROM nfse_emitidas "
        "WHERE empresa_id = %s AND ambiente = 1 ORDER BY id DESC LIMIT 1", (cliente_id,), fetch=True, fetch_one=True)
    if u and u.get('situacao') in ('rejeitada', 'erro'):
        u['motivo'] = motivo_curto(u.get('erro_ultimo'))[0]
    return {'autorizadas': int(r.get('autorizadas') or 0), 'canceladas': int(r.get('canceladas') or 0),
            'recusadas': int(r.get('recusadas') or 0), 'ultima': u}


def contexto_da_matriz(cliente_id, emp=None):
    """Tudo o que a tela de cartões precisa (08/11/2026) — UM montador para o Fiscal e o
    Financeiro: empresa, configuração (salva ou sugerida), séries, o que falta por cartão,
    se a cidade aceita o Emissor Nacional, atividades do CNPJ e histórico de notas."""
    from utils import nfse_servicos as S
    from utils.integrations.nfse_adn.convenio import aceita_emissor_nacional
    emp = emp or empresa_da_matriz(cliente_id)
    if not emp:
        return None
    salvo = NfseConfig.get(cliente_id)
    sugestao = sugestao_da_matriz(emp)
    cfg = dict(salvo) if salvo else dict(sugestao)
    series = NfseEmitida.series(cliente_id)
    ibge = _dig(cfg.get('municipio_ibge'))
    aceita = aceita_emissor_nacional(ibge, ambiente=1) if len(ibge) == 7 else None
    faltas = faltas_da_matriz(emp, salvo, series, sugestao=sugestao, aceita=aceita)
    cartoes = cartoes_da_matriz(faltas)
    ctrib = _dig(cfg.get('cod_trib_nac'))
    return {
        'emp': emp, 'cfg': cfg, 'salva': bool(salvo), 'sugestao': sugestao, 'series': series,
        'faltas': faltas, 'cartoes': {c['chave']: c for c in cartoes}, 'cartoes_lista': cartoes,
        'prontos': sum(1 for c in cartoes if c['ok']) if salvo else 0,
        'aceita': aceita, 'provedor': PROVEDOR_DO_MUNICIPIO.get(ibge, ''),
        'exige_im': MUNICIPIOS_EXIGEM_IM.get(ibge, ''),
        'notas_resumo': resumo_das_notas(cliente_id),
        'atividades': S.atividades_da_empresa(emp['documento'], emp.get('cnae'), emp.get('cnae_descricao')),
        'historico': [{'ctrib': c, 'ctrib_fmt': S.fmt_ctrib(c), 'nome': S.nome_servico(c), 'nbs': n, 'qtd': q}
                      for c, n, q in S.historico_de_notas(cliente_id, emp['documento'])],
        'servico_atual': {'ctrib': ctrib, 'ctrib_fmt': S.fmt_ctrib(ctrib) if ctrib else '',
                          'nome': S.nome_servico(ctrib) if ctrib else ''},
        'nbs_opcoes': S.nbs_sugeridas(ctrib) if ctrib else [],
        'meta_item': S.meta_do_item(ctrib) if ctrib else {},
        'fmt_nbs': S.fmt_nbs,
    }
