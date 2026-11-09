# -*- coding: utf-8 -*-
"""Inscrições estaduais de um cadastro (09/11/2026).

Uma empresa pode ter várias IEs (``clientes_inscricoes_estaduais``), cada uma com um TIPO:

* NORMAL — a IE da empresa no estado onde ela está (o do endereço do CNPJ). Uma é a PRINCIPAL e
  fica espelhada em ``clientes.inscricao_estadual`` (o resto do sistema lê de lá: notas, captura...).
* ST — Substituto Tributário: IE em OUTRO estado sem estabelecimento lá, para recolher o ICMS-ST das
  vendas para aquele estado (filial teria CNPJ próprio = outro cadastro).
* RURAL — Produtor rural (pessoa física): uma IE por propriedade, com o nome da propriedade e o município.

Anderson (09/11/2026): "quando PF for PRODUTOR RURAL, tem que abrir para inserir a Inscrição Estadual ou
Inscrições Estaduais"; "tem algumas empresas que tem IE de Substituto Tributário"; "tem uma que seria da
própria empresa e as demais substituto... como e onde cadastraríamos?" (Petrogoiás: GO + 8 estados).

Isenta = ``clientes.inscricao_estadual = 'ISENTO'`` (a convenção que o resto do app já usa); as linhas
da tabela são só IEs de verdade. ``conferir`` marca a IE que a migração trouxe em conflito com o cadastro
(vai para o A CADASTRAR como "Conferir IE" até alguém olhar no Sintegra e salvar).
"""
import re

from utils.db_helper import execute_query

TIPOS = [('NORMAL', 'Normal (da própria empresa)'), ('ST', 'Substituto Tributário'), ('RURAL', 'Produtor rural')]
TIPO_ROTULO = {'NORMAL': 'Normal', 'ST': 'Substituto Tributário', 'RURAL': 'Produtor rural'}
UFS = ['AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG', 'PA', 'PB', 'PR', 'PE', 'PI',
       'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO']
ISENTO = 'ISENTO'

# Consulta pública de cada estado, como o mapa do www.sintegra.gov.br aponta (lido em 09/11/2026).
SINTEGRA = {
    'AC': 'http://sefaznet.ac.gov.br/sefazonline/servlet/hpfsincon',
    'AL': 'http://www.sefaz.al.gov.br/asp/sintegra/sintegra.asp?estado=AL',
    'AP': 'http://www.sefaz.ap.gov.br/sate/seg/SEGf_AcessarFuncao.jsp?cdFuncao=CAD_011',
    'AM': 'http://www.sefaz.am.gov.br/sintegra/sintegra0.asp',
    'BA': 'https://portal.sefaz.ba.gov.br/scripts/cadastro/cadastroBa/consultaBa.asp',
    'CE': 'https://servicos.sefaz.ce.gov.br/internet/Sintegra/Sintegra.Asp?estado=CE',
    'DF': 'https://ww1.receita.fazenda.df.gov.br/icms/sintegra-consulta',
    'ES': 'http://www.sintegra.es.gov.br/',
    'GO': 'http://appasp.sefaz.go.gov.br/Sintegra/Consulta/default.asp?',
    'MA': 'http://aplicacoes.ma.gov.br/sintegra/jsp/consultaSintegra/consultaSintegraFiltro.jsf',
    'MT': 'https://www.sefaz.mt.gov.br/cadastro/emissaocartao/emissaocartaocontribuinteacessodireto',
    'MS': 'https://servicos.efazenda.ms.gov.br/consultapublica/Home/Index',
    'MG': 'http://consultasintegra.fazenda.mg.gov.br',
    'PA': 'http://app.sefa.pa.gov.br/Sintegra/',
    'PB': 'https://www4.sefaz.pb.gov.br/sintegra',
    'PR': 'http://www.sintegra.fazenda.pr.gov.br/sintegra/',
    'PE': 'http://www.sintegra.sefaz.pe.gov.br',
    'PI': 'http://web.sintegra.sefaz.pi.gov.br',
    'RJ': 'https://sucief-sincad-web.fazenda.rj.gov.br/sincad-web/index.jsf',
    'RN': 'http://www.set.rn.gov.br/uvt/consultacontribuinte.aspx',
    'RS': 'https://www.sefaz.rs.gov.br/consultas/contribuinte',
    'RO': 'http://www.sefin.ro.gov.br/sint_consul.asp',
    'RR': 'https://portalapp.sefaz.rr.gov.br/siate/servlet/wp_siate_consultasintegra',
    'SC': 'https://sat.sef.sc.gov.br/tax.NET/Sat.Cadastro.Web/ComprovanteIE/Consulta.aspx',
    'SP': 'https://www.cadesp.fazenda.sp.gov.br/Pages/Cadastro/Consultas/ConsultaPublica/ConsultaPublica.aspx',
    'SE': 'https://security.sefaz.se.gov.br/SIC/sintegra/index.jsp',
    'TO': 'http://sintegra.sefaz.to.gov.br',
}
SINTEGRA_GERAL = 'http://www.sintegra.gov.br/'


