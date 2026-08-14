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
import re

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


_CONTA_TIPOS = ('CORRENTE', 'POUPANCA', 'SALARIO', 'PAGAMENTO')
_PIX_TIPOS = ('CPF', 'CNPJ', 'EMAIL', 'TELEFONE', 'ALEATORIA')

# Modalidade de trabalho: os três valores do ENUM da migration 07. Qualquer
# outra coisa é lixo de POST forjado — vira 400 amigável, nunca 500.
_MODALIDADES = ('HOME_OFFICE', 'ESCRITORIO', 'HIBRIDO')

# E-mail "bom o suficiente": tem um @, um ponto no domínio e nada de espaço.
# Não é RFC 5322 — é o mesmo rigor prático que o resto do cadastro pede.
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _txt(form, campo, limite):
    """Campo de texto normalizado. Vazio vira None (a coluna é nullable)."""
    v = (form.get(campo) or '').strip()
    return v[:limite] or None


def _campos_parte4(form) -> dict:
    """Contato, endereço e bancários — todos opcionais, todos normalizados.

    ATENÇÃO: os valores bancários que saem daqui NÃO podem ir para log nenhum
    (regra registrada na migration 04). Este dicionário existe para ser gravado
    e descartado — nada de imprimi-lo em except, nem em debug.
    """
    so_dig = lambda c, n: (re.sub(r'\D', '', form.get(c) or '') or None) and \
        re.sub(r'\D', '', form.get(c) or '')[:n]
    nasc = (form.get('data_nascimento') or '').strip() or None
    if nasc and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', nasc):
        nasc = None                      # <input type=date> já entrega ISO
    conta_tipo = (form.get('conta_tipo') or '').strip().upper() or None
    pix_tipo = (form.get('pix_tipo') or '').strip().upper() or None
    d = {
        'cpf': so_dig('cpf', 14), 'nasc': nasc,
        'email_p': _txt(form, 'email_pessoal', 255),
        'email_c': _txt(form, 'email_corporativo', 255),
        'cel_p': so_dig('celular_pessoal', 20),
        'cel_c': so_dig('celular_corporativo', 20),
        'cep': so_dig('cep', 9),
        'logradouro': _txt(form, 'logradouro', 255),
        'numero': _txt(form, 'numero', 20),
        'complemento': _txt(form, 'complemento', 100),
        'bairro': _txt(form, 'bairro', 100),
        'cidade': _txt(form, 'cidade', 100),
        'estado': (_txt(form, 'estado', 2) or '').upper() or None,
        'banco_cod': so_dig('banco_codigo', 5),
        'banco_nome': _txt(form, 'banco_nome', 120),
        'agencia': _txt(form, 'agencia', 15),
        'conta': _txt(form, 'conta', 25),
        'conta_tipo': conta_tipo if conta_tipo in _CONTA_TIPOS else None,
        'titular': _txt(form, 'titular_nome', 255),
        'titular_cpf': so_dig('titular_cpf', 14),
        'pix_tipo': pix_tipo if pix_tipo in _PIX_TIPOS else None,
        'pix_chave': _txt(form, 'pix_chave', 140),
    }
    # Só grava a linha bancária se houver ALGUMA informação de verdade — senão
    # a tabela sensível se enche de linhas vazias por cadastro.
    d['tem_banco'] = any(d[k] for k in
                         ('banco_cod', 'banco_nome', 'agencia', 'conta', 'pix_chave'))
    return d


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
# 3) CEP — Parte 4
#
# A /api/cep do blueprint de clientes é @login_required e não serve aqui. Em vez
# de abrir aquela, o endereço ganha o SEU endpoint, com o mesmo portão de todo o
# resto: sem token válido, não responde. Assim o app não vira um proxy de CEP
# aberto na internet só porque existe um formulário público.
# ---------------------------------------------------------------------------
@cadastro_bp.route('/<token>/cep/<cep>', methods=['GET'])
def buscar_cep(token, cep):
    est = cadastro_token.inspecionar(token, TIPO)
    if not est['ok']:
        return jsonify(ok=False, motivo=est['motivo']), 410
    digitos = re.sub(r'\D', '', cep or '')
    if len(digitos) != 8:
        return jsonify(ok=False, msg='CEP deve ter 8 dígitos.'), 400
    try:
        import requests
        r = requests.get(f'https://viacep.com.br/ws/{digitos}/json/', timeout=5)
        dados = r.json() if r.status_code == 200 else {}
    except Exception:
        logger.warning('[cadastro] ViaCEP indisponível para %s', digitos)
        return jsonify(ok=False, msg='Não consegui consultar o CEP agora. '
                                     'Você pode preencher à mão.'), 503
    if dados.get('erro'):
        return jsonify(ok=False, msg='CEP não encontrado.'), 404
    return jsonify(ok=True, logradouro=dados.get('logradouro') or '',
                   bairro=dados.get('bairro') or '',
                   cidade=dados.get('localidade') or '',
                   estado=(dados.get('uf') or '').upper())


