# -*- coding: utf-8 -*-
"""Caixas de e-mail do escritório — um lugar só: o Departamento (14/11/2026).

Anderson: "lá coloco uma vez só o e-mail e pronto !! O APP inteiro usaremos só e-mails de lá e
vinculando para cada serviço".

- O endereço mora em ``departamentos.email`` e o nome do "De:" em ``departamentos.email_nome``.
- A SENHA mora por endereço em ``email_caixas`` (cifrada): vários departamentos podem usar a mesma
  caixa (Legalização, Atendimento e Comercial = legalizacao@) e a senha é uma só.
- O serviço guarda só o departamento (``fin_cobranca_contas.departamento_id``).

Enquanto a migração ``emails_02_caixas_14_11`` não roda, tudo aqui responde "sem caixa" e a conta de
cobrança segue com as colunas antigas (``FinCobrancaConta.remetente_email`` cai nelas).
"""
import logging
import os

from utils.db_helper import execute_query
from utils import cobranca_segredos as seg

logger = logging.getLogger(__name__)

HOST_PADRAO, PORTA_PADRAO = 'smtp.titan.email', 465


def normal(email):
    return (email or '').strip().lower()


def pronta():
    """A tabela já existe? (antes da migração, nada aqui grava)."""
    try:
        return bool(execute_query("SHOW TABLES LIKE 'email_caixas'", fetch=True))
    except Exception:
        return False


def caixa(email):
    """A caixa do endereço, SEM a senha: {email, host, porta, tem_senha, testado_em, teste_resultado}."""
    em = normal(email)
    if not em or not pronta():
        return None
    c = execute_query('SELECT * FROM email_caixas WHERE email = %s', (em,), fetch=True, fetch_one=True)
    if c:
        c['tem_senha'] = c.get('senha_cif') is not None
        c.pop('senha_cif', None)
    return c


def salvar(email, senha=None, host=None, porta=None, usuario_id=None):
    """Cria/atualiza a caixa. Senha vazia = mantém a que está."""
    em = normal(email)
    if not em or not pronta():
        return False
    host = (host or '').strip()[:80] or HOST_PADRAO
    try:
        porta = int(porta)
        porta = porta if 1 <= porta <= 65535 else PORTA_PADRAO
    except (TypeError, ValueError):
        porta = PORTA_PADRAO
    cif = seg.cifrar(senha) if senha else None
    if execute_query('SELECT id FROM email_caixas WHERE email = %s', (em,), fetch=True, fetch_one=True):
        sets, vals = ['host = %s', 'porta = %s', 'alterado_por = %s', 'alterado_em = NOW()'], [host, porta, usuario_id]
        if cif:
            sets.insert(0, 'senha_cif = %s'); vals.insert(0, cif)
        execute_query(f"UPDATE email_caixas SET {', '.join(sets)} WHERE email = %s", tuple(vals) + (em,), fetch=False)
    else:
        execute_query('INSERT INTO email_caixas (email, senha_cif, host, porta, alterado_por, alterado_em) '
                      'VALUES (%s, %s, %s, %s, %s, NOW())', (em, cif, host, porta, usuario_id), fetch=False)
    return True


def registrar_teste(email, resultado):
    if pronta():
        execute_query('UPDATE email_caixas SET testado_em = NOW(), teste_resultado = %s WHERE email = %s',
                      ((resultado or '')[:255], normal(email)), fetch=False)


def departamento(dep_id):
    return execute_query('SELECT * FROM departamentos WHERE id = %s', (dep_id,), fetch=True, fetch_one=True)


def remetente_do_departamento(dep_id):
    """Pronto para ``email_envio.enviar(remetente=...)``. Decifra só aqui. ValueError com o porquê."""
    d = departamento(dep_id)
    if not d:
        raise ValueError('O departamento escolhido para enviar não existe mais.')
    if not d.get('email'):
        raise ValueError(f'O departamento {d["nome"]} não tem e-mail (Configurações → Departamentos).')
    if not pronta():
        raise ValueError('As caixas de e-mail ainda não foram criadas no banco.')
    c = execute_query('SELECT * FROM email_caixas WHERE email = %s', (normal(d['email']),), fetch=True, fetch_one=True)
    if not c or not c.get('senha_cif'):
        raise ValueError(f'A caixa {d["email"]} está sem senha: preencha em Configurações → Departamentos → '
                         f'{d["nome"]}.')
    return {'host': c['host'] or HOST_PADRAO, 'port': int(c['porta'] or PORTA_PADRAO), 'user': normal(d['email']),
            'senha': seg.decifrar_texto(c['senha_cif']), 'nome': d.get('email_nome') or f'Qualicontax - {d["nome"]}'}


def departamentos_para_enviar():
    """Departamentos ativos com e-mail, para o "Envia por" dos serviços: [{id, nome, email, tem_senha}]."""
    nome = 'email_nome' if _tem_coluna('departamentos', 'email_nome') else 'NULL AS email_nome'
    deps = execute_query(f'SELECT id, nome, email, {nome} FROM departamentos '
                         'WHERE ativo = 1 AND email IS NOT NULL AND email <> "" ORDER BY nome', fetch=True) or []
    com_senha = set()
    if pronta():
        com_senha = {r['email'] for r in execute_query(
            'SELECT email FROM email_caixas WHERE senha_cif IS NOT NULL', fetch=True) or []}
    for d in deps:
        d['tem_senha'] = normal(d['email']) in com_senha
    return deps


