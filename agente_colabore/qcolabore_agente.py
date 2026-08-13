# -*- coding: utf-8 -*-
"""Q-Colabore — agente da máquina do funcionário.

Roda continuamente em C:\\qcolabore. Vigia as pastas que o funcionário escolheu,
envia cada arquivo novo para a nuvem (POST /api/colabore/enviar, autenticado pela
chave dele no Bearer) e ORGANIZA o que enviou. Nunca apaga arquivo do usuário —
só MOVE para subpastas ("Enviados" / "Nao enviados").

Irmão do Q-Robô (que faz o mesmo por posto): burro na ponta, inteligência na
nuvem. A data de corte (o que é "velho demais para mandar") vem SEMPRE do
servidor (GET /api/colabore/config), nunca é decidida aqui.

Tecnologia: Python + Nuitka em modo PASTA (--standalone, ver build_qcolabore.ps1).
PyInstaller e o --onefile do Nuitka foram abandonados no Q-Robô por causa do
Defender (ambos descompactam numa pasta temporária a cada execução — o padrão que
o antivírus marca). A saída agora é uma pasta com o .exe e as peças ao lado,
distribuída em .zip. Dependências externas: ``requests`` (HTTP) e ``pystray`` +
``Pillow`` (ícone na bandeja); o resto é stdlib (tkinter, logging, winreg).

A CHAVE mora SÓ em C:\\qcolabore\\config.json — nunca em variável de ambiente,
registro do Windows, ou log. O início automático guarda no registro apenas o
CAMINHO do executável (HKCU\\...\\Run), jamais a chave.
"""
import datetime as _dt
import hashlib
import json
import logging
import re
import os
import shutil
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

import requests

try:
    import winreg                    # só existe no Windows (o agente é Windows-only)
except ImportError:                  # noqa: em outra plataforma o autostart vira no-op
    winreg = None

__version__ = "0.4.0"

# Assets embutidos no executável (Nuitka --include-data-file) — ver gerar_assets.py.
# Em produção ficam ao lado do .exe; em desenvolvimento, ao lado deste .py.
ASSET_LOGO = "qcolabore_logo.png"     # logo Qualicontax do cabeçalho
ASSET_ICON = "qcolabore_icon.png"     # "Q" verde: ícone da janela e da bandeja


def _recurso(nome):
    """Caminho de um asset embutido, ou None. Procura ao lado do executável
    (standalone) e ao lado deste módulo (desenvolvimento)."""
    for base in (os.path.dirname(os.path.abspath(sys.argv[0])),
                 os.path.dirname(os.path.abspath(__file__))):
        p = os.path.join(base, nome)
        if os.path.exists(p):
            return p
    return None

# ---------------------------------------------------------------------------
# Constantes — um só lugar para mudar.
# ---------------------------------------------------------------------------
SERVIDOR_PADRAO = "https://app.qualicontax.com.br"   # nuvem do Qualicontax
# Casa do agente: C:\qcolabore em produção. QCOLABORE_HOME só é usado pela prova
# automatizada, para não tocar na instalação real da máquina.
BASE_DIR = os.environ.get("QCOLABORE_HOME") or r"C:\qcolabore"
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "qcolabore.log")

# O agente NÃO MOVE MAIS NADA. O arquivo do funcionário fica exatamente onde
# ele salvou — a memória do que já foi enviado é o CADERNINHO (ver Registro).
#
# Estas duas continuam declaradas por um motivo: as instalações antigas moviam
# para cá, e agora que a varredura entra em subpastas o agente ENCONTRARIA tudo
# que já foi entregue e mandaria de novo. Elas são pulada na varredura.
SUBPASTA_OK = "Enviados"          # legado: só para IGNORAR na varredura
SUBPASTA_ERRO = "Nao enviados"    # cópia dos recusados (o original não sai do lugar)
SUBPASTAS = (SUBPASTA_OK, SUBPASTA_ERRO)
SUBPASTAS_IGNORADAS = {s.lower() for s in SUBPASTAS}

REGISTRO_PATH = os.path.join(BASE_DIR, "enviados.json")
# Teto do caderninho. Cada entrada tem ~140 bytes; 200 mil dá ~28 MB e cobre
# anos de uso. Passando disso, as mais antigas saem — e o pior que acontece é
# reenviar um arquivo velho, que o servidor devolve como duplicado.
REGISTRO_MAX = 200_000

INTERVALO_PADRAO = 60             # segundos entre varreduras (também é o heartbeat)
MARGEM_ESTABILIDADE = 5           # segundos: ignora arquivo mexido agora (meio-cópia)
PORTA_INSTANCIA = 52736           # trava de instância única (bind local)
BACKOFF_MAX = 300                 # teto do recuo exponencial em falha de rede (5 min)

# Timeout do POST: conexão curta, leitura longa (arquivo pode ter até 200 MB).
TIMEOUT_CONFIG = (10, 30)
TIMEOUT_ENVIO = (10, 300)

