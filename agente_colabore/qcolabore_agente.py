# -*- coding: utf-8 -*-
"""Q-Colabore — agente da máquina do funcionário.

Roda continuamente em C:\\qcolabore. Vigia as pastas que o funcionário escolheu,
envia cada arquivo novo para a nuvem (POST /api/colabore/enviar, autenticado pela
chave dele no Bearer) e ORGANIZA o que enviou. Nunca apaga arquivo do usuário —
só MOVE para subpastas ("Enviados" / "Nao enviados").

Irmão do Q-Robô (que faz o mesmo por posto): burro na ponta, inteligência na
nuvem. A data de corte (o que é "velho demais para mandar") vem SEMPRE do
servidor (GET /api/colabore/config), nunca é decidida aqui.

Tecnologia: Python + Nuitka (ver build_qcolabore.ps1). PyInstaller foi tentado no
Q-Robô e o Defender travou — por isso Nuitka. Dependência externa única:
``requests``; todo o resto é stdlib (tkinter para as janelas, logging para o log
com rotação). A chave mora SÓ em C:\\qcolabore\\config.json — nunca em variável de
ambiente, registro do Windows, ou log.
"""
import datetime as _dt
import json
import logging
import os
import queue
import shutil
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

import requests

__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Constantes — um só lugar para mudar.
# ---------------------------------------------------------------------------
SERVIDOR_PADRAO = "https://app.qualicontax.com.br"   # nuvem do Qualicontax
# Casa do agente: C:\qcolabore em produção. QCOLABORE_HOME só é usado pela prova
# automatizada, para não tocar na instalação real da máquina.
BASE_DIR = os.environ.get("QCOLABORE_HOME") or r"C:\qcolabore"
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "qcolabore.log")

SUBPASTA_OK = "Enviados"          # 200/409 -> arquivo entregue
SUBPASTA_ERRO = "Nao enviados"    # 413/415 -> não adianta repetir
SUBPASTAS = (SUBPASTA_OK, SUBPASTA_ERRO)

INTERVALO_PADRAO = 60             # segundos entre varreduras
MARGEM_ESTABILIDADE = 5           # segundos: ignora arquivo mexido agora (meio-cópia)
PORTA_INSTANCIA = 52736           # trava de instância única (bind local)

# Timeout do POST: conexão curta, leitura longa (arquivo pode ter até 200 MB).
TIMEOUT_CONFIG = (10, 30)
TIMEOUT_ENVIO = (10, 300)

log = logging.getLogger("qcolabore")


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


def mover_para(sub, caminho, motivo=None):
    """Move o arquivo para <pasta_de_origem>/<sub> sem sobrescrever. Se 'motivo'
    vier (Nao enviados), grava um <nome>.motivo.txt ao lado. Devolve o caminho
    final, ou None em falha (aí o arquivo fica onde está)."""
    try:
        origem_dir = os.path.dirname(caminho)
        nome = os.path.basename(caminho)
        destino_dir = os.path.join(origem_dir, sub)
        os.makedirs(destino_dir, exist_ok=True)
        nome_final = _nome_livre(destino_dir, nome)
        destino = os.path.join(destino_dir, nome_final)
        shutil.move(caminho, destino)
        if motivo:
            try:
                with open(destino + ".motivo.txt", "w", encoding="utf-8") as f:
                    f.write(f"Arquivo: {nome_final}\r\n")
                    f.write(f"Motivo: {motivo}\r\n")
                    f.write(f"Quando: {_dt.datetime.now():%d/%m/%Y %H:%M:%S}\r\n")
            except OSError:
                pass
        return destino
    except OSError as exc:
        log.warning("Falha ao mover '%s' para '%s': %s",
                    os.path.basename(caminho), sub, exc)
        return None


def contar_aguardando(pastas):
    """Quantos arquivos (não .motivo.txt) esperam atenção em 'Nao enviados'."""
    total = 0
    for p in pastas:
        d = os.path.join(p, SUBPASTA_ERRO)
        try:
            for n in os.listdir(d):
                cheio = os.path.join(d, n)
                if os.path.isfile(cheio) and not n.endswith(".motivo.txt"):
                    total += 1
        except OSError:
            pass
    return total


