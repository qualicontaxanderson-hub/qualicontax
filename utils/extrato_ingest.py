# -*- coding: utf-8 -*-
"""Leitura automática de extrato pela pasta (_ENTRADA) — 21/08/2026.

O usuário NÃO importa nada: salva o arquivo na pasta (veio do WhatsApp, do
e-mail, do internet banking), o Q-Colabore leva para a ``_ENTRADA`` do Dropbox
e este motor faz o resto. Decisão do Anderson em 20/08/2026.

Quem descobre o quê:

* **empresa** — pela CONTA BANCÁRIA de dentro do arquivo (``fin_contas``).
  Todo OFX traz o número da conta, e conta é impressão digital: não se digita,
  não se erra. O número no nome do arquivo (``1 - c6 julho.ofx``) é
  CONFERÊNCIA: se ele discordar do dono da conta, o arquivo é RECUSADO com o
  aviso da contradição — em vez de obedecer a quem digitou errado. Pedido do
  Anderson em 21/08/2026: "a ameba pode colocar 100 no extrato da empresa 1 e
  fazer toda a contabilidade sem notar".
  Conta desconhecida NÃO vira palpite: fica parada até alguém dizer de quem é
  (uma vez só). Adivinhar pelo CNPJ dentro do arquivo foi TESTADO e reprovado
  no mesmo dia — o extrato do Cora da Qualicontax tinha mais o CPF do Anderson
  do que o CNPJ dela, e o palpite mandaria para a empresa errada.
* **banco e conta** — o CONTEÚDO. Provado com arquivo real dos 5 bancos:
  ``<ORG>Banco C6 S.A.``, ``Cora SCD SA``, ``Efí``, ``0237`` (Bradesco),
  ``CCPI DO CERRADO DE GO`` (Sicredi). Nome de arquivo não decide nada aqui.
* **senha** (PDF/ZIP trancado) — DERIVADA do CNPJ/CPF do cadastro: tenta as
  fatias conhecidas (6 primeiros, 5, 8, inteiro...). Abriu, memoriza a regra
  para aquele banco; não abriu, fica pendente para alguém digitar UMA vez —
  e aí a regra é descoberta comparando o que foi digitado com o documento.

Depois de importar, o arquivo é ARQUIVADO na pasta da empresa com nome que
se explica: ``{Banco} - {conta} - {período}.{ext}``.

NADA é apagado da _ENTRADA sem sucesso confirmado: arquivo que falhou fica
onde está, com o motivo no histórico.
"""
import logging
import os
import re
from datetime import date

logger = logging.getLogger(__name__)

# Fatias de CNPJ/CPF que os bancos costumam usar como senha. A ordem importa:
# a primeira que abrir vira a regra memorizada.
FATIAS_SENHA = (
    ('cnpj6', lambda d: d[:6]),
    ('cnpj5', lambda d: d[:5]),
    ('cnpj8', lambda d: d[:8]),
    ('cpf5', lambda d: d[:5]),
    ('cpf6', lambda d: d[:6]),
    ('doc_inteiro', lambda d: d),
    ('cnpj4', lambda d: d[:4]),
    ('cnpj_raiz_dv', lambda d: d[:8] + d[12:14] if len(d) >= 14 else ''),
)

# Codigo do banco (BANKID) -> nome curto para pasta e tela. Conferido nos
# arquivos reais em 21/08/2026: o Sicredi manda o nome da COOPERATIVA no ORG
# ('CCPI DO CERRADO DE GO'), que varia por praca — o codigo nao varia.
BANCOS = {
    '1': 'Banco do Brasil', '33': 'Santander', '41': 'Banrisul',
    '77': 'Inter', '104': 'Caixa', '208': 'BTG', '237': 'Bradesco',
    '260': 'Nubank', '290': 'PagBank', '318': 'BMG', '336': 'C6',
    '341': 'Itau', '364': 'EFI', '380': 'PicPay', '403': 'Cora',
    '422': 'Safra', '461': 'Asaas', '655': 'Votorantim', '745': 'Citi',
    '748': 'Sicredi', '756': 'Sicoob',
}


