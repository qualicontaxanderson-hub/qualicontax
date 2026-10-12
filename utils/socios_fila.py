# -*- coding: utf-8 -*-
"""Fila "Sócios a cadastrar" (12/10/2026, pedido do Anderson).

304 empresas estavam sem nenhum sócio. A Receita (BrasilAPI/ReceitaWS) devolve
o quadro de sócios (QSA) com nome, qualificação e data de entrada — mas o CPF
vem com o meio só (***.123.456-**), e sem o CPF inteiro não se cria o cadastro
da pessoa. Então a fila agrupa pela PESSOA: a equipe completa o cadastro UMA
vez e o sistema liga essa pessoa a todas as empresas dela.

Uma linha por empresa x sócio (tabela socios_fila). A "chave" identifica a
mesma pessoa em empresas diferentes:
  PF -> 'PF:<6 dígitos do meio>:<nome normalizado>'
  PJ -> 'PJ:<raiz do CNPJ>'   (sócio empresa: a Receita manda só os 8
                                primeiros dígitos, identificador_de_socio=1)

status: pendente | vinculado | administrador | ignorado
  administrador = Administrador/Diretor/Presidente que não é dono de cotas
  (S.A., administrador não sócio): fica de consulta, não vira sócio — sócio
  exige participação, e a empresa precisa fechar 100%.
"""
import re
import unicodedata
from decimal import Decimal

from utils.db_helper import execute_query

QUAL_NAO_SOCIO = ('administrador', 'diretor', 'presidente', 'procurador', 'conselheiro')


def so_digitos(s):
    return re.sub(r'\D', '', s or '')


def nome_chave(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Z0-9]+', ' ', s.upper()).strip()


def qualificacao_limpa(v):
    return re.sub(r'^\s*\d+\s*-\s*', '', str(v or '')).strip()[:80]


def eh_socio(qualificacao):
    """'Sócio-Administrador' é sócio; 'Administrador'/'Diretor' sozinho não."""
    q = nome_chave(qualificacao).lower()
    if 'socio' in q or 'titular' in q or 'empresario' in q:
        return True
    return not any(p in q for p in QUAL_NAO_SOCIO)


def item_do_qsa(s):
    """Um item do QSA -> dict da fila (ou None se não der para identificar)."""
    if not isinstance(s, dict):
        return None
    nome = (s.get('nome_socio') or s.get('nome') or '').strip()
    if not nome:
        return None
    doc = (s.get('cnpj_cpf_do_socio') or '').strip()
    dig = so_digitos(doc)
    if str(s.get('identificador_de_socio') or '') == '1' or ('*' not in doc and len(dig) in (8, 14)):
        tipo, chave = 'PJ', 'PJ:' + dig[:8]
    else:
        tipo, chave = 'PF', 'PF:%s:%s' % (dig if len(dig) == 6 else '', nome_chave(nome))
    qual = qualificacao_limpa(s.get('qualificacao_socio') or s.get('qual'))
    ent = str(s.get('data_entrada_sociedade') or '')[:10]
    return {'chave': chave[:120], 'tipo': tipo, 'nome': nome[:255], 'doc': doc[:20] or None,
            'qualificacao': qual or None,
            'data_entrada': ent if re.match(r'^\d{4}-\d{2}-\d{2}$', ent) else None,
            'status': 'pendente' if eh_socio(qual) else 'administrador'}


