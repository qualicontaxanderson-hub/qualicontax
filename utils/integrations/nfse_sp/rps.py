# -*- coding: utf-8 -*-
"""RPS da prefeitura de São Paulo, leiaute 2 (reforma tributária): montagem e assinatura.

O RPS é o "recibo provisório" que a prefeitura troca pela NFS-e — o papel que a
DPS faz no emissor nacional. Aqui mora só o que é da prefeitura:

* ``string_assinatura``  — a cadeia de posições fixas do manual §4.3.2 (versão 2);
* ``assinar_rps``        — RSA-SHA1 dessa cadeia, em base64 (campo ``Assinatura``);
* ``xml_rps``            — o ``<RPS>`` na ORDEM do XSD ``TiposNFe_v02.xsd``;
* ``pedido_envio_lote`` / ``pedido_envio_rps`` — o envelope de pedido.

ORDEM DOS CAMPOS: a do XSD de PRODUÇÃO, descoberta campo a campo contra o
``TesteEnvioLoteRPS`` em 31/10/2026, porque o zip publicado (v02-3, nov/2025)
já estava atrasado: produção exige ``cLocPrestacao`` antes de ``IBSCBS``,
recusa ``ValorInicialCobrado`` (erro 640: "utilizar ValorFinalCobrado") e
ignora ``PagamentoParceladoAntecipado`` (alerta 648). Fora de ordem o servidor
responde 1001 (XML não compatível com Schema).

Nada aqui lê banco nem decide regra fiscal: recebe um ``RPS`` já preenchido
(pela tela ou pela rotina de honorários, a partir da configuração da empresa).
"""
import base64
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

NS = 'http://www.prefeitura.sp.gov.br/nfe'


def _d(v):
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def _valor(v):
    """tpValor: 2 casas, sem sinal, sem separador de milhar ("1800.00", "0.00")."""
    return str(_d(v).quantize(Decimal('0.01'), ROUND_HALF_UP))


def _aliquota(v):
    """tpAliquota: FRAÇÃO (5% = 0.05), até 4 casas; zero vira "0"."""
    q = _d(v).quantize(Decimal('0.0001'), ROUND_HALF_UP)
    return '0' if q == 0 else str(q.normalize()) if q != q.to_integral() else str(int(q))


def _centavos(v, n=15):
    return str(int((_d(v).quantize(Decimal('0.01'), ROUND_HALF_UP) * 100))).zfill(n)


def _dig(v):
    return re.sub(r'\D', '', str(v or ''))


def _tag(nome, valor):
    if valor is None or valor == '':
        return ''
    return f'<{nome}>{escape(str(valor))}</{nome}>'


@dataclass
class Tomador:
    documento: str = ''             # CPF (11) ou CNPJ (14); vazio = não informado
    nome: str = ''
    tipo_logradouro: str = ''       # R, AV, ES... (até 3)
    logradouro: str = ''
    numero: str = ''
    complemento: str = ''
    bairro: str = ''
    cidade_ibge: str = ''           # 7 dígitos
    uf: str = ''
    cep: str = ''
    email: str = ''
    inscricao_municipal: str = ''   # só tomador de São Paulo (CCM)

    @property
    def indicador(self):
        d = _dig(self.documento)
        return '1' if len(d) == 11 else '2' if len(d) == 14 else '3'


@dataclass
class RPS:
    inscricao_prestador: str
    serie: str
    numero: int
    data_emissao: date
    valor_final: Decimal                # "ValorFinalCobrado": valor total da nota, com tributos.
                                        # ValorInicialCobrado foi DESATIVADO pela prefeitura (erro 640, 31/10/2026).
    codigo_servico: str                 # código municipal de SP (4-5 dígitos)
    discriminacao: str
    tomador: Tomador = field(default_factory=Tomador)
    tributacao: str = 'T'               # T = tributado em São Paulo
    status: str = 'N'
    deducoes: Decimal = Decimal('0')
    pis: Decimal = Decimal('0')
    cofins: Decimal = Decimal('0')
    inss: Decimal = Decimal('0')
    ir: Decimal = Decimal('0')
    csll: Decimal = Decimal('0')
    aliquota: Decimal = Decimal('0')    # fração; ignorada pela prefeitura para o Simples
    iss_retido: bool = False
    municipio_prestacao: str = ''       # IBGE, opcional
    multa: Decimal = None
    juros: Decimal = None
    ipi: Decimal = Decimal('0')
    exigibilidade_suspensa: int = 0
    pagamento_parcelado_antecipado: int = 0
    nbs: str = '999999999'              # 9 dígitos
    c_loc_prestacao: str = '3550308'    # IBGE do local da prestação (leiaute 2 de produção)
    # IBS/CBS declarado (mesmos valores do emissor nacional)
    fin_nfse: int = 0
    ind_final: int = 0
    c_ind_op: str = '100301'
    ind_dest: int = 0
    c_class_trib: str = '000001'

    def string_assinatura(self, versao: int = 1) -> str:
        """Cadeia de posições fixas (manual §4.3.2), sem intermediário.

        Leiaute 1 e 2 só diferem na inscrição municipal: 8 posições no 1,
        12 no 2. O valor (posição 8) é "dos serviços" no 1 e "final cobrado"
        no 2 — para nós é o mesmo número.
        """
        t = self.tomador
        return ''.join([
            _dig(self.inscricao_prestador).zfill(12 if int(versao) == 2 else 8),
            str(self.serie).strip()[:5].ljust(5),
            str(int(self.numero)).zfill(12),
            self.data_emissao.strftime('%Y%m%d'),
            self.tributacao[:1],
            self.status[:1],
            'S' if self.iss_retido else 'N',
            _centavos(self.valor_final),
            _centavos(self.deducoes),
            _dig(self.codigo_servico).zfill(5),
            t.indicador,
            _dig(t.documento).zfill(14) if t.indicador != '3' else '0' * 14,
        ])


