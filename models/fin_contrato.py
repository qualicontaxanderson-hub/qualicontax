# -*- coding: utf-8 -*-
"""Contrato de honorários (Documento E, fase 6; desenhado com o Anderson em 05–06/11/2026).

Um contrato por empresa. O valor do contrato é a SOMA das discriminações (``fin_contrato_itens``):
"Honorários contábeis" sozinho na maioria; LMC, loja de conveniência, sistema de CT-e etc. quando a
empresa paga por mais de uma coisa (Centro Norte: posto + três serviços mensais).

Regras fixas (dele):
* competência do mês M vence no ``dia_vencimento`` do mês M+1 (out/26 vence 04/11);
* 13º SEMPRE proporcional aos meses de casa no ano (entrou em maio = 8/12; saiu em maio = 4/12),
  em quatro modos: ``UNICA`` (competência nov, vence dez), ``DUAS`` (out vence nov + nov vence dez),
  ``PERSONALIZADO`` (frações em % por competência, digitadas) e ``NENHUM``;
* reajuste padrão pelo salário mínimo todo janeiro: migrados em jan/2027; contrato novo no primeiro
  janeiro depois de 12 meses (nov/26 → jan/28). Exceções guardadas em ``reajuste_tipo``;
* quem não é o Anderson cadastra como ``PENDENTE``; a aprovação dele deixa ``ATIVO``.
"""
import json
from datetime import date
from decimal import Decimal

from utils.db_helper import execute_query

EMPRESA_QUALICONTAX = 2            # clientes.id da Qualicontax (empresa_id do financeiro)
CATEGORIA_HONORARIOS = 1           # fin_categorias 'Honorários contábeis'
CATEGORIA_DECIMO = 25              # fin_categorias 'Décimo terceiro'
SERVICO_HONORARIOS = 1             # nfse_servicos 'Honorários contábeis'

DECIMO_TIPOS = {'NENHUM': 'Sem 13º (não cobra ou foi cancelado)', 'UNICA': 'Parcela única (vence em dezembro)',
                'DUAS': 'Duas parcelas (vencem em novembro e dezembro)', 'PERSONALIZADO': 'Personalizado'}
# Quando o 13º começa a ser cobrado (Anderson, 06/11/2026: "temos que ter a OPÇÃO, já inicia a cobrança no ano
# ou próximo ano em diante"). Vira a coluna decimo_desde: a base do proporcional.
DECIMO_INICIOS = {'PROPORCIONAL': 'Já este ano, proporcional aos meses de casa',
                  'INTEIRO': 'Já este ano, inteiro (12/12)',
                  'PROXIMO': 'Só a partir do ano que vem',
                  'DATA': 'Outra data (na casa desde...)'}
REAJUSTE_TIPOS = {'SALARIO_MINIMO': 'Salário mínimo, todo janeiro', 'INDICE': 'Índice próprio',
                  'DATA_PROPRIA': 'Data própria', 'SEM': 'Sem reajuste / adiado', 'ESCALONADO': 'Escalonado'}
SITUACOES = ('PENDENTE', 'ATIVO', 'ENCERRADO', 'REPROVADO')


def _d(v):
    return Decimal(str(v or 0)).quantize(Decimal('0.01'))


def primeiro_reajuste(inicio):
    """Primeiro janeiro depois de 12 meses de casa: nov/26 → jan/28; jan/26 → jan/27; fev/27 → jan/29."""
    if not inicio:
        return None
    um_ano = date(inicio.year + 1, inicio.month, 1)
    return date(um_ano.year, 1, 1) if um_ano.month == 1 else date(um_ano.year + 1, 1, 1)


def decimo_desde_de(modo, inicio, data=None):
    """Traduz a opção da tela em decimo_desde. PROPORCIONAL = em branco (vale o início do contrato)."""
    if modo == 'INTEIRO' and inicio:
        return date(inicio.year, 1, 1)
    if modo == 'PROXIMO' and inicio:
        return date(inicio.year + 1, 1, 1)
    if modo == 'DATA':
        return data
    return None


