"""Dados da pessoa física (sócios, passo 3 — 14/10/2026).

O que o contrato social, a Junta e o gov.br pedem de quem é sócio:
nascimento, RG, estado civil e regime de bens, nacionalidade, profissão,
nome da mãe e o nível da conta gov.br. Uma linha por cadastro PF
(clientes_pf_dados, 1:1 com clientes). Nada aqui é obrigatório: a pessoa
é completada quando a equipe tiver o documento em mãos.
"""
from utils.db_helper import execute_query

ESTADOS_CIVIS = [
    ('SOLTEIRO', 'Solteiro(a)'), ('CASADO', 'Casado(a)'), ('UNIAO_ESTAVEL', 'União estável'),
    ('SEPARADO', 'Separado(a) judicialmente'), ('DIVORCIADO', 'Divorciado(a)'), ('VIUVO', 'Viúvo(a)'),
]
# Regime de bens só vale para casado(a) e união estável.
COM_REGIME = ('CASADO', 'UNIAO_ESTAVEL')
REGIMES_BENS = [
    ('COMUNHAO_PARCIAL', 'Comunhão parcial de bens'),
    ('COMUNHAO_UNIVERSAL', 'Comunhão universal de bens'),
    ('SEPARACAO_TOTAL', 'Separação total (convencional)'),
    ('SEPARACAO_OBRIGATORIA', 'Separação obrigatória'),
    ('PARTICIPACAO_AQUESTOS', 'Participação final nos aquestos'),
]
NIVEIS_GOVBR = [('BRONZE', 'Bronze'), ('PRATA', 'Prata'), ('OURO', 'Ouro')]
# 17/11/2026: identificação e eSocial (Anderson: "está pobre demais os dados pessoais... me traga coisas melhores")
SEXOS = [('M', 'Masculino'), ('F', 'Feminino')]
GRAUS_INSTRUCAO = [          # tabela 1 do eSocial
    ('01', 'Analfabeto'), ('02', 'Até o 5º ano incompleto'), ('03', '5º ano completo'),
    ('04', '6º ao 9º ano incompleto'), ('05', 'Ensino fundamental completo'), ('06', 'Ensino médio incompleto'),
    ('07', 'Ensino médio completo'), ('08', 'Superior incompleto'), ('09', 'Superior completo'),
    ('10', 'Pós-graduação completa'), ('11', 'Mestrado completo'), ('12', 'Doutorado completo'),
]
RACAS_COR = [('1', 'Branca'), ('2', 'Preta'), ('3', 'Parda'), ('4', 'Amarela'), ('5', 'Indígena'), ('6', 'Não informada')]

UFS = ('AC', 'AL', 'AM', 'AP', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MG', 'MS', 'MT', 'PA',
       'PB', 'PE', 'PI', 'PR', 'RJ', 'RN', 'RO', 'RR', 'RS', 'SC', 'SE', 'SP', 'TO')

# rg/rg_orgao/rg_uf continuam na tabela (histórico), mas o documento agora
# mora em clientes_pf_documentos — ver Documentos abaixo.
CAMPOS = ('data_nascimento', 'estado_civil', 'regime_bens',
          'nacionalidade', 'profissao', 'nome_mae', 'nivel_govbr',
          'conjuge_cpf', 'conjuge_nome',      # cônjuge: 11/11/2026, opcional
          'sexo', 'naturalidade_cidade', 'naturalidade_uf', 'nome_pai', 'nome_social',   # 17/11/2026
          'pis_nit', 'grau_instrucao', 'raca_cor', 'ecac_procuracao_ate')


def cpf_valido(cpf):
    """Confere os dois dígitos verificadores do CPF."""
    import re
    d = re.sub(r'\D', '', cpf or '')
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def cpf_formatado(cpf):
    import re
    d = re.sub(r'\D', '', cpf or '')
    return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}' if len(d) == 11 else None


