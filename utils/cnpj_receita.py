# -*- coding: utf-8 -*-
"""Consulta de CNPJ na base pública da Receita — com DUAS fontes e a DATA da base.

Por que existe (22/09/2026): o Anderson buscou um CNPJ que ele sabia ter
mudado e a tela trouxe o cadastro antigo. Não é defeito da nossa fonte: a
Receita Federal não tem API pública de CNPJ; o que existe são réplicas do
arquivo "Dados Abertos do CNPJ", publicado UMA VEZ POR MÊS. Conferido em
quatro provedores (BrasilAPI, ReceitaWS, MinhaReceita, OpenCNPJ): os quatro
com a mesma foto, de 15/09/2026.

Daí as três coisas que este módulo faz:
  * ``consultar(cnpj)``: BrasilAPI primeiro (sem limite de taxa conhecido);
    se falhar ou cair, ReceitaWS (limite de 3/min, por isso é reserva). As
    duas voltam NORMALIZADAS no mesmo formato — o da tela de clientes.
  * ``base_em()``: a data da foto. Só a ReceitaWS a informa
    (``ultima_atualizacao``) e ela é a MESMA para todos os CNPJs, então fica
    em app_config (``receita_base_em``) e é renovada no máximo uma vez por
    dia — uma chamada por dia, não uma por consulta.
  * ``impressao(dados)`` / ``diferencas(a, b)``: a "impressão digital" dos
    campos que importam e o diff campo a campo, para a vigia
    (utils.cnpj_vigia) saber quando a Receita mudou algo.
"""
import hashlib
import json
import logging
import re
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

TIMEOUT = 10
URL_BRASILAPI = 'https://brasilapi.com.br/api/cnpj/v1/{cnpj}'
URL_RECEITAWS = 'https://receitaws.com.br/v1/cnpj/{cnpj}'
CHAVE_BASE_EM = 'receita_base_em'
_UA = {'User-Agent': 'Qualicontax/1.0 (cadastro de clientes)'}

# Campos que entram na impressão digital da vigia, com o rótulo da tela.
CAMPOS_VIGIA = (
    ('razao_social', 'Razão social'), ('nome_fantasia', 'Nome fantasia'),
    ('situacao_cadastral', 'Situação cadastral'), ('natureza_juridica', 'Natureza jurídica'),
    ('porte', 'Porte'), ('data_inicio_atividade', 'Início da atividade'),
    ('cnae_fiscal', 'CNAE principal'), ('cnae_fiscal_descricao', 'Descrição do CNAE'),
    ('capital_social', 'Capital social'), ('opcao_pelo_simples', 'Simples Nacional'),
    ('opcao_pelo_mei', 'MEI'), ('logradouro', 'Logradouro'), ('numero', 'Número'),
    ('complemento', 'Complemento'), ('bairro', 'Bairro'), ('municipio', 'Município'),
    ('uf', 'UF'), ('cep', 'CEP'), ('email', 'E-mail'),
    ('ddd_telefone_1', 'Telefone 1'), ('ddd_telefone_2', 'Telefone 2'),
)


class CnpjNaoEncontrado(Exception):
    pass


class FonteIndisponivel(Exception):
    pass


def so_digitos(cnpj):
    return re.sub(r'\D', '', str(cnpj or ''))


# ---------------------------------------------------------------- normalização
def _ie_brasilapi(d):
    ies = d.get('inscricoes_estaduais')
    if isinstance(ies, list):
        for ie in ies:
            if isinstance(ie, dict) and ie.get('ativo') and ie.get('inscricao_estadual'):
                return str(ie['inscricao_estadual'])
        for ie in ies:
            if isinstance(ie, dict) and ie.get('inscricao_estadual'):
                return str(ie['inscricao_estadual'])
    return str(d.get('inscricao_estadual') or '')


def _normalizar_brasilapi(d):
    return {
        'cnpj': so_digitos(d.get('cnpj', '')),
        'razao_social': d.get('razao_social', '') or '',
        'nome_fantasia': d.get('nome_fantasia', '') or '',
        'situacao_cadastral': d.get('descricao_situacao_cadastral', '') or '',
        'porte': d.get('porte', '') or '',
        'natureza_juridica': d.get('natureza_juridica', '') or '',
        'data_inicio_atividade': d.get('data_inicio_atividade', '') or '',
        'inscricao_estadual': _ie_brasilapi(d),
        'cnae_fiscal': str(d.get('cnae_fiscal') or ''),
        'cnae_fiscal_descricao': d.get('cnae_fiscal_descricao', '') or '',
        'cnaes_secundarios': d.get('cnaes_secundarios', []) or [],
        'capital_social': str(d.get('capital_social') if d.get('capital_social') is not None else ''),
        'opcao_pelo_simples': bool(d.get('opcao_pelo_simples')),
        'opcao_pelo_mei': bool(d.get('opcao_pelo_mei')),
        'logradouro': d.get('logradouro', '') or '',
        'numero': d.get('numero', '') or '',
        'complemento': d.get('complemento', '') or '',
        'bairro': d.get('bairro', '') or '',
        'municipio': d.get('municipio', '') or '',
        'uf': d.get('uf', '') or '',
        'cep': so_digitos(d.get('cep', '')),
        'ddd_telefone_1': so_digitos(d.get('ddd_telefone_1', '')),
        'ddd_telefone_2': so_digitos(d.get('ddd_telefone_2', '')),
        'email': (d.get('email') or d.get('correio_eletronico') or d.get('endereco_eletronico') or '').strip(),
        'qsa': d.get('qsa', []) or [],
    }


