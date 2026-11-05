# -*- coding: utf-8 -*-
"""Migração Acessórias+Conexa — PARTE 4: "Buscar CNPJ e salvar" em lote (05/11/2026).

Faz, para cada empresa migrada, o que o botão "Buscar CNPJ" do cadastro faz
(templates/clientes/form.html, preencherDadosCNPJ) e salva — mas SÓ nos campos
vazios. Decisão do Anderson (04/11): "cadastrar a empresa e buscar o CNPJ" — a
Receita decide endereço, regime, CNAE, natureza, porte, telefone e e-mail.

Regras:
  * alvo: PJ ativa com CNPJ de 14 dígitos que passou pela migração (conexa_id
    ou log escrita.*_migracao); --todos inclui as 11 que não vieram da planilha.
  * NUNCA sobrescreve: campo preenchido fica; divergência vai para o CSV.
  * regime tributário: só RELATÓRIO (não se sabe Presumido x Real pela Receita),
    exceto os avulsos PJ criados pelo Conexa, que ganharam 'SIMPLES' por padrão do
    Cliente.create sem ser dado: nesses, MEI vira MEI e "não optante" vira NULL
    (fica para a turma em A CADASTRAR), e fica no CSV.
  * endereço: só cria o COMERCIAL principal quando a empresa não tem NENHUM.
  * e-mail/telefone do escritório (qualicontax, m3a3, 11 2523-1815) não entram.
  * situação cadastral BAIXADA/INAPTA/SUSPENSA e razão social diferente: CSV.
  * sócios (QSA) NÃO: a fila "Sócios pendentes" já cuida (utils/socios_fila.py).
  * idempotente: na segunda rodada não há vazio, logo não consulta nem grava.
  * log por cliente: escrita.buscou_cnpj_migracao (usuário 62, Anderson).

Uso:
    python scripts/migracao_buscar_cnpj.py                      # dry-run
    python scripts/migracao_buscar_cnpj.py --limite 5           # amostra
    python scripts/migracao_buscar_cnpj.py --apply --csv saida.csv
"""
import argparse
import csv
import io
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                        # noqa: E402,F401
from utils.db_helper import execute_query            # noqa: E402
from utils import cnpj_receita as RF                 # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'
TEL_ESCRITORIO = {'1125231815', '01125231815'}
DOMINIOS_ESCRITORIO = ('qualicontax', 'm3a3')
PORTE = {'ME': 'ME', 'EPP': 'EPP', 'DEMAIS': 'MEDIO', 'MICRO EMPRESA': 'ME', 'EMPRESA DE PEQUENO PORTE': 'EPP'}

SQL_ALVO = """
SELECT c.id, c.numero_cliente, c.avulso, c.nome_razao_social, c.nome_fantasia, c.cpf_cnpj, c.inscricao_estadual,
       c.email, c.telefone, c.celular, c.regime_tributario, c.porte_empresa, c.cnae_fiscal, c.cnae_fiscal_descricao,
       c.data_inicio_atividade, c.natureza_codigo, c.natureza_juridica,
       (SELECT COUNT(*) FROM enderecos_clientes e WHERE e.cliente_id = c.id) AS n_end,
       (SELECT COUNT(*) FROM clientes_inscricoes_estaduais i WHERE i.cliente_id = c.id) AS n_ie,
       EXISTS(SELECT 1 FROM logs_sistema l WHERE l.tabela_afetada = 'clientes' AND l.registro_id = c.id
                 AND l.acao = 'escrita.criou_avulso_migracao') AS avulso_do_conexa
  FROM clientes c
 WHERE c.tipo_pessoa <> 'PF' AND c.situacao = 'ATIVO'
   AND LENGTH(REGEXP_REPLACE(c.cpf_cnpj, '[^0-9]', '')) = 14
   {filtro}
 ORDER BY c.avulso, CAST(c.numero_cliente AS UNSIGNED), c.id
"""
FILTRO_MIGRADOS = """AND (c.conexa_id IS NOT NULL OR EXISTS(SELECT 1 FROM logs_sistema l WHERE l.tabela_afetada = 'clientes'
                 AND l.registro_id = c.id AND l.acao LIKE 'escrita.%migracao'))"""


