# -*- coding: utf-8 -*-
"""Traz os boletos do Conexa para o nosso sistema (14/11/2026).

Anderson (14/11/2026): "Pode migrar por que nem acesso mais o Conexa ... se der para migrar tudo pra nós eu
prefiro, exemplo se eu precisar baixar um boleto ou alterar eu ter a opção de fazer tudo aqui".

1. cria a conta de cobrança "EFÍ — BOLETOS DO CONEXA" (só consulta) com as credenciais da aplicação "Conexa"
   da Efí, lidas de EFI_CONEXA_CLIENT_ID / EFI_CONEXA_CLIENT_SECRET (gravadas CIFRADAS como as outras contas);
2. grava em fin_boletos o boleto de cada título do Conexa (o pago, se houver; senão o mais recente), ligado
   ao título pelo número da cobrança do Conexa (custom_id do boleto = chave_idem do título) e conferido
   pelo CNPJ;
3. com --trocar-aviso (DEPOIS do deploy): os boletos ainda pagáveis passam a avisar o NOSSO sistema em vez
   do Conexa (a Efí manda o aviso para a URL de cada boleto).

    python scripts/importar_boletos_conexa.py                    # só mostra
    python scripts/importar_boletos_conexa.py --gravar
    python scripts/importar_boletos_conexa.py --trocar-aviso     # só mostra
    python scripts/importar_boletos_conexa.py --trocar-aviso --gravar
"""
import json
import os
import re
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query as q      # noqa: E402
from models.fin_cobranca import FinCobrancaConta    # noqa: E402

GRAVAR = '--gravar' in sys.argv
APELIDO = 'EFÍ — BOLETOS DO CONEXA'
EMPRESA = 2
URL_APP = os.getenv('APP_URL_PUBLICA', 'https://app.qualicontax.com.br').rstrip('/')


def dig(s):
    return re.sub(r'\D', '', str(s or ''))


def conta():
    c = q('SELECT * FROM fin_cobranca_contas WHERE empresa_id = %s AND so_consulta = 1 AND apelido = %s',
          (EMPRESA, APELIDO), fetch=True, fetch_one=True)
    if c:
        return c
    cid, sec = (os.getenv('EFI_CONEXA_CLIENT_ID') or '').strip(), (os.getenv('EFI_CONEXA_CLIENT_SECRET') or '').strip()
    if not cid or not sec:
        sys.exit('faltam EFI_CONEXA_CLIENT_ID / EFI_CONEXA_CLIENT_SECRET no .env')
    if not GRAVAR:
        print(f'criaria a conta "{APELIDO}" (só consulta, produção)')
        return None
    base = q("SELECT fin_conta_id FROM fin_cobranca_contas WHERE empresa_id = %s AND ambiente = 'producao' "
             "AND so_consulta = 0 ORDER BY id LIMIT 1", (EMPRESA,), fetch=True, fetch_one=True) or {}
    novo, erro = FinCobrancaConta.salvar(EMPRESA, {'apelido': APELIDO, 'provedor': 'efi', 'ambiente': 'producao',
                                                   'fin_conta_id': base.get('fin_conta_id'), 'avisa_por': 'ninguem',
                                                   'dias_baixa': '120'},
                                         usuario_id=62, client_id=cid, client_secret=sec)
    if erro:
        sys.exit(erro)
    q('UPDATE fin_cobranca_contas SET so_consulta = 1 WHERE id = %s', (novo,), fetch=False)
    print('conta criada:', novo)
    return FinCobrancaConta.get(novo)