def _data_br_para_iso(s):
    m = re.match(r'^(\d{2})/(\d{2})/(\d{4})$', str(s or '').strip())
    return f'{m.group(3)}-{m.group(2)}-{m.group(1)}' if m else (s or '')


def _normalizar_receitaws(d):
    tels = [so_digitos(t) for t in str(d.get('telefone') or '').split('/') if so_digitos(t)]
    ativ = d.get('atividade_principal') or []
    ativ = ativ[0] if isinstance(ativ, list) and ativ else (ativ if isinstance(ativ, dict) else {})
    nat = re.sub(r'^\s*[\d.-]+\s*-\s*', '', str(d.get('natureza_juridica') or ''))
    cap = str(d.get('capital_social') or '')
    if re.match(r'^\d+\.\d+$', cap):
        cap = str(int(float(cap))) if float(cap).is_integer() else cap
    simples = d.get('simples') if isinstance(d.get('simples'), dict) else {}
    simei = d.get('simei') if isinstance(d.get('simei'), dict) else {}
    return {
        'cnpj': so_digitos(d.get('cnpj', '')),
        'razao_social': d.get('nome', '') or '',
        'nome_fantasia': d.get('fantasia', '') or '',
        'situacao_cadastral': d.get('situacao', '') or '',
        'porte': d.get('porte', '') or '',
        'natureza_juridica': nat,
        'data_inicio_atividade': _data_br_para_iso(d.get('abertura')),
        'inscricao_estadual': '',
        'cnae_fiscal': so_digitos(ativ.get('code', '')),
        'cnae_fiscal_descricao': ativ.get('text', '') or '',
        'cnaes_secundarios': [{'codigo': so_digitos(a.get('code', '')), 'descricao': a.get('text', '')}
                              for a in (d.get('atividades_secundarias') or []) if isinstance(a, dict)],
        'capital_social': cap,
        'opcao_pelo_simples': bool(simples.get('optante')),
        'opcao_pelo_mei': bool(simei.get('optante')),
        'logradouro': d.get('logradouro', '') or '',
        'numero': d.get('numero', '') or '',
        'complemento': d.get('complemento', '') or '',
        'bairro': d.get('bairro', '') or '',
        'municipio': d.get('municipio', '') or '',
        'uf': d.get('uf', '') or '',
        'cep': so_digitos(d.get('cep', '')),
        'ddd_telefone_1': tels[0] if tels else '',
        'ddd_telefone_2': tels[1] if len(tels) > 1 else '',
        'email': (d.get('email') or '').strip(),
        'qsa': d.get('qsa', []) or [],
    }


# ---------------------------------------------------------------- fontes
def _brasilapi(cnpj):
    r = requests.get(URL_BRASILAPI.format(cnpj=cnpj), timeout=TIMEOUT, headers=_UA)
    if r.status_code == 404:
        # 404 de "CNPJ não existe" vem com corpo {type: not_found}; 404 de rota
        # quebrada/CDN vem sem ele — e esse NÃO pode virar "não encontrado".
        try:
            corpo = r.json()
        except ValueError:
            corpo = {}
        if str(corpo.get('type', '')).lower() == 'not_found' or 'cnpj' in str(corpo.get('message', '')).lower():
            raise CnpjNaoEncontrado(cnpj)
        raise FonteIndisponivel('brasilapi HTTP 404 sem corpo de não-encontrado')
    if r.status_code == 400:            # dígito verificador errado: não existe
        raise CnpjNaoEncontrado(cnpj)
    if r.status_code != 200:
        raise FonteIndisponivel(f'brasilapi HTTP {r.status_code}')
    return _normalizar_brasilapi(r.json())


def _receitaws(cnpj):
    r = requests.get(URL_RECEITAWS.format(cnpj=cnpj), timeout=TIMEOUT, headers=_UA)
    if r.status_code == 429:
        raise FonteIndisponivel('receitaws: limite de 3 consultas por minuto')
    if r.status_code == 400:
        raise CnpjNaoEncontrado(cnpj)
    if r.status_code != 200:
        raise FonteIndisponivel(f'receitaws HTTP {r.status_code}')
    d = r.json()
    if str(d.get('status', '')).upper() == 'ERROR':
        msg = str(d.get('message', ''))
        if 'não encontrado' in msg.lower() or 'nao encontrado' in msg.lower() or 'inválido' in msg.lower():
            raise CnpjNaoEncontrado(cnpj)
        raise FonteIndisponivel(f'receitaws: {msg}')
    dados = _normalizar_receitaws(d)
    if d.get('ultima_atualizacao'):
        _guardar_base_em(d['ultima_atualizacao'])
    return dados


