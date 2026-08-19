# -*- coding: utf-8 -*-
"""Indicador de espaço do Dropbox — leitura CACHEADA, pronta para exibir.

SOMENTE LEITURA. Chama ``DropboxService.get_space_usage`` (users/get_space_usage)
e nada mais: não lista, não move, não apaga.

Por que existe um cache
-----------------------
O painel admin recarrega à vontade (e tem auto-refresh em outras telas). Bater na
API do Dropbox a cada render gastaria rate limit sem necessidade — a quota de uma
conta muda devagar. O resultado é guardado em ``app_config`` (a mesma tabela
chave/valor do horário do scheduler) com TTL de 1h por padrão, então em regime
normal há **1 chamada por hora**, não uma por refresh.

O TTL é comparado com o relógio do BANCO (``TIMESTAMPDIFF`` sobre
``app_config.updated_at``), nunca com ``datetime.now()`` do Python — mesma
disciplina de fuso adotada na captura de DFe, e evita divergência entre workers.

Degradação: se a API falhar, devolve o ÚLTIMO valor conhecido marcado como
``stale`` (com o erro ao lado). A tela do admin nunca quebra nem fica em branco
por causa de uma indisponibilidade do Dropbox.
"""
import json
import logging
import os

from utils.db_helper import execute_query

logger = logging.getLogger(__name__)

_CONFIG_KEY = 'dropbox_space_cache'
_TTL_SEG = int(os.getenv('DROPBOX_SPACE_TTL_SEG', '3600'))   # 1h

# Faixas do semáforo da BARRA (percentual de uso).
_LIMITE_ATENCAO = 70
_LIMITE_CRITICO = 90

# Limite do ALERTA destacado no topo das Configurações. Deliberadamente diferente
# do semáforo: a barra fica amarela já em 70% (informação passiva), mas o banner
# só aparece em 85% — assim o aviso mantém peso em vez de virar paisagem.
_LIMITE_ALERTA = int(os.getenv('DROPBOX_SPACE_ALERTA_PCT', '85'))


# --------------------------------------------------------------------------
# Formatação (pt-BR: vírgula decimal). Base binária com rótulo GB/TB — é assim
# que o próprio Dropbox mostra (2 TB = 2 TiB = 2.199.023.255.552 bytes).
# --------------------------------------------------------------------------
_UNIDADES = (('TB', 1024 ** 4), ('GB', 1024 ** 3), ('MB', 1024 ** 2), ('KB', 1024))


def formatar_bytes(n) -> str:
    """1993027683123 -> '1,8 TB'; 62813466624 -> '58,5 GB'; 0 -> '0 B'.

    Sem casa decimal quando o valor é redondo na unidade (2199023255552 -> '2 TB'),
    porque "2,0 TB" no rótulo do plano fica estranho.

    ``None`` (valor desconhecido) devolve '—', NÃO '0 B': mostrar zero para algo
    que não sabemos seria um número errado com cara de certo.
    """
    if n is None or n == '':
        return '—'
    try:
        n = int(n)
    except (TypeError, ValueError):
        return '—'
    if n <= 0:
        return '0 B'
    for rotulo, fator in _UNIDADES:
        if n >= fator:
            v = n / fator
            txt = f'{v:.0f}' if abs(v - round(v)) < 0.05 else f'{v:.1f}'
            return f'{txt.replace(".", ",")} {rotulo}'
    return f'{n} B'


def _cor(pct):
    """verde até 70% · amarelo 70–90% · vermelho acima de 90%."""
    if pct is None:
        return 'cinza'
    if pct > _LIMITE_CRITICO:
        return 'vermelho'
    if pct >= _LIMITE_ATENCAO:
        return 'amarelo'
    return 'verde'


