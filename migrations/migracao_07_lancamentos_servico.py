# -*- coding: utf-8 -*-
"""Parte 6, etapa 2 — lançamentos de serviço avulso, lote mensal e comissões (06/11/2026).

O que o Anderson desenhou em 05–06/11/2026:
* Gabriel, Guilherme, Melke e outros LANÇAM um serviço esporádico numa empresa. Na hora escolhem o
  destino: AVULSO (cobrança própria, só dela) ou MENSAL (entra na cobrança mensal da empresa, junto
  com honorários e 13º). O Anderson APROVA; a aprovação libera boleto + nota (se o serviço emite nota).
* Corte do mês: depois que o lote mensal da competência saiu (ex. 25/11), lançamento MENSAL feito
  depois (26/11) cai na competência seguinte.
* Quem lança ganha COMISSÃO por acordo próprio (pessoa × serviço: % ou valor fixo), SÓ DEPOIS DE PAGO.
  Cada um vê só os seus. Pagos entram numa lista; a pessoa escolhe e gera um FECHAMENTO (lote) que
  manda ao Anderson; o fechamento não se repete; o pagamento (inteiro ou em partes) sai da conta da
  Qualicontax e o extrato classifica como comissão daquela pessoa.
* Comissão sobre HONORÁRIOS de cliente "dele" (ex. 25%) e INDICAÇÃO (prêmio à vista ou em 2x,
  aprovado pelo Anderson) usam a mesma tabela de acordos, com ``base`` diferente.

    python migrations/migracao_07_lancamentos_servico.py            # dry-run
    python migrations/migracao_07_lancamentos_servico.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402

TABELAS = {
    'fin_lotes_mensais': """
