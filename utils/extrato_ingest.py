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
    """'498 - c6 julho.ofx' -> '498'. Devolve None se não houver número na frente."""
    base = os.path.basename(nome_arquivo)
    m = re.match(r'^\s*(\d{1,6})\s*[-_]', base)
    return m.group(1) if m else None


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
    """A conta cadastrada (com a empresa dona), ou None se for desconhecida."""
    from utils.db_helper import execute_query
    cod = re.sub(r'\D', '', str(banco_id or '')).lstrip('0')
    norm = conta_normalizada(conta)
    if not norm:
        return None
    r = execute_query(
        """SELECT c.*, cl.numero_cliente, cl.nome_razao_social, cl.cpf_cnpj
             FROM fin_contas c
             JOIN clientes cl ON cl.id = c.empresa_id
            WHERE c.ativo = 1 AND c.conta_norm = %s
              AND (%s = '' OR c.banco_id IS NULL
                   OR TRIM(LEADING '0' FROM c.banco_id) = %s)
            LIMIT 1""",
        (norm, cod, cod), fetch=True, fetch_one=True)
    return r


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
    em 1", "conta desconhecida", "CONTRADIÇÃO: o nome diz 100 mas a conta é
    da 1".
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

    # CONFERÊNCIA: o número no nome não manda, mas se ele DISCORDA da conta é
    # porque alguém errou — e a conta é a fonte confiável. Para tudo e avisa.
    if num and str(reg['numero_cliente'] or '') != str(num):
        return None, (f'CONTRADIÇÃO: o nome do arquivo diz empresa {num}, mas a '
                      f'conta {conta} é de {reg["numero_cliente"]} — '
                      f'{reg["nome_razao_social"]}. Nada foi lançado. Corrija o '
                      f'nome do arquivo ou o cadastro da conta.')

    como = f'conta {conta} cadastrada em {reg["numero_cliente"]}'
    if num:
        como += f' (o número {num} no nome confere)'
    return cliente, como


def processar_ofx(caminho, empresa_id, usuario_id=None):
    """Lê, grava os lançamentos novos e devolve o resumo — SEM mover nada.

    Quem move é quem chamou, e só depois de confirmar que gravou.
    """
    from models.extrato_lancamento import ExtratoLancamento, ExtratoMemorizacao
    from utils.db_helper import execute_query
    from utils.ofx_parser import parse_ofx, chave_dedup

    dados = parse_ofx(open(caminho, 'rb').read())
    lancs = dados['lancamentos']

    repet, candidatos = {}, []
    for l in lancs:
        k = (l['data'], str(l['valor']), l['descricao'], l['documento'])
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
    novos = [(h, l) for h, l in unicos if h not in ja]
    auto = 0
    if novos:
        ExtratoLancamento.inserir_lote(
            novos, dados['banco'], dados['conta'], os.path.basename(caminho),
            usuario_id, empresa_id=empresa_id)
        marks = ','.join(['%s'] * len(novos))
        ids = execute_query(
            f'SELECT id FROM extrato_lancamentos WHERE hash_dedup IN ({marks})',
            tuple(h for h, _ in novos), fetch=True) or []
        auto = ExtratoMemorizacao.aplicar_em_ids([r['id'] for r in ids])

    return {
        'banco': banco_curto(dados.get('banco_id'), dados['banco']),
        'banco_bruto': dados['banco'], 'conta': dados['conta'],
        'saldo': dados.get('saldo'),
        'total': len(lancs), 'novos': len(novos),
        'repetidos': len(unicos) - len(novos), 'classificados': auto,
        'datas': [l['data'] for l in lancs],
    }