def _montar(usado, total, tipo='desconhecido'):
    """Monta o dict de exibição a partir dos bytes crus."""
    usado = int(usado or 0)
    total = int(total or 0)
    livre = max(0, total - usado) if total else None
    # total=0 -> quota não informada pela API: mostra o usado, sem % nem barra
    # (dividir por zero aqui viraria erro 500 numa tela só de leitura).
    pct = round(usado / total * 100, 1) if total else None

    pct_fmt = f'{pct:.1f}'.replace('.', ',') if pct is not None else '—'

    if total:
        texto = (f'Usando {formatar_bytes(usado)} de {formatar_bytes(total)} '
                 f'({pct_fmt}%) — {formatar_bytes(livre)} livre')
    else:
        texto = f'Usando {formatar_bytes(usado)} (cota não informada pelo Dropbox)'

    # Alerta destacado no topo das Configurações a partir de _LIMITE_ALERTA.
    alerta = pct is not None and pct >= _LIMITE_ALERTA
    return {
        'ok': True,
        'usado': usado, 'total': total, 'livre': livre,
        'usado_fmt': formatar_bytes(usado),
        'total_fmt': formatar_bytes(total) if total else '—',
        'livre_fmt': formatar_bytes(livre) if livre is not None else '—',
        'pct': pct,
        'pct_fmt': pct_fmt,
        'pct_barra': min(100, pct) if pct is not None else 0,
        'cor': _cor(pct),
        'tipo': tipo,
        'texto': texto,
        'alerta': alerta,
        'alerta_nivel': ('vermelho' if (pct is not None and pct > _LIMITE_CRITICO)
                         else 'amarelo') if alerta else None,
        'alerta_texto': (f'Dropbox em {pct_fmt}% — considere limpar XMLs antigos'
                         if alerta else None),
        'stale': False,
        'erro': None,
        'idade_seg': 0,
    }


# --------------------------------------------------------------------------
# Cache em app_config
# --------------------------------------------------------------------------
def _ler_cache():
    """Devolve (payload_dict, idade_em_segundos) ou (None, None).

    A idade sai do relógio do BANCO — não do Python.
    """
    try:
        row = execute_query(
            "SELECT valor, TIMESTAMPDIFF(SECOND, updated_at, NOW()) AS idade "
            "FROM app_config WHERE chave = %s",
            (_CONFIG_KEY,), fetch=True, fetch_one=True,
        )
        if not row or not row.get('valor'):
            return None, None
        return json.loads(row['valor']), int(row.get('idade') or 0)
    except Exception:
        logger.warning('[dropbox-space] cache ilegível; será renovado.', exc_info=True)
        return None, None


def _gravar_cache(payload):
    try:
        execute_query(
            "INSERT INTO app_config (chave, valor) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE valor = VALUES(valor), updated_at = NOW()",
            (_CONFIG_KEY, json.dumps(payload)), fetch=False,
        )
    except Exception:
        # Cache é otimização: falhar aqui não pode derrubar a tela.
        logger.warning('[dropbox-space] falha ao gravar o cache.', exc_info=True)


def get_space(force: bool = False) -> dict:
    """Espaço do Dropbox pronto para o template. NUNCA levanta exceção.

    Args:
        force: ignora o TTL e consulta a API agora (botão "Atualizar" do admin).

    Returns dict com ``ok``, ``texto``, ``pct``, ``cor``, ``stale``, ``erro``,
    ``idade_seg`` e os valores brutos/formatados. Com ``ok=False`` a tela mostra
    o motivo em vez da barra.
    """
    from utils import dropbox_sync   # import tardio: evita ciclo e custo no boot

    cache, idade = _ler_cache()

    if not force and cache and idade is not None and idade < _TTL_SEG:
        cache['idade_seg'] = idade
        return cache

    if not dropbox_sync.is_configured():
        return {'ok': False, 'stale': False, 'idade_seg': idade or 0,
                'erro': 'Dropbox não configurado (DROPBOX_REFRESH_TOKEN, '
                        'DROPBOX_APP_KEY e DROPBOX_APP_SECRET).',
                'cor': 'cinza', 'pct': None, 'pct_barra': 0}

    try:
        bruto = dropbox_sync._service.get_space_usage()
    except Exception as exc:
        # Degradação: preferimos um número velho e rotulado como velho a um
        # painel vazio. Não regrava o cache (preserva updated_at e a idade real).
        logger.warning('[dropbox-space] consulta falhou: %s', exc)
        if cache:
            cache.update({'stale': True, 'erro': str(exc), 'idade_seg': idade or 0})
            return cache
        return {'ok': False, 'stale': False, 'idade_seg': 0, 'erro': str(exc),
                'cor': 'cinza', 'pct': None, 'pct_barra': 0}

    payload = _montar(bruto['usado'], bruto['total'], bruto.get('tipo'))
    _gravar_cache(payload)
    return payload


