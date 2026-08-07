# -*- coding: utf-8 -*-
"""Q-COLABORE Parte 6 — definição PÚBLICA de senha (/senha/<token>).

Fecha o ciclo: a Parte 5 cria a conta com ``senha_pendente=1`` (login bloqueado)
e gera um link ``tipo='SENHA'``. Aqui a própria pessoa define a senha e destrava
o acesso. Como no cadastro, quem chega NÃO faz login — a credencial é o token da
URL, e nenhuma rota deste blueprint existe sem ele.

Segurança:
- O token vale pelo hash (mesmo esquema do cadastro). O GET não consome nada.
- O POST consome o token e grava a senha no MESMO ``transacao()``: se qualquer
  parte falhar, nada persiste e o link continua válido para nova tentativa.
- NUNCA logamos a senha nem o token em claro. Mensagens de recusa não revelam se
  a conta existe — falam só do LINK.
"""
import logging
import re

from flask import Blueprint, render_template, request

from utils import cadastro_token
from utils.auth_helper import hash_password
from utils.db_helper import execute_query, transacao

logger = logging.getLogger(__name__)

senha_bp = Blueprint('senha', __name__, url_prefix='/senha')

TIPO = 'SENHA'

# Texto por motivo — fala do LINK, nunca da existência da conta.
MOTIVOS = {
    'expirado': ('Este link expirou',
                 'Links de senha valem 72 horas. Peça um novo a um administrador '
                 'da Qualicontax.'),
    'usado': ('Este link já foi utilizado',
              'A senha já foi definida com este link. Vá para a tela de login e '
              'entre normalmente.'),
    'revogado': ('Este link foi cancelado',
                 'Um administrador cancelou este link. Peça um novo para continuar.'),
    'inexistente': ('Link inválido',
                    'O endereço não corresponde a nenhum convite de senha. Confira '
                    'se copiou o link inteiro, sem cortar o final.'),
    'tipo_errado': ('Link inválido',
                    'Este link não é de definição de senha. Confira o endereço com '
                    'quem o enviou.'),
}


class _Conflito(RuntimeError):
    """O token deixou de estar consumível entre o inspecionar e o UPDATE."""


def _recusa(motivo, codigo=410):
    titulo, texto = MOTIVOS.get(motivo, MOTIVOS['inexistente'])
    return render_template('cadastro/link_invalido.html',
                           titulo=titulo, texto=texto, motivo=motivo), codigo


def _ip():
    enc = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    return (enc or request.remote_addr or '')[:45]


def _usuario(uid):
    if not uid:
        return None
    return execute_query(
        'SELECT id, nome, login, senha_pendente FROM usuarios WHERE id = %s',
        (uid,), fetch=True, fetch_one=True)


def _senha_invalida(senha):
    """Devolve a mensagem do problema, ou None se a senha passa. Espelha o front,
    mas é a AUTORIDADE — o medidor de força do template é só cortesia."""
    if len(senha) < 8:
        return 'A senha precisa ter ao menos 8 caracteres.'
    if not re.search(r'[A-Za-z]', senha):
        return 'A senha precisa ter ao menos uma letra.'
    if not re.search(r'\d', senha):
        return 'A senha precisa ter ao menos um número.'
    return None


# ---------------------------------------------------------------------------
# 1) Abrir o formulário — NÃO consome o token
# ---------------------------------------------------------------------------
@senha_bp.route('/<token>', methods=['GET'])
def formulario(token):
    est = cadastro_token.inspecionar(token, TIPO)
    if not est['ok']:
        logger.info('[senha] link recusado na abertura: %s', est['motivo'])
        return _recusa(est['motivo'])
    u = _usuario(est['link'].get('usuario_id'))
    if not u:
        # Link válido mas sem dono legível: trata como inválido, sem vazar nada.
        return _recusa('inexistente')
    # NÃO se checa senha_pendente aqui: a autorização é o TOKEN (válido, não usado,
    # não revogado). Serve tanto para a 1ª senha (senha_pendente=1) quanto para a
    # redefinição de quem já tem senha (senha_pendente=0). Como gerar() revoga o
    # link anterior, há no máximo um vivo — um link usado/revogado já cai acima.
    return render_template('senha/formulario.html',
                           token=token, nome=u['nome'], login=u['login'])


# ---------------------------------------------------------------------------
# 2) Definir a senha — AQUI o token é consumido, junto com a gravação
# ---------------------------------------------------------------------------
@senha_bp.route('/<token>', methods=['POST'])
def definir(token):
    senha = request.form.get('senha') or ''
    confirmacao = request.form.get('confirmacao') or ''

    est = cadastro_token.inspecionar(token, TIPO)
    if not est['ok']:
        return _recusa(est['motivo'])
    u = _usuario(est['link'].get('usuario_id'))
    if not u:
        return _recusa('inexistente')

    def erro(msg):
        return render_template('senha/formulario.html', token=token,
                               nome=u['nome'], login=u['login'], erro=msg), 400

    # Validação de servidor (mesmo burlando o front).
    problema = _senha_invalida(senha)
    if problema:
        return erro(problema)
    if senha != confirmacao:
        return erro('As senhas não conferem. Digite a mesma nas duas.')

    # hash_password é caro (pbkdf2) — só depois de a senha passar.
    senha_hash = hash_password(senha)
    token_hash = cadastro_token.hash_token(token)
    ip = _ip()

    try:
        with transacao() as cur:
            # Consumo ATÔMICO do token: quem obtiver rowcount==1 ganhou. url_claro
            # morre aqui, como no cadastro.
            cur.execute(
                """UPDATE cadastro_link
                      SET usado_em = NOW(), usado_ip = %s, url_claro = NULL
                    WHERE token_hash = %s AND tipo = 'SENHA'
                      AND usado_em IS NULL AND revogado_em IS NULL
                      AND expira_em > NOW()""",
                (ip, token_hash))
            if cur.rowcount != 1:
                raise _Conflito()
            # Consumido o token, grava a senha e zera senha_pendente (destrava o
            # login). Vale para 1ª senha E redefinição — a autorização foi o token,
            # consumido atomicamente acima. Mesmo transacao(): falhou aqui, o
            # consumo do token também é revertido e o link continua valendo.
            cur.execute(
                """UPDATE usuarios SET senha_hash = %s, senha_pendente = 0
                    WHERE id = %s""",
                (senha_hash, u['id']))
    except _Conflito:
        # Alguém consumiu/expirou entre o inspecionar e o UPDATE — relê o motivo.
        est2 = cadastro_token.inspecionar(token, TIPO)
        return _recusa(est2['motivo'] if not est2['ok'] else 'usado')
    except Exception:
        # NUNCA ecoar a exceção crua — nem senha, nem token entram em log.
        logger.exception('[senha] falha ao gravar senha do usuário %s', u['id'])
        return erro('Não consegui salvar sua senha agora. Tente novamente em '
                    'instantes — seu link continua valendo.')

    logger.info('[senha] usuário %s definiu a senha; login liberado.', u['id'])
    return render_template('senha/enviado.html', nome=u['nome'], login=u['login'])
