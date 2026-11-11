"""Rotas de Clientes - CRUD completo"""
import re
import logging
import unicodedata
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user
from decimal import Decimal, InvalidOperation
from utils.auth_helper import admin_required, login_required, permission_required
from utils.atividade import registrar, rotulo_empresa
from utils.auditoria_fmt import AUDITORIA_INICIO, hist_preparar
from utils import hist_cartoes as HC
from utils.form_helpers import limpar_form
from utils.db_helper import execute_query
from utils.acesso import filtrar_lista, empresas_ocultas, pode_ver_empresa
from utils import a_cadastrar as AC
from models.cliente import Cliente, COBRANCAS
from models.endereco_cliente import EnderecoCliente
from utils.cnpj_receita import limpar_logradouro
from models.contato_cliente import ContatoCliente, AREAS_ATENDIMENTO
from models.cliente_responsavel import ClienteResponsavel
from models.fin_contrato import FinContrato
from models.cadastro_adicional_cliente import CadastroAdicionalCliente
from models.socio_cliente import SocioCliente, pode_ser_responsavel, qual_recusada_pj
from utils.qualificacao import qualificacao
from models.contabilidade import Contabilidade
from utils.natureza import classificar as natureza_classificar, codigo_formatado, FAMILIAS as NAT_FAMILIAS
from models.pessoa_dados import cpf_valido, cpf_formatado
from models.pessoa_dados import (PessoaDados, CAMPOS as PESSOA_CAMPOS, COM_REGIME, ESTADOS_CIVIS,
                                 REGIMES_BENS, NIVEIS_GOVBR, UFS, Opcoes, TIPOS_OPCAO,
                                 Documentos, TIPOS_DOC, ORGAO_SUGERIDO)
from models.grupo_cliente import GrupoCliente
from models.ramo_atividade import RamoAtividade, SEGMENTO_NOME
from models.cadastro_anp import CadastroAnp
from models.dfe_certificado import DfeCertificado
from models.cliente_contador import ClienteContador
from models import inscricao_estadual as IE
from utils import certificado_autoria, dropbox_sync
from utils.certificado_digital import (
    abrir_certificado, cifrar_senha,
    SenhaInvalidaError, DocumentoNaoEncontradoError, CertificadoError,
)

clientes = Blueprint('clientes', __name__)
logger = logging.getLogger(__name__)


def _parse_decimal_input(value):
    """Converte entrada numérica para Decimal aceitando formatos 10,50 e 10.50."""
    cleaned = (value or '').strip().replace(' ', '')
    if not cleaned:
        raise InvalidOperation

    if ',' in cleaned and '.' in cleaned:
        # Ex.: 1.234,56 (pt-BR) ou 1,234.56 (en-US)
        if cleaned.rfind(',') > cleaned.rfind('.'):
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    elif ',' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')

    return Decimal(cleaned)


def _numero_inteiro(numero):
    """'007' -> '7' (padronizacao de 27/08/2026: o numero e so o inteiro).

    Normaliza ANTES da checagem de duplicado: '007' nao existe no banco, mas
    o '7' que ele vira pode existir — checar o cru deixaria a colisao passar.
    So mexe no que e todo digito; texto misto fica como veio.
    """
    n = (numero or '').strip()
    return str(int(n)) if n.isdigit() else n


def _contatos_do_form(form):
    """Contatos guardados no formulário do cliente (campo contatos_json, 05/11/2026): só os que têm
    nome e celular válido contam."""
    import json as _json
    try:
        lista = _json.loads(form.get('contatos_json') or '[]')
    except ValueError:
        lista = []
    out = []
    for c in lista if isinstance(lista, list) else []:
        if not isinstance(c, dict):
            continue
        nome = str(c.get('nome') or '').strip()
        cel = str(c.get('celular') or '').strip()
        if nome and _telefone_valido(cel):
            email = str(c.get('email') or '').strip().lower()[:255]
            cel_x, email_x = _meios_extras(c.get('celulares_extra') or [], c.get('emails_extra') or [], cel, email)
            out.append({'nome': nome[:255], 'celular': cel[:20], 'telefone': (str(c.get('telefone') or '').strip() or None),
                        'email': (email if _email_ok(email) else None),
                        'cargo': (str(c.get('cargo') or '').strip()[:100] or None),
                        'departamentos': [int(d) for d in (c.get('departamentos') or []) if str(d).isdigit()],
                        '_extras': (cel_x, email_x)})
    return out


def _meios_extras(celulares, emails, celular_principal='', email_principal='', erros=None):
    """Celulares e e-mails A MAIS (10/11/2026): só os válidos, sem repetir o principal nem entre si.
    Com ``erros`` (lista), anota os inválidos em vez de só descartar."""
    cel_x, email_x = [], []
    base = re.sub(r'\D', '', celular_principal or '')
    for v in celulares if isinstance(celulares, list) else []:
        v = str(v or '').strip()
        d = re.sub(r'\D', '', v)
        if not d:
            continue
        if not _telefone_valido(v):
            if erros is not None:
                erros.append('outro celular (%s inválido)' % v)
        elif d != base and d not in [re.sub(r'\D', '', x) for x in cel_x]:
            cel_x.append(v[:20])
    for v in emails if isinstance(emails, list) else []:
        v = str(v or '').strip().lower()[:255]
        if not v:
            continue
        if not _email_ok(v):
            if erros is not None:
                erros.append('outro e-mail (%s não é um e-mail)' % v)
        elif v != (email_principal or '').lower() and v not in email_x:
            email_x.append(v)
    return cel_x, email_x


def _gravar_contatos_do_formulario(cliente_id, form=None):
    """Grava os contatos guardados no formulário (novo ou edição). Devolve quantos."""
    n = 0
    for c in _contatos_do_form(form or request.form):
        cel_x, email_x = c.pop('_extras')
        cid = ContatoCliente.create(cliente_id=cliente_id, ativo=True, **c)
        if cid:
            n += 1
            ContatoCliente.set_meios(cid, cel_x, email_x)
            log = dict(c, cliente_id=cliente_id, origem='formulário do cadastro')
            if cel_x:
                log['outros_celulares'] = cel_x
            if email_x:
                log['outros_emails'] = email_x
            registrar('escrita.criou_contato', 'cadastros', tabela='contatos_clientes', registro_id=cid, depois=log)
    return n


def _conjuge_cadastrado(pf_id):
    """O cônjuge também é cadastro? Devolve {id, nome} para a ficha virar link."""
    d = PessoaDados.get(pf_id)
    dig = re.sub(r'\D', '', d.get('conjuge_cpf') or '')
    if len(dig) != 11:
        return None
    return execute_query("SELECT id, nome_razao_social FROM clientes "
                         "WHERE REGEXP_REPLACE(cpf_cnpj, '[^0-9]', '') = %s LIMIT 1", (dig,), fetch=True, fetch_one=True)


def _empresas_do_socio(pf_id):
    """Pessoa física não tem sócio: a ficha mostra as EMPRESAS em que ela é sócia (atual e ex).
    Anderson (10/11/2026): "PF não tem sócio, seria útil se aparecesse as empresas que a pessoa é sócia"."""
    return execute_query(
        """SELECT s.cliente_id, s.qualificacao, s.percentual_participacao, s.responsavel, s.data_entrada,
                  s.data_saida, s.ativo, s.capital_valor, s.pro_labore,
                  c.nome_razao_social, c.numero_cliente, c.cpf_cnpj, c.situacao
             FROM socios_clientes s JOIN clientes c ON c.id = s.cliente_id
            WHERE s.pf_cliente_id = %s
            ORDER BY s.ativo DESC, (s.data_saida IS NULL) DESC, c.nome_razao_social""",
        (pf_id,), fetch=True) or []


def _contato_da_pessoa_pf(cliente_id, nome):
    """O contato que é a PRÓPRIA pessoa física (mesmo nome do cadastro), se existir."""
    from models.contato_cliente import _nome_chave
    chave = _nome_chave(nome)
    if not cliente_id or not chave:
        return None
    for c in ContatoCliente.get_by_cliente(cliente_id) or []:
        if _nome_chave(c.get('nome')) == chave:
            return c
    return None


def _sincronizar_contato_pf(cliente_id, form=None):
    """Pessoa física: o celular, o telefone fixo e o e-mail do cadastro (e os a mais) SÃO o contato dela.
    Anderson (10/11/2026): "no Contato tem que ter o local para eu colocar e-mail e se usuário quiser colocar
    outro contato ai ele terá a opção... Celular + Telefone Fixo + E-mail... e até opções de colocar mais
    telefones e mais e-mails". Cria o contato com o nome da pessoa (principal, recebe de todos os
    departamentos) ou atualiza o que já existe com o mesmo nome — cargo, principal e departamentos ficam."""
    f = form or request.form
    if f.get('tipo_pessoa') != 'PF':
        return
    nome = (next((v for v in f.getlist('nome_razao_social') if v.strip()), '') or '').strip().upper()
    celular = (f.get('celular') or '').strip()
    if not nome or not _telefone_valido(celular):
        return
    telefone = (f.get('telefone') or '').strip()
    telefone = telefone if re.sub(r'\D', '', telefone) and _telefone_valido(telefone) else None
    email = (f.get('email') or '').strip().lower()[:255]
    email = email if _email_ok(email) else None
    cel_x, email_x = _meios_extras(f.getlist('celular_extra'), f.getlist('email_extra'), celular, email or '')
    atual = _contato_da_pessoa_pf(cliente_id, nome)
    novo = {'nome': nome, 'celular': celular, 'telefone': telefone, 'email': email}
    if atual:
        antes = {k: atual.get(k) for k in ('celular', 'telefone', 'email')}
        antes['outros_celulares'] = atual.get('celulares_extra') or []
        antes['outros_emails'] = atual.get('emails_extra') or []
        depois = {'celular': celular, 'telefone': telefone, 'email': email,
                  'outros_celulares': cel_x, 'outros_emails': email_x}
        if antes == depois:
            return
        ContatoCliente.update(atual['id'], nome=atual['nome'], cargo=atual.get('cargo'), email=email, telefone=telefone,
                              celular=celular, principal=bool(atual.get('principal')), ativo=bool(atual.get('ativo', True)))
        ContatoCliente.set_meios(atual['id'], cel_x, email_x)
        registrar('escrita.alterou_contato', 'cadastros', tabela='contatos_clientes', registro_id=atual['id'],
                  antes=dict(antes, cliente_id=cliente_id), depois=dict(depois, cliente_id=cliente_id, origem='cadastro da pessoa física'))
        return
    deps = [d['id'] for d in ContatoCliente.departamentos_disponiveis()]
    cid = ContatoCliente.create(cliente_id=cliente_id, ativo=True, principal=True, departamentos=deps, **novo)
    if cid:
        ContatoCliente.set_meios(cid, cel_x, email_x)
        registrar('escrita.criou_contato', 'cadastros', tabela='contatos_clientes', registro_id=cid,
                  depois=dict(novo, cliente_id=cliente_id, outros_celulares=cel_x, outros_emails=email_x,
                              origem='cadastro da pessoa física (a própria pessoa)'))


def _obrigatorios_faltando(form, avulso=False, cliente_id=None):
    """Grupo, e-mail, celular e ramo de atividade (07/10/2026): exigidos de
    TODO cadastro — cliente e avulso, novo e edição. A tela confere antes de
    enviar; isto é a rede de segurança do servidor. Devolve o que falta."""
    falta = []
    gid = form.get('grupo_id', type=int)
    grupo = GrupoCliente.get_by_id(gid) if gid else None
    if not grupo or grupo.get('situacao') != 'ATIVO':
        falta.append('grupo de empresas')
    # 05/11/2026 (Anderson): no PJ o e-mail do cadastro é O QUE ESTÁ NO CNPJ (vem da busca), e há CNPJ
    # sem e-mail na Receita — então só a pessoa física continua obrigada a ter e-mail aqui. O e-mail de
    # cobrança é o dos CONTATOS; quem não tem contato com e-mail aparece em A CADASTRAR.
    if form.get('tipo_pessoa') == 'PF':
        # 05/11/2026 (Anderson): "A Pessoa Física vamos retirar esse campo [e-mail] e deixar
        # obrigatório só o Cadastro do Contato" — ao menos um contato com celular.
        ja_tem = cliente_id and execute_query('SELECT COUNT(*) n FROM contatos_clientes WHERE cliente_id = %s AND ativo = 1',
                                              (cliente_id,), fetch=True, fetch_one=True)['n']
        if not ja_tem and not _contatos_do_form(form) and not _telefone_valido(form.get('celular')):
            falta.append('contato (nome e celular): a pessoa física precisa de ao menos um')
    if not _telefone_valido(form.get('celular')):
        falta.append('celular')
    tel = form.get('telefone') or ''
    if re.sub(r'\D', '', tel) and not _telefone_valido(tel):
        falta.append('telefone (número inválido)')
    ids = [int(r) for r in form.getlist('ramos_atividade_ids') if r.strip().isdigit()]
    tipo = 'PF' if form.get('tipo_pessoa') == 'PF' else 'PJ'
    if not ids:
        falta.append('ramo de atividade')
    else:
        # Ramo é de PF ou de PJ (10/10/2026): o da outra pessoa não conta.
        marcas = ','.join(['%s'] * len(ids))
        ok = execute_query(f"SELECT COUNT(*) n FROM ramos_atividade WHERE id IN ({marcas}) AND tipo_pessoa = %s",
                           (*ids, tipo), fetch=True, fetch_one=True) or {}
        if (ok.get('n') or 0) != len(set(ids)):
            falta.append('ramo de atividade de ' + ('pessoa física' if tipo == 'PF' else 'empresa'))
    # ORIGEM (14/10/2026): empresa CLIENTE foi aberta pela casa OU veio de
    # outro escritório — qual. Avulso não precisa; ao converter, precisa.
    if tipo == 'PJ' and not avulso and not _origem_ok(form):
        falta.append('contabilidade anterior (ou marque "aberta pela Qualicontax")')
    if tipo == 'PF' and (form.get('pf_nascimento') or '').strip() and not _data_iso(form.get('pf_nascimento')):
        falta.append('data de nascimento (inválida ou no futuro)')
    # Documento de identificação (19/10/2026): toda PF precisa de ao menos um,
    # e cada um COMPLETO — tipo, número, órgão e UF. Sem o JSON da tela também
    # falta (não há como saber).
    if tipo == 'PF':
        falta += _documentos_faltando(form)
    return falta


def _documentos_faltando(form):
    """O que falta nos documentos da tela: nenhum documento, ou o campo vazio
    de cada um ('RG sem UF'). Órgão fora da lista conta como vazio — exceto o
    que já estava gravado para a pessoa (valor antigo continua valendo)."""
    import json
    try:
        bruto = json.loads((form.get('pf_documentos_json') or '').strip() or 'null')
    except ValueError:
        bruto = None
    linhas = [d for d in bruto if isinstance(d, dict)] if isinstance(bruto, list) else []
    if not linhas:
        return ['documento de identificação (RG, CNH, CIN...)']
    tipos = dict(TIPOS_DOC)
    orgaos = set(Opcoes.listar('rg_orgao'))
    cid = request.view_args.get('id') if request.view_args else None
    if cid:
        orgaos |= {d.get('orgao') for d in Documentos.listar(cid) if d.get('orgao')}
    out = []
    for d in linhas:
        nome = tipos.get(str(d.get('tipo') or '')) and str(d.get('tipo'))
        sem = [rot for rot, ok in (
            ('tipo', nome),
            ('número', str(d.get('numero') or '').strip()),
            ('órgão emissor', str(d.get('orgao') or '') in orgaos),
            ('UF', str(d.get('uf') or '') in UFS)) if not ok]
        if sem:
            out.append(f"documento {nome or ''} sem {', '.join(sem)}".replace('  ', ' '))
    return out


def _telefone_valido(valor):
    """10 ou 11 dígitos (DDD + número) e não um enchimento: a Receita devolve
    "000000000000" quando a empresa não tem o 2º telefone, e isso passava como
    celular (10/10/2026: 13 cadastros salvos assim). Mesmo dígito repetido
    ("(11) 1111-1111") também não é número."""
    d = re.sub(r'\D', '', valor or '')
    return len(d) in (10, 11) and len(set(d)) > 1 and not d.startswith('0')


def _origem_ok(form):
    """Aberta pela casa marcada, ou um escritório que existe na lista."""
    if form.get('aberta_pela_casa'):
        return True
    cid = form.get('contabilidade_anterior_id', type=int)
    return bool(cid and Contabilidade.get(cid))


def _salvar_origem(cliente_id, form, antes_id=None):
    """Grava de onde a empresa veio. Aberta pela casa zera o escritório (uma
    coisa ou outra). Registra só quando muda, com o NOME do escritório."""
    novo_id = None if form.get('aberta_pela_casa') else form.get('contabilidade_anterior_id', type=int)
    if novo_id and not Contabilidade.get(novo_id):
        novo_id = None
    if (antes_id or None) == (novo_id or None):
        return
    execute_query("UPDATE clientes SET contabilidade_anterior_id = %s WHERE id = %s", (novo_id, cliente_id))

    def _nome(i):
        c = Contabilidade.get(i) if i else None
        return c['nome'] if c else None
    registrar('escrita.alterou_origem_cliente', 'cadastros', tabela='clientes', registro_id=cliente_id,
              antes={'contabilidade_anterior': _nome(antes_id)},
              depois={'contabilidade_anterior': _nome(novo_id)})


def _salvar_natureza(cliente_id, form, antes=None):
    """Natureza jurídica que a busca do CNPJ pôs no formulário (só leitura na
    tela: quem manda nela é a Receita). Grava quando muda e registra."""
    cod = ''.join(ch for ch in (form.get('natureza_codigo') or '') if ch.isdigit())[:4]
    desc = ' '.join((form.get('natureza_juridica') or '').split())[:120]
    if not cod:
        return
    antes = antes or {}
    if cod == (antes.get('natureza_codigo') or '') and desc == (antes.get('natureza_juridica') or ''):
        return
    execute_query("UPDATE clientes SET natureza_codigo = %s, natureza_juridica = %s, natureza_em = NOW() WHERE id = %s",
                  (cod, desc or None, cliente_id))
    registrar('escrita.alterou_natureza', 'cadastros', tabela='clientes', registro_id=cliente_id,
              antes={'natureza_juridica': antes.get('natureza_juridica')}, depois={'natureza_juridica': desc})


def _msg_faltando(falta):
    return 'Preencha antes de salvar: ' + ', '.join(falta) + '.'


def _pode_fin():
    return current_user.is_admin() or current_user.has_permission('financeiro.honorarios')


def _travas(form, avulso=False, cliente_id=None):
    """O que IMPEDE gravar o formulário (Novo e Editar), desde 09/11/2026. Anderson: "agora vai permitir
    salvar, e se não tiver completo fica no A CADASTRAR". O que só FALTA (grupo, celular, ramo, contador,
    sócios, origem, contato, documento) deixa gravar e aparece no A CADASTRAR. Continua barrando o valor
    ERRADO: celular/telefone inválido, ramo da outra pessoa, data inválida, documento pela metade, grupo ou
    contador que não existe, sócios que não somam 100%."""
    so_falta = {'ramo de atividade', 'sócios', 'documento de identificação (RG, CNH, CIN...)'}
    out = []
    for f in _obrigatorios_faltando(form, avulso, cliente_id=cliente_id):
        if f in so_falta or f.startswith('contato (') or f.startswith('contabilidade anterior'):
            continue
        if f == 'grupo de empresas' and not form.get('grupo_id', type=int):
            continue
        if f == 'celular':
            if not re.sub(r'\D', '', form.get('celular') or ''):
                continue
            f = 'celular (número inválido: DDD + número)'
        out.append(f)
    if form.get('tipo_pessoa') == 'PF':
        out += IE.erros_rurais(IE.ler_rurais_json(form.get('ies_rurais_json')) or [])
        em = (form.get('email') or '').strip()
        if em and not _email_ok(em.lower()):
            out.append('e-mail (%s não é um e-mail)' % em)
        _meios_extras(form.getlist('celular_extra'), form.getlist('email_extra'), erros=out)
    for f in _socios_e_contador_faltando(form, cliente_id):
        if f in so_falta:
            continue
        if f == 'contador responsável':
            if not form.get('contador_id', type=int):
                continue
            f = 'contador responsável (escolha um da lista)'
        out.append(f)
    return out


def _msg_travas(travas):
    return 'Não foi salvo: corrija ' + ', '.join(travas) + '.'


def _salvar_grupo(cliente_id, grupo_id):
    """Grava o grupo escolhido no formulário. Já está nele: não mexe (e não
    tira de outro grupo que a ficha tenha posto). Trocou: sai dos antigos e
    entra no novo. Devolve (antes, depois) em nomes, ou None se não mudou."""
    atuais = Cliente.get_grupos(cliente_id) or []
    if any(g['id'] == grupo_id for g in atuais):
        return None
    for g in atuais:
        GrupoCliente.remove_cliente(g['id'], cliente_id)
    GrupoCliente.add_cliente(grupo_id, cliente_id)
    novo = GrupoCliente.get_by_id(grupo_id) or {}
    return [g['nome'] for g in atuais], novo.get('nome')


# ── Sócios e contador (07/10/2026, modelo aprovado pelo Anderson) ─────────
# Contador responsável: obrigatório em TODO cadastro (PF e PJ), menos no
# próprio cadastro que é contador. Sócios: obrigatórios em toda PJ (cliente
# e avulsa, novo e edição); sócio é um CADASTRO (PF ou PJ, cliente ou avulso)
# e a participação soma 100%. O vínculo usa socios_clientes.pf_cliente_id,
# que desde aqui aponta para qualquer cadastro — o nome ficou do tempo em que
# só PF podia ser sócia; mudar a coluna não compensa o DDL.

PRIMEIRO_NUMERO_PF = 5000


def _so_digitos(s):
    return re.sub(r'\D', '', s or '')


def _nome_chave(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Z0-9]+', ' ', s.upper()).strip()


def _proximo_numero_pf():
    """Pessoa física numera a partir de 5000: o próximo é o maior + 1."""
    r = execute_query("SELECT MAX(CAST(numero_cliente AS UNSIGNED)) AS mx FROM clientes "
                      "WHERE numero_cliente REGEXP '^[0-9]+$' AND CAST(numero_cliente AS UNSIGNED) >= %s",
                      (PRIMEIRO_NUMERO_PF,), fetch=True, fetch_one=True) or {}
    return max(PRIMEIRO_NUMERO_PF, int(r.get('mx') or 0) + 1)


def _contador_padrao():
    """O contador que atende mais cadastros vem marcado no cadastro novo."""
    r = execute_query("""SELECT cc.contador_id FROM cliente_contadores cc
                           JOIN clientes k ON k.id = cc.contador_id
                          WHERE k.is_contador = 1 AND k.situacao = 'ATIVO'
                          GROUP BY cc.contador_id ORDER BY COUNT(*) DESC LIMIT 1""",
                      fetch=True, fetch_one=True)
    return r['contador_id'] if r else None


def _cadastro_resumo(ids):
    """id -> dados do cadastro para a lista de sócios (nº, nome, doc, tipo, grupo)."""
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        return {}
    marcas = ','.join(['%s'] * len(ids))
    rows = execute_query(f"""
        SELECT c.id, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj, c.tipo_pessoa,
               COALESCE(c.avulso, 0) AS avulso, c.situacao,
               (SELECT g.id FROM cliente_grupo_relacao r JOIN grupos_clientes g ON g.id = r.grupo_id
                 WHERE r.cliente_id = c.id ORDER BY r.id LIMIT 1) AS grupo_id,
               (SELECT g.nome FROM cliente_grupo_relacao r JOIN grupos_clientes g ON g.id = r.grupo_id
                 WHERE r.cliente_id = c.id ORDER BY r.id LIMIT 1) AS grupo_nome
          FROM clientes c WHERE c.id IN ({marcas})""", tuple(ids), fetch=True) or []
    return {r['id']: {'id': r['id'], 'numero': r['numero_cliente'] or '', 'nome': (r['nome_razao_social'] or '').strip(),
                      'doc': r['cpf_cnpj'] or '', 'tipo': r['tipo_pessoa'], 'avulso': bool(r['avulso']),
                      'situacao': r['situacao'], 'grupo_id': r['grupo_id'], 'grupo_nome': r['grupo_nome']}
            for r in rows}


# Sugestões da janela de sócio — os mesmos textos que a Receita devolve no QSA
# (o campo é livre: o que vier da Receita grava como veio).
QUALIFICACOES_SOCIO = [
    'Sócio-Administrador', 'Sócio', 'Administrador', 'Titular Pessoa Física Residente ou Domiciliado no Brasil',
    'Empresário', 'Sócio Pessoa Jurídica Domiciliado no Brasil', 'Sócio Comanditado', 'Sócio Comanditário',
    'Presidente', 'Diretor', 'Procurador', 'Sócio Menor (Assistido/Representado)',
]


def _data_iso(v, ate_hoje=True):
    """'AAAA-MM-DD' (input date / Receita) ou 'DD/MM/AAAA' -> date; inválida,
    anterior a 1900 ou (ate_hoje) futura -> None."""
    v = (v or '').strip()
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', v) or re.match(r'^(\d{2})/(\d{2})/(\d{4})$', v)
    if not m:
        return None
    a, b, c = m.groups()
    ano, mes, dia = (a, b, c) if len(a) == 4 else (c, b, a)
    try:
        d = date(int(ano), int(mes), int(dia))
    except ValueError:
        return None
    if d.year < 1900 or (ate_hoje and d > date.today()):
        return None
    return d


def _qualificacao(v):
    """Texto da qualificação; tira o código que a ReceitaWS põe na frente ('49-')."""
    return re.sub(r'^\s*\d+\s*-\s*', '', str(v or '')).strip()[:80]


def _valor_opcional(v):
    """Capital / pró-labore: vazio -> None; número >= 0 -> Decimal; lixo ou
    negativo -> False (quem chama recusa o salvar)."""
    v = str(v if v is not None else '').strip().replace('R$', '').strip()
    if not v:
        return None
    if re.fullmatch(r'\d{1,3}(\.\d{3})+', v):    # "10.000" em reais é dez mil, não dez
        v = v.replace('.', '')
    try:
        d = _parse_decimal_input(v)
    except (InvalidOperation, ValueError):
        return False
    return d.quantize(Decimal('0.01')) if Decimal('0') <= d < Decimal('1e12') else False


