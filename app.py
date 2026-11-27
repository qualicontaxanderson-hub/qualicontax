"""Aplicação principal Flask - Qualicontax"""
import os
import sys
import time
import logging

# ---------------------------------------------------------------------------
# Logging — PRIMEIRA COISA do boot, antes de qualquer import que já emita log.
# O gunicorn configura apenas os loggers 'gunicorn.*' (com propagate=False);
# o root fica sem handler e TODO logger.info/warning da aplicação era descartado
# silenciosamente. force=True garante que esta config vença independente da
# ordem de import (não mexe nos handlers do gunicorn, que são loggers nomeados).
# Nível ajustável em runtime pela env LOG_LEVEL (default INFO).
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO),
    stream=sys.stdout,
    format='%(asctime)s %(levelname)s [pid=%(process)d] %(name)s: %(message)s',
    force=True,
)
try:
    # Railway captura stdout; sem line-buffering os logs de boot podem ficar
    # presos no buffer e só aparecerem muito depois (ou nunca, se o worker morrer).
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from flask import Flask, render_template, jsonify, url_for as flask_url_for
from flask_login import LoginManager
from flask_compress import Compress
from config import Config
from models.usuario import Usuario

_startup_time = time.time()
logger = logging.getLogger(__name__)


def _versao_em_execucao() -> str:
    """Etiqueta curta do commit que está rodando AGORA.

    Existe para tornar um deploy verificável de fora, sem login: o /health
    já dizia que a aplicação tinha reiniciado, mas não QUAL versão subiu, e
    dois deploys seguidos ficavam indistinguíveis. Com a etiqueta, basta
    comparar com o commit que se acabou de enviar.

    O Railway entrega o SHA em variável de ambiente. Fora dele (máquina do
    desenvolvedor) pergunta ao próprio git. Se as duas falharem devolve
    'desconhecida' — informação de diagnóstico nunca derruba a aplicação.
    """
    sha = (os.environ.get('RAILWAY_GIT_COMMIT_SHA')
           or os.environ.get('GIT_COMMIT_SHA') or '').strip()
    if not sha:
        try:
            import subprocess
            sha = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:                                     # noqa: BLE001
            sha = ''
    return sha[:7] if sha else 'desconhecida'


# Calculado UMA vez, na subida: o /health é consultado com frequência pelo
# monitor do Railway e não pode disparar um subprocesso a cada chamada.
_VERSAO = _versao_em_execucao()

_INSECURE_KEY = 'dev-secret-key-change-in-production'
if Config.FLASK_ENV == 'production' and Config.SECRET_KEY == _INSECURE_KEY:
    raise RuntimeError(
        'SECRET_KEY não configurada. '
        'Defina a variável de ambiente SECRET_KEY no Railway antes de iniciar em produção.'
    )

# Inicializa Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Compressão gzip de respostas (reduz tráfego ~70% para HTML/CSS/JSON)
compress = Compress()
compress.init_app(app)

# Inicializa Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Por favor, faça login para acessar esta página.'
login_manager.login_message_category = 'warning'


@login_manager.user_loader
def load_user(user_id):
    """Carrega usuário para Flask-Login.

    Conta com senha PENDENTE (nunca definida, ou redefinição com bloqueio) não
    sustenta sessão: devolver None faz o current_user cair já na PRÓXIMA
    requisição — quem estava logado é derrubado na hora que o admin bloqueia, e
    cai no /login (onde a rota mostra 'falta definir a senha'). É o que impede a
    pessoa de continuar navegando após um bloqueio.
    """
    u = Usuario.get_by_id(int(user_id))
    if u and u.senha_pendente:
        return None
    # 20/11/2026 — "Ver como": o admin escolhe uma pessoa e o sistema inteiro fica como ela (utils/ver_como.py)
    from utils.ver_como import carregar as _ver_como
    return _ver_como(u)


