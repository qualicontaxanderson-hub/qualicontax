# -*- coding: utf-8 -*-
"""Imposto de Renda Pessoa Física — declarações lidas do programa da Receita (.DEC/.REC).
ADITIVA e IDEMPOTENTE: só CREATE TABLE IF NOT EXISTS.

Por que existe (Anderson, 18/11/2026): "colocar o sistema para fazer a leitura dos Imposto de
Renda pessoa física... ter os dados, relatórios e conseguir analisar mais rápido". O programa
IRPF deixa em C:\\Arquivos de Programas RFB\\IRPF{ano}\\transmitidas um .DEC (declaração inteira,
texto de largura fixa) e um .REC (recibo) por declaração entregue. O Q-Colabore manda esses
arquivos para a _ENTRADA e o cron do roteador lê (utils/irpf/ingest.py).

irpf_declaracoes   uma linha por .DEC lido: quem, exercício, modelo, resultado, totais, recibo,
                   vínculo com o cadastro (cliente_id pelo CPF) e onde o arquivo ficou no Dropbox.
                   hash_dec único: reler o mesmo arquivo não duplica.
                   vigente = a última entregue do CPF no exercício (retificadora derruba a original).
irpf_rendimentos   rendimentos: PJ (titular/dependente), PF/exterior mensal, isentos, exclusivos.
irpf_dependentes   dependentes da declaração.
irpf_bens          bens e direitos (situação anterior e atual).
irpf_dividas       dívidas e ônus.
irpf_pagamentos    pagamentos efetuados (saúde, educação, previdência, pensão...).
SEM foreign key de propósito (como nfe_pagamentos): a exclusão da declaração apaga os filhos
pelo código. cliente_id aponta para clientes (cliente ou avulso); NULL = sem cadastro.

ROLLBACK: DROP TABLE irpf_pagamentos; DROP TABLE irpf_dividas; DROP TABLE irpf_bens;
          DROP TABLE irpf_dependentes; DROP TABLE irpf_rendimentos; DROP TABLE irpf_declaracoes;
Uso:
    python migrations/add_irpf.py            # DRY-RUN (nada grava)
    python migrations/add_irpf.py --apply    # aplica
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mysql.connector                               # noqa: E402
from config import Config                            # noqa: E402

_FIM = ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
TABELAS = {
    'irpf_declaracoes': """
