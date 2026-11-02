"""Texto para gente ler: nomes e endereços em Maiúscula e minúscula.

O cadastro guarda em CAIXA ALTA (vem assim da Receita e dos extratos). Para o boleto (pedido do
Anderson, 02/11/2026: "tudo bonitinho com letra maiúscula e minúscula") convertemos só na hora de
enviar; o cadastro e a nota fiscal não mudam.
"""
import re

# ficam em minúsculo no meio do texto
_MINUSCULAS = {'de', 'da', 'do', 'das', 'dos', 'e', 'em', 'a', 'o', 'as', 'os', 'para', 'por', 'com',
               'no', 'na', 'nos', 'nas', 'ao', 'à', 'sem', 'sob', 'sobre'}
# ficam em caixa alta sempre
_SIGLAS = {'LTDA', 'LTDA.', 'ME', 'EPP', 'EIRELI', 'MEI', 'S/A', 'S.A', 'S.A.', 'SA', 'CIA', 'CIA.',
           'SS', 'S/S', 'EPP.', 'II', 'III', 'IV', 'VI', 'VII', 'VIII', 'IX', 'XI', 'XII', 'JR', 'JR.',
           'CNPJ', 'CPF', 'RG', 'S/N', 'SN', 'BR', 'KM', 'QD', 'LT', 'BL', 'AP', 'CJ', 'ED', 'SL', 'LJ'}
# ficam em caixa alta no endereço quando sozinhas (sigla de estado)
_UFS = {'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG', 'PA', 'PB', 'PR',
        'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO'}


def _palavra(p, primeira):
    up = p.upper()
    if up in _SIGLAS or up in _UFS:
        return up
    if any(ch.isdigit() for ch in p):          # B2T, S-10, 32A: fica como veio
        return up
    if not primeira and p.lower() in _MINUSCULAS:
        return p.lower()
    # partes ligadas por hífen ou apóstrofo: São João-Batista, D'Ávila
    return re.sub(r"(^|[-'’])([^-'’]+)", lambda m: m.group(1) + m.group(2)[:1].upper() + m.group(2)[1:].lower(), p)


def nome_proprio(texto):
    """'RUA JAIME TAVEIRA' -> 'Rua Jaime Taveira'; 'B2T TECNOLOGIA LTDA' -> 'B2T Tecnologia LTDA'.
    Só converte texto que esteja TODO em caixa alta; o que já vem misturado fica como está."""
    t = (texto or '').strip()
    if not t or t != t.upper():
        return t
    partes = t.split(' ')
    saida, primeira, anterior = [], True, ''
    for p in partes:
        if not p:
            saida.append(p)
            continue
        if len(p) == 1 and p.isalpha() and p.upper() != 'E':
            saida.append(p.upper())            # "Apto 32 A", "Bloco A", "Rua A": letra solta é identificação
        else:
            saida.append(_palavra(p, primeira))
        primeira = p.endswith((',', '.', ';', ':', '-', '(')) or p in ('-', '/', '•', '·')
        anterior = p
    return ' '.join(saida)
