# -*- coding: utf-8 -*-
"""E-mail de cobrança ao cliente: boleto + nota fiscal, pela caixa do Financeiro — 03/11/2026.

Decisão do Anderson (02-03/11/2026): quem avisa o cliente é o NOSSO app, não a Efí (o e-mail
dela leva só 5 itens e sai de um remetente que não é o nosso). O e-mail sai da caixa de
cobrança da conta (``fin_cobranca_contas.email_*``, ex.: financeiro@qualicontax.com.br),
então a resposta do cliente cai onde a equipe lê.

O que vai:
  * a lista COMPLETA do que está sendo cobrado (itens do boleto, com o que é só boleto marcado),
  * valor, vencimento, linha digitável, Pix copia e cola, link da página do boleto,
  * o PDF do boleto (baixado da Efí) e o PDF da nota fiscal (DANFSe montado do XML) anexados,
  * a NFS-e vinculada (número e chave) e o slogan.

``enviar_boleto`` grava ``email_enviado_em``/``email_enviado_para`` no boleto e nunca estoura:
devolve ``(ok, mensagem)`` para a tela.
"""
import json
import logging
import os
import secrets
from datetime import date, datetime
from decimal import Decimal

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

# Endereco publico do app para os links do e-mail (o mesmo dos avisos diarios).
APP_URL = os.getenv('APP_URL', 'https://app.qualicontax.com.br').rstrip('/')

MESES = ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto',
         'setembro', 'outubro', 'novembro', 'dezembro')


def _brl(v):
    v = Decimal(str(v or 0))
    s = f'{v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'R$ {s}'


def _data(d):
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.strftime('%d/%m/%Y') if d else ''


def _competencia_txt(n):
    c = (n or {}).get('competencia')
    if isinstance(c, str):
        try:
            c = date.fromisoformat(c[:10])
        except ValueError:
            c = None
    return f'{MESES[c.month - 1].capitalize()}/{c.year}' if c else ''


def _itens(boleto):
    itens = boleto.get('itens_json') or []
    if isinstance(itens, str):
        try:
            itens = json.loads(itens)
        except ValueError:
            itens = []
    return itens


def _env():
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return Environment(loader=FileSystemLoader(os.path.join(raiz, 'templates', 'emails')),
                       autoescape=select_autoescape(['html']))


def _empresa(empresa_id):
    from utils.texto import nome_proprio
    e = execute_query('SELECT nome_razao_social, nome_fantasia FROM clientes WHERE id = %s',
                      (empresa_id,), fetch=True, fetch_one=True) or {}
    return nome_proprio(e.get('nome_fantasia') or e.get('nome_razao_social') or 'o escritório')


def _competencia_do_titulo(boleto):
    """Sem nota, a competência vem do título do Financeiro que gerou o boleto."""
    if not boleto.get('titulo_id'):
        return ''
    t = execute_query('SELECT competencia FROM fin_titulos WHERE id = %s', (boleto['titulo_id'],),
                      fetch=True, fetch_one=True) or {}
    return _competencia_txt(t)


def _telefone_empresa(empresa_id):
    """Telefone (ou celular) da empresa que cobra, do cadastro dela — vai no rodapé do e-mail."""
    e = execute_query('SELECT telefone, celular FROM clientes WHERE id = %s', (empresa_id,),
                      fetch=True, fetch_one=True) or {}
    return (e.get('telefone') or e.get('celular') or '').strip()


def _doc_fmt(doc):
    """CPF 000.000.000-00 / CNPJ 00.000.000/0000-00; outra coisa volta como veio."""
    d = ''.join(ch for ch in str(doc or '') if ch.isdigit())
    if len(d) == 11:
        return f'CPF {d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    if len(d) == 14:
        return f'CNPJ {d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    return str(doc or '')


def _primeiro_nome(nome):
    from utils.texto import nome_proprio
    partes = nome_proprio(nome or '').split()
    return partes[0] if partes else ''


def garantir_token(boleto):
    """O codigo dos links do e-mail (``email_token``): nasce no primeiro envio e nao muda, para o
    e-mail antigo continuar valendo. Devolve o codigo (ou None se a coluna ainda nao existe)."""
    if boleto.get('email_token'):
        return boleto['email_token']
    tok = secrets.token_urlsafe(24)                 # 32 caracteres, 192 bits
    r = execute_query('UPDATE fin_boletos SET email_token = %s WHERE id = %s AND email_token IS NULL',
                      (tok, boleto['id']))
    if r is None:
        return None
    linha = execute_query('SELECT email_token FROM fin_boletos WHERE id = %s', (boleto['id'],),
                          fetch=True, fetch_one=True) or {}
    boleto['email_token'] = linha.get('email_token')
    return boleto['email_token']


