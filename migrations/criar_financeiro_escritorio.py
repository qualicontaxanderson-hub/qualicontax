# -*- coding: utf-8 -*-
"""Cria as tabelas fin_* do financeiro do ESCRITÓRIO (Documento E, 19/08/2026).

Fase 1 do Documento E: contas a pagar/receber da própria Qualicontax —
piloto real antes de qualquer extrato de cliente (Documento A2).

A entidade central é o TÍTULO (fin_titulos): NFS-e, boleto e lançamento de
extrato se PENDURAM nele, nunca o contrário. As regras que o schema carrega:

* ``uk_baixa`` (titulo_id, data_baixa, valor, referencia) — idempotência da
  baixa: webhook e extrato do MESMO pagamento produzem a MESMA chave, a
  segunda gravação colide e só complementa (confirmado_extrato).
* ``uk_idem`` em fin_titulos — geração mensal por contrato rodada 2x não cria
  título duplicado (chave "contrato:{id}:comp:{YYYY-MM}").
* ``status``/``valor_baixado`` são DERIVADOS (recalcular_status é o único
  escritor); ``competencia`` ≠ ``vencimento`` (DRE usa competência, fluxo de
  caixa usa vencimento).

fin_contratos e fin_conciliacao já nascem aqui (schema completo do documento)
mas só ganham uso nas fases 5 e 6. ``extrato_lancamentos`` NÃO nasce aqui —
pertence ao Documento A (importação de extrato).

    python migrations/criar_financeiro_escritorio.py            # dry-run
    python migrations/criar_financeiro_escritorio.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELAS = {
    'fin_titulos': """
