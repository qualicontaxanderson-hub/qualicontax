# -*- coding: utf-8 -*-
"""Leitor dos arquivos .DEC (declaração) e .REC (recibo) do programa IRPF da Receita.

Como o arquivo é:
- ISO-8859-1, uma linha por registro, CRLF, largura fixa, posições 1-based;
- os 2 primeiros caracteres dizem o tipo do registro ("IR" cabeçalho, "16" declarante,
  "18"/"20" resumo da simplificada/completa, "21" rendimentos de PJ, "25" dependentes,
  "26" pagamentos, "27" bens, "28" dívidas, "T9" trailer...; no .REC: "RC" data/hora,
  "MC" mensagem);
- o último campo de cada linha (NR_CONTROLE, 10 dígitos) é o CRC32 do resto da linha:
  serve para saber se o arquivo está íntegro;
- o desenho muda um pouco a cada exercício (campos novos no FIM das linhas). Cada ano
  tem seu JSON em ``leiautes/irpf_{exercicio}.json``; se o exercício não tiver leiaute
  próprio, usa-se o mais próximo e a leitura fica marcada como "aproximada".

Nunca grava nada e não conhece banco: devolve dicionários.
"""
import json
import os
import re
import zlib
from datetime import date, datetime
from decimal import Decimal

PASTA_LEIAUTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'leiautes')
_CACHE = {}
NOME_ARQUIVO = re.compile(r'^(\d{11})-IRPF-([A-Z])-(\d{4})-(\d{4})-(ORIGI|RETIF)\.(DEC|REC)$', re.I)


class LeiauteIndisponivel(Exception):
    pass


def exercicios_disponiveis():
    out = []
    for n in os.listdir(PASTA_LEIAUTES):
        m = re.match(r'irpf_(\d{4})\.json$', n)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def leiaute(exercicio):
    """(dicionário do leiaute, exercício realmente usado). O mais próximo quando não há o exato."""
    anos = exercicios_disponiveis()
    if not anos:
        raise LeiauteIndisponivel('Nenhum leiaute em utils/irpf/leiautes.')
    usado = exercicio if exercicio in anos else min(anos, key=lambda a: (abs(a - exercicio), -a))
    if usado not in _CACHE:
        with open(os.path.join(PASTA_LEIAUTES, f'irpf_{usado}.json'), encoding='utf-8') as f:
            _CACHE[usado] = json.load(f)
    return _CACHE[usado], usado


def _valor(texto, campo):
    t = texto
    tipo = campo.get('tipo')
    if tipo == 'N':
        dig = t.strip()
        if not dig:
            return None
        if not dig.lstrip('-').isdigit():
            return dig
        dec = campo.get('decimais') or 0
        if dec:
            return Decimal(int(dig)) / (Decimal(10) ** dec)
        return int(dig) if len(dig) <= 18 else dig
    return t.strip()


def _ler_linha(linha, reg):
    out = {}
    for c in reg['campos']:
        ini, tam = c['inicio'] - 1, c['tamanho']
        if ini >= len(linha):
            break
        out[c['campo']] = _valor(linha[ini:ini + tam], c)
    return out


def _crc_ok(linha):
    """O NR_CONTROLE (10 últimos caracteres) é o CRC32 do resto da linha."""
    if len(linha) < 12 or not linha[-10:].isdigit():
        return None
    try:
        return zlib.crc32(linha[:-10].encode('latin-1')) & 0xFFFFFFFF == int(linha[-10:])
    except Exception:
        return None