def modo_decimo_inicio(decimo_desde, inicio):
    """Caminho de volta: qual opção da tela corresponde ao decimo_desde gravado."""
    if not decimo_desde:
        return 'PROPORCIONAL'
    if inicio and decimo_desde == date(inicio.year, 1, 1):
        return 'INTEIRO'
    if inicio and decimo_desde == date(inicio.year + 1, 1, 1):
        return 'PROXIMO'
    return 'DATA'


def meses_de_casa(inicio, fim, ano):
    """Quantos meses do ano a empresa esteve na casa (para o 13º proporcional)."""
    de = max(inicio or date(ano, 1, 1), date(ano, 1, 1))
    ate = min(fim or date(ano, 12, 31), date(ano, 12, 31))
    if de.year != ano or ate < de:
        return 0
    return ate.month - de.month + 1


class FinContrato:

    @staticmethod
    def listar(empresa_id=EMPRESA_QUALICONTAX, situacao=None, busca=None, criado_por=None):
        cond, params = ['c.empresa_id = %s'], [empresa_id]
        if situacao:
            cond.append('c.situacao = %s'); params.append(situacao)
        if criado_por:
            cond.append('c.criado_por = %s'); params.append(criado_por)
        if busca:
            cond.append('(cl.nome_razao_social LIKE %s OR cl.numero_cliente = %s OR cl.cpf_cnpj LIKE %s)')
            params += ['%%%s%%' % busca, busca, '%%%s%%' % busca]
        return execute_query(
            f"""SELECT c.*, cl.numero_cliente, cl.nome_razao_social, cl.nome_fantasia, cl.cpf_cnpj,
                       cl.situacao AS cliente_situacao, s.nome AS servico_nome, uc.nome AS criado_por_nome,
                       (SELECT COUNT(*) FROM fin_contrato_itens i WHERE i.contrato_id = c.id) AS n_itens,
                       (SELECT COUNT(*) FROM contatos_clientes ct JOIN contatos_departamentos cd ON cd.contato_id = ct.id
                         WHERE ct.cliente_id = c.cliente_id AND ct.ativo = 1 AND cd.departamento_id = 8 AND ct.email <> '') AS n_contatos_fin
                  FROM fin_contratos c
                  JOIN clientes cl ON cl.id = c.cliente_id
                  LEFT JOIN nfse_servicos s ON s.id = c.servico_id
                  LEFT JOIN usuarios uc ON uc.id = c.criado_por
                 WHERE {' AND '.join(cond)}
                 ORDER BY (cl.numero_cliente IS NULL), cl.numero_cliente + 0, cl.nome_razao_social""", tuple(params), fetch=True) or []

    @staticmethod
    def get(contrato_id):
        return execute_query(
            """SELECT c.*, cl.numero_cliente, cl.nome_razao_social, cl.cpf_cnpj
                 FROM fin_contratos c JOIN clientes cl ON cl.id = c.cliente_id WHERE c.id = %s""",
            (contrato_id,), fetch=True, fetch_one=True)

    @staticmethod
    def por_cliente(cliente_id, so_vigente=True):
        cond = "AND situacao IN ('PENDENTE','ATIVO')" if so_vigente else ''
        return execute_query(
            f"SELECT * FROM fin_contratos WHERE cliente_id = %s {cond} ORDER BY FIELD(situacao,'ATIVO','PENDENTE','ENCERRADO','REPROVADO'), id DESC",
            (cliente_id,), fetch=True) or []

    @staticmethod
    def itens(contrato_id):
        return execute_query('SELECT * FROM fin_contrato_itens WHERE contrato_id = %s ORDER BY ordem, id',
                             (contrato_id,), fetch=True) or []

    @staticmethod
    def criar(cliente_id, valor, dia_vencimento, inicio, itens, descricao='Honorários contábeis',
              plano=None, decimo_tipo='NENHUM', decimo_parcelas=None, reajuste_tipo='SALARIO_MINIMO',
              proximo_reajuste=None, reajuste_obs=None, situacao='ATIVO', fim=None, decimo_desde=None,
              servico_id=SERVICO_HONORARIOS, categoria_id=CATEGORIA_HONORARIOS, gerar_nfse=1, gerar_boleto=1,
              conexa_contrato_id=None, criado_por=None, aprovado_por=None, empresa_id=EMPRESA_QUALICONTAX, observacao=None):
        """Devolve o id. Recusa (ValueError) se a soma dos itens não bate com o valor ou se a empresa
        já tem contrato vigente (um por empresa)."""
        itens = [i for i in (itens or []) if i.get('descricao') and _d(i.get('valor'))]
        if not itens:
            raise ValueError('Informe ao menos uma discriminação com valor.')
        soma = sum(_d(i['valor']) for i in itens)
        if soma != _d(valor):
            raise ValueError('As discriminações somam R$ %s e o contrato diz R$ %s.' % (soma, _d(valor)))
        if FinContrato.por_cliente(cliente_id):
            raise ValueError('Esta empresa já tem contrato de honorários vigente. Edite o existente ou encerre-o.')
        if decimo_tipo not in DECIMO_TIPOS or reajuste_tipo not in REAJUSTE_TIPOS or situacao not in SITUACOES:
            raise ValueError('Modo de 13º, de reajuste ou situação inválidos.')
        if decimo_tipo == 'PERSONALIZADO':
            parc = decimo_parcelas or []
            if not parc or abs(sum(float(p.get('pct') or 0) for p in parc) - 100) > 0.01:
                raise ValueError('No 13º personalizado as frações precisam somar 100%.')
        if proximo_reajuste is None and reajuste_tipo == 'SALARIO_MINIMO':
            proximo_reajuste = primeiro_reajuste(inicio)
        cid = execute_query(
            """INSERT INTO fin_contratos (empresa_id, cliente_id, descricao, valor, dia_vencimento, categoria_id,
                   gerar_nfse, gerar_boleto, discriminacao, inicio, fim, reajuste_mes, ativo,
                   servico_id, plano, decimo_tipo, decimo_parcelas, reajuste_tipo, proximo_reajuste, reajuste_obs,
                   situacao, conexa_contrato_id, criado_por, aprovado_por, aprovado_em, decimo_desde, observacao)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (empresa_id, cliente_id, descricao, _d(valor), int(dia_vencimento), categoria_id, gerar_nfse, gerar_boleto,
             '; '.join('%s = R$ %s' % (i['descricao'], _d(i['valor'])) for i in itens), inicio, fim,
             1 if reajuste_tipo == 'SALARIO_MINIMO' else None, 1 if situacao == 'ATIVO' else 0,
             servico_id, plano, decimo_tipo, json.dumps(decimo_parcelas) if decimo_parcelas else None,
             reajuste_tipo, proximo_reajuste, reajuste_obs, situacao, conexa_contrato_id, criado_por,
             aprovado_por, None, decimo_desde, observacao), fetch=False)
        if aprovado_por:
            execute_query('UPDATE fin_contratos SET aprovado_em = NOW() WHERE id = %s', (cid,), fetch=False)
        FinContrato._gravar_itens(cid, itens)
        return cid

    @staticmethod
    def _gravar_itens(contrato_id, itens):
        execute_query('DELETE FROM fin_contrato_itens WHERE contrato_id = %s', (contrato_id,), fetch=False)
        for n, i in enumerate(itens, 1):
            execute_query('INSERT INTO fin_contrato_itens (contrato_id, servico_id, descricao, valor, ordem) VALUES (%s, %s, %s, %s, %s)',
                          (contrato_id, i.get('servico_id'), i['descricao'][:120], _d(i['valor']), n), fetch=False)

    @staticmethod
    def atualizar(contrato_id, dados, itens=None):
        """``dados``: só as chaves permitidas abaixo. Itens, quando vêm, substituem os antigos e
        precisam somar o valor."""
        permitidas = ('valor', 'dia_vencimento', 'inicio', 'fim', 'plano', 'decimo_tipo', 'decimo_parcelas', 'decimo_desde', 'observacao',
                      'reajuste_tipo', 'proximo_reajuste', 'reajuste_obs', 'situacao', 'gerar_nfse', 'gerar_boleto',
                      'servico_id', 'descricao', 'aprovado_por')
        sets, params = [], []
        for k in permitidas:
            if k in dados:
                v = dados[k]
                if k == 'decimo_parcelas' and v is not None:
                    v = json.dumps(v)
                sets.append('%s = %%s' % k); params.append(v)
        if 'situacao' in dados:
            sets.append('ativo = %s'); params.append(1 if dados['situacao'] == 'ATIVO' else 0)
        if dados.get('aprovado_por'):
            sets.append('aprovado_em = NOW()')
        if itens is not None:
            itens = [i for i in itens if i.get('descricao') and _d(i.get('valor'))]
            valor = _d(dados.get('valor', (FinContrato.get(contrato_id) or {}).get('valor')))
            soma = sum(_d(i['valor']) for i in itens)
            if soma != valor:
                raise ValueError('As discriminações somam R$ %s e o contrato diz R$ %s.' % (soma, valor))
        if sets:
            params.append(contrato_id)
            execute_query('UPDATE fin_contratos SET %s WHERE id = %%s' % ', '.join(sets), tuple(params), fetch=False)
        if itens is not None:
            FinContrato._gravar_itens(contrato_id, itens)

    @staticmethod
    def clientes_sem_contrato(empresa_id=EMPRESA_QUALICONTAX):
        """Clientes ativos (numerados, não avulsos) sem contrato vigente — a lista da Alessandra."""
        return execute_query(
            """SELECT cl.id, cl.numero_cliente, cl.nome_razao_social, cl.cpf_cnpj, cl.tipo_pessoa, cl.criado_por
                 FROM clientes cl
                WHERE cl.situacao = 'ATIVO' AND cl.avulso = 0
                  AND NOT EXISTS (SELECT 1 FROM fin_empresas fe WHERE fe.cliente_id = cl.id)   -- a própria Qualicontax, Brilho, Anderson PF
                  AND NOT EXISTS (SELECT 1 FROM fin_contratos c WHERE c.cliente_id = cl.id
                                   AND c.empresa_id = %s AND c.situacao IN ('PENDENTE','ATIVO'))
                ORDER BY cl.numero_cliente + 0""", (empresa_id,), fetch=True) or []

    @staticmethod
    def parcelas_decimo(contrato, ano):
        """[(competencia(date, dia 1), valor)] do 13º do ano, já proporcional aos meses de casa.
        DUAS: 50% na competência out + 50% na nov; UNICA: 100% na nov; PERSONALIZADO: o que foi digitado."""
        tipo = contrato.get('decimo_tipo') or 'NENHUM'
        if tipo == 'NENHUM':
            return []
        meses = meses_de_casa(contrato.get('decimo_desde') or contrato.get('inicio'), contrato.get('fim'), ano)
        if not meses:
            return []
        base = _d(contrato['valor']) * meses / 12
        if tipo == 'UNICA':
            fr = [(11, Decimal(100))]
        elif tipo == 'DUAS':
            fr = [(10, Decimal(50)), (11, Decimal(50))]
        else:
            parc = contrato.get('decimo_parcelas')
            if isinstance(parc, str):
                parc = json.loads(parc or '[]')
            fr = [(int(p['mes']), Decimal(str(p['pct']))) for p in (parc or [])]
        out, acumulado = [], Decimal('0')
        for n, (mes, pct) in enumerate(fr):
            v = (base * pct / 100).quantize(Decimal('0.01'))
            if n == len(fr) - 1:                       # a última parcela fecha o centavo
                v = base.quantize(Decimal('0.01')) - acumulado
            acumulado += v
            out.append((date(ano, mes, 1), v))
        return out

    @staticmethod
    def resumo(empresa_id=EMPRESA_QUALICONTAX):
        r = execute_query(
            """SELECT COUNT(*) n, COALESCE(SUM(valor), 0) total,
                      SUM(situacao = 'PENDENTE') pendentes, SUM(decimo_tipo <> 'NENHUM') com_decimo
                 FROM fin_contratos WHERE empresa_id = %s AND situacao IN ('PENDENTE','ATIVO')""",
            (empresa_id,), fetch=True, fetch_one=True) or {}
        r['sem_contrato'] = len(FinContrato.clientes_sem_contrato(empresa_id))
        return r
