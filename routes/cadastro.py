# -*- coding: utf-8 -*-
"""Q-COLABORE Parte 3 — formulário PÚBLICO de cadastro (/cadastro/<token>).

Quem chega aqui não tem conta e não faz login: entrou por um link de uso único
que um admin gerou na tela de Usuários. O token é a credencial — por isso ele
governa tudo, e nenhuma rota deste blueprint existe sem ele na URL.

FATIA: esta é a Parte 3 — moldura + IDENTIFICAÇÃO (nome, login, nick, e se a
pessoa já trabalha na casa). Endereço, contatos e dados bancários são a Parte 4;
as colunas já existem e aceitam NULL, então a pendência nasce válida e o admin
já a vê na fila.

Nunca vaza stack trace: link expirado/usado/revogado tem tela própria com o
motivo em português. O token só é CONSUMIDO no envio final — abrir o formulário
(e reabrir, e recarregar) não gasta o link.
"""
import logging

from flask import (Blueprint, jsonify, render_template, request)

from utils import cadastro_sugestoes, cadastro_token
from utils.db_helper import execute_query, transacao

logger = logging.getLogger(__name__)

cadastro_bp = Blueprint('cadastro', __name__, url_prefix='/cadastro')

TIPO = 'CADASTRO'

# Texto por motivo. A tela de recusa fala com uma PESSOA que não tem conta e não
# tem como se ajudar sozinha — cada motivo diz o que aconteceu e qual é o passo.
MOTIVOS = {
    'expirado': ('Este link expirou',
                 'Links de cadastro valem 72 horas. Peça um novo para quem enviou '
                 'este — leva um minuto para gerar.'),
    'usado': ('Este link já foi utilizado',
              'Cada link funciona uma única vez. Se você já enviou seu cadastro, '
              'ele está na fila de aprovação e não precisa reenviar.'),
    'revogado': ('Este link foi cancelado',
                 'Quem gerou o link o cancelou. Peça um novo para continuar.'),
    'inexistente': ('Link inválido',
                    'O endereço não corresponde a nenhum convite. Confira se você '
                    'copiou o link inteiro, sem cortar o final.'),
    'tipo_errado': ('Link inválido',
                    'Este link não é de cadastro. Confira o endereço com quem o enviou.'),
}


def _recusa(motivo, codigo=410):
    titulo, texto = MOTIVOS.get(motivo, MOTIVOS['inexistente'])
    return render_template('cadastro/link_invalido.html',
                           titulo=titulo, texto=texto, motivo=motivo), codigo


def _ip():
    """IP do visitante, respeitando o proxy do Railway."""
    enc = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    return (enc or request.remote_addr or '')[:45]


def _departamentos():
    return execute_query(
        'SELECT id, nome FROM departamentos WHERE ativo = 1 ORDER BY nome',
        fetch=True) or []


def _pendencia_do_link(link_id):
    return execute_query(
        'SELECT id, status FROM cadastro_pendente WHERE link_id = %s',
        (link_id,), fetch=True, fetch_one=True)


# ---------------------------------------------------------------------------
# 1) Abrir o formulário — NÃO consome o token
# ---------------------------------------------------------------------------
@cadastro_bp.route('/<token>', methods=['GET'])
def formulario(token):
    est = cadastro_token.inspecionar(token, TIPO)
    if not est['ok']:
        logger.info('[cadastro] link recusado na abertura: %s', est['motivo'])
        return _recusa(est['motivo'])
    link = est['link']
    return render_template('cadastro/formulario.html',
                           token=token,
                           destinatario=link.get('destinatario'),
                           expira_em=link.get('expira_em'),
                           departamentos=_departamentos())


# ---------------------------------------------------------------------------
# 2) Sugestões de login/nick — o servidor oferece, o candidato escolhe
# ---------------------------------------------------------------------------
@cadastro_bp.route('/<token>/sugestoes', methods=['POST'])
def sugestoes(token):
    """Combinações a partir do nome, já conferidas contra a base. Não consome."""
    est = cadastro_token.inspecionar(token, TIPO)
    if not est['ok']:
        return jsonify(ok=False, motivo=est['motivo']), 410
    nome = (request.form.get('nome_completo') or '').strip()
    if len(nome) < 3:
        return jsonify(ok=True, logins=[], nicks=[])
    try:
        s = cadastro_sugestoes.sugerir(nome)
    except Exception:
        logger.exception('[cadastro] falha ao sugerir login para %r', nome[:40])
        return jsonify(ok=False, msg='Não consegui sugerir agora.'), 500
    return jsonify(ok=True, logins=s['logins'], nicks=s['nicks'])