def assinar_rps(rps: RPS, chave_priv, versao: int = 1) -> str:
    """RSA-SHA1 da cadeia ASCII, em base64 (campo ``Assinatura`` do RPS).

    Uma função só para o hash e a assinatura, como o manual avisa, para não
    assinar "um hash de um hash".
    """
    ass = chave_priv.sign(rps.string_assinatura(versao).encode('ascii'),
                          padding.PKCS1v15(), hashes.SHA1())
    return base64.b64encode(ass).decode('ascii')


def xml_rps(rps: RPS, assinatura_b64: str, versao: int = 1) -> str:
    """``<RPS>`` com filhos sem namespace, na ordem do XSD.

    ``versao=1`` é o leiaute SEM IBS/CBS: ``ValorServicos`` logo após a
    tributação e nada do bloco da reforma no fim. É o que a prefeitura EXIGE
    para quem está no Simples Nacional (erro 641, 31/10/2026: "Contribuinte
    cadastrado como Simples Nacional na data informada. Deverá ser utilizado o
    leiaute 1") — e é por isso que as notas da Qualicontax vêm sem IBS/CBS.
    ``versao=2`` é o leiaute da reforma, para quem não é do Simples.
    """
    v2 = int(versao) == 2
    t = rps.tomador
    doc = _dig(t.documento)
    partes = [
        '<RPS>',
        _tag('Assinatura', assinatura_b64),
        '<ChaveRPS>',
        _tag('InscricaoPrestador', _dig(rps.inscricao_prestador)),
        _tag('SerieRPS', str(rps.serie).strip()[:5]),
        _tag('NumeroRPS', int(rps.numero)),
        '</ChaveRPS>',
        _tag('TipoRPS', 'RPS'),
        _tag('DataEmissao', rps.data_emissao.isoformat()),
        _tag('StatusRPS', rps.status),
        _tag('TributacaoRPS', rps.tributacao),
        _tag('ValorServicos', _valor(rps.valor_final)) if not v2 else '',
        _tag('ValorDeducoes', _valor(rps.deducoes)),
        _tag('ValorPIS', _valor(rps.pis)),
        _tag('ValorCOFINS', _valor(rps.cofins)),
        _tag('ValorINSS', _valor(rps.inss)),
        _tag('ValorIR', _valor(rps.ir)),
        _tag('ValorCSLL', _valor(rps.csll)),
        _tag('CodigoServico', int(_dig(rps.codigo_servico))),
        _tag('AliquotaServicos', _aliquota(rps.aliquota)),
        _tag('ISSRetido', 'true' if rps.iss_retido else 'false'),
    ]
    if doc:
        partes += ['<CPFCNPJTomador>', _tag('CNPJ' if len(doc) == 14 else 'CPF', doc), '</CPFCNPJTomador>']
        partes.append(_tag('InscricaoMunicipalTomador', _dig(t.inscricao_municipal)))
    partes.append(_tag('RazaoSocialTomador', (t.nome or '')[:75]))
    if any([t.logradouro, t.cidade_ibge, t.cep]):
        partes += [
            '<EnderecoTomador>',
            _tag('TipoLogradouro', (t.tipo_logradouro or '')[:3]),
            _tag('Logradouro', (t.logradouro or '')[:50]),
            _tag('NumeroEndereco', (t.numero or '')[:10]),
            _tag('ComplementoEndereco', (t.complemento or '')[:30]),
            _tag('Bairro', (t.bairro or '')[:30]),
            _tag('Cidade', _dig(t.cidade_ibge) or None),
            _tag('UF', (t.uf or '')[:2]),
            _tag('CEP', int(_dig(t.cep)) if _dig(t.cep) else None),
            '</EnderecoTomador>',
        ]
    partes += [
        _tag('EmailTomador', (t.email or '')[:75]),
        _tag('Discriminacao', (rps.discriminacao or '')[:2000]),
        _tag('MunicipioPrestacao', _dig(rps.municipio_prestacao) or None),
    ]
    if not v2:
        partes.append('</RPS>')
        return ''.join(partes)
    partes += [
        _tag('ValorFinalCobrado', _valor(rps.valor_final)),
        _tag('ValorMulta', _valor(rps.multa) if rps.multa is not None else None),
        _tag('ValorJuros', _valor(rps.juros) if rps.juros is not None else None),
        _tag('ValorIPI', _valor(rps.ipi)),
        _tag('ExigibilidadeSuspensa', int(rps.exigibilidade_suspensa)),
        # obrigatório no XSD, mas a prefeitura avisa (alerta 648) que ignora o valor
        _tag('PagamentoParceladoAntecipado', int(rps.pagamento_parcelado_antecipado)),
        _tag('NBS', _dig(rps.nbs).zfill(9)),
        # Esquema de PRODUÇÃO (31/10/2026) exige o local da prestação aqui,
        # entre atvEvento e IBSCBS; o zip v02-3 de nov/2025 ainda não tinha.
        _tag('cLocPrestacao', _dig(rps.municipio_prestacao) or _dig(rps.c_loc_prestacao) or None),
        '<IBSCBS>',
        _tag('finNFSe', int(rps.fin_nfse)),
        _tag('indFinal', int(rps.ind_final)),
        _tag('cIndOp', _dig(rps.c_ind_op).zfill(6)),
        _tag('indDest', int(rps.ind_dest)),
        '<valores><trib><gIBSCBS>',
        _tag('cClassTrib', _dig(rps.c_class_trib).zfill(6)),
        '</gIBSCBS></trib></valores>',
        '</IBSCBS>',
        '</RPS>',
    ]
    return ''.join(partes)


