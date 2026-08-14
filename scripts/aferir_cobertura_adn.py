# -*- coding: utf-8 -*-
"""FASE 0 — aferição de cobertura do ADN. SÓ MEDE. NÃO GRAVA NADA.

Responde a pergunta que decide o projeto inteiro: **quanto da carteira da
Qualicontax o ADN realmente entrega?** O agregado nacional é bom, mas município
com sistema próprio não integrado mantém a nota só na base local — e o que
importa aqui não é a média do Brasil, é esta carteira.

    > 80%    constrói; vira produto
    50–80%   constrói, mas o lançamento manual segue oficial.
             NÃO anunciar como "automático"
    < 50%    engaveta; reavalia em 6 meses

A decisão é do Anderson, não do script. O script traz o número.

O QUE ELE NÃO FAZ
-----------------
* **Não cria tabela.** Nenhuma. Se a cobertura vier baixa o projeto engaveta, e
  tabela órfã em banco de produção é lixo que ninguém tem coragem de apagar
  depois.
* **Não grava nada.** Nem em banco, nem no Dropbox.
* **NUNCA MANIFESTA.** O ADN aceita eventos de manifestação; este script só
  chama `GET`. Manifestar tem efeito fiscal e é irreversível.

Leitura no banco existe só para achar o certificado (`dfe_certificados`) e no
Dropbox para baixar o `.pfx`.

ONDE RODAR
----------
**Colab, não nesta máquina.** O ADN exige mTLS com certificado ICP-Brasil, e a
máquina do Anderson não tem as credenciais do Dropbox nem alcança o servidor —
o TLS é recusado antes do handshake terminar.

    !git clone <repo> && cd qualicontax
    !pip install -q mysql-connector-python dropbox cryptography pyOpenSSL requests
    # variáveis de ambiente: DB_*, DROPBOX_*, CRYPTO_KEY
    !python scripts/aferir_cobertura_adn.py

Ambiente: **produção restrita**, sempre. É o default e não há chave para mudar.
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.integrations.nfse_adn import client, parser   # noqa: E402

# Os três da Fase 0. Escolhidos por VARIEDADE DE PERFIL, não de geografia: a
# cobertura varia por município, e nas TOMADAS quem emite é o prestador, que
# pode estar em qualquer lugar do país.
CNPJS_TESTE = [
    ('40486724000101', 'MEGA TERCEIRIZAÇÃO — Santo André/SP, Simples',
     'única que EMITE NFS-e; mede a cobertura das emitidas'),
    ('33503987000116', 'POSTO NOVO HORIZONTE — Goiatuba/GO, Lucro Real',
     'só TOMA serviço; município pequeno, o caso duvidoso'),
    ('55244401000260', 'CLIRA TRANSPORTES — Curitiba/PR, Simples',
     'filial /0002 — testa o certificado por RAIZ de CNPJ'),
]

# Trava de segurança da aferição. Não é paginação: é o teto que impede um
# backfill acidental de anos de histórico durante uma medição.
MAX_DOCS = 5000
MAX_LOTES = 200


def _cliente_id_do_cnpj(cnpj):
    from utils.db_helper import execute_query
    linha = execute_query(
        """SELECT id, nome_razao_social FROM clientes
            WHERE REGEXP_REPLACE(cpf_cnpj, '[^0-9]', '') = %s LIMIT 1""",
        (cnpj,), fetch=True, fetch_one=True)
    return (linha or {}).get('id'), (linha or {}).get('nome_razao_social')


def aferir(cnpj, rotulo, motivo):
    """Percorre o cursor do CNPJ do NSU 0 até o fim. Devolve dict de métricas."""
    print(f'\n{"=" * 74}\n{rotulo}\n  CNPJ {cnpj} — {motivo}\n{"=" * 74}')

    cliente_id, razao = _cliente_id_do_cnpj(cnpj)
    if not cliente_id:
        return {'cnpj': cnpj, 'erro': 'CNPJ não encontrado em clientes'}
    print(f'  cliente_id {cliente_id} — {razao}')

    try:
        cert = client.resolver_certificado(cliente_id)
    except client.SemCertificado as exc:
        print(f'  SEM CERTIFICADO: {exc}')
        return {'cnpj': cnpj, 'erro': str(exc)}

    if cert.por_raiz:
        print(f'  >>> CERTIFICADO POR RAIZ ({cert.raiz}), do cliente {cert.dono_id}.')
        print('      Se a consulta funcionar, a regra da raiz está PROVADA — e isso')
        print('      dispensa um e-CNPJ por estabelecimento na carteira inteira.')
    else:
        print(f'  certificado próprio ({cert.cnpj})')

    try:
        sessao = client.abrir_sessao(cert)
    except client.SemCertificado as exc:
        print(f'  FALHA AO ABRIR O CERTIFICADO: {exc}')
        return {'cnpj': cnpj, 'erro': str(exc)}

    papeis = Counter()
    tipos_doc = Counter()
    tipos_evento = Counter()
    sem_papel = []
    falhas_parse = []
    competencias = []
    docs = 0
    nsu = 0
    lotes = 0

    while lotes < MAX_LOTES and docs < MAX_DOCS:
        try:
            lote = client.buscar_lote(
                sessao, nsu, cnpj_consulta=cnpj if cert.por_raiz else None)
        except client.ADNAuthError as exc:
            print(f'\n  RECUSA DE AUTORIZAÇÃO: {exc}')
            if cert.por_raiz:
                print('  >>> A regra da RAIZ NÃO funcionou para este CNPJ.')
                print('      Consequência: cada estabelecimento precisará do próprio')
                print('      e-CNPJ, e isso muda o custo do projeto.')
            return {'cnpj': cnpj, 'erro': f'401/403: {exc}', 'por_raiz': cert.por_raiz}
        except client.ADNError as exc:
            print(f'\n  ERRO: {exc}')
            return {'cnpj': cnpj, 'erro': str(exc), 'docs_ate_agora': docs}

        lotes += 1
        if lote.vazio:
            print(f'  fim da fila em NSU {nsu} ({lotes} lote(s), {docs} documento(s))')
            break

        for d in lote.documentos:
            docs += 1
            tipo = (d.get('TipoDocumento') or '?').upper()
            tipos_doc[tipo] += 1

            if tipo == 'EVENTO':
                tipos_evento[d.get('TipoEvento') or '?'] += 1
                continue

            try:
                xml = client.desempacotar_xml(d.get('ArquivoXml'))
                reg = parser.para_registro(xml, d, cliente_id, cnpj)
            except (client.ADNError, parser.XmlInvalido) as exc:
                falhas_parse.append((d.get('NSU'), str(exc)[:70]))
                continue

            papel = reg.get('papel')
            papeis[papel or '(NÃO IDENTIFICADO)'] += 1
            if papel is None:
                sem_papel.append(d.get('NSU'))
            if reg.get('competencia'):
                competencias.append(reg['competencia'])

        maior = lote.ultimo_nsu       # só para navegar a medição; não é cursor
        if maior is None or maior <= nsu:
            print(f'  NSU não avançou (ficou em {nsu}) — encerrando para não repetir.')
            break
        nsu = maior
        print(f'    ... NSU {nsu}, {docs} documento(s)', end='\r')

    if docs >= MAX_DOCS or lotes >= MAX_LOTES:
        print(f'\n  *** TETO DA AFERIÇÃO ATINGIDO ({docs} docs / {lotes} lotes).')
        print('      Há MAIS histórico do que isto. O número abaixo é PISO, não total.')

    nfse = tipos_doc.get('NFSE', 0)
    print(f'\n  documentos          : {docs}')
    print(f'    NFS-e             : {nfse}')
    print(f'    eventos           : {tipos_doc.get("EVENTO", 0)}')
    for t, n in tipos_doc.most_common():
        if t not in ('NFSE', 'EVENTO'):
            print(f'    {t:<18}: {n}')
    print('  por papel:')
    for papel, n in papeis.most_common():
        pct = (100.0 * n / nfse) if nfse else 0
        print(f'    {papel:<18}: {n:>5}  ({pct:.1f}%)')
    if tipos_evento:
        print('  eventos por tipo:')
        for t, n in tipos_evento.most_common():
            conhecido = '' if t in parser.MAPA_EVENTO_SITUACAO else '  <<< FORA DO MAPA'
            print(f'    {t:<42}: {n:>4}{conhecido}')
    if competencias:
        print(f'  competências        : {min(competencias)} a {max(competencias)}')
    if sem_papel:
        print(f'  SEM PAPEL           : {len(sem_papel)} (iriam para quarentena) '
              f'— NSUs {sem_papel[:8]}')
    if falhas_parse:
        print(f'  FALHAS DE LEITURA   : {len(falhas_parse)}')
        for nsu_f, err in falhas_parse[:5]:
            print(f'    NSU {nsu_f}: {err}')

    return {
        'cnpj': cnpj, 'rotulo': rotulo, 'cliente_id': cliente_id,
        'por_raiz': cert.por_raiz, 'docs': docs, 'nfse': nfse,
        'emitidas': papeis.get('emitente', 0),
        'tomadas': papeis.get('tomador', 0),
        'intermediadas': papeis.get('intermediario', 0),
        'sem_papel': len(sem_papel), 'falhas_parse': len(falhas_parse),
        'eventos': tipos_doc.get('EVENTO', 0),
        'competencia_min': min(competencias) if competencias else None,
        'competencia_max': max(competencias) if competencias else None,
        'teto_atingido': docs >= MAX_DOCS or lotes >= MAX_LOTES,
    }


def main():
    print('FASE 0 — aferição de cobertura do ADN')
    print('SOMENTE LEITURA. Não cria tabela, não grava, NUNCA manifesta.')
    print(f'Ambiente: {client.Ambiente.PRODUCAO_RESTRITA.name}')

    resultados = [aferir(c, r, m) for c, r, m in CNPJS_TESTE]

    print(f'\n\n{"=" * 74}\nRESUMO\n{"=" * 74}')
    print(f'{"empresa":<34}{"NFS-e":>7}{"emit":>7}{"toma":>7}{"interm":>8}{"evt":>6}')
    for r in resultados:
        if r.get('erro'):
            print(f'{r["cnpj"]:<34}  ERRO: {r["erro"][:60]}')
            continue
        print(f'{r["rotulo"][:32]:<34}{r["nfse"]:>7}{r["emitidas"]:>7}'
              f'{r["tomadas"]:>7}{r["intermediadas"]:>8}{r["eventos"]:>6}')

    print('\nO QUE FAZER COM ESTES NÚMEROS:')
    print('  1. Compare as TOMADAS com o que o escritório lançou à mão no último')
    print('     trimestre. A razão entre os dois é a cobertura real — o total')
    print('     acima sozinho não diz nada.')
    print('  2. Compare as EMITIDAS com o faturamento de serviço da MEGA.')
    print('  3. Se a CLIRA funcionou por raiz, a regra está provada.')
    for r in resultados:
        if r.get('teto_atingido'):
            print(f'  4. {r["cnpj"]}: teto atingido — o número é PISO, não total.')

    print('\nNada foi gravado. Nenhuma tabela foi criada. Nenhum evento enviado.')


if __name__ == '__main__':
    main()
