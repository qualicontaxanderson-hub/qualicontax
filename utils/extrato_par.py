# -*- coding: utf-8 -*-
"""Acha as duas pontas de uma transferência entre contas do grupo (11/09/2026).

O PROBLEMA, nas palavras do Anderson
------------------------------------
"Saiu do EFI 5.000 e no Sicredi entrou 5.000, aí já era para aparecer para
aprovar, porque é óbvio a transferência." E a exigência que vem junto: "não
somos adivinhos, temos que ter certeza".

Este módulo SÓ ACHA. Não grava, não classifica, não libera — quem decide é a
tela de aprovação. Nada aqui aplica nada sozinho.

O QUE FAZ UM PAR
----------------
Quatro condições, todas obrigatórias:

1. **Valores opostos e iguais em módulo** — uma saída e uma entrada.
2. **Contas DIFERENTES** — mesma conta não é transferência, é lançamento duplo.
3. **Contas do GRUPO** — as duas pertencem a empresas de ``fin_empresas``
   (hoje QUALICONTAX, BRILHO e ANDERSON PF). Dinheiro que vai para fora do
   grupo não é transferência: é despesa de verdade.
4. **Data de até 1 dia de diferença** — medido no extrato real: de 216 pares,
   163 caem no MESMO dia e 19 com 1 dia. Os de 2 e 3 dias eram quase todos
   coincidência de valor redondo (R$ 100, R$ 750), e OITO tinham a entrada
   ANTES da saída, o que não existe. Por isso a janela é 1, e não 3.

5. **O nome ou o CNPJ de uma empresa do grupo na descrição** — escrito pelo
   PRÓPRIO BANCO:

    saida : "Pix enviado via chave: Qualicontax"
    entrada: "RECEBIMENTO PIX-PIX_CRED 11158475000127 QUALICONTAX ASSESSORIA"

   Isso não é dedução, é leitura. E é obrigatório.

6. **A empresa nomeada tem de ser a DONA da conta da outra ponta.** A descrição
   não diz só que houve transferência: diz PARA QUEM. Se a saída nomeia o
   Anderson PF, a entrada tem de estar numa conta DO ANDERSON PF — não vale
   qualquer entrada de mesmo valor.

POR QUE A CONDIÇÃO 5 É OBRIGATÓRIA — o dado decidiu
---------------------------------------------------
A primeira versão deste módulo tratava a condição 5 como um NÍVEL ("alta" com
nome, "media" sem) e mandava as duas para aprovação. A simulação de 11/09/2026
mostrou que isso estava errado: dos 172 pares, **os 21 sem nome eram TODOS
falso positivo**, sem uma exceção — pagamento PIX a uma pessoa saindo do
Sicredi que coincidia em valor e dia com um boleto de cliente entrando no EFI:

    saiu  : PAGAMENTO PIX-PIX_DEB  27752050890 VALERIA DOS SANTOS SILVA
    entrou: Recebimento de cobrança: 991639093 de XINGU COMERCIO DE COMBUSTIVEIS

Valor igual, mesmo dia, contas diferentes — e nada a ver um com o outro. Pior:
os 21 já tinham o lado do EFI classificado como RECEITA pela regra 112, e
corretamente. Parear aquilo teria desfeito receita legítima e travado dinheiro
de cliente.

Valor redondo repetido (R$ 1.400, R$ 500, R$ 300, R$ 200) é o que produz essa
coincidência, e é justamente o valor mais comum num extrato. Por isso a marca
não é desempate: é requisito. Ver [[regra-indicadores-dado-real]].

E A CONDIÇÃO 6 VEIO DA MESMA FORMA
----------------------------------
Com a marca obrigatória sobraram 151 pares — e QUATRO ainda estavam errados:

    saiu  : PAGAMENTO PIX-PIX_DEB  29151141884 ANDERSON ANTUNES VIEIRA
    entrou: Recebimento de cobrança: 968655882 de AUTO POSTO BEIRA RIO

A marca casou porque o Anderson PF É do grupo. Mas o dinheiro foi para a conta
DELE — que não está no sistema —, e a entrada emparelhada era boleto de cliente
no EFI da Qualicontax. Exigir que a empresa nomeada seja a dona da conta da
outra ponta mata os quatro E produz o estado certo para esse caso: **suspeito**,
travado à espera do extrato dele. É o mecanismo que o Anderson pediu para a
Brilho — "tem que travar para eu colocar o extrato, senão não colocarei".

MESMA EMPRESA x ENTRE EMPRESAS — não é a mesma coisa
----------------------------------------------------
* ``mesma_empresa``: as 5 contas da Qualicontax entre si. Mesmo patrimônio,
  dinheiro de bolso a bolso — nunca é receita nem despesa.
* ``entre_empresas``: patrimônios distintos. As duas pontas NÃO terão o mesmo
  tipo (pró-labore é despesa lá e receita aqui). Uma decisão, dois lados.

O campo ``escopo`` diz qual é, e a tela pergunta de acordo.
"""
import re
import unicodedata
from collections import defaultdict