def _ler_pessoa(form, antes=None):
    """Quadro "Dados pessoais" (só PF) -> colunas de clientes_pf_dados. O que
    não é das listas vira vazio; regime de bens só fica para casado(a) e
    união estável. Nacionalidade, profissão e órgão emissor só da lista
    (cadastro_opcoes) — o valor antigo, de antes da lista, fica enquanto não
    for trocado."""
    antes = antes or {}
    listas = Opcoes.todas()

    def lista(campo):
        v = (form.get('pf_' + campo) or '').strip()
        return v if v and (v in listas[campo] or v == antes.get(campo)) else None

    def txt(nome, n, upper=False):
        t = ' '.join((form.get(nome) or '').split())[:n]
        return (t.upper() if upper else t) or None
    civil = form.get('pf_estado_civil') or ''
    civil = civil if civil in dict(ESTADOS_CIVIS) else None
    regime = form.get('pf_regime_bens') or ''
    regime = regime if civil in COM_REGIME and regime in dict(REGIMES_BENS) else None
    nivel = form.get('pf_nivel_govbr') or ''
    # Cônjuge (11/11/2026): opcional, só CPF + nome, só para casado(a)/união estável. Tela que não
    # manda os campos (formulário antigo aberto) mantém o que estava gravado.
    if 'pf_conjuge_cpf' in form or 'pf_conjuge_nome' in form:
        c_cpf = cpf_formatado(form.get('pf_conjuge_cpf')) if cpf_valido(form.get('pf_conjuge_cpf')) else None
        c_nome = txt('pf_conjuge_nome', 255, True)
    else:
        c_cpf, c_nome = antes.get('conjuge_cpf'), antes.get('conjuge_nome')
    if civil not in COM_REGIME:
        c_cpf = c_nome = None
    return {
        'conjuge_cpf': c_cpf,
        'conjuge_nome': c_nome,
        'data_nascimento': _data_iso(form.get('pf_nascimento')),
        'estado_civil': civil,
        'regime_bens': regime,
        'nacionalidade': lista('nacionalidade'),
        'profissao': lista('profissao'),
        'nome_mae': txt('pf_nome_mae', 255, True),
        'nivel_govbr': nivel if nivel in dict(NIVEIS_GOVBR) else None,
    }


def _ler_documentos(form, antes):
    """pf_documentos_json da tela -> lista limpa: tipo da lista fixa, número
    obrigatório (em maiúsculas), órgão da lista fechada (ou o que já estava
    gravado), UF válida; marcados para o contrato — VÁRIOS desde 11/11/2026 (sem marca, o 1º)."""
    import json
    cru = (form.get('pf_documentos_json') or '').strip()
    if not cru:
        return None       # o JS da tela não rodou: vazio NÃO é "sem documento" — não apaga nada
    try:
        bruto = json.loads(cru)
    except ValueError:
        return None                       # tela quebrada: não mexe no que existe
    tipos = dict(TIPOS_DOC)
    orgaos = set(Opcoes.listar('rg_orgao')) | {d.get('orgao') for d in antes if d.get('orgao')}
    out, vistos = [], set()
    for d in bruto if isinstance(bruto, list) else []:
        if not isinstance(d, dict):
            continue
        tipo = str(d.get('tipo') or '')
        numero = ' '.join(str(d.get('numero') or '').split()).upper()[:30]
        if tipo not in tipos or not numero or (tipo, numero) in vistos:
            continue
        vistos.add((tipo, numero))
        orgao = str(d.get('orgao') or '')
        uf = str(d.get('uf') or '')
        out.append({'tipo': tipo, 'numero': numero, 'orgao': orgao if orgao in orgaos else None,
                    'uf': uf if uf in UFS else None, 'contrato': bool(d.get('contrato'))})
    # Anderson (11/11/2026): "ter a opção do Contrato utilizarmos mais de um documento de identificação".
    if out and not any(d['contrato'] for d in out):
        out[0]['contrato'] = True
    return out


def _salvar_pessoa(cliente_id, form):
    """Grava os dados da pessoa se algo mudou e registra só o que mudou."""
    docs_antes = Documentos.listar(cliente_id)
    docs = _ler_documentos(form, docs_antes)
    if docs is not None:
        r_antes, r_depois = Documentos.resumo(docs_antes), Documentos.resumo(docs)
        if r_antes != r_depois:
            Documentos.salvar(cliente_id, docs, current_user.id)
            registrar('escrita.alterou_dados_pessoa', 'cadastros', tabela='clientes',
                      registro_id=cliente_id, antes={'documentos': r_antes},
                      depois={'documentos': r_depois})
    antes = PessoaDados.get(cliente_id)
    depois = _ler_pessoa(form, antes)

    def _s(v):
        return v.isoformat() if hasattr(v, 'isoformat') else (v or None)
    mudou = [c for c in PESSOA_CAMPOS if _s(antes.get(c)) != _s(depois.get(c))]
    if not mudou:
        return
    PessoaDados.salvar(cliente_id, depois, current_user.id)
    registrar('escrita.alterou_dados_pessoa', 'cadastros', tabela='clientes', registro_id=cliente_id,
              antes={c: _s(antes.get(c)) for c in mudou}, depois={c: _s(depois.get(c)) for c in mudou})


def _ler_socios(form):
    """socios_json do formulário -> [{id, pct (Decimal), resp, qual, entrada,
    capital, pl}] (descarta lixo; capital/pl False = valor inválido)."""
    import json
    try:
        bruto = json.loads(form.get('socios_json') or '[]')
    except ValueError:
        return None
    out = []
    for s in bruto if isinstance(bruto, list) else []:
        try:
            sid = int(s.get('id'))
            pct = _parse_decimal_input(str(s.get('pct') or '0'))
        except (TypeError, ValueError, InvalidOperation, AttributeError):
            return None
        out.append({'id': sid, 'pct': pct, 'resp': bool(s.get('resp')),
                    'qual': _qualificacao(s.get('qual')), 'entrada': _data_iso(s.get('entrada')),
                    'capital': _valor_opcional(s.get('capital')), 'pl': _valor_opcional(s.get('pl'))})
    return out


def _eh_contador(form, cliente_id=None):
    """A marca de CONTADOR só o ADMIN muda (08/10/2026: a equipe marcava as
    empresas como contador). Para os demais vale o que já está gravado — a
    caixa nem aparece, e o que vier no form é ignorado."""
    if current_user.is_admin():
        return bool(form.get('is_contador'))
    if not cliente_id:
        return False
    r = execute_query("SELECT is_contador FROM clientes WHERE id = %s",
                      (cliente_id,), fetch=True, fetch_one=True)
    return bool(r and r.get('is_contador'))


def _socios_e_contador_faltando(form, cliente_id=None):
    falta = []
    # contador: todo cadastro, menos o que é ele mesmo contador
    if not _eh_contador(form, cliente_id):
        kid = form.get('contador_id', type=int)
        k = execute_query("SELECT id, is_contador, situacao FROM clientes WHERE id = %s",
                          (kid,), fetch=True, fetch_one=True) if kid else None
        if not k or not k.get('is_contador') or k.get('situacao') != 'ATIVO' or kid == cliente_id:
            falta.append('contador responsável')
    # sócios: toda PJ. Filial travada: o quadro é o da raiz (já conferido lá);
    # aqui só o pró-labore, que é dela.
    cnpj = next((v for v in form.getlist('cpf_cnpj') if v.strip()), '')
    if form.get('tipo_pessoa') == 'PJ' and _filial_travada(cnpj, cliente_id)[0]:
        if any(x['pl'] is False for x in (_ler_socios(form) or [])):
            falta.append('pró-labore dos sócios (valor inválido)')
    elif form.get('tipo_pessoa') == 'PJ':
        socios = _ler_socios(form)
        if not socios:
            falta.append('sócios')
        else:
            ids = [s['id'] for s in socios]
            existem = _cadastro_resumo(ids)
            if len(set(ids)) != len(ids) or cliente_id in ids or any(i not in existem for i in ids):
                falta.append('sócios (confira a lista)')
            elif any(s['pct'] <= 0 for s in socios):
                falta.append('participação de cada sócio')
            elif abs(sum(s['pct'] for s in socios) - Decimal('100')) > Decimal('0.005'):
                falta.append('participação dos sócios somando 100%')
            elif any(s['capital'] is False or s['pl'] is False for s in socios):
                falta.append('capital e pró-labore dos sócios (valor inválido)')
            else:
                quadro = [{'nome': existem[s['id']]['nome'], 'tipo': existem[s['id']]['tipo'],
                           'qual': s['qual']} for s in socios]
                pj_adm = next((q for q in quadro if q['tipo'] == 'PJ' and qual_recusada_pj(q['qual'])), None)
                resp = next((q for s, q in zip(socios, quadro) if s['resp']), None)
                if pj_adm:
                    falta.append(f"qualificação de {pj_adm['nome']}: empresa sócia não pode ser "
                                 f"\"{pj_adm['qual']}\"")
                elif resp and not pode_ser_responsavel(resp, quadro):
                    falta.append(f"responsável: {resp['nome']} é só sócio(a) — a marca vai para quem administra")
    return falta


# ---------------------------------------------------------------------------
# Quadro de sócios da RAIZ (19/10/2026). O quadro é da empresa inteira (os 8
# primeiros dígitos do CNPJ), não de um cadastro: matriz e filiais guardam a
# MESMA cópia em socios_clientes e o salvar mantém as cópias iguais. Só o
# pró-labore é de cada cadastro. Regras do Anderson:
#   * filial cuja raiz já tem quadro (matriz ou outra filial) -> vem pronto e
#     TRAVADO; só o pró-labore fica aberto;
#   * matriz sem sócios -> a filial preenche e o quadro vai para as duas;
#   * matriz não cadastrada -> a filial salva; a matriz, quando vier, já abre
#     com o quadro;
#   * a matriz edita sempre, e a mudança vai para as filiais.
# ---------------------------------------------------------------------------
def _cnpj_digitos(v):
    d = re.sub(r'\D', '', v or '')
    return d if len(d) == 14 else ''


def _raiz_membros(cnpj, excluir=None):
    """Os outros cadastros PJ da mesma raiz, matriz (0001) primeiro."""
    d = _cnpj_digitos(cnpj)
    if not d:
        return []
    rows = execute_query(
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, avulso FROM clientes "
        "WHERE tipo_pessoa = 'PJ' AND REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '/', ''), '-', '') LIKE %s "
        "AND id <> %s", (d[:8] + '%', excluir or 0), fetch=True) or []
    for r in rows:
        r['matriz'] = _cnpj_digitos(r['cpf_cnpj'])[8:12] == '0001'
    return sorted(rows, key=lambda r: (not r['matriz'], r['id']))


def _quadro_da_raiz(cnpj, excluir=None):
    """(cadastro de onde vem o quadro, sócios dele) ou (None, []): o primeiro
    outro cadastro da raiz que TEM sócios — a matriz, se tiver."""
    for m in _raiz_membros(cnpj, excluir):
        soc = SocioCliente.get_by_cliente(m['id'])
        if soc:
            return m, soc
    return None, []


def _filial_travada(cnpj, cliente_id=None):
    """Cadastro de origem quando este CNPJ é FILIAL e a raiz já tem quadro em
    outro cadastro; senão None (matriz e filial "primeira" editam)."""
    d = _cnpj_digitos(cnpj)
    if not d or d[8:12] == '0001':
        return None, []
    return _quadro_da_raiz(d, cliente_id)


def _socios_json(soc, pls=None):
    """Linhas de socios_clientes -> o SOCIOS da tela. pls: {pf_id: pró-labore}
    deste cadastro, quando o quadro vem de outro."""
    resumo = _cadastro_resumo([x['pf_cliente_id'] for x in soc if x.get('pf_cliente_id')])
    out = []
    for x in soc:
        pid = x.get('pf_cliente_id')
        if pid not in resumo:
            continue
        pl = x.get('pro_labore') if pls is None else pls.get(pid)
        out.append(dict(resumo[pid], pct=str(x['percentual_participacao'] or ''),
                        resp=bool(x.get('responsavel')), qual=x.get('qualificacao') or '',
                        entrada=x['data_entrada'].isoformat() if x.get('data_entrada') else '',
                        capital='' if x.get('capital_valor') is None else str(x['capital_valor']),
                        pl='' if pl is None else str(pl)))
    return out


def _origem_json(m):
    return {'id': m['id'], 'numero': m.get('numero_cliente') or '', 'nome': m['nome_razao_social'],
            'matriz': bool(m.get('matriz')), 'avulso': bool(m.get('avulso')),
            'visivel': pode_ver_empresa(m['id'])}


def _gravar_quadro(cliente_id, form):
    """Salva os sócios deste cadastro respeitando a raiz: filial travada grava
    o quadro da raiz com o pró-labore DELA; os demais gravam o da tela e
    copiam para os outros cadastros da raiz (cada um com o próprio pró-labore)."""
    cnpj = next((v for v in form.getlist('cpf_cnpj') if v.strip()), '')
    tela = _ler_socios(form) or []
    origem, soc_ref = _filial_travada(cnpj, cliente_id)
    if origem:
        pls = {x['id']: x['pl'] for x in tela if x['pl'] is not False}
        quadro = [{'id': x['pf_cliente_id'], 'pct': Decimal(str(x['percentual_participacao'] or 0)),
                   'resp': bool(x.get('responsavel')), 'qual': x.get('qualificacao') or '',
                   'entrada': x.get('data_entrada'),
                   'capital': None if x.get('capital_valor') is None else Decimal(str(x['capital_valor'])),
                   'pl': pls.get(x['pf_cliente_id'])}
                  for x in soc_ref if x.get('pf_cliente_id')]
        _salvar_socios(cliente_id, quadro)
        return
    _salvar_socios(cliente_id, tela)
    if not tela:
        return
    for m in _raiz_membros(cnpj, cliente_id):
        pls = {x['pf_cliente_id']: (None if x.get('pro_labore') is None else Decimal(str(x['pro_labore'])))
               for x in SocioCliente.get_by_cliente(m['id']) if x.get('pf_cliente_id')}
        _salvar_socios(m['id'], [dict(x, pl=pls.get(x['id'])) for x in tela])


def _salvar_socios(cliente_id, socios):
    """Deixa os sócios ATUAIS iguais à lista do formulário: quem saiu da
    lista ganha data de saída (hoje) e vira ex-sócio — não se apaga —,
    quem entrou é acrescentado e participação/responsável/qualificação/
    entrada são atualizados."""
    atuais = {s['pf_cliente_id']: s for s in SocioCliente.get_by_cliente(cliente_id) if s.get('pf_cliente_id')}
    novos = {s['id']: s for s in socios}
    dados = _cadastro_resumo(list(novos))
    for pid, s in atuais.items():
        if pid not in novos:
            SocioCliente.registrar_saida(s['id'], date.today())
            registrar('escrita.saida_socio', 'cadastros', tabela='socios_clientes', registro_id=s['id'],
                      antes={'cliente_id': cliente_id, 'nome': s.get('nome'), 'cpf': s.get('cpf'),
                             **rotulo_empresa(cliente_id)},
                      depois={'cliente_id': cliente_id, 'data_saida': date.today().isoformat()})
    resp_id = None
    for pid, s in novos.items():
        d = dados.get(pid) or {}
        if pid in atuais:
            a = atuais[pid]
            if Decimal(str(a.get('percentual_participacao') or 0)) != s['pct']:
                execute_query("UPDATE socios_clientes SET percentual_participacao = %s WHERE id = %s",
                              (s['pct'], a['id']))
                registrar('escrita.alterou_socio', 'cadastros', tabela='socios_clientes', registro_id=a['id'],
                          antes={'cliente_id': cliente_id, 'socio_nome': a.get('nome'),
                                 'percentual_participacao': str(a.get('percentual_participacao'))},
                          depois={'cliente_id': cliente_id, 'socio_nome': a.get('nome'), 'percentual_participacao': str(s['pct'])})
            novo_q = s['qual'] if s['qual'] and s['qual'] != (a.get('qualificacao') or '') else None
            novo_e = s['entrada'] if s['entrada'] and s['entrada'] != a.get('data_entrada') else None
            if novo_q or novo_e:
                SocioCliente.atualizar_dados(a['id'], novo_q, novo_e)
                ent_antes = a.get('data_entrada')
                registrar('escrita.alterou_socio', 'cadastros', tabela='socios_clientes', registro_id=a['id'],
                          antes={'cliente_id': cliente_id, 'socio_nome': a.get('nome'),
                                 'qualificacao': a.get('qualificacao'),
                                 'data_entrada': ent_antes.isoformat() if ent_antes else None},
                          depois={'cliente_id': cliente_id, 'socio_nome': a.get('nome'),
                                  'qualificacao': novo_q or a.get('qualificacao'),
                                  'data_entrada': (novo_e or ent_antes).isoformat() if (novo_e or ent_antes) else None})
            mud_valor = {}
            for col, chave in (('capital_valor', 'capital'), ('pro_labore', 'pl')):
                v_antes = a.get(col)
                v_antes = Decimal(str(v_antes)) if v_antes is not None else None
                if v_antes != s[chave]:
                    mud_valor[col] = (v_antes, s[chave])
            if mud_valor:
                SocioCliente.atualizar_valores(a['id'], s['capital'], s['pl'])
                registrar('escrita.alterou_socio', 'cadastros', tabela='socios_clientes', registro_id=a['id'],
                          antes={'cliente_id': cliente_id, 'socio_nome': a.get('nome'),
                                 **{c: None if v[0] is None else str(v[0]) for c, v in mud_valor.items()}},
                          depois={'cliente_id': cliente_id, 'socio_nome': a.get('nome'),
                                  **{c: None if v[1] is None else str(v[1]) for c, v in mud_valor.items()}})
            if s['resp']:
                resp_id = a['id']
        else:
            sid = SocioCliente.create(cliente_id=cliente_id, pf_cliente_id=pid, nome=d.get('nome'),
                                      cpf=d.get('doc'), percentual_participacao=s['pct'],
                                      responsavel=False, ativo=True,
                                      qualificacao=s['qual'], data_entrada=s['entrada'],
                                      capital_valor=s['capital'], pro_labore=s['pl'])
            registrar('escrita.criou_socio', 'cadastros', tabela='socios_clientes', registro_id=sid,
                      depois={'cliente_id': cliente_id, 'pf_cliente_id': pid, 'nome': d.get('nome'),
                              'cpf': d.get('doc'), 'percentual_participacao': str(s['pct']),
                              'capital_valor': None if s['capital'] is None else str(s['capital']),
                              'pro_labore': None if s['pl'] is None else str(s['pl']),
                              'qualificacao': s['qual'] or None,
                              'data_entrada': s['entrada'].isoformat() if s['entrada'] else None,
                              **rotulo_empresa(cliente_id)})
            if s['resp']:
                resp_id = sid
    SocioCliente.set_responsavel(cliente_id, _resp_valido(cliente_id, resp_id))


def _resp_valido(cliente_id, resp_id):
    """A marca de responsável só fica em quem administra (pode_ser_responsavel).
    Pedida para quem é só sócio — ou para ninguém — vai para o único que
    administra; havendo mais de um, para o primeiro (fica para o usuário trocar)."""
    soc = [dict(x, tipo=x.get('tipo_pessoa') or 'PF', qual=x.get('qualificacao') or '')
           for x in SocioCliente.get_by_cliente(cliente_id)]
    pode = [x['id'] for x in soc if pode_ser_responsavel(x, soc)]
    if resp_id in pode:
        return resp_id
    return pode[0] if pode else None


def _salvar_contador(cliente_id, contador_id):
    """Põe o contador escolhido. Os vínculos que já existem ficam (servem à
    captura pelo certificado do contador)."""
    if not contador_id:
        return
    ja = execute_query("SELECT id FROM cliente_contadores WHERE cliente_id = %s AND contador_id = %s",
                       (cliente_id, contador_id), fetch=True, fetch_one=True)
    if not ja:
        ClienteContador.vincular_contador(cliente_id, contador_id)
        registrar('escrita.vinculou_contador', 'cadastros', tabela='clientes', registro_id=cliente_id,
                  depois={'contador_id': contador_id})


def _ctx_form(cliente=None):
    """Tudo o que o formulário (novo, editar e janela de sócio) precisa."""
    from routes.grupos import _grupos_com_empresas
    grupos_cards = [g for g in _grupos_com_empresas() if g['situacao'] == 'ATIVO']
    grupos_json = [{'id': g['id'], 'nome': g['nome'], 'sigla': g['sigla'],
                    'empresas': len(g['empresas']), 'locais': g['locais'],
                    'numeros': [e['numero'] for e in g['empresas'] if e['numero']][:8],
                    'busca': g['busca']} for g in grupos_cards]
    contadores = [{'id': k['id'], 'numero': k['numero_cliente'] or '', 'nome': (k['nome_razao_social'] or '').strip(),
                   'tipo': k['tipo_pessoa'], 'cert': bool(k.get('tem_certificado')),
                   'validade': k['cert_validade'].strftime('%d/%m/%Y') if k.get('cert_validade') else ''}
                  for k in ClienteContador.listar_contadores()]
    uso = {r['contador_id']: r['n'] for r in (execute_query(
        "SELECT contador_id, COUNT(*) n FROM cliente_contadores GROUP BY contador_id", fetch=True) or [])}
    for k in contadores:
        k['atende'] = uso.get(k['id'], 0)
    ctx = {'ramos_atividade': RamoAtividade.get_all(situacao='ATIVO'),
           'grupos': GrupoCliente.get_all(situacao='ATIVO'), 'grupos_json': grupos_json,
           'contadores_json': contadores, 'proximo_pf': _proximo_numero_pf(), 'pode_fin': _pode_fin(),
           'janela': request.args.get('janela') == '1' or request.form.get('janela') == '1',
           'pre': {'tipo': request.args.get('tipo', ''), 'nome': request.args.get('nome', ''),
                   'grupo_id': request.args.get('grupo_id', type=int), 'doc': request.args.get('doc', '')}}
    if cliente:
        cid = cliente['id']
        ctx['ramos_cliente'] = [r['id'] for r in RamoAtividade.get_by_cliente(cid)]
        ctx['grupos_cliente'] = Cliente.get_grupos(cid)
        ks = ClienteContador.contadores_do_cliente(cid)
        ctx['contador_atual'] = ks[0]['id'] if ks else None
        soc = SocioCliente.get_by_cliente(cid)
        ctx['socios_json'] = _socios_json(soc)
        ctx['raiz'] = None
        if cliente.get('tipo_pessoa') == 'PJ':
            origem, soc_ref = _filial_travada(cliente.get('cpf_cnpj'), cid)
            if origem:
                pls = {x['pf_cliente_id']: x.get('pro_labore') for x in soc if x.get('pf_cliente_id')}
                ctx['socios_json'] = _socios_json(soc_ref, pls)
                ctx['raiz'] = {'travado': True, 'origem': _origem_json(origem)}
        ctx['pessoa'] = PessoaDados.get(cid) if cliente.get('tipo_pessoa') == 'PF' else {}
        ctx['documentos'] = ([dict(d, contrato=bool(d['contrato'])) for d in Documentos.listar(cid)]
                             if cliente.get('tipo_pessoa') == 'PF' else [])
        ctx['ies_rurais'] = [{'uf': x['uf'] or '', 'ie': x['ie'], 'propriedade': x['propriedade'] or '',
                              'municipio': x['municipio'] or ''} for x in IE.listar(cid) if x['tipo'] == 'RURAL']
    else:
        ctx['ramos_cliente'] = []
        ctx['grupos_cliente'] = []
        ctx['contador_atual'] = _contador_padrao()
        ctx['socios_json'] = []
        ctx['raiz'] = None
        ctx['pessoa'] = {}
        ctx['documentos'] = []
        ctx['ies_rurais'] = []
    ctx['contabilidades_json'] = _contab_combo(cliente.get('contabilidade_anterior_id') if cliente else None)
    ctx['IE_UFS'] = IE.UFS
    ctx.update(ESTADOS_CIVIS=ESTADOS_CIVIS, REGIMES_BENS=REGIMES_BENS, NIVEIS_GOVBR=NIVEIS_GOVBR,
               COM_REGIME=COM_REGIME, UFS=UFS, OPCOES=Opcoes.todas(),
               TIPOS_DOC=TIPOS_DOC, ORGAO_SUGERIDO=ORGAO_SUGERIDO,
               hoje_iso=date.today().isoformat())
    if current_user.is_admin():
        from routes.qrobo import csrf_token
        ctx['opcoes_csrf'] = csrf_token()   # o "+ Incluir" das listas (só admin)
    return ctx


def _enfeitar_cartoes(lista):
    """O que o CARTÃO de cliente mostra além do cadastro: ramos/segmentos e
    grupos (para os filtros), município com a cor de Grupos, regime, o grupo
    em miniatura e o nível/dias do certificado. Clientes e Avulsos usam a
    mesma função — o cartão das duas telas é o mesmo (14/10/2026)."""
    if not lista:
        return lista
    from routes.grupos import (_cor_cidade as _g_cor_cidade, _cidade_titulo as _g_cidade_titulo,
                               _sem_acento as _g_sem_acento)
    mapa_raiz = _mapa_raiz()
    rel_ramo, rel_grupo = {}, {}
    # uma ida ao banco só: ramos (com o segmento) e grupos de cada cliente
    for r in execute_query("SELECT 'R' AS t, x.cliente_id, x.ramo_atividade_id AS ref, ra.segmento "
                           "FROM cliente_ramo_atividade_relacao x "
                           "JOIN ramos_atividade ra ON ra.id = x.ramo_atividade_id "
                           "UNION ALL SELECT 'G', cliente_id, grupo_id, NULL FROM cliente_grupo_relacao",
                           fetch=True) or []:
        if r['t'] == 'G':
            rel_grupo.setdefault(r['cliente_id'], set()).add(str(r['ref']))
            continue
        d = rel_ramo.setdefault(r['cliente_id'], {'ramos': set(), 'segs': set()})
        d['ramos'].add(str(r['ref']))
        if r['segmento']:
            d['segs'].add(r['segmento'])
    qtd_grupo = {}
    for gs in rel_grupo.values():
        for g in gs:
            qtd_grupo[g] = qtd_grupo.get(g, 0) + 1
    for c in lista:
        d = rel_ramo.get(c['id'], {'ramos': set(), 'segs': set()})
        c['f_ramos'] = ','.join(sorted(d['ramos']))
        c['f_segs'] = ','.join(sorted(d['segs']))
        c['f_grupos'] = ','.join(sorted(rel_grupo.get(c['id'], ())))
        c['f_reg'] = 'pf' if c['tipo_pessoa'] == 'PF' else (c.get('regime_tributario') or '').lower()
        # tipo jurídico (fase A, 14/10/2026): nome curto e família do código da Receita
        c['f_nat'], c['f_nat_fam'] = (natureza_classificar(c.get('natureza_codigo'), c.get('natureza_juridica'))
                                      if c['tipo_pessoa'] == 'PJ' else ('', ''))
        # matriz e filiais (14/10/2026): pela raiz do CNPJ
        c['f_raiz_papel'], c['f_raiz_info'] = _papel_raiz(c, mapa_raiz)
        # Município com a MESMA cor da tela de Grupos (a cidade tem uma cor só)
        local = f"{_g_cidade_titulo(c['end_cidade'])}/{c.get('end_uf') or ''}" if c.get('end_cidade') else ''
        c['f_local'] = local.rstrip('/')
        c['f_cid_cor'] = _g_cor_cidade(_g_sem_acento(local)) if local else 0
        # Grupo no cartão como em Grupos: sigla, nome e quantas empresas tem
        gids = sorted(rel_grupo.get(c['id'], ()))
        if gids:
            nome_g = (c.get('grupo_nome') or '').split(', ')[0]
            palavras = re.findall(r'\w+', nome_g)
            c['f_grupo'] = {'nome': nome_g, 'qtd': qtd_grupo.get(gids[0], 0),
                            'sigla': (''.join(w[0] for w in palavras[:2]) if len(palavras) > 1
                                      else nome_g[:2]).upper()}

    # Nível do certificado por cliente para a coluna Certificado — MESMA regra
    # única do classificar_validade (verde 'ok' / âmbar 'laranja' / vermelho
    # 'vencido'); 'sem_data' quando não há certificado. A tela nunca mostra
    # vazio: sem cert vira "sem certificado" em cinza.
    hoje = date.today()
    for c in lista:
        c['cert_nivel'] = DfeCertificado.classificar_validade(
            c.get('cert_validade')).get('nivel', 'sem_data')
        v = c.get('cert_validade')
        v = v.date() if hasattr(v, 'date') else v
        c['cert_dias'] = (v - hoje).days if v else None

    return lista


