# -*- coding: utf-8 -*-
"""Sugestões de login e nick a partir do nome completo (Q-Colabore Parte 3).

Por que o SERVIDOR oferece em vez de o candidato digitar
--------------------------------------------------------
Login é chave única em ``usuarios``. Deixar o campo livre produz três problemas
de uma vez: o candidato inventa algo com acento/espaço/maiúscula que não segue a
convenção da casa, descobre a colisão só quando o admin tenta aprovar, e o admin
fica com o trabalho de negociar um nome novo com alguém que já foi embora da
tela. Oferecendo combinações prontas — todas já conferidas contra a base — o
candidato escolhe em um clique e a colisão nunca chega à aprovação.

Regras: minúsculas, sem acento, sem espaço. Só [a-z0-9._].
"""
import re
import unicodedata

from utils.db_helper import execute_query

MAX_LOGIN = 100          # usuarios.login  varchar(100)
MAX_NICK = 60            # usuarios.nick   varchar(60)
QTD_SUGESTOES = 6


def _sem_acento(txt: str) -> str:
    """'João Antônio' -> 'joao antonio'. NFD + descarta os diacríticos."""
    nfd = unicodedata.normalize('NFD', txt or '')
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')


def _limpar(txt: str) -> str:
    """Normaliza para o alfabeto do login: minúsculo, sem acento, só [a-z0-9]."""
    return re.sub(r'[^a-z0-9]', '', _sem_acento(txt).lower())


def partes_do_nome(nome: str) -> list:
    """['anderson','antunes','vieira'] — descarta partículas e iniciais soltas.

    'de/da/do/dos/das/e' não ajudam a identificar ninguém e só poluiriam as
    combinações ('anderson.de.vieira'). Partes de 1 letra idem: viram inicial
    quando útil, nunca uma parte por si.
    """
    ignorar = {'de', 'da', 'do', 'dos', 'das', 'e'}
    fora = []
    for p in _sem_acento(nome or '').lower().split():
        limpa = _limpar(p)
        if limpa and limpa not in ignorar and len(limpa) > 1:
            fora.append(limpa)
    return fora


def _candidatos_login(partes: list) -> list:
    """Combinações em ordem de preferência: da mais curta e legível à mais longa."""
    if not partes:
        return []
    pri, ult = partes[0], partes[-1]
    meio = partes[1] if len(partes) > 2 else None
    sug = [pri]                                   # anderson
    if ult != pri:
        sug += [f'{pri}.{ult}',                   # anderson.vieira
                f'{pri}{ult[0]}',                 # andersonv
                f'{pri[0]}.{ult}',                # a.vieira
                f'{pri}{ult}']                    # andersonvieira
    if meio:
        sug.append(f'{pri}.{meio}')               # anderson.antunes
    if len(partes) > 2:
        sug.append(pri + ''.join(p[0] for p in partes[1:]))   # andersonav
    return sug


def _candidatos_nick(partes: list) -> list:
    """Nick é rótulo de UI: curto e humano, não identificador."""
    if not partes:
        return []
    pri, ult = partes[0], partes[-1]
    sug = [pri.capitalize()]                                  # Anderson
    if ult != pri:
        sug += [f'{pri.capitalize()} {ult.capitalize()}',     # Anderson Vieira
                f'{pri.capitalize()} {ult[0].upper()}.']      # Anderson V.
    if len(pri) > 4:
        sug.append(pri[:4].capitalize())                      # Ande
    return sug


def _logins_ocupados(valores: list) -> set:
    """Quais desses logins já existem — em usuarios OU numa pendência aberta.

    Checa as DUAS origens de propósito: um login pedido por outro candidato que
    ainda não foi aprovado não está em usuarios, mas prometê-lo a um segundo
    candidato criaria a colisão para o admin resolver depois — que é justamente
    o que este módulo existe para evitar.
    """
    if not valores:
        return set()
    marcas = ','.join(['%s'] * len(valores))
    ocupados = set()
    for r in execute_query(
            f'SELECT login FROM usuarios WHERE login IN ({marcas})',
            tuple(valores), fetch=True) or []:
        ocupados.add((r['login'] or '').lower())
    for r in execute_query(
            f"""SELECT login_escolhido AS login FROM cadastro_pendente
                 WHERE status = 'PENDENTE' AND login_escolhido IN ({marcas})""",
            tuple(valores), fetch=True) or []:
        ocupados.add((r['login'] or '').lower())
    return ocupados


def sugerir(nome_completo: str, quantidade: int = QTD_SUGESTOES) -> dict:
    """{'partes', 'logins', 'nicks'} — logins já filtrados contra a base.

    Se todas as combinações estiverem ocupadas, acrescenta sufixo numérico
    (anderson2, anderson3...) até ter o que oferecer: o formulário nunca fica
    sem opção.
    """
    partes = partes_do_nome(nome_completo)
    if not partes:
        return {'partes': [], 'logins': [], 'nicks': []}

    brutos, vistos = [], set()
    for s in _candidatos_login(partes):
        s = s[:MAX_LOGIN]
        if s and s not in vistos:
            vistos.add(s)
            brutos.append(s)

    ocupados = _logins_ocupados(brutos)
    livres = [s for s in brutos if s not in ocupados]

    # Nada livre (ou pouco): numera a primeira combinação até completar.
    if len(livres) < quantidade and brutos:
        base = brutos[0]
        extras = [f'{base}{i}' for i in range(2, 2 + quantidade * 2)]
        ocup2 = _logins_ocupados(extras)
        for e in extras:
            if len(livres) >= quantidade:
                break
            if e not in ocup2 and e not in livres:
                livres.append(e)

    nicks, vistos_n = [], set()
    for s in _candidatos_nick(partes):
        s = s[:MAX_NICK]
        if s and s not in vistos_n:
            vistos_n.add(s)
            nicks.append(s)

    return {'partes': partes, 'logins': livres[:quantidade], 'nicks': nicks}


def login_disponivel(login: str) -> bool:
    """Confere UM login. Usado na validação do POST — a lista pode ter envelhecido
    entre o carregamento da tela e o envio."""
    login = (login or '').strip().lower()
    if not login:
        return False
    return login not in _logins_ocupados([login])


def login_valido(login: str) -> bool:
    """Formato: 3..100 chars, começa com letra, só [a-z0-9._]."""
    return bool(re.fullmatch(r'[a-z][a-z0-9._]{2,%d}' % (MAX_LOGIN - 1), login or ''))
