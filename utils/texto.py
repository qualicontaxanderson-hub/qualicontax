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


_MIUDAS = {'de', 'da', 'do', 'das', 'dos', 'e', 'em', 'a', 'o', 'as', 'os', 'ao', 'aos', 'à', 'às', 'para', 'por', 'com', 'sem', 'no', 'na', 'nos', 'nas', 'ou', 'um', 'uma'}


def titulo_servico(nome):
    """Nome de serviço com as iniciais maiúsculas (20/11/2026 — Anderson: "Alteração contratual para Alteração Contratual...
    com as primeiras letras maiúsculas"). Palavrinhas de ligação ficam minúsculas (menos no início); palavras com maiúscula
    no meio ou toda em maiúsculas (ANP, ANTT, CT-e, DRE, ICMS, LMC, Jucesp) ficam como estão; "13º", "1ª" ficam como estão."""
    nome = ' '.join((nome or '').split())
    if not nome:
        return nome
    if nome.isupper() and ' ' in nome:      # digitado tudo em maiúsculas: vira iniciais (ALVARÁ DE FUNCIONAMENTO -> Alvará de Funcionamento)
        nome = nome.lower()
    partes = nome.split(' ')
    saida = []
    for i, p in enumerate(partes):
        if not p or not p[0].isalpha() and not p[0] in '(\"\'':
            saida.append(p); continue
        if p.lower() in _MIUDAS and i > 0 and partes[i - 1] not in ('—', '–', '-', '/'):
            saida.append(p.lower()); continue
        if any(c.isupper() for c in p[1:]) or p.isupper():
            saida.append(p); continue
        # mantém pontuação de abertura ("(abc" -> "(Abc")
        j = 0
        while j < len(p) and not p[j].isalpha():
            j += 1
        saida.append(p[:j] + p[j:j + 1].upper() + p[j + 1:])
    return ' '.join(saida)