def _mapa_raiz():
    """raiz (8 dígitos) -> {'matriz': {id, numero, nome} | None, 'ids': [...]}
    de todos os cadastros PJ. Matriz = final 0001 (14/10/2026)."""
    m = {}
    for r in execute_query("SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes "
                           "WHERE tipo_pessoa = 'PJ'", fetch=True) or []:
        d = re.sub(r'\D', '', r.get('cpf_cnpj') or '')
        if len(d) != 14:
            continue
        g = m.setdefault(d[:8], {'matriz': None, 'ids': []})
        g['ids'].append(r['id'])
        if d[8:12] == '0001':
            g['matriz'] = {'id': r['id'], 'numero': r['numero_cliente'] or '', 'nome': r['nome_razao_social']}
    return m


def _papel_raiz(c, mapa):
    """('matriz', n_filiais) | ('filial', matriz) | (None, None) para o cartão e a ficha."""
    d = re.sub(r'\D', '', c.get('cpf_cnpj') or '')
    g = mapa.get(d[:8]) if len(d) == 14 else None
    if not g or len(g['ids']) < 2:
        return None, None
    if d[8:12] == '0001':
        return 'matriz', len(g['ids']) - 1
    return 'filial', g['matriz']


def _contab_combo(atual_id=None):
    """Escritórios ativos para o campo; o da empresa entra mesmo se inativo
    (senão o cartão do escolhido sumiria)."""
    lista = Contabilidade.para_combo()
    if atual_id and not any(c['id'] == atual_id for c in lista):
        c = Contabilidade.get(atual_id)
        if c:
            lista.append({'id': c['id'], 'nome': c['nome'] + ' (inativo)', 'cidade': c.get('cidade') or '',
                          'uf': c.get('uf') or '', 'contato': c.get('contato') or '',
                          'telefone': c.get('telefone') or '', 'email': c.get('email') or '', 'empresas': 0})
    return lista


def _lista_clientes_tela():
    """Carteira inteira como a tela de Clientes a mostra (cartões, filtros na
    tela e relatórios): get_all + ramos/segmentos/grupos + nível do certificado.
    Uma fonte só, para o relatório nunca contar diferente dos cartões."""
    # Filtros na tela, como em Grupos (10/10/2026, pedido do Anderson): a
    # lista vem inteira (323 em 10/10) e os botões/painel filtram sem
    # recarregar. Os parâmetros da URL só dizem com que filtro a tela abre.
    result = Cliente.get_all(filters={'sort_by': 'numero', 'sort_dir': 'asc'}, page=1, per_page=5000)
    if not result:
        return result
    _enfeitar_cartoes(result.get('clientes') or [])
    return result


def _kpis_clientes(lista):
    """Contagem da fita de KPIs (e do relatório) sobre a lista que se mostra."""
    niveis = [c.get('cert_nivel') for c in lista]
    return {'ativos': sum(1 for c in lista if c.get('situacao') == 'ATIVO'),
            'inativos': sum(1 for c in lista if c.get('situacao') == 'INATIVO'),
            'com_certificado': sum(1 for n in niveis if n in ('ok', 'laranja')),
            'cert_ok': niveis.count('ok'),
            'cert_vencendo': niveis.count('laranja'),
            'cert_vencido': niveis.count('vencido'),
            'cert_sem': sum(1 for n in niveis if n not in ('ok', 'laranja', 'vencido'))}


@clientes.route('/clientes')
@permission_required('clientes.index')
def index():
    """Lista todos os clientes com filtros e paginação"""
    try:
        # Parâmetros de filtro
        situacao    = request.args.get('situacao', '')
        regime      = request.args.get('regime', '')
        tipo_pessoa = request.args.get('tipo_pessoa', '')
        grupo_id    = request.args.get('grupo_id', '')
        ramo_id     = request.args.get('ramo_id', '')
        segmento    = request.args.get('segmento', '')
        busca       = request.args.get('busca', '')
        busca_tipo  = request.args.get('busca_tipo', 'nome')   # 'nome' | 'numero' | 'ramo'
        sort_by     = request.args.get('sort_by',  'numero')   # 'nome' | 'numero'
        sort_dir    = request.args.get('sort_dir', 'asc')      # 'asc'  | 'desc'
        page        = request.args.get('page', 1, type=int)
        per_page    = 50

        # Buscar clientes com filtros
        filters = {}
        if situacao:
            filters['situacao'] = situacao
        if regime:
            filters['regime_tributario'] = regime
        if tipo_pessoa:
            filters['tipo_pessoa'] = tipo_pessoa
        if grupo_id:
            filters['grupo_id'] = grupo_id
        if ramo_id:
            filters['ramo_id'] = ramo_id
        if segmento in SEGMENTO_NOME:
            filters['segmento'] = segmento
        if busca:
            filters['busca']      = busca
            filters['busca_tipo'] = busca_tipo
        filters['sort_by']  = sort_by
        filters['sort_dir'] = sort_dir

        result = _lista_clientes_tela()
        grupos = GrupoCliente.get_all(situacao='ATIVO')
        ramos_atividade = RamoAtividade.get_all(situacao='ATIVO')
        # Vigias de CNPJ que acharam mudança na Receita e ninguém tratou ainda.
        try:
            from utils import cnpj_vigia as VG
            vigias_mudou = VG.com_novidade()
            vigias_ativas = VG.em_vigia()
            from utils.cnpj_receita import base_em_br
            receita_base = base_em_br()          # só lê app_config: não consulta a Receita
        except Exception:
            logger.exception('[clientes] vigias com novidade falhou')
            vigias_mudou, vigias_ativas, receita_base = [], [], None

        # Verificar se houve erro na obtenção dos dados
        if result is None:
            flash('Erro ao buscar clientes. Verifique a conexão com o banco de dados.', 'danger')
            result = {'clientes': [], 'page': 1, 'total_pages': 0, 'total': 0,
                      'stats': {'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0, 'pj': 0,
                                'com_certificado': 0, 'cert_vencendo': 0}}

        # stats is now bundled inside result (no separate DB call needed)
        stats = result.get('stats', {'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0,
                                     'pj': 0, 'com_certificado': 0, 'cert_vencendo': 0})

        # Fita de KPIs contada sobre a MESMA lista dos cartões (11/10/2026): o
        # COUNT global incluía certificado de avulso (246 contra 244 nos cartões).
        # Vencido = tem certificado e a validade passou; sem certificado = nunca
        # vinculou. Os quatro de certificado somam a carteira inteira.
        stats = dict(stats, **_kpis_clientes(result['clientes']))

        return render_template('clientes/index.html',
                             nat_familias=NAT_FAMILIAS,
                             clientes=result['clientes'],
                             page=result['page'],
                             total_pages=result['total_pages'],
                              total=result['total'],
                              stats=stats,
                              vigias_mudou=vigias_mudou, vigias_ativas=vigias_ativas,
                              receita_base=receita_base,
                              grupos=grupos,
                              ramos_atividade=ramos_atividade,
                              sort_by=sort_by,
                              sort_dir=sort_dir,
                              filtros={'situacao': situacao, 'regime': regime,
                                       'tipo_pessoa': tipo_pessoa, 'grupo_id': grupo_id,
                                       'ramo_id': ramo_id, 'segmento': filters.get('segmento', ''),
                                       'busca': busca,
                                       'busca_tipo': busca_tipo})
    
    except Exception as e:
        flash(f'Erro ao carregar página de clientes: {str(e)}', 'danger')
        return render_template('clientes/index.html',
                              clientes=[],
                             page=1,
                             total_pages=0,
                              total=0,
                              stats={'total': 0, 'ativos': 0, 'inativos': 0, 'pf': 0, 'pj': 0,
                                     'com_certificado': 0, 'cert_vencendo': 0},
                              grupos=[],
                              ramos_atividade=[],
                              sort_by='numero',
                              sort_dir='asc',
                              filtros={'situacao': '', 'regime': '', 'tipo_pessoa': '',
                                       'grupo_id': '', 'ramo_id': '', 'segmento': '',
                                       'busca': '', 'busca_tipo': 'nome'})


@clientes.route('/clientes/relatorio')
@permission_required('clientes.index')
def relatorio():
    """Relatório de clientes (11/10/2026): resumido ou completo, em PDF ou Excel.
    Sai com os clientes que estão NA TELA (``ids`` na ordem dos cartões) e com os
    filtros escritos no cabeçalho; sem ``ids``, a carteira inteira."""
    import os
    from io import BytesIO
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from flask import send_file
    from utils import relatorio_clientes as RC

    tipo = request.args.get('tipo', 'resumido')
    formato = request.args.get('formato', 'pdf')
    if tipo not in ('resumido', 'completo') or formato not in ('pdf', 'xlsx'):
        return jsonify({'error': 'Use tipo=resumido|completo e formato=pdf|xlsx.'}), 400

    lista = (_lista_clientes_tela() or {}).get('clientes') or []
    ids = [int(x) for x in (request.args.get('ids') or '').split(',') if x.strip().isdigit()]
    if request.args.get('ids') is not None:
        por_id = {c['id']: c for c in lista}
        lista = [por_id[i] for i in ids if i in por_id]   # a ordem da tela

    # Completo: o que o cartão não carrega (fantasia, endereço, CNAE, porte, datas)
    extra = {}
    if tipo == 'completo' and lista:
        marc = ','.join(['%s'] * len(lista))
        for r in execute_query(
                f"""SELECT c.id, c.nome_fantasia, c.porte_empresa, c.cnae_fiscal, c.cnae_fiscal_descricao,
                           c.data_inicio_atividade, c.data_inicio_contrato, c.criado_em,
                           e.cep, e.logradouro, e.numero, e.complemento, e.bairro
                      FROM clientes c
                      LEFT JOIN enderecos_clientes e ON e.cliente_id = c.id AND e.principal = 1
                     WHERE c.id IN ({marc})""", tuple(c['id'] for c in lista), fetch=True) or []:
            extra.setdefault(r['id'], r)

    registrar('leitura.exportou_arquivo', 'cadastros', tabela='clientes',
              depois={'relatorio': f'clientes {tipo}', 'formato': formato, 'clientes': len(lista)})

    logo = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'static', 'images', 'logo.png')
    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    ctx = {'tipo': tipo, 'clientes': lista, 'extra': extra, 'kpis': _kpis_clientes(lista),
           'segmentos': SEGMENTO_NOME, 'filtros': (request.args.get('filtros') or '').strip()[:300],
           'gerado_em': agora.strftime('%d/%m/%Y %H:%M'),
           'gerado_por': getattr(current_user, 'nome', '') or '',
           'logo_path': logo if os.path.exists(logo) else None}
    if formato == 'xlsx':
        dados = RC.gerar_xlsx(ctx)
        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else:
        dados = RC.gerar_pdf(ctx)
        mime = 'application/pdf'
    nome = f"clientes-{tipo}-{agora.strftime('%Y-%m-%d')}.{formato}"
    return send_file(BytesIO(dados), as_attachment=True, download_name=nome, mimetype=mime)


def _render_novo():
    return render_template('clientes/form.html', COBRANCAS=COBRANCAS, cliente=None, departamentos_contato=ContatoCliente.departamentos_disponiveis(), **_ctx_form())


def _render_editar(cliente, endereco_principal):
    return render_template('clientes/form.html', COBRANCAS=COBRANCAS, cliente=cliente,
                           endereco_principal=endereco_principal,
                           departamentos_contato=ContatoCliente.departamentos_disponiveis(),
                           contatos_existentes=len(_cts := ContatoCliente.get_by_cliente(cliente['id'])),
                           contatos_sem_celular=sum(1 for k in _cts if k.get('ativo', 1) and not (k.get('celular') or '').strip()),
                           pf_contato=(_contato_da_pessoa_pf(cliente['id'], cliente.get('nome_razao_social'))
                                       if cliente.get('tipo_pessoa') == 'PF' else None),
                           **_ctx_form(cliente))


@clientes.route('/clientes/novo', methods=['GET', 'POST'])
@login_required
def novo():
    """Criar novo cliente"""
    if request.method == 'POST':
        # The form has duplicate-named fields (PF + PJ sections both submit
        # 'cpf_cnpj' and 'nome_razao_social'). The hidden section sends an empty
        # value that appears first in getlist(); pick the first non-empty one.
        cpf_cnpj = next((v for v in request.form.getlist('cpf_cnpj') if v.strip()), '')
        nome_razao_social = next((v for v in request.form.getlist('nome_razao_social') if v.strip()), '')
        numero_cliente = _numero_inteiro(request.form.get('numero_cliente', ''))
        
        if Cliente.existe_cpf_cnpj(cpf_cnpj):
            # REDE DE SEGURANÇA: se o duplicado for um AVULSO, "já cadastrado"
            # manda o usuário procurar na lista de clientes uma empresa que ele
            # NUNCA vai achar (avulso não aparece lá). Diz onde ela está.
            av = Cliente.avulso_por_documento(cpf_cnpj)
            if av:
                flash(f'Esta empresa já existe como AVULSA: '
                      f'{av["nome_razao_social"]}. Abra a aba Avulsos e clique '
                      f'em "Converter em cliente" — assim ela mantém tudo o que '
                      f'já foi guardado.', 'warning')
                return redirect(url_for('clientes.avulsos', busca=cpf_cnpj))
            flash('CPF/CNPJ já cadastrado!', 'danger')
            return redirect(url_for('clientes.novo'))
        
        # AVULSO: ato explícito (botão com confirmação na tela). Não é "número
        # em branco" — em branco significa "usa o ID automático".
        eh_avulso = request.form.get('avulso') == '1'
        if eh_avulso:
            numero_cliente = ''

        # NÚMERO OU AVULSO (05/10/2026): cliente sem número não existe mais —
        # a AR Transportes entrou assim e ficou fora da carteira E dos avulsos.
        if not eh_avulso and not numero_cliente:
            flash('Digite o número do cliente. Se a empresa ainda não tem número, '
                  'cadastre como Cliente Avulso.', 'danger')
            return _render_novo()

        # Validar número do cliente se fornecido
        if numero_cliente and Cliente.existe_numero_cliente(numero_cliente):
            flash(f'Número do cliente "{numero_cliente}" já está em uso!', 'danger')
            return _render_novo()
        
        # Validação de campos obrigatórios
        if not request.form.get('tipo_pessoa') or not nome_razao_social or not cpf_cnpj:
            flash('Preencha todos os campos obrigatórios.', 'danger')
            return _render_novo()
        # 09/11/2026: grava incompleto (o que falta vai para o A CADASTRAR); só valor errado impede.
        travas = _travas(request.form, eh_avulso)
        if travas:
            flash(_msg_travas(travas), 'danger')
            return _render_novo()
        
        # Criar cliente
        data = {
            'numero_cliente': numero_cliente if numero_cliente else None,
            'tipo_pessoa': request.form.get('tipo_pessoa'),
            'nome_razao_social': nome_razao_social,
            'nome_fantasia': request.form.get('nome_fantasia'),
            'cpf_cnpj': cpf_cnpj,
            'inscricao_estadual': request.form.get('inscricao_estadual'),
            'inscricao_municipal': request.form.get('inscricao_municipal'),
            'email': request.form.get('email'),
            'telefone': request.form.get('telefone'),
            'celular': request.form.get('celular'),
            'regime_tributario': request.form.get('regime_tributario'),
            'porte_empresa': request.form.get('porte_empresa'),
            'cnae_fiscal': request.form.get('cnae_fiscal'),
            'cnae_fiscal_descricao': request.form.get('cnae_fiscal_descricao'),
            'situacao': request.form.get('situacao', 'ATIVO'),
            'data_inicio_atividade': request.form.get('data_inicio_atividade'),
            'data_inicio_contrato': request.form.get('data_inicio_contrato'),
            'observacoes': request.form.get('observacoes'),
            'nire': request.form.get('nire'),
            'nire_data_registro': request.form.get('nire_data_registro'),
            # cobrança dos honorários: só admin + financeiro.honorarios (09/11/2026); os demais deixam Pendente
            'cobranca': request.form.get('cobranca') if _pode_fin() else None,
            'aberta_pela_casa': 1 if request.form.get('aberta_pela_casa') else 0,
            'avulso': eh_avulso,
            'criado_por': current_user.id
        }
        # E1: 'None'/'null'/'' de qualquer caminho viram VAZIO antes de gravar.
        data = limpar_form(data)

        cliente_id = Cliente.create(data)
        if cliente_id:
            _gravar_contatos_do_formulario(cliente_id)
            _sincronizar_contato_pf(cliente_id)
            # vigia de CNPJ armada no formulário ANTES de salvar ganha o dono
            from utils import cnpj_vigia as VG
            VG.vincular_cliente(cpf_cnpj, cliente_id)
            _vigia_concluir_se_aplicada(cpf_cnpj, cliente_id)
            IE.espelhar_do_cadastro(cliente_id)          # a IE do formulário entra na lista de IEs

        if cliente_id:
            # AUDITORIA (D2): quem criou este cadastro.
            registrar('escrita.criou_cliente', 'cadastros', tabela='clientes',
                      registro_id=cliente_id, depois=data)

            # Marca de CONTADOR (fora do INSERT: a lista de colunas do create()
            # é explícita e não inclui is_contador).
            if _eh_contador(request.form):
                ClienteContador.marcar_como_contador(cliente_id, 1)

            # Adicionar aos ramos de atividade selecionados (múltiplos)
            ramos_ids = request.form.getlist('ramos_atividade_ids')
            for ramo_id in ramos_ids:
                try:
                    RamoAtividade.add_cliente(int(ramo_id), cliente_id)
                except:
                    pass  # Ignora erros de duplicação

            # Grupo de empresas, contador e sócios (obrigatórios desde 07/10/2026)
            if request.form.get('grupo_id', type=int):
                _salvar_grupo(cliente_id, request.form.get('grupo_id', type=int))
            if not _eh_contador(request.form):
                _salvar_contador(cliente_id, request.form.get('contador_id', type=int))
            if request.form.get('tipo_pessoa') == 'PJ':
                _gravar_quadro(cliente_id, request.form)
                _salvar_origem(cliente_id, request.form)
                _salvar_natureza(cliente_id, request.form)
            else:
                _salvar_pessoa(cliente_id, request.form)
                _rur = IE.ler_rurais_json(request.form.get('ies_rurais_json'))
                if _rur is not None:
                    IE.salvar_rurais(cliente_id, _rur)
            
            # Salvar endereço se fornecido
            cep = request.form.get('cep', '').strip()
            logradouro = limpar_logradouro(request.form.get('logradouro', ''))   # "RUA RUA X" -> "RUA X"
            
            if cep or logradouro:  # Se pelo menos um campo de endereço foi preenchido
                try:
                    endereco_data = {
                        'tipo': 'COMERCIAL',  # Padrão para PJ
                        'cep': cep,
                        'logradouro': logradouro,
                        'numero': request.form.get('numero', '').strip(),
                        'complemento': request.form.get('complemento', '').strip(),
                        'bairro': request.form.get('bairro', '').strip(),
                        'cidade': request.form.get('cidade', '').strip(),
                        'estado': request.form.get('estado', '').strip(),
                        'pais': 'Brasil',
                        'principal': True
                    }
                    
                    EnderecoCliente.create(
                        cliente_id=cliente_id,
                        tipo=endereco_data['tipo'],
                        cep=endereco_data['cep'],
                        logradouro=endereco_data['logradouro'],
                        numero=endereco_data['numero'],
                        complemento=endereco_data['complemento'],
                        bairro=endereco_data['bairro'],
                        cidade=endereco_data['cidade'],
                        estado=endereco_data['estado'],
                        pais=endereco_data['pais'],
                        principal=endereco_data['principal']
                    )
                except Exception as e:
                    # Se houver erro no endereço, não impede a criação do cliente
                    print(f"Erro ao salvar endereço: {e}")
            
            if request.form.get('janela') == '1':
                # aberto pela lista de sócios: avisa a tela de trás e fecha
                return render_template('clientes/janela_ok.html',
                                       cadastro=_cadastro_resumo([cliente_id]).get(cliente_id) or {'id': cliente_id})
            flash('Cliente criado com sucesso!', 'success')
            return redirect(url_for('clientes.detalhes', id=cliente_id))
        else:
            flash('Erro ao criar cliente!', 'danger')
    
    return _render_novo()