def arquivos_novos(pasta):
    """Arquivos no TOPO da pasta (não entra em Enviados/Nao enviados), estáveis
    (não mexidos nos últimos MARGEM_ESTABILIDADE seg — evita pegar meio-cópia)."""
    saida = []
    agora = time.time()
    try:
        nomes = os.listdir(pasta)
    except OSError:
        return saida
    for n in nomes:
        if n.endswith(".motivo.txt"):
            continue
        cheio = os.path.join(pasta, n)
        if not os.path.isfile(cheio):
            continue                      # ignora subpastas (inclui Enviados/…)
        try:
            st = os.stat(cheio)
        except OSError:
            continue
        if agora - st.st_mtime < MARGEM_ESTABILIDADE:
            continue                      # ainda sendo escrito
        saida.append((cheio, st.st_mtime))
    return saida


# ===========================================================================
# Worker — o coração: varre, filtra por data, envia, organiza
# ===========================================================================
class ChaveInvalida(Exception):
    pass


class Worker(threading.Thread):
    daemon = True

    def __init__(self, estado):
        super().__init__(name="qcolabore-worker")
        self.estado = estado
        self._parar = threading.Event()
        self._acordar = threading.Event()
        self.sessao = requests.Session()
        self._ignorados = {}              # caminho -> mtime já ignorado (não re-loga)

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
        log.info("Agente iniciado (versao %s).", __version__)
        while not self._parar.is_set():
            cfg = carregar_config()
            if not config_completa(cfg):
                self.estado.set_conexao("chave_invalida", "Falta configurar (chave e pastas).")
            else:
                self._um_ciclo(cfg)
            # espera o intervalo, mas acorda na hora se cutucarem (config nova)
            self._acordar.wait(timeout=max(10, cfg.get("intervalo_seg") or INTERVALO_PADRAO))
            self._acordar.clear()
        log.info("Agente encerrado.")

    def _um_ciclo(self, cfg):
        try:
            corte, ativo = self._buscar_corte(cfg)
        except ChaveInvalida:
            self.estado.set_conexao("chave_invalida",
                                    "A chave e invalida ou foi revogada. Abra Configurar.")
            log.warning("Chave invalida/revogada — envio pausado.")
            return
        except requests.RequestException as exc:
            self.estado.set_conexao("sem_conexao", "Sem conexao com o servidor.")
            log.info("Sem conexao ao consultar config: %s", exc.__class__.__name__)
            return

        self.estado.set_conexao("conectado", "" if ativo else "Chave desligada no servidor.")
        self.estado.set_aguardando(contar_aguardando(cfg["pastas"]))

        for pasta in cfg["pastas"]:
            if self._parar.is_set():
                return
            for caminho, mtime in arquivos_novos(pasta):
                if self._parar.is_set():
                    return
                # Filtro de data de corte — decidido pelo SERVIDOR, aplicado aqui.
                if corte and _dt.date.fromtimestamp(mtime) < corte:
                    if self._ignorados.get(caminho) != mtime:
                        self._ignorados[caminho] = mtime
                        log.info("Ignorado (anterior a %s): %s", corte, os.path.basename(caminho))
                    continue
                try:
                    resultado, motivo = self._enviar(cfg, caminho)
                except ChaveInvalida:
                    self.estado.set_conexao("chave_invalida",
                                            "A chave e invalida ou foi revogada. Abra Configurar.")
                    log.warning("Chave invalida/revogada no envio — parando este ciclo.")
                    return
                except requests.RequestException as exc:
                    self.estado.set_conexao("sem_conexao", "Sem conexao — vai tentar de novo.")
                    log.info("Falha de rede ao enviar '%s': %s",
                             os.path.basename(caminho), exc.__class__.__name__)
                    return          # deixa onde está; tenta no próximo ciclo
                self._aplicar_resultado(caminho, resultado, motivo)

        self.estado.set_aguardando(contar_aguardando(cfg["pastas"]))

    def _aplicar_resultado(self, caminho, resultado, motivo):
        nome = os.path.basename(caminho)
        if resultado == "ok":
            if mover_para(SUBPASTA_OK, caminho):
                self.estado.marcar_enviado(nome)
                log.info("Enviado e movido para %s: %s", SUBPASTA_OK, nome)
        elif resultado == "rejeitado":
            mover_para(SUBPASTA_ERRO, caminho, motivo=motivo)
            log.warning("Recusado (%s) e movido para %s: %s", motivo, SUBPASTA_ERRO, nome)
        else:  # retry — não move, tenta depois
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
    from tkinter import ttk, filedialog, messagebox

    montar_log()
    estado = Estado()
    worker = Worker(estado)

    root = tk.Tk()
    root.title("Q-Colabore — agente")
    root.geometry("420x300")
    root.minsize(400, 280)

    # -------- janela de configuração (Toplevel) --------
    def abrir_config(primeira=False):
        cfg = carregar_config()
        win = tk.Toplevel(root)
        win.title("Configurar Q-Colabore")
        win.geometry("520x460")
        win.transient(root)
        win.grab_set()

        pad = {"padx": 14, "pady": 6}
        tk.Label(win, text="Chave do funcionário", font=("Segoe UI", 9, "bold")).pack(anchor="w", **pad)
        chave_var = tk.StringVar(value=cfg["chave"])
        linha = tk.Frame(win); linha.pack(fill="x", padx=14)
        ent_chave = tk.Entry(linha, textvariable=chave_var, show="•")
        ent_chave.pack(side="left", fill="x", expand=True)
        mostrar = tk.BooleanVar(value=False)
        def _toggle():
            ent_chave.config(show="" if mostrar.get() else "•")
        tk.Checkbutton(linha, text="mostrar", variable=mostrar, command=_toggle).pack(side="left", padx=6)
        tk.Label(win, text="Cole aqui a chave gerada no sistema (Configurações › Usuários).",
                 fg="#666", font=("Segoe UI", 8)).pack(anchor="w", padx=14)

        tk.Label(win, text="Servidor", font=("Segoe UI", 9, "bold")).pack(anchor="w", **pad)
        serv_var = tk.StringVar(value=cfg["servidor"])
        tk.Entry(win, textvariable=serv_var).pack(fill="x", padx=14)

        tk.Label(win, text="Pastas vigiadas", font=("Segoe UI", 9, "bold")).pack(anchor="w", **pad)
        quadro = tk.Frame(win); quadro.pack(fill="both", expand=True, padx=14)
        lista = tk.Listbox(quadro, height=6)
        lista.pack(side="left", fill="both", expand=True)
        for p in cfg["pastas"]:
            lista.insert("end", p)
        botoes = tk.Frame(quadro); botoes.pack(side="left", fill="y", padx=8)
        def _add():
            d = filedialog.askdirectory(title="Escolha uma pasta para vigiar")
            if d and d not in lista.get(0, "end"):
                lista.insert("end", os.path.normpath(d))
        def _rem():
            for i in reversed(lista.curselection()):
                lista.delete(i)
        tk.Button(botoes, text="Adicionar…", command=_add).pack(fill="x", pady=2)
        tk.Button(botoes, text="Remover", command=_rem).pack(fill="x", pady=2)

        rodape = tk.Frame(win); rodape.pack(fill="x", padx=14, pady=12)
        def _salvar():
            chave = chave_var.get().strip()
            pastas = list(lista.get(0, "end"))
            if not chave:
                messagebox.showwarning("Falta a chave", "Cole a chave do funcionário.", parent=win)
                return
            if not pastas:
                messagebox.showwarning("Falta pasta", "Escolha ao menos uma pasta para vigiar.", parent=win)
                return
            salvar_config({"servidor": serv_var.get().strip() or SERVIDOR_PADRAO,
                           "chave": chave, "pastas": pastas,
                           "intervalo_seg": cfg["intervalo_seg"]})
            log.info("Configuracao salva (%d pasta(s)).", len(pastas))   # nunca a chave
            win.destroy()
            if not worker.is_alive():
                worker.start()
            else:
                worker.cutucar()
        def _cancelar():
            win.destroy()
            if primeira and not config_completa(carregar_config()):
                root.destroy()          # 1º uso sem salvar: não há o que rodar
        tk.Button(rodape, text="Salvar", width=12, command=_salvar,
                  default="active").pack(side="right")
        tk.Button(rodape, text="Cancelar", width=10, command=_cancelar).pack(side="right", padx=6)
        win.protocol("WM_DELETE_WINDOW", _cancelar)

    # -------- janela de status (root) --------
    wrap = tk.Frame(root, padx=16, pady=14)
    wrap.pack(fill="both", expand=True)
    tk.Label(wrap, text="Q-Colabore", font=("Segoe UI", 14, "bold")).pack(anchor="w")
    lbl_conexao = tk.Label(wrap, text="", font=("Segoe UI", 10, "bold"))
    lbl_conexao.pack(anchor="w", pady=(6, 0))
    lbl_detalhe = tk.Label(wrap, text="", fg="#666", font=("Segoe UI", 8), wraplength=380, justify="left")
    lbl_detalhe.pack(anchor="w")
    ttk.Separator(wrap).pack(fill="x", pady=10)
    lbl_hoje = tk.Label(wrap, text="", font=("Segoe UI", 10)); lbl_hoje.pack(anchor="w")
    lbl_ultimo = tk.Label(wrap, text="", font=("Segoe UI", 10)); lbl_ultimo.pack(anchor="w")
    lbl_aguard = tk.Label(wrap, text="", font=("Segoe UI", 10)); lbl_aguard.pack(anchor="w")

    barra = tk.Frame(root); barra.pack(fill="x", padx=16, pady=(0, 12))
    tk.Button(barra, text="Configurar…", command=lambda: abrir_config()).pack(side="left")
    tk.Label(barra, text="v%s" % __version__, fg="#999").pack(side="right")

    CORES = {"conectado": "#15803d", "sem_conexao": "#b45309",
             "chave_invalida": "#b91c1c", "iniciando": "#64748b"}
    ROTULO = {"conectado": "Conectado", "sem_conexao": "Sem conexão",
              "chave_invalida": "Atenção — chave", "iniciando": "Iniciando…"}

    def tick():
        s = estado.snapshot()
        lbl_conexao.config(text="● " + ROTULO.get(s["conexao"], s["conexao"]),
                           fg=CORES.get(s["conexao"], "#333"))
        lbl_detalhe.config(text=s["detalhe"] or "")
        lbl_hoje.config(text="Enviados hoje: %d" % s["enviados_hoje"])
        if s["ultimo_nome"]:
            lbl_ultimo.config(text="Último: %s  às %s" % (s["ultimo_nome"], s["ultimo_hora"]))
        else:
            lbl_ultimo.config(text="Último: —")
        aguard = s["aguardando"]
        lbl_aguard.config(
            text=("Em “Não enviados”: %d — precisa de atenção" % aguard) if aguard
                 else "Em “Não enviados”: 0",
            fg="#b91c1c" if aguard else "#333")
        root.after(1500, tick)

    def ao_fechar():
        # Fechar a janela minimiza (o agente é de fundo); sair de verdade só pela
        # bandeja/gerenciador. Sem tray nesta versão: minimiza para a barra.
        root.iconify()
    root.protocol("WM_DELETE_WINDOW", ao_fechar)

    # 1º uso: sem config -> abre a configuração antes de tudo.
    if not config_completa(carregar_config()):
        root.after(200, lambda: abrir_config(primeira=True))
    else:
        worker.start()
        root.after(400, root.iconify)     # sobe minimizado e trabalha sozinho

    tick()
    root.mainloop()
    worker.parar()


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