CREATE TABLE IF NOT EXISTS fin_lotes_mensais (
    id INT NOT NULL AUTO_INCREMENT,
    empresa_id INT NOT NULL COMMENT 'clientes.id da emissora (Qualicontax = 2)',
    competencia DATE NOT NULL COMMENT 'dia 1 do mes',
    status VARCHAR(10) NOT NULL DEFAULT 'ABERTO' COMMENT 'ABERTO|EMITIDO',
    emitido_por INT NULL,
    emitido_em DATETIME NULL,
    qtd_cobrancas INT NULL,
    total DECIMAL(15,2) NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_lote_comp (empresa_id, competencia)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='um por competencia: EMITIDO fecha o mes (corte dos lancamentos MENSAL)'
""",
    'fin_comissao_acordos': """
CREATE TABLE IF NOT EXISTS fin_comissao_acordos (
    id INT NOT NULL AUTO_INCREMENT,
    usuario_id INT NOT NULL,
    base VARCHAR(12) NOT NULL DEFAULT 'SERVICO' COMMENT 'SERVICO (avulso) | HONORARIOS (cliente dele) | INDICACAO',
    servico_id INT NULL COMMENT 'nfse_servicos; NULL = qualquer servico (base SERVICO)',
    cliente_id INT NULL COMMENT 'base HONORARIOS/INDICACAO: a empresa',
    tipo VARCHAR(5) NOT NULL DEFAULT 'PCT' COMMENT 'PCT | FIXO',
    valor DECIMAL(15,2) NOT NULL COMMENT 'percentual (PCT) ou reais (FIXO)',
    parcelas TINYINT NOT NULL DEFAULT 1 COMMENT 'INDICACAO: a vista (1) ou em 2x',
    vigencia_inicio DATE NOT NULL,
    vigencia_fim DATE NULL,
    ativo TINYINT(1) NOT NULL DEFAULT 1,
    aprovado_por INT NULL,
    aprovado_em DATETIME NULL,
    observacao VARCHAR(255) NULL,
    criado_por INT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_ca_usuario (usuario_id, base, ativo),
    KEY idx_ca_cliente (cliente_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='acordo de comissao: cada pessoa tem o seu, por servico ou por cliente'
""",
    'fin_comissao_fechamentos': """
CREATE TABLE IF NOT EXISTS fin_comissao_fechamentos (
    id INT NOT NULL AUTO_INCREMENT,
    usuario_id INT NOT NULL,
    total DECIMAL(15,2) NOT NULL,
    qtd_itens INT NOT NULL DEFAULT 0,
    status VARCHAR(12) NOT NULL DEFAULT 'ENVIADO' COMMENT 'ENVIADO|PAGO_PARCIAL|PAGO|CANCELADO',
    valor_pago DECIMAL(15,2) NOT NULL DEFAULT 0,
    observacao VARCHAR(255) NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_cf_usuario (usuario_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='lote de comissoes que a pessoa fecha e manda ao Anderson para pagar'
""",
    'fin_comissao_pagamentos': """
CREATE TABLE IF NOT EXISTS fin_comissao_pagamentos (
    id INT NOT NULL AUTO_INCREMENT,
    fechamento_id INT NOT NULL,
    data DATE NOT NULL,
    valor DECIMAL(15,2) NOT NULL,
    titulo_id INT NULL COMMENT 'fin_titulos (a pagar) criado para o pagamento',
    extrato_id BIGINT NULL COMMENT 'extrato_lancamentos que saiu da conta',
    registrado_por INT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_cp_fech (fechamento_id),
    CONSTRAINT fk_cp_fech FOREIGN KEY (fechamento_id) REFERENCES fin_comissao_fechamentos (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='pagamentos de um fechamento (pode ser em partes: 2.500 em 15/11 + 2.500 depois)'
""",
    'fin_lancamentos': """
CREATE TABLE IF NOT EXISTS fin_lancamentos (
    id INT NOT NULL AUTO_INCREMENT,
    empresa_id INT NOT NULL COMMENT 'clientes.id da emissora (Qualicontax = 2)',
    cliente_id INT NOT NULL COMMENT 'a empresa cobrada',
    servico_id INT NOT NULL COMMENT 'nfse_servicos',
    descricao VARCHAR(200) NOT NULL,
    quantidade DECIMAL(10,2) NOT NULL DEFAULT 1,
    valor_unitario DECIMAL(15,2) NOT NULL,
    valor_total DECIMAL(15,2) NOT NULL,
    destino VARCHAR(7) NOT NULL COMMENT 'AVULSO | MENSAL',
    competencia DATE NOT NULL COMMENT 'MENSAL: mes em que entra na cobranca; AVULSO: mes do servico',
    vencimento DATE NULL COMMENT 'AVULSO: vencimento do boleto proprio',
    observacao VARCHAR(500) NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'LANCADO' COMMENT 'LANCADO|APROVADO|REPROVADO|FATURADO|CANCELADO',
    lancado_por INT NOT NULL,
    lancado_em DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    aprovado_por INT NULL,
    aprovado_em DATETIME NULL,
    motivo VARCHAR(255) NULL COMMENT 'reprovacao ou cancelamento',
    titulo_id INT NULL,
    nfse_id INT NULL,
    boleto_id INT NULL,
    lote_id INT NULL COMMENT 'fin_lotes_mensais quando foi no mensal',
    comissao_acordo_id INT NULL,
    comissao_valor DECIMAL(15,2) NULL COMMENT 'calculada na aprovacao pelo acordo vigente',
    comissao_status VARCHAR(10) NULL COMMENT 'AGUARDANDO (nao pago) | LIBERADA (pago) | FECHADA | PAGA | SEM',
    fechamento_id INT NULL,
    criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_fl_cliente (cliente_id, competencia),
    KEY idx_fl_status (empresa_id, status, destino, competencia),
    KEY idx_fl_quem (lancado_por, comissao_status),
    KEY idx_fl_titulo (titulo_id),
    KEY idx_fl_fech (fechamento_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='servico esporadico lancado por um funcionario: avulso ou dentro do mensal; aprovado pelo Anderson'
""",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    faltam = [t for t in TABELAS if not execute_query('SHOW TABLES LIKE %s', (t,), fetch=True)]
    print('tabelas a criar:', faltam or 'nenhuma')
    if not faltam:
        return
    if not a.apply:
        print('DRY-RUN. Rode com --apply.')
        return
    for t in faltam:
        execute_query(TABELAS[t], fetch=False)
        print('  + tabela', t)
    print('pronto.')


if __name__ == '__main__':
    main()