CARIMBOS = {'pixel': 'email_visto_em', 'boleto': 'abriu_boleto_em', 'nota': 'abriu_nota_em'}


def registrar_abertura(token, qual):
    """Anota a ÚLTIMA vez que o cliente abriu (pixel, boleto ou nota). Devolve o boleto ou None."""
    col = CARIMBOS.get(qual)
    if not col or not token or len(token) > 48:
        return None
    b = execute_query('SELECT * FROM fin_boletos WHERE email_token = %s', (token,), fetch=True, fetch_one=True)
    if not b:
        return None
    # Guarda a ULTIMA abertura (Anderson, 04/11/2026: 'nao esta voltando' — com a primeira
    # vez fixa ele clicava de novo e nada mudava). O reenvio zera os tres carimbos.
    # IP e navegador de quem abriu (migracao 05): separam o clique do cliente da visita
    # automatica do servidor de e-mail. Sem as colunas, grava so a hora.
    from utils.qrobo_chaves import contexto_request
    ip, agente = contexto_request()
    if 'abriu_ip' in b:
        execute_query(f'UPDATE fin_boletos SET {col} = NOW(), abriu_ip = %s, abriu_agente = %s WHERE id = %s',
                      ((ip or '')[:45] or None, (agente or '')[:255] or None, b['id']))
    else:
        execute_query(f'UPDATE fin_boletos SET {col} = NOW() WHERE id = %s', (b['id'],))
    return b


def links_do_email(boleto):
    """Links do e-mail pelo nosso app (carimbam a abertura): boleto, boleto_pdf, nota, nota_pdf, pixel.
    Sem codigo (coluna nova ausente) so o link da Efi, direto."""
    tok = boleto.get('email_token')
    if not tok:
        return {'boleto': boleto.get('link') or boleto.get('pdf_url'), 'boleto_pdf': None,
                'nota': None, 'nota_pdf': None, 'pixel': None}
    base = f'{APP_URL}/c/{tok}'
    return {'boleto': f'{base}/boleto', 'boleto_pdf': f'{base}/boleto/pdf',
            'nota': f'{base}/nota', 'nota_pdf': f'{base}/nota/pdf', 'pixel': f'{base}/pixel.png'}


def nomes_dos_pdfs(boleto, nota, empresa_nome):
    """(nome do PDF do boleto, nome do PDF da nota) — os mesmos do anexo e do download."""
    nb = _nome_arquivo(empresa_nome, f'Boleto venc {_data(boleto["vencimento"]).replace("/", "-")}')
    nn = _nome_arquivo(empresa_nome, f"NFS-e {nota['numero_nfse']}", _competencia_txt(nota).replace('/', '-')) if nota else None
    return nb, nn


def _nome_arquivo(*partes):
    """Nome de anexo que o cliente entende ao salvar: "Qualicontax - Boleto venc 09-11-2026.pdf".
    Tira o que Windows/Mac/Gmail recusam em nome de arquivo e encurta."""
    import re
    txt = ' - '.join(str(x).strip() for x in partes if x)
    txt = re.sub(r'[\/:*?"<>|]+', ' ', txt)
    txt = re.sub(r'\s+', ' ', txt).strip(' .')
    return txt[:120] + '.pdf'


def pdf_do_boleto(boleto, timeout=30):
    """O PDF do boleto na Efí (``pdf_url`` é público). Devolve bytes ou None."""
    url = boleto.get('pdf_url')
    if not url:
        return None
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200 and r.content[:4] == b'%PDF':
            return r.content
        logger.warning('[cobranca-email] PDF do boleto %s: HTTP %s', boleto.get('id'), r.status_code)
    except Exception as exc:                        # noqa: BLE001
        logger.warning('[cobranca-email] PDF do boleto %s: %s', boleto.get('id'), exc)
    return None


def pdf_da_nota(nota):
    """DANFSe da nota nacional montado do XML (o mesmo da tela). None quando não dá."""
    if not nota or nota.get('situacao') not in ('autorizada', 'cancelada') or nota.get('emissor') == 'sp':
        return None
    try:
        from utils import nfse_emissor
        from brazilfiscalreport.danfse import Danfse
        xml = nfse_emissor.xml_nfse_da_linha(nota)
        return bytes(Danfse(xml=xml).output()) if xml else None
    except Exception as exc:                        # noqa: BLE001
        logger.warning('[cobranca-email] PDF da nota %s: %s', nota.get('id'), exc)
        return None