# Registra Blueprints
from routes.auth import auth
from routes.dashboard import dashboard
from routes.clientes import clientes
from routes.grupos import grupos
from routes.contabilidades import contabilidades
from routes.ramos_atividade import ramos_atividade
from routes.contratos import contratos
from routes.processos import processos
from routes.relatorios import relatorios
from routes.documentos import documentos
from routes.api import api
from routes.contabil import contabil
import routes.contabil_irpf  # noqa: F401  — rotas do Imposto de Renda PF no blueprint 'contabil'
from routes.municipios import municipios
from routes.financeiro import financeiro
from routes.dropbox import dropbox_bp
from routes.modulos import modulos
from routes.escrita_fiscal import escrita_fiscal as escrita_fiscal_bp
from routes.configuracoes import configuracoes as configuracoes_bp
from routes.adicionais import adicionais
from routes.dfe import dfe_bp
from routes.robo_saidas import robo_saidas
# Q-Colabore F2: API de MÁQUINA (Bearer) do agente do funcionário -> _ENTRADA.
# Sem login_required — a credencial é a chave por funcionário, como o robo_saidas.
from routes.colabore_api import colabore_api
from routes.qrobo import qrobo, gate_escopo_instalador
# Q-Colabore: formulário PÚBLICO de cadastro (/cadastro/<token>). Sem
# login_required — quem chega não tem conta; a credencial é o token da URL.
from routes.cadastro import cadastro_bp
# Q-Colabore Parte 6: definição PÚBLICA de senha (/senha/<token>). Também sem
# login_required — a credencial é o token; destrava a conta senha_pendente.
from routes.senha import senha_bp
# Download público do instalador do Q-Colabore por link de uso único.
from routes.programa import programa as programa_bp
# Manuais de instalação servidos como página (públicos, sem login).
from routes.manuais import manuais as manuais_bp
# Vincular pagamento de outra conta aos títulos (14/11/2026) — rotas no blueprint financeiro; antes do register.
from routes import financeiro_vincular  # noqa: E402,F401
# Acordos de dívida (26/11/2026) — Cobranças › Acordos; rotas no blueprint financeiro.
from routes import financeiro_acordos  # noqa: E402,F401
# Ligar a cobrança (Bradesco) dentro do cartão da conta bancária (15/11/2026).
from routes import financeiro_ligar_banco  # noqa: E402,F401

app.register_blueprint(auth)
app.register_blueprint(dashboard)
app.register_blueprint(clientes)
app.register_blueprint(grupos)
app.register_blueprint(ramos_atividade)
app.register_blueprint(contratos)
app.register_blueprint(processos)
app.register_blueprint(relatorios)
app.register_blueprint(documentos)
app.register_blueprint(api)
app.register_blueprint(contabil)
app.register_blueprint(municipios)
app.register_blueprint(financeiro)
app.register_blueprint(dropbox_bp)
app.register_blueprint(modulos)
import routes.comercial_propostas   # noqa: E402,F401  (PROPOSTAS DE SERVIÇOS: rotas nos blueprints comercial e proposta, antes do registro; 25/11/2026)
from routes.comercial_orcamentos import comercial as comercial_bp   # noqa: E402  (orçamentos do Comercial; 21/11/2026)
app.register_blueprint(comercial_bp)
from routes.proposta_publica import proposta as proposta_bp   # noqa: E402  (o cliente aceita o orçamento pelo link; 21/11/2026)
app.register_blueprint(proposta_bp)
app.register_blueprint(escrita_fiscal_bp)
app.register_blueprint(configuracoes_bp)
app.register_blueprint(adicionais)
app.register_blueprint(contabilidades)
app.register_blueprint(dfe_bp)
from routes.financeiro_cobranca import cobranca_webhook, cobranca_publica   # noqa: E402  (aviso de pagamento da Efí; links do e-mail de cobrança)
app.register_blueprint(cobranca_webhook)
app.register_blueprint(cobranca_publica)
from routes.financeiro_contratos import assinatura   # noqa: E402  (assinatura eletrônica do contrato de prestação, por token; 06/11/2026)
app.register_blueprint(assinatura)
from routes.acordo_publico import acordo_assinatura   # noqa: E402  (assinatura do termo de acordo de dívida, por token; 26/11/2026)
app.register_blueprint(acordo_assinatura)
from routes.email_verifica import verifica   # noqa: E402  ("Este e-mail é da Qualicontax?" — /v/<código> de todo e-mail a cliente; 27/11/2026)
app.register_blueprint(verifica)
app.register_blueprint(robo_saidas)
app.register_blueprint(colabore_api)
app.register_blueprint(cadastro_bp)
app.register_blueprint(senha_bp)
app.register_blueprint(programa_bp)
app.register_blueprint(manuais_bp)
app.register_blueprint(qrobo)
from routes.atendimento import atendimento as atendimento_bp   # noqa: E402  (chamados da equipe ao suporte; 15/11/2026)
app.register_blueprint(atendimento_bp)
from routes.novidades import novidades as novidades_bp   # noqa: E402  (o que mudou no sistema; 15/11/2026)
app.register_blueprint(novidades_bp)
from routes.meu_cadastro import meu_cadastro as meu_cadastro_bp   # noqa: E402  (cada um altera os próprios dados; 17/11/2026)
app.register_blueprint(meu_cadastro_bp)
from routes.ver_como import ver_como as ver_como_bp   # noqa: E402  (admin vê o sistema como a pessoa; 20/11/2026)
app.register_blueprint(ver_como_bp)

