"""Contabilidades — escritórios de onde os clientes vieram (14/10/2026).

Cadastro do concorrente para alguém, no futuro, conseguir ir atrás de
informação da empresa com quem cuidava dela antes. A empresa aponta para cá
em clientes.contabilidade_anterior_id.

19/10/2026 ("cadastro chique"): busca pelo CNPJ na Receita, endereço completo
e VÁRIOS contatos (contabilidade_contatos), cada um com função da lista
fechada, telefone e e-mail, um marcado como principal. A coluna ``contato``
continua existindo e guarda o nome do contato principal — é o que o campo de
escolha do cadastro da empresa mostra.
"""
import re
from utils.db_helper import execute_query

CAMPOS = ('nome', 'nome_fantasia', 'cnpj', 'contato', 'telefone', 'email', 'cep', 'logradouro', 'numero',
          'complemento', 'bairro', 'cidade', 'uf', 'observacoes')

UFS = ('AC', 'AL', 'AM', 'AP', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MG', 'MS', 'MT', 'PA',
       'PB', 'PE', 'PI', 'PR', 'RJ', 'RN', 'RO', 'RR', 'RS', 'SC', 'SE', 'SP', 'TO')

# Lista fechada (a equipe escolhe, não digita): é por ela que se acha "quem
# do escritório cuida do DP" quando precisar.
FUNCOES = ('Sócio / Responsável', 'Contador(a)', 'Fiscal', 'Departamento Pessoal', 'Contábil',
           'Financeiro', 'Societário / Legalização', 'Atendimento', 'Outro')


def limpar(dados):
    """Formulário/JSON -> colunas. Nome e endereço em MAIÚSCULAS (como o
    cadastro de clientes), e-mail em minúsculas, CEP e CNPJ formatados."""
    def txt(k, n):
        return ' '.join(str(dados.get(k) or '').split())[:n] or None

    def mai(k, n):
        return (txt(k, n) or '').upper() or None
    cnpj = re.sub(r'\D', '', str(dados.get('cnpj') or ''))
    cep = re.sub(r'\D', '', str(dados.get('cep') or ''))
    uf = (txt('uf', 2) or '').upper()
    return {
        'nome': mai('nome', 150),
        'nome_fantasia': mai('nome_fantasia', 150),
        'cnpj': (f'{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}' if len(cnpj) == 14
                 else txt('cnpj', 18)),
        'contato': mai('contato', 120),
        'telefone': txt('telefone', 20),
        'email': (txt('email', 150) or '').lower() or None,
        'cep': f'{cep[:5]}-{cep[5:]}' if len(cep) == 8 else None,
        'logradouro': mai('logradouro', 150),
        'numero': mai('numero', 20),
        'complemento': mai('complemento', 100),
        'bairro': mai('bairro', 80),
        'cidade': mai('cidade', 80),
        'uf': uf if uf in UFS else None,
        'observacoes': (str(dados.get('observacoes') or '').strip() or None),
    }


def limpar_contatos(lista):
    """contatos_json da tela -> linhas válidas (nome obrigatório; função da
    lista; um principal — sem marca, o primeiro)."""
    out = []
    for c in lista if isinstance(lista, list) else []:
        if not isinstance(c, dict):
            continue
        nome = ' '.join(str(c.get('nome') or '').split()).upper()[:120]
        if not nome:
            continue
        funcao = str(c.get('funcao') or '')
        email = ' '.join(str(c.get('email') or '').split()).lower()[:150]
        out.append({'nome': nome, 'funcao': funcao if funcao in FUNCOES else None,
                    'telefone': ' '.join(str(c.get('telefone') or '').split())[:20] or None,
                    'email': email if '@' in email else None, 'principal': bool(c.get('principal'))})
    marcados = [c for c in out if c['principal']]
    for c in out:
        c['principal'] = False
    if out:
        (marcados[0] if marcados else out[0])['principal'] = True
    return out