def montar(boleto, nota, empresa_nome, com_anexos=False, telefone=''):
    """(assunto, html, texto, anexos, faltas) — ``faltas`` lista o que não foi anexado.

    Sem anexo por padrão (Anderson, 04/11/2026): o boleto e a nota vão pelos botões, que passam
    pelo nosso link e carimbam a hora em que o cliente abriu. Anexo abriria por fora, sem carimbo.
    ``com_anexos=True`` continua existindo para quem precisar do PDF junto.
    """
    itens = _itens(boleto)
    comp = _competencia_txt(nota) if nota else _competencia_do_titulo(boleto)
    tem_nota = bool(nota and nota.get('numero_nfse') and nota.get('situacao') == 'autorizada')
    # A EMPRESA do cliente em destaque (Anderson, 04/11/2026): o mesmo e-mail pode receber a
    # cobrança de dez empresas; o nome vai no assunto e na faixa verde.
    from utils.texto import nome_proprio
    cliente_nome = nome_proprio((nota or {}).get('tomador_nome') or boleto.get('pagador_nome') or '') or 'Cliente'
    cliente_doc = _doc_fmt((nota or {}).get('tomador_doc') or boleto.get('pagador_doc'))
    titulo = 'Boleto e nota fiscal' if tem_nota else 'Boleto'
    titulo_assunto = f'{titulo} — {comp}' if comp else titulo
    eyebrow = f'Cobrança · {comp}' if comp else 'Cobrança'
    # Saudação com nome só quando o pagador é pessoa (CPF): empresa viraria 'Olá, Ar!'.
    doc_digitos = ''.join(ch for ch in str(boleto.get('pagador_doc') or '') if ch.isdigit())
    primeiro = _primeiro_nome(boleto.get('pagador_nome')) if len(doc_digitos) == 11 else ''
    saudacao = f'Olá, {primeiro}!' if primeiro else 'Olá!'
    quem = 'à empresa acima' if cliente_doc.startswith('CNPJ') else 'ao cliente acima'
    intro = (f'Aqui está a cobrança de {empresa_nome} referente {quem}'
             + (f', competência {comp}' if comp else '') + '. Abaixo, tudo o que foi cobrado'
             + (' e os botões para abrir ou baixar o boleto' + (' e a nota fiscal' if tem_nota else '') + '.'
                if com_anexos is False else
                '; o boleto' + (' e a nota fiscal vão' if tem_nota else ' vai') + ' em anexo.'))
    # So dois numeros grandes (valor e vencimento): tres nao cabem no celular. O numero da
    # NFS-e ja esta na caixa verde da nota.
    kpis = [('Valor', _brl(boleto['valor'])), ('Vencimento', _data(boleto['vencimento']))]
    linhas = [{'nome': i.get('nome') or '', 'valor': _brl(i.get('valor')),
               'obs': '' if i.get('emite_nota', True) else 'não entra na nota fiscal'} for i in itens]
    if not linhas:
        linhas = [{'nome': 'Honorários e serviços', 'valor': _brl(boleto['valor']), 'obs': ''}]
    # Botoes em dois grupos (Anderson, 04/11/2026: 'tem cliente que gosta so de abrir e outro so de
    # baixar'): Boleto -> Abrir (pagina da Efi) / Baixar PDF; Nota fiscal -> Ver / Baixar PDF. Todos
    # passam por links NOSSOS, que carimbam a hora em que o cliente abriu. Sem codigo, so a Efi.
    urls = links_do_email(boleto)
    url_pixel = urls['pixel']
    grupos, botoes = [], []
    if urls['boleto'] and (boleto.get('link') or boleto.get('pdf_url')):
        g = [('Abrir o boleto', urls['boleto'])]
        if urls['boleto_pdf'] and boleto.get('pdf_url'):
            g.append(('Baixar PDF', urls['boleto_pdf']))
        grupos.append(('Boleto', g))
    if tem_nota and urls['nota']:
        grupos.append(('Nota fiscal', [('Ver a nota', urls['nota']), ('Baixar PDF', urls['nota_pdf'])]))
    botoes = [b for _, g in grupos for b in g]
    nota_txt = ''
    if tem_nota:
        nota_txt = (f"Nota fiscal de serviço nº {nota['numero_nfse']} emitida em {_data(nota.get('dh_emissao') or nota.get('data_processamento'))}"
                    + (f" · chave de acesso {nota['chave_acesso']}" if nota.get('chave_acesso') else '') + '.')
    anexos, faltas = [], []
    if com_anexos:
        pb = pdf_do_boleto(boleto)
        if pb:
            anexos.append((_nome_arquivo(empresa_nome, f'Boleto venc {_data(boleto["vencimento"]).replace("/", "-")}'), pb, 'application/pdf'))
        else:
            faltas.append('PDF do boleto')
        if tem_nota:
            pn = pdf_da_nota(nota)
            if pn:
                anexos.append((_nome_arquivo(empresa_nome, f"NFS-e {nota['numero_nfse']}", comp.replace('/', '-')), pn, 'application/pdf'))
            else:
                faltas.append('PDF da nota fiscal')
    anexos_txt = ''
    if anexos:
        anexos_txt = 'Em anexo: ' + ' e '.join('o PDF do boleto' if 'Boleto' in a[0] else 'o PDF da nota fiscal' for a in anexos) + '.'
    elif botoes:
        anexos_txt = ''
    d = {
        'empresa': empresa_nome, 'data_txt': date.today().strftime('%d/%m/%Y'), 'titulo': titulo, 'intro': intro,
        'eyebrow': eyebrow, 'saudacao': saudacao, 'cliente_nome': cliente_nome, 'cliente_doc': cliente_doc,
        'cliente_rotulo': 'Empresa' if cliente_doc.startswith('CNPJ') else 'Cliente',
        'kpis': kpis, 'itens': linhas, 'total': _brl(boleto['valor']),
        'linha_digitavel': boleto.get('linha_digitavel') or '', 'pix': boleto.get('pix_copia_cola') or '',
        'botoes': botoes, 'grupos': grupos, 'nota_txt': nota_txt, 'anexos_txt': anexos_txt, 'pixel': url_pixel,
        'slogan': 'a EMPRESA é sua, a PARCERIA é NOSSA',
        'rodape': (f'Enviado por {empresa_nome}. Em caso de dúvida, responda este e-mail'
                   + (f' ou entre em contato pelo telefone {telefone}' if telefone else '') + '.'),
    }
    html = _env().get_template('cobranca.html').render(d=d)
    t = [titulo_assunto, cliente_nome + ((' · ' + cliente_doc) if cliente_doc else ''), '', saudacao, intro, '', f'Valor: {d["total"]}   Vencimento: {_data(boleto["vencimento"])}', '', 'DETALHAMENTO DA COBRANÇA']
    t += [f'  {l["nome"]}{(" (" + l["obs"] + ")") if l["obs"] else ""}: {l["valor"]}' for l in linhas]
    t += [f'  Total do boleto: {d["total"]}', '']
    if d['linha_digitavel']:
        t += ['Linha digitável: ' + d['linha_digitavel'], '']
    if d['pix']:
        t += ['Pix copia e cola: ' + d['pix'], '']
    t += [f'{r}: {u}' for r, u in botoes]
    if nota_txt:
        t += ['', nota_txt]
    t += ['', d['slogan'], '', d['rodape']]
    assunto = f"{titulo_assunto} · {cliente_nome}"
    return assunto, html, '\n'.join(t), anexos, faltas


