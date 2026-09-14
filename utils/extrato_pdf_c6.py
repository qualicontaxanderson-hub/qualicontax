# -*- coding: utf-8 -*-
"""Extrato em PDF do C6 Bank — o arquivo COMPLETO do C6 (14/09/2026).

O PROBLEMA
----------
O OFX do C6 grava o Pix enviado como ``TRANSF ENVIADA PIX`` e o boleto como
``Boleto`` — sem o nome de quem recebeu. Conferido no arquivo bruto em
14/09/2026: cada lançamento traz só tipo, data, valor, identificador e MEMO;
não existe campo de beneficiário. O extrato em PDF que o C6 manda por e-mail
("Seu extrato chegou") traz ``Pix enviado para FULANO`` e o nome do cedente
do boleto. Anderson: "sem a descrição do OFX é impossível"; e depois: "por
que não adianta eu mandar uma coisa que não é completa? No caso do C6 o
completo será o PDF".

ENTÃO, NO C6 O PDF VALE SOZINHO
-------------------------------
* ``parse_pdf_c6``: abre o PDF (protegido com os 6 primeiros dígitos do CPF
  do titular), lê titular, CPF, agência/conta e os lançamentos.
* ``processar_pdf``: para cada linha do PDF, procura o lançamento já gravado
  (mesma empresa, mesma conta, mesma data e valor). Achou → completa a
  descrição genérica. Não achou → CRIA o lançamento (origem 'pdf'). Depois
  rodam o detector de pares e as memorizações, como no OFX.
* Se o OFX do mesmo período chegar depois, ``processar_ofx`` reconhece a
  linha que o PDF criou (mesma data e valor) e só ENCAIXA nela o
  identificador do banco — não nasce um segundo lançamento. A descrição do
  PDF fica, porque é a melhor.

Os dois caminhos usam o mesmo casamento (``casar_por_data_valor``): primeiro
quem tem a MESMA descrição nos dois lados, depois o resto na ordem do dia —
o C6 lista PDF e OFX na mesma ordem (conferido nos dias com dois Pix de
R$ 2.431,50).

SENHA
-----
O C6 protege o PDF com os 6 primeiros dígitos do CPF do titular. Quem chama
passa os candidatos (CPF ou número no nome do arquivo → cadastro; senão, o
CPF de cada pessoa física cadastrada). A senha nunca vai para log.
"""
import logging
import re
from decimal import Decimal

logger = logging.getLogger(__name__)

BANCO_ID = '336'
BANCO_NOME = 'Banco C6 S.A.'

_RE_DATA = re.compile(r'^\d\d/\d\d$')
_RE_VALOR = re.compile(r'^(-?)R\$\s*([\d.]+),(\d\d)$')
_RE_MES = re.compile(r'^\S+ (\d{4}) \(\s*\d\d/\d\d/(\d{4}) - \d\d/\d\d/\d{4}\s*\)')
_RE_CPF = re.compile(r'(\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})')
_RE_CONTA = re.compile(r'Ag[êe]ncia:\s*(\d+)\s*.\s*Conta:\s*(\d+)', re.IGNORECASE)

#: Descrições do OFX do C6 que não dizem nada — só estas são trocadas.
_GENERICAS = ('TRANSF ENVIADA PIX', 'BOLETO', 'ENVIO DE TED', 'TRANSF ENVIADA')


#: O padrão (Anderson, 14/09/2026): OFX do C6 entra normalmente, mas a
#: pessoa fica sabendo que o arquivo completo é o PDF. Nunca bloqueia.
AVISO_OFX_C6 = ('O OFX do C6 não traz o nome de quem recebeu o Pix nem o cedente '
                'do boleto. O extrato COMPLETO do C6 é o PDF que chega por e-mail '
                '("Seu extrato chegou"): mande o PDF e ele completa estes '
                'lançamentos — ou, daqui em diante, mande só o PDF.')


def aviso_ofx(banco_id=None, banco_nome=None):
    """Texto do aviso quando o OFX é do C6; '' para os demais bancos."""
    cod = re.sub(r'\D', '', str(banco_id or '')).lstrip('0')
    if cod == BANCO_ID or 'C6' in (banco_nome or '').upper():
        return AVISO_OFX_C6
    return ''