JANELA_DIAS = 1


def _norm(texto):
    """Maiúsculas, sem acento, só letra/dígito/espaço — para comparar nome."""
    t = unicodedata.normalize('NFKD', str(texto or ''))
    t = ''.join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r'[^A-Z0-9 ]', ' ', t.upper())
    return re.sub(r'\s+', ' ', t).strip()


def _digitos(v):
    return re.sub(r'\D', '', str(v or ''))


def empresas_do_grupo():
    """[{cliente_id, nome, doc, apelido}] — quem forma o grupo do Financeiro.

    Vem de ``fin_empresas``, a mesma lista dos chips da tela. Empresa fora
    dessa lista não entra em par nenhum.
    """
    from utils.db_helper import execute_query
    return execute_query(
        """SELECT fe.cliente_id, fe.apelido,
                  c.nome_razao_social AS nome, c.cpf_cnpj AS doc
             FROM fin_empresas fe
             JOIN clientes c ON c.id = fe.cliente_id
            WHERE fe.ativo = 1""", fetch=True) or []


def _marcas(grupo):
    """[(cliente_id, tipo, texto)] para procurar na descrição.

    O cliente_id vem junto porque a condição 6 precisa saber DE QUEM é a marca,
    não só que existe uma.

    Do nome só entram os dois primeiros termos significativos: a descrição do
    banco TRUNCA ("QUALICONTAX ASSESSORIA CONTAB"), então exigir a razão social
    inteira perderia o par. Termo curto (LTDA, SA, ME) fica fora — casaria com
    qualquer fornecedor.
    """
    lixo = {'LTDA', 'SA', 'S A', 'ME', 'EPP', 'EIRELI', 'DE', 'DA', 'DO', 'E'}
    out = []
    for e in grupo:
        d = _digitos(e['doc'])
        if len(d) >= 11:
            out.append((e['cliente_id'], 'doc', d))
        termos = [t for t in _norm(e['nome']).split() if t not in lixo and len(t) > 2]
        if termos:
            out.append((e['cliente_id'], 'nome', ' '.join(termos[:2])))
    # doc antes de nome: CNPJ é inequívoco, nome pode ser prefixo de outro
    out.sort(key=lambda m: 0 if m[1] == 'doc' else 1)
    return out


def _nomeia_grupo(descricao, marcas):
    """Qual empresa do grupo a descrição cita? -> (cliente_id, texto) ou (None, '')."""
    d = _norm(descricao)
    puro = _digitos(descricao)
    for cid, tipo, texto in marcas:
        if tipo == 'doc' and texto in puro:
            return cid, texto
        if tipo == 'nome' and texto in d:
            return cid, texto
    return None, ''