@clientes.route('/clientes/<int:id>')
@login_required
def detalhes(id):
    """Visualizar detalhes do cliente com abas"""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        flash('Cliente não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))

    # AUDITORIA (D2): leitura — alguém abriu a ficha deste cliente de propósito.
    registrar('leitura.abriu_ficha_cliente', 'cadastros', tabela='clientes',
              registro_id=id, depois={'numero': cliente.get('numero_cliente'),
                                       'nome': cliente.get('nome_razao_social')})

    with ThreadPoolExecutor(max_workers=11) as executor:
        f_enderecos = executor.submit(EnderecoCliente.get_by_cliente, id)
        f_contatos = executor.submit(ContatoCliente.get_by_cliente, id)
        f_grupos = executor.submit(Cliente.get_grupos, id)
        f_ramos = executor.submit(RamoAtividade.get_by_cliente, id)
        f_processos = executor.submit(Cliente.get_processos, id)
        f_tarefas = executor.submit(Cliente.get_tarefas, id)
        f_obrigacoes = executor.submit(Cliente.get_obrigacoes, id)
        f_cadastros_ad = executor.submit(CadastroAdicionalCliente.get_by_cliente, id)
        f_socios = executor.submit(SocioCliente.get_by_cliente, id)
        f_ex_socios = executor.submit(SocioCliente.get_by_cliente, id, True)
        f_certificado = executor.submit(DfeCertificado.get_by_cliente, id)
        f_contadores = executor.submit(ClienteContador.contadores_do_cliente, id)
        f_contadores_disp = executor.submit(ClienteContador.listar_contadores)
        f_responsaveis = executor.submit(ClienteResponsavel.por_cliente, id)
        f_funcionarios = executor.submit(ClienteResponsavel.usuarios_disponiveis)
        f_rs_convite = executor.submit(ClienteResponsavel.convite_ativo, id)
        f_contrato = executor.submit(FinContrato.por_cliente, id)

    enderecos = f_enderecos.result()
    contatos = f_contatos.result()
    grupos = f_grupos.result()
    ramos_atividade = f_ramos.result()
    processos = f_processos.result()
    tarefas = f_tarefas.result()
    obrigacoes = f_obrigacoes.result()
    cadastros_adicionais = f_cadastros_ad.result()
    socios = f_socios.result()
    ex_socios = f_ex_socios.result()
    qualif_socios = {s['pf_cliente_id']: qualificacao(s['pf_cliente_id'])
                     for s in socios if s.get('pf_cliente_id')}
    certificado = f_certificado.result()
    contadores = f_contadores.result()
    # Dropdown "adicionar": só is_contador=1, menos os JÁ VINCULADOS e o PRÓPRIO
    # cadastro (um cadastro não é contador de si mesmo). Compara sempre c['id']
    # (int) com o id da rota — nunca o dict inteiro.
    _ja = {c['id'] for c in contadores}
    contadores_disponiveis = [c for c in f_contadores_disp.result()
                              if c['id'] != id and c['id'] not in _ja]
    # Classificação da validade p/ a aba Certificado Digital (VENCIDO/vencendo/OK).
    if certificado:
        certificado['cert_status'] = DfeCertificado.classificar_validade(
            certificado.get('validade'))
    # Compute the active-partner total in Python instead of a separate DB query
    socios_total_percentual = sum(
        float(s.get('percentual_participacao') or 0)
        for s in socios
        if s.get('ativo')
    )

    # Cadastros ANP vinculados ao cliente — batch-load socios/produtos to avoid N+1
    cadastros_anp = CadastroAnp.get_by_cliente(id)
    if cadastros_anp:
        anp_socios_map = CadastroAnp.get_socios_by_cliente(id)
        anp_produtos_map = CadastroAnp.get_produtos_by_cliente(id)
        for anp in cadastros_anp:
            anp['socios'] = anp_socios_map.get(anp['id'], [])
            anp['produtos'] = anp_produtos_map.get(anp['id'], [])
    
    grupos_disponiveis = []

    # Estado do cursor de NSU, para a aba Certificado mostrar SEM precisar clicar
    # em "Consultar (teste)". Existe porque o 656 é invisível: a empresa some da
    # captura e ninguém percebe até faltar nota. Com o atraso na tela, o admin vê
    # o problema ao abrir o cliente.
    #
    # ult_nsu é o nosso cursor; sefaz_ult é o maior ultNSU que a SEFAZ mandou
    # usar nas últimas consultas. Diferença grande = cursor desalinhado, que é o
    # caso do seed manual.
    dfe_cursor = execute_query(
        """SELECT n.ult_nsu, n.ult_consulta, n.ult_status, n.proximo_permitido,
                  (SELECT MAX(l.ret_ult_nsu) FROM dfe_consulta_log l
                    WHERE l.cliente_id = n.cliente_id AND l.servico <> 'cte'
                      AND l.momento >= NOW() - INTERVAL 7 DAY) AS sefaz_ult,
                  (SELECT MAX(l.momento) FROM dfe_consulta_log l
                    WHERE l.cliente_id = n.cliente_id AND l.servico <> 'cte'
                      AND l.c_stat IN ('137','138')) AS ult_sucesso
             FROM dfe_nsu n WHERE n.cliente_id = %s""",
        (id,), fetch=True, fetch_one=True)
    if dfe_cursor:
        atraso = (dfe_cursor.get('sefaz_ult') or 0) - (dfe_cursor.get('ult_nsu') or 0)
        dfe_cursor['atraso'] = atraso if atraso > 0 else 0
        dfe_cursor['travado'] = bool(
            (dfe_cursor.get('ult_status') or '').startswith('656') and atraso > 0)

    # Aba "Acesso" (empresa reservada) — só o admin a vê e só ele a grava.
    reserva, reserva_csrf = None, None
    if current_user.is_admin():
        from utils.acesso import dados_reserva, quem_ve_empresa
        from routes.qrobo import csrf_token
        reserva, reserva_csrf = dados_reserva(id), csrf_token()
        if reserva is not None:
            reserva['quem'] = quem_ve_empresa(id) or []
            reserva['contagem'] = [sum(1 for u in reserva['quem'] if u['deps'][i]['aberto'])
                                   for i in range(len(reserva['quem'][0]['deps']) if reserva['quem'] else 0)]

    _falta = AC.pendencias_do_cadastro(id)   # o "O que falta" da tela única: a regra do A CADASTRAR
    # 09/11/2026: Financeiro da ficha só para admin + quem vê os honorários de todas as empresas;
    # empresa reservada mostra só o cadastro a quem não é admin nem exceção.
    from utils.acesso import empresa_reservada_para_mim
    bloq_reserva = empresa_reservada_para_mim(id)
    pode_fin = current_user.is_admin() or current_user.has_permission('financeiro.honorarios')
    # Inscrições estaduais (09/11/2026): várias, com tipo; o produtor rural (PF) tem as dele
    ies = IE.listar(id)
    produtor_rural = cliente.get('tipo_pessoa') == 'PF' and any('RURAL' in (r.get('nome') or '').upper() for r in ramos_atividade)
    return render_template('clientes/detalhes.html',
                         ies=ies, ie_isenta=IE.eh_isento(cliente.get('inscricao_estadual')),
                         produtor_rural=produtor_rural, uf_endereco=IE.uf_do_endereco(id),
                         IE_TIPOS=IE.TIPOS, IE_UFS=IE.UFS,
                         reserva=reserva, bloq_reserva=bloq_reserva, pode_fin=pode_fin,
                         reserva_csrf=reserva_csrf,
                         dfe_cursor=dfe_cursor,
                         cliente=cliente,
                         enderecos=enderecos,
                         contatos=contatos,
                         grupos=grupos,
                         grupos_disponiveis=grupos_disponiveis,
                         ramos_atividade=ramos_atividade,
                          processos=processos,
                          tarefas=tarefas,
                          obrigacoes=obrigacoes,
                         cadastros_adicionais=cadastros_adicionais,
                         cadastros_anp=cadastros_anp,
                         socios=socios,
                         ex_socios=ex_socios,
                         socio_em=(_empresas_do_socio(id) if cliente.get('tipo_pessoa') == 'PF' else []),
                         # "Copiar qualificação" (20/10/2026): o parágrafo do contrato
                         qualif=qualificacao(id),
                         qualif_socios=qualif_socios,
                         qualif_todos=(';\n\n'.join(q[0] for q in qualif_socios.values() if q[0]) + '.'
                                       if any(q[0] for q in qualif_socios.values()) else ''),
                         qualificacoes_socio=QUALIFICACOES_SOCIO,
                         hoje_iso=date.today().isoformat(),
                         pessoa=PessoaDados.get(id) if cliente.get('tipo_pessoa') == 'PF' else {},
                         conjuge_cad=_conjuge_cadastrado(id) if cliente.get('tipo_pessoa') == 'PF' else None,
                         documentos=Documentos.listar(id) if cliente.get('tipo_pessoa') == 'PF' else [],
                         natureza=(natureza_classificar(cliente.get('natureza_codigo'), cliente.get('natureza_juridica'))
                                   + (codigo_formatado(cliente.get('natureza_codigo')),)),
                         raiz_papel=_papel_raiz(cliente, _mapa_raiz()),
                         contab_anterior=(Contabilidade.get(cliente['contabilidade_anterior_id'])
                                          if cliente.get('contabilidade_anterior_id') else None),
                         pessoa_rotulo=PessoaDados.rotulo,
                         certificado=certificado,
                         contadores=contadores,
                         contadores_disponiveis=contadores_disponiveis,
                         socios_total_percentual=socios_total_percentual,
                         departamentos_contato=ContatoCliente.departamentos_disponiveis(),
                         responsaveis=f_responsaveis.result(),
                         funcionarios=f_funcionarios.result(),
                         rs_convite=f_rs_convite.result(),
                         contrato=(f_contrato.result() or [None])[0],
                         areas_atendimento=AREAS_ATENDIMENTO,
                         falta=_falta,
                         falta_cor={f['chave']: f['cor'] for f in _falta['faltam']},
                         alvo_pend=AC.ALVO,
                         cobrancas=COBRANCAS,
                         ramos_todos=[r for r in RamoAtividade.get_all(situacao='ATIVO')
                                      if (r.get('tipo_pessoa') or 'PJ') == ('PF' if cliente.get('tipo_pessoa') == 'PF' else 'PJ')],
                         contabilidades=_contab_combo(cliente.get('contabilidade_anterior_id')),
                         grupos_todos=GrupoCliente.get_all(situacao='ATIVO'))


# ── Tela única (Modelo C, 08/11/2026) ─────────────────────────────────────────────────────────
# Anderson (07/11/2026): "ter a FICHA e CADASTRO não está legal... confuso... uma tela só". Na ficha,
# o valor com sublinhado pontilhado vira campo ao clicar; o "Salvar alterações" do topo manda só o que
# mudou para cá. Só entram campos SIMPLES (uma coluna, sem efeito colateral). Número, razão social e
# CNPJ ficam fora: mudar qualquer um renomeia a pasta da empresa no Dropbox, e isso continua no editor.
_CAMPOS_TEXTO = {'inscricao_estadual': 30, 'inscricao_municipal': 30, 'nire': 20,
                 'email': 255, 'telefone': 20, 'celular': 20, 'observacoes': 5000}
_CAMPOS_DATA = ('nire_data_registro', 'data_inicio_contrato')
_CAMPOS_OPCAO = {'regime_tributario': ('SIMPLES', 'LUCRO_PRESUMIDO', 'LUCRO_REAL', 'MEI'),
                 'porte_empresa': ('MEI', 'ME', 'EPP', 'MEDIO', 'GRANDE'),
                 'cobranca': tuple(v for v, _ in COBRANCAS)}
_ROTULO_CAMPO = {'inscricao_estadual': 'Insc. Estadual', 'inscricao_municipal': 'Insc. Municipal', 'nire': 'NIRE',
                 'email': 'E-mail', 'telefone': 'Telefone', 'celular': 'Celular', 'observacoes': 'Observações',
                 'nire_data_registro': 'Data do registro', 'data_inicio_contrato': 'Início do contrato',
                 'regime_tributario': 'Regime', 'porte_empresa': 'Porte', 'cobranca': 'Cobrança',
                 'origem': 'Origem', 'ramos': 'Ramo de atividade', 'grupo': 'Grupo'}


@clientes.route('/clientes/<int:id>/campos', methods=['POST'])
@login_required
def salvar_campos(id):
    """Grava os campos que mudaram na tela única. Recebe JSON {"campos": {nome: valor}} e devolve
    {"ok", "salvos": [rótulos], "falta": <O que falta recalculado>} ou {"ok": false, "erro"}."""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        return jsonify(ok=False, erro='Cadastro não encontrado.'), 404
    campos = (request.get_json(silent=True) or {}).get('campos') or {}
    if not isinstance(campos, dict) or not campos:
        return jsonify(ok=False, erro='Nada para salvar.'), 400
    desconhecidos = [k for k in campos if k not in _ROTULO_CAMPO]
    if desconhecidos:
        return jsonify(ok=False, erro='Campo que esta tela não grava: ' + ', '.join(desconhecidos)), 400
    # Cobrança é do Financeiro (09/11/2026): a ficha esconde o bloco e o servidor recusa.
    if 'cobranca' in campos and not (current_user.is_admin() or current_user.has_permission('financeiro.honorarios')):
        return jsonify(ok=False, erro='A cobrança dos honorários é alterada pelo Financeiro.'), 403

    pj = cliente.get('tipo_pessoa') != 'PF'
    sets, antes, depois, erros = {}, {}, {}, []
    for k, v in campos.items():
        v = '' if v is None else str(v).strip() if not isinstance(v, list) else v
        if k in _CAMPOS_TEXTO:
            novo = v[:_CAMPOS_TEXTO[k]] or None
            if k in ('nire', 'inscricao_estadual', 'inscricao_municipal') and novo:
                novo = novo.upper()
            if k == 'email' and novo:
                novo = novo.lower()
                if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', novo):
                    erros.append('E-mail inválido.')
                    continue
            if k == 'celular' and not _telefone_valido(novo):
                erros.append('Celular: digite DDD + número (10 ou 11 dígitos). O celular é obrigatório.')
                continue
            if k == 'telefone' and novo and not _telefone_valido(novo):
                erros.append('Telefone inválido (DDD + número).')
                continue
            sets[k] = novo
        elif k in _CAMPOS_DATA:
            if v and not _data_iso(v, ate_hoje=False):
                erros.append(f'{_ROTULO_CAMPO[k]}: data inválida.')
                continue
            sets[k] = _data_iso(v, ate_hoje=False).isoformat() if v else None
        elif k in _CAMPOS_OPCAO:
            if v and v not in _CAMPOS_OPCAO[k]:
                erros.append(f'{_ROTULO_CAMPO[k]}: opção inválida.')
                continue
            if k == 'regime_tributario' and not pj:
                continue                      # pessoa física não tem regime (o editor grava SIMPLES fixo)
            sets[k] = v or ('PENDENTE' if k == 'cobranca' else None)
        elif k == 'origem' and not pj:
            continue
        elif k == 'grupo':
            g = GrupoCliente.get_by_id(int(v)) if str(v).isdigit() else None
            if not g or g.get('situacao') != 'ATIVO':
                erros.append('Grupo é obrigatório: escolha um grupo ativo.')
                continue
        elif k == 'ramos':
            ids = sorted({int(x) for x in (v if isinstance(v, list) else []) if str(x).isdigit()})
            if not ids:
                erros.append('Ramo de atividade é obrigatório: escolha ao menos um.')
                continue
            tipo = 'PF' if not pj else 'PJ'
            marcas = ','.join(['%s'] * len(ids))
            n = (execute_query(f"SELECT COUNT(*) n FROM ramos_atividade WHERE id IN ({marcas}) AND tipo_pessoa = %s",
                               (*ids, tipo), fetch=True, fetch_one=True) or {}).get('n') or 0
            if n != len(ids):
                erros.append('Ramo de atividade de ' + ('pessoa física.' if tipo == 'PF' else 'empresa.'))
    if erros:
        return jsonify(ok=False, erro=' '.join(erros)), 400

    salvos = []
    for k, novo in list(sets.items()):
        velho = cliente.get(k)
        velho_cmp = velho.isoformat() if hasattr(velho, 'isoformat') else (velho if velho not in ('',) else None)
        if (velho_cmp or None) == (novo or None):
            sets.pop(k)
            continue
        antes[k], depois[k] = velho_cmp, novo
        salvos.append(_ROTULO_CAMPO[k])
    if sets:
        execute_query(f"UPDATE clientes SET {', '.join(f'{k} = %s' for k in sets)}, alterado_por = %s, "
                      f"alterado_em = CURRENT_TIMESTAMP WHERE id = %s",
                      (*sets.values(), current_user.id, id))
        registrar('escrita.alterou_cliente', 'cadastros', tabela='clientes', registro_id=id,
                  antes=antes, depois=depois)
        if 'inscricao_estadual' in sets:
            IE.espelhar_do_cadastro(id)

    if 'origem' in campos and pj:
        v = str(campos.get('origem') or '').strip()
        from werkzeug.datastructures import MultiDict
        f = MultiDict({'aberta_pela_casa': '1'} if v == 'CASA' else {'contabilidade_anterior_id': v})
        if v != 'CASA' and not (v.isdigit() and Contabilidade.get(int(v))):
            return jsonify(ok=False, erro='Origem: escolha "Aberta pela Qualicontax" ou um escritório da lista.'), 400
        casa = 1 if v == 'CASA' else 0
        if casa != (1 if cliente.get('aberta_pela_casa') else 0):
            execute_query("UPDATE clientes SET aberta_pela_casa = %s, alterado_por = %s, alterado_em = CURRENT_TIMESTAMP "
                          "WHERE id = %s", (casa, current_user.id, id))
            registrar('escrita.alterou_cliente', 'cadastros', tabela='clientes', registro_id=id,
                      antes={'aberta_pela_casa': cliente.get('aberta_pela_casa')}, depois={'aberta_pela_casa': casa})
        antes_ca = cliente.get('contabilidade_anterior_id')
        _salvar_origem(id, f, antes_ca)
        if casa != (1 if cliente.get('aberta_pela_casa') else 0) or (None if casa else int(v)) != (antes_ca or None):
            salvos.append('Origem')

    if 'grupo' in campos:
        mud = _salvar_grupo(id, int(campos['grupo']))
        if mud:
            registrar('escrita.alterou_grupo_cliente', 'cadastros', tabela='clientes', registro_id=id,
                      antes={'grupos': mud[0]}, depois={'grupos': [mud[1]]})
            salvos.append('Grupo')

    if 'ramos' in campos:
        ids = sorted({int(x) for x in campos['ramos'] if str(x).isdigit()})
        atuais = RamoAtividade.get_by_cliente(id)
        if sorted(r['id'] for r in atuais) != ids:
            for r in atuais:
                if r['id'] not in ids:
                    RamoAtividade.remove_cliente(r['id'], id)
            for rid in ids:
                if rid not in {r['id'] for r in atuais}:
                    RamoAtividade.add_cliente(rid, id)
            registrar('escrita.alterou_ramos_cliente', 'cadastros', tabela='clientes', registro_id=id,
                      antes={'ramos': sorted(r['nome'] for r in atuais)},
                      depois={'ramos': sorted(r['nome'] for r in RamoAtividade.get_by_cliente(id))})
            salvos.append('Ramo de atividade')

    return jsonify(ok=True, salvos=salvos, falta=AC.pendencias_do_cadastro(id))


@clientes.route('/clientes/opcoes/<tipo>', methods=['POST'])
@admin_required
def incluir_opcao(tipo):
    """Inclui um item numa lista fechada do cadastro da pessoa (só admin).

    Antes de gravar confere se já existe o mesmo nome escrito de outro jeito
    ("empresaria" x "Empresário(a)"): aí devolve o que existe e não duplica."""
    from routes.qrobo import csrf_valido
    if tipo not in TIPOS_OPCAO:
        return jsonify({'ok': False, 'erro': 'Lista desconhecida.'}), 404
    if not csrf_valido():
        return jsonify({'ok': False, 'erro': 'A página expirou. Recarregue e tente de novo.'}), 400
    nome = ' '.join((request.get_json(silent=True) or {}).get('nome', '').split())[:80]
    if tipo == 'rg_orgao':
        nome = nome.upper()
    if len(nome) < 2:
        return jsonify({'ok': False, 'erro': 'Escreva o nome.'}), 400
    iguais = Opcoes.parecidas(tipo, nome)
    if iguais:
        return jsonify({'ok': True, 'nome': iguais[0], 'ja_existia': True})
    Opcoes.incluir(tipo, nome, current_user.id)
    registrar('escrita.incluiu_opcao_cadastro', 'cadastros', tabela='cadastro_opcoes',
              depois={'lista': TIPOS_OPCAO[tipo], 'nome': nome})
    return jsonify({'ok': True, 'nome': nome, 'ja_existia': False})


@clientes.route('/clientes/<int:id>/acesso-reservado', methods=['POST'])
@admin_required
def acesso_reservado(id):
    """Marca/desmarca a empresa como RESERVADA e grava quem é exceção.

    Reservada = só admin vê; a exceção libera uma pessoa para ESTA empresa
    (utils/acesso.py aplica a regra em todo o sistema)."""
    from routes.qrobo import csrf_valido
    from utils.acesso import salvar_reserva
    destino = url_for('clientes.detalhes', id=id) + '#acesso'
    if not csrf_valido():
        flash('Formulário expirado. Abra a aba Acesso de novo e salve.', 'danger')
        return redirect(destino)
    if not Cliente.get_by_id(id):
        flash('Cliente não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    reservado = request.form.get('reservado') == '1'
    antes, depois = salvar_reserva(id, reservado, request.form.getlist('excecao'))
    registrar('escrita.alterou_acesso_reservado', 'cadastros', tabela='clientes',
              registro_id=id, antes=antes, depois=depois)
    if reservado:
        n = len(depois['excecoes'])
        flash('Empresa RESERVADA: só administradores a veem'
              + (f', mais {n} pessoa(s) na exceção.' if n else '.'), 'success')
    else:
        flash('Empresa liberada: todos com o perfil certo voltam a vê-la.', 'success')
    return redirect(destino)


# ---------------------------------------------------------------------------
# Certificado Digital (.pfx) — busca na _ENTRADA e vínculo por empresa
# ---------------------------------------------------------------------------
def _tokens_certificado(cliente):
    """Tokens que identificam a empresa no nome do .pfx: número e CNPJ (14 díg.)."""
    tokens = []
    numero = (cliente.get('numero_cliente') or '').strip()
    cnpj = re.sub(r'\D', '', cliente.get('cpf_cnpj') or '')
    if numero:
        tokens.append(numero)
    if cnpj:
        tokens.append(cnpj)
    return tokens


# Separadores aceitos entre o NÚMERO da empresa e o resto do nome do arquivo.
_CERT_SEPARADORES = ('-', '_', ' ', '.')


#: Extensões de certificado A1 aceitas na _ENTRADA. .p12 e .pfx são o MESMO
#: contêiner (PKCS#12) — só o nome muda conforme quem exportou. Até 13/09/2026
#: só .pfx entrava, e um .p12 ficava na _ENTRADA "não encontrado".
_EXT_CERT = ('.pfx', '.p12')


def _e_certificado(nome):
    return (nome or '').lower().endswith(_EXT_CERT)


def _nome_base_pfx(nome):
    """Nome do arquivo sem a extensão .pfx/.p12 final (case-insensitive)."""
    return nome[:-4] if _e_certificado(nome) else nome


# "senha" no nome do arquivo: o que vem DEPOIS da palavra é a senha, até o
# próximo " - " (é assim que os arquivos chegam: "175 - POSTO X - Senha Ce123 -
# Venc 31.08.27.p12") ou até o fim do nome.
#   "175 senha 1234.pfx"  -> 1234      "175-senha-Ab@12.p12" -> Ab@12
#   "175 - POSTO X - Senha Ce123 - Venc 31.08.27.p12" -> Ce123
# A palavra precisa estar isolada — separador antes E depois — para não
# confundir com nome que contenha "senha" por acaso ("Senhas 175.pfx" não
# casa; "senha1234" colado também não: é "senha 1234", "senha-1234", "senha=1234").
_RE_SENHA_NO_NOME = re.compile(r'(?:^|[\s_\-.])senha[\s:=_\-]+(.+?)(?:\s+-\s+.*)?$', re.IGNORECASE)
_RE_SENHA_RABO = re.compile(
    r'\s+(?:(?:venc|valid|valiad|validade|vencimento|val)\b.*|\(?\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b.*)$',
    re.IGNORECASE)


def _senha_no_nome(nome):
    """Senha embutida no nome do arquivo (regra "senha <senha>"), ou None.

    É a convenção que deixa o roteador vincular o certificado SOZINHO
    (utils/certificado_auto.py): o usuário nomeia "175 senha 1234.pfx", o cron
    tenta a senha; se estiver errada, o arquivo fica na _ENTRADA e a tela pede
    a senha como sempre. Depois do vínculo o arquivo é renomeado para
    {CNPJ}.pfx — a senha NÃO sobrevive no nome.
    """
    m = _RE_SENHA_NO_NOME.search(_nome_base_pfx(nome or ''))
    if not m:
        return None
    # A validade às vezes vem colada, sem " - ": "SENHA abc123 18.06.2027" ou
    # "senha abc123 Venc 18.06.27". Depois de um espaço, data ou "venc/valid…"
    # já não é senha (08/10/2026, certificado da 5008 não vinculava).
    senha = _RE_SENHA_RABO.sub('', m.group(1)).strip()
    return senha or None


def _nome_casa_numero(nome, numero):
    """REGRA 1 — o nome COMEÇA com o número da empresa: o número EXATO, ou o
    número seguido de um separador (``-``, ``_``, espaço ou ``.``).

    O separador obrigatório evita casamento de prefixo ambíguo: a empresa 51 NÃO
    casa com "515-....pfx" (depois de "51" vem "5", não um separador). É prefixo
    de propósito — número no meio do nome não vale, senão "Venc 04.09.26" casaria
    com a empresa 4, a 9 e a 26.
    """
    if not numero:
        return False
    base_l = _nome_base_pfx(nome).lower()
    tok_l = str(numero).lower()
    if base_l == tok_l:
        return True
    return (base_l.startswith(tok_l)
            and len(base_l) > len(tok_l)
            and base_l[len(tok_l)] in _CERT_SEPARADORES)


def _nome_casa_documento(nome, doc):
    """REGRA 2 — o nome CONTÉM o documento da empresa em QUALQUER posição.

    Só dígitos crus (CNPJ com 14, CPF com 11), com FRONTEIRA DE DÍGITOS: o
    documento não pode ser pedaço de uma sequência numérica maior. Sem essa
    fronteira, um nome com a chave de 44 dígitos de uma NF-e casaria com
    qualquer empresa cujo CNPJ aparecesse por acaso lá dentro.

    Diferente da regra do número, aqui é "contém" e não "prefixo": o CNPJ é
    identificador único, então não há o risco de ambiguidade que o prefixo do
    número tem. Isso faz "BRR BLINDAGENS 28472667000106.pfx" ser encontrado.
    """
    if not doc or len(doc) not in (11, 14):
        return False
    return re.search(rf'(?<!\d){re.escape(doc)}(?!\d)',
                     _nome_base_pfx(nome)) is not None


def _nome_casa_empresa(nome, cliente):
    """Regra de nome do .pfx: REGRA 1 (número como prefixo) OU REGRA 2 (documento
    contido). Fora disso, o arquivo NÃO é lido."""
    numero = (cliente.get('numero_cliente') or '').strip()
    doc = re.sub(r'\D', '', cliente.get('cpf_cnpj') or '')
    return _nome_casa_numero(nome, numero) or _nome_casa_documento(nome, doc)


def _localizar_certificados_novo(svc, cliente):
    """Lista os .pfx da _ENTRADA que casam com a empresa: NÚMERO como prefixo
    (+ separador) OU DOCUMENTO contido em qualquer posição — ver
    ``_nome_casa_empresa``. Extensões .pfx e .p12 aceitas (case-insensitive).

    Retorna a LISTA de itens casados (0, 1 ou vários). A busca é sempre refeita
    no servidor (não confia em caminho vindo do cliente). O chamador decide: 0 =
    não encontrado; 1 = usa; vários = recusa (não escolhe o certificado sozinho).
    """
    if not _tokens_certificado(cliente):
        return []
    achados = []
    for item in svc.list_folder(svc.pasta_cert_novo()):
        nome = item.get('name') or ''
        if not item.get('is_file') or not _e_certificado(nome):
            continue
        if _nome_casa_empresa(nome, cliente):
            achados.append(item)
    return achados


def _erro_ambiguidade_certificado(achados):
    """Mensagem quando mais de um .pfx casa com a empresa — lista quais, não escolhe."""
    nomes = ', '.join(sorted(i.get('name', '?') for i in achados))
    return ('Mais de um certificado enviado casa com esta empresa: '
            f'{nomes}. Deixe só o arquivo correto na Caixa de entrada e tente de novo.')


@clientes.route('/clientes/<int:id>/certificado/buscar', methods=['POST'])
@login_required
def certificado_buscar(id):
    """Procura sozinho o .pfx da empresa na _ENTRADA (sem seleção manual)."""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        return jsonify({'ok': False, 'erro': 'Cliente não encontrado.'}), 404

    svc = dropbox_sync._service
    if not svc.is_configured():
        return jsonify({'ok': False, 'erro': 'Dropbox não configurado. '
                        'Defina DROPBOX_APP_KEY e DROPBOX_REFRESH_TOKEN.'}), 400

    if not _tokens_certificado(cliente):
        return jsonify({'ok': False, 'erro': 'Empresa sem número nem CNPJ para localizar o certificado.'}), 400

    try:
        achados = _localizar_certificados_novo(svc, cliente)
    except dropbox_sync.DropboxAuthError:
        return jsonify({'ok': False, 'erro': 'Credenciais Dropbox inválidas ou expiradas.'}), 401
    except dropbox_sync.DropboxError:
        return jsonify({'ok': False, 'erro': 'Erro ao acessar o Dropbox. Verifique a conexão.'}), 502

    if len(achados) > 1:
        return jsonify({'ok': False, 'erro': _erro_ambiguidade_certificado(achados)}), 200
    if not achados:
        return jsonify({'ok': True, 'encontrado': False,
                        'erro': 'Certificado não encontrado entre os arquivos enviados.'}), 200

    return jsonify({'ok': True, 'encontrado': True, 'arquivo': achados[0]['name']}), 200


def _fmt_cnpj(doc):
    """Formata 14 dígitos como CNPJ (XX.XXX.XXX/XXXX-XX); devolve cru se não for 14."""
    d = re.sub(r'\D', '', str(doc or ''))
    if len(d) != 14:
        return d
    return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'


def _matriz_ou_filial(doc):
    """'matriz' se a ordem do estabelecimento (dígitos 9–12) for 0001, senão 'filial'."""
    d = re.sub(r'\D', '', str(doc or ''))
    return 'matriz' if len(d) == 14 and d[8:12] == '0001' else 'filial'


def _resp(payload, http=200):
    """(ok, payload, http) — o núcleo do vínculo devolve isto; a rota faz jsonify."""
    return bool(payload.get('ok')), payload, http


def vincular_certificado_arquivo(cliente, senha, procuracao_pedida=False):
    """NÚCLEO do vínculo: reencontra o arquivo na _ENTRADA, valida a senha,
    confere o titular, preserva o anterior, move para CERTIFICADO e grava.

    Devolve ``(ok, payload, http)``. Não toca em ``request`` nem em ``jsonify``
    de propósito: é chamado pela rota ``certificado_vincular`` (tela) E pelo
    instalador automático do roteador (``utils/certificado_auto.py``), que
    roda fora do site. Toda a lógica que existia na rota está aqui, intacta.
    """
    id = cliente['id']
    svc = dropbox_sync._service
    if not svc.is_configured():
        return _resp({'ok': False, 'erro': 'Dropbox não configurado.'}, 400)

    # 1) Reencontra o arquivo na _ENTRADA (não confia em path do cliente).
    try:
        achados = _localizar_certificados_novo(svc, cliente)
    except dropbox_sync.DropboxAuthError:
        return _resp({'ok': False, 'erro': 'Credenciais Dropbox inválidas ou expiradas.'}, 401)
    except dropbox_sync.DropboxError:
        return _resp({'ok': False, 'erro': 'Erro ao acessar o Dropbox. Verifique a conexão.'}, 502)
    if len(achados) > 1:
        return _resp({'ok': False, 'erro': _erro_ambiguidade_certificado(achados)}, 200)
    if not achados:
        return _resp({'ok': False, 'erro': 'Certificado não encontrado entre os arquivos enviados.'}, 200)
    item = achados[0]

    # 2) Baixa o .pfx em memória.
    raw = svc.download_file(item['path'])
    if raw is None:
        return _resp({'ok': False, 'erro': 'Falha ao baixar o certificado do Dropbox.'}, 502)

    # 3) Valida a senha e extrai o documento do titular.
    try:
        info = abrir_certificado(raw, senha)
    except SenhaInvalidaError:
        return _resp({'ok': False, 'erro': 'Senha do certificado incorreta.'}, 200)
    except DocumentoNaoEncontradoError:
        return _resp({'ok': False, 'erro': 'Não foi possível identificar o CNPJ/CPF no certificado.'}, 200)
    except CertificadoError as exc:
        return _resp({'ok': False, 'erro': f'Certificado inválido: {exc}'}, 200)

    # 3b) BLOQUEIO: certificado VENCIDO não vincula — vincular só criaria um vínculo
    #     que já nasce quebrado (a SEFAZ recusa o mTLS com HTTP 403). Só bloqueia o
    #     vencido; um cert válido, mesmo vencendo em breve (laranja), vincula normal.
    if info['validade'] and DfeCertificado.classificar_validade(
            info['validade'])['nivel'] == 'vencido':
        return _resp({'ok': False,
                        'erro': f'Certificado vencido em {info["validade"].strftime("%d/%m/%Y")} '
                                '— não é possível vincular um certificado vencido. '
                                'Renove e tente de novo.'}, 200)

    # 4) Confere se o certificado é mesmo dessa empresa.
    #    O e-CNPJ é emitido para a RAIZ (8 primeiros dígitos) e cobre as filiais.
    #    Match exato → vincula normal. Match SÓ na raiz (matriz cobrindo filial) →
    #    vincula, mas com AVISO explícito (flexibilização consciente da regra:
    #    quero ver na tela p/ não vincular na empresa errada). Raiz diferente →
    #    recusa — SALVO se o vínculo foi pedido como PROCURAÇÃO. CPF (11 díg.)
    #    exige igualdade exata (sem lógica de raiz).
    #
    #    ORDEM DOS RAMOS (importa):
    #      a) doc_cert == doc_empresa  -> caminho NORMAL, intocado. Cobre PJ+e-CNPJ
    #         (14 díg.) e PF+e-CPF (11 díg.) igualmente: a comparação é entre
    #         strings de dígitos, sem olhar tipo_doc nem comprimento.
    #      b) procuração                -> AGNÓSTICA DE TIPO. Só "titular ≠ empresa
    #         E procuracao=1". Vale e-CPF (contador) e e-CNPJ (escritório).
    #      c) raiz                      -> CNPJ-only, como sempre foi.
    doc_cert = info['documento']
    doc_empresa = re.sub(r'\D', '', cliente.get('cpf_cnpj') or '')
    aviso = None
    procuracao = 0
    if doc_cert != doc_empresa:
        if procuracao_pedida:
            # PROCURAÇÃO: titular de terceiro é o caso de uso, não o erro.
            # SEM checagem de tipo_doc/comprimento de propósito — e-CPF (11) e
            # e-CNPJ (14) são igualmente válidos como certificado de terceiro.
            #
            # Guarda: a empresa PRECISA ter CPF/CNPJ próprio. Sem ele,
            # dfe_captura.py (`cliente.cpf_cnpj or vinc.cnpj`) cairia no FALLBACK
            # e consultaria a SEFAZ com o documento do CONTADOR — trazendo os
            # documentos dele para dentro desta empresa.
            if not doc_empresa:
                return _resp({'ok': False,
                                'erro': 'Esta empresa está sem CPF/CNPJ cadastrado. '
                                        'Cadastre o documento antes de vincular um '
                                        'certificado de procuração — sem ele a captura '
                                        'consultaria a SEFAZ com o documento do titular '
                                        'do certificado, e não com o desta empresa.'}, 200)
            procuracao = 1
            aviso = (f'Certificado de TERCEIRO (procuração) — titular {doc_cert} '
                     f'≠ empresa {doc_empresa}. Confira.')
        else:
            raiz_ok = (info['tipo_doc'] == 'CNPJ'
                       and len(doc_cert) == 14 and len(doc_empresa) == 14
                       and doc_cert[:8] == doc_empresa[:8])
            if not raiz_ok:
                return _resp({'ok': False,
                                'erro': 'O certificado da pasta não é dessa empresa '
                                        f'(certificado: {doc_cert} · empresa: {doc_empresa or "sem CNPJ"}).'}, 200)
            aviso = (f'Certificado da {_matriz_ou_filial(doc_cert)} {_fmt_cnpj(doc_cert)} '
                     f'vinculado à {_matriz_ou_filial(doc_empresa)} {_fmt_cnpj(doc_empresa)} '
                     f'(mesma raiz — o e-CNPJ da matriz cobre as filiais).')

    # 5) Cifra a senha ANTES de mover (se falhar aqui, o .pfx continua em NOVO).
    try:
        senha_cifrada = cifrar_senha(senha)
    except CertificadoError as exc:
        return _resp({'ok': False, 'erro': str(exc)}, 500)

    # 6) Move + renomeia para CERTIFICADO/{numero} - {razão}/{CNPJ}.pfx
    origem = item['path']
    numero = (cliente.get('numero_cliente') or '').strip() or None
    razao = cliente.get('nome_razao_social') or 'SEM_NOME'
    pasta_dest = svc.pasta_cert_importados(razao, numero)
    destino = f'{pasta_dest}/{doc_cert}.pfx'
    svc.ensure_folder(pasta_dest)

    # 6a) RENOVAÇÃO: preserva o .pfx anterior desta empresa ANTES de mover o novo —
    #     NUNCA apaga, NUNCA sobrescreve (move_file sobrescreve por padrão, então
    #     uma renovação de mesmo CNPJ perderia o antigo). O arquivo antigo é
    #     renomeado para ``substituido{CNPJ}.venc.{dd.mm.aaaa}.pfx``, com o
    #     VENCIMENTO do cert antigo. A validade vem do banco (foi lida do próprio
    #     .pfx quando ele foi vinculado; reabri-lo aqui exigiria a senha dele, que
    #     não temos). Numa 2ª renovação, sufixa .2, .3… para não colidir.
    #     Lê ``anterior`` ANTES do move (o upsert do passo 7 substitui o registro).
    anterior = DfeCertificado.get_by_cliente(id)

    def _pasta_de(p):
        return p.rsplit('/', 1)[0] if '/' in p else ''

    a_preservar = []
    if anterior:
        _ap = (anterior.get('dropbox_path') or '').strip()
        if _ap and _pasta_de(_ap).lower() == pasta_dest.lower():
            a_preservar.append((_ap, anterior.get('cnpj') or doc_cert, anterior.get('validade')))
    # arquivo já no destino (mesmo nome do novo) que o move sobrescreveria
    if svc._path_exists(destino) and all(destino.lower() != p.lower() for p, _, _ in a_preservar):
        a_preservar.append((destino, doc_cert, anterior.get('validade') if anterior else None))

    renamed = []
    for _old, _cnpj_old, _val in a_preservar:
        if not svc._path_exists(_old):
            continue
        _data = _val.strftime('%d.%m.%Y') if _val else 'sem_venc'
        _base = f'{pasta_dest}/substituido{_cnpj_old}.venc.{_data}'
        _novo = f'{_base}.pfx'
        _k = 2
        while svc._path_exists(_novo):
            _novo = f'{_base}.{_k}.pfx'
            _k += 1
        try:
            if not svc.move_file(_old, _novo):
                return _resp({'ok': False, 'erro': 'Falha ao arquivar o certificado anterior no Dropbox.'}, 502)
        except dropbox_sync.DropboxAuthError:
            return _resp({'ok': False, 'erro': 'Credenciais Dropbox inválidas ou expiradas.'}, 401)
        renamed.append((_old, _novo))

    # 6b) Move o novo .pfx com o nome padrão {CNPJ}.pfx (NUNCA a senha no nome).
    #     O destino agora está livre (o anterior, se havia, virou substituido...).
    try:
        if not svc.move_file(origem, destino):
            return _resp({'ok': False, 'erro': 'Falha ao mover o certificado no Dropbox.'}, 502)
    except dropbox_sync.DropboxAuthError:
        return _resp({'ok': False, 'erro': 'Credenciais Dropbox inválidas ou expiradas.'}, 401)

    # 6c) Mensagem p/ a tela: qual cert saiu de cena e como foi preservado.
    substituicao = None
    if anterior and renamed:
        _val = anterior.get('validade')
        _nome_arq = renamed[0][1].rsplit('/', 1)[-1]
        substituicao = (
            f'Substituiu o certificado anterior desta empresa '
            f'({anterior.get("tipo_doc")} {anterior.get("cnpj")}'
            + (f', válido até {_val.strftime("%d/%m/%Y")}' if _val else '')
            + f'). O arquivo antigo foi PRESERVADO no Dropbox como {_nome_arq} '
              f'(nunca apagado, nunca sobrescrito).')

    # 7) Grava o vínculo. Se falhar, tenta devolver o .pfx para NOVO (sem órfão);
    #    se nem a reversão der certo, loga o caminho para recuperação manual.
    ok = DfeCertificado.upsert(
        cliente_id=id, cnpj=doc_cert, tipo_doc=info['tipo_doc'],
        senha_cifrada=senha_cifrada, dropbox_path=destino, validade=info['validade'],
        procuracao=procuracao,
    )
    if not ok:
        revertido = False
        try:
            revertido = svc.move_file(destino, origem)
        except Exception as exc:
            logger.error('Falha ao reverter move do certificado %s -> %s: %s',
                         destino, origem, exc)
        # Desfaz a preservação do cert anterior (substituido... -> nome original):
        # o dropbox_path do banco (que não mudou, pois o upsert falhou) aponta para
        # o nome antigo, então ele precisa voltar a existir.
        for _orig, _novo in reversed(renamed):
            try:
                svc.move_file(_novo, _orig)
            except Exception:
                logger.error('Falha ao desfazer preservação do cert anterior %s -> %s',
                             _novo, _orig)
        if revertido:
            logger.error('Vínculo do certificado NÃO gravado (cliente_id=%s). '
                         'Arquivo devolvido para NOVO: %s', id, origem)
            return _resp({'ok': False, 'erro': 'Falha ao gravar o vínculo no banco. '
                            'O certificado foi devolvido para a pasta _ENTRADA — tente novamente.'}, 500)
        logger.error('Vínculo do certificado NÃO gravado (cliente_id=%s) e reversão FALHOU. '
                     'ARQUIVO ÓRFÃO em: %s', id, destino)
        return _resp({'ok': False, 'erro': 'Falha ao gravar o vínculo no banco. '
                        f'Atenção: o arquivo ficou em {destino} — avise o suporte.'}, 500)

    # AUDITORIA (D2): vinculou/trocou certificado. senha_cifrada e dropbox_path
    # estão na lista negra — entram só pelo NOME, nunca o valor (nada de dentro
    # do .pfx). Registra a troca (antes = cert anterior, se houver).
    _antes_cert = None
    if anterior:
        _antes_cert = {'cnpj': anterior.get('cnpj'), 'validade': anterior.get('validade'),
                       'senha_cifrada': '(anterior)', 'dropbox_path': anterior.get('dropbox_path')}
    # TRILHA COM AUTOR (05/10/2026): o registrar() acima só grava com usuário
    # logado; o vínculo automático do roteador não tem. Esta trilha acha o autor
    # também pelo Q-Colabore (utils/certificado_autoria.py) e alimenta o e-mail
    # "Certificados recebidos hoje". Nunca estoura.
    certificado_autoria.registrar_vinculo(
        id, doc_cert, info['validade'],
        anterior.get('validade') if anterior else None, raw)

    registrar('escrita.vinculou_certificado', 'cadastros', tabela='dfe_certificados',
              registro_id=id, antes=_antes_cert,
              depois={'cnpj': doc_cert, 'tipo_doc': info['tipo_doc'],
                      'validade': info['validade'], 'procuracao': procuracao,
                      'senha_cifrada': '(nova)', 'dropbox_path': destino})

    return _resp({
        'ok': True,
        'documento': doc_cert,
        'tipo_doc': info['tipo_doc'],
        'procuracao': procuracao,
        'validade': info['validade'].isoformat() if info['validade'] else None,
        'arquivo': f'{doc_cert}.pfx',
        'aviso': aviso,                 # vínculo por raiz (matriz→filial) OU procuração
        'substituicao': substituicao,   # preenchido só quando trocou um cert anterior
    }, 200)


@clientes.route('/clientes/<int:id>/certificado/vincular', methods=['POST'])
@login_required
def certificado_vincular(id):
    """Valida a senha, confere o titular, move p/ IMPORTADOS e grava o vínculo."""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        return jsonify({'ok': False, 'erro': 'Cliente não encontrado.'}), 404

    data = request.get_json(silent=True) or request.form
    senha = (data.get('senha') or '').strip()
    if not senha:
        return jsonify({'ok': False, 'erro': 'Informe a senha do certificado.'}), 400

    # Fase 2: vínculo por PROCURAÇÃO (e-CPF do contador / terceiro autorizado).
    # Só surte efeito quando o titular do .pfx NÃO for a empresa (passo 4); no
    # caminho normal (titular == empresa) o checkbox é ignorado de propósito.
    procuracao_pedida = str(data.get('procuracao') or '').strip().lower() in (
        '1', 'true', 'on', 'sim')
    ok, payload, http = vincular_certificado_arquivo(cliente, senha, procuracao_pedida)
    return jsonify(payload), http


@clientes.route('/clientes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar(id):
    """Editar cliente"""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        flash('Cliente não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    enderecos_cliente = EnderecoCliente.get_by_cliente(id)
    endereco_principal = next((e for e in enderecos_cliente if e.get('principal')), enderecos_cliente[0] if enderecos_cliente else None)
    
    if request.method == 'POST':
        try:
            # The form has duplicate-named fields (PF + PJ sections both submit
            # 'cpf_cnpj' and 'nome_razao_social'). The hidden section sends an empty
            # value that appears first in getlist(); pick the first non-empty one.
            cpf_cnpj = next((v for v in request.form.getlist('cpf_cnpj') if v.strip()), '')
            nome_razao_social = next((v for v in request.form.getlist('nome_razao_social') if v.strip()), '')

            # Validação de campos obrigatórios
            if not request.form.get('tipo_pessoa') or not nome_razao_social or not cpf_cnpj:
                flash('Preencha todos os campos obrigatórios.', 'danger')
                return _render_editar(cliente, endereco_principal)
            
            # Validar número do cliente se fornecido
            numero_cliente = _numero_inteiro(request.form.get('numero_cliente', ''))
            # NÚMERO OU AVULSO: só o avulso fica sem número (05/10/2026).
            _erro_num = None
            if not numero_cliente and not cliente.get('avulso'):
                _erro_num = 'Digite o número do cliente.'
            elif numero_cliente and Cliente.existe_numero_cliente(numero_cliente, id):
                _erro_num = f'Número do cliente "{numero_cliente}" já está em uso por outro cliente!'
            if not _erro_num:
                # Avulso que recebe número aqui está VIRANDO cliente: valem as
                # exigências de cliente (origem da empresa), como no botão
                # "Converter em cliente" (03/11/2026).
                _ainda_avulso = bool(cliente.get('avulso')) and not numero_cliente
                # 09/11/2026: grava incompleto (vai para o A CADASTRAR); só valor errado impede.
                _falta = _travas(request.form, _ainda_avulso, cliente_id=id)
                if _falta:
                    _erro_num = _msg_travas(_falta)
            if _erro_num:
                flash(_erro_num, 'danger')
                return _render_editar(cliente, endereco_principal)
            
            data = {
                'numero_cliente': numero_cliente if numero_cliente else None,
                'tipo_pessoa': request.form.get('tipo_pessoa'),
                'nome_razao_social': nome_razao_social,
                'cpf_cnpj': cpf_cnpj,
                'inscricao_estadual': request.form.get('inscricao_estadual'),
                'inscricao_municipal': request.form.get('inscricao_municipal'),
                'email': request.form.get('email'),
                'telefone': request.form.get('telefone'),
                'celular': request.form.get('celular'),
                'regime_tributario': request.form.get('regime_tributario'),
                'porte_empresa': request.form.get('porte_empresa'),
                'cnae_fiscal': request.form.get('cnae_fiscal'),
                'cnae_fiscal_descricao': request.form.get('cnae_fiscal_descricao'),
                'situacao': request.form.get('situacao'),
                'data_inicio_atividade': request.form.get('data_inicio_atividade'),
                'data_inicio_contrato': request.form.get('data_inicio_contrato'),
                'observacoes': request.form.get('observacoes'),
                'nire': request.form.get('nire'),
                'nire_data_registro': request.form.get('nire_data_registro'),
                'cobranca': request.form.get('cobranca') if _pode_fin() else cliente.get('cobranca'),
                'aberta_pela_casa': 1 if request.form.get('aberta_pela_casa') else 0,
                'alterado_por': current_user.id      # AUDITORIA (D2): quem alterou
            }
            # E1: 'None'/'null'/'' de qualquer caminho viram VAZIO antes de gravar.
            data = limpar_form(data)

            # A pasta da empresa no Dropbox chama "{numero} - {razão}", e os XMLs
            # já arquivados guardam esse caminho no banco. Número ou razão novos
            # exigem renomear a pasta JUNTO — e ANTES de gravar: com o Dropbox
            # fora do ar a edição é recusada, em vez de deixar cadastro e pasta
            # desencontrados (histórico órfão na pasta velha).
            pasta_antiga = dropbox_sync._build_empresa_folder(
                cliente.get('numero_cliente'), cliente.get('nome_razao_social') or '')
            pasta_nova = dropbox_sync._build_empresa_folder(
                data.get('numero_cliente'), data.get('nome_razao_social') or '')
            _pasta_st, _pasta_de, _pasta_para = '', None, None
            if pasta_nova != pasta_antiga:
                _pasta_st, _pasta_de, _pasta_para = \
                    dropbox_sync.renomear_pasta_empresa(pasta_antiga, pasta_nova)
                if _pasta_st in ('conflito', 'erro'):
                    if _pasta_st == 'conflito':
                        flash(f'Já existe uma pasta "{pasta_nova}" no Dropbox. '
                              'Junte ou renomeie a pasta lá e tente de novo — '
                              'nada foi alterado.', 'danger')
                    else:
                        flash('Não consegui renomear a pasta da empresa no Dropbox '
                              'agora. Nada foi alterado — tente novamente em '
                              'instantes.', 'danger')
                    return _render_editar(cliente, endereco_principal)

            sucesso = Cliente.update(id, data)

            # Check if sucesso is not None (None indicates error, 0 or positive number indicates success)
            if sucesso is not None:
                _gravar_contatos_do_formulario(id)
                _sincronizar_contato_pf(id)
                # AUDITORIA (D2): grava só os campos de negócio que mudaram
                # (o helper calcula o diff; alterado_por/criado_por ficam fora).
                _campos_log = ('numero_cliente', 'tipo_pessoa', 'nome_razao_social',
                               'cpf_cnpj', 'inscricao_estadual', 'inscricao_municipal',
                               'email', 'telefone', 'celular', 'regime_tributario',
                               'porte_empresa', 'cnae_fiscal', 'cnae_fiscal_descricao',
                               'situacao', 'observacoes', 'aberta_pela_casa', 'nire', 'nire_data_registro', 'cobranca')
                registrar('escrita.alterou_cliente', 'cadastros', tabela='clientes',
                          registro_id=id,
                          antes={k: cliente.get(k) for k in _campos_log},
                          depois={k: data.get(k) for k in _campos_log})
                _vigia_concluir_se_aplicada(cpf_cnpj, id)
                if (cliente.get('inscricao_estadual') or '') != (data.get('inscricao_estadual') or ''):
                    IE.espelhar_do_cadastro(id)

                # AVULSO QUE GANHOU NÚMERO AQUI vira cliente de verdade (03/11/2026).
                # Antes a edição gravava o número e deixava a marca de avulso
                # ligada: o cadastro sumia da carteira (que só lista avulso = 0),
                # não aparecia na busca, mas "já está em uso" barrava o número —
                # três cadastros do Jabes (591, 592, 596) ficaram assim hoje.
                # É o mesmo que o botão "Converter em cliente" dos Avulsos faz;
                # a pasta já foi renomeada logo acima.
                _virou_cliente = False
                if cliente.get('avulso') and data.get('numero_cliente'):
                    execute_query('UPDATE clientes SET avulso = 0, virou_cliente_em = NOW() '
                                  'WHERE id = %s AND avulso = 1', (id,))
                    _virou_cliente = True
                    registrar('escrita.converteu_avulso', 'cadastros', tabela='clientes',
                              registro_id=id,
                              antes={'avulso': True, 'pasta': pasta_antiga},
                              depois={'numero_cliente': data.get('numero_cliente'),
                                      'pasta': pasta_nova, 'dropbox': _pasta_st or 'sem_mudanca',
                                      'nome': data.get('nome_razao_social'), 'por': 'edicao'})

                # A pasta mudou de nome: reaponta os caminhos gravados. O LIKE
                # escapa % e _ (razões sanitizadas podem conter '_'), e o
                # SUBSTRING preserva tudo depois do prefixo antigo.
                if _pasta_de:
                    _like = (_pasta_de.replace('\\', '\\\\')
                             .replace('%', '\\%').replace('_', '\\_')) + '/%'
                    _corte = len(_pasta_de) + 1
                    _tot = 0
                    for _t, _c in (('nfse_capturadas', 'xml_path'),
                                   ('cte_documentos', 'xml_caminho'),
                                   ('dfe_documentos', 'xml_caminho'),
                                   ('dfe_eventos', 'xml_caminho'),
                                   ('nfe_importacoes', 'xml_caminho'),
                                   ('dfe_certificados', 'dropbox_path'),
                                   ('documentos', 'caminho_arquivo')):
                        _n = execute_query(
                            f'UPDATE {_t} SET {_c} = CONCAT(%s, SUBSTRING({_c}, %s)) '
                            f'WHERE {_c} LIKE %s', (_pasta_para, _corte, _like))
                        _tot += _n or 0
                    registrar('escrita.renomeou_pasta_empresa', 'cadastros',
                              tabela='clientes', registro_id=id,
                              antes={'pasta': pasta_antiga},
                              depois={'pasta': pasta_nova, 'dropbox': _pasta_st,
                                      'caminhos_atualizados': _tot})

                # Marca de CONTADOR (fora do UPDATE, que tem lista de colunas
                # explícita). Desmarcar pode ser RECUSADO se o cadastro ainda
                # for contador de alguém.
                _contador = _eh_contador(request.form, id)
                if current_user.is_admin():
                    r_cont = ClienteContador.marcar_como_contador(id, 1 if _contador else 0)
                    if not r_cont.get('ok') and r_cont.get('erro'):
                        flash(r_cont['erro'], 'warning')

                # Gerenciar ramos de atividade (múltiplos)
                ramos_ids_novos = request.form.getlist('ramos_atividade_ids')
                cliente_ramos_atuais = RamoAtividade.get_by_cliente(id)
                
                # Remover todos os ramos atuais
                for ramo_atual in cliente_ramos_atuais:
                    RamoAtividade.remove_cliente(ramo_atual['id'], id)
                
                # Adicionar novos ramos selecionados
                for ramo_id in ramos_ids_novos:
                    try:
                        RamoAtividade.add_cliente(int(ramo_id), id)
                    except:
                        pass  # Ignora erros de duplicação
                _ramos_antes = sorted(r['nome'] for r in cliente_ramos_atuais)
                _ramos_depois = sorted(r['nome'] for r in RamoAtividade.get_by_cliente(id))
                if _ramos_antes != _ramos_depois:
                    registrar('escrita.alterou_ramos_cliente', 'cadastros', tabela='clientes',
                              registro_id=id, antes={'ramos': _ramos_antes}, depois={'ramos': _ramos_depois})

                # Grupo de empresas (obrigatório desde 07/10/2026)
                _mud_grupo = (_salvar_grupo(id, request.form.get('grupo_id', type=int))
                              if request.form.get('grupo_id', type=int) else None)   # em branco não tira do grupo
                if _mud_grupo:
                    registrar('escrita.alterou_grupo_cliente', 'cadastros', tabela='clientes',
                              registro_id=id, antes={'grupos': _mud_grupo[0]},
                              depois={'grupos': [_mud_grupo[1]]})
                if not _contador:
                    _salvar_contador(id, request.form.get('contador_id', type=int))
                if request.form.get('tipo_pessoa') == 'PJ':
                    # lista vazia não vira "todos saíram" (desde 09/11 grava sem sócios): só a ficha tira sócio
                    if _ler_socios(request.form) or _filial_travada(cpf_cnpj, id)[0]:
                        _gravar_quadro(id, request.form)
                    _salvar_origem(id, request.form, cliente.get('contabilidade_anterior_id'))
                    _salvar_natureza(id, request.form, cliente)
                else:
                    _salvar_pessoa(id, request.form)
                    _rur = IE.ler_rurais_json(request.form.get('ies_rurais_json'))
                    if _rur is not None:
                        _antes_r = [x for x in IE.listar(id) if x['tipo'] == 'RURAL']
                        IE.salvar_rurais(id, _rur)
                        _depois_r = [x for x in IE.listar(id) if x['tipo'] == 'RURAL']
                        if [_ie_resumo(x) for x in _antes_r] != [_ie_resumo(x) for x in _depois_r]:
                            registrar('escrita.alterou_ie', 'cadastros', tabela='clientes', registro_id=id,
                                      antes={'cliente_id': id, 'inscricoes': [_ie_resumo(x) for x in _antes_r]},
                                      depois={'cliente_id': id, 'inscricoes': [_ie_resumo(x) for x in _depois_r]})

                # Atualizar/salvar endereço principal
                cep = request.form.get('cep', '').strip()
                logradouro = limpar_logradouro(request.form.get('logradouro', ''))   # "RUA RUA X" -> "RUA X"
                numero = request.form.get('numero', '').strip()
                complemento = request.form.get('complemento', '').strip()
                bairro = request.form.get('bairro', '').strip()
                cidade = request.form.get('cidade', '').strip()
                estado = request.form.get('estado', '').strip()
                possui_dados_endereco = any([cep, logradouro, numero, complemento, bairro, cidade, estado])

                if possui_dados_endereco:
                    _end_novo = {'cep': cep, 'logradouro': logradouro, 'numero': numero,
                                 'complemento': complemento, 'bairro': bairro, 'cidade': cidade,
                                 'estado': estado}
                    _end_antes = {k: ((endereco_principal or {}).get(k) or '').strip() for k in _end_novo}
                    _end_mud = [k for k in _end_novo if _end_novo[k] != _end_antes[k]]
                    if _end_mud:
                        registrar('escrita.alterou_endereco', 'cadastros', tabela='clientes',
                                  registro_id=id, antes={k: _end_antes[k] for k in _end_mud},
                                  depois={k: _end_novo[k] for k in _end_mud})
                    try:
                        if endereco_principal:
                            EnderecoCliente.update(
                                endereco_principal['id'],
                                endereco_principal.get('tipo') or 'COMERCIAL',
                                cep,
                                logradouro,
                                numero,
                                complemento=complemento,
                                bairro=bairro,
                                cidade=cidade,
                                estado=estado,
                                pais=endereco_principal.get('pais') or 'Brasil',
                                principal=True
                            )
                        else:
                            EnderecoCliente.create(
                                cliente_id=id,
                                tipo='COMERCIAL',
                                cep=cep,
                                logradouro=logradouro,
                                numero=numero,
                                complemento=complemento,
                                bairro=bairro,
                                cidade=cidade,
                                estado=estado,
                                pais='Brasil',
                                principal=True
                            )
                    except Exception as endereco_error:
                        print(f"Erro ao salvar endereço do cliente {id}: {endereco_error}")
                
                if _virou_cliente:
                    flash(f'{data.get("nome_razao_social")} agora é o cliente '
                          f'{data.get("numero_cliente")} e já aparece na lista de Clientes.',
                          'success')
                else:
                    flash('Cliente atualizado com sucesso!', 'success')
                return redirect(url_for('clientes.detalhes', id=id))
            else:
                if _pasta_st == 'movida':
                    # A pasta já tinha ido, mas a gravação falhou: desfaz o
                    # movimento para pasta e cadastro seguirem casados.
                    dropbox_sync.renomear_pasta_empresa(pasta_nova, pasta_antiga)
                flash('Erro ao atualizar cliente. Verifique os dados e tente novamente.', 'danger')
        except Exception as e:
            flash(f'Erro ao atualizar cliente: {str(e)}', 'danger')
            print(f"Erro ao atualizar cliente {id}: {str(e)}")
    
    return _render_editar(cliente, endereco_principal)


@clientes.route('/clientes/avulsos')
@permission_required('clientes.index')
def avulsos():
    """A ÚNICA tela onde o avulso aparece.

    Cadastro de empresa que ainda não é cliente (serviço pontual, orçamento do
    Comercial, legalização). Todo mundo do escritório vê — a regra de "cada um
    vê o que trouxe" é do Comercial, não daqui.
    """
    busca = (request.args.get('busca') or '').strip()
    lista = Cliente.listar_avulsos(busca or None)

    # O cartão é o MESMO da tela de Clientes (14/10/2026): mesmo enfeite
    # (município colorido, grupo, regime, selo do certificado).
    _enfeitar_cartoes(lista)
    for a in lista:
        a['f_dias'] = (date.today() - a['avulso_em'].date()).days if a.get('avulso_em') else None

    # Indicadores contados da própria lista, sem consulta extra e sem
    # inventar número: total, quantos já têm arquivo guardado e há quantos
    # dias espera o mais antigo.
    hoje = date.today()
    idades = [(hoje - a['avulso_em'].date()).days
              for a in lista if a.get('avulso_em')]
    stats = {
        'total': len(lista),
        'com_arquivo': sum(1 for a in lista if (a.get('docs') or 0) > 0),
        'espera_max': max(idades) if idades else 0,
    }
    return render_template('clientes/avulsos.html', avulsos=lista,
                           busca=busca, stats=stats, contabilidades_json=_contab_combo())


@clientes.route('/clientes/<int:id>/converter', methods=['POST'])
@permission_required('clientes.edit')
def converter_avulso(id):
    """Avulso vira cliente: ganha número e a PASTA VAI JUNTO.

    Ordem de propósito: renomeia a pasta ANTES de gravar. Com o Dropbox fora
    do ar a conversão é recusada, em vez de deixar cliente novo com o
    histórico preso na pasta sem número — mesma regra da troca de número.
    """
    from utils import dropbox_sync
    c = Cliente.get_by_id(id)
    if not c:
        flash('Cadastro não encontrado.', 'danger')
        return redirect(url_for('clientes.avulsos'))
    if not c.get('avulso'):
        flash('Este cadastro já é um cliente.', 'warning')
        return redirect(url_for('clientes.detalhes', id=id))

    numero = _numero_inteiro(request.form.get('numero_cliente') or '')
    if not numero:
        flash('Informe o número que este cliente vai receber.', 'danger')
        return redirect(url_for('clientes.avulsos'))
    if Cliente.existe_numero_cliente(numero, id):
        flash(f'O número {numero} já está em uso por outro cliente.', 'danger')
        return redirect(url_for('clientes.avulsos'))
    # Virar cliente exige a ORIGEM (14/10/2026): aberta pela casa ou o
    # escritório de onde veio. Só PJ — pessoa física não tem contabilidade anterior.
    if c.get('tipo_pessoa') == 'PJ' and not _origem_ok(request.form):
        flash('Para virar cliente, diga de onde a empresa veio: marque "aberta pela Qualicontax" '
              'ou escolha a contabilidade anterior.', 'danger')
        return redirect(url_for('clientes.avulsos'))

    # 1) a pasta primeiro: EMPRESAS/{razão} -> EMPRESAS/{nº - razão}. O avulso
    #    não tem número, então tudo dele (certificado, XML) já nasce na pasta só
    #    com a razão; virar cliente é renomear, igual à troca de número.
    de = dropbox_sync._build_empresa_folder(None, c.get('nome_razao_social') or '')
    para = dropbox_sync._build_empresa_folder(numero, c.get('nome_razao_social') or '')
    st, cam_de, cam_para = dropbox_sync.renomear_pasta_empresa(de, para)
    if st in ('conflito', 'erro'):
        flash(('Já existe uma pasta com esse número no Dropbox — junte ou '
               'renomeie lá e tente de novo. Nada foi alterado.'
               if st == 'conflito' else
               'Não consegui mover a pasta do Dropbox agora. Nada foi alterado '
               '— tente novamente em instantes.'), 'danger')
        return redirect(url_for('clientes.avulsos'))

    # 2) agora sim o cadastro
    if not Cliente.converter_em_cliente(id, numero):
        # desfaz o movimento: pasta e cadastro não podem desencontrar
        if st == 'movida':
            dropbox_sync.renomear_pasta_empresa(para, de)
        flash('Erro ao converter o cadastro. Nada foi alterado.', 'danger')
        return redirect(url_for('clientes.avulsos'))

    # 3) reaponta os caminhos gravados (mesmas 7 tabelas da troca de número)
    if cam_de:
        _like = (cam_de.replace('\\', '\\\\')
                 .replace('%', '\\%').replace('_', '\\_')) + '/%'
        _corte = len(cam_de) + 1
        _tot = 0
        for _t, _c in (('nfse_capturadas', 'xml_path'),
                       ('cte_documentos', 'xml_caminho'),
                       ('dfe_documentos', 'xml_caminho'),
                       ('dfe_eventos', 'xml_caminho'),
                       ('nfe_importacoes', 'xml_caminho'),
                       ('dfe_certificados', 'dropbox_path'),
                       ('documentos', 'caminho_arquivo')):
            _n = execute_query(
                f'UPDATE {_t} SET {_c} = CONCAT(%s, SUBSTRING({_c}, %s)) '
                f'WHERE {_c} LIKE %s', (cam_para, _corte, _like))
            _tot += _n or 0

    if c.get('tipo_pessoa') == 'PJ':
        if request.form.get('aberta_pela_casa') and not c.get('aberta_pela_casa'):
            execute_query('UPDATE clientes SET aberta_pela_casa = 1 WHERE id = %s', (id,))
        _salvar_origem(id, request.form, c.get('contabilidade_anterior_id'))

    registrar('escrita.converteu_avulso', 'cadastros', tabela='clientes',
              registro_id=id,
              antes={'avulso': True, 'pasta': de},
              depois={'numero_cliente': numero, 'pasta': para,
                      'dropbox': st, 'nome': c.get('nome_razao_social')})
    flash(f'{c.get("nome_razao_social")} agora é o cliente {numero} — e tudo o '
          f'que já estava guardado foi junto.', 'success')
    return redirect(url_for('clientes.detalhes', id=id))


@clientes.route('/clientes/<int:id>/inativar', methods=['POST'])
@login_required
def inativar(id):
    """Inativar cliente"""
    sucesso = Cliente.update_situacao(id, 'INATIVO')
    if sucesso:
        flash('Cliente inativado com sucesso!', 'success')
    else:
        flash('Erro ao inativar cliente!', 'danger')
    return redirect(url_for('clientes.index'))


@clientes.route('/clientes/<int:id>/deletar', methods=['POST'])
@login_required
def delete(id):
    """DELETE cliente"""
    cliente = Cliente.get_by_id(id)
    
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))
    
    if Cliente.delete(id):
        flash('Cliente removido com sucesso!', 'success')
    else:
        flash('Erro ao remover cliente.', 'danger')
    
    return redirect(url_for('clientes.index'))


# Rotas para Endereços
# ---------------------------------------------------------------------------
# Inscrições estaduais (09/11/2026): várias por cadastro, com tipo Normal / Substituto Tributário /
# Produtor rural. Ver models/inscricao_estadual.py.
# ---------------------------------------------------------------------------
def _ie_resumo(x):
    t = IE.TIPO_ROTULO.get(x.get('tipo'), x.get('tipo'))
    r = f"{x.get('uf') or '--'} {x.get('ie')} ({t}{', principal' if x.get('principal') else ''})"
    if x.get('propriedade'):
        r += f" · {x['propriedade']} / {x.get('municipio') or ''}"
    return r


@clientes.route('/clientes/<int:id>/ies', methods=['POST'])
@login_required
def ie_salvar(id):
    """Inclui (sem ie_id) ou altera uma IE. JSON {ie_id?, uf, ie, tipo, propriedade, municipio, principal}."""
    if not Cliente.get_by_id(id):
        return jsonify(ok=False, erro='Cadastro não encontrado.'), 404
    d = request.get_json(silent=True) or {}
    iid = int(d['ie_id']) if str(d.get('ie_id') or '').isdigit() else None
    antes = next((x for x in IE.listar(id) if x['id'] == iid), None) if iid else None
    if iid and not antes:
        return jsonify(ok=False, erro='Inscrição não encontrada.'), 404
    ok, r = IE.salvar(id, d, iid)
    if not ok:
        return jsonify(ok=False, erro=r), 400
    depois = next((x for x in IE.listar(id) if x['id'] == r), None) or {}
    registrar('escrita.alterou_ie', 'cadastros', tabela='clientes', registro_id=id,
              antes={'cliente_id': id, 'inscricao': _ie_resumo(antes) if antes else None},
              depois={'cliente_id': id, 'inscricao': _ie_resumo(depois)})
    return jsonify(ok=True)


@clientes.route('/clientes/<int:id>/ies/<int:ie_id>/excluir', methods=['POST'])
@login_required
def ie_excluir(id, ie_id):
    x = next((x for x in IE.listar(id) if x['id'] == ie_id), None)
    if not x:
        return jsonify(ok=False, erro='Inscrição não encontrada.'), 404
    IE.excluir(id, ie_id)
    registrar('escrita.excluiu_ie', 'cadastros', tabela='clientes', registro_id=id,
              antes={'cliente_id': id, 'inscricao': _ie_resumo(x)}, depois={'cliente_id': id})
    return jsonify(ok=True)


@clientes.route('/clientes/<int:id>/ies/isenta', methods=['POST'])
@login_required
def ie_isenta(id):
    c = Cliente.get_by_id(id)
    if not c:
        return jsonify(ok=False, erro='Cadastro não encontrado.'), 404
    isenta = bool((request.get_json(silent=True) or {}).get('isenta'))
    IE.marcar_isenta(id, isenta)
    registrar('escrita.alterou_cliente', 'cadastros', tabela='clientes', registro_id=id,
              antes={'inscricao_estadual': c.get('inscricao_estadual')},
              depois={'inscricao_estadual': 'ISENTO' if isenta else (Cliente.get_by_id(id) or {}).get('inscricao_estadual')})
    return jsonify(ok=True)


@clientes.route('/clientes/<int:cliente_id>/enderecos/novo', methods=['POST'])
@login_required
def novo_endereco(cliente_id):
    """Adicionar novo endereço"""
    endereco_id = EnderecoCliente.create(
        cliente_id=cliente_id,
        tipo=request.form.get('tipo'),
        cep=request.form.get('cep'),
        logradouro=request.form.get('logradouro'),
        numero=request.form.get('numero'),
        complemento=request.form.get('complemento'),
        bairro=request.form.get('bairro'),
        cidade=request.form.get('cidade'),
        estado=request.form.get('estado'),
        pais=request.form.get('pais', 'Brasil'),
        principal=request.form.get('principal') == 'on'
    )
    
    if endereco_id:
        registrar('escrita.criou_endereco', 'cadastros', tabela='enderecos_clientes',
                  registro_id=endereco_id,
                  depois={'cliente_id': cliente_id, 'tipo': request.form.get('tipo'),
                          'cep': request.form.get('cep'), 'logradouro': request.form.get('logradouro'),
                          'numero': request.form.get('numero'), 'bairro': request.form.get('bairro'),
                          'cidade': request.form.get('cidade'), 'estado': request.form.get('estado'),
                          **rotulo_empresa(cliente_id)})
        flash('Endereço adicionado com sucesso!', 'success')
    else:
        flash('Erro ao adicionar endereço!', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/enderecos/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_endereco(id):
    """Excluir endereço"""
    endereco = EnderecoCliente.get_by_id(id)
    if not endereco:
        flash('Endereço não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    
    cliente_id = endereco['cliente_id']

    sucesso = EnderecoCliente.delete(id)
    if sucesso:
        registrar('escrita.excluiu_endereco', 'cadastros', tabela='enderecos_clientes',
                  registro_id=id,
                  antes={'cliente_id': cliente_id, 'tipo': endereco.get('tipo'),
                         'logradouro': endereco.get('logradouro'), 'cidade': endereco.get('cidade'),
                         **rotulo_empresa(cliente_id)})
        flash('Endereço excluído com sucesso!', 'success')
    else:
        flash('Erro ao excluir endereço!', 'danger')
    
    return redirect(url_for('clientes.detalhes', id=cliente_id))


# Rotas para Contatos
def _contato_form():
    """Lê e valida o formulário do contato (05/11/2026, Anderson): NOME e CELULAR obrigatórios em
    todo contato (cliente ou avulso); o que ele recebe é a lista de departamentos marcados.
    Devolve (dados, faltas)."""
    f = request.form
    nome = (f.get('nome') or '').strip()
    celular = (f.get('celular') or '').strip()
    falta = []
    if not nome:
        falta.append('nome')
    if not _telefone_valido(celular):
        falta.append('celular (DDD + número)')
    tel = (f.get('telefone') or '').strip()
    if re.sub(r'\D', '', tel) and not _telefone_valido(tel):
        falta.append('telefone (número inválido)')
    email = (f.get('email') or '').strip().lower()[:255]
    if email and not _email_ok(email):
        falta.append('e-mail (%s não é um e-mail)' % email)
    # 10/11/2026 (Anderson): "o contato tem 2 celulares ou 3 e-mails: dar a opção de incluir mais"
    cel_extra, email_extra = [], []
    for v in f.getlist('celular_extra'):
        v = (v or '').strip()
        if not re.sub(r'\D', '', v):
            continue
        if not _telefone_valido(v):
            falta.append('outro celular (%s inválido)' % v)
        elif re.sub(r'\D', '', v) != re.sub(r'\D', '', celular) and v not in cel_extra:
            cel_extra.append(v)
    for v in f.getlist('email_extra'):
        v = (v or '').strip().lower()[:255]
        if not v:
            continue
        if not _email_ok(v):
            falta.append('outro e-mail (%s não é um e-mail)' % v)
        elif v != email and v not in email_extra:
            email_extra.append(v)
    if email_extra and not email:                    # o primeiro vira o principal
        email = email_extra.pop(0)
    dados = dict(nome=nome, cargo=(f.get('cargo') or '').strip()[:100] or None,
                 email=email or None,
                 telefone=tel or None, celular=celular,
                 principal=f.get('principal') == 'on',
                 departamentos=[int(d) for d in f.getlist('departamentos') if d.isdigit()])
    dados['_extras'] = (cel_extra, email_extra)
    return dados, falta


def _email_ok(v):
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', v or ''))


def _contato_resumo(dados):
    nomes = {d['id']: d['nome'] for d in ContatoCliente.departamentos_disponiveis()}
    r = {k: dados.get(k) for k in ('nome', 'cargo', 'email', 'telefone', 'celular', 'principal')}
    r['departamentos'] = [nomes[i] for i in dados.get('departamentos', []) if i in nomes]
    cel_extra, email_extra = dados.get('_extras') or ([], [])
    if cel_extra:
        r['outros_celulares'] = cel_extra
    if email_extra:
        r['outros_emails'] = email_extra
    return r


@clientes.route('/clientes/<int:id>/responsaveis', methods=['POST'])
@login_required
def salvar_responsaveis(id):
    """Responsáveis do escritório por departamento (05/11/2026, migração Acessórias): um funcionário
    por departamento. Só salva com TODOS os departamentos escolhidos — Anderson (05/11/2026): "não poderá
    salvar sem eleger os responsáveis... Mas só nessa etapa"; no futuro o cadastro gera um link e cada
    departamento elege o seu. O que a migração deixou vazio continua vazio até alguém escolher."""
    cliente = Cliente.get_by_id(id)
    if not cliente:
        flash('Cliente não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    from models.cliente_responsavel import SEM, ACEITAM_NINGUEM
    validos = {u['id']: u['nome'] for u in ClienteResponsavel.usuarios_disponiveis()}
    atuais = ClienteResponsavel.por_cliente(id)
    antes = {r['departamento']: (r['usuario_nome'] or ACEITAM_NINGUEM.get(r['departamento']) if r['definido'] else None)
             for r in atuais}
    depois, escolhas, faltando = {}, {}, []
    for r in atuais:
        bruto = (request.form.get('resp_%d' % r['departamento_id']) or '').strip()
        if bruto == SEM and r['departamento'] in ACEITAM_NINGUEM:      # "Sem visita"
            depois[r['departamento']] = ACEITAM_NINGUEM[r['departamento']]
            escolhas[r['departamento_id']] = SEM
            continue
        uid = int(bruto) if bruto.isdigit() else None
        if not uid:
            faltando.append(r['departamento'])
        elif uid not in validos:
            flash('Funcionário inválido para %s.' % r['departamento'], 'danger')
            return redirect(url_for('clientes.detalhes', id=id))
        depois[r['departamento']] = validos.get(uid) if uid else None
        escolhas[r['departamento_id']] = uid
    if faltando:
        flash('Falta escolher o responsável de %s. Todos os departamentos precisam de um para salvar.'
              % ', '.join(faltando), 'danger')
        return redirect(url_for('clientes.detalhes', id=id))
    for r in atuais:
        atual = r['usuario_id'] or (SEM if r['definido'] else None)
        if escolhas[r['departamento_id']] != atual:
            ClienteResponsavel.definir(id, r['departamento_id'], escolhas[r['departamento_id']])
    if antes != depois:
        registrar('escrita.alterou_responsaveis', 'cadastros', tabela='clientes', registro_id=id,
                  antes=antes, depois=depois)
        flash('Responsáveis salvos.', 'success')
    else:
        flash('Nada mudou nos responsáveis.', 'info')
    return redirect(url_for('clientes.detalhes', id=id))


# ── Link para os departamentos elegerem o responsável (06/11/2026) ─────────
# Anderson: "o Miguel manda o link e os departamentos elegem quem ficará como responsável de cada
# departamento"; "quando enviar tem que acessar o sistema com Login e Senha". Cada um escolhe só o
# próprio departamento; quem gerou o link, a Diretoria e o admin escolhem todos. Vale 7 dias e morre
# quando os 7 estão preenchidos.
@clientes.route('/clientes/<int:id>/responsaveis/convite', methods=['POST'])
@login_required
def responsaveis_convite_novo(id):
    cliente = Cliente.get_by_id(id)
    if not cliente:
        flash('Cliente não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    ativo = ClienteResponsavel.convite_ativo(id)
    if ativo:
        link = url_for('clientes.responsaveis_convite', token=ativo['token'], _external=True)
        flash('Já existe um link em aberto para %s (vale até %s): %s' % (cliente['nome_razao_social'], ativo['expira_em'].strftime('%d/%m'), link), 'info')
        return redirect(url_for('clientes.detalhes', id=id))
    cid, token = ClienteResponsavel.convite_criar(id, current_user.id, 7)
    link = url_for('clientes.responsaveis_convite', token=token, _external=True)
    registrar('escrita.gerou_link_responsaveis', 'cadastros', tabela='clientes_responsaveis_convites', registro_id=cid,
              depois={'cliente_id': id, 'empresa': cliente['nome_razao_social'], 'dias': 7})
    flash('Link para os departamentos elegerem os responsáveis de %s (vale 7 dias, exige login): %s'
          % (cliente['nome_razao_social'], link), 'success')
    return redirect(url_for('clientes.detalhes', id=id))


def _convite_responsaveis(token):
    v = ClienteResponsavel.convite_por_token(token)
    if not v:
        return None, 'Este link não existe.'
    if v['concluido_em']:
        return None, 'Este link já cumpriu o papel: todos os departamentos de %s têm responsável.' % v['nome_razao_social']
    from datetime import datetime as _dt
    if v['expira_em'] < _dt.now():
        return None, 'Este link venceu. Peça outro na ficha da empresa.'
    return v, None


@clientes.route('/clientes/responsaveis/convite/<token>')
@login_required
def responsaveis_convite(token):
    v, erro = _convite_responsaveis(token)
    if erro:
        flash(erro, 'warning')
        return redirect('/')
    v['respostas'] = ClienteResponsavel.convite_respostas(v['id'])
    return render_template('clientes/responsaveis_convite.html', convite=v,
                           responsaveis=ClienteResponsavel.por_cliente(v['cliente_id']),
                           funcionarios=ClienteResponsavel.usuarios_disponiveis(),
                           permitidos=ClienteResponsavel.departamentos_que_pode(current_user, v))


@clientes.route('/clientes/responsaveis/convite/<token>/salvar', methods=['POST'])
@login_required
def responsaveis_convite_salvar(token):
    v, erro = _convite_responsaveis(token)
    if erro:
        flash(erro, 'warning')
        return redirect('/')
    permitidos = ClienteResponsavel.departamentos_que_pode(current_user, v)
    validos = {u['id']: u['nome'] for u in ClienteResponsavel.usuarios_disponiveis()}
    atuais = ClienteResponsavel.por_cliente(v['cliente_id'])
    mudou, antes, depois = [], {}, {}
    for r in atuais:
        if permitidos is not None and r['departamento_id'] not in permitidos:
            continue
        from models.cliente_responsavel import SEM, ACEITAM_NINGUEM
        bruto = (request.form.get('resp_%d' % r['departamento_id']) or '').strip()
        if bruto == SEM and r['departamento'] in ACEITAM_NINGUEM:          # "Sem visita" (09/11/2026)
            if r['definido'] and not r['usuario_id']:
                continue
            ClienteResponsavel.definir(v['cliente_id'], r['departamento_id'], SEM)
            mudou.append(r['departamento'])
            antes[r['departamento']] = r['usuario_nome']
            depois[r['departamento']] = ACEITAM_NINGUEM[r['departamento']]
            continue
        uid = int(bruto) if bruto.isdigit() else None
        if not uid or uid not in validos or uid == (r['usuario_id'] or None):
            continue
        ClienteResponsavel.convite_responder(v, r['departamento_id'], uid, current_user.id)
        mudou.append(r['departamento'])
        antes[r['departamento']] = r['usuario_nome']
        depois[r['departamento']] = validos[uid]
    if not mudou:
        flash('Nada escolhido ainda: marque o responsável do seu departamento.', 'warning')
        return redirect(url_for('clientes.responsaveis_convite', token=token))
    registrar('escrita.alterou_responsaveis', 'cadastros', tabela='clientes', registro_id=v['cliente_id'],
              antes=antes, depois=dict(depois, origem='link dos departamentos'))
    if ClienteResponsavel.convite_fechar_se_completo(v):
        flash('Obrigado! %s: todos os departamentos de %s têm responsável e o link foi encerrado.' % (', '.join(mudou), v['nome_razao_social']), 'success')
        return redirect('/')
    flash('Salvo: %s de %s. Os outros departamentos ainda escolhem pelo mesmo link.' % (', '.join(mudou), v['nome_razao_social']), 'success')
    return redirect(url_for('clientes.responsaveis_convite', token=token))


@clientes.route('/clientes/<int:cliente_id>/contatos/novo', methods=['POST'])
@login_required
def novo_contato(cliente_id):
    """Adicionar novo contato (celular obrigatório; departamentos que recebe)."""
    dados, falta = _contato_form()
    if falta:
        flash('Contato não salvo: falta ' + ' e '.join(falta) + '.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id) + '#contatos')
    extras = dados.pop('_extras')
    contato_id = ContatoCliente.create(cliente_id=cliente_id, ativo=True, **dados)
    if contato_id:
        ContatoCliente.set_meios(contato_id, *extras)
        dados['_extras'] = extras
        registrar('escrita.criou_contato', 'cadastros', tabela='contatos_clientes', registro_id=contato_id,
                  depois=dict(_contato_resumo(dados), cliente_id=cliente_id))
        flash('Contato adicionado.', 'success')
    else:
        flash('Erro ao adicionar contato!', 'danger')
    return redirect(url_for('clientes.detalhes', id=cliente_id) + '#contatos')


@clientes.route('/contatos/<int:id>/editar', methods=['POST'])
@login_required
def editar_contato(id):
    """Editar contato (05/11/2026): mesmas regras do novo."""
    contato = ContatoCliente.get_by_id(id)
    if not contato:
        flash('Contato não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    dados, falta = _contato_form()
    if falta:
        flash('Contato não salvo: falta ' + ' e '.join(falta) + '.', 'danger')
        return redirect(url_for('clientes.detalhes', id=contato['cliente_id']) + '#contatos')
    antes = {k: contato.get(k) for k in ('nome', 'cargo', 'email', 'telefone', 'celular', 'principal')}
    antes['departamentos'] = contato.get('departamentos') or []
    if contato.get('celulares_extra'):
        antes['outros_celulares'] = contato['celulares_extra']
    if contato.get('emails_extra'):
        antes['outros_emails'] = contato['emails_extra']
    antes['cliente_id'] = contato['cliente_id']
    extras = dados.pop('_extras')
    ContatoCliente.update(id, ativo=bool(contato.get('ativo', True)), **dados)
    ContatoCliente.set_meios(id, *extras)
    dados['_extras'] = extras
    registrar('escrita.alterou_contato', 'cadastros', tabela='contatos_clientes', registro_id=id,
              antes=antes, depois=dict(_contato_resumo(dados), cliente_id=contato['cliente_id']))
    flash('Contato salvo.', 'success')
    return redirect(url_for('clientes.detalhes', id=contato['cliente_id']) + '#contatos')


@clientes.route('/api/contatos/por-celular/<digitos>')
@login_required
def api_contato_por_celular(digitos):
    """Contatos de OUTRAS empresas com este celular — para reaproveitar nome/e-mail (05/11/2026)."""
    cid = request.args.get('cliente_id', type=int)
    return jsonify(ContatoCliente.por_celular(digitos, exceto_cliente_id=cid))


@clientes.route('/api/enderecos/complemento-tipos')
@login_required
def api_complemento_tipos():
    """Botões do complemento do endereço: + QUADRA, + LOTE, + SALA... (10/11/2026)."""
    from models.endereco_cliente import complemento_tipos
    return jsonify([{'id': t['id'], 'nome': t['nome']} for t in complemento_tipos()])


@clientes.route('/api/clientes/documento-existe')
@login_required
def api_documento_existe():
    """O CPF/CNPJ digitado no "Novo cadastro" já existe? Anderson (10/11/2026): "o sistema não pode
    permitir que façam 2 cadastros da mesma PF ou PJ tem que barrar" — e barrar só no Salvar jogava
    fora tudo o que foi digitado. Agora o formulário pergunta assim que o documento fica completo."""
    from utils.acesso import pode_ver_empresa
    dig = re.sub(r'\D', '', request.args.get('doc') or '')
    if len(dig) not in (11, 14):
        return jsonify(existe=False)
    r = execute_query("""SELECT id, nome_razao_social, numero_cliente, avulso, situacao, tipo_pessoa
                           FROM clientes WHERE REGEXP_REPLACE(cpf_cnpj, '[^0-9]', '') = %s LIMIT 1""",
                      (dig,), fetch=True, fetch_one=True)
    if not r:
        # Cônjuge de alguém (11/11/2026): "digitando o CPF já inicia o cadastro com o nome".
        cj = execute_query("""SELECT d.conjuge_nome, d.cliente_id FROM clientes_pf_dados d
                               WHERE REGEXP_REPLACE(d.conjuge_cpf, '[^0-9]', '') = %s AND d.conjuge_nome <> ''
                               ORDER BY d.alterado_em DESC LIMIT 1""", (dig,), fetch=True, fetch_one=True)
        if cj and pode_ver_empresa(cj['cliente_id']):
            de = execute_query("SELECT nome_razao_social FROM clientes WHERE id = %s", (cj['cliente_id'],),
                               fetch=True, fetch_one=True) or {}
            return jsonify(existe=False, conjuge={'nome': cj['conjuge_nome'], 'de': de.get('nome_razao_social')})
        return jsonify(existe=False)
    if not pode_ver_empresa(r['id']):
        return jsonify(existe=True, reservado=True)
    return jsonify(existe=True, id=r['id'], nome=r['nome_razao_social'], numero=r.get('numero_cliente'),
                   avulso=bool(r.get('avulso')), situacao=r.get('situacao'),
                   url=url_for('clientes.detalhes', id=r['id']))


@clientes.route('/api/enderecos/complemento-tipos/novo', methods=['POST'])
@login_required
def api_complemento_tipo_novo():
    """Tipo novo de complemento. Anderson (10/11/2026): "ter o botão de incluir novos, e tudo isso com
    registro de quem está criando" — criado_por na tabela e a linha no histórico."""
    from models.endereco_cliente import complemento_tipo_criar
    nome = ((request.get_json(silent=True) or {}).get('nome') or request.form.get('nome') or '').strip()
    if len(nome) < 2 or not re.search(r'[A-Za-zÀ-ú]', nome):
        return jsonify({'ok': False, 'erro': 'Escreva o nome do tipo (ex.: GLEBA, PAVILHÃO).'}), 400
    tid, nome, ja = complemento_tipo_criar(nome, current_user.id)
    if not tid:
        return jsonify({'ok': False, 'erro': 'Não consegui incluir o tipo.'}), 400
    if not ja:
        registrar('escrita.criou_tipo_complemento', 'cadastros', tabela='enderecos_complemento_tipos',
                  registro_id=tid, depois={'nome': nome})
    return jsonify({'ok': True, 'id': tid, 'nome': nome, 'ja_existia': ja})


@clientes.route('/api/contatos/da-pessoa')
@login_required
def api_contato_da_pessoa():
    """A mesma pessoa como contato em OUTRAS empresas, pelo nome ou por qualquer celular (10/11/2026):
    a tela mostra o e-mail que ela usa em cada empresa e pergunta qual vale nesta."""
    cid = request.args.get('cliente_id', type=int)
    return jsonify(ContatoCliente.da_pessoa(request.args.get('nome'), request.args.getlist('cel'), exceto_cliente_id=cid))


@clientes.route('/contatos/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_contato(id):
    """Excluir contato"""
    contato = ContatoCliente.get_by_id(id)
    if not contato:
        flash('Contato não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    
    cliente_id = contato['cliente_id']
    
    sucesso = ContatoCliente.delete(id)
    if sucesso:
        flash('Contato excluído com sucesso!', 'success')
    else:
        flash('Erro ao excluir contato!', 'danger')
    
    return redirect(url_for('clientes.detalhes', id=cliente_id))


# Rotas para Cadastros Adicionais
@clientes.route('/clientes/<int:cliente_id>/cadastros-adicionais/novo', methods=['POST'])
@login_required
def novo_cadastro_adicional(cliente_id):
    """Adicionar novo cadastro adicional"""
    tipo = request.form.get('tipo', '').strip()
    campo = request.form.get('campo', '').strip()

    if not tipo or not campo:
        flash('Preencha os campos obrigatórios de cadastro adicional.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    cadastro_id = CadastroAdicionalCliente.create(
        cliente_id=cliente_id,
        tipo=tipo,
        campo=campo,
        valor=request.form.get('valor'),
        data_referencia=request.form.get('data_referencia'),
        observacoes=request.form.get('observacoes'),
        ativo=True
    )

    if cadastro_id:
        flash('Cadastro adicional adicionado com sucesso!', 'success')
    else:
        flash('Erro ao adicionar cadastro adicional!', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/cadastros-adicionais/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_cadastro_adicional(id):
    """Excluir cadastro adicional"""
    cadastro = CadastroAdicionalCliente.get_by_id(id)
    if not cadastro:
        flash('Cadastro adicional não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))

    cliente_id = cadastro['cliente_id']
    sucesso = CadastroAdicionalCliente.delete(id)

    if sucesso:
        flash('Cadastro adicional excluído com sucesso!', 'success')
    else:
        flash('Erro ao excluir cadastro adicional!', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id))


# Rotas para Sócios
@clientes.route('/clientes/<int:cliente_id>/socios/buscar-pf')
@login_required
def socios_buscar_pf(cliente_id):
    """Busca cadastros para virarem sócio (por nome, CPF/CNPJ ou número).

    Só devolve quem pode entrar: pessoa física OU empresa (desde 07/10/2026),
    cliente ou avulso, não inativo, que não seja a própria empresa e que
    ainda não seja sócio DESTA empresa. É a fonte do modal "Adicionar Sócio"
    — sócio não se digita, escolhe-se do cadastro.
    """
    q = (request.args.get('q') or '').strip()
    if len(q) < 2:
        return jsonify([])
    dig = ''.join(ch for ch in q if ch.isdigit())
    rows = execute_query(
        """SELECT id, nome_razao_social AS nome, cpf_cnpj AS cpf, email,
                  COALESCE(NULLIF(celular, ''), telefone) AS telefone,
                  numero_cliente AS numero, tipo_pessoa AS tipo, COALESCE(avulso, 0) AS avulso
             FROM clientes
            WHERE COALESCE(situacao, 'ATIVO') <> 'INATIVO'
              AND id <> %s
              AND (nome_razao_social LIKE %s
                   OR REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '-', ''), '/', '') LIKE %s
                   OR numero_cliente = %s)
              AND id NOT IN (SELECT COALESCE(pf_cliente_id, 0)
                               FROM socios_clientes WHERE cliente_id = %s AND ativo = 1)
            ORDER BY (numero_cliente = %s) DESC, nome_razao_social
            LIMIT 10""",
        (cliente_id, f'%{q}%', f'%{dig}%' if dig else q, q, cliente_id, q), fetch=True) or []
    return jsonify(filtrar_lista(rows))           # empresa reservada


@clientes.route('/clientes/<int:cliente_id>/socios/novo', methods=['POST'])
@login_required
def novo_socio(cliente_id):
    """Adicionar novo sócio"""
    # Sócio NÃO se digita: é um cadastro escolhido na busca — pessoa física ou,
    # desde 07/10/2026, empresa (cliente ou avulsa). Nome/documento/e-mail/
    # telefone saem do cadastro (fonte única) — decisão do Anderson em
    # 19/08/2026, no lugar da digitação livre sem vínculo.
    pf_raw = (request.form.get('pf_cliente_id') or '').strip()
    percentual_raw = (request.form.get('percentual_participacao') or '').strip()
    responsavel = request.form.get('responsavel') == 'on'
    qualificacao = _qualificacao(request.form.get('qualificacao'))
    entrada_raw = (request.form.get('data_entrada') or '').strip()
    data_entrada = _data_iso(entrada_raw)
    if entrada_raw and not data_entrada:
        flash('Data de entrada inválida (não pode ser no futuro).', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))
    capital_valor = _valor_opcional(request.form.get('capital_valor'))
    pro_labore = _valor_opcional(request.form.get('pro_labore'))
    if capital_valor is False or pro_labore is False:
        flash('Capital ou pró-labore inválido: use só números (ex.: 10.000,00).', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    if not pf_raw.isdigit() or not percentual_raw:
        flash('Escolha um cadastro (pessoa física ou empresa) e informe o percentual.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    pf = Cliente.get_by_id(int(pf_raw))
    if not pf or (pf.get('tipo_pessoa') or '').upper() not in ('PF', 'PJ'):
        flash('O sócio precisa ter cadastro (pessoa física ou empresa). '
              'Cadastre primeiro e escolha na busca.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))
    if int(pf_raw) == cliente_id:
        flash('A própria empresa não pode ser sócia dela mesma.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))
    if SocioCliente.existe_pf(cliente_id, int(pf_raw)):
        flash(f'{pf.get("nome_razao_social")} já é sócio desta empresa.', 'warning')
        return redirect(url_for('clientes.detalhes', id=cliente_id))
    tipo_socio = (pf.get('tipo_pessoa') or '').upper()
    if tipo_socio == 'PJ' and qual_recusada_pj(qualificacao):
        flash(f'Empresa sócia não pode ser "{qualificacao}". Use "Sócio Pessoa Jurídica Domiciliado no Brasil" '
              '— quem administra é sempre uma pessoa física.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))
    if responsavel:
        quadro = [dict(x, tipo=x.get('tipo_pessoa') or 'PF', qual=x.get('qualificacao') or '')
                  for x in SocioCliente.get_by_cliente(cliente_id)]
        novo = {'tipo': tipo_socio, 'qual': qualificacao}
        if not pode_ser_responsavel(novo, quadro + [novo]):
            flash(f'{pf.get("nome_razao_social")} não pode ser o responsável: '
                  + ('empresa sócia nunca é.' if tipo_socio == 'PJ'
                     else f'é "{qualificacao}", e o responsável é quem administra (ex.: Sócio-Administrador).'),
                  'danger')
            return redirect(url_for('clientes.detalhes', id=cliente_id))

    nome = pf.get('nome_razao_social') or ''
    cpf = pf.get('cpf_cnpj') or ''
    email = (pf.get('email') or '').strip() or None
    telefone = (pf.get('celular') or pf.get('telefone') or '').strip() or None

    try:
        percentual = _parse_decimal_input(percentual_raw)
    except (InvalidOperation, ValueError):
        flash('Percentual inválido. Use um número válido.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    if percentual <= 0 or percentual > 100:
        flash('Percentual deve ser maior que 0 e menor ou igual a 100.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    total_atual = SocioCliente.get_total_percentual(cliente_id)
    novo_total = total_atual + percentual
    if novo_total > Decimal('100'):
        restante = Decimal('100') - total_atual
        flash(f'Total de participação não pode ultrapassar 100%. Restante disponível: {restante:.2f}%.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    socio_id = SocioCliente.create(
        cliente_id=cliente_id,
        pf_cliente_id=int(pf_raw),
        nome=nome,
        cpf=cpf,
        email=email,
        telefone=telefone,
        percentual_participacao=percentual,
        responsavel=responsavel,
        ativo=True,
        qualificacao=qualificacao,
        data_entrada=data_entrada,
        capital_valor=capital_valor,
        pro_labore=pro_labore
    )

    if socio_id:
        registrar('escrita.criou_socio', 'cadastros', tabela='socios_clientes',
                  registro_id=socio_id,
                  depois={'cliente_id': cliente_id, 'pf_cliente_id': int(pf_raw),
                          'nome': nome, 'cpf': cpf,
                          'email': email, 'telefone': telefone,
                          'percentual_participacao': str(percentual), 'responsavel': responsavel,
                          'qualificacao': qualificacao or None,
                          'data_entrada': data_entrada.isoformat() if data_entrada else None,
                          'capital_valor': None if capital_valor is None else str(capital_valor),
                          'pro_labore': None if pro_labore is None else str(pro_labore),
                          **rotulo_empresa(cliente_id)})
        total_final = SocioCliente.get_total_percentual(cliente_id)
        if total_final == Decimal('100'):
            flash('Sócio adicionado com sucesso! Total de participação atingiu 100%.', 'success')
        else:
            flash(f'Sócio adicionado. Total atual de participação: {total_final:.2f}%.', 'warning')
    else:
        flash('Erro ao adicionar sócio!', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/socios/<int:id>/saida', methods=['POST'])
@login_required
def saida_socio(id):
    """Sócio saiu da sociedade: grava a data de saída e ele vira ex-sócio.
    Nada se apaga — o histórico de quem já foi sócio fica na aba."""
    socio = SocioCliente.get_by_id(id)
    if not socio:
        flash('Sócio não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))
    cliente_id = socio['cliente_id']
    if not socio.get('ativo'):
        flash('Este sócio já tem saída registrada.', 'warning')
        return redirect(url_for('clientes.detalhes', id=cliente_id))
    data_saida = _data_iso(request.form.get('data_saida'))
    if not data_saida:
        flash('Informe a data de saída (não pode ser no futuro).', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))
    if socio.get('data_entrada') and data_saida < socio['data_entrada']:
        flash('A data de saída não pode ser antes da data de entrada '
              f'({socio["data_entrada"].strftime("%d/%m/%Y")}).', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))
    if SocioCliente.registrar_saida(id, data_saida):
        registrar('escrita.saida_socio', 'cadastros', tabela='socios_clientes', registro_id=id,
                  antes={'cliente_id': cliente_id, 'nome': socio.get('nome'), 'cpf': socio.get('cpf'),
                         **rotulo_empresa(cliente_id)},
                  depois={'cliente_id': cliente_id, 'data_saida': data_saida.isoformat()})
        total_final = SocioCliente.get_total_percentual(cliente_id)
        msg = (f'Saída de {socio.get("nome")} registrada em {data_saida.strftime("%d/%m/%Y")}; '
               'o nome passa para Ex-sócios.')
        if total_final != Decimal('100'):
            msg += (f' Os sócios atuais somam {total_final:.2f}%: ajuste a participação '
                    'no Editar para fechar 100%.')
        flash(msg, 'success')
    else:
        flash('Erro ao registrar a saída!', 'danger')
    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/socios/<int:id>/excluir', methods=['POST'])
@login_required
def excluir_socio(id):
    """Apaga de vez — só para sócio lançado POR ENGANO. Quem saiu da
    sociedade usa saida_socio (fica no histórico)."""
    socio = SocioCliente.get_by_id(id)
    if not socio:
        flash('Sócio não encontrado!', 'danger')
        return redirect(url_for('clientes.index'))

    cliente_id = socio['cliente_id']
    sucesso = SocioCliente.delete(id)

    if sucesso:
        registrar('escrita.excluiu_socio', 'cadastros', tabela='socios_clientes',
                  registro_id=id,
                  antes={'cliente_id': cliente_id, 'nome': socio.get('nome'),
                         'cpf': socio.get('cpf'), **rotulo_empresa(cliente_id)})
        total_final = SocioCliente.get_total_percentual(cliente_id)
        flash(f'Lançamento apagado. Total atual de participação: {total_final:.2f}%.', 'success')
    else:
        flash('Erro ao excluir sócio!', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/clientes/<int:cliente_id>/contadores/vincular', methods=['POST'])
@login_required
def vincular_contador(cliente_id):
    """Liga um contador (cadastro is_contador=1) a este cliente."""
    r = ClienteContador.vincular_contador(
        cliente_id,
        request.form.get('contador_id'),
        request.form.get('finalidade'),
    )
    if r.get('ok'):
        flash(f'Contador {r.get("contador", "")} vinculado com sucesso!', 'success')
    else:
        flash(r.get('erro') or 'Falha ao vincular o contador.', 'danger')
    return redirect(url_for('clientes.detalhes', id=cliente_id) + '#certificado')


@clientes.route('/clientes/<int:cliente_id>/contadores/<int:contador_id>/desvincular',
                methods=['POST'])
@login_required
def desvincular_contador(cliente_id, contador_id):
    """Remove o vínculo cliente <-> contador."""
    r = ClienteContador.desvincular_contador(cliente_id, contador_id)
    flash('Contador desvinculado.' if r.get('ok')
          else (r.get('erro') or 'Falha ao desvincular.'),
          'success' if r.get('ok') else 'danger')
    return redirect(url_for('clientes.detalhes', id=cliente_id) + '#certificado')


@clientes.route('/clientes/api/grupo-rapido', methods=['POST'])
@login_required
def grupo_rapido():
    """"Novo grupo" no formulário do cliente (07/10/2026): cria sem sair da
    tela. Nome igual a um grupo que já existe (sem diferenciar maiúscula)
    devolve o existente — e o reativa se estava inativo — em vez de duplicar."""
    nome = re.sub(r'\s+', ' ', ((request.get_json(silent=True) or {}).get('nome') or '')).strip().upper()
    if not nome:
        return jsonify(success=False, message='Digite o nome do grupo.'), 400
    if len(nome) > 100:
        return jsonify(success=False, message='Nome muito longo (máximo 100 letras).'), 400
    existe = execute_query("SELECT id, nome, descricao, situacao FROM grupos_clientes "
                           "WHERE UPPER(TRIM(nome)) = %s LIMIT 1", (nome,), fetch=True, fetch_one=True)
    if existe:
        if existe.get('situacao') != 'ATIVO':
            GrupoCliente.update(existe['id'], existe['nome'], existe.get('descricao'), 'ATIVO')
        return jsonify(success=True, id=existe['id'], nome=existe['nome'], ja_existia=True)
    gid = GrupoCliente.create(nome, None, 'ATIVO')
    if not gid:
        return jsonify(success=False, message='Não consegui criar o grupo agora. Tente de novo.'), 500
    registrar('escrita.criou_grupo', 'cadastros', tabela='grupos_clientes', registro_id=gid,
              depois={'nome': nome, 'situacao': 'ATIVO', 'origem': 'formulario_cliente'})
    return jsonify(success=True, id=gid, nome=nome, ja_existia=False)


@clientes.route('/clientes/api/ramo-rapido', methods=['POST'])
@login_required
def ramo_rapido():
    """"Novo ramo" no formulário do cliente (07/10/2026), como o grupo rápido.
    O nome fica como foi digitado (os ramos usam maiúscula e minúscula);
    nome igual, sem diferenciar maiúscula, devolve o ramo que já existe."""
    dados = request.get_json(silent=True) or {}
    nome = re.sub(r'\s+', ' ', (dados.get('nome') or '')).strip()
    tipo = 'PF' if dados.get('tipo_pessoa') == 'PF' else 'PJ'   # ramo nasce no tipo da tela (10/10/2026)
    # Ramo de empresa nasce dentro do segmento em que foi pedido (10/10/2026).
    seg = None if tipo == 'PF' else (dados.get('segmento') or '').upper()
    if not nome:
        return jsonify(success=False, message='Digite o nome do ramo.'), 400
    if tipo == 'PJ' and seg not in SEGMENTO_NOME:
        return jsonify(success=False, message='Escolha primeiro o segmento (Comércio, Serviços, Locação ou Indústria).'), 400
    if len(nome) > 100:
        return jsonify(success=False, message='Nome muito longo (máximo 100 letras).'), 400
    existe = execute_query("SELECT id, nome, tipo_pessoa, segmento, descricao, situacao FROM ramos_atividade "
                           "WHERE UPPER(TRIM(nome)) = %s LIMIT 1", (nome.upper(),), fetch=True, fetch_one=True)
    if existe:
        if existe.get('situacao') != 'ATIVO':
            RamoAtividade.update(existe['id'], existe['nome'], existe.get('descricao'), 'ATIVO')
        if existe.get('tipo_pessoa') != tipo:
            outro = 'pessoa física' if existe.get('tipo_pessoa') == 'PF' else 'empresa'
            return jsonify(success=False, message=f'"{existe["nome"]}" já existe como ramo de {outro}. '
                                                  'Escolha outro nome ou peça para mudar na tela Ramo de Atividade.'), 400
        if tipo == 'PJ' and existe.get('segmento') != seg:
            onde = SEGMENTO_NOME.get(existe.get('segmento'), 'sem segmento')
            return jsonify(success=False, message=f'"{existe["nome"]}" já existe em {onde}. '
                                                  f'Marque o segmento {onde} para escolher esse ramo.'), 400
        return jsonify(success=True, id=existe['id'], nome=existe['nome'], tipo_pessoa=tipo,
                       segmento=existe.get('segmento'), ja_existia=True)
    rid = RamoAtividade.create(nome, None, 'ATIVO', tipo, seg)
    if not rid:
        return jsonify(success=False, message='Não consegui criar o ramo agora. Tente de novo.'), 500
    registrar('escrita.criou_ramo', 'cadastros', tabela='ramos_atividade', registro_id=rid,
              depois={'nome': nome, 'tipo_pessoa': tipo, 'segmento': seg, 'situacao': 'ATIVO',
                      'origem': 'formulario_cliente'})
    return jsonify(success=True, id=rid, nome=nome, tipo_pessoa=tipo, segmento=seg, ja_existia=False)


@clientes.route('/clientes/api/cadastros/buscar')
@login_required
def cadastros_buscar():
    """Busca de cadastro para virar SÓCIO (07/10/2026): PF ou PJ, cliente ou
    avulso, não inativo. Acha por nome, CPF/CNPJ ou número do cadastro."""
    q = (request.args.get('q') or '').strip()
    excluir = request.args.get('excluir', type=int) or 0
    if len(q) < 2:
        return jsonify([])
    dig = _so_digitos(q)
    rows = execute_query(
        """SELECT id FROM clientes
            WHERE COALESCE(situacao, 'ATIVO') <> 'INATIVO' AND id <> %s
              AND (nome_razao_social LIKE %s
                   OR (%s <> '' AND REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '-', ''), '/', '') LIKE %s)
                   OR numero_cliente = %s)
            ORDER BY (numero_cliente = %s) DESC, nome_razao_social
            LIMIT 12""",
        (excluir, f'%{q}%', dig, f'%{dig}%', q, q), fetch=True) or []
    resumo = _cadastro_resumo([r['id'] for r in filtrar_lista(rows)])
    return jsonify([resumo[r['id']] for r in rows if r['id'] in resumo])


@clientes.route('/clientes/api/socios/raiz')
@login_required
def socios_da_raiz():
    """Quadro de sócios da raiz do CNPJ digitado (cadastro novo ou CNPJ
    trocado na edição). travado = filial cuja raiz já tem quadro em outro
    cadastro; copiar = matriz nova, que abre com o quadro das filiais."""
    cnpj = request.args.get('cnpj', '')
    excluir = request.args.get('excluir', type=int)
    d = _cnpj_digitos(cnpj)
    if not d:
        return jsonify({'ok': False})
    origem, soc = _quadro_da_raiz(d, excluir)
    membros = [_origem_json(m) for m in _raiz_membros(d, excluir)]
    out = {'ok': True, 'filial': d[8:12] != '0001', 'membros': membros,
           'origem': _origem_json(origem) if origem else None, 'socios': []}
    out['travado'] = bool(origem) and out['filial']
    if origem and out['origem']['visivel']:
        out['socios'] = _socios_json(soc, {} if out['filial'] else None)
        if not out['filial']:                     # matriz nova: pró-labore é dela, começa vazio
            for x in out['socios']:
                x['pl'] = ''
    return jsonify(out)


@clientes.route('/clientes/api/socios/receita', methods=['POST'])
@login_required
def socios_da_receita():
    """Casa o quadro de sócios (QSA) que a Receita devolve na busca do CNPJ
    com os cadastros. Empresa sócia: pela raiz do CNPJ (8 dígitos). Pessoa
    física: a Receita esconde o CPF (***116048**) — casa só quando os 6
    dígitos do meio E o nome batem; na dúvida, não casa (vira "cadastrar")."""
    qsa = (request.get_json(silent=True) or {}).get('qsa') or []
    out = []
    for s in qsa[:30]:
        if not isinstance(s, dict):
            continue
        nome = (s.get('nome_socio') or s.get('nome') or '').strip()
        doc = s.get('cnpj_cpf_do_socio') or ''
        dig = _so_digitos(doc)
        eh_pj = '*' not in doc and len(dig) >= 8
        achado = None
        if eh_pj:
            r = execute_query(
                """SELECT id FROM clientes
                    WHERE tipo_pessoa = 'PJ' AND COALESCE(situacao, 'ATIVO') <> 'INATIVO'
                      AND REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '-', ''), '/', '') LIKE %s
                    ORDER BY (REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '-', ''), '/', '') = %s) DESC,
                             (SUBSTRING(REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '-', ''), '/', ''), 9, 4) = '0001') DESC
                    LIMIT 1""", (dig[:8] + '%', dig), fetch=True, fetch_one=True)
            achado = r['id'] if r else None
        elif len(dig) == 6:
            for r in execute_query(
                    """SELECT id, nome_razao_social FROM clientes
                        WHERE tipo_pessoa = 'PF' AND COALESCE(situacao, 'ATIVO') <> 'INATIVO'
                          AND SUBSTRING(REPLACE(REPLACE(cpf_cnpj, '.', ''), '-', ''), 4, 6) = %s""",
                    (dig,), fetch=True) or []:
                if _nome_chave(r['nome_razao_social']) == _nome_chave(nome):
                    achado = r['id']
                    break
        elif not dig and nome:
            # A reserva (ReceitaWS) manda o QSA SEM documento, só nome e
            # qualificação (14/10/2026: sócios 5010/5011 já cadastrados apareciam
            # como "não está cadastrado"). Casa pelo nome — PF ou PJ — só quando
            # UM cadastro tem esse nome; homônimo fica para escolher à mão.
            chave = _nome_chave(nome)
            iguais = [r for r in execute_query(
                """SELECT id, tipo_pessoa, nome_razao_social FROM clientes
                    WHERE COALESCE(situacao, 'ATIVO') <> 'INATIVO' AND nome_razao_social LIKE %s""",
                (nome.split()[0] + '%',), fetch=True) or []
                if _nome_chave(r['nome_razao_social']) == chave]
            if len(iguais) == 1:
                achado = iguais[0]['id']
                eh_pj = iguais[0]['tipo_pessoa'] == 'PJ'
        if achado and achado in empresas_ocultas():
            achado = None
        cad = _cadastro_resumo([achado]).get(achado) if achado else None
        entrada = _data_iso(s.get('data_entrada_sociedade'))
        out.append({'nome': nome, 'qualificacao': _qualificacao(s.get('qualificacao_socio') or s.get('qual')),
                    'entrada': entrada.isoformat() if entrada else '',
                    'doc': doc, 'tipo': 'PJ' if eh_pj else 'PF', 'cadastro': cad})
    return jsonify(out)


def _acad_contagens():
    """Números das três abas de A CADASTRAR (para a barra de abas)."""
    from utils import a_cadastrar as AC
    from utils import socios_fila as SF
    oc = empresas_ocultas()
    socios = {r['chave'] for r in SF.linhas_abertas() if r['status'] == 'pendente' and r['cliente_id'] not in oc}
    return {'empresas': len(AC.empresas_pendentes(oc)), 'socios': len(socios), 'avulsos': len(AC.avulsos_pendentes(oc))}


@clientes.route('/clientes/a-cadastrar')
@login_required
def a_cadastrar():
    """A CADASTRAR (05/11/2026) — aba EMPRESA PENDENTE: o que falta em cada empresa, ao vivo."""
    from utils import a_cadastrar as AC
    lista = AC.empresas_pendentes(empresas_ocultas())
    return render_template('clientes/a_cadastrar.html', acad_aba='empresas', acad_n=_acad_contagens(), lista=lista,
                           catalogo=AC.PENDENCIAS_EMPRESA, contagem=AC.contadores(lista, AC.PENDENCIAS_EMPRESA), alvo=AC.ALVO)


@clientes.route('/clientes/a-cadastrar/avulsos')
@login_required
def a_cadastrar_avulsos():
    """A CADASTRAR — aba AVULSOS PENDENTES: todo avulso e o que falta para virar cliente."""
    from utils import a_cadastrar as AC
    lista = AC.avulsos_pendentes(empresas_ocultas())
    return render_template('clientes/a_cadastrar.html', acad_aba='avulsos', acad_n=_acad_contagens(), lista=lista,
                           catalogo=AC.PENDENCIAS_AVULSO, contagem=AC.contadores(lista, AC.PENDENCIAS_AVULSO), alvo=AC.ALVO)


@clientes.route('/clientes/socios-pendentes')
@login_required
def socios_fila():
    """Fila "Sócios a cadastrar": o quadro de sócios da Receita das empresas
    sem sócio, agrupado pela PESSOA (ver utils/socios_fila.py)."""
    from utils import socios_fila as SF
    rows = [r for r in SF.linhas_abertas() if r['cliente_id'] not in empresas_ocultas()]
    achar = SF.mapa_cadastros()
    pessoas = {}
    for r in rows:
        # sócio numa empresa e só administrador em outra: dois cartões
        k = (r['chave'], r['status'])
        p = pessoas.get(k)
        if not p:
            p = pessoas[k] = {
                'chave': r['chave'], 'tipo': r['tipo'], 'nome': r['nome'],
                'doc': SF.doc_mascarado(r['tipo'], r['doc']), 'status': r['status'],
                'empresas': []}
        p['empresas'].append({'id': r['cliente_id'], 'numero': r['numero_cliente'] or '',
                              'nome': (r['empresa'] or '').strip(), 'avulso': bool(r['avulso']),
                              'qual': r['qualificacao'] or '',
                              'entrada': r['data_entrada'].strftime('%d/%m/%Y') if r['data_entrada'] else ''})
    ids_cad = {}
    for p in pessoas.values():
        cid = achar(p['chave']) if p['status'] == 'pendente' else None
        if cid and all(e['id'] != cid for e in p['empresas']):
            ids_cad[p['chave']] = cid
    resumo = _cadastro_resumo(list(set(ids_cad.values())))
    for p in pessoas.values():
        p['cadastro'] = resumo.get(ids_cad.get(p['chave']))
        p['empresas'].sort(key=lambda e: e['nome'])
    lista = sorted(pessoas.values(), key=lambda p: (p['status'] != 'pendente', not p['cadastro'],
                                                    -len(p['empresas']), _nome_chave(p['nome'])))
    pend = [p for p in lista if p['status'] == 'pendente']
    stats = {'pessoas': sum(1 for p in pend if p['tipo'] == 'PF'),
             'empresas_socias': sum(1 for p in pend if p['tipo'] == 'PJ'),
             'empresas': len({e['id'] for p in pend for e in p['empresas']}),
             'prontos': sum(1 for p in pend if p['cadastro']),
             'administradores': sum(1 for p in lista if p['status'] == 'administrador'),
             'vinculados': (execute_query("SELECT COUNT(DISTINCT chave) n FROM socios_fila WHERE status = 'vinculado'",
                                          fetch=True, fetch_one=True) or {}).get('n', 0)}
    return render_template('clientes/socios_fila.html', pessoas=lista, stats=stats, acad_aba='socios', acad_n=_acad_contagens())


@clientes.route('/clientes/socios-pendentes/vincular', methods=['POST'])
@login_required
def socios_fila_vincular():
    """Liga um cadastro (que já existe ou acabou de ser criado na janela) a
    todas as empresas desta pessoa na fila."""
    from utils import socios_fila as SF
    dados = request.get_json(silent=True) or {}
    chave = str(dados.get('chave') or '')[:120]
    cad_id = dados.get('cadastro_id')
    if not chave or not str(cad_id).isdigit():
        return jsonify(ok=False, erro='Escolha o cadastro da pessoa.'), 400
    cad = Cliente.get_by_id(int(cad_id))
    if not cad or (cad.get('situacao') or 'ATIVO') == 'INATIVO' or cad['id'] in empresas_ocultas():
        return jsonify(ok=False, erro='Cadastro não encontrado.'), 404
    ok, motivo = SF.confere(chave, cad)
    if not ok:
        return jsonify(ok=False, erro='Não liguei: ' + motivo), 400
    feitos = SF.vincular(chave, cad, current_user.id, registrar=registrar, rotulo_empresa=rotulo_empresa,
                         ocultas=empresas_ocultas())
    n100 = sum(1 for _, _, pct in feitos if pct == Decimal('100'))
    return jsonify(ok=True, empresas=len(feitos), com_100=n100, sem_pct=len(feitos) - n100,
                   cadastro=_cadastro_resumo([cad['id']]).get(cad['id']))


@clientes.route('/clientes/socios-pendentes/ignorar', methods=['POST'])
@login_required
def socios_fila_ignorar():
    """Tira a pessoa da fila (homônimo, já saiu da empresa, não é sócio...)."""
    from utils import socios_fila as SF
    chave = str((request.get_json(silent=True) or {}).get('chave') or '')[:120]
    if not chave:
        return jsonify(ok=False), 400
    SF.ignorar(chave, current_user.id, ocultas=empresas_ocultas())
    registrar('escrita.ignorou_socio_fila', 'cadastros', tabela='socios_fila', depois={'chave': chave})
    return jsonify(ok=True)


@clientes.route('/clientes/api/proximo-numero-pf')
@login_required
def proximo_numero_pf():
    return jsonify(numero=_proximo_numero_pf())


@clientes.route('/clientes/<int:cliente_id>/adicionar-grupo', methods=['POST'])
@login_required
def adicionar_grupo(cliente_id):
    """Adicionar cliente a um grupo"""
    try:
        grupo_id = request.form.get('grupo_id', type=int)
        
        if not grupo_id:
            flash('Grupo não selecionado!', 'danger')
            return redirect(url_for('clientes.detalhes', id=cliente_id))
        
        # Verificar se cliente existe
        cliente = Cliente.get_by_id(cliente_id)
        if not cliente:
            flash('Cliente não encontrado!', 'danger')
            return redirect(url_for('clientes.index'))
        
        # Adicionar cliente ao grupo
        sucesso = GrupoCliente.add_cliente(grupo_id, cliente_id)
        
        if sucesso:
            flash('Cliente adicionado ao grupo com sucesso!', 'success')
        else:
            flash('Erro ao adicionar cliente ao grupo!', 'danger')
    
    except Exception as e:
        flash(f'Erro ao adicionar cliente ao grupo: {str(e)}', 'danger')
    
    return redirect(url_for('clientes.detalhes', id=cliente_id))


@clientes.route('/clientes/<int:cliente_id>/remover-grupo/<int:grupo_id>', methods=['POST'])
@login_required
def remover_grupo(cliente_id, grupo_id):
    """Remover cliente de um grupo"""
    try:
        # Verificar se cliente existe
        cliente = Cliente.get_by_id(cliente_id)
        if not cliente:
            flash('Cliente não encontrado!', 'danger')
            return redirect(url_for('clientes.index'))
        
        # Remover cliente do grupo
        sucesso = GrupoCliente.remove_cliente(grupo_id, cliente_id)
        
        if sucesso is not None:
            flash('Cliente removido do grupo com sucesso!', 'success')
        else:
            flash('Erro ao remover cliente do grupo!', 'danger')
    
    except Exception as e:
        flash(f'Erro ao remover cliente do grupo: {str(e)}', 'danger')
    
    return redirect(url_for('clientes.detalhes', id=cliente_id))


# API para busca de CEP
@clientes.route('/api/cep/<cep>')
@login_required
def buscar_cep(cep):
    """Endereço do CEP no formato do ViaCEP — três fontes (utils/cep.py), para
    o ViaCEP fora do ar no Railway não virar "CEP não encontrado"."""
    from utils import cep as CEP
    try:
        dados = CEP.consultar(cep)
    except CEP.CepIndisponivel:
        return jsonify({'erro': True, 'indisponivel': True}), 503
    if not dados:
        return jsonify({'erro': True}), 404
    return jsonify(dados)


@clientes.route('/api/consultar-cnpj/<cnpj>')
@login_required
def consultar_cnpj(cnpj):
    """Consulta o CNPJ na base pública da Receita (utils.cnpj_receita: BrasilAPI
    e, se ela cair, ReceitaWS) e devolve os campos para o formulário.

    ``base_em`` é a DATA da foto que as fontes servem (a Receita publica a
    base uma vez por mês): a tela mostra, para ninguém tomar a réplica por
    dado do dia — foi o que aconteceu em 22/09/2026 com um CNPJ que mudou
    depois de 15/09. ``vigia`` é o estado da vigia deste CNPJ, se houver."""
    from utils import cnpj_receita as RF
    from utils import cnpj_vigia as VG
    cnpj_limpo = RF.so_digitos(cnpj)
    if len(cnpj_limpo) != 14:
        return jsonify({'success': False, 'message': 'CNPJ deve ter 14 dígitos'}), 400
    try:
        dados, fonte = RF.consultar(cnpj_limpo, completar=True)
    except RF.CnpjNaoEncontrado:
        return jsonify({'success': False, 'message': 'CNPJ não encontrado na Receita Federal'}), 404
    except RF.FonteIndisponivel as e:
        logger.warning('[cnpj] fontes indisponíveis para %s: %s', cnpj_limpo, e)
        return jsonify({'success': False,
                        'message': 'As fontes da Receita não responderam agora. Tente de novo em instantes.'}), 503
    return jsonify({'success': True, 'data': dados, 'fonte': fonte,
                    'base_em': RF.base_em_br(cnpj_limpo),
                    'vigia': VG.por_cnpj(cnpj_limpo)}), 200


# ---------------------------------------------------------------------------
# Vigia de CNPJ (22/09/2026) — consulta a Receita a cada 24 h até mudar.
# Ver utils/cnpj_vigia.py. Quem roda a consulta diária é o cron_manutencao.
# ---------------------------------------------------------------------------
@clientes.route('/api/cnpj-vigia/<cnpj>')
@login_required
def cnpj_vigia_status(cnpj):
    from utils import cnpj_vigia as VG
    return jsonify({'success': True, 'vigia': VG.por_cnpj(cnpj)})


@clientes.route('/api/cnpj-vigia/<cnpj>/armar', methods=['POST'])
@login_required
def cnpj_vigia_armar(cnpj):
    from utils import cnpj_receita as RF
    from utils import cnpj_vigia as VG
    corpo = request.get_json(silent=True) or {}
    cid = corpo.get('cliente_id')
    cid = int(cid) if str(cid or '').isdigit() else None
    try:
        vigia, dados, fonte = VG.armar(cnpj, cid, getattr(current_user, 'id', None),
                                       getattr(current_user, 'nome', None) or getattr(current_user, 'login', ''))
    except RF.CnpjNaoEncontrado:
        return jsonify({'success': False, 'message': 'CNPJ não encontrado na Receita Federal'}), 404
    except RF.FonteIndisponivel:
        return jsonify({'success': False, 'message': 'As fontes da Receita não responderam agora; a vigia '
                                                     'precisa de uma primeira consulta para ter a foto de referência.'}), 503
    registrar('escrita.armou_vigia_cnpj', 'cadastros', tabela='cnpj_vigia', registro_id=cid,
              depois={'cnpj': RF.so_digitos(cnpj), 'razao_social': dados.get('razao_social'), 'fonte': fonte})
    return jsonify({'success': True, 'vigia': vigia, 'base_em': RF.base_em_br()})


def _vigia_concluir_se_aplicada(cpf_cnpj, cliente_id):
    """15/10/2026: "Aplicar no formulário" marcava a vigia como aplicada no
    CLIQUE; a pessoa não salvou e a mudança da Expansão (583) sumiu da tela
    sem chegar ao cadastro. Agora o clique só preenche e marca o campo
    oculto ``vigia_aplicada``; a vigia conclui aqui, no cadastro SALVO."""
    if not request.form.get('vigia_aplicada'):
        return
    from utils import cnpj_vigia as VG
    try:
        v = VG.por_cnpj(cpf_cnpj)
        if v and v.get('status') == 'mudou':
            VG.mudar_status(cpf_cnpj, 'aplicada')
            registrar('escrita.vigia_cnpj_aplicada', 'cadastros', tabela='cnpj_vigia',
                      registro_id=cliente_id, depois={'cnpj': v['cnpj'], 'ao_salvar': True})
    except Exception:
        logger.exception('[vigia] não concluiu a vigia de %s', cpf_cnpj)


@clientes.route('/api/cnpj-vigia/<cnpj>/<acao>', methods=['POST'])
@login_required
def cnpj_vigia_acao(cnpj, acao):
    """cancelar (parar de vigiar) | aplicada (a pessoa levou os dados novos ao
    cadastro) | ignorar (viu a mudança e não quer aplicar). As três tiram a
    vigia da lista de novidades."""
    from utils import cnpj_receita as RF
    from utils import cnpj_vigia as VG
    status = {'cancelar': 'cancelada', 'aplicada': 'aplicada', 'ignorar': 'cancelada'}.get(acao)
    if not status:
        return jsonify({'success': False, 'message': 'ação desconhecida'}), 400
    vigia = VG.mudar_status(cnpj, status)
    registrar('escrita.vigia_cnpj_' + acao, 'cadastros', tabela='cnpj_vigia',
              registro_id=(vigia or {}).get('cliente_id'), depois={'cnpj': RF.so_digitos(cnpj)})
    return jsonify({'success': True, 'vigia': vigia})


# ---------------------------------------------------------------------------
# Rotas ANP
# ---------------------------------------------------------------------------

_MAX_ANP_PDF_MB = 20
_MAX_ANP_PDF_BYTES = _MAX_ANP_PDF_MB * 1024 * 1024


@clientes.route('/clientes/<int:cliente_id>/anp/importar-pdf', methods=['POST'])
@login_required
def anp_importar_pdf(cliente_id):
    """Recebe PDF da ANP, extrai dados e exibe formulário de confirmação."""
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))

    arquivo = request.files.get('arquivo_pdf')
    if not arquivo or not arquivo.filename:
        flash('Selecione um arquivo PDF.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    if not arquivo.filename.lower().endswith('.pdf'):
        flash('Apenas arquivos .pdf são aceitos.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    pdf_bytes = arquivo.read()
    if len(pdf_bytes) > _MAX_ANP_PDF_BYTES:
        flash(f'O arquivo excede o tamanho máximo de {_MAX_ANP_PDF_MB} MB.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    from utils.anp_parser import extrair_dados_anp
    resultado = extrair_dados_anp(pdf_bytes)

    if not resultado['sucesso']:
        flash(f'Erro ao processar PDF: {resultado["erro"]}', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    # Verifica se o CNPJ do PDF bate com o do cliente
    cnpj_pdf = resultado['cnpj']
    cnpj_cliente = ''.join(c for c in (cliente.get('cpf_cnpj') or '') if c.isdigit())
    cnpj_ok = (cnpj_pdf == cnpj_cliente) if cnpj_pdf and cnpj_cliente else None

    return render_template(
        'clientes/anp_preview.html',
        cliente=cliente,
        dados=resultado['dados'],
        socios=resultado['socios'],
        produtos=resultado['produtos'],
        texto_bruto=resultado['texto_bruto'],
        cnpj_ok=cnpj_ok,
        cnpj_pdf=cnpj_pdf,
    )


@clientes.route('/clientes/<int:cliente_id>/anp/salvar', methods=['POST'])
@login_required
def anp_salvar(cliente_id):
    """Salva (cria ou atualiza) cadastro ANP a partir do formulário."""
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))

    anp_id_raw = request.form.get('anp_id')
    anp_id = int(anp_id_raw) if anp_id_raw and anp_id_raw.isdigit() else None

    data = {
        'situacao': request.form.get('situacao', '').strip() or None,
        'autorizacao': request.form.get('autorizacao', '').strip() or None,
        'cnpj_anp': request.form.get('cnpj_anp', '').strip() or None,
        'razao_social': request.form.get('razao_social', '').strip() or None,
        'nome_fantasia': request.form.get('nome_fantasia', '').strip() or None,
        'endereco': request.form.get('endereco', '').strip() or None,
        'complemento': request.form.get('complemento', '').strip() or None,
        'bairro': request.form.get('bairro', '').strip() or None,
        'municipio_uf': request.form.get('municipio_uf', '').strip() or None,
        'cep': request.form.get('cep', '').strip() or None,
        'nr_despacho': request.form.get('nr_despacho', '').strip() or None,
        'data_publicacao': request.form.get('data_publicacao') or None,
        'bandeira': request.form.get('bandeira', '').strip() or None,
        'data_inicio_bandeira': request.form.get('data_inicio_bandeira') or None,
        'tipo_posto': request.form.get('tipo_posto', '').strip() or None,
        'pmqc': request.form.get('pmqc', '').strip() or None,
        'delivery': request.form.get('delivery', '').strip() or None,
        'latitude': request.form.get('latitude', '').strip() or None,
        'longitude': request.form.get('longitude', '').strip() or None,
        'data_emissao': request.form.get('data_emissao', '').strip() or None,
        'fonte': request.form.get('fonte', 'MANUAL'),
    }

    # Sócios: cada linha não-vazia
    socios_raw = request.form.get('socios_json', '')
    import json
    try:
        socios = [s for s in json.loads(socios_raw) if s.strip()] if socios_raw else []
    except (json.JSONDecodeError, TypeError):
        socios = []

    # Produtos
    produtos_json = request.form.get('produtos_json', '')
    try:
        produtos = json.loads(produtos_json) if produtos_json else []
    except (json.JSONDecodeError, TypeError):
        produtos = []

    saved_id = CadastroAnp.save_full(
        cliente_id=cliente_id,
        data=data,
        socios=socios,
        produtos=produtos,
        anp_id=anp_id,
    )

    if saved_id:
        flash('Cadastro ANP salvo com sucesso!', 'success')
    else:
        flash('Erro ao salvar cadastro ANP.', 'danger')

    return redirect(url_for('clientes.detalhes', id=cliente_id) + '#cadastros-adicionais')


@clientes.route('/clientes/<int:cliente_id>/anp/<int:anp_id>')
@login_required
def anp_detalhe(cliente_id, anp_id):
    """Exibe detalhes completos de um cadastro ANP."""
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))

    anp = CadastroAnp.get_by_id(anp_id)
    if not anp or anp['cliente_id'] != cliente_id:
        flash('Cadastro ANP não encontrado.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    socios = CadastroAnp.get_socios(anp_id)
    produtos = CadastroAnp.get_produtos(anp_id)

    return render_template(
        'clientes/anp_detalhe.html',
        cliente=cliente,
        anp=anp,
        socios=socios,
        produtos=produtos,
    )


@clientes.route('/clientes/<int:cliente_id>/anp/<int:anp_id>/editar', methods=['GET'])
@login_required
def anp_editar(cliente_id, anp_id):
    """Formulário de edição de cadastro ANP (pré-preenchido)."""
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))

    anp = CadastroAnp.get_by_id(anp_id)
    if not anp or anp['cliente_id'] != cliente_id:
        flash('Cadastro ANP não encontrado.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    socios = [s['nome'] for s in CadastroAnp.get_socios(anp_id)]
    produtos = CadastroAnp.get_produtos(anp_id)

    return render_template(
        'clientes/anp_form.html',
        cliente=cliente,
        anp=anp,
        socios=socios,
        produtos=produtos,
        modo='editar',
    )


@clientes.route('/clientes/<int:cliente_id>/anp/<int:anp_id>/excluir', methods=['POST'])
@login_required
def anp_excluir(cliente_id, anp_id):
    """Exclui cadastro ANP."""
    anp = CadastroAnp.get_by_id(anp_id)
    if not anp or anp['cliente_id'] != cliente_id:
        flash('Cadastro ANP não encontrado.', 'danger')
        return redirect(url_for('clientes.detalhes', id=cliente_id))

    CadastroAnp.delete(anp_id)
    flash('Cadastro ANP excluído com sucesso.', 'success')
    return redirect(url_for('clientes.detalhes', id=cliente_id) + '#cadastros-adicionais')


@clientes.route('/clientes/<int:cliente_id>/anp/novo', methods=['GET'])
@login_required
def anp_novo(cliente_id):
    """Formulário de cadastro ANP manual (em branco)."""
    cliente = Cliente.get_by_id(cliente_id)
    if not cliente:
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('clientes.index'))

    return render_template(
        'clientes/anp_form.html',
        cliente=cliente,
        anp=None,
        socios=[],
        produtos=[],
        modo='novo',
    )


# Aliases para manter compatibilidade
list_clientes = index
create_cliente = novo
view_cliente = detalhes
edit_cliente = editar
create = novo
view = detalhes
edit = editar


# ===========================================================================
# D3/D4 — Histórico de auditoria. Tradução (rótulos/verbos/empresa) e o
# transform linha->cartão vivem em utils/auditoria_fmt.py (compartilhado com
# a tela geral de Auditoria). Aqui só a rota da aba da ficha do cliente.
# ===========================================================================


@clientes.route('/clientes/<int:id>/historico')
@login_required
def historico(id):
    """Partial (AJAX) da aba Histórico: alterações (padrão) ou consultas (admin)."""
    view = 'consultas' if request.args.get('view') == 'consultas' else 'alteracoes'
    # GUARDA NO SERVIDOR: consultas só ADMIN (esconder no front não é trava).
    if view == 'consultas' and not current_user.is_admin():
        return ('<div class="hist-vazio">Apenas administradores podem ver as consultas.</div>'), 403

    try:
        page = max(1, int(request.args.get('page', 1) or 1))
    except (TypeError, ValueError):
        page = 1
    per = 30
    off = (page - 1) * per
    sid = str(id)

    if view == 'alteracoes':
        # Cadastro inteiro: clientes + certificado (registro_id = cliente_id) e as
        # filhas endereços/sócios/contratos (cliente_id vive no JSON do log).
        cond = ("acao LIKE 'escrita.%%' AND ("
                "(tabela_afetada IN ('clientes','dfe_certificados') AND registro_id = %s) "
                "OR (tabela_afetada IN ('enderecos_clientes','socios_clientes','contratos','contatos_clientes') AND "
                "(JSON_UNQUOTE(JSON_EXTRACT(dados_novos,'$.cliente_id')) = %s "
                "OR JSON_UNQUOTE(JSON_EXTRACT(dados_anteriores,'$.cliente_id')) = %s "
                "OR JSON_UNQUOTE(JSON_EXTRACT(dados_novos,'$.excluido.cliente_id')) = %s)))")
        params = [id, sid, sid, sid]
    else:
        # Consultas: abrir ficha (registro_id) + buscas/exportações desta empresa
        # (cliente_id no filtro) + captura SEFAZ (cliente_id no corpo).
        cond = ("acao LIKE 'leitura.%%' AND ("
                "(tabela_afetada = 'clientes' AND registro_id = %s) "
                "OR JSON_UNQUOTE(JSON_EXTRACT(dados_novos,'$.filtros.cliente_id')) = %s "
                "OR JSON_UNQUOTE(JSON_EXTRACT(dados_novos,'$.cliente_id')) = %s)")
        params = [id, sid, sid]

    total = (execute_query(f"SELECT COUNT(*) AS c FROM logs_sistema WHERE {cond}",
                           tuple(params), fetch=True, fetch_one=True) or {}).get('c', 0)
    rows = execute_query(
        f"SELECT id, data_hora, usuario_id, usuario_nome, acao, tabela_afetada, registro_id, "
        f"dados_anteriores, dados_novos FROM logs_sistema WHERE {cond} "
        f"ORDER BY data_hora DESC, id DESC LIMIT %s OFFSET %s",
        tuple(params + [per, off]), fetch=True) or []

    # Autor: usa usuario_nome (D2). Linha antiga sem nome → join por id; senão removido.
    faltam = {r['usuario_id'] for r in rows if not r.get('usuario_nome') and r.get('usuario_id')}
    nomes = {}
    if faltam:
        ph = ','.join(['%s'] * len(faltam))
        for u in execute_query(f"SELECT id, nome FROM usuarios WHERE id IN ({ph})",
                               tuple(faltam), fetch=True) or []:
            nomes[u['id']] = u['nome']

    # 09/11/2026: Histórico em CARTÕES ("assim como está fica cansativo") — utils/hist_cartoes.
    if view == 'alteracoes':
        ref = HC.nomes_referenciados(rows, execute_query)
        eventos, cont, n_cartoes = HC.montar(rows, nomes, ref)
    else:
        eventos, cont, n_cartoes = HC.consultas(rows, nomes)
    total_paginas = max(1, (total + per - 1) // per)
    return render_template('clientes/_historico.html', eventos=eventos, cont=cont, n_cartoes=n_cartoes,
                           categorias=[c for c in HC.CATEGORIAS if cont.get(c[0])],
                           view=view, page=page,
                           total=total, total_paginas=total_paginas, cliente_id=id,
                           auditoria_inicio=AUDITORIA_INICIO, is_admin=current_user.is_admin())