# Início automático: chave Run do PRÓPRIO usuário (HKCU) — NÃO exige administrador.
# Escolhida em vez do atalho em shell:startup porque é UM valor REG_SZ atômico:
# ligar/desligar é uma chamada só e idempotente, sem criar/apagar um .lnk nem
# depender do caminho (localizado) da pasta Inicializar. Guarda só o caminho do
# .exe — nunca a chave.
AUTOSTART_NOME = "QColabore"
AUTOSTART_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"

log = logging.getLogger("qcolabore")


# ===========================================================================
# Início automático com o Windows (HKCU\...\Run) — sem privilégio de admin
# ===========================================================================
def _comando_autostart():
    """Comando que o Windows executa no logon: o próprio executável. Compilado
    (Nuitka) -> o .exe; em desenvolvimento -> pythonw + este script."""
    compilado = "__compiled__" in globals() or getattr(sys, "frozen", False)
    if compilado:
        return '"%s"' % os.path.abspath(sys.argv[0])
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return '"%s" "%s"' % (pyw, os.path.abspath(sys.argv[0]))


def autostart_ativo():
    """True se a entrada Run 'QColabore' existe para este usuário."""
    if not winreg:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN) as k:
            valor, _ = winreg.QueryValueEx(k, AUTOSTART_NOME)
            return bool(valor)
    except OSError:
        return False


def autostart_definir(ativar):
    """Liga/desliga o início com o Windows (HKCU, sem admin). Devolve True se ok."""
    if not winreg:
        return False
    try:
        if ativar:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN) as k:
                winreg.SetValueEx(k, AUTOSTART_NOME, 0, winreg.REG_SZ,
                                  _comando_autostart())
            log.info("Inicio automatico LIGADO.")
        else:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN, 0,
                                winreg.KEY_SET_VALUE) as k:
                try:
                    winreg.DeleteValue(k, AUTOSTART_NOME)
                except FileNotFoundError:
                    pass
            log.info("Inicio automatico DESLIGADO.")
        return True
    except OSError as exc:
        log.warning("Falha ao %s o inicio automatico: %s",
                    "ligar" if ativar else "desligar", exc)
        return False


