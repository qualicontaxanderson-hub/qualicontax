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
EMISSORES = {'nacional': 'Emissor Nacional (Sefin)', 'sp': 'Prefeitura de São Paulo (RPS)'}
SITUACOES = {'rascunho': 'Rascunho', 'autorizada': 'Autorizada', 'rejeitada': 'Rejeitada',
             'erro': 'Falha no envio', 'cancelada': 'Cancelada'}


def _dig(v):
    return re.sub(r'\D', '', str(v or ''))


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
            elif k == 'aliquota_iss':
                v = str(v or '0').replace(',', '.') or '0'
            elif k in ('serie_nacional', 'serie_sp'):
                v = (v or '').upper() or None
            elif v == '':
                v = None
            limpos[k] = v
        if not limpos:
            return
        atual = NfseConfig.get(empresa_id)
        if atual:
            sets = ', '.join(f'{k} = %s' for k in limpos)
            execute_query(f'UPDATE nfse_emissao_config SET {sets}, atualizado_em = NOW(), alterado_por = %s '
                          ' WHERE empresa_id = %s', tuple(limpos.values()) + (usuario_id, empresa_id))
        else:
            cols = ', '.join(limpos) + ', empresa_id, alterado_por'
            marks = ', '.join(['%s'] * (len(limpos) + 2))
            execute_query(f'INSERT INTO nfse_emissao_config ({cols}) VALUES ({marks})',
                          tuple(limpos.values()) + (empresa_id, usuario_id))

    @staticmethod
    def descricao_para(cfg, competencia: date):
        """``descricao_padrao`` com {mes} e {ano} preenchidos para a competência."""
        meses = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto',
                 'setembro', 'outubro', 'novembro', 'dezembro')
        modelo = (cfg or {}).get('descricao_padrao') or 'Serviços prestados em {mes} de {ano}'
        return modelo.replace('{mes}', meses[competencia.month - 1]).replace('{ano}', str(competencia.year))


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
                       n.cancelada_em
                  FROM nfse_emitidas n
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