# Gate do Portal do Instalador — DENY BY DEFAULT para a sessão de escopo
# restrito. Registrado no APP (não no blueprint) de propósito: precisa rodar
# antes de TODO request, senão bastaria digitar /escrita-fiscal/... na barra
# para escapar. Sessão sem o escopo não é tocada — o app segue igual para todos.
# Login seguro (10/11/2026): sessão parada há mais de 24 h cai (e a volta pede
# código no e-mail); anota último acesso e IP. Roda ANTES dos portões.
from utils.login_seguro import vigia_sessao as _vigia_sessao, renovar_depois as _renovar_navegador
app.before_request(_vigia_sessao)
app.after_request(_renovar_navegador)
app.before_request(gate_escopo_instalador)
# Ver como (20/11/2026): admin vendo como outra pessoa só olha — nada se grava fora de Lançamentos.
from utils.ver_como import trava_escrita as _ver_como_trava, contexto as _ver_como_ctx
app.before_request(_ver_como_trava)
app.context_processor(_ver_como_ctx)

# Portão central de PERFIL e EMPRESA RESERVADA — também DENY BY DEFAULT: rota
# que não está classificada em utils/acesso.py é só admin. Roda depois do gate
# do instalador (aquele é mais restrito e responde primeiro). 02/10/2026: havia
# 148 rotas que só conferiam login; o menu escondia, a URL digitada abria.
from utils.acesso import portao as _portao_acesso, pode_rota as _pode_rota
app.before_request(_portao_acesso)
# Nos templates: {% if pode_rota('clientes.editar') %} — a mesma regra do portão.
app.jinja_env.globals['pode_rota'] = _pode_rota
# Segmentos do ramo de atividade (10/10/2026): uma lista só para cadastro,
# tela de ramos e filtro da lista de clientes.
from models.ramo_atividade import SEGMENTOS as _SEGMENTOS_RAMO
app.jinja_env.globals['SEGMENTOS_RAMO'] = _SEGMENTOS_RAMO
# Logo do banco (15/11/2026): {{ logo_banco(banco_id, cor) }} em toda tela que mostra banco;
# banco sem arquivo em static/images/bancos fica com o quadradinho na cor da marca.
from utils.bancos_logo import logo_banco as _logo_banco, logos_banco as _logos_banco, url as _logo_banco_url
app.jinja_env.globals['logo_banco'] = _logo_banco
app.jinja_env.globals['logo_banco_url'] = _logo_banco_url
app.jinja_env.globals['logos_banco'] = _logos_banco
# Número do boleto para gente ler (18/11/2026): {{ numero_boleto(b) }} — Efí/Bradesco o nº do banco; Cora o Nr. do Documento do PDF
from models.fin_cobranca import FinBoleto as _FinBoleto
app.jinja_env.globals['numero_boleto'] = _FinBoleto.numero
# Motivo da recusa da nota em português e com o que fazer (18/11/2026): {{ motivo_nfse(n.erro_ultimo) }}
from models.nfse_emissao import motivo_nfse as _motivo_nfse
app.jinja_env.globals['motivo_nfse'] = _motivo_nfse