# ---------------------------------------------------------------------------
# 3) Enviar — AQUI o token é consumido
# ---------------------------------------------------------------------------
@cadastro_bp.route('/<token>', methods=['POST'])
def enviar(token):
    nome = (request.form.get('nome_completo') or '').strip()
    login = (request.form.get('login_escolhido') or '').strip().lower()
    nick = (request.form.get('nick_escolhido') or '').strip() or None
    ja_func = 1 if (request.form.get('ja_funcionario') or '') == '1' else 0
    deps = [int(d) for d in request.form.getlist('departamentos') if d.isdigit()]

    def erro(msg):
        return render_template('cadastro/formulario.html',
                               token=token, erro=msg, form=request.form,
                               departamentos=_departamentos()), 400

    if len(nome) < 5 or ' ' not in nome:
        return erro('Informe seu nome completo (nome e sobrenome).')
    if not cadastro_sugestoes.login_valido(login):
        return erro('Escolha um dos logins sugeridos.')
    if not cadastro_sugestoes.login_disponivel(login):
        return erro('Esse login acabou de ser tomado. Escolha outro da lista.')

    # O consumo é ATÔMICO: quem obtiver rowcount==1 segue. Dois envios
    # simultâneos do mesmo link resultam num cadastro e num "já utilizado".
    res = cadastro_token.consumir(token, TIPO, ip=_ip())
    pendente_id = None

    if not res['ok']:
        # 'usado' pode ser DUAS coisas muito diferentes, e tratá-las igual
        # prenderia o candidato do lado de fora:
        #
        #   (a) reenvio do formulário — a pendência existe e está aberta.
        #       É UPDATE na mesma linha; o UNIQUE (link_id) garante que nunca
        #       vira uma segunda candidatura.
        #   (b) o consumo passou mas a gravação falhou (queda no meio). O link
        #       está queimado e NÃO há pendência: sem este caminho, a pessoa
        #       tentaria de novo e receberia "já utilizado" para sempre, com o
        #       cadastro dela em lugar nenhum.
        #
        # Só o que NÃO se permite é reescrever pendência já decidida pelo admin.
        alvo = res.get('link') or {}
        if res['motivo'] != 'usado' or not alvo.get('id'):
            logger.info('[cadastro] envio recusado: %s', res['motivo'])
            return _recusa(res['motivo'])
        pend = _pendencia_do_link(alvo['id'])
        if pend and pend['status'] != 'PENDENTE':
            logger.info('[cadastro] envio recusado: pendência %s já %s',
                        pend['id'], pend['status'])
            return _recusa('usado')
        pendente_id = pend['id'] if pend else None
        link_id = alvo['id']
    else:
        link_id = res['link']['id']

    try:
        with transacao() as cur:
            if pendente_id:
                cur.execute(
                    """UPDATE cadastro_pendente
                          SET ja_funcionario=%s, nome_completo=%s,
                              login_escolhido=%s, nick_escolhido=%s
                        WHERE id=%s""",
                    (ja_func, nome, login, nick, pendente_id))
            else:
                cur.execute(
                    """INSERT INTO cadastro_pendente
                         (link_id, ja_funcionario, nome_completo,
                          login_escolhido, nick_escolhido)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (link_id, ja_func, nome, login, nick))
                pendente_id = cur.lastrowid
            cur.execute('DELETE FROM cadastro_pendente_departamentos WHERE pendente_id=%s',
                        (pendente_id,))
            for d in deps:
                cur.execute(
                    """INSERT INTO cadastro_pendente_departamentos
                         (pendente_id, departamento_id) VALUES (%s,%s)""",
                    (pendente_id, d))
    except Exception:
        # Nunca devolve a exceção crua a quem está do lado de fora do sistema.
        logger.exception('[cadastro] falha ao gravar pendência do link %s', link_id)
        return erro('Não consegui salvar seu cadastro agora. Tente novamente em '
                    'instantes — seu link continua valendo.')

    logger.info('[cadastro] pendência %s criada/atualizada pelo link %s.',
                pendente_id, link_id)
    return render_template('cadastro/enviado.html', nome=nome, login=login)