def importar(c):
    from utils.efi_boletos import MESES
    from utils.integrations.efi.client import Efi, ContaEfi
    efi = (FinCobrancaConta.cliente_efi(c['id'])[0] if c else
           Efi(ContaEfi(os.environ['EFI_CONEXA_CLIENT_ID'].strip(), os.environ['EFI_CONEXA_CLIENT_SECRET'].strip(), 'producao')))
    from utils.efi_boletos import listar_tudo
    bol = listar_tudo(efi, MESES)
    por_cid = {}
    for b in bol.values():
        por_cid.setdefault(str(b.get('custom_id') or ''), []).append(b)
    tits = q("""SELECT t.id, t.status, t.valor, t.chave_idem, t.contraparte_nome, COALESCE(cl.cpf_cnpj, t.contraparte_doc) doc
                  FROM fin_titulos t LEFT JOIN clientes cl ON cl.id = t.cliente_id
                 WHERE t.origem = 'conexa' AND t.chave_idem LIKE 'conexa:cobranca:%%'""", fetch=True) or []
    n, sit, sem, outro_doc, ja = 0, Counter(), 0, [], 0
    for t in tits:
        bs = por_cid.get(t['chave_idem'].split(':')[-1], [])
        if not bs:
            sem += 1
            continue
        pagos = [b for b in bs if b['status'] in ('paid', 'settled')]
        b = sorted(pagos or bs, key=lambda x: x.get('created_at') or '')[-1]
        cu = b.get('customer') or {}
        doc = dig(cu.get('cnpj') or cu.get('cpf'))
        if t['doc'] and doc and dig(t['doc']) != doc:
            outro_doc.append(t['id'])
            continue
        if q("SELECT 1 x FROM fin_boletos WHERE provedor = 'efi' AND ambiente = 'producao' AND charge_id = %s",
             (str(b['id']),), fetch=True, fetch_one=True):
            ja += 1
            continue
        sit[b['status']] += 1
        n += 1
        if not GRAVAR or not c:
            continue
        p = b.get('payment') or {}
        bb = p.get('banking_billet') or {}
        pix = bb.get('pix') or {}
        pago = (p.get('received_by_bank_at') or p.get('paid_at') or '')[:10] or None
        raw = {k: v for k, v in b.items() if k != 'payment'}
        raw['payment'] = {k: v for k, v in p.items() if k != 'banking_billet'}
        q("""INSERT INTO fin_boletos (empresa_id, conta_cobranca_id, titulo_id, provedor, ambiente, charge_id, custom_id,
                                      situacao, valor, vencimento, pagador_doc, pagador_nome, linha_digitavel, link,
                                      pdf_url, pix_copia_cola, pago_em, valor_pago, raw_resposta, criado_por)
             VALUES (%s, %s, %s, 'efi', 'producao', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)""",
          (EMPRESA, c['id'], t['id'], str(b['id']), str(b.get('custom_id') or ''), b['status'],
           round(b['total'] / 100, 2), (bb.get('expire_at') or '')[:10] or str(date.today()), doc or None,
           cu.get('corporate_name') or cu.get('name'), bb.get('barcode'), bb.get('link'),
           (bb.get('pdf') or {}).get('charge'), pix.get('qrcode'), pago,
           round((p.get('paid_value') or 0) / 100, 2) if b['status'] == 'paid' else None,
           json.dumps(raw, ensure_ascii=False, default=str)), fetch=False)
    print(f'boletos na Efí: {len(bol)} | títulos do Conexa: {len(tits)} | '
          f'{"gravados" if GRAVAR else "a gravar"}: {n} {dict(sit)} | já estavam: {ja} | '
          f'título sem boleto: {sem} | CNPJ diferente (fora): {outro_doc}')


def trocar_aviso(c):
    if not c:
        sys.exit('a conta ainda não existe: rode com --gravar primeiro')
    from utils.integrations.efi.client import EfiErro
    efi, _ = FinCobrancaConta.cliente_efi(c['id'])
    url = f"{URL_APP}/webhooks/efi/{c['webhook_segredo']}"
    bs = q("""SELECT id, charge_id, custom_id FROM fin_boletos WHERE conta_cobranca_id = %s
               AND situacao IN ('new', 'waiting', 'unpaid', 'link')""", (c['id'],), fetch=True) or []
    print(f'{len(bs)} boleto(s) pagáveis passam a avisar {URL_APP}/webhooks/efi/…')
    if not GRAVAR:
        return
    ok, falha = 0, []
    for b in bs:
        try:
            efi.definir_aviso(b['charge_id'], url, custom_id=b['custom_id'])
            ok += 1
        except EfiErro as e:
            falha.append((b['charge_id'], str(e)[:120]))
    print('trocados:', ok, '| falharam:', falha)
    from utils.atividade import registrar_agente
    registrar_agente('escrita.boletos_conexa_aviso_para_o_app', 'financeiro', 62,
                     'Anderson Antunes Vieira (pedido no chat, aplicado pelo Claude)', 'anderson',
                     tabela='fin_cobranca_contas', registro_id=c['id'], depois={'trocados': ok, 'falharam': len(falha)})


if __name__ == '__main__':
    c = conta()
    if '--trocar-aviso' in sys.argv:
        trocar_aviso(c)
    else:
        importar(c)