def ler(conteudo, exercicio=None):
    """Lê um .DEC ou .REC (bytes ou str) e devolve::

        {'exercicio': 2024, 'leiaute_usado': 2024, 'aproximado': False,
         'registros': {'IR': {...}, '16': {...}, '21': [{...}, ...], ...},
         'linhas': 18, 'desconhecidos': ['99'], 'crc_ok': 18, 'crc_falha': 0}

    Registros que aparecem uma vez viram dict; os repetíveis viram lista.
    """
    texto = conteudo.decode('latin-1') if isinstance(conteudo, (bytes, bytearray)) else conteudo
    linhas = [l for l in texto.replace('\r\n', '\n').split('\n') if l.strip()]
    if not linhas:
        raise ValueError('Arquivo vazio.')
    if exercicio is None:
        cab = next((l for l in linhas if l[:2] in ('IR', 'HC', 'HR')), linhas[0])
        try:
            exercicio = int(cab[8:12]) if cab[:2] == 'IR' else None
        except ValueError:
            exercicio = None
    if exercicio is None:
        exercicio = max(exercicios_disponiveis())
    lay, usado = leiaute(exercicio)
    regs, desconhecidos, ok, falha = {}, [], 0, 0
    for l in linhas:
        tipo = l[:2]
        reg = lay.get(tipo)
        if not reg:
            if tipo not in desconhecidos:
                desconhecidos.append(tipo)
            continue
        d = _ler_linha(l, reg)
        c = _crc_ok(l)
        if c is True:
            ok += 1
        elif c is False:
            falha += 1
        if tipo in regs:
            if isinstance(regs[tipo], list):
                regs[tipo].append(d)
            else:
                regs[tipo] = [regs[tipo], d]
        else:
            regs[tipo] = d
    return {'exercicio': exercicio, 'leiaute_usado': usado, 'aproximado': usado != exercicio,
            'registros': regs, 'linhas': len(linhas), 'desconhecidos': desconhecidos,
            'crc_ok': ok, 'crc_falha': falha}


# ---------------------------------------------------------------------------
# Açúcar: acesso tolerante aos campos (os nomes variam um pouco entre registros)
# ---------------------------------------------------------------------------
def lista(regs, tipo):
    v = regs.get(tipo)
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def um(regs, tipo):
    v = regs.get(tipo)
    if isinstance(v, list):
        return v[0] if v else {}
    return v or {}


def campo(d, *nomes, padrao=None):
    for n in nomes:
        if n in d and d[n] not in (None, ''):
            return d[n]
    return padrao


def data_ddmmaaaa(v):
    s = str(v or '').strip()
    if s.isdigit() and 0 < len(s) < 8:
        s = s.zfill(8)          # campo N vira int no leitor e perde o zero do dia (05011972 -> 5011972)
    if len(s) == 8 and s.isdigit() and s != '00000000':
        try:
            return date(int(s[4:]), int(s[2:4]), int(s[:2]))
        except ValueError:
            return None
    return None


def sim(v):
    return str(v or '').strip().upper() in ('S', '1', 'T')


def _dez(v):
    """Número de 10 dígitos lido de campo N (o int perdeu os zeros à esquerda)."""
    s = str(v if v is not None else '').strip()
    if not s or not s.isdigit() or int(s) == 0:
        return None
    return s.zfill(10) if len(s) < 10 else s


def dec(v):
    if v in (None, ''):
        return Decimal('0')
    return v if isinstance(v, Decimal) else Decimal(str(v))


def nome_do_arquivo(nome):
    """``12345678901-IRPF-A-2024-2023-ORIGI.DEC`` -> dict ou None."""
    m = NOME_ARQUIVO.match(os.path.basename(nome or ''))
    if not m:
        return None
    return {'cpf': m.group(1), 'tipo': m.group(2).upper(), 'exercicio': int(m.group(3)),
            'ano_base': int(m.group(4)), 'retificadora': m.group(5).upper() == 'RETIF',
            'extensao': m.group(6).upper()}