def enviar_boleto(boleto_id, para=None, usuario_id=None):
    """Manda o e-mail de cobrança do boleto pela caixa da conta. Devolve (ok, mensagem para a tela)."""
    from models.fin_cobranca import FinBoleto, FinCobrancaConta
    from models.nfse_emissao import NfseEmitida
    from utils import email_envio
    b = FinBoleto.get(boleto_id)
    if not b:
        return False, 'Boleto não encontrado.'
    if b.get('situacao') in ('canceled', 'erro'):
        return False, 'Este boleto está cancelado; não vai por e-mail.'
    destinos = [p.strip().lower() for p in str(para or b.get('pagador_email') or '').replace(';', ',').split(',') if p.strip()]
    if not destinos:
        return False, 'O pagador não tem e-mail: informe um endereço para enviar.'
    if any('@' not in p for p in destinos):
        return False, 'Há um endereço de e-mail inválido.'
    try:
        rem = FinCobrancaConta.remetente_email(b['conta_cobranca_id'])
    except ValueError as exc:
        return False, f'{exc} Preencha o bloco "E-mail de cobrança" na conta de cobrança.'
    nota = NfseEmitida.get(b['nfse_id']) if b.get('nfse_id') else None
    garantir_token(b)
    assunto, html, texto, anexos, faltas = montar(b, nota, _empresa(b['empresa_id']),
                                                  telefone=_telefone_empresa(b['empresa_id']))
    ok, erro = email_envio.enviar(destinos, assunto, html, texto, remetente=rem, anexos=anexos)
    if not ok:
        return False, f'O e-mail não saiu: {erro}'
    # Os carimbos falam DESTE e-mail: zeram a cada (re)envio.
    execute_query('UPDATE fin_boletos SET email_enviado_em = NOW(), email_enviado_para = %s, atualizado_em = NOW(), '
                  'email_visto_em = NULL, abriu_boleto_em = NULL, abriu_nota_em = NULL, abriu_ip = NULL, abriu_agente = NULL '
                  'WHERE id = %s', (', '.join(destinos)[:255], boleto_id))
    msg = f'E-mail enviado para {", ".join(destinos)}'
    if faltas:
        msg += ' (sem ' + ' e '.join(faltas) + ': não foi possível montar)'
    return True, msg + '.'
