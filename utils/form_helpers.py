# -*- coding: utf-8 -*-
"""E1 — normalização ÚNICA da entrada dos formulários de cadastro.

Um só lugar decide o que é "vazio": string em branco e os textos que representam
nada ('None', 'none', 'null', 'NULL', 'undefined'). Evita que a palavra literal
"None" (renderizada por engano num campo e reenviada) vire valor de verdade no
banco. Complementa o fix do template (finalize: None -> '') — os dois lados.
"""

# Textos que NÃO são valor, são "nada". Comparados em minúsculas.
_VAZIOS = {'', 'none', 'null', 'undefined'}


def limpar_vazio(v):
    """Devolve None quando o valor representa vazio; senão, a string sem bordas.

    Só mexe em str — int/bool/None/etc passam intactos (para não transformar
    0/1 em '0'/'1', nem um id inteiro em texto).
    """
    if not isinstance(v, str):
        return v
    s = v.strip()
    return None if s.lower() in _VAZIOS else s


def limpar_form(data):
    """Aplica limpar_vazio em todos os valores de um dict de formulário."""
    return {k: limpar_vazio(v) for k, v in (data or {}).items()}
