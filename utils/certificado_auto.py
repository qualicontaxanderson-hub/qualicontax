# -*- coding: utf-8 -*-
"""Instalador automático de certificado digital deixado na _ENTRADA (13/09/2026).

A REGRA, em uma frase: se o arquivo .pfx/.p12 na _ENTRADA diz de quem é
(número da empresa ou CNPJ/CPF no nome) E diz a senha ("senha 1234" no nome),
o roteador vincula sozinho, no tick de 5 minutos, sem ninguém clicar em nada.

Pedido do Anderson em 13/09/2026: o .p12 do posto 175 estava parado na
_ENTRADA ("só habilitamos a pfx"), e "quando tiver a palavra senha logo na
sequência terá a senha, aí o sistema tenta automaticamente; se não consegue,
o usuário segue o padrão normal".

O QUE ESTE MÓDULO NÃO FAZ
-------------------------
* Não escolhe: arquivo que casa com DUAS empresas (ou com nenhuma) fica onde
  está. Arquivo sem senha no nome também — a tela pede a senha como sempre.
* Não repete a lógica do vínculo: chama ``routes.clientes.vincular_certificado_arquivo``,
  o MESMO núcleo da tela (valida a senha, recusa vencido, confere titular —
  igual ou mesma raiz —, preserva o anterior, move para CERTIFICADO/{CNPJ}.pfx,
  grava dfe_certificados, audita). Procuração NUNCA entra pelo automático:
  titular de terceiro exige a marcação consciente na tela.
* Não loga o nome do arquivo: ele carrega a senha. Loga o número da empresa
  e o resultado. Depois do vínculo o arquivo é renomeado para {CNPJ}.pfx e a
  senha some do Dropbox.
"""
import logging

logger = logging.getLogger(__name__)


def _clientes_minimos():
    """Só o que a regra de nome precisa. Uma consulta, tabela pequena."""
    from utils.db_helper import execute_query
    return execute_query(
        "SELECT id, numero_cliente, cpf_cnpj, nome_razao_social FROM clientes "
        " WHERE COALESCE(numero_cliente,'') <> '' OR COALESCE(cpf_cnpj,'') <> ''",
        fetch=True) or []


def pendentes(svc):
    """Certificados na _ENTRADA e o que dá para decidir sobre cada um.

    Devolve lista de dicts ``{item, donos, senha}``: ``donos`` são os clientes
    cujo nome casa (0, 1 ou vários) e ``senha`` é a senha embutida no nome
    (ou None). Só lista; não baixa, não move.
    """
    from routes.clientes import _e_certificado, _nome_casa_empresa, _senha_no_nome
    itens = [i for i in svc.list_folder(svc.pasta_cert_novo())
             if i.get('is_file') and _e_certificado(i.get('name') or '')]
    if not itens:
        return []
    clientes = _clientes_minimos()
    saida = []
    for item in itens:
        nome = item.get('name') or ''
        donos = [c for c in clientes if _nome_casa_empresa(nome, c)]
        saida.append({'item': item, 'donos': donos, 'senha': _senha_no_nome(nome)})
    return saida


def instalar_pendentes(svc, dry=False):
    """Vincula cada certificado da _ENTRADA que tem dono único e senha no nome.

    Devolve ``{'vistos', 'instalados', 'sem_dono', 'ambiguos', 'sem_senha',
    'falhas'}`` (contagens) — para o log do roteador e para teste.
    """
    r = {'vistos': 0, 'instalados': 0, 'sem_dono': 0, 'ambiguos': 0,
         'sem_senha': 0, 'falhas': 0}
    if not svc.is_configured():
        return r
    from routes.clientes import vincular_certificado_arquivo
    from models.cliente import Cliente

    for p in pendentes(svc):
        r['vistos'] += 1
        donos, senha = p['donos'], p['senha']
        if not donos:
            r['sem_dono'] += 1
            logger.info('[certificado] arquivo na _ENTRADA sem empresa reconhecida '
                        'no nome; fica para vínculo pela tela.')
            continue
        if len(donos) > 1:
            r['ambiguos'] += 1
            logger.warning('[certificado] arquivo na _ENTRADA casa com %d empresas '
                           '(%s); fica para vínculo pela tela.', len(donos),
                           ', '.join(str(c.get('numero_cliente')) for c in donos))
            continue
        numero = donos[0].get('numero_cliente')
        if not senha:
            r['sem_senha'] += 1
            logger.info('[certificado] empresa %s: arquivo na _ENTRADA sem senha no '
                        'nome; aguarda a senha pela tela.', numero)
            continue
        if dry:
            logger.warning('[certificado] DRY-RUN: vincularia o certificado da '
                           'empresa %s com a senha do nome.', numero)
            continue
        cliente = Cliente.get_by_id(donos[0]['id']) or donos[0]
        try:
            ok, payload, _http = vincular_certificado_arquivo(cliente, senha)
        except Exception:
            r['falhas'] += 1
            logger.exception('[certificado] empresa %s: erro inesperado no vínculo '
                             'automático; arquivo segue na _ENTRADA.', numero)
            continue
        if ok:
            r['instalados'] += 1
            logger.warning('[certificado] empresa %s: certificado %s vinculado '
                           'automaticamente (validade %s).%s', numero,
                           payload.get('documento'), payload.get('validade'),
                           ' ' + payload['aviso'] if payload.get('aviso') else '')
        else:
            r['falhas'] += 1
            logger.warning('[certificado] empresa %s: vínculo automático não feito '
                           '— %s Arquivo segue na _ENTRADA para a tela.', numero,
                           payload.get('erro'))
    if r['vistos']:
        logger.warning('[certificado] _ENTRADA: %s', r)
    return r