def vazio(v):
    return v is None or str(v).strip() == ''


def dig(v):
    return re.sub(r'\D', '', str(v or ''))


def plain(s):
    import unicodedata
    return ''.join(ch for ch in unicodedata.normalize('NFD', str(s or '')) if unicodedata.category(ch) != 'Mn').upper().strip()


def fone_ok(d):
    """Como telefoneValido() da tela: 10 ou 11 dígitos, sem enchimento, não é o escritório."""
    return d and len(d) in (10, 11) and len(set(d)) > 1 and d not in TEL_ESCRITORIO and d.lstrip('0') not in TEL_ESCRITORIO


def fone_fmt(d):
    return f'({d[:2]}) {d[2:6]}-{d[6:]}' if len(d) == 10 else f'({d[:2]}) {d[2:7]}-{d[7:]}'


def cep_fmt(d):
    return f'{d[:5]}-{d[5:]}' if len(d) == 8 else d


def email_do_escritorio(e):
    return any(dm in e for dm in DOMINIOS_ESCRITORIO)


def montar(c, rf):
    """(campos a gravar em clientes, endereço a criar ou None, IE a inserir ou None, avisos para o CSV)."""
    campos, avisos = {}, []
    rotulo = f"{c['numero_cliente'] or 'AV'} {c['nome_razao_social'][:40]}"

    if rf['razao_social'] and plain(rf['razao_social']) != plain(c['nome_razao_social']):
        avisos.append(('razão social difere (não altera)', c['nome_razao_social'], rf['razao_social']))
    sit = (rf.get('situacao_cadastral') or '').upper()
    if sit and sit != 'ATIVA':
        avisos.append(('situação na Receita', 'ATIVO no app', sit))

    if vazio(c['nome_fantasia']) and rf['nome_fantasia']:
        campos['nome_fantasia'] = rf['nome_fantasia'][:255]
    if vazio(c['natureza_codigo']) and rf.get('natureza_juridica_codigo'):
        campos['natureza_codigo'] = rf['natureza_juridica_codigo']
        campos['natureza_juridica'] = ' '.join((rf['natureza_juridica'] or '').split())[:120] or None
    if vazio(c['porte_empresa']):
        p = 'MEI' if rf.get('opcao_pelo_mei') else PORTE.get((rf.get('porte') or '').upper())
        if p:
            campos['porte_empresa'] = p
    if vazio(c['data_inicio_atividade']) and re.match(r'^\d{4}-\d{2}-\d{2}$', rf.get('data_inicio_atividade') or ''):
        campos['data_inicio_atividade'] = rf['data_inicio_atividade']
    if vazio(c['cnae_fiscal']) and rf['cnae_fiscal']:
        campos['cnae_fiscal'] = rf['cnae_fiscal'][:10]
        if rf['cnae_fiscal_descricao']:
            campos['cnae_fiscal_descricao'] = rf['cnae_fiscal_descricao'][:500]
    elif vazio(c['cnae_fiscal_descricao']) and rf['cnae_fiscal_descricao'] and dig(c['cnae_fiscal']) == dig(rf['cnae_fiscal']):
        campos['cnae_fiscal_descricao'] = rf['cnae_fiscal_descricao'][:500]

    email = (rf.get('email') or '').lower().strip()
    if email:
        if email_do_escritorio(email):
            if vazio(c['email']):
                avisos.append(('e-mail da Receita é do escritório (não entra)', '', email))
        elif vazio(c['email']) and '@' in email:
            campos['email'] = email[:255]
        elif (c['email'] or '').lower().strip() != email:
            # 05/11: "o e-mail principal é para saber qual tem que estar no CNPJ" — difere, vai ao CSV
            avisos.append(('e-mail do app difere do que está no CNPJ (não altera)', c['email'], email))

    t1, t2 = dig(rf.get('ddd_telefone_1')), dig(rf.get('ddd_telefone_2'))
    fones = [t for t in (t1, t2) if fone_ok(t)]
    tel_atual, cel_atual = dig(c['telefone']), dig(c['celular'])
    for t in fones:
        if t in (tel_atual, cel_atual):
            continue
        if len(t) == 11 and vazio(c['celular']) and 'celular' not in campos:
            campos['celular'] = fone_fmt(t)
        elif vazio(c['telefone']) and 'telefone' not in campos:
            campos['telefone'] = fone_fmt(t)

    # regime: só relatório, exceto o padrão sem dado dos avulsos vindos do Conexa
    if c['avulso_do_conexa'] and c['regime_tributario'] == 'SIMPLES':
        if rf.get('opcao_pelo_mei'):
            campos['regime_tributario'] = 'MEI'
            avisos.append(('regime: padrão SIMPLES do avulso vira MEI pela Receita', 'SIMPLES', 'MEI'))
        elif not rf.get('opcao_pelo_simples'):
            campos['regime_tributario'] = None
            avisos.append(('regime: Receita diz NÃO optante do Simples; padrão retirado, decidir na tela', 'SIMPLES', 'Presumido ou Real?'))
    else:
        rg = c['regime_tributario']
        if rf.get('opcao_pelo_mei') and rg != 'MEI':
            avisos.append(('regime difere (não altera): Receita diz MEI', rg, 'MEI'))
        elif rf.get('opcao_pelo_simples') and not rf.get('opcao_pelo_mei') and rg not in ('SIMPLES', None, ''):
            avisos.append(('regime difere (não altera): Receita diz Simples', rg, 'SIMPLES'))
        elif not rf.get('opcao_pelo_simples') and rg in ('SIMPLES', 'MEI'):
            avisos.append(('regime difere (não altera): Receita diz NÃO optante do Simples', rg, 'Presumido ou Real'))

    ie = None
    if vazio(c['inscricao_estadual']) and not c['n_ie'] and rf.get('inscricao_estadual'):
        campos['inscricao_estadual'] = rf['inscricao_estadual'][:20]
        ie = (rf['inscricao_estadual'][:20], (rf.get('uf') or '')[:2] or None)

    end = None
    if not c['n_end'] and rf.get('logradouro') and rf.get('municipio'):
        end = dict(cep=cep_fmt(dig(rf.get('cep'))), logradouro=rf['logradouro'][:255], numero=(rf.get('numero') or 'S/N')[:20],
                   complemento=(rf.get('complemento') or None), bairro=rf.get('bairro') or None,
                   cidade=rf['municipio'], estado=(rf.get('uf') or '').upper()[:2] or None)
    return campos, end, ie, [(rotulo,) + a for a in avisos]