# ---------------------------------------------------------------------------
# 4) Enviar — AQUI o token é consumido
# ---------------------------------------------------------------------------
@cadastro_bp.route('/<token>', methods=['POST'])
def enviar(token):
    nome = (request.form.get('nome_completo') or '').strip()
    login = (request.form.get('login_escolhido') or '').strip().lower()
    nick = (request.form.get('nick_escolhido') or '').strip() or None
    ja_func = 1 if (request.form.get('ja_funcionario') or '') == '1' else 0
    modalidade = (request.form.get('modalidade_trabalho') or '').strip().upper()
    deps = [int(d) for d in request.form.getlist('departamentos') if d.isdigit()]

    def erro(msg, causa):
        """Recusa de validação: devolve o formulário com a mensagem E DEIXA
        RASTRO.

        Antes este caminho era CEGO. As recusas abaixo acontecem ANTES do
        consumo do token — de propósito, para um erro de digitação não queimar
        o convite —, mas isso significa que um candidato barrado não deixa
        marca em lugar nenhum: o link continua PENDENTE, nada entra em
        cadastro_pendente, e o log ficava mudo. Em 13/08/2026 cinco candidatos
        não chegaram à fila e não houve como saber se erraram algo, se o
        formulário quebrou ou se nem enviaram.

        Só o motivo e o prefixo do token vão para o log — nunca o token inteiro
        (é credencial) nem CPF/endereço/dado bancário do candidato.
        """
        logger.info('[cadastro] envio BARRADO na validacao (%s) — link %s… '
                    'segue PENDENTE, nada gravado.', causa, (token or '')[:8])
        return render_template('cadastro/formulario.html',
                               token=token, erro=msg, form=request.form,
                               departamentos=_departamentos()), 400

    if len(nome) < 5 or ' ' not in nome:
        return erro('Informe seu nome completo (nome e sobrenome).', 'nome_incompleto')
    if not cadastro_sugestoes.login_valido(login):
        return erro('Escolha um dos logins sugeridos.', 'login_formato_invalido')
    if not cadastro_sugestoes.login_disponivel(login):
        return erro('Esse login acabou de ser tomado. Escolha outro da lista.', 'login_ja_tomado')

    # Modalidade é obrigatória para TODOS e checada contra o ENUM no servidor:
    # o front manda um dos três; lixo forjado cai aqui como 400, não 500.
    if modalidade not in _MODALIDADES:
        return erro('Escolha como você vai trabalhar: home office, escritório ou híbrido.', 'modalidade_ausente')

    # Quem JÁ é funcionário: e-mail e celular corporativos são obrigatórios e
    # validados no servidor (o front esconde/mostra, mas não é a fonte da verdade).
    email_corp = (request.form.get('email_corporativo') or '').strip()
    cel_corp_dig = re.sub(r'\D', '', request.form.get('celular_corporativo') or '')
    if ja_func:
        if not email_corp:
            return erro('Informe seu e-mail corporativo — você marcou que já '
                        'trabalha na Qualicontax.', 'email_corp_ausente')
        if not _EMAIL_RE.match(email_corp):
            return erro('O e-mail corporativo não parece válido. Confira e tente de novo.', 'email_corp_invalido')
        if len(cel_corp_dig) < 10:
            return erro('Informe seu celular corporativo com DDD — você marcou que '
                        'já trabalha na Qualicontax.', 'celular_corp_invalido')

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

    # --- Parte 4: contato, endereço e bancários. Todos OPCIONAIS: o cadastro
    # não pode travar porque alguém não sabe a agência de cor. O admin cobra o
    # que faltar na aprovação.
    p = _campos_parte4(request.form)

    # Contatos corporativos SÓ para quem já é funcionário. Quem não é nem viu os
    # campos — se um POST forjado os trouxer, viram NULL aqui (regra: "= NÃO →
    # gravam NULL"). Para quem é, já passaram pela validação acima.
    if not ja_func:
        p['email_c'] = None
        p['cel_c'] = None

    try:
        with transacao() as cur:
            if pendente_id:
                cur.execute(
                    """UPDATE cadastro_pendente
                          SET ja_funcionario=%s, nome_completo=%s,
                              login_escolhido=%s, nick_escolhido=%s,
                              cpf=%s, data_nascimento=%s,
                              email_pessoal=%s, email_corporativo=%s,
                              celular_pessoal=%s, celular_corporativo=%s,
                              modalidade_trabalho=%s,
                              cep=%s, logradouro=%s, numero=%s, complemento=%s,
                              bairro=%s, cidade=%s, estado=%s
                        WHERE id=%s""",
                    (ja_func, nome, login, nick, p['cpf'], p['nasc'],
                     p['email_p'], p['email_c'], p['cel_p'], p['cel_c'],
                     modalidade,
                     p['cep'], p['logradouro'], p['numero'], p['complemento'],
                     p['bairro'], p['cidade'], p['estado'], pendente_id))
            else:
                cur.execute(
                    """INSERT INTO cadastro_pendente
                         (link_id, ja_funcionario, nome_completo,
                          login_escolhido, nick_escolhido, cpf, data_nascimento,
                          email_pessoal, email_corporativo,
                          celular_pessoal, celular_corporativo,
                          modalidade_trabalho,
                          cep, logradouro, numero, complemento,
                          bairro, cidade, estado)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (link_id, ja_func, nome, login, nick, p['cpf'], p['nasc'],
                     p['email_p'], p['email_c'], p['cel_p'], p['cel_c'],
                     modalidade,
                     p['cep'], p['logradouro'], p['numero'], p['complemento'],
                     p['bairro'], p['cidade'], p['estado']))
                pendente_id = cur.lastrowid
            cur.execute('DELETE FROM cadastro_pendente_departamentos WHERE pendente_id=%s',
                        (pendente_id,))
            for d in deps:
                cur.execute(
                    """INSERT INTO cadastro_pendente_departamentos
                         (pendente_id, departamento_id) VALUES (%s,%s)""",
                    (pendente_id, d))

            # BANCÁRIOS em tabela à parte (gate admin/Financeiro na leitura).
            # DELETE+INSERT em vez de upsert: reenvio do formulário substitui,
            # e não há chave natural além do pendente_id.
            cur.execute('DELETE FROM usuario_dados_bancarios WHERE pendente_id=%s',
                        (pendente_id,))
            if p['tem_banco']:
                cur.execute(
                    """INSERT INTO usuario_dados_bancarios
                         (pendente_id, banco_codigo, banco_nome, agencia, conta,
                          conta_tipo, titular_nome, titular_cpf, pix_tipo, pix_chave)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (pendente_id, p['banco_cod'], p['banco_nome'], p['agencia'],
                     p['conta'], p['conta_tipo'], p['titular'] or nome,
                     p['titular_cpf'] or p['cpf'], p['pix_tipo'], p['pix_chave']))
    except Exception:
        # Nunca devolve a exceção crua a quem está do lado de fora do sistema.
        logger.exception('[cadastro] falha ao gravar pendência do link %s', link_id)
        return erro('Não consegui salvar seu cadastro agora. Tente novamente em '
                    'instantes — seu link continua valendo.')

    logger.info('[cadastro] pendência %s criada/atualizada pelo link %s.',
                pendente_id, link_id)
    return render_template('cadastro/enviado.html', nome=nome, login=login)