# Campos que a BrasilAPI ENTREGA VAZIOS para MEI/empresário individual (ela
# oculta endereço e contato de pessoa física) e a ReceitaWS entrega — medido em
# 22/09/2026 no CNPJ 60.598.411/0001-52. Com ``completar=True`` a consulta
# interativa preenche esses buracos na segunda fonte; a vigia NÃO completa (o
# limite de 3/min da ReceitaWS não aguentaria) e compara só fotos da mesma fonte.
CAMPOS_COMPLEMENTO = ('logradouro', 'numero', 'complemento', 'bairro', 'cep',
                      'email', 'ddd_telefone_1', 'ddd_telefone_2')


def consultar(cnpj, completar=False):
    """(dados normalizados, fonte). BrasilAPI e, se ela falhar, ReceitaWS.

    Levanta CnpjNaoEncontrado quando as fontes dizem que não existe, e
    FonteIndisponivel quando nenhuma respondeu."""
    cnpj = so_digitos(cnpj)
    if len(cnpj) != 14:
        raise CnpjNaoEncontrado(cnpj)
    erros, nao_achou = [], False
    for nome, fn in (('brasilapi', _brasilapi), ('receitaws', _receitaws)):
        try:
            dados = fn(cnpj)
            if completar and nome == 'brasilapi' and not dados.get('logradouro') and not dados.get('email'):
                try:
                    extra = _receitaws(cnpj)
                    for k in CAMPOS_COMPLEMENTO:
                        if not dados.get(k) and extra.get(k):
                            dados[k] = extra[k]
                    nome = 'brasilapi+receitaws'
                except Exception as e:           # complemento é cortesia, nunca derruba
                    logger.info('[cnpj] complemento pela receitaws falhou: %s', e)
            return dados, nome
        except CnpjNaoEncontrado:
            # a segunda fonte ainda pode conhecer (bases de meses diferentes);
            # só é "não existe" quando nenhuma devolve dados
            nao_achou = True
        except (requests.RequestException, FonteIndisponivel, ValueError) as e:
            erros.append(f'{nome}: {e}')
            logger.warning('[cnpj] %s falhou para %s: %s', nome, cnpj, e)
    if nao_achou:
        raise CnpjNaoEncontrado(cnpj)
    raise FonteIndisponivel('; '.join(erros))


# ---------------------------------------------------------------- data da base
def _guardar_base_em(iso):
    try:
        from utils.db_helper import execute_query
        data = str(iso)[:10]
        execute_query(
            "INSERT INTO app_config (chave, valor) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()",
            (CHAVE_BASE_EM, data), fetch=False)
    except Exception:
        logger.exception('[cnpj] não guardou a data da base')


def base_em(cnpj_para_sondar=None):
    """Data (YYYY-MM-DD) da foto da Receita que as fontes estão servindo, ou None.

    Lê app_config; se o valor tem mais de 24 h (ou não existe) e há um CNPJ à
    mão, faz UMA chamada à ReceitaWS só para renovar. Nunca levanta."""
    try:
        from utils.db_helper import execute_query
        r = execute_query(
            "SELECT valor, TIMESTAMPDIFF(HOUR, updated_at, NOW()) AS h FROM app_config WHERE chave = %s",
            (CHAVE_BASE_EM,), fetch=True, fetch_one=True) or {}
        if r.get('valor') and (r.get('h') or 0) < 24:
            return r['valor']
        if cnpj_para_sondar:
            try:
                _receitaws(so_digitos(cnpj_para_sondar))
            except Exception as e:
                logger.info('[cnpj] sonda da data da base falhou: %s', e)
            r2 = execute_query("SELECT valor FROM app_config WHERE chave = %s",
                               (CHAVE_BASE_EM,), fetch=True, fetch_one=True) or {}
            return r2.get('valor') or r.get('valor')
        return r.get('valor')
    except Exception:
        logger.exception('[cnpj] base_em falhou')
        return None


def base_em_br(cnpj_para_sondar=None):
    v = base_em(cnpj_para_sondar)
    try:
        return datetime.strptime(v, '%Y-%m-%d').strftime('%d/%m/%Y') if v else None
    except ValueError:
        return v


# ---------------------------------------------------------------- impressão digital
def _norm(v):
    if isinstance(v, bool):
        return 'S' if v else 'N'
    return re.sub(r'\s+', ' ', str(v if v is not None else '')).strip().upper()


def impressao(dados):
    """Dict só com os campos vigiados (normalizados) e o hash deles."""
    foto = {k: _norm(dados.get(k)) for k, _ in CAMPOS_VIGIA}
    h = hashlib.sha256(json.dumps(foto, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return foto, h


def diferencas(antes, depois):
    """[{campo, rotulo, de, para}] entre duas fotos (as de ``impressao``)."""
    out = []
    for k, rotulo in CAMPOS_VIGIA:
        a, b = _norm(antes.get(k)), _norm(depois.get(k))
        if a != b:
            out.append({'campo': k, 'rotulo': rotulo, 'de': a, 'para': b})
    return out