#: Pendência do PDF que não abriu (Anderson, 14/09/2026: "teremos que ter um
#: lembrete, um local que nos mostre os arquivos que contêm senha e que não
#: estão no nosso cadastro"). Vai para a fila de Financeiro → Contas.
ROTULO_PDF_SENHA = 'PDF com senha'
MOTIVO_PDF_SENHA = ('Este PDF tem senha e nenhum CPF/CNPJ do cadastro abriu. '
                    'Informe abaixo o CPF/CNPJ do titular da conta (o C6 usa os 6 '
                    'primeiros dígitos), ou cadastre o titular como cliente e '
                    'clique em Tentar abrir. Renomear o arquivo com o CPF/CNPJ '
                    'também resolve.')
SENHAS_INFORMADAS = 'extrato_pdf_senhas'


def guardar_senha_pdf(nome_arquivo, documento):
    """Alguém informou o documento do titular deste arquivo: fica guardado
    pelo nome do arquivo, para a releitura (Vincular e ler) abrir de novo."""
    from utils.painel_cache import ler, guardar
    doc = re.sub(r'\D', '', str(documento or ''))
    if not doc or not nome_arquivo:
        return
    m, _ = ler(SENHAS_INFORMADAS)
    m = m or {}
    m[nome_arquivo.lower()] = doc
    guardar(SENHAS_INFORMADAS, m)


class PdfInvalido(Exception):
    """Não é um extrato do C6 (ou não deu para ler)."""


class PdfProtegido(PdfInvalido):
    """Está com senha e nenhum candidato abriu."""


class PdfOutroBanco(PdfInvalido):
    """Abriu, parece extrato bancário, mas não é o layout do C6."""


ROTULO_PDF_OUTRO = 'PDF de outro banco'
MOTIVO_PDF_OUTRO = ('É um extrato em PDF, mas não do C6 — em PDF eu só leio o do C6. '
                    'Deste banco mande o OFX: ele já vem completo, com o nome de quem '
                    'recebeu. Depois apague este PDF da pasta e o cartão some sozinho.')


def parece_extrato_bancario(texto):
    """Cheiro de extrato: agência, conta e saldo na primeira página. DANFE,
    boleto e cartão CNPJ não têm os três."""
    t = _norm(texto).replace('Ê', 'E').replace('Ã', 'A')
    return all(k in t for k in ('AGENCIA', 'CONTA', 'SALDO'))


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
def _abrir(raw, senhas):
    import pymupdf
    doc = pymupdf.open(stream=raw, filetype='pdf')
    if not doc.needs_pass:
        return doc
    vistas = set()
    for s in senhas or ():
        s = re.sub(r'\D', '', str(s or ''))[:6]
        if len(s) != 6 or s in vistas:
            continue
        vistas.add(s)
        if doc.authenticate(s):
            return doc
    raise PdfProtegido('PDF protegido por senha e nenhum CPF cadastrado abriu '
                       '(o C6 usa os 6 primeiros dígitos do CPF do titular).')


def _valor(txt):
    m = _RE_VALOR.match(txt.replace('\xa0', ' ').replace(' ', ''))
    if not m:
        return None
    v = Decimal(m.group(2).replace('.', '') + '.' + m.group(3))
    return -v if m.group(1) else v


def e_extrato_c6(texto_pag1):
    t = texto_pag1 or ''
    return ('Extrato exportado' in t and _RE_CONTA.search(t) is not None
            and 'Saldo' in t)