# ===========================================================================
# Configuração local (config.json)
# ===========================================================================
def carregar_config():
    """Lê o config.json. Devolve dict (vazio-ish) se não existir/ inválido."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        d = {}
    return {
        "servidor": (d.get("servidor") or SERVIDOR_PADRAO).rstrip("/"),
        "chave": d.get("chave") or "",
        "pastas": [p for p in (d.get("pastas") or []) if p],
        "intervalo_seg": int(d.get("intervalo_seg") or INTERVALO_PADRAO),
    }


def salvar_config(cfg):
    """Grava o config.json (só este arquivo guarda a chave)."""
    os.makedirs(BASE_DIR, exist_ok=True)
    dados = {
        "servidor": (cfg.get("servidor") or SERVIDOR_PADRAO).rstrip("/"),
        "chave": cfg.get("chave") or "",
        "pastas": list(cfg.get("pastas") or []),
        "intervalo_seg": int(cfg.get("intervalo_seg") or INTERVALO_PADRAO),
    }
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def config_completa(cfg):
    return bool(cfg.get("chave")) and bool(cfg.get("pastas"))


# ===========================================================================
# Estado partilhado worker <-> janela (thread-safe)
# ===========================================================================
class Estado:
    """O que a janela mostra. O worker escreve, a GUI lê — sob um lock."""

    def __init__(self):
        self._lock = threading.Lock()
        self.conexao = "iniciando"     # 'conectado' | 'sem_conexao' | 'chave_invalida' | 'iniciando'
        self.detalhe = ""
        self.enviados_hoje = 0
        self._dia = _dt.date.today()
        self.ultimo_nome = None
        self.ultimo_hora = None
        self.aguardando = 0            # arquivos em "Nao enviados"

    def _virar_dia_se_preciso(self):
        hoje = _dt.date.today()
        if hoje != self._dia:
            self._dia = hoje
            self.enviados_hoje = 0

    def set_conexao(self, estado, detalhe=""):
        with self._lock:
            self.conexao = estado
            self.detalhe = detalhe

    def marcar_enviado(self, nome):
        with self._lock:
            self._virar_dia_se_preciso()
            self.enviados_hoje += 1
            self.ultimo_nome = nome
            self.ultimo_hora = _dt.datetime.now().strftime("%H:%M")

    def set_aguardando(self, n):
        with self._lock:
            self.aguardando = int(n)

    def snapshot(self):
        with self._lock:
            self._virar_dia_se_preciso()
            return {
                "conexao": self.conexao, "detalhe": self.detalhe,
                "enviados_hoje": self.enviados_hoje,
                "ultimo_nome": self.ultimo_nome, "ultimo_hora": self.ultimo_hora,
                "aguardando": self.aguardando,
            }


# ===========================================================================
# Utilidades de arquivo — mover SEM apagar, sem sobrescrever
# ===========================================================================
# ===========================================================================
# CADERNINHO — o que já foi enviado
#
# Antes, a memória do agente era o MOVER: se o arquivo não estava mais na
# pasta, é porque tinha sido entregue. Isso obrigava a mexer no material do
# funcionário — e ele reclamava, com razão, que "o sistema sumiu com o
# arquivo". Agora nada se move e a memória mora aqui.
#
# A identidade é o SHA-256 do CONTEÚDO, não o caminho. Assim:
#   * renomear não faz reenviar;
#   * mover de subpasta não faz reenviar;
#   * copiar para outra pasta não faz reenviar;
#   * ALTERAR o arquivo faz reenviar — que é o certo, é outro documento.
#
# Hashear tudo a cada minuto seria caro numa pasta com 100 subpastas, então há
# um atalho: o trio (caminho, tamanho, mtime). Bateu, pula sem abrir o arquivo.
# Só quando o trio muda é que se lê o conteúdo para tirar o hash.
# ===========================================================================
class Registro:
    """Caderninho em JSON, na pasta do programa (não na do funcionário)."""

    def __init__(self, caminho=REGISTRO_PATH):
        self.caminho = caminho
        self.hashes = {}        # sha256 -> {"quando": iso, "estado": ok|recusado, "motivo": str}
        self.rapido = {}        # "caminho|tamanho|mtime" -> sha256
        self._lock = threading.Lock()
        self.carregar()

    # -- persistência ------------------------------------------------------
    def carregar(self):
        try:
            with open(self.caminho, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.hashes = d.get("hashes") or {}
            self.rapido = d.get("rapido") or {}
        except (OSError, ValueError):
            self.hashes, self.rapido = {}, {}

    def salvar(self):
        """Grava de forma atômica: escreve num .tmp e renomeia. Uma queda de
        energia no meio não deixa o caderninho pela metade — na pior hipótese
        ele fica com o conteúdo anterior."""
        try:
            os.makedirs(os.path.dirname(self.caminho), exist_ok=True)
            tmp = self.caminho + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"hashes": self.hashes, "rapido": self.rapido}, f)
            os.replace(tmp, self.caminho)
        except OSError as exc:
            log.warning("Nao consegui gravar o registro: %s", exc)

    # -- consulta ----------------------------------------------------------
    @staticmethod
    def _chave_rapida(caminho, st):
        return "%s|%d|%d" % (os.path.normcase(caminho), st.st_size, int(st.st_mtime))

    @staticmethod
    def hash_do_arquivo(caminho):
        """SHA-256 lendo em blocos — não carrega um .zip de 200 MB na memória."""
        h = hashlib.sha256()
        with open(caminho, "rb") as f:
            for bloco in iter(lambda: f.read(1024 * 1024), b""):
                h.update(bloco)
        return h.hexdigest()

    def ja_tratado(self, caminho, st):
        """(True, motivo) se este arquivo já foi enviado ou já foi recusado.

        Devolve também o hash, para quem for enviar não precisar ler de novo.
        """
        with self._lock:
            ch = self._chave_rapida(caminho, st)
            sha = self.rapido.get(ch)
            if sha and sha in self.hashes:
                return True, self.hashes[sha], sha
        try:
            sha = self.hash_do_arquivo(caminho)
        except OSError:
            return False, None, None
        with self._lock:
            reg = self.hashes.get(sha)
            if reg:
                # Mesmo conteúdo em caminho novo (renomeado/copiado/movido pelo
                # funcionário): aprende o atalho e NÃO reenvia.
                self.rapido[self._chave_rapida(caminho, st)] = sha
                return True, reg, sha
        return False, None, sha

    # -- escrita -----------------------------------------------------------
    def anotar(self, caminho, st, sha, estado, motivo=None):
        with self._lock:
            self.hashes[sha] = {"quando": _dt.datetime.now().isoformat(timespec="seconds"),
                                "estado": estado, "motivo": motivo,
                                "nome": os.path.basename(caminho)}
            self.rapido[self._chave_rapida(caminho, st)] = sha
            if len(self.hashes) > REGISTRO_MAX:
                # Poda pelas mais antigas. Reenviar um arquivo velho é
                # inofensivo (o servidor devolve duplicado); crescer sem limite
                # não é.
                mais_velhas = sorted(self.hashes.items(),
                                     key=lambda kv: kv[1].get("quando") or "")
                for k, _ in mais_velhas[: len(self.hashes) - REGISTRO_MAX]:
                    self.hashes.pop(k, None)
                vivos = set(self.hashes)
                self.rapido = {k: v for k, v in self.rapido.items() if v in vivos}
        self.salvar()

    def recusados(self):
        """Lista dos que o servidor recusou — é o que a janela mostra."""
        with self._lock:
            return [dict(v, sha=k) for k, v in self.hashes.items()
                    if v.get("estado") == "recusado"]


# Caracteres que o Windows recusa em nome de arquivo — o nome da cópia é montado
# a partir do caminho relativo, que pode trazer qualquer coisa.
_CHARS_PROIBIDOS_ARQ = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _nome_livre(destino_dir, nome):
    """Nome que não colide em destino_dir; 'x.pdf' -> 'x (1).pdf' ... Nunca
    devolve um nome já ocupado (não sobrescreve nada do usuário)."""
    alvo = os.path.join(destino_dir, nome)
    if not os.path.exists(alvo):
        return nome
    raiz, ext = os.path.splitext(nome)
    i = 1
    while True:
        cand = f"{raiz} ({i}){ext}"
        if not os.path.exists(os.path.join(destino_dir, cand)):
            return cand
        i += 1


def copiar_para_nao_enviados(raiz_vigiada, caminho, motivo):
    """COPIA (não move) o recusado para <raiz_vigiada>/Nao enviados/.

    O original NUNCA sai do lugar — é o ponto da mudança inteira. A cópia é só
    o aviso para quem não abre o programa.

    UMA pasta na raiz da pasta vigiada, e não uma por subpasta: com cem
    subpastas, uma "Nao enviados" em cada seria caçar aviso em cem lugares.

    O nome da cópia carrega DE ONDE veio ("158 - Auto Posto Fenix — nota.xml"),
    senão dois arquivos de mesmo nome vindos de subpastas diferentes se
    atropelariam aqui dentro.

    Devolve o caminho da cópia, ou None (falhar aqui não é motivo para o ciclo
    parar — o registro já sabe que foi recusado).
    """
    try:
        destino_dir = os.path.join(raiz_vigiada, SUBPASTA_ERRO)
        os.makedirs(destino_dir, exist_ok=True)

        rel = os.path.relpath(os.path.dirname(caminho), raiz_vigiada)
        nome = os.path.basename(caminho)
        if rel and rel != ".":
            prefixo = rel.replace(os.sep, " - ")
            nome = "%s — %s" % (prefixo, nome)
        nome = _CHARS_PROIBIDOS_ARQ.sub("_", nome)[:180]

        destino = os.path.join(destino_dir, _nome_livre(destino_dir, nome))
        shutil.copy2(caminho, destino)
        try:
            with open(destino + ".motivo.txt", "w", encoding="utf-8") as f:
                f.write(f"Arquivo original: {caminho}\r\n")
                f.write(f"Motivo: {motivo}\r\n")
                f.write(f"Quando: {_dt.datetime.now():%d/%m/%Y %H:%M:%S}\r\n")
                f.write("\r\nEsta e uma COPIA. O arquivo original continua onde voce\r\n"
                        "salvou e nao foi tocado.\r\n")
        except OSError:
            pass
        return destino
    except OSError as exc:
        log.warning("Falha ao copiar '%s' para '%s': %s",
                    os.path.basename(caminho), SUBPASTA_ERRO, exc)
        return None


def arquivos_para_olhar(pasta):
    """TODOS os arquivos da pasta e das SUBPASTAS, em qualquer profundidade.

    Antes só olhava o topo, e por isso a estrutura que o escritório usa de
    verdade — uma subpasta por empresa dentro da pasta vigiada — era ignorada
    inteira: o agente não capturava nada.

    Pula:
      * 'Enviados' e 'Nao enviados' — a primeira é legado das instalações
        antigas (reenviaria tudo o que já foi entregue) e a segunda é a nossa
        própria pasta de cópias (reenviaria o recusado para sempre);
      * '.motivo.txt' — bilhete nosso, não documento do funcionário;
      * arquivo mexido nos últimos MARGEM_ESTABILIDADE segundos, que pode
        estar sendo copiado ainda (pegaria meio arquivo).
    """
    saida = []
    agora = time.time()
    for dirpath, dirnames, filenames in os.walk(pasta):
        dirnames[:] = [d for d in dirnames if d.lower() not in SUBPASTAS_IGNORADAS]
        for n in filenames:
            if n.endswith(".motivo.txt"):
                continue
            cheio = os.path.join(dirpath, n)
            try:
                st = os.stat(cheio)
            except OSError:
                continue
            if not os.path.isfile(cheio):
                continue
            if agora - st.st_mtime < MARGEM_ESTABILIDADE:
                continue
            saida.append((cheio, st))
    return saida


# ===========================================================================
# Worker — o coração: varre, filtra por data, envia, organiza
# ===========================================================================
class ChaveInvalida(Exception):
    pass


def proxima_espera(atual, base, sem_conexao):
    """Recuo exponencial da espera entre ciclos. Em falha de rede/servidor, DOBRA
    a espera atual (nunca abaixo de ``base``) até o teto ``BACKOFF_MAX``; ao
    reconectar, volta para ``base``. Função pura para poder ser testada sozinha."""
    if sem_conexao:
        return min(max(atual * 2, base), BACKOFF_MAX)
    return base


def testar_conexao(servidor, chave):
    """Bate no GET /api/colabore/config com a chave dada. Devolve:
      (True,  nome_do_funcionario)  se a chave é válida;
      (False, motivo)               se inválida/revogada ou sem conexão.
    É o que o botão 'Testar conexão' usa para confirmar a chave na hora."""
    servidor = (servidor or SERVIDOR_PADRAO).rstrip("/")
    try:
        r = requests.get(servidor + "/api/colabore/config",
                        headers={"Authorization": "Bearer " + (chave or "").strip(),
                                 "User-Agent": "QColaboreAgente/%s" % __version__},
                        timeout=TIMEOUT_CONFIG)
        if r.status_code in (401, 403):
            return False, "Chave inválida ou revogada."
        r.raise_for_status()
        nome = (r.json() or {}).get("funcionario") or "(sem nome no cadastro)"
        return True, nome
    except requests.RequestException:
        return False, "Sem conexão com o servidor."


class Worker(threading.Thread):
    daemon = True

    def __init__(self, estado):
        super().__init__(name="qcolabore-worker")
        self.estado = estado
        self._parar = threading.Event()
        self._acordar = threading.Event()
        self.sessao = requests.Session()
        self._ignorados = {}              # caminho -> mtime já ignorado (não re-loga)
        # O caderninho substitui o antigo "mover para Enviados": é ele que
        # lembra o que já foi entregue, agora que nada sai do lugar.
        self.registro = Registro()

    def parar(self):
        self._parar.set()
        self._acordar.set()

    def cutucar(self):
        """Config mudou: acorda o loop agora (não espera o intervalo)."""
        self._ignorados.clear()
        self._acordar.set()

    # ---- HTTP ----
    def _headers(self, cfg):
        return {"Authorization": "Bearer " + cfg["chave"],
                "User-Agent": "QColaboreAgente/%s" % __version__}

    def _buscar_corte(self, cfg):
        """GET /api/colabore/config -> date de corte (ou None). Levanta
        ChaveInvalida em 401/403. Deixa outras exceções subirem (sem conexão)."""
        r = self.sessao.get(cfg["servidor"] + "/api/colabore/config",
                            headers=self._headers(cfg), timeout=TIMEOUT_CONFIG)
        if r.status_code in (401, 403):
            raise ChaveInvalida()
        r.raise_for_status()
        j = r.json()
        di = j.get("data_inicio_captura")
        corte = None
        if di:
            try:
                corte = _dt.date.fromisoformat(di[:10])
            except ValueError:
                corte = None
        return corte, bool(j.get("ativo", True))

    def _enviar(self, cfg, caminho):
        """POST /api/colabore/enviar. Devolve ('ok'|'rejeitado'|'retry', motivo).
        Levanta ChaveInvalida em 401/403 (para tudo lá em cima)."""
        nome = os.path.basename(caminho)
        with open(caminho, "rb") as f:
            r = self.sessao.post(
                cfg["servidor"] + "/api/colabore/enviar",
                headers=self._headers(cfg),
                files={"arquivo": (nome, f)},
                timeout=TIMEOUT_ENVIO)
        sc = r.status_code
        if sc in (200, 409):
            return "ok", sc
        if sc in (413, 415):
            try:
                status = (r.json() or {}).get("status", "")
            except ValueError:
                status = ""
            return "rejeitado", "HTTP %s %s" % (sc, status or "recusado")
        if sc in (401, 403):
            raise ChaveInvalida()
        return "retry", "HTTP %s" % sc          # 5xx e afins

    # ---- laço principal ----
    def run(self):
        """Nunca termina por erro. Em falha de rede/servidor, recua com intervalo
        crescente (dobra até BACKOFF_MAX) e volta ao normal quando reconecta. Só a
        chave inválida pausa o ENVIO — o programa segue aberto mostrando o aviso."""
        log.info("Agente iniciado (versao %s).", __version__)
        espera = INTERVALO_PADRAO
        while not self._parar.is_set():
            cfg = carregar_config()
            try:
                if not config_completa(cfg):
                    self.estado.set_conexao("chave_invalida",
                                            "Falta configurar (chave e pastas).")
                    status = "sem_config"
                else:
                    status = self._um_ciclo(cfg)
            except Exception as exc:            # blindagem final: o agente não morre
                log.exception("Erro inesperado no ciclo (o agente segue vivo): %s", exc)
                status = "sem_conexao"
            base = max(10, (cfg.get("intervalo_seg") or INTERVALO_PADRAO))
            espera = proxima_espera(espera, base, status == "sem_conexao")
            if status == "sem_conexao":
                log.info("Sem conexao — proxima tentativa em %ss.", espera)
            # espera, mas acorda na hora se cutucarem (config nova) ou pararem.
            self._acordar.wait(timeout=espera)
            self._acordar.clear()
        log.info("Agente encerrado.")

    def _um_ciclo(self, cfg):
        """Um passo. A consulta ao servidor (GET /config) é também o HEARTBEAT:
        o servidor atualiza ultimo_contato a cada chamada, então mesmo sem arquivo
        novo o escritório sabe que a máquina está viva (a cada intervalo, <= 15min).
        Devolve 'ok' | 'sem_conexao' | 'chave_invalida'. NUNCA levanta exceção."""
        try:
            corte, ativo = self._buscar_corte(cfg)      # <- heartbeat
        except ChaveInvalida:
            self.estado.set_conexao("chave_invalida",
                                    "A chave e invalida ou foi revogada. Abra Configurar.")
            log.warning("Chave invalida/revogada — envio pausado (programa segue aberto).")
            return "chave_invalida"
        except requests.RequestException as exc:
            self.estado.set_conexao("sem_conexao", "Sem conexao com o servidor.")
            log.info("Sem conexao ao consultar config: %s", exc.__class__.__name__)
            return "sem_conexao"

        self.estado.set_conexao("conectado", "" if ativo else "Chave desligada no servidor.")
        self.estado.set_aguardando(len(self.registro.recusados()))

        for pasta in cfg["pastas"]:
            if self._parar.is_set():
                return "ok"
            for caminho, st in arquivos_para_olhar(pasta):
                if self._parar.is_set():
                    return "ok"
                nome = os.path.basename(caminho)
                try:
                    # Filtro de data de corte — decidido pelo SERVIDOR, aplicado
                    # aqui. Vem ANTES do caderninho de propósito: numa pasta com
                    # anos de histórico, nem vale calcular hash do que é velho
                    # demais para mandar.
                    if corte and _dt.date.fromtimestamp(st.st_mtime) < corte:
                        if self._ignorados.get(caminho) != st.st_mtime:
                            self._ignorados[caminho] = st.st_mtime
                            log.info("Ignorado (anterior a %s): %s", corte, nome)
                        continue

                    # Já passou por aqui? O trio caminho+tamanho+data resolve
                    # sem abrir o arquivo; só quando ele muda é que se lê o
                    # conteúdo para tirar a impressão digital.
                    tratado, reg, sha = self.registro.ja_tratado(caminho, st)
                    if tratado:
                        continue
                    resultado, motivo = self._enviar(cfg, caminho)
                except ChaveInvalida:
                    self.estado.set_conexao("chave_invalida",
                                            "A chave e invalida ou foi revogada. Abra Configurar.")
                    log.warning("Chave invalida/revogada no envio — pausando (programa aberto).")
                    return "chave_invalida"
                except requests.RequestException as exc:
                    self.estado.set_conexao("sem_conexao", "Sem conexao — vai tentar de novo.")
                    log.info("Falha de rede ao enviar '%s': %s", nome, exc.__class__.__name__)
                    return "sem_conexao"
                except Exception as exc:         # erro num arquivo NÃO derruba o ciclo
                    log.exception("Erro ao processar '%s' (seguindo para o proximo): %s",
                                  nome, exc)
                    continue
                self._aplicar_resultado(pasta, caminho, st, sha, resultado, motivo)

        self.estado.set_aguardando(len(self.registro.recusados()))
        return "ok"

    def _aplicar_resultado(self, raiz_vigiada, caminho, st, sha, resultado, motivo):
        """O que fazer depois do POST. NADA é movido — o arquivo do funcionário
        fica onde ele salvou. O que muda é só o caderninho."""
        nome = os.path.basename(caminho)
        if resultado == "ok":
            self.registro.anotar(caminho, st, sha, "ok")
            self.estado.marcar_enviado(nome)
            log.info("Enviado: %s", caminho)

        elif resultado == "rejeitado":
            # Recusa definitiva (extensão fora da lista, arquivo grande demais):
            # anota para não tentar de novo e deixa uma CÓPIA de aviso. O
            # original continua onde está.
            self.registro.anotar(caminho, st, sha, "recusado", motivo)
            copiar_para_nao_enviados(raiz_vigiada, caminho, motivo)
            log.warning("Recusado (%s): %s — copia em '%s' (original intacto)",
                        motivo, caminho, SUBPASTA_ERRO)

        else:
            # 'retry': falha temporária. NÃO anota — para tentar de novo no
            # próximo ciclo. Anotar aqui faria o arquivo sumir para sempre por
            # causa de uma indisponibilidade de minutos.
            log.info("Adiado (%s): %s", motivo, nome)


# ===========================================================================
# Log local com rotação — NUNCA escreve a chave
# ===========================================================================
def montar_log():
    os.makedirs(BASE_DIR, exist_ok=True)
    log.setLevel(logging.INFO)
    if log.handlers:
        return
    h = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=5,
                            encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                     "%Y-%m-%d %H:%M:%S"))
    log.addHandler(h)


# ===========================================================================
# Trava de instância única — segundo .exe não briga com o primeiro
# ===========================================================================
def trava_instancia():
    """Bind num socket local; se falhar, já há um agente rodando. Mantém o socket
    vivo (retorna-o) enquanto o processo existir."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", PORTA_INSTANCIA))
        s.listen(1)
        return s
    except OSError:
        return None