# Template filters
from utils.formatters import format_cpf, format_cnpj, format_phone, format_currency, format_date

app.jinja_env.filters['format_cpf'] = format_cpf
app.jinja_env.filters['format_cnpj'] = format_cnpj
app.jinja_env.filters['format_phone'] = format_phone
app.jinja_env.filters['format_currency'] = format_currency
app.jinja_env.filters['format_date'] = format_date
from utils.texto import nome_proprio
app.jinja_env.filters['nome_proprio'] = nome_proprio   # 'RUA JAIME TAVEIRA' -> 'Rua Jaime Taveira' (so texto em caixa alta)

# E1: campo vazio renderiza VAZIO, nunca a palavra "None". O Jinja imprime o
# None do Python como str(None)=="None" quando ele vem de uma expressão (ex.:
# {{ cliente.observacoes if cliente else '' }} com valor NULL) — isto era
# reenviado no form e virava a string "None" no banco. finalize corta na raiz,
# para TODOS os templates de uma vez.
app.jinja_env.finalize = lambda v: '' if v is None else v


@app.after_request
def _marcar_consulta_cortada(resposta):
    """Toda resposta JSON diz se ALGUMA consulta dela foi cortada pelo teto.

    Por que aqui e não em cada rota: ``execute_query`` devolve None quando o
    teto de 30s corta, e o padrão ``or []`` — presente em 12 lugares — vira
    "não há nada". Com 161 rotas JSON só no escrita_fiscal, corrigir uma a uma
    seria consertar o sintoma e deixar a próxima nascer igual. Aqui a resposta
    carrega o aviso SEMPRE, sem a rota precisar lembrar.

    Nasceu de 19/09/2026: a Conferência de Entradas mostrou "49 notas" nos
    cartões (o agregado sobreviveu) e "Nenhuma nota encontrada" na tabela (a
    consulta das linhas foi cortada). O db_helper sabia, tentou avisar por
    flash, e o flash não tem para onde ir num fetch de JSON.

    Não derruba a resposta: se o corpo não for um objeto JSON, sai como está.
    """
    try:
        from utils.db_helper import consulta_foi_cortada
        if not consulta_foi_cortada():
            return resposta
        if not (resposta.content_type or '').startswith('application/json'):
            return resposta
        if resposta.direct_passthrough:       # streaming: não dá para reescrever
            return resposta
        import json as _json
        corpo = _json.loads(resposta.get_data(as_text=True))
        if isinstance(corpo, dict):
            corpo['consulta_cortada'] = True
            corpo.setdefault(
                'consulta_cortada_msg',
                'A consulta passou do tempo limite e foi interrompida. '
                'O que aparece pode estar incompleto — tente de novo ou '
                'aperte o período.')
            resposta.set_data(_json.dumps(corpo))
    except Exception:
        logger.exception('[db] não consegui marcar a resposta como cortada; '
                         'a resposta segue intacta.')
    return resposta


# Cache de arquivos estáticos — 1 ano em produção para CSS/JS/imagens.
# Só é seguro por causa do cache-busting logo abaixo: a URL carrega a versão do
# arquivo, então "1 ano" vale para o conteúdo daquela versão, não para o caminho.
if not Config.DEBUG:
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 365 * 24 * 60 * 60  # 1 ano em segundos


