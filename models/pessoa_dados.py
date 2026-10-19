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

UFS = ('AC', 'AL', 'AM', 'AP', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MG', 'MS', 'MT', 'PA',
       'PB', 'PE', 'PI', 'PR', 'RJ', 'RN', 'RO', 'RR', 'RS', 'SC', 'SE', 'SP', 'TO')

CAMPOS = ('data_nascimento', 'rg', 'rg_orgao', 'rg_uf', 'estado_civil', 'regime_bens',
          'nacionalidade', 'profissao', 'nome_mae', 'nivel_govbr')

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