# ===========================================================================
# GUI (tkinter) — janela de status + janela de configuração
# ===========================================================================
def rodar_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox

    # Bandeja: pystray + Pillow. Se faltarem (ex.: ambiente sem elas), o programa
    # não quebra — cai para minimizar na barra de tarefas.
    try:
        import pystray
        from PIL import Image
        tem_tray = True
    except Exception:
        tem_tray = False

    montar_log()
    estado = Estado()
    worker = Worker(estado)
    tray_icon = None

    VERDE = "#15803d"      # títulos das seções e status — verde da marca (como o Q-Robô)
    CINZA = "#64748b"

    cfg0 = carregar_config()

    root = tk.Tk()
    root.title("Q-Colabore %s — Configuração" % __version__)   # mesmo padrão do Q-Robô
    root.geometry("600x640")
    root.minsize(560, 600)
    root.configure(bg="white")

    # Ícone da JANELA (o "Q" verde embutido). Mantido em _refs p/ não ser coletado.
    _refs = []
    p_icone = _recurso(ASSET_ICON)
    if p_icone:
        try:
            ico = tk.PhotoImage(file=p_icone); _refs.append(ico)
            root.iconphoto(True, ico)
        except Exception:
            pass

    def _mostrar(*_a):
        root.after(0, lambda: (root.deiconify(), root.lift(), root.focus_force()))

    def _esconder(*_a):
        root.after(0, root.withdraw if tem_tray else root.iconify)

    def _sair(*_a):
        if tray_icon is not None:
            try:
                tray_icon.stop()
            except Exception:
                pass
        worker.parar()
        root.after(0, root.destroy)

    def _titulo(txt):
        tk.Label(root, text=txt, font=("Segoe UI", 10, "bold"), fg=VERDE, bg="white",
                 anchor="w").pack(fill="x", padx=16, pady=(12, 2))

    # ===== Cabeçalho: logo + "Q-Colabore" + versão (igual ao Q-Robô) =====
    top = tk.Frame(root, bg="white"); top.pack(fill="x", padx=16, pady=(14, 4))
    p_logo = _recurso(ASSET_LOGO)
    if p_logo:
        try:
            logo = tk.PhotoImage(file=p_logo); _refs.append(logo)
            tk.Label(top, image=logo, bg="white").pack(side="left")
        except Exception:
            pass
    tk.Label(top, text="Q-Colabore", font=("Segoe UI", 18, "bold"), bg="white"
             ).pack(side="left", padx=(10, 6))
    tk.Label(top, text=__version__, font=("Segoe UI", 11), fg=CINZA, bg="white"
             ).pack(side="left", pady=(8, 0))

    # ===== 1. Chave de ativação → campo + "Testar conexão" =====
    _titulo("1. Chave de ativação")
    f1 = tk.Frame(root, bg="white"); f1.pack(fill="x", padx=16)
    chave_var = tk.StringVar(value=cfg0["chave"])
    ent_chave = tk.Entry(f1, textvariable=chave_var, show="•")
    ent_chave.pack(side="left", fill="x", expand=True, ipady=3)
    tk.Button(f1, text="Testar conexão", command=lambda: _testar()).pack(side="left", padx=(8, 0))
    lbl_teste = tk.Label(root, text="", font=("Segoe UI", 9), bg="white", anchor="w",
                         justify="left", wraplength=560)
    lbl_teste.pack(fill="x", padx=16, pady=(3, 0))

    # ===== 2. Pastas monitoradas → lista + Adicionar…/Remover =====
    _titulo("2. Pastas monitoradas")
    f2 = tk.Frame(root, bg="white"); f2.pack(fill="both", expand=True, padx=16)
    lista = tk.Listbox(f2, height=6)
    lista.pack(side="left", fill="both", expand=True)
    for p in cfg0["pastas"]:
        lista.insert("end", p)
    b2 = tk.Frame(f2, bg="white"); b2.pack(side="left", fill="y", padx=8)
    def _add():
        d = filedialog.askdirectory(title="Escolha uma pasta para vigiar")
        if d and d not in lista.get(0, "end"):
            lista.insert("end", os.path.normpath(d))
    def _rem():
        for i in reversed(lista.curselection()):
            lista.delete(i)
    tk.Button(b2, text="Adicionar…", width=12, command=_add).pack(pady=(0, 4))
    tk.Button(b2, text="Remover", width=12, command=_rem).pack()

    # ===== 3. Ativar → botão que salva e liga (+ iniciar com o Windows) =====
    _titulo("3. Ativar")
    f3 = tk.Frame(root, bg="white"); f3.pack(fill="x", padx=16)
    tk.Button(f3, text="Ativar", width=12, command=lambda: _ativar()).pack(side="left")
    iniciar_var = tk.BooleanVar(value=(autostart_ativo() if config_completa(cfg0) else True))
    tk.Checkbutton(f3, text="Iniciar junto com o Windows", variable=iniciar_var,
                   bg="white", font=("Segoe UI", 9)).pack(side="left", padx=12)

    # ===== Status (quadro embaixo) =====
    _titulo("Status")
    quad = tk.Frame(root, bg="#f8fdfa", highlightbackground="#e5e7eb", highlightthickness=1)
    quad.pack(fill="x", padx=16, pady=(2, 14))
    lbl_status = tk.Label(quad, text="", font=("Segoe UI", 9), fg=VERDE, bg="#f8fdfa",
                          justify="left", anchor="w")
    lbl_status.pack(fill="x", padx=12, pady=10)

    # ---- ação: Testar conexão (mostra o NOME do funcionário, como o Q-Robô a razão social) ----
    def _testar():
        chave = chave_var.get().strip()
        if not chave:
            lbl_teste.config(text="Cole a chave primeiro.", fg="#b45309")
            return
        lbl_teste.config(text="Testando…", fg=CINZA)

        def _bg():
            ok, msg = testar_conexao(cfg0["servidor"], chave)
            def _mostra():
                if ok:
                    lbl_teste.config(text="✓ Chave válida — funcionário: %s" % msg, fg=VERDE)
                else:
                    lbl_teste.config(text="✗ %s" % msg, fg="#b91c1c")
            root.after(0, _mostra)
        threading.Thread(target=_bg, daemon=True).start()

    # ---- ação: Ativar (salva + liga o início automático + trabalha na bandeja) ----
    def _ativar():
        chave = chave_var.get().strip()
        pastas = list(lista.get(0, "end"))
        if not chave:
            messagebox.showwarning("Falta a chave", "Cole a chave de ativação.", parent=root)
            return
        if not pastas:
            messagebox.showwarning("Falta pasta", "Adicione ao menos uma pasta.", parent=root)
            return
        salvar_config({"servidor": cfg0["servidor"], "chave": chave, "pastas": pastas,
                       "intervalo_seg": cfg0["intervalo_seg"]})
        autostart_definir(iniciar_var.get())
        log.info("Configuracao salva (%d pasta(s)).", len(pastas))   # nunca a chave
        worker.cutucar() if worker.is_alive() else worker.start()
        _esconder()

    ROT = {"conectado": "Conectado", "sem_conexao": "Sem conexão",
           "chave_invalida": "Atenção — verifique a chave", "iniciando": "Iniciando…"}

    def tick():
        s = estado.snapshot()
        aguard = s["aguardando"]
        ult = ("%s às %s" % (s["ultimo_nome"], s["ultimo_hora"])) if s["ultimo_nome"] else "—"
        linhas = ["Conexão: %s" % ROT.get(s["conexao"], s["conexao"])]
        if s["detalhe"]:
            linhas.append("   %s" % s["detalhe"])
        linhas += [
            "Enviados hoje: %d" % s["enviados_hoje"],
            "Último enviado: %s" % ult,
            'Em "Não enviados": %d%s' % (aguard, "  — precisa de atenção" if aguard else ""),
            "Início automático: %s" % ("ativo" if autostart_ativo() else "desligado"),
            "Versão do programa: %s" % __version__,
        ]
        lbl_status.config(text="\n".join(linhas))
        root.after(1500, tick)

    # Fechar no X minimiza para a bandeja (não encerra) — sair só pelo menu da bandeja.
    root.protocol("WM_DELETE_WINDOW", _esconder)

    # ===== Bandeja (ao lado do relógio) — mesmo "Q" verde da janela =====
    if tem_tray:
        def _img_tray():
            p = _recurso(ASSET_ICON)
            if p:
                try:
                    return Image.open(p)
                except Exception:
                    pass
            return Image.new("RGBA", (64, 64), (46, 158, 46, 255))
        menu = pystray.Menu(
            pystray.MenuItem("Abrir", lambda i, it: _mostrar(), default=True),
            pystray.MenuItem("Configurar…", lambda i, it: _mostrar()),
            pystray.MenuItem("Sair", lambda i, it: _sair()),
        )
        tray_icon = pystray.Icon("qcolabore", _img_tray(), "Q-Colabore", menu)
        threading.Thread(target=tray_icon.run, daemon=True).start()

    # O worker roda sempre (idle-a se faltar config). Se já está configurado, a
    # janela sobe escondida na bandeja; senão, fica visível para configurar.
    worker.start()
    if config_completa(cfg0):
        root.after(400, _esconder)

    tick()
    root.mainloop()
    worker.parar()
    if tray_icon is not None:
        try:
            tray_icon.stop()
        except Exception:
            pass


def main():
    trava = trava_instancia()
    if trava is None:
        # já há um agente rodando; não abre um segundo.
        try:
            import tkinter.messagebox as mb
            import tkinter as tk
            r = tk.Tk(); r.withdraw()
            mb.showinfo("Q-Colabore", "O agente já está em execução.")
            r.destroy()
        except Exception:
            pass
        return 0
    try:
        rodar_gui()
    finally:
        try:
            trava.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
