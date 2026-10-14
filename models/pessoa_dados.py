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

CAMPOS = ('data_nascimento', 'rg', 'rg_orgao', 'estado_civil', 'regime_bens',
          'nacionalidade', 'profissao', 'nome_mae', 'nivel_govbr')

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