def precisa_consultar(c):
    """Só gasta consulta quando há algo vazio que a Receita pode dar (idempotência)."""
    return any(vazio(c[k]) for k in ('nome_fantasia', 'natureza_codigo', 'porte_empresa', 'data_inicio_atividade',
                                      'cnae_fiscal', 'cnae_fiscal_descricao', 'email', 'telefone', 'celular')) \
        or not c['n_end'] or (vazio(c['inscricao_estadual']) and not c['n_ie']) \
        or (c['avulso_do_conexa'] and c['regime_tributario'] == 'SIMPLES')


def gravar(c, campos, end, ie, fonte, base_em):
    from models.endereco_cliente import EnderecoCliente
    if campos:
        sets = ', '.join(f'{k} = %s' for k in campos)
        if 'natureza_codigo' in campos:
            sets += ', natureza_em = NOW()'
        execute_query(f'UPDATE clientes SET {sets}, alterado_por = %s, alterado_em = NOW() WHERE id = %s',
                      tuple(campos.values()) + (USUARIO_ID, c['id']))
    if ie:
        execute_query('INSERT IGNORE INTO clientes_inscricoes_estaduais (cliente_id, uf, ie, isenta, principal, origem) '
                      'VALUES (%s, %s, %s, 0, 1, %s)', (c['id'], ie[1], ie[0], 'RECEITA'))
    if end:
        EnderecoCliente.create(c['id'], 'COMERCIAL', principal=True, **end)
    depois = dict(campos)
    if end:
        depois['endereco'] = f"{end['logradouro']}, {end['numero']} - {end['cidade']}/{end['estado']}"
    depois['fonte'] = fonte
    if base_em:
        depois['receita_base_em'] = base_em
    registrar_agente('escrita.buscou_cnpj_migracao', 'cadastros', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                     tabela='clientes', registro_id=c['id'], depois=depois)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--todos', action='store_true', help='inclui PJ que não vieram da migração')
    ap.add_argument('--ids', help='só estes ids de clientes, separados por vírgula')
    ap.add_argument('--limite', type=int, default=0)
    ap.add_argument('--pausa', type=float, default=1.0, help='segundos entre consultas (gentileza com a BrasilAPI)')
    ap.add_argument('--csv', help='relatório: avisos e o que foi/seria preenchido')
    a = ap.parse_args()

    filtro = '' if a.todos else FILTRO_MIGRADOS
    if a.ids:
        filtro += ' AND c.id IN (%s)' % ','.join(str(int(x)) for x in a.ids.split(','))
    alvo = execute_query(SQL_ALVO.format(filtro=filtro), fetch=True) or []
    print(f"{'APLICANDO' if a.apply else 'DRY-RUN'}: {len(alvo)} empresas no alvo")
    base_em = RF.base_em(alvo[0]['cpf_cnpj']) if alvo else None
    print(f'foto da Receita: {base_em or "?"}')

    tot, linhas, campos_cnt, falhas = Counter(), [], Counter(), []
    consultadas = 0
    for c in alvo:
        if not precisa_consultar(c):
            tot['já completas (não consultou)'] += 1
            continue
        if a.limite and consultadas >= a.limite:
            break
        consultadas += 1
        cnpj = dig(c['cpf_cnpj'])
        try:
            rf, fonte = RF.consultar(cnpj, completar=True)
        except RF.CnpjNaoEncontrado:
            tot['CNPJ não encontrado na Receita'] += 1
            linhas.append((f"{c['numero_cliente'] or 'AV'} {c['nome_razao_social'][:40]}", 'CNPJ não encontrado na Receita', cnpj, ''))
            continue
        except RF.FonteIndisponivel as e:
            tot['fonte indisponível (rodar de novo)'] += 1
            falhas.append(c['id'])
            print(f"  ! {c['numero_cliente']} {c['nome_razao_social'][:40]}: fontes fora: {str(e)[:80]}")
            time.sleep(5)
            continue
        finally:
            time.sleep(a.pausa)

        campos, end, ie, avisos = montar(c, rf)
        linhas.extend(avisos)
        if not campos and not end and not ie:
            tot['consultadas sem nada a preencher'] += 1
            continue
        for k in campos:
            campos_cnt[k] += 1
        if end:
            campos_cnt['endereço (novo)'] += 1
        if ie:
            campos_cnt['IE na tabela'] += 1
        resumo = ', '.join(f'{k}={str(v)[:30]}' for k, v in campos.items()) + (f" | end: {end['logradouro'][:30]}, {end['numero']} - {end['cidade']}" if end else '')
        print(f"  {'AV' if c['avulso'] else c['numero_cliente']:>4} {c['nome_razao_social'][:38]:38} [{fonte}] {resumo}")
        linhas.append((f"{c['numero_cliente'] or 'AV'} {c['nome_razao_social'][:40]}", 'preencheu' if a.apply else 'preencheria', '', resumo))
        if a.apply:
            gravar(c, campos, end, ie, fonte, base_em)
        tot['preenchidas' if a.apply else 'preencheria'] += 1

    print()
    print('TOTAIS:', dict(tot))
    print('CAMPOS:', dict(campos_cnt))
    if falhas:
        print('FALHARAM (rodar de novo com --ids):', ','.join(map(str, falhas)))
    avisos_n = Counter(l[1] for l in linhas if l[1] not in ('preencheu', 'preencheria'))
    print('AVISOS:', dict(avisos_n))
    if a.csv:
        with open(a.csv, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['empresa', 'aviso', 'app', 'receita'])
            w.writerows(linhas)
        print('CSV:', a.csv)


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    main()