# ---------------------------------------------------------------------------
# Nome de ramo de atividade (24/11/2026 — Anderson: "transportadora petroleo não, e sim
# Transportadora Petróleo"). Cada palavra com a inicial maiúscula, palavrinhas de ligação em
# minúsculas, e o acento devolvido às palavras comuns de ramo digitadas sem ele.
# ---------------------------------------------------------------------------
_ACENTOS_RAMO = {
    'acai': 'açaí', 'acessorios': 'acessórios', 'aco': 'aço', 'acougue': 'açougue', 'acucar': 'açúcar',
    'administracao': 'administração', 'aereo': 'aéreo', 'aerea': 'aérea', 'agencia': 'agência', 'agricola': 'agrícola',
    'agricolas': 'agrícolas', 'agropecuaria': 'agropecuária', 'agropecuario': 'agropecuário', 'agroquimicos': 'agroquímicos',
    'agrotoxicos': 'agrotóxicos', 'agua': 'água', 'aguas': 'águas', 'alcool': 'álcool', 'alimenticio': 'alimentício',
    'alimenticios': 'alimentícios', 'aluminio': 'alumínio', 'ambulancia': 'ambulância', 'analises': 'análises',
    'animacao': 'animação', 'armazem': 'armazém', 'artistica': 'artística', 'artistico': 'artístico',
    'assistencia': 'assistência', 'audio': 'áudio', 'automacao': 'automação', 'automoveis': 'automóveis',
    'autonoma': 'autônoma', 'autonomo': 'autônomo', 'autopecas': 'autopeças', 'avaliacao': 'avaliação',
    'bancario': 'bancário', 'cafe': 'café', 'caes': 'cães', 'calcados': 'calçados', 'camarao': 'camarão',
    'cambio': 'câmbio', 'cameras': 'câmeras', 'capacitacao': 'capacitação', 'cartorio': 'cartório',
    'cartorario': 'cartorário', 'ceramica': 'cerâmica', 'cinematografica': 'cinematográfica', 'climatizacao': 'climatização',
    'clinica': 'clínica', 'clinicas': 'clínicas', 'clinicos': 'clínicos', 'cobranca': 'cobrança', 'cobrancas': 'cobranças',
    'colchoes': 'colchões', 'combustivel': 'combustível', 'combustiveis': 'combustíveis', 'comercio': 'comércio',
    'condominio': 'condomínio', 'condominios': 'condomínios', 'confeccao': 'confecção', 'confeccoes': 'confecções',
    'conservacao': 'conservação', 'consorcio': 'consórcio', 'construcao': 'construção', 'construcoes': 'construções',
    'contabil': 'contábil', 'conteudo': 'conteúdo', 'conveniencia': 'conveniência', 'copias': 'cópias',
    'cosmeticos': 'cosméticos', 'credito': 'crédito', 'criacao': 'criação', 'danca': 'dança', 'decoracao': 'decoração',
    'decoracoes': 'decorações', 'dedetizacao': 'dedetização', 'demolicao': 'demolição', 'deposito': 'depósito',
    'diagnostico': 'diagnóstico', 'digitalizacao': 'digitalização', 'distribuicao': 'distribuição',
    'documentacao': 'documentação', 'domestica': 'doméstica', 'domesticas': 'domésticas', 'domestico': 'doméstico',
    'educacao': 'educação', 'eletrica': 'elétrica', 'eletricas': 'elétricas', 'eletrico': 'elétrico', 'eletricos': 'elétricos',
    'eletrodomesticos': 'eletrodomésticos', 'eletronica': 'eletrônica', 'eletronicos': 'eletrônicos', 'eolica': 'eólica',
    'escavacao': 'escavação', 'escritorio': 'escritório', 'estetica': 'estética', 'exaustao': 'exaustão',
    'exportacao': 'exportação', 'extracao': 'extração', 'extrusao': 'extrusão', 'fabrica': 'fábrica',
    'fabricacao': 'fabricação', 'farmacia': 'farmácia', 'ferroviario': 'ferroviário', 'fotografico': 'fotográfico',
    'frigorifico': 'frigorífico', 'fundicao': 'fundição', 'funeraria': 'funerária', 'gas': 'gás', 'generos': 'gêneros',
    'gestao': 'gestão', 'ginastica': 'ginástica', 'grafica': 'gráfica', 'graficos': 'gráficos', 'graos': 'grãos',
    'hidraulica': 'hidráulica', 'hidraulico': 'hidráulico', 'hidrica': 'hídrica', 'higienizacao': 'higienização',
    'iluminacao': 'iluminação', 'imobiliaria': 'imobiliária', 'imobiliario': 'imobiliário', 'imovel': 'imóvel',
    'imoveis': 'imóveis', 'impermeabilizacao': 'impermeabilização', 'importacao': 'importação', 'impressao': 'impressão',
    'incendio': 'incêndio', 'incorporacao': 'incorporação', 'industria': 'indústria', 'industrias': 'indústrias',
    'informatica': 'informática', 'injecao': 'injeção', 'instalacao': 'instalação', 'instalacoes': 'instalações',
    'inteligencia': 'inteligência', 'interprete': 'intérprete', 'intima': 'íntima', 'irrigacao': 'irrigação',
    'juridica': 'jurídica', 'juridico': 'jurídico', 'la': 'lã', 'laboratorio': 'laboratório', 'laticinio': 'laticínio',
    'laticinios': 'laticínios', 'leilao': 'leilão', 'leiloes': 'leilões', 'locacao': 'locação', 'locacoes': 'locações',
    'locucao': 'locução', 'logistica': 'logística', 'manutencao': 'manutenção', 'mao': 'mão', 'maquinas': 'máquinas',
    'maritimo': 'marítimo', 'marmores': 'mármores', 'mecanica': 'mecânica', 'medica': 'médica', 'medico': 'médico',
    'medicos': 'médicos', 'metalurgica': 'metalúrgica', 'midia': 'mídia', 'midias': 'mídias', 'mineracao': 'mineração',
    'moveis': 'móveis', 'mudancas': 'mudanças', 'musica': 'música', 'nutricao': 'nutrição', 'odontologico': 'odontológico',
    'optica': 'óptica', 'organica': 'orgânica', 'organicos': 'orgânicos', 'organizacao': 'organização', 'otica': 'ótica',
    'otico': 'ótico', 'panificacao': 'panificação', 'papelao': 'papelão', 'participacoes': 'participações',
    'pavimentacao': 'pavimentação', 'pecas': 'peças', 'pecuaria': 'pecuária', 'pericia': 'perícia', 'petroleo': 'petróleo',
    'plastico': 'plástico', 'plasticos': 'plásticos', 'pneumaticos': 'pneumáticos', 'portoes': 'portões',
    'previdencia': 'previdência', 'producao': 'produção', 'producoes': 'produções', 'programacao': 'programação',
    'proprio': 'próprio', 'proprios': 'próprios', 'protese': 'prótese', 'publicacoes': 'publicações', 'quimica': 'química',
    'quimicos': 'químicos', 'racao': 'ração', 'racoes': 'rações', 'radio': 'rádio', 'radiodifusao': 'radiodifusão',
    'rapido': 'rápido', 'reciclaveis': 'recicláveis', 'refrigeracao': 'refrigeração', 'representacao': 'representação',
    'representacoes': 'representações', 'residuos': 'resíduos', 'retifica': 'retífica', 'robotica': 'robótica',
    'rodoviario': 'rodoviário', 'rodoviaria': 'rodoviária', 'salao': 'salão', 'satelite': 'satélite', 'saude': 'saúde',
    'seguranca': 'segurança', 'selecao': 'seleção', 'servico': 'serviço', 'servicos': 'serviços', 'siderurgica': 'siderúrgica',
    'suino': 'suíno', 'suinos': 'suínos', 'tapecaria': 'tapeçaria', 'taxi': 'táxi', 'tecnica': 'técnica', 'tecnico': 'técnico',
    'tecnicos': 'técnicos', 'telecomunicacoes': 'telecomunicações', 'telefonica': 'telefônica', 'televisao': 'televisão',
    'temporario': 'temporário', 'terceirizacao': 'terceirização', 'textil': 'têxtil', 'traducao': 'tradução',
    'transito': 'trânsito', 'tributaria': 'tributária', 'turistico': 'turístico', 'veiculo': 'veículo',
    'veiculos': 'veículos', 'ventilacao': 'ventilação', 'vestuario': 'vestuário', 'veterinaria': 'veterinária',
    'veterinario': 'veterinário', 'veterinarios': 'veterinários', 'video': 'vídeo', 'vidracaria': 'vidraçaria',
    'vigilancia': 'vigilância',
}
# siglas que ficam como são (TI, ANP...); qualquer palavra de até 3 letras toda em maiúsculas também fica
_SIGLAS_RAMO = {'TI', 'ANP', 'ANTT', 'GLP', 'GNV', 'EPI', 'EPIs', 'UTI', 'MEI', 'ONG', 'OSC', 'TV', 'FM', 'AM',
                'PVC', 'LED', 'RH', 'CNH', 'CFTV'}


