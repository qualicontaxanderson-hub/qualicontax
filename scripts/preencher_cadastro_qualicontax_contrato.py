# -*- coding: utf-8 -*-
"""Dados da QUALICONTAX e de quem assina por ela passam a vir do CADASTRO (Anderson, 06/11/2026: "do lado da Qualicontax
e do cliente os dados têm que estar preenchidos no cadastro de clientes, dos dois").

Completa o que faltava, tirado dos contratos assinados de 2020-2025 (Posto Líder, Infinity, Rede MM):
  - CRC da empresa (2SP025.952/O-7) em cadastros_adicionais_clientes do cliente 2 (tipo REGISTRO, campo CRC);
  - documentos do Albert (PF 25): RG 28.486.559-X SSP/SP (vai no contrato) e CRC 1SP263.139/O-5.
Anderson (PF 1) já tinha o RG 41.123.866 SSP/SP marcado para contrato.

Uso: python scripts/preencher_cadastro_qualicontax_contrato.py --aplicar
"""
import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402
from models.pessoa_dados import Documentos           # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'


def main(aplicar):
    crc = execute_query("SELECT id, valor FROM cadastros_adicionais_clientes WHERE cliente_id = 2 AND campo = 'CRC' AND ativo = 1", fetch=True)
    print('CRC da Qualicontax no cadastro:', crc or 'FALTA (vai gravar 2SP025.952/O-7)')
    docs = Documentos.listar(25)
    print('documentos do Albert (PF 25):', docs or 'NENHUM (vai gravar RG 28.486.559-X SSP/SP e CRC 1SP263.139/O-5)')
    print('documentos do Anderson (PF 1):', Documentos.listar(1))
    if not aplicar:
        print('modo conferência (--aplicar para gravar)')
        return
    if not crc:
        cid = execute_query("INSERT INTO cadastros_adicionais_clientes (cliente_id, tipo, campo, valor, observacoes) VALUES (2, 'REGISTRO', 'CRC', '2SP025.952/O-7', 'registro da empresa no CRC, usado no contrato de prestação')", fetch=False)
        registrar_agente('escrita.cadastro_adicional_criado', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN, tabela='cadastros_adicionais_clientes', registro_id=cid,
                         depois={'cliente_id': 2, 'campo': 'CRC', 'valor': '2SP025.952/O-7', 'fonte': 'contratos assinados 2020-2025'})
        print('  CRC gravado')
    if not docs:
        Documentos.salvar(25, [{'tipo': 'RG', 'numero': '28.486.559-X', 'orgao': 'SSP', 'uf': 'SP', 'contrato': True},
                              {'tipo': 'CRC', 'numero': '1SP263.139/O-5', 'orgao': 'CRC', 'uf': 'SP', 'contrato': False}], USUARIO_ID)
        registrar_agente('escrita.documentos_pf_preenchidos', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN, tabela='clientes_pf_documentos', registro_id=25,
                         depois={'pessoa': 'ALBERT ANTUNES VIEIRA', 'documentos': 'RG 28.486.559-X SSP/SP (contrato); CRC 1SP263.139/O-5', 'fonte': 'contratos assinados 2020-2025'})
        print('  documentos do Albert gravados')
    print('pronto')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--aplicar', action='store_true')
    main(ap.parse_args().aplicar)