def parse_pdf_c6(raw, senhas=()):
    """Dict no mesmo espírito do ``parse_ofx``: banco, banco_id, conta, cpf,
    titular, lancamentos [{data, data_contabil, tipo, descricao, valor,
    documento=None, fitid=None}]."""
    doc = _abrir(raw, senhas)
    paginas = [p.get_text() for p in doc]
    if not paginas or not e_extrato_c6(paginas[0]):
        if paginas and parece_extrato_bancario(paginas[0]):
            raise PdfOutroBanco(MOTIVO_PDF_OUTRO)
        raise PdfInvalido('não é um extrato do C6.')
    cab = paginas[0]
    m = _RE_CONTA.search(cab)
    conta = f'{m.group(1)}/{m.group(2)}'
    mc = _RE_CPF.search(cab)
    cpf = re.sub(r'\D', '', mc.group(1)) if mc else None
    titular = None
    if mc:
        linha = next((l for l in cab.splitlines() if mc.group(1) in l), '')
        titular = linha.split(mc.group(1))[0].strip(' ·-•\xa0') or None

    lancs = []
    ano = None
    linhas = []
    for p in paginas:
        linhas.extend(p.splitlines())
    i, n = 0, len(linhas)
    while i < n:
        l = linhas[i].strip()
        mm = _RE_MES.match(l)
        if mm:
            ano = int(mm.group(2))
            i += 1
            continue
        if (_RE_DATA.match(l) and i + 3 < n and _RE_DATA.match(linhas[i + 1].strip())
                and ano):
            d_lanc, d_cont, tipo = l, linhas[i + 1].strip(), linhas[i + 2].strip()
            j = i + 3
            desc = []
            valor = None
            while j < n and j <= i + 8:
                v = _valor(linhas[j].strip())
                if v is not None:
                    valor = v
                    break
                desc.append(linhas[j].strip())
                j += 1
            if valor is None:
                i += 1
                continue
            dd, mmo = int(d_lanc[:2]), int(d_lanc[3:5])
            dc, mc2 = int(d_cont[:2]), int(d_cont[3:5])
            ano_c = ano + (1 if mc2 < mmo else 0)       # dez → jan vira o ano seguinte
            lancs.append({
                'data': f'{ano:04d}-{mmo:02d}-{dd:02d}',
                'data_contabil': f'{ano_c:04d}-{mc2:02d}-{dc:02d}',
                'tipo': tipo,
                'descricao': ' '.join(x for x in desc if x)[:500],
                'valor': valor,
                'documento': None,
                'fitid': None,
            })
            i = j + 1
            continue
        i += 1
    if not lancs:
        raise PdfInvalido('extrato do C6 sem nenhum lançamento legível.')
    return {'banco': BANCO_NOME, 'banco_id': BANCO_ID, 'conta': conta,
            'cpf': cpf, 'titular': titular, 'lancamentos': lancs}


# ---------------------------------------------------------------------------
# Casamento por (data, valor) — usado nos dois sentidos (PDF→OFX e OFX→PDF)
# ---------------------------------------------------------------------------
def _dec(v):
    return Decimal(str(v)).quantize(Decimal('0.01'))


def _norm(t):
    return ' '.join((t or '').upper().split())


def casar_por_data_valor(itens, rows):
    """{id(item): row} — cada ``row`` casa uma vez só.

    ``itens``: dicts com data (str ISO), valor, descricao e, se houver,
    data_contabil. ``rows``: dicts do banco com id, data (date), valor,
    descricao. Duas passadas: descrição igual primeiro, depois a ordem.
    """
    grupos = {}
    for x in rows:
        d = x['data'].isoformat() if hasattr(x['data'], 'isoformat') else str(x['data'])[:10]
        grupos.setdefault((d, _dec(x['valor'])), []).append(x)
    usados, par = set(), {}

    def _chaves(p):
        out = [(str(p['data'])[:10], _dec(p['valor']))]
        if p.get('data_contabil'):
            out.append((str(p['data_contabil'])[:10], _dec(p['valor'])))
        return out

    def _fila(chave):
        return [x for x in grupos.get(chave, []) if x['id'] not in usados]

    for p in itens:                                   # 1ª passada: descrição igual
        for chave in _chaves(p):
            x = next((x for x in _fila(chave)
                      if _norm(x['descricao']) == _norm(p.get('descricao'))), None)
            if x:
                usados.add(x['id']); par[id(p)] = x
                break
    for p in itens:                                   # 2ª passada: a ordem do dia
        if id(p) in par:
            continue
        for chave in _chaves(p):
            fila = _fila(chave)
            if fila:
                usados.add(fila[0]['id']); par[id(p)] = fila[0]
                break
    return par


