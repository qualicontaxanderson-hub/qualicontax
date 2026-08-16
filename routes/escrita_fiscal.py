"""Blueprint Escrita Fiscal — Conferência de Compras (NF-e)."""
import logging
import os
import re
import threading
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, jsonify, send_file, Response,
)
from io import BytesIO
from flask_login import current_user
from utils.auth_helper import login_required, permission_required
from utils.atividade import registrar, rotulo_empresa
from utils.home_atividade import (card_participacoes, card_trabalhando_agora,
                                  card_chegando_cliente)
from utils.db_helper import execute_query, execute_many, transacao
from utils.nfe_parser import parse_nfe_xml
from utils import dropbox_sync
from utils.dropbox_sync import DropboxAuthError, DropboxError
from utils import import_jobs
# Núcleo de gravação da NF-e (extraído para utils/nfe_import.py para ser reusado
# pela captura SEFAZ sem import circular). Reexportado aqui — os chamadores
# antigos (upload/sync) continuam usando os mesmos nomes.
from utils.nfe_import import (
    _MAX_XML_SIZE, _save_nfe, _save_nfe_dual, _lookup_vinculo,
)
# Upload manual de CT-e (fecha as saídas de CT-e). Espelha o de NF-e, mas resolve
# empresa/papel pelo próprio XML (papel_do_cliente) e grava via _save_cte.
from utils.cte_parser import parse_cte_xml, papel_do_cliente
from utils.cte_import import _save_cte
# Núcleo de LANÇAMENTO de um .xml (extraído para utils/fiscal_ingest.py para o
# cron_roteador.py poder importar sem arrastar Flask). Reexportado aqui — os
# chamadores antigos deste blueprint continuam usando os mesmos nomes.
from utils.fiscal_ingest import (
    _build_cliente_doc_cache, _find_cliente_by_doc_digits,
    _CTE_PARTES, _MODELOS_CTE, _RE_CHAVE_ID, _RE_RAIZ_XML, _RAIZES_CTE,
    _modelo_do_xml, _e_cte, _importar_um_cte,
)
from models.dfe_certificado import DfeCertificado
from models.cliente_contador import ClienteContador
from models.robo_config import RoboConfig
from utils import qrobo_chaves, qrobo_status
from config import Config

logger = logging.getLogger(__name__)
_DROPBOX_AUTH_ERROR_MSG = (
    'Credenciais Dropbox inválidas ou expiradas. '
    'Verifique DROPBOX_REFRESH_TOKEN, DROPBOX_APP_KEY e DROPBOX_APP_SECRET.'
)
# Máximo de arquivos por lote no endpoint SÍNCRONO (api_importar_dropbox).
# Limitado pelo timeout do worker Gunicorn (300 s). Não alterar sem revisar
# a math do _GUNICORN_WORKER_TIMEOUT, pois cada arquivo inclui 1 download +
# 2-3 queries DB + 1 move no Dropbox — tudo operações de rede.
_DROPBOX_BATCH_LIMIT = 20

# Máximo de arquivos por lote nos jobs de BACKGROUND (_run_import_job e
# importar_departamento_background). Sem restrição de timeout HTTP, então
# pode ser maior. Lotes maiores reduzem o número de round-trips para listar
# a pasta NOVO (list_xml_files), que é a principal fonte de overhead.
# 100 arquivos: 9000 arquivos ÷ 100 = 90 iterações vs 450 com batch=20.
_DROPBOX_BATCH_LIMIT_BG = 100

# Máximo de iterações de lote no job de background — guarda-chuva contra
# loop infinito caso a lógica de parada por progresso falhe.
_MAX_IMPORT_ITERATIONS = 1000

# Máximo de mensagens de erro de detalhe armazenadas por job de importação.
_MAX_ERROR_DETAILS = 50

# Workers para download e move paralelos do Dropbox.
_DOWNLOAD_WORKERS = 5

# Timeout do worker Gunicorn (segundos). Margem reservada para serialização
# e envio da resposta HTTP antes de o worker ser encerrado pelo gunicorn.
_GUNICORN_WORKER_TIMEOUT = 300
_GUNICORN_RESPONSE_MARGIN = 60

# Namespace NF-e (usado para detecção de XMLs de evento)
_NFE_NS = 'http://www.portalfiscal.inf.br/nfe'
# Tags raiz de XMLs de evento NF-e (carta de correção, cancelamento, manifestação…)
_NFE_EVENT_ROOT_TAGS = frozenset({
    'procEventoNFe', 'envEvento', 'retEnvEvento',
    'resNFe', 'retCancNFe', 'procCancNFe',
})

# Tags raiz de CT-e — não são NF-e, devem ficar em NOVO para processamento futuro.
_CTE_ROOT_TAGS = frozenset({
    'cteProc', 'procCTe', 'CTe', 'retCTe', 'CTeOS', 'cteOSProc',
})

# Códigos tpEvento por categoria
_TPEVENTO_CANCELAMENTO = frozenset({'110111', '111111', '110113', '110112'})
_TPEVENTO_CCE          = frozenset({'110110'})
_TPEVENTO_MANIFESTACAO = frozenset({'210200', '210210', '210220', '210240'})
_TPEVENTO_DESCR: dict = {
    '110111': 'Cancelamento',
    '111111': 'Cancelamento por Substituição',
    '110113': 'Cancelamento por Substituição',
    '110112': 'Encerramento',
    '110110': 'Carta de Correção (CC-e)',
    '210200': 'Confirmação da Operação',
    '210210': 'Ciência da Operação',
    '210220': 'Desconhecimento da Operação',
    '210240': 'Operação não Realizada',
}

# Categorias de produtos para Postos de Combustíveis
CATEGORIAS_COMBUSTIVEL = [
    ('Combustíveis', [
        'Gasolina Comum', 'Gasolina Aditivada',
        'Etanol Comum', 'Etanol Aditivado',
        'Diesel S-500 Comum', 'Diesel S-500 Aditivado',
        'Diesel S10 Comum', 'Diesel S10 Aditivado',
    ]),
    ('Loja de Conveniência', ['Cigarros', 'Sorvetes', 'Salgados', 'Outros']),
    ('Lubrificantes e Aditivos', ['Lubrificantes', 'Aditivos', 'Outros']),
    ('Insumos e Despesas', []),
]

escrita_fiscal = Blueprint('escrita_fiscal', __name__, url_prefix='/escrita-fiscal')


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------
def _get_empresas():
    # Ordena pelo NÚMERO do cliente (crescente), não pelo nome: é assim que o
    # escritório se refere às empresas, e o seletor mostra "número - nome".
    # numero_cliente é varchar, então o CAST evita 10 vir antes de 9; o nome
    # entra como desempate para o caso de número vazio/repetido.
    return execute_query(
        "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes "
        "WHERE situacao='ATIVO' "
        "ORDER BY CAST(numero_cliente AS UNSIGNED), nome_razao_social",
        fetch=True,
    ) or []


# ---------------------------------------------------------------------------
# Filtros multi-valor
#
# Os <select> de emitente/destinatário/transportadora e de UF viraram
# multi-seleção. O front manda os valores escolhidos numa string separada por
# vírgula (uma string simples atravessa tanto a query string quanto o corpo
# JSON do excluir-lote sem virar lista aninhada). Aqui a string volta a ser
# lista e vira "= %s" (1 valor) ou "IN (%s, %s, ...)" (vários).
# Valor único continua funcionando — nada quebra para quem manda só um.
# ---------------------------------------------------------------------------
def _filtro_lista(valor):
    """'a,b' -> ['a', 'b']; '' -> []. Também aceita lista (corpo JSON)."""
    itens = valor if isinstance(valor, (list, tuple)) else str(valor or '').split(',')
    return [str(i).strip() for i in itens if str(i).strip()]


def _clausula_in(coluna, valores, params):
    """Devolve a cláusula e EMPILHA os params na ordem em que os %s aparecem.

    Deve ser chamada no mesmo ponto em que a cláusula entra no WHERE, para a
    ordem dos placeholders bater com a dos parâmetros."""
    if len(valores) == 1:
        params.append(valores[0])
        return f'{coluna} = %s'
    params.extend(valores)
    return f"{coluna} IN ({', '.join(['%s'] * len(valores))})"


def _get_grupos():
    return execute_query(
        "SELECT id, nome FROM grupos_clientes WHERE situacao='ATIVO' ORDER BY nome",
        fetch=True,
    ) or []


def _get_categorias():
    """Retorna categorias e suas subcategorias do banco."""
    cats = execute_query(
        "SELECT id, nome FROM nfe_produto_categorias ORDER BY ordem, nome",
        fetch=True,
    ) or []
    subs = execute_query(
        "SELECT categoria_id, nome FROM nfe_produto_subcategorias ORDER BY ordem, nome",
        fetch=True,
    ) or []
    subs_map = {}
    for s in subs:
        subs_map.setdefault(s['categoria_id'], []).append(s['nome'])
    return [(c['nome'], subs_map.get(c['id'], [])) for c in cats]


# _build_cliente_doc_cache e _find_cliente_by_doc_digits foram movidos para
# utils/fiscal_ingest.py (reexportados no topo deste arquivo).



def _exigir_empresa(cliente_id):
    """Valida o escopo obrigatório da memorização (Fase 1: sempre por empresa).

    Sem cliente_id não existe escopo possível: gravar assim produziria uma linha
    com as 3 FKs nulas (a "global silenciosa"), que reaplicava o de-para de um
    cliente em todos os outros. Erro explícito em vez de vazamento.
    """
    if not cliente_id:
        raise ValueError('Memorização exige empresa (cliente_id) definida.')
    return int(cliente_id)


# ---------------------------------------------------------------------------
# D3.1 Etapa 3 — POOL VIVO. Um vínculo criado numa empresa que pertence a um
# conjunto passa a valer para TODOS os membros, na MESMA transação.
#
# REGRA DE SEGURANÇA (inegociável): o fan-out só CRIA regra onde o membro ainda
# não tem uma para o par, e o retroativo preenche apenas itens SEM vínculo
# (produto_catalogo_id NULL). Item que já tem vínculo DIFERENTE NÃO é
# sobrescrito — isso continua sendo decisão explícita do operador pelo "Clonar",
# que tem preview. Reclassificar nota alheia em silêncio é o que ninguém
# descobre até fechar errado.
#
# Schema-safe: lê só memo_clone_membro (existe desde a Fase 3a); não depende de
# nenhuma coluna/tabela da migration nova, então funciona antes dela rodar.
# ---------------------------------------------------------------------------
def _pool_vivo_ligado():
    """Interruptor mestre do pool vivo. DESLIGADO por padrão (MEMO_POOL_VIVO=0):
    só propaga quando o Anderson ligar explicitamente (=1), depois dos testes."""
    return os.getenv('MEMO_POOL_VIVO', '0').strip().lower() in ('1', 'true', 'on', 'sim', 'yes')


def _conjunto_fanout(origem_cliente_id, pares, tipo='entrada'):
    """Replica (emit_cnpj, cod, descricao, produto) para os OUTROS membros do
    conjunto de `origem_cliente_id`. Devolve dict de contagens (para log) ou None
    quando não há conjunto / nada a fazer."""
    # Interruptor mestre: com MEMO_POOL_VIVO=0 (padrão) o fan-out fica TRAVADO —
    # um vincular numa empresa do conjunto NÃO propaga. Ligar é decisão do
    # Anderson, depois dos testes passarem.
    if not _pool_vivo_ligado():
        return None
    if not origem_cliente_id or not pares:
        return None
    # Segurança extra: se a migration da gestão ainda não rodou, também não age.
    if not _memo_col_existe('memo_clone_membro', 'corte_data'):
        return None
    # Só propaga dentro do conjunto FISCAL da empresa (isola departamento).
    sid = _fiscal_set_de(origem_cliente_id)
    if not sid:
        return None
    membros = [r['cliente_id'] for r in (execute_query(
        "SELECT cliente_id FROM memo_clone_membro WHERE set_id = %s AND cliente_id <> %s",
        (sid, origem_cliente_id), fetch=True) or [])]
    if not membros:
        return None

    alvo = {}
    for emit, cod, desc, prod in pares:
        if emit and cod and prod:
            alvo[(emit, cod)] = (desc or '', prod)
    if not alvo:
        return None

    n_regras = n_itens = n_divergentes = 0
    afetados = set()
    with transacao() as cur:
        for m in membros:
            cur.execute(
                "SELECT emit_cnpj, codigo_produto_xml, produto_catalogo_id "
                "  FROM nfe_produto_vinculo "
                " WHERE cliente_id = %s AND grupo_id IS NULL AND ramo_atividade_id IS NULL "
                "   AND tipo = %s", (m, tipo))
            existentes = {(r['emit_cnpj'], r['codigo_produto_xml']): r['produto_catalogo_id']
                          for r in cur.fetchall()}
            aplicaveis = []  # pares que este membro pode receber (sem regra, ou regra IGUAL)
            for (emit, cod), (desc, prod) in alvo.items():
                atual = existentes.get((emit, cod))
                if atual is None:
                    # Só insere onde confirmadamente NÃO existe (checado acima, na
                    # transação) — o UNIQUE não protege linhas com grupo_id NULL.
                    cur.execute(
                        "INSERT INTO nfe_produto_vinculo "
                        "  (cliente_id, grupo_id, ramo_atividade_id, emit_cnpj, "
                        "   codigo_produto_xml, descricao_produto_xml, produto_catalogo_id, tipo) "
                        "VALUES (%s, NULL, NULL, %s, %s, %s, %s, %s)",
                        (m, emit, cod, desc, prod, tipo))
                    n_regras += 1
                    afetados.add(m)
                    aplicaveis.append((emit, cod, prod))
                elif atual == prod:
                    aplicaveis.append((emit, cod, prod))   # regra igual: só preenche NULLs
                else:
                    n_divergentes += 1                     # DIVERGENTE: não toca (Clonar decide)
            for emit, cod, prod in aplicaveis:
                cur.execute(
                    "UPDATE nfe_itens i JOIN nfe_importacoes n ON n.id = i.nfe_id "
                    "   SET i.produto_catalogo_id = %s "
                    " WHERE i.produto_catalogo_id IS NULL "
                    "   AND n.cliente_id = %s AND n.tipo = %s "
                    "   AND n.emit_cnpj = %s AND i.codigo_produto = %s",
                    (prod, m, tipo, emit, cod))
                if cur.rowcount:
                    n_itens += cur.rowcount
                    afetados.add(m)

    logging.getLogger(__name__).info(
        "[pool-vivo] conjunto %s: %d de %d membro(s) receberam o vínculo "
        "(regras +%d, itens +%d, divergentes ignorados %d) a partir da empresa %s",
        sid, len(afetados), len(membros), n_regras, n_itens, n_divergentes, origem_cliente_id)
    return {'set_id': sid, 'membros': len(membros), 'membros_afetados': len(afetados),
            'regras_criadas': n_regras, 'itens_preenchidos': n_itens,
            'divergentes': n_divergentes}


# Detecção de schema para a gestão do conjunto (migration D3.1 E3 roda FORA do
# boot). Cacheia só o POSITIVO: enquanto ausente, revalida a cada chamada, então
# o app "vê" a coluna/tabela na 1ª requisição depois da migration, sem restart.
_MEMO_SCHEMA_OK = set()


def _memo_col_existe(tabela, coluna):
    chave = ('col', tabela, coluna)
    if chave in _MEMO_SCHEMA_OK:
        return True
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (tabela, coluna), fetch=True, fetch_one=True) or {}
    if int(r.get('cnt', 0)) > 0:
        _MEMO_SCHEMA_OK.add(chave)
        return True
    return False


def _memo_tabela_existe(tabela):
    chave = ('tbl', tabela)
    if chave in _MEMO_SCHEMA_OK:
        return True
    r = execute_query(
        "SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (tabela,), fetch=True, fetch_one=True) or {}
    if int(r.get('cnt', 0)) > 0:
        _MEMO_SCHEMA_OK.add(chave)
        return True
    return False


# D3.1 E3 — ESCOPO POR DEPARTAMENTO. A tela de Memorizações é do Fiscal: toda
# consulta de conjunto filtra departamento='FISCAL', para que um conjunto de
# outro departamento (Contábil/DP, no futuro) nunca apareça aqui. Schema-safe:
# antes da migration a coluna não existe e o filtro vira no-op (só há Fiscal).
_MEMO_DEPTO = 'FISCAL'


def _memo_depto_ok():
    return _memo_col_existe('memo_clone_set', 'departamento')


def _depto_and(alias='s'):
    """(' AND <alias>.departamento = %s', [DEPTO]) se a coluna existe; ('', []) senão."""
    if _memo_depto_ok():
        return f" AND {alias}.departamento = %s", [_MEMO_DEPTO]
    return "", []


def _fiscal_set_de(cliente_id):
    """set_id do conjunto FISCAL de que a empresa participa, ou None. Isola o
    departamento: membership de conjunto de outro depto não é enxergada aqui."""
    if _memo_depto_ok():
        r = execute_query(
            "SELECT m.set_id FROM memo_clone_membro m "
            "JOIN memo_clone_set s ON s.id = m.set_id "
            "WHERE m.cliente_id = %s AND s.departamento = %s LIMIT 1",
            (cliente_id, _MEMO_DEPTO), fetch=True, fetch_one=True)
    else:
        r = execute_query(
            "SELECT set_id FROM memo_clone_membro WHERE cliente_id = %s LIMIT 1",
            (cliente_id,), fetch=True, fetch_one=True)
    return r['set_id'] if r else None


def _memo_set_nomes():
    """{set_id: nome} dos conjuntos FISCAIS — vazio se a coluna `nome` não existe."""
    if not _memo_col_existe('memo_clone_set', 'nome'):
        return {}
    cond, p = _depto_and('memo_clone_set')
    nomes = {}
    for r in (execute_query("SELECT id, nome FROM memo_clone_set WHERE 1=1" + cond,
                            tuple(p), fetch=True) or []):
        n = (r.get('nome') or '').strip()
        if n:
            nomes[r['id']] = n
    return nomes


def _upsert_vinculo(cliente_id, emit_cnpj, codigo_produto_xml,
                    descricao_produto_xml, produto_catalogo_id, tipo='entrada'):
    """Insert-or-update de uma memorização no escopo EMPRESA.

    grupo_id e ramo_atividade_id são gravados SEMPRE como NULL: memorização de
    grupo é decisão explícita do usuário (Fase 3) e memorização por ramo foi
    descontinuada. Continua sendo SELECT+INSERT/UPDATE em vez de ON DUPLICATE KEY
    porque o UNIQUE uk_vinculo não protege linhas com NULL (MySQL permite N).
    """
    cliente_id = _exigir_empresa(cliente_id)

    existing = execute_query(
        """SELECT id FROM nfe_produto_vinculo
            WHERE cliente_id         = %s
              AND grupo_id          IS NULL
              AND ramo_atividade_id IS NULL
              AND emit_cnpj          = %s
              AND codigo_produto_xml = %s
              AND tipo               = %s
            LIMIT 1""",
        (cliente_id, emit_cnpj, codigo_produto_xml, tipo),
        fetch=True, fetch_one=True,
    )
    if existing:
        execute_query(
            """UPDATE nfe_produto_vinculo
                  SET produto_catalogo_id    = %s,
                      descricao_produto_xml  = %s
                WHERE id = %s""",
            (produto_catalogo_id, descricao_produto_xml, existing['id']),
        )
    else:
        execute_query(
            """INSERT INTO nfe_produto_vinculo
                   (cliente_id, grupo_id, ramo_atividade_id,
                    emit_cnpj, codigo_produto_xml,
                    descricao_produto_xml, produto_catalogo_id, tipo)
               VALUES (%s, NULL, NULL, %s, %s, %s, %s, %s)""",
            (cliente_id, emit_cnpj, codigo_produto_xml,
             descricao_produto_xml, produto_catalogo_id, tipo),
        )

    # Pool vivo: propaga para os outros membros do conjunto (se houver).
    _conjunto_fanout(cliente_id,
                     [(emit_cnpj, codigo_produto_xml, descricao_produto_xml, produto_catalogo_id)],
                     tipo)


def _upsert_vinculo_batch(cliente_id, emit_cnpj, codigo_desc_map: dict,
                          produto_catalogo_id, tipo='entrada'):
    """Batch version of _upsert_vinculo for multiple product codes at once.

    codigo_desc_map: {codigo_produto_xml: descricao_produto_xml}

    Performs 3 queries (SELECT + UPDATE + INSERT) instead of 2×N queries,
    reducing 40-second "Aplicar a Todos" to sub-second for any NF-e size.
    Mesmo escopo de _upsert_vinculo: sempre empresa, nunca grupo/ramo/global.
    """
    cliente_id = _exigir_empresa(cliente_id)
    if not codigo_desc_map or not emit_cnpj:
        return

    codigos = list(codigo_desc_map.keys())
    ph = ','.join(['%s'] * len(codigos))

    existing_rows = execute_query(
        f"""SELECT id, codigo_produto_xml FROM nfe_produto_vinculo
             WHERE cliente_id         = %s
               AND grupo_id          IS NULL
               AND ramo_atividade_id IS NULL
               AND emit_cnpj          = %s
               AND tipo               = %s
               AND codigo_produto_xml IN ({ph})""",
        (cliente_id, emit_cnpj, tipo, *codigos),
        fetch=True,
    ) or []

    existing_map = {r['codigo_produto_xml']: r['id'] for r in existing_rows}

    # Batch UPDATE existing rows (all get the same produto_catalogo_id)
    if existing_map:
        id_list = list(existing_map.values())
        id_ph = ','.join(['%s'] * len(id_list))
        execute_query(
            f"UPDATE nfe_produto_vinculo SET produto_catalogo_id = %s WHERE id IN ({id_ph})",
            tuple([produto_catalogo_id] + id_list),
        )

    # Batch INSERT new rows
    new_codes = [cod for cod in codigos if cod not in existing_map]
    if new_codes:
        values_ph = ','.join(['(%s,NULL,NULL,%s,%s,%s,%s,%s)'] * len(new_codes))
        params: list = []
        for cod in new_codes:
            params.extend([
                cliente_id, emit_cnpj, cod,
                codigo_desc_map[cod], produto_catalogo_id, tipo,
            ])
        execute_query(
            f"""INSERT INTO nfe_produto_vinculo
                   (cliente_id, grupo_id, ramo_atividade_id,
                    emit_cnpj, codigo_produto_xml,
                    descricao_produto_xml, produto_catalogo_id, tipo)
               VALUES {values_ph}""",
            tuple(params),
        )

    # Pool vivo: propaga todos os pares para os outros membros do conjunto.
    _conjunto_fanout(
        cliente_id,
        [(emit_cnpj, cod, codigo_desc_map.get(cod, ''), produto_catalogo_id) for cod in codigos],
        tipo)


def _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=None):
    """Retorna fragmento WHERE + params list para filtro empresa/grupo.

    Para notas importadas sem empresa definida (cliente_id IS NULL),
    faz fallback por dest_cnpj comparado ao cpf_cnpj do cliente selecionado.
    """
    if params is None:
        params = []
    clauses = []
    if f_cliente_id:
        cid = int(f_cliente_id)
        clauses.append(
            f"({alias}.cliente_id = %s"
            f" OR ({alias}.cliente_id IS NULL"
            f"     AND REPLACE(REPLACE(REPLACE({alias}.dest_cnpj,'.',''),'/',''),'-','')"
            f"       = (SELECT REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-','')"
            f"            FROM clientes WHERE id = %s)))"
        )
        params.append(cid)
        params.append(cid)
    if f_grupo_id:
        gid = int(f_grupo_id)
        clauses.append(
            f"({alias}.grupo_id = %s"
            f" OR ({alias}.grupo_id IS NULL"
            f"     AND REPLACE(REPLACE(REPLACE({alias}.dest_cnpj,'.',''),'/',''),'-','')"
            f"       IN (SELECT REPLACE(REPLACE(REPLACE(c.cpf_cnpj,'.',''),'/',''),'-','')"
            f"             FROM clientes c"
            f"             JOIN cliente_grupo_relacao cgr ON cgr.cliente_id = c.id"
            f"             WHERE cgr.grupo_id = %s)))"
        )
        params.append(gid)
        params.append(gid)
    return clauses, params


def _empresa_where_cte(f_cliente_id, f_grupo_id, alias='t', params=None):
    """Filtro empresa/grupo para CT-e (a empresa é o TOMADOR do frete).

    Mesma forma do ``_empresa_where`` das entradas, trocando ``dest_cnpj`` por
    ``tomador_cnpj``: o fallback cobre CT-e gravados sem ``cliente_id`` (importação
    futura pelo Dropbox). Na captura SEFAZ o ``cliente_id`` sempre vem preenchido —
    é o dono do certificado —, então o fallback nem entra em jogo.
    """
    if params is None:
        params = []
    clauses = []
    if f_cliente_id:
        cid = int(f_cliente_id)
        clauses.append(
            f"({alias}.cliente_id = %s"
            f" OR ({alias}.cliente_id IS NULL"
            f"     AND REPLACE(REPLACE(REPLACE({alias}.tomador_cnpj,'.',''),'/',''),'-','')"
            f"       = (SELECT REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-','')"
            f"            FROM clientes WHERE id = %s)))"
        )
        params.append(cid)
        params.append(cid)
    if f_grupo_id:
        gid = int(f_grupo_id)
        clauses.append(
            f"({alias}.grupo_id = %s"
            f" OR ({alias}.grupo_id IS NULL"
            f"     AND REPLACE(REPLACE(REPLACE({alias}.tomador_cnpj,'.',''),'/',''),'-','')"
            f"       IN (SELECT REPLACE(REPLACE(REPLACE(c.cpf_cnpj,'.',''),'/',''),'-','')"
            f"             FROM clientes c"
            f"             JOIN cliente_grupo_relacao cgr ON cgr.cliente_id = c.id"
            f"             WHERE cgr.grupo_id = %s)))"
        )
        params.append(gid)
        params.append(gid)
    return clauses, params


def _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=None):
    """Filtro empresa/grupo para Saídas (cliente = emitente do XML)."""
    if params is None:
        params = []
    clauses = []
    if f_cliente_id:
        cid = int(f_cliente_id)
        clauses.append(
            f"({alias}.cliente_id = %s"
            f" OR ({alias}.cliente_id IS NULL"
            f"     AND REPLACE(REPLACE(REPLACE({alias}.emit_cnpj,'.',''),'/',''),'-','')"
            f"       = (SELECT REPLACE(REPLACE(REPLACE(cpf_cnpj,'.',''),'/',''),'-','')"
            f"            FROM clientes WHERE id = %s)))"
        )
        params.append(cid)
        params.append(cid)
    if f_grupo_id:
        gid = int(f_grupo_id)
        clauses.append(
            f"({alias}.grupo_id = %s"
            f" OR ({alias}.grupo_id IS NULL"
            f"     AND REPLACE(REPLACE(REPLACE({alias}.emit_cnpj,'.',''),'/',''),'-','')"
            f"       IN (SELECT REPLACE(REPLACE(REPLACE(c.cpf_cnpj,'.',''),'/',''),'-','')"
            f"             FROM clientes c"
            f"             JOIN cliente_grupo_relacao cgr ON cgr.cliente_id = c.id"
            f"             WHERE cgr.grupo_id = %s)))"
        )
        params.append(gid)
        params.append(gid)
    return clauses, params


# ---------------------------------------------------------------------------
# API — opções dos filtros, restritas à empresa/grupo selecionado
#
# Antes, os <select> de emitente/destinatário/transportadora e de UF eram
# montados no render da página com um DISTINCT GLOBAL (todas as empresas):
# centenas de opções, quase todas sem relação com a empresa que o usuário
# escolhe depois. Aqui as opções saem do MESMO escopo que a listagem usa —
# reaproveitando _empresa_where / _empresa_where_saidas / _empresa_where_cte,
# com o fallback por CNPJ — para a lista nunca divergir do que a tabela mostra.
#
# Sem empresa nem grupo devolve listas vazias: a busca já exige escopo, então
# não faz sentido oferecer opção nenhuma antes disso.
#
# O recorte de DATA entra no mesmo WHERE: as opções acompanham o período que
# está na tela, então um fornecedor que não apareceu no mês some do dropdown.
# ---------------------------------------------------------------------------
def _clausulas_data(alias, f_data_ini, f_data_fim):
    """Recorte por data_emissao — a mesma coluna e as mesmas comparações que as
    APIs de listagem usam, para as opções baterem com o que a tabela mostra."""
    clauses, params = [], []
    if f_data_ini:
        clauses.append(f'{alias}.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        clauses.append(f'{alias}.data_emissao <= %s')
        params.append(f_data_fim)
    return clauses, params


def _opcoes_cnpjs_ufs(tabela, alias, where_sql, params, cnpj_col, nome_col, uf_cols):
    """Roda o DISTINCT de CNPJ+nome e o de cada coluna de UF dentro do escopo.

    Devolve (cnpjs, {coluna_uf: [uf, ...]}). O MAX(nome) desempata quando o
    mesmo CNPJ aparece com grafias diferentes entre notas."""
    cnpjs = execute_query(
        f"""SELECT {alias}.{cnpj_col} AS cnpj, MAX({alias}.{nome_col}) AS nome
              FROM {tabela} {alias}
             {where_sql} AND COALESCE({alias}.{cnpj_col}, '') <> ''
             GROUP BY {alias}.{cnpj_col}
             ORDER BY nome""",
        tuple(params), fetch=True,
    ) or []
    ufs = {}
    for col in uf_cols:
        rows = execute_query(
            f"""SELECT DISTINCT {alias}.{col} AS uf
                  FROM {tabela} {alias}
                 {where_sql} AND COALESCE({alias}.{col}, '') <> ''
                 ORDER BY uf""",
            tuple(params), fetch=True,
        ) or []
        ufs[col] = [r['uf'] for r in rows]
    return cnpjs, ufs


@escrita_fiscal.route('/conf-compras/api/opcoes-filtros')
@login_required
def api_opcoes_filtros():
    """Emitentes e UFs de emitente nas ENTRADAS da empresa, dentro do período."""
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    if not f_cliente_id and not f_grupo_id:
        return jsonify({'cnpjs': [], 'ufs': []})
    extra, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    d_clauses, d_params = _clausulas_data(
        'n', request.args.get('data_ini', '').strip(),
        request.args.get('data_fim', '').strip())
    # A ordem das cláusulas define a ordem dos %s — os params da data vêm depois.
    where_sql = 'WHERE ' + ' AND '.join(["n.tipo = 'entrada'"] + extra + d_clauses)
    params = params + d_params
    cnpjs, ufs = _opcoes_cnpjs_ufs('nfe_importacoes', 'n', where_sql, params,
                                   'emit_cnpj', 'emit_nome', ['emit_uf'])
    return jsonify({'cnpjs': cnpjs, 'ufs': ufs['emit_uf']})


@escrita_fiscal.route('/conf-saidas/api/opcoes-filtros')
@login_required
def api_opcoes_filtros_saidas():
    """Destinatários e UFs nas SAÍDAS da empresa, dentro do período."""
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    if not f_cliente_id and not f_grupo_id:
        return jsonify({'cnpjs': [], 'ufs': []})
    extra, params = _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=[])
    d_clauses, d_params = _clausulas_data(
        'n', request.args.get('data_ini', '').strip(),
        request.args.get('data_fim', '').strip())
    where_sql = 'WHERE ' + ' AND '.join(["n.tipo = 'saida'"] + extra + d_clauses)
    params = params + d_params
    cnpjs, ufs = _opcoes_cnpjs_ufs('nfe_importacoes', 'n', where_sql, params,
                                   'dest_cnpj', 'dest_nome', ['dest_uf'])
    return jsonify({'cnpjs': cnpjs, 'ufs': ufs['dest_uf']})


@escrita_fiscal.route('/conf-cte/api/opcoes-filtros')
@login_required
def api_opcoes_filtros_cte():
    """Transportadoras e UFs de início/fim nos CT-e da empresa, no período."""
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    if not f_cliente_id and not f_grupo_id:
        return jsonify({'cnpjs': [], 'ufs_ini': [], 'ufs_fim': []})
    extra, params = _empresa_where_cte(f_cliente_id, f_grupo_id, alias='t', params=[])
    # cte_documentos também tem data_emissao — é a coluna que api_ctes filtra.
    d_clauses, d_params = _clausulas_data(
        't', request.args.get('data_ini', '').strip(),
        request.args.get('data_fim', '').strip())
    # Diferente das notas, aqui não há cláusula de tipo; sem escopo o WHERE
    # ficaria vazio — mas o early-return acima garante que extra nunca é vazio.
    where_sql = 'WHERE ' + ' AND '.join(extra + d_clauses)
    params = params + d_params
    cnpjs, ufs = _opcoes_cnpjs_ufs('cte_documentos', 't', where_sql, params,
                                   'emit_cnpj', 'emit_nome', ['uf_ini', 'uf_fim'])
    return jsonify({'cnpjs': cnpjs, 'ufs_ini': ufs['uf_ini'], 'ufs_fim': ufs['uf_fim']})


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/')
@permission_required('escrita_fiscal.index')
def index():
    from utils.scheduler import get_scheduled_time
    schedule = get_scheduled_time() if current_user.is_admin() else {}
    return render_template(
        'escrita_fiscal/index.html',
        is_admin=current_user.is_admin(),
        schedule_texto=schedule.get('texto', ''),
        departamento='Fiscal',
    )


# ---------------------------------------------------------------------------
# Home do Fiscal — DESTAQUES (carrossel + contadores). Painel do ESCRITÓRIO:
# agrega GLOBAL, gated pela mesma permissão das telas de consulta. SÓ SELECTs.
# A home abre instantânea (não espera query) e busca isto por fetch; cache em
# memória de 60s por usuário evita que F5 em sequência martele o banco. Cada
# consulta é isolada: se a query de um card falhar, o card é OMITIDO (os outros
# vivem) e o erro vai pro log, nunca pro usuário — melhor faltar que mentir.
# ---------------------------------------------------------------------------
_HOME_CACHE: dict = {}
_HOME_CACHE_LOCK = threading.Lock()
_HOME_TTL_S = 60

# 1º dia do mês corrente e a mesma janela (mês-a-dia) do mês anterior — em SQL,
# sem DATE_FORMAT (evita o '%' no paramstyle do driver).
_MES_INI = "(CURDATE() - INTERVAL (DAY(CURDATE())-1) DAY)"
_PMES_INI = f"({_MES_INI} - INTERVAL 1 MONTH)"
_PMES_FIM = "(CURDATE() - INTERVAL 1 MONTH)"


def _hi(v):
    return int(v) if v is not None else 0


def _hbrl(v):
    s = f'{float(v or 0):,.2f}'
    return 'R$ ' + s.replace(',', 'X').replace('.', ',').replace('X', '.')


def _hdias_iso(n):
    """n datas ISO terminando hoje (Brasília), em ordem crescente."""
    from datetime import timedelta
    hoje = datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    return [(hoje - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def _hspark(rows, keys, kf, vf):
    """Densifica linhas agrupadas num array alinhado a `keys` (0 onde faltar)."""
    m = {}
    for r in rows or []:
        k = r.get(kf)
        if hasattr(k, 'isoformat'):
            k = k.isoformat()
        m[str(k)] = _hi(r.get(vf))
    return [m.get(str(k), 0) for k in keys]


def _htrend(atual, anterior):
    """Selo de tendência vs mesmo período do mês anterior. Sem base → neutro."""
    if not anterior or anterior <= 0:
        return {'tipo': 'neutro'}
    pct = round((atual - anterior) / anterior * 100)
    return {'tipo': 'alta' if pct > 0 else ('baixa' if pct < 0 else 'igual'), 'pct': pct}


def _hq1(sql):
    """SELECT de 1 linha. Exceção → None (card omitido); tabela vazia → dict."""
    try:
        return execute_query(sql, fetch=True, fetch_one=True) or {}
    except Exception:
        logger.exception('[home-destaques] query1 falhou')
        return None


def _hqn(sql):
    try:
        return execute_query(sql, fetch=True) or []
    except Exception:
        logger.exception('[home-destaques] queryN falhou')
        return []


def _home_destaques_payload():
    cards = []
    counters = {}

    def _card(fn):
        try:
            c = fn()
            if c:
                cards.append(c)
        except Exception:
            logger.exception('[home-destaques] card omitido')

    hoje_dom = datetime.now(ZoneInfo('America/Sao_Paulo')).day
    keys_dom = list(range(1, hoje_dom + 1))
    keys9 = _hdias_iso(9)
    keys7 = _hdias_iso(7)

    # ---- scalares (cada um isolado) ----
    nfe = _hq1(
        "SELECT "
        " SUM(DATE(importado_em)=CURDATE()) imp_hoje, "
        f" SUM(tipo='entrada' AND COALESCE(cancelada,0)=0 AND data_emissao>={_MES_INI}) ent_mes, "
        f" SUM(tipo='saida'   AND COALESCE(cancelada,0)=0 AND data_emissao>={_MES_INI}) sai_mes, "
        f" COALESCE(SUM(CASE WHEN tipo='entrada' AND COALESCE(cancelada,0)=0 AND data_emissao>={_MES_INI} THEN valor_total END),0) ent_valor, "
        f" SUM(tipo='saida' AND COALESCE(cancelada,0)=0 AND data_emissao>={_MES_INI} AND SUBSTRING(chave_acesso,21,2)='65') sai_nfce, "
        f" SUM(tipo='saida' AND COALESCE(cancelada,0)=0 AND data_emissao>={_MES_INI} AND SUBSTRING(chave_acesso,21,2)='55') sai_nfe, "
        f" SUM(tipo='entrada' AND COALESCE(cancelada,0)=0 AND data_emissao>={_PMES_INI} AND data_emissao<={_PMES_FIM}) ent_prev, "
        f" SUM(tipo='saida'   AND COALESCE(cancelada,0)=0 AND data_emissao>={_PMES_INI} AND data_emissao<={_PMES_FIM}) sai_prev "
        "FROM nfe_importacoes")
    cte = _hq1(
        "SELECT SUM(DATE(importado_em)=CURDATE()) imp_hoje, "
        f" SUM(COALESCE(cancelado,0)=0 AND data_emissao>={_MES_INI}) cte_mes "
        "FROM cte_documentos")
    # "Em dia" = cursor alcançado (backlog 0) e já consultou. A "cota" (throttle
    # transitório entre janelas da SEFAZ) NÃO conta como atraso — senão o painel
    # mostraria 0/26 num sistema saudável, só no cooldown. COALESCE espelha o
    # backlog da tela de Status (max_nsu/ult_nsu podem ser NULL).
    nsu = _hq1(
        "SELECT COUNT(*) total, "
        " SUM(GREATEST(COALESCE(max_nsu,0)-COALESCE(ult_nsu,0),0)=0 "
        "     AND ult_consulta IS NOT NULL) em_dia "
        "FROM dfe_nsu")
    cert = _hq1(
        "SELECT COUNT(*) venc FROM dfe_certificados "
        "WHERE ativo=1 AND validade IS NOT NULL "
        "AND validade BETWEEN CURDATE() AND CURDATE()+INTERVAL 30 DAY")
    prod = _hq1("SELECT COUNT(*) total FROM nfe_produtos_catalogo WHERE ativo=1")
    semv = _hq1("SELECT COUNT(DISTINCT descricao, unidade) sem_vinc "
                "FROM nfe_itens WHERE produto_catalogo_id IS NULL")
    memo = _hq1(
        "SELECT COUNT(*) ativas, "
        " SUM(YEAR(criado_em)=YEAR(CURDATE()) AND MONTH(criado_em)=MONTH(CURDATE())) aplicadas_mes, "
        " SUM(YEAR(criado_em)=YEAR(CURDATE()-INTERVAL 1 MONTH) "
        "     AND MONTH(criado_em)=MONTH(CURDATE()-INTERVAL 1 MONTH)) aplicadas_prev "
        "FROM nfe_produto_vinculo")
    cli9 = _hq1("SELECT COUNT(DISTINCT cliente_id) cli FROM nfe_importacoes "
                "WHERE importado_em>=CURDATE()-INTERVAL 8 DAY")

    # ---- séries (sparklines) ----
    dom = _hqn("SELECT DAY(data_emissao) d, SUM(tipo='entrada') ent, SUM(tipo='saida') sai "
               f"FROM nfe_importacoes WHERE COALESCE(cancelada,0)=0 AND data_emissao>={_MES_INI} GROUP BY d")
    nfe9 = _hqn("SELECT DATE(importado_em) d, COUNT(*) c FROM nfe_importacoes "
                "WHERE importado_em>=CURDATE()-INTERVAL 8 DAY GROUP BY d")
    cte9 = _hqn("SELECT DATE(importado_em) d, COUNT(*) c FROM cte_documentos "
                "WHERE importado_em>=CURDATE()-INTERVAL 8 DAY GROUP BY d")
    cons7 = _hqn("SELECT DATE(momento) d, COUNT(*) c FROM dfe_consulta_log "
                 "WHERE momento>=CURDATE()-INTERVAL 6 DAY GROUP BY d")
    vinc9 = _hqn("SELECT DATE(criado_em) d, COUNT(*) c FROM nfe_produto_vinculo "
                 "WHERE criado_em>=CURDATE()-INTERVAL 8 DAY GROUP BY d")

    # ---- 1) CAPTURADAS HOJE ----
    def _c1():
        if nfe is None or cte is None:
            return None
        s = [a + b for a, b in zip(_hspark(nfe9, keys9, 'd', 'c'),
                                   _hspark(cte9, keys9, 'd', 'c'))]
        return {'id': 'capturadas', 'icone': 'fa-bolt', 'titulo': 'Capturadas hoje',
                'valor': _hi(nfe.get('imp_hoje')) + _hi(cte.get('imp_hoje')),
                'apoio': f"últimos 9 dias · {_hi((cli9 or {}).get('cli'))} clientes ativos",
                'spark': s, 'spark_tipo': 'linha', 'trend': {'tipo': 'neutro', 'rotulo': 'hoje'}}
    _card(_c1)

    # ---- 2) SAÍDAS DO MÊS ----
    def _c2():
        if nfe is None:
            return None
        return {'id': 'saidas', 'icone': 'fa-file-export', 'titulo': 'Saídas do mês',
                'valor': _hi(nfe.get('sai_mes')),
                'apoio': f"{_hi(nfe.get('sai_nfce'))} NFC-e · {_hi(nfe.get('sai_nfe'))} NF-e",
                'spark': _hspark(dom, keys_dom, 'd', 'sai'), 'spark_tipo': 'linha',
                'trend': _htrend(_hi(nfe.get('sai_mes')), _hi(nfe.get('sai_prev')))}
    _card(_c2)

    # ---- 3) STATUS SEFAZ ----
    def _c3():
        if nsu is None:
            return None
        v = _hi((cert or {}).get('venc'))
        apoio = (f"{v} certificado(s) vencendo em 30d" if v > 0
                 else "nenhum certificado vencendo")
        return {'id': 'status', 'icone': 'fa-satellite-dish', 'titulo': 'Status SEFAZ',
                'valor': _hi(nsu.get('em_dia')), 'valor_sufixo': f" / {_hi(nsu.get('total'))}",
                'apoio': apoio, 'spark': _hspark(cons7, keys7, 'd', 'c'),
                'spark_tipo': 'barra', 'trend': {'tipo': 'neutro', 'rotulo': 'em dia'}}
    _card(_c3)

    # ---- 4) ENTRADAS DO MÊS ----
    def _c4():
        if nfe is None:
            return None
        return {'id': 'entradas', 'icone': 'fa-file-invoice-dollar', 'titulo': 'Entradas do mês',
                'valor': _hi(nfe.get('ent_mes')),
                'apoio': _hbrl(nfe.get('ent_valor')),
                'spark': _hspark(dom, keys_dom, 'd', 'ent'), 'spark_tipo': 'linha',
                'trend': _htrend(_hi(nfe.get('ent_mes')), _hi(nfe.get('ent_prev')))}
    _card(_c4)

    # ---- 5) PRODUTOS ----
    def _c5():
        if prod is None:
            return None
        return {'id': 'produtos', 'icone': 'fa-tag', 'titulo': 'Produtos',
                'valor': _hi(prod.get('total')),
                'apoio': f"{_hi((semv or {}).get('sem_vinc'))} sem vínculo",
                'spark': _hspark(vinc9, keys9, 'd', 'c'), 'spark_tipo': 'linha',
                'trend': {'tipo': 'neutro', 'rotulo': 'catálogo'}}
    _card(_c5)

    # ---- 6) MEMORIZAÇÕES ----
    def _c6():
        if memo is None:
            return None
        return {'id': 'memorizacoes', 'icone': 'fa-memory', 'titulo': 'Memorizações',
                'valor': _hi(memo.get('ativas')),
                'apoio': f"{_hi(memo.get('aplicadas_mes'))} aplicadas no mês",
                'spark': _hspark(vinc9, keys9, 'd', 'c'), 'spark_tipo': 'linha',
                'trend': {'tipo': 'neutro', 'rotulo': 'ativas'}}
    _card(_c6)

    # Rankings agora são cards tipo LISTA (até 15 posições; sem sparkline — a
    # lista já é a visualização). Rótulo com o número do cliente antes do nome
    # ("#515 PETROGOIAS"); reticências ficam por conta do CSS (300px de card).
    # ---- 7) MAIS ENTRADAS (top 15 empresas) ----
    def _c7():
        rows = _hqn("SELECT c.numero_cliente num, c.nome_razao_social nome, COUNT(*) n "
                    "FROM nfe_importacoes ni JOIN clientes c ON c.id=ni.cliente_id "
                    "WHERE ni.tipo='entrada' GROUP BY ni.cliente_id ORDER BY n DESC LIMIT 15")
        if not rows:
            return None
        return {'id': 'top_entradas', 'icone': 'fa-arrow-down-wide-short',
                'titulo': 'Mais entradas', 'tipo': 'lista',
                'itens': [{'valor': _hi(r['n']),
                           'rotulo': '#%s %s' % (r['num'], (r['nome'] or '').strip())} for r in rows],
                'trend': {'tipo': 'neutro', 'rotulo': 'total'}}
    _card(_c7)

    # ---- 8) MAIS SAÍDAS (top 15 empresas) ----
    def _c8():
        rows = _hqn("SELECT c.numero_cliente num, c.nome_razao_social nome, COUNT(*) n "
                    "FROM nfe_importacoes ni JOIN clientes c ON c.id=ni.cliente_id "
                    "WHERE ni.tipo='saida' GROUP BY ni.cliente_id ORDER BY n DESC LIMIT 15")
        if not rows:
            return None
        return {'id': 'top_saidas', 'icone': 'fa-arrow-up-wide-short',
                'titulo': 'Mais saídas', 'tipo': 'lista',
                'itens': [{'valor': _hi(r['n']),
                           'rotulo': '#%s %s' % (r['num'], (r['nome'] or '').strip())} for r in rows],
                'trend': {'tipo': 'neutro', 'rotulo': 'total'}}
    _card(_c8)

    # ---- 9) FORNECEDORES FREQUENTES (top 15 emitentes de entrada) ----
    def _c9():
        rows = _hqn("SELECT MAX(emit_nome) nome, COUNT(*) n FROM nfe_importacoes "
                    "WHERE tipo='entrada' AND COALESCE(emit_cnpj,'')<>'' "
                    "GROUP BY emit_cnpj ORDER BY n DESC LIMIT 15")
        if not rows:
            return None
        return {'id': 'top_fornecedores', 'icone': 'fa-truck-ramp-box',
                'titulo': 'Fornecedores frequentes', 'tipo': 'lista',
                'itens': [{'valor': _hi(r['n']), 'rotulo': (r['nome'] or '').strip().title()}
                          for r in rows],
                'trend': {'tipo': 'neutro', 'rotulo': 'total'}}
    _card(_c9)

    # ---- ATIVIDADE (logs_sistema / roteador_log) — só aparecem com dado real ----
    _card(lambda: card_participacoes('fiscal'))
    _card(lambda: card_trabalhando_agora('fiscal'))
    _card(card_chegando_cliente)

    # ---- 10) MEMORIZAÇÕES NO MÊS (vs mês anterior — variação real, mesmo negativa) ----
    def _c10():
        if memo is None:
            return None
        at = _hi(memo.get('aplicadas_mes'))
        ant = _hi(memo.get('aplicadas_prev'))
        return {'id': 'memo_mes', 'icone': 'fa-memory', 'titulo': 'Memorizações no mês',
                'valor': at, 'apoio': '%d no mês passado' % ant,
                'spark': _hspark(vinc9, keys9, 'd', 'c'), 'spark_tipo': 'linha',
                'trend': _htrend(at, ant)}
    _card(_c10)

    # ---- 11) PRESAS EM RESUMO (atenção — mesma régua da Frente B: incompleta=1 +30d) ----
    def _c11():
        r = _hq1("SELECT COUNT(*) n, COUNT(DISTINCT cliente_id) emp FROM nfe_importacoes "
                 "WHERE origem='SEFAZ' AND tipo='entrada' AND incompleta=1 "
                 "AND DATEDIFF(CURDATE(), data_emissao) > 30")
        if r is None:
            return None
        n = _hi(r.get('n'))
        emp = _hi(r.get('emp'))
        return {'id': 'presas_resumo', 'icone': 'fa-triangle-exclamation',
                'titulo': 'Presas em resumo', 'valor': n,
                'apoio': ('resumo SEFAZ há +30 dias · %d empresa%s' % (emp, '' if emp == 1 else 's')
                          if n else 'nenhuma presa há +30 dias'),
                'trend': {'tipo': 'neutro', 'rotulo': 'atenção'}}
    _card(_c11)

    # ---- contadores DIA A DIA (mesmo payload; ausente onde a query falhou) ----
    if nfe is not None:
        counters['entradas'] = _hi(nfe.get('ent_mes'))
        counters['saidas'] = _hi(nfe.get('sai_mes'))
    if cte is not None:
        counters['cte'] = _hi(cte.get('cte_mes'))
    if nsu is not None:
        counters['sefaz'] = f"{_hi(nsu.get('em_dia'))}/{_hi(nsu.get('total'))}"
    if prod is not None:
        counters['produtos'] = _hi(prod.get('total'))
    if memo is not None:
        counters['memorizacoes'] = _hi(memo.get('ativas'))

    agora = datetime.now(ZoneInfo('America/Sao_Paulo'))
    return {
        'gerado_em': agora.isoformat(timespec='seconds'),
        'gerado_em_ms': int(agora.timestamp() * 1000),
        'cards': cards,
        'counters': counters,
    }


@escrita_fiscal.route('/api/home-destaques')
@permission_required('escrita_fiscal.conf_compras')
def api_home_destaques():
    """Números da home (carrossel + contadores). Cache 60s por usuário."""
    uid = getattr(current_user, 'id', None)
    agora = datetime.now(timezone.utc).timestamp()
    with _HOME_CACHE_LOCK:
        hit = _HOME_CACHE.get(uid)
        if hit and (agora - hit[0]) < _HOME_TTL_S:
            return jsonify(hit[1])
    payload = _home_destaques_payload()
    with _HOME_CACHE_LOCK:
        _HOME_CACHE[uid] = (agora, payload)
    return jsonify(payload)


# ---------------------------------------------------------------------------
# Painel de Status da Captura SEFAZ — SÓ LEITURA (dfe_nsu / dfe_consulta_log /
# nfe_importacoes). NÃO captura, NÃO consome cota — só mostra o que o cron gravou.
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/status-sefaz/')
@permission_required('escrita_fiscal.conf_compras')
def status_sefaz():
    # Filtros (Bloco D5). Nenhuma consulta NOVA: empresa, certificado e serviço
    # peneiram em Python listas que a tela já carregava; c_stat e período só
    # ajustam o WHERE/LIMIT da consulta de histórico que já existia (a coluna
    # `momento` é indexada e a tabela tem ~13 mil linhas).
    fs = {
        'empresa':  request.args.get('empresa', '').strip(),
        'servico':  request.args.get('servico', '').strip(),    # nfe|cte
        'cert':     request.args.get('cert', '').strip(),       # vencido|d30|d60|ok
        'cstat':    request.args.get('cstat', '').strip(),      # 137|138|656|erro
        'periodo':  request.args.get('periodo', '').strip(),    # 1|7|30 (dias)
    }
    filtros_ativos = any(fs.values())

    # AUDITORIA (D2): leitura — abrir o Status SEFAZ de UMA empresa (com a empresa
    # escolhida). Sem empresa é o painel geral/auto-refresh — não registra.
    if fs['empresa']:
        registrar('leitura.abriu_status_sefaz', 'fiscal',
                  depois={k: v for k, v in fs.items() if v})

    def _fmt(d):
        return d.strftime('%d/%m/%Y %H:%M') if hasattr(d, 'strftime') else None

    def _fmt_data(d):
        return d.strftime('%d/%m/%Y') if hasattr(d, 'strftime') else None

    def _i(v):
        return int(v) if v is not None else 0

    # ---- TOPO: totais de notas SEFAZ + janelas por importado_em ----
    t = execute_query(
        "SELECT COUNT(*) AS total, "
        "  SUM(DATE(importado_em)=CURDATE()) AS hoje, "
        "  SUM(YEARWEEK(importado_em,1)=YEARWEEK(CURDATE(),1)) AS semana, "
        "  SUM(YEAR(importado_em)=YEAR(CURDATE()) AND MONTH(importado_em)=MONTH(CURDATE())) AS mes, "
        "  SUM(COALESCE(incompleta,0)=0) AS completas, "
        "  SUM(COALESCE(incompleta,0)=1) AS resumos "
        "FROM nfe_importacoes WHERE origem='SEFAZ' AND tipo='entrada'",
        fetch=True, fetch_one=True,
    ) or {}

    b = execute_query(
        "SELECT COALESCE(SUM(GREATEST(max_nsu - ult_nsu, 0)),0) AS backlog, "
        "  MAX(ult_consulta) AS ult_consulta FROM dfe_nsu",
        fetch=True, fetch_one=True,
    ) or {}

    topo = {
        'total': _i(t.get('total')), 'hoje': _i(t.get('hoje')),
        'semana': _i(t.get('semana')), 'mes': _i(t.get('mes')),
        'completas': _i(t.get('completas')), 'resumos': _i(t.get('resumos')),
        'backlog': _i(b.get('backlog')), 'ult_consulta': _fmt(b.get('ult_consulta')),
    }

    # ---- TOPO SAÍDA: mesma leitura do topo de entrada, com tipo='saida'.
    # A saída NÃO tem resumo: o resumo (resNFe) sempre entra como 'entrada' na
    # ótica do dono do certificado (ver dfe_captura.SQL_NOTA_RESUMO_UPSERT, que
    # fixa tipo='entrada'). Por isso aqui o recorte útil é o VALOR, não
    # completas/resumos — a conferência é de faturamento, não de completude.
    s = execute_query(
        "SELECT COUNT(*) AS total, "
        "  SUM(DATE(importado_em)=CURDATE()) AS hoje, "
        "  SUM(YEARWEEK(importado_em,1)=YEARWEEK(CURDATE(),1)) AS semana, "
        "  SUM(YEAR(importado_em)=YEAR(CURDATE()) AND MONTH(importado_em)=MONTH(CURDATE())) AS mes, "
        "  SUM(COALESCE(incompleta,0)=0) AS completas, "
        "  SUM(COALESCE(incompleta,0)=1) AS resumos, "
        "  COALESCE(SUM(valor_total),0) AS valor, "
        "  COUNT(DISTINCT cliente_id) AS empresas, "
        "  MAX(importado_em) AS ult_captura "
        "FROM nfe_importacoes WHERE origem='SEFAZ' AND tipo='saida'",
        fetch=True, fetch_one=True,
    ) or {}

    topo_saida = {
        'total': _i(s.get('total')), 'hoje': _i(s.get('hoje')),
        'semana': _i(s.get('semana')), 'mes': _i(s.get('mes')),
        'completas': _i(s.get('completas')), 'resumos': _i(s.get('resumos')),
        'empresas': _i(s.get('empresas')),
        'valor': float(s.get('valor') or 0),
        'ult_captura': _fmt(s.get('ult_captura')),
    }

    # ---- TOPO CT-e: mesma leitura, na tabela própria (cte_documentos/dfe_nsu_cte).
    # CT-e não tem resumo (a distribuição entrega o documento completo), então aqui
    # não há a divisão completas/resumos — o recorte útil é o valor do frete.
    tc = execute_query(
        "SELECT COUNT(*) AS total, "
        "  SUM(DATE(importado_em)=CURDATE()) AS hoje, "
        "  SUM(YEARWEEK(importado_em,1)=YEARWEEK(CURDATE(),1)) AS semana, "
        "  SUM(YEAR(importado_em)=YEAR(CURDATE()) AND MONTH(importado_em)=MONTH(CURDATE())) AS mes, "
        "  COALESCE(SUM(valor_frete),0) AS valor_frete, "
        "  SUM(papel_cliente='tomador') AS tomados, "
        "  SUM(cancelado=1) AS cancelados "
        "FROM cte_documentos WHERE origem='SEFAZ'",
        fetch=True, fetch_one=True,
    ) or {}

    bc = execute_query(
        "SELECT COALESCE(SUM(GREATEST(max_nsu - ult_nsu, 0)),0) AS backlog, "
        "  MAX(ult_consulta) AS ult_consulta FROM dfe_nsu_cte",
        fetch=True, fetch_one=True,
    ) or {}

    topo_cte = {
        'total': _i(tc.get('total')), 'hoje': _i(tc.get('hoje')),
        'semana': _i(tc.get('semana')), 'mes': _i(tc.get('mes')),
        'tomados': _i(tc.get('tomados')), 'cancelados': _i(tc.get('cancelados')),
        'valor_frete': float(tc.get('valor_frete') or 0),
        'backlog': _i(bc.get('backlog')), 'ult_consulta': _fmt(bc.get('ult_consulta')),
    }

    # ---- POR EMPRESA: TODA empresa que CAPTURA, não só quem tem certificado.
    # A espinha era `FROM dfe_certificados`, o que escondia quem captura por
    # contador (Parte 2) e quem só recebe "de carona" (saída que caiu no cursor
    # de outro — ex.: a KET). Agora a base é a UNIÃO de três origens:
    #   (a) certificado próprio ativo
    #   (b) contador vinculado
    #   (c) já tem documento capturado da SEFAZ
    # UNION (não UNION ALL) para o distinct por cliente. Os agregados vivem em
    # derived tables com GROUP BY cliente_id — o SELECT externo não agrupa, então
    # o only_full_group_by do servidor não é problema aqui.
    rows = execute_query(
        "SELECT c.id, c.numero_cliente, c.nome_razao_social, "
        "  dc.validade AS cert_validade, dc.tipo_doc AS cert_tipo_doc, dc.cnpj AS cert_doc, "
        "  n.ult_consulta, n.proximo_permitido, n.ult_status AS nsu_status, "
        "  GREATEST(COALESCE(n.max_nsu,0)-COALESCE(n.ult_nsu,0),0) AS backlog, "
        "  (n.proximo_permitido IS NOT NULL AND n.proximo_permitido > NOW()) AS em_cota, "
        "  TIMESTAMPDIFF(MINUTE, NOW(), n.proximo_permitido) AS libera_min, "
        "  COALESCE(ent.total,0) AS ent_total, COALESCE(ent.completas,0) AS ent_completas, "
        "  COALESCE(ent.resumos,0) AS ent_resumos, ent.ult_captura AS ent_ult, "
        "  COALESCE(sai.qtd,0) AS sai_qtd, COALESCE(sai.valor,0) AS sai_valor, "
        "  sai.de AS sai_de, sai.ate AS sai_ate, sai.ult_captura AS sai_ult, "
        "  nc.ult_consulta AS cte_ult_consulta, "
        "  GREATEST(COALESCE(nc.max_nsu,0)-COALESCE(nc.ult_nsu,0),0) AS cte_backlog, "
        "  (nc.proximo_permitido IS NOT NULL AND nc.proximo_permitido > NOW()) AS cte_em_cota, "
        "  TIMESTAMPDIFF(MINUTE, NOW(), nc.proximo_permitido) AS cte_libera_min, "
        "  COALESCE(ct.total,0) AS cte_total, COALESCE(ct.tomados,0) AS cte_tomados, "
        "  COALESCE(ct.valor_frete,0) AS cte_valor_frete "
        "FROM ( "
        "    SELECT cliente_id AS id FROM dfe_certificados WHERE ativo = 1 "
        "    UNION SELECT cliente_id FROM cliente_contadores "
        "    UNION SELECT DISTINCT cliente_id FROM nfe_importacoes "
        "           WHERE origem='SEFAZ' AND cliente_id IS NOT NULL "
        ") base "
        "JOIN clientes c ON c.id = base.id "
        "LEFT JOIN dfe_certificados dc ON dc.cliente_id = c.id AND dc.ativo = 1 "
        "LEFT JOIN dfe_nsu     n  ON n.cliente_id  = c.id "
        "LEFT JOIN dfe_nsu_cte nc ON nc.cliente_id = c.id "
        "LEFT JOIN ( "
        "    SELECT cliente_id, COUNT(*) AS total, "
        "           SUM(COALESCE(incompleta,0)=0) AS completas, "
        "           SUM(COALESCE(incompleta,0)=1) AS resumos, "
        "           MAX(importado_em) AS ult_captura "
        "    FROM nfe_importacoes WHERE origem='SEFAZ' AND tipo='entrada' GROUP BY cliente_id "
        ") ent ON ent.cliente_id = c.id "
        "LEFT JOIN ( "
        "    SELECT cliente_id, COUNT(*) AS qtd, COALESCE(SUM(valor_total),0) AS valor, "
        "           MIN(data_emissao) AS de, MAX(data_emissao) AS ate, "
        "           MAX(importado_em) AS ult_captura "
        "    FROM nfe_importacoes WHERE origem='SEFAZ' AND tipo='saida' GROUP BY cliente_id "
        ") sai ON sai.cliente_id = c.id "
        "LEFT JOIN ( "
        "    SELECT cliente_id, COUNT(*) AS total, "
        "           SUM(papel_cliente='tomador') AS tomados, "
        "           SUM(valor_frete) AS valor_frete "
        "    FROM cte_documentos WHERE origem='SEFAZ' GROUP BY cliente_id "
        ") ct ON ct.cliente_id = c.id "
        "ORDER BY em_cota DESC, backlog DESC, c.numero_cliente",
        fetch=True,
    ) or []

    # Frente B — classificação em grupos. UMA consulta agregada (sem laço):
    #  travados  = resumo (incompleta=1) com emissão há MAIS de 30 dias (defeito);
    #  recentes  = resumo com <= 30 dias (normal, promove sozinho);
    #  cap_30d   = capturou algo da SEFAZ nos últimos 30 dias.
    _agg = execute_query(
        "SELECT cliente_id, "
        "  SUM(tipo='entrada' AND COALESCE(incompleta,0)=1 AND DATEDIFF(CURDATE(), data_emissao) > 30) AS travados, "
        "  SUM(tipo='entrada' AND COALESCE(incompleta,0)=1 AND DATEDIFF(CURDATE(), data_emissao) <= 30) AS recentes, "
        "  MAX(importado_em >= NOW() - INTERVAL 30 DAY) AS cap_30d "
        "FROM nfe_importacoes WHERE origem='SEFAZ' AND cliente_id IS NOT NULL GROUP BY cliente_id",
        fetch=True) or []
    agg = {a['cliente_id']: a for a in _agg}

    empresas = []
    for r in rows:
        backlog = _i(r.get('backlog'))
        cert_doc = r.get('cert_doc')

        # FONTE da captura — espelha dfe_captura._resolver_certificado: cert
        # PRÓPRIO; senão o 1º contador vinculado COM certificado (mesma ordem de
        # contadores_do_cliente); senão nada. Só consulta contador para quem não
        # tem cert próprio, então são poucas queries extras.
        contador = None
        if not cert_doc:
            for ctd in ClienteContador.contadores_do_cliente(r['id']):
                if ctd.get('tem_certificado'):
                    contador = ctd
                    break

        if cert_doc:
            fonte, fonte_icone, fonte_label = 'propria', '🔑', 'Captura própria'
            fonte_det = f"{r.get('cert_tipo_doc') or ''} {cert_doc}".strip()
            ef_validade = r.get('cert_validade')          # cert que de fato autentica
        elif contador:
            fonte, fonte_icone, fonte_label = 'procuracao', '🤝', 'Por procuração'
            fonte_det = (f"{contador.get('numero_cliente') or ''} - "
                         f"{contador.get('nome_razao_social') or ''}").strip(' -')
            ef_validade = contador.get('cert_validade')
        else:
            fonte, fonte_icone, fonte_label = 'carona', '🚏', 'Só de carona'
            fonte_det = 'sem certificado e sem contador — recebe pelo cursor de outra empresa'
            ef_validade = None

        # 'Nunca consultou' antes de 'Em dia': sem cursor, "Em dia" seria mentira
        # (é o caso de quem só recebe de carona, e da empresa recém-vinculada).
        if r.get('em_cota'):
            label, cor = 'Em cota', 'vermelho'
        elif not r.get('ult_consulta'):
            label, cor = 'Nunca consultou', 'cinza'
        elif backlog > 0:
            label, cor = 'Baixando', 'amarelo'
        else:
            label, cor = 'Em dia', 'verde'

        cte_backlog = _i(r.get('cte_backlog'))
        if r.get('cte_em_cota'):
            cte_label, cte_cor = 'Em cota', 'vermelho'
        elif not r.get('cte_ult_consulta'):
            cte_label, cte_cor = 'Nunca consultou', 'cinza'
        elif cte_backlog > 0:
            cte_label, cte_cor = 'Baixando', 'amarelo'
        else:
            cte_label, cte_cor = 'Em dia', 'verde'
        # Validade do cert EFETIVO (próprio ou o do contador) — é ele que a SEFAZ
        # recusa se estiver vencido, então é dele que o alerta tem de falar.
        cs = DfeCertificado.classificar_validade(ef_validade)

        # Frente B — grupo (prioridade: sem cert > atenção > sem movimento > em dia).
        # Sem certificado não pode poluir o grupo de atenção (ausência de meio).
        a = agg.get(r['id']) or {}
        travados = _i(a.get('travados'))
        recentes = _i(a.get('recentes'))
        cap_30d = bool(_i(a.get('cap_30d')))
        has_cert = fonte != 'carona'
        if not has_cert:
            grupo = 'sem_cert'
        elif travados > 0:
            grupo = 'atencao'
        elif not cap_30d:
            grupo = 'sem_movimento'
        else:
            grupo = 'em_dia'
        # #152: cursor em dia e a SEFAZ respondeu 137 — não há documentos para o
        # CNPJ; é ausência de nota, não falha de captura.
        sem_doc_137 = (grupo == 'sem_movimento'
                       and str(r.get('nsu_status') or '').strip().startswith('137'))

        empresas.append({
            'grupo': grupo, 'resumos_travados': travados,
            'resumos_recentes': recentes, 'sem_doc_137': sem_doc_137,
            'numero': r.get('numero_cliente'), 'nome': r.get('nome_razao_social'),
            'fonte': fonte, 'fonte_icone': fonte_icone,
            'fonte_label': fonte_label, 'fonte_det': fonte_det,
            'cert_nivel': cs['nivel'], 'cert_dias': cs['dias'],
            'cert_validade': _fmt_data(ef_validade),
            'ent_total': _i(r.get('ent_total')), 'ent_completas': _i(r.get('ent_completas')),
            'ent_resumos': _i(r.get('ent_resumos')), 'ent_ult': _fmt(r.get('ent_ult')),
            'sai_qtd': _i(r.get('sai_qtd')), 'sai_valor': float(r.get('sai_valor') or 0),
            'sai_de': _fmt_data(r.get('sai_de')), 'sai_ate': _fmt_data(r.get('sai_ate')),
            'sai_ult': _fmt(r.get('sai_ult')),
            'backlog': backlog,
            'status_label': label, 'status_cor': cor,
            'ult_consulta': _fmt(r.get('ult_consulta')),
            'em_cota': bool(r.get('em_cota')),
            'libera_min': _i(r.get('libera_min')) if r.get('em_cota') else None,
            # CT-e: cursor e cota próprios, então status próprio por empresa.
            'cte_total': _i(r.get('cte_total')),
            'cte_tomados': _i(r.get('cte_tomados')),
            'cte_valor_frete': float(r.get('cte_valor_frete') or 0),
            'cte_backlog': cte_backlog,
            'cte_status_label': cte_label, 'cte_status_cor': cte_cor,
            'cte_ult_consulta': _fmt(r.get('cte_ult_consulta')),
            'cte_em_cota': bool(r.get('cte_em_cota')),
            'cte_libera_min': _i(r.get('cte_libera_min')) if r.get('cte_em_cota') else None,
        })

    # ---- HISTÓRICO: últimas ~20 rodadas do dfe_consulta_log (NF-e + CT-e) ----
    # Sem filtro, continua exatamente como era: as últimas 20 rodadas. Com
    # período escolhido, recorta por data e amplia o teto para 200 — bastante
    # para ler uma janela, sem risco de despejar as 13 mil linhas na tela.
    _hwhere, _hparams = [], []
    if fs['servico'] in ('nfe', 'cte'):
        _hwhere.append('l.servico = %s')
        _hparams.append(fs['servico'])
    if fs['cstat'] in ('137', '138', '656'):
        _hwhere.append('l.c_stat = %s')
        _hparams.append(fs['cstat'])
    elif fs['cstat'] == 'erro':
        _hwhere.append("(l.c_stat IS NULL OR l.c_stat NOT IN ('137','138','656'))")
    if fs['periodo'] in ('1', '7', '30'):
        _hwhere.append('l.momento >= NOW() - INTERVAL %s DAY')
        _hparams.append(int(fs['periodo']))
    if fs['empresa']:
        _hwhere.append("(c.nome_razao_social LIKE %s OR c.numero_cliente LIKE %s)")
        _hparams += ['%%%s%%' % fs['empresa'], '%%%s%%' % fs['empresa']]
    _hsql = (' WHERE ' + ' AND '.join(_hwhere)) if _hwhere else ''
    _hlimit = 200 if fs['periodo'] else 20
    hlog = execute_query(
        "SELECT l.momento, l.origem, l.evento, l.c_stat, l.x_motivo, "
        "  l.docs, l.notas, l.servico, c.numero_cliente, c.nome_razao_social "
        "FROM dfe_consulta_log l "
        "LEFT JOIN clientes c ON c.id = l.cliente_id "
        + _hsql +
        " ORDER BY l.momento DESC LIMIT " + str(_hlimit),
        tuple(_hparams) if _hparams else None,
        fetch=True,
    ) or []
    historico = [{
        'momento': _fmt(h.get('momento')), 'origem': h.get('origem'),
        'evento': h.get('evento'), 'c_stat': h.get('c_stat'),
        'x_motivo': h.get('x_motivo'), 'docs': _i(h.get('docs')),
        'notas': _i(h.get('notas')), 'servico': (h.get('servico') or 'nfe'),
        'empresa': ((str(h['numero_cliente']) + ' - ' if h.get('numero_cliente') else '')
                    + (h.get('nome_razao_social') or '—')),
    } for h in hlog]

    # ---- CERTIFICADOS: validade + dias até vencer (SÓ LEITURA de dfe_certificados).
    # Objetivo: nenhum certificado vence em silêncio — um cert vencido faz a SEFAZ
    # recusar o mTLS (HTTP 403) e a captura para sem aviso (ver _detalhe_403). Limiares
    # por env: laranja (<= CERT_ALERTA_DIAS, default 30) e amarelo (<= CERT_ALERTA_AMARELO,
    # default 60). O amarelo nunca fica abaixo do laranja (senão a faixa some).
    laranja = max(0, int(os.getenv('CERT_ALERTA_DIAS', '30')))
    amarelo = max(laranja, int(os.getenv('CERT_ALERTA_AMARELO', '60')))
    crows = execute_query(
        "SELECT c.numero_cliente, c.nome_razao_social, dc.validade, "
        "  DATEDIFF(dc.validade, CURDATE()) AS dias "
        "FROM dfe_certificados dc "
        "JOIN clientes c ON c.id = dc.cliente_id "
        "WHERE dc.ativo = 1 "
        "ORDER BY dc.validade IS NULL, dc.validade ASC",   # mais próximo de vencer 1º
        fetch=True,
    ) or []

    certificados = []
    n_vencidos = n_venc_laranja = 0
    for r in crows:
        dias = r.get('dias')
        dias = int(dias) if dias is not None else None
        if dias is None:
            emoji, status = '⚪', 'sem validade cadastrada'
        elif dias < 0:
            emoji, status = '🔴', f'Vencido há {-dias} dia(s)'
            n_vencidos += 1
        elif dias <= laranja:
            emoji, status = '🟠', f'Vence em {dias} dia(s)'
            n_venc_laranja += 1
        elif dias <= amarelo:
            emoji, status = '🟡', f'Vence em {dias} dia(s)'
        else:
            emoji, status = '✅', 'OK'
        certificados.append({
            'numero': r.get('numero_cliente'), 'nome': r.get('nome_razao_social'),
            'validade': _fmt_data(r.get('validade')), 'dias': dias,
            'emoji': emoji, 'status': status,
        })

    # Banner (mesmo padrão do alerta do Dropbox): só aparece se há VENCIDO ou vencendo
    # em <= laranja dias. Vermelho se há vencido; senão amarelo.
    cert_alerta = {
        'mostrar': (n_vencidos > 0 or n_venc_laranja > 0),
        'nivel': 'vermelho' if n_vencidos > 0 else 'amarelo',
        'n_vencidos': n_vencidos, 'n_venc': n_venc_laranja,
        'dias': laranja, 'dias_amarelo': amarelo,
    }

    # ---- Peneira em Python: só sobre listas JÁ carregadas (Bloco D5) -------
    total_empresas, total_certs = len(empresas), len(certificados)

    def _casa_empresa(txt, numero, nome):
        alvo = ('%s %s' % (numero or '', nome or '')).lower()
        return txt.lower() in alvo

    if fs['empresa']:
        empresas = [e for e in empresas if _casa_empresa(fs['empresa'], e.get('numero'), e.get('nome'))]
        certificados = [c for c in certificados
                        if _casa_empresa(fs['empresa'], c.get('numero'), c.get('nome'))]
    if fs['servico'] == 'nfe':
        empresas = [e for e in empresas if _i(e.get('ent_total')) or _i(e.get('sai_qtd'))]
    elif fs['servico'] == 'cte':
        empresas = [e for e in empresas if _i(e.get('cte_total'))]
    if fs['cert']:
        # 'ok' = tudo que não está vencido nem dentro das janelas de alerta
        def _passa_cert(dias):
            if dias is None:
                return fs['cert'] == 'ok'
            if fs['cert'] == 'vencido':
                return dias < 0
            if fs['cert'] == 'd30':
                return 0 <= dias <= laranja
            if fs['cert'] == 'd60':
                return laranja < dias <= amarelo
            return dias > amarelo
        certificados = [c for c in certificados if _passa_cert(c.get('dias'))]
        empresas = [e for e in empresas if _passa_cert(e.get('cert_dias'))]

    return render_template(
        'escrita_fiscal/status_sefaz.html',
        topo=topo, topo_cte=topo_cte, empresas=empresas, historico=historico,
        certificados=certificados, cert_alerta=cert_alerta,
        topo_saida=topo_saida,
        filtros=fs, filters_active=filtros_ativos,
        total_empresas=total_empresas, total_certs=total_certs,
        cert_laranja=laranja, cert_amarelo=amarelo,
        travadas=_capturas_travadas(),
    )


# ---------------------------------------------------------------------------
# Capturas travadas — quem parou de receber nota, e há quanto tempo
#
# POR QUE ISTO EXISTE. O 656 ("consumo indevido") é invisível: a empresa some da
# captura e ninguém percebe até faltar nota na conferência. Medido em 14/08/2026:
# o Novo Horizonte estava parado havia 2 dias, o Serafim havia UMA SEMANA, e o
# Pavão e o B2T NUNCA tinham capturado — cursor em 0 desde o cadastro.
#
# A tela mostrava totais bonitos (6.445 CT-e, R$ 20 milhões de frete) e escondia
# isso. Aqui o critério é o oposto: só aparece quem está com problema, ordenado
# pelo pior. Empresa em dia não polui a lista.
#
# O ATRASO É A MEDIDA CERTA, e não "dias sem capturar": medido na mesma data, a
# taxa de recusa da SEFAZ cresce com o tamanho do atraso — 35% em dia, 78% com
# 1–50 atrasados, 100% acima de 500. É um círculo vicioso, e o atraso é o que
# diz a que altura do poço a empresa está.
# ---------------------------------------------------------------------------
def _capturas_travadas(limite=25):
    """Empresas cuja captura de NF-e parou. Só leitura, uma consulta.

    Devolve dicts com atraso (documentos represados), dias parada, taxa de 656 e
    se o cursor nunca saiu do zero — este último é o caso que NÃO se resolve
    sozinho e exige seed manual.
    """
    linhas = execute_query(
        """SELECT c.id, c.numero_cliente AS numero, c.nome_razao_social AS razao,
                  n.ult_nsu, n.ult_status, n.proximo_permitido,
                  (SELECT MAX(l.ret_ult_nsu) FROM dfe_consulta_log l
                    WHERE l.cliente_id = n.cliente_id AND l.servico <> 'cte'
                      AND l.momento >= NOW() - INTERVAL 7 DAY) AS sefaz_ult,
                  (SELECT MAX(l.momento) FROM dfe_consulta_log l
                    WHERE l.cliente_id = n.cliente_id AND l.servico <> 'cte'
                      AND l.c_stat IN ('137','138')) AS ult_sucesso,
                  (SELECT ROUND(100 * SUM(l.c_stat='656') / COUNT(*))
                     FROM dfe_consulta_log l
                    WHERE l.cliente_id = n.cliente_id AND l.servico <> 'cte'
                      AND l.evento = 'consulta'
                      AND l.momento >= NOW() - INTERVAL 7 DAY) AS taxa656
             FROM dfe_nsu n
             JOIN clientes c ON c.id = n.cliente_id AND c.situacao = 'ATIVO'""",
        fetch=True) or []

    agora = datetime.now(ZoneInfo('America/Sao_Paulo')).replace(tzinfo=None)
    saida = []
    for r in linhas:
        atraso = (r.get('sefaz_ult') or 0) - (r.get('ult_nsu') or 0)
        if atraso <= 0:
            continue                      # em dia: não é assunto desta lista
        ult = r.get('ult_sucesso')
        dias = (agora - ult).days if ult else None
        saida.append({
            'id': r['id'], 'numero': r['numero'], 'razao': r['razao'],
            'atraso': atraso, 'ult_nsu': r['ult_nsu'], 'sefaz_ult': r['sefaz_ult'],
            'taxa656': int(r['taxa656'] or 0),
            'ult_sucesso': ult, 'dias_parada': dias,
            # Cursor em 0 = nunca capturou. A SEFAZ não despeja o histórico
            # inteiro de uma vez, então esta NUNCA destrava sozinha: é o único
            # caso em que o seed manual é o remédio, e não paliativo.
            'nunca_capturou': not r.get('ult_nsu'),
        })
    # Pior primeiro: quem nunca capturou, depois pelo tamanho do atraso.
    saida.sort(key=lambda x: (not x['nunca_capturou'], -x['atraso']))
    return saida[:limite]


# ---------------------------------------------------------------------------
# Conferência de Compras — página principal
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/')
@permission_required('escrita_fiscal.conf_compras')
def conf_compras():
    empresas = _get_empresas()
    grupos = _get_grupos()
    # Emitentes e UFs não vêm mais daqui: os <select> nascem vazios e são
    # preenchidos por /conf-compras/api/opcoes-filtros quando o usuário escolhe
    # a empresa — só com o que existe naquele escopo.

    # KPIs começam zerados — serão atualizados via JS ao buscar
    stats = {'total_notas': 0, 'total_valor': 0, 'total_icms': 0,
             'total_pis': 0, 'total_cofins': 0}

    dropbox_ok = dropbox_sync.is_configured()

    return render_template(
        'escrita_fiscal/conf_compras.html',
        stats=stats,
        empresas=empresas,
        grupos=grupos,
        # Só admin enxerga o botão de excluir (o gate real está na rota).
        is_admin=current_user.is_admin(),
        dropbox_configured=dropbox_ok,
        dropbox_folder=Config.DROPBOX_XML_FOLDER,
    )


# ---------------------------------------------------------------------------
# API — notas fiscais (com filtros incluindo empresa/grupo)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/notas')
@login_required
def api_notas():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    f_emit_cnpj = _filtro_lista(request.args.get('emit_cnpj', ''))
    f_data_ini = request.args.get('data_ini', '').strip()
    f_data_fim = request.args.get('data_fim', '').strip()
    f_chave = request.args.get('chave', '').strip()
    f_num_nota = request.args.get('num_nota', '').strip()
    f_cfop = request.args.get('cfop', '').strip()
    f_emit_uf = _filtro_lista(request.args.get('emit_uf', ''))
    f_dest_cnpj = request.args.get('dest_cnpj', '').strip()
    f_vmin = request.args.get('vmin', '').strip()
    f_vmax = request.args.get('vmax', '').strip()
    f_origem = request.args.get('origem', '').strip()
    f_vinc_status = request.args.get('vinc_status', '').strip()
    f_cancelado = request.args.get('cancelado', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 50

    # AUDITORIA (D2): leitura — registra a BUSCA (não a paginação). Só na 1ª
    # página e só quando há termo ou algum filtro (abrir a aba vazia não conta).
    _termo = f_chave or f_num_nota
    _filtros = {k: v for k, v in (
        ('cliente_id', f_cliente_id), ('grupo_id', f_grupo_id),
        ('emit_cnpj', request.args.get('emit_cnpj', '').strip()),
        ('data_ini', f_data_ini), ('data_fim', f_data_fim), ('cfop', f_cfop),
        ('emit_uf', request.args.get('emit_uf', '').strip()),
        ('dest_cnpj', f_dest_cnpj), ('vmin', f_vmin), ('vmax', f_vmax),
        ('origem', f_origem), ('vinc_status', f_vinc_status),
        ('cancelado', f_cancelado)) if v}
    if page == 1 and (_termo or _filtros):
        _filtros.update(rotulo_empresa(f_cliente_id, f_grupo_id))
        registrar('leitura.buscou_entradas', 'fiscal', tabela='nfe_importacoes',
                  depois={'termo': _termo or None, 'filtros': _filtros})

    extra_clauses, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    where = ["n.tipo = 'entrada'"] + extra_clauses

    if f_emit_cnpj:
        where.append(_clausula_in('n.emit_cnpj', f_emit_cnpj, params))
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    if f_chave:
        where.append('n.chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_num_nota:
        where.append('n.num_nota = %s')
        params.append(f_num_nota)
    if f_cfop:
        where.append('n.cfop LIKE %s')
        params.append(f'{f_cfop}%')
    if f_emit_uf:
        where.append(_clausula_in('n.emit_uf', f_emit_uf, params))
    if f_dest_cnpj:
        where.append('n.dest_cnpj LIKE %s')
        params.append(f'%{f_dest_cnpj}%')
    if f_vmin:
        where.append('n.valor_total >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('n.valor_total <= %s')
        params.append(float(f_vmax))
    if f_origem == 'SEFAZ':
        where.append("n.origem = 'SEFAZ'")
    elif f_origem == 'MANUAL':
        where.append("n.origem IN ('UPLOAD','DROPBOX')")
    elif f_origem:
        where.append('n.origem = %s')
        params.append(f_origem)
    _aplica_cancelada(where, f_cancelado, 'n')
    if f_vinc_status == 'completo':
        where.append(
            "NOT EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NULL)"
            " AND EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id)"
        )
    elif f_vinc_status == 'parcial':
        where.append(
            "EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NOT NULL)"
            " AND EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NULL)"
        )
    elif f_vinc_status == 'sem':
        where.append(
            "NOT EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NOT NULL)"
        )
    elif f_vinc_status == 'incompleto':
        where.append(
            "EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NULL)"
        )

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    offset = (page - 1) * per_page

    # Single query: window functions supply total count + KPI aggregates while
    # LIMIT/OFFSET pages the rows — avoids 3 separate round-trips to the DB.
    all_rows = execute_query(
        f"""SELECT n.id, n.chave_acesso, n.num_nota, n.serie, n.data_emissao,
                   n.emit_cnpj, n.emit_nome, n.emit_uf,
                   n.dest_cnpj, n.dest_nome,
                   n.valor_total, n.valor_icms, n.valor_pis, n.valor_cofins, n.valor_ipi,
                   n.cfop, n.natureza_operacao, n.origem, n.incompleta, n.cancelada,
                   n.nome_arquivo,
                   n.importado_em, n.atualizado_em, n.cliente_id, n.grupo_id,
                   c.nome_razao_social AS empresa_nome,
                   g.nome AS grupo_nome,
                   COALESCE(ic.qtd_itens, 0) AS qtd_itens,
                   COALESCE(ic.itens_vinculados, 0) AS itens_vinculados,
                   COUNT(*) OVER() AS _total,
                   COALESCE(SUM(n.valor_total) OVER(), 0) AS _kpi_valor,
                   COALESCE(SUM(n.valor_icms)  OVER(), 0) AS _kpi_icms,
                   COALESCE(SUM(n.valor_pis)   OVER(), 0) AS _kpi_pis,
                   COALESCE(SUM(n.valor_cofins) OVER(), 0) AS _kpi_cofins
              FROM nfe_importacoes n
              LEFT JOIN clientes c ON c.id = n.cliente_id
              LEFT JOIN grupos_clientes g ON g.id = n.grupo_id
              LEFT JOIN (
                  SELECT nfe_id,
                         COUNT(*) AS qtd_itens,
                         COUNT(produto_catalogo_id) AS itens_vinculados
                    FROM nfe_itens
                   GROUP BY nfe_id
              ) ic ON ic.nfe_id = n.id
              {where_sql}
             ORDER BY n.data_emissao DESC, n.id DESC
             LIMIT %s OFFSET %s""",
        tuple(params) + (per_page, offset),
        fetch=True,
    ) or []

    # Extract window-function values from the first row (same for all rows)
    first = all_rows[0] if all_rows else {}
    total = int(first.get('_total') or 0)
    kpi = {
        'total_valor': float(first.get('_kpi_valor') or 0),
        'total_icms':  float(first.get('_kpi_icms')  or 0),
        'total_pis':   float(first.get('_kpi_pis')   or 0),
        'total_cofins':float(first.get('_kpi_cofins') or 0),
    }

    # Handle empty result: total/kpi must still be valid (window cols absent)
    if not all_rows:
        total = 0
        kpi = {'total_valor': 0, 'total_icms': 0, 'total_pis': 0, 'total_cofins': 0}

    rows = []
    _window_cols = {'_total', '_kpi_valor', '_kpi_icms', '_kpi_pis', '_kpi_cofins'}
    for r in all_rows:
        row = {k: v for k, v in r.items() if k not in _window_cols}
        for k in ('data_emissao', 'importado_em', 'atualizado_em'):
            if row.get(k) and hasattr(row[k], 'isoformat'):
                row[k] = row[k].isoformat()
        for k in ('valor_total', 'valor_icms', 'valor_pis', 'valor_cofins', 'valor_ipi'):
            row[k] = float(row.get(k) or 0)
        rows.append(row)

    return jsonify({
        'total': total, 'page': page, 'per_page': per_page, 'rows': rows,
        'kpi': kpi,
    })


# ---------------------------------------------------------------------------
# Export de nota (Fase 1: por nota) — XML e PDF. Reaproveitado por conf-compras
# e conf-saidas. Fonte do XML = nfe_importacoes.xml_raw (banco), que é o nfeProc
# completo (com protNFe) — NÃO usa Dropbox. O guard de acesso segue o TIPO da
# nota, igual às telas que a listam: entrada→conf_compras, saída→conf_saidas.
# ---------------------------------------------------------------------------
def _carregar_nota_export(nfe_id):
    """Retorna (nota, None) pronta para exportar, ou (None, (msg, status)) com uma
    resposta amigável para o chamador devolver direto. Cobre: nota inexistente
    (404), sem permissão para o tipo (403) e resumo/xml vazio (404)."""
    nota = execute_query(
        "SELECT xml_raw, chave_acesso, incompleta, tipo "
        "FROM nfe_importacoes WHERE id = %s",
        (nfe_id,), fetch=True, fetch_one=True,
    )
    if not nota:
        return None, ('Nota não encontrada.', 404)
    # Mesma proteção das telas de conferência (admin passa via has_permission).
    codigo = ('escrita_fiscal.conf_saidas' if nota.get('tipo') == 'saida'
              else 'escrita_fiscal.conf_compras')
    if not current_user.has_permission(codigo):
        return None, ('Você não tem permissão para baixar esta nota.', 403)
    if nota.get('incompleta') or not (nota.get('xml_raw') or '').strip():
        return None, ('Esta nota é só um resumo da SEFAZ — o XML completo não está '
                      'disponível para exportar.', 404)
    return nota, None


@escrita_fiscal.route('/nota/<int:nfe_id>/xml')
@login_required
def nota_xml(nfe_id):
    """Baixa o XML (nfeProc) da nota direto do banco (nfe_importacoes.xml_raw)."""
    nota, erro = _carregar_nota_export(nfe_id)
    if erro:
        return erro
    chave = nota.get('chave_acesso') or str(nfe_id)
    return send_file(BytesIO(nota['xml_raw'].encode('utf-8')), as_attachment=True,
                     download_name=f'{chave}.xml', mimetype='application/xml')


@escrita_fiscal.route('/nota/<int:nfe_id>/pdf')
@login_required
def nota_pdf(nfe_id):
    """Gera o DANFE/DACTE em PDF a partir do xml_raw (nfeProc autorizado).

    Modelo pelos dígitos 21-22 da chave: 55=DANFE, 57=DACTE (brazilfiscalreport),
    65=NFC-e (DANFCE próprio em ReportLab, utils/danfce). A geração é BLINDADA: se o
    XML não for o proc autorizado esperado e a lib falhar, devolve erro amigável,
    nunca 500.

    ?inline=1 → Content-Disposition: inline, para o <iframe> do modal de
    visualização renderizar o PDF em vez de baixar. Sem o parâmetro continua
    attachment (download), como sempre foi. A geração é a MESMA nos dois casos."""
    nota, erro = _carregar_nota_export(nfe_id)
    if erro:
        return erro
    chave = nota.get('chave_acesso') or ''
    xml_raw = nota['xml_raw']
    modelo = chave[20:22] if len(chave) >= 22 else ''

    if modelo == '65':
        # NFC-e: DANFCE próprio em ReportLab (a brazilfiscalreport não gera 65).
        # Blindagem igual à dos 55/57: XML fora do padrão → 422 amigável, nunca 500.
        try:
            from utils.danfce import gerar_danfce_pdf
            pdf_bytes = gerar_danfce_pdf(xml_raw)
        except Exception:
            logging.getLogger(__name__).exception(
                '[export] falha ao gerar DANFCE da nfe_id=%s (chave=%s)', nfe_id, chave)
            return ('Não foi possível gerar o PDF desta NFC-e (o XML pode estar fora '
                    'do padrão esperado). Baixe o XML por enquanto.', 422)
        inline = request.args.get('inline') in ('1', 'true', 'sim')
        return send_file(BytesIO(pdf_bytes), as_attachment=not inline,
                         download_name=f'{chave or nfe_id}.pdf', mimetype='application/pdf')
    try:
        if modelo == '57':
            # NOTA: CT-e não é gravado em nfe_importacoes (fica em cte_documentos),
            # então este ramo é INALCANÇÁVEL vindo de conf-compras/conf-saidas —
            # lá só há 55/65. Fica pronto para um export de CT-e que reuse este
            # endpoint com a fonte certa (cte_documentos.xml_raw).
            from brazilfiscalreport.dacte import Dacte
            doc = Dacte(xml=xml_raw)
        else:
            # 55 (e qualquer outro NF-e): DANFE.
            from brazilfiscalreport.danfe import Danfe
            doc = Danfe(xml=xml_raw)
        # fpdf2 (base da lib): output() sem destino devolve o PDF como bytearray.
        pdf_bytes = bytes(doc.output())
    except Exception:
        logging.getLogger(__name__).exception(
            '[export] falha ao gerar PDF da nfe_id=%s (chave=%s)', nfe_id, chave)
        return ('Não foi possível gerar o PDF desta nota (o XML pode estar fora do '
                'padrão esperado). Baixe o XML por enquanto.', 422)
    inline = request.args.get('inline') in ('1', 'true', 'sim')
    return send_file(BytesIO(pdf_bytes), as_attachment=not inline,
                     download_name=f'{chave or nfe_id}.pdf', mimetype='application/pdf')


@escrita_fiscal.route('/nota/<int:nfe_id>/capturar', methods=['POST'])
@login_required
def nota_capturar(nfe_id):
    """Reconsulta a nota pela CHAVE para tentar trazer o XML completo.

    É o botão "Capturar documento" das linhas em RESUMO. Chama o consChNFe (uma
    requisição, leitura pura — NÃO manifesta, NÃO assina) pelo mesmo motor da
    captura; a gravação é a mesma do cron. Guard de acesso = a permissão da tela
    que lista a nota, igual ao export.

    A resposta é sempre 200 quando a consulta aconteceu: `completa` diz se a nota
    subiu de resumo para completa, e `aguardar` sinaliza cota da SEFAZ. Os códigos
    de erro ficam para os guards (404/403/400)."""
    nota = execute_query(
        "SELECT chave_acesso, cliente_id, tipo, incompleta "
        "FROM nfe_importacoes WHERE id = %s",
        (nfe_id,), fetch=True, fetch_one=True,
    )
    if not nota:
        return jsonify({'ok': False, 'erro': 'Nota não encontrada.'}), 404

    codigo = ('escrita_fiscal.conf_saidas' if nota.get('tipo') == 'saida'
              else 'escrita_fiscal.conf_compras')
    if not current_user.has_permission(codigo):
        return jsonify({'ok': False, 'erro': 'Você não tem permissão para esta nota.'}), 403
    if not nota.get('incompleta'):
        return jsonify({'ok': False, 'erro': 'Esta nota já está completa.'}), 400
    if not nota.get('cliente_id'):
        return jsonify({'ok': False,
                        'erro': 'Nota sem empresa vinculada — não dá para saber qual '
                                'certificado usar na consulta.'}), 400
    chave = (nota.get('chave_acesso') or '').strip()
    if len(chave) != 44:
        return jsonify({'ok': False, 'erro': 'Nota sem chave de acesso válida.'}), 400

    # Import local: o motor de captura puxa certificado/Dropbox/SEFAZ e só é
    # necessário neste endpoint.
    from utils.integrations import dfe_captura

    r = dfe_captura.capturar_por_chave(nota['cliente_id'], chave, origem='manual')

    # AUDITORIA (D2): leitura — consulta à SEFAZ disparada pelo usuário (botão
    # "Capturar documento"). A chave de acesso é pública, não é dado sensível.
    registrar('leitura.consultou_sefaz', 'fiscal', tabela='nfe_importacoes',
              registro_id=nfe_id,
              depois={'chave': chave, 'cliente_id': nota['cliente_id'], 'origem': 'manual',
                      **rotulo_empresa(nota['cliente_id'])})

    if r.get('bloqueado') or r.get('consumo_indevido'):
        return jsonify({'ok': False, 'aguardar': True,
                        'erro': r.get('erro') or 'A SEFAZ pediu para aguardar.'})
    if not r.get('ok'):
        return jsonify({'ok': False, 'erro': r.get('erro') or 'Não foi possível consultar.'})
    return jsonify({'ok': True, 'completa': bool(r.get('completa')), 'chave': chave,
                    'itens': r.get('itens') or 0,
                    'mensagem': r.get('mensagem') or ''})


# ---------------------------------------------------------------------------
# Export de CT-e — XML e PDF (DACTE). Espelha o export de NF-e acima, com UMA
# diferença de fundo: em ``cte_documentos`` a coluna ``xml_raw`` está VAZIA em
# 100% das linhas — a captura de CT-e grava o XML no Dropbox e guarda só o
# ``xml_caminho``. Então a fonte aqui é o arquivo, com ``xml_raw`` como
# preferência caso um dia passe a ser preenchido (upload/importação futura).
# Guard de acesso = a MESMA permissão da tela que lista (conf_cte).
# ---------------------------------------------------------------------------
def _carregar_cte_export(cte_id):
    """Retorna (cte, xml, None) pronto para exportar, ou (None, None, (msg, status)).

    Cobre: CT-e inexistente (404), sem permissão (403), sem XML disponível nem
    no banco nem no Dropbox (404) e falha de download (502)."""
    cte = execute_query(
        "SELECT xml_raw, xml_caminho, chave_acesso, modelo FROM cte_documentos "
        "WHERE id = %s",
        (cte_id,), fetch=True, fetch_one=True,
    )
    if not cte:
        return None, None, ('CT-e não encontrado.', 404)
    if not current_user.has_permission('escrita_fiscal.conf_cte'):
        return None, None, ('Você não tem permissão para baixar este CT-e.', 403)

    xml = (cte.get('xml_raw') or '').strip()
    if xml:
        return cte, xml, None

    caminho = (cte.get('xml_caminho') or '').strip()
    if not caminho:
        return None, None, ('Este CT-e não tem o XML guardado — nada a exportar.', 404)
    try:
        xml = dropbox_sync.download_xml(caminho)
    except (DropboxAuthError, DropboxError) as exc:
        logging.getLogger(__name__).warning(
            '[export] falha ao baixar XML do CT-e id=%s (%s): %s', cte_id, caminho, exc)
        return None, None, ('Não foi possível ler o XML no Dropbox agora. '
                            'Tente de novo em instantes.', 502)
    if not xml:
        return None, None, ('O XML deste CT-e não está mais no Dropbox '
                            '(o arquivo pode ter sido movido ou removido).', 404)
    return cte, xml, None


@escrita_fiscal.route('/cte/<int:cte_id>/xml')
@login_required
def cte_xml(cte_id):
    """Baixa o XML do CT-e (do banco quando houver, senão do Dropbox)."""
    cte, xml, erro = _carregar_cte_export(cte_id)
    if erro:
        return erro
    chave = cte.get('chave_acesso') or str(cte_id)
    return send_file(BytesIO(xml.encode('utf-8')), as_attachment=True,
                     download_name=f'{chave}.xml', mimetype='application/xml')


@escrita_fiscal.route('/cte/<int:cte_id>/pdf')
@login_required
def cte_pdf(cte_id):
    """Gera o DACTE em PDF do CT-e (modelo 57).

    ?inline=1 → Content-Disposition inline, para o <iframe> do modal renderizar
    em vez de baixar — igual ao PDF da NF-e. Modelos 67 (CT-e OS) e 64 (GTV-e)
    não têm layout na lib: devolvem 400 amigável e a tela só oferece o XML.
    A geração é BLINDADA: qualquer falha vira 422, nunca 500."""
    cte, xml, erro = _carregar_cte_export(cte_id)
    if erro:
        return erro
    chave = cte.get('chave_acesso') or ''
    modelo = (cte.get('modelo') or '').strip() or (chave[20:22] if len(chave) >= 22 else '')

    if modelo and modelo != '57':
        return (f'PDF do modelo {modelo} ainda não é gerado (a biblioteca só monta '
                f'o DACTE do CT-e modelo 57). Use o XML por enquanto.', 400)
    try:
        from brazilfiscalreport.dacte import Dacte
        doc = Dacte(xml=xml)
        # fpdf2 (base da lib): output() sem destino devolve o PDF como bytearray.
        pdf_bytes = bytes(doc.output())
    except Exception:
        logging.getLogger(__name__).exception(
            '[export] falha ao gerar DACTE do cte_id=%s (chave=%s)', cte_id, chave)
        return ('Não foi possível gerar o PDF deste CT-e (o XML pode estar fora do '
                'padrão esperado). Baixe o XML por enquanto.', 422)
    inline = request.args.get('inline') in ('1', 'true', 'sim')
    return send_file(BytesIO(pdf_bytes), as_attachment=not inline,
                     download_name=f'{chave or cte_id}.pdf', mimetype='application/pdf')


@escrita_fiscal.route('/conf-cte/excluir/<int:cte_id>', methods=['POST'])
@login_required
def excluir_cte(cte_id):
    """Exclui um CT-e. SÓ ADMIN — mesmo gate do excluir_nfe: esconder o botão no
    front não impede um não-admin de chamar a URL na mão."""
    if not current_user.is_admin():
        logger.warning('[excluir_cte] usuário %s (não-admin) tentou excluir o cte_id=%s',
                       getattr(current_user, 'id', '?'), cte_id)
        msg = 'Apenas administradores podem excluir CT-e.'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': msg}), 403
        flash(msg, 'error')
        return redirect(url_for('escrita_fiscal.conf_cte'))

    execute_query("DELETE FROM cte_documentos WHERE id = %s", (cte_id,))
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    flash('CT-e excluído.', 'success')
    return redirect(url_for('escrita_fiscal.conf_cte'))


@escrita_fiscal.route('/conf-cte/excluir-lote', methods=['POST'])
@login_required
def excluir_lote_cte():
    """Exclui os CT-e MARCADOS ou, sem nada marcado (ids vazios), todos os do
    filtro atual — o mesmo _where_lote que o export usa, então o escopo de
    empresa entra sempre e uma lista de ids forjada não alcança outra empresa.
    SÓ ADMIN, mesmo gate do excluir_cte, aplicado antes de montar o WHERE.
    As NF-e transportadas (cte_nfe) saem por ON DELETE CASCADE."""
    if not current_user.is_admin():
        logger.warning('[excluir_lote_cte] usuário %s (não-admin) tentou excluir CT-e em lote',
                       getattr(current_user, 'id', '?'))
        return jsonify({'error': 'Apenas administradores podem excluir CT-e.'}), 403

    data = request.get_json(silent=True) or {}
    # Trava: sem ids marcados E sem empresa/grupo o WHERE ficaria vazio e o DELETE
    # varreria a base inteira. A tela já exige escopo para listar; aqui é o gate.
    if not _ids_do_lote(data) and not (str(data.get('cliente_id', '')).strip()
                                       or str(data.get('grupo_id', '')).strip()):
        return jsonify({'error': 'Selecione uma empresa ou grupo antes de excluir em lote.'}), 400

    where, params = _where_lote('cte', data)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    total = int((execute_query(
        f"SELECT COUNT(*) AS t FROM cte_documentos t {where_sql}",
        tuple(params), fetch=True, fetch_one=True) or {}).get('t', 0))
    if total:
        execute_query(f"DELETE t FROM cte_documentos t {where_sql}", tuple(params))
    return jsonify({'ok': True, 'deleted': total})


# ---------------------------------------------------------------------------
# Export em LOTE — .zip de XMLs e de PDFs
#
# XML: opera sobre a SELEÇÃO (ids marcados) ou, se nada estiver marcado, sobre
# o FILTRO inteiro da tela — as mesmas cláusulas do excluir-lote. Tem teto: um
# posto faz milhares de NFC-e por mês, e um zip de tudo derrubaria a requisição.
# PDF: só por seleção e no máximo _LOTE_MAX_PDF, porque cada PDF é gerado na
# hora (a lib monta o layout inteiro) — é a operação cara.
#
# NOTA: as cláusulas de filtro abaixo espelham as do excluir_lote/
# excluir_lote_saidas. Preferi duplicar a NÃO mexer no caminho de exclusão, que
# está em produção e apaga dado. Se um filtro novo entrar na tela, precisa
# entrar nos dois lugares.
# ---------------------------------------------------------------------------
# NF-e/NFC-e: teto alto porque o "tudo do filtro" agora sai em STREAMING (zip
# gerado em memória constante — ver _stream_xml_lote_nfe), então não derruba a
# requisição nem com dezenas de milhares. Média real ~12 mil/mês por posto.
_LOTE_MAX_XML = 50000
# Tamanho do lote (por PK) na leitura do xml_raw no streaming — NUNCA todos de uma
# vez. 1000 × ~8 KB ≈ 8 MB por lote em memória.
_LOTE_STREAM_CHUNK = 1000
# Proibidos em nome de arquivo no Windows (e recusados pelo Dropbox).
_INVALIDOS_NOME = {ord(c): None for c in '/\\:*?"<>|'}
# CT-e é mais baixo de propósito: o XML não está no banco, cada um é um download
# do Dropbox. Mesmo em paralelo, 1000 arquivos estouraria o tempo da requisição.
_LOTE_MAX_XML_CTE = 200
_LOTE_MAX_PDF = 10


def _ids_do_lote(data):
    """IDs marcados na tela. Só inteiros, sem repetição; o resto é descartado."""
    brutos = data.get('ids') or []
    if not isinstance(brutos, (list, tuple)):
        return []
    vistos, ids = set(), []
    for b in brutos:
        s = str(b).strip()
        if s.isdigit() and s not in vistos:
            vistos.add(s)
            ids.append(int(s))
    return ids


def _sanitiza_nome(txt):
    """Tira do nome o que Windows/Dropbox não aceitam em arquivo.

    Além dos caracteres proibidos, colapsa espaços e apara ponto/espaço do fim —
    o Windows recusa silenciosamente nomes terminados assim."""
    limpo = (txt or '').translate(_INVALIDOS_NOME)
    return ' '.join(limpo.split()).strip(' .')


def _periodo_zip(datas):
    """Período do nome do .zip a partir das data_emissao das NOTAS que compõem o
    zip: 'MM.AAAA' quando o menor e o maior mês coincidem, 'MM.AAAA_a_MM.AAAA'
    quando diferem, '' quando não há data.

    Vem das notas que realmente entram no zip, NÃO do filtro (data_ini/data_fim) —
    o filtro pode ser mais largo do que a seleção. O min/max é por (ano, mês), não
    pela string 'MM.AAAA' (que ordenaria 12.2025 depois de 01.2026)."""
    yms = []
    for d in (datas or []):
        try:
            dt = datetime.strptime(str(d)[:10], '%Y-%m-%d')
        except (ValueError, TypeError):
            continue
        yms.append((dt.year, dt.month))
    if not yms:
        return ''
    fmt = lambda ym: f'{ym[1]:02d}.{ym[0]}'
    lo, hi = min(yms), max(yms)
    return fmt(lo) if lo == hi else f'{fmt(lo)}_a_{fmt(hi)}'


_PREFIXO_LOTE = {'entrada': 'ENTRADAS', 'saida': 'SAIDAS', 'cte': 'CTE'}


def _prefixo_lote(escopo, formato):
    """Prefixo do nome do zip: '<TELA> <FORMATO>' — ENTRADAS/SAIDAS/CTE + XML/PDF."""
    return f'{_PREFIXO_LOTE.get(escopo, "")} {formato}'.strip()


def _nome_zip_lote(data, datas, prefixo=''):
    """'<PREFIXO> <numero> - <razão> - <período>.zip' da empresa selecionada.

    ``data`` traz o escopo (empresa/grupo); ``datas`` são as data_emissao das notas
    que compõem o zip — o período sai do MIN/MAX delas, não do filtro. ``prefixo`` é
    o TIPO da tela (ENTRADAS/SAIDAS/CTE), vindo do escopo do lote. Em visão por GRUPO
    usa o nome do grupo (não há número/razão de uma empresa só). Sem escopo ou sem
    data, a parte que faltar simplesmente não entra."""
    cid = str(data.get('cliente_id', '')).strip()
    gid = str(data.get('grupo_id', '')).strip()
    quem = ''
    if cid.isdigit():
        r = execute_query(
            "SELECT numero_cliente, nome_razao_social FROM clientes WHERE id = %s",
            (int(cid),), fetch=True, fetch_one=True) or {}
        numero = str(r.get('numero_cliente') or '').strip()
        razao = str(r.get('nome_razao_social') or '').strip()
        quem = f'{numero} - {razao}' if numero and razao else (razao or numero)
    elif gid.isdigit():
        r = execute_query("SELECT nome FROM grupos_clientes WHERE id = %s",
                          (int(gid),), fetch=True, fetch_one=True) or {}
        quem = str(r.get('nome') or '').strip()
    partes = [p for p in (_sanitiza_nome(quem), _periodo_zip(datas)) if p]
    base = ' - '.join(partes) or 'documentos'
    return (f'{prefixo} {base}' if prefixo else base) + '.zip'


def _zip_download(arquivos, nome_zip):
    """arquivos = [(nome, bytes)] → resposta com o .zip pronto para baixar."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for nome, dados in arquivos:
            z.writestr(nome, dados)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=nome_zip,
                     mimetype='application/zip')


def _where_lote_entradas(data):
    """Mesmas cláusulas do excluir_lote (entradas), a partir do corpo JSON."""
    f_cliente_id = str(data.get('cliente_id', '')).strip()
    f_grupo_id   = str(data.get('grupo_id', '')).strip()
    f_emit_cnpj  = _filtro_lista(data.get('emit_cnpj', ''))
    f_data_ini   = str(data.get('data_ini', '')).strip()
    f_data_fim   = str(data.get('data_fim', '')).strip()
    f_chave      = str(data.get('chave', '')).strip()
    f_num_nota   = str(data.get('num_nota', '')).strip()
    f_cfop       = str(data.get('cfop', '')).strip()
    f_emit_uf    = _filtro_lista(data.get('emit_uf', ''))
    f_dest_cnpj  = str(data.get('dest_cnpj', '')).strip()
    f_vmin       = str(data.get('vmin', '')).strip()
    f_vmax       = str(data.get('vmax', '')).strip()
    f_origem     = str(data.get('origem', '')).strip()
    f_cancelado  = str(data.get('cancelado', '')).strip()

    where = ["n.tipo = 'entrada'"]
    extra, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_emit_cnpj:
        where.append(_clausula_in('n.emit_cnpj', f_emit_cnpj, params))
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    if f_chave:
        where.append('n.chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_num_nota:
        where.append('n.num_nota = %s')
        params.append(f_num_nota)
    if f_cfop:
        where.append('n.cfop LIKE %s')
        params.append(f'{f_cfop}%')
    if f_emit_uf:
        where.append(_clausula_in('n.emit_uf', f_emit_uf, params))
    if f_dest_cnpj:
        where.append('n.dest_cnpj LIKE %s')
        params.append(f'%{f_dest_cnpj}%')
    if f_vmin:
        where.append('n.valor_total >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('n.valor_total <= %s')
        params.append(float(f_vmax))
    if f_origem:
        where.append('n.origem = %s')
        params.append(f_origem)
    _aplica_cancelada(where, f_cancelado, 'n')
    return where, params


def _where_lote_saidas(data):
    """Mesmas cláusulas do excluir_lote_saidas, a partir do corpo JSON."""
    f_cliente_id = str(data.get('cliente_id', '')).strip()
    f_grupo_id   = str(data.get('grupo_id', '')).strip()
    f_dest_cnpj  = _filtro_lista(data.get('dest_cnpj', ''))
    f_data_ini   = str(data.get('data_ini', '')).strip()
    f_data_fim   = str(data.get('data_fim', '')).strip()
    f_chave      = str(data.get('chave', '')).strip()
    f_num_nota   = str(data.get('num_nota', '')).strip()
    f_cfop       = str(data.get('cfop', '')).strip()
    f_dest_uf    = _filtro_lista(data.get('dest_uf', ''))
    f_emit_cnpj  = str(data.get('emit_cnpj', '')).strip()
    f_vmin       = str(data.get('vmin', '')).strip()
    f_vmax       = str(data.get('vmax', '')).strip()
    f_origem     = str(data.get('origem', '')).strip()
    f_cancelado  = str(data.get('cancelado', '')).strip()

    where = ["n.tipo = 'saida'"]
    extra, params = _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_dest_cnpj:
        where.append(_clausula_in('n.dest_cnpj', f_dest_cnpj, params))
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    if f_chave:
        where.append('n.chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_num_nota:
        where.append('n.num_nota = %s')
        params.append(f_num_nota)
    if f_cfop:
        where.append('n.cfop LIKE %s')
        params.append(f'{f_cfop}%')
    if f_dest_uf:
        where.append(_clausula_in('n.dest_uf', f_dest_uf, params))
    if f_emit_cnpj:
        where.append('n.emit_cnpj LIKE %s')
        params.append(f'%{f_emit_cnpj}%')
    if f_vmin:
        where.append('n.valor_total >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('n.valor_total <= %s')
        params.append(float(f_vmax))
    if f_origem:
        where.append('n.origem = %s')
        params.append(f_origem)
    _aplica_cancelada(where, f_cancelado, 'n')
    return where, params


def _where_lote_cte(data):
    """Mesmas cláusulas do api_ctes, a partir do corpo JSON."""
    f_cliente_id = str(data.get('cliente_id', '')).strip()
    f_grupo_id   = str(data.get('grupo_id', '')).strip()
    f_emit_cnpj  = _filtro_lista(data.get('emit_cnpj', ''))
    f_tomador    = str(data.get('tomador_cnpj', '')).strip()
    f_data_ini   = str(data.get('data_ini', '')).strip()
    f_data_fim   = str(data.get('data_fim', '')).strip()
    f_chave      = str(data.get('chave', '')).strip()
    f_num_cte    = str(data.get('num_cte', '')).strip()
    f_modelo     = str(data.get('modelo', '')).strip()
    f_uf_ini     = _filtro_lista(data.get('uf_ini', ''))
    f_uf_fim     = _filtro_lista(data.get('uf_fim', ''))
    f_origem     = str(data.get('origem', '')).strip()
    f_papel      = str(data.get('papel', '')).strip()
    f_cancelado  = str(data.get('cancelado', '')).strip()

    where, params = _empresa_where_cte(f_cliente_id, f_grupo_id, alias='t', params=[])
    if f_emit_cnpj:
        where.append(_clausula_in('t.emit_cnpj', f_emit_cnpj, params))
    if f_tomador:
        where.append("REPLACE(REPLACE(REPLACE(t.tomador_cnpj,'.',''),'/',''),'-','') LIKE %s")
        params.append('%' + re.sub(r'\D', '', f_tomador) + '%')
    if f_data_ini:
        where.append('t.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('t.data_emissao <= %s')
        params.append(f_data_fim)
    if f_chave:
        where.append('t.chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_num_cte:
        where.append('t.num_cte = %s')
        params.append(f_num_cte)
    if f_modelo:
        where.append('t.modelo = %s')
        params.append(f_modelo)
    if f_uf_ini:
        where.append(_clausula_in('t.uf_ini', f_uf_ini, params))
    if f_uf_fim:
        where.append(_clausula_in('t.uf_fim', f_uf_fim, params))
    if f_origem == 'SEFAZ':
        where.append("t.origem = 'SEFAZ'")
    elif f_origem == 'MANUAL':
        where.append("t.origem IN ('UPLOAD','DROPBOX')")
    elif f_origem:
        where.append('t.origem = %s')
        params.append(f_origem)
    _aplica_cancelada(where, f_cancelado, 't', 'cancelado')
    if f_papel:
        where.append('t.papel_cliente = %s')
        params.append(f_papel)
    return where, params


def _where_lote(escopo, data):
    """Monta o WHERE do lote: os IDs marcados quando houver, senão o filtro da
    tela. Nos dois casos o escopo de empresa entra — assim uma lista de ids
    forjada não alcança documento de outra empresa."""
    ids = _ids_do_lote(data)
    if not ids:
        if escopo == 'entrada':
            return _where_lote_entradas(data)
        if escopo == 'saida':
            return _where_lote_saidas(data)
        return _where_lote_cte(data)

    cid = str(data.get('cliente_id', '')).strip()
    gid = str(data.get('grupo_id', '')).strip()
    if escopo == 'cte':
        where, params = _empresa_where_cte(cid, gid, alias='t', params=[])
        where.append(_clausula_in('t.id', [str(i) for i in ids], params))
        return where, params
    fn = _empresa_where if escopo == 'entrada' else _empresa_where_saidas
    extra, params = fn(cid, gid, alias='n', params=[])
    where = [f"n.tipo = '{escopo}'"] + extra
    where.append(_clausula_in('n.id', [str(i) for i in ids], params))
    return where, params


def _gerar_pdf_documento(xml, modelo):
    """DANFE (55) / DACTE (57) / DANFCE (65) em bytes, ou None se não montar o layout."""
    if modelo == '57':
        from brazilfiscalreport.dacte import Dacte
        return bytes(Dacte(xml=xml).output())
    if modelo == '55':
        from brazilfiscalreport.danfe import Danfe
        return bytes(Danfe(xml=xml).output())
    if modelo == '65':
        # NFC-e: DANFCE próprio em ReportLab (a brazilfiscalreport não gera 65).
        from utils.danfce import gerar_danfce_pdf
        return gerar_danfce_pdf(xml)
    return None


def _cd_attachment_zip(nome):
    """Content-Disposition de anexo com o nome em RFC 5987 (filename*=UTF-8''…) — o
    mesmo que o send_file monta, mas para respostas em STREAMING (Response), onde o
    cabeçalho é setado à mão. O front lê o filename* p/ preservar acento/espaço."""
    from urllib.parse import quote
    ascii_fb = nome.encode('ascii', 'ignore').decode() or 'documentos.zip'
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_fb, quote(nome))


def _stream_xml_lote_nfe(where_sql, params, nome_zip):
    """Zip de XML em STREAMING (memória constante) para o caminho 'tudo do filtro',
    onde o volume chega a dezenas de milhares. Busca id+chave de uma vez (leve: sem
    xml_raw) e o xml_raw em lotes de _LOTE_STREAM_CHUNK por PK, lidos SÓ na hora de
    o zipstream gravar cada arquivo (a lib consome o data-generator na iteração).
    Assim nunca há mais de um lote de XML em memória e o download começa na hora —
    sem montar centenas de MB antes (evita OOM/timeout do build)."""
    from zipstream import ZipStream

    meta = execute_query(
        f"SELECT n.id, n.chave_acesso FROM nfe_importacoes n {where_sql} ORDER BY n.id",
        tuple(params), fetch=True) or []
    ids = [m['id'] for m in meta]

    def fonte_xml():
        # xml_raw na MESMA ordem de ``meta`` (por id), em lotes — cada arquivo do
        # zip puxa um next() desta fonte, alinhado 1-a-1 com ``meta``.
        for i in range(0, len(ids), _LOTE_STREAM_CHUNK):
            lote = ids[i:i + _LOTE_STREAM_CHUNK]
            ph = ','.join(['%s'] * len(lote))
            porid = {r['id']: (r['xml_raw'] or '') for r in (execute_query(
                f"SELECT id, xml_raw FROM nfe_importacoes WHERE id IN ({ph})",
                tuple(lote), fetch=True) or [])}
            for _id in lote:
                yield porid.get(_id, '')
    cursor = fonte_xml()

    zs = ZipStream(compress_type=zipfile.ZIP_DEFLATED)
    for m in meta:
        def dados():
            xml = next(cursor)   # lido na hora de gravar ESTE arquivo (ordem = meta)
            if xml:
                yield xml.encode('utf-8')
        zs.add(data=dados(), arcname=(m['chave_acesso'] or str(m['id'])) + '.xml')

    resp = Response(zs, mimetype='application/zip')
    resp.headers['Content-Disposition'] = _cd_attachment_zip(nome_zip)
    return resp


# ------------------------------- NF-e (entradas / saídas) -------------------
def _lote_xml_nfe(escopo, permissao):
    if not current_user.has_permission(permissao):
        return jsonify({'error': 'Você não tem permissão para exportar estas notas.'}), 403
    data = request.get_json(silent=True) or {}
    ids = _ids_do_lote(data)
    where, params = _where_lote(escopo, data)
    # Só o que dá para exportar: resumo da SEFAZ e xml vazio não entram no zip.
    where = list(where) + ["n.incompleta = 0", "COALESCE(n.xml_raw,'') <> ''"]
    where_sql = 'WHERE ' + ' AND '.join(where)

    total = int((execute_query(
        f"SELECT COUNT(*) AS t FROM nfe_importacoes n {where_sql}",
        tuple(params), fetch=True, fetch_one=True) or {}).get('t', 0))
    if total == 0:
        return jsonify({'error': 'Nenhuma nota com XML disponível na seleção/filtro '
                                 '(resumos da SEFAZ não têm XML).'}), 404
    if total > _LOTE_MAX_XML:
        return jsonify({'error': f'{total} notas — o limite é {_LOTE_MAX_XML} por vez. '
                                 f'Refine o período ou marque as notas que quer.'}), 413

    # AUDITORIA (D2): exportação de arquivo (XML em lote). LEITURA — exportar não
    # altera dado; fica fora do histórico de alterações do cliente.
    registrar('leitura.exportou_arquivo', 'fiscal', tabela='nfe_importacoes',
              depois={'escopo': escopo, 'formato': 'xml', 'total': total,
                      'filtros': {**{k: v for k, v in data.items() if k != 'ids' and v},
                                  **rotulo_empresa(data.get('cliente_id'), data.get('grupo_id'))}})

    # Seleção da página (ids marcados) → poucos: zip em memória. "Tudo do filtro"
    # (sem ids) → pode ser dezenas de milhares: zip em STREAMING.
    if ids:
        rows = execute_query(
            f"SELECT n.id, n.chave_acesso, n.data_emissao, n.xml_raw FROM nfe_importacoes n {where_sql}",
            tuple(params), fetch=True) or []
        arquivos = [((r['chave_acesso'] or str(r['id'])) + '.xml',
                     (r['xml_raw'] or '').encode('utf-8')) for r in rows]
        return _zip_download(arquivos, _nome_zip_lote(
            data, [r['data_emissao'] for r in rows], _prefixo_lote(escopo, 'XML')))

    # Nome sai do MIN/MAX do filtro (no streaming não temos as linhas de antemão, e
    # o cabeçalho com o nome vai ANTES do corpo).
    per = execute_query(
        f"SELECT MIN(n.data_emissao) AS mn, MAX(n.data_emissao) AS mx "
        f"FROM nfe_importacoes n {where_sql}", tuple(params), fetch=True, fetch_one=True) or {}
    nome = _nome_zip_lote(data, [per.get('mn'), per.get('mx')], _prefixo_lote(escopo, 'XML'))
    return _stream_xml_lote_nfe(where_sql, params, nome)


def _lote_pdf_nfe(escopo, permissao):
    if not current_user.has_permission(permissao):
        return jsonify({'error': 'Você não tem permissão para exportar estas notas.'}), 403
    data = request.get_json(silent=True) or {}
    ids = _ids_do_lote(data)
    if not ids:
        return jsonify({'error': 'Marque as notas que quer em PDF '
                                 f'(no máximo {_LOTE_MAX_PDF}).'}), 400
    if len(ids) > _LOTE_MAX_PDF:
        return jsonify({'error': f'Máximo {_LOTE_MAX_PDF} PDFs por vez '
                                 f'(você marcou {len(ids)}).'}), 413

    where, params = _where_lote(escopo, data)

    # AUDITORIA (D2): exportação de arquivo (PDF em lote) por ação do usuário.
    registrar('leitura.exportou_arquivo', 'fiscal', tabela='nfe_importacoes',
              depois={'escopo': escopo, 'formato': 'pdf', 'marcadas': len(ids),
                      'filtros': {**{k: v for k, v in data.items() if k != 'ids' and v},
                                  **rotulo_empresa(data.get('cliente_id'), data.get('grupo_id'))}})

    where = list(where) + ["n.incompleta = 0", "COALESCE(n.xml_raw,'') <> ''"]
    rows = execute_query(
        "SELECT n.id, n.chave_acesso, n.data_emissao, n.xml_raw FROM nfe_importacoes n "
        "WHERE " + ' AND '.join(where), tuple(params), fetch=True) or []

    arquivos, ignorados = [], 0
    for r in rows:
        chave = r['chave_acesso'] or ''
        modelo = chave[20:22] if len(chave) >= 22 else ''
        try:
            pdf = _gerar_pdf_documento(r['xml_raw'], modelo)
        except Exception:
            logging.getLogger(__name__).exception(
                '[export-lote] falha no PDF da nfe_id=%s', r['id'])
            pdf = None
        if pdf:
            arquivos.append(((chave or str(r['id'])) + '.pdf', pdf))
        else:
            ignorados += 1
    if not arquivos:
        return jsonify({'error': 'Nenhuma das notas marcadas gerou PDF (o XML pode estar '
                                 'fora do padrão esperado). Baixe o XML por enquanto.'}), 404
    return _zip_download(arquivos, _nome_zip_lote(
        data, [r['data_emissao'] for r in rows], _prefixo_lote(escopo, 'PDF')))


@escrita_fiscal.route('/conf-compras/export/xml-lote', methods=['POST'])
@login_required
def lote_xml_compras():
    return _lote_xml_nfe('entrada', 'escrita_fiscal.conf_compras')


@escrita_fiscal.route('/conf-compras/export/pdf-lote', methods=['POST'])
@login_required
def lote_pdf_compras():
    return _lote_pdf_nfe('entrada', 'escrita_fiscal.conf_compras')


@escrita_fiscal.route('/conf-saidas/export/xml-lote', methods=['POST'])
@login_required
def lote_xml_saidas():
    return _lote_xml_nfe('saida', 'escrita_fiscal.conf_saidas')


@escrita_fiscal.route('/conf-saidas/export/pdf-lote', methods=['POST'])
@login_required
def lote_pdf_saidas():
    return _lote_pdf_nfe('saida', 'escrita_fiscal.conf_saidas')


# ------------------------------------ CT-e ----------------------------------
def _xmls_cte(rows):
    """Resolve o XML de cada CT-e: banco quando houver, senão Dropbox.

    Os downloads vão em paralelo porque cada um é uma chamada de rede — em
    série, algumas dezenas já estourariam o tempo da requisição. Quem falhar
    fica de fora do zip em vez de derrubar o lote inteiro."""
    pendentes = [r for r in rows if not (r.get('xml_raw') or '').strip()]
    baixados = {}
    if pendentes:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futuros = {pool.submit(dropbox_sync.download_xml, r['xml_caminho']): r['id']
                       for r in pendentes if (r.get('xml_caminho') or '').strip()}
            for fut in as_completed(futuros):
                cte_id = futuros[fut]
                try:
                    baixados[cte_id] = fut.result()
                except Exception:
                    logging.getLogger(__name__).warning(
                        '[export-lote] falha ao baixar XML do cte_id=%s', cte_id)
    saida = []
    for r in rows:
        xml = (r.get('xml_raw') or '').strip() or baixados.get(r['id'])
        if xml:
            saida.append((r, xml))
    return saida


@escrita_fiscal.route('/conf-cte/export/xml-lote', methods=['POST'])
@login_required
def lote_xml_cte():
    if not current_user.has_permission('escrita_fiscal.conf_cte'):
        return jsonify({'error': 'Você não tem permissão para exportar estes CT-e.'}), 403
    data = request.get_json(silent=True) or {}
    where, params = _where_lote('cte', data)
    where = list(where) + ["(COALESCE(t.xml_raw,'') <> '' OR COALESCE(t.xml_caminho,'') <> '')"]
    where_sql = 'WHERE ' + ' AND '.join(where)

    total = int((execute_query(
        f"SELECT COUNT(*) AS t FROM cte_documentos t {where_sql}",
        tuple(params), fetch=True, fetch_one=True) or {}).get('t', 0))
    if total == 0:
        return jsonify({'error': 'Nenhum CT-e com XML disponível na seleção/filtro.'}), 404
    if total > _LOTE_MAX_XML_CTE:
        return jsonify({'error': f'{total} CT-e — o limite é {_LOTE_MAX_XML_CTE} por vez '
                                 f'(cada XML é baixado do Dropbox). Refine o período '
                                 f'ou marque os que quer.'}), 413

    # AUDITORIA (D2): exportação de arquivo (CT-e XML em lote) por ação do usuário.
    registrar('leitura.exportou_arquivo', 'fiscal', tabela='cte_documentos',
              depois={'escopo': 'cte', 'formato': 'xml', 'total': total,
                      'filtros': {**{k: v for k, v in data.items() if k != 'ids' and v},
                                  **rotulo_empresa(data.get('cliente_id'), data.get('grupo_id'))}})

    rows = execute_query(
        f"SELECT t.id, t.chave_acesso, t.data_emissao, t.xml_raw, t.xml_caminho "
        f"FROM cte_documentos t {where_sql}", tuple(params), fetch=True) or []
    arquivos = [((r['chave_acesso'] or str(r['id'])) + '.xml', xml.encode('utf-8'))
                for r, xml in _xmls_cte(rows)]
    if not arquivos:
        return jsonify({'error': 'Não foi possível ler os XMLs no Dropbox agora. '
                                 'Tente de novo em instantes.'}), 502
    return _zip_download(arquivos, _nome_zip_lote(
        data, [r['data_emissao'] for r in rows], _prefixo_lote('cte', 'XML')))


@escrita_fiscal.route('/conf-cte/export/pdf-lote', methods=['POST'])
@login_required
def lote_pdf_cte():
    if not current_user.has_permission('escrita_fiscal.conf_cte'):
        return jsonify({'error': 'Você não tem permissão para exportar estes CT-e.'}), 403
    data = request.get_json(silent=True) or {}
    ids = _ids_do_lote(data)
    if not ids:
        return jsonify({'error': 'Marque os CT-e que quer em PDF '
                                 f'(no máximo {_LOTE_MAX_PDF}).'}), 400
    if len(ids) > _LOTE_MAX_PDF:
        return jsonify({'error': f'Máximo {_LOTE_MAX_PDF} PDFs por vez '
                                 f'(você marcou {len(ids)}).'}), 413

    # AUDITORIA (D2): exportação de arquivo (CT-e PDF em lote) por ação do usuário.
    registrar('leitura.exportou_arquivo', 'fiscal', tabela='cte_documentos',
              depois={'escopo': 'cte', 'formato': 'pdf', 'marcadas': len(ids),
                      'filtros': {**{k: v for k, v in data.items() if k != 'ids' and v},
                                  **rotulo_empresa(data.get('cliente_id'), data.get('grupo_id'))}})

    where, params = _where_lote('cte', data)
    where = list(where) + ["t.modelo = '57'"]
    rows = execute_query(
        "SELECT t.id, t.chave_acesso, t.modelo, t.data_emissao, t.xml_raw, t.xml_caminho "
        "FROM cte_documentos t WHERE " + ' AND '.join(where),
        tuple(params), fetch=True) or []

    arquivos = []
    for r, xml in _xmls_cte(rows):
        try:
            pdf = _gerar_pdf_documento(xml, '57')
        except Exception:
            logging.getLogger(__name__).exception(
                '[export-lote] falha no DACTE do cte_id=%s', r['id'])
            pdf = None
        if pdf:
            arquivos.append(((r['chave_acesso'] or str(r['id'])) + '.pdf', pdf))
    if not arquivos:
        return jsonify({'error': 'Nenhum dos CT-e marcados gera PDF (só o modelo 57 tem '
                                 'DACTE).'}), 404
    return _zip_download(arquivos, _nome_zip_lote(
        data, [r['data_emissao'] for r in rows], _prefixo_lote('cte', 'PDF')))


# ---------------------------------------------------------------------------
# Relatório (PDF + Excel) — Entradas / Saídas / CT-e. Monta o ctx a partir do
# MESMO filtro da listagem (não da seleção). KPIs sempre sobre o filtro inteiro.
# Excel leva o filtro inteiro (teto _LOTE_MAX_XML); PDF lista até _REL_MAX_PDF e,
# acima disso, sai só com cabeçalho+KPIs+totais + aviso (PDF de 12k páginas não
# serve). Colunas 'R': número no Excel (soma por fórmula), string BRL no PDF.
# ---------------------------------------------------------------------------
_REL_MAX_PDF = 5000
_REL_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'static', 'images', 'logo.png')


def _rel_brl(v):
    return 'R$ ' + f'{float(v or 0):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _rel_int(v):
    return f'{int(v or 0):,}'.replace(',', '.')


def _rel_data(d):
    return d.strftime('%d/%m/%Y') if hasattr(d, 'strftime') else str(d or '')


# ---------------------------------------------------------------------------
# Filtro SITUAÇÃO — contrato ÚNICO das três telas
#
# Parâmetro 'cancelado' na querystring (ou no corpo do lote), com os MESMOS três
# valores que a conf-cte já usava: '' = Todas (default), '0' = Autorizadas,
# '1' = Canceladas. O nome segue 'cancelado' mesmo nas notas, onde a coluna
# chama-se 'cancelada': o contrato é do FRONT — um só select em três telas — e
# inventar 'cancelada' na querystring criaria justamente o segundo padrão que se
# quer evitar.
#
# A conf-cte foi migrada para cá também. A mesma regra estava escrita em três
# lugares; agora é uma função — o único jeito de garantir que as três telas
# continuem concordando quando alguém mexer.
def _clausula_cancelada(valor, alias='n', coluna='cancelada'):
    """Cláusula do filtro Situação, ou None quando é 'Todas'."""
    v = str(valor or '').strip()
    if v == '1':
        return f'{alias}.{coluna} = 1'
    if v == '0':
        return f'{alias}.{coluna} = 0'
    return None


def _aplica_cancelada(where, valor, alias='n', coluna='cancelada'):
    c = _clausula_cancelada(valor, alias, coluna)
    if c:
        where.append(c)


def _rel_filtro(escopo):
    """WHERE do relatório = o MESMO filtro da listagem (request.args). Replica as
    cláusulas de api_notas (entrada) / api_notas_saidas (saida) / api_ctes (cte) —
    é o filtro, não a seleção. Isolado de propósito: não toca nos endpoints da
    listagem que estão em produção."""
    a = request.args
    ci, gi = a.get('cliente_id', '').strip(), a.get('grupo_id', '').strip()

    def dt(w, p, al):
        if a.get('data_ini', '').strip(): w.append(f'{al}.data_emissao >= %s'); p.append(a.get('data_ini').strip())
        if a.get('data_fim', '').strip(): w.append(f'{al}.data_emissao <= %s'); p.append(a.get('data_fim').strip())

    def org(w, p, al):
        o = a.get('origem', '').strip()
        if o == 'SEFAZ': w.append(f"{al}.origem = 'SEFAZ'")
        elif o == 'MANUAL': w.append(f"{al}.origem IN ('UPLOAD','DROPBOX')")
        elif o: w.append(f'{al}.origem = %s'); p.append(o)

    def vlr(w, p, col):
        if a.get('vmin', '').strip(): w.append(f'{col} >= %s'); p.append(float(a.get('vmin')))
        if a.get('vmax', '').strip(): w.append(f'{col} <= %s'); p.append(float(a.get('vmax')))

    def vinc(w, p):
        vs = a.get('vinc_status', '').strip()
        base = "SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id"
        if vs == 'completo':
            w.append(f"NOT EXISTS ({base} AND i.produto_catalogo_id IS NULL) AND EXISTS ({base})")
        elif vs == 'parcial':
            w.append(f"EXISTS ({base} AND i.produto_catalogo_id IS NOT NULL) AND EXISTS ({base} AND i.produto_catalogo_id IS NULL)")
        elif vs == 'sem':
            w.append(f"NOT EXISTS ({base} AND i.produto_catalogo_id IS NOT NULL)")
        elif vs == 'incompleto':
            w.append(f"EXISTS ({base} AND i.produto_catalogo_id IS NULL)")

    if escopo == 'cte':
        w, p = _empresa_where_cte(ci, gi, alias='t', params=[])
        emit = _filtro_lista(a.get('emit_cnpj', ''))
        if emit: w.append(_clausula_in('t.emit_cnpj', emit, p))
        if a.get('tomador_cnpj', '').strip():
            w.append("REPLACE(REPLACE(REPLACE(t.tomador_cnpj,'.',''),'/',''),'-','') LIKE %s")
            p.append('%' + re.sub(r'\D', '', a.get('tomador_cnpj')) + '%')
        dt(w, p, 't')
        if a.get('chave', '').strip(): w.append('t.chave_acesso LIKE %s'); p.append('%' + a.get('chave').strip() + '%')
        if a.get('num_cte', '').strip(): w.append('t.num_cte = %s'); p.append(a.get('num_cte').strip())
        if a.get('modelo', '').strip(): w.append('t.modelo = %s'); p.append(a.get('modelo').strip())
        ui, uf = _filtro_lista(a.get('uf_ini', '')), _filtro_lista(a.get('uf_fim', ''))
        if ui: w.append(_clausula_in('t.uf_ini', ui, p))
        if uf: w.append(_clausula_in('t.uf_fim', uf, p))
        vlr(w, p, 't.valor_frete'); org(w, p, 't')
        _aplica_cancelada(w, a.get('cancelado'), 't', 'cancelado')
        if a.get('papel', '').strip(): w.append('t.papel_cliente = %s'); p.append(a.get('papel').strip())
        return 'WHERE ' + ' AND '.join(w), p

    if escopo == 'saida':
        w, p = _empresa_where_saidas(ci, gi, alias='n', params=[])
        w = ["n.tipo = 'saida'"] + w
        dest = _filtro_lista(a.get('dest_cnpj', ''))
        if dest: w.append(_clausula_in('n.dest_cnpj', dest, p))
        dt(w, p, 'n')
        if a.get('chave', '').strip(): w.append('n.chave_acesso LIKE %s'); p.append('%' + a.get('chave').strip() + '%')
        if a.get('num_nota', '').strip(): w.append('n.num_nota = %s'); p.append(a.get('num_nota').strip())
        if a.get('cfop', '').strip(): w.append('n.cfop LIKE %s'); p.append(a.get('cfop').strip() + '%')
        du = _filtro_lista(a.get('dest_uf', ''))
        if du: w.append(_clausula_in('n.dest_uf', du, p))
        if a.get('emit_cnpj', '').strip(): w.append('n.emit_cnpj LIKE %s'); p.append('%' + a.get('emit_cnpj').strip() + '%')
        vlr(w, p, 'n.valor_total'); org(w, p, 'n'); vinc(w, p)
        _aplica_cancelada(w, a.get('cancelado'), 'n')
        return 'WHERE ' + ' AND '.join(w), p

    # entrada
    w, p = _empresa_where(ci, gi, alias='n', params=[])
    w = ["n.tipo = 'entrada'"] + w
    emit = _filtro_lista(a.get('emit_cnpj', ''))
    if emit: w.append(_clausula_in('n.emit_cnpj', emit, p))
    dt(w, p, 'n')
    if a.get('chave', '').strip(): w.append('n.chave_acesso LIKE %s'); p.append('%' + a.get('chave').strip() + '%')
    if a.get('num_nota', '').strip(): w.append('n.num_nota = %s'); p.append(a.get('num_nota').strip())
    if a.get('cfop', '').strip(): w.append('n.cfop LIKE %s'); p.append(a.get('cfop').strip() + '%')
    eu = _filtro_lista(a.get('emit_uf', ''))
    if eu: w.append(_clausula_in('n.emit_uf', eu, p))
    if a.get('dest_cnpj', '').strip(): w.append('n.dest_cnpj LIKE %s'); p.append('%' + a.get('dest_cnpj').strip() + '%')
    vlr(w, p, 'n.valor_total'); org(w, p, 'n'); vinc(w, p)
    _aplica_cancelada(w, a.get('cancelado'), 'n')
    return 'WHERE ' + ' AND '.join(w), p


def _rel_empresa():
    a = request.args
    ci, gi = a.get('cliente_id', '').strip(), a.get('grupo_id', '').strip()
    if ci.isdigit():
        r = execute_query("SELECT numero_cliente, nome_razao_social FROM clientes WHERE id = %s",
                          (int(ci),), fetch=True, fetch_one=True) or {}
        num, raz = str(r.get('numero_cliente') or '').strip(), str(r.get('nome_razao_social') or '').strip()
        return f'{num} - {raz}' if num and raz else (raz or num or '—')
    if gi.isdigit():
        r = execute_query("SELECT nome FROM grupos_clientes WHERE id = %s",
                          (int(gi),), fetch=True, fetch_one=True) or {}
        return f"Grupo: {r.get('nome') or '—'}"
    return '—'


def _rel_periodo():
    a = request.args
    def br(s):
        try:
            return datetime.strptime(str(s)[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
        except (ValueError, TypeError):
            return None
    di, df = br(a.get('data_ini', '')), br(a.get('data_fim', ''))
    if di and df:
        return di if di == df else f'{di} a {df}'
    return di or df or 'Todo o período'


def _rel_nome(escopo, ext):
    data = {'cliente_id': request.args.get('cliente_id', ''), 'grupo_id': request.args.get('grupo_id', '')}
    datas = [d for d in (request.args.get('data_ini', '').strip(),
                         request.args.get('data_fim', '').strip()) if d]
    base = _nome_zip_lote(data, datas, _prefixo_lote(escopo, 'RELATORIO'))  # termina em .zip
    return base[:-4] + '.' + ext


def _rel_linhas(escopo, formato, where_sql, params, limite):
    """Linhas do relatório (até ``limite``). Colunas 'R' = string BRL no PDF, número
    no Excel (pra somar). Contagens (Itens/NF-e) sempre string (não é moeda)."""
    pdf = (formato == 'pdf')
    if escopo == 'cte':
        rows = execute_query(
            "SELECT t.data_emissao, t.num_cte, t.serie, t.emit_nome, t.emit_cnpj, "
            "t.uf_ini, t.uf_fim, t.cfop, t.valor_frete, t.valor_icms, "
            "(SELECT COUNT(*) FROM cte_nfe cn WHERE cn.cte_id = t.id) AS qtd_nfes "
            f"FROM cte_documentos t {where_sql} ORDER BY t.data_emissao DESC, t.id DESC LIMIT %s",
            tuple(params) + (limite,), fetch=True) or []
        out = []
        for r in rows:
            fre, icm = r['valor_frete'] or 0, r['valor_icms'] or 0
            out.append([
                _rel_data(r['data_emissao']),
                f"{r['num_cte'] or '—'}/{r['serie'] or '—'}",
                r['emit_nome'] or r['emit_cnpj'] or '—',
                f"{r['uf_ini'] or '—'} → {r['uf_fim'] or '—'}",
                r['cfop'] or '—', str(int(r['qtd_nfes'] or 0)),
                _rel_brl(fre) if pdf else float(fre),
                _rel_brl(icm) if pdf else float(icm),
            ])
        return out

    hora = (", CASE WHEN LOCATE('<dhEmi>', n.xml_raw) > 0 THEN "
            "SUBSTRING(SUBSTRING_INDEX(SUBSTRING_INDEX(n.xml_raw,'<dhEmi>',-1),'</dhEmi>',1),12,8) "
            "END AS hora") if escopo == 'saida' else ""
    partes = ("n.dest_nome AS nome, n.dest_cnpj AS doc, n.dest_uf AS uf"
              if escopo == 'saida' else "n.emit_nome AS nome, n.emit_cnpj AS doc, n.emit_uf AS uf")
    rows = execute_query(
        f"SELECT n.id, n.data_emissao, n.num_nota, n.serie, {partes}, n.cfop, "
        f"n.valor_total, n.valor_icms, n.valor_pis, n.valor_cofins, "
        f"COALESCE(ic.qtd, 0) AS qtd_itens{hora} FROM nfe_importacoes n "
        f"LEFT JOIN (SELECT nfe_id, COUNT(*) AS qtd FROM nfe_itens GROUP BY nfe_id) ic ON ic.nfe_id = n.id "
        f"{where_sql} ORDER BY n.data_emissao DESC, n.id DESC LIMIT %s",
        tuple(params) + (limite,), fetch=True) or []
    out = []
    for r in rows:
        data = _rel_data(r['data_emissao'])
        if escopo == 'saida' and r.get('hora'):
            data += ' ' + str(r['hora'])[:5]
        v, ic, pi, co = (r['valor_total'] or 0, r['valor_icms'] or 0,
                         r['valor_pis'] or 0, r['valor_cofins'] or 0)
        out.append([
            data, f"{r['num_nota'] or '—'}/{r['serie'] or '—'}",
            r['nome'] or r['doc'] or '—', r['uf'] or '—', r['cfop'] or '—',
            str(int(r['qtd_itens'] or 0)),
            _rel_brl(v) if pdf else float(v), _rel_brl(ic) if pdf else float(ic),
            _rel_brl(pi) if pdf else float(pi), _rel_brl(co) if pdf else float(co),
        ])
    return out


def _exportar_relatorio(escopo, permissao, titulo):
    from utils.relatorio import gerar_relatorio_pdf, gerar_relatorio_xlsx
    if not current_user.has_permission(permissao):
        return jsonify({'error': 'Você não tem permissão para exportar este relatório.'}), 403
    formato = (request.args.get('formato', 'pdf') or 'pdf').lower()
    if formato not in ('pdf', 'xlsx'):
        return jsonify({'error': 'Formato inválido (use pdf ou xlsx).'}), 400

    where_sql, params = _rel_filtro(escopo)

    # AUDITORIA (D2): exportação de RELATÓRIO (PDF/XLSX) por ação do usuário.
    registrar('leitura.exportou_arquivo', 'fiscal',
              tabela=('cte_documentos' if escopo == 'cte' else 'nfe_importacoes'),
              depois={'escopo': escopo, 'formato': formato, 'relatorio': titulo,
                      **rotulo_empresa(request.args.get('cliente_id'),
                                       request.args.get('grupo_id'))})

    # KPIs + total sobre o FILTRO INTEIRO (independe do teto do PDF).
    if escopo == 'cte':
        agg = execute_query(
            "SELECT COUNT(*) AS tot, COALESCE(SUM(t.valor_frete),0) AS frete, "
            "COALESCE(SUM(t.valor_icms),0) AS icms, COALESCE(SUM(t.cancelado),0) AS canc, "
            "COALESCE(SUM((SELECT COUNT(*) FROM cte_nfe cn WHERE cn.cte_id = t.id)),0) AS nfes "
            f"FROM cte_documentos t {where_sql}", tuple(params), fetch=True, fetch_one=True) or {}
        total = int(agg.get('tot') or 0)
        kpis = [('Total de CT-e', _rel_int(total)), ('Valor (Frete)', _rel_brl(agg.get('frete'))),
                ('ICMS', _rel_brl(agg.get('icms'))), ('Cancelados', _rel_int(agg.get('canc'))),
                ('NF-e transportadas', _rel_int(agg.get('nfes')))]
        colunas = [('Data', 'L'), ('Nº/Série', 'L'), ('Transportadora', 'L'), ('Trajeto', 'L'),
                   ('CFOP', 'L'), ('NF-e', 'L'), ('Frete R$', 'R'), ('ICMS', 'R')]
        totais = ['', '', 'TOTAL', '', '', '', _rel_brl(agg.get('frete')), _rel_brl(agg.get('icms'))]
    else:
        agg = execute_query(
            "SELECT COUNT(*) AS tot, COALESCE(SUM(n.valor_total),0) AS v, "
            "COALESCE(SUM(n.valor_icms),0) AS icms, COALESCE(SUM(n.valor_pis),0) AS pis, "
            "COALESCE(SUM(n.valor_cofins),0) AS cofins "
            f"FROM nfe_importacoes n {where_sql}", tuple(params), fetch=True, fetch_one=True) or {}
        total = int(agg.get('tot') or 0)
        kpis = [('Total de Notas', _rel_int(total)), ('Valor Total', _rel_brl(agg.get('v'))),
                ('ICMS', _rel_brl(agg.get('icms'))), ('PIS', _rel_brl(agg.get('pis'))),
                ('COFINS', _rel_brl(agg.get('cofins')))]
        parte = 'Destinatário' if escopo == 'saida' else 'Emitente (Fornecedor)'
        cabD = ('Data / Hora', 'L') if escopo == 'saida' else ('Data', 'L')
        colunas = [cabD, ('Nº/Série', 'L'), (parte, 'L'), ('UF', 'L'), ('CFOP', 'L'),
                   ('Itens', 'L'), ('Valor R$', 'R'), ('ICMS', 'R'), ('PIS', 'R'), ('COFINS', 'R')]
        totais = ['', 'TOTAL', '', '', '', '', _rel_brl(agg.get('v')), _rel_brl(agg.get('icms')),
                  _rel_brl(agg.get('pis')), _rel_brl(agg.get('cofins'))]

    if total == 0:
        return jsonify({'error': 'Nada no filtro atual para o relatório.'}), 404
    if formato == 'xlsx' and total > _LOTE_MAX_XML:
        return jsonify({'error': f'{total} registros — o limite do Excel é {_LOTE_MAX_XML}. '
                                 f'Refine o filtro.'}), 413

    aviso, linhas = None, []
    if formato == 'pdf' and total > _REL_MAX_PDF:
        aviso = (f'Listagem completa no Excel — são {_rel_int(total)} registros '
                 f'(o PDF lista até {_rel_int(_REL_MAX_PDF)}).')
    else:
        limite = _REL_MAX_PDF if formato == 'pdf' else _LOTE_MAX_XML
        linhas = _rel_linhas(escopo, formato, where_sql, params, limite)

    ctx = {
        'titulo': titulo, 'empresa': _rel_empresa(), 'periodo': _rel_periodo(),
        'gerado_em': datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y %H:%M'),
        'kpis': kpis, 'colunas': colunas, 'linhas': linhas, 'totais': totais,
        'logo_path': _REL_LOGO if os.path.exists(_REL_LOGO) else None, 'aviso': aviso,
    }
    if formato == 'xlsx':
        dados = gerar_relatorio_xlsx(ctx)
        mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else:
        dados = gerar_relatorio_pdf(ctx)
        mime = 'application/pdf'
    return send_file(BytesIO(dados), as_attachment=True,
                     download_name=_rel_nome(escopo, formato), mimetype=mime)


@escrita_fiscal.route('/conf-compras/export/relatorio')
@login_required
def relatorio_entradas():
    return _exportar_relatorio('entrada', 'escrita_fiscal.conf_compras', 'Relatório de Entradas')


@escrita_fiscal.route('/conf-saidas/export/relatorio')
@login_required
def relatorio_saidas():
    return _exportar_relatorio('saida', 'escrita_fiscal.conf_saidas', 'Relatório de Saídas')


@escrita_fiscal.route('/conf-cte/export/relatorio')
@login_required
def relatorio_cte():
    return _exportar_relatorio('cte', 'escrita_fiscal.conf_cte', 'Relatório de CT-e')


# ---------------------------------------------------------------------------
# API — itens de uma NF-e específica
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/itens/<int:nfe_id>')
@login_required
def api_itens(nfe_id):
    nota = execute_query(
        "SELECT id, tipo, emit_cnpj, emit_nome, num_nota, data_emissao, dest_nome, cliente_id, grupo_id "
        "FROM nfe_importacoes WHERE id = %s",
        (nfe_id,), fetch=True, fetch_one=True,
    )
    if not nota:
        return jsonify({'error': 'NF-e não encontrada'}), 404

    for k in ('data_emissao',):
        if nota.get(k) and hasattr(nota[k], 'isoformat'):
            nota[k] = nota[k].isoformat()

    itens = execute_query(
        """SELECT i.id, i.num_item, i.codigo_produto, i.descricao, i.ncm, i.cfop,
                  i.unidade, i.quantidade, i.valor_unitario, i.valor_total,
                  i.valor_icms, i.valor_pis, i.valor_cofins,
                  i.valor_unit_comercial,
                  i.produto_catalogo_id,
                  p.nome AS produto_catalogo_nome, p.categoria AS produto_categoria
             FROM nfe_itens i
             LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
            WHERE i.nfe_id = %s
            ORDER BY i.num_item""",
        (nfe_id,), fetch=True,
    ) or []

    # Auto-aplicar regras memorizadas nos itens ainda sem vínculo (batch), SÓ do
    # tipo desta nota (entrada não classifica saída e vice-versa).
    emit_cnpj = nota.get('emit_cnpj', '')
    cliente_id = nota.get('cliente_id')
    grupo_id = nota.get('grupo_id')
    tipo_nota = nota.get('tipo') or 'entrada'
    unlinked = [it for it in itens if it.get('produto_catalogo_id') is None and it.get('codigo_produto')]
    if unlinked:
        codigos = list({it['codigo_produto'] for it in unlinked})
        mapa = _auto_vincular_batch(emit_cnpj, codigos, cliente_id, grupo_id, tipo=tipo_nota)
        if mapa:
            # Collect unique pids to fetch names in one query
            pids = list(set(mapa.values()))
            placeholders_p = ','.join(['%s'] * len(pids))
            prod_rows = execute_query(
                f"SELECT id, nome, categoria FROM nfe_produtos_catalogo WHERE id IN ({placeholders_p})",
                tuple(pids), fetch=True,
            ) or []
            prod_map = {r['id']: r for r in prod_rows}
            # Collect updates to apply in one batch
            updates = [(mapa[it['codigo_produto']], it['id'])
                       for it in unlinked if it['codigo_produto'] in mapa]
            # Batch UPDATE: group items by product ID to minimize DB round-trips
            by_product: dict = defaultdict(list)
            for pid, item_id in updates:
                by_product[pid].append(item_id)
            for pid, ids in by_product.items():
                ph = ','.join(['%s'] * len(ids))
                execute_query(
                    f"UPDATE nfe_itens SET produto_catalogo_id = %s WHERE id IN ({ph})",
                    tuple([pid] + ids),
                )
            # Update in-memory objects
            for it in unlinked:
                pid = mapa.get(it['codigo_produto'])
                if pid:
                    prod = prod_map.get(pid)
                    it['produto_catalogo_id'] = pid
                    it['produto_catalogo_nome'] = prod['nome'] if prod else None
                    it['produto_categoria'] = prod['categoria'] if prod else None

    for it in itens:
        for k in ('quantidade', 'valor_unitario', 'valor_total',
                  'valor_icms', 'valor_pis', 'valor_cofins'):
            it[k] = float(it.get(k) or 0)
        # valor_unit_comercial: NULL = "ainda não calculado" (item anterior ao
        # backfill), NUNCA custo zero — preserva None para a tela mostrar "—".
        vc = it.get('valor_unit_comercial')
        it['valor_unit_comercial'] = float(vc) if vc is not None else None

    return jsonify({'nota': nota, 'itens': itens})


# ---------------------------------------------------------------------------
# API — sugestão de produto para vínculo automático
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/sugestao-produto')
@login_required
def api_sugestao_produto():
    emit_cnpj = request.args.get('emit_cnpj', '').strip()
    codigo_xml = request.args.get('codigo_produto', '').strip()
    cliente_id = request.args.get('cliente_id', '').strip()
    grupo_id = request.args.get('grupo_id', '').strip()
    # tipo escopa a sugestão (entrada=Compras / saida=Saídas). A tela de Saídas deve
    # passar tipo=saida; sem o parâmetro assume 'entrada' (compat. com Compras).
    tipo = request.args.get('tipo', 'entrada').strip() or 'entrada'

    if not emit_cnpj or not codigo_xml:
        return jsonify({'produto_id': None})

    # Procura vínculo registrado (empresa → grupo). Regras de ramo/global não são
    # mais resolvidas: memorização é por empresa (Fase 1).
    row = None
    for cli, grp in [
        (int(cliente_id) if cliente_id else None, None),
        (None, int(grupo_id) if grupo_id else None),
    ]:
        if cli is None and grp is None:
            continue
        cli_cond = '= %s' if cli is not None else 'IS NULL'
        grp_cond = '= %s' if grp is not None else 'IS NULL'
        query = (f"SELECT produto_catalogo_id FROM nfe_produto_vinculo "
                 f"WHERE emit_cnpj = %s AND codigo_produto_xml = %s AND tipo = %s "
                 f"AND cliente_id {cli_cond} AND grupo_id {grp_cond} LIMIT 1")
        bind = [emit_cnpj, codigo_xml, tipo]
        if cli is not None:
            bind.append(cli)
        if grp is not None:
            bind.append(grp)
        row = execute_query(query, tuple(bind), fetch=True, fetch_one=True)
        if row:
            break

    return jsonify({'produto_id': row['produto_catalogo_id'] if row else None})


# ---------------------------------------------------------------------------
# API — vincular item ao produto do catálogo
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/vincular-produto', methods=['POST'])
@login_required
def api_vincular_produto():
    data = request.get_json(force=True) or {}
    item_id = data.get('item_id')
    produto_id = data.get('produto_id')  # None = desvincular
    salvar_regra = bool(data.get('salvar_regra', True))

    if not item_id:
        return jsonify({'error': 'item_id obrigatório'}), 400

    # Busca o item para obter emit_cnpj e código
    item = execute_query(
        """SELECT i.id, i.nfe_id, i.codigo_produto, i.descricao,
                  n.emit_cnpj, n.cliente_id, n.grupo_id, n.tipo
             FROM nfe_itens i JOIN nfe_importacoes n ON n.id = i.nfe_id
            WHERE i.id = %s""",
        (item_id,), fetch=True, fetch_one=True,
    )
    if not item:
        return jsonify({'error': 'Item não encontrado'}), 404

    cli = item.get('cliente_id')
    tipo_nota = item.get('tipo') or 'entrada'

    # Memorizar exige empresa: sem ela a regra não teria escopo e virava global.
    # Validado ANTES de qualquer UPDATE para não aplicar o pedido pela metade.
    if salvar_regra and produto_id and not cli:
        return jsonify({
            'error': 'NF-e sem empresa definida — não é possível memorizar. '
                     'Defina a empresa da nota ou desmarque "salvar regra".'
        }), 400

    # Atualiza o vínculo no item
    execute_query(
        "UPDATE nfe_itens SET produto_catalogo_id = %s WHERE id = %s",
        (produto_id, item_id),
    )

    # AUDITORIA (D2): vincular (produto_id) ou desvincular (produto_id None) produto.
    registrar('escrita.vinculou_produto' if produto_id else 'escrita.desvinculou_produto',
              'fiscal', tabela='nfe_itens', registro_id=item_id,
              depois={'item_id': item_id, 'nfe_id': item.get('nfe_id'), 'produto_id': produto_id,
                      'emit_cnpj': item.get('emit_cnpj'), 'codigo': item.get('codigo_produto'),
                      'salvar_regra': salvar_regra, 'tipo': tipo_nota,
                      'cliente_id': item.get('cliente_id'),
                      **rotulo_empresa(item.get('cliente_id'))})

    # Salva regra de auto-vínculo e aplica retroativamente nos itens históricos
    # DESTA MESMA EMPRESA.
    if salvar_regra and produto_id:
        emit_cnpj = item['emit_cnpj']
        cod = item['codigo_produto']
        # Descrição do produto conforme XML
        descricao_xml = item.get('descricao') or ''
        # Regra no escopo da empresa + TIPO da nota (entrada x saída, independentes)
        _upsert_vinculo(cli, emit_cnpj, cod, descricao_xml, produto_id, tipo=tipo_nota)

        # Aplica retroativamente nos itens históricos do mesmo emit_cnpj +
        # codigo_produto que ainda estão sem vínculo, restrito à empresa E AO TIPO
        # da regra (exceto o item atual, já atualizado acima).
        if emit_cnpj and cod:
            execute_query(
                """UPDATE nfe_itens i
                      JOIN nfe_importacoes n ON n.id = i.nfe_id
                   SET i.produto_catalogo_id = %s
                   WHERE i.produto_catalogo_id IS NULL
                     AND n.emit_cnpj = %s
                     AND n.cliente_id = %s
                     AND n.tipo = %s
                     AND i.codigo_produto = %s
                     AND i.id != %s""",
                (produto_id, emit_cnpj, cli, tipo_nota, cod, item_id),
            )

    # Nome do produto vinculado — returned so the caller can update the UI
    prod_nome = None
    if produto_id:
        p = execute_query(
            "SELECT nome, categoria FROM nfe_produtos_catalogo WHERE id = %s",
            (produto_id,), fetch=True, fetch_one=True,
        )
        if p:
            prod_nome = p['nome']

    return jsonify({'ok': True, 'produto_nome': prod_nome})


# ---------------------------------------------------------------------------
# API — por emissor
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/por-emissor')
@login_required
def api_por_emissor():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    f_data_ini = request.args.get('data_ini', '').strip()
    f_data_fim = request.args.get('data_fim', '').strip()
    f_cancelado  = request.args.get('cancelado', '').strip()

    where, params = ["n.tipo = 'entrada'"], []
    extra, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    _aplica_cancelada(where, f_cancelado, 'n')

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = execute_query(
        f"""SELECT n.emit_cnpj, n.emit_nome, n.emit_uf,
                   COUNT(*) AS qtd_notas,
                   SUM(n.valor_total) AS total_valor,
                   SUM(n.valor_icms) AS total_icms,
                   SUM(n.valor_pis) AS total_pis,
                   SUM(n.valor_cofins) AS total_cofins
              FROM nfe_importacoes n {where_sql}
             GROUP BY n.emit_cnpj, n.emit_nome, n.emit_uf
             ORDER BY total_valor DESC""",
        tuple(params), fetch=True,
    ) or []

    for r in rows:
        for k in ('total_valor', 'total_icms', 'total_pis', 'total_cofins'):
            r[k] = float(r.get(k) or 0)

    return jsonify(rows)


# ---------------------------------------------------------------------------
# API — por produto
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/por-produto')
@login_required
def api_por_produto():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    f_data_ini = request.args.get('data_ini', '').strip()
    f_data_fim = request.args.get('data_fim', '').strip()
    f_cancelado  = request.args.get('cancelado', '').strip()
    f_emit_cnpj = _filtro_lista(request.args.get('emit_cnpj', ''))
    f_ncm = request.args.get('ncm', '').strip()
    f_descricao = request.args.get('descricao', '').strip()

    where, params = ["n.tipo = 'entrada'"], []
    extra, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    _aplica_cancelada(where, f_cancelado, 'n')
    if f_emit_cnpj:
        where.append(_clausula_in('n.emit_cnpj', f_emit_cnpj, params))
    if f_ncm:
        where.append('i.ncm LIKE %s')
        params.append(f'{f_ncm}%')
    if f_descricao:
        where.append('i.descricao LIKE %s')
        params.append(f'%{f_descricao}%')

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = execute_query(
        f"""SELECT i.codigo_produto, i.descricao, i.ncm, i.cfop, i.unidade,
                   i.produto_catalogo_id,
                   p.nome AS produto_catalogo_nome, p.categoria AS produto_categoria,
                   COUNT(DISTINCT n.id) AS qtd_notas,
                   SUM(i.quantidade) AS total_qtd,
                   SUM(i.valor_total) AS total_valor,
                   SUM(i.valor_icms) AS total_icms,
                   SUM(i.valor_pis) AS total_pis,
                   SUM(i.valor_cofins) AS total_cofins
              FROM nfe_itens i
              JOIN nfe_importacoes n ON n.id = i.nfe_id
              LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
              {where_sql}
             GROUP BY i.codigo_produto, i.descricao, i.ncm, i.cfop, i.unidade,
                      i.produto_catalogo_id, p.nome, p.categoria
             ORDER BY total_valor DESC
             LIMIT 500""",
        tuple(params), fetch=True,
    ) or []

    for r in rows:
        for k in ('total_qtd', 'total_valor', 'total_icms', 'total_pis', 'total_cofins'):
            r[k] = float(r.get(k) or 0)

    return jsonify(rows)


# ---------------------------------------------------------------------------
# API — Resumo de produtos para o painel de totais (todos os registros do filtro)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/resumo-produtos')
@login_required
def api_resumo_produtos():
    """Retorna totais agregados por categoria → produto para todos os registros
    que correspondam ao filtro atual (sem paginação).  Inclui também os itens
    ainda sem vínculo agrupados numa categoria especial."""
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id   = request.args.get('grupo_id', '').strip()
    f_data_ini   = request.args.get('data_ini', '').strip()
    f_data_fim   = request.args.get('data_fim', '').strip()
    f_emit_cnpj  = _filtro_lista(request.args.get('emit_cnpj', ''))
    f_emit_uf    = _filtro_lista(request.args.get('emit_uf', ''))

    where, params = ["n.tipo = 'entrada'"], []
    extra, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    if f_emit_cnpj:
        where.append(_clausula_in('n.emit_cnpj', f_emit_cnpj, params))
    if f_emit_uf:
        where.append(_clausula_in('n.emit_uf', f_emit_uf, params))

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    # Agrega por produto_catalogo_id (vinculados) ou por código/descrição (sem vínculo)
    rows = execute_query(
        f"""SELECT
               p.categoria                          AS categoria,
               p.subcategoria                       AS subcategoria,
               p.id                                 AS produto_id,
               p.nome                               AS produto_nome,
               p.unidade                            AS produto_unidade,
               COALESCE(SUM(i.quantidade), 0)       AS total_qtd,
               COALESCE(SUM(i.valor_total), 0)      AS total_valor,
               COALESCE(SUM(i.valor_icms), 0)       AS total_icms,
               COUNT(DISTINCT n.id)                 AS qtd_notas
           FROM nfe_itens i
           JOIN nfe_importacoes n ON n.id = i.nfe_id
           JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
           {where_sql}
           GROUP BY p.categoria, p.subcategoria, p.id, p.nome, p.unidade
           ORDER BY p.categoria, p.subcategoria, total_valor DESC""",
        tuple(params), fetch=True,
    ) or []

    # Itens sem vínculo — agrupados pelo descrição normalizada do XML
    unlinked = execute_query(
        f"""SELECT
               i.descricao                          AS produto_nome,
               i.unidade                            AS produto_unidade,
               COALESCE(SUM(i.quantidade), 0)       AS total_qtd,
               COALESCE(SUM(i.valor_total), 0)      AS total_valor,
               COALESCE(SUM(i.valor_icms), 0)       AS total_icms,
               COUNT(DISTINCT n.id)                 AS qtd_notas
           FROM nfe_itens i
           JOIN nfe_importacoes n ON n.id = i.nfe_id
           {where_sql}
               {'AND' if where_sql else 'WHERE'} i.produto_catalogo_id IS NULL
           GROUP BY i.descricao, i.unidade
           ORDER BY total_valor DESC
           LIMIT 200""",
        tuple(params), fetch=True,
    ) or []

    # Converte decimais
    for r in rows:
        for k in ('total_qtd', 'total_valor', 'total_icms'):
            r[k] = float(r.get(k) or 0)

    for r in unlinked:
        for k in ('total_qtd', 'total_valor', 'total_icms'):
            r[k] = float(r.get(k) or 0)
        r['categoria']       = '— Sem vínculo —'
        r['subcategoria']    = None
        r['produto_id']      = None

    # Monta estrutura hierárquica: { categoria: { subcategoria: [produtos] } }
    from collections import OrderedDict
    cats = OrderedDict()

    def _add(cat, subcat, row):
        if cat not in cats:
            cats[cat] = {'total_valor': 0, 'total_qtd': 0, 'total_icms': 0, 'qtd_notas': 0, 'subcats': OrderedDict()}
        c = cats[cat]
        c['total_valor'] += row['total_valor']
        c['total_icms']  += row['total_icms']
        c['qtd_notas']   += row.get('qtd_notas', 0)
        sub_key = subcat or ''
        if sub_key not in c['subcats']:
            c['subcats'][sub_key] = {'total_valor': 0, 'total_qtd': 0, 'total_icms': 0, 'produtos': []}
        s = c['subcats'][sub_key]
        s['total_valor'] += row['total_valor']
        s['total_icms']  += row['total_icms']
        s['produtos'].append({
            'id':       row.get('produto_id'),
            'nome':     row.get('produto_nome') or '—',
            'unidade':  row.get('produto_unidade') or '',
            'total_qtd':   row['total_qtd'],
            'total_valor': row['total_valor'],
            'total_icms':  row['total_icms'],
            'qtd_notas':   row.get('qtd_notas', 0),
        })

    for r in rows:
        _add(r.get('categoria') or '— Sem categoria —', r.get('subcategoria'), r)
    for r in unlinked:
        _add(r['categoria'], r.get('subcategoria'), r)

    # Serializa preservando ordem
    result = []
    for cat_nome, cat_data in cats.items():
        subcats_list = []
        for sub_nome, sub_data in cat_data['subcats'].items():
            subcats_list.append({
                'nome':        sub_nome,
                'total_valor': round(sub_data['total_valor'], 2),
                'total_icms':  round(sub_data['total_icms'],  2),
                'produtos':    sub_data['produtos'],
            })
        result.append({
            'categoria':   cat_nome,
            'total_valor': round(cat_data['total_valor'], 2),
            'total_icms':  round(cat_data['total_icms'],  2),
            'qtd_notas':   cat_data['qtd_notas'],
            'subcats':     subcats_list,
        })

    return jsonify(result)


# ---------------------------------------------------------------------------
# Upload manual — roteamento por MODELO
#
# A Importação Manual da tela inicial é o ÚNICO ponto de upload do sistema:
# NF-e/NFC-e (55/65) e CT-e (57) entram no MESMO lote. O modelo é decidido
# ANTES de qualquer parse (dígitos 21-22 da chave do Id=, ou a raiz do XML
# quando não há Id) — era daí que vinha o "Nó não encontrado no XML": um CT-e
# caindo no parser de NF-e.
#
# CT-e: resolve empresa e papel pelo próprio XML (papel_do_cliente contra a
# base — emitente cliente → SAÍDA), grava com origem='UPLOAD' e dedup por
# (chave, cliente_id). É o mesmo core que a rota /conf-cte/importar usa.
# ---------------------------------------------------------------------------
# _CTE_PARTES, _MODELOS_CTE, _RE_CHAVE_ID, _RE_RAIZ_XML, _RAIZES_CTE,
# _modelo_do_xml, _e_cte e _importar_um_cte foram movidos para
# utils/fiscal_ingest.py (reexportados no topo deste arquivo), para que o
# cron_roteador.py possa LANÇAR o XML antes de arquivar sem importar Flask.


def _expandir_uploads(files, errors):
    """Desempacota os arquivos enviados em [(nome, bytes)]: XMLs soltos e os de
    dentro de cada .zip (o upload de CT-e sempre veio em zip). O que não for XML
    nem ZIP vira erro. Devolve a lista e quantos arquivos foram recusados."""
    entradas, recusados = [], 0
    for f in files:
        nome = f.filename or ''
        low = nome.lower()
        if low.endswith('.zip'):
            try:
                with zipfile.ZipFile(BytesIO(f.read())) as z:
                    membros = [n for n in z.namelist() if n.lower().endswith('.xml')]
                    for n in membros:
                        entradas.append((n.rsplit('/', 1)[-1], z.read(n)))
                if not membros:
                    recusados += 1
                    errors.append(f'{nome}: zip sem XML dentro.')
            except zipfile.BadZipFile:
                recusados += 1
                errors.append(f'{nome}: zip inválido/corrompido.')
        elif low.endswith('.xml'):
            entradas.append((nome, f.read()))
        else:
            recusados += 1
            errors.append(f'{nome}: não é XML nem ZIP.')
    return entradas, recusados


# ---------------------------------------------------------------------------
# Importar XML — upload manual (NF-e/NFC-e + CT-e no mesmo lote)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/importar', methods=['POST'])
@login_required
def importar_xml():
    files = request.files.getlist('xml_files')
    cliente_id = request.form.get('cliente_id', '').strip() or None
    grupo_id = request.form.get('grupo_id', '').strip() or None

    if not files or all(f.filename == '' for f in files):
        flash('Nenhum arquivo selecionado.', 'warning')
        return redirect(url_for('escrita_fiscal.conf_compras'))

    ok, dup = 0, 0
    errors = []

    _upload_cache = _build_cliente_doc_cache()
    # err já começa contando o que nem chegou a ser XML (zip ruim, extensão errada)
    entradas, err = _expandir_uploads(files, errors)

    for nome, raw in entradas:
        try:
            content = raw.decode('utf-8', errors='replace')

            # Roteia POR MODELO antes do parse: CT-e vai para o core do conf-cte
            # (empresa e papel saem do próprio XML); 55/65 segue intocado abaixo.
            if _e_cte(content):
                res, motivo = _importar_um_cte(nome, content, _upload_cache)
                if motivo:
                    err += 1
                    errors.append(motivo)
                elif res == 'ok':
                    ok += 1
                else:
                    dup += 1
                continue

            parsed = parse_nfe_xml(content)

            dest_cli = int(cliente_id) if cliente_id else None
            grp_id = int(grupo_id) if grupo_id else None

            # Detecta cliente emitente para gerar registro de saída
            emit_digits = re.sub(r'\D', '', parsed['header'].get('emit_cnpj', ''))
            emit_cli = None
            if len(emit_digits) >= 11:
                _ef = _find_cliente_by_doc_digits(emit_digits, _upload_cache)
                if _ef and _ef['id'] != dest_cli:
                    emit_cli = _ef['id']

            if dest_cli is None and emit_cli is None:
                # Sem empresa selecionada e nenhum CNPJ reconhecido — salva sem vínculo
                result = _save_nfe(parsed, nome, 'UPLOAD', content,
                                   grupo_id=grp_id, tipo='entrada')
            else:
                result = _save_nfe_dual(parsed, nome, 'UPLOAD', content,
                                        dest_cli=dest_cli, emit_cli=emit_cli,
                                        grupo_id=grp_id)
            if result == 'dup':
                dup += 1
            else:
                ok += 1
        except ValueError as exc:
            err += 1
            errors.append(f'{nome}: {exc}')
        except Exception as exc:
            err += 1
            errors.append(f'{nome}: erro inesperado — {exc}')

    # AUDITORIA (D2): importação MANUAL de XML por um usuário logado (a importação
    # AUTOMÁTICA do robô/scheduler não passa por esta rota e não loga).
    registrar('escrita.importou_manual', 'fiscal', tabela='nfe_importacoes',
              depois={'arquivos': len(entradas), 'ok': ok, 'duplicados': dup, 'erros': err,
                      'cliente_id': cliente_id, 'grupo_id': grupo_id,
                      **rotulo_empresa(cliente_id, grupo_id)})

    # Resposta JSON para chamadas AJAX (modal de Importação Manual)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': ok, 'dup': dup, 'err': err, 'errors': errors[:10]})

    msgs = []
    if ok:
        msgs.append(f'{ok} documento(s) importado(s) com sucesso.')
    if dup:
        msgs.append(f'{dup} documento(s) já existiam (duplicados, ignorados).')
    if err:
        msgs.append(f'{err} arquivo(s) com erro.')
    flash(' '.join(msgs) or 'Nenhum documento processado.', 'success' if ok else 'warning')
    for e in errors[:5]:
        flash(e, 'danger')

    return redirect(url_for('escrita_fiscal.conf_compras'))


# ---------------------------------------------------------------------------
# Upload manual SÓ de CT-e — fecha as SAÍDAS de CT-e (cliente = transportadora/
# emitente), que a CTeDistribuicaoDFe não devolve. A tela do conf-cte não tem
# mais botão de importar (a Importação Manual da home faz os dois tipos); a rota
# fica de pé como API do mesmo core (_importar_um_cte).
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-cte/importar', methods=['POST'])
@login_required
def importar_cte_xml():
    """Importa CT-e de XMLs soltos e/ou de um .zip. Grava com origem='UPLOAD' e o
    XML em ``xml_raw`` (o olhinho/PDF/lote do CT-e já preferem xml_raw — ver
    _carregar_cte_export). Dedup por (chave, cliente_id): reimportar não duplica
    (atualiza). Devolve importados / pulados (já existiam) / rejeitados."""
    if not current_user.has_permission('escrita_fiscal.conf_cte'):
        return jsonify({'error': 'Você não tem permissão para importar CT-e.'}), 403
    files = request.files.getlist('xml_files')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400

    # Coleta os XMLs: soltos e os de dentro de cada .zip (o Anderson manda um zip).
    detalhes = []
    xmls, rejeitados = _expandir_uploads(files, detalhes)

    cache = _build_cliente_doc_cache()
    importados = pulados = 0
    for nome, raw in xmls:
        res, motivo = _importar_um_cte(nome, raw.decode('utf-8', 'replace'), cache)
        if motivo:
            rejeitados += 1
            detalhes.append(motivo)
        elif res == 'ok':
            importados += 1
        else:
            pulados += 1

    return jsonify({'ok': True, 'importados': importados, 'pulados': pulados,
                    'rejeitados': rejeitados, 'detalhes': detalhes[:10]})


# ---------------------------------------------------------------------------
# Importar XML — sincronização com Dropbox
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/sync-dropbox', methods=['POST'])
@login_required
def sync_dropbox():
    if not dropbox_sync.is_configured():
        return jsonify({'error': 'Dropbox não configurado. Defina DROPBOX_ACCESS_TOKEN.'}), 400

    data = request.get_json(force=True) or {}
    cliente_id = data.get('cliente_id') or None
    grupo_id = data.get('grupo_id') or None

    files = dropbox_sync.list_xml_files()
    if not files:
        return jsonify({'ok': 0, 'dup': 0, 'err': 0,
                        'msg': 'Nenhum arquivo XML encontrado na pasta Dropbox.'}), 200

    ok, dup, err = 0, 0, 0
    for info in files:
        content = dropbox_sync.download_xml(info['path'])
        if content is None:
            err += 1
            continue
        try:
            parsed = parse_nfe_xml(content)
            result = _save_nfe(parsed, info['name'], 'DROPBOX', content,
                               cliente_id=cliente_id, grupo_id=grupo_id, tipo='entrada')
            if result == 'dup':
                dup += 1
            else:
                ok += 1
        except Exception:
            err += 1

    total = len(files)
    return jsonify({
        'ok': ok, 'dup': dup, 'err': err,
        'msg': f'{total} arquivo(s) lido(s). {ok} importado(s), {dup} duplicado(s), {err} erro(s).',
    })


# ---------------------------------------------------------------------------
# Importar XML — sincronização com Dropbox por departamento
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/importar-dropbox', methods=['POST'])
@login_required
def api_importar_dropbox():
    """Lê arquivos da pasta NOVO do departamento, importa e move para IMPORTADOS ou ERROS."""
    if not dropbox_sync.is_configured():
        return jsonify({'error': 'Dropbox não configurado. Defina DROPBOX_APP_KEY e DROPBOX_REFRESH_TOKEN.'}), 400

    data = request.get_json(force=True) or {}
    departamento = data.get('departamento', '').strip()
    cliente_id = data.get('cliente_id') or None
    grupo_id = data.get('grupo_id') or None
    # Cursor para paginação filtrada: nome do último arquivo analisado na chamada anterior.
    # Usado quando um filtro de empresa/grupo está ativo para avançar pelo diretório NOVO
    # sem re-processar os mesmos arquivos que foram pulados (skipped) em batches anteriores.
    last_scanned = (data.get('last_scanned') or '').strip()

    if not departamento or departamento not in dropbox_sync.DEPARTAMENTOS:
        return jsonify({'error': 'Departamento inválido.'}), 400
    departamento = dropbox_sync.normalize_departamento(departamento)

    svc = dropbox_sync._service

    logger.info('Importar Dropbox: departamento=%r cliente_id=%r grupo_id=%r',
                departamento, cliente_id, grupo_id)

    # ------------------------------------------------------------------
    # Monta conjunto de CNPJs aceitos como filtro (digits only, sem pontuação).
    # None = aceitar todos.  Quando cliente_id ou grupo_id é fornecido pelo
    # front-end, apenas XMLs cujo dest_cnpj bata com esse conjunto são
    # processados; os demais são IGNORADOS (ficam na pasta NOVO intocados).
    # A empresa salva no banco é sempre determinada pelo dest_cnpj do XML —
    # NUNCA pelo cliente_id/grupo_id do filtro — para garantir fidedignidade.
    # ------------------------------------------------------------------
    filter_cnpjs: set | None = None  # set de strings de dígitos
    if cliente_id:
        c = execute_query(
            "SELECT cpf_cnpj FROM clientes WHERE id = %s",
            (int(cliente_id),), fetch=True, fetch_one=True,
        )
        if c:
            _d = re.sub(r'\D', '', c['cpf_cnpj'] or '')
            if _d:
                filter_cnpjs = {_d}
    elif grupo_id:
        members = execute_query(
            "SELECT c.cpf_cnpj FROM clientes c "
            "JOIN cliente_grupo_relacao cgr ON cgr.cliente_id = c.id "
            "WHERE cgr.grupo_id = %s",
            (int(grupo_id),), fetch=True,
        ) or []
        filter_cnpjs = {re.sub(r'\D', '', m['cpf_cnpj'] or '') for m in members} - {''}
        if not filter_cnpjs:
            filter_cnpjs = None  # grupo vazio → sem filtro

    pasta_novo = svc.pasta_novo(departamento)
    logger.info('Buscando XMLs em: %r', pasta_novo)
    try:
        files = svc.list_xml_files(pasta_novo)
    except DropboxAuthError:
        return jsonify({'error': _DROPBOX_AUTH_ERROR_MSG}), 401
    except DropboxError:
        logger.exception('Erro ao listar pasta Dropbox %r', pasta_novo)
        return jsonify({'error': 'Erro ao conectar ao Dropbox. Verifique as credenciais e a conexão.'}), 502

    if not files:
        return jsonify({
            'ok': 0, 'dup': 0, 'err': 0, 'moved_ok': 0, 'moved_err': 0,
            'msg': 'Nenhum arquivo XML encontrado na pasta NOVO.',
        }), 200

    # ------------------------------------------------------------------
    # Paginação por cursor quando filtro de empresa/grupo está ativo.
    # Arquivos pulados (skipped) permanecem em NOVO na mesma posição
    # alfabética, então sem cursor cada chamada re-processaria o mesmo
    # lote sem nunca alcançar os arquivos da empresa selecionada.
    # O cursor (last_scanned) é o nome do último arquivo analisado na
    # chamada anterior; avançamos para o arquivo imediatamente seguinte
    # na lista ordenada pelo Dropbox.
    # ------------------------------------------------------------------
    if filter_cnpjs is not None and last_scanned:
        # Cursor legado do endpoint traz apenas o nome; usa path vazio como menor
        # sufixo possível para avançar para o próximo item ordenado por (name, path).
        cursor_key = (last_scanned.lower(), '')
        _cursor_applied = False
        for _ci, _cf in enumerate(files):
            _cf_key = ((_cf.get('name') or '').lower(), _cf.get('path') or '')
            if _cf_key > cursor_key:
                files = files[_ci:]
                _cursor_applied = True
                break
        if not _cursor_applied:
            # Cursor aponta para além do último arquivo — toda a pasta foi varrida.
            files = []

    if not files and last_scanned:
        # Pasta totalmente varrida com filtro ativo.
        return jsonify({
            'ok': 0, 'dup': 0, 'err': 0, 'skipped': 0,
            'moved_ok': 0, 'moved_err': 0, 'has_more': False,
            'last_scanned': None, 'unregistered_companies': [],
            'imported_companies': [], 'details': [],
            'msg': 'Todos os arquivos da pasta NOVO foram analisados.',
        }), 200

    # Processa no máximo _DROPBOX_BATCH_LIMIT arquivos por chamada para evitar timeout do worker.
    # Se houver mais arquivos, o front-end deve chamar novamente até receber
    # has_more=False ou msg indicando que não há mais arquivos.
    has_more = len(files) > _DROPBOX_BATCH_LIMIT
    files = files[:_DROPBOX_BATCH_LIMIT]

    # Registra o nome do último arquivo deste lote para uso como cursor na próxima chamada.
    last_scanned_out = files[-1]['name'] if files else None

    # Vira mês de pasta no Dropbox → horário de Brasília, não now() cru (UTC no Railway).
    now = datetime.now(ZoneInfo('America/Sao_Paulo'))
    # Cache de pastas já criadas no Dropbox para evitar chamadas redundantes.
    _pastas_criadas: set = set()
    # Cache de vínculos de produto para evitar N×M consultas DB por lote.
    # Chave: (emit_cnpj, codigo_produto, cli, grp) → produto_catalogo_id | None
    _vinculos_cache: dict = {}
    # Cache de dest_cnpj → cliente para evitar repetir a mesma lookup por arquivo.
    _cnpj_cliente_cache: dict = _build_cliente_doc_cache()

    def _get_or_create_pasta(path: str) -> str:
        if path not in _pastas_criadas:
            svc.ensure_folder(path)
            _pastas_criadas.add(path)
        return path

    ok, dup, err, moved_ok, moved_err, skipped = 0, 0, 0, 0, 0, 0
    analyzed_in_batch = 0
    details = []
    # Empresas detectadas nos XMLs que não têm cadastro no sistema.
    # Chave: CNPJ dígitos (ou nome do arquivo), Valor: {dest_nome, dest_cnpj, emit_nome, emit_cnpj}.
    unregistered: dict = {}
    # Sumário de empresas/períodos importados: (numero, nome) → {(year, month): {ok, dup, err}}
    _imported_companies: dict = {}

    for info in files:
        try:
            raw = svc.download_file(info['path'])
        except DropboxAuthError:
            return jsonify({'error': _DROPBOX_AUTH_ERROR_MSG}), 401
        if raw is None:
            err += 1
            analyzed_in_batch += 1
            details.append(f"{info['name']}: falha ao baixar do Dropbox")
            # Arquivo deixado em NOVO para reprocessamento automático.
            # Não criamos pasta GLOBAL — sem o conteúdo do XML não é possível
            # identificar a empresa nem garantir a organização correta.
            continue

        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError:
            content = raw.decode('latin-1', errors='replace')

        # Inicializa variáveis de contexto antes do try para que o bloco
        # except sempre possa referenciá-las sem risco de NameError
        # (ocorre quando parse_nfe_xml lança exceção antes de atribuí-las).
        _nome = None
        _num = None
        _cli = None
        _dt = now

        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # Classifica o XML antes de qualquer processamento.
        # ------------------------------------------------------------------
        _clf = _classify_xml(content)

        if _clf['tipo'] == 'cte':
            logger.info('%s: CT-e detectado — deixado em NOVO', info['name'])
            skipped += 1
            analyzed_in_batch += 1
            continue

        if _clf['tipo'] in ('cancelamento', 'cce', 'manifestacao', 'evento_outro'):
            # Aplica filtro de empresa/grupo se ativo.
            if filter_cnpjs is not None and (
                not _clf['dest_cnpj_digits'] or _clf['dest_cnpj_digits'] not in filter_cnpjs
            ):
                skipped += 1
                analyzed_in_batch += 1
                logger.info('%s: evento %r, CNPJ=%r não pertence ao filtro, ignorado',
                            info['name'], _clf['tipo'], _clf['dest_cnpj_digits'])
                continue

            _proc = _process_evento(_clf, info['name'], content, _cnpj_cliente_cache, now)
            if _proc['empresa_nome'] is None:
                skipped += 1
                analyzed_in_batch += 1
                logger.info('%s: evento sem empresa identificada — deixado em NOVO', info['name'])
                continue
            logger.info('%s: %s → movendo para EVENTOS',
                        info['name'], _clf['descr_evento'])
            ok += 1
            try:
                pasta_err_ev = _get_or_create_pasta(
                    svc.pasta_fiscal(_proc['empresa_nome'], _proc['dt'].year,
                                     _proc['dt'].month, 'EVENTOS', _proc['empresa_num']))
                if svc.move_file(info['path'], f"{pasta_err_ev}/{info['name']}"):
                    moved_err += 1
                else:
                    details.append(f"{info['name']}: falha ao mover evento para ERROS")
            except DropboxAuthError:
                logger.warning('Falha de autenticação ao mover evento %s', info['name'])
            analyzed_in_batch += 1
            continue

        try:
            parsed = parse_nfe_xml(content)
            _dt = parsed['header'].get('data_emissao') or now

            _r = _processar_nota_nfe(
                parsed, info['name'], content, _cnpj_cliente_cache,
                _imported_companies, unregistered,
                vinculos_cache=_vinculos_cache,
                filter_cnpjs=filter_cnpjs,
                grupo_id=grupo_id,
                origem='DROPBOX',
                now=now,
            )
            _dt   = _r['dt']
            _nome = _r['nome']
            _num  = _r['num']

            if _r['codigo'] == 'skipped':
                skipped += 1
                analyzed_in_batch += 1
                logger.info('%s: dest_cnpj não pertence ao filtro, ignorado', info['name'])
                continue
            if _r['codigo'] == 'unregistered':
                analyzed_in_batch += 1
                logger.warning('%s: empresa não cadastrada → deixado em NOVO', info['name'])
                continue
            if _r['codigo'] == 'dup':
                dup += 1
            else:
                ok += 1
            # Copia para pasta do emitente (SAIDAS) quando ambos (dest e emit) são clientes
            if _r['cli'] is not None and _r['emit_cli'] is not None:
                try:
                    pasta_emit_sync = _get_or_create_pasta(
                        svc.pasta_fiscal(_r['emit_nome'], _dt.year, _dt.month,
                                         'SAIDAS', _r['emit_num']))
                    if not svc.copy_file(info['path'], f"{pasta_emit_sync}/{info['name']}"):
                        logger.warning('%s: falha ao copiar para pasta do emitente', info['name'])
                except Exception as _exc_copy:
                    logger.warning('%s: erro ao copiar para pasta do emitente: %s',
                                   info['name'], _exc_copy)
            # Sucesso (incl. duplicata) → move DIRETO para EMPRESAS/.../FISCAL/{SENTIDO}:
            # ENTRADAS quando o dest é cliente; SAIDAS quando só o emit é (nota emitida).
            _sent_main = 'ENTRADAS' if _r['cli'] is not None else 'SAIDAS'
            try:
                pasta_imp = _get_or_create_pasta(
                    svc.pasta_fiscal(_nome, _dt.year, _dt.month, _sent_main, _num))
                if svc.move_file(info['path'], f"{pasta_imp}/{info['name']}"):
                    moved_ok += 1
                else:
                    details.append(f"{info['name']}: falha ao mover para IMPORTADOS no Dropbox")
            except DropboxAuthError:
                logger.warning('Falha de autenticação ao mover %s para importados', info['name'])
            analyzed_in_batch += 1
        except DropboxAuthError:
            return jsonify({'error': _DROPBOX_AUTH_ERROR_MSG}), 401
        except Exception as exc:
            err += 1
            analyzed_in_batch += 1
            if len(details) < _MAX_ERROR_DETAILS:
                details.append({
                    'arquivo': info['name'],
                    'empresa': (_nome or 'DESCONHECIDO')[:80],
                    'erro':    str(exc)[:200],
                })
            logger.exception('Erro ao processar %s', info['name'])
            _err_empresa = _nome or 'DESCONHECIDO'
            _err_num = _num if _nome else None
            try:
                pasta_err = _get_or_create_pasta(
                    svc.pasta_fiscal(_err_empresa, _dt.year, _dt.month, 'ERROS', _err_num))
                if svc.move_file(info['path'], f"{pasta_err}/{info['name']}"):
                    moved_err += 1
                else:
                    details.append(f"{info['name']}: falha ao mover para ERROS no Dropbox")
            except DropboxAuthError:
                logger.warning('Falha de autenticação ao mover %s para erros', info['name'])

    total = len(files)
    msg = (f'{total} arquivo(s) analisado(s). {ok} importado(s), '
           f'{dup} duplicado(s), {err} com erro.')
    if skipped:
        msg += f' {skipped} ignorado(s) (não pertencem à empresa/grupo selecionado).'
    if unregistered:
        msg += (f' {len(unregistered)} empresa(s) não cadastrada(s) — XMLs não importados.'
                ' Cadastre as empresas listadas abaixo e importe novamente.')
    if moved_ok or moved_err:
        msg += f' {moved_ok} movido(s) para IMPORTADOS, {moved_err} movido(s) para ERROS.'

    # Segurança: se nenhum arquivo foi fisicamente movido para fora da pasta NOVO,
    # desliga has_more para evitar que o front-end entre em loop infinito
    # re-listando os mesmos arquivos (p.ex. XMLs de evento sem empresa identificada
    # que ficam em NOVO e incrementam err sem sair do lugar).
    # EXCEÇÃO: quando um filtro de empresa/grupo está ativo e houve arquivos pulados
    # (skipped), o cursor last_scanned avança para um novo bloco de arquivos na próxima
    # chamada — não há risco de loop infinito, então has_more deve permanecer True.
    files_physically_moved = moved_ok + moved_err
    # Se nenhum arquivo do lote chegou a um estado analisado, interrompe paginação
    # para evitar reprocessar o mesmo lote infinitamente.
    if has_more and analyzed_in_batch == 0:
        has_more = False
    elif has_more and files_physically_moved == 0:
        if filter_cnpjs is None or skipped == 0:
            has_more = False

    if has_more:
        msg += ' Há mais arquivos na fila — clique em Importar novamente para continuar.'
    elif unregistered and files_physically_moved == 0:
        msg += ' Cadastre as empresas e importe novamente para continuar.'

    # Converte para lista ordenada para o frontend (formato rico: dest_nome/dest_cnpj/emit_nome/emit_cnpj).
    unreg_list = sorted(
        unregistered.values(),
        key=lambda x: x.get('dest_nome') or x.get('dest_cnpj') or '',
    )

    # Sumário de empresas importadas com totais por período.
    imported_companies_list = []
    for (num, nome), periods_data in sorted(_imported_companies.items(), key=lambda x: x[0][1] or ''):
        periodos = [
            {'periodo': f'{m:02d}/{y}', 'ok': s['ok'], 'dup': s['dup'], 'err': s['err']}
            for (y, m), s in sorted(periods_data.items())
            if s['ok'] + s['dup'] + s['err'] > 0
        ]
        if periodos:
            imported_companies_list.append({'numero': num, 'nome': nome, 'periodos': periodos})

    return jsonify({
        'ok': ok, 'dup': dup, 'err': err, 'skipped': skipped,
        'moved_ok': moved_ok, 'moved_err': moved_err,
        'has_more': has_more,
        'last_scanned': last_scanned_out,
        'unregistered_companies': unreg_list,
        'imported_companies': imported_companies_list,
        'msg': msg,
        'details': details[:10],
    })


# ---------------------------------------------------------------------------
# Importação assíncrona — background thread (não bloqueia workers gunicorn)
# ---------------------------------------------------------------------------

def _download_batch_parallel(svc, files: list) -> 'dict | None':
    """Baixa arquivos do Dropbox em paralelo com ThreadPoolExecutor.

    Retorna {path: bytes|None} para cada arquivo do lote.
    Retorna None se qualquer download levantar DropboxAuthError.
    """
    results: dict = {}

    def _fetch(file_info: dict):
        return file_info['path'], svc.download_file(file_info['path'])

    with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as executor:
        future_to_file = {executor.submit(_fetch, f): f for f in files}
        for future in as_completed(future_to_file):
            try:
                path, content = future.result()
                results[path] = content
            except DropboxAuthError:
                for pending in future_to_file:
                    pending.cancel()
                return None
            except Exception as exc:
                file_info = future_to_file[future]
                logger.warning('[import_job] Download falhou %r: %s', file_info.get('path'), exc)
                results[file_info['path']] = None
    return results


def _execute_moves_parallel(
    svc, pending_moves: list
) -> 'tuple[int, int, list]':
    """Executa moves do Dropbox em paralelo com ThreadPoolExecutor.

    pending_moves: lista de (from_path, to_path, move_type, file_name)
    move_type é 'ok' (→ IMPORTADOS) ou 'err' (→ ERROS).

    Retorna (moved_ok, moved_err, error_details).
    """
    if not pending_moves:
        return 0, 0, []

    moved_ok = moved_err = 0
    error_details: list = []

    def _move(args: tuple):
        from_path, to_path, move_type, file_name = args
        try:
            success = svc.move_file(from_path, to_path)
            return move_type, success, file_name
        except DropboxAuthError:
            logger.warning('[import_job] Auth ao mover %s', file_name)
            return move_type, False, file_name
        except Exception as exc:
            logger.warning('[import_job] Erro ao mover %s: %s', file_name, exc)
            return move_type, False, file_name

    with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as executor:
        for move_type, success, file_name in executor.map(_move, pending_moves, timeout=120):
            if success:
                if move_type == 'ok':
                    moved_ok += 1
                else:
                    moved_err += 1
            else:
                dest = 'IMPORTADOS' if move_type == 'ok' else 'ERROS'
                error_details.append(f"{file_name}: falha ao mover para {dest} no Dropbox")

    return moved_ok, moved_err, error_details


def _run_import_job(job: dict, departamento: str,
                    filter_cnpjs: 'set | None', grupo_id_val: 'int | None') -> None:
    """Executa importação Dropbox completa em background thread.

    Processa todos os lotes de ``_DROPBOX_BATCH_LIMIT_BG`` arquivos até que a
    pasta NOVO fique vazia ou sem progresso real, atualizando ``job`` com o
    progresso entre cada lote.  Suporta filtro de empresa/grupo e parada
    antecipada via ``job['stop_requested']``.

    Não requer contexto Flask — usa diretamente execute_query / dropbox_sync.
    """
    svc = dropbox_sync._service
    pasta_novo = svc.pasta_novo(departamento)

    ok = dup = err = skipped = 0
    moved_ok = moved_err = 0
    unregistered: dict = {}          # cnpj/key → {dest_nome, dest_cnpj, emit_nome, emit_cnpj}
    _imported_companies: dict = {}   # (num, nome) → {(year, month): {ok, dup, err}}
    details: list = []
    _vinculos_cache: dict = {}
    _cnpj_cliente_cache: dict = _build_cliente_doc_cache()
    _pastas_criadas: set = set()
    last_scanned_key: tuple[str, str] | None = None  # cursor (name_lower, path)

    def _get_or_create_pasta(path: str) -> str:
        if path not in _pastas_criadas:
            svc.ensure_folder(path)
            _pastas_criadas.add(path)
        return path

    def _snapshot() -> None:
        """Grava progresso atual no dict do job (lido pelo endpoint /status)."""
        unreg_list = sorted(
            unregistered.values(),
            key=lambda x: x.get('dest_nome') or x.get('dest_cnpj') or '',
        )
        imp_list = []
        for (num, nome), periods_data in sorted(
                _imported_companies.items(), key=lambda x: x[0][1] or ''):
            periodos = [
                {'periodo': f'{m:02d}/{y}',
                 'ok': s['ok'], 'dup': s['dup'], 'err': s['err']}
                for (y, m), s in sorted(periods_data.items())
                if s['ok'] + s['dup'] + s['err'] > 0
            ]
            if periodos:
                imp_list.append({'numero': num, 'nome': nome, 'periodos': periodos})
        total = ok + dup + err + skipped
        msg = (f'{total} arquivo(s) processado(s). {ok} importado(s), '
               f'{dup} duplicado(s), {err} com erro.')
        if skipped:
            msg += f' {skipped} ignorado(s).'
        if unregistered:
            msg += f' {len(unregistered)} empresa(s) não cadastrada(s).'
        if moved_ok or moved_err:
            msg += f' {moved_ok} movido(s) para IMPORTADOS, {moved_err} movido(s) para ERROS.'
        job.update({
            'ok': ok, 'dup': dup, 'err': err, 'skipped': skipped,
            'moved_ok': moved_ok, 'moved_err': moved_err,
            'msg': msg,
            'unregistered_companies': unreg_list,
            'imported_companies': imp_list,
            'details': details[:_MAX_ERROR_DETAILS],
        })

    try:
        for _iteration in range(_MAX_IMPORT_ITERATIONS):
            if job.get('stop_requested'):
                job['status'] = 'stopped'
                _snapshot()
                job['msg'] += ' Importação interrompida pelo usuário.'
                return

            try:
                files = svc.list_xml_files(pasta_novo)
            except DropboxAuthError:
                job['status'] = 'error'
                job['msg'] = _DROPBOX_AUTH_ERROR_MSG
                return
            except DropboxError:
                logger.exception('[import_job] Erro ao listar pasta %r', pasta_novo)
                job['status'] = 'error'
                job['msg'] = 'Erro ao conectar ao Dropbox. Verifique as credenciais.'
                return

            if not files:
                break

            # Garante ordem estável entre iterações para que o cursor avance
            # monotonicamente e não volte para arquivos já analisados.
            files = sorted(files, key=lambda f: ((f.get('name') or '').lower(), f.get('path') or ''))

            # Aplica cursor para avançar além dos arquivos já analisados em lotes
            # anteriores — inclui arquivos de empresas não cadastradas (que ficam em
            # NOVO mas já foram processados nesta execução) e arquivos saltados por
            # filtro de empresa/grupo.
            if last_scanned_key:
                advanced = False
                for ci, cf in enumerate(files):
                    cf_key = ((cf.get('name') or '').lower(), cf.get('path') or '')
                    if cf_key > last_scanned_key:
                        files = files[ci:]
                        advanced = True
                        break
                if not advanced:
                    # Cursor além do último arquivo — pasta totalmente varrida.
                    break

            if not files:
                break

            batch = files[:_DROPBOX_BATCH_LIMIT_BG]
            has_more = len(files) > _DROPBOX_BATCH_LIMIT_BG
            last_scanned_this_key = (
                ((batch[-1].get('name') or '').lower(), batch[-1].get('path') or '')
                if batch else None
            )
            batch_skipped = 0
            batch_unregistered_this = 0
            batch_processed = 0
            # Vira mês de pasta no Dropbox → horário de Brasília, não now() cru.
            now = datetime.now(ZoneInfo('America/Sao_Paulo'))
            # Moves acumulados durante o processamento — executados em paralelo no final.
            pending_moves: list[tuple[str, str, str, str]] = []  # (from, to, type, name)

            # ── Phase 1: downloads em paralelo ────────────────────────────
            downloaded = _download_batch_parallel(svc, batch)
            if downloaded is None:
                job['status'] = 'error'
                job['msg'] = _DROPBOX_AUTH_ERROR_MSG
                _snapshot()
                return

            # ── Phase 2: parse + DB (serial — mantém integridade transacional) ──
            for info in batch:
                if job.get('stop_requested'):
                    break

                raw = downloaded.get(info['path'])
                if raw is None:
                    err += 1
                    details.append(f"{info['name']}: falha ao baixar do Dropbox")
                    batch_processed += 1
                    continue

                try:
                    content = raw.decode('utf-8')
                except UnicodeDecodeError:
                    content = raw.decode('latin-1', errors='replace')

                _nome = None
                _num = None
                _cli = None
                _dt = now

                # Classifica o XML antes de qualquer processamento.
                _clf = _classify_xml(content)

                if _clf['tipo'] == 'cte':
                    logger.info('[import_job] %s: CT-e — deixado em NOVO', info['name'])
                    skipped += 1
                    batch_processed += 1
                    continue

                if _clf['tipo'] in ('cancelamento', 'cce', 'manifestacao', 'evento_outro'):
                    if filter_cnpjs is not None and (
                        not _clf['dest_cnpj_digits'] or _clf['dest_cnpj_digits'] not in filter_cnpjs
                    ):
                        batch_skipped += 1
                        skipped += 1
                        batch_processed += 1
                        continue

                    _proc = _process_evento(_clf, info['name'], content, _cnpj_cliente_cache, now)
                    if _proc['empresa_nome'] is None:
                        skipped += 1
                        batch_skipped += 1
                        batch_processed += 1
                        logger.info('[import_job] %s: evento sem empresa identificada — deixado em NOVO', info['name'])
                        continue
                    ok += 1
                    try:
                        pasta_err_ev = _get_or_create_pasta(
                            svc.pasta_fiscal(_proc['empresa_nome'], _proc['dt'].year,
                                             _proc['dt'].month, 'EVENTOS', _proc['empresa_num']))
                        pending_moves.append((
                            info['path'], f"{pasta_err_ev}/{info['name']}", 'err', info['name']))
                    except DropboxAuthError:
                        logger.warning('[import_job] Auth ao criar pasta para evento %s', info['name'])
                    batch_processed += 1
                    continue

                try:
                    parsed = parse_nfe_xml(content)
                    _dt = parsed['header'].get('data_emissao') or now

                    _r = _processar_nota_nfe(
                        parsed, info['name'], content, _cnpj_cliente_cache,
                        _imported_companies, unregistered,
                        vinculos_cache=_vinculos_cache,
                        filter_cnpjs=filter_cnpjs,
                        grupo_id=grupo_id_val,
                        origem='DROPBOX',
                        now=now,
                    )
                    _dt   = _r['dt']
                    _nome = _r['nome']
                    _num  = _r['num']

                    if _r['codigo'] == 'skipped':
                        batch_skipped += 1
                        skipped += 1
                        batch_processed += 1
                        continue
                    if _r['codigo'] == 'unregistered':
                        batch_unregistered_this += 1
                        batch_processed += 1
                        continue
                    if _r['codigo'] == 'dup':
                        dup += 1
                    else:
                        ok += 1
                    # Copia para pasta do emitente (SAIDAS) quando ambos (dest e emit) são clientes
                    if _r['cli'] is not None and _r['emit_cli'] is not None:
                        try:
                            pasta_emit_job = _get_or_create_pasta(
                                svc.pasta_fiscal(_r['emit_nome'], _dt.year, _dt.month,
                                                 'SAIDAS', _r['emit_num']))
                            if not svc.copy_file(info['path'], f"{pasta_emit_job}/{info['name']}"):
                                logger.warning('[import_job] %s: falha ao copiar para pasta do emitente',
                                               info['name'])
                        except Exception as _exc_copy:
                            logger.warning('[import_job] %s: erro ao copiar para pasta do emitente: %s',
                                           info['name'], _exc_copy)
                    # Move DIRETO para EMPRESAS/.../FISCAL/{SENTIDO} (ENTRADAS/SAIDAS).
                    _sent_main = 'ENTRADAS' if _r['cli'] is not None else 'SAIDAS'
                    try:
                        pasta_imp = _get_or_create_pasta(
                            svc.pasta_fiscal(_nome, _dt.year, _dt.month, _sent_main, _num))
                        pending_moves.append((
                            info['path'], f"{pasta_imp}/{info['name']}", 'ok', info['name']))
                    except DropboxAuthError:
                        logger.warning('[import_job] Auth ao criar pasta para %s', info['name'])
                    batch_processed += 1

                except DropboxAuthError:
                    job['status'] = 'error'
                    job['msg'] = _DROPBOX_AUTH_ERROR_MSG
                    _snapshot()
                    return
                except Exception as exc:
                    err += 1
                    if len(details) < _MAX_ERROR_DETAILS:
                        details.append({
                            'arquivo': info['name'],
                            'empresa': (_nome or 'DESCONHECIDO')[:80],
                            'erro':    str(exc)[:200],
                        })
                    if _nome:
                        try:
                            _pe_key = (str(_num or ''), _nome)
                            _pe_p = (_dt.year, _dt.month) if hasattr(_dt, 'year') else (now.year, now.month)
                            if _pe_key not in _imported_companies:
                                _imported_companies[_pe_key] = {}
                            if _pe_p not in _imported_companies[_pe_key]:
                                _imported_companies[_pe_key][_pe_p] = {'ok': 0, 'dup': 0, 'err': 0}
                            _imported_companies[_pe_key][_pe_p]['err'] += 1
                        except Exception:
                            pass
                    logger.exception('[import_job] Erro ao processar %s', info['name'])
                    _err_empresa = _nome or 'DESCONHECIDO'
                    _err_num = _num if _nome else None
                    try:
                        pasta_err = _get_or_create_pasta(
                            svc.pasta_fiscal(_err_empresa, _dt.year, _dt.month,
                                             'ERROS', _err_num))
                        pending_moves.append((
                            info['path'], f"{pasta_err}/{info['name']}", 'err', info['name']))
                    except DropboxAuthError:
                        logger.warning('[import_job] Auth ao criar pasta de erros para %s', info['name'])
                    batch_processed += 1

            # ── Phase 3: moves em paralelo ────────────────────────────────
            if pending_moves:
                m_ok, m_err, m_details = _execute_moves_parallel(svc, pending_moves)
                moved_ok += m_ok
                moved_err += m_err
                details.extend(m_details)

            # Atualiza cursor com o último arquivo analisado neste lote.
            if last_scanned_this_key:
                last_scanned_key = last_scanned_this_key

            _snapshot()

            if not has_more:
                break

            if batch_processed == 0:
                break

    except Exception:
        logger.exception('[import_job] Falha inesperada no job de importação')
        job['status'] = 'error'
        job['msg'] = 'Falha inesperada durante a importação. Consulte os logs do servidor.'
        _snapshot()
        return

    if job.get('status') == 'running':
        job['status'] = 'done'
    _snapshot()
    logger.info('[import_job] Concluído: ok=%d dup=%d err=%d skipped=%d', ok, dup, err, skipped)


@escrita_fiscal.route('/conf-compras/api/importar-dropbox/start', methods=['POST'])
@login_required
def api_importar_dropbox_start():
    """Inicia importação Dropbox em background thread e retorna job_id imediatamente.

    O cliente deve chamar /status/<job_id> periodicamente para acompanhar
    o progresso, e /stop/<job_id> para interromper antes da conclusão.
    """
    if not dropbox_sync.is_configured():
        return jsonify({'error': 'Dropbox não configurado. Defina DROPBOX_APP_KEY e DROPBOX_REFRESH_TOKEN.'}), 400

    active = import_jobs.get_active_job_for_user(current_user.id)
    if active:
        return jsonify({'error': 'Você já possui uma importação em andamento.', 'job_id': active}), 429

    data = request.get_json(force=True) or {}
    departamento = data.get('departamento', '').strip()
    cliente_id = data.get('cliente_id') or None
    grupo_id = data.get('grupo_id') or None

    if not departamento or departamento not in dropbox_sync.DEPARTAMENTOS:
        return jsonify({'error': 'Departamento inválido.'}), 400
    departamento = dropbox_sync.normalize_departamento(departamento)

    # Resolve filter_cnpjs aqui, no contexto HTTP, para não precisar de app
    # context na thread de background.
    filter_cnpjs: 'set | None' = None
    grupo_id_val: 'int | None' = None
    if cliente_id:
        c = execute_query(
            "SELECT cpf_cnpj FROM clientes WHERE id = %s",
            (int(cliente_id),), fetch=True, fetch_one=True,
        )
        if c:
            _d = re.sub(r'\D', '', c['cpf_cnpj'] or '')
            if _d:
                filter_cnpjs = {_d}
    elif grupo_id:
        grupo_id_val = int(grupo_id)
        members = execute_query(
            "SELECT c.cpf_cnpj FROM clientes c "
            "JOIN cliente_grupo_relacao cgr ON cgr.cliente_id = c.id "
            "WHERE cgr.grupo_id = %s",
            (grupo_id_val,), fetch=True,
        ) or []
        filter_cnpjs = {re.sub(r'\D', '', m['cpf_cnpj'] or '') for m in members} - {''}
        if not filter_cnpjs:
            filter_cnpjs = None

    job_id, job = import_jobs.create_job(user_id=current_user.id)
    t = threading.Thread(
        target=_run_import_job,
        args=(job, departamento, filter_cnpjs, grupo_id_val),
        daemon=True,
        name=f'import-job-{job_id}',
    )
    t.start()
    logger.info('import_job %s iniciado: depto=%r filter=%r', job_id, departamento, filter_cnpjs)
    # AUDITORIA (D2): clique manual em "Executar Agora" (importação do Dropbox).
    # A rodada AUTOMÁTICA do scheduler usa create_job() SEM usuário e não passa
    # por esta rota — só o clique de uma pessoa logada gera linha.
    registrar('escrita.executou_importacao', 'fiscal',
              depois={'departamento': departamento,
                      'escopo': 'cliente' if cliente_id else ('grupo' if grupo_id else 'todas'),
                      'cliente_id': cliente_id, 'grupo_id': grupo_id, 'job_id': job_id,
                      **rotulo_empresa(cliente_id, grupo_id)})
    return jsonify({'job_id': job_id})


@escrita_fiscal.route('/conf-compras/api/importar-dropbox/status/<job_id>')
@login_required
def api_importar_dropbox_status(job_id: str):
    """Retorna o estado atual de um job de importação assíncrona."""
    job = import_jobs.get_job(job_id)
    if job is None:
        return jsonify({'error': 'Job não encontrado ou expirado.'}), 404
    return jsonify(job)


@escrita_fiscal.route('/conf-compras/api/importar-dropbox/stop/<job_id>', methods=['POST'])
@login_required
def api_importar_dropbox_stop(job_id: str):
    """Solicita parada antecipada de um job de importação assíncrona."""
    stopped = import_jobs.request_stop(job_id)
    return jsonify({'ok': stopped})


def importar_departamento_background(departamento: str, origem: str = 'agendado',
                                      usuario_id: int = None,
                                      deadline: float = None) -> dict:
    """Executa a importação completa de um departamento sem contexto HTTP.

    Processa todos os arquivos XML da pasta NOVO do departamento, fazendo
    múltiplas passagens (lotes de ``_DROPBOX_BATCH_LIMIT_BG``) até que não haja
    mais arquivos a processar.  Não aplica filtro de empresa/grupo.

    Retorna um dict com o sumário: ok, dup, err, moved_ok, moved_err, skipped,
    log_id (id do registro em scheduler_import_log), file_logs (lista de detalhes).
    Pensado para uso por tarefas agendadas (scheduler) e execução manual.
    """
    import json as _json
    import time as _time

    if not dropbox_sync.is_configured():
        logger.warning('importar_departamento_background: Dropbox não configurado, abortando.')
        return {'ok': 0, 'dup': 0, 'err': 0, 'moved_ok': 0, 'moved_err': 0, 'skipped': 0, 'log_id': None, 'file_logs': []}

    if departamento not in dropbox_sync.DEPARTAMENTOS:
        logger.warning('importar_departamento_background: departamento inválido %r', departamento)
        return {'ok': 0, 'dup': 0, 'err': 0, 'moved_ok': 0, 'moved_err': 0, 'skipped': 0, 'log_id': None, 'file_logs': []}
    departamento = dropbox_sync.normalize_departamento(departamento)

    # Cria registro de auditoria antes de iniciar
    # iniciado_em via NOW() do MySQL (relógio do banco em -03:00), não Python.
    log_id = None
    try:
        log_id = execute_query(
            "INSERT INTO scheduler_import_log (iniciado_em, departamento, origem, usuario_id) "
            "VALUES (NOW(), %s, %s, %s)",
            (departamento, origem, usuario_id), fetch=False,
        )
    except Exception:
        logger.exception('[agendado] Falha ao criar registro de log para %r', departamento)

    file_logs: list = []  # [{arquivo, resultado, empresa, detalhe}]

    svc = dropbox_sync._service
    pasta_novo = svc.pasta_novo(departamento)
    logger.info('[agendado] Importando departamento=%r, pasta=%r', departamento, pasta_novo)

    totals = {
        'ok': 0,
        'dup': 0,
        'err': 0,
        'moved_ok': 0,
        'moved_err': 0,
        'skipped': 0,
        'error': None,
        'pasta': pasta_novo,
    }
    _vinculos_cache: dict = {}
    _cnpj_cliente_cache: dict = _build_cliente_doc_cache()
    _pastas_criadas: set = set()
    _imported_companies: dict = {}   # (num, nome) → {(year, month): {ok, dup, err}}
    _unregistered_companies: dict = {}  # dest_cnpj_digits → {dest_nome, dest_cnpj, emit_nome, emit_cnpj}
    _last_seen_key: tuple[str, str] | None = None  # cursor (name_lower, path)

    def _get_or_create_pasta(path: str) -> str:
        if path not in _pastas_criadas:
            svc.ensure_folder(path)
            _pastas_criadas.add(path)
        return path

    # Loop de lotes — continua enquanto houver arquivos novos para processar.
    # O cursor _last_seen garante que arquivos de empresas não cadastradas (que
    # ficam em NOVO) não bloqueiem o processamento dos demais.
    max_iterations = 1000  # guarda-chuva contra loop infinito
    iteration = 0
    while iteration < max_iterations:
        iteration += 1
        if deadline is not None:
            if _time.monotonic() >= deadline:
                logger.warning('[agendado] departamento=%r: deadline atingido, encerrando loop.', departamento)
                break
        try:
            files = svc.list_xml_files(pasta_novo)
        except DropboxAuthError as exc:
            logger.error('[agendado] Erro de autenticação ao listar %r: %s', pasta_novo, exc)
            totals['error'] = 'Erro de autenticação no Dropbox. Verifique o token de acesso.'
            break
        except DropboxError as exc:
            logger.error('[agendado] Erro ao listar %r: %s', pasta_novo, exc)
            totals['error'] = (
                f'Não foi possível ler a pasta "{pasta_novo}". '
                'Verifique se a variável DROPBOX_ROOT_FOLDER está configurada corretamente '
                '(ex.: DROPBOX_ROOT_FOLDER=/Aplicativos/ESCRITA FISCAL). '
                'Consulte os logs do servidor para mais detalhes.'
            )
            break

        if not files:
            logger.info('[agendado] Nenhum arquivo em %r — concluído.', pasta_novo)
            break

        # Mantém ordenação determinística para o cursor _last_seen não regredir.
        files = sorted(files, key=lambda f: ((f.get('name') or '').lower(), f.get('path') or ''))

        # Avança cursor para pular arquivos já analisados em lotes anteriores.
        if _last_seen_key:
            advanced = False
            for ci, cf in enumerate(files):
                cf_key = ((cf.get('name') or '').lower(), cf.get('path') or '')
                if cf_key > _last_seen_key:
                    files = files[ci:]
                    advanced = True
                    break
            if not advanced:
                logger.info('[agendado] Cursor além do último arquivo em %r — concluído.', pasta_novo)
                break
            if not files:
                break

        batch = files[:_DROPBOX_BATCH_LIMIT_BG]
        now = datetime.now(ZoneInfo('America/Sao_Paulo'))
        batch_moved = 0
        batch_unregistered_this = 0
        batch_processed = 0

        for info in batch:
            _nome = None
            _num = None
            _cli = None
            _dt = now

            try:
                raw = svc.download_file(info['path'])
            except DropboxAuthError as exc:
                logger.error('[agendado] Falha de auth ao baixar %s: %s', info['name'], exc)
                break
            if raw is None:
                totals['err'] += 1
                logger.warning('[agendado] %s: falha ao baixar, deixado em NOVO', info['name'])
                batch_processed += 1
                continue

            try:
                content = raw.decode('utf-8')
            except UnicodeDecodeError:
                content = raw.decode('latin-1', errors='replace')

            # Classifica o XML antes de qualquer processamento.
            _clf = _classify_xml(content)

            if _clf['tipo'] == 'cte':
                logger.info('[agendado] %s: CT-e — deixado em NOVO', info['name'])
                totals['skipped'] += 1
                file_logs.append({'arquivo': info['name'], 'resultado': 'ignorado',
                                  'empresa': '', 'detalhe': 'CT-e — aguardando suporte futuro'})
                batch_processed += 1
                continue

            if _clf['tipo'] in ('cancelamento', 'cce', 'manifestacao', 'evento_outro'):
                _proc = _process_evento(_clf, info['name'], content, _cnpj_cliente_cache, now)
                if _proc['empresa_nome'] is None:
                    totals['skipped'] += 1
                    file_logs.append({'arquivo': info['name'], 'resultado': 'ignorado',
                                      'empresa': '', 'detalhe': f'{_proc["detalhe"]} — empresa não identificada'})
                    logger.info('[agendado] %s: evento sem empresa identificada — deixado em NOVO', info['name'])
                    batch_processed += 1
                    continue
                logger.info('[agendado] %s: %s → movendo para EVENTOS',
                            info['name'], _clf['descr_evento'])
                totals['ok'] += 1
                file_logs.append({'arquivo': info['name'], 'resultado': 'importado',
                                  'empresa': _proc['empresa_nome'],
                                  'detalhe': _proc['detalhe']})
                try:
                    pasta_err_ev = _get_or_create_pasta(
                        svc.pasta_fiscal(_proc['empresa_nome'], _proc['dt'].year,
                                         _proc['dt'].month, 'EVENTOS', _proc['empresa_num']))
                    if svc.move_file(info['path'], f"{pasta_err_ev}/{info['name']}"):
                        totals['moved_err'] += 1
                        batch_moved += 1
                    else:
                        file_logs.append({'arquivo': info['name'], 'resultado': 'erro',
                                          'empresa': _proc['empresa_nome'],
                                          'detalhe': 'Falha ao mover evento para ERROS'})
                except DropboxAuthError:
                    logger.warning('[agendado] Falha de auth ao mover evento %s', info['name'])
                batch_processed += 1
                continue

            try:
                parsed = parse_nfe_xml(content)
                _dt = parsed['header'].get('data_emissao') or now

                _r = _processar_nota_nfe(
                    parsed, info['name'], content, _cnpj_cliente_cache,
                    _imported_companies, _unregistered_companies,
                    vinculos_cache=_vinculos_cache,
                    origem='DROPBOX',
                    now=now,
                )
                _dt   = _r['dt']
                _nome = _r['nome']
                _num  = _r['num']

                if _r['codigo'] == 'unregistered':
                    _raw_dest_cnpj = parsed['header'].get('dest_cnpj', '')
                    _dest_nome_xml = (parsed['header'].get('dest_nome', '') or '').strip()
                    logger.info('[agendado] %s: empresa não cadastrada (dest_cnpj=%r) → NOVO',
                                info['name'], _raw_dest_cnpj)
                    file_logs.append({'arquivo': info['name'], 'resultado': 'ignorado',
                                      'empresa': _dest_nome_xml or _raw_dest_cnpj,
                                      'detalhe': f'Empresa não cadastrada (CNPJ: {_raw_dest_cnpj})'})
                    batch_unregistered_this += 1
                    batch_processed += 1
                    continue

                _dup_log = (_r['codigo'] == 'dup')
                _res_log = 'duplicata' if _dup_log else 'importado'
                _det_log = ('NF-e já importada anteriormente' if _dup_log
                            else 'Importado com sucesso → IMPORTADOS')
                if _dup_log:
                    totals['dup'] += 1
                else:
                    totals['ok'] += 1

                # Registra detalhamento por empresa (ambas no lançamento duplo)
                _empresas_log = [(_num, _nome)]
                if _r['cli'] is not None and _r['emit_cli'] is not None:
                    _empresas_log.append((_r['emit_num'], _r['emit_nome']))
                for _emp_num_log, _emp_nome_log in _empresas_log:
                    _comp_log = ''
                    try:
                        _comp_log = f' ({_dt.month:02d}/{_dt.year})' if hasattr(_dt, 'year') else ''
                    except Exception:
                        _comp_log = ''
                    file_logs.append({'arquivo': info['name'], 'resultado': _res_log,
                                      'empresa': _emp_nome_log,
                                      'numero': _emp_num_log,
                                      'detalhe': _det_log + _comp_log})

                # Copia para pasta do emitente (SAIDAS) quando ambos (dest e emit) são clientes
                if _r['cli'] is not None and _r['emit_cli'] is not None:
                    try:
                        pasta_emit_ag = _get_or_create_pasta(
                            svc.pasta_fiscal(_r['emit_nome'], _dt.year, _dt.month,
                                             'SAIDAS', _r['emit_num']))
                        if not svc.copy_file(info['path'], f"{pasta_emit_ag}/{info['name']}"):
                            logger.warning('[agendado] %s: falha ao copiar para pasta do emitente',
                                           info['name'])
                    except Exception as _exc_copy:
                        logger.warning('[agendado] %s: erro ao copiar para pasta do emitente: %s',
                                       info['name'], _exc_copy)
                # Move DIRETO para EMPRESAS/.../FISCAL/{SENTIDO} (ENTRADAS/SAIDAS).
                _sent_main = 'ENTRADAS' if _r['cli'] is not None else 'SAIDAS'
                try:
                    pasta_imp = _get_or_create_pasta(
                        svc.pasta_fiscal(_nome, _dt.year, _dt.month, _sent_main, _num))
                    if svc.move_file(info['path'], f"{pasta_imp}/{info['name']}"):
                        totals['moved_ok'] += 1
                        batch_moved += 1
                    else:
                        file_logs.append({'arquivo': info['name'], 'resultado': 'erro',
                                          'empresa': _nome, 'detalhe': 'Falha ao mover para IMPORTADOS no Dropbox'})
                except DropboxAuthError:
                    logger.warning('[agendado] Falha de auth ao mover %s para importados', info['name'])
                batch_processed += 1

            except DropboxAuthError as exc:
                logger.error('[agendado] Falha de auth ao processar %s: %s', info['name'], exc)
                break
            except Exception as exc:
                totals['err'] += 1
                _detalhe_err = str(exc)[:200]
                logger.exception('[agendado] Erro ao processar %s: %s', info['name'], exc)
                _err_empresa = _nome or 'DESCONHECIDO'
                _err_num = _num if _nome else None
                file_logs.append({'arquivo': info['name'], 'resultado': 'erro',
                                  'empresa': _err_empresa, 'detalhe': _detalhe_err})
                try:
                    pasta_err = _get_or_create_pasta(
                        svc.pasta_fiscal(_err_empresa, _dt.year, _dt.month, 'ERROS', _err_num))
                    if svc.move_file(info['path'], f"{pasta_err}/{info['name']}"):
                        totals['moved_err'] += 1
                        batch_moved += 1
                except DropboxAuthError:
                    logger.warning('[agendado] Falha de auth ao mover %s para erros', info['name'])
                batch_processed += 1

        # Atualiza cursor com o último arquivo analisado neste lote.
        if batch:
            _last_seen_key = ((batch[-1].get('name') or '').lower(), batch[-1].get('path') or '')

        # Sem progresso neste lote → pára apenas quando não houve nenhum arquivo
        # processado (nem movidos, nem empresas não cadastradas).  O cursor acima
        # garante que os mesmos arquivos não serão re-processados em iterações futuras.
        if batch_processed == 0:
            logger.info('[agendado] Nenhum arquivo processado neste lote — encerrando loop.')
            break

    logger.info('[agendado] departamento=%r concluído: %s', departamento, totals)

    # Persiste resultado no log de auditoria
    # concluido_em via NOW() do MySQL (relógio do banco em -03:00), não Python.
    try:
        execute_query(
            "UPDATE scheduler_import_log SET concluido_em=NOW(), ok=%s, dup=%s, err=%s, "
            "moved_ok=%s, moved_err=%s, skipped=%s, detalhes=%s WHERE id=%s",
            (totals['ok'], totals['dup'], totals['err'],
             totals['moved_ok'], totals['moved_err'], totals['skipped'],
             _json.dumps(file_logs, default=str, ensure_ascii=False),
             log_id),
            fetch=False,
        )
    except Exception:
        logger.exception('[agendado] Falha ao atualizar log_id=%s', log_id)

    # Converte o sumário por empresa em lista (mesmo formato do fluxo síncrono):
    # [{numero, nome, periodos: [{periodo, ok, dup, err}]}]
    imported_companies_list = []
    for (num, nome), periods_data in sorted(
            _imported_companies.items(), key=lambda x: x[0][1] or ''):
        periodos = [
            {'periodo': f'{m:02d}/{y}', 'ok': s['ok'], 'dup': s['dup'], 'err': s['err']}
            for (y, m), s in sorted(periods_data.items())
            if s['ok'] + s['dup'] + s['err'] > 0
        ]
        if periodos:
            imported_companies_list.append({'numero': num, 'nome': nome, 'periodos': periodos})

    totals['log_id'] = log_id
    totals['file_logs'] = file_logs
    totals['imported_companies'] = imported_companies_list
    totals['unregistered_companies'] = list(_unregistered_companies.values())
    return totals


# ---------------------------------------------------------------------------
# Execução manual do job de importação (para testes / auditoria)
# ---------------------------------------------------------------------------
def _run_all_departments_job(job: dict, usuario_id: 'int | None', departamentos: list) -> None:
    """Executa importação dos departamentos informados sequencialmente em background thread.

    Atualiza ``job`` (dict compartilhado) com progresso em tempo real para que
    o endpoint de status possa informar o cliente via polling.
    """
    deps = departamentos
    job['total_deps'] = len(deps)
    job['completed_deps'] = 0
    job['resumo'] = {}
    job['erros'] = []
    job['imported_companies'] = []
    job['unregistered_companies'] = []
    # Agrega o sumário por empresa/competência de TODOS os departamentos:
    # (numero, nome) → {'mm/yyyy': {ok, dup, err}}
    _agg_companies: dict = {}
    # Empresas não cadastradas de TODOS os departamentos, dedupe por dest_cnpj.
    _agg_unregistered: dict = {}

    def _per_sort_key(p: str):
        try:
            _mm, _yy = p.split('/')
            return (int(_yy), int(_mm))
        except Exception:
            return (0, 0)

    total_ok = 0
    total_dup = 0
    total_err = 0
    total_skipped = 0

    for dep in deps:
        if job.get('stop_requested'):
            break
        job['current_dep'] = dep
        job['msg'] = f'Processando {dep} ({job["completed_deps"] + 1}/{len(deps)})...'
        try:
            result = importar_departamento_background(dep, origem='manual', usuario_id=usuario_id)
            if result.get('error'):
                job['erros'].append(result['error'])
            dep_entry = {
                'ok': result['ok'],
                'dup': result['dup'],
                'err': result['err'],
                'moved_ok': result['moved_ok'],
                'moved_err': result['moved_err'],
                'skipped': result['skipped'],
                'log_id': result.get('log_id'),
                'pasta': result.get('pasta', ''),
            }
            new_resumo = dict(job['resumo'])
            new_resumo[dep] = dep_entry
            job['resumo'] = new_resumo

            # Junta o imported_companies deste departamento no agregado global,
            # somando ok/dup/err por (numero, nome, competência).
            for _co in (result.get('imported_companies') or []):
                _key = (_co.get('numero') or '', _co.get('nome') or '')
                _bucket = _agg_companies.setdefault(_key, {})
                for _p in (_co.get('periodos') or []):
                    _slot = _bucket.setdefault(_p.get('periodo') or '',
                                               {'ok': 0, 'dup': 0, 'err': 0})
                    _slot['ok'] += _p.get('ok', 0)
                    _slot['dup'] += _p.get('dup', 0)
                    _slot['err'] += _p.get('err', 0)
            # Reconstrói a lista para o snapshot do job (lida pelo /status).
            _imp_list = []
            for (_num_c, _nome_c), _pers in sorted(
                    _agg_companies.items(), key=lambda x: x[0][1] or ''):
                _periodos = [
                    {'periodo': _pp, 'ok': _ss['ok'], 'dup': _ss['dup'], 'err': _ss['err']}
                    for _pp, _ss in sorted(_pers.items(), key=lambda kv: _per_sort_key(kv[0]))
                    if _ss['ok'] + _ss['dup'] + _ss['err'] > 0
                ]
                if _periodos:
                    _imp_list.append({'numero': _num_c, 'nome': _nome_c, 'periodos': _periodos})
            job['imported_companies'] = _imp_list

            # Junta as empresas não cadastradas deste departamento (dedupe por dest_cnpj).
            for _uc in (result.get('unregistered_companies') or []):
                _uk = re.sub(r'\D', '', _uc.get('dest_cnpj') or '') or (_uc.get('dest_nome') or '')
                if _uk and _uk not in _agg_unregistered:
                    _agg_unregistered[_uk] = _uc
            job['unregistered_companies'] = list(_agg_unregistered.values())

            total_ok += result['ok']
            total_dup += result['dup']
            total_err += result['err']
            total_skipped += result['skipped']
        except Exception:
            logger.exception('_run_all_departments_job: erro no dep %r', dep)
            job['erros'].append(
                f'Erro ao processar departamento {dep}. Consulte os logs do servidor.'
            )
        job['completed_deps'] += 1
        job['ok'] = total_ok
        job['dup'] = total_dup
        job['err'] = total_err
        job['skipped'] = total_skipped

    total_departamentos = len(deps)
    job['msg'] = (
        f'{total_ok} importado(s), {total_dup} duplicata(s), '
        f'{total_err} erro(s), {total_skipped} ignorado(s) '
        f'em {total_departamentos} departamento(s).'
    )
    job['current_dep'] = None
    job['status'] = 'done'
    logger.info('_run_all_departments_job concluído: ok=%d dup=%d err=%d skipped=%d',
                total_ok, total_dup, total_err, total_skipped)


@escrita_fiscal.route('/conf-compras/api/executar-importacao-agendada', methods=['POST'])
@login_required
def api_executar_importacao_agendada():
    """Dispara imediatamente a importação de um ou todos os departamentos em background thread.

    Restrito a usuários administradores.  Aceita ``departamento`` no body JSON:
    - Vazio ou ``"todos"`` → importa todos os departamentos (admin only).
    - Nome de departamento válido → importa somente ele.

    Retorna imediatamente um ``job_id`` consultável via
    GET /api/executar-importacao-agendada/status/<job_id>.
    """
    usuario = current_user
    if not usuario.is_authenticated or not usuario.is_admin():
        return jsonify({'error': 'Acesso restrito a administradores.'}), 403

    if not dropbox_sync.is_configured():
        return jsonify({'error': 'Dropbox não configurado.'}), 400

    data = request.get_json(force=True) or {}
    departamento = (data.get('departamento') or '').strip()

    if not departamento or departamento.lower() == 'todos':
        departamentos = list(dropbox_sync.DEPARTAMENTOS_CANONICOS)
    else:
        if departamento not in dropbox_sync.DEPARTAMENTOS:
            return jsonify({'error': f'Departamento inválido: {departamento!r}.'}), 400
        departamentos = [dropbox_sync.normalize_departamento(departamento)]

    usuario_id = getattr(usuario, 'id', None)

    job_id, job = import_jobs.create_job()
    job['resumo'] = {}
    job['erros'] = []
    job['current_dep'] = None
    job['completed_deps'] = 0
    job['total_deps'] = len(departamentos)

    t = threading.Thread(
        target=_run_all_departments_job,
        args=(job, usuario_id, departamentos),
        daemon=True,
        name=f'import-all-{job_id}',
    )
    t.start()
    logger.info('api_executar_importacao_agendada: job %s iniciado', job_id)
    return jsonify({'job_id': job_id})


@escrita_fiscal.route('/conf-compras/api/executar-importacao-agendada/status/<job_id>')
@login_required
def api_executar_agendada_status(job_id: str):
    """Retorna o estado atual de um job de importação de todos os departamentos."""
    job = import_jobs.get_job(job_id)
    if job is None:
        return jsonify({'error': 'Job não encontrado ou expirado.'}), 404
    return jsonify(job)


@escrita_fiscal.route('/conf-compras/api/log-importacoes')
@login_required
def api_log_importacoes():
    """Retorna os últimos registros do log de importação agendada/manual.

    Parâmetros opcionais: limit (padrão 50), log_id (para buscar detalhes de uma entrada específica).
    """
    import json as _json

    def _fmt_ts(v):
        """Formata o timestamp já em horário de Brasília.

        iniciado_em/concluido_em agora são gravados via NOW() do MySQL, com o
        pool em -03:00 — portanto já vêm em BRT. NÃO reconverte fuso (fazer isso
        deslocaria -3h). Linhas antigas, gravadas em UTC antes da padronização,
        exibem +3h — degrau único aceito na migração de fuso.
        """
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.strftime('%Y-%m-%d %H:%M')
        s = str(v).strip().replace('T', ' ')
        return s[:16]

    log_id = request.args.get('log_id', type=int)
    if log_id:
        row = execute_query(
            "SELECT id, iniciado_em, concluido_em, departamento, origem, usuario_id, "
            "ok, dup, err, moved_ok, moved_err, skipped, detalhes "
            "FROM scheduler_import_log WHERE id = %s",
            (log_id,), fetch=True, fetch_one=True,
        )
        if not row:
            return jsonify({'error': 'Log não encontrado'}), 404
        if row.get('detalhes') and isinstance(row['detalhes'], str):
            try:
                row['detalhes'] = _json.loads(row['detalhes'])
            except Exception:
                pass
        row['iniciado_em'] = _fmt_ts(row.get('iniciado_em'))
        row['concluido_em'] = _fmt_ts(row.get('concluido_em'))
        return jsonify({'row': row})

    limit = min(request.args.get('limit', 50, type=int), 200)
    rows = execute_query(
        "SELECT id, iniciado_em, concluido_em, departamento, origem, "
        "ok, dup, err, moved_ok, moved_err, skipped "
        "FROM scheduler_import_log "
        "ORDER BY iniciado_em DESC LIMIT %s",
        (limit,), fetch=True,
    ) or []

    for r in rows:
        r['iniciado_em'] = _fmt_ts(r.get('iniciado_em'))
        r['concluido_em'] = _fmt_ts(r.get('concluido_em'))
    return jsonify({'rows': rows})


@escrita_fiscal.route('/api/historico')
@login_required
def api_historico():
    """Histórico de importações + empresas não cadastradas (visível a todos os usuários)."""
    import json as _json

    _brt = ZoneInfo('America/Sao_Paulo')

    def _fmt_ts(v):
        if v is None:
            return None
        if isinstance(v, datetime):
            if v.tzinfo is None:
                v = v.replace(tzinfo=ZoneInfo('UTC')).astimezone(_brt)
            else:
                v = v.astimezone(_brt)
            return v.strftime('%d/%m/%Y %H:%M')
        try:
            s = str(v).strip().replace('T', ' ')
            if len(s) < 19:
                return s[:16]
            dt = datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')
            dt = dt.replace(tzinfo=ZoneInfo('UTC')).astimezone(_brt)
            return dt.strftime('%d/%m/%Y %H:%M')
        except Exception:
            return str(v)[:16]

    # Admin vê todos os departamentos; não-admin vê apenas Fiscal
    if current_user.is_admin():
        sql = (
            "SELECT id, iniciado_em, concluido_em, departamento, origem, "
            "ok, dup, err, moved_ok, moved_err, skipped, detalhes "
            "FROM scheduler_import_log "
            "ORDER BY iniciado_em DESC LIMIT 60"
        )
        rows = execute_query(sql, fetch=True) or []
    else:
        sql = (
            "SELECT id, iniciado_em, concluido_em, departamento, origem, "
            "ok, dup, err, moved_ok, moved_err, skipped, detalhes "
            "FROM scheduler_import_log "
            "WHERE departamento = 'Fiscal' "
            "ORDER BY iniciado_em DESC LIMIT 30"
        )
        rows = execute_query(sql, fetch=True) or []

    unreg: dict = {}  # cnpj_key → {cnpj, nome, files: set}
    result_rows = []

    for r in rows:
        detalhes_raw = r.pop('detalhes', None)
        r['iniciado_em'] = _fmt_ts(r.get('iniciado_em'))
        r['concluido_em'] = _fmt_ts(r.get('concluido_em'))

        ok = r.get('ok') or 0
        dup = r.get('dup') or 0
        err = r.get('err') or 0
        skipped = r.get('skipped') or 0

        # Ignora execuções sem processamento real
        if ok == 0 and dup == 0 and err == 0 and skipped == 0:
            continue

        # Breakdown por empresa e agregação de não cadastradas
        breakdown: dict = {}  # empresa_nome → {ok, err, dup}

        if detalhes_raw:
            try:
                file_logs = _json.loads(detalhes_raw) if isinstance(detalhes_raw, str) else (detalhes_raw or [])
                for entry in file_logs:
                    resultado = entry.get('resultado', '')
                    empresa = (entry.get('empresa', '') or '').strip()
                    arquivo = entry.get('arquivo', '')

                    if resultado == 'ignorado':
                        detalhe = entry.get('detalhe', '')
                        if 'Empresa não cadastrada' in detalhe:
                            m = re.search(r'CNPJ: ([0-9./\-]+)', detalhe)
                            cnpj_raw = m.group(1).strip() if m else ''
                            cnpj_key = re.sub(r'\D', '', cnpj_raw) or (empresa or '')[:30]
                            nome = (empresa or cnpj_raw or 'Desconhecido').strip()
                            if cnpj_key not in unreg:
                                unreg[cnpj_key] = {'cnpj': cnpj_raw, 'nome': nome, 'files': set()}
                            if arquivo:
                                unreg[cnpj_key]['files'].add(arquivo)

                    if empresa and resultado in ('importado', 'duplicado', 'erro'):
                        if empresa not in breakdown:
                            breakdown[empresa] = {'ok': 0, 'err': 0, 'dup': 0}
                        if resultado == 'importado':
                            breakdown[empresa]['ok'] += 1
                        elif resultado == 'erro':
                            breakdown[empresa]['err'] += 1
                        elif resultado == 'duplicado':
                            breakdown[empresa]['dup'] += 1
            except Exception:
                pass

        r['breakdown'] = sorted(
            [{'empresa': k, **v} for k, v in breakdown.items()],
            key=lambda x: (x.get('empresa') or '').lower(),
        )
        result_rows.append(r)

    unreg_list = sorted(
        [{'cnpj': v['cnpj'], 'nome': v['nome'], 'qtd': len(v['files'])} for v in unreg.values()],
        key=lambda x: (x.get('nome') or '').lower(),
    )

    return jsonify({'rows': result_rows, 'unregistered': unreg_list})


@escrita_fiscal.route('/conf-compras/api/horario-agendado', methods=['GET'])
@login_required
def api_horario_agendado():
    """Retorna o horário atual do job de importação automática."""
    from utils.scheduler import get_scheduled_time
    return jsonify(get_scheduled_time())


@escrita_fiscal.route('/conf-compras/api/horario-agendado', methods=['POST'])
@login_required
def api_configurar_horario_agendado():
    """Atualiza o horário do job de importação automática (somente administradores).

    Body JSON: {"hora": 0-23, "minuto": 0-59}
    """
    usuario = current_user
    if not usuario.is_authenticated or not usuario.is_admin():
        return jsonify({'error': 'Acesso restrito a administradores.'}), 403

    data = request.get_json(silent=True) or {}
    try:
        hora = int(data.get('hora', -1))
        minuto = int(data.get('minuto', -1))
        if not (0 <= hora <= 23 and 0 <= minuto <= 59):
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({'error': 'Hora (0-23) e minuto (0-59) são obrigatórios e devem ser válidos.'}), 400

    from utils.scheduler import reschedule, get_scheduled_time
    horario_anterior = get_scheduled_time().get('texto', '—')
    try:
        reschedule(hora, minuto)
    except Exception:
        logger.exception('api_configurar_horario_agendado: erro ao reagendar')
        return jsonify({'error': 'Erro interno ao atualizar o horário. Tente novamente.'}), 500

    logger.info('Horário do scheduler atualizado de %s para %02d:%02d por usuário %s',
                horario_anterior, hora, minuto, usuario.id)
    return jsonify({'ok': True, 'hora': hora, 'minuto': minuto, 'texto': f'{hora:02d}:{minuto:02d}'})


@escrita_fiscal.route('/conf-compras/excluir/<int:nfe_id>', methods=['POST'])
@login_required
def excluir_nfe(nfe_id):
    """Exclui uma nota. SÓ ADMIN — este é o gate real: esconder o botão no
    front não impede um não-admin de chamar a URL na mão. Usa o mecanismo que já
    existe no app: Usuario.is_admin() (tipo_usuario == 'ADMIN'). Usada pelas duas
    telas (conf-compras e conf-saidas)."""
    if not current_user.is_admin():
        logger.warning('[excluir_nfe] usuário %s (não-admin) tentou excluir a nfe_id=%s',
                       getattr(current_user, 'id', '?'), nfe_id)
        msg = 'Apenas administradores podem excluir notas fiscais.'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': msg}), 403
        flash(msg, 'error')
        return redirect(url_for('escrita_fiscal.conf_compras'))

    execute_query("DELETE FROM nfe_importacoes WHERE id = %s", (nfe_id,))
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    flash('Nota fiscal excluída.', 'success')
    return redirect(url_for('escrita_fiscal.conf_compras'))


# ---------------------------------------------------------------------------
# API — exclusão em lote (por filtros ativos)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/excluir-lote', methods=['POST'])
@login_required
def excluir_lote():
    """Exclui TODAS as notas de entrada que batem com os filtros. SÓ ADMIN —
    mesmo gate do excluir_nfe, aplicado antes de montar qualquer WHERE."""
    if not current_user.is_admin():
        logger.warning('[excluir_lote] usuário %s (não-admin) tentou excluir em lote (entradas)',
                       getattr(current_user, 'id', '?'))
        return jsonify({'error': 'Apenas administradores podem excluir notas fiscais.'}), 403

    data = request.get_json(silent=True) or {}
    f_cliente_id = str(data.get('cliente_id', '')).strip()
    f_grupo_id   = str(data.get('grupo_id', '')).strip()
    f_emit_cnpj  = _filtro_lista(data.get('emit_cnpj', ''))
    f_data_ini   = str(data.get('data_ini', '')).strip()
    f_data_fim   = str(data.get('data_fim', '')).strip()
    f_chave      = str(data.get('chave', '')).strip()
    f_num_nota   = str(data.get('num_nota', '')).strip()
    f_cfop       = str(data.get('cfop', '')).strip()
    f_emit_uf    = _filtro_lista(data.get('emit_uf', ''))
    f_dest_cnpj  = str(data.get('dest_cnpj', '')).strip()
    f_vmin       = str(data.get('vmin', '')).strip()
    f_vmax       = str(data.get('vmax', '')).strip()
    f_origem     = str(data.get('origem', '')).strip()

    where, params = ["n.tipo = 'entrada'"], []
    extra_clauses, params = _empresa_where(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra_clauses)

    if f_emit_cnpj:
        where.append(_clausula_in('n.emit_cnpj', f_emit_cnpj, params))
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    if f_chave:
        where.append('n.chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_num_nota:
        where.append('n.num_nota = %s')
        params.append(f_num_nota)
    if f_cfop:
        where.append('n.cfop LIKE %s')
        params.append(f'{f_cfop}%')
    if f_emit_uf:
        where.append(_clausula_in('n.emit_uf', f_emit_uf, params))
    if f_dest_cnpj:
        where.append('n.dest_cnpj LIKE %s')
        params.append(f'%{f_dest_cnpj}%')
    if f_vmin:
        where.append('n.valor_total >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('n.valor_total <= %s')
        params.append(float(f_vmax))
    if f_origem:
        where.append('n.origem = %s')
        params.append(f_origem)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    count_row = execute_query(
        f"SELECT COUNT(*) AS total FROM nfe_importacoes n {where_sql}",
        params, fetch=True, fetch_one=True,
    ) or {}
    total = int(count_row.get('total', 0))

    execute_query(
        f"DELETE n FROM nfe_importacoes n {where_sql}",
        params,
    )
    return jsonify({'ok': True, 'deleted': total})


# ---------------------------------------------------------------------------
# Catálogo de Produtos — listagem
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/')
@permission_required('escrita_fiscal.produtos_catalogo')
def produtos_catalogo():
    # Filtros (Bloco D2). A consulta ao banco continua sendo UMA só, a mesma de
    # antes e sem WHERE: o catálogo é pequeno e limitado por natureza, então a
    # peneira acontece em Python. Isso evita query nova e ainda deixa montar as
    # opções de Unidade a partir do conjunto COMPLETO — se filtrasse no SQL, o
    # select de Unidade só ofereceria o que já sobrou do filtro anterior.
    f = {
        'busca':        request.args.get('busca', '').strip(),
        'categoria':    request.args.get('categoria', '').strip(),
        'subcategoria': request.args.get('subcategoria', '').strip(),
        'tipo_uso':     request.args.get('tipo_uso', '').strip(),
        'unidade':      request.args.get('unidade', '').strip(),
        'cliente_id':   request.args.get('cliente_id', '').strip(),
        'grupo_id':     request.args.get('grupo_id', '').strip(),
        'situacao':     request.args.get('situacao', '').strip(),   # '', 'ativos', 'inativos'
    }

    todos = execute_query(
        """SELECT p.id, p.codigo, p.nome, p.categoria, p.subcategoria, p.tipo_uso, p.unidade,
                  p.ativo, p.cliente_id, p.grupo_id,
                  c.nome_razao_social AS empresa_nome,
                  g.nome AS grupo_nome
             FROM nfe_produtos_catalogo p
             LEFT JOIN clientes c ON c.id = p.cliente_id
             LEFT JOIN grupos_clientes g ON g.id = p.grupo_id
            ORDER BY p.categoria, p.nome""",
        fetch=True,
    ) or []

    # Opções dos selects, tiradas do conjunto completo (nunca do filtrado).
    unidades = sorted({(p.get('unidade') or '').strip()
                       for p in todos if (p.get('unidade') or '').strip()})
    tipos_uso = sorted({(p.get('tipo_uso') or '').strip()
                        for p in todos if (p.get('tipo_uso') or '').strip()})

    def _passa(p):
        if f['busca']:
            alvo = ((p.get('nome') or '') + ' ' + (p.get('codigo') or '')).lower()
            if f['busca'].lower() not in alvo:
                return False
        if f['categoria'] and (p.get('categoria') or '') != f['categoria']:
            return False
        if f['subcategoria'] and (p.get('subcategoria') or '') != f['subcategoria']:
            return False
        if f['tipo_uso'] and (p.get('tipo_uso') or '') != f['tipo_uso']:
            return False
        if f['unidade'] and (p.get('unidade') or '') != f['unidade']:
            return False
        if f['cliente_id'] and str(p.get('cliente_id') or '') != f['cliente_id']:
            return False
        if f['grupo_id'] and str(p.get('grupo_id') or '') != f['grupo_id']:
            return False
        if f['situacao'] == 'ativos' and not p.get('ativo'):
            return False
        if f['situacao'] == 'inativos' and p.get('ativo'):
            return False
        return True

    produtos = [p for p in todos if _passa(p)]
    total_geral = len(todos)

    empresas = _get_empresas()
    grupos = _get_grupos()
    categorias = _get_categorias()

    # Fetch full category objects (id + nome) for the management modal
    cats_db = execute_query(
        "SELECT id, nome FROM nfe_produto_categorias ORDER BY ordem, nome",
        fetch=True,
    ) or []
    subs_db = execute_query(
        "SELECT id, categoria_id, nome FROM nfe_produto_subcategorias ORDER BY ordem, nome",
        fetch=True,
    ) or []

    return render_template(
        'escrita_fiscal/produtos_catalogo.html',
        produtos=produtos,
        empresas=empresas,
        grupos=grupos,
        # f_cliente_id / f_grupo_id continuam para não quebrar nada que os use;
        # o painel novo lê tudo de filtros.
        f_cliente_id=f['cliente_id'],
        f_grupo_id=f['grupo_id'],
        filtros=f,
        # Calculado aqui, e não com {% set %} no template, porque variável criada
        # dentro de um {% block %} do Jinja NÃO enxerga em outro bloco — o painel
        # ficava fechado mesmo com filtro ativo, porque o script mora no extra_js.
        filters_active=any(f.values()),
        total_geral=total_geral,
        unidades=unidades,
        tipos_uso=tipos_uso,
        categorias=categorias,
        cats_db=cats_db,
        subs_db=subs_db,
    )


# ---------------------------------------------------------------------------
# Catálogo de Produtos — salvar (criar / editar)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/salvar', methods=['POST'])
@login_required
def produtos_catalogo_salvar():
    pid = request.form.get('id', '').strip() or None
    cliente_id = request.form.get('cliente_id', '').strip() or None
    grupo_id = request.form.get('grupo_id', '').strip() or None
    codigo = request.form.get('codigo', '').strip()
    nome = request.form.get('nome', '').strip()
    categoria = request.form.get('categoria', '').strip()
    subcategoria = request.form.get('subcategoria', '').strip()
    tipo_uso = request.form.get('tipo_uso', '').strip() or None
    unidade = request.form.get('unidade', '').strip()
    ativo = 1 if request.form.get('ativo') else 0

    if not nome:
        flash('Nome do produto é obrigatório.', 'danger')
        return redirect(url_for('escrita_fiscal.produtos_catalogo'))

    if pid:
        execute_query(
            """UPDATE nfe_produtos_catalogo
                  SET cliente_id=%s, grupo_id=%s, codigo=%s, nome=%s,
                      categoria=%s, subcategoria=%s, tipo_uso=%s, unidade=%s, ativo=%s
                WHERE id=%s""",
            (cliente_id, grupo_id, codigo, nome, categoria, subcategoria, tipo_uso, unidade, ativo, int(pid)),
        )
        flash('Produto atualizado.', 'success')
    else:
        execute_query(
            """INSERT INTO nfe_produtos_catalogo
                   (cliente_id, grupo_id, codigo, nome, categoria, subcategoria, tipo_uso, unidade, ativo)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (cliente_id, grupo_id, codigo, nome, categoria, subcategoria, tipo_uso, unidade, ativo),
        )
        flash('Produto cadastrado.', 'success')

    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


# ---------------------------------------------------------------------------
# Catálogo de Produtos — excluir
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/excluir/<int:pid>', methods=['POST'])
@login_required
def produtos_catalogo_excluir(pid):
    execute_query("DELETE FROM nfe_produtos_catalogo WHERE id = %s", (pid,))
    flash('Produto excluído.', 'success')
    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


# ---------------------------------------------------------------------------
# Categorias — criar
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/categorias/criar', methods=['POST'])
@login_required
def categoria_criar():
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash('Nome da categoria é obrigatório.', 'danger')
        return redirect(url_for('escrita_fiscal.produtos_catalogo'))
    existing = execute_query(
        "SELECT id FROM nfe_produto_categorias WHERE nome = %s", (nome,), fetch=True, fetch_one=True,
    )
    if existing:
        flash('Já existe uma categoria com esse nome.', 'warning')
    else:
        max_ordem = execute_query(
            "SELECT COALESCE(MAX(ordem),0)+1 AS o FROM nfe_produto_categorias", fetch=True, fetch_one=True,
        ) or {}
        execute_query(
            "INSERT INTO nfe_produto_categorias (nome, ordem) VALUES (%s, %s)",
            (nome, max_ordem.get('o', 0)),
        )
        flash(f'Categoria "{nome}" criada.', 'success')
    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


# ---------------------------------------------------------------------------
# Categorias — excluir
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/categorias/excluir/<int:cid>', methods=['POST'])
@login_required
def categoria_excluir(cid):
    cat = execute_query(
        "SELECT nome FROM nfe_produto_categorias WHERE id = %s", (cid,), fetch=True, fetch_one=True,
    )
    if not cat:
        flash('Categoria não encontrada.', 'danger')
        return redirect(url_for('escrita_fiscal.produtos_catalogo'))
    in_use = execute_query(
        "SELECT COUNT(*) AS cnt FROM nfe_produtos_catalogo WHERE categoria = %s",
        (cat['nome'],), fetch=True, fetch_one=True,
    ) or {}
    if in_use.get('cnt', 0) > 0:
        flash(f'A categoria "{cat["nome"]}" está em uso por produtos e não pode ser excluída.', 'danger')
    else:
        execute_query("DELETE FROM nfe_produto_categorias WHERE id = %s", (cid,))
        flash(f'Categoria "{cat["nome"]}" excluída.', 'success')
    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


# ---------------------------------------------------------------------------
# Sub-Categorias — criar
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/subcategorias/criar', methods=['POST'])
@login_required
def subcategoria_criar():
    categoria_id = request.form.get('categoria_id', '').strip()
    nome = request.form.get('nome', '').strip()
    if not categoria_id or not nome:
        flash('Categoria e nome da sub-categoria são obrigatórios.', 'danger')
        return redirect(url_for('escrita_fiscal.produtos_catalogo'))
    existing = execute_query(
        "SELECT id FROM nfe_produto_subcategorias WHERE categoria_id = %s AND nome = %s",
        (int(categoria_id), nome), fetch=True, fetch_one=True,
    )
    if existing:
        flash('Já existe uma sub-categoria com esse nome nessa categoria.', 'warning')
    else:
        max_ordem = execute_query(
            "SELECT COALESCE(MAX(ordem),0)+1 AS o FROM nfe_produto_subcategorias WHERE categoria_id = %s",
            (int(categoria_id),), fetch=True, fetch_one=True,
        ) or {}
        execute_query(
            "INSERT INTO nfe_produto_subcategorias (categoria_id, nome, ordem) VALUES (%s, %s, %s)",
            (int(categoria_id), nome, max_ordem.get('o', 0)),
        )
        flash(f'Sub-categoria "{nome}" criada.', 'success')
    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


# ---------------------------------------------------------------------------
# Sub-Categorias — excluir
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/produtos-catalogo/subcategorias/excluir/<int:sid>', methods=['POST'])
@login_required
def subcategoria_excluir(sid):
    sub = execute_query(
        "SELECT s.nome, c.nome AS cat_nome FROM nfe_produto_subcategorias s "
        "JOIN nfe_produto_categorias c ON c.id = s.categoria_id WHERE s.id = %s",
        (sid,), fetch=True, fetch_one=True,
    )
    if not sub:
        flash('Sub-categoria não encontrada.', 'danger')
        return redirect(url_for('escrita_fiscal.produtos_catalogo'))
    in_use = execute_query(
        "SELECT COUNT(*) AS cnt FROM nfe_produtos_catalogo WHERE subcategoria = %s",
        (sub['nome'],), fetch=True, fetch_one=True,
    ) or {}
    if in_use.get('cnt', 0) > 0:
        flash(f'A sub-categoria "{sub["nome"]}" está em uso e não pode ser excluída.', 'danger')
    else:
        execute_query("DELETE FROM nfe_produto_subcategorias WHERE id = %s", (sid,))
        flash(f'Sub-categoria "{sub["nome"]}" excluída.', 'success')
    return redirect(url_for('escrita_fiscal.produtos_catalogo'))


@escrita_fiscal.route('/conf-compras/api/produtos-catalogo')
@login_required
def api_produtos_catalogo():
    cliente_id = request.args.get('cliente_id', '').strip()
    grupo_id = request.args.get('grupo_id', '').strip()

    # Retorna produtos do cliente + do grupo + globais
    conds, params = ["ativo = 1"], []
    scope_or = ["(cliente_id IS NULL AND grupo_id IS NULL)"]
    if cliente_id:
        scope_or.append("cliente_id = %s")
        params.append(int(cliente_id))
    if grupo_id:
        scope_or.append("grupo_id = %s")
        params.append(int(grupo_id))
    conds.append('(' + ' OR '.join(scope_or) + ')')

    rows = execute_query(
        "SELECT id, codigo, nome, categoria, subcategoria, unidade "
        "FROM nfe_produtos_catalogo WHERE " + ' AND '.join(conds) +
        " ORDER BY categoria, nome",
        tuple(params) if params else None,
        fetch=True,
    ) or []

    resp = jsonify(rows)
    resp.headers['Cache-Control'] = 'private, max-age=300'
    return resp


# ---------------------------------------------------------------------------
# API — vincular todos os itens de uma NF-e ao mesmo produto
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/conf-compras/api/vincular-todos', methods=['POST'])
@login_required
def api_vincular_todos():
    data = request.get_json(force=True) or {}
    nfe_id = data.get('nfe_id')
    produto_id = data.get('produto_id')

    if not nfe_id or not produto_id:
        return jsonify({'error': 'nfe_id e produto_id são obrigatórios'}), 400

    nota = execute_query(
        "SELECT id, tipo, emit_cnpj, cliente_id, grupo_id FROM nfe_importacoes WHERE id = %s",
        (nfe_id,), fetch=True, fetch_one=True,
    )
    if not nota:
        return jsonify({'error': 'NF-e não encontrada'}), 404

    emit_cnpj = nota['emit_cnpj']
    cli = nota.get('cliente_id')
    tipo_nota = nota.get('tipo') or 'entrada'

    # Mesma regra de api_vincular_produto: sem empresa não há escopo para memorizar.
    if not cli:
        return jsonify({
            'error': 'NF-e sem empresa definida — não é possível memorizar. '
                     'Defina a empresa da nota antes de aplicar a todos.'
        }), 400

    itens = execute_query(
        "SELECT id, codigo_produto, descricao FROM nfe_itens WHERE nfe_id = %s",
        (nfe_id,), fetch=True,
    ) or []

    if not itens:
        prod = execute_query(
            "SELECT nome FROM nfe_produtos_catalogo WHERE id = %s",
            (produto_id,), fetch=True, fetch_one=True,
        )
        return jsonify({'ok': True, 'vinculados': 0, 'produto_nome': prod['nome'] if prod else ''})

    item_ids = [it['id'] for it in itens]

    # Batch UPDATE all items of this NF-e at once
    ph = ','.join(['%s'] * len(item_ids))
    execute_query(
        f"UPDATE nfe_itens SET produto_catalogo_id = %s WHERE id IN ({ph})",
        tuple([produto_id] + item_ids),
    )

    # Collect unique codes for rule upserts and retroactive apply
    unique_codes = {it['codigo_produto']: it.get('descricao') or ''
                    for it in itens if it.get('codigo_produto')}

    # Batch upsert rules for all unique codes: 3 queries instead of N×2
    _upsert_vinculo_batch(cli, emit_cnpj, unique_codes, produto_id, tipo=tipo_nota)

    # Retroactive apply: single batch UPDATE covering all historical items for
    # all unique codes — restrito à empresa E AO TIPO da nota.
    if emit_cnpj and unique_codes:
        item_ids_ph = ','.join(['%s'] * len(item_ids))
        cod_ph = ','.join(['%s'] * len(unique_codes))
        execute_query(
            f"""UPDATE nfe_itens i
                  JOIN nfe_importacoes n ON n.id = i.nfe_id
               SET i.produto_catalogo_id = %s
               WHERE i.produto_catalogo_id IS NULL
                 AND n.emit_cnpj = %s
                 AND n.cliente_id = %s
                 AND n.tipo = %s
                 AND i.codigo_produto IN ({cod_ph})
                 AND i.id NOT IN ({item_ids_ph})""",
            tuple([produto_id, emit_cnpj, cli, tipo_nota] + list(unique_codes.keys()) + item_ids),
        )

    prod = execute_query(
        "SELECT nome FROM nfe_produtos_catalogo WHERE id = %s",
        (produto_id,), fetch=True, fetch_one=True,
    )
    prod_nome = prod['nome'] if prod else ''
    return jsonify({'ok': True, 'vinculados': len(itens), 'produto_nome': prod_nome})


# ---------------------------------------------------------------------------
# Memorizações — listagem
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/memorizacoes/')
@permission_required('escrita_fiscal.memorizacoes')
def memorizacoes():
    rows = execute_query(
        """SELECT v.id, v.cliente_id, v.grupo_id, v.ramo_atividade_id,
                  v.emit_cnpj, v.codigo_produto_xml,
                  COALESCE(v.descricao_produto_xml,
                      (SELECT i.descricao FROM nfe_itens i
                         JOIN nfe_importacoes n ON n.id = i.nfe_id
                        WHERE n.emit_cnpj = v.emit_cnpj
                          AND i.codigo_produto = v.codigo_produto_xml
                        LIMIT 1)
                  ) AS descricao_produto_xml,
                  v.produto_catalogo_id, v.tipo, v.criado_em,
                  p.nome AS produto_nome, p.categoria AS produto_categoria,
                  c.nome_razao_social AS empresa_nome,
                  g.nome AS grupo_nome,
                  ra.nome AS ramo_nome,
                  (SELECT n.emit_nome FROM nfe_importacoes n
                    WHERE n.emit_cnpj = v.emit_cnpj LIMIT 1) AS fornecedor_nome
             FROM nfe_produto_vinculo v
             LEFT JOIN nfe_produtos_catalogo p ON p.id = v.produto_catalogo_id
             LEFT JOIN clientes c ON c.id = v.cliente_id
             LEFT JOIN grupos_clientes g ON g.id = v.grupo_id
             LEFT JOIN ramos_atividade ra ON ra.id = v.ramo_atividade_id
            ORDER BY v.emit_cnpj, v.codigo_produto_xml""",
        fetch=True,
    ) or []

    for r in rows:
        if r.get('criado_em') and hasattr(r['criado_em'], 'isoformat'):
            r['criado_em'] = r['criado_em'].isoformat()

    # Catálogo de produtos para o modal de edição
    catalogo = execute_query(
        "SELECT id, nome, categoria FROM nfe_produtos_catalogo WHERE ativo = 1 ORDER BY categoria, nome",
        fetch=True,
    ) or []

    # Empresas para o modal de clonagem: só as que já têm memorização ou já
    # estão num set — clonar com uma empresa sem histórico nenhum é o caso de
    # "copiar tudo de A para B", que continua permitido pelo próprio seletor.
    # clone_set_id só considera conjunto FISCAL (isola departamento). O 'FISCAL'
    # é constante da casa (não é entrada de usuário), então inlinar é seguro.
    _dj = (" JOIN memo_clone_set s ON s.id = m.set_id AND s.departamento = 'FISCAL'"
           if _memo_depto_ok() else "")
    empresas_clone = execute_query(
        f"""SELECT c.id, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj,
                  (SELECT COUNT(*) FROM nfe_produto_vinculo v
                    WHERE v.cliente_id = c.id
                      AND v.grupo_id IS NULL AND v.ramo_atividade_id IS NULL) AS regras,
                  (SELECT m.set_id FROM memo_clone_membro m{_dj}
                    WHERE m.cliente_id = c.id LIMIT 1) AS clone_set_id
             FROM clientes c
            WHERE c.situacao = 'ATIVO'
            ORDER BY c.nome_razao_social""",
        fetch=True,
    ) or []

    # ------------------------------------------------------------------
    # Bloco D3 — agrupamento por empresa e depois por categoria.
    # SEM consulta nova: tudo sai de `rows` (já carregado) e de
    # `empresas_clone`, que já trazia o clone_set_id de cada empresa.
    #
    # Sobre "clonagem": o banco NÃO registra quem foi clonado de quem, e não é
    # omissão — a operação cria um CONJUNTO e funde as regras entre as empresas,
    # sem origem (memo_clone_set / memo_clone_membro, todas com o mesmo
    # criado_em). Por isso a tela fala em "sincronizada com" e mostra QUAIS
    # empresas formam o conjunto; "clonada de X" seria inventar direção.
    # ------------------------------------------------------------------
    conjuntos = {}
    for e in empresas_clone:
        if e.get('clone_set_id'):
            conjuntos.setdefault(e['clone_set_id'], []).append(e)

    # D3.1 E3 — nomes reais dos conjuntos (schema-safe: {} antes da migration).
    set_nomes = _memo_set_nomes()

    fm = {
        'busca':    request.args.get('busca', '').strip(),
        'empresa':  request.args.get('empresa', '').strip(),
        'categoria': request.args.get('categoria', '').strip(),
        'produto':  request.args.get('produto', '').strip(),
        'tipo':     request.args.get('tipo', '').strip(),      # global|empresa|grupo|ramo
        'sincronia': request.args.get('sincronia', '').strip(),  # ''|em_conjunto|fora
    }

    def _ambito(r):
        if r.get('cliente_id'):
            return 'empresa'
        if r.get('grupo_id'):
            return 'grupo'
        if r.get('ramo_atividade_id'):
            return 'ramo'
        return 'global'

    def _passa(r):
        if fm['busca']:
            alvo = ' '.join(str(r.get(k) or '') for k in
                            ('fornecedor_nome', 'emit_cnpj', 'codigo_produto_xml',
                             'descricao_produto_xml', 'produto_nome')).lower()
            if fm['busca'].lower() not in alvo:
                return False
        if fm['empresa'] and str(r.get('cliente_id') or '') != fm['empresa']:
            return False
        if fm['categoria'] and (r.get('produto_categoria') or '') != fm['categoria']:
            return False
        if fm['produto'] and str(r.get('produto_catalogo_id') or '') != fm['produto']:
            return False
        if fm['tipo'] and _ambito(r) != fm['tipo']:
            return False
        if fm['sincronia']:
            cid = str(r.get('cliente_id') or '')
            if fm['sincronia'].startswith('set:'):
                # isola UM conjunto específico ("set:<id>")
                try:
                    sid_f = int(fm['sincronia'][4:])
                except ValueError:
                    sid_f = None
                membros_set = {str(e['id']) for e in conjuntos.get(sid_f, [])}
                if cid not in membros_set:
                    return False
            else:
                em_conjunto = bool(conjuntos) and any(
                    cid == str(e['id'])
                    for membros in conjuntos.values() for e in membros)
                if fm['sincronia'] == 'em_conjunto' and not em_conjunto:
                    return False
                if fm['sincronia'] == 'fora' and em_conjunto:
                    return False
        return True

    filtradas = [r for r in rows if _passa(r)]

    # Empresa -> categoria -> linhas. A ordem dos cartões é definida mais abaixo
    # (por número do cliente); dentro da categoria, por fornecedor e produto.
    por_empresa = {}
    for r in filtradas:
        chave = r.get('cliente_id') or 0          # 0 = sem empresa (global/grupo/ramo)
        por_empresa.setdefault(chave, []).append(r)

    set_de = {}
    for sid, membros in conjuntos.items():
        for e in membros:
            set_de[e['id']] = sid

    grupos_empresa = []
    for chave, lista in por_empresa.items():
        sid = set_de.get(chave)
        outros = [e for e in conjuntos.get(sid, []) if e['id'] != chave] if sid else []
        por_cat = {}
        for r in lista:
            por_cat.setdefault(r.get('produto_categoria') or '(sem categoria)', []).append(r)
        cats = []
        for nome_cat in sorted(por_cat):
            linhas = sorted(por_cat[nome_cat],
                            key=lambda x: ((x.get('fornecedor_nome') or '').upper(),
                                           (x.get('produto_nome') or '').upper()))
            cats.append({'nome': nome_cat, 'total': len(linhas), 'linhas': linhas})
        primeiro = lista[0]
        # Contagem por TIPO a partir das linhas já carregadas (sem query nova por
        # empresa). 'saida' é explícito; o resto (entrada / tipo em branco, cujo
        # default é 'entrada') conta como entrada — assim entrada+saida == total.
        n_saida = sum(1 for r in lista if (r.get('tipo') or '') == 'saida')
        n_entrada = len(lista) - n_saida
        grupos_empresa.append({
            'cliente_id': chave,
            'nome': primeiro.get('empresa_nome') or 'Sem empresa (global, grupo ou ramo)',
            'numero': next((e.get('numero_cliente') for e in empresas_clone
                            if e['id'] == chave), None),
            'total': len(lista),
            'n_entrada': n_entrada,
            'n_saida': n_saida,
            'set_id': sid,
            # D3.1 — nome real do conjunto quando houver; senão, rótulo estável
            # derivado do id, nunca em branco.
            'set_nome': (set_nomes.get(sid) or ('Conjunto #%d' % sid)) if sid else None,
            'set_membros': outros,
            'categorias': cats,
        })

    # D3.1 — ordem por NÚMERO DO CLIENTE crescente (#23, #39, #162, #211…);
    # empresa sem número (e o balde global/grupo/ramo) vai para o fim.
    def _ordem_por_numero(g):
        n = str(g['numero']).strip() if g.get('numero') is not None else ''
        if n.isdigit():
            return (0, int(n), '')
        if n:
            return (1, 0, n.upper())                     # número não-numérico (raro)
        return (2, 0, (g['nome'] or '').upper())         # sem número: por último
    grupos_empresa.sort(key=_ordem_por_numero)

    # Painel de conjuntos (aba "Conjuntos" do modal). Descreve o BANCO, não o
    # recorte da tela — por isso NÃO reusa `rows` (que os filtros de pesquisa
    # encolhem). Duas consultas próprias, independentes de qualquer filtro:
    _dep_cond, _dep_p = _depto_and('s')
    # (1) membros de cada conjunto FISCAL: TODOS, inclusive os com 0 regras e os
    #     INATIVOS; ordenados por número do cliente.
    _mrows = execute_query(
        "SELECT m.set_id, c.id, c.numero_cliente, c.nome_razao_social "
        "  FROM memo_clone_membro m "
        "  JOIN clientes c ON c.id = m.cliente_id "
        "  JOIN memo_clone_set s ON s.id = m.set_id "
        " WHERE 1=1" + _dep_cond +
        " ORDER BY m.set_id, CAST(c.numero_cliente AS UNSIGNED)",
        tuple(_dep_p), fetch=True) or []
    # (2) tamanho do pool por conjunto: pares distintos (emit_cnpj,
    #     codigo_produto_xml, tipo) das regras escopo-empresa dos membros. UMA
    #     consulta para todos os conjuntos (GROUP BY set_id), não uma por laço.
    #     COALESCE no tipo porque COUNT(DISTINCT ...) ignora linha com algum NULL.
    _rrows = execute_query(
        "SELECT m.set_id, "
        "       COUNT(DISTINCT v.emit_cnpj, v.codigo_produto_xml, COALESCE(v.tipo,'')) AS n "
        "  FROM memo_clone_membro m "
        "  JOIN memo_clone_set s ON s.id = m.set_id "
        "  JOIN nfe_produto_vinculo v ON v.cliente_id = m.cliente_id "
        "       AND v.grupo_id IS NULL AND v.ramo_atividade_id IS NULL "
        " WHERE 1=1" + _dep_cond +
        " GROUP BY m.set_id",
        tuple(_dep_p), fetch=True) or []
    _regras_por_set = {r['set_id']: int(r['n'] or 0) for r in _rrows}
    _membros_por_set = {}
    for r in _mrows:
        _membros_por_set.setdefault(r['set_id'], []).append(
            {'id': r['id'], 'numero': r['numero_cliente'], 'nome': r['nome_razao_social']})
    conjuntos_info = []
    for sid in sorted(_membros_por_set):
        membros = _membros_por_set[sid]
        conjuntos_info.append({
            'set_id': sid,
            'nome': set_nomes.get(sid),          # None = ainda sem nome
            'membros': membros,
            'n_empresas': len(membros),
            'n_regras': _regras_por_set.get(sid, 0),
        })

    categorias_filtro = sorted({(r.get('produto_categoria') or '(sem categoria)') for r in rows})
    produtos_filtro = sorted(
        {(r['produto_catalogo_id'], r['produto_nome']) for r in rows
         if r.get('produto_catalogo_id') and r.get('produto_nome')},
        key=lambda x: x[1])
    empresas_filtro = sorted(
        {(r['cliente_id'], r['empresa_nome']) for r in rows
         if r.get('cliente_id') and r.get('empresa_nome')},
        key=lambda x: x[1])

    return render_template('escrita_fiscal/memorizacoes.html', rows=rows,
                           catalogo=catalogo, empresas_clone=empresas_clone,
                           grupos_empresa=grupos_empresa,
                           filtros=fm,
                           filters_active=any(fm.values()),
                           total_geral=len(rows),
                           total_filtrado=len(filtradas),
                           total_empresas=len({r.get('cliente_id') for r in rows
                                               if r.get('cliente_id')}),
                           categorias_filtro=categorias_filtro,
                           produtos_filtro=produtos_filtro,
                           empresas_filtro=empresas_filtro,
                           conjuntos=conjuntos,
                           conjuntos_ordenados=sorted(conjuntos),
                           conjuntos_info=conjuntos_info,
                           set_nomes=set_nomes,
                           is_admin=current_user.is_admin(),
                           empresas_livres=[
                               {'id': e['id'], 'numero': e.get('numero_cliente'),
                                'nome': e.get('nome_razao_social')}
                               for e in empresas_clone if not e.get('clone_set_id')])


# ---------------------------------------------------------------------------
# Memorizações — listar empresas que usam a memorização
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/memorizacoes/empresas-vinculadas/<int:vid>')
@login_required
def memorizacoes_empresas(vid):
    vinculo = execute_query(
        "SELECT emit_cnpj, cliente_id, grupo_id, ramo_atividade_id FROM nfe_produto_vinculo WHERE id = %s",
        (vid,), fetch=True, fetch_one=True,
    )
    if not vinculo:
        return jsonify({'error': 'Memorização não encontrada'}), 404

    emit_cnpj = vinculo['emit_cnpj']

    if vinculo.get('cliente_id'):
        # Regra específica para uma empresa
        empresas = execute_query(
            "SELECT id, numero_cliente, nome_razao_social, cpf_cnpj FROM clientes WHERE id = %s",
            (vinculo['cliente_id'],), fetch=True,
        ) or []
    elif vinculo.get('ramo_atividade_id'):
        # Regra por ramo de atividade — lista clientes do mesmo ramo que importaram desse fornecedor
        empresas = execute_query(
            """SELECT DISTINCT c.id, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj
                 FROM clientes c
                 JOIN cliente_ramo_atividade_relacao crar ON crar.cliente_id = c.id
                   AND crar.ramo_atividade_id = %s
                 JOIN nfe_importacoes n ON (
                     n.cliente_id = c.id
                     OR (n.cliente_id IS NULL
                         AND REPLACE(REPLACE(REPLACE(c.cpf_cnpj,'.',''),'/',''),'-','')
                           = REPLACE(REPLACE(REPLACE(n.dest_cnpj,'.',''),'/',''),'-',''))
                 )
                   AND n.emit_cnpj = %s
                ORDER BY c.nome_razao_social""",
            (vinculo['ramo_atividade_id'], emit_cnpj), fetch=True,
        ) or []
    else:
        # Regra global — todas as empresas que já importaram desse fornecedor
        # (considera tanto cliente_id explícito quanto match por dest_cnpj)
        empresas = execute_query(
            """SELECT DISTINCT c.id, c.numero_cliente, c.nome_razao_social, c.cpf_cnpj
                 FROM clientes c
                 JOIN nfe_importacoes n ON (
                     n.cliente_id = c.id
                     OR (n.cliente_id IS NULL
                         AND REPLACE(REPLACE(REPLACE(c.cpf_cnpj,'.',''),'/',''),'-','')
                           = REPLACE(REPLACE(REPLACE(n.dest_cnpj,'.',''),'/',''),'-',''))
                 )
                 WHERE n.emit_cnpj = %s
                ORDER BY c.nome_razao_social""",
            (emit_cnpj,), fetch=True,
        ) or []

    return jsonify({'ok': True, 'empresas': [dict(e) for e in empresas]})


# ---------------------------------------------------------------------------
# Memorizações — editar (troca produto vinculado)
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/memorizacoes/editar/<int:vid>', methods=['POST'])
@login_required
def memorizacoes_editar(vid):
    data = request.get_json(force=True) or {}
    produto_id = data.get('produto_id')
    if not produto_id:
        return jsonify({'error': 'produto_id obrigatório'}), 400

    execute_query(
        "UPDATE nfe_produto_vinculo SET produto_catalogo_id = %s WHERE id = %s",
        (int(produto_id), vid),
    )

    prod = execute_query(
        "SELECT nome, categoria FROM nfe_produtos_catalogo WHERE id = %s",
        (int(produto_id),), fetch=True, fetch_one=True,
    )
    return jsonify({
        'ok': True,
        'produto_nome': prod['nome'] if prod else '',
        'produto_categoria': prod['categoria'] if prod else '',
    })


# ---------------------------------------------------------------------------
# Memorizações — excluir
# ---------------------------------------------------------------------------
@escrita_fiscal.route('/memorizacoes/excluir/<int:vid>', methods=['POST'])
@login_required
def memorizacoes_excluir(vid):
    # AUDITORIA (D2): captura o vínculo ANTES de apagar (para o 'antes' do log).
    _antes = execute_query(
        "SELECT emit_cnpj, codigo_produto_xml, produto_catalogo_id, tipo "
        "FROM nfe_produto_vinculo WHERE id = %s", (vid,), fetch=True, fetch_one=True)
    execute_query("DELETE FROM nfe_produto_vinculo WHERE id = %s", (vid,))
    registrar('escrita.desvinculou_produto', 'fiscal', tabela='nfe_produto_vinculo',
              registro_id=vid, antes=_antes)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'ok': True})
    flash('Memorização excluída.', 'success')
    return redirect(url_for('escrita_fiscal.memorizacoes'))


# ===========================================================================
# Clone de Memorizações (Fase 3a) — merge entre empresas "iguais"
# ===========================================================================
# Mecanismo próprio, independente de grupos_clientes. Um "set de clone" agrupa
# empresas cujas memorizações de escopo EMPRESA devem ser idênticas. Esta fase
# implementa só a ação de clonar (merge + decisão de conflito + retroativo); a
# sincronização contínua e o "Reverter Clonagem" são a Fase 3b.

# Pares por statement no retroativo — evita montar um WHERE gigante de uma vez.
_CLONE_CHUNK = 200


def _clone_resolver_membros(cliente_ids):
    """Expande a seleção com os membros dos sets já existentes.

    Devolve (membros ordenados, set_id existente ou None, {id: dados do cliente}).
    Levanta ValueError com mensagem pronta para a tela.
    """
    try:
        ids = sorted({int(c) for c in (cliente_ids or []) if c})
    except (TypeError, ValueError):
        raise ValueError('Lista de empresas inválida.')
    if len(ids) < 2:
        raise ValueError('Selecione pelo menos 2 empresas para clonar.')

    ph = ','.join(['%s'] * len(ids))
    validos = execute_query(
        f"SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE id IN ({ph})",
        tuple(ids), fetch=True,
    ) or []
    if len(validos) != len(ids):
        raise ValueError('Alguma das empresas selecionadas não existe mais.')

    # Clone só enxerga conjunto FISCAL (isola departamento). 'FISCAL' é constante.
    _dj = (" JOIN memo_clone_set s ON s.id = m.set_id AND s.departamento = 'FISCAL'"
           if _memo_depto_ok() else "")
    vinc = execute_query(
        f"SELECT m.set_id, m.cliente_id FROM memo_clone_membro m{_dj} WHERE m.cliente_id IN ({ph})",
        tuple(ids), fetch=True,
    ) or []
    sets = {v['set_id'] for v in vinc}
    if len(sets) > 1:
        raise ValueError(
            'As empresas selecionadas já pertencem a sets de clone diferentes. '
            'Junte-as a partir de um set só ou reverta um dos clones antes.')
    set_id = sets.pop() if sets else None

    membros = set(ids)
    if set_id:
        atuais = execute_query(
            "SELECT cliente_id FROM memo_clone_membro WHERE set_id = %s",
            (set_id,), fetch=True,
        ) or []
        membros |= {r['cliente_id'] for r in atuais}

    nomes = {c['id']: c for c in validos}
    faltam = [m for m in membros if m not in nomes]
    if faltam:
        ph2 = ','.join(['%s'] * len(faltam))
        extra = execute_query(
            f"SELECT id, numero_cliente, nome_razao_social FROM clientes WHERE id IN ({ph2})",
            tuple(faltam), fetch=True,
        ) or []
        nomes.update({c['id']: c for c in extra})

    return sorted(membros), set_id, nomes


def _clone_regras_dos_membros(membros):
    ph = ','.join(['%s'] * len(membros))
    return execute_query(
        f"""SELECT v.id, v.cliente_id, v.emit_cnpj, v.codigo_produto_xml,
                   v.produto_catalogo_id, v.descricao_produto_xml,
                   p.nome AS produto_nome
              FROM nfe_produto_vinculo v
              LEFT JOIN nfe_produtos_catalogo p ON p.id = v.produto_catalogo_id
             WHERE v.cliente_id IN ({ph})
               AND v.grupo_id IS NULL AND v.ramo_atividade_id IS NULL""",
        tuple(membros), fetch=True,
    ) or []


def _clone_montar_pares(membros, regras):
    """Agrupa a UNIÃO dos pares (emit_cnpj + cProd) e classifica cada um.

    situacao: 'conflito' (produtos diferentes entre membros),
              'copiar'   (concordam, mas falta membro),
              'ok'       (todos os membros já têm, e concordam).
    """
    por_par = {}
    for r in regras:
        por_par.setdefault((r['emit_cnpj'], r['codigo_produto_xml']), {})[r['cliente_id']] = r

    saida = {}
    for chave, por_cliente in por_par.items():
        produtos = {r['produto_catalogo_id'] for r in por_cliente.values()}
        if len(produtos) > 1:
            situacao, alvo = 'conflito', None
        elif len(por_cliente) >= len(membros):
            situacao, alvo = 'ok', next(iter(produtos))
        else:
            situacao, alvo = 'copiar', next(iter(produtos))
        descricao = next((r.get('descricao_produto_xml') for r in por_cliente.values()
                          if r.get('descricao_produto_xml')), '')
        saida[chave] = {'por_cliente': por_cliente, 'situacao': situacao,
                        'alvo': alvo, 'descricao': descricao}
    return saida


def _clone_cond_pares(bloco, com_alvo=True):
    """(sql, params) com um OR por par. com_alvo restringe a itens que MUDARIAM."""
    ors, params = [], []
    for emit, cod, alvo, _desc in bloco:
        if com_alvo:
            ors.append("(n.emit_cnpj = %s AND i.codigo_produto = %s "
                       "AND (i.produto_catalogo_id IS NULL OR i.produto_catalogo_id <> %s))")
            params += [emit, cod, alvo]
        else:
            ors.append("(n.emit_cnpj = %s AND i.codigo_produto = %s)")
            params += [emit, cod]
    return ' OR '.join(ors), params


def _clone_contar_itens(membros, alvos):
    """Itens de ENTRADA dos membros que MUDARIAM de classificação. Read-only."""
    if not alvos:
        return 0
    ph_m = ','.join(['%s'] * len(membros))
    total = 0
    for i in range(0, len(alvos), _CLONE_CHUNK):
        cond, p_cond = _clone_cond_pares(alvos[i:i + _CLONE_CHUNK])
        row = execute_query(
            f"""SELECT COUNT(*) AS qtd
                  FROM nfe_itens i
                  JOIN nfe_importacoes n ON n.id = i.nfe_id
                 WHERE n.tipo = 'entrada' AND n.cliente_id IN ({ph_m})
                   AND ({cond})""",
            tuple(list(membros) + p_cond), fetch=True, fetch_one=True,
        ) or {}
        total += int(row.get('qtd') or 0)
    return total


def _clone_preview(cliente_ids):
    """Passo 1 — read-only. Não altera absolutamente nada."""
    membros, set_id, nomes = _clone_resolver_membros(cliente_ids)
    pares = _clone_montar_pares(membros, _clone_regras_dos_membros(membros))

    conflitos, alvos, n_copiar, n_ok = [], [], 0, 0
    for (emit, cod), info in sorted(pares.items()):
        if info['situacao'] == 'conflito':
            conflitos.append({
                'emit_cnpj': emit,
                'codigo_produto_xml': cod,
                'descricao': info['descricao'],
                'opcoes': [{
                    'cliente_id': cid,
                    'empresa': (nomes.get(cid) or {}).get('nome_razao_social') or f'#{cid}',
                    'produto_id': r['produto_catalogo_id'],
                    'produto_nome': r['produto_nome'] or '(produto removido do catálogo)',
                } for cid, r in sorted(info['por_cliente'].items())],
            })
            continue
        if info['situacao'] == 'copiar':
            n_copiar += 1
        else:
            n_ok += 1
        alvos.append((emit, cod, info['alvo'], info['descricao']))

    return {
        'membros': [{
            'cliente_id': m,
            'numero_cliente': (nomes.get(m) or {}).get('numero_cliente'),
            'nome': (nomes.get(m) or {}).get('nome_razao_social') or f'#{m}',
            'regras': sum(1 for p in pares.values() if m in p['por_cliente']),
        } for m in membros],
        'set_id': set_id,
        'set_novo': set_id is None,
        'total_pares': len(pares),
        'pares_copiar': n_copiar,
        'pares_ja_ok': n_ok,
        'conflitos': conflitos,
        'itens_reclassificar': _clone_contar_itens(membros, alvos),
        'itens_em_conflito': _clone_contar_itens(
            membros, [(c['emit_cnpj'], c['codigo_produto_xml'], -1, '') for c in conflitos]),
    }


def _clone_aplicar(cliente_ids, decisoes):
    """Passo 2 — grava. Backup ANTES, tudo numa transação, com conferência."""
    membros, set_id, nomes = _clone_resolver_membros(cliente_ids)
    pares = _clone_montar_pares(membros, _clone_regras_dos_membros(membros))

    # Monta os alvos a partir do estado do banco; do cliente só vêm as decisões
    # de conflito, e cada uma precisa ser um dos produtos realmente em disputa.
    alvos, pendentes = [], []
    for (emit, cod), info in sorted(pares.items()):
        if info['situacao'] != 'conflito':
            alvos.append((emit, cod, info['alvo'], info['descricao']))
            continue
        escolhido = (decisoes or {}).get(f'{emit}|{cod}')
        if escolhido is None:
            pendentes.append(f'{emit} / {cod}')
            continue
        opcoes = {r['produto_catalogo_id'] for r in info['por_cliente'].values()}
        if int(escolhido) not in opcoes:
            raise ValueError(
                f'Decisão inválida para {emit} / {cod}: o produto escolhido não é '
                f'nenhum dos que estão em conflito.')
        alvos.append((emit, cod, int(escolhido), info['descricao']))

    if pendentes:
        raise ValueError('Há conflitos sem decisão: ' + ', '.join(pendentes[:5])
                         + ('…' if len(pendentes) > 5 else ''))
    if not alvos:
        raise ValueError('Nada a clonar: as empresas selecionadas não têm memorizações.')

    ph_m = ','.join(['%s'] * len(membros))

    with transacao() as cur:
        # 1. Set + membros + operação (âncora do rollback)
        if set_id is None:
            if _memo_depto_ok():
                cur.execute("INSERT INTO memo_clone_set (departamento, criado_em) "
                            "VALUES ('FISCAL', CURRENT_TIMESTAMP)")
            else:
                cur.execute("INSERT INTO memo_clone_set (criado_em) VALUES (CURRENT_TIMESTAMP)")
            set_id = cur.lastrowid
        cur.execute("SELECT cliente_id FROM memo_clone_membro WHERE set_id = %s", (set_id,))
        ja_membros = {r['cliente_id'] for r in cur.fetchall()}
        for m in membros:
            if m not in ja_membros:
                cur.execute(
                    "INSERT INTO memo_clone_membro (set_id, cliente_id) VALUES (%s, %s)",
                    (set_id, m))
        cur.execute("INSERT INTO memo_clone_op (set_id) VALUES (%s)", (set_id,))
        op_id = cur.lastrowid

        # 2. Estado atual das regras, relido DENTRO da transação
        cur.execute(
            f"""SELECT id, cliente_id, emit_cnpj, codigo_produto_xml, produto_catalogo_id
                  FROM nfe_produto_vinculo
                 WHERE cliente_id IN ({ph_m})
                   AND grupo_id IS NULL AND ramo_atividade_id IS NULL""",
            tuple(membros))
        atual = {(r['cliente_id'], r['emit_cnpj'], r['codigo_produto_xml']): r
                 for r in cur.fetchall()}

        bkp, a_inserir = [], []
        upd_por_alvo = defaultdict(list)
        for emit, cod, alvo, desc in alvos:
            for m in membros:
                r = atual.get((m, emit, cod))
                if r is None:
                    a_inserir.append((m, emit, cod, desc, alvo))
                    bkp.append((op_id, 'INSERT', None, m, emit, cod, None, alvo))
                elif r['produto_catalogo_id'] != alvo:
                    upd_por_alvo[alvo].append(r['id'])
                    bkp.append((op_id, 'UPDATE', r['id'], m, emit, cod,
                                r['produto_catalogo_id'], alvo))

        # 3. BACKUP das regras ANTES de qualquer escrita nelas
        for i in range(0, len(bkp), _CLONE_CHUNK):
            bloco = bkp[i:i + _CLONE_CHUNK]
            ph = ','.join(['(%s,%s,%s,%s,%s,%s,%s,%s)'] * len(bloco))
            cur.execute(
                f"""INSERT INTO memo_clone_regras_bkp_fase3a
                        (op_id, acao, vinculo_id, cliente_id, emit_cnpj,
                         codigo_produto_xml, produto_antes, produto_depois)
                    VALUES {ph}""",
                tuple(x for linha in bloco for x in linha))

        # 4. Escrita das regras
        for alvo, ids in upd_por_alvo.items():
            for i in range(0, len(ids), _CLONE_CHUNK):
                bloco = ids[i:i + _CLONE_CHUNK]
                ph = ','.join(['%s'] * len(bloco))
                cur.execute(
                    f"UPDATE nfe_produto_vinculo SET produto_catalogo_id = %s "
                    f"WHERE id IN ({ph})", tuple([alvo] + bloco))
        for i in range(0, len(a_inserir), _CLONE_CHUNK):
            bloco = a_inserir[i:i + _CLONE_CHUNK]
            ph = ','.join(['(%s,NULL,NULL,%s,%s,%s,%s)'] * len(bloco))
            cur.execute(
                f"""INSERT INTO nfe_produto_vinculo
                        (cliente_id, grupo_id, ramo_atividade_id, emit_cnpj,
                         codigo_produto_xml, descricao_produto_xml, produto_catalogo_id)
                    VALUES {ph}""",
                tuple(x for linha in bloco for x in linha))

        # 5. BACKUP dos itens que o retroativo vai reclassificar (antes + depois)
        for i in range(0, len(alvos), _CLONE_CHUNK):
            bloco = alvos[i:i + _CLONE_CHUNK]
            cases, p_case = [], []
            for emit, cod, alvo, _d in bloco:
                cases.append("WHEN n.emit_cnpj = %s AND i.codigo_produto = %s THEN %s")
                p_case += [emit, cod, alvo]
            cond, p_cond = _clone_cond_pares(bloco)
            cur.execute(
                f"""INSERT INTO memo_clone_itens_bkp_fase3a
                        (op_id, item_id, produto_antes, produto_depois,
                         cliente_id, emit_cnpj, codigo_produto)
                    SELECT %s, i.id, i.produto_catalogo_id,
                           CASE {' '.join(cases)} END,
                           n.cliente_id, n.emit_cnpj, i.codigo_produto
                      FROM nfe_itens i
                      JOIN nfe_importacoes n ON n.id = i.nfe_id
                     WHERE n.tipo = 'entrada' AND n.cliente_id IN ({ph_m})
                       AND ({cond})""",
                tuple([op_id] + p_case + list(membros) + p_cond))

        # 6. Retroativo — o backup é quem dirige o UPDATE, como na Fase 2
        cur.execute(
            """UPDATE nfe_itens i
                 JOIN memo_clone_itens_bkp_fase3a b
                   ON b.item_id = i.id AND b.op_id = %s
                  SET i.produto_catalogo_id = b.produto_depois""",
            (op_id,))
        itens_reclassificados = cur.rowcount

        # 7. Conferência antes do commit
        cur.execute("SELECT COUNT(*) AS c FROM memo_clone_itens_bkp_fase3a WHERE op_id = %s",
                    (op_id,))
        n_bkp_itens = int(cur.fetchone()['c'])
        cur.execute("SELECT COUNT(*) AS c FROM memo_clone_regras_bkp_fase3a WHERE op_id = %s",
                    (op_id,))
        n_bkp_regras = int(cur.fetchone()['c'])
        if itens_reclassificados != n_bkp_itens:
            raise RuntimeError(
                f'Contagens não fecharam: {itens_reclassificados} itens atualizados '
                f'vs {n_bkp_itens} no backup. Nada foi gravado.')
        if n_bkp_regras != len(bkp):
            raise RuntimeError(
                f'Contagens não fecharam: {n_bkp_regras} regras no backup '
                f'vs {len(bkp)} previstas. Nada foi gravado.')

    return {
        'ok': True,
        'set_id': set_id,
        'op_id': op_id,
        'membros': membros,
        'regras_criadas': len(a_inserir),
        'regras_sobrescritas': len(bkp) - len(a_inserir),
        'itens_reclassificados': itens_reclassificados,
        'rollback_sql': _clone_rollback_sql(op_id, set_id),
    }


def _clone_rollback_sql(op_id, set_id):
    return (
        f"-- desfaz a clonagem op_id={op_id}\n"
        f"UPDATE nfe_itens i JOIN memo_clone_itens_bkp_fase3a b\n"
        f"     ON b.item_id = i.id AND b.op_id = {op_id}\n"
        f"   SET i.produto_catalogo_id = b.produto_antes;\n"
        f"UPDATE nfe_produto_vinculo v JOIN memo_clone_regras_bkp_fase3a b\n"
        f"     ON b.vinculo_id = v.id AND b.op_id = {op_id} AND b.acao = 'UPDATE'\n"
        f"   SET v.produto_catalogo_id = b.produto_antes;\n"
        f"DELETE v FROM nfe_produto_vinculo v JOIN memo_clone_regras_bkp_fase3a b\n"
        f"     ON b.op_id = {op_id} AND b.acao = 'INSERT'\n"
        f"    AND b.cliente_id = v.cliente_id AND b.emit_cnpj = v.emit_cnpj\n"
        f"    AND b.codigo_produto_xml = v.codigo_produto_xml\n"
        f" WHERE v.grupo_id IS NULL AND v.ramo_atividade_id IS NULL;\n"
        f"-- e, se quiser desfazer o set inteiro:\n"
        f"DELETE FROM memo_clone_membro WHERE set_id = {set_id};\n"
        f"DELETE FROM memo_clone_set WHERE id = {set_id};"
    )


@escrita_fiscal.route('/memorizacoes/clone/preview', methods=['POST'])
@permission_required('escrita_fiscal.memorizacoes')
def memorizacoes_clone_preview():
    data = request.get_json(force=True) or {}
    try:
        return jsonify({'ok': True, **_clone_preview(data.get('cliente_ids'))})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@escrita_fiscal.route('/memorizacoes/clone/aplicar', methods=['POST'])
@permission_required('escrita_fiscal.memorizacoes')
def memorizacoes_clone_aplicar():
    data = request.get_json(force=True) or {}
    try:
        resultado = _clone_aplicar(data.get('cliente_ids'), data.get('decisoes') or {})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception('Falha ao aplicar clonagem de memorizações')
        return jsonify({'error': f'Falha ao aplicar: {e}'}), 500
    logger.info('Clonagem aplicada: op_id=%s set_id=%s membros=%s regras=+%s/~%s itens=%s',
                resultado['op_id'], resultado['set_id'], resultado['membros'],
                resultado['regras_criadas'], resultado['regras_sobrescritas'],
                resultado['itens_reclassificados'])
    return jsonify(resultado)


# ===========================================================================
# D3.1 Etapa 3 — Gestão do conjunto: NOMEAR, INCLUIR (com corte por data de
# emissão) e DESVINCULAR (admin, com rede de proteção). Preview antes de aplicar,
# como o Clonar. A DDL destas features roda FORA do boot (ver migrations/): os
# endpoints avisam "migração pendente" enquanto a coluna/tabela não existir.
# ===========================================================================
def _regras_empresa(cliente_id):
    """{(emit_cnpj, cod, tipo): produto} das regras escopo-empresa da empresa."""
    rows = execute_query(
        "SELECT emit_cnpj, codigo_produto_xml, tipo, produto_catalogo_id "
        "  FROM nfe_produto_vinculo "
        " WHERE cliente_id = %s AND grupo_id IS NULL AND ramo_atividade_id IS NULL",
        (cliente_id,), fetch=True) or []
    return {(r['emit_cnpj'], r['codigo_produto_xml'], r['tipo']): r['produto_catalogo_id']
            for r in rows}


def _pool_regras(set_id, excluir_cliente=None):
    """Regras do POOL (união dos membros do set): {(emit,cod,tipo):(desc,produto)}."""
    membros = [r['cliente_id'] for r in (execute_query(
        "SELECT cliente_id FROM memo_clone_membro WHERE set_id = %s", (set_id,), fetch=True) or [])]
    if excluir_cliente:
        membros = [m for m in membros if m != excluir_cliente]
    if not membros:
        return {}
    ph = ','.join(['%s'] * len(membros))
    rows = execute_query(
        f"SELECT emit_cnpj, codigo_produto_xml, tipo, descricao_produto_xml, produto_catalogo_id "
        f"  FROM nfe_produto_vinculo "
        f" WHERE cliente_id IN ({ph}) AND grupo_id IS NULL AND ramo_atividade_id IS NULL",
        tuple(membros), fetch=True) or []
    pool = {}
    for r in rows:
        pool.setdefault((r['emit_cnpj'], r['codigo_produto_xml'], r['tipo']),
                        (r['descricao_produto_xml'], r['produto_catalogo_id']))
    return pool


def _incluir_preview(set_id, cliente_id, corte_data):
    """Read-only. Quantos itens seriam vinculados e quantos divergentes ignorados."""
    pool = _pool_regras(set_id, excluir_cliente=cliente_id)
    existentes = _regras_empresa(cliente_id)
    regras_a_criar = sum(1 for k in pool if k not in existentes)

    por_tipo = defaultdict(list)   # tipo -> [(emit,cod,prod)]
    for (emit, cod, tipo), (_desc, prod) in pool.items():
        if existentes.get((emit, cod, tipo), prod) == prod:   # pula só os divergentes já existentes
            por_tipo[tipo].append((emit, cod, prod))

    a_vincular = 0
    divergentes = 0
    for tipo, trip in por_tipo.items():
        pares = [(e, c) for e, c, _ in trip]
        alvo_por_par = {(e, c): p for e, c, p in trip}
        pares_ph = ','.join(['(%s,%s)'] * len(pares))
        base = []
        for e, c in pares:
            base += [e, c]
        cond_data = " AND n.data_emissao >= %s" if corte_data else ""
        p_data = [corte_data] if corte_data else []
        # itens SEM vínculo no escopo (respeitando o corte)
        row = execute_query(
            f"SELECT COUNT(*) AS c FROM nfe_itens i JOIN nfe_importacoes n ON n.id = i.nfe_id "
            f" WHERE n.cliente_id = %s AND n.tipo = %s AND i.produto_catalogo_id IS NULL "
            f"   AND (n.emit_cnpj, i.codigo_produto) IN ({pares_ph}){cond_data}",
            tuple([cliente_id, tipo] + base + p_data), fetch=True, fetch_one=True) or {}
        a_vincular += int(row.get('c') or 0)
        # itens JÁ vinculados com produto DIFERENTE do pool (seriam ignorados)
        drows = execute_query(
            f"SELECT n.emit_cnpj, i.codigo_produto, i.produto_catalogo_id, COUNT(*) AS c "
            f"  FROM nfe_itens i JOIN nfe_importacoes n ON n.id = i.nfe_id "
            f" WHERE n.cliente_id = %s AND n.tipo = %s AND i.produto_catalogo_id IS NOT NULL "
            f"   AND (n.emit_cnpj, i.codigo_produto) IN ({pares_ph}){cond_data} "
            f" GROUP BY n.emit_cnpj, i.codigo_produto, i.produto_catalogo_id",
            tuple([cliente_id, tipo] + base + p_data), fetch=True) or []
        for r in drows:
            if r['produto_catalogo_id'] != alvo_por_par.get((r['emit_cnpj'], r['codigo_produto'])):
                divergentes += int(r['c'] or 0)

    return {'pool': len(pool), 'regras_a_criar': regras_a_criar,
            'itens_a_vincular': a_vincular, 'itens_divergentes': divergentes}


def _incluir_aplicar(set_id, cliente_id, corte_data, actor_id):
    """Grava: entra no conjunto (com o corte), cria as regras que faltam e
    preenche apenas itens SEM vínculo. Nunca sobrescreve regra/ item divergente.
    Não é destrutivo — sem backup."""
    pool = _pool_regras(set_id, excluir_cliente=cliente_id)
    if not pool:
        raise ValueError('O conjunto não tem regras para aplicar.')
    existentes = _regras_empresa(cliente_id)
    n_regras = n_itens = 0
    with transacao() as cur:
        # Membership FISCAL da empresa (não enxerga/mexe em conjunto de outro depto).
        if _memo_depto_ok():
            cur.execute("SELECT m.id FROM memo_clone_membro m "
                        "JOIN memo_clone_set s ON s.id = m.set_id "
                        "WHERE m.cliente_id = %s AND s.departamento = 'FISCAL'", (cliente_id,))
        else:
            cur.execute("SELECT id FROM memo_clone_membro WHERE cliente_id = %s", (cliente_id,))
        fila = cur.fetchone()
        if fila is None:
            cur.execute("INSERT INTO memo_clone_membro (set_id, cliente_id, corte_data) "
                        "VALUES (%s, %s, %s)", (set_id, cliente_id, corte_data))
        else:
            # UPDATE pela linha específica — nunca por cliente_id (isso reatribuiria
            # a membership de outro departamento para este conjunto FISCAL).
            cur.execute("UPDATE memo_clone_membro SET set_id = %s, corte_data = %s "
                        "WHERE id = %s", (set_id, corte_data, fila['id']))
        for (emit, cod, tipo), (desc, prod) in pool.items():
            atual = existentes.get((emit, cod, tipo))
            if atual is None:
                cur.execute(
                    "INSERT INTO nfe_produto_vinculo "
                    "  (cliente_id, grupo_id, ramo_atividade_id, emit_cnpj, codigo_produto_xml, "
                    "   descricao_produto_xml, produto_catalogo_id, tipo) "
                    "VALUES (%s, NULL, NULL, %s, %s, %s, %s, %s)",
                    (cliente_id, emit, cod, desc, prod, tipo))
                n_regras += 1
            elif atual != prod:
                continue   # empresa já tem regra divergente: respeita, não toca nos itens
            sql = ("UPDATE nfe_itens i JOIN nfe_importacoes n ON n.id = i.nfe_id "
                   "   SET i.produto_catalogo_id = %s "
                   " WHERE i.produto_catalogo_id IS NULL AND n.cliente_id = %s AND n.tipo = %s "
                   "   AND n.emit_cnpj = %s AND i.codigo_produto = %s")
            p = [prod, cliente_id, tipo, emit, cod]
            if corte_data:
                sql += " AND n.data_emissao >= %s"
                p.append(corte_data)
            cur.execute(sql, tuple(p))
            n_itens += cur.rowcount
    logger.info('[incluir] empresa %s no conjunto %s (corte=%s): regras +%d, itens +%d',
                cliente_id, set_id, corte_data or 'todos', n_regras, n_itens)
    return {'ok': True, 'set_id': set_id, 'cliente_id': cliente_id,
            'regras_criadas': n_regras, 'itens_vinculados': n_itens,
            'corte_data': corte_data}


def _desvincular_preview(cliente_id):
    row = execute_query(
        "SELECT COUNT(*) AS c FROM nfe_produto_vinculo "
        " WHERE cliente_id = %s AND grupo_id IS NULL AND ramo_atividade_id IS NULL",
        (cliente_id,), fetch=True, fetch_one=True) or {}
    return int(row.get('c') or 0)


def _desvincular_tudo(set_id, cliente_id, actor_id):
    """ADMIN. Backup ANTES do delete, tudo numa transação, com conferência.
    Remove a empresa do conjunto e APAGA as regras dela (escopo empresa)."""
    with transacao() as cur:
        cur.execute("INSERT INTO memo_desvinculo_op (set_id, cliente_id, modo, corte_data, criado_por) "
                    "VALUES (%s, %s, 'tudo', NULL, %s)", (set_id, cliente_id, actor_id))
        op_id = cur.lastrowid
        # 1) BACKUP das regras ANTES de qualquer delete
        cur.execute(
            "INSERT INTO memo_desvinculo_bkp "
            "  (op_id, vinculo_id, cliente_id, grupo_id, ramo_atividade_id, emit_cnpj, "
            "   codigo_produto_xml, descricao_produto_xml, produto_catalogo_id, tipo, removido_por) "
            "SELECT %s, id, cliente_id, grupo_id, ramo_atividade_id, emit_cnpj, "
            "   codigo_produto_xml, descricao_produto_xml, produto_catalogo_id, tipo, %s "
            "  FROM nfe_produto_vinculo "
            " WHERE cliente_id = %s AND grupo_id IS NULL AND ramo_atividade_id IS NULL",
            (op_id, actor_id, cliente_id))
        n_bkp = cur.rowcount
        # 2) DELETE das regras
        cur.execute("DELETE FROM nfe_produto_vinculo "
                    " WHERE cliente_id = %s AND grupo_id IS NULL AND ramo_atividade_id IS NULL",
                    (cliente_id,))
        n_del = cur.rowcount
        # 3) remove do conjunto — pela membership DESTE set (FISCAL), nunca por
        # cliente_id sozinho (isso apagaria membership de outros departamentos).
        cur.execute("DELETE FROM memo_clone_membro WHERE cliente_id = %s AND set_id = %s",
                    (cliente_id, set_id))
        # 4) conferência antes do commit
        if n_del != n_bkp:
            raise RuntimeError(f'Contagens não fecharam: {n_del} apagadas vs {n_bkp} '
                               f'no backup. Nada foi gravado.')
    logger.info('[desvincular:tudo] empresa %s do conjunto %s: op_id=%s, %s regra(s) '
                'para backup e apagadas', cliente_id, set_id, op_id, n_del)
    return {'ok': True, 'op_id': op_id, 'removidas': n_del}


def _desvincular_restore(op_id):
    """Reinsere por op_id o que o desvincular apagou (prova de que dá para voltar)."""
    linhas = execute_query("SELECT * FROM memo_desvinculo_bkp WHERE op_id = %s",
                           (op_id,), fetch=True) or []
    if not linhas:
        raise ValueError('Nada para restaurar nesse op_id.')
    with transacao() as cur:
        for r in linhas:
            cur.execute(
                "INSERT INTO nfe_produto_vinculo "
                "  (cliente_id, grupo_id, ramo_atividade_id, emit_cnpj, codigo_produto_xml, "
                "   descricao_produto_xml, produto_catalogo_id, tipo) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (r['cliente_id'], r['grupo_id'], r['ramo_atividade_id'], r['emit_cnpj'],
                 r['codigo_produto_xml'], r['descricao_produto_xml'],
                 r['produto_catalogo_id'], r['tipo']))
    return len(linhas)


@escrita_fiscal.route('/memorizacoes/conjunto/nomear', methods=['POST'])
@permission_required('escrita_fiscal.memorizacoes')
def memorizacoes_conjunto_nomear():
    if not _memo_col_existe('memo_clone_set', 'nome'):
        return jsonify({'error': 'Migração pendente: a coluna de nome do conjunto ainda não '
                                 'existe. Rode a migration da gestão do conjunto.'}), 409
    data = request.get_json(force=True) or {}
    try:
        sid = int(data.get('set_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'set_id inválido.'}), 400
    nome = (data.get('nome') or '').strip()[:120]
    cond, p = _depto_and('memo_clone_set')   # só renomeia conjunto FISCAL
    res = execute_query("UPDATE memo_clone_set SET nome = %s WHERE id = %s" + cond,
                        tuple([nome or None, sid] + p))
    if res is None:
        return jsonify({'error': 'Falha ao gravar o nome.'}), 500
    return jsonify({'ok': True, 'set_id': sid, 'nome': nome, 'rotulo': nome or f'Conjunto #{sid}'})


@escrita_fiscal.route('/memorizacoes/conjunto/incluir/preview', methods=['POST'])
@permission_required('escrita_fiscal.memorizacoes')
def memorizacoes_conjunto_incluir_preview():
    if not _memo_col_existe('memo_clone_membro', 'corte_data'):
        return jsonify({'error': 'Migração pendente: a data de corte por membro ainda não '
                                 'existe. Rode a migration da gestão do conjunto.'}), 409
    data = request.get_json(force=True) or {}
    try:
        set_id = int(data.get('set_id'))
        cliente_id = int(data.get('cliente_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'set_id e cliente_id são obrigatórios.'}), 400
    corte = (data.get('corte_data') or '').strip() or None
    if _memo_depto_ok():   # não deixa "enxergar" set de outro departamento
        s = execute_query("SELECT departamento FROM memo_clone_set WHERE id = %s",
                          (set_id,), fetch=True, fetch_one=True)
        if not s or s.get('departamento') != _MEMO_DEPTO:
            return jsonify({'error': 'Conjunto inválido para o Fiscal.'}), 400
    try:
        return jsonify({'ok': True, 'corte_data': corte, **_incluir_preview(set_id, cliente_id, corte)})
    except Exception as e:
        logger.exception('Falha no preview de incluir empresa no conjunto')
        return jsonify({'error': f'Falha ao analisar: {e}'}), 500


@escrita_fiscal.route('/memorizacoes/conjunto/incluir/aplicar', methods=['POST'])
@permission_required('escrita_fiscal.memorizacoes')
def memorizacoes_conjunto_incluir_aplicar():
    if not _memo_col_existe('memo_clone_membro', 'corte_data'):
        return jsonify({'error': 'Migração pendente. Rode a migration da gestão do conjunto.'}), 409
    data = request.get_json(force=True) or {}
    try:
        set_id = int(data.get('set_id'))
        cliente_id = int(data.get('cliente_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'set_id e cliente_id são obrigatórios.'}), 400
    corte = (data.get('corte_data') or '').strip() or None
    # o conjunto-alvo tem de ser FISCAL (não deixa incluir num set de outro depto).
    if _memo_depto_ok():
        s = execute_query("SELECT departamento FROM memo_clone_set WHERE id = %s",
                          (set_id,), fetch=True, fetch_one=True)
        if not s or s.get('departamento') != _MEMO_DEPTO:
            return jsonify({'error': 'Conjunto inválido para o Fiscal.'}), 400
    # a empresa não pode já pertencer a OUTRO conjunto FISCAL (conjunto de outro
    # departamento é ignorado — não bloqueia nem é enxergado aqui).
    existente = _fiscal_set_de(cliente_id)
    if existente and existente != set_id:
        return jsonify({'error': 'A empresa já pertence a outro conjunto. Desvincule antes.'}), 400
    try:
        return jsonify(_incluir_aplicar(set_id, cliente_id, corte,
                                        getattr(current_user, 'id', None)))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        logger.exception('Falha ao incluir empresa no conjunto')
        return jsonify({'error': f'Falha ao aplicar: {e}'}), 500


@escrita_fiscal.route('/memorizacoes/conjunto/desvincular/preview', methods=['POST'])
@permission_required('escrita_fiscal.memorizacoes')
def memorizacoes_conjunto_desvincular_preview():
    if not current_user.is_admin():
        return jsonify({'error': 'Apenas administradores podem desvincular.'}), 403
    data = request.get_json(force=True) or {}
    try:
        cliente_id = int(data.get('cliente_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'cliente_id obrigatório.'}), 400
    emp = execute_query("SELECT nome_razao_social FROM clientes WHERE id = %s",
                        (cliente_id,), fetch=True, fetch_one=True) or {}
    return jsonify({'ok': True, 'cliente_id': cliente_id,
                    'nome_empresa': emp.get('nome_razao_social') or f'#{cliente_id}',
                    'regras_a_remover': _desvincular_preview(cliente_id)})


@escrita_fiscal.route('/memorizacoes/conjunto/desvincular/aplicar', methods=['POST'])
@permission_required('escrita_fiscal.memorizacoes')
def memorizacoes_conjunto_desvincular_aplicar():
    if not current_user.is_admin():
        return jsonify({'error': 'Apenas administradores podem desvincular.'}), 403
    if not (_memo_tabela_existe('memo_desvinculo_op') and _memo_tabela_existe('memo_desvinculo_bkp')):
        return jsonify({'error': 'Migração pendente: as tabelas de backup do desvincular ainda '
                                 'não existem. Rode a migration da gestão do conjunto.'}), 409
    data = request.get_json(force=True) or {}
    modo = (data.get('modo') or '').strip()
    try:
        cliente_id = int(data.get('cliente_id'))
    except (TypeError, ValueError):
        return jsonify({'error': 'cliente_id obrigatório.'}), 400
    confirmacao = (data.get('confirmacao') or '').strip()
    set_id_fiscal = _fiscal_set_de(cliente_id)   # só o conjunto FISCAL da empresa
    if not set_id_fiscal:
        return jsonify({'error': 'Empresa não está em nenhum conjunto.'}), 400
    emp = execute_query("SELECT nome_razao_social FROM clientes WHERE id = %s",
                        (cliente_id,), fetch=True, fetch_one=True) or {}
    nome_emp = (emp.get('nome_razao_social') or '').strip()
    # confirmação digitando o nome da empresa (defesa contra clique fácil)
    if not confirmacao or confirmacao.lower() != nome_emp.lower():
        return jsonify({'error': 'Confirmação inválida: digite exatamente o nome da empresa.'}), 400
    if modo != 'tudo':
        # "a partir de data de emissão" ainda não definido contra o schema atual
        # (o backup espelha REGRAS, que não têm data de emissão) — não invento.
        return jsonify({'error': 'Modo de desvincular não suportado nesta versão. Use "tudo".'}), 400
    try:
        return jsonify(_desvincular_tudo(set_id_fiscal, cliente_id,
                                         getattr(current_user, 'id', None)))
    except Exception as e:
        logger.exception('Falha ao desvincular empresa (tudo)')
        return jsonify({'error': f'Falha ao desvincular: {e}'}), 500


# _lookup_vinculo, _save_nfe e _save_nfe_dual foram movidos para utils/nfe_import.py
# (reusados pela captura SEFAZ sem import circular) e reexportados no topo deste
# arquivo. Comportamento idêntico ao anterior.


def _processar_nota_nfe(
    parsed: dict,
    nome_arquivo: str,
    xml_raw: str,
    cnpj_cliente_cache: dict,
    imported_companies: dict,
    unregistered_companies: dict,
    *,
    vinculos_cache: 'dict | None' = None,
    filter_cnpjs: 'set | None' = None,
    grupo_id: 'int | None' = None,
    origem: str = 'DROPBOX',
    now: 'datetime | None' = None,
) -> dict:
    """Processa uma NF-e já parseada: detecta empresas, salva e atualiza sumários.

    Modifica imported_companies e unregistered_companies in-place.

    Retorna dict:
        codigo      'ok' | 'dup' | 'unregistered' | 'skipped'
        save_result 'ok' | 'dup' | None
        cli         id do cliente destinatário (None se não encontrado)
        nome        nome da empresa (dest ou emit quando só emit, para pasta Dropbox)
        num         número do cliente (str | None)
        dt          datetime de emissão
        emit_cli    id do cliente emitente (None se não for cliente distinto)
        emit_nome   nome do emitente (None se emit_cli is None)
        emit_num    número do cliente emitente (None se emit_cli is None)
    """
    _now = now or datetime.now(ZoneInfo('America/Sao_Paulo'))
    _dt = parsed['header'].get('data_emissao') or _now
    dest_cnpj_digits = re.sub(r'\D', '', parsed['header'].get('dest_cnpj', ''))

    _skipped = {
        'codigo': 'skipped', 'save_result': None, 'cli': None,
        'nome': None, 'num': None, 'dt': _dt,
        'emit_cli': None, 'emit_nome': None, 'emit_num': None,
    }

    # Filtro de empresa/grupo — aplicado apenas quando filter_cnpjs está definido
    if filter_cnpjs is not None:
        if len(dest_cnpj_digits) < 11 or dest_cnpj_digits not in filter_cnpjs:
            return _skipped

    # Detecta destinatário
    _cli = None
    _nome = None
    _num = None
    if len(dest_cnpj_digits) >= 11:
        found = _find_cliente_by_doc_digits(dest_cnpj_digits, cnpj_cliente_cache)
        if found:
            _cli  = found['id']
            _nome = found['nome_razao_social']
            _num  = found.get('numero_cliente') or None

    # Detecta emitente (para gerar registro de saída quando for cliente distinto)
    _emit_cli  = None
    _emit_nome = None
    _emit_num  = None
    _emit_digits = re.sub(r'\D', '', parsed['header'].get('emit_cnpj', ''))
    if len(_emit_digits) >= 11:
        _emit_found = _find_cliente_by_doc_digits(_emit_digits, cnpj_cliente_cache)
        if _emit_found and _emit_found['id'] != _cli:
            _emit_cli  = _emit_found['id']
            _emit_nome = _emit_found['nome_razao_social']
            _emit_num  = _emit_found.get('numero_cliente') or None
            if _nome is None:
                # Só emitente encontrado: usa nome/num dele para nomear a pasta
                _nome = _emit_nome
                _num  = _emit_num

    # Empresa não cadastrada — registra no formato rico e retorna sem salvar
    if _nome is None:
        _raw_dest_cnpj = parsed['header'].get('dest_cnpj', '')
        _dest_nome_xml = (parsed['header'].get('dest_nome', '') or '').strip()
        _raw_emit_cnpj = parsed['header'].get('emit_cnpj', '')
        _emit_nome_xml = (parsed['header'].get('emit_nome', '') or '').strip()
        _unreg_key = dest_cnpj_digits or _raw_dest_cnpj or nome_arquivo
        if _unreg_key not in unregistered_companies:
            unregistered_companies[_unreg_key] = {
                'dest_nome': _dest_nome_xml,
                'dest_cnpj': _raw_dest_cnpj,
                'emit_nome': _emit_nome_xml,
                'emit_cnpj': _raw_emit_cnpj,
            }
        return {
            'codigo': 'unregistered', 'save_result': None, 'cli': None,
            'nome': None, 'num': None, 'dt': _dt,
            'emit_cli': None, 'emit_nome': None, 'emit_num': None,
        }

    # Salva a nota (entrada para dest e/ou saída para emit)
    save_result = _save_nfe_dual(
        parsed, nome_arquivo, origem, xml_raw,
        dest_cli=_cli,
        emit_cli=_emit_cli,
        grupo_id=grupo_id if _cli is None else None,
        vinculos_cache=vinculos_cache,
    )

    # Atualiza sumário por empresa/competência (inclui as duas no lançamento duplo)
    try:
        _period = (_dt.year, _dt.month) if hasattr(_dt, 'year') else (_now.year, _now.month)
        _co_keys = [(str(_num or ''), _nome)]
        if _cli is not None and _emit_cli is not None:
            _co_keys.append((str(_emit_num or ''), _emit_nome))
        for _co_key in _co_keys:
            if _co_key not in imported_companies:
                imported_companies[_co_key] = {}
            if _period not in imported_companies[_co_key]:
                imported_companies[_co_key][_period] = {'ok': 0, 'dup': 0, 'err': 0}
            imported_companies[_co_key][_period]['dup' if save_result == 'dup' else 'ok'] += 1
    except Exception:
        pass

    return {
        'codigo': save_result,
        'save_result': save_result,
        'cli': _cli,
        'nome': _nome,
        'num': _num,
        'dt': _dt,
        'emit_cli': _emit_cli,
        'emit_nome': _emit_nome,
        'emit_num': _emit_num,
    }


def _classify_xml(content: str) -> dict:
    """Classifica um XML fiscal e extrai metadados para tratamento inteligente.

    Retorna dict:
        tipo: 'nfe'|'nfce'|'cancelamento'|'cce'|'manifestacao'|'evento_outro'
              |'cte'|'desconhecido'
        root_tag, chave_nfe, tp_evento, descr_evento, seq_evento,
        dh_evento (datetime|None), dest_cnpj_digits
    """
    out: dict = {
        'tipo': 'desconhecido',
        'root_tag': '',
        'chave_nfe': '',
        'tp_evento': '',
        'descr_evento': '',
        'seq_evento': 1,
        'dh_evento': None,
        'dest_cnpj_digits': '',
    }
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return out

    raw_tag = root.tag
    tag = raw_tag.split('}')[-1] if '}' in raw_tag else raw_tag
    out['root_tag'] = tag

    # CT-e — deixar em NOVO
    if tag in _CTE_ROOT_TAGS:
        out['tipo'] = 'cte'
        return out

    # NF-e / NFC-e — detecta modelo (55 vs 65)
    if tag in ('nfeProc', 'NFe'):
        mod = ''
        for xpath in [f'.//{{{_NFE_NS}}}mod', './/mod']:
            el = root.find(xpath)
            if el is not None and el.text:
                mod = el.text.strip()
                break
        out['tipo'] = 'nfce' if mod == '65' else 'nfe'
        return out

    # Eventos NF-e
    if tag in _NFE_EVENT_ROOT_TAGS:
        # chNFe
        for xpath in [f'.//{{{_NFE_NS}}}chNFe', './/chNFe']:
            el = root.find(xpath)
            if el is not None and el.text:
                out['chave_nfe'] = re.sub(r'\D', '', el.text.strip())
                break

        # tpEvento
        for xpath in [f'.//{{{_NFE_NS}}}tpEvento', './/tpEvento']:
            el = root.find(xpath)
            if el is not None and el.text:
                out['tp_evento'] = el.text.strip()
                break

        # nSeqEvento
        for xpath in [f'.//{{{_NFE_NS}}}nSeqEvento', './/nSeqEvento']:
            el = root.find(xpath)
            if el is not None and el.text:
                try:
                    out['seq_evento'] = int(el.text.strip())
                except ValueError:
                    pass
                break

        # dhEvento / dhRegEvento
        for xpath in [f'.//{{{_NFE_NS}}}dhEvento', './/dhEvento',
                      f'.//{{{_NFE_NS}}}dhRegEvento', './/dhRegEvento']:
            el = root.find(xpath)
            if el is not None and el.text:
                try:
                    out['dh_evento'] = datetime.fromisoformat(
                        el.text.strip().replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    pass
                break

        # CNPJ para filtro de empresa/grupo
        for xpath in [f'.//{{{_NFE_NS}}}CNPJ', './/CNPJ',
                      f'.//{{{_NFE_NS}}}CNPJDest', './/CNPJDest']:
            el = root.find(xpath)
            if el is not None and el.text:
                digits = re.sub(r'\D', '', el.text.strip())
                if len(digits) >= 11:
                    out['dest_cnpj_digits'] = digits
                    break

        tp = out['tp_evento']
        out['descr_evento'] = _TPEVENTO_DESCR.get(tp, f'Evento {tp}' if tp else 'Evento desconhecido')

        if tp in _TPEVENTO_CANCELAMENTO:
            out['tipo'] = 'cancelamento'
        elif tp in _TPEVENTO_CCE:
            out['tipo'] = 'cce'
        elif tp in _TPEVENTO_MANIFESTACAO:
            out['tipo'] = 'manifestacao'
        else:
            out['tipo'] = 'evento_outro'

        return out

    return out  # desconhecido — tenta parse_nfe_xml como fallback


def _marcar_cancelada(chave_nfe: str) -> int:
    """Marca NF-e(s) com a chave como canceladas. Retorna quantas linhas foram marcadas."""
    if not chave_nfe:
        return 0
    execute_query(
        "UPDATE nfe_importacoes SET cancelada = 1 WHERE chave_acesso = %s",
        (chave_nfe,), fetch=False,
    )
    row = execute_query(
        "SELECT COUNT(*) AS cnt FROM nfe_importacoes WHERE chave_acesso = %s AND cancelada = 1",
        (chave_nfe,), fetch=True, fetch_one=True,
    ) or {}
    return int(row.get('cnt', 0))


def _salvar_evento(chave_nfe: str, tp_evento: str, descr_evento: str,
                   seq_evento: int, dh_evento, xml_raw: str,
                   nome_arquivo: str) -> None:
    """Persiste evento (CC-e ou outro relevante) em nfe_eventos, vinculando à NF-e se encontrada."""
    nfe_id = None
    if chave_nfe:
        row = execute_query(
            "SELECT id FROM nfe_importacoes WHERE chave_acesso = %s LIMIT 1",
            (chave_nfe,), fetch=True, fetch_one=True,
        )
        if row:
            nfe_id = row['id']
    execute_query(
        """INSERT INTO nfe_eventos
               (nfe_id, chave_nfe, tp_evento, descricao_evento,
                seq_evento, dh_evento, xml_raw, nome_arquivo)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (nfe_id, chave_nfe, tp_evento, descr_evento,
         seq_evento, dh_evento, (xml_raw or '')[:_MAX_XML_SIZE], nome_arquivo),
        fetch=False,
    )


def _process_evento(clf: dict, file_name: str, xml_raw: str,
                    cnpj_cache: dict, now) -> dict:
    """Executa operações de banco para um evento NF-e e determina pasta destino.

    Retorna dict: empresa_nome, empresa_num, dt, detalhe.
    """
    _ev_nome = None
    _ev_num = None
    chave = clf.get('chave_nfe', '')
    tp = clf.get('tp_evento', '')
    descr = clf.get('descr_evento', f'Evento {tp}')
    dh = clf.get('dh_evento') or now

    # Busca empresa pela chave NF-e no banco (mais confiável)
    if chave:
        ev_rec = execute_query(
            "SELECT c.nome_razao_social, c.numero_cliente "
            "FROM nfe_importacoes n "
            "JOIN clientes c ON c.id = n.cliente_id "
            "WHERE n.chave_acesso = %s LIMIT 1",
            (chave,), fetch=True, fetch_one=True,
        )
        if ev_rec:
            _ev_nome = ev_rec['nome_razao_social']
            _ev_num = ev_rec.get('numero_cliente') or None

    # Fallback: busca empresa pelo CNPJ extraído do evento
    if not _ev_nome:
        cnpj_dig = clf.get('dest_cnpj_digits', '')
        if cnpj_dig and len(cnpj_dig) >= 11:
            ev_found = _find_cliente_by_doc_digits(cnpj_dig, cnpj_cache)
            if ev_found:
                _ev_nome = ev_found['nome_razao_social']
                _ev_num = ev_found.get('numero_cliente') or None

    # Empresa não identificada → deixa em NOVO (skip)
    if not _ev_nome:
        return {'empresa_nome': None, 'empresa_num': None, 'dt': dh, 'detalhe': descr}

    # Operação de banco conforme tipo
    tipo = clf.get('tipo', 'evento_outro')
    if tipo == 'cancelamento' and chave:
        cnt = _marcar_cancelada(chave)
        detalhe = f'{descr} — {"NF-e cancelada" if cnt else "NF-e não encontrada no sistema"}'
    elif tipo == 'cce' and chave:
        _salvar_evento(chave, tp, descr, clf.get('seq_evento', 1), dh, xml_raw, file_name)
        detalhe = f'{descr} — registrada no sistema'
    else:
        detalhe = descr

    return {
        'empresa_nome': _ev_nome,
        'empresa_num':  _ev_num,
        'dt':           dh,
        'detalhe':      detalhe,
    }


def _auto_vincular(emit_cnpj: str, codigo_produto: str, cliente_id, grupo_id,
                   cache: dict | None = None, tipo: str = 'entrada'):
    """
    Tenta encontrar um vínculo automático registrado para o par emit_cnpj + codigo_produto.
    Busca na ordem: empresa específica → grupo → ramo de atividade → global.
    O parâmetro `cache` (dict mutável) permite reutilizar resultados dentro de um lote
    de importação, eliminando consultas DB repetidas para o mesmo par. ``tipo`` escopa
    o vínculo por entrada (Compras) x saída — não misturam.
    """
    if not emit_cnpj or not codigo_produto:
        return None

    cache_key = (emit_cnpj, codigo_produto, cliente_id, grupo_id, tipo)
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    result = _auto_vincular_db(emit_cnpj, codigo_produto, cliente_id, grupo_id, tipo)

    if cache is not None:
        cache[cache_key] = result
    return result


def _auto_vincular_db(emit_cnpj: str, codigo_produto: str, cliente_id, grupo_id,
                      tipo: str = 'entrada'):
    """
    Tenta encontrar um vínculo automático registrado para o par emit_cnpj + codigo_produto.
    Busca na ordem: empresa específica → grupo.

    Regras de ramo de atividade e globais NÃO são mais resolvidas — mesmo que as
    linhas ainda existam na tabela (limpeza é a Fase 2). Era esse fallback que
    fazia o de-para de um cliente classificar as notas de todos os outros.
    """
    if not emit_cnpj or not codigo_produto:
        return None

    # 1. Empresa/grupo específico
    for cli, grp in [
        (cliente_id, None),
        (None, grupo_id),
    ]:
        if cli is None and grp is None:
            continue
        cli_cond = '= %s' if cli is not None else 'IS NULL'
        grp_cond = '= %s' if grp is not None else 'IS NULL'
        query = (f"SELECT produto_catalogo_id FROM nfe_produto_vinculo "
                 f"WHERE emit_cnpj = %s AND codigo_produto_xml = %s AND tipo = %s "
                 f"AND cliente_id {cli_cond} AND grupo_id {grp_cond} LIMIT 1")
        bind = [emit_cnpj, codigo_produto, tipo]
        if cli is not None:
            bind.append(cli)
        if grp is not None:
            bind.append(grp)
        row = execute_query(query, tuple(bind), fetch=True, fetch_one=True)
        if row:
            return row['produto_catalogo_id']

    return None


def _auto_vincular_batch(emit_cnpj: str, codigos: list, cliente_id, grupo_id,
                         tipo: str = 'entrada') -> dict:
    """
    Versão batch de _auto_vincular_db: recebe uma lista de codigos_produto e
    retorna um dict {codigo_produto: produto_catalogo_id} em 1 query ao invés
    de disparar N consultas sequenciais.
    Respeita a mesma prioridade: empresa(1) → grupo(2). Ramo e global não são
    mais resolvidos (Fase 1) — sem empresa nem grupo não há o que reaplicar.
    """
    if not emit_cnpj or not codigos:
        return {}
    if not cliente_id and not grupo_id:
        return {}

    # Query única: escopos como OR, prioridade via CASE.
    # Os parâmetros seguem a ORDEM DE APARIÇÃO no SQL: o CASE fica no SELECT,
    # portanto vem antes dos do WHERE (emit_cnpj/códigos) e dos do escopo.
    ph_c = ','.join(['%s'] * len(codigos))
    case_parts, case_params = [], []
    scope_or_parts, scope_params = [], []

    if cliente_id:
        case_parts.append("WHEN cliente_id = %s AND grupo_id IS NULL AND ramo_atividade_id IS NULL THEN 1")
        case_params.append(cliente_id)
        scope_or_parts.append("(cliente_id = %s AND grupo_id IS NULL AND ramo_atividade_id IS NULL)")
        scope_params.append(cliente_id)

    if grupo_id:
        case_parts.append("WHEN grupo_id = %s AND cliente_id IS NULL AND ramo_atividade_id IS NULL THEN 2")
        case_params.append(grupo_id)
        scope_or_parts.append("(grupo_id = %s AND cliente_id IS NULL AND ramo_atividade_id IS NULL)")
        scope_params.append(grupo_id)

    case_sql = "CASE " + " ".join(case_parts) + " ELSE 9 END"
    scope_sql = " OR ".join(scope_or_parts)
    # ORDEM dos params segue a aparição no SQL: CASE (SELECT) -> emit -> códigos ->
    # tipo -> escopo (WHERE).
    params = case_params + [emit_cnpj] + list(codigos) + [tipo] + scope_params

    rows = execute_query(
        f"SELECT codigo_produto_xml, produto_catalogo_id, {case_sql} AS priority "
        f"FROM nfe_produto_vinculo "
        f"WHERE emit_cnpj = %s AND codigo_produto_xml IN ({ph_c}) "
        f"AND tipo = %s "
        f"AND produto_catalogo_id IS NOT NULL "
        f"AND ({scope_sql}) "
        f"ORDER BY priority",
        tuple(params),
        fetch=True,
    ) or []

    # Keep highest-priority (lowest number) match per codigo
    result: dict = {}
    for r in rows:
        cod = r['codigo_produto_xml']
        if cod not in result:
            result[cod] = r['produto_catalogo_id']
    return result


# ===========================================================================
# Conferência de Saídas
# ===========================================================================

@escrita_fiscal.route('/conf-saidas/')
@permission_required('escrita_fiscal.conf_saidas')
def conf_saidas():
    empresas = _get_empresas()
    grupos = _get_grupos()
    # Destinatários e UFs vêm de /conf-saidas/api/opcoes-filtros ao escolher a
    # empresa (antes era um DISTINCT global sobre todas as saídas).
    dropbox_ok = dropbox_sync.is_configured()
    stats = {'total_notas': 0, 'total_valor': 0, 'total_icms': 0,
             'total_pis': 0, 'total_cofins': 0}
    return render_template(
        'escrita_fiscal/conf_saidas.html',
        stats=stats,
        empresas=empresas,
        grupos=grupos,
        # Só admin enxerga o botão de excluir (o gate real está na rota).
        is_admin=current_user.is_admin(),
        dropbox_configured=dropbox_ok,
        dropbox_folder=Config.DROPBOX_XML_FOLDER,
    )


# ---------------------------------------------------------------------------
# Painel do Q-Robô — Fase 2.1: MONITOR, somente leitura.
# Nenhuma escrita em robo_config aqui. Ligar/desligar, data de captura,
# reprocessar histórico, token e cadastro de posto entram nas fases seguintes.
# ---------------------------------------------------------------------------

# Semáforo: a regra mora em utils/qrobo_status.py e é a MESMA que o portal usa
# (o instalador precisa ver o mesmo verde/amarelo/vermelho que você vê aqui).
# Os nomes locais ficam como apelido para não mexer no resto do arquivo.
_QROBO_VERDE   = qrobo_status.LIMIAR_VERDE
_QROBO_LARANJA = qrobo_status.LIMIAR_LARANJA
_qrobo_ha      = qrobo_status.ha
_qrobo_status  = qrobo_status.classificar


def _qrobo_dt(valor, com_hora=True):
    """Formata datetime/date vindo do banco (já em BRT) para dd/mm/aaaa HH:MM."""
    if not hasattr(valor, 'strftime'):
        return None
    return valor.strftime('%d/%m/%Y %H:%M' if com_hora else '%d/%m/%Y')


_QROBO_AUD_FILTROS = {
    'chaves': (qrobo_chaves.ACAO_GERADA, qrobo_chaves.ACAO_REGERADA),
    'downloads': (qrobo_chaves.ACAO_DOWNLOAD,),
}


def _qrobo_painel_contexto(**extra):
    """Monta o contexto do painel (monitor + auditoria). Só leitura."""
    postos = []
    resumo = {'total': 0, 'verde': 0, 'amarelo': 0, 'vermelho': 0, 'cinza': 0,
              'saidas': 0, 'desligados': 0}
    for r in RoboConfig.listar_painel():
        cls, rotulo = _qrobo_status(r.get('min_sem_contato'))
        total_saidas = int(r.get('total_saidas') or 0)
        ativo = bool(r.get('ativo'))
        postos.append({
            'cliente_id':     r['cliente_id'],
            'numero':         r.get('numero_cliente') or '—',
            'razao':          r.get('nome_razao_social') or '(cliente removido)',
            'status_cls':     cls,
            'status_txt':     rotulo,
            'ultimo_contato': _qrobo_dt(r.get('robo_ultimo_contato')),
            'ultima_captura': _qrobo_dt(r.get('ultima_captura')),
            'captura_ha':     _qrobo_ha(r.get('min_ultima_captura')),
            'data_captura':   _qrobo_dt(r.get('data_inicio_captura'), com_hora=False),
            'ativo':          ativo,
            'total_saidas':   total_saidas,
            'reset_seq':      int(r.get('robo_reset_seq') or 0),
            # usados só para ordenar o painel (Bloco D4)
            'min_sem_contato': r.get('min_sem_contato'),
            'contato_ha':      _qrobo_ha(r.get('min_sem_contato')),
        })
        resumo['total']  += 1
        resumo[cls]      += 1
        resumo['saidas'] += total_saidas
        if not ativo:
            resumo['desligados'] += 1

    # Bloco D4 — quem exige ação primeiro. Parado e sem-contato no topo, depois
    # atenção, por último os que estão capturando. Dentro do mesmo estado, o
    # silêncio mais longo primeiro. Só ordenação em Python: nenhuma query nova.
    _ordem = {'vermelho': 0, 'cinza': 1, 'amarelo': 2, 'verde': 3}
    postos.sort(key=lambda p: (_ordem.get(p['status_cls'], 9),
                               -(p['min_sem_contato'] or 0),
                               p['razao']))

    # ---- Auditoria do Portal do Instalador --------------------------------
    filtro = (request.args.get('aud') or 'todas').strip()
    acoes = _QROBO_AUD_FILTROS.get(filtro)
    trilha = qrobo_chaves.historico_geral(limite=300)
    if acoes:
        trilha = [t for t in trilha if t['acao'] in acoes]
    aud_resumo = {
        'chaves': sum(1 for t in trilha if t['acao'] in _QROBO_AUD_FILTROS['chaves']),
        'downloads': sum(1 for t in trilha if t['acao'] == qrobo_chaves.ACAO_DOWNLOAD),
    }

    ctx = {
        'postos': postos, 'resumo': resumo,
        'limiar_verde': _QROBO_VERDE, 'limiar_laranja': _QROBO_LARANJA,
        'trilha': trilha, 'aud_filtro': filtro, 'aud_resumo': aud_resumo,
        'hoje': datetime.now(ZoneInfo('America/Sao_Paulo')).date().isoformat(),
        'csrf_token': _qrobo_csrf_token(),
        'confirmacao': None, 'chave': None,
    }
    ctx.update(extra)
    return ctx


@escrita_fiscal.route('/conf-saidas/q-robo')
@permission_required('escrita_fiscal.q_robo')
def q_robo_painel():
    """Monitor dos robôs + auditoria do Portal do Instalador (somente leitura)."""
    return render_template('escrita_fiscal/q_robo.html', **_qrobo_painel_contexto())


# ---------------------------------------------------------------------------
# Geração de chave PELO PAINEL (origem='ADMIN')
#
# Mesmo serviço do portal (utils/qrobo_chaves) — a trava do número, a data que
# nunca fica NULL e a auditoria atômica são as mesmas. Muda só a origem
# registrada na trilha e a tela. O CSRF é o mesmo do portal: gerar chave é ação
# sensível, e reusar o helper custa uma linha.
# ---------------------------------------------------------------------------
def _qrobo_csrf_token():
    from routes.qrobo import csrf_token
    return csrf_token()


def _qrobo_csrf_ok():
    from routes.qrobo import csrf_valido
    return csrf_valido()


@escrita_fiscal.route('/conf-saidas/q-robo/resolver', methods=['POST'])
@permission_required('escrita_fiscal.q_robo')
def q_robo_resolver():
    """Número -> razão social para conferência. NÃO gera nada."""
    if not _qrobo_csrf_ok():
        flash('Formulário expirado. Tente de novo.', 'danger')
        return redirect(url_for('escrita_fiscal.q_robo_painel'))

    numero = (request.form.get('numero') or '').strip()
    cliente_id = (request.form.get('cliente_id') or '').strip()
    # Escolha na busca vem por id (sem ambiguidade); digitar mantém a trava do
    # número exato.
    res = (qrobo_chaves.resolver_cliente_id(cliente_id) if cliente_id
           else qrobo_chaves.resolver_numero(numero))
    if not res['ok']:
        msgs = {'numero_vazio': 'Informe o número do cliente.',
                'cliente_inexistente': 'Cliente não encontrado.',
                'nao_encontrado': f'Nenhum cliente com o número {numero}. '
                                  'O número é exato ("023" ≠ "23").',
                'ambiguo': f'Mais de um cliente com o número {numero} — '
                           'resolva o cadastro antes. Nada foi gerado.'}
        flash(msgs.get(res['erro'], 'Não foi possível localizar o cliente.'), 'danger')
        return redirect(url_for('escrita_fiscal.q_robo_painel'))

    return render_template('escrita_fiscal/q_robo.html',
                           **_qrobo_painel_contexto(confirmacao=res))


@escrita_fiscal.route('/conf-saidas/q-robo/gerar', methods=['POST'])
@permission_required('escrita_fiscal.q_robo')
def q_robo_gerar():
    if not _qrobo_csrf_ok():
        flash('Formulário expirado. Tente de novo.', 'danger')
        return redirect(url_for('escrita_fiscal.q_robo_painel'))

    numero = (request.form.get('numero') or '').strip()
    cliente_id = (request.form.get('cliente_id') or '').strip()
    data_inicio = (request.form.get('data_inicio') or '').strip()
    confirmou = request.form.get('confirmar_regeracao') == '1'

    # Reconfere pelo id e exige que o número ainda seja o que apareceu na tela
    # (formulário adulterado / cadastro alterado durante a conferência).
    res = qrobo_chaves.resolver_cliente_id(cliente_id)
    if not res['ok'] or (res['cliente']['numero_cliente'] or '') != numero:
        logger.warning('[q-robo/admin] gerar abortado: cliente_id=%r x numero=%r',
                       cliente_id, numero)
        flash('A conferência não bateu com o cadastro. Recomece — nada foi gerado.',
              'danger')
        return redirect(url_for('escrita_fiscal.q_robo_painel'))

    ja_tem = res['robo'] is not None
    if ja_tem and not confirmou:
        flash('Este posto já tem chave. Marque a confirmação para substituir.', 'warning')
        return render_template('escrita_fiscal/q_robo.html',
                               **_qrobo_painel_contexto(confirmacao=res,
                                                        faltou_confirmar=True))

    ip, ua = qrobo_chaves.contexto_request()
    r = qrobo_chaves.gerar_chave(
        res['cliente']['cliente_id'], current_user.id, current_user.nome,
        regerar=ja_tem, data_inicio=data_inicio or None,
        origem=qrobo_chaves.ORIGEM_ADMIN, ip=ip, user_agent=ua)

    if not r['ok']:
        msgs = {'data_futura': 'A data de início não pode ser no futuro.',
                'data_invalida': 'Data de início inválida.',
                'ja_existe': 'Este posto passou a ter chave agora há pouco. '
                             'Confira e confirme de novo.',
                'cliente_inexistente': 'Cliente não encontrado.'}
        flash(msgs.get(r['erro'], 'Não foi possível gerar a chave.'), 'danger')
        return render_template('escrita_fiscal/q_robo.html',
                               **_qrobo_painel_contexto(confirmacao=res))

    # Queima o token: F5 na tela da chave não gera outra.
    from routes.qrobo import _rotaciona_csrf
    _rotaciona_csrf()
    # AUDITORIA (D2): ação MANUAL de um usuário logado gerando/regerando a chave do
    # Q-Robô (a importação AUTOMÁTICA do scheduler não passa por aqui e não loga).
    registrar('escrita.gerou_chave_robo', 'fiscal', tabela='robo_config',
              registro_id=res['cliente']['cliente_id'],
              depois={'numero': numero, 'cliente_id': res['cliente']['cliente_id'],
                      'data_inicio': data_inicio or None, 'acao': r.get('acao'),
                      'versao': r.get('versao'),
                      **rotulo_empresa(res['cliente']['cliente_id'])})
    logger.info('[q-robo/admin] %s por %s (id=%s) para cliente_id=%s versao=%s',
                r['acao'], current_user.nome, current_user.id,
                res['cliente']['cliente_id'], r['versao'])
    return render_template('escrita_fiscal/q_robo.html',
                           **_qrobo_painel_contexto(chave=r))


@escrita_fiscal.route('/conf-saidas/api/notas')
@login_required
def api_notas_saidas():
    f_cliente_id  = request.args.get('cliente_id', '').strip()
    f_grupo_id    = request.args.get('grupo_id', '').strip()
    f_dest_cnpj   = _filtro_lista(request.args.get('dest_cnpj', ''))
    f_data_ini    = request.args.get('data_ini', '').strip()
    f_data_fim    = request.args.get('data_fim', '').strip()
    f_chave       = request.args.get('chave', '').strip()
    f_num_nota    = request.args.get('num_nota', '').strip()
    f_cfop        = request.args.get('cfop', '').strip()
    f_dest_uf     = _filtro_lista(request.args.get('dest_uf', ''))
    f_emit_cnpj   = request.args.get('emit_cnpj', '').strip()
    f_vmin        = request.args.get('vmin', '').strip()
    f_vmax        = request.args.get('vmax', '').strip()
    f_origem      = request.args.get('origem', '').strip()
    f_cancelado   = request.args.get('cancelado', '').strip()
    f_vinc_status = request.args.get('vinc_status', '').strip()
    page          = max(1, int(request.args.get('page', 1)))
    per_page      = 50

    # AUDITORIA (D2): leitura — busca de Saídas (só 1ª página com termo/filtro).
    _termo = f_chave or f_num_nota
    _filtros = {k: v for k, v in (
        ('cliente_id', f_cliente_id), ('grupo_id', f_grupo_id),
        ('dest_cnpj', request.args.get('dest_cnpj', '').strip()),
        ('data_ini', f_data_ini), ('data_fim', f_data_fim), ('cfop', f_cfop),
        ('dest_uf', request.args.get('dest_uf', '').strip()),
        ('emit_cnpj', f_emit_cnpj), ('vmin', f_vmin), ('vmax', f_vmax),
        ('origem', f_origem), ('cancelado', f_cancelado),
        ('vinc_status', f_vinc_status)) if v}
    if page == 1 and (_termo or _filtros):
        _filtros.update(rotulo_empresa(f_cliente_id, f_grupo_id))
        registrar('leitura.buscou_saidas', 'fiscal', tabela='nfe_importacoes',
                  depois={'termo': _termo or None, 'filtros': _filtros})

    extra_clauses, params = _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=[])
    where = ["n.tipo = 'saida'"] + extra_clauses

    if f_dest_cnpj:
        # Vem do <select>: CNPJ exato, nao mais busca parcial.
        where.append(_clausula_in('n.dest_cnpj', f_dest_cnpj, params))
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    if f_chave:
        where.append('n.chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_num_nota:
        where.append('n.num_nota = %s')
        params.append(f_num_nota)
    if f_cfop:
        where.append('n.cfop LIKE %s')
        params.append(f'{f_cfop}%')
    if f_dest_uf:
        where.append(_clausula_in('n.dest_uf', f_dest_uf, params))
    if f_emit_cnpj:
        where.append('n.emit_cnpj LIKE %s')
        params.append(f'%{f_emit_cnpj}%')
    if f_vmin:
        where.append('n.valor_total >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('n.valor_total <= %s')
        params.append(float(f_vmax))
    if f_origem == 'SEFAZ':
        where.append("n.origem = 'SEFAZ'")
    elif f_origem == 'MANUAL':
        where.append("n.origem IN ('UPLOAD','DROPBOX')")
    elif f_origem:
        where.append('n.origem = %s')
        params.append(f_origem)
    _aplica_cancelada(where, f_cancelado, 'n')
    if f_vinc_status == 'completo':
        where.append(
            "NOT EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NULL)"
            " AND EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id)"
        )
    elif f_vinc_status == 'parcial':
        where.append(
            "EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NOT NULL)"
            " AND EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NULL)"
        )
    elif f_vinc_status == 'sem':
        where.append(
            "NOT EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NOT NULL)"
        )
    elif f_vinc_status == 'incompleto':
        where.append(
            "EXISTS (SELECT 1 FROM nfe_itens i WHERE i.nfe_id = n.id AND i.produto_catalogo_id IS NULL)"
        )

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    offset = (page - 1) * per_page

    all_rows = execute_query(
        f"""SELECT n.id, n.chave_acesso, n.num_nota, n.serie, n.data_emissao,
                   n.emit_cnpj, n.emit_nome, n.emit_uf,
                   n.dest_cnpj, n.dest_nome, n.dest_uf,
                   n.valor_total, n.valor_icms, n.valor_pis, n.valor_cofins, n.valor_ipi,
                   n.cfop, n.natureza_operacao, n.origem, n.incompleta, n.cancelada,
                   n.nome_arquivo,
                   n.importado_em, n.cliente_id, n.grupo_id,
                   c.nome_razao_social AS empresa_nome,
                   g.nome AS grupo_nome,
                   COALESCE(ic.qtd_itens, 0) AS qtd_itens,
                   COALESCE(ic.itens_vinculados, 0) AS itens_vinculados,
                   COUNT(*) OVER() AS _total,
                   COALESCE(SUM(n.valor_total) OVER(), 0) AS _kpi_valor,
                   COALESCE(SUM(n.valor_icms)  OVER(), 0) AS _kpi_icms,
                   COALESCE(SUM(n.valor_pis)   OVER(), 0) AS _kpi_pis,
                   COALESCE(SUM(n.valor_cofins) OVER(), 0) AS _kpi_cofins
              FROM nfe_importacoes n
              LEFT JOIN clientes c ON c.id = n.cliente_id
              LEFT JOIN grupos_clientes g ON g.id = n.grupo_id
              LEFT JOIN (
                  SELECT nfe_id,
                         COUNT(*) AS qtd_itens,
                         COUNT(produto_catalogo_id) AS itens_vinculados
                    FROM nfe_itens
                   GROUP BY nfe_id
              ) ic ON ic.nfe_id = n.id
              {where_sql}
             ORDER BY n.data_emissao DESC, n.id DESC
             LIMIT %s OFFSET %s""",
        tuple(params) + (per_page, offset),
        fetch=True,
    ) or []

    first = all_rows[0] if all_rows else {}
    total = int(first.get('_total') or 0)
    kpi = {
        'total_valor':  float(first.get('_kpi_valor') or 0),
        'total_icms':   float(first.get('_kpi_icms')  or 0),
        'total_pis':    float(first.get('_kpi_pis')   or 0),
        'total_cofins': float(first.get('_kpi_cofins') or 0),
    }
    if not all_rows:
        total = 0
        kpi = {'total_valor': 0, 'total_icms': 0, 'total_pis': 0, 'total_cofins': 0}

    # Hora de emissão (HH:MM:SS) das linhas da página. data_emissao é DATE (sem
    # hora), então a hora sai do dhEmi do xml_raw (formato 'AAAA-MM-DDThh:mm:ss…',
    # a hora nos chars 12-19). Extraída só dos ids da página, por PK, para NÃO
    # varrer o xml_raw de todo o conjunto filtrado — a listagem tem COUNT(*) OVER(),
    # que bufferiza o filtro inteiro. Resumo sem xml → sem hora (NULL).
    horas = {}
    ids_pagina = [r['id'] for r in all_rows]
    if ids_pagina:
        ph = ','.join(['%s'] * len(ids_pagina))
        hrows = execute_query(
            f"SELECT id, CASE WHEN LOCATE('<dhEmi>', xml_raw) > 0 THEN "
            f"SUBSTRING(SUBSTRING_INDEX(SUBSTRING_INDEX(xml_raw,'<dhEmi>',-1),'</dhEmi>',1),12,8) "
            f"END AS hora_emissao FROM nfe_importacoes WHERE id IN ({ph})",
            tuple(ids_pagina), fetch=True) or []
        horas = {h['id']: h['hora_emissao'] for h in hrows}

    _window_cols = {'_total', '_kpi_valor', '_kpi_icms', '_kpi_pis', '_kpi_cofins'}
    rows = []
    for r in all_rows:
        row = {k: v for k, v in r.items() if k not in _window_cols}
        for k in ('data_emissao', 'importado_em'):
            if row.get(k) and hasattr(row[k], 'isoformat'):
                row[k] = row[k].isoformat()
        for k in ('valor_total', 'valor_icms', 'valor_pis', 'valor_cofins', 'valor_ipi'):
            row[k] = float(row.get(k) or 0)
        row['hora_emissao'] = horas.get(r['id'])
        rows.append(row)

    return jsonify({'total': total, 'page': page, 'per_page': per_page, 'rows': rows, 'kpi': kpi})


@escrita_fiscal.route('/conf-saidas/api/por-destinatario')
@login_required
def api_por_destinatario():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id   = request.args.get('grupo_id', '').strip()
    f_data_ini   = request.args.get('data_ini', '').strip()
    f_data_fim   = request.args.get('data_fim', '').strip()
    f_cancelado  = request.args.get('cancelado', '').strip()

    where = ["n.tipo = 'saida'"]
    extra, params = _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    _aplica_cancelada(where, f_cancelado, 'n')

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = execute_query(
        f"""SELECT n.dest_cnpj, n.dest_nome, n.dest_uf,
                   COUNT(*) AS qtd_notas,
                   SUM(n.valor_total)  AS total_valor,
                   SUM(n.valor_icms)   AS total_icms,
                   SUM(n.valor_pis)    AS total_pis,
                   SUM(n.valor_cofins) AS total_cofins
              FROM nfe_importacoes n {where_sql}
             GROUP BY n.dest_cnpj, n.dest_nome, n.dest_uf
             ORDER BY total_valor DESC""",
        tuple(params), fetch=True,
    ) or []

    for r in rows:
        for k in ('total_valor', 'total_icms', 'total_pis', 'total_cofins'):
            r[k] = float(r.get(k) or 0)

    return jsonify(rows)


@escrita_fiscal.route('/conf-saidas/api/por-produto')
@login_required
def api_por_produto_saidas():
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id   = request.args.get('grupo_id', '').strip()
    f_data_ini   = request.args.get('data_ini', '').strip()
    f_data_fim   = request.args.get('data_fim', '').strip()
    f_cancelado  = request.args.get('cancelado', '').strip()
    f_dest_cnpj  = _filtro_lista(request.args.get('dest_cnpj', ''))
    f_ncm        = request.args.get('ncm', '').strip()
    f_descricao  = request.args.get('descricao', '').strip()

    where = ["n.tipo = 'saida'"]
    extra, params = _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra)
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    _aplica_cancelada(where, f_cancelado, 'n')
    if f_dest_cnpj:
        where.append(_clausula_in('n.dest_cnpj', f_dest_cnpj, params))
    if f_ncm:
        where.append('i.ncm LIKE %s')
        params.append(f'{f_ncm}%')
    if f_descricao:
        where.append('i.descricao LIKE %s')
        params.append(f'%{f_descricao}%')

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows = execute_query(
        f"""SELECT i.codigo_produto, i.descricao, i.ncm, i.cfop, i.unidade,
                   i.produto_catalogo_id,
                   p.nome AS produto_catalogo_nome, p.categoria AS produto_categoria,
                   COUNT(DISTINCT n.id) AS qtd_notas,
                   SUM(i.quantidade)   AS total_qtd,
                   SUM(i.valor_total)  AS total_valor,
                   SUM(i.valor_icms)   AS total_icms,
                   SUM(i.valor_pis)    AS total_pis,
                   SUM(i.valor_cofins) AS total_cofins
              FROM nfe_itens i
              JOIN nfe_importacoes n ON n.id = i.nfe_id
              LEFT JOIN nfe_produtos_catalogo p ON p.id = i.produto_catalogo_id
              {where_sql}
             GROUP BY i.codigo_produto, i.descricao, i.ncm, i.cfop, i.unidade,
                      i.produto_catalogo_id, p.nome, p.categoria
             ORDER BY total_valor DESC
             LIMIT 500""",
        tuple(params), fetch=True,
    ) or []

    for r in rows:
        for k in ('total_qtd', 'total_valor', 'total_icms', 'total_pis', 'total_cofins'):
            r[k] = float(r.get(k) or 0)

    return jsonify(rows)


@escrita_fiscal.route('/conf-saidas/excluir-lote', methods=['POST'])
@login_required
def excluir_lote_saidas():
    """Exclui TODAS as notas de saída que batem com os filtros. SÓ ADMIN —
    mesmo gate do excluir_nfe, aplicado antes de montar qualquer WHERE."""
    if not current_user.is_admin():
        logger.warning('[excluir_lote_saidas] usuário %s (não-admin) tentou excluir em lote (saídas)',
                       getattr(current_user, 'id', '?'))
        return jsonify({'error': 'Apenas administradores podem excluir notas fiscais.'}), 403

    data = request.get_json(silent=True) or {}
    f_cliente_id = str(data.get('cliente_id', '')).strip()
    f_grupo_id   = str(data.get('grupo_id', '')).strip()
    f_dest_cnpj  = _filtro_lista(data.get('dest_cnpj', ''))
    f_data_ini   = str(data.get('data_ini', '')).strip()
    f_data_fim   = str(data.get('data_fim', '')).strip()
    f_chave      = str(data.get('chave', '')).strip()
    f_num_nota   = str(data.get('num_nota', '')).strip()
    f_cfop       = str(data.get('cfop', '')).strip()
    f_dest_uf    = _filtro_lista(data.get('dest_uf', ''))
    f_emit_cnpj  = str(data.get('emit_cnpj', '')).strip()
    f_vmin       = str(data.get('vmin', '')).strip()
    f_vmax       = str(data.get('vmax', '')).strip()
    f_origem     = str(data.get('origem', '')).strip()

    where = ["n.tipo = 'saida'"]
    extra_clauses, params = _empresa_where_saidas(f_cliente_id, f_grupo_id, alias='n', params=[])
    where.extend(extra_clauses)

    if f_dest_cnpj:
        where.append(_clausula_in('n.dest_cnpj', f_dest_cnpj, params))
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim)
    if f_chave:
        where.append('n.chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_num_nota:
        where.append('n.num_nota = %s')
        params.append(f_num_nota)
    if f_cfop:
        where.append('n.cfop LIKE %s')
        params.append(f'{f_cfop}%')
    if f_dest_uf:
        where.append(_clausula_in('n.dest_uf', f_dest_uf, params))
    if f_emit_cnpj:
        where.append('n.emit_cnpj LIKE %s')
        params.append(f'%{f_emit_cnpj}%')
    if f_vmin:
        where.append('n.valor_total >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('n.valor_total <= %s')
        params.append(float(f_vmax))
    if f_origem:
        where.append('n.origem = %s')
        params.append(f_origem)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    count_row = execute_query(
        f"SELECT COUNT(*) AS total FROM nfe_importacoes n {where_sql}",
        params, fetch=True, fetch_one=True,
    ) or {}
    total = int(count_row.get('total', 0))
    execute_query(f"DELETE n FROM nfe_importacoes n {where_sql}", params)
    return jsonify({'ok': True, 'deleted': total})

    return result


# ===========================================================================
# CONFERÊNCIA DE CT-e (fretes) — /escrita-fiscal/conf-cte/
#
# Tela ESPELHO da Conferência de Entradas, mas lendo de cte_documentos/cte_nfe.
# ROTA E ENDPOINT PRÓPRIOS (conf_cte): nada aqui reaproveita a rota de
# conf_compras — as duas telas são independentes.
#
# SOMENTE LEITURA. Não importa, não exclui, não manifesta (no CT-e nem existe
# manifestação). Quem grava é a captura (utils/integrations/cte_captura.py).
# ===========================================================================
@escrita_fiscal.route('/conf-cte/')
@permission_required('escrita_fiscal.conf_cte')
def conf_cte():
    empresas = _get_empresas()
    grupos = _get_grupos()
    # Transportadoras e UFs vêm de /conf-cte/api/opcoes-filtros ao escolher a
    # empresa (antes era um DISTINCT global sobre todos os CT-e).

    # KPIs começam zerados — o JS preenche ao buscar (igual à tela de Entradas).
    stats = {'total_ctes': 0, 'total_frete': 0, 'total_icms': 0,
             'total_cancelados': 0, 'total_nfes': 0}

    return render_template(
        'escrita_fiscal/conf_cte.html',
        stats=stats,
        empresas=empresas,
        grupos=grupos,
        # Só admin enxerga o botão de excluir (o gate real está na rota).
        is_admin=current_user.is_admin(),
    )


@escrita_fiscal.route('/conf-cte/api/ctes')
@login_required
def api_ctes():
    """Lista paginada de CT-e + KPIs da seleção (window functions, 1 round-trip)."""
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    f_emit_cnpj = _filtro_lista(request.args.get('emit_cnpj', ''))
    f_tomador = request.args.get('tomador_cnpj', '').strip()
    f_data_ini = request.args.get('data_ini', '').strip()
    f_data_fim = request.args.get('data_fim', '').strip()
    f_chave = request.args.get('chave', '').strip()
    f_num_cte = request.args.get('num_cte', '').strip()
    f_modelo = request.args.get('modelo', '').strip()
    f_uf_ini = _filtro_lista(request.args.get('uf_ini', ''))
    f_uf_fim = _filtro_lista(request.args.get('uf_fim', ''))
    f_vmin = request.args.get('vmin', '').strip()
    f_vmax = request.args.get('vmax', '').strip()
    f_origem = request.args.get('origem', '').strip()
    f_cancelado = request.args.get('cancelado', '').strip()
    f_papel = request.args.get('papel', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 50

    # AUDITORIA (D2): leitura — busca de CT-e (só 1ª página com termo/filtro).
    _termo = f_chave or f_num_cte
    _filtros = {k: v for k, v in (
        ('cliente_id', f_cliente_id), ('grupo_id', f_grupo_id),
        ('emit_cnpj', request.args.get('emit_cnpj', '').strip()), ('tomador_cnpj', f_tomador),
        ('data_ini', f_data_ini), ('data_fim', f_data_fim), ('modelo', f_modelo),
        ('uf_ini', request.args.get('uf_ini', '').strip()),
        ('uf_fim', request.args.get('uf_fim', '').strip()),
        ('vmin', f_vmin), ('vmax', f_vmax), ('origem', f_origem),
        ('cancelado', f_cancelado), ('papel', f_papel)) if v}
    if page == 1 and (_termo or _filtros):
        _filtros.update(rotulo_empresa(f_cliente_id, f_grupo_id))
        registrar('leitura.buscou_ctes', 'fiscal', tabela='cte_documentos',
                  depois={'termo': _termo or None, 'filtros': _filtros})

    where, params = _empresa_where_cte(f_cliente_id, f_grupo_id, alias='t', params=[])

    if f_emit_cnpj:
        where.append(_clausula_in('t.emit_cnpj', f_emit_cnpj, params))
    if f_tomador:
        where.append("REPLACE(REPLACE(REPLACE(t.tomador_cnpj,'.',''),'/',''),'-','') LIKE %s")
        params.append('%' + re.sub(r'\D', '', f_tomador) + '%')
    if f_data_ini:
        where.append('t.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        where.append('t.data_emissao <= %s')
        params.append(f_data_fim)
    if f_chave:
        where.append('t.chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_num_cte:
        where.append('t.num_cte = %s')
        params.append(f_num_cte)
    if f_modelo:
        where.append('t.modelo = %s')
        params.append(f_modelo)
    if f_uf_ini:
        where.append(_clausula_in('t.uf_ini', f_uf_ini, params))
    if f_uf_fim:
        where.append(_clausula_in('t.uf_fim', f_uf_fim, params))
    if f_vmin:
        where.append('t.valor_frete >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('t.valor_frete <= %s')
        params.append(float(f_vmax))
    if f_origem == 'SEFAZ':
        where.append("t.origem = 'SEFAZ'")
    elif f_origem == 'MANUAL':
        where.append("t.origem IN ('UPLOAD','DROPBOX')")
    elif f_origem:
        where.append('t.origem = %s')
        params.append(f_origem)
    _aplica_cancelada(where, f_cancelado, 't', 'cancelado')
    if f_papel:
        where.append('t.papel_cliente = %s')
        params.append(f_papel)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    offset = (page - 1) * per_page

    all_rows = execute_query(
        f"""SELECT t.id, t.chave_acesso, t.modelo, t.num_cte, t.serie,
                   t.data_emissao, t.cfop, t.natureza_operacao,
                   t.tp_cte, t.tp_serv, t.modal,
                   t.emit_cnpj, t.emit_nome, t.emit_uf,
                   t.rem_nome, t.rem_uf, t.dest_nome, t.dest_uf,
                   t.tomador_cnpj, t.tomador_nome, t.tomador_papel, t.papel_cliente,
                   t.uf_ini, t.mun_ini, t.uf_fim, t.mun_fim,
                   t.valor_frete, t.valor_icms, t.aliq_icms, t.cst_icms,
                   t.cancelado, t.origem, t.importado_em, t.atualizado_em,
                   t.cliente_id, t.grupo_id,
                   cl.nome_razao_social AS empresa_nome,
                   g.nome AS grupo_nome,
                   COALESCE(nf.qtd, 0) AS qtd_nfes,
                   -- Tem XML para exportar? O CT-e da captura guarda o arquivo no
                   -- Dropbox (xml_caminho) e deixa xml_raw vazio; qualquer um dos
                   -- dois serve. Sem isso a tela não sabe quando mostrar os botões.
                   (COALESCE(t.xml_raw, '') <> '' OR COALESCE(t.xml_caminho, '') <> '')
                       AS tem_xml,
                   COUNT(*) OVER() AS _total,
                   COALESCE(SUM(t.valor_frete) OVER(), 0) AS _kpi_frete,
                   COALESCE(SUM(t.valor_icms)  OVER(), 0) AS _kpi_icms,
                   COALESCE(SUM(t.cancelado)   OVER(), 0) AS _kpi_cancelados,
                   COALESCE(SUM(COALESCE(nf.qtd,0)) OVER(), 0) AS _kpi_nfes
              FROM cte_documentos t
              LEFT JOIN clientes cl ON cl.id = t.cliente_id
              LEFT JOIN grupos_clientes g ON g.id = t.grupo_id
              LEFT JOIN (
                  SELECT cte_id, COUNT(*) AS qtd FROM cte_nfe GROUP BY cte_id
              ) nf ON nf.cte_id = t.id
              {where_sql}
             ORDER BY t.data_emissao DESC, t.id DESC
             LIMIT %s OFFSET %s""",
        tuple(params) + (per_page, offset),
        fetch=True,
    ) or []

    first = all_rows[0] if all_rows else {}
    total = int(first.get('_total') or 0)
    kpi = {
        'total_frete': float(first.get('_kpi_frete') or 0),
        'total_icms': float(first.get('_kpi_icms') or 0),
        'total_cancelados': int(first.get('_kpi_cancelados') or 0),
        'total_nfes': int(first.get('_kpi_nfes') or 0),
    }
    if not all_rows:
        total = 0
        kpi = {'total_frete': 0, 'total_icms': 0, 'total_cancelados': 0, 'total_nfes': 0}

    _window_cols = {'_total', '_kpi_frete', '_kpi_icms', '_kpi_cancelados', '_kpi_nfes'}
    rows = []
    for r in all_rows:
        row = {k: v for k, v in r.items() if k not in _window_cols}
        for k in ('data_emissao', 'importado_em', 'atualizado_em'):
            if row.get(k) and hasattr(row[k], 'isoformat'):
                row[k] = row[k].isoformat()
        for k in ('valor_frete', 'valor_icms', 'aliq_icms'):
            row[k] = float(row.get(k) or 0)
        rows.append(row)

    return jsonify({'total': total, 'page': page, 'per_page': per_page,
                    'rows': rows, 'kpi': kpi})


@escrita_fiscal.route('/conf-cte/api/nfes/<int:cte_id>')
@login_required
def api_cte_nfes(cte_id):
    """NF-e transportadas por um CT-e — o "detalhe" da linha.

    Marca quais dessas chaves JÁ existem na Conferência de Entradas (LEFT JOIN por
    chave_acesso): é o cruzamento frete x nota que dá valor à tela.
    """
    cte = execute_query(
        "SELECT id, chave_acesso, modelo, num_cte, serie, data_emissao, "
        "       emit_cnpj, emit_nome, tomador_nome, tomador_papel, papel_cliente, "
        "       valor_frete, valor_icms, uf_ini, mun_ini, uf_fim, mun_fim, "
        "       natureza_operacao, cancelado, origem, xml_caminho "
        "FROM cte_documentos WHERE id = %s",
        (cte_id,), fetch=True, fetch_one=True,
    )
    if not cte:
        return jsonify({'erro': 'CT-e não encontrado'}), 404

    if cte.get('data_emissao') and hasattr(cte['data_emissao'], 'isoformat'):
        cte['data_emissao'] = cte['data_emissao'].isoformat()
    for k in ('valor_frete', 'valor_icms'):
        cte[k] = float(cte.get(k) or 0)

    nfes = execute_query(
        "SELECT n.id, n.chave_nfe, n.num_nota, n.serie, n.valor, "
        "       imp.id AS nfe_id, imp.num_nota AS imp_num, imp.serie AS imp_serie, "
        "       imp.emit_nome AS imp_emit, imp.data_emissao AS imp_data, "
        "       imp.valor_total AS imp_valor "
        "FROM cte_nfe n "
        "LEFT JOIN nfe_importacoes imp "
        "       ON imp.chave_acesso = n.chave_nfe AND imp.tipo = 'entrada' "
        "WHERE n.cte_id = %s ORDER BY n.id",
        (cte_id,), fetch=True,
    ) or []

    for n in nfes:
        if n.get('imp_data') and hasattr(n['imp_data'], 'isoformat'):
            n['imp_data'] = n['imp_data'].isoformat()
        for k in ('valor', 'imp_valor'):
            n[k] = float(n[k]) if n.get(k) is not None else None
        n['na_conferencia'] = n.get('nfe_id') is not None

    return jsonify({'cte': cte, 'nfes': nfes})


# ===========================================================================
# CONFERÊNCIA DE NFS-e  (/conf-nfse/)
#
# Espelha a de CT-e: mesma barra de empresa, mesmos KPIs, mesmos filtros, mesma
# paginação por window function num round-trip só.
#
# O QUE MUDA EM RELAÇÃO ÀS OUTRAS TELAS, e por quê:
#
# 1) A empresa NÃO é dona do documento, é PARTE dele. Na NF-e o cliente é
#    emitente ou destinatário; na NFS-e ele pode ser prestador, tomador OU
#    intermediário, e a mesma nota aparece para duas empresas da carteira com
#    papéis diferentes (a chave única é (chave_acesso, papel)). Por isso o
#    filtro de papel existe e a coluna aparece na tabela: sem ela, "500 notas"
#    não diz se o escritório prestou ou contratou.
#
# 2) NÃO há cancelado 0/1 — há ``situacao`` derivada de evento
#    ('ativa' | 'cancelada' | 'substituida'), com escritor único no
#    repositório. A tela só LÊ; nunca escreve situação.
#
# 3) Município é código IBGE cru. A tabela ``municipios`` do projeto tem duas
#    linhas e serve para outra coisa (link de prefeitura), então não há de onde
#    tirar o nome. Mostrar o código é honesto; inventar nome, não.
#
# SOMENTE LEITURA, como o CT-e. Nenhum endpoint de escrita aqui — e o ADN
# aceita eventos de manifestação que este sistema nunca envia.
# ===========================================================================
def _empresa_where_nfse(f_cliente_id, f_grupo_id, alias='n', params=None):
    """Filtro empresa/grupo para NFS-e.

    Sem o fallback por CNPJ que as outras telas têm: ``empresa_id`` é gravado
    pela captura a partir do cursor e nunca vem nulo. Fallback aqui seria código
    para um caso que não existe.
    """
    if params is None:
        params = []
    clauses = []
    if f_cliente_id:
        clauses.append(f'{alias}.empresa_id = %s')
        params.append(int(f_cliente_id))
    if f_grupo_id:
        clauses.append(
            f'{alias}.empresa_id IN (SELECT cliente_id FROM cliente_grupo_relacao'
            f'                        WHERE grupo_id = %s)')
        params.append(int(f_grupo_id))
    return clauses, params


@escrita_fiscal.route('/conf-nfse/')
@permission_required('escrita_fiscal.conf_nfse')
def conf_nfse():
    stats = {'total_nfse': 0, 'total_servicos': 0, 'total_iss': 0,
             'total_canceladas': 0, 'total_retencoes': 0}
    return render_template(
        'escrita_fiscal/conf_nfse.html',
        stats=stats,
        empresas=_get_empresas(),
        grupos=_get_grupos(),
    )


@escrita_fiscal.route('/conf-nfse/api/opcoes-filtros')
@login_required
def api_opcoes_filtros_nfse():
    """Prestadores e municípios que EXISTEM no escopo escolhido.

    Mesma ideia da de CT-e: sem escopo devolve vazio, para não varrer a base
    inteira montando um combo que ninguém pediu.
    """
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    if not f_cliente_id and not f_grupo_id:
        return jsonify({'prestadores': [], 'municipios': []})

    where, params = _empresa_where_nfse(f_cliente_id, f_grupo_id, alias='n', params=[])
    for campo, arg in (('n.data_emissao >= %s', 'data_ini'),
                       ('n.data_emissao <= %s', 'data_fim')):
        v = request.args.get(arg, '').strip()
        if v:
            where.append(campo)
            params.append(v if arg == 'data_ini' else v + ' 23:59:59')
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    prest = execute_query(
        f"""SELECT n.prestador_doc AS doc, MAX(n.prestador_nome) AS nome
              FROM nfse_capturadas n {where_sql}
             GROUP BY n.prestador_doc
            HAVING doc IS NOT NULL AND doc <> ''
             ORDER BY nome LIMIT 500""", tuple(params), fetch=True) or []
    muni = execute_query(
        f"""SELECT DISTINCT n.municipio_ibge AS m FROM nfse_capturadas n {where_sql}
            HAVING m IS NOT NULL AND m <> '' ORDER BY m""",
        tuple(params), fetch=True) or []
    return jsonify({'prestadores': prest, 'municipios': [r['m'] for r in muni]})


@escrita_fiscal.route('/conf-nfse/api/notas')
@login_required
def api_nfse():
    """Lista paginada de NFS-e + KPIs da seleção (window functions, 1 round-trip)."""
    f_cliente_id = request.args.get('cliente_id', '').strip()
    f_grupo_id = request.args.get('grupo_id', '').strip()
    f_prestador = _filtro_lista(request.args.get('prestador_doc', ''))
    f_tomador = request.args.get('tomador_doc', '').strip()
    f_data_ini = request.args.get('data_ini', '').strip()
    f_data_fim = request.args.get('data_fim', '').strip()
    f_chave = request.args.get('chave', '').strip()
    f_numero = request.args.get('numero', '').strip()
    f_municipio = _filtro_lista(request.args.get('municipio', ''))
    f_servico = request.args.get('codigo_servico', '').strip()
    f_vmin = request.args.get('vmin', '').strip()
    f_vmax = request.args.get('vmax', '').strip()
    f_papel = request.args.get('papel', '').strip()
    f_situacao = request.args.get('situacao', '').strip()
    page = max(1, int(request.args.get('page', 1)))
    per_page = 50

    # AUDITORIA (D2): leitura — mesma regra das outras telas (só 1ª página).
    _termo = f_chave or f_numero
    _filtros = {k: v for k, v in (
        ('cliente_id', f_cliente_id), ('grupo_id', f_grupo_id),
        ('prestador_doc', request.args.get('prestador_doc', '').strip()),
        ('tomador_doc', f_tomador), ('data_ini', f_data_ini), ('data_fim', f_data_fim),
        ('municipio', request.args.get('municipio', '').strip()),
        ('codigo_servico', f_servico), ('vmin', f_vmin), ('vmax', f_vmax),
        ('papel', f_papel), ('situacao', f_situacao)) if v}
    if page == 1 and (_termo or _filtros):
        _filtros.update(rotulo_empresa(f_cliente_id, f_grupo_id))
        registrar('leitura.buscou_nfse', 'fiscal', tabela='nfse_capturadas',
                  depois={'termo': _termo or None, 'filtros': _filtros})

    where, params = _empresa_where_nfse(f_cliente_id, f_grupo_id, alias='n', params=[])

    if f_prestador:
        where.append(_clausula_in('n.prestador_doc', f_prestador, params))
    if f_tomador:
        where.append("REPLACE(REPLACE(REPLACE(n.tomador_doc,'.',''),'/',''),'-','') LIKE %s")
        params.append('%' + re.sub(r'\D', '', f_tomador) + '%')
    if f_data_ini:
        where.append('n.data_emissao >= %s')
        params.append(f_data_ini)
    if f_data_fim:
        # data_emissao é DATETIME: sem o 23:59:59 o último dia do período ficaria
        # de fora, e o usuário veria "faltou nota" num filtro que ele fez certo.
        where.append('n.data_emissao <= %s')
        params.append(f_data_fim + ' 23:59:59')
    if f_chave:
        where.append('n.chave_acesso LIKE %s')
        params.append(f'%{f_chave}%')
    if f_numero:
        where.append('n.numero = %s')
        params.append(f_numero)
    if f_municipio:
        where.append(_clausula_in('n.municipio_ibge', f_municipio, params))
    if f_servico:
        where.append('n.codigo_servico LIKE %s')
        params.append(f'{f_servico}%')
    if f_vmin:
        where.append('n.valor_servicos >= %s')
        params.append(float(f_vmin))
    if f_vmax:
        where.append('n.valor_servicos <= %s')
        params.append(float(f_vmax))
    if f_papel:
        where.append('n.papel = %s')
        params.append(f_papel)
    if f_situacao:
        where.append('n.situacao = %s')
        params.append(f_situacao)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    offset = (page - 1) * per_page

    all_rows = execute_query(
        f"""SELECT n.id, n.chave_acesso, n.numero, n.serie, n.papel,
                   n.data_emissao, n.competencia, n.municipio_ibge,
                   n.prestador_doc, n.prestador_nome,
                   n.tomador_doc, n.tomador_nome, n.intermediario_doc,
                   n.codigo_servico, n.codigo_servico_mun, n.discriminacao,
                   n.valor_servicos, n.base_calculo, n.aliquota_iss, n.valor_iss,
                   n.total_retencoes, n.valor_liquido, n.iss_retido,
                   n.situacao, n.cstat, n.restricao_eventos, n.criado_em,
                   n.empresa_id, cl.nome_razao_social AS empresa_nome,
                   COUNT(*) OVER() AS _total,
                   COALESCE(SUM(n.valor_servicos)  OVER(), 0) AS _kpi_servicos,
                   COALESCE(SUM(n.valor_iss)       OVER(), 0) AS _kpi_iss,
                   COALESCE(SUM(n.total_retencoes) OVER(), 0) AS _kpi_ret,
                   COALESCE(SUM(n.situacao = 'cancelada') OVER(), 0) AS _kpi_canc
              FROM nfse_capturadas n
              LEFT JOIN clientes cl ON cl.id = n.empresa_id
              {where_sql}
             ORDER BY n.data_emissao DESC, n.id DESC
             LIMIT %s OFFSET %s""",
        tuple(params) + (per_page, offset), fetch=True) or []

    first = all_rows[0] if all_rows else {}
    kpi = {
        'total_servicos': float(first.get('_kpi_servicos') or 0),
        'total_iss': float(first.get('_kpi_iss') or 0),
        'total_retencoes': float(first.get('_kpi_ret') or 0),
        'total_canceladas': int(first.get('_kpi_canc') or 0),
    }
    total = int(first.get('_total') or 0)
    if not all_rows:
        total = 0
        kpi = {'total_servicos': 0, 'total_iss': 0, 'total_retencoes': 0,
               'total_canceladas': 0}

    _win = {'_total', '_kpi_servicos', '_kpi_iss', '_kpi_ret', '_kpi_canc'}
    rows = []
    for r in all_rows:
        row = {k: v for k, v in r.items() if k not in _win}
        for k in ('data_emissao', 'competencia', 'criado_em'):
            if row.get(k) and hasattr(row[k], 'isoformat'):
                row[k] = row[k].isoformat()
        for k in ('valor_servicos', 'base_calculo', 'aliquota_iss', 'valor_iss',
                  'total_retencoes', 'valor_liquido'):
            row[k] = float(row.get(k) or 0)
        rows.append(row)

    return jsonify({'total': total, 'page': page, 'per_page': per_page,
                    'rows': rows, 'kpi': kpi})


@escrita_fiscal.route('/conf-nfse/api/detalhe/<int:nfse_id>')
@login_required
def api_nfse_detalhe(nfse_id):
    """Uma NFS-e por inteiro + os eventos dela — o detalhe da linha.

    Os eventos vêm por ``chave_referenciada``, NÃO por empresa: ``nfse_eventos``
    colapsa o mesmo evento visto por cursores diferentes numa linha só, e os
    campos ``*_origem`` são proveniência, nunca filtro. Filtrar por empresa aqui
    esconderia o cancelamento de uma nota sempre que ele tivesse chegado pelo
    cursor da outra parte.
    """
    n = execute_query(
        'SELECT * FROM nfse_capturadas WHERE id = %s', (nfse_id,),
        fetch=True, fetch_one=True)
    if not n:
        return jsonify({'erro': 'NFS-e não encontrada'}), 404

    n.pop('raw_json', None)                      # o envelope cru não vai para a tela
    for k, v in list(n.items()):
        if hasattr(v, 'isoformat'):
            n[k] = v.isoformat()
        elif isinstance(v, Decimal):
            n[k] = float(v)

    eventos = execute_query(
        'SELECT tipo_evento, sequencia, data_evento, motivo, chave_substituta, '
        '       revisar, divergencia, nsu_origem '
        '  FROM nfse_eventos WHERE chave_referenciada = %s '
        ' ORDER BY data_evento, sequencia', (n['chave_acesso'],), fetch=True) or []
    for e in eventos:
        if e.get('data_evento') and hasattr(e['data_evento'], 'isoformat'):
            e['data_evento'] = e['data_evento'].isoformat()

    return jsonify({'nfse': n, 'eventos': eventos})
