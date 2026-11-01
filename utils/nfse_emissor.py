# -*- coding: utf-8 -*-
"""Emitir uma NFS-e a partir do que a tela mandou — o passo a passo inteiro.

Dois caminhos, escolhidos pela SÉRIE (cada série pertence a um emissor):

* **nacional** — DPS assinada, POST no Sefin Nacional (``nfse_adn.emissao``).
  Provado em 31/10/2026 na produção restrita (série 99 nº 7, autorizada).
* **sp** — RPS assinado, ``EnvioRPS`` na prefeitura de São Paulo
  (``nfse_sp``). O lote de TESTE passou (série QC nº 1); a emissão REAL ainda
  não foi disparada por aqui.

Ordem que não muda: (1) reservar o número e gravar o rascunho na mesma
transação; (2) montar e assinar; (3) enviar; (4) gravar a resposta — autorizada,
rejeitada ou erro de transporte. Depois do passo 1 a linha de ``nfse_emitidas``
existe SEMPRE: uma rejeição fica registrada com o motivo, e um número nunca
se perde sem rastro.

O XML da NFS-e autorizada vai para o Dropbox só em produção (ambiente 1), na
mesma pasta NFSE da captura — a captura vai encontrar a mesma chave depois e
sobrescrever com o mesmo conteúdo. Nota de teste (ambiente 2) fica só no banco
(``raw_resposta`` traz o XML gzipado que o Sefin devolveu).
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from lxml import etree

from utils.db_helper import execute_query
from utils.integrations.nfse_adn import numeracao

logger = logging.getLogger(__name__)

FUSO = timezone(timedelta(hours=-3))


class PedidoInvalido(Exception):
    """Falta ou erro em campo do pedido — a tela mostra a mensagem como veio."""


def _dig(v):
    return re.sub(r'\D', '', str(v or ''))


def _dec(v, nome, minimo=None):
    try:
        d = Decimal(str(v or '0').replace('.', '').replace(',', '.')) if isinstance(v, str) and ',' in str(v) \
            else Decimal(str(v or '0'))
    except (InvalidOperation, ValueError):
        raise PedidoInvalido(f'{nome}: valor inválido ({v!r}).')
    if minimo is not None and d < minimo:
        raise PedidoInvalido(f'{nome}: precisa ser pelo menos {minimo}.')
    return d.quantize(Decimal('0.01'))


@dataclass
class Pedido:
    """O que a tela manda. Tudo que o padrão da empresa preenche pode vir alterado."""
    empresa_id: int
    serie: str
    competencia: date
    # tomador
    tomador_doc: str
    tomador_nome: str
    tomador_ibge: str
    tomador_cep: str = ''
    tomador_logradouro: str = ''
    tomador_numero: str = ''
    tomador_complemento: str = ''
    tomador_bairro: str = ''
    tomador_uf: str = ''
    tomador_email: str = ''
    tomador_fone: str = ''
    tomador_im: str = ''
    tomador_cliente_id: int = None
    # serviço
    descricao: str = ''
    cod_trib_nac: str = ''
    cod_trib_mun: str = ''
    cod_nbs: str = ''
    cod_servico_sp: str = ''
    loc_prestacao: str = ''
    # valores
    valor_servico: Decimal = Decimal('0')
    trib_issqn: int = 1
    iss_retido: bool = False
    aliquota_iss: Decimal = None
    ret_pis: Decimal = Decimal('0')
    ret_cofins: Decimal = Decimal('0')
    ret_inss: Decimal = Decimal('0')
    ret_irrf: Decimal = Decimal('0')
    ret_csll: Decimal = Decimal('0')
    # reforma
    ibscbs_cst: str = '000'
    ibscbs_cclasstrib: str = '000001'
    ibscbs_cind_op: str = '100301'
    ibscbs_ind_dest: int = 0
    ibscbs_ind_final: int = 0
    fin_nfse: int = 0
    # ligação
    fin_titulo_id: int = None
    origem: str = 'manual'
    extras: dict = field(default_factory=dict)

    @property
    def total_retencoes(self):
        return sum((self.ret_pis, self.ret_cofins, self.ret_inss, self.ret_irrf, self.ret_csll), Decimal('0'))


def pedido_do_form(f, empresa_id, cfg):
    """Monta o ``Pedido`` a partir do formulário (``request.form``), validando o essencial."""
    def g(k, padrao=''):
        v = f.get(k)
        return (v.strip() if isinstance(v, str) else v) if v not in (None, '') else padrao

    serie = g('serie').upper()
    if not serie:
        raise PedidoInvalido('Escolha a série da nota.')
    try:
        comp = date.fromisoformat(g('competencia') or date.today().isoformat())
    except ValueError:
        raise PedidoInvalido('Competência inválida.')
    doc = _dig(g('tomador_doc'))
    if len(doc) not in (11, 14):
        raise PedidoInvalido('CNPJ ou CPF do tomador inválido.')
    nome = g('tomador_nome')
    if not nome:
        raise PedidoInvalido('Informe o nome do tomador.')
    ibge = _dig(g('tomador_ibge'))
    if len(ibge) != 7:
        raise PedidoInvalido('Município do tomador: informe o código IBGE de 7 dígitos.')
    cep = _dig(g('tomador_cep'))
    if cep and len(cep) != 8:
        raise PedidoInvalido('CEP do tomador inválido.')
    if cep and not g('tomador_logradouro'):
        raise PedidoInvalido('Endereço do tomador incompleto: o cadastro dele está sem a rua. '
                             'Preencha a rua (o CEP costuma trazer) ou apague o CEP para a nota sair sem endereço.')
    if cep and not g('tomador_bairro'):
        raise PedidoInvalido('Endereço do tomador incompleto: falta o bairro. '
                             'Preencha o bairro ou apague o CEP para a nota sair sem endereço.')
    valor = _dec(g('valor_servico'), 'Valor do serviço', Decimal('0.01'))
    descricao = g('descricao')
    if not descricao:
        raise PedidoInvalido('Informe a descrição do serviço.')
    aliq = g('aliquota_iss')
    aliq = _dec(aliq, 'Alíquota do ISS', Decimal('0')) if aliq not in ('', None) else None
    tid = g('fin_titulo_id')
    cid = g('tomador_cliente_id')
    return Pedido(
        empresa_id=int(empresa_id), serie=serie, competencia=comp,
        tomador_doc=doc, tomador_nome=nome[:150], tomador_ibge=ibge, tomador_cep=cep,
        tomador_logradouro=g('tomador_logradouro')[:255], tomador_numero=(g('tomador_numero') or 'S/N')[:60],
        tomador_complemento=g('tomador_complemento')[:156], tomador_bairro=g('tomador_bairro')[:60],
        tomador_uf=g('tomador_uf')[:2].upper(), tomador_email=g('tomador_email')[:80],
        tomador_fone=_dig(g('tomador_fone')), tomador_im=_dig(g('tomador_im')),
        tomador_cliente_id=int(cid) if str(cid).isdigit() else None,
        descricao=descricao[:1000],
        cod_trib_nac=_dig(g('cod_trib_nac', cfg.get('cod_trib_nac'))),
        cod_trib_mun=_dig(g('cod_trib_mun', cfg.get('cod_trib_mun') or '')),
        cod_nbs=_dig(g('cod_nbs', cfg.get('cod_nbs') or '')),
        cod_servico_sp=_dig(g('cod_servico_sp', cfg.get('cod_servico_sp') or '')),
        loc_prestacao=_dig(g('loc_prestacao', cfg.get('municipio_ibge') or '')),
        valor_servico=valor,
        trib_issqn=int(g('trib_issqn', 1) or 1),
        iss_retido=g('iss_retido') in ('1', 'on', 'true'),
        aliquota_iss=aliq,
        ret_pis=_dec(g('ret_pis'), 'PIS retido', Decimal('0')), ret_cofins=_dec(g('ret_cofins'), 'COFINS retida', Decimal('0')),
        ret_inss=_dec(g('ret_inss'), 'INSS retido', Decimal('0')), ret_irrf=_dec(g('ret_irrf'), 'IR retido', Decimal('0')),
        ret_csll=_dec(g('ret_csll'), 'CSLL retida', Decimal('0')),
        ibscbs_cst=_dig(g('ibscbs_cst', cfg.get('ibscbs_cst') or '000')).zfill(3),
        ibscbs_cclasstrib=_dig(g('ibscbs_cclasstrib', cfg.get('ibscbs_cclasstrib') or '000001')).zfill(6),
        ibscbs_cind_op=_dig(g('ibscbs_cind_op', cfg.get('ibscbs_cind_op') or '100301')).zfill(6),
        ibscbs_ind_dest=int(g('ibscbs_ind_dest', cfg.get('ibscbs_ind_dest') or 0) or 0),
        ibscbs_ind_final=int(g('ibscbs_ind_final', cfg.get('ibscbs_ind_final') or 0) or 0),
        fin_titulo_id=int(tid) if str(tid).isdigit() else None,
    )


def emissor_da_serie(cfg, serie):
    """A série diz o caminho: a série da prefeitura vai pelo RPS; as demais, pelo nacional."""
    return 'sp' if cfg.get('serie_sp') and str(serie).upper() == str(cfg['serie_sp']).upper() else 'nacional'


# ---------------------------------------------------------------------------
# A emissão
# ---------------------------------------------------------------------------
def emitir(pedido: Pedido, cfg: dict, usuario_id=None) -> dict:
    """Executa a emissão e devolve a linha final de ``nfse_emitidas``.

    Levanta ``PedidoInvalido`` antes de reservar número (nada gravado) e
    ``numeracao.SerieNaoCadastrada``/``SerieInativa`` idem. Depois da reserva
    NÃO levanta: qualquer falha vira ``situacao='erro'`` na linha.
    """
    from utils.integrations.nfse_adn import client as adn
    emissor = emissor_da_serie(cfg, pedido.serie)
    if emissor == 'sp' and not _dig(cfg.get('inscricao_municipal')):
        raise PedidoInvalido('A prefeitura de São Paulo exige a inscrição municipal da empresa na configuração.')
    if emissor == 'sp' and not pedido.cod_servico_sp:
        raise PedidoInvalido('Informe o código de serviço da prefeitura (ex.: 3476).')
    if emissor == 'nacional' and len(pedido.cod_trib_nac) != 6:
        raise PedidoInvalido('Código de tributação nacional precisa ter 6 dígitos (ex.: 171901).')

    cert = adn.resolver_certificado(pedido.empresa_id)      # SemCertificado se não houver
    dps_json = _pedido_json(pedido)
    emitida_id, numero = numeracao.reservar_dps(
        pedido.empresa_id, pedido.serie, cnpj_emitente=cert.cnpj, criado_por=usuario_id,
        origem=pedido.origem, fin_titulo_id=pedido.fin_titulo_id, dps_json=dps_json)
    agora = datetime.now(FUSO).replace(microsecond=0)
    _gravar_rascunho(emitida_id, pedido, cfg, emissor, agora)

    try:
        if emissor == 'nacional':
            _enviar_nacional(emitida_id, numero, pedido, cfg, cert, agora)
        else:
            _enviar_sp(emitida_id, numero, pedido, cfg, agora)
    except Exception as exc:                      # rede, certificado, bug — nunca some
        logger.exception('[nfse-emissao] falha ao enviar nfse_emitidas id %s', emitida_id)
        execute_query(
            "UPDATE nfse_emitidas SET situacao = 'erro', erro_ultimo = %s, tentativas = tentativas + 1, "
            " enviado_em = NOW(), atualizado_em = NOW() WHERE id = %s",
            (f'{exc.__class__.__name__}: {exc}'[:2000], emitida_id))
    linha = execute_query('SELECT * FROM nfse_emitidas WHERE id = %s', (emitida_id,), fetch=True, fetch_one=True)
    if linha and linha['situacao'] == 'autorizada' and pedido.fin_titulo_id:
        execute_query('UPDATE fin_titulos SET nfse_id = %s WHERE id = %s AND nfse_id IS NULL',
                      (emitida_id, pedido.fin_titulo_id))
    return linha


def _pedido_json(p: Pedido) -> str:
    d = {k: (str(v) if isinstance(v, (Decimal, date)) else v) for k, v in p.__dict__.items() if k != 'extras'}
    return json.dumps(d, ensure_ascii=False)


def _gravar_rascunho(emitida_id, p: Pedido, cfg, emissor, agora):
    execute_query(
        """UPDATE nfse_emitidas SET emissor = %s, dh_emissao = %s, competencia = %s, loc_emissao = %s,
                  loc_prestacao = %s, op_simp_nac = %s, reg_ap_trib_sn = %s, reg_esp_trib = %s,
                  tomador_doc = %s, tomador_nome = %s, tomador_municipio = %s, tomador_email = %s,
                  cod_trib_nac = %s, cod_trib_mun = %s, cod_nbs = %s, descricao = %s,
                  valor_servico = %s, iss_retido = %s, aliquota_iss = %s, total_retencoes = %s,
                  ibscbs_cst = %s, ibscbs_cclasstrib = %s, ibscbs_fin_nfse = %s, ibscbs_cind_op = %s,
                  ibscbs_ind_dest = %s, atualizado_em = NOW()
            WHERE id = %s""",
        (emissor, agora.replace(tzinfo=None), p.competencia, _dig(cfg.get('municipio_ibge')) or None,
         p.loc_prestacao or _dig(cfg.get('municipio_ibge')) or None,
         cfg.get('op_simp_nac'), cfg.get('reg_ap_trib_sn'), cfg.get('reg_esp_trib'),
         p.tomador_doc, p.tomador_nome, p.tomador_ibge, p.tomador_email or None,
         p.cod_trib_nac or None, p.cod_trib_mun or None, p.cod_nbs or None, p.descricao,
         p.valor_servico, 1 if p.iss_retido else 0, p.aliquota_iss, p.total_retencoes or None,
         p.ibscbs_cst, p.ibscbs_cclasstrib, p.fin_nfse, p.ibscbs_cind_op, p.ibscbs_ind_dest, emitida_id))


# --- nacional ---------------------------------------------------------------
def _enviar_nacional(emitida_id, numero, p: Pedido, cfg, cert, agora):
    from utils.integrations.nfse_adn import client as adn, dps as D, emissao as E
    sess = adn.abrir_sessao(cert)
    x509, chave, _ = sess.material
    amb = execute_query('SELECT ambiente FROM nfse_emitidas WHERE id = %s', (emitida_id,),
                        fetch=True, fetch_one=True)['ambiente']
    d = D.DPS(
        tp_amb=int(amb), serie=p.serie, numero=numero, dh_emissao=agora, competencia=p.competencia,
        loc_emissao=_dig(cfg['municipio_ibge']),
        # IM do prestador NÃO vai: São Paulo não tem cadastro complementar no CNC (E0120, 31/10/2026).
        prestador=D.Pessoa(documento=cert.cnpj, email=cfg.get('email_remetente') or ''),
        op_simp_nac=cfg['op_simp_nac'], reg_ap_trib_sn=cfg.get('reg_ap_trib_sn'), reg_esp_trib=cfg.get('reg_esp_trib') or 0,
        tomador=D.Pessoa(documento=p.tomador_doc, nome=p.tomador_nome, inscricao_municipal=p.tomador_im,
                         cidade_ibge=p.tomador_ibge, cep=p.tomador_cep, logradouro=p.tomador_logradouro,
                         numero=p.tomador_numero, complemento=p.tomador_complemento, bairro=p.tomador_bairro,
                         fone=p.tomador_fone, email=p.tomador_email),
        loc_prestacao=p.loc_prestacao or _dig(cfg['municipio_ibge']),
        c_trib_nac=p.cod_trib_nac, c_trib_mun=p.cod_trib_mun, descricao=p.descricao, c_nbs=p.cod_nbs,
        c_int_contrib=str(p.fin_titulo_id) if p.fin_titulo_id else '',
        valor_servico=p.valor_servico, trib_issqn=p.trib_issqn,
        tp_ret_issqn=2 if p.iss_retido else 1,
        # o Simples não informa alíquota; fora do Simples a alíquota declarada vai
        p_aliq=p.aliquota_iss if (cfg.get('op_simp_nac') == 1 and p.aliquota_iss is not None) else None,
        ret_pis=p.ret_pis, ret_cofins=p.ret_cofins, ret_inss=p.ret_inss, ret_irrf=p.ret_irrf, ret_csll=p.ret_csll,
        fin_nfse=p.fin_nfse, ind_final=p.ibscbs_ind_final, c_ind_op=p.ibscbs_cind_op, ind_dest=p.ibscbs_ind_dest,
        cst=p.ibscbs_cst, c_class_trib=p.ibscbs_cclasstrib,
        leiaute=D.LEIAUTE_POR_AMBIENTE[int(amb)],
    )
    xml = D.xml_dps(d)
    assinado = D.assinar_dps(xml, d.id, chave, x509)
    execute_query('UPDATE nfse_emitidas SET dps_id = %s, xml_dps = %s WHERE id = %s', (d.id, assinado, emitida_id))

    r = E.emitir(sess, assinado, int(amb))
    bruto = json.dumps(r.bruto, ensure_ascii=False)
    if not r.ok:
        execute_query(
            "UPDATE nfse_emitidas SET situacao = 'rejeitada', raw_resposta = %s, erro_ultimo = %s, "
            " tentativas = tentativas + 1, enviado_em = NOW(), atualizado_em = NOW() WHERE id = %s",
            (bruto, _texto_erros(r.erros), emitida_id))
        return
    res = _resumo_nfse_nacional(r.xml_nfse)
    caminho = _arquivar(r.xml_nfse, p.empresa_id, r.chave_acesso, agora) if int(amb) == 1 else None
    execute_query(
        """UPDATE nfse_emitidas SET situacao = 'autorizada', chave_acesso = %s, numero_nfse = %s,
                  data_processamento = %s, cstat = %s, xmotivo = %s, raw_resposta = %s, erro_ultimo = NULL,
                  valor_liquido = %s, ibscbs_bc = %s, ibs_uf_aliq_efet = %s, ibs_uf_valor = %s,
                  ibs_mun_aliq_efet = %s, ibs_mun_valor = %s, ibs_total = %s, cbs_aliq_efet = %s, cbs_valor = %s,
                  valor_total_nf = %s, xml_nfse_path = %s, tentativas = tentativas + 1,
                  enviado_em = NOW(), autorizada_em = NOW(), atualizado_em = NOW()
            WHERE id = %s""",
        (r.chave_acesso, res.get('nNFSe'), res.get('dhProc'), res.get('cStat'),
         '; '.join(f'{c} {d}' for c, d, _ in r.alertas)[:500] or None, bruto,
         res.get('vLiq'), res.get('vBC'), res.get('pAliqEfetUF'), res.get('vIBSUF'), res.get('pAliqEfetMun'),
         res.get('vIBSMun'), res.get('vIBSTot'), res.get('pAliqEfetCBS'), res.get('vCBS'), res.get('vTotNF'),
         caminho, emitida_id))


def _texto_erros(erros):
    return ' | '.join(f'{c}: {d}' + (f' ({x})' if x else '') for c, d, x in erros)[:2000] or 'rejeitada sem código'


def _resumo_nfse_nacional(xml_nfse: str) -> dict:
    """Os números da NFS-e que a lista e o detalhe mostram, sem depender do parser da captura."""
    out = {}
    if not xml_nfse:
        return out
    try:
        root = etree.fromstring(xml_nfse.encode('utf-8') if isinstance(xml_nfse, str) else xml_nfse)
    except etree.XMLSyntaxError:
        return out
    inf = None
    for el in root.iter():
        if el.tag.split('}')[-1] == 'infNFSe':
            inf = el
            break
    if inf is None:
        return out

    def txt(nome, base=inf):
        for el in base.iter():
            if el.tag.split('}')[-1] == nome:
                return (el.text or '').strip()
        return None

    def dec(nome):
        v = txt(nome)
        try:
            return Decimal(v) if v not in (None, '') else None
        except InvalidOperation:
            return None

    out['nNFSe'] = txt('nNFSe')
    out['cStat'] = int(txt('cStat')) if (txt('cStat') or '').isdigit() else None
    dh = txt('dhProc')
    try:
        out['dhProc'] = datetime.fromisoformat(dh).astimezone(FUSO).replace(tzinfo=None) if dh else None
    except ValueError:
        out['dhProc'] = None
    # vLiq fica em infNFSe/valores; os de IBS/CBS em infNFSe/IBSCBS (primeira ocorrência de cada)
    for nome in ('vLiq', 'vBC', 'pAliqEfetUF', 'vIBSUF', 'pAliqEfetMun', 'vIBSMun', 'vIBSTot', 'pAliqEfetCBS',
                 'vCBS', 'vTotNF'):
        out[nome] = dec(nome)
    out['cLocalidadeIncid'] = txt('cLocalidadeIncid')
    return out


def _arquivar(xml_nfse, empresa_id, chave, quando):
    """XML da NFS-e no Dropbox, pasta NFSE da empresa (a mesma da captura). Falha vira None."""
    try:
        from utils import dropbox_sync
        emp = execute_query('SELECT nome_razao_social, numero_cliente FROM clientes WHERE id = %s',
                            (empresa_id,), fetch=True, fetch_one=True) or {}
        pasta = dropbox_sync._service.pasta_fiscal(emp.get('nome_razao_social') or str(empresa_id),
                                                   quando.year, quando.month, 'NFSE', emp.get('numero_cliente'))
        caminho = f'{pasta}/{chave}.xml'
        if dropbox_sync._service.upload_bytes(caminho, xml_nfse.encode('utf-8')):
            return caminho
        logger.warning('[nfse-emissao] Dropbox recusou o upload de %s', caminho)
    except Exception as exc:
        logger.warning('[nfse-emissao] falha ao arquivar a NFS-e %s: %s', chave, exc)
    return None


# --- prefeitura de São Paulo ---------------------------------------------------
def _enviar_sp(emitida_id, numero, p: Pedido, cfg, agora):
    from utils.integrations.nfse_sp import client as sp, rps as R
    from models.nfse_emissao import separar_logradouro
    sess = sp.abrir_sessao(p.empresa_id)
    versao = int(cfg.get('leiaute_sp') or 1)
    tipo_lgr, lgr = separar_logradouro(p.tomador_logradouro)
    rps = R.RPS(
        inscricao_prestador=_dig(cfg['inscricao_municipal']), serie=p.serie, numero=numero,
        data_emissao=agora.date(), valor_final=p.valor_servico, codigo_servico=p.cod_servico_sp,
        discriminacao=p.descricao,
        tomador=R.Tomador(documento=p.tomador_doc, nome=p.tomador_nome, tipo_logradouro=tipo_lgr, logradouro=lgr,
                          numero=p.tomador_numero, complemento=p.tomador_complemento, bairro=p.tomador_bairro,
                          cidade_ibge=p.tomador_ibge, uf=p.tomador_uf, cep=p.tomador_cep, email=p.tomador_email,
                          inscricao_municipal=p.tomador_im if p.tomador_ibge == '3550308' else ''),
        tributacao=cfg.get('tributacao_sp') or 'T', iss_retido=p.iss_retido,
        aliquota=(p.aliquota_iss / 100) if p.aliquota_iss else Decimal('0'),
        pis=p.ret_pis, cofins=p.ret_cofins, inss=p.ret_inss, ir=p.ret_irrf, csll=p.ret_csll,
        municipio_prestacao=p.loc_prestacao or '', nbs=p.cod_nbs or '999999999',
        c_loc_prestacao=p.loc_prestacao or '3550308', fin_nfse=p.fin_nfse, ind_final=p.ibscbs_ind_final,
        c_ind_op=p.ibscbs_cind_op, ind_dest=p.ibscbs_ind_dest, c_class_trib=p.ibscbs_cclasstrib,
    )
    ass = R.assinar_rps(rps, sess.chave_priv, versao)
    xml = R.xml_rps(rps, ass, versao)
    pedido = R.pedido_envio_rps(sess.cert.cnpj, xml, versao)
    execute_query('UPDATE nfse_emitidas SET dps_id = %s, xml_dps = %s WHERE id = %s',
                  (f'RPS{p.serie}{numero}'[:45], xml, emitida_id))

    ret = sp.chamar(sess, 'EnvioRPS', pedido, versao_schema=versao)
    bruto = json.dumps({'sucesso': ret.sucesso, 'erros': ret.erros, 'alertas': ret.alertas, 'xml': ret.xml},
                       ensure_ascii=False)
    if not ret.sucesso:
        execute_query(
            "UPDATE nfse_emitidas SET situacao = 'rejeitada', raw_resposta = %s, erro_ultimo = %s, "
            " tentativas = tentativas + 1, enviado_em = NOW(), atualizado_em = NOW() WHERE id = %s",
            (bruto, ' | '.join(f'{c}: {d}' for c, d in ret.erros)[:2000] or 'rejeitada sem código', emitida_id))
        return
    numero_nfe, cod_verif = None, None
    for el in ret.raiz.iter():
        t = el.tag.split('}')[-1]
        if t == 'NumeroNFe' and not numero_nfe:
            numero_nfe = (el.text or '').strip()
        elif t == 'CodigoVerificacao' and not cod_verif:
            cod_verif = (el.text or '').strip()
    execute_query(
        """UPDATE nfse_emitidas SET situacao = 'autorizada', numero_nfse = %s, chave_acesso = %s,
                  data_processamento = NOW(), xmotivo = %s, raw_resposta = %s, erro_ultimo = NULL,
                  valor_liquido = %s, tentativas = tentativas + 1,
                  enviado_em = NOW(), autorizada_em = NOW(), atualizado_em = NOW()
            WHERE id = %s""",
        (numero_nfe, cod_verif, '; '.join(f'{c} {d}' for c, d in ret.alertas)[:500] or None, bruto,
         p.valor_servico - p.total_retencoes, emitida_id))


def xml_nfse_da_linha(linha) -> str:
    """O XML da NFS-e autorizada: do Dropbox quando arquivado, senão do que o Sefin devolveu
    (``raw_resposta.nfseXmlGZipB64``) ou do retorno da prefeitura (``raw_resposta.xml``)."""
    raw = linha.get('raw_resposta')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    raw = raw or {}
    if linha.get('xml_nfse_path'):
        try:
            from utils import dropbox_sync
            conteudo = dropbox_sync._service.download_file(linha['xml_nfse_path'])
            if conteudo:
                return conteudo.decode('utf-8') if isinstance(conteudo, bytes) else conteudo
        except Exception as exc:
            logger.warning('[nfse-emissao] não li %s no Dropbox: %s', linha['xml_nfse_path'], exc)
    if raw.get('nfseXmlGZipB64'):
        from utils.integrations.nfse_adn.emissao import desempacotar
        return desempacotar(raw['nfseXmlGZipB64'])
    return raw.get('xml') or ''
