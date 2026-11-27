"""E-mail de agradecimento quando o cliente PAGA (27/11/2026).

Anderson: "quando o cliente PAGAR quero que envie o e-mail de Agradecimento e informar que foi pago".
Regras dele:
- só quando a cobrança fica paga por INTEIRO (pagamento parcial não manda);
- baixa automática (banco ou extrato) manda sozinha;
- baixa feita à mão pela equipe: quem baixa escolhe se manda ou não.
Mesmo molde e mesmos destinatários do e-mail da cobrança (utils/cobranca_email).
"""
import json
from datetime import date
from decimal import Decimal

from utils.db_helper import execute_query
from utils import cobranca_email as CE


def _q(sql, p=(), one=False):
    return execute_query(sql, p, fetch=True, fetch_one=one)


def _itens(t, b):
    itens = (b or {}).get('itens_json') or []
    if isinstance(itens, str):
        try:
            itens = json.loads(itens)
        except ValueError:
            itens = []
    linhas = [{'nome': i.get('nome') or '', 'valor': CE._brl(i.get('valor'))} for i in itens if i.get('nome')]
    if not linhas:
        desc = (t.get('descricao') or 'Honorários e serviços').split(' — ')[-1]
        linhas = [{'nome': desc, 'valor': CE._brl(t['valor'])}]
    return linhas


def montar(titulo_id):
    """(assunto, html, texto) do agradecimento do título pago. None se o título não está liquidado."""
    from utils.texto import nome_proprio
    t = _q('SELECT * FROM fin_titulos WHERE id = %s', (titulo_id,), one=True)
    if not t or t['status'] != 'liquidado' or t['tipo'] != 'R':
        return None
    b = _q('SELECT * FROM fin_boletos WHERE id = %s', (t['boleto_id'],), one=True) if t.get('boleto_id') else None
    if not b:
        b = _q("SELECT * FROM fin_boletos WHERE titulo_id = %s AND situacao = 'paid' ORDER BY id DESC LIMIT 1",
               (titulo_id,), one=True)
    bx = _q('SELECT data_baixa, SUM(valor) v, SUM(juros + multa) jm FROM fin_titulo_baixas WHERE titulo_id = %s '
            'GROUP BY data_baixa ORDER BY data_baixa DESC', (titulo_id,)) or []
    pago_em = bx[0]['data_baixa'] if bx else date.today()
    pago = sum((Decimal(str(r['v'] or 0)) for r in bx), Decimal(0))
    nota = None
    nid = t.get('nfse_id') or (b or {}).get('nfse_id')
    if nid:
        nota = _q('SELECT * FROM nfse_emitidas WHERE id = %s', (nid,), one=True)
    tem_nota = bool(nota and nota.get('numero_nfse') and nota.get('situacao') == 'autorizada')

    empresa = CE._empresa(t['empresa_id'])
    telefone = CE._telefone_empresa(t['empresa_id'])
    comp = CE._competencia_txt(nota) if tem_nota else CE._competencia_txt(t)
    doc = (b or {}).get('pagador_doc') or t.get('contraparte_doc') or ''
    cliente_nome = nome_proprio((b or {}).get('pagador_nome') or t.get('contraparte_nome') or '') or 'Cliente'
    cliente_doc = CE._doc_fmt(doc)
    dig = ''.join(ch for ch in str(doc) if ch.isdigit())
    primeiro = CE._primeiro_nome(cliente_nome) if len(dig) == 11 else ''
    saudacao = f'Olá, {primeiro}!' if primeiro else 'Olá!'
    quem = 'da empresa acima' if cliente_doc.startswith('CNPJ') else 'do cliente acima'
    intro = (f'Recebemos o pagamento {quem}' + (f', referente a {comp}' if comp else '')
             + f', no dia {CE._data(pago_em)}. Está tudo certo: esta cobrança está quitada. '
             'Muito obrigado!')
    nota_txt = ''
    if tem_nota:
        nota_txt = (f"Nota fiscal de serviço nº {nota['numero_nfse']} emitida em "
                    f"{CE._data(nota.get('dh_emissao') or nota.get('data_processamento'))}"
                    + (f" · chave de acesso {nota['chave_acesso']}" if nota.get('chave_acesso') else '') + '.')
    d = {
        'empresa': empresa, 'data_txt': date.today().strftime('%d/%m/%Y'),
        'titulo': 'Pagamento recebido',
        'eyebrow': f'Pagamento confirmado · {comp}' if comp else 'Pagamento confirmado',
        'saudacao': saudacao, 'intro': intro, 'cliente_nome': cliente_nome, 'cliente_doc': cliente_doc,
        'cliente_rotulo': 'Empresa' if cliente_doc.startswith('CNPJ') else 'Cliente',
        'kpis': [('Valor pago', CE._brl(pago or t['valor'])), ('Pago em', CE._data(pago_em))],
        'itens': _itens(t, b), 'total': CE._brl(pago or t['valor']), 'nota_txt': nota_txt,
        'obrigado': ('Obrigado pela confiança! Este e-mail confirma que recebemos o '
                     'pagamento; não é preciso fazer mais nada.'),
        'slogan': 'a EMPRESA é sua, a PARCERIA é NOSSA',
        'rodape': (f'Enviado por {empresa}. Em caso de dúvida, responda este e-mail'
                   + (f' ou entre em contato pelo telefone {telefone}' if telefone else '') + '.'),
    }
    html = CE._env().get_template('pagamento_recebido.html').render(d=d)
    tx = ['Pagamento recebido' + (f' — {comp}' if comp else ''), cliente_nome + (f' · {cliente_doc}' if cliente_doc else ''),
          '', saudacao, intro, '', f'Valor pago: {d["total"]}   Pago em: {CE._data(pago_em)}', '', 'O QUE FOI PAGO']
    tx += [f'  {i["nome"]}: {i["valor"]}' for i in d['itens']]
    if nota_txt:
        tx += ['', nota_txt]
    tx += ['', d['obrigado'], '', d['slogan'], '', d['rodape']]
    assunto = f'Pagamento recebido' + (f' — {comp}' if comp else '') + f' · {cliente_nome}'
    return assunto, html, '\n'.join(tx)


