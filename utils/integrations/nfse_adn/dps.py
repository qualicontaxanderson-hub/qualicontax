# -*- coding: utf-8 -*-
"""DPS (Declaração de Prestação de Serviços) do emissor nacional — montagem e assinatura.

A DPS é o que o contribuinte declara; o Sefin Nacional devolve a NFS-e. Aqui
mora só a DPS: ordem dos campos do leiaute 1.01 (ANEXO I, SEFIN/ADN, 12/02/2026),
o identificador de 45 posições e a assinatura XML.

ORDEM E PRESENÇA DOS CAMPOS foram conferidas contra uma DPS REAL gerada pelo
Sefin Nacional (NFS-e de 30/10/2026 de um prestador do Simples): os grupos
opcionais que a Qualicontax não usa (subst, interm, comExt, obra, atvEvento,
vDescCondIncond, vDedRed, tribFed, BM, exigSusp) ficam de fora, e os que o
leiaute marca 1-1 entram sempre.

ASSINATURA (igual à do DFe e à da prefeitura): enveloped, RSA-SHA1, SHA-1,
C14N 20010315, referência ``#<Id do infDPS>``. Reusa ``dfe_manifesto.assinar_evento``,
que já faz exatamente isso para o evento da NF-e.

Nada aqui lê banco nem decide regra fiscal: recebe uma ``DPS`` já preenchida
pela tela / rotina a partir de ``nfse_emissao_config``.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

NS = 'http://www.sped.fazenda.gov.br/nfse'
VERSAO_LEIAUTE = '1.01'
VER_APLIC = 'Qualicontax 1.0'


def _d(v):
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def _v2(v):
    """1-15V2: duas casas, ponto, sem milhar."""
    return str(_d(v).quantize(Decimal('0.01'), ROUND_HALF_UP))


def _dig(v):
    return re.sub(r'\D', '', str(v or ''))


def _tag(nome, valor):
    if valor is None or valor == '':
        return ''
    return f'<{nome}>{escape(str(valor))}</{nome}>'


@dataclass
class Pessoa:
    documento: str = ''         # CPF ou CNPJ
    nome: str = ''
    inscricao_municipal: str = ''
    cidade_ibge: str = ''
    cep: str = ''
    logradouro: str = ''        # tipo + nome ("Avenida Quarta Avenida")
    numero: str = ''
    complemento: str = ''
    bairro: str = ''
    fone: str = ''
    email: str = ''

    def _doc_tag(self):
        d = _dig(self.documento)
        return _tag('CNPJ' if len(d) == 14 else 'CPF', d) if d else ''

    def _end_xml(self):
        # O grupo <end> é opcional, mas quando vai precisa estar inteiro: xLgr, nro e xBairro
        # são obrigatórios no esquema (E1235 se faltar). Sem rua ou bairro, a nota vai sem endereço.
        if not (_dig(self.cidade_ibge) and _dig(self.cep) and (self.logradouro or '').strip() and (self.bairro or '').strip()):
            return ''
        return ('<end><endNac>' + _tag('cMun', _dig(self.cidade_ibge)) + _tag('CEP', _dig(self.cep).zfill(8))
                + '</endNac>' + _tag('xLgr', self.logradouro.strip()[:255]) + _tag('nro', ((self.numero or '').strip() or 'S/N')[:60])
                + _tag('xCpl', (self.complemento or '')[:156] or None) + _tag('xBairro', self.bairro.strip()[:60])
                + '</end>')


@dataclass
class DPS:
    # identificação
    tp_amb: int                         # 1 produção, 2 produção restrita
    serie: str
    numero: int
    dh_emissao: datetime                # com fuso (ex.: -03:00)
    competencia: date
    loc_emissao: str                    # IBGE do emitente (cLocEmi)
    # prestador (emitente, tpEmit = 1)
    prestador: Pessoa
    op_simp_nac: int                    # 1 não optante, 2 MEI, 3 ME/EPP
    reg_ap_trib_sn: int = None          # só quando opSimpNac = 3
    reg_esp_trib: int = 0
    # tomador
    tomador: Pessoa = field(default_factory=Pessoa)
    # serviço
    loc_prestacao: str = ''             # IBGE; vazio = loc_emissao
    c_trib_nac: str = ''                # 6 dígitos
    c_trib_mun: str = ''                # 3 dígitos, opcional
    descricao: str = ''
    c_nbs: str = ''                     # 9 dígitos, opcional
    c_int_contrib: str = ''             # código interno (ex.: nº do título)
    # valores
    valor_servico: Decimal = Decimal('0')
    trib_issqn: int = 1                 # 1 tributável, 2 imunidade, 3 exportação, 4 não incidência
    tp_ret_issqn: int = 1               # 1 não retido, 2 retido pelo tomador, 3 pelo intermediário
    p_aliq: Decimal = None              # % (5.00); None omite — o Simples não informa
    tot_trib_fed: Decimal = Decimal('0')
    tot_trib_est: Decimal = Decimal('0')
    tot_trib_mun: Decimal = Decimal('0')
    # retenções federais (tribFed) — só entram no XML quando > 0. Qualicontax
    # (Simples) não retém; o grupo existe para a nota que precisar. Ordem do XSD
    # NT 009: piscofins{CST, vPis, vCofins, tpRetPisCofins}, vRetCP, vRetIRRF,
    # vRetCSLL. NÃO provado no Sefin (nenhuma nota com retenção foi enviada).
    ret_pis: Decimal = Decimal('0')
    ret_cofins: Decimal = Decimal('0')
    ret_inss: Decimal = Decimal('0')      # vRetCP
    ret_irrf: Decimal = Decimal('0')
    ret_csll: Decimal = Decimal('0')
    # IBS/CBS declarado
    fin_nfse: int = 0
    ind_final: int = 0
    c_ind_op: str = '100301'
    ind_dest: int = 0
    cst: str = '000'
    c_class_trib: str = '000001'
    ver_aplic: str = VER_APLIC
    # '1.01'  = esquema de PRODUÇÃO (XSD de 09/02/2026): finNFSe dentro do
    #           IBSCBS, xDescServ antes do cNBS, CST/cClassTrib dentro de gIBSCBS.
    # 'nt009' = esquema da PRODUÇÃO RESTRITA (XSD de 20/10/2026, NT 009):
    #           finNFSe logo após tpEmit, xDescServ por último no cServ,
    #           CST/cClassTrib direto em IBSCBS/valores/trib.
    # O atributo versao="1.01" é o mesmo nos dois; só o servidor sabe qual
    # aceita. Quando a produção virar, muda-se LEIAUTE_POR_AMBIENTE.
    leiaute: str = '1.01'

    @property
    def id(self) -> str:
        """``DPS`` + cLocEmi(7) + tpInsc(1: 1 CPF, 2 CNPJ) + inscrição(14) + série(5) + nDPS(15) = 45."""
        doc = _dig(self.prestador.documento)
        tp = '2' if len(doc) == 14 else '1'
        return ('DPS' + _dig(self.loc_emissao).zfill(7) + tp + doc.zfill(14)
                + _dig(self.serie).zfill(5) + str(int(self.numero)).zfill(15))


LEIAUTE_POR_AMBIENTE = {1: '1.01', 2: 'nt009'}   # 31/10/2026


def _trib_fed(d: DPS) -> str:
    """``<tribFed>`` só com o que for maior que zero; vazio quando não há retenção."""
    pis, cofins = _d(d.ret_pis), _d(d.ret_cofins)
    inss, irrf, csll = _d(d.ret_inss), _d(d.ret_irrf), _d(d.ret_csll)
    if not any(v > 0 for v in (pis, cofins, inss, irrf, csll)):
        return ''
    partes = ['<tribFed>']
    if pis > 0 or cofins > 0:
        # CST 01 = operação tributável; tpRetPisCofins 1 = retidos
        partes += ['<piscofins>', _tag('CST', '01'), _tag('vPis', _v2(pis)), _tag('vCofins', _v2(cofins)),
                   _tag('tpRetPisCofins', 1), '</piscofins>']
    partes += [_tag('vRetCP', _v2(inss)) if inss > 0 else '',
               _tag('vRetIRRF', _v2(irrf)) if irrf > 0 else '',
               _tag('vRetCSLL', _v2(csll)) if csll > 0 else '',
               '</tribFed>']
    return ''.join(partes)


def xml_dps(d: DPS) -> str:
    """``<DPS>`` sem assinatura, no namespace nacional, na ordem do leiaute 1.01."""
    p, t = d.prestador, d.tomador
    nt009 = d.leiaute == 'nt009'
    dh = d.dh_emissao.isoformat(timespec='seconds')
    partes = [
        f'<DPS versao="{VERSAO_LEIAUTE}" xmlns="{NS}">',
        f'<infDPS Id="{d.id}">',
        _tag('tpAmb', int(d.tp_amb)),
        _tag('dhEmi', dh),
        _tag('verAplic', d.ver_aplic[:20]),
        _tag('serie', int(_dig(d.serie))),
        _tag('nDPS', int(d.numero)),
        _tag('dCompet', d.competencia.isoformat()),
        _tag('tpEmit', 1),
        _tag('finNFSe', int(d.fin_nfse)) if nt009 else '',
        _tag('cLocEmi', _dig(d.loc_emissao)),
        '<prest>',
        p._doc_tag(),
        _tag('IM', _dig(p.inscricao_municipal) or None),
        # xNome/end do prestador: o Sefin preenche pelo cadastro do CNPJ; a DPS real não os traz
        _tag('fone', _dig(p.fone) or None),
        _tag('email', (p.email or '')[:80] or None),
        '<regTrib>',
        _tag('opSimpNac', int(d.op_simp_nac)),
        _tag('regApTribSN', int(d.reg_ap_trib_sn)) if d.reg_ap_trib_sn is not None else '',
        _tag('regEspTrib', int(d.reg_esp_trib)),
        '</regTrib>',
        '</prest>',
    ]
    if _dig(t.documento) or t.nome:
        partes += ['<toma>', t._doc_tag(), _tag('IM', _dig(t.inscricao_municipal) or None),
                   _tag('xNome', (t.nome or '')[:150]), t._end_xml(),
                   _tag('fone', _dig(t.fone) or None), _tag('email', (t.email or '')[:80] or None), '</toma>']
    partes += [
        '<serv>',
        '<locPrest>', _tag('cLocPrestacao', _dig(d.loc_prestacao) or _dig(d.loc_emissao)), '</locPrest>',
        '<cServ>',
        _tag('cTribNac', _dig(d.c_trib_nac).zfill(6)),
        _tag('cTribMun', _dig(d.c_trib_mun).zfill(3) if _dig(d.c_trib_mun) else None),
        _tag('xDescServ', (d.descricao or '')[:1000]) if not nt009 else '',
        _tag('cNBS', _dig(d.c_nbs).zfill(9) if _dig(d.c_nbs) else None),
        _tag('cIntContrib', (d.c_int_contrib or '')[:20] or None),
        _tag('xDescServ', (d.descricao or '')[:1000]) if nt009 else '',
        '</cServ>',
        '</serv>',
        '<valores>',
        '<vServPrest>', _tag('vServ', _v2(d.valor_servico)), '</vServPrest>',
        '<trib>',
        '<tribMun>',
        _tag('tribISSQN', int(d.trib_issqn)),
        _tag('tpRetISSQN', int(d.tp_ret_issqn)),
        _tag('pAliq', _v2(d.p_aliq)) if d.p_aliq is not None else '',
        '</tribMun>',
        _trib_fed(d),
        '<totTrib><vTotTrib>',
        _tag('vTotTribFed', _v2(d.tot_trib_fed)),
        _tag('vTotTribEst', _v2(d.tot_trib_est)),
        _tag('vTotTribMun', _v2(d.tot_trib_mun)),
        '</vTotTrib></totTrib>',
        '</trib>',
        '</valores>',
        '<IBSCBS>',
        _tag('finNFSe', int(d.fin_nfse)) if not nt009 else '',
        _tag('indFinal', int(d.ind_final)),
        _tag('cIndOp', _dig(d.c_ind_op).zfill(6)),
        _tag('indDest', int(d.ind_dest)),
        '<valores><trib>' + ('' if nt009 else '<gIBSCBS>'),
        _tag('CST', _dig(d.cst).zfill(3)),
        _tag('cClassTrib', _dig(d.c_class_trib).zfill(6)),
        ('' if nt009 else '</gIBSCBS>') + '</trib></valores>',
        '</IBSCBS>',
        '</infDPS>',
        '</DPS>',
    ]
    return ''.join(partes)


def assinar_dps(xml: str, id_dps: str, chave_priv, x509) -> str:
    """DPS assinada (Signature dentro de ``<DPS>``, referência ``#Id`` do infDPS)."""
    from utils.integrations.dfe_manifesto import assinar_evento
    # O Sefin exige a declaração XML com UTF-8 (E1229 sem ela, 31/10/2026).
    return '<?xml version="1.0" encoding="UTF-8"?>' + assinar_evento(xml, id_dps, chave_priv, x509)
