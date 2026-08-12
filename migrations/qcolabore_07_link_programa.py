# -*- coding: utf-8 -*-
"""
Q-COLABORE — migration 07: acrescenta 'PROGRAMA' ao ENUM ``tipo`` de
``cadastro_link``.

Por que
-------
Passa a existir um link de uso único para BAIXAR o instalador do Q-Colabore,
para o Anderson mandar por WhatsApp a quem não tem acesso ao sistema. A
mecânica (token com hash, prefixo, validade, uso único, revogação) é a MESMA
dos links de cadastro e de senha que já existem — então o link novo mora na
mesma tabela, com um tipo novo, em vez de ganhar tabela própria.

POR QUE O VALOR VAI NO FIM DA LISTA
-----------------------------------
Um ENUM é armazenado como o ÍNDICE do valor, não como o texto. Acrescentar no
fim não mexe em índice nenhum dos valores existentes: é alteração de metadado,
instantânea. Inserir no meio renumeraria tudo a partir dali, forçaria a
reescrita da tabela e — pior — mudaria o significado das linhas já gravadas.

Por isso o ALTER exige ``ALGORITHM=INSTANT`` explicitamente: se um dia alguém
editar este arquivo e puser o valor fora do fim, o servidor RECUSA em vez de
reescrever a tabela caladamente. A exigência é a rede de proteção. (Mesmo
raciocínio, e mesma medição, da migration 06.)

Idempotente e reversível.

  python migrations/qcolabore_07_link_programa.py             # aplica
  python migrations/qcolabore_07_link_programa.py --reverter  # tira do ENUM
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query   # noqa: E402

TABELA = 'cadastro_link'
COLUNA = 'tipo'
ANTES = ('CADASTRO', 'SENHA')
DEPOIS = ('CADASTRO', 'SENHA', 'PROGRAMA')


def _tipo_atual():
    r = execute_query(
        "SELECT COLUMN_TYPE t FROM information_schema.COLUMNS "
        " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABELA, COLUNA), fetch=True, fetch_one=True)
    return (r or {}).get('t') or ''


def _enum(valores):
    return "enum(" + ",".join("'%s'" % v for v in valores) + ")"


def _alterar(valores):
    sql = ("ALTER TABLE %s MODIFY %s %s NOT NULL, ALGORITHM=INSTANT"
           % (TABELA, COLUNA, _enum(valores)))
    print('   %s' % sql)
    execute_query(sql, fetch=False)


def aplicar():
    atual = _tipo_atual()
    print('ENUM atual: %s' % (atual or '(coluna não encontrada)'))
    if not atual:
        print('ABORTADO: %s.%s não existe.' % (TABELA, COLUNA))
        return 1
    if 'PROGRAMA' in atual:
        print('Já contém PROGRAMA — nada a fazer.')
        return 0
    _alterar(DEPOIS)
    print('ENUM depois: %s' % _tipo_atual())
    return 0


def reverter():
    atual = _tipo_atual()
    print('ENUM atual: %s' % atual)
    if 'PROGRAMA' not in atual:
        print('Não contém PROGRAMA — nada a fazer.')
        return 0
    # Guard: reverter com linhas PROGRAMA gravadas as truncaria para ''.
    r = execute_query("SELECT COUNT(*) n FROM %s WHERE %s = 'PROGRAMA'" % (TABELA, COLUNA),
                      fetch=True, fetch_one=True) or {}
    if int(r.get('n') or 0):
        print('ABORTADO: existem %s linha(s) com tipo=PROGRAMA. Reverter as '
              'destruiria.' % r['n'])
        return 1
    _alterar(ANTES)
    print('ENUM depois: %s' % _tipo_atual())
    return 0


if __name__ == '__main__':
    sys.exit(reverter() if '--reverter' in sys.argv else aplicar())
