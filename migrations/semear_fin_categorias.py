# -*- coding: utf-8 -*-
"""Semeia o plano gerencial do escritorio em fin_categorias.

A arvore tem tres niveis, como o Anderson desenhou:

    GRUPO            (texto na coluna `grupo`)
      CATEGORIA      (linha com pai_id NULL)
        SUBCATEGORIA (linha com pai_id apontando para a categoria)

Exemplo dele, que virou a regra dos veiculos:

    Veiculos > SPIN > {Documentos, Manutencao, Abastecimento, Multas, ...}

Duas decisoes que moldam o resto e ficam registradas aqui:

* MENSALIDADE E UMA SO. O fluxo de caixa antigo tinha ~37 linhas
  "Mensalidade Rede X" somando R$ 2,75 mi. Rede e de QUEM paga, nao O QUE e:
  ela vem do grupo do cliente e o relatorio separa depois. Como categoria,
  cada rede nova mexeria no plano de contas e um posto trocando de rede
  obrigaria a reclassificar o passado.
* PESSOA VAI EM CONTRAPARTE. Os ~23 "Func. Fulano" e os 13 nomes de comissao
  viram duas categorias (Remuneracao e Comissao) com o nome no titulo. Assim
  o DRE mostra uma linha em vez de trinta e seis, quem sai do escritorio nao
  deixa categoria morta, e o custo por pessoa continua consultavel.

O tipo ganhou dois valores alem de R e P:

* T (transferencia) — dinheiro andando entre contas do proprio grupo. Sao
  R$ 1,6 mi entre EFI e Sicredi que, lancados como receita, dobrariam o
  faturamento; ignorados, quebrariam o saldo da conta. Entra no saldo, fica
  fora do DRE.
* I (investimento) — imovel, consorcio, conta capital. Sai do caixa mas nao
  e despesa do periodo.

Roda sem --apply para ver o que faria.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv                                # noqa: E402
load_dotenv()

from utils.db_helper import execute_query                     # noqa: E402

VEICULOS = ['SPIN', 'Classic', 'Zafira', 'Fiorino', 'F250', 'Commander',
            'Carretinha', 'Caminhão Scania']
NAT_VEIC = ['Documentos (IPVA/licenciamento)', 'Manutenção', 'Abastecimento',
            'Multas', 'Lavagem', 'Seguro / proteção']

# (tipo, grupo, categoria, [subcategorias])
ARVORE = [
    # ---------------- RECEITAS: os 4 grupos que o Anderson definiu ---------
    # 1) HONORÁRIOS — o recorrente. "Honorarios contabeis" e a mensalidade de
    #    todo mundo: a rede vem do grupo do cliente, nao daqui.
    ('R', 'Receita de honorários', 'Honorários contábeis', []),
    ('R', 'Receita de honorários', 'Décimo terceiro',
     ['1ª parcela', '2ª parcela', 'Parcela única']),

    # 2) AVULSAS — o que se cobra fora da mensalidade.
    ('R', 'Receitas avulsas', 'Serviços avulsos', [
        'Abertura de Empresa', 'Alteração Contratual', 'Baixa de Empresa',
        'Declaração de Imposto de Renda', 'Constituição - Posto de Combustível',
        'Alteração Contratual - Posto de Combustível', 'Cadastro ANP',
        'Cadastro ANTT', 'Inscrição Estadual ST', 'Emissão de Nota Fiscal Eletrônica',
        'Taxa de Urgência - Jucesp', 'Taxa Junta Comercial',
        'Consulta Pendência ACEO', 'Elaboração de DRE / BP',
        'Ata de Distribuição de Lucro', 'Taxas de Cartórios', 'Correios',
        'Placa de Preços ANP / ICMS', 'Fita Adesiva Dupla Face',
        'Folha de Pagamento', 'Complemento de Mensalidade',
        '13º Salário (Diferença)']),
    ('R', 'Receitas avulsas', 'Sistemas e serviços', [
        'LMC', 'Mensalidade Loja de Conveniência', 'Sistema de Emissão de CT-e',
        'Sistema de Envio de Recibos Trabalhistas',
        'Arquivo Remessa - Folha de Pagamento']),

    # 3) DEDUÇÕES — nao e receita, e o que se abate dela. Ficam em grupo
    #    proprio porque no DRE a linha e "receita bruta menos deducoes": posto
    #    junto das avulsas, um desconto concedido apareceria somando.
    ('R', 'Deduções', 'Deduções da receita', ['Descontos', 'Juros e Multa']),

    # 4) FINANCEIRAS e OUTRAS.
    ('R', 'Receitas financeiras', 'Rendimentos', []),
    ('R', 'Outras receitas', 'Aluguel', []),
    ('R', 'Outras receitas', 'Sub-locações', []),
    ('R', 'Outras receitas', 'Serviços', []),

    # ---------------- PESSOAL ----------------
    ('P', 'Pessoal', 'Remuneração', ['Salários', 'Seguro de vida']),
    ('P', 'Pessoal', 'Benefícios',
     ['Unimed', 'Alimentação e refeição', 'Café', 'Uniformes']),
    # FGTS, INSS e pro-labore NAO aparecem em lugar nenhum do fluxo de caixa
    # de 08/2025 a 08/2026 — devem estar embutidos dentro de cada "Func.
    # Fulano". Ficam aqui porque um escritorio com 23 funcionarios paga os
    # dois, e sem a categoria o primeiro DARF de FGTS a ser conciliado nao
    # teria onde cair.
    ('P', 'Pessoal', 'Encargos', ['FGTS', 'INSS']),
    ('P', 'Pessoal', 'Pró-labore', []),
    ('P', 'Comissões', 'Comissão', []),

    # ---------------- OCUPAÇÃO ----------------
    ('P', 'Ocupação', 'Aluguel', ['Escritório', 'Canedo', 'Delma', 'MA']),
    ('P', 'Ocupação', 'Energia elétrica', []),
    ('P', 'Ocupação', 'Água', []),

    # ---------------- ADMINISTRATIVAS ----------------
    ('P', 'Administrativas', 'Advogado', []),
    ('P', 'Administrativas', 'Cartório', ['Goiatuba']),
    ('P', 'Administrativas', 'Certificado digital', []),
    ('P', 'Administrativas', 'Compras on-line', []),
    ('P', 'Administrativas', 'Correios', []),
    ('P', 'Administrativas', 'Material de escritório', []),
    ('P', 'Administrativas', 'Móveis e utensílios', []),
    ('P', 'Administrativas', 'Segurança / alarme', []),
    ('P', 'Administrativas', 'Viagens e estadias', []),
    ('P', 'Administrativas', 'Telefonia e internet',
     ['Vivo Fixo', 'Vivo Móvel', 'Starlink', 'Hospedagem']),
    ('P', 'Administrativas', 'Taxas de órgãos', ['CRC', 'Jucesp']),

    # ---------------- SISTEMAS ----------------
    ('P', 'Sistemas', 'Sistemas e assinaturas', [
        'ACEO', 'IOB', 'LMC', 'LMC Katia', 'Alterdata', 'Conexa', 'Questor',
        'Veri', 'Sysconv', '55PBX', 'Acessórias e Komunic', 'Captura',
        'HTEC', 'SPA', 'Luciano']),

    # ---------------- IMPOSTOS ----------------
    ('P', 'Impostos e taxas', 'Simples Nacional', []),
    ('P', 'Impostos e taxas', 'DCTFWEB', []),
    ('P', 'Impostos e taxas', 'ITR', []),
    ('P', 'Impostos e taxas', 'Taxa municipal', []),
    ('P', 'Impostos e taxas', 'Multas fiscais', []),
    ('P', 'Impostos e taxas', 'INMETRO', []),
    ('P', 'Impostos e taxas', 'Parcelamentos',
     ['PGFN', 'Simples Nacional', 'ICMS GTBA']),

    # ---------------- FINANCEIRAS ----------------
    ('P', 'Financeiras', 'Tarifas bancárias', []),
    ('P', 'Financeiras', 'Juros e encargos', []),
    ('P', 'Financeiras', 'Seguros', []),
    # Empréstimo: só o juro é despesa. O principal devolve o que foi tomado —
    # lançado inteiro como despesa, o resultado do escritório fica pior do
    # que é. Duas categorias para que a separação seja obrigatória.
    ('P', 'Financeiras', 'Empréstimos - juros', []),
    ('P', 'Financeiras', 'Empréstimos - principal', []),

    # ---------------- MARKETING ----------------
    ('P', 'Marketing', 'Gráfica', []),
    ('P', 'Marketing', 'Marketing digital', []),
    ('P', 'Marketing', 'Brindes e eventos', []),
    ('P', 'Marketing', 'Fretes', []),

    # ---------------- OUTROS ----------------
    ('P', 'Erro do escritório', 'Erro do escritório', []),
    ('P', 'Devoluções', 'Devolução a cliente', []),
    ('P', 'Cartões de crédito', 'Cartão Bradesco', []),
    ('P', 'Cartões de crédito', 'Cartão C6', []),
    ('P', 'Cartões de crédito', 'Cartão EFI', []),
    ('P', 'Pessoais do sócio', 'Despesas pessoais', []),
    # Vala comum, de proposito. Na conciliacao sempre aparece um movimento que
    # ninguem sabe classificar na hora; sem um lugar para ele, ou o lancamento
    # fica sem categoria (e some dos relatorios) ou alguem o enfia numa
    # categoria errada — o segundo caso e pior, porque mente calado.
    ('R', 'Outras receitas', 'Outras receitas', []),
    ('P', 'Outras despesas', 'Outras despesas', []),

    # ---------------- TRANSFERÊNCIA (fora do DRE) ----------------
    ('T', 'Transferência', 'Entre contas do grupo', []),
    ('T', 'Transferência', 'Aporte do sócio', []),
    ('T', 'Transferência', 'Retirada do sócio', []),

    # ---------------- INVESTIMENTO (sai do caixa, não é despesa) ----------
    ('I', 'Investimentos', 'Imóveis e obras', []),
    ('I', 'Investimentos', 'Consórcio', []),
    ('I', 'Investimentos', 'Conta capital', []),
]

for _v in VEICULOS:
    ARVORE.append(('P', 'Veículos', _v, list(NAT_VEIC)))
ARVORE.append(('P', 'Veículos', 'Asproauto', []))


def existentes():
    r = execute_query(
        'SELECT id, tipo, grupo, nome, pai_id, ativo FROM fin_categorias',
        fetch=True) or []
    return {(x['tipo'], x['grupo'], x['nome'], x['pai_id']): x for x in r}


def em_uso():
    """Categorias que algum titulo ou lancamento ja usa.

    Desativar uma categoria em uso deixaria lancamento orfao — por isso a
    conferencia acontece ANTES, e nao depois.
    """
    usados = set()
    for tab, col in (('fin_titulos', 'categoria_id'),
                     ('extrato_lancamentos', 'categoria_id'),
                     ('fin_programacoes', 'categoria_id')):
        try:
            r = execute_query(
                f'SELECT DISTINCT {col} AS c FROM {tab} WHERE {col} IS NOT NULL',
                fetch=True) or []
            usados |= {x['c'] for x in r}
        except Exception:
            pass
    return usados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    antes = existentes()
    uso = em_uso()

    alvo_cats = [(t, g, c) for t, g, c, _ in ARVORE]
    alvo_subs = [(t, g, c, s) for t, g, c, subs in ARVORE for s in subs]
    print('ARVORE PROPOSTA')
    print('  grupos .......:', len({g for _, g, _ in alvo_cats}))
    print('  categorias ...:', len(alvo_cats))
    print('  subcategorias :', len(alvo_subs))
    print('  TOTAL de nós .:', len(alvo_cats) + len(alvo_subs))
    print()
    print('JA NO BANCO')
    print('  categorias ...:', len(antes))
    print('  em uso .......:', len(uso), '(nenhuma sera tocada se estiver em uso)')
    print()

    novas = [k for k in
             [(t, g, c, None) for t, g, c in alvo_cats] if k not in antes]
    print('SERAO CRIADAS: %d categorias de 1o nivel' % len(novas))
    nomes_alvo = {(t, g, c) for t, g, c in alvo_cats}
    sobrando = [v for k, v in antes.items()
                if v['pai_id'] is None
                and (v['tipo'], v['grupo'], v['nome']) not in nomes_alvo]
    print('SERAO DESATIVADAS: %d categorias antigas que sairam do plano' % len(sobrando))
    for v in sobrando:
        marca = '  <-- EM USO, NAO SERA TOCADA' if v['id'] in uso else ''
        print('   - %s / %s%s' % (v['grupo'], v['nome'], marca))

    if not args.apply:
        print()
        print('[dry-run] nada foi gravado. Rode com --apply.')
        return 0

    # O execute_query engole o 1062 (chave duplicada) e devolve None. Contar
    # tentativas em vez de resultados fez a rodada de 21/08 relatar 177 nós
    # criados quando 44 haviam sido recusados em silencio. Aqui cada insercao
    # so conta depois de RELER o banco, e o que falhar sai listado no fim.
    criadas, falhas = 0, []

    def inserir(pai_id, t, g, nome, ordem):
        execute_query(
            'INSERT INTO fin_categorias (pai_id, tipo, grupo, nome, ordem, ativo) '
            'VALUES (%s, %s, %s, %s, %s, 1)', (pai_id, t, g, nome, ordem))
        r = execute_query(
            'SELECT id FROM fin_categorias WHERE tipo = %s AND grupo = %s '
            '  AND nome = %s AND IFNULL(pai_id, 0) = %s',
            (t, g, nome, pai_id or 0), fetch=True, fetch_one=True)
        if not r:
            falhas.append((t, g, nome, pai_id))
            return None
        return r['id']

    # PASSO de 1000 entre categorias. Com passo 10, as 24 subcategorias de
    # "Serviços avulsos" receberam ordem 31..54 e invadiram a faixa das
    # categorias seguintes — a tela passou a repetir o mesmo cabeçalho de
    # grupo. O passo precisa ser maior que a maior lista de subcategorias.
    PASSO = 1000
    assert PASSO > max((len(s) for _, _, _, s in ARVORE), default=0)

    for i, (t, g, c, subs) in enumerate(ARVORE, start=1):
        ordem = i * PASSO
        k = (t, g, c, None)
        if k in antes:
            pai = antes[k]['id']
            execute_query('UPDATE fin_categorias SET ativo = 1, ordem = %s '
                          'WHERE id = %s', (ordem, pai))
        else:
            pai = inserir(None, t, g, c, ordem)
            if pai:
                criadas += 1
        if not pai:
            continue
        for j, s in enumerate(subs, start=1):
            ks = (t, g, s, pai)
            if ks in antes:
                execute_query('UPDATE fin_categorias SET ativo = 1, ordem = %s '
                              'WHERE id = %s', (ordem + j, antes[ks]['id']))
            elif inserir(pai, t, g, s, ordem + j):
                criadas += 1

    # Desativar a categoria e deixar os filhos ativos cria subcategoria pendurada
    # em pai morto — ela some da arvore mas continua aparecendo em seletor plano.
    # Foi o que aconteceu com Tecnologia > Informatica: o pai saiu e Alterdata,
    # Claude e Conexa ficaram para tras. A desativacao desce um nivel.
    desativadas = 0
    for v in sobrando:
        if v['id'] in uso:
            continue
        filhos = execute_query('SELECT id FROM fin_categorias WHERE pai_id = %s',
                               (v['id'],), fetch=True) or []
        for f in filhos:
            if f['id'] in uso:
                continue
            execute_query('UPDATE fin_categorias SET ativo = 0 WHERE id = %s',
                          (f['id'],))
            desativadas += 1
        execute_query('UPDATE fin_categorias SET ativo = 0 WHERE id = %s', (v['id'],))
        desativadas += 1

    # Rede de seguranca: qualquer filho ativo cujo pai esteja desativado.
    orfaos = execute_query(
        """SELECT c.id FROM fin_categorias c
             JOIN fin_categorias p ON p.id = c.pai_id
            WHERE c.ativo = 1 AND p.ativo = 0""", fetch=True) or []
    for o in orfaos:
        if o['id'] in uso:
            continue
        execute_query('UPDATE fin_categorias SET ativo = 0 WHERE id = %s', (o['id'],))
        desativadas += 1

    print()
    if falhas:
        print('!! %d nós RECUSADOS pelo banco (nao foram criados):' % len(falhas))
        for t, g, n, p in falhas[:12]:
            print('     %s / %s / %s (pai=%s)' % (t, g, n, p))
        print('   Provavel causa: chave unica sem o pai. Rode antes')
        print('   migrations/fix_uk_fin_categorias.py --apply')
    print('OK: %d nós criados, %d antigos desativados.' % (criadas, desativadas))
    tot = execute_query('SELECT COUNT(*) n FROM fin_categorias WHERE ativo = 1',
                        fetch=True, fetch_one=True)['n']
    print('   fin_categorias ativas agora:', tot)
    return 0


if __name__ == '__main__':
    sys.exit(main())
