# -*- coding: utf-8 -*-
"""Migração Acessórias+Conexa — PARTE 4b: endereço das PESSOAS FÍSICAS vindo do Conexa (05/11/2026).

Decisão do Anderson (05/11): "Endereços: CNPJ buscar na Receita, PF trazer como está no Conexa."
A Receita não tem endereço de CPF; o Conexa é a única fonte. Só quem NÃO tem nenhum endereço no
app recebe um (RESIDENCIAL, principal), gravado como está no Conexa (o modelo põe em maiúsculas).
Idempotente: na segunda rodada todos já têm endereço. Log: escrita.endereco_conexa_migracao.

Uso:
    python scripts/migracao_enderecos_conexa.py --base <Conexa_Base_Completa.xlsx>          # dry-run
    python scripts/migracao_enderecos_conexa.py --base <...> --apply
"""
import argparse
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                        # noqa: E402,F401
from utils.db_helper import execute_query            # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'


def tx(v):
    s = str(v).strip() if v is not None else ''
    return '' if s in ('None', 'nan') else s


def dig(v):
    return re.sub(r'\D', '', str(v or ''))


def ler_clientes(caminho):
    import openpyxl
    ws = openpyxl.load_workbook(caminho, read_only=True, data_only=True)['Clientes']
    rows = ws.iter_rows(values_only=True)
    head = [str(h) for h in next(rows)]
    return [dict(zip(head, r)) for r in rows if any(v not in (None, '') for v in r)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--pj-tambem', action='store_true', help='inclui PJ sem endereço (depois da Receita, se sobrar)')
    a = ap.parse_args()
    from models.endereco_cliente import EnderecoCliente

    conexa = {int(r['ID']): r for r in ler_clientes(a.base) if tx(r.get('ID')).isdigit()}
    app = execute_query("""
        SELECT c.id, c.conexa_id, c.numero_cliente, c.avulso, c.tipo_pessoa, c.nome_razao_social,
               (SELECT COUNT(*) FROM enderecos_clientes e WHERE e.cliente_id = c.id) AS n_end
          FROM clientes c WHERE c.conexa_id IS NOT NULL""", fetch=True) or []
    print(f"{'APLICANDO' if a.apply else 'DRY-RUN'}: {len(app)} clientes com vínculo do Conexa")
    tot = {'já tem endereço': 0, 'Conexa sem rua': 0, 'PJ (fica para a Receita)': 0, 'criado' if a.apply else 'criaria': 0}
    for c in app:
        r = conexa.get(c['conexa_id'])
        if not r:
            continue
        if c['tipo_pessoa'] != 'PF' and not a.pj_tambem:
            tot['PJ (fica para a Receita)'] += 1
            continue
        if c['n_end']:
            tot['já tem endereço'] += 1
            continue
        if not tx(r.get('Rua')) or not tx(r.get('Cidade.1') or r.get('Cidade')):
            tot['Conexa sem rua'] += 1
            continue
        cep = dig(r.get('CEP'))
        end = dict(cep=f'{cep[:5]}-{cep[5:]}' if len(cep) == 8 else (tx(r.get('CEP')) or None),
                   logradouro=tx(r.get('Rua'))[:255], numero=(tx(r.get('Numero')) or 'S/N')[:20],
                   complemento=tx(r.get('Complemento')) or None, bairro=tx(r.get('Bairro')) or None,
                   cidade=tx(r.get('Cidade.1') or r.get('Cidade')), estado=(tx(r.get('UF')) or '').upper()[:2] or None)
        rot = 'AV' if c['avulso'] else c['numero_cliente']
        print(f"  {rot:>4} {c['nome_razao_social'][:36]:36} {end['logradouro'][:30]}, {end['numero']} - {end['bairro'] or ''} - {end['cidade']}/{end['estado']} {end['cep'] or ''}")
        if a.apply:
            tipo = 'RESIDENCIAL' if c['tipo_pessoa'] == 'PF' else 'COMERCIAL'
            EnderecoCliente.create(c['id'], tipo, principal=True, **end)
            registrar_agente('escrita.endereco_conexa_migracao', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='clientes', registro_id=c['id'],
                             depois={'endereco': f"{end['logradouro']}, {end['numero']} - {end['cidade']}/{end['estado']}", 'tipo': tipo, 'fonte': 'Conexa'})
        tot['criado' if a.apply else 'criaria'] += 1
    print('TOTAIS:', tot)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    main()