# ---------------------------------------------------------------------------
# SAÚDE — separada de ESPAÇO, e de propósito
#
# O painel dizia "Dropbox quebrado" com base numa chamada que o app NÃO precisa
# para funcionar: users_get_space_usage exige o escopo account_info.read, que
# não tem relação nenhuma com arquivo. Resultado real, medido em 14/08/2026: o
# aviso de credencial inválida ficou aceso na tela enquanto o Q-Colabore gravava
# arquivos e o Portal servia o instalador — os dois lendo e escrevendo no
# Dropbox sem qualquer problema.
#
# O estrago não é o incômodo de hoje. É que a MESMA mensagem vai aparecer no dia
# em que o refresh token expirar de verdade, e a essa altura todo mundo já terá
# aprendido a ignorá-la. Um alerta que mente rotineiramente deixa de ser alerta.
#
# Por isso agora são dois eixos independentes:
#   SAÚDE  = escopo de ARQUIVO (files_get_metadata). É a capacidade da qual o
#            sistema depende; se ela cai, algo realmente parou.
#   ESPAÇO = informativo. Falhar aqui vira "espaço indisponível", nunca alarme
#            de credencial.
#
# Não há cache aqui de propósito: é UMA chamada de metadados, só para admin, e
# um número de saúde velho não serve para nada — ou funciona agora, ou não.
# ---------------------------------------------------------------------------

def get_saude() -> dict:
    """O Dropbox está utilizável para ARQUIVO? NUNCA levanta exceção.

    Devolve ``{'ok', 'erro', 'escopo_faltando', 'caminho'}``.

    Mede pedindo os metadados da ``_ENTRADA`` — a porta por onde todo arquivo
    do sistema entra. Pasta ausente devolve None sem erro: isso é 'autenticou,
    mas a pasta não existe', que é problema de configuração de pasta e não de
    credencial. Os dois casos são reportados com textos diferentes porque as
    ações são diferentes.
    """
    from utils import dropbox_sync

    if not dropbox_sync.is_configured():
        return {'ok': False, 'escopo_faltando': False, 'caminho': None,
                'erro': 'Dropbox não configurado (DROPBOX_REFRESH_TOKEN, '
                        'DROPBOX_APP_KEY e DROPBOX_APP_SECRET).'}

    svc = dropbox_sync._service
    try:
        caminho = svc.pasta_cert_novo()
    except Exception as exc:
        logger.warning('[dropbox-saude] falha ao montar o caminho: %s', exc)
        return {'ok': False, 'escopo_faltando': False, 'caminho': None,
                'erro': 'Não consegui montar o caminho da _ENTRADA: %s' % exc}

    try:
        md = svc.file_metadata(caminho)
    except Exception as exc:
        # O atributo vem da tradução (_erro_auth); o _is_scope_error fica de
        # fallback para exceção que chegar CRUA por algum caminho não traduzido.
        escopo = getattr(exc, 'escopo_faltando',
                         dropbox_sync.DropboxService._is_scope_error(exc))
        logger.warning('[dropbox-saude] leitura de %s falhou (escopo=%s): %s',
                       caminho, escopo, exc)
        return {'ok': False, 'escopo_faltando': bool(escopo),
                'caminho': caminho, 'erro': str(exc)}

    if md is None:
        # Autenticou e leu — a pasta é que não está lá.
        return {'ok': False, 'escopo_faltando': False, 'caminho': caminho,
                'erro': 'A pasta %s não existe no Dropbox. As credenciais estão '
                        'válidas — o que falta é a pasta.' % caminho}

    return {'ok': True, 'escopo_faltando': False, 'caminho': caminho, 'erro': None}