def pis_valido(pis):
    """PIS/PASEP/NIT: 11 dígitos e o dígito verificador (pesos 3,2,9,8,7,6,5,4,3,2, módulo 11)."""
    import re
    d = re.sub(r'\D', '', pis or '')
    if len(d) != 11 or d == d[0] * 11:
        return False
    soma = sum(int(x) * p for x, p in zip(d[:10], (3, 2, 9, 8, 7, 6, 5, 4, 3, 2)))
    dv = 11 - soma % 11
    return int(d[10]) == (0 if dv in (10, 11) else dv)


def pis_formatado(pis):
    import re
    d = re.sub(r'\D', '', pis or '')
    return f'{d[:3]}.{d[3:8]}.{d[8:10]}-{d[10]}' if len(d) == 11 else None

# ---------------------------------------------------------------------------
# Listas fechadas (19/10/2026): nacionalidade, profissão e órgão emissor saem
# de cadastro_opcoes — a equipe ESCOLHE, só o admin inclui. Texto livre virou
# "Brasileira"/"BRASILEIRA" e "SSP"/"SSP SP"/"SSP/MG" em 7 cadastros, e com
# isso nenhum filtro funciona. Uma forma só, com "(a)" como o estado civil:
# "Brasileiro(a)", "Empresário(a)". O órgão é a sigla; a UF fica em rg_uf.
# ---------------------------------------------------------------------------
TIPOS_OPCAO = {'nacionalidade': 'Nacionalidade', 'profissao': 'Profissão',
               'rg_orgao': 'Órgão emissor'}
OPCOES_INICIAIS = {
    'nacionalidade': ['Brasileiro(a)'],
    'profissao': ['Empresário(a)'],
    'rg_orgao': ['SSP', 'PC', 'DGPC', 'SESP', 'SDS', 'SEJUSP', 'SSPDS', 'IGP', 'IFP',
                 'POLITEC', 'DETRAN', 'DPF', 'EB', 'MB', 'FAB',
                 'OAB', 'CRC', 'CRM', 'CREA', 'CRO', 'CRF', 'CRBM', 'CRA'],
}


class Opcoes:
    """Itens das listas fechadas do cadastro da pessoa."""

    @staticmethod
    def listar(tipo):
        rows = execute_query(
            "SELECT nome FROM cadastro_opcoes WHERE tipo = %s ORDER BY nome",
            (tipo,), fetch=True) or []
        return [r['nome'] for r in rows]

    @staticmethod
    def todas():
        return {t: Opcoes.listar(t) for t in TIPOS_OPCAO}

    @staticmethod
    def parecidas(tipo, nome):
        """Itens que já existem e são o mesmo nome escrito de outro jeito
        (sem acento, caixa, "(a)", "/"): o admin vê antes de duplicar."""
        alvo = _chave(nome)
        return [n for n in Opcoes.listar(tipo) if _chave(n) == alvo]

    @staticmethod
    def incluir(tipo, nome, usuario_id):
        return execute_query(
            "INSERT IGNORE INTO cadastro_opcoes (tipo, nome, criado_por) VALUES (%s, %s, %s)",
            (tipo, nome, usuario_id))


def _chave(nome):
    """'Empresária' / 'EMPRESARIO' / 'Empresário(a)' -> 'EMPRESARI'."""
    import re
    import unicodedata
    t = unicodedata.normalize('NFKD', nome or '').encode('ascii', 'ignore').decode().upper()
    t = re.sub(r'\(A\)|[^A-Z]', '', t)
    return re.sub(r'(O|A)$', '', t)

ROTULOS = {
    'estado_civil': dict(ESTADOS_CIVIS),
    'regime_bens': dict(REGIMES_BENS),
    'nivel_govbr': dict(NIVEIS_GOVBR),
    'sexo': dict(SEXOS),
    'grau_instrucao': dict(GRAUS_INSTRUCAO),
    'raca_cor': dict(RACAS_COR),
}


