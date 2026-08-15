# -*- coding: utf-8 -*-
"""Captura de NFS-e pelo ADN — as quatro tabelas.

ADITIVA e IDEMPOTENTE. Não altera, não apaga e nem toca em NENHUMA tabela
existente. São quatro CREATE TABLE IF NOT EXISTS e nada mais.

    dfe_nsu_nfse       cursor por CNPJ (isolado do dfe_nsu da NF-e)
    nfse_capturadas    os documentos
    nfse_eventos       os eventos — tabela SEPARADA, ver abaixo
    nfse_consulta_log  trilha de execução

REVERSÍVEL DE PROPÓSITO. O projeto ainda não passou pelo go/no-go: a Fase 0
provou que o ADN entrega (189 documentos reais, Novo Horizonte com 85 tomadas
desde março/2024), mas a cobertura contra o lançamento manual não foi medida.
Se decepcionar, o rollback é derrubar quatro tabelas vazias — está impresso no
fim e não há dado de cliente em jogo.

AS DUAS DECISÕES DE MODELAGEM QUE NÃO SÃO ÓBVIAS
------------------------------------------------

1) nfse_capturadas tem chave única (chave_acesso, papel), não só a chave.
   Se DUAS empresas da carteira forem partes da mesma nota — uma presta, outra
   toma —, o documento chega pelos dois cursores com papéis diferentes, e os
   dois registros são legítimos. Chave simples apagaria um deles em silêncio.

2) nfse_eventos é tabela separada, e o evento é gravado EXISTA OU NÃO o
   documento. Eventos chegam com NSU próprio, independente da nota. O bug
   clássico é aplicar evento só em linha existente e descartar o resto: aí o
   evento órfão desaparece e, quando o documento finalmente chega, entra como
   'ativa' — permanentemente errado e sem sinal nenhum.

   E o evento COLAPSA entre cursores (uma linha), ao contrário do documento: um
   cancelamento é UM fato do documento, não dois. Por isso os campos de origem
   se chamam *_origem — são PROVENIÊNCIA, nunca filtro. Para os eventos de uma
   empresa: nfse_capturadas (filtrada por empresa_id) → JOIN por
   chave_acesso = chave_referenciada.

Spec completa: docs/NFSE_ADN_ESPECIFICACAO.md

Uso:
    python migrations/add_nfse_adn.py            # DRY-RUN (nada grava)
    python migrations/add_nfse_adn.py --apply    # cria de verdade
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector                                          # noqa: E402
from config import Config                                       # noqa: E402

TABELAS = {}

TABELAS['dfe_nsu_nfse'] = """
CREATE TABLE IF NOT EXISTS dfe_nsu_nfse (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id       INT NOT NULL,
  cnpj             VARCHAR(14) NOT NULL,
  ult_nsu          BIGINT NOT NULL DEFAULT 0,
  modo             VARCHAR(15) NOT NULL DEFAULT 'backfill'
                   COMMENT 'backfill|incremental',
  ultima_exec      DATETIME NULL,
  ultimo_sucesso   DATETIME NULL,
  tentativas_falha INT NOT NULL DEFAULT 0,
  ultimo_erro      TEXT NULL,
  ativo            TINYINT(1) NOT NULL DEFAULT 1,
  criado_em        DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_cnpj (cnpj),
  INDEX idx_modo_ativo (modo, ativo),
  INDEX idx_ultimo_sucesso (ultimo_sucesso)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Cursor de NSU do ADN. Isolado do dfe_nsu (NF-e): sao filas diferentes'
"""

TABELAS['nfse_capturadas'] = """
CREATE TABLE IF NOT EXISTS nfse_capturadas (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  empresa_id        INT NOT NULL,
  cnpj_interessado  VARCHAR(14) NOT NULL COMMENT 'de quem e o cursor',
  nsu               BIGINT NOT NULL,
  chave_acesso      VARCHAR(60) NOT NULL COMMENT '50 digitos (o Id do infNFSe tem 53)',
  papel             VARCHAR(15) NOT NULL
                    COMMENT 'emitente|tomador|intermediario. emitente = PRESTADOR',
  tipo_doc          VARCHAR(15) NOT NULL DEFAULT 'nfse',

  numero            VARCHAR(30) NULL,
  serie             VARCHAR(10) NULL,
  data_emissao      DATETIME NULL COMMENT 'dhEmi, declarada',
  data_processamento DATETIME NULL COMMENT 'dhProc, da autoridade',
  competencia       DATE NULL COMMENT 'dCompet. USAR ESTA na apuracao, nao dhEmi',
  municipio_ibge    VARCHAR(7) NULL COMMENT 'cLocIncid, incidencia do ISS',
  municipio_emissao VARCHAR(7) NULL,

  prestador_doc     VARCHAR(20) NULL,
  prestador_nome    VARCHAR(255) NULL,
  tomador_doc       VARCHAR(20) NULL,
  tomador_nome      VARCHAR(255) NULL,
  intermediario_doc VARCHAR(20) NULL,
  destinatario_doc  VARCHAR(20) NULL
                    COMMENT 'QUARTO ator. E DADO, nunca papel: o ADN nao distribui a ele',
  destinatario_nome VARCHAR(255) NULL,

  codigo_servico     VARCHAR(20) NULL COMMENT 'cTribNac, LC 116',
  codigo_servico_mun VARCHAR(10) NULL,
  codigo_nbs         VARCHAR(20) NULL,
  discriminacao      TEXT NULL,

  valor_servicos    DECIMAL(15,2) NULL COMMENT 'declarado',
  valor_desc_incond DECIMAL(15,2) NULL,
  valor_desc_cond   DECIMAL(15,2) NULL,
  base_calculo      DECIMAL(15,2) NULL COMMENT 'vBC do ISS, CALCULADO pela autoridade',
  aliquota_iss      DECIMAL(7,4) NULL,
  valor_iss         DECIMAL(15,2) NULL,
  total_retencoes   DECIMAL(15,2) NULL,
  valor_liquido     DECIMAL(15,2) NULL,
  iss_retido        TINYINT(1) NULL COMMENT '1|0|NULL — nao informado NAO e nao retido',
  opt_simples       TINYINT NULL COMMENT 'opSimpNac: 3 = ME/EPP',

  cstat             SMALLINT NULL
                    COMMENT 'GERACAO (100/101/102/103/107). NAO indica cancelamento. 101 = ESTA nota E a substituta',
  situacao          VARCHAR(20) NOT NULL DEFAULT 'ativa'
                    COMMENT 'ativa|cancelada|substituida. SO recalcular_situacao() escreve aqui',
  chave_substituta  VARCHAR(60) NULL COMMENT 'a nota que substituiu ESTA (vem do evento)',
  substitui_chave   VARCHAR(60) NULL
                    COMMENT 'subst/chSubstda: a nota que ESTA substituiu. SENTIDO OPOSTO ao de cima. Verificacao cruzada, nunca escreve situacao',

  restricao_eventos TINYINT(1) NOT NULL DEFAULT 0
                    COMMENT 'Municipio impediu o CANCELAMENTO desta NFS-e. Ela segue ATIVA e VALIDA',
  restricao_codigos VARCHAR(200) NULL COMMENT 'codEvento: dominio fechado de 5 valores',
  restricao_em      DATETIME NULL,

  ibscbs_cst        VARCHAR(3) NULL COMMENT 'CST do IBS/CBS. NAO confundir com o CST do PIS/COFINS',
  ibscbs_cclasstrib VARCHAR(10) NULL,
  ibscbs_fin_nfse   TINYINT NULL,
  ibscbs_cind_op    VARCHAR(10) NULL,
  ibscbs_ind_dest   TINYINT NULL,
  ibscbs_bc         DECIMAL(15,2) NULL COMMENT 'vBC do IBS/CBS. NAO e o vBC do ISS',
  ibs_uf_aliq_efet  DECIMAL(7,4) NULL,
  ibs_uf_valor      DECIMAL(15,2) NULL,
  ibs_uf_dif        DECIMAL(15,2) NULL,
  ibs_mun_aliq_efet DECIMAL(7,4) NULL,
  ibs_mun_valor     DECIMAL(15,2) NULL,
  ibs_mun_dif       DECIMAL(15,2) NULL,
  ibs_total         DECIMAL(15,2) NULL,
  cbs_aliq_efet     DECIMAL(7,4) NULL,
  cbs_valor         DECIMAL(15,2) NULL,
  cbs_dif           DECIMAL(15,2) NULL,
  ibs_cred_pres     DECIMAL(15,2) NULL,
  cbs_cred_pres     DECIMAL(15,2) NULL,
  valor_total_nf    DECIMAL(15,2) NULL,

  xml_path          VARCHAR(500) NULL COMMENT 'Dropbox',
  raw_json          JSON NULL COMMENT 'envelope do ADN. SEMPRE gravar',
  criado_em         DATETIME DEFAULT CURRENT_TIMESTAMP,

  UNIQUE KEY uk_chave_papel (chave_acesso, papel),
  INDEX idx_empresa_comp (empresa_id, competencia),
  INDEX idx_empresa_nsu (empresa_id, nsu),
  INDEX idx_papel (empresa_id, papel, competencia),
  INDEX idx_situacao (situacao)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='NFS-e capturadas do ADN. Chave unica (chave_acesso, papel): duas empresas da carteira na mesma nota geram DUAS linhas'
"""

TABELAS['nfse_eventos'] = """
CREATE TABLE IF NOT EXISTS nfse_eventos (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  empresa_id_origem INT NOT NULL
                    COMMENT 'PROVENIENCIA: qual cursor entregou primeiro. NAO filtrar eventos por empresa com este campo',
  cnpj_origem       VARCHAR(14) NOT NULL COMMENT 'idem',
  nsu_origem        BIGINT NOT NULL
                    COMMENT 'NSU no cursor de origem. Outros cursores tem NSU diferente para o MESMO evento',

  chave_referenciada VARCHAR(60) NOT NULL COMMENT 'chNFSe: a NFS-e a que o evento se refere',
  tipo_evento       VARCHAR(40) NOT NULL COMMENT 'enum do ADN. No XML o tipo e o NOME DO ELEMENTO (e101101, e105102...)',
  sequencia         INT NOT NULL COMMENT 'nSeqEvento, 3 digitos, obrigatorio no leiaute',
  data_evento       DATETIME NULL,
  motivo            TEXT NULL,
  chave_substituta  VARCHAR(60) NULL
                    COMMENT 'e105102: aponta para a nota NOVA. Sentido OPOSTO ao subst/chSubstda do documento',

  aplicado          TINYINT(1) NOT NULL DEFAULT 0,
  orfao             TINYINT(1) NOT NULL DEFAULT 0
                    COMMENT 'documento ainda nao capturado. O evento e gravado do mesmo jeito',
  revisar           TINYINT(1) NOT NULL DEFAULT 0
                    COMMENT 'tipo AUSENTE do MAPA_EVENTO_SITUACAO. Requer analise humana',

  raw_json          JSON NULL COMMENT 'SEMPRE',
  criado_em         DATETIME DEFAULT CURRENT_TIMESTAMP,

  UNIQUE KEY uk_evento (chave_referenciada, tipo_evento, sequencia),
  INDEX idx_chave (chave_referenciada),
  INDEX idx_orfao (orfao, aplicado),
  INDEX idx_revisar (revisar),
  INDEX idx_empresa (empresa_id_origem, criado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Eventos de NFS-e. Gravado EXISTA OU NAO o documento (orfao=1). COLAPSA entre cursores: um cancelamento e UM fato do documento'
"""

TABELAS['nfse_consulta_log'] = """
CREATE TABLE IF NOT EXISTS nfse_consulta_log (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  empresa_id     INT NULL,
  cnpj           VARCHAR(14) NULL,
  modo           VARCHAR(15) NULL COMMENT 'backfill|incremental',
  nsu_inicial    BIGINT NULL,
  nsu_final      BIGINT NULL,
  qtd_docs       INT NOT NULL DEFAULT 0,
  qtd_salvos     INT NOT NULL DEFAULT 0,
  qtd_duplicados INT NOT NULL DEFAULT 0,
  http_status    INT NULL,
  duracao_ms     INT NULL,
  erro           TEXT NULL,
  criado_em      DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_empresa_data (empresa_id, criado_em),
  INDEX idx_erro (criado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Trilha de execucao da captura de NFS-e. Preenchida em TODA execucao, inclusive nas que falham'
"""


def main():
    aplicar = '--apply' in sys.argv
    cn = mysql.connector.connect(
        host=Config.DB_HOST, port=Config.DB_PORT, database=Config.DB_NAME,
        user=Config.DB_USER, password=Config.DB_PASSWORD, charset='utf8mb4')
    cur = cn.cursor()

    print('=' * 70)
    print('CAPTURA DE NFS-e (ADN) — quatro tabelas')
    print('MODO:', 'APLICAR' if aplicar else 'DRY-RUN (nada sera gravado)')
    print('banco:', Config.DB_NAME, '@', Config.DB_HOST)
    print('=' * 70)

    criadas, ja_existiam = [], []
    for nome, ddl in TABELAS.items():
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s", (Config.DB_NAME, nome))
        existe = cur.fetchone()[0] > 0
        if existe:
            ja_existiam.append(nome)
            print(f'  {nome:<22} JA EXISTE — nada a fazer')
            continue
        if aplicar:
            cur.execute(ddl)
            criadas.append(nome)
            print(f'  {nome:<22} CRIADA')
        else:
            print(f'  {nome:<22} seria criada')

    if aplicar:
        cn.commit()
        # Confere de verdade, em vez de confiar no que acabamos de mandar.
        print()
        for nome in TABELAS:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s", (Config.DB_NAME, nome))
            n = cur.fetchone()[0]
            cur.execute(f'SELECT COUNT(*) FROM `{nome}`')
            linhas = cur.fetchone()[0]
            print(f'  conferido: {nome:<22} {n:>2} colunas, {linhas} linha(s)')

    print()
    print('-' * 70)
    if criadas:
        print('ROLLBACK (derruba SO o que esta migration criou agora):')
        for nome in reversed(criadas):
            print(f'    DROP TABLE {nome};')
        print()
        print('Seguro: as tabelas nascem VAZIAS e nenhuma tabela existente foi')
        print('tocada. Nada de cliente e perdido no rollback.')
    elif not aplicar:
        print('Rode com --apply para criar. O rollback sera impresso no fim.')
    else:
        print('Nada foi criado (todas ja existiam).')
    if ja_existiam and aplicar:
        print()
        print('NAO derrube estas — ja existiam antes:', ', '.join(ja_existiam))

    cur.close()
    cn.close()


if __name__ == '__main__':
    main()
