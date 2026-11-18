# -*- coding: utf-8 -*-
"""Grava no banco as declarações do IRPF lidas pelo ``leitor`` e cuida dos arquivos.

Três portas de entrada, todas por ``gravar_dec``:
- a _ENTRADA do Dropbox, onde o Q-Colabore deixa o que vigia nos PCs do escritório
  (``varrer_entrada``, chamada pelo cron do roteador a cada 5 min);
- o envio pela tela (routes/contabil_irpf.py);
- a releitura de uma declaração já gravada (``reler``).

Regras:
- o CPF cruza com o cadastro (clientes, com os avulsos) pelos dígitos; sem cadastro a
  declaração fica "sem cadastro" e a tela oferece criar o avulso ou vincular;
- o mesmo arquivo (SHA-256) não entra duas vezes; a retificadora entra como nova linha e a
  original deixa de ser a vigente;
- o arquivo fica no Dropbox: ``EMPRESAS/{nº - nome}/IRPF/{exercício}/`` quando há cadastro,
  ``IRPF/SEM CADASTRO/{cpf}/`` quando não há (e muda de pasta quando vincula);
- o .REC (recibo) só completa a declaração: se chegar antes do .DEC, espera na _ENTRADA.
"""
import hashlib
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.db_helper import execute_query
from utils.irpf import leitor

logger = logging.getLogger(__name__)
FUSO = ZoneInfo('America/Sao_Paulo')
EXTENSOES = ('.dec', '.rec')


# ---------------------------------------------------------------------------
# Cadastro
# ---------------------------------------------------------------------------
def cliente_por_cpf(cpf):
    """id do cadastro (cliente ou avulso) com este CPF, ou None. PF primeiro."""
    dig = re.sub(r'\D', '', str(cpf or ''))
    if len(dig) != 11:
        return None
    r = execute_query(
        "SELECT id, tipo_pessoa FROM clientes "
        " WHERE REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-','') = %s "
        " ORDER BY (tipo_pessoa='PF') DESC, avulso ASC, id ASC LIMIT 1", (dig,), fetch=True, fetch_one=True)
    return r['id'] if r else None


def cpf_formatado(dig):
    d = re.sub(r'\D', '', str(dig or ''))
    return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}' if len(d) == 11 else d


def _avulso_automatico(r, usuario_id=None):
    """Cria o avulso PF na leitura. Nunca levanta: se falhar, a declaração fica 'sem cadastro' e a tela oferece o botão."""
    try:
        cid = criar_avulso(r, usuario_id)
    except Exception:
        logger.exception('[irpf] não criei o avulso do CPF %s***', str(r.get('cpf'))[:3])
        return None
    try:
        from utils.atividade import registrar_agente
        registrar_agente('escrita.irpf_avulso_criado', 'contabil', usuario_id, 'Leitura do IRPF', tabela='clientes', registro_id=cid,
                         depois={'nome': r.get('nome'), 'exercicio': r.get('exercicio'), 'origem': r.get('origem')})
    except Exception:
        pass
    return cid


def criar_avulso(decl, usuario_id=None):
    """Cria o cadastro avulso (PF) com o que a declaração traz e devolve o id."""
    from models.cliente import Cliente
    ja = cliente_por_cpf(decl['cpf'])
    if ja:
        return ja
    return Cliente.create({
        'tipo_pessoa': 'PF', 'nome_razao_social': decl.get('nome') or '', 'cpf_cnpj': cpf_formatado(decl['cpf']),
        'email': decl.get('email'), 'telefone': decl.get('telefone'), 'situacao': 'ATIVO',
        'observacoes': f"Criado pela leitura do IRPF {decl.get('exercicio')} em {datetime.now(FUSO):%d/%m/%Y}.",
        'avulso': 1, 'criado_por': usuario_id,
    })


# ---------------------------------------------------------------------------
# Dropbox
# ---------------------------------------------------------------------------
def _svc():
    from utils import dropbox_sync
    return dropbox_sync._service


def pasta_destino(cliente_id, cpf, exercicio):
    from utils.dropbox_sync import _build_empresa_folder
    svc = _svc()
    if cliente_id:
        c = execute_query("SELECT numero_cliente, nome_razao_social FROM clientes WHERE id=%s", (cliente_id,),
                          fetch=True, fetch_one=True)
        if c:
            return svc._build_path('EMPRESAS', _build_empresa_folder(c.get('numero_cliente'), c.get('nome_razao_social') or ''),
                                   'IRPF', str(exercicio))
    return svc._build_path('IRPF', 'SEM CADASTRO', re.sub(r'\D', '', str(cpf or '')) or 'SEM CPF')


