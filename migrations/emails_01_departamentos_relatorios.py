# -*- coding: utf-8 -*-
"""E-mails automáticos: departamento com e-mail, destinos, envios e autoria do certificado.

POR QUE
-------
05/10/2026. O escritório vai receber cinco avisos por e-mail (certificados de
hoje, da semana, do mês, recebidos hoje e empresas novas), enviados por
app@qualicontax.com.br (Titan). O destino NÃO é o e-mail do usuário: o pessoal
cadastra gmail ou alias e "não tem como confiar muito" (Anderson). O aviso vai
para a caixa do DEPARTAMENTO — legalizacao@, dp@, escritafiscal@, contabil@.

A tabela ``departamentos`` já existia (nov/2025) e ``usuarios.departamento_id``
também, mas nenhuma tela mostrava. Esta migration só acrescenta o que falta.

O QUE CRIA
----------
* ``departamentos.email``  VARCHAR(255) NULL — a caixa que recebe os avisos.
* departamento ``Diretoria`` (anderson@), se ainda não existir.
* ``email_relatorio_destino`` (relatorio, departamento_id) — quem recebe o quê.
* ``email_envio`` — cada envio. ``chave_dia`` (relatorio|AAAA-MM-DD) é UNIQUE e
  só é preenchida no envio de verdade: é ela que impede o mesmo aviso de sair
  duas vezes no dia, mesmo com o cron rodando de novo. Teste fica com NULL.
* ``certificado_envio`` (content_hash) — quem mandou cada .pfx/.p12 pelo
  Q-Colabore. Chave é o content_hash do Dropbox, NUNCA o nome do arquivo (o
  nome traz a senha). O vínculo automático acha o autor por ele.
* ``certificado_vinculo`` — trilha de cada vínculo (origem, quem, validade
  nova e anterior). Sem FK de propósito: a trilha sobrevive à exclusão.

DADOS (só se o campo estiver vazio / diferente, e listados no dry-run)
-----
* e-mail de cada departamento, como o Anderson passou em 05/10/2026;
* departamento de cada usuário, idem (por login).

    python migrations/emails_01_departamentos_relatorios.py            # dry-run
    python migrations/emails_01_departamentos_relatorios.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.db_helper import execute_query          # noqa: E402

SQL_COLUNA = [
    'ALTER TABLE departamentos ADD COLUMN email VARCHAR(255) NULL, ALGORITHM=INSTANT',
    'ALTER TABLE departamentos ADD COLUMN email VARCHAR(255) NULL, ALGORITHM=INPLACE, LOCK=NONE',
]

TABELAS = {
    'email_relatorio_destino': """
