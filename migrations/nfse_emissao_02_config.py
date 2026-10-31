# -*- coding: utf-8 -*-
"""Emissor de NFS-e — configuração fiscal por empresa (31/10/2026).

A regra combinada com o Anderson: **o cadastro da empresa guarda o padrão,
cada nota pode mudar.** Nada de alíquota, código de serviço ou série dentro do
código. Esta tabela é o "padrão": a tela de emissão e a rotina de honorários
leem daqui e preenchem a nota, e o usuário altera na nota o que precisar.

Uma linha por empresa (``empresa_id`` = ``clientes.id``). Os campos cobrem os
DOIS emissores — nacional (DPS) e prefeitura de São Paulo (RPS) — porque a
declaração é a mesma e só o empacotamento muda:

* ``emissor``            por onde a empresa emite hoje: 'nacional' ou 'sp'.
                         Trocar aqui troca o caminho sem mexer em nota nenhuma.
* séries                 ``serie_nacional`` / ``serie_sp`` apontam para
                         ``nfse_dps_controle`` (a numeração é de lá).
* serviço                código municipal de SP (``cod_servico_sp``), código
                         nacional (``cod_trib_nac``), municipal do leiaute
                         nacional (``cod_trib_mun``) e NBS.
* regime                 opSimpNac / regApTribSN / regEspTrib (prestador).
* ISS                    alíquota (fração) e retenção padrão.
* IBS/CBS                CST, cClassTrib, cIndOp, indDest, indFinal — para a
                         prefeitura só valem fora do Simples (erro 641).
* ``leiaute_sp``         1 = sem IBS/CBS (Simples), 2 = reforma. Derivado do
                         regime, mas gravado para a tela mostrar e permitir
                         correção se a prefeitura mudar a regra.

A semente grava a Qualicontax (cliente 2) com o que a consulta à prefeitura
devolveu em 31/10/2026 (NFS-e 1813): IM 39666026, serviço 3476, tributação T,
Simples (opSimpNac 3, regApTribSN 1), alíquota 0, e os valores de IBS/CBS que
os outros emitentes do Simples usam no nacional (000 / 000001 / 100301 / 0).
Séries: 2 no nacional e QC na prefeitura (a RP1 é do Conexa).

Idempotente. Pelo Colab ou daqui:

    python migrations/nfse_emissao_02_config.py            # dry-run: só lista
    python migrations/nfse_emissao_02_config.py --apply    # cria e semeia
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

QUALICONTAX_ID = 2

DDL = """
CREATE TABLE IF NOT EXISTS nfse_emissao_config (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  empresa_id      INT NOT NULL COMMENT 'clientes.id',
  emissor         VARCHAR(10) NOT NULL DEFAULT 'nacional' COMMENT 'nacional | sp (prefeitura de Sao Paulo)',
  serie_nacional  VARCHAR(5) NULL COMMENT 'serie em nfse_dps_controle para a DPS',
  serie_sp        VARCHAR(5) NULL COMMENT 'serie em nfse_dps_controle para o RPS',
  leiaute_sp      TINYINT NOT NULL DEFAULT 1 COMMENT '1 = sem IBS/CBS (Simples, erro 641); 2 = reforma',

  inscricao_municipal VARCHAR(15) NULL,
  municipio_ibge  VARCHAR(7) NULL COMMENT 'cLocEmi / local da prestacao padrao',
  tributacao_sp   CHAR(1) NOT NULL DEFAULT 'T' COMMENT 'TributacaoRPS: T = tributado em SP',
  cod_servico_sp  VARCHAR(5) NULL COMMENT 'codigo de servico da prefeitura de SP (ex. 3476)',
  cod_trib_nac    VARCHAR(6) NULL COMMENT 'cTribNac (ex. 171901)',
  cod_trib_mun    VARCHAR(3) NULL COMMENT 'cTribMun',
  cod_nbs         VARCHAR(9) NULL,

  op_simp_nac     TINYINT NULL COMMENT '1 nao optante, 2 MEI, 3 ME/EPP',
  reg_ap_trib_sn  TINYINT NULL,
  reg_esp_trib    TINYINT NULL DEFAULT 0,
  aliquota_iss    DECIMAL(7,4) NOT NULL DEFAULT 0 COMMENT 'fracao: 5% = 0.0500',
  iss_retido      TINYINT(1) NOT NULL DEFAULT 0,

  ibscbs_cst        VARCHAR(3)  NULL,
  ibscbs_cclasstrib VARCHAR(10) NULL,
  ibscbs_cind_op    VARCHAR(10) NULL,
  ibscbs_ind_dest   TINYINT NULL DEFAULT 0,
  ibscbs_ind_final  TINYINT NULL DEFAULT 0,

  email_remetente VARCHAR(255) NULL,
  descricao_padrao TEXT NULL COMMENT 'texto da discriminacao; aceita {mes} e {ano}',

  ativo           TINYINT(1) NOT NULL DEFAULT 1,
  criado_em       DATETIME DEFAULT CURRENT_TIMESTAMP,
  atualizado_em   DATETIME NULL,
  alterado_por    INT NULL COMMENT 'usuarios.id',
  UNIQUE KEY uk_empresa (empresa_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

SEMENTE_QUALICONTAX = {
    'empresa_id': QUALICONTAX_ID,
    'emissor': 'sp',
    'serie_nacional': '2',
    'serie_sp': 'QC',
    'leiaute_sp': 1,
    'inscricao_municipal': '39666026',
    'municipio_ibge': '3550308',
    'tributacao_sp': 'T',
    'cod_servico_sp': '3476',
    'cod_trib_nac': '171901',
    'cod_trib_mun': '001',
    'cod_nbs': '999999999',
    'op_simp_nac': 3,
    'reg_ap_trib_sn': 1,
    'reg_esp_trib': 0,
    'aliquota_iss': 0,
    'iss_retido': 0,
    'ibscbs_cst': '000',
    'ibscbs_cclasstrib': '000001',
    'ibscbs_cind_op': '100301',
    'ibscbs_ind_dest': 0,
    'ibscbs_ind_final': 0,
    'email_remetente': 'financeiro@qualicontax.com.br',
    'descricao_padrao': 'Servicos prestados em {mes} de {ano}',
}


def tabela_existe(nome):
    return bool(execute_query(
        "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (nome,), fetch=True, fetch_one=True))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='executa; sem ele só lista')
    args = ap.parse_args()

    existe = tabela_existe('nfse_emissao_config')
    print('nfse_emissao_config:', 'já existe' if existe else 'criar')
    semeada = existe and execute_query(
        'SELECT id FROM nfse_emissao_config WHERE empresa_id = %s', (QUALICONTAX_ID,),
        fetch=True, fetch_one=True)
    print('semente da Qualicontax (cliente 2):', 'já existe' if semeada else 'gravar')
    for k, v in SEMENTE_QUALICONTAX.items():
        if k != 'empresa_id':
            print(f'   {k} = {v!r}')

    if not args.apply:
        print('[dry-run] nada executado. Rode com --apply.')
        return 0
    if not existe:
        if execute_query(DDL) is None:
            print('FALHOU: tabela não criada.')
            return 1
        assert tabela_existe('nfse_emissao_config')
        print('OK: nfse_emissao_config criada.')
    if not semeada:
        cols = list(SEMENTE_QUALICONTAX)
        r = execute_query(
            f"INSERT INTO nfse_emissao_config ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
            tuple(SEMENTE_QUALICONTAX[c] for c in cols))
        if r is None:
            print('FALHOU: semente não gravada.')
            return 1
        print('OK: Qualicontax semeada (id %s).' % r)
    return 0


if __name__ == '__main__':
    sys.exit(main())
