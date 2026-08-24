# -*- coding: utf-8 -*-
"""A memorização do extrato vira REGRA (degrau 2, 24/08/2026).

O que muda, e por quê — tudo saiu do desenho fechado com o Anderson depois
de três rodadas de protótipo com os 511 lançamentos dele na mão:

1. O padrão deixa de ser UM texto e passa a ser uma LISTA de trechos que
   TODOS precisam aparecer. É o que resolve o número que muda no meio:

       TARIFA COM R LIQUIDACAO-COB000001  262005312  DISTRIBUIDORA ... SAARA
                                          ^^^^^^^^^ muda todo mês

   Com os dois trechos ligados e o número de fora, a regra pega as 4 tarifas
   do ano em vez de 1. ``padrao`` continua para a regra de um trecho só.

2. CONDIÇÕES. A mesma pessoa pode ter duas regras, e o que as separa é a
   conta: Gabriel pelo Bradesco é salário; pelo Sicredi pode ser comissão.
   Entram ``conta``, ``sinal`` (só saídas / só entradas) e ``valor_exato``.
   O sinal não é luxo: "só quem está do outro lado" na tarifa da SAARA pega
   8 lançamentos — as 4 tarifas de R$ 1,15 E os 4 recebimentos de R$
   20.663,87 do mesmo cliente. Sem "só saídas", receita cairia dentro de
   despesa bancária.

3. ESCOPO. Um grupo de 6 empresas usa o mesmo banco e os mesmos
   fornecedores. A regra vale para UMA empresa, para uma LISTA delas ou para
   o GRUPO inteiro. Guardar o grupo, e não a lista de hoje, é o que faz a
   sétima empresa herdar a regra ao entrar no grupo.

4. APLICAR. ``direto`` classifica sozinha; ``aprovar`` deixa o lançamento
   preenchido e marcado A CONFERIR — daí a coluna ``conferir`` no
   lançamento. É a diferença entre o salário da Talita (nunca é outra coisa)
   e o Gabriel no Sicredi (pode ser comissão ou salário).

Nada é apagado e nada é reescrito: a tabela tem ZERO linhas hoje, e as
colunas novas nascem com o padrão que reproduz o comportamento atual
(escopo='empresa', aplicar='direto', sem condição).

    python migrations/regras_extrato.py            # dry-run: só mostra
    python migrations/regras_extrato.py --apply    # executa
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELA = 'fin_extrato_memorizacoes'

# (coluna, DDL). Cada uma é conferida antes: rodar duas vezes não quebra.
COLUNAS = [
    ('termos',
     f"ALTER TABLE {TABELA} ADD COLUMN termos JSON NULL AFTER padrao"),
    ('conta',
     f"ALTER TABLE {TABELA} ADD COLUMN conta VARCHAR(40) NULL AFTER termos"),
    ('sinal',
     f"ALTER TABLE {TABELA} ADD COLUMN sinal CHAR(1) NULL AFTER conta"),
    ('valor_exato',
     f"ALTER TABLE {TABELA} ADD COLUMN valor_exato DECIMAL(15,2) NULL AFTER sinal"),
    ('escopo',
     f"ALTER TABLE {TABELA} ADD COLUMN escopo VARCHAR(8) NOT NULL DEFAULT 'empresa' "
     f"AFTER valor_exato"),
    ('grupo_id',
     f"ALTER TABLE {TABELA} ADD COLUMN grupo_id INT NULL AFTER escopo"),
    ('aplicar',
     f"ALTER TABLE {TABELA} ADD COLUMN aplicar VARCHAR(8) NOT NULL DEFAULT 'direto' "
     f"AFTER grupo_id"),
]

# O lançamento ganha o terceiro estado: nem sem categoria, nem resolvido.
COLUNAS_LANC = [
    ('conferir',
     "ALTER TABLE extrato_lancamentos ADD COLUMN conferir TINYINT(1) NOT NULL "
     "DEFAULT 0 AFTER memorizacao_id, ADD INDEX idx_conferir (conferir)"),
]

# Escopo 'lista': as empresas escolhidas. Tabela filha em vez de campo com
# vírgulas — dá para perguntar "quais regras valem para a empresa X" por JOIN,
# e apagar a regra leva as linhas junto.
TABELA_EMPRESAS = """
CREATE TABLE IF NOT EXISTS fin_regra_empresas (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  regra_id    INT NOT NULL,
  empresa_id  INT NOT NULL,
  UNIQUE KEY uk_regra_empresa (regra_id, empresa_id),
  INDEX idx_empresa (empresa_id),
  CONSTRAINT fk_regra_emp_regra FOREIGN KEY (regra_id)
      REFERENCES fin_extrato_memorizacoes (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def colunas_de(tabela):
    rows = execute_query(f'SHOW COLUMNS FROM {tabela}', fetch=True) or []
    return {r['Field'] for r in rows}


def tabela_existe(nome):
    r = execute_query(
        'SELECT COUNT(*) n FROM information_schema.TABLES '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s',
        (nome,), fetch=True, fetch_one=True)
    return bool(r and r['n'])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='executa de verdade (sem isto, só mostra)')
    args = ap.parse_args()

    faltando = []

    tem = colunas_de(TABELA)
    for nome, ddl in COLUNAS:
        if nome in tem:
            print(f'  ja existe   {TABELA}.{nome}')
        else:
            faltando.append((f'{TABELA}.{nome}', ddl))

    tem_l = colunas_de('extrato_lancamentos')
    for nome, ddl in COLUNAS_LANC:
        if nome in tem_l:
            print(f'  ja existe   extrato_lancamentos.{nome}')
        else:
            faltando.append((f'extrato_lancamentos.{nome}', ddl))

    if tabela_existe('fin_regra_empresas'):
        print('  ja existe   tabela fin_regra_empresas')
    else:
        faltando.append(('tabela fin_regra_empresas', TABELA_EMPRESAS.strip()))

    if not faltando:
        print('\nNada a fazer: o banco ja esta como o codigo espera.')
        return

    print('\n' + '=' * 74)
    print('O QUE FALTA' + ('  (executando)' if args.apply else '  (dry-run: nada sera feito)'))
    print('=' * 74)
    for nome, ddl in faltando:
        print(f'\n-- {nome}')
        print(ddl if ddl.endswith(';') else ddl + ';')

    if not args.apply:
        print('\n' + '=' * 74)
        print('Nada foi executado. Rode de novo com --apply para valer.')
        return

    print('\n' + '=' * 74)
    for nome, ddl in faltando:
        ok = execute_query(ddl)
        print(f'  {"OK  " if ok else "FALHOU"}  {nome}')

    print('\nConferindo o resultado:')
    depois = colunas_de(TABELA)
    for nome, _ in COLUNAS:
        print(f'  {"ok" if nome in depois else "SUMIU"}  {TABELA}.{nome}')
    depois_l = colunas_de('extrato_lancamentos')
    for nome, _ in COLUNAS_LANC:
        print(f'  {"ok" if nome in depois_l else "SUMIU"}  extrato_lancamentos.{nome}')
    print(f'  {"ok" if tabela_existe("fin_regra_empresas") else "SUMIU"}  fin_regra_empresas')


if __name__ == '__main__':
    main()