def _remetente(cnpj):
    d = _dig(cnpj)
    return f'<CPFCNPJRemetente>{"<CNPJ>" if len(d) == 14 else "<CPF>"}{d}{"</CNPJ>" if len(d) == 14 else "</CPF>"}</CPFCNPJRemetente>'


def pedido_envio_lote(cnpj_remetente: str, rps_list: list, dt_inicio: date, dt_fim: date,
                      versao: int = 1, transacao: bool = True) -> str:
    """``PedidoEnvioLoteRPS`` (também é o pedido do ``TesteEnvioLoteRPS``). Até 50 RPS.

    ``rps_list`` é uma lista de pares ``(RPS, xml_assinado)``: o cabeçalho do
    leiaute 1 exige os totais do lote (``ValorTotalServicos`` e
    ``ValorTotalDeducoes``), que o leiaute 2 aboliu (manual 3.3.3).
    """
    if not 1 <= len(rps_list) <= 50:
        raise ValueError('O lote aceita de 1 a 50 RPS.')
    totais = ''
    if int(versao) == 1:
        totais = (f'<ValorTotalServicos>{_valor(sum(_d(r.valor_final) for r, _ in rps_list))}</ValorTotalServicos>'
                  f'<ValorTotalDeducoes>{_valor(sum(_d(r.deducoes) for r, _ in rps_list))}</ValorTotalDeducoes>')
    return (
        f'<nfe:PedidoEnvioLoteRPS xmlns:nfe="{NS}">'
        f'<Cabecalho Versao="{int(versao)}">'
        f'{_remetente(cnpj_remetente)}'
        f'<transacao>{"true" if transacao else "false"}</transacao>'
        f'<dtInicio>{dt_inicio.isoformat()}</dtInicio>'
        f'<dtFim>{dt_fim.isoformat()}</dtFim>'
        f'<QtdRPS>{len(rps_list)}</QtdRPS>'
        f'{totais}'
        f'</Cabecalho>'
        + ''.join(x for _, x in rps_list) +
        f'</nfe:PedidoEnvioLoteRPS>'
    )


def pedido_envio_rps(cnpj_remetente: str, rps_xml: str, versao: int = 1) -> str:
    """``PedidoEnvioRPS``: uma nota, resposta na hora com a chave da NFS-e."""
    return (
        f'<nfe:PedidoEnvioRPS xmlns:nfe="{NS}">'
        f'<Cabecalho Versao="{int(versao)}">{_remetente(cnpj_remetente)}</Cabecalho>'
        f'{rps_xml}'
        f'</nfe:PedidoEnvioRPS>'
    )