def candidatos(empresa_ids=None, exigir_marca=True):
    """Devolve (pares, resumo) sem gravar nada.

    ``exigir_marca=False`` devolve também os que batem só em valor/conta/data,
    e existe SÓ para diagnóstico — a simulação de 11/09/2026 provou que ali é
    tudo coincidência (ver o cabeçalho). Em produção nunca use False.

    Cada par: {saida, entrada, valor, dias, escopo, confianca, marca}, onde
    ``saida``/``entrada`` são o dict do lançamento (id, conta, data, ...).

    Um lançamento entra em NO MÁXIMO um par: o melhor candidato leva, e a
    ordem de preferência é mesma data antes de 1 dia, e nome confirmado antes
    de não confirmado. Sem isso, um valor que se repete no mês (R$ 100,00
    várias vezes) casaria em cadeia com quem aparecesse primeiro.
    """
    from utils.db_helper import execute_query

    grupo = empresas_do_grupo()
    ids = {e['cliente_id'] for e in grupo}
    if empresa_ids:
        ids &= set(empresa_ids)
    if not ids:
        return [], {'lancamentos': 0, 'pares': 0, 'motivo': 'nenhuma empresa no grupo'}

    marcas = _marcas(grupo)
    apelido = {e['cliente_id']: (e['apelido'] or e['nome']) for e in grupo}

    marks = ','.join(['%s'] * len(ids))
    linhas = execute_query(
        f"""SELECT id, empresa_id, banco, conta, data, valor, descricao,
                   categoria_id, par_id, par_estado
              FROM extrato_lancamentos
             WHERE empresa_id IN ({marks})
             ORDER BY data, id""", tuple(ids), fetch=True) or []

    # Índice por valor absoluto: o par tem de ter o MESMO módulo.
    por_valor = defaultdict(lambda: {'saidas': [], 'entradas': []})
    for l in linhas:
        chave = round(abs(float(l['valor'])), 2)
        lado = 'saidas' if float(l['valor']) < 0 else 'entradas'
        por_valor[chave][lado].append(l)

    # Monta TODOS os candidatos possíveis, com nota, e só depois escolhe — é o
    # que garante que o melhor par leve, e não o primeiro encontrado.
    # Lido UMA vez por lançamento: a descrição não muda no meio do laço, e
    # chamar _nomeia_grupo dentro do produto cartesiano custaria caro.
    citado = {l['id']: _nomeia_grupo(l['descricao'], marcas) for l in linhas}

    brutos = []
    for chave, lados in por_valor.items():
        if not lados['saidas'] or not lados['entradas']:
            continue
        for s in lados['saidas']:
            cid_s, txt_s = citado[s['id']]
            for e in lados['entradas']:
                if str(s['conta']) == str(e['conta']):
                    continue                      # mesma conta não é transferência
                dias = (e['data'] - s['data']).days
                if dias < 0 or dias > JANELA_DIAS:
                    continue                      # entrada antes da saída não existe
                cid_e, txt_e = citado[e['id']]

                # Condições 5 e 6 juntas: quem a descrição nomeia tem de ser a
                # dona da conta da OUTRA ponta. Vale por qualquer um dos lados
                # — basta que um deles nomeie o dono do outro.
                if cid_s and cid_s == e['empresa_id']:
                    marca, lado = txt_s, 'saida'
                elif cid_e and cid_e == s['empresa_id']:
                    marca, lado = txt_e, 'entrada'
                elif exigir_marca:
                    continue
                else:
                    marca, lado = (txt_s or txt_e), 'nenhum'

                brutos.append({
                    'saida': s, 'entrada': e, 'valor': chave, 'dias': dias,
                    'marca': marca, 'marca_lado': lado,
                    'confianca': 'alta' if lado != 'nenhum' else 'media',
                    'escopo': ('mesma_empresa' if s['empresa_id'] == e['empresa_id']
                               else 'entre_empresas'),
                })

    # nota: data exata primeiro, nome confirmado depois, valor maior por último
    brutos.sort(key=lambda p: (p['dias'], 0 if p['marca'] else 1, -p['valor']))
    usados, pares = set(), []
    for p in brutos:
        if p['saida']['id'] in usados or p['entrada']['id'] in usados:
            continue
        usados.add(p['saida']['id'])
        usados.add(p['entrada']['id'])
        pares.append(p)

    # SUSPEITOS: a descrição nomeia uma empresa do grupo, mas par não houve.
    # É aqui que o caso Brilho/Anderson PF cai, e é por isso que o estado
    # 'suspeito' existe: o lançamento trava à espera do extrato do outro lado,
    # que pode nem estar cadastrado. Sem isto, dinheiro que saiu para dentro do
    # grupo viraria despesa comum e ninguém cobraria o extrato que falta.
    suspeitos = []
    for l in linhas:
        if l['id'] in usados:
            continue
        cid, txt = citado[l['id']]
        if not cid:
            continue
        suspeitos.append({
            'lancamento': l,
            'aponta_para': cid,
            'aponta_nome': apelido.get(cid, '?'),
            'marca': txt,
            'motivo': ('outra_empresa' if cid != l['empresa_id']
                       else 'mesma_empresa'),
        })

    resumo = {
        'lancamentos': len(linhas),
        'candidatos_brutos': len(brutos),
        'pares': len(pares),
        'valor': sum(p['valor'] for p in pares),
        'mesma_empresa': sum(1 for p in pares if p['escopo'] == 'mesma_empresa'),
        'entre_empresas': sum(1 for p in pares if p['escopo'] == 'entre_empresas'),
        'ja_classificados': sum(
            1 for p in pares
            if p['saida']['categoria_id'] or p['entrada']['categoria_id']),
        'suspeitos': len(suspeitos),
        'suspeitos_outra_empresa': sum(
            1 for s in suspeitos if s['motivo'] == 'outra_empresa'),
        'empresas': apelido,
    }
    return pares, suspeitos, resumo


