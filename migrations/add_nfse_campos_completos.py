# -*- coding: utf-8 -*-
"""Amplia nfse_capturadas: tributos, contatos, endereços e nomes de município.

POR QUE ESTA MIGRAÇÃO EXISTE
---------------------------
A primeira versão da captura leu o que a especificação destacava. Em 16/08/2026,
com 1.008 notas reais no banco, o inventário de UM documento mostrou o que ficava
na mesa. Dois casos doíam de verdade:

1. ``total_retencoes`` guardava 9,05 — e o XML trazia a composição ao lado:
   PIS 1,60 e COFINS 7,45. Total sem composição não se escritura.
2. ``municipio_ibge`` guardava 3550308 e o XML trazia "São Paulo" no elemento
   vizinho. A tela mostrava o código porque nós não gravávamos o nome, não
   porque o nome não existisse.

A REGRA QUE SEPARA O QUE ENTRA DO QUE FICA FORA
-----------------------------------------------
Entra o que descreve o DOCUMENTO ou as PARTES. Fica fora o que descreve o
SOFTWARE que emitiu: ``verAplic`` ("EmissorWeb_1.6.0.0"), ``tpEmis``,
``procEmi`` e ``ambGer`` não respondem nenhuma pergunta de escrita fiscal, e
coluna que ninguém consulta é coluna que envelhece errado. ``nDFSe`` entra: é o
número do documento no sistema da prefeitura, e serve para abrir chamado lá.

ORDEM DE EXECUÇÃO
-----------------
Esta migração é INÓCUA sozinha — só cria colunas nulas. O que preenche é a
RECAPTURA, porque o XML não fica guardado: a captura lê e descarta. Portanto:
migrar, ajustar o parser, zerar os cursores, recapturar.

    python migrations/add_nfse_campos_completos.py            # dry-run
    python migrations/add_nfse_campos_completos.py --apply

Idempotente: confere INFORMATION_SCHEMA antes de cada ALTER e pula o que existe.
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELA = 'nfse_capturadas'

# (coluna, definição, de onde vem no XML)
COLUNAS = [
    # ---- Tributos federais: a composição da retenção ----
    ('pis_valor', 'DECIMAL(15,2) NULL',
     'DPS/infDPS/valores/trib/tribFed/piscofins/vPis'),
    ('cofins_valor', 'DECIMAL(15,2) NULL',
     'DPS/infDPS/valores/trib/tribFed/piscofins/vCofins'),
    ('piscofins_cst', 'VARCHAR(2) NULL',
     'DPS/infDPS/valores/trib/tribFed/piscofins/CST'),

    # ---- Totais por ente. Somar destrói a apuração, então ficam separados ----
    ('trib_total_federal', 'DECIMAL(15,2) NULL', 'totTrib/vTotTrib/vTotTribFed'),
    ('trib_total_estadual', 'DECIMAL(15,2) NULL', 'totTrib/vTotTrib/vTotTribEst'),
    ('trib_total_municipal', 'DECIMAL(15,2) NULL', 'totTrib/vTotTrib/vTotTribMun'),

    # ---- ISS declarado. iss_retido (0/1) já existe; aqui vem o COMO ----
    ('iss_tributacao', 'TINYINT NULL COMMENT \'tribISSQN: 1 operacao tributavel\'',
     'valores/trib/tribMun/tribISSQN'),
    ('iss_tipo_retencao', 'TINYINT NULL COMMENT \'tpRetISSQN\'',
     'valores/trib/tribMun/tpRetISSQN'),
    ('iss_aliquota_declarada', 'DECIMAL(7,4) NULL',
     'valores/trib/tribMun/pAliq (declarada; aliquota_iss e a calculada)'),
    ('regime_especial', 'TINYINT NULL COMMENT \'regEspTrib\'',
     'prest/regTrib/regEspTrib'),

    # ---- Municípios PELO NOME ----
    ('municipio_incid_nome', 'VARCHAR(120) NULL', 'infNFSe/xLocIncid'),
    ('municipio_emissao_nome', 'VARCHAR(120) NULL', 'infNFSe/xLocEmi'),
    ('municipio_prestacao_ibge', 'VARCHAR(7) NULL', 'serv/locPrest/cLocPrestacao'),
    ('municipio_prestacao_nome', 'VARCHAR(120) NULL', 'infNFSe/xLocPrestacao'),

    # ---- Serviço ----
    ('servico_descricao_nacional', 'VARCHAR(500) NULL', 'infNFSe/xTribNac'),
    ('codigo_interno_contrib', 'VARCHAR(60) NULL', 'serv/cServ/cIntContrib'),

    # ---- Prestador: inscrição, contato e endereço ----
    ('prestador_im', 'VARCHAR(30) NULL', 'infNFSe/emit/IM'),
    ('prestador_email', 'VARCHAR(120) NULL', 'infNFSe/emit/email'),
    ('prestador_fone', 'VARCHAR(30) NULL', 'infNFSe/emit/fone'),
    ('prestador_logradouro', 'VARCHAR(150) NULL', 'emit/enderNac/xLgr'),
    ('prestador_numero', 'VARCHAR(20) NULL', 'emit/enderNac/nro'),
    ('prestador_complemento', 'VARCHAR(80) NULL', 'emit/enderNac/xCpl'),
    ('prestador_bairro', 'VARCHAR(100) NULL', 'emit/enderNac/xBairro'),
    ('prestador_municipio_ibge', 'VARCHAR(7) NULL', 'emit/enderNac/cMun'),
    ('prestador_uf', 'VARCHAR(2) NULL', 'emit/enderNac/UF'),
    ('prestador_cep', 'VARCHAR(10) NULL', 'emit/enderNac/CEP'),

    # ---- Tomador: mesma coisa. O endereço dele mora em toma/end, com o
    #      município dentro de endNac — caminho DIFERENTE do prestador ----
    ('tomador_im', 'VARCHAR(30) NULL', 'toma/IM'),
    ('tomador_email', 'VARCHAR(120) NULL', 'toma/email'),
    ('tomador_fone', 'VARCHAR(30) NULL', 'toma/fone'),
    ('tomador_logradouro', 'VARCHAR(150) NULL', 'toma/end/xLgr'),
    ('tomador_numero', 'VARCHAR(20) NULL', 'toma/end/nro'),
    ('tomador_complemento', 'VARCHAR(80) NULL', 'toma/end/xCpl'),
    ('tomador_bairro', 'VARCHAR(100) NULL', 'toma/end/xBairro'),
    ('tomador_municipio_ibge', 'VARCHAR(7) NULL', 'toma/end/endNac/cMun'),
    # SEM tomador_uf, e não por esquecimento. Ele existiu por meia hora em
    # 17/08/2026 e veio VAZIO em 1.008 de 1.008 notas: o grupo de endereço
    # nacional do tomador é só cMun + CEP, enquanto o do prestador
    # (emit/enderNac) tem UF. Não é falta de dado — o código IBGE já determina o
    # estado nos dois primeiros dígitos, então a UF do tomador se deriva na
    # leitura. Coluna que nunca preenche é pior que coluna que não existe.
    ('tomador_cep', 'VARCHAR(10) NULL', 'toma/end/endNac/CEP'),

    # ---- Identificação e natureza ----
    ('numero_dps', 'VARCHAR(20) NULL COMMENT \'nDPS, diferente de nNFSe\'',
     'DPS/infDPS/nDPS'),
    ('numero_dfse', 'VARCHAR(30) NULL COMMENT \'numero na prefeitura\'',
     'infNFSe/nDFSe'),
    ('consumidor_final', 'TINYINT NULL COMMENT \'indFinal\'', 'IBSCBS/indFinal'),
    ('tipo_emitente', 'TINYINT NULL COMMENT \'tpEmit\'', 'DPS/infDPS/tpEmit'),

    # ---- Metadados de emissão. O Anderson pediu em 17/08/2026: "até os dados do
    #      sistema". Entram, e com uma correção do que eu havia dito antes:
    #      ``verAplic`` existe em DOIS níveis do XSD: no infNFSe (lado da
    #      prefeitura) e na DPS (lado do contribuinte). Medido em quatro
    #      documentos reais: os dois trazem VALOR IDÊNTICO. A distinção é
    #      possível, não observada — ficam em colunas separadas porque o dia em
    #      que divergirem é o dia em que a informação vale, e é o que explicaria
    #      um município passar a mandar leiaute diferente da noite para o dia.
    ('aplicativo_prefeitura', 'VARCHAR(40) NULL', 'infNFSe/verAplic'),
    ('aplicativo_emitente', 'VARCHAR(40) NULL', 'DPS/infDPS/verAplic'),
    ('tipo_emissao', 'TINYINT NULL COMMENT \'tpEmis\'', 'infNFSe/tpEmis'),
    ('processo_emissao', 'TINYINT NULL COMMENT \'procEmi\'', 'infNFSe/procEmi'),
    ('ambiente_gerador', 'TINYINT NULL COMMENT \'ambGer\'', 'infNFSe/ambGer'),
    ('ambiente_dps', 'TINYINT NULL COMMENT \'tpAmb da declaracao\'',
     'DPS/infDPS/tpAmb'),
]


def existentes():
    r = execute_query(
        'SELECT COLUMN_NAME c FROM INFORMATION_SCHEMA.COLUMNS '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', (TABELA,),
        fetch=True) or []
    return {x['c'] for x in r}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true',
                    help='executa; sem isto é só dry-run')
    args = ap.parse_args()

    tem = existentes()
    if not tem:
        print(f'ERRO: tabela {TABELA} não encontrada. Rodar antes o add_nfse_adn.py.')
        return 1

    print(f'{TABELA}: {len(tem)} coluna(s) hoje')
    print()

    faltam = [(c, d, o) for c, d, o in COLUNAS if c not in tem]
    ja = [c for c, _, _ in COLUNAS if c in tem]

    if ja:
        print(f'JÁ EXISTEM, serão puladas ({len(ja)}): {", ".join(ja)}')
        print()
    if not faltam:
        print('Nada a fazer — todas as colunas já existem.')
        return 0

    print(f'A CRIAR ({len(faltam)}):')
    for c, d, origem in faltam:
        print(f'   {c:<28} {d:<22} <- {origem}')
    print()

    if not args.apply:
        print('DRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        print()
        print('ROLLBACK, se precisar:')
        print(f'   ALTER TABLE {TABELA}')
        print('     ' + ',\n     '.join(f'DROP COLUMN {c}' for c, _, _ in faltam) + ';')
        return 0

    # Um ALTER só: MySQL reconstrói a tabela por ALTER, e 40 ALTERs seriam 40
    # reconstruções de uma tabela com mil linhas e crescendo.
    sql = f'ALTER TABLE {TABELA}\n  ' + ',\n  '.join(
        f'ADD COLUMN {c} {d}' for c, d, _ in faltam)
    print('EXECUTANDO:')
    print(sql)
    print()
    r = execute_query(sql, fetch=False)
    if r is None:
        print('FALHOU. Nada foi alterado (ALTER é atômico por tabela).')
        return 1

    agora = existentes()
    criadas = [c for c, _, _ in faltam if c in agora]
    print(f'OK: {len(criadas)} de {len(faltam)} criada(s). '
          f'{TABELA} agora tem {len(agora)} colunas.')
    if len(criadas) != len(faltam):
        print('ATENÇÃO — não criadas: '
              + ', '.join(c for c, _, _ in faltam if c not in agora))
        return 1

    print()
    print('AS COLUNAS NASCEM VAZIAS. O que preenche é a recaptura, porque o XML')
    print('não fica guardado. Próximo passo: ajustar o parser, zerar os cursores')
    print('em dfe_nsu_nfse e rodar o backfill de novo.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
