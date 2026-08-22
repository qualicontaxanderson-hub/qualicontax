# -*- coding: utf-8 -*-
"""Semeia o plano gerencial do escritorio em fin_categorias.

A arvore tem tres niveis, como o Anderson desenhou:

    GRUPO            (texto na coluna `grupo`)
      CATEGORIA      (linha com pai_id NULL)
        SUBCATEGORIA (linha com pai_id apontando para a categoria)

Exemplo dele, que virou a regra dos veiculos:

    Veiculos > SPIN > {Documentos, Manutencao, Abastecimento, Multas, ...}

As DESPESAS sao a planilha que ele devolveu em 22/08, transcrita literalmente
— mesmos nomes, mesma ordem, mesmo agrupamento. A versao anterior deste
arquivo reorganizava aquilo em grupos meus (Ocupacao, Sistemas, Marketing,
Impostos...) e ele avisou: "mandei como queremos mas nao adiantou de nada".
Nao reinterpretar.

Uma decisao que moldou as RECEITAS e fica registrada:

* MENSALIDADE E UMA SO. O fluxo de caixa antigo tinha ~37 linhas
  "Mensalidade Rede X" somando R$ 2,75 mi. Rede e de QUEM paga, nao O QUE e:
  ela vem do grupo do cliente e o relatorio separa depois. Como categoria,
  cada rede nova mexeria no plano de contas e um posto trocando de rede
  obrigaria a reclassificar o passado.

Nas despesas ele escolheu o contrario e a escolha e dele: cada funcionario
e cada comissionado e uma categoria propria ("Func. Fulano"), em vez de uma
categoria unica com o nome na contraparte. Da custo por pessoa direto no
plano, ao preco de o DRE ter 25 linhas de pessoal e de quem sai deixar
categoria para desativar.

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

# (tipo, grupo, categoria, [subcategorias])
ARVORE = [
    # ---------------- RECEITAS: os 4 grupos que o Anderson definiu ---------
    # 1) HONORÁRIOS — o recorrente. "Honorarios contabeis" e a mensalidade de
    #    todo mundo: a rede vem do grupo do cliente, nao daqui.
    ('R', 'Receita de honorários', 'Honorários contábeis', []),
    ('R', 'Receita de honorários', 'Décimo terceiro',
     ['1ª parcela', '2ª parcela', 'Parcela única']),

    # 2) AVULSAS — o que se cobra fora da mensalidade. SEM linha-mae: cada
    #    servico e uma categoria do grupo, nao subcategoria de um guarda-chuva.
    #    "Servicos avulsos" dentro de "Receitas avulsas" so repetia o nome do
    #    grupo e criava um nivel que nao significa nada.
] + [('R', 'Receitas avulsas', _n, []) for _n in [
        'Abertura de Empresa', 'Alteração Contratual', 'Baixa de Empresa',
        'Declaração de Imposto de Renda', 'Constituição - Posto de Combustível',
        'Alteração Contratual - Posto de Combustível', 'Cadastro ANP',
        'Cadastro ANTT', 'Inscrição Estadual ST', 'Emissão de Nota Fiscal Eletrônica',
        'Taxa de Urgência - Jucesp', 'Taxa Junta Comercial',
        'Consulta Pendência ACEO', 'Elaboração de DRE / BP',
        'Ata de Distribuição de Lucro', 'Taxas de Cartórios', 'Correios',
        'Placa de Preços ANP / ICMS', 'Fita Adesiva Dupla Face',
        'Folha de Pagamento', 'Complemento de Mensalidade',
        '13º Salário (Diferença)', 'LMC', 'Mensalidade Loja de Conveniência',
        'Sistema de Emissão de CT-e', 'Sistema de Envio de Recibos Trabalhistas',
        'Arquivo Remessa - Folha de Pagamento']] + [

    # 3) DEDUÇÕES — nao e receita, e o que se abate dela. Ficam em grupo
    #    proprio porque no DRE a linha e "receita bruta menos deducoes": posto
    #    junto das avulsas, um desconto concedido apareceria somando.
    ('R', 'Deduções', 'Deduções da receita', ['Descontos', 'Juros e Multa']),

    # 4) FINANCEIRAS e OUTRAS.
    ('R', 'Receitas financeiras', 'Rendimentos', []),
    ('R', 'Outras receitas', 'Aluguel', []),
    ('R', 'Outras receitas', 'Sub-locações', []),
    ('R', 'Outras receitas', 'Serviços', []),

    # ============ DESPESAS E INVESTIMENTOS ============
    # Este bloco e a planilha que o Anderson devolveu em 22/08, LITERAL:
    # mesmos nomes, mesma ordem, mesmo agrupamento. A versao anterior era
    # uma reorganizacao minha por cima do que ele tinha descrito, e ele
    # avisou que nao servia. Nao reinterpretar de novo.

    # ---- Administrativas ----
    ('P', 'Administrativas', 'ACEO', []),
    ('P', 'Administrativas', 'Advogado', []),
    ('P', 'Administrativas', 'Água', []),
    ('P', 'Administrativas', 'Aluguel - Delma', []),
    ('P', 'Administrativas', 'Aluguel - Escritório SP', []),
    ('P', 'Administrativas', 'Aluguel - Escrtiório Senador Canedo', []),
    ('P', 'Administrativas', 'Aluguel - MA', []),
    ('P', 'Administrativas', 'Cartório - Goiatuba', []),
    ('P', 'Administrativas', 'Certificado digital', []),
    ('P', 'Administrativas', 'Compras on-line', []),
    ('P', 'Administrativas', 'Correios', []),
    ('P', 'Administrativas', 'CRC - Albert Antunes', []),
    ('P', 'Administrativas', 'CRC - Carlos', []),
    ('P', 'Administrativas', 'CRC - Qualicontax', []),
    ('P', 'Administrativas', 'Energia elétrica', []),
    ('P', 'Administrativas', 'Hospedagem', []),
    ('P', 'Administrativas', 'IOB', []),
    ('P', 'Administrativas', 'Material de escritório', []),
    ('P', 'Administrativas', 'Móveis e utensílios', []),
    ('P', 'Administrativas', 'Segurança / alarme', []),
    ('P', 'Administrativas', 'Starlink', []),
    ('P', 'Administrativas', 'Vivo Fixo', []),
    ('P', 'Administrativas', 'Vivo Móvel', []),
    ('P', 'Administrativas', 'Unimed', []),
    ('P', 'Administrativas', 'Ração para o Animais', []),

    # ---- Cartão de Crédito ----
    ('P', 'Cartão de Crédito', 'Cartão Bradesco', []),
    ('P', 'Cartão de Crédito', 'Cartão BRB', []),
    ('P', 'Cartão de Crédito', 'Cartão C6', [
        'Anderson Antunes', 'Emily Lavinia', 'Livia Maria',
        'Beatriz Cunha', 'Qualicontax', 'Rodrigo Silva']),
    ('P', 'Cartão de Crédito', 'Cartão Carrefour', []),
    ('P', 'Cartão de Crédito', 'Cartão Cora', []),
    ('P', 'Cartão de Crédito', 'Cartão EFI', []),
    ('P', 'Cartão de Crédito', 'Cartão Mercado Pago', []),
    ('P', 'Cartão de Crédito', 'Cartão Neon', []),
    ('P', 'Cartão de Crédito', 'Cartão Nubank', []),
    ('P', 'Cartão de Crédito', 'Cartão Poupaki', []),
    ('P', 'Cartão de Crédito', 'Cartão Reis', []),
    ('P', 'Cartão de Crédito', 'Cartão Renner', []),
    ('P', 'Cartão de Crédito', 'Cartão Santander', []),
    ('P', 'Cartão de Crédito', 'Cartão XP', []),

    # ---- Despesa com Pessoal ----
    ('P', 'Despesa com Pessoal', 'Func. Alessandra Pereira', []),
    ('P', 'Despesa com Pessoal', 'Func. Bruna Lopes', []),
    ('P', 'Despesa com Pessoal', 'Func. Bruna Schumann', []),
    ('P', 'Despesa com Pessoal', 'Func. Carolina Damião', []),
    ('P', 'Despesa com Pessoal', 'Func. Eduarda Simões', []),
    ('P', 'Despesa com Pessoal', 'Func. Ester de Almeida', []),
    ('P', 'Despesa com Pessoal', 'Func. Gabriel Mendes', []),
    ('P', 'Despesa com Pessoal', 'Func. Graziella Almeida', []),
    ('P', 'Despesa com Pessoal', 'Func. Guilherme Rocha', []),
    ('P', 'Despesa com Pessoal', 'Func. Guylhermmy', []),
    ('P', 'Despesa com Pessoal', 'Func. Henrique Vicentini', []),
    ('P', 'Despesa com Pessoal', 'Func. Isabella Tomia', []),
    ('P', 'Despesa com Pessoal', 'Func. Jabes', []),
    ('P', 'Despesa com Pessoal', 'Func. José Querobino', []),
    ('P', 'Despesa com Pessoal', 'Func. Julian Amaral', []),
    ('P', 'Despesa com Pessoal', 'Func. Karina de Sousa', []),
    ('P', 'Despesa com Pessoal', 'Func. Lais Fernanda', []),
    ('P', 'Despesa com Pessoal', 'Func. Melchesedech', []),
    ('P', 'Despesa com Pessoal', 'Func. Miguel Sousa', []),
    ('P', 'Despesa com Pessoal', 'Func. Rodrigo Cunha', []),
    ('P', 'Despesa com Pessoal', 'Func. Rodrigo Silva', []),
    ('P', 'Despesa com Pessoal', 'Func. Selma Pereira', []),
    ('P', 'Despesa com Pessoal', 'Func. Sergio Camara', []),
    ('P', 'Despesa com Pessoal', 'Func. Talita Miyazaki', []),
    ('P', 'Despesa com Pessoal', 'Seguro de Vida - Rodrigo Silva', []),

    # ---- Devoluções ----
    ('P', 'Devoluções', 'Complemento - Nico', []),

    # ---- Erro do escritório ----
    ('P', 'Erro do escritório', 'Erro do escritório', []),

    # ---- Financeiras ----
    ('P', 'Financeiras', 'Bradesco - Capital de Giro 04/2028', []),
    ('P', 'Financeiras', 'Bradesco - Cheque Especial', []),
    ('P', 'Financeiras', 'Bradesco - Encargos s/Limite', []),
    ('P', 'Financeiras', 'Bradesco - IOF S/Limite', []),
    ('P', 'Financeiras', 'Bradesco - Seguros', []),
    ('P', 'Financeiras', 'Empréstimos - juros', []),
    ('P', 'Financeiras', 'Juros e encargos', []),
    ('P', 'Financeiras', 'Tarifa Pix - Bradesco', []),
    ('P', 'Financeiras', 'Tarifas bancárias - Bradesco', []),
    ('P', 'Financeiras', 'Tarifas bancárias - Sicredi', []),
    ('P', 'Financeiras', 'Tarifas Boleto - Bradesco', []),
    ('P', 'Financeiras', 'Tarifas Boleto - EFI', []),
    ('P', 'Financeiras', 'Tarifas Boleto - Sicredi', []),

    # ---- Impostos e taxas ----
    ('P', 'Impostos e taxas', 'DCTFWEB', []),
    ('P', 'Impostos e taxas', 'INMETRO', []),
    ('P', 'Impostos e taxas', 'ITR', []),
    ('P', 'Impostos e taxas', 'Parcelamentos - PGFN', []),
    ('P', 'Impostos e taxas', 'Parcelamentos - Simples Nacional', []),
    ('P', 'Impostos e taxas', 'Simples Nacional', []),
    ('P', 'Impostos e taxas', 'Taxa municipal', []),

    # ---- Informática ----
    ('P', 'Informática', '55PBX', []),
    ('P', 'Informática', 'Acessórias e Komunic', []),
    ('P', 'Informática', 'Alterdata', []),
    ('P', 'Informática', 'Captura', []),
    ('P', 'Informática', 'Conexa', []),
    ('P', 'Informática', 'HTEC', []),
    ('P', 'Informática', 'LMC', []),
    ('P', 'Informática', 'LMC Katia', []),
    ('P', 'Informática', 'Luciano', []),
    ('P', 'Informática', 'Questor', []),
    ('P', 'Informática', 'SPA', []),
    ('P', 'Informática', 'Sysconv', []),
    ('P', 'Informática', 'Veri', []),

    # ---- Marketing ----
    ('P', 'Marketing', 'Fretes', []),
    ('P', 'Marketing', 'Gráfica', [
        'Boião', 'Adesiva', 'Ideias', 'Business', 'Alcalima']),
    ('P', 'Marketing', 'Marketing digital', ['Lorraine']),

    # ---- Veículos ----
    ('P', 'Veículos', 'Asproauto', []),
    ('P', 'Veículos', 'Caminhão Scania - R540', [
        'Documentos (IPVA/licenciamento)', 'Manutenção', 'Abastecimento',
        'Multas', 'Lavagem', 'Seguro / proteção']),
    ('P', 'Veículos', 'Caminhão Scania - R500', [
        'Documentos (IPVA/licenciamento)', 'Manutenção', 'Abastecimento',
        'Multas', 'Lavagem', 'Seguro / proteção']),
    ('P', 'Veículos', 'Carretinha', [
        'Documentos (IPVA/licenciamento)', 'Manutenção', 'Abastecimento',
        'Multas', 'Lavagem', 'Seguro / proteção']),
    ('P', 'Veículos', 'Classic', [
        'Documentos (IPVA/licenciamento)', 'Manutenção', 'Abastecimento',
        'Multas', 'Lavagem', 'Seguro / proteção']),
    ('P', 'Veículos', 'Commander', [
        'Documentos (IPVA/licenciamento)', 'Manutenção', 'Abastecimento',
        'Multas', 'Lavagem', 'Seguro / proteção']),
    ('P', 'Veículos', 'F250', [
        'Documentos (IPVA/licenciamento)', 'Manutenção', 'Abastecimento',
        'Multas', 'Lavagem', 'Seguro / proteção']),
    ('P', 'Veículos', 'Fiorino', [
        'Documentos (IPVA/licenciamento)', 'Manutenção', 'Abastecimento',
        'Multas', 'Lavagem', 'Seguro / proteção']),
    ('P', 'Veículos', 'SPIN', [
        'Documentos (IPVA/licenciamento)', 'Manutenção', 'Abastecimento',
        'Multas', 'Lavagem', 'Seguro / proteção']),
    ('P', 'Veículos', 'Zafira', [
        'Documentos (IPVA/licenciamento)', 'Manutenção', 'Abastecimento',
        'Multas', 'Lavagem', 'Seguro / proteção']),

    # ---- Comissões ----
    ('P', 'Comissões', 'Alessandra Vassalo', []),
    ('P', 'Comissões', 'Barros Advocacia', []),
    ('P', 'Comissões', 'Bruna Schumann', []),
    ('P', 'Comissões', 'Celso Ricardo', []),
    ('P', 'Comissões', 'Diego (Integração)', []),
    ('P', 'Comissões', 'Gabriel Mendes', []),
    ('P', 'Comissões', 'Guilherme Rocha', []),
    ('P', 'Comissões', 'Julian Amaral', []),
    ('P', 'Comissões', 'Lais Fernanda', []),
    ('P', 'Comissões', 'Nildson - Indicações', []),
    ('P', 'Comissões', 'Paulo - Sinal Verde', []),
    ('P', 'Comissões', 'Rodrigo Silva', []),
    ('P', 'Comissões', 'Vinicius Varela', []),

    # ---- Sócios ----
    ('P', 'Sócios', 'Albert Antunes Vieira', ['Pro-Labore']),
    ('P', 'Sócios', 'Anderson Antunes Vieira', [
        'Pro-Labore', 'Alimentação', 'Banho Bitu', 'Cinema',
        'Vestimentas', 'Julierme', 'Joveci', 'Faculdade', 'Manicure']),

    # ---- Investimentos ----
    ('I', 'Investimentos', 'Apto Cohab II', []),
    ('I', 'Investimentos', 'Apto Oggi Penha', []),
    ('I', 'Investimentos', 'BR-153', ['Manutenção', 'Licenciamento', 'ITR']),
    ('I', 'Investimentos', 'Lote - Morrinhos', ['IPTU', 'Manutenção', 'Parcelas']),
    ('I', 'Investimentos', 'Lote - PB 23', ['IPTU', 'Manutenção', 'Parcelas']),
    ('I', 'Investimentos', 'Lote - São Simão', ['IPTU', 'Manutenção', 'Parcelas']),
    ('I', 'Investimentos', 'Tesouro Direto', ['Selic', 'IPCA', 'Prefixada']),
    ('I', 'Investimentos', 'Renda Fixa', [
        'XP', 'C6', 'Ágora', 'Sicredi', 'Sicoob']),
    ('I', 'Investimentos', 'Ações', ['Ágora']),
    ('I', 'Investimentos', 'Fundos Imóbiliarios', ['Ágora']),

    # ---- Ocupação ----
    ('P', 'Ocupação', 'Apto Oggi Penha', [
        'Condominio', 'Energia', 'Internet', 'Demais', 'Manutenção']),
    ('P', 'Ocupação', 'Aluguel Goiatuba', [
        'Energia', 'Internet', 'Agua', 'Piscinero', 'Manutenção', 'IPTU']),
    ('P', 'Ocupação', 'Apto Unique Tower', [
        'Condominio', 'Energia', 'Internet', 'Agua', 'Manutenção']),

    # ============ TRANSFERENCIA (fora do DRE) ============
    # Nao veio na planilha porque a planilha era so de despesas — e
    # transferencia nao e despesa. Sao os R$ 1,6 mi que andam entre o EFI e o
    # Sicredi: lancados como receita dobrariam o faturamento, e ignorados
    # quebrariam o saldo da conta. Entram no saldo e ficam fora do resultado.
    ('T', 'Transferência', 'Entre contas do grupo', []),
    ('T', 'Transferência', 'Aporte do sócio', []),
    ('T', 'Transferência', 'Retirada do sócio', []),
]


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
