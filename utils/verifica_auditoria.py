# -*- coding: utf-8 -*-
"""Quais rotas que ESCREVEM não deixam rastro de quem fez.

Por que existe
--------------
O Anderson pediu, em 24/08/2026: "eu quero em todo o sistema eu consiga
auditar quem fez tal coisa ou não". A máquina já existia — ``registrar`` e
``registrar_agente`` gravam em ``logs_sistema`` com nome e login COPIADOS, de
modo que apagar o usuário não apaga o rastro. O que faltava era cobertura: na
primeira medida, 53 de 150 rotas de escrita registravam.

Fechar as 97 uma vez não resolve sozinho: a rota nova de amanhã nasce sem
registro e ninguém percebe. Este script é a trava — roda, lista o que falta e
sai com código 1 quando há rota descoberta fora da lista de dispensadas.

    python utils/verifica_auditoria.py           # lista o que falta
    python utils/verifica_auditoria.py --tudo    # lista também as cobertas

Lê com ``ast``, não com texto: decorador quebrado em várias linhas enganaria
uma busca por regex, e uma chamada a ``registrar`` dentro de um comentário
contaria como cobertura.
"""
import argparse
import ast
import glob
import io
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METODOS_DE_ESCRITA = {'POST', 'PUT', 'PATCH', 'DELETE'}
LOGGERS = {'registrar', 'registrar_agente'}

#: Rotas que escrevem e NÃO precisam registrar, com o motivo. Toda linha aqui
#: é uma decisão consciente — a lista é curta de propósito.
DISPENSADAS = {
    # Sem sessão: quem chama é o agente com chave própria, e ele usa
    # registrar_agente no lugar certo.
    ('robo_saidas.py', 'receber_saida'): 'ator de máquina; registra em outra camada',
    # POST que so LE: a previa da regra recebe o desenho no corpo (nao cabe
    # em querystring) e devolve a contagem. Nao escreve nada.
    ('financeiro.py', 'extrato_regra_previa'): 'POST que so le — a previa nao grava',
    # Tela aposentada em 21/08/2026 (tabela vazia, consulta quebrada).
    ('financeiro.py', 'recebimento_excluir'): 'tela aposentada',
    ('financeiro.py', 'recebimento_excluir_lote'): 'tela aposentada',
}


def _metodos(dec):
    """Os métodos HTTP declarados em @bp.route(..., methods=[...])."""
    if not isinstance(dec, ast.Call):
        return set()
    nome = getattr(dec.func, 'attr', getattr(dec.func, 'id', ''))
    if nome != 'route':
        return set()
    for kw in dec.keywords:
        if kw.arg == 'methods' and isinstance(kw.value, (ast.List, ast.Tuple)):
            return {e.value.upper() for e in kw.value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return {'GET'}


def _registra(fn):
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            nome = getattr(n.func, 'attr', getattr(n.func, 'id', ''))
            if nome in LOGGERS:
                return True
    return False


def varrer():
    """[(arquivo, funcao, metodos, registra)] de toda rota que escreve."""
    achados = []
    for caminho in sorted(glob.glob(os.path.join(RAIZ, 'routes', '*.py'))):
        arq = os.path.basename(caminho)
        try:
            arvore = ast.parse(io.open(caminho, encoding='utf-8').read())
        except SyntaxError as e:
            print(f'  !! {arq} nao compila: {e}')
            continue
        for no in ast.walk(arvore):
            if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            metodos = set()
            for d in no.decorator_list:
                metodos |= _metodos(d)
            escreve = metodos & METODOS_DE_ESCRITA
            if not escreve:
                continue
            achados.append((arq, no.name, ','.join(sorted(escreve)), _registra(no)))
    return achados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tudo', action='store_true', help='mostra também as cobertas')
    args = ap.parse_args()

    achados = varrer()
    com = [a for a in achados if a[3]]
    sem = [a for a in achados if not a[3] and (a[0], a[1]) not in DISPENSADAS]
    dispensadas = [a for a in achados if not a[3] and (a[0], a[1]) in DISPENSADAS]

    total = len(achados)
    pct = (len(com) * 100 // total) if total else 100
    print(f'Rotas que escrevem: {total}')
    print(f'  com rastro:   {len(com)}  ({pct}%)')
    print(f'  dispensadas:  {len(dispensadas)}')
    print(f'  SEM rastro:   {len(sem)}')

    if sem:
        print('\nSEM RASTRO — cada uma destas é uma pergunta que o sistema')
        print('não consegue responder ("quem fez isso?"):')
        por_arq = {}
        for arq, fn, met, _ in sem:
            por_arq.setdefault(arq, []).append(f'{fn} [{met}]')
        for arq in sorted(por_arq, key=lambda a: -len(por_arq[a])):
            print(f'\n  {arq}  ({len(por_arq[arq])})')
            for fn in sorted(por_arq[arq]):
                print(f'     {fn}')

    if args.tudo and com:
        print('\nCOM RASTRO:')
        por_arq = {}
        for arq, fn, met, _ in com:
            por_arq.setdefault(arq, []).append(fn)
        for arq in sorted(por_arq):
            print(f'  {arq:<26} {len(por_arq[arq]):>3}')

    if dispensadas:
        print('\nDISPENSADAS (e por quê):')
        for arq, fn, met, _ in dispensadas:
            print(f'  {arq}:{fn} — {DISPENSADAS[(arq, fn)]}')

    return 1 if sem else 0


if __name__ == '__main__':
    sys.exit(main())