def resumo(lido):
    """Dos registros brutos para o que a tela e o banco querem (uma declaração)."""
    r = lido['registros']
    ir, r16, dr = um(r, 'IR'), um(r, '16'), um(r, 'DR')
    completa = sim(campo(r16, 'IN_COMPLETA', padrao=campo(ir, 'IN_COMPLETA')))
    res = um(r, '20') if completa else um(r, '18')
    retif = sim(campo(r16, 'IN_RETIFICADORA', padrao=campo(ir, 'IN_RETIFICADORA')))
    vr_rest = dec(campo(res, 'VR_IMPREST', 'VR_IMPRESTIT', padrao=campo(dr, 'VR_IMPREST')))
    vr_pag = dec(campo(res, 'VR_IMPPAGAR', padrao=campo(dr, 'VR_IMPPAGAR')))
    if vr_rest > 0:
        resultado = 'restituir'
    elif vr_pag > 0:
        resultado = 'pagar'
    else:
        resultado = 'zero'
    out = {
        'exercicio': int(campo(ir, 'EXERCICIO', padrao=lido['exercicio']) or lido['exercicio']),
        'ano_base': campo(ir, 'ANO_BASE'),
        'cpf': str(campo(r16, 'NR_CPF', padrao=campo(ir, 'NR_CPF')) or '').strip(),
        'nome': str(campo(r16, 'NM_NOME', padrao=campo(ir, 'NM_NOME')) or '').strip(),
        'retificadora': retif,
        'completa': completa,
        'tipo_declaracao': str(campo(r16, 'IN_TIPODECLARACAO', padrao='A') or 'A')[:1],
        'resultado': resultado,
        'vr_restituir': vr_rest,
        'vr_pagar': vr_pag,
        'vr_tributaveis': dec(campo(res, 'VR_TOTTRIB', 'VR_RENDTRIB', padrao=campo(ir, 'VR_TOTAL_RENDTRIB_PFPJ_TITDEP'))),
        'vr_isentos': dec(campo(res, 'VR_TOTISENTOS', 'VR_TOTISENTO', padrao=campo(ir, 'VR_TOTISENTOS'))),
        'vr_exclusivos': dec(campo(res, 'VR_TOTEXCLUS', 'VR_TOTEXCLUSIVO', padrao=campo(ir, 'VR_TOTEXCLUSIVO'))),
        'vr_base_calculo': dec(campo(res, 'VR_BASECALC')),
        'vr_imposto_devido': dec(campo(res, 'VR_IMPDEV', 'VR_IMPDEVIDO', padrao=campo(dr, 'VR_IMPDEV'))),
        'vr_imposto_retido': dec(campo(res, 'VR_IMPFONTE', 'VR_IMPOSTO')),
        'vr_bens_anterior': dec(campo(res, 'VR_BENSANT', 'VR_TOTBENSANOBASEANTERIOR')),
        'vr_bens_atual': dec(campo(res, 'VR_BENSATUAL', 'VR_TOTBENSANOBASE')),
        'vr_dividas_atual': dec(campo(res, 'VR_DIVIDAATUAL', 'VR_TOTDIVIDAANOBASE')),
        'vr_pagamentos': dec(campo(ir, 'VR_TOTAL_PAGAMENTOS')),
        'quotas': campo(dr, 'NR_QUOTAS'),
        'vr_quota': dec(campo(dr, 'VR_QUOTA')),
        'banco': (str(campo(r16, 'NR_BANCO', padrao=campo(dr, 'NR_BANCO'))).zfill(3) if campo(r16, 'NR_BANCO', padrao=campo(dr, 'NR_BANCO')) not in (None, '', 0, '0') else None),
        'agencia': str(campo(r16, 'NR_AGENCIA', padrao=campo(dr, 'NR_AGENCIA')) or '').strip() or None,
        'conta': str(campo(r16, 'NR_CONTA', padrao=campo(dr, 'NR_CONTA')) or '').strip() or None,
        'nr_hash': _dez(campo(ir, 'NR_HASH', padrao=campo(um(r, 'R9'), 'NR_HASH'))),
        'recibo_anterior': _dez(campo(ir, 'NR_RECIBO_ULTIMA_DEC_EX_ANTERIOR')),
        'dt_nascimento': data_ddmmaaaa(campo(r16, 'DT_NASCIM', padrao=campo(ir, 'DT_NASCIM'))),
        'email': str(campo(r16, 'NM_EMAIL') or '').strip().lower() or None,
        'telefone': str(campo(r16, 'NR_TELEFONE') or '').strip() or None,
        'uf': str(campo(r16, 'SG_UF', padrao=campo(ir, 'SG_UF')) or '').strip() or None,
        'municipio': str(campo(r16, 'NM_MUNICIP') or '').strip() or None,
        'cep': str(campo(r16, 'NR_CEP') or '').strip() or None,
        'ocupacao': (str(campo(r16, 'CD_OCUP')).zfill(3) if campo(r16, 'CD_OCUP') not in (None, '', 0, '0', '000') else None),
        'conjuge_cpf': str(campo(r16, 'NR_CPF_CONJUGE') or '').strip() or None,
        'leiaute_usado': lido['leiaute_usado'], 'aproximado': lido['aproximado'],
        'crc_ok': lido['crc_ok'], 'crc_falha': lido['crc_falha'], 'desconhecidos': lido['desconhecidos'],
    }
    from utils.irpf.tabelas import numero_recibo
    out['recibo'] = numero_recibo(out['nr_hash'])      # NR_HASH + 2 DV (módulo 11 do programa)
    # blocos
    def pj(d, dep):
        return {'bloco': 'pj_dependente' if dep else 'pj_titular',
                'dependente_cpf': str(campo(d, 'NR_CPF_DEPEN', 'NR_CPF_DEPENDENTE', 'NI_DEPEND') or '').strip() or None,
                'fonte_doc': str(campo(d, 'NR_PAGADOR', 'NR_PAGADORA', 'NR_CNPJ') or '').strip() or None,
                'fonte_nome': str(campo(d, 'NM_PAGADOR', 'NM_PAGADORA', 'NM_NOME') or '').strip() or None,
                'valor': dec(campo(d, 'VR_RENDTO')), 'previdencia': dec(campo(d, 'VR_CONTRIB')),
                'decimo_terceiro': dec(campo(d, 'VR_DECTERC')), 'imposto_retido': dec(campo(d, 'VR_IMPOSTO')),
                'irrf_13': dec(campo(d, 'VR_IRRF13SALARIO'))}
    rend = [pj(d, False) for d in lista(r, '21')] + [pj(d, True) for d in lista(r, '32')]
    for d in lista(r, '22'):
        rend.append({'bloco': 'pf_exterior', 'dependente_cpf': str(campo(d, 'NR_CPF_DEPEN') or '').strip() or None,
                     'mes': campo(d, 'NR_MES'), 'valor': dec(campo(d, 'VR_RENDTO')),
                     'imposto_retido': dec(campo(d, 'VR_IMPOSTO')), 'previdencia': dec(campo(d, 'VR_CONTRIB', 'VR_PREVID'))})
    for d in lista(r, '23'):
        rend.append({'bloco': 'isento', 'codigo': str(campo(d, 'CD_ISENTO') or '').strip(), 'valor': dec(campo(d, 'VR_VALOR'))})
    for d in lista(r, '24'):
        rend.append({'bloco': 'exclusivo', 'codigo': str(campo(d, 'CD_EXCLUSIVO') or '').strip(), 'valor': dec(campo(d, 'VR_VALOR'))})
    for d in lista(r, '88'):
        rend.append({'bloco': 'exclusivo_detalhe', 'codigo': str(campo(d, 'NR_COD') or '').strip(),
                     'dependente_cpf': (str(campo(d, 'NR_CPF_BENEFIC') or '').strip() or None) if str(campo(d, 'IN_TIPO') or '').upper() == 'D' else None,
                     'fonte_doc': str(campo(d, 'NR_PAGADORA') or '').strip() or None,
                     'fonte_nome': str(campo(d, 'NM_NOME') or '').strip() or None, 'valor': dec(campo(d, 'VR_VALOR'))})
    out['rendimentos'] = [x for x in rend if (x.get('valor') or 0) or x.get('fonte_nome')]
    out['dependentes'] = [{'chave': campo(d, 'NR_CHAVE'), 'codigo': str(campo(d, 'CD_DEPEND') or '').strip(),
                           'nome': str(campo(d, 'NM_DEPEND') or '').strip(), 'nascimento': data_ddmmaaaa(campo(d, 'DT_NASCIM')),
                           'cpf': str(campo(d, 'NI_DEPEND', 'NR_CPF_DEPEN') or '').strip() or None}
                          for d in lista(r, '25')]
    out['bens'] = [{'chave': campo(d, 'NR_CHAVE_BEM'), 'grupo': str(campo(d, 'CD_GRUPO_BEM') or '').strip(),
                    'codigo': str(campo(d, 'CD_BEM') or '').strip(), 'exterior': sim(campo(d, 'IN_EXTERIOR')),
                    'pais': str(campo(d, 'CD_PAIS') or '').strip() or None, 'descricao': str(campo(d, 'TX_BEM') or '').strip(),
                    'vr_anterior': dec(campo(d, 'VR_ANTER')), 'vr_atual': dec(campo(d, 'VR_ATUAL')),
                    'cpf_cnpj': str(campo(d, 'NM_CPFCNPJ') or '').strip() or None,
                    'banco': str(campo(d, 'NR_BANCO') or '').strip() or None, 'conta': str(campo(d, 'NR_CONTA') or '').strip() or None,
                    'matricula': str(campo(d, 'MATRIC_IMOV') or '').strip() or None, 'area': campo(d, 'AREA'),
                    'renavam': str(campo(d, 'NR_RENAVAN') or '').strip() or None, 'dt_aquisicao': data_ddmmaaaa(campo(d, 'DT_AQUISICAO'))}
                   for d in lista(r, '27')]
    out['dividas'] = [{'codigo': str(campo(d, 'CD_DIV') or '').strip(), 'descricao': str(campo(d, 'TX_DIV') or '').strip(),
                       'vr_anterior': dec(campo(d, 'VR_ANTER')), 'vr_atual': dec(campo(d, 'VR_ATUAL')),
                       'vr_pago': dec(campo(d, 'VR_PGTO_ANUAL'))} for d in lista(r, '28')]
    out['pagamentos'] = [{'codigo': str(campo(d, 'CD_PAGTO') or '').strip(), 'dependente_chave': campo(d, 'NR_CHAVE_DEPEND'),
                          'beneficiario_doc': str(campo(d, 'NR_BENEF') or '').strip() or None,
                          'beneficiario_nome': str(campo(d, 'NM_BENEF') or '').strip() or None,
                          'valor': dec(campo(d, 'VR_PAGTO')), 'vr_nao_dedutivel': dec(campo(d, 'VR_REDUC')),
                          'tipo': str(campo(d, 'IN_TIPO_PGTO') or '').strip() or None,
                          'descricao': str(campo(d, 'NM_DESCRICAO') or '').strip() or None} for d in lista(r, '26')]
    return out


def resumo_recibo(lido):
    """Do .REC: data/hora da transmissão, local e mensagem."""
    r = lido['registros']
    rc, mc = um(r, 'RC'), um(r, 'MC')
    try:
        quando = datetime(int(rc['ANOREC']), int(rc['MESREC']), int(rc['DIAREC']),
                          int(rc.get('HORAREC') or 0), int(rc.get('MINREC') or 0), int(rc.get('SEGREC') or 0))
    except (KeyError, TypeError, ValueError):
        quando = None
    return {'transmitida_em': quando, 'local': str(rc.get('LOCALREC') or '').strip() or None,
            'mensagem': str(mc.get('DES_MENSAGEM') or '').strip() or None,
            'cpf': str(rc.get('NR_CPF') or '').strip() or None}
