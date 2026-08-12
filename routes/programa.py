# -*- coding: utf-8 -*-
"""Download PÚBLICO do instalador do Q-Colabore por link de uso único.

Para quem NÃO tem acesso ao sistema: o Anderson gera o link em
Configurações → Usuários e manda por WhatsApp. A pessoa abre, baixa, e pronto.
Ela nunca vê o Dropbox e nunca precisa de login.

Blueprint SEPARADO, e não uma rota sem @login_required dentro de
``configuracoes``, para o escopo ficar óbvio na leitura: tudo que mora aqui é
público por desenho. Uma rota pública escondida no meio de um módulo
administrativo é o tipo de coisa que passa despercebida numa revisão.

O QUE ESTE LINK NÃO DÁ
----------------------
Entrega SÓ o instalador. Não abre tela nenhuma, não cria sessão, não gera chave
e não vale como login. Sem a chave — que só o admin gera, por funcionário — o
agente não envia nada. Por isso o pacote em si não é segredo, e o link curto de
uso único é proteção suficiente contra ele circular à toa.
"""
import logging
from io import BytesIO

from flask import Blueprint, render_template, request, send_file

from utils import cadastro_token, qcolabore_instalador

logger = logging.getLogger(__name__)

programa = Blueprint('programa', __name__, url_prefix='/programa')

TIPO = 'PROGRAMA'

# Espelham as constantes da rota que GERA o link (routes/configuracoes). A
# tolerância é a única que importa aqui: sem ela, uma queda de conexão no meio
# dos 26 MB deixaria a pessoa a pé.
TOLERANCIA_MIN = 15

_MOTIVOS = {
    cadastro_token.EXPIRADO: ('Link expirado',
                              'Este link valia por poucas horas e já passou do prazo. '
                              'Peça um novo ao escritório.'),
    cadastro_token.USADO: ('Link já utilizado',
                           'Este link valia por um download e já foi usado. '
                           'Peça um novo ao escritório.'),
    cadastro_token.REVOGADO: ('Link cancelado',
                              'Este link foi cancelado pelo escritório.'),
    cadastro_token.INEXISTENTE: ('Link inválido',
                                 'Confira se o endereço foi copiado inteiro.'),
    cadastro_token.TIPO_ERRADO: ('Link inválido',
                                 'Este link não serve para baixar o programa.'),
}


def _recusa(motivo):
    titulo, texto = _MOTIVOS.get(
        motivo, ('Link inválido', 'Não foi possível usar este link.'))
    logger.info('[programa] download recusado: %s', motivo)
    return render_template('programa/recusado.html',
                           titulo=titulo, texto=texto), 410


@programa.route('/<token>', methods=['GET'])
def baixar(token):
    """Consome o link e entrega o .zip do agente.

    O consumo vem ANTES da leitura do Dropbox de propósito? Não — ao contrário:
    o arquivo é lido PRIMEIRO. Se o Dropbox falhar depois de o link ter sido
    queimado, a pessoa ficaria sem o programa e sem o link. Só se há bytes em
    mãos é que o token é gasto.
    """
    m = qcolabore_instalador.manifesto()
    if not m.get('ok'):
        logger.warning('[programa] instalador indisponivel: %s', m.get('erro'))
        return render_template(
            'programa/recusado.html', titulo='Programa indisponível',
            texto='O escritório precisa publicar o instalador. '
                  'Avise quem mandou o link.'), 503

    # Inspeciona sem gastar: se o link já não presta, nem baixamos os 26 MB.
    est = cadastro_token.inspecionar(token, TIPO)
    if not est['ok']:
        return _recusa(est['motivo'])

    dados = qcolabore_instalador.baixar(m['caminho'])
    if not dados:
        logger.warning('[programa] falha ao ler o pacote — link NAO consumido.')
        return render_template(
            'programa/recusado.html', titulo='Falha ao preparar o download',
            texto='Tente de novo em alguns instantes. Seu link continua válido.'), 503

    res = cadastro_token.consumir_tolerante(
        token, TIPO, tolerancia_min=TOLERANCIA_MIN,
        ip=(request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
            or request.remote_addr))
    if not res['ok']:
        return _recusa(res['motivo'])

    logger.info('[programa] instalador %s entregue (link %s…, retomada=%s).',
                m.get('rotulo'), token[:8], bool(res.get('retomada')))
    return send_file(BytesIO(dados), as_attachment=True,
                     download_name=m['nome_download'],
                     mimetype='application/octet-stream')
