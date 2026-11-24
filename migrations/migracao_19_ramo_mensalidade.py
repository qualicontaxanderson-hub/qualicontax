# -*- coding: utf-8 -*-
"""Mensalidade sugerida por ramo de atividade (24/11/2026).

Anderson: "quero colocar sugestões de mensalidades para as atividades, e será aqui o parâmetro, que vamos incluir nos
honorários... Posto de Combustíveis sugestão 2400,00, Loja de Conveniência 1200,00, Prestadora de Serviços Geral 600,00,
Terceirizadora 1000,00... uma empresa tem Posto, Loja de Conveniência e Transportadora: ao chegar no campo de honorários
o usuário escolhe trazer as 3, ou só duas, ou só uma". ``ramos_atividade.mensalidade_sugerida``: NULL = sem sugestão.
Edita-se em Financeiro › SERVIÇOS › Mensalidade por ramo; o formulário do contrato mostra os ramos da empresa com o valor
e soma os escolhidos.

    python migrations/migracao_19_ramo_mensalidade.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402


def _tem_coluna(t, c):
    r = execute_query("SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                      (t, c), fetch=True, fetch_one=True) or {}
    return bool(r.get('n'))


def aplicar(so_ver=False):
    if _tem_coluna('ramos_atividade', 'mensalidade_sugerida'):
        print('  ramos_atividade.mensalidade_sugerida ja existe'); return
    print('  ramos_atividade.mensalidade_sugerida %s' % ('criaria' if so_ver else 'criando'))
    if not so_ver:
        execute_query("ALTER TABLE ramos_atividade ADD COLUMN mensalidade_sugerida DECIMAL(10,2) NULL "
                      "COMMENT 'sugestao de honorario mensal para empresas deste ramo (contrato de prestacao); NULL = sem sugestao'", fetch=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    aplicar(so_ver=not a.apply)


if __name__ == '__main__':
    main()
