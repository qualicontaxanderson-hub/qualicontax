# -*- coding: utf-8 -*-
"""Converte os perfis de acesso para Ver / Incluir / Alterar / Excluir (Anderson, 09/11/2026).

Até hoje, fora de Clientes, uma permissão por tela abria, editava e excluía. Com o
catálogo novo (utils/permissions.py) cada ação tem o seu código, e o portão
(utils/acesso.py) pede a ação certa em cada rota. Sem este script, quem trabalha
hoje pararia amanhã: o perfil teria o Ver mas não o Incluir/Alterar.

O que faz, perfil por perfil (decisões do Anderson em 09/11/2026):
  * quem tem a tela ganha Ver + Incluir + Alterar + ações comuns do que JÁ fazia;
  * EXCLUIR e ações irreversíveis (gerar chave do Q-Robô, ligar/desligar captura,
    excluir sócio/contato...) NÃO entram — "exclusão eu vou liberando aos poucos";
  * saem as caixas que não faziam nada: Usuários, Perfis (rotas só do admin),
    Contratos antigos (só redirecionam) e Dashboard (abre para todos).

Uso:
  python scripts/permissoes_por_acao_09_11.py            # SIMULAÇÃO: só mostra
  python scripts/permissoes_por_acao_09_11.py --aplicar  # grava (idempotente)
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                       # noqa: E402,F401
from utils.db_helper import execute_query           # noqa: E402
from utils.atividade import registrar_agente         # noqa: E402
from utils.permissions import get_flat_catalog, codigos_de_risco  # noqa: E402

USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN = 62, 'Anderson Antunes Vieira', 'anderson'

EF, F = 'escrita_fiscal.', 'financeiro.'
# código que o perfil já tem -> códigos novos que mantêm o que ele faz hoje
GANHA = {
    EF + 'conf_compras': [EF + 'conf_compras.incluir', EF + 'conf_compras.alterar', EF + 'conf_compras.lote',
                          EF + 'status_sefaz'],
    EF + 'conf_saidas': [EF + 'conf_saidas.lote'],
    EF + 'conf_cte': [EF + 'conf_cte.incluir', EF + 'conf_cte.lote'],
    EF + 'conf_nfse': [EF + 'conf_nfse.lote'],
    EF + 'produtos_catalogo': [EF + 'produtos_catalogo.incluir', EF + 'produtos_catalogo.alterar'],
    EF + 'memorizacoes': [EF + 'memorizacoes.incluir', EF + 'memorizacoes.alterar'],
    EF + 'q_robo': [EF + 'q_robo.alterar', EF + 'q_robo.revelar'],
    EF + 'nfse_emitir': [EF + 'nfse_emitir.incluir', EF + 'nfse_emitir.alterar'],
    EF + 'nfse_matriz': [EF + 'nfse_matriz.alterar'],
    'clientes.index': ['cadastros.tabelas'],
    'clientes.edit': ['clientes.inativar', 'cadastros.tabelas.incluir', 'cadastros.tabelas.alterar'],
    'grupos.index': ['grupos.incluir', 'grupos.alterar'],
    'contabil.plano_contas': ['contabil.plano_contas.incluir'],
    'contabil.conciliacoes': ['contabil.conciliacoes.incluir', 'contabil.conciliacoes.alterar'],
    F + 'titulos': [F + 'titulos.incluir', F + 'titulos.alterar'],
    F + 'extrato': [F + 'extrato.incluir', F + 'extrato.alterar', F + 'contas', F + 'contas.incluir', F + 'contas.alterar'],
    F + 'categorias': [F + 'categorias.incluir', F + 'categorias.alterar'],
    F + 'fluxo': [F + 'fluxo.alterar'],
    F + 'empresas': [F + 'empresas.alterar'],
    F + 'programacoes': [F + 'programacoes.incluir', F + 'programacoes.alterar', F + 'programacoes.gerar'],
    F + 'notas': [F + 'notas.incluir', F + 'notas.configurar'],
    F + 'cobranca': [F + 'cobranca.incluir', F + 'cobranca.alterar'],
    F + 'honorarios': [F + 'honorarios.alterar', F + 'honorarios.aprovar'],
    F + 'lancamentos': [F + 'lancamentos.incluir'],
    F + 'lote': [F + 'lote.incluir'],
    'processos.index': ['processos.incluir'],
    'configuracoes.caixa_entrada': ['configuracoes.caixa_entrada.alterar'],
}
SAEM = {'configuracoes.usuarios', 'configuracoes.perfis', 'contratos.list_contratos',
        'contratos.create_contrato', 'dashboard.index'}


def plano():
    catalogo, risco = set(get_flat_catalog()), codigos_de_risco()
    for novos in GANHA.values():
        for c in novos:
            assert c in catalogo, f'{c} não está no catálogo'
            assert c not in risco, f'{c} é de risco: não pode entrar pelo script'
    perfis = execute_query('SELECT id, nome FROM perfis_acesso ORDER BY nome', fetch=True) or []
    out = []
    for p in perfis:
        tem = {r['permissao_codigo'] for r in (execute_query(
            'SELECT permissao_codigo FROM perfil_permissoes WHERE perfil_id = %s', (p['id'],), fetch=True) or [])}
        entra = sorted({n for c in tem for n in GANHA.get(c, [])} - tem)
        sai = sorted(tem & SAEM)
        desconhecidos = sorted(tem - catalogo - SAEM)
        out.append((p, tem, entra, sai, desconhecidos))
    return out


def main():
    aplicar = '--aplicar' in sys.argv
    print('MODO:', 'APLICAR (grava)' if aplicar else 'SIMULAÇÃO (nada é gravado)')
    for p, tem, entra, sai, desconhecidos in plano():
        print(f"\n[{p['id']}] {p['nome']} — tem {len(tem)}")
        for c in entra:
            print('   + ', c)
        for c in sai:
            print('   - ', c)
        for c in desconhecidos:
            print('   ? fora do catálogo (fica como está):', c)
        if not entra and not sai:
            print('    (nada muda)')
        if not aplicar:
            continue
        for c in entra:
            execute_query('INSERT IGNORE INTO perfil_permissoes (perfil_id, permissao_codigo) VALUES (%s, %s)',
                          (p['id'], c), fetch=False)
        for c in sai:
            execute_query('DELETE FROM perfil_permissoes WHERE perfil_id = %s AND permissao_codigo = %s',
                          (p['id'], c), fetch=False)
        if entra or sai:
            registrar_agente('perfil_permissoes_por_acao', 'configuracoes', USUARIO_ID, USUARIO_NOME, USUARIO_LOGIN,
                             tabela='perfis_acesso', registro_id=p['id'],
                             depois={'entrou': entra, 'saiu': sai,
                                     'permissoes': sorted((tem | set(entra)) - set(sai))})
    print('\npronto.' if aplicar else '\nSimulação. Para gravar: --aplicar')


if __name__ == '__main__':
    main()
