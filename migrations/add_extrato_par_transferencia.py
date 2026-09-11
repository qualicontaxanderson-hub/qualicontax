# -*- coding: utf-8 -*-
"""Amarra as duas pontas de uma transferência em extrato_lancamentos.

POR QUE ESTA MIGRAÇÃO EXISTE
---------------------------
Hoje NADA liga um lançamento a outro. Saiu R$ 5.000 do EFI e entrou R$ 5.000 no
Sicredi no mesmo dia: são duas linhas que não se conhecem, cada uma pedindo
classificação. Medido em 10/09/2026 sobre o extrato real da Qualicontax: **216
pares, R$ 1.913.002,94**, sendo **128 pares EFI -> Sicredi (R$ 1,44 milhão)**.
Classificar as duas pontas como receita/despesa conta o mesmo dinheiro duas
vezes; o Anderson viu o efeito nos cartões da tela, onde ``totais()`` soma toda
linha sem olhar categoria.

A REGRA, decidida por ele em 10 e 11/09/2026
--------------------------------------------
O par NUNCA é aplicado sozinho: ele PARA o lançamento e espera aprovação.
"Não somos adivinhos, temos que ter certeza."

* MESMA empresa (as 5 contas da Qualicontax): dinheiro de bolso a bolso. Casa e
  classifica ``#138 Entre contas do grupo`` (tipo T) nos dois lados, fora do DRE.
* ENTRE empresas do grupo: patrimônios distintos, e as duas pontas NÃO têm o
  mesmo tipo — pró-labore é DESPESA na Qualicontax e RECEITA na PF. UMA decisão
  classifica os dois lados.
* A outra ponta nunca vem: botão libera com MOTIVO obrigatório, **classificando
  no mesmo gesto** (quem libera sabe que a ponta não vem, logo sabe o que era).

POR QUE ``par_estado`` TEM O VALOR 'suspeito'
---------------------------------------------
Esta é a coluna que resolve o caso Brilho. A trava precisa nascer ANTES do par
existir: a Brilho e o Anderson PF não têm conta cadastrada, então o extrato
delas nem é identificado. O débito na Qualicontax que carrega o nome/CNPJ de
outra empresa do grupo nasce 'suspeito' e trava ali — e isso é PROPOSITAL, nas
palavras dele: "tem que travar para eu colocar o extrato, senão não colocarei".

POR QUE QUEM LIBEROU FICA NA LINHA, E NÃO SÓ NO LOG
---------------------------------------------------
Mesmo padrão de ``dfe_certificados.manifesta_por/manifesta_em``: ato com
consequência fica na linha, para a tela mostrar sem garimpar a auditoria. O
``registrar()`` da rota continua obrigatório — um não substitui o outro.

O QUE NÃO ENTRA AQUI
--------------------
* ``fin_titulos.cliente_id`` JÁ EXISTE (e já é parâmetro de quem cria título) —
  está nulo nos 3.216 porque ninguém passa o valor. As receitas por grupo ->
  empresa são PREENCHIMENTO, não estrutura.
* Marca de "categoria intragrupo" para o consolidado: dá para derivar das três
  categorias T que já existem (#138/#139/#140), e o consolidado está adiado.
* Filtro de conta múltiplo, nome de banco padronizado e os blocos do retroativo
  não precisam de DDL nenhum.

ORDEM DE EXECUÇÃO
-----------------
Esta migração é INÓCUA sozinha — cria colunas nulas e dois índices, sem tocar em
uma linha de dado. Quem preenche é o detector de pares, que ainda não existe.

    python migrations/add_extrato_par_transferencia.py            # dry-run
    python migrations/add_extrato_par_transferencia.py --apply

Idempotente: confere INFORMATION_SCHEMA antes de cada ALTER e pula o que existe.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

TABELA = 'extrato_lancamentos'

# (coluna, definição, para que serve)
COLUNAS = [
    ('par_id', 'BIGINT NULL',
     'o id da OUTRA PONTA; simétrico (os dois lados se apontam)'),
    ('par_estado', 'VARCHAR(12) NULL',
     "suspeito | casado | aprovado | liberado (NULL = não é transferência)"),
    ('par_liberado_motivo', 'VARCHAR(255) NULL',
     'por que foi liberado sem a outra ponta — obrigatório no gesto'),
    ('par_liberado_por', 'INT NULL',
     'quem liberou (usuarios.id)'),
    ('par_liberado_em', 'DATETIME NULL',
     'quando liberou'),
]

# (nome, colunas) — idx_par acha a outra ponta; idx_par_estado monta a fila
INDICES = [
    ('idx_par', 'par_id'),
    ('idx_par_estado', 'par_estado'),
]


def colunas_existentes():
    r = execute_query(
        'SELECT COLUMN_NAME c FROM INFORMATION_SCHEMA.COLUMNS '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', (TABELA,),
        fetch=True) or []
    return {x['c'] for x in r}


def indices_existentes():
    r = execute_query(
        'SELECT DISTINCT INDEX_NAME i FROM INFORMATION_SCHEMA.STATISTICS '
        ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', (TABELA,),
        fetch=True) or []
    return {x['i'] for x in r}


def main():
    ap = argparse.ArgumentParser(description='Colunas do par de transferência.')
    ap.add_argument('--apply', action='store_true',
                    help='executa; sem isto é só dry-run')
    args = ap.parse_args()

    tem = colunas_existentes()
    if not tem:
        print(f'ERRO: tabela {TABELA} não encontrada.')
        return 1
    tem_idx = indices_existentes()

    linhas = (execute_query(f'SELECT COUNT(*) n FROM {TABELA}',
                            fetch=True, fetch_one=True) or {}).get('n')
    print(f'{TABELA}: {len(tem)} coluna(s) e {linhas} linha(s) hoje')
    print()

    faltam = [(c, d, o) for c, d, o in COLUNAS if c not in tem]
    ja = [c for c, _, _ in COLUNAS if c in tem]
    faltam_idx = [(n, col) for n, col in INDICES if n not in tem_idx]

    if ja:
        print(f'JÁ EXISTEM, serão puladas ({len(ja)}): {", ".join(ja)}')
        print()
    if not faltam and not faltam_idx:
        print('Nada a fazer — colunas e índices já existem.')
        return 0

    if faltam:
        print(f'A CRIAR — colunas ({len(faltam)}):')
        for c, d, para in faltam:
            print(f'   {c:<22} {d:<16} {para}')
    if faltam_idx:
        print(f'A CRIAR — índices ({len(faltam_idx)}):')
        for n, col in faltam_idx:
            print(f'   {n:<22} ({col})')
    print()
    print('Todas NULAS e aditivas: nenhuma linha de dado é reescrita, nenhum')
    print('default muda, nada fica obrigatório. As colunas nascem vazias.')
    print()

    partes = [f'ADD COLUMN {c} {d}' for c, d, _ in faltam]
    partes += [f'ADD INDEX {n} ({col})' for n, col in faltam_idx]
    sql = f'ALTER TABLE {TABELA}\n  ' + ',\n  '.join(partes)

    if not args.apply:
        print('SQL QUE SERIA EXECUTADO:')
        print(sql)
        print()
        print('ROLLBACK, se precisar:')
        rb = [f'DROP COLUMN {c}' for c, _, _ in faltam]
        rb += [f'DROP INDEX {n}' for n, _ in faltam_idx]
        print(f'   ALTER TABLE {TABELA}')
        print('     ' + ',\n     '.join(rb) + ';')
        print()
        print('DRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        return 0

    # Um ALTER só: o MySQL reconstrói a tabela por ALTER, e sete ALTERs seriam
    # sete reconstruções da mesma tabela.
    print('EXECUTANDO:')
    print(sql)
    print()
    r = execute_query(sql, fetch=False)
    if r is None:
        print('FALHOU. Nada foi alterado (ALTER é atômico por tabela).')
        return 1

    agora, agora_idx = colunas_existentes(), indices_existentes()
    criadas = [c for c, _, _ in faltam if c in agora]
    criados_idx = [n for n, _ in faltam_idx if n in agora_idx]
    print(f'OK: {len(criadas)}/{len(faltam)} coluna(s) e '
          f'{len(criados_idx)}/{len(faltam_idx)} índice(s). '
          f'{TABELA} agora tem {len(agora)} colunas.')
    if len(criadas) != len(faltam) or len(criados_idx) != len(faltam_idx):
        print('ATENÇÃO — não criadas: '
              + ', '.join(c for c, _, _ in faltam if c not in agora)
              + ' '.join(n for n, _ in faltam_idx if n not in agora_idx))
        return 1

    print()
    print('AS COLUNAS NASCEM VAZIAS — esta migração não trava nada por si. Quem')
    print('preenche é o detector de pares, que ainda não existe. Até ele subir,')
    print('a tela se comporta exatamente como antes.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