def _guardar(conteudo, nome, pasta, origem_path=None):
    """Põe o arquivo na pasta de destino: move (quando veio da _ENTRADA) ou sobe (tela)."""
    svc = _svc()
    destino = f'{pasta}/{nome}'
    try:
        if origem_path:                      # da _ENTRADA: só mover (se falhar, fica lá e a próxima rodada tenta)
            return destino if svc.move_file(origem_path, destino) else None
        if svc.upload_bytes(destino, conteudo):
            return destino
    except Exception:
        logger.exception('[irpf] não guardou %s em %s', nome, pasta)
    return None


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
_CAMPOS = ['cpf', 'nome', 'cliente_id', 'vinculo', 'exercicio', 'ano_base', 'retificadora', 'completa', 'tipo_declaracao',
           'resultado', 'vr_restituir', 'vr_pagar', 'vr_tributaveis', 'vr_isentos', 'vr_exclusivos', 'vr_base_calculo',
           'vr_imposto_devido', 'vr_imposto_retido', 'vr_bens_anterior', 'vr_bens_atual', 'vr_dividas_atual', 'vr_pagamentos',
           'quotas', 'vr_quota', 'banco', 'agencia', 'conta', 'nr_hash', 'recibo', 'recibo_anterior', 'dt_nascimento', 'email',
           'telefone', 'uf', 'municipio', 'cep', 'ocupacao', 'conjuge_cpf', 'arquivo_base', 'arquivo_dec', 'hash_dec', 'origem',
           'leiaute_usado', 'aproximado', 'crc_falhas', 'aviso', 'criado_por']


def _aviso(r):
    partes = []
    if r.get('aproximado'):
        partes.append(f"leiaute de {r['leiaute_usado']} usado para o exercício {r['exercicio']}")
    if r.get('desconhecidos'):
        partes.append('registros não lidos: ' + ', '.join(r['desconhecidos'][:6]))
    if (r.get('crc_falha') or 0) > 1:
        partes.append(f"{r['crc_falha']} linha(s) com controle divergente")
    return '; '.join(partes)[:255] or None