# =======================================================================
# O MARCADOR — a única parte deste módulo que escreve
# =======================================================================
#
# Roda depois de cada importação e é uma PASSADA DE RECONCILIAÇÃO, não um
# gravador de uma vez: quando o arquivo do EFI entra antes do do Sicredi, a
# ponta do EFI nasce 'suspeito' (nomeia a própria empresa, par não existe
# ainda) e VIRA 'casado' quando o outro arquivo chega. Por isso ele reavalia
# tudo em vez de só marcar o que está em branco.
#
# TRÊS COISAS QUE ELE NUNCA FAZ:
#  * não toca em quem você já decidiu ('aprovado' ou 'liberado') — decisão de
#    gente não é revista por rotina;
#  * não trava quem JÁ está classificado. Travar depois seria desfazer
#    trabalho, e o caso existe: uma regra pode classificar a ponta antes de o
#    par aparecer. Quem já tem categoria fica de fora e aparece no resumo como
#    'ignorados_classificados', para alguém olhar;
#  * não classifica, não cria título, não mexe em categoria. Só par_id e
#    par_estado.
ESTADOS_DECIDIDOS = ('aprovado', 'liberado')

#: Estados que TRAVAM o lançamento: nem gente nem regra classifica.
ESTADOS_TRAVA = ('casado', 'suspeito')

#: Para pôr direto num WHERE. Quem está fora da trava é NULL, 'aprovado' ou
#: 'liberado' — os dois últimos já foram decididos e voltaram para a vida.
SQL_FORA_DA_TRAVA = ("COALESCE(par_estado, '') NOT IN ('%s', '%s')"
                     % ESTADOS_TRAVA)


def motivo_trava(lanc):
    """Por que este lançamento não pode ser classificado — ou None se pode.

    Devolve a frase que a tela mostra. Não é aviso: é recusa. O lançamento só
    sai daqui pela aprovação do par ou pela liberação com motivo.
    """
    estado = (lanc or {}).get('par_estado') or ''
    if estado == 'casado':
        return ('Este lançamento é uma das pontas de uma TRANSFERÊNCIA entre '
                'contas do grupo — a outra ponta já foi encontrada. Classificar '
                'só um lado contaria o mesmo dinheiro duas vezes. Aprove o par '
                'na fila de transferências.')
    if estado == 'suspeito':
        return ('Este lançamento parece TRANSFERÊNCIA para outra empresa do '
                'grupo, e a outra ponta ainda não chegou ao sistema. Importe o '
                'extrato da outra empresa, ou libere com motivo se não houver '
                'mais acesso a ele.')
    return None


def marcar(empresa_ids=None, dry=True):
    """Grava par_id/par_estado. ``dry=True`` (padrão) só devolve o que faria."""
    from utils.db_helper import execute_query

    pares, suspeitos, resumo = candidatos(empresa_ids=empresa_ids)

    # quem já foi decidido por gente, ou já está classificado, fica fora
    alvos = {}
    for p in pares:
        for lado, outro in ((p['saida'], p['entrada']), (p['entrada'], p['saida'])):
            alvos[lado['id']] = (outro['id'], 'casado', lado)
    for s in suspeitos:
        l = s['lancamento']
        alvos[l['id']] = (None, 'suspeito', l)

    plano, ignorados_decididos, ignorados_classificados, ja_ok = [], [], [], []
    for lid, (par_id, estado, l) in alvos.items():
        if (l.get('par_estado') or '') in ESTADOS_DECIDIDOS:
            ignorados_decididos.append(lid)
            continue
        if l.get('categoria_id'):
            ignorados_classificados.append(lid)
            continue
        if l.get('par_estado') == estado and (l.get('par_id') or None) == par_id:
            ja_ok.append(lid)
            continue
        plano.append((lid, par_id, estado))

    r = {
        'pares': resumo['pares'],
        'suspeitos': resumo['suspeitos'],
        'a_gravar': len(plano),
        'ja_corretos': len(ja_ok),
        'ignorados_decididos': len(ignorados_decididos),
        'ignorados_classificados': len(ignorados_classificados),
        'casado': sum(1 for _, _, e in plano if e == 'casado'),
        'suspeito_a_gravar': sum(1 for _, _, e in plano if e == 'suspeito'),
        'dry': dry,
    }
    if dry or not plano:
        r['plano'] = plano
        return r

    # UPDATE por lançamento, não em lote: são centenas, não milhares, e cada
    # linha leva um par_id diferente. Fazer em CASE WHEN daria uma query
    # ilegível para ganhar milissegundos.
    for lid, par_id, estado in plano:
        execute_query(
            'UPDATE extrato_lancamentos SET par_id = %s, par_estado = %s '
            ' WHERE id = %s AND COALESCE(par_estado, \'\') NOT IN (%s, %s)',
            (par_id, estado, lid) + ESTADOS_DECIDIDOS, fetch=False)
    r['gravados'] = len(plano)
    return r