def dig(v):
    return re.sub(r'\D', '', str(v or ''))


def eh_isento(v):
    return str(v or '').strip().upper() in ('ISENTO', 'ISENTA')


def uf_do_endereco(cliente_id):
    r = execute_query("SELECT estado FROM enderecos_clientes WHERE cliente_id = %s ORDER BY principal DESC, id LIMIT 1",
                      (cliente_id,), fetch=True, fetch_one=True)
    return ((r or {}).get('estado') or '').upper() or None


def listar(cliente_id):
    """IEs do cadastro: principal primeiro, depois Normal, ST e Rural, por UF."""
    linhas = execute_query(
        """SELECT id, uf, ie, tipo, propriedade, municipio, principal, conferir, conferir_motivo, origem
             FROM clientes_inscricoes_estaduais WHERE cliente_id = %s
            ORDER BY principal DESC, FIELD(tipo, 'NORMAL', 'ST', 'RURAL'), uf, ie""",
        (cliente_id,), fetch=True) or []
    for x in linhas:
        x['tipo_rotulo'] = TIPO_ROTULO.get(x['tipo'], x['tipo'])
        x['sintegra'] = SINTEGRA.get(x['uf'] or '', SINTEGRA_GERAL)
    return linhas


def _espelhar(cliente_id):
    """clientes.inscricao_estadual = a IE principal (ou fica ISENTO / vazio)."""
    p = execute_query("SELECT ie FROM clientes_inscricoes_estaduais WHERE cliente_id = %s AND principal = 1 LIMIT 1",
                      (cliente_id,), fetch=True, fetch_one=True)
    atual = (execute_query("SELECT inscricao_estadual FROM clientes WHERE id = %s", (cliente_id,),
                           fetch=True, fetch_one=True) or {}).get('inscricao_estadual')
    if p:
        if (atual or '') != p['ie']:
            execute_query("UPDATE clientes SET inscricao_estadual = %s WHERE id = %s", (p['ie'], cliente_id))
    elif not eh_isento(atual) and atual:
        execute_query("UPDATE clientes SET inscricao_estadual = NULL WHERE id = %s", (cliente_id,))


def _eleger_principal(cliente_id):
    """Sem principal (e não isenta): a 1ª IE Normal vira a principal."""
    if execute_query("SELECT 1 FROM clientes_inscricoes_estaduais WHERE cliente_id = %s AND principal = 1",
                     (cliente_id,), fetch=True):
        return
    c = execute_query("SELECT inscricao_estadual FROM clientes WHERE id = %s", (cliente_id,), fetch=True, fetch_one=True) or {}
    if eh_isento(c.get('inscricao_estadual')):
        return
    n = execute_query("SELECT id FROM clientes_inscricoes_estaduais WHERE cliente_id = %s AND tipo = 'NORMAL' ORDER BY id LIMIT 1",
                      (cliente_id,), fetch=True, fetch_one=True)
    if n:
        execute_query("UPDATE clientes_inscricoes_estaduais SET principal = 1 WHERE id = %s", (n['id'],))