def _gravar_filhos(decl_id, r):
    for t in ('irpf_rendimentos', 'irpf_dependentes', 'irpf_bens', 'irpf_dividas', 'irpf_pagamentos'):
        execute_query(f'DELETE FROM {t} WHERE declaracao_id=%s', (decl_id,))
    for x in r['rendimentos']:
        execute_query("""INSERT INTO irpf_rendimentos (declaracao_id, bloco, dependente_cpf, fonte_doc, fonte_nome, codigo, mes,
                         valor, previdencia, decimo_terceiro, imposto_retido, irrf_13) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (decl_id, x['bloco'], x.get('dependente_cpf'), (x.get('fonte_doc') or None), (x.get('fonte_nome') or '')[:80] or None,
                       (x.get('codigo') or None), x.get('mes'), x.get('valor') or 0, x.get('previdencia') or 0,
                       x.get('decimo_terceiro') or 0, x.get('imposto_retido') or 0, x.get('irrf_13') or 0))
    for x in r['dependentes']:
        execute_query("INSERT INTO irpf_dependentes (declaracao_id, chave, codigo, nome, nascimento, cpf) VALUES (%s,%s,%s,%s,%s,%s)",
                      (decl_id, str(x.get('chave') or '')[:5] or None, x.get('codigo') or None, (x.get('nome') or '')[:80] or None,
                       x.get('nascimento'), x.get('cpf')))
    for x in r['bens']:
        execute_query("""INSERT INTO irpf_bens (declaracao_id, chave, grupo, codigo, exterior, pais, descricao, vr_anterior, vr_atual,
                         cpf_cnpj, banco, conta, matricula, area, renavam, dt_aquisicao) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (decl_id, str(x.get('chave') or '')[:5] or None, x.get('grupo') or None, x.get('codigo') or None, int(bool(x.get('exterior'))),
                       x.get('pais'), x.get('descricao'), x.get('vr_anterior') or 0, x.get('vr_atual') or 0, (x.get('cpf_cnpj') or None),
                       x.get('banco'), x.get('conta'), x.get('matricula'), x.get('area') if isinstance(x.get('area'), (int, float)) or hasattr(x.get('area'), 'quantize') else None,
                       x.get('renavam'), x.get('dt_aquisicao')))
    for x in r['dividas']:
        execute_query("INSERT INTO irpf_dividas (declaracao_id, codigo, descricao, vr_anterior, vr_atual, vr_pago) VALUES (%s,%s,%s,%s,%s,%s)",
                      (decl_id, x.get('codigo') or None, x.get('descricao'), x.get('vr_anterior') or 0, x.get('vr_atual') or 0, x.get('vr_pago') or 0))
    for x in r['pagamentos']:
        execute_query("""INSERT INTO irpf_pagamentos (declaracao_id, codigo, dependente_chave, beneficiario_doc, beneficiario_nome, valor,
                         vr_nao_dedutivel, tipo, descricao) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (decl_id, x.get('codigo') or None, str(x.get('dependente_chave') or '')[:5] or None, x.get('beneficiario_doc'),
                       (x.get('beneficiario_nome') or '')[:80] or None, x.get('valor') or 0, x.get('vr_nao_dedutivel') or 0,
                       x.get('tipo'), (x.get('descricao') or '')[:512] or None))


def _marcar_vigente(cpf, exercicio):
    """A última entregue do CPF no exercício é a vigente: retificadora > original; depois a mais recente."""
    rows = execute_query("SELECT id, retificadora, transmitida_em, lido_em FROM irpf_declaracoes WHERE cpf=%s AND exercicio=%s",
                         (cpf, exercicio), fetch=True) or []
    if not rows:
        return
    rows.sort(key=lambda r: (int(r['retificadora'] or 0), r['transmitida_em'] or datetime.min, r['lido_em'] or datetime.min, r['id']))
    vig = rows[-1]['id']
    execute_query("UPDATE irpf_declaracoes SET vigente = (id = %s) WHERE cpf=%s AND exercicio=%s", (vig, cpf, exercicio))


def gravar_dec(conteudo, nome, origem='colabore', usuario_id=None, origem_path=None):
    """Lê e grava um .DEC. Devolve {'situacao': 'nova'|'repetida'|'erro', 'id', 'mensagem', 'resumo'}."""
    nome = os.path.basename(nome or 'declaracao.DEC')
    h = hashlib.sha256(conteudo).hexdigest()
    ja = execute_query("SELECT id, cliente_id, exercicio, cpf FROM irpf_declaracoes WHERE hash_dec=%s", (h,), fetch=True, fetch_one=True)
    if ja:
        if origem_path:        # veio de novo pela _ENTRADA: guarda na pasta certa e some da entrada
            _guardar(conteudo, nome, pasta_destino(ja['cliente_id'], ja['cpf'], ja['exercicio']), origem_path)
        return {'situacao': 'repetida', 'id': ja['id'], 'mensagem': 'Esta declaração já estava lida.'}
    try:
        lido = leitor.ler(conteudo)
        r = leitor.resumo(lido)
    except Exception as exc:
        logger.warning('[irpf] %s: não foi possível ler (%s)', nome, exc)
        return {'situacao': 'erro', 'id': None, 'mensagem': f'Não foi possível ler o arquivo: {exc}'}
    meta = leitor.nome_do_arquivo(nome) or {}
    if not r['cpf'] or len(r['cpf']) != 11:
        return {'situacao': 'erro', 'id': None, 'mensagem': 'O arquivo não traz o CPF do declarante: não é um .DEC do IRPF.'}
    if meta.get('cpf') and meta['cpf'] != r['cpf']:
        return {'situacao': 'erro', 'id': None, 'mensagem': 'O CPF do nome do arquivo não é o CPF de dentro da declaração.'}
    cid = cliente_por_cpf(r['cpf'])
    r['vinculo'] = 'auto'
    if not cid:
        # 18/11/2026 (Anderson): "quando não tiver cadastro fazer um avulso, para termos cadastro de tudo"
        cid = _avulso_automatico(r, usuario_id)
        r['vinculo'] = 'avulso_novo' if cid else 'sem_cadastro'
    r['cliente_id'] = cid
    r['arquivo_base'] = os.path.splitext(nome)[0][:80]
    r['hash_dec'] = h
    r['origem'] = origem
    r['crc_falhas'] = r.get('crc_falha') or 0
    r['aviso'] = _aviso(r)
    r['criado_por'] = usuario_id
    r['retificadora'] = int(bool(r['retificadora'])); r['completa'] = int(bool(r['completa'])); r['aproximado'] = int(bool(r['aproximado']))
    r['arquivo_dec'] = _guardar(conteudo, nome, pasta_destino(cid, r['cpf'], r['exercicio']), origem_path)
    cols = [c for c in _CAMPOS]
    decl_id = execute_query(f"INSERT INTO irpf_declaracoes ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                            tuple(r.get(c) for c in cols))
    _gravar_filhos(decl_id, r)
    _marcar_vigente(r['cpf'], r['exercicio'])
    # o .REC pode já estar na mesma pasta do destino? Não: ele chega pela _ENTRADA; a varredura casa pelo nome.
    return {'situacao': 'nova', 'id': decl_id, 'mensagem': 'Declaração lida.', 'resumo': r}


def gravar_rec(conteudo, nome, origem_path=None):
    """Casa o .REC com a declaração de mesmo nome-base e grava data/hora da transmissão.
    Devolve {'situacao': 'ok'|'aguardando'|'erro', 'id'}."""
    nome = os.path.basename(nome or '')
    base = os.path.splitext(nome)[0]
    d = execute_query("SELECT id, cliente_id, cpf, exercicio FROM irpf_declaracoes WHERE arquivo_base=%s ORDER BY id DESC LIMIT 1",
                      (base[:80],), fetch=True, fetch_one=True)
    if not d:
        return {'situacao': 'aguardando', 'id': None, 'mensagem': 'A declaração (.DEC) deste recibo ainda não chegou.'}
    try:
        rr = leitor.resumo_recibo(leitor.ler(conteudo, exercicio=d['exercicio']))
    except Exception as exc:
        return {'situacao': 'erro', 'id': d['id'], 'mensagem': f'Recibo ilegível: {exc}'}
    path = _guardar(conteudo, nome, pasta_destino(d['cliente_id'], d['cpf'], d['exercicio']), origem_path)
    execute_query("""UPDATE irpf_declaracoes SET transmitida_em=COALESCE(%s, transmitida_em), recibo_local=%s,
                     recibo_mensagem=%s, arquivo_rec=COALESCE(%s, arquivo_rec) WHERE id=%s""",
                  (rr['transmitida_em'], (rr['local'] or '')[:30] or None, (rr['mensagem'] or '')[:255] or None, path, d['id']))
    _marcar_vigente(d['cpf'], d['exercicio'])
    return {'situacao': 'ok', 'id': d['id'], 'mensagem': 'Recibo lido.'}


def vincular(decl_id, cliente_id, manual=True):
    """Liga a declaração ao cadastro e leva os arquivos para a pasta da pessoa."""
    d = execute_query("SELECT * FROM irpf_declaracoes WHERE id=%s", (decl_id,), fetch=True, fetch_one=True)
    if not d:
        return False
    execute_query("UPDATE irpf_declaracoes SET cliente_id=%s, vinculo=%s WHERE id=%s",
                  (cliente_id, 'manual' if manual else 'auto', decl_id))
    pasta = pasta_destino(cliente_id, d['cpf'], d['exercicio'])
    svc = _svc()
    for col in ('arquivo_dec', 'arquivo_rec', 'arquivo_pdf'):
        p = d.get(col)
        if p and not p.startswith(pasta + '/'):
            novo = f"{pasta}/{os.path.basename(p)}"
            try:
                if svc.move_file(p, novo):
                    execute_query(f"UPDATE irpf_declaracoes SET {col}=%s WHERE id=%s", (novo, decl_id))
            except Exception:
                logger.exception('[irpf] não moveu %s', p)
    return True


def vincular_pendentes():
    """Declarações ainda sem cadastro: liga ao cadastro que apareceu ou cria o avulso (roda no cron)."""
    n = 0
    for d in execute_query("SELECT * FROM irpf_declaracoes WHERE cliente_id IS NULL", fetch=True) or []:
        cid = cliente_por_cpf(d['cpf']) or _avulso_automatico(d, None)
        if cid and vincular(d['id'], cid, manual=False):
            if not execute_query("SELECT 1 FROM clientes WHERE id=%s AND avulso=0", (cid,), fetch=True, fetch_one=True):
                execute_query("UPDATE irpf_declaracoes SET vinculo='avulso_novo' WHERE id=%s", (d['id'],))
            n += 1
    return n


def reler(decl_id):
    """Baixa o .DEC guardado e lê de novo (leiaute novo, correção do leitor)."""
    d = execute_query("SELECT * FROM irpf_declaracoes WHERE id=%s", (decl_id,), fetch=True, fetch_one=True)
    if not d or not d.get('arquivo_dec'):
        return {'situacao': 'erro', 'mensagem': 'Sem arquivo guardado para reler.'}
    conteudo = _svc().download_file(d['arquivo_dec'])
    if not conteudo:
        return {'situacao': 'erro', 'mensagem': 'Não consegui baixar o arquivo do Dropbox.'}
    r = leitor.resumo(leitor.ler(conteudo))
    r['crc_falhas'] = r.get('crc_falha') or 0
    r['aviso'] = _aviso(r)
    sets = [c for c in _CAMPOS if c not in ('cpf', 'cliente_id', 'vinculo', 'arquivo_base', 'arquivo_dec', 'hash_dec', 'origem', 'criado_por')]
    r['retificadora'] = int(bool(r['retificadora'])); r['completa'] = int(bool(r['completa'])); r['aproximado'] = int(bool(r['aproximado']))
    execute_query(f"UPDATE irpf_declaracoes SET {', '.join(c + '=%s' for c in sets)}, lido_em=NOW() WHERE id=%s",
                  tuple(r.get(c) for c in sets) + (decl_id,))
    _gravar_filhos(decl_id, r)
    _marcar_vigente(d['cpf'], d['exercicio'])
    return {'situacao': 'ok', 'mensagem': 'Declaração relida.'}


def excluir(decl_id):
    for t in ('irpf_rendimentos', 'irpf_dependentes', 'irpf_bens', 'irpf_dividas', 'irpf_pagamentos'):
        execute_query(f'DELETE FROM {t} WHERE declaracao_id=%s', (decl_id,))
    d = execute_query("SELECT cpf, exercicio FROM irpf_declaracoes WHERE id=%s", (decl_id,), fetch=True, fetch_one=True)
    execute_query("DELETE FROM irpf_declaracoes WHERE id=%s", (decl_id,))
    if d:
        _marcar_vigente(d['cpf'], d['exercicio'])


# ---------------------------------------------------------------------------
# _ENTRADA (Q-Colabore) — chamada pelo cron do roteador
# ---------------------------------------------------------------------------
def varrer_entrada(limite=200):
    """Lê os .DEC/.REC que o Q-Colabore deixou na _ENTRADA. Nunca levanta."""
    out = {'dec_novas': 0, 'dec_repetidas': 0, 'rec_ok': 0, 'rec_aguardando': 0, 'erros': 0, 'vinculadas': 0, 'detalhes': []}
    try:
        svc = _svc()
        base = svc.pasta_cert_novo()
        itens = svc.list_folder(base, recursive=False) or []
    except Exception:
        logger.exception('[irpf] não listei a _ENTRADA')
        return out
    arqs = [i for i in itens if i.get('is_file') and i.get('name', '').lower().endswith(EXTENSOES)]
    # .DEC antes dos .REC, para o recibo achar a declaração na mesma rodada
    arqs.sort(key=lambda i: (not i['name'].lower().endswith('.dec'), i['name']))
    for i in arqs[:limite]:
        nome, path = i['name'], i['path']
        try:
            conteudo = svc.download_file(path)
            if not conteudo:
                out['erros'] += 1; out['detalhes'].append((nome, 'não baixou')); continue
            if nome.lower().endswith('.dec'):
                r = gravar_dec(conteudo, nome, origem='colabore', origem_path=path)
                out['dec_novas' if r['situacao'] == 'nova' else 'dec_repetidas' if r['situacao'] == 'repetida' else 'erros'] += 1
                if r['situacao'] == 'erro':
                    _para_erros(svc, path, nome)
            else:
                r = gravar_rec(conteudo, nome, origem_path=path)
                out['rec_ok' if r['situacao'] == 'ok' else 'rec_aguardando' if r['situacao'] == 'aguardando' else 'erros'] += 1
                if r['situacao'] == 'erro':
                    _para_erros(svc, path, nome)
            out['detalhes'].append((nome[:3] + '***' + nome[11:], r['situacao']))
        except Exception as exc:
            out['erros'] += 1; out['detalhes'].append((nome, f'exceção {type(exc).__name__}'))
            logger.exception('[irpf] falha em %s', nome)
    try:
        out['vinculadas'] = vincular_pendentes()
    except Exception:
        logger.exception('[irpf] vincular_pendentes falhou')
    return out


def _para_erros(svc, path, nome):
    try:
        svc.move_file(path, svc._build_path('IRPF', 'ERROS', nome))
    except Exception:
        pass