CREATE TABLE IF NOT EXISTS email_relatorio_destino (
    relatorio        VARCHAR(40) NOT NULL,
    departamento_id  INT NOT NULL,
    criado_em        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    criado_por       INT NULL,
    PRIMARY KEY (relatorio, departamento_id),
    KEY idx_destino_dep (departamento_id),
    CONSTRAINT fk_destino_dep FOREIGN KEY (departamento_id)
        REFERENCES departamentos (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    'email_envio': """
CREATE TABLE IF NOT EXISTS email_envio (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    relatorio      VARCHAR(40) NOT NULL,
    data_ref       DATE NOT NULL,
    chave_dia      VARCHAR(60) NULL,
    teste          TINYINT(1) NOT NULL DEFAULT 0,
    status         ENUM('ENVIANDO','ENVIADO','FALHOU','VAZIO') NOT NULL DEFAULT 'ENVIANDO',
    destinatarios  TEXT NULL,
    assunto        VARCHAR(255) NULL,
    itens          INT NOT NULL DEFAULT 0,
    erro           VARCHAR(500) NULL,
    enviado_por    INT NULL,
    criado_em      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    concluido_em   DATETIME NULL,
    UNIQUE KEY uk_envio_chave_dia (chave_dia),
    KEY idx_envio_rel (relatorio, criado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    'certificado_envio': """
CREATE TABLE IF NOT EXISTS certificado_envio (
    content_hash   CHAR(64) NOT NULL PRIMARY KEY,
    usuario_id     INT NULL,
    usuario_nome   VARCHAR(120) NULL,
    ext            VARCHAR(8) NULL,
    enviado_em     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_cert_envio_em (enviado_em)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    'certificado_vinculo': """
CREATE TABLE IF NOT EXISTS certificado_vinculo (
    id                 INT AUTO_INCREMENT PRIMARY KEY,
    cliente_id         INT NOT NULL,
    cnpj               VARCHAR(14) NULL,
    validade           DATE NULL,
    validade_anterior  DATE NULL,
    origem             ENUM('FICHA','QCOLABORE','DROPBOX') NOT NULL,
    usuario_id         INT NULL,
    usuario_nome       VARCHAR(120) NULL,
    vinculado_em       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_vinculo_em (vinculado_em),
    KEY idx_vinculo_cliente (cliente_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
}

DIRETORIA = ('Diretoria', 'Direção do escritório')

# Passado pelo Anderson em 05/10/2026.
EMAILS_DEP = {
    'Fiscal': 'escritafiscal@qualicontax.com.br',
    'Contábil': 'contabil@qualicontax.com.br',
    'Pessoal': 'dp@qualicontax.com.br',
    'Legalização': 'legalizacao@qualicontax.com.br',
    'Atendimento': 'legalizacao@qualicontax.com.br',
    'Comercial': 'legalizacao@qualicontax.com.br',
    'Diretoria': 'anderson@qualicontax.com.br',
}
USUARIO_DEP = {
    'henrique': 'Fiscal', 'guylhermmy': 'Fiscal',
    'rodrigo': 'Legalização', 'guilherme.rocha': 'Legalização', 'miguel': 'Legalização',
    'jabes.oliveira': 'Legalização', 'melchisedech': 'Legalização', 'gabriel': 'Legalização',
    'albert': 'Pessoal',
    'anderson': 'Diretoria',
}


def _um(sql, params=None):
    return execute_query(sql, params, fetch=True, fetch_one=True) or {}


def coluna_existe():
    return int(_um('SELECT COUNT(*) n FROM INFORMATION_SCHEMA.COLUMNS '
                   ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s',
                   ('departamentos', 'email')).get('n') or 0) > 0


def tabela_existe(nome):
    return int(_um('SELECT COUNT(*) n FROM INFORMATION_SCHEMA.TABLES '
                   ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s', (nome,)).get('n') or 0) > 0


def _deps():
    cols = 'id, nome' + (', email' if coluna_existe() else ', NULL AS email')
    return {r['nome']: r for r in (execute_query(f'SELECT {cols} FROM departamentos', fetch=True) or [])}


def planejar_dados():
    """Lista do que mudaria nos DADOS: [(descricao, sql, params)]."""
    deps = _deps()
    plano = []
    if DIRETORIA[0] not in deps:
        plano.append((f'criar departamento {DIRETORIA[0]}',
                      'INSERT INTO departamentos (nome, descricao, ativo) VALUES (%s, %s, 1)', DIRETORIA))
    for nome, email in EMAILS_DEP.items():
        d = deps.get(nome)
        if d is None and nome != DIRETORIA[0]:
            plano.append((f'AVISO: departamento {nome} não existe; e-mail não gravado', None, None))
            continue
        if d is not None and (d.get('email') or '').strip():
            continue                      # já preenchido: não sobrescreve
        plano.append((f'{nome}: e-mail = {email}',
                      'UPDATE departamentos SET email = %s WHERE nome = %s AND (email IS NULL OR email = \'\')',
                      (email, nome)))
    usuarios = {r['login']: r for r in (execute_query(
        'SELECT u.id, u.login, u.nome, d.nome AS dep FROM usuarios u '
        ' LEFT JOIN departamentos d ON d.id = u.departamento_id', fetch=True) or [])}
    for login, dep in USUARIO_DEP.items():
        u = usuarios.get(login)
        if not u:
            plano.append((f'AVISO: usuário {login} não encontrado', None, None))
            continue
        if u.get('dep') == dep:
            continue
        plano.append((f'{u["nome"]}: {u.get("dep") or "sem departamento"} -> {dep}',
                      'UPDATE usuarios SET departamento_id = (SELECT id FROM departamentos WHERE nome = %s LIMIT 1) '
                      ' WHERE login = %s', (dep, login)))
    return plano


def main():
    ap = argparse.ArgumentParser(description='E-mails automáticos: departamentos e envios.')
    ap.add_argument('--apply', action='store_true', help='executa; sem isto é só dry-run')
    args = ap.parse_args()

    falta_coluna = not coluna_existe()
    faltam = [t for t in TABELAS if not tabela_existe(t)]

    if not args.apply:
        print('ESTRUTURA QUE SERIA CRIADA:')
        if falta_coluna:
            print('   ' + SQL_COLUNA[0] + ';')
        for t in faltam:
            print(TABELAS[t].strip() + ';')
        if not (falta_coluna or faltam):
            print('   (nada: já existe)')
        print('\nDADOS QUE SERIAM ALTERADOS:')
        for desc, _s, _p in planejar_dados():
            print('   ' + desc)
        print('\nROLLBACK, se precisar:')
        print('   DROP TABLE email_relatorio_destino, email_envio, certificado_envio, certificado_vinculo;')
        print('   ALTER TABLE departamentos DROP COLUMN email;')
        print("   DELETE FROM departamentos WHERE nome = 'Diretoria';  (antes, tire o Anderson dela)")
        print('\nDRY-RUN. Nada foi alterado. Repita com --apply para executar.')
        return 0

    if falta_coluna:
        for s in SQL_COLUNA:
            print('EXECUTANDO: ' + s)
            if execute_query(s, fetch=False) is not None:
                break
            print('   recusado; tentando a forma seguinte.')
    for t in faltam:
        print('EXECUTANDO: CREATE TABLE ' + t)
        execute_query(TABELAS[t], fetch=False)

    for desc, sql, params in planejar_dados():
        print(('EXECUTANDO: ' if sql else '') + desc)
        if sql:
            execute_query(sql, params, fetch=False)

    ok = coluna_existe() and all(tabela_existe(t) for t in TABELAS)
    resto = [d for d, s, _ in planejar_dados() if s]
    print('OK.' if ok and not resto else f'FALHOU: {resto or "estrutura incompleta"}')
    return 0 if ok and not resto else 1


if __name__ == '__main__':
    raise SystemExit(main())
