# -*- coding: utf-8 -*-
"""Emissor de NFS-e — catálogo de SERVIÇOS por empresa (02/11/2026).

Pedido do Anderson: "uma contabilidade faz Balanço, Abertura da empresa e
outras mais; quero que apareça o serviço na nota". Em vez de um único texto
padrão, cada empresa tem a sua lista de serviços, e a nota escolhe um: o nome
aparece na lista, a ``descricao`` vira o texto da nota (aceita ``{mes}``,
``{ano}`` e ``{competencia}``), e os códigos/valor, quando preenchidos, cobrem
o padrão da empresa (``nfse_emissao_config``). Vazio = herda o padrão.

A lista é editada em Financeiro → Notas Fiscais → Configuração → Serviços.
A rotina de honorários (próxima fase) também lê daqui.

Idempotente. Pelo Colab ou daqui:

    python migrations/nfse_emissao_03_servicos.py            # dry-run: só lista
    python migrations/nfse_emissao_03_servicos.py --apply    # cria e semeia
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

QUALICONTAX_ID = 2

DDL = """
CREATE TABLE IF NOT EXISTS nfse_servicos (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id      INT NOT NULL COMMENT 'clientes.id',
  nome            VARCHAR(80) NOT NULL COMMENT 'o que aparece na lista: Honorarios contabeis, Balanco, Abertura de empresa',
  descricao       VARCHAR(1000) NOT NULL COMMENT 'texto da nota; aceita {mes}, {ano} e {competencia}',
  cod_trib_nac    VARCHAR(6) NULL COMMENT 'vazio = o padrao da empresa',
  cod_trib_mun    VARCHAR(3) NULL,
  cod_nbs         VARCHAR(9) NULL,
  cod_servico_sp  VARCHAR(5) NULL,
  valor_padrao    DECIMAL(12,2) NULL COMMENT 'vazio = digita na nota',
  emite_nota      TINYINT NOT NULL DEFAULT 1 COMMENT '1 = entra na nota fiscal; 0 = so no boleto (taxa repassada)',
  ordem           INT NOT NULL DEFAULT 0,
  ativo           TINYINT NOT NULL DEFAULT 1,
  criado_em       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  atualizado_em   DATETIME NULL,
  alterado_por    INT NULL,
  UNIQUE KEY uk_empresa_nome (empresa_id, nome),
  INDEX idx_empresa (empresa_id, ativo, ordem)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# A lista da Qualicontax, lida das planilhas do Conexa (02/11/2026, "Vendas e Contratos").
# Regra do Anderson: o que é honorário é HONORÁRIO (as "Mensalidade Rede X" do Conexa são
# faixas de preço, não serviços; e o 13º também é honorário); o que é serviço leva o nome
# correto. Taxas repassadas (Jucesp, cartório, Correios) entram com emite_nota = 0: vão
# só no boleto, nunca na nota (exemplo dele: honorário 200 + taxa da Junta 50 = nota de 200
# e boleto de 250; honorário 200 + emissão de NF 20 = nota e boleto de 220, os dois itens).
_H = 'Honorários contábeis'
_SERVICOS = [
    # (nome, descrição na nota[, emite_nota])
    (_H, 'Honorários contábeis referentes a {mes} de {ano}'),
    (_H + ' — 13º, 1ª parcela', 'Honorários contábeis — décimo terceiro honorário de {ano}, 1ª parcela'),
    (_H + ' — 13º, 2ª parcela', 'Honorários contábeis — décimo terceiro honorário de {ano}, 2ª parcela'),
    (_H + ' — 13º, parcela única', 'Honorários contábeis — décimo terceiro honorário de {ano}, parcela única'),
    (_H + ' — empresa sem movimento', 'Honorários contábeis referentes a {mes} de {ano} — empresa sem movimento'),
    ('Complemento de honorários', 'Complemento de honorários contábeis referentes a {mes} de {ano}'),
    ('Declaração de Imposto de Renda', 'Elaboração e entrega da Declaração de Imposto de Renda Pessoa Física'),
    ('Abertura de empresa', 'Abertura de empresa: elaboração do contrato social, registro na Junta Comercial e inscrições'),
    ('Abertura de filial', 'Abertura de filial: alteração contratual, registro na Junta Comercial e inscrições'),
    ('Constituição — posto de combustível', 'Constituição de empresa — posto de combustível: contrato social, registro e inscrições'),
    ('Alteração contratual', 'Alteração contratual: elaboração do instrumento e registro na Junta Comercial'),
    ('Alteração contratual — posto de combustível', 'Alteração contratual — posto de combustível: elaboração do instrumento e registro na Junta Comercial'),
    ('Baixa de empresa', 'Baixa de empresa: distrato, baixas nas inscrições e na Junta Comercial'),
    ('Ata de distribuição de lucro', 'Elaboração de ata de distribuição de lucros'),
    ('Elaboração de DRE / Balanço Patrimonial', 'Elaboração de Demonstração do Resultado do Exercício e Balanço Patrimonial'),
    ('Emissão de nota fiscal eletrônica', 'Emissão de notas fiscais eletrônicas por conta do cliente — {mes} de {ano}'),
    ('Folha de pagamento', 'Processamento de folha de pagamento — {mes} de {ano}'),
    ('Arquivo remessa — folha de pagamento', 'Geração de arquivo remessa bancário da folha de pagamento — {mes} de {ano}'),
    ('Sistema de envio de recibos trabalhistas', 'Sistema de envio de recibos trabalhistas — {mes} de {ano}'),
    ('LMC — Livro de Movimentação de Combustíveis', 'Escrituração do Livro de Movimentação de Combustíveis (LMC) — {mes} de {ano}'),
    ('Sistema de emissão de CT-e', 'Sistema de emissão de Conhecimento de Transporte Eletrônico (CT-e) — {mes} de {ano}'),
    ('Endereço virtual', 'Endereço virtual (domicílio fiscal) — {mes} de {ano}'),
    ('Cadastro ANP', 'Cadastro e regularização junto à Agência Nacional do Petróleo (ANP)'),
    ('Cadastro ANTT', 'Cadastro junto à Agência Nacional de Transportes Terrestres (ANTT)'),
    ('Inscrição estadual — substituição tributária', 'Obtenção de inscrição estadual de substituto tributário (ST)'),
    ('Placa de preços ANP / ICMS', 'Confecção de placa de preços exigida pela ANP / ICMS'),
    ('Ressarcimento de impostos', 'Pedido de ressarcimento / restituição de impostos'),
    ('Consulta de pendência', 'Consulta e regularização de pendências cadastrais e fiscais'),
    ('Digitalização de documentos', 'Digitalização e organização de documentos'),
    # só no boleto: repasses de taxa e material
    ('Taxa da Junta Comercial', 'Taxa da Junta Comercial (repasse)', 0),
    ('Taxa de urgência — Jucesp', 'Taxa de urgência da Jucesp (repasse)', 0),
    ('Taxas de cartório', 'Taxas de cartório (repasse)', 0),
    ('Correios', 'Despesa com Correios (repasse)', 0),
    ('Diferença de 13º salário', 'Diferença de 13º salário (repasse)', 0),
]
SEMENTE = [{'empresa_id': QUALICONTAX_ID, 'nome': t[0], 'descricao': t[1],
            'emite_nota': t[2] if len(t) > 2 else 1, 'ordem': i + 1}
           for i, t in enumerate(_SERVICOS)]


def tabela_existe(nome):
    r = execute_query('SHOW TABLES LIKE %s', (nome,), fetch=True)
    return bool(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem ele só lista')
    args = ap.parse_args()

    existe = tabela_existe('nfse_servicos')
    print('nfse_servicos:', 'já existe' if existe else 'criar')
    ja = set()
    if existe:
        ja = {r['nome'] for r in execute_query(
            'SELECT nome FROM nfse_servicos WHERE empresa_id = %s', (QUALICONTAX_ID,), fetch=True) or []}
    faltam = [s for s in SEMENTE if s['nome'] not in ja]
    print(f'semente da Qualicontax (cliente 2): {len(SEMENTE)} serviços, {len(faltam)} a gravar')
    for s in faltam:
        print(f"   {s['ordem']:2}. {s['nome']}  ->  {s['descricao']}{'' if s['emite_nota'] else '   [SÓ BOLETO]'}")

    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0
    if not existe:
        if execute_query(DDL) is None:
            print('FALHOU: tabela não criada.')
            return 1
        assert tabela_existe('nfse_servicos')
        print('OK: nfse_servicos criada.')
    for s in faltam:
        cols = list(s)
        r = execute_query(
            f"INSERT INTO nfse_servicos ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
            tuple(s[c] for c in cols))
        if r is None:
            print(f"FALHOU: {s['nome']!r} não gravado.")
            return 1
    print(f'OK: {len(faltam)} serviços semeados.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