def lancamentos_da_conta(empresa_id, conta, data_ini, data_fim, origem=None):
    """Linhas já gravadas da conta no período (conta comparada normalizada)."""
    from utils.db_helper import execute_query
    from utils.extrato_ingest import conta_normalizada
    cond = " AND origem = %s" if origem else ''
    params = (empresa_id, data_ini, data_fim) + ((origem,) if origem else ())
    rows = execute_query(
        'SELECT id, conta, data, valor, descricao, origem FROM extrato_lancamentos '
        f' WHERE empresa_id = %s AND data BETWEEN %s AND %s{cond} ORDER BY data, id',
        params, fetch=True) or []
    alvo = conta_normalizada(conta)
    return [x for x in rows if conta_normalizada(x['conta']) == alvo]


# ---------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------
def _generica(desc):
    d = (desc or '').strip().upper()
    return d == 'BOLETO' or any(d.startswith(g) for g in _GENERICAS if g != 'BOLETO')


def _nova_descricao(ofx_desc, pdf):
    """A descrição que o lançamento passa a ter, ou None se o PDF não ajuda."""
    d = (pdf.get('descricao') or '').strip()
    if not (ofx_desc or '').strip():
        return d[:500] or None                    # OFX veio vazio: qualquer coisa ajuda
    if not _generica(ofx_desc):
        return None
    if not d or d.upper() == (ofx_desc or '').strip().upper():
        return None
    tipo = (pdf.get('tipo') or '').strip().lower()
    nova = (f'Boleto · {d}' if tipo == 'pagamento'
            and (ofx_desc or '').strip().upper().startswith('BOLETO') else d)[:500]
    return None if nova == (ofx_desc or '').strip() else nova


def processar_pdf(empresa_id, dados, arquivo='', usuario_id=None, dry=True):
    """Completa o que já existe e cria o que falta. Devolve contagens."""
    from utils.db_helper import execute_query
    from utils.ofx_parser import chave_dedup
    from models.extrato_lancamento import ExtratoLancamento, ExtratoMemorizacao

    lancs = dados['lancamentos']
    r = {'no_pdf': len(lancs), 'casados': 0, 'completadas': 0, 'novos': 0,
         'repetidos': 0, 'ambiguos': 0, 'classificados': 0, 'travados': 0, 'ids': []}
    if not lancs:
        return r
    datas = sorted(set(l['data'] for l in lancs) | set(l['data_contabil'] for l in lancs))
    rows = lancamentos_da_conta(empresa_id, dados['conta'], datas[0], datas[-1])
    par = casar_por_data_valor(lancs, rows)

    trocas, faltam = [], []
    for p in lancs:
        x = par.get(id(p))
        if not x:
            faltam.append(p)
            continue
        r['casados'] += 1
        chave = (p['data'], p['valor'])
        if any(q is not p and (q['data'], q['valor']) == chave
               and q['descricao'] != p['descricao'] for q in lancs):
            r['ambiguos'] += 1
        nova = _nova_descricao(x['descricao'], p)
        if nova:
            trocas.append((x['id'], nova))
    r['completadas'] = len(trocas)
    r['repetidos'] = r['casados'] - len(trocas)

    # O que o PDF traz e ninguém gravou: nasce aqui, com a mesma chave de
    # idempotência do OFX sem FITID (data, valor, descrição, repetição).
    repet, novos = {}, []
    for p in faltam:
        p2 = {'data': p['data'], 'valor': p['valor'], 'descricao': p['descricao'],
              'documento': None, 'fitid': None,
              'tipo': 'credito' if p['valor'] >= 0 else 'debito'}
        k = (p2['data'], str(p2['valor']), p2['descricao'])
        n = repet.get(k, 0)
        repet[k] = n + 1
        novos.append((chave_dedup(empresa_id, dados['banco'], dados['conta'], p2, n), p2))
    ja = ExtratoLancamento.hashes_existentes([h for h, _ in novos]) if novos else set()
    novos = [(h, l) for h, l in novos if h not in ja]
    r['novos'] = len(novos)
    r['ids'] = [i for i, _ in trocas]
    if dry:
        return r

    for k in range(0, len(trocas), 300):
        lote = trocas[k:k + 300]
        casos = ' '.join(['WHEN %s THEN %s'] * len(lote))
        params = [v for i, d in lote for v in (i, d)] + [i for i, _ in lote]
        execute_query(
            f'UPDATE extrato_lancamentos SET descricao = CASE id {casos} END '
            f' WHERE id IN ({",".join(["%s"] * len(lote))})', tuple(params), fetch=False)
    ids_novos = []
    if novos:
        ExtratoLancamento.inserir_lote(novos, dados['banco'], dados['conta'], arquivo,
                                       usuario_id, empresa_id=empresa_id, origem='pdf')
        marks = ','.join(['%s'] * len(novos))
        ids_novos = [x['id'] for x in (execute_query(
            f'SELECT id FROM extrato_lancamentos WHERE hash_dedup IN ({marks})',
            tuple(h for h, _ in novos), fetch=True) or [])]
        # Mesma ordem do OFX: primeiro a trava dos pares, depois as regras.
        try:
            from utils.extrato_par import marcar
            r['travados'] = (marcar(dry=False) or {}).get('gravados', 0)
        except Exception:
            logger.exception('[extrato-pdf] detector de pares falhou; os '
                             'lançamentos VALEM e ficam sem trava nesta rodada.')
    r['ids'] = r['ids'] + ids_novos
    try:
        r['classificados'] = ExtratoMemorizacao.aplicar_em_ids(r['ids'])
    except Exception:
        logger.exception('[extrato-pdf] memorizações falharam; os lançamentos VALEM.')
    return r