CREATE TABLE IF NOT EXISTS irpf_declaracoes (
    id                 INT           NOT NULL AUTO_INCREMENT,
    cpf                CHAR(11)      NOT NULL,
    nome               VARCHAR(80)   NULL,
    cliente_id         INT           NULL,
    vinculo            VARCHAR(12)   NOT NULL DEFAULT 'sem_cadastro',
    exercicio          SMALLINT      NOT NULL,
    ano_base           SMALLINT      NULL,
    retificadora       TINYINT(1)    NOT NULL DEFAULT 0,
    completa           TINYINT(1)    NOT NULL DEFAULT 0,
    tipo_declaracao    CHAR(1)       NULL,
    vigente            TINYINT(1)    NOT NULL DEFAULT 1,
    resultado          VARCHAR(10)   NULL,
    vr_restituir       DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_pagar           DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_tributaveis     DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_isentos         DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_exclusivos      DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_base_calculo    DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_imposto_devido  DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_imposto_retido  DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_bens_anterior   DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_bens_atual      DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_dividas_atual   DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_pagamentos      DECIMAL(15,2) NOT NULL DEFAULT 0,
    quotas             TINYINT       NULL,
    vr_quota           DECIMAL(15,2) NULL,
    banco              VARCHAR(5)    NULL,
    agencia            VARCHAR(10)   NULL,
    conta              VARCHAR(25)   NULL,
    nr_hash            CHAR(10)      NULL,
    recibo             VARCHAR(14)   NULL,
    recibo_anterior    VARCHAR(14)   NULL,
    transmitida_em     DATETIME      NULL,
    recibo_local       VARCHAR(30)   NULL,
    recibo_mensagem    VARCHAR(255)  NULL,
    dt_nascimento      DATE          NULL,
    email              VARCHAR(120)  NULL,
    telefone           VARCHAR(20)   NULL,
    uf                 CHAR(2)       NULL,
    municipio          VARCHAR(60)   NULL,
    cep                VARCHAR(9)    NULL,
    ocupacao           VARCHAR(5)    NULL,
    conjuge_cpf        CHAR(11)      NULL,
    arquivo_base       VARCHAR(80)   NULL,
    arquivo_dec        VARCHAR(255)  NULL,
    arquivo_rec        VARCHAR(255)  NULL,
    arquivo_pdf        VARCHAR(255)  NULL,
    hash_dec           CHAR(64)      NOT NULL,
    origem             VARCHAR(10)   NOT NULL DEFAULT 'colabore',
    leiaute_usado      SMALLINT      NULL,
    aproximado         TINYINT(1)    NOT NULL DEFAULT 0,
    crc_falhas         SMALLINT      NOT NULL DEFAULT 0,
    aviso              VARCHAR(255)  NULL,
    lido_em            DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    criado_por         INT           NULL,
    atualizado_em      DATETIME      NULL ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_hash (hash_dec),
    KEY idx_cpf_ex (cpf, exercicio),
    KEY idx_cliente (cliente_id),
    KEY idx_ex (exercicio, vigente)
""" + _FIM,
    'irpf_rendimentos': """
CREATE TABLE IF NOT EXISTS irpf_rendimentos (
    id              INT           NOT NULL AUTO_INCREMENT,
    declaracao_id   INT           NOT NULL,
    bloco           VARCHAR(20)   NOT NULL,
    dependente_cpf  CHAR(11)      NULL,
    fonte_doc       VARCHAR(14)   NULL,
    fonte_nome      VARCHAR(80)   NULL,
    codigo          VARCHAR(5)    NULL,
    mes             TINYINT       NULL,
    valor           DECIMAL(15,2) NOT NULL DEFAULT 0,
    previdencia     DECIMAL(15,2) NOT NULL DEFAULT 0,
    decimo_terceiro DECIMAL(15,2) NOT NULL DEFAULT 0,
    imposto_retido  DECIMAL(15,2) NOT NULL DEFAULT 0,
    irrf_13         DECIMAL(15,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    KEY idx_decl (declaracao_id),
    KEY idx_fonte (fonte_doc)
""" + _FIM,
    'irpf_dependentes': """
CREATE TABLE IF NOT EXISTS irpf_dependentes (
    id              INT           NOT NULL AUTO_INCREMENT,
    declaracao_id   INT           NOT NULL,
    chave           VARCHAR(5)    NULL,
    codigo          VARCHAR(2)    NULL,
    nome            VARCHAR(80)   NULL,
    nascimento      DATE          NULL,
    cpf             CHAR(11)      NULL,
    PRIMARY KEY (id),
    KEY idx_decl (declaracao_id)
""" + _FIM,
    'irpf_bens': """
CREATE TABLE IF NOT EXISTS irpf_bens (
    id              INT           NOT NULL AUTO_INCREMENT,
    declaracao_id   INT           NOT NULL,
    chave           VARCHAR(5)    NULL,
    grupo           VARCHAR(2)    NULL,
    codigo          VARCHAR(2)    NULL,
    exterior        TINYINT(1)    NOT NULL DEFAULT 0,
    pais            VARCHAR(3)    NULL,
    descricao       TEXT          NULL,
    vr_anterior     DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_atual        DECIMAL(15,2) NOT NULL DEFAULT 0,
    cpf_cnpj        VARCHAR(14)   NULL,
    banco           VARCHAR(5)    NULL,
    conta           VARCHAR(25)   NULL,
    matricula       VARCHAR(40)   NULL,
    area            DECIMAL(12,1) NULL,
    renavam         VARCHAR(30)   NULL,
    dt_aquisicao    DATE          NULL,
    PRIMARY KEY (id),
    KEY idx_decl (declaracao_id),
    KEY idx_doc (cpf_cnpj)
""" + _FIM,
    'irpf_dividas': """
CREATE TABLE IF NOT EXISTS irpf_dividas (
    id              INT           NOT NULL AUTO_INCREMENT,
    declaracao_id   INT           NOT NULL,
    codigo          VARCHAR(2)    NULL,
    descricao       TEXT          NULL,
    vr_anterior     DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_atual        DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_pago         DECIMAL(15,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    KEY idx_decl (declaracao_id)
""" + _FIM,
    'irpf_pagamentos': """
CREATE TABLE IF NOT EXISTS irpf_pagamentos (
    id                 INT           NOT NULL AUTO_INCREMENT,
    declaracao_id      INT           NOT NULL,
    codigo             VARCHAR(2)    NULL,
    dependente_chave   VARCHAR(5)    NULL,
    beneficiario_doc   VARCHAR(14)   NULL,
    beneficiario_nome  VARCHAR(80)   NULL,
    valor              DECIMAL(15,2) NOT NULL DEFAULT 0,
    vr_nao_dedutivel   DECIMAL(15,2) NOT NULL DEFAULT 0,
    tipo               CHAR(1)       NULL,
    descricao          VARCHAR(512)  NULL,
    PRIMARY KEY (id),
    KEY idx_decl (declaracao_id)
""" + _FIM,
}
ROLLBACK = ("DROP TABLE irpf_pagamentos; DROP TABLE irpf_dividas; DROP TABLE irpf_bens; "
            "DROP TABLE irpf_dependentes; DROP TABLE irpf_rendimentos; DROP TABLE irpf_declaracoes;")


def main():
    aplicar = '--apply' in sys.argv
    cn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD)
    cur = cn.cursor()
    print(f"#  banco: {Config.DB_USER}@{Config.DB_HOST}:{Config.DB_PORT}/{Config.DB_NAME}")
    criar = []
    for nome, ddl in TABELAS.items():
        cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = %s", (nome,))
        existe = cur.fetchone()[0] > 0
        print(f"#  {nome}: {'JÁ EXISTE — nada a fazer' if existe else 'não existe — será criada'}")
        if not existe:
            criar.append(nome)
    if not criar:
        return
    if not aplicar:
        print("\n#  DRY-RUN: nada gravado. Rode com --apply para criar.")
        return
    for nome in criar:
        cur.execute(TABELAS[nome])
    cn.commit()
    print(f"\n#  APLICADO: {', '.join(criar)} criada(s).  Rollback: {ROLLBACK}")


if __name__ == '__main__':
    main()