def _sem_acento(txt):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', txt or '') if unicodedata.category(c) != 'Mn')


def nome_ramo(nome):
    """'transportadora petroleo' -> 'Transportadora Petróleo'; 'LAVA RAPIDO' -> 'Lava Rápido';
    'Serviços em geral' -> 'Serviços em Geral'; 'TI' e outras siglas curtas ficam como estão."""
    nome = ' '.join((nome or '').split())
    partes = nome.split(' ')
    saida = []

    def _pedaco(m):
        w = m.group(0)
        base = _ACENTOS_RAMO.get(_sem_acento(w).lower(), w.lower())
        return base[:1].upper() + base[1:]

    for i, p in enumerate(partes):
        if not p or not any(c.isalpha() for c in p):
            saida.append(p); continue
        if p.lower() in _MIUDAS and i > 0 and partes[i - 1] not in ('—', '–', '-', '/'):
            saida.append(p.lower()); continue
        if p in _SIGLAS_RAMO or p.upper() in _SIGLAS_RAMO or (p.isupper() and len(p) <= 3):
            saida.append(p if p in _SIGLAS_RAMO else p.upper()); continue
        # cada pedaço ligado por hífen ou barra ganha inicial maiúscula e o acento, se faltar
        saida.append(re.sub(r"[^\s\-/]+", _pedaco, p))
    return ' '.join(saida)