def mesmos_da_caixa(email, menos_id=None):
    """Outros departamentos que usam a mesma caixa (a senha é uma só)."""
    return [d for d in (execute_query('SELECT id, nome FROM departamentos WHERE LOWER(TRIM(email)) = %s ORDER BY nome',
                                      (normal(email),), fetch=True) or []) if d['id'] != menos_id]


# ---------------------------------------------------------------------------
# Mapa: todo serviço do app que manda e-mail e por qual departamento
# ---------------------------------------------------------------------------

def _tem_coluna(tabela, coluna):
    try:
        return bool(execute_query(f'SHOW COLUMNS FROM {tabela} LIKE %s', (coluna,), fetch=True))
    except Exception:
        return False


def mapa():
    """Linhas {servico, detalhe, icone, cor, departamento_id, departamento, email, situacao, tipo}.
    tipo: 'envia' (manda em nome do escritório) | 'recebe' (avisos que chegam)."""
    linhas = []
    deps = {d['id']: d for d in (execute_query('SELECT id, nome, email FROM departamentos', fetch=True) or [])}
    tem_dep = _tem_coluna('fin_cobranca_contas', 'departamento_id')
    contas = execute_query(
        f"""SELECT c.id, c.apelido, c.provedor, c.ambiente, c.ativo, c.so_consulta, c.avisa_por, c.email_remetente,
                   {'c.departamento_id' if tem_dep else 'NULL AS departamento_id'},
                   fc.banco_nome, fc.agencia, fc.conta, cl.nome_razao_social
              FROM fin_cobranca_contas c
              LEFT JOIN fin_contas fc ON fc.id = c.fin_conta_id
              JOIN clientes cl ON cl.id = c.empresa_id
             WHERE c.ativo = 1 AND c.so_consulta = 0
             ORDER BY c.empresa_id, c.ambiente = 'producao' DESC, c.id""", fetch=True) or []
    from models.fin_cobranca import PROVEDORES
    for c in contas:
        d = deps.get(c['departamento_id'])
        banco = PROVEDORES.get(c['provedor'], c['provedor'])
        conta_txt = ' · '.join(x for x in (f"ag {c['agencia']}" if c.get('agencia') else '',
                                            f"cc {c['conta']}" if c.get('conta') else '') if x)
        if (c.get('avisa_por') or 'app') != 'app':
            sit = 'o banco avisa' if c['avisa_por'] == 'efi' else 'não avisa'
        elif d:
            sit = 'envia'
        elif c.get('email_remetente'):
            sit = 'caixa antiga'
        else:
            sit = 'sem e-mail'
        linhas.append({
            'servico': f'Boletos · {banco}' + (' (teste)' if c['ambiente'] != 'producao' else ''),
            'detalhe': f"{conta_txt or c['apelido']} · manda o boleto e a nota ao cliente",
            'icone': 'fa-barcode', 'cor': {'efi': '#ea580c', 'bradesco': '#cc092f'}.get(c['provedor'], '#15803d'),
            'departamento_id': d['id'] if d else None, 'departamento': d['nome'] if d else None,
            'email': (d or {}).get('email') or c.get('email_remetente'), 'situacao': sit, 'tipo': 'envia',
            'link': 'cobranca', 'ordem': 1})
    # contratos: saem pela caixa da conta de cobrança padrão da Qualicontax (routes/financeiro_contratos._remetente)
    try:
        from models.fin_cobranca import FinCobrancaConta
        p = FinCobrancaConta.padrao_da_empresa(2)
    except Exception:
        p = None
    if p:
        d = deps.get(p.get('departamento_id'))
        linhas.append({'servico': 'Contratos', 'detalhe': 'manda o contrato para o cliente assinar',
                       'icone': 'fa-file-signature', 'cor': '#0369a1',
                       'departamento_id': d['id'] if d else None, 'departamento': d['nome'] if d else None,
                       'email': (d or {}).get('email') or p.get('email_remetente'),
                       'situacao': 'envia' if d else ('caixa antiga' if p.get('email_remetente') else 'sistema'),
                       'tipo': 'envia', 'link': 'contratos', 'ordem': 2})
    sistema = os.getenv('SMTP_USER', 'app@qualicontax.com.br').strip()
    linhas.append({'servico': 'Código de acesso (login)', 'detalhe': 'manda o código para quem entra no app',
                   'icone': 'fa-key', 'cor': '#64748b', 'departamento_id': None, 'departamento': None,
                   'email': sistema, 'situacao': 'sistema', 'tipo': 'envia', 'ordem': 3})
    # avisos automáticos: quem MANDA é a caixa do sistema; o departamento RECEBE
    try:
        from utils.relatorios_email import RELATORIOS
    except Exception:
        RELATORIOS = {}
    rec = execute_query('SELECT departamento_id, relatorio FROM email_relatorio_destino', fetch=True) or []
    por_dep = {}
    for r in rec:
        por_dep.setdefault(r['departamento_id'], []).append(r['relatorio'])
    for dep_id, rels in sorted(por_dep.items(), key=lambda x: (deps.get(x[0]) or {}).get('nome') or ''):
        d = deps.get(dep_id)
        if not d:
            continue
        linhas.append({'servico': 'Avisos automáticos', 'icone': 'fa-bell', 'cor': '#6d28d9',
                       'detalhe': ', '.join(RELATORIOS.get(x, {}).get('nome', x) for x in sorted(rels)),
                       'qtd': len(rels), 'departamento_id': d['id'], 'departamento': d['nome'],
                       'email': d.get('email'), 'situacao': 'recebe', 'tipo': 'recebe', 'ordem': 4})
    return linhas
