# -*- coding: utf-8 -*-
"""Teste OFFLINE do guard autXML (sem banco, sem rede).

Valida _mesmo_titular — o casamento dono-do-cert × destinatário que decide se a
captura cria a ENTRADA do dono. Casos reais do diagnóstico:

  * entrada normal (dest == dono, CNPJ cheio)          -> True  (cria entrada)
  * matriz/filial (mesma raiz, estabelecimentos ≠)     -> True  (matriz cobre filial)
  * autXML KET (dono CNPJ, dest = CPF consumidor)      -> False (NÃO cria entrada)
  * autXML entre postos (dest = outro grupo)           -> False
  * e-CPF (dono e dest CPF iguais / diferentes)        -> True / False
  * documento vazio / curto / formatado               -> conforme regra

Uso:  python test_dfe_autxml_guard.py     (sai 0 se tudo passar)
"""
import sys

from utils.integrations.dfe_captura import _mesmo_titular

# (nome, dono_cnpj, dest_cnpj, esperado)
CASOS = [
    # Entrada NORMAL — dest é exatamente o dono do certificado.
    ("entrada normal (mesmo CNPJ cheio)", "11158475000127", "11158475000127", True),
    ("entrada normal com formatação", "11.158.475/0001-27", "11158475000127", True),

    # MATRIZ COBRE FILIAL — mesma raiz (8 díg.), estabelecimento diferente.
    ("matriz cobre filial (PETROGOIAS 0001 x 0010)", "05470445000159", "05470445001040", True),
    ("filial cobre matriz (ordem inversa)", "05470445001040", "05470445000159", True),

    # autXML — dono é só AUTORIZADO, NÃO é o destinatário → NÃO cria entrada.
    ("autXML KET: dono CNPJ, dest = CPF consumidor", "11158475000127", "00970136099", False),
    ("autXML: dono MOURA, dest = JK (outro grupo)", "03038307000170", "42454478000131", False),
    ("autXML: raízes diferentes", "11158475000127", "47819301000105", False),

    # e-CPF (dono cliente 1 = ANDERSON) — compara 11 dígitos inteiros.
    ("e-CPF dono==dest", "29151141884", "29151141884", True),
    ("e-CPF dono!=dest", "29151141884", "11111111111", False),
    ("CNPJ dono x CPF dest (tamanhos diferentes)", "11158475000127", "29151141884", False),

    # Degenerados — nunca casa.
    ("dest vazio", "11158475000127", "", False),
    ("dest None", "11158475000127", None, False),
    ("dono vazio", "", "11158475000127", False),
    ("dest curto (<11)", "11158475000127", "1234567", False),
]


def main():
    falhas = 0
    for nome, dono, dest, esperado in CASOS:
        got = _mesmo_titular(dest, dono)
        ok = got is esperado
        print(f"  [{'OK ' if ok else 'FALHA'}] {nome}: "
              f"_mesmo_titular(dest={dest!r}, dono={dono!r}) = {got} (esperado {esperado})")
        if not ok:
            falhas += 1

    print("-" * 70)
    if falhas:
        print(f"FALHOU: {falhas}/{len(CASOS)} caso(s).")
        return 1
    print(f"OK: {len(CASOS)}/{len(CASOS)} casos passaram.")
    print("  -> entrada normal e matriz/filial preservadas; autXML barrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
