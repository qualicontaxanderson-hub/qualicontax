"""Contabilidades — escritórios de onde os clientes vieram (14/10/2026).

Cadastro simples do concorrente (nome, CNPJ, contato, telefone, e-mail,
cidade) para alguém, no futuro, conseguir ir atrás de informação da empresa
com quem cuidava dela antes. A empresa aponta para cá em
clientes.contabilidade_anterior_id.
"""
import re
from utils.db_helper import execute_query

CAMPOS = ('nome', 'cnpj', 'contato', 'telefone', 'email', 'cidade', 'uf', 'observacoes')


def limpar(dados):
    """Formulário/JSON -> colunas, com o nome em maiúsculas e o resto aparado."""
    def txt(k, n):
        return ' '.join(str(dados.get(k) or '').split())[:n] or None
    out = {
        'nome': (txt('nome', 150) or '').upper() or None,
        'cnpj': txt('cnpj', 18),
        'contato': txt('contato', 120),
        'telefone': txt('telefone', 20),
        'email': (txt('email', 150) or '').lower() or None,
        'cidade': txt('cidade', 80),
        'uf': (txt('uf', 2) or '').upper() or None,
        'observacoes': (str(dados.get('observacoes') or '').strip() or None),
    }
    return out


class Contabilidade:

    @staticmethod
    def listar(situacao='ATIVO'):
        """Escritórios com quantas empresas vieram de cada um."""
        cond = "WHERE c.situacao = %s" if situacao else ""
        return execute_query(
            f"""SELECT c.id, c.nome, c.cnpj, c.contato, c.telefone, c.email, c.cidade, c.uf,
                       c.observacoes, c.situacao, c.criado_em,
                       (SELECT COUNT(*) FROM clientes x WHERE x.contabilidade_anterior_id = c.id) AS empresas
                  FROM contabilidades c {cond}
                 ORDER BY c.nome""", (situacao,) if situacao else None, fetch=True) or []

    @staticmethod
    def get(cid):
        return execute_query(
            "SELECT id, nome, cnpj, contato, telefone, email, cidade, uf, observacoes, situacao "
            "FROM contabilidades WHERE id = %s", (cid,), fetch=True, fetch_one=True)

    @staticmethod
    def por_nome(nome):
        """Mesmo nome (sem diferenciar espaço e maiúscula) — evita duplicar."""
        n = re.sub(r'\s+', ' ', (nome or '')).strip().upper()
        return execute_query("SELECT id, nome, situacao FROM contabilidades WHERE UPPER(TRIM(nome)) = %s LIMIT 1",
                             (n,), fetch=True, fetch_one=True)

    @staticmethod
    def criar(dados, usuario_id=None):
        d = limpar(dados)
        return execute_query(
            f"INSERT INTO contabilidades ({', '.join(CAMPOS)}, criado_por) "
            f"VALUES ({', '.join(['%s'] * len(CAMPOS))}, %s)",
            tuple(d[k] for k in CAMPOS) + (usuario_id,))

    @staticmethod
    def atualizar(cid, dados, usuario_id=None):
        d = limpar(dados)
        return execute_query(
            f"UPDATE contabilidades SET {', '.join(k + ' = %s' for k in CAMPOS)}, alterado_por = %s WHERE id = %s",
            tuple(d[k] for k in CAMPOS) + (usuario_id, cid))

    @staticmethod
    def mudar_situacao(cid, situacao):
        return execute_query("UPDATE contabilidades SET situacao = %s WHERE id = %s", (situacao, cid))

    @staticmethod
    def empresas(cid):
        return execute_query(
            "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, situacao, COALESCE(avulso, 0) AS avulso "
            "FROM clientes WHERE contabilidade_anterior_id = %s ORDER BY nome_razao_social",
            (cid,), fetch=True) or []

    @staticmethod
    def para_combo():
        """Lista leve para o campo de escolha (formulário e converter avulso)."""
        return [{'id': r['id'], 'nome': r['nome'], 'cidade': r.get('cidade') or '', 'uf': r.get('uf') or '',
                 'contato': r.get('contato') or '', 'telefone': r.get('telefone') or '',
                 'email': r.get('email') or '', 'empresas': int(r.get('empresas') or 0)}
                for r in Contabilidade.listar()]