# ---------------------------------------------------------------- envio

import logging
logger = logging.getLogger(__name__)

_CHAVE_DESDE = 'agradecimento_desde'


def ja_tratado(titulo_id):
    return bool(_q('SELECT id FROM fin_agradecimentos WHERE titulo_id = %s', (titulo_id,), one=True))


def pular(titulo_id, usuario_id=None):
    """A equipe baixou à mão e escolheu NÃO mandar: fica registrado, e a rotina automática não manda depois."""
    execute_query("INSERT IGNORE INTO fin_agradecimentos (titulo_id, situacao, usuario_id) VALUES (%s, 'pular', %s)",
                  (titulo_id, usuario_id))


def enviar(titulo_id, usuario_id=None):
    """Manda o agradecimento do título pago. Devolve (ok, mensagem). Um título recebe UM agradecimento só."""
    from models.fin_cobranca import FinBoleto
    from utils import email_envio
    r = montar(titulo_id)
    if not r:
        return False, 'a cobrança não está paga por inteiro'
    marca = execute_query("INSERT IGNORE INTO fin_agradecimentos (titulo_id, situacao, usuario_id) "
                          "VALUES (%s, 'enviando', %s)", (titulo_id, usuario_id))
    if type(marca) is not int:            # True = a linha já existia (outro processo, ou já agradecido)
        return False, 'o agradecimento desta cobrança já foi tratado'
    try:
        return _mandar(titulo_id, r, usuario_id)
    except Exception as exc:              # nunca deixa a linha presa em 'enviando'
        logger.exception('[agradecimento] título %s', titulo_id)
        execute_query("UPDATE fin_agradecimentos SET situacao = 'erro', erro = %s WHERE titulo_id = %s",
                      (str(exc)[:255], titulo_id))
        return False, 'erro interno'


