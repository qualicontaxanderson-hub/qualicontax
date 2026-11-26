"""Acordos de dívida (Cobranças › Acordos). Desenho aprovado pelo Anderson em 26/11/2026.

Um acordo junta títulos em aberto de um cliente (e valores que não estão no sistema), dá desconto se for o caso
(sem juros e sem correção, sempre) e divide em parcelas: junto com o honorário (mesmo dia) ou em boleto separado,
com entrada opcional. A regra de rompimento é escolhida em cada acordo. O termo (confissão de dívida) é assinado
pelo mesmo caminho do contrato; "sem assinatura" ativa na hora.

Ativar = os títulos da dívida saem da cobrança como "renegociados no AC-000N" (cancelados com fin_titulos.acordo_id;
o que já tinha pago fica pago) e nascem os títulos das parcelas (origem 'acordo').

Etapa 3 (26/11/2026) — o boleto das parcelas:
* a ENTRADA sai em boleto próprio na hora em que o acordo começa a valer;
* "boleto separado": cada parcela ganha o boleto ``JANELA_SEPARADO`` dias antes de vencer (o cron_roteador passa
  de hora em hora; no máximo uma tentativa por parcela por dia);
* "junto com o honorário": a parcela vai como linha "só no boleto" (fora da nota) no boleto do mês, pelo lote de
  honorários (utils/lote_mensal). Dado o boleto, o título da parcela sai da cobrança (absorvido) e a parcela passa a
  apontar para o título do boleto do mês (``titulo_proprio_id`` guarda o dela). Se o lote não a levar até
  ``JANELA_JUNTO`` dias antes do vencimento, ela sai em boleto próprio — nunca fica sem cobrar.

Etapa 4 (26/11/2026) — o rompimento. Anderson: "quando o acordo rompe, o desconto cai" (opção A, e isso tem de estar
no termo). Bateu a regra do acordo (2 parcelas atrasadas, ou 1 com mais de 30 dias), o cron rompe sozinho:
* as parcelas que faltam saem da cobrança (boleto cancelado no banco; título cancelado com acordo_id);
* o desconto é cancelado: o cliente volta a dever a DÍVIDA ORIGINAL menos tudo o que já pagou no acordo, numa
  cobrança só (origem 'acordo_rompido', chave ``acordo:<id>:rompido``), vencida desde a 1ª parcela atrasada — vai
  para Atrasados e pode entrar num acordo novo;
* parcela "junto" que já foi no boleto do honorário e não foi paga continua dentro daquele boleto (não se cobra duas
  vezes): ela sai da conta da cobrança nova.
A parcela só conta como atrasada para romper ``TOLERANCIA_ROMPE`` dias depois do vencimento (o banco leva um ou dois
dias para mandar a baixa de quem pagou no dia).
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
JANELA_SEPARADO = 20     # dias antes do vencimento em que o boleto da parcela "separado" é emitido
JANELA_JUNTO = 5         # "junto": se o lote do mês não levou, boleto próprio a esta distância do vencimento
TOLERANCIA_ROMPE = 3     # etapa 4: dias depois do vencimento até a parcela contar para o rompimento "2 atrasadas"
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
        v['avisos'] = ativar(aid, usuario, 'Sem assinatura: combinado com o cliente')
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
    try:                                     # etapa 3: entrada e parcelas que já estão perto ganham o boleto agora
        avisos += [r['msg'] for r in emitir_pendentes(aid, getattr(usuario, 'id', None))]
    except Exception:
        logger.exception('[acordo] boletos do acordo %s', aid)
        avisos.append('Os boletos não saíram agora; o sistema tenta de novo em até uma hora.')
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
    junto = [p for p in a['parcelas'] if p.get('junto')]
    if junto:
        raise AcordoInvalido(f'A parcela {junto[0]["n"]} já foi no boleto do honorário (cobrança nº {junto[0]["titulo_id"]}): '
                             'não dá para cancelar o acordo por aqui. Fale com o financeiro.')
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


# ─────────────────────────── boletos das parcelas (etapa 3) ───────────────────────────
def rotulo(a, p):
    return 'entrada' if p['entrada'] else f'parcela {p["n"]}/{a["n_parcelas"]}'


def _tem_boleto(titulo_id, hoje_tambem=False):
    """Boleto ainda valendo (ou, com ``hoje_tambem``, qualquer tentativa de hoje: não repete o erro de hora em hora)."""
    if q("SELECT 1 x FROM fin_boletos WHERE titulo_id = %s AND situacao NOT IN ('canceled', 'erro', 'expired') LIMIT 1",
         (titulo_id,), one=True):
        return True
    return bool(hoje_tambem and q('SELECT 1 x FROM fin_boletos WHERE titulo_id = %s AND DATE(criado_em) = CURDATE() LIMIT 1',
                                  (titulo_id,), one=True))


def _conta(a):
    from models.fin_cobranca import FinCobrancaConta
    c = FinCobrancaConta.get(a['conta_cobranca_id']) if a.get('conta_cobranca_id') else None
    if not c or not c.get('ativo') or c.get('empresa_id') != a['empresa_id']:
        c = FinCobrancaConta.padrao_da_empresa(a['empresa_id'])
    return c


def emitir_boleto_parcela(a, p, usuario_id=None):
    """Boleto de UMA parcela (título dela, sem nota: dívida renegociada não é serviço novo). Devolve (ok, msg)."""
    from models.nfse_emissao import tomador_do_cliente
    from routes.financeiro_cobranca import _pagador_do_tomador, emitir_boleto_avulso
    rot = rotulo(a, p)
    t = q('SELECT id, status, valor, valor_baixado, vencimento FROM fin_titulos WHERE id = %s', (p['titulo_id'],), one=True)
    if not t or t['status'] not in ('aberto', 'parcial'):
        return False, f'{rot.capitalize()}: a cobrança não está em aberto.'
    if _tem_boleto(t['id']):
        return False, f'{rot.capitalize()}: já tem boleto.'
    conta = _conta(a)
    if not conta:
        return False, f'{rot.capitalize()}: a empresa não tem conta de cobrança ativa.'
    tom = tomador_do_cliente(a['cliente_id'], consultar_cep=False)
    if not tom or not tom.get('documento'):
        return False, f'{rot.capitalize()}: o cadastro do cliente está sem CPF/CNPJ.'
    valor = d(t['valor']) - d(t['valor_baixado'])
    venc = max(t['vencimento'], hoje())
    nome = f'Acordo {a["numero"]} — {rot}'
    bid, ok, msg, aviso = emitir_boleto_avulso(
        a['empresa_id'], conta, _pagador_do_tomador(tom),
        [{'servico_id': None, 'nome': nome, 'valor': valor, 'emite_nota': False}], valor, p['vencimento'].replace(day=1),
        venc, f'{nome} (termo de acordo / confissão de dívida).', t['id'], usuario_id)
    if not ok:
        logger.warning('[acordo] boleto da %s do %s: %s', rot, a['numero'], msg)
        return False, f'{rot.capitalize()}: o banco não emitiu o boleto ({msg}).'
    evento(a['id'], f'Boleto da {rot} emitido', f'{brl(valor)} · vence {venc.strftime("%d/%m/%Y")}'
           + (f' · {aviso}' if aviso else ''), usuario_id, 'fa-barcode')
    return True, f'Boleto da {rot} emitido: {brl(valor)}, vence {venc.strftime("%d/%m/%Y")}.' + (f' {aviso}' if aviso else '')


def emitir_pendentes(aid=None, usuario_id=None):
    """Emite o que já está na hora: a entrada sempre; "separado" a JANELA_SEPARADO dias do vencimento; "junto" que o
    lote não levou, a JANELA_JUNTO dias. Devolve [{acordo, ok, msg}]. Seguro rodar de hora em hora."""
    h = hoje()
    cond, par = ('AND a.id = %s', (aid,)) if aid else ('', ())
    rows = q(f"""SELECT p.*, a.forma FROM fin_acordo_parcelas p JOIN fin_acordos a ON a.id = p.acordo_id
                   JOIN fin_titulos t ON t.id = p.titulo_id
                  WHERE a.situacao = 'ATIVO' AND p.titulo_proprio_id IS NULL AND t.status IN ('aberto', 'parcial') {cond}
                  ORDER BY a.id, p.n""", par)
    out, cache = [], {}
    for p in rows:
        dias = (p['vencimento'] - h).days
        if not (p['entrada'] or dias <= (JANELA_SEPARADO if p['forma'] == 'SEPARADO' else JANELA_JUNTO)):
            continue
        if _tem_boleto(p['titulo_id'], hoje_tambem=not aid):
            continue
        a = cache.get(p['acordo_id']) or cache.setdefault(p['acordo_id'], get(p['acordo_id']))
        try:
            ok, msg = emitir_boleto_parcela(a, p, usuario_id)
        except Exception as exc:
            logger.exception('[acordo] boleto da parcela %s', p['id'])
            ok, msg = False, f'{rotulo(a, p).capitalize()}: erro ao emitir o boleto ({exc}).'
        out.append({'acordo': a['numero'], 'ok': ok, 'msg': msg})
    return out


def emitir_uma(aid, pid, usuario_id=None):
    """Botão "Emitir boleto agora" da janela (qualquer parcela em aberto sem boleto). Devolve (ok, msg)."""
    a = get(aid)
    p = next((x for x in (a or {}).get('parcelas', []) if x['id'] == pid), None)
    if not a or not p or a['situacao'] != 'ATIVO':
        return False, 'O acordo não está valendo.'
    if p.get('junto'):
        return False, 'Esta parcela já foi no boleto do honorário.'
    return emitir_boleto_parcela(a, p, usuario_id)


def parcelas_junto(empresa_id, ate):
    """Parcelas "junto com o honorário" que vão no boleto do mês (vencem até ``ate``), por cliente: {cliente_id: [itens]}.
    Item no formato do lote (utils/lote_mensal.itens_da_empresa), origem 'acordo', sem serviço (fora da nota)."""
    rows = q("""SELECT p.id, p.n, p.vencimento, p.titulo_id, a.cliente_id, a.numero, a.parcelas AS n_parcelas,
                       t.valor - t.valor_baixado AS saldo
                  FROM fin_acordo_parcelas p JOIN fin_acordos a ON a.id = p.acordo_id
                  JOIN fin_titulos t ON t.id = p.titulo_id
                 WHERE a.empresa_id = %s AND a.situacao = 'ATIVO' AND a.forma = 'JUNTO' AND p.entrada = 0
                   AND p.titulo_proprio_id IS NULL AND t.status IN ('aberto', 'parcial') AND p.vencimento <= %s
                   AND NOT EXISTS (SELECT 1 FROM fin_boletos b WHERE b.titulo_id = p.titulo_id
                                      AND b.situacao NOT IN ('canceled', 'erro', 'expired'))
                 ORDER BY p.vencimento, p.n""", (empresa_id, ate))
    out = {}
    for r in rows:
        if d(r['saldo']) <= 0:
            continue
        out.setdefault(r['cliente_id'], []).append({
            'origem': 'acordo', 'servico_id': None, 'acordo_parcela_id': r['id'],
            'nome': f'Acordo {r["numero"]} — parcela {r["n"]}/{r["n_parcelas"]} (venc. {r["vencimento"].strftime("%d/%m/%Y")})',
            'valor': str(d(r['saldo']))})
    return out


def absorver(parcela_ids, titulo_id, onde):
    """Dado o boleto do mês, as parcelas "junto" passam a ser cobradas por ele: o título próprio sai da cobrança
    (cancelado, com acordo_id — não vai para Cancelados) e a parcela aponta para o título do boleto do mês."""
    from models.fin_titulo import FinTitulo
    for pid in parcela_ids or []:
        p = q("""SELECT p.*, a.numero, a.parcelas AS n_parcelas FROM fin_acordo_parcelas p JOIN fin_acordos a ON a.id = p.acordo_id
                  WHERE p.id = %s AND p.titulo_proprio_id IS NULL""", (pid,), one=True)
        if not p or not p.get('titulo_id'):
            continue
        proprio = p['titulo_id']
        x('UPDATE fin_acordo_parcelas SET titulo_proprio_id = %s, titulo_id = %s WHERE id = %s', (proprio, titulo_id, pid))
        FinTitulo.cancelar(proprio)
        x("""UPDATE fin_titulos SET acordo_id = %s, observacao = CONCAT_WS('\n', NULLIF(observacao, ''), %s) WHERE id = %s""",
          (p['acordo_id'], f'{hoje().strftime("%d/%m/%Y")} — cobrada no boleto do honorário (cobrança nº {titulo_id})', proprio))
        evento(p['acordo_id'], f'A parcela {p["n"]}/{p["n_parcelas"]} foi no boleto do honorário', f'{onde} · cobrança nº {titulo_id}',
               None, 'fa-barcode')


# ─────────────────────────── rompimento (etapa 4) ───────────────────────────
def motivo_rompimento(a, h=None):
    """O texto do motivo se a regra do acordo já bateu; None se não. Só acordo ATIVO e não quitado."""
    h = h or hoje()
    if a['situacao'] != 'ATIVO' or a['rompe'] not in ('2_ATRASADAS', '1_30_DIAS'):
        return None
    ps = a['parcelas']
    if not ps or all(p['sit'] == 'paga' for p in ps):
        return None
    atr = [p for p in ps if p['sit'] == 'atrasada']
    if a['rompe'] == '2_ATRASADAS':
        conta = [p for p in atr if (h - p['venc']).days > TOLERANCIA_ROMPE]
        if len(conta) >= 2:
            return '2 parcelas atrasadas (%s)' % ' e '.join(
                f'{rotulo(a, p)} de {p["venc"].strftime("%d/%m/%Y")}' for p in conta[:2])
    else:
        conta = [p for p in atr if (h - p['venc']).days > 30]
        if conta:
            p = conta[0]
            return f'{rotulo(a, p)} de {p["venc"].strftime("%d/%m/%Y")} com {(h - p["venc"]).days} dias de atraso'
    return None


def saldo_do_rompimento(a):
    """Quanto o cliente volta a dever se o acordo romper (o desconto cai) e quanto disso vira a cobrança nova.
    Devolve {cheio, pago, no_honorario, nova}."""
    pago = sum((p['pago'] for p in a['parcelas']), Decimal('0'))
    no_hon = sum((d(p['valor']) - p['pago'] for p in a['parcelas'] if p.get('junto') and p['sit'] != 'paga'), Decimal('0'))
    cheio = max(d(a['divida']) - pago, Decimal('0'))
    return {'cheio': cheio, 'pago': pago, 'no_honorario': no_hon, 'nova': max(cheio - no_hon, Decimal('0'))}


def romper(aid, motivo, usuario=None):
    """Rompe o acordo (opção A: o desconto cai). Devolve {'titulo_id', 'valor', 'avisos'}."""
    from models.fin_titulo import FinTitulo
    from utils.financeiro_core import recalcular_status
    a = get(aid)
    if not a or a['situacao'] != 'ATIVO':
        raise AcordoInvalido('Só um acordo valendo pode ser rompido.')
    uid = getattr(usuario, 'id', None)
    h = hoje()
    sal = saldo_do_rompimento(a)
    avisos = []
    nota = f'{h.strftime("%d/%m/%Y")} — o acordo {a["numero"]} foi rompido ({motivo})'
    # 1) as parcelas que faltam saem da cobrança (a "junto" já absorvida fica no boleto do honorário)
    for p in a['parcelas']:
        if p.get('junto') or not p.get('titulo_id'):
            continue
        t = q('SELECT id, status, valor_baixado FROM fin_titulos WHERE id = %s', (p['titulo_id'],), one=True)
        if not t or t['status'] not in ('aberto', 'parcial'):
            continue
        avisos += _cancelar_boletos(t['id'])
        if d(t['valor_baixado']) > 0:
            x("""UPDATE fin_titulos SET valor = valor_baixado, acordo_id = %s,
                        observacao = CONCAT_WS('\n', NULLIF(observacao, ''), %s) WHERE id = %s""", (aid, nota, t['id']))
            recalcular_status(t['id'])
        else:
            FinTitulo.cancelar(t['id'])
            x("""UPDATE fin_titulos SET acordo_id = %s, observacao = CONCAT_WS('\n', NULLIF(observacao, ''), %s)
                  WHERE id = %s""", (aid, nota, t['id']))
    # 2) a dívida volta sem o desconto, menos o que já pagou, numa cobrança só
    tid = None
    if sal['nova'] > 0:
        cli = q('SELECT nome_razao_social, cpf_cnpj FROM clientes WHERE id = %s', (a['cliente_id'],), one=True) or {}
        cat = q("SELECT id FROM fin_categorias WHERE tipo = 'R' AND nome LIKE 'Honor%%' ORDER BY id LIMIT 1", one=True) or {}
        atr = [p['venc'] for p in a['parcelas'] if p['sit'] == 'atrasada']
        venc = min(atr) if atr else h
        obs = (f'Acordo {a["numero"]} rompido em {h.strftime("%d/%m/%Y")}: {motivo}. Dívida original {brl(a["divida"])}'
               + (f', desconto de {brl(a["desconto"])} cancelado' if d(a['desconto']) > 0 else '')
               + f', já pago no acordo {brl(sal["pago"])}'
               + (f', {brl(sal["no_honorario"])} continuam no boleto do honorário' if sal['no_honorario'] > 0 else '') + '.')
        tid = FinTitulo.criar(tipo='R', contraparte_nome=cli.get('nome_razao_social') or '', categoria_id=cat.get('id'),
                              descricao=f'Acordo {a["numero"]} rompido — saldo da dívida', competencia=h.replace(day=1),
                              emissao=h, vencimento=venc, valor=sal['nova'], empresa_id=a['empresa_id'],
                              contraparte_doc=''.join(ch for ch in (cli.get('cpf_cnpj') or '') if ch.isdigit()) or None,
                              cliente_id=a['cliente_id'], observacao=obs, origem='acordo_rompido',
                              chave_idem=f'acordo:{aid}:rompido')
        if not tid or tid is True:
            raise AcordoInvalido(f'Não consegui criar a cobrança do saldo do acordo {a["numero"]}.')
    x("""UPDATE fin_acordos SET situacao = 'ROMPIDO', encerrado_em = NOW(), encerrado_motivo = %s WHERE id = %s""",
      (('Rompido: ' + motivo)[:255], aid))
    sub = (f'desconto de {brl(a["desconto"])} cancelado · ' if d(a['desconto']) > 0 else '') + \
          (f'volta a dever {brl(sal["cheio"])} (cobrança nº {tid})' if tid else 'nada a cobrar além do boleto do honorário')
    evento(aid, ('O acordo foi rompido: ' + motivo) if not uid else f'{usuario.nome} rompeu o acordo: {motivo}', sub, uid,
           'fa-link-slash')
    from utils.atividade import registrar
    registrar('escrita.rompeu_acordo', 'financeiro', tabela='fin_acordos', registro_id=aid,
              depois={'numero': a['numero'], 'motivo': motivo, 'titulo_saldo': tid, 'valor': str(sal['nova']),
                      'desconto_cancelado': str(a['desconto']), 'pago': str(sal['pago'])})
    return {'titulo_id': tid, 'valor': sal['nova'], 'avisos': avisos}


def romper_vencidos():
    """Cron: rompe os acordos em que a regra bateu. Devolve [{acordo, msg}]. Seguro rodar de hora em hora."""
    out = []
    for r in q("SELECT id FROM fin_acordos WHERE situacao = 'ATIVO' AND rompe IN ('2_ATRASADAS', '1_30_DIAS')"):
        a = get(r['id'])
        m = motivo_rompimento(a) if a else None
        if not m:
            continue
        try:
            res = romper(a['id'], m)
            out.append({'acordo': a['numero'], 'msg': f'rompido ({m}); saldo {brl(res["valor"])}'
                        + (f' na cobrança nº {res["titulo_id"]}' if res['titulo_id'] else '')})
        except Exception as exc:
            logger.exception('[acordo] romper %s', a['numero'])
            out.append({'acordo': a['numero'], 'msg': f'não rompeu ({exc})'})
    return out


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
                                (SELECT MAX(x.data_baixa) FROM fin_titulo_baixas x WHERE x.titulo_id = p.titulo_id) AS pago_em,
                                (SELECT b.id FROM fin_boletos b WHERE b.titulo_id = p.titulo_id
                                    AND b.situacao NOT IN ('canceled', 'erro', 'expired') ORDER BY b.id DESC LIMIT 1) AS boleto_id,
                                (SELECT b.vencimento FROM fin_boletos b WHERE b.titulo_id = p.titulo_id
                                    AND b.situacao NOT IN ('canceled', 'erro', 'expired') ORDER BY b.id DESC LIMIT 1) AS boleto_venc
                           FROM fin_acordo_parcelas p LEFT JOIN fin_titulos t ON t.id = p.titulo_id
                          WHERE p.acordo_id = %s ORDER BY p.n""", (aid,))
    h = hoje()
    for p in a['parcelas']:
        # etapa 3: a parcela que foi no boleto do honorário aponta para o título do mês; paga = o título do mês pago
        p['junto'] = bool(p.get('titulo_proprio_id')) and p.get('titulo_id') != p.get('titulo_proprio_id')
        p['pago'] = min(d(p.get('pago')), d(p['valor'])) if p['junto'] else d(p.get('pago'))
        p['venc'] = p.get('t_venc') or p['vencimento']
        p['sit'] = ('paga' if p.get('t_status') == 'liquidado' else
                    'cancelada' if p.get('t_status') == 'cancelado' else
                    'atrasada' if (p.get('titulo_id') and p['venc'] < h) else
                    'a vencer' if p.get('titulo_id') else 'espera assinar')
    a['eventos'] = q("""SELECT e.*, u.nome AS quem FROM fin_acordo_eventos e LEFT JOIN usuarios u ON u.id = e.usuario_id
                         WHERE e.acordo_id = %s ORDER BY e.id""", (aid,))
    a['saldo_romper'] = None
    if a['situacao'] == 'ROMPIDO':        # etapa 4: a cobrança do saldo (a dívida sem o desconto, menos o que pagou)
        a['saldo_romper'] = q("""SELECT id, valor, valor_baixado, status, vencimento FROM fin_titulos
                                  WHERE chave_idem = %s LIMIT 1""", (f'acordo:{aid}:rompido',), one=True)
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