def salvar(cliente_id, dados, ie_id=None):
    """Inclui ou altera uma IE. dados: uf, ie, tipo, propriedade, municipio, principal.
    Devolve (ok, erro_ou_id). Salvar tira a marca de conferir (alguém olhou)."""
    uf = (dados.get('uf') or '').strip().upper()
    ie = ' '.join(str(dados.get('ie') or '').split()).upper()[:20]
    tipo = (dados.get('tipo') or 'NORMAL').upper()
    prop = ' '.join(str(dados.get('propriedade') or '').split()).upper()[:120] or None
    mun = ' '.join(str(dados.get('municipio') or '').split()).upper()[:80] or None
    principal = bool(dados.get('principal')) and tipo == 'NORMAL'
    if uf not in UFS:
        return False, 'Escolha a UF.'
    if not dig(ie) or eh_isento(ie):
        return False, 'Digite o número da inscrição (para empresa isenta use "Empresa isenta de IE").'
    if tipo not in TIPO_ROTULO:
        return False, 'Tipo inválido.'
    if tipo == 'RURAL' and not (prop and mun):
        return False, 'Produtor rural: informe o nome da propriedade e o município.'
    if len(dig(ie)) == 14 and '/' in ie:
        return False, 'Esse número é um CNPJ, não uma inscrição estadual.'
    for x in listar(cliente_id):
        if x['id'] != ie_id and dig(x['ie']) == dig(ie) and x['uf'] == uf:
            return False, f'A IE {ie} ({uf}) já está cadastrada.'
    if principal:
        execute_query("UPDATE clientes_inscricoes_estaduais SET principal = 0 WHERE cliente_id = %s", (cliente_id,))
        c = execute_query("SELECT inscricao_estadual FROM clientes WHERE id = %s", (cliente_id,), fetch=True, fetch_one=True) or {}
        if eh_isento(c.get('inscricao_estadual')):
            execute_query("UPDATE clientes SET inscricao_estadual = NULL WHERE id = %s", (cliente_id,))
    if ie_id:
        execute_query("""UPDATE clientes_inscricoes_estaduais SET uf = %s, ie = %s, tipo = %s, propriedade = %s, municipio = %s,
                                principal = %s, conferir = 0, conferir_motivo = NULL, isenta = 0
                          WHERE id = %s AND cliente_id = %s""",
                      (uf, ie, tipo, prop, mun, 1 if principal else 0, ie_id, cliente_id))
        novo = ie_id
    else:
        execute_query("""INSERT INTO clientes_inscricoes_estaduais (cliente_id, uf, ie, tipo, propriedade, municipio, principal, origem)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, 'app')""",
                      (cliente_id, uf, ie, tipo, prop, mun, 1 if principal else 0))
        r = execute_query("SELECT id FROM clientes_inscricoes_estaduais WHERE cliente_id = %s AND uf = %s AND ie = %s",
                          (cliente_id, uf, ie), fetch=True, fetch_one=True)
        novo = r['id'] if r else None
    if not principal:
        execute_query("UPDATE clientes_inscricoes_estaduais SET principal = 0 WHERE id = %s AND tipo <> 'NORMAL'", (novo,))
    _eleger_principal(cliente_id)
    _espelhar(cliente_id)
    return True, novo


def excluir(cliente_id, ie_id):
    execute_query("DELETE FROM clientes_inscricoes_estaduais WHERE id = %s AND cliente_id = %s", (ie_id, cliente_id))
    _eleger_principal(cliente_id)
    _espelhar(cliente_id)


def marcar_isenta(cliente_id, isenta):
    """Empresa isenta de IE (prestador de serviço): nenhuma IE é a principal e o cadastro fica ISENTO."""
    if isenta:
        execute_query("UPDATE clientes_inscricoes_estaduais SET principal = 0 WHERE cliente_id = %s", (cliente_id,))
        execute_query("UPDATE clientes SET inscricao_estadual = %s WHERE id = %s", (ISENTO, cliente_id))
    else:
        execute_query("UPDATE clientes SET inscricao_estadual = NULL WHERE id = %s AND inscricao_estadual = %s",
                      (cliente_id, ISENTO))
        _eleger_principal(cliente_id)
        _espelhar(cliente_id)