class PessoaDados:

    @staticmethod
    def get(cliente_id):
        """Dados da pessoa ou {} (cadastro PF que ainda não foi completado)."""
        row = execute_query(
            f"SELECT {', '.join(CAMPOS)} FROM clientes_pf_dados WHERE cliente_id = %s",
            (cliente_id,), fetch=True, fetch_one=True)
        return row or {}

    @staticmethod
    def salvar(cliente_id, dados, usuario_id=None):
        """Grava (cria ou substitui) os dados — vazio grava NULL."""
        vals = [dados.get(c) or None for c in CAMPOS]
        cols = ', '.join(CAMPOS)
        marks = ', '.join(['%s'] * len(CAMPOS))
        upd = ', '.join(f'{c} = VALUES({c})' for c in CAMPOS)
        return execute_query(
            f"""INSERT INTO clientes_pf_dados (cliente_id, {cols}, alterado_por)
                VALUES (%s, {marks}, %s)
                ON DUPLICATE KEY UPDATE {upd}, alterado_por = VALUES(alterado_por)""",
            (cliente_id, *vals, usuario_id))

    @staticmethod
    def rotulo(campo, valor):
        """Código gravado -> texto da tela (o que não tem rótulo volta como veio)."""
        return ROTULOS.get(campo, {}).get(valor, valor)


# ---------------------------------------------------------------------------
# Documentos de identificação (19/10/2026): "RG é vago" — a pessoa tem RG, CNH,
# CIN, registro no conselho (CRC, OAB...) e cada um com órgão e UF. Vários por
# pessoa; os marcados "Contrato" vão no contrato (vários desde 11/11/2026). Tipo é lista fixa; órgão vem
# da lista fechada rg_orgao (cadastro_opcoes).
# ---------------------------------------------------------------------------
TIPOS_DOC = [
    ('RG', 'RG'), ('CIN', 'CIN (nova identidade)'), ('CNH', 'CNH'),
    ('OAB', 'OAB'), ('CRC', 'CRC'), ('CRM', 'CRM'), ('CREA', 'CREA'), ('CRO', 'CRO'),
    ('CRF', 'CRF'), ('CRBM', 'CRBM'), ('CRA', 'CRA'),
    ('RNM', 'RNM (estrangeiro)'), ('PASSAPORTE', 'Passaporte'),
    ('TITULO', 'Título de eleitor'), ('CTPS', 'CTPS'),          # 17/11/2026
]
# Documento que vence (17/11/2026): a tela pede "válido até" e avisa quando passou.
COM_VALIDADE = ('CNH', 'PASSAPORTE', 'RNM', 'CIN')
# Órgão que já vem escolhido ao trocar o tipo (a pessoa pode mudar).
ORGAO_SUGERIDO = {'CNH': 'DETRAN', 'RNM': 'DPF', 'PASSAPORTE': 'DPF',
                  **{c: c for c in ('OAB', 'CRC', 'CRM', 'CREA', 'CRO', 'CRF', 'CRBM', 'CRA')}}


class Documentos:

    @staticmethod
    def listar(cliente_id):
        return execute_query(
            "SELECT tipo, numero, orgao, uf, contrato, validade FROM clientes_pf_documentos "
            "WHERE cliente_id = %s ORDER BY contrato DESC, id", (cliente_id,), fetch=True) or []

    @staticmethod
    def salvar(cliente_id, docs, usuario_id=None):
        """Substitui o conjunto de documentos da pessoa pelo da tela."""
        execute_query("DELETE FROM clientes_pf_documentos WHERE cliente_id = %s", (cliente_id,))
        for d in docs:
            execute_query(
                "INSERT INTO clientes_pf_documentos (cliente_id, tipo, numero, orgao, uf, contrato, validade, alterado_por) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (cliente_id, d['tipo'], d['numero'], d.get('orgao'), d.get('uf'),
                 1 if d.get('contrato') else 0, d.get('validade') or None, usuario_id))

    @staticmethod
    def resumo(docs):
        """'RG 41123866 SSP/GO (contrato); CNH 0123 DETRAN/GO' — para o Histórico."""
        partes = []
        for d in docs:
            org = '/'.join(x for x in (d.get('orgao'), d.get('uf')) if x)
            val = d.get('validade')
            val = f" válido até {val.strftime('%d/%m/%Y')}" if hasattr(val, 'strftime') else (f' válido até {val}' if val else '')
            partes.append(' '.join(x for x in (d['tipo'], d['numero'], org) if x) + val
                          + (' (contrato)' if d.get('contrato') else ''))
        return '; '.join(partes) or None
