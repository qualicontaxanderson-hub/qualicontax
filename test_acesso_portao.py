# -*- coding: utf-8 -*-
"""Teste OFFLINE do portão de acesso (sem banco, sem rede).

Falha quando:
  * existe rota (não pública) que o portão não sabe classificar — ela seria
    negada a todo não-admin sem ninguém ter decidido isso;
  * o MAPA de utils/acesso.py cita rota que não existe mais (sobra de rename);
  * o MAPA cita permissão que não está no catálogo (perfil nenhum a teria);
  * uma rota recebe id numérico na URL e ninguém disse se ele aponta para uma
    empresa — é por aí que a empresa reservada vazaria pela URL digitada.

Uso:  python test_acesso_portao.py     (sai 0 se tudo passar)
"""
import io
import logging
import os
import sys
import types
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
# Importar o app roda init_db.run_migrations() contra o banco do .env, que é o
# de PRODUÇÃO (ver memória "teste que escreve em produção"). Este teste só lê o
# url_map: marca as migrações como feitas para o import não tocar o banco.
os.environ['MIGRATIONS_DONE'] = '1'

# O scheduler não é importável no Windows e não deve subir num teste.
_sched = types.ModuleType('utils.scheduler')
_sched.init_scheduler = lambda app: False
sys.modules['utils.scheduler'] = _sched
logging.disable(logging.CRITICAL)

with redirect_stdout(io.StringIO()):          # os prints de import dos módulos
    from app import app                        # noqa: E402

from utils import acesso                       # noqa: E402
from utils.permissions import get_flat_catalog  # noqa: E402

# Argumentos numéricos da URL que NÃO identificam empresa (decisão consciente).
SEM_EMPRESA = {
    'pid', 'uid', 'link_id',                    # perfis/usuários (só admin)
    'contador_id', 'grupo_id', 'ramo_id',       # entidades sem dono-empresa
    'anp_id',                                   # vem junto do cliente_id na URL
    'plano_id', 'item_id', 'conciliacao_id', 'memorizacao_id',  # Contábil (sem dado em produção)
    'cid', 'sid',                               # categorias do catálogo de produtos
}
# O Financeiro é o do ESCRITÓRIO (as empresas dele, não as clientes): nenhum id
# ali aponta para cliente. Quem entra no Financeiro vê o Financeiro inteiro.
BLUEPRINTS_SEM_EMPRESA = {'financeiro'}
# Rotas cujo 'id' NÃO é de empresa.
ID_SEM_EMPRESA_PREFIXOS = ('grupos.', 'ramos_atividade.', 'municipios.', 'financeiro.',
                           'configuracoes.', 'contabil.')


def main():
    erros = []
    catalogo = set(get_flat_catalog())
    endpoints = {}
    for r in app.url_map.iter_rules():
        endpoints.setdefault(r.endpoint, set()).update(
            a for a, conv in r._converters.items()
            if type(conv).__name__ in ('IntegerConverter',))

    for ep in sorted(endpoints):
        bp = ep.split('.', 1)[0]
        if ep in acesso.ENDPOINTS_PUBLICOS or bp in acesso.BLUEPRINTS_PUBLICOS:
            continue
        regra = acesso.regra_da_rota(ep, app.view_functions[ep])
        if regra is None:
            erros.append(f'SEM CLASSIFICAÇÃO: {ep}')
            continue
        codigos = regra if isinstance(regra, tuple) else (regra,)
        for c in codigos:
            if c not in ('LOGIN', 'ADMIN') and c not in catalogo:
                erros.append(f'PERMISSÃO FORA DO CATÁLOGO: {ep} -> {c}')
        if regra == 'ADMIN' or bp in BLUEPRINTS_SEM_EMPRESA:
            continue
        for arg in sorted(endpoints[ep]):
            if arg in acesso._EMPRESA_POR_ARG or arg in SEM_EMPRESA:
                continue
            ep_regra = acesso._EMPRESA_POR_ENDPOINT.get(ep)
            if ep_regra and ep_regra[0] == arg:
                continue
            if arg == 'id' and ep.startswith(ID_SEM_EMPRESA_PREFIXOS):
                continue
            erros.append(f'ID SEM DONO DECIDIDO: {ep} <{arg}>')

    for ep in acesso.MAPA:
        if ep not in endpoints:
            erros.append(f'MAPA CITA ROTA INEXISTENTE: {ep}')
    for ep in acesso._EMPRESA_POR_ENDPOINT:
        if ep not in endpoints:
            erros.append(f'RESOLVEDOR CITA ROTA INEXISTENTE: {ep}')

    for e in erros:
        print('FALHA', e)
    print(f'{len(endpoints)} rotas conferidas, {len(erros)} falha(s).')
    return 1 if erros else 0


if __name__ == '__main__':
    raise SystemExit(main())
