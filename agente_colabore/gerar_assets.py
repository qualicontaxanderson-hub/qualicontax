# -*- coding: utf-8 -*-
"""Gera os assets embutidos do agente a partir das imagens OFICIAIS do sistema
(a MESMA marca que o Q-Robô usa), para a próxima versão não depender de ninguém
lembrar de onde saiu cada imagem:

  - qcolabore_logo.png : o logo horizontal "Qualicontax" (static/images/logo.png),
    redimensionado para caber no cabeçalho da janela (mesma marca do Q-Robô).
  - qcolabore_icon.png : o "Q" verde (static/icons/icon-512.png) em 256px, para o
    ícone da janela e da BANDEJA — o mesmo desenho do ícone do Q-Robô.
  - qcolabore.ico      : o mesmo "Q" verde em múltiplos tamanhos, para o ícone do
    EXECUTÁVEL (Nuitka --windows-icon-from-ico).

Rodar da raiz do repositório:  python agente_colabore/gerar_assets.py
"""
import os

from PIL import Image

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AQUI = os.path.join(RAIZ, "agente_colabore")
LOGO_SRC = os.path.join(RAIZ, "static", "images", "logo.png")
QMARK_SRC = os.path.join(RAIZ, "static", "icons", "icon-512.png")

ALTURA_LOGO = 44          # altura do logo no cabeçalho (px), como no Q-Robô


def main():
    # 1) logo do cabeçalho — preserva proporção, altura fixa.
    logo = Image.open(LOGO_SRC).convert("RGBA")
    larg = max(1, round(logo.width * ALTURA_LOGO / logo.height))
    logo.resize((larg, ALTURA_LOGO), Image.LANCZOS).save(
        os.path.join(AQUI, "qcolabore_logo.png"))
    print("qcolabore_logo.png:", larg, "x", ALTURA_LOGO)

    # 2) "Q" verde — 256px para janela/bandeja.
    q = Image.open(QMARK_SRC).convert("RGBA")
    q.resize((256, 256), Image.LANCZOS).save(os.path.join(AQUI, "qcolabore_icon.png"))
    print("qcolabore_icon.png: 256 x 256")

    # 3) .ico multi-tamanho para o executável.
    q.save(os.path.join(AQUI, "qcolabore.ico"),
           sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("qcolabore.ico: 16..256")


if __name__ == "__main__":
    main()