def banco_curto(banco_id, nome_bruto=None):
    """'748' -> 'Sicredi'. Sem codigo conhecido, limpa o nome que veio."""
    cod = re.sub(r'\D', '', str(banco_id or '')).lstrip('0')
    if cod and cod in BANCOS:
        return BANCOS[cod]
    bruto = (nome_bruto or '').strip()
    if bruto and not bruto.isdigit():
        return re.sub(r'\s+(S\.?A\.?|SCD|LTDA).*$', '', bruto,
                      flags=re.IGNORECASE).strip() or bruto
    return f'Banco {cod}' if cod else 'Banco'


_MES = {1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun',
        7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'}


def numero_empresa_do_nome(nome_arquivo):
    """Número do cadastro da empresa indicado no NOME do arquivo, ou None.

    Duas formas, na mesma regra do certificado (pedido de 14/09/2026):
      * número do cadastro no começo: '498 - c6 julho.ofx' -> '498';
      * CPF (11 dígitos) ou CNPJ (14) em qualquer parte, isolado de outros
        dígitos: 'c6 setembro 12345678901.ofx' -> o número da empresa que tem
        esse documento no cadastro. Documento que não é de nenhuma empresa
        cadastrada é ignorado (uma data 'AAAAMMDDhhmmss' de 14 dígitos, por
        exemplo, não casa com ninguém e não atrapalha).
    Devolve SEMPRE o número do cadastro, para quem compara com a conta
    continuar igual.
    """
    base = os.path.basename(nome_arquivo)
    sem_ext = os.path.splitext(base)[0]
    # '5000.ofx', '5000 - c6.ofx', '5000_c6.ofx', '5000 c6.ofx': o numero e o nome
    # inteiro (sem extensao) ou vem seguido de separador — igual ao certificado.
    m = re.match(r'^\s*(\d{1,6})(?:\s*[-_ .]|\s*$)', sem_ext)
    if m:
        return m.group(1)
    for doc in re.findall(r'(?<!\d)(\d{11}|\d{14})(?!\d)', sem_ext):
        from utils.db_helper import execute_query
        e = execute_query(
            "SELECT numero_cliente FROM clientes "
            " WHERE REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-','') = %s "
            "   AND COALESCE(numero_cliente,'') <> '' LIMIT 1", (doc,),
            fetch=True, fetch_one=True)
        if e:
            return str(e['numero_cliente'])
    return None


def _so_digitos(v):
    return re.sub(r'\D', '', str(v or ''))


def senhas_candidatas(documento):
    """[(regra, senha)] a tentar, na ordem, para este CNPJ/CPF."""
    d = _so_digitos(documento)
    if not d:
        return []
    out, vistas = [], set()
    for nome, fn in FATIAS_SENHA:
        try:
            s = fn(d)
        except Exception:
            continue
        if s and s not in vistas:
            vistas.add(s)
            out.append((nome, s))
    return out


def descobrir_regra(senha_digitada, documento):
    """Alguém digitou a senha: qual fatia do documento ela é?

    É assim que o sistema aprende sem ninguém manter tabela de regra —
    devolve o nome da regra, ou None se a senha não sair do documento.
    """
    d = _so_digitos(documento)
    alvo = str(senha_digitada or '').strip()
    if not d or not alvo:
        return None
    for nome, fn in FATIAS_SENHA:
        try:
            if fn(d) == alvo:
                return nome
        except Exception:
            continue
    return None


def rotulo_periodo(datas):
    """['2026-01-02', ...] -> 'Jan-Jul.2026' | 'Jul.2026' | 'Dez.2025-Jul.2026'."""
    ds = sorted(d for d in datas if d)
    if not ds:
        return ''
    a, b = ds[0], ds[-1]
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    if (ya, ma) == (yb, mb):
        return f'{_MES[ma]}.{ya}'
    if ya == yb:
        return f'{_MES[ma]}-{_MES[mb]}.{ya}'
    return f'{_MES[ma]}.{ya}-{_MES[mb]}.{yb}'


def _limpo(txt, tam=40):
    """Pedaço de nome de arquivo sem caractere proibido."""
    t = re.sub(r'[\\/:*?"<>|]', '', str(txt or '')).strip()
    return t[:tam] or 'SEM NOME'


def nome_arquivo_final(banco, conta, datas, extensao):
    """'C6 - 211346179 - Jan-Jul.2026.ofx' — quem abre a pasta sabe o que é."""
    partes = [_limpo(banco or 'BANCO')]
    if conta:
        # '1/211346179' -> '211346179' (a parte antes da barra é o tipo)
        c = str(conta).split('/')[-1]
        partes.append(_limpo(c, 24))
    per = rotulo_periodo(datas)
    if per:
        partes.append(per)
    ext = (extensao or '').lstrip('.').lower() or 'dat'
    return ' - '.join(partes) + '.' + ext


def pasta_destino(numero_cliente, razao_social, ano):
    """EMPRESAS/{nº - razão}/FINANCEIRO/EXTRATOS/{ano}.

    Ano só (sem mês): um extrato costuma atravessar meses, e o período já
    está no NOME do arquivo — pasta por mês espalharia o mesmo extrato.
    """
    from utils.dropbox_sync import _service, _build_empresa_folder
    pasta = _build_empresa_folder(numero_cliente, razao_social)
    return _service._build_path('EMPRESAS', pasta, 'FINANCEIRO', 'EXTRATOS', str(ano))


def conta_normalizada(conta):
    """'1/545806-4' e '5458064' viram a mesma chave — o banco varia a grafia."""
    bruta = str(conta or '').split('/')[-1]
    return re.sub(r'\D', '', bruta).lstrip('0') or bruta


def achar_conta(banco_id, conta):
    """A conta cadastrada (com a empresa dona), ou None se for desconhecida.

    Tolerante à grafia (14/09/2026): o OFX do Bradesco diz '16865', o PDF e
    o CSV dizem '16865-3' (com o dígito); o OFX do Sicredi diz
    '39500000000156390', o PDF diz 'Conta: 15639-0'. Mesmo banco e um número
    terminando no outro (mínimo 5 dígitos) é a mesma conta.
    """
    from utils.db_helper import execute_query
    cod = re.sub(r'\D', '', str(banco_id or '')).lstrip('0')
    norm = conta_normalizada(conta)
    if not norm:
        return None
    sql = """SELECT c.*, cl.numero_cliente, cl.nome_razao_social, cl.cpf_cnpj
               FROM fin_contas c
               JOIN clientes cl ON cl.id = c.empresa_id
              WHERE c.ativo = 1 AND c.conta_norm = %s
                AND (%s = '' OR c.banco_id IS NULL
                     OR TRIM(LEADING '0' FROM c.banco_id) = %s)
              LIMIT 1"""
    r = execute_query(sql, (norm, cod, cod), fetch=True, fetch_one=True)
    if r or not cod or len(norm) < 5:
        return r
    cands = execute_query(
        """SELECT c.*, cl.numero_cliente, cl.nome_razao_social, cl.cpf_cnpj
             FROM fin_contas c
             JOIN clientes cl ON cl.id = c.empresa_id
            WHERE c.ativo = 1 AND TRIM(LEADING '0' FROM COALESCE(c.banco_id, '')) = %s""",
        (cod,), fetch=True) or []
    ok = [c for c in cands if len(c['conta_norm'] or '') >= 5
          and (norm.endswith(c['conta_norm']) or c['conta_norm'].endswith(norm)
               or norm[:-1] == c['conta_norm'] or c['conta_norm'][:-1] == norm)]
    return ok[0] if len(ok) == 1 else None


def registrar_conta(empresa_id, banco_id, banco_nome, conta, agencia=None,
                    apelido=None, usuario_id=None):
    """Alguém disse de quem é a conta: memoriza e nunca mais pergunta."""
    from utils.db_helper import execute_query
    return execute_query(
        'INSERT INTO fin_contas (empresa_id, banco_id, banco_nome, agencia, '
        'conta, conta_norm, apelido, criado_por) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
        (empresa_id, re.sub(r'\D', '', str(banco_id or '')) or None, banco_nome,
         agencia, conta, conta_normalizada(conta), apelido, usuario_id))


def identificar_empresa(caminho_ou_nome, banco_id=None, conta=None,
                       banco_nome=None):
    """(cliente, motivo) — decidido pela CONTA, conferido pelo nome.

    ``cliente`` é a empresa dona da conta (ou None). ``motivo`` conta como foi
    decidido, e é o texto que aparece no histórico: "conta 16865 cadastrada
    em 1", "conta desconhecida", "conta 16865 cadastrada em 1 — ATENÇÃO: o
    nome do arquivo diz 100, ignorado; a conta manda".
    """
    from utils.db_helper import execute_query

    reg = achar_conta(banco_id, conta)
    num = numero_empresa_do_nome(caminho_ou_nome)

    if not reg:
        return None, (f'conta desconhecida ({banco_nome or banco_id or "banco"} '
                      f'· {conta}). Diga de quem é ela UMA vez, na tela do '
                      f'Extrato, e o sistema nunca mais pergunta.')

    cliente = {'id': reg['empresa_id'], 'numero_cliente': reg['numero_cliente'],
               'nome_razao_social': reg['nome_razao_social'],
               'cpf_cnpj': reg['cpf_cnpj']}

    # A CONTA MANDA. Decisão do Anderson em 14/09/2026: depois de cadastrada,
    # a conta é lida com qualquer nome de arquivo — número errado no nome não
    # segura o extrato (até então travava com "CONTRADIÇÃO"; ficou confuso e
    # pouco prático). O número/CPF no nome só importa no PRIMEIRO arquivo, para
    # a pendência aparecer na tela da empresa certa; se alguém confirmar a conta
    # na empresa errada, apaga o cadastro da conta e recomeça. A discordância
    # fica registrada no histórico, para quem quiser conferir.
    como = f'conta {conta} cadastrada em {reg["numero_cliente"]}'
    if num and str(reg['numero_cliente'] or '') != str(num):
        como += (f' — ATENÇÃO: o nome do arquivo diz {num}, ignorado; '
                 f'a conta manda')
    elif num:
        como += f' (o número {num} no nome confere)'
    return cliente, como


def processar_ofx(caminho, empresa_id, usuario_id=None):
    """Lê o OFX e grava pelo núcleo comum — SEM mover nada."""
    from utils.ofx_parser import parse_ofx
    dados = parse_ofx(open(caminho, 'rb').read())
    return processar_lancamentos(dados, empresa_id, os.path.basename(caminho),
                                 usuario_id=usuario_id, origem='ofx')


def fitids_existentes(empresa_id, conta, fitids):
    """FITIDs já gravados nesta conta (conta comparada normalizada). Existe
    porque OFX e CSV do mesmo banco podem escrever o NOME do banco diferente
    e a chave de deduplicação inclui esse nome — o identificador do banco é
    a verdade final."""
    from utils.db_helper import execute_query
    achados = set()
    fitids = [f for f in fitids if f]
    if not fitids:
        return achados
    alvo = conta_normalizada(conta)
    for i in range(0, len(fitids), 300):
        fatia = fitids[i:i + 300]
        marks = ','.join(['%s'] * len(fatia))
        rows = execute_query(
            f'SELECT fitid, conta FROM extrato_lancamentos '
            f' WHERE empresa_id = %s AND fitid IN ({marks})',
            (empresa_id, *fatia), fetch=True) or []
        achados.update(r['fitid'] for r in rows if conta_normalizada(r['conta']) == alvo)
    return achados


def documentos_existentes(empresa_id, conta, documentos):
    """Documentos (Dcto do Bradesco, protocolo da Efí) já gravados nesta conta,
    comparados sem zeros à esquerda — o OFX da Efí grava '000004399469655' e o
    CSV diz '4399469655'."""
    from utils.db_helper import execute_query
    docs = list(dict.fromkeys((d or '').strip().lstrip('0') for d in documentos if d and len((d or '').strip().lstrip('0')) >= 4))
    achados = set()
    if not docs:
        return achados
    alvo = conta_normalizada(conta)
    for i in range(0, len(docs), 300):
        fatia = docs[i:i + 300]
        marks = ','.join(['%s'] * len(fatia))
        rows = execute_query(
            f"SELECT documento, conta FROM extrato_lancamentos "
            f" WHERE empresa_id = %s AND TRIM(LEADING '0' FROM COALESCE(documento, '')) IN ({marks})",
            (empresa_id, *fatia), fetch=True) or []
        achados.update((r['documento'] or '').lstrip('0') for r in rows if conta_normalizada(r['conta']) == alvo)
    return achados


def processar_lancamentos(dados, empresa_id, arquivo, usuario_id=None, origem='ofx'):
    """Grava os lançamentos novos de um arquivo já lido (OFX ou CSV) e devolve
    o resumo — SEM mover nada. Quem move é quem chamou, depois de gravar.

    Idempotência em três camadas: a chave de deduplicação (FITID ou
    data+valor+descrição+repetição), o FITID já gravado na mesma conta
    (OFX × CSV), e o encaixe em linha que um PDF criou sem FITID.
    """
    from models.extrato_lancamento import ExtratoLancamento, ExtratoMemorizacao
    from utils.db_helper import execute_query
    from utils.ofx_parser import chave_dedup

    lancs = dados['lancamentos']
    repet, candidatos = {}, []
    for l in lancs:
        k = (l['data'], str(l['valor']), l['descricao'], l.get('documento'))
        n = repet.get(k, 0)
        repet[k] = n + 1
        candidatos.append((chave_dedup(empresa_id, dados['banco'],
                                       dados['conta'], l, n), l))

    vistos, unicos = set(), []
    for h, l in candidatos:
        if h in vistos:
            continue
        vistos.add(h)
        unicos.append((h, l))

    ja = ExtratoLancamento.hashes_existentes([h for h, _ in unicos])
    ja_fitid = fitids_existentes(empresa_id, dados['conta'], [l.get('fitid') for _, l in unicos])
    ja_doc = documentos_existentes(empresa_id, dados['conta'], [l.get('documento') for _, l in unicos])
    novos = [(h, l) for h, l in unicos if h not in ja
             and not (l.get('fitid') and l['fitid'] in ja_fitid)
             and not (l.get('documento') and (l['documento'] or '').lstrip('0') in ja_doc)]
    # O PDF pode ter chegado ANTES e criado a linha sem FITID: o arquivo com
    # FITID só encaixa nela — a descrição do PDF, que tem o nome, fica.
    encaixados = 0
    if novos:
        from utils.extrato_pdf_c6 import encaixar_ofx_em_pdf
        novos, encaixados = encaixar_ofx_em_pdf(empresa_id, dados['banco'],
                                                dados['conta'], novos)
    auto, par = 0, {}
    if novos:
        ExtratoLancamento.inserir_lote(
            novos, dados['banco'], dados['conta'], arquivo,
            usuario_id, empresa_id=empresa_id, origem=origem)
        marks = ','.join(['%s'] * len(novos))
        ids = execute_query(
            f'SELECT id FROM extrato_lancamentos WHERE hash_dedup IN ({marks})',
            tuple(h for h, _ in novos), fetch=True) or []

        # A ORDEM IMPORTA: primeiro TRAVAR as pontas de transferência, só
        # depois deixar as regras classificarem (ver histórico no git).
        try:
            from utils.extrato_par import marcar
            par = marcar(dry=False)
        except Exception:
            logger.exception('[extrato] detector de pares falhou; os '
                             'lançamentos VALEM e ficam sem trava nesta rodada.')
            par = {}
        auto = ExtratoMemorizacao.aplicar_em_ids([r['id'] for r in ids])

    return {
        'banco': banco_curto(dados.get('banco_id'), dados['banco']),
        'banco_bruto': dados['banco'], 'conta': dados['conta'],
        'saldo': dados.get('saldo'),
        'total': len(lancs), 'novos': len(novos),
        'repetidos': len(unicos) - len(novos) - encaixados, 'classificados': auto,
        'encaixados': encaixados,
        'travados': (par or {}).get('gravados', 0),
        'datas': [l['data'] for l in lancs],
    }


def conta_da_empresa_no_banco(empresa_id, banco_id):
    """A conta cadastrada da empresa nesse banco, se for UMA só (CSV não diz a
    conta: o número no nome do arquivo diz a empresa, o layout diz o banco).
    Devolve (registro, quantas)."""
    from utils.db_helper import execute_query
    cod = re.sub(r'\D', '', str(banco_id or '')).lstrip('0')
    rows = execute_query(
        "SELECT * FROM fin_contas WHERE empresa_id = %s AND ativo = 1 "
        "  AND TRIM(LEADING '0' FROM COALESCE(banco_id, '')) = %s",
        (empresa_id, cod), fetch=True) or []
    return (rows[0] if len(rows) == 1 else None), len(rows)


def empresa_pelo_nome_no_arquivo(nome_arquivo):
    """A empresa do grupo cujo apelido (ou começo da razão) está no nome do
    arquivo — 'cora qualicontax_...csv' é da Qualicontax. Só o grupo do
    Financeiro, e só apelidos com 4+ letras, para não casar por acaso."""
    from utils.extrato_par import empresas_do_grupo
    nome = re.sub(r'[^a-z0-9]+', ' ', str(nome_arquivo or '').lower())
    achou = []
    for e in empresas_do_grupo():
        for ap in ((e.get('apelido') or ''), (e.get('nome') or '')[:12]):
            ap = re.sub(r'[^a-z0-9]+', ' ', ap.lower()).strip()
            if len(ap) >= 4 and ap in nome:
                achou.append(e['cliente_id'])
                break
    return achou[0] if len(set(achou)) == 1 else None


def conta_pelos_documentos(lancamentos):
    """(empresa_id, conta) da conta onde esses documentos/fitids JÁ estão
    gravados — o Efí não diz a conta no CSV nem no PDF, mas o protocolo de
    cada lançamento é o mesmo que o OFX gravou em ``documento``."""
    from utils.db_helper import execute_query
    ids = []
    for l in lancamentos:
        for k in ('fitid', 'documento'):
            v = (l.get(k) or '').strip().lstrip('0')
            if len(v) >= 5:
                ids.append(v)
    ids = list(dict.fromkeys(ids))[:400]
    if len(ids) < 3:
        return None
    marks = ','.join(['%s'] * len(ids))
    rows = execute_query(
        f"SELECT empresa_id, conta, COUNT(*) AS n FROM extrato_lancamentos "
        f" WHERE TRIM(LEADING '0' FROM COALESCE(documento, '')) IN ({marks}) "
        f"    OR TRIM(LEADING '0' FROM COALESCE(fitid, '')) IN ({marks}) "
        f" GROUP BY empresa_id, conta ORDER BY n DESC", tuple(ids) * 2, fetch=True) or []
    if not rows or int(rows[0]['n']) < 3:
        return None
    if len(rows) > 1 and int(rows[1]['n']) * 4 > int(rows[0]['n']):
        return None                               # ambíguo entre duas contas
    return rows[0]['empresa_id'], rows[0]['conta']


def identificar_arquivo(nome_arquivo, previa):
    """(cliente, conta_str, motivo) para QUALQUER formato, na ordem de força:
    1. a conta escrita no arquivo (OFX, C6/Bradesco CSV, PDFs com capa);
    2. documentos/fitids já gravados (Efí CSV/PDF);
    3. número ou nome da empresa no arquivo + a única conta dela no banco.
    Sem nada disso, (None, None, motivo) — vira pendência."""
    from utils.db_helper import execute_query
    banco_id = previa.get('banco_id')
    banco = banco_curto(banco_id, previa.get('banco'))
    if previa.get('conta'):
        cli, motivo = identificar_empresa(nome_arquivo, banco_id=banco_id,
                                          conta=previa.get('conta'), banco_nome=banco)
        if cli:
            reg = achar_conta(banco_id, previa.get('conta'))
            conta_str = (f"{reg['agencia']}/{reg['conta']}" if reg and reg.get('agencia') else (reg or {}).get('conta')) or previa['conta']
            return cli, conta_str, motivo
        return None, None, motivo
    achado = conta_pelos_documentos(previa.get('lancamentos') or [])
    if achado:
        emp_id, conta_str = achado
        cli = execute_query('SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes WHERE id = %s',
                            (emp_id,), fetch=True, fetch_one=True)
        if cli:
            return cli, conta_str, f'conta {conta_str} reconhecida pelos documentos já lançados'
    num = numero_empresa_do_nome(nome_arquivo)
    emp_id = None
    if num:
        cli = execute_query('SELECT id FROM clientes WHERE numero_cliente = %s', (num,), fetch=True, fetch_one=True)
        emp_id = (cli or {}).get('id')
        if not emp_id:
            return None, None, f'o nome do arquivo diz empresa {num}, que não existe no cadastro.'
    else:
        emp_id = empresa_pelo_nome_no_arquivo(nome_arquivo)
    if not emp_id:
        return None, None, (f'{banco}: o arquivo não diz de que conta é. Coloque o número ou o '
                            'nome da empresa no nome do arquivo — ou mande o OFX.')
    reg, n = conta_da_empresa_no_banco(emp_id, banco_id)
    cli = execute_query('SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes WHERE id = %s',
                        (emp_id,), fetch=True, fetch_one=True)
    if not reg:
        if n == 0:
            return None, None, (f'{banco} da empresa {cli["numero_cliente"]}: a conta ainda não está '
                                'cadastrada. Diga qual é UMA vez e o sistema lê.')
        return None, None, (f'a empresa {cli["numero_cliente"]} tem {n} contas no {banco} e o arquivo '
                            'não diz qual é. Deixe só uma ativa ou mande o OFX.')
    conta_str = f"{reg['agencia']}/{reg['conta']}" if reg.get('agencia') else reg['conta']
    return cli, conta_str, f'{banco}: empresa {cli["numero_cliente"]} pelo nome do arquivo, conta {conta_str} do cadastro'


def identificar_empresa_csv(nome_arquivo, banco_id, banco_nome=None):
    """(cliente, conta_str, motivo) para um CSV: empresa pelo número/CPF no
    nome do arquivo; conta = a única da empresa nesse banco."""
    from utils.db_helper import execute_query
    num = numero_empresa_do_nome(nome_arquivo)
    if not num:
        return None, None, (f'CSV do {banco_nome or "banco"}: o arquivo não diz de que conta é. '
                            'Coloque o número da empresa (ou o CPF/CNPJ) no nome do arquivo.')
    cli = execute_query(
        'SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes '
        ' WHERE numero_cliente = %s', (num,), fetch=True, fetch_one=True)
    if not cli:
        return None, None, f'o nome do arquivo diz empresa {num}, que não existe no cadastro.'
    reg, n = conta_da_empresa_no_banco(cli['id'], banco_id)
    if not reg:
        if n == 0:
            return None, None, (f'CSV do {banco_nome or "banco"} da empresa {num}: a conta ainda '
                                'não está cadastrada. Diga qual é UMA vez e o sistema lê.')
        return None, None, (f'a empresa {num} tem {n} contas no {banco_nome or "banco"} e o CSV '
                            'não diz qual é. Cadastre só uma como ativa ou mande o OFX.')
    conta_str = f"{reg['agencia']}/{reg['conta']}" if reg.get('agencia') else reg['conta']
    return cli, conta_str, f'CSV: empresa {num} pelo nome do arquivo, conta {conta_str} do cadastro'