class Contabilidade:

    @staticmethod
    def listar(situacao='ATIVO'):
        """Escritórios com quantas empresas vieram de cada um."""
        cond = "WHERE c.situacao = %s" if situacao else ""
        return execute_query(
            f"""SELECT c.id, {', '.join('c.' + k for k in CAMPOS)}, c.situacao, c.criado_em,
                       (SELECT COUNT(*) FROM clientes x WHERE x.contabilidade_anterior_id = c.id) AS empresas
                  FROM contabilidades c {cond}
                 ORDER BY c.nome""", (situacao,) if situacao else None, fetch=True) or []

    @staticmethod
    def get(cid):
        return execute_query(
            f"SELECT id, {', '.join(CAMPOS)}, situacao FROM contabilidades WHERE id = %s",
            (cid,), fetch=True, fetch_one=True)

    @staticmethod
    def por_nome(nome):
        """Mesmo nome (sem diferenciar espaço e maiúscula) — evita duplicar."""
        n = re.sub(r'\s+', ' ', (nome or '')).strip().upper()
        return execute_query("SELECT id, nome, situacao FROM contabilidades WHERE UPPER(TRIM(nome)) = %s LIMIT 1",
                             (n,), fetch=True, fetch_one=True)

    @staticmethod
    def por_cnpj(cnpj):
        """Mesmo CNPJ (só os dígitos) — o outro jeito de duplicar."""
        d = re.sub(r'\D', '', cnpj or '')
        if len(d) != 14:
            return None
        return execute_query(
            "SELECT id, nome, situacao FROM contabilidades "
            "WHERE REPLACE(REPLACE(REPLACE(cnpj, '.', ''), '/', ''), '-', '') = %s LIMIT 1",
            (d,), fetch=True, fetch_one=True)

    @staticmethod
    def criar(dados, usuario_id=None):
        d = limpar(dados)
        return execute_query(
            f"INSERT INTO contabilidades ({', '.join(CAMPOS)}, criado_por) "
            f"VALUES ({', '.join(['%s'] * len(CAMPOS))}, %s)",
            tuple(d[k] for k in CAMPOS) + (usuario_id,))

    @staticmethod
    def atualizar(cid, dados, usuario_id=None):
        """Edição pela janela: ``contato`` fica de fora — quem o mantém é
        salvar_contatos (nome do contato principal)."""
        d = limpar(dados)
        cols = [k for k in CAMPOS if k != 'contato']
        return execute_query(
            f"UPDATE contabilidades SET {', '.join(k + ' = %s' for k in cols)}, alterado_por = %s WHERE id = %s",
            tuple(d[k] for k in cols) + (usuario_id, cid))

    @staticmethod
    def mudar_situacao(cid, situacao):
        return execute_query("UPDATE contabilidades SET situacao = %s WHERE id = %s", (situacao, cid))

    @staticmethod
    def empresas(cid):
        return execute_query(
            "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj, situacao, COALESCE(avulso, 0) AS avulso "
            "FROM clientes WHERE contabilidade_anterior_id = %s ORDER BY nome_razao_social",
            (cid,), fetch=True) or []

    # ---- contatos ----------------------------------------------------------
    @staticmethod
    def contatos(cid):
        return execute_query(
            "SELECT nome, funcao, telefone, email, principal FROM contabilidade_contatos "
            "WHERE contabilidade_id = %s ORDER BY principal DESC, id", (cid,), fetch=True) or []

    @staticmethod
    def contatos_de_todos():
        """{contabilidade_id: [contatos]} numa ida só (a lista de cartões)."""
        por = {}
        for r in execute_query(
                "SELECT contabilidade_id, nome, funcao, telefone, email, principal FROM contabilidade_contatos "
                "ORDER BY contabilidade_id, principal DESC, id", fetch=True) or []:
            por.setdefault(r['contabilidade_id'], []).append(
                {k: r[k] for k in ('nome', 'funcao', 'telefone', 'email')} | {'principal': bool(r['principal'])})
        return por

    @staticmethod
    def salvar_contatos(cid, contatos, usuario_id=None):
        """Substitui os contatos do escritório e copia o nome do principal para
        a coluna ``contato`` (o campo de escolha da empresa lê de lá)."""
        execute_query("DELETE FROM contabilidade_contatos WHERE contabilidade_id = %s", (cid,))
        for c in contatos:
            execute_query(
                "INSERT INTO contabilidade_contatos (contabilidade_id, nome, funcao, telefone, email, principal, criado_por) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (cid, c['nome'], c['funcao'], c['telefone'], c['email'], 1 if c['principal'] else 0, usuario_id))
        principal = next((c for c in contatos if c['principal']), None)
        execute_query("UPDATE contabilidades SET contato = %s WHERE id = %s",
                      (principal['nome'] if principal else None, cid))

    @staticmethod
    def resumo_contatos(contatos):
        """'FULANO (Fiscal) 62 9999-9999 *; BELTRANO' — para o Histórico."""
        return '; '.join(' '.join(x for x in (c['nome'], f"({c['funcao']})" if c.get('funcao') else '',
                                               c.get('telefone') or '', c.get('email') or '',
                                               '*' if c.get('principal') else '') if x)
                         for c in contatos) or None

    @staticmethod
    def para_combo():
        """Lista leve para o campo de escolha (formulário e converter avulso)."""
        return [{'id': r['id'], 'nome': r['nome'], 'cidade': r.get('cidade') or '', 'uf': r.get('uf') or '',
                 'contato': r.get('contato') or '', 'telefone': r.get('telefone') or '',
                 'email': r.get('email') or '', 'empresas': int(r.get('empresas') or 0)}
                for r in Contabilidade.listar()]
