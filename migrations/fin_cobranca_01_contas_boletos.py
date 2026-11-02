# -*- coding: utf-8 -*-
"""Cobrança (boleto e Pix) — contas de cobrança por empresa e boletos (02/11/2026).

Decisões do Anderson em 02/11/2026:
* Efí primeiro, Bradesco depois. Tudo pelo NOSSO app: a Efí não manda e-mail
  (nós mandamos), Pix sim, carnê/assinatura/link/cartão só preparados, aviso de
  pagamento (webhook) obrigatório — é ele que dá a baixa no título.
* As credenciais NÃO vão para o Railway: cada empresa cadastra a sua conta de
  cobrança na tela Financeiro → Cobrança, e tudo fica cifrado no banco com a
  mesma chave mestra do certificado digital (``DFE_CRYPTO_KEY``). Pôr outra
  empresa, ou um cliente, a cobrar pela Efí dele é preencher a tela.

Duas tabelas:

``fin_cobranca_contas`` — uma linha por conta de cobrança (empresa + provedor +
ambiente). Guarda Client ID / Client Secret / certificado .p12 da API (todos
cifrados), a conta bancária do Financeiro a que pertence (``fin_contas``), a
chave Pix, e o padrão do boleto (juros, multa, dias para baixa, mensagem, dia
de vencimento). ``webhook_segredo`` é o pedaço aleatório da URL que a Efí chama.

``fin_boletos`` — uma linha por boleto emitido: o título (``fin_titulos``) e a
nota (``nfse_emitidas``) a que pertence, a identificação na Efí
(``charge_id``), linha digitável, código de barras, links, Pix copia-e-cola,
situação, pagamento, PDF arquivado e os itens cobrados (JSON, com o que entra
na nota e o que vai só no boleto). ``fin_titulos.boleto_id`` já existia desde
o Documento E e passa a apontar para cá.

Idempotente. Pelo Colab ou daqui:

    python migrations/fin_cobranca_01_contas_boletos.py            # dry-run
    python migrations/fin_cobranca_01_contas_boletos.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL = {
    'fin_cobranca_contas': """
CREATE TABLE IF NOT EXISTS fin_cobranca_contas (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id        INT NOT NULL COMMENT 'clientes.id (empresa do financeiro)',
  provedor          VARCHAR(12) NOT NULL DEFAULT 'efi' COMMENT 'efi | bradesco',
  apelido           VARCHAR(60) NOT NULL COMMENT 'como aparece nas telas: Efi Qualicontax',
  ambiente          VARCHAR(12) NOT NULL DEFAULT 'homologacao' COMMENT 'homologacao | producao',
  fin_conta_id      INT NULL COMMENT 'fin_contas.id: a conta bancaria onde o dinheiro cai',
  client_id_cif     BLOB NULL COMMENT 'Fernet(DFE_CRYPTO_KEY)',
  client_secret_cif BLOB NULL COMMENT 'Fernet(DFE_CRYPTO_KEY)',
  certificado_cif   MEDIUMBLOB NULL COMMENT '.p12 da API (Pix) cifrado; a API de cobrancas nao usa',
  certificado_nome  VARCHAR(120) NULL,
  certificado_senha_cif BLOB NULL,
  chave_pix         VARCHAR(80) NULL,
  juros_mes         DECIMAL(5,2) NOT NULL DEFAULT 1.00 COMMENT '% ao mes',
  multa_pct         DECIMAL(5,2) NOT NULL DEFAULT 2.00 COMMENT '% sobre o valor',
  dias_baixa        SMALLINT NOT NULL DEFAULT 60 COMMENT 'dias apos o vencimento em que o boleto deixa de valer',
  dia_vencimento    TINYINT NULL COMMENT 'dia padrao do mes para os honorarios',
  mensagem_padrao   VARCHAR(400) NULL COMMENT 'ate 4 linhas de 100 no boleto',
  webhook_segredo   VARCHAR(48) NOT NULL COMMENT 'parte aleatoria da URL /webhooks/efi/<segredo>',
  ativo             TINYINT NOT NULL DEFAULT 1,
  testado_em        DATETIME NULL,
  teste_resultado   VARCHAR(255) NULL,
  criado_em         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  atualizado_em     DATETIME NULL,
  alterado_por      INT NULL,
  UNIQUE KEY uk_webhook (webhook_segredo),
  INDEX idx_empresa (empresa_id, ativo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
    'fin_boletos': """
CREATE TABLE IF NOT EXISTS fin_boletos (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id        INT NOT NULL,
  conta_cobranca_id INT NOT NULL COMMENT 'fin_cobranca_contas.id',
  titulo_id         INT NULL COMMENT 'fin_titulos.id (o titulo e o centro)',
  nfse_id           INT NULL COMMENT 'nfse_emitidas.id quando nasce de uma nota',
  provedor          VARCHAR(12) NOT NULL DEFAULT 'efi',
  ambiente          VARCHAR(12) NOT NULL,
  charge_id         VARCHAR(40) NULL COMMENT 'id da cobranca na Efi',
  custom_id         VARCHAR(60) NULL COMMENT 'nosso identificador enviado a Efi',
  situacao          VARCHAR(16) NOT NULL DEFAULT 'rascunho' COMMENT 'rascunho | waiting | paid | unpaid | canceled | settled | erro',
  valor             DECIMAL(15,2) NOT NULL,
  vencimento        DATE NOT NULL,
  pagador_doc       VARCHAR(20) NULL,
  pagador_nome      VARCHAR(255) NULL,
  pagador_email     VARCHAR(255) NULL,
  itens_json        JSON NULL COMMENT '[{nome, valor, emite_nota}]',
  mensagem          VARCHAR(400) NULL,
  linha_digitavel   VARCHAR(80) NULL,
  codigo_barras     VARCHAR(80) NULL,
  link              VARCHAR(255) NULL,
  pdf_url           VARCHAR(255) NULL,
  pix_copia_cola    TEXT NULL,
  pix_qrcode        MEDIUMTEXT NULL COMMENT 'SVG base64 devolvido pela Efi',
  pdf_path          VARCHAR(500) NULL COMMENT 'copia do PDF no Dropbox',
  pago_em           DATE NULL,
  valor_pago        DECIMAL(15,2) NULL,
  notificacao_token VARCHAR(80) NULL,
  ultimo_evento_id  INT NULL COMMENT 'id do ultimo evento da notificacao ja processado',
  email_enviado_em  DATETIME NULL,
  erro              TEXT NULL,
  raw_resposta      JSON NULL,
  criado_por        INT NULL,
  criado_em         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  atualizado_em     DATETIME NULL,
  INDEX idx_empresa (empresa_id, situacao, vencimento),
  INDEX idx_charge (provedor, ambiente, charge_id),
  INDEX idx_titulo (titulo_id),
  INDEX idx_nfse (nfse_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
}


def tabela_existe(nome):
    return bool(execute_query('SHOW TABLES LIKE %s', (nome,), fetch=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem ele só lista')
    args = ap.parse_args()
    pend = []
    for nome in DDL:
        existe = tabela_existe(nome)
        print(f'{nome}:', 'já existe' if existe else 'criar')
        if not existe:
            pend.append(nome)
    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0
    for nome in pend:
        if execute_query(DDL[nome]) is None:
            print(f'FALHOU: {nome} não criada.')
            return 1
        assert tabela_existe(nome)
        print(f'OK: {nome} criada.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
