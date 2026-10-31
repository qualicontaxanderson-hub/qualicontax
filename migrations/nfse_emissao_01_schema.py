# -*- coding: utf-8 -*-
"""Emissor de NFS-e nacional — Fase 2: schema (31/10/2026).

Duas tabelas, nada mais. Sem montador de DPS, sem tela, sem envio.

* ``nfse_dps_controle`` — a numeração. Uma linha por (empresa, série) e o
  último número usado. A Qualicontax já emite pelo Conexa desde setembro com
  a série de lá; aqui ela ganha série PRÓPRIA, independente — por isso a chave
  única é (empresa_id, serie) e não só empresa. O número só anda por
  ``proximo_numero_dps()`` (utils/integrations/nfse_adn/numeracao.py), que faz
  SELECT ... FOR UPDATE: dois pedidos ao mesmo tempo nunca saem com o mesmo
  número.

* ``nfse_emitidas`` — cada DPS que o sistema montou, do rascunho à NFS-e
  autorizada (ou rejeitada, cancelada, substituída). Uma linha por DPS; a
  resposta do governo preenche a mesma linha (chave, número da NFS-e, cStat).

MULTIEMPRESA DESDE O INÍCIO
---------------------------
``empresa_id`` = ``clientes.id``, igual a ``nfse_capturadas`` e ao financeiro
(Qualicontax é o cliente 2). Cliente ME/EPP do Simples emite pelo mesmo motor,
separado por esse campo. O certificado vem de ``resolver_certificado(cliente_id)``
do ``client.py`` que já existe.

IBS/CBS
-------
O grupo DECLARADO da DPS (``infDPS/IBSCBS``) tem cinco campos obrigatórios no
leiaute vigente (Patch 7 do Doc D): CST, cClassTrib, finNFSe, cIndOp, indDest.
Entram como NOT NULL com DEFAULT para o rascunho poder nascer antes de a tela
perguntar — o montador da DPS (Fase 3) é quem recusa emitir se ficaram no
padrão. Os valores CALCULADOS pela autoridade (IBS-UF, IBS-Município, CBS) vêm
na NFS-e de volta e ficam em colunas separadas: são entes diferentes, somar
destrói a apuração (Doc D §7.3). Os nomes das colunas são os MESMOS de
``nfse_capturadas`` para a apuração ler as duas tabelas com o mesmo código.

XML
---
A DPS assinada fica no banco (``xml_dps``, uns 5 KB): é ela que se reenvia
quando a resposta não chegou, e reenviar DPS diferente com o mesmo número é
rejeição certa. A NFS-e devolvida vai para o Dropbox (``xml_nfse_path``), na
mesma pasta da captura (Doc D §7.6). Documento no Dropbox, dado no banco.

Idempotente: cria o que falta, acrescenta coluna que falta, não mexe no que
existe. Para rodar pelo Colab:

    python migrations/nfse_emissao_01_schema.py            # dry-run: só lista
    python migrations/nfse_emissao_01_schema.py --apply    # cria
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

DDL_CONTROLE = """
CREATE TABLE IF NOT EXISTS nfse_dps_controle (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id     INT NOT NULL COMMENT 'clientes.id (Qualicontax = 2)',
  serie          VARCHAR(5) NOT NULL COMMENT 'infDPS/serie: ate 5 digitos',
  ambiente       TINYINT NOT NULL DEFAULT 2
    COMMENT 'tpAmb: 1 producao, 2 producao restrita. Serie de teste e OUTRA serie.',
  ultimo_numero  BIGINT NOT NULL DEFAULT 0
    COMMENT 'ultimo nDPS entregue. So anda por proximo_numero_dps() (FOR UPDATE).',
  ativo          TINYINT(1) NOT NULL DEFAULT 1,
  observacao     VARCHAR(255) NULL,
  criado_em      DATETIME DEFAULT CURRENT_TIMESTAMP,
  atualizado_em  DATETIME NULL,
  UNIQUE KEY uk_empresa_serie (empresa_id, serie),
  INDEX idx_ativo (ativo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

DDL_EMITIDAS = """
CREATE TABLE IF NOT EXISTS nfse_emitidas (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  empresa_id     INT NOT NULL COMMENT 'clientes.id — quem emite',
  cnpj_emitente  VARCHAR(14) NOT NULL,
  ambiente       TINYINT NOT NULL COMMENT 'tpAmb copiado do controle na reserva',

  -- identificacao da DPS (o que o sistema manda)
  serie          VARCHAR(5) NOT NULL,
  numero_dps     BIGINT NOT NULL COMMENT 'nDPS, vem de nfse_dps_controle',
  dps_id         VARCHAR(45) NULL
    COMMENT 'infDPS/@Id = DPS + cLocEmi + tpInsc + inscricao(14) + serie(5) + nDPS(15)',
  tp_emit        TINYINT NOT NULL DEFAULT 1 COMMENT '1 prestador, 2 tomador, 3 intermediario',
  dh_emissao     DATETIME NULL,
  competencia    DATE NULL COMMENT 'dCompet — usar na apuracao, nao dh_emissao',
  loc_emissao    VARCHAR(7) NULL COMMENT 'cLocEmi (IBGE)',
  loc_prestacao  VARCHAR(7) NULL COMMENT 'serv/locPrest/cLocPrestacao (IBGE)',

  -- situacao: o que aconteceu com esta DPS
  situacao       VARCHAR(20) NOT NULL DEFAULT 'rascunho'
    COMMENT 'rascunho|assinada|enviada|autorizada|rejeitada|cancelada|substituida|erro',
  origem         VARCHAR(20) NOT NULL DEFAULT 'manual' COMMENT 'manual|contrato|api',
  fin_titulo_id  INT NULL COMMENT 'fin_titulos.id quando a nota nasce do honorario',

  -- o que o governo devolveu (NFS-e)
  chave_acesso   VARCHAR(50) NULL,
  numero_nfse    VARCHAR(30) NULL COMMENT 'infNFSe/nNFSe',
  data_processamento DATETIME NULL COMMENT 'infNFSe/dhProc',
  cstat          SMALLINT NULL COMMENT 'geracao (100/101/102/103/107). NAO indica cancelamento.',
  xmotivo        VARCHAR(500) NULL,

  -- prestador: regime declarado
  op_simp_nac    TINYINT NULL COMMENT 'prest/regTrib/opSimpNac (3 = ME/EPP)',
  reg_ap_trib_sn TINYINT NULL COMMENT 'prest/regTrib/regApTribSN',
  reg_esp_trib   TINYINT NULL COMMENT 'prest/regTrib/regEspTrib',

  -- tomador
  tomador_doc    VARCHAR(20) NULL,
  tomador_nome   VARCHAR(255) NULL,
  tomador_municipio VARCHAR(7) NULL,
  tomador_email  VARCHAR(255) NULL,

  -- servico
  cod_trib_nac   VARCHAR(6) NULL COMMENT 'serv/cServ/cTribNac',
  cod_trib_mun   VARCHAR(3) NULL COMMENT 'serv/cServ/cTribMun',
  cod_nbs        VARCHAR(9) NULL,
  descricao      TEXT NULL COMMENT 'serv/cServ/xDescServ',

  -- valores declarados
  valor_servico  DECIMAL(15,2) NOT NULL DEFAULT 0 COMMENT 'valores/vServPrest/vServ',
  valor_desc_incond DECIMAL(15,2) NULL,
  valor_desc_cond   DECIMAL(15,2) NULL,
  valor_deducoes DECIMAL(15,2) NULL,
  iss_retido     TINYINT(1) NOT NULL DEFAULT 0 COMMENT 'tribMun/tpRetISSQN <> 1',
  aliquota_iss   DECIMAL(7,4) NULL,
  -- valores calculados pela autoridade (vem na NFS-e)
  base_calculo   DECIMAL(15,2) NULL,
  valor_iss      DECIMAL(15,2) NULL,
  total_retencoes DECIMAL(15,2) NULL,
  valor_liquido  DECIMAL(15,2) NULL,

  -- IBS/CBS declarado (infDPS/IBSCBS) — obrigatorios no leiaute, Patch 7
  ibscbs_cst        VARCHAR(3)  NOT NULL DEFAULT '' COMMENT 'CST',
  ibscbs_cclasstrib VARCHAR(10) NOT NULL DEFAULT '' COMMENT 'cClassTrib',
  ibscbs_fin_nfse   TINYINT     NOT NULL DEFAULT 0 COMMENT 'finNFSe',
  ibscbs_cind_op    VARCHAR(10) NOT NULL DEFAULT '' COMMENT 'cIndOp',
  ibscbs_ind_dest   TINYINT     NOT NULL DEFAULT 0 COMMENT 'indDest',
  -- IBS/CBS calculado (infNFSe/IBSCBS) — tres eixos, nunca somados
  ibscbs_bc         DECIMAL(15,2) NULL,
  ibs_uf_aliq_efet  DECIMAL(7,4)  NULL,
  ibs_uf_valor      DECIMAL(15,2) NULL,
  ibs_mun_aliq_efet DECIMAL(7,4)  NULL,
  ibs_mun_valor     DECIMAL(15,2) NULL,
  ibs_total         DECIMAL(15,2) NULL,
  cbs_aliq_efet     DECIMAL(7,4)  NULL,
  cbs_valor         DECIMAL(15,2) NULL,
  valor_total_nf    DECIMAL(15,2) NULL,

  -- substituicao e cancelamento
  substitui_chave  VARCHAR(50) NULL
    COMMENT 'subst/chSubstda: qual NFS-e ESTA substituiu (nota nova aponta para a velha)',
  chave_substituta VARCHAR(50) NULL
    COMMENT 'preenchido na nota VELHA quando a nova e autorizada (sentido oposto)',
  cancelada_em     DATETIME NULL,
  cancelamento_motivo VARCHAR(255) NULL,
  cancelamento_protocolo VARCHAR(60) NULL,

  -- documentos e resposta bruta
  dps_json       JSON NULL COMMENT 'tudo que a tela/contrato declarou — fonte do montador',
  xml_dps        MEDIUMTEXT NULL COMMENT 'DPS assinada; e ela que se reenvia',
  xml_nfse_path  VARCHAR(500) NULL COMMENT 'NFS-e devolvida, no Dropbox (Doc D 7.6)',
  raw_resposta   JSON NULL COMMENT 'ultima resposta do ADN, sempre gravada',

  -- rastro
  tentativas     INT NOT NULL DEFAULT 0,
  erro_ultimo    TEXT NULL,
  criado_por     INT NULL COMMENT 'usuarios.id',
  criado_em      DATETIME DEFAULT CURRENT_TIMESTAMP,
  enviado_em     DATETIME NULL,
  autorizada_em  DATETIME NULL,
  atualizado_em  DATETIME NULL,

  UNIQUE KEY uk_dps (empresa_id, serie, numero_dps),
  UNIQUE KEY uk_chave (chave_acesso),
  INDEX idx_empresa_comp (empresa_id, competencia),
  INDEX idx_empresa_situacao (empresa_id, situacao, criado_em),
  INDEX idx_tomador (empresa_id, tomador_doc),
  INDEX idx_titulo (fin_titulo_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

TABELAS = (('nfse_dps_controle', DDL_CONTROLE), ('nfse_emitidas', DDL_EMITIDAS))

# Colunas acrescentadas DEPOIS da criação entram aqui, nunca no CREATE:
# (tabela, coluna, ALTER). Vazio hoje; é o lugar dos próximos patches.
ALTERS = []


def tabela_existe(nome):
    r = execute_query(
        "SELECT 1 FROM information_schema.TABLES "
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (nome,), fetch=True, fetch_one=True)
    return bool(r)


def coluna_existe(tabela, coluna):
    r = execute_query(
        "SELECT 1 FROM information_schema.COLUMNS "
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (tabela, coluna), fetch=True, fetch_one=True)
    return bool(r)


def _contar_colunas(tabela):
    r = execute_query(
        "SELECT COUNT(*) n FROM information_schema.COLUMNS "
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (tabela,), fetch=True, fetch_one=True)
    return (r or {}).get('n', 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem ele só lista')
    args = ap.parse_args()

    pend_tab = [(t, ddl) for t, ddl in TABELAS if not tabela_existe(t)]
    pend_cols = [(t, c, ddl) for t, c, ddl in ALTERS
                 if tabela_existe(t) and not coluna_existe(t, c)]
    for t, _ in TABELAS:
        if tabela_existe(t):
            print(f'{t}: já existe ({_contar_colunas(t)} colunas)')
        else:
            print(f'{t}: criar')
    for t, c, _ in pend_cols:
        print(f'{t}.{c}: acrescentar')
    if not pend_tab and not pend_cols:
        print('Nada a fazer.')
        return 0

    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0

    for t, ddl in pend_tab:
        if execute_query(ddl) is None:
            print(f'FALHOU: {t} não foi criada.')
            return 1
        assert tabela_existe(t), f'{t} não apareceu'
        print(f'OK: {t} criada ({_contar_colunas(t)} colunas).')
    for t, c, ddl in pend_cols:
        if execute_query(ddl) is None:
            print(f'FALHOU: {t}.{c}')
            return 1
        assert coluna_existe(t, c), f'{t}.{c} não apareceu'
        print(f'OK: {t}.{c}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
