"""Acordos de dívida (Cobranças › Acordos). Desenho aprovado pelo Anderson em 26/11/2026.

Um acordo junta títulos em aberto de um cliente (e valores que não estão no sistema), dá desconto se for o caso
(sem juros e sem correção, sempre) e divide em parcelas: junto com o honorário (mesmo dia) ou em boleto separado,
com entrada opcional. A regra de rompimento é escolhida em cada acordo. O termo (confissão de dívida) é assinado
pelo mesmo caminho do contrato; "sem assinatura" ativa na hora.

Ativar = os títulos da dívida saem da cobrança como "renegociados no AC-000N" (cancelados com fin_titulos.acordo_id;
o que já tinha pago fica pago) e nascem os títulos das parcelas (origem 'acordo').
"""
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)
CENT = Decimal('0.01')
MES = ['', 'jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
FORMA = {'JUNTO': 'junto com o honorário', 'SEPARADO': 'boleto separado'}
ROMPE = {'2_ATRASADAS': ('Rompe com 2 parcelas atrasadas', 'rompe com 2', 'parcelas atrasadas'),
         '1_30_DIAS': ('Rompe com 1 parcela 30 dias atrasada', 'rompe com 1', 'parcela 30 dias atrasada'),
         'SEM': ('Sem regra de rompimento', 'sem regra', 'atrasou, cai em Atrasados')}
ASSINATURA = {'ENVIAR': 'enviar para assinar', 'DEPOIS': 'guardar para enviar depois', 'SEM': 'sem assinatura (combinado)'}
SITUACAO = {'FALTA_ENVIAR': 'Falta enviar', 'FALTA_ASSINAR': 'Falta assinar', 'ATIVO': 'Ativo', 'QUITADO': 'Quitado',
            'ROMPIDO': 'Rompido', 'CANCELADO': 'Cancelado'}


def q(sql, params=(), one=False):
    r = execute_query(sql, params, fetch=True, fetch_one=one)
    return r if one else (r or [])


def x(sql, params=()):
    return execute_query(sql, params)


def d(v):
    return Decimal(str(v or 0)).quantize(CENT)


def brl(v):
    return 'R$ ' + f'{d(v):,.2f}'.replace(',', '#').replace('.', ',').replace('#', '.')


def hoje():
    from utils.titulos_receber import hoje_br
    return hoje_br()


def mes_rot(dt, longo=False):
    return f'{MES[dt.month]}/{dt.year if longo else str(dt.year)[2:]}' if dt else ''


def _dia_no_mes(ano, mes, dia):
    import calendar
    return date(ano, mes, min(int(dia), calendar.monthrange(ano, mes)[1]))


def _soma_mes(dt, n):
    a, m = dt.year + (dt.month - 1 + n) // 12, (dt.month - 1 + n) % 12 + 1
    return a, m


# ─────────────────────────── o cliente ───────────────────────────
def titulos_do_cliente(cliente_id):
    """Títulos a receber em aberto do cliente (fora os que já estão em acordo e as parcelas de acordo), o mais antigo
    primeiro, com saldo, dias de atraso e se tem boleto valendo."""
    h = hoje()
    rows = q("""SELECT t.id, t.empresa_id, t.descricao, t.competencia, t.vencimento, t.valor, t.valor_baixado, t.status,
                       t.origem, (SELECT COUNT(*) FROM fin_boletos b WHERE b.titulo_id = t.id
                                   AND b.situacao IN ('new', 'waiting', 'unpaid')) AS boletos_valendo
                  FROM fin_titulos t
                 WHERE t.cliente_id = %s AND t.tipo = 'R' AND t.status IN ('aberto', 'parcial')
                   AND t.valor - t.valor_baixado > 0.004 AND t.acordo_id IS NULL AND COALESCE(t.origem, '') <> 'acordo'
                 ORDER BY t.vencimento, t.id""", (cliente_id,))
    from utils.titulos_receber import _desc
    for r in rows:
        r['saldo'] = d(r['valor']) - d(r['valor_baixado'])
        r['dias'] = (h - r['vencimento']).days if r['vencimento'] else 0
        r['vencido'] = bool(r['vencimento'] and r['vencimento'] < h)
        r['desc'] = _desc(r)
    return rows


def contrato_do_cliente(cliente_id):
    """O contrato de honorários ativo: valor e dia (a parcela junto com o honorário vence no dia dele) e o banco."""
    return q("""SELECT id, empresa_id, valor, dia_vencimento, banco_boleto FROM fin_contratos
                 WHERE cliente_id = %s AND ativo = 1 AND COALESCE(situacao, 'ATIVO') = 'ATIVO'
                 ORDER BY id DESC LIMIT 1""", (cliente_id,), one=True)


def bancos(empresa_id):
    """Contas de cobrança que emitem boleto de verdade (produção, não só consulta)."""
    return q("""SELECT id, provedor, apelido FROM fin_cobranca_contas
                 WHERE empresa_id = %s AND ativo = 1 AND COALESCE(so_consulta, 0) = 0 AND ambiente = 'producao'
                 ORDER BY FIELD(provedor, 'bradesco', 'cora', 'efi'), id""", (empresa_id,))


def dados_do_cliente(cliente_id):
    """Tudo que a caixa Novo acordo precisa, do cadastro."""
    from models.prest_contrato import PrestContrato
    c = q('SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes WHERE id = %s', (cliente_id,), one=True)
    if not c:
        return None
    tits = titulos_do_cliente(cliente_id)
    k = contrato_do_cliente(cliente_id)
    emp = (k or {}).get('empresa_id') or (tits[0]['empresa_id'] if tits else 2)
    e = PrestContrato.empresa(cliente_id) or {}
    socios = [{'nome': s.get('nome') or '', 'cpf': s.get('cpf') or '', 'cargo': s.get('cargo') or 'sócio',
               'email': s.get('email') or '', 'celular': s.get('celular') or ''} for s in (e.get('socios') or [])]
    qc = [{'nome': v['nome'], 'cpf': v['cpf'], 'cargo': v['cargo'], 'email': v['email']}
          for v in PrestContrato.signatarios_qualicontax().values()]
    bs = bancos(emp)
    banco_padrao = None
    if k and str(k.get('banco_boleto') or '').isdigit() and any(b['id'] == int(k['banco_boleto']) for b in bs):
        banco_padrao = int(k['banco_boleto'])
    elif bs:
        from models.fin_cobranca import FinCobrancaConta
        p = FinCobrancaConta.padrao_da_empresa(emp)
        banco_padrao = p['id'] if p and any(b['id'] == p['id'] for b in bs) else bs[0]['id']
    return {'cliente': c, 'titulos': tits, 'contrato': k, 'empresa_id': emp, 'socios': socios, 'qc': qc,
            'bancos': bs, 'banco_padrao': banco_padrao, 'endereco': e.get('endereco') or ''}


# ─────────────────────────── as parcelas ───────────────────────────
def plano(total, n, ano, mes, dia, entrada=None, entrada_venc=None):
    """[(n, vencimento, valor, é_entrada)]. A entrada (se houver) é a parcela 0; o resto em n parcelas iguais e a
    última acerta os centavos."""
    total, out = d(total), []
    resto = total
    if entrada and d(entrada) > 0:
        out.append((0, entrada_venc, d(entrada), True))
        resto = total - d(entrada)
    n = max(int(n), 1)
    p = (resto / n).quantize(CENT, rounding=ROUND_DOWN)
    for i in range(n):
        a, m = _soma_mes(date(ano, mes, 1), i)
        v = p if i < n - 1 else resto - p * (n - 1)
        out.append((i + 1, _dia_no_mes(a, m, dia), v, False))
    return out


# ─────────────────────────── gravar ───────────────────────────
def proximo_numero():
    r = q("SELECT MAX(CAST(SUBSTRING(numero, 4) AS UNSIGNED)) n FROM fin_acordos WHERE numero LIKE 'AC-%%'", one=True) or {}
    return 'AC-%04d' % ((r.get('n') or 0) + 1)


def evento(acordo_id, texto, sub='', usuario_id=None, ico='fa-circle'):
    x('INSERT INTO fin_acordo_eventos (acordo_id, usuario_id, texto, sub, ico) VALUES (%s, %s, %s, %s, %s)',
      (acordo_id, usuario_id, texto[:255], (sub or '')[:255], ico))


class AcordoInvalido(Exception):
    pass


def validar(f, cli):
    """f = o formulário já lido (dict). Devolve o dict pronto para gravar ou levanta AcordoInvalido."""
    ids = {t['id']: t for t in cli['titulos']}
    escolhidos = [ids[i] for i in f['titulos'] if i in ids]
    if len(escolhidos) != len(set(f['titulos'])):
        raise AcordoInvalido('Algum título marcado já não está em aberto. Abra a caixa de novo.')
    outros = [(o['descricao'].strip()[:200], d(o['valor'])) for o in f.get('outros') or [] if o.get('descricao', '').strip() and d(o.get('valor')) > 0]
    divida = sum((t['saldo'] for t in escolhidos), Decimal('0')) + sum((v for _, v in outros), Decimal('0'))
    if divida <= 0:
        raise AcordoInvalido('Marque o que entra no acordo.')
    desc = Decimal('0')
    if f.get('desconto_tipo') == 'PCT':
        pct = d(f.get('desconto'))
        if not (0 < pct < 100):
            raise AcordoInvalido('O desconto em % tem de ser maior que 0 e menor que 100.')
        desc = (divida * pct / 100).quantize(CENT)
    elif f.get('desconto_tipo') == 'RS':
        desc = d(f.get('desconto'))
        if not (0 < desc < divida):
            raise AcordoInvalido('O desconto em R$ tem de ser maior que 0 e menor que a dívida.')
    if desc > 0 and len((f.get('motivo_desconto') or '').strip()) < 3:
        raise AcordoInvalido('Escreva o motivo do desconto.')
    total = divida - desc
    n = int(f.get('parcelas') or 0)
    if not 1 <= n <= 60:
        raise AcordoInvalido('Escolha em quantas parcelas (1 a 60).')
    forma = f.get('forma') if f.get('forma') in FORMA else 'SEPARADO'
    if forma == 'JUNTO' and not cli.get('contrato'):
        raise AcordoInvalido('O cliente não tem contrato de honorários ativo: use boleto separado.')
    dia = int((cli['contrato'] or {}).get('dia_vencimento') or 0) if forma == 'JUNTO' else int(f.get('dia') or 0)
    if not 1 <= dia <= 31:
        raise AcordoInvalido('Escolha o dia do vencimento das parcelas.')
    try:
        ano, mes = int(f['inicio'][:4]), int(f['inicio'][5:7])
        inicio = date(ano, mes, 1)
    except (KeyError, ValueError, TypeError):
        raise AcordoInvalido('Escolha o mês da primeira parcela.')
    h = hoje()
    if (ano, mes) < (h.year, h.month):
        raise AcordoInvalido('A primeira parcela não pode ser num mês que já passou.')
    entrada, entrada_venc = None, None
    if f.get('com_entrada'):
        entrada = d(f.get('entrada'))
        try:
            entrada_venc = date.fromisoformat(f.get('entrada_venc') or '')
        except ValueError:
            entrada_venc = None
        if not (0 < entrada < total):
            raise AcordoInvalido('A entrada tem de ser maior que 0 e menor que o total do acordo.')
        if not entrada_venc or entrada_venc < h:
            raise AcordoInvalido('Informe o vencimento da entrada (hoje ou depois).')
    pls = plano(total, n, ano, mes, dia, entrada, entrada_venc)
    if pls[-1][2] <= 0 or any(v <= 0 for _, _, v, _ in pls):
        raise AcordoInvalido('Com essa entrada não sobra valor para as parcelas.')
    if any(v < Decimal('5.00') for _, _, v, _ in pls):
        raise AcordoInvalido('Cada parcela tem de ser de pelo menos R$ 5,00 (mínimo do boleto).')
    if pls[1 if entrada else 0][1] < h:
        raise AcordoInvalido('A primeira parcela venceria antes de hoje: comece no mês seguinte.')
    rompe = f.get('rompe') if f.get('rompe') in ROMPE else '2_ATRASADAS'
    assinatura = f.get('assinatura') if f.get('assinatura') in ASSINATURA else 'ENVIAR'
    banco = int(f.get('banco') or 0)
    if not any(b['id'] == banco for b in cli['bancos']):
        raise AcordoInvalido('Escolha o banco do boleto.')
    ass = f.get('assina') or {}
    if assinatura != 'SEM' and (not (ass.get('nome') or '').strip() or not (ass.get('email') or '').strip()):
        raise AcordoInvalido('Quem assina pela empresa precisa de nome e e-mail (o código chega no e-mail).')
    qc = f.get('qc') or {}
    if assinatura != 'SEM' and not (qc.get('nome') or '').strip():
        raise AcordoInvalido('Escolha quem assina pela Qualicontax.')
    return {'titulos': escolhidos, 'outros': outros, 'divida': divida, 'desconto': desc, 'total': total,
            'motivo_desconto': (f.get('motivo_desconto') or '').strip()[:200], 'parcelas': n,
            'valor_parcela': pls[1 if entrada else 0][2], 'dia': dia, 'inicio': inicio, 'forma': forma, 'rompe': rompe,
            'assinatura': assinatura, 'banco': banco, 'entrada': entrada, 'entrada_venc': entrada_venc, 'plano': pls,
            'assina': ass, 'qc': qc}


def criar(v, cli, usuario):
    """Grava o acordo (rascunho de assinatura ou já ativo, se 'sem assinatura'). Devolve o id."""
    numero = proximo_numero()
    a, qc = v['assina'], v['qc']
    situacao = 'ATIVO' if v['assinatura'] == 'SEM' else ('FALTA_ASSINAR' if v['assinatura'] == 'ENVIAR' else 'FALTA_ENVIAR')
    aid = execute_query("""INSERT INTO fin_acordos (numero, empresa_id, cliente_id, situacao, divida, desconto, motivo_desconto,
                                total, entrada, entrada_venc, parcelas, valor_parcela, dia, inicio, forma, rompe,
                                conta_cobranca_id, assinatura, assina_nome, assina_cpf, assina_cargo, assina_email,
                                assina_celular, qc_nome, qc_cpf, qc_cargo, criado_por)
                         VALUES (%s, %s, %s, 'FALTA_ENVIAR', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                                 %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (numero, cli['empresa_id'], cli['cliente']['id'], v['divida'], v['desconto'], v['motivo_desconto'] or None,
                         v['total'], v['entrada'], v['entrada_venc'], v['parcelas'], v['valor_parcela'], v['dia'], v['inicio'],
                         v['forma'], v['rompe'], v['banco'], v['assinatura'], (a.get('nome') or '')[:150] or None,
                         (a.get('cpf') or '')[:20] or None, (a.get('cargo') or '')[:80] or None, (a.get('email') or '')[:150] or None,
                         (a.get('celular') or '')[:30] or None, (qc.get('nome') or '')[:150] or None, (qc.get('cpf') or '')[:20] or None,
                         (qc.get('cargo') or '')[:80] or None, usuario.id))
    if not aid or aid is True:
        raise AcordoInvalido('Não consegui gravar o acordo.')
    for t in v['titulos']:
        x("""INSERT INTO fin_acordo_titulos (acordo_id, titulo_id, descricao, vencimento, valor) VALUES (%s, %s, %s, %s, %s)""",
          (aid, t['id'], t['desc'][:200], t['vencimento'], t['saldo']))
    for desc, val in v['outros']:
        x('INSERT INTO fin_acordo_titulos (acordo_id, titulo_id, descricao, vencimento, valor) VALUES (%s, NULL, %s, NULL, %s)',
          (aid, desc, val))
    for n, venc, val, ent in v['plano']:
        x('INSERT INTO fin_acordo_parcelas (acordo_id, n, vencimento, valor, entrada) VALUES (%s, %s, %s, %s, %s)',
          (aid, n, venc, val, 1 if ent else 0))
    evento(aid, f'{usuario.nome} montou o acordo',
           f'{len(v["titulos"]) + len(v["outros"])} itens · {brl(v["total"])} · {v["parcelas"]}x {FORMA[v["forma"]]}',
           usuario.id, 'fa-handshake')
    if situacao == 'ATIVO':
        ativar(aid, usuario, 'Sem assinatura: combinado com o cliente')
    else:
        x('UPDATE fin_acordos SET situacao = %s WHERE id = %s', (situacao, aid))
    return aid


def _cancelar_boletos(titulo_id):
    """Boletos ainda pagáveis do título saem do banco (o cliente não paga a dívida velha por fora do acordo)."""
    from models.fin_cobranca import FinBoleto, FinCobrancaConta
    avisos = []
    for r in q("SELECT id FROM fin_boletos WHERE titulo_id = %s AND situacao IN ('new', 'waiting', 'unpaid')", (titulo_id,)):
        b = FinBoleto.get(r['id'])
        try:
            if b['provedor'] == 'bradesco':
                from utils import bradesco_boletos
                bradesco_boletos.cancelar(b)
            elif b['provedor'] == 'cora':
                from utils import cora_boletos
                cora_boletos.cancelar(b)
            else:
                efi, _ = FinCobrancaConta.cliente_efi(b['conta_cobranca_id'])
                efi.cancelar(b['charge_id'])
            FinBoleto.atualizar_situacao(b['id'], 'canceled')
        except Exception as exc:
            logger.exception('[acordo] cancelar boleto %s', b.get('id'))
            avisos.append(f'O banco não cancelou o boleto {FinBoleto.numero(b)} ({exc}): cancele pela tela do boleto.')
    return avisos


def ativar(aid, usuario, como=''):
    """O acordo começa a valer: os títulos da dívida saem da cobrança (renegociados) e nascem as parcelas."""
    from models.fin_titulo import FinTitulo
    from utils.financeiro_core import recalcular_status
    a = get(aid)
    if not a or a['situacao'] in ('ATIVO', 'QUITADO', 'ROMPIDO', 'CANCELADO'):
        return []
    avisos = []
    nota = f'{hoje().strftime("%d/%m/%Y")} — renegociado no acordo {a["numero"]}'
    for it in a['itens']:
        tid = it.get('titulo_id')
        if not tid:
            continue
        t = q('SELECT id, status, valor, valor_baixado FROM fin_titulos WHERE id = %s', (tid,), one=True)
        if not t or t['status'] not in ('aberto', 'parcial'):
            continue
        avisos += _cancelar_boletos(tid)
        if d(t['valor_baixado']) > 0:          # já recebeu parte: fica com o que pagou e o resto vai para o acordo
            x("""UPDATE fin_titulos SET valor = valor_baixado, acordo_id = %s,
                        observacao = CONCAT_WS('\n', NULLIF(observacao, ''), %s) WHERE id = %s""",
              (aid, f'{nota} (o saldo de {brl(d(t["valor"]) - d(t["valor_baixado"]))} foi para o acordo)', tid))
            recalcular_status(tid)
        else:
            FinTitulo.cancelar(tid)
            x("""UPDATE fin_titulos SET acordo_id = %s, observacao = CONCAT_WS('\n', NULLIF(observacao, ''), %s)
                  WHERE id = %s""", (aid, nota, tid))
    cli = q('SELECT nome_razao_social, cpf_cnpj FROM clientes WHERE id = %s', (a['cliente_id'],), one=True) or {}
    cat = q("SELECT id FROM fin_categorias WHERE tipo = 'R' AND nome LIKE 'Honor%%' ORDER BY id LIMIT 1", one=True) or {}
    for p in a['parcelas']:
        if p.get('titulo_id'):
            continue
        rot = 'entrada' if p['entrada'] else f'parcela {p["n"]}/{a["n_parcelas"]}'
        tid = FinTitulo.criar(tipo='R', contraparte_nome=cli.get('nome_razao_social') or '', categoria_id=cat.get('id'),
                              descricao=f'Acordo {a["numero"]} — {rot}', competencia=p['vencimento'].replace(day=1),
                              emissao=hoje(), vencimento=p['vencimento'], valor=p['valor'], empresa_id=a['empresa_id'],
                              contraparte_doc=''.join(ch for ch in (cli.get('cpf_cnpj') or '') if ch.isdigit()) or None,
                              cliente_id=a['cliente_id'],
                              observacao=f'Acordo {a["numero"]}: {FORMA[a["forma"]]}', origem='acordo',
                              chave_idem=f'acordo:{aid}:{p["n"]}')
        if not tid or tid is True:
            raise AcordoInvalido(f'Não consegui criar o título da {rot}. Avise o suporte: o acordo {a["numero"]} ficou pela metade.')
        x('UPDATE fin_acordo_parcelas SET titulo_id = %s WHERE id = %s', (tid, p['id']))
    x("UPDATE fin_acordos SET situacao = 'ATIVO', ativado_em = NOW() WHERE id = %s", (aid,))
    evento(aid, 'O acordo começou a valer', como or '', getattr(usuario, 'id', None), 'fa-circle-check')
    from utils.atividade import registrar
    registrar('escrita.ativou_acordo', 'financeiro', tabela='fin_acordos', registro_id=aid,
              depois={'numero': a['numero'], 'titulos': [i['titulo_id'] for i in a['itens'] if i.get('titulo_id')],
                      'total': str(a['total']), 'parcelas': a['n_parcelas'], 'como': como})
    return avisos


def cancelar(aid, motivo, usuario):
    """Desfaz o acordo enquanto nenhuma parcela foi paga: os títulos voltam como estavam e as parcelas somem."""
    from models.fin_titulo import FinTitulo
    from utils.financeiro_core import recalcular_status
    a = get(aid)
    if not a or a['situacao'] in ('CANCELADO', 'QUITADO', 'ROMPIDO'):
        raise AcordoInvalido('Este acordo não pode mais ser cancelado.')
    if any(p['pago'] > 0 for p in a['parcelas']):
        raise AcordoInvalido('Já tem parcela paga: não dá para cancelar. Fale com o financeiro.')
    for p in a['parcelas']:
        if p.get('titulo_id'):
            _cancelar_boletos(p['titulo_id'])
            FinTitulo.cancelar(p['titulo_id'])
    for it in a['itens']:
        tid = it.get('titulo_id')
        if not tid:
            continue
        t = q('SELECT id, status, valor, valor_baixado FROM fin_titulos WHERE id = %s AND acordo_id = %s', (tid, aid), one=True)
        if not t:
            continue
        nota = f'{hoje().strftime("%d/%m/%Y")} — o acordo {a["numero"]} foi cancelado; voltou a cobrar'
        if d(t['valor_baixado']) > 0:
            x("""UPDATE fin_titulos SET valor = valor + %s, acordo_id = NULL,
                        observacao = CONCAT_WS('\n', NULLIF(observacao, ''), %s) WHERE id = %s""", (d(it['valor']), nota, tid))
            recalcular_status(tid)
        else:
            x("""UPDATE fin_titulos SET status = 'aberto', acordo_id = NULL,
                        observacao = CONCAT_WS('\n', NULLIF(observacao, ''), %s) WHERE id = %s AND status = 'cancelado'""", (nota, tid))
    x("""UPDATE fin_acordos SET situacao = 'CANCELADO', encerrado_em = NOW(), encerrado_motivo = %s WHERE id = %s""",
      (motivo[:255], aid))
    evento(aid, f'{usuario.nome} cancelou o acordo', motivo, usuario.id, 'fa-ban')
    from utils.atividade import registrar
    registrar('escrita.cancelou_acordo', 'financeiro', tabela='fin_acordos', registro_id=aid,
              depois={'numero': a['numero'], 'motivo': motivo})


# ─────────────────────────── ler ───────────────────────────
def get(aid):
    a = q("""SELECT a.*, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj, u.nome AS criado_por_nome,
                    cc.provedor AS banco_provedor, cc.apelido AS banco_apelido
               FROM fin_acordos a JOIN clientes c ON c.id = a.cliente_id
               LEFT JOIN usuarios u ON u.id = a.criado_por
               LEFT JOIN fin_cobranca_contas cc ON cc.id = a.conta_cobranca_id
              WHERE a.id = %s""", (aid,), one=True)
    if not a:
        return None
    a['n_parcelas'] = a['parcelas']          # a coluna é a quantidade; a['parcelas'] vira a lista abaixo
    a['itens'] = q('SELECT * FROM fin_acordo_titulos WHERE acordo_id = %s ORDER BY vencimento IS NULL, vencimento, id', (aid,))
    a['parcelas'] = q("""SELECT p.*, t.status AS t_status, t.valor_baixado AS pago, t.vencimento AS t_venc,
                                (SELECT MAX(x.data_baixa) FROM fin_titulo_baixas x WHERE x.titulo_id = p.titulo_id) AS pago_em
                           FROM fin_acordo_parcelas p LEFT JOIN fin_titulos t ON t.id = p.titulo_id
                          WHERE p.acordo_id = %s ORDER BY p.n""", (aid,))
    h = hoje()
    for p in a['parcelas']:
        p['pago'] = d(p.get('pago'))
        p['venc'] = p.get('t_venc') or p['vencimento']
        p['sit'] = ('paga' if p.get('t_status') == 'liquidado' else
                    'cancelada' if p.get('t_status') == 'cancelado' else
                    'atrasada' if (p.get('titulo_id') and p['venc'] < h) else
                    'a vencer' if p.get('titulo_id') else 'espera assinar')
    a['eventos'] = q("""SELECT e.*, u.nome AS quem FROM fin_acordo_eventos e LEFT JOIN usuarios u ON u.id = e.usuario_id
                         WHERE e.acordo_id = %s ORDER BY e.id""", (aid,))
    return resumo(a)


def resumo(a):
    """Os números que a aba e a janela mostram: pagas, atrasadas, próxima, situação de tela e a cor."""
    ps = a['parcelas']
    a['n_pagas'] = sum(1 for p in ps if p['sit'] == 'paga')
    a['n_atrasadas'] = sum(1 for p in ps if p['sit'] == 'atrasada')
    a['pago_total'] = sum((p['pago'] for p in ps), Decimal('0'))
    a['proxima'] = next((p for p in ps if p['sit'] in ('atrasada', 'a vencer', 'espera assinar')), None)
    s = a['situacao']
    if s == 'ATIVO' and ps and a['n_pagas'] == len(ps):
        s = 'QUITADO'
    a['tela'] = {'FALTA_ENVIAR': ('Falta enviar', 'ass'), 'FALTA_ASSINAR': ('Falta assinar', 'ass'),
                 'QUITADO': ('Quitado', 'qui'), 'ROMPIDO': ('Rompido', 'rom'), 'CANCELADO': ('Cancelado', 'qui')}.get(
        s, ('Parcela atrasada', 'atr') if a['n_atrasadas'] else ('Em dia', ''))
    a['filtro'] = {'ass': 'ass', 'atr': 'atr', 'qui': 'qui' if s == 'QUITADO' else 'canc', 'rom': 'rom', '': 'dia'}[a['tela'][1]]
    venc = [i['vencimento'] for i in a['itens'] if i.get('vencimento')]
    a['periodo'] = (f'{mes_rot(min(venc))} a {mes_rot(max(venc))}' if venc and (min(venc).year, min(venc).month) != (max(venc).year, max(venc).month)
                    else (mes_rot(venc[0]) if venc else 'valores avulsos'))
    a['n_itens'] = len(a['itens'])
    ent = next((p for p in ps if p['entrada']), None)
    a['texto_parcelas'] = (('entrada + ' if ent else '') + f'{a["n_parcelas"]}x {brl(a["valor_parcela"])}')
    return a


def listar(empresa_ids=None):
    cond, params = '', []
    if empresa_ids:
        cond = ' WHERE empresa_id IN (%s)' % ','.join(['%s'] * len(empresa_ids))
        params = list(empresa_ids)
    try:
        ids = [r['id'] for r in q('SELECT id FROM fin_acordos' + cond + ' ORDER BY id DESC', tuple(params))]
    except Exception:                       # tabela ainda não criada
        return []
    return [get(i) for i in ids]