def _mandar(titulo_id, r, usuario_id):
    from models.fin_cobranca import FinBoleto
    from utils import email_envio
    t = _q('SELECT id, cliente_id, empresa_id, boleto_id FROM fin_titulos WHERE id = %s', (titulo_id,), one=True)
    destinos = [d['email'] for d in CE.destinatarios_cobranca(t['cliente_id'])]
    if not destinos:
        execute_query("UPDATE fin_agradecimentos SET situacao = 'sem_email' WHERE titulo_id = %s", (titulo_id,))
        return False, 'o cliente não tem contato com e-mail'
    b = FinBoleto.get(t['boleto_id']) if t.get('boleto_id') else None
    try:
        rem = CE.remetente_do_boleto(b or {'conta_cobranca_id': None, 'empresa_id': t['empresa_id']})
    except ValueError as exc:
        execute_query("UPDATE fin_agradecimentos SET situacao = 'erro', erro = %s WHERE titulo_id = %s",
                      (str(exc)[:255], titulo_id))
        return False, str(exc)
    assunto, html, texto = r
    ok, erro = email_envio.enviar(destinos, assunto, html, texto, remetente=rem, servico='Agradecimento',
                                  usuario_id=usuario_id, cliente=True,
                                  referencia=('boleto', b['id']) if b else None)
    execute_query('UPDATE fin_agradecimentos SET situacao = %s, para = %s, erro = %s WHERE titulo_id = %s',
                  ('enviado' if ok else 'erro', ', '.join(destinos)[:255], None if ok else str(erro)[:255], titulo_id))
    return (True, 'agradecimento enviado para ' + ', '.join(destinos)) if ok else (False, f'o e-mail não saiu: {erro}')


def depois_da_baixa_manual(titulo_id, agradecer, usuario_id):
    """Chamada pelas telas de baixa à mão (pergunta 'Enviar e-mail de agradecimento?'). Devolve texto para o aviso
    da tela, ou ''. Pagamento parcial não manda nada (Anderson: 'Só quando pagar tudo')."""
    try:
        t = _q('SELECT status, tipo FROM fin_titulos WHERE id = %s', (titulo_id,), one=True) or {}
        if t.get('status') != 'liquidado' or t.get('tipo') != 'R' or ja_tratado(titulo_id):
            return ''
        if not agradecer:
            pular(titulo_id, usuario_id)
            return ''
        ok, msg = enviar(titulo_id, usuario_id)
        return (' E-mail de agradecimento enviado.' if ok else f' O agradecimento não foi: {msg}.')
    except Exception:
        logger.exception('[agradecimento] título %s', titulo_id)
        return ' O agradecimento não foi (erro interno).'


def rodar_automatico():
    """A cada volta do roteador: títulos a receber que ficaram pagos por inteiro pelo BANCO ou pelo extrato (baixa sem
    usuário) desde que isto foi ligado. Baixa à mão (usuário) nunca entra aqui: quem baixou já escolheu.
    Fora: título nascido do próprio extrato (não houve cobrança), sem cliente, negativo, e a baixa feita à mão no
    app da Efí ('efi-app:'); pagamento de mais de 7 dias atrás (consulta atrasada) não agradece. Desliga com AGRADECIMENTO_ATIVO=0."""
    import os
    if os.getenv('AGRADECIMENTO_ATIVO', '1').strip() != '1':
        return None
    r = _q('SELECT valor FROM app_config WHERE chave = %s', (_CHAVE_DESDE,), one=True)
    if not r:                              # 1ª volta: marca a hora; nada de antes recebe e-mail
        execute_query("INSERT IGNORE INTO app_config (chave, valor) VALUES (%s, CAST(NOW() AS CHAR))",
                      (_CHAVE_DESDE,))
        return None
    desde = r['valor']
    cands = _q("""SELECT t.id,
                         SUBSTRING_INDEX(GROUP_CONCAT(CONCAT(IF(b.usuario_id IS NULL, 'A', 'M'),
                             IF(LEFT(COALESCE(b.referencia, ''), 8) = 'efi-app:', 'X', '-')) ORDER BY b.criado_em DESC, b.id DESC), ',', 1) AS ultima
                    FROM fin_titulos t
                    JOIN fin_titulo_baixas b ON b.titulo_id = t.id
                    LEFT JOIN fin_agradecimentos a ON a.titulo_id = t.id
                   WHERE t.tipo = 'R' AND t.status = 'liquidado' AND t.valor > 0 AND t.cliente_id IS NOT NULL
                     AND COALESCE(t.origem, '') <> 'extrato' AND a.id IS NULL
                   GROUP BY t.id
                  HAVING MAX(b.criado_em) >= %s AND MAX(b.data_baixa) >= CURDATE() - INTERVAL 7 DAY
                   LIMIT 50""", (desde,)) or []
    feitos = 0
    for c in cands:
        if c['ultima'] != 'A-':
            continue
        try:
            ok, msg = enviar(c['id'])
            feitos += ok
            logger.warning('[agradecimento] título %s: %s', c['id'], msg)
        except Exception:
            logger.exception('[agradecimento] título %s', c['id'])
    return feitos