def encaixar_ofx_em_pdf(empresa_id, banco, conta, novos):
    """O OFX chegou DEPOIS do PDF: casa cada lançamento novo do OFX com a
    linha que o PDF criou (origem 'pdf', mesma data e valor) e grava nela o
    FITID, o documento e a chave do OFX. Devolve (restantes, encaixados) —
    ``restantes`` são os (hash, lanc) que ainda precisam ser inseridos."""
    from utils.db_helper import execute_query
    if not novos:
        return novos, 0
    datas = sorted(str(l['data'])[:10] for _, l in novos)
    rows = lancamentos_da_conta(empresa_id, conta, datas[0], datas[-1], origem='pdf')
    if not rows:
        return novos, 0
    itens = [l for _, l in novos]
    par = casar_por_data_valor(itens, rows)
    restantes, n = [], 0
    for h, l in novos:
        x = par.get(id(l))
        if not x:
            restantes.append((h, l))
            continue
        execute_query(
            'UPDATE extrato_lancamentos SET fitid = %s, documento = %s, hash_dedup = %s, '
            "       origem = 'ofx' WHERE id = %s",
            (l.get('fitid'), l.get('documento'), h, x['id']), fetch=False)
        n += 1
    return restantes, n


def senhas_candidatas(nome_arquivo):
    """Documento (ou número do cadastro → documento) no nome do arquivo
    primeiro; depois o CPF/CNPJ de cada cliente cadastrado — pessoas físicas
    antes. Só os 6 primeiros dígitos contam; testar 300 é questão de
    milissegundos, o PDF está na memória."""
    from utils.db_helper import execute_query
    from utils.extrato_ingest import numero_empresa_do_nome
    out = []
    try:                                     # documento informado na pendência
        from utils.painel_cache import ler
        m, _ = ler(SENHAS_INFORMADAS)
        d = (m or {}).get((nome_arquivo or '').lower())
        if d:
            out.append(d)
    except Exception:
        pass
    num = numero_empresa_do_nome(nome_arquivo)
    if num:
        e = execute_query('SELECT cpf_cnpj FROM clientes WHERE numero_cliente = %s',
                          (num,), fetch=True, fetch_one=True)
        if e and e.get('cpf_cnpj'):
            out.append(re.sub(r'\D', '', e['cpf_cnpj']))
    so = re.sub(r'\D', ' ', nome_arquivo or '')
    out += [t for t in so.split() if len(t) in (11, 14)]
    docs = execute_query(
        "SELECT REPLACE(REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'-',''),'/',''),' ','') d "
        "  FROM clientes WHERE cpf_cnpj IS NOT NULL HAVING LENGTH(d) IN (11, 14) "
        " ORDER BY LENGTH(d), id", fetch=True) or []
    out += [p['d'] for p in docs]
    return out