def popular(cliente_id, qsa, gravar=True):
    """Põe o QSA de UMA empresa na fila (repetir não duplica). Devolve os itens."""
    itens = [i for i in (item_do_qsa(s) for s in (qsa or [])) if i]
    if gravar:
        for i in itens:
            execute_query(
                """INSERT IGNORE INTO socios_fila
                       (cliente_id, chave, tipo, nome, doc, qualificacao, data_entrada, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (cliente_id, i['chave'], i['tipo'], i['nome'], i['doc'], i['qualificacao'],
                 i['data_entrada'], i['status']))
    return itens


def doc_mascarado(tipo, doc):
    d = so_digitos(doc)
    if tipo == 'PJ' and len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    if tipo == 'PJ' and len(d) == 8:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/…'
    if tipo == 'PF' and len(d) == 6:
        return f'***.{d[:3]}.{d[3:]}-**'
    return doc or ''


def confere(chave, cadastro):
    """O cadastro escolhido é a pessoa da chave? -> (ok, motivo)."""
    tipo = chave[:2]
    d = so_digitos(cadastro.get('cpf_cnpj'))
    if tipo == 'PJ':
        if cadastro.get('tipo_pessoa') != 'PJ':
            return False, 'este sócio é uma empresa: escolha um cadastro de empresa (CNPJ).'
        cnpj = chave[3:]
        if not cnpj or d[:8] != cnpj[:8]:
            return False, f'o CNPJ do cadastro não é o que a Receita mostra ({doc_mascarado("PJ", cnpj)}).'
        return True, ''
    if cadastro.get('tipo_pessoa') != 'PF':
        return False, 'este sócio é uma pessoa física: escolha um cadastro de pessoa física (CPF).'
    meio = chave.split(':')[1]
    if meio and len(d) == 11 and d[3:9] != meio:
        return False, f'o CPF do cadastro não bate com o que a Receita mostra ({doc_mascarado("PF", meio)}).'
    if meio and len(d) != 11:
        return False, 'o cadastro escolhido está sem CPF completo.'
    return True, ''


def achar_cadastro(chave, nome):
    """Cadastro que já existe para esta pessoa (mesma regra da tela de cadastro:
    PF pelos 6 dígitos do meio E o nome; PJ pela raiz do CNPJ)."""
    tipo = chave[:2]
    if tipo == 'PJ':
        cnpj = chave[3:11]
        return execute_query(
            """SELECT id FROM clientes
                WHERE tipo_pessoa = 'PJ' AND COALESCE(situacao, 'ATIVO') <> 'INATIVO'
                  AND REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '-', ''), '/', '') LIKE %s
                ORDER BY (SUBSTRING(REPLACE(REPLACE(REPLACE(cpf_cnpj, '.', ''), '-', ''), '/', ''), 9, 4) = '0001') DESC
                LIMIT 1""", (cnpj + '%',), fetch=True, fetch_one=True)
    meio = chave.split(':')[1]
    if not meio:
        return None
    for r in execute_query(
            """SELECT id, nome_razao_social FROM clientes
                WHERE tipo_pessoa = 'PF' AND COALESCE(situacao, 'ATIVO') <> 'INATIVO'
                  AND SUBSTRING(REPLACE(REPLACE(cpf_cnpj, '.', ''), '-', ''), 4, 6) = %s""",
            (meio,), fetch=True) or []:
        if nome_chave(r['nome_razao_social']) == nome_chave(nome):
            return r
    return None


def mapa_cadastros():
    """chave -> id do cadastro que já existe, para a tela inteira de uma vez
    (a mesma regra do achar_cadastro, sem uma consulta por pessoa)."""
    pf, pj = {}, {}
    for r in execute_query(
            """SELECT id, cpf_cnpj, nome_razao_social, tipo_pessoa FROM clientes
                WHERE COALESCE(situacao, 'ATIVO') <> 'INATIVO'
                ORDER BY id""", fetch=True) or []:
        d = so_digitos(r['cpf_cnpj'])
        if r['tipo_pessoa'] == 'PF' and len(d) == 11:
            pf.setdefault('PF:%s:%s' % (d[3:9], nome_chave(r['nome_razao_social'])), r['id'])
        elif r['tipo_pessoa'] == 'PJ' and len(d) == 14:
            # raiz: a matriz (0001) primeiro, senão o primeiro cadastro da raiz
            if d[8:12] == '0001' or d[:8] not in pj:
                pj[d[:8]] = r['id']
    def achar(chave):
        if chave.startswith('PJ:'):
            return pj.get(chave[3:11])
        return pf.get(chave) if chave.split(':')[1] else None
    return achar


def linhas_abertas():
    """Linhas pendentes/administrador de empresas ATIVAS que ainda não têm
    esta pessoa como sócia atual (se alguém ligou pelo cadastro, some daqui)."""
    rows = execute_query(
        """SELECT f.*, c.numero_cliente, c.nome_razao_social AS empresa, c.cpf_cnpj AS empresa_doc,
                  COALESCE(c.avulso, 0) AS avulso
             FROM socios_fila f JOIN clientes c ON c.id = f.cliente_id
            WHERE f.status IN ('pendente', 'administrador') AND c.situacao = 'ATIVO'
            ORDER BY f.chave, c.nome_razao_social""", fetch=True) or []
    if not rows:
        return []
    ids = sorted({r['cliente_id'] for r in rows})
    marcas = ','.join(['%s'] * len(ids))
    atuais = {}
    for s in execute_query(
            f"""SELECT s.cliente_id, k.cpf_cnpj, k.nome_razao_social, k.tipo_pessoa
                  FROM socios_clientes s JOIN clientes k ON k.id = s.pf_cliente_id
                 WHERE s.ativo = 1 AND s.cliente_id IN ({marcas})""", tuple(ids), fetch=True) or []:
        d = so_digitos(s['cpf_cnpj'])
        k = ('PJ:' + d[:8]) if s['tipo_pessoa'] == 'PJ' else 'PF:%s:%s' % (d[3:9], nome_chave(s['nome_razao_social']))
        atuais.setdefault(s['cliente_id'], set()).add(k[:120])
    return [r for r in rows if r['chave'] not in atuais.get(r['cliente_id'], set())]


def vincular(chave, cadastro, usuario_id, registrar=None, rotulo_empresa=None, ocultas=frozenset()):
    """Liga o cadastro a TODAS as empresas pendentes desta chave.

    Participação: 100% quando a pessoa é o ÚNICO sócio do quadro da Receita e
    a empresa não tem outro sócio atual; senão 0% — a Receita não informa a
    participação e ela vem do contrato social (o Editar exige fechar 100%).
    Responsável: o primeiro sócio da empresa. Devolve [(cliente_id, socio_id, pct)].
    """
    from models.socio_cliente import SocioCliente
    rows = execute_query(
        """SELECT f.id, f.cliente_id, f.qualificacao, f.data_entrada
             FROM socios_fila f JOIN clientes c ON c.id = f.cliente_id
            WHERE f.chave = %s AND f.status = 'pendente' AND c.situacao = 'ATIVO'""",
        (chave,), fetch=True) or []
    feitos = []
    for r in rows:
        cid = r['cliente_id']
        if cid == cadastro['id'] or cid in ocultas:     # empresa reservada fica de fora
            continue
        if SocioCliente.existe_pf(cid, cadastro['id']):
            sid = (execute_query(
                "SELECT id FROM socios_clientes WHERE cliente_id = %s AND pf_cliente_id = %s AND ativo = 1",
                (cid, cadastro['id']), fetch=True, fetch_one=True) or {}).get('id')
        else:
            n_qsa = (execute_query(
                "SELECT COUNT(*) n FROM socios_fila WHERE cliente_id = %s AND status <> 'administrador'",
                (cid,), fetch=True, fetch_one=True) or {}).get('n', 0)
            atuais = SocioCliente.get_by_cliente(cid)
            pct = Decimal('100') if n_qsa == 1 and not atuais else Decimal('0')
            resp = not any(s.get('responsavel') for s in atuais)
            sid = SocioCliente.create(
                cliente_id=cid, pf_cliente_id=cadastro['id'], nome=cadastro.get('nome_razao_social'),
                cpf=cadastro.get('cpf_cnpj'), email=cadastro.get('email'),
                telefone=cadastro.get('celular') or cadastro.get('telefone'),
                percentual_participacao=pct, responsavel=resp, ativo=True,
                qualificacao=r['qualificacao'], data_entrada=r['data_entrada'])
            if registrar and sid:
                registrar('escrita.criou_socio', 'cadastros', tabela='socios_clientes', registro_id=sid,
                          depois={'cliente_id': cid, 'pf_cliente_id': cadastro['id'],
                                  'nome': cadastro.get('nome_razao_social'), 'cpf': cadastro.get('cpf_cnpj'),
                                  'percentual_participacao': str(pct), 'responsavel': resp,
                                  'qualificacao': r['qualificacao'],
                                  'data_entrada': r['data_entrada'].isoformat() if r['data_entrada'] else None,
                                  'origem': 'fila Sócios a cadastrar',
                                  **(rotulo_empresa(cid) if rotulo_empresa else {})})
            if sid:
                feitos.append((cid, sid, pct))
        if sid:
            execute_query(
                """UPDATE socios_fila SET status = 'vinculado', socio_id = %s,
                          resolvido_por = %s, resolvido_em = NOW() WHERE id = %s""",
                (sid, usuario_id, r['id']))
    return feitos


def ignorar(chave, usuario_id, ocultas=frozenset()):
    """Tira a pessoa da fila (não é sócio de verdade, homônimo, etc.)."""
    for r in execute_query("SELECT id, cliente_id FROM socios_fila WHERE chave = %s AND status = 'pendente'",
                           (chave,), fetch=True) or []:
        if r['cliente_id'] not in ocultas:
            execute_query("""UPDATE socios_fila SET status = 'ignorado', resolvido_por = %s, resolvido_em = NOW()
                              WHERE id = %s""", (usuario_id, r['id']))
    return True