def espelhar_do_cadastro(cliente_id):
    """O campo único (Novo/Editar, clique na ficha) gravou clientes.inscricao_estadual: a tabela acompanha.
    Número que já existe na lista vira a principal; número novo entra como Normal na UF do endereço."""
    c = execute_query("SELECT inscricao_estadual FROM clientes WHERE id = %s", (cliente_id,), fetch=True, fetch_one=True)
    if not c:
        return
    v = (c.get('inscricao_estadual') or '').strip()
    if eh_isento(v):
        execute_query("UPDATE clientes SET inscricao_estadual = %s WHERE id = %s AND inscricao_estadual <> %s", (ISENTO, cliente_id, ISENTO))
        execute_query("UPDATE clientes_inscricoes_estaduais SET principal = 0 WHERE cliente_id = %s", (cliente_id,))
        return
    if not dig(v):
        execute_query("UPDATE clientes_inscricoes_estaduais SET principal = 0 WHERE cliente_id = %s", (cliente_id,))
        return
    mesma = next((x for x in listar(cliente_id) if dig(x['ie']) == dig(v)), None)
    execute_query("UPDATE clientes_inscricoes_estaduais SET principal = 0 WHERE cliente_id = %s", (cliente_id,))
    if mesma:
        execute_query("UPDATE clientes_inscricoes_estaduais SET principal = 1, tipo = 'NORMAL' WHERE id = %s", (mesma['id'],))
    else:
        execute_query("""INSERT INTO clientes_inscricoes_estaduais (cliente_id, uf, ie, tipo, principal, origem)
                         VALUES (%s, %s, %s, 'NORMAL', 1, 'app')""", (cliente_id, uf_do_endereco(cliente_id), v[:20]))


def ler_rurais_json(bruto):
    """ies_rurais_json da tela -> lista (None = a tela não mandou: não mexe em nada)."""
    import json
    bruto = (bruto or '').strip()
    if not bruto:
        return None
    try:
        v = json.loads(bruto)
    except ValueError:
        return None
    return v if isinstance(v, list) else None


def erros_rurais(linhas):
    return _limpar_rurais(linhas)[1]


def _limpar_rurais(linhas):
    limpas, erros = [], []
    for i, d in enumerate(linhas or [], 1):
        if not isinstance(d, dict):
            continue
        if not any(str(d.get(k) or '').strip() for k in ('uf', 'ie', 'propriedade', 'municipio')):
            continue
        uf = (d.get('uf') or '').strip().upper()
        ie = ' '.join(str(d.get('ie') or '').split()).upper()[:20]
        prop = ' '.join(str(d.get('propriedade') or '').split()).upper()[:120]
        mun = ' '.join(str(d.get('municipio') or '').split()).upper()[:80]
        sem = [r for r, ok in (('UF', uf in UFS), ('número', dig(ie)), ('propriedade', prop), ('município', mun)) if not ok]
        if sem:
            erros.append(f'IE de produtor rural {i} sem ' + ', '.join(sem))
        else:
            limpas.append((uf, ie, prop, mun))
    return limpas, erros


def salvar_rurais(cliente_id, linhas):
    """Novo/Editar da pessoa física produtor rural: a lista da tela substitui as IEs de produtor rural.
    linhas = [{uf, ie, propriedade, municipio}]; devolve a lista de erros (nada é gravado se houver)."""
    limpas, erros = _limpar_rurais(linhas)
    if erros:
        return erros
    atuais = {(x['uf'], dig(x['ie'])): x for x in listar(cliente_id) if x['tipo'] == 'RURAL'}
    novas = {(uf, dig(ie)) for uf, ie, _, _ in limpas}
    for k, x in atuais.items():
        if k not in novas:
            execute_query("DELETE FROM clientes_inscricoes_estaduais WHERE id = %s", (x['id'],))
    for uf, ie, prop, mun in limpas:
        x = atuais.get((uf, dig(ie)))
        if x:
            execute_query("UPDATE clientes_inscricoes_estaduais SET ie = %s, propriedade = %s, municipio = %s, conferir = 0 WHERE id = %s",
                          (ie, prop, mun, x['id']))
        else:
            execute_query("""INSERT IGNORE INTO clientes_inscricoes_estaduais (cliente_id, uf, ie, tipo, propriedade, municipio, principal, origem)
                             VALUES (%s, %s, %s, 'RURAL', %s, %s, 0, 'app')""", (cliente_id, uf, ie, prop, mun))
    return []
