# -*- coding: utf-8 -*-
"""
Q-COLABORE â€” migration 09: acrescenta 'PROGRAMA' ao ENUM ``tipo`` de
``cadastro_link``.

Por que
-------
Passa a existir um link de uso Ãºnico para BAIXAR o instalador do Q-Colabore,
para o Anderson mandar por WhatsApp a quem nÃ£o tem acesso ao sistema. A
mecÃ¢nica (token com hash, prefixo, validade, uso Ãºnico, revogaÃ§Ã£o) Ã© a MESMA
dos links de cadastro e de senha que jÃ¡ existem â€” entÃ£o o link novo mora na
mesma tabela, com um tipo novo, em vez de ganhar tabela prÃ³pria.

POR QUE O VALOR VAI NO FIM DA LISTA
-----------------------------------
Um ENUM Ã© armazenado como o ÃNDICE do valor, nÃ£o como o texto. Acrescentar no
fim nÃ£o mexe em Ã­ndice nenhum dos valores existentes: Ã© alteraÃ§Ã£o de metadado,
instantÃ¢nea. Inserir no meio renumeraria tudo a partir dali, forÃ§aria a
reescrita da tabela e â€” pior â€” mudaria o significado das linhas jÃ¡ gravadas.

Por isso o ALTER exige ``ALGORITHM=INSTANT`` explicitamente: se um dia alguÃ©m
editar este arquivo e puser o valor fora do fim, o servidor RECUSA em vez de
reescrever a tabela caladamente. A exigÃªncia Ã© a rede de proteÃ§Ã£o. (Mesmo
raciocÃ­nio, e mesma mediÃ§Ã£o, da migration 06.)

Idempotente e reversÃ­vel.

  python migrations/qcolabore_09_link_programa.py             # aplica
  python migrations/qcolabore_09_link_programa.py --reverter  # tira do ENUM
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
    print('ENUM atual: %s' % (atual or '(coluna nÃ£o encontrada)'))
    if not atual:
        print('ABORTADO: %s.%s nÃ£o existe.' % (TABELA, COLUNA))
        return 1
    if 'PROGRAMA' in atual:
        print('JÃ¡ contÃ©m PROGRAMA â€” nada a fazer.')
        return 0
    _alterar(DEPOIS)
    print('ENUM depois: %s' % _tipo_atual())
    return 0


def reverter():
    atual = _tipo_atual()
    print('ENUM atual: %s' % atual)
    if 'PROGRAMA' not in atual:
        print('NÃ£o contÃ©m PROGRAMA â€” nada a fazer.')
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