CREATE TABLE IF NOT EXISTS fin_titulos (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  tipo            CHAR(1) NOT NULL,
  contraparte_doc  VARCHAR(20),
  contraparte_nome VARCHAR(255) NOT NULL,
  cliente_id      INT NULL,
  categoria_id    INT NOT NULL,
  descricao       VARCHAR(255) NOT NULL,
  competencia     DATE NOT NULL,
  emissao         DATE NOT NULL,
  vencimento      DATE NOT NULL,
  valor           DECIMAL(15,2) NOT NULL,
  valor_baixado   DECIMAL(15,2) NOT NULL DEFAULT 0,
  status          VARCHAR(15) NOT NULL DEFAULT 'aberto',
  contrato_id     INT NULL,
  nfse_id         INT NULL,
  boleto_id       INT NULL,
  origem          VARCHAR(15) DEFAULT 'manual',
  chave_idem      VARCHAR(120) NULL,
  observacao      TEXT,
  criado_em       DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_idem (chave_idem),
  INDEX idx_venc (tipo, status, vencimento),
  INDEX idx_compet (tipo, competencia),
  INDEX idx_contraparte (contraparte_doc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'fin_titulo_baixas': """
CREATE TABLE IF NOT EXISTS fin_titulo_baixas (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  titulo_id      INT NOT NULL,
  data_baixa     DATE NOT NULL,
  valor          DECIMAL(15,2) NOT NULL,
  juros          DECIMAL(15,2) DEFAULT 0,
  multa          DECIMAL(15,2) DEFAULT 0,
  desconto       DECIMAL(15,2) DEFAULT 0,
  origem         VARCHAR(10) NOT NULL,
  referencia     VARCHAR(255),
  lancamento_id  BIGINT NULL,
  confirmado_extrato TINYINT(1) NOT NULL DEFAULT 0,
  usuario_id     INT NULL,
  criado_em      DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_baixa (titulo_id, data_baixa, valor, referencia),
  INDEX idx_nao_confirmado (confirmado_extrato, data_baixa)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'fin_contratos': """
CREATE TABLE IF NOT EXISTS fin_contratos (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  cliente_id     INT NOT NULL,
  descricao      VARCHAR(255) NOT NULL,
  valor          DECIMAL(15,2) NOT NULL,
  dia_vencimento TINYINT NOT NULL,
  categoria_id   INT NOT NULL,
  gerar_nfse     TINYINT(1) DEFAULT 1,
  gerar_boleto   TINYINT(1) DEFAULT 1,
  banco_boleto   VARCHAR(20),
  codigo_servico VARCHAR(20),
  discriminacao  TEXT,
  inicio         DATE NOT NULL,
  fim            DATE NULL,
  reajuste_mes   TINYINT NULL,
  reajuste_indice VARCHAR(10) NULL,
  ativo          TINYINT(1) DEFAULT 1,
  INDEX idx_ativo (ativo, dia_vencimento)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'fin_categorias': """
CREATE TABLE IF NOT EXISTS fin_categorias (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  tipo        CHAR(1) NOT NULL,
  grupo       VARCHAR(50) NOT NULL,
  nome        VARCHAR(100) NOT NULL,
  ordem       INT DEFAULT 0,
  ativo       TINYINT(1) DEFAULT 1,
  UNIQUE KEY uk_cat (tipo, grupo, nome)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
    'fin_conciliacao': """
CREATE TABLE IF NOT EXISTS fin_conciliacao (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  lancamento_id  BIGINT NOT NULL,
  titulo_id      INT NULL,
  metodo         VARCHAR(20),
  confianca      TINYINT,
  status         VARCHAR(15) DEFAULT 'sugerido',
  usuario_id     INT NULL,
  criado_em      DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_lanc (lancamento_id),
  INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""",
}

# Plano GERENCIAL inicial (seção 3.4 + linhas do DRE da seção 7). Gerencial,
# não contábil: serve ao DRE do escritório. `grupo` É a linha do DRE; `ordem`
# dita a posição. Editável em tela na Fase 3.
SEED_CATEGORIAS = [
    # (tipo, grupo, nome, ordem)
    ('R', 'Receita de honorários',   'Honorários contábeis',    10),
    ('R', 'Receita de honorários',   'Honorários avulsos',      11),
    ('R', 'Outras receitas',         'Outras receitas',         20),
    ('P', 'Impostos sobre serviço',  'Simples Nacional',        30),
    ('P', 'Impostos sobre serviço',  'ISS',                     31),
    ('P', 'Pessoal',                 'Salários',                40),
    ('P', 'Pessoal',                 'Pró-labore',              41),
    ('P', 'Pessoal',                 'FGTS / INSS',             42),
    ('P', 'Ocupação',                'Aluguel e condomínio',    50),
    ('P', 'Ocupação',                'Energia / água',          51),
    ('P', 'Tecnologia',              'Sistemas e assinaturas',  60),
    ('P', 'Tecnologia',              'Internet / telefonia',    61),
    ('P', 'Serviços de terceiros',   'Serviços de terceiros',   70),
    ('P', 'Outras despesas',         'Outras despesas',         80),
    ('P', 'Financeiras',             'Tarifas bancárias',       90),
    ('P', 'Financeiras',             'Juros e multas pagos',    91),
]


def tabela_existe(nome: str) -> bool:
    row = execute_query(
        """SELECT COUNT(*) AS n FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s""",
        (nome,), fetch=True, fetch_one=True) or {}
    return bool(row.get('n'))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--apply', action='store_true', help='executa (sem isso é dry-run)')
    args = ap.parse_args()

    faltam = [t for t in TABELAS if not tabela_existe(t)]
    if not faltam:
        print('As 5 tabelas fin_* já existem.')
    else:
        print('Tabelas a criar:', ', '.join(faltam))

    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0

    for nome, ddl in TABELAS.items():
        execute_query(ddl)
        assert tabela_existe(nome), f'{nome} não apareceu'
        print(f'OK: {nome}')

    n_cat = (execute_query('SELECT COUNT(*) AS n FROM fin_categorias',
                           fetch=True, fetch_one=True) or {}).get('n', 0)
    if n_cat == 0:
        for tipo, grupo, nome, ordem in SEED_CATEGORIAS:
            execute_query(
                'INSERT INTO fin_categorias (tipo, grupo, nome, ordem) '
                'VALUES (%s, %s, %s, %s)', (tipo, grupo, nome, ordem))
        print(f'OK: fin_categorias semeada com {len(SEED_CATEGORIAS)} categorias.')
    else:
        print(f'fin_categorias já tem {n_cat} linhas — seed pulado.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