# ---------------------------------------------------------------------------
# CACHE-BUSTING DOS ESTÁTICOS
#
# Por que existe: os estáticos são servidos com max-age de 1 ano e o navegador
# NÃO revalida nesse período. Sem versão na URL, um deploy que muda CSS/JS não
# chega em quem já visitou o site — foi assim que a home do fiscal quebrou
# depois que o CSS dela saiu do template para o identidade.css (revert 7d919b5).
#
# Como funciona: sobrescreve o url_for QUE OS TEMPLATES ENXERGAM. Toda chamada
# url_for('static', filename=...) ganha ?v=<mtime do arquivo>, no sistema
# inteiro de uma vez — não há nada para lembrar de atualizar arquivo por
# arquivo. Arquivo mudou → mtime muda → URL muda → o navegador é obrigado a
# baixar. Arquivo igual → URL igual → cache de 1 ano aproveitado por inteiro.
#
# O valor NÃO é escrito à mão de propósito: número manual é exatamente o que se
# esquece de atualizar, e aí o problema volta calado.
# ---------------------------------------------------------------------------
def _versao_estatico(filename):
    """mtime do arquivo em static/, como string. None se o arquivo não existir."""
    try:
        caminho = os.path.join(app.static_folder, filename)
        return str(int(os.stat(caminho).st_mtime))
    except (OSError, TypeError, ValueError):
        # Arquivo ausente ou nome estranho: devolve a URL sem ?v= em vez de
        # quebrar a página. Perde-se o cache-busting daquele arquivo, só isso.
        return None


def url_for_versionado(endpoint, **values):
    if endpoint == 'static' and 'v' not in values:
        versao = _versao_estatico(values.get('filename'))
        if versao:
            values['v'] = versao
    return flask_url_for(endpoint, **values)


@app.context_processor
def _injeta_url_for_versionado():
    """Faz os templates usarem a versão com ?v= no lugar do url_for do Flask."""
    return {'url_for': url_for_versionado}


# ---------------------------------------------------------------------------
# Health check — sem autenticação, usado pelo Railway para zero-downtime deploy.
# Verifica conectividade com o banco antes de sinalizar "pronto".
# ---------------------------------------------------------------------------
@app.route('/health')
def health():
    from utils.db_helper import execute_query
    try:
        execute_query("SELECT 1", fetch=True)
        db_ok = True
        db_msg = 'ok'
    except Exception as exc:
        db_ok = False
        db_msg = str(exc)[:120]
        logger.warning('Health check: DB indisponível — %s', db_msg)

    uptime = round(time.time() - _startup_time, 1)
    status = 'ok' if db_ok else 'degraded'
    code   = 200  if db_ok else 503
    return jsonify({'status': status, 'db': db_msg, 'uptime_s': uptime,
                    'versao': _VERSAO}), code


# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    """Página de erro 404"""
    return render_template('errors/404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    """Página de erro 500"""
    return render_template('errors/500.html'), 500


# Cria diretórios necessários
os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join('static', 'uploads'), exist_ok=True)

# Executa as migrations do banco de dados uma única vez por processo.
# Em ambiente Gunicorn com --preload, roda apenas no processo mestre antes
# de fazer fork dos workers. Sem --preload, cada worker executa uma vez
# ao iniciar, mas a variável de ambiente evita re-execução dentro do mesmo processo.
if os.environ.get('MIGRATIONS_DONE') != '1':
    try:
        from init_db import run_migrations as _run_migrations
        _run_migrations()
        os.environ['MIGRATIONS_DONE'] = '1'
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).exception(
            'Falha FATAL nas migrations — abortando o boot (schema incompleto).')
        # Re-levanta: melhor o worker morrer e o deploy falhar RUIDOSAMENTE do que
        # subir "com sucesso" com schema incompleto (lição da Fase 1).
        raise


# Inicia o scheduler de tarefas agendadas.
#
# Roda in-process no worker do gunicorn que adquire o file-lock. Cobre o job
# NOTURNO (importação automática 1x/dia). A captura de DFe (*/20) NÃO depende mais
# deste scheduler: ela é disparada pelo Railway Cron (cron_captura_dfe.py). Por
# isso o job de DFe in-process fica DESLIGADO no serviço web via DFE_SCHED_ATIVO=0
# (evita disparo duplo com o Cron).
try:
    from utils.scheduler import init_scheduler
    init_scheduler(app)
except Exception:
    import logging as _logging
    _logging.getLogger(__name__).exception('Falha ao iniciar o scheduler.')


if __name__ == '__main__':
    app.run(debug=Config.DEBUG, host='0.0.0.0', port=5000)
